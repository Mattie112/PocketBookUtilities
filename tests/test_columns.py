"""Column setup, exercised against a real but throwaway calibre library.

Never touches the user's own library.
"""

import shutil
import tempfile
import unittest

from calibre_plugins.pocketbook_utilities import columns

from base import needs_calibre_db

needs_calibre_db()


def make_library():
    from calibre.db.legacy import LibraryDatabase
    path = tempfile.mkdtemp(prefix='pbutil-lib-')
    return LibraryDatabase(path), path


class TestSpecs(unittest.TestCase):
    """Pure checks -- no library needed."""

    def test_every_spec_is_valid_for_calibre(self):
        for spec in columns.COLUMN_SPECS:
            self.assertIsNone(columns.validate(spec), spec.lookup)

    def test_lookup_names_are_unique_and_namespaced(self):
        names = [s.lookup for s in columns.COLUMN_SPECS]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(n.startswith('#pb_') for n in names), names)

    def test_only_collections_is_multi_valued(self):
        multi = [s.field for s in columns.COLUMN_SPECS if s.is_multiple]
        self.assertEqual(multi, ['collections'])

    def test_label_rules(self):
        self.assertTrue(columns.valid_label('#pb_read'))
        self.assertTrue(columns.valid_label('pb_read'))
        self.assertFalse(columns.valid_label('#PB_Read'))
        self.assertFalse(columns.valid_label('#1read'))
        self.assertFalse(columns.valid_label('#has space'))
        self.assertFalse(columns.valid_label('#'))

    def test_unused_label_suffixes(self):
        self.assertEqual(columns.unused_label('#pb_read', {}), 'pb_read')
        self.assertEqual(
            columns.unused_label('#pb_read', {'pb_read': {}}), 'pb_read_1')
        self.assertEqual(
            columns.unused_label('#pb_read',
                                 {'pb_read': {}, 'pb_read_1': {}}),
            'pb_read_2')

    def test_multiple_on_a_non_text_column_is_rejected(self):
        bad = columns.ColumnSpec(field='x', lookup='#pb_x', heading='X',
                                 datatype='bool', is_multiple=True)
        self.assertIn('cannot hold multiple values', columns.validate(bad))


class TestReuseDetection(unittest.TestCase):
    """Reuse must only ever claim a field's own column.

    Matching on datatype alone once mapped `favourite` onto an unrelated
    `#read` boolean, pointed `position` at `#kobo_reading_location`, and
    gave two fields the same column.
    """

    def test_only_the_fields_own_lookup_name_is_reused(self):
        spec = columns.BY_FIELD['read']
        existing = {'pb_read': {'datatype': 'bool', 'is_multiple': None}}
        self.assertEqual(columns.find_reusable(spec, existing), '#pb_read')

    def test_an_unrelated_compatible_column_is_never_claimed(self):
        spec = columns.BY_FIELD['read']
        existing = {'read': {'datatype': 'bool', 'is_multiple': None,
                             'name': 'Read'}}
        self.assertIsNone(columns.find_reusable(spec, existing))

    def test_kobo_columns_are_never_claimed(self):
        kobo = {
            'kobo_reading_location': {'datatype': 'text',
                                      'is_multiple': None},
            'kobo_percent_read': {'datatype': 'int', 'is_multiple': None},
            'kobo_last_read': {'datatype': 'datetime', 'is_multiple': None},
        }
        for field in ('position', 'progress', 'last_read', 'read_date'):
            spec = columns.BY_FIELD[field]
            self.assertIsNone(columns.find_reusable(spec, kobo),
                              '%s tried to claim a Kobo column' % field)

    def test_a_claimed_column_is_not_handed_out_twice(self):
        spec = columns.BY_FIELD['read']
        existing = {'pb_read': {'datatype': 'bool', 'is_multiple': None}}
        self.assertIsNone(
            columns.find_reusable(spec, existing, claimed={'#pb_read'}))

    def test_wrong_datatype_is_not_reused(self):
        spec = columns.BY_FIELD['read']
        existing = {'pb_read': {'datatype': 'text', 'is_multiple': None}}
        self.assertIsNone(columns.find_reusable(spec, existing))

    def test_multi_valued_mismatch_is_not_reused(self):
        spec = columns.BY_FIELD['collections']
        single = {'pb_collections': {'datatype': 'text',
                                     'is_multiple': None}}
        self.assertIsNone(columns.find_reusable(spec, single))
        multi = {'pb_collections': {'datatype': 'text',
                                    'is_multiple': {'cache_to_list': ','}}}
        self.assertEqual(columns.find_reusable(spec, multi),
                         '#pb_collections')


class TestAgainstAScratchLibrary(unittest.TestCase):

    def setUp(self):
        self.db, self.path = make_library()

    def tearDown(self):
        try:
            self.db.close()
        except Exception as err:          # pragma: no cover - cleanup only
            print('could not close scratch library: %s' % err)
        shutil.rmtree(self.path, ignore_errors=True)

    def lookup_names(self):
        return set(columns.current_custom_columns(self.db))

    def test_one_pass_creates_every_column(self):
        self.assertEqual(self.lookup_names(), set())
        result = columns.ensure_columns(self.db, reuse_existing=False)

        self.assertEqual(result.failed, {}, result.describe())
        self.assertEqual(len(result.created), len(columns.COLUMN_SPECS))
        self.assertTrue(result.needs_restart)

        present = columns.current_custom_columns(self.db)
        for spec in columns.COLUMN_SPECS:
            label = spec.lookup.lstrip('#')
            self.assertIn(label, present, spec.lookup)
            meta = present[label]
            self.assertEqual(meta['datatype'], spec.datatype, spec.lookup)
            self.assertEqual(bool(meta['is_multiple']), spec.is_multiple,
                             spec.lookup)
            self.assertEqual(meta['name'], spec.heading)

    def test_mapping_points_at_real_columns(self):
        result = columns.ensure_columns(self.db, reuse_existing=False)
        present = self.lookup_names()
        for field, lookup in result.mapping.items():
            self.assertIn(lookup.lstrip('#'), present, field)

    def test_running_twice_creates_nothing_more(self):
        columns.ensure_columns(self.db, reuse_existing=False)
        before = self.lookup_names()

        second = columns.ensure_columns(self.db, reuse_existing=True)
        self.assertEqual(second.created, {}, second.describe())
        self.assertEqual(len(second.reused), len(columns.COLUMN_SPECS))
        self.assertEqual(self.lookup_names(), before)

    def test_a_clashing_column_of_the_wrong_type_does_not_wedge_setup(self):
        """A pre-existing #pb_read of the wrong type must not break things."""
        self.db.create_custom_column(label='pb_read', name='PB Read',
                                     datatype='text', is_multiple=False,
                                     display={})
        result = columns.ensure_columns(self.db, reuse_existing=False)

        self.assertEqual(result.failed, {}, result.describe())
        self.assertEqual(result.created['read'], '#pb_read_1')
        present = columns.current_custom_columns(self.db)
        self.assertEqual(present['pb_read']['datatype'], 'text')
        self.assertEqual(present['pb_read_1']['datatype'], 'bool')

    def test_a_library_with_kobo_columns_keeps_them_untouched(self):
        """The exact library this plugin was first run against."""
        for label, name, datatype in (
                ('read', 'Read', 'bool'),
                ('kobo_reading_location', 'Kobo reading location', 'text'),
                ('kobo_percent_read', 'Kobo % read', 'int'),
                ('kobo_last_read', 'Kobo last read', 'datetime')):
            self.db.create_custom_column(label=label, name=name,
                                         datatype=datatype,
                                         is_multiple=False, display={})

        result = columns.ensure_columns(self.db, reuse_existing=True)

        self.assertEqual(result.failed, {}, result.describe())
        self.assertEqual(result.reused, {},
                         'nothing of the user\'s should have been claimed')
        self.assertEqual(len(result.created), len(columns.COLUMN_SPECS))
        for lookup in result.mapping.values():
            self.assertTrue(lookup.startswith('#pb_'),
                            '%s is not in the plugin namespace' % lookup)

    def test_no_two_fields_share_a_column(self):
        result = columns.ensure_columns(self.db, reuse_existing=True)
        lookups = list(result.mapping.values())
        self.assertEqual(len(lookups), len(set(lookups)),
                         'a column was handed to two fields: %r' % lookups)

    def test_rerunning_reuses_only_the_plugins_own_columns(self):
        first = columns.ensure_columns(self.db, reuse_existing=False)
        second = columns.ensure_columns(self.db, reuse_existing=True)
        self.assertEqual(second.created, {})
        self.assertEqual(second.mapping, first.mapping)

    def test_only_fields_limits_what_is_created(self):
        result = columns.ensure_columns(self.db, reuse_existing=False,
                                        only_fields={'read'})
        self.assertEqual(list(result.created), ['read'])
        self.assertEqual(self.lookup_names(), {'pb_read'})

    def test_values_can_actually_be_stored_in_the_new_columns(self):
        columns.ensure_columns(self.db, reuse_existing=False)
        # Reopen so the freshly created columns are part of the schema.
        self.db.close()
        from calibre.db.legacy import LibraryDatabase
        self.db = LibraryDatabase(self.path)
        api = self.db.new_api

        from calibre.ebooks.metadata.book.base import Metadata
        book_id = api.add_books(
            [(Metadata('Test Book', ['An Author']), {})])[0][0]

        api.set_field('#pb_read', {book_id: True})
        api.set_field('#pb_progress', {book_id: 42.5})
        api.set_field('#pb_position', {book_id: 'pbr:/webkit?##epubcfi(/6/2)'})
        api.set_field('#pb_collections', {book_id: ['Sci-Fi', "O'Brien"]})

        self.assertIs(api.field_for('#pb_read', book_id), True)
        self.assertAlmostEqual(api.field_for('#pb_progress', book_id), 42.5)
        self.assertEqual(api.field_for('#pb_position', book_id),
                         'pbr:/webkit?##epubcfi(/6/2)')
        self.assertEqual(sorted(api.field_for('#pb_collections', book_id)),
                         ["O'Brien", 'Sci-Fi'])


if __name__ == '__main__':
    unittest.main()


class TestPlanMatchesWhatHappens(unittest.TestCase):
    """The preview shown to the user must be what setup actually does."""

    def setUp(self):
        self.db, self.path = make_library()

    def tearDown(self):
        try:
            self.db.close()
        except Exception as err:          # pragma: no cover - cleanup only
            print('could not close scratch library: %s' % err)
        shutil.rmtree(self.path, ignore_errors=True)

    def test_plan_creates_nothing_by_itself(self):
        planned = columns.plan_setup(self.db)
        self.assertEqual(len(planned), len(columns.COLUMN_SPECS))
        self.assertTrue(all(p.action == 'create' for p in planned))
        self.assertEqual(columns.current_custom_columns(self.db), {},
                         'planning must not touch the library')

    def test_plan_then_apply_agree(self):
        planned = columns.plan_setup(self.db)
        promised = {p.field: p.lookup for p in planned
                    if p.action == 'create'}
        result = columns.ensure_columns(self.db)
        self.assertEqual(result.created, promised)
        self.assertEqual(result.failed, {})

    def test_plan_reports_reuse_on_a_second_run(self):
        columns.ensure_columns(self.db)
        planned = columns.plan_setup(self.db)
        self.assertTrue(all(p.action == 'reuse' for p in planned),
                        [p.describe() for p in planned])

    def test_planned_names_are_distinct(self):
        self.db.create_custom_column(label='pb_read', name='PB Read',
                                     datatype='text', is_multiple=False,
                                     display={})
        planned = columns.plan_setup(self.db)
        lookups = [p.lookup for p in planned if p.action == 'create']
        self.assertEqual(len(lookups), len(set(lookups)))
        self.assertIn('#pb_read_1', lookups)

    def test_description_names_columns_and_types(self):
        text = columns.describe_plan(columns.plan_setup(self.db))
        self.assertIn('Will create 7 column(s)', text)
        self.assertIn('#pb_read', text)
        self.assertIn('bool', text)
        self.assertIn('multiple values', text)

    def test_description_of_a_fully_set_up_library(self):
        columns.ensure_columns(self.db)
        text = columns.describe_plan(columns.plan_setup(self.db))
        self.assertIn('Will reuse 7', text)
        self.assertNotIn('Will create', text)


class TestCalibreFieldNames(unittest.TestCase):
    """Field names the plugin passes to all_field_for must be fetchable.

    'title_sort' appears in field_metadata but raises KeyError from
    all_field_for, so the metadata push crashed on its first real use
    whatever the user had ticked.
    """

    FIELDS = ('title', 'sort', 'authors', 'author_sort', 'series',
              'series_index', 'last_modified', 'tags')

    def setUp(self):
        self.db, self.path = make_library()
        from calibre.ebooks.metadata.book.base import Metadata
        self.book_id = self.db.new_api.add_books(
            [(Metadata('A Test Book', ['Tom Clancy']), {})])[0][0]

    def tearDown(self):
        try:
            self.db.close()
        except Exception as err:          # pragma: no cover - cleanup only
            print('could not close scratch library: %s' % err)
        shutil.rmtree(self.path, ignore_errors=True)

    def test_every_field_the_plugin_reads_is_fetchable(self):
        api = self.db.new_api
        for name in self.FIELDS:
            with self.subTest(field=name):
                api.all_field_for(name, [self.book_id])

    def test_title_sort_is_spelled_sort(self):
        api = self.db.new_api
        self.assertEqual(api.all_field_for('sort', [self.book_id])
                         [self.book_id], 'Test Book, A')
        with self.assertRaises(KeyError):
            api.all_field_for('title_sort', [self.book_id])

    def test_the_action_does_not_ask_for_title_sort(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'action.py'), encoding='utf-8') as fh:
            source = fh.read()
        self.assertNotIn("all_field_for('title_sort'", source)
