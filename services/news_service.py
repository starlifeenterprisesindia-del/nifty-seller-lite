from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

from config import CONFIG
from models import NewsContext, NewsHeadline


class MarketNewsService:
    """Public-RSS market-news reader with strict publication-time freshness.

    Fetch time is never treated as article freshness. Headlines older than the configured
    stale threshold get zero strategy weight. Headlines in the OLD band remain visible
    as low-weight context only. Explicit old dates inside a headline (for example a
    republished "June 8" story in late July) are rejected even if RSS pubDate is recent.
    """

    QUERIES: tuple[str, ...] = (
        "Nifty OR Sensex Indian stock market RBI",
        "India rupee crude oil inflation RBI markets",
        "Federal Reserve rates global markets India stocks",
        "HDFC Bank ICICI Bank Reliance Bharti Airtel L&T SBI Axis Bank",
    )

    HIGH_IMPACT = (
        "rbi", "repo rate", "rate cut", "rate hike", "federal reserve", "fed rate",
        "powell", "war", "missile", "attack", "ceasefire", "tariff", "sanction",
        "budget", "inflation", "gdp", "crude oil", "brent", "rupee", "us jobs", "cpi",
    )
    MEDIUM_IMPACT = (
        "nifty", "sensex", "fii", "dii", "bank nifty", "yield", "dollar", "earnings",
        "results", "guidance",
    )
    BULLISH_WORDS = (
        "rally", "rises", "gains", "surges", "rate cut", "eases", "cooling inflation",
        "record high", "inflows", "ceasefire", "stimulus", "beats estimates",
    )
    BEARISH_WORDS = (
        "falls", "slumps", "drops", "tumbles", "selloff", "rate hike", "war", "attack",
        "tariff", "sanction", "inflation rises", "crude surges", "rupee hits record low",
        "misses estimates",
    )
    MONTHS = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
        "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    _TITLE_DATE_RE = re.compile(
        r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\s+(\d{1,2})(?:,?\s+(20\d{2}))?\b",
        re.IGNORECASE,
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
        return "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-IN&gl=IN&ceid=IN:en"

    @classmethod
    def _explicit_title_date(cls, title: str, now: datetime) -> datetime | None:
        match = cls._TITLE_DATE_RE.search(title or "")
        if not match:
            return None
        month = cls.MONTHS.get(match.group(1).lower())
        day = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else now.year
        try:
            candidate = datetime(year, month, day, tzinfo=now.tzinfo)
        except (TypeError, ValueError):
            return None
        # At year turn, "Dec 31" on Jan 1 normally means the previous year.
        if match.group(3) is None and candidate.date() > (now + timedelta(days=7)).date():
            try:
                candidate = candidate.replace(year=year - 1)
            except ValueError:
                return None
        return candidate

    @classmethod
    def _title_date_is_stale(cls, title: str, now: datetime) -> bool:
        explicit = cls._explicit_title_date(title, now)
        if explicit is None:
            return False
        return (now.date() - explicit.date()).days > CONFIG.news_title_date_max_days

    @staticmethod
    def _freshness_status(newest_age_minutes: float | None) -> str:
        if newest_age_minutes is None:
            return "UNAVAILABLE"
        if newest_age_minutes <= CONFIG.news_recent_minutes:
            return "READY"
        if newest_age_minutes <= CONFIG.news_stale_minutes:
            return "OLD"
        if newest_age_minutes <= CONFIG.news_context_minutes:
            return "CONTEXT ONLY"
        return "STALE"

    @staticmethod
    def _downgrade_risk_for_old(risk: str) -> str:
        return {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW", "NONE": "NONE"}.get(risk, "LOW")

    @staticmethod
    def _decision_rows(
        rows: list[dict[str, Any]], now: datetime, status: str
    ) -> list[dict[str, Any]]:
        """Return only headlines allowed to influence the current decision.

        Display context may remain visible for the full context window, but a fresh
        headline must not make unrelated 19-23 hour stories fresh again.
        """
        if status not in {"READY", "OLD"}:
            return []
        maximum_age = (
            CONFIG.news_recent_minutes
            if status == "READY"
            else CONFIG.news_stale_minutes
        )
        eligible: list[dict[str, Any]] = []
        for row in rows:
            try:
                published = datetime.fromisoformat(str(row.get("published_at")))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=now.tzinfo)
                age = (now - published.astimezone(now.tzinfo)).total_seconds() / 60.0
            except Exception:
                continue
            if 0 <= age <= maximum_age:
                eligible.append(row)
        return eligible

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
            title = str(raw.get("title") or "").strip()
            if not title or self._title_date_is_stale(title, now):
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
                if age_minutes > CONFIG.news_context_minutes:
                    continue
            headlines.append(
                NewsHeadline(
                    title=title,
                    source=str(raw.get("source") or "Unknown").strip(),
                    published_at=published,
                    age_minutes=round(age_minutes, 1) if age_minutes is not None else None,
                    impact=str(raw.get("impact") or "LOW").upper(),
                    bias=str(raw.get("bias") or "NEUTRAL").upper(),
                    link=str(raw.get("link") or ""),
                )
            )

        newest_age = min((item.age_minutes for item in headlines if item.age_minutes is not None), default=None)
        status = self._freshness_status(newest_age)
        # Re-summarize from the surviving, currently fresh-enough items so a cached READY
        # payload cannot stay READY after time has moved on.
        rows = [
            {
                "title": item.title,
                "source": item.source,
                "link": item.link,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "impact": item.impact,
                "bias": item.bias,
            }
            for item in headlines
        ]
        decision_rows = self._decision_rows(rows, now, status)
        bias, risk, summary = self._summarize(decision_rows)
        if status == "OLD":
            risk = self._downgrade_risk_for_old(risk)
            summary = "OLD / low-weight context. " + summary
        elif status == "CONTEXT ONLY":
            summary = "CONTEXT ONLY / decision weight zero. " + summary
        elif status in {"STALE", "UNAVAILABLE"}:
            bias, risk = "NEUTRAL", "NONE"
            summary = "Fresh market-moving news available nahi hai; news decision weight zero hai."
        return NewsContext(
            as_of=now,
            headlines=tuple(headlines[: CONFIG.news_max_headlines]),
            bias=bias,
            risk_level=risk,
            summary=summary,
            newest_age_minutes=newest_age,
            status=status,
            source=str(payload.get("source") or "Google News RSS"),
        )

    @staticmethod
    def _parse_feed(xml_text: str, now: datetime) -> list[dict[str, Any]]:
        root = ElementTree.fromstring(xml_text)
        rows: list[dict[str, Any]] = []
        now_utc = MarketNewsService._now_utc(now)
        max_age_seconds = CONFIG.news_context_minutes * 60
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title or MarketNewsService._title_date_is_stale(title, now):
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
            # Unknown publication time cannot be treated as fresh decision evidence.
            if published is None:
                continue
            age_seconds = (now_utc - published.astimezone(timezone.utc)).total_seconds()
            if age_seconds < -300 or age_seconds > max_age_seconds:
                continue
            rows.append(
                {
                    "title": title,
                    "source": source or "Unknown",
                    "link": link,
                    "published_at": published.isoformat(),
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
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NiftySellerLite/2.12; market-news-context)"}
        for query in self.QUERIES:
            try:
                response = requests.get(
                    self._rss_url(query), headers=headers, timeout=CONFIG.news_request_timeout_seconds
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

        newest_age = None
        if clean:
            ages = []
            for row in clean:
                try:
                    published = datetime.fromisoformat(str(row["published_at"]))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=now.tzinfo)
                    ages.append(max(0.0, (now - published.astimezone(now.tzinfo)).total_seconds() / 60.0))
                except Exception:
                    continue
            newest_age = min(ages, default=None)
        status = self._freshness_status(newest_age)
        decision_rows = self._decision_rows(clean, now, status)
        bias, risk, summary = self._summarize(decision_rows)
        if not clean:
            # If every live request failed, preserve the last same-day context instead
            # of replacing it with an empty payload. Freshness still controls weight.
            if failures == len(self.QUERIES) and self.cache_path.exists():
                try:
                    previous_payload = json.loads(
                        self.cache_path.read_text(encoding="utf-8")
                    )
                    fallback = self._context_from_payload(previous_payload, now)
                    if fallback.headlines:
                        return NewsContext(
                            as_of=fallback.as_of,
                            headlines=fallback.headlines,
                            bias=fallback.bias,
                            risk_level=fallback.risk_level,
                            summary="RSS unavailable; last context shown. " + fallback.summary,
                            newest_age_minutes=fallback.newest_age_minutes,
                            status=fallback.status,
                            source=fallback.source + " (last context)",
                        )
                except Exception:
                    pass
            status = "UNAVAILABLE" if failures == len(self.QUERIES) else "NO FRESH NEWS"
            bias, risk = "NEUTRAL", "NONE"
            summary = "Fresh market-moving news available nahi hai; news decision weight zero hai."
        elif status == "OLD":
            risk = self._downgrade_risk_for_old(risk)
            summary = "OLD / low-weight context. " + summary
        elif status == "CONTEXT ONLY":
            summary = "CONTEXT ONLY / decision weight zero. " + summary

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
