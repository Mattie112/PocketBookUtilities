import os
import unittest

from base import REAL_DUMP, FixtureTestCase, real_dump_available

from calibre_plugins.pocketbook_utilities.device import detect


class FakeDriver:
    gui_name = 'PocketBook Touch Lux 2'

    def __init__(self, main, card=None):
        self._main_prefix = main
        self._card_a_prefix = card
        self._card_b_prefix = None


class FakeManager:
    def __init__(self, driver):
        self.is_device_present = driver is not None
        self.connected_device = driver


class FakeGui:
    def __init__(self, driver):
        self.device_manager = FakeManager(driver)


class TestDetection(FixtureTestCase):

    def test_finds_database_and_reads_driveinfo(self):
        device = detect.probe_prefix(self.prefix)
        self.assertIsNotNone(device)
        self.assertEqual(device.explorer_version, 3)
        self.assertEqual(device.serial, 'fixture-0000-1111-2222')
        self.assertEqual(device.device_name, 'PocketBook Fixture')
        self.assertTrue(device.db_path.endswith('explorer-3.db'))

    def test_non_pocketbook_prefix_is_rejected(self):
        self.assertIsNone(detect.probe_prefix(self.tmp))
        self.assertIsNone(detect.probe_prefix(''))

    def test_get_device_works_through_any_driver(self):
        gui = FakeGui(FakeDriver(self.prefix, self.card_prefix))
        device = detect.get_device(gui)
        self.assertIsNotNone(device)
        self.assertEqual(device.card_prefix, self.card_prefix)
        self.assertIn('FakeDriver', device.driver_name)

    def test_detection_reports_capabilities(self):
        """Detection must not hand back a device with empty capabilities.

        The menu gates collection entries on caps.has_shelves, and the setup
        wizard shows the schema version, so an uninspected device would
        silently disable working features.
        """
        gui = FakeGui(FakeDriver(self.prefix, self.card_prefix))
        device = detect.get_device(gui)
        self.assertEqual(device.caps.db_version, 39)
        self.assertTrue(device.caps.has_shelves)
        self.assertTrue(device.caps.has_reading_state)

    def test_capabilities_can_be_skipped(self):
        gui = FakeGui(FakeDriver(self.prefix, self.card_prefix))
        device = detect.get_device(gui, with_caps=False)
        self.assertEqual(device.caps.db_version, 0)

    def test_unreadable_database_still_detects_the_device(self):
        """A database we cannot open must not make the reader vanish."""
        import os
        os.remove(self.db_path)
        with open(self.db_path, 'wb') as handle:
            handle.write(b'this is not a database')
        gui = FakeGui(FakeDriver(self.prefix, self.card_prefix))
        device = detect.get_device(gui)
        self.assertIsNotNone(device)
        self.assertEqual(device.caps.db_version, 0)

    def test_get_device_falls_back_to_the_card_slot(self):
        """Some drivers report the reader on the card prefix instead."""
        gui = FakeGui(FakeDriver(self.tmp, self.prefix))
        device = detect.get_device(gui)
        self.assertIsNotNone(device)
        self.assertEqual(device.main_prefix, self.prefix)

    def test_no_device_returns_none(self):
        self.assertIsNone(detect.get_device(FakeGui(None)))

    def test_capabilities_and_profile_are_resolved(self):
        device = detect.describe_path(self.prefix)
        self.assertEqual(device.caps.db_version, 39)
        self.assertEqual(device.profile_id, 1)
        self.assertFalse(device.uses_coolreader)

    def test_summary_is_renderable(self):
        device = detect.describe_path(self.prefix)
        pairs = device.summary()
        self.assertTrue(all(isinstance(k, str) and isinstance(v, str)
                            for k, v in pairs))


class TestProfiles(FixtureTestCase):
    with_profiles = True
    with_cr3hist = True

    def test_current_lnk_trailing_newline_is_stripped(self):
        name = detect.read_current_profile_name(self.prefix)
        self.assertEqual(name, 'Matthijs')

    def test_profile_id_resolves_by_name(self):
        device = detect.describe_path(self.prefix)
        self.assertEqual(device.profile_name, 'Matthijs')
        self.assertEqual(device.profile_id, 1)

    def test_coolreader_detected_only_when_the_file_exists(self):
        device = detect.describe_path(self.prefix)
        self.assertTrue(device.uses_coolreader)
        self.assertTrue(device.cr3hist_path.endswith('.cr3hist'))


class TestSingleProfileFallback(FixtureTestCase):

    def test_no_current_lnk_uses_the_only_profile(self):
        self.assertIsNone(detect.read_current_profile_name(self.prefix))
        device = detect.describe_path(self.prefix)
        self.assertEqual(device.profile_id, 1)
        self.assertEqual(device.profile_name, 'default')


@unittest.skipUnless(real_dump_available(), 'no device dump at %s' % REAL_DUMP)
class TestAgainstRealDump(unittest.TestCase):

    def test_real_dump_is_recognised(self):
        device = detect.describe_path(REAL_DUMP)
        self.assertIsNotNone(device)
        self.assertEqual(device.explorer_version, 3)
        self.assertEqual(device.caps.db_version, 39)
        self.assertEqual(device.device_name, 'PocketBook Improved')
        # The real device has one profile and no system/profiles directory.
        self.assertEqual(device.profile_id, 1)
        self.assertEqual(device.profile_name, 'default')
        self.assertFalse(device.uses_coolreader)


if __name__ == '__main__':
    unittest.main()
