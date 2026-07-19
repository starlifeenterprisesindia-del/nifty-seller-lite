from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from config import CONFIG

try:  # Linux/Streamlit runtime.
    import fcntl
except ImportError:  # pragma: no cover - Windows local fallback.
    fcntl = None


class MarketContextStore:
    """Small durable journal for optional FII/DII and verified event-risk context."""

    SCHEMA_VERSION = 1
    ALLOWED_EVENT_RISK = {"NONE", "LOW", "MEDIUM", "HIGH"}

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or CONFIG.market_context_path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid numeric context value: {value!r}") from None

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "entries": []}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != self.SCHEMA_VERSION
            or not isinstance(data.get("entries"), list)
        ):
            return self._empty()
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def load(self) -> list[dict[str, Any]]:
        with self._locked():
            return list(self._read_unlocked()["entries"])

    def upsert(
        self,
        *,
        session_date: date,
        fii_cash_net: float | None,
        dii_cash_net: float | None,
        event_risk: str,
        event_note: str = "",
        verified: bool = False,
    ) -> list[dict[str, Any]]:
        level = str(event_risk or "NONE").strip().upper()
        if level not in self.ALLOWED_EVENT_RISK:
            raise ValueError(f"Unsupported event risk: {level}")
        if level in {"MEDIUM", "HIGH"} and not verified:
            raise ValueError("Medium/high event risk must be marked verified")
        entry = {
            "date": session_date.isoformat(),
            "fii_cash_net": self._number(fii_cash_net),
            "dii_cash_net": self._number(dii_cash_net),
            "event_risk": level,
            "event_note": str(event_note or "").strip()[:280],
            "verified": bool(verified),
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        with self._locked():
            data = self._read_unlocked()
            entries = [
                item
                for item in data["entries"]
                if isinstance(item, dict) and item.get("date") != entry["date"]
            ]
            entries.append(entry)
            entries.sort(key=lambda item: str(item.get("date", "")))
            entries = entries[-CONFIG.market_context_max_entries :]
            data["entries"] = entries
            self._write_unlocked(data)
            return list(entries)

    def clear(self) -> None:
        with self._locked():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
