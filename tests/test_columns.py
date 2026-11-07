"""Column setup, exercised against a real but throwaway calibre library.

Never touches the user's own library.
"""

import shutil
import tempfile
import unittest

from calibre_plugins.pocketbook_utilities import columns


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

    def test_a_plain_read_bool_can_stand_in(self):
        spec = columns.BY_FIELD['read']
        existing = {'read': {'datatype': 'bool', 'is_multiple': None,
                             'name': 'Read'}}
        self.assertEqual(columns.find_reusable(spec, existing), '#read')

    def test_wrong_datatype_is_not_reused(self):
        spec = columns.BY_FIELD['read']
        existing = {'read': {'datatype': 'text', 'is_multiple': None}}
        self.assertIsNone(columns.find_reusable(spec, existing))

    def test_multi_valued_mismatch_is_not_reused(self):
        spec = columns.BY_FIELD['collections']
        single = {'shelf': {'datatype': 'text', 'is_multiple': None}}
        self.assertIsNone(columns.find_reusable(spec, single))
        multi = {'shelf': {'datatype': 'text',
                           'is_multiple': {'cache_to_list': ','}}}
        self.assertEqual(columns.find_reusable(spec, multi), '#shelf')

    def test_exact_name_wins_over_another_compatible_column(self):
        spec = columns.BY_FIELD['read']
        existing = {
            'aaa_other': {'datatype': 'bool', 'is_multiple': None},
            'pb_read': {'datatype': 'bool', 'is_multiple': None},
        }
        self.assertEqual(columns.find_reusable(spec, existing), '#pb_read')


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

    def test_existing_read_column_is_reused_rather_than_duplicated(self):
        self.db.create_custom_column(label='read', name='Read',
                                     datatype='bool', is_multiple=False,
                                     display={})
        result = columns.ensure_columns(self.db, reuse_existing=True)
        self.assertEqual(result.reused.get('read'), '#read')
        self.assertNotIn('pb_read', self.lookup_names())

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
