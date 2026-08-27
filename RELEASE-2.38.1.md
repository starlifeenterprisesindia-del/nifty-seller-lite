# 2.38.1 — shared Big Player entry policy

Complete cumulative package including 2.37.0 and 2.38.0. Use this instead of
the earlier ZIP. Version: 2.38.1_SHARED_ACTIVITY_GATE.

## Fixed

Main AI and final execution guard now call the SAME Big Player gate:

- READY activity, score >=60, >=2 observations and actual price FOLLOW-THROUGH
  are required to classify confirmed activity.
- Confirmed opposite activity blocks directional entry.
- Confirmed directional activity blocks Iron Condor.
- Weak, missing, mixed, unconfirmed or stalled composite Big Player evidence is
  context only, not an additional mandatory 60-point vote.
- Strong-signal fast confirmation also requires price follow-through.
- This does not grant entry permission by itself. Core/options confidence,
  flow maturity, final AI action, live quote/candles/options, protected plan,
  risk budget, entry window, trade lock and confirmation checks still apply.

Weights, independent entry calculator, paper threshold 45, position protection,
existing Telegram price alerts and recorded journal data are unchanged.
No capital/risk limit has been increased.

## Remaining operational limits, not claimed as completed

- No new external live-news subscription/feed was configured. Existing fresh
  news handling remains; old/missing news receives no direction vote.
- New candle/W-M alerts are app-computed and require refreshes; no independent
  background scanner was added. Telegram gateway dedup resets on restart.
- Broker/live-market matching and forward performance must be checked after
  deployment. Offline tests do not establish trading accuracy or profitability.
- Auto paper journal still requires its risk/data/time checks in addition to 45.

Upload the contents of nifty-seller-lite-main to the existing repository root.
Update both app and gateway deployments; preserve current secrets and data.
No deployment, live Telegram message or broker trade was performed here.
Read RELEASE-2.38.0.md for the full cumulative feature list.
