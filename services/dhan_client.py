from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests

from config import CONFIG, DHAN_BASE_URL
from models import Credentials
from services.errors import DhanAPIError


class DhanClient:
    # Read-only client. There are intentionally no order methods here.
    def __init__(self, credentials: Credentials, session: requests.Session | None = None):
        credentials.validate()
        self.credentials = credentials
        self.session = session or requests.Session()
        self.timeout = CONFIG.request_timeout_seconds
        self._last_option_chain_call = 0.0

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": self.credentials.access_token,
            "client-id": self.credentials.client_id,
        }

    def _post(self, path: str, payload: dict[str, Any], *, retry_once: bool = True) -> dict[str, Any]:
        url = f"{DHAN_BASE_URL}{path}"
        attempts = 2 if retry_once else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.session.post(url, headers=self.headers, json=payload, timeout=self.timeout)
                data = response.json() if response.content else {}
                if response.status_code >= 400:
                    message = data.get("errorMessage") or data.get("message") or response.text
                    raise DhanAPIError(f"DhanHQ HTTP {response.status_code}: {message}")
                if isinstance(data, dict) and data.get("status") not in (None, "success"):
                    message = data.get("errorMessage") or data.get("message") or str(data)
                    raise DhanAPIError(f"DhanHQ unsuccessful response: {message}")
                if not isinstance(data, dict):
                    raise DhanAPIError("DhanHQ returned non-object JSON")
                return data
            except (requests.RequestException, ValueError, DhanAPIError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.6)
                    continue
                break
        if isinstance(last_error, DhanAPIError):
            raise last_error
        raise DhanAPIError(f"DhanHQ request failed: {last_error}")

    def market_quote(self, instruments: dict[str, list[int]]) -> dict[str, Any]:
        cleaned = {
            segment: sorted({int(item) for item in ids})
            for segment, ids in instruments.items()
            if ids
        }
        if not cleaned:
            return {"status": "success", "data": {}}
        return self._post("/marketfeed/quote", cleaned)

    def intraday_candles(
        self,
        *,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        interval: int,
        from_date: datetime,
        to_date: datetime,
        include_oi: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "interval": str(interval),
            "oi": bool(include_oi),
            "fromDate": from_date.strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": to_date.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return self._post("/charts/intraday", payload)

    def expiry_list(self, underlying_security_id: int = 13, segment: str = "IDX_I") -> list[str]:
        payload = {"UnderlyingScrip": int(underlying_security_id), "UnderlyingSeg": segment}
        response = self._post("/optionchain/expirylist", payload)
        data = response.get("data", [])
        if not isinstance(data, list):
            raise DhanAPIError("Expiry list response is malformed")
        return [str(item) for item in data]

    def option_chain(
        self,
        *,
        expiry: str,
        underlying_security_id: int = 13,
        segment: str = "IDX_I",
    ) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_option_chain_call
        if elapsed < 3.05:
            time.sleep(3.05 - elapsed)
        payload = {
            "UnderlyingScrip": int(underlying_security_id),
            "UnderlyingSeg": segment,
            "Expiry": expiry,
        }
        response = self._post("/optionchain", payload)
        self._last_option_chain_call = time.monotonic()
        return response
