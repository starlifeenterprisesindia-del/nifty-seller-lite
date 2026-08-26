# Nifty Seller Lite v2.34.3 — Calculation Fix

## Corrected

- Option Flow now blends the same OI/premium/volume evidence across ready 1m, 3m
  and 5m windows. It no longer labels an active multi-window move as extreme range
  merely because the latest short snapshot is quiet.
- The final option-flow bias is derived after confidence calibration, so the label
  and displayed Bull/Bear/Range scores cannot disagree.
- Fresh news scoring uses only headlines inside the active freshness band. Older
  headlines stay visible as context but cannot turn fresh news risk HIGH.
- CE/PE BUY and SELL selection checks that a valid farther-OTM protection leg exists
  before ranking the main strike. Edge strikes can no longer win and then fail hedge
  construction when a valid protected pair exists nearby.
- Iron Condor receives a bounded penalty when core evidence or option flow is clearly
  directional, and an additional penalty when both agree in the same direction.

## Safety

- One-Brain remains the only strategy decision source.
- Big Player remains confirmation/contradiction evidence; it is not double-counted.
- Every displayed directional structure remains defined-risk and same-expiry.
- Auto Shadow Journal threshold remains 55 with maximum 5 paper entries per day.

## Verification

- Python compile check passed.
- Full automated suite passed: 226 tests.
- The supplied live option chain was checked: protected CE alternatives exist at
  24,350–24,550, so the selector no longer gets trapped on unhedgeable edge strikes.
