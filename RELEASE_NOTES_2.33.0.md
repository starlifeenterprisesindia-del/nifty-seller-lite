# Nifty Seller Lite 2.33.0

## One-Brain stability

- The existing `calculate_final_decision` remains the only strategy selector.
- Strong aligned moves require 2 snapshots/30 seconds; normal setups 3/60 seconds;
  Iron Condor 3/120 seconds; direction reversals 3/180 seconds.
- A 12-point leader margin is required; developing or flipping signals stay at WAIT.
- Entry Guard requires 75% strategy score, 75% entry confidence and aligned Big Player evidence.

## OI and participation integrity

- Intraday OI uses same expiry, strike, side and security ID when available.
- Negative volume-counter resets and unmatched rows are invalid comparisons.
- Healthy bid/ask midpoint is preferred over stale LTP for premium movement.
- Options/OI/volume and bounded Big Player evidence receive more One-Brain weight.

## Iron Condor Balance Guard

- Checks CE/PE short-leg delta, credit balance and directional room.
- Aligned 3-minute/15-minute RSI and persistent directional flow can block the condor.

## Auto Shadow Journal

- Records at most five confirmed paper trades per market session.
- Supports CE/PE BUY, CE/PE SELL and Iron Condor with complete legs and entry reasons.
- Tracks MFE, MAE, target/stop/time exit and estimated net P&L.
- Never calls a broker order API or uses real money.
