# Architecture — V2.9 Early Barrier + News + Protected Hedge Update

## One authoritative snapshot
`services/snapshot_service.py` builds one `MarketSnapshot`. Screen, PDF, decision,
pre-touch warnings, protected strike candidates, execution guard and position guardian
all consume that same snapshot.

## One canonical strategy brain
`analysis/decision.py::calculate_final_decision` remains the only CE Sell / PE Sell /
Iron Condor / WAIT selector. No new module may override its final action.

## Early barrier layer
`analysis/pre_touch_barriers.py` combines existing support/resistance structure with
Previous Day / Opening Range anchors and current CE/PE OI walls/clusters. It provides an
early warning before touch. It is not a strategy selector.

## News context
`services/news_service.py` reads recent public market-news RSS, classifies conservative
risk/bias context and caches it for a short TTL. Failure becomes UNAVAILABLE. The final
brain uses news only as a risk/WAIT/fake-move caution; no separate news strategy exists.

## FII/DII journal
`services/context_store.py` owns the date-wise 15-session journal. Primary and mirror
atomic files are merged monotonically. `analysis/market_context.py` keeps latest valid
institutional data separate from the latest event row.

## Protected strike planner
`analysis/trade_plan.py` runs only after the final brain. It selects read-only protected
CE/PE candidates, including a mandatory farther-OTM hedge. Hedge search is bounded and
scores liquidity, distance and credit/risk efficiency. It cannot change strategy scores.

## Read-only execution
No module places, modifies or exits broker orders. Execution Guard and Position Guardian
remain deterministic read-only risk/discipline layers.

## Spot-to-Premium utility boundary (V2.13)
- `analysis/spot_premium_calculator.py` consumes only the already-fetched snapshot option chain plus manual user inputs.
- It estimates scenario premiums and P&L; it never emits CE SELL / PE SELL / IRON CONDOR / WAIT strategy decisions.
- It has no broker client, no network call, no state-store write and no order-placement path.
- The only canonical strategy brain remains `analysis/decision.py::calculate_final_decision`.
