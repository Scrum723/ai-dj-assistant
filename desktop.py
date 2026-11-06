#!/usr/bin/env python3
"""Single-process Chief desktop: API + booth + overlay + room."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
os.chdir(ROOT)
sys.path.insert(0, str(BACKEND))

# Cannot import backend.envcompat before sys.path; inline the same CHIEF_ then HALO_ lookup.
_port = os.getenv("CHIEF_PORT")
if _port is None:
    _port = os.getenv("HALO_PORT", "8000")
PORT = int(os.getenv("PORT") or _port)
_host = os.getenv("CHIEF_HOST")
if _host is None:
    _host = os.getenv("HALO_HOST", "0.0.0.0")
HOST = _host


def wait_up(url: str, timeout: float = 25) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as res:
                if res.status < 500:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def open_window(url: str):
    try:
        import webview

        webview.create_window("DJ Bot Botty", url, width=1440, height=920)
        webview.start()
        return True
    except Exception:
        pass
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(chrome):
        subprocess.Popen([chrome, f"--app={url}"])
        return False
    webbrowser.open(url)
    return False


def main():
    import uvicorn

    os.environ.setdefault("CHIEF_DESKTOP", "1")
    os.environ.setdefault("HALO_DESKTOP", "1")
    os.chdir(BACKEND)
    config = uvicorn.Config(
        "main:app",
        host=HOST,
        port=PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    booth = f"http://127.0.0.1:{PORT}/booth/"
    overlay = f"http://127.0.0.1:{PORT}/overlay/"
    if not wait_up(f"http://127.0.0.1:{PORT}/state"):
        print("Backend failed to start on port", PORT)
        sys.exit(1)
    print("Chief desktop is up")
    print("  Booth     ", booth)
    print("  Room      ", f"{booth}?tab=room")
    print("  Room short", f"http://127.0.0.1:{PORT}/room")
    print("  Overlay   ", overlay)
    print("  Visualizer", f"http://127.0.0.1:{PORT}/visualizer/")
    print("Phones on this Wi-Fi can open the Room URL shown in the Room tab.")
    print("Closing the window does NOT stop Chief. Press Ctrl+C here to quit.")
    open_window(booth)
    try:
        while thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Chief...")
        server.should_exit = True


if __name__ == "__main__":
    main()
