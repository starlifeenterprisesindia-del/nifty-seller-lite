# Nifty Seller Lite v2.36.0

## RSI Top–Bottom Setup

- Added a separate, hidden-by-default `RSI Top–Bottom Setup — Alag Strategy` panel.
- The setup reads the existing 3-minute RSI, Barrier + OI synthesis, Big Player activity,
  and protected strike plans.
- It can return `CE SELL`, `PE SELL`, `IRON CONDOR`, or `WAIT`.
- A directional sell requires RSI to turn away from the extreme, a strong nearby barrier,
  and two confirmed Big Player observations in the matching direction.
- Medium confirmation can suggest a protected Iron Condor only when both barriers are
  strong and the existing Iron Condor strike plan is available.
- The card displays protected strikes, barrier-based 3-minute-close invalidation,
  a ₹5,000 total hard-loss budget, and risk-budget-based maximum lots.
- The module is advisory and read-only. It does not vote in, modify, or override the
  Main AI / One-Brain decision.

## Safety

- MACD, EMA, news, FII/DII, PCR, and Bollinger Band do not add votes to this setup.
- Market-closed output is clearly marked `REFERENCE ONLY`.
- Missing protected strikes or a setup that does not fit within the loss budget returns
  `WAIT`.
