import asyncio
import logging
import os
import socket
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth import require_operator
from auth import router as auth_router
from cohost import CohostAgent
from community.aggregator import Aggregator
from community.adapters.twitch import TwitchAdapter
from community.adapters.youtube import YouTubeAdapter
from community.schema import ChatMessage
from community.store import CommunityStore, DEFAULT_RETENTION_DAYS
from dj_controller import DJController, MIDI_MAP
from sentinel import is_killed
from sentinel import router as sentinel_router
from settings import load as load_settings
from settings import public_settings, save as save_settings
from trainer import idle_trainer
from voice import MAC_VOICES, resolve_voice_file, synthesize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

dj = DJController(port_name="AI_DJ_Virtual_Port")
DB_PATH = os.path.join(os.path.dirname(__file__), "library.db")
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

agent: Optional[CohostAgent] = None
aggregator: Optional[Aggregator] = None
community_store: Optional[CommunityStore] = None
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
    "autonomy": 80,
    "crossfader": 0.0,
    "midi_connected": dj.connected,
    "midi_port": dj.port_name,
    "midi_error": dj.error,
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


def all_tracks() -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    conn = db_connect()
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
    rows = conn.execute(
        "SELECT id, filepath, filename, bpm, key, energy FROM tracks "
        "WHERE filename LIKE ? OR filepath LIKE ? ORDER BY filename",
        (like, like),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


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
    return {
        **state,
        "track_count": len(all_tracks()),
        "compatible_keys": compatible_keys(key),
        "midi_map": MIDI_MAP,
        "agent": agent.status() if agent else {"running": False},
        "settings": public_settings(),
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


def enqueue_request(query: str, requester: str, via: str, silent: bool = False) -> dict:
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
    }
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
    global agent, aggregator, community_store, speech_queue, agent_task, speech_task, presence_task, _main_loop
    _main_loop = asyncio.get_running_loop()
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
    aggregator = Aggregator(
        cohost_ingest=agent.ingest,
        note_chat=note_chat,
        room_broadcast=room_broadcast,
        get_settings=load_settings,
        store=community_store,
    )
    yt_channel = ""
    community = cfg.get("community") or {}
    adapters_cfg = community.get("adapters") or {}
    yt_cfg = adapters_cfg.get("youtube") or {}
    tw_cfg = adapters_cfg.get("twitch") or {}
    yt_channel = str(yt_cfg.get("channel") or cfg.get("youtube_id") or "").strip()
    aggregator.register(YouTubeAdapter(channel=yt_channel))
    aggregator.register(TwitchAdapter(channel=str(tw_cfg.get("channel") or "")))

    def _on_youtube_change(video_id: Optional[str]) -> None:
        vid = (video_id or "").strip()
        save_settings({"youtube_id": vid or "", "community": {"adapters": {"youtube": {"channel": vid}}}})
        if aggregator:
            # Preserve enabled flag from settings; only update channel.
            enabled = bool(((load_settings().get("community") or {}).get("adapters") or {}).get("youtube", {}).get("enabled"))
            aggregator.configure_adapter("youtube", enabled=enabled, channel=vid)

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
    logger.info("DJ Bot Botty online. Halo LLM buddy started. Community aggregator ready.")
    yield
    trainer_stop.set()
    if aggregator:
        aggregator.stop()
    if agent:
        agent.stop()
    for task in (agent_task, speech_task, trainer_task, presence_task):
        if task:
            task.cancel()


app = FastAPI(title="DJ Bot Botty", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "https://djbotbotty.com",
        "https://www.djbotbotty.com",
        "https://halo-dj-production.up.railway.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(sentinel_router)


class CrossfadeRequest(BaseModel):
    value: float = Field(ge=0.0, le=1.0)


class DeckRequest(BaseModel):
    deck: int = 1


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
    data = save_settings(patch)
    if agent:
        if "demo" in patch:
            agent.set_demo(bool(data.get("demo")))
        if "youtube_id" in patch:
            agent.set_youtube(data.get("youtube_id") or None)
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


@app.post("/action/play")
def play_deck(req: DeckRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    dj.deck_play_pause(deck=req.deck)
    if not state["current_track"]:
        queue = state["queue"]
        tracks = all_tracks()
        if queue:
            item = queue.pop(0)
            state["current_track"] = item.get("track") or item
        elif tracks:
            state["current_track"] = tracks[0]
    state["playing"] = not state["playing"]
    push_log(f"{'Playing' if state['playing'] else 'Paused'} deck {req.deck}")
    publish_state()
    return {"status": f"Toggled deck {req.deck}", "playing": state["playing"], "midi_connected": dj.connected}


@app.post("/action/skip")
def skip_track(_auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    if state["queue"]:
        item = state["queue"].pop(0)
        state["current_track"] = item.get("track") or item
        state["playing"] = True
        dj.deck_play_pause(deck=1)
        push_log(f"Skipped to {state['current_track'].get('filename', 'next track')}")
        if agent:
            title = state["current_track"].get("filename", "the next track")
            queued_speak(f"Skipping ahead — {title} coming up.", mood="hype")
    publish_state()
    return {"status": "skipped", "current_track": state["current_track"]}


@app.post("/action/load")
def load_track(req: LoadTrackRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    tracks = [t for t in all_tracks() if t["id"] == req.track_id]
    if not tracks:
        return {"error": "Track not found"}
    state["current_track"] = tracks[0]
    push_log(f"Loaded {tracks[0]['filename']}")
    publish_state()
    return {"status": "loaded", "current_track": tracks[0]}


@app.post("/action/crossfade")
def crossfade(req: CrossfadeRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    dj.crossfader(req.value)
    state["crossfader"] = req.value
    publish_state()
    return {"status": f"Crossfader moved to {req.value}"}


@app.post("/action/autonomy")
def set_autonomy(req: AutonomyRequest, _auth: None = Depends(require_operator)):
    if is_killed():
        return {"status": "killed"}
    state["autonomy"] = req.value
    save_settings({"autonomy": req.value})
    publish_state()
    return {"status": "ok", "autonomy": req.value}


@app.post("/request")
def request_song(req: SongRequest):
    result = enqueue_request(req.query, req.requester, req.via, silent=req.silent)
    if agent and not req.silent:
        agent.ingest(req.requester, f"requested {req.query}", platform=req.via)
    return result


@app.post("/speak")
def speak(req: SpeakRequest):
    queued_speak(req.text, target=req.target, mood=req.mood)
    return {"status": "ok"}


@app.post("/chat")
def incoming_chat(req: ChatIn):
    platform = (req.platform or "room").strip().lower() or "room"
    if platform in ("chat", "dashboard", "terminal", "web"):
        # Treat generic web/dashboard as room for the unified stream.
        if platform in ("chat", "web"):
            platform = "room"
    msg = ChatMessage.make_room(author=req.author, text=req.message)
    if platform != "room":
        msg.platform = platform
        msg.id = f"{platform}:{msg.user.handle}:{msg.ts}"
        msg.channel = platform
    if aggregator:
        accepted = aggregator.ingest(msg)
        return {"status": "ok" if accepted else "dropped"}
    if not agent:
        return {"error": "cohost offline"}
    agent.ingest(req.author, req.message, platform=platform)
    return {"status": "ok"}


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
