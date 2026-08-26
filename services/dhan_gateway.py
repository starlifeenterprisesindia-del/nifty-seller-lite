from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Any, Callable

from models import Credentials
from services.dhan_client import DhanClient


class DhanGateway:
    """One process-wide Dhan client with cache, spacing and shared 429 backoff."""

    def __init__(self, client_id: str, access_token: str) -> None:
        self.client = DhanClient(Credentials(client_id=client_id, access_token=access_token))
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_call: dict[str, float] = {}
        self._blocked_until = 0.0
        self.last_error = ""

    @staticmethod
    def _key(name: str, payload: Any) -> str:
        return f"{name}:{json.dumps(payload, sort_keys=True, default=str)}"

    def _run(
        self,
        name: str,
        payload: Any,
        function: Callable[[], Any],
        *,
        cache_seconds: float,
        min_spacing_seconds: float,
    ) -> Any:
        key = self._key(name, payload)
        with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now - cached[0] <= cache_seconds:
                return cached[1]
            if now < self._blocked_until:
                if cached:
                    return cached[1]
                raise RuntimeError(
                    f"Dhan rate-limit cooldown active; {self._blocked_until - now:.1f}s wait"
                )
            wait = min_spacing_seconds - (now - self._last_call.get(name, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_call[name] = time.monotonic()
            try:
                result = function()
            except Exception as exc:
                self.last_error = str(exc)[:300]
                if "429" in self.last_error or "Too many requests" in self.last_error:
                    self._blocked_until = time.monotonic() + 15.0
                if cached:
                    return cached[1]
                raise
            self.last_error = ""
            self._cache[key] = (time.monotonic(), result)
            return result

    def market_quote(self, instruments: dict[str, list[int]]) -> dict[str, Any]:
        return self._run(
            "market_quote",
            instruments,
            lambda: self.client.market_quote(instruments),
            cache_seconds=1.8,
            min_spacing_seconds=1.05,
        )

    def intraday(self, payload: dict[str, Any]) -> dict[str, Any]:
        cache_payload = dict(payload)
        # Snapshot timestamps differ by seconds, but the completed candle does not.
        # Canonicalising the cache key prevents identical candle requests on every
        # Streamlit rerun while still refreshing at the next minute boundary.
        try:
            to_dt = datetime.fromisoformat(str(payload["to_date"]))
            cache_payload["to_date"] = to_dt.replace(second=0, microsecond=0).isoformat()
        except (KeyError, ValueError):
            pass
        return self._run(
            "intraday",
            cache_payload,
            lambda: self.client.intraday_candles(
                security_id=str(payload["security_id"]),
                exchange_segment=str(payload["exchange_segment"]),
                instrument=str(payload["instrument"]),
                interval=int(payload["interval"]),
                from_date=datetime.fromisoformat(str(payload["from_date"])),
                to_date=datetime.fromisoformat(str(payload["to_date"])),
                include_oi=bool(payload.get("include_oi", False)),
            ),
            cache_seconds=18.0,
            min_spacing_seconds=0.45,
        )

    def expiry_list(self, underlying_security_id: int, segment: str) -> list[str]:
        payload = {"underlying_security_id": underlying_security_id, "segment": segment}
        return self._run(
            "expiry_list",
            payload,
            lambda: self.client.expiry_list(underlying_security_id, segment),
            cache_seconds=1800.0,
            min_spacing_seconds=3.1,
        )

    def option_chain(self, expiry: str, underlying_security_id: int, segment: str) -> dict[str, Any]:
        payload = {
            "expiry": expiry,
            "underlying_security_id": underlying_security_id,
            "segment": segment,
        }
        return self._run(
            "option_chain",
            payload,
            lambda: self.client.option_chain(
                expiry=expiry,
                underlying_security_id=underlying_security_id,
                segment=segment,
            ),
            cache_seconds=4.0,
            min_spacing_seconds=3.1,
        )

    def status(self) -> dict[str, Any]:
        remaining = max(0.0, self._blocked_until - time.monotonic())
        return {
            "configured": True,
            "rate_limit_cooldown_seconds": round(remaining, 1),
            "last_error": self.last_error,
            "cache_entries": len(self._cache),
        }
