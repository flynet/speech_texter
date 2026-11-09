@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYTHONUTF8=1"
echo ============================================================
echo  VoiceType - debug run from source
echo  Logs: %LOCALAPPDATA%\VoiceType\logs\app.log
echo  Close this window or press Ctrl+C to stop the app.
echo ============================================================
echo.
".venv\Scripts\python.exe" -m voicetype
set "CODE=%ERRORLEVEL%"
echo.
echo VoiceType exited with code %CODE%.
pause
