# anirr

A small anime PVR (Sonarr-style): tracks series, watches indexers, grabs the best release
per episode, hands it to qBittorrent, then renames and imports it. Python 3.8+, **no third-party dependencies**.

```
python -m anirr init                  # writes config.json (edit qBittorrent, library, API keys)
python -m anirr search "frieren"      # find the AniList id
python -m anirr add 154587            # track it (optionally --tvdb ID --tmdb ID)
python -m anirr serve                 # scheduler + web UI on http://127.0.0.1:8989
python -m unittest tests.test_core    # offline tests
```

## How it avoids Sonarr's anime pain points

| Problem | Approach |
|---|---|
| Absolute vs season numbering | Each tracked series is **one AniList entry (season/cour)** with its own 1..N plus `season`, `season_offset`, `abs_offset`. `S02E03`, `Show 2nd Season - 03` and `Show - 15` all resolve to the same episode (`matcher.py`). |
| Split cours | Part/Cour entries share a `season` and carry a `season_offset`, so `S01E14` lands on episode 2 of "Part 2". |
| TVDB/TMDB/AniDB disagree | AniList defines structure; with `season_source: "tvdb"` or `"tmdb"` the season/offset used for **file naming and `SxxExx` matching** are remapped onto that provider's layout (`providers.remap`). |
| JP/EN titles & bad aliases | Aliases = AniList romaji/english/native/synonyms + TMDB alt titles + TVDB aliases/translations, all normalised. `Romaji / English` release titles are split. |
| Specials / OVA / movies | AniList formats OVA/SPECIAL/MOVIE/ONA are tracked as their own entries (season 0) linked to the franchise. |
| Multi-episode files / batches | Parser yields episode lists and ranges; batches map each number across seasons; imports re-parse every file in the torrent. |
| Non-standard release naming | One parser (`parser.py`) handles `S01E01`, `- 01`, `001`, `01v2`, `EP01`, `[01]`, `OVA 2`, `(01-12)`, scene `.` names. `python -m anirr parse "<title>"` shows what it saw. |
| Dub/sub/dual | Profile `audio`: `sub` (rejects dubs), `dub`, `dual`, `any`. |

## TMDB / TVDB
Put keys in `config.json` (`tmdb.api_key`; `tvdb.api_key` + optional `pin`). They are optional: with no key the provider is
skipped. IDs are auto-found by title/year, or forced via `add --tvdb/--tmdb`. They add aliases and a season layout.

## Layout
`parser` → `matcher` → `quality` (profiles, custom formats, upgrades) → `engine` (add/refresh, RSS+search, grab, import,
failed-download retry via blacklist) · `anilist`, `providers`, `indexers`, `qbit` · `web` · `__main__`.

## Known limits
- Sub vs dub releases that number differently need a manual `season_offset`/`abs_offset` edit in the DB.
- The network layers (AniList, TMDB, TVDB, Nyaa, qBittorrent) are written against their public APIs but not exercised
  here; only parser, matcher, remapping and scoring have tests. Expect to fix small things on first live run.
- qBittorrent must be reachable at the same filesystem path (hardlink/copy/move import).
