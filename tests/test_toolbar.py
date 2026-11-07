"""The plugin must never end up loaded but unreachable.

calibre's default toolbar layout contains built-in actions only, so a plugin
installed from the command line -- or a toolbar reset to defaults -- leaves
the button missing and the plugin impossible to open.
"""

import unittest

from calibre_plugins.pocketbook_utilities import toolbar

NAME = toolbar.ACTION_NAME

DEFAULT_TOOLBAR = ('Add Books', 'Edit Metadata', None, 'Convert Books',
                   'View', None, 'Store', 'Donate', 'Fetch News', 'Help',
                   None, 'Preferences', 'Remove Books', 'Choose Library',
                   'Save To Disk', 'Connect Share', 'Tweak ePub')
DEFAULT_DEVICE = ('Add Books', 'Edit Metadata', None, 'Convert Books',
                  'View', 'Send To Device', None, None, 'Location Manager',
                  None, None, 'Fetch News', 'Save To Disk', 'Store',
                  'Connect Share', None, 'Remove Books', None, 'Help',
                  'Preferences')


class FakePrefs(dict):
    """Stands in for calibre's gprefs."""

    def __init__(self, **overrides):
        super().__init__({
            'action-layout-toolbar': DEFAULT_TOOLBAR,
            'action-layout-toolbar-device': DEFAULT_DEVICE,
            'action-layout-menubar': (),
            'action-layout-menubar-device': (),
            'action-layout-context-menu': (),
            'action-layout-context-menu-device': (),
        })
        self.update(overrides)


class TestReachability(unittest.TestCase):

    def test_calibre_defaults_contain_no_plugin(self):
        """The premise: resetting toolbars removes the button."""
        prefs = FakePrefs()
        self.assertFalse(toolbar.is_reachable(prefs))
        self.assertEqual(toolbar.placements(prefs), [])

    def test_a_button_on_the_toolbar_counts(self):
        prefs = FakePrefs(**{
            'action-layout-toolbar': DEFAULT_TOOLBAR + (NAME,)})
        self.assertTrue(toolbar.is_reachable(prefs))

    def test_a_user_who_moved_it_to_a_menu_is_not_nagged(self):
        prefs = FakePrefs(**{'action-layout-menubar': ('Something', NAME)})
        self.assertTrue(toolbar.is_reachable(prefs))

    def test_a_missing_layout_key_does_not_raise(self):
        prefs = FakePrefs()
        del prefs['action-layout-menubar']

        class Strict(dict):
            def __getitem__(self, key):
                raise KeyError(key)

        self.assertFalse(toolbar.is_reachable(Strict()))


class TestAdding(unittest.TestCase):

    def test_adds_to_both_toolbars(self):
        prefs = FakePrefs()
        changed = toolbar.add_to_toolbars(prefs)
        self.assertEqual(sorted(changed), ['action-layout-toolbar',
                                           'action-layout-toolbar-device'])
        self.assertIn(NAME, prefs['action-layout-toolbar'])
        self.assertIn(NAME, prefs['action-layout-toolbar-device'])
        self.assertTrue(toolbar.is_reachable(prefs))

    def test_existing_entries_are_preserved(self):
        prefs = FakePrefs()
        toolbar.add_to_toolbars(prefs)
        for item in DEFAULT_TOOLBAR:
            self.assertIn(item, prefs['action-layout-toolbar'])
        self.assertIn('KoboUtilities',
                      list(FakePrefs(**{
                          'action-layout-toolbar':
                              DEFAULT_TOOLBAR + ('KoboUtilities',)}
                      )['action-layout-toolbar']))

    def test_adding_twice_does_not_duplicate(self):
        prefs = FakePrefs()
        toolbar.add_to_toolbars(prefs)
        self.assertEqual(toolbar.add_to_toolbars(prefs), [])
        self.assertEqual(
            list(prefs['action-layout-toolbar']).count(NAME), 1)

    def test_layouts_stay_tuples(self):
        """calibre stores these as tuples; keep the shape it expects."""
        prefs = FakePrefs()
        toolbar.add_to_toolbars(prefs)
        self.assertIsInstance(prefs['action-layout-toolbar'], tuple)


if __name__ == '__main__':
    unittest.main()
