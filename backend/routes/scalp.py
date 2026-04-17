"""
Scalp Routes — Quantum Trading Scalp
======================================
Rotas para o engine de scalp (S1/S2/S3), posições, auto trading e log de trades.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
import asyncio
import uuid
import logging
import urllib.parse
import ipaddress

from services.macro_context_service import MacroContextService
from services.scalp_pnl import pnl_usd as calc_pnl_usd, compute_pnl_pts, DOLLAR_PER_POINT

logger = logging.getLogger("scalp_routes")

scalp_router = APIRouter(prefix="/api/scalp", tags=["scalp"])

_database          = None
_scalp_engine      = None
_live_data_service = None
_scalp_auto_trader = None
_gamma_ratio_svc   = None   # GammaRatioService — ETF→Futures ratio D-1 (opcional)


def set_scalp_deps(database, scalp_engine, live_data_service,
                   auto_trader=None, gamma_ratio_service=None):
    global _database, _scalp_engine, _live_data_service, _scalp_auto_trader, _gamma_ratio_svc
    _database          = database
    _scalp_engine      = scalp_engine
    _live_data_service = live_data_service
    _scalp_auto_trader = auto_trader
    _gamma_ratio_svc   = gamma_ratio_service


# ══════════════════════════════════════════════════════════════════
# Models
# ══════════════════════════════════════════════════════════════════

def _validate_webhook_url(url: str) -> str:
    """Valida que webhook_url é HTTPS e não aponta para redes privadas (SSRF prevention)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("webhook_url deve usar HTTPS.")
    hostname = parsed.hostname or ""
    # Bloqueia loopback, metadados de cloud e redes privadas
    _PRIVATE_PREFIXES = ("localhost", "127.", "0.", "::1", "169.254.")
    if any(hostname.startswith(p) for p in _PRIVATE_PREFIXES):
        raise ValueError(f"webhook_url aponta para endereço reservado: {hostname}")
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"webhook_url aponta para endereço IP privado/reservado: {ip}")
    except ValueError as ve:
        # Re-raise se for o nosso erro; se for só parse error (hostname ≠ IP), ok.
        if "aponta para" in str(ve):
            raise
    return url


class ScalpConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    paper_trading: bool = True
    webhook_url: Optional[str] = Field(None, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def _strip_nones(cls, values):
        """Remove chaves com valor None/NaN ou 0-inválido para que o Pydantic use os defaults.

        O frontend serializa NaN como null (JSON), que chega aqui como None.
        Campos numéricos sem Optional rejeitam None → 422 Unprocessable.
        Campos com ge=1 rejeitam 0 → 422 se o utilizador limpar o input.
        Ao remover as chaves, o Pydantic aplica os field defaults sem validação.
        """
        if isinstance(values, dict):
            # Campos NOT Optional que têm defaults — None é sempre inválido
            numeric_keys = {
                "mnq_quantity", "mes_quantity",
                "sl_ticks_mnq", "tp_ticks_mnq", "be_ticks_mnq",
                "sl_ticks_mes", "tp_ticks_mes", "be_ticks_mes",
                "auto_interval_sec", "cooldown_sec",
                "max_positions", "max_per_symbol", "max_total_contracts",
                "max_daily_loss_usd", "max_consecutive_losses", "max_daily_trades",
                "account_size", "risk_per_trade_pct", "max_daily_loss_pct",
                "atr_stop_multiplier", "atr_target_multiplier",
                "trading_start_hour", "trading_end_hour",
                "avoid_first_minutes", "avoid_last_minutes",
                "pre_close_flatten_minutes", "globex_flatten_before_ny_minutes",
                "news_blackout_minutes_before", "news_blackout_minutes_after",
            }
            # Campos que também rejeitam 0 (ge=1) — strip 0 → usa default
            ge1_keys = {
                "mnq_quantity", "mes_quantity",
                "sl_ticks_mnq", "tp_ticks_mnq",
                "sl_ticks_mes", "tp_ticks_mes",
                "auto_interval_sec",
                "max_positions", "max_per_symbol",
                "max_consecutive_losses", "max_daily_trades",
            }
            def _should_strip(k, v):
                if k in numeric_keys and v is None:
                    return True
                if k in ge1_keys and v == 0:
                    return True
                return False
            return {k: v for k, v in values.items() if not _should_strip(k, v)}
        return values

    @field_validator("webhook_url", mode="before")
    @classmethod
    def validate_webhook_url(cls, v):
        if v is None or v == "":
            return v
        return _validate_webhook_url(str(v))

    # Quantidades — máx 10 contratos por ordem (micro futures)
    mnq_quantity: int = Field(default=1, ge=1, le=10)
    mes_quantity: int = Field(default=1, ge=1, le=10)
    # SL/TP/BE em ticks
    sl_ticks_mnq: int = Field(default=6,  ge=1, le=100)
    tp_ticks_mnq: int = Field(default=10, ge=1, le=200)
    be_ticks_mnq: int = Field(default=4,  ge=0, le=50)
    sl_ticks_mes: int = Field(default=4,  ge=1, le=100)
    tp_ticks_mes: int = Field(default=8,  ge=1, le=200)
    be_ticks_mes: int = Field(default=3,  ge=0, le=50)
    # Auto Trading
    auto_trade: bool = False
    # Gate por modo — permite executar automaticamente só em FLOW, só em ZONES, ou ambos
    auto_trade_flow:  bool = True
    auto_trade_zones: bool = True
    auto_interval_sec: int = Field(default=5,  ge=1,   le=60)
    cooldown_sec:         int = Field(default=60, ge=0,   le=3600)
    max_positions:        int = Field(default=2,  ge=1,   le=5)
    max_per_symbol:       int = Field(default=1,  ge=1,   le=3)
    # G-5: Tecto de contratos simultâneos (partilhado entre símbolos; 0 = desactivado)
    max_total_contracts:  int = Field(default=0,  ge=0,   le=100)
    symbols_enabled: List[str] = ["MNQ", "MES"]
    disabled_zone_types: List[str] = []
    # G-1: Filtro de horário RTH
    rth_only: bool = True
    # G-2: Qualidade mínima para execução (por símbolo, por sessão)
    min_quality_rth_mnq:       str = "STRONG"
    min_quality_rth_mes:       str = "STRONG"
    min_quality_overnight_mnq: str = "MODERATE"
    min_quality_overnight_mes: str = "STRONG"
    # legado — mantido para compatibilidade com configs antigas
    min_quality_to_execute:    str = "STRONG"
    # G-3: Circuit Breaker de sessão
    max_daily_loss_usd:      float = Field(default=200.0, ge=0.0, le=999999.0)
    max_consecutive_losses:  int   = Field(default=3,     ge=1,   le=9999)
    # G-4: Limite de trades por dia
    max_daily_trades: int = Field(default=10, ge=1, le=9999)
    # Conta & Risco
    account_size:        float = Field(default=50000.0, ge=0.0)
    risk_per_trade_pct:  float = Field(default=1.0,     ge=0.1, le=10.0)
    max_daily_loss_pct:  float = Field(default=2.0,     ge=0.1, le=20.0)
    # ATR Stop/Target
    atr_stop_multiplier:   float = Field(default=1.5, ge=0.1, le=10.0)
    atr_target_multiplier: float = Field(default=3.0, ge=0.1, le=20.0)
    # Horário de Trading
    auto_hours_mode:    bool = True
    globex_auto_enabled: bool = False
    trading_start_hour: int = Field(default=9,  ge=0, le=23)
    trading_end_hour:   int = Field(default=16, ge=0, le=23)
    avoid_first_minutes:              int = Field(default=15, ge=0, le=120)
    avoid_last_minutes:               int = Field(default=15, ge=0, le=120)
    pre_close_flatten_minutes:        int = Field(default=30, ge=0, le=120)
    eod_flatten_enabled: bool = True
    globex_flatten_before_ny_minutes: int = Field(default=5,  ge=0, le=120)
    # News Blackout
    news_blackout_enabled:         bool = True
    news_blackout_minutes_before:  int  = Field(default=15, ge=0, le=60)
    news_blackout_minutes_after:   int  = Field(default=15, ge=0, le=60)
    # ── Filtros R1-R4 (análise Apr 14-17, Δ +88 pts esperados) ──────────────
    # R1: bloqueia sinais MODERATE em RTH_MID (WR=16.7%, EV=−53.78 pts)
    r1_moderate_rth_mid_block:      bool = True
    # R2: bloqueia sinais em BEARISH_FLOW durante RTH_MID/RTH_CLOSE (WR=27.3%, EV=−49.81 pts)
    r2_bearish_rth_mid_close_block: bool = True
    # R3: desactiva zona GAMMA_PUT_WALL (performance negativa histórica)
    r3_gamma_put_wall_disabled:     bool = True
    # R4: desactiva zona VWAP_PULLBACK (WR<40% consistente)
    r4_vwap_pullback_disabled:      bool = True


class ScalpTradeClose(BaseModel):
    position_id: str
    reason: str = "manual"


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

async def _build_macro_context(symbol: str, futures_price: float = 0.0) -> Dict[str, Any]:
    """
    Monta o macro_context para o ZONES engine (F5-1 + F5-2).

    Delega inteiramente ao MacroContextService — serviço dedicado e autônomo
    sem dependência do sistema V3.

    Fontes diretas (Yahoo Finance):
      - Term Structure : ^VIX / ^VIX3M ratio (cache 15min RTH)
      - Gamma Levels   : options chain QQQ/SPY → Black-Scholes GEX (cache 60min)
      - ETF→Futures    : ratio D-1 via GammaRatioService ou real-time fallback
    """
    return await MacroContextService.get_context(
        symbol,
        futures_price=futures_price,
        gamma_ratio_svc=_gamma_ratio_svc,
    )


def _get_collection(name: str):
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    return _database[name]


async def _get_scalp_config() -> Dict[str, Any]:
    col = _get_collection("scalp_config")
    doc = await col.find_one({"id": "default"}, {"_id": 0})
    if not doc:
        default = {
            "id": "default",
            "enabled": True,
            "paper_trading": True,
            "webhook_url": None,
            "mnq_quantity": 1,
            "mes_quantity": 1,
            "sl_ticks_mnq": 6, "tp_ticks_mnq": 10, "be_ticks_mnq": 4,
            "sl_ticks_mes": 4, "tp_ticks_mes": 8,  "be_ticks_mes": 3,
            "auto_trade": False,
            "auto_trade_flow": True,
            "auto_trade_zones": True,
            "auto_interval_sec": 5,
            "cooldown_sec": 60,
            "max_positions": 2,
            "max_per_symbol": 1,
            "max_total_contracts": 0,
            "symbols_enabled": ["MNQ", "MES"],
            "mode": "FLOW",
            # Conta & Risco
            "account_size": 50000.0,
            "risk_per_trade_pct": 1.0,
            "max_daily_loss_pct": 2.0,
            # ATR
            "atr_stop_multiplier": 1.5,
            "atr_target_multiplier": 3.0,
            # Horário
            "auto_hours_mode": True,
            "globex_auto_enabled": False,
            "trading_start_hour": 9,
            "trading_end_hour": 16,
            "avoid_first_minutes": 15,
            "avoid_last_minutes": 15,
            "pre_close_flatten_minutes": 30,
            "eod_flatten_enabled": True,
            "globex_flatten_before_ny_minutes": 5,
            # News Blackout
            "news_blackout_enabled": True,
            "news_blackout_minutes_before": 15,
            "news_blackout_minutes_after": 15,
            # Guardas
            "rth_only": True,
            "min_quality_rth_mnq": "STRONG",
            "min_quality_rth_mes": "STRONG",
            "min_quality_overnight_mnq": "MODERATE",
            "min_quality_overnight_mes": "STRONG",
            "min_quality_to_execute": "STRONG",
            "max_daily_loss_usd": 200.0,
            "max_consecutive_losses": 3,
            "max_daily_trades": 10,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await col.insert_one({**default})
        return default
    return doc


async def _send_signalstack_order(webhook_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    # Validação de segurança no ponto de execução — defesa em profundidade
    # (URLs salvas antes da validação Pydantic também são protegidas aqui)
    try:
        _validate_webhook_url(webhook_url)
    except ValueError as _ve:
        raise HTTPException(status_code=400, detail=f"webhook_url inválida: {_ve}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(webhook_url, json=payload)
        return {
            "status_code": response.status_code,
            "response": response.text[:500],
            "ok": response.status_code < 300,
        }


def _get_tradovate_symbol(base: str) -> str:
    """
    Retorna o símbolo Tradovate do contrato front-month trimestral de MNQ/MES.
    MNQ/MES só têm contratos em H(Mar), M(Jun), U(Set), Z(Dez).
    Faz rollover ~10 dias antes da expiração (3ª sexta do mês de expiração).
    """
    now = datetime.now()
    m = now.month
    y = now.year
    # Determina o front-month trimestral correto
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


def _parse_dt(value) -> datetime:
    """Aceita created_at tanto como string ISO quanto como datetime nativo do MongoDB."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


def _get_live_price(symbol: str) -> Optional[float]:
    if _live_data_service is None:
        return None
    try:
        if hasattr(_live_data_service, 'buffers') and symbol in _live_data_service.buffers:
            return _live_data_service.buffers[symbol].last_price
        if hasattr(_live_data_service, 'get_live_data'):
            d = _live_data_service.get_live_data(symbol)
            return d.get("last_price") if d else None
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════
# MODO
# ══════════════════════════════════════════════════════════════════

@scalp_router.get("/mode")
async def get_scalp_mode():
    if _scalp_engine is None:
        return {"mode": "FLOW"}
    return {"mode": _scalp_engine.get_mode()}


@scalp_router.post("/mode/{mode}")
async def set_scalp_mode(mode: str):
    mode = mode.upper()
    if mode not in ("FLOW", "ZONES"):
        raise HTTPException(status_code=400, detail="Modo inválido. Use FLOW ou ZONES.")
    if _scalp_engine is None:
        raise HTTPException(status_code=503, detail="ScalpEngine não inicializado")
    _scalp_engine.set_mode(mode)
    col = _get_collection("scalp_config")
    await col.update_one({"id": "default"}, {"$set": {"mode": mode}}, upsert=True)
    return {"mode": mode, "message": f"Modo alterado para {mode}"}


# ══════════════════════════════════════════════════════════════════
# SINAIS
# ══════════════════════════════════════════════════════════════════

@scalp_router.get("/signal/{symbol}")
async def get_scalp_signal(
    symbol: str,
    quantity: int = Query(default=1, ge=1, le=10),
    mode: Optional[str] = Query(default=None),
):
    symbol = symbol.upper()
    if symbol not in ("MNQ", "MES"):
        raise HTTPException(status_code=400, detail="Símbolo inválido. Use MNQ ou MES.")
    if _scalp_engine is None:
        raise HTTPException(status_code=503, detail="ScalpEngine não inicializado")
    try:
        signal = await _scalp_engine.evaluate(symbol, quantity, mode=mode)
        return signal.to_dict()
    except Exception as e:
        logger.error(f"Erro ao avaliar sinal scalp para {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@scalp_router.get("/signal/{symbol}/cached")
async def get_scalp_signal_cached(symbol: str):
    symbol = symbol.upper()
    if _scalp_engine is None:
        raise HTTPException(status_code=503, detail="ScalpEngine não inicializado")
    sig = _scalp_engine.get_last_signal(symbol)
    if sig is None:
        return {"symbol": symbol, "scalp_status": "NO_DATA", "message": "Nenhum sinal avaliado ainda"}
    return sig.to_dict()


# ══════════════════════════════════════════════════════════════════
# EXECUÇÃO MANUAL
# ══════════════════════════════════════════════════════════════════

@scalp_router.post("/execute/{symbol}")
async def execute_scalp_trade(
    symbol: str,
    force: bool = Query(default=False),
):
    symbol = symbol.upper()
    if symbol not in ("MNQ", "MES"):
        raise HTTPException(status_code=400, detail="Símbolo inválido. Use MNQ ou MES.")
    if _scalp_engine is None:
        raise HTTPException(status_code=503, detail="ScalpEngine não inicializado")

    # Proteção contra execução duplicada (double-click / network retry):
    # não abre nova posição se já há uma OPEN para este símbolo,
    # a menos que force=true seja explicitamente enviado.
    if not force and _database is not None:
        existing_open = await _database["scalp_trades"].find_one(
            {"symbol": symbol, "state": "OPEN"},
            {"id": 1}
        )
        if existing_open:
            return {
                "status": "blocked",
                "message": f"Posição já aberta para {symbol} (id: {existing_open['id']}). Use force=true para forçar nova entrada.",
                "trade_id": existing_open["id"],
            }

    config = await _get_scalp_config()
    qty    = config.get(f"{symbol.lower()}_quantity", 1)

    signal = await _scalp_engine.evaluate(symbol, qty)
    sig_dict = signal.to_dict()

    if not force and signal.scalp_status != "ACTIVE_SIGNAL":
        return {
            "status": "no_signal",
            "scalp_status": signal.scalp_status,
            "message": f"Sinal não ativo ({signal.scalp_status}). Use force=true para forçar.",
            "signal": sig_dict,
        }
    if signal.s3_action is None or signal.last_price <= 0:
        return {"status": "error", "message": "S3 sem parâmetros válidos", "signal": sig_dict}

    from services.scalp_engine import SCALP_PARAMS, TICK_SIZE
    params   = SCALP_PARAMS.get(symbol, SCALP_PARAMS['MNQ'])
    sl_ticks = config.get(f"sl_ticks_{symbol.lower()}", params['sl_ticks'])
    tp_ticks = config.get(f"tp_ticks_{symbol.lower()}", params['tp_ticks'])
    be_ticks = config.get(f"be_ticks_{symbol.lower()}", params['be_ticks'])
    sl_pts   = sl_ticks * TICK_SIZE
    tp_pts   = tp_ticks * TICK_SIZE
    be_pts   = be_ticks * TICK_SIZE

    price  = signal.last_price
    action = signal.s3_action

    if action.lower() == "buy":
        sl_price = round(price - sl_pts, 2)
        tp_price = round(price + tp_pts, 2)
    else:
        sl_price = round(price + sl_pts, 2)
        tp_price = round(price - tp_pts, 2)

    tradovate_symbol = _get_tradovate_symbol(symbol)
    paper       = config.get("paper_trading", True)
    webhook_url = config.get("webhook_url")

    # ── Formato PickMyTrade (template completo) ──
    sl_pts_rel = round(abs(price - sl_price), 2)
    tp_pts_rel = round(abs(tp_price - price), 2)
    TICK        = 0.25
    pmt_token   = config.get("pmt_token", "")
    pmt_acct    = config.get("pmt_account_id", "")
    trail_on    = 1 if (be_pts and be_pts > 0) else 0
    # PMT: dollar_sl/dollar_tp = offset em PONTOS a partir do fill price
    # (apesar do nome, o campo é em pontos, não em dólares — confirmado em teste)
    dollar_sl_val = sl_pts_rel
    dollar_tp_val = tp_pts_rel
    payload = {
        "symbol":                f"{symbol.upper()}1!",  # MNQ→MNQ1! / MES→MES1!
        "strategy_name":         "QuantumScalp",
        "date":                  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data":                  action,
        "quantity":              signal.s3_quantity,
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

    trade_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    from services.trading_calendar_service import get_session_label
    trade_doc = {
        "id": trade_id,
        "symbol": symbol,
        "tradovate_symbol": tradovate_symbol,
        "action": action,
        "quantity": signal.s3_quantity,
        "entry_price": price,
        "stop_loss_price": sl_price,
        "take_profit_price": tp_price,
        "breakeven_pts": be_pts,
        "paper": paper,
        "mode": signal.mode,
        "source": "manual",
        "s1_regime": str(signal.s1_regime),
        "s1_confidence": signal.s1_confidence,
        "s2_quality": str(signal.s2_quality),
        "s2_risk_modifier": signal.s2_risk_modifier,
        "ofi_fast": signal.ofi_fast,
        "ofi_slow": signal.ofi_slow,
        "absorption_flag": signal.absorption_flag,
        "absorption_side": signal.absorption_side,
        "cvd": signal.cvd,
        "cvd_trend": signal.cvd_trend,
        "atr_m1": signal.atr_m1,
        "session_label":   get_session_label(),
        "session_minutes": signal.session_minutes,
        "zone_type": (
            signal.zone_type_str
            or (signal.active_zone.get("type") if signal.active_zone else None)
        ),
        "score_breakdown": signal.zone_score_breakdown,
        "state": "OPEN",
        "exit_price": None,
        "exit_reason": None,
        "pnl_pts": None,
        "pnl_usd": None,
        "duration_sec": None,
        "created_at": now.isoformat(),
        "closed_at": None,
        "events": [{"ts": now.isoformat(), "event": "OPEN", "price": price, "source": "manual"}],
        "signalstack_response": None,
        "signalstack_ok": None,
    }

    webhook_result = None
    if not paper and webhook_url:
        try:
            webhook_result = await _send_signalstack_order(webhook_url, payload)
            trade_doc["signalstack_response"] = webhook_result
            trade_doc["signalstack_ok"] = webhook_result.get("ok")
        except Exception as e:
            logger.error(f"Erro SignalStack: {e}")
            trade_doc["signalstack_response"] = {"error": str(e)}
            trade_doc["signalstack_ok"] = False

    await _get_collection("scalp_trades").insert_one({**trade_doc, "_id": trade_id})
    trade_doc.pop("_id", None)

    return {
        "status": "paper_executed" if paper else "executed",
        "trade_id": trade_id,
        "paper": paper,
        "payload": payload,
        "webhook_result": webhook_result,
        "signal": sig_dict,
        "trade": trade_doc,
    }


# ══════════════════════════════════════════════════════════════════
# FECHAMENTO MANUAL
# ══════════════════════════════════════════════════════════════════

@scalp_router.post("/close/{position_id}")
@scalp_router.patch("/close/{position_id}")
async def close_scalp_position(position_id: str, body: ScalpTradeClose = None):
    reason = body.reason if body else "manual"
    now    = datetime.now(timezone.utc)

    # Tenta fechar via AutoTrader (se estiver em memória)
    if _scalp_auto_trader is not None:
        live_price = _get_live_price("MNQ") or _get_live_price("MES") or 0.0
        # Busca símbolo do trade
        trade_col = _get_collection("scalp_trades")
        doc = await trade_col.find_one({"id": position_id, "state": "OPEN"}, {"symbol": 1})
        if doc:
            sym_price = _get_live_price(doc.get("symbol", "MNQ")) or 0.0
            if sym_price > 0:
                closed = await _scalp_auto_trader.close_trade_manual(position_id, sym_price)
                if closed:
                    return {"status": "closed", "position_id": position_id, "reason": reason, "source": "auto_trader"}

    # Fechamento direto no MongoDB
    col = _get_collection("scalp_trades")
    doc = await col.find_one({"id": position_id})
    if not doc or doc.get("state") == "CLOSED":
        raise HTTPException(status_code=404, detail="Trade não encontrado ou já fechado")
    config      = await _get_scalp_config()
    paper       = doc.get("paper", True)
    webhook_url = config.get("webhook_url")
    webhook_result = None

    if not paper and webhook_url:
        sym          = doc.get("symbol", "MNQ")
        pmt_sym      = f"{sym.upper()}1!"
        pmt_token    = config.get("pmt_token", "")
        pmt_acct     = config.get("pmt_account_id", "")
        try:
            from datetime import datetime as _dt_close, timezone as _tz_close
            # PMT recomenda data:"close" para fechar posição individual —
            # cancela brackets SL/TP automaticamente sem risco de reverse
            webhook_result = await _send_signalstack_order(webhook_url, {
                "symbol":                pmt_sym,
                "strategy_name":         "QuantumScalp",
                "date":                  _dt_close.now(_tz_close.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data":                  "close",
                "quantity":              doc.get("quantity", 1),
                "price":                 0,
                "tp":                    0,
                "sl":                    0,
                "trail":                 0,
                "token":                 pmt_token,
                "account_id":            pmt_acct,
                "pyramid":               False,
                "same_direction_ignore": True,
                "reverse_order_close":   False,
                "multiple_accounts": [{
                    "token": pmt_token, "account_id": pmt_acct,
                    "risk_percentage": 0, "quantity_multiplier": 1,
                }],
            })
        except Exception as e:
            logger.error(f"Erro PickMyTrade ao fechar: {e}")

    sym        = doc.get("symbol", "MNQ")
    live_price = _get_live_price(sym) or doc.get("entry_price", 0.0)
    action     = doc.get("action", "buy")
    entry      = doc.get("entry_price", 0.0)
    pnl_pts    = (live_price - entry) if action.lower() == "buy" else (entry - live_price)
    pnl_pts    = round(pnl_pts, 4)
    qty        = doc.get("quantity", 1)
    pnl_usd    = calc_pnl_usd(sym, pnl_pts, qty)
    created    = _parse_dt(doc["created_at"])
    duration   = round((now - created).total_seconds(), 1)

    await col.update_one({"id": position_id}, {
        "$set": {
            "state": "CLOSED",
            "closed_at": now.isoformat(),
            "close_reason": reason,
            "exit_price": live_price,
            "exit_reason": "MANUAL_CLOSE",
            "pnl_pts": pnl_pts,
            "pnl_usd": pnl_usd,
            "duration_sec": duration,
        },
        "$push": {"events": {"ts": now.isoformat(), "event": "CLOSED", "reason": reason, "pnl_usd": pnl_usd}},
    })

    return {"status": "closed", "position_id": position_id, "reason": reason}


# ══════════════════════════════════════════════════════════════════
@scalp_router.get("/positions")
async def list_scalp_positions(
    state: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
):
    col   = _get_collection("scalp_trades")
    query = {}
    if state:  query["state"] = state.upper()
    if symbol: query["symbol"] = symbol.upper()
    cursor    = col.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    positions = await cursor.to_list(length=limit)
    open_count = await col.count_documents({"state": "OPEN"})
    total      = await col.count_documents(query)
    return {"positions": positions, "total": total, "open_count": open_count, "skip": skip, "limit": limit}


@scalp_router.get("/positions/stats")
async def scalp_position_stats():
    col        = _get_collection("scalp_trades")
    closed     = await col.find({"state": "CLOSED"}, {"_id": 0}).to_list(length=1000)
    open_pos   = await col.count_documents({"state": "OPEN"})
    total_closed = len(closed)
    if total_closed == 0:
        return {"total_trades": 0, "open_positions": open_pos, "win_rate": None, "avg_pnl_pts": None, "by_symbol": {}, "by_regime": {}}
    wins = 0
    pnl_list = []
    by_symbol: Dict[str, Dict] = {}
    by_regime: Dict[str, Dict] = {}
    for pos in closed:
        outcome = pos.get("outcome")
        pnl     = pos.get("pnl_pts")
        sym     = pos.get("symbol", "?")
        regime  = pos.get("s1_regime", "?")
        if outcome == "WIN" or (pnl is not None and pnl > 0): wins += 1
        if pnl is not None: pnl_list.append(pnl)
        by_symbol.setdefault(sym,    {"total": 0, "wins": 0})["total"] += 1
        by_regime.setdefault(regime, {"total": 0, "wins": 0})["total"] += 1
    return {
        "total_trades": total_closed,
        "open_positions": open_pos,
        "win_rate": round(wins / total_closed * 100, 1) if total_closed > 0 else None,
        "avg_pnl_pts": round(sum(pnl_list) / len(pnl_list), 2) if pnl_list else None,
        "by_symbol": by_symbol,
        "by_regime": by_regime,
    }


# ══════════════════════════════════════════════════════════════════
# AUTO TRADER
# ══════════════════════════════════════════════════════════════════

@scalp_router.get("/autotrader/status")
async def autotrader_status():
    if _scalp_auto_trader is None:
        return {"status": "STOPPED", "message": "AutoTrader não inicializado"}
    return _scalp_auto_trader.get_status()


@scalp_router.post("/autotrader/start")
async def autotrader_start():
    if _scalp_auto_trader is None:
        raise HTTPException(status_code=503, detail="AutoTrader não inicializado")
    await _scalp_auto_trader.start()
    return {"status": "RUNNING", "message": "Auto Trading iniciado"}


@scalp_router.post("/autotrader/stop")
async def autotrader_stop():
    if _scalp_auto_trader is None:
        raise HTTPException(status_code=503, detail="AutoTrader não inicializado")
    await _scalp_auto_trader.stop()
    return {"status": "STOPPED", "message": "Auto Trading parado"}


@scalp_router.post("/autotrader/reset_circuit_breaker")
async def autotrader_reset_circuit_breaker():
    """
    G-3/G-4: Reseta manualmente o circuit breaker de sessão.
    Limpa os contadores de perdas consecutivas, PnL diário e trades diários.
    O loop retoma na próxima iteração se auto_trade=True e dentro de RTH.
    """
    if _scalp_auto_trader is None:
        raise HTTPException(status_code=503, detail="AutoTrader não inicializado")
    _scalp_auto_trader.reset_circuit_breaker()
    return {"ok": True, "message": "Circuit breaker resetado — contadores diários zerados"}


# ══════════════════════════════════════════════════════════════════
# LOG DE TRADES (scalp_trades — unificado manual + auto)
# ══════════════════════════════════════════════════════════════════

@scalp_router.get("/trades")
async def list_scalp_trades(
    state:      Optional[str]  = Query(default=None),
    symbol:     Optional[str]  = Query(default=None),
    source:     Optional[str]  = Query(default=None),   # "manual" | "auto"
    paper:      Optional[bool] = Query(default=None),
    session:    Optional[str]  = Query(default=None),   # OVERNIGHT | RTH_OPEN | RTH_MID | RTH_CLOSE | RTH_ALL | NY | GLOBEX
    date_from:  Optional[str]  = Query(default=None),   # YYYY-MM-DD (inclusive)
    date_to:    Optional[str]  = Query(default=None),   # YYYY-MM-DD (inclusive, fim do dia)
    mode:       Optional[str]  = Query(default=None),   # ZONES | FLOW | CANDLE
    limit:      int            = Query(default=200, ge=1, le=1000),
    skip:       int            = Query(default=0, ge=0),
):
    """Log completo de trades de scalp (manual + auto) com PnL em USD.
    Filtros: state, symbol, source, paper, session, date_from, date_to, mode.
    """
    from datetime import datetime, timezone
    col   = _get_collection("scalp_trades")
    query: Dict[str, Any] = {}
    if state:              query["state"]         = state.upper()
    if symbol:             query["symbol"]        = symbol.upper()
    if source:             query["source"]        = source.lower()
    if paper is not None:  query["paper"]         = paper
    if session:
        _sess = session.upper()
        _RTH_LABELS   = ["RTH_OPEN", "RTH_MID", "RTH_CLOSE"]
        _GLOBEX_LABELS = ["OVERNIGHT", "HALTED"]
        if _sess in ("RTH_ALL", "NY"):
            query["session_label"] = {"$in": _RTH_LABELS}
        elif _sess == "GLOBEX":
            query["session_label"] = {"$in": _GLOBEX_LABELS}
        else:
            query["session_label"] = _sess
    if mode:               query["mode"]          = mode.upper()
    if date_from or date_to:
        dt_filter: Dict[str, Any] = {}
        if date_from:
            try:
                dt_filter["$gte"] = datetime.fromisoformat(date_from).replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                pass
        if date_to:
            try:
                dt_filter["$lte"] = datetime.fromisoformat(date_to).replace(
                    hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                pass
        if dt_filter:
            query["created_at"] = dt_filter

    cursor = col.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    trades = await cursor.to_list(length=limit)
    total  = await col.count_documents(query)
    open_count = await col.count_documents({"state": "OPEN"})

    return {
        "trades":     trades,
        "total":      total,
        "open_count": open_count,
        "skip":       skip,
        "limit":      limit,
        "filters":    {
            "state": state, "symbol": symbol, "source": source,
            "paper": paper, "session": session, "mode": mode,
            "date_from": date_from, "date_to": date_to,
        },
    }


@scalp_router.get("/trades/stats")
async def scalp_trades_stats(
    mode:      Optional[str]  = Query(default=None),
    symbol:    Optional[str]  = Query(default=None),
    session:   Optional[str]  = Query(default=None),
    date_from: Optional[str]  = Query(default=None),
    date_to:   Optional[str]  = Query(default=None),
    paper:     Optional[bool] = Query(default=None),
):
    """Estatísticas de performance do log de trades com PnL em USD.
    Aceita os mesmos filtros que /trades para sincronizar com o journal."""
    from datetime import datetime, timezone
    col = _get_collection("scalp_trades")

    # Filtro idêntico ao endpoint /trades
    query: Dict[str, Any] = {}
    if mode:   query["mode"]   = mode.upper()
    if symbol: query["symbol"] = symbol.upper()
    if session:
        _sess = session.upper()
        _RTH_LABELS    = ["RTH_OPEN", "RTH_MID", "RTH_CLOSE"]
        _GLOBEX_LABELS = ["OVERNIGHT", "HALTED"]
        if _sess in ("RTH_ALL", "NY"):
            query["session_label"] = {"$in": _RTH_LABELS}
        elif _sess == "GLOBEX":
            query["session_label"] = {"$in": _GLOBEX_LABELS}
        else:
            query["session_label"] = _sess
    if paper is not None: query["paper"] = paper
    if date_from or date_to:
        dt_filter: Dict[str, Any] = {}
        if date_from:
            try:
                dt_filter["$gte"] = datetime.fromisoformat(date_from).replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                pass
        if date_to:
            try:
                dt_filter["$lte"] = datetime.fromisoformat(date_to).replace(
                    hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                pass
        if dt_filter:
            query["created_at"] = dt_filter

    closed = await col.find({**query, "state": "CLOSED"}, {"_id": 0}).to_list(length=2000)
    open_n = await col.count_documents({**query, "state": "OPEN"})
    total  = await col.count_documents(query)

    if not closed:
        return {
            "total_trades": total, "closed_trades": 0, "open_trades": open_n,
            "wins": 0, "losses": 0, "win_rate": None,
            "total_pnl_pts": 0.0, "total_pnl_usd": 0.0,
            "avg_pnl_pts": None, "avg_pnl_usd": None,
            "avg_duration_sec": None,
            "paper_trades": 0, "live_trades": 0,
            "auto_trades": 0, "manual_trades": 0,
            "by_symbol": {}, "by_regime": {}, "by_mode": {},
        }

    wins = losses = 0
    pnl_pts_list = []
    pnl_usd_list = []
    dur_list = []
    paper_n = live_n = auto_n = manual_n = 0
    by_sym: Dict[str, Any] = {}
    by_reg: Dict[str, Any] = {}
    by_mode: Dict[str, Any] = {}

    for t in closed:
        pnl_pts = t.get("pnl_pts")
        pnl_usd = t.get("pnl_usd")
        dur     = t.get("duration_sec")
        sym     = t.get("symbol", "?")
        regime  = t.get("s1_regime", "?")
        mode    = t.get("mode", "FLOW")
        is_paper= t.get("paper", True)
        source  = t.get("source", "manual")

        if pnl_pts is not None:
            if pnl_pts > 0: wins += 1
            else:           losses += 1
            pnl_pts_list.append(pnl_pts)
        if pnl_usd is not None:
            pnl_usd_list.append(pnl_usd)
        if dur is not None:
            dur_list.append(dur)

        if is_paper: paper_n += 1
        else:        live_n  += 1
        if source == "auto":   auto_n   += 1
        else:                  manual_n += 1

        # Por símbolo
        s = by_sym.setdefault(sym, {"total": 0, "wins": 0, "pnl_usd": 0.0})
        s["total"] += 1
        if pnl_pts is not None and pnl_pts > 0: s["wins"] += 1
        if pnl_usd is not None: s["pnl_usd"] = round(s["pnl_usd"] + pnl_usd, 2)

        # Por regime
        r = by_reg.setdefault(regime, {"total": 0, "wins": 0})
        r["total"] += 1
        if pnl_pts is not None and pnl_pts > 0: r["wins"] += 1

        # Por modo
        m = by_mode.setdefault(mode, {"total": 0, "wins": 0, "pnl_usd": 0.0})
        m["total"] += 1
        if pnl_pts is not None and pnl_pts > 0: m["wins"] += 1
        if pnl_usd is not None: m["pnl_usd"] = round(m["pnl_usd"] + pnl_usd, 2)

    n = len(closed)
    return {
        "total_trades": total,
        "closed_trades": n,
        "open_trades": open_n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / n * 100, 1) if n > 0 else None,
        "total_pnl_pts": round(sum(pnl_pts_list), 4),
        "total_pnl_usd": round(sum(pnl_usd_list), 2),
        "avg_pnl_pts":  round(sum(pnl_pts_list) / len(pnl_pts_list), 4) if pnl_pts_list else None,
        "avg_pnl_usd":  round(sum(pnl_usd_list) / len(pnl_usd_list), 2) if pnl_usd_list else None,
        "avg_duration_sec": round(sum(dur_list) / len(dur_list), 1) if dur_list else None,
        "paper_trades": paper_n,
        "live_trades": live_n,
        "auto_trades": auto_n,
        "manual_trades": manual_n,
        "by_symbol": by_sym,
        "by_regime": by_reg,
        "by_mode": by_mode,
    }


@scalp_router.post("/trades/{trade_id}/close")
async def close_scalp_trade(
    trade_id:   str,
    reason:     str            = Query(default="manual"),
    exit_price: Optional[float] = Query(default=None, description="Preço de saída explícito; usa live price se omitido"),
):
    """Fecha um trade do log de scalp_trades."""
    col = _get_collection("scalp_trades")
    doc = await col.find_one({"id": trade_id, "state": "OPEN"})
    if not doc:
        raise HTTPException(status_code=404, detail="Trade não encontrado ou já fechado")

    sym  = doc.get("symbol", "MNQ")
    price = exit_price or _get_live_price(sym) or doc.get("entry_price", 0.0)

    if _scalp_auto_trader and trade_id in _scalp_auto_trader._open_trades:
        await _scalp_auto_trader.close_trade_manual(trade_id, price)
    else:
        now     = datetime.now(timezone.utc)
        action  = doc.get("action", "buy")
        entry   = doc.get("entry_price", 0.0)
        qty     = doc.get("quantity", 1)
        pnl_pts = round((price - entry) if action.lower() == "buy" else (entry - price), 4)
        pnl_usd = calc_pnl_usd(sym, pnl_pts, qty)
        created  = _parse_dt(doc["created_at"])
        duration = round((now - created).total_seconds(), 1)
        await col.update_one({"id": trade_id}, {"$set": {
            "state": "CLOSED", "exit_price": price, "exit_reason": reason,
            "pnl_pts": pnl_pts, "pnl_usd": pnl_usd, "duration_sec": duration,
            "closed_at": now.isoformat(),
        }})

    return {"status": "closed", "trade_id": trade_id}


@scalp_router.post("/trades/flatten_all")
async def flatten_all_scalp_trades(reason: str = Query(default="manual")):
    """Fecha todas as posições scalp abertas (paper e live)."""
    closed_count = 0
    if _scalp_auto_trader is not None:
        closed_count = await _scalp_auto_trader.flatten_all_trades(reason=reason)

    trades_col = _get_collection("scalp_trades")
    remaining  = await trades_col.find({"state": "OPEN"}).to_list(length=200)
    for doc in remaining:
        trade_id = doc.get("id")
        sym      = doc.get("symbol", "MNQ")
        live_price = _get_live_price(sym) or doc.get("entry_price", 0.0)
        action   = doc.get("action", "buy")
        entry    = doc.get("entry_price", 0.0)
        qty      = doc.get("quantity", 1)
        pnl_pts  = round((live_price - entry) if action.lower() == "buy" else (entry - live_price), 4)
        pnl_usd  = calc_pnl_usd(sym, pnl_pts, qty)
        now      = datetime.now(timezone.utc)
        created  = _parse_dt(doc["created_at"])
        duration = round((now - created).total_seconds(), 1)
        close_update = {
            "state": "CLOSED", "exit_price": live_price, "exit_reason": reason,
            "pnl_pts": pnl_pts, "pnl_usd": pnl_usd, "duration_sec": duration,
            "closed_at": now.isoformat(),
        }
        await trades_col.update_one({"id": trade_id}, {"$set": close_update})
        closed_count += 1

    return {"status": "ok", "closed": closed_count, "reason": reason}


# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

@scalp_router.get("/config")
async def get_scalp_config_route():
    config = await _get_scalp_config()
    return {"config": config}


class PaperModeUpdate(BaseModel):
    paper_trading: bool
    webhook_url: Optional[str] = Field(None, max_length=500)

    @field_validator("webhook_url", mode="before")
    @classmethod
    def validate_webhook(cls, v):
        if v is None or v == "":
            return v
        return _validate_webhook_url(str(v))


@scalp_router.patch("/config/paper-mode")
async def set_paper_mode(body: PaperModeUpdate):
    """Endpoint mínimo para alternar Paper/LIVE — evita reenvio do config completo."""
    col = _get_collection("scalp_config")
    update: dict = {
        "paper_trading": body.paper_trading,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if body.webhook_url is not None:
        update["webhook_url"] = body.webhook_url
    await col.update_one({"id": "default"}, {"$set": update}, upsert=True)
    if _scalp_auto_trader is not None:
        _scalp_auto_trader.invalidate_config_cache()
    config = await _get_scalp_config()
    return {"status": "saved", "paper_trading": body.paper_trading, "config": config}


@scalp_router.post("/config/save")
@scalp_router.patch("/config/save")
async def save_scalp_config(config: ScalpConfigUpdate):
    col  = _get_collection("scalp_config")
    now  = datetime.now(timezone.utc).isoformat()
    data = config.model_dump()
    data["updated_at"] = now

    if _scalp_engine is not None:
        from services.scalp_engine import SCALP_PARAMS
        SCALP_PARAMS['MNQ']['sl_ticks'] = config.sl_ticks_mnq
        SCALP_PARAMS['MNQ']['tp_ticks'] = config.tp_ticks_mnq
        SCALP_PARAMS['MNQ']['be_ticks'] = config.be_ticks_mnq
        SCALP_PARAMS['MES']['sl_ticks'] = config.sl_ticks_mes
        SCALP_PARAMS['MES']['tp_ticks'] = config.tp_ticks_mes
        SCALP_PARAMS['MES']['be_ticks'] = config.be_ticks_mes

        # Aplica parâmetros zones per-símbolo ao session_params do engine live
        # zones_mnq.score_strong_thresh → zones_score_strong_thresh_mnq (e vice-versa)
        try:
            from services.scalp_zones import update_session_params as _update_sp
            _ZONES_LIVE_KEYS = [
                "score_strong_thresh", "score_moderate_thresh",
                "ofi_slow_fade_thresh", "ofi_slow_momentum_thresh",
            ]
            _sym_sp: Dict = {}
            for _sym in ("mnq", "mes"):
                _overrides: Dict = data.get(f"zones_{_sym}") or {}
                for _k in _ZONES_LIVE_KEYS:
                    if _k in _overrides:
                        _sym_sp[f"zones_{_k}_{_sym}"] = _overrides[_k]
            if _sym_sp:
                for _sg in ("NY", "GLOBEX"):
                    _update_sp(_sg, _sym_sp)
        except Exception as _e:
            import logging; logging.getLogger(__name__).warning(
                "Zones per-symbol session_params apply error: %s", _e)

    # Invalida cache do AutoTrader para que o loop use a nova config na próxima iteração
    if _scalp_auto_trader is not None:
        _scalp_auto_trader.invalidate_config_cache()
        # Sincroniza estado do loop com o campo auto_trade
        if config.auto_trade and _scalp_auto_trader._status != "RUNNING":
            await _scalp_auto_trader.start()
        elif not config.auto_trade and _scalp_auto_trader._status == "RUNNING":
            await _scalp_auto_trader.stop()

    await col.update_one({"id": "default"}, {"$set": data}, upsert=True)
    return {"status": "saved", "config": data}


# ══════════════════════════════════════════════════════════════════
# LIVE DATA
# ══════════════════════════════════════════════════════════════════

@scalp_router.get("/live/{symbol}")
async def get_scalp_live_data(symbol: str):
    symbol = symbol.upper()
    if _live_data_service is None:
        raise HTTPException(status_code=503, detail="LiveDataService não inicializado")
    try:
        if hasattr(_live_data_service, 'get_live_data'):
            data = _live_data_service.get_live_data(symbol)
        elif hasattr(_live_data_service, 'buffers') and symbol in _live_data_service.buffers:
            data = _live_data_service.buffers[symbol].get_data()
        else:
            data = {}
        return {"symbol": symbol, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════
# MARKET LEVELS (VWAP bands, VP, ONH/ONL)
# ══════════════════════════════════════════════════════════════════

@scalp_router.get("/zones/{symbol}")
async def get_scalp_zones(symbol: str, quantity: int = Query(default=1, ge=1, le=10)):
    """
    Retorna o regime de mercado do dia e as zonas de interesse ativas para o símbolo.
    Detecta automaticamente EXPANSION_BULL/BEAR, ROTATION ou BREAKOUT.
    Para cada zona: nível, direção, distância atual, se o preço está dentro, e parâmetros de risco.

    HF1+HF2: usa _last_zones_cache do engine quando fresco (< 3s) — evita recomputação dupla.
    """
    import time as _time
    symbol = symbol.upper()
    if _scalp_engine is None:
        raise HTTPException(status_code=503, detail="ScalpEngine não inicializado")
    try:
        from services.scalp_zones import (
            evaluate_zones, ScalpDayRegime, REGIME_RISK_PARAMS,
        )
        from services.scalp_engine import ATR_MIN_FALLBACK, build_m1_bars, evaluate_s1_candle, MIN_BARS_FOR_ATR, _atr_cache, ATR_CACHE_TTL

        # ── F5: Busca macro_context (TS + Gamma Levels) — sempre fresco ─────
        live_for_price = _scalp_engine._get_live_data(symbol) or {}
        cur_price      = live_for_price.get("last_price", 0.0) or 0.0
        macro_ctx = await _build_macro_context(symbol, futures_price=cur_price)

        # ── HF2: Usa cache do engine se fresco (< 3s) ────────────────────────
        # Nota: com macro_context é sempre recomputado para garantir TS gate actualizado
        cached = getattr(_scalp_engine, '_last_zones_cache', {}).get(symbol)
        if cached and (_time.monotonic() - cached['ts']) < 3.0 and not macro_ctx.get('ts_hard_stop'):
            result = cached['result']
            atr    = cached['atr']
            live   = _scalp_engine._get_live_data(symbol) or {}
            price  = live.get("last_price", 0.0) or 0.0
            levels = {}
        else:
            levels = await _scalp_engine._get_levels(symbol) or {}
            live   = _scalp_engine._get_live_data(symbol) or {}
            price  = live.get("last_price", 0.0) or 0.0

            # ── HF1: ATR do M1 com cache de 60s ──────────────────────────────
            atr_cached = _atr_cache.get(symbol)
            if atr_cached and (_time.monotonic() - atr_cached['ts']) < ATR_CACHE_TTL:
                atr = atr_cached['atr']
            else:
                atr = ATR_MIN_FALLBACK.get(symbol, 5.0)
                try:
                    trades_z = _scalp_engine._get_trades(symbol)
                    bars_z   = build_m1_bars(trades_z, symbol)
                    if len(bars_z) >= MIN_BARS_FOR_ATR + 1:
                        _, _, _, atr_z, _, _, _ = evaluate_s1_candle(bars_z, live, symbol)
                        if atr_z:
                            atr = atr_z
                except Exception:
                    pass

            from datetime import datetime, timezone
            now_utc  = datetime.now(timezone.utc)
            rth_open = now_utc.replace(hour=14, minute=30, second=0, microsecond=0)
            on_min   = max(0.0, (now_utc - rth_open).total_seconds() / 60.0)

            delta_ratio = _scalp_engine._get_delta_ratio(symbol)

            result = evaluate_zones(
                price=price, levels=levels, live_data=live,
                delta_ratio=delta_ratio, atr=atr, symbol=symbol,
                quantity=quantity, on_session_minutes=on_min,
                macro_context=macro_ctx,
            )

        # ── Regime info — usa resultado já calculado (sem dupla chamada) ──────
        regime_value = result.get("regime", "UNDEFINED")
        try:
            regime_enum = ScalpDayRegime(regime_value)
        except ValueError:
            regime_enum = ScalpDayRegime.UNDEFINED
        regime_info = REGIME_RISK_PARAMS.get(regime_enum, {})

        # levels snapshot — lê do engine se não tínhamos recomputado
        if not levels:
            levels = await _scalp_engine._get_levels(symbol) or {}

        d1_poc = levels.get("d1_poc", 0.0) or 0.0
        d1_vah = levels.get("d1_vah", 0.0) or 0.0
        d1_val = levels.get("d1_val", 0.0) or 0.0

        return {
            "symbol":      symbol,
            "price":       price,
            "atr":         round(atr, 3),
            "regime":      regime_value,
            "regime_desc": regime_info.get("description", ""),
            "sl_atr_mult": regime_info.get("sl_atr"),
            "be_atr_mult": regime_info.get("be_atr"),
            "status":      result.get("status"),
            "quality":     result.get("quality"),
            "active_zone": result.get("best_zone"),
            "s3":          result.get("s3"),
            "all_zones":      result.get("all_zones", []),
            "block_reasons":  result.get("block_reasons", []),
            "score_breakdown": result.get("score_breakdown"),
            "macro_context":  result.get("macro_context_applied", macro_ctx),
            "levels_snapshot": {
                "vwap": levels.get("vwap"), "vwap_std": levels.get("vwap_std"),
                "vwap_upper_1": levels.get("vwap_upper_1"), "vwap_lower_1": levels.get("vwap_lower_1"),
                "vwap_upper_2": levels.get("vwap_upper_2"), "vwap_lower_2": levels.get("vwap_lower_2"),
                "vwap_upper_3": levels.get("vwap_upper_3"), "vwap_lower_3": levels.get("vwap_lower_3"),
                "d1_poc": d1_poc, "d1_vah": d1_vah, "d1_val": d1_val,
                "onh": levels.get("onh"), "onl": levels.get("onl"),
            },
        }
    except Exception as e:
        logger.exception(f"Erro em get_scalp_zones({symbol}): {e}")
        raise HTTPException(status_code=500, detail=str(e))


@scalp_router.get("/levels/{symbol}")
async def get_scalp_levels(symbol: str):
    """
    Retorna o contexto de mercado completo para o símbolo:
    VWAP ±1σ/2σ/3σ, VP Sessão (POC/VAH/VAL), VP D-1, ONH/ONL, zona VWAP atual.
    """
    symbol = symbol.upper()
    if _scalp_engine is None:
        raise HTTPException(status_code=503, detail="ScalpEngine não inicializado")
    try:
        levels = await _scalp_engine._get_levels(symbol)
        # Adiciona zona VWAP se tivermos preço e bandas
        if levels:
            from services.scalp_engine import classify_vwap_zone
            vwap = levels.get("vwap", 0.0) or 0.0
            u1 = levels.get("vwap_upper_1", 0.0) or 0.0
            l1 = levels.get("vwap_lower_1", 0.0) or 0.0
            u2 = levels.get("vwap_upper_2", 0.0) or 0.0
            l2 = levels.get("vwap_lower_2", 0.0) or 0.0
            u3 = levels.get("vwap_upper_3", 0.0) or 0.0
            l3 = levels.get("vwap_lower_3", 0.0) or 0.0
            # Preço atual via live data
            live = {}
            try:
                if hasattr(_live_data_service, 'get_live_data'):
                    live = _live_data_service.get_live_data(symbol) or {}
            except Exception:
                pass
            price = live.get("last_price", 0.0) or 0.0
            if vwap > 0 and u1 > 0 and l1 > 0 and price > 0:
                levels["vwap_zone"] = classify_vwap_zone(price, vwap, u1, l1, u2, l2, u3, l3)
                levels["last_price"] = price
        return {"symbol": symbol, "levels": levels}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Scalp Snapshots (Auto-Tune) ─────────────────────────────────────────────

@scalp_router.get("/snapshots/stats")
async def get_scalp_snapshot_stats_route():
    """
    Retorna estatísticas da colecção scalp_snapshots para o painel Auto-Tune.
    Espelha /api/replay/snapshot-stats mas para dados do ScalpEngine.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    try:
        from services.scalp_snapshot_service import get_scalp_snapshot_stats
        stats = await get_scalp_snapshot_stats(_database)
        return stats
    except Exception as e:
        logger.exception(f"Scalp snapshot stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@scalp_router.get("/snapshots")
async def query_scalp_snapshots_route(
    symbol:       str  = Query("MNQ", description="Símbolo (MNQ | MES)"),
    mode:         Optional[str] = Query(None, description="Modo (FLOW | ZONES)"),
    regime:       Optional[str] = Query(None, description="S1 Regime"),
    quality:      Optional[str] = Query(None, description="S2 Quality (STRONG | MODERATE | WEAK | NO_TRADE)"),
    gamma_regime: Optional[str] = Query(None, description="Gamma regime (LONG_GAMMA | SHORT_GAMMA | UNKNOWN)"),
    ts_hard_stop: Optional[bool] = Query(None, description="Filtrar por TS Hard Stop activo"),
    action_only:  bool = Query(False, description="Apenas snapshots com BUY ou SELL"),
    start:        Optional[str] = Query(None, description="ISO datetime início (UTC)"),
    end:          Optional[str] = Query(None, description="ISO datetime fim (UTC)"),
    limit:        int  = Query(500, ge=1, le=2000, description="Máximo de documentos"),
):
    """
    Consulta snapshots do ScalpEngine com filtros para replay / análise Auto-Tune.
    Inclui filtros de Gamma Regime e Term Structure além dos filtros base.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    try:
        from services.scalp_snapshot_service import query_scalp_snapshots

        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
        end_dt   = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None

        results = await query_scalp_snapshots(
            _database,
            symbol=symbol.upper(),
            start=start_dt,
            end=end_dt,
            mode=mode,
            regime=regime,
            quality=quality,
            gamma_regime=gamma_regime,
            ts_hard_stop=ts_hard_stop,
            action_only=action_only,
            limit=limit,
        )
        return {"symbol": symbol.upper(), "count": len(results), "snapshots": results}
    except Exception as e:
        logger.exception(f"Scalp snapshot query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-TUNE — Replay, Optimização (Grid/Bayesian) e Walk-Forward
# ══════════════════════════════════════════════════════════════════════════════

class ScalpReplayRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict,
                                   description="Config de replay (merge sobre defaults)")


class ScalpOptimizeRequest(BaseModel):
    method: str = Field("BAYESIAN", description="GRID | BAYESIAN")
    mode: str = Field("ZONES", description="ZONES | ZONES_MNQ | ZONES_MES | FLOW | ALL")
    symbol: Optional[str] = Field(None, description="MNQ | MES — se fornecido, injectado em base_config")
    base_config: Dict[str, Any] = Field(default_factory=dict)
    objective: str = Field("sharpe", description="sharpe|sortino|profit_factor|net_pnl|expectancy|min_drawdown|calmar")
    grid_cfg: Optional[Dict[str, Any]] = Field(None, description="Steps por param (GRID apenas)")
    n_random: int = Field(8, ge=3, le=20, description="Avaliações aleatórias iniciais (BAYESIAN)")
    n_iter: int = Field(25, ge=5, le=60, description="Iterações guiadas por GP (BAYESIAN)")
    min_snapshots: int = Field(5, ge=1)
    custom_space: Optional[Dict[str, Any]] = Field(
        None,
        description="Espaço de busca customizado: {param_key: [min, max]}. "
                    "Substitui o espaço padrão quando presente.",
    )


class ScalpWalkForwardRequest(BaseModel):
    base_config: Dict[str, Any] = Field(default_factory=dict)
    method: str = Field("BAYESIAN", description="GRID | BAYESIAN")
    mode: str = Field("ZONES", description="ZONES | ZONES_MNQ | ZONES_MES | FLOW | ALL")
    symbol: Optional[str] = Field(None, description="MNQ | MES — se fornecido, injectado em base_config")
    objective: str = Field("sharpe")
    train_days: int = Field(10, ge=3, le=30)
    test_days: int = Field(3, ge=1, le=10)
    n_folds: int = Field(4, ge=2, le=8)
    n_random: int = Field(5, ge=3, le=15)
    n_iter: int = Field(15, ge=5, le=40)
    custom_space: Optional[Dict[str, Any]] = Field(
        None,
        description="Espaço de busca customizado: {param_key: [min, max]}. "
                    "Substitui o espaço padrão quando presente.",
    )


@scalp_router.post("/tune/replay")
@scalp_router.patch("/tune/replay")
async def scalp_tune_replay(body: ScalpReplayRequest):
    """Executa um único replay walk-forward em scalp_snapshots."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    try:
        from services.scalp_replay_engine import run_scalp_replay
        result = await run_scalp_replay(_database, body.config)
        return result
    except Exception as e:
        logger.exception(f"Scalp replay error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@scalp_router.post("/tune/optimize")
@scalp_router.patch("/tune/optimize")
async def scalp_tune_optimize(body: ScalpOptimizeRequest):
    """
    Inicia optimização de parâmetros em background.
    Retorna imediatamente com optimization_id.
    Suporta GRID e BAYESIAN (GP + EI via scipy).
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    try:
        from services.scalp_optimizer import start_scalp_optimization
        # Injeta symbol no base_config caso venha como campo top-level
        merged_config = dict(body.base_config)
        if body.symbol and "symbol" not in merged_config:
            merged_config["symbol"] = body.symbol.upper()
        result = await start_scalp_optimization(
            database=_database,
            method=body.method.upper(),
            mode=body.mode.upper(),
            base_config=merged_config,
            objective=body.objective,
            grid_cfg=body.grid_cfg,
            n_random=body.n_random,
            n_iter=body.n_iter,
            min_snapshots=body.min_snapshots,
            custom_space=body.custom_space,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Scalp optimize error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@scalp_router.get("/tune/optimize/status")
async def scalp_tune_optimize_status():
    """Retorna o progresso da optimização activa."""
    from services.scalp_optimizer import get_scalp_optimization_status
    status = get_scalp_optimization_status()
    if status is None:
        return {"status": "idle"}
    return status


@scalp_router.get("/tune/optimize/result")
async def scalp_tune_optimize_result():
    """Retorna o resultado completo após optimização concluída."""
    from services.scalp_optimizer import get_scalp_optimization_result
    result = get_scalp_optimization_result()
    if result is None:
        raise HTTPException(status_code=404, detail="Nenhum resultado disponível. Optimização ainda em execução ou não iniciada.")
    return result


@scalp_router.post("/tune/optimize/cancel")
async def scalp_tune_optimize_cancel():
    """Cancela a optimização em curso."""
    from services.scalp_optimizer import cancel_scalp_optimization
    cancelled = await cancel_scalp_optimization()
    return {"cancelled": cancelled}


@scalp_router.post("/tune/walk-forward")
@scalp_router.patch("/tune/walk-forward")
async def scalp_tune_walk_forward(body: ScalpWalkForwardRequest):
    """
    Executa Walk-Forward Optimization completa.
    Bloqueia até terminar — recomendado apenas para janelas pequenas.
    Para sessões longas, use /tune/optimize com method=BAYESIAN.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    try:
        from services.scalp_optimizer import run_walk_forward
        # Injeta symbol no base_config caso venha como campo top-level
        merged_config = dict(body.base_config)
        if body.symbol and "symbol" not in merged_config:
            merged_config["symbol"] = body.symbol.upper()
        result = await run_walk_forward(
            database=_database,
            base_config=merged_config,
            objective=body.objective,
            method=body.method.upper(),
            mode=body.mode.upper(),
            train_days=body.train_days,
            test_days=body.test_days,
            n_folds=body.n_folds,
            n_random=body.n_random,
            n_iter=body.n_iter,
            custom_space=body.custom_space,
        )
        return result
    except Exception as e:
        logger.exception(f"Scalp walk-forward error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@scalp_router.get("/tune/params/space")
async def scalp_tune_params_space(mode: str = Query("ZONES")):
    """
    Retorna o espaço de parâmetros para o modo indicado.

    Inclui:
    - `params` — espaço padrão do modo (min/max/is_int por param)
    - `catalogue` — catálogo completo com grupo + label (para UI configurável)
    - `objectives` — lista de objectivos disponíveis
    """
    from services.scalp_optimizer import (
        MODE_SEARCH_SPACES, PARAM_CATALOGUE, OBJECTIVE_KEYS, GROUP_LABELS,
    )
    space = MODE_SEARCH_SPACES.get(mode.upper())
    if not space:
        raise HTTPException(status_code=400, detail=f"Modo inválido: {mode}")

    default_space_keys = set(space.keys())

    return {
        "mode": mode.upper(),
        "params": {
            k: {"min": v[0], "max": v[1], "is_int": isinstance(v[0], int)}
            for k, v in space.items()
        },
        "catalogue": {
            k: {
                "min":     v[0],
                "max":     v[1],
                "is_int":  isinstance(v[0], int),
                "group":   GROUP_LABELS.get(k, ("other", k))[0],
                "label":   GROUP_LABELS.get(k, ("other", k))[1],
                "in_mode": k in default_space_keys,
            }
            for k, v in PARAM_CATALOGUE.items()
        },
        "objectives": list(OBJECTIVE_KEYS.keys()),
    }


# ══════════════════════════════════════════════════════════════════
# SCHEDULE DO AUTO-TUNE SCALP
# ══════════════════════════════════════════════════════════════════

class ScalpScheduleRequest(BaseModel):
    enabled:                   bool  = True
    frequency:                 str   = Field("weekly", description="daily | weekly | custom_hours")
    custom_hours:              int   = Field(24, ge=1, le=168)
    day_of_week:               int   = Field(6, ge=0, le=6, description="0=Seg … 6=Dom")
    hour_utc:                  int   = Field(6, ge=0, le=23)
    mode:                      str   = Field("FLOW", description="ZONES | FLOW")
    method:                    str   = Field("BAYESIAN", description="GRID | BAYESIAN")
    objective:                 str   = Field("sharpe")
    symbol:                    str   = Field("MNQ")
    train_days:                int   = Field(10, ge=3, le=30)
    test_days:                 int   = Field(3,  ge=1, le=10)
    n_folds:                   int   = Field(4,  ge=2, le=8)
    n_random:                  int   = Field(5,  ge=3, le=15)
    n_iter:                    int   = Field(15, ge=5, le=40)
    min_snapshots:             int   = Field(50, ge=10)
    auto_apply:                bool  = False
    improvement_threshold_pct: float = Field(5.0, ge=0.0, le=100.0)


@scalp_router.get("/tune/schedule")
async def get_scalp_tune_schedule():
    """Retorna o schedule activo do Auto-Tune Scalp."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_scheduler import get_scalp_schedule
    doc = await get_scalp_schedule(_database)
    if doc is None:
        return {"active": False, "message": "Sem schedule configurado"}
    return doc


@scalp_router.post("/tune/schedule")
@scalp_router.patch("/tune/schedule")
async def set_scalp_tune_schedule(body: ScalpScheduleRequest):
    """Cria ou actualiza o schedule do Auto-Tune Scalp."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_scheduler import update_scalp_schedule
    doc = await update_scalp_schedule(_database, body.model_dump())
    return doc


@scalp_router.delete("/tune/schedule")
async def delete_scalp_tune_schedule():
    """Desactiva o schedule do Auto-Tune Scalp."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_scheduler import disable_scalp_schedule
    await disable_scalp_schedule(_database)
    return {"status": "disabled"}


@scalp_router.get("/tune/schedule/history")
async def get_scalp_tune_schedule_history(limit: int = Query(default=20, ge=1, le=100)):
    """Retorna o histórico de runs do scheduler Auto-Tune."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_scheduler import get_scalp_schedule_history
    history = await get_scalp_schedule_history(_database, limit=limit)
    return {"history": history, "count": len(history)}


@scalp_router.get("/tune/calibration")
async def scalp_tune_calibration(
    symbol: str  = Query("MNQ"),
    days:   int  = Query(default=90, ge=1, le=365),
    min_n:  int  = Query(default=20, ge=5, le=100),
):
    """
    Calibração granular por (zone_type × s2_quality × s1_regime × session_phase).

    Agrega scalp_trades fechados e calcula win rate por combinação dimensional.
    Progressive collapsing quando n < min_n:
      1. Colapsa session_phase → (zone_type, quality, regime)
      2. Colapsa regime       → (zone_type, quality)
      3. Colapsa quality      → (zone_type)
    Cada célula retorna quais dimensões foram colapsadas e se ainda é insuficiente.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")

    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {
            "symbol":      symbol,
            "zone_type":   {"$exists": True, "$ne": None},
            "exit_reason": {"$in": ["TARGET_HIT", "STOP_HIT"]},
            "created_at":  {"$gte": cutoff.isoformat()},
        }},
        {"$addFields": {
            "_session_phase": {"$switch": {
                "branches": [
                    {"case": {"$eq":  [{"$type": "$session_minutes"}, "null"]},  "then": "UNKNOWN"},
                    {"case": {"$lt":  ["$session_minutes", 30]},                 "then": "OPEN"},
                    {"case": {"$lt":  ["$session_minutes", 120]},                "then": "MID"},
                ],
                "default": "LATE",
            }},
            "_win": {"$cond": [{"$eq": ["$exit_reason", "TARGET_HIT"]}, 1, 0]},
        }},
        {"$group": {
            "_id": {
                "zone_type":     "$zone_type",
                "s2_quality":    {"$ifNull": ["$s2_quality",    "UNKNOWN"]},
                "s1_regime":     {"$ifNull": ["$s1_regime",     "UNKNOWN"]},
                "session_phase": "$_session_phase",
            },
            "n":    {"$sum": 1},
            "wins": {"$sum": "$_win"},
        }},
        {"$sort": {"_id.zone_type": 1, "_id.s2_quality": 1, "_id.s1_regime": 1, "_id.session_phase": 1}},
    ]

    raw_cells = []
    async for doc in _database["scalp_trades"].aggregate(pipeline):
        raw_cells.append({
            "zone_type":     doc["_id"]["zone_type"],
            "s2_quality":    doc["_id"]["s2_quality"],
            "s1_regime":     doc["_id"]["s1_regime"],
            "session_phase": doc["_id"]["session_phase"],
            "n":             doc["n"],
            "wins":          doc["wins"],
            "losses":        doc["n"] - doc["wins"],
        })

    # ── Progressive collapsing ────────────────────────────────────────────────
    # Tenta colapsar dimensões progressivamente quando n < min_n.
    # Cada iteração usa um nível de detalhe menor.

    def _merge(cells, key_fn):
        """Agrega células com a mesma key_fn."""
        merged = {}
        for c in cells:
            k = key_fn(c)
            if k not in merged:
                merged[k] = {"n": 0, "wins": 0}
            merged[k]["n"]    += c["n"]
            merged[k]["wins"] += c["wins"]
        return merged

    def _build_result(cells, collapsed_dims):
        result = []
        for c in cells:
            n = c["n"]
            result.append({
                "zone_type":      c["zone_type"],
                "s2_quality":     c.get("s2_quality", "*"),
                "s1_regime":      c.get("s1_regime",  "*"),
                "session_phase":  c.get("session_phase", "*"),
                "n":              n,
                "wins":           c["wins"],
                "losses":         c["n"] - c["wins"],
                "win_rate":       round(c["wins"] / n, 4) if n > 0 else None,
                "collapsed_dims": collapsed_dims,
                "insufficient":   n < min_n,
            })
        return result

    # Nível 0: célula completa 4D
    sufficient = [c for c in raw_cells if c["n"] >= min_n]
    need_collapse = [c for c in raw_cells if c["n"] < min_n]

    output_cells = _build_result(sufficient, [])

    # Nível 1: colapsa session_phase → (zone_type, quality, regime)
    if need_collapse:
        lvl1_merged = _merge(need_collapse, lambda c: (c["zone_type"], c["s2_quality"], c["s1_regime"]))
        lvl1_cells  = [
            {"zone_type": k[0], "s2_quality": k[1], "s1_regime": k[2],
             "n": v["n"], "wins": v["wins"]}
            for k, v in lvl1_merged.items()
        ]
        still_insuf = [c for c in lvl1_cells if c["n"] < min_n]
        ok_lvl1     = [c for c in lvl1_cells if c["n"] >= min_n]
        output_cells += _build_result(ok_lvl1, ["session_phase"])

        # Nível 2: colapsa regime → (zone_type, quality)
        if still_insuf:
            lvl2_merged = _merge(still_insuf, lambda c: (c["zone_type"], c["s2_quality"]))
            lvl2_cells  = [
                {"zone_type": k[0], "s2_quality": k[1],
                 "n": v["n"], "wins": v["wins"]}
                for k, v in lvl2_merged.items()
            ]
            still_insuf2 = [c for c in lvl2_cells if c["n"] < min_n]
            ok_lvl2      = [c for c in lvl2_cells if c["n"] >= min_n]
            output_cells += _build_result(ok_lvl2, ["session_phase", "s1_regime"])

            # Nível 3: colapsa quality → (zone_type)
            if still_insuf2:
                lvl3_merged = _merge(still_insuf2, lambda c: (c["zone_type"],))
                lvl3_cells  = [
                    {"zone_type": k[0], "n": v["n"], "wins": v["wins"]}
                    for k, v in lvl3_merged.items()
                ]
                output_cells += _build_result(lvl3_cells, ["session_phase", "s1_regime", "s2_quality"])

    # Ordena: zone_type, collapsed_dims len, win_rate desc
    output_cells.sort(key=lambda c: (
        c["zone_type"],
        len(c["collapsed_dims"]),
        -(c["win_rate"] or 0),
    ))

    total_n = sum(c["n"] for c in raw_cells)
    zone_types = sorted({c["zone_type"] for c in raw_cells})

    return {
        "summary": {
            "symbol":            symbol,
            "days":              days,
            "min_n":             min_n,
            "total_trades":      total_n,
            "zone_types_found":  len(zone_types),
            "zone_types":        zone_types,
            "cells_total":       len(output_cells),
            "cells_sufficient":  sum(1 for c in output_cells if not c["insufficient"]),
            "cells_collapsed":   sum(1 for c in output_cells if c["collapsed_dims"]),
            "cells_insufficient": sum(1 for c in output_cells if c["insufficient"]),
        },
        "cells": output_cells,
    }


@scalp_router.get("/tune/diagnostics")
async def scalp_tune_diagnostics(
    symbol:      str           = Query("MNQ"),
    days:        int           = Query(default=30, ge=1, le=365),
    mode_filter: Optional[str] = Query(default=None, description="Filtro de modo: FLOW | ZONES"),
    session:     Optional[str] = Query(default=None, description="Sessão: RTH_OPEN | RTH_MID | RTH_CLOSE | OVERNIGHT | HALTED"),
):
    """
    Diagnóstico de bloqueio de sinais sobre scalp_snapshots.

    Retorna 4 análises em paralelo:
      1. outcome_distribution  — READY / S2_BLOCKED / S1_NO_DATA por regime + taxa de disparo
      2. s2_block_reasons      — quais gates bloqueiam mais, por regime
      3. zone_quality_dist     — distribuição de qualidade ZONES por regime
      4. ofi_slow_impact       — % de candidatos que o penalty OFI Slow converte em bloqueio

    Motor de sugestões automáticas emite texto em português quando n >= 30 snapshots.
    session: RTH_OPEN | RTH_MID | RTH_CLOSE | OVERNIGHT | HALTED (omitir = todas)
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.trading_calendar_service import VALID_SESSIONS
    if session is not None and session not in VALID_SESSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"session inválida: '{session}'. Válidas: {sorted(VALID_SESSIONS)}",
        )
    try:
        from services.scalp_diagnostics_service import get_scalp_diagnostics
        return await get_scalp_diagnostics(
            _database,
            symbol=symbol,
            days=days,
            mode_filter=mode_filter,
            session=session or None,
        )
    except Exception as e:
        logger.exception(f"Scalp diagnostics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@scalp_router.post("/tune/schedule/run-now")
async def run_scalp_tune_now():
    """Força um run imediato do Walk-Forward agendado (ignora next_run_at)."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_scheduler import (
        get_scalp_schedule, _execute_scheduled_run, _scheduler_running
    )
    schedule = await get_scalp_schedule(_database)
    if not schedule:
        raise HTTPException(status_code=404, detail="Sem schedule configurado")
    if _scheduler_running:
        raise HTTPException(status_code=409, detail="Scheduler já está em execução")

    # Converte next_run_at de ISO string para datetime se necessário
    from services.scalp_scheduler import update_scalp_schedule as _upd
    asyncio.create_task(_execute_scheduled_run(_database, schedule))
    return {"status": "started", "message": "Run forçado iniciado em background"}


# ══════════════════════════════════════════════════════════════════════════════
# ANÁLISE COMBINADA D1×D2
# ══════════════════════════════════════════════════════════════════════════════

@scalp_router.get("/tune/combined")
async def get_combined_analysis_route(
    symbol:    str            = Query("MNQ"),
    days_diag: int            = Query(default=30, ge=1, le=180),
    days_cal:  int            = Query(default=90, ge=1, le=365),
    max_delta: float          = Query(default=0.05, ge=0.01, le=0.20),
    session:   Optional[str]  = Query(default=None),
):
    """
    Análise combinada D1×D2 com filtro opcional de sessão.
    session=NY     → análise NY (RTH_OPEN + RTH_MID + RTH_CLOSE) com params NY
    session=GLOBEX → análise Globex (OVERNIGHT + HALTED) com params Globex
    session=RTH_ALL | RTH_OPEN | RTH_MID | RTH_CLOSE | OVERNIGHT | HALTED → granular
    session omitido → modo ALL: resposta dupla {ny: {...}, globex: {...}}
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_combined_service import get_combined_analysis
    from services.trading_calendar_service import VALID_SESSIONS
    if session and session not in VALID_SESSIONS:
        raise HTTPException(status_code=422,
            detail=f"session inválida: '{session}'. Válidas: {sorted(VALID_SESSIONS)}")
    return await get_combined_analysis(_database, symbol, days_diag, days_cal, max_delta, session=session)


@scalp_router.post("/tune/combined/apply")
@scalp_router.patch("/tune/combined/apply")
async def apply_combined_route(body: dict):
    """
    Aplica sugestões geradas pela análise combinada ao scalp_config activo.
    body: { suggestions: [...], dry_run: bool }
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_combined_service import apply_combined_suggestions
    suggestions = body.get("suggestions", [])
    dry_run     = bool(body.get("dry_run", True))
    return await apply_combined_suggestions(_database, suggestions, dry_run)


@scalp_router.get("/tune/combined/schedule")
async def get_combined_schedule_route():
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_combined_service import get_combined_schedule
    schedule = await get_combined_schedule(_database)
    return {"schedule": schedule}


@scalp_router.post("/tune/combined/schedule")
@scalp_router.patch("/tune/combined/schedule")
async def set_combined_schedule_route(body: dict):
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_combined_service import update_combined_schedule
    return await update_combined_schedule(_database, body)


@scalp_router.delete("/tune/combined/schedule")
async def delete_combined_schedule_route():
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_combined_service import disable_combined_schedule
    await disable_combined_schedule(_database)
    return {"status": "disabled"}


@scalp_router.post("/tune/combined/run-now")
async def run_combined_now_route(symbol: str = Query("MNQ")):
    """Trigger manual imediato da análise combinada."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_combined_service import run_combined_now
    return await run_combined_now(_database, symbol)


@scalp_router.get("/tune/combined/history")
async def get_combined_history_route(
    symbol: str = Query("MNQ"),
    limit:  int = Query(default=20, ge=1, le=100),
):
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.scalp_combined_service import get_combined_history
    rows = await get_combined_history(_database, symbol, limit)
    return {"history": rows, "total": len(rows)}


# ══════════════════════════════════════════════════════════════════════════════
# ATLAS STORAGE — Monitor de espaço
# ══════════════════════════════════════════════════════════════════════════════

@scalp_router.get("/atlas/storage")
async def atlas_storage_status():
    """
    Retorna o estado actual do espaço utilizado no MongoDB Atlas.
    Inclui breakdown por colecção e nível de alerta (OK / WARN / HIGH / CRITICAL).
    """
    from services.atlas_storage_monitor import get_storage_status
    return get_storage_status()


@scalp_router.post("/atlas/storage/check")
async def atlas_storage_check_now(plan: str = Query("M0")):
    """
    Força uma verificação imediata do espaço Atlas (não espera pelo ciclo horário).
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.atlas_storage_monitor import check_now
    return await check_now(_database, plan=plan)


# ══════════════════════════════════════════════════════════════════════════════
# FUNIL DE SINAIS — audit trail S1→S2→S3→G2→EXECUTED
# ══════════════════════════════════════════════════════════════════════════════

@scalp_router.get("/funnel")
async def get_signal_funnel(
    symbol:  Optional[str] = Query(None),
    session: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to:   Optional[str] = Query(None, description="YYYY-MM-DD"),
    paper:     Optional[bool] = Query(None),
    limit:     int = Query(500, ge=1, le=5000),
):
    """
    Agrega o log de sinais (scalp_signal_log) num funil S1→S2→S3→Executado.
    Retorna total de zone-touches, distribuição por gate_outcome e breakdown
    de motivos de bloqueio por gate.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")

    col = _database["scalp_signal_log"]
    match: Dict[str, Any] = {}
    _RTH_LABELS = ["RTH_OPEN", "RTH_MID", "RTH_CLOSE"]
    if symbol:
        match["symbol"] = symbol.upper()
    if session:
        _sess = session.upper()
        if _sess in ("RTH_ALL", "NY"):
            match["session"] = {"$in": _RTH_LABELS}
        else:
            match["session"] = _sess
    if paper is not None:
        match["paper"] = paper
    if date_from or date_to:
        ts_filter: Dict[str, Any] = {}
        if date_from:
            ts_filter["$gte"] = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        if date_to:
            from datetime import timedelta
            ts_filter["$lte"] = (datetime.fromisoformat(date_to) + timedelta(days=1)).replace(tzinfo=timezone.utc)
        match["ts"] = ts_filter

    docs = await col.find(match, {"_id": 0}).sort("ts", -1).limit(limit).to_list(length=limit)

    # ── Agrega ──────────────────────────────────────────────────────────────
    outcome_counts: Dict[str, int] = {}
    block_reasons:  Dict[str, Dict[str, int]] = {}
    zone_types:     Dict[str, Dict[str, int]] = {}
    sessions_agg:   Dict[str, int] = {}
    s2_quality_agg: Dict[str, int] = {}
    regime_agg:     Dict[str, Dict[str, int]] = {}
    g2_sub_agg:     Dict[str, int] = {}
    recent:         list = []

    GATE_ORDER = ["MODE_BLOCKED", "G7_BLOCKED", "G8_BLOCKED", "G2_BLOCKED", "EXECUTED"]

    for d in docs:
        outcome   = d.get("gate_outcome", "UNKNOWN")
        zone      = d.get("zone_type", "UNKNOWN")
        sess      = d.get("session", "UNKNOWN")
        quality   = d.get("s2_quality", "UNKNOWN")
        reason    = d.get("gate_reason", "")
        regime    = d.get("s1_regime") or "UNKNOWN"
        vwap_zone = d.get("vwap_zone") or "UNKNOWN"

        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

        if outcome not in block_reasons:
            block_reasons[outcome] = {}
        if reason:
            block_reasons[outcome][reason] = block_reasons[outcome].get(reason, 0) + 1

        if zone not in zone_types:
            zone_types[zone] = {}
        zone_types[zone][outcome] = zone_types[zone].get(outcome, 0) + 1

        sessions_agg[sess]      = sessions_agg.get(sess, 0) + 1
        s2_quality_agg[quality] = s2_quality_agg.get(quality, 0) + 1

        if regime not in regime_agg:
            regime_agg[regime] = {}
        regime_agg[regime][outcome] = regime_agg[regime].get(outcome, 0) + 1

        if outcome == "G2_BLOCKED":
            for sub in (d.get("g2_sub_reasons") or []):
                g2_sub_agg[sub] = g2_sub_agg.get(sub, 0) + 1

        if len(recent) < 50:
            recent.append({
                "ts":            d.get("ts").isoformat() if d.get("ts") else None,
                "symbol":        d.get("symbol"),
                "session":       sess,
                "zone_type":     zone,
                "zone_score":    d.get("zone_score"),
                "s2_quality":    quality,
                "s1_regime":     regime,
                "vwap_zone":     vwap_zone,
                "gate_outcome":  outcome,
                "gate_reason":   reason,
                "g2_sub_reasons": d.get("g2_sub_reasons") or [],
                "paper":         d.get("paper"),
            })

    total = len(docs)
    executed = outcome_counts.get("EXECUTED", 0)

    # ── Funil linear ────────────────────────────────────────────────────────
    # A cada gate, quantos passaram (chegaram até ele)
    funnel_steps = []
    cumulative = total
    for gate in GATE_ORDER:
        blocked_here = outcome_counts.get(gate, 0) if gate != "EXECUTED" else 0
        passed       = executed if gate == "EXECUTED" else 0
        funnel_steps.append({
            "gate":    gate,
            "reached": cumulative,
            "blocked": blocked_here if gate != "EXECUTED" else 0,
            "executed": executed if gate == "EXECUTED" else 0,
            "pct_of_total": round(cumulative / total * 100, 1) if total else 0,
        })
        if gate != "EXECUTED":
            cumulative -= blocked_here

    # ── Cross-trade match ────────────────────────────────────────────────────
    # Para os EXECUTED: busca o trade correspondente pelo timestamp
    exec_docs = [d for d in docs if d.get("gate_outcome") == "EXECUTED"]
    exec_trade_ids = [d.get("trade_id") for d in exec_docs if d.get("trade_id")]
    trades_col = _database["scalp_trades"]
    win_count = loss_count = 0
    if exec_trade_ids:
        matched = await trades_col.find(
            {"_id": {"$in": exec_trade_ids}},
            {"_id": 1, "pnl_usd": 1}
        ).to_list(length=len(exec_trade_ids))
        for t in matched:
            pnl = t.get("pnl_usd", 0) or 0
            if pnl > 0:
                win_count += 1
            elif pnl < 0:
                loss_count += 1

    return {
        "total_zone_touches": total,
        "executed":           executed,
        "execution_rate_pct": round(executed / total * 100, 1) if total else 0,
        "win_count":          win_count,
        "loss_count":         loss_count,
        "win_rate_pct":       round(win_count / executed * 100, 1) if executed else None,
        "outcome_counts":     outcome_counts,
        "funnel_steps":       funnel_steps,
        "block_reasons":      block_reasons,
        "zone_types":         zone_types,
        "by_session":         sessions_agg,
        "by_s2_quality":      s2_quality_agg,
        "by_regime":          regime_agg,
        "g2_sub_reasons":     dict(sorted(g2_sub_agg.items(), key=lambda x: -x[1])),
        "recent":             recent,
    }


@scalp_router.get("/daily-funnel")
async def get_daily_funnel(
    date:   Optional[str] = Query(None, description="YYYY-MM-DD (default: hoje)"),
    symbol: Optional[str] = Query(None),
):
    """
    Daily Funnel Report — agrega scalp_snapshots num funil N1→N2→D30→N3.
    Inclui benchmark hipotético (N2 que teriam atingido TP vs SL),
    top block reasons, matriz zona×sessão e distribuição N1 por regime/sessão.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")

    from datetime import timedelta

    # ── Data ────────────────────────────────────────────────────────────────
    if date:
        try:
            day_start = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de data inválido. Use YYYY-MM-DD.")
    else:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    # ── Filtro base ──────────────────────────────────────────────────────────
    base_q: Dict[str, Any] = {"recorded_at": {"$gte": day_start, "$lt": day_end}}
    if symbol:
        base_q["symbol"] = symbol.upper()

    col = _database["scalp_snapshots"]

    # ── 1. Contagens de status ───────────────────────────────────────────────
    no_zone   = await col.count_documents({**base_q, "scalp_status": "NO_ZONE"})
    no_signal = await col.count_documents({**base_q, "scalp_status": "NO_SIGNAL"})
    active    = await col.count_documents({**base_q, "scalp_status": "ACTIVE_SIGNAL"})
    d30_bl    = await col.count_documents({**base_q, "scalp_status": "D30_BLOCKED"})
    blocked   = await col.count_documents({**base_q, "scalp_status": "BLOCKED"})
    total_snaps = no_zone + no_signal + active + d30_bl + blocked

    n1 = total_snaps - no_zone  # avaliações com zona activa

    # ── 2. Trades do dia ─────────────────────────────────────────────────────
    trades_col = _database["scalp_trades"]
    day_str = day_start.strftime("%Y-%m-%d")
    day_str_end = day_end.strftime("%Y-%m-%d")
    _trade_proj = {
        "_id": 1, "symbol": 1, "action": 1,
        "pnl_pts": 1, "pnl_usd": 1, "exit_reason": 1,
        "zone_type": 1, "session_label": 1, "s1_regime": 1, "s2_quality": 1,
        "zone_score": 1, "vwap_zone": 1, "d30_state": 1,
        "entry_price": 1, "stop_loss_price": 1, "take_profit_price": 1,
        "created_at": 1, "closed_at": 1, "state": 1,
    }
    # trades usam created_at como string ISO
    all_day_trades = await trades_col.find(
        {"created_at": {"$gte": day_str, "$lt": day_str_end}}, _trade_proj
    ).to_list(None)

    # fallback: usar datetime se string falhar
    if not all_day_trades:
        all_day_trades = await trades_col.find(
            {"created_at": {"$gte": day_start, "$lt": day_end}}, _trade_proj
        ).to_list(None)

    n3 = len(all_day_trades)
    closed_trades = [t for t in all_day_trades if t.get("state") not in (None, "OPEN", "PENDING", "WATCHING")]
    wins_t   = sum(1 for t in closed_trades if (t.get("pnl_pts") or 0) > 0)
    losses_t = sum(1 for t in closed_trades if (t.get("pnl_pts") or 0) <= 0)
    pnl_pts_total = round(sum(t.get("pnl_pts") or 0 for t in closed_trades), 2)
    pnl_usd_total = round(sum(t.get("pnl_usd") or 0 for t in closed_trades), 2)

    # ── 2b. N3 Context — breakdown por dimensão ───────────────────────────────
    def _is_win(t: Dict) -> bool:
        return (t.get("pnl_pts") or 0) > 0

    def _score_bucket(s) -> str:
        if s is None: return "?"
        if s < 0:    return "<0"
        if s < 2:    return "0-2"
        if s < 3:    return "2-3"
        if s < 4:    return "3-4"
        return "4+"

    # Breakdown por qualidade
    n3_by_quality: Dict[str, Dict] = {}
    for t in closed_trades:
        qual = str(t.get("s2_quality") or "?").replace("ScalpSignalQuality.", "")
        if qual not in n3_by_quality:
            n3_by_quality[qual] = {"wins": 0, "losses": 0, "pnl_pts": 0.0}
        if _is_win(t): n3_by_quality[qual]["wins"] += 1
        else:          n3_by_quality[qual]["losses"] += 1
        n3_by_quality[qual]["pnl_pts"] += (t.get("pnl_pts") or 0)

    for v in n3_by_quality.values():
        v["pnl_pts"] = round(v["pnl_pts"], 2)
        tot = v["wins"] + v["losses"]
        v["wr_pct"] = round(v["wins"] / tot * 100, 1) if tot > 0 else None

    # Breakdown por d30_state
    n3_by_d30: Dict[str, Dict] = {}
    for t in closed_trades:
        d30 = str(t.get("d30_state") or "?")
        if d30 not in n3_by_d30:
            n3_by_d30[d30] = {"wins": 0, "losses": 0, "pnl_pts": 0.0}
        if _is_win(t): n3_by_d30[d30]["wins"] += 1
        else:          n3_by_d30[d30]["losses"] += 1
        n3_by_d30[d30]["pnl_pts"] += (t.get("pnl_pts") or 0)

    for v in n3_by_d30.values():
        v["pnl_pts"] = round(v["pnl_pts"], 2)
        tot = v["wins"] + v["losses"]
        v["wr_pct"] = round(v["wins"] / tot * 100, 1) if tot > 0 else None

    # Breakdown por score bucket
    n3_by_score: Dict[str, Dict] = {}
    for t in closed_trades:
        bkt = _score_bucket(t.get("zone_score"))
        if bkt not in n3_by_score:
            n3_by_score[bkt] = {"wins": 0, "losses": 0, "pnl_pts": 0.0}
        if _is_win(t): n3_by_score[bkt]["wins"] += 1
        else:          n3_by_score[bkt]["losses"] += 1
        n3_by_score[bkt]["pnl_pts"] += (t.get("pnl_pts") or 0)

    for v in n3_by_score.values():
        v["pnl_pts"] = round(v["pnl_pts"], 2)
        tot = v["wins"] + v["losses"]
        v["wr_pct"] = round(v["wins"] / tot * 100, 1) if tot > 0 else None

    # Lista individual de trades (para tabela no frontend)
    n3_trade_list = []
    for t in all_day_trades:
        ca = t.get("created_at")
        n3_trade_list.append({
            "id":           str(t.get("_id", "")),
            "symbol":       t.get("symbol"),
            "action":       t.get("action"),
            "state":        t.get("state"),
            "session":      t.get("session_label"),
            "zone_type":    t.get("zone_type"),
            "quality":      str(t.get("s2_quality") or "").replace("ScalpSignalQuality.", ""),
            "regime":       str(t.get("s1_regime") or "").replace("ScalpRegime.", ""),
            "zone_score":   round(t["zone_score"], 2) if t.get("zone_score") is not None else None,
            "d30_state":    t.get("d30_state"),
            "vwap_zone":    t.get("vwap_zone"),
            "entry_price":  t.get("entry_price"),
            "sl_price":     t.get("stop_loss_price"),
            "tp_price":     t.get("take_profit_price"),
            "pnl_pts":      t.get("pnl_pts"),
            "exit_reason":  t.get("exit_reason"),
            "created_at":   ca.isoformat() if isinstance(ca, datetime) else str(ca or ""),
            "closed_at":    (t["closed_at"].isoformat() if isinstance(t.get("closed_at"), datetime)
                             else str(t.get("closed_at") or "")),
            "outcome":      ("WIN" if _is_win(t) else "LOSS")
                             if t.get("state") not in (None, "OPEN", "PENDING", "WATCHING") else "OPEN",
        })
    n3_trade_list.sort(key=lambda x: x["created_at"])

    # ── 3. ACTIVE_SIGNAL snapshots (para dedup e benchmark) ──────────────────
    active_snaps = await col.find(
        {**base_q, "scalp_status": "ACTIVE_SIGNAL"},
        {"recorded_at": 1, "symbol": 1, "last_price": 1, "session_label": 1,
         "s1_regime": 1, "s2_quality": 1, "zones": 1, "s3": 1, "indicators": 1}
    ).sort("recorded_at", 1).to_list(None)

    # Dedup: mesma zona + símbolo + dentro de 5 minutos = 1 evento
    n2_events: List[Dict] = []
    for s in active_snaps:
        az = (s.get("zones") or {}).get("active_zone") or {}
        zt    = az.get("type", "?") or az.get("zone_type", "?")
        level = az.get("level", 0)
        sym   = s.get("symbol", "")
        key   = f"{sym}_{zt}_{int(level)}"
        ts    = s.get("recorded_at")
        if not isinstance(ts, datetime):
            try: ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except: continue

        is_dup = False
        for ev in reversed(n2_events):
            if ev["key"] == key:
                diff = (ts - ev["ts"]).total_seconds()
                if diff < 300:
                    is_dup = True
                break
        if not is_dup:
            s3 = s.get("s3") or {}
            n2_events.append({
                "key":      key,
                "ts":       ts,
                "symbol":   sym,
                "zone_type": zt,
                "zone_level": level,
                "zone_label": az.get("label", ""),
                "direction": az.get("direction", ""),
                "session":   s.get("session_label", ""),
                "regime":    str(s.get("s1_regime") or "").replace("ScalpRegime.", ""),
                "quality":   str(s.get("s2_quality") or "").replace("ScalpSignalQuality.", ""),
                "entry":     s3.get("entry_price"),
                "sl":        s3.get("stop_loss_price"),
                "tp":        s3.get("take_profit_price"),
                "action":    s3.get("action", ""),
                "price_at_signal": s.get("last_price"),
                "benchmark_outcome": None,
                "benchmark_pnl_pts": None,
            })

    n2_dedup = len(n2_events)

    # ── 4. Benchmark hipotético ──────────────────────────────────────────────
    # Para cada N2 evento, busca snapshots seguintes (até 30min) e verifica TP/SL
    bench_resolved = bench_win = bench_loss = bench_pending = 0
    bench_pnl_pts = 0.0

    # Buscar todos os snapshots do dia por símbolo (para lookup de preço)
    price_snaps_by_sym: Dict[str, List[Dict]] = {}
    all_price_raw = await col.find(
        base_q,
        {"recorded_at": 1, "symbol": 1, "last_price": 1}
    ).sort("recorded_at", 1).to_list(None)
    for p in all_price_raw:
        sym = p.get("symbol", "")
        if sym not in price_snaps_by_sym:
            price_snaps_by_sym[sym] = []
        price_snaps_by_sym[sym].append(p)

    for ev in n2_events:
        entry = ev.get("entry")
        sl    = ev.get("sl")
        tp    = ev.get("tp")
        sym   = ev.get("symbol", "")
        ev_ts = ev["ts"]
        action = (ev.get("action") or "").lower()

        if not (entry and sl and tp):
            bench_pending += 1
            ev["benchmark_outcome"] = "SEM_DADOS"
            continue

        is_long = action in ("buy", "long") or tp > entry
        window_end = ev_ts + timedelta(minutes=30)
        outcome = None
        pnl_ev  = None

        for p in price_snaps_by_sym.get(sym, []):
            p_ts = p.get("recorded_at")
            if not isinstance(p_ts, datetime):
                try: p_ts = datetime.fromisoformat(str(p_ts).replace("Z", "+00:00"))
                except: continue
            if p_ts <= ev_ts: continue
            if p_ts > window_end: break
            price = p.get("last_price", 0) or 0
            if price <= 0: continue
            if is_long:
                if price >= tp:
                    outcome = "TARGET"
                    pnl_ev = round(tp - entry, 2)
                    break
                elif price <= sl:
                    outcome = "STOP"
                    pnl_ev = round(sl - entry, 2)
                    break
            else:
                if price <= tp:
                    outcome = "TARGET"
                    pnl_ev = round(entry - tp, 2)
                    break
                elif price >= sl:
                    outcome = "STOP"
                    pnl_ev = round(entry - sl, 2)
                    break

        if outcome is None:
            bench_pending += 1
            ev["benchmark_outcome"] = "PENDENTE"
        else:
            bench_resolved += 1
            ev["benchmark_outcome"] = outcome
            ev["benchmark_pnl_pts"] = pnl_ev
            bench_pnl_pts += pnl_ev or 0
            if outcome == "TARGET":
                bench_win += 1
            else:
                bench_loss += 1

    bench_wr = round(bench_win / bench_resolved * 100, 1) if bench_resolved > 0 else None

    # ── 5. Block reasons (NO_SIGNAL) ─────────────────────────────────────────
    reasons_raw: Dict[str, int] = {}
    async for s in col.find(
        {**base_q, "scalp_status": "NO_SIGNAL"},
        {"s2.block_reasons": 1}
    ):
        for r in (s.get("s2") or {}).get("block_reasons") or []:
            reasons_raw[r] = reasons_raw.get(r, 0) + 1

    # Agrupar: separar por score (contém "pts") vs gate real
    score_reasons: Dict[str, int] = {}
    gate_reasons:  Dict[str, int] = {}
    for r, cnt in reasons_raw.items():
        if " pts" in r or "pts" in r:
            score_reasons[r] = cnt
        else:
            gate_reasons[r] = cnt

    top_score_reasons = sorted(score_reasons.items(), key=lambda x: -x[1])[:12]
    top_gate_reasons  = sorted(gate_reasons.items(),  key=lambda x: -x[1])[:12]

    # ── 6. Zona × Sessão (N2 events) ─────────────────────────────────────────
    zone_sess_matrix: Dict[str, Dict[str, int]] = {}
    for ev in n2_events:
        zt   = ev.get("zone_type", "?")
        sess = ev.get("session", "?")
        if zt not in zone_sess_matrix:
            zone_sess_matrix[zt] = {}
        zone_sess_matrix[zt][sess] = zone_sess_matrix[zt].get(sess, 0) + 1

    # ── 7. N1 distribuição (NO_SIGNAL + ACTIVE_SIGNAL por regime/sessão) ─────
    n1_by_regime: Dict[str, int] = {}
    n1_by_session: Dict[str, int] = {}
    async for s in col.find(
        {**base_q, "scalp_status": {"$in": ["NO_SIGNAL", "ACTIVE_SIGNAL", "D30_BLOCKED", "BLOCKED"]}},
        {"s1_regime": 1, "session_label": 1}
    ):
        reg  = str(s.get("s1_regime") or "?").replace("ScalpRegime.", "")
        sess = str(s.get("session_label") or "?")
        n1_by_regime[reg]   = n1_by_regime.get(reg, 0) + 1
        n1_by_session[sess] = n1_by_session.get(sess, 0) + 1

    # ── 8. D30 distribution nos snapshots ─────────────────────────────────────
    d30_clear = await col.count_documents({**base_q, "indicators.d30_state": "OK"})
    d30_risk  = await col.count_documents({**base_q, "indicators.d30_state": "RISK"})
    d30_block_cnt = await col.count_documents({**base_q, "indicators.d30_state": "BLOCKED"})

    # ── 9. Filtros R1-R4: quantos ACTIVE_SIGNAL seriam bloqueados ────────────
    # Nota: conta snapshots brutos (30s loop) — não eventos únicos dedup.
    # Comparar com n2_dedup para escala relativa.
    r1_count = await col.count_documents({
        **base_q,
        "scalp_status": "ACTIVE_SIGNAL",
        "zone_quality": "MODERATE",
        "session_label": "RTH_MID",
    })
    r2_count = await col.count_documents({
        **base_q,
        "scalp_status": "ACTIVE_SIGNAL",
        "s1_regime": {"$regex": "BEARISH_FLOW"},
        "session_label": {"$in": ["RTH_MID", "RTH_CLOSE"]},
        "$nor": [{"zone_quality": "MODERATE", "session_label": "RTH_MID"}],
    })
    r3_count = await col.count_documents({
        **base_q,
        "scalp_status": "ACTIVE_SIGNAL",
        "zones.active_zone.type": {"$regex": "GAMMA_PUT_WALL"},
    })
    r4_count = await col.count_documents({
        **base_q,
        "scalp_status": "ACTIVE_SIGNAL",
        "zones.active_zone.type": {"$regex": "VWAP_PULLBACK"},
    })
    r_filters_total = r1_count + r2_count + r3_count + r4_count

    # ── 10. Track B Observer — funil do dia ─────────────────────────────────
    tb_col   = _database["pb_state_log"]
    tb_base_q: Dict[str, Any] = {"ts": {"$gte": day_start, "$lt": day_end}}
    if symbol:
        tb_base_q["symbol"] = symbol.upper()

    tb_touched  = await tb_col.count_documents({**tb_base_q, "event": "TOUCHED"})
    tb_pullback = await tb_col.count_documents({**tb_base_q, "event": "PULLBACK"})
    tb_return   = await tb_col.count_documents({**tb_base_q, "event": "RETURN"})

    # Detalhes dos RETURN events do dia
    _tb_ret_proj = {
        "ts": 1, "symbol": 1, "zone_type": 1, "direction": 1, "session": 1,
        "pullback_distance_pts": 1, "return_pct": 1, "would_have_triggered": 1,
    }
    tb_return_docs = await tb_col.find(
        {**tb_base_q, "event": "RETURN"}, _tb_ret_proj
    ).sort("ts", 1).to_list(None)

    tb_would_trigger  = sum(1 for d in tb_return_docs if d.get("would_have_triggered"))
    tb_avg_pullback   = (
        round(sum(d.get("pullback_distance_pts") or 0 for d in tb_return_docs) / len(tb_return_docs), 2)
        if tb_return_docs else None
    )
    tb_avg_return_pct = (
        round(sum(d.get("return_pct") or 0 for d in tb_return_docs) / len(tb_return_docs), 3)
        if tb_return_docs else None
    )

    # Readiness acumulada (all-time, não filtrada por dia)
    tb_return_all = await tb_col.count_documents({"event": "RETURN"})
    _tb_dates_agg = await tb_col.aggregate([
        {"$match": {"event": "RETURN"}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}}}},
        {"$count": "n"},
    ]).to_list(length=1)
    tb_distinct_dates = _tb_dates_agg[0]["n"] if _tb_dates_agg else 0
    tb_readiness_met  = (tb_return_all >= 10 and tb_distinct_dates >= 2)

    # Trades Track B hoje (campo events[].source = "track_b")
    tb_trades_today = await _database["scalp_trades"].count_documents({
        "created_at": {"$gte": day_str, "$lt": day_str_end},
        "events.source": "track_b",
    })

    # ── 11. Signal list formatada ─────────────────────────────────────────────
    signal_list = [
        {
            "ts":       ev["ts"].isoformat() if isinstance(ev["ts"], datetime) else str(ev["ts"]),
            "symbol":   ev["symbol"],
            "zone_type": ev["zone_type"],
            "zone_label": ev["zone_label"],
            "zone_level": ev.get("zone_level"),
            "direction": ev["direction"],
            "session":  ev["session"],
            "regime":   ev["regime"],
            "quality":  ev["quality"],
            "entry":    ev["entry"],
            "sl":       ev["sl"],
            "tp":       ev["tp"],
            "price_at_signal": ev["price_at_signal"],
            "benchmark_outcome": ev["benchmark_outcome"],
            "benchmark_pnl_pts": ev["benchmark_pnl_pts"],
        }
        for ev in n2_events
    ]

    # ── Response ──────────────────────────────────────────────────────────────
    return {
        "date":            day_str,
        "symbol_filter":   symbol,
        "funnel": {
            "n1_total":    n1,
            "n2_raw":      active,
            "n2_dedup":    n2_dedup,
            "d30_blocked": d30_bl,
            "other_blocked": blocked,
            "n3_trades":   n3,
            "n1_to_n2_pct": round(active / n1 * 100, 1) if n1 > 0 else 0,
            "n2_to_n3_pct": round(n3 / n2_dedup * 100, 1) if n2_dedup > 0 else 0,
        },
        "n3_stats": {
            "total":      n3,
            "closed":     len(closed_trades),
            "wins":       wins_t,
            "losses":     losses_t,
            "wr_pct":     round(wins_t / len(closed_trades) * 100, 1) if closed_trades else None,
            "pnl_pts":    pnl_pts_total,
            "pnl_usd":    pnl_usd_total,
        },
        "benchmark": {
            "resolved":   bench_resolved,
            "pending":    bench_pending,
            "wins":       bench_win,
            "losses":     bench_loss,
            "wr_pct":     bench_wr,
            "pnl_pts":    round(bench_pnl_pts, 2),
        },
        "block_reasons": {
            "score_factors": [{"reason": r, "count": c} for r, c in top_score_reasons],
            "gate_blocks":   [{"reason": r, "count": c} for r, c in top_gate_reasons],
        },
        "d30": {
            "clear":   d30_clear,
            "risk":    d30_risk,
            "blocked": d30_block_cnt,
        },
        "zone_session_matrix": zone_sess_matrix,
        "n1_by_regime":  dict(sorted(n1_by_regime.items(),  key=lambda x: -x[1])),
        "n1_by_session": dict(sorted(n1_by_session.items(), key=lambda x: -x[1])),
        "signals":       signal_list,
        "n3_context": {
            "by_quality":  n3_by_quality,
            "by_d30":      n3_by_d30,
            "by_score":    n3_by_score,
            "trades":      n3_trade_list,
        },
        "r_filters": {
            "r1_moderate_rth_mid":       r1_count,
            "r2_bearish_rth_mid_close":  r2_count,
            "r3_gamma_put_wall":         r3_count,
            "r4_vwap_pullback":          r4_count,
            "total":                     r_filters_total,
            "note": "Snapshots ACTIVE_SIGNAL que os filtros R1-R4 bloqueiam (brutos, ~30s loop)",
        },
        "track_b": {
            "today": {
                "touched":          tb_touched,
                "pullback":         tb_pullback,
                "return":           tb_return,
                "would_trigger":    tb_would_trigger,
                "avg_pullback_pts": tb_avg_pullback,
                "avg_return_pct":   tb_avg_return_pct,
                "trades_executed":  tb_trades_today,
            },
            "readiness": {
                "n_return_all_time": tb_return_all,
                "distinct_dates":    tb_distinct_dates,
                "readiness_met":     tb_readiness_met,
                "target_returns":    10,
                "target_dates":      2,
            },
            "return_events": [
                {
                    "ts":           d["ts"].isoformat() if isinstance(d.get("ts"), datetime) else None,
                    "symbol":       d.get("symbol"),
                    "zone_type":    d.get("zone_type"),
                    "direction":    d.get("direction"),
                    "session":      d.get("session"),
                    "pullback_pts": d.get("pullback_distance_pts"),
                    "return_pct":   d.get("return_pct"),
                    "would_trigger": d.get("would_have_triggered"),
                }
                for d in tb_return_docs
            ],
        },
    }


@scalp_router.delete("/funnel/clear")
async def clear_signal_log(confirm: bool = Query(False)):
    """Apaga todos os registos de scalp_signal_log (requer confirm=true)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Passe confirm=true para confirmar o clear.")
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    result = await _database["scalp_signal_log"].delete_many({})
    return {"deleted": result.deleted_count}


# ══════════════════════════════════════════════════════════════════════════════
# ZONE OUTCOMES — histórico de outcomes por zona × nível
# ══════════════════════════════════════════════════════════════════════════════

@scalp_router.get("/zone-outcomes")
async def zone_outcomes(
    symbol:  Optional[str] = Query(None),
    days:    int           = Query(30,  ge=1,  le=90),
    min_n:   int           = Query(1,   ge=1,  le=20),
    window:  int           = Query(30,  ge=5,  le=120, description="Janela benchmark em minutos"),
):
    """
    Auditoria de funil por zona × nível — 4 camadas:
      N2  : ACTIVE_SIGNAL (passaram S2) — outcome benchmark hipotético
      D30 : D30_BLOCKED (bloqueados pelo gate D30 pós-S2) — outcome benchmark hipotético
      S2  : NO_SIGNAL com zona activa (bloqueados antes de S2) — contagem + razões
      N3  : scalp_trades executados (por zone_type; outcome real)

    Parâmetros:
      - days   : janela histórica em dias (default 30)
      - min_n  : filtrar grupos com n2+d30 < min_n
      - window : janela de simulação benchmark em minutos (default 30)
    """
    import bisect
    from datetime import timedelta
    from collections import Counter

    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")

    now        = datetime.now(timezone.utc)
    date_start = now - timedelta(days=days)

    col    = _database["scalp_snapshots"]
    base_q: Dict[str, Any] = {"recorded_at": {"$gte": date_start}}
    if symbol:
        base_q["symbol"] = symbol.upper()

    # ── Projecção comum para snapshots ────────────────────────────────────────
    SNAP_PROJ = {
        "recorded_at": 1, "symbol": 1, "last_price": 1,
        "session_label": 1, "s1_regime": 1, "s2_quality": 1,
        "zones": 1, "s3": 1, "s2": 1, "indicators": 1,
    }

    # ── 1. Carregar camadas de snapshots em paralelo ──────────────────────────
    # D30: bloqueados pela gate de deslocamento 30min (indicators.d30_state=="BLOCKED")
    # Podem ter scalp_status variado (NO_ZONE, NO_SIGNAL, D30_BLOCKED)
    # S2 fail: NO_SIGNAL/BLOCKED que NÃO são D30 e têm zona activa
    n2_raw, d30_raw, s2fail_raw, d30_total_cnt = await asyncio.gather(
        col.find({**base_q, "scalp_status": "ACTIVE_SIGNAL"}, SNAP_PROJ
                 ).sort("recorded_at", 1).to_list(None),
        col.find({**base_q, "indicators.d30_state": "BLOCKED"}, SNAP_PROJ
                 ).sort("recorded_at", 1).to_list(None),
        col.find({**base_q,
                  "scalp_status": {"$in": ["NO_SIGNAL", "BLOCKED"]},
                  "indicators.d30_state": {"$ne": "BLOCKED"}}, SNAP_PROJ
                 ).sort("recorded_at", 1).to_list(None),
        col.count_documents({**base_q, "indicators.d30_state": "BLOCKED"}),
    )

    # ── 2. Preços indexados para benchmark (busca binária) ────────────────────
    all_prices_raw = await col.find(
        base_q,
        {"recorded_at": 1, "symbol": 1, "last_price": 1},
    ).sort("recorded_at", 1).to_list(None)

    # Indexar: prices_idx[sym] = lista de (ts, price) ordenada por ts
    prices_idx: Dict[str, List] = {}
    for p in all_prices_raw:
        sym_p = p.get("symbol", "")
        p_ts  = p.get("recorded_at")
        price = p.get("last_price", 0) or 0
        if not isinstance(p_ts, datetime):
            try:    p_ts = datetime.fromisoformat(str(p_ts).replace("Z", "+00:00"))
            except: continue
        if price > 0:
            prices_idx.setdefault(sym_p, []).append((p_ts, price))

    # ── 3. N3 trades reais ────────────────────────────────────────────────────
    # Trades usam campo "created_at" (não "opened_at")
    # Filtrar apenas trades fechados com pnl_pts definido (state=CLOSED)
    trades_col  = _database["scalp_trades"]
    trades_q: Dict[str, Any] = {
        "created_at": {"$gte": date_start.isoformat()},
        "state": "CLOSED",
    }
    if symbol:
        trades_q["symbol"] = symbol.upper()
    trades_raw = await trades_col.find(
        trades_q,
        {"symbol": 1, "zone_type": 1, "entry_price": 1,
         "pnl_pts": 1, "exit_reason": 1, "s1_regime": 1, "s2_quality": 1,
         "created_at": 1},
    ).to_list(None)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _parse_ts(v):
        if isinstance(v, datetime): return v
        try: return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except: return None

    def _bucket(level) -> float:
        if not level: return 0.0
        tick = 0.25
        return round(round(float(level) / tick) * tick, 2)

    def _clean_status(s):
        return str(s or "").replace("ScalpRegime.", "").replace("ScalpSignalQuality.", "")

    window_td = timedelta(minutes=window)

    def _benchmark(ev_ts, sym, entry, sl, tp, action) -> tuple:
        """Retorna (outcome, pnl_pts) usando busca binária no índice de preços."""
        if not (entry and sl and tp):
            return "NO_DATA", None
        is_long    = (action or "").lower() in ("buy", "long") or (tp > entry)
        window_end = ev_ts + window_td
        prices     = prices_idx.get(sym, [])
        if not prices:
            return "PENDENTE", None
        # Bisect: encontrar primeiro index com ts > ev_ts
        lo = bisect.bisect_right(prices, (ev_ts, float("inf")))
        for i in range(lo, len(prices)):
            p_ts, price = prices[i]
            if p_ts > window_end: break
            if is_long:
                if price >= tp: return "TARGET", round(tp - entry, 2)
                if price <= sl: return "STOP",   round(sl - entry, 2)
            else:
                if price <= tp: return "TARGET", round(entry - tp, 2)
                if price >= sl: return "STOP",   round(entry - sl, 2)
        return "PENDENTE", None

    def _extract_snap(s) -> Optional[Dict]:
        """Extrai campos relevantes de um snapshot. None se sem zona activa."""
        az  = (s.get("zones") or {}).get("active_zone") or {}
        zt  = az.get("type") or az.get("zone_type") or ""
        if not zt:
            return None
        level = az.get("level") or 0
        sym   = s.get("symbol", "")
        ts    = _parse_ts(s.get("recorded_at"))
        if ts is None: return None
        s3    = s.get("s3") or {}
        s2d   = s.get("s2") or {}
        ind   = s.get("indicators") or {}
        return {
            "ts":        ts,
            "symbol":    sym,
            "zone_type": zt,
            "zone_level":level,
            "direction": az.get("direction", ""),
            "session":   s.get("session_label", ""),
            "regime":    _clean_status(s.get("s1_regime")),
            "quality":   _clean_status(s.get("s2_quality")),
            "entry":     s3.get("entry_price"),
            "sl":        s3.get("stop_loss_price"),
            "tp":        s3.get("take_profit_price"),
            "action":    s3.get("action", ""),
            "block_reasons": s2d.get("block_reasons", []) or [],
            "d30_state":     ind.get("d30_state"),
            "disp_30m":      ind.get("disp_30m"),
        }

    def _dedup_layer(raw_snaps: List) -> List[Dict]:
        """Dedup 5-min por (sym, zone_type, level). Retorna lista de eventos."""
        events: List[Dict] = []
        last_ts: Dict[str, datetime] = {}
        for s in raw_snaps:
            ev = _extract_snap(s)
            if ev is None: continue
            key = f"{ev['symbol']}_{ev['zone_type']}_{int(ev['zone_level'])}"
            prev = last_ts.get(key)
            if prev and (ev["ts"] - prev).total_seconds() < 300:
                continue
            last_ts[key] = ev["ts"]
            events.append(ev)
        return events

    # ── 4. Processar cada camada ──────────────────────────────────────────────
    n2_events  = _dedup_layer(n2_raw)
    d30_events = _dedup_layer(d30_raw)
    s2_events  = _dedup_layer(s2fail_raw)

    # Aplicar benchmark a N2 e D30
    for ev in n2_events + d30_events:
        ev["bm_outcome"], ev["bm_pnl"] = _benchmark(
            ev["ts"], ev["symbol"],
            ev.get("entry"), ev.get("sl"), ev.get("tp"), ev.get("action"),
        )

    # D30: construir motivo de bloqueio legível
    for ev in d30_events:
        parts = []
        if ev.get("disp_30m") is not None:
            parts.append(f"disp30={ev['disp_30m']:.1f}pts")
        if ev.get("d30_state"):
            parts.append(ev["d30_state"])
        ev["block_reasons"] = parts if parts else ["D30_BLOCKED"]

    # ── 5. Agrupar por zona × nível × símbolo ────────────────────────────────
    def _layer_stats(events: List[Dict]) -> Dict:
        n_tot = n_tgt = n_stp = n_pnd = 0
        sum_pnl = 0.0
        all_reasons: List[str] = []
        evt_list = []
        for ev in events:
            n_tot += 1
            bm = ev.get("bm_outcome")
            if   bm == "TARGET":  n_tgt += 1; sum_pnl += ev.get("bm_pnl") or 0
            elif bm == "STOP":    n_stp += 1; sum_pnl += ev.get("bm_pnl") or 0
            elif bm == "PENDENTE":n_pnd += 1
            all_reasons.extend(ev.get("block_reasons") or [])
            evt_list.append({
                "ts":      ev["ts"].isoformat(),
                "session": ev.get("session"),
                "regime":  ev.get("regime"),
                "quality": ev.get("quality"),
                "entry":   ev.get("entry"),
                "sl":      ev.get("sl"),
                "tp":      ev.get("tp"),
                "outcome": bm,
                "pnl_pts": ev.get("bm_pnl"),
                "reasons": ev.get("block_reasons", []),
            })
        resolved = n_tgt + n_stp
        top_reasons = [{"reason": r, "count": c}
                       for r, c in Counter(all_reasons).most_common(5)]
        return {
            "n":            n_tot,
            "n_target":     n_tgt,
            "n_stop":       n_stp,
            "n_pending":    n_pnd,
            "wr_pct":       round(n_tgt / resolved * 100, 1) if resolved > 0 else None,
            "avg_pnl_pts":  round(sum_pnl / resolved, 2)     if resolved > 0 else None,
            "top_reasons":  top_reasons,
            "events":       evt_list,
        }

    def _s2fail_stats(events: List[Dict]) -> Dict:
        all_reasons: List[str] = []
        for ev in events:
            all_reasons.extend(ev.get("block_reasons") or [])
        return {
            "n":           len(events),
            "top_reasons": [{"reason": r, "count": c}
                            for r, c in Counter(all_reasons).most_common(8)],
        }

    # Map: group_key → {"n2_events":[], "d30_events":[], "s2_events":[]}
    grp_n2:  Dict[str, List] = {}
    grp_d30: Dict[str, List] = {}
    grp_s2:  Dict[str, List] = {}

    def _gk(ev): return f"{ev['symbol']}|{ev['zone_type']}|{_bucket(ev['zone_level'])}"

    for ev in n2_events:
        grp_n2.setdefault(_gk(ev), []).append(ev)
    for ev in d30_events:
        grp_d30.setdefault(_gk(ev), []).append(ev)
    for ev in s2_events:
        grp_s2.setdefault(_gk(ev), []).append(ev)

    all_keys = set(grp_n2) | set(grp_d30) | set(grp_s2)

    # ── 6. N3 por zone_type (entry_price como proxy do nível) ────────────────
    n3_by_type: Dict[str, Dict] = {}
    for t in trades_raw:
        sym_t = t.get("symbol", "")
        zt_t  = t.get("zone_type") or ""
        if not zt_t: continue
        gkt   = f"{sym_t}|{zt_t}"
        pnl   = t.get("pnl_pts") or 0
        exit_ = t.get("exit_reason", "")
        win   = (pnl > 0) or ("TARGET" in str(exit_).upper())
        if gkt not in n3_by_type:
            n3_by_type[gkt] = {
                "symbol": sym_t, "zone_type": zt_t,
                "n": 0, "wins": 0, "losses": 0,
                "sum_pnl": 0.0, "events": [],
            }
        g3 = n3_by_type[gkt]
        g3["n"]      += 1
        g3["wins"]   += 1 if win else 0
        g3["losses"] += 0 if win else 1
        g3["sum_pnl"]+= pnl
        t_ts_raw = t.get("created_at") or t.get("opened_at")
        t_ts     = _parse_ts(t_ts_raw)
        g3["events"].append({
            "ts":       t_ts.isoformat() if t_ts else None,
            "entry":    t.get("entry_price"),
            "pnl_pts":  pnl,
            "exit":     exit_,
            "regime":   _clean_status(t.get("s1_regime")),
            "quality":  _clean_status(t.get("s2_quality")),
        })

    n3_list = []
    for g3 in n3_by_type.values():
        n3   = g3["n"]
        wins = g3["wins"]
        n3_list.append({
            "symbol":     g3["symbol"],
            "zone_type":  g3["zone_type"],
            "n":          n3,
            "wins":       wins,
            "losses":     g3["losses"],
            "wr_pct":     round(wins / n3 * 100, 1) if n3 > 0 else None,
            "pnl_pts":    round(g3["sum_pnl"], 2),
            "avg_pnl_pts":round(g3["sum_pnl"] / n3, 2) if n3 > 0 else None,
            "events":     g3["events"],
        })
    n3_list.sort(key=lambda x: -x["n"])

    # ── 7. Montar resultado por grupo ─────────────────────────────────────────
    result = []
    for gk in all_keys:
        parts      = gk.split("|")
        sym        = parts[0]
        zt         = parts[1]
        lvl        = float(parts[2])
        n2_evs     = grp_n2.get(gk,  [])
        d30_evs    = grp_d30.get(gk, [])
        s2_evs     = grp_s2.get(gk,  [])

        n2_stats   = _layer_stats(n2_evs)
        d30_stats  = _layer_stats(d30_evs)
        s2_stats   = _s2fail_stats(s2_evs)

        # Filtro: pelo menos min_n entre N2+D30
        if (n2_stats["n"] + d30_stats["n"]) < min_n:
            continue

        direction  = ""
        last_seen  = ""
        for ev in sorted(n2_evs + d30_evs + s2_evs, key=lambda e: e["ts"], reverse=True)[:1]:
            direction  = ev.get("direction", "")
            last_seen  = ev["ts"].isoformat()

        # Flag de alerta: D30 filtrou algo com WR melhor do que N2
        d30_wr = d30_stats.get("wr_pct")
        n2_wr  = n2_stats.get("wr_pct")
        d30_overfiltered = (
            d30_evs and d30_wr is not None and n2_wr is not None
            and d30_wr > n2_wr + 10
        )

        result.append({
            "symbol":           sym,
            "zone_type":        zt,
            "level":            lvl,
            "direction":        direction,
            "last_seen":        last_seen,
            "d30_overfiltered": d30_overfiltered,
            "n2":               n2_stats,
            "d30":              d30_stats,
            "s2_fail":          s2_stats,
        })

    result.sort(key=lambda x: (-(x["n2"]["n"] + x["d30"]["n"]), x["zone_type"], x["level"]))

    # Totais gerais
    # d30_total = contagem de TODOS os snapshots com d30_state=BLOCKED (gate de sessão)
    # d30 por zona só inclui os que têm zona activa (pequena fracção — D30 bloqueia antes da zona)
    totals = {
        "n2_total":      len(n2_events),
        "d30_total":     d30_total_cnt,
        "d30_with_zone": len(d30_events),
        "s2fail_total":  len(s2_events),
        "n3_total":      len(trades_raw),
    }

    return {
        "days":          days,
        "window_min":    window,
        "symbol_filter": symbol,
        "totals":        totals,
        "n_groups":      len(result),
        "outcomes":      result,
        "n3_by_zone":    n3_list,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PARAM AUDIT — sensibilidade de parâmetros × score × outcomes
# ══════════════════════════════════════════════════════════════════════════════

@scalp_router.get("/param-audit")
async def param_audit(
    symbol:    Optional[str] = Query(None),
    days:      int           = Query(30,  ge=1,  le=90),
    zone_type: Optional[str] = Query(None, description="Filtrar por zone_type específico"),
):
    """
    Auditoria de sensibilidade parametros×outcomes.

    Para cada zone_type com zona activa, retorna:
      - Distribuição de scores por bucket (0.5 pts) × scalp_status
      - Thresholds activos (MODERATE / STRONG / BASE_FLOW_GATE) extraídos do snapshot mais recente
      - Contagens: n_active (ACTIVE_SIGNAL) vs n_blocked (NO_SIGNAL + BLOCKED)
      - Score médio separado por passados e bloqueados

    Permite responder: "Se baixar MODERATE de X para Y, quantos sinais adicionais? Com que score médio?"
    """
    from datetime import timedelta

    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")

    now        = datetime.now(timezone.utc)
    date_start = now - timedelta(days=days)

    col = _database["scalp_snapshots"]

    match_q: Dict[str, Any] = {
        "recorded_at":      {"$gte": date_start},
        "mode":             "ZONES",
        "zones.active_zone": {"$exists": True, "$ne": None},
    }
    if symbol:
        match_q["symbol"] = symbol.upper()
    if zone_type:
        match_q["zones.active_zone.type"] = zone_type.upper()

    # ── Thresholds actuais — snapshot mais recente com active_params ───────────
    threshold_q: Dict[str, Any] = {
        "zones.active_params": {"$exists": True, "$ne": None},
    }
    if symbol:
        threshold_q["symbol"] = symbol.upper()
    latest_with_params = await col.find_one(
        threshold_q,
        {"zones.active_params": 1},
        sort=[("recorded_at", -1)],
    )
    active_params_latest: Dict[str, Any] = {}
    if latest_with_params:
        active_params_latest = latest_with_params.get("zones", {}).get("active_params", {})

    # ── Pipeline: score buckets por zone_type × status ─────────────────────────
    pipeline = [
        {"$match": match_q},
        # Resolve score: top-level zone_score (novo) ou nested (legado)
        {"$addFields": {
            "_score": {
                "$ifNull": [
                    "$zone_score",
                    "$zones.score_breakdown.total_score",
                ]
            }
        }},
        # Bucket 0.5pts: arredonda score para múltiplo de 0.5 mais próximo
        {"$addFields": {
            "_bucket": {
                "$cond": {
                    "if":   {"$ne": ["$_score", None]},
                    "then": {
                        "$multiply": [
                            {"$round": [{"$multiply": ["$_score", 2]}, 0]},
                            0.5,
                        ]
                    },
                    "else": None,
                }
            }
        }},
        {"$group": {
            "_id": {
                "zone_type": "$zones.active_zone.type",
                "status":    "$scalp_status",
                "bucket":    "$_bucket",
            },
            "count":     {"$sum": 1},
            "score_sum": {"$sum": {"$ifNull": ["$_score", 0]}},
        }},
        {"$sort": {"_id.zone_type": 1, "_id.bucket": 1}},
    ]

    rows = await col.aggregate(pipeline).to_list(None)

    # ── Restructura: zona → {status→{bucket→count}} ─────────────────────────
    from collections import defaultdict
    zone_map: Dict[str, Dict] = defaultdict(lambda: {
        "active": defaultdict(lambda: {"count": 0, "score_sum": 0.0}),
        "blocked": defaultdict(lambda: {"count": 0, "score_sum": 0.0}),
        "no_score": {"active": 0, "blocked": 0},
    })

    _active_statuses  = {"ACTIVE_SIGNAL"}
    _blocked_statuses = {"NO_SIGNAL", "BLOCKED", "HARD_STOP", "D30_BLOCKED", "MARKET_CLOSED"}

    for row in rows:
        zt     = row["_id"].get("zone_type") or "UNKNOWN"
        status = row["_id"].get("status") or "UNKNOWN"
        bucket = row["_id"].get("bucket")
        cnt    = row["count"]
        ssum   = row.get("score_sum", 0.0)

        bucket_key  = "active" if status in _active_statuses else "blocked"
        if bucket is None:
            zone_map[zt]["no_score"][bucket_key] += cnt
        else:
            zone_map[zt][bucket_key][bucket]["count"]     += cnt
            zone_map[zt][bucket_key][bucket]["score_sum"] += ssum

    # ── Formata resposta ─────────────────────────────────────────────────────
    zones_out = []
    for zt, zm in sorted(zone_map.items()):
        active_data  = zm["active"]
        blocked_data = zm["blocked"]

        # União de todos os buckets
        all_buckets = sorted(set(list(active_data.keys()) + list(blocked_data.keys())))

        buckets_list = []
        for b in all_buckets:
            a_cnt  = active_data[b]["count"]
            bl_cnt = blocked_data[b]["count"]
            total  = a_cnt + bl_cnt
            buckets_list.append({
                "bucket":    round(float(b), 1),
                "n_active":  a_cnt,
                "n_blocked": bl_cnt,
                "n_total":   total,
            })

        n_active_total  = sum(d["count"] for d in active_data.values())
        n_blocked_total = sum(d["count"] for d in blocked_data.values())

        # Score médio por grupo
        active_sum  = sum(d["score_sum"] for d in active_data.values())
        blocked_sum = sum(d["score_sum"] for d in blocked_data.values())
        avg_active  = round(active_sum  / n_active_total,  2) if n_active_total  else None
        avg_blocked = round(blocked_sum / n_blocked_total, 2) if n_blocked_total else None

        # Sinais bloqueados com score ≥ MODERATE threshold (oportunidades perdidas estimadas)
        mod_thresh = float(active_params_latest.get("score_moderate", 2.5))
        str_thresh = float(active_params_latest.get("score_strong", 4.0))
        marginal_blocked = sum(
            d["count"]
            for bucket_val, d in blocked_data.items()
            if bucket_val is not None and float(bucket_val) >= mod_thresh
        )

        zones_out.append({
            "zone_type":       zt,
            "n_total":         n_active_total + n_blocked_total,
            "n_active":        n_active_total,
            "n_blocked":       n_blocked_total,
            "n_no_score":      zm["no_score"],
            "avg_score_active":  avg_active,
            "avg_score_blocked": avg_blocked,
            "marginal_blocked":  marginal_blocked,  # bloqueados ≥ MODERATE (com score)
            "score_buckets":   buckets_list,
        })

    return {
        "days":      days,
        "symbol":    symbol.upper() if symbol else "ALL",
        "zone_type": zone_type,
        "thresholds": {
            "score_moderate":  active_params_latest.get("score_moderate", 2.5),
            "score_strong":    active_params_latest.get("score_strong",   4.0),
            "base_flow_gate":  active_params_latest.get("base_flow_gate", 1.2),
            "ofi_slow_fade":   active_params_latest.get("ofi_slow_fade",  0.55),
            "d30_threshold":   active_params_latest.get("d30_threshold",  20.0),
        },
        "zones": zones_out,
        "total_zones": len(zones_out),
        "note": "score_buckets com bucket=None não incluídos (legado sem score_breakdown).",
    }


# ══════════════════════════════════════════════════════════════════════════════
# BACKUP — GitHub automático
# ══════════════════════════════════════════════════════════════════════════════

@scalp_router.get("/backup/status")
async def backup_status():
    """Retorna estado actual do serviço de backup GitHub."""
    from services.github_backup_service import get_backup_status
    return get_backup_status()


@scalp_router.post("/backup/run")
async def backup_run_now(
    include_code: bool = Query(True, description="Incluir push de código (git push)"),
):
    """
    Dispara um backup manual imediato.
    Exporta dados Atlas + (opcional) push de código para ventonorte21/Quantum_v2.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database não inicializado")
    from services.github_backup_service import run_backup, _is_running
    if _is_running:
        raise HTTPException(status_code=409, detail="Backup já em execução — aguarde.")
    result = await run_backup(_database, include_code=include_code)
    return result
