# Nifty Seller Lite — V2.9 Early Barrier + News + Protected Hedge Update

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
2.9.0_EARLY_BARRIER_NEWS_HEDGE_UPDATE
```

Decision-support only. Verify broker quotes, liquidity, margin, fills and hedge prices.
