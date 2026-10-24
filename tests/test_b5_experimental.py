"""B5: Experimental TikTok LIVE + X adapters — fixtures, flags, auto-disable."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from community.adapters.experimental import FailureTracker
from community.adapters.tiktok import TikTokAdapter, map_tiktok_event
from community.adapters.x import XAdapter, extract_x_search_messages, map_x_tweet
from community.aggregator import Aggregator
from settings import DEFAULTS

FIXTURES = Path(__file__).parent / "fixtures" / "b5"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_defaults_experimental_off():
    ads = DEFAULTS["community"]["adapters"]
    assert ads["tiktok"]["enabled"] is False
    assert ads["tiktok"].get("experimental") is True
    assert ads["x"]["enabled"] is False
    assert ads["x"].get("experimental") is True
    assert ads["x"].get("can_post") is False


def test_tiktok_comment_gift_join_fixtures():
    c = map_tiktok_event(_load("tiktok_comment.json"), channel="charles")
    assert c is not None
    assert c.platform == "tiktok"
    assert c.kind == "chat"
    assert c.user.handle == "fan_tt"
    assert c.text == "fire set"

    g = map_tiktok_event(_load("tiktok_gift.json"))
    assert g.kind == "gift"
    assert g.support.count == 3
    assert g.support.currency == "diamonds"
    assert g.support.tier == "Rose"

    j = map_tiktok_event(_load("tiktok_join.json"))
    assert j.kind == "member"
    assert "joined" in j.text


def test_tiktok_can_send_false_and_flag_gating():
    ad = TikTokAdapter(username="charles")
    assert ad.can_send is False
    assert ad.experimental is True
    assert ad.is_ready() in (True, False)  # depends on optional TikTokLive
    # Without username → not ready
    bare = TikTokAdapter()
    assert bare.is_ready() is False


def test_tiktok_injectable_client_emits(monkeypatch):
    emitted = []

    class FakeClient:
        def __init__(self):
            self._handlers = {}

        def on(self, name, cb):
            self._handlers[name] = cb

        def pump(self):
            self._handlers["comment"](
                {"type": "comment", "id": "p1", "comment": "hi", "user": {"uniqueId": "a", "nickname": "A"}}
            )

        def stop(self):
            pass

    fake = FakeClient()
    ad = TikTokAdapter(username="host", client_factory=lambda u: fake)
    assert ad.is_ready() is True
    ad.start(lambda m: emitted.append(m))
    time.sleep(0.8)
    ad.stop()
    assert any(m.text == "hi" and m.platform == "tiktok" for m in emitted)


def test_x_fixture_maps_replies():
    payload = _load("x_search_replies.json")
    msgs = extract_x_search_messages(payload, post_id="1800000000000000000")
    assert len(msgs) == 2
    assert msgs[0].platform == "x"
    assert msgs[0].user.handle == "kim_x"
    assert msgs[1].text == "play Mad World"


def test_x_disabled_without_creds():
    ad = XAdapter()
    assert ad.is_ready() is False
    assert ad.can_send is False
    ad2 = XAdapter(post_id="1", bearer_token="tok", can_post=True)
    assert ad2.is_ready() is True
    assert ad2.can_send is True


def test_x_poll_with_inject_http():
    payload = _load("x_search_replies.json")
    emitted = []

    class Res:
        status_code = 200

        def json(self):
            return payload

    ad = XAdapter(
        post_id="1800000000000000000",
        bearer_token="test-token",
        poll_interval_sec=5,
        http_get=lambda url: Res(),
    )
    ad.start(lambda m: emitted.append(m))
    time.sleep(0.6)
    ad.stop()
    assert len(emitted) >= 2
    assert {m.user.handle for m in emitted} >= {"kim_x", "raj_x"}


def test_failure_tracker_auto_disable_threshold():
    t = FailureTracker(max_failures=5, window_sec=600)
    base = time.time()
    for i in range(4):
        assert t.record_failure(now=base + i) is False
    assert t.record_failure(now=base + 4) is True
    assert t.tripped is True


def test_adapter_auto_disable_and_alert():
    alerts = []
    disabled = []

    class BoomClient:
        def on(self, *a, **k):
            pass

        def pump(self):
            raise RuntimeError("webcast down")

        def stop(self):
            pass

    ad = TikTokAdapter(
        username="host",
        client_factory=lambda u: BoomClient(),
        on_auto_disable=lambda n, r: disabled.append((n, r)),
        on_alert=lambda p: alerts.append(p),
    )
    # Force low threshold via tracker
    ad._tracker = FailureTracker(max_failures=3, window_sec=600)
    ad.start(lambda m: None)
    deadline = time.time() + 3.0
    while time.time() < deadline and not disabled:
        time.sleep(0.1)
    ad.stop()
    assert disabled, "expected auto-disable"
    assert disabled[0][0] == "tiktok"
    assert alerts and alerts[0].get("experimental") is True


def test_aggregator_flag_gating_skips_disabled():
    settings = {
        "community": {
            "adapters": {
                "tiktok": {"enabled": False, "username": "charles"},
                "x": {"enabled": False, "post_id": "1", "bearer_token": "t"},
            }
        },
        "moderation": {"blocklist": []},
    }
    agg = Aggregator(get_settings=lambda: settings)
    tt = TikTokAdapter(username="charles", client_factory=lambda u: object())
    x = XAdapter(post_id="1", bearer_token="t", http_get=lambda u: None)
    agg.register(tt)
    agg.register(x)
    agg.start()
    assert tt.status().get("running") is False
    assert x.status().get("running") is False
    agg.stop()


def test_bearer_redacted_in_public_settings():
    from settings import public_settings

    data = {
        "halo": {"elevenlabs_key": ""},
        "community": {
            "adapters": {
                "x": {"enabled": False, "bearer_token": "super-secret", "post_id": "1"},
            }
        },
    }
    pub = public_settings(data)
    xcfg = pub["community"]["adapters"]["x"]
    assert xcfg["bearer_token"] == ""
    assert xcfg["bearer_token_set"] is True
