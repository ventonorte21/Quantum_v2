"""
ScalpAutoTrader — Loop de Auto Trading para Quantum Trading Scalp
=================================================================
Executa sinais S1/S2/S3 automaticamente (paper ou real via SignalStack).

Funcionalidades:
  - Loop configurable (padrão 5s) que avalia sinais para cada símbolo ativo
  - Cooldown entre trades por símbolo (evita over-trading)
  - Limite de posições abertas simultâneas (global e por símbolo)
  - Paper trading: fill simulado imediato, resolução por monitoramento de preço
  - Real trading: ordem bracket OCO enviada ao SignalStack/Tradovate
  - Monitoramento de SL/TP em tempo real (live price do DataBento)
  - PnL em pontos e em dólares (MNQ=$2/pt | MES=$5/pt)
  - Registro completo de cada trade em `scalp_trades` (MongoDB)
  - Proteção: sem duplicata de entrada quando posição já aberta no símbolo

Proteções de execução (Gates G-1 a G-8):
  G-1: Filtro RTH — quando rth_only=True só opera 09:30–16:15 ET Seg–Sex
  G-2: min_quality_to_execute — bloqueia sinais abaixo de STRONG/MODERATE
  G-3: Circuit Breaker — para loop quando max_daily_loss_usd ou
       max_consecutive_losses é atingido na sessão
  G-4: max_daily_trades — tecto de operações por dia de sessão
  G-5: max_total_contracts — tecto de contratos simultâneos partilhado entre
       símbolos; ajusta qty ao espaço disponível, bloqueia apenas se
       capacidade restante = 0 (0 = desactivado)
  G-6: News Blackout — suprime entradas durante eventos de calendário económico
       e janela de protecção em torno deles
  G-7: Warm-up filter — sessão-aware:
       RTH_OPEN: suprime os primeiros warmup_minutes (default 15) usando session_minutes
       OVERNIGHT: suprime os primeiros warmup_minutes_overnight (default 2) usando uptime
                  do AutoTrader (session_minutes é sempre 0 antes das 09:30 ET — não usar)
       Outros (RTH_MID, RTH_CLOSE): sem warm-up (sessão já estabilizada)
  G-8: Daily bias filter — suprime acção contrária ao bias de VWAP zone
       quando preço ≥ 1 SD acima/abaixo do VWAP; config: bias_filter_enabled,
       bias_filter_vwap_zones, bias_filter_suppress

Constantes de valor por ponto:
  MNQ: $2.00/ponto  ($0.50/tick)
  MES: $5.00/ponto  ($1.25/tick)
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List
import urllib.parse
import ipaddress

from services.scalp_pnl import DOLLAR_PER_POINT, pnl_usd as calc_pnl_usd, compute_pnl_pts, round_to_tick, align_sl_to_tick

logger = logging.getLogger("scalp_auto_trader")


def _is_safe_webhook_url(url: str) -> bool:
    """Verifica que a URL de webhook é HTTPS e não aponta para rede privada (SSRF prevention)."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return False
        hostname = parsed.hostname or ""
        _PRIVATE_PREFIXES = ("localhost", "127.", "0.", "::1", "169.254.")
        if any(hostname.startswith(p) for p in _PRIVATE_PREFIXES):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass  # hostname is a domain name, not an IP — ok
        return True
    except Exception:
        return False

def _tradovate_symbol(base: str) -> str:
    """
    Retorna o símbolo Tradovate do contrato front-month trimestral (H/M/U/Z).
    Faz rollover automático ~10 dias antes da expiração (3ª sexta do mês expiry).
    Espelha a lógica de _get_tradovate_symbol() em routes/scalp.py.
    """
    from datetime import datetime as _dt
    now = _dt.now()
    m, y = now.month, now.year
    if m < 3 or (m == 3 and now.day < 15):
        code, yr = 'H', y
    elif m < 6 or (m == 6 and now.day < 15):
        code, yr = 'M', y
    elif m < 9 or (m == 9 and now.day < 15):
        code, yr = 'U', y
    elif m < 12 or (m == 12 and now.day < 15):
        code, yr = 'Z', y
    else:
        code, yr = 'H', y + 1
    return f"{base}{code}{yr % 10}"

# Estado de status do loop
LOOP_STOPPED  = "STOPPED"
LOOP_RUNNING  = "RUNNING"
LOOP_PAUSED   = "PAUSED"

# Fuso horário de Nova Iorque (CME / NYSE)
_ET = ZoneInfo("America/New_York")

# Ranking de qualidade de sinal (G-2)
_QUALITY_RANK = {"WEAK": 0, "MODERATE": 1, "STRONG": 2}

# TTL do cache de config em memória (30s evita ~720 hits/hora ao MongoDB)
_CONFIG_CACHE_TTL = 30.0


def _is_rth() -> bool:
    """
    G-1: Verifica se o momento actual está dentro da sessão RTH dos micro futuros
    MNQ/MES (CME Globex Regular Trading Hours).
    RTH: segunda a sexta, 09:30–16:15 ET.
    """
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:          # sábado=5, domingo=6
        return False
    return (
        (now_et.hour == 9  and now_et.minute >= 30) or
        (now_et.hour >= 10 and now_et.hour < 16)    or
        (now_et.hour == 16 and now_et.minute <= 15)
    )


def _get_trade_session_label() -> str:
    """Rótulo de sessão no momento da abertura da trade."""
    from services.trading_calendar_service import get_session_label
    return get_session_label()


def _is_manual_window(config: Dict[str, Any]) -> bool:
    """Verifica horário manual: trading_start_hour–trading_end_hour ET."""
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return False
    start_h = int(config.get("trading_start_hour", 9))
    end_h   = int(config.get("trading_end_hour", 16))
    skip_first = int(config.get("avoid_first_minutes", 0))
    skip_last  = int(config.get("avoid_last_minutes", 0))
    open_dt  = now_et.replace(hour=start_h, minute=skip_first, second=0, microsecond=0)
    close_dt = now_et.replace(hour=end_h,   minute=0, second=0, microsecond=0)
    if skip_last:
        close_dt -= timedelta(minutes=skip_last)
    return open_dt <= now_et < close_dt


def _normalize_quality(q) -> str:
    """Normaliza qualidade para chave limpa: 'ScalpSignalQuality.MODERATE' → 'MODERATE'."""
    s = q.value if hasattr(q, "value") else str(q)
    return s.split(".")[-1].upper().strip()


def _meets_quality(signal_quality, min_quality: str) -> bool:
    """
    G-2: Retorna True se o sinal ≥ qualidade mínima configurada.
    Hierarquia: WEAK < MODERATE < STRONG.
    Aceita enum ScalpSignalQuality ou string (com ou sem prefixo de classe).
    """
    sig_rank = _QUALITY_RANK.get(_normalize_quality(signal_quality), -1)
    min_rank = _QUALITY_RANK.get(_normalize_quality(min_quality), 2)
    return sig_rank >= min_rank


class ScalpAutoTrader:
    """
    Loop de auto trading de scalp.
    Inicializado no startup do servidor via set_auto_trader_deps().
    """

    def __init__(self):
        self._scalp_engine     = None
        self._live_data_service= None
        self._database         = None
        self._config_getter    = None   # async callable → scalp_config dict

        self._status           = LOOP_STOPPED
        self._loop_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None

        # Cooldown tracker: symbol → último timestamp de entrada
        self._last_trade_ts: Dict[str, datetime] = {}

        # Posições abertas em memória (complementa MongoDB): trade_id → doc
        self._open_trades: Dict[str, Dict] = {}

        # Estatísticas em memória (acumuladas desde o start)
        self._session_stats: Dict[str, Any] = self._empty_stats()

        self._errors: List[str] = []

        # ── Track B — Pullback Observer (log_only) ────────────────────────────
        # Máquina de estados por zona: chave = "{symbol}:{zone_type}:{level:.2f}"
        # Observer-only até critério de activação ser atingido (ver _check_track_b_readiness).
        self._pb_state: Dict[str, Dict] = {}

        # Critério de activação Track B live:
        #   N ≥ 10 eventos RETURN observados no pb_state_log
        #   E ≥ 2 datas de calendário distintas (sessões de tipos diferentes)
        # Quando atingido: log WARNING proeminente + readiness_met=True no status.
        self._track_b_readiness: Dict[str, Any] = {
            "n_return_events":    0,
            "distinct_dates":     0,
            "readiness_met":      False,
            "last_checked":       None,
        }
        self._track_b_readiness_checked_ts: float = 0.0

        # Cache de config: evita MongoDB hit a cada iteração do loop (TTL 30s)
        self._config_cache: Optional[Dict[str, Any]] = None
        self._config_cache_ts: float = 0.0

        # Funil de sinais: deduplicação por (symbol, zone_type, gate_outcome)
        # key → último timestamp de log (float unix)
        self._signal_log_dedup: Dict[str, float] = {}

        # Rastreamento de data para reset automático dos contadores G-3/G-4
        self._last_session_date: Optional[str] = None

        # Rastreamento de transições para flatten automático
        self._was_in_trading_window: bool = False
        self._was_in_blackout:       bool = False

        # Cliente HTTP persistente: elimina handshake TCP+TLS por execução de ordem
        # Reutiliza keep-alive connections para SignalStack/Tradovate
        self._http_client: Optional[Any] = None  # httpx.AsyncClient lazy-init

    def set_deps(self, database, scalp_engine, live_data_service, config_getter, trading_calendar=None):
        self._database          = database
        self._scalp_engine      = scalp_engine
        self._live_data_service = live_data_service
        self._config_getter     = config_getter
        self._trading_calendar  = trading_calendar
        logger.info("ScalpAutoTrader: dependências configuradas")

    def _empty_stats(self) -> Dict[str, Any]:
        return {
            "total_trades": 0,
            "open": 0,
            "wins": 0,
            "losses": 0,
            "pnl_pts": 0.0,
            "pnl_usd": 0.0,
            "paper_trades": 0,
            "live_trades": 0,
            "errors": 0,
            "started_at": None,
            # G-3: Circuit Breaker
            "consecutive_losses": 0,
            "daily_pnl_usd": 0.0,
            "circuit_breaker_active": False,
            "circuit_breaker_reason": None,
            # G-4: Limite diário
            "daily_trades": 0,
        }

    def get_status(self) -> Dict[str, Any]:
        total_contracts_open = sum(
            int(t.get("quantity", 1)) for t in self._open_trades.values()
        )
        return {
            "status":                self._status,
            "open_positions":        len(self._open_trades),
            "total_contracts_open":  total_contracts_open,
            "session_stats":         self._session_stats,
            "last_errors":           self._errors[-5:],
            "track_b_readiness":     self._track_b_readiness,
        }

    # ── Start / Stop ──

    def invalidate_config_cache(self):
        """Invalida cache de configuração — força leitura fresh do MongoDB na próxima iteração."""
        self._config_cache    = None
        self._config_cache_ts = 0.0
        logger.debug("AutoTrader: config cache invalidado")

    async def start(self):
        if self._status == LOOP_RUNNING:
            logger.info("ScalpAutoTrader: já em execução")
            return
        self._status = LOOP_RUNNING
        self._session_stats = self._empty_stats()
        self._session_stats["started_at"] = datetime.now(timezone.utc).isoformat()
        self._errors = []

        # Invalida cache para garantir que o loop arranca com config fresca do MongoDB
        self.invalidate_config_cache()

        # Carrega trades abertos do MongoDB (se reiniciado)
        await self._reload_open_trades()

        self._loop_task    = asyncio.create_task(self._trading_loop())
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("ScalpAutoTrader: INICIADO")

    async def stop(self):
        self._status = LOOP_STOPPED
        if self._loop_task:
            self._loop_task.cancel()
        if self._monitor_task:
            self._monitor_task.cancel()
        self._loop_task = self._monitor_task = None
        # Fecha o cliente HTTP persistente — libera conexões keep-alive ao SignalStack
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None
        logger.info("ScalpAutoTrader: PARADO")

    # ── Circuit Breaker ───────────────────────────────────────────────────────

    def _trigger_circuit_breaker(self, reason: str):
        """Activa o circuit breaker de sessão e pausa o auto trader."""
        self._session_stats["circuit_breaker_active"] = True
        self._session_stats["circuit_breaker_reason"] = reason
        logger.warning(f"AutoTrader CIRCUIT BREAKER: {reason} — loop suspenso. Reinicie manualmente.")
        try:
            asyncio.get_event_loop().create_task(self._persist_session_stats())
        except Exception:
            pass

    def reset_circuit_breaker(self):
        """
        Reseta manualmente o circuit breaker.
        Zera contadores diários (daily_pnl_usd, daily_trades, consecutive_losses)
        e desactiva o bloqueio. O loop retoma na próxima iteração.
        """
        self._session_stats["circuit_breaker_active"] = False
        self._session_stats["circuit_breaker_reason"] = None
        self._session_stats["consecutive_losses"]     = 0
        self._session_stats["daily_pnl_usd"]          = 0.0
        self._session_stats["daily_trades"]            = 0
        logger.info("AutoTrader: circuit breaker resetado manualmente — contadores diários zerados")

    def _check_circuit_breaker(self, config: Dict[str, Any]) -> bool:
        """
        G-3 / G-4: Verifica se o loop deve ser bloqueado.
        Retorna True quando algum limite foi atingido.
        """
        if self._session_stats.get("circuit_breaker_active"):
            return True

        stats = self._session_stats

        # G-3a: Perda diária USD
        max_loss = float(config.get("max_daily_loss_usd", 200.0))
        if max_loss > 0 and stats["daily_pnl_usd"] <= -max_loss:
            self._trigger_circuit_breaker(
                f"Perda diária atingida: ${stats['daily_pnl_usd']:.2f} ≤ -${max_loss:.2f}"
            )
            return True

        # G-3b: Perdas consecutivas
        max_consec = int(config.get("max_consecutive_losses", 3))
        if max_consec > 0 and stats["consecutive_losses"] >= max_consec:
            self._trigger_circuit_breaker(
                f"Perdas consecutivas: {stats['consecutive_losses']} ≥ {max_consec}"
            )
            return True

        # G-4: Tecto de trades diários
        max_daily = int(config.get("max_daily_trades", 10))
        if max_daily > 0 and stats["daily_trades"] >= max_daily:
            self._trigger_circuit_breaker(
                f"Limite diário de trades atingido: {stats['daily_trades']} ≥ {max_daily}"
            )
            return True

        return False

    def _is_trading_window(self, config: Dict[str, Any]) -> bool:
        """
        G-1 expandido: verifica se o momento está dentro da janela de trading.
        - auto_hours_mode=True  → usa is_within_auto_trading_hours() do trading_calendar
        - globex_auto_enabled   → também aceita sessão Globex
        - auto_hours_mode=False → usa horário manual (trading_start/end_hour)
        - Fallback               → _is_rth() simples
        """
        cal = self._trading_calendar
        auto_mode = config.get("auto_hours_mode", None)
        if cal is not None and auto_mode is not False:
            hours_ok, _ = cal.is_within_auto_trading_hours()
            if hours_ok:
                return True
            if config.get("globex_auto_enabled", False):
                globex_ok, _ = cal.is_within_globex_auto_hours()
                return globex_ok
            return False
        if auto_mode is False:
            return _is_manual_window(config)
        return _is_rth()

    async def _is_news_blackout_active(self, config: Dict[str, Any]) -> bool:
        """Verifica se estamos num período de blackout de notícias (G-5).
        Falha de forma segura: em caso de erro assume blackout ativo (bloqueia trading)."""
        if not config.get("news_blackout_enabled", False):
            return False
        cal = self._trading_calendar
        if cal is None:
            return False
        try:
            events = await cal.get_news_blackouts()
            is_blackout, _ = cal.check_news_blackout(
                events,
                minutes_before=int(config.get("news_blackout_minutes_before", 15)),
                minutes_after=int(config.get("news_blackout_minutes_after", 15)),
            )
            return is_blackout
        except Exception as e:
            logger.warning(f"ScalpAutoTrader: erro ao verificar news blackout — bloqueando por segurança: {e}")
            return True  # fail-safe: bloqueia trading em caso de erro

    async def flatten_all_trades(self, reason: str = "flatten_all") -> int:
        """Fecha todas as posições abertas e cancela todas as ordens no broker.

        Sequência para trades live:
          1. Agrega por símbolo e envia close via PickMyTrade (PMT cancela o bracket
             SL/TP automaticamente ao fechar a posição no Tradovate).
          2. Fecha cada trade internamente (MongoDB + memória) sem re-chamar o broker.

        Para paper trades: apenas fecha estado interno — sem chamadas ao broker.

        Retorna o nº de trades fechados.
        """
        if not self._open_trades:
            return 0

        config = await self._config_getter()
        webhook_url = config.get("webhook_url", "") or ""

        # ── Passo 1: Broker — cancel + close por símbolo PMT (apenas trades live) ──
        if webhook_url and _is_safe_webhook_url(webhook_url):
            # Agrega por tradovate_symbol: close_action dominante + quantidade total
            live_by_symbol: Dict[str, Dict] = {}
            for trade in self._open_trades.values():
                if trade.get("paper", True):
                    continue
                tv_sym       = trade["tradovate_symbol"]
                close_action = "sell" if trade["action"] == "buy" else "buy"
                qty          = int(trade.get("quantity", 1))
                if tv_sym not in live_by_symbol:
                    live_by_symbol[tv_sym] = {"close_action": close_action, "quantity": qty}
                else:
                    # Mesma direção → acumula; direcções opostas → net (edge case raro)
                    if live_by_symbol[tv_sym]["close_action"] == close_action:
                        live_by_symbol[tv_sym]["quantity"] += qty
                    else:
                        net = live_by_symbol[tv_sym]["quantity"] - qty
                        if net > 0:
                            live_by_symbol[tv_sym]["quantity"] = net
                        elif net < 0:
                            live_by_symbol[tv_sym]["close_action"] = close_action
                            live_by_symbol[tv_sym]["quantity"] = abs(net)
                        else:
                            # Posição líquida zero — só cancela ordens
                            live_by_symbol[tv_sym]["quantity"] = 0

            for tv_sym, info in live_by_symbol.items():
                await self._broker_flatten_symbol(
                    webhook_url, tv_sym, info["close_action"], info["quantity"]
                )

        # ── Passo 2: Estado interno — fecha cada trade sem nova chamada ao broker ──
        closed = 0
        for trade_id in list(self._open_trades.keys()):
            try:
                doc        = self._open_trades.get(trade_id, {})
                sym        = doc.get("symbol", "MNQ")
                live_price = self._get_live_price(sym) or doc.get("entry_price", 0.0)
                await self._close_trade(trade_id, live_price, reason, skip_broker=True)
                closed += 1
            except Exception as e:
                logger.warning(f"ScalpAutoTrader flatten_all: erro a fechar {trade_id}: {e}")
        return closed

    def _get_http_client(self):
        """Retorna (ou cria) o cliente HTTP persistente com keep-alive.
        Evita handshake TCP+TLS por execução de ordem — crítico para latência."""
        import httpx
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=10.0,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=60),
            )
        return self._http_client

    async def _broker_flatten_symbol(
        self, webhook_url: str, tradovate_symbol: str, close_action: str, quantity: int
    ) -> None:
        """Envia ao PickMyTrade o comando FLAT para um símbolo (EOD / blackout).

        data:"flat" é a acção nativa PMT para flatten — cancela todas as ordens pendentes
        (brackets SL/TP e órfãs) E fecha a posição aberta numa única chamada.

        Acções suportadas pelo PMT: BUY, SELL, CLOSE, FLAT, LONG, SHORT.
        ("cancel" NÃO é suportado — retorna "Wrong Action" e é ignorado.)
        """
        config  = await self._config_getter()
        token   = config.get("pmt_token", "")
        acct    = config.get("pmt_account_id", "")
        # base symbol: "MNQM6" → "MNQ1!" ; "MESM6" → "MES1!"
        base    = tradovate_symbol.rstrip("0123456789").rstrip("FGHJKMNQUVXZ")
        pmt_sym = f"{base}1!"
        client  = self._get_http_client()

        from datetime import datetime as _dt_flat
        def _flat_payload(action: str) -> dict:
            """Payload completo para cancel/close — PMT rejeita payloads incompletos."""
            return {
                "symbol":                pmt_sym,
                "strategy_name":         "QuantumScalp",
                "date":                  _dt_flat.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data":                  action,
                "quantity":              quantity or 1,
                "price":                 0,
                "tp":                    0,
                "sl":                    0,
                "trail":                 0,
                "token":                 token,
                "account_id":            acct,
                "pyramid":               False,
                "same_direction_ignore": True,
                "reverse_order_close":   False,
                "multiple_accounts": [{
                    "token": token, "account_id": acct,
                    "risk_percentage": 0, "quantity_multiplier": 1,
                }],
            }

        # ── FLAT: acção nativa PMT — cancela todas as ordens pendentes E fecha posição ──
        # PMT suporta: BUY, SELL, CLOSE, FLAT, LONG, SHORT  ("cancel" não é suportado)
        try:
            await client.post(webhook_url, json=_flat_payload("flat"))
            logger.info("AutoTrader broker: PMT flat → %s (qty=%d)", pmt_sym, quantity)
        except Exception as e:
            logger.warning("AutoTrader broker: PMT flat falhou %s: %s", pmt_sym, e)

    async def _get_config_cached(self) -> Dict[str, Any]:
        """Retorna config com TTL de 30s — evita hit MongoDB a cada iteração do loop."""
        import time as _t
        if self._config_cache is not None and (_t.monotonic() - self._config_cache_ts) < _CONFIG_CACHE_TTL:
            return self._config_cache
        cfg = await self._config_getter()
        self._config_cache    = cfg
        self._config_cache_ts = _t.monotonic()
        return cfg

    def _check_daily_reset(self):
        """Reset automático dos contadores G-3/G-4 no início de cada dia de sessão ET."""
        today = datetime.now(_ET).strftime("%Y-%m-%d")
        if self._last_session_date and self._last_session_date != today:
            self._session_stats["daily_pnl_usd"]         = 0.0
            self._session_stats["daily_trades"]           = 0
            self._session_stats["consecutive_losses"]     = 0
            self._session_stats["circuit_breaker_active"] = False
            self._session_stats["circuit_breaker_reason"] = None
            logger.info(f"AutoTrader: reset diário automático ({self._last_session_date} → {today})")
        self._last_session_date = today

    # ── Loop principal de sinal ──

    async def _trading_loop(self):
        while self._status == LOOP_RUNNING:
            try:
                config = await self._get_config_cached()
                self._check_daily_reset()
                await self._check_track_b_readiness()
                interval = int(config.get("auto_interval_sec", 5))
                await asyncio.sleep(interval)

                # ── G-1: Janela de trading (horário) ──────────────────────────────
                in_window = self._is_trading_window(config)
                if not in_window:
                    # Transição: estava dentro da janela → agora fora → EOD flatten
                    if self._was_in_trading_window and self._open_trades:
                        if config.get("eod_flatten_enabled", True):
                            logger.info(
                                "AutoTrader EOD: janela de trading fechou com %d posição(ões) aberta(s) "
                                "— flatten automático activado.",
                                len(self._open_trades),
                            )
                            await self.flatten_all_trades(reason="EOD_FLATTEN")
                    self._was_in_trading_window = False
                    continue
                self._was_in_trading_window = True

                # ── G-5: News Blackout ─────────────────────────────────────────────
                in_blackout = await self._is_news_blackout_active(config)
                if in_blackout:
                    # Transição: entrou em blackout com posições abertas → flatten
                    if not self._was_in_blackout and self._open_trades:
                        logger.info(
                            "AutoTrader BLACKOUT: blackout iniciado com %d posição(ões) "
                            "— flatten activado.",
                            len(self._open_trades),
                        )
                        await self.flatten_all_trades(reason="BLACKOUT_FLATTEN")
                    self._was_in_blackout = True
                    continue
                # Blackout terminou — reset de estado (trading retoma normalmente)
                self._was_in_blackout = False

                # ── G-3 / G-4: Circuit Breaker ────────────────────────────────────
                if self._check_circuit_breaker(config):
                    continue

                symbols_enabled     = config.get("symbols_enabled", ["MNQ", "MES"])
                disabled_zone_types = list(config.get("disabled_zone_types", []))
                engine_mode         = config.get("mode", "ZONES")

                # ── R3: desactiva GAMMA_PUT_WALL ─────────────────────────────────────
                if config.get("r3_gamma_put_wall_disabled", True):
                    for _zt in ("GAMMA_PUT_WALL_BUY", "GAMMA_PUT_WALL_SELL"):
                        if _zt not in disabled_zone_types:
                            disabled_zone_types.append(_zt)

                # ── R4: desactiva VWAP_PULLBACK ──────────────────────────────────────
                if config.get("r4_vwap_pullback_disabled", True):
                    for _zt in ("VWAP_PULLBACK_BUY", "VWAP_PULLBACK_SELL"):
                        if _zt not in disabled_zone_types:
                            disabled_zone_types.append(_zt)

                r1_block = bool(config.get("r1_moderate_rth_mid_block", True))
                r2_block = bool(config.get("r2_bearish_rth_mid_close_block", True))
                max_positions    = int(config.get("max_positions", 2))
                cooldown_sec     = int(config.get("cooldown_sec", 60))
                max_per_symbol   = int(config.get("max_per_symbol", 1))
                min_quality      = config.get("min_quality_to_execute", "STRONG")

                # Conta posições abertas por símbolo
                open_by_sym: Dict[str, int] = {}
                for t in self._open_trades.values():
                    s = t.get("symbol", "")
                    open_by_sym[s] = open_by_sym.get(s, 0) + 1

                total_open = len(self._open_trades)
                if total_open >= max_positions:
                    continue

                for symbol in symbols_enabled:
                    if symbol not in ("MNQ", "MES"):
                        continue

                    # Limite por símbolo
                    if open_by_sym.get(symbol, 0) >= max_per_symbol:
                        continue

                    # Cooldown por símbolo
                    last_ts = self._last_trade_ts.get(symbol)
                    if last_ts:
                        elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
                        if elapsed < cooldown_sec:
                            continue

                    sizing_mode = config.get("sizing_mode", "fixed")

                    # Sizing inicial — fixo lê config; risk_pct avalia com 1 e recalcula depois
                    if sizing_mode == "risk_pct":
                        qty = 1
                    else:
                        qty = int(config.get(f"{symbol.lower()}_quantity", 1))

                    # ── G-5: Limite de contratos totais (partilhado) ──────────
                    max_total_contracts = int(config.get("max_total_contracts", 0))
                    if max_total_contracts > 0:
                        total_contracts_open = sum(
                            int(t.get("quantity", 1))
                            for t in self._open_trades.values()
                        )
                        remaining = max_total_contracts - total_contracts_open
                        if remaining <= 0:
                            logger.debug(
                                f"AutoTrader G-5: {symbol} bloqueado — "
                                f"contratos abertos={total_contracts_open} "
                                f"≥ max={max_total_contracts}"
                            )
                            continue
                        if qty > remaining:
                            logger.info(
                                f"AutoTrader G-5: {symbol} qty ajustado "
                                f"{qty}→{remaining} "
                                f"(abertos={total_contracts_open}, "
                                f"max={max_total_contracts})"
                            )
                            qty = remaining

                    try:
                        signal = await self._scalp_engine.evaluate(
                            symbol, qty,
                            mode=engine_mode,
                            disabled_zone_types=disabled_zone_types,
                            r1_block=r1_block,
                            r2_block=r2_block,
                        )
                    except Exception as e:
                        logger.warning(f"ScalpAutoTrader: erro ao avaliar {symbol}: {e}")
                        continue

                    # ── Track B Observer (sempre, independente do status) ──────────
                    try:
                        await self._update_pb_observer(symbol, signal, config)
                    except Exception as _pb_err:
                        logger.debug("PB observer error [%s]: %s", symbol, _pb_err)

                    # ── Fix A shadow logging: SIGMA2_FADE_(BUY|SELL) bloqueadas em RTH_OPEN ──
                    # Se havia zonas bloqueadas por Fix A (BUY ou SELL), regista no
                    # signal_log com gate_outcome="FA_SHADOW" para auditoria de custo de oportunidade.
                    # Permite medir PnL hipotético vs real após N sessões.
                    _fix_a_shadows = getattr(signal, "fix_a_shadow_zones", [])
                    if _fix_a_shadows:
                        _fa_sess  = _get_trade_session_label()
                        _fa_paper = config.get("paper_trading", True)
                        _fa_types = list({z.get("zone_type", "") for z in _fix_a_shadows})
                        asyncio.ensure_future(self._log_signal_event(
                            signal, _fa_sess, "FA_SHADOW",
                            f"Fix A: {len(_fix_a_shadows)} zona(s) bloqueada(s) "
                            f"em RTH_OPEN — tipos={_fa_types} "
                            f"níveis={[z.get('level') for z in _fix_a_shadows]}",
                            paper=_fa_paper,
                        ))

                    if signal.scalp_status != "ACTIVE_SIGNAL":
                        continue

                    # ── Gate por modo: auto_trade_flow / auto_trade_zones ─────────────
                    # Permite habilitar/desabilitar execução automática por modo
                    # independentemente do master switch auto_trade.
                    # Default True em ambos → comportamento idêntico ao anterior.
                    _sig_mode   = getattr(signal, "mode", "FLOW")
                    _sig_paper  = config.get("paper_trading", True)
                    _sig_sess   = _get_trade_session_label()
                    if _sig_mode == "FLOW" and not config.get("auto_trade_flow", True):
                        logger.info(
                            "AutoTrader: %s [FLOW] suprimido — auto_trade_flow=False",
                            symbol,
                        )
                        asyncio.ensure_future(self._log_signal_event(
                            signal, _sig_sess, "MODE_BLOCKED", "auto_trade_flow=False", paper=_sig_paper))
                        continue
                    if _sig_mode == "ZONES" and not config.get("auto_trade_zones", True):
                        logger.info(
                            "AutoTrader: %s [ZONES] suprimido — auto_trade_zones=False",
                            symbol,
                        )
                        asyncio.ensure_future(self._log_signal_event(
                            signal, _sig_sess, "MODE_BLOCKED", "auto_trade_zones=False", paper=_sig_paper))
                        continue

                    # ── G-7: Warm-up filter — sessão-aware ────────────────────────────
                    # RTH_OPEN: session_minutes < warmup_minutes (default 15) → bloqueia
                    # OVERNIGHT: session_minutes é sempre 0 antes das 09:30 ET (bug de cálculo);
                    #            usa uptime do AutoTrader < warmup_minutes_overnight (default 2)
                    # Outros (RTH_MID, RTH_CLOSE): sem warm-up — sessão já estabilizada
                    _g7_session = _get_trade_session_label()
                    if _g7_session == "RTH_OPEN":
                        warmup_min = float(config.get("warmup_minutes", 15))
                        if warmup_min > 0:
                            sig_sess_min = getattr(signal, "session_minutes", None)
                            if sig_sess_min is not None and sig_sess_min < warmup_min:
                                logger.info(
                                    "AutoTrader G-7 BLOQUEADO: %s RTH_OPEN session_minutes=%.1f < %.0f",
                                    symbol, sig_sess_min, warmup_min,
                                )
                                asyncio.ensure_future(self._log_signal_event(
                                    signal, _sig_sess, "G7_BLOCKED",
                                    f"RTH_OPEN warm-up: session_minutes={sig_sess_min:.1f} < {warmup_min:.0f}",
                                    paper=_sig_paper))
                                continue
                    elif _g7_session == "OVERNIGHT":
                        warmup_min_ovn = float(config.get("warmup_minutes_overnight", 2))
                        if warmup_min_ovn > 0:
                            started_at_str = self._session_stats.get("started_at")
                            if started_at_str:
                                try:
                                    started_at_dt = datetime.fromisoformat(started_at_str)
                                    uptime_min = (datetime.now(timezone.utc) - started_at_dt).total_seconds() / 60.0
                                    if uptime_min < warmup_min_ovn:
                                        logger.info(
                                            "AutoTrader G-7 BLOQUEADO: %s OVERNIGHT uptime=%.1f min < %.0f min (warm-up)",
                                            symbol, uptime_min, warmup_min_ovn,
                                        )
                                        asyncio.ensure_future(self._log_signal_event(
                                            signal, _sig_sess, "G7_BLOCKED",
                                            f"OVN warm-up: uptime={uptime_min:.1f} min < {warmup_min_ovn:.0f} min",
                                            paper=_sig_paper))
                                        continue
                                except Exception:
                                    pass
                    # RTH_MID / RTH_CLOSE: sem warm-up

                    # ── G-8: Daily bias filter — suprime SELL em mercado bullish ──────
                    # Quando o preço está ≥ 1 SD acima do VWAP, o fluxo macro é bullish.
                    # Estatística: SELL WR=23% (−$912) em dia ABOVE_2SD; BUY WR=35% (+$275).
                    # Suprime a acção contrária ao bias para preservar capital em dias
                    # de tendência clara. Configurável via bias_filter_enabled +
                    # bias_filter_vwap_zones + bias_filter_suppress.
                    if config.get("bias_filter_enabled", True):
                        sig_vwap_zone  = getattr(signal, "vwap_zone", None)
                        sig_action     = signal.s3_action
                        bias_zones     = config.get(
                            "bias_filter_vwap_zones",
                            ["ABOVE_2SD"],  # REVER: threshold ABOVE_1SD pendente de validação — semana 2026-04-13
                        )
                        bias_suppress  = config.get("bias_filter_suppress", "sell")
                        if (sig_vwap_zone in bias_zones
                                and sig_action == bias_suppress):
                            logger.info(
                                "AutoTrader G-8 BLOQUEADO: %s vwap_zone=%s + action=%s "
                                "— daily bias filter (bullish zone + counter-trend SELL)",
                                symbol, sig_vwap_zone, sig_action,
                            )
                            asyncio.ensure_future(self._log_signal_event(
                                signal, _sig_sess, "G8_BLOCKED",
                                f"bias_filter: vwap_zone={sig_vwap_zone} + action={sig_action}",
                                paper=_sig_paper))
                            continue

                    # ── Sizing dinâmico por Risk % (recalcula após evaluate) ──
                    if sizing_mode == "risk_pct":
                        entry_price = signal.last_price
                        sl_price    = signal.s3_stop_loss_price
                        if sl_price and entry_price and abs(entry_price - sl_price) > 0.01:
                            stop_pts    = abs(entry_price - sl_price)
                            pv          = DOLLAR_PER_POINT.get(symbol, 2.0)
                            risk_usd    = (float(config.get("account_size", 50000))
                                          * float(config.get("risk_per_trade_pct", 1.0)) / 100.0)
                            max_qty_cap = int(config.get("max_qty_risk_pct", 5))
                            dynamic_qty = max(1, min(int(risk_usd / (stop_pts * pv)), max_qty_cap))
                            # Reaplica G-5 após sizing dinâmico
                            if max_total_contracts > 0:
                                total_now   = sum(int(t.get("quantity", 1)) for t in self._open_trades.values())
                                remaining_now = max_total_contracts - total_now
                                if remaining_now <= 0:
                                    logger.debug(f"AutoTrader G-5 (risk_pct): {symbol} bloqueado após sizing dinâmico")
                                    continue
                                dynamic_qty = min(dynamic_qty, remaining_now)
                            signal.s3_quantity = dynamic_qty
                            qty = dynamic_qty
                            logger.info(
                                f"AutoTrader RISK%: {symbol} risk_usd=${risk_usd:.0f} "
                                f"stop_pts={stop_pts:.2f} pv={pv} → qty={qty}"
                            )
                        else:
                            logger.warning(f"AutoTrader RISK%: {symbol} SL inválido — usando qty=1")
                            signal.s3_quantity = 1
                            qty = 1

                    # ── G-2: Filtro de qualidade mínima (sessão-aware, por símbolo) ──
                    # OVERNIGHT : min_quality_overnight_mnq / _mes  (fallback MODERATE)
                    # RTH e demais: min_quality_rth_mnq / _mes      (fallback STRONG)
                    # Legado: min_quality_to_execute usado como fallback final
                    _g2_session = _get_trade_session_label()
                    _sym_lower  = symbol.lower()
                    if _g2_session == "OVERNIGHT":
                        min_quality_eff = (
                            config.get(f"min_quality_overnight_{_sym_lower}")
                            or config.get("min_quality_overnight")
                            or "MODERATE"
                        )
                    else:
                        min_quality_eff = (
                            config.get(f"min_quality_rth_{_sym_lower}")
                            or config.get("min_quality_to_execute")
                            or "STRONG"
                        )

                    signal_quality = getattr(signal, "s2_quality", "WEAK")
                    signal_quality_str = _normalize_quality(signal_quality)
                    if not _meets_quality(signal_quality, min_quality_eff):
                        logger.info(
                            f"AutoTrader G-2 BLOQUEADO: {symbol} qualidade={signal_quality_str} "
                            f"< min={min_quality_eff} (sessão={_g2_session}) — sinal ignorado"
                        )
                        asyncio.ensure_future(self._log_signal_event(
                            signal, _sig_sess, "G2_BLOCKED",
                            f"quality={signal_quality_str} < min={min_quality_eff}",
                            paper=_sig_paper))
                        continue

                    logger.info(
                        f"AutoTrader EXEC: {symbol} qualidade={signal_quality_str} "
                        f"≥ min={min_quality_eff} (sessão={_g2_session}) — chamando _execute_trade"
                    )
                    await self._execute_trade(signal, config)

            except asyncio.CancelledError:
                break
            except Exception as e:
                msg = f"ScalpAutoTrader loop error: {e}"
                logger.error(msg)
                self._errors.append(msg)
                self._session_stats["errors"] += 1
                await asyncio.sleep(5)

    # ── Execução de trade ──

    async def _execute_trade(self, signal, config: Dict[str, Any]):
        symbol    = signal.symbol
        action    = signal.s3_action
        price     = signal.last_price
        sl_price  = signal.s3_stop_loss_price
        tp_price  = signal.s3_take_profit_price
        be_pts    = signal.s3_breakeven
        qty       = signal.s3_quantity
        paper     = config.get("paper_trading", True)
        webhook_url = config.get("webhook_url", "")
        mode      = signal.mode

        # Alinhar SL e TP ao tick antes de gravar e monitorizar.
        # O Tradovate (via PickMyTrade) aplica o offset ao fill price e arredonda
        # para o tick — o threshold interno deve coincidir com o stop real no broker.
        # SL: arredondamento conservador por direcção (floor para SELL, ceil para BUY)
        #     para garantir que o fill real é detectado mesmo com slippage de 1 tick.
        # TP: arredondamento para o tick mais próximo (neutro).
        if sl_price is not None and action is not None:
            sl_price = align_sl_to_tick(sl_price, action, symbol)
        if tp_price is not None:
            tp_price = round_to_tick(tp_price, symbol)

        if not action or price <= 0 or sl_price is None or tp_price is None:
            logger.warning(
                f"AutoTrader _execute_trade ABORTADO: {symbol} "
                f"action={action!r} price={price} sl={sl_price} tp={tp_price} — campo inválido"
            )
            return

        # ── Guard: posição já aberta para este símbolo ─────────────────────────
        # PMT tem same_direction_ignore=True, mas retorna 200 OK mesmo quando ignora
        # a ordem — o sistema interpretaria isso como execução e criaria um trade
        # fantasma com P&L fictício no journal. Bloquear aqui antes de qualquer
        # registo ou envio ao broker.
        _existing = [
            tid for tid, t in self._open_trades.items()
            if t.get("symbol") == symbol and t.get("action") == action
        ]
        if _existing:
            logger.warning(
                "AutoTrader BLOQUEADO (posição aberta): %s %s já tem trade(s) aberto(s) "
                "na mesma direcção (%s) — ordem ignorada para evitar trade fantasma. "
                "trade_ids=%s",
                symbol, action.upper(), action, _existing,
            )
            return

        trade_id = str(uuid.uuid4())
        now      = datetime.now(timezone.utc)

        trade_doc = {
            "id": trade_id,
            "symbol": symbol,
            "tradovate_symbol": _tradovate_symbol(symbol),
            "mode": mode,
            "source": "auto",
            "action": action,
            "quantity": qty,
            "entry_price": price,
            "stop_loss_price": sl_price,
            "take_profit_price": tp_price,
            "breakeven_pts": be_pts,
            "trailing_active": False,
            "best_price": None,
            "paper": paper,
            # Signal context
            "s1_regime": _normalize_quality(signal.s1_regime) if signal.s1_regime else None,
            "s1_confidence": signal.s1_confidence,
            "s2_quality": _normalize_quality(signal.s2_quality),
            "s2_risk_modifier": signal.s2_risk_modifier,
            "ofi_fast": signal.ofi_fast,
            "ofi_slow": signal.ofi_slow,
            "absorption_flag": signal.absorption_flag,
            "absorption_side": signal.absorption_side,
            "cvd": signal.cvd,
            "cvd_trend": signal.cvd_trend,
            "atr_m1": signal.atr_m1,
            # Calibração granular — tuple (zone_type × quality × regime × session_phase)
            # Fonte primária: zone_type_str (campo de 1ª classe no ScalpSignal).
            # Fallback: active_zone["type"] para compatibilidade com sinais FLOW manuais.
            "zone_type": (
                signal.zone_type_str
                or (signal.active_zone.get("type") if signal.active_zone else None)
            ),
            "score_breakdown": signal.zone_score_breakdown,
            "zone_score": (
                signal.zone_score_breakdown.get("total_score")
                if isinstance(signal.zone_score_breakdown, dict) else None
            ),
            "vwap_zone":  signal.vwap_zone,
            "disp_30m":  signal.disp_30m,
            "d30_state": signal.d30_state,
            "session_minutes": signal.session_minutes,
            "session_label":   _get_trade_session_label(),
            # Estado
            "state": "OPEN",
            "exit_price": None,
            "exit_reason": None,
            "pnl_pts": None,
            "pnl_usd": None,
            "duration_sec": None,
            "created_at": now.isoformat(),
            "closed_at": None,
            "events": [{"ts": now.isoformat(), "event": "OPEN", "price": price, "source": "auto"}],
            # SignalStack
            "signalstack_response": None,
            "signalstack_ok": None,
            # Track A/B — mecanismo de entrada
            "entry_mechanism": "DIRECT",
        }

        # ── Execução ──
        if paper:
            trade_doc["state"] = "OPEN"
            trade_doc["fill_type"] = "paper_simulated"
            self._session_stats["paper_trades"] += 1
            logger.info(f"AutoTrader PAPER: {symbol} {action.upper()} @ {price} | SL={sl_price} TP={tp_price}")
        else:
            if not webhook_url:
                logger.warning(f"AutoTrader LIVE: sem webhook_url configurado, ignorando {symbol}")
                return
            if not _is_safe_webhook_url(webhook_url):
                logger.error(f"AutoTrader LIVE: webhook_url rejeitada (SSRF/protocolo inválido): {webhook_url[:80]}")
                return
            # ── Formato PickMyTrade (template completo) ──
            from datetime import datetime as _dt2
            sl_pts_rel = round(abs(price - sl_price), 2)
            tp_pts_rel = round(abs(tp_price - price), 2)
            TICK        = 0.25
            pmt_token   = config.get("pmt_token", "")
            pmt_acct    = config.get("pmt_account_id", "")
            trail_on    = 1 if (be_pts and be_pts > 0) else 0
            pmt_symbol = f"{symbol.upper()}1!"   # MNQ → MNQ1! / MES → MES1!
            # PMT: dollar_sl/dollar_tp = offset em PONTOS a partir do fill price
            # (apesar do nome, o campo é em pontos, não em dólares — confirmado em teste)
            dollar_sl_val = sl_pts_rel
            dollar_tp_val = tp_pts_rel
            payload = {
                "symbol":                pmt_symbol,
                "strategy_name":         "QuantumScalp",
                "date":                  _dt2.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data":                  action,
                "quantity":              qty,
                "risk_percentage":       0,
                "price":                 0,
                "tp":                    0,
                "percentage_tp":         0,
                "dollar_tp":             dollar_tp_val,
                "sl":                    0,
                "dollar_sl":             dollar_sl_val,
                "percentage_sl":         0,
                "trail":                 trail_on,
                "trail_stop":            be_pts if trail_on else 0,
                "trail_trigger":         be_pts if trail_on else 0,
                "trail_freq":            TICK   if trail_on else 0,
                "update_tp":             False,
                "update_sl":             False,
                "breakeven":             0,
                "breakeven_offset":      0,
                "token":                 pmt_token,
                "account_id":            pmt_acct,
                "pyramid":               False,
                "same_direction_ignore": True,
                "reverse_order_close":   True,
                "multiple_accounts": [{
                    "token":               pmt_token,
                    "account_id":          pmt_acct,
                    "risk_percentage":     0,
                    "quantity_multiplier": 1,
                }],
            }
            try:
                client = self._get_http_client()
                resp = await client.post(webhook_url, json=payload)
                ss_resp = {"status_code": resp.status_code, "response": resp.text[:500], "ok": resp.status_code < 300}
                trade_doc["signalstack_response"] = ss_resp
                trade_doc["signalstack_ok"] = ss_resp["ok"]
                trade_doc["fill_type"] = "live_pickmytrade"
                self._session_stats["live_trades"] += 1
                logger.info(f"AutoTrader LIVE: {symbol} {action.upper()} @ {price} → PMT {resp.status_code} sl={sl_pts_rel} tp={tp_pts_rel} trail={be_pts}")
            except Exception as e:
                logger.error(f"AutoTrader LIVE: erro SignalStack para {symbol}: {e}")
                self._errors.append(f"SignalStack error {symbol}: {e}")
                trade_doc["signalstack_ok"] = False
                trade_doc["fill_type"] = "live_error"

        await self._database["scalp_trades"].insert_one({**trade_doc, "_id": trade_id})

        # Registra em memória
        self._open_trades[trade_id] = trade_doc
        self._last_trade_ts[symbol] = now
        self._session_stats["total_trades"] += 1
        self._session_stats["daily_trades"]  += 1   # G-4
        self._session_stats["open"] = len(self._open_trades)

        # ── Log de funil: EXECUTED com trade_id real ───────────────────────────
        try:
            _exec_sess   = _get_trade_session_label()
            _exec_qual   = _normalize_quality(getattr(signal, "s2_quality", "WEAK"))
            asyncio.ensure_future(self._log_signal_event(
                signal, _exec_sess, "EXECUTED",
                f"quality={_exec_qual} → trade_id={trade_id}",
                trade_id=trade_id,
                paper=paper,
            ))
        except Exception as _le:
            logger.debug("signal_log EXECUTED error: %s", _le)

        # ── Drain do buffer de ticks pré-trade ────────────────────────────────
        # consume_period_range() acumula _period_low/_period_high continuamente,
        # mesmo quando não há trades abertos. Se não houver nenhum trade por horas,
        # o primeiro ciclo do monitor consome todo esse histórico (ex: 3h de ticks)
        # e usa extremos pré-trade para detecção de SL/TP e activação do trailing.
        # Isso produz: trailing activado por high histórico + exit registado com
        # preço errado → journal regista +$20 quando o real foi -$74.
        # Solução: chamar consume_period_range() imediatamente após abrir o trade,
        # descartando os ticks anteriores; o monitor só verá ticks pós-abertura.
        try:
            if (hasattr(self._live_data_service, 'buffers')
                    and symbol in self._live_data_service.buffers):
                _buf = self._live_data_service.buffers[symbol]
                if hasattr(_buf, 'consume_period_range'):
                    _stale = _buf.consume_period_range()
                    logger.info(
                        f"AutoTrader buffer-drain {symbol}: ticks pré-trade descartados "
                        f"(stale low={_stale[0]:.2f} high={_stale[1]:.2f} entry={price})"
                    )
        except Exception as _drain_err:
            logger.debug("AutoTrader buffer-drain error: %s", _drain_err)

        # ── Track B: reset estado do observer para esta zona ──────────────────
        # DIRECT consumiu o toque — Track B não deve herdar este contexto
        try:
            await self._reset_pb_for_direct(symbol, signal)
        except Exception as _pb_r_err:
            logger.debug("PB reset_for_direct error: %s", _pb_r_err)

        # Persiste cooldown state no MongoDB — sobrevive a restarts do backend
        await self._persist_cooldown_state()

    # ── Monitor de SL/TP ──

    async def _monitor_loop(self):
        """Monitora posições abertas e fecha quando SL ou TP atingido."""
        while self._status == LOOP_RUNNING:
            try:
                await asyncio.sleep(2)
                if not self._open_trades:
                    continue

                for trade_id, trade in list(self._open_trades.items()):
                    symbol    = trade["symbol"]
                    action    = trade["action"]
                    tp_price  = trade.get("take_profit_price") or 0
                    is_paper  = trade.get("paper", True)

                    # Consumir range de ticks do DataBento desde o último ciclo.
                    # period_low/period_high cobrem TODOS os ticks (incluindo spikes
                    # que ocorram entre ciclos de 30s) — nenhum cruzamento de SL/TP é perdido.
                    period_low, period_high, live_price = self._consume_live_range(symbol)
                    if live_price is None or live_price <= 0:
                        continue

                    # ── Acumular worst/best price para TODOS os trades (paper e live) ────────
                    # worst_price: pior preço adverso visto desde a entrada (ou desde reset).
                    # best_price:  melhor preço favorável visto desde a entrada.
                    # Alimentados pelo range de ticks reais do DataBento — tick-level accuracy.
                    entry_px   = trade.get("entry_price", 0) or 0
                    _p_low     = period_low  if period_low  is not None else live_price
                    _p_high    = period_high if period_high is not None else live_price
                    _cur_best  = trade.get("best_price");  _cur_best  = _cur_best  if _cur_best  is not None else entry_px
                    _cur_worst = trade.get("worst_price"); _cur_worst = _cur_worst if _cur_worst is not None else entry_px
                    if action == "buy":
                        best_px  = max(_cur_best,  _p_high)
                        worst_px = min(_cur_worst, _p_low)
                    else:
                        best_px  = min(_cur_best,  _p_low)
                        worst_px = max(_cur_worst, _p_high)
                    trade["best_price"]  = best_px
                    trade["worst_price"] = worst_px

                    sl_price = trade.get("stop_loss_price") or 0

                    # ── Detecção SL/TP — SEMPRE ANTES do trailing ratchet ─────────────────
                    # Ordem crítica: verificar o SL ACTUAL antes de o mover.
                    # Se corrermos o trailing primeiro e resetarmos worst_price, podemos
                    # esconder um stop que foi atingido no mesmo período — incorrecto.
                    # SL tem prioridade sobre TP (conservador: evita registar ganho após
                    # drawdown severo quando ambos são cruzados no mesmo período de ticks).
                    exit_reason      = None
                    exit_price_final = live_price
                    _wp = trade.get("worst_price")
                    _bp = trade.get("best_price")
                    check_adverse    = _wp if _wp is not None else live_price
                    check_favor      = _bp if _bp is not None else live_price

                    if action == "buy":
                        if check_adverse <= sl_price:
                            exit_reason      = "STOP_HIT"
                            exit_price_final = sl_price
                        elif check_favor >= tp_price:
                            exit_reason      = "TARGET_HIT"
                            exit_price_final = tp_price
                    else:
                        if check_adverse >= sl_price:
                            exit_reason      = "STOP_HIT"
                            exit_price_final = sl_price
                        elif check_favor <= tp_price:
                            exit_reason      = "TARGET_HIT"
                            exit_price_final = tp_price

                    # ── Trailing ratchet (paper e live) ──────────────────────────────────────
                    # Aplicado a ambos — para live, espelha o comportamento do Tradovate
                    # (trail_trigger + trail_stop enviados no payload PMT = be_pts).
                    # Sem isto, trades live fechados pelo trailing ficam "fantasma" abertos
                    # em MongoDB (stop_loss_price original nunca acompanha o trail real).
                    # Só corre se não houver exit_reason — evita mover o SL após stop atingido.
                    if not exit_reason:
                        be_pts = trade.get("breakeven_pts", 0) or 0
                        if be_pts > 0 and entry_px > 0:
                            # Activar trailing quando be_pts atingido pela primeira vez
                            if not trade.get("trailing_active", False):
                                be_triggered = (
                                    (action == "buy"  and best_px >= entry_px + be_pts) or
                                    (action == "sell" and best_px <= entry_px - be_pts)
                                )
                                if be_triggered:
                                    trade["trailing_active"] = True
                                    logger.info(
                                        f"AutoTrader TRAIL {'LIVE' if not is_paper else 'PAPER'}: "
                                        f"{symbol} {action.upper()} trailing activado "
                                        f"em best={best_px:.2f} (entry={entry_px}, be_pts={be_pts})"
                                    )

                            # Mover SL como ratchet: be_pts atrás do best_price.
                            # Ao mover o SL, reset worst_price para live_price (último tick
                            # do período) — o mínimo anterior era relativo ao SL antigo.
                            if trade.get("trailing_active", False):
                                if action == "buy":
                                    new_sl = round(best_px - be_pts, 2)
                                    if new_sl > trade["stop_loss_price"]:
                                        trade["stop_loss_price"] = new_sl
                                        trade["worst_price"] = live_price
                                else:
                                    new_sl = round(best_px + be_pts, 2)
                                    if new_sl < trade["stop_loss_price"]:
                                        trade["stop_loss_price"] = new_sl
                                        trade["worst_price"] = live_price

                    # Fechar o registo interno quando SL/TP é detectado.
                    # Live: skip_broker=True — o bracket OCO do Tradovate já fechou a posição;
                    #        apenas actualizamos o MongoDB sem chamar PMT novamente.
                    # Paper: skip_broker=False — não há broker real, _close_trade ignora PMT.
                    if exit_reason:
                        await self._close_trade(
                            trade_id, exit_price_final, exit_reason,
                            skip_broker=not is_paper,
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ScalpAutoTrader monitor error: {e}")
                await asyncio.sleep(5)

    def _get_live_price(self, symbol: str) -> Optional[float]:
        """Retorna apenas o último preço. Usar _consume_live_range() no monitor de posições."""
        try:
            if hasattr(self._live_data_service, 'buffers') and symbol in self._live_data_service.buffers:
                return self._live_data_service.buffers[symbol].last_price
            if hasattr(self._live_data_service, 'get_live_data'):
                data = self._live_data_service.get_live_data(symbol)
                if data:
                    return data.get("last_price")
        except Exception:
            pass
        return None

    def _consume_live_range(self, symbol: str) -> tuple:
        """Consome e retorna (period_low, period_high, last_price) do buffer DataBento.
        Inclui TODOS os ticks desde a última chamada — nenhum cruzamento de SL/TP é perdido,
        independentemente da frequência do ciclo do monitor (30s ou outra).
        Retorna (last, last, last) se o buffer não suportar o método."""
        try:
            if hasattr(self._live_data_service, 'buffers') and symbol in self._live_data_service.buffers:
                buf = self._live_data_service.buffers[symbol]
                if hasattr(buf, 'consume_period_range'):
                    low, high, last = buf.consume_period_range()
                    return low, high, last
                # Fallback: buffer sem consume_period_range (versão antiga)
                p = buf.last_price
                return p, p, p
            if hasattr(self._live_data_service, 'get_live_data'):
                data = self._live_data_service.get_live_data(symbol)
                if data:
                    p = data.get("last_price") or 0.0
                    return p, p, p
        except Exception:
            pass
        return 0.0, 0.0, 0.0

    async def _close_trade(
        self, trade_id: str, exit_price: float, reason: str, skip_broker: bool = False
    ):
        """Fecha um trade internamente (MongoDB + memória) e, por omissão, envia close ao broker.

        skip_broker=True: usado por flatten_all_trades, que já enviou cancel + close via PMT
        agregado por símbolo — evita duplicar chamadas ao broker.
        """
        trade = self._open_trades.pop(trade_id, None)
        if trade is None:
            return

        symbol   = trade["symbol"]
        action   = trade["action"]
        qty      = trade["quantity"]
        entry    = trade["entry_price"]
        paper    = trade["paper"]
        now      = datetime.now(timezone.utc)
        _ca = trade["created_at"]
        created = _ca if isinstance(_ca, datetime) else datetime.fromisoformat(str(_ca))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        duration = round((now - created).total_seconds(), 1)

        pnl_pts = compute_pnl_pts(action, entry, exit_price)
        pnl_usd = calc_pnl_usd(symbol, pnl_pts, qty)

        # Fecha ordem live via PickMyTrade (close oposto).
        # Ignorado quando skip_broker=True (flatten_all_trades já enviou o flatten).
        if not paper and not skip_broker:
            webhook_url = trade.get("webhook_url") or ""
            config = await self._config_getter()
            webhook_url = webhook_url or config.get("webhook_url", "")
            if webhook_url and _is_safe_webhook_url(webhook_url):
                token   = config.get("pmt_token", "")
                acct    = config.get("pmt_account_id", "")
                sym     = trade.get("symbol", "MNQ")
                pmt_sym = f"{sym.upper()}1!"
                try:
                    client = self._get_http_client()
                    # PMT recomenda data:"close" para fechar posição individual —
                    # cancela brackets SL/TP automaticamente sem risco de reverse
                    await client.post(webhook_url, json={
                        "symbol":                pmt_sym,
                        "strategy_name":         "QuantumScalp",
                        "date":                  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "data":                  "close",
                        "quantity":              qty,
                        "price":                 0,
                        "tp":                    0,
                        "sl":                    0,
                        "trail":                 0,
                        "token":                 token,
                        "account_id":            acct,
                        "pyramid":               False,
                        "same_direction_ignore": True,
                        "reverse_order_close":   False,
                        "multiple_accounts": [{
                            "token": token, "account_id": acct,
                            "risk_percentage": 0, "quantity_multiplier": 1,
                        }],
                    })
                    logger.info(f"AutoTrader: PMT close live {pmt_sym} qty={qty}")
                except Exception as e:
                    logger.warning(f"AutoTrader: erro ao fechar live {trade_id}: {e}")

        # pnl ponderado pelo lote (0.5 / 0.75 / 1.0 conforme regime)
        lot_fraction      = float(trade.get("s2_risk_modifier") or 1.0)
        pnl_pts_weighted  = round(pnl_pts * lot_fraction, 4)

        update = {
            "state": "CLOSED",
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl_pts": pnl_pts,
            "pnl_pts_weighted": pnl_pts_weighted,
            "lot_fraction": lot_fraction,
            "pnl_usd": pnl_usd,
            "duration_sec": duration,
            "closed_at": now.isoformat(),
            # Persistir estado do trailing ratchet — estava só em memória
            "trailing_active": trade.get("trailing_active", False),
            "best_price":      trade.get("best_price"),
            "worst_price":     trade.get("worst_price"),
        }
        await self._database["scalp_trades"].update_one(
            {"id": trade_id},
            {"$set": update, "$push": {"events": {"ts": now.isoformat(), "event": reason, "price": exit_price, "pnl_usd": pnl_usd}}}
        )

        # Estatísticas
        self._session_stats["open"]    = len(self._open_trades)
        self._session_stats["pnl_pts"] = round(self._session_stats["pnl_pts"] + pnl_pts, 4)
        self._session_stats["pnl_usd"] = round(self._session_stats["pnl_usd"] + pnl_usd, 2)

        # G-3: circuit breaker — rastreia PnL diário e perdas consecutivas
        self._session_stats["daily_pnl_usd"] = round(
            self._session_stats["daily_pnl_usd"] + pnl_usd, 2
        )
        if pnl_pts > 0:
            self._session_stats["wins"] += 1
            self._session_stats["consecutive_losses"] = 0   # reset em ganho
        else:
            self._session_stats["losses"] += 1
            self._session_stats["consecutive_losses"] += 1

        label = "WIN" if pnl_pts > 0 else "LOSS"
        logger.info(
            f"AutoTrader {label}: {symbol} {action.upper()} | {reason} @ {exit_price} | "
            f"PnL={pnl_pts:+.2f}pts / ${pnl_usd:+.2f} | dur={duration}s | "
            f"consec_losses={self._session_stats['consecutive_losses']} "
            f"daily_pnl=${self._session_stats['daily_pnl_usd']:+.2f}"
        )
        await self._persist_session_stats()

    # ── Fechamento manual ──

    async def close_trade_manual(self, trade_id: str, live_price: float) -> bool:
        if trade_id in self._open_trades:
            await self._close_trade(trade_id, live_price, "MANUAL_CLOSE")
            return True
        # Tenta fechar via DB (posição que não está em memória)
        trades_col = self._database["scalp_trades"]
        doc = await trades_col.find_one({"id": trade_id, "state": "OPEN"})
        if doc:
            pnl_pts = compute_pnl_pts(doc["action"], doc["entry_price"], live_price)
            pnl_usd = calc_pnl_usd(doc["symbol"], pnl_pts, doc.get("quantity", 1))
            now = datetime.now(timezone.utc)
            close_update = {
                "state": "CLOSED", "exit_price": live_price, "exit_reason": "MANUAL_CLOSE",
                "pnl_pts": pnl_pts, "pnl_usd": pnl_usd,
                "closed_at": now.isoformat(),
                "duration_sec": round((now - datetime.fromisoformat(doc["created_at"])).total_seconds(), 1),
            }
            await trades_col.update_one(
                {"id": trade_id},
                {
                    "$set": close_update,
                    "$push": {"events": {"ts": now.isoformat(), "event": "MANUAL_CLOSE",
                                         "price": live_price, "pnl_usd": pnl_usd}},
                }
            )
            return True
        return False

    # ── Reload ──

    async def _persist_cooldown_state(self):
        """Persiste _last_trade_ts no MongoDB para sobreviver a restarts do backend.

        Usa upsert no documento { _id: "cooldown_state" } da colecção
        scalp_trader_state. Os timestamps são armazenados como strings ISO 8601.
        """
        try:
            payload = {
                sym: ts.isoformat()
                for sym, ts in self._last_trade_ts.items()
            }
            await self._database["scalp_trader_state"].update_one(
                {"_id": "cooldown_state"},
                {"$set": {"last_trade_ts": payload, "updated_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
        except Exception as e:
            logger.warning("AutoTrader: falha ao persistir cooldown state: %s", e)

    async def _persist_session_stats(self):
        """Persiste contadores G-3/G-4 e circuit breaker no MongoDB.

        Usa _id="session_stats" na colecção scalp_trader_state.
        Inclui date_et para que o restore ignore stats de sessões anteriores.
        """
        if self._database is None:
            return
        try:
            today = datetime.now(_ET).strftime("%Y-%m-%d")
            payload = {
                "date_et":                today,
                "consecutive_losses":     self._session_stats.get("consecutive_losses", 0),
                "daily_pnl_usd":          self._session_stats.get("daily_pnl_usd", 0.0),
                "daily_trades":           self._session_stats.get("daily_trades", 0),
                "circuit_breaker_active": self._session_stats.get("circuit_breaker_active", False),
                "circuit_breaker_reason": self._session_stats.get("circuit_breaker_reason"),
                "updated_at":             datetime.now(timezone.utc).isoformat(),
            }
            await self._database["scalp_trader_state"].update_one(
                {"_id": "session_stats"},
                {"$set": payload},
                upsert=True,
            )
        except Exception as e:
            logger.warning("AutoTrader: falha ao persistir session_stats: %s", e)

    async def _reload_open_trades(self):
        """Recarrega trades abertos do MongoDB ao iniciar o loop (crash recovery).

        Também restaura _last_trade_ts da colecção scalp_trader_state para que
        o cooldown por símbolo sobreviva a restarts do backend.

        Se existirem posições recuperadas, marca _was_in_trading_window=True para que
        a primeira iteração do loop fora de janela dispare o EOD flatten automaticamente.
        Sem este flag, a transição True→False nunca aconteceria e as posições ficariam
        abertas indefinidamente após restart fora de horário.
        """
        # ── Restaura cooldown state ──────────────────────────────────────────
        try:
            state_doc = await self._database["scalp_trader_state"].find_one(
                {"_id": "cooldown_state"}
            )
            if state_doc and isinstance(state_doc.get("last_trade_ts"), dict):
                restored = 0
                for sym, ts_str in state_doc["last_trade_ts"].items():
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        # Garante timezone-aware
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        self._last_trade_ts[sym] = ts
                        restored += 1
                    except (ValueError, TypeError):
                        pass
                if restored:
                    logger.info(
                        "AutoTrader: cooldown state restaurado para %d símbolo(s): %s",
                        restored,
                        {s: self._last_trade_ts[s].isoformat() for s in self._last_trade_ts},
                    )
            else:
                logger.info("AutoTrader: sem cooldown state no MongoDB — arranque limpo")
        except Exception as e:
            logger.warning("AutoTrader: erro ao restaurar cooldown state: %s", e)

        # ── Restaura circuit breaker e contadores G-3/G-4 ────────────────────
        try:
            stats_doc = await self._database["scalp_trader_state"].find_one(
                {"_id": "session_stats"}
            )
            today = datetime.now(_ET).strftime("%Y-%m-%d")
            if stats_doc and stats_doc.get("date_et") == today:
                self._session_stats["consecutive_losses"]     = stats_doc.get("consecutive_losses", 0)
                self._session_stats["daily_pnl_usd"]          = stats_doc.get("daily_pnl_usd", 0.0)
                self._session_stats["daily_trades"]           = stats_doc.get("daily_trades", 0)
                self._session_stats["circuit_breaker_active"] = stats_doc.get("circuit_breaker_active", False)
                self._session_stats["circuit_breaker_reason"] = stats_doc.get("circuit_breaker_reason")
                logger.info(
                    "AutoTrader: G-3/G-4 restaurados — consecutive_losses=%d "
                    "daily_pnl=$%.2f daily_trades=%d circuit_breaker=%s",
                    self._session_stats["consecutive_losses"],
                    self._session_stats["daily_pnl_usd"],
                    self._session_stats["daily_trades"],
                    self._session_stats["circuit_breaker_active"],
                )
            else:
                logger.info("AutoTrader: sem session_stats de hoje no MongoDB — contadores G-3/G-4 a zero")
        except Exception as e:
            logger.warning("AutoTrader: erro ao restaurar session_stats: %s", e)

        # ── Recarrega posições abertas ────────────────────────────────────────
        try:
            col = self._database["scalp_trades"]
            cursor = col.find({"state": "OPEN"}, {"_id": 0})
            count = 0
            async for doc in cursor:
                self._open_trades[doc["id"]] = doc
                count += 1
            self._session_stats["open"] = count
            if count > 0:
                # Posições recuperadas implicam que estavam em janela activa antes do crash.
                # Garante que o loop deteceta a transição "estava em janela → fora de janela"
                # e dispara o EOD flatten se o restart ocorrer fora de horário.
                self._was_in_trading_window = True
                logger.info(
                    f"ScalpAutoTrader: {count} posição(ões) recuperada(s) do MongoDB — "
                    f"_was_in_trading_window=True (EOD flatten activado se fora de janela)"
                )
            else:
                logger.info("ScalpAutoTrader: sem posições abertas no MongoDB")
        except Exception as e:
            logger.warning(f"ScalpAutoTrader: erro ao recarregar posições: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Track B — Pullback Observer (log_only=True)
    # ══════════════════════════════════════════════════════════════════════════

    # Parâmetros fixos do observer
    _PB_TIMEOUT_SEC    : int   = 300       # 5 min desde TOUCHED
    _PB_BROKEN_CYCLES  : int   = 3         # ciclos × 15s além de 2× tolerance
    _PB_OFI_MIN        : float = 0.3       # |OFI_FAST| mínimo no retorno
    _PB_SPEED_MIN      : float = 0.8       # speed_ratio mínimo no retorno (P25 da dist. real; era 1.2 = P75, bloqueava 78% do mercado)
    _PB_RETURN_PCT     : float = 0.5       # 50% do pullback percorrido de volta
    _PB_MAX_STATES     : int   = 3         # cap de zonas activas por símbolo
    _PB_TOLERANCE      : Dict[str, float] = {"MNQ": 2.5, "MES": 0.5}
    _PB_RETURN_MIN_ABS : Dict[str, float] = {"MNQ": 1.0, "MES": 0.5}

    async def _update_pb_observer(self, symbol: str, signal, config: Dict) -> None:
        """
        Track B observer: rastreia estados TOUCHED → PULLBACK → RETURN por zona.
        Não executa nenhum trade. Grava todos os eventos em pb_state_log.
        log_only=True até N ≥ 30 DIRECT trades com outcome.
        """
        now          = datetime.now(timezone.utc)
        price        = signal.last_price or 0.0
        ofi_fast     = signal.ofi_fast or 0.0
        speed        = signal.speed_ratio or 0.0
        session      = _get_trade_session_label()
        active_zone  = signal.active_zone          # dict | None
        quality      = _normalize_quality(getattr(signal, "s2_quality", "WEAK"))
        tolerance    = self._PB_TOLERANCE.get(symbol, 2.5)
        ret_min_abs  = self._PB_RETURN_MIN_ABS.get(symbol, 1.0)

        # ── 1. Detectar novo TOUCHED ──────────────────────────────────────────
        if active_zone and quality in ("MODERATE", "STRONG"):
            z_type  = active_zone.get("type", "UNKNOWN")
            z_level = float(active_zone.get("level", 0.0))
            z_dir   = active_zone.get("direction", "BUY")
            key     = f"{symbol}:{z_type}:{z_level:.2f}"

            existing = self._pb_state.get(key, {})
            ex_state = existing.get("state", "IDLE")

            if ex_state == "IDLE":
                # Cap: máx 3 estados activos por símbolo
                active_sym = [
                    k for k, v in self._pb_state.items()
                    if k.startswith(f"{symbol}:") and v.get("state") not in ("IDLE", None)
                ]
                if len(active_sym) < self._PB_MAX_STATES:
                    direct_active = any(
                        t.get("symbol") == symbol for t in self._open_trades.values()
                    )
                    gamma = ""
                    if signal.macro_context:
                        gamma = signal.macro_context.get("gamma_regime", "")
                    s1r = str(
                        signal.s1_regime.value
                        if hasattr(signal.s1_regime, "value")
                        else (signal.s1_regime or "")
                    )
                    # Parâmetros de risco da zona — usados na execução Track B no RETURN
                    _sl_price_touch = signal.s3_stop_loss_price
                    _tp_price_touch = signal.s3_take_profit_price
                    _sl_pts = abs(price - _sl_price_touch) if _sl_price_touch else 4.0
                    _tp_pts = abs(_tp_price_touch - price) if _tp_price_touch else 8.0

                    new_state = {
                        "state":                   "TOUCHED",
                        "touch_ts":                now,
                        "touch_price":             price,
                        "zone_level":              z_level,
                        "zone_type":               z_type,
                        "direction":               z_dir,
                        "score_at_touch":          quality,
                        "gamma_regime":            gamma,
                        "s1_regime":               s1r,
                        "s1_confidence":           round(float(signal.s1_confidence or 0.0), 2),
                        "session":                 session,
                        "direct_active_at_touch":  direct_active,
                        "expiry_ts":               now + timedelta(seconds=self._PB_TIMEOUT_SEC),
                        "broken_cycles":           0,
                        "pullback_extreme":        None,
                        "pullback_started_ts":     None,
                        # Risk params snapshot (usados na execução RETURN)
                        "sl_pts":                  round(_sl_pts, 4),
                        "tp_pts":                  round(_tp_pts, 4),
                        "be_pts":                  float(getattr(signal, "s3_breakeven", 0.75) or 0.75),
                        "qty":                     int(getattr(signal, "s3_quantity", 1) or 1),
                    }
                    self._pb_state[key] = new_state
                    await self._pb_log_event(
                        key, new_state, "IDLE", "TOUCHED", "zone_detected",
                        price, ofi_fast, speed,
                    )
                else:
                    # Cap atingido: evict estado de menor score se possível
                    candidates = [
                        k for k in active_sym
                        if self._pb_state[k].get("state") == "TOUCHED"
                    ]
                    if candidates:
                        evict_key = candidates[0]
                        old = self._pb_state[evict_key]
                        old["state"] = "IDLE"
                        await self._pb_log_event(
                            evict_key, old, "TOUCHED", "IDLE", "cap_evicted",
                            price, ofi_fast, speed,
                        )
                        del self._pb_state[evict_key]

        # ── 2. Actualizar estados existentes ─────────────────────────────────
        for key in list(self._pb_state.keys()):
            if not key.startswith(f"{symbol}:"):
                continue

            state     = self._pb_state[key]
            cur_state = state.get("state", "IDLE")
            if cur_state == "IDLE":
                continue

            z_level    = state["zone_level"]
            z_dir      = state["direction"]
            touch_ts   = state["touch_ts"]
            touch_price = state["touch_price"]
            expiry_ts  = state["expiry_ts"]

            # ── Timeout ──────────────────────────────────────────────────────
            if now > expiry_ts:
                state["state"] = "IDLE"
                await self._pb_log_event(
                    key, state, cur_state, "IDLE", "timeout",
                    price, ofi_fast, speed,
                )
                continue

            # ── Nível quebrado (3 ciclos consecutivos além de 2× tolerance) ──
            if z_dir == "BUY":
                broken_now = price < (z_level - 2 * tolerance)
            else:
                broken_now = price > (z_level + 2 * tolerance)

            if broken_now:
                state["broken_cycles"] = state.get("broken_cycles", 0) + 1
                if state["broken_cycles"] >= self._PB_BROKEN_CYCLES:
                    state["state"] = "IDLE"
                    await self._pb_log_event(
                        key, state, cur_state, "IDLE", "level_broken",
                        price, ofi_fast, speed,
                    )
                    continue
            else:
                state["broken_cycles"] = 0

            # ── TOUCHED ───────────────────────────────────────────────────────
            if cur_state == "TOUCHED":
                # Aguarda início do pullback. Score não invalida — durante o pullback
                # o preço afasta-se da zona e o score cai naturalmente; é exactamente
                # para isso que o Track B existe. Só level_broken ou direct_reset
                # podem cancelar o Track B nesta fase.
                pb_min = tolerance  # 1× tolerance
                if z_dir == "BUY":
                    pb_started = price < (touch_price - pb_min)
                else:
                    pb_started = price > (touch_price + pb_min)

                if pb_started:
                    state["state"]               = "PULLBACK"
                    state["pullback_extreme"]     = price
                    state["pullback_started_ts"]  = now
                    await self._pb_log_event(
                        key, state, "TOUCHED", "PULLBACK", "pullback_started",
                        price, ofi_fast, speed,
                    )

            # ── PULLBACK ──────────────────────────────────────────────────────
            elif cur_state == "PULLBACK":
                # Actualizar extremo do pullback
                if z_dir == "BUY":
                    state["pullback_extreme"] = min(state.get("pullback_extreme", price), price)
                else:
                    state["pullback_extreme"] = max(state.get("pullback_extreme", price), price)

                pb_extreme = state["pullback_extreme"]

                # Verificar condição de retorno
                if z_dir == "BUY":
                    pullback_range = touch_price - pb_extreme   # positivo
                    ret_thresh     = pb_extreme + max(pullback_range * self._PB_RETURN_PCT, ret_min_abs)
                    returning      = price >= ret_thresh
                else:
                    pullback_range = pb_extreme - touch_price   # positivo
                    ret_thresh     = pb_extreme - max(pullback_range * self._PB_RETURN_PCT, ret_min_abs)
                    returning      = price <= ret_thresh

                if returning and pullback_range > 0:
                    ofi_ok    = ofi_fast >= self._PB_OFI_MIN  if z_dir == "BUY" else ofi_fast <= -self._PB_OFI_MIN
                    speed_ok  = speed >= self._PB_SPEED_MIN
                    score_ok  = quality in ("MODERATE", "STRONG")
                    ret_pct   = min(abs(price - pb_extreme) / pullback_range, 1.0) if pullback_range > 0 else 0.0

                    gates = {
                        "ofi":        ofi_ok,
                        "speed":      speed_ok,
                        "return_pct": True,
                        "score":      score_ok,
                    }
                    would_trigger = all(gates.values())

                    pb_started_ts = state.get("pullback_started_ts")
                    pb_dur = (
                        round((now - pb_started_ts).total_seconds(), 1)
                        if pb_started_ts else None
                    )

                    state["state"] = "IDLE"

                    # ── Track B execution ────────────────────────────────────
                    tb_trade_id = None
                    if would_trigger:
                        _paper   = config.get("paper_trading", True)
                        _tb_on   = config.get("track_b_enabled", False)
                        _ready   = self._track_b_readiness.get("readiness_met", False)

                        # Track B executa sempre que habilitado:
                        # - sistema em paper → paper
                        # - sistema em live + readiness_met → live
                        # - sistema em live + readiness NÃO met → força paper
                        #   (valida o padrão sem risco real enquanto acumula dados)
                        _force_paper = (not _paper) and (not _ready)

                        # Guarda: sem posição aberta na mesma direcção para este símbolo
                        _tb_action = "buy" if z_dir == "BUY" else "sell"
                        _same_open = any(
                            t.get("symbol") == symbol and t.get("action") == _tb_action
                            for t in self._open_trades.values()
                        )

                        if _tb_on and not _same_open:
                            _sl_pts = state.get("sl_pts", 4.0) or 4.0
                            _tp_pts = state.get("tp_pts", 8.0) or 8.0
                            _be_pts = state.get("be_pts", 0.75) or 0.75
                            _qty    = state.get("qty", 1) or 1
                            if z_dir == "BUY":
                                _sl = price - _sl_pts
                                _tp = price + _tp_pts
                            else:
                                _sl = price + _sl_pts
                                _tp = price - _tp_pts
                            tb_trade_id = await self._execute_pb_trade(
                                symbol, _tb_action, price, _sl, _tp,
                                _be_pts, _qty, state, config,
                                force_paper=_force_paper,
                            )
                        elif _tb_on and _same_open:
                            logger.debug(
                                "Track B RETURN bloqueado: posição aberta %s %s",
                                symbol, _tb_action
                            )

                    await self._pb_log_event(
                        key, state, "PULLBACK", "RETURN", "return_confirmed",
                        price, ofi_fast, speed,
                        extra={
                            "pullback_distance_pts": round(pullback_range, 4),
                            "pullback_duration_sec": pb_dur,
                            "return_pct":            round(ret_pct, 3),
                            "ofi_fast_at_return":    round(ofi_fast, 4),
                            "speed_ratio_at_return": round(speed, 3),
                            "gates_at_return":       gates,
                            "would_have_triggered":  would_trigger,
                            "tb_trade_id":           tb_trade_id,
                        },
                    )

    async def _execute_pb_trade(
        self,
        symbol: str,
        action: str,
        price: float,
        sl_price: float,
        tp_price: float,
        be_pts: float,
        qty: int,
        state: Dict,
        config: Dict[str, Any],
        force_paper: bool = False,
    ) -> Optional[str]:
        """
        Executa uma entrada Track B (pullback RETURN confirmado).
        Replica a lógica de _execute_trade mas constrói o trade_doc a partir dos
        parâmetros do estado pb_observer — sem precisar de um ScalpSignal completo.
        force_paper=True: força simulação mesmo com sistema em LIVE
                          (usado quando readiness_met=False — valida padrão sem risco real).
        Devolve o trade_id em caso de sucesso, ou None se abortado.
        """
        paper       = config.get("paper_trading", True) or force_paper
        webhook_url = config.get("webhook_url", "") or ""

        sl_price = align_sl_to_tick(sl_price, action, symbol)
        tp_price = round_to_tick(tp_price, symbol)

        if not action or price <= 0 or sl_price is None or tp_price is None:
            logger.warning(
                "Track B _execute_pb_trade ABORTADO: %s action=%r price=%s sl=%s tp=%s",
                symbol, action, price, sl_price, tp_price,
            )
            return None

        trade_id = str(uuid.uuid4())
        now      = datetime.now(timezone.utc)

        trade_doc: Dict[str, Any] = {
            "id":               trade_id,
            "symbol":           symbol,
            "tradovate_symbol": _tradovate_symbol(symbol),
            "mode":             "ZONES",
            "source":           "auto",
            "action":           action,
            "quantity":         qty,
            "entry_price":      price,
            "stop_loss_price":  sl_price,
            "take_profit_price": tp_price,
            "breakeven_pts":    be_pts,
            "trailing_active":  False,
            "best_price":       None,
            "paper":            paper,
            "s1_regime":        state.get("s1_regime"),
            "s1_confidence":    state.get("s1_confidence"),
            "s2_quality":       state.get("score_at_touch"),
            "s2_risk_modifier": None,
            "ofi_fast":         None,
            "ofi_slow":         None,
            "absorption_flag":  None,
            "absorption_side":  None,
            "cvd":              None,
            "cvd_trend":        None,
            "atr_m1":           None,
            "zone_type":        state.get("zone_type"),
            "score_breakdown":  None,
            "session_minutes":  None,
            "session_label":    _get_trade_session_label(),
            "state":            "OPEN",
            "exit_price":       None,
            "exit_reason":      None,
            "pnl_pts":          None,
            "pnl_usd":          None,
            "duration_sec":     None,
            "created_at":       now.isoformat(),
            "closed_at":        None,
            "events":           [{"ts": now.isoformat(), "event": "OPEN", "price": price, "source": "track_b"}],
            "signalstack_response": None,
            "signalstack_ok":   None,
            "entry_mechanism":  "TRACK_B",
            # Contexto do pullback
            "pb_zone_key":      f"{symbol}:{state.get('zone_type','')}:{state.get('zone_level',0):.2f}",
            "pb_touch_price":   state.get("touch_price"),
            "pb_pullback_extreme": state.get("pullback_extreme"),
        }

        if paper:
            trade_doc["fill_type"] = "paper_simulated"
            trade_doc["tb_forced_paper"] = force_paper  # True = live mode mas readiness pendente
            self._session_stats["paper_trades"] += 1
            _paper_reason = "forçado (live mode, readiness pendente)" if force_paper else "paper mode"
            logger.info(
                "Track B PAPER [%s]: %s %s @ %.2f | SL=%.2f TP=%.2f be=%.2f qty=%d",
                _paper_reason, symbol, action.upper(), price, sl_price, tp_price, be_pts, qty,
            )
        else:
            if not webhook_url:
                logger.warning("Track B LIVE: sem webhook_url configurado, ignorando %s", symbol)
                return None
            if not _is_safe_webhook_url(webhook_url):
                logger.error("Track B LIVE: webhook_url rejeitada (SSRF): %s", webhook_url[:80])
                return None
            from datetime import datetime as _dt2
            sl_pts_rel  = round(abs(price - sl_price), 2)
            tp_pts_rel  = round(abs(tp_price - price), 2)
            TICK        = 0.25
            pmt_token   = config.get("pmt_token", "")
            pmt_acct    = config.get("pmt_account_id", "")
            trail_on    = 1 if (be_pts and be_pts > 0) else 0
            pmt_symbol  = f"{symbol.upper()}1!"
            payload = {
                "symbol":                pmt_symbol,
                "strategy_name":         "QuantumScalpB",
                "date":                  _dt2.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data":                  action,
                "quantity":              qty,
                "risk_percentage":       0,
                "price":                 0,
                "tp":                    0,
                "percentage_tp":         0,
                "dollar_tp":             tp_pts_rel,
                "sl":                    0,
                "dollar_sl":             sl_pts_rel,
                "percentage_sl":         0,
                "trail":                 trail_on,
                "trail_stop":            be_pts if trail_on else 0,
                "trail_trigger":         be_pts if trail_on else 0,
                "trail_freq":            TICK if trail_on else 0,
                "update_tp":             False,
                "update_sl":             False,
                "breakeven":             0,
                "breakeven_offset":      0,
                "token":                 pmt_token,
                "account_id":            pmt_acct,
                "pyramid":               False,
                "same_direction_ignore": True,
                "reverse_order_close":   True,
                "multiple_accounts": [{
                    "token":               pmt_token,
                    "account_id":          pmt_acct,
                    "risk_percentage":     0,
                    "quantity_multiplier": 1,
                }],
            }
            try:
                client = self._get_http_client()
                resp = await client.post(webhook_url, json=payload)
                ss_ok = resp.status_code < 300
                trade_doc["signalstack_response"] = {"status_code": resp.status_code, "response": resp.text[:500], "ok": ss_ok}
                trade_doc["signalstack_ok"]  = ss_ok
                trade_doc["fill_type"]       = "live_pickmytrade"
                self._session_stats["live_trades"] += 1
                logger.info(
                    "Track B LIVE: %s %s @ %.2f → PMT %d sl=%.2f tp=%.2f",
                    symbol, action.upper(), price, resp.status_code, sl_pts_rel, tp_pts_rel,
                )
            except Exception as e:
                logger.error("Track B LIVE: erro PMT para %s: %s", symbol, e)
                self._errors.append(f"TrackB PMT error {symbol}: {e}")
                trade_doc["signalstack_ok"] = False
                trade_doc["fill_type"]      = "live_error"

        await self._database["scalp_trades"].insert_one({**trade_doc, "_id": trade_id})
        self._open_trades[trade_id] = trade_doc
        self._last_trade_ts[symbol] = now
        self._session_stats["total_trades"] += 1
        self._session_stats["daily_trades"]  += 1
        self._session_stats["open"] = len(self._open_trades)

        # Buffer drain (mesma lógica do DIRECT)
        try:
            if (hasattr(self._live_data_service, "buffers")
                    and symbol in self._live_data_service.buffers):
                _buf = self._live_data_service.buffers[symbol]
                if hasattr(_buf, "consume_period_range"):
                    _stale = _buf.consume_period_range()
                    logger.debug(
                        "Track B buffer-drain %s: stale low=%.2f high=%.2f entry=%.2f",
                        symbol, _stale[0], _stale[1], price,
                    )
        except Exception as _drain_err:
            logger.debug("Track B buffer-drain error: %s", _drain_err)

        logger.warning(
            "Track B EXECUTED %s %s @ %.2f | SL=%.2f TP=%.2f | trade_id=%s | paper=%s",
            symbol, action.upper(), price, sl_price, tp_price, trade_id, paper,
        )
        return trade_id

    async def _pb_log_event(
        self,
        key: str,
        state: Dict,
        state_from: str,
        state_to: str,
        reason: str,
        price: float,
        ofi_fast: float,
        speed: float,
        extra: Optional[Dict] = None,
    ) -> None:
        """Grava evento de transição do Track B em pb_state_log (MongoDB)."""
        parts = key.split(":")
        symbol   = parts[0] if len(parts) > 0 else ""
        z_type   = parts[1] if len(parts) > 1 else ""
        try:
            level = float(parts[2]) if len(parts) > 2 else 0.0
        except (ValueError, IndexError):
            level = 0.0

        ex = extra or {}
        doc = {
            "ts":                        datetime.now(timezone.utc),
            "symbol":                    symbol,
            "zone_type":                 z_type,
            "level":                     level,
            "direction":                 state.get("direction", ""),
            "session":                   state.get("session", ""),
            "state_from":                state_from,
            "state_to":                  state_to,
            "event":                     state_to,
            "reason":                    reason,
            "price_at_event":            price,
            "score_at_touch":            state.get("score_at_touch", ""),
            "gamma_regime_at_touch":     state.get("gamma_regime", ""),
            "s1_regime_at_touch":        state.get("s1_regime", ""),
            "s1_confidence_at_touch":    state.get("s1_confidence", 0.0),
            "direct_trade_active_at_touch": state.get("direct_active_at_touch", False),
            # Métricas do pullback (None se ainda não disponíveis)
            "pullback_distance_pts":     ex.get("pullback_distance_pts"),
            "pullback_duration_sec":     ex.get("pullback_duration_sec"),
            "return_pct":                ex.get("return_pct"),
            "ofi_fast_at_return":        ex.get("ofi_fast_at_return"),
            "speed_ratio_at_return":     ex.get("speed_ratio_at_return"),
            "gates_at_return":           ex.get("gates_at_return"),
            "would_have_triggered":      ex.get("would_have_triggered"),
            "tb_trade_id":               ex.get("tb_trade_id"),
        }
        try:
            if self._database is not None:
                await self._database["pb_state_log"].insert_one(doc)
        except Exception as _e:
            logger.debug("pb_state_log write error: %s", _e)

    async def _check_track_b_readiness(self) -> None:
        """
        Verifica critério de activação Track B live:
          - N ≥ 10 eventos RETURN gravados em pb_state_log
          - ≥ 2 datas de calendário distintas (garante sessões de tipos diferentes)
        Executa a cada 10 min. Loga WARNING proeminente quando critério é atingido.
        """
        import time as _time
        now_ts = _time.monotonic()
        if now_ts - self._track_b_readiness_checked_ts < 600:
            return
        self._track_b_readiness_checked_ts = now_ts

        if self._database is None:
            return
        try:
            col = self._database["pb_state_log"]
            n_return = await col.count_documents({"event": "RETURN"})
            pipeline = [
                {"$match": {"event": "RETURN"}},
                {"$group": {"_id": {
                    "$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}
                }}},
                {"$count": "n_dates"},
            ]
            agg = await col.aggregate(pipeline).to_list(length=1)
            n_dates = agg[0]["n_dates"] if agg else 0

            was_met = self._track_b_readiness["readiness_met"]
            met     = (n_return >= 10 and n_dates >= 2)

            self._track_b_readiness.update({
                "n_return_events": n_return,
                "distinct_dates":  n_dates,
                "readiness_met":   met,
                "last_checked":    datetime.now(timezone.utc).isoformat(),
            })

            if met and not was_met:
                logger.warning(
                    "★★★ TRACK B READINESS ATINGIDO ★★★ "
                    "n_return=%d | datas=%d | "
                    "Critério cumprido: N≥10 RETURN events, ≥2 sessões distintas. "
                    "Track B pode ser activado para live.",
                    n_return, n_dates,
                )
            else:
                logger.info(
                    "Track B readiness: %d/10 RETURN events | %d/2 datas distintas | met=%s",
                    n_return, n_dates, met,
                )
        except Exception as _e:
            logger.debug("Track B readiness check error: %s", _e)

    async def _reset_pb_for_direct(self, symbol: str, signal) -> None:
        """
        Quando um DIRECT trade executa numa zona, reseta o estado Track B dessa zona
        para IDLE. O pullback pós-DIRECT é um evento novo — não deve herdar
        o contexto do toque que o Track A já consumiu.
        """
        z_type = (
            signal.zone_type_str
            or (signal.active_zone.get("type") if signal.active_zone else None)
        )
        z_level_raw = (
            signal.active_zone.get("level") if signal.active_zone else None
        )
        if not z_type or z_level_raw is None:
            return

        key = f"{symbol}:{z_type}:{float(z_level_raw):.2f}"
        state = self._pb_state.get(key)
        if state and state.get("state") not in ("IDLE", None):
            prev = state.get("state", "TOUCHED")
            state["state"] = "IDLE"
            await self._pb_log_event(
                key, state, prev, "IDLE", "direct_reset",
                signal.last_price or 0.0,
                signal.ofi_fast or 0.0,
                signal.speed_ratio or 0.0,
            )
            logger.debug(
                "PB Track B: %s reset para IDLE (DIRECT executou em %s @ %.2f)",
                key, symbol, float(z_level_raw),
            )


    # ── Funil de sinais ─────────────────────────────────────────────────────────

    async def _log_signal_event(
        self,
        signal,
        session: str,
        gate_outcome: str,
        gate_reason: str,
        trade_id: Optional[str] = None,
        paper: bool = False,
    ) -> None:
        """
        Persiste um evento de sinal no MongoDB (colecção scalp_signal_log).
        Deduplicação: mesmo (symbol, zone_type, gate_outcome) não é re-logado
        mais do que uma vez por minuto, excepto EXECUTED que é sempre logado.
        """
        if self._database is None:
            return
        symbol    = getattr(signal, "symbol", "?")
        zone_type = getattr(signal, "zone_type_str", None) or getattr(signal, "zone_type", "?")
        dedup_key = f"{symbol}:{zone_type}:{gate_outcome}"
        now_ts    = datetime.now(timezone.utc).timestamp()
        if gate_outcome != "EXECUTED":
            last_log = self._signal_log_dedup.get(dedup_key, 0.0)
            if now_ts - last_log < 60.0:
                return
        self._signal_log_dedup[dedup_key] = now_ts

        sb = getattr(signal, "zone_score_breakdown", None) or {}

        # ── Sub-razões G2: extrai contribuintes negativos do score_breakdown ──
        g2_sub: list = []
        if isinstance(sb, dict):
            if (sb.get("ofi_slow_penalty") or 0) < 0:
                g2_sub.append(f"ofi_slow_penalty={sb['ofi_slow_penalty']:.2f}")
            if (sb.get("tape_speed_modifier") or 0) < 0:
                g2_sub.append(f"tape_speed={sb['tape_speed_modifier']:.2f}")
            if (sb.get("late_session_penalty") or 0) < 0:
                g2_sub.append(f"late_session={sb['late_session_penalty']:.2f}")
            if (sb.get("gamma_modifier") or 0) < 0:
                g2_sub.append(f"gamma={sb['gamma_modifier']:.2f}")
        # Adiciona ema/gamma block reasons do signal
        g2_sub += [f"ema:{r}" for r in (getattr(signal, "ema_block_reasons", []) or [])]
        g2_sub += [f"gamma:{r}" for r in (getattr(signal, "gamma_block_reasons", []) or [])]

        doc = {
            "ts":                  datetime.now(timezone.utc),
            "symbol":              symbol,
            "session":             session,
            "zone_type":           str(zone_type),
            "zone_level":          getattr(signal, "s3_entry_price", None) or getattr(signal, "last_price", None),
            "zone_score":          sb.get("total_score") if isinstance(sb, dict) else None,
            "zone_score_breakdown": sb if isinstance(sb, dict) else {},
            "s1_regime":           str(getattr(signal, "s1_regime", "")),
            "s1_confidence":       getattr(signal, "s1_confidence", None),
            "s2_quality":          str(getattr(signal, "s2_quality", "")),
            "s2_block_reasons":    getattr(signal, "s2_block_reasons", []),
            "ema_block_reasons":   getattr(signal, "ema_block_reasons", []),
            "gamma_block_reasons": getattr(signal, "gamma_block_reasons", []),
            "g2_sub_reasons":      g2_sub,
            "vwap_zone":           getattr(signal, "vwap_zone", None),
            # ── Fix C: campos de diagnóstico — antes ausentes do signal_log ──────
            # ofi_slow_raw: valor bruto da janela 150s — necessário para validar
            # Fix B (F1-2-EXT threshold −0.30). Sem este campo era impossível
            # saber se ofi_slow estava abaixo/acima do threshold durante o trade.
            "ofi_slow_raw":        getattr(signal, "ofi_slow", None),
            "ofi_fast_raw":        getattr(signal, "ofi_fast", None),
            "cvd_trend":           getattr(signal, "cvd_trend", None),
            # Fix A shadow: SIGMA2_FADE_BUY/SELL que foram bloqueadas neste ciclo RTH_OPEN
            "fix_a_shadow_zones":  getattr(signal, "fix_a_shadow_zones", []),
            # RTH Open: primeiro preço após 09:30 ET — referência de bias intraday
            # Capturado em ScalpEngine._rth_open_prices; None antes da primeira avaliação RTH do dia.
            "rth_open_price":      getattr(signal, "rth_open_price", None),
            # ── Item 5: Bias intraday vs RTH Open ─────────────────────────────
            # "ABOVE"|"BELOW"|"NEUTRAL" — threshold 0.10×ATR.
            "price_vs_rth_open":   getattr(signal, "price_vs_rth_open", "NEUTRAL"),
            # "BULLISH"|"BEARISH"|"NEUTRAL" — regime derivado de price_vs_rth_open.
            "regime_bias":         getattr(signal, "regime_bias", "NEUTRAL"),
            # ── Item 6: CVD Regime Confirmation ──────────────────────────────
            # "CONFIRMED"|"CONTESTED"|"NEUTRAL" — alinhamento CVD com regime EXPANSION.
            "regime_cvd_conf":     getattr(signal, "regime_cvd_conf", "NEUTRAL"),
            # D30 Fase 2 — gate de deslocamento 30 min
            "disp_30m":            getattr(signal, "disp_30m", None),
            "d30_state":           getattr(signal, "d30_state", None),
            "gate_outcome":        gate_outcome,
            "gate_reason":         gate_reason,
            "trade_id":            trade_id,
            "paper":               paper,
        }
        try:
            await self._database["scalp_signal_log"].insert_one(doc)
        except Exception as _e:
            logger.debug("signal_log insert error: %s", _e)


# ── Instância global ──
scalp_auto_trader = ScalpAutoTrader()
