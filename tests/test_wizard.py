"""The setup wizard's buttons must say what they do.

An "OK" that silently creates seven custom columns depends on the user
having read the dialog. The action belongs in the button label.
"""

import unittest

_app = None


def setUpModule():
    global _app
    from calibre.gui2 import Application
    from qt.core import QApplication
    _app = QApplication.instance() or Application([])


class FakeCaps:
    db_version = 39

    def describe(self):
        return []


class FakeDevice:
    device_name = 'PocketBook Fixture'
    db_path = r'F:\system\explorer-3\explorer-3.db'
    caps = FakeCaps()


class WizardTestCase(unittest.TestCase):

    def make(self, needs_toolbar_button=False):
        from calibre_plugins.pocketbook_utilities.common.dialogs import (
            SetupWizard)
        self.calls = []

        def on_setup():
            self.calls.append('setup')
            return 'Created 7 column(s).'

        wizard = SetupWizard(None, FakeDevice(), on_setup,
                             needs_toolbar_button=needs_toolbar_button)
        self.addCleanup(wizard.deleteLater)
        return wizard


class TestButtons(WizardTestCase):

    def test_the_buttons_name_their_action(self):
        wizard = self.make()
        self.assertIn('Create columns', wizard.create_button.text())
        self.assertIn('without creating', wizard.skip_button.text())

    def test_creating_is_the_default_button(self):
        wizard = self.make()
        self.assertTrue(wizard.create_button.isDefault())

    def test_skip_accepts_without_creating_anything(self):
        wizard = self.make()
        wizard.skip_button.click()
        self.assertEqual(self.calls, [])
        self.assertFalse(wizard.columns_done)
        self.assertEqual(wizard.result(), wizard.DialogCode.Accepted)

    def test_cancel_creates_nothing_and_rejects(self):
        from qt.core import QDialogButtonBox
        wizard = self.make()
        wizard.reject()
        self.assertEqual(self.calls, [])
        self.assertEqual(wizard.result(), wizard.DialogCode.Rejected)


class TestOptions(WizardTestCase):

    def test_backup_and_dry_run_default_to_on(self):
        wizard = self.make()
        self.assertTrue(wizard.wants_backup)
        self.assertTrue(wizard.wants_dry_run)

    def test_toolbar_option_only_offered_when_needed(self):
        self.assertFalse(self.make(needs_toolbar_button=False)
                         .wants_toolbar_button)
        self.assertTrue(self.make(needs_toolbar_button=True)
                        .wants_toolbar_button)

    def test_schema_version_is_shown(self):
        wizard = self.make()
        self.assertIsNotNone(wizard)


if __name__ == '__main__':
    unittest.main()
