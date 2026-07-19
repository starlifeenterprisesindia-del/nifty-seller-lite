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
    """Bounded date-wise journal for institutional and verified event context.

    One row is kept per trading date. Saving the same date updates that row; saving a
    different date never overwrites another date. The journal is capped at the latest
    configured trading-session records.
    """

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

    @staticmethod
    def _sorted_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean = [
            item for item in entries if isinstance(item, dict) and item.get("date")
        ]
        clean.sort(key=lambda item: str(item.get("date", "")))
        return clean

    def load(self) -> list[dict[str, Any]]:
        with self._locked():
            return list(self._sorted_entries(self._read_unlocked()["entries"]))

    def get(self, session_date: date) -> dict[str, Any] | None:
        target = session_date.isoformat()
        with self._locked():
            for item in self._read_unlocked()["entries"]:
                if isinstance(item, dict) and item.get("date") == target:
                    return dict(item)
        return None

    def upsert(
        self,
        *,
        session_date: date,
        fii_cash_net: float | None,
        dii_cash_net: float | None,
        fii_index_futures_net: float | None = None,
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
            "fii_index_futures_net": self._number(fii_index_futures_net),
            "event_risk": level,
            "event_note": str(event_note or "").strip()[:280],
            "verified": bool(verified),
            "source": "MANUAL",
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
            entries = self._sorted_entries(entries)[
                -CONFIG.market_context_max_entries :
            ]
            data["entries"] = entries
            self._write_unlocked(data)
            return list(entries)

    def delete_date(self, session_date: date) -> list[dict[str, Any]]:
        target = session_date.isoformat()
        with self._locked():
            data = self._read_unlocked()
            data["entries"] = self._sorted_entries(
                [
                    item
                    for item in data["entries"]
                    if not (isinstance(item, dict) and item.get("date") == target)
                ]
            )
            self._write_unlocked(data)
            return list(data["entries"])

    def export_bytes(self) -> bytes:
        with self._locked():
            data = self._read_unlocked()
            data["entries"] = self._sorted_entries(data["entries"])[
                -CONFIG.market_context_max_entries :
            ]
            return json.dumps(data, indent=2, sort_keys=True).encode("utf-8")

    def import_bytes(self, payload: bytes) -> list[dict[str, Any]]:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid context backup: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ValueError("Invalid context backup structure")

        validated: list[dict[str, Any]] = []
        for raw in data["entries"]:
            if not isinstance(raw, dict):
                continue
            try:
                session_date = date.fromisoformat(str(raw.get("date")))
            except ValueError:
                continue
            level = str(raw.get("event_risk") or "NONE").upper()
            verified = bool(raw.get("verified"))
            if level not in self.ALLOWED_EVENT_RISK:
                level = "NONE"
            if level in {"MEDIUM", "HIGH"} and not verified:
                level = "NONE"
            validated.append(
                {
                    "date": session_date.isoformat(),
                    "fii_cash_net": self._number(raw.get("fii_cash_net")),
                    "dii_cash_net": self._number(raw.get("dii_cash_net")),
                    "fii_index_futures_net": self._number(
                        raw.get("fii_index_futures_net")
                    ),
                    "event_risk": level,
                    "event_note": str(raw.get("event_note") or "").strip()[:280],
                    "verified": verified,
                    "source": str(raw.get("source") or "BACKUP")[:40],
                    "updated_at": str(raw.get("updated_at") or ""),
                }
            )

        deduped: dict[str, dict[str, Any]] = {
            item["date"]: item for item in self._sorted_entries(validated)
        }
        entries = list(deduped.values())[-CONFIG.market_context_max_entries :]
        with self._locked():
            self._write_unlocked(
                {"schema_version": self.SCHEMA_VERSION, "entries": entries}
            )
        return entries

    def clear(self) -> None:
        with self._locked():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
