"""Build the installable plugin zip.

    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e build.py

Produces ``PocketBook Utilities.zip`` next to this file. Tests, caches and
the git directory are left out.
"""

import ast
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, 'PocketBook Utilities.zip')

EXCLUDE_DIRS = {'tests', '__pycache__'}
EXCLUDE_FILES = {'build.py', 'PocketBook Utilities.zip'}
EXCLUDE_SUFFIXES = ('.pyc', '.pyo', '.zip', '.orig', '.rej')


def skip_dir(name):
    """Anything dot-prefixed is tooling, not plugin content.

    Covers .git, .github, .idea, .vscode, .venv and whatever comes next,
    rather than needing the list extended each time.
    """
    return name.startswith('.') or name in EXCLUDE_DIRS


def files_to_pack():
    for dirpath, dirnames, filenames in os.walk(HERE):
        dirnames[:] = sorted(d for d in dirnames if not skip_dir(d))
        for name in sorted(filenames):
            if name in EXCLUDE_FILES or name.startswith('.'):
                continue
            if name.endswith(EXCLUDE_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            yield full, os.path.relpath(full, HERE).replace('\\', '/')


def build():
    if os.path.exists(OUTPUT):
        os.remove(OUTPUT)
    packed = []
    with zipfile.ZipFile(OUTPUT, 'w', zipfile.ZIP_DEFLATED) as archive:
        for full, arcname in files_to_pack():
            archive.write(full, arcname)
            packed.append(arcname)

    marker = 'plugin-import-name-pocketbook_utilities.txt'
    if marker not in packed:
        raise SystemExit('the import-name marker is missing from the zip')
    if '__init__.py' not in packed:
        raise SystemExit('__init__.py is missing from the zip')

    print('%s  (%d files, %.1f KB)'
          % (OUTPUT, len(packed), os.path.getsize(OUTPUT) / 1024.0))
    for name in packed:
        print('   ', name)
    return OUTPUT


def plugin_version():
    """``PLUGIN_VERSION`` read from the source.

    Parsed rather than imported, because ``__init__.py`` imports calibre and
    this has to work on a bare CI runner.
    """
    with open(os.path.join(HERE, '__init__.py'), encoding='utf-8') as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if getattr(target, 'id', None) == 'PLUGIN_VERSION':
                return tuple(ast.literal_eval(node.value))
    raise SystemExit('__init__.py does not define PLUGIN_VERSION')


def version_string():
    return '.'.join(str(part) for part in plugin_version())


def check_tag(tag):
    """Fail unless a release tag matches the version in the source."""
    wanted = version_string()
    cleaned = tag.strip()
    if cleaned.startswith('v'):
        cleaned = cleaned[1:]
    if cleaned != wanted:
        raise SystemExit(
            'tag %r does not match PLUGIN_VERSION %s. Update '
            'PLUGIN_VERSION in __init__.py, or tag v%s.'
            % (tag, wanted, wanted))
    print('tag %s matches PLUGIN_VERSION %s' % (tag, wanted))


def main(argv):
    if len(argv) > 2 and argv[1] == '--check-tag':
        check_tag(argv[2])
        return
    if len(argv) > 1 and argv[1] == '--version':
        print(version_string())
        return
    build()


if __name__ == '__main__':
    main(sys.argv)
