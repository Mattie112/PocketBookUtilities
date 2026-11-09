import datetime
import os
import sqlite3
import tempfile
import unittest

from base import FixtureTestCase

from calibre_plugins.pocketbook_utilities.device import db as dbmod
from calibre_plugins.pocketbook_utilities.device import schema
from calibre_plugins.pocketbook_utilities.sync import engine, fields, identity
from calibre_plugins.pocketbook_utilities.sync.engine import Mode, SyncInputs
from calibre_plugins.pocketbook_utilities.sync.model import (
    BookLink, DeviceState, MatchConfidence, Side)
from calibre_plugins.pocketbook_utilities.sync.state import (
    UNKNOWN, SyncStateStore)

import make_fixture
from test_identity import calibre_books_from_fixture

UTC = datetime.timezone.utc
COLUMNS = {
    'read': '#pb_read',
    'read_date': '#pb_read_date',
    'last_read': '#pb_last_read',
    'favourite': '#pb_favourite',
    'progress': '#pb_progress',
    'position': '#pb_position',
    'collections': '#pb_collections',
}


class TestCanonicalisation(unittest.TestCase):
    """The exact comparisons the previous plugin got wrong."""

    def test_bool_forms_collapse(self):
        for falsey in (0, '0', False, 'false', 'No'):
            self.assertIs(fields.canon_bool(falsey), False, repr(falsey))
        for truthy in (1, '1', True, 'true', 'Yes'):
            self.assertIs(fields.canon_bool(truthy), True, repr(truthy))
        for empty in (None, '', 'None', 'null'):
            self.assertIsNone(fields.canon_bool(empty), repr(empty))

    def test_str_none_never_equals_zero(self):
        """`str(None) != "0"` is what rewrote thousands of books every run."""
        self.assertNotEqual(str(None), '0')
        self.assertNotEqual(fields.canon_bool(None), fields.canon_bool(0))
        self.assertIsNone(fields.canon_bool(None))
        self.assertIs(fields.canon_bool(0), False)

    def test_text_blank_becomes_none(self):
        self.assertIsNone(fields.canon_text('   '))
        self.assertIsNone(fields.canon_text(''))
        self.assertEqual(fields.canon_text('  x '), 'x')

    def test_datetime_and_timestamp_are_comparable(self):
        stamp = 1790000000
        as_dt = datetime.datetime.fromtimestamp(stamp, tz=UTC)
        self.assertEqual(fields.canon_datetime(stamp),
                         fields.canon_datetime(as_dt))

    def test_calibre_undefined_date_is_none(self):
        undefined = datetime.datetime(101, 1, 1, tzinfo=UTC)
        self.assertIsNone(fields.canon_datetime(undefined))

    def test_sets_ignore_order_and_blanks(self):
        self.assertEqual(fields.canon_set(['b', 'a', '', '  ']),
                         frozenset({'a', 'b'}))
        self.assertIsNone(fields.canon_set([]))


class TestDecide(unittest.TestCase):
    spec = fields.BY_KEY['read']

    def decide(self, dev, cal, prior_dev=UNKNOWN, prior_cal=UNKNOWN,
               dev_ts=None, cal_ts=None, **kw):
        return engine.decide(self.spec, self.spec.canonical(dev),
                             self.spec.canonical(cal), prior_dev, prior_cal,
                             dev_ts, cal_ts, **kw)

    def test_equal_values_propose_nothing(self):
        self.assertIsNone(self.decide(1, True))
        self.assertIsNone(self.decide(0, False))
        self.assertIsNone(self.decide(None, None))

    def test_only_device_moved_loads_into_calibre(self):
        side, reason = self.decide(True, False, prior_dev=False,
                                   prior_cal=False)
        self.assertIs(side, Side.CALIBRE)
        self.assertIn('device', reason)

    def test_only_calibre_moved_sends_to_device(self):
        side, reason = self.decide(False, True, prior_dev=False,
                                   prior_cal=False)
        self.assertIs(side, Side.DEVICE)
        self.assertIn('calibre', reason)

    def test_both_moved_newest_wins(self):
        side, reason = self.decide(True, False, prior_dev=None,
                                   prior_cal=None,
                                   dev_ts=2000, cal_ts=1000)
        self.assertIs(side, Side.CALIBRE)
        self.assertIn('newer', reason)

        side, reason = self.decide(True, False, prior_dev=None,
                                   prior_cal=None,
                                   dev_ts=1000, cal_ts=2000)
        self.assertIs(side, Side.DEVICE)

    def test_simultaneous_change_is_skipped_not_guessed(self):
        side, reason = self.decide(True, False, prior_dev=None,
                                   prior_cal=None, dev_ts=1000, cal_ts=1010)
        self.assertEqual(side, engine.SKIP)

    def test_empty_side_never_wins_on_a_first_sync(self):
        side, _ = self.decide(True, None)
        self.assertIs(side, Side.CALIBRE)
        side, _ = self.decide(None, True)
        self.assertIs(side, Side.DEVICE)

    def test_force_modes_ignore_history(self):
        side, _ = self.decide(True, False, prior_dev=True, prior_cal=False,
                              mode=Mode.FORCE_SEND)
        self.assertIs(side, Side.DEVICE)
        side, _ = self.decide(True, False, prior_dev=True, prior_cal=False,
                              mode=Mode.FORCE_LOAD)
        self.assertIs(side, Side.CALIBRE)

    def test_force_send_refuses_to_clear_by_default(self):
        side, reason = self.decide(True, None, mode=Mode.FORCE_SEND)
        self.assertEqual(side, engine.SKIP)
        side, _ = self.decide(True, None, mode=Mode.FORCE_SEND,
                              allow_clear=True)
        self.assertIs(side, Side.DEVICE)


class TestClearingIsGuardedEverywhere(unittest.TestCase):
    """An empty value must never overwrite a filled one by default.

    The guard once sat only on the forced paths, so a column cleared in
    calibre was treated as an ordinary edit and carried onto the device,
    wiping reading positions. Every route out of decide() is checked here.
    """

    spec = fields.BY_KEY['position']
    POSITION = 'pbr:/webkit?##epubcfi(/6/62!/4/2/4/1:1)'

    def decide(self, dev, cal, **kw):
        return engine.decide(self.spec, self.spec.canonical(dev),
                             self.spec.canonical(cal),
                             kw.pop('prior_dev', UNKNOWN),
                             kw.pop('prior_cal', UNKNOWN),
                             kw.pop('dev_ts', None),
                             kw.pop('cal_ts', None), **kw)

    def test_a_cleared_calibre_column_does_not_wipe_the_device(self):
        """The exact case: the column was cleared, the device still has it."""
        side, reason = self.decide(self.POSITION, None,
                                   prior_dev=self.POSITION,
                                   prior_cal=self.POSITION)
        self.assertEqual(side, engine.SKIP)
        self.assertIn('would clear', reason)
        self.assertIn('device', reason)

    def test_the_same_case_is_allowed_when_asked_for(self):
        side, _ = self.decide(self.POSITION, None, prior_dev=self.POSITION,
                              prior_cal=self.POSITION, allow_clear=True)
        self.assertIs(side, Side.DEVICE)

    def test_a_cleared_device_value_does_not_wipe_calibre(self):
        side, reason = self.decide(None, self.POSITION,
                                   prior_dev=self.POSITION,
                                   prior_cal=self.POSITION)
        self.assertEqual(side, engine.SKIP)
        self.assertIn('calibre', reason)

    def test_when_both_moved_the_side_with_a_value_wins(self):
        """The conflict path fills rather than clears, whatever the dates.

        Both sides changed and one is now empty. Even with timestamps that
        would favour the empty side, the value is copied rather than the
        emptiness, so nothing has to be blocked here.
        """
        side, reason = self.decide(self.POSITION, None, prior_dev=None,
                                   prior_cal=self.POSITION,
                                   dev_ts=1000, cal_ts=9000)
        self.assertIs(side, Side.CALIBRE)
        self.assertIn('only the device has a value', reason)

        side, reason = self.decide(None, self.POSITION, prior_dev=self.POSITION,
                                   prior_cal=None, dev_ts=9000, cal_ts=1000)
        self.assertIs(side, Side.DEVICE)
        self.assertIn('only calibre has a value', reason)

    def test_filling_an_empty_side_is_never_blocked(self):
        side, _ = self.decide(self.POSITION, None, prior_dev=None,
                              prior_cal=None)
        self.assertIs(side, Side.CALIBRE)
        side, _ = self.decide(None, self.POSITION, prior_dev=None,
                              prior_cal=None)
        self.assertIs(side, Side.DEVICE)

    def test_a_real_change_still_goes_through(self):
        other = 'pbr:/webkit?##epubcfi(/6/99!/4/2)'
        side, _ = self.decide(self.POSITION, other,
                              prior_dev=self.POSITION,
                              prior_cal=self.POSITION)
        self.assertIs(side, Side.DEVICE)

    def test_read_status_false_is_a_value_not_a_clear(self):
        """False means "not read"; only None is absence."""
        read = fields.BY_KEY['read']
        side, _ = engine.decide(read, read.canonical(True),
                                read.canonical(False), True, True,
                                None, None)
        self.assertIs(side, Side.DEVICE)


class EngineTestCase(FixtureTestCase):
    """A full read of the fixture, wired up the way the action layer does."""

    def inputs(self, calibre_values=None, prior=None, modified=None,
               calibre_collections=None):
        copy = dbmod.WorkingCopy(self.db_path)
        self.addCleanup(copy.close)
        conn = copy.open()
        caps = schema.introspect(conn)
        index = identity.load_device_index(
            conn, caps, {'main': self.prefix, 'card': self.card_prefix})
        links, unmatched, device_only = identity.build_links(
            index, calibre_books_from_fixture())
        states = identity.load_reading_state(conn, caps, 1)
        return conn, caps, SyncInputs(
            links=links,
            device_states=states,
            calibre_values=calibre_values or {},
            calibre_modified=modified or {},
            calibre_collections=calibre_collections or {},
            prior=prior or {},
            unmatched_calibre=unmatched,
            device_only=device_only,
        )


class TestFirstRun(EngineTestCase):

    def test_device_state_is_loaded_into_an_empty_library(self):
        conn, caps, inputs = self.inputs()
        plan = engine.build_plan(inputs, caps,
                                 enabled_fields={'read', 'progress',
                                                 'position', 'favourite'})
        self.assertFalse(plan.is_empty)
        self.assertEqual(plan.device_changes, [],
                         'a first run must not write to the device')

        by_field = {}
        for change in plan.calibre_changes:
            by_field.setdefault(change.field, []).append(change)
        # Three completed books in the fixture, one of them not on disk.
        read_true = [c for c in by_field['read'] if c.new is True]
        self.assertEqual(len(read_true), 2)
        self.assertIn('progress', by_field)
        self.assertIn('position', by_field)

    def test_read_only_fields_never_produce_device_changes(self):
        conn, caps, inputs = self.inputs()
        plan = engine.build_plan(inputs, caps,
                                 enabled_fields={'progress', 'last_read'})
        self.assertEqual(plan.device_changes, [])
        self.assertTrue(plan.calibre_changes)

    def test_unsupported_field_is_skipped_with_a_reason(self):
        conn, caps, inputs = self.inputs()
        plan = engine.build_plan(inputs, caps, enabled_fields={'read'})
        reasons = dict(plan.skipped)
        self.assertEqual(reasons.get('Favourite'), 'not enabled')

    def test_weak_matches_are_preselected_off(self):
        conn, caps, inputs = self.inputs()
        weak = BookLink(calibre_id=5555, device_book_id=1,
                        lpath='x', device_path='y',
                        confidence=MatchConfidence.METADATA, title='Weak')
        inputs.links = [weak]
        plan = engine.build_plan(inputs, caps, enabled_fields={'read'},
                                 trust_floor=MatchConfidence.BASENAME)
        self.assertTrue(plan.calibre_changes)
        self.assertFalse(any(c.selected for c in plan.calibre_changes))


class TestIdempotence(EngineTestCase):
    """The direct regression test for the churn bug."""

    def run_cycle(self, store, library='lib', serial='ser'):
        prior = store.load(library, serial)
        conn, caps, inputs = self.inputs(
            calibre_values=self.calibre_values, prior=prior)
        enabled = {'read', 'progress', 'position', 'favourite', 'read_date',
                   'last_read'}
        plan = engine.build_plan(inputs, caps, enabled_fields=enabled)

        # Apply the calibre side into our fake library.
        for change in plan.selected(Side.CALIBRE):
            self.calibre_values.setdefault(
                change.link.calibre_id, {})[change.field] = change.new

        entries = engine.record_entries(plan, inputs, caps,
                                        enabled_fields=enabled)
        store.record(library, serial, entries)
        return plan

    def setUp(self):
        super().setUp()
        self.calibre_values = {}
        self.store_path = os.path.join(self.tmp, 'state.db')

    def test_second_run_proposes_nothing(self):
        with SyncStateStore(self.store_path) as store:
            first = self.run_cycle(store)
            self.assertFalse(first.is_empty, 'first run should do something')

            second = self.run_cycle(store)
            self.assertTrue(
                second.is_empty,
                'second run proposed %d change(s): %r'
                % (len(second.all_changes),
                   [(c.link.title, c.field, c.old, c.new)
                    for c in second.all_changes][:5]))

            third = self.run_cycle(store)
            self.assertTrue(third.is_empty)

    def test_a_change_on_one_side_is_still_detected_afterwards(self):
        with SyncStateStore(self.store_path) as store:
            self.run_cycle(store)
            self.assertTrue(self.run_cycle(store).is_empty)

            # The user marks a book read in calibre.
            book = [c for c in self.calibre_values
                    if self.calibre_values[c].get('read') is False][0]
            self.calibre_values[book]['read'] = True

            plan = self.run_cycle(store)
            changes = [c for c in plan.device_changes if c.field == 'read']
            self.assertEqual(len(changes), 1)
            self.assertIs(changes[0].direction, Side.DEVICE)
            self.assertEqual(changes[0].new, 1)


class TestDeviceWrites(EngineTestCase):

    def test_upsert_statement_is_fully_parameterised(self):
        statement = engine.upsert_statement(('completed', 'completed_ts'))
        self.assertIn('ON CONFLICT(bookid, profileid) DO UPDATE', statement)
        self.assertEqual(statement.count('?'), 4)

    def test_rows_are_grouped_per_statement(self):
        conn, caps, inputs = self.inputs()
        link = inputs.links[0]
        plan = engine.build_plan(inputs, caps, enabled_fields={'read'})
        plan.calibre_changes = []
        plan.device_changes = [
            engine.ProposedChange(link=link, field='read',
                                  direction=Side.DEVICE, old=0, new=1,
                                  reason='test'),
        ]
        groups = engine.device_settings_rows(plan, 1, timestamp=999)
        self.assertEqual(list(groups), [('completed', 'completed_ts')])
        self.assertEqual(groups[('completed', 'completed_ts')],
                         [(1, 999, link.device_book_id, 1)])

    def test_writing_read_status_round_trips_through_the_device(self):
        conn, caps, inputs = self.inputs()
        link = [l for l in inputs.links if l.device_book_id == 2][0]
        plan = engine.build_plan(inputs, caps, enabled_fields={'read'})
        plan.calibre_changes = []
        plan.device_changes = [
            engine.ProposedChange(link=link, field='read',
                                  direction=Side.DEVICE, old=False, new=1,
                                  reason='test')]

        with dbmod.DeviceWriteTransaction(self.db_path) as device:
            written = engine.apply_to_device(device, plan, 1)
        self.assertEqual(written, 1)

        check = sqlite3.connect(self.db_path)
        self.addCleanup(check.close)
        row = check.execute('SELECT completed FROM books_settings '
                            'WHERE bookid = 2 AND profileid = 1').fetchone()
        self.assertEqual(row[0], 1)

    def test_insert_path_for_a_book_with_no_settings_row(self):
        conn, caps, inputs = self.inputs()
        link = [l for l in inputs.links if l.device_book_id == 11][0]
        plan = engine.build_plan(inputs, caps, enabled_fields={'read'})
        plan.calibre_changes = []
        plan.device_changes = [
            engine.ProposedChange(link=link, field='read',
                                  direction=Side.DEVICE, old=None, new=1,
                                  reason='test')]
        with dbmod.DeviceWriteTransaction(self.db_path) as device:
            engine.apply_to_device(device, plan, 1)

        check = sqlite3.connect(self.db_path)
        self.addCleanup(check.close)
        row = check.execute('SELECT completed, completed_ts FROM '
                            'books_settings WHERE bookid = 11').fetchone()
        self.assertEqual(row[0], 1)
        self.assertTrue(row[1], 'the device trigger stamps completed_ts')


class TestStateStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='pbutil-state-')
        self.path = os.path.join(self.tmp, 'state.db')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip_preserves_types(self):
        with SyncStateStore(self.path) as store:
            store.record('lib', 'ser', [
                (1, 'read', True, True),
                (2, 'position', 'pbr:/webkit?##epubcfi(/6/2)', 'pbr:/webkit?##epubcfi(/6/2)'),
                (3, 'collections', frozenset({'b', 'a'}), frozenset({'a', 'b'})),
            ])
            loaded = store.load('lib', 'ser')
        self.assertIs(loaded[(1, 'read')][0], True)
        self.assertEqual(loaded[(2, 'position')][0][:4], 'pbr:')
        self.assertEqual(sorted(loaded[(3, 'collections')][0]), ['a', 'b'])

    def test_unknown_for_never_seen_fields(self):
        with SyncStateStore(self.path) as store:
            loaded = store.load('lib', 'ser')
        self.assertEqual(loaded.get((99, 'read'), (UNKNOWN, UNKNOWN))[0],
                         UNKNOWN)

    def test_receipts_round_trip(self):
        with SyncStateStore(self.path) as store:
            store.set_receipt('ser', {'digest': 'abc', 'ids': [1, 2]})
            self.assertEqual(store.get_receipt('ser')['digest'], 'abc')


if __name__ == '__main__':
    unittest.main()


class TestDisplayValues(EngineTestCase):
    """What the review list shows must read the same in both directions.

    A favourite being sent to the device showed "No -> 1", because `new`
    holds whatever the target side stores and the device stores a bool as
    an integer.
    """

    def test_a_bool_sent_to_the_device_reads_as_yes_and_no(self):
        """Marking a book favourite in calibre, exactly as a user would."""
        conn, caps, inputs = self.inputs()
        link = [l for l in inputs.links if l.device_book_id == 1][0]
        inputs.links = [link]
        inputs.calibre_values = {link.calibre_id: {'favourite': True}}
        # Only calibre moved since the last sync, so this goes to the device.
        inputs.prior = {(link.calibre_id, 'favourite'): (False, False)}

        plan = engine.build_plan(inputs, caps, enabled_fields={'favourite'})
        self.assertEqual(len(plan.device_changes), 1, plan.summary())
        change = plan.device_changes[0]

        self.assertEqual(change.new, 1, 'the device still gets an integer')
        self.assertIs(change.display_new, True)
        self.assertIs(change.display_old, False)

    def test_a_bool_loaded_into_calibre_also_reads_as_yes_and_no(self):
        conn, caps, inputs = self.inputs()
        link = [l for l in inputs.links if l.device_book_id == 3][0]
        inputs.links = [link]
        plan = engine.build_plan(inputs, caps, enabled_fields={'favourite'})
        change = plan.calibre_changes[0]
        self.assertIs(change.display_new, True)

    def test_dates_are_shown_as_dates_not_timestamps(self):
        import datetime
        conn, caps, inputs = self.inputs()
        plan = engine.build_plan(inputs, caps, enabled_fields={'last_read'})
        change = plan.calibre_changes[0]
        self.assertIsInstance(change.display_new, datetime.datetime)

    def test_display_defaults_to_the_stored_value(self):
        from calibre_plugins.pocketbook_utilities.sync.model import (
            BookLink, MatchConfidence, ProposedChange, Side)
        link = BookLink(calibre_id=1, device_book_id=1, lpath='', 
                        device_path='', confidence=MatchConfidence.EXACT_PATH)
        change = ProposedChange(link=link, field='read',
                                direction=Side.DEVICE, old=0, new=1,
                                reason='test')
        self.assertEqual(change.display_old, 0)
        self.assertEqual(change.display_new, 1)
