"""PocketBook Utilities -- a calibre plugin for PocketBook e-readers.

Keeps reading status, reading position, progress, favourites and collections
in step between a PocketBook and a calibre library, and can repair the
author/series information the reader sorts by.

This module is imported when calibre starts, before any GUI exists, so it
must not import Qt or anything from ``calibre.gui2``.
"""

from calibre.customize import InterfaceActionBase

PLUGIN_VERSION = (0, 0, 2)


class PocketBookUtilities(InterfaceActionBase):
    name = 'PocketBook Utilities'
    description = ('Sync reading status, reading positions, progress, '
                   'favourites and collections with a PocketBook reader, '
                   'and fix the author information it sorts by.')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Matthijs'
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
