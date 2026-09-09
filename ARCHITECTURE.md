# Architecture - V2.48 Simple One-Brain

## One authoritative snapshot

`services/snapshot_service.py` builds one immutable `MarketSnapshot`. Screen, journal and PDFs consume the same snapshot. Market-data services fetch data; analysis modules never fetch broker data themselves.

## Simple operational decision path

The rich legacy evidence engine remains available for diagnostics and protected-plan construction, but the **operational entry authority** is `analysis/simple_brain.py`.

The live path is deliberately short:

**Regime -> Direction -> Entry -> Risk -> Action**

Only four blocks are used by the Simple One-Brain:

1. **Trend / Regime (40%)** — completed 15m + 3m price action and the existing core EMA/MACD/RSI evidence. A confirmed 15m breakout/breakdown is directional regime evidence and cannot remain an 82% RANGE veto.
2. **Options Flow (25%)** — bounded 1m/3m/5m blended OI/premium/volume evidence. A fresh 1m+3m reversal compresses a stale opposite 5m extreme into TRANSITION instead of producing false 90%+ conviction.
3. **Participation (20%)** — NIFTY futures volume + Top-9. Big Player is confirmation inside this block, not a duplicate fifth vote.
4. **Barrier / Entry (15%)** — nearest support/resistance is classified as HOLDING/NEAR, UNDER ATTACK, BROKEN or OPEN ROOM. A broken barrier is no longer an automatic permanent WAIT.

Scores are normalized over available core evidence. Missing optional evidence does not consume denominator weight and cannot make the entry threshold mathematically unreachable.

## Advisory/risk-only modules

`analysis/future_brain.py` remains a next-5/15-minute advisory. It can warn about bounce/reversal risk but ordinary MIXED output does not veto a valid current setup. RSI extremes are chase-risk modifiers, not automatic opposite-direction votes.

VIX, verified fresh news/events and data integrity are risk context. FII/DII is background context. Greeks/IV/theta/delta are used for protected strike/hedge quality. W/M and special candles remain supporting detail and do not create independent hard direction votes.

## Execution safety

`analysis/execution_guard.py` keeps genuine hard safety checks: live session/data, price progression, protected plan availability, defined-risk budget, one-trade lock and entry window. In Simple One-Brain mode it does not repeat the old 75-fit, 15m/3m, barrier, persistence and Future-Brain gates as separate vetoes.

The app remains read-only and never places, modifies or exits broker orders.

## Fast lane

The 5-second monitor stays lightweight. It does not decide trades. A confirmed major move can request a priority full snapshot (with cooldown), reducing the delay between a fast market move and a fresh Simple One-Brain decision.

## Journal and learning

`services/shadow_journal.py` has two lanes:

- **Decision Journal** records meaningful WAIT/READY/ENTRY states and backfills observed +5m/+15m/+30m spot outcomes.
- **Paper Trade Journal** records only gate-passed protected simulated trades.

This lets the app distinguish a protective WAIT from a missed move instead of learning only from executed paper trades. Railway day-memory also stores Simple One-Brain regime/direction/entry fields. Historical/Future matching is advisory and cannot hard-block entry.

## Presentation

The main UI presents one operational answer: **MARKET, REGIME, ENTRY, ACTION, TRIGGER, RISK/NEXT LEVEL**. Future Brain and advanced evidence remain expandable diagnostics so they cannot create visible decision conflict.
