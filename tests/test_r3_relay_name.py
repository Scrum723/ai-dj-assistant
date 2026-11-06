"""R3: Relay/adapter display text uses configured cohost name (Chief)."""

from __future__ import annotations

import os
import time

import pytest

from community.adapters.base import ChatAdapter
from community.adapters.twitch import TwitchAdapter
from community.aggregator import Aggregator
from community.relay import CommunityRelay, format_relay_text
from community.schema import ChatMessage, ChatUser
from moderation import reset_rate_limits
from settings import cohost_name


class FakeSendAdapter(ChatAdapter):
    name = "twitch"
    can_send = True

    def __init__(self, name: str = "twitch"):
        super().__init__(channel="chan")
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
def settings_env(tmp_path, monkeypatch):
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    monkeypatch.setattr("settings.VOICE_DIR", tmp_path / "voice")
    return tmp_path


def test_format_relay_halo_shows_chief():
    msg = _msg("halo", "1", "Chief", "Thanks Alice!")
    assert format_relay_text(msg) == "Chief: Thanks Alice!"
    assert "Chief" in format_relay_text(_msg("halo", "2", "", "hi"), cohost="Chief")


def test_relay_output_shows_chief(settings_env):
    settings = {
        "halo": {"name": "Chief"},
        "community": {"relay": {"twitch": {"halo": True, "room": False, "max_per_min": 10}}},
    }
    tw = FakeSendAdapter("twitch")
    relay = CommunityRelay(
        get_settings=lambda: settings,
        get_adapters=lambda: {"twitch": tw},
    )
    assert relay.handle(_msg("halo", "h1", "Chief", "yo crew")) == ["twitch"]
    assert tw.sent == ["Chief: yo crew"]


def test_custom_name_flows_through_relay_and_publish(settings_env):
    settings = {
        "halo": {"name": "DJ Sparky"},
        "community": {"relay": {"kick": {"halo": True, "room": False, "max_per_min": 10}}},
        "moderation": {},
    }
    assert cohost_name(settings) == "DJ Sparky"

    kick = FakeSendAdapter("kick")
    relay = CommunityRelay(
        get_settings=lambda: settings,
        get_adapters=lambda: {"kick": kick},
    )
    agg = Aggregator(get_settings=lambda: settings, relay=relay)
    assert agg.publish_halo("drop incoming") is True
    assert kick.sent == ["DJ Sparky: drop incoming"]
    recent = agg.recent(1)
    assert recent[-1]["user"]["display_name"] == "DJ Sparky"
    assert recent[-1]["user"]["handle"] == "DJ Sparky"
    assert recent[-1]["platform"] == "halo"


def test_twitch_bot_nick_credential_untouched(monkeypatch):
    """Display text changes must not alter TWITCH_BOT_NICK / bot account wiring."""
    monkeypatch.setenv("TWITCH_BOT_NICK", "charles_bot_acct")
    calls = []

    def fake_irc(channel, nick, token, body):
        calls.append((channel, nick, token, body))

    ad = TwitchAdapter(
        channel="mychan",
        bot_token="secret",
        bot_nick="",  # fall back to env
        irc_connect=fake_irc,
    )
    assert ad.bot_nick == "charles_bot_acct"
    assert os.getenv("TWITCH_BOT_NICK") == "charles_bot_acct"
    out = ad.send("Chief: hello chat")
    assert calls == [("mychan", "charles_bot_acct", "secret", "Chief: hello chat")]
    assert out["id"].startswith("twitch:out:")
