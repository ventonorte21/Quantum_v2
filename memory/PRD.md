# PRD — Quantum Trading V3

## Problem Statement
Sistema de trading quantitativo Full-Stack (React + FastAPI + MongoDB) com Arquitetura V3 de 3 Níveis (Regime, Confirmação, Execução). Operações automatizadas em futuros micro (MES/MNQ) com session-awareness CME, auto-polling, replay engine e journal integrado.

## Core Architecture
- **Backend**: FastAPI (server.py ~9400 lines) + routes (auth, fills, replay) + services
- **Frontend**: React com activePanel overlays (AutoTrade, SignalStack, Replay, Journal, Pipeline Logs)
- **DB**: MongoDB (v3_orders, positions, signalstack_orders, replay_runs, v3_snapshots, v3_pipeline_logs)
- **Auth**: Emergent-managed Google Auth
- **External**: DataBento (market data), Telegram Bot, SignalStack (MOCKED)

## What's Been Implemented
- [x] V3 Engine (N1 Regime, N2 Confirmação, N3 Execução)
- [x] Session-Aware caching (VIX/VXN/VP/OFI com TTL por sessão CME)
- [x] Auto-polling com transição de sessão RTH/Globex/Halted
- [x] Replay Engine (corrigido: regime→símbolo, sem overlaps, duração>0)
- [x] Frontend activePanel overlays (sem reload destrutivo)
- [x] Auto Trading com background loop + auto-recovery
- [x] Journal/FillMonitor unificado
- [x] Code Quality Fixes (Abr 2026): 51 Python linter issues, frontend imports, key={i} fix, ESLint config
- [x] App.js refatorado: 1294 → ~860 linhas (extraiu SignalStackPanel, AutoTradingPanel)
- [x] **Pipeline Logs V3 (Abr 2026)**: Telemetria estruturada no pipeline N1/N2/N3 com instrumentação de latência, data quality tracking e storage no MongoDB (TTL 30d). APIs: GET /api/v3/pipeline-logs, GET /api/v3/pipeline-logs/stats, POST /api/v3/pipeline-logs/verbose. Frontend: PipelineLogsPanel com filtros, stats bar, expandable rows.

## P0/P1/P2 Features Remaining
### P0 (None pending)

### P1
- [ ] Testes unitários pipeline V3 + session boundaries (pytest)
- [ ] Aumentar cobertura de Type Hints Python
- [ ] Refatoração completa do server.py (9400+ lines → módulos)

### P2
- [ ] Auth 401 intermitente no /api/auth/me
- [ ] VP/GEX Vetorizado (otimizar Pandas iterations)
- [ ] Fix pre-existing DataBentoChart locale issue (en-US@posix)

### P3 (Backlog)
- [ ] Real Breadth ($TICK) integration via proxy/Yahoo Finance
- [ ] ML no N1 (HMM/GMM) para Roteamento de Regime
- [ ] Shadow Mode para auto trading
