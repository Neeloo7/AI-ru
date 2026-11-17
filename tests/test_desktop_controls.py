import unittest
from unittest.mock import Mock, patch

import desktop_controls as desktop
import airu_gui


class DesktopTests(unittest.TestCase):
    def test_routing_preserves_literal_text_and_gui_commands(self):
        self.assertEqual(desktop.parse_desktop_command("type Hello!"), ("type", "Hello!"))
        self.assertEqual(desktop.parse_desktop_command('type "100% + {ENTER}"'), ("type", "100% + {ENTER}"))
        self.assertIsNone(airu_gui.parse_command('type "change the wake phrase to Hello"'))
        self.assertEqual(airu_gui.parse_command("switch to Concise personality")[0], "persona_switch")
        self.assertEqual(desktop.parse_desktop_command("open my downloads"), ("folder", "downloads"))

    def test_text_cannot_be_interpreted_as_shortcuts(self):
        self.assertEqual(desktop.literal_keys("^a%{ENTER}"), "{^}a{%}{{}ENTER{}}")
        with self.assertRaises(ValueError):
            desktop.literal_keys("hello\nworld")
        self.assertEqual(desktop.key_sequence("Ctrl+Shift+S"), "^+s")
        with self.assertRaises(ValueError):
            desktop.key_sequence("{ENTER 100}")

    def test_ambiguous_controls_are_not_clicked(self):
        with self.assertRaisesRegex(ValueError, "More than one"):
            desktop.select_unique(["Save", "Save"], "Save", lambda item: item)
        self.assertEqual(desktop.select_unique(["Save", "Save as"], "Save", lambda item: item), "Save")

    def test_helper_receives_json_and_remembers_explicit_window(self):
        controller = desktop.DesktopController()
        with patch.object(desktop.os, "name", "nt"), patch.object(desktop.subprocess, "run") as run:
            run.return_value = Mock(returncode=0, stdout='{"message":"Controlling Notepad","target":123}')
            controller.execute(("focus", "Notepad"))
            controller.execute(("type", "hello"))
        import json
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(request, {"action": "type", "value": "hello", "target": 123})
        self.assertNotIn("shell", run.call_args.kwargs)
