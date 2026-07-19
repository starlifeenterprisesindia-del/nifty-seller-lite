from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from config import CONFIG
from models import DisciplineState

try:  # Linux/Streamlit runtime.
    import fcntl
except ImportError:  # pragma: no cover - Windows local fallback.
    fcntl = None


class DisciplineStore:
    """Small same-day journal for signal persistence and one-trade discipline.

    The store contains no credentials, broker orders or account data. It records only
    final-action labels, timestamps and the user's manual one-trade/day status.
    """

    SCHEMA_VERSION = 1
    ALLOWED_OUTCOMES = {"", "OPEN", "TARGET / MANUAL EXIT", "SL HIT"}

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or CONFIG.discipline_state_path)
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
    def _day_key(value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return date.fromisoformat(str(value)).isoformat()

    def _empty_day(self, session_date: str) -> dict[str, Any]:
        return {
            "session_date": session_date,
            "trades_taken": 0,
            "day_locked": False,
            "last_outcome": "",
            "last_action": "",
            "signal_history": [],
            "updated_at": "",
        }

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": self.SCHEMA_VERSION, "days": {}}

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
            or not isinstance(data.get("days"), dict)
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
    def _to_state(raw: dict[str, Any], status: str = "READY") -> DisciplineState:
        history = raw.get("signal_history", [])
        clean_history = tuple(item for item in history if isinstance(item, dict))
        return DisciplineState(
            session_date=str(raw.get("session_date") or ""),
            trades_taken=max(0, int(raw.get("trades_taken") or 0)),
            day_locked=bool(raw.get("day_locked")),
            last_outcome=str(raw.get("last_outcome") or ""),
            last_action=str(raw.get("last_action") or ""),
            signal_history=clean_history,
            status=status,
        )

    def load(self, session_date: date | datetime | str) -> DisciplineState:
        key = self._day_key(session_date)
        with self._locked():
            data = self._read_unlocked()
            raw = data["days"].get(key)
            if not isinstance(raw, dict):
                raw = self._empty_day(key)
            return self._to_state(raw)

    def append_signal(
        self,
        *,
        captured_at: datetime,
        action: str,
        execution_status: str,
    ) -> tuple[DisciplineState, bool]:
        key = self._day_key(captured_at)
        sample = {
            "captured_at": captured_at.isoformat(),
            "action": str(action or "WAIT").upper(),
            "execution_status": str(execution_status or "").upper(),
        }
        with self._locked():
            data = self._read_unlocked()
            # Active file stays tiny: retain only the current trading date.
            raw = data["days"].get(key)
            if not isinstance(raw, dict):
                raw = self._empty_day(key)
            history = raw.setdefault("signal_history", [])
            appended = True
            if history:
                latest = history[-1]
                try:
                    latest_at = datetime.fromisoformat(str(latest["captured_at"]))
                    age = max(0.0, (captured_at - latest_at).total_seconds())
                except Exception:
                    age = CONFIG.discipline_signal_dedupe_seconds + 1
                if (
                    str(latest.get("action")) == sample["action"]
                    and str(latest.get("execution_status"))
                    == sample["execution_status"]
                    and age < CONFIG.discipline_signal_dedupe_seconds
                ):
                    appended = False
            if appended:
                history.append(sample)
                del history[: -CONFIG.discipline_state_max_signals]
                raw["last_action"] = sample["action"]
                raw["updated_at"] = captured_at.isoformat()
                data["days"] = {key: raw}
                self._write_unlocked(data)
            return self._to_state(raw), appended

    def mark_trade(
        self, *, session_date: date | datetime | str, action: str
    ) -> DisciplineState:
        key = self._day_key(session_date)
        with self._locked():
            data = self._read_unlocked()
            raw = data["days"].get(key)
            if not isinstance(raw, dict):
                raw = self._empty_day(key)
            if bool(raw.get("day_locked")) or int(raw.get("trades_taken") or 0) >= 1:
                raise ValueError("One trade is already used; the day is locked")
            clean_action = str(action or "WAIT").upper()
            if clean_action == "WAIT":
                raise ValueError("WAIT cannot be marked as a trade")
            raw["trades_taken"] = 1
            raw["day_locked"] = True
            raw["last_outcome"] = "OPEN"
            raw["last_action"] = clean_action
            raw["updated_at"] = datetime.now().astimezone().isoformat()
            data["days"] = {key: raw}
            self._write_unlocked(data)
            return self._to_state(raw)

    def mark_outcome(
        self,
        *,
        session_date: date | datetime | str,
        outcome: str,
    ) -> DisciplineState:
        clean_outcome = str(outcome or "").strip().upper()
        if clean_outcome not in self.ALLOWED_OUTCOMES - {"", "OPEN"}:
            raise ValueError(f"Unsupported outcome: {outcome}")
        key = self._day_key(session_date)
        with self._locked():
            data = self._read_unlocked()
            raw = data["days"].get(key)
            if not isinstance(raw, dict) or int(raw.get("trades_taken") or 0) < 1:
                raise ValueError("No trade is marked for this session")
            raw["day_locked"] = True
            raw["last_outcome"] = clean_outcome
            raw["updated_at"] = datetime.now().astimezone().isoformat()
            data["days"] = {key: raw}
            self._write_unlocked(data)
            return self._to_state(raw)

    def clear_day(self, session_date: date | datetime | str) -> None:
        key = self._day_key(session_date)
        with self._locked():
            data = self._read_unlocked()
            data["days"].pop(key, None)
            if data["days"]:
                self._write_unlocked(data)
            else:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
