# Architecture Lock — One Snapshot, One Final Brain Later

Current flow:

`DhanHQ APIs -> SnapshotService -> MarketSnapshot -> Analysis-only calculations -> Streamlit UI`

Rules:

1. Every screen section receives the same `MarketSnapshot`.
2. Analysis modules never fetch data.
3. EMA, MACD and RSI are evidence-only calculations.
4. There is no decision engine in this milestone.
5. A future single decision engine may produce CE SELL, PE SELL, IRON CONDOR or WAIT scores.
6. No secondary brain, duplicate classifier or hidden fallback decision is allowed.
7. Closed/stale market data is labelled reference-only and cannot be treated as live evidence.
