"""Small compatibility helpers shared by the whole plugin.

This module must stay free of Qt imports: it is imported by the headless
``device`` and ``sync`` packages, which are unit-tested without a GUI.
"""

import os
import sys
import time

PLUGIN_NAME = 'PocketBook Utilities'
LOG_PREFIX = 'PocketBookUtilities:'

#: Set by the config layer; when true every debug() call is printed.
DEBUG = bool(os.environ.get('POCKETBOOK_UTILITIES_DEBUG'))


def set_debug(value):
    global DEBUG
    DEBUG = bool(value)


#: Set when writing to stdout fails, so we stop trying on every call.
_LOGGING_BROKEN = False


def debug(*args):
    """Print a diagnostic line when debugging is on.

    calibre captures stdout of the GUI process into its own log, so this is
    visible with ``calibre-debug -g``. A console that cannot be written to
    (a detached GUI process on Windows) disables logging rather than raising
    out of whatever called us.
    """
    global _LOGGING_BROKEN
    if not DEBUG or _LOGGING_BROKEN:
        return
    try:
        print(LOG_PREFIX, *args)
        sys.stdout.flush()
    except Exception:
        _LOGGING_BROKEN = True


def now_ts():
    """Current time as the integer unix timestamp PocketBook stores."""
    return int(time.time())


class PluginError(Exception):
    """Base class for errors we raise ourselves.

    Carries a plain-English explanation that the error dialog shows above the
    traceback, so the user never sees a bare "Unknown error".
    """

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class DeviceNotSupported(PluginError):
    pass


class SchemaUnsupported(PluginError):
    pass
