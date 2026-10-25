"""A6: Supporter shoutouts — mapping, burst grouping, templates, overlay payload."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from community.adapters.kick import map_kick_event
from community.adapters.tiktok import map_tiktok_event
from community.adapters.twitch import parse_irc_line, parse_usernotice
from community.adapters.youtube import map_pytchat_item
from community.aggregator import Aggregator
from community.schema import ChatMessage, ChatUser, SupportInfo
from community.shoutouts import thank_template
from community.store import CommunityStore
from conftest import UnavailableLLM
from halo_queue import PriorityQueue
from moderation import reset_rate_limits


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += float(sec)


def test_youtube_superchat_fixture_maps_support():
    item = SimpleNamespace(
        id="sc1",
        type="superChat",
        message="love the set",
        amountValue=5.0,
        currency="USD",
        messageEx=None,
        author=SimpleNamespace(
            name="Kim",
            channelId="UC123",
            imageUrl=None,
            isChatModerator=False,
            isChatOwner=False,
            isChatSponsor=False,
        ),
    )
    msg = map_pytchat_item(item, "vid")
    assert msg is not None
    assert msg.kind == "super_chat"
    assert msg.support is not None
    assert msg.support.amount == 5.0
    assert msg.support.currency == "USD"
    assert "support" in msg.to_client()


def test_youtube_member_milestone_maps():
    item = SimpleNamespace(
        id="mm1",
        type="memberMilestone",
        message="12 months!",
        amountValue=None,
        currency=None,
        messageEx=None,
        memberMilestoneChatDetails={"memberMonth": 12, "memberLevelName": "VIP"},
        author=SimpleNamespace(
            name="Pat",
            channelId="UC456",
            imageUrl=None,
            isChatModerator=False,
            isChatOwner=False,
            isChatSponsor=True,
        ),
    )
    msg = map_pytchat_item(item, "vid")
    assert msg.kind == "member"
    assert msg.support.count == 12
    assert msg.support.tier == "VIP"


def test_twitch_usernotice_sub_and_bits():
    sub = (
        "@badge-info=subscriber/1;badges=subscriber/0;color=;display-name=CoolFan;"
        "id=sub-1;login=coolfan;mod=0;msg-id=sub;msg-param-cumulative-months=1;"
        "msg-param-sub-plan=1000;room-id=1;subscriber=1;tmi-sent-ts=1;user-id=99;user-type= "
        ":tmi.twitch.tv USERNOTICE #charles"
    )
    msg = parse_usernotice(sub)
    assert msg is not None
    assert msg.kind == "member"
    assert msg.support.count == 1

    bits = (
        "@badge-info=;badges=;bits=100;color=#FFF;display-name=Cheerer;emotes=;"
        "id=bits-1;mod=0;room-id=1;subscriber=0;tmi-sent-ts=1;turbo=0;user-id=42;user-type= "
        ":cheerer!cheerer@cheerer.tmi.twitch.tv PRIVMSG #charles :Cheer100 nice"
    )
    bmsg = parse_irc_line(bits)
    assert bmsg.kind == "super_chat"
    assert bmsg.support.amount == 100
    assert bmsg.support.currency == "bits"


def test_kick_gift_and_tiktok_gift_fixtures():
    kick = map_kick_event(
        {"type": "gift", "id": "k1", "sender": {"username": "KickPal", "id": "1"}, "count": 3}
    )
    assert kick.kind == "gift"
    assert kick.support.count == 3
    assert kick.platform == "kick"

    tt = map_tiktok_event(
        {
            "type": "gift",
            "id": "t1",
            "user": {"uniqueId": "ttfan", "nickname": "TTFan"},
            "gift_name": "Rose",
            "repeat_count": 5,
            "diamond_count": 5,
        }
    )
    assert tt.kind == "gift"
    assert tt.support.count == 5
    assert tt.support.currency == "diamonds"
    assert tt.support.tier == "Rose"


def test_thank_templates_platform_and_no_amount_by_default():
    yt = thank_template("youtube", "Kim", {"amount": 5, "currency": "USD"}, say_amounts=False)
    assert "Kim" in yt
    assert "5" not in yt
    assert "$" not in yt
    yt_amt = thank_template("youtube", "Kim", {"amount": 5, "currency": "USD"}, say_amounts=True)
    assert "5" in yt_amt
    short = thank_template("tiktok", "TTFan", {"count": 1}, say_amounts=False)
    assert short.startswith("Thanks")
    grouped = thank_template("kick", "KickPal", {"count": 20}, say_amounts=False, count=20)
    assert "20" in grouped


def test_fixture_supporters_thank_within_5s(agent, spoken):
    agent._llm = UnavailableLLM()
    clock = FakeClock()
    agent._clock = clock
    agent.queue = PriorityQueue(clock=clock, task_ttl_sec=120)
    agent._last_reply_at = 0.0
    agent._last_idle = clock()
    highlights = []
    agent.set_highlight_hook(lambda p: highlights.append(p))

    cases = [
        ("Kim", "youtube", "super_chat", {"amount": 5.0, "currency": "USD", "count": 1}, "love it"),
        ("CoolFan", "twitch", "member", {"count": 1}, "subscribed"),
        ("KickPal", "kick", "gift", {"count": 1}, "gifted a sub"),
        ("TTFan", "tiktok", "gift", {"count": 1, "currency": "diamonds", "amount": 1}, "sent Rose"),
    ]
    for author, plat, kind, support, text in cases:
        spoken.clear()
        highlights.clear()
        agent._supporter_bursts.clear()
        agent.queue.clear()
        agent._last_reply_at = 0.0
        agent._reply_times.clear()
        agent._last_reply_user.clear()
        t0 = clock()
        agent.ingest(author, text, platform=plat, event_kind=kind, support=support)
        clock.advance(1.6)
        asyncio.run(agent._tick())
        clock.advance(0.1)
        asyncio.run(agent._tick())
        assert spoken, f"expected thank-you for {plat} {kind}"
        assert author.lower() in spoken[0]["text"].lower()
        assert highlights, f"expected highlight for {plat}"
        assert highlights[0]["platform"] == plat
        assert "thank_you" in highlights[0]
        assert clock() - t0 <= 5.0


def test_burst_of_20_gifts_one_thank(agent, spoken):
    agent._llm = UnavailableLLM()
    clock = FakeClock()
    agent._clock = clock
    agent.queue = PriorityQueue(clock=clock, task_ttl_sec=120)
    agent._last_reply_at = 0.0
    agent._last_idle = clock()
    agent._supporter_bursts.clear()
    agent.queue.clear()
    spoken.clear()

    for i in range(20):
        agent.ingest(
            "Gifter",
            f"gift {i}",
            platform="tiktok",
            event_kind="gift",
            support={"count": 1, "tier": "Rose"},
        )
        clock.advance(0.2)  # within 10s window
    clock.advance(1.6)
    asyncio.run(agent._tick())
    clock.advance(0.1)
    asyncio.run(agent._tick())
    assert len(spoken) == 1
    assert "20" in spoken[0]["text"] or "Gifter" in spoken[0]["text"]


def test_aggregator_passes_event_kind_to_cohost():
    reset_rate_limits()
    seen = []

    def cohost(author, message, platform="chat", *, from_aggregator=False, event_kind=None, support=None, profile_id=None):
        seen.append(
            {
                "author": author,
                "event_kind": event_kind,
                "support": support,
                "from_aggregator": from_aggregator,
            }
        )

    agg = Aggregator(
        cohost_ingest=cohost,
        get_settings=lambda: {"moderation": {"blocklist": []}},
    )
    msg = ChatMessage(
        id="youtube:sc-agg",
        platform="youtube",
        channel="v",
        user=ChatUser(platform_user_id="u", handle="Kim", display_name="Kim"),
        text="SC",
        kind="super_chat",
        support=SupportInfo(amount=10.0, currency="USD", count=1),
        ts=time.time(),
    )
    assert agg.ingest(msg) is True
    assert seen and seen[0]["event_kind"] == "super_chat"
    assert seen[0]["support"]["amount"] == 10.0


def test_supporters_today_wall(tmp_path):
    store = CommunityStore(db_path=tmp_path / "c.db")
    now = time.time()
    for plat, handle, kind in (
        ("youtube", "Kim", "super_chat"),
        ("twitch", "Kim", "member"),
        ("kick", "Other", "gift"),
    ):
        store.record_supporter(
            ChatMessage(
                id=f"{plat}:{handle}:{kind}",
                platform=plat,
                channel="c",
                user=ChatUser(platform_user_id=handle, handle=handle, display_name=handle),
                text="x",
                kind=kind,
                support=SupportInfo(count=1, amount=5.0 if kind == "super_chat" else None, currency="USD" if kind == "super_chat" else None),
                profile_id=7 if handle == "Kim" else None,
                ts=now,
            )
        )
    wall = store.supporters_today(now=now)
    assert len(wall) >= 2
    kim = next(x for x in wall if x["display_name"] == "Kim")
    assert set(kim["platforms"]) >= {"youtube", "twitch"}
    assert kim["profile_id"] == 7


def test_overlay_highlight_payload_shape(agent):
    payloads = []
    agent.set_highlight_hook(lambda p: payloads.append(p))
    agent._publish_highlight(
        {
            "author": "Kim",
            "platform": "youtube",
            "event_kind": "super_chat",
            "message": "hi",
            "support": {"amount": 5, "currency": "USD"},
            "ts": 1.0,
        },
        "Thank you Kim!",
    )
    assert payloads
    p = payloads[0]
    assert p["author"] == "Kim"
    assert p["thank_you"] == "Thank you Kim!"
    assert p["kind"] == "super_chat"


def test_community_supporters_endpoint(tmp_path, monkeypatch):
    import main as main_mod

    store = CommunityStore(db_path=tmp_path / "api.db")
    store.record_supporter(
        ChatMessage(
            id="yt:1",
            platform="youtube",
            channel="c",
            user=ChatUser(handle="Kim", display_name="Kim", platform_user_id="Kim"),
            text="sc",
            kind="super_chat",
            support=SupportInfo(amount=2.0, currency="USD", count=1),
            ts=time.time(),
        )
    )
    with TestClient(main_mod.app) as client:
        # Lifespan creates its own store — swap after startup
        monkeypatch.setattr(main_mod, "community_store", store)
        monkeypatch.setattr(main_mod, "agent", None)
        monkeypatch.setattr(main_mod, "aggregator", None)
        res = client.get("/community/supporters/today")
        assert res.status_code == 200
        body = res.json()
        assert body["supporters"]
        assert body["supporters"][0]["display_name"] == "Kim"
