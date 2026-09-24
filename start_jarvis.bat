@echo off
setlocal
cd /d "%~dp0"
set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
set "SCRIPT=%~dp0jarvis.py"

if not exist "%PYTHONW%" (
    echo IVATRON virtual environment was not found.
    echo Expected: %PYTHONW%
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo IVATRON jarvis.py was not found at:
    echo %SCRIPT%
    pause
    exit /b 1
)

start "IVATRON" /D "%~dp0" "%PYTHONW%" "%SCRIPT%"
exit /b 0
