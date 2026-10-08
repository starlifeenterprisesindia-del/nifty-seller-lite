# Nifty Seller Lite 2.74.1 — Barrier Correctness Sync

Read-only NIFTY options decision-support app with one operational path:

**Regime → Direction → Entry → Risk → Action**

The canonical Simple One-Brain remains unchanged: **Trend/Regime 40% + Options Flow 25% + Participation 20% + Barrier/Entry 15%**. Missing evidence is **NO VOTE**. Future Brain and Market Intelligence remain advisory/shadow layers and never become a second order/entry authority.


## 2.74.1 highlights

- **Completed-close barrier promotion:** live spot/wicks can test or temporarily cross R1/S1, but a barrier is not promoted/role-flipped until a completed 3-minute close accepts beyond the full zone.
- **Confluence-zone integrity:** barrier clusters are built first and cleared only after the completed close clears the whole zone; individual anchors no longer disappear mid-zone.
- **Net-vulnerability break bias:** `Break Pressure - Barrier Strength` is compared on both sides, so a weak support under moderate pressure is correctly distinguished from a very strong resistance under similar raw pressure.
- **Recency-aware reaction quality:** recent completed 1-minute reactions (measured over the next ~3 minutes) receive modestly higher weight than old touches without erasing older evidence.
- **System-wide sync:** `LevelBundle` confirmation logic feeds One Brain Barrier/Entry, trade-plan, patterns and pre-touch; `BarrierMap` feeds Market Intelligence, Pressure Integrity, Liquidity, Big Player, Smart Entry, SL/Target, Premium Calculator, maps, replay, PDF and journal views.
- **Market Intelligence consistency:** breakout direction now follows the same net-vulnerability/barrier-bias logic instead of comparing raw break pressure alone.
- **No new Dhan/broker/API calls. No weight changes.** One Brain/MI thresholds, Pressure Integrity, Institutional Window, Liquidity Magnet, Smart Entry and Journal logic are otherwise unchanged.

## 2.74 highlights

- **Two independent paper-validation lanes:** One Brain and Market Intelligence are now recorded separately, so their results are never mixed.
- **One Brain paper trigger:** concrete One-Brain action + direction strength at least 54/100 + entry readiness at least 62/100, followed by the existing protected-plan/live-data/risk guard.
- **Market Intelligence paper trigger:** Institutional Window `OPEN/STRONG` with 6/6 gates and data safety, or Pressure Integrity `VERIFIED/REALIZED` with active barrier/attack support. `BULLISH → PE SELL`, `BEARISH → CE SELL` for seller-side research.
- **Higher research capacity:** up to 25 paper samples/day per lane with a 5-minute research cooldown. Samples can overlap and are explicitly labelled as correlated research observations.
- **Liquidity Magnet remains evidence, not a standalone trigger:** it is frozen at entry and used for aligned/conflict review without creating a trade by itself.
- **Entry-time evidence freeze:** every paper trade stores Pressure Integrity, Institutional Window, Liquidity Magnet, barrier/attack state, One-Brain state and alignment context.
- **Outcome attribution:** closed paper trades store gross P&L, estimated charges/net P&L, MFE/MAE, duration, 5m/15m/30m directional outcomes and evidence-based `likely` profit/loss factors.
- **Aligned/conflict analysis:** when One Brain and MI are simultaneously active, each lane records whether they were aligned or in conflict for later comparison.
- **Journal durability hardening:** Decision Journal read-update-write is atomic; Railway paper persistence accepts later 5m/15m/30m backfills without allowing a stale OPEN payload to reopen a closed trade.
- **Zero new broker/API calls and zero changes to One Brain/MI calculations.**

## 2.73 highlights

- **Liquidity Magnet / Money Concentration:** Market Intelligence now compares visible option-chain concentration above vs below spot using existing OI, positive OI change and volume. Output is `UPSIDE / DOWNSIDE / BALANCED` plus 0–100 concentration scores. This is a proxy, not exact rupee money or proof of stop hunting.
- **Institutional Window integration:** Money Magnet alignment is a bounded Path Clearance modifier. Strong opposite concentration prevents a `STRONG` upgrade; six-gate `OPEN` logic remains intact.
- **Recorded-data validation:** new snapshots save Money Concentration inside the Liquidity payload. Older recorded sessions can backfill it from same-timestamp option rows; future outcomes are never inputs. Calibration adds descriptive Money Concentration diagnostics.
- **Smart Entry Advisor:** Premium Calculator → `Plan new entry` now gives one-glance `Current / Best Entry / Acceptable / No Chase` plus `WAIT / ARMED / ENTRY CONDITIONS MET / FAST MOVE / NO CHASE / CANCEL` status.
- **Price Quality vs Move Urgency:** a better premium is preferred when time allows, but a genuinely accelerating move can mark the current executable price acceptable so the app does not wait for a perfect retest and miss the movement.
- **Defined-risk SELL context:** current/preferred/minimum net credit is shown with the selected hedge.
- **Simple default UI, full detail preserved:** planner, ladder, invalidation, scores, IV/time and existing R1/R2/S1/S2 + SL/Target calculations remain available in collapsed detail.
- **W/M + Strong Candle stay supportive only:** neither is mandatory for Institutional Window or Smart Entry Advisor.
- **Zero extra Dhan/API calls.**

## 2.72/2.71/2.70 foundation retained

- Institutional Opportunity Window with six gates and smart state-change alerts.
- Snapshot Integrity + latency observability + on-demand replay/calibration.
- Pressure Integrity: real/fake/absorbed/exhausting/flip states.
- Move Attack and Big Move Radar.
- Current Battle Zone vs Next Hunt Zone.
- Smart Alert controller with one primary Market Intelligence Telegram voice.
- Durable Day Memory on SQLite and hardened bounded runtime state stores.

## Safety/performance rules

- no new broker/API request for Liquidity Magnet or Smart Entry Advisor
- no future data in live calculations
- no automatic threshold tuning
- replay/calibration is on-demand/post-market
- no duplicate evidence weighting into One Brain
- Money Concentration uses visible option positioning/trading data only
- protected One Brain decision/risk files remain unchanged from 2.72

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Verify

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

Runtime state, credentials, caches and generated reports are excluded through `.gitignore`.
