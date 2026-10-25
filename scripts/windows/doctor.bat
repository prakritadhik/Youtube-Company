@echo off
REM Prints what this computer has (OS, RAM, GPU, FFmpeg, free disk) and whether the database is healthy.
cd /d "%~dp0\..\..\backend"
".venv\Scripts\python.exe" -m app.cli doctor
pause
