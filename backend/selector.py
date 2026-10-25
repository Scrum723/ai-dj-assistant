"""Next-track selection: BPM / Camelot / energy plan / recency / request bonus."""

from __future__ import annotations

import re
from typing import Any, Optional

DEFAULT_WEIGHTS = {
    "bpm_fit": 1.0,
    "camelot_fit": 1.0,
    "energy_fit": 1.0,
    "recency": 1.0,
    "request_bonus": 1.5,
}

# Target mean energy (0–1 scale) and preferred step from current.
PLANS: dict[str, dict[str, float]] = {
    "warmup": {"target": 0.35, "step": 0.06},
    "build": {"target": 0.55, "step": 0.08},
    "peak": {"target": 0.85, "step": 0.0},
    "cooldown": {"target": 0.28, "step": -0.08},
}

DEFAULT_RECENCY_N = 8
_CAMELOT_RE = re.compile(r"^(\d{1,2})([AB])$", re.I)


def normalize_camelot(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    m = _CAMELOT_RE.match(s)
    if not m:
        return None
    num = int(m.group(1))
    if num < 1 or num > 12:
        return None
    return f"{num}{m.group(2).upper()}"


def camelot_distance(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Wheel steps between two Camelot codes (same letter), or None if incomparable."""
    ca, cb = normalize_camelot(a), normalize_camelot(b)
    if not ca or not cb:
        return None
    na, la = int(ca[:-1]), ca[-1]
    nb, lb = int(cb[:-1]), cb[-1]
    if la != lb:
        return None
    return min((na - nb) % 12, (nb - na) % 12)


def bpm_ratio_score(current_bpm: float, candidate_bpm: float) -> float:
    """1.0 when candidate matches current, half, or double within ~6%."""
    if current_bpm <= 0 or candidate_bpm <= 0:
        return 0.0
    ratio = candidate_bpm / current_bpm
    best = min(abs(ratio - t) for t in (0.5, 1.0, 2.0))
    # 0.06 ≈ 6% tolerance at unity
    return max(0.0, 1.0 - best / 0.08)


def camelot_fit_score(current: Optional[str], candidate: Optional[str]) -> float:
    ca, cb = normalize_camelot(current), normalize_camelot(candidate)
    if not ca or not cb:
        return 0.35  # unknown — neutral-low
    if ca == cb:
        return 1.0
    na, la = int(ca[:-1]), ca[-1]
    nb, lb = int(cb[:-1]), cb[-1]
    if la == lb:
        dist = min((na - nb) % 12, (nb - na) % 12)
        if dist == 1:
            return 0.9
        if dist == 2:
            return 0.45
        return 0.1
    # Relative major/minor (same number, flip letter)
    if na == nb:
        return 0.75
    dist = min((na - nb) % 12, (nb - na) % 12)
    if dist == 1:
        return 0.4
    return 0.1


def _energy_of(track: dict) -> float:
    try:
        e = float(track.get("energy") if track.get("energy") is not None else 0.5)
    except (TypeError, ValueError):
        e = 0.5
    return max(0.0, min(1.0, e))


def energy_fit_score(
    current: dict,
    candidate: dict,
    plan_name: str,
) -> float:
    plan = PLANS.get(plan_name) or PLANS["build"]
    cur_e = _energy_of(current)
    cand_e = _energy_of(candidate)
    desired = cur_e + float(plan["step"])
    # Pull toward plan target as well
    desired = 0.6 * desired + 0.4 * float(plan["target"])
    desired = max(0.0, min(1.0, desired))
    err = abs(cand_e - desired)
    return max(0.0, 1.0 - err / 0.45)


def _track_id(track: dict) -> Any:
    return track.get("id")


def _title(track: dict) -> str:
    return str(track.get("filename") or track.get("title") or f"track {_track_id(track)}")


def score(current: dict, candidate: dict, ctx: Optional[dict] = None) -> dict:
    """
    Score candidate vs current under ctx.
    Returns {score, parts, reasons, excluded}.
    """
    ctx = ctx or {}
    weights = {**DEFAULT_WEIGHTS, **(ctx.get("weights") or {})}
    plan = str(ctx.get("plan") or "build")
    recent_ids = list(ctx.get("recent_ids") or [])
    recency_n = int(ctx.get("recency_n") or DEFAULT_RECENCY_N)
    request_ids = set(ctx.get("request_ids") or [])

    cid = _track_id(candidate)
    cur_id = _track_id(current)
    if cid is not None and cur_id is not None and cid == cur_id:
        return {
            "score": -999.0,
            "parts": {},
            "reasons": ["Same as current"],
            "excluded": True,
        }

    window = recent_ids[-recency_n:] if recency_n > 0 else []
    if cid is not None and cid in window:
        return {
            "score": -999.0,
            "parts": {"recency": -10.0},
            "reasons": [f"Played in last {recency_n}"],
            "excluded": True,
        }

    try:
        cur_bpm = float(current.get("bpm") or 0)
    except (TypeError, ValueError):
        cur_bpm = 0.0
    try:
        cand_bpm = float(candidate.get("bpm") or 0)
    except (TypeError, ValueError):
        cand_bpm = 0.0

    bpm_s = bpm_ratio_score(cur_bpm, cand_bpm)
    cam_s = camelot_fit_score(current.get("camelot"), candidate.get("camelot"))
    eng_s = energy_fit_score(current, candidate, plan)
    req = 1.0 if cid is not None and cid in request_ids else 0.0

    parts = {
        "bpm_fit": bpm_s * float(weights.get("bpm_fit", 1.0)),
        "camelot_fit": cam_s * float(weights.get("camelot_fit", 1.0)),
        "energy_fit": eng_s * float(weights.get("energy_fit", 1.0)),
        "request_bonus": req * float(weights.get("request_bonus", 1.5)),
        "recency": 0.0,
    }
    total = sum(parts.values())

    reasons: list[str] = []
    if req:
        reasons.append("Chat requested this")
    if bpm_s >= 0.85 and cur_bpm and cand_bpm:
        reasons.append(f"BPM fits ({cand_bpm:.0f} near {cur_bpm:.0f})")
    elif bpm_s >= 0.6 and cur_bpm and cand_bpm:
        reasons.append(f"BPM workable ({cand_bpm:.0f} vs {cur_bpm:.0f})")
    ca, cb = normalize_camelot(current.get("camelot")), normalize_camelot(candidate.get("camelot"))
    if ca and cb:
        if ca == cb:
            reasons.append(f"Same Camelot {cb}")
        elif camelot_distance(ca, cb) == 1:
            reasons.append(f"Camelot neighbour {ca} → {cb}")
        elif ca[:-1] == cb[:-1]:
            reasons.append(f"Relative key {ca} → {cb}")
        elif cam_s >= 0.4:
            reasons.append(f"Key ok {ca} → {cb}")
    if eng_s >= 0.7:
        reasons.append(f"Fits {plan} energy")
    elif eng_s >= 0.45:
        reasons.append(f"Energy ok for {plan}")
    if not reasons:
        reasons.append("Best available from library")

    return {
        "score": total,
        "parts": parts,
        "reasons": reasons,
        "excluded": False,
    }


def pick(
    current: dict,
    candidates: list[dict],
    ctx: Optional[dict] = None,
    k: int = 3,
) -> list[dict]:
    """Return top-k scored candidates with reasons (excluded filtered out)."""
    ctx = ctx or {}
    scored: list[dict] = []
    for cand in candidates:
        result = score(current, cand, ctx)
        if result.get("excluded"):
            continue
        scored.append(
            {
                "track": cand,
                "score": result["score"],
                "parts": result["parts"],
                "reasons": result["reasons"],
                "reason": "; ".join(result["reasons"]),
            }
        )
    scored.sort(key=lambda x: (-x["score"], str(_title(x["track"]))))
    return scored[: max(0, int(k))]


def plan_names() -> list[str]:
    return list(PLANS.keys())
