# Nifty Seller Lite — V2.18.0 Hinglish One-Brain UI


## V2.18.0 changes

- Support/Resistance cards now say clearly whether the score is the level's holding strength or breaking pressure.
- Main AI includes the relevant barrier verdict and 3-minute close confirmation.
- Nearest Levels and Core Market Evidence are combined near the top.
- Top strategy is one compact card; all-five comparison stays in the lower audit.
- NIFTY Top-7, FII/DII and News/Event are separate One-Brain evidence cards.
- Top-7 shows current weighted move and previous-snapshot change when available.
- Wide evidence, outlook and strategy tables are responsive mobile cards.
- Quick and Full Audit PDFs use the same updated snapshot and wording.

Expected version:

```text
2.18.0_HINGLISH_ONE_BRAIN_UI
```

## V2.17.2 fixes

- Dhan quote/VIX timestamps now parse DD/MM/YYYY explicitly, so 03/08/2026 is never misread as 08/03/2026.
- Live-session confirmation uses the corrected quote age; stale/future timestamps remain safely blocked.
- FII/DII journal now writes three atomic local copies: primary, mirror and rescue.
- Optional private GitHub journal remains the permanent cross-redeploy source and auto-restores a fresh deployment.
- The UI clearly warns when cloud persistence is OFF instead of calling runtime-only storage safe.
- Missing FII/DII receives zero decision weight; Top-7 evidence is not diluted by a missing institutional row.
- First-snapshot option-flow extremes are confidence-calibrated while 1m/3m/5m continuity warms up.
- Reference-only session state no longer appears as fake-move risk 100%; data/session blocking stays separate.
- Bullish/range/bearish outlook paths retain a small non-zero uncertainty floor.
- Overlapping support/resistance is shown as one Decision / Compression Zone.

Expected version:

```text
2.17.2_LIVE_SESSION_FII_DURABILITY
```

## V2.17 additions

- Main screen is reorganized around one prominent **Final One-Brain** card.
- The selected valid setup is soft green; WAIT stays soft amber and never looks like an entry approval.
- The existing AI Strategy Planner appears immediately below the Brain and shows only the **top 3** setups on the normal screen.
- A compact nearest Resistance / NIFTY / Support strip replaces the large always-open Barrier Map. The full map remains inside detailed evidence.
- Risk controls, evidence/outlook and reports are collapsed by default to reduce visual noise.
- W/M, Special Candle, FII/DII and data/entry status remain visible as small evidence cards without creating separate actions.
- FII/DII supports an optional private GitHub cloud journal. V2.17.2 keeps primary + mirror + rescue local copies as runtime fallback.
- Cloud and local rows merge date-wise; blank numeric inputs cannot erase already saved valid FII/DII values.
- A cloud failure does not block local saving, but the UI clearly warns that local runtime copies are not redeploy-permanent.

Expected version:

```text
2.17.1_SIMPLE_VIEW_DEDUP_FINAL
```

### Optional Streamlit Secrets for permanent FII/DII storage

```toml
[fii_dii_cloud]
owner = "YOUR_GITHUB_USERNAME"
repo = "nifty-seller-private-data"
token = "YOUR_FINE_GRAINED_TOKEN"
path = "fii_dii_15_sessions.json"
branch = "main"
```

Create a separate private data repository and give the fine-grained token access only to that repository with Contents read/write permission. Do not commit the token or `.streamlit/secrets.toml`.

---


## V2.16 AI strategy planner additions

- The same **Final One-Brain** now compares five setups: **CE Buy, PE Buy, CE Sell with hedge, PE Sell with hedge and Iron Condor with hedges**.
- Buy setups require directional 3m/15m alignment, supportive volume, sufficient room to the next barrier, usable option flow and a reasonable VIX/premium environment.
- Slow support-hold or resistance-hold conditions can still favour protected selling; strong aligned momentum can favour option buying.
- The existing Protected Strike Planner is upgraded instead of adding another noisy dashboard section.
- Buy plans select one liquid ATM/near-ITM leg; seller plans keep mandatory farther-OTM protection.
- The selected approved row turns **soft green automatically**. When the Final Action is WAIT, the highest row is only soft amber as a reference and never receives green approval.
- Brain Fit is strategy suitability, not a guaranteed profit probability.
- Execution Guard, one-trade discipline, manual trade record and Position Guardian now understand buy as well as protected seller setups.

Expected version:

```text
2.16.0_AI_STRATEGY_PLANNER
```

---


## V2.15 pattern confirmation additions

- Completed **3-minute W/M structure** detection: W is bullish, M is bearish.
- W is strengthened only near support; M is strengthened only near resistance.
- Neckline confirmation, ATR-based symmetry/depth, freshness and volume context reduce false/noisy patterns.
- Added a small **Special Candle** set: Bull/Bear Engulfing, Hammer, Shooting Star, Morning Star, Evening Star and level-based Doji.
- Noisy single-candle shapes in the middle of a range are hidden to keep the app quiet.
- Both modules feed the existing Final One-Brain as bounded evidence only: W/M max 8 points, candle max 4 points, combined cap 12 points.
- Conflicting W/M and candle evidence is reduced and increases WAIT/fake-move caution.
- The All Features table now has two compact rows and short Hinglish Current Result text. No row gives a separate action.

## Deployment

Use `V2_15_UPDATE_LIST.txt`, or deploy the complete extracted repository. Preserve Streamlit Secrets and the runtime `data/` folder.

Expected version:

```text
2.15.0_PATTERN_CONFIRMATION
```

---

## V2.14 Premium Explainability (included)

## Combined fixes included

This package starts from V2.13 Spot Premium Calculator and already includes the earlier V2.13.1 Presentation Integrity corrections, so the user does not need to upload the older patch first.

- Support/resistance wording is generated from the actual Barrier Map side.
- A mixed core tape is displayed as **MIXED**; option-flow lean is explained separately.
- Old/stale news is normalized and cannot become the displayed Main Blocker.
- PE SELL and CE SELL reference candidates show correct support/resistance invalidation.
- Screen, Quick PDF and Full Audit PDF use the same presentation-safe copy of the canonical snapshot.
- Dhan-chain IV/Greeks are range-validated and broker IV differences are explained.

## V2.14 premium explainability additions

- Current premium is split visibly into **Intrinsic Value + Time Value** with Time Value share %.
- Manual **Expected IV change (points)** input applies `Vega × IV change` to lower/upper scenarios.
- The result exposes separate premium drivers: Delta+Gamma spot effect, Theta time effect, Vega IV effect and live chain/smile adjustment.
- A 15/30/60-minute sideways table estimates Theta decay while assuming spot and IV stay unchanged.
- Selected-strike OI, day OI change and volume are shown as demand/participation context. The app does not claim a false exact rupee attribution from OI/volume; the same-expiry live chain is used as the market-price proxy.
- Every target estimate is floored at intrinsic value, so future Time Value cannot become negative.
- The calculator stays read-only and cannot change One-Brain Final Action.

## Deployment — replace/update only

Use `V2_14_UPDATE_LIST.txt`. Upload the extracted files at the same repository paths and replace matching files. Add `analysis/presentation_safety.py` if it is not already present. Do **not** delete Streamlit Secrets or the runtime `data/` folder.

Expected version:

```text
2.14.0_PREMIUM_EXPLAINABILITY
```

---

## V2.12 changes

### 1. Mobile-first Main AI clarity
- Main AI top metrics now use compact responsive cards instead of oversized stacked Streamlit metrics.
- `Decision Confidence` is presented as **Entry Readiness**, while **Rukh Evidence** separately exposes the matching Core Market bullish/bearish/range evidence already calculated by the canonical snapshot.
- Technical feed names are removed from the main user card; detailed feed diagnostics remain in audit evidence.
- `Brain score` is relabeled **Strategy Suitability** and `Quality` is relabeled **Strike + Hedge Quality** so neither looks like a profit probability.

### 2. Strict news freshness
- Article publication time, not RSS fetch time, controls freshness.
- <=90 minutes: READY; 90–180 minutes: OLD / low weight; >180 minutes: zero decision weight.
- Explicit old dates embedded in republished headlines are rejected (for example a June 8 story resurfacing in late July).
- Main AI no longer calls stale headlines “fresh news”.

### 3. Correct session wording
- Before 09:00 IST: `MARKET CLOSED — NEXT SESSION NOT OPEN`.
- 09:00–09:15 IST: separate `PRE-OPEN — REFERENCE DATA` state.
- Regular live session still requires fresh quotes plus fresh completed candles.

### 4. 24-hour automatic temporary-data cleanup
On every app rerun, raw/temporary option snapshots, abandoned news cache and safe temp snapshot files older than 24 hours are pruned automatically.
The cleanup does **not** delete the FII/DII 15-session journal, manual discipline/trade state, instrument master or compact learning summaries.

### 5. Two report downloads
- **Quick Market Report**: compact 2-page Main AI + Road Map + evidence/safety report for daily use.
- **Full Audit PDF**: detailed immutable snapshot audit for verification/debugging.
Both read the same authoritative snapshot and perform no independent strategy calculation.

### 6. One-Brain architecture preserved
`analysis/decision.py::calculate_final_decision` remains the single canonical strategy brain. The new labels, mobile cards, news guards, housekeeping and PDFs are presentation/safety layers only.

## Update deployment — replace/update only
Use `V2_12_UPDATE_LIST.txt`. Replace matching files at the same paths and add the new files. Do **not** delete the repository or the runtime `data/` folder.

Expected version:

```text
2.12.0_ACCURACY_CLARITY_RETENTION
```

---

## V2.11 changes

### 1. Main AI — Market View at the very top
A new top-screen summary reads the **same authoritative MarketSnapshot and same Final
One-Brain Decision**. It does not calculate a second signal. In one place it shows:
- NIFTY, market rukh, Final Action, decision confidence and Market Danger
- simple Hinglish explanation: market upar/neeche/range kyun lag raha hai
- live-feed and entry-readiness status
- current protected reference/selected setup with mandatory hedge
- nearest support/resistance with Strength and Break Pressure
- probable range, Range Confidence, India VIX context, FII/DII and News state
- last-snapshot changes for decision confidence, speed and barrier pressure

### 2. Duplicate top sections removed from the normal screen
The standalone Pre-Touch Support/Resistance and large Final One-Brain block are no longer
shown as separate top-level sections. Their calculations remain active and are consumed by
the Main AI/Barrier Map. The detailed Final One-Brain and Protected Planner stay inside one
collapsed detail expander for audit/proof.

The Barrier Map also no longer repeats a second row of NIFTY/range/speed metric cards; those
headline values now live in Main AI while the map focuses on R1/R2/S1/S2 and live pressure.

### 3. Safer update/add workflow
- FII/DII keeps **Save / update selected date**; the normal UI no longer exposes a delete-date button.
- Destructive cache/history maintenance is hidden by default and can only be exposed with
  `NSL_SHOW_MAINTENANCE=1`.
- Developer Raw Market Data is hidden by default and can only be exposed with
  `NSL_SHOW_DEVELOPER_DATA=1`.
- FII/DII JSON backup/restore remains merge-based for deployment resets.

### 4. VIX danger calculation tightened
A fast **rise** in India VIX now adds materially more danger than an equal-sized VIX fall.
The current VIX regime still supplies the base risk. Daily expected move remains the
approximate `Spot × (VIX/100) / sqrt(252)` estimate; live remaining-session move is scaled by
the square root of the remaining 375-minute cash-session fraction. VIX is still risk context,
never bullish/bearish direction.

### 5. Updated Main AI Audit PDF
The PDF first page now mirrors the new Main AI screen: Final Action, Hinglish explanation,
nearest barriers, range/VIX context and the protected setup. Detailed evidence follows on
later pages. The PDF still performs no API call and no strategy recalculation.

## Update deployment — replace/update only
Use `V2_11_UPDATE_LIST.txt`. Upload the listed files to the same paths and replace matching
files. Do **not** delete the repository or runtime data folder.

Expected version:

```text
2.12.0_ACCURACY_CLARITY_RETENTION
```

---

## V2.10 Live Barrier Roadmap + Speed/VIX

## V2.10 changes

### 1. Top-screen Live Barrier + Range Map
A new display-only roadmap is rendered near the top of the app. It does not create a
second strategy brain and cannot override the Final One-Brain Decision. It shows:
- nearest and next resistance (R1/R2)
- nearest and next support (S1/S2)
- Barrier Strength 0–100
- Break Pressure 0–100
- probable current range + Range Confidence
- position inside the range
- upside/downside break bias and next range path

Barrier Strength is an evidence score, **not** a guaranteed hold probability. It combines
price-structure confluence, CE/PE OI wall/flow behaviour, recent reaction quality,
3m/15m momentum, Top-7 direction and volume confirmation.

### 2. Market Speed / Danger Meter
The roadmap classifies the live tape as `NORMAL`, `ACTIVE`, `FAST` or `DANGER` and
adds a separate speed direction (`UP`, `DOWN`, `MIXED`). The score combines:
- 1m/3m/5m spot displacement versus current 3m ATR
- futures relative-volume expansion
- option-premium shock across the existing 1m/3m/5m option windows
- India VIX regime and short-horizon VIX change when enough same-session snapshots exist
- Top-7 synchronisation
- opening/closing/expiry time-risk modifier

The first 5/15 minutes of VIX-speed history can show `warming` until sufficient new
snapshots are collected after deployment.

### 3. India VIX expected-move context
The roadmap uses the already fetched live India VIX value to calculate a one-trading-day
volatility move estimate: `Spot × (VIX/100) / sqrt(252)`. During a confirmed live session,
it also scales that move by the square root of the remaining fraction of the 375-minute
NSE cash session. VIX is used as volatility/risk context, never as bullish/bearish direction.

### 4. Barrier break path
Once spot clears an old barrier zone, that old zone is no longer kept as the active barrier;
the next valid R/S cluster is promoted. This makes the map answer “agar yeh toot gaya to
next barrier kahan hai aur kitna strong hai?” without waiting for a separate manual chart.

### 5. VIX telemetry persisted with bounded option history
The existing same-day bounded option-state snapshots now also store the live VIX value.
This allows 5m/15m VIX-speed calculations without adding another unbounded state file.
Runtime state remains local/atomic and the app stays read-only.

### 6. Audit PDF
The full audit PDF now includes the Barrier + Range Map values, strength/break-pressure,
range state, market-speed state and VIX remaining-move context.

## Update deployment — replace only, no delete
Use `V2_10_UPDATE_LIST.txt`. Upload only the listed files and replace matching files in
the existing repository. Do **not** delete app folders or runtime JSON state files.

Expected version:

```text
2.10.0_BARRIER_ROADMAP_SPEED_VIX
```

---


Read-only Streamlit decision-support app built around one authoritative DhanHQ
`MarketSnapshot` and exactly one canonical strategy brain:
`analysis/decision.py::calculate_final_decision`.

## V2.9 changes

### 1. Pre-Touch Support / Resistance
The app now shows probable support and resistance **before price touches the level**.
It combines the existing completed-candle structure with Previous Day High/Low,
Opening Range High/Low and current CE/PE OI wall/cluster information.

The pre-touch layer is an early-warning layer only. It does not create a second strategy
brain and cannot override the final CE/PE/Condor/WAIT decision.

### 2. FII/DII 15-session persistence repair
- One row per trading date.
- Saving the same date updates only that date; no duplicate row is created.
- Latest 15 dated rows are retained.
- Primary + mirror atomic JSON copies are monotonically merged on read.
- A stale/corrupt primary copy cannot make already-saved rows move backwards while the
  same deployment filesystem remains alive.
- A newer event-only row no longer hides the latest valid FII/DII values.
- Backup restore is merge-based instead of destructive replacement.

Runtime JSON remains gitignored, so normal source-file replacement does not delete it.
The existing JSON download/restore remains available for deployment/filesystem resets.

### 3. Live market-news context
A public RSS news layer fetches recent Indian/global market-moving headlines using a
3-minute local TTL cache. It shows:
- News Bias: Bullish / Bearish / Mixed / Neutral
- News Risk: Low / Medium / High
- headline age, impact and source

If public news fails or has no fresh data, it becomes `UNAVAILABLE` / `NO RECENT NEWS`.
Old/unavailable headlines are never silently treated as live directional evidence.
News risk can increase WAIT/fake-move caution, but news does not create another trading
brain.

### 4. Simple Hinglish Brain explanation
The technical labels remain unchanged. Under Final One-Brain Decision the app now adds a
simple line such as:

`Market upar ja sakta hai kyunki Price Action bullish hai, Options/OI flow support kar raha hai...`

or

`Market neeche ja sakta hai kyunki Price Action bearish hai, Options/OI flow pressure dikha raha hai...`

It also mentions nearby pre-touch support/resistance and fresh news risk when relevant.

### 5. Best CE Sell / PE Sell with mandatory hedge
The protected strike planner still runs **after** the Final One-Brain Decision. It now:
- shows a compact Best CE / PE Sell + Hedge table near the main decision,
- updates both directional protected candidates from every fresh option-chain snapshot,
- highlights the Brain Pick when CE Sell or PE Sell is selected,
- keeps candidates reference-only when the final action is WAIT,
- searches a bounded 2–5 strike-step window for a better liquid hedge instead of blindly
  taking the first farther-OTM strike.

## Retained safety gates
A live setup cannot become `ENTRY READY` unless the required live feeds, option-flow
continuity, signal persistence, completed-candle agreement, protected risk budget,
one-trade/day discipline and entry-window rules all pass.

## Update deployment — no delete workflow
Use `V2_9_UPDATE_LIST.txt`. Upload only the listed files to the existing repository and
**replace matching files**. Do not delete the app folders or runtime data files.

Expected version:

```text
2.9.1_FII_FUTURES_POSITION_FIX
```

Decision-support only. Verify broker quotes, liquidity, margin, fills and hedge prices.
