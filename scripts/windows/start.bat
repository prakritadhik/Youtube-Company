@echo off
REM Starts the API, the worker and the dashboard. Press Ctrl+C in this window to stop everything.
REM Pass --dev for hot-reload dashboard development, or --check to verify prerequisites only.
cd /d "%~dp0\..\.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" launcher.py %*
) else (
  py -3 launcher.py %*
)
pause
