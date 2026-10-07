# Architecture — V2.70 Pressure Integrity / Smart Alert Hardening

## One authoritative snapshot

`services/snapshot_service.py` builds one `MarketSnapshot`. Screen, journal, PDF and decision logic consume that same snapshot. New Market Intelligence logic performs **no independent broker/API fetch**.

## Canonical operational brain — unchanged

`analysis/simple_brain.py` remains the operational authority after evidence is built:

1. **Trend / Regime — 40%**
2. **Options Flow — 25%**
3. **Participation — 20%**
4. **Barrier / Entry — 15%**

Weights normalize over available evidence. **Missing = no vote.** Market Intelligence never feeds a second directional score back into One Brain.

## Two-speed Market Intelligence

### Fast lane — do not miss the move

Move Radar can enter WATCH / BUILDING / HIGH immediately from pressure acceleration and already-available independent evidence. It does **not** wait for W/M, strong-candle completion or multi-snapshot persistence.

### Quality lane — real vs fake pressure

`analysis/pressure_integrity.py` evaluates:

- independent-family confirmation/opposition
- immediate price response / pressure efficiency
- nearest relevant barrier distance, strength and break pressure
- already-computed live 1m market speed
- pressure persistence and collapse
- W/M and candle evidence as **small supportive context only**

Outputs include pressure quality, fake/real evidence score, move-attack state, realized progress and flip state. A pressure wave that already produced a meaningful price move is marked REALIZED; later cooling becomes EXHAUSTING instead of being misclassified as fake.

## Pattern rule

W/M and strong-candle patterns are never mandatory gates for Move Radar or pressure verification. They can strengthen or oppose a view but their absence is **NO VOTE**.

## Liquidity rule

A liquidity pool already containing price is a **CURRENT BATTLE ZONE**, not a future target. The next directional extension is a **NEXT HUNT ZONE**. A first breach is PENDING ACCEPTANCE. A quick reclaim is only REVERSAL WATCH; follow-through completed closes are required before REVERSAL FAVORED.

## Alert architecture

Market Intelligence is the primary automatic Telegram voice. Pattern/W-M/Big Player evidence remains calculated and journaled, but when the MI lane is enabled it is merged into the Smart Alert context. Material state changes can alert: Big Move Watch, Pressure Verified, Absorption/Fake Risk, Build-up Failed, Exhaustion, Flip Watch/Confirmed, Move Attack, Liquidity Sweep, Alignment or System Conflict.

## WAIT presentation

One Brain action logic is unchanged. UI labels may show NO EDGE / WATCH / ARMED while the canonical action remains WAIT. This is presentation only and cannot open the entry gate.

## Validation

The Market Intelligence Test Pack records raw move pressure, pressure quality, price response, barrier attack, supportive signals, realized progress, flip states, liquidity roles and retrospective 5/15/30-minute outcomes. Future outcome columns are post-hoc labels only and are never exposed to the live predictor.

## Performance rules

- no new Dhan/broker request
- no full-history scan on the critical live path
- no ML model on the live path
- no duplicate evidence weighting
- W/M/candle support is optional, never blocking
- current One Brain core files stay protected

The app remains read-only and never places, modifies or exits broker orders.
