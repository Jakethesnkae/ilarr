import json
import urllib.request
import urllib.parse

UA = "ilarr/0.1"


def request(url, data=None, headers=None, method=None, timeout=30, raw=False):
    h = {"User-Agent": UA}
    h.update(headers or {})
    if isinstance(data, (dict, list)) and h.get("Content-Type") == "application/json":
        data = json.dumps(data).encode()
    elif isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if raw:
            return body, resp
    return body


def get_json(url, **kw):
    return json.loads(request(url, **kw).decode("utf-8"))
