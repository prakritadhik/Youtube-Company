@echo off
REM Sets a new password for the owner (signs out every session). Use if you forgot it.
cd /d "%~dp0\..\..\backend"
".venv\Scripts\python.exe" -m app.cli reset-password --username owner
pause
