"""Dialogs shared by the plugin.

The review dialog is the centrepiece: nothing is ever written to calibre or
to the reader without it having been listed here first, with the reason the
plugin picked that direction.
"""

import datetime

from qt.core import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFont, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QMessageBox, QPushButton, QSize, QSizePolicy, Qt, QTableWidget,
    QTableWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget)

from ..sync.model import Side

MAX_VALUE_CHARS = 60


def format_value(value):
    """A short, readable rendering of any value a field can hold."""
    if value is None or value == '':
        return '—'                       # em dash: "nothing"
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, datetime.datetime):
        return value.strftime('%Y-%m-%d %H:%M')
    if isinstance(value, (set, frozenset, list, tuple)):
        items = sorted(str(v) for v in value)
        return ', '.join(items) if items else '—'
    if isinstance(value, float):
        return '%.0f%%' % value
    text = str(value)
    if len(text) > MAX_VALUE_CHARS:
        return text[:MAX_VALUE_CHARS - 1] + '…'
    return text


class ErrorDialog(QDialog):
    """An error the user can actually act on, with the traceback available.

    The plugin this replaces showed every failure as "Unknown error.", which
    made real bugs impossible to report. Here the cause comes first and the
    traceback is one click away.
    """

    def __init__(self, parent, title, message, detail=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)

        label = QLabel(message)
        label.setWordWrap(True)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        layout.addWidget(label)

        self.detail_view = QPlainTextEdit(detail or '')
        self.detail_view.setReadOnly(True)
        self.detail_view.setVisible(False)
        self.detail_view.setMinimumHeight(220)
        layout.addWidget(self.detail_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        if detail:
            self.toggle = buttons.addButton(
                'Show &details', QDialogButtonBox.ButtonRole.ActionRole)
            self.toggle.clicked.connect(self._toggle_details)
            copy_button = buttons.addButton(
                '&Copy', QDialogButtonBox.ButtonRole.ActionRole)
            copy_button.clicked.connect(self._copy)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(QSize(620, 200))

    def _toggle_details(self):
        visible = not self.detail_view.isVisible()
        self.detail_view.setVisible(visible)
        self.toggle.setText('Hide &details' if visible else 'Show &details')
        self.adjustSize()

    def _copy(self):
        QApplication.clipboard().setText(self.detail_view.toPlainText())


def show_error(parent, title, message, detail=None):
    ErrorDialog(parent, title, message, detail).exec()


class TextReportDialog(QDialog):
    """A read-only report: device information, integrity check, book lists."""

    def __init__(self, parent, title, text, monospace=True):
        QDialog.__init__(self, parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        if monospace:
            view.setFont(QFont('Consolas' if hasattr(QFont, 'Monospace')
                               else 'monospace'))
        layout.addWidget(view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy_button = buttons.addButton(
            '&Copy', QDialogButtonBox.ButtonRole.ActionRole)
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(view.toPlainText()))
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(QSize(820, 560))


COLUMNS = ('', 'Book', 'What', 'Direction', 'From', 'To', 'Why')
CHECK_COL, TITLE_COL, FIELD_COL, DIR_COL, OLD_COL, NEW_COL, WHY_COL = range(7)


class ChangeReviewDialog(QDialog):
    """Lists every proposed change and lets the user veto any of them."""

    def __init__(self, parent, plan, field_labels, read_only=False,
                 title='Review changes'):
        QDialog.__init__(self, parent)
        self.plan = plan
        self.read_only = read_only
        self.field_labels = field_labels
        # Sorted by book, then by field, so a book's changes stay together
        # and the order is something the reader can predict. Clicking a
        # column header re-sorts.
        #
        # Fields follow the order of *field_labels* rather than their
        # alphabet, so a book reads status, then dates, then progress and
        # position, then collections. Sorting the labels instead produced
        # "Read date, Favourite, Read status", which is an order nobody
        # thinks in.
        order = {key: index for index, key in enumerate(field_labels)}
        last = len(order)
        self.changes = sorted(
            list(plan.calibre_changes) + list(plan.device_changes),
            key=lambda c: ((c.link.title or '').casefold(),
                           order.get(c.field, last),
                           field_labels.get(c.field, c.field)))

        self.setWindowTitle(title)
        layout = QVBoxLayout(self)

        heading = QLabel(plan.summary())
        heading.setWordWrap(True)
        layout.addWidget(heading)

        sort_note = QLabel('Sorted by book, then by what changes. '
                           'Click a column heading to sort by it.')
        sort_note.setStyleSheet('color: palette(mid);')
        layout.addWidget(sort_note)

        if read_only:
            note = QLabel('This is a dry run. Nothing will be written.')
            note.setStyleSheet('color: palette(mid);')
            layout.addWidget(note)

        self.table = QTableWidget(len(self.changes), len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self._fill()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(TITLE_COL,
                                    QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(WHY_COL, QHeaderView.ResizeMode.Stretch)
        self.table.resizeColumnToContents(CHECK_COL)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table)

        if not read_only:
            buttons_row = QHBoxLayout()
            for label, slot in (('Select &all', self.select_all),
                                ('Select &none', self.select_none),
                                ('Only &confident matches',
                                 self.select_trusted)):
                button = QPushButton(label)
                button.clicked.connect(slot)
                buttons_row.addWidget(button)
            buttons_row.addStretch(1)
            layout.addLayout(buttons_row)

        if read_only:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
        else:
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
                '&Apply selected')
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(QSize(1000, 600))

    def _fill(self):
        for row, change in enumerate(self.changes):
            check = QTableWidgetItem()
            # Remember which change this row is, so the tick boxes still map
            # correctly after the user re-sorts the table.
            check.setData(Qt.ItemDataRole.UserRole, row)
            if self.read_only:
                check.setFlags(Qt.ItemFlag.ItemIsEnabled)
            else:
                check.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                               | Qt.ItemFlag.ItemIsEnabled)
                check.setCheckState(Qt.CheckState.Checked if change.selected
                                    else Qt.CheckState.Unchecked)
            self.table.setItem(row, CHECK_COL, check)

            title = QTableWidgetItem(change.link.title or
                                     str(change.link.calibre_id))
            if not change.link.is_trusted:
                title.setToolTip(
                    'Matched to the device only by %s, so this is unticked '
                    'by default.' % change.link.confidence.label)
            self.table.setItem(row, TITLE_COL, title)

            self.table.setItem(row, FIELD_COL, QTableWidgetItem(
                self.field_labels.get(change.field, change.field)))
            direction = ('device → calibre'
                         if change.direction is Side.CALIBRE
                         else 'calibre → device')
            self.table.setItem(row, DIR_COL, QTableWidgetItem(direction))
            self.table.setItem(
                row, OLD_COL, QTableWidgetItem(format_value(
                    change.display_old)))
            self.table.setItem(
                row, NEW_COL, QTableWidgetItem(format_value(
                    change.display_new)))

            why = QTableWidgetItem(change.reason)
            if 'newer' in change.reason or 'same time' in change.reason:
                why.setToolTip('Both sides changed since the last sync.')
            self.table.setItem(row, WHY_COL, why)

    # -- selection helpers ---------------------------------------------
    def _rows(self):
        """``(check_item, change)`` pairs, correct whatever the sort order."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, CHECK_COL)
            if item is None:
                continue
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is None or not 0 <= int(index) < len(self.changes):
                continue
            yield item, self.changes[int(index)]

    def _set_all(self, predicate):
        for item, change in self._rows():
            item.setCheckState(Qt.CheckState.Checked if predicate(change)
                               else Qt.CheckState.Unchecked)

    def select_all(self):
        self._set_all(lambda change: True)

    def select_none(self):
        self._set_all(lambda change: False)

    def select_trusted(self):
        self._set_all(lambda change: change.link.is_trusted)

    def apply_selection(self):
        """Copy the tick boxes back onto the plan."""
        if self.read_only:
            return self.plan
        for item, change in self._rows():
            change.selected = item.checkState() == Qt.CheckState.Checked
        return self.plan

    def accept(self):
        self.apply_selection()
        QDialog.accept(self)


META_COLUMNS = ('', 'Book', 'Field', 'From', 'To')
M_CHECK, M_TITLE, M_FIELD, M_OLD, M_NEW = range(5)

#: Device column -> what to call it in the list.
FIELD_LABELS_DEVICE = {
    'title': 'Title',
    'sort_title': 'Title sort',
    'author': 'Author',
    'firstauthor': 'Author sort',
    'first_author_letter': 'Author index letter',
    'series': 'Series',
    'numinseries': 'Series number',
}


class MetadataReviewDialog(QDialog):
    """Review the metadata push in a table you can read and resize.

    This used to be calibre's question dialog with the whole preview
    crammed into its details box: fixed size, no wrapping, and all or
    nothing. Same shape as the sync review now, including per-row choice.
    """

    def __init__(self, parent, changes, title='Update device metadata'):
        QDialog.__init__(self, parent)
        self.changes = sorted(changes,
                              key=lambda c: (c.display_title or '').casefold())
        self.rows = []
        for change in self.changes:
            for column in sorted(change.columns):
                self.rows.append((change, column))

        self.setWindowTitle(title)
        layout = QVBoxLayout(self)

        heading = QLabel('%d book(s) and %d value(s) would change on the '
                         'device.' % (len(self.changes), len(self.rows)))
        heading.setWordWrap(True)
        layout.addWidget(heading)

        note = QLabel('Only the device database is changed; the book files '
                      'are left alone. A backup is taken first.')
        note.setStyleSheet('color: palette(mid);')
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(len(self.rows), len(META_COLUMNS), self)
        self.table.setHorizontalHeaderLabels(META_COLUMNS)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self._fill()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(M_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(M_OLD, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(M_NEW, QHeaderView.ResizeMode.Stretch)
        self.table.resizeColumnToContents(M_CHECK)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table)

        buttons_row = QHBoxLayout()
        for label, slot in (('Select &all', self.select_all),
                            ('Select &none', self.select_none)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons_row.addWidget(button)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            '&Write to device')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(QSize(1000, 600))

    def _fill(self):
        for row, (change, column) in enumerate(self.rows):
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, row)
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                           | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, M_CHECK, check)
            self.table.setItem(row, M_TITLE,
                               QTableWidgetItem(change.display_title))
            self.table.setItem(row, M_FIELD, QTableWidgetItem(
                FIELD_LABELS_DEVICE.get(column, column)))
            self.table.setItem(row, M_OLD, QTableWidgetItem(
                format_value(change.old.get(column))))
            self.table.setItem(row, M_NEW, QTableWidgetItem(
                format_value(change.columns.get(column))))

    def _items(self):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, M_CHECK)
            if item is None:
                continue
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is None or not 0 <= int(index) < len(self.rows):
                continue
            yield item, self.rows[int(index)]

    def select_all(self):
        for item, _row in self._items():
            item.setCheckState(Qt.CheckState.Checked)

    def select_none(self):
        for item, _row in self._items():
            item.setCheckState(Qt.CheckState.Unchecked)

    def selected_changes(self):
        """The changes, rebuilt from only the ticked rows."""
        from dataclasses import replace

        keep = {}
        for item, (change, column) in self._items():
            if item.checkState() != Qt.CheckState.Checked:
                continue
            keep.setdefault(id(change), (change, []))[1].append(column)

        out = []
        for change, columns in keep.values():
            if len(columns) == len(change.columns):
                out.append(change)
                continue
            out.append(replace(
                change,
                columns={k: v for k, v in change.columns.items()
                         if k in columns},
                old={k: v for k, v in change.old.items() if k in columns}))
        return out


class SetupWizard(QDialog):
    """Shown once per device: explains what the plugin does, then sets it up."""

    def __init__(self, parent, device, on_setup_columns,
                 needs_toolbar_button=False, column_preview=''):
        QDialog.__init__(self, parent)
        self.device = device
        self.on_setup_columns = on_setup_columns
        self.columns_done = False
        self.needs_toolbar_button = needs_toolbar_button
        self.column_preview = column_preview

        self.setWindowTitle('Set up PocketBook Utilities')
        layout = QVBoxLayout(self)

        intro = QTextBrowser()
        intro.setOpenExternalLinks(False)
        intro.setHtml(
            '<h3>A PocketBook was detected</h3>'
            '<p><b>%s</b><br/>Database: %s (schema version %s)</p>'
            '<p>This plugin can keep your reading status, reading position, '
            'progress, favourites and collections in step between the reader '
            'and calibre.</p>'
            '<p>It needs a few custom columns to put that information in. '
            '<b>Create columns and continue</b> makes all of them in one '
            'go, and reuses any it created before. Your own columns are '
            'left alone.</p>'
            % (device.device_name or 'PocketBook',
               device.db_path, device.caps.db_version))
        intro.setMaximumHeight(200)
        layout.addWidget(intro)

        if column_preview:
            preview = QPlainTextEdit(column_preview)
            preview.setReadOnly(True)
            preview.setMinimumHeight(150)
            font = QFont('Consolas')
            font.setStyleHint(QFont.StyleHint.Monospace)
            preview.setFont(font)
            layout.addWidget(preview)

        self.result_label = QLabel('')
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        self.toolbar_box = QCheckBox(
            'Add a PocketBook Utilities button to the toolbar')
        self.toolbar_box.setChecked(True)
        self.toolbar_box.setToolTip(
            'calibre\'s default toolbar contains built-in actions only, so '
            'no plugin button appears there unless one is added. Without it '
            'the plugin has no menu you can open.')
        self.toolbar_box.setVisible(needs_toolbar_button)
        layout.addWidget(self.toolbar_box)

        self.backup_box = QCheckBox(
            'Back up the device database before anything else')
        self.backup_box.setChecked(True)
        layout.addWidget(self.backup_box)

        self.dry_run_box = QCheckBox(
            'Show me what would change, without writing anything')
        self.dry_run_box.setChecked(True)
        layout.addWidget(self.dry_run_box)

        layout.addStretch(1)

        # The action lives in the button rather than in a separate control
        # above it, so nobody has to read the dialog to work out what OK
        # will do.
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = buttons.addButton(
            'Create columns and continue',
            QDialogButtonBox.ButtonRole.AcceptRole)
        self.skip_button = buttons.addButton(
            'Continue without creating columns',
            QDialogButtonBox.ButtonRole.AcceptRole)
        self.create_button.setDefault(True)
        self.create_button.setToolTip(
            'Creates every custom column the plugin needs, reusing any it '
            'already made before. Your other columns are left alone.')
        self.skip_button.setToolTip(
            'Use this if you want to map the fields onto columns yourself, '
            'on the Columns tab of the plugin settings.')
        self.create_button.clicked.connect(self._create_and_accept)
        self.skip_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(QSize(620, 440))

    def _create_and_accept(self):
        text = self.on_setup_columns()
        self.columns_done = True
        self.result_label.setText(text)
        QMessageBox.information(self, 'Column setup', text)
        self.accept()

    @property
    def wants_toolbar_button(self):
        return (self.needs_toolbar_button
                and self.toolbar_box.isChecked())

    @property
    def wants_backup(self):
        return self.backup_box.isChecked()

    @property
    def wants_dry_run(self):
        return self.dry_run_box.isChecked()
