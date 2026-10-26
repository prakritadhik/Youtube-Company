"""The launcher rebuilds the dashboard when its code changed, so an update that adds a page does not keep serving the old build."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("launcher_under_test", ROOT / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_dashboard_is_rebuilt_only_when_missing_or_older_than_its_sources(tmp_path, monkeypatch):
    mod = load()
    fe = tmp_path / "frontend"
    (fe / "app" / "(app)" / "ai").mkdir(parents=True)
    (fe / ".next").mkdir()
    page, pkg = fe / "app" / "(app)" / "ai" / "page.tsx", fe / "package.json"
    page.write_text("export default function P() { return null }")
    pkg.write_text("{}")
    monkeypatch.setattr(mod, "FRONTEND", fe)

    assert mod.dashboard_is_stale() is True  # never built
    build = fe / ".next" / "BUILD_ID"
    build.write_text("abc")
    now = build.stat().st_mtime
    os.utime(page, (now - 100, now - 100))
    os.utime(pkg, (now - 100, now - 100))
    assert mod.dashboard_is_stale() is False  # built after every source file was last changed

    (fe / "app" / "(app)" / "connections").mkdir()
    new_page = fe / "app" / "(app)" / "connections" / "page.tsx"
    new_page.write_text("export default function P() { return null }")
    os.utime(new_page, (now + 50, now + 50))  # an update brought a new page
    assert mod.dashboard_is_stale() is True

    os.utime(new_page, (now - 10, now - 10))
    (fe / "node_modules").mkdir()
    (fe / "node_modules" / "x.js").write_text("changed")
    os.utime(fe / "node_modules" / "x.js", (now + 500, now + 500))
    assert mod.dashboard_is_stale() is False  # dependencies and the build folder itself are not sources
