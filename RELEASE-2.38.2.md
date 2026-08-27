# 2.38.2 — budget-aware pairs and Greeks diagnostics

Complete cumulative release; replaces 2.38.1 and includes all earlier fixes.
Version: 2.38.2_BUDGET_GREEKS_FIX.

## User-approved capital

Default capital is now Rs 900,000. Risk percentage remains 0.5% = Rs 4,500.
Maximum quantity cap remains 1 lot. Entry window remains 10:15–11:30.
No recorded positions/journal entries or secrets are overwritten.
These are planning limits, not broker margin estimates or guaranteed SL fills.

## Greeks

- Basic finite/sign/bounds failures remain UNAVAILABLE; delta-pair discrepancy
  outside existing tolerance remains MODEL MISMATCH. These rows stay excluded.
- CE/PE IV ratio >1.35 alone becomes IV WARNING instead of blanking both legs.
  Original source values remain intact; per-strike status and reason are visible.
- IV-warning rows are conditional candidates, subject to quote/hedge,
  bid/ask, credit and risk checks. This is NOT certification of vendor Greeks.
  Source timestamp/model assumptions may still differ.
- Independent retest premium estimation still requires READY Greeks; it does
  not fabricate a target price for an IV-warning contract.

## Risk-aware selection

- The actual RiskProfile is passed to the spread selector.
- Short/hedge pairs are screened for one-lot expiry maximum loss before ranking,
  including alternative hedge choices. Profiles use the same budget.
- Budget-aware mode can compare a one-strike-step hedge (50 points on this chain)
  as well as wider hedges. Narrower spreads reduce defined maximum loss but may
  also reduce credit; no score or premium gain is promised.
- Full condor combined risk and all final plan risks are checked again. If no
  eligible pair fits, the UI reports budget/eligibility failure, not a fake entry.
- Expiry maximum loss excludes actual charges/slippage. Independent entry
  calculator retains its separate 10% allowance. Neither places broker orders.

## Other

The main candle card now correctly says 3-MINUTE CANDLE.

## Saved-bundle regression

For the user's 2026-08-27 19:06:29 reference snapshot, restored raw source Greeks:
21 IV WARNING, 3 READY, 4 UNAVAILABLE, 2 MODEL MISMATCH. Source theta was missing
for one row previously hidden within the blanket pair-mismatch classification.

Under the Rs 4,500 one-lot budget, the reference CE 24350/24400 spread has
Rs 2,583.75 expiry maximum loss and PE 24050/24000 has Rs 2,424.50, excluding
charges. This is an offline regression result, NOT a live recommendation.
Market-closed action remains WAIT / REFERENCE ONLY.

Upload this full package to the existing repository root, preserve secrets/data,
and reboot the app. Confirm the new version and Rs 900,000 / 0.5% profile.
No deployment, external message or broker order was performed during testing.
Live forward testing is still required; previous news/background-alert limits
remain as documented in RELEASE-2.38.1.md.
