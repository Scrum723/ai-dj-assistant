"""MIDI control layer for rekordbox. Never import mido/rtmidi at module top level."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Per-deck MIDI_MAP: deck 1 → channel 0, deck 2 → channel 1. Same CC numbers per deck.
# Crossfader is global on channel 0.
MIDI_MAP: dict[str, Any] = {
    "decks": {
        1: {
            "channel": 0,
            "play": 10,
            "cue": 11,
            "sync": 13,
            "load": 14,
            "volume": 15,
            "filter": 16,
            "eq_low": 20,
            "eq_mid": 21,
            "eq_hi": 22,
            "browse_up": 30,
            "browse_down": 31,
        },
        2: {
            "channel": 1,
            "play": 10,
            "cue": 11,
            "sync": 13,
            "load": 14,
            "volume": 15,
            "filter": 16,
            "eq_low": 20,
            "eq_mid": 21,
            "eq_hi": 22,
            "browse_up": 30,
            "browse_down": 31,
        },
    },
    "crossfader": {"channel": 0, "control": 12},
}

DECK_COMMANDS = frozenset(
    {
        "play",
        "cue",
        "sync",
        "load",
        "volume",
        "filter",
        "eq_low",
        "eq_mid",
        "eq_hi",
        "browse_up",
        "browse_down",
    }
)
TAP_COMMANDS = frozenset({"play", "cue", "sync", "load", "browse_up", "browse_down"})
TAP_MS = 0.08


def _load_mido():
    """Lazy import — cloud / Windows / Linux without CoreMIDI must not fail at import."""
    try:
        import mido  # type: ignore

        return mido
    except Exception:
        return None


def _dj_settings() -> dict:
    try:
        from settings import load as load_settings

        return (load_settings().get("dj") or {})
    except Exception:
        return {}


class DJController:
    def __init__(
        self,
        port_name: str = "AI_DJ_Virtual_Port",
        *,
        backend: Optional[str] = None,
        outport: Any = None,
        simulator: Any = None,
    ):
        self.port_name = port_name
        self.outport = None
        self.error: Optional[str] = None
        self.simulator = simulator
        self.backend = (backend or os.getenv("DJ_BACKEND") or "midi").strip().lower()
        self._tap_timers: list[threading.Timer] = []
        if outport is not None:
            self.outport = outport
            if simulator is None and hasattr(outport, "simulator"):
                self.simulator = outport.simulator
        else:
            self._initialize_midi()

    @property
    def connected(self) -> bool:
        return self.outport is not None

    def _initialize_midi(self) -> None:
        if self.backend == "sim":
            from sim.deck_sim import DeckSimulator, FakeMidiOut

            if self.simulator is None:
                self.simulator = DeckSimulator()
            self.outport = FakeMidiOut(simulator=self.simulator)
            self.error = None
            logger.info("DJ_BACKEND=sim — FakeMidiOut active (no mido).")
            return

        mido = _load_mido()
        if mido is None:
            self.error = "MIDI disabled (no mido/rtmidi on this host)"
            logger.warning(self.error)
            return
        try:
            self.outport = mido.open_output(self.port_name, virtual=True)
            logger.info("Virtual MIDI port '%s' created.", self.port_name)
        except Exception as e:
            self.error = str(e)
            logger.error("Error creating virtual MIDI port: %s", e)

    def send(
        self,
        command: str,
        deck: int = 1,
        value: float = 1.0,
        source: str = "manual",
    ) -> dict:
        """Only MIDI send path. Gates: kill; auto vs automation/takeover."""
        from sentinel import is_killed

        if is_killed():
            return {"ok": False, "reason": "killed"}

        src = (source or "manual").lower()
        if src == "auto":
            dj = _dj_settings()
            if not bool(dj.get("automation", False)):
                return {"ok": False, "reason": "automation_off"}
            if bool(dj.get("manual_takeover", False)):
                return {"ok": False, "reason": "manual_takeover"}

        if command == "crossfader":
            return self._send_crossfader(value)

        if command == "panic":
            return self._panic()

        if command not in DECK_COMMANDS:
            return {"ok": False, "reason": "bad_command"}

        if deck not in (1, 2):
            return {"ok": False, "reason": "bad_deck"}

        deck_map = MIDI_MAP["decks"][deck]
        channel = int(deck_map["channel"])
        control = int(deck_map[command])

        if command in TAP_COMMANDS:
            ok = self._tap(channel, control)
        else:
            midi_val = max(0, min(127, int(float(value) * 127)))
            ok = self._send_cc(channel, control, midi_val)

        if not ok:
            return {"ok": False, "reason": "no_port"}
        return {
            "ok": True,
            "command": command,
            "deck": deck,
            "channel": channel,
            "control": control,
            "source": src,
        }

    def _send_cc(self, channel: int, control: int, value: int) -> bool:
        if not self.outport:
            logger.warning("MIDI outport not initialized. Cannot send command.")
            return False
        value = max(0, min(127, int(value)))
        use_sim_msg = self.backend == "sim" or self.outport.__class__.__name__ == "FakeMidiOut"
        if use_sim_msg:
            from sim.deck_sim import MidiMessage

            self.outport.send(
                MidiMessage(type="control_change", channel=channel, control=control, value=value)
            )
        else:
            mido = _load_mido()
            if mido is None:
                return False
            msg = mido.Message("control_change", channel=channel, control=control, value=value)
            self.outport.send(msg)
        logger.info("Sent MIDI CC ch=%s cc=%s val=%s", channel, control, value)
        return True

    def _tap(self, channel: int, control: int) -> bool:
        """Non-blocking play/cue/sync/load tap (127 then 0)."""
        if not self._send_cc(channel, control, 127):
            return False

        def _release() -> None:
            try:
                self._send_cc(channel, control, 0)
            except Exception:
                pass

        t = threading.Timer(TAP_MS, _release)
        t.daemon = True
        t.start()
        self._tap_timers.append(t)
        return True

    def _send_crossfader(self, value: float) -> dict:
        cf = MIDI_MAP["crossfader"]
        midi_val = max(0, min(127, int(float(value) * 127)))
        ok = self._send_cc(int(cf["channel"]), int(cf["control"]), midi_val)
        if not ok:
            return {"ok": False, "reason": "no_port"}
        return {"ok": True, "command": "crossfader", "value": float(value)}

    def _panic(self) -> dict:
        """Reset EQ/filter on both decks; centre fader on playing deck."""
        results = []
        playing_deck = None
        if self.simulator is not None:
            playing_deck = self.simulator.playing_deck()
        for deck in (1, 2):
            for band in ("eq_low", "eq_mid", "eq_hi", "filter"):
                results.append(self.send(band, deck=deck, value=0.5, source="manual"))
        if playing_deck == 1:
            xf = 0.0
        elif playing_deck == 2:
            xf = 1.0
        else:
            xf = 0.5
        results.append(self._send_crossfader(xf))
        if self.simulator is not None:
            # Keep sim state aligned if MIDI path already updated it
            self.simulator.panic()
            if playing_deck == 1:
                self.simulator.crossfader = 0.0
            elif playing_deck == 2:
                self.simulator.crossfader = 1.0
            else:
                self.simulator.crossfader = 0.5
        return {"ok": True, "command": "panic", "crossfader": xf, "playing_deck": playing_deck}

    # --- Convenience wrappers (all go through send) ---

    def deck_play_pause(self, deck: int = 1, source: str = "manual") -> dict:
        return self.send("play", deck=deck, source=source)

    def crossfader(self, value: float, source: str = "manual") -> dict:
        return self.send("crossfader", deck=1, value=value, source=source)

    def set_eq(self, deck: int = 1, band: str = "low", value: float = 0.5, source: str = "manual") -> dict:
        cmd = {"low": "eq_low", "mid": "eq_mid", "hi": "eq_hi"}.get(band, band)
        if cmd not in ("eq_low", "eq_mid", "eq_hi"):
            return {"ok": False, "reason": "bad_band"}
        return self.send(cmd, deck=deck, value=value, source=source)

    def set_filter(self, deck: int = 1, value: float = 0.5, source: str = "manual") -> dict:
        return self.send("filter", deck=deck, value=value, source=source)

    def sync(self, deck: int = 1, source: str = "manual") -> dict:
        return self.send("sync", deck=deck, source=source)

    def load(self, deck: int = 1, source: str = "manual") -> dict:
        return self.send("load", deck=deck, source=source)

    def browse(self, deck: int = 1, direction: str = "down", source: str = "manual") -> dict:
        cmd = "browse_up" if direction == "up" else "browse_down"
        return self.send(cmd, deck=deck, source=source)

    def panic(self, source: str = "manual") -> dict:
        from sentinel import is_killed

        if is_killed():
            return {"ok": False, "reason": "killed"}
        if (source or "").lower() == "auto":
            dj = _dj_settings()
            if not bool(dj.get("automation", False)):
                return {"ok": False, "reason": "automation_off"}
            if bool(dj.get("manual_takeover", False)):
                return {"ok": False, "reason": "manual_takeover"}
        return self._panic()
