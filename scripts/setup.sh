#!/usr/bin/env bash
# One-time setup for Linux/macOS (also used to verify the install path in CI-like conditions).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Python 3.11+ is required"; exit 1; }
command -v node >/dev/null || { echo "Node.js 20+ is required"; exit 1; }
command -v ffmpeg >/dev/null || echo "note: FFmpeg not found; needed from Phase 2 for video rendering"

echo "==> Python environment"
"$PY" -m venv backend/.venv
backend/.venv/bin/python -m pip install --quiet --upgrade pip
backend/.venv/bin/python -m pip install --quiet -e "backend[dev]"

echo "==> Dashboard dependencies"
(cd frontend && npm install --no-audit --no-fund --silent)

[ -f .env ] || { cp .env.example .env; echo "==> Created .env from .env.example"; }

echo "==> Database"
(cd backend && .venv/bin/python -m app.cli setup)

echo "==> Building dashboard"
(cd frontend && npm run build >/dev/null)

echo
echo "Done. Start with:  python3 launcher.py     then open http://localhost:3000"
