"""Core logic: add/refresh series, find releases, grab, import."""
import json
import logging
import os
import re
import shutil
import time
from collections import defaultdict

from . import anilist, indexers
from .matcher import match
from .parser import norm, parse, VIDEO_EXT
from .providers import Tmdb, Tvdb, remap
from .qbit import QBit
from .quality import DEFAULT_PROFILE, evaluate, is_upgrade

log = logging.getLogger("anirr")
DEFAULT_CONFIG = {
    "library": "library",
    "db": "anirr.db",
    "listen": ["127.0.0.1", 8989],
    "indexers": [{"name": "Nyaa", "url": "https://nyaa.si/?page=rss&c=1_2&f=0&q={query}"}],
    "qbittorrent": {"url": "http://127.0.0.1:8080", "username": "admin", "password": "adminadmin",
                    "category": "anirr", "save_path": None},
    "tmdb": {"api_key": ""},
    "tvdb": {"api_key": "", "pin": ""},
    "season_source": "anilist",       # anilist | tmdb | tvdb  (which season layout to name files with)
    "naming": "{title} - S{season:02d}{ep} [{res}p]",
    "import_mode": "hardlink",        # hardlink | copy | move
    "interval_minutes": 15,
    "release_delay_minutes": 10,      # wait after airtime before searching
    "stall_hours": 24,
    "default_profile": DEFAULT_PROFILE,
}


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
    return cfg


def safe(name):
    return re.sub(r'[<>:"/\\|?*]+', "", name).strip(" .")


class Engine:
    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        q = cfg["qbittorrent"]
        self.qbit = QBit(q["url"], q["username"], q["password"], q["category"])
        self.tmdb = Tmdb(cfg["tmdb"]["api_key"]) if cfg["tmdb"].get("api_key") else None
        self.tvdb = Tvdb(cfg["tvdb"]["api_key"], cfg["tvdb"].get("pin")) if cfg["tvdb"].get("api_key") else None

    # ---------------------------------------------------------------- series
    def add_series(self, anilist_id, tmdb_id=None, tvdb_id=None, path=None, profile=None):
        if self.db.one("SELECT id FROM series WHERE anilist_id=?", (anilist_id,)):
            raise ValueError("already tracked")
        m = anilist.get(anilist_id, fresh=True)
        if m["format"] in ("TV", "ONA"):
            root, season, soff, aoff = anilist.lineage(m)
        else:
            parent = anilist.parent_of(m)
            root = anilist.lineage(parent)[0] if parent else m
            season, soff, aoff = 0, 0, 0
        aliases = sorted({norm(t) for t in anilist.titles(m)})
        fam_aliases = sorted({norm(t) for t in anilist.titles(root)})
        sib = self.db.one("SELECT tmdb_id, tvdb_id FROM series WHERE franchise_id=?", (root["id"],)) or {}
        ids = {"tmdb": tmdb_id or sib.get("tmdb_id"), "tvdb": tvdb_id or sib.get("tvdb_id")}
        layouts = {}
        for name, prov in (("tmdb", self.tmdb), ("tvdb", self.tvdb)):
            if not prov:
                continue
            try:
                ids[name] = ids[name] or prov.find(anilist.titles(root), root["year"])
                if ids[name]:
                    info = prov.info(ids[name])
                    fam_aliases = sorted(set(fam_aliases) | {norm(a) for a in info["aliases"] if a})
                    layouts[name] = info["seasons"]
            except Exception as e:  # providers are optional enrichment
                log.warning("%s lookup failed: %s", name, e)
        src = self.cfg["season_source"]
        if src in layouts and m["format"] in ("TV", "ONA") and layouts[src]:
            hit = remap(layouts[src], aoff)
            if hit:
                season, soff = hit
        profile = profile or self.cfg["default_profile"]
        folder = path or os.path.join(self.cfg["library"], safe(root["english"] or root["title"]))
        sid = self.db.x(
            "INSERT INTO series(anilist_id,franchise_id,tmdb_id,tvdb_id,title,title_english,title_native,format,"
            "status,total,season,season_offset,abs_offset,aliases,franchise_aliases,path,profile,start_year)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["id"], root["id"], ids["tmdb"], ids["tvdb"], m["title"], m["english"], m["native"], m["format"],
             m["status"], m["total"], season, soff, aoff, json.dumps(aliases), json.dumps(fam_aliases),
             folder, json.dumps(profile), m["year"]))
        self.refresh_episodes(sid)
        return sid

    def refresh_episodes(self, sid):
        s = self.db.one("SELECT * FROM series WHERE id=?", (sid,))
        m = anilist.get(s["anilist_id"], fresh=True)
        self.db.update("series", "id", sid, status=m["status"], total=m["total"])
        for n in range(1, m["max_known"] + 1):
            aired = m["schedule"].get(n) or (1 if m["status"] == "FINISHED" else None)
            self.db.x("INSERT OR IGNORE INTO episodes(series_id,number,air_date) VALUES(?,?,?)", (sid, n, aired))
            if aired:
                self.db.x("UPDATE episodes SET air_date=? WHERE series_id=? AND number=?", (aired, sid, n))

    def tracked(self):
        return self.db.q("SELECT * FROM series WHERE monitored=1")

    # ----------------------------------------------------------- search/grab
    def wanted_eps(self, s):
        cutoff = time.time() - self.cfg["release_delay_minutes"] * 60
        return self.db.q("SELECT * FROM episodes WHERE series_id=? AND air_date IS NOT NULL AND air_date<=?"
                         " AND status IN ('missing','downloaded')", (s["id"], cutoff))

    def collect(self, queries_by_series=None):
        """Fetch releases: the latest RSS feed plus targeted searches for series with gaps."""
        items, seen = [], set()

        def pull(q):
            for ix in self.cfg["indexers"]:
                try:
                    for it in indexers.fetch(ix, q):
                        if it["guid"] not in seen:
                            seen.add(it["guid"])
                            items.append(it)
                except Exception as e:
                    log.warning("indexer %s failed for %r: %s", ix["name"], q, e)

        pull("")
        for s in self.tracked():
            missing = [e for e in self.wanted_eps(s) if e["status"] == "missing"]
            if not missing:
                continue
            for t in {s["title"], s["title_english"]} - {None}:
                pull(t)
        return items

    def process(self, items):
        tracked = self.tracked()
        black = {r["guid"] for r in self.db.q("SELECT guid FROM downloads WHERE status='failed'")}
        active = {(d["series_id"], n) for d in
                  self.db.q("SELECT series_id, episodes FROM downloads WHERE status IN ('queued','downloading')")
                  for n in json.loads(d["episodes"])}
        per_series = defaultdict(list)
        for it in items:
            if it["guid"] in black:
                continue
            rel = parse(it["title"])
            for m in match(rel, tracked):
                per_series[m.series["id"]].append((it, rel, m))
        grabbed = []
        for sid, cands in per_series.items():
            s = next(t for t in tracked if t["id"] == sid)
            eps = {e["number"]: e for e in self.db.q("SELECT * FROM episodes WHERE series_id=?", (sid,))}
            scored = []
            for it, rel, m in cands:
                sc = evaluate(rel, it, s["profile"])
                if sc is None:
                    continue
                wanted = [n for n in m.episodes
                          if (sid, n) not in active and self._wants(eps.get(n), s, rel, sc)]
                if wanted:
                    scored.append({"it": it, "rel": rel, "score": sc, "eps": wanted})
            taken = set()
            prefer_batch = (s["profile"] or {}).get("prefer_batch", True)
            ordered = sorted(scored, key=lambda c: (-(len(c["eps"]) >= 3 and prefer_batch), -c["score"]))
            for c in ordered:
                eps_left = [n for n in c["eps"] if n not in taken]
                if not eps_left or (len(c["eps"]) >= 3 and len(eps_left) < len(c["eps"])):
                    continue
                taken.update(c["eps"])
                grabbed.append(self.grab(s, c))
        return grabbed

    def _wants(self, ep, s, rel, score):
        if not ep or not ep["air_date"]:
            return False
        if ep["status"] == "missing":
            return True
        if ep["status"] == "downloaded":
            return is_upgrade(ep, score, rel.resolution, rel.version, s["profile"])
        return False

    def grab(self, s, c):
        it, rel = c["it"], c["rel"]
        did = self.db.x("INSERT INTO downloads(series_id,guid,title,episodes,status,added,score,res,version,grp)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (s["id"], it["guid"], it["title"], json.dumps(c["eps"]), "queued", int(time.time()),
                         c["score"], rel.resolution, rel.version, rel.group))
        self.qbit.add(it["url"], "anirr-%d" % did, self.cfg["qbittorrent"].get("save_path"))
        for n in c["eps"]:
            ep = self.db.one("SELECT status FROM episodes WHERE series_id=? AND number=?", (s["id"], n))
            if ep["status"] == "missing":
                self.db.x("UPDATE episodes SET status='queued' WHERE series_id=? AND number=?", (s["id"], n))
        log.info("grabbed %s -> %s eps %s", it["title"], s["title"], c["eps"])
        return did

    # ----------------------------------------------------- downloads / import
    def poll_downloads(self):
        for d in self.db.q("SELECT * FROM downloads WHERE status IN ('queued','downloading')"):
            try:
                t = self.qbit.info("anirr-%d" % d["id"])
            except Exception as e:
                log.warning("qbittorrent unreachable: %s", e)
                return
            if t is None:
                if time.time() - d["added"] > 3600:
                    self.fail(d, None)
                continue
            if t["state"] in ("error", "missingFiles") or (
                    t["progress"] < 1 and t["state"] in ("stalledDL", "metaDL")
                    and time.time() - d["added"] > self.cfg["stall_hours"] * 3600):
                self.fail(d, t["hash"])
            elif t["progress"] >= 1:
                self.import_download(d, t)
            else:
                self.db.update("downloads", "id", d["id"], status="downloading")

    def fail(self, d, hash_):
        log.warning("download failed, will retry another release: %s", d["title"])
        if hash_:
            try:
                self.qbit.delete(hash_)
            except Exception:
                pass
        self.db.update("downloads", "id", d["id"], status="failed")
        for n in json.loads(d["episodes"]):
            self.db.x("UPDATE episodes SET status='missing' WHERE series_id=? AND number=? AND status='queued'",
                      (d["series_id"], n))

    def import_download(self, d, t):
        s = self.db.one("SELECT * FROM series WHERE id=?", (d["series_id"],))
        dl_eps = json.loads(d["episodes"])
        files = [f for f in self.qbit.files(t["hash"]) if f["name"].lower().endswith(VIDEO_EXT)]
        imported = set()
        for f in files:
            src = os.path.join(t["save_path"], f["name"])
            rel = parse(os.path.basename(f["name"]))
            ms = [m for m in match(rel, [s]) if m.series["id"] == s["id"]]
            eps = ms[0].episodes if ms else (dl_eps if len(files) == 1 else [])
            eps = [n for n in eps if n in dl_eps or len(dl_eps) == 0]
            if not eps:
                continue
            self.place(s, src, eps, rel, d)
            imported.update(eps)
        for n in dl_eps:
            if n not in imported:  # nothing usable inside: put it back in the wanted list
                self.db.x("UPDATE episodes SET status='missing' WHERE series_id=? AND number=? AND status='queued'",
                          (s["id"], n))
        self.db.update("downloads", "id", d["id"], status="imported")

    def place(self, s, src, eps, rel, d):
        ext = os.path.splitext(src)[1]
        tag = "E%02d" % (s["season_offset"] + eps[0])
        if len(eps) > 1:
            tag += "-E%02d" % (s["season_offset"] + eps[-1])
        name = self.cfg["naming"].format(
            title=safe(s["title_english"] or s["title"]), season=s["season"], ep=tag, abs=s["abs_offset"] + eps[0],
            rel=eps[0], res=rel.resolution or "", group=rel.group or "")
        folder = os.path.join(s["path"], "Season %02d" % s["season"])
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, safe(name) + ext)
        if os.path.exists(dst):
            os.remove(dst)
        mode = self.cfg["import_mode"]
        if mode == "move":
            shutil.move(src, dst)
        elif mode == "hardlink":
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        else:
            shutil.copy2(src, dst)
        for n in eps:
            old = self.db.one("SELECT file FROM episodes WHERE series_id=? AND number=?", (s["id"], n))
            if old and old["file"] and old["file"] != dst and os.path.exists(old["file"]):
                os.remove(old["file"])  # upgraded
            self.db.x("UPDATE episodes SET status='downloaded', file=?, score=?, res=?, version=?, grp=?"
                      " WHERE series_id=? AND number=?",
                      (dst, d["score"], d["res"], d["version"], d["grp"], s["id"], n))
        log.info("imported %s", dst)

    # ------------------------------------------------------------------ loop
    def cycle(self, refresh=False):
        if refresh:
            for s in self.tracked():
                if s["status"] != "FINISHED":
                    try:
                        self.refresh_episodes(s["id"])
                    except Exception as e:
                        log.warning("refresh %s failed: %s", s["title"], e)
        self.process(self.collect())
        self.poll_downloads()
