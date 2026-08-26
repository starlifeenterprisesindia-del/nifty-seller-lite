from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.news_service import MarketNewsService


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=IST)


def test_news_parser_keeps_fresh_and_same_day_context():
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
    assert len(rows) == 2
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


def test_republished_old_date_in_title_is_rejected_even_with_recent_pubdate():
    published = (NOW - timedelta(minutes=10)).strftime("%a, %d %b %Y %H:%M:%S %z")
    xml = f"""<?xml version='1.0' encoding='UTF-8'?>
    <rss><channel><item>
      <title>Stock Market Outlook Today, June 8: Sensex and Nifty - Example News</title>
      <link>https://example.com/old</link>
      <pubDate>{published}</pubDate>
      <source>Example News</source>
    </item></channel></rss>"""
    assert MarketNewsService._parse_feed(xml, NOW) == []


def test_cached_news_becomes_old_and_low_weight_after_90_minutes(tmp_path):
    service = MarketNewsService(tmp_path / "news.json")
    published = NOW - timedelta(minutes=120)
    payload = {
        "fetched_at": NOW.isoformat(),
        "headlines": [{
            "title": "RBI rate cut lifts Nifty as banks rally - Example News",
            "source": "Example News",
            "published_at": published.isoformat(),
            "impact": "HIGH",
            "bias": "BULLISH",
            "link": "",
        }],
        "source": "test",
    }
    context = service._context_from_payload(payload, NOW)
    assert context.status == "OLD"
    assert context.risk_level == "MEDIUM"
    assert "low-weight" in context.summary


def test_news_older_than_180_minutes_is_context_only_with_zero_live_weight(tmp_path):
    service = MarketNewsService(tmp_path / "news.json")
    published = NOW - timedelta(minutes=181)
    payload = {
        "fetched_at": NOW.isoformat(),
        "headlines": [{
            "title": "Nifty falls on global risk - Example News",
            "source": "Example News",
            "published_at": published.isoformat(),
            "impact": "HIGH",
            "bias": "BEARISH",
            "link": "",
        }],
        "source": "test",
    }
    context = service._context_from_payload(payload, NOW)
    assert context.status == "CONTEXT ONLY"
    assert context.risk_level == "NONE"
    assert context.headlines
    assert "decision weight zero" in context.summary


def test_fresh_headline_does_not_reactivate_old_high_risk_story(tmp_path):
    service = MarketNewsService(tmp_path / "news.json")
    payload = {
        "fetched_at": NOW.isoformat(),
        "headlines": [
            {
                "title": "Nifty trades flat - Example News",
                "source": "Example News",
                "published_at": (NOW - timedelta(minutes=20)).isoformat(),
                "impact": "MEDIUM",
                "bias": "NEUTRAL",
                "link": "",
            },
            {
                "title": "Old attack headline - Example News",
                "source": "Example News",
                "published_at": (NOW - timedelta(hours=20)).isoformat(),
                "impact": "HIGH",
                "bias": "BEARISH",
                "link": "",
            },
        ],
        "source": "test",
    }
    context = service._context_from_payload(payload, NOW)
    assert context.status == "READY"
    assert context.risk_level != "HIGH"
    assert len(context.headlines) == 2  # old item remains visible as context


def test_news_older_than_context_window_is_removed(tmp_path):
    service = MarketNewsService(tmp_path / "news.json")
    published = NOW - timedelta(minutes=1441)
    payload = {
        "fetched_at": NOW.isoformat(),
        "headlines": [{
            "title": "Nifty falls on global risk - Example News",
            "source": "Example News",
            "published_at": published.isoformat(),
            "impact": "HIGH",
            "bias": "BEARISH",
            "link": "",
        }],
        "source": "test",
    }
    context = service._context_from_payload(payload, NOW)
    assert context.status == "UNAVAILABLE"
    assert context.risk_level == "NONE"
    assert not context.headlines
