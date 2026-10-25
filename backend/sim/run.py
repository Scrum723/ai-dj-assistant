"""CLI: replay a set through the mix engine (sim) and print score table + JSON."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Allow `python -m sim.run` from backend/ or repo root
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _fixture_tracks(n: int = 10) -> list[dict]:
    tracks = []
    for i in range(n):
        bpm = 128.0 + (i % 3) - 1
        camelot = f"{(i % 12) + 1}{'A' if i % 2 == 0 else 'B'}"
        energy = round(0.35 + (i / max(1, n - 1)) * 0.45, 3)
        # 96 bars @ bpm ≈ duration; outro at last 32 bars
        from mixer import bars_to_sec

        dur = bars_to_sec(96, bpm)
        outro = bars_to_sec(96 - 32, bpm)
        # phrases every 16 bars
        phrases = []
        for b in range(0, 97, 16):
            phrases.append({"start": bars_to_sec(b, bpm), "end": bars_to_sec(min(b + 16, 96), bpm), "bars": 16})
        tracks.append(
            {
                "id": i + 1,
                "filename": f"Sim_{i + 1:02d}.mp3",
                "bpm": bpm,
                "camelot": camelot,
                "key": "Am",
                "energy": energy,
                "duration_sec": dur,
                "outro_sec": outro,
                "phrases": phrases,
            }
        )
    return tracks


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += float(sec)


async def _run_sim(
    tracks: list[dict],
    *,
    template: Optional[str] = None,
    automation: bool = True,
) -> dict:
    from dj_controller import DJController
    from mixer import Mixer
    from settings import save
    from sim.score import score_set

    clock = FakeClock()

    async def fake_sleep(dt: float) -> None:
        clock.advance(max(0.0, float(dt)))

    # Enable automation so source=auto sends succeed
    try:
        save({"dj": {"automation": bool(automation), "manual_takeover": False}})
    except Exception:
        pass

    ctl = DJController(backend="sim")
    # Share clock with simulator
    if ctl.simulator is not None:
        ctl.simulator.clock = clock

    mix = Mixer(ctl, clock=clock, sleep=fake_sleep, enabled=True)
    transitions = await mix.run_set(tracks, template=template, play_lead_bars=2.0)
    report = score_set(tracks, transitions)
    report["mixer"] = mix.status()
    report["wall_clock_sec"] = 0.0
    report["sim_clock_sec"] = clock()
    return report


def _print_table(report: dict) -> None:
    rows = report.get("transitions") or []
    headers = ("#", "from", "to", "score", "grade", "phrase", "bpmΔ%", "camelot", "bass_s")
    print("  ".join(f"{h:>8}" for h in headers))
    print("-" * 88)
    for i, r in enumerate(rows, 1):
        print(
            f"{i:>8}  "
            f"{str(r.get('from'))[:12]:>8}  "
            f"{str(r.get('to'))[:12]:>8}  "
            f"{r.get('score'):>8}  "
            f"{r.get('grade'):>8}  "
            f"{str(r.get('phrase_aligned')):>8}  "
            f"{r.get('bpm_delta_pct'):>8}  "
            f"{str(r.get('camelot_ok')):>8}  "
            f"{r.get('bass_clash_sec'):>8}"
        )
    print("-" * 88)
    print(
        f"average={report.get('average')}  grade={report.get('grade')}  "
        f"silence_sec={report.get('silence_sec')}  aligned={report.get('all_phrase_aligned')}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="DJ mix sim + transition scoring")
    p.add_argument("--replay", type=str, default=None, help="JSON/CSV replay path")
    p.add_argument("--tracks", type=int, default=10, help="Synthetic track count if no replay")
    p.add_argument("--template", type=str, default=None, choices=["bass_swap_blend", "filter_out", "phrase_cut"])
    p.add_argument("--json", type=str, default=None, help="Write full report JSON to path (or '-' for stdout)")
    p.add_argument("--no-table", action="store_true")
    args = p.parse_args(argv)

    tracks: list[dict]
    if args.replay:
        from sim.replay import entries_to_tracks, load_replay

        entries = load_replay(args.replay)
        tracks = entries_to_tracks(entries)
        # Fill missing structure for mixer
        from mixer import bars_to_sec

        for t in tracks:
            bpm = float(t.get("bpm") or 128.0)
            if not t.get("duration_sec"):
                t["duration_sec"] = bars_to_sec(96, bpm)
            if t.get("outro_sec") is None:
                t["outro_sec"] = bars_to_sec(64, bpm)
            if not t.get("phrases"):
                t["phrases"] = [
                    {"start": bars_to_sec(b, bpm), "end": bars_to_sec(b + 16, bpm), "bars": 16}
                    for b in range(0, 97, 16)
                ]
    else:
        tracks = _fixture_tracks(max(2, int(args.tracks)))

    t0 = time.perf_counter()
    report = asyncio.run(_run_sim(tracks, template=args.template))
    report["wall_clock_sec"] = round(time.perf_counter() - t0, 4)

    if not args.no_table:
        _print_table(report)
    if args.json:
        payload = json.dumps(report, indent=2, default=str)
        if args.json == "-":
            print(payload)
        else:
            Path(args.json).write_text(payload, encoding="utf-8")
            if not args.no_table:
                print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
