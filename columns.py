"""The custom columns the plugin needs, and one-click creation of them.

Creating these by hand is the step most likely to go wrong for a user, so
the plugin does it: one button, every column, correct datatypes, and the
names it actually ended up with written back into the configuration.

A note on how, because the obvious route is a trap. calibre offers
``CreateNewCustomColumn.create_column()``, but that helper is *interactive*:
it opens an application-modal dialog per column and does not even prefill
the lookup name, so seven columns would mean seven dialogs and seven chances
to typo. Instead we call ``db.create_custom_column()`` -- precisely what
calibre's own dialog calls once the user presses OK -- and do the validation
the dialog would have done ourselves. One click, seven columns, no typing.
"""

import re
from dataclasses import dataclass, field as dc_field
from typing import Dict, Optional, Tuple

#: Datatypes calibre accepts for a custom column.
VALID_DATATYPES = frozenset({
    'bool', 'comments', 'composite', 'datetime', 'enumeration', 'float',
    'int', 'rating', 'series', 'text',
})

#: Only these may be multi-valued.
MULTIPLE_CAPABLE = frozenset({'text'})

#: calibre requires lookup names to be lowercase word characters.
LABEL_RE = re.compile(r'^\w*$', re.UNICODE)


@dataclass(frozen=True)
class ColumnSpec:
    field: str              # key in the field registry
    lookup: str             # default lookup name, including the '#'
    heading: str
    datatype: str
    is_multiple: bool = False
    description: str = ''
    display: Dict = dc_field(default_factory=dict)
    #: Datatypes an existing column may have and still be usable here.
    compatible: Tuple[str, ...] = ()

    @property
    def accepts(self):
        return (self.datatype,) + tuple(self.compatible)


COLUMN_SPECS = (
    ColumnSpec(
        field='read', lookup='#pb_read', heading='PB Read', datatype='bool',
        description='Marked as finished on the PocketBook.',
        display={'bools_show_text': False, 'bools_show_icons': True},
    ),
    ColumnSpec(
        field='read_date', lookup='#pb_read_date', heading='PB Read date',
        datatype='datetime',
        description='When the PocketBook marked the book finished.',
        display={'date_format': 'dd MMM yyyy'},
    ),
    ColumnSpec(
        field='last_read', lookup='#pb_last_read', heading='PB Last opened',
        datatype='datetime',
        description='When the book was last opened on the PocketBook.',
        display={'date_format': 'dd MMM yyyy hh:mm'},
    ),
    ColumnSpec(
        field='favourite', lookup='#pb_favourite', heading='PB Favourite',
        datatype='bool',
        description='Marked as a favourite on the PocketBook.',
        display={'bools_show_text': False, 'bools_show_icons': True},
    ),
    ColumnSpec(
        field='progress', lookup='#pb_progress', heading='PB Progress',
        datatype='float',
        description='Percentage read, from the device page counters.',
        display={'number_format': '{:.0f}%'},
        compatible=('int',),
    ),
    ColumnSpec(
        field='position', lookup='#pb_position', heading='PB Position',
        datatype='text',
        description='Reading position on the PocketBook (an EPUB CFI).',
        display={'use_decorations': False},
        compatible=('comments',),
    ),
    ColumnSpec(
        field='collections', lookup='#pb_collections',
        heading='PB Collections', datatype='text', is_multiple=True,
        description='Collections this book belongs to on the PocketBook.',
        display={'is_names': False},
    ),
)

BY_FIELD = {spec.field: spec for spec in COLUMN_SPECS}


def column_is_compatible(spec, metadata):
    """Can an existing column described by *metadata* hold this field?"""
    if metadata is None:
        return False
    if metadata.get('datatype') not in spec.accepts:
        return False
    return bool(metadata.get('is_multiple')) == spec.is_multiple


def find_reusable(spec, current_columns):
    """An existing column that already fits, preferring the default name.

    Lets the user keep a column they already have -- a plain ``#read``
    boolean, say -- instead of ending up with a near-duplicate.
    """
    exact = current_columns.get(spec.lookup.lstrip('#'))
    if column_is_compatible(spec, exact):
        return spec.lookup
    for name, meta in sorted(current_columns.items()):
        if column_is_compatible(spec, meta):
            return '#' + name.lstrip('#')
    return None


def valid_label(label):
    """calibre's rules for a lookup name, minus the leading '#'."""
    label = (label or '').lstrip('#')
    if not label:
        return False
    if not LABEL_RE.match(label):
        return False
    if label[0].isdigit() or label.startswith('_'):
        return False
    return label == label.lower()


def unused_label(base, taken):
    """``pb_read`` -> ``pb_read`` or, when that exists, ``pb_read_1``."""
    base = base.lstrip('#')
    if base not in taken:
        return base
    for index in range(1, 1000):
        candidate = '%s_%d' % (base, index)
        if candidate not in taken:
            return candidate
    raise ValueError('could not find an unused lookup name for %r' % base)


def unused_heading(base, taken):
    if base not in taken:
        return base
    for index in range(1, 1000):
        candidate = '%s %d' % (base, index)
        if candidate not in taken:
            return candidate
    raise ValueError('could not find an unused heading for %r' % base)


def validate(spec):
    """What calibre's own dialog would have checked before accepting."""
    if not valid_label(spec.lookup):
        return ('%r is not a usable lookup name' % spec.lookup)
    if spec.datatype not in VALID_DATATYPES:
        return ('%r is not a calibre column type' % spec.datatype)
    if spec.is_multiple and spec.datatype not in MULTIPLE_CAPABLE:
        return ('a %s column cannot hold multiple values' % spec.datatype)
    if not isinstance(spec.display, dict):
        return 'the display parameter must be a dictionary'
    return None


@dataclass
class SetupResult:
    created: Dict[str, str] = dc_field(default_factory=dict)
    reused: Dict[str, str] = dc_field(default_factory=dict)
    failed: Dict[str, str] = dc_field(default_factory=dict)
    needs_restart: bool = False

    @property
    def mapping(self):
        combined = dict(self.reused)
        combined.update(self.created)
        return combined

    def describe(self):
        lines = []
        if self.created:
            lines.append('Created %d column(s):' % len(self.created))
            for fieldname, lookup in sorted(self.created.items()):
                lines.append('    %s  ->  %s' % (fieldname, lookup))
        if self.reused:
            lines.append('Reused %d existing column(s):' % len(self.reused))
            for fieldname, lookup in sorted(self.reused.items()):
                lines.append('    %s  ->  %s' % (fieldname, lookup))
        if self.failed:
            lines.append('Could not set up %d column(s):' % len(self.failed))
            for fieldname, why in sorted(self.failed.items()):
                lines.append('    %s: %s' % (fieldname, why))
        if not lines:
            lines.append('Everything was already set up; nothing to do.')
        return '\n'.join(lines)


CUSTOM_COLUMNS_SQL = (
    'SELECT label, name, datatype, is_multiple, display FROM custom_columns')


def current_custom_columns(db):
    """``{lookup_name_without_hash: metadata}`` for the library behind *db*.

    Read from the ``custom_columns`` table rather than from
    ``field_metadata``: calibre does not refresh its in-memory field metadata
    until it restarts, so immediately after creating a column the metadata
    still reports it missing. Trusting that would make a second setup run try
    to create everything again.
    """
    try:
        rows = db.new_api.backend.conn.execute(CUSTOM_COLUMNS_SQL).fetchall()
    except Exception as err:
        from .common.compat import debug
        debug('falling back to field_metadata for custom columns: %s' % err)
        return {key.lstrip('#'): value for key, value
                in db.field_metadata.custom_field_metadata().items()}

    out = {}
    for label, name, datatype, is_multiple, display in rows:
        out[label] = {
            'label': label,
            'name': name,
            'datatype': datatype,
            'is_multiple': bool(is_multiple),
            'display': display,
        }
    return out


def ensure_columns(db, specs=None, reuse_existing=True, only_fields=None):
    """Create every missing column in one pass.

    *db* is a calibre library database (``gui.library_view.model().db``).
    Returns a :class:`SetupResult` whose ``mapping`` is field key -> the
    lookup name that exists now, so a column that had to be created under a
    suffixed name is still wired up correctly.
    """
    specs = specs or COLUMN_SPECS
    result = SetupResult()
    current = current_custom_columns(db)
    headings = {meta.get('name') for meta in current.values()}

    for spec in specs:
        if only_fields is not None and spec.field not in only_fields:
            continue

        if reuse_existing:
            existing = find_reusable(spec, current)
            if existing:
                result.reused[spec.field] = existing
                continue

        problem = validate(spec)
        if problem:
            result.failed[spec.field] = problem
            continue

        label = unused_label(spec.lookup, current)
        heading = unused_heading(spec.heading, headings)
        display = dict(spec.display)
        display.setdefault('description', spec.description)

        try:
            db.create_custom_column(label=label, name=heading,
                                    datatype=spec.datatype,
                                    is_multiple=spec.is_multiple,
                                    display=display)
        except Exception as err:
            result.failed[spec.field] = '%s: %s' % (type(err).__name__, err)
            continue

        result.created[spec.field] = '#' + label
        result.needs_restart = True
        current = current_custom_columns(db)
        headings.add(heading)

    return result


def ensure_columns_for_gui(gui, **kwargs):
    """``ensure_columns`` for the live GUI, flagging the restart calibre needs."""
    db = gui.library_view.model().db
    result = ensure_columns(db, **kwargs)
    if result.needs_restart:
        gui.must_restart_before_config = True
    return result
