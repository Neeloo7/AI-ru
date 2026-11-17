import sys
import os
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog
from airu_gui import AiruGUI, AiruSetupDialog
from core_engine import AiruEngine
from audio_engine import AudioEngine, VOICES
from task_planner import parse_task_command

class AiruAppBridge(QObject):
    _result = pyqtSignal(int, str, str)
    _transcript = pyqtSignal(int, str)
    _voice_event = pyqtSignal(int, str, str)
    _speech_state = pyqtSignal(int, int, str)

    def __init__(self, companion_name="AI-ru"):
        super().__init__()
        self.companion_name = companion_name
        self.gui = AiruGUI(companion_name=companion_name)
        
        self.engine = AiruEngine()
        self.audio = AudioEngine()
        self.requests = ThreadPoolExecutor(max_workers=1)
        self.speech = ThreadPoolExecutor(max_workers=1)
        self.session_generation = 0
        self.mic_generation = 0
        self.speech_generation = 0
        self.voice_input = False
        self.manual_listening = False
        self.task_generation = 0
        self._result.connect(self._apply_result, Qt.ConnectionType.QueuedConnection)
        self._transcript.connect(self._apply_transcript, Qt.ConnectionType.QueuedConnection)
        
        self._voice_event.connect(self._apply_voice_event, Qt.ConnectionType.QueuedConnection)
        self._speech_state.connect(self._apply_speech_state, Qt.ConnectionType.QueuedConnection)

        # Connect GUI signals to bridge handlers
        self.gui.set_mic_callback(self.handle_mic_toggle)
        self.gui.set_send_callback(self.handle_user_text)
        self.gui.set_new_session_callback(self.handle_new_session)
        self.gui.set_voice_callback(self.audio.set_voice)
        self.gui.set_wake_word_callback(self.handle_wake_settings)
        self.gui.set_settings_callback(self.handle_audio_setting)
        self.audio.set_voice(self.gui.voice)
        self.gui.set_voices(list(VOICES), self.audio.voice)
        self.audio.on_notice = self.gui.notify
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def shutdown(self):
        self.engine.tasks.reset()
        self.session_generation += 1
        self.mic_generation += 1
        self.audio.close()
        self.requests.shutdown(wait=False, cancel_futures=True)
        self.speech.shutdown(wait=False, cancel_futures=True)

    def handle_user_text(self, text):
        task_intent = parse_task_command(text)
        if task_intent and task_intent[0] == "cancel":
            # Do not queue cancellation behind the task it needs to stop.
            self.engine.tasks.reset()
            self.task_generation += 1
            self.gui._do_add_message("ai-ru", "Cancellation requested. Any current Windows operation may finish; no subsequent step will start.")
            return
        generation = self.session_generation
        task_generation = self.task_generation
        speak = self.gui.speak_replies
        system_prompt = self.gui.get_system_prompt()
        if self.voice_input:
            system_prompt += ("\nThis is a spoken conversation. Use natural, short sentences and contractions. "
                              "Avoid markdown, bullet lists, and reading URLs aloud. Ask one question at a time. "
                              "Keep the first response brief unless detail is requested; don't pretend to be human.")
        speech_generation = self.speech_generation
        history = [{"role": "user" if sender in ("user", "you") else "assistant", "content": content}
                   for sender, content in self.gui.current["msgs"] if sender != "settings"]
        if history and history[-1] == {"role": "user", "content": text}:
            history.pop()
        def _worker():
            if generation != self.session_generation:
                return
            if task_intent and task_generation != self.task_generation:
                return
            self._result.emit(generation, "status", "THINKING")
            try:
                response = self.engine.process_request(text, companion_name=self.companion_name,
                                                       history=history, system_prompt=system_prompt,
                                                       on_chunk=lambda partial: self._result.emit(generation, "partial", partial))
            
                if generation != self.session_generation:
                    return
                self._result.emit(generation, "ai-ru", response)
                if speak:
                    def say_reply():
                        if generation == self.session_generation and speech_generation == self.speech_generation:
                            self._speech_state.emit(generation, speech_generation, "SPEAKING")
                            try:
                                self.audio.speak(response, blocking=True)
                            finally:
                                self._speech_state.emit(generation, speech_generation, "IDLE")
                    self.speech.submit(say_reply)
            except Exception as exc:
                if generation == self.session_generation:
                    self._result.emit(generation, "ai-ru", f"Request failed: {exc}")
            finally:
                self._result.emit(generation, "status", "IDLE")
            
            
        self.requests.submit(_worker)

    def _apply_result(self, generation, kind, text):
        if generation != self.session_generation:
            return
        if kind == "status":
            if not self.manual_listening:
                if text == "IDLE" and self.audio.speaking.is_set():
                    text = "SPEAKING"
                self.gui._apply_status(text)
        elif kind == "partial":
            self.gui.show_stream_preview(text)
        else:
            self.gui.clear_stream_preview()
            self.gui._do_add_message(kind, text)

    def _apply_speech_state(self, generation, speech_generation, state):
        if generation == self.session_generation and speech_generation == self.speech_generation and not self.manual_listening:
            self.gui._apply_status(state)

    def _apply_transcript(self, generation, text):
        if generation != self.mic_generation:
            return
        self.gui._apply_status("IDLE")
        self._configure_listener(False)
        if text:
            self.voice_input = True
            try:
                self.gui._submit_text(text)
            finally:
                self.voice_input = False
        else:
            self.gui.add_message("ai-ru", "I didn't catch that. Say the wake phrase or try the mic again.")

    def handle_mic_toggle(self, active):
        """The mic button interrupts speech and starts one pause-delimited turn."""
        self.speech_generation += 1
        self.audio.stop_speaking()
        self._configure_listener(active)
        self.gui.update_status("LISTENING" if active else "IDLE")
        if active:
            self.gui.add_thought("Listening until you finish speaking...")

    def _configure_listener(self, manual=False):
        self.manual_listening = manual
        self.mic_generation += 1
        self.audio.configure_listening(self.gui.wake_word, self.gui.wake_enabled, manual, self.mic_generation)
        if not manual and not self.gui.wake_enabled:
            self.gui.drawer.wake_status.setText("Wake listening is off.")
            self.gui.drawer.wake_level.setValue(0)

    def handle_wake_settings(self, phrase, enabled):
        self._configure_listener(False)
        self.gui.update_status("IDLE")
        self.gui.add_thought(f"Wake listening: {phrase}" if enabled else "Wake listening off. Use the mic button.")

    def handle_audio_setting(self, key, value):
        if key == "wake_test":
            self.audio.test_wake()
            self._configure_listener(False)
            self.gui.set_wake_diagnostic("Say the wake phrase within 20 seconds. Results stay in this panel.")
        if key == "audio_speak" and not value:
            self.speech_generation += 1
            self.audio.stop_speaking()
            if not self.manual_listening:
                self.gui.update_status("IDLE")

    def _apply_voice_event(self, generation, kind, text):
        if generation != self.mic_generation:
            return
        if kind == "level":
            self.gui.drawer.wake_level.setValue(int(text))
        elif kind == "wake_debug":
            self.gui.set_wake_diagnostic(text)
        elif kind == "wake":
            self.speech_generation += 1
            self.audio.stop_speaking()
            if text:
                self._apply_transcript(generation, text)
            else:
                self.handle_mic_toggle(True)
        elif kind == "transcript":
            self._apply_transcript(generation, text)
        elif kind == "error":
            self.manual_listening = False
            self.gui._apply_status("IDLE")
            self.gui.notify(text)
            self.gui.add_thought("Microphone unavailable. Check audio settings.")
            self.gui.drawer.wake_status.setText(text)
        elif kind == "timeout":
            self._configure_listener(False)
            self.gui._apply_status("IDLE")
            self.gui.notify(text)
        else:
            self.gui.add_thought(text)
            if kind == "ready":
                self.gui.drawer.wake_status.setText(text)

    def handle_new_session(self):
        self.engine.tasks.reset()
        self.engine.desktop.target = 0
        self.task_generation += 1
        self.session_generation += 1
        self.mic_generation += 1
        self.speech_generation += 1
        self.audio.stop_speaking()
        self._configure_listener(False)
        self.gui.update_status("IDLE")
        print(f"[{self.companion_name}] New session initialized.")

    def run(self):
        # Start the AiruGUI application window and main loop
        self.gui.start()
        self._configure_listener(False)
        self.audio.start_listener(self._voice_event.emit)
        if self.gui.speak_replies:
            self.speech.submit(self.audio.prepare_voice)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    CONFIG_PATH = Path(__file__).resolve().with_name("config.json")
    chosen_name = "AI-ru"
    
    # Check if first-run setup is needed
    is_first_run = True
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
                is_first_run = data.get("first_run", True)
                chosen_name = data.get("companion_name", "AI-ru")
        except Exception:
            is_first_run = True

    # 1. Initialize the bridge and main GUI first
    bridge = AiruAppBridge(companion_name=chosen_name)

    # 2. If it's first run, show our setup dialog
    if is_first_run:
        setup_dialog = AiruSetupDialog()
        if setup_dialog.exec() == QDialog.DialogCode.Accepted:
            chosen_name = setup_dialog.selected_config.get("companion_name", "AI-ru")
            bridge.companion_name = chosen_name
            bridge.gui.default_wake = f"Hey {chosen_name}"
            bridge.gui.set_wake_word(f"Hey {chosen_name}")

    # 3. Run the main application loop
    bridge.run()
    sys.exit(app.exec())
