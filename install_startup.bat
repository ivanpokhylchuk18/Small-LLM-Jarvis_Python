@echo off
setlocal
cd /d "%~dp0"

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET=%~dp0start_jarvis.bat"
set "SHORTCUT=%STARTUP%\IVATRON.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$W=New-Object -ComObject WScript.Shell; $S=$W.CreateShortcut('%SHORTCUT%'); $S.TargetPath='%TARGET%'; $S.WorkingDirectory='%~dp0'; $S.Description='Start IVATRON'; $S.Save()"

echo.
echo IVATRON startup shortcut installed:
echo %SHORTCUT%
echo.
echo IVATRON will start automatically when you sign into Windows.
pause
endlocal
