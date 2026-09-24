# JARVIS — Local AI Desktop Assistant

A local, voice-controlled AI assistant built with Python, Ollama, Whisper, Piper TTS, and PyAutoGUI.

JARVIS is designed to understand natural-language voice commands and translate them into controlled actions on a Windows computer. It can launch applications, interact with the desktop, navigate the web, perform browser searches, read and create files, control system volume, and respond using synthesized speech.

The project also supports English and Ukrainian voice interaction and can send generated audio to a Lenovo Smart Clock over the local network.

---

## Features

### Voice Interaction

- Voice input through a computer microphone
- Speech recognition using Faster-Whisper
- Automatic language detection
- English voice commands
- Ukrainian voice commands
- Text-to-speech responses using Piper

### Local AI

- Uses Ollama for local LLM inference
- Currently configured for `llama3.1:8b`
- Natural-language command interpretation
- Converts user requests into structured JSON actions
- Conversation history for contextual responses

### Windows Computer Control

JARVIS can perform controlled computer actions such as:

- Launch applications
- Open websites
- Move the mouse
- Click
- Double-click
- Type text
- Press keyboard keys
- Use keyboard shortcuts
- Increase volume
- Decrease volume
- Mute/unmute volume
- Take screenshots
- Read files
- Create files
- Move files
- Copy files
- List directories
- Wait for a specified amount of time

### Browser Automation

JARVIS can interact with the web without relying exclusively on screen coordinates.

Examples:

- Open Google
- Open Opera GX
- Search Google
- Search for a topic and open the first result
- Navigate directly to websites
- Use deterministic URLs for web searches

This allows commands such as:

> "Open Opera GX."

> "Search Google for University of Evansville."

> "Find University of Evansville and open the first result."

### Smart Speaker Output

JARVIS can send generated speech to a Lenovo Smart Clock over the local network.

The PC runs a small HTTP server that hosts the generated WAV file, allowing the Smart Clock to retrieve and play the audio.

### Safety Controls

JARVIS uses an explicit action allow-list.

The AI cannot directly execute arbitrary commands on the computer.

Potentially dangerous operations are blocked, including:

- Arbitrary CMD commands
- PowerShell execution
- Arbitrary shell commands
- File deletion
- Folder deletion
- Disk formatting
- Registry modification
- Administrator commands
- Software installation/uninstallation
- Firewall modification
- Windows Defender/antivirus modification
- Process termination
- Boot configuration changes
- Shutdown/restart commands
- Password modification
- Accessing credentials
- Accessing browser cookies
- Accessing authentication tokens

The goal is to allow useful computer automation while keeping the execution layer under explicit Python control.

---

# Architecture

JARVIS follows a pipeline similar to:

                 ┌─────────────────┐
                 │    Microphone   │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │ Faster-Whisper  │
                 │ Speech-to-Text  │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │     Ollama      │
                 │  Local LLM      │
                 └────────┬────────┘
                          │
                 Structured JSON
                          │
                          ▼
                 ┌─────────────────┐
                 │ Python Action   │
                 │    Executor     │
                 └────────┬────────┘
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
         Windows       Browser      Files
         Control      Navigation   Operations
             │            │            │
             └────────────┼────────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │    Piper TTS    │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │ Lenovo Smart    │
                 │     Clock       │
                 └─────────────────┘

  
  Technologies
  Technology	Purpose
  Python	Core application
  Ollama	Local LLM reasoning
  Llama 3.1 8B	Current language model
  Faster-Whisper	Speech recognition
  Piper	Text-to-speech
  PyAutoGUI	Mouse and keyboard automation
  PyChromecast	Smart Clock communication
  SoundDevice	Microphone recording
  NumPy	Audio processing
  Requests	HTTP communication
  python-dotenv	Local configuration
  HTTP Server	Local audio hosting
  Requirements
  Hardware

Recommended:
  Windows PC
  NVIDIA GPU for faster Whisper inference
  Microphone
  Local network connection

Optional:
  Lenovo Smart Clock
  Other Google Cast-compatible audio device
  A dedicated NVIDIA GPU is not strictly required, but GPU acceleration significantly improves speech recognition performance.

Software Requirements
Python
  Python 3.10 or newer is recommended.
  Check your Python version:
  python --version

Ollama
  JARVIS uses Ollama to run the language model locally.
  Install Ollama, then download the model:
  ollama pull llama3.1:8b
  Make sure Ollama is running before starting JARVIS.

Piper
Piper is used for text-to-speech.

JARVIS currently uses:
  English Piper voice
  Ukrainian Piper voice
The voice models are intentionally not included in this repository because they are large model files.

Installation
1. Clone the repository
  git clone https://github.com/YOUR_USERNAME/jarvis-local-ai-assistant.git
  cd jarvis-local-ai-assistant
  Replace YOUR_USERNAME with your GitHub username.
2. Create a virtual environment
  On Windows:
  python -m venv .venv
  Activate it:
  .venv\Scripts\activate
3. Install Python dependencies
  pip install requests pychromecast pyautogui sounddevice numpy faster-whisper python-dotenv
  Configuration

JARVIS uses a .env file for machine-specific configuration.

Create a file named:
.env
Do not commit this file to GitHub!!!
Use .env.example as a template.

Example:
  MY_IP=
  CLOCK_IP=
  CLOCK_UUID=
  OLLAMA_MODEL=llama3.1:8b
  PIPER_EN_MODEL=
  PIPER_UK_MODEL=
  RECORDING_WAV=
  RESPONSE_WAV=
  HTTP_PORT=8080



Configuration variables
Variable	Description

MY_IP	- Local IP address of the computer running JARVIS

CLOCK_IP -	IP address of the Lenovo Smart Clock

CLOCK_UUID -	Chromecast UUID of the Smart Clock

OLLAMA_MODEL -	Ollama model used for reasoning

PIPER_EN_MODEL -	Path to English Piper voice

PIPER_UK_MODEL -	Path to Ukrainian Piper voice

RECORDING_WAV -	Temporary microphone recording path

RESPONSE_WAV -	Generated speech WAV path

HTTP_PORT -	Local HTTP server port



Piper Voice Models
JARVIS expects the Piper voice models to exist locally.

  Example:
  E:\piper-voices\
  ├── en_US-lessac-medium.onnx
  ├── en_US-lessac-medium.onnx.json
  ├── uk_UA-ukrainian_tts-medium.onnx
  └── uk_UA-ukrainian_tts-medium.onnx.json



The models should not be committed to this repository.


Running JARVIS
After configuring the environment:
  python jarvis.py

JARVIS will:
Initialize Whisper
    Connect to Ollama
    Start the local HTTP server
    Connect to the Lenovo Smart Clock
    Listen for voice input
    Transcribe the request
    Send the request to Ollama
    Validate the generated actions
    Execute the allowed actions
    Generate a spoken response
    Send the response audio to the Smart Clock


Example Commands
Applications
  "Open Chrome."
  "Open Opera GX."
  "Open Spotify."
  "Open Visual Studio Code."
  "Open Discord."
Websites
  "Open Google."
  "Open YouTube."
  "Open GitHub."
Searching
  "Search Google for University of Evansville."
  "Search for the latest Python documentation."
  "Find the website and open it."
Desktop interaction
  "Move the mouse to the center of the screen."
  "Click here."
  "Type hello world."
  "Press Enter."
  "Press Control C."
Files
  "Read this file."
  "Create a file."
  "Copy this file."
  "Move this file."
  "List the files in this folder."
Volume
  "Turn the volume up."
  "Turn the volume down."
  "Mute the computer."
  

English and Ukrainian
  JARVIS supports multilingual speech recognition.
  Faster-Whisper automatically detects the spoken language.
  For Ukrainian speech, JARVIS can use the Ukrainian Piper voice for its response.

Example:
    User:
    "Привіт, Джарвіс, відкрий Google."
    JARVIS
    "Звичайно. Відкриваю Google."
    English:
    User:
    "Open Google."
    JARVIS:
    "Sure. Opening Google."

Security Model
The AI model does not receive unrestricted access to the computer.
Instead, Ollama produces a structured action plan:

{
    "speech": "Opening Google.",
    "actions": [
        {
            "type": "open_url",
            "parameters": {
                "url": "https://www.google.com"
            }
        }
    ]
}

Python then validates the requested action.
Only actions explicitly included in the allow-list can be executed.
This creates a separation between:
      AI reasoning
            ↓
      Structured actions
            ↓
      Python validation
            ↓
      Computer execution

The AI can decide what it wants to do, but Python decides whether that action is permitted to execute.

Project Structure
A typical installation looks like:
    JARVIS/
    │
    ├── jarvis.py
    ├── README.md
    ├── .gitignore
    ├── .env.example
    ├── .env
    │
    └── .venv/

The following files/directories should remain local and should not be committed:
  .env
  .venv/
  *.wav
  *.onnx
  *.onnx.json


Why Local AI?
One of the main goals of JARVIS is to experiment with a computer assistant that keeps its core AI processing on the local machine.
Instead of:
      Microphone
          ↓
      Cloud AI
          ↓
      Cloud response
          ↓
      Computer
      
      JARVIS is designed around:
      
      Microphone
          ↓
      Local Whisper
          ↓
      Local Ollama
          ↓
      Python
          ↓
      Computer

This provides more control over the system and makes the project useful as an experiment in local AI agents and computer automation.
Current Limitations
JARVIS is still an active project.

Current limitations include:
    Speech recognition accuracy can vary depending on microphone quality.
    Ukrainian and Russian speech can sometimes be difficult for automatic language detection.
    Browser automation can depend on the installed browser.
    Some applications use Microsoft Store packaging and may require Start Menu shortcut detection.
    Piper voice models must be installed separately.
    The Lenovo Smart Clock must be reachable on the same local network.
    Computer vision-based UI understanding is not yet fully implemented.
    The system currently uses an explicit action allow-list rather than unrestricted computer control.
    Roadmap

Planned improvements include:
 More reliable Ukrainian language detection
 Better browser automation
 Computer vision for UI understanding
 Screenshot → vision model → action pipeline
 Automatic UI element detection
 Better application discovery
 More natural conversational memory
 Improved voice activity detection
 More TTS voices
 Better error recovery
 More Windows automation capabilities
 Plugin/action architecture
 Smarter task planning
 Multi-step task execution
 Action verification after execution
Vision-Based Automation

A future version of JARVIS is intended to use computer vision to understand the current screen.

The planned architecture is:
      Screenshot
          ↓
      Vision Model
          ↓
      Understand UI
          ↓
      Identify target
          ↓
      Generate coordinates/action
          ↓
      Execute
          ↓
      Take another screenshot
          ↓
      Verify result
This would allow JARVIS to interact with unfamiliar interfaces instead of relying entirely on hardcoded coordinates.

Contributing:
Contributions, ideas, and improvements are welcome.

If you find a bug:
    Check whether it has already been reported.
    Open an issue with steps to reproduce it.
    Include relevant error messages.
    Avoid posting private configuration such as IP addresses, credentials, or device identifiers.
    For larger changes, open an issue first so the approach can be discussed.

Disclaimer
JARVIS is an experimental personal AI automation project.
Computer automation can have unintended consequences. Always review the actions and permissions granted to the system before using it on important files or systems.
The project intentionally restricts potentially destructive or security-sensitive operations.


Author:
Ivan Pokhylchuk

Built as a personal project exploring:
    Local AI
    Voice interfaces
    Computer automation
    LLM agents
    Speech recognition
    Text-to-speech
    Human-computer interaction
    Smart-device integration


⭐ Project Goal

The long-term goal of JARVIS is to create a practical local AI assistant that can understand natural language, reason about tasks, interact with a computer, verify its actions, and communicate naturally through voice.
