#!/usr/bin/env python3
"""Install, build, and launch Halo as one desktop process."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Windows uses Scripts/; macOS/Linux use bin/
_VENV_BIN = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
_VENV_PY_NAME = "python.exe" if os.name == "nt" else "python"
_VENV_PIP_NAME = "pip.exe" if os.name == "nt" else "pip"
VENV_PY = _VENV_BIN / _VENV_PY_NAME
VENV_PIP = _VENV_BIN / _VENV_PIP_NAME


def run(cmd, **kwargs):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call(cmd, **kwargs)


def launch_desktop(venv_py: Path, desktop: Path) -> None:
    """Hand off to desktop.py. Windows uses subprocess so Ctrl+C works; Unix uses execv."""
    if os.name == "nt":
        raise SystemExit(subprocess.call([str(venv_py), str(desktop)]))
    os.execv(str(venv_py), [str(venv_py), str(desktop)])


def main():
    os.chdir(ROOT)
    # On Windows / CI without Core MIDI, prefer the sim backend unless already set.
    if os.name == "nt":
        os.environ.setdefault("DJ_BACKEND", "sim")
        os.environ.setdefault("COOKIE_SECURE", "false")
    if not VENV_PY.exists():
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
    # Prefer requirements-local.txt when present (local/desktop deps); else requirements.txt.
    req = ROOT / "requirements-local.txt"
    if not req.exists():
        req = ROOT / "requirements.txt"
    run([str(VENV_PIP), "install", "-r", str(req)])
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found. Install Node.js, then re-run.")
    overlay = ROOT / "frontend-overlay"
    if not overlay.exists():
        overlay = ROOT / "backend" / "frontend-overlay"
    run([npm, "install"], cwd=ROOT / "frontend-dashboard")
    run([npm, "run", "build"], cwd=ROOT / "frontend-dashboard")
    run([npm, "install"], cwd=overlay)
    run([npm, "run", "build"], cwd=overlay)
    launch_desktop(VENV_PY, ROOT / "desktop.py")


if __name__ == "__main__":
    main()
