"""Local, deterministic turn boundaries and wake-phrase matching."""
from collections import deque
import re


def wake_pattern(phrase):
    letters = re.sub(r"[^a-z0-9]", "", phrase.lower())
    if not letters:
        return None
    if letters == "heyairu":
        body = r"hey[\W_]*(?:ai|eye|ay|a|i)[\W_]*r(?:u|oo|ew|ue)"
    else:
        body = r"[\W_]*".join(re.escape(c) for c in letters)
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", re.I)


def split_wake(text, phrase):
    pattern = wake_pattern(phrase)
    match = pattern.match(text.strip()) if pattern else None
    return (True, text.strip()[match.end():].strip(" ,.:;!?")) if match else (False, "")


class TurnDetector:
    """30 ms PCM frames; retain word onsets and allow pauses within a turn."""
    FRAME_SECONDS = 0.03

    def __init__(self, pause_seconds=1.0, max_seconds=45):
        self.pause_seconds = pause_seconds
        self.max_seconds = max_seconds
        self.preroll = deque(maxlen=10)
        self.frames = []
        self.speech_frames = 0
        self.silence_frames = 0
        self.recent = deque(maxlen=5)
        self.started = False
        self.limit_reached = False

    def feed(self, frame, is_speech):
        self.recent.append(bool(is_speech))
        if not self.started:
            self.preroll.append(frame)
            if len(self.recent) < 5 or sum(self.recent) < 3:
                return None
            self.started = True
            self.frames = list(self.preroll)
            self.speech_frames = sum(self.recent)
        else:
            self.frames.append(frame)
            self.speech_frames += int(is_speech)
        self.silence_frames = 0 if is_speech else self.silence_frames + 1
        # Longer thoughts get a little more breathing room, capped at 1.5 seconds.
        pause = min(1.5, self.pause_seconds + min(0.4, self.speech_frames * 0.03 / 30))
        ended = self.silence_frames * 0.03 >= pause
        limit = len(self.frames) * 0.03 >= self.max_seconds
        self.limit_reached = limit and not ended
        if ended or limit:
            return b"".join(self.frames) if self.speech_frames >= 8 else b""
        return None


def speech_text(text):
    text = re.sub(r"```.*?```", " I've put the code in the chat. ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "the link in the chat", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s*|[-*]\s+)", "", text)
    return re.sub(r"[*_`]+", "", text).strip()
