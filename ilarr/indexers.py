"""RSS / Torznab indexer client (Nyaa, AnimeTosho, Jackett/Prowlarr feeds...)."""
import re
import urllib.parse
import xml.etree.ElementTree as ET
from .http import request

UNITS = {"b": 1, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}


def _size(txt):
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)?", txt or "")
    if not m:
        return 0
    return int(float(m.group(1)) * UNITS.get((m.group(2) or "b").lower(), 1))


def build_url(indexer, query=""):
    if indexer.get("type") == "torznab":  # Prowlarr / Jackett: <url>/api?t=search&q=...
        params = {"t": "search", "q": query, "limit": 100, "apikey": indexer.get("api_key", "")}
        if indexer.get("categories"):
            params["cat"] = ",".join(str(c) for c in indexer["categories"])
        return indexer["url"] + ("&" if "?" in indexer["url"] else "?") + urllib.parse.urlencode(params)
    return indexer["url"].replace("{query}", urllib.parse.quote_plus(query))


def prowlarr_indexers(cfg):
    """Expand the `prowlarr` config block into torznab indexer entries ('all' = Prowlarr's aggregate feed)."""
    if not cfg.get("url") or not cfg.get("api_key"):
        return []
    return [{"name": "Prowlarr:%s" % i, "type": "torznab", "api_key": cfg["api_key"],
             "url": "%s/%s/api" % (cfg["url"].rstrip("/"), i), "categories": cfg.get("categories", [5070])}
            for i in cfg.get("indexers", ["all"])]


def fetch(indexer, query=""):
    """Return [{guid,title,url,size,seeders}] for a query ('' = latest feed)."""
    root = ET.fromstring(request(build_url(indexer, query), timeout=60))
    if root.tag == "error":  # torznab error document
        raise RuntimeError("%s: %s" % (root.get("code"), root.get("description")))
    items = []
    for it in root.iter("item"):
        d = {"title": "", "url": "", "size": 0, "seeders": 0, "guid": "", "hash": None}
        for ch in it:
            tag = ch.tag.split("}")[-1]
            if tag == "title": d["title"] = ch.text or ""
            elif tag == "guid": d["guid"] = ch.text or ""
            elif tag == "link": d["url"] = d["url"] or (ch.text or "")
            elif tag == "enclosure":
                d["url"] = ch.get("url") or d["url"]
                d["size"] = d["size"] or int(ch.get("length") or 0)
            elif tag == "size": d["size"] = _size(ch.text)
            elif tag == "seeders": d["seeders"] = int(ch.text or 0)
            elif tag == "infoHash": d["hash"] = ch.text
            elif tag == "attr":  # torznab
                k, v = ch.get("name"), ch.get("value")
                if k == "seeders": d["seeders"] = int(v or 0)
                elif k == "size": d["size"] = int(v or 0)
                elif k == "infohash": d["hash"] = v
                elif k == "magneturl": d["magnet"] = v
        if d.get("magnet"):
            d["url"] = d["magnet"]
        elif d["hash"]:
            d["url"] = "magnet:?xt=urn:btih:%s&dn=%s" % (d["hash"], urllib.parse.quote(d["title"]))
        d["guid"] = d["guid"] or d["url"]
        if d["title"]:
            items.append(d)
    return items
