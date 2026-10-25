<#
 One-time setup for Windows 10/11. Run from the repository root in PowerShell:
     powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
 NOTE: written on Linux and NOT yet tested on Windows. If a step fails, the message says which one.
#>
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Write-Error "$cmd was not found. $hint"; exit 1 }
}

Write-Host "==> Checking prerequisites"
$py = $null
foreach ($c in @("py -3.13","py -3.12","py -3.11","python")) {
  try { $v = & cmd /c "$c -c ""import sys;print(sys.version_info[:2]>=(3,11))"" 2>nul"; if ($v -eq "True") { $py = $c; break } } catch {}
}
if (-not $py) { Write-Error "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH')."; exit 1 }
Need "node" "Install Node.js LTS from https://nodejs.org"
Need "npm" "Install Node.js LTS from https://nodejs.org"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Warning "FFmpeg not found. The dashboard works without it, but video rendering (Phase 2) needs it: winget install Gyan.FFmpeg"
}

Write-Host "==> Creating the Python environment (backend\.venv)"
& cmd /c "$py -m venv backend\.venv"
& backend\.venv\Scripts\python.exe -m pip install --upgrade pip
& backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"

Write-Host "==> Installing dashboard dependencies"
Push-Location frontend; npm install --no-audit --no-fund; Pop-Location

if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "==> Created .env from .env.example (edit it later to add provider keys)" }

Write-Host "==> Creating the database"
Push-Location backend; & .venv\Scripts\python.exe -m app.cli setup; Pop-Location

Write-Host "==> Building the dashboard"
Push-Location frontend; npm run build; Pop-Location

Write-Host ""
Write-Host "Setup finished. Start the platform with:  scripts\windows\start.bat"
Write-Host "Then open http://localhost:3000 and create your owner account (first run only)."
