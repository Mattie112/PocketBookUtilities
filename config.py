"""Configuration storage.

Two layers:

* ``PREFS`` -- a plugin-wide ``JSONConfig`` for things that are true of the
  installation (debug logging, backup retention).
* per-library settings in the library's own database, namespaced, holding one
  entry per device serial. Column mappings belong to a library, not to the
  plugin, because the same reader may be used with more than one library.

Every read goes through :func:`device_settings`, which fills in defaults, so
callers never have to cope with a half-populated dictionary.
"""

import copy

from .common.compat import debug

NAMESPACE = 'pocketbook_utilities'
SETTINGS_KEY = 'settings'
SCHEMA_VERSION = 1

#: Plugin-wide defaults.
PLUGIN_DEFAULTS = {
    'debug': False,
    'backup_keep': 10,
    'schema': SCHEMA_VERSION,
}

#: Per-device defaults, stored inside a library.
DEVICE_DEFAULTS = {
    # field key -> calibre lookup name, filled in by the setup step
    'columns': {},
    # which fields take part in a sync
    'enabled_fields': ['read', 'read_date', 'last_read', 'favourite',
                       'progress', 'position', 'collections'],
    'collections_source': 'column',     # column | tags | series | template
    'collections_template': '',
    'collections_remove_missing': True,
    'skew_seconds': 90,
    'allow_clear': False,
    'always_review': True,
    'trust_floor': 2,                   # MatchConfidence.BASENAME
    'eject_after_write': False,
    'configured': False,
    'schema_fingerprint': '',
    'last_sync': 0,
    # metadata push
    'push_title': False,
    'push_author': True,
    'push_series': True,
    'push_missing_authors_only': False,
}


def plugin_prefs():
    """The plugin-wide JSONConfig, created on first use."""
    from calibre.utils.config import JSONConfig
    prefs = JSONConfig('plugins/PocketBook Utilities')
    for key, value in PLUGIN_DEFAULTS.items():
        prefs.defaults[key] = value
    return prefs


def config_dir():
    """Where we keep the state database and the device backups."""
    import os
    from calibre.constants import config_dir as calibre_config_dir
    path = os.path.join(calibre_config_dir, 'plugins', 'pocketbook_utilities')
    os.makedirs(path, exist_ok=True)
    return path


def state_db_path():
    import os
    return os.path.join(config_dir(), 'state.db')


def backup_dir(serial):
    import os
    path = os.path.join(config_dir(), 'backups', _safe(serial))
    os.makedirs(path, exist_ok=True)
    return path


def _safe(text):
    keep = '-_.'
    return ''.join(c if (c.isalnum() or c in keep) else '_'
                   for c in str(text))[:64] or 'device'


# --------------------------------------------------------------------------
# Per-library settings
# --------------------------------------------------------------------------

def _raw_settings(library_db):
    stored = library_db.prefs.get_namespaced(NAMESPACE, SETTINGS_KEY, None)
    if not stored:
        return {'schema': SCHEMA_VERSION, 'devices': {}}
    return migrate(copy.deepcopy(stored))


def migrate(settings):
    """Bring stored settings up to the current schema."""
    version = settings.get('schema', 0)
    if version > SCHEMA_VERSION:
        debug('settings schema %s is newer than this plugin understands (%s)'
              % (version, SCHEMA_VERSION))
        return settings
    # No migrations yet; future versions chain here.
    settings['schema'] = SCHEMA_VERSION
    settings.setdefault('devices', {})
    return settings


def _write(library_db, settings):
    library_db.prefs.set_namespaced(NAMESPACE, SETTINGS_KEY, settings)


def device_settings(library_db, serial):
    """Settings for one device in one library, with defaults filled in."""
    settings = _raw_settings(library_db)
    stored = settings['devices'].get(str(serial), {})
    merged = copy.deepcopy(DEVICE_DEFAULTS)
    merged.update(stored)
    return merged


def save_device_settings(library_db, serial, values):
    settings = _raw_settings(library_db)
    current = settings['devices'].setdefault(str(serial), {})
    current.update(values)
    _write(library_db, settings)
    debug('saved settings for device %s' % serial)
    return current


def known_devices(library_db):
    return sorted(_raw_settings(library_db)['devices'])


def library_key(library_db):
    """A stable identifier for the library, used by the state store."""
    try:
        return str(library_db.new_api.library_id)
    except Exception as err:
        debug('could not read library id: %s' % err)
        return str(getattr(library_db, 'library_path', 'unknown'))


# --------------------------------------------------------------------------
# Helpers the sync layer needs
# --------------------------------------------------------------------------

def column_for_field(settings, field):
    return settings.get('columns', {}).get(field)


def mapped_fields(settings):
    """Fields that are both enabled and actually mapped to a column."""
    columns = settings.get('columns', {})
    enabled = set(settings.get('enabled_fields', ()))
    return {key for key in enabled if columns.get(key)}


def missing_columns(settings, caps):
    """Enabled fields the device supports but which have no column yet."""
    from .columns import BY_FIELD
    from .sync.fields import BY_KEY

    columns = settings.get('columns', {})
    out = []
    for key in settings.get('enabled_fields', ()):
        spec = BY_KEY.get(key)
        if spec is None or not spec.available(caps):
            continue
        if not columns.get(key) and key in BY_FIELD:
            out.append(key)
    return out
