import sqlite3
import unittest

from base import FixtureTestCase

from calibre_plugins.pocketbook_utilities.device import db as dbmod
from calibre_plugins.pocketbook_utilities.device import schema
from calibre_plugins.pocketbook_utilities.sync import collections as coll
from calibre_plugins.pocketbook_utilities.sync import identity


class CollectionTestCase(FixtureTestCase):

    def load(self):
        copy = dbmod.WorkingCopy(self.db_path)
        self.addCleanup(copy.close)
        conn = copy.open()
        caps = schema.introspect(conn)
        shelves, memberships = identity.load_shelves(conn, caps)
        return caps, shelves, memberships

    def writer(self, device_conn, caps, shelves, memberships):
        return coll.ShelfWriter(device_conn, shelves, memberships, caps)

    def device_shelves(self):
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        rows = conn.execute(
            'SELECT id, name, is_deleted, uuid FROM bookshelfs').fetchall()
        return {r[1]: {'id': r[0], 'is_deleted': r[2], 'uuid': r[3]}
                for r in rows}

    def memberships_for(self, book_id):
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        return {r[0]: r[1] for r in conn.execute(
            'SELECT bookshelfid, is_deleted FROM bookshelfs_books '
            'WHERE bookid = ?', (book_id,))}


class TestCreatingCollections(CollectionTestCase):

    def test_new_shelf_is_created_with_a_uuid(self):
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['Science Fiction'], timestamp=100)

        made = self.device_shelves()
        self.assertIn('Science Fiction', made)
        self.assertEqual(made['Science Fiction']['is_deleted'], 0)
        self.assertEqual(len(made['Science Fiction']['uuid']), 32)
        self.assertEqual(list(self.memberships_for(1).values()), [0])

    def test_shelf_names_with_quotes_survive_intact(self):
        nasty = "Sci-Fi 'n' Fantasy"
        injection = "x'); DROP TABLE bookshelfs;--"
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, [nasty, injection], timestamp=100)

        made = self.device_shelves()
        self.assertIn(nasty, made)
        self.assertIn(injection, made)
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute('SELECT COUNT(*) FROM bookshelfs').fetchone()[0], 2)

    def test_two_books_share_one_shelf_row(self):
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['Shared'], timestamp=100)
            writer.set_for_book(2, ['Shared'], timestamp=100)
        self.assertEqual(len(self.device_shelves()), 1)
        self.assertEqual(self.memberships_for(2), self.memberships_for(1))


class TestSoftDeletedShelves(CollectionTestCase):
    shelves = [('Archived', 1), ('Live', 0)]

    def test_existing_soft_deleted_shelf_is_undeleted_not_duplicated(self):
        """UNIQUE(name) ON CONFLICT FAIL means a blind INSERT would raise."""
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['Archived'], timestamp=100)

        made = self.device_shelves()
        self.assertEqual(len(made), 2, 'shelf was duplicated')
        self.assertEqual(made['Archived']['is_deleted'], 0)

    def test_name_matching_ignores_case(self):
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['live'], timestamp=100)
        self.assertEqual(len(self.device_shelves()), 2)


class TestRemoval(CollectionTestCase):

    def setUp(self):
        super().setUp()
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['Keep', 'Drop'], timestamp=100)

    def test_removal_is_a_soft_delete(self):
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['Keep'], remove_missing=True,
                                timestamp=200)

        states = self.memberships_for(1)
        self.assertEqual(sorted(states.values()), [0, 1])
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute('SELECT COUNT(*) FROM bookshelfs_books')
            .fetchone()[0], 2, 'rows must be kept, only flagged')

    def test_removal_can_be_turned_off(self):
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['Keep'], remove_missing=False,
                                timestamp=200)
        self.assertEqual(sorted(self.memberships_for(1).values()), [0, 0])

    def test_re_adding_undeletes_the_membership(self):
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['Keep'], timestamp=200)

        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            counts = writer.set_for_book(1, ['Keep', 'Drop'], timestamp=300)

        self.assertEqual(counts['readded'], 1)
        self.assertEqual(counts['added'], 0)
        self.assertEqual(sorted(self.memberships_for(1).values()), [0, 0])


class TestReadingBack(CollectionTestCase):
    shelves = [('Ghost', 1), ('Real', 0)]

    def test_deleted_shelves_and_memberships_are_not_reported(self):
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(1, ['Real'], timestamp=100)
            writer.set_for_book(2, ['Real'], timestamp=100)
        caps, shelves, memberships = self.load()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            writer = self.writer(conn, caps, shelves, memberships)
            writer.set_for_book(2, [], remove_missing=True, timestamp=200)

        caps, shelves, memberships = self.load()
        live = coll.device_collections_by_book(shelves, memberships)
        self.assertEqual(live.get(1), frozenset({'Real'}))
        self.assertNotIn(2, live)


class TestNameParsing(unittest.TestCase):

    def test_comma_separated_text_splits(self):
        self.assertEqual(coll.names_from_value('a, b , ,c'),
                         frozenset({'a', 'b', 'c'}))

    def test_lists_pass_through(self):
        self.assertEqual(coll.names_from_value(['a', ' b ']),
                         frozenset({'a', 'b'}))

    def test_none_is_empty(self):
        self.assertEqual(coll.names_from_value(None), frozenset())


if __name__ == '__main__':
    unittest.main()
