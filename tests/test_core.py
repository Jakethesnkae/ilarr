import unittest
from anirr.parser import parse, norm
from anirr.matcher import match
from anirr.providers import remap
from anirr.quality import evaluate, DEFAULT_PROFILE


def S(id, title, fmt="TV", total=12, season=1, soff=0, aoff=0, fid=1, extra=(), fam=()):
    return {"id": id, "title": title, "format": fmt, "total": total, "season": season, "season_offset": soff,
            "abs_offset": aoff, "franchise_id": fid, "aliases": [norm(title)] + [norm(e) for e in extra],
            "franchise_aliases": [norm(f) for f in fam]}


class ParserTests(unittest.TestCase):
    def test_subsplease(self):
        r = parse("[SubsPlease] Sousou no Frieren - 05 (1080p) [ABCD1234].mkv")
        self.assertEqual((r.group, r.episodes, r.resolution), ("SubsPlease", [5], 1080))
        self.assertEqual(r.names, ["Sousou no Frieren"])

    def test_scene_sxxexx(self):
        r = parse("Frieren.S01E05.1080p.WEB.H264-GRP")
        self.assertEqual((r.season, r.episodes, r.group, r.codec), (1, [5], "GRP", "avc"))

    def test_multi_episode_and_range(self):
        self.assertEqual(parse("Title S01E01-E02 1080p").episodes, [1, 2])
        r = parse("[Group] Title (01-12) [BD 1080p HEVC]")
        self.assertTrue(r.is_batch)
        self.assertEqual(r.episodes, list(range(1, 13)))

    def test_batch_without_range(self):
        r = parse("[Group] Title S02 Complete [1080p]")
        self.assertTrue(r.is_batch)
        self.assertEqual((r.season, r.episodes), (2, []))

    def test_season_in_title_and_version(self):
        r = parse("[Erai-raws] Title 2nd Season - 03v2 [720p]")
        self.assertEqual((r.season, r.season_src, r.episodes, r.version), (2, "title", [3], 2))
        self.assertEqual(r.base_names, ["Title"])

    def test_bilingual_title(self):
        r = parse("[Group] Shingeki no Kyojin / Attack on Titan - 10 [1080p]")
        self.assertEqual(r.names, ["Shingeki no Kyojin", "Attack on Titan"])

    def test_dual_and_dub(self):
        self.assertTrue(parse("[G] Title - 01 [1080p][Dual Audio]").dual_audio)
        self.assertTrue(parse("[G] Title - 01 [1080p][English Dub]").dub)

    def test_special_and_abs(self):
        r = parse("[G] Title - OVA 2 [1080p]")
        self.assertTrue(r.special)
        self.assertEqual(r.episodes, [2])
        self.assertEqual(parse("[G] One Piece - 1085 [1080p]").episodes, [1085])

    def test_title_with_number(self):
        r = parse("[G] Mob Psycho 100 - 05 [1080p]")
        self.assertEqual((r.names, r.episodes), (["Mob Psycho 100"], [5]))


class MatcherTests(unittest.TestCase):
    def setUp(self):
        fam = ["Show"]
        self.s1 = S(1, "Show", total=12, fam=fam)
        self.s2 = S(2, "Show 2nd Season", season=2, aoff=12, fam=fam)
        self.tracked = [self.s1, self.s2]

    def m(self, title):
        return [(x.series["id"], x.episodes) for x in match(parse(title), self.tracked)]

    def test_season_view(self):
        self.assertEqual(self.m("Show S02E03 1080p"), [(2, [3])])
        self.assertEqual(self.m("Show S01E03 1080p"), [(1, [3])])

    def test_specific_title_is_relative(self):
        self.assertEqual(self.m("[G] Show 2nd Season - 03 [1080p]"), [(2, [3])])

    def test_franchise_title_goes_absolute(self):
        self.assertEqual(self.m("[G] Show - 15 [1080p]"), [(2, [3])])
        self.assertEqual(self.m("[G] Show - 03 [1080p]"), [(1, [3])])

    def test_batch_range_spanning_seasons(self):
        got = dict(self.m("[G] Show (01-24) [BD 1080p]"))
        self.assertEqual(got[1], list(range(1, 13)))
        self.assertEqual(got[2], list(range(1, 13)))

    def test_split_cour_shares_season(self):
        a = S(1, "Show", total=12, fam=["Show"])
        b = S(2, "Show Part 2", season=1, soff=12, aoff=12, fam=["Show"])
        r = [(x.series["id"], x.episodes) for x in match(parse("Show S01E14 1080p"), [a, b])]
        self.assertEqual(r, [(2, [2])])

    def test_alias_japanese_english(self):
        s = S(1, "Shingeki no Kyojin", extra=["Attack on Titan"])
        r = match(parse("[G] Attack on Titan - 04 [1080p]"), [s])
        self.assertEqual(r[0].episodes, [4])

    def test_ova_and_movie(self):
        ova = S(3, "Show OVA", fmt="OVA", total=2, season=0, fam=["Show"])
        mov = S(4, "Show Movie", fmt="MOVIE", total=1, season=0, fam=["Show"])
        t = self.tracked + [ova, mov]
        self.assertEqual([(x.series["id"], x.episodes) for x in match(parse("[G] Show OVA - OVA 2 [1080p]"), t)],
                         [(3, [2])])
        self.assertEqual([x.series["id"] for x in match(parse("[G] Show Movie [BD 1080p]"), t)], [4])

    def test_unknown(self):
        self.assertEqual(self.m("[G] Other Thing - 01 [1080p]"), [])


class OtherTests(unittest.TestCase):
    def test_remap(self):
        layout = {1: 24, 2: 12}  # provider merged both cours into S1
        self.assertEqual(remap(layout, 0), (1, 0))
        self.assertEqual(remap(layout, 12), (1, 12))
        self.assertEqual(remap(layout, 24), (2, 0))
        self.assertIsNone(remap(layout, 99))

    def test_quality(self):
        item = {"title": "x", "seeders": 5}
        good = evaluate(parse("[SubsPlease] T - 01 (1080p)"), item, DEFAULT_PROFILE)
        low = evaluate(parse("[Other] T - 01 (720p)"), item, DEFAULT_PROFILE)
        self.assertGreater(good, low)
        self.assertIsNone(evaluate(parse("[G] T - 01 (2160p)"), item, DEFAULT_PROFILE))
        self.assertIsNone(evaluate(parse("[G] T - 01 (1080p) [English Dub]"), item, DEFAULT_PROFILE))
        dual = dict(DEFAULT_PROFILE, audio="dual")
        self.assertIsNone(evaluate(parse("[G] T - 01 (1080p)"), item, dual))
        self.assertIsNotNone(evaluate(parse("[G] T - 01 (1080p) [Dual Audio]"), item, dual))


if __name__ == "__main__":
    unittest.main()
