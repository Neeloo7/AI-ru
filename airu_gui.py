#!/usr/bin/env python3
"""
gui.py - Ai-ru HUD v5 (PyQt6): Dust Orb v2 + neon aurora, settings drawer, personalities, search, text commands.

Backend hooks (all thread-safe unless noted):
    update_status(state)                 "IDLE" | "LISTENING" | "THINKING" | "SPEAKING"
    add_message(sender, text)            "user"/"you" render right, anything else renders as Ai-ru on the left
    set_mic_callback(cb)                 cb(active: bool)        mic button, wake phrase or command toggled the mic
    set_send_callback(cb)                cb(text: str)           user sent a message that is not a settings command
    set_new_session_callback(cb)

Context and settings hooks:
    set_personality_callback(cb)         cb(name, system_prompt) active personality or its prompt changed
    get_system_prompt() / .system_prompt the active system prompt
    set_voices([names], current=None)    fills the voice dropdown;  set_voice_callback(cb)  cb(voice)
    set_wake_word_callback(cb)           cb(phrase, enabled)
    set_settings_callback(cb)            cb(key, value) for every change (accent, font, audio_*, voice, ...)
    hear(transcript) -> bool             feed every transcript; the wake phrase ("Hey, Airu" by default) starts listening
    split_wake(text) -> (heard, rest)    the words after the wake phrase
    handle_command(text) -> bool         run a natural-language settings command; True means "consumed"
    wake()  set_audio_level(0..1)  add_thought(text)  notify(text)  start()

Keys: Ctrl+K palette | Ctrl+, settings | Ctrl+F search | Ctrl+Space / Ctrl+M mic | Ctrl+N new | Ctrl+B sidebar
      Ctrl+T theme | Ctrl+E export | F11 fullscreen | F2 presence | Ctrl+Shift+S/O/P sound/orb/party | Esc | Ctrl+Q

Files next to gui.py:
    assets/orb.html, orb.css, orb.js, dust-orb.js, material.js     the orb page (Dust Orb v2, recoloured)
    assets/vendor/three-r180.js, motion.js                        libraries from the dust-orb package
    assets/fonts/*.ttf                                            optional: Inter, Sora or any font; loaded automatically

Run directly for a demo:   python gui.py        Requires: PyQt6, PyQt6-WebEngine, numpy
"""
from __future__ import annotations

import colorsys
import difflib
import html
import inspect
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import time
import traceback
import wave
import weakref
from pathlib import Path

import numpy as np

# Chromium flags must be set before Qt creates the web engine.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--ignore-gpu-blocklist --enable-gpu-rasterization")

from PyQt6.QtCore import (QCoreApplication, QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRect, QRectF, QSize,
                          Qt, QThread, QTimer, QUrl, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QKeySequence, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient, QRegion, QShortcut, QTextOption)
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QColorDialog, QComboBox, QFileDialog, QFrame,
                             QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit,
                             QScrollArea, QSizeGrip, QSizePolicy, QSlider, QStackedWidget, QSystemTrayIcon,
                             QVBoxLayout, QWidget)

try:
    from PyQt6.QtMultimedia import QSoundEffect
    HAVE_MM = True
except Exception:  # pragma: no cover
    HAVE_MM = False

try:
    if QCoreApplication.instance() is None:
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAVE_WEB = True
except Exception:  # pragma: no cover
    HAVE_WEB = False
    QWebEnginePage = object

APP_NAME = "Ai-ru"
TAU = math.tau
HOME = Path.home()
ASSETS = Path(__file__).resolve().parent / "assets"
DATA_DIR = HOME / ".airu"
if not DATA_DIR.exists() and (HOME / ".aura").exists():  # migrate old Aura data once
    try:
        shutil.copytree(HOME / ".aura", DATA_DIR)
    except Exception:
        pass
SESSIONS_FILE = DATA_DIR / "sessions.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

ADD = QPainter.CompositionMode.CompositionMode_Plus
SRC = QPainter.CompositionMode.CompositionMode_SourceOver
NOPEN = Qt.PenStyle.NoPen
NOBRUSH = Qt.BrushStyle.NoBrush
ALIGN_C = Qt.AlignmentFlag.AlignCenter
ALIGN_VL = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
ALIGN_VR = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight

# --------------------------------------------------------------------------------------
# States and themes
# --------------------------------------------------------------------------------------
STATE_DEFS = {
    "IDLE":      dict(c1=(26, 170, 255), c2=(0, 76, 210),   speed=0.22, hz=0.16, energy=0.30, label="present"),
    "LISTENING": dict(c1=(255, 72, 168), c2=(255, 150, 60), speed=0.55, hz=0.90, energy=0.75, label="listening"),
    "THINKING":  dict(c1=(150, 104, 255), c2=(70, 40, 230), speed=2.60, hz=2.20, energy=0.95, label="thinking"),
    "SPEAKING":  dict(c1=(48, 242, 172), c2=(0, 168, 124),  speed=0.80, hz=1.40, energy=0.62, label="speaking"),
}
ORB_STATE = {"IDLE": "idle", "LISTENING": "listening", "THINKING": "thinking", "SPEAKING": "speaking"}
ALIASES = {
    "READY": "IDLE", "STANDBY": "IDLE", "SLEEPING": "IDLE", "SLEEP": "IDLE",
    "LISTEN": "LISTENING", "RECORDING": "LISTENING",
    "THINK": "THINKING", "PROCESSING": "THINKING", "BUSY": "THINKING", "WORKING": "THINKING",
    "SPEAK": "SPEAKING", "TALKING": "SPEAKING", "RESPONDING": "SPEAKING",
}
THEMES = {  # recolor IDLE (orb, aurora, accents); the other states keep their meaning
    "Neon":     ((26, 170, 255),  (0, 76, 210)),
    "Cyan":     ((0, 229, 255),   (0, 110, 190)),
    "Samantha": ((255, 112, 98),  (196, 52, 84)),
    "Ice":      ((196, 226, 255), (96, 136, 214)),
    "Void":     ((150, 160, 255), (74, 62, 204)),
    "Mono":     ((236, 238, 244), (110, 114, 140)),
}
THEME_ORDER = list(THEMES)
ORB_MODES = ("background", "always", "minimized")
ORB_MODE_TEXT = {
    "background": "Orb: shows whenever Ai-ru is in the background",
    "always": "Orb: always on",
    "minimized": "Orb: only when minimized or closed",
}
# Bahnschrift (built into Windows 10/11) is a DIN-style face: steady and dependable. Drop any .ttf in
# assets/fonts and put its family name first in this list to swap it.
FONT_BASE = ["Bahnschrift", "Sora", "Inter", "Segoe UI Variable Display", "Segoe UI", "Helvetica Neue",
             "Noto Sans", "Sans Serif"]
FONT_FAMILIES = list(FONT_BASE)   # first entry is the user's choice; the rest are fallbacks
FONT_SCALE = 1.0                  # every make_font() size is multiplied by this (Settings > Look > Text size)
BODY_PT = 12.5                    # the size that FONT_SCALE == 1.0 corresponds to
CUSTOM_FAMILIES = []              # families loaded from assets/fonts

DEFAULT_PERSONA = "Ai-ru (default)"
DEFAULT_PROMPT = ("You are Ai-ru, a calm, loyal voice companion who lives on the user's laptop. Speak in short, natural "
                  "sentences, because your replies are often read aloud. Be warm, direct and honest, and say so when you "
                  "are unsure. Ask at most one question at a time. Keep answers brief unless the user asks for depth, "
                  "and never claim abilities you do not have.")
PERSONA_SEEDS = [
    {"name": "Concise", "prompt": "You are Ai-ru. Answer in as few words as possible. No filler, no small talk, and no "
                                  "follow-up questions unless they are essential."},
    {"name": "Warm friend", "prompt": "You are Ai-ru, a warm and encouraging friend. Be conversational and kind, celebrate "
                                      "small wins, and gently check in on how the user is feeling. Keep replies short "
                                      "enough to be spoken aloud."},
]
DEFAULT_WAKE = "Hey, Airu"
SHORTCUTS = [("Ctrl+K", "Command palette"), ("Ctrl+,", "Settings"), ("Ctrl+F", "Search messages"),
             ("Ctrl+Space / Ctrl+M", "Mic on or off"), ("Ctrl+N", "New session"), ("Ctrl+B", "Slide the sidebar"),
             ("Ctrl+T", "Next theme"), ("Ctrl+E", "Export session"), ("F11", "Fullscreen"), ("F2", "Presence mode"),
             ("Ctrl+Shift+S", "Sound effects on or off"), ("Ctrl+Shift+O", "Orb visibility mode"),
             ("Ctrl+Shift+P", "Party mode"), ("Esc", "Close search, panel or fullscreen"), ("Ctrl+Q", "Quit")]
POKE_QUIPS = ["hehe, that tickles", "boop.", "careful, I'm ticklish", "I felt that", "do that again. I dare you.",
              "ow! ...kidding", "you know I'm made of dust, right?", "yes? hello?"]
IDLE_QUIPS = ["still here when you need me", "drifting quietly", "humming softly", "poke me, I dare you",
              "thinking about nothing in particular", "all calm"]


def normalize_state(s) -> str:
    key = ALIASES.get(str(s).strip().upper(), str(s).strip().upper())
    return key if key in STATE_DEFS else "IDLE"


def is_user(sender) -> bool:
    return str(sender).strip().lower() in {"user", "you", "me", "human"}


def is_ai(sender) -> bool:
    return str(sender).strip().lower() in {"aura", "ai-ru", "airu", "ai_ru", "assistant", "ai", "bot"}


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def approach(cur, tgt, rate, dt):
    return cur + (tgt - cur) * (1.0 - math.exp(-rate * dt))


def rgba(c, a=255):
    return QColor(int(clamp(c[0], 0, 255)), int(clamp(c[1], 0, 255)), int(clamp(c[2], 0, 255)), int(clamp(a, 0, 255)))


def lighten(c, k):
    return tuple(v + (255 - v) * k for v in c)


def mix(a, b, k):
    return tuple(a[i] + (b[i] - a[i]) * k for i in range(3))


def hexstr(c):
    return "#%02x%02x%02x" % tuple(int(clamp(v, 0, 255)) for v in c)


def hue_shift(c, deg):
    h, s, v = colorsys.rgb_to_hsv(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
    r, g, b = colorsys.hsv_to_rgb((h + deg / 360.0) % 1.0, max(s, 0.7), max(v, 0.85))
    return (r * 255.0, g * 255.0, b * 255.0)


def pen_of(col, a, w):
    pn = QPen(rgba(col, a), w)
    pn.setCapStyle(Qt.PenCapStyle.RoundCap)
    pn.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pn


def make_font(size, weight=QFont.Weight.Normal, spacing=0.0, italic=False):
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(size * FONT_SCALE)
    f.setWeight(weight)
    f.setItalic(italic)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def load_custom_fonts():
    d = ASSETS / "fonts"
    if d.is_dir():
        for f in list(d.glob("*.ttf")) + list(d.glob("*.otf")):
            fid = QFontDatabase.addApplicationFont(str(f))
            if fid >= 0:
                for fam in QFontDatabase.applicationFontFamilies(fid):
                    if fam not in CUSTOM_FAMILIES:
                        CUSTOM_FAMILIES.append(fam)


_FONT_REG = []


def ufont(widget, size, weight=QFont.Weight.Normal, spacing=0.0, italic=False):
    """Set a widget's font and remember it, so Settings > Look can restyle it later."""
    widget.setFont(make_font(size, weight, spacing, italic))
    _FONT_REG.append((weakref.ref(widget), size, weight, spacing, italic))


def refresh_fonts():
    alive = []
    for ref, size, weight, spacing, italic in _FONT_REG:
        w = ref()
        if w is None:
            continue
        try:
            w.setFont(make_font(size, weight, spacing, italic))
        except RuntimeError:  # the Qt object is already gone
            continue
        alive.append((ref, size, weight, spacing, italic))
    _FONT_REG[:] = alive


def set_font_globals(family, pt):
    global FONT_SCALE
    if family:
        FONT_FAMILIES[:] = [family] + [f for f in FONT_BASE if f != family]
    FONT_SCALE = clamp(pt, 9.0, 20.0) / BODY_PT


def safe_call(cb, *args):
    """Call a backend callback tolerant of arity differences; never let it kill the GUI."""
    if cb is None:
        return
    try:
        try:
            inspect.signature(cb).bind(*args)
            call_args = args
        except TypeError:
            call_args = ()
        except ValueError:
            call_args = args
        cb(*call_args)
    except Exception:
        traceback.print_exc()


def add_action(menu, text, fn):
    a = menu.addAction(text)
    a.triggered.connect(lambda _=False: fn())
    return a


def fmt_ts(ts):
    lt, now = time.localtime(ts), time.localtime()
    return time.strftime("Today %H:%M", lt) if lt[:3] == now[:3] else time.strftime("%d %b %H:%M", lt)


def greeting():
    h = time.localtime().tm_hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


def build_qss(accent):
    qss = """
    QToolTip{background:#04070d;color:#bfe3ff;border:1px solid rgba(ACC,90);padding:5px 9px;}
    QLabel{background:transparent;}
    QLabel#sessionTitle{color:#7f9bb5;}
    QLabel#sideLabel{color:#4b6580;}
    QLabel#sideFoot{color:#364c63;}
    QLabel#sender{color:rgba(ACC,200);}
    QLabel#body{color:#d6e8f7;}
    QLabel#hint{color:#4a6580;}
    QLabel#liveCaption{color:#cfe4f7;}
    QLabel#drawerTitle{color:#eaf5ff;}
    QLabel#setLabel{color:#d3e6f6;}
    QLabel#setHint{color:#64809a;}
    QLabel#setSection{color:rgba(ACC,215);}
    QLabel#personaChip{color:rgba(ACC,215);}
    QLineEdit#field{background:rgba(150,200,255,12);border:1px solid rgba(ACC,60);border-radius:0px;padding:7px 10px;color:#eaf5ff;selection-background-color:rgba(ACC,90);}
    QLineEdit#field:focus{border:1px solid rgba(ACC,170);}
    QLineEdit#field:disabled{color:#58728a;border:1px solid rgba(ACC,28);}
    QLineEdit#searchField{background:transparent;border:none;padding:0px;color:#eaf5ff;selection-background-color:rgba(ACC,90);}
    QPlainTextEdit#prompt{background:rgba(150,200,255,12);border:1px solid rgba(ACC,60);border-radius:0px;padding:8px;color:#e2f0fb;selection-background-color:rgba(ACC,90);}
    QPlainTextEdit#prompt:focus{border:1px solid rgba(ACC,170);}
    QComboBox{background:rgba(150,200,255,12);border:1px solid rgba(ACC,60);border-radius:0px;padding:7px 30px 7px 10px;color:#eaf5ff;min-height:20px;}
    QComboBox:hover{border:1px solid rgba(ACC,150);}
    QComboBox::drop-down{border:none;width:26px;}
    QComboBox::down-arrow{image:none;}
    QComboBox QAbstractItemView{background:#04070d;color:#cfe3f5;border:1px solid rgba(ACC,80);selection-background-color:rgba(ACC,50);outline:0;padding:4px;}
    QSlider::groove:horizontal{height:2px;background:rgba(150,200,255,45);}
    QSlider::sub-page:horizontal{background:rgba(ACC,230);}
    QSlider::handle:horizontal{width:12px;height:12px;margin:-5px 0;background:rgb(ACC);border-radius:0px;}
    QSlider::handle:horizontal:hover{background:#ffffff;}
    QStackedWidget{background:transparent;}
    #pageInner{background:transparent;}
    QLineEdit#entry{background:transparent;border:none;color:#eaf5ff;selection-background-color:rgba(ACC,90);}
    QLineEdit#paletteEntry{background:transparent;border:none;color:#f2f9ff;selection-background-color:rgba(ACC,90);}
    QLineEdit#orbEntry{background:transparent;border:none;color:#eaf5ff;selection-background-color:rgba(ACC,90);}
    QScrollArea{background:transparent;border:none;}
    #chatInner,#chronInner{background:transparent;}
    QScrollBar:vertical{background:transparent;width:8px;margin:2px;}
    QScrollBar::handle:vertical{background:rgba(ACC,45);border-radius:1px;min-height:30px;}
    QScrollBar::handle:vertical:hover{background:rgba(ACC,110);}
    QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
    QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}
    QMenu{background:#04070d;color:#cfe3f5;border:1px solid rgba(ACC,70);padding:6px;border-radius:3px;}
    QMenu::item{padding:7px 24px;border-radius:2px;}
    QMenu::item:selected{background:rgba(ACC,40);}
    QMenu::separator{height:1px;background:rgba(ACC,30);margin:5px 8px;}
    """
    return qss.replace("ACC", f"{int(accent[0])},{int(accent[1])},{int(accent[2])}")


# --------------------------------------------------------------------------------------
# Shape language: cut corners and hexagons instead of circles and pills
# --------------------------------------------------------------------------------------
def chamfer(r, tl=0.0, tr=0.0, br=0.0, bl=0.0):
    """Rectangle with diagonal cuts on any corner."""
    p = QPainterPath()
    p.moveTo(r.left() + tl, r.top())
    p.lineTo(r.right() - tr, r.top())
    p.lineTo(r.right(), r.top() + tr)
    p.lineTo(r.right(), r.bottom() - br)
    p.lineTo(r.right() - br, r.bottom())
    p.lineTo(r.left() + bl, r.bottom())
    p.lineTo(r.left(), r.bottom() - bl)
    p.lineTo(r.left(), r.top() + tl)
    p.closeSubpath()
    return p


def hex_path(cx, cy, r, rot=-math.pi / 2):
    path = QPainterPath()
    for i in range(6):
        a = rot + i * TAU / 6
        pt = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    return path


def diamond(r):
    return QPolygonF([QPointF(0, -r), QPointF(r, 0), QPointF(0, r), QPointF(-r, 0)])


def draw_sigil(p, c1, cx, cy, r, t, glow=1.0):
    """Ai-ru's mark: a hexagon holding a slowly turning diamond and a hot core."""
    p.save()
    p.translate(cx, cy)
    p.setPen(NOPEN)
    p.setCompositionMode(ADD)
    g = QRadialGradient(0, 0, r * 1.9)
    g.setColorAt(0, rgba(c1, 95 * glow))
    g.setColorAt(1, rgba(c1, 0))
    p.setBrush(g)
    p.drawEllipse(QPointF(0, 0), r * 1.9, r * 1.9)
    p.setCompositionMode(SRC)
    p.setBrush(NOBRUSH)
    w = max(1.0, r * 0.11)
    p.setPen(pen_of(c1, 235, w))
    p.drawPath(hex_path(0, 0, r))
    p.rotate(math.degrees(t * 0.7))
    p.setPen(pen_of(lighten(c1, 0.45), 200, w * 0.8))
    p.drawPolygon(diamond(r * 0.58))
    p.setPen(NOPEN)
    p.setBrush(rgba(lighten(c1, 0.85), 255))
    p.drawPolygon(diamond(r * 0.22))
    p.restore()


def make_app_icon(accent=(26, 170, 255)):
    """Painted at runtime so the app ships with an icon and no image files."""
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = hex_path(s / 2, s / 2, s * 0.47)
        g = QLinearGradient(0, 0, 0, s)
        g.setColorAt(0, QColor(8, 16, 30))
        g.setColorAt(1, QColor(2, 3, 8))
        p.fillPath(body, g)
        p.setPen(QPen(rgba(accent, 150), max(1.0, s * 0.03)))
        p.drawPath(body)
        draw_sigil(p, accent, s / 2, s / 2, s * 0.25, 0.6)
        p.end()
        icon.addPixmap(pm)
    return icon


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        traceback.print_exc()


def load_sessions():
    out = []
    for s in load_json(SESSIONS_FILE, []):
        try:
            out.append({"id": int(s["id"]), "title": str(s["title"]), "ts": float(s["ts"]),
                        "msgs": [(str(a), str(b)) for a, b in s["msgs"]]})
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------------------
# Launch at login
# --------------------------------------------------------------------------------------
def set_autostart(enabled: bool) -> bool:
    """Register (or remove) this program as a login item. It is started with --background so it
    comes up as the floating orb instead of throwing a window at you."""
    try:
        script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
        frozen = bool(getattr(sys, "frozen", False))
        if sys.platform == "win32":
            import winreg
            exe = sys.executable
            if frozen:
                cmd = f'"{exe}" --background'
            else:
                pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
                cmd = f'"{pyw if os.path.exists(pyw) else exe}" "{script}" --background'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                                winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
            return True
        if sys.platform.startswith("linux"):
            f = HOME / ".config" / "autostart" / "airu.desktop"
            if enabled:
                f.parent.mkdir(parents=True, exist_ok=True)
                cmd = f'"{sys.executable}" "{script}" --background' if not frozen else f'"{sys.executable}" --background'
                f.write_text(f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nExec={cmd}\n"
                             "X-GNOME-Autostart-enabled=true\n", encoding="utf-8")
            elif f.exists():
                f.unlink()
            return True
    except Exception:
        traceback.print_exc()
    return False


# --------------------------------------------------------------------------------------
# Synthesized tactile audio
# --------------------------------------------------------------------------------------
SR = 44100
SOUND = None


def sfx(name):
    if SOUND is not None:
        SOUND.play(name)


def _click(freq=2200, dur=0.03, noise=0.45, body=160, gain=1.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    rng = np.random.default_rng()
    sig = (0.6 * np.sin(TAU * freq * t) * np.exp(-t * 190)
           + noise * rng.standard_normal(n) * np.exp(-t * 450)
           + 0.5 * np.sin(TAU * body * t) * np.exp(-t * 100))
    return gain * sig


def _sweep(f0, f1, dur, gain=1.0, decay=10.0):
    n = int(SR * dur)
    t = np.arange(n) / SR
    ph = TAU * np.cumsum(np.linspace(f0, f1, n)) / SR
    return gain * np.sin(ph) * np.minimum(1.0, t / 0.006) * np.exp(-t * decay)


def _mix(parts, level):
    length = max(int(SR * off) + len(a) for off, a in parts)
    out = np.zeros(length)
    for off, a in parts:
        i = int(SR * off)
        out[i:i + len(a)] += a
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * level * 32767).astype(np.int16)


def build_sounds():
    rnd = random.Random(3)
    crackle = [(rnd.uniform(0, 0.16), _click(rnd.uniform(1500, 4200), 0.02, 0.9, 200, rnd.uniform(0.4, 1.0)))
               for _ in range(9)] + [(0, _sweep(300, 1300, 0.22, 0.5, 12))]
    return {
        "key":       _mix([(0, _click(3000, 0.016, 0.3, 220))], 0.20),
        "tick":      _mix([(0, _click(2600, 0.022, 0.35, 140))], 0.30),
        "press":     _mix([(0, _click(1700, 0.030, 0.50, 120))], 0.38),
        "send":      _mix([(0, _click(1500)), (0.045, _click(2300)), (0.05, _sweep(700, 1150, 0.12, 0.5, 14))], 0.42),
        "recv":      _mix([(0, _sweep(520, 780, 0.14, 1.0, 14)), (0, _click(1900, 0.02, 0.2, 140, 0.4))], 0.26),
        "new":       _mix([(0, _click(900, 0.05, 0.6, 90)), (0.03, _sweep(300, 640, 0.16, 0.7, 12))], 0.40),
        "mic_on":    _mix([(0, _click(1900)), (0.03, _sweep(620, 940, 0.10, 0.6, 16))], 0.38),
        "mic_off":   _mix([(0, _click(1500)), (0.03, _sweep(900, 560, 0.10, 0.6, 16))], 0.38),
        "crackle":   _mix(crackle, 0.36),
        "idle":      _mix([(0, _sweep(560, 420, 0.20, 1.0, 12))], 0.22),
        "listening": _mix([(0, _sweep(640, 900, 0.15, 1.0, 14))], 0.24),
        "thinking":  _mix([(0, _sweep(1100, 1100, 0.04, 1, 40)), (0.05, _sweep(1400, 1400, 0.04, 1, 40)),
                           (0.10, _sweep(1750, 1750, 0.05, 1, 40))], 0.20),
        "speaking":  _mix([(0, _sweep(500, 720, 0.12, 1.0, 12)), (0.10, _sweep(720, 620, 0.12, 0.8, 12))], 0.24),
        "whoosh":    _mix([(0, _sweep(180, 900, 0.22, 1.0, 9)), (0.02, _click(800, 0.04, 0.8, 80, 0.5))], 0.34),
        "palette":   _mix([(0, _click(2100, 0.02, 0.3, 150)), (0.03, _sweep(900, 1300, 0.07, 0.5, 20))], 0.30),
        "zap":       _mix([(0, _sweep(2200, 260, 0.13, 1.0, 20)), (0, _click(3500, 0.03, 0.9, 200))], 0.36),
        "pop":       _mix([(0, _click(1100, 0.04, 0.5, 180)), (0.02, _sweep(520, 200, 0.10, 0.8, 22))], 0.32),
        "slide":     _mix([(0, _sweep(240, 520, 0.18, 0.8, 10))], 0.24),
    }


class SoundEngine:
    def __init__(self):
        self.enabled = True
        self.cats = {"ui": True, "typing": True, "state": True}
        self._fx, self._idx, self._tmp = {}, {}, None
        if not HAVE_MM:
            return
        try:
            self._tmp = tempfile.TemporaryDirectory(prefix="airu_snd_")
            for name, data in build_sounds().items():
                path = os.path.join(self._tmp.name, name + ".wav")
                with wave.open(path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SR)
                    wf.writeframes(data.tobytes())
                pool = []
                for _ in range(3):
                    fx = QSoundEffect()
                    fx.setSource(QUrl.fromLocalFile(path))
                    fx.setVolume(0.7)
                    pool.append(fx)
                self._fx[name], self._idx[name] = pool, 0
        except Exception:
            traceback.print_exc()
            self._fx = {}

    CATS = {"key": "typing", "idle": "state", "listening": "state", "thinking": "state", "speaking": "state",
            "mic_on": "state", "mic_off": "state", "crackle": "state"}

    def play(self, name):
        pool = self._fx.get(name)
        if not self.enabled or not pool or not self.cats.get(self.CATS.get(name, "ui"), True):
            return
        i = self._idx[name]
        self._idx[name] = (i + 1) % len(pool)
        pool[i].play()


# --------------------------------------------------------------------------------------
# Presence: the single animated source of truth (shared by window, wave line and floating orb)
# --------------------------------------------------------------------------------------
class Presence:
    def __init__(self):
        d = STATE_DEFS["IDLE"]
        self.state, self.party = "IDLE", False
        self.c1, self.c2 = [float(v) for v in d["c1"]], [float(v) for v in d["c2"]]
        self.speed, self.hz, self.energy = d["speed"], d["hz"], d["energy"]
        self.level = self.kick_v = 0.0
        self.shock_req = 0
        self.weights = {k: (1.0 if k == "IDLE" else 0.0) for k in STATE_DEFS}
        self.rot = self.phase = self.t = self.flash = 0.0
        self.ext_level, self.ext_time = 0.0, -10.0

    def set_state(self, s):
        if s != self.state:
            self.state = s
            self.flash = 1.0
            self.shock_req += 1

    def kick(self, v, shock=False):
        self.kick_v = max(self.kick_v, v)
        if shock:
            self.shock_req += 1

    def push_level(self, v):
        self.ext_level, self.ext_time = clamp(float(v)), self.t

    def targets(self):
        d = STATE_DEFS[self.state]
        if self.party:
            sh = (self.t * 70.0) % 360.0
            return hue_shift(d["c1"], sh), hue_shift(d["c2"], sh + 25.0)
        return d["c1"], d["c2"]

    def _synth(self):
        t, s = self.t, self.state
        if s == "LISTENING":
            burst = max(0.0, math.sin(t * 2.3) * math.sin(t * 0.9 + 1.0))
            return clamp(0.14 + 0.85 * burst * (0.65 + 0.35 * abs(math.sin(t * 11.0))) + 0.05 * random.random())
        if s == "SPEAKING":
            syl = abs(math.sin(t * 6.2)) ** 0.8 * (0.55 + 0.45 * math.sin(t * 1.9) ** 2)
            return clamp(0.15 + 0.75 * syl)
        if s == "THINKING":
            return clamp(0.5 + 0.25 * math.sin(t * 5.0) * math.sin(t * 1.3))
        return 0.0

    def advance(self, dt):
        dt = min(dt, 0.1)
        self.t += dt
        d = STATE_DEFS[self.state]
        t1, t2 = self.targets()
        for i in range(3):
            self.c1[i] = approach(self.c1[i], t1[i], 3.5, dt)
            self.c2[i] = approach(self.c2[i], t2[i], 3.5, dt)
        self.speed = approach(self.speed, d["speed"], 2.5, dt)
        self.hz = approach(self.hz, d["hz"], 2.5, dt)
        self.kick_v = approach(self.kick_v, 0.0, 6.0, dt)
        base = self.ext_level if self.t - self.ext_time < 0.4 else self._synth()
        tgt = clamp(base + self.kick_v)
        self.level = approach(self.level, tgt, 14 if tgt > self.level else 7, dt)
        self.energy = approach(self.energy, d["energy"] + 0.35 * self.level, 5.0, dt)
        for k, v in self.weights.items():
            self.weights[k] = approach(v, 1.0 if k == self.state else 0.0, 3.2, dt)
        self.rot += dt * self.speed
        self.phase += dt * TAU * self.hz
        self.flash = max(0.0, self.flash - dt * 2.2)


# --------------------------------------------------------------------------------------
# Dust cloud: a small Qt-painted echo of the Dust Orb (floating orb + fallback when WebEngine is missing)
# --------------------------------------------------------------------------------------
class DustCloud:
    def __init__(self, n=900):
        rng = np.random.default_rng(5)
        z = rng.uniform(-1, 1, n)
        a = rng.uniform(0, TAU, n)
        s = np.sqrt(1 - z * z)
        layer = rng.random(n)
        r = np.where(layer < 0.8, 1.0 + rng.uniform(-0.07, 0.07, n),
                     np.where(layer < 0.94, rng.random(n) ** 0.4, 1.1 + rng.random(n) * 0.22))
        self.p = np.stack([s * np.cos(a) * r, z * r, s * np.sin(a) * r], axis=1)
        self.seed = rng.random((n, 3))

    def draw(self, p, P, R, size=1.0):
        t, lvl = P.t, P.level
        q, sd = self.p, self.seed
        wob = (1.0 + 0.07 * np.sin(q[:, 1] * 5.0 + t * 0.8 + sd[:, 0] * TAU) * np.cos(q[:, 0] * 4.0 - t * 0.5)
               + 0.05 * lvl * np.sin(t * 8.0 + sd[:, 1] * TAU))
        scale = 1.0 + 0.025 * math.sin(P.phase) + 0.10 * lvl + 0.05 * P.flash
        a = t * 0.22 * (0.6 + 0.4 * P.speed)
        ca, sa = math.cos(a), math.sin(a)
        x = (q[:, 0] * ca + q[:, 2] * sa) * wob * scale
        z = (-q[:, 0] * sa + q[:, 2] * ca) * wob * scale
        y = q[:, 1] * wob * scale
        ct, st = math.cos(0.25), math.sin(0.25)
        y2, z2 = y * ct - z * st, y * st + z * ct
        persp = 3.6 / (3.6 - z2)
        X, Y = x * persp * R, y2 * persp * R
        front = np.clip((z2 + 1.3) / 2.6, 0.0, 1.0)
        c1, c2 = P.c1, P.c2
        p.setPen(NOPEN)
        p.setCompositionMode(ADD)
        g = QRadialGradient(0, 0, R * 0.9)
        g.setColorAt(0, rgba(lighten(c1, 0.5), 70 + 80 * P.energy))
        g.setColorAt(0.5, rgba(c2, 34 + 30 * P.energy))
        g.setColorAt(1, rgba(c2, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), R * 0.9, R * 0.9)
        for lo, hi, alpha, w in ((0.0, 0.38, 70, 1.3), (0.38, 0.68, 125, 1.7), (0.68, 1.01, 200, 2.1)):
            m = (front >= lo) & (front < hi)
            if not m.any():
                continue
            pts = QPolygonF([QPointF(a_, b_) for a_, b_ in zip(X[m].tolist(), Y[m].tolist())])
            pen = QPen(rgba(mix(c2, lighten(c1, 0.35), (lo + hi) / 2), alpha), w * size)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPoints(pts)
        p.setCompositionMode(SRC)


# --------------------------------------------------------------------------------------
# The orb host: Dust Orb + aurora in a web view. Falls back to a painted cloud if assets are missing.
# --------------------------------------------------------------------------------------
class OrbPage(QWebEnginePage):
    interacted = pyqtSignal(bool)  # True = tap on the orb, False = drag released

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message == "airu:poke":
            self.interacted.emit(True)
        elif message == "airu:release":
            self.interacted.emit(False)


class OrbView(QWidget):
    interacted = pyqtSignal(bool)

    def __init__(self, pres):
        super().__init__()
        self.pres, self.ready, self.pending, self.view, self.reason = pres, False, [], None, ""
        self._last_voice = -1.0
        self.cloud = DustCloud(1100)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        three = ASSETS / "vendor" / "three-r180.js"
        if os.environ.get("AIRU_SOFT_ORB"):
            self.reason = "AIRU_SOFT_ORB is set"
        elif not HAVE_WEB:
            self.reason = "PyQt6-WebEngine is not installed (pip install PyQt6-WebEngine)"
        elif not (ASSETS / "orb.html").exists():
            self.reason = "assets/orb.html is missing"
        elif not three.exists():
            self.reason = "assets/vendor/three-r180.js is missing (copy it from the dust-orb package)"
        else:
            self.view = QWebEngineView(self)
            self.page = OrbPage(QWebEngineProfile(QApplication.instance()), self.view)  # off-the-record profile
            self.page.setBackgroundColor(QColor(3, 3, 5))
            self.page.interacted.connect(self.interacted)
            self.page.loadFinished.connect(self._loaded)
            self.view.setPage(self.page)
            self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # typing keeps going to the message box
            self.view.setUrl(QUrl.fromLocalFile(str(ASSETS / "orb.html")))
            lay.addWidget(self.view)

    def _loaded(self, ok):
        self.ready = bool(ok)
        if ok:
            for code in self.pending:
                self.page.runJavaScript(code)
        else:
            self.reason = "the orb page failed to load"
        self.pending = []

    def js(self, code):
        if self.view is None:
            return
        if self.ready:
            self.page.runJavaScript(code)
        else:
            self.pending.append(code)

    def set_state(self, s):
        self.js(f"airu.setState('{ORB_STATE.get(s, 'idle')}')")

    def set_theme(self, c1, c2):
        dust = [v / 255.0 for v in mix(c1, (255, 255, 255), 0.10)]
        ember = [v / 255.0 for v in lighten(c1, 0.22)]
        self.js(f"airu.setPalette({json.dumps(dust)},{json.dumps(ember)})")

    def set_aurora(self, c1, c2):
        self.js(f"airu.setAurora('{hexstr(c1)}','{hexstr(c2)}','{hexstr(lighten(c1, 0.55))}')")

    def set_voice(self, v):
        if self.ready and abs(v - self._last_voice) > 0.015:
            self._last_voice = v
            self.page.runJavaScript(f"airu.setVoice({v:.3f})")

    def pulse(self):
        self.js("airu.pulse()")

    def set_speed(self, x):
        self.js(f"airu.setSpeed({x:.3f})")

    def set_light(self, v):
        self.js(f"airu.setLight({v:.3f})")

    def tick(self):
        if self.view is None:
            self.update()

    def paintEvent(self, _):
        if self.view is not None:
            return
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.translate(w / 2, h / 2)
        self.cloud.draw(p, P, max(20.0, min(w, h) / 2 * 0.8))


# --------------------------------------------------------------------------------------
# Wave line (voice print) and state strip
# --------------------------------------------------------------------------------------
class WaveLine(QWidget):
    """A flowing audio line. Faint at rest, full when the mic is on or Ai-ru speaks."""

    def __init__(self, pres):
        super().__init__()
        self.pres = pres
        self.setFixedHeight(46)

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cy = H / 2
        vis = clamp(P.weights["LISTENING"] + 0.85 * P.weights["SPEAKING"] + 0.35 * P.weights["THINKING"])
        amp = (2.0 + H * 0.46 * P.level * 1.2) * (0.25 + 0.75 * vis)
        x0, x1 = W * 0.14, W * 0.86
        c1, t = P.c1, P.t
        p.setPen(QPen(rgba(c1, 26 + 40 * vis), 1))
        p.drawLine(QPointF(x0, cy), QPointF(x1, cy))
        p.setCompositionMode(ADD)
        n = 110
        for k, (freq, spd, al, w) in enumerate(((1.0, 1.0, 235, 1.8), (1.7, -1.3, 135, 1.2), (2.6, 1.9, 85, 1.0))):
            path = QPainterPath()
            for i in range(n + 1):
                f = i / n
                env = math.sin(math.pi * f) ** 1.6
                y = cy + amp * env * math.sin(f * TAU * 2.5 * freq + t * (2.5 + 4.0 * vis) * spd + k) \
                    * (0.65 + 0.35 * math.sin(f * 7 + t * 3 + k))
                pt = QPointF(x0 + (x1 - x0) * f, y)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            fade = 0.30 + 0.70 * vis
            p.setBrush(NOBRUSH)
            p.setPen(pen_of(c1, al * 0.20 * fade, w * 4.5))
            p.drawPath(path)
            p.setPen(pen_of(lighten(c1, 0.35 * (1 - k * 0.3)), al * fade, w))
            p.drawPath(path)
        p.setCompositionMode(SRC)
        p.setPen(NOPEN)
        p.setBrush(rgba(lighten(c1, 0.5), 120 + 120 * vis))
        for x in (x0, x1):
            p.save()
            p.translate(x, cy)
            p.drawPolygon(diamond(3.4))
            p.restore()


class StateStrip(QWidget):
    """Live thought line over a ruled state label. One widget, so the two can never overlap."""

    def __init__(self, pres):
        super().__init__()
        self.pres, self.thought, self.thought_at = pres, "", -100.0
        self.setFixedHeight(64)

    def set_thought(self, text):
        self.thought, self.thought_at = text, time.time()

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        c1 = P.c1
        age = time.time() - self.thought_at
        if self.thought and age < 7:
            fade = clamp(age / 0.4) * clamp((7 - age) / 1.5)
            p.setFont(make_font(10, QFont.Weight.Normal, 0.3, italic=True))
            p.setPen(rgba(lighten(c1, 0.5), 205 * fade))
            fm = QFontMetrics(p.font())
            p.drawText(QRectF(0, 0, W, 22), ALIGN_C, fm.elidedText(self.thought, Qt.TextElideMode.ElideRight, int(W * 0.7)))
        label = STATE_DEFS[P.state]["label"]
        font = make_font(11, QFont.Weight.DemiBold, 4)
        p.setFont(font)
        tw = QFontMetrics(font).horizontalAdvance(label)
        cx, cy, gap = W / 2, 43.0, 26.0
        for sgn in (-1, 1):
            xa = cx + sgn * (tw / 2 + gap)
            xb = xa + sgn * 170
            g = QLinearGradient(xa, 0, xb, 0)
            g.setColorAt(0, rgba(c1, 150))
            g.setColorAt(1, rgba(c1, 0))
            p.setPen(QPen(g, 1.2))
            p.drawLine(QPointF(xa, cy), QPointF(xb, cy))
            p.setPen(NOPEN)
            p.setBrush(rgba(lighten(c1, 0.5), 230))
            p.save()
            p.translate(cx + sgn * (tw / 2 + gap * 0.5), cy)
            p.drawPolygon(diamond(2.6 + 0.8 * math.sin(P.phase)))
            p.restore()
        p.setPen(rgba(lighten(c1, 0.4), 225))
        p.drawText(QRectF(0, cy - 13, W, 26), ALIGN_C, label)


# --------------------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------------------
class GlassButton(QAbstractButton):
    def __init__(self, pres, parent=None):
        super().__init__(parent)
        self.pres, self.h, self.down, self.press_sound = pres, 0.0, False, "press"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_h)

    def _set_h(self, v):
        self.h = float(v)
        self.update()

    def _go(self, target):
        self._anim.stop()
        self._anim.setStartValue(self.h)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._go(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._go(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self.down = True
        self.update()
        if self.press_sound:
            sfx(self.press_sound)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self.down = False
        self.update()
        super().mouseReleaseEvent(e)


class IconButton(GlassButton):
    def __init__(self, pres, kind):
        super().__init__(pres)
        self.kind = kind
        self.setFixedSize(34, 34)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = (255, 92, 116) if self.kind == "close" else self.pres.c1
        if self.h > 0:
            p.setPen(QPen(rgba(c, 110 * self.h), 1.0))
            p.setBrush(rgba(c, 34 * self.h))
            p.drawPath(chamfer(QRectF(self.rect()).adjusted(3, 3, -3, -3), 7, 0, 7, 0))
        p.setPen(pen_of(lighten(c, 0.3), 150 + 105 * self.h, 1.6))
        p.setBrush(NOBRUSH)
        p.translate(self.width() / 2, self.height() / 2)
        k = self.kind
        if k == "min":
            p.drawLine(QPointF(-5, 2), QPointF(5, 2))
        elif k == "close":
            p.drawLine(QPointF(-4.5, -4.5), QPointF(4.5, 4.5))
            p.drawLine(QPointF(-4.5, 4.5), QPointF(4.5, -4.5))
        elif k == "menu":
            for y, x2 in ((-4.5, 6), (0, 3.5), (4.5, 6)):
                p.drawLine(QPointF(-6, y), QPointF(x2, y))
        elif k == "search":
            p.drawEllipse(QPointF(-1.5, -1.5), 4.6, 4.6)
            p.drawLine(QPointF(1.8, 1.8), QPointF(5.5, 5.5))
        elif k == "full":
            full = self.window().isFullScreen() if self.window() else False
            s, L = 5.0, 3.6
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                if full:
                    pts = [QPointF(sx * (s - L), sy * s), QPointF(sx * (s - L), sy * (s - L)), QPointF(sx * s, sy * (s - L))]
                else:
                    pts = [QPointF(sx * s, sy * (s - L)), QPointF(sx * s, sy * s), QPointF(sx * (s - L), sy * s)]
                p.drawPolyline(QPolygonF(pts))
        elif k == "gear":
            p.drawEllipse(QPointF(0, 0), 2.4, 2.4)
            p.drawEllipse(QPointF(0, 0), 5.0, 5.0)
            for i in range(8):
                a = i * TAU / 8
                p.drawLine(QPointF(5.4 * math.cos(a), 5.4 * math.sin(a)), QPointF(7.4 * math.cos(a), 7.4 * math.sin(a)))
        elif k == "up":
            p.drawPolyline(QPolygonF([QPointF(-4.5, 2), QPointF(0, -2.5), QPointF(4.5, 2)]))
        elif k == "down":
            p.drawPolyline(QPolygonF([QPointF(-4.5, -2), QPointF(0, 2.5), QPointF(4.5, -2)]))
        else:  # sound
            p.drawPolygon(QPolygonF([QPointF(-7, -3), QPointF(-4, -3), QPointF(0, -6.5), QPointF(0, 6.5),
                                     QPointF(-4, 3), QPointF(-7, 3)]))
            if SOUND is None or SOUND.enabled:
                p.drawArc(QRectF(-3, -4, 8, 8), -50 * 16, 100 * 16)
                p.drawArc(QRectF(-4, -7.5, 15, 15), -50 * 16, 100 * 16)
            else:
                p.drawLine(QPointF(3, -3), QPointF(8, 3))
                p.drawLine(QPointF(8, -3), QPointF(3, 3))


class PillButton(GlassButton):
    """Cut-corner action button with a plus mark and a shortcut hint."""

    def __init__(self, pres, text, hint=""):
        super().__init__(pres)
        self.label, self.hint = text, hint
        self.setFixedHeight(44)

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self.down:
            r = r.adjusted(1, 1, -1, -1)
        path = chamfer(r, 14, 0, 14, 0)
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, rgba(P.c1, 30 + 44 * self.h))
        g.setColorAt(1, rgba(P.c2, 6 + 24 * self.h))
        p.setBrush(g)
        p.setPen(QPen(rgba(P.c1, 90 + 140 * self.h), 1.1))
        p.drawPath(path)
        p.setPen(pen_of(P.c1, 140 + 115 * self.h, 2.2))  # brighter diagonal edges
        p.drawLine(QPointF(r.left(), r.top() + 14), QPointF(r.left() + 14, r.top()))
        p.drawLine(QPointF(r.right() - 14, r.bottom()), QPointF(r.right(), r.bottom() - 14))
        cy = r.center().y()
        p.setPen(pen_of(lighten(P.c1, 0.5), 255, 1.7))
        p.drawLine(QPointF(r.left() + 24, cy), QPointF(r.left() + 34, cy))
        p.drawLine(QPointF(r.left() + 29, cy - 5), QPointF(r.left() + 29, cy + 5))
        p.setFont(make_font(10.5, QFont.Weight.DemiBold, 0.8))
        p.setPen(rgba(lighten(P.c1, 0.6), 255))
        p.drawText(QRectF(r.left() + 46, r.top(), r.width() - 60, r.height()), ALIGN_VL, self.label)
        if self.hint:
            p.setFont(make_font(8.5))
            p.setPen(rgba(P.c1, 130))
            p.drawText(QRectF(r.left(), r.top(), r.width() - 24, r.height()), ALIGN_VR, self.hint)


class MicButton(GlassButton):
    def __init__(self, pres):
        super().__init__(pres)
        self.setCheckable(True)
        self.setFixedSize(56, 56)
        self.press_sound = None

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, on = P.c1, self.isChecked()
        p.translate(self.width() / 2, self.height() / 2)
        if on:
            p.setBrush(NOBRUSH)
            for k in range(2):
                pr = (P.t * 0.9 + k * 0.5) % 1.0
                p.setPen(QPen(rgba(c, 150 * (1 - pr)), 1.4))
                p.drawPath(hex_path(0, 0, 21 + 7 * pr))
            p.setCompositionMode(ADD)
            for k in range(6):  # sparks off the hexagon corners
                a = -math.pi / 2 + k * TAU / 6
                ln = 3 + 6 * (0.5 + 0.5 * math.sin(P.t * 11 + k * 2.1))
                p.setPen(pen_of(lighten(c, 0.6), 190, 1.3))
                p.drawLine(QPointF(22 * math.cos(a), 22 * math.sin(a)), QPointF((22 + ln) * math.cos(a), (22 + ln) * math.sin(a)))
            p.setCompositionMode(SRC)
        p.setBrush(rgba(c, (70 if on else 16) + 30 * self.h))
        p.setPen(QPen(rgba(c, (230 if on else 110) + 25 * self.h), 1.3))
        p.drawPath(hex_path(0, 0, 21 - (1 if self.down else 0)))
        p.setPen(pen_of(lighten(c, 0.5), 255 if on else 190, 1.7))
        p.setBrush(NOBRUSH)
        p.drawRoundedRect(QRectF(-3.5, -9, 7, 12), 3.5, 3.5)
        p.drawArc(QRectF(-7, -6, 14, 13), 180 * 16, 180 * 16)
        p.drawLine(QPointF(0, 7), QPointF(0, 10.5))
        p.drawLine(QPointF(-3.5, 10.5), QPointF(3.5, 10.5))


class SendButton(GlassButton):
    def __init__(self, pres):
        super().__init__(pres)
        self.setFixedSize(56, 56)
        self.press_sound = None
        self.ready = False

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = P.c1
        p.translate(self.width() / 2, self.height() / 2)
        half = 18 - (1 if self.down else 0)
        shape = chamfer(QRectF(-half, -half, 2 * half, 2 * half), 9, 0, 9, 0)
        if self.ready:
            p.setCompositionMode(ADD)
            g = QRadialGradient(0, 0, 30)
            g.setColorAt(0.55, rgba(c, 70 + 50 * self.h))
            g.setColorAt(1, rgba(c, 0))
            p.setPen(NOPEN)
            p.setBrush(g)
            p.drawEllipse(QPointF(0, 0), 30, 30)
            p.setCompositionMode(SRC)
            p.setBrush(rgba(c, 215 + 40 * self.h))
            p.setPen(NOPEN)
        else:
            p.setBrush(rgba(c, 8))
            p.setPen(QPen(rgba(c, 75), 1.2))
        p.drawPath(shape)
        p.setPen(pen_of((3, 6, 12) if self.ready else c, 255 if self.ready else 120, 2.0))
        p.setBrush(NOBRUSH)
        p.drawPolyline(QPolygonF([QPointF(-6, 1), QPointF(0, -5), QPointF(6, 1)]))
        p.drawLine(QPointF(0, -5), QPointF(0, 7))


# --------------------------------------------------------------------------------------
# Input dock
# --------------------------------------------------------------------------------------
class InputDock(QWidget):
    GM, CUT, CUT_S = 8, 20, 6

    def __init__(self, pres):
        super().__init__()
        self.pres, self.focus = pres, 0.0
        self.setFixedHeight(56 + 2 * self.GM)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(self.GM + 52, self.GM, self.GM + 10, self.GM)
        lay.setSpacing(2)
        self.entry = QLineEdit()
        self.entry.setObjectName("entry")
        ufont(self.entry, 12.5)
        self.entry.setPlaceholderText("Message Ai-ru")
        self.entry.setFrame(False)
        self.entry.installEventFilter(self)
        self.mic = MicButton(pres)
        self.send = SendButton(pres)
        lay.addWidget(self.entry, 1)
        lay.addWidget(self.mic)
        lay.addWidget(self.send)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_focus)
        self.entry.textChanged.connect(self._text_changed)

    def _text_changed(self, s):
        self.send.ready = bool(s.strip())
        self.send.update()

    def _set_focus(self, v):
        self.focus = float(v)
        self.update()

    def refresh(self):
        self.update()
        self.mic.update()
        self.send.update()

    def eventFilter(self, obj, ev):
        if obj is self.entry and ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._anim.stop()
            self._anim.setStartValue(self.focus)
            self._anim.setEndValue(1.0 if ev.type() == QEvent.Type.FocusIn else 0.0)
            self._anim.start()
        return super().eventFilter(obj, ev)

    def paintEvent(self, _):
        P, M, C, S = self.pres, self.GM, self.CUT, self.CUT_S
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(M, M, -M, -M)
        p.setBrush(NOBRUSH)
        for i in range(1, M + 1):  # soft halo that follows the cut shape
            p.setPen(QPen(rgba(P.c1, (10 + 60 * self.focus) * (1 - i / M) ** 2), 1.4))
            k = 0.414 * i
            p.drawPath(chamfer(r.adjusted(-i, -i, i, i), C + k, S + k, C + k, S + k))
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor(150, 200, 255, 16))
        g.setColorAt(1, QColor(150, 200, 255, 5))
        p.setBrush(g)
        p.setPen(QPen(rgba(P.c1, 50 + 120 * self.focus), 1.1))
        p.drawPath(chamfer(r, C, S, C, S))
        p.setPen(pen_of(P.c1, 150 + 105 * self.focus, 2.2))  # bright cut edges
        p.drawLine(QPointF(r.left(), r.top() + C), QPointF(r.left() + C, r.top()))
        p.drawLine(QPointF(r.right() - C, r.bottom()), QPointF(r.right(), r.bottom() - C))
        if self.focus > 0.01:  # underline grows from the middle on focus
            half = r.width() / 2 * self.focus
            cx, y = r.center().x(), r.bottom() - 0.5
            ug = QLinearGradient(cx - half, 0, cx + half, 0)
            ug.setColorAt(0, rgba(P.c1, 0))
            ug.setColorAt(0.5, rgba(lighten(P.c1, 0.4), 255))
            ug.setColorAt(1, rgba(P.c1, 0))
            p.setPen(QPen(ug, 2.0))
            p.drawLine(QPointF(cx - half, y), QPointF(cx + half, y))
        draw_sigil(p, P.c1, r.left() + C + 14, r.center().y(), 7.5, P.t, 0.6 + 0.6 * self.focus)


# --------------------------------------------------------------------------------------
# Conversation
# --------------------------------------------------------------------------------------
class Sigil(QWidget):
    """The little mark beside Ai-ru's messages."""

    def __init__(self, pres):
        super().__init__()
        self.pres = pres
        self.setFixedSize(26, 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_sigil(p, self.pres.c1, 13, 13, 9, 0.9, 0.8)


class Bubble(QWidget):
    """Message plate with cut corners. User plates cut top-left/bottom-right, Ai-ru's the mirror."""

    def __init__(self, pres, kind):
        super().__init__()
        self.pres, self.kind = pres, kind

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.kind == "user":
            path = chamfer(r, 14, 4, 14, 4)
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, rgba(P.c1, 34))
            g.setColorAt(1, rgba(P.c2, 10))
            p.setBrush(g)
            p.setPen(QPen(rgba(P.c1, 80), 1.0))
            p.drawPath(path)
            p.setPen(pen_of(P.c1, 200, 1.8))
            p.drawLine(QPointF(r.left(), r.top() + 14), QPointF(r.left() + 14, r.top()))
        else:
            path = chamfer(r, 4, 14, 4, 14)
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor(150, 200, 255, 16))
            g.setColorAt(1, QColor(150, 200, 255, 4))
            p.setBrush(g)
            p.setPen(QPen(rgba(P.c1, 44), 1.0))
            p.drawPath(path)
            lg = QLinearGradient(0, r.top(), 0, r.bottom())  # glowing spine on the left
            lg.setColorAt(0, rgba(P.c1, 0))
            lg.setColorAt(0.25, rgba(P.c1, 230))
            lg.setColorAt(0.75, rgba(P.c1, 230))
            lg.setColorAt(1, rgba(P.c1, 0))
            p.setPen(QPen(lg, 2.2))
            p.drawLine(QPointF(r.left() + 0.5, r.top() + 4), QPointF(r.left() + 0.5, r.bottom() - 4))
            p.setPen(pen_of(P.c1, 190, 1.8))
            p.drawLine(QPointF(r.right() - 14, r.top()), QPointF(r.right(), r.top() + 14))


class MessageRow(QWidget):
    MAXW = 700

    def __init__(self, pres, sender, text, animate=True):
        super().__init__()
        user = is_user(sender)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.body = QLabel()
        self.body.setObjectName("body")
        self.body.setFont(make_font(12.5))
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._full, self._n, self._timer, self._hl = text, 0, None, False
        fm = QFontMetrics(self.body.font())
        width = fm.boundingRect(QRect(0, 0, self.MAXW, 100000), int(Qt.TextFlag.TextWordWrap.value), text).width() + 8
        self.body.setMaximumWidth(min(width, self.MAXW))
        self.body.setMinimumWidth(min(width, 360))  # QLabel's own wrap guess is narrow; keep bubbles roomy
        self.body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        if user:
            self.body.setTextFormat(Qt.TextFormat.PlainText)
            self.body.setText(text)
            bub = Bubble(pres, "user")
            bl = QVBoxLayout(bub)
            bl.setContentsMargins(20, 12, 18, 12)
            bl.addWidget(self.body)
            outer.addStretch(1)
            outer.addWidget(bub, 0)
        else:
            name = QLabel(APP_NAME if is_ai(sender) else str(sender).title())
            name.setObjectName("sender")
            name.setFont(make_font(9.5, QFont.Weight.DemiBold, 1.6))
            bub = Bubble(pres, "ai")
            bl = QVBoxLayout(bub)
            bl.setContentsMargins(20, 11, 20, 13)
            bl.setSpacing(5)
            bl.addWidget(name)
            bl.addWidget(self.body)
            outer.addWidget(Sigil(pres), 0, Qt.AlignmentFlag.AlignTop)
            outer.addWidget(bub, 0, Qt.AlignmentFlag.AlignTop)
            outer.addStretch(1)  # keeps Ai-ru's replies hugging the left edge
            if animate and len(text) <= 1500:
                self.body.setTextFormat(Qt.TextFormat.RichText)
                self._reveal(0)
                self._timer = QTimer(self)
                self._timer.timeout.connect(self._step)
                self._timer.start(18)
            else:
                self.body.setTextFormat(Qt.TextFormat.PlainText)
                self.body.setText(text)
        if animate:
            fx = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(fx)
            a = QPropertyAnimation(fx, b"opacity", self)
            a.setDuration(320)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.finished.connect(lambda: self.setGraphicsEffect(None))
            a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def set_highlight(self, query, current=False, accent=(26, 170, 255)):
        """Marks every match of `query` in this message (the current match is brighter)."""
        if self._timer is not None and self._timer.isActive():
            self._timer.stop()
        q = query.strip()
        if not q:
            if self._hl:
                self._hl = False
                self.body.setTextFormat(Qt.TextFormat.PlainText)
                self.body.setText(self._full)
            return
        self._hl = True
        esc = lambda t: html.escape(t).replace("\n", "<br>")
        bg = hexstr(lighten(accent, 0.15) if current else mix(accent, (0, 0, 0), 0.55))
        out, pos = [], 0
        for m in re.finditer(re.escape(q), self._full, re.IGNORECASE):
            out.append(esc(self._full[pos:m.start()]))
            out.append(f'<span style="background-color:{bg};color:#ffffff">{esc(m.group(0))}</span>')
            pos = m.end()
        out.append(esc(self._full[pos:]))
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setText("".join(out))

    def _reveal(self, n):
        esc = lambda s: html.escape(s).replace("\n", "<br>")
        self.body.setText(f'<span>{esc(self._full[:n])}</span><span style="color:rgba(0,0,0,0)">{esc(self._full[n:])}</span>')

    def _step(self):
        self._n += max(2, len(self._full) // 110)
        if self._n >= len(self._full):
            self._timer.stop()
            self.body.setTextFormat(Qt.TextFormat.PlainText)
            self.body.setText(self._full)
        else:
            self._reveal(self._n)


class ChatView(QScrollArea):
    def __init__(self, pres):
        super().__init__()
        self.pres, self.rows = pres, []
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)
        self.inner = QWidget()
        self.inner.setObjectName("chatInner")
        self.setWidget(self.inner)
        self.lay = QVBoxLayout(self.inner)
        self.lay.setContentsMargins(4, 10, 12, 10)
        self.lay.setSpacing(16)
        self.lay.addStretch(1)
        self.stick = True
        sb = self.verticalScrollBar()
        sb.rangeChanged.connect(lambda lo, hi: self.stick and sb.setValue(hi))
        sb.valueChanged.connect(lambda v: setattr(self, "stick", v >= sb.maximum() - 48))
        self.hint = QLabel("Nothing here yet.\nType below, say the wake phrase, or press Ctrl+K.", self.viewport())
        self.hint.setObjectName("hint")
        ufont(self.hint, 11.5)
        self.hint.setAlignment(ALIGN_C)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.hint.setGeometry(self.viewport().rect())

    def clear(self):
        while self.lay.count() > 1:
            w = self.lay.takeAt(1).widget()
            if w:
                w.deleteLater()
        self.rows = []
        self.stick = True
        self.hint.show()

    def add_message(self, sender, text, animate=True):
        self.hint.hide()
        row = MessageRow(self.pres, sender, text, animate)
        self.rows.append(row)
        self.lay.addWidget(row)
        return row

    def scroll_to(self, row):
        self.stick = False
        QTimer.singleShot(40, lambda: self.ensureWidgetVisible(row, 0, 90))


# --------------------------------------------------------------------------------------
# Sidebar (slides in and out)
# --------------------------------------------------------------------------------------
class Wordmark(QWidget):
    def __init__(self, pres):
        super().__init__()
        self.pres = pres
        self.setFixedHeight(58)

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        draw_sigil(p, P.c1, 28, cy, 14 * (1 + 0.04 * math.sin(P.phase)), P.t)
        p.setFont(make_font(20, QFont.Weight.DemiBold, 3.2))
        p.setPen(QColor(234, 245, 255))
        p.drawText(QRectF(58, 0, self.width() - 58, self.height()), ALIGN_VL, APP_NAME)


class ChronicleItem(QWidget):
    clicked = pyqtSignal(int)
    delete_requested = pyqtSignal(int)

    def __init__(self, pres, session, active):
        super().__init__()
        self.pres, self.s, self.active, self.hover, self.x_hover = pres, session, active, False, False
        self.setFixedHeight(60)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _x_rect(self):
        return QRectF(self.width() - 34, self.height() / 2 - 12, 24, 24)

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = self.x_hover = False
        self.update()

    def mouseMoveEvent(self, e):
        xh = self._x_rect().contains(e.position())
        if xh != self.x_hover:
            self.x_hover = xh
            self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._x_rect().contains(e.position()):
            self.delete_requested.emit(self.s["id"])
        else:
            self.clicked.emit(self.s["id"])

    def contextMenuEvent(self, e):
        m = QMenu(self)
        add_action(m, "Open", lambda: self.clicked.emit(self.s["id"]))
        add_action(m, "Delete", lambda: self.delete_requested.emit(self.s["id"]))
        m.exec(e.globalPos())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r, c = QRectF(self.rect()).adjusted(2, 2, -2, -2), self.pres.c1
        shape = chamfer(r, 0, 0, 12, 0)
        p.setPen(NOPEN)
        if self.active:
            g = QLinearGradient(r.left(), 0, r.right(), 0)
            g.setColorAt(0, rgba(c, 44))
            g.setColorAt(1, rgba(c, 2))
            p.setBrush(g)
            p.drawPath(shape)
            p.setBrush(rgba(c, 240))  # tall marker on the left edge
            p.drawRect(QRectF(r.left(), r.top(), 3, r.height()))
        elif self.hover:
            p.setBrush(QColor(150, 200, 255, 12))
            p.drawPath(shape)
        p.setFont(make_font(10.5, QFont.Weight.Medium))
        p.setPen(QColor("#e4f2ff") if self.active else QColor("#8ea9c0"))
        title = p.fontMetrics().elidedText(self.s["title"], Qt.TextElideMode.ElideRight, int(r.width() - (62 if self.hover else 34)))
        p.drawText(QPointF(r.left() + 18, r.top() + 24), title)
        n = len(self.s["msgs"])
        p.setFont(make_font(8.5))
        p.setPen(QColor("#4d6882"))
        p.drawText(QPointF(r.left() + 18, r.top() + 43), f"{fmt_ts(self.s['ts'])}, {n} message{'s' if n != 1 else ''}")
        if self.hover:  # delete cross
            xr = self._x_rect()
            if self.x_hover:
                p.setPen(NOPEN)
                p.setBrush(QColor(255, 92, 116, 60))
                p.drawPath(chamfer(xr, 6, 0, 6, 0))
            p.setPen(pen_of((255, 120, 140) if self.x_hover else (150, 175, 195), 255, 1.5))
            cx, cy = xr.center().x(), xr.center().y()
            p.drawLine(QPointF(cx - 3.5, cy - 3.5), QPointF(cx + 3.5, cy + 3.5))
            p.drawLine(QPointF(cx - 3.5, cy + 3.5), QPointF(cx + 3.5, cy - 3.5))


class Sidebar(QWidget):
    W = 276
    new_requested = pyqtSignal()
    session_selected = pyqtSignal(int)
    session_delete = pyqtSignal(int)

    def __init__(self, pres):
        super().__init__()
        self.pres, self.active_item, self.cut = pres, None, 30
        self.setFixedWidth(self.W)
        self.panel = QWidget(self)  # fixed-width content that slides under the clip
        self.panel.setFixedWidth(self.W)
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(14, 18, 10, 16)
        lay.setSpacing(0)
        self.wordmark = Wordmark(pres)
        lay.addWidget(self.wordmark)
        lay.addSpacing(14)
        self.new_btn = PillButton(pres, "New session", "Ctrl+N")
        self.new_btn.press_sound = None
        self.new_btn.clicked.connect(self.new_requested)
        lay.addWidget(self.new_btn)
        lay.addSpacing(22)
        lbl = QLabel("Chronicle")
        lbl.setObjectName("sideLabel")
        ufont(lbl, 9.5, QFont.Weight.DemiBold, 2.4)
        lbl.setContentsMargins(10, 0, 0, 0)
        lay.addWidget(lbl)
        lay.addSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("chronInner")
        self.col = QVBoxLayout(inner)
        self.col.setContentsMargins(0, 0, 4, 0)
        self.col.setSpacing(3)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)
        foot = QLabel("Saved on this device in ~/.airu\nhover a session and hit x to delete it")
        foot.setObjectName("sideFoot")
        ufont(foot, 8.5)
        foot.setContentsMargins(10, 10, 0, 0)
        lay.addWidget(foot)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.panel.setGeometry(self.width() - self.W, 0, self.W, self.height())

    def set_sessions(self, sessions, current_id):
        while self.col.count():
            it = self.col.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.active_item = None
        for s in sessions:
            if not s["msgs"] and s["id"] != current_id:
                continue
            item = ChronicleItem(self.pres, s, s["id"] == current_id)
            item.clicked.connect(self.session_selected)
            item.delete_requested.connect(self.session_delete)
            if s["id"] == current_id:
                self.active_item = item
            self.col.addWidget(item)
        self.col.addStretch(1)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        shape = chamfer(r, self.cut, 0, 0, self.cut * 0.4)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(150, 200, 255, 14))
        g.setColorAt(1, QColor(150, 200, 255, 4))
        p.setPen(NOPEN)
        p.setBrush(g)
        p.drawPath(shape)
        line = QLinearGradient(0, 0, 0, self.height())
        line.setColorAt(0, rgba(self.pres.c1, 0))
        line.setColorAt(0.5, rgba(self.pres.c1, 70))
        line.setColorAt(1, rgba(self.pres.c1, 0))
        p.fillRect(QRectF(self.width() - 1, 0, 1, self.height()), line)


# --------------------------------------------------------------------------------------
# Overlays: toast (optionally with an action) and command palette
# --------------------------------------------------------------------------------------
class Toast(QWidget):
    def __init__(self, parent, pres):
        super().__init__(parent)
        self.pres, self.text, self.a = pres, "", 0.0
        self.action, self.cb = None, None
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._set)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._fade(0.0, 300))
        self.hide()

    def _set(self, v):
        self.a = float(v)
        if self.a <= 0.001 and self._anim.endValue() == 0.0:
            self.hide()
        self.update()

    def _fade(self, to, ms):
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setStartValue(self.a)
        self._anim.setEndValue(to)
        self._anim.start()

    def show_text(self, text, ms=2400, action=None, cb=None):
        self.text, self.action, self.cb = text, action, cb
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, action is None)
        fm = QFontMetrics(make_font(10.5))
        lines = text.split("\n")
        w = max(fm.horizontalAdvance(l) for l in lines) + 60 + (fm.horizontalAdvance(action) + 34 if action else 0)
        h = len(lines) * 22 + 22
        self.setFixedSize(w, h)
        par = self.parentWidget()
        self.move((par.width() - w) // 2, par.M + 64)
        self.raise_()
        self.show()
        self._fade(1.0, 180)
        self._timer.start(ms)

    def _action_rect(self):
        fm = QFontMetrics(make_font(10.5, QFont.Weight.DemiBold))
        aw = fm.horizontalAdvance(self.action or "") + 22
        return QRectF(self.width() - aw - 8, 8, aw, self.height() - 16)

    def mousePressEvent(self, e):
        if self.action and self._action_rect().contains(e.position()):
            cb = self.cb
            self.action = self.cb = None
            self._fade(0.0, 160)
            if cb:
                QTimer.singleShot(0, cb)

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        cut = min(14.0, r.height() * 0.34)
        p.setBrush(QColor(4, 8, 14, int(238 * self.a)))
        p.setPen(QPen(rgba(P.c1, 130 * self.a), 1.1))
        p.drawPath(chamfer(r, cut, 0, cut, 0))
        p.setPen(pen_of(P.c1, 230 * self.a, 2.0))
        p.drawLine(QPointF(r.left(), r.top() + cut), QPointF(r.left() + cut, r.top()))
        p.setFont(make_font(10.5))
        p.setPen(rgba((226, 242, 255), 255 * self.a))
        tr = r.adjusted(0, 0, -(self._action_rect().width() + 4) if self.action else 0, 0)
        p.drawText(tr, ALIGN_C, self.text)
        if self.action:
            ar = self._action_rect()
            p.setBrush(rgba(P.c1, 40 * self.a))
            p.setPen(NOPEN)
            p.drawPath(chamfer(ar, 7, 0, 7, 0))
            p.setFont(make_font(10.5, QFont.Weight.DemiBold))
            p.setPen(rgba(lighten(P.c1, 0.5), 255 * self.a))
            p.drawText(ar, ALIGN_C, self.action)


class CommandPalette(QWidget):
    ROW = 44

    def __init__(self, parent, pres):
        super().__init__(parent)
        self.pres, self.items, self.shown, self.sel, self.hover = pres, [], [], 0, -1
        self.entry = QLineEdit(self)
        self.entry.setObjectName("paletteEntry")
        ufont(self.entry, 13.5)
        self.entry.setPlaceholderText("Type a command or a session title")
        self.entry.setFrame(False)
        self.entry.textChanged.connect(self._filter)
        self.entry.installEventFilter(self)
        self.setMouseTracking(True)
        self.hide()

    def open_(self, items):
        self.items = items
        self.setGeometry(self.parentWidget().rect())
        self.entry.clear()
        self._filter("")
        self.raise_()
        self.show()
        self.entry.setFocus()
        sfx("palette")

    def close_(self):
        if self.isVisible():
            self.hide()
            self.parentWidget().dock.entry.setFocus()

    def _filter(self, q):
        words = q.lower().split()
        self.shown = [it for it in self.items if all(w in it[0].lower() for w in words)][:8]
        self.sel, self.hover = 0, -1
        self._layout()
        self.update()

    def _panel(self):
        w = min(620, self.width() - 80)
        rows = max(1, len(self.shown))
        return QRectF((self.width() - w) / 2, max(40, self.height() * 0.16), w, 68 + rows * self.ROW + 10)

    def _layout(self):
        pr = self._panel()
        self.entry.setGeometry(int(pr.x() + 56), int(pr.y() + 16), int(pr.width() - 82), 36)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def _run(self, i):
        if 0 <= i < len(self.shown):
            fn = self.shown[i][2]
            self.close_()
            QTimer.singleShot(0, fn)

    def eventFilter(self, obj, ev):
        if obj is self.entry and ev.type() == QEvent.Type.KeyPress:
            k, n = ev.key(), len(self.shown)
            if k == Qt.Key.Key_Down and n:
                self.sel = (self.sel + 1) % n
            elif k == Qt.Key.Key_Up and n:
                self.sel = (self.sel - 1) % n
            elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run(self.sel)
            elif k == Qt.Key.Key_Escape:
                self.close_()
            else:
                return super().eventFilter(obj, ev)
            self.update()
            return True
        return super().eventFilter(obj, ev)

    def _row_at(self, pos):
        pr = self._panel()
        y = pos.y() - (pr.y() + 68)
        if pr.contains(pos) and y >= 0:
            i = int(y // self.ROW)
            return i if i < len(self.shown) else -1
        return -1

    def mouseMoveEvent(self, e):
        i = self._row_at(e.position())
        if i != self.hover:
            self.hover = i
            if i >= 0:
                self.sel = i
            self.update()

    def mousePressEvent(self, e):
        if not self._panel().contains(e.position()):
            self.close_()
        else:
            self._run(self._row_at(e.position()))

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 2, 6, 165))
        pr = self._panel()
        p.setBrush(NOBRUSH)
        for i in range(1, 15):
            p.setPen(QPen(rgba(P.c1, 70 * (1 - i / 15) ** 2), 1.4))
            k = 0.414 * i
            p.drawPath(chamfer(pr.adjusted(-i, -i, i, i), 24 + k, 5 + k, 24 + k, 5 + k))
        p.setBrush(QColor(3, 6, 11, 250))
        p.setPen(QPen(rgba(P.c1, 140), 1.1))
        p.drawPath(chamfer(pr, 24, 5, 24, 5))
        p.setPen(pen_of(P.c1, 230, 2.2))
        p.drawLine(QPointF(pr.left(), pr.top() + 24), QPointF(pr.left() + 24, pr.top()))
        p.drawLine(QPointF(pr.right() - 24, pr.bottom()), QPointF(pr.right(), pr.bottom() - 24))
        draw_sigil(p, P.c1, pr.x() + 36, pr.y() + 34, 8, P.t)
        p.setPen(QPen(rgba(P.c1, 45), 1))
        p.drawLine(QPointF(pr.x() + 16, pr.y() + 62), QPointF(pr.right() - 16, pr.y() + 62))
        if not self.shown:
            p.setFont(make_font(11))
            p.setPen(QColor("#4a6580"))
            p.drawText(QRectF(pr.x(), pr.y() + 68, pr.width(), self.ROW), ALIGN_C, "No match")
            return
        for i, (name, hint, _fn) in enumerate(self.shown):
            r = QRectF(pr.x() + 10, pr.y() + 68 + i * self.ROW, pr.width() - 20, self.ROW)
            if i == self.sel:
                g = QLinearGradient(r.left(), 0, r.right(), 0)
                g.setColorAt(0, rgba(P.c1, 46))
                g.setColorAt(1, rgba(P.c1, 6))
                p.setPen(NOPEN)
                p.setBrush(g)
                p.drawPath(chamfer(r.adjusted(0, 3, 0, -3), 0, 0, 10, 0))
                p.setBrush(rgba(P.c1, 240))
                p.drawRect(QRectF(r.left(), r.top() + 3, 2.5, r.height() - 6))
            p.setFont(make_font(11.5, QFont.Weight.Medium))
            p.setPen(QColor("#ecf6ff") if i == self.sel else QColor("#9db8cd"))
            p.drawText(QRectF(r.left() + 18, r.top(), r.width() * 0.62, r.height()), ALIGN_VL, name)
            p.setFont(make_font(9))
            p.setPen(QColor("#53728a"))
            p.drawText(QRectF(r.left(), r.top(), r.width() - 16, r.height()), ALIGN_VR, hint)


# --------------------------------------------------------------------------------------
# The floating Ai-ru orb: lives in the corner whenever the app is in the background
# --------------------------------------------------------------------------------------
class MiniOrb(QWidget):
    S, CAP = 160, 440
    BTNS = (("ask", math.radians(150)), ("mic", math.radians(100)), ("open", math.radians(50)))

    def __init__(self, pres):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        mac = getattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow", None)
        if mac is not None:
            self.setAttribute(mac)
        self.setMouseTracking(True)
        self.pres = pres
        self.cloud = DustCloud(650)
        self.hover, self.hv, self.btn_hover, self.mic_on = False, 0.0, -1, False
        noop = lambda *a: None
        self.on_restore = self.on_quit = self.on_mute = self.on_mic = self.on_submit = self.on_mode = noop
        self.saved_right = self.saved_y = None
        self.mode, self._toast = "orb", ""
        self._press, self._press_btn, self._moved, self._placed = None, -1, False, False
        self.setFixedSize(self.S, self.S)
        self.entry = QLineEdit(self)
        self.entry.setObjectName("orbEntry")
        ufont(self.entry, 10.5)
        self.entry.setPlaceholderText("Ask Ai-ru...")
        self.entry.setFrame(False)
        self.entry.hide()
        self.entry.returnPressed.connect(self._submit)
        self.entry.installEventFilter(self)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._tt = QTimer(self)
        self._tt.setSingleShot(True)
        self._tt.timeout.connect(self._end_toast)

    # --- lifecycle
    def right_edge(self):
        return self.x() + self.width()

    def appear(self, msg=None):
        if self.isVisible():
            if msg:
                self.show_toast(msg)
            return
        geo = QApplication.primaryScreen().availableGeometry()
        if not self._placed:
            right = self.saved_right if self.saved_right is not None else geo.right() - 22
            y = self.saved_y if self.saved_y is not None else geo.bottom() - self.height() - 22
            right = int(clamp(right, geo.left() + self.width(), geo.right()))
            y = int(clamp(y, geo.top(), geo.bottom() - self.height()))
            self.move(right - self.width(), y)
            self._placed = True
        self.setWindowOpacity(0.0)
        self.show()
        self._fade.stop()
        self._fade.setDuration(260)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        if msg:
            self.show_toast(msg)

    def vanish(self):
        if not self.isVisible():
            return
        self._tt.stop()
        self.close_ask()
        self._end_toast()
        self.hide()

    def set_mic(self, on):
        self.mic_on = on

    # --- capsule (toast / quick ask) to the left of the orb
    def _set_width(self, w):
        if self.width() == w:
            return
        right = self.right_edge()
        self.setFixedSize(w, self.S)
        self.move(right - w, self.y())

    def _capsule(self):
        cx = self.width() - self.S / 2
        return QRectF(10, self.S / 2 - 27, cx - self.S * 0.36 * 1.3 - 10, 54)

    def show_toast(self, text):
        if not self.isVisible() or self.mode == "ask":
            return
        text = " ".join(text.split())
        self._toast = text[:110] + ("..." if len(text) > 110 else "")
        self._set_width(self.CAP)
        self._tt.start(4800)

    def _end_toast(self):
        self._toast = ""
        if self.mode != "ask":
            self._set_width(self.S)
        self.update()

    def open_ask(self):
        if not self.isVisible():
            return
        self._tt.stop()
        self._toast, self.mode = "", "ask"
        self._set_width(self.CAP)
        r = self._capsule().adjusted(26, 8, -16, -8)
        self.entry.setGeometry(r.toRect())
        self.entry.clear()
        self.entry.show()
        self.activateWindow()
        self.raise_()
        self.entry.setFocus()

    def close_ask(self):
        if self.mode != "ask":
            return
        self.mode = "orb"
        self.entry.hide()
        self.entry.clear()
        if not self._toast:
            self._set_width(self.S)

    def _submit(self):
        text = self.entry.text().strip()
        self.close_ask()
        if text:
            self.on_submit(text)
            self.show_toast("Sent. Ai-ru is on it")

    def eventFilter(self, obj, ev):
        if obj is self.entry and ev.type() == QEvent.Type.FocusOut and self.mode == "ask":
            QTimer.singleShot(150, lambda: self.close_ask() if not self.entry.hasFocus() else None)
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_ask()
        else:
            super().keyPressEvent(e)

    def tick(self, dt):
        self.hv = approach(self.hv, 1.0 if (self.hover or self.mode == "ask") else 0.0, 12, dt)

    # --- geometry helpers
    def _center(self):
        return self.width() - self.S / 2, self.S / 2

    def _btn_pos(self, i):
        cx, cy = self._center()
        rb = self.S * 0.36 * 1.17
        a = self.BTNS[i][1]
        return cx + rb * math.cos(a), cy + rb * math.sin(a)

    def _btn_at(self, pos):
        if self.hv < 0.4:
            return -1
        for i in range(len(self.BTNS)):
            bx, by = self._btn_pos(i)
            if math.hypot(pos.x() - bx, pos.y() - by) < 13:
                return i
        return -1

    def _act(self, name):
        if name == "mic":
            self.on_mic()
        elif name == "ask":
            self.open_ask()
        else:
            self.on_restore()

    # --- input
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press, self._moved = e.globalPosition(), False
            self._press_btn = self._btn_at(e.position())

    def mouseMoveEvent(self, e):
        cx, cy = self._center()
        self.hover = math.hypot(e.position().x() - cx, e.position().y() - cy) < self.S * 0.36 * 1.17 + 14
        self.btn_hover = self._btn_at(e.position())
        if self._press is not None and self._press_btn < 0 and not self._moved:
            d = e.globalPosition() - self._press
            if abs(d.x()) + abs(d.y()) > 6:
                self._moved = True
                h = self.windowHandle()
                if h:
                    h.startSystemMove()
                self._press = None
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._press is not None and not self._moved:
            if self._press_btn >= 0:
                if self._btn_at(e.position()) == self._press_btn:
                    self._act(self.BTNS[self._press_btn][0])
            elif self.mode != "ask":
                self.on_restore()
        self._press, self._press_btn = None, -1

    def moveEvent(self, e):
        super().moveEvent(e)
        if self._placed and self.isVisible():
            self.saved_right, self.saved_y = self.right_edge(), self.y()

    def leaveEvent(self, e):
        self.hover, self.btn_hover = False, -1
        self.update()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        add_action(m, "Open Ai-ru", self.on_restore)
        add_action(m, "Quick ask", self.open_ask)
        add_action(m, "Mic off" if self.mic_on else "Mic on", self.on_mic)
        add_action(m, "Toggle sound", self.on_mute)
        add_action(m, "Cycle orb mode", self.on_mode)
        m.addSeparator()
        add_action(m, "Quit", self.on_quit)
        m.exec(e.globalPos())

    # --- painting
    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c1, c2, t, en = P.c1, P.c2, P.t, P.energy
        cx, cy = self._center()
        Ro = self.S * 0.36 * (1.05 if self.hover else 1.0)

        if self.mode == "ask" or self._toast:
            r = self._capsule()
            cut = r.height() * 0.34
            p.setBrush(QColor(4, 8, 14, 236))
            p.setPen(QPen(rgba(c1, 130), 1.1))
            p.drawPath(chamfer(r, cut, 0, cut, 0))
            p.setPen(pen_of(c1, 230, 2.0))
            p.drawLine(QPointF(r.left(), r.top() + cut), QPointF(r.left() + cut, r.top()))
            if self.mode != "ask":
                p.setFont(make_font(9.5))
                p.setPen(QColor(222, 240, 255))
                opt = QTextOption(ALIGN_VL)
                opt.setWrapMode(QTextOption.WrapMode.WordWrap)
                p.drawText(r.adjusted(26, 4, -16, -4), self._toast, opt)

        p.translate(cx, cy + 2.5 * math.sin(t * 1.4))  # gentle float
        p.setCompositionMode(ADD)
        p.setPen(NOPEN)
        g = QRadialGradient(0, 0, Ro * 1.35)
        g.setColorAt(0, rgba(c2, 90 + 90 * en))
        g.setColorAt(0.5, rgba(c2, 28 + 34 * en))
        g.setColorAt(1, rgba(c2, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), Ro * 1.35, Ro * 1.35)
        p.setCompositionMode(SRC)

        p.setBrush(NOBRUSH)
        if P.state != "IDLE":  # hexagonal ripples
            for k in range(2):
                pr = (t * 0.8 + k * 0.5) % 1.0
                p.setPen(QPen(rgba(c1, 170 * (1 - pr) ** 1.6), 1.6))
                p.drawPath(hex_path(0, 0, Ro * (0.7 + 0.5 * pr)))
        rot, R1 = P.rot * 0.15, Ro * 1.12  # hex frame with alternating bright edges
        verts = [QPointF(R1 * math.cos(rot - math.pi / 2 + i * TAU / 6), R1 * math.sin(rot - math.pi / 2 + i * TAU / 6))
                 for i in range(6)]
        for i in range(6):
            bright = i % 2 == 0
            p.setPen(pen_of(c1, 200 if bright else 55, 2.0 if bright else 1.0))
            p.drawLine(verts[i], verts[(i + 1) % 6])

        self.cloud.draw(p, P, Ro * 0.8, size=0.95)

        # hover buttons: ask / mic / open, as small hexagons
        p.resetTransform()
        a = self.hv
        if a > 0.02:
            for i, (name, _ang) in enumerate(self.BTNS):
                bx, by = self._btn_pos(i)
                hov = i == self.btn_hover
                active = name == "mic" and self.mic_on
                p.setBrush(rgba(c1, 150 * a) if active else QColor(5, 9, 15, int(236 * a)))
                p.setPen(QPen(rgba(c1, (130 + 125 * hov) * a), 1.2))
                p.drawPath(hex_path(bx, by, 12.0 if hov else 11.0))
                p.save()
                p.translate(bx, by)
                p.setPen(pen_of(lighten(c1, 0.6) if not active else (5, 5, 8), 255 * a, 1.3))
                p.setBrush(NOBRUSH)
                if name == "mic":
                    p.drawRoundedRect(QRectF(-2, -5, 4, 7), 2, 2)
                    p.drawArc(QRectF(-4, -3, 8, 8), 180 * 16, 180 * 16)
                    p.drawLine(QPointF(0, 5), QPointF(0, 6.5))
                elif name == "ask":
                    p.setBrush(rgba(lighten(c1, 0.6), 255 * a))
                    p.setPen(NOPEN)
                    for dx in (-3.6, 0, 3.6):
                        p.drawEllipse(QPointF(dx, 0), 1.2, 1.2)
                else:
                    p.drawLine(QPointF(-3, 3), QPointF(3, -3))
                    p.drawPolyline(QPolygonF([QPointF(-0.5, -3), QPointF(3, -3), QPointF(3, 0.5)]))
                p.restore()


# --------------------------------------------------------------------------------------
# Settings building blocks
# --------------------------------------------------------------------------------------
class Divider(QWidget):
    """Hairline between the header's tool icons and the window controls."""

    def __init__(self, pres):
        super().__init__()
        self.pres = pres
        self.setFixedSize(13, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, rgba(self.pres.c1, 0))
        g.setColorAt(0.5, rgba(self.pres.c1, 110))
        g.setColorAt(1, rgba(self.pres.c1, 0))
        p.fillRect(QRectF(6, 0, 1, self.height()), g)


class ClickLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text=""):
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        else:
            super().mousePressEvent(e)


class Toggle(QAbstractButton):
    """Cut-corner switch."""

    def __init__(self, pres, checked=False):
        super().__init__()
        self.pres, self._k = pres, 1.0 if checked else 0.0
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(46, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.valueChanged.connect(self._set)
        self.toggled.connect(self._go)

    def _set(self, v):
        self._k = float(v)
        self.update()

    def _go(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._k)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sync(self, on):
        self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(False)
        self._anim.stop()
        self._k = 1.0 if on else 0.0
        self.update()

    def paintEvent(self, _):
        c, k = self.pres.c1, self._k
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(rgba(c, 18 + 90 * k))
        p.setPen(QPen(rgba(c, 90 + 130 * k), 1.1))
        p.drawPath(chamfer(r, 7, 0, 7, 0))
        kx = r.left() + 4 + (r.width() - 8 - 14) * k
        p.setPen(NOPEN)
        p.setBrush(rgba(lighten(c, 0.55), 255 if k > 0.5 else 140))
        p.drawPath(chamfer(QRectF(kx, r.top() + 4, 14, r.height() - 8), 3, 0, 3, 0))


class PickBox(QComboBox):
    """Combo box with a drawn chevron (the stylesheet removes the native arrow)."""

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(pen_of((150, 200, 255), 200, 1.5))
        x, y = self.width() - 17, self.height() / 2
        p.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))


class ActionButton(GlassButton):
    """Small text button with cut corners."""

    def __init__(self, pres, text, primary=False, danger=False):
        super().__init__(pres)
        self.label, self.primary, self.danger = text, primary, danger
        self.setFixedHeight(34)
        self._width = QFontMetrics(make_font(10, QFont.Weight.DemiBold, 0.6)).horizontalAdvance(text) + 36
        self.setMinimumWidth(self._width)

    def sizeHint(self):
        return QSize(self._width, 34)

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = (255, 92, 116) if self.danger else P.c1
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self.down:
            r = r.adjusted(1, 1, -1, -1)
        if not self.isEnabled():
            p.setOpacity(0.35)
        p.setBrush(rgba(col, (60 if self.primary else 14) + 70 * self.h))
        p.setPen(QPen(rgba(col, 90 + 140 * self.h), 1.1))
        p.drawPath(chamfer(r, 9, 0, 9, 0))
        p.setFont(make_font(10, QFont.Weight.DemiBold, 0.6))
        p.setPen(rgba(lighten(col, 0.6), 255))
        p.drawText(r, ALIGN_C, self.label)


class TabStrip(QWidget):
    changed = pyqtSignal(int)

    def __init__(self, pres, labels):
        super().__init__()
        self.pres, self.labels, self.idx, self.hover = pres, list(labels), 0, -1
        self.setFixedHeight(40)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_index(self, i):
        if i != self.idx:
            self.idx = i
            self.update()

    def _at(self, x):
        return int(clamp(x // (self.width() / len(self.labels)), 0, len(self.labels) - 1))

    def mouseMoveEvent(self, e):
        h = self._at(e.position().x())
        if h != self.hover:
            self.hover = h
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            i = self._at(e.position().x())
            if i != self.idx:
                self.idx = i
                self.changed.emit(i)
                sfx("tick")
                self.update()

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H, n = self.width(), self.height(), len(self.labels)
        p.setPen(QPen(rgba(P.c1, 40), 1))
        p.drawLine(QPointF(0, H - 1), QPointF(W, H - 1))
        cw = W / n
        for i, lab in enumerate(self.labels):
            r = QRectF(i * cw, 2, cw, H - 4)
            on = i == self.idx
            if on:
                g = QLinearGradient(0, r.top(), 0, r.bottom())
                g.setColorAt(0, rgba(P.c1, 6))
                g.setColorAt(1, rgba(P.c1, 40))
                p.setPen(NOPEN)
                p.setBrush(g)
                p.drawPath(chamfer(r.adjusted(2, 0, -2, 0), 9, 0, 0, 0))
                p.setBrush(rgba(P.c1, 255))
                p.drawRect(QRectF(r.left() + 2, H - 3, cw - 4, 2.4))
            p.setFont(make_font(10, QFont.Weight.DemiBold if on else QFont.Weight.Medium, 0.8))
            p.setPen(rgba(lighten(P.c1, 0.55), 255) if on else QColor("#a9c2d8") if i == self.hover else QColor("#6f8aa3"))
            p.drawText(r, ALIGN_C, lab)


class SwatchRow(QWidget):
    picked = pyqtSignal(str)

    def __init__(self, pres, names):
        super().__init__()
        self.pres, self.names = pres, list(names) + ["Custom"]
        self.sel, self.custom, self.hover = "Neon", None, -1
        self.setFixedHeight(46)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, theme, custom):
        self.sel, self.custom = theme, custom
        self.update()

    def _rect(self, i):
        n, gap = len(self.names), 8.0
        w = (self.width() - gap * (n - 1)) / n
        return QRectF(i * (w + gap), 4, w, self.height() - 8)

    def _at(self, pos):
        for i in range(len(self.names)):
            if self._rect(i).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):
        h = self._at(e.position())
        if h != self.hover:
            self.hover = h
            self.setToolTip(self.names[h] if h >= 0 else "")
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._at(e.position())
        if e.button() == Qt.MouseButton.LeftButton and i >= 0:
            self.picked.emit(self.names[i])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, name in enumerate(self.names):
            r = self._rect(i)
            col = THEMES[name][0] if name in THEMES else (self.custom or (110, 120, 145))
            on, hov = name == self.sel, i == self.hover
            shape = chamfer(r, 9, 0, 9, 0)
            if on:
                p.setPen(QPen(rgba(col, 90), 1))
                p.setBrush(NOBRUSH)
                p.drawPath(chamfer(r.adjusted(-3, -3, 3, 3), 11, 0, 11, 0))
            p.setBrush(rgba(col, 215 if (on or hov) else 150))
            p.setPen(QPen(rgba(lighten(col, 0.6), 255 if on else 110), 1.3 if on else 1.0))
            p.drawPath(shape)
            if name == "Custom" and self.custom is None:
                p.setPen(pen_of((235, 245, 255), 230, 1.6))
                c = r.center()
                p.drawLine(QPointF(c.x() - 5, c.y()), QPointF(c.x() + 5, c.y()))
                p.drawLine(QPointF(c.x(), c.y() - 5), QPointF(c.x(), c.y() + 5))


class ShortcutList(QWidget):
    ROW = 32

    def __init__(self, pres, items):
        super().__init__()
        self.pres, self.items = pres, items
        self.setFixedHeight(len(items) * self.ROW + 6)

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fk, fd = make_font(9.5, QFont.Weight.DemiBold, 0.4), make_font(10.5)
        fm = QFontMetrics(fk)
        for i, (keys, desc) in enumerate(self.items):
            y = 3 + i * self.ROW
            r = QRectF(0, y + 4, fm.horizontalAdvance(keys) + 22, self.ROW - 8)
            p.setBrush(rgba(P.c1, 18))
            p.setPen(QPen(rgba(P.c1, 90), 1))
            p.drawPath(chamfer(r, 6, 0, 6, 0))
            p.setFont(fk)
            p.setPen(rgba(lighten(P.c1, 0.5), 255))
            p.drawText(r, ALIGN_C, keys)
            p.setFont(fd)
            p.setPen(QColor("#b7cde0"))
            p.drawText(QRectF(190, y, self.width() - 190, self.ROW), ALIGN_VL, desc)


# --------------------------------------------------------------------------------------
# Chat search bar
# --------------------------------------------------------------------------------------
class ChatSearchBar(QWidget):
    changed = pyqtSignal(str, bool)  # query, search all chats
    step = pyqtSignal(int)           # +1 older match, -1 newer match
    closed = pyqtSignal()

    def __init__(self, pres):
        super().__init__()
        self.pres, self.all_chats = pres, False
        self.setFixedHeight(54)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(52, 8, 12, 8)
        lay.setSpacing(4)
        self.field = QLineEdit()
        self.field.setObjectName("searchField")
        ufont(self.field, 11.5)
        self.field.setPlaceholderText("Search messages")
        self.field.setFrame(False)
        self.field.installEventFilter(self)
        self.count = QLabel("")
        self.count.setObjectName("setHint")
        ufont(self.count, 9.5)
        self.count.setMinimumWidth(70)
        self.count.setAlignment(ALIGN_VR)
        self.scope = ActionButton(pres, "This chat")
        self.scope.setFixedWidth(96)
        self.up, self.down = IconButton(pres, "up"), IconButton(pres, "down")
        self.up.setToolTip("Older match (Enter)")
        self.down.setToolTip("Newer match (Shift+Enter)")
        self.x = IconButton(pres, "close")
        self.x.setToolTip("Close search (Esc)")
        for w in (self.field,):
            lay.addWidget(w, 1)
        for w in (self.count, self.scope, self.up, self.down, self.x):
            lay.addWidget(w)
        self.field.textChanged.connect(self._emit)
        self.scope.clicked.connect(self._toggle_scope)
        self.up.clicked.connect(lambda: self.step.emit(1))
        self.down.clicked.connect(lambda: self.step.emit(-1))
        self.x.clicked.connect(self.closed)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.field and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.step.emit(-1 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                return True
            if k == Qt.Key.Key_Escape:
                self.closed.emit()
                return True
        return super().eventFilter(obj, ev)

    def _emit(self, *_):
        self.changed.emit(self.field.text(), self.all_chats)

    def _toggle_scope(self):
        self.all_chats = not self.all_chats
        self.scope.label = "All chats" if self.all_chats else "This chat"
        self.scope.update()
        self._emit()

    def open_(self, text=""):
        self.show()
        if text:
            self.field.setText(text)
        self.field.setFocus()
        self.field.selectAll()

    def set_count(self, i, n):
        self.count.setText("" if not self.field.text().strip() else ("no matches" if n == 0 else f"{i + 1} of {n}"))

    def paintEvent(self, _):
        P = self.pres
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 4, -1, -4)
        p.setBrush(QColor(150, 200, 255, 11))
        p.setPen(QPen(rgba(P.c1, 80), 1.0))
        p.drawPath(chamfer(r, 14, 4, 14, 4))
        p.setPen(pen_of(P.c1, 220, 1.8))
        p.drawLine(QPointF(r.left(), r.top() + 14), QPointF(r.left() + 14, r.top()))
        p.setPen(pen_of(lighten(P.c1, 0.4), 220, 1.6))  # magnifier
        p.setBrush(NOBRUSH)
        cx, cy = r.left() + 30, r.center().y()
        p.drawEllipse(QPointF(cx - 1, cy - 1), 5.2, 5.2)
        p.drawLine(QPointF(cx + 2.8, cy + 2.8), QPointF(cx + 6.5, cy + 6.5))


# --------------------------------------------------------------------------------------
# The settings drawer (slides in from the right and pushes the layout; no overlay)
# --------------------------------------------------------------------------------------
class SettingsDrawer(QWidget):
    W = 420
    TABS = ("Persona", "Look", "Voice", "Wake", "Keys")

    def __init__(self, win):
        super().__init__()
        self.win, self.pres, self.cut, self._busy = win, win.pres, 30, False
        self._loaded_name, self._loaded_prompt = None, None
        self.setFixedWidth(0)
        self.panel = QWidget(self)
        self.panel.setFixedWidth(self.W)
        root = QVBoxLayout(self.panel)
        root.setContentsMargins(24, 20, 20, 18)
        root.setSpacing(0)
        head = QHBoxLayout()
        title = QLabel("Settings")
        title.setObjectName("drawerTitle")
        ufont(title, 16, QFont.Weight.DemiBold, 1.4)
        close = IconButton(self.pres, "close")
        close.clicked.connect(win.close_settings)
        head.addWidget(title, 1)
        head.addWidget(close)
        root.addLayout(head)
        root.addSpacing(10)
        self.tabs = TabStrip(self.pres, self.TABS)
        root.addWidget(self.tabs)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        for build in (self._persona_page, self._look_page, self._voice_page, self._wake_page, self._keys_page):
            self.stack.addWidget(self._scroll(build()))
        self.tabs.changed.connect(self.stack.setCurrentIndex)

    # ---------- small builders
    def _scroll(self, page):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.viewport().setAutoFillBackground(False)
        page.setObjectName("pageInner")
        sc.setWidget(page)
        return sc

    def _page(self):
        pg = QWidget()
        v = QVBoxLayout(pg)
        v.setContentsMargins(0, 16, 8, 12)
        v.setSpacing(9)
        return pg, v

    def _sec(self, text):
        lab = QLabel(text)
        lab.setObjectName("setSection")
        ufont(lab, 9.5, QFont.Weight.DemiBold, 1.8)
        return lab

    def _lab(self, text, hint=False):
        lab = QLabel(text)
        lab.setObjectName("setHint" if hint else "setLabel")
        lab.setWordWrap(True)
        ufont(lab, 9.5 if hint else 10.5)
        return lab

    def _field(self, placeholder=""):
        f = QLineEdit()
        f.setObjectName("field")
        f.setPlaceholderText(placeholder)
        ufont(f, 11)
        return f

    def _pick(self, on_activated):
        b = PickBox()
        ufont(b, 11)
        b.activated.connect(on_activated)
        return b

    def _slider(self, title, lo, hi, fmt, apply):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        top = QHBoxLayout()
        val = QLabel("")
        val.setObjectName("setHint")
        ufont(val, 9.5)
        top.addWidget(self._lab(title), 1)
        top.addWidget(val)
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setFixedHeight(22)
        s.setCursor(Qt.CursorShape.PointingHandCursor)

        def changed(x):
            val.setText(fmt(x))
            if not self._busy and not s.isSliderDown():
                apply(x)

        s.valueChanged.connect(changed)
        s.sliderReleased.connect(lambda: apply(s.value()))
        v.addLayout(top)
        v.addWidget(s)
        s._val, s._fmt = val, fmt
        return box, s

    def _toggle_row(self, title, hint, apply):
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(self._lab(title))
        if hint:
            col.addWidget(self._lab(hint, hint=True))
        t = Toggle(self.pres)
        t.toggled.connect(lambda on: None if self._busy else apply(on))
        row.addLayout(col, 1)
        row.addWidget(t, 0, Qt.AlignmentFlag.AlignVCenter)
        return row, t

    # ---------- pages
    def _persona_page(self):
        pg, v = self._page()
        v.addWidget(self._sec("Active personality"))
        self.persona_pick = self._pick(self._persona_picked)
        v.addWidget(self.persona_pick)
        v.addSpacing(4)
        v.addWidget(self._sec("Name"))
        self.persona_name = self._field("Personality name")
        v.addWidget(self.persona_name)
        v.addWidget(self._sec("System prompt"))
        self.persona_text = QPlainTextEdit()
        self.persona_text.setObjectName("prompt")
        self.persona_text.setPlaceholderText("Write or paste the instructions that shape how Ai-ru behaves")
        self.persona_text.setMinimumHeight(200)
        ufont(self.persona_text, 10.5)
        self.persona_text.textChanged.connect(self._persona_changed)
        v.addWidget(self.persona_text, 1)
        self.persona_info = self._lab("", hint=True)
        v.addWidget(self.persona_info)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_save = ActionButton(self.pres, "Save", primary=True)
        self.btn_new = ActionButton(self.pres, "Save as new")
        self.btn_del = ActionButton(self.pres, "Delete", danger=True)
        for b in (self.btn_save, self.btn_new, self.btn_del):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.btn_restore = ActionButton(self.pres, "Restore default text")
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_restore)
        row2.addStretch(1)
        v.addLayout(row2)
        v.addWidget(self._lab("Switching personalities updates the active context immediately. "
                              "You can also type: switch to the Concise personality.", hint=True))
        self.btn_save.clicked.connect(self._persona_save)
        self.btn_new.clicked.connect(self._persona_save_new)
        self.btn_del.clicked.connect(self._persona_delete)
        self.btn_restore.clicked.connect(lambda: self.persona_text.setPlainText(DEFAULT_PROMPT))
        return pg

    def _look_page(self):
        pg, v = self._page()
        v.addWidget(self._sec("Accent colour"))
        self.swatches = SwatchRow(self.pres, THEME_ORDER)
        self.swatches.picked.connect(self._swatch_picked)
        v.addWidget(self.swatches)
        v.addSpacing(6)
        v.addWidget(self._sec("Font"))
        self.font_pick = self._pick(self._font_picked)
        v.addWidget(self.font_pick)
        v.addWidget(self._lab("Inter and Sora show up once installed. Drop .ttf files into assets/fonts to add any font.",
                              hint=True))
        box, self.size_s = self._slider("Text size", 20, 36, lambda x: f"{x / 2:.1f} pt",
                                        lambda x: self.win.set_font_size(x / 2))
        v.addWidget(box)
        v.addSpacing(6)
        v.addWidget(self._sec("Orb"))
        box, self.speed_s = self._slider("Animation speed", 25, 250, lambda x: f"{x}%",
                                         lambda x: self.win.set_anim_speed(x / 100))
        v.addWidget(box)
        box, self.glow_s = self._slider("Inner glow", 0, 100, lambda x: f"{x}%",
                                        lambda x: self.win.set_glow(x / 100))
        v.addWidget(box)
        v.addStretch(1)
        return pg

    def _voice_page(self):
        pg, v = self._page()
        v.addWidget(self._sec("Voice"))
        self.voice_pick = self._pick(self._voice_picked)
        v.addWidget(self.voice_pick)
        v.addWidget(self._lab("Choose a local neural voice or the Windows fallback. "
                              "You can also type: use the Heart voice.", hint=True))
        v.addSpacing(8)
        v.addWidget(self._sec("Audio feedback"))
        for attr, title, hint, key in (
                ("t_all", "Sound effects", "Master switch", "all"),
                ("t_ui", "Interface sounds", "Clicks, whooshes, message chimes", "ui"),
                ("t_typing", "Typing clicks", "A soft tick for every key", "typing"),
                ("t_state", "State cues", "Listening, thinking, speaking and mic tones", "state"),
                ("t_speak", "Speak replies aloud", "Speech is generated locally on this PC", "speak")):
            row, t = self._toggle_row(title, hint, lambda on, k=key: self.win.set_audio_option(k, on))
            setattr(self, attr, t)
            v.addLayout(row)
        v.addStretch(1)
        return pg

    def _wake_page(self):
        from PyQt6.QtWidgets import QProgressBar
        pg, v = self._page()
        v.addWidget(self._sec("Wake phrase"))
        self.wake_field = self._field(DEFAULT_WAKE)
        self.wake_field.returnPressed.connect(self._wake_save)
        v.addWidget(self.wake_field)
        row = QHBoxLayout()
        row.setSpacing(6)
        b1 = ActionButton(self.pres, "Save", primary=True)
        b2 = ActionButton(self.pres, "Reset")
        b3 = ActionButton(self.pres, "Test")
        b1.clicked.connect(self._wake_save)
        b2.clicked.connect(lambda: self.win.set_wake_word(self.win.default_wake))
        b3.clicked.connect(self.win.test_wake)
        for b in (b1, b2, b3):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        v.addSpacing(6)
        row, self.t_wake = self._toggle_row("Listen for the wake phrase", "When off, only the mic button starts listening",
                                            self.win.set_wake_enabled)
        v.addLayout(row)
        v.addSpacing(8)
        self.wake_status = self._lab("Microphone listener starting...", hint=True)
        self.wake_status.setWordWrap(True)
        v.addWidget(self.wake_status)
        self.wake_level = QProgressBar()
        self.wake_level.setRange(0, 100)
        self.wake_level.setValue(0)
        self.wake_level.setFormat("Microphone level: %p%")
        v.addWidget(self.wake_level)
        self.wake_diagnostic = self._lab("Test listens for your actual wake phrase for 20 seconds. Nothing is saved.", hint=True)
        self.wake_diagnostic.setWordWrap(True)
        v.addWidget(self.wake_diagnostic)
        v.addWidget(self._lab("Wake detection and speech recognition run locally. Say the phrase alone, wait for "
                              "Listening, then speak; or say the phrase and request together. "
                              "The mic button interrupts a reply. Wake listening pauses while Ai-ru speaks.", hint=True))
        v.addWidget(self._lab("Typing the phrase in the message box works too. Try: change the wake word to Hey Nova.",
                              hint=True))
        v.addStretch(1)
        return pg

    def _keys_page(self):
        pg, v = self._page()
        v.addWidget(self._sec("Keyboard shortcuts"))
        v.addWidget(ShortcutList(self.pres, SHORTCUTS))
        v.addSpacing(8)
        v.addWidget(self._sec("Or just ask"))
        for ex in ("change the accent to teal", "switch to the Concise personality", "set the font to Sora",
                   "make the text bigger", "animation speed 150%", "set the wake word to Hey Nova",
                   "use the Aria voice", "mute typing sounds", "search chat for invoice", "list personalities"):
            v.addWidget(self._lab("\u201c" + ex + "\u201d", hint=True))
        v.addStretch(1)
        return pg

    # ---------- handlers
    def _persona_picked(self, i):
        name = self.persona_pick.itemData(i)
        if name:
            self.win.activate_persona(name)

    def _persona_changed(self):
        cur = self.win.persona_obj()
        txt = self.persona_text.toPlainText()
        dirty = cur is not None and txt != cur["prompt"]
        self.persona_info.setText(f"{len(txt)} characters" + ("  \u00b7  unsaved changes" if dirty else ""))

    def _persona_save(self):
        if not self._busy:
            self.win.save_persona(self.persona_name.text(), self.persona_text.toPlainText(), replace=self.win.persona)

    def _persona_save_new(self):
        if not self._busy:
            self.win.save_persona(self.persona_name.text(), self.persona_text.toPlainText(), replace=None)

    def _persona_delete(self):
        self.win.delete_persona(self.win.persona)

    def _swatch_picked(self, name):
        if name == "Custom":
            c = QColorDialog.getColor(QColor(*[int(v) for v in self.win.accent]), self.win, "Accent colour")
            if c.isValid():
                self.win.set_accent((c.red(), c.green(), c.blue()))
        else:
            self.win.apply_theme(name)

    def _font_picked(self, i):
        self.win.set_font_family(self.font_pick.itemText(i))

    def _voice_picked(self, i):
        self.win.set_voice(self.voice_pick.itemText(i))

    def _wake_save(self):
        self.win.set_wake_word(self.wake_field.text())

    # ---------- sync from window state
    def refresh(self):
        w = self.win
        self._busy = True
        try:
            self.persona_pick.clear()
            for p in w.personas:
                self.persona_pick.addItem(p["name"] + ("   \u25cf" if p["name"] == w.persona else ""), p["name"])
            self.persona_pick.setCurrentIndex(max(0, self.persona_pick.findData(w.persona)))
            cur = w.persona_obj()
            if cur:
                txt = self.persona_text.toPlainText()
                if self._loaded_name != cur["name"] or txt in (self._loaded_prompt, cur["prompt"]):
                    self.persona_name.setText(cur["name"])  # a different personality, or no unsaved edits
                    self.persona_text.setPlainText(cur["prompt"])
                    self._loaded_name, self._loaded_prompt = cur["name"], cur["prompt"]
            builtin = w.persona == DEFAULT_PERSONA
            self.persona_name.setEnabled(not builtin)
            self.btn_del.setEnabled(not builtin)
            self.swatches.set_value(w.theme, w.accent if w.theme == "Custom" else None)
            self.font_pick.clear()
            for f in available_fonts(FONT_FAMILIES[0]):
                self.font_pick.addItem(f)
            self.font_pick.setCurrentText(FONT_FAMILIES[0])
            for s, val in ((self.size_s, int(round(w.font_pt * 2))), (self.speed_s, int(round(w.anim_speed * 100))),
                           (self.glow_s, int(round(w.glow * 100)))):
                s.blockSignals(True)
                s.setValue(val)
                s.blockSignals(False)
                s._val.setText(s._fmt(val))
            self.voice_pick.clear()
            for vname in w.voices:
                self.voice_pick.addItem(vname)
            self.voice_pick.setCurrentText(w.voice)
            self.t_all.sync(w.sound.enabled)
            self.t_ui.sync(w.sound.cats["ui"])
            self.t_typing.sync(w.sound.cats["typing"])
            self.t_state.sync(w.sound.cats["state"])
            self.t_speak.sync(w.speak_replies)
            if not self.wake_field.hasFocus():
                self.wake_field.setText(w.wake_word)
            self.t_wake.sync(w.wake_enabled)
        finally:
            self._busy = False
        self._persona_changed()

    def show_tab(self, i):
        i = int(clamp(i, 0, len(self.TABS) - 1))
        self.tabs.set_index(i)
        self.stack.setCurrentIndex(i)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.panel.setGeometry(0, 0, self.W, self.height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        shape = chamfer(r, 0, self.cut * 0.4, self.cut, 0)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(150, 200, 255, 15))
        g.setColorAt(1, QColor(150, 200, 255, 5))
        p.setPen(NOPEN)
        p.setBrush(g)
        p.drawPath(shape)
        line = QLinearGradient(0, 0, 0, self.height())
        line.setColorAt(0, rgba(self.pres.c1, 0))
        line.setColorAt(0.5, rgba(self.pres.c1, 80))
        line.setColorAt(1, rgba(self.pres.c1, 0))
        p.fillRect(QRectF(0, 0, 1, self.height()), line)


# --------------------------------------------------------------------------------------
# Natural-language settings control
# --------------------------------------------------------------------------------------
COLOR_NAMES = {
    "blue": (26, 170, 255), "sky": (110, 200, 255), "navy": (60, 100, 255), "cyan": (0, 229, 255),
    "aqua": (0, 229, 255), "turquoise": (0, 214, 200), "teal": (0, 214, 190), "green": (60, 255, 150),
    "mint": (48, 242, 172), "lime": (170, 255, 70), "yellow": (255, 214, 70), "gold": (255, 190, 60),
    "amber": (255, 176, 40), "orange": (255, 150, 50), "peach": (255, 170, 130), "red": (255, 70, 90),
    "coral": (255, 112, 98), "rose": (255, 110, 150), "pink": (255, 72, 168), "magenta": (255, 60, 220),
    "purple": (160, 100, 255), "violet": (150, 104, 255), "indigo": (110, 100, 255),
    "lavender": (196, 170, 255), "white": (236, 238, 244), "silver": (200, 206, 220),
    "grey": (170, 180, 195), "gray": (170, 180, 195),
}
_PREFIX = re.compile(r"^(?:(?:hey,?\s*)?(?:ai-?ru|airu)[,:]?\s+)?(?:(?:please|kindly|can you|could you|would you|"
                     r"will you|i want you to|i'd like you to|i would like to)\s+)*", re.I)


def _unquote(s):
    return str(s).strip().strip("\"'\u201c\u201d\u2018\u2019").strip()


def _brighten(rgb):
    h, s, v = colorsys.rgb_to_hsv(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
    if s > 0.12:
        s, v = max(s, 0.45), max(v, 0.92)
    else:
        v = max(v, 0.85)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def color_from_text(t):
    """An (r, g, b) accent from a hex code or a colour word, else None."""
    m = re.search(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", t)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return _brighten(tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)))
    for w in re.findall(r"[a-z]+", t.lower()):
        if w in COLOR_NAMES:
            return COLOR_NAMES[w]
    return None


def _onoff(t):
    if re.search(r"\b(?:unmute|enable|turn on|switch on|activate|on)\b", t):
        return True
    if re.search(r"\b(?:mute|disable|turn off|switch off|deactivate|silence|stop|off)\b", t):
        return False
    return None


def fuzzy(name, options):
    """Best option for a spoken/typed name: exact, prefix, substring, then close match."""
    n = str(name).strip().lower()
    if not n:
        return None
    low = [o.lower() for o in options]
    for test in (lambda o: o == n, lambda o: o.startswith(n), lambda o: n in o, lambda o: o in n):
        for i, o in enumerate(low):
            if test(o):
                return options[i]
    hit = difflib.get_close_matches(n, low, n=1, cutoff=0.6)
    return options[low.index(hit[0])] if hit else None


def available_fonts(current=None):
    fams = set(QFontDatabase.families())
    wanted = ["Bahnschrift", "Inter", "Sora", "Segoe UI Variable Display", "Segoe UI", "Roboto", "Open Sans",
              "Noto Sans", "Helvetica Neue", "Consolas", "Georgia", "Cambria"]
    out = [f for f in wanted if f in fams] + [f for f in CUSTOM_FAMILIES if f in fams and f not in wanted]
    if current and current not in out:
        out.insert(0, current)
    return out or [current or "Sans Serif"]


def parse_command(text):
    """Turn a typed or spoken sentence into (intent, argument), or None if it is ordinary conversation.
    Pure function: no side effects, safe to call from any thread."""
    from windows_controls import parse_windows_command
    from task_planner import parse_task_command
    from screen_awareness import parse_screen_command
    if parse_screen_command(text):
        return None
    if parse_task_command(text):
        return None
    from desktop_controls import parse_desktop_command
    if parse_windows_command(text):
        return None
    desktop_intent = parse_desktop_command(text)
    if desktop_intent and desktop_intent[0] != "focus":
        return None
    s = _PREFIX.sub("", " ".join(str(text).split())).strip()
    t = s.lower().rstrip(".!? ")
    if not t or len(t) > 600:
        return None

    # ---- chat search (needs an explicit "chat/history/messages" so normal web searches pass through)
    m = (re.match(r"(?:search|find|look)\s+(?:through\s+|in\s+)?(?:my\s+|the\s+|our\s+)?(?:chat|chats|history|messages|"
                  r"conversation|conversations)\s*(?:history\s*)?(?:for|about)?\s*[:\-]?\s*(.+)$", t)
         or re.match(r"(?:search|find|look)\s+(?:for\s+)?(.+?)\s+(?:in|through)\s+(?:my\s+|the\s+|our\s+)?(?:chat|chats|"
                     r"history|messages|conversation|conversations)(?:\s+history)?$", t))
    if m:
        return ("search", _unquote(m.group(1)))
    if re.fullmatch(r"(?:open|show|start)?\s*(?:chat\s+)?search|search\s+(?:my\s+|the\s+)?(?:chat|history|messages)", t):
        return ("search", "")

    # ---- settings panel
    if re.search(r"\b(?:shortcuts?|hotkeys|key ?bindings?)\b", t) and re.match(r"(?:show|open|list|display|view|what are)", t):
        return ("settings", 4)
    m = re.match(r"(?:open|show|go to|display|take me to)\s+(?:the\s+|my\s+)?(?:(\w+)\s+)?(?:settings|preferences|options)$", t)
    if m:
        tab = {"persona": 0, "personality": 0, "personalities": 0, "look": 1, "appearance": 1, "theme": 1, "voice": 2,
               "sound": 2, "audio": 2, "wake": 3, "shortcut": 4, "shortcuts": 4, "keys": 4}.get(m.group(1) or "", None)
        return ("settings", tab)
    if re.fullmatch(r"(?:close|hide|dismiss)\s+(?:the\s+)?(?:settings|preferences)", t):
        return ("settings_close", None)
    if re.search(r"\bwhat (?:settings|can i (?:change|customi[sz]e)|can you change)\b|\bsettings help\b", t):
        return ("help", None)

    # ---- personalities
    if re.search(r"\b(?:list|show|what|which)\b.*\b(?:personalit(?:y|ies)|personas?)\b", t) \
            and not re.search(r"\b(?:switch|change|use|set|create|delete|remove)\b", t):
        return ("persona_list" if re.search(r"personalities|personas\b|\blist\b|available|\ball\b|do i have", t)
                else "persona_current", None)
    m = re.match(r"(?:create|add|make|save|new)\s+(?:a\s+)?(?:new\s+)?(?:personality|persona)\s*(?:called|named)?\s*[\"\u201c']?"
                 r"([^:\"\u201d']{1,40}?)[\"\u201d']?\s*(?:with\s+(?:the\s+)?(?:system\s+)?prompt|prompt|:|-|that)\s*[:\-]?\s*(.+)$",
                 s, re.I | re.S)
    if m:
        return ("persona_create", (_unquote(m.group(1)), m.group(2).strip()))
    m = re.match(r"(?:create|add|make|new)\s+(?:a\s+)?(?:new\s+)?(?:personality|persona)\s*(?:called|named)?\s*[\"\u201c']?(.{1,40}?)[\"\u201d']?$",
                 s, re.I)
    if m:
        return ("persona_create", (_unquote(m.group(1)), ""))
    m = re.match(r"(?:set|change|update|replace|edit)\s+(?:the\s+|my\s+)?(?:system\s+prompt|prompt)\s*(?:to|as|:|=)\s*(.+)$",
                 s, re.I | re.S)
    if m:
        return ("prompt_set", m.group(1).strip())
    m = (re.match(r"(?:delete|remove)\s+(?:the\s+)?(?:personality|persona)\s+(.+)$", t)
         or re.match(r"(?:delete|remove)\s+(?:the\s+)?(.+?)\s+(?:personality|persona)$", t))
    if m:
        return ("persona_delete", _unquote(m.group(1)))
    if re.fullmatch(r"(?:reset|restore)\s+(?:the\s+)?(?:personality|persona|system prompt|prompt)(?:\s+to\s+(?:the\s+)?default)?", t):
        return ("persona_reset", None)
    m = (re.match(r"(?:switch|change|set|use|activate|load|swap|go|move|be)\s*(?:over\s+)?(?:to|into)?\s*(?:the\s+)?"
                  r"(?:personality|persona)\s*(?:to|as|:|called|named)?\s*(.+)$", t)
         or re.match(r"(?:switch|change|use|activate|load|swap|go|move|be)\s*(?:over\s+)?(?:to|into)?\s*(?:the\s+)?(.+?)\s+"
                     r"(?:personality|persona)$", t))
    if m:
        return ("persona_switch", _unquote(m.group(1)))

    # ---- wake word
    m = (re.match(r"(?:change|set|update|make)\s+(?:the\s+|my\s+)?(?:wake[\s-]?(?:word|phrase)|trigger(?:\s+(?:word|phrase))?|"
                  r"keyword|activation\s+(?:word|phrase))\s*(?:to|as|:|=)\s*(.+)$", s.rstrip(".!? "), re.I)
         or re.match(r"(?:my\s+)?(?:wake[\s-]?(?:word|phrase)|trigger(?:\s+(?:word|phrase))?)\s+(?:is|should be)\s+(.+)$",
                     s.rstrip(".!? "), re.I))
    if m:
        return ("wake_set", _unquote(m.group(1)))
    if re.search(r"\bwake[\s-]?(?:word|phrase)\b", t):
        if re.search(r"\b(?:disable|turn off|stop|deactivate|switch off)\b", t):
            return ("wake_enable", False)
        if re.search(r"\b(?:enable|turn on|activate|switch on)\b", t):
            return ("wake_enable", True)
        if re.search(r"\b(?:what|which|tell)\b", t):
            return ("wake_show", None)

    # ---- sliders
    m = re.search(r"\b(?:animation|motion|orb)\s+speed\b.*?(\d+(?:\.\d+)?)\s*(%|x)?", t)
    if m:
        v, unit = float(m.group(1)), m.group(2)
        return ("speed", v / 100 if unit == "%" or (unit is None and v > 5) else v)
    if re.search(r"\b(?:speed up|faster|quicken|quicker)\b", t) and re.search(r"\b(?:animation|orb|motion)\b", t):
        return ("speed_delta", 0.25)
    if re.search(r"\b(?:slow down|slower)\b", t) and re.search(r"\b(?:animation|orb|motion)\b", t):
        return ("speed_delta", -0.25)
    m = re.search(r"\bglow\b.*?(\d+(?:\.\d+)?)\s*%?", t) or re.search(r"(\d+(?:\.\d+)?)\s*%?\s*(?:inner\s+)?glow\b", t)
    if m:
        return ("glow", float(m.group(1)) / 100)
    if re.search(r"\bglow\b", t):
        if re.search(r"\b(?:increase|raise|boost|brighter|brighten|more|stronger|turn up)\b", t):
            return ("glow_delta", 0.15)
        if re.search(r"\b(?:decrease|reduce|dim|lower|less|softer|weaker|turn down)\b", t):
            return ("glow_delta", -0.15)

    # ---- audio feedback
    if re.search(r"\b(?:typing|keyboard|key|click)\s+(?:sounds?|clicks?)\b", t) and _onoff(t) is not None:
        return ("sound_cat", ("typing", _onoff(t)))
    if re.search(r"\b(?:chimes?|state sounds?|status sounds?|state cues?|mic tones?)\b", t) and _onoff(t) is not None:
        return ("sound_cat", ("state", _onoff(t)))
    if re.search(r"\b(?:voice|spoken|speak(?:ing)?)\s+(?:replies|responses|answers)\b|\b(?:read|speak)\s+(?:replies|answers|"
                 r"responses)\s+aloud\b", t) and _onoff(t) is not None:
        return ("sound_cat", ("speak", _onoff(t)))
    if re.search(r"\b(?:sounds?|audio|sound effects|sfx)\b", t):
        m = re.search(r"\b(?:turn|switch)\s+(on|off)\b|\b(?:sounds?|audio|sound effects|sfx)\s+(on|off)\b|\b(mute|unmute)\b", t)
        if m:
            word = next(g for g in m.groups() if g)
            return ("sound_all", word in ("on", "unmute"))

    # ---- fonts
    m = (re.match(r"(?:change|set|switch|use|make)\s+(?:the\s+|my\s+)?(?:font|typeface)(?:\s+family)?\s*(?:to|as|:|=)\s*(.+)$", t)
         or re.match(r"(?:switch|change|use|set)\s+(?:to\s+)?(?:the\s+)?(.+?)\s+(?:font|typeface)$", t))
    if m:
        return ("font", _unquote(m.group(1)))
    m = re.search(r"\b(?:font|text|type|writing)\s+size\b.*?(\d+(?:\.\d+)?)", t)
    if m:
        return ("font_size", float(m.group(1)))
    if re.search(r"\b(?:font|text|writing)\b", t):
        if re.search(r"\b(?:bigger|larger|increase|enlarge|raise)\b", t):
            return ("font_delta", 1.0)
        if re.search(r"\b(?:smaller|decrease|reduce|shrink|lower)\b", t):
            return ("font_delta", -1.0)

    # ---- voice
    if re.search(r"\b(?:list|show|what|which)\b.*\bvoices\b", t):
        return ("voice_list", None)
    m = (re.match(r"(?:change|set|switch|use|pick|choose|select)\s+(?:the\s+|my\s+)?voice\s*(?:to|as|:|=)?\s*(.+)$", t)
         or re.match(r"(?:switch|change|use|pick|choose|select)\s+(?:to\s+)?(?:the\s+)?(.+?)\s+voice$", t))
    if m:
        return ("voice", _unquote(m.group(1)))

    # ---- accent / theme
    if re.search(r"\b(?:next|another|different|cycle)\s+(?:theme|colou?rs?|accent)\b", t) or t in ("cycle theme", "change theme"):
        return ("theme_next", None)
    m = re.search(r"\b(neon|cyan|samantha|ice|void|mono)\b", t)
    if m and re.search(r"\b(?:theme|look|style|preset)\b", t):
        return ("theme", m.group(1))
    rgb = color_from_text(s)
    if rgb and re.search(r"\b(?:accent|colou?rs?|theme|highlight|orb|ui|interface)\b", t) \
            and re.search(r"\b(?:change|make|set|turn|use|switch|paint|pick|apply|want|go)\b|\bcolou?r\b", t):
        return ("accent", rgb)
    if rgb and re.match(r"make (?:it|everything|the (?:orb|ui|interface|app))\s+\w+$", t):
        return ("accent", rgb)
    return None


HELP_TEXT = ("You can change things by typing: \u201cchange the accent to teal\u201d, \u201cswitch to the Concise personality\u201d, "
             "\u201cset the font to Sora\u201d, \u201cmake the text bigger\u201d, \u201canimation speed 150%\u201d, \u201cglow 80\u201d, "
             "\u201cuse the Aria voice\u201d, \u201cset the wake word to Hey Nova\u201d, \u201cmute typing sounds\u201d, "
             "\u201csearch chat for invoice\u201d or \u201copen settings\u201d.")


class SettingsCommands:
    """Carries out a parsed intent against the window and returns a short confirmation."""

    def __init__(self, win):
        self.w = win

    def run(self, kind, arg):
        fn = getattr(self, "do_" + kind, None)
        return fn(arg) if fn else None

    # --- navigation
    def do_search(self, q):
        self.w.open_search(q)

    def do_settings(self, tab):
        self.w.open_settings(tab)
        return "Settings are open."

    def do_settings_close(self, _):
        self.w.close_settings()
        return "Settings closed."

    def do_help(self, _):
        return HELP_TEXT

    # --- personalities
    def do_persona_list(self, _):
        names = [p["name"] + (" (active)" if p["name"] == self.w.persona else "") for p in self.w.personas]
        return "Personalities: " + ", ".join(names) + "."

    def do_persona_current(self, _):
        p = self.w.persona_obj()
        return f"Active personality: {p['name']}. " + (p["prompt"][:140] + ("..." if len(p["prompt"]) > 140 else ""))

    def do_persona_switch(self, name):
        hit = fuzzy(name, [p["name"] for p in self.w.personas])
        if not hit:
            return f"I don't have a personality called \u201c{name}\u201d. " + self.do_persona_list(None)
        self.w.activate_persona(hit)
        return f"Switched to {hit}."

    def do_persona_create(self, arg):
        name, prompt = arg
        if prompt and not re.match(r"you\b", prompt, re.I):
            prompt = f"You are Ai-ru. Your personality: {prompt}"
        made = self.w.save_persona(name, prompt or DEFAULT_PROMPT, replace=None)
        if not prompt:
            self.w.open_settings(0)
            return f"Created \u201c{made}\u201d with the default prompt. Edit it in Settings."
        return f"Created and switched to \u201c{made}\u201d."

    def do_prompt_set(self, text):
        self.w.set_system_prompt(text)
        return f"Updated the system prompt for {self.w.persona}."

    def do_persona_delete(self, name):
        hit = fuzzy(name, [p["name"] for p in self.w.personas])
        if not hit:
            return f"I don't have a personality called \u201c{name}\u201d."
        if hit == DEFAULT_PERSONA:
            return "The default personality can't be deleted. Say \u201creset personality\u201d to restore its prompt."
        self.w.delete_persona(hit)
        return f"Deleted {hit}."

    def do_persona_reset(self, _):
        self.w.reset_default_persona()
        return "Restored the default personality and prompt."

    # --- look
    def do_theme_next(self, _):
        self.w.cycle_theme()
        return f"Theme: {self.w.theme}."

    def do_theme(self, name):
        hit = fuzzy(name, THEME_ORDER)
        if not hit:
            return "Themes: " + ", ".join(THEME_ORDER) + "."
        self.w.apply_theme(hit)
        return f"Theme: {hit}."

    def do_accent(self, rgb):
        self.w.set_accent(rgb)
        return "Accent colour updated."

    def do_font(self, name):
        ok = self.w.set_font_family(name)
        if ok:
            return f"Font is now {FONT_FAMILIES[0]}."
        return (f"I can't find a font called \u201c{name}\u201d. Installed options: " + ", ".join(available_fonts()[:8]) +
                ". To add one, put its .ttf in assets/fonts and restart.")

    def do_font_size(self, pt):
        self.w.set_font_size(pt)
        return f"Text size {self.w.font_pt:.1f} pt."

    def do_font_delta(self, d):
        self.w.set_font_size(self.w.font_pt + d)
        return f"Text size {self.w.font_pt:.1f} pt."

    def do_speed(self, x):
        self.w.set_anim_speed(x)
        return f"Animation speed {int(round(self.w.anim_speed * 100))}%."

    def do_speed_delta(self, d):
        self.w.set_anim_speed(self.w.anim_speed + d)
        return f"Animation speed {int(round(self.w.anim_speed * 100))}%."

    def do_glow(self, x):
        self.w.set_glow(x)
        return f"Inner glow {int(round(self.w.glow * 100))}%."

    def do_glow_delta(self, d):
        self.w.set_glow(self.w.glow + d)
        return f"Inner glow {int(round(self.w.glow * 100))}%."

    # --- voice and sound
    def do_voice_list(self, _):
        return "Voices: " + ", ".join(self.w.voices) + "."

    def do_voice(self, name):
        hit = fuzzy(name, self.w.voices)
        if not hit:
            return f"I don't have a voice called \u201c{name}\u201d. " + self.do_voice_list(None)
        self.w.set_voice(hit)
        return f"Voice: {hit}."

    def do_sound_all(self, on):
        self.w.set_audio_option("all", on)
        return "Sound effects on." if on else "Sound effects off."

    def do_sound_cat(self, arg):
        cat, on = arg
        self.w.set_audio_option(cat, on)
        label = {"typing": "Typing clicks", "state": "State cues", "speak": "Spoken replies"}[cat]
        return f"{label} {'on' if on else 'off'}."

    # --- wake word
    def do_wake_set(self, phrase):
        if not self.w.set_wake_word(phrase):
            return "That wake phrase is too short. Use at least three letters."
        return f"Wake phrase is now \u201c{self.w.wake_word}\u201d."

    def do_wake_enable(self, on):
        self.w.set_wake_enabled(on)
        return "Wake phrase on." if on else "Wake phrase off."

    def do_wake_show(self, _):
        return f"Wake phrase: \u201c{self.w.wake_word}\u201d ({'on' if self.w.wake_enabled else 'off'})."


# --------------------------------------------------------------------------------------
# The main window
# --------------------------------------------------------------------------------------
def _num(d, key, default, lo, hi):
    try:
        return clamp(float(d.get(key, default)), lo, hi)
    except Exception:
        return default


class AiruGUI(QWidget):
    _status_sig = pyqtSignal(str)
    _message_sig = pyqtSignal(str, str)
    _thought_sig = pyqtSignal(str)
    _notify_sig = pyqtSignal(str)
    _wake_sig = pyqtSignal()
    _intent_sig = pyqtSignal(str, object)
    _voices_sig = pyqtSignal(list, str)

    def __init__(self, companion_name="AI-ru"):
        super().__init__()
        global SOUND
        QApplication.setQuitOnLastWindowClosed(False)
        if "--background" in sys.argv and sys.argv and sys.argv[0]:
            try:  # login items start in an unrelated folder; relative paths in the backend need the script's own
                os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))
            except Exception:
                pass
        load_custom_fonts()
        self.M, self.cut = 22, 30
        settings = load_json(SETTINGS_FILE, {})
        if not isinstance(settings, dict):
            settings = {}

        # ---- typography first: every widget reads these while it is being built
        self.font_pt = _num(settings, "font_pt", BODY_PT, 9.0, 20.0)
        fam = settings.get("font_family")
        set_font_globals(fam if isinstance(fam, str) and fam else None, self.font_pt)
        app_font = QFont()
        app_font.setFamilies(FONT_FAMILIES)
        app_font.setPointSizeF(10 * FONT_SCALE)
        QApplication.instance().setFont(app_font)

        # ---- look
        theme, acc = settings.get("theme", "Neon"), settings.get("accent")
        if theme == "Custom" and isinstance(acc, list) and len(acc) == 3:
            try:
                self.accent = tuple(int(clamp(v, 0, 255)) for v in acc)
                self.accent2, self.theme = mix(self.accent, (0, 0, 40), 0.58), "Custom"
            except Exception:
                self.theme, (self.accent, self.accent2) = "Neon", THEMES["Neon"]
        else:
            self.theme = theme if theme in THEMES else "Neon"
            self.accent, self.accent2 = THEMES[self.theme]
        STATE_DEFS["IDLE"].update(c1=self.accent, c2=self.accent2)
        self.anim_speed = _num(settings, "anim_speed", 1.0, 0.25, 2.5)
        self.glow = _num(settings, "glow", 0.67, 0.0, 1.0)
        self.orb_mode = settings.get("orb_mode") if settings.get("orb_mode") in ORB_MODES else "background"
        self.sidebar_pref = bool(settings.get("sidebar", True))
        self.autostart = bool(settings.get("autostart", True))

        # ---- voice, wake word, personalities
        self.voices = ["System default"]
        self.voice = str(settings.get("voice") or self.voices[0])
        if self.voice not in self.voices:
            self.voices.append(self.voice)
        self.speak_replies = bool(settings.get("speak_replies", True))
        self.default_wake = f"Hey {companion_name}"
        self.wake_word = str(settings.get("wake_word") or self.default_wake)[:40]
        self.wake_enabled = bool(settings.get("wake_enabled", True))
        self.last_wake_remainder = ""
        self.personas = self._load_personas(settings)
        self.persona = settings.get("persona") if any(p["name"] == settings.get("persona") for p in self.personas) \
            else DEFAULT_PERSONA

        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(APP_NAME)
        self.icon = make_app_icon(self.accent)
        self.setWindowIcon(self.icon)
        QApplication.instance().setStyleSheet(build_qss(self.accent))
        scr = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(900, 600)
        self.resize(min(1240, int(scr.width() * 0.92)), min(820, int(scr.height() * 0.92)))

        self.pres = Presence()
        for i in range(3):
            self.pres.c1[i], self.pres.c2[i] = self.accent[i], self.accent2[i]
        self.sound = SoundEngine()
        self.sound.enabled = bool(settings.get("sound", True))
        for k, v in (settings.get("sound_cats") or {}).items():
            if k in self.sound.cats:
                self.sound.cats[k] = bool(v)
        SOUND = self.sound
        self._on_submit = self._on_mic = self._on_new = None
        self._on_persona = self._on_voice = self._on_wake_cfg = self._on_setting = None
        self.pinned, self.presence_mode, self._opacity_i = False, False, 0
        self.mic_on, self.backend_state, self._quitting = False, "IDLE", False
        self.stage, self.stage_target, self._chat_h = 0.0, False, 200
        self.settings_open, self._dr_w = False, 0.0
        self._sq, self._matches, self._mi = "", [], 0
        self._pokes, self._undo, self._party_token, self._bg_hinted = [], None, 0, False
        self._party_t, self._party_i = 0.0, 0
        self._idle_since, self._next_quip, self._frame_n = time.time(), random.uniform(45, 90), 0

        self.sessions = load_sessions()[:200]
        self._sid = max([s["id"] for s in self.sessions], default=0)
        self.current = None
        self.commands = SettingsCommands(self)

        root = QHBoxLayout(self)
        self.root = root
        root.setContentsMargins(self.M, self.M, self.M, self.M)
        root.setSpacing(0)
        self.sidebar = Sidebar(self.pres)
        root.addWidget(self.sidebar)
        self._sb_w = float(Sidebar.W if self.sidebar_pref else 0)
        self._sb_end = int(self._sb_w)
        if not self.sidebar_pref:
            self.sidebar.setFixedWidth(0)
            self.sidebar.hide()

        right = QVBoxLayout()
        right.setContentsMargins(34, 8, 26, 14)
        right.setSpacing(0)
        root.addLayout(right, 1)
        self.drawer = SettingsDrawer(self)
        self.drawer.hide()
        root.addWidget(self.drawer)

        # ---- header: [menu] title . personality ........ [search] [settings] | [min] [full] [close]
        bar = QHBoxLayout()
        bar.setSpacing(2)
        bar.setContentsMargins(0, 6, 0, 6)
        self.btn_menu = IconButton(self.pres, "menu")
        self.btn_menu.setToolTip("Slide the sidebar (Ctrl+B)")
        self.session_title = QLabel("New session")
        self.session_title.setObjectName("sessionTitle")
        ufont(self.session_title, 11.5, QFont.Weight.Normal, 0.6)
        self.persona_chip = ClickLabel("")
        self.persona_chip.setObjectName("personaChip")
        ufont(self.persona_chip, 10, QFont.Weight.DemiBold, 0.8)
        self.persona_chip.setToolTip("Change personality")
        self.persona_chip.clicked.connect(lambda: self.open_settings(0))
        self.btn_search = IconButton(self.pres, "search")
        self.btn_search.setToolTip("Search messages (Ctrl+F)")
        self.btn_settings = IconButton(self.pres, "gear")
        self.btn_settings.setToolTip("Settings (Ctrl+,)")
        self.btn_full = IconButton(self.pres, "full")
        self.btn_full.setToolTip("Fullscreen (F11)")
        self.min_btn = IconButton(self.pres, "min")
        self.min_btn.setToolTip("Minimize to the Ai-ru orb")
        self.close_btn = IconButton(self.pres, "close")
        self.close_btn.setToolTip("Send to background (Ctrl+Q quits)")
        self.icon_btns = (self.btn_menu, self.btn_search, self.btn_settings, self.btn_full, self.min_btn, self.close_btn)
        bar.addWidget(self.btn_menu)
        bar.addSpacing(8)
        bar.addWidget(self.session_title)
        bar.addSpacing(10)
        bar.addWidget(self.persona_chip)
        bar.addStretch(1)
        for w in (self.btn_search, self.btn_settings, Divider(self.pres), self.min_btn, self.btn_full, self.close_btn):
            bar.addWidget(w)
        barw = QWidget()
        barw.setFixedHeight(48)
        barw.setLayout(bar)
        right.addWidget(barw)

        self.orb_view = OrbView(self.pres)
        self.wave = WaveLine(self.pres)
        self.strip = StateStrip(self.pres)
        self.live_caption = QLabel("")
        self.live_caption.setObjectName("liveCaption")
        ufont(self.live_caption, 15, QFont.Weight.Light, 0.3)
        self.live_caption.setWordWrap(True)
        self.live_caption.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.live_caption.setContentsMargins(70, 0, 70, 8)
        self.live_caption.setMinimumHeight(64)
        self.live_caption.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.live_caption.hide()
        self.searchbar = ChatSearchBar(self.pres)
        self.chat = ChatView(self.pres)
        self.chat.setMinimumHeight(0)
        self.dock = InputDock(self.pres)
        right.addWidget(self.orb_view, 6)
        right.addWidget(self.wave)
        right.addWidget(self.strip)
        right.addWidget(self.live_caption)
        right.addWidget(self.searchbar)
        right.addWidget(self.chat, 5)
        right.addSpacing(6)
        right.addWidget(self.dock)

        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(18, 18)
        self.toast = Toast(self, self.pres)
        self.palette = CommandPalette(self, self.pres)

        self.orb = MiniOrb(self.pres)
        self.orb.saved_right, self.orb.saved_y = settings.get("orb_right"), settings.get("orb_y")
        self.orb.on_restore, self.orb.on_quit, self.orb.on_mute = self._restore, self._quit, self._toggle_sound
        self.orb.on_mic, self.orb.on_submit, self.orb.on_mode = self._toggle_mic, self._submit_text, self._cycle_orb_mode
        self._build_tray()

        self._stage_anim = QVariantAnimation(self)
        self._stage_anim.setDuration(560)
        self._stage_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._stage_anim.valueChanged.connect(self._stage_tick)
        self._sb_anim = QVariantAnimation(self)
        self._sb_anim.setDuration(280)
        self._sb_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._sb_anim.valueChanged.connect(self._sb_tick)
        self._sb_anim.finished.connect(self._sb_done)
        self._dr_anim = QVariantAnimation(self)
        self._dr_anim.setDuration(300)
        self._dr_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._dr_anim.valueChanged.connect(self._dr_tick)
        self._dr_anim.finished.connect(self._dr_done)
        self._orb_timer = QTimer(self)
        self._orb_timer.setSingleShot(True)
        self._orb_timer.timeout.connect(self._sync_orb)
        QApplication.instance().applicationStateChanged.connect(lambda _s: self._orb_timer.start(250))

        # wiring
        self.sidebar.new_requested.connect(self._new_session)
        self.sidebar.session_selected.connect(self._switch_session)
        self.sidebar.session_delete.connect(self._delete_session_id)
        self.orb_view.interacted.connect(self._orb_touched)
        self.searchbar.changed.connect(self._search_changed)
        self.searchbar.step.connect(self._step_search)
        self.searchbar.closed.connect(self.close_search)
        self.dock.entry.returnPressed.connect(self._submit)
        self.dock.entry.textEdited.connect(self._typing)
        self.dock.send.clicked.connect(self._submit)
        self.dock.mic.clicked.connect(self._mic_clicked)
        self.btn_menu.clicked.connect(self._toggle_sidebar)
        self.btn_search.clicked.connect(lambda: self.open_search())
        self.btn_settings.clicked.connect(self.toggle_settings)
        self.btn_full.clicked.connect(self._toggle_fullscreen)
        self.min_btn.clicked.connect(self.showMinimized)
        self.close_btn.clicked.connect(self._to_background)
        self._status_sig.connect(self._apply_status, Qt.ConnectionType.QueuedConnection)
        self._message_sig.connect(self._do_add_message, Qt.ConnectionType.QueuedConnection)
        self._thought_sig.connect(self._show_thought, Qt.ConnectionType.QueuedConnection)
        self._notify_sig.connect(self._do_notify, Qt.ConnectionType.QueuedConnection)
        self._wake_sig.connect(self._do_wake, Qt.ConnectionType.QueuedConnection)
        self._intent_sig.connect(self._run_intent, Qt.ConnectionType.QueuedConnection)
        self._voices_sig.connect(self._apply_voices, Qt.ConnectionType.QueuedConnection)
        for keys, fn in (("Ctrl+K", self._open_palette), ("Ctrl+,", self.toggle_settings), ("Ctrl+F", self.open_search),
                         ("F11", self._toggle_fullscreen), ("F2", self._toggle_presence),
                         ("Ctrl+B", self._toggle_sidebar), ("Ctrl+T", self.cycle_theme), ("Ctrl+E", self._export),
                         ("Ctrl+N", self._new_session), ("Ctrl+M", self._toggle_mic), ("Ctrl+Space", self._toggle_mic),
                         ("Ctrl+Shift+S", self._toggle_sound), ("Ctrl+Shift+O", self._cycle_orb_mode),
                         ("Ctrl+Shift+P", self._toggle_party), ("Ctrl+Q", self._quit), ("Esc", self._escape)):
            QShortcut(QKeySequence(keys), self).activated.connect(fn)

        self._create_session()
        self._refresh_sessions()
        self.persona_chip.setText("\u00b7  " + self.persona)
        self._push_orb_theme()
        self.orb_view.set_state("IDLE")
        self.orb_view.set_speed(self.anim_speed)
        self.orb_view.set_light(self.glow)
        self.drawer.refresh()
        if self.orb_view.view is None:
            QTimer.singleShot(1200, lambda: self._toast("Orb: using the built-in cloud. " + self.orb_view.reason, 7000))
        if self.autostart:
            set_autostart(True)

        self._last = time.perf_counter()
        self._fps = 60.0
        self._clock = QTimer(self)
        self._clock.setTimerType(Qt.TimerType.PreciseTimer)
        self._clock.timeout.connect(self._frame)
        self._clock.start(16)
        self.move(scr.center() - self.rect().center())
        self.dock.entry.setFocus()
        self._orb_timer.start(900)

    # ================= backend contract =================
    def update_status(self, state: str):
        """IDLE | LISTENING | THINKING | SPEAKING (thread-safe)."""
        self._status_sig.emit(str(state))

    def add_message(self, sender: str, text: str):
        """Adds a bubble. 'user'/'you' go right, anything else is Ai-ru on the left (thread-safe)."""
        self._message_sig.emit(str(sender), str(text))

    def set_mic_callback(self, cb):
        """cb(active: bool) is called when the mic button, the wake phrase or a command toggles the mic."""
        self._on_mic = cb

    def set_send_callback(self, cb):
        """cb(text: str) is called when the user sends a message that is not a settings command."""
        self._on_submit = cb

    def set_new_session_callback(self, cb):
        self._on_new = cb

    def set_callbacks(self, on_submit=None, on_mic=None, on_new_session=None):  # older single-call form
        self._on_submit, self._on_mic, self._on_new = on_submit, on_mic, on_new_session

    def set_personality_callback(self, cb, call_now=True):
        """cb(name: str, system_prompt: str) runs whenever the active personality or its prompt changes."""
        self._on_persona = cb
        if call_now:
            p = self.persona_obj()
            safe_call(cb, p["name"], p["prompt"])

    def set_voice_callback(self, cb):
        """cb(voice_name: str)"""
        self._on_voice = cb

    def set_wake_word_callback(self, cb):
        """cb(phrase: str, enabled: bool)"""
        self._on_wake_cfg = cb

    def set_settings_callback(self, cb):
        """cb(key: str, value) for every setting change: accent, font, personality, voice, wake_word, audio_*..."""
        self._on_setting = cb

    def set_voices(self, names, current=None):
        """Fill the voice dropdown from your speech backend (thread-safe)."""
        self._voices_sig.emit([str(n) for n in names], str(current or ""))

    def get_system_prompt(self) -> str:
        return self.persona_obj()["prompt"]

    @property
    def system_prompt(self) -> str:
        return self.get_system_prompt()

    def set_audio_level(self, level: float):
        self.pres.push_level(level)

    def add_thought(self, text: str):
        self._thought_sig.emit(str(text))

    def notify(self, text: str):
        self._notify_sig.emit(str(text))

    def wake(self):
        """Thread-safe. Turns the mic on like the button does (state becomes LISTENING)."""
        self._wake_sig.emit()

    def split_wake(self, text):
        """(heard, remainder): whether the wake phrase is in `text`, and what was said after it."""
        rx = self._wake_regex()
        m = rx.search(str(text)) if rx else None
        return (True, str(text)[m.end():].strip(" ,.:;!?")) if m else (False, str(text))

    def hear(self, text) -> bool:
        """Feed every transcript here. If it contains the wake phrase, Ai-ru switches to listening.
        The words after the phrase are kept in `last_wake_remainder`. Thread-safe."""
        if not self.wake_enabled:
            return False
        ok, rest = self.split_wake(text)
        if ok:
            self.last_wake_remainder = rest
            self._wake_sig.emit()
        return ok

    def handle_command(self, text) -> bool:
        """Runs a natural-language settings command ('change the accent to teal', 'switch to Concise'...).
        Returns True if `text` was a command, so your backend can skip it. Thread-safe."""
        intent = parse_command(text)
        if intent is None:
            return False
        if QThread.currentThread() is self.thread():
            self._run_intent(*intent)
        else:
            self._intent_sig.emit(intent[0], intent[1])
        return True

    def start(self):
        """Show the window, or go straight to the floating orb when launched at login (--background)."""
        if "--background" in sys.argv:
            self.hide()
            self._to_background(quiet=True)
        else:
            self.show()

    # ================= personalities =================
    def _load_personas(self, settings):
        saved, out = settings.get("personas"), []
        if isinstance(saved, list):
            for p in saved:
                try:
                    out.append({"name": " ".join(str(p["name"]).split())[:40], "prompt": str(p["prompt"])})
                except Exception:
                    continue
        if not any(p["name"] == DEFAULT_PERSONA for p in out):
            out.insert(0, {"name": DEFAULT_PERSONA, "prompt": DEFAULT_PROMPT})
        if saved is None:
            out += [dict(p) for p in PERSONA_SEEDS]
        return out

    def persona_obj(self, name=None):
        key = (name or self.persona).lower()
        return next((p for p in self.personas if p["name"].lower() == key), None)

    def _unique_persona(self, name):
        base, n = name, 2
        while self.persona_obj(name):
            name, n = f"{base} {n}", n + 1
        return name

    def _persona_changed(self, toast=None):
        p = self.persona_obj()
        self.persona_chip.setText("\u00b7  " + p["name"])
        self._save_settings()
        safe_call(self._on_persona, p["name"], p["prompt"])
        self._emit_setting("personality", p["name"])
        self.drawer.refresh()
        if toast:
            self._toast(toast)

    def activate_persona(self, name):
        p = self.persona_obj(name)
        if p is None:
            return False
        self.persona = p["name"]
        self._persona_changed(f"Personality: {p['name']}")
        return True

    def save_persona(self, name, prompt, replace=None):
        name = " ".join(str(name).split())[:40] or "Untitled"
        prompt = str(prompt).strip() or DEFAULT_PROMPT
        cur = self.persona_obj(replace) if replace else None
        if cur is not None:
            if cur["name"] == DEFAULT_PERSONA:
                name = DEFAULT_PERSONA
            elif name.lower() != cur["name"].lower() and self.persona_obj(name):
                name = self._unique_persona(name)
            cur["name"], cur["prompt"] = name, prompt
            self.persona = name
            self._persona_changed(f"Saved {name}")
            return name
        name = self._unique_persona(name)
        self.personas.append({"name": name, "prompt": prompt})
        self.persona = name
        self._persona_changed(f"Created {name}")
        return name

    def delete_persona(self, name):
        p = self.persona_obj(name)
        if p is None or p["name"] == DEFAULT_PERSONA:
            self._toast("The default personality can't be deleted")
            return False
        self.personas.remove(p)
        if self.persona == p["name"]:
            self.persona = DEFAULT_PERSONA
        self._persona_changed(f"Deleted {p['name']}")
        return True

    def set_system_prompt(self, text):
        p = self.persona_obj()
        p["prompt"] = str(text).strip() or DEFAULT_PROMPT
        self._persona_changed("System prompt updated")

    def reset_default_persona(self):
        self.persona_obj(DEFAULT_PERSONA)["prompt"] = DEFAULT_PROMPT
        self.persona = DEFAULT_PERSONA
        self._persona_changed("Default personality restored")

    # ================= settings: look, sound, voice, wake =================
    def _emit_setting(self, key, value):
        safe_call(self._on_setting, key, value)

    def apply_theme(self, name):
        if name in THEMES:
            self._apply_colors(THEMES[name][0], THEMES[name][1], name)

    def set_accent(self, rgb):
        c1 = tuple(int(clamp(v, 0, 255)) for v in rgb)
        self._apply_colors(c1, mix(c1, (0, 0, 40), 0.58), "Custom")

    def cycle_theme(self):
        i = THEME_ORDER.index(self.theme) if self.theme in THEME_ORDER else -1
        self.apply_theme(THEME_ORDER[(i + 1) % len(THEME_ORDER)])

    def _apply_colors(self, c1, c2, name):
        self.theme, self.accent, self.accent2 = name, c1, c2
        STATE_DEFS["IDLE"].update(c1=c1, c2=c2)
        QApplication.instance().setStyleSheet(build_qss(c1))
        self.icon = make_app_icon(c1)
        self.setWindowIcon(self.icon)
        if self.tray:
            self.tray.setIcon(self.icon)
        self._push_orb_theme()
        self.pres.kick(0.8, shock=True)
        sfx("whoosh")
        self._save_settings()
        self._emit_setting("accent", [int(v) for v in c1])
        self.drawer.refresh()
        self._toast(f"Theme: {name}")

    def set_font_family(self, name) -> bool:
        hit = fuzzy(name, available_fonts(FONT_FAMILIES[0]))
        if not hit:
            return False
        set_font_globals(hit, self.font_pt)
        self._fonts_changed()
        return True

    def set_font_size(self, pt):
        self.font_pt = clamp(float(pt), 9.0, 20.0)
        set_font_globals(None, self.font_pt)
        self._fonts_changed()

    def _fonts_changed(self):
        f = QFont()
        f.setFamilies(FONT_FAMILIES)
        f.setPointSizeF(10 * FONT_SCALE)
        QApplication.instance().setFont(f)
        refresh_fonts()
        self._load_current()
        self._save_settings()
        self._emit_setting("font", [FONT_FAMILIES[0], self.font_pt])
        self.drawer.refresh()
        self.update()

    def set_anim_speed(self, x):
        self.anim_speed = clamp(float(x), 0.25, 2.5)
        self.orb_view.set_speed(self.anim_speed)
        self._save_settings()
        self._emit_setting("animation_speed", self.anim_speed)
        self.drawer.refresh()

    def set_glow(self, g):
        self.glow = clamp(float(g), 0.0, 1.0)
        self.orb_view.set_light(self.glow)
        self._save_settings()
        self._emit_setting("inner_glow", self.glow)
        self.drawer.refresh()

    def set_audio_option(self, key, on):
        on = bool(on)
        if key == "all":
            self.sound.enabled = on
        elif key == "speak":
            self.speak_replies = on
        elif key in self.sound.cats:
            self.sound.cats[key] = on
        if on and key != "speak":
            sfx("tick")
        self._save_settings()
        self._emit_setting("audio_" + key, on)
        self.drawer.refresh()
        self.btn_settings.update()

    def _apply_voices(self, names, current):
        self.voices = [str(n) for n in names] or ["System default"]
        if current in self.voices:
            self.voice = current
        elif self.voice not in self.voices:
            self.voice = self.voices[0]
        self.drawer.refresh()

    def set_voice(self, name) -> bool:
        hit = fuzzy(name, self.voices)
        if not hit:
            return False
        self.voice = hit
        self._save_settings()
        safe_call(self._on_voice, hit)
        self._emit_setting("voice", hit)
        self.drawer.refresh()
        self._toast(f"Voice: {hit}")
        return True

    def set_wake_word(self, phrase) -> bool:
        phrase = " ".join(str(phrase).split()).strip(" .!?")
        if len(re.sub(r"[^a-z0-9]", "", phrase.lower())) < 3:
            self._toast("The wake phrase needs at least three letters")
            return False
        self.wake_word = phrase[:40]
        self._save_settings()
        safe_call(self._on_wake_cfg, self.wake_word, self.wake_enabled)
        self._emit_setting("wake_word", self.wake_word)
        self.drawer.refresh()
        self._toast(f"Wake phrase: {self.wake_word}")
        return True

    def set_wake_enabled(self, on):
        self.wake_enabled = bool(on)
        self._save_settings()
        safe_call(self._on_wake_cfg, self.wake_word, self.wake_enabled)
        self._emit_setting("wake_enabled", self.wake_enabled)
        self.drawer.refresh()

    def test_wake(self):
        if not self.wake_enabled:
            self._toast("The wake phrase is switched off")
        else:
            self._emit_setting("wake_test", True)

    def set_wake_diagnostic(self, text):
        self.drawer.wake_diagnostic.setText(text)

    def _wake_regex(self):
        from voice_activity import wake_pattern
        return wake_pattern(self.wake_word)

    def _do_wake(self):
        if self.mic_on:
            return
        self.pres.kick(1.0, shock=True)
        self.dock.mic.setChecked(True)
        self._mic_clicked()

    # ================= tray =================
    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        add_action(menu, "Show Ai-ru", self._restore)
        add_action(menu, "Toggle mic", self._toggle_mic)
        add_action(menu, "Settings", lambda: (self._restore(), self.open_settings()))
        add_action(menu, "Toggle sound", self._toggle_sound)
        add_action(menu, "Cycle orb mode", self._cycle_orb_mode)
        menu.addSeparator()
        add_action(menu, "Quit", self._quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self._restore() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    # ================= orb visibility =================
    def _want_orb(self):
        hidden = (not self.isVisible()) or self.isMinimized()
        if self.orb_mode == "always":
            return True
        if self.orb_mode == "minimized":
            return hidden
        return hidden or not self.isActiveWindow()

    def _sync_orb(self, msg=None):
        if self._quitting:
            return
        if self._want_orb():
            self.orb.appear(msg)
        else:
            self.orb.vanish()

    def _cycle_orb_mode(self):
        self.orb_mode = ORB_MODES[(ORB_MODES.index(self.orb_mode) + 1) % len(ORB_MODES)]
        sfx("tick")
        self._toast(ORB_MODE_TEXT[self.orb_mode], 3200)
        self._save_settings()
        self._sync_orb()

    def _to_background(self, quiet=False):
        if self.isFullScreen():
            self._set_chrome(False)
            self.showNormal()
        self.hide()
        if not quiet:
            sfx("whoosh")
        self._sync_orb(None if quiet else "Ai-ru is running in the background")
        if self.tray and not self._bg_hinted and not quiet:
            self._bg_hinted = True
            self.tray.showMessage(APP_NAME, "Still here. Click the orb or this icon to bring me back.", self.icon, 3500)

    # ================= orb page =================
    def _push_orb_theme(self):
        self.orb_view.set_theme(self.accent, self.accent2)
        d = STATE_DEFS[self.pres.state]
        self.orb_view.set_aurora(d["c1"], d["c2"])

    def _orb_state(self, s):
        d = STATE_DEFS[s]
        self.orb_view.set_aurora(d["c1"], d["c2"])
        if not self.pres.party:
            self.orb_view.set_state(s)

    def _orb_touched(self, tap):
        if tap:
            self._poke()
        if not self.palette.isVisible():
            self.dock.entry.setFocus()

    # ================= state, mic, messages =================
    def _refresh_state(self, silent=False):
        s = "LISTENING" if self.mic_on else self.backend_state
        if s == self.pres.state:
            return
        self.pres.set_state(s)
        self._orb_state(s)
        self._idle_since = time.time()
        if not silent:
            sfx(s.lower())
        if self.orb.isVisible() and s != "IDLE":
            self.orb.show_toast(STATE_DEFS[s]["label"].capitalize() + "...")

    def _apply_status(self, state):
        s = normalize_state(state)
        if s == self.backend_state:
            return
        self.backend_state = s
        if s == "LISTENING" and not self.mic_on:
            self._set_mic_ui(True)
        elif s != "LISTENING" and self.mic_on:
            self._set_mic_ui(False)
        self._refresh_state()

    def _set_mic_ui(self, on):
        self.mic_on = on
        self.dock.mic.setChecked(on)
        self.dock.entry.setPlaceholderText("Listening... speak, or type" if on else "Message Ai-ru")
        self.orb.set_mic(on)
        self._update_stage()
        self._update_sidebar()
        self._update_caption()

    def _toggle_mic(self):
        self.dock.mic.setChecked(not self.dock.mic.isChecked())
        self._mic_clicked()

    def _mic_clicked(self):
        on = self.dock.mic.isChecked()
        sfx("crackle" if on else "mic_off")
        self.pres.kick(0.8, shock=True)
        if not on and self.backend_state == "LISTENING":
            self.backend_state = "IDLE"
        self._set_mic_ui(on)
        self._refresh_state(silent=True)
        safe_call(self._on_mic, on)

    def _show_thought(self, text):
        self.strip.set_thought(text)

    def _do_notify(self, text):
        if self.orb.isVisible():
            self.orb.show_toast(text)
        if self.tray and (not self.isVisible() or self.isMinimized()):
            self.tray.showMessage(APP_NAME, " ".join(text.split())[:200], self.icon, 4000)
        else:
            self._toast(text)

    def _typing(self, _):
        sfx("key")
        self._idle_since = time.time()
        self.pres.kick(0.3)

    def _submit(self):
        text = self.dock.entry.text()
        self.dock.entry.clear()
        self._submit_text(text)

    def _submit_text(self, text):
        text = text.strip()
        if not text:
            return
        rx = self._wake_regex() if self.wake_enabled else None
        m = rx.match(text) if rx else None
        if m:  # typing the wake phrase at the start of a message works like saying it
            rest = text[m.end():].strip(" ,.:;!?")
            self._do_wake()
            if not rest:
                return
            text = rest
        sfx("send")
        self.pres.kick(1.0, shock=True)
        self._do_add_message("user", text, quiet=True)
        intent = parse_command(text)
        if intent is not None:  # a settings command: handled here, not sent to the brain
            self._run_intent(*intent)
            return
        safe_call(self._on_submit, text)

    def _run_intent(self, kind, arg):
        try:
            reply = self.commands.run(kind, arg)
        except Exception:
            traceback.print_exc()
            reply = "That settings command did not work."
        if reply:
            self._do_add_message("settings", reply, quiet=True)
            sfx("recv")

    def show_stream_preview(self, text):
        row = getattr(self, "_stream_row", None)
        if row is None:
            row = self.chat.add_message("ai-ru", text, animate=False)
            self._stream_row = row
        row._full = text
        row.body.setTextFormat(Qt.TextFormat.PlainText)
        row.body.setText(text)
        row.body.setMaximumWidth(row.MAXW)
        row.body.setMinimumWidth(360)

    def clear_stream_preview(self):
        row = getattr(self, "_stream_row", None)
        if row is not None:
            self.chat.lay.removeWidget(row)
            if row in self.chat.rows:
                self.chat.rows.remove(row)
            row.deleteLater()
            self._stream_row = None

    def _do_add_message(self, sender, text, quiet=False):
        s = self.current
        s["msgs"].append((sender, text))
        s["ts"] = time.time()
        if s["title"] == "New session":
            if is_user(sender):
                s["title"] = " ".join(text.split())[:48] or "New session"
            else:
                s["title"], s["auto"] = "Session " + time.strftime("%H:%M"), True
        elif s.get("auto") and is_user(sender):
            s["title"], s["auto"] = " ".join(text.split())[:48] or s["title"], False
        self.chat.add_message(sender, text, animate=is_user(sender))
        self._idle_since = time.time()
        if not quiet:
            sfx("tick" if is_user(sender) else "recv")
        if not is_user(sender):
            self.pres.kick(0.5, shock=True)
            self._update_caption()
            if self.orb.isVisible():
                self.orb.show_toast(text)
            if self.tray and (not self.isVisible() or self.isMinimized()):
                self.tray.showMessage(APP_NAME, " ".join(text.split())[:200], self.icon, 4500)
        self._refresh_sessions()
        self._save()

    def _update_caption(self):
        if self.mic_on:
            self.live_caption.setText("Listening. Go ahead...")
            return
        last = next((t for s, t in reversed(self.current["msgs"]) if not is_user(s)), "") if self.current else ""
        t = " ".join(last.split())
        self.live_caption.setText(t if len(t) <= 320 else t[:317] + "...")

    # ================= search =================
    def open_search(self, query=""):
        if self.presence_mode:
            self._toggle_presence()
        self.searchbar.open_(query if isinstance(query, str) else "")
        self._search_changed(self.searchbar.field.text(), self.searchbar.all_chats)

    def close_search(self):
        self.searchbar.hide()
        self._sq, self._matches = "", []
        self._apply_highlights()
        self.dock.entry.setFocus()

    def _search_changed(self, q, all_chats):
        self._sq, self._matches, self._mi = q.strip(), [], 0
        if self._sq:
            low = self._sq.lower()
            for s in (self.sessions if all_chats else [self.current]):
                for i in range(len(s["msgs"]) - 1, -1, -1):  # newest first
                    if low in s["msgs"][i][1].lower():
                        self._matches.append((s["id"], i))
        if self._matches:
            self._goto_match(0)
        else:
            self._apply_highlights()
            self.searchbar.set_count(0, 0)

    def _step_search(self, d):
        if self._matches:
            self._goto_match((self._mi + d) % len(self._matches))

    def _goto_match(self, i):
        self._mi = i
        sid, idx = self._matches[i]
        if sid != self.current["id"]:
            target = next((s for s in self.sessions if s["id"] == sid), None)
            if target is None:
                return
            self.current = target
            self._load_current()
        self._apply_highlights()
        if idx < len(self.chat.rows):
            self.chat.scroll_to(self.chat.rows[idx])
        self.searchbar.set_count(i, len(self._matches))

    def _apply_highlights(self):
        q = self._sq if self.searchbar.isVisible() else ""
        cur = None
        if q and self._matches:
            sid, idx = self._matches[self._mi]
            cur = idx if sid == self.current["id"] else None
        for i, row in enumerate(self.chat.rows):
            hit = bool(q) and q.lower() in row._full.lower()
            row.set_highlight(q if hit else "", i == cur, self.accent)

    # ================= sessions =================
    def _save(self):
        save_json(SESSIONS_FILE, [{"id": s["id"], "title": s["title"], "ts": s["ts"], "msgs": s["msgs"]}
                                  for s in self.sessions if s["msgs"]])

    def _create_session(self):
        if self.current is not None and not self.current["msgs"]:
            return
        self._sid += 1
        self.current = {"id": self._sid, "title": "New session", "ts": time.time(), "msgs": []}
        self.sessions.insert(0, self.current)

    def _refresh_sessions(self):
        self.sidebar.set_sessions(self.sessions, self.current["id"])
        self.session_title.setText(self.current["title"])

    def _load_current(self):
        self.clear_stream_preview()
        if getattr(self, "_backend_session", None) is not self.current:
            self._backend_session = self.current
            safe_call(self._on_new)
        self.sessions = [x for x in self.sessions if x["msgs"] or x is self.current]
        self.chat.clear()
        for sender, text in self.current["msgs"]:
            self.chat.add_message(sender, text, animate=False)
        self._update_caption()
        self._refresh_sessions()
        if self.searchbar.isVisible():
            self._apply_highlights()

    def _new_session(self):
        sfx("new")
        if self.current["msgs"]:
            self.current = None
            self._create_session()
            self._load_current()
        self.dock.entry.setFocus()

    def _switch_session(self, sid):
        target = next((s for s in self.sessions if s["id"] == sid), None)
        if target is None or target is self.current:
            return
        sfx("tick")
        self.current = target
        self._load_current()

    def _delete_session_id(self, sid):
        s = next((x for x in self.sessions if x["id"] == sid), None)
        if s is None:
            return
        self._undo = (s, self.sessions.index(s))
        was_current = s is self.current
        self.sessions.remove(s)
        if was_current:
            self.current = self.sessions[0] if self.sessions else None
            if self.current is None:
                self._create_session()
            self._load_current()
        else:
            self._refresh_sessions()
        self._save()
        sfx("pop")
        self._toast("Session deleted", 6000, action="Undo", cb=self._undo_delete)

    def _delete_session(self):
        self._delete_session_id(self.current["id"])

    def _undo_delete(self):
        if not self._undo:
            self._toast("Nothing to undo")
            return
        s, idx = self._undo
        self._undo = None
        if s not in self.sessions:
            self.sessions.insert(min(idx, len(self.sessions)), s)
        self.current = s
        self._load_current()
        self._save()
        sfx("new")
        self._toast("Session restored")

    def _delete_all(self):
        self.sessions, self.current, self._undo = [], None, None
        self._create_session()
        self._load_current()
        self._save()
        sfx("pop")
        self._toast("All sessions deleted")

    def _clear_session(self):
        safe_call(self._on_new)
        self.current["msgs"].clear()
        self.current["title"] = "New session"
        self._load_current()
        self._save()
        self._toast("Session cleared")

    def _export(self):
        if not self.current["msgs"]:
            self._toast("Nothing to export yet")
            return
        default = str(HOME / f"airu-session-{time.strftime('%Y%m%d-%H%M', time.localtime(self.current['ts']))}.md")
        path, _ = QFileDialog.getSaveFileName(self, "Export session", default, "Markdown (*.md)")
        if not path:
            return
        lines = [f"# {self.current['title']}", f"*{fmt_ts(self.current['ts'])}*", ""]
        for sender, text in self.current["msgs"]:
            who = "You" if is_user(sender) else (APP_NAME if is_ai(sender) else str(sender).title())
            lines += [f"**{who}:** {text}", ""]
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self._toast("Exported to " + Path(path).name)
        except Exception as ex:
            self._toast(f"Export failed: {ex}")

    def _copy_last(self):
        last = next((t for s, t in reversed(self.current["msgs"]) if is_ai(s)), "")
        if last:
            QApplication.clipboard().setText(last)
            self._toast("Copied Ai-ru's last reply")
        else:
            self._toast("No reply to copy")

    # ================= playful stuff =================
    def _poke(self):
        now = time.time()
        self._pokes = [t for t in self._pokes if now - t < 4.0] + [now]
        self._idle_since = now
        sfx("zap")
        self.pres.kick(0.9, shock=True)
        if len(self._pokes) >= 7:
            self._pokes = []
            self.strip.set_thought("okay okay, party time")
            self._set_party(True, 14000)
        else:
            self.strip.set_thought(random.choice(POKE_QUIPS))

    def _set_party(self, on, ms=None):
        self._party_token += 1
        tok = self._party_token
        self.pres.party = on
        if on:
            sfx("whoosh")
            self._toast("Party mode. Ctrl+Shift+P to stop", 2600)
            if ms:
                QTimer.singleShot(ms, lambda: self._set_party(False) if tok == self._party_token else None)
        else:
            self.orb_view.set_state(self.pres.state)
            self._toast("Party over")

    def _toggle_party(self):
        self._set_party(not self.pres.party)

    # ================= layout modes =================
    def _toast(self, text, ms=2400, action=None, cb=None):
        self.toast.show_text(text, ms, action, cb)

    def _update_stage(self):
        tgt = self.presence_mode or self.mic_on
        if tgt == self.stage_target:
            return
        self.stage_target = tgt
        if self.stage < 0.01:
            self._chat_h = max(self.chat.height(), 140)
        self._stage_anim.stop()
        self._stage_anim.setStartValue(self.stage)
        self._stage_anim.setEndValue(1.0 if tgt else 0.0)
        self._stage_anim.start()

    def _stage_tick(self, v):
        self.stage = float(v)
        done = self.stage >= 0.999
        self.chat.setVisible(not done)
        if not done:
            self.chat.setMaximumHeight(16777215 if self.stage <= 0.001 else max(1, int(self._chat_h * (1 - self.stage))))
        self.live_caption.setVisible(self.stage > 0.55)

    def _update_sidebar(self, instant=False):
        want = self.sidebar_pref and not self.stage_target and not self.settings_open
        end = Sidebar.W if want else 0
        if self._sb_end == end and not instant:
            return
        self._sb_end = end
        self._sb_anim.stop()
        if want:
            self.sidebar.show()
        if instant or not self.isVisible():
            self._sb_w = float(end)
            self.sidebar.setFixedWidth(end)
            self.sidebar.setVisible(want)
            return
        self._sb_anim.setStartValue(self._sb_w)
        self._sb_anim.setEndValue(float(end))
        self._sb_anim.start()

    def _sb_tick(self, v):
        self._sb_w = float(v)
        self.sidebar.setFixedWidth(max(0, int(v)))

    def _sb_done(self):
        if self._sb_end == 0:
            self.sidebar.hide()

    def _toggle_sidebar(self):
        self.sidebar_pref = not self.sidebar_pref
        sfx("slide")
        self._update_sidebar()
        self._save_settings()

    # ---- settings drawer
    def open_settings(self, tab=None):
        if tab is not None:
            self.drawer.show_tab(tab)
        self.drawer.refresh()
        if not self.settings_open:
            self.settings_open = True
            sfx("slide")
            self._update_drawer()
            self._update_sidebar()

    def close_settings(self):
        if self.settings_open:
            self.settings_open = False
            sfx("slide")
            self._update_drawer()
            self._update_sidebar()
            self.dock.entry.setFocus()

    def toggle_settings(self):
        self.close_settings() if self.settings_open else self.open_settings()

    def _update_drawer(self):
        end = SettingsDrawer.W if self.settings_open else 0
        self._dr_anim.stop()
        if end:
            self.drawer.show()
        if not self.isVisible():
            self._dr_w = float(end)
            self.drawer.setFixedWidth(end)
            self.drawer.setVisible(bool(end))
            return
        self._dr_anim.setStartValue(self._dr_w)
        self._dr_anim.setEndValue(float(end))
        self._dr_anim.start()

    def _dr_tick(self, v):
        self._dr_w = float(v)
        self.drawer.setFixedWidth(max(0, int(v)))

    def _dr_done(self):
        if not self.settings_open:
            self.drawer.hide()

    def _toggle_presence(self):
        self.presence_mode = not self.presence_mode
        sfx("whoosh")
        self._update_stage()
        self._update_sidebar()
        self._update_caption()
        self._toast("Presence mode on. F2 to return." if self.presence_mode else "Presence mode off")

    # ================= features =================
    def _toggle_sound(self):
        self.set_audio_option("all", not self.sound.enabled)
        self._toast("Sound on" if self.sound.enabled else "Sound off")

    def _toggle_autostart(self):
        self.autostart = not self.autostart
        ok = set_autostart(self.autostart)
        base = "Ai-ru will start when you log in" if self.autostart else "Ai-ru won't start at login"
        self._toast(base if ok else base + " (this system could not be updated)", 3200)
        self._save_settings()

    def _save_settings(self):
        save_json(SETTINGS_FILE, {
            "theme": self.theme, "accent": [int(v) for v in self.accent], "sound": self.sound.enabled,
            "sound_cats": dict(self.sound.cats), "orb_mode": self.orb_mode, "sidebar": self.sidebar_pref,
            "orb_right": self.orb.saved_right, "orb_y": self.orb.saved_y, "autostart": self.autostart,
            "font_family": FONT_FAMILIES[0], "font_pt": self.font_pt, "anim_speed": self.anim_speed,
            "glow": self.glow, "voice": self.voice, "speak_replies": self.speak_replies,
            "wake_word": self.wake_word, "wake_enabled": self.wake_enabled,
            "personas": self.personas, "persona": self.persona})

    def _toggle_pin(self):
        self.pinned = not self.pinned
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.pinned)
        self.show()
        self._toast("Pinned on top" if self.pinned else "Unpinned")

    def _cycle_opacity(self):
        levels = (1.0, 0.88, 0.75)
        self._opacity_i = (self._opacity_i + 1) % len(levels)
        self.setWindowOpacity(levels[self._opacity_i])
        self._toast(f"Window opacity {int(levels[self._opacity_i] * 100)}%")

    def _set_chrome(self, full):
        self.M, self.cut = (0, 0) if full else (22, 30)
        self.sidebar.cut = self.drawer.cut = self.cut
        self.root.setContentsMargins(self.M, self.M, self.M, self.M)
        self.grip.setVisible(not full)
        self.update()

    def _toggle_fullscreen(self):
        sfx("whoosh")
        if self.isFullScreen():
            self._set_chrome(False)
            self.showNormal()
        else:
            self._set_chrome(True)
            self.showFullScreen()
        self.btn_full.update()

    def _escape(self):
        if self.palette.isVisible():
            self.palette.close_()
        elif self.searchbar.isVisible():
            self.close_search()
        elif self.settings_open:
            self.close_settings()
        elif self.isFullScreen():
            self._toggle_fullscreen()

    def _palette_commands(self):
        cmds = [
            ("Toggle mic (listening mode)", "Ctrl+Space", self._toggle_mic),
            ("Wake Ai-ru (test the wake phrase)", "", self.test_wake),
            ("Search messages", "Ctrl+F", self.open_search),
            ("Open settings", "Ctrl+,", self.open_settings),
            ("Settings: personalities", "", lambda: self.open_settings(0)),
            ("Settings: look", "", lambda: self.open_settings(1)),
            ("Settings: voice and sound", "", lambda: self.open_settings(2)),
            ("Settings: wake phrase", "", lambda: self.open_settings(3)),
            ("Keyboard shortcuts", "", lambda: self.open_settings(4)),
            ("New session", "Ctrl+N", self._new_session),
            ("Slide sidebar", "Ctrl+B", self._toggle_sidebar),
            ("Toggle fullscreen", "F11", self._toggle_fullscreen),
            ("Presence mode", "F2", self._toggle_presence),
            ("Cycle theme", "Ctrl+T", self.cycle_theme),
            ("Party mode", "Ctrl+Shift+P", self._toggle_party),
            ("Poke Ai-ru", "", self._poke),
            ("Toggle sound", "Ctrl+Shift+S", self._toggle_sound),
            ("Start Ai-ru when I log in (toggle)", "", self._toggle_autostart),
            ("Orb: cycle visibility mode", "Ctrl+Shift+O", self._cycle_orb_mode),
            ("Send to background orb", "", self._to_background),
            ("Minimize to Ai-ru orb", "", self.showMinimized),
            ("Pin on top", "", self._toggle_pin),
            ("Cycle window opacity", "", self._cycle_opacity),
            ("Export session as Markdown", "Ctrl+E", self._export),
            ("Copy last reply", "", self._copy_last),
            ("Clear this session", "", self._clear_session),
            ("Delete this session", "", self._delete_session),
            ("Undo delete session", "", self._undo_delete),
            ("Delete ALL sessions", "", self._delete_all),
            ("Quit Ai-ru", "Ctrl+Q", self._quit),
        ]
        for p in self.personas:
            cmds.append((f"Personality: {p['name']}", "active" if p["name"] == self.persona else "",
                         lambda n=p["name"]: self.activate_persona(n)))
        for name in THEME_ORDER:
            cmds.append((f"Theme: {name}", "", lambda n=name: self.apply_theme(n)))
        for s in self.sessions[:40]:
            if s is not self.current and s["msgs"]:
                cmds.append(("Open: " + s["title"], fmt_ts(s["ts"]), lambda sid=s["id"]: self._switch_session(sid)))
        return cmds

    def _open_palette(self):
        self.palette.open_(self._palette_commands())

    def _restore(self):
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()
        self.dock.entry.setFocus()
        self._orb_timer.start(300)

    def _quit(self):
        self._quitting = True
        self._save()
        self._save_settings()
        self.orb.hide()
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    # ================= clock =================
    def _frame(self):
        now = time.perf_counter()
        dt, self._last = now - self._last, now
        if dt > 0:
            self._fps = approach(self._fps, min(1.0 / dt, 240.0), 3.0, dt)
        P = self.pres
        P.advance(dt * self.anim_speed)
        main_vis = self.isVisible() and not self.isMinimized()
        want = 16 if main_vis else 33
        if self._clock.interval() != want:
            self._clock.setInterval(want)
        if self.orb.isVisible():
            self.orb.tick(dt)
            self.orb.update()
        if not main_vis:
            P.shock_req = 0
            return
        self._frame_n += 1
        if P.shock_req:  # every state change, send, poke and mic click rings through the orb
            P.shock_req = 0
            self.orb_view.pulse()
        self.orb_view.tick()
        if self._frame_n % 2 == 0:
            self.orb_view.set_voice(P.level if P.state in ("LISTENING", "SPEAKING") else 0.0)
        if P.party and time.time() - self._party_t > 0.8:  # party: the orb cycles through its state colours
            self._party_t, self._party_i = time.time(), (self._party_i + 1) % 4
            self.orb_view.set_state(("LISTENING", "THINKING", "SPEAKING", "IDLE")[self._party_i])
        self.wave.update()
        self.strip.update()
        self.dock.refresh()
        self.sidebar.wordmark.update()
        self.sidebar.new_btn.update()
        if self.sidebar.active_item is not None:
            self.sidebar.active_item.update()
        if self.settings_open:
            self.drawer.update()
            self.drawer.tabs.update()
            self.drawer.swatches.update()
        if self._frame_n % 4 == 0:
            for b in self.icon_btns:
                b.update()
        k = self.M + 58
        self.update(QRegion(self.rect()).subtracted(QRegion(self.rect().adjusted(k, k, -k, -k))))
        if (time.time() - self._idle_since > self._next_quip and P.state == "IDLE" and not self.mic_on):
            self.strip.set_thought(random.choice(IDLE_QUIPS))
            self._idle_since, self._next_quip = time.time(), random.uniform(45, 90)

    # ================= window chrome =================
    def paintEvent(self, _):
        P, M, C = self.pres, self.M, self.cut
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # clear first: a translucent window never wipes its own backing store
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(SRC)
        r = QRectF(self.rect()).adjusted(M, M, -M, -M)
        glow = (0.25 + 0.8 * P.energy + 0.4 * P.flash) * (0.4 + 0.9 * self.glow)
        S = C * 0.4

        def shape(rect, grow=0.0):
            k = 0.414 * grow
            return chamfer(rect, C + k if C else 0, S + k if C else 0, C + k if C else 0, S + k if C else 0)

        p.setBrush(NOBRUSH)
        for i in range(1, M + 1):  # soft outer halo (windowed mode only)
            p.setPen(QPen(rgba(P.c1, 55 * glow * (1 - i / M) ** 2.4), 1.6))
            p.drawPath(shape(r.adjusted(-i, -i, i, i), i))
        path = shape(r)
        p.setPen(NOPEN)
        p.setBrush(QColor(3, 3, 5, 255))
        p.drawPath(path)
        p.setClipPath(path)
        p.setBrush(NOBRUSH)
        for i in range(0, 22, 2):  # gentle inner spill
            p.setPen(QPen(rgba(P.c1, 46 * glow * (1 - i / 22) ** 2.4), 3))
            p.drawPath(shape(r.adjusted(i, i, -i, -i)))
        p.setClipping(False)
        if M:
            p.setPen(QPen(rgba(P.c1, 60 + 60 * P.energy), 1.0))
            p.drawPath(shape(r.adjusted(0.5, 0.5, -0.5, -0.5)))
            p.setPen(pen_of(P.c1, 190 + 60 * P.energy, 2.4))  # the two big cut edges glow brightest
            p.drawLine(QPointF(r.left(), r.top() + C), QPointF(r.left() + C, r.top()))
            p.drawLine(QPointF(r.right() - C, r.bottom()), QPointF(r.right(), r.bottom() - C))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.grip.move(self.width() - self.M - 20, self.height() - self.M - 20)
        self.grip.raise_()
        if self.palette.isVisible():
            self.palette.setGeometry(self.rect())

    def mousePressEvent(self, e):
        if (e.button() == Qt.MouseButton.LeftButton and e.position().y() < self.M + 54
                and not self.isFullScreen()):
            h = self.windowHandle()
            if h:
                h.startSystemMove()
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < self.M + 54:
            self._toggle_fullscreen()
        else:
            super().mouseDoubleClickEvent(e)

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange):
            self._orb_timer.start(150)
        super().changeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self._update_sidebar(instant=True)
        self._orb_timer.start(300)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._orb_timer.start(100)

    def closeEvent(self, e):
        if self._quitting:
            self._clock.stop()
            e.accept()
        else:  # Alt+F4 / taskbar close: go to the background, keep the orb
            e.ignore()
            self._to_background()


# backwards-compatible names for existing backends
AuraGUI = AuraWindow = AiruGUI


# --------------------------------------------------------------------------------------
# Demo harness (not used by the backend)
# --------------------------------------------------------------------------------------
def run_demo():
    if sys.platform == "win32":  # lets Windows show Ai-ru's own icon in the taskbar
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("airu.orb.hud")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win = AiruGUI()
    app.setWindowIcon(win.icon)

    def reply(text):
        win.update_status("THINKING")
        QTimer.singleShot(300, lambda: win.add_thought("routing the request through the demo stub"))
        QTimer.singleShot(1900, lambda: (win.update_status("SPEAKING"), win.add_message("ai-ru", text)))
        QTimer.singleShot(1900 + 450 + 28 * len(text), lambda: win.update_status("IDLE"))

    # What a real backend does: register hooks, then drive the GUI.
    win.set_voices(["System default", "Aria (warm)", "Sora (calm)", "Atlas (deep)"])
    win.set_personality_callback(lambda name, prompt: print(f"[personality] {name} ({len(prompt)} chars)"))
    win.set_voice_callback(lambda v: print("[voice]", v))
    win.set_wake_word_callback(lambda phrase, on: print("[wake phrase]", phrase, on))
    win.set_send_callback(lambda t: reply(f"({win.persona}) You said: {t}. The backend is not connected, "
                                          "so this is the demo harness talking."))
    win.set_mic_callback(lambda active: None if active else reply("I heard you. This is a simulated transcript."))
    win.start()
    if "--background" not in sys.argv:
        QTimer.singleShot(500, lambda: win.add_message(
            "ai-ru", f"{greeting()}. Say {win.wake_word}, poke the orb, or type \u201copen settings\u201d."))
    sys.exit(app.exec())


if __name__ == "__main__":
    run_demo()

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QRadioButton, QButtonGroup, QPushButton

class AiruSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI-ru // First-Launch Setup")
        self.setFixedSize(500, 580)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b0f19;
                border: 1px solid #1e3a8a;
            }
            QLabel {
                color: #93c5fd;
                font-family: 'Segoe UI';
            }
            QLineEdit {
                background-color: #111827;
                border: 1px solid #3b82f6;
                border-radius: 6px;
                color: #ffffff;
                padding: 10px;
                font-size: 14px;
            }
            QRadioButton {
                color: #e2e8f0;
                font-size: 13px;
                spacing: 8px;
                padding: 4px;
            }
            QPushButton {
                background-color: #2563eb;
                color: white;
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
                padding: 12px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)
        
        # Welcome message
        welcome_lbl = QLabel(
            "Hey there! Systems are fully online and I'm ready to settle in.\n"
            "I'll be your local desktop companion—handling your apps, keeping things running smooth,\n"
            "and hanging out right here on your screen.\n\n"
            "Before we dive in, I need a couple of quick things..."
        )
        welcome_lbl.setWordWrap(True)
        layout.addWidget(welcome_lbl)
        
        # Name section
        name_lbl = QLabel("1. What are you going to call me?")
        name_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(name_lbl)
        
        self.name_input = QLineEdit()
        self.name_input.setText("AI-ru")
        layout.addWidget(self.name_input)
        
        # Voice selection section
        voice_lbl = QLabel("2. Pick a voice that fits the vibe:")
        voice_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(voice_lbl)
        
        self.voice_group = QButtonGroup(self)
        
        self.rb_male = QRadioButton("Atlas — Deep & Clean (Male Profile)")
        self.rb_female = QRadioButton("Nova — Warm & Bright (Female Profile)")
        self.rb_neutral = QRadioButton("Echo — Smooth & Balanced (Neutral Profile)")
        
        self.rb_male.setProperty("val", "male_atlas")
        self.rb_female.setProperty("val", "female_nova")
        self.rb_neutral.setProperty("val", "neutral_echo")
        
        self.voice_group.addButton(self.rb_male)
        self.voice_group.addButton(self.rb_female)
        self.voice_group.addButton(self.rb_neutral)
        self.rb_neutral.setChecked(True)
        
        layout.addWidget(self.rb_male)
        layout.addWidget(self.rb_female)
        layout.addWidget(self.rb_neutral)
        
        # Submit button
        submit_btn = QPushButton("Initialize Companion")
        submit_btn.clicked.connect(self.save_and_accept)
        layout.addWidget(submit_btn)
        
        self.selected_config = {}

    def save_and_accept(self):
        name = self.name_input.text().strip() or "AI-ru"
        selected_btn = self.voice_group.checkedButton()
        voice_val = selected_btn.property("val") if selected_btn else "neutral_echo"
        
        self.selected_config = {
            "companion_name": name,
            "voice_profile": voice_val,
            "first_run": False
        }
        
        try:
            config_path = Path(__file__).resolve().with_name("config.json")
            existing = load_json(config_path, {})
            if not isinstance(existing, dict):
                existing = {}
            existing.update(self.selected_config)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")
            
        self.accept()
