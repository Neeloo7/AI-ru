import subprocess
import unittest
from unittest.mock import Mock, patch

import windows_controls as controls
import airu_gui


class WindowsControlsTests(unittest.TestCase):
    def test_audio_commands_bypass_interface_sound_preferences(self):
        for text in ("unmute the volume", "Please unmute my audio", "unmute", "mute the sound", "check volume"):
            self.assertEqual(controls.parse_windows_command(text)[0], "audio")
            self.assertIsNone(airu_gui.parse_command(text))
        self.assertIsNotNone(airu_gui.parse_command("mute typing sounds"))
    def test_radio_command_variants_and_questions(self):
        for text in ("turn Wi-Fi off", "Please turn off wifi", "Could you disable WiFi?"):
            self.assertEqual(controls.parse_windows_command(text), ("radio", ("wifi", "off")))
        for text in ("How do I turn off wifi?", "turn wifi off and delete files", "What is Bluetooth?"):
            self.assertIsNone(controls.parse_windows_command(text))

    def test_real_failed_user_wording_and_polite_variants(self):
        for text in ("Turn on the Bluetooth on this device.", "can you turn on my bluetooth for me?",
                     "I want you to enable the Bluetooth", "turn my blue tooth on please"):
            self.assertEqual(controls.parse_windows_command(text), ("radio", ("bluetooth", "on")))
            self.assertIsNone(airu_gui.parse_command(text))
        self.assertEqual(controls.parse_windows_command("Is my Bluetooth turned on?"), ("radio", ("bluetooth", "status")))
        self.assertIsNone(controls.parse_windows_command("Don't turn my Bluetooth on"))

    def test_native_settings_reach_backend(self):
        for text in ("open Bluetooth settings", "open Windows settings", "open sound settings"):
            self.assertIsNone(airu_gui.parse_command(text))
        self.assertEqual(airu_gui.parse_command("open settings")[0], "settings")

    def test_wifi_profile_keeps_case_and_is_one_argument(self):
        intent = controls.parse_windows_command('connect Wi-Fi to "Home & Office"')
        with patch.object(controls.os, "name", "nt"), patch.object(controls, "run_command", return_value="requested") as run:
            controls.execute_windows_command(intent)
        run.assert_called_once_with(["netsh", "wlan", "connect", "name=Home & Office"])

    def test_timeout_and_permission_failure_are_reported(self):
        with patch.object(controls.os, "name", "nt"), patch.object(controls.subprocess, "run", side_effect=subprocess.TimeoutExpired("netsh", 20)):
            self.assertIn("too long", controls.execute_windows_command(("wifi_status", None)))
        with patch.object(controls.subprocess, "run", return_value=Mock(returncode=1, stdout="Access denied", stderr="")):
            self.assertIn("could not complete", controls.run_command(["netsh"]))

    def test_brightness_range_does_not_execute(self):
        with patch.object(controls.os, "name", "nt"), patch.object(controls, "run_command") as run:
            self.assertIn("between", controls.execute_windows_command(("brightness", 101)))
            run.assert_not_called()
