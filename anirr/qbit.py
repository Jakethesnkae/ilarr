"""qBittorrent WebUI client (API v2). Torrents are tracked by a per-download tag."""
import json
import urllib.parse
from http.cookiejar import CookieJar
import urllib.request


class QBit:
    def __init__(self, url, username="", password="", category="anirr"):
        self.url, self.user, self.pw, self.category = url.rstrip("/"), username, password, category
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.logged_in = False

    def _call(self, path, data=None):
        if not self.logged_in and path != "/auth/login":
            self._call("/auth/login", {"username": self.user, "password": self.pw})
            self.logged_in = True
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + "/api/v2" + path, data=body,
                                     headers={"Referer": self.url})
        return self.op.open(req, timeout=30).read().decode()

    def add(self, url, tag, save_path=None):
        data = {"urls": url, "tags": tag, "category": self.category}
        if save_path:
            data["savepath"] = save_path
        self._call("/torrents/add", data)

    def info(self, tag):
        r = json.loads(self._call("/torrents/info?" + urllib.parse.urlencode({"tag": tag})))
        return r[0] if r else None

    def files(self, hash_):
        return json.loads(self._call("/torrents/files?hash=" + hash_))

    def delete(self, hash_, files=True):
        self._call("/torrents/delete", {"hashes": hash_, "deleteFiles": str(files).lower()})
