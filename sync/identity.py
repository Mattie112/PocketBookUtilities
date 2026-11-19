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
LEFT JOIN books_impl bi ON bi.id = fi.book_id
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


def _better_root(root, current):
    """Which of two equally well-supported roots to believe.

    A folder that *is* the candidate root always counts as found, because
    all it proves is that the storage itself exists. So when every book
    sits in one folder, ``/mnt/ext1`` and ``/mnt/ext1/Books`` score the
    same, and taking the longer one strips ``Books/`` off every path and
    leaves nothing matching exactly. A root the firmware is known to use
    wins a tie; failing that the shorter one, which claims less.
    """
    known = root in CANDIDATE_ROOTS
    if known != (current in CANDIDATE_ROOTS):
        return known
    return len(root) < len(current)


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
        for name in folders[:200]:
            for root, _rest in _split_root(name):
                candidates.add(root)

        best = None
        for location, prefix in prefixes.items():
            if not prefix or not os.path.isdir(prefix):
                continue
            for root in candidates:
                hits = 0
                for name in folders:
                    normalised = name.replace('\\', '/')
                    if not normalised.startswith(root.rstrip('/') + '/') \
                            and normalised != root:
                        continue
                    rest = normalised[len(root):].strip('/')
                    target = os.path.join(prefix, *rest.split('/')) if rest \
                        else prefix
                    if os.path.isdir(target):
                        hits += 1
                if hits and (best is None or hits > best[0]
                             or (hits == best[0]
                                 and _better_root(root, best[1]))):
                    best = (hits, root, location, prefix)
        if best is not None:
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
        if info is not None and folder.startswith(info['root']):
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

def _unique(rows, used):
    free = [r for r in rows if r.book_id not in used]
    return free[0] if len(free) == 1 else None


def build_links(index, calibre_books):
    """Match calibre books to device rows.

    *calibre_books* is an iterable of dicts with ``id``, ``lpath``, ``title``,
    ``authors`` and optionally ``size``.

    Returns ``(links, unmatched_calibre_ids, device_only_rows)``.
    """
    links = []
    unmatched = []
    used = set()

    pending = []
    # Pass 1: exact relative path. Done first for every book so a weaker
    # match can never steal a row that an exact path needs.
    for book in calibre_books:
        key = normalise_path(book.get('lpath'))
        rows = index.by_relpath.get(key, [])
        row = _unique(rows, used)
        if row is not None:
            used.add(row.book_id)
            links.append(BookLink(
                calibre_id=book['id'], device_book_id=row.book_id,
                lpath=book.get('lpath') or '', device_path=row.device_path,
                confidence=MatchConfidence.EXACT_PATH,
                title=book.get('title') or row.title or ''))
        else:
            pending.append(book)

    # Pass 2: filename, confirmed by size where both sides know it.
    still_pending = []
    for book in pending:
        base = os.path.basename(
            (book.get('lpath') or '').replace('\\', '/')).casefold()
        rows = [r for r in index.by_basename.get(base, [])
                if r.book_id not in used]
        size = book.get('size')
        confidence = None
        row = None
        if size is not None:
            sized = [r for r in rows if r.size == size]
            if len(sized) == 1:
                row, confidence = sized[0], MatchConfidence.BASENAME_SIZE
        if row is None and len(rows) == 1:
            row, confidence = rows[0], MatchConfidence.BASENAME
        if row is not None:
            used.add(row.book_id)
            links.append(BookLink(
                calibre_id=book['id'], device_book_id=row.book_id,
                lpath=book.get('lpath') or '', device_path=row.device_path,
                confidence=confidence,
                title=book.get('title') or row.title or ''))
        else:
            still_pending.append(book)

    # Pass 3: title + author.
    for book in still_pending:
        key = (normalise_text(book.get('title')),
               author_key(book.get('authors')))
        rows = index.by_titleauthor.get(key, [])
        row = _unique(rows, used)
        if row is not None:
            used.add(row.book_id)
            links.append(BookLink(
                calibre_id=book['id'], device_book_id=row.book_id,
                lpath=book.get('lpath') or '', device_path=row.device_path,
                confidence=MatchConfidence.METADATA,
                title=book.get('title') or row.title or ''))
        else:
            unmatched.append(book['id'])

    device_only = [r for r in index.rows if r.book_id not in used]
    debug('matched %d/%d calibre books; %d device books not in the library'
          % (len(links), len(links) + len(unmatched), len(device_only)))
    return links, unmatched, device_only


def state_for(states, link):
    """The device reading state for *link*, synthesising an empty one."""
    found = states.get(link.device_book_id)
    if found is not None:
        return found
    return DeviceState(book_id=link.device_book_id, exists=False)


def with_relpath(row, relpath):
    return replace(row, relpath=relpath)
