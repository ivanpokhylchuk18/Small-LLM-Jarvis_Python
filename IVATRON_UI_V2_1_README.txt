IVATRON UI V2.1

Changes:
- Real animated/pulsing IVATRON core.
- Animation speed/state changes for listening, thinking, executing and speaking.
- Hidden Windows subprocesses for nvidia-smi and Piper so CMD windows do not flash.
- start_jarvis.bat remains the normal launcher.
- launch_jarvis.vbs launches the BAT completely hidden (optional).

Install:
1. Replace jarvis.py, start_jarvis.bat, and optionally launch_jarvis.vbs in E:\jarvis.
2. Keep your .env, .gitignore, memory.json, workspace, and logs.
3. Use the existing .venv.
4. Run: .venv\\Scripts\\python.exe -m pip install -r requirements-ui.txt
5. Double-click start_jarvis.bat, or launch_jarvis.vbs for zero CMD window.

Closing start_jarvis.bat after launch does NOT shut down IVATRON. The BAT starts pythonw.exe as a separate process and then exits. To stop IVATRON, close the IVATRON window (or terminate pythonw.exe from Task Manager).


V2.1 HOTFIX
- Fixed startup NameError caused by os being referenced before import.
- Fixed launch_jarvis.vbs so it works regardless of Windows current directory.
- start_jarvis.bat uses the venv next to this file.
