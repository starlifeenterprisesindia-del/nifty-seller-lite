# Architecture — V2.76.0 Integrated Edge Context

## Canonical live path — unchanged

`services/snapshot_service.py` builds one authoritative `MarketSnapshot`. The protected operational One Brain remains:

1. Trend / Regime — 40%
2. Options Flow — 25%
3. Participation — 20%
4. Barrier / Entry — 15%

Available evidence is normalized; missing evidence is NO VOTE. Market Intelligence has zero One Brain weight.

## Barrier correctness and synchronization

Two related barrier representations remain intentionally available, but now share the same completed-close acceptance rule:

1. `analysis.levels.calculate_levels` builds the structural `LevelBundle`. Live spot controls distance/status, while completed 3-minute closes control support/resistance role and R1/S1 promotion. This bundle is consumed by the canonical One Brain Barrier/Entry block, trade-plan level clearance, pattern level context and pre-touch barriers.
2. `analysis.barrier_map.calculate_barrier_map` enriches those structural levels with OI flow, reaction quality, momentum, Top-9, futures volume, market speed and VIX. It is consumed by Market Intelligence, Pressure Integrity, Liquidity Intelligence, Big Player, Smart Entry/Strike Entry, SL/Target planning, Premium Calculator, live maps, PDF/replay/day-memory and related display/validation layers.

A live wick/tick through a barrier therefore remains `TESTING` / `AWAITING 3M CLOSE`; the next barrier is not promoted until the completed 3-minute close clears the full confluence zone. Range break bias compares net vulnerability (`Break Pressure - Strength`) on both sides. No new data request is made.


## Integrated Edge Context — v2.76

`analysis/research_edge_context.py` runs only after the canonical One Brain/common-decision pipeline and after the existing Market Intelligence shadow result is attached. It consumes the already-built `MarketSnapshot` plus the existing compact ATM-IV session summaries. It performs no network/broker request and cannot feed back into the protected decision pipeline.

### VIX Expected-Move Context

The existing Barrier Map remains the single source for expected-move points (`spot × India VIX / 100 / √252` and square-root-of-time remaining-session scaling). The v2.76 context does not create a second volatility formula; it anchors the existing move to the current session open/previous close and reports live envelope utilisation, remaining room and nearest-barrier fit.

### Trend + Mean-Reversion Context

The diagnostic uses completed 15-minute EMA20/EMA50 trend, completed-candle RSI(2), 15-minute ATR stretch, nearest trend-side barrier proximity, 3-minute directional evidence/pattern confirmation and existing Pressure Integrity. It differentiates `PULLBACK COMPLETION WATCH/CONFIRMED` from `GENUINE REVERSAL RISK`. These labels remain advisory and have zero decision weight.

### DTE-matched IV Percentile

`OptionStateStore` already persists one compact real ATM-IV summary per trading date. v2.76 retains a bounded 160-session history and compares current ATM IV only with prior observations in the same calendar-DTE bucket. The in-progress current date is excluded. A minimum of 20 matched sessions is required; otherwise the output is `WARMING UP / NO VOTE`. Advanced Options Intelligence and Strategy + Strike Value use the same DTE-matched population to avoid conflicting IVP definitions.

### Recording and validation

Day Memory, app observations and research-journal market context persist the three labels/metrics so later replay can test incremental value without retroactively changing the live decision. Performance timing records `edge_context_seconds`.

## Market Intelligence — Institutional Opportunity Window

`analysis/institutional_window.py` remains a shadow-only, zero-network sub-engine. Six independent gates remain: Directional Edge, Opposition Weakness, Path Clearance, Participation Capacity Proxy, Trigger Readiness and Pressure Effectiveness. `OPEN` requires 6/6 + live data safety. W/M and strong candles are supportive only.

## Liquidity Magnet / Money Concentration

`analysis/liquidity_intelligence.py` now adds a separate observable concentration layer using the already-fetched option chain:

- UPSIDE proxy: CE strikes at/above spot
- DOWNSIDE proxy: PE strikes at/below spot
- inputs: OI, positive day OI change, traded volume
- outputs: side scores, confidence, strongest visible strike/zone, `UPSIDE / DOWNSIDE / BALANCED`

The layer intentionally does **not** claim exact rupee capital, retail stops, hidden orders or participant intent. Large OI may represent attraction, resistance/support, hedging inventory or other positioning, so Path Clearance and Trigger evidence remain required.

Institutional Window uses Money Magnet only as a bounded path-quality modifier. It does not add a seventh gate or another One Brain vote. A strong opposite magnet blocks the `STRONG` quality upgrade.

## Premium Calculator — Smart Entry Advisor

`analysis/smart_entry_advisor.py` is a display/advisory layer for `Plan new entry` mode. It reuses:

- current executable bid/ask
- independent Strike Entry Planner barrier/retest state
- existing premium scenario engine
- structural target/invalidation context
- Pressure Integrity
- Institutional Window
- Liquidity Magnet

It separates:

1. **Price Quality** — preferred entry zone, acceptable zone, no-chase boundary and risk/reward context.
2. **Move Urgency** — whether waiting for the ideal premium risks missing a move already accelerating.

Status flow is intentionally simple: `REFERENCE ONLY / WAIT / ARMED / ENTRY CONDITIONS MET / FAST MOVE / NO CHASE / CANCEL`.

For SELL setups the same-expiry protective hedge remains mandatory in the independent planner; the advisor shows current/preferred/minimum net credit. E2/E3 are conditional only and never automatic averaging.

`Already entered — actual fill` mode remains separate and continues to use the user's actual fill/NIFTY inputs for existing P&L, R1/R2/S1/S2 and SL/Target calculations.

## Recorded-data validation

New Market Intelligence snapshots automatically include Money Concentration inside `liquidity`. Test Pack exports explicit Money Concentration columns. Historical evidence can reconstruct the proxy from option rows recorded at that exact timestamp. Replay/calibration never uses future labels as live inputs.

## Performance / Golden Rule

- zero additional Dhan/API calls
- Money Concentration is bounded to the existing compact option chain
- Smart Entry Advisor performs bounded local calculations only
- protected One Brain, Execution Guard, Trade Plan, Position Guardian, Snapshot Service and Pressure Integrity files remain unchanged from v2.72

**These layers improve interpretation and entry planning; they do not become a second trading brain.**
