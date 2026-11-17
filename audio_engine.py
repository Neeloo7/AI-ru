import threading
import time
import queue
import json
from pathlib import Path

import pyttsx3
from faster_whisper import WhisperModel
import sounddevice as sd
import numpy as np

from voice_activity import TurnDetector, split_wake, speech_text

try:
    import pythoncom
except ImportError:
    pythoncom = None

MODEL_DIR = Path(__file__).resolve().with_name("models")
VOICES = {
    "Heart (warm)": ("af_heart", "en-us"),
    "Bella (soft)": ("af_bella", "en-us"),
    "Michael (calm)": ("am_michael", "en-us"),
    "Emma (British)": ("bf_emma", "en-gb"),
    "George (British)": ("bm_george", "en-gb"),
    "Windows voice (offline fallback)": None,
}


class AudioEngine:
    def __init__(self):
        self.tts_lock = threading.Lock()
        self.record_lock = threading.Lock()
        self.whisper_model = None
        self.kokoro = None
        self.voice = "Heart (warm)"
        self.speaking = threading.Event()
        self.speech_epoch = 0
        self.echo_until = 0.0
        self.closed = threading.Event()
        self.changed = threading.Event()
        self.config_lock = threading.Lock()
        self.listening_config = ("Hey Airu", False, False, 0)
        self.listener = None
        self.on_event = lambda generation, kind, text: None
        self.on_notice = lambda text: None
        self.fallback_reported = False
        self.wake_debug_until = 0.0
        try:
            config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))
            self.pause_seconds = max(0.6, min(1.5, float(config.get("speech_pause_seconds", 1.0))))
        except (OSError, ValueError, TypeError, AttributeError):
            self.pause_seconds = 1.0

    def set_voice(self, name):
        self.voice = name if name in VOICES else "Heart (warm)"

    def stop_speaking(self):
        self.speech_epoch += 1

    def speak(self, text, blocking=False):
        text = speech_text(text)
        if not text or text.count("@") > 3:
            return
        epoch = self.speech_epoch

        def run():
            with self.tts_lock:
                if epoch != self.speech_epoch or self.closed.is_set():
                    return
                self.speaking.set()
                try:
                    if self.voice == "Windows voice (offline fallback)":
                        self._speak_windows(text, epoch)
                    else:
                        self._speak_neural(text, epoch)
                except Exception as exc:
                    if not self.fallback_reported:
                        self.on_notice(f"Natural voice unavailable ({exc}). Using the offline Windows voice. Run setup_voice.py to install models.")
                        self.fallback_reported = True
                    if epoch == self.speech_epoch and not self.closed.is_set():
                        try:
                            self._speak_windows(text, epoch)
                        except Exception as fallback_error:
                            self.on_notice(f"Speech output failed: {fallback_error}")
                finally:
                    self.echo_until = time.monotonic() + 0.4
                    self.speaking.clear()
        if blocking:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()

    def _ensure_voice(self):
        if self.kokoro is None:
            from kokoro_onnx import Kokoro
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(MODEL_DIR / "kokoro-v1.0.int8.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])
            self.kokoro = Kokoro.from_session(session, str(MODEL_DIR / "voices-v1.0.bin"))

    def prepare_voice(self):
        with self.tts_lock:
            if self.closed.is_set() or self.voice == "Windows voice (offline fallback)":
                return
            try:
                self._ensure_voice()
                voice, language = VOICES.get(self.voice) or VOICES["Heart (warm)"]
                self.kokoro.create("Hello.", voice=voice, lang=language)
            except Exception as exc:
                self.on_notice(f"Natural voice isn't ready: {exc}. Windows speech is available as a fallback.")

    def _speak_neural(self, text, epoch):
        self._ensure_voice()
        voice, language = VOICES.get(self.voice) or VOICES["Heart (warm)"]
        # Sentence-sized chunks reduce time to the first sound and bound cancellation latency.
        import re
        import textwrap
        sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
        for sentence in sentences:
            for chunk in textwrap.wrap(sentence, width=240, break_long_words=False, break_on_hyphens=False):
                if epoch != self.speech_epoch or self.closed.is_set():
                    return
                samples, rate = self.kokoro.create(chunk, voice=voice, speed=1.0, lang=language)
                if epoch != self.speech_epoch or self.closed.is_set():
                    return
                with sd.OutputStream(samplerate=rate, channels=1, dtype="float32") as stream:
                    for offset in range(0, len(samples), 1024):
                        if epoch != self.speech_epoch or self.closed.is_set():
                            stream.abort()
                            return
                        stream.write(np.asarray(samples[offset:offset + 1024], dtype=np.float32).reshape(-1, 1))

    def _speak_windows(self, text, epoch):
        if pythoncom:
            pythoncom.CoInitialize()
        engine = None
        loop_started = False
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.say(text)
            engine.startLoop(False)
            loop_started = True
            while engine.isBusy() and epoch == self.speech_epoch and not self.closed.is_set():
                engine.iterate()
                time.sleep(0.01)
        finally:
            if engine:
                engine.stop()
                if loop_started:
                    engine.endLoop()
            if pythoncom:
                pythoncom.CoUninitialize()

    def _ensure_whisper(self):
        if self.whisper_model is None:
            local = MODEL_DIR / "whisper-base"
            self.whisper_model = WhisperModel(str(local) if local.exists() else "base", device="cpu",
                                              compute_type="int8", cpu_threads=2, local_files_only=True)

    def transcribe(self, pcm, wake_phrase=None):
        if not pcm:
            return ""
        with self.record_lock:
            self._ensure_whisper()
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            options = {"beam_size": 1, "vad_filter": True, "condition_on_previous_text": False}
            if wake_phrase:
                # Very short greetings have unreliable language detection; audio is already VAD-gated.
                options.update(language="en", beam_size=3, vad_filter=False, temperature=0.0,
                               initial_prompt=f"The assistant is called {wake_phrase.removeprefix('Hey ').removeprefix('Hey, ')}.")
            segments, _ = self.whisper_model.transcribe(audio, **options)
            if wake_phrase:
                segments = (segment for segment in segments
                            if segment.no_speech_prob < 0.6 and segment.avg_logprob > -1.2)
            return " ".join(segment.text.strip() for segment in segments).strip()

    def test_wake(self):
        self.wake_debug_until = time.monotonic() + 20

    def configure_listening(self, phrase, enabled, manual, generation):
        with self.config_lock:
            self.listening_config = (phrase, bool(enabled), bool(manual), generation)
        self.changed.set()

    def start_listener(self, on_event):
        self.on_event = on_event
        if self.listener is None or not self.listener.is_alive():
            self.listener = threading.Thread(target=self._listen_loop, daemon=True, name="airu-local-listener")
            self.listener.start()

    def close(self):
        self.closed.set()
        self.stop_speaking()
        self.changed.set()

    def _config(self):
        with self.config_lock:
            return self.listening_config

    def _listen_loop(self):
        while not self.closed.is_set():
            self.changed.clear()
            config = self._config()
            phrase, enabled, manual, generation = config
            if not (enabled or manual):
                self.changed.wait(0.2)
                continue
            try:
                import webrtcvad
                self._ensure_whisper()
                if self.closed.is_set() or config != self._config():
                    continue
                self._capture(config, webrtcvad.Vad(1))
            except Exception as exc:
                self.on_event(generation, "error", f"Microphone listening stopped: {exc}. Check microphone access and run setup_voice.py if models are missing.")
                # Retry only after a user setting changes, not in a failing-device loop.
                while not self.closed.is_set() and config == self._config():
                    self.changed.wait(0.2)
                    self.changed.clear()

    def _capture(self, config, vad):
        phrase, enabled, manual, generation = config
        frames = queue.Queue(maxsize=100)
        overflowed = threading.Event()
        def receive(data, count, timing, status):
            if status:
                overflowed.set()
            try:
                frames.put_nowait(bytes(data))
            except queue.Full:
                overflowed.set()
        detector = TurnDetector(self.pause_seconds)
        began = time.monotonic()
        last_level = 0.0
        diagnostic_turn = False
        with sd.RawInputStream(samplerate=16000, blocksize=480, channels=1, dtype="int16", callback=receive):
            self.on_event(generation, "ready", "Listening" if manual else f"Waiting for {phrase}")
            while not self.closed.is_set() and config == self._config():
                try:
                    frame = frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                now = time.monotonic()
                if now - last_level >= 0.25:
                    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(samples * samples)))
                    level = max(0, min(100, round((20 * np.log10(max(rms, 1e-6)) + 60) * 100 / 60)))
                    self.on_event(generation, "level", str(level))
                    last_level = now
                if self.speaking.is_set() or time.monotonic() < self.echo_until:
                    detector = TurnDetector(self.pause_seconds)
                    began = time.monotonic()
                    continue
                if overflowed.is_set():
                    # Never join separated audio into a potentially different command.
                    overflowed.clear()
                    detector = TurnDetector(self.pause_seconds)
                    while not frames.empty():
                        frames.get_nowait()
                    continue
                was_started = detector.started
                pcm = detector.feed(frame, vad.is_speech(frame, 16000))
                if detector.started and not was_started:
                    diagnostic_turn = time.monotonic() < self.wake_debug_until
                if manual and not detector.started and time.monotonic() - began > 12:
                    self._finish_capture(config, "transcript", "")
                    return
                if pcm is None:
                    continue
                if detector.limit_reached:
                    if manual:
                        self._finish_capture(config, "timeout", "That turn exceeded 45 seconds. Please split it into shorter requests; nothing was submitted.")
                        return
                    detector = TurnDetector(self.pause_seconds)
                    continue
                if manual:
                    self.on_event(generation, "transcribing", "Understanding your words...")
                text = self.transcribe(pcm) if manual else self.transcribe(pcm, wake_phrase=phrase)
                if self.closed.is_set() or config != self._config():
                    return
                if manual:
                    self._finish_capture(config, "transcript", text)
                    return
                heard, remainder = split_wake(text, phrase)
                if diagnostic_turn:
                    self.on_event(generation, "wake_debug", f"Heard: {text or '(no confident words)'} — "
                                  + ("wake phrase matched" if heard else "no wake match"))
                    detector = TurnDetector(self.pause_seconds)
                    began = time.monotonic()
                    continue
                if heard:
                    self._finish_capture(config, "wake", remainder)
                    return
                # Unaddressed speech is discarded, never sent to the chat engine or database.
                detector = TurnDetector(self.pause_seconds)
                began = time.monotonic()

    def _finish_capture(self, config, kind, text):
        with self.config_lock:
            if config != self.listening_config or self.closed.is_set():
                return
            phrase, enabled, manual, generation = config
            # Wait for the GUI to decide the next mode, avoiding duplicate activations.
            self.listening_config = (phrase, False, False, generation)
        self.on_event(generation, kind, text)
