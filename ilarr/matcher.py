"""Match a parsed Release to tracked series and resolve episode numbers.

Every tracked series is one season/cour with its own 1..total numbering plus
  season / season_offset  -> the S01E13 view (split cours share a season)
  abs_offset              -> the absolute view (101 -> sequel ep 1 if offset 100)
Release numbers are tried in the way their title implies:
  S02E05            -> season view
  "Title 2nd Season - 05" (specific title) -> relative to that entry
  "Title - 105" (franchise title)           -> absolute view
"""
from .parser import norm


class Match:
    def __init__(self, series, episodes):
        self.series, self.episodes = series, episodes  # episodes: relative numbers

    def __repr__(self):
        return "Match(%s, %s)" % (self.series["title"], self.episodes)


def _has_room(s, n):
    return n >= 1 and (not s["total"] or n <= s["total"])


def _by_abs(n, fam):
    for s in sorted(fam, key=lambda x: x["abs_offset"]):
        if s["format"] != "TV" and s["format"] != "ONA":
            continue
        if s["abs_offset"] < n and (not s["total"] or n <= s["abs_offset"] + s["total"]):
            return s, n - s["abs_offset"]
    return None


def _by_season(season, n, fam):
    cands = [s for s in fam if s["season"] == season and s["format"] in ("TV", "ONA")]
    for s in sorted(cands, key=lambda x: x["season_offset"]):
        if s["season_offset"] < n and (not s["total"] or n <= s["season_offset"] + s["total"]):
            return s, n - s["season_offset"]
    if len(cands) == 1 and _has_room(cands[0], n):  # group restarted numbering inside a split season
        return cands[0], n
    return None


def match(rel, tracked):
    names = {norm(n) for n in rel.names if n}
    base = {norm(n) for n in rel.base_names if n}
    own = [s for s in tracked if (names | base) & set(s["aliases"])]
    own_exact = [s for s in tracked if names & set(s["aliases"])]
    fam_ids = {s["franchise_id"] for s in own}
    fam = [s for s in tracked if s["franchise_id"] in fam_ids or (names | base) & set(s["franchise_aliases"])]
    if not own and not fam:
        return []
    fam = fam or own
    out = {}

    def add(s, n):
        out.setdefault(s["id"], (s, []))[1].append(n)

    # specials / OVAs / ONAs / movies tracked as their own entries
    if rel.special or rel.season == 0:
        for s in own_exact or own:
            if s["format"] in ("OVA", "SPECIAL", "ONA", "MOVIE", "TV_SHORT"):
                for n in rel.episodes or [1]:
                    if _has_room(s, n):
                        add(s, n)
        specials = [s for s in fam if s["format"] in ("OVA", "SPECIAL")]
        if not out and len(specials) == 1:
            for n in rel.episodes or [1]:
                add(specials[0], n)
        return [Match(s, sorted(e)) for s, e in out.values()]

    pick = None
    if rel.season_src == "ep":
        pick = lambda n: _by_season(rel.season, n, fam)
    elif own_exact:
        s0 = own_exact[0]

        def pick(n):
            if _has_room(s0, n):
                return s0, n
            return _by_abs(n, fam)
    elif rel.season_src == "title":  # "Title 2nd Season" not among the aliases
        def pick(n):
            return _by_season(rel.season, n, fam)
    else:
        def pick(n):
            if len(fam) == 1 and _has_room(fam[0], n):
                return fam[0], n
            return _by_abs(n, fam)

    if rel.episodes:
        for n in rel.episodes:
            hit = pick(n)
            if hit:
                add(*hit)
    elif rel.is_batch:  # batch with no explicit range: whole season / entry
        if rel.season_src == "ep":
            targets = [s for s in fam if s["season"] == rel.season and s["format"] in ("TV", "ONA")]
        else:
            targets = own_exact[:1] or own[:1]
        for s in targets:
            for n in range(1, (s["total"] or 0) + 1):
                add(s, n)
    else:  # no number at all: movie / one-shot
        for s in own_exact or own:
            if s["format"] in ("MOVIE", "OVA", "SPECIAL", "ONA") and (s["total"] or 1) == 1:
                add(s, 1)
    return [Match(s, sorted(set(e))) for s, e in out.values()]
