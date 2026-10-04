"""Tiny JSON API + single-page UI."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import anilist

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>anirr</title><style>
body{font:15px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;background:#111;color:#ddd}
input,button{font:inherit;padding:.4rem .6rem;background:#222;color:#ddd;border:1px solid #444;border-radius:4px}
button{cursor:pointer}a{color:#7ab}table{width:100%;border-collapse:collapse}td,th{padding:.3rem;text-align:left;border-bottom:1px solid #222}
.missing{color:#e66}.queued{color:#ea4}.downloaded{color:#6c6}small{color:#888}</style>
<h2>anirr</h2>
<p><input id=q placeholder="search AniList"> <button onclick=find()>Search</button>
<button onclick="post('/api/run').then(load)">Run cycle</button></p>
<div id=res></div><h3>Series</h3><table id=list></table><div id=eps></div>
<script>
const j=(u,o)=>fetch(u,o).then(r=>r.json());
const post=(u,b)=>j(u,{method:'POST',body:JSON.stringify(b||{})});
async function find(){const r=await j('/api/search?q='+encodeURIComponent(q.value));
 res.innerHTML=r.map(m=>`<div>${m.title} <small>${m.format} · ${m.total||'?'} eps · ${m.year||''}</small>
 <button onclick="add(${m.id})">Add</button></div>`).join('')}
async function add(id){const r=await post('/api/series',{anilist_id:id});if(r.error)alert(r.error);res.innerHTML='';load()}
async function load(){const r=await j('/api/series');
 list.innerHTML='<tr><th>Title<th>Fmt<th>Season<th>Abs<th>Have</tr>'+r.map(s=>`<tr><td><a href=# onclick="show(${s.id});return false">${s.title}</a>
 <td>${s.format}<td>${s.season}${s.season_offset?'+'+s.season_offset:''}<td>${s.abs_offset}<td>${s.have}/${s.total||'?'}</tr>`).join('')}
async function show(id){const r=await j('/api/series/'+id+'/episodes');
 eps.innerHTML='<h3>Episodes</h3><table>'+r.map(e=>`<tr><td>${e.number}<td class=${e.status}>${e.status}
 <td>${e.air_date>1?new Date(e.air_date*1000).toLocaleString():''}<td><small>${e.file||''}</small></tr>`).join('')+'</table>'}
load()
</script>"""


def serve(engine, host, port, run_cycle):
    db = engine.db

    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200, ctype="application/json"):
            body = (obj if isinstance(obj, str) else json.dumps(obj)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/":
                    return self._send(PAGE, ctype="text/html; charset=utf-8")
                if u.path == "/api/search":
                    return self._send(anilist.search(parse_qs(u.query).get("q", [""])[0]))
                if u.path == "/api/series":
                    rows = db.q("SELECT s.*, (SELECT COUNT(*) FROM episodes e WHERE e.series_id=s.id"
                                " AND e.status='downloaded') AS have FROM series s ORDER BY title")
                    return self._send(rows)
                parts = u.path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "series"] and parts[3] == "episodes":
                    return self._send(db.q("SELECT * FROM episodes WHERE series_id=? ORDER BY number", (parts[2],)))
                self._send({"error": "not found"}, 404)
            except Exception as e:
                self._send({"error": str(e)}, 500)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            try:
                if self.path == "/api/series":
                    return self._send({"id": engine.add_series(
                        body["anilist_id"], body.get("tmdb_id"), body.get("tvdb_id"))})
                if self.path == "/api/run":
                    threading.Thread(target=run_cycle, daemon=True).start()
                    return self._send({"ok": True})
                self._send({"error": "not found"}, 404)
            except Exception as e:
                self._send({"error": str(e)}, 400)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer((host, port), H).serve_forever()
