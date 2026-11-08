"""Build the installable plugin zip.

    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e build.py

Produces ``PocketBook Utilities.zip`` next to this file. Tests, caches and
the git directory are left out.
"""

import ast
import os
import re
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


#: vX.Y.Z with an optional pre-release suffix: v1.2.0-rc1, v0.0.1-alpha.
TAG_RE = re.compile(r'^v?(?P<version>\d+\.\d+\.\d+)'
                    r'(?:[-+.](?P<suffix>[0-9A-Za-z.\-+]+))?$')


def parse_tag(tag):
    """``('0.0.1', 'alpha')`` for ``v0.0.1-alpha``.

    calibre plugin versions are tuples of integers, so a pre-release label
    cannot live in PLUGIN_VERSION. It belongs to the tag and the GitHub
    release only; the numeric part is what has to agree.
    """
    match = TAG_RE.match((tag or '').strip())
    if match is None:
        raise SystemExit(
            'tag %r is not of the form vX.Y.Z or vX.Y.Z-suffix '
            '(for example v1.0.0 or v0.0.1-alpha).' % tag)
    return match.group('version'), match.group('suffix')


def check_tag(tag):
    """Fail unless a release tag matches the version in the source."""
    numeric, suffix = parse_tag(tag)
    wanted = version_string()
    if numeric != wanted:
        raise SystemExit(
            'tag %r has version %s but PLUGIN_VERSION is %s. Update '
            'PLUGIN_VERSION in __init__.py to (%s), or tag v%s%s.'
            % (tag, numeric, wanted, ', '.join(numeric.split('.')),
               wanted, '-' + suffix if suffix else ''))
    label = ' (pre-release: %s)' % suffix if suffix else ''
    print('tag %s matches PLUGIN_VERSION %s%s' % (tag, wanted, label))


def is_prerelease(tag):
    """True when the tag carries a pre-release suffix."""
    return parse_tag(tag)[1] is not None


def set_version(tag):
    """Rewrite PLUGIN_VERSION in ``__init__.py`` to match *tag*.

    Keeps the source as the single place the version is stated, while
    making a release one command instead of a hand edit that is easy to
    forget. Only the PLUGIN_VERSION line is touched.
    """
    numeric, _suffix = parse_tag(tag)
    parts = numeric.split('.')
    replacement = 'PLUGIN_VERSION = (%s)\n' % ', '.join(parts)

    path = os.path.join(HERE, '__init__.py')
    with open(path, encoding='utf-8') as handle:
        lines = handle.readlines()

    for index, line in enumerate(lines):
        if line.startswith('PLUGIN_VERSION'):
            if line == replacement:
                print('PLUGIN_VERSION is already %s' % numeric)
                return
            lines[index] = replacement
            break
    else:
        raise SystemExit('__init__.py does not define PLUGIN_VERSION')

    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.writelines(lines)
    print('PLUGIN_VERSION set to %s; commit this before tagging %s'
          % (numeric, tag))


def main(argv):
    if len(argv) > 2 and argv[1] == '--check-tag':
        check_tag(argv[2])
        return
    if len(argv) > 2 and argv[1] == '--is-prerelease':
        print('true' if is_prerelease(argv[2]) else 'false')
        return
    if len(argv) > 2 and argv[1] == '--set-version':
        set_version(argv[2])
        return
    if len(argv) > 1 and argv[1] == '--version':
        print(version_string())
        return
    build()


if __name__ == '__main__':
    main(sys.argv)
