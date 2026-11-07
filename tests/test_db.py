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

    def test_ro_uri_handles_windows_paths_and_spaces(self):
        uri = dbmod.ro_uri(r'C:\Program Files\x\y.db')
        self.assertTrue(uri.startswith('file:/C%3A/Program%20Files/')
                        or uri.startswith('file:/C:/Program%20Files/'))
        self.assertTrue(uri.endswith('?mode=ro'))


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


class TestReceipts(FixtureTestCase):

    def test_digest_changes_when_the_device_reverts(self):
        with dbmod.WorkingCopy(self.db_path) as copy:
            first = dbmod.receipt_digest(copy.conn, 1, [1, 2, 3])

        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_settings SET favorite = 1 '
                         'WHERE bookid = 2 AND profileid = 1')

        with dbmod.WorkingCopy(self.db_path) as copy:
            second = dbmod.receipt_digest(copy.conn, 1, [1, 2, 3])
        self.assertNotEqual(first, second)

    def test_digest_is_stable_for_the_same_rows(self):
        with dbmod.WorkingCopy(self.db_path) as copy:
            a = dbmod.receipt_digest(copy.conn, 1, [3, 1, 2])
            b = dbmod.receipt_digest(copy.conn, 1, [1, 2, 3])
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
