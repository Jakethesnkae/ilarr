"""Quality profile: accept/reject a release and give it a comparable score."""
import re

DEFAULT_PROFILE = {
    "resolutions": [1080, 720],        # best first; anything else is rejected
    "cutoff": 1080,                    # stop upgrading once reached
    "audio": "sub",                    # sub | dub | dual | any
    "preferred_groups": ["SubsPlease", "Erai-raws"],
    "blocked_groups": [],
    "codecs": [],                      # e.g. ["hevc"]; empty = any
    "hdr": "allow",                    # allow | reject | prefer
    "min_seeders": 1,
    "prefer_batch": True,
    "reject_words": ["hardsub", "cam"],
    # Custom formats: regex on the release title -> score delta
    "custom_formats": [{"pattern": "(?i)\\bremux\\b", "score": -300}],
}


def evaluate(rel, item, profile):
    """Return a score (higher is better) or None if the release is rejected."""
    p = dict(DEFAULT_PROFILE, **(profile or {}))
    title = item["title"]
    if rel.resolution not in p["resolutions"]:
        return None
    if item.get("seeders", 0) < p["min_seeders"]:
        return None
    if rel.group and rel.group.lower() in [g.lower() for g in p["blocked_groups"]]:
        return None
    if any(w.lower() in title.lower() for w in p["reject_words"]):
        return None
    if p["codecs"] and rel.codec not in p["codecs"]:
        return None
    hdr = rel.hdr or rel.dv
    if hdr and p["hdr"] == "reject":
        return None
    audio = p["audio"]
    if audio == "dual" and not rel.dual_audio:
        return None
    if audio == "dub" and not (rel.dub or rel.dual_audio):
        return None
    if audio == "sub" and rel.dub:
        return None

    score = (len(p["resolutions"]) - p["resolutions"].index(rel.resolution)) * 1000
    groups = [g.lower() for g in p["preferred_groups"]]
    if rel.group and rel.group.lower() in groups:
        score += 500 - groups.index(rel.group.lower()) * 10
    if audio in ("dub", "dual") and rel.dual_audio:
        score += 300
    if hdr and p["hdr"] == "prefer":
        score += 200
    score += (rel.version - 1) * 50
    score += min(item.get("seeders", 0), 50)
    for cf in p["custom_formats"]:
        if re.search(cf["pattern"], title):
            score += cf["score"]
    return score


def is_upgrade(old, new_score, new_res, new_version, profile):
    """old: episode row for an already downloaded episode."""
    cutoff = (profile or {}).get("cutoff", DEFAULT_PROFILE["cutoff"])
    if (old.get("res") or 0) < cutoff and new_score > (old.get("score") or 0):
        return True
    return new_res == old.get("res") and new_version > (old.get("version") or 1)
