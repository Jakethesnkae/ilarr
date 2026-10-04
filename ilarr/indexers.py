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


def fetch(indexer, query=""):
    """Return [{guid,title,url,size,seeders}] for a query ('' = latest feed)."""
    url = indexer["url"].replace("{query}", urllib.parse.quote_plus(query))
    root = ET.fromstring(request(url, timeout=30))
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
        if d["hash"]:
            d["url"] = "magnet:?xt=urn:btih:%s&dn=%s" % (d["hash"], urllib.parse.quote(d["title"]))
        d["guid"] = d["guid"] or d["url"]
        if d["title"]:
            items.append(d)
    return items
