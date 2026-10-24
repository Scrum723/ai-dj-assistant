"""Silence + heartbeat watchdog for DJ automation safety."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SILENCE_SEC = 3.0
HEARTBEAT_STALL_SEC = 2.0
LEVEL_FLOOR = 0.02

Clock = Callable[[], float]
LevelFn = Callable[[], float]


def sim_level(controller: Any) -> float:
    """Proxy RMS from DeckSimulator: playing deck volume × fader weight."""
    sim = getattr(controller, "simulator", None)
    if sim is None:
        return 0.0
    if hasattr(sim, "output_level"):
        return float(sim.output_level())
    level = 0.0
    xf = float(getattr(sim, "crossfader", 0.0))
    for n, weight in ((1, 1.0 - xf), (2, xf)):
        try:
            d = sim.deck(n)
        except Exception:
            continue
        if getattr(d, "playing", False):
            vol = float(getattr(d, "volume", 1.0) or 0.0)
            level = max(level, vol * max(weight, 0.05))
    return level


def sounddevice_level(device: Optional[str] = None) -> float:
    """Mac loopback RMS via sounddevice (BlackHole). Guarded import; 0 if unavailable."""
    try:
        import numpy as np  # type: ignore
        import sounddevice as sd  # type: ignore
    except Exception:
        return 0.0
    try:
        duration = 0.05
        fs = 44100
        kwargs: dict[str, Any] = {"samplerate": fs, "channels": 1, "dtype": "float32"}
        if device:
            kwargs["device"] = device
        audio = sd.rec(int(duration * fs), **kwargs)
        sd.wait()
        rms = float(np.sqrt(np.mean(np.square(audio))))
        return min(1.0, rms * 8.0)
    except Exception:
        logger.debug("sounddevice level read failed", exc_info=True)
        return 0.0


class Watchdog:
    """Silence >3s → backup deck; heartbeat stall >2s → panic + /state alert."""

    def __init__(
        self,
        controller: Any,
        mixer: Any = None,
        *,
        clock: Optional[Clock] = None,
        level_fn: Optional[LevelFn] = None,
        silence_sec: float = SILENCE_SEC,
        stall_sec: float = HEARTBEAT_STALL_SEC,
        automation_fn: Optional[Callable[[], bool]] = None,
    ):
        self.controller = controller
        self.mixer = mixer
        self.clock: Clock = clock or time.monotonic
        self.level_fn: LevelFn = level_fn or (lambda: sim_level(controller))
        self.silence_sec = float(silence_sec)
        self.stall_sec = float(stall_sec)
        self._automation_fn = automation_fn
        self.last_heartbeat = self.clock()
        self.last_audible_at = self.clock()
        self.alerts: list[dict] = []
        self.silence_recovered = False
        self.panic_triggered = False
        self._silence_armed = True

    def _automation_on(self) -> bool:
        if self._automation_fn is not None:
            return bool(self._automation_fn())
        try:
            from settings import load as load_settings

            dj = load_settings().get("dj") or {}
            if bool(dj.get("manual_takeover", False)):
                return False
            return bool(dj.get("automation", False))
        except Exception:
            return False

    def tick_heartbeat(self) -> None:
        self.last_heartbeat = self.clock()

    def add_alert(self, kind: str, message: str, **extra: Any) -> dict:
        row = {"kind": kind, "message": message, "ts": self.clock(), **extra}
        self.alerts = ([row] + self.alerts)[:20]
        logger.warning("watchdog alert: %s — %s", kind, message)
        return row

    def check(self) -> dict:
        """Run silence + stall checks once. Returns status snapshot."""
        now = self.clock()
        level = float(self.level_fn())
        if level >= LEVEL_FLOOR:
            self.last_audible_at = now
            self._silence_armed = True

        silence_age = now - self.last_audible_at
        heartbeat_age = now - self.last_heartbeat
        actions: list[str] = []

        if (
            self._automation_on()
            and self._silence_armed
            and silence_age >= self.silence_sec
            and level < LEVEL_FLOOR
        ):
            self.recover_silence()
            self._silence_armed = False
            actions.append("silence_recover")

        mixer_active = False
        if self.mixer is not None:
            st = getattr(self.mixer, "state", None)
            paused = bool(getattr(self.mixer, "_paused", False))
            enabled = bool(getattr(self.mixer, "enabled", False))
            name = getattr(st, "value", st)
            mixer_active = enabled and not paused and name not in (None, "IDLE", "idle")

        if mixer_active and heartbeat_age >= self.stall_sec:
            self.on_stall(heartbeat_age)
            actions.append("stall_panic")

        return {
            "level": level,
            "silence_age": silence_age,
            "heartbeat_age": heartbeat_age,
            "actions": actions,
            "alerts": list(self.alerts),
            "silence_recovered": self.silence_recovered,
            "panic_triggered": self.panic_triggered,
        }

    def recover_silence(self) -> dict:
        """Play backup deck and swing crossfader to it (source=auto if gates allow)."""
        ctl = self.controller
        sim = getattr(ctl, "simulator", None)
        active = 1
        if self.mixer is not None and getattr(self.mixer, "active_deck", None):
            active = int(self.mixer.active_deck)
        elif sim is not None and hasattr(sim, "playing_deck"):
            active = sim.playing_deck() or 1
        backup = 2 if active == 1 else 1
        xf = 1.0 if backup == 2 else 0.0
        # Prefer auto so gates apply; fall back to manual for recovery if refused
        play = ctl.send("play", deck=backup, source="auto")
        if not play.get("ok"):
            play = ctl.send("play", deck=backup, source="manual")
        fade = ctl.send("crossfader", value=xf, source="auto")
        if not fade.get("ok"):
            fade = ctl.send("crossfader", value=xf, source="manual")
        if self.mixer is not None:
            try:
                self.mixer.active_deck = backup
            except Exception:
                pass
        self.silence_recovered = True
        self.last_audible_at = self.clock()
        self.add_alert(
            "silence",
            f"Silence >{self.silence_sec}s — played backup deck {backup}",
            backup_deck=backup,
        )
        return {"ok": True, "backup_deck": backup, "play": play, "crossfader": fade}

    def on_stall(self, age: Optional[float] = None) -> dict:
        """Mixer heartbeat stall → panic + alert."""
        age = float(age if age is not None else (self.clock() - self.last_heartbeat))
        result = self.controller.panic(source="manual")
        self.panic_triggered = True
        # Retrigger window: bump heartbeat so we don't spam panic every check
        self.last_heartbeat = self.clock()
        self.add_alert(
            "stall",
            f"Heartbeat stall {age:.2f}s — panic",
            heartbeat_age=age,
        )
        return {"ok": True, "panic": result, "heartbeat_age": age}

    def status(self) -> dict:
        now = self.clock()
        return {
            "level": float(self.level_fn()),
            "silence_age": now - self.last_audible_at,
            "heartbeat_age": now - self.last_heartbeat,
            "alerts": list(self.alerts),
            "silence_recovered": self.silence_recovered,
            "panic_triggered": self.panic_triggered,
            "charles_blockers": [
                "controller (MIDI input for takeover)",
                "BlackHole (or audio loopback) for silence RMS",
                "rekordbox (live MIDI Learn + playback)",
            ],
        }
