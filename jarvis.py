import subprocess
import http.server
import socketserver
import threading
import requests
import pychromecast
import time
import os
import json
import webbrowser
import shutil
from urllib.parse import quote_plus
from functools import partial
import pyautogui
import wave
import sounddevice as sd
import numpy as np

from pathlib import Path
from faster_whisper import WhisperModel
from dotenv import load_dotenv
load_dotenv()


# ============================================================
# CONFIG
# ============================================================

MY_IP = os.getenv("MY_IP")
CLOCK_IP = os.getenv("CLOCK_IP")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

PIPER_EN_MODEL = os.getenv("PIPER_EN_MODEL")
PIPER_UK_MODEL = os.getenv("PIPER_UK_MODEL")

RECORDING_WAV = os.getenv("RECORDING_WAV")
RESPONSE_WAV = os.getenv("RESPONSE_WAV")

HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))
CLOCK_UUID = os.getenv("CLOCK_UUID")

SAMPLE_RATE = 16000


# ============================================================
# JARVIS AI SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = r"""
You are JARVIS, a witty and concise personal AI computer assistant.

You control a Windows computer through structured actions.

IMPORTANT:
You are the intelligence that decides what actions are necessary.
Do NOT ask Python to interpret the user's natural language.
Instead, determine the exact computer actions yourself and return
them as JSON.

You have access to these SAFE computer actions:

open_application
open_url
google_search
google_first_result
screenshot
mouse_move
mouse_click
mouse_double_click
keyboard_type
keyboard_press
hotkey
volume_up
volume_down
volume_mute
read_file
create_file
move_file
copy_file
list_directory
wait

You MUST NOT use arbitrary shell commands.

You MUST NOT attempt to:
- delete files
- delete folders
- format drives
- modify partitions
- modify the Windows registry
- run commands as administrator
- disable antivirus
- disable Windows Defender
- disable the firewall
- modify firewall rules
- change security policies
- modify boot configuration
- kill system processes
- terminate arbitrary processes
- install software
- uninstall software
- download and execute programs
- execute scripts
- execute PowerShell
- execute CMD
- change user permissions
- access passwords or credentials
- access browser cookies
- access authentication tokens
- make purchases
- send messages or emails without an explicit future action system
- perform financial transactions
- modify system-critical files
- access files outside normal user-accessible locations when unnecessary

If the user asks for a dangerous or unsupported action, return an empty
actions array and briefly explain that the action is blocked.

Do not invent actions.

Your response MUST be valid JSON.

Use exactly this structure:

{
    "speech": "Good explained and long response.",
    "actions": [
        {
            "type": "action_name",
            "parameters": {}
        }
    ]
}

For example:

{
    "speech": "Opening YouTube.",
    "actions": [
        {
            "type": "open_url",
            "parameters": {
                "url": "https://www.youtube.com"
            }
        }
    ]
}

Another example:

{
    "speech": "Opening Visual Studio Code.",
    "actions": [
        {
            "type": "open_application",
            "parameters": {
                "application": "code"
            }
        }
    ]
}

You can perform multiple actions in one response, and open links in a browser, type text, click buttons, and move the mouse.

Example:

{
    "speech": "Opening Chrome and searching for the weather.",
    "actions": [
        {
            "type": "open_application",
            "parameters": {
                "application": "chrome"
            }
        },
        {
            "type": "wait",
            "parameters": {
                "seconds": 2
            }
        },
        {
            "type": "hotkey",
            "parameters": {
                "keys": ["ctrl", "l"]
            }
        },
        {
            "type": "keyboard_type",
            "parameters": {
                "text": "weather in Evansville Indiana"
            }
        },
        {
            "type": "keyboard_press",
            "parameters": {
                "key": "enter"
            }
        }
    ]
}

For computer interaction, use coordinates only when necessary.

IMPORTANT BROWSER RULES:
- If the user asks to search for something AND open the website (e.g., "search for University of Evansville and open the website"), ALWAYS use "google_first_result".
- If the user asks to search Google and open the first result, use "google_first_result" instead of mouse coordinates.
- Prefer direct URLs and browser navigation over coordinate-based clicking when a reliable direct navigation method exists.
- Only use mouse coordinates when there is no safer deterministic way to perform the action.

LANGUAGE RULES:
- Reply in the same language the user is speaking.
- If the user speaks Ukrainian, "speech" must be Ukrainian.
- If the user speaks English, "speech" must be English.
- If the user explicitly asks for another response language, follow that request.

Keep "speech" short, natural, and suitable for speaking aloud (usually 1-3 sentences).

Do not put explanations outside the JSON.
"""


conversation = [
    {
        "role": "system",
        "content": SYSTEM_PROMPT
    }
]


# ============================================================
# SAFETY POLICY
# ============================================================

# These are the ONLY actions Python is allowed to execute.
#
# Ollama decides which ones to use.
# Python simply enforces this allow-list.

ALLOWED_ACTIONS = {
    "open_application",
    "open_url",
    "google_search",
    "google_first_result",
    "screenshot",
    "mouse_move",
    "mouse_click",
    "mouse_double_click",
    "keyboard_type",
    "keyboard_press",
    "hotkey",
    "volume_up",
    "volume_down",
    "volume_mute",
    "read_file",
    "create_file",
    "move_file",
    "copy_file",
    "list_directory",
    "wait",
}


# ============================================================
# ACTIONS THAT ARE ALWAYS BLOCKED
# ============================================================

BLOCKED_ACTIONS = {
    "delete_file",
    "delete_folder",
    "remove_file",
    "remove_folder",
    "format_drive",
    "format_disk",
    "diskpart",
    "registry",
    "registry_write",
    "run_command",
    "shell",
    "powershell",
    "cmd",
    "terminal",
    "execute",
    "execute_command",
    "admin_command",
    "sudo",
    "run_as_admin",
    "install_software",
    "uninstall_software",
    "download_execute",
    "disable_firewall",
    "disable_defender",
    "disable_antivirus",
    "firewall",
    "kill_process",
    "terminate_process",
    "modify_permissions",
    "change_password",
    "access_credentials",
    "access_cookies",
    "access_tokens",
    "modify_boot",
    "shutdown",
    "restart",
}


# ============================================================
# DANGEROUS PATH PROTECTION
# ============================================================

PROTECTED_PATHS = [
    Path(os.environ.get("WINDIR", r"C:\Windows")),
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")),
]


def is_protected_path(path):
    """
    Prevent JARVIS from manipulating important Windows directories.
    """

    try:
        resolved = Path(path).resolve()

        for protected in PROTECTED_PATHS:
            try:
                resolved.relative_to(protected.resolve())
                return True
            except ValueError:
                pass

        return False

    except Exception:
        return True


# ============================================================
# LOAD WHISPER
# ============================================================

print("================================")
print("       JARVIS AI")
print("================================")
print()

print("Loading Whisper model...")

try:
    whisper_model = WhisperModel(
        "small",
        device="cuda",
        compute_type="float16"
    )
    print("Whisper loaded on CUDA.")
except Exception as e:
    print(f"CUDA Whisper failed ({e}). Falling back to CPU.")
    whisper_model = WhisperModel(
        "small",
        device="cpu",
        compute_type="int8"
    )
    print("Whisper loaded on CPU.")

if Path(PIPER_EN_MODEL).exists():
    print("English Piper voice: FOUND")
else:
    print("WARNING: English Piper voice not found.")
    print(f"Expected: {PIPER_EN_MODEL}")

if Path(PIPER_UK_MODEL).exists():
    print("Ukrainian Piper voice: FOUND")
else:
    print("WARNING: Ukrainian Piper voice not found.")
    print(f"Expected: {PIPER_UK_MODEL}")


# ============================================================
# AUDIO RECORDING
# ============================================================

def wait_for_speech_then_record(
    threshold=500,
    max_duration=15,
    silence_duration=1.2
):
    """
    Waits until speech is detected,
    then records until silence.
    """

    chunk_samples = int(
        SAMPLE_RATE * 0.2
    )

    silence_chunks_needed = int(
        silence_duration / 0.2
    )

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16"
    )

    stream.start()

    try:

        print("Waiting for speech...")

        while True:

            chunk, _ = stream.read(
                chunk_samples
            )

            if np.abs(chunk).mean() > threshold:
                break

        print("Recording...")

        frames = [chunk]

        silent_count = 0

        start_time = time.time()

        while time.time() - start_time < max_duration:

            chunk, _ = stream.read(
                chunk_samples
            )

            frames.append(chunk)

            if np.abs(chunk).mean() > threshold:

                silent_count = 0

            else:

                silent_count += 1

                if silent_count >= silence_chunks_needed:
                    break

    finally:

        stream.stop()
        stream.close()

    audio = np.concatenate(
        frames,
        axis=0
    )

    with wave.open(
        RECORDING_WAV,
        "wb"
    ) as wf:

        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)

        wf.writeframes(
            audio.tobytes()
        )


# ============================================================
# WHISPER
# ============================================================

def transcribe_audio():

    # First pass: let Whisper automatically detect the language.
    segments, info = whisper_model.transcribe(
        RECORDING_WAV,
        task="transcribe",
        beam_size=5
    )

    text = " ".join(
        segment.text
        for segment in segments
    ).strip()

    detected_language = getattr(
        info,
        "language",
        None
    ) or "en"

    # --------------------------------------------------------
    # Ukrainian vs Russian correction
    # --------------------------------------------------------
    has_cyrillic = any(
        ("\u0400" <= char <= "\u04ff")
        for char in text
    )

    ukrainian_specific_letters = any(
        letter in text.lower()
        for letter in ("і", "ї", "є", "ґ")
    )

    if detected_language == "ru" and has_cyrillic:
        try:
            uk_segments, uk_info = whisper_model.transcribe(
                RECORDING_WAV,
                language="uk",
                task="transcribe",
                beam_size=5
            )

            uk_text = " ".join(
                segment.text
                for segment in uk_segments
            ).strip()

            if uk_text:
                text = uk_text
                detected_language = "uk"

        except Exception as e:
            print(
                f"[WHISPER] Ukrainian second-pass detection failed: {e}"
            )

    elif ukrainian_specific_letters:
        detected_language = "uk"

    return text, detected_language


# ============================================================
# OLLAMA
# ============================================================

def ask_llm(prompt):

    conversation.append(
        {
            "role": "user",
            "content": prompt
        }
    )

    try:

        response = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": conversation,
                "stream": False,
                "format": "json"
            },
            timeout=120
        )

        response.raise_for_status()

        data = response.json()

        raw_answer = (
            data["message"]["content"]
            .strip()
        )

        print()
        print("AI RAW RESPONSE:")
        print(raw_answer)
        print()

        parsed = json.loads(raw_answer)

        conversation.append(
            {
                "role": "assistant",
                "content": raw_answer
            }
        )

        return parsed

    except json.JSONDecodeError:

        print(
            "WARNING: Ollama returned invalid JSON."
        )

        # Remove the user message that produced a bad response
        # so the conversation does not get two user turns in a row.
        if conversation and conversation[-1]["role"] == "user":
            conversation.pop()

        return {
            "speech": "I couldn't understand my own action plan.",
            "actions": []
        }

    except Exception as e:

        print(
            f"Ollama error: {e}"
        )

        if conversation and conversation[-1]["role"] == "user":
            conversation.pop()

        return {
            "speech": "I couldn't connect to my AI system.",
            "actions": []
        }


# ============================================================
# ACTION VALIDATION
# ============================================================

def validate_action(action):

    if not isinstance(action, dict):

        return False, "Invalid action format."

    action_type = action.get("type")

    if not action_type:

        return False, "Action has no type."

    # Explicitly blocked action
    if action_type in BLOCKED_ACTIONS:

        return (
            False,
            f"Blocked dangerous action: {action_type}"
        )

    # Anything not explicitly allowed is blocked
    if action_type not in ALLOWED_ACTIONS:

        return (
            False,
            f"Unknown or unauthorized action: {action_type}"
        )

    parameters = action.get(
        "parameters",
        {}
    )

    if not isinstance(parameters, dict):

        return (
            False,
            "Action parameters must be an object."
        )

    return True, "OK"


# ============================================================
# SAFE APPLICATION LAUNCHING
# ============================================================

def _normalize_app_name(name):
    return " ".join(
        str(name).strip().lower().replace("_", " ").split()
    )


def _find_start_menu_shortcut(aliases):
    start_menu_dirs = [
        Path(os.environ.get(
            "APPDATA",
            str(Path.home() / "AppData" / "Roaming")
        )) / "Microsoft" / "Windows" / "Start Menu" / "Programs",

        Path(os.environ.get(
            "PROGRAMDATA",
            r"C:\ProgramData"
        )) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]

    normalized_aliases = [
        _normalize_app_name(alias)
        for alias in aliases
    ]

    for start_menu in start_menu_dirs:
        if not start_menu.exists():
            continue

        try:
            for shortcut in start_menu.rglob("*.lnk"):
                shortcut_name = _normalize_app_name(
                    shortcut.stem
                )

                for alias in normalized_aliases:
                    if (
                        shortcut_name == alias
                        or shortcut_name.startswith(alias + " ")
                        or alias in shortcut_name
                    ):
                        return shortcut

        except Exception:
            continue

    return None


def open_application(application):

    key = _normalize_app_name(application)

    allowed_apps = {
        "chrome": {
            "executables": ["chrome.exe"],
            "aliases": ["Google Chrome", "Chrome"],
        },
        "google chrome": {
            "executables": ["chrome.exe"],
            "aliases": ["Google Chrome", "Chrome"],
        },
        "edge": {
            "executables": ["msedge.exe"],
            "aliases": ["Microsoft Edge", "Edge"],
        },
        "microsoft edge": {
            "executables": ["msedge.exe"],
            "aliases": ["Microsoft Edge", "Edge"],
        },
        "firefox": {
            "executables": ["firefox.exe"],
            "aliases": ["Mozilla Firefox", "Firefox"],
        },
        "opera gx": {
            "executables": ["opera.exe"],
            "aliases": ["Opera GX", "Opera GX Browser"],
        },
        "opera gx browser": {
            "executables": ["opera.exe"],
            "aliases": ["Opera GX", "Opera GX Browser"],
        },
        "notepad": {
            "executables": ["notepad.exe"],
            "aliases": ["Notepad"],
        },
        "calculator": {
            "executables": ["calc.exe"],
            "aliases": ["Calculator"],
        },
        "paint": {
            "executables": ["mspaint.exe"],
            "aliases": ["Paint", "Paint 3D"],
        },
        "explorer": {
            "executables": ["explorer.exe"],
            "aliases": ["File Explorer", "Windows Explorer", "Explorer"],
        },
        "code": {
            "executables": ["code.exe"],
            "aliases": ["Visual Studio Code", "VS Code", "Code"],
        },
        "visual studio code": {
            "executables": ["code.exe"],
            "aliases": ["Visual Studio Code", "VS Code", "Code"],
        },
        "spotify": {
            "executables": ["spotify.exe"],
            "aliases": ["Spotify"],
            "uri": "spotify:",
        },
        "discord": {
            "executables": ["Discord.exe"],
            "aliases": ["Discord"],
        },
        "steam": {
            "executables": ["steam.exe"],
            "aliases": ["Steam"],
        },
    }

    if key not in allowed_apps:
        return (
            False,
            f"I don't have permission to launch {application}."
        )

    app = allowed_apps[key]
    aliases = app["aliases"]

    for executable in app["executables"]:
        executable_path = shutil.which(executable)

        if executable_path:
            try:
                subprocess.Popen(
                    [executable_path],
                    shell=False
                )

                return (
                    True,
                    f"Opened {application}."
                )

            except Exception:
                pass

    shortcut = _find_start_menu_shortcut(aliases)

    if shortcut:
        try:
            os.startfile(str(shortcut))

            return (
                True,
                f"Opened {application}."
            )

        except Exception:
            pass

    uri = app.get("uri")

    if uri:
        try:
            os.startfile(uri)

            return (
                True,
                f"Opened {application}."
            )

        except Exception:
            pass

    return (
        False,
        f"Could not find the approved Windows launcher for {application}."
    )


# ============================================================
# ACTION EXECUTOR
# ============================================================

def execute_action(action):

    valid, reason = validate_action(action)

    if not valid:

        print(
            f"[SECURITY] BLOCKED: {reason}"
        )

        return reason

    try:

        action_type = action["type"]

        parameters = action.get(
            "parameters",
            {}
        )

        # --------------------------------------------------------
        # OPEN APPLICATION
        # --------------------------------------------------------

        if action_type == "open_application":

            application = parameters.get(
                "application"
            )

            if not application:

                return "No application was specified."

            success, message = open_application(
                application
            )

            print(
                f"[ACTION] {message}"
            )

            return message

        # --------------------------------------------------------
        # OPEN URL
        # --------------------------------------------------------

        if action_type == "open_url":

            url = parameters.get(
                "url"
            )

            if not url:

                return "No URL was specified."

            if not (
                url.startswith("https://")
                or url.startswith("http://")
            ):

                return "That URL was blocked."

            try:

                webbrowser.open(url)

                message = "Opened the website."

                print(
                    f"[ACTION] {message}"
                )

                return message

            except Exception as e:

                return f"Could not open website: {e}"

        # --------------------------------------------------------
        # GOOGLE SEARCH
        # --------------------------------------------------------

        if action_type == "google_search":

            query = parameters.get(
                "query"
            )

            if not isinstance(query, str) or not query.strip():
                return "No Google search query was specified."

            if len(query) > 500:
                return "That search query was too large."

            try:
                url = (
                    "https://www.google.com/search?q="
                    + quote_plus(query.strip())
                )

                webbrowser.open(url)

                message = (
                    f"Searched Google for {query.strip()}."
                )

                print(
                    f"[ACTION] {message}"
                )

                return message

            except Exception as e:
                return (
                    f"Could not perform the Google search: {e}"
                )

        # --------------------------------------------------------
        # GOOGLE FIRST RESULT (Python Background Scraper)
        # --------------------------------------------------------

        if action_type == "google_first_result":

            query = parameters.get("query")

            if not isinstance(query, str) or not query.strip():
                return "No search query was specified."

            if len(query) > 500:
                return "That search query was too large."

            print(f"[ACTION] Searching for direct link to: {query.strip()}")

            try:
                import re
                from urllib.parse import unquote

                # Spoof a standard web browser user-agent so the
                # search engine does not block the request.
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                }
                search_url = "https://html.duckduckgo.com/html/"
                data = {"q": query.strip()}

                response = requests.post(
                    search_url,
                    data=data,
                    headers=headers,
                    timeout=5
                )
                response.raise_for_status()

                # Extract the first destination URL from
                # DuckDuckGo's redirect wrapper.
                match = re.search(
                    r'href=[\'"]?(?:https?:)?//duckduckgo\.com/l/\?uddg=([^\'">&]+)',
                    response.text
                )

                if match:
                    target_url = unquote(match.group(1))

                    webbrowser.open(target_url)
                    message = f"Opened direct link: {target_url}"
                    print(f"[ACTION] {message}")
                    return message
                else:
                    fallback_url = (
                        "https://www.google.com/search?q="
                        + quote_plus(query.strip())
                    )
                    webbrowser.open(fallback_url)
                    message = (
                        "Could not find a direct link, "
                        "opened search results instead."
                    )
                    print(f"[ACTION] {message}")
                    return message

            except Exception as e:
                return f"Could not perform the search: {e}"

        # --------------------------------------------------------
        # SCREENSHOT
        # --------------------------------------------------------

        if action_type == "screenshot":

            path_str = str(
                parameters.get(
                    "path",
                    r"E:\jarvis\screenshot.png"
                )
            ).strip()

            if not path_str:
                return "No screenshot path was specified."

            path = Path(path_str)

            if is_protected_path(path):

                return "Screenshot location is protected."

            try:

                path.parent.mkdir(
                    parents=True,
                    exist_ok=True
                )

                pyautogui.screenshot(
                    str(path)
                )

                message = (
                    f"Screenshot saved to {path}."
                )

                print(
                    f"[ACTION] {message}"
                )

                return message

            except Exception as e:

                return (
                    f"Could not take screenshot: {e}"
                )

        # --------------------------------------------------------
        # MOUSE MOVE
        # --------------------------------------------------------

        if action_type == "mouse_move":

            x = int(
                parameters.get("x", 0)
            )

            y = int(
                parameters.get("y", 0)
            )

            pyautogui.moveTo(
                x,
                y,
                duration=0.2
            )

            print(
                f"[ACTION] Mouse moved to {x}, {y}"
            )

            return "Moved the mouse."

        # --------------------------------------------------------
        # MOUSE CLICK
        # --------------------------------------------------------

        if action_type == "mouse_click":

            button = parameters.get(
                "button",
                "left"
            )

            if button not in (
                "left",
                "right",
                "middle"
            ):

                return "Invalid mouse button."

            clicks = int(
                parameters.get(
                    "clicks",
                    1
                )
            )

            if clicks < 1 or clicks > 3:

                return "Invalid click count."

            x = parameters.get("x")
            y = parameters.get("y")

            if x is not None and y is not None:
                pyautogui.click(
                    x=int(x),
                    y=int(y),
                    clicks=clicks,
                    button=button
                )
            else:
                pyautogui.click(
                    clicks=clicks,
                    button=button
                )

            print(
                f"[ACTION] Mouse click: {button}"
            )

            return "Clicked."

        # --------------------------------------------------------
        # DOUBLE CLICK
        # --------------------------------------------------------

        if action_type == "mouse_double_click":

            x = parameters.get("x")
            y = parameters.get("y")

            if x is not None and y is not None:
                pyautogui.doubleClick(
                    x=int(x),
                    y=int(y)
                )
            else:
                pyautogui.doubleClick()

            print(
                "[ACTION] Double click"
            )

            return "Double clicked."

        # --------------------------------------------------------
        # TYPE
        # --------------------------------------------------------

        if action_type == "keyboard_type":

            text = parameters.get(
                "text",
                ""
            )

            if not isinstance(text, str):

                return "Invalid text."

            if len(text) > 5000:

                return "That input was too large."

            # pyautogui.write() cannot reliably type non-ASCII
            # characters (e.g. Cyrillic). Use the clipboard for those.
            if any(ord(c) > 127 for c in text):
                try:
                    import pyperclip
                    pyperclip.copy(text)
                    pyautogui.hotkey("ctrl", "v")
                except Exception as e:
                    return f"Could not type non-ASCII text: {e}"
            else:
                pyautogui.write(
                    text,
                    interval=0.01
                )

            print(
                "[ACTION] Typed text."
            )

            return "Typed the requested text."

        # --------------------------------------------------------
        # KEY PRESS
        # --------------------------------------------------------

        if action_type == "keyboard_press":

            key = parameters.get(
                "key"
            )

            if not key:

                return "No key specified."

            allowed_keys = {
                "enter",
                "esc",
                "escape",
                "tab",
                "space",
                "backspace",
                "delete",
                "up",
                "down",
                "left",
                "right",
                "home",
                "end",
                "pageup",
                "pagedown",

                "f1",
                "f2",
                "f3",
                "f4",
                "f5",
                "f6",
                "f7",
                "f8",
                "f9",
                "f10",
                "f11",
                "f12",

                "volumeup",
                "volumedown",
                "volumemute",

                "shift",
                "ctrl",
                "alt",
            }

            key = str(key).lower()

            if key not in allowed_keys:

                return f"Keyboard key blocked: {key}"

            pyautogui.press(key)

            print(
                f"[ACTION] Key pressed: {key}"
            )

            return f"Pressed {key}."

        # --------------------------------------------------------
        # HOTKEY
        # --------------------------------------------------------

        if action_type == "hotkey":

            keys = parameters.get(
                "keys",
                []
            )

            if not isinstance(keys, list):

                return "Invalid hotkey."

            if len(keys) < 2 or len(keys) > 4:

                return "Invalid hotkey length."

            allowed_keys = {
                "ctrl",
                "shift",
                "alt",
                "win",

                "a",
                "c",
                "v",
                "x",
                "z",
                "y",
                "s",
                "f",
                "l",
                "t",
                "w",
                "n",

                "tab",
                "enter",
                "esc",
            }

            normalized = [
                str(key).lower()
                for key in keys
            ]

            for key in normalized:

                if key not in allowed_keys:

                    return (
                        f"Hotkey blocked because of key: {key}"
                    )

            dangerous_hotkeys = {
                ("ctrl", "alt", "delete"),
                ("ctrl", "shift", "esc"),
                ("alt", "f4"),
            }

            if tuple(normalized) in dangerous_hotkeys:

                return "That keyboard shortcut is blocked."

            pyautogui.hotkey(
                *normalized
            )

            print(
                f"[ACTION] Hotkey: {normalized}"
            )

            return "Keyboard shortcut executed."

        # --------------------------------------------------------
        # VOLUME UP
        # --------------------------------------------------------

        if action_type == "volume_up":

            amount = int(
                parameters.get(
                    "amount",
                    2
                )
            )

            amount = min(
                max(amount, 1),
                20
            )

            for _ in range(amount):

                pyautogui.press(
                    "volumeup"
                )

            return "Volume increased."

        # --------------------------------------------------------
        # VOLUME DOWN
        # --------------------------------------------------------

        if action_type == "volume_down":

            amount = int(
                parameters.get(
                    "amount",
                    2
                )
            )

            amount = min(
                max(amount, 1),
                20
            )

            for _ in range(amount):

                pyautogui.press(
                    "volumedown"
                )

            return "Volume decreased."

        # --------------------------------------------------------
        # MUTE
        # --------------------------------------------------------

        if action_type == "volume_mute":

            pyautogui.press(
                "volumemute"
            )

            return "Volume toggled."

        # --------------------------------------------------------
        # READ FILE
        # --------------------------------------------------------

        if action_type == "read_file":

            path_str = str(
                parameters.get("path", "")
            ).strip()

            if not path_str:

                return "No file specified."

            path = Path(path_str)

            if is_protected_path(path):

                return "Access to that protected location is blocked."

            if not path.exists():

                return "That file does not exist."

            if not path.is_file():

                return "That is not a file."

            try:

                content = path.read_text(
                    encoding="utf-8",
                    errors="replace"
                )

                return content[:20000]

            except Exception as e:

                return (
                    f"Could not read the file: {e}"
                )

        # --------------------------------------------------------
        # CREATE FILE
        # --------------------------------------------------------

        if action_type == "create_file":

            path_str = str(
                parameters.get("path", "")
            ).strip()

            content = str(
                parameters.get("content", "")
            )

            if not path_str:

                return "No file path specified."

            path = Path(path_str)

            if is_protected_path(path):

                return "Writing to that protected location is blocked."

            try:

                path.parent.mkdir(
                    parents=True,
                    exist_ok=True
                )

                path.write_text(
                    content,
                    encoding="utf-8"
                )

                return (
                    f"Created {path}."
                )

            except Exception as e:

                return (
                    f"Could not create file: {e}"
                )

        # --------------------------------------------------------
        # MOVE FILE
        # --------------------------------------------------------

        if action_type == "move_file":

            source_str = str(
                parameters.get("source", "")
            ).strip()

            dest_str = str(
                parameters.get("destination", "")
            ).strip()

            if not source_str:
                return "No source file specified."

            if not dest_str:
                return "No destination specified."

            source = Path(source_str)
            destination = Path(dest_str)

            if not source.exists():

                return "The source file does not exist."

            if is_protected_path(source):
                return "That source location is protected."

            if is_protected_path(destination):
                return "That destination is protected."

            try:

                shutil.move(
                    str(source),
                    str(destination)
                )

                return (
                    f"Moved {source} to {destination}."
                )

            except Exception as e:

                return (
                    f"Could not move file: {e}"
                )

        # --------------------------------------------------------
        # COPY FILE
        # --------------------------------------------------------

        if action_type == "copy_file":

            source_str = str(
                parameters.get("source", "")
            ).strip()

            dest_str = str(
                parameters.get("destination", "")
            ).strip()

            if not source_str:
                return "No source file specified."

            if not dest_str:
                return "No destination specified."

            source = Path(source_str)
            destination = Path(dest_str)

            if not source.exists():

                return "The source file does not exist."

            if is_protected_path(source):
                return "That source location is protected."

            if is_protected_path(destination):
                return "That destination is protected."

            try:

                shutil.copy2(
                    source,
                    destination
                )

                return (
                    f"Copied {source} to {destination}."
                )

            except Exception as e:

                return (
                    f"Could not copy file: {e}"
                )

        # --------------------------------------------------------
        # LIST DIRECTORY
        # --------------------------------------------------------

        if action_type == "list_directory":

            path_str = str(
                parameters.get("path", ".")
            ).strip()

            if not path_str:
                path_str = "."

            path = Path(path_str)

            if is_protected_path(path):

                return "That directory is protected."

            if not path.exists():

                return "That directory does not exist."

            if not path.is_dir():

                return "That is not a directory."

            try:

                entries = []

                for item in path.iterdir():

                    entries.append(
                        {
                            "name": item.name,
                            "type": (
                                "directory"
                                if item.is_dir()
                                else "file"
                            )
                        }
                    )

                return json.dumps(
                    entries[:500]
                )

            except Exception as e:

                return (
                    f"Could not list directory: {e}"
                )

        # --------------------------------------------------------
        # WAIT
        # --------------------------------------------------------

        if action_type == "wait":

            seconds = float(
                parameters.get(
                    "seconds",
                    1
                )
            )

            seconds = min(
                max(seconds, 0),
                10
            )

            time.sleep(seconds)

            return "Waited."

        return "Action was not executed."

    except Exception as e:

        print(
            f"[ACTION ERROR] {e}"
        )

        return f"Action failed: {e}"


# ============================================================
# EXECUTE AI ACTION PLAN
# ============================================================

def execute_action_plan(plan):

    if not isinstance(plan, dict):

        return (
            "I received an invalid action plan."
        )

    speech = plan.get(
        "speech",
        "Done."
    )

    actions = plan.get(
        "actions",
        []
    )

    if not isinstance(actions, list):

        print(
            "[SECURITY] Invalid actions list."
        )

        return speech

    print()
    print("JARVIS ACTION PLAN")
    print("------------------")

    for action in actions:

        print(
            json.dumps(
                action,
                indent=2
            )
        )

    print("------------------")

    results = []

    for action in actions:

        result = execute_action(
            action
        )

        results.append(result)

    return speech


# ============================================================
# PIPER TTS
# ============================================================

def speak_to_wav(text, out_path, language="en"):

    if language == "uk":
        model = PIPER_UK_MODEL
    else:
        if language != "en":
            print(
                f"[TTS] No voice for '{language}', using English voice."
            )
        model = PIPER_EN_MODEL

    if not Path(model).exists():
        raise FileNotFoundError(
            f"Piper voice model was not found: {model}"
        )

    subprocess.run(
        [
            "piper",
            "--model",
            model,
            "--output_file",
            out_path
        ],
        input=text.encode("utf-8"),
        check=True
    )


# ============================================================
# HTTP SERVER FOR LENOVO
# ============================================================

class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_directory(directory, port):

    handler = partial(
        http.server.SimpleHTTPRequestHandler,
        directory=directory
    )

    httpd = ReusableTCPServer(
        ("", port),
        handler
    )

    threading.Thread(
        target=httpd.serve_forever,
        daemon=True
    ).start()

    return httpd


# ============================================================
# CONNECT TO LENOVO
# ============================================================

def connect_cast():

    print(
        "Connecting to Lenovo Smart Clock..."
    )

    host = (
        CLOCK_IP,
        8009,
        CLOCK_UUID,
        None,
        None
    )

    cast = (
        pychromecast
        .get_chromecast_from_host(host)
    )

    cast.wait()

    print(
        "Connected to Lenovo."
    )

    return cast


# ============================================================
# HANDLE ONE VOICE TURN
# ============================================================

def handle_turn(cast):

    print()
    print("=" * 50)
    print("LISTENING")
    print("=" * 50)

    wait_for_speech_then_record()

    question, detected_language = transcribe_audio()

    print()
    print(
        f"You said: {question}"
    )
    print(
        f"Detected language: {detected_language}"
    )

    if not question:

        print(
            "Didn't catch anything."
        )

        return

    # --------------------------------------------------------
    # ASK THE AI
    # --------------------------------------------------------

    language_name = (
        "Ukrainian"
        if detected_language == "uk"
        else "English"
        if detected_language == "en"
        else detected_language
    )

    plan = ask_llm(
        f"Detected spoken language: {language_name} ({detected_language}). "
        f"Respond in the same language unless the user explicitly asks for "
        f"a different response language. User request: {question}"
    )

    print()
    print(
        "JARVIS:",
        plan.get(
            "speech",
            ""
        )
    )

    # --------------------------------------------------------
    # EXECUTE AI'S PLAN
    # --------------------------------------------------------

    speech = execute_action_plan(
        plan
    )

    # --------------------------------------------------------
    # SPEAK
    # --------------------------------------------------------

    if not speech:

        speech = "Done."

    speak_to_wav(
        speech,
        RESPONSE_WAV,
        language=detected_language
    )

    # --------------------------------------------------------
    # PLAY THROUGH LENOVO
    # --------------------------------------------------------

    url = (
        f"http://{MY_IP}:"
        f"{HTTP_PORT}/response.wav"
    )

    cast.media_controller.play_media(
        url,
        "audio/wav"
    )

    cast.media_controller.block_until_active()

    time.sleep(1)


# ============================================================
# MAIN
# ============================================================

def main():

    httpd = None

    try:

        cast = connect_cast()

        httpd = serve_directory(
            r"E:\jarvis",
            HTTP_PORT
        )

        print()
        print("=" * 50)
        print("          JARVIS IS ONLINE")
        print("=" * 50)
        print()
        print(
            "AI computer control enabled."
        )
        print(
            "Dangerous operations are blocked."
        )
        print(
            "Press Ctrl+C to stop."
        )
        print()

        while True:

            try:

                handle_turn(cast)

            except Exception as e:

                print(
                    f"[TURN ERROR] {e}"
                )

                time.sleep(2)

    except KeyboardInterrupt:

        print()
        print(
            "Shutting down JARVIS..."
        )

    finally:

        if httpd is not None:

            httpd.shutdown()
            httpd.server_close()

        print(
            "Server closed cleanly."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()