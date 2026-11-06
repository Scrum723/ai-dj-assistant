"""Visualizer remote control (V4): state, command validation, WS hub helpers.

Command set (POST /api/visualizer → broadcast type=vis_cmd):
  scene, mode, next, prev, reactivity, sens, sceneMinutes, autogain, fx,
  projection, strobeSafe (+ confirm when turning off), titleCards,
  nowPlayingCard (+ cardstyle), debug, ui, songSync.

Song-sync (songSync=true): on each track_change the visualizer advances to the
next Story-order scene, resets the act timeline, and sets sceneMinutes from
track duration when known. Optional BPM/energy scene pick (client-side):
  bpm < 110 or energy<=2 → STORY_ORDER indices 0–6 (calm / build)
  bpm 110–132 or energy 3–6 → mid band
  bpm > 132 or energy>=7 → peak / drop scenes
Default songSync path is sequential Story advance (not BPM pick).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

MODES = frozenset({"story", "shuffle", "loop", "rando"})
CMD_KEYS = frozenset(
    {
        "scene",
        "mode",
        "next",
        "prev",
        "reactivity",
        "sens",
        "sceneMinutes",
        "autogain",
        "fx",
        "projection",
        "strobeSafe",
        "titleCards",
        "nowPlayingCard",
        "cardstyle",
        "debug",
        "ui",
        "songSync",
        "confirm",
    }
)

DEFAULT_STATE: dict[str, Any] = {
    "scene": 0,
    "mode": "story",
    "reactivity": 1.2,
    "sens": 1.0,
    "sceneMinutes": 4.0,
    "autogain": True,
    "fx": True,
    "projection": False,
    "strobeSafe": True,
    "titleCards": True,
    "nowPlayingCard": True,
    "cardstyle": "default",
    "debug": False,
    "ui": True,
    "songSync": False,
}


class VisualizerCtl:
    """In-memory visualizer control state + client sockets."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = deepcopy(DEFAULT_STATE)
        self.clients: list[WebSocket] = []
        self.last_status: Optional[dict[str, Any]] = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": dict(self.state),
            "clients": len(self.clients),
            "status": dict(self.last_status) if self.last_status else None,
        }

    def apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """Validate and merge command fields into state. Raises ValueError → 422."""
        if not isinstance(body, dict) or not body:
            raise ValueError("empty command body")

        raw = {k: v for k, v in body.items() if k != "type"}
        unknown = [k for k in raw if k not in CMD_KEYS]
        if unknown:
            raise ValueError(f"unknown command fields: {', '.join(sorted(unknown))}")
        if not raw:
            raise ValueError("no command fields")

        patch: dict[str, Any] = {}

        if "scene" in raw:
            sc = raw["scene"]
            if sc == "R" or sc == "r":
                patch["scene"] = "R"
            else:
                try:
                    idx = int(sc)
                except (TypeError, ValueError) as e:
                    raise ValueError("scene must be int index or 'R'") from e
                if idx < 0:
                    raise ValueError("scene index must be >= 0")
                patch["scene"] = idx

        if "mode" in raw:
            m = str(raw["mode"]).lower()
            if m not in MODES:
                raise ValueError(f"mode must be one of {sorted(MODES)}")
            patch["mode"] = m

        if "next" in raw and raw["next"] not in (False, None, 0, "0", "false"):
            patch["next"] = True
        if "prev" in raw and raw["prev"] not in (False, None, 0, "0", "false"):
            patch["prev"] = True

        if "reactivity" in raw:
            try:
                r = float(raw["reactivity"])
            except (TypeError, ValueError) as e:
                raise ValueError("reactivity must be a number") from e
            if not 0 <= r <= 3:
                raise ValueError("reactivity must be 0–3")
            patch["reactivity"] = r

        if "sens" in raw:
            try:
                s = float(raw["sens"])
            except (TypeError, ValueError) as e:
                raise ValueError("sens must be a number") from e
            if not 0.3 <= s <= 3:
                raise ValueError("sens must be 0.3–3")
            patch["sens"] = s

        if "sceneMinutes" in raw:
            try:
                sm = float(raw["sceneMinutes"])
            except (TypeError, ValueError) as e:
                raise ValueError("sceneMinutes must be a number") from e
            if not 0.5 <= sm <= 15:
                raise ValueError("sceneMinutes must be 0.5–15")
            patch["sceneMinutes"] = sm

        for bool_key in (
            "autogain",
            "projection",
            "titleCards",
            "nowPlayingCard",
            "debug",
            "ui",
            "songSync",
        ):
            if bool_key in raw:
                patch[bool_key] = _as_bool(raw[bool_key], bool_key)

        if "fx" in raw:
            fx = raw["fx"]
            if isinstance(fx, (int, float)) and not isinstance(fx, bool):
                if not 0 <= float(fx) <= 1:
                    raise ValueError("fx level must be 0–1")
                patch["fx"] = float(fx)
            else:
                patch["fx"] = _as_bool(fx, "fx")

        if "strobeSafe" in raw:
            want = _as_bool(raw["strobeSafe"], "strobeSafe")
            if want is False and self.state.get("strobeSafe", True):
                if not _as_bool(raw.get("confirm"), "confirm"):
                    raise ValueError("strobeSafe off requires confirm=true")
            patch["strobeSafe"] = want

        if "cardstyle" in raw:
            cs = raw["cardstyle"]
            if cs is None or not str(cs).strip():
                raise ValueError("cardstyle must be a non-empty string")
            patch["cardstyle"] = str(cs).strip()[:64]

        # Persist durable fields (skip one-shot next/prev)
        for k, v in patch.items():
            if k in ("next", "prev"):
                continue
            self.state[k] = v
        if patch.get("mode") == "rando":
            self.state["scene"] = "R"
        elif "scene" in patch and patch["scene"] != "R" and self.state.get("mode") == "rando":
            self.state["mode"] = "story"

        return patch

    def cmd_message(self, patch: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Build vis_cmd payload: full state, optionally with one-shot keys."""
        msg = {"type": "vis_cmd", **dict(self.state)}
        if patch:
            if patch.get("next"):
                msg["next"] = True
            if patch.get("prev"):
                msg["prev"] = True
            if patch.get("resetAct"):
                msg["resetAct"] = True
        return msg

    def song_sync_patch(self, track: dict[str, Any]) -> Optional[dict[str, Any]]:
        """If songSync on, return a patch that advances scene + optional duration."""
        if not self.state.get("songSync"):
            return None
        patch: dict[str, Any] = {"next": True, "mode": "story", "resetAct": True}
        # duration may be seconds or ms depending on source
        dur = track.get("duration") or track.get("duration_sec") or track.get("length")
        if dur is not None:
            try:
                sec = float(dur)
                if sec > 1000:  # likely ms
                    sec = sec / 1000.0
                mins = max(0.5, min(15.0, round(sec / 60.0 * 2) / 2))
                patch["sceneMinutes"] = mins
                self.state["sceneMinutes"] = mins
            except (TypeError, ValueError):
                pass
        self.state["mode"] = "story"
        return patch

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.append(ws)
        try:
            await ws.send_json(self.cmd_message())
        except Exception:
            pass

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def note_status(self, payload: dict[str, Any]) -> None:
        # Never store audio; only status metadata
        safe = {
            k: payload.get(k)
            for k in ("scene", "mode", "fps", "input", "scenes", "songSync")
            if k in payload
        }
        self.last_status = safe


def _as_bool(v: Any, name: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("1", "true", "on", "yes"):
            return True
        if low in ("0", "false", "off", "no"):
            return False
    raise ValueError(f"{name} must be a boolean")


vis_ctl = VisualizerCtl()
