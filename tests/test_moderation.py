"""Unit tests for input/output filters and rate limiting."""

from __future__ import annotations

import time

from moderation import (
    check_input,
    check_output,
    is_muted,
    mute,
    reset_rate_limits,
    unmute,
)


def test_blocklist_blocks_default_word():
    ok, reason = check_input("Fan", "you are a retard")
    assert ok is False
    assert reason == "blocklist"


def test_blocklist_from_settings():
    settings = {"moderation": {"blocklist": ["bananatown"]}}
    ok, reason = check_input("Fan", "welcome to bananatown", settings=settings)
    assert ok is False
    assert reason == "blocklist"


def test_links_blocked():
    ok, reason = check_input("Fan", "check https://evil.example/phish")
    assert ok is False
    assert reason == "link"
    ok2, reason2 = check_input("Fan", "go to www.spam.com now")
    assert ok2 is False
    assert reason2 == "link"


def test_caps_and_flood():
    ok, reason = check_input("Fan", "AAAAAAAAHHHH THIS IS CRAZY LOUD")
    assert ok is False
    assert reason in {"caps", "flood"}
    reset_rate_limits()
    ok2, reason2 = check_input("Fan", "heyyyyyyyyyyy")
    assert ok2 is False
    assert reason2 == "flood"


def test_clean_message_passes():
    ok, reason = check_input("Fan", "hey halo this slaps")
    assert ok is True
    assert reason == ""


def test_chatmessage_dict_shape():
    ok, reason = check_input(
        {
            "user": {"handle": "TwitchFan", "display_name": "TwitchFan"},
            "text": "hey from twitch",
        }
    )
    assert ok is True
    assert reason == ""


def test_rate_limit_ten_in_ten_seconds():
    reset_rate_limits()
    author = "Speedy"
    results = [check_input(author, f"msg {i}") for i in range(10)]
    assert all(r[0] for r in results[:9])
    assert results[9][0] is False
    assert results[9][1] == "rate"


def test_mute_blocks_input():
    mute("Troublemaker")
    assert is_muted("Troublemaker")
    ok, reason = check_input("Troublemaker", "hello there")
    assert ok is False
    assert reason == "muted"
    unmute("Troublemaker")
    ok2, reason2 = check_input("Troublemaker", "hello again")
    assert ok2 is True


def test_check_output_strips_links_and_blocks_slurs():
    cleaned = check_output("Love this set https://spam.example/x friends")
    assert cleaned is not None
    assert "http" not in cleaned.lower()
    assert check_output("what a retard move") is None
    assert check_output("ok") is None  # too short after clean
    assert check_output("This track is fire tonight") is not None
