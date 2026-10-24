"""MIDI control layer for rekordbox. Never import mido/rtmidi at module top level."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ACTIONS_LOG = Path(
    os.getenv("DATA_DIR") or Path(__file__).resolve().parent
) / "dj_actions.jsonl"

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
        actions_log: Optional[Path] = None,
    ):
        self.port_name = port_name
        self.outport = None
        self.inport = None
        self.error: Optional[str] = None
        self.simulator = simulator
        self.backend = (backend or os.getenv("DJ_BACKEND") or "midi").strip().lower()
        self._tap_timers: list[threading.Timer] = []
        self._takeover_timer: Optional[threading.Timer] = None
        self._midi_in_thread: Optional[threading.Thread] = None
        self._midi_in_stop = threading.Event()
        self._on_takeover: Optional[Callable[[], None]] = None
        self._actions_log = Path(actions_log) if actions_log else ACTIONS_LOG
        self.midi_in_port_name: Optional[str] = None
        self.last_foreign_midi_at: Optional[float] = None
        if outport is not None:
            self.outport = outport
            if simulator is None and hasattr(outport, "simulator"):
                self.simulator = outport.simulator
        else:
            self._initialize_midi()

    @property
    def connected(self) -> bool:
        return self.outport is not None

    def set_takeover_callback(self, cb: Optional[Callable[[], None]]) -> None:
        """Called when foreign MIDI input triggers manual takeover (e.g. pause Mixer)."""
        self._on_takeover = cb

    def _log_action(self, payload: dict) -> None:
        row = {"ts": time.time(), **payload}
        try:
            self._actions_log.parent.mkdir(parents=True, exist_ok=True)
            with self._actions_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:
            logger.exception("dj_actions log failed")

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
            result = {"ok": False, "reason": "killed"}
            self._log_action({"command": command, "deck": deck, "value": value, "source": source, **result})
            return result

        src = (source or "manual").lower()
        if src == "auto":
            dj = _dj_settings()
            if not bool(dj.get("automation", False)):
                result = {"ok": False, "reason": "automation_off"}
                self._log_action({"command": command, "deck": deck, "value": value, "source": src, **result})
                return result
            if bool(dj.get("manual_takeover", False)):
                result = {"ok": False, "reason": "manual_takeover"}
                self._log_action({"command": command, "deck": deck, "value": value, "source": src, **result})
                return result

        if command == "crossfader":
            result = self._send_crossfader(value)
            result.setdefault("source", src)
            self._log_action({"command": "crossfader", "deck": deck, "value": value, "source": src, **result})
            return result

        if command == "panic":
            result = self._panic()
            result.setdefault("source", src)
            self._log_action({"command": "panic", "deck": deck, "value": value, "source": src, **result})
            return result

        if command not in DECK_COMMANDS:
            result = {"ok": False, "reason": "bad_command"}
            self._log_action({"command": command, "deck": deck, "value": value, "source": src, **result})
            return result

        if deck not in (1, 2):
            result = {"ok": False, "reason": "bad_deck"}
            self._log_action({"command": command, "deck": deck, "value": value, "source": src, **result})
            return result

        deck_map = MIDI_MAP["decks"][deck]
        channel = int(deck_map["channel"])
        control = int(deck_map[command])

        if command in TAP_COMMANDS:
            ok = self._tap(channel, control)
        else:
            midi_val = max(0, min(127, int(float(value) * 127)))
            ok = self._send_cc(channel, control, midi_val)

        if not ok:
            result = {"ok": False, "reason": "no_port"}
            self._log_action({"command": command, "deck": deck, "value": value, "source": src, **result})
            return result
        result = {
            "ok": True,
            "command": command,
            "deck": deck,
            "channel": channel,
            "control": control,
            "source": src,
        }
        self._log_action({"value": value, **result})
        return result

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
            result = {"ok": False, "reason": "killed"}
            self._log_action({"command": "panic", "source": source, **result})
            return result
        if (source or "").lower() == "auto":
            dj = _dj_settings()
            if not bool(dj.get("automation", False)):
                result = {"ok": False, "reason": "automation_off"}
                self._log_action({"command": "panic", "source": source, **result})
                return result
            if bool(dj.get("manual_takeover", False)):
                result = {"ok": False, "reason": "manual_takeover"}
                self._log_action({"command": "panic", "source": source, **result})
                return result
        result = self._panic()
        result.setdefault("source", (source or "manual").lower())
        self._log_action({"command": "panic", **result})
        return result

    # --- Manual takeover + MIDI input (guarded) ---

    def trigger_manual_takeover(self, *, minutes: Optional[float] = None) -> dict:
        """Human touch / foreign MIDI → pause automation for takeover_minutes."""
        from settings import save as save_settings

        dj = _dj_settings()
        mins = float(minutes if minutes is not None else dj.get("takeover_minutes", 5) or 5)
        save_settings({"dj": {"manual_takeover": True}})
        if self._takeover_timer is not None:
            try:
                self._takeover_timer.cancel()
            except Exception:
                pass
        if mins > 0:

            def _clear() -> None:
                try:
                    from settings import save as _save

                    _save({"dj": {"manual_takeover": False}})
                    logger.info("Manual takeover auto-cleared after %s min", mins)
                except Exception:
                    logger.exception("takeover clear failed")

            self._takeover_timer = threading.Timer(mins * 60.0, _clear)
            self._takeover_timer.daemon = True
            self._takeover_timer.start()
        if self._on_takeover is not None:
            try:
                self._on_takeover()
            except Exception:
                logger.exception("on_takeover callback failed")
        self.last_foreign_midi_at = time.monotonic()
        return {"ok": True, "manual_takeover": True, "minutes": mins}

    def ingest_midi_input(self, msg: Any) -> Optional[dict]:
        """Process a foreign MIDI CC/note as human takeover (sim or live)."""
        typ = getattr(msg, "type", None) or (msg.get("type") if isinstance(msg, dict) else None)
        if typ not in ("control_change", "note_on", "note_off"):
            return None
        if typ == "note_off":
            return None
        if typ == "note_on":
            vel = int(getattr(msg, "velocity", None) or (msg.get("velocity") if isinstance(msg, dict) else 0) or 0)
            if vel <= 0:
                return None
        return self.trigger_manual_takeover()

    def start_midi_input(self, port_name: Optional[str] = None) -> dict:
        """Optional MIDI input listener. Guarded mido import; no-op without mido/port."""
        name = (port_name or os.getenv("DJ_MIDI_IN_PORT") or "").strip()
        if not name:
            dj = _dj_settings()
            name = str((dj.get("midi_in_port") or "")).strip()
        if not name:
            return {"ok": False, "reason": "no_port_configured"}
        mido = _load_mido()
        if mido is None:
            return {"ok": False, "reason": "no_mido"}
        self.stop_midi_input()
        self.midi_in_port_name = name
        self._midi_in_stop.clear()
        try:
            self.inport = mido.open_input(name)
        except Exception as e:
            self.error = f"MIDI in: {e}"
            logger.warning("MIDI input open failed: %s", e)
            return {"ok": False, "reason": str(e)}

        def _loop() -> None:
            while not self._midi_in_stop.is_set():
                try:
                    for msg in self.inport.iter_pending():
                        self.ingest_midi_input(msg)
                    self._midi_in_stop.wait(0.02)
                except Exception:
                    if self._midi_in_stop.is_set():
                        break
                    logger.exception("MIDI input loop error")
                    self._midi_in_stop.wait(0.2)

        self._midi_in_thread = threading.Thread(target=_loop, name="midi-in", daemon=True)
        self._midi_in_thread.start()
        logger.info("MIDI input listening on '%s'", name)
        return {"ok": True, "port": name}

    def stop_midi_input(self) -> None:
        self._midi_in_stop.set()
        if self.inport is not None:
            try:
                self.inport.close()
            except Exception:
                pass
            self.inport = None
        self._midi_in_thread = None
