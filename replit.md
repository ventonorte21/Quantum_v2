# Quantum Trading Scalp

Full-stack quantitative trading system for automated micro futures (MES/MNQ). Bi-mode Scalp Engine (FLOW + ZONES) with DataBento Live tick feed, 1–3 minute holds, bracket OCO execution via SignalStack/Tradovate.

**V3 legacy system fully removed.** Event loop belongs entirely to the Scalp pipeline.

**Critical bug fixed (2026-04-13):** G-2 quality filter in `scalp_auto_trader.py` was blocking ALL trades. Root cause: Python 3.12 `str(ScalpSignalQuality.MODERATE)` returns `"ScalpSignalQuality.MODERATE"` (not `"MODERATE"`), causing `_QUALITY_RANK` dict lookup to return -1 (below WEAK). Fix: `_normalize_quality()` extracts `.value` from enum directly. Auto-start on restart now works via `auto_trade: True` in `scalp_config`.

---

## Architecture

- **Frontend**: React (CRA + CRACO) on port 5000 (`frontend/`)
- **Backend**: FastAPI (Python) on port 8000 (`backend/server.py`)
- **Database (dev)**: MongoDB local on port 27017 — database name: `quantum_trading`
- **Database (prod)**: MongoDB Atlas M0 (`quantumscalp.xj16qml.mongodb.net`) — secret `MONGO_URL_ATLAS`; `start_production.sh` auto-selects Atlas when the secret is set
- **Data feed**: DataBento Live (`MNQ.v.0`, `MES.v.0` — continuous front month)
- **Order execution**: SignalStack → Tradovate webhook (bracket OCO)

---

## Workflows

1. **MongoDB** — `mkdir -p /home/runner/mongodb-data && exec mongod --dbpath /home/runner/mongodb-data --port 27017 --bind_ip 127.0.0.1`
2. **Backend API** — `bash start_backend.sh` (waits for MongoDB, then starts uvicorn on port 8000)
3. **Start application** — `cd frontend && PORT=5000 yarn start` (webview on port 5000)

---

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABENTO_API_KEY` | ✅ Yes | Real-time tick data (MNQ/MES) |
| `DASHBOARD_PASSCODE` | ✅ Yes | UI authentication |
| `MONGO_URL` | auto | `mongodb://localhost:27017` |
| `DB_NAME` | auto | `quantum_trading` |
| `REACT_APP_BACKEND_URL` | auto | empty — uses webpack proxy |

Frontend proxies `/api` and `/ws` to `http://localhost:8000` via `frontend/craco.config.js`.

---

## Scalp Engine Architecture (S1 → S2 → S3)

### S1 — Entry Signal (FLOW / ZONES)
- **FLOW mode**: OFI imbalance + delta + tape speed
- **ZONES**: Delta zonal levels (support/resistance from CVD)
- RTH/Globex session gates + SQS (Signal Quality Score) gate
- Outputs `ScalpSignal` with direction, confidence, entry params

### S2 — Confirmation
- Macro context validation (VIX term structure, GEX)
- Feed health DQS gate (≥0.70 required)
- Regime filter (no counter-trend in strong regime)

### S3 — Execution
- Bracket OCO via SignalStack webhook (1–3 min target hold)
- ScalpAutoTrader manages paper/live toggle, position sizing
- `MNQ=$2.00/pt | MES=$5.00/pt | tick_size=0.25`

### Flatten automático
- **EOD flatten** (`eod_flatten_enabled`): detecta transição window-close → chama `flatten_all_trades("EOD_FLATTEN")`.
- **Blackout flatten**: detecta transição `_was_in_blackout=False → True` com posições abertas → chama `flatten_all_trades("BLACKOUT_FLATTEN")`. Trading retoma automaticamente quando o blackout termina.
- **Sequência de flatten para live**: (1) `cancel_all` por símbolo ao Tradovate via SignalStack (cancela SL/TP bracket e ordens órfãs); (2) `close` por símbolo a mercado (apenas se qty > 0); (3) fecha estado interno (`_close_trade(skip_broker=True)`) sem duplicar chamadas ao broker.
- **Close individual** (SL/TP, manual): envia apenas `action_type: "close"` — broker OCO cancela bracket automaticamente. Sem `cancel_all`.

---

## Background Loops (Scalp-only, V3 removed)

| Loop | Interval | Purpose |
|---|---|---|
| `scalp_snapshot_loop` | 30s | Records ScalpEngine state to MongoDB for Auto-Tune |
| `live_price_broadcaster` | 1s | Pushes live prices via WebSocket to frontend |
| `feed_health_monitor` | 15s | DQS checks on DataBento feed |
| `scalp_scheduler` | event-driven | Walk-forward Auto-Tune scheduler |
| `combined_scheduler` | event-driven | Combined D1×D2 analysis scheduler (60s poll) |

---

## Key Files

| File | Purpose |
|---|---|
| `backend/server.py` | Main FastAPI app (~4,900 lines) — Scalp routes + background loops |
| `backend/services/scalp_engine.py` | Full Scalp pipeline: ScalpSignal, ScalpEngine, evaluate() |
| `backend/services/scalp_snapshot_service.py` | Parallel snapshot recording for Auto-Tune |
| `backend/services/scalp_auto_trader.py` | Execution loop: paper/live, position tracking |
| `backend/services/macro_context_service.py` | Standalone VIX/GEX service (no V3 deps) |
| `backend/services/live_data_service.py` | DataBento Live client — tick buffers for MNQ/MES |
| `backend/services/delta_zonal_service.py` | Delta zone analysis + OFI + Welford Z-score |
| `backend/services/feed_health.py` | DataBento DQS monitor |
| `backend/services/overnight_inventory.py` | Overnight gap/inventory for Scalp context |
| `backend/services/scalp_diagnostics_service.py` | D1 analysis: fire rate / block reasons / OFI Slow impact (from snapshots) |
| `backend/services/scalp_combined_service.py` | Combined D1×D2 analysis: crosses diagnostics + calibration → param suggestions + schedule |
| `backend/routes/scalp.py` | All scalp routes: signal, execute, trades, zones |
| `backend/routes/fills.py` | Trade journal (Fill Monitor) API routes |
| `backend/routes/auth.py` | Google OAuth + passcode auth |
| `frontend/src/App.js` | Main app — header nav + overlay panels (Scalp-only, V3 removed) |
| `frontend/src/components/ScalpDashboard.jsx` | Main Scalp UI with ScalpStatusBar |
| `frontend/src/components/ScalpStatusBar.jsx` | Horizontal status strip, polls /api/system/status every 15s |
| `frontend/src/components/ScalpAutoTunePanel.jsx` | Walk-forward replay / Auto-Tune UI |
| `frontend/src/components/FillMonitor.jsx` | Trade journal UI |
| `start_backend.sh` | Backend startup (waits for MongoDB, then uvicorn) |

---

## SignalStack / Tradovate Integration

All live entries use a **single webhook call** with native Tradovate bracket fields.
Tradovate creates OCO child orders — when one side fills, the other is cancelled automatically.

### Bracket payload:
```json
{
  "symbol": "MNQM6",
  "action": "buy",
  "quantity": 1,
  "class": "future",
  "stop_loss_price": 19500.0,
  "take_profit_price": 19560.0,
  "trail_trigger": 20.0,
  "trail_stop": 10.0
}
```

### Pre-flight price guard:
Before any order, validates prices against live market. Deviation >12% → blocked locally (422), saved to MongoDB.

---

## Authentication

- Google OAuth (via `/api/auth/google`) — whitelist-based
- Passcode fallback (`DASHBOARD_PASSCODE` env var)
- Session tokens stored in `localStorage` + verified server-side
- `ProtectedRoute` wraps all authenticated routes

---

## Frontend Navigation

The main Dashboard has:
- **Header nav**: Auto Trade | SignalStack | Auto Tune | Journal | Scalp
- **Scalp panel** (`activePanel === 'scalp'`): Full-screen `ScalpDashboard`
- **Sub-panels inside Scalp**: Ordens Manuais (SignalStack) | Auto Tune | Journal
- **Auto Trading panel**: `AutoTradingPanel` (legacy config UI, execution delegates to ScalpAutoTrader)
- **Fill Monitor**: Trade journal overlay

---

## AutoTrader Sizing Modes

Two modes selectable in the AutoTrader panel (Símbolos & Loop card):

- **FIXO** (default): always sends `mnq_quantity` / `mes_quantity` contracts from config. `account_size` and `risk_per_trade_pct` are reference-only.
- **RISK %**: dynamically calculates qty at execution time: `qty = floor((account_size × risk_pct/100) / (stop_pts × point_value))`. Uses `signal.s3_stop_loss_price` and `signal.last_price` for stop distance. Capped by `max_qty_risk_pct` (safety cap) and G-5 (global contracts ceiling).
  - MNQ point value: $2.00/pt; MES: $5.00/pt
  - Backend: `scalp_auto_trader.py` around line 586 (`sizing_mode` branch)

---

## Track B — Critério de Activação Live

Track B (Pullback Observer) corre em modo observer-only. Activação live requer:

| Critério | Threshold | Estado actual |
|---|---|---|
| Eventos RETURN em `pb_state_log` | ≥ 10 | 1 (2026-04-15) |
| Datas de calendário distintas | ≥ 2 | 1 |

Quando atingido: o backend loga `★★★ TRACK B READINESS ATINGIDO ★★★` (WARNING) e o campo `track_b_readiness.readiness_met=true` aparece em `/api/scalp/autotrader/status`. O monitor corre a cada 10 minutos automaticamente.

**Parâmetros actuais Track B:** `_PB_SPEED_MIN=0.8`, `_PB_OFI_MIN=0.3`, `_PB_RETURN_PCT=0.5`, `_PB_TIMEOUT_SEC=300`, `_PB_MAX_STATES=3`, `_PB_BROKEN_CYCLES=3`

**Nota:** Track B é independente do path DIRECT. O TOUCH pode ocorrer com qualidade baixa ou regime adverso — o critério de entrada é avaliado exclusivamente no momento do RETURN.

---

## Journal vs Broker PnL Discrepancies — Causas e Fixes

### 1. Stale tick buffer pré-trade (bug crítico — 2026-04-16)

**Causa:** `SymbolBuffer._period_low/_period_high` acumula ticks continuamente via `add_trade()`. O método `consume_period_range()` só é chamado pelo monitor de posições — que só corre quando há trades abertos. Se não houver trades durante horas (ex: das 19:02 às 22:24), o buffer acumula 3+ horas de extremos históricos.

Quando um novo trade abre e o monitor faz a **primeira** chamada `consume_period_range()`, recebe o range completo de horas anteriores. Isso produz:
- `period_high` histórico → activa trailing (move SL virtualmente para nível errado)
- Após activação falsa, o crash real fecha o trade ao SL virtualizado errado
- Journal regista um WIN (+$20) quando o real foi um LOSS (-$74)

**Fix:** Em `_execute_trade()`, imediatamente após registar o trade em `_open_trades`, fazer **drain** do buffer com `consume_period_range()` (resultado descartado). O monitor só vê ticks pós-abertura. Log: `AutoTrader buffer-drain {symbol}: ticks pré-trade descartados (stale low=... high=... entry=...)`.

### 2. SL threshold vs broker stop alignment

**Causa:** O sistema calcula o SL a partir do `signal.last_price` (preço do sinal). O Tradovate/PickMyTrade aplica o mesmo offset em pontos ao **fill price** (que pode diferir 1 tick do sinal) e arredonda para tick. Isso cria um gap de 0.25pt entre o SL interno e o stop real no broker.

**Fix:** `align_sl_to_tick(sl_price, action, symbol)` em `scalp_pnl.py`:
- `SELL` (SL acima entrada): arredonda para **baixo** (`math.floor`) → ex: 26359.68 → 26359.50
- `BUY` (SL abaixo entrada): arredonda para **cima** (`math.ceil`) → ex: 26443.32 → 26443.50

Aplicado em `_execute_trade()` ao `sl_price` antes de gravar; `tp_price` usa `round_to_tick()` (neutro).

### 3. Residual discrepância de PnL

Após os dois fixes acima, subsiste uma **discrepância residual** de ~$4 por trade (ex: sistema -$70, broker -$74) devida ao slippage de 1 tick na entrada:
- Sistema usa `signal.last_price` como entry (ex: 26446.75)
- Broker fill real: 26447.00 (+0.25pt = +$5/contrato × 10 = +$5 de diferença na entrada)
- PnL sistema: (26443.25 - 26446.75) × 10 × $2 = -$70
- PnL broker: (26443.25 - 26447.00) × 10 × $2 = -$74 (aprox, baseado em fill avg)

Esta discrepância residual só é eliminável com confirmação de fill do broker (não disponível actualmente via PMT/Tradovate webhook).

---

## ZONES Engine Fixes (2026-04-16)

### Fix A — Block SIGMA2_FADE_BUY + SIGMA2_FADE_SELL em RTH_OPEN ✅ IMPLEMENTADO

**Evidência (BUY):** 3/3 STOP_HIT em RTH_OPEN (17, 24, 49 min após open) → 0% WR, −$1025.  
**Argumento SELL:** Estrutural simétrico — price discovery e OFI incoerente afectam igualmente a fade de alta (+2σ). Sem evidência empírica directa de STOP_HIT em SELL, por isso shadow log cobre o lado SELL para validação retrospectiva.  
**Root cause:** Volatilidade de abertura produz wide ATR e OFI incoerente; absorção local no 2σ é indistinguível de distribuição durante price discovery.  
**Fix:** Filtro em `evaluate_zones()` após `in_zone` ser computado: remove `SIGMA2_FADE_BUY` e `SIGMA2_FADE_SELL` quando `0 ≤ on_session_minutes < 60` (RTH_OPEN = 09:30–10:29 ET).  
**Toggles independentes:**  
- `FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_BUY = False` → reverte só o lado BUY  
- `FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_SELL = False` → reverte só o lado SELL  
**Shadow logging:** Toda zona removida (BUY ou SELL) registada em `signal_log` com `gate_outcome="FA_SHADOW"`, `fix_a_shadow_zones`, `ofi_slow_raw`, `ofi_fast_raw`.  
**Critério de revisão:** ≥3 sessões RTH_OPEN + comparar shadow log PnL hipotético vs real por direcção.

### Fix B — F1-2 estendido: OFI slow bearish → bloqueia SIGMA2_FADE_BUY ✅ IMPLEMENTADO (hypothesis)

**Hypothesis:** Quando `ofi_slow < −0.30`, fluxo vendedor de fundo prevalece sobre absorção local; a rejeição é distribuição disfarçada, não reversão genuína.  
**Fix:** Em `evaluate_zone_entry()`, AND gate adicional para `SIGMA2_FADE_BUY`: se `ofi_slow < -FIX_B_OFI_SLOW_FADE_BUY_THRESH (0.30)` → BLOCKED com razão `"F1-2-EXT [hypothesis=True]"`.  
**HYPOTHESIS = True:** threshold −0.30 não validado (Fix C não estava activo até hoje). Rever após N≥30 trades com `ofi_slow_raw` registado.  
**Localização:** `scalp_zones.py`, função `evaluate_zone_entry()`, antes do bloco OFI fast scoring.

### Fix C — ofi_slow_raw + cvd_trend no signal_log ✅ IMPLEMENTADO

**Problema:** `scalp_signal_log` não registava o valor bruto de OFI slow (150s window), impossibilitando a validação retrospectiva do threshold Fix B.  
**Fix:** Em `_log_signal_event()` em `scalp_auto_trader.py`, adicionados campos ao doc:
- `ofi_slow_raw`: valor bruto `signal.ofi_slow` (janela 150s)
- `ofi_fast_raw`: valor bruto `signal.ofi_fast` (janela ~10s)
- `cvd_trend`: tendência CVD para diagnóstico do Bug 1
- `fix_a_shadow_zones`: lista de SIGMA2_FADE_BUY removidas por Fix A no ciclo

### Fix D — Tape Speed bónus condicional em zonas de fade ✅ IMPLEMENTADO

**Problema:** O bónus de +0.3 pts por "tape a secar" (`speed_ratio ≤ 0.9`) era aplicado incondicionalmente em qualquer zona de fade. Tape lento com OFI slow contra a direcção do trade não confirma reversão — pode ser pausa antes de continuation.  
**Correcção:** Bónus aplicado apenas quando OFI slow está alinhado:
- LONG fades (SIGMA2_FADE_BUY, etc.): bónus se `ofi_slow >= 0` (não bearish)
- SHORT fades (SIGMA2_FADE_SELL, etc.): bónus se `ofi_slow <= 0` (não bullish)  

**Toggle:** `FIX_D_TAPE_SPEED_CONDITIONAL = True` em `scalp_zones.py` — `False` restaura comportamento original.  
**Visibilidade:** Quando bónus suspenso, a razão é registada em `score_breakdown.reasons` com OFI slow, direcção e ratio actual.  
**Efeito esperado:** Reduz inflação de score em entradas de fade com fluxo de ordem médio-prazo adverso. Não é novo critério — é correcção de comportamento existente.

### Fix E — CVD opõe-se à fade → penalidade −0.5 pts ✅ IMPLEMENTADO

**Problema:** CVD FALLING durante uma fade LONG (comprar em -2σ) indica pressão vendedora de médio prazo contra a entrada. CVD RISING durante uma fade SHORT indica pressão compradora de médio prazo contra a entrada. Em ambos os casos, o CVD não confirma a reversão — o bónus existente (+0.5 quando alinhado) não capturava o caso adverso.  
**Fix:** Penalidade simétrica em zonas de fade quando CVD se opõe:
- `zone.direction == "LONG"` + `cvd_trend == "FALLING"` → −0.5 pts
- `zone.direction == "SHORT"` + `cvd_trend == "RISING"` → −0.5 pts

**Intervalo CVD resultante por tipo de zona:**
- Fade: [−0.5, +0.5] — assimetria total de 1.0 pt
- Momentum/Breakout: [0, +0.5] — sem penalidade (CVD adverso já é capturado por OFI slow)

**Toggle:** `FIX_E_CVD_FADE_PENALTY_ENABLED = True`, `FIX_E_CVD_FADE_PENALTY_PTS = 0.5`  
**Visibilidade:** Razão registada em `score_breakdown.reasons` com cvd_trend e direcção.  
**Nota:** CVD `reasons.append` adicionado também ao bónus de alinhamento existente (+0.5) para paridade de visibilidade no breakdown.

### Item 3 — gap_open, snapshots_in_regime, range_consumed ✅ IMPLEMENTADO

Três filtros de contexto estrutural adicionados em `evaluate_zones()` após o bloco Fix A, com toggles de emergência individuais:

**Fix 3a — gap_open:** `abs(rth_open_price − d1_poc) > 0.5×ATR` durante `RTH_OPEN` → suprime `SIGMA2_FADE_*` e `EMA_PULLBACK_*`. Em dias de gap (abertura longe do POC D-1), o price discovery é violento e fades são estruturalmente arriscadas.  
**Fix 3b — snapshots_in_regime:** Se `snapshots_in_regime < 3` (primeiras 3 avaliações num regime novo) → suprime todas as fades. Regime não confirmado = incerteza alta.  
**Fix 3c — range_consumed v2:** `(session_HOD − session_LOD) / (D1_HIGH − D1_LOW) > 1.5` → suprime todas as fades. Compara range de hoje com range RTH do dia anterior — escalas equivalentes.  
  *Redesign 2026-04-16:* versão original usava ATR_M1 como denominador (escalas incompatíveis → bloqueava 100% dos trades). Substituído por `D1_HIGH−D1_LOW` (já existente no pipeline, zero nova infraestrutura). Threshold ajustado de 1.8 para 1.5×. Exemplo: hoje 284/394=0.72× → não activa; dia excepcional 700/394=1.78× → activa.  
  `evaluate_zones()` recebe `d1_high`/`d1_low` como parâmetros adicionais; `scalp_engine.py` passa `signal.d1_high`/`signal.d1_low`.

**Tracking em ScalpEngine:** `_session_hod`, `_session_lod` (por símbolo+data), `_regime_snapshot_counts` (por símbolo).  
**Toggles:** `FIX_3_GAP_OPEN_ENABLED`, `FIX_3_SNAPSHOTS_ENABLED`, `FIX_3_RANGE_CONSUMED_ENABLED` (todos `True`).

---

### D30 — Gate activo de deslocamento de preço em 30 minutos ✅ ACTIVO (Fase 2)

**Evidência empírica:** Simulação com 57 trades Apr 14–17:
- `|D30| > 20 pts` → BLOCKED: WR=33%, EV=−78.94 pts/semana
- `|D30| 10–20 pts` → RISK:    WR=43%, EV=−11.00 pts
- `|D30| ≤ 10 pts`  → OK:      WR=56%, EV=+3.88 pts
- Sem filtro: semana −44.32 pts. Com filtro: +34.62 pts.

**Fase 2 (activa desde Apr 17 2026) — gate hard antes de evaluate_zones:**

```
|D30| > 20 pts  → D30_BLOCKED: trade rejeitado antes de avaliar zonas
|D30| 10–20 pts → D30_RISK:    sinal bloqueado se zone_quality ≠ STRONG
|D30| ≤ 10 pts  → OK:          fluxo normal
D30 = None      → sem gate (buffer insuficiente / gap Globex)
```

`signal.d30_state` persistido no MongoDB (`disp_30m` + `d30_state`) para comparação com/sem gate.

**Ficheiros:** `scalp_engine.py` — helper `_compute_disp_30m()`, campos `ScalpSignal.disp_30m` / `ScalpSignal.d30_state`, gate BLOCKED (antes de `evaluate_zones`), gate RISK (após `zones_result`). `scalp_auto_trader.py` — campos `disp_30m` / `d30_state` no documento MongoDB.

**Bug evitado:** usa aritmética directa em epoch-seconds (`abs(bar["ts"] - target_ts)`), não `timedelta.seconds`.

---

### Item 4 — RTH_OPEN como regime nativo S1 ✅ IMPLEMENTADO

**Refactorização:** `ScalpDayRegime.RTH_OPEN` adicionado ao enum. Em `evaluate_zones()`, após `apply_regime_hysteresis()`, override via tempo: `0 ≤ session_min < 60 → regime = RTH_OPEN`. Bypass correcto — regime baseado em tempo não deve ser debounced.

**Consequências:**  
- `signal.day_regime` reporta `"RTH_OPEN"` durante os primeiros 60 min → dashboard e logs mostram o regime real  
- Fix A refactorizado: condição mudou de `_fix_a_in_window = on_session_minutes < 60` para `regime == RTH_OPEN` — semântica idêntica, arquitectura limpa  
- Fix 3a (gap_open) também usa `regime == RTH_OPEN`  
- `evaluate_zones()` aceita `snapshots_in_regime`, `session_hod`, `session_lod` como parâmetros novos

---

### Item 5 — Price vs RTH Open como bias intraday ✅ IMPLEMENTADO

**Lógica:** `rth_open_price` injectado nos `levels` (em `scalp_engine.py` antes de `evaluate_zones()`). Threshold: `abs(price − open) > 0.10×ATR`.  
- `price > open + threshold` → `price_vs_rth_open = "ABOVE"`, `regime_bias = "BULLISH"`  
- `price < open − threshold` → `price_vs_rth_open = "BELOW"`, `regime_bias = "BEARISH"`

**Efeito no scoring:** Bónus de +0.3 pts em fades alinhadas com o bias:  
- fade BUY + bias BULLISH: mean-reversion valid (compra abaixo do open RTH)  
- fade SELL + bias BEARISH: mean-reversion valid (vende acima do open RTH)  
Sem penalidade por oposição — conservador até N≥30 dados.

**Toggles:**
- `FIX_5_RTH_BIAS_ENABLED = True`, `FIX_5_RTH_BIAS_SCORE = +0.3` (bónus alinhado)
- `FIX_5_RTH_BIAS_PENALTY_ENABLED = True`, `FIX_5_RTH_BIAS_PENALTY = -0.3` (penalidade oposto — simétrico)
- `FIX_5_RTH_BIAS_ATR_THRESH = 0.50` (corrigido de 0.10)

**Efeito total:** swing ±0.6 pts entre fade alinhada e oposta. Fade SELL quando bias=BULLISH perde 0.3 pts; fade BUY quando bias=BEARISH perde 0.3 pts. hypothesis:True — calibrar com N≥30.  
**Campos novos:** `signal.price_vs_rth_open`, `signal.regime_bias` (em `signal.to_dict()["zones"]`)  
**Persistência:** `scalp_snapshots.zones.price_vs_rth_open` + `zones.regime_bias` + `levels.rth_open_price` ✅ | `scalp_signal_log.price_vs_rth_open` + `signal_log.regime_bias` ✅

---

### Item 6 — CVD como confirmação de regime S1 ✅ IMPLEMENTADO

**Lógica:** Após detecção de regime, compara `cvd_trend` vs direcção do regime:  
- `EXPANSION_BULL` + `RISING` → `regime_cvd_conf = "CONFIRMED"`  
- `EXPANSION_BULL` + `FALLING` → `regime_cvd_conf = "CONTESTED"`  
- `EXPANSION_BEAR` + `FALLING` → `regime_cvd_conf = "CONFIRMED"`  
- `EXPANSION_BEAR` + `RISING` → `regime_cvd_conf = "CONTESTED"`  
- `ROTATION`, `RTH_OPEN`, `BREAKOUT`, `UNDEFINED` → `"NEUTRAL"`

**Informativo apenas:** sem gate hard — aguarda N≥30 por categoria para calibrar impacto.  
**Toggle:** `FIX_6_CVD_REGIME_CONF_ENABLED = True`  
**Campo novo:** `signal.regime_cvd_conf` (em `signal.to_dict()["zones"]`)  
**Persistência:** `scalp_snapshots.zones.regime_cvd_conf` ✅ | `scalp_signal_log.regime_cvd_conf` ✅

---

### Bug 1 — cvd_trend="?" Diagnóstico

**Análise:** `signal.cvd_trend = live_data.get("cvd_trend", "NEUTRAL")` é atribuído na linha 975 de `scalp_engine.py`. `SymbolBuffer` nunca emite "?". O "?" observado em scalp_trades é provavelmente artefacto de sessões de warm-up (antes do primeiro `calculate_indicators()`), onde `SymbolBuffer.cvd_trend` é ainda "NEUTRAL" e não "RISING"/"FALLING".  
**Monitoring:** Fix C adiciona `cvd_trend` ao signal_log — próximas N sessões confirmarão se o valor é sempre NEUTRAL/RISING/FALLING ou se existe outro caminho para "?".

### Bug 2 — pb_state_log from_state=None/to_state=None

**Análise:** Os documentos `pb_state_log` usam `state_from`/`state_to` (não `from_state`/`to_state`). A observação de "None" em 51 entradas usou os nomes de campo errados. Os dados reais estão correctos. Não é um bug de código.

### Bug 3 — Track B readiness reseta para 0

**Análise:** `_check_track_b_readiness()` lê do MongoDB (`count_documents({"event": "RETURN"})`), não da memória. Não zera em restart. Leitura actual: 1/10 RETURN events, 1/2 datas distintas. Sem fix necessário — o contador é persistente via MongoDB.

---

## Action Items Pós-Coleta (Fix A + Fix C) — PENDENTE

**Critério de activação: ≥3 sessões RTH completas (segunda a sexta) com Fix A + Fix C activos.**  
Verificar progresso contando entradas em `scalp_signal_log` com `ofi_slow_raw` não-nulo e `zone_type = "SIGMA2_FADE_BUY"`.

| # | Item | Como executar | Status |
|---|---|---|---|
| 1 | **Validar Fix B** — confirmar/ajustar threshold OFI slow −0.30 | Consultar `scalp_signal_log` onde `zone_type=SIGMA2_FADE_BUY` e `ofi_slow_raw` presente. Ver distribuição — se maioria > −0.30, ajustar threshold. Mudar `FIX_B_HYPOTHESIS = False` e `FIX_B_OFI_SLOW_FADE_BUY_THRESH` em `scalp_zones.py` quando N≥30. | ⏳ Aguarda dados |
| 2 | **Avaliar shadow log Fix A (BUY + SELL)** — decidir se 60 min é certo e se SELL deve manter-se bloqueado | Consultar `scalp_signal_log` onde `gate_outcome = "FA_SHADOW"`. Separar por `zone_type`: SIGMA2_FADE_BUY e SIGMA2_FADE_SELL. Ver minuto de sessão e direcção do mercado após cada evento. Se SELL entre 0–59 min teria ganho consistentemente → `FIX_A_RTH_OPEN_BLOCK_SIGMA2_FADE_SELL = False`. Se entradas BUY entre 30–59 min teriam ganho → reduzir `FIX_A_RTH_OPEN_DURATION_MIN` de 60 para 30. | ⏳ Aguarda dados |
| 3 | **Confirmar Bug 1 (cvd_trend)** — verificar se aparece valor inesperado | Consultar `scalp_signal_log` com campo `cvd_trend`. Se valor for sempre NEUTRAL/RISING/FALLING → bug inexistente. Se aparecer "?" ou null → rastrear hora do dia e confirmar warm-up como causa. | ⏳ Aguarda dados |
| 4 | **Track B readiness** — verificar se contador avançou | Verificar `/api/scalp/autotrader/status` campo `track_b_readiness`. Critério: 10 RETURN events + 2 datas distintas. Sem acção até atingido. | ⏳ 1/10 events |
| 5 | **Calibração global OFI slow fade (0.55 → ?)** — avaliar se threshold global está correcto | Após Fix B validado, usar dados de `ofi_slow_raw` de todas as fade zones para ver se 0.55 é tolerante demais. Ajustar `OFI_SLOW_BLOCK_FADE` em `scalp_zones.py` apenas se padrão claro. | ⏳ Bloqueado por item 1 |

---

## Auditoria de Persistência (2026-04-17)

Auditoria sistemática de todo o estado em memória que deve sobreviver a restarts.

| Estado | Localização | Persiste? | Impacto |
|---|---|---|---|
| ATR cache | `scalp_engine._atr_cache` | ✅ Snapshot → warm-start | — |
| VWAP / VP | caches | ✅ Snapshot → warm-start | — |
| Posições abertas | `scalp_auto_trader._open_trades` | ✅ MongoDB `scalp_trades` | — |
| Cooldown por símbolo | `_last_trade_ts` | ✅ `scalp_trader_state` | — |
| ScalpConfig / thresholds | session_params | ✅ MongoDB → restore | — |
| Track B readiness | `_track_b_readiness` | ✅ `pb_state_log` query | — |
| OFI/CVD | tick buffer rolling | ✅ Recalculado em <30s | — |
| Welford Z-Scores | `delta_zonal_service` | ✅ **CORRIGIDO 17/04** (5 min + shutdown) | — |
| **RTH open price** | `_rth_open_prices` | ✅ **CORRIGIDO 17/04** (restore de snapshot de hoje ET) | Fix 3a + Fix 5 |
| **Session HOD/LOD** | `_session_hod/_lod` | ✅ **CORRIGIDO 17/04** (max/min last_price dos snapshots de hoje) | Fix 3c |
| **Circuit breaker G-3/G-4** | `_session_stats` | ✅ **CORRIGIDO 17/04** (persist após cada trade + restore por data ET) | Segurança diária |
| Regime snapshot counts | `_regime_snapshot_counts` | ❌ Default 999 no restart (safe — Fix 3b passa) | Mínimo |

**Notas de implementação:**
- RTH open: filtrado por `recorded_at >= midnight ET hoje` — evita importar open de ontem durante Globex
- HOD/LOD: computed de todos os snapshots de hoje; `_ET` date garante alinhamento com chave `SYMBOL:YYYY-MM-DD`
- Circuit breaker: gravado em `scalp_trader_state._id="session_stats"` com campo `date_et`; restore só actua se `date_et == hoje ET` (reset diário automático continua a funcionar)

---

## Performance Backlog (P0)

- Master loop centralizing evaluate() calls (reduce redundant computations)
- WebSocket push for S3 signals (eliminate polling latency)
- `_get_trades()` without deque copy (memory optimization)
