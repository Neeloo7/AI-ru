import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import core_engine


class EngineRegressionTests(unittest.TestCase):
    def test_radio_request_runs_control_and_never_asks_model_to_pretend(self):
        engine = core_engine.AiruEngine.__new__(core_engine.AiruEngine)
        engine.save_memory = Mock()
        with patch.object(core_engine, "execute_windows_command", return_value="Verified Bluetooth: On") as control, \
             patch.object(core_engine.requests, "post") as model:
            self.assertEqual(engine.process_request("Turn on the Bluetooth on this device."), "Verified Bluetooth: On")
            control.assert_called_once_with(("radio", ("bluetooth", "on")))
            self.assertIn("haven't changed", engine.process_request("I will turn on Bluetooth."))
            model.assert_not_called()
    def test_streamed_reply_is_saved_once_and_connection_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(core_engine, "DB_PATH", str(Path(directory) / "memory.db")), patch.object(core_engine.AiruEngine, "scan_start_menu"):
                engine = core_engine.AiruEngine()
            try:
                response = Mock(status_code=200)
                response.iter_lines.return_value = [b'{"message":{"content":"Hello "}}', b'{"message":{"content":"there"},"done":true}']
                chunks = []
                with patch.object(core_engine.requests, "post", return_value=response) as request:
                    self.assertEqual(engine.process_request("hello", history=[], on_chunk=chunks.append), "Hello there")
                self.assertEqual(chunks[-1], "Hello there")
                self.assertTrue(request.call_args.kwargs["stream"])
                self.assertEqual(engine.conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0], 2)
                response.close.assert_called_once()
                response.iter_lines.return_value = [b'{"message":{"content":"unfinished"}}']
                with patch.object(core_engine.requests, "post", return_value=response):
                    self.assertIn("stopped before completing", engine.process_request("again", on_chunk=chunks.append))
                self.assertEqual(engine.conn.execute("SELECT COUNT(*) FROM memory WHERE role='assistant'").fetchone()[0], 1)
            finally:
                engine.conn.close()

    def test_explicit_memory_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(core_engine, "DB_PATH", str(Path(directory) / "memory.db")), patch.object(core_engine.AiruEngine, "scan_start_menu"):
                engine = core_engine.AiruEngine()
            try:
                with patch.object(core_engine.requests, "post") as request:
                    engine.process_request("remember that I prefer short replies")
                    request.assert_not_called()
                self.assertIn("I prefer short replies", engine.get_learned_facts())
            finally:
                engine.conn.close()

    def test_unmute_preserves_volume_level(self):
        engine = core_engine.AiruEngine.__new__(core_engine.AiruEngine)
        with patch.object(core_engine, "PYCAW_AVAILABLE", True), \
             patch.object(core_engine, "CoInitialize", create=True), \
             patch.object(core_engine, "CoUninitialize", create=True), \
             patch.object(core_engine, "AudioUtilities", create=True) as audio:
            engine.handle_system_controls("unmute volume")
            volume = audio.GetSpeakers.return_value.EndpointVolume
            volume.GetMute.return_value = 0
            volume.GetMasterVolumeLevelScalar.return_value = 0.5
            result = engine.handle_system_controls("please unmute the volume")
            self.assertIn("Verified", result)
            volume.SetMute.assert_called_with(0, None)
            volume.SetMasterVolumeLevelScalar.assert_not_called()

    def test_volume_uses_current_device_api_and_initializes_worker_com(self):
        engine = core_engine.AiruEngine.__new__(core_engine.AiruEngine)
        with patch.object(core_engine, "PYCAW_AVAILABLE", True), \
             patch.object(core_engine, "CoInitialize", create=True) as initialize, \
             patch.object(core_engine, "CoUninitialize", create=True) as cleanup, \
             patch.object(core_engine, "AudioUtilities", create=True) as audio:
            volume = audio.GetSpeakers.return_value.EndpointVolume
            volume.GetMasterVolumeLevelScalar.return_value = 1.0
            volume.GetMute.return_value = 0
            self.assertIn("volume 100%", engine.set_system_volume(150))
            initialize.assert_called_once()
            audio.GetSpeakers.return_value.EndpointVolume.SetMasterVolumeLevelScalar.assert_called_once_with(1.0, None)
            cleanup.assert_called_once()

    def test_mute_change_must_be_verified(self):
        engine = core_engine.AiruEngine.__new__(core_engine.AiruEngine)
        with patch.object(core_engine, "PYCAW_AVAILABLE", True), \
             patch.object(core_engine, "CoInitialize", create=True), \
             patch.object(core_engine, "CoUninitialize", create=True), \
             patch.object(core_engine, "AudioUtilities", create=True) as audio:
            volume = audio.GetSpeakers.return_value.EndpointVolume
            volume.GetMute.return_value = 1
            self.assertIn("verification failed", engine.set_system_mute(False))
            volume.GetMute.return_value = 0
            volume.GetMasterVolumeLevelScalar.return_value = 0.0
            self.assertIn("still zero", engine.set_system_mute(False))

    def test_unmatched_action_never_calls_chat_model(self):
        engine = core_engine.AiruEngine.__new__(core_engine.AiruEngine)
        engine.save_memory = Mock()
        with patch.object(core_engine.requests, "post") as model:
            answer = engine.process_request("Can you move my files into a new folder?")
            self.assertIn("haven't performed", answer)
            model.assert_not_called()

    def test_questions_do_not_launch_apps(self):
        with patch.object(core_engine.os, "startfile", create=True) as launch:
            for prompt in ("What is Microsoft Store?", "How does Spotify work?", "Tell me about Steam"):
                self.assertIsNone(core_engine.execute_os_command(prompt))
            launch.assert_not_called()

    def test_application_target_is_not_executed_by_shell(self):
        engine = core_engine.AiruEngine.__new__(core_engine.AiruEngine)
        engine.app_index = {}
        with patch.object(core_engine.subprocess, "Popen", side_effect=FileNotFoundError) as launch:
            engine.run_application("missing & calc.exe")
            launch.assert_called_once_with(["missing & calc.exe"], shell=False)
        self.assertIn("specify", engine.run_application(""))

    def test_volume_question_has_no_side_effect(self):
        engine = core_engine.AiruEngine.__new__(core_engine.AiruEngine)
        with patch.object(engine, "set_system_volume") as volume:
            self.assertIsNone(engine.handle_system_controls("What does volume mean?"))
            volume.assert_not_called()
            engine.handle_system_controls("set volume to 150")
            volume.assert_called_once_with(150)

    def test_new_context_preserves_saved_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(core_engine, "DB_PATH", str(Path(directory) / "memory.db")), patch.object(core_engine.AiruEngine, "scan_start_menu"):
                engine = core_engine.AiruEngine()
            try:
                engine.save_memory("user", "old chat")
                response = Mock(status_code=200)
                response.json.return_value = {"message": {"content": "hello back"}}
                with patch.object(core_engine.requests, "post", return_value=response) as request:
                    engine.process_request("hello", history=[], system_prompt="Be friendly.")
                messages = request.call_args.kwargs["json"]["messages"]
                self.assertEqual(len(messages), 2)
                self.assertIn("Be friendly.", messages[0]["content"])
                self.assertEqual(messages[1], {"role": "user", "content": "hello"})
                self.assertEqual(engine.conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0], 3)
            finally:
                engine.conn.close()


if __name__ == "__main__":
    unittest.main()
