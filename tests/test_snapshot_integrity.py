from types import SimpleNamespace
from analysis.snapshot_integrity import build_snapshot_integrity


def _feed(state="LIVE", age=1.0, ok=True):
    return SimpleNamespace(use_state=state, age_seconds=age, ok=ok)


def test_snapshot_integrity_good_live_snapshot():
    snapshot = SimpleNamespace(
        market_session=SimpleNamespace(is_live=True),
        feed_status={
            "quotes": _feed(age=2),
            "candles": _feed(age=8),
            "option_chain": _feed(age=None),
            "future_volume": _feed(age=7),
            "heavyweights": _feed(age=None),
            "vix": _feed(age=3),
        },
    )
    report = build_snapshot_integrity(snapshot)
    assert report["state"] == "GOOD"
    assert report["core_live"] == 3
    assert report["effect_on_one_brain"].startswith("NONE")


def test_snapshot_integrity_limited_when_core_feed_not_live():
    snapshot = SimpleNamespace(
        market_session=SimpleNamespace(is_live=True),
        feed_status={
            "quotes": _feed(),
            "candles": _feed(),
            "option_chain": _feed(state="UNAVAILABLE", age=None, ok=False),
        },
    )
    report = build_snapshot_integrity(snapshot)
    assert report["state"] == "LIMITED"
    assert report["core_live"] == 2
