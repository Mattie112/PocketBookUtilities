"""Shared test scaffolding.

Most of the suite needs nothing but the standard library: everything under
``device/`` and ``sync/`` is deliberately free of calibre and Qt imports, so
it runs under any Python. The tests that do need calibre say so, and skip
themselves when it is not importable. That lets CI run the bulk of the suite
cheaply and the rest only where calibre is installed.
"""

import os
import shutil
import tempfile
import unittest

import make_fixture

REAL_DUMP = r'C:\temp\pocketbook'


def _can_import(name):
    import importlib
    try:
        importlib.import_module(name)
    except Exception:
        return False
    return True


#: calibre's library database, needed to create real custom columns.
HAVE_CALIBRE_DB = _can_import('calibre.db.legacy')

#: Qt plus calibre's GUI layer, needed to build dialogs.
HAVE_CALIBRE_GUI = HAVE_CALIBRE_DB and _can_import('qt.core')


def needs_calibre_db():
    """Raise at module level to skip a whole module."""
    if not HAVE_CALIBRE_DB:
        raise unittest.SkipTest('needs calibre (run with calibre-debug -e)')


def needs_calibre_gui():
    if not HAVE_CALIBRE_GUI:
        raise unittest.SkipTest('needs calibre with Qt '
                                '(run with calibre-debug -e)')


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
