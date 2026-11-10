"""Record what was written to the device, and check it is still there.

PocketBook can rewrite its library database from memory when the USB
connection drops, undoing anything written while it was plugged in. To spot
that, we note what we wrote and re-check it on the next connection.

The first version hashed whole ``books_settings`` rows. That was wrong in
both directions: the hash covered ``position`` and ``position_ts``, which
the reader updates simply by being read, so an evening's reading looked
like a revert; and for a collections write it hashed a table we had not
touched while ignoring the one we had.

So an expectation names exactly one thing we wrote, and nothing else.
"""

from ..common.compat import debug

SETTING = 'setting'
COLLECTIONS = 'collections'

#: Columns the device maintains itself, which must never be asserted on.
#: Triggers rewrite completed_ts, and reading moves the rest. That includes
#: ``position``: we do write it, but opening the book moves it again, so a
#: position that differs next time says nothing about a revert.
DEVICE_OWNED = frozenset({'completed_ts', 'favorite_ts', 'position_ts',
                          'position', 'cpage', 'npage', 'opentime'})


def _clean_names(names):
    """Collection names the way the shelf writer treats them."""
    return {n.strip() for n in (names or ()) if n and n.strip()}


def _fold(names):
    return {n.casefold() for n in names}


def build(plan, profile_id, field_for_key, remove_missing=True):
    """Expectations for the selected device-bound changes in *plan*.

    *field_for_key* maps a field key to its :class:`FieldSpec`.
    *remove_missing* is the setting the collections were written with: when
    it is off nothing was removed, so nothing is expected to stay removed.
    """
    from ..sync.model import Side

    out = []
    for change in plan.selected(Side.DEVICE):
        spec = field_for_key(change.field)
        if spec is None:
            continue
        if change.field == COLLECTIONS:
            # Record what we did, not what the book's whole set should be:
            # the names we made sure are there, and the ones we took away.
            # Anything else on the book was not ours to assert on.
            wanted = _clean_names(change.new)
            removed = set()
            if remove_missing:
                kept = _fold(wanted)
                removed = {n for n in _clean_names(change.old)
                           if n.casefold() not in kept}
            out.append({
                'kind': COLLECTIONS,
                'bookid': change.link.device_book_id,
                'title': change.link.title,
                'names': sorted(wanted),
                'removed': sorted(removed),
            })
            continue
        column = spec.device_column
        if not column or column in DEVICE_OWNED:
            continue
        out.append({
            'kind': SETTING,
            'bookid': change.link.device_book_id,
            'title': change.link.title,
            'profileid': profile_id,
            'column': column,
            'value': change.new,
            'field': change.field,
        })
    return out


def _has_column(conn, table, column):
    # `table` is always a literal from this module, never data.
    rows = conn.execute('PRAGMA table_info("%s")' % table).fetchall()
    return any(r[1] == column for r in rows)


def _live_collection_names(conn, bookid):
    # Not every firmware soft-deletes; without the column, a row is live.
    conditions = ['bb.bookid = ?']
    if _has_column(conn, 'bookshelfs_books', 'is_deleted'):
        conditions.append('bb.is_deleted = 0')
    if _has_column(conn, 'bookshelfs', 'is_deleted'):
        conditions.append('bs.is_deleted = 0')
    rows = conn.execute(
        'SELECT bs.name FROM bookshelfs_books bb '
        'JOIN bookshelfs bs ON bs.id = bb.bookshelfid '
        'WHERE ' + ' AND '.join(conditions), (bookid,)).fetchall()
    return sorted(r[0] for r in rows if r[0])


def check(conn, expectations):
    """``(failures, unchecked)`` for *expectations*.

    *failures* no longer hold. *unchecked* could not be looked at, which is
    not the same as having survived and must not be reported as such.
    """
    failures = []
    unchecked = []
    for item in expectations or ():
        kind = item.get('kind')
        try:
            if kind == COLLECTIONS:
                live = _live_collection_names(conn, item['bookid'])
                # The device keeps its own casing for a shelf that already
                # existed, so names are compared without regard to case.
                present = _fold(live)
                missing = _fold(item.get('names') or ()) - present
                back = _fold(item.get('removed') or ()) & present
                if missing or back:
                    failures.append((item, live))
            elif kind == SETTING:
                # The column name comes from the field registry, never data.
                # SQLite reads a quoted name that is not a column as a
                # string literal, which would compare the name itself to
                # the expected value, so a missing column is ruled out
                # first rather than left to raise.
                if not _has_column(conn, 'books_settings', item['column']):
                    unchecked.append(item)
                    continue
                where, params = 'bookid = ?', [item['bookid']]
                if _has_column(conn, 'books_settings', 'profileid'):
                    where += ' AND profileid = ?'
                    params.append(item['profileid'])
                row = conn.execute(
                    'SELECT "%s" FROM books_settings WHERE %s'
                    % (item['column'], where), params).fetchone()
                found = row[0] if row is not None else None
                if not _same(found, item.get('value')):
                    failures.append((item, found))
            else:
                unchecked.append(item)
        except Exception as err:
            debug('could not verify %r: %s' % (item, err))
            unchecked.append(item)
    return failures, unchecked


def verify(conn, expectations):
    """Which expectations no longer hold."""
    return check(conn, expectations)[0]


def _same(found, expected):
    if found is None or expected is None:
        return found == expected
    if isinstance(expected, bool) or isinstance(found, bool):
        return bool(found) == bool(expected)
    if isinstance(expected, int) and not isinstance(expected, bool):
        try:
            return int(found) == int(expected)
        except (TypeError, ValueError):
            return False
    return str(found) == str(expected)


def describe(failures, when):
    """A message naming what is missing, not just that something is."""
    lines = ['%d change(s) written on %s are no longer on the device:'
             % (len(failures), when), '']
    for item, found in failures[:12]:
        title = (item.get('title') or 'book %s' % item.get('bookid'))[:48]
        if item.get('kind') == COLLECTIONS:
            lines.append('   %s' % title)
            lines.append('      collections: expected %s, found %s'
                         % (', '.join(item.get('names') or ()) or 'none',
                            ', '.join(found) or 'none'))
            if item.get('removed'):
                lines.append('      removed by the sync: %s'
                             % ', '.join(item['removed']))
        else:
            lines.append('   %-48s %s: expected %r, found %r'
                         % (title, item.get('field') or item.get('column'),
                            item.get('value'), found))
    if len(failures) > 12:
        lines.append('   ... and %d more' % (len(failures) - 12))
    lines += [
        '',
        'PocketBook rewrites its library database from memory when the USB '
        'connection drops, which undoes changes made while it was plugged '
        'in. Eject the reader from calibre before unplugging it, then sync '
        'again.',
    ]
    return '\n'.join(lines)
