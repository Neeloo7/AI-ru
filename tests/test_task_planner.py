import json
import unittest
from unittest.mock import Mock, patch

import airu_gui
from task_planner import TaskPlanner, parse_task_command, validate_steps


class TaskPlannerTests(unittest.TestCase):
    def proposal(self, steps, limitation=""):
        return {"steps": steps, "limitation": limitation}

    def test_plan_is_preview_only_and_runs_once(self):
        planner = TaskPlanner()
        response = Mock()
        response.json.return_value = {"message": {"content": json.dumps(self.proposal(["turn Bluetooth on", "set volume to 30"]))}}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("task_planner.requests.post", return_value=response):
            preview = planner.propose("enable Bluetooth and set volume to 30", "test-model", "http://localhost:11434/api/chat")
        self.assertIn("nothing has run", preview)
        control = Mock(return_value="Verified output: On")
        desktop = Mock()
        result = planner.run(control, desktop)
        self.assertEqual(control.call_count, 2)
        desktop.assert_not_called()
        self.assertIn("All steps returned verified", result)
        planner.run(control, desktop)
        self.assertEqual(control.call_count, 2)

    def test_all_steps_validated_before_execution(self):
        for steps in (["turn Bluetooth on", "powershell Remove-Item C:\\Work"],
                      ["press Ctrl+S"], ["open cmd.exe"], ["close window"],
                      ["set volume to 900"], ["set brightness to 900"],
                      ["turn Bluetooth on"] * 9):
            with self.assertRaises(ValueError):
                validate_steps(self.proposal(steps))
        with self.assertRaisesRegex(ValueError, "Task not prepared"):
            validate_steps(self.proposal(["turn Bluetooth on"], "Cannot perform the rest."))

    def test_failure_and_unverified_input_stop_remaining_steps(self):
        for outcome in ("Windows did not apply the mute change; verification failed.", "Sent input; state not verified."):
            planner = TaskPlanner()
            planner.pending = validate_steps(self.proposal(["unmute volume", "set volume to 30"]))
            control = Mock(return_value=outcome)
            report = planner.run(control, Mock())
            control.assert_called_once()
            self.assertIn("Stopped", report)

    def test_cancel_after_first_step_stops_second(self):
        planner = TaskPlanner()
        planner.pending = validate_steps(self.proposal(["unmute volume", "set volume to 30"]))
        def first(command):
            planner.reset()
            return "Verified output: unmuted"
        control = Mock(side_effect=first)
        report = planner.run(control, Mock())
        control.assert_called_once()
        self.assertIn("Cancelled before", report)

    def test_exception_is_reported_and_stops_task(self):
        planner = TaskPlanner()
        planner.pending = validate_steps(self.proposal(["unmute volume", "set volume to 30"]))
        report = planner.run(Mock(side_effect=RuntimeError("device disappeared")), Mock())
        self.assertIn("device disappeared", report)
        self.assertIn("Stopped", report)

    def test_plan_routing_does_not_run_gui_settings(self):
        text = "Plan a task: open display settings and set brightness to 60"
        self.assertEqual(parse_task_command(text)[0], "propose")
        self.assertIsNone(airu_gui.parse_command(text))
        self.assertEqual(parse_task_command("Please run the plan.")[0], "run")

    def test_ui_plan_requires_explicit_focus(self):
        valid = validate_steps(self.proposal(["switch to Notepad", 'type "Hello there"']))
        self.assertEqual(valid[-1][1], "desktop")
        with self.assertRaises(ValueError):
            validate_steps(self.proposal(['type "Hello there"']))
