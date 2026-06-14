# ⚠️ VULCAN SENTINEL ABNORMAL ALERT — WARNING

- **Generated (autonomous):** 2026-06-14 08:10:11 UTC
- **Asset / parameter:** CC2-MO-01 / bearing_temp_C
- **Latest reading:** 62.3 degC (at 2026-06-09 22:00:00)
- **Limits:** warning 75.0 / critical 90.0 degC
- **Severity transition:** NORMAL → WARNING
- **Health score:** 82.9/100 (HEALTHY)

## Anomaly layers fired (Tier-1 evidence)
- `L2_ZSCORE` [WARNING] — latest deviates 3.5 sigma from baseline mean 59.08 degC
- `L3_CUSUM` [WARNING] — sustained drift from baseline detected, first signalled at 2026-06-05 22:00:00
- `L4_TREND` [INFO] — recent trend rising at 0.0181 degC/h

*Source: stored sensor readings only — the sentinel reads data, never creates it (C-07).*
