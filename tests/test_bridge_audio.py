import os
import unittest
from unittest.mock import ANY, MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from main import AiruAppBridge
from audio_engine import AudioEngine


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch("main.AiruGUI") as gui, patch("main.AiruEngine"), patch("main.AudioEngine"):
            gui.return_value.current = {"msgs": [("user", "hello")]}
            gui.return_value.speak_replies = False
            self.bridge = AiruAppBridge()

    def tearDown(self):
        self.bridge.requests.shutdown(wait=True)
        self.bridge.speech.shutdown(wait=True)
        self.app.processEvents()

    def test_cancelled_session_drops_already_queued_reply(self):
        self.bridge._result.emit(0, "ai-ru", "old reply")
        self.bridge.handle_new_session()
        self.app.processEvents()
        self.bridge.gui._do_add_message.assert_not_called()

    def test_cancelled_recording_drops_transcript(self):
        self.bridge.handle_mic_toggle(False)
        self.bridge._apply_transcript(0, "open calculator")
        self.bridge.gui._do_add_message.assert_not_called()

    def test_transcript_uses_same_command_router_as_typed_input(self):
        self.bridge._apply_transcript(0, "open settings")
        self.bridge.gui._submit_text.assert_called_once_with("open settings")

    def test_wake_and_command_in_one_utterance_is_submitted_once(self):
        self.bridge._apply_voice_event(0, "wake", "open calculator")
        self.bridge.gui._restore.assert_not_called()
        self.bridge.gui._submit_text.assert_called_once_with("open calculator")
        self.assertFalse(self.bridge.audio.configure_listening.call_args.args[2])

    def test_wake_alone_starts_manual_turn_and_stop_cancels_it(self):
        self.bridge._apply_voice_event(0, "wake", "")
        self.bridge.gui._restore.assert_not_called()
        self.bridge.gui.show.assert_not_called()
        self.assertTrue(self.bridge.audio.configure_listening.call_args.args[2])
        generation = self.bridge.mic_generation
        self.bridge.handle_mic_toggle(False)
        self.bridge._apply_voice_event(generation, "transcript", "stale command")
        self.bridge.gui._submit_text.assert_not_called()

    def test_wake_disabled_still_allows_manual_mic(self):
        self.bridge.gui.wake_enabled = False
        self.bridge.handle_mic_toggle(True)
        args = self.bridge.audio.configure_listening.call_args.args
        self.assertEqual(args[1:3], (False, True))

    def test_request_uses_gui_context_and_respects_muted_replies(self):
        self.bridge.engine.process_request.return_value = "hello back"
        self.bridge.handle_user_text("hello")
        self.bridge.requests.shutdown(wait=True)
        self.app.processEvents()
        self.bridge.engine.process_request.assert_called_once_with(
            "hello", companion_name="AI-ru", history=[],
            system_prompt=self.bridge.gui.get_system_prompt.return_value, on_chunk=ANY)
        self.bridge.audio.speak.assert_not_called()
        self.bridge.gui._do_add_message.assert_called_once_with("ai-ru", "hello back")

    def test_partial_reply_is_not_saved_as_a_message(self):
        self.bridge._apply_result(0, "partial", "hello")
        self.bridge.gui.show_stream_preview.assert_called_once_with("hello")
        self.bridge.gui._do_add_message.assert_not_called()
        self.bridge.handle_new_session()
        self.bridge._apply_result(0, "partial", "stale")
        self.bridge.gui.show_stream_preview.assert_called_once()

    def test_cancel_task_does_not_wait_in_request_queue(self):
        self.bridge.handle_user_text("cancel the plan")
        self.bridge.engine.tasks.reset.assert_called_once()
        self.bridge.engine.process_request.assert_not_called()
        self.assertEqual(self.bridge.task_generation, 1)

    def test_speech_does_not_block_the_next_request(self):
        import threading
        started, release = threading.Event(), threading.Event()
        self.bridge.gui.speak_replies = True
        def speak(*args, **kwargs):
            started.set()
            release.wait(3)
        self.bridge.audio.speak.side_effect = speak
        self.bridge.engine.process_request.return_value = "hello"
        try:
            self.bridge.handle_user_text("first")
            self.assertTrue(started.wait(2))
            self.bridge.handle_user_text("second")
            self.bridge.requests.shutdown(wait=True)
            self.assertEqual(self.bridge.engine.process_request.call_count, 2)
        finally:
            release.set()


class AudioTests(unittest.TestCase):
    def test_startup_does_not_load_speech_models_or_audio_devices(self):
        with patch("audio_engine.WhisperModel") as model, patch("audio_engine.pyttsx3.init") as tts:
            AudioEngine()
            model.assert_not_called()
            tts.assert_not_called()

    def test_transcription_uses_memory_and_local_model_only(self):
        import numpy as np
        engine = AudioEngine()
        engine.whisper_model = MagicMock()
        engine.whisper_model.transcribe.return_value = ([MagicMock(text="hello")], None)
        self.assertEqual(engine.transcribe(np.zeros(480, dtype=np.int16).tobytes()), "hello")
        self.assertIsInstance(engine.whisper_model.transcribe.call_args.args[0], np.ndarray)
        engine.whisper_model = None
        with patch("audio_engine.WhisperModel") as model:
            engine._ensure_whisper()
            self.assertTrue(model.call_args.kwargs["local_files_only"])

    def test_transcription_failure_propagates_without_saving_audio(self):
        engine = AudioEngine()
        engine.whisper_model = MagicMock()
        engine.whisper_model.transcribe.side_effect = RuntimeError("model failed")
        with self.assertRaisesRegex(RuntimeError, "model failed"):
            engine.transcribe(bytes(960))

    def test_stop_interrupts_neural_output(self):
        import numpy as np
        engine = AudioEngine()
        engine.kokoro = MagicMock()
        engine.kokoro.create.return_value = (np.zeros(4096, dtype=np.float32), 24000)
        with patch("audio_engine.sd.OutputStream") as output:
            stream = output.return_value.__enter__.return_value
            stream.write.side_effect = lambda data: engine.stop_speaking()
            engine.speak("Hello there.", blocking=True)
            stream.write.assert_called_once()
            stream.abort.assert_called_once()
        self.assertFalse(engine.speaking.is_set())

    def test_neural_failure_uses_only_offline_fallback(self):
        engine = AudioEngine()
        engine.on_notice = MagicMock()
        with patch.object(engine, "_speak_neural", side_effect=RuntimeError("missing model")), \
             patch.object(engine, "_speak_windows") as fallback:
            engine.speak("Hello.", blocking=True)
            fallback.assert_called_once()
        engine.on_notice.assert_called_once()

    def test_capture_routes_wake_and_stops_old_capture(self):
        from unittest.mock import Mock
        engine = AudioEngine()
        config = ("Hey Airu", True, False, 7)
        engine.configure_listening(*config)
        engine.on_event = Mock()
        engine.transcribe = Mock(return_value="Hey, Iru, open calculator.")
        voice, silence = b"\x01\x00" * 480, bytes(960)
        class Input:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]
            def __enter__(self):
                for frame in [voice] * 45 + [silence] * 40:
                    self.callback(frame, 480, None, None)
                return self
            def __exit__(self, *args):
                pass
        vad = Mock()
        vad.is_speech.side_effect = lambda frame, rate: frame == voice
        with patch("audio_engine.sd.RawInputStream", Input):
            engine._capture(config, vad)
        engine.on_event.assert_any_call(7, "wake", "open calculator")
        self.assertEqual(engine._config()[1:3], (False, False))

        # A settings change during recognition invalidates the entire result.
        engine.configure_listening(*config)
        engine.on_event.reset_mock()
        def stale(pcm, **kwargs):
            engine.configure_listening("Hey Nova", False, False, 8)
            return "Hey Airu, open calculator."
        engine.transcribe.side_effect = stale
        with patch("audio_engine.sd.RawInputStream", Input):
            engine._capture(config, vad)
        self.assertFalse(any(call.args[1] == "wake" for call in engine.on_event.call_args_list))

    def test_wake_recognition_uses_english_and_filters_low_confidence(self):
        from types import SimpleNamespace
        engine = AudioEngine()
        engine.whisper_model = MagicMock()
        engine.whisper_model.transcribe.return_value = ([
            SimpleNamespace(text="Hey Iru", no_speech_prob=0.01, avg_logprob=-0.2),
            SimpleNamespace(text="hallucination", no_speech_prob=0.9, avg_logprob=-2.0)], None)
        result = engine.transcribe(bytes(960), wake_phrase="Hey Airu")
        self.assertEqual(result, "Hey Iru")
        options = engine.whisper_model.transcribe.call_args.kwargs
        self.assertEqual(options["language"], "en")
        self.assertFalse(options["vad_filter"])

    def test_disabled_listener_never_opens_microphone(self):
        import threading
        engine = AudioEngine()
        with patch("audio_engine.sd.RawInputStream") as microphone:
            engine.start_listener(MagicMock())
            threading.Event().wait(0.05)
            engine.close()
            engine.listener.join(timeout=1)
            microphone.assert_not_called()
            self.assertFalse(engine.listener.is_alive())
