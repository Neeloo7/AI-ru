"""Explicit integration check: creates and controls only its own disposable window.

Run from the project root with the project's Python. Requires an interactive desktop
and the configured local Ollama text/vision models. No user files are edited.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_controls import DesktopController
from routines import RoutineStore
from screen_awareness import ScreenAwareness
from task_planner import TaskPlanner


FIXTURE = '''
import sys
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton
app = QApplication([])
class FixtureWindow(QWidget):
    def closeEvent(self, event):
        # The parent owns this short-lived fixture and terminates its process.
        event.ignore()
window = FixtureWindow()
window.setWindowTitle(sys.argv[1])
window.resize(480, 260)
layout = QVBoxLayout(window)
label = QLabel('Details are closed')
label.setAccessibleName('Details are closed')
button = QPushButton('Show details')
def reveal():
    label.setText('Details are open')
    label.setAccessibleName('Details are open')
button.clicked.connect(reveal)
layout.addWidget(label)
layout.addWidget(button)
window.show()
app.exec()
'''


def main():
    title = "AIru disposable screen test " + uuid.uuid4().hex[:8]
    process = subprocess.Popen([sys.executable, "-c", FIXTURE, title])
    desktop = DesktopController()
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            result = desktop.execute(("focus", title))
            if result.startswith("Controlling:"):
                break
            time.sleep(0.2)
        else:
            raise AssertionError(result)
        before = desktop.observe(image=True)
        assert before["window"] == title
        assert before.get("image"), before.get("image_note")
        assert any(c["name"] == "Show details" for c in before["controls"])
        print("PASS: real accessibility observation and in-memory window capture", flush=True)

        config = json.loads(Path(__file__).resolve().parents[1].joinpath("config.json").read_text())
        url = "http://localhost:11434/api/chat"
        description = ScreenAwareness().describe(desktop, config.get("vision_model", "gemma3:4b"), url,
                                                "Describe the button and the status text in this test window.")
        assert description.startswith("Screen observation"), description
        print(description, flush=True)
        assert process.poll() is None, f"Fixture exited during vision inference: {process.returncode}"
        # Vision inference can take long enough for another window to gain focus.
        result = desktop.execute(("focus", title))
        assert result.startswith("Controlling:"), result

        planner = TaskPlanner()
        context = desktop.observe()
        proposal = planner.propose("Click Show details in this window.", config.get("model_name", "qwen2.5:7b"),
                                   url, context=context)
        assert planner.pending, proposal
        print(proposal, flush=True)
        with tempfile.TemporaryDirectory() as directory:
            planner.routines = RoutineStore(Path(directory) / "routines.json")
            assert planner.routine("save_routine", "show test details").startswith("Saved routine")
            report = planner.run(lambda command: "Unexpected system command", desktop.execute, observe=desktop.observe)
            print(report, flush=True)
            after = desktop.observe()
            assert any(c["name"] == "Details are open" for c in after["controls"]), "The real button did not change the status."
            assert desktop.execute(("wait", "Details are open")).startswith("Verified visible control")
            assert "Stopped" in report, "Unverified input must stop execution."
            recovery = planner.recover(desktop, config.get("model_name", "qwen2.5:7b"), url)
            print("Recovery proposal: " + recovery, flush=True)
            # Recovery only proposes; it must not mutate the fixture.
            unchanged = desktop.observe()
            assert any(c["name"] == "Details are open" for c in unchanged["controls"])
            assert planner.routine("load_routine", "show test details").startswith("Proposed steps")
        print("PASS: local vision, observed plan, actual click, fresh observation, recovery and routine round-trip", flush=True)
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()
