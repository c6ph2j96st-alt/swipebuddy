"""Tests for swipebuddy.config and swipebuddy.paths."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from swipebuddy import paths
from swipebuddy.config import (
    ATTRIBUTE_LABELS,
    AppConfig,
    Preferences,
    is_local_url,
    load_config,
    save_config,
    validate,
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("SWIPEBUDDY_HOME", str(home))
    return home


def test_paths_use_override_and_create(_home: Path) -> None:
    assert paths.app_dir() == _home and _home.is_dir()
    assert paths.config_path() == _home / "config.json"
    assert paths.profile_dir() == _home / "browser-profile"
    assert paths.models_dir() == _home / "models"
    assert paths.logs_dir() == _home / "logs"
    assert paths.stats_path() == _home / "stats.json"


def test_paths_localappdata_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SWIPEBUDDY_HOME")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert paths.app_dir() == tmp_path / "local" / "SwipeBuddy"
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "h")
    assert paths.app_dir() == tmp_path / "h" / "AppData" / "Local" / "SwipeBuddy"


def test_defaults() -> None:
    c = AppConfig()
    assert (c.version, c.threshold, c.mode, c.daily_cap) == (1, 70, "suggest", 100)
    assert (c.delay_min_s, c.delay_max_s) == (3.0, 9.0)
    assert (c.pause_every_min, c.pause_every_max) == (15, 30)
    assert (c.pause_min_s, c.pause_max_s) == (60, 240)
    assert c.vlm_model == "qwen2.5vl:7b"
    assert c.ollama_url == "http://127.0.0.1:11434"
    assert c.browser_channel == "msedge"
    assert c.max_images_per_profile == 6
    assert c.preferences.free_text == ""
    assert c.preferences.age_hint_min is None and c.preferences.age_hint_max is None


def test_attributes_ascii_and_labelled() -> None:
    attrs = Preferences().attributes
    assert attrs and all(isinstance(v, bool) for v in attrs.values())
    for key in attrs:
        assert key.isascii() and key == key.lower() and " " not in key
    assert set(attrs) <= set(ATTRIBUTE_LABELS)


def test_attributes_not_shared_between_instances() -> None:
    a, b = Preferences(), Preferences()
    a.attributes["glasses_ok"] = False
    assert b.attributes["glasses_ok"] is True


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nope.json") == AppConfig()
    assert load_config() == AppConfig()


@pytest.mark.parametrize("content", ["{not json", "", "[]", "null", "42", '"str"'])
def test_load_corrupt_returns_defaults(tmp_path: Path, content: str) -> None:
    p = tmp_path / "c.json"
    p.write_text(content, encoding="utf-8")
    assert load_config(p) == AppConfig()


def test_load_binary_garbage(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_bytes(b"\xff\xfe\x00\x80garbage")
    assert load_config(p) == AppConfig()


def test_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    cfg = AppConfig(
        preferences=Preferences(
            free_text="gillar hundar åäö",
            attributes={**Preferences().attributes, "glasses_ok": False},
            age_hint_min=25,
            age_hint_max=35,
        ),
        threshold=55,
        mode="auto",
        daily_cap=50,
        delay_min_s=2.5,
        delay_max_s=7.5,
        pause_every_min=10,
        pause_every_max=20,
        pause_min_s=30,
        pause_max_s=90,
        vlm_model="other:1b",
        ollama_url="http://localhost:1234",
        browser_channel="chromium",
        max_images_per_profile=4,
    )
    save_config(cfg, p)
    assert load_config(p) == cfg


def test_save_default_path_and_format(_home: Path) -> None:
    save_config(AppConfig())
    p = _home / "config.json"
    raw = p.read_text(encoding="utf-8")
    assert raw.startswith("{\n  ")  # indent=2
    assert json.loads(raw)["threshold"] == 70
    assert load_config() == AppConfig()


def test_save_is_atomic_no_tmp_left(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "c.json"
    save_config(AppConfig(), p)
    assert [f.name for f in p.parent.iterdir()] == ["c.json"]


def test_save_utf8_not_escaped(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    cfg = AppConfig(preferences=Preferences(free_text="åäö"))
    save_config(cfg, p)
    assert "åäö" in p.read_bytes().decode("utf-8")


def test_failed_save_keeps_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "c.json"
    save_config(AppConfig(threshold=11), p)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("fail")

    monkeypatch.setattr("swipebuddy.config.os.replace", boom)
    with pytest.raises(OSError):
        save_config(AppConfig(threshold=99), p)
    assert load_config(p).threshold == 11


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(
        json.dumps({"threshold": 40, "bogus": 1, "preferences": {"free_text": "x", "zzz": 2}}),
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.threshold == 40 and cfg.preferences.free_text == "x"
    assert not hasattr(cfg, "bogus")


def test_missing_keys_get_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"daily_cap": 5, "preferences": {"attributes": {"glasses_ok": False}}}))
    cfg = load_config(p)
    assert cfg.daily_cap == 5
    assert cfg.threshold == 70
    assert cfg.preferences.attributes["glasses_ok"] is False
    assert cfg.preferences.attributes["tattoos_ok"] is True  # default retained


def test_wrong_types_fall_back(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(
        json.dumps(
            {
                "threshold": "high",
                "daily_cap": None,
                "delay_min_s": [1],
                "mode": 5,
                "preferences": {"free_text": 7, "attributes": {"glasses_ok": "yes"}, "age_hint_min": "x"},
            }
        )
    )
    cfg = load_config(p)
    assert cfg.threshold == 70 and cfg.daily_cap == 100 and cfg.delay_min_s == 3.0
    assert cfg.mode == "suggest"
    assert cfg.preferences.free_text == ""
    assert cfg.preferences.attributes["glasses_ok"] is True
    assert cfg.preferences.age_hint_min is None


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("threshold", -5, 0),
        ("threshold", 0, 0),
        ("threshold", 100, 100),
        ("threshold", 250, 100),
        ("daily_cap", 0, 1),
        ("daily_cap", -3, 1),
        ("daily_cap", 1000, 1000),
        ("daily_cap", 99999, 1000),
        ("delay_min_s", 0.2, 1.0),
        ("delay_min_s", -4, 1.0),
        ("max_images_per_profile", 0, 1),
        ("max_images_per_profile", 11, 10),
        ("max_images_per_profile", 10, 10),
        ("pause_every_min", -1, 0),
        ("pause_min_s", -10, 0),
        ("mode", "yolo", "suggest"),
        ("mode", "auto", "auto"),
        ("browser_channel", "firefox", "msedge"),
        ("browser_channel", "chromium", "chromium"),
    ],
)
def test_clamps(field: str, value: object, expected: object) -> None:
    out = validate(replace(AppConfig(), **{field: value}))
    assert getattr(out, field) == expected


def test_delay_max_not_below_min() -> None:
    out = validate(AppConfig(delay_min_s=10, delay_max_s=2))
    assert out.delay_min_s == 10 and out.delay_max_s == 10


def test_delay_min_raised_then_max_follows() -> None:
    out = validate(AppConfig(delay_min_s=0, delay_max_s=0.5))
    assert out.delay_min_s == 1.0 and out.delay_max_s == 1.0


def test_pause_ranges_sane() -> None:
    out = validate(AppConfig(pause_every_min=40, pause_every_max=5, pause_min_s=300, pause_max_s=10))
    assert out.pause_every_min <= out.pause_every_max
    assert out.pause_min_s <= out.pause_max_s
    out = validate(AppConfig(pause_every_min=-5, pause_every_max=-9, pause_min_s=-1, pause_max_s=-2))
    assert out.pause_every_min == 0 and out.pause_every_max == 0
    assert out.pause_min_s == 0 and out.pause_max_s == 0


def test_nan_and_inf_use_defaults() -> None:
    out = validate(AppConfig(delay_min_s=float("nan"), delay_max_s=float("inf"), threshold=float("nan")))  # type: ignore[arg-type]
    assert out.delay_min_s == 3.0 and out.delay_max_s == 9.0 and out.threshold == 70


def test_free_text_truncated() -> None:
    out = validate(AppConfig(preferences=Preferences(free_text="a" * 5000)))
    assert len(out.preferences.free_text) == 2000
    out = validate(AppConfig(preferences=Preferences(free_text="b" * 2000)))
    assert len(out.preferences.free_text) == 2000


def test_age_hint_swapped_if_reversed() -> None:
    out = validate(AppConfig(preferences=Preferences(age_hint_min=40, age_hint_max=20)))
    assert (out.preferences.age_hint_min, out.preferences.age_hint_max) == (20, 40)


def test_validate_does_not_mutate_input() -> None:
    cfg = AppConfig(threshold=500)
    validate(cfg)
    assert cfg.threshold == 500


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://example.com:11434",
        "https://127.0.0.1.evil.com",
        "http://127.0.0.1.evil.com:11434",
        "file:///etc/passwd",
        "https://127.0.0.1:11434",
        "http://localhost.evil.com",
        "http://evil.com/@127.0.0.1",
        "http://127.0.0.1@example.com",  # userinfo trick: real host is example.com
        "http://192.168.1.5:11434",
        "http://0.0.0.0:11434",
        "ftp://localhost",
        "",
        "localhost:11434",
        "http://",
    ],
)
def test_remote_urls_rejected(url: str) -> None:
    assert not is_local_url(url)
    assert validate(AppConfig(ollama_url=url)).ollama_url == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434", "http://LOCALHOST:5000/"],
)
def test_local_urls_accepted(url: str) -> None:
    assert is_local_url(url)
    assert validate(AppConfig(ollama_url=url)).ollama_url == url


def test_remote_url_in_file_reset_on_load(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"ollama_url": "http://example.com"}))
    assert load_config(p).ollama_url == "http://127.0.0.1:11434"


def test_save_sanitizes_remote_url(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    save_config(AppConfig(ollama_url="http://example.com"), p)
    assert json.loads(p.read_text())["ollama_url"] == "http://127.0.0.1:11434"


def test_disclaimer_default_roundtrip_and_validation(tmp_path: Path) -> None:
    assert AppConfig().disclaimer_accepted is False
    p = tmp_path / "d.json"
    save_config(AppConfig(disclaimer_accepted=True), p)
    assert load_config(p).disclaimer_accepted is True
    p.write_text(json.dumps({"disclaimer_accepted": "yes"}), encoding="utf-8")
    assert load_config(p).disclaimer_accepted is False
