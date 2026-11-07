"""Device collections (``bookshelfs`` / ``bookshelfs_books``).

Three details of the real schema drive this module:

* ``bookshelfs`` declares ``UNIQUE(name) ON CONFLICT FAIL``. Inserting a
  shelf whose name already exists -- even one that is only soft-deleted --
  raises. The existing row has to be undeleted instead, which is exactly
  what the previous plugin failed to do, leaving collections permanently
  invisible on the reader.
* Membership is removed by setting ``is_deleted = 1``, never by deleting the
  row. Nothing here issues a DELETE.
* ``bookshelfs.uuid`` is indexed and PocketBook's cloud sync relies on it.
  We sample the format from an existing row rather than inventing one.
"""

import uuid as uuid_module

from ..common.compat import debug, now_ts

CREATE_SHELF_WITH_UUID = (
    'INSERT INTO bookshelfs (name, is_deleted, ts, uuid) VALUES (?, 0, ?, ?)')
CREATE_SHELF = (
    'INSERT INTO bookshelfs (name, is_deleted, ts) VALUES (?, 0, ?)')
UNDELETE_SHELF = (
    'UPDATE bookshelfs SET is_deleted = 0, ts = ? WHERE id = ?')
ADD_MEMBERSHIP = (
    'INSERT INTO bookshelfs_books (bookshelfid, bookid, ts, is_deleted) '
    'VALUES (?, ?, ?, 0)')
UNDELETE_MEMBERSHIP = (
    'UPDATE bookshelfs_books SET is_deleted = 0, ts = ? '
    'WHERE bookshelfid = ? AND bookid = ?')
REMOVE_MEMBERSHIP = (
    'UPDATE bookshelfs_books SET is_deleted = 1, ts = ? '
    'WHERE bookshelfid = ? AND bookid = ?')


def sample_uuid_format(conn):
    """How this firmware writes ``bookshelfs.uuid``: 32 hex, 36 dashed, none."""
    row = conn.execute(
        'SELECT uuid FROM bookshelfs WHERE uuid IS NOT NULL '
        "AND uuid <> '' LIMIT 1").fetchone()
    if row is None:
        return 'hex32'          # what the reference firmware writes
    text = str(row[0])
    if len(text) == 36 and text.count('-') == 4:
        return 'dashed36'
    if len(text) == 32:
        return 'hex32'
    return 'hex32'


def make_uuid(style):
    if style == 'dashed36':
        return str(uuid_module.uuid4())
    return uuid_module.uuid4().hex


def device_collections_by_book(shelves, memberships):
    """``{device_book_id: frozenset(names)}`` for live memberships only."""
    out = {}
    for book_id, by_shelf in memberships.items():
        names = set()
        for shelf_id, is_deleted in by_shelf.items():
            shelf = shelves.get(shelf_id)
            if shelf is None or is_deleted or shelf['is_deleted']:
                continue
            if shelf['name']:
                names.add(shelf['name'])
        if names:
            out[book_id] = frozenset(names)
    return out


class ShelfWriter:
    """Applies collection changes inside a caller-owned transaction."""

    def __init__(self, conn, shelves, memberships, caps):
        self.conn = conn
        self.caps = caps
        self.uuid_style = (sample_uuid_format(conn)
                           if caps.has_shelf_uuid else None)
        # Case-insensitive lookup, but the device's own casing is preserved.
        self.by_name = {}
        self.shelves = dict(shelves)
        for shelf in shelves.values():
            self.by_name.setdefault(shelf['name'].casefold(), shelf)
        self.memberships = {k: dict(v) for k, v in memberships.items()}
        self.counts = {'shelves_created': 0, 'shelves_undeleted': 0,
                       'added': 0, 'readded': 0, 'removed': 0}

    def shelf_id_for(self, name, timestamp):
        """Resolve a collection name to an id, creating or undeleting it."""
        existing = self.by_name.get(name.casefold())
        if existing is not None:
            if existing['is_deleted']:
                self.conn.execute(UNDELETE_SHELF, (timestamp, existing['id']))
                existing['is_deleted'] = 0
                self.counts['shelves_undeleted'] += 1
            return existing['id']

        if self.uuid_style:
            cursor = self.conn.execute(
                CREATE_SHELF_WITH_UUID,
                (name, timestamp, make_uuid(self.uuid_style)))
        else:
            cursor = self.conn.execute(CREATE_SHELF, (name, timestamp))
        shelf_id = cursor.lastrowid
        record = {'id': shelf_id, 'name': name, 'is_deleted': 0}
        self.shelves[shelf_id] = record
        self.by_name[name.casefold()] = record
        self.counts['shelves_created'] += 1
        return shelf_id

    def set_for_book(self, device_book_id, wanted, remove_missing=True,
                     timestamp=None):
        """Make the device's collections for one book match *wanted*."""
        timestamp = timestamp or now_ts()
        wanted = {n.strip() for n in (wanted or ()) if n and n.strip()}
        current = self.memberships.get(device_book_id, {})

        wanted_ids = {}
        for name in wanted:
            wanted_ids[self.shelf_id_for(name, timestamp)] = name

        for shelf_id in wanted_ids:
            state = current.get(shelf_id)
            if state is None:
                self.conn.execute(ADD_MEMBERSHIP,
                                  (shelf_id, device_book_id, timestamp))
                self.counts['added'] += 1
            elif state:
                self.conn.execute(UNDELETE_MEMBERSHIP,
                                  (timestamp, shelf_id, device_book_id))
                self.counts['readded'] += 1
            current[shelf_id] = 0

        if remove_missing:
            for shelf_id, state in list(current.items()):
                if shelf_id in wanted_ids or state:
                    continue
                self.conn.execute(REMOVE_MEMBERSHIP,
                                  (timestamp, shelf_id, device_book_id))
                current[shelf_id] = 1
                self.counts['removed'] += 1

        self.memberships[device_book_id] = current
        return self.counts


def apply_collections_to_device(conn, plan, shelves, memberships, caps,
                                remove_missing=True, timestamp=None):
    """Apply the selected ``collections`` changes. Caller owns the transaction."""
    from .model import Side

    changes = [c for c in plan.selected(Side.DEVICE)
               if c.field == 'collections']
    if not changes:
        return {}
    writer = ShelfWriter(conn, shelves, memberships, caps)
    for change in changes:
        writer.set_for_book(change.link.device_book_id, change.new,
                            remove_missing=remove_missing,
                            timestamp=timestamp)
    debug('collections: %r' % writer.counts)
    return writer.counts


# --------------------------------------------------------------------------
# Where the calibre-side collection names come from
# --------------------------------------------------------------------------

SOURCE_COLUMN = 'column'
SOURCE_TAGS = 'tags'
SOURCE_SERIES = 'series'
SOURCE_TEMPLATE = 'template'

SOURCE_LABELS = (
    (SOURCE_COLUMN, 'A custom column (default: PB Collections)'),
    (SOURCE_TAGS, 'Calibre tags'),
    (SOURCE_SERIES, 'Series name'),
    (SOURCE_TEMPLATE, 'A template'),
)


def names_from_value(value):
    """Normalise whatever a source produced into a set of names."""
    if value is None:
        return frozenset()
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(',')]
    else:
        parts = [str(p).strip() for p in value]
    return frozenset(p for p in parts if p)
