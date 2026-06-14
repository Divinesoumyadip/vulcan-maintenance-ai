# 🚨 VULCAN SENTINEL PREDICTIVE FAILURE ALERT — CRITICAL

- **Generated (autonomous):** 2026-06-14 08:10:11 UTC
- **Asset / parameter:** LF1-HYD-01 / oil_temp_C
- **Latest reading:** 79.3 degC (at 2026-06-11 02:00:00)
- **Limits:** warning 80.0 / critical 95.0 degC
- **Severity transition:** NORMAL → CRITICAL
- **Health score:** 36.3/100 (WARNING)

## Predictive evidence (v10 — fired BEFORE limit breach)
- **Model:** linear_regression_drift (VULCAN Section 7)
- **Projected time to critical limit:** 19.7 h (80% CI ['19.3 h', '20.0 h'])
- **Degradation rate:** 0.80201 degC/h over 12 readings (R² 1.0)
- **Caveat:** Linear extrapolation assumes the current degradation mechanism continues unchanged.

*No limit has been breached yet — this alert exists so the intervention can be PLANNED instead of forced.*

## Anomaly layers fired (Tier-1 evidence)
- `L2_ZSCORE` [WARNING] — latest deviates 7.6 sigma from baseline mean 48.75 degC
- `L3_CUSUM` [WARNING] — sustained drift from baseline detected, first signalled at 2026-06-10 10:00:00
- `L4_TREND` [INFO] — recent trend rising at 0.8057 degC/h

*Source: stored sensor readings only — the sentinel reads data, never creates it (C-07).*
