"""Test runner.

Run with calibre's bundled Python::

    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e tests\\run_tests.py

Note that ``calibre-debug -c`` executes with split globals/locals, so helper
functions defined inline cannot see module-level names. Always use ``-e``.

The plugin is normally imported as ``calibre_plugins.pocketbook_utilities``.
We register that name as a namespace pointing at the development directory
*without* executing the plugin's own ``__init__.py``, so the headless
``device`` and ``sync`` packages can be imported with no calibre GUI and no
Qt.
"""

import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGE = 'calibre_plugins.pocketbook_utilities'


def install_package_alias():
    parent = sys.modules.get('calibre_plugins')
    if parent is None:
        parent = types.ModuleType('calibre_plugins')
        parent.__path__ = []
        sys.modules['calibre_plugins'] = parent

    module = types.ModuleType(PACKAGE)
    module.__path__ = [ROOT]
    module.__package__ = PACKAGE
    sys.modules[PACKAGE] = module
    setattr(parent, 'pocketbook_utilities', module)

    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    return module


def main():
    install_package_alias()
    loader = unittest.TestLoader()
    suite = loader.discover(HERE, pattern='test_*.py', top_level_dir=HERE)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
