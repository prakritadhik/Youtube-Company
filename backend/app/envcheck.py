"""Inspect the machine the application is running on (used by ``cli doctor`` and the dashboard)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from typing import Any

from app.config import get_settings


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
    except (OSError, subprocess.SubprocessError):
        return None


def _memory_gib() -> float | None:
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="utf-8") as f:
                kb = int(next(line for line in f if line.startswith("MemTotal")).split()[1])
            return round(kb / 1024 / 1024, 1)
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return round(stat.ullTotalPhys / 1024**3, 1)
        if sys.platform == "darwin":
            v = _run(["sysctl", "-n", "hw.memsize"])
            return round(int(v) / 1024**3, 1) if v else None
    except (OSError, StopIteration, ValueError):
        return None
    return None


def _gpu() -> dict[str, Any]:
    if shutil.which("nvidia-smi"):
        return {"detected": True, "detail": _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])}
    return {"detected": False, "detail": "No NVIDIA GPU tooling found; the pipeline is designed for CPU-only rendering."}


def inspect() -> dict[str, Any]:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(s.data_dir)
    ffmpeg = shutil.which("ffmpeg")
    encoders = _run(["ffmpeg", "-hide_banner", "-encoders"]) if ffmpeg else None
    return {
        "os": f"{platform.system()} {platform.release()}", "platform": platform.platform(), "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(), "memory_gib": _memory_gib(), "gpu": _gpu(),
        "disk_free_gib": round(disk.free / 1024**3, 1), "disk_total_gib": round(disk.total / 1024**3, 1),
        "tools": {
            "ffmpeg": {"path": ffmpeg, "version": _run(["ffmpeg", "-version"]) if ffmpeg else None, "ok": ffmpeg is not None},
            "ffprobe": {"path": shutil.which("ffprobe"), "ok": shutil.which("ffprobe") is not None},
            "node": {"path": shutil.which("node"), "version": _run(["node", "--version"]) if shutil.which("node") else None},
            "git": {"path": shutil.which("git")},
        },
        "ffmpeg_h264_encoder": bool(encoders is not None),
        "providers_configured": {
            "anthropic": s.anthropic_api_key is not None, "elevenlabs": s.elevenlabs_api_key is not None,
        },
        "data_dir": str(s.data_dir),
    }
