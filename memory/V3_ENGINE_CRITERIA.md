# V3 Engine — Tabela de Critérios N2/N3 por Regime

## Legenda Geral
- **DZ** = Delta Zonal (Order Flow Analysis por nível)
- **Z** = Z-Score (anomalia de volume vs baseline Welford)
- **DR** = Delta Ratio (net_delta / total_volume — dominância direcional)
- **OFI** = Order Flow Imbalance EMA 20s (agressão direcional imediata)
- **ATR M5** = Average True Range 5 minutos (volatilidade estrutural)
- **VA** = Value Area (VAH/VAL/POC)
- **Caixote** = Envelope formado pela UNIÃO de VA Session + VA D-1

---

## N1 — Roteamento de Regime

| Score Macro | Term Structure Override | Regime | Símbolo | Tática | Lot % | Direção |
|---|---|---|---|---|---|---|
| ≥ 11 | — | COMPLACÊNCIA | MNQ | Trend Defensivo | 75% | LONG |
| — | < 0.80 (contango extremo) | COMPLACÊNCIA | MNQ | Trend Defensivo | 75% | LONG |
| 8–10 | — | BULL | MNQ | Trend Following | 100% | LONG |
| 5–7 | — | TRANSIÇÃO | MES | Range Scalping | 50% | COUNTER_TREND |
| 2–4 | — | BEAR | MNQ | Momentum Short | 100% | SHORT |
| 0–1 | — | CAPITULAÇÃO | MES | Fading (Exaustão) | 50% | LONG_REVERSAL |
| — | > 1.10 (backwardation extrema) | CAPITULAÇÃO | MES | Fading (Exaustão) | 50% | LONG_REVERSAL |

> **DQS (Data Quality Score)** é avaliado antes do N1. Se DQS < 0.75 → **HARD_STOP** (nenhum trade liberado).

---

## N2 — Filtro de Interação (por Regime)

### 1. COMPLACÊNCIA

| # | Critério | Onde é medido | Threshold RTH | Threshold Globex | Buffer ATR | Resultado se FALHA |
|---|---|---|---|---|---|---|
| C1 | Preço acima da VAH | current_price > VAH | Sim | Sim | — | ❌ BLOCKED |
| C2 | Preço acima de +2σ VWAP | current_price > vwap_upper_2 | Sim | Sim | — | ❌ BLOCKED |
| C3 | Z-Score na zona VAH/VWAP | DZ na VAH (fallback: VWAP) | Z > -0.5 | Z > -0.5 | — | ❌ INSUFFICIENT |
| C4 | Delta Ratio positivo | DR na zona VAH/VWAP | DR > 0.15 | DR > 0.15 | — | ❌ INSUFFICIENT |

**Combinação para PASS:** C1 ✅ AND C2 ✅ AND C3 ✅ AND C4 ✅ → `BULLISH_ACCEPTANCE`
**Sem DZ disponível:** C1 ✅ AND C2 ✅ → `POSITIONAL_ONLY` (aceita sem DZ)

| Interaction Quality | Significado |
|---|---|
| BULLISH_ACCEPTANCE | Todos os critérios passaram — fluxo institucional bullish confirmado |
| INSUFFICIENT | DZ disponível mas Z ou DR falharam |
| POSITIONAL_ONLY | Sem dados DZ — aprovado apenas por posição (legacy) |

---

### 2. BULL

| # | Critério | Onde é medido | Threshold RTH | Threshold Globex | Buffer ATR | Resultado se FALHA |
|---|---|---|---|---|---|---|
| B1 | Preço acima da VAH | current_price > VAH | Sim | Sim | — | ❌ BLOCKED |
| B2 | Preço acima de +1σ VWAP | current_price > vwap_upper_1 | Sim | Sim | — | ❌ BLOCKED |
| B3 | Preço acima do POC D-1 | current_price > d1_poc | Sim (se d1_poc > 0) | Sim | — | ❌ BLOCKED |
| B4 | Z-Score na zona VAH/VWAP | DZ na VAH (fallback: VWAP) | Z > 1.0 | Z > 1.0 | — | ❌ WEAK_ACCEPTANCE |
| B5 | Delta Ratio positivo forte | DR na zona VAH/VWAP | DR > 0.20 | DR > 0.20 | — | ❌ WEAK_ACCEPTANCE |

**Combinação para PASS:** B1 ✅ AND B2 ✅ AND B3 ✅ AND B4 ✅ AND B5 ✅ → `BULLISH_ACCEPTANCE`
**Sem DZ disponível:** B1 ✅ AND B2 ✅ AND B3 ✅ → `POSITIONAL_ONLY`

| Interaction Quality | Significado |
|---|---|
| BULLISH_ACCEPTANCE | Aceitação compradora confirmada via order flow |
| WEAK_ACCEPTANCE | Posição OK mas DZ não confirma plenamente (Z ou DR abaixo) |
| POSITIONAL_ONLY | Sem dados DZ — aprovado apenas por posição |

---

### 3. TRANSIÇÃO (Caixote)

**Lógica especial: O caixote é a UNIÃO da VA Session + VA D-1.**
- `eff_vah = max(VAH_Session, VAH_D1)`
- `eff_val = min(VAL_Session, VAL_D1)`
- 4 bordas possíveis: VAH, VAL, VAH_D1, VAL_D1
- Borda D-1 só é considerada se `|d1_level - session_level| > 0.5 × ATR_M5`
- O engine detecta a **borda mais próxima** do preço e avalia DZ nessa borda específica

| # | Critério | Onde é medido | Threshold RTH | Threshold Globex | Buffer ATR | Resultado se FALHA |
|---|---|---|---|---|---|---|
| T1 | Preço DENTRO do caixote | eff_val ≤ price ≤ eff_vah | Sim | Sim | — | ❌ "Preço FORA do caixote" |
| T2 | Volume significativo na borda | DZ na borda mais próxima | volume_significant = True | volume_significant = True | — | ⚠️ POSITIONAL_ONLY |
| T3 | Z-Score na borda | \|Z\| na borda detectada | \|Z\| > 1.0 | \|Z\| > 1.0 | — | ❌ "Z < 1.0 (vol insuficiente)" |
| T4 | Delta Ratio com rejeição | DR na borda detectada | **DR > 0.25** (suporte) / **DR < -0.25** (resist.) | **DR > 0.10** / **DR < -0.10** | — | ❌ "Sem rejeição na borda" |
| T5 | R:R viável (dist ao POC) | \|price - POC\| | ≥ **1.5 × ATR_M5** | ≥ **0.75 × ATR_M5** | ATR_M5 | ❌ "R:R inviável" |

**Combinação para PASS:** T1 ✅ AND T2 ✅ AND T3 ✅ AND T4 ✅ AND T5 ✅ → `STRUCTURAL_REJECTION`

**NOTA IMPORTANTE (problema identificado):**
- T5 calcula R:R como `|price - POC_Session|`. Quando o POC Session está próximo do preço (ex: 6.75 pts), o R:R falha mesmo em caixote de 60+ pts.
- **Melhoria proposta:** Em TRANSIÇÃO, o target deveria ser a **borda oposta** do caixote ou o **POC D-1** (quando a borda trigger é Session), não apenas o POC mais próximo.

| Interaction Quality | Significado |
|---|---|
| STRUCTURAL_REJECTION | Rejeição confirmada na borda — absorção direcional oposta ao rompimento |
| NO_REJECTION | Volume presente mas sem dominância direcional (DR abaixo do threshold) |
| RR_UNVIABLE | Rejeição confirmada mas distância ao POC insuficiente para R:R |
| POSITIONAL_ONLY | Sem dados DZ — aprovado apenas por estar dentro do caixote |

**Regra do Target (N2 → N3):**
- Se borda trigger é Session (VAH/VAL) → target = POC Session
- Se borda trigger é D-1 (VAH_D1/VAL_D1) → target = POC D-1

---

### 4. BEAR

| # | Critério | Onde é medido | Threshold RTH | Threshold Globex | Buffer ATR | Resultado se FALHA |
|---|---|---|---|---|---|---|
| BE1 | Preço abaixo da VAL | current_price < VAL | Sim | Sim | — | ❌ BLOCKED |
| BE2 | Preço abaixo de -1σ VWAP | current_price < vwap_lower_1 | Sim | Sim | — | ❌ BLOCKED |
| BE3 | Preço abaixo do POC D-1 | current_price < d1_poc | Sim (se d1_poc > 0) | Sim | — | ❌ BLOCKED |
| BE4 | Z-Score na zona VAL/VWAP | DZ na VAL (fallback: VWAP) | Z > 1.0 | Z > 1.0 | — | ❌ WEAK_ACCEPTANCE |
| BE5 | Delta Ratio negativo forte | DR na zona VAL/VWAP | DR < -0.20 | DR < -0.20 | — | ❌ WEAK_ACCEPTANCE |

**Combinação para PASS:** BE1 ✅ AND BE2 ✅ AND BE3 ✅ AND BE4 ✅ AND BE5 ✅ → `BEARISH_ACCEPTANCE`
**Sem DZ disponível:** BE1 ✅ AND BE2 ✅ AND BE3 ✅ → `POSITIONAL_ONLY`

| Interaction Quality | Significado |
|---|---|
| BEARISH_ACCEPTANCE | Aceitação vendedora confirmada via order flow |
| WEAK_ACCEPTANCE | Posição OK mas DZ não confirma plenamente |
| POSITIONAL_ONLY | Sem dados DZ — aprovado apenas por posição |

---

### 5. CAPITULAÇÃO

| # | Critério | Onde é medido | Threshold RTH | Threshold Globex | Buffer ATR | Resultado se FALHA |
|---|---|---|---|---|---|---|
| CA1 | Preço esticado abaixo da VAL | current_price < VAL | Sim | Sim | — | ❌ BLOCKED |
| CA2a | Preço abaixo de -3σ VWAP | current_price < vwap_lower_3 | Sim | Sim | — | ❌ (ver CA2b) |
| CA2b | OU perto de Put Wall/ZGL | \|price - put_wall\|/price < 0.5% OU \|price - zgl\|/price < 0.5% | 0.5% de distância | 0.5% de distância | — | ❌ se CA2a também falha |
| CA3 | Z-Score de pânico | DZ na VAL (fallback: VWAP) | \|Z\| > 1.5 | \|Z\| > 1.5 | — | ❌ PANIC_ONGOING |
| CA4 | Delta Ratio migrando (exaustão) | DR na zona VAL/VWAP | DR > -0.10 | DR > -0.10 | — | ❌ PANIC_ONGOING |

**Combinação para PASS:** CA1 ✅ AND (CA2a ✅ OR CA2b ✅) AND CA3 ✅ AND CA4 ✅ → `EXHAUSTION_CONFIRMED`
**Sem DZ disponível:** CA1 ✅ AND (CA2a ✅ OR CA2b ✅) → `POSITIONAL_ONLY`

| Interaction Quality | Significado |
|---|---|
| EXHAUSTION_CONFIRMED | Volume de choque presente + vendedores enfraquecendo (DR migrando de extremo negativo para neutro) |
| PANIC_ONGOING | Pânico ainda ativo — DR muito negativo, vendedores ainda dominam |
| POSITIONAL_ONLY | Sem dados DZ |

---

## N3 — Execução (por Regime)

### 1. COMPLACÊNCIA → BUY

| # | Critério N3 | Threshold | Resultado se FALHA |
|---|---|---|---|
| CN1 | N2 IQ = BULLISH_ACCEPTANCE (ou POSITIONAL_ONLY) | ratio > 0 | ❌ WAIT |
| CN2 | OFI EMA 20s confirma agressão compradora | OFI > 0.3 | ❌ WAIT ("OFI EMA < 0.3") |

**PASS → BUY MARKET_PULLBACK**
- Entry: Market
- TP: Escalation ladder (VAH → Call Wall → +1σ → +3σ)
- SL: max(vwap_lower_1, vwap - ATR_M5)

---

### 2. BULL → BUY

| # | Critério N3 | Threshold | Buffer ATR | Resultado se FALHA |
|---|---|---|---|---|
| BN1 | Pullback até +1σ VWAP | price ≤ vwap_upper_1 + tolerance | **0.25 × ATR_M5** | ❌ WAIT ("Sem pullback") |
| BN2 | N2 IQ = BULLISH_ACCEPTANCE / STRONG | ratio > 0.10 AND Z > 0.5 | — | ❌ WAIT |
| BN3 | OFI EMA 20s forte | OFI > 0.4 | — | ❌ WAIT ("OFI < 0.4") |

**PASS → BUY MARKET**
- Entry: Market
- TP: Escalation ladder (Call Wall → +1σ → +3σ → VAH)
- SL: Implícito (não definido explicitamente no código)

---

### 3. TRANSIÇÃO → BUY ou SELL (Contra-Tendência)

**Gatilho Primário: Absorção N3 (DZ N3 extremes)**

| # | Critério N3 | Threshold | Resultado se FALHA |
|---|---|---|---|
| TN1a | N3 SELL_ABSORPTION detectada + preço acima do POC target | has_sell_absorption AND price > target_poc | → Tentar TN1b |
| TN1a+ | OFI confirma agressão compradora (contexto de venda) | OFI > 0.2 | ❌ WAIT ("OFI não confirma") |
| TN1b | N3 BUY_ABSORPTION detectada + preço abaixo do POC target | has_buy_absorption AND price < target_poc | → Tentar TN2 |
| TN1b+ | OFI confirma agressão vendedora (contexto de compra) | OFI < -0.2 | ❌ WAIT ("OFI não confirma") |

**PASS TN1a → SELL LIMIT** | **PASS TN1b → BUY LIMIT**
- Entry: Limit
- TP: POC da família do trigger (Session ou D-1)

**Gatilho Secundário: Rejeição Estrutural N2 (sem absorção N3)**

| # | Critério N3 | Threshold | Resultado se FALHA |
|---|---|---|---|
| TN2a | N2 IQ = STRUCTURAL_REJECTION na VAH/VAH_D1 + preço acima do POC target | — | ❌ WAIT |
| TN2b | N2 IQ = STRUCTURAL_REJECTION na VAL/VAL_D1 + preço abaixo do POC target | — | ❌ WAIT |

**PASS TN2a → SELL LIMIT** | **PASS TN2b → BUY LIMIT**
- Entry: Limit
- TP: POC da família do trigger

**Bloqueio especial:** Se Gamma é fallback (fictício) → absorção N3 ignorada (Walls não confiáveis)

**Gatilho Overnight (se N3 primário/secundário = WAIT):**

| # | Critério | Threshold |
|---|---|---|
| TON1 | Preço perto de ONH + (sell_absorption OU (OFI < -0.3 AND ratio < -0.10)) | \|price - ONH\| ≤ 0.5 × ATR_M1 |
| TON2 | Preço perto de ONL + (buy_absorption OU (OFI > 0.3 AND ratio > 0.10)) | \|price - ONL\| ≤ 0.5 × ATR_M1 |

**PASS TON1 → SELL LIMIT (TP = ON_POC)** | **PASS TON2 → BUY LIMIT (TP = ON_POC)**
- Só ativo na 1ª hora da NYSE com decaimento temporal (overnight_relevance > 0.1)

---

### 4. BEAR → SELL

| # | Critério N3 | Threshold | Buffer ATR | Resultado se FALHA |
|---|---|---|---|---|
| BEN1 | Repique até -1σ VWAP | price ≥ vwap_lower_1 - tolerance | **0.25 × ATR_M5** | ❌ WAIT ("Sem repique") |
| BEN2 | N2 IQ = BEARISH_ACCEPTANCE / STRONG | ratio < -0.10 AND Z > 0.5 | — | ❌ WAIT |
| BEN3 | OFI EMA 20s forte bearish | OFI < -0.4 | — | ❌ WAIT ("OFI > -0.4") |

**PASS → SELL MARKET**
- Entry: Market
- TP: Escalation ladder (Put Wall → -1σ → -3σ → VAL)

---

### 5. CAPITULAÇÃO → BUY (Fading)

| # | Critério N3 | Threshold | Resultado se FALHA |
|---|---|---|---|
| CAN1 | Absorção N3 no suporte (SELL_ABSORPTION) | has_sell_absorption = True | ❌ WAIT ("Sem absorção N3") |
| CAN2 | N2 exaustão confirmada | IQ = EXHAUSTION_CONFIRMED OU (ratio > -0.10 AND Z > 1.0) | ❌ WAIT |
| CAN3 | OFI virando (saindo de extremo bearish) | OFI > -0.2 | ❌ WAIT ("OFI still bearish") |

**PASS → BUY PASSIVE_ABSORPTION**
- Entry: Passive Absorption
- TP: vwap_lower_1 (se acima do preço) senão VWAP

**Bloqueio especial:** Se Gamma é fallback (fictício) → **TODAS as entradas bloqueadas** (Put Wall fictícia, fading impossível)

---

## Validação Universal N3 (Pós-Trade)

| Validação | Regra | Ação |
|---|---|---|
| TP atrás do preço (BUY) | TP ≤ current_price | TP escalado para próximo nível acima |
| TP atrás do preço (SELL) | TP ≥ current_price | TP escalado para próximo nível abaixo |

**Ladder de escalação LONG:** VAH → Call Wall → +1σ → +3σ → price + ATR
**Ladder de escalação SHORT:** Put Wall → -1σ → -3σ → VAL → price - ATR

---

## Resumo de Thresholds Globex vs RTH

| Parâmetro | RTH | Globex | Usado em |
|---|---|---|---|
| Delta Ratio (rejeição) — TRANSIÇÃO | ±0.25 | ±0.10 | N2 T4 |
| R:R mínimo — TRANSIÇÃO | 1.5 × ATR_M5 | 0.75 × ATR_M5 | N2 T5 |
| Delta Ratio — COMPLACÊNCIA | > 0.15 | > 0.15 | N2 C4 |
| Delta Ratio — BULL | > 0.20 | > 0.20 | N2 B5 |
| Delta Ratio — BEAR | < -0.20 | < -0.20 | N2 BE5 |
| Z-Score — COMPLACÊNCIA | > -0.5 | > -0.5 | N2 C3 |
| Z-Score — BULL/BEAR/TRANSIÇÃO | > 1.0 | > 1.0 | N2 B4/BE4/T3 |
| Z-Score — CAPITULAÇÃO | > 1.5 | > 1.5 | N2 CA3 |
| OFI — COMPLACÊNCIA | > 0.3 | > 0.3 | N3 CN2 |
| OFI — BULL | > 0.4 | > 0.4 | N3 BN3 |
| OFI — BEAR | < -0.4 | < -0.4 | N3 BEN3 |
| OFI — TRANSIÇÃO (absorção) | > 0.2 (sell) / < -0.2 (buy) | idem | N3 TN1a+/TN1b+ |
| OFI — CAPITULAÇÃO | > -0.2 | > -0.2 | N3 CAN3 |
| Pullback tolerance — BULL | 0.25 × ATR_M5 | 0.25 × ATR_M5 | N3 BN1 |
| Bounce tolerance — BEAR | 0.25 × ATR_M5 | 0.25 × ATR_M5 | N3 BEN1 |
| Overnight buffer | 0.5 × ATR_M1 | 0.5 × ATR_M1 | N3 TON1/TON2 |
| Borda D-1 ativação | \|d1_level - session_level\| > 0.5 × ATR_M5 | idem | N2 (Transição) |
| Near level tolerance | 0.3% do preço | 0.3% do preço | N3 (near_level helper) |

---

## Problema Identificado no Caso Atual (MES TRANSIÇÃO)

**Estado:** Price=6600, VAL=6587, VAH=6617, POC=6607, D1_VAH=6649, D1_VAL=6626, ATR_M5=11.95

**Bloqueio 1 — T4 (DR):** DR=-0.013 no VAL, threshold=0.25. Sem rejeição (absorção compradora ausente).
**Bloqueio 2 — T5 (R:R):** |6600 - 6607| = 6.75 pts < 17.92 (1.5 × 11.95). Target POC Session muito próximo.

**Root cause do R:R:** O target é fixamente o `POC Session`, mas num caixote de 60+ pts (6587→6649), o target real deveria ser a **borda oposta** ou **POC D-1** (6637), resultando em R:R de ~37 pts (> 17.92 ✅).
