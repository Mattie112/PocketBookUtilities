"""Remembers what each side looked like at the last successful sync.

This is what makes the merge a real three-way merge instead of a timestamp
comparison. Knowing the last-synced value lets us tell *which* side changed,
so an idle run proposes nothing at all and a value that only one side touched
is never fought over.

It also removes the need for hidden mirror-timestamp columns in the user's
library, and makes the plugin immune to bogus timestamps already present on
the device -- the reference device has every ``favorite_ts`` set to the same
bulk value while every ``favorite`` is 0.
"""

import json
import os
import sqlite3

from ..common.compat import debug

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_state (
    library     TEXT NOT NULL,
    serial      TEXT NOT NULL,
    calibre_id  INTEGER NOT NULL,
    field       TEXT NOT NULL,
    device_json TEXT,
    calibre_json TEXT,
    updated     INTEGER,
    PRIMARY KEY (library, serial, calibre_id, field)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

#: Sentinel meaning "we have never synced this field for this book".
UNKNOWN = object()


def _dumps(value):
    if isinstance(value, (set, frozenset)):
        return json.dumps(sorted(value), ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(text):
    if text is None:
        return UNKNOWN
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return UNKNOWN


class SyncStateStore:
    """A small SQLite file in the plugin's own configuration directory."""

    def __init__(self, path):
        self.path = path
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -- reading --------------------------------------------------------
    def load(self, library, serial):
        """``{(calibre_id, field): (device_value, calibre_value)}``."""
        out = {}
        for row in self.conn.execute(
                'SELECT calibre_id, field, device_json, calibre_json '
                'FROM sync_state WHERE library = ? AND serial = ?',
                (library, serial)):
            out[(row['calibre_id'], row['field'])] = (
                _loads(row['device_json']), _loads(row['calibre_json']))
        debug('loaded %d remembered field states' % len(out))
        return out

    def last_sync(self, library, serial):
        row = self.conn.execute(
            'SELECT value FROM meta WHERE key = ?',
            ('last_sync:%s:%s' % (library, serial),)).fetchone()
        return int(row[0]) if row else 0

    # -- writing --------------------------------------------------------
    def record(self, library, serial, entries, timestamp=None):
        """Remember the agreed values.

        *entries* is an iterable of ``(calibre_id, field, device_value,
        calibre_value)``.
        """
        rows = [
            (library, serial, int(calibre_id), field,
             _dumps(device_value), _dumps(calibre_value), timestamp or 0)
            for calibre_id, field, device_value, calibre_value in entries
        ]
        if not rows:
            return 0
        self.conn.executemany(
            'INSERT INTO sync_state (library, serial, calibre_id, field, '
            'device_json, calibre_json, updated) VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(library, serial, calibre_id, field) DO UPDATE SET '
            'device_json = excluded.device_json, '
            'calibre_json = excluded.calibre_json, '
            'updated = excluded.updated', rows)
        self.conn.commit()
        return len(rows)

    def set_last_sync(self, library, serial, timestamp):
        self.conn.execute(
            'INSERT INTO meta (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            ('last_sync:%s:%s' % (library, serial), str(int(timestamp))))
        self.conn.commit()

    def forget(self, library, serial):
        """Start over for one library/device pair.

        Uses a soft reset rather than removing rows, so the file keeps a
        record that a reset happened and nothing is ever silently destroyed.
        """
        self.conn.execute(
            'UPDATE sync_state SET device_json = NULL, calibre_json = NULL, '
            'updated = 0 WHERE library = ? AND serial = ?', (library, serial))
        self.conn.commit()

    # -- receipts -------------------------------------------------------
    def set_receipt(self, serial, payload):
        self.conn.execute(
            'INSERT INTO meta (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            ('receipt:%s' % serial, json.dumps(payload, ensure_ascii=False)))
        self.conn.commit()

    def get_receipt(self, serial):
        row = self.conn.execute('SELECT value FROM meta WHERE key = ?',
                                ('receipt:%s' % serial,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return None

    def clear_receipt(self, serial):
        self.conn.execute(
            "UPDATE meta SET value = '' WHERE key = ?", ('receipt:%s' % serial,))
        self.conn.commit()
