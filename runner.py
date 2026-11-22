"""Orchestration: read the device, build a plan, apply what the user kept.

Kept apart from ``action.py`` so the menu code stays about menus.
"""

import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import replace
from datetime import datetime

from .common.compat import PluginError, debug, now_ts
from .config import (backup_dir, collections_in_column, column_for_field,
                     device_settings, library_key, mapped_fields,
                     plugin_prefs, state_db_path)
from .device import db as dbmod
from .device import receipt, schema
from .device.detect import complete_from_db, get_device, read_current_profile_name
from .sync import collections as collmod
from .sync import engine, identity, metadata_push
from .sync.engine import Mode, SyncInputs
from .sync.fields import BY_KEY, canon_set
from .sync.model import MatchConfidence, Side
from .sync.state import SyncStateStore


# --------------------------------------------------------------------------
# The calibre side
# --------------------------------------------------------------------------

def _calibre_device_files(gui):
    """The mounted files calibre has associated with a library book."""
    found = []
    views = (('memory_view', 'main'), ('card_a_view', 'card_a'),
             ('card_b_view', 'card_b'))
    for name, location in views:
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
            found.append({
                'id': int(calibre_id),
                'lpath': lpath,
                'size': getattr(book, 'size', None),
                'location': location,
            })

    return found


def collect_calibre_books(gui):
    """Collect calibre's device-view entries with their library metadata."""
    found = _calibre_device_files(gui)
    if not found:
        raise PluginError(
            'calibre does not list any books on this device yet.',
            'Open the device view once so calibre reads the reader\'s book '
            'list, then try again. If the device was just connected, give it '
            'a moment to finish scanning.')

    api = gui.current_db.new_api
    ids = {book['id'] for book in found}
    titles = api.all_field_for('title', ids)
    authors = api.all_field_for('authors', ids)
    for book in found:
        book['title'] = titles.get(book['id'])
        book['authors'] = ' & '.join(authors.get(book['id']) or ())
    debug('calibre reports %d book(s) on the device' % len(found))
    return found


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


def read_calibre_values(gui, settings, book_ids, field_keys, strict=False):
    """``{calibre_id: {field_key: value}}`` plus the last-modified map."""
    api = gui.current_db.new_api
    values = {book_id: {} for book_id in book_ids}
    for key in field_keys:
        column = column_for_field(settings, key)
        column_values = _read_source(api, column, book_ids, strict)
        for book_id, value in column_values.items():
            values[book_id][key] = value
    modified = api.all_field_for('last_modified', book_ids)
    return values, modified


def read_calibre_metadata(gui, book_ids):
    api = gui.current_db.new_api
    # calibre exposes title sort as 'sort' through the cache.
    fields = {name: api.all_field_for(name, book_ids) for name in
              ('title', 'sort', 'authors', 'author_sort', 'series', 'series_index')}
    return {
        book_id: {
            'title': fields['title'].get(book_id),
            'title_sort': fields['sort'].get(book_id),
            'authors': ' & '.join(fields['authors'].get(book_id) or ()),
            'author_sort': fields['author_sort'].get(book_id),
            'series': fields['series'].get(book_id),
            'series_index': fields['series_index'].get(book_id),
        }
        for book_id in book_ids
    }


def describe_collection_errors(errors):
    lines = ['%s: %s' % item for item in errors[:10]]
    if len(errors) > len(lines):
        lines.append('… and %d more' % (len(errors) - len(lines)))
    return '\n'.join(lines)


def _read_source(api, column, book_ids, strict):
    if strict and column:
        return api.all_field_for(column, book_ids)
    return read_column(api, column, book_ids)


def calibre_collections(gui, settings, book_ids, errors=None, strict=False):
    """Collection names per book, from whichever source is configured."""
    api = gui.current_db.new_api
    source = settings.get('collections_source', collmod.SOURCE_COLUMN)
    out = {}

    if source == collmod.SOURCE_TAGS:
        raw = _read_source(api, 'tags', book_ids, strict)
    elif source == collmod.SOURCE_SERIES:
        raw = _read_source(api, 'series', book_ids, strict)
    elif source == collmod.SOURCE_TEMPLATE:
        return _collections_from_template(gui, settings, book_ids, errors)
    else:
        column = column_for_field(settings, 'collections')
        raw = _read_source(api, column, book_ids, strict)

    for book_id, value in raw.items():
        names = collmod.names_from_value(value)
        if names:
            out[book_id] = names
    return out


def _collections_from_template(gui, settings, book_ids, errors):
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
            text = formatter.unsafe_format(template, metadata, metadata)
        except Exception as err:
            if errors is not None:
                errors[book_id] = str(err)
            debug('template failed for book %s: %s' % (book_id, err))
            continue
        names = collmod.names_from_value(text)
        if names:
            out[book_id] = names
    return out


# --------------------------------------------------------------------------
# Backups
# --------------------------------------------------------------------------

def _validate_backup(conn, device):
    problems = [row[0] for row in conn.execute('PRAGMA quick_check')]
    if problems != ['ok']:
        raise PluginError('The backup failed SQLite integrity checks.',
                          '\n'.join(problems[:10]))
    caps = schema.introspect(conn)
    if not (caps.has('books_impl', 'id', 'title', 'author')
            and caps.has('files', 'book_id', 'folder_id', 'storageid', 'filename')
            and caps.has('folders', 'id', 'storageid', 'name')):
        raise PluginError('The backup does not contain a PocketBook library.')
    if device.caps.db_version and caps.db_version != device.caps.db_version:
        raise PluginError(
            'The backup uses a different PocketBook database version.',
            'Backup version %d, reader version %d.'
            % (caps.db_version, device.caps.db_version))
    if device.caps.fingerprint and caps.fingerprint != device.caps.fingerprint:
        raise PluginError('The backup and reader use different database schemas.')


def make_backup(device, keep=10):
    """Validate a consistent snapshot before making it available to restore."""
    target_dir = backup_dir(device.serial)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    with tempfile.TemporaryDirectory(dir=target_dir, prefix='.snapshot-') as temp:
        pending = os.path.join(temp, 'snapshot.db')
        dbmod.snapshot(device.db_path, pending)
        with closing(sqlite3.connect(dbmod.ro_uri(pending), uri=True)) as conn:
            _validate_backup(conn, device)

        unique = os.path.basename(temp)[len('.snapshot-'):]
        name = 'explorer-%d-%s-%s.db' % (device.explorer_version, stamp, unique)
        path = os.path.join(target_dir, name)
        os.replace(pending, path)
        if device.cr3hist_path and os.path.exists(device.cr3hist_path):
            shutil.copyfile(device.cr3hist_path,
                            os.path.join(target_dir, 'cr3hist-%s-%s.xml'
                                         % (stamp, unique)))

    prune_backups(target_dir, keep)
    debug('backup written to %s' % path)
    return path


def list_backups(serial):
    target = backup_dir(serial)
    names = [n for n in os.listdir(target) if n.endswith('.db')]
    out = []
    for name in sorted(names, key=lambda n: _backup_order(target, n)):
        full = os.path.join(target, name)
        stat = os.stat(full)
        out.append({'path': full, 'name': name, 'size': stat.st_size,
                    'mtime': stat.st_mtime})
    return out


def _backup_order(target_dir, name):
    return os.stat(os.path.join(target_dir, name)).st_mtime_ns, name


def prune_backups(target_dir, keep):
    """Keep the newest *keep* backups, and always the very first one."""
    names = sorted((n for n in os.listdir(target_dir) if n.endswith('.db')),
                   key=lambda n: _backup_order(target_dir, n))
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
    """Validate the selected snapshot, then restore it via SQLite."""
    if not os.path.isfile(backup_path):
        raise PluginError('That backup file no longer exists.', backup_path)
    with closing(sqlite3.connect(dbmod.ro_uri(backup_path), uri=True)) as source:
        source.execute('BEGIN')
        _validate_backup(source, device)
        # Invalidate prior merge history before replacing the reader. This
        # also pauses unattended sync if the process stops during restore.
        with SyncStateStore(state_db_path()) as store:
            store.reset_after_restore(device.serial)
        with closing(sqlite3.connect(
                dbmod.file_uri(device.db_path, 'rw'), uri=True,
                timeout=dbmod.WRITE_TIMEOUT)) as target:
            source.backup(target)
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
        self.calibre_books = []
        self.plan = None
        self.library_db = gui.current_db
        self.library = library_key(self.library_db)
        self.review_after_restore = False
        self.receipt_conflicts = {}

    def read(self, restrict_to=None, mode=Mode.MERGE):
        self.copy = dbmod.WorkingCopy(self.device.db_path)
        conn = self.copy.open()
        self.device = complete_from_db(self.device, conn)
        self.caps = self.device.caps

        prefixes = self.device.storage_prefixes or {
            'main': self.device.main_prefix, 'card_a': self.device.card_prefix}
        self.index = identity.load_device_index(conn, self.caps, prefixes)
        self.states = identity.load_reading_state(conn, self.caps,
                                                  self.device.profile_id)
        self.shelves, self.memberships = identity.load_shelves(conn, self.caps)

        self.calibre_books = collect_calibre_books(self.gui)
        links, unmatched, device_only = identity.build_links(
            self.index, self.calibre_books)
        if restrict_to is not None:
            wanted = set(restrict_to)
            links = [link for link in links if link.calibre_id in wanted]
            unmatched = [book_id for book_id in unmatched if book_id in wanted]

        fields = mapped_fields(self.settings)
        with SyncStateStore(state_db_path()) as store:
            prior = store.load(self.library, self.device.serial)
            stored = store.get_receipt(self.device.serial) or {}
            self.review_after_restore = store.review_after_restore(
                self.library, self.device.serial)
        self.receipt_conflicts = _receipt_conflicts(conn, stored)

        book_ids = [link.calibre_id for link in links]
        values, modified = read_calibre_values(
            self.gui, self.settings, book_ids, fields)
        collection_errors = {}
        wanted_collections = (calibre_collections(self.gui, self.settings,
                                                  book_ids, collection_errors)
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
            collection_errors=collection_errors,
        )

        calibre_read_only = {}
        if not collections_in_column(self.settings):
            calibre_read_only['collections'] = (
                'collection names come from calibre\'s %s here, which this '
                'plugin reads but never changes; switch the source to the '
                'collections column to load collections from the reader'
                % self.settings.get('collections_source'))

        self.plan = engine.build_plan(
            self.inputs, self.caps, enabled_fields=fields, mode=mode,
            calibre_read_only=calibre_read_only,
            skew=int(self.settings.get('skew_seconds', 90)),
            allow_clear=bool(self.settings.get('allow_clear', False)),
            trust_floor=MatchConfidence(
                int(self.settings.get('trust_floor', 2))))
        for change in self.plan.all_changes:
            conflict = self._receipt_conflict(change)
            if conflict is not None:
                item, found = conflict
                expected = item.get('value', item.get('names'))
                change.reason = ('Reader differs from the last write '
                                 '(expected %r, found %r)' % (expected, found))
                change.requires_review = True
                change.selected = False
            if self.review_after_restore:
                change.requires_review = True
                change.selected = False
        return self.plan

    def close(self):
        if self.copy is not None:
            self.copy.close()
            self.copy = None

    # -- applying ------------------------------------------------------
    def finish_restore_review(self):
        if self.review_after_restore:
            with SyncStateStore(state_db_path()) as store:
                store.finish_restore_review(self.library, self.device.serial)

    def _receipt_conflict(self, change):
        profile_id = None if change.field == 'collections' else self.device.profile_id
        key = (profile_id, change.link.device_book_id, change.field)
        return self.receipt_conflicts.get(key)

    def require_current_device(self):
        current = get_device(self.gui, with_caps=False)
        if (current is None or current.serial != self.device.serial
                or current.db_path != self.device.db_path
                or current.storage_prefixes != self.device.storage_prefixes
                or self.gui.current_db is not self.library_db):
            raise PluginError('The reader or calibre library changed during review.',
                              'Run the operation again to review current values.')

    def validate_device(self, conn, book_ids):
        current = complete_from_db(
            replace(self.device, profile_name=read_current_profile_name(
                self.device.main_prefix)), conn)
        if (current.profile_id != self.device.profile_id
                or current.caps.fingerprint != self.caps.fingerprint
                or current.caps.db_version != self.caps.db_version):
            raise PluginError('The reader profile or database changed during review.',
                              'Run the operation again to review current values.')
        expected = identity.book_file_identities(self.copy.conn, self.caps, book_ids)
        found = identity.book_file_identities(conn, current.caps, book_ids)
        if set(found) != book_ids or found != expected:
            raise PluginError('A matched reader book changed during review.',
                              'Run the operation again to review current matches.')

    def _validate_device_values(self, conn, changes):
        self.validate_device(conn, {c.link.device_book_id for c in changes})
        states = identity.load_reading_state(conn, self.caps, self.device.profile_id)
        shelves, memberships = identity.load_shelves(conn, self.caps)
        live_collections = collmod.device_collections_by_book(shelves, memberships)
        for change in changes:
            spec = BY_KEY[change.field]
            book_id = change.link.device_book_id
            if change.field == 'collections':
                before = self.inputs.device_collections.get(book_id)
                actual = live_collections.get(book_id)
            else:
                before = spec.read_device(self.inputs.device_state(change.link))
                actual = spec.read_device(identity.state_for(states, change.link))
            if spec.canonical(before) != spec.canonical(actual):
                self._stale_value(change)
        return shelves, memberships

    def validate_calibre_matches(self, book_ids):
        def signatures(books):
            return {(book['id'], book['location'], book['lpath'], book['size'])
                    for book in books if book['id'] in book_ids}

        current = _calibre_device_files(self.gui)
        if signatures(current) != signatures(self.calibre_books):
            raise PluginError('A matched calibre book moved during review.',
                              'Run the operation again to review current matches.')

    def _validate_calibre_values(self, changes):
        book_ids = {change.link.calibre_id for change in changes}
        self.validate_calibre_matches(book_ids)
        api = self.library_db.new_api
        if book_ids.difference(api.all_book_ids()):
            raise PluginError('A calibre book was removed during review.',
                              'Run the operation again to review current matches.')
        fields = {change.field for change in changes} - {'collections'}
        values, _modified = read_calibre_values(
            self.gui, self.settings, book_ids, fields, strict=True)
        collection_ids = {change.link.calibre_id for change in changes
                          if change.field == 'collections'}
        errors = {}
        collections = (calibre_collections(self.gui, self.settings,
                                           collection_ids, errors, strict=True)
                       if collection_ids else {})
        for change in changes:
            spec = BY_KEY[change.field]
            book_id = change.link.calibre_id
            if change.field == 'collections':
                before = self.inputs.calibre_collections.get(book_id)
                actual = collections.get(book_id)
            else:
                before = self.inputs.calibre_values.get(book_id, {}).get(spec.key)
                actual = values[book_id].get(spec.key)
            failed = change.field == 'collections' and book_id in errors
            if failed or spec.canonical(before) != spec.canonical(actual):
                self._stale_value(change)

    @staticmethod
    def _stale_value(change):
        raise PluginError(
            'A reading field or collection changed during review.',
            '%s (%s). Run sync again to review current values.'
            % (change.link.title or change.link.calibre_id,
               BY_KEY[change.field].label))

    def apply(self):
        """Revalidate and write the selected changes."""
        changes = [change for change in self.plan.all_changes if change.selected]
        if not changes:
            return 0, 0
        self.require_current_device()
        self._validate_calibre_values(changes)
        device_written = self._apply_device(changes)
        if not device_written:
            with dbmod.WorkingCopy(self.device.db_path) as fresh:
                self._validate_device_values(fresh.conn, changes)
        calibre_written = self._apply_calibre()
        self.remember()
        return calibre_written, device_written

    def _apply_calibre(self):
        changes = self.plan.selected(Side.CALIBRE)
        if not changes:
            return 0
        self._validate_calibre_values(changes)
        api = self.library_db.new_api
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

    def _apply_device(self, changes):
        writes = self.plan.selected(Side.DEVICE)
        if not writes:
            return 0

        keep = int(plugin_prefs().get('backup_keep', 10))
        make_backup(self.device, keep=keep)

        stamp = now_ts()
        self.require_current_device()
        with self.copy.write() as conn:
            self._validate_calibre_values(writes)
            shelves, memberships = self._validate_device_values(conn, changes)
            engine.apply_to_device(conn, self.plan, self.device.profile_id,
                                   timestamp=stamp,
                                   has_profiles=self.caps.has_profiles)
            if self.caps.has_shelves:
                collmod.apply_collections_to_device(
                    conn, self.plan, shelves, memberships, self.caps,
                    remove_missing=bool(self.settings.get(
                        'collections_remove_missing', True)),
                    timestamp=stamp)
            self.inputs.device_states = identity.load_reading_state(
                conn, self.caps, self.device.profile_id)
            shelves, memberships = identity.load_shelves(conn, self.caps)
            self.inputs.device_collections = collmod.device_collections_by_book(
                shelves, memberships)
        self._write_receipt()
        return len({change.link.device_book_id for change in writes})

    def _resolved_receipts(self, side):
        resolved = set()
        for change in self.plan.selected(side):
            conflict = self._receipt_conflict(change)
            if conflict is not None:
                resolved.add(receipt.key(conflict[0]))
        return resolved

    def _write_receipt(self):
        checks = receipt.build(
            self.plan, self.device.profile_id, BY_KEY.get,
            remove_missing=(self.caps.has_shelf_is_deleted and bool(
                self.settings.get('collections_remove_missing', True))),
            protect_positions=True)
        if not checks:
            return
        with SyncStateStore(state_db_path()) as store:
            previous = store.get_receipt(self.device.serial) or {}
            store.set_receipt(self.device.serial, {
                'version': 3,
                'expectations': checks,
                'pending': receipt.merge_pending(
                    _pending_receipt(previous), checks,
                    self._resolved_receipts(Side.DEVICE)),
                'when': now_ts(),
            })

    def remember(self):
        """Record selected changes and freshly observed agreement."""
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
            if any(change.selected for change in self.plan.all_changes):
                store.set_last_sync(self.library, self.device.serial, now_ts())
            resolved = self._resolved_receipts(Side.CALIBRE)
            if resolved:
                stored = store.get_receipt(self.device.serial) or {}
                stored['pending'] = receipt.merge_pending(
                    _pending_receipt(stored), (), resolved)
                store.set_receipt(self.device.serial, stored)


def split_for_connect(plan, mode):
    """Split a plan into what runs silently and what must be shown.

    A change is applied without being seen only when the chosen mode covers
    its direction *and* the book was matched confidently. A book recognised
    only by its title and author should never have a reading position
    written to it unwatched: that is how the wrong book gets one.
    """
    from .config import applies_without_asking

    silent, needs_review = [], []
    for change in plan.all_changes:
        automatic = (applies_without_asking(
            mode, change.direction is Side.DEVICE)
            and change.link.is_trusted and change.selected
            and not change.requires_review)
        (silent if automatic else needs_review).append(change)
    debug('on connect: %d silent, %d to review'
          % (len(silent), len(needs_review)))
    return silent, needs_review


def has_selected_changes(plan):
    """Is there anything ticked to write, on either side?"""
    return bool(plan.selected(Side.CALIBRE) or plan.selected(Side.DEVICE))


def sub_plan(plan, changes):
    """A plan holding only *changes*, for showing part of a larger one."""
    from .sync.model import SyncPlan

    wanted = {id(c) for c in changes}
    out = SyncPlan()
    out.calibre_changes = [c for c in plan.calibre_changes
                           if id(c) in wanted]
    out.device_changes = [c for c in plan.device_changes if id(c) in wanted]
    out.skipped = list(plan.skipped)
    out.errors = list(plan.errors)
    out.unmatched_calibre = list(plan.unmatched_calibre)
    out.device_only = list(plan.device_only)
    out.stats = dict(plan.stats)
    return out


def completion_message(calibre_written, device_written, ejected=False):
    """What to tell the user after a sync.

    The advice about ejecting belongs only to a run that actually wrote to
    the reader. A sync that only filled in calibre columns has nothing on
    the device to lose, and saying otherwise is both noise and wrong.
    """
    lines = []
    if calibre_written:
        lines.append('Updated %d value(s) in calibre.' % calibre_written)
    if device_written:
        lines.append('Updated %d book(s) on the device.' % device_written)
    if not lines:
        return 'Nothing was written.'

    if device_written:
        lines.append('')
        if ejected:
            lines.append('Calibre has marked the reader as ejected. Wait '
                         'for your reader or operating system to confirm '
                         'it is safe to unplug.')
        else:
            lines.append('Eject the reader from calibre before unplugging '
                         'it, so it keeps these changes.')
    return '\n'.join(lines)


def _pending_receipt(stored):
    return stored.get('pending', stored.get('expectations') or ())


def _receipt_conflicts(conn, stored):
    failures, unchecked = receipt.check(conn, _pending_receipt(stored), for_reconnect=True)
    return {receipt.key(item): (item, found)
            for item, found in failures + [(item, None) for item in unchecked]}


def acknowledge_receipt(device):
    """Note that a failed receipt has been shown to the user."""
    with SyncStateStore(state_db_path()) as store:
        stored = store.get_receipt(device.serial)
        if stored:
            stored['reported'] = True
            store.set_receipt(device.serial, stored)


def verify_last_write(device, unattended=False):
    """Did the reader keep the changes we made last time?

    PocketBook can rewrite its database from memory when the USB connection
    drops, silently undoing anything written. Returns ``(ok, message)`` or
    ``None`` when there is nothing to check. *unattended* is the check made
    on connecting, which stays quiet about a failure already reported.
    """
    with SyncStateStore(state_db_path()) as store:
        stored = store.get_receipt(device.serial)
        if not stored:
            return None
        expectations = (_pending_receipt(stored) if unattended
                        else stored.get('expectations'))
        if not expectations:
            return None

        with dbmod.WorkingCopy(device.db_path) as copy:
            failures, unchecked = receipt.check(
                copy.conn, expectations, for_reconnect=unattended)
        if unattended:
            stored['pending'] = [item for item, _found in failures] + unchecked
            store.set_receipt(device.serial, stored)
            if stored.get('reported'):
                return None

    when = time.strftime('%Y-%m-%d %H:%M',
                         time.localtime(stored.get('when', 0)))
    if failures:
        return False, receipt.describe(failures, when)
    if unchecked:
        # Not a revert, but not a clean bill of health either.
        return True, ('%d of the %d change(s) written on %s are still on '
                      'the device. The other %d could not be checked on '
                      'this device.'
                      % (len(expectations) - len(unchecked),
                         len(expectations), when, len(unchecked)))
    return True, ('All %d change(s) written on %s are still on the '
                  'device.' % (len(expectations), when))
