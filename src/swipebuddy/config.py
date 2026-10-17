"""Application configuration: dataclasses, load/save and validation."""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from .paths import config_path

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_FREE_TEXT = 2000

ATTRIBUTE_LABELS: dict[str, str] = {
    "glasses_ok": "Glasögon är okej",
    "tattoos_ok": "Tatueringar är okej",
    "beard_ok": "Skägg är okej",
    "smoking_ok": "Rökning är okej",
    "group_photos_ok": "Gruppbilder är okej",
    "prefer_smiling": "Föredrar leende",
    "prefer_outdoors": "Föredrar utomhusbilder",
    "prefer_sporty": "Föredrar sportig stil",
}


def _default_attributes() -> dict[str, bool]:
    return {
        "glasses_ok": True,
        "tattoos_ok": True,
        "beard_ok": True,
        "smoking_ok": True,
        "group_photos_ok": True,
        "prefer_smiling": False,
        "prefer_outdoors": False,
        "prefer_sporty": False,
    }


@dataclass
class Preferences:
    """User preferences handed to the VLM as text hints."""

    free_text: str = ""
    attributes: dict[str, bool] = field(default_factory=_default_attributes)
    age_hint_min: int | None = None
    age_hint_max: int | None = None


@dataclass
class AppConfig:
    """Top-level application configuration."""

    version: int = 1
    preferences: Preferences = field(default_factory=Preferences)
    threshold: int = 70
    mode: Literal["suggest", "auto"] = "suggest"
    daily_cap: int = 100
    delay_min_s: float = 3.0
    delay_max_s: float = 9.0
    pause_every_min: int = 15
    pause_every_max: int = 30
    pause_min_s: float = 60
    pause_max_s: float = 240
    vlm_model: str = "qwen2.5vl:7b"
    ollama_url: str = DEFAULT_OLLAMA_URL
    browser_channel: Literal["msedge", "chromium"] = "msedge"
    max_images_per_profile: int = 6
    disclaimer_accepted: bool = False


def is_local_url(url: str) -> bool:
    """True if ``url`` is an http URL whose host is loopback."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        _ = parts.port  # raises ValueError on a bad port
    except ValueError:
        return False
    return parts.scheme == "http" and host is not None and host.lower() in _LOCAL_HOSTS


def _num(value: Any, default: float) -> float:
    """Return ``value`` if it is a finite real number, else ``default``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(value):
        return default
    return value


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value)


def validate(cfg: AppConfig) -> AppConfig:
    """Return a copy of ``cfg`` with all values clamped to sane ranges."""
    d = AppConfig()
    prefs = cfg.preferences
    free_text = prefs.free_text if isinstance(prefs.free_text, str) else ""
    attrs = {
        str(k): v for k, v in dict(prefs.attributes or {}).items() if isinstance(v, bool)
    }
    amin, amax = _opt_int(prefs.age_hint_min), _opt_int(prefs.age_hint_max)
    if amin is not None and amax is not None and amin > amax:
        amin, amax = amax, amin

    delay_min = max(1.0, float(_num(cfg.delay_min_s, d.delay_min_s)))
    delay_max = max(delay_min, float(_num(cfg.delay_max_s, d.delay_max_s)))
    pe_min = max(0, int(_num(cfg.pause_every_min, d.pause_every_min)))
    pe_max = max(pe_min, int(_num(cfg.pause_every_max, d.pause_every_max)))
    p_min = max(0.0, float(_num(cfg.pause_min_s, d.pause_min_s)))
    p_max = max(p_min, float(_num(cfg.pause_max_s, d.pause_max_s)))

    url = cfg.ollama_url if isinstance(cfg.ollama_url, str) else ""
    model = cfg.vlm_model if isinstance(cfg.vlm_model, str) and cfg.vlm_model.strip() else d.vlm_model

    return AppConfig(
        version=1,
        preferences=Preferences(
            free_text=free_text[:MAX_FREE_TEXT],
            attributes=attrs,
            age_hint_min=amin,
            age_hint_max=amax,
        ),
        threshold=int(max(0, min(100, _num(cfg.threshold, d.threshold)))),
        mode=cfg.mode if cfg.mode in ("suggest", "auto") else "suggest",
        daily_cap=int(max(1, min(1000, _num(cfg.daily_cap, d.daily_cap)))),
        delay_min_s=delay_min,
        delay_max_s=delay_max,
        pause_every_min=pe_min,
        pause_every_max=pe_max,
        pause_min_s=p_min,
        pause_max_s=p_max,
        vlm_model=model.strip(),
        ollama_url=url.strip() if is_local_url(url) else DEFAULT_OLLAMA_URL,
        browser_channel=cfg.browser_channel if cfg.browser_channel in ("msedge", "chromium") else "msedge",
        max_images_per_profile=int(
            max(1, min(10, _num(cfg.max_images_per_profile, d.max_images_per_profile)))
        ),
        disclaimer_accepted=cfg.disclaimer_accepted is True,
    )


def _from_dict(data: Any) -> AppConfig:
    """Build an AppConfig from untrusted JSON data (unknown keys ignored)."""
    cfg = AppConfig()
    if not isinstance(data, dict):
        return validate(cfg)
    for f in fields(AppConfig):
        if f.name in ("preferences", "version") or f.name not in data:
            continue
        setattr(cfg, f.name, data[f.name])
    pdata = data.get("preferences")
    if isinstance(pdata, dict):
        prefs = cfg.preferences
        for f in fields(Preferences):
            if f.name == "attributes" or f.name not in pdata:
                continue
            setattr(prefs, f.name, pdata[f.name])
        attrs = pdata.get("attributes")
        if isinstance(attrs, dict):
            prefs.attributes.update({str(k): v for k, v in attrs.items() if isinstance(v, bool)})
    return validate(cfg)


def load_config(path: Path | None = None) -> AppConfig:
    """Load config; missing or corrupt files yield defaults. Never raises."""
    try:
        p = Path(path) if path is not None else config_path()
        return _from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - any failure falls back to defaults
        return AppConfig()


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    """Validate and atomically write ``cfg`` as UTF-8 JSON (tmp file + replace)."""
    p = Path(path) if path is not None else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(asdict(validate(cfg)), indent=2, ensure_ascii=False)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
