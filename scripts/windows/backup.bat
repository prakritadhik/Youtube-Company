@echo off
REM Creates a verified database backup in data\backups. Safe to run while the app is running.
cd /d "%~dp0\..\..\backend"
".venv\Scripts\python.exe" -m app.cli backup
pause
