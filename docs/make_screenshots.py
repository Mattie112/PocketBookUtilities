"""Render the plugin's dialogs to PNGs for the wiki.

    & "C:\\Program Files\\Calibre2\\calibre-debug.exe" -e docs\\make_screenshots.py

Builds the real dialog classes with made-up books and grabs each one. The
widgets are laid out off-screen, so nothing appears on the desktop and no
reader has to be plugged in.

Only the plugin's own dialogs can be made this way. Anything that is calibre
showing its own window -- the library view with the PB columns in it, the
toolbar menu dropped down -- has to be photographed from a running calibre.

The books are invented. Screenshots of a real library put its contents in a
public repository, and these pages only need something plausible on screen.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, 'screenshots')

sys.path.insert(0, os.path.join(ROOT, 'tests'))
from run_tests import install_package_alias      # noqa: E402

install_package_alias()

from calibre_plugins.pocketbook_utilities.sync.model import (   # noqa: E402
    BookLink, MatchConfidence, ProposedChange, Side, SyncPlan)
from calibre_plugins.pocketbook_utilities.sync.fields import (  # noqa: E402
    FIELDS)

FIELD_LABELS = {spec.key: spec.label for spec in FIELDS}

#: (calibre id, device id, filename, title, how it was matched)
BOOKS = (
    (1, 101, 'Dungeon Crawler Carl.epub',
     'Dungeon Crawler Carl', MatchConfidence.EXACT_PATH),
    (2, 102, "Carl's Doomsday Scenario.epub",
     "Carl's Doomsday Scenario", MatchConfidence.EXACT_PATH),
    (3, 103, "The Dungeon Anarchist's Cookbook.epub",
     "The Dungeon Anarchist's Cookbook", MatchConfidence.EXACT_PATH),
    (4, 104, 'The Gate of the Feral Gods.epub',
     'The Gate of the Feral Gods', MatchConfidence.BASENAME),
    (5, 105, "The Butcher's Masquerade.epub",
     "The Butcher's Masquerade", MatchConfidence.METADATA),
)


class DocsCaps:
    """What an InkPad 3 Color on schema 39 actually reports."""
    db_version = 39
    fingerprint = 'd41d8cd98f00'

    def describe(self):
        return [('Reading status (completed)', True),
                ('Favourites', True),
                ('Reading position', True),
                ('Progress (page counters)', True),
                ('Last opened time', True),
                ('Collections (shelves)', True),
                ('Collection UUIDs', True),
                ('Series on device', True),
                ('User profiles', True)]

    def __getattr__(self, name):
        if name.startswith('has_'):
            return True
        raise AttributeError(name)


class DocsDevice:
    """A plausible reader, so the pages do not show "PocketBook Fixture"."""
    serial = '8f2a1c04-5db7-4e19-9a3f-6c0b2d7e8441'
    device_name = 'PocketBook InkPad 3 Color'
    driver_name = 'POCKETBOOK626 (PocketBook)'
    main_prefix = 'E:\\'
    card_prefix = None
    db_path = r'E:\system\explorer-3\explorer-3.db'
    explorer_version = 3
    profile_id = 1
    profile_name = 'default'
    cr3hist_path = None
    caps = DocsCaps()

    def summary(self):
        return [('Device', self.device_name),
                ('Driver in use', self.driver_name),
                ('Serial / storage id', self.serial),
                ('Main storage', self.main_prefix),
                ('SD card', 'none'),
                ('Database', self.db_path),
                ('Explorer version', str(self.explorer_version)),
                ('Schema version', str(self.caps.db_version)),
                ('Profile', '%s (id %s)' % (self.profile_name,
                                            self.profile_id)),
                ('CoolReader history', 'not present')]


def link(index):
    book_id, device_id, filename, title, confidence = BOOKS[index]
    return BookLink(calibre_id=book_id, device_book_id=device_id,
                    lpath='Books/' + filename,
                    device_path='/mnt/ext1/Books/' + filename,
                    confidence=confidence, title=title)


def change(index, field, direction, old, new, reason, selected=True,
           display_old=None, display_new=None):
    return ProposedChange(link=link(index), field=field, direction=direction,
                          old=old, new=new, reason=reason, selected=selected,
                          display_old=display_old, display_new=display_new)


def make_plan():
    """A sync with something to say in both directions."""
    plan = SyncPlan()
    plan.calibre_changes = [
        change(0, 'read', Side.CALIBRE, None, True,
               'changed on the device'),
        change(0, 'read_date', Side.CALIBRE, None, '14 Nov 2026',
               'changed on the device'),
        change(1, 'progress', Side.CALIBRE, 12.0, 64.0,
               'changed on the device'),
        change(1, 'position', Side.CALIBRE, None,
               'pbr:/webkit?##epubcfi(/6/184!/4/2/2/6/1)',
               'changed on the device'),
        change(4, 'read', Side.CALIBRE, None, True,
               'only the device has a value', selected=False),
    ]
    plan.device_changes = [
        change(2, 'favourite', Side.DEVICE, None, 1,
               'changed in calibre', display_old='No', display_new='Yes'),
        change(3, 'collections', Side.DEVICE, None, ['LitRPG'],
               'changed in calibre',
               display_old='', display_new='LitRPG'),
    ]
    plan.unmatched_calibre = [7, 9]
    plan.device_only = []
    return plan


def applied_changes():
    """Rows a connect-time sync already wrote, shown for completeness."""
    return [
        change(2, 'last_read', Side.CALIBRE, None, '19 Nov 2026 21:40',
               'changed on the device'),
        change(3, 'progress', Side.CALIBRE, 0.0, 8.0,
               'changed on the device'),
    ]


def grab(widget, name, size=None):
    """Lay *widget* out off-screen and save a PNG of it."""
    from qt.core import QApplication, QSize, Qt

    if size is not None:
        widget.resize(QSize(*size))
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()
    QApplication.processEvents()
    QApplication.processEvents()

    path = os.path.join(OUT, name)
    if not widget.grab().save(path, 'PNG'):
        raise SystemExit('could not write %s' % path)
    widget.hide()
    print('%-22s %6.1f KB' % (name, os.path.getsize(path) / 1024.0))


def shoot_review(app):
    from calibre_plugins.pocketbook_utilities.common.dialogs import (
        ChangeReviewDialog)

    dialog = ChangeReviewDialog(None, make_plan(), FIELD_LABELS,
                                applied=applied_changes())
    grab(dialog, 'review.png', (1000, 450))
    dialog.deleteLater()


def shoot_wizard(app):
    from calibre_plugins.pocketbook_utilities.common.dialogs import (
        SetupWizard)


    preview = '\n'.join([
        'These columns will be created:',
        '',
        '  #pb_read          PB Read            Yes/No',
        '  #pb_read_date     PB Read date       Date',
        '  #pb_last_read     PB Last opened     Date',
        '  #pb_favourite     PB Favourite       Yes/No',
        '  #pb_progress      PB Progress        Float',
        '  #pb_position      PB Position        Text',
        '  #pb_collections   PB Collections     Text, comma separated',
    ])
    wizard = SetupWizard(None, DocsDevice(), lambda: '7 columns created',
                         needs_toolbar_button=True, column_preview=preview)
    grab(wizard, 'wizard.png', (640, 516))
    wizard.deleteLater()


def shoot_settings(app):
    """The settings dialog needs a library to read its column list from."""
    import shutil
    import tempfile

    from calibre.db.legacy import LibraryDatabase
    from qt.core import QWidget

    from calibre_plugins.pocketbook_utilities import config
    from calibre_plugins.pocketbook_utilities.config_ui import ConfigWidget


    path = tempfile.mkdtemp(prefix='pbutil-shots-')
    db = LibraryDatabase(path)
    try:
        model = type('Model', (), {})()
        model.db = db
        view = type('View', (), {})()
        view.model = lambda: model

        gui = QWidget()
        gui.library_view = view
        gui.current_db = db

        action = type('A', (), {})()
        action.gui = gui
        action.device = DocsDevice()
        action.settings = lambda: dict(config.DEVICE_DEFAULTS)
        action.rebuild_menus = lambda: None
        for name in ('backup_now', 'restore_backup', 'check_database',
                     'verify_write'):
            setattr(action, name, lambda: None)
        action._create_columns = lambda: 'created'

        widget = ConfigWidget(action)
        # The Device tab is the tallest, because it lists every capability
        # the firmware was found to have; a shared height scrollbars it.
        for index, name, size in ((0, 'settings-device.png', (820, 770)),
                                  (2, 'settings-sync.png', (820, 530))):
            widget.tabs.setCurrentIndex(index)
            grab(widget, name, size)
        widget.deleteLater()
        gui.deleteLater()
    finally:
        try:
            db.close()
        except Exception as err:
            print('could not close the scratch library: %s' % err)
        shutil.rmtree(path, ignore_errors=True)


def main():
    from calibre.gui2 import Application
    from qt.core import QApplication

    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or Application([])

    shoot_review(app)
    shoot_wizard(app)
    shoot_settings(app)
    print('\nwritten to %s' % OUT)


if __name__ == '__main__':
    main()
