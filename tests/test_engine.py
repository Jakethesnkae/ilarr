import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ilarr.db import DB
from ilarr.engine import DEFAULT_CONFIG, Engine
from ilarr.parser import parse
from ilarr.web import redacted


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = DB(str(self.root / 'state.db'))
        self.addCleanup(self.db.c.close)
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.engine = Engine(self.cfg, self.db)
        sid = self.db.x('INSERT INTO series(title,path,season,season_offset,abs_offset) VALUES(?,?,?,?,?)',
                        ('Show', str(self.root / 'library'), 1, 0, 0))
        self.series = self.db.one('SELECT * FROM series WHERE id=?', (sid,))
        self.destination = self.root / 'library' / 'Season 01' / 'Show - S01E01 [1080p].mkv'
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b'original')
        self.db.x('INSERT INTO episodes(series_id,number,status,file) VALUES(?,?,?,?)',
                  (sid, 1, 'downloaded', str(self.destination)))
        self.source = self.root / 'source.mkv'
        self.source.write_bytes(b'replacement')
        self.rel = parse('Show - 01 [1080p]')
        self.download = dict(score=2000, res=1080, version=2, grp='G')

    def place(self):
        self.engine.place(self.series, str(self.source), [1], self.rel, self.download)

    def test_missing_source_preserves_existing_file_and_metadata(self):
        self.source.unlink()
        before = self.db.q('SELECT * FROM episodes')
        for mode in ('copy', 'hardlink', 'move'):
            with self.subTest(mode=mode):
                self.cfg['import_mode'] = mode
                with self.assertRaises(FileNotFoundError):
                    self.place()
                self.assertEqual(self.destination.read_bytes(), b'original')
                self.assertEqual(self.db.q('SELECT * FROM episodes'), before)
                self.assertEqual(list(self.destination.parent.glob('.ilarr-*')), [])

    def test_partial_copy_failure_preserves_existing_file(self):
        self.cfg['import_mode'] = 'copy'
        def fail_copy(src, dst):
            Path(dst).write_bytes(b'partial')
            raise OSError('disk full')
        with patch('ilarr.engine.shutil.copy2', side_effect=fail_copy):
            with self.assertRaises(OSError):
                self.place()
        self.assertEqual(self.destination.read_bytes(), b'original')
        self.assertEqual(list(self.destination.parent.glob('.ilarr-*')), [])

    def test_publication_failure_preserves_move_source(self):
        self.cfg['import_mode'] = 'move'
        with patch('ilarr.engine.os.replace', side_effect=OSError('cannot replace')):
            with self.assertRaises(OSError):
                self.place()
        self.assertEqual(self.source.read_bytes(), b'replacement')
        self.assertEqual(self.destination.read_bytes(), b'original')

    def test_successful_import_modes(self):
        for mode in ('copy', 'hardlink', 'move'):
            with self.subTest(mode=mode):
                self.cfg['import_mode'] = mode
                self.place()
                self.assertEqual(self.destination.read_bytes(), b'replacement')
                self.assertEqual(self.source.exists(), mode != 'move')
                if mode == 'hardlink':
                    self.assertTrue(os.path.samefile(self.source, self.destination))
                ep = self.db.one('SELECT * FROM episodes WHERE number=1')
                self.assertEqual((ep['status'], ep['version']), ('downloaded', 2))

    def test_hardlink_fallback_copies(self):
        with patch('ilarr.engine.os.link', side_effect=OSError('cross-device link')):
            self.place()
        self.assertEqual(self.destination.read_bytes(), b'replacement')
        self.assertTrue(self.source.exists())

    def test_shared_file_survives_until_last_episode_is_upgraded(self):
        shared = self.root / 'batch.mkv'
        shared.write_bytes(b'episodes 1 and 2')
        self.db.x('UPDATE episodes SET file=?', (str(shared),))
        self.db.x('INSERT INTO episodes(series_id,number,status,file) VALUES(?,?,?,?)',
                  (self.series['id'], 2, 'downloaded', str(shared)))
        self.place()
        self.assertEqual(shared.read_bytes(), b'episodes 1 and 2')
        self.assertEqual(self.db.one('SELECT file FROM episodes WHERE number=2')['file'], str(shared))
        self.engine.place(self.series, str(self.source), [2], self.rel, self.download)
        self.assertFalse(shared.exists())


class SelectionTests(unittest.TestCase):
    @patch('ilarr.engine.time.time', return_value=10000)
    def test_airtime_and_delay_boundaries(self, clock):
        engine = Engine(copy.deepcopy(DEFAULT_CONFIG), None)
        rel = parse('Show - 01 [1080p]')
        series = dict(profile={})
        for status in ('missing', 'downloaded'):
            for air_date, expected in ((None, False), (11000, False), (9999, False),
                                       (9401, False), (9400, True), (9399, True)):
                with self.subTest(status=status, air_date=air_date):
                    ep = dict(status=status, air_date=air_date, res=720, score=1, version=1)
                    self.assertEqual(engine._wants(ep, series, rel, 2000), expected)
        engine.cfg['release_delay_minutes'] = 0
        self.assertTrue(engine._wants(dict(status='missing', air_date=10000), series, rel, 2000))


class RedactionTests(unittest.TestCase):
    def test_masks_credentials_without_modifying_configuration(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg['tvdb']['pin'] = 'synthetic-pin'
        cfg['indexers'] = [dict(name='custom', api_key='synthetic-key'), dict(name='public')]
        for provider in ('prowlarr', 'tvdb', 'tmdb'):
            cfg[provider]['api_key'] = 'synthetic-provider-key'
        before = copy.deepcopy(cfg)
        result = redacted(cfg)
        self.assertEqual(result['tvdb']['pin'], '***')
        self.assertEqual(result['indexers'][0]['api_key'], '***')
        self.assertEqual(result['indexers'][1], dict(name='public'))
        self.assertEqual(result['qbittorrent']['password'], '***')
        for provider in ('prowlarr', 'tvdb', 'tmdb'):
            self.assertEqual(result[provider]['api_key'], '***')
        self.assertEqual(cfg, before)
