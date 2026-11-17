"""Explicit Windows intents; model output is never executed as a command."""
import os
import re
import subprocess
from pathlib import Path
from desktop_controls import DESKTOP_HELP


SETTINGS = {
    "windows": "", "wifi": "network-wifi", "bluetooth": "bluetooth",
    "network": "network-status", "display": "display", "sound": "sound",
    "battery": "batterysaver", "power": "powersleep", "storage": "storagesense",
    "updates": "windowsupdate", "privacy": "privacy", "apps": "appsfeatures",
    "microphone": "privacy-microphone", "camera": "privacy-webcam", "notifications": "notifications",
    "mouse": "mousetouchpad", "keyboard": "typing", "language": "regionlanguage",
    "date and time": "dateandtime", "printers": "printers", "default apps": "defaultapps",
    "startup apps": "startupapps", "personalization": "personalization", "accounts": "yourinfo",
}
HELP = (
    "Try: turn Wi-Fi on/off; Wi-Fi status; list Wi-Fi networks; saved Wi-Fi networks; "
    'connect Wi-Fi to "Profile name"; disconnect Wi-Fi; turn Bluetooth on/off; '
    "Bluetooth status; pair Bluetooth; set volume to 50; mute volume; unmute volume; "
    "set brightness to 60; open Windows settings; open display settings; "
    "unmute the volume; check volume; "
    "open task manager; open file explorer; lock computer. "
    "New Wi-Fi passwords and Bluetooth pairing are handled in Windows Settings. " + DESKTOP_HELP
)


def normalize_command(text):
    text = re.sub(r"^(?:(?:please|can you|could you|would you|will you|i want you to|i need you to)\s+)+", "", text.strip(), flags=re.I)
    text = re.sub(r"\bwi[ -]?fi\b", "wifi", text, flags=re.I)
    text = re.sub(r"\bblue tooth\b", "bluetooth", text, flags=re.I)
    text = re.sub(r"[, ]+please[.!?]*$", "", text, flags=re.I)
    return text.rstrip(".!? ")


def parse_windows_command(text):
    text = normalize_command(text)
    p = text.lower()
    audio = parse_audio_command(text)
    if audio:
        return "audio", audio
    if p in ("system help", "windows help", "what can you control"):
        return "help", None
    device = r"(?:(?:the|my) )?(wifi|bluetooth)"
    suffix = r"(?: for me| on (?:this|my|the) (?:device|computer|pc|laptop))*"
    m = re.fullmatch(r"(?:turn|switch|set|i want|i need) " + device + r" (?:back )?(on|off)" + suffix, p)
    if m:
        return "radio", m.groups()
    m = re.fullmatch(r"(?:turn|switch) (on|off) " + device + suffix, p)
    if m:
        return "radio", (m[2], m[1])
    m = re.fullmatch(r"(enable|disable|activate|deactivate) " + device + suffix, p)
    if m:
        return "radio", (m[2], "on" if m[1] in ("enable", "activate") else "off")
    m = re.fullmatch(r"is " + device + r" (?:turned |switched )?(?:on|off|enabled|disabled)" + suffix, p)
    if m:
        return "radio", (m[1], "status")
    m = re.fullmatch(r"(?:(?:show|check) )?(wifi|bluetooth) status", p)
    if m:
        return ("wifi_status", None) if m[1] == "wifi" else ("radio", ("bluetooth", "status"))
    if p in ("list wifi networks", "show wifi networks", "scan wifi", "scan for wifi networks"):
        return "wifi_networks", None
    if p in ("saved wifi networks", "list saved wifi networks"):
        return "wifi_profiles", None
    m = re.fullmatch(r"connect (?:to )?wifi(?: to)? (.+)", text, re.I)
    if m:
        return "wifi_connect", m[1].strip().strip('"')
    if p in ("disconnect wifi", "disconnect from wifi"):
        return "wifi_disconnect", None
    if p in ("pair bluetooth", "pair bluetooth device", "add bluetooth device"):
        return "settings", "bluetooth"
    m = re.fullmatch(r"open (.+) settings", p)
    if m and m[1] in SETTINGS:
        return "settings", m[1]
    if p in ("open task manager", "open file explorer", "open control panel"):
        return "utility", p[5:]
    if p in ("lock computer", "lock my computer", "lock pc", "lock screen"):
        return "lock", None
    m = re.fullmatch(r"set brightness(?: to)? (\d{1,3})%?", p)
    if m:
        return "brightness", int(m[1])
    return None


def parse_audio_command(text):
    p = normalize_command(text).lower()
    m = re.fullmatch(r"(unmute|mute)(?: (?:(?:the|my|system|computer) )?(?:volume|sound|audio|speakers))?(?: for me| on this device)?", p)
    if m:
        return "mute", m[1] == "mute"
    if re.fullmatch(r"(?:check|show) (?:(?:the|my|system) )?(?:volume|audio)(?: status)?|(?:volume|audio) status|is (?:the |my )?(?:volume|audio|sound) muted", p):
        return "status", None
    m = re.fullmatch(r"(?:set|turn) (?:(?:the|my|system) )?volume(?: to)? (\d+|max|full|half)%?", p)
    if m:
        return "volume", {"max": 100, "full": 100, "half": 50}.get(m[1], m[1])
    return None


def run_command(args):
    result = subprocess.run(args, capture_output=True, text=True, errors="replace",
                            encoding="utf-8" if args[0] == "powershell.exe" else None,
                            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = (result.stdout or result.stderr).strip()
    if result.returncode:
        return f"Windows could not complete the command: {output or 'permission or hardware unavailable'}."
    return output or "Windows completed the command."


def execute_windows_command(intent):
    action, value = intent
    if action == "help":
        return HELP
    if os.name != "nt":
        return "These controls require Windows."
    try:
        if action == "radio":
            kind, state = value
            return run_command(["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
                                str(Path(__file__).with_name("windows_radio.ps1")),
                                "-Kind", kind, "-State", state])
        if action.startswith("wifi_"):
            args = {
                "wifi_status": ["show", "interfaces"],
                "wifi_networks": ["show", "networks"],
                "wifi_profiles": ["show", "profiles"],
                "wifi_disconnect": ["disconnect"],
            }
            if action == "wifi_connect":
                if not value or len(value) > 256 or any(ord(c) < 32 for c in value):
                    return "Specify a valid saved Wi-Fi profile name."
                return run_command(["netsh", "wlan", "connect", f"name={value}"]) + "\nCheck Wi-Fi status to confirm connection."
            return run_command(["netsh", "wlan", *args[action]])
        if action == "settings":
            os.startfile("ms-settings:" + SETTINGS[value])
            return f"Windows accepted the request to open {value} settings."
        if action == "utility":
            executable = {"task manager": "Taskmgr.exe", "file explorer": "explorer.exe",
                          "control panel": "control.exe"}[value]
            subprocess.Popen([executable], shell=False)
            return f"Started {value}; its window is not yet verified."
        if action == "lock":
            import ctypes
            return "Computer locked." if ctypes.windll.user32.LockWorkStation() else "Windows could not lock the computer."
        if action == "brightness":
            if not 0 <= value <= 100:
                return "Brightness must be between 0 and 100."
            return run_command(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                "$ErrorActionPreference='Stop'; "
                                "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods; "
                                "if (!$m) { throw 'This display does not support brightness control; use its physical controls.' }; "
                                f"$m | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness=[byte]{value}}} | Out-Null; "
                                "Start-Sleep -Milliseconds 200; "
                                "$actual = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness); "
                                f"if (!$actual.Count -or @($actual | Where-Object {{ [Math]::Abs($_.CurrentBrightness - {value}) -gt 1 }}).Count) {{ throw 'Brightness change was not verified.' }}; "
                                f"Write-Output 'Verified brightness: {value}%.'"])
    except subprocess.TimeoutExpired:
        return "Windows took too long to respond. Check the device in Windows Settings before retrying."
    except OSError as exc:
        return f"Windows control failed: {exc}"
