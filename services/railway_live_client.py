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


class RailwayDhanClient:
    """Read-only Dhan-compatible client backed by the single Railway gateway."""

    def __init__(self, base_url: str, api_key: str, *, timeout_seconds: float = 15.0):
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        if not self.base_url or not self.api_key:
            raise ValueError("Railway live URL or API key is missing")

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "X-Live-Key": self.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code == 401:
                raise RuntimeError("Railway LIVE_API_KEY match nahi hui") from exc
            raise RuntimeError(f"Railway Dhan gateway HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Railway Dhan gateway unavailable: {exc.reason}") from exc
        if not isinstance(envelope, dict) or not envelope.get("ok"):
            raise RuntimeError(str(envelope.get("error") if isinstance(envelope, dict) else envelope))
        return envelope.get("data")

    def market_quote(self, instruments: dict[str, list[int]]) -> dict[str, Any]:
        return self._post("/dhan/market-quote", {"instruments": instruments})

    def intraday_candles(
        self,
        *,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        interval: int,
        from_date: Any,
        to_date: Any,
        include_oi: bool = False,
    ) -> dict[str, Any]:
        return self._post(
            "/dhan/intraday",
            {
                "security_id": str(security_id),
                "exchange_segment": exchange_segment,
                "instrument": instrument,
                "interval": int(interval),
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "include_oi": bool(include_oi),
            },
        )

    def expiry_list(self, underlying_security_id: int = 13, segment: str = "IDX_I") -> list[str]:
        data = self._post(
            "/dhan/expiry-list",
            {"underlying_security_id": int(underlying_security_id), "segment": segment},
        )
        return [str(item) for item in (data or [])]

    def option_chain(
        self,
        *,
        expiry: str,
        underlying_security_id: int = 13,
        segment: str = "IDX_I",
    ) -> dict[str, Any]:
        return self._post(
            "/dhan/option-chain",
            {
                "expiry": str(expiry),
                "underlying_security_id": int(underlying_security_id),
                "segment": segment,
            },
        )


def post_railway_json(
    base_url: str,
    api_key: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Post an alert/evidence command to Railway using header-only authentication."""
    client = RailwayDhanClient(base_url, api_key, timeout_seconds=timeout_seconds)
    result = client._post(path, payload)
    return result if isinstance(result, dict) else {"result": result}


def delete_railway_alert(base_url: str, api_key: str, alert_id: str) -> bool:
    root = str(base_url or "").strip().rstrip("/")
    request = Request(
        f"{root}/alerts/{alert_id}",
        headers={"X-Live-Key": str(api_key or "").strip(), "Accept": "application/json"},
        method="DELETE",
    )
    try:
        with urlopen(request, timeout=5.0) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Railway alert cancel failed: {exc}") from exc
    return bool(((envelope.get("data") or {}).get("cancelled")))
