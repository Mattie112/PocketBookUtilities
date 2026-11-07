"""Build the installable plugin zip.

    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e build.py

Produces ``PocketBook Utilities.zip`` next to this file. Tests, caches and
the git directory are left out.
"""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, 'PocketBook Utilities.zip')

EXCLUDE_DIRS = {'tests', '.git', '__pycache__', '.idea', '.vscode'}
EXCLUDE_FILES = {'build.py', 'PocketBook Utilities.zip'}
EXCLUDE_SUFFIXES = ('.pyc', '.pyo', '.zip', '.orig', '.rej')


def files_to_pack():
    for dirpath, dirnames, filenames in os.walk(HERE):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
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


if __name__ == '__main__':
    build()
