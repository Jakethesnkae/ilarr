import argparse
import json
import logging
import threading
import time

from . import anilist
from .db import DB
from .engine import Engine, DEFAULT_CONFIG, load_config
from .parser import parse
from .web import serve


def main():
    ap = argparse.ArgumentParser(prog="ilarr", description="Simple anime PVR")
    ap.add_argument("-c", "--config", default="config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run scheduler + web UI")
    sub.add_parser("run", help="run one search/import cycle")
    sub.add_parser("list")
    sub.add_parser("init", help="write a default config.json")
    p = sub.add_parser("search", help="search AniList"); p.add_argument("query")
    p = sub.add_parser("add", help="track an AniList id")
    p.add_argument("anilist_id", type=int); p.add_argument("--tmdb", type=int); p.add_argument("--tvdb", type=int)
    p = sub.add_parser("parse", help="debug: parse a release title"); p.add_argument("title")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if a.cmd == "init":
        with open(a.config, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return print("wrote", a.config)
    if a.cmd == "parse":
        return print(json.dumps(parse(a.title).__dict__, indent=2, ensure_ascii=False))
    if a.cmd == "search":
        for m in anilist.search(a.query):
            print("%-8d %-8s %-4s %s" % (m["id"], m["format"], m["total"] or "?", m["title"]))
        return

    cfg = load_config(a.config)
    eng = Engine(cfg, DB(cfg["db"]))
    if a.cmd == "add":
        print("added series", eng.add_series(a.anilist_id, a.tmdb, a.tvdb))
    elif a.cmd == "list":
        for s in eng.db.q("SELECT * FROM series ORDER BY title"):
            print("%-4d S%02d+%-3d abs+%-4d %-6s %s" % (s["id"], s["season"], s["season_offset"],
                                                       s["abs_offset"], s["format"], s["title"]))
    elif a.cmd == "run":
        eng.cycle(refresh=True)
    elif a.cmd == "serve":
        lock = threading.Lock()

        def run():
            if lock.acquire(blocking=False):
                try:
                    eng.cycle(refresh=True)
                except Exception:
                    logging.exception("cycle failed")
                finally:
                    lock.release()

        def loop():
            while True:
                run()
                time.sleep(cfg["interval_minutes"] * 60)

        threading.Thread(target=loop, daemon=True).start()
        host, port = cfg["listen"]
        logging.info("web UI on http://%s:%d", host, port)
        serve(eng, host, port, run)


main()
