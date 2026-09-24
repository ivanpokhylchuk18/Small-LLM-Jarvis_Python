IVATRON - Flet UI upgrade

FILES
-----
jarvis.py            Main IVATRON application with the new desktop UI.
start_jarvis.bat     Starts IVATRON using the project's .venv.
install_startup.bat  Adds IVATRON to the Windows Startup folder.

RUN
---
From E:\jarvis:

    .venv\Scripts\python.exe jarvis.py

or double-click:

    start_jarvis.bat

AUTO-START
----------
Double-click install_startup.bat once.
After that, IVATRON starts when you sign into Windows.

AUDIO
-----
The Lenovo Smart Clock remains a lazy Chromecast output.
When it is reachable, responses use the clock.
If it cannot be reached or disconnects, responses automatically use
the Windows PC speakers.

IMPORTANT
---------
Keep your existing .env, .gitignore, memory.json, workspace, logs,
Piper models, Ollama setup, and other existing files.

The new jarvis.py keeps the existing backend/action safety model and
adds a native Flet desktop interface around it.

FLET
----
If needed:

    .venv\Scripts\python.exe -m pip install --upgrade flet

The current Flet 1.x entry point is ft.run(main).
