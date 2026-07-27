from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

from config import CONFIG, IST_TIMEZONE
from models import NewsContext, NewsHeadline


class MarketNewsService:
    """Small public-RSS market-news reader with bounded file cache.

    The news layer is informational/risk context only. It does not place trades and it
    does not silently turn old headlines into live evidence. If all public feeds fail,
    the returned state is UNAVAILABLE and the strategy brain gives the news layer zero
    directional weight.
    """

    QUERIES: tuple[str, ...] = (
        "Nifty OR Sensex Indian stock market RBI",
        "India rupee crude oil inflation RBI markets",
        "Federal Reserve rates global markets India stocks",
        "HDFC Bank ICICI Bank Reliance Bharti Airtel L&T SBI Axis Bank",
    )

    HIGH_IMPACT = (
        "rbi",
        "repo rate",
        "rate cut",
        "rate hike",
        "federal reserve",
        "fed rate",
        "powell",
        "war",
        "missile",
        "attack",
        "ceasefire",
        "tariff",
        "sanction",
        "budget",
        "inflation",
        "gdp",
        "crude oil",
        "brent",
        "rupee",
        "us jobs",
        "cpi",
    )
    MEDIUM_IMPACT = (
        "nifty",
        "sensex",
        "fii",
        "dii",
        "bank nifty",
        "yield",
        "dollar",
        "earnings",
        "results",
        "guidance",
    )
    BULLISH_WORDS = (
        "rally",
        "rises",
        "gains",
        "surges",
        "rate cut",
        "eases",
        "cooling inflation",
        "record high",
        "inflows",
        "ceasefire",
        "stimulus",
        "beats estimates",
    )
    BEARISH_WORDS = (
        "falls",
        "slumps",
        "drops",
        "tumbles",
        "selloff",
        "rate hike",
        "war",
        "attack",
        "tariff",
        "sanction",
        "inflation rises",
        "crude surges",
        "rupee hits record low",
        "misses estimates",
    )

    def __init__(self, cache_path: str | Path | None = None):
        self.cache_path = Path(cache_path or CONFIG.news_cache_path)

    @staticmethod
    def _now_utc(now: datetime) -> datetime:
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    @staticmethod
    def _headline_bias(title: str) -> str:
        lower = title.lower()
        bull = sum(token in lower for token in MarketNewsService.BULLISH_WORDS)
        bear = sum(token in lower for token in MarketNewsService.BEARISH_WORDS)
        if bull > bear:
            return "BULLISH"
        if bear > bull:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def _impact(title: str) -> str:
        lower = title.lower()
        if any(token in lower for token in MarketNewsService.HIGH_IMPACT):
            return "HIGH"
        if any(token in lower for token in MarketNewsService.MEDIUM_IMPACT):
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _rss_url(query: str) -> str:
        return (
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + "&hl=en-IN&gl=IN&ceid=IN:en"
        )

    def _write_cache(self, payload: dict[str, Any]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.cache_path)
        except OSError:
            pass

    def _read_cache(self, now: datetime) -> NewsContext | None:
        if not self.cache_path.exists():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(str(payload.get("fetched_at")))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=now.tzinfo)
            age = (now - fetched_at.astimezone(now.tzinfo)).total_seconds()
            if age < 0 or age > CONFIG.news_cache_ttl_seconds:
                return None
            return self._context_from_payload(payload, now)
        except Exception:
            return None

    def _context_from_payload(self, payload: dict[str, Any], now: datetime) -> NewsContext:
        headlines: list[NewsHeadline] = []
        for raw in payload.get("headlines", []):
            if not isinstance(raw, dict):
                continue
            published = None
            try:
                published = datetime.fromisoformat(str(raw.get("published_at")))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=now.tzinfo)
            except Exception:
                published = None
            age_minutes = None
            if published is not None:
                age_minutes = max(0.0, (now - published.astimezone(now.tzinfo)).total_seconds() / 60.0)
            headlines.append(
                NewsHeadline(
                    title=str(raw.get("title") or "").strip(),
                    source=str(raw.get("source") or "Unknown").strip(),
                    published_at=published,
                    age_minutes=round(age_minutes, 1) if age_minutes is not None else None,
                    impact=str(raw.get("impact") or "LOW").upper(),
                    bias=str(raw.get("bias") or "NEUTRAL").upper(),
                    link=str(raw.get("link") or ""),
                )
            )
        return NewsContext(
            as_of=now,
            headlines=tuple(headlines[: CONFIG.news_max_headlines]),
            bias=str(payload.get("bias") or "NEUTRAL").upper(),
            risk_level=str(payload.get("risk_level") or "NONE").upper(),
            summary=str(payload.get("summary") or "No recent market-moving headline found."),
            newest_age_minutes=(
                min(
                    (item.age_minutes for item in headlines if item.age_minutes is not None),
                    default=None,
                )
            ),
            status=str(payload.get("status") or "UNAVAILABLE").upper(),
            source=str(payload.get("source") or "Google News RSS"),
        )

    @staticmethod
    def _parse_feed(xml_text: str, now: datetime) -> list[dict[str, Any]]:
        root = ElementTree.fromstring(xml_text)
        rows: list[dict[str, Any]] = []
        now_utc = MarketNewsService._now_utc(now)
        max_age_seconds = CONFIG.news_max_age_hours * 3600
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            source = (item.findtext("source") or "").strip()
            if not source and " - " in title:
                source = title.rsplit(" - ", 1)[-1].strip()
            link = (item.findtext("link") or "").strip()
            published_raw = (item.findtext("pubDate") or "").strip()
            published = None
            try:
                published = parsedate_to_datetime(published_raw)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                published = published.astimezone(now.tzinfo)
            except Exception:
                published = None
            if published is not None:
                age_seconds = (now_utc - published.astimezone(timezone.utc)).total_seconds()
                if age_seconds < -300 or age_seconds > max_age_seconds:
                    continue
            rows.append(
                {
                    "title": title,
                    "source": source or "Unknown",
                    "link": link,
                    "published_at": published.isoformat() if published is not None else None,
                    "impact": MarketNewsService._impact(title),
                    "bias": MarketNewsService._headline_bias(title),
                }
            )
        return rows

    @staticmethod
    def _summarize(rows: list[dict[str, Any]]) -> tuple[str, str, str]:
        if not rows:
            return "NEUTRAL", "NONE", "No recent market-moving headline found."
        bull = sum(row["bias"] == "BULLISH" for row in rows)
        bear = sum(row["bias"] == "BEARISH" for row in rows)
        high = sum(row["impact"] == "HIGH" for row in rows)
        medium = sum(row["impact"] == "MEDIUM" for row in rows)
        if bull >= bear + 2:
            bias = "BULLISH"
        elif bear >= bull + 2:
            bias = "BEARISH"
        elif bull and bear:
            bias = "MIXED"
        else:
            bias = "NEUTRAL"
        risk = "HIGH" if high >= 1 else "MEDIUM" if medium >= 2 else "LOW"
        lead = rows[0]["title"]
        summary = f"{len(rows)} recent headline(s); risk {risk}; bias {bias}. Latest: {lead}"
        return bias, risk, summary[:420]

    def fetch(self, now: datetime) -> NewsContext:
        cached = self._read_cache(now)
        if cached is not None:
            return cached

        rows: list[dict[str, Any]] = []
        failures = 0
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NiftySellerLite/2.11; market-news-context)"
        }
        for query in self.QUERIES:
            try:
                response = requests.get(
                    self._rss_url(query),
                    headers=headers,
                    timeout=CONFIG.news_request_timeout_seconds,
                )
                response.raise_for_status()
                rows.extend(self._parse_feed(response.text, now))
            except Exception:
                failures += 1

        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = " ".join(str(row.get("title") or "").lower().split())
            if key and key not in deduped:
                deduped[key] = row
        clean = list(deduped.values())
        clean.sort(key=lambda row: str(row.get("published_at") or ""), reverse=True)
        clean = clean[: CONFIG.news_max_headlines]

        bias, risk, summary = self._summarize(clean)
        status = "READY" if clean else "UNAVAILABLE" if failures == len(self.QUERIES) else "NO RECENT NEWS"
        payload = {
            "fetched_at": now.isoformat(),
            "headlines": clean,
            "bias": bias,
            "risk_level": risk,
            "summary": summary,
            "status": status,
            "source": "Google News RSS (multiple market queries)",
        }
        self._write_cache(payload)
        return self._context_from_payload(payload, now)
