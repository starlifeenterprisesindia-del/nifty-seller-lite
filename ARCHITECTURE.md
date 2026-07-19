# Architecture — V2.6 Market Memory & Full Live Audit PDF

## One market-data authority

Only `services/snapshot_service.py` reads DhanHQ and builds one `MarketSnapshot`.
Analysis modules do not fetch data. The same option-chain snapshot is reused for option
intelligence, strike planning, exact-leg monitoring and PDF reporting.

## Exactly one strategy brain

Only `analysis/decision.py::calculate_final_decision` may produce or stabilize:

- CE Sell suitability
- PE Sell suitability
- Iron Condor suitability
- WAIT Need
- Final Action
- Signal State
- Fake-Move Risk
- Conditional 5–15 minute outlook

`services/snapshot_service.py` calls that function exactly once. There is no
`prediction.py`, `forecast.py` or separate market-direction engine.

These downstream modules cannot select or alter a strategy:

- `analysis/evidence_matrix.py` — display summary only
- `analysis/trade_plan.py` — protected strikes after the final decision
- `analysis/execution_guard.py` — pre-entry safety and risk gate
- `analysis/position_guardian.py` — post-entry read-only monitor
- `services/pdf_report.py` — immutable rendering of the completed snapshot

## PDF report boundary

`services/pdf_report.py::build_full_audit_pdf` accepts an already-built
`MarketSnapshot`. It cannot:

- call DhanHQ or any network client,
- read or write state stores,
- call `calculate_final_decision`,
- alter scores, memory, action, strikes or guard status.

It may call the existing display-only compact evidence matrix so the PDF matches the
screen. Every strategy value, outlook, level and timestamp is taken from the same
snapshot object. The report includes recent completed candles and current ATM-window
option rows for later live-market comparison.

## Market memory and anti-flip logic

The atomic discipline-state file stores bounded same-day signal evidence: strategy
scores, direction, signal state, fake-move risk and spot. It stores no credentials,
account information or broker orders.

- First aligned snapshot: direction is `DEVELOPING`.
- Repeated aligned snapshot: direction can become `CONFIRMED`.
- One ordinary opposite snapshot: final action is held at `WAIT` as a transition.
- Persistent opposite evidence with adequate score separation can confirm reversal.
- A rapid-reversal override is allowed only when the score gap is large and core market,
  option flow and confidence strongly agree.
- Stale, cross-day and overly old memory is rejected.

The Execution Guard still requires its own final-action persistence before entry. Thus
memory cannot bypass freshness, risk-budget, entry-window or one-trade/day rules.

## Fake-move risk ownership

Fake-move risk is calculated inside the same decision module from already-owned inputs:

- 3m/15m price-action conflict
- Core market versus option-flow disagreement
- Top-7 confirmation or lack of confirmation
- NIFTY-futures volume participation
- Nearby support/resistance
- Option-window maturity and persistence
- Direction change versus recent memory
- VIX and verified event risk

It does not create a separate action. High fake-move risk can only increase caution or
hold the one-brain result at WAIT.

## Outlook semantics

The 5–15 minute outlook blends current directional evidence with recent bounded memory.
Bullish, range and bearish path weights add to 100. High fake-move risk shifts conviction
toward transition/range. The displayed invalidation comes from the existing price-action
and level engines. These are conditional scenario weights, not profit probabilities.

## State and safety

- Memory is appended only when market, quote, completed candle and option chain are live.
- Reference/weekend snapshots never pollute live signal memory.
- Option history remains bounded and same-session only.
- Strike Planner, Execution Guard and Position Guardian never place broker orders.
- PDF creation is read-only and snapshot-specific.
- Runtime files, generated PDFs, caches and credentials remain outside active source code.
