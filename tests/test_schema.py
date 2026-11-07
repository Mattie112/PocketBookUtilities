import os
import sqlite3
import unittest

from base import REAL_DUMP, FixtureTestCase, real_dump_available

from calibre_plugins.pocketbook_utilities.device import db as dbmod
from calibre_plugins.pocketbook_utilities.device import schema


class TestIntrospection(FixtureTestCase):

    def caps(self):
        conn = dbmod.connect_readonly(self.db_path)
        self.addCleanup(conn.close)
        return schema.introspect(conn)

    def test_reference_schema_reports_every_capability(self):
        caps = self.caps()
        self.assertEqual(caps.db_version, 39)
        self.assertTrue(caps.is_reference_version)
        for label, available in caps.describe():
            if label == 'User profiles':
                continue  # fixture has the table; flag depends on columns
            self.assertTrue(available, '%s should be available' % label)

    def test_fingerprint_is_stable_and_specific(self):
        first = self.caps().fingerprint
        second = self.caps().fingerprint
        self.assertEqual(first, second)
        self.assertEqual(len(first), 40)

    def test_has_checks_table_and_columns(self):
        caps = self.caps()
        self.assertTrue(caps.has('books_settings', 'completed', 'position'))
        self.assertFalse(caps.has('books_settings', 'no_such_column'))
        self.assertFalse(caps.has('no_such_table', 'x'))


class TestMissingFavouriteColumn(FixtureTestCase):
    caps_drop = ('books_settings.favorite',)

    def test_capability_turns_off(self):
        conn = dbmod.connect_readonly(self.db_path)
        self.addCleanup(conn.close)
        caps = schema.introspect(conn)
        self.assertFalse(caps.has_favorites)
        # Everything else still works.
        self.assertTrue(caps.has_reading_state)
        self.assertTrue(caps.has_position)


class TestMissingShelves(FixtureTestCase):
    caps_drop = ('bookshelfs_books', 'bookshelfs')

    def test_collections_capability_turns_off(self):
        conn = dbmod.connect_readonly(self.db_path)
        self.addCleanup(conn.close)
        caps = schema.introspect(conn)
        self.assertFalse(caps.has_shelves)
        self.assertTrue(caps.has_reading_state)
        self.assertNotIn('bookshelfs', caps.tables)


@unittest.skipUnless(real_dump_available(), 'no device dump at %s' % REAL_DUMP)
class TestAgainstRealDump(unittest.TestCase):
    """The fixture schema must match what the real device actually has."""

    def setUp(self):
        self.real_db = os.path.join(REAL_DUMP, 'system', 'explorer-3',
                                    'explorer-3.db')

    def test_real_database_reports_reference_version(self):
        copy = dbmod.WorkingCopy(self.real_db)
        self.addCleanup(copy.close)
        conn = copy.open()
        caps = schema.introspect(conn)
        self.assertEqual(caps.db_version, 39)
        self.assertTrue(caps.has_reading_state)
        self.assertTrue(caps.has_position)
        self.assertTrue(caps.has_page_counters)
        self.assertTrue(caps.has_opentime)
        self.assertTrue(caps.has_shelves)
        self.assertTrue(caps.has_shelf_uuid)
        self.assertTrue(caps.has_series)

    def test_fixture_fingerprint_matches_the_device(self):
        copy = dbmod.WorkingCopy(self.real_db)
        self.addCleanup(copy.close)
        real = schema.introspect(copy.open())

        import make_fixture
        import tempfile
        dest = os.path.join(tempfile.mkdtemp(prefix='pbutil-cmp-'), 'main')
        make_fixture.build_fixture(dest)
        conn = sqlite3.connect(os.path.join(dest, 'system', 'explorer-3',
                                            'explorer-3.db'))
        self.addCleanup(conn.close)
        fixture = schema.introspect(conn)
        self.assertEqual(real.fingerprint, fixture.fingerprint,
                         'fixture schema has drifted from the real device')


if __name__ == '__main__':
    unittest.main()
