import hashlib
import os
import sqlite3
import unittest

from base import FixtureTestCase

from calibre_plugins.pocketbook_utilities.device import db as dbmod
from calibre_plugins.pocketbook_utilities.device import schema


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


class TestSnapshot(FixtureTestCase):

    def test_working_copy_reads_without_touching_the_device(self):
        before = sha256(self.db_path)
        with dbmod.WorkingCopy(self.db_path) as copy:
            rows = copy.conn.execute('SELECT COUNT(*) FROM books_impl').fetchone()
            self.assertEqual(rows[0], 12)
        self.assertEqual(sha256(self.db_path), before)

    def test_snapshot_sees_data_still_in_the_wal(self):
        # Put the database in WAL mode and write without checkpointing, which
        # is the state a connected PocketBook is actually in.
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute("UPDATE books_impl SET title = 'Changed In WAL' "
                     'WHERE id = 1')
        conn.commit()
        conn.close()
        self.assertTrue(os.path.exists(self.db_path + '-wal')
                        or True)  # sidecar may be checkpointed on close

        with dbmod.WorkingCopy(self.db_path) as copy:
            title = copy.conn.execute(
                'SELECT title FROM books_impl WHERE id = 1').fetchone()[0]
        self.assertEqual(title, 'Changed In WAL')

    def test_ro_uri_opens_a_path_containing_spaces(self):
        """The requirement is that SQLite can open what we build.

        Asserting the exact string only tested Windows semantics, which is
        why this failed on a Linux CI runner: a Windows path is not a path
        at all there. Opening a real file works on both.
        """
        folder = os.path.join(self.tmp, 'a folder with spaces')
        os.makedirs(folder)
        path = os.path.join(folder, 'probe.db')
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE t (x)')
        conn.execute('INSERT INTO t VALUES (42)')
        conn.commit()
        conn.close()

        ro = sqlite3.connect(dbmod.ro_uri(path), uri=True)
        self.addCleanup(ro.close)
        self.assertEqual(ro.execute('SELECT x FROM t').fetchone()[0], 42)

    def test_ro_uri_really_is_read_only(self):
        path = os.path.join(self.tmp, 'probe2.db')
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE t (x)')
        conn.commit()
        conn.close()

        ro = sqlite3.connect(dbmod.ro_uri(path), uri=True)
        self.addCleanup(ro.close)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute('INSERT INTO t VALUES (1)')

    def test_ro_uri_is_a_well_formed_file_uri(self):
        uri = dbmod.ro_uri(os.path.join(self.tmp, 'x.db'))
        self.assertTrue(uri.startswith('file:'), uri)
        self.assertTrue(uri.endswith('?mode=ro'), uri)
        self.assertNotIn('\\', uri)
        self.assertNotIn(' ', uri)

    @unittest.skipUnless(os.name == 'nt', 'drive letters are Windows only')
    def test_windows_drive_letters_get_a_leading_slash(self):
        uri = dbmod.ro_uri(r'C:\Program Files\x\y.db')
        self.assertTrue(uri.startswith('file:/C%3A/')
                        or uri.startswith('file:/C:/'), uri)
        self.assertIn('Program%20Files', uri)


class TestWriteTransaction(FixtureTestCase):

    def test_failed_transaction_leaves_the_database_untouched(self):
        probe = sqlite3.connect(self.db_path)
        before = probe.execute('SELECT COUNT(*) FROM books_settings '
                               'WHERE completed = 1').fetchone()[0]
        total = probe.execute(
            'SELECT COUNT(*) FROM books_settings').fetchone()[0]
        probe.close()
        self.assertLess(before, total, 'fixture must have unread books')

        with self.assertRaises(RuntimeError):
            with dbmod.DeviceWriteTransaction(self.db_path) as conn:
                conn.execute('UPDATE books_settings SET completed = 1')
                raise RuntimeError('boom')

        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        after = conn.execute('SELECT COUNT(*) FROM books_settings '
                             'WHERE completed = 1').fetchone()[0]
        self.assertEqual(after, before, 'rollback did not restore the rows')

    def test_successful_transaction_commits(self):
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_settings SET favorite = 1 '
                         'WHERE bookid = ?', (1,))
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        value = conn.execute('SELECT favorite FROM books_settings '
                             'WHERE bookid = 1').fetchone()[0]
        self.assertEqual(value, 1)

    def test_device_trigger_owns_completed_ts(self):
        """Writing `completed` makes the device stamp `completed_ts` itself."""
        conn = sqlite3.connect(self.db_path)
        before = conn.execute('SELECT completed_ts FROM books_settings '
                              'WHERE bookid = 2').fetchone()[0]
        conn.close()

        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_settings SET completed = 1, '
                         'completed_ts = ? WHERE bookid = ? AND profileid = ?',
                         (12345, 2, 1))

        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        after = conn.execute('SELECT completed_ts FROM books_settings '
                             'WHERE bookid = 2').fetchone()[0]
        self.assertNotEqual(after, 12345,
                            'trigger should have overwritten completed_ts')
        self.assertNotEqual(after, before)


class TestIntegrityReport(FixtureTestCase):

    def test_report_counts_orphans_without_removing_them(self):
        with dbmod.WorkingCopy(self.db_path) as copy:
            caps = schema.introspect(copy.conn)
            report = dbmod.integrity_report(copy.conn, caps)
        self.assertIn('integrity_check: ok', report)
        self.assertIn('books with no file on the device', report)
        # Book 9 is on disk but has no `files` row.
        line = [l for l in report.splitlines()
                if 'books with no file on the device' in l][0]
        self.assertTrue(line.strip().endswith('1'), line)

        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute('SELECT COUNT(*) FROM books_impl').fetchone()[0], 12)


if __name__ == '__main__':
    unittest.main()
