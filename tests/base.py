"""Shared test scaffolding."""

import os
import shutil
import tempfile
import unittest

import make_fixture

REAL_DUMP = r'C:\temp\pocketbook'


def real_dump_available():
    return os.path.exists(os.path.join(REAL_DUMP, 'system', 'explorer-3',
                                       'explorer-3.db'))


class FixtureTestCase(unittest.TestCase):
    """A test that gets a freshly built synthetic device tree."""

    caps_drop = ()
    with_card = True
    with_profiles = False
    with_cr3hist = False
    shelves = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='pbutil-test-')
        self.prefix = os.path.join(self.tmp, 'main')
        make_fixture.build_fixture(
            self.prefix,
            caps_drop=self.caps_drop,
            with_card=self.with_card,
            with_profiles=self.with_profiles,
            with_cr3hist=self.with_cr3hist,
            shelves=self.shelves,
        )
        self.card_prefix = os.path.join(self.tmp, 'card')
        self.db_path = os.path.join(self.prefix, 'system', 'explorer-3',
                                    'explorer-3.db')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
