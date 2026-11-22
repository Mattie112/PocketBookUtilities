import json
import os
import sqlite3
import unittest

from base import REAL_DUMP, FixtureTestCase, real_dump_available

from calibre_plugins.pocketbook_utilities.device import db as dbmod
from calibre_plugins.pocketbook_utilities.device import schema
from calibre_plugins.pocketbook_utilities.sync import identity
from calibre_plugins.pocketbook_utilities.sync.model import DeviceFileRow, MatchConfidence

import make_fixture


def calibre_books_from_fixture(books=None):
    """What calibre would report for the fixture's books."""
    out = []
    for book in (books or make_fixture.default_books()):
        if not book['on_disk']:
            continue
        out.append({
            'id': 1000 + book['id'],
            'lpath': book['folder'] + '/' + book['filename'],
            'title': book['title'],
            'authors': book['author'],
            'size': book['size'],
        })
    return out


class IdentityTestCase(FixtureTestCase):

    def load(self):
        copy = dbmod.WorkingCopy(self.db_path)
        self.addCleanup(copy.close)
        conn = copy.open()
        caps = schema.introspect(conn)
        prefixes = {'main': self.prefix, 'card': self.card_prefix}
        index = identity.load_device_index(conn, caps, prefixes)
        return conn, caps, index


class TestStorageRoots(IdentityTestCase):

    def test_roots_are_verified_against_the_filesystem(self):
        conn, caps, index = self.load()
        self.assertIn(1, index.roots)
        self.assertEqual(index.roots[1]['root'], '/mnt/ext1')
        self.assertEqual(index.roots[1]['location'], 'main')
        self.assertIn(2, index.roots)
        self.assertEqual(index.roots[2]['root'], '/mnt/ext2')
        self.assertEqual(index.roots[2]['location'], 'card')

    def test_relpaths_strip_the_device_root(self):
        conn, caps, index = self.load()
        row = index.by_book_id[1]
        self.assertEqual(row.relpath,
                         'Books/Weir, Andy/Project Hail Mary - Andy Weir.epub')


class TestMatching(IdentityTestCase):

    def test_every_on_disk_book_matches_by_exact_path(self):
        conn, caps, index = self.load()
        books = calibre_books_from_fixture()
        links, unmatched, device_only = identity.build_links(index, books)

        by_calibre = {l.calibre_id: l for l in links}
        # Book 9 is on disk but has no `files` row, so it cannot match.
        self.assertEqual(unmatched, [1009])
        for link in links:
            self.assertEqual(link.confidence, MatchConfidence.EXACT_PATH,
                             '%s matched only by %s'
                             % (link.title, link.confidence.label))
        self.assertEqual(len(links), len(books) - 1)

    def test_apostrophes_and_sql_metacharacters_match(self):
        conn, caps, index = self.load()
        books = calibre_books_from_fixture()
        links, _unmatched, _only = identity.build_links(index, books)
        titles = {l.title for l in links}
        self.assertIn("Harry Potter and the Philosopher's Stone", titles)
        self.assertIn("O'Brien's Last Stand", titles)
        self.assertIn('Title; DROP TABLE books_impl;--', titles)
        self.assertIn('Ünïcödé Trävels', titles)
        # The injection attempt did not damage the database.
        self.assertEqual(
            conn.execute('SELECT COUNT(*) FROM books_impl').fetchone()[0], 12)

    def test_case_differences_in_the_path_still_match(self):
        conn, caps, index = self.load()
        books = calibre_books_from_fixture()
        for book in books:
            book['lpath'] = book['lpath'].upper()
        links, unmatched, _only = identity.build_links(index, books)
        self.assertEqual(unmatched, [1009])
        self.assertTrue(all(l.confidence == MatchConfidence.EXACT_PATH
                            for l in links))

    def test_renamed_folder_falls_back_to_filename_and_size(self):
        conn, caps, index = self.load()
        books = calibre_books_from_fixture()
        target = [b for b in books if b['id'] == 1001][0]
        target['lpath'] = 'Somewhere/Else/' + os.path.basename(target['lpath'])
        links, _unmatched, _only = identity.build_links(index, books)
        link = [l for l in links if l.calibre_id == 1001][0]
        self.assertEqual(link.confidence, MatchConfidence.BASENAME_SIZE)
        self.assertEqual(link.device_book_id, 1)

    def test_renamed_file_falls_back_to_title_and_author(self):
        conn, caps, index = self.load()
        books = calibre_books_from_fixture()
        target = [b for b in books if b['id'] == 1001][0]
        target['lpath'] = 'Books/Weir, Andy/totally-different-name.epub'
        links, _unmatched, _only = identity.build_links(index, books)
        link = [l for l in links if l.calibre_id == 1001][0]
        self.assertEqual(link.confidence, MatchConfidence.METADATA)
        self.assertEqual(link.device_book_id, 1)
        self.assertFalse(link.is_trusted,
                         'metadata matches must not be trusted by default')

    def test_books_only_on_the_device_are_reported_not_touched(self):
        conn, caps, index = self.load()
        books = [b for b in calibre_books_from_fixture() if b['id'] != 1001]
        links, _unmatched, device_only = identity.build_links(index, books)
        ids = {r.book_id for r in device_only}
        self.assertIn(1, ids)        # dropped from the library side
        self.assertIn(8, ids)        # in the database but not on disk
        self.assertNotIn(2, ids)

    def test_a_row_is_never_claimed_twice(self):
        conn, caps, index = self.load()
        books = calibre_books_from_fixture()
        duplicate = dict(books[0])
        duplicate['id'] = 9999
        links, unmatched, _only = identity.build_links(index, books + [duplicate])
        device_ids = [l.device_book_id for l in links]
        self.assertEqual(len(device_ids), len(set(device_ids)))
        self.assertIn(9999, unmatched)


class TestVolumeMatching(unittest.TestCase):

    def setUp(self):
        path = 'Books/a.epub'
        self.main = DeviceFileRow(1, 1, '/mnt/ext1/Books', 'a.epub', 100, relpath=path)
        self.card = DeviceFileRow(2, 2, '/mnt/ext2/Books', 'a.epub', 100, relpath=path)
        self.roots = {1: {'location': 'main'}, 2: {'location': 'card_a'}}
        self.book = {'id': 10, 'lpath': path, 'size': 100, 'location': 'main'}

    def match(self, rows, books):
        return identity.build_links(identity.DeviceIndex(rows, self.roots), books)

    def test_same_path_on_two_volumes_uses_the_correct_volume(self):
        links, unmatched, _only = self.match([self.card, self.main], [self.book])
        self.assertEqual(unmatched, [])
        self.assertEqual((links[0].device_book_id, links[0].confidence),
                         (1, MatchConfidence.EXACT_PATH))

    def test_card_copy_cannot_match_when_only_main_copy_is_reported(self):
        links, unmatched, _only = self.match([self.card], [self.book])
        self.assertEqual(links, [])
        self.assertEqual(unmatched, [10])

    def test_two_equal_copies_of_one_calibre_book_remain_ambiguous(self):
        second = dict(self.book, location='card_a')
        links, unmatched, _only = self.match([self.main, self.card],
                                              [self.book, second])
        self.assertEqual(links, [])
        self.assertEqual(unmatched, [10])

    def test_a_known_size_mismatch_does_not_get_filename_confidence(self):
        for path in ('Books/a.epub', 'Moved/a.epub'):
            with self.subTest(path=path):
                book = dict(self.book, lpath=path, size=101)
                links, unmatched, _only = self.match([self.main], [book])
                self.assertEqual(links, [])
                self.assertEqual(unmatched, [10])


class TestReadingState(IdentityTestCase):

    def test_state_is_loaded_for_the_active_profile(self):
        conn, caps, index = self.load()
        states = identity.load_reading_state(conn, caps, 1)
        self.assertEqual(states[1].completed, 1)
        self.assertEqual(states[1].progress, 100.0)
        self.assertEqual(states[2].progress, 37.21)
        self.assertEqual(states[3].favorite, 1)
        self.assertNotIn(11, states, 'book with no settings row')

    def test_zero_page_count_does_not_divide_by_zero(self):
        conn, caps, index = self.load()
        states = identity.load_reading_state(conn, caps, 1)
        self.assertIsNone(states[12].progress)

    def test_missing_state_synthesises_an_empty_one(self):
        conn, caps, index = self.load()
        states = identity.load_reading_state(conn, caps, 1)
        books = calibre_books_from_fixture()
        links, _u, _d = identity.build_links(index, books)
        link = [l for l in links if l.device_book_id == 11][0]
        state = identity.state_for(states, link)
        self.assertFalse(state.exists)
        self.assertIsNone(state.completed)


class TestDegradedSchema(IdentityTestCase):
    caps_drop = ('books_settings.favorite',)

    def test_state_loads_without_the_favorite_column(self):
        conn, caps, index = self.load()
        states = identity.load_reading_state(conn, caps, 1)
        self.assertFalse(caps.has_favorites)
        self.assertIsNone(states[1].favorite)
        self.assertEqual(states[1].completed, 1)


@unittest.skipUnless(real_dump_available(), 'no device dump at %s' % REAL_DUMP)
class TestAgainstRealDump(unittest.TestCase):

    def setUp(self):
        self.db = os.path.join(REAL_DUMP, 'system', 'explorer-3',
                               'explorer-3.db')
        self.copy = dbmod.WorkingCopy(self.db)
        self.addCleanup(self.copy.close)
        self.conn = self.copy.open()
        self.caps = schema.introspect(self.conn)
        self.index = identity.load_device_index(
            self.conn, self.caps, {'main': REAL_DUMP, 'card': None})

    def real_calibre_books(self):
        path = os.path.join(REAL_DUMP, 'metadata.calibre')
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        out = []
        for entry in data:
            if entry.get('application_id') is None:
                continue
            out.append({
                'id': entry['application_id'],
                'lpath': entry.get('lpath') or '',
                'title': entry.get('title'),
                'authors': ' & '.join(entry.get('authors') or []),
                'size': entry.get('size'),
            })
        return out

    def test_storage_root_is_mnt_ext1(self):
        self.assertEqual(self.index.roots[1]['root'], '/mnt/ext1')
        self.assertEqual(len(self.index), 476)

    def test_real_library_matches_by_exact_path(self):
        books = self.real_calibre_books()
        links, unmatched, device_only = identity.build_links(
            self.index, books)
        exact = [l for l in links if l.confidence == MatchConfidence.EXACT_PATH]
        self.assertGreaterEqual(
            len(exact), 470,
            'only %d/%d matched by exact path' % (len(exact), len(links)))
        self.assertEqual(len(links), len(exact),
                         'some books needed a weaker match: %r'
                         % [(l.title, l.confidence.label) for l in links
                            if l.confidence != MatchConfidence.EXACT_PATH][:5])

    def test_real_reading_state_is_readable(self):
        states = identity.load_reading_state(self.conn, self.caps, 1)
        self.assertEqual(len(states), 472)
        completed = [s for s in states.values() if s.completed]
        self.assertEqual(len(completed), 158)
        positions = [s for s in states.values() if s.position]
        self.assertTrue(positions)
        self.assertTrue(any(s.position.startswith('pbr:/')
                            for s in positions))

    def test_no_collections_exist_on_the_device(self):
        shelves, memberships = identity.load_shelves(self.conn, self.caps)
        self.assertEqual(shelves, {})
        self.assertEqual(memberships, {})


if __name__ == '__main__':
    unittest.main()


class TestOlderBooksImpl(IdentityTestCase):
    """Columns not every firmware has must not take the whole sync down."""

    OPTIONAL = {'first_author_letter', 'sort_title', 'series', 'numinseries'}

    def test_missing_optional_columns_are_not_selected(self):
        import dataclasses
        copy = dbmod.WorkingCopy(self.db_path)
        self.addCleanup(copy.close)
        conn = copy.open()
        caps = schema.introspect(conn)
        cols = dict(caps.cols)
        cols['books_impl'] = frozenset(cols['books_impl']) - self.OPTIONAL
        older = dataclasses.replace(caps, cols=cols)

        sql = identity.files_sql(older)
        for name in self.OPTIONAL:
            self.assertNotIn('bi.%s' % name, sql)

        index = identity.load_device_index(
            conn, older, {'main': self.prefix, 'card': self.card_prefix})
        self.assertTrue(len(index))
        row = index.rows[0]
        self.assertIsNone(row.sort_title)
        self.assertIsNone(row.first_author_letter)
        self.assertIsNone(row.series)

    def test_present_columns_are_still_read(self):
        _conn, _caps, index = self.load()
        self.assertTrue(any(r.first_author_letter for r in index.rows))


class TestSingleBookFolder(unittest.TestCase):
    """Every book in one folder must not make that folder the root."""

    def test_the_known_storage_root_is_resolved_not_the_folder(self):
        self.assertEqual(self.resolve_root('/mnt/ext1/Books'), '/mnt/ext1')

    def test_an_unknown_root_uses_the_shortest_matching_root(self):
        self.assertEqual(self.resolve_root('/mnt/int0/Books'), '/mnt/int0')

    def resolve_root(self, device_folder):
        import shutil
        import tempfile
        prefix = tempfile.mkdtemp(prefix='pbutil-root-')
        self.addCleanup(shutil.rmtree, prefix, True)
        os.makedirs(os.path.join(prefix, 'Books'))
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE folders(id INTEGER PRIMARY KEY, '
                     'storageid INTEGER, name TEXT)')
        conn.execute('INSERT INTO folders VALUES (1, 1, ?)', (device_folder,))
        return identity.resolve_storage_roots(conn, {'main': prefix})[1]['root']


class TestShelvesWithoutSoftDelete(unittest.TestCase):
    """Firmware with no is_deleted columns must still read and add."""

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            'CREATE TABLE bookshelfs(id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name TEXT, ts INTEGER);'
            'CREATE TABLE bookshelfs_books(bookshelfid INTEGER, '
            'bookid INTEGER, ts INTEGER, PRIMARY KEY(bookshelfid, bookid));'
            "INSERT INTO bookshelfs (name, ts) VALUES ('Old', 1);"
            'INSERT INTO bookshelfs_books VALUES (1, 7, 1);')
        self.caps = schema.introspect(self.conn)

    def test_shelves_load(self):
        self.assertFalse(self.caps.has_shelf_is_deleted)
        shelves, memberships = identity.load_shelves(self.conn, self.caps)
        self.assertEqual(shelves[1]['name'], 'Old')
        self.assertEqual(memberships, {7: {1: 0}})

    def test_adding_works_and_removal_is_declined(self):
        from calibre_plugins.pocketbook_utilities.sync import (
            collections as collmod)
        shelves, memberships = identity.load_shelves(self.conn, self.caps)
        writer = collmod.ShelfWriter(self.conn, shelves, memberships,
                                     self.caps)
        counts = writer.set_for_book(7, ['New'], timestamp=5)
        self.assertEqual(counts['added'], 1)
        self.assertEqual(counts['not_removable'], 1,
                         'a membership must never be erased outright')
        self.assertEqual(
            self.conn.execute('SELECT COUNT(*) FROM bookshelfs_books '
                              'WHERE bookid = 7').fetchone()[0], 2)
