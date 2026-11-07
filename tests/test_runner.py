"""Reading calibre columns, including ones calibre has not loaded yet."""

import unittest

from calibre_plugins.pocketbook_utilities.runner import read_column


class FakeApi:
    """Mimics calibre's Cache.all_field_for, including its KeyError."""

    def __init__(self, known):
        self.known = known
        self.calls = []

    def all_field_for(self, field, book_ids, default_value=None):
        self.calls.append(field)
        if field not in self.known:
            raise KeyError(field)
        return {book_id: self.known[field] for book_id in book_ids}


class TestReadColumn(unittest.TestCase):
    """A column created but not yet loaded must read as empty, not crash.

    db.create_custom_column writes the column immediately; calibre only
    picks it up on restart. Until then all_field_for raises KeyError, which
    crashed the dry run the wizard runs straight after setup.
    """

    def test_a_loaded_column_is_read(self):
        api = FakeApi({'#pb_read': True})
        self.assertEqual(read_column(api, '#pb_read', [1, 2]),
                         {1: True, 2: True})

    def test_an_unloaded_column_reads_as_empty(self):
        api = FakeApi({})
        self.assertEqual(read_column(api, '#pb_collections', [1, 2]), {})

    def test_an_unset_column_name_is_skipped_entirely(self):
        api = FakeApi({})
        self.assertEqual(read_column(api, '', [1]), {})
        self.assertEqual(read_column(api, None, [1]), {})
        self.assertEqual(api.calls, [])

    def test_other_failures_are_also_contained(self):
        class Exploding:
            def all_field_for(self, *a, **k):
                raise RuntimeError('database is busy')
        self.assertEqual(read_column(Exploding(), '#pb_read', [1]), {})


if __name__ == '__main__':
    unittest.main()
