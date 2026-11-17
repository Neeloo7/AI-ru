"""Local Windows navigation through UI Automation, with explicit target windows."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


KEYS = {"enter": "{ENTER}", "tab": "{TAB}", "escape": "{ESC}", "esc": "{ESC}",
        "backspace": "{BACKSPACE}", "delete": "{DELETE}", "up": "{UP}", "down": "{DOWN}",
        "left": "{LEFT}", "right": "{RIGHT}", "home": "{HOME}", "end": "{END}",
        "page up": "{PGUP}", "page down": "{PGDN}", "space": "{SPACE}"}
MODIFIERS = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}
FOLDERS = {"documents": "Personal", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
           "desktop": "Desktop", "pictures": "My Pictures", "music": "My Music", "videos": "My Video"}
DESKTOP_HELP = (
    "Desktop navigation: list windows; switch to Notepad; show controls; click Save; "
    'type "Hello there"; press Ctrl+S; scroll down; maximize window; minimize window; '
    "close window; open my downloads; open folder C:\\Work. "
    "Select a target with 'switch to <window title>' before clicking or typing."
    " Use 'wait for control <exact visible name>' to verify an expected result (up to 8 seconds)."
)


def parse_desktop_command(text):
    text = re.sub(r"^(?:(?:please|can you|could you|would you)\s+)+", "", text.strip(), flags=re.I)
    p = text.lower().rstrip(".!? ")
    if p in ("list windows", "show windows", "list open windows"):
        return "list", None
    if p in ("show controls", "list controls", "describe window"):
        return "inspect", None
    m = re.fullmatch(r"wait for control (.+)", text, re.I)
    if m:
        return "wait", m[1].strip('"')
    m = re.fullmatch(r"(?:switch to|focus|control window) (.+)", text, re.I)
    if m:
        return "focus", m[1]
    m = re.fullmatch(r"(click|type|press) (.+)", text, re.I | re.S)
    if m:
        value = m[2]
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        return m[1].lower(), value
    m = re.fullmatch(r"(minimize|maximize|restore|close) (?:the |current )?window", p)
    if m:
        return m[1], None
    if p in ("scroll up", "scroll down"):
        return "press", "page up" if p.endswith("up") else "page down"
    m = re.fullmatch(r"open (?:my |the )?(documents|downloads|desktop|pictures|music|videos)(?: folder)?", p)
    if m:
        return "folder", m[1]
    m = re.fullmatch(r"open folder (.+)", text, re.I)
    if m:
        return "folder", m[1].strip('"')
    return None


def key_sequence(text):
    pieces = [part.strip().lower() for part in text.split("+")]
    if not pieces or len(pieces) > 4 or any(part not in MODIFIERS for part in pieces[:-1]):
        raise ValueError("Use a key such as Enter or a shortcut such as Ctrl+S.")
    key = pieces[-1]
    if key in KEYS:
        key = KEYS[key]
    elif re.fullmatch(r"f(?:[1-9]|1[0-2])", key):
        key = "{" + key.upper() + "}"
    elif not re.fullmatch(r"[a-z0-9]", key):
        raise ValueError("Unknown key. Try Enter, Escape, Tab, Page Down, or Ctrl+S.")
    return "".join(MODIFIERS[part] for part in pieces[:-1]) + key


def literal_keys(text):
    if not text or len(text) > 4000 or any(ord(c) < 32 for c in text):
        raise ValueError("Type one line of up to 4,000 characters. Use 'press Enter' separately.")
    return "".join("{" + c + "}" if c in "{}+^%~()" else c for c in text)


def select_unique(items, text, label):
    exact = [item for item in items if label(item).casefold() == text.casefold()]
    matches = exact or [item for item in items if text.casefold() in label(item).casefold()]
    if not matches:
        raise ValueError(f"No visible match for '{text}'.")
    if len(matches) != 1:
        raise ValueError("More than one match; use a more specific name: " + "; ".join(label(item) for item in matches[:8]))
    return matches[0]


def folder_path(value):
    if value not in FOLDERS:
        path = Path(os.path.expandvars(value)).expanduser()
        if not path.is_absolute():
            raise ValueError("Specify an absolute folder path, or say 'open my downloads'.")
        return path
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
        value = winreg.QueryValueEx(key, FOLDERS[value])[0]
    return Path(os.path.expandvars(value))


class DesktopController:
    def __init__(self):
        self.target = 0

    def _request(self, action, value=None, **extra):
        request = {"action": action, "value": value, "target": self.target, **extra}
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                                input=json.dumps(request), capture_output=True, text=True, encoding="utf-8",
                                timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError(result.stderr.strip()[-500:] or "Desktop helper failed.")
        return json.loads(result.stdout)

    def observe(self, image=False):
        if os.name != "nt":
            raise ValueError("Screen awareness requires Windows.")
        try:
            response = self._request("observe", image=image)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("The window did not respond to inspection within 20 seconds.") from exc
        if "observation" not in response:
            raise ValueError(response.get("message", "Could not inspect the window."))
        return response["observation"]

    def execute(self, intent):
        if os.name != "nt":
            return "Desktop navigation requires Windows."
        action, value = intent
        try:
            if action == "folder":
                path = folder_path(value)
                if not path.is_dir():
                    return f"Folder not found: {path}"
                os.startfile(str(path))
                return f"Windows accepted the request to open folder: {path}"
            response = self._request(action, value)
            self.target = response.get("target", self.target)
            return response["message"]
        except subprocess.TimeoutExpired:
            return "The app did not respond to Windows automation. Check its state before retrying."
        except (OSError, ValueError) as exc:
            return f"Desktop navigation failed: {exc}"


def perform(request):
    # A separate process bounds hung accessibility providers and isolates COM from Qt.
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys
    import win32gui
    action, value, target = request["action"], request.get("value"), request.get("target", 0)
    desktop = Desktop(backend="uia")
    if action == "observe":
        handle = target or win32gui.GetForegroundWindow()
        if not handle or not win32gui.IsWindow(handle):
            raise ValueError("No available target window. Say 'switch to <window title>' first.")
        window = desktop.window(handle=handle).wrapper_object()
        observation = window_observation(window)
        if request.get("image"):
            if observation["protected"] or observation["truncated"]:
                observation["image_note"] = "Image skipped: protected fields or incomplete accessibility inspection."
            elif not window.is_visible() or window.is_minimized():
                observation["image_note"] = "Image skipped: the window is hidden or minimized."
            else:
                from PIL import ImageGrab
                import base64
                from io import BytesIO
                image = ImageGrab.grab(window=handle).convert("RGB")
                image.thumbnail((1280, 1280))
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                observation["image"] = base64.b64encode(buffer.getvalue()).decode("ascii")
        return {"observation": observation}
    if action in ("list", "focus"):
        windows = [w for w in desktop.windows() if w.is_visible() and w.window_text().strip()]
        if action == "list":
            return {"message": "Open windows:\n" + "\n".join(w.window_text() for w in windows[:40]), "target": target}
        window = select_unique(windows, value, lambda w: w.window_text())
        window.set_focus()
        return {"message": f"Controlling: {window.window_text()}", "target": window.handle}
    if not target or not win32gui.IsWindow(target):
        raise ValueError("Choose a window first: say 'list windows', then 'switch to <window title>'.")
    window = desktop.window(handle=target).wrapper_object()
    title = window.window_text()
    if action == "wait":
        import time
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = window_observation(window)
            if any(c["name"].casefold() == value.casefold() for c in state["controls"]):
                return {"message": f"Verified visible control in {title}: {value}.", "target": target}
            time.sleep(0.2)
        raise ValueError(f"Expected control '{value}' did not become visible within 8 seconds.")
    if action == "inspect":
        state = window_observation(window)
        lines = [f"{control['type']}: {control['name']}" for control in state["controls"]]
        return {"message": f"Controls in {title}:\n" + "\n".join(lines[:100]), "target": target}
    window.set_focus()
    if win32gui.GetForegroundWindow() != target:
        raise ValueError("Windows did not focus the selected window; no input was sent.")
    if action in ("minimize", "maximize", "restore", "close"):
        getattr(window, action)()
    elif action == "click":
        controls = [c for c in window.descendants() if c.is_visible() and c.is_enabled() and
                    c.element_info.control_type in ("Button", "CheckBox", "RadioButton", "MenuItem", "TabItem", "Hyperlink", "ListItem", "TreeItem", "Edit", "ComboBox")]
        control = select_unique(controls, value, lambda c: c.window_text())
        control.click_input()
    elif action == "type":
        send_keys(literal_keys(value), with_spaces=True, pause=0.01)
    elif action == "press":
        send_keys(key_sequence(value), pause=0.05)
    else:
        raise ValueError("Unsupported desktop command.")
    if action == "close":
        import time
        deadline = time.monotonic() + 2
        while win32gui.IsWindow(target) and time.monotonic() < deadline:
            time.sleep(0.1)
        message = f"Verified closed: {title}." if not win32gui.IsWindow(target) else f"Sent close to {title}; the window is still open. Check for a save prompt."
    elif action in ("minimize", "maximize", "restore"):
        placement = win32gui.GetWindowPlacement(target)[1]
        expected = {"minimize": 2, "maximize": 3, "restore": 1}[action]
        message = f"Verified {action}: {title}." if placement == expected else f"Sent {action} to {title}; the requested state was not verified."
    else:
        message = f"Sent {action} to {title}. The application's resulting state is not verified."
    return {"message": message, "target": 0 if action == "close" and not win32gui.IsWindow(target) else target}


def window_observation(window):
    """Bounded traversal; never read password names/values or their descendants."""
    from collections import deque
    from datetime import datetime, timezone
    controls, protected, visited = [], False, 0
    queue = deque(window.children())
    while queue and visited < 1000:
        control = queue.popleft()
        visited += 1
        try:
            if not control.is_visible():
                continue
            if control.element_info.element.CurrentIsPassword:
                protected = True
                continue
            name = control.window_text().strip()[:300]
            if name and len(controls) < 150:
                controls.append({"name": name, "type": control.element_info.control_type,
                                 "enabled": bool(control.is_enabled())})
            queue.extend(control.children())
        except Exception:
            protected = True  # Do not capture an image when protection cannot be checked.
    return {"window": window.window_text()[:300], "handle": window.handle,
            "captured_at": datetime.now(timezone.utc).isoformat(), "controls": controls,
            "protected": protected, "truncated": bool(queue) or len(controls) >= 150}


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        print(json.dumps(perform(json.load(sys.stdin))))
    except Exception as exc:
        print(json.dumps({"message": f"Desktop navigation failed: {exc}"}))
