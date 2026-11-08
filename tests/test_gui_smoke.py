"""Construct the GUI pieces without showing them.

Catches wrong Qt enum names, missing imports and bad calibre API calls --
the class of mistake that otherwise only shows up as a plugin that refuses
to load. Nothing here calls exec(), so no windows appear.
"""

import unittest

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


if __name__ == '__main__':
    unittest.main()
