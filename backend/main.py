import asyncio
import logging
import os
import socket
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from auth import COOKIE, require_operator, require_room_mod, set_email_opt_in, user_from_cookie
from auth import router as auth_router
from llm import get_provider
from cohost import CohostAgent
from community.aggregator import Aggregator
from community.adapters.facebook import FacebookAdapter
from community.adapters.kick import KickAdapter, verify_kick_signature
from community.adapters.rumble import RumbleAdapter
from community.adapters.tiktok import TikTokAdapter
from community.adapters.twitch import TwitchAdapter
from community.adapters.x import XAdapter
from community.adapters.youtube import YouTubeAdapter
from community.adapters.youtube_official import YouTubeOfficialAdapter
from community.identity import IdentityStore, get_identity, set_identity
from community.mod import CommunityMod, get_mod, set_mod
from community.relay import CommunityRelay
from community.schema import ChatMessage, ChatUser
from community.store import CommunityStore, DEFAULT_RETENTION_DAYS
from dj_controller import DJController, MIDI_MAP
from mixer import Mixer
from ops import (
    allow_ip,
    check_adapter_down_alerts,
    configure_json_logging,
    cors_origins,
    data_dir,
    init_sentry,
    maybe_daily_backup,
    new_request_id,
)
from selector import pick as selector_pick
from selector import plan_names as selector_plan_names
from sentinel import is_killed
from sentinel import router as sentinel_router
from settings import load as load_settings
from settings import public_settings, save as save_settings
from trainer import idle_trainer
from voice import MAC_VOICES, resolve_voice_file, synthesize
from watchdog import Watchdog

configure_json_logging()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
init_sentry()

dj = DJController(port_name="AI_DJ_Virtual_Port")
# Automation off by default — enable via POST /dj/automation
mixer = Mixer(dj, enabled=False)
watchdog = Watchdog(dj, mixer)


def _on_manual_takeover() -> None:
    mixer.pause()
    push_log("Manual takeover — mixer paused (foreign MIDI / controller)")


dj.set_takeover_callback(_on_manual_takeover)
mixer.on_heartbeat = watchdog.tick_heartbeat
DB_PATH = str(data_dir() / "library.db")
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
EQ_BANDS = frozenset({"low", "mid", "hi"})

agent: Optional[CohostAgent] = None
aggregator: Optional[Aggregator] = None
community_store: Optional[CommunityStore] = None
community_mod: Optional[CommunityMod] = None
community_relay: Optional[CommunityRelay] = None
speech_queue: asyncio.Queue
agent_task: Optional[asyncio.Task] = None
speech_task: Optional[asyncio.Task] = None
presence_task: Optional[asyncio.Task] = None
_main_loop: Optional[asyncio.AbstractEventLoop] = None


class Hub:
    def __init__(self, *, send_state_on_connect: bool = True):
        self.clients: list[WebSocket] = []
        self.send_state_on_connect = send_state_on_connect

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)
        if self.send_state_on_connect:
            await ws.send_json({"type": "state", "payload": snapshot()})

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, message: dict):
        dead: list[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = Hub()
room_hub = Hub(send_state_on_connect=False)

state: dict[str, Any] = {
    "playing": False,
    "current_track": None,
    "queue": [],
    "recent_track_ids": [],
    "next_suggestions": [],
    "dj_plan": "build",
    "autonomy": 80,
    "crossfader": 0.0,
    "active_deck": 1,
    "deck_eq": {
        1: {"low": 0.5, "mid": 0.5, "hi": 0.5},
        2: {"low": 0.5, "mid": 0.5, "hi": 0.5},
    },
    "deck_filter": {1: 0.5, 2: 0.5},
    "midi_connected": dj.connected,
    "midi_port": dj.port_name,
    "midi_error": dj.error,
    "dj_backend": dj.backend,
    "speak_id": 0,
    "speak_text": "",
    "speak_target": None,
    "speak_mood": "idle",
    "speak_audio": None,
    "log": [],
    "chat": [],
    "highlight": None,
}
_request_seq = 0


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_TRACK_COLS = "id, filepath, filename, bpm, key, energy, camelot"


def all_tracks() -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    conn = db_connect()
    try:
        rows = conn.execute(
            f"SELECT {_TRACK_COLS} FROM tracks ORDER BY filename"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            "SELECT id, filepath, filename, bpm, key, energy FROM tracks ORDER BY filename"
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def search_tracks(query: str) -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    conn = db_connect()
    like = f"%{query.strip()}%"
    try:
        rows = conn.execute(
            f"SELECT {_TRACK_COLS} FROM tracks "
            "WHERE filename LIKE ? OR filepath LIKE ? ORDER BY filename",
            (like, like),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            "SELECT id, filepath, filename, bpm, key, energy FROM tracks "
            "WHERE filename LIKE ? OR filepath LIKE ? ORDER BY filename",
            (like, like),
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _remember_played(track: Optional[dict]) -> None:
    if not track or track.get("id") is None:
        return
    tid = track["id"]
    recent = state.setdefault("recent_track_ids", [])
    if recent and recent[-1] == tid:
        return
    recent.append(tid)
    # Keep a little more than max recency window
    if len(recent) > 64:
        del recent[:-64]


def _request_track_ids() -> set:
    ids: set = set()
    for item in state.get("queue") or []:
        track = item.get("track") if isinstance(item, dict) else None
        if track and track.get("id") is not None:
            ids.add(track["id"])
        elif isinstance(item, dict) and item.get("id") is not None and item.get("found"):
            # queue item may itself be a matched track-shaped dict
            pass
    return ids


def build_next_picks(*, plan: Optional[str] = None, k: int = 3) -> dict:
    cfg = load_settings()
    dj_cfg = cfg.get("dj") or {}
    plan_name = plan or state.get("dj_plan") or dj_cfg.get("plan") or "build"
    if plan_name not in selector_plan_names():
        plan_name = "build"
    current = state.get("current_track")
    tracks = all_tracks()
    if not current:
        current = tracks[0] if tracks else None
    if not current:
        return {"plan": plan_name, "picks": [], "current": None}
    weights = dj_cfg.get("weights") or {}
    recency_n = int(dj_cfg.get("recency_n") or 8)
    ctx = {
        "weights": weights,
        "plan": plan_name,
        "recent_ids": list(state.get("recent_track_ids") or []),
        "recency_n": recency_n,
        "request_ids": _request_track_ids(),
    }
    ranked = selector_pick(current, tracks, ctx=ctx, k=k)
    picks = []
    for row in ranked:
        t = row["track"]
        picks.append(
            {
                "id": t.get("id"),
                "filename": t.get("filename"),
                "bpm": t.get("bpm"),
                "key": t.get("key"),
                "camelot": t.get("camelot"),
                "energy": t.get("energy"),
                "score": round(float(row["score"]), 4),
                "reasons": row["reasons"],
                "reason": row["reason"],
            }
        )
    state["next_suggestions"] = picks
    state["dj_plan"] = plan_name
    return {"plan": plan_name, "picks": picks, "current": current}


def compatible_keys(key: Optional[str]) -> list[str]:
    if not key or key not in KEYS:
        return []
    i = KEYS.index(key)
    return [KEYS[i], KEYS[(i + 7) % 12], KEYS[(i + 5) % 12]]


def lan_ips() -> list[str]:
    found: set[str] = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.add(probe.getsockname()[0])
        probe.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                found.add(ip)
    except Exception:
        pass
    return sorted(found)


def room_urls() -> dict:
    port = int(os.getenv("PORT") or os.getenv("HALO_PORT") or "8000")
    local = f"http://127.0.0.1:{port}/booth/?tab=room"
    lan = [f"http://{ip}:{port}/booth/?tab=room" for ip in lan_ips()]
    return {"local": local, "lan": lan, "short": f"http://127.0.0.1:{port}/room"}


def snapshot() -> dict:
    current = state["current_track"]
    key = current.get("key") if current else None
    cfg = public_settings()
    dj_cfg = (cfg.get("dj") or {})
    sim_snap = None
    if getattr(dj, "simulator", None) is not None:
        try:
            sim_snap = dj.simulator.snapshot()
        except Exception:
            sim_snap = None
    return {
        **state,
        "track_count": len(all_tracks()),
        "compatible_keys": compatible_keys(key),
        "midi_map": MIDI_MAP,
        "midi_connected": dj.connected,
        "midi_error": dj.error,
        "dj_backend": dj.backend,
        "manual_takeover": bool(dj_cfg.get("manual_takeover", False)),
        "automation": bool(dj_cfg.get("automation", False)),
        "mixer": mixer.status(),
        "watchdog": watchdog.status(),
        "alerts": list(watchdog.alerts),
        "sim": sim_snap,
        "agent": agent.status() if agent else {"running": False},
        "settings": cfg,
        "room_urls": room_urls(),
    }


def push_log(message: str):
    state["log"] = ([{"t": message}] + state["log"])[:40]


def _schedule(coro) -> None:
    """Schedule on the app loop from request handlers or adapter threads."""
    loop = _main_loop
    if loop is None or not loop.is_running():
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
            return
        except RuntimeError:
            return
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception:
        pass


def broadcast(message: dict):
    _schedule(hub.broadcast(message))


def room_broadcast(message: dict):
    _schedule(room_hub.broadcast(message))


def publish_state():
    broadcast({"type": "state", "payload": snapshot()})


def note_chat(item: dict):
    state["chat"] = (state["chat"] + [item])[-40:]
    publish_state()


def publish_highlight(payload: dict):
    """Supporter shoutout card → overlay (/ws) and room (/ws/room)."""
    state["highlight"] = payload
    publish_state()
    msg = {"type": "highlight", "payload": payload}
    broadcast(msg)
    room_broadcast(msg)


def emit_speak(text: str, target: Optional[str] = None, mood: str = "talk", audio_url: Optional[str] = None):
    state["speak_id"] += 1
    state["speak_text"] = text
    state["speak_target"] = target
    state["speak_mood"] = mood
    state["speak_audio"] = audio_url
    push_log(f"Halo: {text}")
    publish_state()
    broadcast(
        {
            "type": "speak",
            "text": text,
            "id": state["speak_id"],
            "target": target,
            "mood": mood,
            "audio_url": audio_url,
        }
    )


def queued_speak(text: str, target: Optional[str] = None, mood: str = "talk"):
    item = {"text": text, "target": target, "mood": mood}
    try:
        speech_queue.put_nowait(item)
    except asyncio.QueueFull:
        if mood == "idle":
            return
        try:
            _ = speech_queue.get_nowait()
        except Exception:
            return
        try:
            speech_queue.put_nowait(item)
        except Exception:
            pass


def enqueue_request(
    query: str,
    requester: str,
    via: str,
    silent: bool = False,
    *,
    priority: bool = False,
    profile_id: Any = None,
) -> dict:
    global _request_seq
    results = search_tracks(query)
    _request_seq += 1
    item = {
        "id": _request_seq,
        "query": query,
        "requester": requester,
        "via": via,
        "found": bool(results),
        "track": results[0] if results else None,
        "filename": results[0]["filename"] if results else query,
        "bpm": results[0]["bpm"] if results else None,
        "key": results[0]["key"] if results else None,
        "priority": bool(priority),
        "profile_id": profile_id,
    }
    if priority:
        idx = 0
        while idx < len(state["queue"]) and state["queue"][idx].get("priority"):
            idx += 1
        state["queue"].insert(idx, item)
    else:
        state["queue"].append(item)
    if results:
        push_log(f"Queued {item['filename']} for {requester}")
        speak_text = f"Queued {item['filename']} for {requester}!"
        status = "queued"
    else:
        push_log(f"No library match for '{query}' from {requester}")
        speak_text = f"Sorry {requester}, I couldn't find {query} in the library."
        status = "not_found"
    if not silent:
        queued_speak(speak_text, target=requester, mood="hype")
    else:
        publish_state()
    return {"status": status, "item": item, "results": results}


async def speech_worker():
    while True:
        item = await speech_queue.get()
        if is_killed():
            continue
        text = item["text"]
        cfg = load_settings()
        # Old full-mute; DJ mode itself is quiet (selective replies) in cohost
        if (cfg.get("halo") or {}).get("dj_mode_silent"):
            continue
        audio_url = None
        try:
            audio_url = await asyncio.to_thread(synthesize, text)
        except Exception:
            logger.exception("Voice synthesize failed")
        emit_speak(text, item.get("target"), item.get("mood") or "talk", audio_url=audio_url)
        words = max(1, len(text.split()))
        await asyncio.sleep(min(14.0, max(3.5, words * 0.38 + 1.1)))


def _retention_days(cfg: Optional[dict] = None) -> int:
    cfg = cfg if cfg is not None else load_settings()
    community = cfg.get("community") or {}
    try:
        return max(1, int(community.get("retention_days") or DEFAULT_RETENTION_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def presence_payload() -> dict:
    sockets = len(room_hub.clients)
    if community_store:
        return community_store.presence(room_sockets=sockets)
    return {
        "ts": time.time(),
        "ttl_sec": 300.0,
        "counts": {"room": sockets},
        "users": [],
        "total": sockets,
    }


async def presence_worker():
    while True:
        try:
            await asyncio.sleep(10.0)
            room_broadcast({"type": "presence", "payload": presence_payload()})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("presence broadcast failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, aggregator, community_store, community_mod, speech_queue, agent_task, speech_task, presence_task, _main_loop
    _main_loop = asyncio.get_running_loop()
    try:
        maybe_daily_backup()
    except Exception:
        logger.exception("daily backup failed")
    speech_queue = asyncio.Queue(maxsize=12)
    speech_task = asyncio.create_task(speech_worker())
    cfg = load_settings()
    state["autonomy"] = int(cfg.get("autonomy") or 70)
    community_store = CommunityStore(retention_days=_retention_days(cfg))
    try:
        community_store.prune(retention_days=_retention_days(cfg))
    except Exception:
        logger.exception("community store prune failed")
    agent = CohostAgent(
        get_snapshot=snapshot,
        search_tracks=search_tracks,
        enqueue_request=enqueue_request,
        speak=queued_speak,
        note_chat=note_chat,
        get_autonomy=lambda: int(load_settings().get("autonomy") or 70),
        get_settings=load_settings,
    )
    identity_store = IdentityStore()
    set_identity(identity_store)

    def _on_linked(info: dict) -> None:
        try:
            agent.memory.attach_profile(
                info.get("platform") or "chat",
                info.get("handle") or "",
                info.get("user_id"),
            )
        except Exception:
            logger.exception("memory attach on link failed")

    community_mod = CommunityMod(
        db_path=community_store.db_path,
        get_settings=load_settings,
        identity=identity_store,
        broadcast=room_broadcast,
    )
    set_mod(community_mod)

    aggregator = Aggregator(
        cohost_ingest=agent.ingest,
        note_chat=note_chat,
        room_broadcast=room_broadcast,
        get_settings=load_settings,
        store=community_store,
        identity=identity_store,
        mod=community_mod,
        on_linked=_on_linked,
        memory=agent.memory,
    )
    community_mod.set_get_adapters(aggregator.adapters_map)
    community_relay = CommunityRelay(
        get_settings=load_settings,
        get_adapters=aggregator.adapters_map,
    )
    aggregator.set_relay(community_relay)
    yt_channel = ""
    community = cfg.get("community") or {}
    adapters_cfg = community.get("adapters") or {}
    yt_cfg = adapters_cfg.get("youtube") or {}
    tw_cfg = adapters_cfg.get("twitch") or {}
    kick_cfg = adapters_cfg.get("kick") or {}
    fb_cfg = adapters_cfg.get("facebook") or {}
    rumble_cfg = adapters_cfg.get("rumble") or {}
    yt_off_cfg = adapters_cfg.get("youtube_official") or {}
    yt_channel = str(yt_cfg.get("channel") or cfg.get("youtube_id") or "").strip()
    aggregator.register(YouTubeAdapter(channel=yt_channel))
    aggregator.register(
        TwitchAdapter(
            channel=str(tw_cfg.get("channel") or ""),
            bot_token=str(tw_cfg.get("bot_token") or ""),
            bot_nick=str(tw_cfg.get("bot_nick") or ""),
        )
    )
    aggregator.register(
        KickAdapter(
            channel=str(kick_cfg.get("channel") or ""),
            access_token=str(kick_cfg.get("access_token") or ""),
            broadcaster_user_id=kick_cfg.get("broadcaster_user_id"),
        )
    )
    aggregator.register(
        FacebookAdapter(
            channel=str(fb_cfg.get("live_video_id") or fb_cfg.get("channel") or ""),
            page_access_token=str(fb_cfg.get("page_access_token") or ""),
            poll_interval_sec=float(fb_cfg.get("poll_interval_sec") or 3),
        )
    )
    aggregator.register(
        RumbleAdapter(
            channel=str(rumble_cfg.get("api_url") or rumble_cfg.get("channel") or ""),
            api_url=str(rumble_cfg.get("api_url") or rumble_cfg.get("channel") or ""),
            poll_interval_sec=float(rumble_cfg.get("poll_interval_sec") or 3),
        )
    )

    def _yt_official_quota_fallback() -> None:
        """When official quota is exhausted, start pytchat youtube adapter if configured."""
        if not aggregator:
            return
        s = load_settings()
        ycfg = ((s.get("community") or {}).get("adapters") or {}).get("youtube") or {}
        vid = str(ycfg.get("channel") or s.get("youtube_id") or "").strip()
        if not vid:
            return
        # Enable pytchat path without requiring settings flip if channel known
        yt = aggregator.get_adapter("youtube")
        if yt and not yt.status().get("running"):
            try:
                yt.set_channel(vid)
                yt.start(aggregator.ingest)
                logger.warning("youtube_official quota exhausted — fell back to pytchat youtube adapter")
            except Exception:
                logger.exception("pytchat fallback start failed")

    aggregator.register(
        YouTubeOfficialAdapter(
            channel=str(yt_off_cfg.get("channel") or cfg.get("youtube_id") or ""),
            api_key=str(yt_off_cfg.get("api_key") or ""),
            live_chat_id=str(yt_off_cfg.get("live_chat_id") or ""),
            oauth_token=str(yt_off_cfg.get("oauth_token") or ""),
            daily_quota=int(yt_off_cfg.get("daily_quota") or 10000),
            on_quota_exhausted=_yt_official_quota_fallback,
        )
    )

    operator_alerts: list[dict] = []

    def _operator_alert(payload: dict) -> None:
        operator_alerts.append(payload)
        if len(operator_alerts) > 50:
            del operator_alerts[:-50]
        try:
            room_broadcast({"type": "operator_alert", "payload": payload})
        except Exception:
            logger.exception("operator_alert broadcast failed")
        try:
            if aggregator:
                aggregator.ingest(
                    ChatMessage(
                        id=f"system:alert:{payload.get('adapter')}:{time.time()}",
                        platform="halo",
                        channel="booth",
                        user=ChatUser(
                            platform_user_id="system",
                            handle="system",
                            display_name="System",
                            is_owner=True,
                        ),
                        text=str(payload.get("message") or "Experimental adapter alert"),
                        kind="system",
                        ts=float(payload.get("ts") or time.time()),
                    )
                )
        except Exception:
            logger.exception("operator_alert ingest failed")

    def _auto_disable_adapter(name: str, reason: str) -> None:
        logger.warning("auto-disabling experimental adapter %s: %s", name, reason)
        try:
            save_settings({"community": {"adapters": {name: {"enabled": False}}}})
        except Exception:
            logger.exception("failed to persist auto-disable for %s", name)
        if aggregator:
            try:
                aggregator.configure_adapter(name, enabled=False)
            except Exception:
                logger.exception("configure_adapter disable failed for %s", name)

    tt_cfg = adapters_cfg.get("tiktok") or {}
    x_cfg = adapters_cfg.get("x") or {}
    aggregator.register(
        TikTokAdapter(
            channel=str(tt_cfg.get("username") or tt_cfg.get("channel") or ""),
            username=str(tt_cfg.get("username") or tt_cfg.get("channel") or ""),
            on_auto_disable=_auto_disable_adapter,
            on_alert=_operator_alert,
        )
    )
    aggregator.register(
        XAdapter(
            channel=str(x_cfg.get("post_id") or x_cfg.get("channel") or ""),
            post_id=str(x_cfg.get("post_id") or x_cfg.get("channel") or ""),
            bearer_token=str(x_cfg.get("bearer_token") or ""),
            can_post=bool(x_cfg.get("can_post")),
            poll_interval_sec=float(x_cfg.get("poll_interval_sec") or 15),
            on_auto_disable=_auto_disable_adapter,
            on_alert=_operator_alert,
        )
    )
    aggregator._operator_alerts = operator_alerts  # type: ignore[attr-defined]

    def _on_youtube_change(video_id: Optional[str]) -> None:
        vid = (video_id or "").strip()
        save_settings({"youtube_id": vid or "", "community": {"adapters": {"youtube": {"channel": vid}}}})
        if aggregator:
            # Preserve enabled flag from settings; only update channel.
            enabled = bool(((load_settings().get("community") or {}).get("adapters") or {}).get("youtube", {}).get("enabled"))
            aggregator.configure_adapter("youtube", enabled=enabled, channel=vid)
            off_enabled = bool(
                ((load_settings().get("community") or {}).get("adapters") or {})
                .get("youtube_official", {})
                .get("enabled")
            )
            aggregator.configure_adapter("youtube_official", enabled=off_enabled, channel=vid)

    agent.set_youtube_hook(_on_youtube_change)

    def _publish_halo_reply(author: str, text: str) -> None:
        if aggregator:
            aggregator.publish_halo(text, name=author)

    agent.set_publish_halo_hook(_publish_halo_reply)
    agent.set_presence_hook(presence_payload)
    agent.set_highlight_hook(publish_highlight)
    agent.set_demo(bool(cfg.get("demo")))
    if cfg.get("youtube_id"):
        agent.youtube_id = str(cfg.get("youtube_id") or "").strip() or None
    aggregator.start()  # adapters disabled by default in settings
    agent_task = asyncio.create_task(agent.run())
    presence_task = asyncio.create_task(presence_worker())
    trainer_stop = asyncio.Event()
    trainer_task = asyncio.create_task(idle_trainer(snapshot, trainer_stop))
    tracks = all_tracks()
    if tracks and not state["current_track"]:
        state["current_track"] = tracks[0]

    # Optional MIDI input (Mac controller); no-op if port unset / mido missing
    midi_in = ((cfg.get("dj") or {}).get("midi_in_port") or os.getenv("DJ_MIDI_IN_PORT") or "").strip()
    if midi_in:
        started = dj.start_midi_input(midi_in)
        if started.get("ok"):
            logger.info("MIDI input: %s", started.get("port"))
        else:
            logger.info(
                "MIDI input skipped (%s). Blocked on Charles: controller, BlackHole, rekordbox.",
                started.get("reason"),
            )
    else:
        logger.info(
            "C5 safety: MIDI in unset. Blocked on Charles: controller, BlackHole, rekordbox."
        )

    async def _watchdog_worker() -> None:
        while True:
            try:
                watchdog.check()
            except Exception:
                logger.exception("watchdog tick failed")
            await asyncio.sleep(0.2)

    async def _adapter_health_worker() -> None:
        while True:
            try:
                await asyncio.sleep(30.0)
                if not aggregator:
                    continue
                st = (aggregator.status() or {}).get("adapters") or {}
                check_adapter_down_alerts(st, on_alert=_operator_alert)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("adapter health watch failed")

    watchdog_task = asyncio.create_task(_watchdog_worker())
    adapter_health_task = asyncio.create_task(_adapter_health_worker())
    logger.info("DJ Bot Botty online. Halo LLM buddy started. Community aggregator ready.")
    yield
    trainer_stop.set()
    dj.stop_midi_input()
    if aggregator:
        aggregator.stop()
    if agent:
        agent.stop()
    for task in (agent_task, speech_task, trainer_task, presence_task, watchdog_task, adapter_health_task):
        if task:
            task.cancel()


app = FastAPI(title="DJ Bot Botty", lifespan=lifespan)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """JSON-friendly request id + IP rate limits on public chat/auth routes."""

    RATE_PREFIXES = ("/chat", "/request", "/auth")

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_request_id()
        request.state.request_id = rid
        path = request.url.path or ""
        if any(path == p or path.startswith(p + "/") for p in self.RATE_PREFIXES):
            from auth import _client_ip

            ip = _client_ip(request)
            if not allow_ip(ip, path=path):
                return JSONResponse(
                    {"detail": "rate limit", "request_id": rid},
                    status_code=429,
                    headers={"X-Request-Id": rid},
                )
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response


app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(sentinel_router)


class CrossfadeRequest(BaseModel):
    value: float = Field(ge=0.0, le=1.0)
    source: str = "manual"


class DeckRequest(BaseModel):
    deck: int = Field(default=1, ge=1, le=2)
    source: str = "manual"


class AutonomyRequest(BaseModel):
    value: int = Field(ge=0, le=100)


class SongRequest(BaseModel):
    query: str
    requester: str = "Fan"
    via: str = "Web Hub"
    silent: bool = False


class SpeakRequest(BaseModel):
    text: str
    target: Optional[str] = None
    mood: str = "talk"


class LoadTrackRequest(BaseModel):
    track_id: int
    deck: int = Field(default=1, ge=1, le=2)
    source: str = "manual"


class EqRequest(BaseModel):
    deck: int = Field(default=1, ge=1, le=2)
    band: str
    value: float = Field(ge=0.0, le=1.0)
    source: str = "manual"


class FilterRequest(BaseModel):
    deck: int = Field(default=1, ge=1, le=2)
    value: float = Field(ge=0.0, le=1.0)
    source: str = "manual"


class SyncRequest(BaseModel):
    deck: int = Field(default=1, ge=1, le=2)
    source: str = "manual"


class TakeoverRequest(BaseModel):
    enabled: bool = True


class AutomationRequest(BaseModel):
    enabled: bool = False


class DjPlanRequest(BaseModel):
    plan: str = "build"


class ChatIn(BaseModel):
    author: str = "Fan"
    message: str
    platform: str = "chat"


class DemoRequest(BaseModel):
    enabled: bool = True


class YoutubeRequest(BaseModel):
    video_id: Optional[str] = None


class DjModeRequest(BaseModel):
    enabled: bool


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DASH_DIST = os.path.join(ROOT_DIR, "frontend-dashboard", "dist")
OVER_DIST = os.path.join(ROOT_DIR, "frontend-overlay", "dist")
if not os.path.isdir(OVER_DIST):
    OVER_DIST = os.path.join(ROOT_DIR, "backend", "frontend-overlay", "dist")


@app.get("/room")
def room_redirect():
    return RedirectResponse("/booth/?tab=room")


@app.get("/")
def read_root():
    if os.path.isdir(os.path.join(DASH_DIST, "assets")):
        return RedirectResponse("/booth/")
    return {
        "status": "DJ Bot Botty is online.",
        "product": "DJ Bot Botty",
        "cohost": (load_settings().get("halo") or {}).get("name") or "Halo",
        "midi_connected": dj.connected,
        "dashboard": "http://127.0.0.1:5173",
        "overlay": "http://127.0.0.1:5174",
        "booth": "http://127.0.0.1:8000/booth/",
        "room": "http://127.0.0.1:8000/booth/?tab=room",
    }


@app.get("/settings")
def get_settings_route():
    return public_settings()


@app.post("/settings")
def update_settings_route(patch: dict, _auth: None = Depends(require_operator)):
    current = load_settings()
    halo_patch = patch.get("halo") if isinstance(patch.get("halo"), dict) else None
    if halo_patch is not None:
        halo_patch.pop("elevenlabs_key_set", None)
        if not halo_patch.get("elevenlabs_key"):
            halo_patch["elevenlabs_key"] = current["halo"].get("elevenlabs_key") or ""
    # Preserve redacted adapter secrets when client sends empty / *_set flags
    community_patch = patch.get("community") if isinstance(patch.get("community"), dict) else None
    if community_patch and isinstance(community_patch.get("adapters"), dict):
        cur_adapters = (current.get("community") or {}).get("adapters") or {}
        secret_keys = ("access_token", "page_access_token", "api_key", "api_url", "bearer_token")
        for name, acfg in community_patch["adapters"].items():
            if not isinstance(acfg, dict):
                continue
            prev = cur_adapters.get(name) or {}
            for sk in secret_keys:
                acfg.pop(f"{sk}_set", None)
                if not acfg.get(sk) and prev.get(sk):
                    acfg[sk] = prev[sk]
    data = save_settings(patch)
    if agent:
        if "demo" in patch:
            agent.set_demo(bool(data.get("demo")))
        if "youtube_id" in patch:
            agent.set_youtube(data.get("youtube_id") or None)
    if aggregator and community_patch and "adapters" in community_patch:
        try:
            aggregator.start()
        except Exception:
            logger.exception("adapter restart after settings save failed")
    state["autonomy"] = int(data.get("autonomy") or 70)
    publish_state()
    return public_settings(data)


@app.get("/voices")
def list_voices():
    return {"macos": MAC_VOICES}


@app.get("/voice/{name}")
def voice_file(name: str):
    path = resolve_voice_file(name)
    if not path:
        return {"error": "not found"}
    media = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"
    return FileResponse(str(path), media_type=media)


@app.post("/agent/dj")
def set_dj_mode(req: DjModeRequest, _auth: None = Depends(require_operator)):
    data = save_settings({"halo": {"dj_mode": req.enabled}})
    publish_state()
    return {"dj_mode": data["halo"]["dj_mode"]}


@app.get("/state")
def get_state():
    return snapshot()


@app.get("/tracks")
def get_tracks():
    if not os.path.exists(DB_PATH):
        return {"error": "Database not initialized. Run: python3 backend/audio_analyzer.py", "tracks": []}
    return all_tracks()


@app.get("/search")
def search_song(query: str):
    return {"results": search_tracks(query)}


def _midi_result_or_killed(result: dict) -> Optional[dict]:
    if not result.get("ok"):
        reason = result.get("reason")
        if reason == "killed":
            return {"status": "killed"}
        return {"status": "refused", "reason": reason, "midi": result}
    return None


@app.post("/action/play")
def play_deck(req: DeckRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    result = dj.deck_play_pause(deck=req.deck, source=req.source)
    refused = _midi_result_or_killed(result)
    if refused and refused.get("status") == "killed":
        return refused
    if refused and result.get("reason") in ("automation_off", "manual_takeover"):
        return refused
    if not state["current_track"]:
        queue = state["queue"]
        tracks = all_tracks()
        if queue:
            item = queue.pop(0)
            state["current_track"] = item.get("track") or item
        elif tracks:
            state["current_track"] = tracks[0]
    state["playing"] = not state["playing"]
    state["active_deck"] = req.deck
    push_log(f"{'Playing' if state['playing'] else 'Paused'} deck {req.deck}")
    publish_state()
    return {
        "status": f"Toggled deck {req.deck}",
        "playing": state["playing"],
        "midi_connected": dj.connected,
        "midi": result,
    }


@app.post("/action/skip")
def skip_track(_auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    if state["queue"]:
        _remember_played(state.get("current_track"))
        item = state["queue"].pop(0)
        state["current_track"] = item.get("track") or item
        state["playing"] = True
        result = dj.deck_play_pause(deck=1, source="manual")
        if getattr(dj, "simulator", None) is not None:
            dj.simulator.load_track(1, state["current_track"])
        push_log(f"Skipped to {state['current_track'].get('filename', 'next track')}")
        if agent:
            title = state["current_track"].get("filename", "the next track")
            queued_speak(f"Skipping ahead — {title} coming up.", mood="hype")
        publish_state()
        return {"status": "skipped", "current_track": state["current_track"], "midi": result}
    publish_state()
    return {"status": "skipped", "current_track": state["current_track"]}


@app.post("/action/load")
def load_track(req: LoadTrackRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    tracks = [t for t in all_tracks() if t["id"] == req.track_id]
    if not tracks:
        raise HTTPException(status_code=404, detail="Track not found")
    track = tracks[0]
    # browse + load to deck (MIDI), then update booth state
    dj.browse(deck=req.deck, direction="down", source=req.source)
    result = dj.load(deck=req.deck, source=req.source)
    refused = _midi_result_or_killed(result)
    if refused and refused.get("status") == "killed":
        return refused
    if refused and result.get("reason") in ("automation_off", "manual_takeover"):
        return refused
    prev = state.get("current_track")
    if prev and prev.get("id") != track.get("id"):
        _remember_played(prev)
    state["current_track"] = track
    state["active_deck"] = req.deck
    if getattr(dj, "simulator", None) is not None:
        dj.simulator.load_track(req.deck, track)
    push_log(f"Loaded {track['filename']} → deck {req.deck}")
    publish_state()
    return {"status": "loaded", "current_track": track, "deck": req.deck, "midi": result}


@app.get("/dj/next")
def dj_next(
    plan: Optional[str] = None,
    k: int = 3,
    announce: bool = False,
    _auth: None = Depends(require_operator),
):
    """Top-k next-track suggestions with plain-word reasons."""
    if is_killed():
        return {"status": "killed"}
    k = max(1, min(10, int(k or 3)))
    if plan:
        if plan not in selector_plan_names():
            raise HTTPException(status_code=422, detail=f"plan must be one of {selector_plan_names()}")
        save_settings({"dj": {"plan": plan}})
    result = build_next_picks(plan=plan, k=k)
    picks = result["picks"]
    announced = False
    if announce and picks:
        cfg = load_settings()
        halo = cfg.get("halo") or {}
        # Respect DJ quiet / full mute — no booth chatter while Charles is mixing quiet
        if not halo.get("dj_mode_silent") and not halo.get("dj_mode"):
            top = picks[0]
            title = top.get("filename") or "the next track"
            reason = top.get("reason") or "best fit"
            queued_speak(f"Next up suggestion: {title}. {reason}.", mood="hype")
            announced = True
    publish_state()
    return {
        "status": "ok",
        "plan": result["plan"],
        "plans": selector_plan_names(),
        "picks": picks,
        "announced": announced,
        "current": {
            "id": (result["current"] or {}).get("id"),
            "filename": (result["current"] or {}).get("filename"),
            "bpm": (result["current"] or {}).get("bpm"),
            "key": (result["current"] or {}).get("key"),
            "camelot": (result["current"] or {}).get("camelot"),
            "energy": (result["current"] or {}).get("energy"),
        }
        if result["current"]
        else None,
    }


@app.post("/dj/plan")
def set_dj_plan(req: DjPlanRequest, _auth: None = Depends(require_operator)):
    if req.plan not in selector_plan_names():
        raise HTTPException(status_code=422, detail=f"plan must be one of {selector_plan_names()}")
    save_settings({"dj": {"plan": req.plan}})
    state["dj_plan"] = req.plan
    publish_state()
    return {"status": "ok", "plan": req.plan}


@app.post("/action/crossfade")
def crossfade(req: CrossfadeRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    result = dj.crossfader(req.value, source=req.source)
    refused = _midi_result_or_killed(result)
    if refused and refused.get("status") == "killed":
        return refused
    if refused and result.get("reason") in ("automation_off", "manual_takeover"):
        return refused
    state["crossfader"] = req.value
    publish_state()
    return {"status": f"Crossfader moved to {req.value}", "midi": result}


@app.post("/action/eq")
def action_eq(req: EqRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    band = (req.band or "").lower().strip()
    if band not in EQ_BANDS:
        raise HTTPException(status_code=422, detail="band must be low, mid, or hi")
    result = dj.set_eq(deck=req.deck, band=band, value=req.value, source=req.source)
    refused = _midi_result_or_killed(result)
    if refused and refused.get("status") == "killed":
        return refused
    if refused and result.get("reason") in ("automation_off", "manual_takeover", "bad_band", "bad_deck"):
        if result.get("reason") in ("bad_band", "bad_deck"):
            raise HTTPException(status_code=422, detail=result.get("reason"))
        return refused
    state["deck_eq"][req.deck][band] = req.value
    publish_state()
    return {"status": "ok", "deck": req.deck, "band": band, "value": req.value, "midi": result}


@app.post("/action/filter")
def action_filter(req: FilterRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    result = dj.set_filter(deck=req.deck, value=req.value, source=req.source)
    refused = _midi_result_or_killed(result)
    if refused and refused.get("status") == "killed":
        return refused
    if refused and result.get("reason") in ("automation_off", "manual_takeover"):
        return refused
    state["deck_filter"][req.deck] = req.value
    publish_state()
    return {"status": "ok", "deck": req.deck, "value": req.value, "midi": result}


@app.post("/action/sync")
def action_sync(req: SyncRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    result = dj.sync(deck=req.deck, source=req.source)
    refused = _midi_result_or_killed(result)
    if refused and refused.get("status") == "killed":
        return refused
    if refused and result.get("reason") in ("automation_off", "manual_takeover"):
        return refused
    push_log(f"Sync deck {req.deck}")
    publish_state()
    return {"status": "ok", "deck": req.deck, "midi": result}


@app.post("/action/panic")
def action_panic(_auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    result = dj.panic(source="manual")
    if not result.get("ok") and result.get("reason") == "killed":
        return {"status": "killed"}
    state["deck_eq"] = {
        1: {"low": 0.5, "mid": 0.5, "hi": 0.5},
        2: {"low": 0.5, "mid": 0.5, "hi": 0.5},
    }
    state["deck_filter"] = {1: 0.5, 2: 0.5}
    state["crossfader"] = float(result.get("crossfader", 0.5))
    push_log("PANIC — EQ/filter reset, fader centred on playing deck")
    publish_state()
    return {"status": "ok", "midi": result}


@app.post("/action/takeover")
def action_takeover(req: TakeoverRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    save_settings({"dj": {"manual_takeover": bool(req.enabled)}})
    push_log(f"Manual takeover {'ON' if req.enabled else 'OFF'}")
    publish_state()
    return {"status": "ok", "manual_takeover": bool(req.enabled)}


@app.post("/dj/automation")
def set_dj_automation(req: AutomationRequest, _auth: None = Depends(require_operator)):
    """Operator toggle for mix automation. Default off; Mixer sends use source=auto."""
    if is_killed():
        return {"status": "killed"}
    enabled = bool(req.enabled)
    save_settings({"dj": {"automation": enabled}})
    mixer.enabled = enabled
    if not enabled:
        mixer.stop()
    push_log(f"DJ automation {'ON' if enabled else 'OFF'}")
    publish_state()
    return {"status": "ok", "automation": enabled, "mixer": mixer.status()}


@app.post("/action/autonomy")
def set_autonomy(req: AutonomyRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    state["autonomy"] = req.value
    save_settings({"autonomy": req.value})
    publish_state()
    return {"status": "ok", "autonomy": req.value}


@app.post("/request")
def request_song(
    req: SongRequest,
    djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE),
):
    user = user_from_cookie(djbb_session)
    priority = bool(user and (user.get("perks") or {}).get("request_priority"))
    requester = req.requester
    profile_id = None
    if user:
        requester = user.get("display_name") or requester
        profile_id = user.get("id")
        priority = priority or bool((user.get("perks") or {}).get("request_priority"))
    result = enqueue_request(
        req.query,
        requester,
        req.via,
        silent=req.silent,
        priority=priority,
        profile_id=profile_id,
    )
    if agent and not req.silent:
        agent.ingest(
            requester,
            f"requested {req.query}",
            platform=req.via,
            profile_id=profile_id,
        )
    return result


@app.post("/speak")
def speak(req: SpeakRequest):
    queued_speak(req.text, target=req.target, mood=req.mood)
    return {"status": "ok"}


@app.get("/me/profile")
def me_profile(djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE)):
    user = user_from_cookie(djbb_session)
    if not user:
        raise HTTPException(401, "Sign in required")
    identity = get_identity()
    links = identity.list_links(int(user["id"]))
    retention = {"visits": 0, "streak": 0, "badges": []}
    if agent and getattr(agent, "memory", None) is not None:
        try:
            retention = agent.memory.retention_for(profile_id=user["id"])
        except Exception:
            logger.exception("retention lookup failed")
    return {
        "user": user,
        "linked_handles": links,
        "visits": retention.get("visits", 0),
        "streak": retention.get("streak", 0),
        "badges": retention.get("badges") or [],
        "retention": retention,
    }


@app.post("/me/link-code")
def me_link_code(djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE)):
    user = user_from_cookie(djbb_session)
    if not user:
        raise HTTPException(401, "Sign in required")
    identity = get_identity()
    return identity.create_link_code(int(user["id"]))


class UnlinkIn(BaseModel):
    platform: str
    handle: str


@app.post("/me/unlink")
def me_unlink(body: UnlinkIn, djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE)):
    user = user_from_cookie(djbb_session)
    if not user:
        raise HTTPException(401, "Sign in required")
    identity = get_identity()
    ok = identity.unlink(int(user["id"]), body.platform, body.handle)
    return {"ok": ok, "linked_handles": identity.list_links(int(user["id"]))}


class NotifyIn(BaseModel):
    email_opt_in: bool = False


@app.post("/me/notify")
def me_notify(body: NotifyIn, djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE)):
    """Store going-live email opt-in only — no mail is sent in B8."""
    user = user_from_cookie(djbb_session)
    if not user:
        raise HTTPException(401, "Sign in required")
    updated = set_email_opt_in(int(user["id"]), bool(body.email_opt_in))
    if not updated:
        raise HTTPException(404, "User not found")
    return {"ok": True, "email_opt_in": bool(updated.get("email_opt_in")), "user": updated}


def _parse_show_start(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def next_show_payload(cfg: Optional[dict] = None, *, now: Optional[datetime] = None) -> dict:
    cfg = cfg if cfg is not None else load_settings()
    now = now or datetime.now(timezone.utc)
    shows = cfg.get("shows") or []
    upcoming: list[tuple[datetime, dict]] = []
    for item in shows:
        if not isinstance(item, dict):
            continue
        start = _parse_show_start(str(item.get("start_iso") or ""))
        if start is None:
            continue
        if start >= now:
            upcoming.append((start, {"start_iso": start.isoformat().replace("+00:00", "Z"), "title": str(item.get("title") or "Live set")}))
    upcoming.sort(key=lambda x: x[0])
    nxt = upcoming[0][1] if upcoming else None
    return {"next": nxt, "shows": [u[1] for u in upcoming[:10]]}


@app.get("/shows/next")
def shows_next():
    return next_show_payload()


@app.get("/healthz")
def healthz():
    """Railway / cloud health — always 200; midi may be false without mido."""
    midi_ok = bool(getattr(dj, "connected", False))
    llm_ok = False
    try:
        if agent and getattr(agent, "_llm", None) is not None:
            llm_ok = bool(agent._llm.available)
        else:
            llm_ok = bool(get_provider().available)
    except Exception:
        llm_ok = False
    adapters = {}
    if aggregator:
        try:
            adapters = (aggregator.status() or {}).get("adapters") or {}
        except Exception:
            adapters = {}
    return {
        "ok": True,
        "midi": midi_ok,
        "llm": llm_ok,
        "adapters": adapters,
    }


@app.post("/chat")
def incoming_chat(
    req: ChatIn,
    djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE),
):
    platform = (req.platform or "room").strip().lower() or "room"
    if platform in ("chat", "dashboard", "terminal", "web"):
        # Treat generic web/dashboard as room for the unified stream.
        if platform in ("chat", "web"):
            platform = "room"
    user = user_from_cookie(djbb_session)
    author = req.author
    profile_id = None
    if user and platform == "room":
        author = user.get("display_name") or author
        profile_id = user.get("id")
    msg = ChatMessage.make_room(author=author, text=req.message)
    if profile_id is not None:
        msg.profile_id = int(profile_id)
        msg.user = ChatUser(
            platform_user_id=f"user:{profile_id}",
            handle=author,
            display_name=author,
        )
    if platform != "room":
        msg.platform = platform
        msg.id = f"{platform}:{msg.user.handle}:{msg.ts}"
        msg.channel = platform
    if aggregator:
        accepted = aggregator.ingest(msg)
        return {"status": "ok" if accepted else "dropped", "profile_id": msg.profile_id}
    if not agent:
        return {"error": "cohost offline"}
    agent.ingest(author, req.message, platform=platform, profile_id=profile_id)
    return {"status": "ok", "profile_id": profile_id}


@app.get("/metrics")
def halo_metrics(_auth: None = Depends(require_operator)):
    if not agent:
        return {
            "calls_today": 0,
            "p50_ms": 0,
            "p95_ms": 0,
            "est_cost_usd": 0.0,
            "tokens_est": 0,
        }
    return agent.metrics()


@app.get("/community/status")
def community_status():
    if not aggregator:
        return {"started": False, "adapters": {}}
    return aggregator.status()


@app.post("/community/webhooks/kick")
async def kick_webhook(
    request: Request,
    kick_event_message_id: Optional[str] = Header(default=None, alias="Kick-Event-Message-Id"),
    kick_event_message_timestamp: Optional[str] = Header(default=None, alias="Kick-Event-Message-Timestamp"),
    kick_event_signature: Optional[str] = Header(default=None, alias="Kick-Event-Signature"),
    kick_event_type: Optional[str] = Header(default=None, alias="Kick-Event-Type"),
):
    """Kick Events API webhook — verifies RSA signature then maps to ChatMessage."""
    body = await request.body()
    if not kick_event_message_id or not kick_event_message_timestamp or not kick_event_signature:
        raise HTTPException(401, "missing Kick signature headers")
    # Tests may set KICK_SKIP_SIGNATURE=1; production must verify.
    skip = os.getenv("KICK_SKIP_SIGNATURE", "").strip() in {"1", "true", "yes"}
    if not skip:
        ok = verify_kick_signature(
            message_id=kick_event_message_id,
            timestamp=kick_event_message_timestamp,
            body=body,
            signature_b64=kick_event_signature,
        )
        if not ok:
            raise HTTPException(401, "invalid Kick signature")
    try:
        import json as _json

        payload = _json.loads(body.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    if not aggregator:
        return {"status": "ok", "accepted": False, "reason": "aggregator offline"}
    kick = aggregator.get_adapter("kick")
    if kick is None or not isinstance(kick, KickAdapter):
        return {"status": "ok", "accepted": False, "reason": "kick adapter missing"}
    if not kick.status().get("running"):
        # Auto-start when webhook arrives and adapter enabled in settings
        acfg = ((load_settings().get("community") or {}).get("adapters") or {}).get("kick") or {}
        if bool(acfg.get("enabled")):
            try:
                kick.start(aggregator.ingest)
            except Exception:
                logger.exception("kick webhook start failed")
        else:
            return {"status": "ok", "accepted": False, "reason": "kick disabled"}
    msg = kick.handle_webhook_payload(payload, event_type=kick_event_type or "")
    return {"status": "ok", "accepted": bool(msg), "id": msg.id if msg else None}


@app.get("/community/history")
def community_history(before: Optional[float] = None, limit: int = 100):
    """Public scrollback — client payloads only (no raw)."""
    limit = max(1, min(int(limit or 100), 200))
    if community_store:
        return {"messages": community_store.history(before=before, limit=limit)}
    if aggregator:
        items = aggregator.recent(limit)
        if before is not None:
            items = [m for m in items if float(m.get("ts") or 0) < float(before)]
        return {"messages": items[-limit:]}
    return {"messages": []}


@app.get("/community/presence")
def community_presence():
    return presence_payload()


@app.get("/community/supporters/today")
def community_supporters_today():
    """Daily supporter wall across platforms (grouped by profile when linked)."""
    if community_store:
        return {"supporters": community_store.supporters_today(), "day": time.strftime("%Y-%m-%d", time.gmtime())}
    return {"supporters": [], "day": time.strftime("%Y-%m-%d", time.gmtime())}


@app.get("/community/listening-from")
def community_listening_from():
    """Opt-in 'where from' map for Room (text viewers volunteered)."""
    if agent:
        return {
            "listening_from": agent._listening_from_list(),
            "current_segment": (agent._segments.status().get("current")),
            "community_name": ((agent._cfg().get("community") or {}).get("name") or "") or None,
        }
    return {"listening_from": [], "current_segment": None, "community_name": None}


class ModTargetIn(BaseModel):
    target_profile_id: Optional[int] = None
    platform: str = ""
    handle: str = ""
    reason: str = ""
    seconds: Optional[int] = None
    push: Optional[bool] = None


class ModDeleteIn(BaseModel):
    message_id: str
    reason: str = ""
    platform: str = ""
    handle: str = ""
    target_profile_id: Optional[int] = None


def _mod_or_503() -> CommunityMod:
    mod = get_mod() or community_mod
    if not mod:
        raise HTTPException(503, "moderation offline")
    return mod


@app.post("/community/mod/ban")
def community_mod_ban(body: ModTargetIn, actor: dict = Depends(require_room_mod)):
    mod = _mod_or_503()
    try:
        return mod.ban(
            target_profile_id=body.target_profile_id,
            platform=body.platform,
            handle=body.handle,
            until=None,
            reason=body.reason,
            by_user_id=actor.get("user_id"),
            by_name=str(actor.get("display_name") or "mod"),
            push=body.push,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/community/mod/timeout")
def community_mod_timeout(body: ModTargetIn, actor: dict = Depends(require_room_mod)):
    mod = _mod_or_503()
    secs = int(body.seconds or 600)
    try:
        return mod.timeout(
            seconds=secs,
            target_profile_id=body.target_profile_id,
            platform=body.platform,
            handle=body.handle,
            reason=body.reason,
            by_user_id=actor.get("user_id"),
            by_name=str(actor.get("display_name") or "mod"),
            push=body.push,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/community/mod/unban")
def community_mod_unban(body: ModTargetIn, actor: dict = Depends(require_room_mod)):
    mod = _mod_or_503()
    try:
        return mod.unban(
            target_profile_id=body.target_profile_id,
            platform=body.platform,
            handle=body.handle,
            reason=body.reason,
            by_user_id=actor.get("user_id"),
            by_name=str(actor.get("display_name") or "mod"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/community/mod/delete")
def community_mod_delete(body: ModDeleteIn, actor: dict = Depends(require_room_mod)):
    mod = _mod_or_503()
    try:
        result = mod.delete_message(
            body.message_id,
            reason=body.reason,
            by_user_id=actor.get("user_id"),
            by_name=str(actor.get("display_name") or "mod"),
            platform=body.platform,
            handle=body.handle,
            target_profile_id=body.target_profile_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if aggregator:
        try:
            aggregator.drop_from_history(body.message_id)
        except Exception:
            logger.exception("drop_from_history failed")
    return result


@app.get("/community/mod/log")
def community_mod_log(limit: int = 100, _auth: None = Depends(require_operator)):
    mod = _mod_or_503()
    return {"log": mod.list_log(limit=limit), "bans": mod.active_bans()}


@app.get("/community/mod/me")
def community_mod_me(djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE)):
    """Whether the current session can use the room mod menu."""
    user = user_from_cookie(djbb_session)
    perks = (user or {}).get("perks") or {}
    is_mod = bool(perks.get("operator") or perks.get("room_mod"))
    # Local booth without OPERATOR_KEY mirrors require_room_mod local path
    if not user and not (os.getenv("OPERATOR_KEY") or "").strip():
        is_mod = True
    return {
        "is_mod": is_mod,
        "user_id": (user or {}).get("id"),
        "display_name": (user or {}).get("display_name"),
        "perks": {"operator": bool(perks.get("operator")), "room_mod": bool(perks.get("room_mod"))},
    }


@app.post("/agent/demo")
def set_demo(req: DemoRequest, _auth: None = Depends(require_operator)):
    if not agent:
        return {"error": "cohost offline"}
    agent.set_demo(req.enabled)
    save_settings({"demo": req.enabled})
    publish_state()
    return {"status": "ok", "demo": agent.demo}


@app.post("/agent/youtube")
def set_youtube(req: YoutubeRequest, _auth: None = Depends(require_operator)):
    if not agent:
        return {"error": "cohost offline"}
    agent.set_youtube(req.video_id)
    publish_state()
    return {"status": "ok", "youtube_id": agent.youtube_id}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)


@app.websocket("/ws/room")
async def room_websocket(ws: WebSocket):
    # IP rate limit on connect (mirrors HTTP /chat limits)
    client = ws.client
    ip = (client.host if client else "") or "unknown"
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("TRUSTED_PROXY", "").lower() in {"1", "true", "yes"}:
        forwarded = ws.headers.get("x-forwarded-for", "")
        if forwarded:
            ip = forwarded.split(",")[0].strip() or ip
    if not allow_ip(ip, path="/ws/room"):
        await ws.close(code=1008)
        return
    await room_hub.connect(ws)
    try:
        if community_store:
            hist = community_store.history(limit=100)
        elif aggregator:
            hist = aggregator.recent(100)
        else:
            hist = []
        await ws.send_json({"type": "history", "payload": hist})
    except Exception:
        pass
    try:
        await ws.send_json({"type": "presence", "payload": presence_payload()})
    except Exception:
        pass
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        room_hub.disconnect(ws)
        room_broadcast({"type": "presence", "payload": presence_payload()})


if os.path.isdir(DASH_DIST):
    app.mount("/booth", StaticFiles(directory=DASH_DIST, html=True), name="booth")
if os.path.isdir(OVER_DIST):
    app.mount("/overlay", StaticFiles(directory=OVER_DIST, html=True), name="overlay")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT") or os.getenv("HALO_PORT") or "8000")
    uvicorn.run(app, host="0.0.0.0", port=port)
