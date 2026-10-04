"""Release-title parser: turns a torrent name into a structured Release."""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

VIDEO_EXT = (".mkv", ".mp4", ".avi", ".webm", ".m4v")


def norm(s):
    """Normalise a title for comparison (case, width, punctuation)."""
    s = unicodedata.normalize("NFKC", s).lower().replace("&", " and ")
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


@dataclass
class Release:
    raw: str
    group: Optional[str] = None
    names: List[str] = field(default_factory=list)       # title variants incl. trailing number
    base_names: List[str] = field(default_factory=list)  # title variants without season marker
    season: Optional[int] = None
    season_src: Optional[str] = None                      # 'ep' (S02E05) or 'title' ("2nd Season")
    episodes: List[int] = field(default_factory=list)
    is_batch: bool = False
    special: bool = False
    version: int = 1
    resolution: Optional[int] = None
    codec: Optional[str] = None
    hdr: bool = False
    dv: bool = False
    source: Optional[str] = None
    dual_audio: bool = False
    dub: bool = False
    langs: List[str] = field(default_factory=list)


RES = re.compile(r"(2160|1080|720|480)p|\b4k\b|\d{3,4}x(2160|1080|720|480)\b", re.I)
TAG_CUT = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|bd|bdrip|blu-?ray|web-?dl|webrip|web|hdtv|batch|complete|"
    r"x26[45]|hevc|av1|dual[ ._-]?audio|multi[ ._-]?audio)\b", re.I)
LANGS = {"eng": "en", "english": "en", "jpn": "ja", "jap": "ja", "japanese": "ja", "ita": "it",
         "spa": "es", "por": "pt", "ger": "de", "fre": "fr", "rus": "ru", "chi": "zh", "kor": "ko"}

SXXEXX = re.compile(r"\bS(\d{1,2})\s?E(\d{1,4})(?:v(\d))?(?:(?:\s?[-~]\s?E?|E)(\d{1,4}))?\b", re.I)
NXNN = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")
RANGE_BODY = re.compile(r"\s-\s(\d{1,4})\s*[-~]\s*(\d{1,4})\b")
EP_WORD = re.compile(r"\b(?:ep|episode)\s?\.?(\d{1,4})(?:v(\d))?\b", re.I)
SPECIAL = re.compile(r"\b(?:(OVAs?|OADs?|ONAs?|Specials?)|(SP))\s?(\d{1,2})?\b")
DASH_EP = re.compile(r"\s-\s(\d{1,4})(?:v(\d))?(?=\s|$)")
LOOSE_EP = re.compile(r"\s(\d{2,4})(?:v(\d))?\s*$")
SEASON_TITLE = [re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", re.I),
                re.compile(r"\bseason\s+(\d{1,2})\b", re.I),
                re.compile(r"\bs(\d{1,2})$", re.I)]


def _clean(t):
    return t.strip(" -_.~|/")


def parse(name):
    r = Release(raw=name)
    s = re.sub(r"\.(mkv|mp4|avi|webm|m4v|torrent)$", "", name.strip(), flags=re.I)
    low = s.lower()

    m = re.match(r"^\s*[\[\(]([^\]\)]+)[\]\)]", s)
    if m and not RES.search(m.group(1)) and not re.fullmatch(r"[0-9A-F]{8}|\d{4}", m.group(1)):
        r.group = m.group(1).strip()

    # --- tags -------------------------------------------------------
    m = RES.search(s)
    if m:
        txt = m.group(0).lower()
        r.resolution = 2160 if txt == "4k" else int(re.search(r"(2160|1080|720|480)", txt).group(1))
    if re.search(r"x265|hevc|h\.?265", low): r.codec = "hevc"
    elif re.search(r"x264|avc|h\.?264", low): r.codec = "avc"
    elif re.search(r"\bav1\b", low): r.codec = "av1"
    r.hdr = bool(re.search(r"\bhdr(10)?\b|hdr10\+", low))
    r.dv = bool(re.search(r"dolby[ ._-]?vision|\bdv\b", low))
    for pat, src in ((r"blu-?ray|\bbd(rip|remux)?\b", "bd"), (r"web-?dl|webrip|\bweb\b", "web"), (r"hdtv", "tv")):
        if re.search(pat, low):
            r.source = src
            break
    r.dual_audio = bool(re.search(r"dual[ ._-]?audio|multi[ ._-]?audio|\bdual\b", low))
    r.dub = not r.dual_audio and bool(re.search(r"\b(dub|dubbed|eng[ ._-]?dub|english[ ._-]?dub)\b", low))
    r.langs = sorted({v for k, v in LANGS.items() if re.search(r"\b%s\b" % k, low)})
    vm = re.search(r"\b(?:repack|proper)(\d?)\b", low)
    if vm:
        r.version = int(vm.group(1) or 2)
    batch_word = bool(re.search(r"\b(batch|complete|season pack)\b", low))

    # --- bracketed content: ranges / bare episode numbers ----------------
    brackets = re.findall(r"[\[\(]([^\]\)]*)[\]\)]", s)
    body = re.sub(r"[\[\(][^\]\)]*[\]\)]", " ", s)
    if r.group is None:  # scene style trailing -GROUP
        gm = re.search(r"-([A-Za-z0-9]+)$", body.strip())
        if gm and gm.group(1).lower() not in ("dl", "rip", "raws"):
            r.group = gm.group(1)
    if body.count(" ") < body.count(".") + body.count("_"):
        body = body.replace(".", " ").replace("_", " ")
    body = " ".join(body.split())

    pos = None  # where the title ends
    eps: List[int] = []

    def span(a, b):
        return list(range(a, b + 1)) if 0 < a < b <= 2000 and b - a < 400 and not (a >= 1900) else None

    m = SXXEXX.search(body)
    if m:
        r.season, r.season_src = int(m.group(1)), "ep"
        a = int(m.group(2))
        eps = [a]
        if m.group(3): r.version = max(r.version, int(m.group(3)))
        b = int(m.group(4)) if m.group(4) else None
        if b and b > a and b not in (480, 720, 1080, 2160) and b - a < 100:
            eps = list(range(a, b + 1))
        pos = m.start()
    if not eps:
        m = NXNN.search(body)
        if m:
            r.season, r.season_src, eps, pos = int(m.group(1)), "ep", [int(m.group(2))], m.start()
    if not eps:
        for b in brackets:
            rm = re.fullmatch(r"\s*(\d{1,4})\s*[-~]\s*(\d{1,4})\s*(?:\+.*)?", b)
            if rm and span(int(rm.group(1)), int(rm.group(2))):
                eps = span(int(rm.group(1)), int(rm.group(2)))
                r.is_batch = True
                break
    if not eps:
        m = RANGE_BODY.search(body)
        if m and span(int(m.group(1)), int(m.group(2))):
            eps, pos, r.is_batch = span(int(m.group(1)), int(m.group(2))), m.start(), True
    if not eps:
        m = EP_WORD.search(body)
        if m:
            eps, pos = [int(m.group(1))], m.start()
            if m.group(2): r.version = max(r.version, int(m.group(2)))
    if not eps:
        ms = list(SPECIAL.finditer(body))
        m = next((x for x in reversed(ms) if x.group(3)), ms[0] if ms else None)
        if m:
            r.special, eps, pos = True, [int(m.group(3) or 1)], m.start()
    if not eps:
        ms = list(DASH_EP.finditer(body))
        if ms:
            m = ms[-1]
            eps, pos = [int(m.group(1))], m.start()
            if m.group(2): r.version = max(r.version, int(m.group(2)))
    if not eps:
        for b in brackets:  # "[Group] Title [05] [1080p]"
            bm = re.fullmatch(r"(?:ep?\s?)?(\d{1,4})(?:v(\d))?", b.strip(), re.I)
            if bm:
                eps = [int(bm.group(1))]
                if bm.group(2): r.version = max(r.version, int(bm.group(2)))
                break
    if not eps:
        m = LOOSE_EP.search(body)
        if m and not re.fullmatch(r"(19|20)\d\d", m.group(1)) and body[:m.start()].strip():
            eps, pos = [int(m.group(1))], m.start()
            if m.group(2): r.version = max(r.version, int(m.group(2)))
    if r.season == 0:
        r.special = True
    r.episodes = eps
    r.is_batch = r.is_batch or batch_word

    # --- title ----------------------------------------------------------
    title = body
    if pos is not None:
        title = body[:pos]
    else:
        tm = TAG_CUT.search(body)
        if tm:
            title = body[:tm.start()]
    # a trailing number that was taken as the episode may belong to the title ("Mob Psycho 100")
    full = _clean(body[:LOOSE_EP.search(body).end()]) if (pos is not None and LOOSE_EP.search(body)
                                                           and LOOSE_EP.search(body).start() == pos) else None
    title = _clean(title)
    # Season markers in the title text
    season_t = None
    base = title
    for pat in SEASON_TITLE:
        sm = pat.search(base)
        if sm:
            season_t = int(sm.group(1))
            base = _clean(base[:sm.start()] + " " + base[sm.end():])
            break
    if season_t is not None and r.season is None:
        r.season, r.season_src = season_t, "title"
    if season_t is None and r.season is not None and r.season_src == "ep" and not eps:
        pass
    # Season-only pack: "Title S02 [1080p]"
    if not eps:
        sm = re.search(r"\bS(\d{1,2})\b", body, re.I)
        if sm and r.season is None:
            r.season, r.season_src = int(sm.group(1)), "ep"
            title = base = _clean(body[:sm.start()])
            r.is_batch = True

    alts = [t for t in re.split(r"\s+[/|]\s+", title) if t.strip()] or [title]
    balts = [t for t in re.split(r"\s+[/|]\s+", base) if t.strip()] or [base]
    r.names = [a for a in alts if a]
    if full:
        r.names += [f for f in re.split(r"\s+[/|]\s+", full) if f]
    r.base_names = [a for a in balts if a]
    return r
