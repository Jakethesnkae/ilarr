import copy
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ilarr.db import DB
from ilarr.engine import DEFAULT_CONFIG, Engine
from ilarr.parser import parse
from ilarr.web import redacted


class ImportTests(unittest.TestCase):
    """Check import safety, transfer modes, and shared episode file cleanup."""

    def setUp(self):
        """Create an isolated database, existing episode, and replacement video."""
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
        """Import the replacement video for the fixture's first episode."""
        self.engine.place(self.series, str(self.source), [1], self.rel, self.download)

    def test_missing_source_preserves_existing_file_and_metadata(self):
        """Verify every import mode preserves the episode when its source is missing."""
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
        """Verify a failed copy preserves the destination and removes staged data."""
        self.cfg['import_mode'] = 'copy'
        def fail_copy(src, dst):
            """Simulate a copy that writes partial data before running out of space."""
            Path(dst).write_bytes(b'partial')
            raise OSError('disk full')
        with patch('ilarr.engine.shutil.copy2', side_effect=fail_copy):
            with self.assertRaises(OSError):
                self.place()
        self.assertEqual(self.destination.read_bytes(), b'original')
        self.assertEqual(list(self.destination.parent.glob('.ilarr-*')), [])

    def test_publication_failure_preserves_move_source(self):
        """Verify a failed move publication preserves both source and destination."""
        self.cfg['import_mode'] = 'move'
        before = self.db.q('SELECT * FROM episodes')
        with patch('ilarr.engine.os.replace', side_effect=OSError('cannot replace')):
            with self.assertRaises(OSError):
                self.place()
        self.assertEqual(self.source.read_bytes(), b'replacement')
        self.assertEqual(self.destination.read_bytes(), b'original')
        self.assertEqual(self.db.q('SELECT * FROM episodes'), before)

    def prepare_multi_episode_import(self):
        self.db.x('INSERT INTO episodes(series_id,number,status,file) VALUES(?,?,?,?)',
                  (self.series['id'], 2, 'downloaded', str(self.destination)))
        return self.destination.with_name('Show - S01E01-E02 [1080p].mkv')

    def reject_commit(self):
        # A deferred constraint fails at COMMIT, after both UPDATEs and publication.
        self.db.x('PRAGMA foreign_keys=ON')
        self.db.x('CREATE TABLE commit_parent(id INTEGER PRIMARY KEY)')
        self.db.x('CREATE TABLE commit_child(parent_id INTEGER REFERENCES commit_parent(id) '
                  'DEFERRABLE INITIALLY DEFERRED)')
        self.db.x('CREATE TRIGGER reject_commit AFTER UPDATE ON episodes '
                  'BEGIN INSERT INTO commit_child VALUES(1); END')

    def test_second_episode_update_failure_rolls_back_import(self):
        target = self.prepare_multi_episode_import()
        target.write_bytes(b'previous batch')
        self.cfg['import_mode'] = 'move'
        before = self.db.q('SELECT * FROM episodes')
        self.db.x("CREATE TRIGGER reject_update BEFORE UPDATE ON episodes WHEN NEW.number=2 "
                  "BEGIN SELECT RAISE(ABORT, 'update failed'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'update failed'):
            self.engine.place(self.series, str(self.source), [1, 2], self.rel, self.download)
        self.assertEqual(self.db.q('SELECT * FROM episodes'), before)
        self.assertEqual(target.read_bytes(), b'previous batch')
        self.assertEqual(self.destination.read_bytes(), b'original')
        self.assertEqual(self.source.read_bytes(), b'replacement')
        self.assertFalse(self.db.c.in_transaction)
        self.assertEqual(list(target.parent.glob('.ilarr-*')), [])

    def test_commit_failure_restores_files_and_all_episode_metadata(self):
        target = self.prepare_multi_episode_import()
        self.cfg['import_mode'] = 'move'
        before = self.db.q('SELECT * FROM episodes')
        self.reject_commit()
        for existing in (False, True):
            with self.subTest(existing_destination=existing):
                if existing:
                    target.write_bytes(b'previous batch')
                with self.assertRaises(sqlite3.IntegrityError):
                    self.engine.place(self.series, str(self.source), [1, 2], self.rel, self.download)
                self.assertEqual(self.db.q('SELECT * FROM episodes'), before)
                self.assertEqual(target.exists(), existing)
                if existing:
                    self.assertEqual(target.read_bytes(), b'previous batch')
                self.assertEqual(self.destination.read_bytes(), b'original')
                self.assertEqual(self.source.read_bytes(), b'replacement')
                self.assertFalse(self.db.c.in_transaction)
                self.assertEqual(list(target.parent.glob('.ilarr-*')), [])

    def test_failed_file_rollback_retains_recoverable_backup(self):
        self.reject_commit()
        replace = os.replace

        def fail_restore(src, dst):
            if Path(src).name == 'previous.mkv':
                raise OSError('cannot restore')
            return replace(src, dst)

        before = self.db.q('SELECT * FROM episodes')
        with patch('ilarr.engine.os.replace', side_effect=fail_restore):
            with self.assertLogs('ilarr', level='ERROR'):
                with self.assertRaisesRegex(OSError, 'cannot restore'):
                    self.place()
        self.assertEqual(self.db.q('SELECT * FROM episodes'), before)
        backups = list(self.destination.parent.glob('.ilarr-*/previous.mkv'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'original')

    def test_multi_episode_success_commits_before_cleanup(self):
        target = self.prepare_multi_episode_import()
        target.write_bytes(b'previous batch')
        self.cfg['import_mode'] = 'move'
        remove = os.remove

        def check_committed_before_remove(path):
            with sqlite3.connect(str(self.root / 'state.db')) as observer:
                rows = observer.execute('SELECT file, version FROM episodes').fetchall()
            self.assertEqual(rows, [(str(target), 2), (str(target), 2)])
            return remove(path)

        with patch('ilarr.engine.os.remove', side_effect=check_committed_before_remove):
            self.engine.place(self.series, str(self.source), [1, 2], self.rel, self.download)
        self.assertEqual(target.read_bytes(), b'replacement')
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.source.exists())
        self.assertEqual(list(target.parent.glob('.ilarr-*')), [])

    def test_successful_import_modes(self):
        """Check file contents, source retention, and metadata for each import mode."""
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
        """Verify an unavailable hardlink falls back to copying and retains the source."""
        with patch('ilarr.engine.os.link', side_effect=OSError('cross-device link')):
            self.place()
        self.assertEqual(self.destination.read_bytes(), b'replacement')
        self.assertTrue(self.source.exists())

    def test_shared_file_survives_until_last_episode_is_upgraded(self):
        """Keep a shared video until its last referencing episode is upgraded."""
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

    def test_move_preserves_source_referenced_by_another_episode(self):
        self.cfg['import_mode'] = 'move'
        self.db.x('UPDATE episodes SET file=?', (str(self.source),))
        self.db.x('INSERT INTO episodes(series_id,number,status,file) VALUES(?,?,?,?)',
                  (self.series['id'], 2, 'downloaded', str(self.source)))
        self.place()
        self.assertEqual(self.source.read_bytes(), b'replacement')
        self.assertEqual(self.db.one('SELECT file FROM episodes WHERE number=2')['file'], str(self.source))
        self.engine.place(self.series, str(self.source), [2], self.rel, self.download)
        self.assertFalse(self.source.exists())

    def test_move_preserves_source_when_it_is_the_destination(self):
        self.cfg['import_mode'] = 'move'
        self.source = self.destination
        self.place()
        self.assertEqual(self.destination.read_bytes(), b'original')


class SelectionTests(unittest.TestCase):
    """Check release eligibility at airtime and release delay boundaries."""

    @patch('ilarr.engine.time.time', return_value=10000)
    def test_airtime_and_delay_boundaries(self, clock):
        """Verify missing episodes and upgrades both respect the release delay."""
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
    """Check credential masking in configuration views."""

    def test_masks_credentials_without_modifying_configuration(self):
        """Verify credentials are masked in a copy without changing the configuration."""
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
