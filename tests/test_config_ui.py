"""Build the configuration dialog for real.

Every tab is constructed, with and without a connected device. Nothing here
asserts much about appearance; the point is that the widgets are built at
all. A raw ``0x1`` passed where Qt6 wants an enum crashed the whole dialog
and no test noticed, because nothing ever constructed it.
"""

import os
import shutil
import tempfile
import unittest

from base import needs_calibre_gui

needs_calibre_gui()

_app = None


def setUpModule():
    global _app
    from calibre.gui2 import Application
    from qt.core import QApplication
    _app = QApplication.instance() or Application([])


class FakeCaps:
    db_version = 39
    fingerprint = 'abc123'
    tables = frozenset({'books_impl', 'books_settings', 'bookshelfs'})
    cols = {}

    def describe(self):
        return [('Reading status (completed)', True),
                ('Favourites', True),
                ('Collections (shelves)', False)]

    def __getattr__(self, name):
        if name.startswith('has_'):
            return True
        raise AttributeError(name)


class FakeDevice:
    serial = 'fixture-0000'
    device_name = 'PocketBook Fixture'
    driver_name = 'FakeDriver'
    main_prefix = 'F:\\'
    card_prefix = None
    db_path = r'F:\system\explorer-3\explorer-3.db'
    explorer_version = 3
    profile_id = 1
    profile_name = 'default'
    cr3hist_path = None
    caps = FakeCaps()

    def summary(self):
        return [('Device', self.device_name),
                ('Serial / storage id', self.serial),
                ('Database', self.db_path),
                ('Schema version', str(self.caps.db_version))]


class ConfigTestCase(unittest.TestCase):

    def setUp(self):
        from calibre.db.legacy import LibraryDatabase
        from qt.core import QWidget

        self.path = tempfile.mkdtemp(prefix='pbutil-cfg-')
        self.db = LibraryDatabase(self.path)

        class Model:
            pass

        class View:
            pass

        model, view = Model(), View()
        model.db = self.db
        view.model = lambda: model

        gui = QWidget()
        gui.library_view = view
        gui.current_db = self.db
        self.gui = gui

        plugin_action = type('A', (), {})()
        plugin_action.gui = gui
        plugin_action.device = None
        plugin_action.settings = lambda: self.settings()
        plugin_action.rebuild_menus = lambda: None
        plugin_action.backup_now = lambda: None
        plugin_action.restore_backup = lambda: None
        plugin_action.check_database = lambda: None
        plugin_action.verify_write = lambda: None
        plugin_action._create_columns = lambda: 'created'
        self.plugin_action = plugin_action

    def settings(self):
        from calibre_plugins.pocketbook_utilities import config
        return dict(config.DEVICE_DEFAULTS)

    def tearDown(self):
        try:
            self.db.close()
        except Exception as err:        # pragma: no cover - cleanup only
            print('could not close scratch library: %s' % err)
        shutil.rmtree(self.path, ignore_errors=True)

    def build(self, device=None):
        from calibre_plugins.pocketbook_utilities.config_ui import (
            ConfigWidget)
        self.plugin_action.device = device
        widget = ConfigWidget(self.plugin_action)
        self.addCleanup(widget.deleteLater)
        return widget


class TestConstruction(ConfigTestCase):

    def test_builds_without_a_device(self):
        widget = self.build(None)
        self.assertEqual(widget.tabs.count(), 6)

    def test_builds_with_a_device(self):
        """The crash was here: the Device tab is only built with a device."""
        widget = self.build(FakeDevice())
        self.assertEqual(widget.tabs.count(), 6)

    def test_every_tab_has_a_widget(self):
        widget = self.build(FakeDevice())
        for index in range(widget.tabs.count()):
            self.assertIsNotNone(widget.tabs.widget(index),
                                 'tab %d is empty' % index)
            self.assertTrue(widget.tabs.tabText(index))

    def test_device_summary_labels_are_selectable(self):
        from qt.core import Qt
        widget = self.build(FakeDevice())
        labels = widget.findChildren(type(widget.setup_result))
        selectable = [l for l in labels
                      if l.textInteractionFlags()
                      & Qt.TextInteractionFlag.TextSelectableByMouse]
        self.assertTrue(selectable,
                        'device details should be selectable for copying')


class TestSaving(ConfigTestCase):

    def test_saving_without_a_device_does_not_raise(self):
        widget = self.build(None)
        widget.save_settings()

    def test_field_and_column_controls_exist_for_every_field(self):
        from calibre_plugins.pocketbook_utilities import columns
        from calibre_plugins.pocketbook_utilities.sync.fields import FIELDS

        widget = self.build(FakeDevice())
        for spec in FIELDS:
            self.assertIn(spec.key, widget.field_boxes, spec.key)
        for spec in columns.COLUMN_SPECS:
            self.assertIn(spec.field, widget.column_combos, spec.field)


if __name__ == '__main__':
    unittest.main()
