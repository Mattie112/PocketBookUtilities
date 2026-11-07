"""The configuration dialog."""

from qt.core import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                     QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                     QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from calibre.gui2 import info_dialog

from calibre_plugins.pocketbook_utilities import columns as columns_mod
from calibre_plugins.pocketbook_utilities import config as config_mod
from calibre_plugins.pocketbook_utilities.common.compat import debug
from calibre_plugins.pocketbook_utilities.sync import collections as collmod
from calibre_plugins.pocketbook_utilities.sync.fields import FIELDS
from calibre_plugins.pocketbook_utilities.sync.model import MatchConfidence

TRUST_CHOICES = (
    (MatchConfidence.EXACT_PATH, 'Only books matched by their exact path'),
    (MatchConfidence.BASENAME_SIZE, 'Also filename + size matches'),
    (MatchConfidence.BASENAME, 'Also filename matches (recommended)'),
    (MatchConfidence.METADATA, 'Also title + author matches'),
)


def _scrollable(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class ColumnCombo(QComboBox):
    """Lets the user point a field at any column of a usable type."""

    def __init__(self, parent, spec, available, selected):
        QComboBox.__init__(self, parent)
        self.addItem('(not synced)', '')
        for lookup in available:
            self.addItem(lookup, lookup)
        if selected:
            index = self.findData(selected)
            if index < 0:
                self.addItem(selected + '  (missing)', selected)
                index = self.findData(selected)
            self.setCurrentIndex(index)

    def value(self):
        return self.currentData() or ''


class ConfigWidget(QWidget):

    def __init__(self, plugin_action):
        QWidget.__init__(self)
        self.plugin_action = plugin_action
        self.gui = plugin_action.gui
        self.device = plugin_action.device
        self.settings = plugin_action.settings()
        self.plugin_prefs = config_mod.plugin_prefs()

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        self.tabs.addTab(_scrollable(self._device_tab()), '&Device')
        self.tabs.addTab(_scrollable(self._columns_tab()), '&Columns')
        self.tabs.addTab(_scrollable(self._sync_tab()), '&Sync')
        self.tabs.addTab(_scrollable(self._collections_tab()),
                         'C&ollections')
        self.tabs.addTab(_scrollable(self._metadata_tab()), '&Metadata')
        self.tabs.addTab(_scrollable(self._advanced_tab()), '&Advanced')

    # -- tabs ----------------------------------------------------------
    def _device_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        if self.device is None:
            layout.addWidget(QLabel(
                'No PocketBook is connected.\n\n'
                'Connect the reader in USB storage mode and reopen this '
                'dialog to see what it supports. Column setup on the next '
                'tab works without a device.'))
            layout.addStretch(1)
            return page

        info = QGroupBox('Connected device')
        form = QFormLayout(info)
        for key, value in self.device.summary():
            label = QLabel(value)
            label.setTextInteractionFlags(label.textInteractionFlags()
                                          | 0x1)   # selectable by mouse
            form.addRow(key + ':', label)
        layout.addWidget(info)

        caps_box = QGroupBox('What this device supports')
        caps_layout = QVBoxLayout(caps_box)
        for label, available in self.device.caps.describe():
            caps_layout.addWidget(
                QLabel(('✓  ' if available else '✕  ') + label))
        layout.addWidget(caps_box)

        backup_box = QGroupBox('Backups')
        backup_form = QFormLayout(backup_box)
        note = QLabel('A backup is taken automatically before every write to '
                      'the device. This cannot be turned off.')
        note.setWordWrap(True)
        backup_form.addRow(note)
        self.backup_keep = QSpinBox()
        self.backup_keep.setRange(1, 200)
        self.backup_keep.setValue(int(self.plugin_prefs.get('backup_keep', 10)))
        backup_form.addRow('Backups to keep:', self.backup_keep)

        buttons = QHBoxLayout()
        for text, slot in (('Back up now', self.plugin_action.backup_now),
                           ('Restore a backup…',
                            self.plugin_action.restore_backup),
                           ('Check the database…',
                            self.plugin_action.check_database),
                           ('Verify the last write…',
                            self.plugin_action.verify_write)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        backup_form.addRow(buttons)
        layout.addWidget(backup_box)

        self.eject_after_write = QCheckBox(
            'Eject the device automatically after writing to it')
        self.eject_after_write.setChecked(
            bool(self.settings.get('eject_after_write')))
        self.eject_after_write.setToolTip(
            'PocketBook can rewrite its database from memory when the USB '
            'connection drops, undoing changes. Ejecting first avoids that.')
        layout.addWidget(self.eject_after_write)

        layout.addStretch(1)
        return page

    def _columns_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(
            'The plugin stores what it reads from the reader in custom '
            'columns. The button below creates every column it needs in one '
            'go, and reuses any suitable column you already have.')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        setup = QPushButton('⚙  Set up columns')
        setup.clicked.connect(self._setup_columns)
        layout.addWidget(setup)

        self.setup_result = QLabel('')
        self.setup_result.setWordWrap(True)
        layout.addWidget(self.setup_result)

        box = QGroupBox('Which column holds what')
        form = QFormLayout(box)
        existing = columns_mod.current_custom_columns(
            self.gui.library_view.model().db)
        mapping = self.settings.get('columns', {})
        caps = self.device.caps if self.device is not None else None

        self.column_combos = {}
        for spec in columns_mod.COLUMN_SPECS:
            usable = ['#' + name for name, meta in sorted(existing.items())
                      if columns_mod.column_is_compatible(spec, meta)]
            combo = ColumnCombo(page, spec, usable, mapping.get(spec.field))
            self.column_combos[spec.field] = combo

            label = spec.heading
            if caps is not None:
                from calibre_plugins.pocketbook_utilities.sync.fields import (
                    BY_KEY)
                field_spec = BY_KEY.get(spec.field)
                if field_spec is not None and not field_spec.available(caps):
                    combo.setEnabled(False)
                    label += '  (not supported by this device)'
            form.addRow(label + ':', combo)
        layout.addWidget(box)
        layout.addStretch(1)
        return page

    def _sync_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        box = QGroupBox('What to keep in step')
        box_layout = QVBoxLayout(box)
        enabled = set(self.settings.get('enabled_fields', ()))
        self.field_boxes = {}
        for spec in FIELDS:
            check = QCheckBox(spec.label)
            check.setChecked(spec.key in enabled)
            if not spec.syncs_to_device:
                check.setToolTip('Only ever read from the device.')
            self.field_boxes[spec.key] = check
            box_layout.addWidget(check)
        layout.addWidget(box)

        merge = QGroupBox('How disagreements are settled')
        form = QFormLayout(merge)
        explain = QLabel(
            'The plugin remembers what each side looked like after the last '
            'sync, so it can tell which side actually changed and copy only '
            'that. If both changed, the more recent one wins and the change '
            'is flagged in the review list.')
        explain.setWordWrap(True)
        form.addRow(explain)

        self.always_review = QCheckBox(
            'Always show me the changes before writing them')
        self.always_review.setChecked(
            bool(self.settings.get('always_review', True)))
        form.addRow(self.always_review)

        self.allow_clear = QCheckBox(
            'Let an empty value overwrite a filled one')
        self.allow_clear.setChecked(bool(self.settings.get('allow_clear')))
        self.allow_clear.setToolTip(
            'Off by default, so a blank column never wipes a reading '
            'position on the device.')
        form.addRow(self.allow_clear)

        self.skew = QSpinBox()
        self.skew.setRange(0, 3600)
        self.skew.setSuffix(' seconds')
        self.skew.setValue(int(self.settings.get('skew_seconds', 90)))
        self.skew.setToolTip('Clock difference tolerated between the reader '
                             'and this computer before one counts as newer.')
        form.addRow('Clock tolerance:', self.skew)

        self.trust = QComboBox()
        for value, label in TRUST_CHOICES:
            self.trust.addItem(label, int(value))
        index = self.trust.findData(int(self.settings.get('trust_floor', 2)))
        self.trust.setCurrentIndex(max(index, 0))
        form.addRow('Pre-tick changes for:', self.trust)
        layout.addWidget(merge)

        layout.addStretch(1)
        return page

    def _collections_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        box = QGroupBox('Where collection names come from')
        form = QFormLayout(box)
        self.collections_source = QComboBox()
        for value, label in collmod.SOURCE_LABELS:
            self.collections_source.addItem(label, value)
        index = self.collections_source.findData(
            self.settings.get('collections_source', collmod.SOURCE_COLUMN))
        self.collections_source.setCurrentIndex(max(index, 0))
        form.addRow('Source:', self.collections_source)

        self.collections_template = QLineEdit(
            self.settings.get('collections_template', ''))
        self.collections_template.setPlaceholderText(
            'e.g. {series} or {#genre}, used only with the Template source')
        form.addRow('Template:', self.collections_template)

        preview = QPushButton('Preview for 10 books')
        preview.clicked.connect(self._preview_collections)
        form.addRow(preview)
        self.collections_preview = QPlainTextEdit()
        self.collections_preview.setReadOnly(True)
        self.collections_preview.setMaximumHeight(180)
        form.addRow(self.collections_preview)
        layout.addWidget(box)

        self.collections_remove = QCheckBox(
            'Remove books from device collections that calibre does not list')
        self.collections_remove.setChecked(
            bool(self.settings.get('collections_remove_missing', True)))
        self.collections_remove.setToolTip(
            'Memberships are flagged as deleted, never erased.')
        layout.addWidget(self.collections_remove)

        layout.addStretch(1)
        return page

    def _metadata_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        explain = QLabel(
            'PocketBook sorts its author list by a field it fills in itself, '
            'often from the filename. On this device it can end up as '
            'something like "01, Clancy, Tom - Net Force Explorers", which '
            'sorts under a digit. These options replace it with calibre\'s '
            'author sort.\n\nThis never runs automatically; it is the "Fix '
            'titles, authors and series" menu entry.')
        explain.setWordWrap(True)
        layout.addWidget(explain)

        box = QGroupBox('What to send')
        box_layout = QVBoxLayout(box)
        self.push_author = QCheckBox('Author and author sort')
        self.push_author.setChecked(bool(self.settings.get('push_author',
                                                           True)))
        self.push_series = QCheckBox('Series name and number')
        self.push_series.setChecked(bool(self.settings.get('push_series',
                                                           True)))
        self.push_title = QCheckBox('Title')
        self.push_title.setChecked(bool(self.settings.get('push_title')))
        self.push_missing_only = QCheckBox(
            'Only books whose author is missing on the device')
        self.push_missing_only.setChecked(
            bool(self.settings.get('push_missing_authors_only')))
        for widget in (self.push_author, self.push_series, self.push_title,
                       self.push_missing_only):
            box_layout.addWidget(widget)
        layout.addWidget(box)
        layout.addStretch(1)
        return page

    def _advanced_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.debug_box = QCheckBox('Write diagnostic messages to the log')
        self.debug_box.setChecked(bool(self.plugin_prefs.get('debug')))
        self.debug_box.setToolTip(
            'Visible when calibre is started with calibre-debug -g.')
        layout.addWidget(self.debug_box)

        reset = QPushButton('Forget what was synced before…')
        reset.setToolTip(
            'The next sync will compare both sides from scratch rather than '
            'against what it remembers. Nothing is deleted.')
        reset.clicked.connect(self._forget_state)
        layout.addWidget(reset)

        copy_button = QPushButton('Copy diagnostics to the clipboard')
        copy_button.clicked.connect(self._copy_diagnostics)
        layout.addWidget(copy_button)

        note = QLabel('Diagnostics contain version and capability '
                      'information only -- no book titles or file paths.')
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addStretch(1)
        return page

    # -- actions -------------------------------------------------------
    def _setup_columns(self):
        text = self.plugin_action._create_columns()
        self.setup_result.setText(text)
        mapping = self.plugin_action.settings().get('columns', {})
        for field, combo in self.column_combos.items():
            lookup = mapping.get(field)
            if not lookup:
                continue
            index = combo.findData(lookup)
            if index < 0:
                combo.addItem(lookup, lookup)
                index = combo.findData(lookup)
            combo.setCurrentIndex(index)

    def _preview_collections(self):
        settings = dict(self.settings)
        settings['collections_source'] = self.collections_source.currentData()
        settings['collections_template'] = self.collections_template.text()
        settings.setdefault('columns', {})
        settings['columns'] = dict(settings['columns'])
        settings['columns']['collections'] = \
            self.column_combos['collections'].value()

        from calibre_plugins.pocketbook_utilities.runner import (
            calibre_collections)
        try:
            ids = list(self.gui.current_db.new_api.all_book_ids())[:10]
            found = calibre_collections(self.gui, settings, ids)
        except Exception as err:
            self.collections_preview.setPlainText('Preview failed: %s' % err)
            return

        titles = self.gui.current_db.new_api.all_field_for('title', ids)
        lines = []
        for book_id in ids:
            names = sorted(found.get(book_id, ()))
            lines.append('%-44s %s' % ((titles.get(book_id) or '')[:44],
                                       ', '.join(names) or '(none)'))
        self.collections_preview.setPlainText('\n'.join(lines))

    def _forget_state(self):
        from calibre_plugins.pocketbook_utilities.sync.state import (
            SyncStateStore)
        if self.device is None:
            return info_dialog(self.gui, 'No device',
                               'Connect the reader first.', show=True)
        library = config_mod.library_key(self.gui.current_db)
        with SyncStateStore(config_mod.state_db_path()) as store:
            store.forget(library, self.device.serial)
        info_dialog(self.gui, 'Done',
                    'The next sync will compare both sides from scratch.',
                    show=True)
        return None

    def _copy_diagnostics(self):
        from qt.core import QApplication
        from calibre.constants import numeric_version

        lines = ['calibre %s' % '.'.join(str(p) for p in numeric_version)]
        if self.device is None:
            lines.append('no device connected')
        else:
            caps = self.device.caps
            lines.append('explorer version: %s' % self.device.explorer_version)
            lines.append('schema version: %s' % caps.db_version)
            lines.append('schema fingerprint: %s' % caps.fingerprint)
            lines.append('driver: %s' % self.device.driver_name)
            lines.append('profiles: %s' % caps.has_profiles)
            for label, available in caps.describe():
                lines.append('  %-32s %s' % (label, available))
        lines.append('enabled fields: %s'
                     % sorted(k for k, b in self.field_boxes.items()
                              if b.isChecked()))
        lines.append('mapped fields: %s'
                     % sorted(k for k, c in self.column_combos.items()
                              if c.value()))
        QApplication.clipboard().setText('\n'.join(lines))
        info_dialog(self.gui, 'Copied',
                    'Diagnostics were copied to the clipboard.', show=True)

    # -- saving --------------------------------------------------------
    def save_settings(self):
        self.plugin_prefs['debug'] = self.debug_box.isChecked()
        if hasattr(self, 'backup_keep'):
            self.plugin_prefs['backup_keep'] = self.backup_keep.value()

        from calibre_plugins.pocketbook_utilities.common.compat import (
            set_debug)
        set_debug(self.debug_box.isChecked())

        if self.device is None:
            debug('no device connected; per-device settings not saved')
            return

        values = {
            'columns': {field: combo.value()
                        for field, combo in self.column_combos.items()
                        if combo.value()},
            'enabled_fields': [key for key, box in self.field_boxes.items()
                               if box.isChecked()],
            'always_review': self.always_review.isChecked(),
            'allow_clear': self.allow_clear.isChecked(),
            'skew_seconds': self.skew.value(),
            'trust_floor': int(self.trust.currentData()),
            'collections_source': self.collections_source.currentData(),
            'collections_template': self.collections_template.text(),
            'collections_remove_missing': self.collections_remove.isChecked(),
            'push_author': self.push_author.isChecked(),
            'push_series': self.push_series.isChecked(),
            'push_title': self.push_title.isChecked(),
            'push_missing_authors_only': self.push_missing_only.isChecked(),
        }
        if hasattr(self, 'eject_after_write'):
            values['eject_after_write'] = self.eject_after_write.isChecked()
        if values['columns']:
            values['configured'] = True
        if self.device.caps is not None:
            values['schema_fingerprint'] = self.device.caps.fingerprint

        config_mod.save_device_settings(self.gui.current_db,
                                        self.device.serial, values)
        self.plugin_action.rebuild_menus()
