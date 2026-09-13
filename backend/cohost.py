"""Halo — autonomous DJ co-host that socializes with the room and overlay."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Optional

from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.env"))
load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
AGENT_NAME = "Halo"

PERSONA = (
    "You are Halo, the AI DJ co-host on Doc Weather's livestream "
    "(Charles Clottin / A Geostrophic Flow). You live in the booth with the DJ. "
    "Your job is to socialize with chat: greet people by name, hype the music, "
    "take song requests, answer short questions, shout out fans, and keep energy up. "
    "Speak like a warm, hyped booth partner — not a helpdesk. "
    "Reply in 1-2 spoken sentences. No hashtags, no URLs, no emojis, no 'as an AI'."
)

IGNORE_USERS = {
    "nightbot",
    "streamelements",
    "moobot",
    "fossabot",
    "halo",
    "ai dj",
    "ai_dj",
}

REQUEST_RE = re.compile(
    r"(?:!request\s+|can you play(?: some)?\s+|play(?: some)?\s+|queue\s+|request\s+)(.+)",
    re.I,
)
QUESTION_RE = re.compile(
    r"\?|^(what|when|where|why|how|who|which|is|are|can|do|does|did|will|should|could)\b",
    re.I,
)
GREET_RE = re.compile(r"\b(hi|hey|hello|yo|sup|what'?s up|good (morning|evening|night))\b", re.I)
HYPE_RE = re.compile(
    r"\b(fire|slaps|banger|lets go|let's go|love this|w+|huge|drop|energy|hype|insane)\b",
    re.I,
)
MENTION_RE = re.compile(r"\b(halo|dj|nova|doc weather|geostrophic)\b", re.I)

WELCOME_LINES = [
    "{name} just walked in — welcome to the booth!",
    "Hey {name}! Glad you're here. Throw a request in chat if you want in the mix.",
    "What's good {name}? You picked a good time to pull up.",
    "{name} is in the room. Say hey and tell us what you want to hear.",
]
HYPE_LINES = [
    "That's the energy {name} — let it ride.",
    "I felt that {name}. This one was made for the chat.",
    "Yes {name}! Keep that in the comments, the booth hears you.",
]
IDLE_GENERIC = [
    "Chat's live — drop a song name or just say hey. Halo's in the booth.",
    "If you're new here, welcome. Request a track and I'll try to queue it.",
    "Who's hanging in the stream right now? Call out your city.",
    "This set's for the night crowd. Tell me if you want it darker or brighter.",
    "Don't be shy in chat. I talk back.",
]
FOLLOW_LINES = [
    "If you're enjoying the mix, follow so you catch the next live set.",
    "New faces in chat — hit follow if you want more of this energy.",
]

DEMO_FANS = [
    ("BassLover99", "this slaps"),
    ("SnowInBuffalo", "first time here, what artist is this?"),
    ("DanceFan", "can you play DNB Bloodrave?"),
    ("WNYnight", "yo just got here from work"),
    ("HaloWatcher", "that drop though"),
    ("RekordKid", "play Mad World!"),
    ("Cloudline", "where you streaming from?"),
    ("VibeCheck", "love this, following now"),
    ("NightShift", "hey halo what's up"),
    ("Mixmail", "queue Synthetic Halo"),
    ("LakeEffect", "Buffalo in the chat"),
    ("TempoJack", "what's the BPM on this?"),
]


class CohostAgent:
    def __init__(
        self,
        *,
        get_snapshot: Callable[[], dict],
        search_tracks: Callable[[str], list[dict]],
        enqueue_request: Callable[..., dict],
        speak: Callable[..., None],
        note_chat: Callable[[dict], None],
        get_autonomy: Callable[[], int],
        get_settings: Callable[[], dict],
    ):
        self.get_snapshot = get_snapshot
        self.search_tracks = search_tracks
        self.enqueue_request = enqueue_request
        self.speak = speak
        self.note_chat = note_chat
        self.get_autonomy = get_autonomy
        self.get_settings = get_settings

        self.running = True
        self.demo = bool(get_settings().get("demo"))
        self.youtube_id: Optional[str] = os.getenv("YOUTUBE_LIVE_VIDEO_ID") or None
        self.mood = "listening"
        self.last_target: Optional[str] = None
        self.viewers: dict[str, float] = {}
        self.chat: deque[dict] = deque(maxlen=40)
        self.replies = 0
        self.greets = 0
        self.requests_handled = 0
        self.last_real_chat = 0.0
        self._intro_done = False
        self._last_idle = 0.0
        self._last_gemini = 0.0
        self._ai_backoff_until = 0.0
        self._last_reply_user: dict[str, float] = {}
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._ai = None
        self._demo_i = 0
        self._next_demo = 0.0
        self._yt_thread: Optional[threading.Thread] = None
        self._yt_stop = threading.Event()
        self._init_ai()

    def _init_ai(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY missing — Halo will use local lines only.")
            return
        try:
            from google import genai

            self._ai = genai.Client(api_key=api_key)
            logger.info("Halo Gemini client ready (%s).", GEMINI_MODEL)
        except Exception as e:
            logger.error("Gemini init failed: %s", e)
            self._ai = None

    def _cfg(self) -> dict:
        return self.get_settings()

    def _halo_name(self) -> str:
        return (self._cfg().get("halo") or {}).get("name") or AGENT_NAME

    def status(self) -> dict[str, Any]:
        cfg = self._cfg()
        halo = cfg.get("halo") or {}
        env = cfg.get("environment") or {}
        return {
            "name": self._halo_name(),
            "running": self.running,
            "demo": self.demo,
            "mood": "quiet" if halo.get("dj_mode") else self.mood,
            "youtube_id": self.youtube_id,
            "ai_online": bool(self._ai),
            "last_target": self.last_target,
            "viewer_count": len(self.viewers),
            "viewers": list(self.viewers.keys())[-12:],
            "chat": list(self.chat)[-20:],
            "replies": self.replies,
            "greets": self.greets,
            "requests_handled": self.requests_handled,
            "dj_mode": bool(halo.get("dj_mode")),
            "energy_mode": env.get("energy_mode") or "balanced",
            "vibe": env.get("vibe") or "night",
            "listen_mic": bool(halo.get("listen_mic")),
        }

    def stop(self):
        self.running = False
        self._yt_stop.set()

    def set_demo(self, enabled: bool):
        self.demo = bool(enabled)

    def set_youtube(self, video_id: Optional[str]):
        self.youtube_id = (video_id or "").strip() or None
        self._yt_stop.set()
        if self.youtube_id:
            self._yt_stop = threading.Event()
            self._start_youtube()

    def ingest(self, author: str, message: str, platform: str = "chat"):
        author = (author or "Fan").strip()[:40]
        message = (message or "").strip()[:280]
        if not message:
            return
        if author.lower() in IGNORE_USERS:
            return
        item = {
            "author": author,
            "message": message,
            "platform": platform,
            "ts": time.time(),
        }
        self.chat.append(item)
        if author not in self.viewers:
            self.viewers[author] = time.time()
        if platform != "demo":
            self.last_real_chat = time.time()
        self.note_chat(item)
        try:
            self._inbox.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def run(self):
        logger.info("Halo co-host loop starting (demo=%s).", self.demo)
        if self.youtube_id:
            self._start_youtube()
        await asyncio.sleep(1.2)
        if not self._intro_done:
            self._intro_done = True
            name = self._halo_name()
            if not (self._cfg().get("halo") or {}).get("dj_mode"):
                self._say(
                    f"{name} in the booth. Chat with me — request a song, say hey, or tell me where you're watching from.",
                    mood="hype",
                )
            self._last_idle = time.time()
            self._next_demo = time.time() + 22
        while self.running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Halo tick failed")
            energy = (self._cfg().get("environment") or {}).get("energy_mode") or "balanced"
            await asyncio.sleep(0.55 if energy == "eco" else 0.3)

    async def _tick(self):
        cfg = self._cfg()
        halo = cfg.get("halo") or {}
        env = cfg.get("environment") or {}
        caps = halo.get("capabilities") or {}
        dj_mode = bool(halo.get("dj_mode"))
        energy = env.get("energy_mode") or "balanced"
        distraction = int(env.get("distraction") or 30)
        autonomy = int(cfg.get("autonomy") or self.get_autonomy())
        now = time.time()
        try:
            item = self._inbox.get_nowait()
        except asyncio.QueueEmpty:
            item = None
        if item:
            await self._handle_chat(item, autonomy, dj_mode=dj_mode, caps=caps, energy=energy)

        if dj_mode or not caps.get("idle_talk", True) or energy == "eco":
            idle_every = 120.0
        else:
            base = {"balanced": 42.0, "show": 20.0}.get(energy, 42.0)
            idle_every = max(16.0, base * (1.35 - distraction / 140.0))
        if (not dj_mode) and caps.get("idle_talk", True) and now - self._last_idle >= idle_every and self._inbox.empty():
            self._idle_line(autonomy, caps)
            self._last_idle = now

        demo_ok = self.demo and energy != "eco" and not dj_mode
        if demo_ok and now - self.last_real_chat > 25:
            gap = 32.0 + random.uniform(0, 14)
            if now >= self._next_demo:
                author, text = DEMO_FANS[self._demo_i % len(DEMO_FANS)]
                self._demo_i += 1
                self.ingest(author, text, platform="demo")
                self._next_demo = now + gap

    async def _handle_chat(self, item: dict, autonomy: int, dj_mode: bool = False, caps: dict | None = None, energy: str = "balanced"):
        author = item["author"]
        message = item["message"]
        kind = self._classify(author, message)
        self.mood = "listening"
        caps = caps or {}

        demo = item.get("platform") == "demo"
        if kind == "request":
            if not caps.get("requests", True):
                return
            query = self._request_query(message)
            self.requests_handled += 1
            result = self.enqueue_request(query=query, requester=author, via=item["platform"], silent=True)
            found = result.get("status") == "queued"
            title = self._short_title((result.get("item") or {}).get("filename") or query)
            if found:
                line = f"{author} I got you — {title} is in the queue."
            else:
                line = f"I don't have {query} in this crate {author}, but I logged it. Name another track."
            if not demo and energy != "eco":
                line = await self._compose(
                    author,
                    message,
                    fallback=line,
                    hint="They requested a song. Confirm the queue in one short spoken line and say the title.",
                )
            if dj_mode:
                return
            self._say(line, target=author, mood="hype")
            return

        user_key = author.lower()
        last = self._last_reply_user.get(user_key, 0)
        cooldown = 12 if kind in {"welcome", "question", "mention"} else 28
        if time.time() - last < cooldown and kind not in {"request"}:
            return

        should = False
        if dj_mode:
            return
        cap_map = {
            "welcome": "greet",
            "new_viewer": "greet",
            "question": "questions",
            "mention": "questions",
            "hype": "hype",
            "chat": "hype",
        }
        if not caps.get(cap_map.get(kind, "hype"), True):
            return

        if kind in {"welcome", "question", "mention", "new_viewer"}:
            should = True
        elif kind == "hype":
            should = autonomy >= 35
        else:
            should = random.random() < (autonomy / 100.0) * (0.25 if energy == "eco" else 0.5)

        if not should:
            return

        fallback = self._fallback(kind, author, message)
        use_ai = (not demo) and energy != "eco" and kind in {"question", "mention"}
        if use_ai:
            hint = "Answer briefly if you can. If not, pivot to the music. Name them once."
            line = await self._compose(author, message, fallback=fallback, hint=hint)
        else:
            line = fallback
        mood = "hype" if kind in {"hype", "welcome", "new_viewer"} else "talk"
        if kind in {"welcome", "new_viewer"}:
            self.greets += 1
        self._say(line, target=author, mood=mood)
        self._last_reply_user[user_key] = time.time()

    def _idle_line(self, autonomy: int, caps: dict | None = None):
        snap = self.get_snapshot()
        track = snap.get("current_track") or {}
        title = self._short_title(track.get("filename") or "")
        queue = snap.get("queue") or []
        viewers = [v for v in self.viewers.keys() if v.lower() != self._halo_name().lower()]
        options = list(IDLE_GENERIC)
        caps = caps or {}
        if caps.get("introduce_fans") and len(viewers) >= 2:
            a, b = random.sample(viewers, 2)
            options.append(f"{a}, meet {b} — both of you are in the booth tonight. Say hey.")
        if title:
            options.extend(
                [
                    f"Right now we're on {title}. Chat if you want this darker, faster, or left on repeat.",
                    f"{title} is in the air. Who's still with me?",
                ]
            )
        if queue:
            nxt = self._short_title(queue[0].get("filename") or queue[0].get("query") or "the next request")
            who = queue[0].get("requester") or "chat"
            options.append(f"Up next I have {nxt} for {who}. Stay for that one.")
        if viewers:
            options.append(f"Shoutout {random.choice(viewers)} for hanging in the stream.")
        if autonomy >= 50:
            options.extend(FOLLOW_LINES)
        self._say(random.choice(options), mood="idle")

    def _classify(self, author: str, message: str) -> str:
        if REQUEST_RE.search(message) or message.lower().startswith("!request"):
            return "request"
        if QUESTION_RE.search(message):
            return "question"
        if MENTION_RE.search(message):
            return "mention"
        is_new = author.lower() not in self._last_reply_user and (time.time() - self.viewers.get(author, 0) < 2)
        if is_new:
            return "welcome" if GREET_RE.search(message) else "new_viewer"
        if GREET_RE.search(message):
            return "welcome"
        if HYPE_RE.search(message):
            return "hype"
        return "chat"

    def _request_query(self, message: str) -> str:
        match = REQUEST_RE.search(message)
        if match:
            return match.group(1).strip(" .!?")
        if message.lower().startswith("!request "):
            return message.split(" ", 1)[1].strip()
        return message

    def _fallback(self, kind: str, author: str, message: str) -> str:
        if kind in {"welcome", "new_viewer"}:
            return random.choice(WELCOME_LINES).format(name=author)
        if kind == "hype":
            return random.choice(HYPE_LINES).format(name=author)
        if kind == "question":
            return f"Good question {author}. Stay with the mix and I'll keep you posted from the booth."
        return f"I hear you {author}. Keep talking — this stream's better when the chat's alive."

    async def _compose(self, author: str, message: str, fallback: str, hint: str) -> str:
        now = time.time()
        if not self._ai or now < self._ai_backoff_until or now - self._last_gemini < 12:
            return fallback
        snap = self.get_snapshot()
        track = snap.get("current_track") or {}
        title = self._short_title(track.get("filename") or "nothing loaded")
        halo_name = self._halo_name()
        recent = "\n".join(f"{c['author']}: {c['message']}" for c in list(self.chat)[-5:] if c.get("author") != halo_name)
        prompt = (
            f"{hint}\n"
            f"Now playing: {title}.\n"
            f"Recent chat:\n{recent}\n"
            f"Reply to {author} who said: {message}"
        )
        try:
            from google.genai import types

            persona = (self._cfg().get("halo") or {}).get("persona") or PERSONA

            def _call():
                return self._ai.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=persona,
                        temperature=0.8,
                        max_output_tokens=120,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    ),
                )

            self._last_gemini = now
            response = await asyncio.to_thread(_call)
            text = (getattr(response, "text", None) or "").strip()
            text = re.sub(r"https?://\S+", "", text)
            text = re.sub(r"[#*_`]+", "", text).strip()
            if text and len(text) > 12:
                return text[:280]
        except Exception as e:
            err = str(e)
            logger.error("Halo Gemini reply failed: %s", e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                self._ai_backoff_until = time.time() + 75
        return fallback

    def _say(self, text: str, target: Optional[str] = None, mood: str = "talk"):
        text = (text or "").strip()
        if not text:
            return
        self.mood = "speaking"
        self.last_target = target
        self.replies += 1
        self.speak(text, target=target, mood=mood)
        name = self._halo_name()
        self.chat.append(
            {
                "author": name,
                "message": text,
                "platform": "halo",
                "ts": time.time(),
            }
        )
        self.note_chat({"author": name, "message": text, "platform": "halo", "ts": time.time()})

    def _short_title(self, filename: str) -> str:
        name = os.path.splitext(filename or "")[0]
        name = re.sub(r"\s*-\s*\d{1,2}:\d{1,2}:\d{2}.*", "", name)
        return name.strip() or filename

    def _start_youtube(self):
        if not self.youtube_id:
            return

        def loop():
            try:
                import pytchat
            except Exception as e:
                logger.error("pytchat missing: %s", e)
                return
            logger.info("Halo listening to YouTube live chat %s", self.youtube_id)
            try:
                chat = pytchat.create(video_id=self.youtube_id)
                while self.running and not self._yt_stop.is_set() and chat.is_alive():
                    for c in chat.get().sync_items():
                        self.ingest(c.author.name, c.message, platform="youtube")
                    time.sleep(1)
            except Exception:
                logger.exception("YouTube listener died")

        self._yt_thread = threading.Thread(target=loop, daemon=True)
        self._yt_thread.start()
