import unittest

from voice_activity import TurnDetector, split_wake, speech_text


class VoiceActivityTests(unittest.TestCase):
    def test_short_pauses_do_not_end_a_turn_and_onset_is_kept(self):
        detector = TurnDetector()
        for _ in range(10):
            self.assertIsNone(detector.feed(b"pre", False))
        for _ in range(20):
            self.assertIsNone(detector.feed(b"voice", True))
        for _ in range(20):
            self.assertIsNone(detector.feed(b"gap", False))
        for _ in range(20):
            self.assertIsNone(detector.feed(b"more", True))
        result = None
        for _ in range(55):
            result = detector.feed(b"end", False)
            if result is not None:
                break
        self.assertTrue(result.startswith(b"pre"))
        self.assertIn(b"voice", result)
        self.assertIn(b"more", result)

    def test_background_noise_and_short_clicks_do_not_form_utterances(self):
        detector = TurnDetector()
        for i in range(500):
            self.assertIsNone(detector.feed(b"noise", i % 20 == 0))
        self.assertFalse(detector.started)

    def test_longer_turn_gets_more_pause_time(self):
        short, long = TurnDetector(), TurnDetector()
        for _ in range(15):
            short.feed(b"v", True)
        for _ in range(400):
            long.feed(b"v", True)
        short_done = None
        for _ in range(37):
            short_done = short_done or short.feed(b"s", False)
            self.assertIsNone(long.feed(b"s", False))
        self.assertIsNotNone(short_done)

    def test_wake_phrase_requires_start_and_complete_name(self):
        for phrase in ("Hey, AI-ru", "Hey eye roo", "Hey Airu", "Hey, Iru"):
            self.assertEqual(split_wake(phrase + ", open calculator", "Hey AI-ru"), (True, "open calculator"))
        self.assertEqual(split_wake("Hey Nova!", "Hey Nova"), (True, ""))
        for text in ("I said hey Nova", "Hey Novaline", "turn on wifi"):
            self.assertEqual(split_wake(text, "Hey Nova"), (False, ""))

    def test_hard_limit_is_distinguished_from_finishing_a_turn(self):
        detector = TurnDetector(max_seconds=1)
        for _ in range(40):
            if detector.feed(b"speech", True) is not None:
                break
        self.assertTrue(detector.limit_reached)

    def test_speech_cleanup_preserves_words_and_removes_markup(self):
        result = speech_text("**Hello** [friend](https://example.com).\n```python\nprint(1)\n```")
        self.assertIn("Hello friend.", result)
        self.assertNotIn("print(1)", result)
        self.assertNotIn("https://", result)
