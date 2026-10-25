"""Shared helpers for experimental (best-effort) chat adapters.

TikTok LIVE and X have no official live-chat API for general apps.
Failures are tracked in a sliding window; 5 failures in 10 minutes
auto-disables the adapter and alerts the operator.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Optional

AlertFn = Callable[[dict[str, Any]], None]
DisableFn = Callable[[str, str], None]

DEFAULT_MAX_FAILURES = 5
DEFAULT_WINDOW_SEC = 600.0  # 10 minutes


class FailureTracker:
    """Sliding-window failure counter. Thread-safe."""

    def __init__(
        self,
        *,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_sec: float = DEFAULT_WINDOW_SEC,
    ):
        self.max_failures = max(1, int(max_failures))
        self.window_sec = float(window_sec)
        self._times: deque[float] = deque()
        self._lock = threading.Lock()
        self._tripped = False

    def reset(self) -> None:
        with self._lock:
            self._times.clear()
            self._tripped = False

    def on_success(self) -> None:
        """Clear the window after a healthy cycle (does not un-trip)."""
        with self._lock:
            self._times.clear()

    def record_failure(self, now: Optional[float] = None) -> bool:
        """Record a failure. Returns True if the auto-disable threshold was hit."""
        t = time.time() if now is None else float(now)
        with self._lock:
            if self._tripped:
                return True
            cutoff = t - self.window_sec
            while self._times and self._times[0] < cutoff:
                self._times.popleft()
            self._times.append(t)
            if len(self._times) >= self.max_failures:
                self._tripped = True
                return True
            return False

    @property
    def tripped(self) -> bool:
        with self._lock:
            return self._tripped

    def count(self, now: Optional[float] = None) -> int:
        t = time.time() if now is None else float(now)
        with self._lock:
            cutoff = t - self.window_sec
            while self._times and self._times[0] < cutoff:
                self._times.popleft()
            return len(self._times)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "failures_in_window": len(self._times),
                "max_failures": self.max_failures,
                "window_sec": self.window_sec,
                "auto_disabled": self._tripped,
            }


class ExperimentalMixin:
    """Mixin for ChatAdapter subclasses that are experimental."""

    experimental: bool = True
    _tracker: FailureTracker
    _on_auto_disable: Optional[DisableFn]
    _on_alert: Optional[AlertFn]
    _auto_disabled: bool

    def _init_experimental(
        self,
        *,
        on_auto_disable: Optional[DisableFn] = None,
        on_alert: Optional[AlertFn] = None,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_sec: float = DEFAULT_WINDOW_SEC,
    ) -> None:
        self._tracker = FailureTracker(max_failures=max_failures, window_sec=window_sec)
        self._on_auto_disable = on_auto_disable
        self._on_alert = on_alert
        self._auto_disabled = False

    def _note_success(self) -> None:
        self._tracker.on_success()

    def _note_failure(self, reason: str) -> bool:
        """Record failure; if threshold hit, auto-disable and alert. Returns True if disabled."""
        if not self._tracker.record_failure():
            return False
        self._auto_disabled = True
        name = getattr(self, "name", "experimental")
        msg = f"Experimental adapter '{name}' auto-disabled after repeated failures: {reason}"
        if self._on_alert:
            try:
                self._on_alert(
                    {
                        "type": "operator_alert",
                        "adapter": name,
                        "experimental": True,
                        "reason": reason,
                        "message": msg,
                        "ts": time.time(),
                    }
                )
            except Exception:
                pass
        if self._on_auto_disable:
            try:
                self._on_auto_disable(name, reason)
            except Exception:
                pass
        stop = getattr(self, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        return True

    def clear_auto_disable(self) -> None:
        """Clear trip state when operator re-enables the adapter in settings."""
        self._tracker.reset()
        self._auto_disabled = False
        if getattr(self, "_error", None) and "auto-disabled" in str(self._error):
            self._error = None

    def experimental_status(self) -> dict[str, Any]:
        st = self._tracker.status()
        st["experimental"] = True
        st["auto_disabled"] = bool(self._auto_disabled or st.get("auto_disabled"))
        return st
