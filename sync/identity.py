"""Map calibre books to rows in the PocketBook database.

The plugin this replaces ran two queries per book inside a loop over the
whole library, against a storage prefix guessed from an arbitrary ``folders``
row. Here everything is prefetched with a handful of bulk queries and matched
in memory, and the storage roots are established by checking which candidate
root actually yields directories that exist on disk.

Every value reaches SQLite as a ``?`` parameter. Table names are literals.
"""

import os
import re
from collections import defaultdict
from dataclasses import replace

from ..common.compat import debug
from .model import BookLink, DeviceFileRow, DeviceState, MatchConfidence

#: Roots the firmware writes into ``folders.name``.
CANDIDATE_ROOTS = ('/mnt/ext1', '/mnt/ext2', '/mnt/ext3', '/mnt/secsd')

FILES_SQL = """
SELECT fi.book_id   AS book_id,
       fi.storageid AS storage_id,
       fo.name      AS folder_name,
       fi.filename  AS filename,
       {size} AS size,
       fi.ext       AS ext,
       bi.title     AS title,
       bi.author    AS author,
       bi.firstauthor AS firstauthor,
       {first_author_letter} AS first_author_letter,
       {sort_title} AS sort_title,
       {series} AS series,
       {numinseries} AS numinseries
FROM files fi
JOIN folders fo ON fo.id = fi.folder_id
JOIN books_impl bi ON bi.id = fi.book_id
"""

#: ``books_impl`` columns that not every firmware has. Selecting one that is
#: missing fails the whole query, and with it every sync, so each is read
#: only when the schema has it and comes back as NULL otherwise.
OPTIONAL_BOOK_COLUMNS = ('first_author_letter', 'sort_title', 'series',
                         'numinseries')


def files_sql(caps):
    """The file query, limited to the columns this firmware has."""
    book_columns = caps.cols.get('books_impl', ())
    parts = {name: ('bi.%s' % name if name in book_columns else 'NULL')
             for name in OPTIONAL_BOOK_COLUMNS}
    parts['size'] = 'fi.size' if caps.has_file_size else 'NULL'
    return FILES_SQL.format(**parts)

SETTINGS_SQL = """
SELECT bookid, completed, completed_ts, favorite, favorite_ts,
       position, position_ts, cpage, npage, opentime
FROM books_settings
WHERE profileid = ?
"""

SHELVES_SQL = """
SELECT bs.id AS shelf_id, bs.name AS shelf_name, {deleted} AS shelf_deleted
FROM bookshelfs bs
"""

MEMBERSHIP_SQL = """
SELECT bookshelfid, bookid, {deleted} AS is_deleted
FROM bookshelfs_books
"""


def shelves_sql(caps):
    """The shelf query; a firmware without soft deletes has only live ones."""
    return SHELVES_SQL.format(
        deleted='bs.is_deleted' if caps.has_shelf_soft_delete else '0')


def membership_sql(caps):
    return MEMBERSHIP_SQL.format(
        deleted='is_deleted' if caps.has_shelf_is_deleted else '0')

_PUNCT = re.compile(r'[^\w\s]', re.UNICODE)
_SPACE = re.compile(r'\s+', re.UNICODE)


def normalise_path(path):
    """Lower-cased, slash-normalised relative path used as a match key."""
    if not path:
        return ''
    return path.replace('\\', '/').strip('/').casefold()


def normalise_text(value):
    if not value:
        return ''
    text = _PUNCT.sub(' ', str(value))
    return _SPACE.sub(' ', text).strip().casefold()


def author_key(authors):
    """A comparable key for an author string from either side."""
    if not authors:
        return ''
    # "Clancy, Tom & Greaney, Mark" and "Tom Clancy" should both reduce to
    # something containing the surname, so sort the words.
    words = normalise_text(authors).split()
    return ' '.join(sorted(words))


def _split_root(folder_name):
    """``/mnt/ext1/Books/X`` -> ``('/mnt/ext1', 'Books/X')`` candidates."""
    parts = folder_name.replace('\\', '/').strip('/').split('/')
    out = []
    for depth in (2, 1, 3):
        if len(parts) >= depth:
            out.append(('/' + '/'.join(parts[:depth]),
                        '/'.join(parts[depth:])))
    return out


def _storage_rank(option):
    """Prefer more verified folders, then known and shorter firmware roots."""
    hits, root, _location, _prefix = option
    return -hits, root not in CANDIDATE_ROOTS, len(root), root


def resolve_storage_roots(conn, prefixes):
    """Work out the device root string for each ``storages.id``.

    *prefixes* maps a location name (``'main'``/``'card'``) to the local
    filesystem path. For every storage we score each candidate root by how
    many of its folders actually exist under each prefix, and keep the best
    pairing. A storage that scores nothing is left unmapped and its books are
    reported rather than silently mismatched.
    """
    rows = conn.execute(
        'SELECT storageid, name FROM folders ORDER BY storageid, name'
    ).fetchall()
    by_storage = {}
    for row in rows:
        by_storage.setdefault(row['storageid'], []).append(row['name'])

    resolved = {}
    for storage_id, folders in by_storage.items():
        candidates = set(CANDIDATE_ROOTS)
        folders = [name.replace('\\', '/') for name in folders if name]
        for name in folders[:200]:
            for root, _rest in _split_root(name):
                candidates.add(root)

        options = []
        for location, prefix in prefixes.items():
            if not prefix or not os.path.isdir(prefix):
                continue
            for root in candidates:
                if root == '/mnt/ext1' and location != 'main':
                    continue
                if root in ('/mnt/ext2', '/mnt/secsd') and location == 'main':
                    continue
                hits = 0
                for name in folders:
                    known = next((candidate for candidate in CANDIDATE_ROOTS
                                  if name == candidate
                                  or name.startswith(candidate + '/')), None)
                    if known and root != known:
                        continue
                    if name != root and not name.startswith(root + '/'):
                        continue
                    rest = name[len(root):].strip('/')
                    target = os.path.join(prefix, *rest.split('/')) if rest \
                        else prefix
                    if os.path.isdir(target):
                        hits += 1
                if hits:
                    options.append((hits, root, location, prefix))
        if options:
            best = min(options, key=_storage_rank)
            tied = [option for option in options
                    if option[:2] == best[:2]]
            if len(tied) != 1:
                debug('storage %s matches multiple local volumes' % storage_id)
                continue
            resolved[storage_id] = {
                'root': best[1], 'location': best[2], 'prefix': best[3],
                'hits': best[0], 'folders': len(folders),
            }
            debug('storage %s -> %s (%s, %d/%d folders verified)'
                  % (storage_id, best[1], best[2], best[0], len(folders)))
        else:
            debug('storage %s could not be mapped to a local path' % storage_id)
    return resolved


class DeviceIndex:
    """Everything we read from the device, indexed for in-memory matching."""

    def __init__(self, rows, roots):
        self.rows = rows
        self.roots = roots
        self.by_book_id = {r.book_id: r for r in rows}
        self.by_relpath = {}
        self.by_basename = {}
        self.by_titleauthor = {}
        for row in rows:
            self.by_relpath.setdefault(normalise_path(row.relpath), []).append(row)
            self.by_basename.setdefault(row.filename.casefold(), []).append(row)
            key = (normalise_text(row.title), author_key(row.author))
            if any(key):
                self.by_titleauthor.setdefault(key, []).append(row)

    def __len__(self):
        return len(self.rows)


def load_device_index(conn, caps, prefixes):
    """Read ``files``/``folders``/``books_impl`` into a :class:`DeviceIndex`."""
    roots = resolve_storage_roots(conn, prefixes)
    rows = []
    for row in conn.execute(files_sql(caps)):
        folder = (row['folder_name'] or '').replace('\\', '/')
        info = roots.get(row['storage_id'])
        if info is not None and (folder == info['root']
                                 or folder.startswith(info['root'] + '/')):
            rest = folder[len(info['root']):].strip('/')
        else:
            # Unmapped storage: keep the path as-is so the book still shows
            # up in reports, but it will not match by path.
            rest = folder.strip('/')
        relpath = (rest + '/' + row['filename']) if rest else row['filename']
        rows.append(DeviceFileRow(
            book_id=row['book_id'],
            storage_id=row['storage_id'],
            folder_name=folder,
            filename=row['filename'] or '',
            size=row['size'],
            ext=row['ext'],
            title=row['title'],
            author=row['author'],
            firstauthor=row['firstauthor'],
            first_author_letter=row['first_author_letter'],
            sort_title=row['sort_title'],
            series=row['series'],
            numinseries=row['numinseries'],
            relpath=relpath,
            device_path=folder + '/' + (row['filename'] or ''),
        ))
    debug('loaded %d device file rows' % len(rows))
    return DeviceIndex(rows, roots)


def book_file_identities(conn, caps, book_ids):
    """Raw file identities for reviewed book IDs, without filesystem scans."""
    columns = caps.cols.get('files', ())
    optional = [f'fi.{name}' for name in
                ('size', 'fast_hash', 'modification_time') if name in columns]
    select = ', '.join(['fi.book_id', 'fi.id', 'fi.storageid',
                        'fo.name', 'fi.filename'] + optional)
    result = defaultdict(set)
    ids = sorted(book_ids)
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        placeholders = ','.join('?' for _ in batch)
        rows = conn.execute(
            f'SELECT {select} FROM files fi '
            'JOIN folders fo ON fo.id = fi.folder_id '
            'JOIN books_impl bi ON bi.id = fi.book_id '
            f'WHERE fi.book_id IN ({placeholders})', batch)
        for row in rows:
            result[row[0]].add(tuple(row[1:]))
    return result


def load_reading_state(conn, caps, profile_id):
    """``{device_book_id: DeviceState}`` for the active profile."""
    if not caps.has_reading_state:
        return {}
    columns = caps.cols.get('books_settings', frozenset())
    select = ['bookid', 'completed', 'completed_ts']
    for optional in ('favorite', 'favorite_ts', 'position', 'position_ts',
                     'cpage', 'npage', 'opentime'):
        if optional in columns:
            select.append(optional)
    sql = 'SELECT %s FROM books_settings' % ', '.join(select)
    params = ()
    if caps.has_profiles:
        sql += ' WHERE profileid = ?'
        params = (profile_id,)

    states = {}
    for row in conn.execute(sql, params):
        data = {key: row[key] for key in select}
        book_id = data.pop('bookid')
        states[book_id] = DeviceState(book_id=book_id, exists=True, **data)
    debug('loaded reading state for %d books' % len(states))
    return states


def load_shelves(conn, caps):
    """``(shelves_by_id, memberships)``.

    ``memberships`` maps a device book id to ``{shelf_id: is_deleted}`` and
    deliberately includes soft-deleted rows: adding a book back to a shelf
    means clearing the flag, not inserting a duplicate.
    """
    if not caps.has_shelves:
        return {}, {}
    shelves = {}
    for row in conn.execute(shelves_sql(caps)):
        shelves[row['shelf_id']] = {
            'id': row['shelf_id'],
            'name': row['shelf_name'] or '',
            'is_deleted': row['shelf_deleted'] or 0,
        }
    memberships = {}
    for row in conn.execute(membership_sql(caps)):
        memberships.setdefault(row['bookid'], {})[row['bookshelfid']] = \
            row['is_deleted'] or 0
    return shelves, memberships


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def _same_size(book, row):
    size = book.get('size')
    return size is None or row.size is None or size == row.size


def _match_candidates(index, book, confidence):
    location = book.get('location')

    def on_volume(row):
        return (location is None
                or index.roots.get(row.storage_id, {}).get('location') == location)

    lpath = normalise_path(book.get('lpath'))
    if confidence is MatchConfidence.EXACT_PATH:
        rows = index.by_relpath.get(lpath, ())
        return [row for row in rows if on_volume(row) and _same_size(book, row)]
    if confidence in (MatchConfidence.BASENAME_SIZE, MatchConfidence.BASENAME):
        rows = index.by_basename.get(lpath.rsplit('/', 1)[-1], ())
        return [row for row in rows if on_volume(row) and _same_size(book, row)
                and (confidence is MatchConfidence.BASENAME
                     or book.get('size') is not None and row.size == book['size'])]
    key = (normalise_text(book.get('title')), author_key(book.get('authors')))
    return [row for row in index.by_titleauthor.get(key, ()) if on_volume(row)]


def build_links(index, calibre_books):
    """Pair books uniquely, considering every calibre copy of each book."""
    books_by_id = defaultdict(list)
    for book in calibre_books:
        books_by_id[book['id']].append(book)

    links = []
    pending = dict(books_by_id)
    claimed = set()
    contested = set()
    for confidence in reversed(MatchConfidence):
        matches = defaultdict(dict)
        owners = defaultdict(set)
        for calibre_id, books in pending.items():
            for book in books:
                for row in _match_candidates(index, book, confidence):
                    matches[calibre_id].setdefault(row.book_id, (book, row))
                    owners[row.book_id].add(calibre_id)

        for calibre_id, candidates in matches.items():
            del pending[calibre_id]
            if len(candidates) != 1:
                contested.update(candidates)
        for calibre_id, candidates in matches.items():
            if len(candidates) != 1:
                continue
            book, row = next(iter(candidates.values()))
            if len(owners[row.book_id]) != 1:
                contested.add(row.book_id)
            if row.book_id in claimed or row.book_id in contested:
                continue
            claimed.add(row.book_id)
            links.append(BookLink(
                calibre_id=calibre_id, device_book_id=row.book_id,
                lpath=book.get('lpath') or '', device_path=row.device_path,
                confidence=confidence,
                title=book.get('title') or row.title or ''))

    linked_ids = {link.calibre_id for link in links}
    unmatched = [calibre_id for calibre_id in books_by_id
                 if calibre_id not in linked_ids]
    device_only = [row for row in index.rows if row.book_id not in claimed]
    debug('matched %d/%d calibre books; %d device file rows not in the library'
          % (len(links), len(books_by_id), len(device_only)))
    return links, unmatched, device_only


def state_for(states, link):
    """The device reading state for *link*, synthesising an empty one."""
    found = states.get(link.device_book_id)
    if found is not None:
        return found
    return DeviceState(book_id=link.device_book_id, exists=False)


def with_relpath(row, relpath):
    return replace(row, relpath=relpath)
