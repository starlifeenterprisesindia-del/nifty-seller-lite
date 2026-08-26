from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


class RailwayAlertStore:
    """Small persistent store for cloud premium alerts (never broker orders)."""

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or os.getenv("RAILWAY_ALERT_STORE_PATH", "/tmp/nsl_alerts.json"))
        self._lock = threading.RLock()

    def _read(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def list(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._read()
        return [dict(row) for row in rows if not active_only or row.get("status") == "ACTIVE"]

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        row = {
            "id": uuid.uuid4().hex[:12],
            "status": "ACTIVE",
            "created_at": now,
            "triggered_at": None,
            **payload,
        }
        with self._lock:
            rows = self._read()
            active = [item for item in rows if item.get("status") == "ACTIVE"]
            if len(active) >= 20:
                raise ValueError("Maximum 20 active premium alerts")
            rows.append(row)
            self._write(rows[-200:])
        return dict(row)

    def cancel(self, alert_id: str) -> bool:
        changed = False
        with self._lock:
            rows = self._read()
            for row in rows:
                if row.get("id") == alert_id and row.get("status") == "ACTIVE":
                    row["status"] = "CANCELLED"
                    changed = True
            if changed:
                self._write(rows)
        return changed

    def mark_triggered(self, alert_id: str, premium: float) -> None:
        with self._lock:
            rows = self._read()
            for row in rows:
                if row.get("id") == alert_id:
                    row["status"] = "TRIGGERED"
                    row["triggered_at"] = time.time()
                    row["triggered_premium"] = round(float(premium), 2)
            self._write(rows)


class PremiumAlertMonitor:
    def __init__(
        self,
        store: RailwayAlertStore,
        quote_fetcher: Callable[[dict[str, list[int]]], dict[str, Any]],
        sender: Callable[[str], None],
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self.store = store
        self.quote_fetcher = quote_fetcher
        self.sender = sender
        self.interval_seconds = max(3.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""

    @staticmethod
    def _triggered(row: dict[str, Any], premium: float) -> bool:
        target = float(row.get("target_premium", 0.0))
        mode = str(row.get("mode", "TOUCH")).upper()
        tolerance = max(0.05, float(row.get("tolerance", 0.5)))
        if mode == "ABOVE":
            return premium >= target
        if mode == "BELOW":
            return premium <= target
        return abs(premium - target) <= tolerance

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            rows = self.store.list(active_only=True)
            if not rows:
                continue
            ids = sorted({int(row["security_id"]) for row in rows})
            try:
                response = self.quote_fetcher({"NSE_FNO": ids})
                segment = ((response or {}).get("data") or {}).get("NSE_FNO") or {}
                for row in rows:
                    quote = segment.get(str(row["security_id"])) or segment.get(int(row["security_id"])) or {}
                    premium = quote.get("last_price", quote.get("LTP"))
                    if premium is None or not self._triggered(row, float(premium)):
                        continue
                    message = (
                        f"🔔 PREMIUM ALERT — {row.get('strike'):,.0f} {row.get('side')} {row.get('position')}\n"
                        f"Current ₹{float(premium):,.2f} · Target ₹{float(row.get('target_premium')):,.2f}\n"
                        f"Mode {row.get('mode')} · Entry {row.get('entry_no', 1)}/3\n"
                        "Alert-only: automatic order nahi lagaya gaya."
                    )
                    self.sender(message)
                    self.store.mark_triggered(str(row["id"]), float(premium))
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)[:300]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="premium-alert-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "active": len(self.store.list(active_only=True)),
            "last_error": self.last_error,
            "interval_seconds": self.interval_seconds,
        }
