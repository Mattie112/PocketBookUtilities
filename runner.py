"""Orchestration: read the device, build a plan, apply what the user kept.

Kept apart from ``action.py`` so the menu code stays about menus.
"""

import os
import shutil
import time
from datetime import datetime

from .common.compat import PluginError, debug, now_ts
from .config import (backup_dir, column_for_field, device_settings,
                     library_key, mapped_fields, plugin_prefs, state_db_path)
from .device import db as dbmod
from .device.detect import complete_from_db
from .sync import collections as collmod
from .sync import engine, identity, metadata_push
from .sync.engine import Mode, SyncInputs
from .sync.fields import BY_KEY, canon_set
from .sync.model import MatchConfidence, Side
from .sync.state import SyncStateStore


# --------------------------------------------------------------------------
# The calibre side
# --------------------------------------------------------------------------

def collect_calibre_books(gui):
    """Books calibre believes are on the device, with their device paths.

    Read from calibre's own device views rather than from ``metadata.calibre``
    on the reader: it is the same information, but calibre has already done
    the matching and already knows about books deleted since the last sync.
    """
    api = gui.current_db.new_api
    found = {}
    for name in ('memory_view', 'card_a_view', 'card_b_view'):
        view = getattr(gui, name, None)
        if view is None:
            continue
        try:
            booklist = view.model().db
        except Exception as err:
            debug('no book list for %s: %s' % (name, err))
            continue
        for book in booklist or ():
            calibre_id = getattr(book, 'application_id', None)
            lpath = getattr(book, 'lpath', None)
            if calibre_id is None or not lpath:
                continue
            found[int(calibre_id)] = {
                'id': int(calibre_id),
                'lpath': lpath,
                'size': getattr(book, 'size', None),
                'title': None,
                'authors': None,
            }

    if not found:
        raise PluginError(
            'calibre does not list any books on this device yet.',
            'Open the device view once so calibre reads the reader\'s book '
            'list, then try again. If the device was just connected, give it '
            'a moment to finish scanning.')

    ids = list(found)
    titles = api.all_field_for('title', ids)
    authors = api.all_field_for('authors', ids)
    for calibre_id in ids:
        found[calibre_id]['title'] = titles.get(calibre_id)
        found[calibre_id]['authors'] = ' & '.join(
            authors.get(calibre_id) or ())
    debug('calibre reports %d book(s) on the device' % len(found))
    return list(found.values())


def read_column(api, column, book_ids):
    """``all_field_for`` that tolerates a column calibre has not loaded.

    ``db.create_custom_column`` writes the column straight away, but calibre
    only picks it up when it restarts -- until then ``all_field_for`` raises
    ``KeyError``. Treating that as "calibre has no values yet" is exactly
    right, and it is what lets the dry run run immediately after setup.
    """
    if not column:
        return {}
    try:
        return api.all_field_for(column, book_ids)
    except KeyError:
        debug('column %s is not loaded yet; treating it as empty' % column)
        return {}
    except Exception as err:
        debug('cannot read column %s: %s' % (column, err))
        return {}


def read_calibre_values(gui, settings, book_ids, field_keys):
    """``{calibre_id: {field_key: value}}`` plus the last-modified map."""
    api = gui.current_db.new_api
    values = {book_id: {} for book_id in book_ids}
    for key in field_keys:
        column_values = read_column(api, column_for_field(settings, key),
                                    book_ids)
        for book_id, value in column_values.items():
            values[book_id][key] = value
    modified = api.all_field_for('last_modified', book_ids)
    return values, modified


def calibre_collections(gui, settings, book_ids):
    """Collection names per book, from whichever source is configured."""
    api = gui.current_db.new_api
    source = settings.get('collections_source', collmod.SOURCE_COLUMN)
    out = {}

    if source == collmod.SOURCE_TAGS:
        raw = read_column(api, 'tags', book_ids)
    elif source == collmod.SOURCE_SERIES:
        raw = read_column(api, 'series', book_ids)
    elif source == collmod.SOURCE_TEMPLATE:
        return _collections_from_template(gui, settings, book_ids)
    else:
        raw = read_column(api, column_for_field(settings, 'collections'),
                          book_ids)

    for book_id, value in raw.items():
        names = collmod.names_from_value(value)
        if names:
            out[book_id] = names
    return out


def _collections_from_template(gui, settings, book_ids):
    from calibre.ebooks.metadata.book.formatter import SafeFormat

    template = settings.get('collections_template') or ''
    if not template.strip():
        return {}
    api = gui.current_db.new_api
    formatter = SafeFormat()
    out = {}
    for book_id in book_ids:
        try:
            metadata = api.get_proxy_metadata(book_id)
            text = formatter.safe_format(template, metadata, 'PB-COLLECTIONS',
                                         metadata)
        except Exception as err:
            debug('template failed for book %s: %s' % (book_id, err))
            continue
        names = collmod.names_from_value(text)
        if names:
            out[book_id] = names
    return out


# --------------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------------

def make_backup(device, keep=10):
    """A consistent copy of the device database, before any write."""
    target_dir = backup_dir(device.serial)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    name = 'explorer-%d-%s.db' % (device.explorer_version, stamp)
    path = os.path.join(target_dir, name)
    dbmod.snapshot(device.db_path, path)

    if device.cr3hist_path and os.path.exists(device.cr3hist_path):
        shutil.copyfile(device.cr3hist_path,
                        os.path.join(target_dir, 'cr3hist-%s.xml' % stamp))

    prune_backups(target_dir, keep)
    debug('backup written to %s' % path)
    return path


def list_backups(serial):
    target = backup_dir(serial)
    names = [n for n in os.listdir(target) if n.endswith('.db')]
    out = []
    for name in sorted(names):
        full = os.path.join(target, name)
        stat = os.stat(full)
        out.append({'path': full, 'name': name, 'size': stat.st_size,
                    'mtime': stat.st_mtime})
    return out


def prune_backups(target_dir, keep):
    """Keep the newest *keep* backups, and always the very first one."""
    names = sorted(n for n in os.listdir(target_dir) if n.endswith('.db'))
    if len(names) <= max(keep, 1):
        return []
    doomed = names[1:-keep] if keep > 0 else names[1:]
    removed = []
    for name in doomed:
        full = os.path.join(target_dir, name)
        try:
            os.remove(full)
            removed.append(name)
        except OSError as err:
            debug('could not remove old backup %s: %s' % (name, err))
    return removed


def restore_backup(device, backup_path):
    """Put a backup back on the device, via SQLite rather than a file copy."""
    if not os.path.exists(backup_path):
        raise PluginError('That backup file no longer exists.', backup_path)
    import sqlite3
    source = sqlite3.connect(dbmod.ro_uri(backup_path), uri=True)
    try:
        target = sqlite3.connect(device.db_path, timeout=dbmod.WRITE_TIMEOUT)
        try:
            target.execute('PRAGMA busy_timeout = 30000')
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    debug('restored %s onto the device' % backup_path)


# --------------------------------------------------------------------------
# The sync itself
# --------------------------------------------------------------------------

class SyncContext:
    """Everything read from the device for one run, plus the plan."""

    def __init__(self, gui, device, settings):
        self.gui = gui
        self.device = device
        self.settings = settings
        self.copy = None
        self.caps = None
        self.index = None
        self.states = {}
        self.shelves = {}
        self.memberships = {}
        self.inputs = None
        self.plan = None
        self.library = library_key(gui.current_db)

    def read(self, restrict_to=None, mode=Mode.MERGE):
        self.copy = dbmod.WorkingCopy(self.device.db_path)
        conn = self.copy.open()
        self.device = complete_from_db(self.device, conn)
        self.caps = self.device.caps

        prefixes = {'main': self.device.main_prefix,
                    'card': self.device.card_prefix}
        self.index = identity.load_device_index(conn, self.caps, prefixes)
        self.states = identity.load_reading_state(conn, self.caps,
                                                  self.device.profile_id)
        self.shelves, self.memberships = identity.load_shelves(conn, self.caps)

        books = collect_calibre_books(self.gui)
        if restrict_to is not None:
            wanted = set(restrict_to)
            books = [b for b in books if b['id'] in wanted]

        links, unmatched, device_only = identity.build_links(self.index, books)

        fields = mapped_fields(self.settings)
        with SyncStateStore(state_db_path()) as store:
            prior = store.load(self.library, self.device.serial)

        book_ids = [link.calibre_id for link in links]
        values, modified = read_calibre_values(
            self.gui, self.settings, book_ids, fields)
        wanted_collections = (calibre_collections(self.gui, self.settings,
                                                  book_ids)
                              if 'collections' in fields else {})

        self.inputs = SyncInputs(
            links=links,
            device_states=self.states,
            calibre_values=values,
            calibre_modified=modified,
            device_collections=collmod.device_collections_by_book(
                self.shelves, self.memberships),
            calibre_collections=wanted_collections,
            prior=prior,
            unmatched_calibre=unmatched,
            device_only=device_only,
        )

        self.plan = engine.build_plan(
            self.inputs, self.caps, enabled_fields=fields, mode=mode,
            skew=int(self.settings.get('skew_seconds', 90)),
            allow_clear=bool(self.settings.get('allow_clear', False)),
            trust_floor=MatchConfidence(
                int(self.settings.get('trust_floor', 2))))
        return self.plan

    def close(self):
        if self.copy is not None:
            self.copy.close()
            self.copy = None

    # -- applying ------------------------------------------------------
    def apply(self):
        """Write the selected changes. Returns a short report."""
        report = []
        calibre_written = self._apply_calibre()
        if calibre_written:
            report.append('Updated %d value(s) in calibre.' % calibre_written)

        device_written = self._apply_device()
        if device_written:
            report.append('Updated %d book(s) on the device.' % device_written)

        self._remember()
        if not report:
            report.append('Nothing was written.')
        return '\n'.join(report)

    def _apply_calibre(self):
        changes = self.plan.selected(Side.CALIBRE)
        if not changes:
            return 0
        api = self.gui.current_db.new_api
        updates = engine.calibre_updates(
            self.plan, lambda key: column_for_field(self.settings, key))
        written = 0
        for column, mapping in updates.items():
            api.set_field(column, mapping)
            written += len(mapping)
        touched = sorted({c.link.calibre_id for c in changes})
        try:
            self.gui.library_view.model().refresh_ids(touched)
            self.gui.tags_view.recount()
        except Exception as err:
            debug('could not refresh the library view: %s' % err)
        return written

    def _apply_device(self):
        changes = self.plan.selected(Side.DEVICE)
        if not changes:
            return 0

        keep = int(plugin_prefs().get('backup_keep', 10))
        make_backup(self.device, keep=keep)

        stamp = now_ts()
        with self.copy.write() as conn:
            engine.apply_to_device(conn, self.plan, self.device.profile_id,
                                   timestamp=stamp)
            if self.caps.has_shelves:
                collmod.apply_collections_to_device(
                    conn, self.plan, self.shelves, self.memberships,
                    self.caps,
                    remove_missing=bool(self.settings.get(
                        'collections_remove_missing', True)),
                    timestamp=stamp)
        self._write_receipt()
        return len({c.link.device_book_id for c in changes})

    def _write_receipt(self):
        ids = engine.touched_device_ids(self.plan)
        if not ids:
            return
        fresh = dbmod.WorkingCopy(self.device.db_path)
        try:
            conn = fresh.open()
            digest = dbmod.receipt_digest(conn, self.device.profile_id, ids)
        finally:
            fresh.close()
        with SyncStateStore(state_db_path()) as store:
            store.set_receipt(self.device.serial, {
                'digest': digest, 'book_ids': ids, 'when': now_ts(),
                'profile_id': self.device.profile_id,
            })

    def _remember(self):
        """Re-read the device so trigger-written timestamps are recorded."""
        if self.plan.selected(Side.DEVICE):
            fresh = dbmod.WorkingCopy(self.device.db_path)
            try:
                conn = fresh.open()
                self.inputs.device_states = identity.load_reading_state(
                    conn, self.caps, self.device.profile_id)
                shelves, memberships = identity.load_shelves(conn, self.caps)
                self.inputs.device_collections = \
                    collmod.device_collections_by_book(shelves, memberships)
            finally:
                fresh.close()

        # Mirror the calibre-side writes into the snapshot we compare against.
        for change in self.plan.selected(Side.CALIBRE):
            self.inputs.calibre_values.setdefault(
                change.link.calibre_id, {})[change.field] = change.new
            if change.field == 'collections':
                self.inputs.calibre_collections[change.link.calibre_id] = \
                    canon_set(change.new) or frozenset()

        fields = mapped_fields(self.settings)
        entries = engine.record_entries(self.plan, self.inputs, self.caps,
                                        enabled_fields=fields)
        with SyncStateStore(state_db_path()) as store:
            store.record(self.library, self.device.serial, entries, now_ts())
            store.set_last_sync(self.library, self.device.serial, now_ts())


def verify_last_write(device):
    """Did the reader keep the changes we made last time?

    PocketBook can rewrite its database from memory when the USB connection
    drops, silently undoing anything written. Returns ``(ok, message)`` or
    ``None`` when there is nothing to check.
    """
    with SyncStateStore(state_db_path()) as store:
        receipt = store.get_receipt(device.serial)
    if not receipt or not receipt.get('book_ids'):
        return None

    copy = dbmod.WorkingCopy(device.db_path)
    try:
        conn = copy.open()
        digest = dbmod.receipt_digest(conn, receipt.get('profile_id', 1),
                                      receipt['book_ids'])
    finally:
        copy.close()

    when = time.strftime('%Y-%m-%d %H:%M',
                         time.localtime(receipt.get('when', 0)))
    if digest == receipt.get('digest'):
        return True, ('The %d change(s) written on %s are still on the device.'
                      % (len(receipt['book_ids']), when))
    return False, (
        'The %d change(s) written on %s are no longer on the device.\n\n'
        'PocketBook rewrites its library database from memory when the USB '
        'connection drops, which undoes changes made while it was plugged '
        'in. Eject the reader from calibre before unplugging it, then sync '
        'again.' % (len(receipt['book_ids']), when))
