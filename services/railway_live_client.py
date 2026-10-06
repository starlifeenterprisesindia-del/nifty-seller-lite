from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import requests


_SESSION_LOCK = threading.Lock()
_SESSIONS: dict[str, requests.Session] = {}


def _session_for(base_url: str) -> requests.Session:
    """Return a process-wide keep-alive pool for one Railway service.

    Full snapshots make several small protected calls to the same Railway host and
    the 5-second live monitor polls it continuously. Reusing TCP/TLS connections
    removes repeated handshake latency without changing any market calculation.
    """

    root = str(base_url or "").strip().rstrip("/")
    with _SESSION_LOCK:
        session = _SESSIONS.get(root)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=8, pool_maxsize=16, max_retries=0
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            _SESSIONS[root] = session
        return session


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


def fetch_railway_health(
    base_url: str,
    api_key: str,
    *,
    timeout_seconds: float = 1.5,
) -> dict[str, Any]:
    """Lightweight Railway readiness probe used only during cold start.

    This endpoint performs no market-data fetch.  It prevents Streamlit from
    launching a full snapshot while Railway is between deployments.
    """
    root = str(base_url or "").strip().rstrip("/")
    key = str(api_key or "").strip()
    if not root or not key:
        raise ValueError("Railway live URL or API key is missing")
    try:
        response = _session_for(root).get(
            f"{root}/ready",
            headers={"X-Live-Key": key, "Accept": "application/json"},
            timeout=max(0.5, float(timeout_seconds)),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Railway readiness unavailable: {exc}") from exc
    if response.status_code == 401:
        raise RuntimeError("Railway LIVE_API_KEY match nahi hui")
    if response.status_code == 503:
        return {"ready": False}
    if response.status_code >= 400:
        raise RuntimeError(f"Railway readiness HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Railway readiness returned invalid JSON") from exc
    if not isinstance(payload, dict):
        return {"ready": False}
    payload = dict(payload)
    payload["ready"] = bool(payload.get("ready", True))
    return payload


def fetch_railway_premium_alerts(
    base_url: str,
    api_key: str,
    *,
    timeout_seconds: float = 3.0,
) -> list[dict[str, Any]]:
    """Read durable Railway premium-alert state; no broker/Dhan request is made."""
    root = str(base_url or "").strip().rstrip("/")
    key = str(api_key or "").strip()
    if not root or not key:
        return []
    try:
        response = _session_for(root).get(
            f"{root}/alerts",
            headers={"X-Live-Key": key, "Accept": "application/json"},
            timeout=max(0.5, float(timeout_seconds)),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Railway alert state unavailable: {exc}") from exc
    if response.status_code == 401:
        raise RuntimeError("Railway LIVE_API_KEY match nahi hui")
    if response.status_code >= 400:
        raise RuntimeError(f"Railway alerts HTTP {response.status_code}")
    try:
        envelope = response.json()
    except ValueError as exc:
        raise RuntimeError("Railway alerts returned invalid JSON") from exc
    data = envelope.get("data") if isinstance(envelope, dict) else None
    rows = (data or {}).get("alerts") if isinstance(data, dict) else None
    return [dict(item) for item in (rows or []) if isinstance(item, dict)]


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

    try:
        response = _session_for(root).get(
            f"{root}/live",
            headers={"X-Live-Key": key, "Accept": "application/json"},
            timeout=max(0.5, float(timeout_seconds)),
        )
        if response.status_code == 401:
            raise RuntimeError("Railway LIVE_API_KEY match nahi hui")
        if response.status_code >= 400:
            raise RuntimeError(f"Railway live server HTTP {response.status_code}")
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Railway live server unavailable: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("Railway live server returned invalid JSON") from exc

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
        # Phase-11 transport cache: one Railway HTTP request can prefetch the raw
        # components needed by a full snapshot.  It changes transport only; every
        # downstream calculation still consumes the same method contracts below.
        self._prefetched_market_quote: tuple[str, Any] | None = None
        self._prefetched_intraday: dict[str, Any] = {}
        self._prefetched_expiry: tuple[int, str, list[str]] | None = None
        self._prefetched_option_chain: tuple[str, int, str, Any] | None = None
        # Same Railway response also carries the latest NIFTY WebSocket tick.
        # It is used only to make the full snapshot spot price fresh; no extra
        # Dhan or Railway request is introduced.
        self._prefetched_live_state: dict[str, Any] | None = None
        self._transport_http_calls = 0
        self._transport_bundle_calls = 0
        self._transport_prefetch_hits = 0
        self._transport_bundle_seconds = 0.0
        self._transport_bundle_errors: dict[str, str] = {}

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        self._transport_http_calls += 1
        try:
            response = _session_for(self.base_url).post(
                f"{self.base_url}{path}",
                headers={
                    "X-Live-Key": self.api_key,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Railway Dhan gateway unavailable: {exc}") from exc

        if response.status_code == 401:
            raise RuntimeError("Railway LIVE_API_KEY match nahi hui")
        if response.status_code >= 400:
            detail = response.text[:300]
            raise RuntimeError(
                f"Railway Dhan gateway HTTP {response.status_code}: {detail}"
            )
        try:
            envelope = response.json()
        except ValueError as exc:
            raise RuntimeError("Railway Dhan gateway returned invalid JSON") from exc
        if not isinstance(envelope, dict) or not envelope.get("ok"):
            raise RuntimeError(
                str(envelope.get("error") if isinstance(envelope, dict) else envelope)
            )
        return envelope.get("data")

    def download_bytes(self, path: str, *, maximum_bytes: int = 80 * 1024 * 1024) -> bytes:
        """Download a protected binary response with an explicit client-side cap."""
        request = Request(
            f"{self.base_url}{path}",
            headers={"X-Live-Key": self.api_key, "Accept": "application/gzip"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=max(30.0, self.timeout_seconds)) as response:
                length = int(response.headers.get("Content-Length") or 0)
                if length > maximum_bytes:
                    raise RuntimeError("Evidence export is larger than the safe download limit")
                data = response.read(maximum_bytes + 1)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Railway evidence export HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Railway evidence export unavailable: {exc.reason}") from exc
        if len(data) > maximum_bytes:
            raise RuntimeError("Evidence export is larger than the safe download limit")
        return data

    @staticmethod
    def _quote_signature(instruments: dict[str, list[int]]) -> str:
        normalized = {
            str(segment): sorted({int(item) for item in ids})
            for segment, ids in (instruments or {}).items()
            if ids
        }
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _intraday_signature(payload: dict[str, Any]) -> str:
        normalized = {
            "security_id": str(payload.get("security_id", "")),
            "exchange_segment": str(payload.get("exchange_segment", "")),
            "instrument": str(payload.get("instrument", "")),
            "interval": int(payload.get("interval", 1) or 1),
            "from_date": str(payload.get("from_date", "")),
            "to_date": str(payload.get("to_date", "")),
            "include_oi": bool(payload.get("include_oi", False)),
        }
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"))

    def prepare_snapshot_bundle(
        self,
        *,
        instruments: dict[str, list[int]],
        candle_requests: dict[str, dict[str, Any]],
        underlying_security_id: int = 13,
        segment: str = "IDX_I",
        as_of: Any = None,
    ) -> dict[str, Any]:
        """Prefetch one snapshot's raw Railway payload in one HTTP round trip.

        The Railway server still enforces the same Dhan cache/rate-limit gates.
        Missing partial items simply fall back to the legacy per-endpoint method, so
        this optimization cannot turn a partial bundle into fabricated market data.
        """
        payload = {
            "instruments": instruments,
            "candles": candle_requests,
            "underlying_security_id": int(underlying_security_id),
            "segment": str(segment),
            "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of or ""),
        }
        started = time.perf_counter()
        data = self._post("/dhan/snapshot-bundle", payload)
        self._transport_bundle_calls += 1
        self._transport_bundle_seconds = round(time.perf_counter() - started, 4)
        if not isinstance(data, dict):
            return {}
        self._transport_bundle_errors = {
            str(k): str(v)[:120] for k, v in (data.get("errors") or {}).items()
        }
        live_state = data.get("live_state")
        if isinstance(live_state, dict):
            self._prefetched_live_state = dict(live_state)
        quote = data.get("market_quote")
        if isinstance(quote, dict):
            self._prefetched_market_quote = (self._quote_signature(instruments), quote)
        candles = data.get("candles") or {}
        for label, request_payload in (candle_requests or {}).items():
            if label in candles and isinstance(candles.get(label), dict):
                self._prefetched_intraday[self._intraday_signature(request_payload)] = candles[label]
        expiries = data.get("expiry_list")
        if isinstance(expiries, list):
            self._prefetched_expiry = (int(underlying_security_id), str(segment), [str(x) for x in expiries])
        option = data.get("option_chain")
        selected_expiry = str(data.get("selected_expiry") or "")
        if selected_expiry and isinstance(option, dict):
            self._prefetched_option_chain = (
                selected_expiry, int(underlying_security_id), str(segment), option
            )
        return data

    def snapshot_live_state(self) -> dict[str, Any]:
        """Return the WebSocket state bundled with this snapshot request.

        This is intentionally a memory read only.  SnapshotService can use the
        fresh NIFTY tick without making another HTTP/Dhan request.
        """
        return dict(self._prefetched_live_state or {})

    def transport_status(self) -> dict[str, Any]:
        saved = max(0, self._transport_prefetch_hits - self._transport_bundle_calls)
        return {
            "mode": "COMBINED_RAILWAY_BUNDLE" if self._transport_bundle_calls else "LEGACY_ENDPOINTS",
            "http_calls": int(self._transport_http_calls),
            "bundle_calls": int(self._transport_bundle_calls),
            "prefetch_hits": int(self._transport_prefetch_hits),
            "estimated_round_trips_saved": int(saved),
            "bundle_seconds": round(float(self._transport_bundle_seconds), 4),
            "bundle_errors": dict(self._transport_bundle_errors),
            "live_tick_bundled": bool(self._prefetched_live_state),
        }

    def market_quote(self, instruments: dict[str, list[int]]) -> dict[str, Any]:
        if self._prefetched_market_quote is not None:
            signature, data = self._prefetched_market_quote
            if signature == self._quote_signature(instruments):
                self._transport_prefetch_hits += 1
                return data
        return self._post("/dhan/market-quote", {"instruments": instruments})

    def market_history(self, expiry: str) -> dict[str, Any]:
        # No Dhan request: read existing recorder observations only.
        client = RailwayDhanClient(self.base_url, self.api_key, timeout_seconds=3)
        return client._post("/market-history", {"expiry": expiry})

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
        payload = {
            "security_id": str(security_id),
            "exchange_segment": exchange_segment,
            "instrument": instrument,
            "interval": int(interval),
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "include_oi": bool(include_oi),
        }
        signature = self._intraday_signature(payload)
        if signature in self._prefetched_intraday:
            self._transport_prefetch_hits += 1
            return self._prefetched_intraday[signature]
        return self._post("/dhan/intraday", payload)

    def expiry_list(self, underlying_security_id: int = 13, segment: str = "IDX_I") -> list[str]:
        if self._prefetched_expiry is not None:
            sid, seg, data = self._prefetched_expiry
            if sid == int(underlying_security_id) and seg == str(segment):
                self._transport_prefetch_hits += 1
                return list(data)
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
        if self._prefetched_option_chain is not None:
            exp, sid, seg, data = self._prefetched_option_chain
            if exp == str(expiry) and sid == int(underlying_security_id) and seg == str(segment):
                self._transport_prefetch_hits += 1
                return data
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
