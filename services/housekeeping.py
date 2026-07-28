from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from config import CONFIG

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


_PROTECTED_FILES = {
    Path(CONFIG.market_context_path).name,
    Path(CONFIG.market_context_mirror_path).name,
    Path(CONFIG.discipline_state_path).name,
}


@contextmanager
def _locked_path(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _parse_dt(value: Any, fallback_tz) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _prune_option_state(now: datetime, cutoff: datetime) -> int:
    path = Path(CONFIG.option_state_path)
    if not path.exists():
        return 0
    with _locked_path(path):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        sessions = payload.get("sessions")
        if not isinstance(sessions, dict):
            return 0
        removed = 0
        clean_sessions: dict[str, list[dict[str, Any]]] = {}
        for key, rows in sessions.items():
            if not isinstance(rows, list):
                continue
            clean_rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                captured = _parse_dt(row.get("captured_at"), now.tzinfo)
                if captured is None or captured < cutoff:
                    removed += 1
                    continue
                clean_rows.append(row)
            if clean_rows:
                clean_sessions[str(key)] = clean_rows[-CONFIG.option_state_max_snapshots :]
        if removed or clean_sessions != sessions:
            payload["sessions"] = clean_sessions
            _atomic_json_write(path, payload)
        return removed


def _expire_news_cache(now: datetime, cutoff: datetime) -> int:
    path = Path(CONFIG.news_cache_path)
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = _parse_dt(payload.get("fetched_at"), now.tzinfo)
    except (OSError, json.JSONDecodeError):
        fetched = None
    # News service has a much shorter live TTL; this 24h guard only prevents abandoned
    # cache files from surviving indefinitely when the app is not run for days.
    if fetched is None or fetched < cutoff:
        try:
            path.unlink()
            return 1
        except FileNotFoundError:
            return 0
    return 0


def _delete_old_safe_temp_files(now: datetime, cutoff: datetime) -> int:
    data_dir = Path("data")
    if not data_dir.exists():
        return 0
    removed = 0
    safe_patterns = ("*.tmp", "*.cache", "snapshot_*.json")
    for pattern in safe_patterns:
        for path in data_dir.glob(pattern):
            if path.name in _PROTECTED_FILES or not path.is_file():
                continue
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
            except OSError:
                continue
            if modified < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    # Future raw snapshot directory support. Only this explicitly named temp directory
    # is cleaned; FII/DII, trade journal and learning files are never glob-deleted.
    snapshot_dir = data_dir / "snapshots"
    if snapshot_dir.exists():
        for path in snapshot_dir.glob("*.json"):
            if not path.is_file():
                continue
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
            except OSError:
                continue
            if modified < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def run_housekeeping(now: datetime) -> dict[str, int]:
    """Quietly enforce 24-hour retention for raw/temporary market state.

    Preserved by design: FII/DII 15-session journal, discipline/manual trade state,
    instrument master and any compact learning summaries. The function is safe to call
    on every app rerun; it only writes when something actually needs pruning.
    """

    hours = max(1, int(CONFIG.temporary_data_retention_hours))
    cutoff = now - timedelta(hours=hours)
    return {
        "option_snapshots_removed": _prune_option_state(now, cutoff),
        "news_cache_removed": _expire_news_cache(now, cutoff),
        "temp_files_removed": _delete_old_safe_temp_files(now, cutoff),
    }
