"""A3: Halo merged-room context, reply selection, metrics, aggregator publish."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from community.aggregator import Aggregator
from conftest import UnavailableLLM


def test_select_mentions_before_requests_and_chat(agent):
    batch = [
        {"author": "A", "message": "cool set", "platform": "twitch", "ts": 1.0},
        {"author": "B", "message": "hey halo", "platform": "youtube", "ts": 2.0},
        {"author": "C", "message": "play Mad World", "platform": "room", "ts": 3.0},
        {"author": "D", "message": "what BPM?", "platform": "twitch", "ts": 4.0},
    ]
    selected, mode = agent._select_from_batch(batch)
    assert mode == "single"
    assert selected[0]["author"] == "B"
    assert selected[0]["_kind"] == "mention"


def test_select_combined_new_people(agent):
    now = time.time()
    agent.viewers["Kim"] = now
    agent.viewers["Raj"] = now
    batch = [
        {"author": "Kim", "message": "hi everyone", "platform": "twitch", "ts": now},
        {"author": "Raj", "message": "hello", "platform": "youtube", "ts": now + 0.1},
        {"author": "Old", "message": "nice", "platform": "room", "ts": now + 0.2},
    ]
    agent._last_reply_user["old"] = now - 100
    agent.viewers["Old"] = now - 100
    selected, mode = agent._select_from_batch(batch)
    assert mode == "combined_new"
    names = {s["author"] for s in selected}
    assert names == {"Kim", "Raj"}


def test_combined_shoutout_kim_raj(agent, spoken):
    agent._llm = UnavailableLLM()
    now = time.time()
    agent.viewers["Kim"] = now
    agent.viewers["Raj"] = now
    batch = [
        {"author": "Kim", "message": "hi", "platform": "twitch", "ts": now},
        {"author": "Raj", "message": "hello", "platform": "youtube", "ts": now + 0.1},
    ]
    asyncio.run(
        agent._handle_batch(
            batch,
            autonomy=70,
            dj_mode=False,
            caps=agent._cfg()["halo"]["capabilities"],
            energy="eco",
        )
    )
    assert spoken
    text = spoken[-1]["text"]
    assert "Kim from Twitch" in text
    assert "Raj on YouTube" in text


def test_min_reply_sec_under_load_picks_mention(agent, spoken):
    base = agent.get_settings()

    def gs():
        return {
            **base,
            "demo": False,
            "autonomy": 70,
            "halo": {
                **base["halo"],
                "min_reply_sec": 8,
                "dj_mode": False,
            },
            "environment": {"energy_mode": "eco", "distraction": 30, "vibe": "night"},
        }

    agent.get_settings = gs
    agent._llm = UnavailableLLM()
    agent._last_reply_at = time.time()
    agent._last_idle = time.time()
    agent._pending.clear()
    agent.queue.clear()

    agent.ingest("Chatty", "cool vibes", platform="twitch")
    agent.ingest("Asker", "hey halo", platform="youtube")
    for i in range(8):
        agent.ingest(f"Fan{i}", f"msg {i}", platform="room")

    asyncio.run(agent._tick())
    assert spoken == []
    assert agent.queue.length() >= 2

    agent._last_reply_at = time.time() - 9
    agent._last_idle = time.time()
    asyncio.run(agent._tick())
    assert len(spoken) == 1
    assert spoken[0]["target"] == "Asker"


def test_compose_prompt_platform_tags_and_crowd(agent, fake_llm):
    agent.chat.append(
        {"author": "Kim", "message": "yo", "platform": "twitch", "ts": time.time()}
    )
    agent.set_presence_hook(
        lambda: {"total": 12, "counts": {"youtube": 6, "twitch": 4, "room": 2}}
    )
    agent._last_gemini = 0.0
    agent._ai_backoff_until = 0.0
    text = asyncio.run(
        agent._compose(
            "Raj",
            "what is playing?",
            fallback="FALLBACK",
            hint="Answer briefly.",
            platform="youtube",
        )
    )
    assert text != "FALLBACK"
    assert fake_llm.calls
    _system, prompt, _max = fake_llm.calls[-1]
    assert "[twitch] Kim: yo" in prompt
    assert "12 here: 6 YouTube, 4 Twitch, 2 room" in prompt
    assert "Raj on YouTube" in prompt


def test_daily_cap_uses_fallback(agent, fake_llm, monkeypatch):
    monkeypatch.setenv("HALO_DAILY_CALL_CAP", "2")
    agent._calls_today = 2
    agent._metrics_day = time.strftime("%Y-%m-%d")
    agent._last_gemini = 0.0
    agent._ai_backoff_until = 0.0
    before = len(fake_llm.calls)
    line = asyncio.run(
        agent._compose(
            "Fan",
            "hey halo?",
            fallback="LOCAL CAP FALLBACK",
            hint="Answer.",
            platform="twitch",
        )
    )
    assert line == "LOCAL CAP FALLBACK"
    assert len(fake_llm.calls) == before


def test_metrics_math(agent, fake_llm, monkeypatch):
    monkeypatch.setenv("HALO_COST_PER_1K", "0.01")
    agent._calls_today = 0
    agent._tokens_est = 0
    agent._latencies_ms.clear()
    agent._metrics_day = time.strftime("%Y-%m-%d")
    agent._last_gemini = 0.0
    agent._ai_backoff_until = 0.0
    asyncio.run(
        agent._compose(
            "Fan",
            "what is this track?",
            fallback="FALLBACK",
            hint="Answer briefly.",
            platform="chat",
        )
    )
    m = agent.metrics()
    assert m["calls_today"] == 1
    assert m["tokens_est"] > 0
    assert m["est_cost_usd"] == round(m["tokens_est"] / 1000.0 * 0.01, 6)
    assert m["p50_ms"] >= 0
    assert m["p95_ms"] >= m["p50_ms"]


def test_say_publishes_halo_to_hook(agent, spoken, noted):
    published: list[tuple[str, str]] = []
    agent.set_publish_halo_hook(lambda name, text: published.append((name, text)))
    agent._say("Hello merged room", target="Fan", mood="talk")
    assert spoken and spoken[-1]["text"] == "Hello merged room"
    assert published == [("Halo", "Hello merged room")]
    # Aggregator owns note_chat when hook is set
    assert not any(n.get("platform") == "halo" for n in noted)


def test_aggregator_publish_halo_fans_out():
    noted: list[dict] = []
    broadcast: list[dict] = []
    ingested: list[dict] = []

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
        note_chat=lambda x: noted.append(x),
        room_broadcast=lambda x: broadcast.append(x),
        get_settings=lambda: {},
    )
    assert agg.publish_halo("Booth check", name="Halo") is True
    assert any(n.get("platform") == "halo" for n in noted)
    assert any(
        b.get("type") == "chat" and b.get("payload", {}).get("platform") == "halo"
        for b in broadcast
    )
    recent = agg.recent(5)
    assert recent and recent[-1]["platform"] == "halo"
    # cohost may be called; IGNORE_USERS would drop in real agent — here we just see fan-out
    assert ingested and ingested[-1]["platform"] == "halo"


def test_metrics_endpoint_operator(monkeypatch):
    monkeypatch.delenv("OPERATOR_KEY", raising=False)
    from main import app

    with TestClient(app) as client:
        r = client.get("/metrics")
        assert r.status_code == 200
        body = r.json()
        for key in ("calls_today", "p50_ms", "p95_ms", "est_cost_usd"):
            assert key in body
