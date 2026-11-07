"""The toolbar button, its menu, and what each entry does."""

import traceback
from functools import partial

from qt.core import QIcon, QMenu, QPixmap, QToolButton

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.pocketbook_utilities import columns as columns_mod
from calibre_plugins.pocketbook_utilities import config as config_mod
from calibre_plugins.pocketbook_utilities import runner
from calibre_plugins.pocketbook_utilities.common.compat import (
    PluginError, debug, set_debug)
from calibre_plugins.pocketbook_utilities.common.dialogs import (
    ChangeReviewDialog, SetupWizard, TextReportDialog, show_error)
from calibre_plugins.pocketbook_utilities.device import db as dbmod
from calibre_plugins.pocketbook_utilities.device.detect import get_device
from calibre_plugins.pocketbook_utilities.sync.engine import Mode
from calibre_plugins.pocketbook_utilities.sync.fields import BY_KEY, FIELDS

PLUGIN_ICON = 'images/icon.png'

FIELD_LABELS = {spec.key: spec.label for spec in FIELDS}

READ_FIELDS = {'read', 'read_date', 'favourite'}
POSITION_FIELDS = {'position', 'progress', 'last_read'}
COLLECTION_FIELDS = {'collections'}


class PocketBookUtilitiesAction(InterfaceAction):
    name = 'PocketBook Utilities'
    action_spec = ('PocketBook Utilities', None,
                   'Sync reading status, positions and collections with a '
                   'PocketBook reader', None)
    action_type = 'current'
    popup_type = QToolButton.ToolButtonPopupMode.MenuButtonPopup
    dont_add_to = frozenset(['context-menu-device'])

    def genesis(self):
        self.device = None
        self.menu = QMenu(self.gui)
        self.menu.aboutToShow.connect(self.rebuild_menus)
        icon = self._load_icon()
        if icon is not None:
            self.qaction.setIcon(icon)
        self.qaction.setMenu(self.menu)
        self.qaction.triggered.connect(self.sync_now)
        set_debug(bool(config_mod.plugin_prefs().get('debug')))

    def _load_icon(self):
        try:
            data = self.load_resources([PLUGIN_ICON]).get(PLUGIN_ICON)
            if not data:
                return None
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            return QIcon(pixmap)
        except Exception as err:
            debug('could not load the plugin icon: %s' % err)
            return None

    def initialization_complete(self):
        try:
            from calibre.gui2.device import device_signals
            device_signals.device_connection_changed.connect(
                self._device_changed)
            device_signals.device_metadata_available.connect(
                self._device_metadata)
        except Exception as err:
            debug('could not subscribe to device signals: %s' % err)
        self.rebuild_menus()

    def library_changed(self, db):
        self.rebuild_menus()

    # -- device lifecycle ----------------------------------------------
    def _device_changed(self, connected):
        if not connected:
            self.device = None
        self.rebuild_menus()

    def _device_metadata(self):
        self.device = get_device(self.gui)
        self.rebuild_menus()
        if self.device is None:
            return
        self._check_previous_write()
        settings = self.settings()
        if not settings.get('configured'):
            self.run_setup_wizard()

    def settings(self):
        if self.device is None:
            return dict(config_mod.DEVICE_DEFAULTS)
        return config_mod.device_settings(self.gui.current_db,
                                          self.device.serial)

    def save(self, values):
        config_mod.save_device_settings(self.gui.current_db,
                                        self.device.serial, values)

    def _check_previous_write(self):
        try:
            outcome = runner.verify_last_write(self.device)
        except Exception as err:
            debug('could not verify the previous write: %s' % err)
            return
        if outcome is None or outcome[0]:
            return
        error_dialog(self.gui, 'Changes were undone by the reader',
                     outcome[1], show=True)

    # -- menu ----------------------------------------------------------
    def rebuild_menus(self):
        self.menu.clear()
        has_device = self.device is not None
        caps = self.device.caps if has_device else None
        configured = self.settings().get('configured') if has_device else False

        def add(text, slot, enabled=True, why=None, menu=None):
            target = menu if menu is not None else self.menu
            action = target.addAction(text)
            action.triggered.connect(slot)
            action.setEnabled(bool(enabled))
            if not enabled and why:
                action.setToolTip(why)
                action.setStatusTip(why)
            return action

        no_device = 'No PocketBook is connected'
        not_set_up = 'Run "Set up columns" first'
        ready = has_device and configured

        add('&Sync now (review changes)…', self.sync_now,
            enabled=ready,
            why=no_device if not has_device else not_set_up)
        add('Sync &selected books…', self.sync_selected,
            enabled=ready and bool(self._selected_ids()),
            why=(no_device if not has_device else
                 not_set_up if not configured else
                 'Select some books in the library first'))
        self.menu.addSeparator()

        send = self.menu.addMenu('Send to device')
        send.setEnabled(ready)
        _explain(send, ready, no_device if not has_device else not_set_up)
        add('Send &everything (overwrite the device)…',
            partial(self.forced_sync, Mode.FORCE_SEND, None),
            enabled=ready, menu=send)
        add('Send &read status and favourites…',
            partial(self.forced_sync, Mode.FORCE_SEND, READ_FIELDS),
            enabled=ready, menu=send)
        add('Send &collections…',
            partial(self.forced_sync, Mode.FORCE_SEND, COLLECTION_FIELDS),
            enabled=ready and bool(caps and caps.has_shelves), menu=send)
        add('Fix &titles, authors and series on the device…',
            self.push_metadata, enabled=has_device, menu=send)

        load = self.menu.addMenu('Load from device')
        load.setEnabled(ready)
        _explain(load, ready, no_device if not has_device else not_set_up)
        add('Load &everything (overwrite calibre)…',
            partial(self.forced_sync, Mode.FORCE_LOAD, None),
            enabled=ready, menu=load)
        add('Load &read status and favourites…',
            partial(self.forced_sync, Mode.FORCE_LOAD, READ_FIELDS),
            enabled=ready, menu=load)
        add('Load reading &positions and progress…',
            partial(self.forced_sync, Mode.FORCE_LOAD, POSITION_FIELDS),
            enabled=ready, menu=load)
        add('Load &collections…',
            partial(self.forced_sync, Mode.FORCE_LOAD, COLLECTION_FIELDS),
            enabled=ready and bool(caps and caps.has_shelves), menu=load)

        self.menu.addSeparator()
        add('Show device &information…', self.show_device_info,
            enabled=has_device, why=no_device)
        add('Books on the device not in this &library…',
            self.show_device_only, enabled=has_device, why=no_device)
        add('Check the device &database…', self.check_database,
            enabled=has_device, why=no_device)

        self.menu.addSeparator()
        add('&Back up the device database now', self.backup_now,
            enabled=has_device, why=no_device)
        add('&Restore a backup…', self.restore_backup,
            enabled=has_device, why=no_device)
        add('&Verify the last write…', self.verify_write,
            enabled=has_device, why=no_device)

        self.menu.addSeparator()
        add('Set &up columns…', self.setup_columns)
        add('&Customize this plugin…', self.show_configuration)

    # -- helpers -------------------------------------------------------
    def _selected_ids(self):
        try:
            rows = self.gui.library_view.selectionModel().selectedRows()
            return [self.gui.library_view.model().id(row) for row in rows]
        except Exception:
            return []

    def require_device(self):
        if self.device is None:
            self.device = get_device(self.gui)
        if self.device is None:
            raise PluginError(
                'No PocketBook is connected.',
                'Connect the reader in USB storage mode and wait for calibre '
                'to finish reading it.')
        return self.device

    def guarded(self, title, func, *args, **kwargs):
        """Run *func*, turning any failure into a dialog the user can read."""
        try:
            return func(*args, **kwargs)
        except PluginError as err:
            show_error(self.gui, title, err.message, err.detail)
        except Exception as err:
            show_error(self.gui, title,
                       '%s: %s' % (type(err).__name__, err),
                       traceback.format_exc())
        return None

    # -- features ------------------------------------------------------
    def setup_columns(self):
        self.guarded('Could not set up columns', self._setup_columns_inner)

    def _setup_columns_inner(self):
        text = self._create_columns()
        info_dialog(self.gui, 'Column setup', text, show=True)

    def _create_columns(self):
        """Create the columns and wire the mapping into the settings."""
        result = columns_mod.ensure_columns_for_gui(self.gui,
                                                    reuse_existing=True)
        if self.device is not None:
            values = {'columns': result.mapping}
            if not result.failed:
                values['configured'] = True
            self.save(values)
        self.rebuild_menus()

        text = result.describe()
        if result.needs_restart:
            text += ('\n\nRestart calibre to see the new columns in the '
                     'book list.')
        return text

    def run_setup_wizard(self):
        device = self.device
        wizard = SetupWizard(self.gui, device, self._create_columns)
        if not wizard.exec():
            return
        if wizard.wants_backup:
            self.guarded('Backup failed', self.backup_now, quiet=True)
        if wizard.wants_dry_run:
            self.guarded('Dry run failed', self._run_sync, Mode.MERGE,
                         None, True)

    def sync_now(self):
        self.guarded('Sync failed', self._run_sync, Mode.MERGE, None, False)

    def sync_selected(self):
        ids = self._selected_ids()
        if not ids:
            return info_dialog(self.gui, 'Nothing selected',
                               'Select some books in the library first.',
                               show=True)
        self.guarded('Sync failed', self._run_sync, Mode.MERGE, ids, False)

    def forced_sync(self, mode, fields):
        self.guarded('Sync failed', self._run_sync, mode, None, False, fields)

    def _run_sync(self, mode, restrict_to, dry_run, only_fields=None):
        device = self.require_device()
        settings = self.settings()
        if only_fields is not None:
            settings = dict(settings)
            settings['enabled_fields'] = [
                key for key in settings.get('enabled_fields', ())
                if key in only_fields]

        context = runner.SyncContext(self.gui, device, settings)
        try:
            plan = context.read(restrict_to=restrict_to, mode=mode)
            self.device = context.device

            if plan.is_empty:
                return info_dialog(
                    self.gui, 'Nothing to do',
                    'Everything is already in step.\n\n%s' % plan.summary(),
                    show=True)

            always_review = bool(settings.get('always_review', True))
            if dry_run or always_review:
                dialog = ChangeReviewDialog(
                    self.gui, plan, FIELD_LABELS, read_only=dry_run,
                    title=('What would change' if dry_run
                           else 'Review changes'))
                if not dialog.exec() or dry_run:
                    return None

            report = context.apply()
            if settings.get('eject_after_write') and plan.selected(
                    _device_side()):
                self._eject()
            else:
                report += ('\n\nEject the reader from calibre before '
                           'unplugging it, so it keeps these changes.')
            info_dialog(self.gui, 'Sync finished', report, show=True)
        finally:
            context.close()
        return None

    def _eject(self):
        try:
            self.gui.device_manager.umount_device()
        except Exception as err:
            debug('could not eject the device: %s' % err)

    def push_metadata(self):
        self.guarded('Could not update device metadata',
                     self._push_metadata_inner)

    def _push_metadata_inner(self):
        from calibre_plugins.pocketbook_utilities.sync import metadata_push

        device = self.require_device()
        settings = self.settings()
        context = runner.SyncContext(self.gui, device, settings)
        try:
            context.read()
            api = self.gui.current_db.new_api
            ids = [link.calibre_id for link in context.inputs.links]
            meta = {}
            titles = api.all_field_for('title', ids)
            sorts = api.all_field_for('title_sort', ids)
            authors = api.all_field_for('authors', ids)
            author_sorts = api.all_field_for('author_sort', ids)
            series = api.all_field_for('series', ids)
            indices = api.all_field_for('series_index', ids)
            for book_id in ids:
                meta[book_id] = {
                    'title': titles.get(book_id),
                    'title_sort': sorts.get(book_id),
                    'authors': ' & '.join(authors.get(book_id) or ()),
                    'author_sort': author_sorts.get(book_id),
                    'series': series.get(book_id),
                    'series_index': indices.get(book_id),
                }

            changes = metadata_push.build_metadata_changes(
                context.inputs.links, context.index.by_book_id, meta,
                context.caps,
                push_title=bool(settings.get('push_title')),
                push_author=bool(settings.get('push_author', True)),
                push_series=bool(settings.get('push_series', True)),
                missing_authors_only=bool(
                    settings.get('push_missing_authors_only')))

            if not changes:
                return info_dialog(
                    self.gui, 'Nothing to do',
                    'The device already has the right titles and authors.',
                    show=True)

            preview = _metadata_preview(changes)
            if not question_dialog(
                    self.gui, 'Update device metadata',
                    'Update %d book(s) in the device database?'
                    % len(changes), det_msg=preview):
                return None

            runner.make_backup(
                device,
                keep=int(config_mod.plugin_prefs().get('backup_keep', 10)))
            with context.copy.write() as conn:
                written = metadata_push.apply_metadata(conn, changes)
            info_dialog(self.gui, 'Device metadata updated',
                        'Updated %d book(s). Eject the reader before '
                        'unplugging it.' % written, show=True)
        finally:
            context.close()
        return None

    def show_device_info(self):
        self.guarded('Could not read the device', self._show_device_info)

    def _show_device_info(self):
        device = self.require_device()
        copy = dbmod.WorkingCopy(device.db_path)
        try:
            conn = copy.open()
            from calibre_plugins.pocketbook_utilities.device.detect import (
                complete_from_db)
            device = complete_from_db(device, conn)
            self.device = device
            counts = []
            for table in ('books_impl', 'files', 'folders', 'books_settings',
                          'bookshelfs', 'bookshelfs_books'):
                if table not in device.caps.tables:
                    continue
                total = conn.execute(
                    'SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
                counts.append((table, str(total)))
        finally:
            copy.close()

        lines = ['%-22s %s' % (key + ':', value)
                 for key, value in device.summary()]
        lines.append('')
        lines.append('Rows in the device database:')
        lines += ['  %-22s %s' % (name, value) for name, value in counts]
        lines.append('')
        lines.append('What this device supports:')
        for label, available in device.caps.describe():
            lines.append('  [%s] %s' % ('x' if available else ' ', label))
        if not device.caps.is_reference_version:
            lines.append('')
            lines.append('Note: this database reports schema version %s; the '
                         'plugin was developed against 39. Unsupported '
                         'features are disabled above rather than guessed at.'
                         % device.caps.db_version)
        TextReportDialog(self.gui, 'PocketBook device information',
                         '\n'.join(lines)).exec()

    def show_device_only(self):
        self.guarded('Could not read the device', self._show_device_only)

    def _show_device_only(self):
        device = self.require_device()
        context = runner.SyncContext(self.gui, device, self.settings())
        try:
            context.read()
            rows = context.inputs.device_only
            unmatched = context.inputs.unmatched_calibre
        finally:
            context.close()

        lines = ['%d book(s) are in the device database but not matched to '
                 'this library.' % len(rows),
                 'They are never modified by this plugin.', '']
        for row in sorted(rows, key=lambda r: (r.author or '', r.title or '')):
            lines.append('  %-38s %-26s %s'
                         % ((row.title or '')[:38],
                            (row.author or '')[:26], row.relpath))
        if unmatched:
            lines += ['', '%d book(s) in calibre are listed as being on the '
                      'device but were not found in its database:'
                      % len(unmatched)]
            api = self.gui.current_db.new_api
            titles = api.all_field_for('title', unmatched)
            for book_id in unmatched:
                lines.append('  %s' % titles.get(book_id, book_id))
            lines += ['', 'That usually means the reader has not re-scanned '
                      'its library yet.']
        TextReportDialog(self.gui, 'Books on the device', '\n'.join(lines)).exec()

    def check_database(self):
        self.guarded('Could not check the database', self._check_database)

    def _check_database(self):
        device = self.require_device()
        copy = dbmod.WorkingCopy(device.db_path)
        try:
            conn = copy.open()
            from calibre_plugins.pocketbook_utilities.device.schema import (
                introspect)
            report = dbmod.integrity_report(conn, introspect(conn))
        finally:
            copy.close()
        TextReportDialog(self.gui, 'Device database check', report).exec()

    def backup_now(self, quiet=False):
        def run():
            device = self.require_device()
            keep = int(config_mod.plugin_prefs().get('backup_keep', 10))
            path = runner.make_backup(device, keep=keep)
            if not quiet:
                info_dialog(self.gui, 'Backup written',
                            'The device database was copied to:\n\n%s' % path,
                            show=True)
            return path
        return self.guarded('Backup failed', run)

    def restore_backup(self):
        self.guarded('Restore failed', self._restore_backup)

    def _restore_backup(self):
        from qt.core import QInputDialog
        device = self.require_device()
        backups = runner.list_backups(device.serial)
        if not backups:
            return info_dialog(self.gui, 'No backups',
                               'This device has no backups yet.', show=True)

        import time as _time
        labels = ['%s  (%.1f MB, %s)'
                  % (b['name'], b['size'] / 1048576.0,
                     _time.strftime('%Y-%m-%d %H:%M',
                                    _time.localtime(b['mtime'])))
                  for b in reversed(backups)]
        choice, ok = QInputDialog.getItem(
            self.gui, 'Restore a backup',
            'This replaces the reader\'s library database.\n'
            'Reading positions and collections recorded since the backup '
            'will be lost.\n\nChoose a backup:', labels, 0, False)
        if not ok:
            return None
        selected = list(reversed(backups))[labels.index(choice)]

        if not question_dialog(
                self.gui, 'Restore this backup?',
                'Replace the device library database with\n\n%s\n\n'
                'This cannot be undone.' % selected['name']):
            return None

        runner.make_backup(device, keep=99)     # safety net first
        runner.restore_backup(device, selected['path'])
        info_dialog(self.gui, 'Backup restored',
                    'The device database was restored from %s.\n\n'
                    'Eject the reader before unplugging it.'
                    % selected['name'], show=True)
        return None

    def verify_write(self):
        def run():
            device = self.require_device()
            outcome = runner.verify_last_write(device)
            if outcome is None:
                info_dialog(self.gui, 'Nothing to verify',
                            'This plugin has not written to the device yet.',
                            show=True)
                return
            ok, message = outcome
            if ok:
                info_dialog(self.gui, 'Changes are still there', message,
                            show=True)
            else:
                error_dialog(self.gui, 'Changes were undone', message,
                             show=True)
        self.guarded('Could not verify', run)

    def show_configuration(self):
        self.interface_action_base_plugin.do_user_config(self.gui)
        self.rebuild_menus()


def _explain(submenu, enabled, why):
    """Give a disabled submenu a reason instead of echoing its own name."""
    action = submenu.menuAction()
    action.setToolTip('' if enabled else why)
    action.setStatusTip('' if enabled else why)


def _device_side():
    from calibre_plugins.pocketbook_utilities.sync.model import Side
    return Side.DEVICE


def _metadata_preview(changes, limit=40):
    lines = []
    for change in changes[:limit]:
        lines.append(change.display_title)
        for column in sorted(change.columns):
            lines.append('    %-20s %r  ->  %r'
                         % (column, change.old.get(column),
                            change.columns[column]))
    if len(changes) > limit:
        lines.append('... and %d more' % (len(changes) - limit))
    return '\n'.join(lines)
