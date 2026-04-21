# AGENTS.md

This document is the operational guide for coding agents working in this repository.

## 1) Project Identity (Current Reality)

- Project: **Quantum Trading Scalp**
- Purpose: full-stack quantitative trading system for **automated micro futures** (MNQ/MES)
- Current engine: **Scalp pipeline only**
- Legacy: **V3 fully removed**; do not reintroduce V3 dependencies, loops, or routing paths

### Critical known fix (must not regress)

- Date: 2026-04-13
- File: `backend/services/scalp_auto_trader.py`
- Bug: quality gate G-2 blocked all trades in Python 3.12 because:
  - `str(ScalpSignalQuality.MODERATE)` => `"ScalpSignalQuality.MODERATE"`
  - `_QUALITY_RANK` expected `"MODERATE"`
  - Lookup failed, returned fallback below WEAK
- Fix: `_normalize_quality()` must extract enum `.value` directly before ranking
- Also expected: auto-start on restart works when `scalp_config.auto_trade == true`

## 2) Stack / Ports / Runtime

- Frontend: React (CRA + CRACO), default dev port **5000** (`frontend/`)
- Backend: FastAPI, port **8000** (`backend/server.py`)
- Mongo dev: local `mongodb://localhost:27017`, DB `quantum_trading`
- Mongo prod: Atlas M0 via `MONGO_URL_ATLAS` (used by `start_production.sh` when present)
- Market data: DataBento Live tick feed (continuous front month):
  - `MNQ.v.0`
  - `MES.v.0`
- Broker execution: SignalStack webhook -> Tradovate bracket OCO

## 3) Required Environment Variables

- `DATABENTO_API_KEY` (**required**)
- `DASHBOARD_PASSCODE` (**required**)
- `MONGO_URL` (auto/default local)
- `DB_NAME` (auto/default `quantum_trading`)
- `REACT_APP_BACKEND_URL` (typically empty in dev; frontend proxy is used)

Frontend proxy behavior is defined in `frontend/craco.config.js` for `/api` and `/ws` -> `http://localhost:8000`.

## 4) Standard Local Runbook

### MongoDB

```bash
mkdir -p /home/runner/mongodb-data && exec mongod --dbpath /home/runner/mongodb-data --port 27017 --bind_ip 127.0.0.1
```

### Backend API

```bash
bash start_backend.sh
```

`start_backend.sh` waits for MongoDB then launches uvicorn on 8000.

### Frontend

```bash
cd frontend && PORT=5000 yarn start
```

## 5) Scalp Architecture (S1 -> S2 -> S3)

### S1: Entry Signal (FLOW / ZONES)

- FLOW: OFI imbalance + delta + tape speed
- ZONES: delta-zonal levels (support/resistance from CVD)
- Session gates (RTH/Globex) + SQS gate
- Output: `ScalpSignal` with direction/confidence/entry params

### S2: Confirmation

- Macro context (VIX term structure + GEX)
- Feed health gate (DQS >= 0.70)
- Regime filter (avoid counter-trend in strong regime)

### S3: Execution

- Bracket OCO execution via SignalStack/Tradovate
- Hold target: ~1 to 3 minutes
- `ScalpAutoTrader` controls paper/live and sizing

### Product constants

- MNQ point value: **$2.00/pt**
- MES point value: **$5.00/pt**
- Tick size: **0.25**

## 6) Background Loops (Scalp-only)

- `scalp_snapshot_loop` (30s): snapshot engine state for auto-tune
- `live_price_broadcaster` (1s): websocket live prices to frontend
- `feed_health_monitor` (15s): DataBento DQS monitoring
- `scalp_scheduler` (event-driven): walk-forward auto-tune scheduling
- `combined_scheduler` (event-driven, 60s poll): D1 x D2 analysis scheduling

## 7) Key Files to Know

- `backend/server.py` (main FastAPI app)
- `backend/services/scalp_engine.py` (signal pipeline + evaluate)
- `backend/services/scalp_auto_trader.py` (execution, sizing, open positions)
- `backend/services/scalp_snapshot_service.py` (auto-tune snapshots)
- `backend/services/live_data_service.py` (DataBento tick client)
- `backend/services/delta_zonal_service.py` (zones, OFI, z-score)
- `backend/services/feed_health.py` (DQS)
- `backend/services/macro_context_service.py` (VIX/GEX)
- `backend/routes/scalp.py` (scalp API routes)
- `backend/routes/fills.py` (trade journal routes)
- `backend/routes/auth.py` (OAuth/passcode auth)
- `frontend/src/components/ScalpDashboard.jsx`
- `frontend/src/components/ScalpStatusBar.jsx`
- `frontend/src/components/ScalpAutoTunePanel.jsx`
- `frontend/src/components/FillMonitor.jsx`

## 8) Execution and Flatten Semantics (Important)

### Live entry

- One webhook call with bracket fields (`stop_loss_price`, `take_profit_price`, optional trailing)
- Tradovate creates OCO child orders

### Pre-flight price guard

- Before sending order: compare order prices against live market
- If deviation > 12%: block locally with HTTP 422 and persist rejection to MongoDB

### Flatten behavior

- **EOD flatten**: transition into close window triggers `flatten_all_trades("EOD_FLATTEN")`
- **Blackout flatten**: transition `_was_in_blackout: false -> true` with open positions triggers `flatten_all_trades("BLACKOUT_FLATTEN")`
- On blackout end, trading resumes automatically

### Live flatten sequence

1. `cancel_all` per symbol (clear SL/TP bracket and orphans)
2. market `close` per symbol (only if qty > 0)
3. internal close `_close_trade(skip_broker=True)` (avoid duplicate broker calls)

### Individual close semantics

- SL/TP/manual close sends only `action_type: "close"`
- No explicit `cancel_all` for individual close (broker OCO handles bracket cleanup)

## 9) Sizing Modes

- `FIXED` (default): always use configured `mnq_quantity` / `mes_quantity`
- `RISK %`: dynamic qty at execution:
  - `qty = floor((account_size * risk_pct/100) / (stop_pts * point_value))`
  - `stop_pts` from `signal.s3_stop_loss_price` vs `signal.last_price`
  - bounded by `max_qty_risk_pct` and global gate G-5

Implementation area: `backend/services/scalp_auto_trader.py` (sizing branch around the documented section).

## 10) Known Data/Journaling Discrepancy Fixes

### A) Stale tick buffer pre-trade (critical)

- Fix in `_execute_trade()`: after registering `_open_trades`, drain `consume_period_range()` immediately and discard result
- Goal: position monitor sees only post-entry ticks; avoids historical-range trailing false activations

### B) SL alignment to broker tick behavior

- Use `align_sl_to_tick(sl_price, action, symbol)` in `scalp_pnl.py`
- SELL SL (above entry): floor to tick
- BUY SL (below entry): ceil to tick
- `tp_price` stays neutral `round_to_tick()`

### C) Residual discrepancy still expected

- Small residual (~1 tick slippage impact) remains without broker fill confirmation callback

## 11) ZONES / Regime Fixes and Toggles (Current)

- Fix A: block `SIGMA2_FADE_BUY` + `SIGMA2_FADE_SELL` in RTH open window (regime-based)
- Fix B: extended OFI slow bearish block for `SIGMA2_FADE_BUY` (hypothesis mode)
- Fix C: persist `ofi_slow_raw`, `ofi_fast_raw`, `cvd_trend`, `fix_a_shadow_zones` to signal log
- Fix D: tape speed bonus only when OFI slow is directionally aligned for fade
- Fix E: CVD-opposed fade penalty (`-0.5`) with visibility in score breakdown
- Item 3 structural filters:
  - gap_open
  - snapshots_in_regime
  - range_consumed v2
- D30 active gate (phase 2):
  - `|D30| > 20`: blocked
  - `10 < |D30| <= 20`: allow only if quality STRONG
  - `<= 10`: normal flow
- Item 4: `RTH_OPEN` as native regime
- Item 5: price vs RTH open bias (+/- scoring path)
- Item 6: regime CVD confirmation label (`CONFIRMED`/`CONTESTED`/`NEUTRAL`)

When editing these behaviors, preserve toggle-based rollback paths and update logs/persistence fields accordingly.

## 12) Track B (Pullback Observer) Readiness

Observer-only until readiness criteria are met:

- `RETURN` events >= 10
- Distinct calendar dates >= 2

Status endpoint: `/api/scalp/autotrader/status` (field `track_b_readiness`)

Readiness monitor runs automatically every 10 minutes.

## 13) Persistence Notes (Do Not Break)

Persisted and restart-safe (expected):

- Open positions (`scalp_trades`)
- Cooldowns and config state
- Track B readiness (via Mongo query)
- RTH open price restore (today ET)
- Session HOD/LOD restore (today ET snapshots)
- Circuit breaker session stats with ET date guards
- Welford z-score persistence (5-min + shutdown checkpoint)

Non-critical/safe default:

- Regime snapshot counts may default high after restart (safe for current filter logic)

## 14) Pending Post-Collection Analyses

After enough sample size (especially N >= 30 in relevant segments):

1. Validate/retune Fix B threshold (`FIX_B_OFI_SLOW_FADE_BUY_THRESH`)
2. Analyze Fix A shadow logs (BUY/SELL and 60-min window calibration)
3. Confirm whether unexpected `cvd_trend` values appear (vs warm-up artifacts)
4. Recheck Track B readiness progression
5. Evaluate global OFI slow fade threshold after Fix B evidence

Track B relational backlog (defer until sample criteria met):

- Archetype logic by zone type (avoid tautological classifications for non-EMA zones)
- Only then consider archetype-specific SL/TP or score adaptations

## 15) Agent Working Rules for This Repo

- Keep the system **Scalp-only**.
- Preserve the enum quality normalization behavior for G-2 quality gating.
- Do not silently remove telemetry fields used for diagnostics/backtests.
- Keep emergency toggles for new gating/scoring logic where practical.
- Prefer additive, observable changes: include reason tags in logs/score breakdowns.
- For any risk-related execution change, verify:
  - live/paper behavior
  - flatten semantics
  - Mongo persistence and restore behavior
  - API compatibility for frontend panels (`/api/system/status`, scalp status/trades routes)

---

If you need a quick orientation, start in:

1. `backend/services/scalp_engine.py`
2. `backend/services/scalp_auto_trader.py`
3. `backend/server.py`
4. `frontend/src/components/ScalpDashboard.jsx`
