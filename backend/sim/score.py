"""Transition scorer: beat offset, phrase align, BPM, Camelot, loudness, bass clash."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from selector import camelot_fit_score, normalize_camelot


@dataclass
class TransitionMetrics:
    beat_offset_ms: float = 0.0
    phrase_aligned: bool = False
    bpm_delta_pct: float = 0.0
    camelot_ok: bool = False
    loudness_overlap: float = 0.0  # 0..1 estimated dual-deck energy overlap
    bass_clash_sec: float = 0.0
    score: float = 0.0
    grade: str = "F"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _bpm(track: dict, default: float = 128.0) -> float:
    try:
        b = float(track.get("bpm") or default)
        return b if b > 0 else default
    except (TypeError, ValueError):
        return default


def _energy(track: dict, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(track.get("energy") if track.get("energy") is not None else default)))
    except (TypeError, ValueError):
        return default


def grade_from_score(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def phrase_aligned_from_offset(beat_offset_ms: float, bpm: float, tol_beats: float = 1.0) -> bool:
    """True when absolute beat offset is within tol_beats of a phrase grid beat."""
    if bpm <= 0:
        return abs(beat_offset_ms) < 50
    ms_per_beat = 60_000.0 / bpm
    beats = abs(beat_offset_ms) / ms_per_beat
    return beats <= tol_beats + 1e-6


def score_transition(
    outgoing: dict,
    incoming: dict,
    *,
    beat_offset_ms: float = 0.0,
    phrase_beat: Optional[float] = None,
    transition_start_beat: Optional[float] = None,
    template: Optional[str] = None,
    bass_clash_sec: Optional[float] = None,
    loudness_overlap: Optional[float] = None,
    transition_bars: float = 16.0,
) -> TransitionMetrics:
    """
    Score one transition 0–100.

    Weights (approx): phrase/beat 35, bpm 20, camelot 20, loudness 15, bass clash 10.
    """
    bpm0 = _bpm(outgoing)
    bpm1 = _bpm(incoming)
    bpm_delta_pct = abs(bpm1 - bpm0) / bpm0 * 100.0 if bpm0 else 0.0
    # Half/double are fine
    ratio = bpm1 / bpm0 if bpm0 else 1.0
    if min(abs(ratio - t) for t in (0.5, 1.0, 2.0)) < 0.06:
        bpm_delta_pct = min(bpm_delta_pct, abs(ratio - round(ratio)) * 100 if False else bpm_delta_pct)
        nearest = min((0.5, 1.0, 2.0), key=lambda t: abs(ratio - t))
        bpm_delta_pct = abs(ratio - nearest) / nearest * 100.0

    camelot_s = camelot_fit_score(outgoing.get("camelot") or outgoing.get("key"), incoming.get("camelot") or incoming.get("key"))
    camelot_ok = camelot_s >= 0.75

    # Phrase alignment from provided beats or offset alone
    if phrase_beat is not None and transition_start_beat is not None:
        beat_err = abs(float(transition_start_beat) - float(phrase_beat))
        beat_offset_ms = beat_err * (60_000.0 / bpm0) if bpm0 else beat_offset_ms
    aligned = phrase_aligned_from_offset(beat_offset_ms, bpm0, tol_beats=1.0)

    e0, e1 = _energy(outgoing), _energy(incoming)
    if loudness_overlap is None:
        # Overlap proxy: both loud during blend → higher overlap risk; mid values ok
        loudness_overlap = min(e0, e1) * (0.6 if (template or "") == "phrase_cut" else 1.0)
    loudness_overlap = max(0.0, min(1.0, float(loudness_overlap)))

    if bass_clash_sec is None:
        # bass_swap / phrase_cut manage bass; filter_out less so
        if template == "bass_swap_blend":
            bass_clash_sec = 0.5
        elif template == "phrase_cut":
            bass_clash_sec = 0.0
        elif template == "filter_out":
            bass_clash_sec = 1.5
        else:
            # Unknown: estimate from energy product × blend length
            blend_sec = transition_bars * 4 * 60.0 / bpm0 if bpm0 else 8.0
            bass_clash_sec = blend_sec * e0 * e1 * 0.35
    bass_clash_sec = max(0.0, float(bass_clash_sec))

    # Component scores 0..1
    # beat: full marks within 1 beat; linear to 0 at 4 beats
    ms_per_beat = 60_000.0 / bpm0 if bpm0 else 500.0
    beat_err_beats = abs(beat_offset_ms) / ms_per_beat
    s_beat = max(0.0, 1.0 - beat_err_beats / 4.0)
    if aligned:
        s_beat = max(s_beat, 0.85)

    s_bpm = max(0.0, 1.0 - bpm_delta_pct / 12.0)  # 12% → 0
    s_cam = camelot_s
    # Prefer moderate overlap (some blend) but penalize both-full
    s_loud = 1.0 - abs(loudness_overlap - 0.35) / 0.65
    s_loud = max(0.0, min(1.0, s_loud))
    # bass clash: 0 sec → 1; 4+ sec → 0
    s_bass = max(0.0, 1.0 - bass_clash_sec / 4.0)

    score = 100.0 * (
        0.35 * s_beat
        + 0.20 * s_bpm
        + 0.20 * s_cam
        + 0.15 * s_loud
        + 0.10 * s_bass
    )
    score = round(max(0.0, min(100.0, score)), 1)
    m = TransitionMetrics(
        beat_offset_ms=round(beat_offset_ms, 2),
        phrase_aligned=aligned,
        bpm_delta_pct=round(bpm_delta_pct, 3),
        camelot_ok=camelot_ok,
        loudness_overlap=round(loudness_overlap, 3),
        bass_clash_sec=round(bass_clash_sec, 3),
        score=score,
        grade=grade_from_score(score),
        detail={
            "s_beat": round(s_beat, 3),
            "s_bpm": round(s_bpm, 3),
            "s_cam": round(s_cam, 3),
            "s_loud": round(s_loud, 3),
            "s_bass": round(s_bass, 3),
            "template": template,
            "camelot_from": normalize_camelot(outgoing.get("camelot") or outgoing.get("key")),
            "camelot_to": normalize_camelot(incoming.get("camelot") or incoming.get("key")),
        },
    )
    return m


def score_set(
    tracks: list[dict],
    transitions: Optional[list[dict]] = None,
) -> dict:
    """
    Score consecutive pairs. Optional transitions list from Mixer.transitions
    (TransitionRecord or dicts with phrase_beat / start_beat / template).
    """
    results: list[dict] = []
    n = len(tracks)
    for i in range(n - 1):
        meta: dict[str, Any] = {}
        if transitions and i < len(transitions):
            tr = transitions[i]
            if hasattr(tr, "__dict__") and not isinstance(tr, dict):
                meta = {
                    "phrase_beat": getattr(tr, "phrase_beat", None),
                    "transition_start_beat": getattr(tr, "start_beat", None),
                    "template": getattr(tr, "template", None),
                }
            elif isinstance(tr, dict):
                meta = {
                    "phrase_beat": tr.get("phrase_beat"),
                    "transition_start_beat": tr.get("start_beat") or tr.get("transition_start_beat"),
                    "template": tr.get("template"),
                    "beat_offset_ms": tr.get("beat_offset_ms", 0.0),
                    "bass_clash_sec": tr.get("bass_clash_sec"),
                    "loudness_overlap": tr.get("loudness_overlap"),
                }
        m = score_transition(tracks[i], tracks[i + 1], **{k: v for k, v in meta.items() if v is not None or k == "beat_offset_ms"})
        row = m.to_dict()
        row["from_id"] = tracks[i].get("id")
        row["to_id"] = tracks[i + 1].get("id")
        row["from"] = tracks[i].get("filename") or tracks[i].get("id")
        row["to"] = tracks[i + 1].get("filename") or tracks[i + 1].get("id")
        results.append(row)

    scores = [r["score"] for r in results]
    avg = round(sum(scores) / len(scores), 1) if scores else 0.0
    silence = 0.0
    if transitions:
        for tr in transitions:
            if hasattr(tr, "silence_sec"):
                silence += float(tr.silence_sec or 0)
            elif isinstance(tr, dict):
                silence += float(tr.get("silence_sec") or 0)
    return {
        "transitions": results,
        "average": avg,
        "grade": grade_from_score(avg),
        "count": len(results),
        "silence_sec": silence,
        "all_phrase_aligned": all(r["phrase_aligned"] for r in results) if results else True,
    }
