# Architecture — V2.73 Liquidity Magnet + Smart Entry Advisor

## Canonical live path — unchanged

`services/snapshot_service.py` builds one authoritative `MarketSnapshot`. The protected operational One Brain remains:

1. Trend / Regime — 40%
2. Options Flow — 25%
3. Participation — 20%
4. Barrier / Entry — 15%

Available evidence is normalized; missing evidence is NO VOTE. Market Intelligence has zero One Brain weight.

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
