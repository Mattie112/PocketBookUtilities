"""What the plugin does when the reader is plugged in.

Four settings, an escalating ladder of trust. The labels say what happens
rather than describing a consequence: "don't show a popup" was never the
thing wanted, "don't make me approve the safe direction" was.
"""

import unittest

from calibre_plugins.pocketbook_utilities import config
from calibre_plugins.pocketbook_utilities.runner import (
    split_for_connect, sub_plan)
from calibre_plugins.pocketbook_utilities.sync.model import (
    BookLink, MatchConfidence, ProposedChange, Side, SyncPlan)


def link(calibre_id=1, confidence=MatchConfidence.EXACT_PATH, title='Book'):
    return BookLink(calibre_id=calibre_id, device_book_id=calibre_id,
                    lpath='', device_path='', title=title,
                    confidence=confidence)


def change(direction, confidence=MatchConfidence.EXACT_PATH, field='read'):
    return ProposedChange(link=link(confidence=confidence), field=field,
                          direction=direction, old=None, new=True,
                          reason='test')


def plan_with(*changes):
    plan = SyncPlan()
    for item in changes:
        if item.direction is Side.CALIBRE:
            plan.calibre_changes.append(item)
        else:
            plan.device_changes.append(item)
    return plan


class TestModes(unittest.TestCase):

    def test_the_four_choices_are_offered_in_order_of_trust(self):
        values = [value for value, _label in config.ON_CONNECT_CHOICES]
        self.assertEqual(values, ['off', 'ask', 'auto_load', 'auto_both'])

    def test_every_label_says_what_happens(self):
        for value, label in config.ON_CONNECT_CHOICES:
            self.assertTrue(label and label[0].isupper(), value)
            self.assertNotIn('popup', label.lower(),
                             'labels describe the choice, not its side effect')

    def test_off_is_the_default(self):
        self.assertEqual(config.on_connect_mode({}), config.ON_CONNECT_OFF)

    def test_an_unknown_value_falls_back_to_off(self):
        self.assertEqual(config.on_connect_mode({'on_connect': 'nonsense'}),
                         config.ON_CONNECT_OFF)

    def test_only_three_modes_do_anything(self):
        self.assertNotIn(config.ON_CONNECT_OFF, config.ON_CONNECT_ACTIVE)
        self.assertEqual(len(config.ON_CONNECT_ACTIVE), 3)


class TestWhatRunsSilently(unittest.TestCase):

    def split(self, mode, *changes):
        return split_for_connect(plan_with(*changes), mode)

    def test_ask_shows_everything(self):
        silent, review = self.split(
            config.ON_CONNECT_ASK,
            change(Side.CALIBRE), change(Side.DEVICE))
        self.assertEqual(silent, [])
        self.assertEqual(len(review), 2)

    def test_auto_load_applies_only_towards_calibre(self):
        to_calibre, to_device = change(Side.CALIBRE), change(Side.DEVICE)
        silent, review = self.split(config.ON_CONNECT_AUTO_LOAD,
                                    to_calibre, to_device)
        self.assertEqual(silent, [to_calibre])
        self.assertEqual(review, [to_device],
                         'device-bound changes are still shown, not dropped')

    def test_auto_both_applies_everything(self):
        silent, review = self.split(
            config.ON_CONNECT_AUTO_BOTH,
            change(Side.CALIBRE), change(Side.DEVICE))
        self.assertEqual(len(silent), 2)
        self.assertEqual(review, [])

    def test_a_weak_match_is_never_applied_unseen(self):
        """Matched by title and author only: show it, whatever the mode."""
        weak = change(Side.CALIBRE, confidence=MatchConfidence.METADATA)
        for mode in (config.ON_CONNECT_AUTO_LOAD,
                     config.ON_CONNECT_AUTO_BOTH):
            silent, review = self.split(mode, weak)
            self.assertEqual(silent, [], mode)
            self.assertEqual(review, [weak], mode)

    def test_a_filename_match_is_trusted_enough(self):
        ok = change(Side.CALIBRE, confidence=MatchConfidence.BASENAME)
        silent, review = self.split(config.ON_CONNECT_AUTO_LOAD, ok)
        self.assertEqual(silent, [ok])


class TestSubPlan(unittest.TestCase):

    def test_it_holds_only_the_changes_given(self):
        a, b = change(Side.CALIBRE), change(Side.DEVICE)
        plan = plan_with(a, b)
        only_b = sub_plan(plan, [b])
        self.assertEqual(only_b.calibre_changes, [])
        self.assertEqual(only_b.device_changes, [b])

    def test_the_original_is_not_disturbed(self):
        a, b = change(Side.CALIBRE), change(Side.DEVICE)
        plan = plan_with(a, b)
        sub_plan(plan, [b])
        self.assertEqual(len(plan.all_changes), 2)

    def test_context_is_carried_over(self):
        plan = plan_with(change(Side.CALIBRE))
        plan.unmatched_calibre = [7]
        plan.device_only = ['x']
        out = sub_plan(plan, plan.all_changes)
        self.assertEqual(out.unmatched_calibre, [7])
        self.assertEqual(out.device_only, ['x'])


class TestDirectionHelper(unittest.TestCase):

    def test_auto_load_covers_calibre_but_not_the_device(self):
        self.assertTrue(config.applies_without_asking(
            config.ON_CONNECT_AUTO_LOAD, side_is_device=False))
        self.assertFalse(config.applies_without_asking(
            config.ON_CONNECT_AUTO_LOAD, side_is_device=True))

    def test_ask_covers_neither(self):
        for side in (True, False):
            self.assertFalse(config.applies_without_asking(
                config.ON_CONNECT_ASK, side_is_device=side))


if __name__ == '__main__':
    unittest.main()


class FakeLibrary:
    """Just the prefs namespace calibre's db exposes."""

    def __init__(self):
        self.store = {}

    class _Prefs:
        def __init__(self, outer):
            self.outer = outer

        def get_namespaced(self, ns, key, default=None):
            return self.outer.store.get((ns, key), default)

        def set_namespaced(self, ns, key, val):
            self.outer.store[(ns, key)] = val

    @property
    def prefs(self):
        return self._Prefs(self)


class FakeDevice:
    def __init__(self, serial):
        self.serial = serial


class TestSettingsSurviveNoDevice(unittest.TestCase):
    """Settings are per device, but the dialog opens without one.

    Falling back to the bare defaults made every saved value look unset:
    the on-connect choice read back as "do not sync" after a restart, and
    column mappings would have looked unmapped too.
    """

    def setUp(self):
        self.db = FakeLibrary()
        self.device = FakeDevice('serial-1')

    def save(self, **values):
        config.save_device_settings(self.db, self.device.serial, values)

    def test_a_saved_choice_reads_back_without_a_device(self):
        self.save(on_connect=config.ON_CONNECT_AUTO_LOAD)
        settings = config.settings_for(self.db, None)
        self.assertEqual(config.on_connect_mode(settings),
                         config.ON_CONNECT_AUTO_LOAD)

    def test_it_also_reads_back_with_the_device(self):
        self.save(on_connect=config.ON_CONNECT_AUTO_BOTH)
        settings = config.settings_for(self.db, self.device)
        self.assertEqual(config.on_connect_mode(settings),
                         config.ON_CONNECT_AUTO_BOTH)

    def test_column_mappings_survive_too(self):
        self.save(columns={'read': '#pb_read'})
        self.assertEqual(
            config.settings_for(self.db, None)['columns'],
            {'read': '#pb_read'})

    def test_an_empty_library_still_gives_defaults(self):
        settings = config.settings_for(self.db, None)
        self.assertEqual(config.on_connect_mode(settings),
                         config.ON_CONNECT_OFF)
        self.assertIsNone(config.resolve_serial(self.db, None))

    def test_the_last_saved_device_wins_when_several_are_known(self):
        config.save_device_settings(self.db, 'serial-1',
                                    {'on_connect': config.ON_CONNECT_ASK})
        config.save_device_settings(self.db, 'serial-2',
                                    {'on_connect': config.ON_CONNECT_AUTO_BOTH})
        self.assertEqual(config.resolve_serial(self.db, None), 'serial-2')
        self.assertEqual(
            config.on_connect_mode(config.settings_for(self.db, None)),
            config.ON_CONNECT_AUTO_BOTH)

    def test_a_connected_device_always_wins_over_the_remembered_one(self):
        config.save_device_settings(self.db, 'serial-1',
                                    {'on_connect': config.ON_CONNECT_ASK})
        config.save_device_settings(self.db, 'serial-2',
                                    {'on_connect': config.ON_CONNECT_AUTO_BOTH})
        settings = config.settings_for(self.db, FakeDevice('serial-1'))
        self.assertEqual(config.on_connect_mode(settings),
                         config.ON_CONNECT_ASK)
