from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from config import CONFIG, INSTRUMENT_MASTER_URL
from services.errors import SnapshotBuildError


COLUMN_ALIASES = {
    "security_id": ["SECURITY_ID", "SEM_SMST_SECURITY_ID", "SM_SECURITY_ID"],
    "exchange_id": ["EXCH_ID", "SEM_EXM_EXCH_ID"],
    "segment": ["SEGMENT", "SEM_SEGMENT"],
    "instrument": ["INSTRUMENT", "SEM_INSTRUMENT_NAME"],
    "symbol": ["SYMBOL_NAME", "SM_SYMBOL_NAME", "UNDERLYING_SYMBOL"],
    "display_name": ["DISPLAY_NAME", "SEM_CUSTOM_SYMBOL", "SEM_TRADING_SYMBOL"],
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
    def __init__(self, cache_path: Path | None = None):
        self.cache_path = cache_path or Path("data/instrument_master.csv")

    @staticmethod
    def _first_existing(df: pd.DataFrame, candidates: list[str]) -> str | None:
        normalized = {str(col).strip().upper(): col for col in df.columns}
        for candidate in candidates:
            if candidate in normalized:
                return normalized[candidate]
        return None

    def download(self) -> pd.DataFrame:
        response = requests.get(INSTRUMENT_MASTER_URL, timeout=CONFIG.request_timeout_seconds)
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text), low_memory=False)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.cache_path, index=False)
        return df

    def load(self, *, allow_download: bool = True) -> pd.DataFrame:
        if self.cache_path.exists():
            try:
                cached = pd.read_csv(self.cache_path, low_memory=False)
                if not cached.empty:
                    return cached
            except Exception:
                pass
        if allow_download:
            return self.download()
        raise SnapshotBuildError("Dhan instrument master is unavailable")

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(index=df.index)
        for target, aliases in COLUMN_ALIASES.items():
            source = self._first_existing(df, aliases)
            result[target] = df[source] if source is not None else ""
        result["security_id"] = pd.to_numeric(result["security_id"], errors="coerce")
        for col in ("symbol", "display_name", "underlying_symbol", "instrument", "exchange_id", "segment"):
            result[col] = result[col].fillna("").astype(str).str.upper().str.strip()
        result["expiry"] = pd.to_datetime(result["expiry"], errors="coerce")
        return result.dropna(subset=["security_id"]).copy()

    @staticmethod
    def _exchange_segment(row: pd.Series) -> str:
        exch = str(row.get("exchange_id", "")).upper()
        segment = str(row.get("segment", "")).upper()
        instrument = str(row.get("instrument", "")).upper()
        if instrument == "INDEX" or (exch == "NSE" and segment in {"I", "INDEX"}):
            return "IDX_I"
        if exch == "NSE" and segment in {"E", "EQ", "EQUITY"}:
            return "NSE_EQ"
        if exch == "NSE" and segment in {"D", "FNO", "DERIVATIVES"}:
            return "NSE_FNO"
        if exch == "BSE" and segment in {"E", "EQ", "EQUITY"}:
            return "BSE_EQ"
        if exch == "BSE" and segment in {"D", "FNO", "DERIVATIVES"}:
            return "BSE_FNO"
        return ""

    def resolve_equities(
        self,
        symbols: Iterable[str],
        df: pd.DataFrame | None = None,
    ) -> list[ResolvedInstrument]:
        frame = self.normalize(df if df is not None else self.load())
        results: list[ResolvedInstrument] = []
        for requested in symbols:
            symbol = requested.upper().strip()
            candidates = frame[
                (frame["symbol"] == symbol)
                & frame["instrument"].isin(["EQUITY", "EQ"])
            ].copy()
            if candidates.empty:
                candidates = frame[
                    frame["display_name"].str.fullmatch(symbol, na=False)
                    & frame["instrument"].isin(["EQUITY", "EQ"])
                ].copy()
            if candidates.empty:
                continue
            row = candidates.iloc[0]
            segment = self._exchange_segment(row)
            if not segment:
                continue
            results.append(
                ResolvedInstrument(
                    symbol=symbol,
                    security_id=int(row["security_id"]),
                    exchange_segment=segment,
                    instrument="EQUITY",
                    display_name=str(row["display_name"] or symbol),
                )
            )
        return results

    def resolve_index(
        self,
        names: Iterable[str],
        df: pd.DataFrame | None = None,
    ) -> ResolvedInstrument | None:
        frame = self.normalize(df if df is not None else self.load())
        mask = frame["instrument"].eq("INDEX")
        for raw_name in names:
            term = raw_name.upper().strip()
            candidates = frame[
                mask
                & (
                    frame["symbol"].str.contains(term, regex=False)
                    | frame["display_name"].str.contains(term, regex=False)
                )
            ]
            if not candidates.empty:
                row = candidates.iloc[0]
                return ResolvedInstrument(
                    symbol=term,
                    security_id=int(row["security_id"]),
                    exchange_segment="IDX_I",
                    instrument="INDEX",
                    display_name=str(row["display_name"] or term),
                )
        return None

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
                    frame["display_name"].str.contains(r"(^|\s)NIFTY(\s|$)", regex=True, na=False)
                    & ~frame["display_name"].str.contains("BANKNIFTY|FINNIFTY|MIDCPNIFTY", regex=True, na=False)
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
