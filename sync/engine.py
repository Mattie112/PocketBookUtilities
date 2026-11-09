"""Build and apply a sync plan.

The engine never writes anything by itself: :func:`build_plan` produces a
list of proposed changes that the review dialog shows and the user filters,
and only then do :func:`apply_to_calibre` / :func:`apply_to_device` run.

Direction is decided by a three-way merge. Knowing the value each side had at
the last successful sync tells us *which* side moved, so:

* nothing moved            -> no change is proposed at all
* one side moved           -> that value is copied to the other
* both moved               -> the newer timestamp wins, flagged as a conflict

That first case is what keeps an idle run silent and stops the library's
``last_modified`` churning.
"""

import time
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional

from ..common.compat import debug
from .fields import (FIELDS, BY_KEY, FieldSpec, available_fields, canon_datetime)
from .model import BookLink, DeviceState, ProposedChange, Side, SyncPlan
from .state import UNKNOWN

#: Seconds of clock drift tolerated before calling one side newer.
DEFAULT_SKEW = 90

SKIP = 'skip'


class Mode(Enum):
    MERGE = 'merge'
    FORCE_SEND = 'force_send'
    FORCE_LOAD = 'force_load'


@dataclass
class SyncInputs:
    links: List[BookLink]
    device_states: Dict[int, DeviceState]
    calibre_values: Dict[int, Dict[str, Any]]
    calibre_modified: Dict[int, Any] = dc_field(default_factory=dict)
    device_collections: Dict[int, FrozenSet[str]] = dc_field(default_factory=dict)
    calibre_collections: Dict[int, FrozenSet[str]] = dc_field(default_factory=dict)
    prior: Dict[Any, Any] = dc_field(default_factory=dict)
    unmatched_calibre: List[int] = dc_field(default_factory=list)
    device_only: List[Any] = dc_field(default_factory=list)

    def device_state(self, link):
        found = self.device_states.get(link.device_book_id)
        if found is not None:
            return found
        return DeviceState(book_id=link.device_book_id, exists=False)


def _describe_ts(timestamp):
    if not timestamp:
        return 'no date'
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(int(timestamp)))


def decide(spec, dev_c, cal_c, prior_dev, prior_cal, dev_ts, cal_ts,
           mode=Mode.MERGE, skew=DEFAULT_SKEW, allow_clear=False):
    """Which side to write, or ``(SKIP, reason)``, or ``None`` for no change.

    Whatever route the decision takes, a side is never written an empty
    value over a filled one unless *allow_clear* says so. That guard used to
    sit only on the forced paths, so an ordinary merge would happily carry a
    cleared calibre column onto the device and wipe a reading position --
    exactly what the option exists to prevent.
    """
    outcome = _direction(spec, dev_c, cal_c, prior_dev, prior_cal,
                         dev_ts, cal_ts, mode, skew)
    if outcome is None or outcome[0] is SKIP:
        return outcome

    side = outcome[0]
    source = cal_c if side is Side.DEVICE else dev_c
    if source is None and not allow_clear:
        return SKIP, ('would clear the value on the %s; turn on "let an '
                      'empty value overwrite a filled one" if that is what '
                      'you want' % side.label)
    return outcome


def _direction(spec, dev_c, cal_c, prior_dev, prior_cal, dev_ts, cal_ts,
               mode, skew):
    """Which side the values alone point at, before the clearing guard."""
    if dev_c == cal_c:
        return None

    if mode is Mode.FORCE_SEND:
        return Side.DEVICE, 'forced send to device'
    if mode is Mode.FORCE_LOAD:
        return Side.CALIBRE, 'forced load from device'

    dev_changed = (prior_dev is UNKNOWN
                   or spec.canonical(prior_dev) != dev_c)
    cal_changed = (prior_cal is UNKNOWN
                   or spec.canonical(prior_cal) != cal_c)

    if dev_changed and not cal_changed:
        return Side.CALIBRE, 'changed on the device'
    if cal_changed and not dev_changed:
        return Side.DEVICE, 'changed in calibre'

    # Either both moved, or we have never synced this field before.
    if dev_c is None:
        return Side.DEVICE, 'only calibre has a value'
    if cal_c is None:
        return Side.CALIBRE, 'only the device has a value'

    if dev_ts and cal_ts:
        if dev_ts > cal_ts + skew:
            return Side.CALIBRE, ('device is newer (%s vs %s)'
                                  % (_describe_ts(dev_ts), _describe_ts(cal_ts)))
        if cal_ts > dev_ts + skew:
            return Side.DEVICE, ('calibre is newer (%s vs %s)'
                                 % (_describe_ts(cal_ts), _describe_ts(dev_ts)))
        return SKIP, 'both sides changed at about the same time'
    if dev_ts:
        return Side.CALIBRE, 'the device has a date, calibre does not'
    if cal_ts:
        return Side.DEVICE, 'calibre has a date, the device does not'
    return SKIP, 'both sides changed and neither has a date'


def build_plan(inputs, caps, enabled_fields=None, mode=Mode.MERGE,
               skew=DEFAULT_SKEW, allow_clear=False, trust_floor=None):
    """Compare every linked book field by field."""
    plan = SyncPlan()
    plan.unmatched_calibre = list(inputs.unmatched_calibre)
    plan.device_only = list(inputs.device_only)

    specs = available_fields(caps, enabled_fields)
    enabled_keys = {s.key for s in specs}
    for spec in FIELDS:
        if spec.key not in enabled_keys:
            reason = ('not enabled' if spec.available(caps)
                      else 'not supported by this device database')
            plan.skipped.append((spec.label, reason))

    counts = {'compared': 0, 'conflicts': 0, 'unchanged': 0}

    for link in inputs.links:
        state = inputs.device_state(link)
        values = inputs.calibre_values.get(link.calibre_id, {})
        cal_ts = _canon_modified(inputs.calibre_modified.get(link.calibre_id))

        for spec in specs:
            if spec.key == 'collections':
                raw_device = inputs.device_collections.get(link.device_book_id)
                raw_calibre = inputs.calibre_collections.get(link.calibre_id)
            else:
                raw_device = spec.read_device(state)
                raw_calibre = values.get(spec.key)

            dev_c = spec.canonical(raw_device)
            cal_c = spec.canonical(raw_calibre)
            prior_dev, prior_cal = inputs.prior.get(
                (link.calibre_id, spec.key), (UNKNOWN, UNKNOWN))
            dev_ts = spec.device_timestamp(state)

            counts['compared'] += 1
            outcome = decide(spec, dev_c, cal_c, prior_dev, prior_cal,
                             dev_ts, cal_ts, mode=mode, skew=skew,
                             allow_clear=allow_clear)
            if outcome is None:
                counts['unchanged'] += 1
                continue

            side, reason = outcome
            if side is SKIP:
                plan.skipped.append(
                    ('%s - %s' % (link.title or link.calibre_id, spec.label),
                     reason))
                continue
            if side is Side.DEVICE and not spec.syncs_to_device:
                plan.skipped.append(
                    ('%s - %s' % (link.title or link.calibre_id, spec.label),
                     'this field is only ever read from the device'))
                continue
            if side is Side.CALIBRE and not spec.syncs_to_calibre:
                continue

            if 'newer' in reason or 'same time' in reason:
                counts['conflicts'] += 1

            if side is Side.CALIBRE:
                new_value = spec.to_calibre(dev_c)
                old_value = raw_calibre
                target = plan.calibre_changes
            else:
                new_value = spec.to_device(cal_c)
                old_value = raw_device
                target = plan.device_changes

            selected = True
            if trust_floor is not None and link.confidence < trust_floor:
                selected = False

            target.append(ProposedChange(
                link=link, field=spec.key, direction=side,
                old=old_value, new=new_value, reason=reason,
                selected=selected))

    plan.stats = counts
    debug('plan: %s' % plan.summary())
    return plan


def _canon_modified(value):
    return canon_datetime(value)


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------

def calibre_updates(plan, column_for):
    """``{lookup_name: {calibre_id: value}}`` for ``new_api.set_field``."""
    updates = {}
    for change in plan.selected(Side.CALIBRE):
        column = column_for(change.field)
        if not column:
            continue
        updates.setdefault(column, {})[change.link.calibre_id] = change.new
    return updates


def device_settings_rows(plan, profile_id, timestamp=None):
    """Group the device-bound changes into upsertable rows.

    Returns ``{(column, ...): [(values..., book_id, profile_id), ...]}`` ready
    for ``executemany``; each group shares one statement.
    """

    timestamp = int(timestamp if timestamp is not None else time.time())

    per_book = {}
    for change in plan.selected(Side.DEVICE):
        spec = BY_KEY[change.field]
        if not spec.device_column:
            continue
        slot = per_book.setdefault(change.link.device_book_id, {})
        slot[spec.device_column] = change.new
        if spec.device_ts_column:
            slot[spec.device_ts_column] = timestamp

    groups = {}
    for book_id, columns in per_book.items():
        key = tuple(sorted(columns))
        groups.setdefault(key, []).append(
            tuple(columns[name] for name in key) + (book_id, profile_id))
    return groups


def upsert_statement(columns):
    """INSERT ... ON CONFLICT upsert for ``books_settings``.

    Column names come from the field registry, never from data.
    """
    names = list(columns) + ['bookid', 'profileid']
    placeholders = ', '.join('?' * len(names))
    assignments = ', '.join('%s = excluded.%s' % (c, c) for c in columns)
    return (
        'INSERT INTO books_settings (%s) VALUES (%s) '
        'ON CONFLICT(bookid, profileid) DO UPDATE SET %s'
        % (', '.join(names), placeholders, assignments)
    )


def apply_to_device(conn, plan, profile_id, timestamp=None):
    """Write the selected device-bound changes. Caller owns the transaction."""
    groups = device_settings_rows(plan, profile_id, timestamp)
    written = 0
    for columns, rows in groups.items():
        statement = upsert_statement(columns)
        conn.executemany(statement, rows)
        written += len(rows)
    debug('wrote %d books_settings row(s) in %d statement group(s)'
          % (written, len(groups)))
    return written


def touched_device_ids(plan):
    return sorted({c.link.device_book_id for c in plan.selected(Side.DEVICE)})


def record_entries(plan, inputs, caps, enabled_fields=None):
    """What to remember after a successful apply.

    Both sides are recorded as agreeing on the value the plan settled on, so
    the next run sees "nothing moved" and proposes nothing.
    """
    specs = available_fields(caps, enabled_fields)
    applied = {}
    for change in plan.all_changes:
        if change.selected:
            applied[(change.link.calibre_id, change.field)] = change

    entries = []
    for link in inputs.links:
        state = inputs.device_state(link)
        values = inputs.calibre_values.get(link.calibre_id, {})
        for spec in specs:
            if spec.key == 'collections':
                raw_device = inputs.device_collections.get(link.device_book_id)
                raw_calibre = inputs.calibre_collections.get(link.calibre_id)
            else:
                raw_device = spec.read_device(state)
                raw_calibre = values.get(spec.key)
            dev_c = spec.canonical(raw_device)
            cal_c = spec.canonical(raw_calibre)

            change = applied.get((link.calibre_id, spec.key))
            if change is not None:
                agreed = dev_c if change.direction is Side.CALIBRE else cal_c
            elif dev_c == cal_c:
                agreed = dev_c
            else:
                # Not applied and still different: do not claim agreement,
                # or the next run would treat it as "nothing moved".
                continue
            entries.append((link.calibre_id, spec.key, agreed, agreed))
    return entries
