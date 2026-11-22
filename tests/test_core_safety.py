"""Regression coverage for the boundaries between planning and writing."""

import datetime
import os
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

from base import FixtureTestCase

from calibre_plugins.pocketbook_utilities import config, runner
from calibre_plugins.pocketbook_utilities.common.compat import PluginError
from calibre_plugins.pocketbook_utilities.device import db as dbmod, detect
from calibre_plugins.pocketbook_utilities.sync import metadata_push
from calibre_plugins.pocketbook_utilities.sync.engine import Mode
from calibre_plugins.pocketbook_utilities.sync.model import Side
from calibre_plugins.pocketbook_utilities.sync.state import UNKNOWN, SyncStateStore

from test_detect import FakeDriver, FakeGui


class TestRecovery(FixtureTestCase):

    def setUp(self):
        super().setUp()
        self.device = detect.describe_path(self.prefix)
        self.backups = os.path.join(self.tmp, 'backups')
        os.mkdir(self.backups)
        self.state_path = os.path.join(self.tmp, 'state.db')
        for name, value in (('backup_dir', self.backups),
                            ('state_db_path', self.state_path)):
            patcher = patch.object(runner, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def position(self, path):
        with dbmod.WorkingCopy(path) as copy:
            return copy.conn.execute(
                'SELECT position FROM books_settings WHERE bookid = 1'
            ).fetchone()[0]

    def test_empty_or_unrelated_backup_leaves_device_and_state_untouched(self):
        before = self.position(self.db_path)
        empty = os.path.join(self.backups, 'empty.db')
        with open(empty, 'wb'):
            pass
        unrelated = os.path.join(self.backups, 'unrelated.db')
        with closing(sqlite3.connect(unrelated)) as conn:
            conn.execute('CREATE TABLE unrelated (id INTEGER)')
        for backup in (empty, unrelated):
            with self.subTest(backup=backup):
                with self.assertRaisesRegex(PluginError, 'PocketBook library'):
                    runner.restore_backup(self.device, backup)
                self.assertEqual(self.position(self.db_path), before)
                self.assertFalse(os.path.exists(self.state_path))

    def test_version_mismatch_leaves_device_untouched(self):
        backup = runner.make_backup(self.device)
        before = self.position(self.db_path)
        with closing(sqlite3.connect(backup)) as conn, conn:
            conn.execute('UPDATE version SET id = id + 1')
        with self.assertRaisesRegex(PluginError, 'different PocketBook database version'):
            runner.restore_backup(self.device, backup)
        self.assertEqual(self.position(self.db_path), before)

    def test_unvalidated_backup_is_not_published(self):
        def create_empty(_source, destination):
            with open(destination, 'wb'):
                pass

        with patch.object(dbmod, 'snapshot', side_effect=create_empty):
            with self.assertRaises(PluginError):
                runner.make_backup(self.device)
        self.assertEqual(os.listdir(self.backups), [])

    def test_restore_resets_all_baselines_and_requires_review_per_library(self):
        before = self.position(self.db_path)
        with patch.object(runner, 'datetime') as clock:
            clock.now.return_value = datetime.datetime(2026, 11, 21, 14, 0)
            original = runner.make_backup(self.device)
            with dbmod.DeviceWriteTransaction(self.db_path) as conn:
                conn.execute("UPDATE books_settings SET position = 'new' WHERE bookid = 1")
            newer = runner.make_backup(self.device, keep=1)
        self.assertNotEqual(original, newer)
        self.assertEqual(self.position(original), before)
        self.assertEqual(self.position(newer), 'new')

        serial = self.device.serial
        with SyncStateStore(self.state_path) as store:
            for library in ('first', 'second'):
                store.record(library, serial, [(1, 'position', 'a', 'b')])
            store.set_receipt(serial, {'expectations': []})
        runner.restore_backup(self.device, original)
        self.assertEqual(self.position(self.db_path), before)
        with SyncStateStore(self.state_path) as store:
            self.assertIsNone(store.get_receipt(serial))
            self.assertEqual(store.load('first', serial)[(1, 'position')],
                             (UNKNOWN, UNKNOWN))
            self.assertTrue(store.review_after_restore('first', serial))
            self.assertTrue(store.review_after_restore('second', serial))
            store.finish_restore_review('first', serial)
            self.assertFalse(store.review_after_restore('first', serial))
            self.assertTrue(store.review_after_restore('second', serial))


class FakeCache:
    library_id = 'library'

    def __init__(self):
        self.fields = {'title': 'Project Hail Mary',
                       'authors': ('Andy Weir',),
                       'last_modified': 9999999999,
                       '#pb_position': None}

    def all_field_for(self, name, ids):
        return {book_id: self.fields[name] for book_id in ids}

    def all_book_ids(self):
        return {101}

    def set_field(self, name, values):
        self.fields[name] = values[101]


class TestSyncBoundary(FixtureTestCase):

    def setUp(self):
        super().setUp()
        self.state_path = os.path.join(self.tmp, 'state.db')
        backups = os.path.join(self.tmp, 'backups')
        os.mkdir(backups)
        for name, value in (('backup_dir', backups),
                            ('state_db_path', self.state_path),
                            ('plugin_prefs', {})):
            patcher = patch.object(runner, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.gui = FakeGui(FakeDriver(self.prefix, self.card_prefix))
        self.cache = FakeCache()
        self.gui.current_db = SimpleNamespace(new_api=self.cache)
        self.book = SimpleNamespace(application_id=101, size=2048, lpath=(
            'Books/Weir, Andy/Project Hail Mary - Andy Weir.epub'))
        self.gui.memory_view = SimpleNamespace(
            model=lambda: SimpleNamespace(db=[self.book]))
        self.device = detect.get_device(self.gui)
        self.settings = {'columns': {'position': '#pb_position'},
                         'enabled_fields': ['position']}

    def context(self, mode=Mode.MERGE):
        context = runner.SyncContext(self.gui, self.device, self.settings)
        self.addCleanup(context.close)
        context.read(mode=mode)
        return context

    def set_position(self, value):
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_settings SET position = ? WHERE bookid = 1',
                         (value,))

    def position(self):
        with dbmod.WorkingCopy(self.db_path) as copy:
            return copy.conn.execute(
                'SELECT position FROM books_settings WHERE bookid = 1'
            ).fetchone()[0]

    def test_a_new_reader_value_blocks_a_stale_review(self):
        self.cache.fields['#pb_position'] = 'calibre'
        context = self.context(Mode.FORCE_SEND)
        self.set_position('new on reader')
        with self.assertRaisesRegex(PluginError, 'changed during review'):
            context.apply()
        self.assertEqual(self.position(), 'new on reader')

    def test_a_new_calibre_value_blocks_a_stale_review(self):
        context = self.context(Mode.FORCE_LOAD)
        self.cache.fields['#pb_position'] = 'new in calibre'
        with self.assertRaisesRegex(PluginError, 'changed during review'):
            context.apply()
        self.assertEqual(self.cache.fields['#pb_position'], 'new in calibre')

    def test_replaced_device_file_blocks_a_stale_review(self):
        self.cache.fields['#pb_position'] = 'calibre'
        context = self.context(Mode.FORCE_SEND)
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE files SET fast_hash = ? WHERE book_id = 1',
                         ('new hash',))
        with self.assertRaisesRegex(PluginError, 'matched reader book changed'):
            context.apply()

    def test_live_metadata_changes_block_a_stale_review(self):
        context = self.context()
        old_title = context.index.by_book_id[1].title
        change = metadata_push.MetadataChange(
            1, 101, old_title, {'title': 'approved title'}, {'title': old_title})
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_impl SET title = ? WHERE id = 1', ('new title',))
        with self.assertRaisesRegex(PluginError, 'Reader metadata changed'):
            with context.copy.write() as conn:
                context.validate_device(conn, {1})
                metadata_push.validate_targets(conn, [change])
                metadata_push.apply_metadata(conn, [change])
        with dbmod.WorkingCopy(self.db_path) as copy:
            self.assertEqual(copy.conn.execute(
                'SELECT title FROM books_impl WHERE id = 1'
            ).fetchone()[0], 'new title')

    def test_unrelated_reader_changes_do_not_invalidate_review(self):
        self.cache.fields['#pb_position'] = 'approved'
        context = self.context(Mode.FORCE_SEND)
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_settings SET position = ? WHERE bookid = 2',
                         ('other book',))
        self.assertEqual(context.apply(), (0, 1))
        self.assertEqual(self.position(), 'approved')
        with dbmod.WorkingCopy(self.db_path) as copy:
            self.assertEqual(copy.conn.execute(
                'SELECT position FROM books_settings WHERE bookid = 2'
            ).fetchone()[0], 'other book')

    def test_idle_agreement_prevents_the_next_sync_from_reversing_direction(self):
        with SyncStateStore(self.state_path) as store:
            store.record('library', self.device.serial, [(101, 'position', 'A', 'A')])
        self.set_position('B')
        self.cache.fields['#pb_position'] = 'B'
        context = self.context()
        self.assertTrue(context.plan.is_empty)
        context.remember()
        self.set_position('C')
        next_context = self.context()
        self.assertEqual([(change.direction, change.new)
                          for change in next_context.plan.all_changes],
                         [(Side.CALIBRE, 'C')])

    def test_reverted_position_requires_review_even_in_automatic_load(self):
        self.set_position('before')
        self.cache.fields['#pb_position'] = 'intended'
        context = self.context(Mode.FORCE_SEND)
        self.assertEqual(context.apply(), (0, 1))

        self.set_position('before')
        verification = runner.verify_last_write(self.device, unattended=True)
        self.assertFalse(verification[0])
        runner.acknowledge_receipt(self.device)
        next_context = self.context()
        silent, review = runner.split_for_connect(next_context.plan,
                                                  config.ON_CONNECT_AUTO_LOAD)
        self.assertEqual(silent, [])
        self.assertEqual(len(review), 1)
        self.assertFalse(review[0].selected)
        self.assertEqual(review[0].direction, Side.CALIBRE)
        self.assertEqual(self.cache.fields['#pb_position'], 'intended')

        review[0].selected = True
        self.assertEqual(next_context.apply(), (1, 0))
        self.assertEqual(self.cache.fields['#pb_position'], 'before')
        with SyncStateStore(self.state_path) as store:
            self.assertEqual(store.get_receipt(self.device.serial)['pending'], [])

    def test_later_reading_is_not_treated_as_a_revert(self):
        self.set_position('before')
        self.cache.fields['#pb_position'] = 'intended'
        self.context(Mode.FORCE_SEND).apply()
        self.set_position('read further')
        outcome = runner.verify_last_write(self.device, unattended=True)
        self.assertTrue(outcome[0])
        context = self.context()
        silent, review = runner.split_for_connect(context.plan,
                                                  config.ON_CONNECT_AUTO_LOAD)
        self.assertEqual(review, [])
        self.assertEqual([change.new for change in silent], ['read further'])
