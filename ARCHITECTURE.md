# Architecture — V1.8 Position Guardian

## One market-data authority

Only `services/snapshot_service.py` reads DhanHQ and builds one `MarketSnapshot`.
Analysis modules never fetch data. The option-chain response is parsed once:

- ATM±7 rows feed the visible chain, option intelligence and strike planner.
- The same full parsed chain is kept only inside the snapshot build for monitoring
  exact legs of a manually marked open trade.

No second option-chain API request is made.

## Canonical processing order

1. Core market and options evidence
2. `analysis/decision.py` — the only final strategy brain
3. `analysis/trade_plan.py` — protected strikes after the decision
4. `analysis/execution_guard.py` — pre-entry safety gate
5. `analysis/position_guardian.py` — post-entry read-only monitor

The trade planner, execution guard and position guardian cannot calculate final
strategy scores, change the selected action, fetch data or place orders.

## Position record

`create_trade_record()` runs only after an `ENTRY READY` setup is manually marked.
It freezes:

- action, setup and expiry
- exact short and hedge strikes
- conservative entry prices: short bid, hedge ask, LTP fallback
- entry credit, lots, lot size and entry spot
- target debit, SL debit, spot invalidation and forced-exit time

`services/discipline_store.py` stores this small record in the current-day journal.
Schema V1 is migrated to V2 by adding an empty trade record without discarding the
existing same-day signal history or one-trade lock.

## Position monitoring maths

For the exact stored legs:

- short closing cost = current ask, LTP fallback
- hedge closing value = current bid, LTP fallback
- combination close debit = sum(short closing costs) − sum(hedge closing values)
- P&L points = frozen entry credit − current close debit
- estimated P&L rupees = P&L points × lot size × lots

The monitor blocks P&L when an exact leg, expiry, lot value or entry credit is
missing. It never substitutes a nearby strike.

## Alert precedence

For a live open position:

1. data integrity block
2. compulsory exit time
3. NIFTY spot invalidation
4. protected-combination SL debit
5. protected-combination target debit
6. profit-protection / rising-risk warning
7. hold and monitor

A closed or weekend session is always reference-only. All exits remain manual.

## Bounded local state

- `services/option_state_store.py`: same-day bounded option-flow snapshots
- `services/context_store.py`: bounded FII/DII and verified-event history
- `services/discipline_store.py`: current-day signals, one-trade lock, frozen manual
  trade and manual outcome

Runtime files are gitignored and may reset after a Streamlit redeploy. Broker order
IDs, credentials and account positions are never stored.
