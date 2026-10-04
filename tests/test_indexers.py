import unittest
from unittest import mock
from ilarr import indexers

XML = b"""<?xml version="1.0"?><rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>
<item><title>[SubsPlease] Show - 05 (1080p)</title><guid>g1</guid>
<enclosure url="http://prowlarr:9696/3/download?link=abc" length="1000" type="application/x-bittorrent"/>
<torznab:attr name="seeders" value="42"/><torznab:attr name="magneturl" value="magnet:?xt=urn:btih:ABC"/></item>
</channel></rss>"""


class TorznabTests(unittest.TestCase):
    def test_prowlarr_expansion_and_url(self):
        ix = indexers.prowlarr_indexers({"url": "http://p:9696/", "api_key": "K", "indexers": ["all"]})[0]
        self.assertEqual(ix["url"], "http://p:9696/all/api")
        url = indexers.build_url(ix, "frieren 05")
        self.assertIn("t=search", url)
        self.assertIn("apikey=K", url)
        self.assertIn("cat=5070", url)
        self.assertIn("q=frieren+05", url)

    def test_no_prowlarr_without_key(self):
        self.assertEqual(indexers.prowlarr_indexers({"url": "", "api_key": ""}), [])

    def test_fetch_parses_torznab(self):
        with mock.patch.object(indexers, "request", return_value=XML):
            items = indexers.fetch({"type": "torznab", "url": "http://p/all/api", "api_key": "K"})
        self.assertEqual((items[0]["seeders"], items[0]["url"]), (42, "magnet:?xt=urn:btih:ABC"))

    def test_error_document(self):
        err = b'<error code="100" description="Incorrect user credentials"/>'
        with mock.patch.object(indexers, "request", return_value=err):
            with self.assertRaises(RuntimeError):
                indexers.fetch({"type": "torznab", "url": "http://p/all/api"})


if __name__ == "__main__":
    unittest.main()
