# AI-ru

Windows desktop companion using PyQt6, Ollama, local Whisper recognition, and local Kokoro neural speech.

Install Python 3.11, then create a fresh virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
.\.venv\Scripts\python.exe setup_voice.py
.\.venv\Scripts\python.exe main.py
```

Copied virtual environments are not portable. For chat, run Ollama locally with
the `qwen2.5:7b` model available. `setup_voice.py` downloads the Kokoro model/voices
(about 121 MB combined) and Whisper `base` into `models/`. This setup step needs
internet; normal speech processing does not. Text chat works without voice models.
Radio changes depend on Windows permissions, device drivers, and hardware switches. Optional web orb assets
belong in `assets/`; the GUI uses its native fallback when those assets are missing.

The repository includes source, tests, and a sample configuration. Personal
`config.json`, SQLite chat memory, saved routines, generated artifacts, virtual
environments, and downloaded models are excluded from Git. These stay on the
device and are recreated or downloaded during setup. Chat preferences and sessions
also remain in the user's local `~/.airu` directory.

Run regression checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Windows controls

Type or speak these commands (polite prefixes such as "please" also work):

| Feature | Examples |
| --- | --- |
| Wi-Fi radio | `turn Wi-Fi on`, `turn Wi-Fi off` |
| Wi-Fi information | `Wi-Fi status`, `list Wi-Fi networks`, `saved Wi-Fi networks` |
| Saved Wi-Fi connection | `connect Wi-Fi to "Home"`, `disconnect Wi-Fi` |
| Bluetooth radio | `turn Bluetooth on`, `turn Bluetooth off`, `Bluetooth status` |
| Pairing | `pair Bluetooth` opens the Windows pairing settings |
| System audio | `set volume to 35`, `mute volume`, `unmute volume` |
| Display brightness | `set brightness to 60` (supported built-in displays) |
| Windows settings | `open Windows settings`, `open Bluetooth settings`, `open display settings`, `open updates settings` |
| Utilities | `open task manager`, `open file explorer`, `open control panel` |
| Lock | `lock computer` |
| Command reference | `system help` |

Natural variants such as `Turn on the Bluetooth on this device`, `Can you turn
on my Wi-Fi for me?`, and `Is my Bluetooth on?` go directly to Windows. Radio
responses now say **Verified ...: On/Off** only after reading the device state.
Unrecognized radio instructions do not go to the chat model to invent a success.

Wi-Fi connections use an existing Windows profile; enter new passwords in
`open Wi-Fi settings`. Bluetooth pairing and device selection use Windows Settings.
Wi-Fi discovery may require Windows location permission. Multiple Wi-Fi adapters
may require choosing a connection in Settings. Radio toggles target all available
radios of the requested type and report each device's observed state. A disabled
adapter, hardware switch, or administrator policy can prevent changes.

`open settings` still opens AI-ru's own preferences. Commands are matched explicitly;
questions about a control do not execute it, and model output is never run as shell
code. Supported controls run locally without Ollama.

## Response improvements

- Chat text appears as Ollama generates it; the completed reply is saved once.
- Speech playback no longer blocks processing the next request.
- The model stays loaded for 15 minutes after a request to reduce repeated cold starts.
- Chat uses up to eight recent messages and allows detailed replies when requested.
- Memory uses `remember that I prefer short replies`. Existing learned facts remain
  available; automatic background extraction was removed to avoid competing model requests.
- Speech recognition uses greedy decoding and voice activity filtering.

Set `model_name` in `config.json` to any model already installed in Ollama, then
restart AI-ru. The default remains `qwen2.5:7b`; `max_response_tokens` defaults to 384
(accepted range 64–2048). Smaller models can reduce latency at the cost of answer
quality. Streaming improves time to visible text; actual generation speed depends
on the model and hardware. The first response may still require loading the model.

Implementation references: [Windows Radio API](https://learn.microsoft.com/en-us/uwp/api/windows.devices.radios.radio),
[netsh WLAN](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/netsh-wlan),
and [Ollama chat API](https://docs.ollama.com/api/chat).

## Natural speech and wake activation

1. Start the app and wait for the wake-listening message. Models initialize in the
   background. The app must be running, including in its tray/background mode.
2. Say **"Hey Airu"**, wait for **Listening**, then speak normally. You can also say
   **"Hey Airu, what can you help me with?"** in one utterance.
3. AI-ru finishes recording after roughly 1–1.4 seconds of silence, with more time
   for pauses in longer speech. There is no fixed five-second recording window.
4. Choose **Settings → Voice** for Heart, Bella, Michael, Emma, or George. Heart is
   the new default. A Windows voice remains available as an offline fallback.
5. Choose **Settings → Wake phrase** to change the phrase, e.g. **"Hey Nova"**, or
   disable background listening. The mic button still works when wake listening is off.

If wake activation does not work, open **Settings → Wake phrase → Test**, then say
the phrase. This is now a real 20-second recognition test, not a simulated mic-button
press. The panel shows microphone levels, listener errors, and what Whisper heard.
Test utterances do not run commands or enter chat history. Their diagnostic text
stays in the panel until replaced or the app closes; it is not saved to disk.
Wake recognition explicitly uses English and avoids a second VAD pass that can
discard very short greetings. Normal mic-button transcription retains automatic
language detection.

The default wake phrase follows the companion name for new settings. An existing
saved wake phrase is preserved. Reset uses the companion's current configured name.
Wake detection accepts common Whisper spellings of Airu, including "Iru" and "eye roo".

**Privacy:** microphone audio stays in short-lived RAM buffers; it is not written
to WAV files or sent to an online service. Wake detection uses local Whisper on
speech segments, and unaddressed transcripts are discarded without reaching chat
or memory. Activated requests become normal local chat history. Kokoro generates
speech on this PC. There is no cloud speech fallback, account, or API key. Runtime
Whisper loading is local-only; missing models produce a setup notice. The model
installer contacts GitHub/Hugging Face for model files only.

**Turn-taking limits:** this uses voice activity and adaptive silence thresholds,
not semantic end-of-turn prediction. A manual turn waits up to 12 seconds for speech;
turns over 45 seconds are rejected rather than submitting an incomplete command.
Set `speech_pause_seconds` in `config.json` (0.6–1.5, default 1.0) and restart if you
need longer pauses. Quiet rooms and a close microphone improve recognition.

Wake detection pauses while AI-ru speaks and briefly afterward to avoid self-triggering.
Click the mic to interrupt playback and start speaking. This is not full-duplex
Gemini Live: voice interruptions during playback are not supported, and each new
hands-free turn needs the wake phrase. It cannot wake a sleeping or powered-off PC.
Whisper-based wake recognition supports custom phrases but uses more CPU and has
more latency than a dedicated trained wake-word model. Model startup and hardware
also affect response latency.

Voice references: [Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx) and
[WebRTC voice activity detection](https://github.com/wiseman/py-webrtcvad).

## Local desktop navigation

Wake activation now stays in the corner widget when the main window is hidden or
minimized. Listening, processing, and spoken replies run in the background. Open
the full interface yourself using the widget or tray menu when you want chat history.

Audio accepts `unmute the volume`, `unmute my audio`, `mute`, and `check volume`.
Mute and volume changes read back the default output state and name that device
before reporting **Verified**. Positive volume settings also clear mute. Unmuting
preserves the volume level; if it is zero, the reply explains that it is still silent.
App mixer mute, an output-device mismatch, a physical speaker mute, or an audio
enhancer can still silence playback independently of the Windows default endpoint.

Unmatched action commands return an explicit “I haven't performed that action”
response instead of being passed to the conversational model. Desktop clicks and
keystrokes report **Sent** rather than claiming that the app accomplished the task.
Launch requests are identified as requests unless the resulting state was checked.

AI-ru can navigate other Windows apps through their accessibility controls:

| Command | Result |
| --- | --- |
| `list windows` | List open window titles |
| `switch to Notepad` | Select and focus a window (use a unique title from the list) |
| `show controls` | List visible named controls in the selected window |
| `click Save` | Click a unique matching control |
| `wait for control Saved successfully` | Wait up to 8 seconds for an exact visible control name |
| `type "Hello there!"` | Type literal text into the focused field of the selected window |
| `press Ctrl+S` | Send a keyboard shortcut |
| `scroll down` / `scroll up` | Send Page Down / Page Up |
| `maximize window` / `minimize window` / `restore window` / `close window` | Manage the selected window |
| `open my downloads` | Open a known folder (also Documents, Desktop, Pictures, Music, Videos) |
| `open folder C:\Work` | Open an existing absolute folder path |

Select a window before clicking, typing, or pressing keys. AI-ru remembers that
target even when its own chat window takes focus. Ambiguous names return the
matching choices rather than clicking an arbitrary control. Navigation runs in a
separate helper with a 20-second timeout so an unresponsive app cannot hang chat.
Text is passed literally; keyboard commands use a separate validated shortcut
parser. No screenshots, UI text, or commands go to a cloud service.

These direct commands also serve as the tools for screen-informed task proposals.
Custom-drawn game/canvas controls may not expose accessibility names. Windows UAC,
secure desktops, and elevated windows still enforce their own access restrictions;
running an assistant locally does not bypass them. Closing a window or using a
shortcut has the same effect as doing it yourself, including save prompts.

Implementation: [pywinauto Windows UI Automation](https://pywinauto.readthedocs.io/en/latest/getting_started.html).

## Local task planning

Say **"Plan a task: turn Bluetooth on and set the volume to 30 percent."** AI-ru
uses the existing local Ollama model to propose up to eight steps. Planning itself
does not change any device setting. Review the steps in chat or listen to the reply,
then say **"Run the plan"**. It also works from the background widget.

- **"Show the plan"** shows the waiting proposal.
- **"Task status"** shows the waiting proposal or last execution report.
- **"Cancel the plan"** / **"Stop the task"** cancels without waiting behind the
  running task. A Windows call already underway can finish; no subsequent step
  starts after cancellation is observed.

The planner compiles commands into the existing device controls, validates the
entire proposal first, and rejects unsupported steps. It never executes generated
Python, PowerShell, shell scripts, or arbitrary program names. Partly unsupported
goals must return a limitation rather than silently dropping requested work.

Each task runs once and produces a step-by-step report. Failure or an unverified
result stops the sequence. Completed changes are not rolled back automatically.
Starting a new chat session discards the waiting proposal and cancels task work.
Proposals/reports are included in ordinary local chat history; executable proposals
are held only in memory and do not survive restart. Planning uses no cloud service.

Verified radio, audio, brightness and window controls can be sequenced. UI clicks,
typing and shortcuts only confirm that input was sent. A plan can follow such a
step immediately with `wait for control <exact expected result>`; a successful
visibility check permits continuation. Otherwise execution stops after the input.
Opening a settings page stops the sequence because its
window is not verified. Plans must select a target window before UI interaction.
Automatic app closing, file edits, installations, and arbitrary app launches are
excluded from this first planner. Direct supported commands continue to work.

This adds task decomposition and controlled execution. A local model can propose
incorrect steps, so review the proposal before
running it. Structured output reference: [Ollama](https://docs.ollama.com/capabilities/structured-outputs).

## Screen awareness, recovery, and routines

All these commands work through voice or text, including from the background widget:

| Command | Behavior |
| --- | --- |
| `Look at the screen` | Describe the selected window using local vision and accessibility data |
| `Look at screen: what does this error mean?` | Ask a question about that window |
| `Plan from screen: click Show details` | Inspect visible controls and propose steps grounded in that window |
| `Run the plan` | Recheck the observed window before starting the reviewed proposal |
| `Recover task` | Inspect the stopped task and propose remaining work without executing it |
| `Save plan as focus mode` | Save the pending, reviewed command list to local routine storage |
| `List routines` | Show saved names |
| `Load routine focus mode` | Validate the stored commands and preview them; follow with `Run the plan` |
| `Delete routine focus mode` | Remove that routine |

Use `list windows`, then `switch to <unique window title>` to select the app you
want AI-ru to inspect. Without a selected target, it inspects the foreground
window. When typing into AI-ru this may be AI-ru's own window; voice commands from
the corner widget avoid taking focus. A new chat session clears the selected target.

`Look at the screen` captures only the selected/foreground window, not the entire
desktop. Its image is resized to at most 1280 pixels per side, held in memory,
and sent to `http://localhost:11434/api/chat` using `vision_model` from `config.json`
(default `gemma3:4b`, already installed on this PC). No cloud API is used by this
implementation. Other installations need a local vision model, for example via
`ollama pull gemma3:4b`. Switching between text and vision models may add loading
latency on machines with limited RAM/VRAM.

Screen-based planning and recovery read a fresh accessibility snapshot rather than
an image: window title, handle, visible control names/types, and enabled states.
Snapshots omit password controls and their descendants. Screenshots are skipped
when protected fields are detected, inspection is incomplete, or the window is
hidden/minimized. Custom apps may not correctly identify sensitive fields, so an
explicit screen request can include whatever is visibly displayed in that window.
Raw screenshots and snapshots are not saved by AI-ru. Descriptions, proposals, and
execution reports are ordinary local chat history. Window content is marked as
untrusted data in model prompts and is never executed as instructions or code.

Before running a screen-based proposal, AI-ru rechecks the window handle, title,
and visible controls. A changed window stops the plan before actions run. Proposed
clicks must match a unique enabled control in the observed window; the actual
click resolves that control again. The planner cannot invent pixel coordinates.
Dynamic custom/canvas interfaces may be describable visually but still lack
accessible controls that AI-ru can click by name.

When a step fails or lacks a verified result, AI-ru attempts a fresh observation
and reports the current window. `Recover task` uses the original goal, previous
results, remaining unissued commands, and current controls to propose a repair.
Review it and say `Run the plan` again. There is no automatic retry loop or blind
replay of the whole task. Recovery can explain that the goal appears satisfied or
that it cannot safely propose another supported step. The local model can still
misinterpret an observation; its proposal is not proof that a task succeeded.

For checkable workflows, a plan can use `switch to My app`, `click Show details`,
then `wait for control Details are open`. The wait checks for the named visible
control for up to 8 seconds and stops on timeout. This verifies visible presence,
not unrelated effects such as delivery of a message or successful file upload.

Routines are stored in `routines.json` beside the app, using names as JSON keys,
not paths. Saving the same name replaces its command list. Writes use a temporary
file and replacement; malformed existing storage is not overwritten. Loaded
routines are revalidated against current controls and need `Run the plan` before
execution. Stored commands contain any literal text you included but no images,
screen snapshots, or generated executable code. Limits: 100 routines, 8 steps each.
Canceling a task or starting a new session leaves saved routines intact.

Run regression checks normally. An additional integration check creates and
controls its own disposable test window, calls the local text/vision models,
clicks a test button, checks its visible result, and tests recovery/routine loading:

```powershell
.\venv\Scripts\python.exe tests\live_desktop_smoke.py
```

It needs a normal interactive Windows desktop. The fixture is terminated afterward;
user documents are not modified. This is on-demand awareness and reviewed recovery,
not continuous surveillance or unrestricted autonomous control.

References: [Ollama vision](https://docs.ollama.com/capabilities/vision),
[Pillow window capture](https://pillow.readthedocs.io/en/stable/reference/ImageGrab.html).
