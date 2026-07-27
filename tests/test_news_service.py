from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.news_service import MarketNewsService


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=IST)


def test_news_parser_keeps_recent_and_classifies_impact_and_bias():
    published = (NOW - timedelta(minutes=20)).strftime("%a, %d %b %Y %H:%M:%S %z")
    old = (NOW - timedelta(hours=20)).strftime("%a, %d %b %Y %H:%M:%S %z")
    xml = f"""<?xml version='1.0' encoding='UTF-8'?>
    <rss><channel>
      <item>
        <title>RBI rate cut lifts Nifty as banks rally - Example News</title>
        <link>https://example.com/a</link>
        <pubDate>{published}</pubDate>
        <source>Example News</source>
      </item>
      <item>
        <title>Old market story - Example News</title>
        <link>https://example.com/b</link>
        <pubDate>{old}</pubDate>
        <source>Example News</source>
      </item>
    </channel></rss>"""
    rows = MarketNewsService._parse_feed(xml, NOW)
    assert len(rows) == 1
    assert rows[0]["impact"] == "HIGH"
    assert rows[0]["bias"] == "BULLISH"


def test_news_summary_marks_high_impact_risk():
    rows = [
        {
            "title": "Crude oil surges after attack",
            "source": "A",
            "link": "",
            "published_at": NOW.isoformat(),
            "impact": "HIGH",
            "bias": "BEARISH",
        },
        {
            "title": "Nifty falls on global risk",
            "source": "B",
            "link": "",
            "published_at": NOW.isoformat(),
            "impact": "MEDIUM",
            "bias": "BEARISH",
        },
    ]
    bias, risk, summary = MarketNewsService._summarize(rows)
    assert bias == "BEARISH"
    assert risk == "HIGH"
    assert "risk HIGH" in summary
