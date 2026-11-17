import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import airu_gui


class GuiTests(unittest.TestCase):
    def test_session_changes_notify_backend_but_font_refresh_does_not(self):
        app = airu_gui.QApplication.instance() or airu_gui.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(airu_gui, "DATA_DIR", root), \
                 patch.object(airu_gui, "SESSIONS_FILE", root / "sessions.json"), \
                 patch.object(airu_gui, "SETTINGS_FILE", root / "settings.json"):
                window = airu_gui.AiruGUI()
                try:
                    callback = Mock()
                    window.set_new_session_callback(callback)
                    window._do_add_message("user", "test")
                    window.show_stream_preview("partial")
                    window.show_stream_preview("partial reply")
                    self.assertEqual(len(window.current["msgs"]), 1)
                    self.assertEqual(window._stream_row.body.text(), "partial reply")
                    window._new_session()
                    self.assertIsNone(window._stream_row)
                    callback.assert_called_once()
                    window._load_current()
                    callback.assert_called_once()
                    window._clear_session()
                    self.assertEqual(callback.call_count, 2)
                    app.processEvents()
                finally:
                    window._quitting = True
                    window.orb.hide()
                    if window.tray:
                        window.tray.hide()
                    window.close()
