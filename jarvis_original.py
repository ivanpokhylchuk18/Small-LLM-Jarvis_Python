"""
IVATRON - AI Computer Assistant
Advanced single-file version.

Features:
- Ollama LLM + tool-calling via structured JSON actions
- faster-whisper STT with EN/UK detection + correction
- Piper TTS with EN/UK voices
- Lenovo Smart Clock (Chromecast) audio output (lazy connect + PC fallback)
- Vision (screenshot -> Ollama vision model), gracefully skipped if missing
- Screen element finding (find_element)
- Verification of actions (optional, vision-backed)
- Browser controller (navigate, search, back/forward, refresh, read page)
- Long-term memory (memory.json)
- Workspace sandbox for file operations
- Logging to logs/iv....log
- Status dashboard at http://<ip>:<port>/dashboard
- Optional wake word ("hey jarvis") via openwakeword
- Optional VAD via webrtcvad
- Explicit language switching ("speak Ukrainian", "speak English")
"""

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
import logging
import base64
import re
import traceback
from datetime import datetime
from urllib.parse import quote_plus, unquote
from functools import partial
from pathlib import Path

import pyautogui
import wave
import sounddevice as sd
import numpy as np

try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False

from faster_whisper import WhisperModel
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# OPTIONAL DEPENDENCIES
# ============================================================

try:
    import webrtcvad
    HAS_WEBRTCVAD = True
except ImportError:
    HAS_WEBRTCVAD = False

try:
    from openwakeword.model import Model as OWWModel
    import openwakeword
    HAS_OWW = True
except ImportError:
    HAS_OWW = False


# ============================================================
# CONFIG
# ============================================================

MY_IP = os.getenv("MY_IP", "192.168.1.71")
CLOCK_IP = os.getenv("CLOCK_IP", "192.168.1.124")
CLOCK_UUID = os.getenv(
    "CLOCK_UUID",
    "6417e6b7-60ba-df93-d9c4-7218458fb1b4"
)

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava:latest")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

PIPER_EN_MODEL = os.getenv("PIPER_EN_MODEL")
PIPER_UK_MODEL = os.getenv("PIPER_UK_MODEL")

# Optional speaker index, only needed for multi-speaker Piper models
# (e.g. some uk_UA voices ship several speakers in one .onnx file).
# Single-speaker models like en_US-ryan or en_GB-alan don't need this.
PIPER_EN_SPEAKER = os.getenv("PIPER_EN_SPEAKER")
PIPER_UK_SPEAKER = os.getenv("PIPER_UK_SPEAKER")

RECORDING_WAV = os.getenv("RECORDING_WAV", r"E:\jarvis\input.wav")
RESPONSE_WAV = os.getenv("RESPONSE_WAV", r"E:\jarvis\response.wav")
SCREENSHOT_PATH = os.getenv("SCREENSHOT_PATH", r"E:\jarvis\screenshot.png")

HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))

SAMPLE_RATE = 16000

# --- Speed tuning (Phase 1.5) -------------------------------------
# Smaller model / lower beam size = faster but slightly less accurate.
# tiny < base < small < medium in both speed and accuracy.
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "1"))
# If true, also write the mic recording to RECORDING_WAV on disk
# (useful for debugging what Whisper heard). Off by default: Whisper
# transcribes straight from the in-memory buffer, which skips a disk
# write + disk read on every single turn.
SAVE_RECORDINGS = os.getenv("SAVE_RECORDINGS", "false").lower() == "true"

WORKSPACE_ROOT = Path(
    os.getenv("WORKSPACE_ROOT", r"E:\jarvis\workspace")
)

LOGS_DIR = Path(os.getenv("LOGS_DIR", r"E:\jarvis\logs"))
MEMORY_FILE = Path(
    os.getenv("MEMORY_FILE", r"E:\jarvis\memory.json")
)

USE_WAKE_WORD = os.getenv("USE_WAKE_WORD", "false").lower() == "true"
USE_VAD = os.getenv("USE_VAD", "false").lower() == "true"
USE_VERIFICATION = (
    os.getenv("USE_VERIFICATION", "false").lower() == "true"
)

WAKE_WORD_MODEL = os.getenv("WAKE_WORD_MODEL", "hey_ivantron.onnx")

ALLOW_OUTSIDE_WORKSPACE = (
    os.getenv("ALLOW_OUTSIDE_WORKSPACE", "false").lower() == "true"
)

# Lazy, on-demand cast connection.
# We only try to reach the clock when we actually need to play audio.
CAST_CONNECT_ATTEMPTS = int(os.getenv("CAST_CONNECT_ATTEMPTS", "2"))
CAST_CONNECT_TIMEOUT = float(os.getenv("CAST_CONNECT_TIMEOUT", "6"))
CAST_RETRY_WAIT = float(os.getenv("CAST_RETRY_WAIT", "1"))


# ============================================================
# LOGGING
# ============================================================

LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("iv...")
logger.setLevel(logging.INFO)

if not logger.handlers:
    fh = logging.FileHandler(
        LOGS_DIR / "iv....log",
        encoding="utf-8"
    )
    fh.setFormatter(
        logging.Formatter(
            "[%(asctime)s] %(levelname)s %(message)s",
            datefmt="%H:%M:%S"
        )
    )
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(sh)


# ============================================================
# STATUS (for dashboard)
# ============================================================

STATUS = {
    "state": "starting",
    "ollama": "unknown",
    "vision": "unknown",
    "whisper": "loading",
    "piper_en": "unknown",
    "piper_uk": "unknown",
    "lenovo": "disconnected",
    "microphone": "idle",
    "last_command": "",
    "last_response": "",
    "last_action": "",
    "last_error": "",
    "last_timings": {},
    "last_total_time": None,
    "started": datetime.now().isoformat(timespec="seconds"),
}

STATUS_LOCK = threading.Lock()


def set_status(**kwargs):
    with STATUS_LOCK:
        STATUS.update(kwargs)


VISION_AVAILABLE = False


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = r"""
You are Ivatron, a witty and concise personal AI computer assistant.

You control a Windows computer through structured JSON actions.

You are the intelligence that decides what actions are necessary.
Do NOT ask Python to interpret the user's natural language.
Determine the exact actions yourself and return them as JSON.

Available SAFE actions:

  --- Applications / URLs ---
  open_application          { application: str }
  open_url                  { url: str }
  google_search             { query: str }
  google_first_result       { query: str }

  --- Browser controller (works on the focused browser) ---
  browser_navigate          { url: str }
  browser_search            { query: str }
  browser_back              {}
  browser_forward           {}
  browser_refresh           {}
  browser_find              { text: str }
  browser_read_page         {}
  browser_new_tab           { url: str }   (optional url)

  --- Vision ---
  screenshot                { path?: str }
  vision_query              { prompt: str }
  find_element              { description: str, click?: bool }

  --- Mouse / keyboard ---
  mouse_move                { x: int, y: int }
  mouse_click                { button?: str, clicks?: int, x?: int, y?: int }
  mouse_double_click        { x?: int, y?: int }
  keyboard_type              { text: str }
  keyboard_press             { key: str }
  hotkey                     { keys: [str] }

  --- Volume ---
  volume_up                 { amount?: int }
  volume_down               { amount?: int }
  volume_mute                {}

  --- Files (sandboxed to the workspace) ---
  read_file                  { path: str }
  create_file                { path: str, content: str }
  move_file                  { source: str, destination: str }
  copy_file                  { source: str, destination: str }
  list_directory              { path?: str }

  --- Memory ---
  memory_save                { key: str, value: str }
  memory_forget               { key: str }
  memory_list                 {}

  --- Misc ---
  wait                       { seconds: float }

You MUST NOT use arbitrary shell commands.

You MUST NOT attempt to:
- delete files or folders
- format drives or modify partitions
- modify the Windows registry
- run commands as administrator
- disable antivirus, Defender, or the firewall
- modify firewall rules or security policies
- modify boot configuration
- kill or terminate arbitrary processes
- install or uninstall software
- download and execute programs
- execute scripts, PowerShell, or CMD
- change user permissions
- access passwords, credentials, cookies, or tokens
- make purchases or perform financial transactions
- modify system-critical files

If the user asks for a dangerous or unsupported action, return an empty
actions array and briefly explain that the action is blocked.

If the user is just making conversation, asking a question you can
answer directly, or saying something that doesn't require touching the
computer (e.g. "how are you", "what's 12 times 4", "tell me a joke"),
return an EMPTY actions array and just answer in "speech". Do not call
an action "to be helpful" or "to check something" unless the user's
request actually requires interacting with the computer or browser.

Do not invent actions.

Your response MUST be valid JSON using exactly this structure:

{
    "speech": "Short natural spoken reply (1-3 sentences).",
    "actions": [
        { "type": "action_name", "parameters": { } }
    ]
}

Important browser rules:
- If the user asks to search for something AND open the website, use
  "google_first_result" OR "browser_search" then "find_element" with the
  site name.
- Prefer direct URLs and the browser_* actions over coordinate clicking.
- Use mouse coordinates only when no deterministic method exists.
- When you need to know what is on screen, use "screenshot" or
  "vision_query".
- When you need to click something visible but you don't know its
  coordinates, use "find_element".

Vision rules:
- "vision_query", "screenshot", and "find_element" always operate on a
  live screenshot of the CURRENT screen. Their optional "path" is only
  where to save that screenshot -- never a file to inspect, and never a
  Windows/system path. In almost all cases, omit "path" entirely and let
  it use the default location.
- Never call "vision_query" or "screenshot" just to answer a general
  question like "how are you" or "what's the weather" -- those have
  nothing to do with the screen and need no actions at all.

Language rules:
- Reply in the same language the user is speaking.
- Ukrainian -> Ukrainian "speech". English -> English "speech".
- If the user says "speak Ukrainian" or "говори українською", reply in
  Ukrainian from then on.
- If the user says "speak English" or "говори англійською", reply in
  English from then on.
- If the user explicitly asks for another response language, follow it.

Keep "speech" short and natural for speaking aloud.
Do not put explanations outside the JSON.
"""


# ============================================================
# CONVERSATION
# ============================================================

conversation = [
    {
        "role": "system",
        "content": SYSTEM_PROMPT
    }
]

# Max number of non-system messages (user+assistant turns) to keep.
# Without this, `conversation` grows forever, eventually blowing past
# the model's context window and causing garbled / truncated JSON,
# slow responses, or silent Ollama errors after a long session.
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))

LANGUAGE_OVERRIDE = {"value": None}


def trim_conversation():
    """Keep the system prompt plus only the most recent turns."""
    if len(conversation) > MAX_HISTORY_MESSAGES + 1:
        del conversation[1:len(conversation) - MAX_HISTORY_MESSAGES]


# ============================================================
# SAFETY POLICY
# ============================================================

ALLOWED_ACTIONS = {
    "open_application",
    "open_url",
    "google_search",
    "google_first_result",
    "browser_navigate",
    "browser_search",
    "browser_back",
    "browser_forward",
    "browser_refresh",
    "browser_find",
    "browser_read_page",
    "browser_new_tab",
    "screenshot",
    "vision_query",
    "find_element",
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
    "memory_save",
    "memory_forget",
    "memory_list",
    "wait",
}

BLOCKED_ACTIONS = {
    "delete_file", "delete_folder", "remove_file", "remove_folder",
    "format_drive", "format_disk", "diskpart",
    "registry", "registry_write",
    "run_command", "shell", "powershell", "cmd", "terminal",
    "execute", "execute_command", "admin_command",
    "sudo", "run_as_admin",
    "install_software", "uninstall_software", "download_execute",
    "disable_firewall", "disable_defender", "disable_antivirus",
    "firewall", "kill_process", "terminate_process",
    "modify_permissions", "change_password",
    "access_credentials", "access_cookies", "access_tokens",
    "modify_boot", "shutdown", "restart",
}


# ============================================================
# PATH PROTECTION
# ============================================================

PROTECTED_PATHS = [
    Path(os.environ.get("WINDIR", r"C:\Windows")),
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")),
]


def is_protected_path(path):
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


def resolve_workspace_path(path_str, create_parent=True):
    if not path_str:
        raise ValueError("Empty path.")

    p = Path(path_str)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p

    try:
        resolved = p.resolve()
    except Exception:
        resolved = p

    if not ALLOW_OUTSIDE_WORKSPACE:
        try:
            workspace_resolved = WORKSPACE_ROOT.resolve()
            resolved.relative_to(workspace_resolved)
        except ValueError:
            raise ValueError(
                f"Path '{resolved}' is outside the workspace "
                f"({WORKSPACE_ROOT})."
            )

    if is_protected_path(resolved):
        raise ValueError(f"Path '{resolved}' is protected.")

    if create_parent:
        resolved.parent.mkdir(parents=True, exist_ok=True)

    return resolved


def resolve_screenshot_path(path_str):
    """
    Resolve a path for a screenshot/vision action defensively.

    vision_query/screenshot/find_element only ever need to read or write
    a screenshot image -- never an arbitrary file the model dreamed up.
    If the given path is empty, protected, or outside the allowed
    screenshot/workspace locations (including drives that don't exist
    on this machine), fall back to the default SCREENSHOT_PATH instead
    of raising, so one bad path from the model can't fail the whole
    action plan.
    """
    default_path = Path(SCREENSHOT_PATH)
    path_str = (path_str or "").strip()

    if not path_str:
        return default_path

    candidate = Path(path_str)

    allowed_roots = [default_path.parent, WORKSPACE_ROOT]
    try:
        resolved = candidate.resolve()
    except Exception:
        logger.warning(f"[VISION] Unusable path '{path_str}', using default.")
        return default_path

    is_allowed = False
    for root in allowed_roots:
        try:
            resolved.relative_to(root.resolve())
            is_allowed = True
            break
        except Exception:
            continue

    if not is_allowed or is_protected_path(resolved):
        logger.warning(
            f"[VISION] Ignoring out-of-bounds path '{path_str}', "
            f"using default screenshot location instead."
        )
        return default_path

    return resolved


def ensure_screenshot_dir(path):
    """Create a screenshot's parent directory, never letting a bad
    path (e.g. a drive that doesn't exist on this machine) raise."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.warning(f"[VISION] Could not create '{path.parent}': {e}")
        return False


# ============================================================
# MEMORY
# ============================================================

MEMORY_LOCK = threading.Lock()


def load_memory():
    try:
        if MEMORY_FILE.exists():
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "facts" in data:
                    return data
    except Exception as e:
        logger.warning(f"Memory load failed: {e}")
    return {"facts": {}}


def save_memory(mem):
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with MEMORY_LOCK:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(mem, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.warning(f"Memory save failed: {e}")
        return False


def memory_save_fact(key, value):
    if not key:
        return False
    mem = load_memory()
    mem["facts"][str(key)] = str(value)
    return save_memory(mem)


def memory_forget_fact(key):
    mem = load_memory()
    if key in mem["facts"]:
        del mem["facts"][key]
        return save_memory(mem)
    return False


def refresh_conversation_memory():
    mem = load_memory()
    facts = mem.get("facts", {})
    if not facts:
        conversation[0]["content"] = SYSTEM_PROMPT
        return

    lines = ["", "REMEMBERED FACTS ABOUT THE USER:"]
    for k, v in facts.items():
        lines.append(f"  - {k}: {v}")
    conversation[0]["content"] = SYSTEM_PROMPT + "\n".join(lines)


# ============================================================
# WHISPER
# ============================================================

print("================================")
print("       IVATRON AI")
print("================================")
print()

print("Loading Whisper model...")
logger.info(f"Loading Whisper model ({WHISPER_MODEL_SIZE})...")

try:
    whisper_model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device="cuda",
        compute_type="float16"
    )
    logger.info("Whisper loaded on CUDA.")
    set_status(whisper="ready (cuda)")
except Exception as e:
    logger.warning(f"CUDA Whisper failed ({e}). Falling back to CPU.")
    whisper_model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device="cpu",
        compute_type="int8"
    )
    logger.info("Whisper loaded on CPU.")
    set_status(whisper="ready (cpu)")

if PIPER_EN_MODEL and Path(PIPER_EN_MODEL).exists():
    set_status(piper_en="ready")
    logger.info("English Piper voice: FOUND")
else:
    set_status(piper_en="missing")
    logger.warning(f"English Piper voice not found: {PIPER_EN_MODEL}")

if PIPER_UK_MODEL and Path(PIPER_UK_MODEL).exists():
    set_status(piper_uk="ready")
    logger.info("Ukrainian Piper voice: FOUND")
else:
    set_status(piper_uk="missing")
    logger.warning(f"Ukrainian Piper voice not found: {PIPER_UK_MODEL}")

WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
for sub in ("Documents", "Projects", "Downloads", "Temp"):
    (WORKSPACE_ROOT / sub).mkdir(parents=True, exist_ok=True)


# ============================================================
# AUDIO / VAD / WAKE WORD
# ============================================================

_vad = webrtcvad.Vad(2) if HAS_WEBRTCVAD else None
_oww_model = None


def _init_wake_word():
    global _oww_model
    if not (USE_WAKE_WORD and HAS_OWW):
        return
    try:
        logger.info("Downloading / loading wake word model...")
        try:
            openwakeword.utils.download_models()
        except Exception:
            pass
        _oww_model = OWWModel(
            wakeword_models=[WAKE_WORD_MODEL],
            inference_framework="onnx"
        )
        logger.info(f"Wake word model loaded: {WAKE_WORD_MODEL}")
    except Exception as e:
        logger.warning(f"Wake word init failed: {e}")
        _oww_model = None


def _chunk_is_speech(chunk_int16):
    if _vad is not None:
        try:
            frame_ms = 30
            frame_samples = int(SAMPLE_RATE * frame_ms / 1000)
            votes = 0
            total = 0
            for i in range(0, len(chunk_int16) - frame_samples + 1, frame_samples):
                frame = chunk_int16[i:i + frame_samples]
                try:
                    if _vad.is_speech(frame.tobytes(), SAMPLE_RATE):
                        votes += 1
                except Exception:
                    pass
                total += 1
            return total > 0 and (votes / total) >= 0.5
        except Exception:
            pass
    return float(np.abs(chunk_int16).mean()) > 500.0


def _wait_for_wake_word():
    if not (USE_WAKE_WORD and _oww_model is not None):
        return

    logger.info("Waiting for wake word...")
    set_status(microphone="waiting for wake word")

    chunk_samples = 1280
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16"
    )
    stream.start()
    try:
        while True:
            chunk, _ = stream.read(chunk_samples)
            audio = chunk.flatten()
            try:
                prediction = _oww_model.predict(audio)
                for _, score in prediction.items():
                    if score and score > 0.5:
                        logger.info("Wake word detected.")
                        return
            except Exception:
                time.sleep(0.05)
    finally:
        stream.stop()
        stream.close()


def wait_for_speech_then_record(
    threshold=500,
    max_duration=15,
    silence_duration=1.2
):
    chunk_samples = int(SAMPLE_RATE * 0.2)
    silence_chunks_needed = int(silence_duration / 0.2)

    _wait_for_wake_word()

    set_status(microphone="listening")

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16"
    )
    stream.start()

    try:
        logger.info("Waiting for speech...")

        while True:
            chunk, _ = stream.read(chunk_samples)
            flat = chunk.flatten()
            if USE_VAD and HAS_WEBRTCVAD:
                if _chunk_is_speech(flat):
                    break
            else:
                if float(np.abs(flat).mean()) > threshold:
                    break

        logger.info("Recording...")
        set_status(microphone="recording")

        frames = [chunk]
        silent_count = 0
        start_time = time.time()

        while time.time() - start_time < max_duration:
            chunk, _ = stream.read(chunk_samples)
            frames.append(chunk)
            flat = chunk.flatten()

            if USE_VAD and HAS_WEBRTCVAD:
                is_speech = _chunk_is_speech(flat)
            else:
                is_speech = float(np.abs(flat).mean()) > threshold

            if is_speech:
                silent_count = 0
            else:
                silent_count += 1
                if silent_count >= silence_chunks_needed:
                    break

    finally:
        stream.stop()
        stream.close()
        set_status(microphone="idle")

    audio = np.concatenate(frames, axis=0).flatten()

    if SAVE_RECORDINGS:
        try:
            with wave.open(RECORDING_WAV, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio.tobytes())
        except Exception as e:
            logger.warning(f"Could not save recording: {e}")

    return audio


# ============================================================
# TRANSCRIPTION
# ============================================================

def transcribe_audio(audio):
    """
    Transcribe an in-memory int16 mono audio buffer directly, with no
    disk round trip. `audio` is the numpy array returned by
    wait_for_speech_then_record().
    """
    audio_float32 = np.ascontiguousarray(
        audio.flatten().astype(np.float32) / 32768.0
    )

    segments, info = whisper_model.transcribe(
        audio_float32,
        task="transcribe",
        beam_size=WHISPER_BEAM_SIZE
    )
    text = " ".join(s.text for s in segments).strip()
    detected_language = getattr(info, "language", None) or "en"

    has_cyrillic = any("\u0400" <= c <= "\u04ff" for c in text)
    uk_specific = any(c in text.lower() for c in ("і", "ї", "є", "ґ"))

    needs_uk_pass = (
        (detected_language == "ru" and has_cyrillic)
        or (uk_specific and detected_language != "uk")
    )

    if needs_uk_pass:
        try:
            uk_segments, _ = whisper_model.transcribe(
                audio_float32,
                language="uk",
                task="transcribe",
                beam_size=WHISPER_BEAM_SIZE
            )
            uk_text = " ".join(s.text for s in uk_segments).strip()
            if uk_text:
                text = uk_text
                detected_language = "uk"
        except Exception as e:
            logger.warning(f"Ukrainian second-pass failed: {e}")
    elif uk_specific:
        detected_language = "uk"

    return text, detected_language


# ============================================================
# OLLAMA
# ============================================================

def ollama_available():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        return True
    except Exception:
        return False


def check_vision_model():
    global VISION_AVAILABLE
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m.get("name", "") for m in r.json().get("models", [])]

        base = OLLAMA_VISION_MODEL.split(":")[0]
        for name in models:
            if (
                name == OLLAMA_VISION_MODEL
                or name == base
                or name.startswith(base + ":")
            ):
                VISION_AVAILABLE = True
                set_status(vision="ready")
                logger.info(
                    f"Vision model available: {OLLAMA_VISION_MODEL} "
                    f"(matched '{name}')"
                )
                return True

        VISION_AVAILABLE = False
        set_status(vision="missing")
        logger.warning(
            f"Vision model '{OLLAMA_VISION_MODEL}' not found in Ollama. "
            f"Run: ollama pull {OLLAMA_VISION_MODEL}"
        )
        return False
    except Exception as e:
        VISION_AVAILABLE = False
        set_status(vision="unreachable")
        logger.warning(f"Could not check vision models: {e}")
        return False


def ask_llm(prompt):
    refresh_conversation_memory()
    conversation.append({"role": "user", "content": prompt})

    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": conversation,
                "stream": False,
                "format": "json"
            },
            timeout=180
        )
        response.raise_for_status()
        data = response.json()
        raw_answer = data["message"]["content"].strip()

        logger.info("AI RAW RESPONSE:")
        logger.info(raw_answer)

        parsed = json.loads(raw_answer)
        conversation.append(
            {"role": "assistant", "content": raw_answer}
        )
        trim_conversation()
        return parsed

    except json.JSONDecodeError:
        logger.warning("Ollama returned invalid JSON.")
        if conversation and conversation[-1]["role"] == "user":
            conversation.pop()
        return {
            "speech": "I couldn't understand my own action plan.",
            "actions": []
        }

    except Exception as e:
        logger.error(f"Ollama error: {e}")
        if conversation and conversation[-1]["role"] == "user":
            conversation.pop()
        return {
            "speech": "I couldn't connect to my AI system.",
            "actions": []
        }


def vision_query_ollama(image_path, prompt):
    if not VISION_AVAILABLE:
        return "__VISION_ERROR__: vision model not available"
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("ascii")

        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_VISION_MODEL,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False
            },
            timeout=180
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()
    except Exception as e:
        logger.error(f"Vision query failed: {e}")
        return f"__VISION_ERROR__: {e}"


# ============================================================
# ACTION VALIDATION
# ============================================================

def validate_action(action):
    if not isinstance(action, dict):
        return False, "Invalid action format."

    action_type = action.get("type")
    if not action_type:
        return False, "Action has no type."

    if action_type in BLOCKED_ACTIONS:
        return False, f"Blocked dangerous action: {action_type}"

    if action_type not in ALLOWED_ACTIONS:
        return False, f"Unknown or unauthorized action: {action_type}"

    parameters = action.get("parameters", {})
    if not isinstance(parameters, dict):
        return False, "Action parameters must be an object."

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

    normalized_aliases = [_normalize_app_name(a) for a in aliases]

    for start_menu in start_menu_dirs:
        if not start_menu.exists():
            continue
        try:
            for shortcut in start_menu.rglob("*.lnk"):
                shortcut_name = _normalize_app_name(shortcut.stem)
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


ALLOWED_APPS = {
    "chrome": {"executables": ["chrome.exe"],
               "aliases": ["Google Chrome", "Chrome"]},
    "google chrome": {"executables": ["chrome.exe"],
                      "aliases": ["Google Chrome", "Chrome"]},
    "edge": {"executables": ["msedge.exe"],
             "aliases": ["Microsoft Edge", "Edge"]},
    "microsoft edge": {"executables": ["msedge.exe"],
                       "aliases": ["Microsoft Edge", "Edge"]},
    "firefox": {"executables": ["firefox.exe"],
                "aliases": ["Mozilla Firefox", "Firefox"]},
    "opera gx": {"executables": ["opera.exe"],
                 "aliases": ["Opera GX", "Opera GX Browser"]},
    "opera gx browser": {"executables": ["opera.exe"],
                         "aliases": ["Opera GX", "Opera GX Browser"]},
    "notepad": {"executables": ["notepad.exe"], "aliases": ["Notepad"]},
    "calculator": {"executables": ["calc.exe"], "aliases": ["Calculator"]},
    "paint": {"executables": ["mspaint.exe"], "aliases": ["Paint", "Paint 3D"]},
    "explorer": {"executables": ["explorer.exe"],
                 "aliases": ["File Explorer", "Windows Explorer", "Explorer"]},
    "code": {"executables": ["code.exe"],
             "aliases": ["Visual Studio Code", "VS Code", "Code"]},
    "visual studio code": {"executables": ["code.exe"],
                           "aliases": ["Visual Studio Code", "VS Code", "Code"]},
    "spotify": {"executables": ["spotify.exe"],
                "aliases": ["Spotify"], "uri": "spotify:"},
    "discord": {"executables": ["Discord.exe"], "aliases": ["Discord"]},
    "steam": {"executables": ["steam.exe"], "aliases": ["Steam"]},
}


def open_application(application):
    key = _normalize_app_name(application)

    if key not in ALLOWED_APPS:
        return False, f"I don't have permission to launch {application}."

    app = ALLOWED_APPS[key]

    for executable in app["executables"]:
        executable_path = shutil.which(executable)
        if executable_path:
            try:
                subprocess.Popen([executable_path], shell=False)
                return True, f"Opened {application}."
            except Exception:
                pass

    shortcut = _find_start_menu_shortcut(app["aliases"])
    if shortcut:
        try:
            os.startfile(str(shortcut))
            return True, f"Opened {application}."
        except Exception:
            pass

    uri = app.get("uri")
    if uri:
        try:
            os.startfile(uri)
            return True, f"Opened {application}."
        except Exception:
            pass

    return False, f"Could not find the approved Windows launcher for {application}."


# ============================================================
# BROWSER HELPERS
# ============================================================

def _focus_browser():
    try:
        import pygetwindow as gw
    except ImportError:
        return

    keywords = ("chrome", "edge", "firefox", "opera", "brave", "vivaldi")
    for w in reversed(gw.getAllWindows()):
        title = (w.title or "").lower()
        if any(k in title for k in keywords):
            try:
                if w.isMinimized:
                    w.restore()
                w.activate()
                return
            except Exception:
                pass


def browser_navigate(url):
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    _focus_browser()
    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "a")
    if HAS_PYPERCLIP:
        pyperclip.copy(url)
        pyautogui.hotkey("ctrl", "v")
    else:
        pyautogui.write(url, interval=0.005)
    time.sleep(0.1)
    pyautogui.press("enter")
    return f"Navigated to {url}."


def browser_search(query):
    _focus_browser()
    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "a")
    if HAS_PYPERCLIP:
        pyperclip.copy(query)
        pyautogui.hotkey("ctrl", "v")
    else:
        pyautogui.write(query, interval=0.005)
    pyautogui.press("enter")
    return f"Searched for {query}."


def browser_new_tab(url=None):
    _focus_browser()
    pyautogui.hotkey("ctrl", "t")
    time.sleep(0.4)
    if url:
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url
        if HAS_PYPERCLIP:
            pyperclip.copy(url)
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(url, interval=0.005)
        pyautogui.press("enter")
    return "Opened a new tab."


def browser_read_page():
    if not HAS_PYPERCLIP:
        return "Clipboard helper (pyperclip) is not installed."

    _focus_browser()
    time.sleep(0.2)

    try:
        w, h = pyautogui.size()
        pyautogui.click(w // 2, int(h * 0.5))
        time.sleep(0.2)
    except Exception:
        pass

    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "c")
    time.sleep(0.4)
    try:
        text = pyperclip.paste()
    except Exception as e:
        return f"Could not read page: {e}"

    if not text:
        return "The page appears to be empty or the selection failed."

    return text[:20000]


# ============================================================
# FIND ELEMENT (VISION)
# ============================================================

FIND_ELEMENT_PROMPT_TEMPLATE = """
You are looking at a full computer screen screenshot.
The user wants to click or find: "{description}".

Respond ONLY with valid JSON, exactly one of these shapes:

  {{"found": true, "x": <int>, "y": <int>, "reason": "short"}}

or

  {{"found": false, "reason": "why not"}}

Coordinates must be in pixels, in the SAME coordinate space as the
image. Origin (0,0) is the top-left corner of the image.
Be as precise as you can. Do not include any text outside the JSON.
"""


def find_element_on_screen(description, click=False):
    if not VISION_AVAILABLE:
        return (
            "Vision model is not available. "
            f"Run: ollama pull {OLLAMA_VISION_MODEL}"
        )

    path = Path(SCREENSHOT_PATH)
    if not ensure_screenshot_dir(path):
        return f"Could not use screenshot location '{path}'."
    pyautogui.screenshot(str(path))

    prompt = FIND_ELEMENT_PROMPT_TEMPLATE.format(description=description)
    raw = vision_query_ollama(str(path), prompt)

    if raw.startswith("__VISION_ERROR__"):
        return raw

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return f"Vision model returned non-JSON: {raw[:200]}"

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return f"Vision model returned invalid JSON: {raw[:200]}"

    if not data.get("found"):
        return f"Could not find '{description}': {data.get('reason', 'unknown')}"

    try:
        x = int(data["x"])
        y = int(data["y"])
    except Exception:
        return f"Vision model gave invalid coordinates: {data}"

    if click:
        pyautogui.click(x, y)
        return f"Clicked '{description}' at ({x}, {y})."

    return f"Found '{description}' at ({x}, {y})."


# ============================================================
# ACTION EXECUTOR
# ============================================================

def execute_action(action):
    valid, reason = validate_action(action)
    if not valid:
        logger.warning(f"[SECURITY] BLOCKED: {reason}")
        return reason

    try:
        action_type = action["type"]
        parameters = action.get("parameters", {}) or {}

        set_status(last_action=action_type)

        # ----------------------------------------------------
        # APPLICATIONS / URLS
        # ----------------------------------------------------
        if action_type == "open_application":
            application = parameters.get("application")
            if not application:
                return "No application was specified."
            ok, msg = open_application(application)
            logger.info(f"[ACTION] {msg}")
            return msg

        if action_type == "open_url":
            url = parameters.get("url") or ""
            if not url:
                return "No URL was specified."
            if not (url.startswith("https://") or url.startswith("http://")):
                return "That URL was blocked."
            webbrowser.open(url)
            msg = "Opened the website."
            logger.info(f"[ACTION] {msg}")
            return msg

        if action_type == "google_search":
            query = parameters.get("query")
            if not isinstance(query, str) or not query.strip():
                return "No Google search query was specified."
            if len(query) > 500:
                return "That search query was too large."
            url = "https://www.google.com/search?q=" + quote_plus(query.strip())
            webbrowser.open(url)
            msg = f"Searched Google for {query.strip()}."
            logger.info(f"[ACTION] {msg}")
            return msg

        if action_type == "google_first_result":
            query = parameters.get("query")
            if not isinstance(query, str) or not query.strip():
                return "No search query was specified."
            if len(query) > 500:
                return "That search query was too large."

            logger.info(f"[ACTION] Searching for direct link: {query.strip()}")
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                }
                response = requests.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query.strip()},
                    headers=headers,
                    timeout=8
                )
                response.raise_for_status()

                match = re.search(
                    r'href=[\'"]?(?:https?:)?//duckduckgo\.com/l/\?uddg=([^\'">&]+)',
                    response.text
                )
                if match:
                    target_url = unquote(match.group(1))
                    webbrowser.open(target_url)
                    return f"Opened direct link: {target_url}"
                fallback = (
                    "https://www.google.com/search?q="
                    + quote_plus(query.strip())
                )
                webbrowser.open(fallback)
                return "Could not find a direct link, opened search results."
            except Exception as e:
                return f"Could not perform the search: {e}"

        # ----------------------------------------------------
        # BROWSER CONTROLLER
        # ----------------------------------------------------
        if action_type == "browser_navigate":
            url = parameters.get("url", "").strip()
            if not url:
                return "No URL specified for browser_navigate."
            msg = browser_navigate(url)
            logger.info(f"[ACTION] {msg}")
            return msg

        if action_type == "browser_search":
            q = parameters.get("query", "").strip()
            if not q:
                return "No query specified for browser_search."
            msg = browser_search(q)
            logger.info(f"[ACTION] {msg}")
            return msg

        if action_type == "browser_back":
            _focus_browser()
            pyautogui.hotkey("alt", "left")
            return "Went back."

        if action_type == "browser_forward":
            _focus_browser()
            pyautogui.hotkey("alt", "right")
            return "Went forward."

        if action_type == "browser_refresh":
            _focus_browser()
            pyautogui.press("f5")
            return "Refreshed."

        if action_type == "browser_find":
            text = parameters.get("text", "").strip()
            if not text:
                return "No text specified for browser_find."
            _focus_browser()
            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.3)
            pyautogui.write(text, interval=0.01)
            pyautogui.press("enter")
            return f"Searched for '{text}' on the page."

        if action_type == "browser_read_page":
            text = browser_read_page()
            return text

        if action_type == "browser_new_tab":
            url = parameters.get("url")
            msg = browser_new_tab(url)
            logger.info(f"[ACTION] {msg}")
            return msg

        # ----------------------------------------------------
        # VISION
        # ----------------------------------------------------
        if action_type == "screenshot":
            path_str = str(parameters.get("path", "")).strip()
            path = resolve_screenshot_path(path_str)
            if not ensure_screenshot_dir(path):
                return f"Could not use screenshot location '{path}'."
            pyautogui.screenshot(str(path))
            return f"Screenshot saved to {path}."

        if action_type == "vision_query":
            if not VISION_AVAILABLE:
                return (
                    "Vision model is not available. "
                    f"Run: ollama pull {OLLAMA_VISION_MODEL}"
                )
            prompt = parameters.get("prompt", "").strip()
            if not prompt:
                return "No prompt specified for vision_query."
            # Always look at a fresh screenshot of the live screen.
            # (Deliberately ignores any "path" the model might send -
            # letting the model choose an arbitrary filesystem path
            # here previously let a hallucinated/injected path point
            # mkdir + screenshot at protected system locations.)
            path = Path(SCREENSHOT_PATH)
            if not ensure_screenshot_dir(path):
                return f"Could not use screenshot location '{path}'."
            pyautogui.screenshot(str(path))
            result = vision_query_ollama(str(path), prompt)
            if result.startswith("__VISION_ERROR__"):
                return result
            return result[:4000]

        if action_type == "find_element":
            desc = parameters.get("description", "").strip()
            if not desc:
                return "No element description given."
            do_click = bool(parameters.get("click", False))
            return find_element_on_screen(desc, click=do_click)

        # ----------------------------------------------------
        # MOUSE / KEYBOARD
        # ----------------------------------------------------
        if action_type == "mouse_move":
            x = int(parameters.get("x", 0))
            y = int(parameters.get("y", 0))
            pyautogui.moveTo(x, y, duration=0.2)
            return f"Moved the mouse to {x}, {y}."

        if action_type == "mouse_click":
            button = parameters.get("button", "left")
            if button not in ("left", "right", "middle"):
                return "Invalid mouse button."
            clicks = int(parameters.get("clicks", 1))
            if clicks < 1 or clicks > 3:
                return "Invalid click count."
            x = parameters.get("x")
            y = parameters.get("y")
            if x is not None and y is not None:
                pyautogui.click(
                    x=int(x), y=int(y), clicks=clicks, button=button
                )
            else:
                pyautogui.click(clicks=clicks, button=button)
            return "Clicked."

        if action_type == "mouse_double_click":
            x = parameters.get("x")
            y = parameters.get("y")
            if x is not None and y is not None:
                pyautogui.doubleClick(x=int(x), y=int(y))
            else:
                pyautogui.doubleClick()
            return "Double clicked."

        if action_type == "keyboard_type":
            text = parameters.get("text", "")
            if not isinstance(text, str):
                return "Invalid text."
            if len(text) > 5000:
                return "That input was too large."

            if any(ord(c) > 127 for c in text):
                if not HAS_PYPERCLIP:
                    return "Non-ASCII input requires pyperclip."
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            else:
                pyautogui.write(text, interval=0.01)
            return "Typed the requested text."

        if action_type == "keyboard_press":
            key = parameters.get("key")
            if not key:
                return "No key specified."
            allowed_keys = {
                "enter", "esc", "escape", "tab", "space", "backspace",
                "delete", "up", "down", "left", "right",
                "home", "end", "pageup", "pagedown",
                "f1", "f2", "f3", "f4", "f5", "f6",
                "f7", "f8", "f9", "f10", "f11", "f12",
                "volumeup", "volumedown", "volumemute",
                "shift", "ctrl", "alt",
            }
            key = str(key).lower()
            if key not in allowed_keys:
                return f"Keyboard key blocked: {key}"
            pyautogui.press(key)
            return f"Pressed {key}."

        if action_type == "hotkey":
            keys = parameters.get("keys", [])
            if not isinstance(keys, list):
                return "Invalid hotkey."
            if len(keys) < 2 or len(keys) > 4:
                return "Invalid hotkey length."

            allowed_keys = {
                "ctrl", "shift", "alt", "win",
                "a", "c", "v", "x", "z", "y", "s", "f", "l", "t", "w", "n",
                "tab", "enter", "esc",
            }
            normalized = [str(k).lower() for k in keys]
            for k in normalized:
                if k not in allowed_keys:
                    return f"Hotkey blocked because of key: {k}"

            dangerous = {
                ("ctrl", "alt", "delete"),
                ("ctrl", "shift", "esc"),
                ("alt", "f4"),
            }
            if tuple(normalized) in dangerous:
                return "That keyboard shortcut is blocked."

            pyautogui.hotkey(*normalized)
            return "Keyboard shortcut executed."

        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------
        if action_type == "volume_up":
            amount = min(max(int(parameters.get("amount", 2)), 1), 20)
            for _ in range(amount):
                pyautogui.press("volumeup")
            return "Volume increased."

        if action_type == "volume_down":
            amount = min(max(int(parameters.get("amount", 2)), 1), 20)
            for _ in range(amount):
                pyautogui.press("volumedown")
            return "Volume decreased."

        if action_type == "volume_mute":
            pyautogui.press("volumemute")
            return "Volume toggled."

        # ----------------------------------------------------
        # FILE OPERATIONS (WORKSPACE)
        # ----------------------------------------------------
        if action_type == "read_file":
            path_str = str(parameters.get("path", "")).strip()
            if not path_str:
                return "No file specified."
            try:
                path = resolve_workspace_path(path_str, create_parent=False)
            except ValueError as e:
                return str(e)
            if not path.exists():
                return "That file does not exist."
            if not path.is_file():
                return "That is not a file."
            try:
                return path.read_text(encoding="utf-8", errors="replace")[:20000]
            except Exception as e:
                return f"Could not read the file: {e}"

        if action_type == "create_file":
            path_str = str(parameters.get("path", "")).strip()
            content = str(parameters.get("content", ""))
            if not path_str:
                return "No file path specified."
            try:
                path = resolve_workspace_path(path_str, create_parent=True)
            except ValueError as e:
                return str(e)
            try:
                path.write_text(content, encoding="utf-8")
                return f"Created {path}."
            except Exception as e:
                return f"Could not create file: {e}"

        if action_type == "move_file":
            src = str(parameters.get("source", "")).strip()
            dst = str(parameters.get("destination", "")).strip()
            if not src:
                return "No source file specified."
            if not dst:
                return "No destination specified."
            try:
                source = resolve_workspace_path(src, create_parent=False)
                destination = resolve_workspace_path(dst, create_parent=True)
            except ValueError as e:
                return str(e)
            if not source.exists():
                return "The source file does not exist."
            try:
                shutil.move(str(source), str(destination))
                return f"Moved {source} to {destination}."
            except Exception as e:
                return f"Could not move file: {e}"

        if action_type == "copy_file":
            src = str(parameters.get("source", "")).strip()
            dst = str(parameters.get("destination", "")).strip()
            if not src:
                return "No source file specified."
            if not dst:
                return "No destination specified."
            try:
                source = resolve_workspace_path(src, create_parent=False)
                destination = resolve_workspace_path(dst, create_parent=True)
            except ValueError as e:
                return str(e)
            if not source.exists():
                return "The source file does not exist."
            try:
                shutil.copy2(str(source), str(destination))
                return f"Copied {source} to {destination}."
            except Exception as e:
                return f"Could not copy file: {e}"

        if action_type == "list_directory":
            path_str = str(parameters.get("path", ".")).strip() or "."
            try:
                path = resolve_workspace_path(path_str, create_parent=False)
            except ValueError as e:
                return str(e)
            if not path.exists():
                return "That directory does not exist."
            if not path.is_dir():
                return "That is not a directory."
            try:
                entries = [
                    {"name": item.name,
                     "type": "directory" if item.is_dir() else "file"}
                    for item in path.iterdir()
                ]
                return json.dumps(entries[:500])
            except Exception as e:
                return f"Could not list directory: {e}"

        # ----------------------------------------------------
        # MEMORY
        # ----------------------------------------------------
        if action_type == "memory_save":
            key = str(parameters.get("key", "")).strip()
            value = str(parameters.get("value", "")).strip()
            if not key:
                return "No memory key specified."
            ok = memory_save_fact(key, value)
            return f"Saved memory: {key} = {value}" if ok else "Could not save memory."

        if action_type == "memory_forget":
            key = str(parameters.get("key", "")).strip()
            if not key:
                return "No memory key specified."
            ok = memory_forget_fact(key)
            return f"Forgot '{key}'." if ok else f"No memory stored for '{key}'."

        if action_type == "memory_list":
            mem = load_memory()
            if not mem.get("facts"):
                return "No memories stored yet."
            return json.dumps(mem["facts"], indent=2, ensure_ascii=False)

        # ----------------------------------------------------
        # WAIT
        # ----------------------------------------------------
        if action_type == "wait":
            seconds = min(max(float(parameters.get("seconds", 1)), 0), 10)
            time.sleep(seconds)
            return "Waited."

        return "Action was not executed."

    except Exception as e:
        logger.error(f"[ACTION ERROR] {e}\n{traceback.format_exc()}")
        set_status(last_error=str(e))
        return f"Action failed: {e}"


# ============================================================
# VERIFICATION (OPTIONAL)
# ============================================================

def verify_last_action(goal_description):
    if not VISION_AVAILABLE:
        return None

    path = Path(SCREENSHOT_PATH)
    if not ensure_screenshot_dir(path):
        return None
    pyautogui.screenshot(str(path))

    prompt = (
        "Looking at this screenshot, did the following goal succeed?\n\n"
        f"Goal: {goal_description}\n\n"
        "Respond ONLY with JSON: "
        '{"success": true|false, "reason": "short explanation"}'
    )
    raw = vision_query_ollama(str(path), prompt)
    if raw.startswith("__VISION_ERROR__"):
        return None

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ============================================================
# EXECUTE ACTION PLAN
# ============================================================

FAILURE_HINTS = (
    "could not", "couldn't", "cannot", "can't", "blocked",
    "does not exist", "doesn't exist", "not available", "not found",
    "invalid", "no application", "no url", "no query", "no text",
    "no file", "no memory key", "no element", "no source",
    "no destination", "action failed", "not executed",
    "unauthorized", "protected", "too large", "too small",
)


def _looks_like_failure(result_text):
    if not isinstance(result_text, str) or not result_text:
        return False
    low = result_text.lower()
    return any(hint in low for hint in FAILURE_HINTS)


def execute_action_plan(plan, verify=False):
    if not isinstance(plan, dict):
        return "I received an invalid action plan."

    speech = plan.get("speech", "Done.")
    actions = plan.get("actions", [])

    if not isinstance(actions, list):
        logger.warning("[SECURITY] Invalid actions list.")
        return speech

    logger.info("IVATRON ACTION PLAN")
    for a in actions:
        try:
            logger.info(json.dumps(a, ensure_ascii=False))
        except Exception:
            logger.info(str(a))

    results = []
    for action in actions:
        result = execute_action(action)
        results.append(result)

    # The model writes "speech" before any action actually runs, so on
    # its own it can't know whether an action succeeded. Check the real
    # results and be honest about it instead of always reporting success.
    failures = [r for r in results if _looks_like_failure(r)]
    if failures:
        speech = speech.rstrip(".") + ". " + failures[0]

    if verify and actions and VISION_AVAILABLE:
        last = actions[-1]
        if isinstance(last, dict):
            goal = f"Action '{last.get('type')}' with parameters {last.get('parameters', {})}"
            v = verify_last_action(goal)
            if v and not v.get("success", True):
                logger.info(f"Verification failed: {v}")
                speech = (
                    speech
                    + " However, the screen doesn't seem to confirm it worked."
                )

    return speech


# ============================================================
# PIPER TTS
# ============================================================

def speak_to_wav(text, out_path, language="en"):
    if language == "uk" and PIPER_UK_MODEL and Path(PIPER_UK_MODEL).exists():
        model = PIPER_UK_MODEL
        speaker = PIPER_UK_SPEAKER
    else:
        if language not in ("en", "uk"):
            logger.info(f"[TTS] No voice for '{language}', using English.")
        model = PIPER_EN_MODEL
        speaker = PIPER_EN_SPEAKER

    if not model or not Path(model).exists():
        raise FileNotFoundError(f"Piper voice model not found: {model}")

    command = ["piper", "--model", model, "--output_file", out_path]
    if speaker not in (None, ""):
        command += ["--speaker", str(speaker)]

    subprocess.run(
        command,
        input=text.encode("utf-8"),
        check=True
    )


# ============================================================
# HTTP SERVER (files + dashboard)
# ============================================================

class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def get_dashboard_html():
    with STATUS_LOCK:
        s = dict(STATUS)

    mem = load_memory()
    facts = mem.get("facts", {})
    memory_html = "".join(
        f"<li><b>{k}</b>: {v}</li>" for k, v in facts.items()
    ) or "<li><i>empty</i></li>"

    def badge(v):
        v = str(v).lower()
        if "ready" in v or v in ("connected", "online"):
            return f'<span class="ok">{v}</span>'
        if "unknown" in v or "idle" in v or "starting" in v or "will" in v:
            return f'<span class="warn">{v}</span>'
        return f'<span class="err">{v}</span>'

    timing_labels = {
        "speech_detection": "Speech detection",
        "transcription": "Transcription",
        "ollama": "Ollama",
        "actions": "Actions",
        "piper": "Piper",
        "lenovo": "Lenovo",
    }
    timings_html = "".join(
        f"<li>{label}: {s['last_timings'].get(key, 0):.2f}s</li>"
        for key, label in timing_labels.items()
    ) or "<li><i>no turns yet</i></li>"
    total_time_html = (
        f"{s['last_total_time']:.2f}s" if s.get("last_total_time") is not None
        else "<i>none</i>"
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>I.V.A.T.R.O.N</title>
<meta http-equiv="refresh" content="2">
<style>
body {{ background:#0b0f17; color:#cfe3ff; font-family:Consolas,monospace;
        padding:24px; }}
h1 {{ color:#5ac8fa; }}
table {{ border-collapse:collapse; margin: 12px 0; }}
td {{ padding: 4px 14px; border-bottom:1px solid #1c2634; }}
.ok {{ color:#4ade80; }}
.warn {{ color:#facc15; }}
.err {{ color:#ef4444; }}
.box {{ background:#111827; border:1px solid #1f2937;
        border-radius:8px; padding:16px; margin:12px 0; max-width:760px; }}
</style>
</head>
<body>
<h1>I.V.A.T.R.O.N</h1>
<div class="box">
<table>
<tr><td>Status</td><td>{badge(s['state'])}</td></tr>
<tr><td>Ollama</td><td>{badge(s['ollama'])}</td></tr>
<tr><td>Vision</td><td>{badge(s['vision'])}</td></tr>
<tr><td>Whisper</td><td>{badge(s['whisper'])}</td></tr>
<tr><td>Piper EN</td><td>{badge(s['piper_en'])}</td></tr>
<tr><td>Piper UK</td><td>{badge(s['piper_uk'])}</td></tr>
<tr><td>Lenovo</td><td>{badge(s['lenovo'])}</td></tr>
<tr><td>Microphone</td><td>{badge(s['microphone'])}</td></tr>
<tr><td>Started</td><td>{s['started']}</td></tr>
</table>
</div>

<div class="box">
<h3>Last command</h3>
<div>{s['last_command'] or '<i>none</i>'}</div>
<h3>Last response</h3>
<div>{s['last_response'] or '<i>none</i>'}</div>
<h3>Last action</h3>
<div>{s['last_action'] or '<i>none</i>'}</div>
<h3>Last error</h3>
<div>{s['last_error'] or '<i>none</i>'}</div>
</div>

<div class="box">
<h3>Last turn performance (total: {total_time_html})</h3>
<ul>{timings_html}</ul>
</div>

<div class="box">
<h3>Memory</h3>
<ul>{memory_html}</ul>
</div>
</body>
</html>"""


class IvatronHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/dashboard", "/dashboard/"):
            body = get_dashboard_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, fmt, *args):
        pass


def serve_directory(directory, port):
    handler = partial(IvatronHTTPRequestHandler, directory=directory)
    httpd = ReusableTCPServer(("", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# ============================================================
# CONNECT TO LENOVO (LAZY, NO PING)
# ============================================================

def connect_cast(attempts=None, timeout=None, retry_wait=None):
    """
    Try to connect to the clock. Returns a Cast object, or None if
    the clock is unreachable.

    This is now called *lazily* — only when we actually need to play
    audio through the clock — not at startup.
    """
    if attempts is None:
        attempts = CAST_CONNECT_ATTEMPTS
    if timeout is None:
        timeout = CAST_CONNECT_TIMEOUT
    if retry_wait is None:
        retry_wait = CAST_RETRY_WAIT

    host = (CLOCK_IP, 8009, CLOCK_UUID, None, None)

    for attempt in range(1, attempts + 1):
        try:
            logger.info(
                f"Connecting to Lenovo at {CLOCK_IP}:8009 "
                f"(attempt {attempt}/{attempts}, timeout {timeout}s)..."
            )

            try:
                cast = pychromecast.get_chromecast_from_host(
                    host,
                    tries=1,
                    retry_wait=retry_wait,
                    timeout=timeout,
                )
            except TypeError:
                # Older pychromecast builds don't accept those kwargs.
                cast = pychromecast.get_chromecast_from_host(host)

            cast.wait(timeout=timeout)

            logger.info("Connected to Lenovo.")
            set_status(lenovo="connected")
            return cast

        except Exception as e:
            logger.warning(f"Cast connect attempt {attempt} failed: {e}")
            if attempt < attempts:
                time.sleep(retry_wait)

    logger.warning(
        f"Lenovo at {CLOCK_IP}:8009 unreachable. "
        f"Audio will play on PC speakers instead."
    )
    set_status(lenovo="unreachable")
    return None


def is_cast_connected(cast):
    try:
        return cast is not None and cast.socket_client.is_connected
    except Exception:
        return False


def ensure_cast_ready(cast, timeout=None):
    if is_cast_connected(cast):
        return cast
    if cast is None:
        return connect_cast(attempts=1, timeout=CAST_CONNECT_TIMEOUT)
    try:
        cast.disconnect(timeout=2)
    except Exception:
        pass
    set_status(lenovo="reconnecting")
    return connect_cast(attempts=1, timeout=CAST_CONNECT_TIMEOUT)


def safe_play_media(cast, url, content_type="audio/wav"):
    """
    Play media on the cast.

    - If we don't have a cast yet, try ONE lazy connect.
    - If playing raises NotConnected, try to reconnect once and retry.
    - Raises if it still can't play, so the caller can fall back
      to local (PC speaker) playback.
    """
    # Lazily try to acquire a cast the first time we need one.
    if cast is None:
        cast = connect_cast(attempts=1, timeout=CAST_CONNECT_TIMEOUT)
        if cast is None:
            raise RuntimeError("Chromecast unreachable")

    last_exc = None
    for attempt in (1, 2):
        try:
            if not is_cast_connected(cast):
                # One-shot reconnect; short timeout so we don't hang.
                cast = connect_cast(attempts=1, timeout=CAST_CONNECT_TIMEOUT)
                if cast is None:
                    raise RuntimeError("Chromecast unreachable")

            cast.media_controller.play_media(url, content_type)
            cast.media_controller.block_until_active(timeout=15)
            return cast

        except pychromecast.error.NotConnected as e:
            last_exc = e
            logger.warning(f"[CAST] NotConnected (attempt {attempt}): {e}")
            cast = None  # force full reconnect on next loop

        except Exception as e:
            last_exc = e
            logger.error(f"[CAST] play_media failed (attempt {attempt}): {e}")
            if attempt == 2:
                raise
            time.sleep(1)

    if last_exc is not None:
        raise last_exc
    return cast


def play_audio_locally(wav_path):
    """
    Fallback audio playback on the PC when no cast is available.
    Uses winsound on Windows (no extra deps).
    """
    try:
        import winsound
        winsound.PlaySound(wav_path, winsound.SND_FILENAME)
        return True
    except Exception as e:
        logger.error(f"Local playback failed: {e}")
        return False


# ============================================================
# LANGUAGE HELPERS
# ============================================================

def detect_language_command(text):
    t = text.lower().strip()

    uk_triggers = [
        "speak ukrainian", "reply in ukrainian", "answer in ukrainian",
        "говори українською", "відповідай українською",
        "українською", "українська мова",
    ]
    en_triggers = [
        "speak english", "reply in english", "answer in english",
        "говори англійською", "відповідай англійською",
        "english please", "switch to english",
    ]

    for trig in uk_triggers:
        if trig in t:
            LANGUAGE_OVERRIDE["value"] = "uk"
            return "uk"
    for trig in en_triggers:
        if trig in t:
            LANGUAGE_OVERRIDE["value"] = "en"
            return "en"
    return None


# ============================================================
# HANDLE TURN
# ============================================================

def handle_turn(cast):
    logger.info("=" * 50)
    logger.info("LISTENING")
    logger.info("=" * 50)

    timings = {}
    t_turn_start = time.perf_counter()

    t0 = time.perf_counter()
    audio = wait_for_speech_then_record()
    timings["speech_detection"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    question, detected_language = transcribe_audio(audio)
    timings["transcription"] = time.perf_counter() - t0

    logger.info(f"You said: {question}")
    logger.info(f"Detected language: {detected_language}")
    set_status(last_command=question)

    if not question:
        logger.info("Didn't catch anything.")
        return cast

    switch = detect_language_command(question)
    effective_language = switch or LANGUAGE_OVERRIDE["value"] or detected_language

    language_name = {
        "uk": "Ukrainian",
        "en": "English",
    }.get(effective_language, effective_language)

    prompt = (
        f"Detected spoken language: {language_name} ({effective_language}). "
        f"Respond in {language_name} unless the user explicitly asks for "
        f"a different response language. "
        f"User request: {question}"
    )

    t0 = time.perf_counter()
    plan = ask_llm(prompt)
    timings["ollama"] = time.perf_counter() - t0

    speech = plan.get("speech", "")
    logger.info(f"IVATRON: {speech}")
    set_status(last_response=speech)

    t0 = time.perf_counter()
    speech = execute_action_plan(plan, verify=USE_VERIFICATION)
    timings["actions"] = time.perf_counter() - t0

    if not speech:
        speech = "Done."

    tts_language = effective_language if effective_language in ("en", "uk") else "en"

    t0 = time.perf_counter()
    try:
        speak_to_wav(speech, RESPONSE_WAV, language=tts_language)
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return cast
    timings["piper"] = time.perf_counter() - t0

    url = f"http://{MY_IP}:{HTTP_PORT}/response.wav"

    t0 = time.perf_counter()
    if cast is not None:
        try:
            cast = safe_play_media(cast, url, "audio/wav")
        except Exception as e:
            logger.error(f"Could not play response through Lenovo: {e}")
            set_status(last_error=f"cast: {e}")
            play_audio_locally(RESPONSE_WAV)
    else:
        try:
            cast = safe_play_media(None, url, "audio/wav")
        except Exception as e:
            logger.warning(
                f"Clock unavailable ({e}); playing on PC speakers."
            )
            play_audio_locally(RESPONSE_WAV)
    timings["lenovo"] = time.perf_counter() - t0

    total = time.perf_counter() - t_turn_start

    summary_lines = ["IVATRON PERFORMANCE"]
    labels = {
        "speech_detection": "Speech detection",
        "transcription": "Transcription",
        "ollama": "Ollama",
        "actions": "Actions",
        "piper": "Piper",
        "lenovo": "Lenovo",
    }
    for key, label in labels.items():
        summary_lines.append(f"  {label:<18} {timings.get(key, 0):.2f}s")
    summary_lines.append(f"  {'TOTAL':<18} {total:.2f}s")
    summary_text = "\n".join(summary_lines)
    logger.info(summary_text)
    set_status(last_timings=timings, last_total_time=round(total, 2))

    time.sleep(1)
    return cast


# ============================================================
# MAIN
# ============================================================

def main():
    httpd = None

    _init_wake_word()

    if ollama_available():
        set_status(ollama="connected")
        logger.info("Ollama: CONNECTED")

        check_vision_model()
        if USE_VERIFICATION and not VISION_AVAILABLE:
            logger.warning(
                "USE_VERIFICATION=true but vision model is missing. "
                "Verification will be skipped this session."
            )
    else:
        set_status(ollama="unreachable")
        logger.warning("Ollama: UNREACHABLE")

    # Show the resolved config so a stale .env is obvious in the log.
    logger.info(f"Config: CLOCK_IP={CLOCK_IP}  MY_IP={MY_IP}  PORT={HTTP_PORT}")

    try:
        # Do NOT connect to the clock here.
        # We connect lazily the first time we actually need to play audio.
        cast = None

        httpd = serve_directory(r"E:\jarvis", HTTP_PORT)
        logger.info(f"HTTP server on port {HTTP_PORT}")
        logger.info(f"Dashboard: http://{MY_IP}:{HTTP_PORT}/dashboard")

        set_status(state="online")

        print()
        print("=" * 50)
        print("          IVATRON IS ONLINE")
        print("=" * 50)
        print()
        print(f"Dashboard:  http://localhost:{HTTP_PORT}/dashboard")
        print(f"Wake word:  {'ENABLED' if USE_WAKE_WORD else 'disabled'}")
        print(f"VAD:        {'ENABLED' if (USE_VAD and HAS_WEBRTCVAD) else 'disabled'}")
        print(f"Verify:     {'ENABLED' if (USE_VERIFICATION and VISION_AVAILABLE) else 'disabled'}")
        print(f"Vision:     {'available' if VISION_AVAILABLE else 'NOT available'}")
        print(f"Lenovo:     will connect on first audio output")
        print(f"Workspace:  {WORKSPACE_ROOT}")
        print("Press Ctrl+C to stop.")
        print()

        while True:
            try:
                cast = handle_turn(cast)
            except Exception as e:
                logger.error(f"[TURN ERROR] {e}\n{traceback.format_exc()}")
                set_status(last_error=str(e))
                time.sleep(2)

    except KeyboardInterrupt:
        logger.info("Shutting down Ivatron...")

    finally:
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
        set_status(state="offline")
        logger.info("Server closed cleanly.")


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()