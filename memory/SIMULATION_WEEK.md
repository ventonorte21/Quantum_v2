# Simulação Semanal Completa — Trading System V3
## Semana: 6–10 Abril 2026 (Segunda a Sexta)

> **Nota**: Esta simulação usa valores realistas de mercado para demonstrar todos os
> ciclos, análises e decisões do sistema. Cada regime é exercitado pelo menos uma vez.

---

## Infraestrutura de Ciclos (Background Loops)

O sistema mantém **3 loops** simultâneos em background após o startup:

| Loop | Intervalo | Função | Quando roda |
|------|-----------|--------|-------------|
| **Snapshot Loop** | 60s | Grava estado V3 completo (N1+N2+N3) para MNQ e MES no MongoDB | Apenas em dias de pregão (`is_trading_day=True`) |
| **EOD Monitor Loop** | 30s | Verifica EOD flatten, news blackout, horário de trading | Sempre (mas só age se `config.enabled=True`) |
| **Frontend Polling** | Manual/UI | Dispara `evaluate_all_symbols` ou `execute_v3_signal` | Quando o usuário (ou scheduler) requisita |

### Horários de referência (DST ativo, ET = UTC-4)

| Marco | Horário ET | Horário UTC |
|-------|-----------|-------------|
| CME Futures Open (domingo) | 18:00 (dom) | 22:00 (dom) |
| Pre-market start | 04:00 | 08:00 |
| NYSE Cash Open | 09:30 | 13:30 |
| NYSE Cash Close | 16:00 | 20:00 |
| Auto Trading End (30min antes) | 15:30 | 19:30 |
| CME Daily Maintenance | 17:00–18:00 | 21:00–22:00 |

---

## Fluxo de uma Avaliação V3 (cada chamada a `v3_engine.evaluate(symbol)`)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     v3_engine.evaluate("MNQ")                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. FETCH: analyze_market(symbol, "1H")                            │
│     → DataBento hist/live, VIX (Yahoo), Treasury, Gamma (yfinance) │
│     → Term Structure (VX1/VX2), OFI, VWAP, Volume Profile          │
│                                                                     │
│  2. N1 — MACRO SCORE (0–13 pts)                                    │
│     ├── VIX level         (0–3 pts)                                │
│     ├── Yield Curve 2s10s (0–4 pts)                                │
│     ├── Gamma/ZGL         (0–3 pts)                                │
│     └── Term Structure    (0–3 pts)                                │
│     → detect_regime(score, ts_ratio)                               │
│     → Output: regime, tactic, lot_pct, direction, target_symbol    │
│                                                                     │
│  3. GAMMA D-1 CONVERSION                                           │
│     → GammaRatioService: ETF Walls → Futures price (D-1 ratio)    │
│     → Put Wall, Call Wall, ZGL em preço de futuro                  │
│                                                                     │
│  4. N2 — FILTROS CONFIRMATÓRIOS                                    │
│     → evaluate_filters(regime, price, VA, VWAP bands, Walls)       │
│     → Output: passed=True/False, reason                            │
│                                                                     │
│  5. DELTA ZONAL ANCORADO                                           │
│     ├── N2-DZ: Fluxo estrutural em VWAP/POC/VAH/VAL              │
│     │   → Z-Score por nível, net_delta, status                     │
│     └── N3-DZ: Absorção em extremos (Walls, ±3σ)                  │
│         → Absorções ativas (BUY/SELL/CONTESTED)                    │
│                                                                     │
│  6. N3 — SINAL DE EXECUÇÃO                                        │
│     → generate_execution_signal(regime, DZ, OFI, levels)           │
│     → Output: action (BUY/SELL/WAIT), entry, SL, TP, reason       │
│                                                                     │
│  7. V3 STATUS                                                      │
│     → ACTIVE_SIGNAL (N3≠WAIT + N2 passed)                         │
│     → FILTERS_PASSED (N2 ok, N3=WAIT)                             │
│     → WAITING (N2 not passed)                                      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## SEGUNDA-FEIRA, 6 Abril 2026

### Contexto Macro do Dia
- **VIX**: 16.5 (baixo, mercado calmo)
- **Yield 2s10s**: +42 bps (normal, 3 pts)
- **Gamma**: GEX positivo, QQQ acima do ZGL → POSITIVE/ABOVE_ZGL (3 pts)
- **Term Structure**: VX1/VX2 = 0.93 (contango leve, 2 pts)
- **Macro Score Total**: 2 (VIX) + 3 (Yield) + 3 (Gamma) + 2 (TS) = **10 → Regime BULL**

### 04:00 ET — Pre-market Start
```
EOD Monitor [30s]: Verifica session_info → is_trading_day=True, is_premarket=True
                    → Auto trading window ABERTA (04:00 → 15:30)
                    → eod_flattened_today = False (reset)

Snapshot Loop [60s]: is_trading_day=True → GRAVA snapshot
  → v3_engine.evaluate("MNQ"):
      N1: score=10 → BULL (MNQ, Trend Following, 100%, LONG)
      N2: price=21,450 > VAH(21,380) + acima +1σ(21,410) → passed=True
      N3: N2-DZ bullish, OFI Fast=+0.25 < 0.4 → WAIT
          Reason: "Aguardando: OFI Fast=0.25 (need>0.4)"
      V3 Status: FILTERS_PASSED

  → v3_engine.evaluate("MES"):
      N1: score=10 → BULL (target=MNQ, MES não é target deste regime)
      N2: passed=True
      N3: WAIT (mesma razão)
      V3 Status: FILTERS_PASSED
```

### 04:01–09:29 ET — Pre-market (1 snapshot/min × 2 símbolos)
```
330 minutos × 2 símbolos = 660 snapshots gravados

Cada ciclo de 60s:
  → Snapshot Loop grava N1+N2+N3 para MNQ e MES
  → N1 permanece BULL (macro não muda intraday significativamente)
  → N2 flutua entre passed/not passed conforme preço vs VA/VWAP
  → N3 aguarda confluência de OFI + Delta Zonal
  → EOD Monitor (30s): apenas verifica — nenhuma ação necessária
```

### 09:35 ET — Cash Open + Volume Spike
```
Snapshot #331 (MNQ):
  N1: score=10 → BULL (inalterado)
  N2: MNQ price=21,485 > VAH(21,380) + acima +1σ(21,420)
      → passed=True
      Reason: "Preco acima da VAH (21380.00), acima de +1SD (21420.00)"
  N3: Pullback detectado:
      - price=21,430 recuou para +1σ+tolerance (21,420 + 0.5×ATR_M1)
      - N2-DZ: BULLISH_CONFIRMED (delta=+180, bull=3/4 levels)
      - OFI Fast: +0.52 > 0.4 ✓
      → action=BUY, entry_type=MARKET, entry_price=21,430
      → take_profit=22,100 (Call Wall convertida via D-1 ratio)
      Reason: "Compra no pullback. Pullback ate VWAP/+1s | N2 DZ: delta=+180, bull=3 | OFI Fast: 0.52"
  V3 Status: ACTIVE_SIGNAL ★
```

### ★ TRADE 1: BULL → MNQ LONG (Trend Following)
```
execute_v3_signal("MNQ"):
  config: enabled=True, paper_trading=False
  
  1. V3 evaluate → ACTIVE_SIGNAL, action=BUY
  2. Archetype: get_archetype("BULL") → TREND
  3. Position params (calculate_position_params):
     - entry_price: 21,430.00
     - hard_stop: 21,430 - 1.5×ATR_M1(6.45) = 21,420.33
     - take_profit: None (TREND = open, trailing gerencia saída)
     - trailing: VWAP_CENTRAL
  4. Auto sizing: account_risk=$500, SL distance=9.67pts, tick_value=$2
     → risk_per_contract = 9.67/0.25 × $2.00 = $77.36
     → max_qty = floor($500 / $77.36) = 6 contracts
     → lot_pct=100% → quantity = 6

  5. SignalStack OCO (3 webhooks):
     Webhook 1 (Entry):
       {"symbol":"MNQM6","action":"buy","quantity":6,"class":"future",
        "trail_trigger":6.45,"trail_stop":4.84,"trail_freq":0.50}
       → Tradovate: FILLED @ 21,430.25

     Webhook 2 (Hard Stop):
       {"symbol":"MNQM6","action":"sell","quantity":6,"class":"future",
        "stop_price":21420.33}
       → Tradovate: ACCEPTED (stop working)

     Webhook 3 (Take Profit): SKIP (TREND = open trailing)

  6. MongoDB: position salva em `positions` collection
     state=MANAGING, events=[{type:'OPENED'}, {type:'SS_OCO_SENT'}]
     trailing={trail_trigger:6.45, trail_stop:4.84, trail_freq:0.50}

  → O trailing é gerenciado pelo BROKER (Tradovate server-side):
    - Preço sobe 6.45 pts acima da entrada → trailing ativa
    - Stop acompanha preço a 4.84 pts de distância
    - Atualiza a cada 0.50 pts de movimento favorável
```

### 10:30–15:25 ET — Sessão Regular
```
Snapshot Loop continua a cada 60s (MNQ + MES):
  → N1: BULL estável (score=10)
  → N2: passed=True (preço sustenta acima de +1σ)
  → N3: WAIT (já há posição aberta, sem novo sinal)

  MNQ sobe para 21,510 ao meio-dia:
  → Trailing ativou no Tradovate (21,430+6.45=21,436.45 atingido)
  → Stop trailing acompanhando: stop agora em 21,505.16 (21,510-4.84)

EOD Monitor [30s]:
  → Verifica minutes_to_auto_end
  → 15:25 ET: minutes_to_auto_end=5.0 → LOG WARNING "5min to auto flatten"
```

### 15:29 ET — EOD Auto Flatten
```
EOD Monitor [30s]:
  minutes_to_auto_end = 1.0 → FLATTEN TRIGGERED
  → flatten_all_positions(reason='eod_auto_flatten'):

    Position: MNQ LONG 6x @ 21,430

    Webhook 1 (Close):
      {"symbol":"MNQM6","action":"sell","quantity":6,"class":"future"}
      → Tradovate: FILLED @ 21,508.50

    Webhook 2 (Cancel pendentes):
      {"symbol":"MNQM6","action":"cancel","quantity":1,"class":"future"}
      → Tradovate: 201 Accepted (cancela stop trailing órfão)

  MongoDB: position.state = CLOSED, close_reason = "eod_auto_flatten"
  P&L: (21,508.50 - 21,430.00) × 6 × $2.00 = +$942.00

  LOG: "EOD Monitor: Flattened 1 positions"
  eod_flattened_today = True
```

### 15:30–17:00 ET — Pós-mercado
```
Snapshot Loop: is_auto_trading_window=False, mas is_market_open=True
  → Continua gravando snapshots (mercado aberto, embora auto trading esteja off)

17:00 ET: CME Maintenance → Snapshot retorna "market_closed" → para de gravar
```

### Resumo Segunda-feira
| Métrica | Valor |
|---------|-------|
| Snapshots gravados | ~1,380 (690min × 2 sym) |
| Regime dominante | BULL |
| Trades executados | 1 (MNQ LONG) |
| Resultado | +$942.00 |

---

## TERÇA-FEIRA, 7 Abril 2026

### Contexto Macro do Dia
- **VIX**: 21.0 (subindo — ansiedade pré-CPI)
- **Yield 2s10s**: +35 bps (normal, 3 pts)
- **Gamma**: GEX positivo mas preço caiu para perto do ZGL → POSITIVE/NEUTRAL (2 pts)
- **Term Structure**: VX1/VX2 = 1.01 (flat, 1 pt)
- **Macro Score**: 1 (VIX) + 3 (Yield) + 2 (Gamma) + 1 (TS) = **7 → Regime TRANSICAO**

### 04:00 ET — Pre-market
```
EOD Monitor: eod_flattened_today = False (novo dia)

Snapshot Loop:
  → v3_engine.evaluate("MES"):  ← Note: TRANSICAO target é MES
      N1: score=7 → TRANSICAO (MES, Range Scalping, 50%, COUNTER_TREND)
      N2: MES price=6,085 dentro da VA (6,060–6,110)
          → passed=True
          Reason: "Preco DENTRO da VA (6060.00-6110.00), perto da VWAP"
      N3: Sem absorção N3, N2-DZ VAH Z=0.4, VAL Z=0.3
          → WAIT
          Reason: "Aguardando absorcao ou atividade nas bordas VA. VAH Z=0.4, VAL Z=0.3"
      V3 Status: FILTERS_PASSED
```

### 10:15 ET — MES toca VAH com absorção
```
Snapshot (MES):
  N1: score=7 → TRANSICAO (inalterado)
  N2: MES price=6,108 — perto de VAH(6,110)
      → passed=True ("Preco DENTRO da VA")
  N3: Delta Zonal N3 detecta absorção:
      - BUY_ABSORPTION em call_wall (converted: 6,145)
        → Vendedores sendo absorvidos na resistência
        → Z-Score=1.8 (acima do threshold 1.0)
      - Preço=6,108 > POC(6,082) → posição acima do POC confirma SHORT
      - OFI Fast: -0.15 (leve pressão vendedora)
      → action=SELL, entry_type=LIMIT, entry_price=6,108
      → take_profit=6,082 (POC)
      Reason: "Venda por absorcao na resistencia. Absorcao N3: BUY_ABSORPTION em call_wall (Z=1.8) | Preco > POC (6108 > 6082) | OFI Fast: -0.15"
  V3 Status: ACTIVE_SIGNAL ★
```

### ★ TRADE 2: TRANSICAO → MES SHORT (Range Scalping)
```
execute_v3_signal("MES"):
  1. V3 evaluate → ACTIVE_SIGNAL, action=SELL
  2. Archetype: get_archetype("TRANSICAO") → RANGE
  3. Position params:
     - entry_price: 6,108.00
     - hard_stop: VAH(6,110) + 0.5×ATR_M1(1.80) = 6,110.90
     - take_profit: POC = 6,082.00 (FIXED_POC)
     - trailing: NONE (tese binária — ou bate TP ou SL)
  4. Auto sizing: risk=$500, SL=2.90pts, tick_size=0.25, tick_value=$1.25
     → risk_per_contract = (2.90/0.25) × $1.25 = $14.50
     → max_qty = floor($500 / $14.50) = 34
     → lot_pct=50% → quantity = 17 contracts

  5. SignalStack OCO:
     Webhook 1 (Entry):
       {"symbol":"MESM6","action":"sell","quantity":17,"class":"future"}
       → FILLED @ 6,108.00

     Webhook 2 (Hard Stop):
       {"symbol":"MESM6","action":"buy","quantity":17,"class":"future",
        "stop_price":6110.90}
       → ACCEPTED

     Webhook 3 (Take Profit):
       {"symbol":"MESM6","action":"buy","quantity":17,"class":"future",
        "limit_price":6082.00}
       → ACCEPTED

  → Sem trailing. Fire & forget. Broker resolve via OCO estático.
```

### 11:45 ET — MES reverte para POC
```
Tradovate: TP limit @ 6,082.00 FILLED
  → SL stop automaticamente cancelado pelo broker (OCO nativo)

Position fechada automaticamente no broker.
(O sistema detecta na próxima verificação que a posição foi encerrada)

P&L: (6,108.00 - 6,082.00) × 17 × $5.00 = +$2,210.00
```

### 14:00 ET — News Blackout (CPI Preview Event)
```
EOD Monitor [30s]:
  → check_news_blackout(): CPI Preview @ 14:30 ET
  → news_blackout_minutes_before=15
  → 14:00 é 30 min antes → is_blackout=False
  
14:15 ET:
  → 15 min antes → is_blackout=True
  → Verifica posições abertas → nenhuma (Trade 2 já fechou por TP)
  → LOG: "NEWS BLACKOUT — CPI Preview. No positions to flatten."
  → Bloqueia novas entradas até 14:30 + news_blackout_minutes_after
```

### 15:29 ET — EOD Flatten
```
EOD Monitor: Sem posições abertas → nada a fazer
eod_flattened_today = True
```

### Resumo Terça-feira
| Métrica | Valor |
|---------|-------|
| Snapshots gravados | ~1,380 |
| Regime dominante | TRANSICAO |
| Trades executados | 1 (MES SHORT) |
| Resultado | +$2,210.00 |

---

## QUARTA-FEIRA, 8 Abril 2026

### Contexto Macro — MANHÃ (CPI Hot)
- **VIX**: 27.5 (spike pós-CPI acima do esperado)
- **Yield 2s10s**: +15 bps (achatando, 1 pt)
- **Gamma**: GEX negativo, preço abaixo do ZGL → NEGATIVE/BELOW_ZGL (0 pts)
- **Term Structure**: VX1/VX2 = 1.06 (flat/leve backwardation, 0 pts)
- **Macro Score**: 0 (VIX) + 1 (Yield) + 0 (Gamma) + 0 (TS) = **1 → Regime CAPITULACAO**

### 08:30 ET — CPI Release (Hot: +0.5% vs +0.3% expected)
```
News Blackout ATIVO (8:15-8:45 ET):
  → EOD Monitor bloqueia entradas
  → Snapshot Loop grava mas trades bloqueados

08:45 ET: Blackout encerra
```

### 09:00 ET — Pós-CPI Selloff
```
Snapshot (MES):  ← CAPITULACAO target é MES
  N1: score=1 → CAPITULACAO (MES, Fading Exaustão, 50%, LONG_REVERSAL)
  N2: MES price=5,980 < VAL(6,010) + abaixo -3σ(5,985)
      → passed=True
      Reason: "Preco esticado abaixo da VAL, abaixo de -3SD, Put Wall hit=False, ZGL hit=False"
  N3: Delta Zonal N3:
      - Sem SELL_ABSORPTION na Put Wall ainda
      - N2-DZ: pressão vendedora forte (delta=-280)
      - OFI Fast: -0.65 (agressão vendedora intensa)
      → WAIT
      Reason: "Aguardando exaustao: Sem absorcao N3 | N2 ainda vendedor (delta=-280) | OFI Fast=-0.65 (still bearish)"
  V3 Status: FILTERS_PASSED (N2 ok, N3 aguarda)
```

### 09:45 ET — Absorção na Put Wall
```
Snapshot (MES):
  N1: score=1 → CAPITULACAO (inalterado)
  N2: MES price=5,965 < VAL, abaixo de -3σ
      → passed=True
  N3: Delta Zonal N3:
      - SELL_ABSORPTION detectada na Put Wall (5,960 convertida D-1)
        → Vendedores chocando contra parede de suporte, volume absorvido
        → Z-Score=2.1
      - N2-DZ: delta=-45 (pressão vendedora ENFRAQUECENDO vs -280 antes)
      - OFI Fast: -0.15 (agressão vendedora diminuindo)
      → Todas as 3 condições atendidas:
        absorption_at_support=True ✓
        n2_selling_fading (delta > -50) ✓
        ofi_turning (> -0.2) ✓
      → action=BUY, entry_type=PASSIVE_ABSORPTION, entry_price=5,965
      → take_profit=5,998 (-1σ VWAP)
      Reason: "Compra por exaustao. N3 Absorcao: SELL_ABSORPTION em put_wall | N2 DZ: pressao vendedora enfraquecendo (delta=-45) | OFI Fast: -0.15 (virando)"
  V3 Status: ACTIVE_SIGNAL ★
```

### ★ TRADE 3: CAPITULACAO → MES LONG (Fading na Exaustão)
```
execute_v3_signal("MES"):
  1. V3 evaluate → ACTIVE_SIGNAL, action=BUY
  2. Archetype: get_archetype("CAPITULACAO") → FADE
  3. Position params:
     - entry_price: 5,965.00
     - hard_stop: put_wall(5,960) - 0.5×ATR_M1(2.10) = 5,958.95
     - take_profit: -1σ VWAP(5,998) (FIXED_VWAP_1S)
     - break_even: threshold = 5,965 + 1.0×2.10 + 4×0.25 = 5,968.10
       → Quando preço atinge 5,968.10, SL move para 5,965.00 (entry)
  4. Auto sizing: risk=$500, SL=6.05pts, tick_value=$1.25
     → risk_per_contract = (6.05/0.25) × $1.25 = $30.25
     → max_qty = floor($500 / $30.25) = 16
     → lot_pct=50% → quantity = 8

  5. SignalStack OCO:
     Webhook 1 (Entry + breakeven):
       {"symbol":"MESM6","action":"buy","quantity":8,"class":"future",
        "breakeven":3.10}
       → FILLED @ 5,965.25

     Webhook 2 (Hard Stop):
       {"symbol":"MESM6","action":"sell","quantity":8,"class":"future",
        "stop_price":5958.95}
       → ACCEPTED

     Webhook 3 (Take Profit):
       {"symbol":"MESM6","action":"sell","quantity":8,"class":"future",
        "limit_price":5998.00}
       → ACCEPTED

  → Breakeven gerenciado pelo broker:
    - Preço sobe 3.10 pts acima do fill → stop move para entry (5,965.25)
    - Depois disso, ou bate TP(5,998) ou é stopado no break-even
```

### 10:30 ET — MES recupera para -1σ VWAP
```
Tradovate: TP limit @ 5,998.00 FILLED
  → SL + breakeven automaticamente cancelados

P&L: (5,998.00 - 5,965.25) × 8 × $5.00 = +$1,311.00
```

### Contexto Macro — TARDE (Mercado estabiliza)
- **VIX**: 24.0 (recuando do pico)
- **Yield 2s10s**: +20 bps (1 pt)
- **Gamma**: GEX negativo, preço ainda abaixo do ZGL → 0 pts
- **Term Structure**: VX1/VX2 = 1.04 (normalizando, 1 pt)
- **Macro Score**: 1 (VIX) + 1 (Yield) + 0 (Gamma) + 1 (TS) = **3 → Regime BEAR**

### 13:15 ET — Repique até VWAP/-1σ
```
Snapshot (MNQ):  ← BEAR target é MNQ
  N1: score=3 → BEAR (MNQ, Momentum Short, 100%, SHORT)
  N2: MNQ price=21,180 < VAL(21,220) + abaixo -1σ(21,200)
      → passed=True
      Reason: "Preco abaixo da VAL (21220.00), abaixo de -1SD (21200.00)"
  N3:
      - Bounce tolerance: 0.5×ATR_M1(6.5) = 3.25
      - price=21,195 ≥ -1σ(21,200) - 3.25 = 21,196.75? → price < threshold
      → Repique insuficiente
      → WAIT
      Reason: "Aguardando: Sem repique (price=21195, -1s=21200)"
  V3 Status: FILTERS_PASSED
```

### 13:40 ET — Repique atinge VWAP/-1σ com fluxo bearish
```
Snapshot (MNQ):
  N1: score=3 → BEAR
  N2: MNQ price=21,198 < VAL + abaixo -1σ
      → passed=True
  N3:
      - price=21,198 ≥ -1σ(21,200) - 3.25 = 21,196.75 ✓ (bounce ok)
      - N2-DZ: BEARISH (delta=-120, bear=3/4 levels) ✓
      - OFI Fast: -0.55 < -0.4 ✓
      → action=SELL, entry_type=MARKET, entry_price=21,198
      → take_profit=20,950 (Put Wall convertida)
      Reason: "Venda no repique. Repique ate VWAP/-1s | N2 DZ: delta=-120, bear=3 | OFI Fast: -0.55"
  V3 Status: ACTIVE_SIGNAL ★
```

### ★ TRADE 4: BEAR → MNQ SHORT (Momentum Short)
```
execute_v3_signal("MNQ"):
  1. V3 evaluate → ACTIVE_SIGNAL, action=SELL
  2. Archetype: get_archetype("BEAR") → TREND
  3. Position params:
     - entry_price: 21,198.00
     - hard_stop: 21,198 + 1.5×6.50 = 21,207.75
     - take_profit: None (TREND SHORT = open, trailing gerencia)
     - trailing: VWAP_CENTRAL (invertido — stop acompanha para baixo)
  4. Auto sizing: risk=$500, SL=9.75pts
     → risk_per_contract = (9.75/0.25) × $2.00 = $78.00
     → max_qty = floor($500 / $78.00) = 6
     → lot_pct=100% → quantity = 6

  5. SignalStack OCO:
     Webhook 1 (Entry + trailing):
       {"symbol":"MNQM6","action":"sell","quantity":6,"class":"future",
        "trail_trigger":6.50,"trail_stop":4.88,"trail_freq":0.50}
       → FILLED @ 21,197.75

     Webhook 2 (Hard Stop):
       {"symbol":"MNQM6","action":"buy","quantity":6,"class":"future",
        "stop_price":21207.75}
       → ACCEPTED
```

### 15:29 ET — EOD Flatten (posição SHORT ainda aberta)
```
EOD Monitor: minutes_to_auto_end ≤ 1
  → flatten_all_positions(reason='eod_auto_flatten')

  MNQ @ 21,130 (em queda)

  Webhook 1 (Close):
    {"symbol":"MNQM6","action":"buy","quantity":6,"class":"future"}
    → FILLED @ 21,131.00

  Webhook 2 (Cancel):
    {"symbol":"MNQM6","action":"cancel","quantity":1,"class":"future"}
    → 201 Accepted (cancela trailing stop órfão)

P&L: (21,197.75 - 21,131.00) × 6 × $2.00 = +$801.00
```

### Resumo Quarta-feira
| Métrica | Valor |
|---------|-------|
| Snapshots gravados | ~1,380 |
| Regimes | CAPITULACAO (manhã) → BEAR (tarde) |
| Trades executados | 2 (MES LONG fade, MNQ SHORT momentum) |
| Resultado | +$1,311 + $801 = +$2,112.00 |

---

## QUINTA-FEIRA, 9 Abril 2026

### Contexto Macro do Dia
- **VIX**: 13.5 (colapso pós-CPI digerido, mercado relaxa)
- **Yield 2s10s**: +55 bps (steepening, 4 pts)
- **Gamma**: GEX muito positivo, QQQ bem acima do ZGL → POSITIVE/ABOVE_ZGL (3 pts)
- **Term Structure**: VX1/VX2 = 0.88 (contango, 2 pts)
- **Macro Score**: 3 (VIX) + 4 (Yield) + 3 (Gamma) + 2 (TS) = **12 → Regime COMPLACENCIA**

### 09:35 ET — Cash Open
```
Snapshot (MNQ):  ← COMPLACENCIA target é MNQ
  N1: score=12 → COMPLACENCIA (MNQ, Trend Defensivo, 75%, LONG)
  N2: MNQ price=21,520 > VAH(21,460) + acima +2σ(21,500)
      → passed=True
      Reason: "Preco acima da VAH (21460.00), acima de +2SD (21500.00)"
  N3:
      - N2-DZ: status=BULLISH_CONFIRMED (delta=+95, bull=3/4)
      - OFI Fast: +0.22 < 0.3
      → WAIT
      Reason: "Aguardando: OFI Fast=0.22 (need>0.3)"
  V3 Status: FILTERS_PASSED
```

### 10:05 ET — OFI Fast confirma
```
Snapshot (MNQ):
  N1: score=12 → COMPLACENCIA
  N2: passed=True
  N3:
      - N2-DZ: BULLISH_CONFIRMED ✓
      - OFI Fast: +0.38 > 0.3 ✓
      → action=BUY, entry_type=MARKET_PULLBACK, entry_price=21,535
      Reason: "Compra inercial. N2 DZ: BULLISH_CONFIRMED (delta=+95, bull=3/4) | OFI Fast: 0.38 > 0.3"
  V3 Status: ACTIVE_SIGNAL ★
```

### ★ TRADE 5: COMPLACENCIA → MNQ LONG (Trend Defensivo)
```
execute_v3_signal("MNQ"):
  1. V3 evaluate → ACTIVE_SIGNAL, action=BUY
  2. Archetype: get_archetype("COMPLACENCIA") → TREND
  3. Position params:
     - entry_price: 21,535.00
     - hard_stop: 21,535 - 1.5×5.80 = 21,526.30
     - take_profit: None (TREND = open trailing)
  4. Auto sizing: risk=$500, SL=8.70pts
     → risk_per_contract = (8.70/0.25) × $2.00 = $69.60
     → max_qty = floor($500 / $69.60) = 7
     → lot_pct=75% → quantity = max(1, 7×0.75) = 5

  5. SignalStack OCO:
     Webhook 1 (Entry + trailing):
       {"symbol":"MNQM6","action":"buy","quantity":5,"class":"future",
        "trail_trigger":5.80,"trail_stop":4.35,"trail_freq":0.50}
       → FILLED @ 21,535.50

     Webhook 2 (Hard Stop):
       {"symbol":"MNQM6","action":"buy","quantity":5,"class":"future",
        "stop_price":21526.30}
       → ACCEPTED

  Nota: lot_pct=75% (defensivo) vs 100% no BULL.
  O regime COMPLACENCIA opera com posição menor porque VIX muito baixo
  pode significar complacência excessiva do mercado.
```

### 14:30 ET — Trailing profit-take pelo broker
```
MNQ atinge 21,590:
  → Trail ativou (21,535.50 + 5.80 = 21,541.30 superado)
  → Stop trailing agora em: 21,585.15 (21,590 - 4.35)

MNQ recua para 21,584:
  → Tradovate: Trailing stop FILLED @ 21,585.00

P&L: (21,585.00 - 21,535.50) × 5 × $2.00 = +$495.00

MongoDB: position atualizada via próxima verificação
  state=CLOSED, close_reason=TRAILING_STOP
```

### 15:29 ET — EOD Flatten
```
EOD Monitor: Sem posições abertas → nada a fazer
```

### Resumo Quinta-feira
| Métrica | Valor |
|---------|-------|
| Snapshots gravados | ~1,380 |
| Regime dominante | COMPLACENCIA |
| Trades executados | 1 (MNQ LONG defensivo) |
| Resultado | +$495.00 |

---

## SEXTA-FEIRA, 10 Abril 2026

### Contexto Macro do Dia
- **VIX**: 18.0
- **Yield 2s10s**: +40 bps (3 pts)
- **Gamma**: GEX positivo, preço no ZGL → POSITIVE/NEUTRAL (2 pts)
- **Term Structure**: VX1/VX2 = 0.97 (normal, 1 pt)
- **Macro Score**: 2 (VIX) + 3 (Yield) + 2 (Gamma) + 1 (TS) = **8 → Regime BULL**

### 09:35 ET — Cash Open
```
Snapshot (MNQ):
  N1: score=8 → BULL (na fronteira BULL/TRANSICAO)
  N2: MNQ price=21,560 > VAH + acima +1σ → passed=True
  N3:
      - Pullback insuficiente (preço > +1σ + tolerance)
      → WAIT
      Reason: "Aguardando: Sem pullback (price=21560, +1s=21540)"
  V3 Status: FILTERS_PASSED
```

### 10:00–11:00 ET — Mercado lateraliza, sem sinais
```
60 snapshots × 2 = 120 snapshots gravados
Todos com v3_status=FILTERS_PASSED ou WAITING
N3 permanece WAIT — sem confluência de fluxo
```

### 11:15 ET — Pullback com confluência
```
Snapshot (MNQ):
  N1: score=8 → BULL
  N2: MNQ price=21,542 → pullback ok (+1σ=21,540 + tolerance=3.25)
      → passed=True
  N3:
      - N2-DZ: delta=+65, bull=2/4 ✓
      - OFI Fast: +0.45 > 0.4 ✓
      → action=BUY, entry_type=MARKET
  V3 Status: ACTIVE_SIGNAL ★
```

### ★ TRADE 6: BULL → MNQ LONG (Friday Continuation)
```
execute_v3_signal("MNQ"):
  Archetype: TREND
  entry=21,542, SL=21,532.33 (1.5×6.45), trailing ativo
  lot_pct=100%, qty=6

  SignalStack: Entry + SL + trailing enviados
```

### 14:45 ET — Posição ainda aberta, mercado +$12 no trade
```
MNQ @ 21,554. Trailing ativou, stop em 21,549.

Snapshot Loop: v3_status=FILTERS_PASSED (posição aberta, sem novo sinal)
```

### 15:29 ET — EOD Flatten (fim de semana)
```
EOD Monitor: minutes_to_auto_end ≤ 1

flatten_all_positions(reason='eod_auto_flatten'):
  Webhook 1 (Close):
    {"symbol":"MNQM6","action":"sell","quantity":6,"class":"future"}
    → FILLED @ 21,553.25

  Webhook 2 (Cancel):
    {"symbol":"MNQM6","action":"cancel","quantity":1,"class":"future"}
    → 201 Accepted

P&L: (21,553.25 - 21,542.00) × 6 × $2.00 = +$135.00
```

### 15:30–17:00 ET — Snapshots finais da semana
```
Snapshot Loop continua até CME maintenance
Última gravação: 16:59 ET

17:00 ET: CME Maintenance → Snapshot para
```

### Resumo Sexta-feira
| Métrica | Valor |
|---------|-------|
| Snapshots gravados | ~1,380 |
| Regime dominante | BULL |
| Trades executados | 1 (MNQ LONG) |
| Resultado | +$135.00 |

---

## SÁBADO-DOMINGO (11-12 Abril)

```
04:00 Sábado:
  EOD Monitor [30s]: is_trading_day=False, is_weekend=True → SKIP
  Snapshot Loop [60s]: is_trading_day=False → return "not_trading_day" → SKIP

  Nenhum snapshot gravado.
  Nenhuma avaliação V3.
  yfinance/DataBento podem retornar erros/rate limits → fallback handlers ativos

18:00 Domingo ET:
  CME Futures reabrem → próximo ciclo detecta is_trading_day=True
  → Snapshot Loop retoma gravação
  → Nova semana de trading começa
```

---

## Resumo Consolidado da Semana

### Trades Executados

| # | Dia | Regime | Archetype | Ativo | Lado | Qty | Entry | Exit | Motivo Saída | P&L |
|---|-----|--------|-----------|-------|------|-----|-------|------|-------------|-----|
| 1 | Seg | BULL | TREND | MNQM6 | LONG | 6 | 21,430.25 | 21,508.50 | EOD Flatten | +$942 |
| 2 | Ter | TRANSICAO | RANGE | MESM6 | SHORT | 17 | 6,108.00 | 6,082.00 | Take Profit | +$2,210 |
| 3 | Qua AM | CAPITULACAO | FADE | MESM6 | LONG | 8 | 5,965.25 | 5,998.00 | Take Profit | +$1,311 |
| 4 | Qua PM | BEAR | TREND | MNQM6 | SHORT | 6 | 21,197.75 | 21,131.00 | EOD Flatten | +$801 |
| 5 | Qui | COMPLACENCIA | TREND | MNQM6 | LONG | 5 | 21,535.50 | 21,585.00 | Trailing Stop | +$495 |
| 6 | Sex | BULL | TREND | MNQM6 | LONG | 6 | 21,542.00 | 21,553.25 | EOD Flatten | +$135 |

### Cobertura de Regimes

| Regime | Archetype | Trade | Tática N3 | SL Tipo | TP Tipo | Gestão |
|--------|-----------|-------|-----------|---------|---------|--------|
| **COMPLACENCIA** | TREND | #5 | Compra inercial (N2-DZ bullish + OFI>0.3) | 1.5×ATR | Open (trailing) | trail_trigger/trail_stop/trail_freq |
| **BULL** | TREND | #1, #6 | Compra no pullback (+1σ + N2-DZ + OFI>0.4) | 1.5×ATR | Open (trailing) | trail_trigger/trail_stop/trail_freq |
| **TRANSICAO** | RANGE | #2 | Venda por absorção N3 na resistência (Z>1.0) | 0.5×ATR acima VAH | POC fixo | Fire & forget (OCO estático) |
| **BEAR** | TREND | #4 | Venda no repique (-1σ + N2-DZ bearish + OFI<-0.4) | 1.5×ATR | Open (trailing) | trail_trigger/trail_stop/trail_freq |
| **CAPITULACAO** | FADE | #3 | Compra por exaustão (absorção Put Wall + selling fading + OFI virando) | 0.5×ATR abaixo Put Wall | -1σ VWAP | breakeven param |

### Ciclos e Dados Gravados

| Métrica | Valor |
|---------|-------|
| Snapshots totais (5 dias) | ~6,900 (690min × 2sym × 5dias) |
| Armazenamento estimado | ~42 MB (6,900 × 6.2KB) |
| Avaliações V3 (total) | ~6,900 (1 por snapshot) |
| Avaliações que geraram ACTIVE_SIGNAL | 6 (0.09%) |
| Avaliações FILTERS_PASSED (N2 ok, N3 WAIT) | ~4,500 (65%) |
| Avaliações WAITING (N2 not passed) | ~2,394 (35%) |
| EOD Flattens executados | 3 (Seg, Qua, Sex) |
| News Blackouts | 1 (Terça 14:15 — CPI Preview) |
| Dias sem trade | 0 |

### Fluxo de Decisão — Funil Semanal

```
6,900 snapshots (V3 evaluate)
  │
  ├── 2,394 (35%) → N2 NOT PASSED → v3_status=WAITING
  │     └── Preço fora das condições do regime (ex: BULL mas preço abaixo da VAH)
  │
  ├── 4,500 (65%) → N2 PASSED → v3_status=FILTERS_PASSED
  │     └── Preço confirma regime, mas N3 aguarda confluência de fluxo
  │
  └── 6 (0.09%) → N3 ACTIVE_SIGNAL → TRADE EXECUTADO
        └── Confluência total: N1 regime + N2 filtros + N3 Delta Zonal/OFI
```
