# Nifty Seller Lite 2.37.0 — Signal and risk safety

## Upload

Upload the contents of `nifty-seller-lite-main` to the existing app repository.
Do not create a second nested app folder. Keep existing secrets and runtime data.
This package intentionally omits data journals, tokens, caches, and test outputs.
No live service was deployed or broker order placed during this update.

## Changes

- WAIT hero shows no confirmed entry, blocker, and no tradable strike recommendation.
- Main AI explanation includes completed 3m/15m candle timestamps and recent flow.
- Presentation does not rewrite canonical strategy direction to MIXED.
- Confirmed opposite Big Player flow vetoes instant directional entry.
- Big Player uses the latest up-to-three completed one-minute intervals, not the
  largest move among historical windows. Previous-day futures bars are excluded;
  old observations expire after 180 seconds and old shock labels do not self-renew.
- Option flow window tolerance is capped at 20%; a 28-second sample cannot be
  presented as a one-minute READY observation.
- RSI reversal uses adjacent completed candles, independent of UI refresh history.
- RSI remains a separate hidden panel. Its decision inputs remain RSI, barrier/OI,
  and Big Player; feed/time/discipline/quote/hedge checks are safety checks.
- PARTIAL/unknown flow cannot become a neutral Iron Condor signal. A condor also
  requires a strong range and both valid barriers. Broken barriers block entry.
- RSI quantity uses conservative maximum spread loss plus a 10% allowance,
  capped by configured lots and the smaller of the configured risk budget/INR5000.
  Capital/risk/lot settings have NOT been increased automatically.
- Optional manual actual-fill form records an explicitly confirmed broker trade
  in the existing one-trade journal. The position guardian checks combined short
  and hedge P&L against the frozen money alert and completed 3m barrier close.
  Monitoring requires the app/feed to run and actual fills to be recorded.
  Alerts are NOT broker stop orders, guaranteed loss caps, or automatic exits.
  Brokerage, charges and slippage can increase actual loss.
- Greeks are never force-matched to another broker. Finite/sign/missing checks
  and conservative same-strike CE/PE model-consistency checks flag unsafe ranking
  inputs. Original source Greeks are retained in source_* columns; quotes/OI remain.
  Pair IV ratio >1.35 or delta-difference deviation >0.15 is a conservative review
  threshold, not proof of a vendor error or a statistically calibrated strategy.
  Flagged rows cannot select a short, buy, or hedge leg. This may cause WAIT until
  reliable Greeks are available. No invented replacement values are generated.

## Verification

- Full regression suite, offline Streamlit startup/card/form tests, new risk and
  timing tests, static error checks, and support-bundle replay performed.
- Original release baseline had five failures: missing gitignore and four stale
  test expectations. Added gitignore; tests now use configured auction boundary,
  restrict JSON assertion to the PDF (not support ZIP), recognize persistent UI
  panels, and respect insufficient valid candle history. Market-session rules and
  minimum candle requirements were not relaxed.
- Supplied 12:58:40 snapshot: original RSI/EMA/MACD calculations remain unchanged;
  recent flow replays as SELLING / NORMAL 31.0 with zero confirmations, not an
  actionable selling signal. The earlier 89 screenshot cannot be replayed from
  this later bundle.
- Supplied option rows: 24 model-mismatch, 3 missing/invalid, 3 ready under the
  conservative screen. This identifies input reliability concerns; it does not
  establish which broker's model is correct.
- Same-time broker comparison and live deployment verification remain necessary.
  Test success does not validate trading profitability or forecast accuracy.
