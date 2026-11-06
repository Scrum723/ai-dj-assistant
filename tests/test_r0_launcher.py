"""R0: start.py and make_app.sh resolve overlay under backend/frontend-overlay."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_start():
    if "start" in sys.modules:
        del sys.modules["start"]
    # Import by path so we get the repo-root start.py, not a site package.
    sys.path.insert(0, str(ROOT))
    try:
        return importlib.import_module("start")
    finally:
        if sys.path and sys.path[0] == str(ROOT):
            sys.path.pop(0)


def test_start_npm_uses_backend_frontend_overlay(tmp_path, monkeypatch):
    """On a tree without root frontend-overlay, npm cwd is backend/frontend-overlay."""
    tree = tmp_path / "app"
    (tree / "backend" / "frontend-overlay").mkdir(parents=True)
    (tree / "frontend-dashboard").mkdir()
    (tree / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    # No root frontend-overlay; no requirements-local.txt.
    venv_py = tree / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    (tree / "desktop.py").write_text("# stub\n", encoding="utf-8")

    start = _load_start()
    monkeypatch.setattr(start, "ROOT", tree)
    monkeypatch.setattr(start, "VENV_PY", venv_py)
    monkeypatch.setattr(start, "VENV_PIP", tree / ".venv" / "bin" / "pip")

    calls: list[tuple] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setattr(start.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(start, "launch_desktop", lambda *a, **k: None)
    monkeypatch.setattr(start.os, "chdir", lambda path: None)

    start.main()

    npm_cwds = [
        Path(cwd).resolve()
        for cmd, cwd in calls
        if cmd and str(cmd[0]).endswith("npm") and cwd is not None
    ]
    overlay = (tree / "backend" / "frontend-overlay").resolve()
    assert overlay in npm_cwds
    assert (tree / "frontend-overlay").resolve() not in npm_cwds


def test_start_prefers_root_frontend_overlay_when_present(tmp_path, monkeypatch):
    tree = tmp_path / "app"
    (tree / "frontend-overlay").mkdir(parents=True)
    (tree / "backend" / "frontend-overlay").mkdir(parents=True)
    (tree / "frontend-dashboard").mkdir()
    (tree / "requirements-local.txt").write_text("pytest\n", encoding="utf-8")
    venv_py = tree / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    (tree / "desktop.py").write_text("# stub\n", encoding="utf-8")

    start = _load_start()
    monkeypatch.setattr(start, "ROOT", tree)
    monkeypatch.setattr(start, "VENV_PY", venv_py)
    monkeypatch.setattr(start, "VENV_PIP", tree / ".venv" / "bin" / "pip")

    calls: list[tuple] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setattr(start.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(start, "launch_desktop", lambda *a, **k: None)
    monkeypatch.setattr(start.os, "chdir", lambda path: None)

    start.main()

    npm_cwds = [
        Path(cwd).resolve()
        for cmd, cwd in calls
        if cmd and str(cmd[0]).endswith("npm") and cwd is not None
    ]
    assert (tree / "frontend-overlay").resolve() in npm_cwds


def _find_bash() -> str | None:
    from shutil import which

    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        which("bash"),
        which("bash.exe"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return None


def test_make_app_sh_syntax():
    script = ROOT / "make_app.sh"
    assert script.is_file()
    bash = _find_bash()
    if not bash:
        pytest.skip("bash not available for bash -n make_app.sh")
    proc = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and "WSL" in out:
        pytest.skip("bash is a WSL stub with no distro; use Git bash")
    assert proc.returncode == 0, out


def test_make_app_sh_resolves_backend_overlay():
    text = (ROOT / "make_app.sh").read_text(encoding="utf-8")
    assert 'OVERLAY="$ROOT/backend/frontend-overlay"' in text
    assert 'OVERLAY="$ROOT/frontend-overlay"' in text
    assert '(cd "$OVERLAY" && npm install && npm run build)' in text
