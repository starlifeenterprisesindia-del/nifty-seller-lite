from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from config import CONFIG

try:  # Linux/Streamlit runtime.
    import fcntl
except ImportError:  # pragma: no cover - Windows local fallback.
    fcntl = None


class OptionStateStore:
    """Bounded same-day persistence for option-chain comparison snapshots.

    It stores only market fields needed for flow comparison. Credentials, headers,
    order data and arbitrary session data are never written.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or CONFIG.option_state_path)
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
    def _clean_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if pd.isna(number):
            return None
        return number

    @classmethod
    def frame_rows(cls, frame: pd.DataFrame) -> list[dict[str, Any]]:
        keep = (
            "strike",
            "side",
            "last_price",
            "oi",
            "volume",
            "previous_oi",
            "previous_volume",
            "previous_close_price",
            "day_oi_change",
            "day_price_change",
            "implied_volatility",
            "is_atm",
        )
        rows: list[dict[str, Any]] = []
        for raw in frame.to_dict(orient="records"):
            row: dict[str, Any] = {}
            for key in keep:
                value = raw.get(key)
                if key == "side":
                    row[key] = str(value or "").upper()
                elif key == "is_atm":
                    row[key] = bool(value)
                else:
                    row[key] = cls._clean_number(value)
            if row.get("strike") is not None and row.get("side") in {"CE", "PE"}:
                rows.append(row)
        rows.sort(key=lambda item: (item["strike"], item["side"]))
        return rows

    @staticmethod
    def _fingerprint(
        expiry: str, spot: float | None, rows: list[dict[str, Any]]
    ) -> str:
        payload = {"expiry": expiry, "spot": spot, "rows": rows}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]

    def make_snapshot(
        self,
        *,
        captured_at: datetime,
        expiry: str,
        spot: float | None,
        frame: pd.DataFrame,
    ) -> dict[str, Any]:
        rows = self.frame_rows(frame)
        clean_spot = self._clean_number(spot)
        return {
            "captured_at": captured_at.isoformat(),
            "expiry": str(expiry),
            "spot": clean_spot,
            "fingerprint": self._fingerprint(str(expiry), clean_spot, rows),
            "rows": rows,
        }

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "sessions": {}}

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
        ):
            return self._empty()
        if not isinstance(data.get("sessions"), dict):
            return self._empty()
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _session_key(captured_at: datetime, expiry: str) -> str:
        return f"{captured_at.date().isoformat()}|{expiry}"

    def load_session(
        self, *, captured_at: datetime, expiry: str
    ) -> list[dict[str, Any]]:
        key = self._session_key(captured_at, expiry)
        with self._locked():
            data = self._read_unlocked()
            raw = data["sessions"].get(key, [])
            return list(raw) if isinstance(raw, list) else []

    def append(self, snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        captured_at = datetime.fromisoformat(str(snapshot["captured_at"]))
        expiry = str(snapshot["expiry"])
        key = self._session_key(captured_at, expiry)
        with self._locked():
            data = self._read_unlocked()
            # Active file stays small: keep only the current trading date.
            date_prefix = f"{captured_at.date().isoformat()}|"
            data["sessions"] = {
                name: value
                for name, value in data["sessions"].items()
                if name.startswith(date_prefix)
            }
            history = data["sessions"].setdefault(key, [])
            appended = True
            if history:
                latest = history[-1]
                latest_at = datetime.fromisoformat(str(latest["captured_at"]))
                age = max(0.0, (captured_at - latest_at).total_seconds())
                if (
                    latest.get("fingerprint") == snapshot.get("fingerprint")
                    and age < CONFIG.option_state_dedupe_seconds
                ):
                    appended = False
            if appended:
                history.append(snapshot)
                del history[: -CONFIG.option_state_max_snapshots]
                self._write_unlocked(data)
            return list(history), appended

    def clear(self) -> None:
        with self._locked():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
