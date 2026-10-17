"""Tests for swipebuddy.stats."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from swipebuddy.stats import StatsStore


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("SWIPEBUDDY_HOME", str(home))
    return home


class Clock:
    def __init__(self, d: date) -> None:
        self.d = d

    def __call__(self) -> date:
        return self.d


D0 = date(2025, 3, 10)


def test_empty_start(tmp_path: Path) -> None:
    s = StatsStore(tmp_path / "s.json", today_fn=Clock(D0))
    assert s.today() == {"like": 0, "pass": 0}
    assert s.today_total() == 0


def test_increment(tmp_path: Path) -> None:
    s = StatsStore(tmp_path / "s.json", today_fn=Clock(D0))
    s.record("like")
    s.record("like")
    s.record("pass")
    assert s.today() == {"like": 2, "pass": 1}
    assert s.today_total() == 3


def test_persistence_across_instances(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    StatsStore(p, today_fn=Clock(D0)).record("pass")
    s2 = StatsStore(p, today_fn=Clock(D0))
    assert s2.today() == {"like": 0, "pass": 1}
    s2.record("like")
    assert StatsStore(p, today_fn=Clock(D0)).today_total() == 2


def test_default_path_used(_home: Path) -> None:
    StatsStore(today_fn=Clock(D0)).record("like")
    assert (_home / "stats.json").exists()


def test_file_contains_only_counts(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    s = StatsStore(p, today_fn=Clock(D0))
    s.record("like")
    assert json.loads(p.read_text()) == {"2025-03-10": {"like": 1, "pass": 0}}
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]  # no tmp left


def test_day_rollover(tmp_path: Path) -> None:
    clock = Clock(D0)
    s = StatsStore(tmp_path / "s.json", today_fn=clock)
    s.record("like")
    clock.d = D0 + timedelta(days=1)
    assert s.today_total() == 0
    s.record("pass")
    assert s.today() == {"like": 0, "pass": 1}
    clock.d = D0
    assert s.today() == {"like": 1, "pass": 0}


def test_invalid_decision_rejected(tmp_path: Path) -> None:
    s = StatsStore(tmp_path / "s.json", today_fn=Clock(D0))
    for bad in ["Like", "skip", "", "like ", None]:
        with pytest.raises(ValueError):
            s.record(bad)  # type: ignore[arg-type]
    assert s.today_total() == 0


def test_corrupt_file_starts_empty(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    for content in ["{bad", "[]", '{"not-a-date": {"like": 1}}', ""]:
        p.write_text(content)
        s = StatsStore(p, today_fn=Clock(D0))
        assert s.today_total() == 0
        s.record("like")
        assert StatsStore(p, today_fn=Clock(D0)).today_total() == 1


def test_bad_values_sanitized(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"2025-03-10": {"like": "x", "pass": -4, "extra": "name"}}))
    s = StatsStore(p, today_fn=Clock(D0))
    assert s.today() == {"like": 0, "pass": 0}
    s.record("like")
    assert "extra" not in json.loads(p.read_text())["2025-03-10"]


def test_pruning_old_entries(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    old = (D0 - timedelta(days=91)).isoformat()
    edge = (D0 - timedelta(days=90)).isoformat()
    recent = (D0 - timedelta(days=5)).isoformat()
    p.write_text(
        json.dumps(
            {
                old: {"like": 1, "pass": 1},
                edge: {"like": 2, "pass": 0},
                recent: {"like": 3, "pass": 3},
            }
        )
    )
    s = StatsStore(p, today_fn=Clock(D0))
    s.record("like")
    data = json.loads(p.read_text())
    assert old not in data
    assert edge in data and recent in data
    assert D0.isoformat() in data


def test_pruning_after_rollover_in_running_instance(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    clock = Clock(D0)
    s = StatsStore(p, today_fn=clock)
    s.record("like")
    clock.d = D0 + timedelta(days=100)
    s.record("pass")
    assert list(json.loads(p.read_text())) == [clock.d.isoformat()]
