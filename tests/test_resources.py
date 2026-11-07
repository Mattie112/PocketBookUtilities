"""Resources the plugin asks for must exist where it asks for them.

The toolbar icon once silently failed to load because the code asked for
'images/icon.png' while the build packed it at 'resources/images/icon.png'.
The loader degraded to a text-only button rather than raising, so nothing
pointed at the cause.
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestIconIsWhereTheCodeExpects(unittest.TestCase):

    def icon_path(self):
        from calibre_plugins.pocketbook_utilities.action import PLUGIN_ICON
        return PLUGIN_ICON

    def test_icon_exists_on_disk_at_the_path_the_code_uses(self):
        rel = self.icon_path()
        full = os.path.join(ROOT, *rel.split('/'))
        self.assertTrue(os.path.exists(full),
                        '%s does not exist (looked at %s)' % (rel, full))

    def test_icon_is_a_real_png(self):
        full = os.path.join(ROOT, *self.icon_path().split('/'))
        with open(full, 'rb') as handle:
            header = handle.read(8)
        self.assertEqual(header, b'\x89PNG\r\n\x1a\n')

    def test_build_packs_the_icon_at_that_exact_path(self):
        """The decisive check: what build.py produces, not what is on disk."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'pbu_build', os.path.join(ROOT, 'build.py'))
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)

        packed = {arcname for _full, arcname in build.files_to_pack()}
        self.assertIn(self.icon_path(), packed,
                      'build.py does not pack the icon where action.py '
                      'looks for it; packed resources: %r'
                      % sorted(n for n in packed if 'resources' in n))

    def test_build_includes_the_marker_and_entry_point(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'pbu_build2', os.path.join(ROOT, 'build.py'))
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        packed = {arcname for _full, arcname in build.files_to_pack()}

        self.assertIn('plugin-import-name-pocketbook_utilities.txt', packed)
        self.assertIn('__init__.py', packed)
        self.assertNotIn('build.py', packed)
        self.assertFalse([n for n in packed if n.startswith('tests/')],
                         'tests must not ship inside the plugin')


if __name__ == '__main__':
    unittest.main()
