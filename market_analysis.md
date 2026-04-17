# Quantum Trading Scalp — Análise de Mercado

Registo diário de sessões RTH. Dados reais do live feed DataBento (MNQ/MES micro futuros).  
Engine: Bi-modo FLOW + ZONES | Conta paper: $30 000 | Risco/trade: 1% | Sizing: risk_pct dinâmico.

---

## 2026-04-13 (Segunda-feira)

### Contexto de Mercado

| | MNQ | MES |
|---|---|---|
| **D-1 High** | 25 393,50 | 6 883,00 |
| **D-1 Low** | 25 222,00 | 6 846,25 |
| **Range D-1** | 171,50 pts | 36,75 pts |
| **Regime RTH** | EXPANSION BULL | EXPANSION BULL |
| **Zona VWAP** | ABOVE 3SD | ABOVE 3SD |

Ambos os símbolos em regime de expansão bullish. Preço acima da banda superior do VWAP — contexto de momentum comprador mas sobrecomprado relativamente aos desvios padrão. D-1 do MES encerrou exactamente no High (6 883,00 = d1_high do dia corrente), criando resistência dinâmica relevante.

---

### Actividade de Sinais — Sessão RTH (177 min)

| Métrica | MNQ | MES |
|---|---|---|
| **Snapshots RTH** | 1 079 | 1 080 |
| **ACTIVE_SIGNAL** | 104 | 159 |
| **Taxa de activação** | 9,6% | 14,7% |
| **MODERATE** | 72 (69%) | 100 (63%) |
| **STRONG** | 32 (31%) | 59 (37%) |
| **ZONES activas** | poucas (< 10) | poucas (< 10) |

MES gerou proporcionalmente mais sinais activos. A maioria dos sinais foi MODERATE — expectável em dia de expansão sem recuos limpos. Sinais ZONES foram escassos: preço manteve-se longe das zonas de interesse (POC, VWAP, ON levels) durante toda a sessão.

> `delta_ratio = None` em todos os snapshots — acumuladores Welford ainda em warm-up após restart. Filtro delta não aplicado nesta sessão.

---

### Parâmetros SL/TP (Modo FLOW)

| | MNQ | MES |
|---|---|---|
| **SL** | 6 ticks (7,50 pts) | 4 ticks (1,00 pt) |
| **TP** | 10 ticks (12,50 pts) | 8 ticks (2,00 pts) |
| **BE** | 4 ticks (5,00 pts) | 3 ticks (0,75 pt) |
| **R:R implícito** | 1:1,67 | 1:2,00 |

---

### Resultados Paper Trading

> **Nota técnica:** Bug crítico G-2 identificado e corrigido durante esta sessão (ver secção Bug).  
> Os trades abaixo foram todos executados no período pós-correcção.

| Métrica | Valor |
|---|---|
| **Total trades** | 61 |
| **Wins** | 13 |
| **Losses** | 43 |
| **Break-even** | 5 |
| **Win rate** | 21,3% |
| **PnL total** | −$898 |
| **PnL médio/trade** | −$14,7 |
| **Melhor trade** | +$150 (MES BUY @ 6 885,00 — STRONG) |
| **Pior trade** | −$355 (MNQ SELL @ 25 407,25 — gap de SL) |

#### Breakdown por Símbolo

| | MNQ | MES |
|---|---|---|
| Trades | ~30 | ~31 |
| Regime dominante | BEARISH + BULLISH misto | BULLISH |
| SL ticks pequenos | Não (6 ticks) | Sim (4 ticks = 1 pt) |

#### Observações dos Resultados

- **Win rate de 21%** num regime de expansão bullish com FLOW puro é sintomático de mercado em tendência onde contratrend signals são frequent. O engine FLOW entra em ambas as direcções (BUY e SELL) — em dia de strong bull run, os SELL signals têm taxa de sucesso baixa.
- **Outlier -$355** (MNQ SELL @ 25 407,25, pnl_pts = -17,75): loss de ~2,4× o SL normal (7,5 pts). Em paper trading, o monitor de posições verifica SL/TP a cada 2 segundos — gap de preço entre ciclos pode produzir slippage simulado superior ao SL configurado. A investigar.
- **MES SL = 1 pt (4 ticks)**: extremamente apertado para micro futures com ATR_m1 de ~5 pts. A maior parte das perdas MES são losses imediatos de 1 pt. Considerar aumentar para 6-8 ticks.

---

### Níveis Estruturais para Amanhã

| Nível | MNQ | MES |
|---|---|---|
| **D-1 High (resistência)** | 25 393,50 | 6 883,00 |
| **D-1 Low (suporte)** | 25 222,00 | 6 846,25 |
| **POC D-1 (MNQ)** | 25 271,75 | — |
| **POC D-1 (MES)** | — | 6 856,00 |
| **POC sessão** | 25 319,00 | 6 856,50 |

O preço de fecho (MNQ ~25 420, MES ~6 887) está **acima do D-1 High** em ambos os símbolos — breakout técnico. Se confirmado na abertura de amanhã, os níveis D-1 High tornam-se suporte dinâmico.

---

### Bug Identificado e Corrigido

**G-2 quality filter bloqueava 100% dos trades desde o deploy inicial.**

- **Causa raiz:** Python 3.12 alterou o comportamento de `str(StrEnum)`. `str(ScalpSignalQuality.MODERATE)` passou a retornar `"ScalpSignalQuality.MODERATE"` em vez de `"MODERATE"`. O dicionário `_QUALITY_RANK` não continha esta chave → lookup retornava -1 → todos os sinais falhavam a comparação G-2.
- **Impacto:** ~100% de sessões RTH anteriores sem execução. Os logs `AutoTrader RISK%` eram reais mas todos os trades eram silenciosamente descartados a seguir.
- **Correcção:** Função `_normalize_quality()` extrai `.value` do enum antes de qualquer comparação. Função agora é robusta a qualquer forma de string de qualidade.
- **Colateral:** Logging G-2 promovido de DEBUG para INFO. Retorno silencioso em `_execute_trade` agora emite WARNING com campos exactos. Auto-start no reinício do backend implementado via `auto_trade: True` no config.

---

## Análise Detalhada — Comportamento do Modelo (2026-04-13)

> Baseada em 2 380 snapshots RTH + 119 trades paper armazenados no Atlas.

---

### 1. Detecção de Regime (S1)

#### O problema do NO_DATA (52% da sessão)

| Regime | MNQ | MES | % do total |
|---|---|---|---|
| **NO_DATA** | 620 | 620 | **52,1%** |
| NEUTRAL | 355 | 309 | 27,9% |
| BULLISH_FLOW | 115 | 128 | 10,2% |
| BEARISH_FLOW | 100 | 133 | 9,8% |

Mais de metade dos snapshots RTH retornaram `NO_DATA` — o detector S1 não tinha dados suficientes para calcular regime. Estes períodos correspondem ao warm-up dos buffers de tick data no início da sessão. O engine está totalmente inactivo durante este tempo: nenhum sinal é gerado, o auto trader não executa. Efectivamente, a sessão útil de hoje foi de apenas ~85 minutos (dos 177 min totais).

**Confidence médio por regime:**

| Regime | MNQ conf médio | MES conf médio | Percentil 25 | Percentil 75 |
|---|---|---|---|---|
| BULLISH_FLOW | 5,22 | 5,67 | 4,30–4,40 | 4,90–8,50 |
| BEARISH_FLOW | 4,83 | 5,49 | 4,30–4,40 | 4,70–5,00 |
| NEUTRAL | 0,42 | 0,41 | 0,20 | 0,60 |
| NO_DATA | 0,00 | 0,00 | — | — |

O confidence mínimo para identificar FLOW está na zona dos 4,0–4,5. O limite actual parece adequado, mas o p25 de 4,30 indica que muitos sinais FLOW estão próximos do threshold de corte — são regimes "fracos". O p75 do MES em BULLISH_FLOW (8,50) vs MNQ (4,90) indica que o MES mostrou convicção bullish muito superior ao MNQ hoje.

**Threshold de calibração recomendado:** Elevar o mínimo de confidence S1 de 4,0 para **4,5** filtraria os regimes de baixa convicção sem eliminar os sinais de qualidade.

---

### 2. Filtros S2 — O que está a bloquear sinais

#### Principais causas de bloqueio (não-activos)

| Razão | MNQ | MES |
|---|---|---|
| **Preço fora de todas as zonas** | 543 | 465 |
| Score muito baixo (< 1,5) | 27 | 75 |
| Gamma opõe momentum (LONG_GAMMA) | 42 | 21 |
| Confidence S1 baixa (4,1–4,4) | 54 | 55 |
| F6 Flow Gate: score < 1,5 | ~11 | ~16 |

**Descoberta crítica:** "Preço fora de todas as zonas de interesse" é o maior bloqueador (543 MNQ + 465 MES = 1 008 casos). Isto confirma que o modo **ZONES está efectivamente inactivo**: durante toda a sessão de expansão bullish, o preço nunca regressou às zonas estruturais (POC, VWAP, ON levels). O engine ZONES é arquitecturalmente correcto mas inadequado para dias de tendência forte.

#### Sinais activos que ainda têm warnings S2

Os 136 sinais activos MNQ e 193 MES passaram S2 mas carregam avisos:
- **"S1 confidence baixa (4,2–4,4)"** aparece em 23 sinais MNQ e 17 MES activos — estes sinais passaram o threshold mas são marginais
- **"OFI slow não alinhado"** em 8 sinais (MNQ+MES) — o OFI lento contradiz a direcção do sinal

---

### 3. OFI — Diferenciação entre Activos e Não-Activos

| | MNQ Activos | MNQ Não-activos | MES Activos | MES Não-activos |
|---|---|---|---|---|
| **OFI_fast média** | +0,0505 | −0,0098 | −0,0020 | −0,0081 |
| **OFI_fast std** | 0,3023 | 0,1652 | 0,3355 | 0,1864 |
| **OFI_slow média** | +0,0268 | +0,0020 | +0,0047 | +0,0066 |

**Observações:**
- MNQ activos têm OFI_fast médio positivo (+0,05) vs não-activos negativo (−0,01) — o engine está a seleccionar correctamente períodos de fluxo comprador para o MNQ
- MES activos têm OFI quase neutro (−0,002) — o MES estava em equilíbrio de fluxo mesmo nos momentos de sinal, sugerindo que o filtro OFI não foi suficientemente exigente
- A standard deviation dos activos é quase 2× a dos não-activos em ambos os símbolos — o engine selecciona momentos de maior volatilidade de fluxo, que é o esperado para scalp

**Calibração OFI sugerida:** Para MES, a threshold actual de OFI está demasiado permissiva. Um mínimo de `|ofi_fast| > 0,15` reduziria os sinais fracos sem eliminar os melhores.

---

### 4. Sizing e SL/TP — Dimensionamento Real

| | MNQ (124 activos) | MES (193 activos) |
|---|---|---|
| **SL médio** | 1,55 pts (6,2 ticks) | 1,04 pts (4,2 ticks) |
| **SL mínimo** | 1,50 pts (6 ticks) | 1,00 pt (4 ticks) |
| **SL máximo** | 8,60 pts (34,4 ticks) | 2,92 pts (11,7 ticks) |
| **TP médio** | 2,62 pts (10,5 ticks) | 2,11 pts (8,5 ticks) |
| **R:R médio** | 1,670 | 2,013 |
| **R:R mínimo** | 1,667 | 2,000 |

O SL mínimo bate no piso configurado (6 ticks MNQ / 4 ticks MES). Os valores máximos (8,6 pts MNQ / 2,9 pts MES) correspondem a sinais com ATR elevado onde o SL é ajustado dinamicamente pelo ATR. O R:R está estável e próximo do configurado (1:1,67 / 1:2,00).

**Bug identificado: `qty = 0` em todos os 119 trades.** O campo quantidade não está a ser guardado no documento de trade. O PnL em USD foi calculado correctamente (implica qty=10 calculado internamente) mas o registo em base de dados não persiste este valor. Investigar `_execute_trade()`.

---

### 5. Performance por Qualidade, Regime e Direcção

#### Por Qualidade de Sinal

| Qualidade | Trades | Wins | Losses | BE | WR | PnL |
|---|---|---|---|---|---|---|
| **STRONG** | 29 | 9 | 16 | 4 | **36%** | −$20 |
| **MODERATE** | 90 | 23 | 57 | 10 | **29%** | −$618 |

STRONG quase atinge o break-even (−$20 em 29 trades). MODERATE perde consistentemente. O **threshold de break-even matemático** para este R:R (1,72:1) é WR = 1/(1+1,72) = **36,8%**. MODERATE a 29% está 7,8 pontos percentuais abaixo. Elevar `min_quality_to_execute` para `STRONG` teria reduzido o drawdown de $618 e deixado apenas os 29 trades STRONG com resultado quase neutro.

#### Por Regime de Mercado

| Regime | Trades | WR | PnL |
|---|---|---|---|
| **BULLISH_FLOW** | 68 | **35%** | **+$275** |
| **BEARISH_FLOW** | 51 | **23%** | **−$912** |

Este é o dado mais revelador da sessão. Em dia de expansão bullish (preço acima 3SD VWAP o dia inteiro):
- Trades BUY em BULLISH_FLOW → **lucrativos** (+$275)
- Trades SELL em BEARISH_FLOW → **destrutivos** (−$912)

O engine entrou em SELL/BEARISH contra a tendência intra-dia dominante. Num dia com preço acima do VWAP 3SD o dia inteiro, os sinais BEARISH_FLOW são ruído estrutural, não oportunidades reais.

#### Por Direcção

| Direcção | Trades | WR | PnL |
|---|---|---|---|
| **BUY** | 68 | 35% | **+$275** |
| **SELL** | 51 | 23% | **−$912** |

Confirma: os SELL são o problema. Sem os SELL, a sessão seria **+$275** (lucrativa).

#### Por Símbolo

| Símbolo | Trades | WR | PnL |
|---|---|---|---|
| **MNQ** | 67 | 32% | −$550 |
| **MES** | 52 | 28% | −$88 |

MES quase neutro (−$88), MNQ negativo (−$550). O outlier de −$355 (MNQ SELL) distorce muito o MNQ. Sem o outlier, MNQ teria −$195.

---

### 6. Distribuição de SL hits e Outliers

```
TP hits (wins):   2,0 × 9 | 2,5 × 6 | 2,75 × 4 | 3,0 × 4 | 3,25 × 3 | 3,5 × 2 | 4,25 | 5,5
SL hits (losses): 1,0 × 22 | 1,25 × 6 | 1,5 × 14 | 1,75 × 4 | 2,0 × 3 | 2,5 × 6 | 3,0 | 4,25 | 5,5 | 17,75
```

73 SL hits com média −1,61 pts. Dois outliers acima do SL esperado: **−5,5 pts** e **−17,75 pts**. Estes representam casos onde o monitor de posições (ciclo de 2s) não apanhou o SL a tempo — o preço já tinha ultrapassado o nível de stop quando o ciclo executou. Em paper trading isto é aceitável como simulação de slippage/gap, mas precisam de `close_reason` e `close_price` guardados para auditoria.

**Outlier crítico: MNQ SELL @ 25 407,25 | pnl = −17,75 pts | −$355**
- SL estava em 25 408,75 (1,5 pts acima da entrada)
- Preço subiu para ~25 425 entre ciclos de 2s
- `close_price = None`, `close_reason = None` — posição fechada sem registo
- Investigar: o monitor deve guardar o preço real de fecho e a razão

---

### 7. VWAP Zones — Onde Estavam os Sinais

| Zona VWAP | MNQ activos | MES activos |
|---|---|---|
| **ABOVE_3SD** | 51 (37%) | 78 (40%) |
| BETWEEN_2SD_3SD_BULL | 41 (30%) | 57 (30%) |
| BETWEEN_1SD_2SD_BULL | 28 (21%) | 44 (23%) |
| BETWEEN_VWAP_1SD_BULL | 6 (4%) | 7 (4%) |
| Zonas bear / abaixo | ≤ 5 | ≤ 5 |

**67% dos sinais activos** ocorreram com o preço **acima do 2SD superior** do VWAP. O engine está a gerar sinais maioritariamente em zonas de sobrecompra relativa ao VWAP. Em dia de tendência isto é normal (o preço fica estendido), mas em dias laterais estes sinais teriam muito maior probabilidade de reversão — o que tornaria o ABOVE_3SD um filtro de supressão a considerar.

---

### 8. Curva de Equity — Narrativa da Sessão

```
Trades 1–10:   +$372  (arranque forte, primeiros wins com STRONG)
Trades 11–23:  −$602  (declínio acentuado, 13 losses seguidos, outlier −$355)
Trades 24–39:  −$284  (tentativa de recuperação, inconclusiva)
Trades 40–69:  −$530  (drawdown máximo ~−$1 140, série negativa)
Trades 70–82:  +$320  (recuperação parcial com STRONG wins consecutivos)
Trades 83–101: +$218  (oscilação, alguns wins MES e MNQ grandes)
Trades 102–119: −$192  (deterioração no fecho RTH)
```

O pico de equity foi +$372 (trade 10). O fundo foi ~−$1 140 (trade 70). A recuperação final para −$638 deve-se a uma série de STRONG wins e alguns MODERATE wins no final da tarde.

---

### 9. Ajustes Identificados e Prioridades

| Prioridade | Ajuste | Impacto Estimado |
|---|---|---|
| 🔴 **CRÍTICO** | Elevar `min_quality_to_execute` → STRONG | Elimina −$618 de perdas MODERATE |
| 🔴 **CRÍTICO** | Corrigir persistência de `qty` no trade doc | Auditoria correcta de sizing |
| 🔴 **CRÍTICO** | Guardar `close_price` e `close_reason` sempre | Rastreabilidade de outliers |
| 🟠 **ALTO** | Filtro de bias diário: suprimir SELL em dias ABOVE_2SD VWAP | Elimina −$912 de SELL losses |
| 🟠 **ALTO** | Elevar threshold S1 confidence: 4,0 → 4,5 | Remove regimes de baixa convicção |
| 🟡 **MÉDIO** | MES OFI mínimo: `|ofi_fast| > 0,15` | Reduz sinais fracos MES |
| 🟡 **MÉDIO** | NO_DATA warm-up: suprimir auto-trader nos primeiros 20 min RTH | Evita entradas sem regime |
| 🟡 **MÉDIO** | ZONES mode: implementar `_check_obstacle_between()` | Melhora qualidade ZONES |
| 🟢 **BAIXO** | ATR adaptativo: no mínimo 8 ticks SL se ATR_m1 > 3 pts (MES) | Evita SL ultra-apertados |
| 🟢 **BAIXO** | `delta_ratio` Welford: aguardar 2–3 dias de dados para activar | Adiciona filtro de convicção |

---

### Acções para Próxima Sessão

- [x] ~~Analisar win rate STRONG vs MODERATE separadamente~~ → STRONG WR=36% / MODERATE WR=29% (confirmado)
- [x] ~~Corrigir `s1_regime = NO_DATA` em modo ZONES~~ → **RESOLVIDO 2026-04-13 noite** (ver secção Fixes)
- [x] ~~Implementar filtro G-7 warm-up (primeiros 15 min RTH)~~ → **RESOLVIDO 2026-04-13 noite**
- [x] ~~Implementar filtro G-8 bias diário (suprimir SELL em ABOVE_1SD/2SD)~~ → **RESOLVIDO 2026-04-13 noite**
- [ ] **Elevar `min_quality_to_execute` para STRONG** na próxima sessão RTH — verificar no config UI
- [ ] **Corrigir `close_price=None`** para trades LIVE — sem callback de fill do broker (SignalStack)
- [ ] **Elevar S1 confidence mínimo** de 4,0 → 4,5 no config AutoTune RTH
- [ ] **MES ATR SL**: se ATR_m1 < 2,0 pts → SL mínimo = 6 ticks (actual 4 ticks demasiado apertado)
- [ ] **ZONES `_check_obstacle_between()`**: melhora qualidade de sinais ZONES

---

### Fixes Aplicados — 2026-04-13 (pós-sessão)

#### Fix 1: `s1_regime = NO_DATA` em modo ZONES (bug arquitectural)

**Causa raiz confirmada:** O branch `ScalpMode.ZONES` em `evaluate()` (linhas 1055–1180)
nunca chamava `evaluate_s1_flow()`. O campo `signal.s1_regime` ficava no valor inicial
`ScalpRegime.NO_DATA` para 100% dos snapshots ZONES.

Impacto: 52% de todos os snapshots (todos os 620 docs ZONES/símbolo) tinham
`s1_regime = NO_DATA`. O AutoTune ficava cego — não sabia se a zona foi tocada em
regime bullish, bearish ou neutro. Os 9 campos de S1 (regime, confidence, direction)
eram invariavelmente nulos.

**Correcção aplicada** (`scalp_engine.py`, linhas 1181–1196):
Após o branch ZONES completar, chamar `evaluate_s1_flow(live_data)` (função pura, O(1),
sem I/O) e popular `signal.s1_regime`, `signal.s1_confidence` e `signal.s1_direction`
(preservando a direcção da zona se já definida por ACTIVE_SIGNAL ZONES).

A partir de amanhã, todos os snapshots ZONES terão S1 context completo:
`BULLISH_FLOW`, `BEARISH_FLOW`, `NEUTRAL` ou `NO_DATA` apenas quando a feed falha.

#### Fix 2: Gate G-7 — Warm-up Filter (primeiros 15 min RTH)

**Problema:** Nos primeiros 15 minutos RTH, dados VWAP/VP incompletos, volatilidade de
abertura alta e regime S1 instável. A sessão 2026-04-13 mostra concentração de
perdas no período inicial.

**Correcção aplicada** (`scalp_auto_trader.py`, linhas 634–647):
Novo gate G-7: se `signal.session_minutes < warmup_minutes` (default 15) → sinal
ignorado. Configurável via `warmup_minutes` no config (0 = desactivado).

#### Fix 3: Gate G-8 — Daily Bias Filter (suprimir SELL em mercado bullish)

**Problema:** Em dias com `vwap_zone ∈ {ABOVE_2SD, ABOVE_1SD}`, o fluxo macro é
claramente bullish. Estatística 2026-04-13: SELL WR=23% (−$912) vs BUY WR=35%
(+$275). Executar SELL contra o bias de VWAP destruiu capital que BUY ganhou.

**Correcção aplicada** (`scalp_auto_trader.py`, linhas 649–670):
Novo gate G-8: se `vwap_zone` da zona em que a entrada ocorreria está na lista de
bias zones E a acção é a acção suprimida → sinal ignorado. Configurável via:
- `bias_filter_enabled` (default `True`)
- `bias_filter_vwap_zones` (default `["ABOVE_2SD"]`) — **ABOVE_1SD excluído pendente validação**
- `bias_filter_suppress` (default `"sell"`)

> ⏰ **REVISÃO AGENDADA: semana de 2026-04-20**
> Depois de 1 semana de dados ao vivo, rever se o threshold ABOVE_1SD também justifica
> bloquear SELLs. Analisar: (1) quantos SELLs foram executados em ABOVE_1SD; (2) WR e
> PnL desses SELLs; (3) se WR < 35% → adicionar ABOVE_1SD à lista; (4) se WR ≥ 40%
> em ABOVE_1SD → manter excluído. Ajustar `bias_filter_vwap_zones` no config UI.

**Impacto estimado combinado G-7 + G-8:** elimina a maioria das −$912 de SELL
losses do dia 2026-04-13. Break-even WR necessário = 36,8%; STRONG WR = 36% — com
os filtros activos, o efectivo WR operante deve subir acima do break-even.

---

*Sessão registada automaticamente. Dados: MongoDB Atlas `quantum_trading` / `scalp_snapshots` + `scalp_trades`.*


---

## 2026-04-14 — Implementação EMA Pullback Zones (F7)

### Análise Crítica da Proposta

Antes da implementação, análise detalhada identificou dois pré-condições bloqueantes e quatro pontos de ajuste:

| Ponto | Resolução |
|---|---|
| SL não definido para EMA | `SL = EMA_21 ± 0.6×ATR_m1`, fixo no momento da entrada |
| Day regime não operacionalizado | Já operacionalizado: `s1_regime=BULLISH_FLOW + price≥vwap` |
| EMA 8 como zona (lag insuficiente) | **Eliminada** — substituída por EMA 9 como trigger dentro da EMA 21 |
| Warm-up de 20 min | **Corrigido para 39 min** (EMA_34 + 5 barras buffer) |
| ATR sem cap absoluto | `min(0.4×ATR, cap)` — MNQ cap=4.0pts, MES cap=1.0pt |
| Cooldown por tipo inadequado | **Cooldown por coordenada de preço**: sem re-entrada se EMA21 dentro de 2×ATR do último trade em 180s |

### Arquitectura EMA Implementada

**EMAs:**
- EMA 21: nível de zona primário (suporte/resistência dinâmico)
- EMA 34: confirmador de cluster (convergência com EMA 21 → boost)
- EMA 9: trigger de entrada (preço toca EMA 9 enquanto EMA 21 próxima)

**Qualidade por convergência (como boost para o scoring OFI/CVD existente):**
- EMA9 + EMA21 + EMA34 dentro do limiar → base_boost = 1.0 (potential STRONG)
- EMA21 + EMA34 (sem EMA9 trigger) → base_boost = 0.5 (potential MODERATE)
- EMA21 + EMA9 trigger → base_boost = 0.0 (depende exclusivamente de OFI/CVD)
- EMA21 sozinha → base_boost = 0.0, priority=4

**Bónus VWAP proximity:** EMA_21 dentro de 0.30×ATR de um nível VWAP → +0.5 pts.

**SL fixo:** `EMA_21_value ± 0.6 × ATR_m1` no momento do toque.

**BE:** 0.4 × ATR_m1.

### Gates Obrigatórios (todos AND)

1. Barras M1 ≥ 39 (warm-up EMA 34 + buffer)
2. Sessão RTH ≥ 39 minutos
3. s1_regime = BULLISH_FLOW (LONG) ou BEARISH_FLOW (SHORT)
4. VWAP context: LONG exige price ≥ vwap; SHORT exige price ≤ vwap
5. OFI fast > 0.30 direcional (AND gate — zona em `_BREAK_ZONE_TYPES`)
6. Cooldown de coordenada de preço: sem re-entrada no mesmo nível em 180s

### Ficheiros modificados

- `backend/services/scalp_zones.py`: ZoneType.EMA_PULLBACK_BUY/SELL, `_compute_ema()`, `_build_ema_pullback_zones()`, cooldown por preço, injecção em `evaluate_zones()`
- `backend/services/scalp_engine.py`: `bars_z` sempre construído (antes era só no cache miss); passagem de `m1_bars` e `s1_regime_value` para `evaluate_zones()`

### Regra de revisão

> ⏰ **REVISÃO EMA AGENDADA: semana de 2026-04-20** (simultânea com revisão G-8 ABOVE_1SD)
> Analisar: (1) quantos sinais EMA foram gerados (target: >20/semana em dias BULLISH/BEARISH_FLOW); 
> (2) WR dos trades EMA vs static ZONES; (3) se WR EMA < 30% → rever threshold OFI ou desactivar;
> (4) se WR EMA ≥ 36.8% → manter; (5) ajustar `EMA_TOUCH_ATR_MULT` se frequência de activação 
> for 0 (muito restritivo) ou >5×/sessão (muito permissivo).

### Correcções aplicadas após análise crítica F7-v2 (2026-04-14)

| Ponto | Antes | Depois |
|---|---|---|
| TP EMA pullback | `target=None` (fallback ATR×1.5) | **TP explícito:** `EMA_21 ± 1.5×ATR_m1` → R:R = 2.5:1 declarado |
| Cap de toque MNQ | 4.0 pts (só activava com ATR>10pts) | **2.5 pts** (activa em ATR>6.2pts — condições de alta volatilidade normais) |
| Cap de toque MES | 1.0 pt (só activava com ATR>2.5pts) | **0.8 pts** (activa em ATR=2.0pts — sessão de abertura RTH) |
| Cooldown interacção | Implícita, não documentada | **Documentada:** zone-type cooldown + price-coord cooldown coexistem; o mais restritivo que bloquear primeiro prevalece |

**Ressalva documentada:** `s1_regime=BULLISH_FLOW + ABOVE_VWAP` é uma aproximação de "dia de tendência" — não equivale a trending day estabelecido. OFI pode classificar BULLISH_FLOW num range day com spike de fluxo. O AutoTune revelará isto nos dados: se WR em range days for <30%, o gate de regime precisará de refinamento (ex: filtro de ATR acumulado ou distância ao VWAP em σ).

---

## 2026-04-14 — Audit pós-sessão + Bug Fixes

### Bug #1 — `zone_type=None` em 100% dos trades ZONES (CORRIGIDO)

**Raiz:** `scalp_auto_trader.py` e `routes/scalp.py` gravavam `active_zone.get("zone_type")` mas o dicionário `best_zone` retornado por `evaluate_zones()` usa a chave `"type"` (não `"zone_type"`). Resultado: 32 trades ZONES gravados hoje com `zone_type=None`, tornando o breakdown `by_zone_type` do AutoTune cego para o modo ZONES.

**Fix:** `.get("zone_type")` → `.get("type")` nos dois ficheiros. Trades futuros passam a gravar `VWAP_PULLBACK_BUY`, `EMA_PULLBACK_BUY`, etc. Os 32 trades históricos permanecem com `None` (pré-fix).

**Validação:**
```
zone_type com chave antiga ("zone_type"): None   ← era sempre None
zone_type com chave nova   ("type"):      'VWAP_PULLBACK_BUY'  ← agora correcto
EMA zone_type: 'EMA_PULLBACK_BUY'
```

### Bug #2 — `s1_regime` com prefixo de classe em 3 trades históricos (HISTÓRICO)

3 trades de `2026-04-13 16:50-16:51 UTC` gravaram `"ScalpRegime.BULLISH_FLOW"` em vez de `"BULLISH_FLOW"`. O `_normalize_quality()` actual trata correctamente este caso via `.split(".")[-1]`. Bug pré-existente em código anterior; não afecta trades actuais.

### Campos `ema_block_reasons` no snapshot

`zones.ema_block_reasons` está a ser gravado correctamente (empty list `[]` na maior parte dos snapshots porque ainda não houve activação de blocos EMA Gate 5/6 com volume suficiente). O audit que reportava 100% "missing" confundia `[]` com ausência do campo — o campo existe.

### Estado pós-fix

| Ficheiros | Mudança |
|---|---|
| `backend/services/scalp_auto_trader.py` linha 787 | `.get("zone_type")` → `.get("type")` |
| `backend/routes/scalp.py` linha 479 | `.get("zone_type")` → `.get("type")` |
| `backend/_tmp_*.py` | limpos |

---

## 2026-04-14 — Diagnóstico Globex + Validação Combined Service

### Bug #3 — `auto_trade: False` (CORRIGIDO 01:57 UTC)

O PATCH enviado em 01:46 UTC incluiu `auto_trade=False` (default Pydantic para campo não enviado), parando o loop. Corrigido via PATCH seguro com todos os campos explícitos às 01:57 UTC.

**Prevenção:** Sempre incluir `auto_trade: true` + todos os campos não-nulos no payload PATCH para `/api/scalp/config/save`.

### Arquitectura MongoDB confirmada

- Backend usa **Atlas** (`MONGO_URL_ATLAS`) via `start_backend.sh` (fallback para `localhost:27017` sem Atlas)
- DB name: `quantum_trading`
- Local MongoDB (port 27017) está vazio — apenas o Atlas tem dados de produção
- Queries de diagnóstico devem usar `MONGO_URL_ATLAS`, não `localhost`

### Combined Service — estrutura de dados correcta

O `scalp_combined_history` armazena resultados em `doc.analysis` (dict plano, não nested em `sample`):

| Campo (flat) | Valor actual (MNQ, 90d) |
|---|---|
| `n_trades` | 74 |
| `overall_win_rate` | 35.1% |
| `zones_win_rate` | **85.7%** (n=7) |
| `zones_avg_pnl` | **11.0 pts** |
| `n_zones` | 7 |
| `strong_win_rate` | 44.4% (n=18) |
| `moderate_win_rate` | 32.7% (n=55) |

O `by_zone_type` mostra `{'UNKNOWN': {n:74, wr:35.1%}}` porque todos os 7 trades ZONES pré-fix tinham `zone_type=None` (substituído por `'UNKNOWN'` via `$ifNull`). Populará correctamente após próximo RTH com trades pós-fix.

### Desempenho ZONES (7 trades em Globex, pré-fix)

| Qualidade | n | Wins | WR | Avg PnL (pts) |
|---|---|---|---|---|
| MODERATE | 5 | 4 | 80.0% | ~9.5 (ganhos) / -0.75 (perda) |
| STRONG | 2 | 2 | 100.0% | 15.12 |
| **Total** | **7** | **6** | **85.7%** | **11.0** |

> ⚠️ Amostra pequena (n=7) e toda em Globex — WR elevado pode reflectir baixa volatilidade overnight. Validar com trades RTH pós-fix antes de qualquer conclusão.

### ACTIVE_SIGNAL Globex não executados (comportamento correcto)

MES disparou ACTIVE_SIGNAL às 01:54-01:55 UTC (6924.75, SESSION_POC_BUY + SESSION_VAL_FADE). Auto_trader NÃO executou por:
- `rth_only: true` — bloqueio correcto em Globex
- `globex_auto_enabled: false` — sem execução automática overnight
- `auto_trade: false` — bug adicional (já corrigido)

Próxima execução esperada: **segunda 14:30 UTC** (9:30am ET, abertura RTH).

### Snapshot `zones.active_zone` verificado

Estrutura confirmada correcta no MongoDB:
```json
"active_zone": {
  "type": "SESSION_POC_BUY",
  "level": 6925.25,
  "direction": "LONG",
  "label": "...",
  "priority": 1
}
```
A chave é `"type"` (não `"zone_type"`). Fix confirmado estruturalmente.

### Agenda 2026-04-20 (mantida)

1. **G-8 ABOVE_1SD threshold** — validar se o threshold actual é demasiado permissivo/restritivo
2. **EMA zones WR/frequência** — target >20 trades EMA/semana; WR mínimo 36.8%
3. **BEARISH_FLOW WR** — WR=17.3% (52 trades) vs break-even 36.8% — necessita confirmação
4. **`by_zone_type` AutoTune** — primeiro breakdown real por zona com dados pós-fix (segunda, RTH)
5. **ZONES RTH WR** — confirmar se 85.7% Globex WR se mantém em RTH com maior volatilidade
