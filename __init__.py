"""PocketBook Utilities -- a calibre plugin for PocketBook e-readers.

Keeps reading status, reading position, progress, favourites and collections
in step between a PocketBook and a calibre library, and can repair the
author/series information the reader sorts by.

This module is imported when calibre starts, before any GUI exists, so it
must not import Qt or anything from ``calibre.gui2``.
"""

from calibre.customize import InterfaceActionBase

#: The version calibre shows, and the version the built zip is named after.
#:
#: Releases do not use this value. The release workflow rewrites this line
#: from the git tag before building, so the published zip reports the tag it
#: was cut from and nothing has to be edited before tagging. What is
#: committed here is only the default for a build from a checkout, so it can
#: trail the latest release without anything being wrong.
#:
#: To bring it up to date: python build.py --set-version 0.0.5
PLUGIN_VERSION = (0, 0, 5)


class PocketBookUtilities(InterfaceActionBase):
    name = 'PocketBook Utilities'
    description = ('Sync reading status, reading positions, progress, '
                   'favourites and collections with a PocketBook reader, '
                   'and fix the author information it sorts by.')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Mattie112'
    version = PLUGIN_VERSION
    minimum_calibre_version = (5, 35, 0)

    actual_plugin = ('calibre_plugins.pocketbook_utilities.action:'
                     'PocketBookUtilitiesAction')

    def is_customizable(self):
        return True

    def config_widget(self):
        if self.actual_plugin_:
            from calibre_plugins.pocketbook_utilities.config_ui import (
                ConfigWidget)
            return ConfigWidget(self.actual_plugin_)
        return None

    def save_settings(self, config_widget):
        config_widget.save_settings()
