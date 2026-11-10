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
#: Triggers rewrite completed_ts, and reading moves the rest.
DEVICE_OWNED = frozenset({'completed_ts', 'favorite_ts', 'position_ts',
                          'cpage', 'npage', 'opentime'})


def build(plan, profile_id, field_for_key):
    """Expectations for the selected device-bound changes in *plan*.

    *field_for_key* maps a field key to its :class:`FieldSpec`.
    """
    from ..sync.model import Side

    out = []
    for change in plan.selected(Side.DEVICE):
        spec = field_for_key(change.field)
        if spec is None:
            continue
        if change.field == COLLECTIONS:
            out.append({
                'kind': COLLECTIONS,
                'bookid': change.link.device_book_id,
                'title': change.link.title,
                'names': sorted(change.new or ()),
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


def _live_collection_names(conn, bookid):
    rows = conn.execute(
        'SELECT bs.name FROM bookshelfs_books bb '
        'JOIN bookshelfs bs ON bs.id = bb.bookshelfid '
        'WHERE bb.bookid = ? AND bb.is_deleted = 0 AND bs.is_deleted = 0',
        (bookid,)).fetchall()
    return sorted(r[0] for r in rows if r[0])


def verify(conn, expectations, caps=None):
    """Which expectations no longer hold. Empty means everything survived."""
    failures = []
    for item in expectations or ():
        kind = item.get('kind')
        try:
            if kind == COLLECTIONS:
                live = _live_collection_names(conn, item['bookid'])
                if live != sorted(item.get('names') or ()):
                    failures.append((item, live))
            elif kind == SETTING:
                # The column name comes from the field registry, never data.
                row = conn.execute(
                    'SELECT "%s" FROM books_settings '
                    'WHERE bookid = ? AND profileid = ?' % item['column'],
                    (item['bookid'], item['profileid'])).fetchone()
                found = row[0] if row is not None else None
                if not _same(found, item.get('value')):
                    failures.append((item, found))
        except Exception as err:
            debug('could not verify %r: %s' % (item, err))
    return failures


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
