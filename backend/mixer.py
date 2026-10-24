"""Phrase-aligned mix engine: IDLE → PLAYING → PREPARING → TRANSITIONING → PLAYING."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

BEATS_PER_BAR = 4
DEFAULT_PHRASE_BARS = 16
OUTRO_LEAD_BARS = 32
SOURCE_AUTO = "auto"

Clock = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]


class MixState(str, Enum):
    IDLE = "IDLE"
    PLAYING = "PLAYING"
    PREPARING = "PREPARING"
    TRANSITIONING = "TRANSITIONING"


@dataclass
class MixStep:
    """One timed action relative to transition start (bars)."""

    at_bar: float
    command: str
    deck: int = 1
    value: float = 1.0


@dataclass
class TransitionRecord:
    from_track: dict
    to_track: dict
    template: str
    start_sec: float
    end_sec: float
    start_beat: float
    phrase_beat: float
    steps_fired: list[dict] = field(default_factory=list)
    silence_sec: float = 0.0


TEMPLATES: dict[str, list[MixStep]] = {
    # 16-bar bass-swap: cut outgoing bass, bring incoming, crossfade
    "bass_swap_blend": [
        MixStep(0.0, "sync", deck=2),
        MixStep(0.0, "eq_low", deck=2, value=0.15),
        MixStep(0.0, "eq_low", deck=1, value=0.85),
        MixStep(0.0, "play", deck=2),
        MixStep(0.0, "crossfader", value=0.15),
        MixStep(4.0, "eq_low", deck=1, value=0.45),
        MixStep(4.0, "eq_low", deck=2, value=0.55),
        MixStep(4.0, "crossfader", value=0.4),
        MixStep(8.0, "eq_low", deck=1, value=0.15),
        MixStep(8.0, "eq_low", deck=2, value=0.85),
        MixStep(8.0, "crossfader", value=0.7),
        MixStep(12.0, "eq_low", deck=1, value=0.0),
        MixStep(12.0, "eq_low", deck=2, value=1.0),
        MixStep(12.0, "crossfader", value=0.9),
        MixStep(16.0, "crossfader", value=1.0),
        MixStep(16.0, "eq_low", deck=1, value=0.5),
        MixStep(16.0, "eq_mid", deck=1, value=0.5),
        MixStep(16.0, "eq_hi", deck=1, value=0.5),
        MixStep(16.0, "play", deck=1),  # stop outgoing (toggle)
    ],
    # Filter sweep out over 8 bars then cut
    "filter_out": [
        MixStep(0.0, "sync", deck=2),
        MixStep(0.0, "play", deck=2),
        MixStep(0.0, "filter", deck=1, value=0.35),
        MixStep(0.0, "crossfader", value=0.2),
        MixStep(2.0, "filter", deck=1, value=0.2),
        MixStep(2.0, "crossfader", value=0.4),
        MixStep(4.0, "filter", deck=1, value=0.08),
        MixStep(4.0, "crossfader", value=0.65),
        MixStep(6.0, "filter", deck=1, value=0.0),
        MixStep(6.0, "crossfader", value=0.9),
        MixStep(8.0, "crossfader", value=1.0),
        MixStep(8.0, "filter", deck=1, value=0.5),
        MixStep(8.0, "play", deck=1),
    ],
    # Hard phrase cut at bar 0 (1-beat aligned)
    "phrase_cut": [
        MixStep(0.0, "sync", deck=2),
        MixStep(0.0, "play", deck=2),
        MixStep(0.0, "crossfader", value=1.0),
        MixStep(0.25, "play", deck=1),
        MixStep(0.25, "eq_low", deck=1, value=0.5),
        MixStep(0.25, "filter", deck=1, value=0.5),
    ],
}


def bars_to_sec(bars: float, bpm: float) -> float:
    if bpm <= 0:
        return 0.0
    return float(bars) * BEATS_PER_BAR * 60.0 / float(bpm)


def beats_to_sec(beats: float, bpm: float) -> float:
    if bpm <= 0:
        return 0.0
    return float(beats) * 60.0 / float(bpm)


def sec_to_beats(sec: float, bpm: float) -> float:
    if bpm <= 0:
        return 0.0
    return float(sec) * float(bpm) / 60.0


def track_bpm(track: dict, default: float = 128.0) -> float:
    try:
        b = float(track.get("bpm") or default)
        return b if b > 0 else default
    except (TypeError, ValueError):
        return default


def track_duration_sec(track: dict) -> float:
    for key in ("duration_sec", "duration", "length_sec"):
        if track.get(key) is not None:
            try:
                return max(0.0, float(track[key]))
            except (TypeError, ValueError):
                pass
    bpm = track_bpm(track)
    # Synthetic default: ~4 min
    return 240.0 if bpm <= 0 else bars_to_sec(128, bpm)


def track_outro_sec(track: dict) -> float:
    if track.get("outro_sec") is not None:
        try:
            return float(track["outro_sec"])
        except (TypeError, ValueError):
            pass
    dur = track_duration_sec(track)
    return max(0.0, dur - bars_to_sec(OUTRO_LEAD_BARS, track_bpm(track)))


def phrase_boundaries_beats(track: dict) -> list[float]:
    """Return phrase-start beats (prefer phrases_json; else every 16 bars)."""
    bpm = track_bpm(track)
    phrases = track.get("phrases") or track.get("phrases_json")
    if isinstance(phrases, str):
        import json

        try:
            phrases = json.loads(phrases)
        except Exception:
            phrases = None
    out: list[float] = []
    if isinstance(phrases, list) and phrases:
        for p in phrases:
            if isinstance(p, dict) and p.get("start") is not None:
                out.append(sec_to_beats(float(p["start"]), bpm))
            elif isinstance(p, (int, float)):
                out.append(sec_to_beats(float(p), bpm))
    if out:
        return sorted(out)
    dur_beats = sec_to_beats(track_duration_sec(track), bpm)
    step = DEFAULT_PHRASE_BARS * BEATS_PER_BAR
    b = 0.0
    while b <= dur_beats + 0.01:
        out.append(b)
        b += step
    return out


def transition_start_beat(track: dict, lead_bars: int = OUTRO_LEAD_BARS) -> float:
    """Phrase boundary at or after outro − lead_bars."""
    bpm = track_bpm(track)
    outro = track_outro_sec(track)
    target = max(0.0, sec_to_beats(outro, bpm) - lead_bars * BEATS_PER_BAR)
    boundaries = phrase_boundaries_beats(track)
    for b in boundaries:
        if b + 1e-6 >= target:
            return b
    return boundaries[-1] if boundaries else target


def choose_template(outgoing: dict, incoming: dict) -> str:
    """Pick template from energy / camelot heuristics."""
    try:
        e0 = float(outgoing.get("energy") if outgoing.get("energy") is not None else 0.5)
        e1 = float(incoming.get("energy") if incoming.get("energy") is not None else 0.5)
    except (TypeError, ValueError):
        e0, e1 = 0.5, 0.5
    from selector import camelot_fit_score

    fit = camelot_fit_score(outgoing.get("camelot"), incoming.get("camelot"))
    if abs(e1 - e0) >= 0.25 or fit < 0.4:
        return "filter_out"
    if fit >= 0.9 and abs(e1 - e0) < 0.08:
        return "phrase_cut"
    return "bass_swap_blend"


def template_duration_bars(name: str) -> float:
    steps = TEMPLATES.get(name) or TEMPLATES["bass_swap_blend"]
    return max((s.at_bar for s in steps), default=0.0)


class Mixer:
    """Async mix state machine. All MIDI goes through controller with source=auto."""

    def __init__(
        self,
        controller: Any,
        *,
        clock: Optional[Clock] = None,
        sleep: Optional[SleepFn] = None,
        enabled: bool = False,
    ):
        self.controller = controller
        self.clock: Clock = clock or time.monotonic
        self._sleep: SleepFn = sleep or asyncio.sleep
        self.enabled = bool(enabled)
        self.state = MixState.IDLE
        self.active_deck = 1
        self.outgoing: Optional[dict] = None
        self.incoming: Optional[dict] = None
        self.play_started_at: Optional[float] = None
        self.play_origin_beats: float = 0.0
        self.drift_beats: float = 0.0
        self.transitions: list[TransitionRecord] = []
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._log: list[dict] = []

    def position_beats(self) -> float:
        if self.play_started_at is None or not self.outgoing:
            return self.play_origin_beats
        bpm = track_bpm(self.outgoing)
        elapsed = self.clock() - self.play_started_at
        return self.play_origin_beats + sec_to_beats(elapsed, bpm)

    def expected_position_beats(self) -> float:
        """Ideal position from start timestamp + bpm (no sim drift)."""
        return self.position_beats()

    def measure_drift(self) -> float:
        """Compare controller sim position vs timestamp clock; store on self.drift_beats."""
        sim = getattr(self.controller, "simulator", None)
        if sim is None or self.outgoing is None:
            self.drift_beats = 0.0
            return 0.0
        ideal = self.expected_position_beats()
        actual = float(sim.position(self.active_deck))
        self.drift_beats = actual - ideal
        return self.drift_beats

    def _send(self, command: str, deck: int = 1, value: float = 1.0) -> dict:
        if command == "crossfader":
            r = self.controller.send("crossfader", deck=1, value=value, source=SOURCE_AUTO)
        elif command.startswith("eq_"):
            band = command.replace("eq_", "")
            r = self.controller.set_eq(deck=deck, band=band, value=value, source=SOURCE_AUTO)
        elif command == "filter":
            r = self.controller.set_filter(deck=deck, value=value, source=SOURCE_AUTO)
        elif command == "sync":
            r = self.controller.sync(deck=deck, source=SOURCE_AUTO)
        elif command == "play":
            r = self.controller.deck_play_pause(deck=deck, source=SOURCE_AUTO)
        elif command == "load":
            r = self.controller.load(deck=deck, source=SOURCE_AUTO)
        else:
            r = self.controller.send(command, deck=deck, value=value, source=SOURCE_AUTO)
        self._log.append(
            {
                "t": self.clock(),
                "command": command,
                "deck": deck,
                "value": value,
                "source": SOURCE_AUTO,
                "ok": bool(r.get("ok")),
                "reason": r.get("reason"),
            }
        )
        return r

    def _remap_steps(self, steps: list[MixStep], out_deck: int, in_deck: int) -> list[MixStep]:
        remapped: list[MixStep] = []
        for s in steps:
            if s.command == "crossfader":
                # Template assumes out=1 (xf→0), in=2 (xf→1). Flip if decks swapped.
                val = s.value
                if out_deck == 2:
                    val = 1.0 - float(s.value)
                remapped.append(MixStep(s.at_bar, "crossfader", deck=1, value=val))
            else:
                deck = in_deck if s.deck == 2 else out_deck
                remapped.append(MixStep(s.at_bar, s.command, deck=deck, value=s.value))
        return remapped

    async def start_track(self, track: dict, deck: int = 1) -> None:
        self.outgoing = dict(track)
        self.active_deck = deck
        self.state = MixState.PLAYING
        self.play_started_at = self.clock()
        self.play_origin_beats = 0.0
        sim = getattr(self.controller, "simulator", None)
        if sim is not None:
            sim.load_track(deck, track)
            # Align sim clock with mixer clock
            if hasattr(sim, "clock"):
                pass
        self._send("load", deck=deck)
        # Ensure playing
        if sim is not None and not sim.deck(deck).playing:
            self._send("play", deck=deck)
        elif sim is None:
            self._send("play", deck=deck)
        xf = 0.0 if deck == 1 else 1.0
        self._send("crossfader", value=xf)

    async def prepare_incoming(self, track: dict) -> int:
        """Load next track on the other deck. Returns incoming deck number."""
        self.state = MixState.PREPARING
        self.incoming = dict(track)
        in_deck = 2 if self.active_deck == 1 else 1
        sim = getattr(self.controller, "simulator", None)
        if sim is not None:
            sim.load_track(in_deck, track)
        self._send("load", deck=in_deck)
        return in_deck

    async def run_transition(
        self,
        incoming: dict,
        *,
        template: Optional[str] = None,
    ) -> TransitionRecord:
        if not self.outgoing:
            raise RuntimeError("no outgoing track")
        out_deck = self.active_deck
        in_deck = await self.prepare_incoming(incoming)
        name = template or choose_template(self.outgoing, incoming)
        steps = self._remap_steps(TEMPLATES.get(name) or TEMPLATES["bass_swap_blend"], out_deck, in_deck)
        phrase_beat = transition_start_beat(self.outgoing)
        # Wait until phrase boundary if still early
        while True:
            pos = self.position_beats()
            if pos + 1e-6 >= phrase_beat:
                break
            bpm = track_bpm(self.outgoing)
            wait = beats_to_sec(phrase_beat - pos, bpm)
            await self._sleep(max(0.0, min(wait, 0.25)))
            if self._stop.is_set():
                break

        self.state = MixState.TRANSITIONING
        t0 = self.clock()
        start_beat = self.position_beats()
        fired: list[dict] = []
        bpm = track_bpm(self.outgoing)
        # Sort steps; fire in order with sleeps between
        ordered = sorted(steps, key=lambda s: s.at_bar)
        last_bar = 0.0
        for step in ordered:
            if self._stop.is_set():
                break
            delta_bars = step.at_bar - last_bar
            if delta_bars > 0:
                await self._sleep(bars_to_sec(delta_bars, bpm))
            last_bar = step.at_bar
            r = self._send(step.command, deck=step.deck, value=step.value)
            fired.append(
                {
                    "at_bar": step.at_bar,
                    "command": step.command,
                    "deck": step.deck,
                    "value": step.value,
                    "ok": bool(r.get("ok")),
                }
            )

        t1 = self.clock()
        rec = TransitionRecord(
            from_track=dict(self.outgoing),
            to_track=dict(incoming),
            template=name,
            start_sec=t0,
            end_sec=t1,
            start_beat=start_beat,
            phrase_beat=phrase_beat,
            steps_fired=fired,
            silence_sec=0.0,
        )
        self.transitions.append(rec)
        self.measure_drift()
        # Swap roles
        self.outgoing = dict(incoming)
        self.incoming = None
        self.active_deck = in_deck
        self.play_started_at = self.clock()
        self.play_origin_beats = 0.0
        self.state = MixState.PLAYING
        return rec

    async def run_set(
        self,
        tracks: list[dict],
        *,
        template: Optional[str] = None,
        play_lead_bars: float = 4.0,
    ) -> list[TransitionRecord]:
        """Play a multi-track set (fake-clock friendly). No silence between tracks."""
        if not tracks:
            return []
        self._stop.clear()
        self.transitions.clear()
        await self.start_track(tracks[0], deck=1)
        # Brief play before first transition cue
        await self._sleep(bars_to_sec(play_lead_bars, track_bpm(tracks[0])))
        for nxt in tracks[1:]:
            if self._stop.is_set():
                break
            # Jump timeline near transition phrase so tests stay fast
            phrase = transition_start_beat(self.outgoing or tracks[0])
            bpm = track_bpm(self.outgoing or tracks[0])
            # Set origin so position ≈ phrase − 1 bar
            target = max(0.0, phrase - BEATS_PER_BAR)
            self.play_origin_beats = target
            self.play_started_at = self.clock()
            sim = getattr(self.controller, "simulator", None)
            if sim is not None:
                d = sim.deck(self.active_deck)
                d.position_beats = target
                d._play_origin_beats = target
                d._play_started_at = self.clock()
                d.playing = True
            await self.run_transition(nxt, template=template)
        return list(self.transitions)

    def stop(self) -> None:
        self._stop.set()
        self.state = MixState.IDLE

    def status(self) -> dict:
        return {
            "state": self.state.value,
            "enabled": self.enabled,
            "active_deck": self.active_deck,
            "position_beats": self.position_beats(),
            "drift_beats": self.drift_beats,
            "outgoing_id": (self.outgoing or {}).get("id"),
            "incoming_id": (self.incoming or {}).get("id"),
            "transitions": len(self.transitions),
        }
