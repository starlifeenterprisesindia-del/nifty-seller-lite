from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RailwayLiveState:
    connected: bool
    tick_count: int
    last_tick_age_seconds: float | None
    nifty_ltp: float | None
    change_5s: float | None
    change_15s: float | None
    change_30s: float | None
    change_60s: float | None
    captured_at: str
    last_error: str


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_railway_live_state(
    base_url: str,
    api_key: str,
    *,
    timeout_seconds: float = 3.0,
) -> RailwayLiveState:
    """Read the protected Railway WebSocket snapshot without exposing its key in a URL."""

    root = str(base_url or "").strip().rstrip("/")
    key = str(api_key or "").strip()
    if not root or not key:
        raise ValueError("Railway live URL or API key is missing")

    request = Request(
        f"{root}/live",
        headers={"X-Live-Key": key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=max(0.5, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("Railway LIVE_API_KEY match nahi hui") from exc
        raise RuntimeError(f"Railway live server HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Railway live server unavailable: {exc.reason}") from exc

    nifty = payload.get("nifty") or {}
    impulse = payload.get("impulse") or {}
    return RailwayLiveState(
        connected=bool(payload.get("connected")),
        tick_count=int(payload.get("tick_count") or 0),
        last_tick_age_seconds=_number(payload.get("last_tick_age_seconds")),
        nifty_ltp=_number(nifty.get("ltp")),
        change_5s=_number(impulse.get("change_5s")),
        change_15s=_number(impulse.get("change_15s")),
        change_30s=_number(impulse.get("change_30s")),
        change_60s=_number(impulse.get("change_60s")),
        captured_at=str(nifty.get("captured_at") or ""),
        last_error=str(payload.get("last_error") or ""),
    )
