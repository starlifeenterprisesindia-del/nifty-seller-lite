from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
import threading

import pandas as pd
import requests

from config import CONFIG, INSTRUMENT_MASTER_URL
from services.errors import SnapshotBuildError


COLUMN_ALIASES = {
    "security_id": ["SECURITY_ID", "SEM_SMST_SECURITY_ID", "SM_SECURITY_ID"],
    "exchange_id": ["EXCH_ID", "SEM_EXM_EXCH_ID"],
    "segment": ["SEGMENT", "SEM_SEGMENT"],
    "instrument": ["INSTRUMENT", "SEM_INSTRUMENT_NAME"],
    "symbol": ["SYMBOL_NAME", "SM_SYMBOL_NAME"],
    "display_name": ["DISPLAY_NAME", "SEM_CUSTOM_SYMBOL"],
    "expiry": ["SM_EXPIRY_DATE", "SEM_EXPIRY_DATE"],
    "underlying_symbol": ["UNDERLYING_SYMBOL"],
}


@dataclass(frozen=True)
class ResolvedInstrument:
    symbol: str
    security_id: int
    exchange_segment: str
    instrument: str
    display_name: str
    expiry: str | None = None


class InstrumentMaster:
    """Cached Dhan instrument resolver for the dynamic future and index references.

    The CSV is large enough that reparsing and renormalising it on every Streamlit
    rerun can dominate otherwise-light UI work.  Keep a process-wide cache keyed by
    file metadata; Railway/Streamlit workers naturally invalidate it when the cache
    file is refreshed or replaced.
    """

    _memory_lock = threading.RLock()
    _raw_memory: dict[str, tuple[int, int, pd.DataFrame]] = {}
    _normalized_memory: dict[str, tuple[int, int, pd.DataFrame]] = {}

    def __init__(self, cache_path: Path | None = None):
        self.cache_path = cache_path or Path("data/instrument_master.csv")

    def _cache_identity(self) -> tuple[str, int, int] | None:
        try:
            stat = self.cache_path.stat()
        except OSError:
            return None
        return (str(self.cache_path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))

    @classmethod
    def _remember_raw(cls, identity: tuple[str, int, int], frame: pd.DataFrame) -> None:
        path, mtime_ns, size = identity
        with cls._memory_lock:
            cls._raw_memory[path] = (mtime_ns, size, frame)
            # New raw bytes invalidate the normalized view for this path.
            normalized = cls._normalized_memory.get(path)
            if normalized and normalized[:2] != (mtime_ns, size):
                cls._normalized_memory.pop(path, None)

    @classmethod
    def clear_memory_cache(cls) -> None:
        with cls._memory_lock:
            cls._raw_memory.clear()
            cls._normalized_memory.clear()

    @staticmethod
    def _first_existing(df: pd.DataFrame, candidates: list[str]) -> str | None:
        normalized = {str(col).strip().upper(): col for col in df.columns}
        for candidate in candidates:
            if candidate in normalized:
                return normalized[candidate]
        return None

    def download(self) -> pd.DataFrame:
        response = requests.get(
            INSTRUMENT_MASTER_URL, timeout=CONFIG.request_timeout_seconds
        )
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text), low_memory=False)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.cache_path, index=False)
        identity = self._cache_identity()
        if identity is not None:
            self._remember_raw(identity, df)
        return df

    def load(self, *, allow_download: bool = True) -> pd.DataFrame:
        cached: pd.DataFrame | None = None
        cache_is_fresh = False
        identity = self._cache_identity()
        if identity is not None:
            path, mtime_ns, size = identity
            with self._memory_lock:
                memory = self._raw_memory.get(path)
                if memory and memory[:2] == (mtime_ns, size):
                    cached = memory[2]
            if cached is None:
                try:
                    cached = pd.read_csv(self.cache_path, low_memory=False)
                    if cached.empty:
                        cached = None
                    else:
                        self._remember_raw(identity, cached)
                except Exception:
                    cached = None
            try:
                age_seconds = max(
                    0.0, datetime.now().timestamp() - self.cache_path.stat().st_mtime
                )
                cache_is_fresh = (
                    age_seconds <= CONFIG.instrument_master_cache_max_age_hours * 3600
                )
            except OSError:
                cache_is_fresh = False

        if cached is not None and cache_is_fresh:
            return cached
        if allow_download:
            try:
                return self.download()
            except Exception:
                if cached is not None:
                    return cached
                raise
        if cached is not None:
            return cached
        raise SnapshotBuildError("Dhan instrument master is unavailable")

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        identity = self._cache_identity()
        if identity is not None:
            path, mtime_ns, size = identity
            with self._memory_lock:
                raw = self._raw_memory.get(path)
                normalized = self._normalized_memory.get(path)
                if (
                    raw
                    and raw[:2] == (mtime_ns, size)
                    and raw[2] is df
                    and normalized
                    and normalized[:2] == (mtime_ns, size)
                ):
                    return normalized[2]

        result = pd.DataFrame(index=df.index)
        for target, aliases in COLUMN_ALIASES.items():
            source = self._first_existing(df, aliases)
            result[target] = df[source] if source is not None else ""
        result["security_id"] = pd.to_numeric(result["security_id"], errors="coerce")
        text_columns = (
            "symbol",
            "display_name",
            "underlying_symbol",
            "instrument",
            "exchange_id",
            "segment",
        )
        for col in text_columns:
            result[col] = result[col].fillna("").astype(str).str.upper().str.strip()
        result["expiry"] = pd.to_datetime(result["expiry"], errors="coerce")
        result = result.dropna(subset=["security_id"]).copy()

        if identity is not None:
            path, mtime_ns, size = identity
            with self._memory_lock:
                raw = self._raw_memory.get(path)
                if raw and raw[:2] == (mtime_ns, size) and raw[2] is df:
                    self._normalized_memory[path] = (mtime_ns, size, result)
        return result

    def resolve_india_vix(
        self,
        df: pd.DataFrame | None = None,
    ) -> ResolvedInstrument | None:
        frame = self.normalize(df if df is not None else self.load())
        symbol_match = frame["symbol"].str.replace(" ", "", regex=False).eq("INDIAVIX")
        display_match = (
            frame["display_name"]
            .str.replace(" ", "", regex=False)
            .str.contains("INDIAVIX", regex=False, na=False)
        )
        candidates = frame[symbol_match | display_match].copy()
        if candidates.empty:
            return None
        index_like = candidates[
            candidates["instrument"].isin(["INDEX", "INDEXVALUE", "IDX"])
        ]
        row = (index_like if not index_like.empty else candidates).iloc[0]
        return ResolvedInstrument(
            symbol="INDIA VIX",
            security_id=int(row["security_id"]),
            exchange_segment="IDX_I",
            instrument="INDEX",
            display_name=str(row["display_name"] or "INDIA VIX"),
        )

    def resolve_nearest_nifty_future(
        self,
        df: pd.DataFrame | None = None,
        now: datetime | None = None,
    ) -> ResolvedInstrument | None:
        frame = self.normalize(df if df is not None else self.load())
        current = pd.Timestamp(now or datetime.now())
        candidates = frame[
            frame["instrument"].isin(["FUTIDX", "FUTURE", "FUTURES"])
            & (
                frame["underlying_symbol"].str.fullmatch("NIFTY", na=False)
                | frame["symbol"].str.fullmatch("NIFTY", na=False)
                | (
                    frame["display_name"].str.contains(
                        r"(?:^|\s)NIFTY(?:\s|$)",
                        regex=True,
                        na=False,
                    )
                    & ~frame["display_name"].str.contains(
                        "BANKNIFTY|FINNIFTY|MIDCPNIFTY",
                        regex=True,
                        na=False,
                    )
                )
            )
            & frame["expiry"].notna()
            & (frame["expiry"] >= current.normalize())
        ].sort_values("expiry")
        if candidates.empty:
            return None
        row = candidates.iloc[0]
        return ResolvedInstrument(
            symbol="NIFTY_FUT",
            security_id=int(row["security_id"]),
            exchange_segment="NSE_FNO",
            instrument="FUTIDX",
            display_name=str(row["display_name"] or "NIFTY FUTURE"),
            expiry=row["expiry"].date().isoformat(),
        )
