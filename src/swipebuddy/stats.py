"""Daily like/pass counters. Stores counts only."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from .paths import stats_path

RETAIN_DAYS = 90
_DECISIONS = ("like", "pass")


def _count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


class StatsStore:
    """Persistent per-day counts of like/pass decisions."""

    def __init__(self, path: Path | None = None, today_fn: Callable[[], date] | None = None) -> None:
        self._path = Path(path) if path is not None else stats_path()
        self._today_fn = today_fn or date.today
        self._data: dict[str, dict[str, int]] = self._load()
        self._prune()

    def _load(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            for day, entry in raw.items():
                date.fromisoformat(day)
                if isinstance(entry, dict):
                    out[day] = {k: _count(entry.get(k)) for k in _DECISIONS}
        except Exception:  # noqa: BLE001 - corrupt file starts empty
            return {}
        return out

    def _prune(self) -> None:
        cutoff = (self._today_fn() - timedelta(days=RETAIN_DAYS)).isoformat()
        for day in [d for d in self._data if d < cutoff]:
            del self._data[day]

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def record(self, decision: str) -> None:
        """Count one ``"like"`` or ``"pass"`` for today."""
        if decision not in _DECISIONS:
            raise ValueError("decision must be 'like' or 'pass'")
        self._prune()
        entry = self._data.setdefault(self._today_fn().isoformat(), {"like": 0, "pass": 0})
        entry[decision] += 1
        self._save()

    def today(self) -> dict[str, int]:
        """Today's counts as ``{"like": n, "pass": n}``."""
        entry = self._data.get(self._today_fn().isoformat(), {})
        return {"like": entry.get("like", 0), "pass": entry.get("pass", 0)}

    def today_total(self) -> int:
        """Total decisions today."""
        t = self.today()
        return t["like"] + t["pass"]
