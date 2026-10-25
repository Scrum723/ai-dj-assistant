"""Fake MIDI out + deck simulator for DJ_BACKEND=sim (Windows/Linux safe)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class MidiMessage:
    type: str
    channel: int
    control: int = 0
    value: int = 0
    note: int = 0
    velocity: int = 0

    def bytes(self) -> bytes:
        if self.type == "control_change":
            return bytes([0xB0 | (self.channel & 0x0F), self.control & 0x7F, self.value & 0x7F])
        if self.type == "note_on":
            return bytes([0x90 | (self.channel & 0x0F), self.note & 0x7F, self.velocity & 0x7F])
        if self.type == "note_off":
            return bytes([0x80 | (self.channel & 0x0F), self.note & 0x7F, self.velocity & 0x7F])
        return b""


class FakeMidiOut:
    """Drop-in MIDI output that records messages and optionally feeds a DeckSimulator."""

    def __init__(self, simulator: Optional["DeckSimulator"] = None):
        self.simulator = simulator
        self.sent: list[MidiMessage] = []

    def send(self, msg: Any) -> None:
        if isinstance(msg, MidiMessage):
            m = msg
        else:
            m = MidiMessage(
                type=getattr(msg, "type", "control_change"),
                channel=int(getattr(msg, "channel", 0)),
                control=int(getattr(msg, "control", 0) or 0),
                value=int(getattr(msg, "value", 0) or 0),
                note=int(getattr(msg, "note", 0) or 0),
                velocity=int(getattr(msg, "velocity", 0) or 0),
            )
        self.sent.append(m)
        if self.simulator is not None:
            self.simulator.handle_midi(m)

    def close(self) -> None:
        pass


@dataclass
class DeckState:
    loaded_track: Optional[dict] = None
    playing: bool = False
    position_beats: float = 0.0
    bpm: float = 128.0
    pitch: float = 0.0
    eq_low: float = 0.5
    eq_mid: float = 0.5
    eq_hi: float = 0.5
    filter: float = 0.5
    volume: float = 1.0
    _play_started_at: Optional[float] = None
    _play_origin_beats: float = 0.0


@dataclass
class DeckSimulator:
    """Two-deck rekordbox stand-in driven by MIDI CCs (same map as DJController)."""

    clock: Callable[[], float] = field(default=time.monotonic)
    crossfader: float = 0.0
    decks: dict[int, DeckState] = field(default_factory=lambda: {1: DeckState(), 2: DeckState()})

    # CC map mirrors dj_controller.MIDI_MAP (channel selects deck)
    CC_PLAY = 10
    CC_CUE = 11
    CC_CROSSFADER = 12
    CC_SYNC = 13
    CC_LOAD = 14
    CC_VOLUME = 15
    CC_FILTER = 16
    CC_EQ_LOW = 20
    CC_EQ_MID = 21
    CC_EQ_HI = 22
    CC_BROWSE_UP = 30
    CC_BROWSE_DOWN = 31

    def deck(self, n: int) -> DeckState:
        return self.decks[n]

    def _channel_to_deck(self, channel: int) -> Optional[int]:
        if channel == 0:
            return 1
        if channel == 1:
            return 2
        return None

    def _norm(self, value: int) -> float:
        return max(0.0, min(1.0, int(value) / 127.0))

    def handle_midi(self, msg: MidiMessage) -> None:
        if msg.type != "control_change":
            return
        if msg.control == self.CC_CROSSFADER and msg.channel == 0:
            self.crossfader = self._norm(msg.value)
            return
        deck_n = self._channel_to_deck(msg.channel)
        if deck_n is None:
            return
        d = self.decks[deck_n]
        cc = msg.control
        if cc == self.CC_PLAY and msg.value >= 64:
            self._toggle_play(deck_n)
        elif cc == self.CC_CUE and msg.value >= 64:
            self._cue(deck_n)
        elif cc == self.CC_SYNC and msg.value >= 64:
            other = self.decks[2 if deck_n == 1 else 1]
            if other.bpm > 0:
                d.bpm = other.bpm
        elif cc == self.CC_VOLUME:
            d.volume = self._norm(msg.value)
        elif cc == self.CC_FILTER:
            d.filter = self._norm(msg.value)
        elif cc == self.CC_EQ_LOW:
            d.eq_low = self._norm(msg.value)
        elif cc == self.CC_EQ_MID:
            d.eq_mid = self._norm(msg.value)
        elif cc == self.CC_EQ_HI:
            d.eq_hi = self._norm(msg.value)
        elif cc == self.CC_LOAD and msg.value >= 64:
            # Load is usually preceded by browse; keep current loaded_track
            pass
        elif cc in (self.CC_BROWSE_UP, self.CC_BROWSE_DOWN):
            pass

    def _sync_position(self, deck_n: int) -> None:
        d = self.decks[deck_n]
        if d.playing and d._play_started_at is not None:
            elapsed = self.clock() - d._play_started_at
            rate = d.bpm / 60.0 * (1.0 + d.pitch)
            d.position_beats = d._play_origin_beats + elapsed * rate

    def _toggle_play(self, deck_n: int) -> None:
        d = self.decks[deck_n]
        self._sync_position(deck_n)
        if d.playing:
            d.playing = False
            d._play_started_at = None
            d._play_origin_beats = d.position_beats
        else:
            d.playing = True
            d._play_started_at = self.clock()
            d._play_origin_beats = d.position_beats

    def _cue(self, deck_n: int) -> None:
        d = self.decks[deck_n]
        d.playing = False
        d.position_beats = 0.0
        d._play_started_at = None
        d._play_origin_beats = 0.0

    def load_track(self, deck_n: int, track: dict) -> None:
        d = self.decks[deck_n]
        d.loaded_track = dict(track)
        d.playing = False
        d.position_beats = 0.0
        d._play_started_at = None
        d._play_origin_beats = 0.0
        bpm = track.get("bpm")
        if bpm is not None:
            try:
                d.bpm = float(bpm)
            except (TypeError, ValueError):
                pass

    def position(self, deck_n: int) -> float:
        self._sync_position(deck_n)
        return self.decks[deck_n].position_beats

    def playing_deck(self) -> Optional[int]:
        """Deck closest to the crossfader that is playing; prefer louder side."""
        a_play = self.decks[1].playing
        b_play = self.decks[2].playing
        if a_play and not b_play:
            return 1
        if b_play and not a_play:
            return 2
        if a_play and b_play:
            return 1 if self.crossfader < 0.5 else 2
        return None

    def output_level(self) -> float:
        """Approximate master level 0–1 for silence watchdog (sim)."""
        xf = float(self.crossfader)
        level = 0.0
        for n, weight in ((1, 1.0 - xf), (2, xf)):
            d = self.decks[n]
            if d.playing:
                vol = float(d.volume or 0.0)
                level = max(level, vol * max(weight, 0.05))
        return level

    def panic(self) -> None:
        """Reset EQ/filter; centre crossfader toward the playing deck."""
        for d in self.decks.values():
            d.eq_low = 0.5
            d.eq_mid = 0.5
            d.eq_hi = 0.5
            d.filter = 0.5
        playing = self.playing_deck()
        if playing == 1:
            self.crossfader = 0.0
        elif playing == 2:
            self.crossfader = 1.0
        else:
            self.crossfader = 0.5

    def snapshot(self) -> dict:
        out = {"crossfader": self.crossfader, "decks": {}}
        for n, d in self.decks.items():
            self._sync_position(n)
            out["decks"][n] = {
                "loaded_track": d.loaded_track,
                "playing": d.playing,
                "position_beats": d.position_beats,
                "bpm": d.bpm,
                "pitch": d.pitch,
                "eq_low": d.eq_low,
                "eq_mid": d.eq_mid,
                "eq_hi": d.eq_hi,
                "filter": d.filter,
                "volume": d.volume,
            }
        return out
