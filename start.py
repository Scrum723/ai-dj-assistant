#!/usr/bin/env python3
"""Install, build, and launch Halo as one desktop process."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
VENV_PIP = ROOT / ".venv" / "bin" / "pip"


def run(cmd, **kwargs):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call(cmd, **kwargs)


def main():
    os.chdir(ROOT)
    if not VENV_PY.exists():
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
    req = ROOT / "requirements-local.txt"
    if not req.exists():
        req = ROOT / "requirements.txt"
    run([str(VENV_PIP), "install", "-r", str(req)])
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found. Install Node.js, then re-run.")
    run([npm, "install"], cwd=ROOT / "frontend-dashboard")
    run([npm, "run", "build"], cwd=ROOT / "frontend-dashboard")
    run([npm, "install"], cwd=ROOT / "frontend-overlay")
    run([npm, "run", "build"], cwd=ROOT / "frontend-overlay")
    os.execv(str(VENV_PY), [str(VENV_PY), str(ROOT / "desktop.py")])


if __name__ == "__main__":
    main()
