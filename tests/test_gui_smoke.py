"""Construct the GUI pieces without showing them.

Catches wrong Qt enum names, missing imports and bad calibre API calls --
the class of mistake that otherwise only shows up as a plugin that refuses
to load. Nothing here calls exec(), so no windows appear.
"""

import unittest
from contextlib import closing
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from base import needs_calibre_gui

needs_calibre_gui()

from calibre_plugins.pocketbook_utilities.sync.model import (
    BookLink, MatchConfidence, ProposedChange, Side, SyncPlan)

_app = None


def setUpModule():
    global _app
    from calibre.gui2 import Application
    from qt.core import QApplication
    _app = QApplication.instance() or Application([])


def make_plan():
    link = BookLink(calibre_id=1, device_book_id=10, lpath='Books/a.epub',
                    device_path='/mnt/ext1/Books/a.epub',
                    confidence=MatchConfidence.EXACT_PATH,
                    title="Harry Potter and the Philosopher's Stone")
    weak = BookLink(calibre_id=2, device_book_id=11, lpath='Books/b.epub',
                    device_path='/mnt/ext1/Books/b.epub',
                    confidence=MatchConfidence.METADATA, title='Weak Match')
    plan = SyncPlan()
    plan.calibre_changes = [
        ProposedChange(link=link, field='read', direction=Side.CALIBRE,
                       old=None, new=True, reason='changed on the device'),
        ProposedChange(link=link, field='progress', direction=Side.CALIBRE,
                       old=None, new=37.21, reason='changed on the device'),
        ProposedChange(link=weak, field='read', direction=Side.CALIBRE,
                       old=None, new=True, reason='only the device has a '
                       'value', selected=False),
    ]
    plan.device_changes = [
        ProposedChange(link=link, field='collections', direction=Side.DEVICE,
                       old=frozenset(), new=frozenset({'Sci-Fi', "O'Brien"}),
                       reason='changed in calibre'),
    ]
    plan.unmatched_calibre = [99]
    return plan


class TestFormatting(unittest.TestCase):

    def test_values_render_readably(self):
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            format_value)
        import datetime
        self.assertEqual(format_value(True), 'Yes')
        self.assertEqual(format_value(False), 'No')
        self.assertEqual(format_value(None), '—')
        self.assertEqual(format_value(''), '—')
        self.assertEqual(format_value(37.21), '37%')
        self.assertEqual(format_value(frozenset({'b', 'a'})), 'a, b')
        self.assertEqual(format_value(frozenset()), '—')
        self.assertEqual(
            format_value(datetime.datetime(2026, 2, 3, 14, 5)),
            '2026-02-03 14:05')
        self.assertTrue(format_value('x' * 200).endswith('…'))


class TestReviewDialog(unittest.TestCase):

    def setUp(self):
        from calibre_plugins.pocketbook_utilities.action import FIELD_LABELS
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            ChangeReviewDialog)
        self.plan = make_plan()
        self.dialog = ChangeReviewDialog(None, self.plan, FIELD_LABELS)
        self.addCleanup(self.dialog.deleteLater)

    def test_every_change_gets_a_row(self):
        self.assertEqual(self.dialog.table.rowCount(), 4)

    def test_weak_matches_start_unticked(self):
        self.dialog.apply_selection()
        selected = [c for c in self.plan.all_changes if c.selected]
        self.assertEqual(len(selected), 3)
        self.assertTrue(all(c.link.is_trusted for c in selected))

    def test_select_none_then_all(self):
        self.dialog.select_none()
        self.dialog.apply_selection()
        self.assertEqual([c for c in self.plan.all_changes if c.selected], [])

        self.dialog.select_all()
        self.dialog.apply_selection()
        self.assertEqual(len([c for c in self.plan.all_changes if c.selected]),
                         4)

    def test_only_confident_matches(self):
        self.dialog.select_all()
        self.dialog.select_trusted()
        self.dialog.apply_selection()
        selected = [c for c in self.plan.all_changes if c.selected]
        self.assertTrue(all(c.link.is_trusted for c in selected))
        self.assertEqual(len(selected), 3)

    def test_read_only_mode_has_no_tick_boxes(self):
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            ChangeReviewDialog)
        from calibre_plugins.pocketbook_utilities.action import FIELD_LABELS
        dialog = ChangeReviewDialog(None, make_plan(), FIELD_LABELS,
                                    read_only=True)
        self.addCleanup(dialog.deleteLater)
        from qt.core import Qt
        item = dialog.table.item(0, 0)
        self.assertFalse(bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable))

    def test_rows_are_sorted_by_book_then_field(self):
        titles = [self.dialog.table.item(row, 1).text()
                  for row in range(self.dialog.table.rowCount())]
        self.assertEqual(titles, sorted(titles, key=str.casefold))

    def test_fields_follow_the_registry_order_not_the_alphabet(self):
        """Read status before Read date, not the other way round."""
        from calibre_plugins.pocketbook_utilities.action import FIELD_LABELS
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            ChangeReviewDialog)
        from calibre_plugins.pocketbook_utilities.sync.model import (
            BookLink, MatchConfidence, ProposedChange, Side, SyncPlan)

        link = BookLink(calibre_id=1, device_book_id=10, lpath='a',
                        device_path='a', title='One Book',
                        confidence=MatchConfidence.EXACT_PATH)
        plan = SyncPlan()
        # Deliberately added in an order that alphabetises differently.
        for field in ('collections', 'read_date', 'favourite', 'read',
                      'position', 'progress', 'last_read'):
            plan.calibre_changes.append(
                ProposedChange(link=link, field=field,
                               direction=Side.CALIBRE, old=None, new='x',
                               reason='test'))

        dialog = ChangeReviewDialog(None, plan, FIELD_LABELS)
        self.addCleanup(dialog.deleteLater)
        shown = [c.field for c in dialog.changes]

        self.assertEqual(shown, [k for k in FIELD_LABELS if k in shown])
        self.assertLess(shown.index('read'), shown.index('read_date'),
                        'read status should come before its date')
        self.assertLess(shown.index('read'), shown.index('favourite'))
        self.assertEqual(shown[-1], 'collections')

    def test_selection_survives_re_sorting(self):
        """Tick boxes must follow their row when the user re-sorts."""
        from qt.core import Qt
        self.dialog.select_all()
        self.dialog.table.sortItems(1, Qt.SortOrder.DescendingOrder)
        self.dialog.select_trusted()
        self.dialog.apply_selection()
        selected = [c for c in self.plan.all_changes if c.selected]
        self.assertEqual(len(selected), 3)
        self.assertTrue(all(c.link.is_trusted for c in selected),
                        'the wrong rows were unticked after sorting')

    def test_direction_is_shown_both_ways(self):
        texts = {self.dialog.table.item(row, 3).text()
                 for row in range(self.dialog.table.rowCount())}
        self.assertIn('device → calibre', texts)
        self.assertIn('calibre → device', texts)


class TestOtherDialogs(unittest.TestCase):

    def test_error_dialog_keeps_the_traceback(self):
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            ErrorDialog)
        dialog = ErrorDialog(None, 'Title', 'Something went wrong',
                             'Traceback (most recent call last): ...')
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.detail_view.isVisible())
        self.assertIn('Traceback', dialog.detail_view.toPlainText())

    def test_text_report_dialog_builds(self):
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            TextReportDialog)
        dialog = TextReportDialog(None, 'Report', 'line one\nline two')
        self.addCleanup(dialog.deleteLater)


class TestModulesImport(unittest.TestCase):
    """Every GUI module must import cleanly."""

    def test_action_and_config_ui_import(self):
        from calibre_plugins.pocketbook_utilities import action, config_ui
        self.assertTrue(hasattr(action, 'PocketBookUtilitiesAction'))
        self.assertTrue(hasattr(config_ui, 'ConfigWidget'))

    def test_action_spec_and_popup_type_are_valid(self):
        from qt.core import QToolButton
        from calibre_plugins.pocketbook_utilities.action import (
            PocketBookUtilitiesAction)
        self.assertEqual(len(PocketBookUtilitiesAction.action_spec), 4)
        self.assertEqual(PocketBookUtilitiesAction.popup_type,
                         QToolButton.ToolButtonPopupMode.MenuButtonPopup)

    def test_every_field_has_a_label_and_a_column(self):
        from calibre_plugins.pocketbook_utilities import columns
        from calibre_plugins.pocketbook_utilities.action import FIELD_LABELS
        from calibre_plugins.pocketbook_utilities.sync.fields import FIELDS
        for spec in FIELDS:
            self.assertIn(spec.key, FIELD_LABELS)
            self.assertIn(spec.key, columns.BY_FIELD,
                          '%s has no column spec' % spec.key)

    def test_plugin_entry_point_matches_the_action_class(self):
        import importlib
        base = importlib.import_module(
            'calibre_plugins.pocketbook_utilities.__init__')
        module_path, _, class_name = \
            base.PocketBookUtilities.actual_plugin.partition(':')
        module = importlib.import_module(module_path)
        self.assertTrue(hasattr(module, class_name))




class TestMetadataReviewDialog(unittest.TestCase):
    """The metadata push used calibre's question dialog with everything
    crammed into its details box: fixed size, unreadable, all or nothing."""

    def changes(self):
        from calibre_plugins.pocketbook_utilities.sync.metadata_push import (
            MetadataChange)
        return [
            MetadataChange(device_book_id=1, calibre_id=11,
                           display_title='Virtual Vandals',
                           columns={'author': 'Tom Clancy',
                                    'firstauthor': 'Clancy, Tom',
                                    'first_author_letter': 'C'},
                           old={'author': 'Clancy, Tom - NFE 01',
                                'firstauthor': '01, Clancy, Tom - NFE',
                                'first_author_letter': '0'}),
            MetadataChange(device_book_id=2, calibre_id=12,
                           display_title='Another Book',
                           columns={'series': 'A Series'},
                           old={'series': None}),
        ]

    def build(self):
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            MetadataReviewDialog)
        dialog = MetadataReviewDialog(None, self.changes())
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_one_row_per_value_the_user_can_choose(self):
        """The index letter rides on the author sort's row, not its own."""
        dialog = self.build()
        self.assertEqual(dialog.table.rowCount(), 3)

    def test_fields_are_named_not_shown_as_columns(self):
        dialog = self.build()
        labels = {dialog.table.item(r, 2).text()
                  for r in range(dialog.table.rowCount())}
        self.assertIn('Author sort', labels)
        self.assertNotIn('Author index letter', labels,
                         'a derived value must not be separately tickable')
        self.assertNotIn('firstauthor', labels)

    def test_a_derived_value_alone_still_gets_a_row(self):
        """Right name, wrong letter: the letter is the whole change."""
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            MetadataReviewDialog)
        from calibre_plugins.pocketbook_utilities.sync.metadata_push import (
            MetadataChange)
        dialog = MetadataReviewDialog(None, [MetadataChange(
            device_book_id=3, calibre_id=13, display_title='Letter Only',
            columns={'first_author_letter': 'C'},
            old={'first_author_letter': '0'})])
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 2).text(),
                         'Author index letter')
        self.assertEqual(dialog.selected_changes()[0].columns,
                         {'first_author_letter': 'C'})

    def test_everything_is_ticked_to_begin_with(self):
        dialog = self.build()
        chosen = dialog.selected_changes()
        self.assertEqual(len(chosen), 2)
        self.assertEqual(sum(len(c.columns) for c in chosen), 4)

    def test_unticking_drops_that_value_and_what_follows_it(self):
        """Author sort and its index letter are written together or not at
        all: one without the other files the book under the wrong letter."""
        from qt.core import Qt
        dialog = self.build()
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 2).text() == 'Author sort':
                dialog.table.item(row, 0).setCheckState(
                    Qt.CheckState.Unchecked)
        chosen = dialog.selected_changes()
        first = [c for c in chosen if c.device_book_id == 1][0]
        self.assertEqual(set(first.columns), {'author'})
        self.assertEqual(set(first.old), {'author'})

    def test_select_none_means_nothing_is_written(self):
        dialog = self.build()
        dialog.select_none()
        self.assertEqual(dialog.selected_changes(), [])

    def test_rows_are_sorted_by_book(self):
        dialog = self.build()
        titles = [dialog.table.item(r, 1).text()
                  for r in range(dialog.table.rowCount())]
        self.assertEqual(titles, sorted(titles, key=str.casefold))


class TestAppliedRowsAreShown(unittest.TestCase):
    """A sync that applies half of itself silently must still account for it.

    The dialog reported "0 change(s) for calibre" while three calibre
    changes were about to be made automatically, because it described only
    the slice it was showing.
    """

    def build(self):
        from calibre_plugins.pocketbook_utilities.action import FIELD_LABELS
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            ChangeReviewDialog)
        from calibre_plugins.pocketbook_utilities.sync.model import (
            BookLink, MatchConfidence, ProposedChange, Side, SyncPlan)

        link = BookLink(calibre_id=1, device_book_id=1, lpath='',
                        device_path='', title='Operation Bounce House',
                        confidence=MatchConfidence.EXACT_PATH)
        plan = SyncPlan()
        plan.device_changes.append(ProposedChange(
            link=link, field='favourite', direction=Side.DEVICE,
            old=False, new=1, reason='changed in calibre'))
        applied = [
            ProposedChange(link=link, field='progress',
                           direction=Side.CALIBRE, old=None, new=43.67,
                           reason='changed on the device'),
            ProposedChange(link=link, field='position',
                           direction=Side.CALIBRE, old=None, new='pbr:/x',
                           reason='changed on the device'),
        ]
        dialog = ChangeReviewDialog(None, plan, FIELD_LABELS,
                                    applied=applied)
        self.addCleanup(dialog.deleteLater)
        return dialog, plan, applied

    def test_applied_changes_get_rows_too(self):
        dialog, plan, applied = self.build()
        self.assertEqual(dialog.table.rowCount(), 3)

    def test_the_heading_counts_the_automatic_ones_as_calibre_changes(self):
        dialog, _plan, _applied = self.build()
        text = dialog._summary()
        self.assertIn('2 for calibre will be applied automatically', text)
        self.assertIn('Awaiting your approval: 0 for calibre, 1 for the '
                      'device', text)

    def test_without_automatic_changes_the_plain_summary_is_used(self):
        from calibre_plugins.pocketbook_utilities.action import FIELD_LABELS
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            ChangeReviewDialog)
        _dialog, plan, _applied = self.build()
        plain = ChangeReviewDialog(None, plan, FIELD_LABELS)
        self.addCleanup(plain.deleteLater)
        self.assertEqual(plain._summary(), plan.summary())

    def test_applied_rows_cannot_be_ticked(self):
        from qt.core import Qt
        dialog, _plan, _applied = self.build()
        checkable = 0
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                checkable += 1
        self.assertEqual(checkable, 1, 'only the real change is approvable')

    def test_applying_selection_ignores_them(self):
        dialog, plan, applied = self.build()
        dialog.apply_selection()
        self.assertTrue(plan.device_changes[0].selected)
        # The automatic ones are applied by the caller, not by this dialog.
        self.assertEqual(len(dialog.changes), 1)

    def test_rows_stay_grouped_by_book(self):
        dialog, _plan, _applied = self.build()
        titles = [dialog.table.item(r, 1).text()
                  for r in range(dialog.table.rowCount())]
        self.assertEqual(len(set(titles)), 1)


class TestCoreActionSafety(unittest.TestCase):

    def test_failed_eject_does_not_claim_safe_to_unplug(self):
        from calibre.gui2.device import DeviceManager
        from calibre_plugins.pocketbook_utilities.action import (
            PocketBookUtilitiesAction)

        ejected = []
        manager = SimpleNamespace(
            is_device_connected=True, is_device_present=True,
            job_manager=SimpleNamespace(has_device_jobs=lambda: True),
            connected_device=SimpleNamespace(eject=lambda: ejected.append(True)))
        manager.umount_device = lambda: DeviceManager.umount_device(manager)
        action = SimpleNamespace(gui=SimpleNamespace(device_manager=manager))
        self.assertFalse(PocketBookUtilitiesAction._eject(action))
        self.assertEqual(ejected, [])

    def test_eject_reports_success_once_calibre_marks_device_gone(self):
        from calibre_plugins.pocketbook_utilities.action import (
            PocketBookUtilitiesAction)

        manager = SimpleNamespace(is_device_present=True)
        manager.umount_device = lambda: setattr(manager, 'is_device_present', False)
        action = SimpleNamespace(gui=SimpleNamespace(device_manager=manager))
        self.assertTrue(PocketBookUtilitiesAction._eject(action))

    def test_idle_action_records_agreement_but_dry_run_does_not(self):
        from calibre_plugins.pocketbook_utilities import action
        from calibre_plugins.pocketbook_utilities.sync.engine import Mode

        fake = SimpleNamespace(
            gui=object(), require_device=lambda: object(),
            _require_live_columns=lambda _settings: None,
            settings=dict, _sync_running=False)
        with patch.object(action.runner, 'SyncContext') as factory, \
                patch.object(action, 'info_dialog'):
            context = factory.return_value
            context.read.return_value = SyncPlan()
            context.review_after_restore = False
            action.PocketBookUtilitiesAction._run_sync(fake, Mode.MERGE, None, True)
            context.remember.assert_not_called()
            action.PocketBookUtilitiesAction._run_sync(fake, Mode.MERGE, None, False)
            context.remember.assert_called_once_with()

    def test_invalid_template_does_not_propose_a_collection_replacement(self):
        from calibre.db.legacy import LibraryDatabase
        from calibre.ebooks.metadata.book.base import Metadata
        from calibre_plugins.pocketbook_utilities import runner
        from calibre_plugins.pocketbook_utilities.device.schema import Capabilities
        from calibre_plugins.pocketbook_utilities.sync.engine import (
            Mode, SyncInputs, build_plan)

        with TemporaryDirectory() as folder, closing(LibraryDatabase(folder)) as db:
            book_id = db.new_api.create_book_entry(Metadata('Book', ['Author']))
            settings = {'collections_source': 'template',
                        'collections_template': '{title'}
            gui = SimpleNamespace(current_db=db)
            errors = {}
            result = runner.calibre_collections(gui, settings, [book_id], errors)
            self.assertEqual(result, {})
            self.assertIn(book_id, errors)
            settings['collections_template'] = '{title}'
            self.assertEqual(runner.calibre_collections(gui, settings, [book_id]),
                             {book_id: frozenset({'Book'})})

        link = BookLink(book_id, 1, 'Book.epub', '/mnt/ext1/Book.epub',
                        MatchConfidence.EXACT_PATH)
        inputs = SyncInputs(
            [link], {}, {}, device_collections={1: frozenset({'Keep'})},
            calibre_collections=result, collection_errors=errors)
        caps = Capabilities(tables=frozenset({'bookshelfs', 'bookshelfs_books'}))
        plan = build_plan(inputs, caps, {'collections'},
                          mode=Mode.FORCE_SEND, allow_clear=True)
        self.assertEqual(plan.device_changes, [])
        self.assertEqual(len(plan.errors), 1)


if __name__ == '__main__':
    unittest.main()
