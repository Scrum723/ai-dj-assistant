"""B4: Official API adapters — fixtures only, backoff, Kick sig, YT quota."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from community.adapters.base import BackoffState
from community.adapters.facebook import FacebookAdapter, map_facebook_comment
from community.adapters.kick import (
    KickAdapter,
    map_kick_webhook,
    set_kick_public_key_pem,
    verify_kick_signature,
)
from community.adapters.rumble import RumbleAdapter, extract_rumble_messages
from community.adapters.youtube_official import (
    YouTubeOfficialAdapter,
    map_youtube_official_item,
)
from community.aggregator import Aggregator

FIXTURES = Path(__file__).parent / "fixtures" / "b4"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_kick_fixture_maps_to_chatmessage():
    payload = _load("kick_chat_message.json")
    msg = map_kick_webhook(payload, event_type="chat.message.sent")
    assert msg is not None
    assert msg.platform == "kick"
    assert msg.id == "kick:unique_message_id_123"
    assert msg.user.handle == "sender_name"
    assert msg.user.is_mod is True
    assert msg.text == "Hello Kick chat"
    assert "raw" not in msg.to_client()


def test_kick_signature_verify_roundtrip():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    set_kick_public_key_pem(pub_pem)
    try:
        message_id = "01HZTESTMESSAGEID0000000000"
        timestamp = "2025-01-14T16:08:06Z"
        body = b'{"content":"hi"}'
        signed = f"{message_id}.{timestamp}.".encode("utf-8") + body
        sig = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        sig_b64 = base64.b64encode(sig).decode("ascii")
        assert verify_kick_signature(
            message_id=message_id,
            timestamp=timestamp,
            body=body,
            signature_b64=sig_b64,
            public_pem=pub_pem,
            allow_network=False,
        )
        assert not verify_kick_signature(
            message_id=message_id,
            timestamp=timestamp,
            body=b'{"content":"tampered"}',
            signature_b64=sig_b64,
            public_pem=pub_pem,
            allow_network=False,
        )
    finally:
        set_kick_public_key_pem(None)


def test_kick_webhook_route_with_skip_sig(monkeypatch):
    from fastapi.testclient import TestClient
    import main as main_mod

    monkeypatch.setenv("KICK_SKIP_SIGNATURE", "1")
    monkeypatch.setenv("OPERATOR_KEY", "test-op")
    ingested = []

    class _Kick(KickAdapter):
        pass

    kick = _Kick(channel="broadcaster_channel")
    agg = Aggregator(cohost_ingest=lambda *a, **k: ingested.append(k), get_settings=lambda: {
        "community": {"adapters": {"kick": {"enabled": True, "channel": "broadcaster_channel"}}},
        "moderation": {"blocklist": []},
    })
    agg.register(kick)
    kick.start(agg.ingest)
    monkeypatch.setattr(main_mod, "aggregator", agg)

    client = TestClient(main_mod.app)
    payload = _load("kick_chat_message.json")
    res = client.post(
        "/community/webhooks/kick",
        json=payload,
        headers={
            "Kick-Event-Message-Id": "mid1",
            "Kick-Event-Message-Timestamp": "2025-01-14T16:08:06Z",
            "Kick-Event-Signature": "dGVzdA==",
            "Kick-Event-Type": "chat.message.sent",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] is True
    assert body["id"] == "kick:unique_message_id_123"


def test_facebook_fixture_maps():
    data = _load("facebook_comments.json")
    msg = map_facebook_comment(data["data"][0], live_video_id="vid1")
    assert msg.platform == "facebook"
    assert msg.user.display_name == "Steph C."
    assert "great live stream" in msg.text


def test_facebook_backoff_on_429():
    class FakeRes:
        status_code = 429
        text = "rate limit"

        def json(self):
            return {}

    calls = {"n": 0}

    def http_get(url, params):
        calls["n"] += 1
        return FakeRes()

    adapter = FacebookAdapter(
        channel="vid1",
        page_access_token="token",
        poll_interval_sec=0.05,
        http_get=http_get,
    )
    emitted = []
    adapter.start(lambda m: emitted.append(m))
    time.sleep(0.25)
    adapter.stop()
    assert adapter._backoff.failures >= 1
    assert adapter._backoff.remaining() > 0 or adapter.status().get("health") in {"degraded", "dead"}


def test_rumble_fixture_maps_chat_and_rant():
    payload = _load("rumble_livestream.json")
    msgs = extract_rumble_messages(payload)
    assert any(m.kind == "chat" and m.user.handle == "UserNameA" for m in msgs)
    rant = next(m for m in msgs if m.kind == "super_chat")
    assert rant.support is not None
    assert rant.support.amount == 1.0
    assert rant.platform == "rumble"


def test_rumble_backoff_on_429():
    class FakeRes:
        status_code = 429

        def json(self):
            return {}

    adapter = RumbleAdapter(
        api_url="https://example.invalid/rumble",
        poll_interval_sec=0.05,
        http_get=lambda url: FakeRes(),
    )
    adapter.start(lambda m: None)
    time.sleep(0.2)
    adapter.stop()
    assert adapter._backoff.failures >= 1


def test_youtube_official_fixture_and_quota():
    data = _load("youtube_livechat_list.json")
    chat = map_youtube_official_item(data["items"][0], channel="vid")
    assert chat.platform == "youtube"
    assert chat.text == "Hello from official API"
    sc = map_youtube_official_item(data["items"][1], channel="vid")
    assert sc.kind == "super_chat"
    assert sc.support.amount == 5.0
    assert sc.support.currency == "USD"

    fallback = {"n": 0}

    class FakeRes:
        status_code = 200

        def json(self):
            return data

    adapter = YouTubeOfficialAdapter(
        channel="vid",
        api_key="key",
        live_chat_id="livechat123",
        daily_quota=10,  # two list calls then exhausted (5 each)
        http_get=lambda url, params: FakeRes(),
        on_quota_exhausted=lambda: fallback.__setitem__("n", fallback["n"] + 1),
    )
    # First charge succeeds
    assert adapter._charge_quota(5) is True
    assert adapter._charge_quota(5) is True
    assert adapter._charge_quota(5) is False
    assert adapter.quota_status()["exhausted"] is True
    adapter._quota_exhausted = True
    adapter.start(lambda m: None)
    assert adapter.status()["health"] == "dead"
    assert fallback["n"] == 1


def test_backoff_state_exponential():
    b = BackoffState(base=1.0, max_sec=10.0)
    d1 = b.on_error()
    d2 = b.on_error()
    assert d2 >= d1
    d429 = b.on_error(is_429=True)
    assert d429 >= 1.0
    b.on_success()
    assert b.failures == 0


def test_dead_adapter_does_not_block_others():
    """A failing facebook adapter must not prevent kick ingest."""
    seen = []

    class BoomFB(FacebookAdapter):
        def start(self, emit):
            self._emit = emit
            self._running = True
            self._connected = False
            self._error = "boom"
            self._health = "dead"

    agg = Aggregator(
        cohost_ingest=lambda author, text, **k: seen.append(author),
        get_settings=lambda: {"moderation": {"blocklist": []}, "community": {"adapters": {}}},
    )
    fb = BoomFB(channel="x", page_access_token="t")
    kick = KickAdapter(channel="c")
    agg.register(fb)
    agg.register(kick)
    # start individually simulating aggregator.start isolation
    try:
        fb.start(agg.ingest)
    except Exception:
        pytest.fail("dead adapter start should not raise")
    kick.start(agg.ingest)
    msg = kick.handle_webhook_payload(_load("kick_chat_message.json"), event_type="chat.message.sent")
    assert msg is not None
    assert kick.status()["health"] == "ok"
    assert fb.status()["health"] == "dead"
