from types import SimpleNamespace as NS
from ui.components import compact_evidence_note


def test_recent_breadth_is_count_not_daily_direction_or_score():
    snap = NS(heavyweights=NS(rows=tuple(NS(change_15m_pct=v, direction="DOWN")
              for v in [.1, .2, .04, -.1, -.2, 0, .03, -.03, None])))
    note = compact_evidence_note(snap, {"Module": "NIFTY Top-9", "Bullish %": 11})
    assert note == "15m: 3 Up · 2 Down · 3 Flat · 1 pending"


def test_missing_history_not_nine_flat():
    snap = NS(heavyweights=NS(rows=(NS(change_15m_pct=None), NS(change_15m_pct=float('nan')))))
    assert compact_evidence_note(snap, {"Module": "NIFTY Top-9"}) == "15m: warming / data missing"


def test_other_notes_are_optional_and_short():
    assert compact_evidence_note(None, {"Module": "Price Action"}) == "—"
    assert compact_evidence_note(None, {"Module": "Special Candle", "Result": "FORMING · extra"}) == "FORMING"
