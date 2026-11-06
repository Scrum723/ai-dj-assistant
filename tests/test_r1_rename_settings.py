"""R1: Chief default name, one-time Halo→Chief migration, chief API alias."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    monkeypatch.setattr("settings.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr("settings.APP_DIR", tmp_path)
    monkeypatch.setattr("settings.VOICE_DIR", tmp_path / "voice")
    return tmp_path


def test_fresh_settings_default_name_chief(settings_env):
    from settings import DEFAULT_PERSONA, DEFAULTS, load

    assert DEFAULTS["halo"]["name"] == "Chief"
    assert DEFAULTS["halo"]["persona"] == DEFAULT_PERSONA
    assert DEFAULT_PERSONA.startswith("You are Chief,")

    data = load()
    assert data["halo"]["name"] == "Chief"
    assert data["halo"]["persona"] == DEFAULT_PERSONA
    assert data.get("meta", {}).get("renamed_chief") is True


def test_migrate_old_halo_name_to_chief(settings_env):
    from settings import DEFAULT_PERSONA, OLD_DEFAULT_PERSONA, load

    path = settings_env / "settings.json"
    path.write_text(
        json.dumps(
            {
                "halo": {
                    "name": "Halo",
                    "persona": OLD_DEFAULT_PERSONA,
                }
            }
        ),
        encoding="utf-8",
    )

    data = load()
    assert data["halo"]["name"] == "Chief"
    assert data["halo"]["persona"] == DEFAULT_PERSONA
    assert data["meta"]["renamed_chief"] is True

    # Persisted
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["halo"]["name"] == "Chief"
    assert raw["halo"]["persona"] == DEFAULT_PERSONA
    assert raw["meta"]["renamed_chief"] is True


def test_custom_name_and_persona_preserved(settings_env):
    from settings import load

    custom_persona = "You are DJ Sparky, keep it weird."
    path = settings_env / "settings.json"
    path.write_text(
        json.dumps(
            {
                "halo": {
                    "name": "DJ Sparky",
                    "persona": custom_persona,
                }
            }
        ),
        encoding="utf-8",
    )

    data = load()
    assert data["halo"]["name"] == "DJ Sparky"
    assert data["halo"]["persona"] == custom_persona
    assert data["meta"]["renamed_chief"] is True


def test_migration_idempotent(settings_env):
    from settings import DEFAULT_PERSONA, OLD_DEFAULT_PERSONA, load, save

    path = settings_env / "settings.json"
    path.write_text(
        json.dumps({"halo": {"name": "Halo", "persona": OLD_DEFAULT_PERSONA}}),
        encoding="utf-8",
    )
    first = load()
    assert first["halo"]["name"] == "Chief"

    # Operator renames back to Halo after migration — must not re-migrate.
    save({"halo": {"name": "Halo", "persona": OLD_DEFAULT_PERSONA}})
    second = load()
    assert second["halo"]["name"] == "Halo"
    assert second["halo"]["persona"] == OLD_DEFAULT_PERSONA
    assert second["meta"]["renamed_chief"] is True

    # Default persona already Chief stays put on subsequent loads
    save({"halo": {"name": "Chief", "persona": DEFAULT_PERSONA}})
    third = load()
    assert third["halo"]["name"] == "Chief"
    assert third["halo"]["persona"] == DEFAULT_PERSONA


def test_public_settings_exposes_chief_alias(settings_env):
    from settings import load, public_settings, save

    save({"halo": {"name": "Chief"}})
    pub = public_settings(load())
    assert "halo" in pub
    assert "chief" in pub
    assert pub["chief"]["name"] == pub["halo"]["name"] == "Chief"
    assert pub["chief"]["elevenlabs_key"] == ""
    assert "elevenlabs_key_set" in pub["chief"]


def test_save_chief_alias_merges_into_halo(settings_env):
    from settings import load, save

    load()
    save({"chief": {"name": "Booth Boss", "min_reply_sec": 12}})
    data = load()
    assert data["halo"]["name"] == "Booth Boss"
    assert data["halo"]["min_reply_sec"] == 12
    assert "chief" not in data or data.get("chief") is None or "name" not in (data.get("chief") or {})


def test_save_halo_wins_over_chief_on_conflict(settings_env):
    from settings import load, save

    load()
    save({"chief": {"name": "FromChief"}, "halo": {"name": "FromHalo"}})
    data = load()
    assert data["halo"]["name"] == "FromHalo"


def test_publish_halo_defaults_to_configured_name(settings_env):
    from community.aggregator import Aggregator
    from settings import load, save

    save({"halo": {"name": "Chief"}})
    noted = []
    agg = Aggregator(
        note_chat=lambda item: noted.append(item),
        get_settings=load,
    )
    assert agg.publish_halo("Booth check") is True
    assert noted
    assert noted[-1]["author"] == "Chief"


def test_publish_halo_fallback_chief_when_name_missing(settings_env):
    from community.aggregator import Aggregator

    noted = []
    agg = Aggregator(
        note_chat=lambda item: noted.append(item),
        get_settings=lambda: {"halo": {}},
    )
    assert agg.publish_halo("Hi") is True
    assert noted[-1]["author"] == "Chief"


def test_root_cohost_fallback_is_chief(monkeypatch):
    import main as main_mod

    monkeypatch.setattr(main_mod, "load_settings", lambda: {"halo": {}})
    # Avoid depending on dist folders / midi
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: False)
    payload = main_mod.read_root()
    assert payload["cohost"] == "Chief"
