"""Build a synthetic PocketBook storage tree for the tests.

The database is created from ``schema_v39.sql``, which was dumped verbatim
from a real InkPad 3 Color (schema ``version`` = 39) -- including the
triggers, which matter: writing ``books_settings.completed`` makes the device
overwrite ``completed_ts``, and updating ``books_impl.title``/``author``
rewrites the FTS ``search`` index.
"""

import json
import os
import shutil
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_SQL = os.path.join(HERE, 'schema_v39.sql')

DEVICE_ROOT = '/mnt/ext1'
CARD_ROOT = '/mnt/ext2'

NOW = 1790000000


def _book(book_id, title, author, firstauthor, folder, filename, **kw):
    row = {
        'id': book_id,
        'title': title,
        'author': author,
        'firstauthor': firstauthor,
        'first_author_letter': (firstauthor or ' ')[0],
        'series': kw.get('series'),
        'numinseries': kw.get('numinseries'),
        'sort_title': kw.get('sort_title', title),
        'folder': folder,
        'filename': filename,
        'size': kw.get('size', 1024),
        'storage': kw.get('storage', 1),
        'on_disk': kw.get('on_disk', True),
        'in_files': kw.get('in_files', True),
        'settings': kw.get('settings'),
    }
    return row


def _settings(completed=0, completed_ts=None, favorite=0, favorite_ts=None,
              position=None, position_ts=None, cpage=None, npage=None,
              opentime=None):
    return {
        'completed': completed,
        'completed_ts': completed_ts if completed_ts is not None else NOW - 1000,
        'favorite': favorite,
        'favorite_ts': favorite_ts if favorite_ts is not None else NOW - 5000,
        'position': position,
        'position_ts': position_ts if position_ts is not None else NOW - 900,
        'cpage': cpage,
        'npage': npage,
        'opentime': opentime if opentime is not None else NOW - 1100,
    }


def default_books():
    """A small but deliberately awkward library."""
    cfi = 'pbr:/webkit?##epubcfi(/6/62!/4/2/4/1:1)'
    return [
        _book(1, 'Project Hail Mary', 'Weir, Andy', 'Weir, Andy',
              'Books/Weir, Andy', 'Project Hail Mary - Andy Weir.epub',
              size=2048,
              settings=_settings(completed=1, position=cfi, cpage=380,
                                 npage=380)),
        # Apostrophes: the exact data that aborted the previous plugin.
        _book(2, "Harry Potter and the Philosopher's Stone", "Rowling, J.K.",
              "Rowling, J.K.", "Books/Rowling, J.K_",
              "Harry Potter and the Philosopher's Stone - J.K. Rowling.epub",
              size=3072,
              settings=_settings(completed=0, position=cfi, cpage=144,
                                 npage=387)),
        _book(3, "O'Brien's Last Stand", "O'Brien, Tim", "O'Brien, Tim",
              "Books/O'Brien, Tim", "O'Brien's Last Stand - Tim O'Brien.epub",
              size=4096, settings=_settings(favorite=1)),
        # SQL metacharacters in the title.
        _book(4, 'Title; DROP TABLE books_impl;--', 'Bobby, Tables',
              'Bobby, Tables', 'Books/Bobby, Tables',
              'Title drop table - Bobby Tables.epub',
              size=512, settings=_settings()),
        _book(5, 'Ünïcödé Trävels', 'Zoë, Ångström', 'Zoë, Ångström',
              'Books/Zoë, Ångström', 'Ünïcödé Trävels - Zoë Ångström.epub',
              size=777, settings=_settings(completed=1)),
        # Author sorting broken exactly as on the real device.
        _book(6, 'Net Force Explorers ENG - 01 - Virtual Vandals',
              'Clancy, Tom - Net Force Explorers 01',
              '01, Clancy, Tom - Net Force Explorers',
              'Books/01, Clancy, Tom - Net Force Explorers',
              'Virtual Vandals - Clancy.epub',
              series='Net Force Explorers ENG', numinseries=1,
              sort_title='Virtual Vandals', size=1500,
              settings=_settings()),
        _book(7, 'Missing Author Book', '', '', 'Books/Unknown',
              'Missing Author Book.epub', size=900, settings=_settings()),
        # Present in the database but no longer on disk.
        _book(8, 'Ghost In The Database', 'Nobody, A', 'Nobody, A',
              'Books/Nobody, A', 'Ghost In The Database.epub',
              size=100, on_disk=False, settings=_settings(completed=1)),
        # On disk but never indexed by the reader.
        _book(9, 'Not Yet Indexed', 'Fresh, Copy', 'Fresh, Copy',
              'Books/Fresh, Copy', 'Not Yet Indexed.epub',
              size=200, in_files=False, settings=None),
        # On the SD card.
        _book(10, 'Card Resident', 'Carder, Sam', 'Carder, Sam',
              'Books/Carder, Sam', 'Card Resident.epub',
              size=640, storage=2, settings=_settings(cpage=10, npage=100)),
        # No books_settings row at all.
        _book(11, 'Never Opened', 'Pristine, P', 'Pristine, P',
              'Books/Pristine, P', 'Never Opened.epub',
              size=321, settings=None),
        _book(12, 'Zero Pages', 'Edge, Case', 'Edge, Case',
              'Books/Edge, Case', 'Zero Pages.epub',
              size=50, settings=_settings(cpage=0, npage=0)),
    ]


def _schema_statements():
    with open(SCHEMA_SQL, 'r', encoding='utf-8') as handle:
        return handle.read()


def _apply_caps_drop(conn, caps_drop):
    """Remove tables or columns so degraded-schema paths get exercised.

    Accepts ``'bookshelfs'`` (a whole table) or ``'books_settings.favorite'``
    (a single column).
    """
    for item in caps_drop:
        if '.' in item:
            table, column = item.split('.', 1)
            conn.execute('ALTER TABLE "%s" DROP COLUMN "%s"' % (table, column))
        else:
            for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall():
                if item in (conn.execute(
                        "SELECT sql FROM sqlite_master WHERE name = ?",
                        (name,)).fetchone()[0] or ''):
                    conn.execute('DROP TRIGGER IF EXISTS "%s"' % name)
            conn.execute('DROP TABLE IF EXISTS "%s"' % item)
    conn.commit()


def build_fixture(dest, books=None, caps_drop=(), with_card=True,
                  with_profiles=False, with_cr3hist=False, shelves=None):
    """Create a device tree at *dest* and return its path.

    ``with_profiles`` adds ``system/profiles/.current.lnk`` (with the
    trailing newline the real file has). The reference device has none, so
    the default is off.
    """
    if os.path.exists(dest):
        shutil.rmtree(dest)
    books = list(default_books() if books is None else books)

    os.makedirs(dest)
    db_dir = os.path.join(dest, 'system', 'explorer-3')
    os.makedirs(db_dir)
    db_path = os.path.join(db_dir, 'explorer-3.db')

    card_prefix = os.path.join(dest, '..', 'card') if with_card else None
    if with_card:
        card_prefix = os.path.abspath(card_prefix)
        if os.path.exists(card_prefix):
            shutil.rmtree(card_prefix)
        os.makedirs(card_prefix)

    # --- files on disk -------------------------------------------------
    for book in books:
        if not book['on_disk']:
            continue
        root = card_prefix if book['storage'] == 2 else dest
        if root is None:
            continue
        folder = os.path.join(root, *book['folder'].split('/'))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, book['filename']), 'wb') as handle:
            handle.write(b'x' * book['size'])

    with open(os.path.join(dest, 'driveinfo.calibre'), 'w',
              encoding='utf-8') as handle:
        json.dump({
            'device_store_uuid': 'fixture-0000-1111-2222',
            'device_name': 'PocketBook Fixture',
            'location_code': 'main',
            'prefix': dest,
        }, handle)

    if with_profiles:
        prof_dir = os.path.join(dest, 'system', 'profiles')
        os.makedirs(prof_dir, exist_ok=True)
        with open(os.path.join(prof_dir, '.current.lnk'), 'wb') as handle:
            handle.write((DEVICE_ROOT + '/system/profiles/Matthijs\n')
                         .encode('utf-8'))
        if with_cr3hist:
            cr3 = os.path.join(prof_dir, 'Matthijs', 'state', 'cr3')
            os.makedirs(cr3, exist_ok=True)
            with open(os.path.join(cr3, '.cr3hist'), 'w',
                      encoding='utf-8') as handle:
                handle.write('<?xml version="1.0" encoding="utf-8"?>\n'
                             '<FictionBookMarks/>\n')

    # --- the database --------------------------------------------------
    conn = sqlite3.connect(db_path)
    conn.executescript(_schema_statements())
    conn.execute('INSERT INTO version (id) VALUES (39)')
    conn.execute('INSERT INTO storages (id, type, name, sync_account_id) '
                 'VALUES (1, 1, ?, NULL)', ('InternalStorage',))
    if with_card:
        conn.execute('INSERT INTO storages (id, type, name, sync_account_id) '
                     'VALUES (2, 2, ?, NULL)', ('SDCard1',))
    conn.execute('INSERT INTO profiles (id, name) VALUES (1, ?)',
                 ('Matthijs' if with_profiles else 'default',))

    folder_ids = {}
    next_folder = 1
    # Non-book folders, as the real device has (manuals, language packs).
    for extra in ('English', 'Color.Guide'):
        conn.execute('INSERT INTO folders (id, storageid, name) '
                     'VALUES (?, 1, ?)',
                     (next_folder, DEVICE_ROOT + '/' + extra))
        next_folder += 1

    for book in books:
        root = CARD_ROOT if book['storage'] == 2 else DEVICE_ROOT
        key = (book['storage'], book['folder'])
        if key not in folder_ids:
            folder_ids[key] = next_folder
            conn.execute(
                'INSERT INTO folders (id, storageid, name) VALUES (?, ?, ?)',
                (next_folder, book['storage'], root + '/' + book['folder']))
            next_folder += 1

        conn.execute(
            'INSERT INTO books_impl (id, title, first_title_letter, author, '
            'firstauthor, first_author_letter, series, numinseries, size, '
            'ts_added, sort_title) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (book['id'], book['title'], (book['title'] or ' ')[0],
             book['author'], book['firstauthor'], book['first_author_letter'],
             book['series'], book['numinseries'], book['size'], NOW - 100000,
             book['sort_title']))

        if book['in_files']:
            conn.execute(
                'INSERT INTO files (id, storageid, folder_id, book_id, '
                'filename, size, modification_time, ext) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (book['id'], book['storage'], folder_ids[key], book['id'],
                 book['filename'], book['size'], NOW - 200000,
                 book['filename'].rsplit('.', 1)[-1]))

        settings = book['settings']
        if settings is not None:
            conn.execute(
                'INSERT INTO books_settings (bookid, profileid, position, '
                'position_ts, cpage, npage, opentime, completed, '
                'completed_ts, favorite, favorite_ts) '
                'VALUES (?,1,?,?,?,?,?,?,?,?,?)',
                (book['id'], settings['position'], settings['position_ts'],
                 settings['cpage'], settings['npage'], settings['opentime'],
                 settings['completed'], settings['completed_ts'],
                 settings['favorite'], settings['favorite_ts']))
            # The insert trigger stamps completed_ts with "now"; put the
            # intended value back so tests have deterministic timestamps.
            conn.execute('UPDATE books_settings SET completed_ts = ? '
                         'WHERE bookid = ? AND profileid = 1',
                         (settings['completed_ts'], book['id']))

    for name, is_deleted in (shelves or []):
        conn.execute('INSERT INTO bookshelfs (name, is_deleted, ts, uuid) '
                     'VALUES (?,?,?,?)',
                     (name, is_deleted, NOW, 'f' * 32))

    conn.commit()
    if caps_drop:
        _apply_caps_drop(conn, caps_drop)
    conn.close()

    return dest


def main():
    import tempfile
    dest = os.path.join(tempfile.gettempdir(), 'pbutil-fixture', 'main')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    build_fixture(dest)
    print('fixture at', dest)


if __name__ == '__main__':
    main()
