# Architecture — V2.50 Lean AI Tracker

## One authoritative snapshot

`services/snapshot_service.py` builds one `MarketSnapshot`. Screen, journal, PDF and decision logic consume that same snapshot. Analysis modules do not fetch broker data independently.

## Canonical operational brain

`analysis/simple_brain.py` is the operational decision authority after evidence is built:

1. **Trend / Regime — 40%**
2. **Options Flow — 25%**
3. **Participation — 20%**
4. **Barrier / Entry — 15%**

Weights normalize over available evidence. **Missing = no vote.** Big Player is confirmation inside Participation, not a fifth directional vote. FII/DII, VIX, news, Greeks, W/M and special candles keep their context/risk/quality roles and do not create duplicate final decisions.

## Future Brain

`analysis/future_brain.py` remains advisory for the next 5/15 minutes. Longer 30m/1h rows are context strength, not a second calibrated probability engine. The old duplicate 5–15 minute outlook is not rendered on the main screen.

## AI Move Check

`analysis/ai_move_tracker.py` freezes a valid live UP/DOWN thesis from the canonical Simple One-Brain. `ui/ai_move_tracker.py` runs as a 3-minute Streamlit fragment and reads only Railway's already-running `/live` cache. It does **not** fetch Dhan data or recalculate indicators/options/Top-9/Brain.

The tracker records current signed move, MFE, MAE, 5m/15m/30m checkpoints and a locked relevant barrier. Full snapshots may confirm that barrier using their already-computed completed 3m close. A tiny Railway-only `AI TRACKER` event persists each 3-minute observation; it never creates a decision vote or Dhan call.

## Journal

New decision rows are limited to **09:30–15:00 IST**. Existing rows can continue receiving outcome backfills after 15:00 so late signals are not left incomplete.

## Alerts

Strong 3m candle, valid W/M and Big Player activity share one combined Telegram path. Fingerprints are reserved before asynchronous delivery and recent fingerprints persist on Railway volume to prevent duplicate messages across reruns/restarts. Manual CE/PE premium alerts remain separate because they are user-defined price alerts, not market-evidence alerts.

## Options walls

Operational Options Intelligence continues to use the bounded near-ATM window. The full chain is already available during snapshot construction, so presentation also records **Global Max OI CE/PE** without another API call. The two concepts are explicitly labelled and are not double-counted.

## Performance rules

- no extra Dhan call for AI Move Check
- no full Brain rerun from the 3-minute tracker
- instrument master / VIX / nearest-future resolution cached in process
- one combined alert path rather than duplicate Big Player delivery
- runtime files and caches excluded from release

The app remains read-only and never places, modifies or exits broker orders.
