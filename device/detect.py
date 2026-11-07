"""Recognise a connected PocketBook and describe it.

Detection is by filesystem evidence -- the presence of
``system/explorer-N/explorer-N.db`` under a storage prefix -- and never by
``isinstance`` against a particular calibre driver class. Several drivers can
drive the same reader (calibre's own ``POCKETBOOK626``, or the third-party
"PocketBook Improved"), and which one is installed must not change whether
the plugin works.
"""

import glob
import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from typing import Optional

from ..common.compat import debug
from . import db as dbmod
from .schema import Capabilities, introspect

DRIVEINFO = 'driveinfo.calibre'
#: Storage roots as the device writes them into ``folders.name``.
KNOWN_DEVICE_ROOTS = ('/mnt/ext1', '/mnt/ext2', '/mnt/ext3', '/mnt/secsd')


@dataclass(frozen=True)
class PocketBookDevice:
    main_prefix: str
    db_path: str
    explorer_version: int
    serial: str
    card_prefix: Optional[str] = None
    driver_name: str = ''
    device_name: str = ''
    profile_name: Optional[str] = None
    profile_id: int = 1
    cr3hist_path: Optional[str] = None
    caps: Capabilities = Capabilities()

    @property
    def has_card(self):
        return bool(self.card_prefix)

    @property
    def uses_coolreader(self):
        return bool(self.cr3hist_path)

    def prefix_for(self, location):
        return self.card_prefix if location == 'card' else self.main_prefix

    def summary(self):
        """Human-readable lines for the Device tab and the info dialog."""
        lines = [
            ('Device', self.device_name or 'PocketBook'),
            ('Driver in use', self.driver_name or 'unknown'),
            ('Serial / storage id', self.serial),
            ('Main storage', self.main_prefix),
            ('SD card', self.card_prefix or 'none'),
            ('Database', self.db_path),
            ('Explorer version', str(self.explorer_version)),
            ('Schema version', str(self.caps.db_version)),
            ('Profile', '%s (id %s)' % (self.profile_name or 'default',
                                        self.profile_id)),
            ('CoolReader history', self.cr3hist_path or 'not present'),
        ]
        return lines


def find_database(prefix):
    """``(db_path, explorer_version)`` for *prefix*, or ``None``.

    Prefers the highest ``explorer-N`` present.
    """
    if not prefix:
        return None
    pattern = os.path.join(prefix, 'system', 'explorer-*', 'explorer-*.db')
    best = None
    for path in glob.glob(pattern):
        match = re.search(r'explorer-(\d+)\.db$', os.path.basename(path))
        if not match:
            continue
        version = int(match.group(1))
        if best is None or version > best[1]:
            best = (path, version)
    return best


def read_driveinfo(prefix):
    path = os.path.join(prefix, DRIVEINFO)
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_current_profile_name(prefix):
    """The active profile name from ``system/profiles/.current.lnk``.

    The file holds a path such as ``/mnt/ext1/system/profiles/Matthijs`` and
    ends with a newline. Not stripping that newline is what made the previous
    plugin fail to find the profile row at all.
    """
    path = os.path.join(prefix, 'system', 'profiles', '.current.lnk')
    try:
        with open(path, 'rb') as handle:
            raw = handle.read()
    except Exception:
        return None
    text = raw.decode('utf-8', 'replace').strip().replace('\\', '/')
    if not text:
        return None
    return text.rstrip('/').rsplit('/', 1)[-1] or None


def find_cr3hist(prefix, profile_name):
    """Path of the CoolReader history file, when the reader is actually used."""
    candidates = []
    if profile_name:
        candidates.append(os.path.join(prefix, 'system', 'profiles',
                                       profile_name, 'state', 'cr3', '.cr3hist'))
    candidates.append(os.path.join(prefix, 'system', 'state', 'cr3', '.cr3hist'))
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _serial_for(prefix, driveinfo):
    uuid = driveinfo.get('device_store_uuid')
    if uuid:
        return str(uuid)
    # Fall back to something stable-ish rather than failing outright; the
    # drive letter can change between connections, so this is last resort.
    return 'unknown-' + hashlib.sha1(
        os.path.abspath(prefix).encode('utf-8')).hexdigest()[:12]


def probe_prefix(main_prefix, card_prefix=None, driver_name=''):
    """Build a device description from paths alone. No database access."""
    found = find_database(main_prefix)
    if found is None:
        return None
    db_path, explorer_version = found
    driveinfo = read_driveinfo(main_prefix)
    profile_name = read_current_profile_name(main_prefix)
    return PocketBookDevice(
        main_prefix=main_prefix,
        card_prefix=card_prefix or None,
        db_path=db_path,
        explorer_version=explorer_version,
        serial=_serial_for(main_prefix, driveinfo),
        driver_name=driver_name,
        device_name=driveinfo.get('device_name', '') or '',
        profile_name=profile_name,
        cr3hist_path=find_cr3hist(main_prefix, profile_name),
    )


def resolve_profile_id(conn, caps, profile_name):
    """Which ``profiles.id`` the reading state belongs to.

    Order: the profile named by ``.current.lnk``; else the only profile row
    when there is exactly one; else 1.
    """
    if not caps.has_profiles:
        return 1
    if profile_name:
        row = conn.execute('SELECT id FROM profiles WHERE name = ?',
                           (profile_name,)).fetchone()
        if row is not None:
            return int(row[0])
        debug('profile %r not found in the profiles table' % profile_name)
    rows = conn.execute('SELECT id, name FROM profiles ORDER BY id').fetchall()
    if len(rows) == 1:
        return int(rows[0][0])
    debug('%d profiles and no usable .current.lnk; defaulting to 1' % len(rows))
    return 1


def complete_from_db(device, conn):
    """Attach capabilities and the resolved profile id, read from *conn*."""
    caps = introspect(conn)
    profile_id = resolve_profile_id(conn, caps, device.profile_name)
    name = device.profile_name
    if name is None and caps.has_profiles:
        row = conn.execute('SELECT name FROM profiles WHERE id = ?',
                           (profile_id,)).fetchone()
        if row is not None:
            name = row[0]
    return replace(device, caps=caps, profile_id=profile_id, profile_name=name)


def describe_path(prefix, driver_name='manual'):
    """Full device description for a path, opening a snapshot to read caps.

    Used by the tests and by the "treat this folder as a device" debug
    setting, so every milestone can be exercised against a real dump.
    """
    device = probe_prefix(prefix, driver_name=driver_name)
    if device is None:
        return None
    with dbmod.WorkingCopy(device.db_path) as copy:
        return complete_from_db(device, copy.conn)


def with_capabilities(device):
    """Fill in ``caps`` and the profile by reading the device database.

    Detection itself only looks at the filesystem, which is cheap and works
    no matter which driver calibre picked. Everything downstream needs the
    capability flags, though, so they are read here -- and a database we
    cannot open leaves the flags empty rather than failing detection.
    """
    if device is None:
        return None
    try:
        with dbmod.WorkingCopy(device.db_path) as copy:
            return complete_from_db(device, copy.conn)
    except Exception as err:
        debug('could not read capabilities from %s: %s'
              % (device.db_path, err))
        return device


def get_device(gui, with_caps=True):
    """The connected PocketBook, or ``None``.

    Imports of calibre internals stay inside the function so this module can
    be imported headlessly by the tests.
    """
    manager = getattr(gui, 'device_manager', None)
    if manager is None or not getattr(manager, 'is_device_present', False):
        return None
    driver = manager.connected_device
    if driver is None:
        return None

    main = getattr(driver, '_main_prefix', None)
    card = (getattr(driver, '_card_a_prefix', None)
            or getattr(driver, '_card_b_prefix', None))
    driver_name = '%s (%s)' % (driver.__class__.__name__,
                               getattr(driver, 'gui_name', ''))

    for prefix, other in ((main, card), (card, None)):
        if not prefix:
            continue
        device = probe_prefix(prefix, card_prefix=other,
                              driver_name=driver_name)
        if device is not None:
            debug('detected PocketBook at', prefix)
            return with_capabilities(device) if with_caps else device
    debug('connected device has no system/explorer-N database')
    return None
