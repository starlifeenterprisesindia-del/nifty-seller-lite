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
    """Bounded shared FII/DII journal with redundant atomic persistence.

    One row is kept per trading date. Saving the same date updates only that row. A
    primary and mirror JSON are monotonically merged on every read, so one stale/corrupt
    copy cannot make already-saved dates disappear while the deployment filesystem lives.
    """

    SCHEMA_VERSION = 1
    MAX_ABS_CRORE = 100_000.0
    ALLOWED_EVENT_RISK = {"NONE", "LOW", "MEDIUM", "HIGH"}

    def __init__(
        self,
        path: str | Path | None = None,
        mirror_path: str | Path | None = None,
    ):
        if path is None:
            self.path = Path(CONFIG.market_context_path)
            self.mirror_path = Path(mirror_path or CONFIG.market_context_mirror_path)
        else:
            self.path = Path(path)
            self.mirror_path = Path(
                mirror_path
                or self.path.with_name(f"{self.path.stem}.mirror{self.path.suffix}")
            )
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

    @classmethod
    def _number(cls, value: Any, field_name: str = "value") -> float | None:
        if value in (None, ""):
            return None
        try:
            number = round(float(value), 4)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid {field_name}: {value!r}") from None
        if abs(number) > cls.MAX_ABS_CRORE:
            raise ValueError(
                f"{field_name} looks too large ({number:,.2f} crore). "
                "Enter the daily net amount in crore, not contracts or cumulative quantity."
            )
        return number

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "entries": []}

    def _read_path(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return self._empty()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != self.SCHEMA_VERSION
            or not isinstance(data.get("entries"), list)
        ):
            return self._empty()
        return data

    @staticmethod
    def _updated_key(item: dict[str, Any]) -> str:
        return str(item.get("updated_at") or "")

    @staticmethod
    def _sorted_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean = [item for item in entries if isinstance(item, dict) and item.get("date")]
        clean.sort(key=lambda item: str(item.get("date", "")))
        return clean

    def _merge_entries(self, *entry_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_date: dict[str, dict[str, Any]] = {}
        for group in entry_groups:
            for item in self._sorted_entries(group):
                key = str(item.get("date"))
                current = by_date.get(key)
                if current is None or self._updated_key(item) >= self._updated_key(current):
                    by_date[key] = dict(item)
        return self._sorted_entries(list(by_date.values()))[-CONFIG.market_context_max_entries :]

    def _read_unlocked(self) -> dict[str, Any]:
        primary = self._read_path(self.path)
        mirror = self._read_path(self.mirror_path)
        merged = self._merge_entries(primary["entries"], mirror["entries"])
        return {"schema_version": self.SCHEMA_VERSION, "entries": merged}

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        clean = {
            "schema_version": self.SCHEMA_VERSION,
            "entries": self._sorted_entries(data.get("entries", []))[
                -CONFIG.market_context_max_entries :
            ],
        }
        # Write both copies. A future read merges them if one write was interrupted.
        self._atomic_write(self.path, clean)
        self._atomic_write(self.mirror_path, clean)

    def load(self) -> list[dict[str, Any]]:
        with self._locked():
            data = self._read_unlocked()
            # Self-heal both copies after a successful monotonic merge.
            self._write_unlocked(data)
            return list(data["entries"])

    def get(self, session_date: date) -> dict[str, Any] | None:
        target = session_date.isoformat()
        with self._locked():
            data = self._read_unlocked()
            self._write_unlocked(data)
            for item in data["entries"]:
                if item.get("date") == target:
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
            "fii_cash_net": self._number(fii_cash_net, "FII cash net"),
            "dii_cash_net": self._number(dii_cash_net, "DII cash net"),
            "fii_index_futures_net": self._number(
                fii_index_futures_net, "FII index futures net"
            ),
            "event_risk": level,
            "event_note": str(event_note or "").strip()[:280],
            "verified": bool(verified),
            "source": "MANUAL",
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        with self._locked():
            data = self._read_unlocked()
            entries = [item for item in data["entries"] if item.get("date") != entry["date"]]
            entries.append(entry)
            data["entries"] = self._sorted_entries(entries)[-CONFIG.market_context_max_entries :]
            self._write_unlocked(data)
            return list(data["entries"])

    def delete_date(self, session_date: date) -> list[dict[str, Any]]:
        target = session_date.isoformat()
        with self._locked():
            data = self._read_unlocked()
            data["entries"] = self._sorted_entries(
                [item for item in data["entries"] if item.get("date") != target]
            )
            self._write_unlocked(data)
            return list(data["entries"])

    def export_bytes(self) -> bytes:
        with self._locked():
            data = self._read_unlocked()
            self._write_unlocked(data)
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
                    "fii_cash_net": self._number(raw.get("fii_cash_net"), "FII cash net"),
                    "dii_cash_net": self._number(raw.get("dii_cash_net"), "DII cash net"),
                    "fii_index_futures_net": self._number(
                        raw.get("fii_index_futures_net"), "FII index futures net"
                    ),
                    "event_risk": level,
                    "event_note": str(raw.get("event_note") or "").strip()[:280],
                    "verified": verified,
                    "source": str(raw.get("source") or "BACKUP")[:40],
                    "updated_at": str(raw.get("updated_at") or datetime.now().astimezone().isoformat()),
                }
            )

        with self._locked():
            current = self._read_unlocked()["entries"]
            # Restore is a merge, not a destructive replacement. Same-date newer rows win.
            entries = self._merge_entries(current, validated)
            self._write_unlocked({"schema_version": self.SCHEMA_VERSION, "entries": entries})
        return entries

    def clear(self) -> None:
        with self._locked():
            for path in (self.path, self.mirror_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
