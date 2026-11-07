"""The registry of synchronised fields.

The engine is generic over these, so adding a field is one table entry rather
than another branch in a loop.

Canonicalisation is the quiet hero here. The plugin this replaces compared
``str(None)`` against ``"0"``, decided they differed, and rewrote thousands
of calibre books on every single run. Every value from either side is
normalised to one comparable form *before* anything is compared.
"""

import datetime
from dataclasses import dataclass, field as dc_field
from typing import Callable, FrozenSet, Optional

from .model import Side

UTC = datetime.timezone.utc

#: calibre uses this to mean "no date".
UNDEFINED_YEAR = 101


# --------------------------------------------------------------------------
# Canonical forms
# --------------------------------------------------------------------------

def canon_bool(value):
    if value is None or value == '':
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ('', 'none', 'null'):
            return None
        if lowered in ('0', 'false', 'no'):
            return False
        if lowered in ('1', 'true', 'yes'):
            return True
        return None
    return bool(value)


def canon_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def canon_float(value):
    if value is None or value == '':
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def canon_datetime(value):
    """Everything becomes a whole-second UTC unix timestamp, or ``None``."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime.datetime):
        if value.year <= UNDEFINED_YEAR:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return int(value.timestamp())
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


def canon_set(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    items = {str(v).strip() for v in value if str(v).strip()}
    return frozenset(items) or None


def to_datetime(timestamp):
    """A timezone-aware datetime for calibre, from a device timestamp."""
    if not timestamp:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(timestamp), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


# --------------------------------------------------------------------------
# Field definitions
# --------------------------------------------------------------------------

BOTH = frozenset({Side.CALIBRE, Side.DEVICE})
TO_CALIBRE = frozenset({Side.CALIBRE})


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    datatype: str                       # the calibre custom-column datatype
    canonical: Callable
    read_device: Callable               # (DeviceState) -> raw value
    to_calibre: Callable                # canonical value -> calibre value
    to_device: Callable                 # canonical value -> device value
    requires: str                       # Capabilities attribute name
    directions: FrozenSet = dc_field(default_factory=lambda: BOTH)
    device_column: Optional[str] = None       # column in books_settings
    device_ts_column: Optional[str] = None
    ts_attr: Optional[str] = None             # DeviceState attr for conflicts
    trigger_owned: bool = False               # device overwrites the ts itself

    @property
    def syncs_to_device(self):
        return Side.DEVICE in self.directions

    @property
    def syncs_to_calibre(self):
        return Side.CALIBRE in self.directions

    def available(self, caps):
        return bool(getattr(caps, self.requires, False))

    def device_timestamp(self, state):
        if not self.ts_attr:
            return None
        return getattr(state, self.ts_attr, None) or None


def _identity(value):
    return value


FIELDS = (
    FieldSpec(
        key='read',
        label='Read status',
        datatype='bool',
        canonical=canon_bool,
        read_device=lambda s: None if s.completed is None else bool(s.completed),
        to_calibre=_identity,
        to_device=lambda v: None if v is None else int(bool(v)),
        requires='has_reading_state',
        directions=BOTH,
        device_column='completed',
        device_ts_column='completed_ts',
        ts_attr='completed_ts',
        trigger_owned=True,
    ),
    FieldSpec(
        key='read_date',
        label='Read date',
        datatype='datetime',
        canonical=canon_datetime,
        read_device=lambda s: s.completed_ts if s.completed else None,
        to_calibre=to_datetime,
        to_device=_identity,
        requires='has_reading_state',
        directions=TO_CALIBRE,
        ts_attr='completed_ts',
    ),
    FieldSpec(
        key='last_read',
        label='Last opened',
        datatype='datetime',
        canonical=canon_datetime,
        read_device=lambda s: s.opentime,
        to_calibre=to_datetime,
        to_device=_identity,
        requires='has_opentime',
        directions=TO_CALIBRE,
        ts_attr='opentime',
    ),
    FieldSpec(
        key='favourite',
        label='Favourite',
        datatype='bool',
        canonical=canon_bool,
        read_device=lambda s: None if s.favorite is None else bool(s.favorite),
        to_calibre=_identity,
        to_device=lambda v: None if v is None else int(bool(v)),
        requires='has_favorites',
        directions=BOTH,
        device_column='favorite',
        device_ts_column='favorite_ts',
        ts_attr='favorite_ts',
    ),
    FieldSpec(
        key='progress',
        label='Progress',
        datatype='float',
        canonical=canon_float,
        read_device=lambda s: s.progress,
        to_calibre=_identity,
        to_device=_identity,
        requires='has_page_counters',
        directions=TO_CALIBRE,
        ts_attr='position_ts',
    ),
    FieldSpec(
        key='position',
        label='Reading position',
        datatype='text',
        canonical=canon_text,
        read_device=lambda s: s.position,
        to_calibre=_identity,
        to_device=_identity,
        requires='has_position',
        directions=BOTH,
        device_column='position',
        device_ts_column='position_ts',
        ts_attr='position_ts',
    ),
    FieldSpec(
        key='collections',
        label='Collections',
        datatype='text',
        canonical=canon_set,
        read_device=lambda s: None,     # supplied separately by the engine
        to_calibre=lambda v: sorted(v) if v else [],
        to_device=_identity,
        requires='has_shelves',
        directions=BOTH,
        ts_attr=None,
    ),
)

BY_KEY = {spec.key: spec for spec in FIELDS}

#: Fields stored in books_settings that the engine can write generically.
WRITABLE_SETTINGS_FIELDS = tuple(
    spec for spec in FIELDS
    if spec.syncs_to_device and spec.device_column
)


def available_fields(caps, enabled=None):
    """Specs usable against *caps*, optionally filtered by an enabled set."""
    out = []
    for spec in FIELDS:
        if not spec.available(caps):
            continue
        if enabled is not None and spec.key not in enabled:
            continue
        out.append(spec)
    return out


def unavailable_reason(spec, caps):
    if spec.available(caps):
        return None
    return ('this device\'s database has no %s support'
            % spec.label.lower())
