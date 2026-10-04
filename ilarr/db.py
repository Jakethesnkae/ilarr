import json
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS series(
  id INTEGER PRIMARY KEY, anilist_id INTEGER UNIQUE, franchise_id INTEGER,
  tmdb_id INTEGER, tvdb_id INTEGER,
  title TEXT, title_english TEXT, title_native TEXT, format TEXT, status TEXT,
  total INTEGER, season INTEGER DEFAULT 1, season_offset INTEGER DEFAULT 0, abs_offset INTEGER DEFAULT 0,
  aliases TEXT DEFAULT '[]', franchise_aliases TEXT DEFAULT '[]',
  path TEXT, profile TEXT, monitored INTEGER DEFAULT 1, start_year INTEGER, locked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS episodes(
  series_id INTEGER, number INTEGER, air_date INTEGER, status TEXT DEFAULT 'missing',
  file TEXT, score INTEGER, res INTEGER, version INTEGER, grp TEXT,
  PRIMARY KEY(series_id, number));
CREATE TABLE IF NOT EXISTS downloads(
  id INTEGER PRIMARY KEY, series_id INTEGER, guid TEXT, title TEXT, episodes TEXT,
  status TEXT, added INTEGER, score INTEGER, res INTEGER, version INTEGER, grp TEXT);
"""
JSON_COLS = ("aliases", "franchise_aliases", "profile")


class DB:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.c.executescript(SCHEMA)
        have = {r[1] for r in self.c.execute("PRAGMA table_info(series)")}
        for col in ("cover", "banner", "description"):  # added after v0.1
            if col not in have:
                self.c.execute("ALTER TABLE series ADD COLUMN %s TEXT" % col)
        self.c.commit()

    def q(self, sql, args=()):
        with self.lock:
            rows = [dict(r) for r in self.c.execute(sql, args).fetchall()]
        for r in rows:
            for k in JSON_COLS:
                if k in r and isinstance(r[k], str):
                    r[k] = json.loads(r[k])
        return rows

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args)
            self.c.commit()
            return cur.lastrowid

    def update(self, table, key, kid, **vals):
        vals = {k: (json.dumps(v) if k in JSON_COLS else v) for k, v in vals.items()}
        cols = ", ".join("%s=?" % k for k in vals)
        self.x("UPDATE %s SET %s WHERE %s=?" % (table, cols, key), list(vals.values()) + [kid])
