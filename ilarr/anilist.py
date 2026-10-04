"""AniList: titles, synonyms, format, airing schedule, and the prequel chain.

One AniList entry == one season/cour, which gives each tracked series its own
1..N numbering. Absolute numbering is derived by walking PREQUEL links.
"""
import re
from .http import get_json

URL = "https://graphql.anilist.co"
FIELDS = """id format status episodes seasonYear
 title{romaji english native} synonyms startDate{year}
 coverImage{extraLarge large} bannerImage description(asHtml:false)
 nextAiringEpisode{episode airingAt}
 airingSchedule(perPage:150){nodes{episode airingAt}}
 relations{edges{relationType node{id format episodes title{romaji english}}}}"""
SPLIT = re.compile(r"\b(part|cour)\s*(\d|ii)\b|\b\d(st|nd|rd|th)\s+cour\b|\bpart\b", re.I)

_cache = {}


def _gql(query, variables):
    r = get_json(URL, data={"query": query, "variables": variables},
                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    return r["data"]


def _norm(m):
    t = m["title"]
    sched = {n["episode"]: n["airingAt"] for n in (m.get("airingSchedule") or {}).get("nodes", [])}
    nxt = m.get("nextAiringEpisode")
    if nxt:
        sched.setdefault(nxt["episode"], nxt["airingAt"])
    total = m.get("episodes")
    return {
        "id": m["id"], "format": m.get("format"), "status": m.get("status"), "total": total,
        "title": t.get("romaji") or t.get("english") or "", "english": t.get("english"),
        "native": t.get("native"), "synonyms": m.get("synonyms") or [],
        "year": (m.get("startDate") or {}).get("year") or m.get("seasonYear"),
        "cover": (m.get("coverImage") or {}).get("extraLarge") or (m.get("coverImage") or {}).get("large"),
        "banner": m.get("bannerImage"),
        "description": re.sub(r"<[^>]+>", " ", m.get("description") or "").strip(),
        "schedule": sched, "max_known": max(list(sched) + [total or 0]),
        "relations": [{"type": e["relationType"], "id": e["node"]["id"], "format": e["node"]["format"],
                       "episodes": e["node"]["episodes"]} for e in m["relations"]["edges"]],
    }


def get(anilist_id, fresh=False):
    if fresh or anilist_id not in _cache:
        d = _gql("query($id:Int){Media(id:$id,type:ANIME){%s}}" % FIELDS, {"id": anilist_id})
        _cache[anilist_id] = _norm(d["Media"])
    return _cache[anilist_id]


def search(text, n=10):
    d = _gql("query($q:String,$n:Int){Page(perPage:$n){media(search:$q,type:ANIME,sort:SEARCH_MATCH){%s}}}"
             % FIELDS, {"q": text, "n": n})
    return [_norm(m) for m in d["Page"]["media"]]


def titles(m):
    return [t for t in [m["title"], m["english"], m["native"]] + m["synonyms"] if t]


def is_split_cour(m):
    return any(SPLIT.search(t) for t in titles(m) if t)


def lineage(m):
    """Return (root, season, season_offset, abs_offset) for a TV-like entry."""
    kind = m["format"]
    season, season_off, abs_off, in_own = 1, 0, 0, True
    cur, root = m, m
    while True:
        prev = next((r for r in cur["relations"] if r["type"] == "PREQUEL" and r["format"] == kind), None)
        if not prev:
            break
        p = get(prev["id"])
        abs_off += p["total"] or 0
        if is_split_cour(cur):
            if in_own:
                season_off += p["total"] or 0
        else:
            season += 1
            in_own = False
        cur = root = p
    return root, season, season_off, abs_off


def parent_of(m):
    """For OVA/movie/special entries: the TV entry they belong to (if any)."""
    for t in ("PARENT", "PREQUEL", "SIDE_STORY", "ALTERNATIVE", "SEQUEL", "SUMMARY"):
        r = next((r for r in m["relations"] if r["type"] == t and r["format"] in ("TV", "ONA")), None)
        if r:
            return get(r["id"])
    return None
