"""Write receipts must notice a revert and ignore ordinary reading.

The first version hashed whole books_settings rows, so reading a book
between connections looked identical to the reader having undone our work,
and a collections write was checked against a table we never touched.
"""

import sqlite3
import unittest

from base import FixtureTestCase

from calibre_plugins.pocketbook_utilities.device import db as dbmod
from calibre_plugins.pocketbook_utilities.device import receipt, schema
from calibre_plugins.pocketbook_utilities.sync import collections as collmod
from calibre_plugins.pocketbook_utilities.sync import identity
from calibre_plugins.pocketbook_utilities.sync.fields import BY_KEY
from calibre_plugins.pocketbook_utilities.sync.model import (
    BookLink, MatchConfidence, ProposedChange, Side, SyncPlan)


def link(device_book_id, title='A Book', calibre_id=1):
    return BookLink(calibre_id=calibre_id, device_book_id=device_book_id,
                    lpath='', device_path='', title=title,
                    confidence=MatchConfidence.EXACT_PATH)


def plan_with(*changes):
    plan = SyncPlan()
    plan.device_changes = list(changes)
    return plan


class ReceiptTestCase(FixtureTestCase):

    def conn(self):
        copy = dbmod.WorkingCopy(self.db_path)
        self.addCleanup(copy.close)
        return copy.open()


class TestBuilding(ReceiptTestCase):

    def test_a_favourite_write_is_recorded_as_one_column(self):
        plan = plan_with(ProposedChange(
            link=link(1), field='favourite', direction=Side.DEVICE,
            old=False, new=1, reason='test'))
        items = receipt.build(plan, 1, BY_KEY.get)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['column'], 'favorite')
        self.assertEqual(items[0]['value'], 1)

    def test_device_owned_columns_are_never_asserted_on(self):
        """Timestamps and page counters belong to the reader."""
        for column in ('completed_ts', 'favorite_ts', 'position_ts',
                       'cpage', 'npage', 'opentime'):
            self.assertIn(column, receipt.DEVICE_OWNED)

    def test_read_only_fields_contribute_nothing(self):
        plan = plan_with(ProposedChange(
            link=link(1), field='progress', direction=Side.DEVICE,
            old=None, new=50.0, reason='test'))
        self.assertEqual(receipt.build(plan, 1, BY_KEY.get), [])

    def test_collections_are_recorded_by_name(self):
        plan = plan_with(ProposedChange(
            link=link(1), field='collections', direction=Side.DEVICE,
            old=frozenset(), new=frozenset({'B', 'A'}), reason='test'))
        items = receipt.build(plan, 1, BY_KEY.get)
        self.assertEqual(items[0]['kind'], receipt.COLLECTIONS)
        self.assertEqual(items[0]['names'], ['A', 'B'])

    def test_unselected_changes_are_not_recorded(self):
        plan = plan_with(ProposedChange(
            link=link(1), field='favourite', direction=Side.DEVICE,
            old=False, new=1, reason='test', selected=False))
        self.assertEqual(receipt.build(plan, 1, BY_KEY.get), [])


class TestVerifying(ReceiptTestCase):

    def write_favourite(self, book_id=1):
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_settings SET favorite = 1 '
                         'WHERE bookid = ? AND profileid = 1', (book_id,))
        return receipt.build(plan_with(ProposedChange(
            link=link(book_id), field='favourite', direction=Side.DEVICE,
            old=False, new=1, reason='test')), 1, BY_KEY.get)

    def test_a_surviving_write_verifies(self):
        items = self.write_favourite()
        self.assertEqual(receipt.verify(self.conn(), items), [])

    def test_reading_the_book_is_not_mistaken_for_a_revert(self):
        """The exact false alarm: position and page counters moved."""
        items = self.write_favourite()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute(
                'UPDATE books_settings SET position = ?, position_ts = ?, '
                'cpage = ?, opentime = ? WHERE bookid = 1 AND profileid = 1',
                ('pbr:/webkit?##epubcfi(/6/99!/4)', 1799999999, 163,
                 1799999000))
        self.assertEqual(receipt.verify(self.conn(), items), [],
                         'reading a book must not look like a revert')

    def test_a_real_revert_is_caught(self):
        items = self.write_favourite()
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_settings SET favorite = 0 '
                         'WHERE bookid = 1 AND profileid = 1')
        failures = receipt.verify(self.conn(), items)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][1], 0)

    def test_a_missing_row_is_caught(self):
        items = receipt.build(plan_with(ProposedChange(
            link=link(9999), field='favourite', direction=Side.DEVICE,
            old=None, new=1, reason='test')), 1, BY_KEY.get)
        self.assertEqual(len(receipt.verify(self.conn(), items)), 1)


class TestCollectionsVerifying(ReceiptTestCase):

    def add(self, book_id, names):
        conn = self.conn()
        caps = schema.introspect(conn)
        shelves, memberships = identity.load_shelves(conn, caps)
        with dbmod.DeviceWriteTransaction(self.db_path) as device:
            writer = collmod.ShelfWriter(device, shelves, memberships, caps)
            writer.set_for_book(book_id, names, timestamp=100)
        return receipt.build(plan_with(ProposedChange(
            link=link(book_id), field='collections', direction=Side.DEVICE,
            old=frozenset(), new=frozenset(names), reason='test')),
            1, BY_KEY.get)

    def test_surviving_collections_verify(self):
        items = self.add(1, ['Sci-Fi', 'Favourites'])
        self.assertEqual(receipt.verify(self.conn(), items), [])

    def test_a_reverted_collection_is_caught(self):
        """What the row-hash version could not see at all."""
        items = self.add(1, ['Sci-Fi'])
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE bookshelfs_books SET is_deleted = 1 '
                         'WHERE bookid = 1')
        failures = receipt.verify(self.conn(), items)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][1], [])

    def test_reading_does_not_affect_a_collections_receipt(self):
        items = self.add(1, ['Sci-Fi'])
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            conn.execute('UPDATE books_settings SET cpage = 200, '
                         'position_ts = 1799999999 WHERE bookid = 1')
        self.assertEqual(receipt.verify(self.conn(), items), [])


class TestMessage(unittest.TestCase):

    def test_the_message_names_what_is_missing(self):
        failures = [({'kind': receipt.SETTING, 'title': 'Some Book',
                      'field': 'favourite', 'column': 'favorite',
                      'value': 1}, 0)]
        text = receipt.describe(failures, '2026-11-09 20:36')
        self.assertIn('Some Book', text)
        self.assertIn('favourite', text)
        self.assertIn('Eject', text)

    def test_collection_failures_list_the_names(self):
        failures = [({'kind': receipt.COLLECTIONS, 'title': 'Some Book',
                      'bookid': 1, 'names': ['Sci-Fi']}, [])]
        text = receipt.describe(failures, 'then')
        self.assertIn('Sci-Fi', text)
        self.assertIn('none', text)


if __name__ == '__main__':
    unittest.main()
