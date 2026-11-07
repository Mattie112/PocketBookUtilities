"""Safe access to the PocketBook ``explorer-N.db``.

Two rules shape this module:

* **Reads never touch the device.** The database is in WAL mode (a 1 MB
  ``-wal`` sidecar is normal), so a plain file copy would read stale data.
  We take a transactionally consistent snapshot with SQLite's online backup
  API into a temp file and do all analysis there.
* **Writes are one short transaction on the device itself.** We deliberately
  do not push the whole working copy back: that would discard anything the
  reader changed in the meantime and would widen the write window from
  milliseconds to seconds.

The package contains no ``DELETE FROM`` or ``DROP`` statement anywhere; a unit
test enforces that. Removing a book from a collection is a soft delete.
"""

import hashlib
import os
import shutil
import sqlite3
import tempfile
import urllib.parse
from contextlib import closing

from ..common.compat import PluginError, debug

READ_TIMEOUT = 15.0
WRITE_TIMEOUT = 30.0


def ro_uri(path):
    """A ``file:`` URI that opens *path* read-only, safe for Windows paths."""
    p = os.path.abspath(path).replace('\\', '/')
    if len(p) > 1 and p[1] == ':':          # C:/... -> /C:/...
        p = '/' + p
    return 'file:' + urllib.parse.quote(p) + '?mode=ro'


def sidecars(db_path):
    """The WAL sidecar files that belong to *db_path* and exist."""
    return [db_path + suffix for suffix in ('-wal', '-shm')
            if os.path.exists(db_path + suffix)]


def snapshot(db_path, dest_path):
    """Copy *db_path* to *dest_path* as a consistent, checkpointed database.

    Tries the online backup API from a read-only connection first. If SQLite
    refuses (it cannot always attach to a WAL database read-only when the
    ``-shm`` file would have to be created), falls back to copying the
    database and its sidecars and recovering from the copy -- which still
    never writes to the device.
    """
    if os.path.exists(dest_path):
        os.remove(dest_path)
    try:
        with closing(sqlite3.connect(ro_uri(db_path), uri=True,
                                     timeout=READ_TIMEOUT)) as src:
            src.execute('PRAGMA busy_timeout = %d' % int(READ_TIMEOUT * 1000))
            with closing(sqlite3.connect(dest_path)) as dst:
                src.backup(dst)
        debug('snapshot via backup API ->', dest_path)
        return dest_path
    except sqlite3.Error as err:
        debug('read-only snapshot failed (%s); falling back to file copy' % err)

    shutil.copyfile(db_path, dest_path)
    for side in sidecars(db_path):
        shutil.copyfile(side, dest_path + side[len(db_path):])
    # Opening the copy read-write lets SQLite replay the copied WAL.
    with closing(sqlite3.connect(dest_path, timeout=READ_TIMEOUT)) as conn:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.commit()
    debug('snapshot via file copy ->', dest_path)
    return dest_path


def connect_readonly(path):
    """Open a local snapshot for reading, with dict-like rows."""
    conn = sqlite3.connect(path, timeout=READ_TIMEOUT)
    conn.row_factory = sqlite3.Row
    return conn


class DeviceWriteTransaction:
    """One ``BEGIN IMMEDIATE`` ... ``COMMIT`` directly against the device.

    Usage::

        with DeviceWriteTransaction(db_path) as conn:
            conn.executemany(SQL, rows)

    The transaction rolls back and the device database is left byte-identical
    if the body raises.
    """

    def __init__(self, db_path, timeout=WRITE_TIMEOUT):
        self.db_path = db_path
        self.timeout = timeout
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_path, timeout=self.timeout,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA busy_timeout = %d' % int(self.timeout * 1000))
        self.conn.execute('BEGIN IMMEDIATE')
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.execute('COMMIT')
            else:
                self.conn.execute('ROLLBACK')
        finally:
            self.conn.close()
            self.conn = None
        return False


class WorkingCopy:
    """A snapshot of the device database plus the path to write back to."""

    def __init__(self, db_path, work_dir=None):
        self.db_path = db_path
        self._tmp_dir = work_dir or tempfile.mkdtemp(prefix='pbutil-')
        self._own_tmp = work_dir is None
        self.work_path = os.path.join(self._tmp_dir, 'explorer.work.db')
        self.conn = None
        self.source_stat = None

    def open(self):
        self.source_stat = self.stat_source()
        snapshot(self.db_path, self.work_path)
        self.conn = connect_readonly(self.work_path)
        return self.conn

    def stat_source(self):
        st = os.stat(self.db_path)
        return {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}

    def write(self):
        """A transaction against the real device database."""
        return DeviceWriteTransaction(self.db_path)

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self._own_tmp:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


# --------------------------------------------------------------------------
# Integrity reporting. Reports problems; never repairs or removes anything.
# --------------------------------------------------------------------------

ORPHAN_CHECKS = (
    ('books_settings',
     'books_settings rows without a book',
     'SELECT COUNT(*) FROM books_settings '
     'WHERE bookid NOT IN (SELECT id FROM books_impl)'),
    ('files',
     'files rows without a book',
     'SELECT COUNT(*) FROM files '
     'WHERE book_id NOT IN (SELECT id FROM books_impl)'),
    ('files',
     'files rows without a folder',
     'SELECT COUNT(*) FROM files '
     'WHERE folder_id NOT IN (SELECT id FROM folders)'),
    ('books_impl',
     'books with no file on the device',
     'SELECT COUNT(*) FROM books_impl '
     'WHERE id NOT IN (SELECT book_id FROM files)'),
    ('bookshelfs_books',
     'collection memberships without a collection',
     'SELECT COUNT(*) FROM bookshelfs_books '
     'WHERE bookshelfid NOT IN (SELECT id FROM bookshelfs)'),
    ('bookshelfs_books',
     'collection memberships without a book',
     'SELECT COUNT(*) FROM bookshelfs_books '
     'WHERE bookid NOT IN (SELECT id FROM books_impl)'),
)


def integrity_report(conn, caps):
    """Run SQLite's own checks plus orphan counts. Read-only."""
    lines = []
    try:
        rows = conn.execute('PRAGMA integrity_check').fetchall()
        lines.append('PRAGMA integrity_check: %s'
                     % ', '.join(str(r[0]) for r in rows))
    except sqlite3.Error as err:
        lines.append('PRAGMA integrity_check failed: %s' % err)

    try:
        fk = conn.execute('PRAGMA foreign_key_check').fetchall()
        lines.append('PRAGMA foreign_key_check: %d problem(s)' % len(fk))
    except sqlite3.Error as err:
        lines.append('PRAGMA foreign_key_check failed: %s' % err)

    lines.append('')
    lines.append('Orphan rows (reported only -- this plugin never removes them):')
    for table, label, sql in ORPHAN_CHECKS:
        if table not in caps.tables:
            continue
        try:
            count = conn.execute(sql).fetchone()[0]
        except sqlite3.Error as err:
            lines.append('  %-48s error: %s' % (label, err))
            continue
        lines.append('  %-48s %d' % (label, count))
    return '\n'.join(lines)


# --------------------------------------------------------------------------
# Write receipts: detect a device that reverted our changes on disconnect.
# --------------------------------------------------------------------------

RECEIPT_SQL = (
    'SELECT bookid, completed, completed_ts, favorite, favorite_ts, '
    'position, position_ts FROM books_settings '
    'WHERE profileid = ? AND bookid IN (%s) ORDER BY bookid'
)


def receipt_digest(conn, profile_id, book_ids):
    """A hash of the reading-state rows for *book_ids*.

    Stored after a write and recomputed on the next connect; a mismatch means
    the reader restored its own copy of the database and our changes are gone.
    """
    book_ids = sorted({int(b) for b in book_ids})
    digest = hashlib.sha1()
    # Chunked so we never exceed SQLite's host-parameter limit.
    for start in range(0, len(book_ids), 500):
        chunk = book_ids[start:start + 500]
        sql = RECEIPT_SQL % ','.join('?' * len(chunk))
        for row in conn.execute(sql, [profile_id] + chunk):
            digest.update(repr(tuple(row)).encode('utf-8'))
    return digest.hexdigest()


def require(condition, message, detail=None):
    if not condition:
        raise PluginError(message, detail)
