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
        """PLUGIN_ICON read from the source, so this needs no Qt."""
        import ast
        with open(os.path.join(ROOT, 'action.py'), encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if getattr(target, 'id', None) == 'PLUGIN_ICON':
                    return ast.literal_eval(node.value)
        self.fail('action.py no longer defines PLUGIN_ICON')

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
        self.assertFalse([n for n in packed if n.startswith('.')],
                         'tooling directories must not ship in the plugin')
        self.assertFalse([n for n in packed if 'github' in n],
                         'CI config must not ship inside the plugin')
        self.assertIn('LICENSE', packed, 'the licence should ship')


if __name__ == '__main__':
    unittest.main()


class TestVersioning(unittest.TestCase):
    """A release tag must not be able to disagree with the plugin."""

    def build_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'pbu_build_ver', os.path.join(ROOT, 'build.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_version_is_read_without_importing_calibre(self):
        build = self.build_module()
        version = build.plugin_version()
        self.assertIsInstance(version, tuple)
        self.assertTrue(all(isinstance(p, int) for p in version), version)
        self.assertEqual(len(version), 3)

    def test_matching_tag_is_accepted(self):
        build = self.build_module()
        build.check_tag('v' + build.version_string())
        build.check_tag(build.version_string())

    def test_mismatched_tag_is_refused(self):
        build = self.build_module()
        with self.assertRaises(SystemExit):
            build.check_tag('v99.98.97')


class TestWorkflows(unittest.TestCase):

    def workflow(self, name):
        path = os.path.join(ROOT, '.github', 'workflows', name)
        self.assertTrue(os.path.exists(path), '%s is missing' % name)
        with open(path, encoding='utf-8') as handle:
            return handle.read()

    def test_ci_runs_the_suite_both_ways(self):
        text = self.workflow('ci.yml')
        self.assertIn('python tests/run_tests.py', text)
        self.assertIn('calibre-debug -e tests/run_tests.py', text)
        self.assertIn('xvfb-run', text)

    def test_release_checks_the_tag_before_building(self):
        text = self.workflow('release.yml')
        check = text.index('--check-tag')
        build = text.index('python build.py\n')
        self.assertLess(check, build,
                        'the tag check must come before the build')

    def test_release_publishes_the_zip_the_build_produces(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'pbu_build_name', os.path.join(ROOT, 'build.py'))
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertIn(os.path.basename(build.OUTPUT),
                      self.workflow('release.yml'))
