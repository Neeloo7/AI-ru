import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import airu_gui
from desktop_controls import window_observation
from routines import RoutineStore
from screen_awareness import ScreenAwareness, observation_text, parse_screen_command
from task_planner import TaskPlanner, parse_task_command, validate_steps


def snapshot(name="Test window"):
    return {"window": name, "handle": 123, "controls": [
        {"name": "Show details", "type": "Button", "enabled": True}],
        "protected": False, "truncated": False}


def response_for(steps):
    response = Mock()
    response.json.return_value = {"message": {"content": json.dumps({"steps": steps, "limitation": ""})}}
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


class ScreenAndRoutineTests(unittest.TestCase):
    def test_password_subtrees_are_not_read(self):
        protected = Mock()
        protected.is_visible.return_value = True
        protected.element_info.element.CurrentIsPassword = True
        normal = Mock()
        normal.is_visible.return_value = True
        normal.element_info.element.CurrentIsPassword = False
        normal.element_info.control_type = "Button"
        normal.window_text.return_value = "Show details"
        normal.is_enabled.return_value = True
        normal.children.return_value = []
        window = Mock(handle=123)
        window.window_text.return_value = "Test window"
        window.children.return_value = [protected, normal]
        observed = window_observation(window)
        protected.window_text.assert_not_called()
        protected.children.assert_not_called()
        self.assertTrue(observed["protected"])
        self.assertEqual(observed["controls"][0]["name"], "Show details")

    def test_local_vision_has_image_separate_from_untrusted_text(self):
        observed = {**snapshot(), "image": "fake-base64"}
        desktop = Mock()
        desktop.observe.return_value = observed
        response = response_for([])
        response.json.return_value = {"message": {"content": "A test window with a details button."}}
        with patch("screen_awareness.requests.post", return_value=response) as post:
            result = ScreenAwareness().describe(desktop, "local-vision", "http://localhost:11434/api/chat", "What is visible?")
        self.assertIn("No actions performed", result)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["messages"][1]["images"], ["fake-base64"])
        self.assertNotIn("fake-base64", payload["messages"][1]["content"])
        self.assertIn("untrusted", payload["messages"][0]["content"])
        self.assertNotIn("fake-base64", observation_text(observed))
        desktop.execute.assert_not_called()

    def test_contextual_plan_rejects_unobserved_click_and_target(self):
        for steps in (["switch to Other app", "click Show details"],
                      ["switch to Test window", "click Delete everything"]):
            planner = TaskPlanner()
            with patch("task_planner.requests.post", return_value=response_for(steps)):
                text = planner.propose("Show details", "local", "http://localhost/api/chat", context=snapshot())
            self.assertIn("Could not prepare", text)
            self.assertIsNone(planner.pending)

    def test_context_binds_missing_focus_to_observed_window(self):
        planner = TaskPlanner()
        with patch("task_planner.requests.post", return_value=response_for(["click Show details"])):
            planner.propose("Show details", "local", "http://localhost/api/chat", context=snapshot())
        self.assertEqual(planner.pending[0][2], ("focus", "Test window"))

    def test_changed_screen_invalidates_plan_before_any_action(self):
        planner = TaskPlanner()
        planner.pending = validate_steps({"steps": ["switch to Test window", "click Show details"], "limitation": ""})
        planner.pending_context = snapshot()
        desktop = Mock()
        result = planner.run(Mock(), desktop, observe=lambda: snapshot("Unexpected save dialog"))
        self.assertIn("window changed", result)
        desktop.assert_not_called()

    def test_input_can_only_continue_to_a_visible_state_check(self):
        planner = TaskPlanner()
        planner.pending = validate_steps({"steps": ["switch to Test window", "click Show details", "wait for control Details are open"], "limitation": ""})
        desktop = Mock(side_effect=["Controlling: Test window", "Sent click to Test window. Not verified.",
                                    "Verified visible control: Details are open."])
        result = planner.run(Mock(), desktop)
        self.assertEqual(desktop.call_count, 3)
        self.assertIn("all requested visible-control checks passed", result)
        planner.pending = validate_steps({"steps": ["switch to Test window", "click Show details", "wait for control Details are open", "maximize window"], "limitation": ""})
        desktop = Mock(side_effect=["Controlling: Test window", "Sent click to Test window. Not verified.", "Desktop navigation failed: wait timed out"])
        self.assertIn("Stopped", planner.run(Mock(), desktop))
        self.assertEqual(desktop.call_count, 3)

    def test_recovery_observes_and_proposes_without_executing(self):
        planner = TaskPlanner()
        planner.last_goal = "Show the details"
        planner.last_report = "Sent click; outcome not verified."
        desktop = Mock()
        desktop.observe.return_value = snapshot()
        with patch("task_planner.requests.post", return_value=response_for(["switch to Test window", "click Show details"])) as post:
            result = planner.recover(desktop, "local", "http://localhost/api/chat")
        self.assertIn("nothing has run", result)
        self.assertIn("PREVIOUS TASK RESULTS", post.call_args.kwargs["json"]["messages"][-1]["content"])
        desktop.execute.assert_not_called()

    def test_cancellation_during_observation_cannot_arm_a_recovery_plan(self):
        planner = TaskPlanner()
        planner.last_goal = "Show details"
        planner.last_report = "Stopped"
        desktop = Mock()
        def observe():
            planner.reset()
            return snapshot()
        desktop.observe.side_effect = observe
        with patch("task_planner.requests.post") as model:
            result = planner.recover(desktop, "local", "http://localhost/api/chat")
        self.assertIn("cancelled", result)
        self.assertIsNone(planner.pending)
        model.assert_not_called()

    def test_routines_survive_restart_and_revalidate_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routines.json"
            planner = TaskPlanner()
            planner.routines = RoutineStore(path)
            planner.pending = validate_steps({"steps": ["set volume to 30"], "limitation": ""})
            self.assertIn("Saved routine", planner.routine("save_routine", "Focus Mode"))
            planner2 = TaskPlanner()
            planner2.routines = RoutineStore(path)
            self.assertIn("set volume to 30", planner2.routine("load_routine", "focus mode"))
            self.assertIn("focus mode", planner2.routine("list_routines"))
            planner2.routines.save("bad", ["open cmd.exe"])
            self.assertIn("failed", planner2.routine("load_routine", "bad"))
            self.assertIsNone(planner2.pending)
            self.assertIn("Deleted", planner2.routine("delete_routine", "Focus Mode"))
            self.assertNotIn("focus mode", planner2.routines.read())

    def test_corrupt_routine_storage_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routines.json"
            path.write_text("corrupt", encoding="utf-8")
            store = RoutineStore(path)
            with self.assertRaises(ValueError):
                store.save("test", ["set volume to 30"])
            self.assertEqual(path.read_text(), "corrupt")
            with self.assertRaises(ValueError):
                store.save("../escape", [])

    def test_voice_routes_do_not_get_consumed_by_gui_preferences(self):
        for text in ("look at the screen", "look at screen: why is this dialog open?",
                     "plan from screen: click Show details", "recover task", "save plan as focus mode", "load routine focus mode"):
            self.assertIsNone(airu_gui.parse_command(text))
        self.assertIsNotNone(parse_screen_command("What do you see?"))
        self.assertEqual(parse_task_command("load routine focus mode"), ("load_routine", "focus mode"))
