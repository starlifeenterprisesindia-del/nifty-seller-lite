# Architecture Lock — One Snapshot, One Brain Later

Current flow:

`DhanHQ APIs -> SnapshotService -> MarketSnapshot -> Streamlit UI`

Every screen section receives the same `MarketSnapshot`. UI components never fetch market data.

Future analysis modules will return evidence only. Only one future decision engine may produce CE SELL, PE SELL, IRON CONDOR or WAIT scores.
