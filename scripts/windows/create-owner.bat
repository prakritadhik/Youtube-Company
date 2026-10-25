@echo off
REM Creates the owner account from the command line (the dashboard can also do this on first run).
cd /d "%~dp0\..\..\backend"
".venv\Scripts\python.exe" -m app.cli create-owner
pause
