"""JSON API + the single-page UI in static/index.html."""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import anilist

STATIC = os.path.join(os.path.dirname(__file__), "static")
SERIES_COLS = ("id,anilist_id,title,title_english,title_native,format,status,total,season,season_offset,abs_offset,"
               "monitored,cover,banner,description,start_year,tmdb_id,tvdb_id,path")


def redacted(cfg):
    c = json.loads(json.dumps(cfg))
    c["qbittorrent"]["password"] = "***" if c["qbittorrent"].get("password") else ""
    for k in ("tmdb", "tvdb", "prowlarr"):
        if c[k].get("api_key"):
            c[k]["api_key"] = "***"
    if c["tvdb"].get("pin"):
        c["tvdb"]["pin"] = "***"
    for indexer in c.get("indexers", []):
        if indexer.get("api_key"):
            indexer["api_key"] = "***"
    return c


def serve(engine, host, port, run_cycle):
    db, cfg = engine.db, engine.cfg

    def series_rows(where="", args=()):
        return db.q("SELECT %s, (SELECT COUNT(*) FROM episodes e WHERE e.series_id=s.id AND e.status='downloaded') AS have,"
                    " (SELECT COUNT(*) FROM episodes e WHERE e.series_id=s.id AND e.status='queued') AS queued,"
                    " (SELECT COUNT(*) FROM episodes e WHERE e.series_id=s.id AND e.status='missing'"
                    "  AND e.air_date IS NOT NULL AND e.air_date<=?) AS missing,"
                    " (SELECT MIN(air_date) FROM episodes e WHERE e.series_id=s.id AND e.air_date>?) AS next_air"
                    " FROM series s %s ORDER BY title COLLATE NOCASE" % (SERIES_COLS, where),
                    (int(time.time()), int(time.time())) + tuple(args))

    def episode_rows(where, args, order="", limit=500):
        return db.q("SELECT e.*, s.title, s.title_english, s.cover, s.season, s.season_offset, s.abs_offset, s.format"
                    " FROM episodes e JOIN series s ON s.id=e.series_id WHERE s.monitored=1 AND %s %s LIMIT %d"
                    % (where, order, limit), args)

    routes = []

    def route(method, pattern):
        def deco(fn):
            routes.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    @route("GET", "/api/status")
    def status(q, body):
        t = int(time.time())
        return {"version": "0.1", "running": engine.running, "last_cycle": engine.last_cycle,
                "series": db.one("SELECT COUNT(*) n FROM series")["n"],
                "missing": db.one("SELECT COUNT(*) n FROM episodes e JOIN series s ON s.id=e.series_id"
                                  " WHERE s.monitored=1 AND e.status='missing' AND e.air_date IS NOT NULL"
                                  " AND e.air_date<=?", (t,))["n"],
                "downloading": db.one("SELECT COUNT(*) n FROM downloads WHERE status IN ('queued','downloading')")["n"],
                "indexers": len(engine.indexers), "interval_minutes": cfg["interval_minutes"],
                "providers": {"prowlarr": bool(cfg["prowlarr"].get("url") and cfg["prowlarr"].get("api_key")),
                              "tmdb": bool(engine.tmdb), "tvdb": bool(engine.tvdb)},
                "season_source": cfg["season_source"], "config": redacted(cfg)}

    @route("GET", "/api/search")
    def search(q, body):
        tracked = {r["anilist_id"] for r in db.q("SELECT anilist_id FROM series")}
        out = anilist.search(q.get("q", [""])[0])
        for m in out:
            m["tracked"] = m["id"] in tracked
        return out

    @route("GET", "/api/series")
    def series_list(q, body):
        return series_rows()

    @route("POST", "/api/series")
    def series_add(q, body):
        return {"id": engine.add_series(body["anilist_id"], body.get("tmdb_id"), body.get("tvdb_id"))}

    @route("GET", r"/api/series/(\d+)")
    def series_get(q, body, sid):
        rows = series_rows("WHERE s.id=?", (sid,))
        if not rows:
            raise KeyError
        rows[0]["episodes"] = db.q("SELECT * FROM episodes WHERE series_id=? ORDER BY number", (sid,))
        return rows[0]

    @route("PATCH", r"/api/series/(\d+)")
    def series_patch(q, body, sid):
        allowed = {k: body[k] for k in ("monitored", "season", "season_offset", "abs_offset") if k in body}
        if allowed:
            db.update("series", "id", sid, **allowed)
        return {"ok": True}

    @route("DELETE", r"/api/series/(\d+)")
    def series_delete(q, body, sid):
        engine.delete_series(int(sid))
        return {"ok": True}

    @route("POST", r"/api/series/(\d+)/refresh")
    def series_refresh(q, body, sid):
        engine.refresh_episodes(int(sid))
        return {"ok": True}

    @route("GET", "/api/wanted")
    def wanted(q, body):
        return episode_rows("e.status='missing' AND e.air_date IS NOT NULL AND e.air_date<=?",
                            (int(time.time()),), "ORDER BY e.air_date DESC")

    @route("GET", "/api/upcoming")
    def upcoming(q, body):
        now = int(time.time())
        return episode_rows("e.air_date>? AND e.air_date<?", (now - 86400, now + 45 * 86400), "ORDER BY e.air_date")

    @route("GET", "/api/downloads")
    def downloads(q, body):
        return db.q("SELECT d.*, s.title AS series_title, s.cover FROM downloads d LEFT JOIN series s ON s.id=d.series_id"
                    " ORDER BY d.id DESC LIMIT 100")

    @route("POST", "/api/run")
    def run(q, body):
        threading.Thread(target=run_cycle, daemon=True).start()
        return {"ok": True}

    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200, ctype="application/json", cache=None):
            payload = (obj if isinstance(obj, (str, bytes)) else json.dumps(obj))
            payload = payload.encode() if isinstance(payload, str) else payload
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            if cache:
                self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(payload)

        def _dispatch(self, method):
            u = urlparse(self.path)
            if method == "GET" and not u.path.startswith("/api/"):
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    return self._send(f.read(), ctype="text/html; charset=utf-8", cache="no-cache")
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            for m, pat, fn in routes:
                hit = pat.match(u.path)
                if m == method and hit:
                    try:
                        return self._send(fn(parse_qs(u.query), body, *hit.groups()))
                    except KeyError:
                        return self._send({"error": "not found"}, 404)
                    except Exception as e:
                        return self._send({"error": str(e)}, 400)
            self._send({"error": "not found"}, 404)

        do_GET = lambda self: self._dispatch("GET")
        do_POST = lambda self: self._dispatch("POST")
        do_PATCH = lambda self: self._dispatch("PATCH")
        do_DELETE = lambda self: self._dispatch("DELETE")

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((host, port), H).serve_forever()
