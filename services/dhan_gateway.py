from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Callable

from models import Credentials
from services.dhan_client import DhanClient


class DhanGateway:
    """One process-wide Dhan client with cache, spacing and shared 429 backoff."""

    def __init__(self, client_id: str, access_token: str) -> None:
        self.client = DhanClient(Credentials(client_id=client_id, access_token=access_token))
        self._lock = threading.RLock()
        # Responses can contain seven days of candles. A plain dict keyed by the
        # minute-specific to_date retained every old response forever and slowly
        # exhausted Railway RAM. Store TTL per item and enforce a hard LRU cap.
        self._cache: OrderedDict[str, tuple[float, float, Any]] = OrderedDict()
        self._cache_max_entries = max(
            8, min(64, int(os.getenv("DHAN_GATEWAY_CACHE_MAX_ENTRIES", "32") or 32))
        )
        self._last_call: dict[str, float] = {}
        self._blocked_until = 0.0
        self.last_error = ""
        self._last_foreground_at = 0.0

    def mark_foreground(self) -> None:
        with self._lock:
            self._last_foreground_at = time.monotonic()

    def foreground_idle_seconds(self) -> float:
        with self._lock:
            if self._last_foreground_at <= 0:
                return 9999.0
            return max(0.0, time.monotonic() - self._last_foreground_at)

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
            expired = [
                cache_key
                for cache_key, (saved_at, ttl, _value) in self._cache.items()
                if now - saved_at > ttl
            ]
            for cache_key in expired:
                self._cache.pop(cache_key, None)
            cached = self._cache.get(key)
            if cached and now - cached[0] <= cached[1]:
                self._cache.move_to_end(key)
                return cached[2]
            if now < self._blocked_until:
                if cached:
                    return cached[2]
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
                    return cached[2]
                raise
            self.last_error = ""
            self._cache[key] = (time.monotonic(), cache_seconds, result)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_max_entries:
                self._cache.popitem(last=False)
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
            "cache_max_entries": self._cache_max_entries,
        }
