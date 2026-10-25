"""B7: Relay back to platform chats — loop prevention, rate cap, rule matrix."""

from __future__ import annotations

import time
from typing import Optional

import pytest

from community.adapters.base import ChatAdapter
from community.aggregator import Aggregator
from community.relay import CommunityRelay, TokenBucket, format_relay_text
from community.schema import ChatMessage, ChatUser
from moderation import reset_rate_limits


class FakeSendAdapter(ChatAdapter):
    name = "twitch"
    can_send = True

    def __init__(self, name: str = "twitch", channel: str = "chan"):
        super().__init__(channel=channel)
        self.name = name
        self.can_send = True
        self.sent: list[str] = []

    def start(self, emit):
        self._emit = emit
        self._running = True
        self._connected = True

    def stop(self):
        self._running = False
        self._connected = False

    def send(self, text: str) -> dict:
        self.sent.append(text)
        return {"id": f"{self.name}:out:{len(self.sent)}"}


def _msg(platform: str, mid: str, handle: str, text: str) -> ChatMessage:
    return ChatMessage(
        id=f"{platform}:{mid}",
        platform=platform,
        channel="test",
        user=ChatUser(platform_user_id=handle, handle=handle, display_name=handle),
        text=text,
        kind="chat",
        ts=time.time(),
    )


@pytest.fixture(autouse=True)
def _reset_mod():
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture
def clock():
    t = {"now": 1_700_000_000.0}

    def now():
        return t["now"]

    now.advance = lambda s: t.__setitem__("now", t["now"] + s)  # type: ignore[attr-defined]
    return now


def test_format_room_and_halo():
    room = _msg("room", "1", "Alice", "hi there")
    halo = _msg("halo", "2", "Halo", "Thanks Alice!")
    assert format_relay_text(room) == "[room] Alice: hi there"
    assert format_relay_text(halo) == "Thanks Alice!"


def test_off_by_default_rule_matrix(clock):
    settings = {
        "community": {
            "relay": {
                "twitch": {"halo": False, "room": False, "max_per_min": 10},
                "kick": {"halo": True, "room": False, "max_per_min": 10},
                "facebook": {"halo": False, "room": True, "max_per_min": 10},
            }
        }
    }
    tw = FakeSendAdapter("twitch")
    kick = FakeSendAdapter("kick")
    fb = FakeSendAdapter("facebook")
    adapters = {"twitch": tw, "kick": kick, "facebook": fb, "youtube_official": FakeSendAdapter("youtube_official")}
    relay = CommunityRelay(
        get_settings=lambda: settings,
        get_adapters=lambda: adapters,
        clock=clock,
    )

    assert relay.handle(_msg("room", "r1", "Bob", "hello")) == ["facebook"]
    assert tw.sent == []
    assert kick.sent == []
    assert fb.sent == ["[room] Bob: hello"]

    assert relay.handle(_msg("halo", "h1", "Halo", "yo")) == ["kick"]
    assert kick.sent == ["yo"]
    assert len(fb.sent) == 1


def test_rate_cap(clock):
    settings = {
        "community": {
            "relay": {"twitch": {"halo": True, "room": True, "max_per_min": 2}}
        }
    }
    tw = FakeSendAdapter("twitch")
    relay = CommunityRelay(
        get_settings=lambda: settings,
        get_adapters=lambda: {"twitch": tw},
        clock=clock,
    )
    assert relay.handle(_msg("room", "1", "A", "one")) == ["twitch"]
    assert relay.handle(_msg("room", "2", "A", "two")) == ["twitch"]
    assert relay.handle(_msg("room", "3", "A", "three")) == []
    assert len(tw.sent) == 2
    clock.advance(60)
    assert relay.handle(_msg("room", "4", "A", "four")) == ["twitch"]
    assert len(tw.sent) == 3


def test_loop_prevention_via_echo_map(clock):
    settings = {
        "community": {
            "relay": {"twitch": {"halo": True, "room": True, "max_per_min": 20}}
        }
    }
    tw = FakeSendAdapter("twitch")
    relay = CommunityRelay(
        get_settings=lambda: settings,
        get_adapters=lambda: {"twitch": tw},
        clock=clock,
    )
    agg = Aggregator(get_settings=lambda: {"moderation": {}}, relay=relay)
    agg.register(tw)

    assert agg.ingest(_msg("room", "r1", "Carol", "loop me")) is True
    assert tw.sent == ["[room] Carol: loop me"]

    echo = _msg("twitch", "out:1", "bot", "[room] Carol: loop me")
    echo.id = "twitch:out:1"
    assert relay.is_echo(echo) is True
    assert agg.ingest(echo) is False

    # Different text still accepted
    other = _msg("twitch", "x2", "viewer", "unrelated")
    assert agg.ingest(other) is True


def test_token_bucket_unit(clock):
    b = TokenBucket(3, clock=clock)
    assert b.try_take() and b.try_take() and b.try_take()
    assert not b.try_take()
    clock.advance(20)
    assert b.try_take()  # ~1 token refilled
    assert not b.try_take()


def test_twitch_send_uses_irc_hook():
    calls = []

    def fake_irc(channel, nick, token, body):
        calls.append((channel, nick, token, body))

    from community.adapters.twitch import TwitchAdapter

    ad = TwitchAdapter(channel="mychan", bot_token="secret", bot_nick="mybot", irc_connect=fake_irc)
    assert ad.can_send is True
    out = ad.send("hello chat")
    assert calls == [("mychan", "mybot", "secret", "hello chat")]
    assert out["id"].startswith("twitch:out:")


def test_facebook_send_posts_comment():
    posts = []

    class Res:
        status_code = 200

        def json(self):
            return {"id": "lv_1"}

    def http_post(url, params):
        posts.append((url, params))
        return Res()

    from community.adapters.facebook import FacebookAdapter, GRAPH_BASE

    ad = FacebookAdapter(channel="vid123", page_access_token="pat", http_post=http_post)
    assert ad.can_send is True
    assert ad.send("page says hi") == {"id": "lv_1"}
    assert posts[0][0] == f"{GRAPH_BASE}/vid123/comments"
    assert posts[0][1]["message"] == "page says hi"


def test_youtube_official_send_insert():
    posts = []

    class Res:
        status_code = 200

        def json(self):
            return {"id": "ytmsg1"}

    def http_post(url, params, payload, headers):
        posts.append((url, params, payload, headers))
        return Res()

    from community.adapters.youtube_official import YouTubeOfficialAdapter, QUOTA_PER_INSERT

    ad = YouTubeOfficialAdapter(
        live_chat_id="LC1",
        oauth_token="ya29.x",
        api_key="key",
        http_post=http_post,
    )
    assert ad.can_send is True
    assert ad.send("halo line") == {"id": "ytmsg1"}
    assert posts[0][2]["snippet"]["liveChatId"] == "LC1"
    assert ad.quota_status()["used"] == QUOTA_PER_INSERT
