"""Runtime introspection of the PocketBook ``explorer-N.db`` schema.

Every feature is gated on a capability flag derived here rather than on a
firmware string, so a database that differs from the one this plugin was
developed against disables one feature with a visible reason instead of
raising somewhere deep in a query.

Reference schema: InkPad 3 Color, ``version`` table = 39.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Dict, FrozenSet

from ..common.compat import debug

#: Tables we ever look at. Table names are never taken from data -- any name
#: interpolated into SQL must come from this tuple.
KNOWN_TABLES = (
    'books_impl',
    'books_settings',
    'books_fast_hashes',
    'bookshelfs',
    'bookshelfs_books',
    'booktogenre',
    'files',
    'folders',
    'genres',
    'profiles',
    'social',
    'storages',
    'sync_data',
    'version',
)

#: The schema version we developed and tested against.
REFERENCE_DB_VERSION = 39


@dataclass(frozen=True)
class Capabilities:
    tables: FrozenSet[str] = frozenset()
    cols: Dict[str, FrozenSet[str]] = field(default_factory=dict)
    db_version: int = 0
    fingerprint: str = ''

    # --- derived flags -------------------------------------------------
    @property
    def has_profiles(self):
        return 'profiles' in self.tables and 'profileid' in self.cols.get('books_settings', ())

    @property
    def has_reading_state(self):
        return 'books_settings' in self.tables and 'completed' in self.cols.get('books_settings', ())

    @property
    def has_favorites(self):
        return 'favorite' in self.cols.get('books_settings', ())

    @property
    def has_position(self):
        return 'position' in self.cols.get('books_settings', ())

    @property
    def has_page_counters(self):
        bs = self.cols.get('books_settings', ())
        return 'cpage' in bs and 'npage' in bs

    @property
    def has_opentime(self):
        return 'opentime' in self.cols.get('books_settings', ())

    @property
    def has_shelves(self):
        return 'bookshelfs' in self.tables and 'bookshelfs_books' in self.tables

    @property
    def has_shelf_uuid(self):
        return 'uuid' in self.cols.get('bookshelfs', ())

    @property
    def has_shelf_is_deleted(self):
        """Can a book be taken off a shelf (by flagging the membership)?"""
        return 'is_deleted' in self.cols.get('bookshelfs_books', ())

    @property
    def has_shelf_soft_delete(self):
        """Can a shelf itself be flagged as deleted?"""
        return 'is_deleted' in self.cols.get('bookshelfs', ())

    @property
    def has_series(self):
        bi = self.cols.get('books_impl', ())
        return 'series' in bi and 'numinseries' in bi

    @property
    def has_sort_title(self):
        return 'sort_title' in self.cols.get('books_impl', ())

    @property
    def has_file_size(self):
        return 'size' in self.cols.get('files', ())

    @property
    def is_reference_version(self):
        return self.db_version == REFERENCE_DB_VERSION

    def has(self, table, *columns):
        if table not in self.tables:
            return False
        present = self.cols.get(table, frozenset())
        return all(c in present for c in columns)

    def describe(self):
        """Ordered (label, bool) pairs for the capability checklist in the UI."""
        return [
            ('Reading status (completed)', self.has_reading_state),
            ('Favourites', self.has_favorites),
            ('Reading position', self.has_position),
            ('Progress (page counters)', self.has_page_counters),
            ('Last opened time', self.has_opentime),
            ('Collections (shelves)', self.has_shelves),
            ('Collection UUIDs', self.has_shelf_uuid),
            ('Series on device', self.has_series),
            ('User profiles', self.has_profiles),
        ]


def _table_columns(conn, table):
    # `table` is always a literal from KNOWN_TABLES, never user data.
    try:
        rows = conn.execute('PRAGMA table_info("%s")' % table).fetchall()
    except Exception:
        return frozenset()
    return frozenset(r[1] for r in rows)


def read_db_version(conn):
    """The single row of the ``version`` table, or 0 when absent."""
    try:
        row = conn.execute('SELECT id FROM version').fetchone()
    except Exception:
        return 0
    return int(row[0]) if row else 0


def introspect(conn):
    """Build :class:`Capabilities` for an open connection."""
    names = frozenset(
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    )
    tables = frozenset(t for t in KNOWN_TABLES if t in names)
    cols = {t: _table_columns(conn, t) for t in tables}

    parts = []
    for t in sorted(cols):
        for c in sorted(cols[t]):
            parts.append('%s.%s' % (t, c))
    fingerprint = hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()

    caps = Capabilities(
        tables=tables,
        cols=cols,
        db_version=read_db_version(conn),
        fingerprint=fingerprint,
    )
    debug('schema version=%s fingerprint=%s tables=%d'
          % (caps.db_version, fingerprint[:8], len(tables)))
    return caps
