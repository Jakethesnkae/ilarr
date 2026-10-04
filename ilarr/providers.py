"""Optional TMDB / TVDB providers.

They add two things on top of AniList:
  * extra aliases (translations, alternative titles) for title matching
  * season layout (episodes per season), used to map our absolute order onto
    the season numbers Plex/Jellyfin and most release groups expect.
Both need an API key in config.json; without a key the provider is inert.
"""
import urllib.parse
from .http import get_json
from .parser import norm


class Tmdb:
    base = "https://api.themoviedb.org/3"

    def __init__(self, key):
        self.key = key

    def _get(self, path, **params):
        params["api_key"] = self.key
        return get_json("%s%s?%s" % (self.base, path, urllib.parse.urlencode(params)))

    def find(self, titles, year=None):
        """Best TMDB tv id for any of the titles (genre filtered to animation if possible)."""
        wanted = {norm(t) for t in titles}
        for t in titles[:3]:
            for res in self._get("/search/tv", query=t).get("results", []):
                names = {norm(res.get("name", "")), norm(res.get("original_name", ""))}
                yr = (res.get("first_air_date") or "")[:4]
                if names & wanted and (not year or not yr or abs(int(yr) - year) <= 1):
                    return res["id"]
        return None

    def info(self, tid):
        d = self._get("/tv/%d" % tid)
        alts = self._get("/tv/%d/alternative_titles" % tid).get("results", [])
        seasons = {s["season_number"]: s["episode_count"] for s in d.get("seasons", [])
                   if s["season_number"] > 0 and s.get("episode_count")}
        return {"aliases": [d.get("name"), d.get("original_name")] + [a["title"] for a in alts],
                "seasons": seasons}


class Tvdb:
    base = "https://api4.thetvdb.com/v4"

    def __init__(self, key, pin=None):
        self.key, self.pin, self.token = key, pin, None

    def _login(self):
        body = {"apikey": self.key}
        if self.pin:
            body["pin"] = self.pin
        r = get_json(self.base + "/login", data=body, headers={"Content-Type": "application/json"})
        self.token = r["data"]["token"]

    def _get(self, path, **params):
        if not self.token:
            self._login()
        url = "%s%s?%s" % (self.base, path, urllib.parse.urlencode(params))
        return get_json(url, headers={"Authorization": "Bearer " + self.token})

    def find(self, titles, year=None):
        wanted = {norm(t) for t in titles}
        for t in titles[:3]:
            for res in self._get("/search", query=t, type="series").get("data", []):
                names = {norm(res.get("name", ""))} | {norm(a) for a in res.get("aliases", [])}
                yr = res.get("year")
                if names & wanted and (not year or not yr or abs(int(yr) - year) <= 1):
                    return int(res["tvdb_id"])
        return None

    def info(self, tid):
        d = self._get("/series/%d/extended" % tid, short="true").get("data", {})
        aliases = [d.get("name")] + [a.get("name") for a in d.get("aliases", [])]
        seasons, page = {}, 0
        while page is not None:
            r = self._get("/series/%d/episodes/default" % tid, page=page)
            for e in r.get("data", {}).get("episodes", []):
                if e.get("seasonNumber"):
                    seasons[e["seasonNumber"]] = seasons.get(e["seasonNumber"], 0) + 1
            page = page + 1 if (r.get("links") or {}).get("next") else None
        return {"aliases": aliases, "seasons": seasons}


def remap(seasons, abs_offset):
    """Map an entry starting at absolute episode abs_offset+1 onto a provider's season list.

    Returns (season, season_offset) or None when the layout doesn't reach that far.
    """
    start = abs_offset
    for s in sorted(seasons):
        if start < seasons[s]:
            return s, start
        start -= seasons[s]
    return None
