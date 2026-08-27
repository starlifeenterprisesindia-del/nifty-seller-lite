"""One shared Big Player confirmation policy; never selects a trade."""
from config import CONFIG


def confirmed_activity(activity):
    return bool(
        activity is not None
        and activity.status == "READY"
        and activity.direction in {"BUYING", "SELLING"}
        and activity.score >= CONFIG.big_player_min_score
        and activity.confirmation_count >= CONFIG.big_player_min_confirmations
        and getattr(activity, "price_response", "UNCONFIRMED") == "FOLLOW-THROUGH"
    )


def activity_gate(setup, activity):
    """Return block + explanation. Weak/missing composite evidence is no vote.

    Feed freshness, core/options confidence and risk remain separate mandatory
    guards. Stalled pressure is not confirmed buying/selling follow-through.
    """
    setup = setup.replace(" WITH HEDGE", "")
    expected = {"CE BUY": "BUYING", "PE SELL": "BUYING",
                "PE BUY": "SELLING", "CE SELL": "SELLING"}.get(setup)
    if setup not in {"CE BUY", "PE BUY", "CE SELL", "PE SELL", "IRON CONDOR"}:
        return False, ""
    if not confirmed_activity(activity):
        return False, "Big Player unconfirmed/stalled — context only; other entry checks still required"
    if setup == "IRON CONDOR":
        return True, "Confirmed Big Player follow-through conflicts with Iron Condor range"
    if activity.direction != expected:
        return True, "Confirmed Big Player activity opposes the strategy (price follow-through)"
    return False, "Big Player aligned with price follow-through"
