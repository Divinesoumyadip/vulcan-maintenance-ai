# ⚠️ VULCAN SENTINEL ABNORMAL ALERT — WARNING

- **Generated (autonomous):** 2026-06-14 08:10:11 UTC
- **Asset / parameter:** CC2-MO-01 / vibration_mm_s
- **Latest reading:** 7.22 mm/s (at 2026-06-09 22:00:00)
- **Limits:** warning 7.0 / critical 11.0 mm/s
- **Severity transition:** NORMAL → WARNING
- **Health score:** 33.1/100 (WARNING)

## Anomaly layers fired (Tier-1 evidence)
- `L1_THRESHOLD` [WARNING] — latest 7.22 mm/s >= warning limit 7.0 mm/s
- `L2_ZSCORE` [WARNING] — latest deviates 15.9 sigma from baseline mean 3.00 mm/s
- `L3_CUSUM` [WARNING] — sustained drift from baseline detected, first signalled at 2026-06-04 06:00:00
- `L4_TREND` [INFO] — recent trend rising at 0.0218 mm/s/h

*Source: stored sensor readings only — the sentinel reads data, never creates it (C-07).*
