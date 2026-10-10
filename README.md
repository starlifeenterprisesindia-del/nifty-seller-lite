# Nifty Seller Lite 2.76.0 — Integrated Edge Context

Read-only NIFTY options decision-support app with one operational path:

**Regime → Direction → Entry → Risk → Action**

The canonical Simple One-Brain remains unchanged: **Trend/Regime 40% + Options Flow 25% + Participation 20% + Barrier/Entry 15%**. Missing evidence is **NO VOTE**. Future Brain and Market Intelligence remain advisory/shadow layers and never become a second order/entry authority.


## 2.76.0 integrated edge context

Three research-backed contexts are now fitted into their natural existing modules without creating a second brain or changing the canonical decision score:

- **VIX Expected-Move Context → Barrier + Market Intelligence:** reuses the Barrier Map's existing India-VIX daily/remaining expected-move calculation, anchors a live 1σ-style envelope to the session open/previous close, shows range utilisation and whether the nearest barrier sits inside realistic remaining room. It is magnitude/room context, never a direction signal.
- **Trend + Mean-Reversion Context → Pullback vs Reversal:** combines completed 15m EMA20/EMA50 trend, RSI(2) extreme, ATR stretch, nearest trend-side barrier, 3m confirmation/candle support and existing Pressure Integrity. It labels pullback-completion watch/confirmation versus genuine reversal risk. It is an intraday adaptation of the research principle, not a copied daily-system win-rate claim.
- **DTE-matched IV Percentile → Strategy + Strike Value:** current ATM IV is compared only with prior real ATM-IV sessions in the same calendar-DTE bucket (`0–1D`, `2–3D`, `4–7D`, `8+D`). At least 20 matched prior sessions are required; otherwise the result is `WARMING / NO VOTE`. No synthetic history is created.

Golden-rule guarantees for v2.76:

- all three contexts have **decision weight 0**; One Brain scores/thresholds/final action are unchanged
- no additional Dhan/broker/API call
- IV history is loaded from the existing tiny local option-state summary and cached once per trading date
- missing/stale inputs stay **NO VOTE**, never fake neutral/zero
- results are recorded in Day Memory / research journal for later live/replay validation
- canonical One Brain, Execution Guard, Trade Plan, Barrier formulas, Pressure Integrity and MI gate calculations are not retuned


## 2.75.2 hotfix

- Railway `/ready` now separates **deployment ready** from **Dhan snapshot ready** without making Railway restart a healthy service.
- Streamlit waits and retries automatically during a Dhan 429 cooldown instead of leaving the first snapshot on a manual-error screen.
- Day Memory and premium-alert background lanes stay quiet during a bounded post-deploy startup grace, so the foreground One Brain snapshot gets first priority.
- Background lanes skip themselves while the shared Dhan gateway is in cooldown.
- No One Brain score, weight, barrier formula, MI gate, pressure formula, FII/DII weight, or trading threshold was changed by this hotfix.


## 2.75.0 highlights

- **No core retuning:** One Brain weights, 54/62 research floors, MI gates, Pressure/Barrier formulas and FII/DII live direction weight are unchanged.
- **Journal validation hardening:** restart recovery, OB candidate research sampling, MI unique-cycle dedupe, richer paper-trade finalization and evidence diagnostics.
- **Performance hardening:** live Snapshot path uses stale-while-revalidate Instrument Master refresh instead of waiting on a stale cache download.
- **Readability:** full Regime card, strong W/M/candle/evidence highlights, staged Barrier vulnerability/attack/break visuals and clearer Liquidity Concentration wording.
- **Alert discipline:** STRONG ONLY Market Intelligence delivery with story-level dedupe; lower-priority events remain available as app/journal evidence.
- **Cleaner live route:** Strategy + Strike always visible; Live Chart moved under Advanced; Quick Guide hidden from the operational screen.
- **Calculator:** large-lot what-if calculations no longer stop at the live risk cap; live execution safety remains unchanged. IV modes are Auto History / Manual What-if / Off.
- **Top-9 / late session:** explicit LIVE/WARMING/REFERENCE/NO-VOTE handling rather than fake zero evidence.
- **Testing/recording:** one-click Master Live Test Pack adds decision, paper, Smart Entry, alert-delivery and performance timelines without new live broker calls.


## 2.74.2 hotfix

- Fixes a live-only `NameError` in the Market Intelligence Telegram/app alert controller when an MI alert becomes eligible.
- The fix only binds the already-computed `institutional_window` payload inside the alert function; no market calculation, barrier logic, One Brain logic, journal logic, thresholds, weights, or API calls change.
- v2.74.1 Barrier Correctness Sync remains intact.

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
