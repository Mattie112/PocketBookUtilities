import sqlite3
import unittest

from base import FixtureTestCase

from calibre_plugins.pocketbook_utilities.device import db as dbmod
from calibre_plugins.pocketbook_utilities.device import schema
from calibre_plugins.pocketbook_utilities.sync import identity, metadata_push
from calibre_plugins.pocketbook_utilities.sync.model import (
    BookLink, MatchConfidence)


class MetadataTestCase(FixtureTestCase):

    def load(self):
        copy = dbmod.WorkingCopy(self.db_path)
        self.addCleanup(copy.close)
        conn = copy.open()
        caps = schema.introspect(conn)
        index = identity.load_device_index(
            conn, caps, {'main': self.prefix, 'card': self.card_prefix})
        return caps, index

    @staticmethod
    def link(calibre_id, device_book_id, title=''):
        return BookLink(calibre_id=calibre_id, device_book_id=device_book_id,
                        lpath='', device_path='',
                        confidence=MatchConfidence.EXACT_PATH, title=title)


class TestBuildingChanges(MetadataTestCase):

    def test_broken_firstauthor_is_corrected_from_author_sort(self):
        caps, index = self.load()
        # Book 6 mirrors the real device: firstauthor starts with a digit.
        row = index.by_book_id[6]
        self.assertEqual(row.firstauthor,
                         '01, Clancy, Tom - Net Force Explorers')

        changes = metadata_push.build_metadata_changes(
            [self.link(100, 6, 'Virtual Vandals')],
            index.by_book_id,
            {100: {'title': 'Virtual Vandals',
                   'authors': 'Tom Clancy',
                   'author_sort': 'Clancy, Tom',
                   'series': 'Net Force Explorers',
                   'series_index': 1}},
            caps, push_author=True, push_series=True)

        self.assertEqual(len(changes), 1)
        columns = changes[0].columns
        self.assertEqual(columns['firstauthor'], 'Clancy, Tom')
        self.assertEqual(columns['first_author_letter'], 'C')
        self.assertEqual(columns['author'], 'Tom Clancy')
        self.assertEqual(columns['series'], 'Net Force Explorers')
        self.assertNotIn('title', columns, 'title not requested')

    def test_nothing_proposed_when_already_correct(self):
        caps, index = self.load()
        row = index.by_book_id[1]
        changes = metadata_push.build_metadata_changes(
            [self.link(100, 1)], index.by_book_id,
            {100: {'title': row.title, 'authors': row.author,
                   'author_sort': row.firstauthor,
                   'series': row.series, 'series_index': row.numinseries}},
            caps, push_title=True, push_author=True, push_series=True)
        self.assertEqual(changes, [])

    def test_missing_authors_only_skips_books_that_have_one(self):
        caps, index = self.load()
        links = [self.link(100, 1), self.link(107, 7)]
        meta = {
            100: {'authors': 'Someone Else', 'author_sort': 'Else, Someone'},
            107: {'authors': 'Found Author', 'author_sort': 'Author, Found'},
        }
        changes = metadata_push.build_metadata_changes(
            links, index.by_book_id, meta, caps, missing_authors_only=True)
        self.assertEqual([c.device_book_id for c in changes], [7])


class TestApplying(MetadataTestCase):

    def test_update_is_parameterised_and_allowlisted(self):
        statement, names = metadata_push.update_statement(
            ('author', 'firstauthor'))
        self.assertEqual(statement,
                         'UPDATE books_impl SET author = ?, firstauthor = ? '
                         'WHERE id = ?')
        self.assertEqual(names, ['author', 'firstauthor'])

        with self.assertRaises(ValueError):
            metadata_push.update_statement(('author', 'id; DROP TABLE x'))

    def test_apply_writes_and_the_device_reindexes_search(self):
        caps, index = self.load()
        changes = metadata_push.build_metadata_changes(
            [self.link(100, 6, 'Virtual Vandals')], index.by_book_id,
            {100: {'title': 'Virtual Vandals', 'authors': 'Tom Clancy',
                   'author_sort': 'Clancy, Tom', 'series': None,
                   'series_index': None}},
            caps, push_title=True, push_author=True, push_series=False)

        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            written = metadata_push.apply_metadata(conn, changes)
        self.assertEqual(written, 1)

        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        row = conn.execute('SELECT title, author, firstauthor, '
                           'first_author_letter FROM books_impl '
                           'WHERE id = 6').fetchone()
        self.assertEqual(row[0], 'Virtual Vandals')
        self.assertEqual(row[2], 'Clancy, Tom')
        self.assertEqual(row[3], 'C')

        # The device's own trigger keeps full-text search in step.
        found = conn.execute(
            'SELECT title, author FROM search WHERE book_id = 6').fetchone()
        self.assertEqual(found[0], 'Virtual Vandals')
        self.assertEqual(found[1], 'Tom Clancy')

    def test_titles_with_quotes_apply_cleanly(self):
        caps, index = self.load()
        changes = metadata_push.build_metadata_changes(
            [self.link(100, 1)], index.by_book_id,
            {100: {'title': "Mary's Tale; DROP TABLE books_impl;--",
                   'authors': "O'Hara, Mary", 'author_sort': "O'Hara, Mary"}},
            caps, push_title=True, push_author=True, push_series=False)
        with dbmod.DeviceWriteTransaction(self.db_path) as conn:
            metadata_push.apply_metadata(conn, changes)

        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        title = conn.execute(
            'SELECT title FROM books_impl WHERE id = 1').fetchone()[0]
        self.assertEqual(title, "Mary's Tale; DROP TABLE books_impl;--")
        self.assertEqual(
            conn.execute('SELECT COUNT(*) FROM books_impl').fetchone()[0], 12)


if __name__ == '__main__':
    unittest.main()


class TestThePreviewTellsTheTruth(MetadataTestCase):
    """The preview is the only safeguard for this write, so it must be real.

    old['first_author_letter'] and old['sort_title'] were hardcoded to None,
    so the dialog showed "None -> C" whatever the device actually held.
    """

    def test_old_values_come_from_the_device(self):
        caps, index = self.load()
        row = index.by_book_id[6]
        self.assertEqual(row.first_author_letter, '0',
                         'the device row must carry the real letter')
        self.assertEqual(row.sort_title, 'Virtual Vandals')

        changes = metadata_push.build_metadata_changes(
            [self.link(100, 6, 'Virtual Vandals')], index.by_book_id,
            {100: {'title': 'Virtual Vandals', 'title_sort': 'Virtual Vandals',
                   'authors': 'Tom Clancy', 'author_sort': 'Clancy, Tom'}},
            caps, push_title=True, push_author=True, push_series=False)

        change = changes[0]
        self.assertEqual(change.old['first_author_letter'], '0')
        self.assertEqual(change.columns['first_author_letter'], 'C')

    def test_a_misfiled_book_is_detectable_from_the_change(self):
        """What a caller needs to answer "which books does this rescue?"."""
        caps, index = self.load()
        changes = metadata_push.build_metadata_changes(
            [self.link(100, 6)], index.by_book_id,
            {100: {'authors': 'Tom Clancy', 'author_sort': 'Clancy, Tom'}},
            caps, push_author=True, push_series=False)
        misfiled = [c for c in changes
                    if str(c.old.get('first_author_letter') or '').isdigit()]
        self.assertEqual(len(misfiled), 1)
