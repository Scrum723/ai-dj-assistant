"""B1: ChatMessage schema, aggregator dedupe/fan-out, Twitch IRC parse, /ws/room."""

from __future__ import annotations

import time
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from community.adapters.base import ChatAdapter
from community.adapters.twitch import parse_privmsg
from community.aggregator import Aggregator
from community.schema import ChatMessage, ChatUser
from moderation import reset_rate_limits


class FakeAdapter(ChatAdapter):
    name = "fake"

    def __init__(self, channel: str = "test"):
        super().__init__(channel=channel)

    def start(self, emit):
        self._emit = emit
        self._running = True
        self._connected = True

    def stop(self):
        self._running = False
        self._connected = False

    def push(self, msg: ChatMessage):
        self._note_emit(msg)


def _msg(platform: str, mid: str, handle: str, text: str) -> ChatMessage:
    return ChatMessage(
        id=f"{platform}:{mid}",
        platform=platform,
        channel="test",
        user=ChatUser(platform_user_id=handle, handle=handle, display_name=handle),
        text=text,
        ts=time.time(),
    )


def test_schema_to_client_drops_raw():
    m = _msg("youtube", "abc", "Kim", "hello")
    m.raw = {"secret": True}
    client = m.to_client()
    assert "raw" not in client
    assert client["platform"] == "youtube"
    assert client["user"]["handle"] == "Kim"
    assert client["id"] == "youtube:abc"


def test_twitch_privmsg_parse_tags():
    line = (
        "@badge-info=;badges=broadcaster/1,subscriber/6;color=#0000FF;display-name=Foofoo;"
        "emotes=25:0-4;id=f80a19d6-e35a-4273-82d0-cd87f614e767;mod=0;room-id=1;subscriber=1;"
        "tmi-sent-ts=1642696567751;turbo=0;user-id=713936733;user-type= "
        ":foofoo!foofoo@foofoo.tmi.twitch.tv PRIVMSG #bar :Kappa hello"
    )
    msg = parse_privmsg(line)
    assert msg is not None
    assert msg.platform == "twitch"
    assert msg.id == "twitch:f80a19d6-e35a-4273-82d0-cd87f614e767"
    assert msg.user.display_name == "Foofoo"
    assert msg.user.handle == "foofoo"
    assert msg.user.is_owner is True
    assert "broadcaster" in msg.user.badges
    assert msg.text == "Kappa hello"
    assert msg.emotes and msg.emotes[0].name == "Kappa"
    assert "raw" not in msg.to_client()


def test_aggregator_dedupe_and_moderation():
    reset_rate_limits()
    ingested = []
    noted = []
    roomed = []

    def cohost(author, message, platform="chat", *, from_aggregator=False):
        ingested.append(
            {
                "author": author,
                "message": message,
                "platform": platform,
                "from_aggregator": from_aggregator,
            }
        )

    agg = Aggregator(
        cohost_ingest=cohost,
        note_chat=lambda item: noted.append(item),
        room_broadcast=lambda m: roomed.append(m),
        get_settings=lambda: {"moderation": {"blocklist": []}},
    )
    fake = FakeAdapter()
    agg.register(fake)
    fake.start(agg.ingest)

    yt = _msg("youtube", "1", "Raj", "this slaps")
    tw = _msg("twitch", "2", "Kim", "hello from twitch")
    assert agg.ingest(yt) is True
    assert agg.ingest(tw) is True
    assert agg.ingest(yt) is False  # duplicate id

    assert len(ingested) == 2
    assert ingested[0]["from_aggregator"] is True
    assert ingested[0]["platform"] == "youtube"
    assert ingested[1]["platform"] == "twitch"
    assert len(noted) == 2
    assert len(roomed) == 2
    assert roomed[0]["type"] == "chat"
    assert roomed[0]["payload"]["platform"] == "youtube"

    bad = _msg("room", "3", "Spammer", "buy now https://evil.example")
    assert agg.ingest(bad) is False
    assert len(ingested) == 2


def test_room_post_platform_room(agent, noted):
    reset_rate_limits()
    roomed = []
    agg = Aggregator(
        cohost_ingest=agent.ingest,
        note_chat=lambda item: noted.append(item),
        room_broadcast=lambda m: roomed.append(m),
        get_settings=agent.get_settings,
    )
    msg = ChatMessage.make_room(author="Alex", text="hey booth")
    assert agg.ingest(msg) is True
    assert roomed[0]["payload"]["platform"] == "room"
    assert noted[-1]["platform"] == "room"
    assert any(c["author"] == "Alex" for c in agent.chat)


def test_community_status_adapters_disabled_by_default():
    from main import app

    with TestClient(app) as client:
        r = client.get("/community/status")
        assert r.status_code == 200
        body = r.json()
        assert "adapters" in body
        yt = body["adapters"].get("youtube") or {}
        tw = body["adapters"].get("twitch") or {}
        assert yt.get("enabled") is False
        assert tw.get("enabled") is False


def test_ws_room_broadcast():
    import main
    from community.schema import ChatMessage, ChatUser

    reset_rate_limits()
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/room") as ws:
            first = ws.receive_json()
            assert first["type"] in ("history", "chat")

            msg = ChatMessage(
                id=f"youtube:ws-{time.time()}",
                platform="youtube",
                channel="vid",
                user=ChatUser(handle="Raj", display_name="Raj", platform_user_id="1"),
                text="ws test ping",
                ts=time.time(),
            )
            assert main.aggregator is not None
            assert main.aggregator.ingest(msg) is True
            got = None
            for _ in range(50):
                try:
                    got = ws.receive_json()
                    if got.get("type") == "chat":
                        break
                except Exception:
                    time.sleep(0.05)
            assert got is not None
            assert got["type"] == "chat"
            assert got["payload"]["platform"] == "youtube"
            assert got["payload"]["text"] == "ws test ping"
            assert "raw" not in got["payload"]


def test_post_chat_goes_through_aggregator():
    from main import app

    reset_rate_limits()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/room") as ws:
            _ = ws.receive_json()  # history
            r = client.post(
                "/chat",
                json={"author": "BoothFan", "message": "hello unified room", "platform": "room"},
            )
            assert r.status_code == 200
            assert r.json().get("status") == "ok"
            got = ws.receive_json()
            assert got["type"] == "chat"
            assert got["payload"]["platform"] == "room"
            assert got["payload"]["user"]["handle"] == "BoothFan"
