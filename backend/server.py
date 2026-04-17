from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
import asyncio
import time
import orjson
import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator, ADXIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator
import databento as db
import httpx
import yfinance as yf
from scipy.stats import norm
import math

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection — variáveis obrigatórias; falha rápida com mensagem clara
_mongo_url = os.environ.get('MONGO_URL')
_db_name   = os.environ.get('DB_NAME')
if not _mongo_url:
    raise RuntimeError(
        "MONGO_URL não configurada. Defina a variável de ambiente antes de iniciar o servidor."
    )
if not _db_name:
    raise RuntimeError(
        "DB_NAME não configurada. Defina a variável de ambiente antes de iniciar o servidor."
    )
mongo_url = _mongo_url
client = AsyncIOMotorClient(mongo_url)
database = client[_db_name]

# DataBento API Key
DATABENTO_API_KEY = os.environ.get('DATABENTO_API_KEY', '')

# Alpha Vantage API Key
ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY', '')

# Live Data Service
from services.live_data_service import LiveDataService
from services.delta_zonal_service import delta_zonal_service
from services.position_manager import (
    calculate_position_params, create_position_document,
    get_archetype, PositionState,
)
from services.gamma_ratio_service import GammaRatioService
from services.trading_calendar_service import TradingCalendarService
from services.scalp_snapshot_service import (
    ensure_scalp_snapshot_indexes,
    record_scalp_snapshots,
    SCALP_SNAPSHOT_INTERVAL,
    SCALP_SNAPSHOT_SYMBOLS,
)
from services.data_quality import (
    lkg_store, breaker_registry, Confidence,
)
from services.feed_health import FeedHealthMonitor
from services.telegram_alerts import (
    alert_position_opened, alert_position_closed,
)
from routes.auth import auth_router, set_auth_db
from routes.replay import replay_router, set_replay_db
from routes.fills import fills_router, set_fills_db
from routes.scalp import scalp_router, set_scalp_deps
live_data_service = LiveDataService(DATABENTO_API_KEY)
feed_health_monitor = FeedHealthMonitor(live_data_service, database)
scalp_engine = None      # initialised in warm_caches() on startup
scalp_auto_trader = None  # initialised in warm_caches() on startup

# Create the main app
app = FastAPI(title="Quantum Trading System", version="1.0.0")

_validation_logger = logging.getLogger("validation")

@app.exception_handler(RequestValidationError)
async def _log_422(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
        _validation_logger.error(
            "422 %s %s — errors: %s | body: %s",
            request.method, request.url.path,
            exc.errors(),
            body[:2000].decode("utf-8", errors="replace"),
        )
    except Exception:
        _validation_logger.error("422 %s %s — errors: %s", request.method, request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

# Readiness flag — set to True once warm_caches() completes at startup.
# /api/ready and /api/health expose this to clients.
_backend_ready = False

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Register auth router (no /api prefix — it handles its own /api/auth prefix)
app.include_router(auth_router)
app.include_router(replay_router)
app.include_router(fills_router)
app.include_router(scalp_router)
set_auth_db(database)
set_replay_db(database)
set_fills_db(database)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== MODELS ==========

class IndicatorWeight(BaseModel):
    name: str
    weight: float
    category: str  # 'predictive' or 'confirmatory'

class TradingSignal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    timeframe: str
    signal_type: str  # 'LONG', 'SHORT', 'NEUTRAL'
    confluence_score: float
    confidence_score: float
    predictive_indicators: Dict[str, Any]
    confirmatory_indicators: Dict[str, Any]
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class OHLCVData(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    symbol: str

class MarketDataRequest(BaseModel):
    symbol: str
    timeframe: str = '5M'  # '4H', '1H', '5M'
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class BacktestRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    initial_capital: float = 100000
    risk_per_trade: float = 0.02

class BacktestResult(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    sharpe_ratio: float
    trades: List[Dict]

# ========== SIGNALSTACK MODELS ==========

class SignalStackConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    webhook_url: str
    name: str = "Default"
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SignalStackOrder(BaseModel):
    """Order model for SignalStack → Tradovate webhook.

    Supported order types: market, limit, stop, stop_limit
    Supported actions: buy, sell, close (flatten), cancel

    Bracket order (OCO nativo no Tradovate):
        take_profit_price — preço absoluto do TP (cria child order; cancelado se SL preencher)
        stop_loss_price   — preço absoluto do SL hard stop (cancelado se TP preencher)

    Trailing Stop (Tradovate native via webhook providers):
        trail_trigger — points of profit before trailing activates
        trail_stop    — distance trailing stop keeps behind price
        trail_freq    — how often the stop updates (in points)

    Break-even:
        breakeven — points of profit to move SL to entry

    Update existing SL/TP:
        update_sl / update_tp — replace current SL/TP with new values
    """
    symbol: str  # Tradovate format: e.g. "MESH6" (1-digit year)
    action: str  # "buy", "sell", "close", "cancel"
    quantity: int = 1
    order_type: str = "market"  # "market", "limit", "stop", "stop_limit"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    # Bracket order — OCO nativo (take_profit_price + stop_loss_price na mesma entrada)
    take_profit_price: Optional[float] = None  # TP absoluto; child cancelado se SL preencher
    stop_loss_price: Optional[float] = None    # SL hard stop; child cancelado se TP preencher
    # Trailing Stop (Tradovate native)
    trail_trigger: Optional[float] = None  # Points profit to activate trailing
    trail_stop: Optional[float] = None     # Distance behind price
    trail_freq: Optional[float] = None     # Update frequency in points
    # Break-even
    breakeven: Optional[float] = None      # Points profit to move SL to entry
    # Update existing orders
    update_sl: Optional[bool] = None       # Replace current SL
    update_tp: Optional[bool] = None       # Replace current TP

class SignalStackOrderRequest(BaseModel):
    webhook_url: str
    order: SignalStackOrder

class SignalStackOrderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    action: str
    quantity: int
    order_type: str
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    status: str  # "sent", "success", "error"
    response_code: Optional[int] = None
    response_message: Optional[str] = None
    child_ids: Optional[List[int]] = None  # Bracket child order IDs from Tradovate
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    webhook_url: str
    payload: Optional[Dict[str, Any]] = None

# Symbol mapping for Tradovate format
def get_tradovate_symbol(symbol: str) -> str:
    """Convert our symbol to Tradovate format with current front month.
    
    Tradovate uses 1-digit year: MNQH6 = MNQ March 2026
    Month codes: H=Mar, M=Jun, U=Sep, Z=Dec
    """
    now = datetime.now()
    month = now.month
    year = now.year % 10  # 1-digit year per Tradovate spec

    if month <= 3:
        month_code = 'H'
    elif month <= 6:
        month_code = 'M'
    elif month <= 9:
        month_code = 'U'
    else:
        month_code = 'Z'

    # Past current quarter expiry (~3rd Friday) → roll to next quarter
    if month in [3, 6, 9, 12] and now.day > 15:
        if month_code == 'H':
            month_code = 'M'
        elif month_code == 'M':
            month_code = 'U'
        elif month_code == 'U':
            month_code = 'Z'
        else:
            month_code = 'H'
            year = (year + 1) % 10

    return f"{symbol}{month_code}{year}"

# ========== AUTO TRADING MODELS ==========

class AutoTradingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Enable/Disable
    enabled: bool = False
    paper_trading: bool = True  # If True, don't send real orders
    
    # Symbols to trade
    symbols: List[str] = ['MNQ', 'MES']
    
    # Entry Conditions (Confluence - 1st Filter)
    min_confluence_score: float = 70.0  # Minimum confluence to consider entry
    
    # Confirmation Conditions (Confidence - 2nd Filter)
    min_confidence_score: float = 60.0  # Minimum confidence to confirm entry
    
    # Multi-Timeframe Alignment
    require_mtf_alignment: bool = True  # Require 4H and 1H to agree
    mtf_timeframes: List[str] = ['4H', '1H']
    
    # VIX/Volatility Filter
    max_vix: float = 30.0  # Don't trade if VIX > this
    min_vix: float = 10.0  # Don't trade if VIX < this (too quiet)
    
    # Volume Filter
    min_volume_ratio: float = 0.8  # Minimum volume vs 20-day average
    
    # Position Sizing
    account_size: float = 50000.0  # Account size in USD
    risk_per_trade_pct: float = 1.0  # Max risk per trade as % of account
    default_quantity: int = 1
    max_positions_per_symbol: int = 1
    max_total_positions: int = 4
    
    # Risk Management
    use_atr_stops: bool = True
    atr_stop_multiplier: float = 1.5
    atr_target_multiplier: float = 3.0
    max_daily_trades: int = 10
    max_daily_loss_percent: float = 2.0  # Stop trading if daily loss > 2%
    
    # Time Filters
    auto_hours_mode: bool = True  # True = automatic NYSE hours, False = manual
    globex_auto_enabled: bool = False  # Enable Globex session (18:00 ET → 09:30 ET)
    trading_start_hour: int = 9  # EST (manual mode only)
    trading_end_hour: int = 16  # EST (manual mode only)
    avoid_first_minutes: int = 15  # Avoid first 15 min after open
    avoid_last_minutes: int = 15  # Avoid last 15 min before close
    pre_close_flatten_minutes: int = 30  # Flatten positions N min before close
    globex_flatten_before_ny_minutes: int = 10  # Flatten Globex positions N min before NY open

    # News Blackout
    news_blackout_enabled: bool = True
    news_blackout_minutes_before: int = 15
    news_blackout_minutes_after: int = 15

    # EOD Flatten
    eod_flatten_enabled: bool = True  # Auto close all positions at session end
    
    # Cooldown
    min_minutes_between_trades: int = 5
    
    # Webhook
    webhook_url: str = ""
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AutoTradingSignal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    action: str  # 'LONG' or 'SHORT'
    
    # Scores
    confluence_score: float
    confidence_score: float
    mtf_alignment: bool
    mtf_signals: Dict[str, str]
    
    # Conditions Met
    conditions_met: Dict[str, bool]
    all_conditions_met: bool
    
    # Trade Details
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    quantity: int = 1
    
    # Status
    executed: bool = False
    paper_trade: bool = True
    order_id: Optional[str] = None
    execution_time: Optional[datetime] = None
    
    # Metadata
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""

class AutoTradingState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    # Current state
    is_running: bool = False
    last_check: Optional[datetime] = None
    
    # Positions
    open_positions: Dict[str, Dict] = {}  # symbol -> position info
    
    # Daily stats
    daily_trades: int = 0
    daily_pnl: float = 0.0
    daily_start: Optional[datetime] = None
    
    # Last trade times per symbol
    last_trade_time: Dict[str, datetime] = {}

# Global auto trading state
auto_trading_state = AutoTradingState()

# Helper function to convert numpy types to Python native types
MONGO_INT_MAX = 9223372036854775807
MONGO_INT_MIN = -9223372036854775808


def _clamp_int(val: int) -> int:
    """Clamp int to MongoDB's 8-byte signed range."""
    if val > MONGO_INT_MAX:
        return MONGO_INT_MAX
    if val < MONGO_INT_MIN:
        return MONGO_INT_MIN
    return val


def _safe_float(val: float) -> float:
    """Replace NaN/Inf with 0.0 for JSON/MongoDB safety."""
    if val != val or val == float('inf') or val == float('-inf'):
        return 0.0
    return val


def convert_numpy_types(obj):
    """Convert numpy types to Python native types for JSON serialization and MongoDB compatibility."""
    if isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_numpy_types(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [convert_numpy_types(v) for v in obj.tolist()]
    # numpy scalars
    if hasattr(np, 'bool_') and isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return _clamp_int(int(obj))
    if isinstance(obj, (np.floating,)):
        return _safe_float(float(obj))
    # Python primitives
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return _clamp_int(obj)
    if isinstance(obj, float):
        return _safe_float(obj)
    # Datetime
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return obj

# ========== INDICATOR WEIGHTS (Best Practices) ==========

INDICATOR_WEIGHTS = {
    # Predictive Indicators (First Filter - Confluence)
    'predictive': {
        'RSI': {'weight': 0.15, 'description': 'Relative Strength Index'},
        'MACD': {'weight': 0.20, 'description': 'Moving Average Convergence Divergence'},
        'EMA_CROSS': {'weight': 0.15, 'description': 'EMA 9/21 Crossover'},
        'BOLLINGER': {'weight': 0.10, 'description': 'Bollinger Bands Position'},
        'STOCHASTIC': {'weight': 0.10, 'description': 'Stochastic Oscillator'},
        'ADX': {'weight': 0.15, 'description': 'Average Directional Index'},
        'ATR': {'weight': 0.15, 'description': 'Average True Range Breakout'}
    },
    # Confirmatory Indicators (Second Filter - Confidence)
    'confirmatory': {
        'VOLUME_PROFILE': {'weight': 0.25, 'description': 'Volume Analysis'},
        'OBV': {'weight': 0.20, 'description': 'On-Balance Volume'},
        'VWAP': {'weight': 0.15, 'description': 'Volume Weighted Average Price'},
        'GAMMA_EXPOSURE': {'weight': 0.20, 'description': 'Gamma Exposure (GEX)'},
        'VIX': {'weight': 0.20, 'description': 'Volatility Index'}
    }
}

# Supported symbols
SYMBOLS = {
    'MNQ': {'name': 'Micro E-mini Nasdaq-100', 'continuous': 'MNQ.v.0', 'tick_size': 0.25, 'tick_value': 0.50, 'point_value': 2.0},
    'MES': {'name': 'Micro E-mini S&P 500', 'continuous': 'MES.v.0', 'tick_size': 0.25, 'tick_value': 1.25, 'point_value': 5.0},
}

# ========== DATABENTO SERVICE ==========

class DataBentoService:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.dataset = "GLBX.MDP3"
        self._client = None
        # Cache for DataBento availability boundary (parsed from 422 errors)
        # Prevents redundant retries when data hasn't been processed yet (e.g. post-midnight UTC)
        self._availability_cap: datetime | None = None
        self._availability_cap_time: datetime | None = None
        self._AVAILABILITY_CAP_TTL = 600  # recheck every 10 minutes

    @property
    def client(self):
        if self._client is None and self.api_key:
            self._client = db.Historical(self.api_key)
        return self._client

    def _parse_availability_cap(self, error_str: str) -> datetime | None:
        """Extract upper availability boundary from DataBento 422 error messages.
        Handles both formats:
          - data_end_after_available_end: "available up to 'TIMESTAMP'"
          - data_schema_not_fully_available: "only available between START and TIMESTAMP."
        """
        import re as _re
        # Format 1: data_end_after_available_end
        m = _re.search(r"available up to '([^']+)'", error_str)
        if m:
            try:
                return datetime.fromisoformat(m.group(1).replace('Z', '+00:00'))
            except Exception:
                pass
        # Format 2: data_schema_not_fully_available
        # "only available between TIMESTAMP and TIMESTAMP."
        m2 = _re.search(r'only available between .+ and ([0-9T:.\-+Z]+)', error_str)
        if m2:
            try:
                raw = m2.group(1).rstrip('.').replace('Z', '+00:00')
                return datetime.fromisoformat(raw)
            except Exception:
                pass
        return None

    def _get_safe_end(self, end_dt: datetime, schema: str, now: datetime) -> datetime:
        """Compute a safe end time that respects both schema delay and known availability cap."""
        if schema == 'ohlcv-1h':
            # Floor to the last COMPLETED hour boundary.
            # DataBento ohlcv-1h bars are only finalized after the hour closes
            # (plus processing lag). Using now - 70min can still land inside the
            # current hour when requests fire near the top of an hour.
            last_completed_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            safe = min(end_dt, last_completed_hour)
        else:
            availability_delay = timedelta(minutes=35)
            safe = min(end_dt, now - availability_delay)
        # Apply cached availability cap if still fresh
        if (self._availability_cap is not None and self._availability_cap_time is not None
                and (now - self._availability_cap_time).total_seconds() < self._AVAILABILITY_CAP_TTL):
            safe = min(safe, self._availability_cap - timedelta(minutes=1))
        return safe

    def clamp_to_availability(self, dt: datetime, now: datetime) -> datetime:
        """Clamp dt to the cached DataBento availability boundary (if known and fresh).
        Used by trades-schema services (SharedTrades, TickIndex, CVD, OFI) whose
        safe_end = now - 2h will cross midnight UTC after ~02:00 UTC."""
        if (self._availability_cap is not None and self._availability_cap_time is not None
                and (now - self._availability_cap_time).total_seconds() < self._AVAILABILITY_CAP_TTL):
            clamped = min(dt, self._availability_cap - timedelta(minutes=1))
            if clamped != dt:
                logger.debug(f"DataBento trades safe_end clamped: {dt.isoformat()} → {clamped.isoformat()} (cap={self._availability_cap.isoformat()})")
            return clamped
        return dt
    
    def get_schema_for_timeframe(self, timeframe: str) -> str:
        mapping = {
            '1M': 'ohlcv-1m',
            '5M': 'ohlcv-1m',  # Aggregate from 1M (DataBento has no 5M schema)
            '1H': 'ohlcv-1h',
            '4H': 'ohlcv-1h',  # Aggregate from 1H
            '1D': 'ohlcv-1d'
        }
        return mapping.get(timeframe, 'ohlcv-1h')
    
    async def get_historical_data(self, symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        if not self.client:
            logger.warning("DataBento client not initialized, using simulated data")
            return self._generate_simulated_data(symbol, timeframe, start, end)
        
        # OHLCV cache (keyed by symbol+timeframe, 5min TTL)
        if not hasattr(self, '_ohlcv_cache'):
            self._ohlcv_cache = {}
            self._ohlcv_cache_time = {}
        
        cache_key = f"ohlcv_{symbol}_{timeframe}"
        now = datetime.now(timezone.utc)
        if cache_key in self._ohlcv_cache and cache_key in self._ohlcv_cache_time:
            if (now - self._ohlcv_cache_time[cache_key]).total_seconds() < 300:
                return self._ohlcv_cache[cache_key]
        
        try:
            continuous_symbol = SYMBOLS.get(symbol, {}).get('continuous', f'{symbol}.v.0')
            schema = self.get_schema_for_timeframe(timeframe)
            
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00')) if isinstance(end, str) else end
            safe_end = self._get_safe_end(end_dt, schema, now)
            
            data = await asyncio.to_thread(
                self.client.timeseries.get_range,
                dataset=self.dataset,
                symbols=continuous_symbol,
                schema=schema,
                start=start,
                end=safe_end.isoformat(),
                stype_in="continuous",
            )
            
            df = data.to_df()
            
            if timeframe == '5M' and not df.empty:
                df = self._aggregate_to_5m(df)
            elif timeframe == '4H' and not df.empty:
                df = self._aggregate_to_4h(df)
            
            self._ohlcv_cache[cache_key] = df
            self._ohlcv_cache_time[cache_key] = now
            return df
            
        except Exception as e:
            logger.error(f"DataBento API error: {e}")
            # On any 422 with a parsable availability boundary, cache it so that
            # subsequent calls skip retries that are guaranteed to fail.
            # Handles both data_end_after_available_end and data_schema_not_fully_available.
            if '422' in str(e):
                cap = self._parse_availability_cap(str(e))
                if cap:
                    self._availability_cap = cap
                    self._availability_cap_time = now
                    logger.info(f"DataBento availability cap cached: {cap.isoformat()}")
            if '422' in str(e):
                for fallback_hours in [1, 3, 6, 24, 48]:
                    try:
                        fallback_end = now - timedelta(hours=fallback_hours)
                        # Skip this step if cached cap already tells us it will fail
                        if (self._availability_cap is not None and
                                fallback_end > self._availability_cap):
                            logger.debug(f"DataBento skip retry hours={fallback_hours} (still after cap {self._availability_cap.isoformat()})")
                            continue
                        logger.info(f"DataBento retry with end={fallback_end.isoformat()}")
                        data = await asyncio.to_thread(
                            self.client.timeseries.get_range,
                            dataset=self.dataset,
                            symbols=continuous_symbol,
                            schema=schema,
                            start=start,
                            end=fallback_end.isoformat(),
                            stype_in="continuous",
                        )
                        df = data.to_df()
                        if timeframe == '5M' and not df.empty:
                            df = self._aggregate_to_5m(df)
                        elif timeframe == '4H' and not df.empty:
                            df = self._aggregate_to_4h(df)
                        if not df.empty:
                            self._ohlcv_cache[cache_key] = df
                            self._ohlcv_cache_time[cache_key] = now
                            return df
                    except Exception as retry_e:
                        logger.warning(f"DataBento retry (fallback_hours={fallback_hours}) failed: {retry_e}")
                        continue
            # LKG: return stale OHLCV cache if available rather than synthetic data
            if cache_key in self._ohlcv_cache and not self._ohlcv_cache[cache_key].empty:
                stale_age = int((now - self._ohlcv_cache_time.get(cache_key, now)).total_seconds())
                logger.warning(f"DataBento OHLCV all retries failed for {symbol}/{timeframe} — serving stale cache (age={stale_age}s)")
                return self._ohlcv_cache[cache_key]
            logger.warning(f"DataBento OHLCV no stale cache for {symbol}/{timeframe} — falling back to simulated data")
            return self._generate_simulated_data(symbol, timeframe, start, end)
    
    def _aggregate_to_4h(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        
        df_resampled = df.resample('4h').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        return df_resampled

    def _aggregate_to_5m(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        
        df_resampled = df.resample('5min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        return df_resampled
    
    def _generate_simulated_data(self, symbol: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        """Generate simulated data for demo/testing - respects market hours"""
        base_prices = {'MNQ': 21500, 'MES': 6100}
        base_price = base_prices.get(symbol, 5000)
        
        start_dt = datetime.fromisoformat(start.replace('Z', '+00:00')) if isinstance(start, str) else start
        end_dt = datetime.fromisoformat(end.replace('Z', '+00:00')) if isinstance(end, str) else end
        
        freq_map = {'5M': '5min', '1H': '1h', '4H': '4h', '1D': '1D'}
        freq = freq_map.get(timeframe, '1h')
        
        dates = pd.date_range(start=start_dt, end=end_dt, freq=freq)
        
        # Filter out weekends (CME futures trade Sun 6PM - Fri 5PM CT)
        # Remove Saturday entirely and Sunday before 23:00 UTC (6PM CT)
        dates = dates[~((dates.dayofweek == 5) |  # Saturday
                        ((dates.dayofweek == 6) & (dates.hour < 23)))]  # Sunday before 23:00 UTC
        
        # Also remove Friday after 22:00 UTC (5PM CT close)
        dates = dates[~((dates.dayofweek == 4) & (dates.hour >= 22))]
        
        n = len(dates)
        
        if n == 0:
            return pd.DataFrame()
        
        np.random.seed(42)
        returns = np.random.normal(0.0001, 0.002, n)
        prices = base_price * np.cumprod(1 + returns)
        
        volatility = base_price * 0.001
        data = {
            'open': prices + np.random.normal(0, volatility, n),
            'high': prices + np.abs(np.random.normal(volatility, volatility, n)),
            'low': prices - np.abs(np.random.normal(volatility, volatility, n)),
            'close': prices,
            'volume': np.random.randint(1000, 50000, n)
        }
        
        df = pd.DataFrame(data, index=dates)
        df['symbol'] = symbol
        
        return df

databento_service = DataBentoService(DATABENTO_API_KEY)
gamma_ratio_service = GammaRatioService(databento_service, database)
trading_calendar = TradingCalendarService(database=database)

# ========== SHARED TRADES FETCH ==========

class SharedTradesService:
    """Fetch DataBento trades with INCREMENTAL delta fetch (ring buffer).
    
    Instead of re-fetching 60 minutes of trades every 2 minutes (~300k trades),
    only fetches the NEW trades since last fetch (~10k) and appends to the buffer.
    Prunes trades older than the lookback window.
    """
    _buffer: Dict[str, list] = {}            # Ring buffer: accumulated trades
    _buffer_end: Dict[str, datetime] = {}    # Timestamp of last fetch end
    _cache_time: Dict[str, datetime] = {}    # When we last refreshed
    CACHE_DURATION = 120  # 2 min between refreshes

    @classmethod
    async def get_trades(cls, symbol: str, lookback_minutes: int = 60) -> list:
        """Fetch raw trades with incremental delta fetch.
        Returns a list of dicts with keys: side, size, price, ts"""
        now = datetime.now(timezone.utc)
        cache_key = f"trades_{symbol}_{lookback_minutes}"

        # Return buffer if still fresh
        if cache_key in cls._buffer and cache_key in cls._cache_time:
            if (now - cls._cache_time[cache_key]).total_seconds() < cls.CACHE_DURATION:
                return cls._buffer[cache_key]

        if not databento_service.client:
            return cls._buffer.get(cache_key, [])

        # Skip trades fetch on weekends (CME closed Sat, Sun before 23:00 UTC)
        weekday = now.weekday()
        if weekday == 5 or (weekday == 6 and now.hour < 23):
            logger.info(f"Weekend detected, skipping DataBento trades fetch for {symbol}")
            cls._buffer[cache_key] = []
            cls._cache_time[cache_key] = now
            return []

        try:
            continuous_symbol = SYMBOLS.get(symbol, {}).get('continuous', f'{symbol}.v.0')
            safe_end = databento_service.clamp_to_availability(now - timedelta(hours=2), now)

            # Determine fetch window: delta (incremental) or full (cold start)
            if cache_key in cls._buffer_end and cache_key in cls._buffer:
                # INCREMENTAL: fetch only new trades since last end
                fetch_start = cls._buffer_end[cache_key]
                is_incremental = True
            else:
                # COLD START: fetch full lookback window
                fetch_start = safe_end - timedelta(minutes=lookback_minutes)
                is_incremental = False

            # Don't fetch if start >= end (clock drift or very rapid calls)
            if fetch_start >= safe_end:
                cls._cache_time[cache_key] = now
                return cls._buffer.get(cache_key, [])

            # ── Run blocking DataBento I/O in thread pool ──
            # timeseries.get_range is synchronous — must NOT run on the event loop.
            import asyncio, functools as _ft
            _client = databento_service.client
            _dataset = databento_service.dataset
            _fetch_kwargs = dict(
                dataset=_dataset,
                symbols=continuous_symbol,
                schema="trades",
                start=fetch_start.isoformat(),
                end=safe_end.isoformat(),
                stype_in="continuous",
            )

            def _sync_fetch():
                """Blocking DataBento fetch — runs in thread pool."""
                data = _client.timeseries.get_range(**_fetch_kwargs)
                trades_out = []
                for record in data:
                    side = getattr(record, 'side', None)
                    size = float(getattr(record, 'size', 1) or 1)
                    price_raw = getattr(record, 'price', 0)
                    price = float(price_raw) / 1e9 if price_raw > 1e6 else float(price_raw)
                    ts = getattr(record, 'ts_event', 0)
                    trades_out.append({'side': side, 'size': size, 'price': price, 'ts': ts})
                return trades_out

            loop = asyncio.get_event_loop()
            new_trades = await loop.run_in_executor(None, _sync_fetch)

            if is_incremental:
                # Merge: existing buffer + new trades, prune old ones
                cutoff_ns = int((safe_end - timedelta(minutes=lookback_minutes)).timestamp() * 1e9)
                existing = cls._buffer.get(cache_key, [])
                combined = [t for t in existing if t['ts'] >= cutoff_ns] + new_trades
                cls._buffer[cache_key] = combined
                logger.debug(
                    "SharedTrades INCREMENTAL %s: +%d new, %d pruned, %d total",
                    symbol, len(new_trades), len(existing) - (len(combined) - len(new_trades)), len(combined)
                )
            else:
                # Cold start: just store everything
                cls._buffer[cache_key] = new_trades
                logger.debug("SharedTrades COLD START %s: %d trades loaded", symbol, len(new_trades))

            cls._buffer_end[cache_key] = safe_end
            cls._cache_time[cache_key] = now
            return cls._buffer[cache_key]

        except Exception as e:
            logger.error(f"SharedTradesService error for {symbol}: {e}")
            # Return stale buffer instead of empty (LKG pattern for trades)
            if cache_key in cls._buffer and cls._buffer[cache_key]:
                logger.info("SharedTrades: returning stale buffer for %s (%d trades)", symbol, len(cls._buffer[cache_key]))
                cls._cache_time[cache_key] = now
                return cls._buffer[cache_key]
            cls._cache_time[cache_key] = now
            return []

shared_trades_service = SharedTradesService()

# ========== TICK INDEX SERVICE (DataBento TRADES) ==========

class TickIndexService:
    """Calculate TICK Index from DataBento trade data (side field)"""
    _cache = {}
    _cache_time = {}
    CACHE_DURATION = 120  # 2 min cache for TICK (more real-time)

    @classmethod
    async def get_tick_index(cls, symbol: str, lookback_minutes: int = 60) -> Dict[str, Any]:
        """Get TICK Index calculated from DataBento trades (uptick vs downtick)"""
        now = datetime.now(timezone.utc)
        cache_key = f"{symbol}_{lookback_minutes}"

        if cache_key in cls._cache and cache_key in cls._cache_time:
            cache_age = (now - cls._cache_time[cache_key]).total_seconds()
            if cache_age < cls.CACHE_DURATION:
                return cls._cache[cache_key]

        try:
            if not databento_service.client:
                return cls._simulate_tick_index(symbol)

            continuous_symbol = SYMBOLS.get(symbol, {}).get('continuous', f'{symbol}.v.0')
            # Ensure safe_end > start: use 2h buffer for end, capped to availability boundary
            safe_end = databento_service.clamp_to_availability(now - timedelta(hours=2), now)
            start_dt = safe_end - timedelta(minutes=lookback_minutes)

            import asyncio as _aio
            _ti_client = databento_service.client
            _ti_kwargs = dict(
                dataset=databento_service.dataset,
                symbols=continuous_symbol,
                schema="trades",
                start=start_dt.isoformat(),
                end=safe_end.isoformat(),
                stype_in="continuous",
            )

            def _sync_tick_fetch():
                data = _ti_client.timeseries.get_range(**_ti_kwargs)
                up, down, total, cur = 0, 0, 0, 0
                series = []
                for record in data:
                    total += 1
                    side = getattr(record, 'side', None)
                    if side == 'A':
                        down += 1; cur -= 1
                    elif side == 'B':
                        up += 1; cur += 1
                    if total % 100 == 0:
                        series.append({'trade_num': total, 'tick': cur})
                return up, down, total, cur, series

            uptick, downtick, total_trades, current_tick, tick_series = await _aio.get_event_loop().run_in_executor(None, _sync_tick_fetch)

            tick_index = uptick - downtick

            # Determine sentiment
            if total_trades == 0:
                ratio = 0.5
            else:
                ratio = uptick / total_trades

            if tick_index > 200:
                sentiment = 'STRONG_BUY'
                signal = 'CONFIRM'
                score = min(0.95, 0.7 + tick_index / 2000)
            elif tick_index > 50:
                sentiment = 'BULLISH'
                signal = 'CONFIRM'
                score = min(0.8, 0.6 + tick_index / 1000)
            elif tick_index > -50:
                sentiment = 'NEUTRAL'
                signal = 'NEUTRAL'
                score = 0.5
            elif tick_index > -200:
                sentiment = 'BEARISH'
                signal = 'CAUTION'
                score = max(0.3, 0.4 - abs(tick_index) / 1000)
            else:
                sentiment = 'STRONG_SELL'
                signal = 'CAUTION'
                score = max(0.1, 0.3 - abs(tick_index) / 2000)

            result = {
                'tick_index': tick_index,
                'uptick': uptick,
                'downtick': downtick,
                'total_trades': total_trades,
                'ratio': round(ratio, 4),
                'sentiment': sentiment,
                'signal': signal,
                'score': round(score, 2),
                'lookback_minutes': lookback_minutes,
                'tick_high': max((s['tick'] for s in tick_series), default=tick_index),
                'tick_low': min((s['tick'] for s in tick_series), default=tick_index),
                'source': 'databento_trades',
                'timestamp': now.isoformat()
            }

            cls._cache[cache_key] = result
            cls._cache_time[cache_key] = now
            return result

        except Exception as e:
            logger.error(f"Error fetching TICK index from DataBento: {e}")
            return cls._simulate_tick_index(symbol)

    @classmethod
    def calculate_from_trades(cls, symbol: str, trades: list) -> Dict[str, Any]:
        """Calculate TICK index from pre-fetched trades list (no API call)"""
        now = datetime.now(timezone.utc)
        if not trades:
            return cls._simulate_tick_index(symbol)

        uptick = 0
        downtick = 0
        total_trades = 0
        tick_series = []
        current_tick = 0

        for t in trades:
            total_trades += 1
            side = t.get('side')
            if side == 'A':
                downtick += 1
                current_tick -= 1
            elif side == 'B':
                uptick += 1
                current_tick += 1
            if total_trades % 100 == 0:
                tick_series.append({'trade_num': total_trades, 'tick': current_tick})

        tick_index = uptick - downtick
        if total_trades == 0:
            ratio = 0.5
        else:
            ratio = uptick / total_trades

        if tick_index > 200:
            sentiment, signal, score = 'STRONG_BUY', 'CONFIRM', min(0.95, 0.7 + tick_index / 2000)
        elif tick_index > 50:
            sentiment, signal, score = 'BULLISH', 'CONFIRM', min(0.8, 0.6 + tick_index / 1000)
        elif tick_index > -50:
            sentiment, signal, score = 'NEUTRAL', 'NEUTRAL', 0.5
        elif tick_index > -200:
            sentiment, signal, score = 'BEARISH', 'CAUTION', max(0.3, 0.4 - abs(tick_index) / 1000)
        else:
            sentiment, signal, score = 'STRONG_SELL', 'CAUTION', max(0.1, 0.3 - abs(tick_index) / 2000)

        return {
            'tick_index': tick_index,
            'uptick': uptick,
            'downtick': downtick,
            'total_trades': total_trades,
            'ratio': round(ratio, 4),
            'sentiment': sentiment,
            'signal': signal,
            'score': round(score, 2),
            'lookback_minutes': 60,
            'tick_high': max((s['tick'] for s in tick_series), default=tick_index),
            'tick_low': min((s['tick'] for s in tick_series), default=tick_index),
            'source': 'databento_trades',
            'timestamp': now.isoformat()
        }

    @classmethod
    def _simulate_tick_index(cls, symbol: str) -> Dict[str, Any]:
        """Fallback simulated TICK index"""
        tick = int(np.random.normal(0, 150))
        total = abs(tick) + int(np.random.uniform(500, 2000))
        uptick = (total + tick) // 2
        downtick = total - uptick
        return {
            'tick_index': tick,
            'uptick': uptick,
            'downtick': downtick,
            'total_trades': total,
            'ratio': round(uptick / total if total > 0 else 0.5, 4),
            'sentiment': 'BULLISH' if tick > 50 else ('BEARISH' if tick < -50 else 'NEUTRAL'),
            'signal': 'CONFIRM' if tick > 50 else ('CAUTION' if tick < -50 else 'NEUTRAL'),
            'score': round(min(0.9, max(0.1, 0.5 + tick / 500)), 2),
            'lookback_minutes': 60,
            'tick_high': max(tick, 0),
            'tick_low': min(tick, 0),
            'source': 'simulated',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

tick_index_service = TickIndexService()

# ========== CVD (CUMULATIVE VOLUME DELTA) SERVICE ==========

class CVDService:
    """Calculate Cumulative Volume Delta from DataBento trade data"""
    _cache = {}
    _cache_time = {}
    CACHE_DURATION = 120

    @classmethod
    async def get_cvd(cls, symbol: str, lookback_minutes: int = 60) -> Dict[str, Any]:
        """Get CVD from DataBento trades: buy_volume - sell_volume accumulated"""
        now = datetime.now(timezone.utc)
        cache_key = f"{symbol}_{lookback_minutes}"

        if cache_key in cls._cache and cache_key in cls._cache_time:
            cache_age = (now - cls._cache_time[cache_key]).total_seconds()
            if cache_age < cls.CACHE_DURATION:
                return cls._cache[cache_key]

        try:
            if not databento_service.client:
                return cls._simulate_cvd(symbol)

            continuous_symbol = SYMBOLS.get(symbol, {}).get('continuous', f'{symbol}.v.0')
            safe_end = databento_service.clamp_to_availability(now - timedelta(hours=2), now)
            start_dt = safe_end - timedelta(minutes=lookback_minutes)

            import asyncio as _aio
            _cvd_client = databento_service.client
            _cvd_kwargs = dict(
                dataset=databento_service.dataset,
                symbols=continuous_symbol,
                schema="trades",
                start=start_dt.isoformat(),
                end=safe_end.isoformat(),
                stype_in="continuous",
            )

            def _sync_cvd_fetch():
                data = _cvd_client.timeseries.get_range(**_cvd_kwargs)
                bv, sv, total, cvd_val, cvd_h, cvd_l = 0, 0, 0, 0, 0, 0
                series = []
                for record in data:
                    total += 1
                    side = getattr(record, 'side', None)
                    size = float(getattr(record, 'size', 1) or 1)
                    if side == 'A':
                        sv += size; cvd_val -= size
                    elif side == 'B':
                        bv += size; cvd_val += size
                    cvd_h = max(cvd_h, cvd_val)
                    cvd_l = min(cvd_l, cvd_val)
                    if total % 200 == 0:
                        series.append({'trade_num': total, 'cvd': round(cvd_val, 0)})
                return bv, sv, total, cvd_val, cvd_h, cvd_l, series

            buy_volume, sell_volume, total_trades, cvd, cvd_high, cvd_low, cvd_series = await _aio.get_event_loop().run_in_executor(None, _sync_cvd_fetch)

            total_volume = buy_volume + sell_volume

            # CVD trend: compare current to midpoint
            if len(cvd_series) >= 4:
                mid_idx = len(cvd_series) // 2
                first_half_avg = sum(s['cvd'] for s in cvd_series[:mid_idx]) / mid_idx
                second_half_avg = sum(s['cvd'] for s in cvd_series[mid_idx:]) / (len(cvd_series) - mid_idx)
                cvd_trend = 'RISING' if second_half_avg > first_half_avg else 'FALLING'
            else:
                cvd_trend = 'RISING' if cvd > 0 else 'FALLING'

            # Sentiment
            if total_volume == 0:
                buy_pct = 50
            else:
                buy_pct = round(buy_volume / total_volume * 100, 1)

            if cvd > 0 and cvd_trend == 'RISING':
                sentiment = 'STRONG_BUY'
                signal = 'CONFIRM'
                score = min(0.95, 0.7 + abs(cvd) / (total_volume + 1) * 2)
            elif cvd > 0:
                sentiment = 'BULLISH'
                signal = 'CONFIRM'
                score = min(0.8, 0.6 + abs(cvd) / (total_volume + 1))
            elif cvd == 0 or abs(cvd) < total_volume * 0.05:
                sentiment = 'NEUTRAL'
                signal = 'NEUTRAL'
                score = 0.5
            elif cvd < 0 and cvd_trend == 'FALLING':
                sentiment = 'STRONG_SELL'
                signal = 'CAUTION'
                score = max(0.1, 0.3 - abs(cvd) / (total_volume + 1))
            else:
                sentiment = 'BEARISH'
                signal = 'CAUTION'
                score = max(0.3, 0.4 - abs(cvd) / (total_volume + 1) * 0.5)

            result = {
                'cvd': round(cvd, 0),
                'buy_volume': round(buy_volume, 0),
                'sell_volume': round(sell_volume, 0),
                'total_volume': round(total_volume, 0),
                'buy_pct': buy_pct,
                'sell_pct': round(100 - buy_pct, 1),
                'cvd_high': round(cvd_high, 0),
                'cvd_low': round(cvd_low, 0),
                'cvd_trend': cvd_trend,
                'total_trades': total_trades,
                'sentiment': sentiment,
                'signal': signal,
                'score': round(score, 2),
                'lookback_minutes': lookback_minutes,
                'source': 'databento_trades',
                'timestamp': now.isoformat()
            }

            cls._cache[cache_key] = result
            cls._cache_time[cache_key] = now
            return result

        except Exception as e:
            logger.error(f"Error fetching CVD from DataBento: {e}")
            return cls._simulate_cvd(symbol)

    @classmethod
    def calculate_from_trades(cls, symbol: str, trades: list) -> Dict[str, Any]:
        """Calculate CVD from pre-fetched trades list (no API call)"""
        now = datetime.now(timezone.utc)
        if not trades:
            return cls._simulate_cvd(symbol)

        buy_volume = 0
        sell_volume = 0
        total_trades = 0
        cvd = 0
        cvd_series = []
        cvd_high = 0
        cvd_low = 0

        for t in trades:
            total_trades += 1
            side = t.get('side')
            size = t.get('size', 1)
            if side == 'A':
                sell_volume += size
                cvd -= size
            elif side == 'B':
                buy_volume += size
                cvd += size
            cvd_high = max(cvd_high, cvd)
            cvd_low = min(cvd_low, cvd)
            if total_trades % 200 == 0:
                cvd_series.append({'trade_num': total_trades, 'cvd': round(cvd, 0)})

        total_volume = buy_volume + sell_volume

        if len(cvd_series) >= 4:
            mid_idx = len(cvd_series) // 2
            first_half_avg = sum(s['cvd'] for s in cvd_series[:mid_idx]) / mid_idx
            second_half_avg = sum(s['cvd'] for s in cvd_series[mid_idx:]) / (len(cvd_series) - mid_idx)
            cvd_trend = 'RISING' if second_half_avg > first_half_avg else 'FALLING'
        else:
            cvd_trend = 'RISING' if cvd > 0 else 'FALLING'

        if total_volume == 0:
            buy_pct = 50
        else:
            buy_pct = round(buy_volume / total_volume * 100, 1)

        if cvd > 0 and cvd_trend == 'RISING':
            sentiment, signal = 'STRONG_BUY', 'CONFIRM'
            score = min(0.95, 0.7 + abs(cvd) / (total_volume + 1) * 2)
        elif cvd > 0:
            sentiment, signal = 'BULLISH', 'CONFIRM'
            score = min(0.8, 0.6 + abs(cvd) / (total_volume + 1))
        elif cvd == 0 or abs(cvd) < total_volume * 0.05:
            sentiment, signal, score = 'NEUTRAL', 'NEUTRAL', 0.5
        elif cvd < 0 and cvd_trend == 'FALLING':
            sentiment, signal = 'STRONG_SELL', 'CAUTION'
            score = max(0.1, 0.3 - abs(cvd) / (total_volume + 1))
        else:
            sentiment, signal = 'BEARISH', 'CAUTION'
            score = max(0.3, 0.4 - abs(cvd) / (total_volume + 1) * 0.5)

        return {
            'cvd': round(cvd, 0),
            'buy_volume': round(buy_volume, 0),
            'sell_volume': round(sell_volume, 0),
            'total_volume': round(total_volume, 0),
            'buy_pct': buy_pct,
            'sell_pct': round(100 - buy_pct, 1),
            'cvd_high': round(cvd_high, 0),
            'cvd_low': round(cvd_low, 0),
            'cvd_trend': cvd_trend,
            'total_trades': total_trades,
            'sentiment': sentiment,
            'signal': signal,
            'score': round(score, 2),
            'lookback_minutes': 60,
            'source': 'databento_trades',
            'timestamp': now.isoformat()
        }

    @classmethod
    def _simulate_cvd(cls, symbol: str) -> Dict[str, Any]:
        """Fallback simulated CVD"""
        total = int(np.random.uniform(5000, 20000))
        buy_vol = int(total * np.random.uniform(0.4, 0.6))
        sell_vol = total - buy_vol
        cvd = buy_vol - sell_vol
        buy_pct = round(buy_vol / total * 100, 1)
        return {
            'cvd': cvd,
            'buy_volume': buy_vol,
            'sell_volume': sell_vol,
            'total_volume': total,
            'buy_pct': buy_pct,
            'sell_pct': round(100 - buy_pct, 1),
            'cvd_high': max(cvd, 0),
            'cvd_low': min(cvd, 0),
            'cvd_trend': 'RISING' if cvd > 0 else 'FALLING',
            'total_trades': int(total * 0.8),
            'sentiment': 'BULLISH' if cvd > 0 else 'BEARISH',
            'signal': 'CONFIRM' if cvd > 0 else 'CAUTION',
            'score': round(min(0.8, max(0.2, 0.5 + cvd / total)), 2),
            'lookback_minutes': 60,
            'source': 'simulated',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

cvd_service = CVDService()

# ========== OFI (ORDER FLOW IMBALANCE) SERVICE ==========

class OFIService:
    """
    Order Flow Imbalance: OFI_Ratio_Fast (30s/500 trades), OFI_Ratio_Slow (3min/2000 trades),
    Absorption_Flag (|OFI_Fast| > 0.7 AND Price_Delta < 2 ticks).
    Session-Aware Cache: 60s RTH / 120s Globex.
    """
    CACHE_DURATION_RTH = 60     # 60s during Regular Trading Hours
    CACHE_DURATION_GLOBEX = 120  # 120s during Globex (overnight — lower urgency)
    _cache: Dict[str, Any] = {}
    _cache_time: Dict[str, datetime] = {}

    @classmethod
    def _get_cache_ttl(cls) -> int:
        return cls.CACHE_DURATION_RTH if _get_current_session_type() == 'rth' else cls.CACHE_DURATION_GLOBEX

    @classmethod
    async def get_ofi(cls, symbol: str, lookback_minutes: int = 5) -> Dict[str, Any]:
        """Get OFI from DataBento trades with fast/slow windows and absorption detection"""
        now = datetime.now(timezone.utc)
        cache_key = f"ofi_{symbol}"
        cache_ttl = cls._get_cache_ttl()

        if cache_key in cls._cache and cache_key in cls._cache_time:
            cache_age = (now - cls._cache_time[cache_key]).total_seconds()
            if cache_age < cache_ttl:
                return cls._cache[cache_key]

        try:
            if not databento_service.client:
                return cls._simulate_ofi(symbol)

            continuous_symbol = SYMBOLS.get(symbol, {}).get('continuous', f'{symbol}.v.0')
            tick_size = SYMBOLS.get(symbol, {}).get('tick_size', 0.25)
            safe_end = databento_service.clamp_to_availability(now - timedelta(hours=2), now)
            start_dt = safe_end - timedelta(minutes=lookback_minutes)

            import asyncio as _aio
            _ofi_client = databento_service.client
            _ofi_kwargs = dict(
                dataset=databento_service.dataset,
                symbols=continuous_symbol,
                schema="trades",
                start=start_dt.isoformat(),
                end=safe_end.isoformat(),
                stype_in="continuous",
            )

            def _sync_ofi_fetch():
                data = _ofi_client.timeseries.get_range(**_ofi_kwargs)
                records = []
                for record in data:
                    side = getattr(record, 'side', None)
                    size = float(getattr(record, 'size', 1) or 1)
                    price_raw = getattr(record, 'price', 0)
                    price = float(price_raw) / 1e9 if price_raw > 1e6 else float(price_raw)
                    ts = getattr(record, 'ts_event', 0)
                    records.append({'side': side, 'size': size, 'price': price, 'ts': ts})
                return records

            trades = await _aio.get_event_loop().run_in_executor(None, _sync_ofi_fetch)

            total_trades = len(trades)
            if total_trades == 0:
                return cls._simulate_ofi(symbol)

            # ---- OFI_Ratio_Fast: last 500 trades ----
            fast_window = trades[-500:] if total_trades >= 500 else trades
            fast_buy = sum(t['size'] for t in fast_window if t['side'] == 'B')
            fast_sell = sum(t['size'] for t in fast_window if t['side'] == 'A')
            fast_total = fast_buy + fast_sell
            ofi_fast = round((fast_buy - fast_sell) / fast_total, 4) if fast_total > 0 else 0.0

            # ---- OFI_Ratio_Slow: last 2000 trades ----
            slow_window = trades[-2000:] if total_trades >= 2000 else trades
            slow_buy = sum(t['size'] for t in slow_window if t['side'] == 'B')
            slow_sell = sum(t['size'] for t in slow_window if t['side'] == 'A')
            slow_total = slow_buy + slow_sell
            ofi_slow = round((slow_buy - slow_sell) / slow_total, 4) if slow_total > 0 else 0.0

            # ---- Absorption_Flag ----
            # Price delta = |last_price - first_price| in the fast window
            if len(fast_window) >= 2:
                price_first = fast_window[0]['price']
                price_last = fast_window[-1]['price']
                price_delta = abs(price_last - price_first)
                price_delta_ticks = round(price_delta / tick_size, 1) if tick_size > 0 else 0
            else:
                price_delta = 0
                price_delta_ticks = 0

            absorption_flag = abs(ofi_fast) > 0.7 and price_delta_ticks < 2

            # Determine which side is absorbing
            absorption_side = 'NONE'
            if absorption_flag:
                absorption_side = 'SELL_ABSORBED' if ofi_fast > 0 else 'BUY_ABSORBED'

            # ---- OFI Series (for sparkline) ----
            ofi_series = []
            chunk_size = max(1, total_trades // 20)
            for i in range(0, total_trades, chunk_size):
                chunk = trades[i:i + chunk_size]
                c_buy = sum(t['size'] for t in chunk if t['side'] == 'B')
                c_sell = sum(t['size'] for t in chunk if t['side'] == 'A')
                c_total = c_buy + c_sell
                ofi_val = round((c_buy - c_sell) / c_total, 4) if c_total > 0 else 0
                ofi_series.append({'idx': len(ofi_series), 'ofi': ofi_val})

            # ---- Signal Logic ----
            # Confluence: both fast and slow agree
            if ofi_fast > 0.3 and ofi_slow > 0.2:
                if absorption_flag:
                    signal = 'ABSORPTION'
                    sentiment = 'SELL_ABSORBED'
                    interpretation = 'Compradores dominam mas preco nao sobe. Vendedores absorvendo agressao. Trava de seguranca ativa.'
                else:
                    signal = 'STRONG_BUY'
                    sentiment = 'BULLISH'
                    interpretation = 'Fluxo comprador dominante em ambas janelas. Gatilho favoravel para entradas compradas.'
            elif ofi_fast < -0.3 and ofi_slow < -0.2:
                if absorption_flag:
                    signal = 'ABSORPTION'
                    sentiment = 'BUY_ABSORBED'
                    interpretation = 'Vendedores dominam mas preco nao cai. Compradores absorvendo agressao. Trava de seguranca ativa.'
                else:
                    signal = 'STRONG_SELL'
                    sentiment = 'BEARISH'
                    interpretation = 'Fluxo vendedor dominante em ambas janelas. Gatilho favoravel para entradas vendidas.'
            elif abs(ofi_fast) > 0.3 and abs(ofi_slow) < 0.15:
                signal = 'DIVERGENCE'
                sentiment = 'CAUTIOUS'
                interpretation = 'Fast e Slow divergem. Sinal de curtissimo prazo sem confirmacao de inercia. Aguardar alinhamento.'
            elif abs(ofi_fast) < 0.1 and abs(ofi_slow) < 0.1:
                signal = 'NEUTRAL'
                sentiment = 'NEUTRAL'
                interpretation = 'Fluxo equilibrado. Sem pressao direcional clara.'
            else:
                signal = 'MIXED'
                sentiment = 'BULLISH' if ofi_fast > 0 else 'BEARISH'
                interpretation = 'Pressao direcional moderada. Monitorar evolucao do OFI_Fast para gatilho.'

            result = {
                'ofi_fast': ofi_fast,
                'ofi_slow': ofi_slow,
                'fast_buy_vol': round(fast_buy, 0),
                'fast_sell_vol': round(fast_sell, 0),
                'fast_trades': len(fast_window),
                'slow_buy_vol': round(slow_buy, 0),
                'slow_sell_vol': round(slow_sell, 0),
                'slow_trades': len(slow_window),
                'absorption_flag': absorption_flag,
                'absorption_side': absorption_side,
                'price_delta': round(price_delta, 4),
                'price_delta_ticks': price_delta_ticks,
                'tick_size': tick_size,
                'total_trades': total_trades,
                'ofi_series': ofi_series[-20:],
                'signal': signal,
                'sentiment': sentiment,
                'interpretation': interpretation,
                'source': 'databento_trades',
                'timestamp': now.isoformat()
            }

            cls._cache[cache_key] = result
            cls._cache_time[cache_key] = now
            return result

        except Exception as e:
            logger.error(f"Error fetching OFI from DataBento: {e}")
            return cls._simulate_ofi(symbol)

    @classmethod
    def calculate_from_trades(cls, symbol: str, trades: list) -> Dict[str, Any]:
        """Calculate OFI from pre-fetched trades list (no API call).
        Uses last 5 min worth of trades (the OFI window is much shorter)."""
        now = datetime.now(timezone.utc)
        tick_size = SYMBOLS.get(symbol, {}).get('tick_size', 0.25)

        if not trades:
            return cls._simulate_ofi(symbol)

        total_trades = len(trades)

        # OFI_Ratio_Fast: last 500 trades
        fast_window = trades[-500:] if total_trades >= 500 else trades
        fast_buy = sum(t['size'] for t in fast_window if t.get('side') == 'B')
        fast_sell = sum(t['size'] for t in fast_window if t.get('side') == 'A')
        fast_total = fast_buy + fast_sell
        ofi_fast = round((fast_buy - fast_sell) / fast_total, 4) if fast_total > 0 else 0.0

        # OFI_Ratio_Slow: last 2000 trades
        slow_window = trades[-2000:] if total_trades >= 2000 else trades
        slow_buy = sum(t['size'] for t in slow_window if t.get('side') == 'B')
        slow_sell = sum(t['size'] for t in slow_window if t.get('side') == 'A')
        slow_total = slow_buy + slow_sell
        ofi_slow = round((slow_buy - slow_sell) / slow_total, 4) if slow_total > 0 else 0.0

        # Absorption Flag
        if len(fast_window) >= 2:
            price_first = fast_window[0].get('price', 0)
            price_last = fast_window[-1].get('price', 0)
            price_delta = abs(price_last - price_first)
            price_delta_ticks = round(price_delta / tick_size, 1) if tick_size > 0 else 0
        else:
            price_delta, price_delta_ticks = 0, 0

        absorption_flag = abs(ofi_fast) > 0.7 and price_delta_ticks < 2
        absorption_side = 'NONE'
        if absorption_flag:
            absorption_side = 'SELL_ABSORBED' if ofi_fast > 0 else 'BUY_ABSORBED'

        # OFI Series
        ofi_series = []
        chunk_size = max(1, total_trades // 20)
        for i in range(0, total_trades, chunk_size):
            chunk = trades[i:i + chunk_size]
            c_buy = sum(t['size'] for t in chunk if t.get('side') == 'B')
            c_sell = sum(t['size'] for t in chunk if t.get('side') == 'A')
            c_total = c_buy + c_sell
            ofi_val = round((c_buy - c_sell) / c_total, 4) if c_total > 0 else 0
            ofi_series.append({'idx': len(ofi_series), 'ofi': ofi_val})

        # Signal Logic
        if ofi_fast > 0.3 and ofi_slow > 0.2:
            if absorption_flag:
                signal, sentiment = 'ABSORPTION', 'SELL_ABSORBED'
                interpretation = 'Compradores dominam mas preco nao sobe. Vendedores absorvendo agressao.'
            else:
                signal, sentiment = 'STRONG_BUY', 'BULLISH'
                interpretation = 'Fluxo comprador dominante em ambas janelas.'
        elif ofi_fast < -0.3 and ofi_slow < -0.2:
            if absorption_flag:
                signal, sentiment = 'ABSORPTION', 'BUY_ABSORBED'
                interpretation = 'Vendedores dominam mas preco nao cai. Compradores absorvendo agressao.'
            else:
                signal, sentiment = 'STRONG_SELL', 'BEARISH'
                interpretation = 'Fluxo vendedor dominante em ambas janelas.'
        elif abs(ofi_fast) > 0.3 and abs(ofi_slow) < 0.15:
            signal, sentiment = 'DIVERGENCE', 'CAUTIOUS'
            interpretation = 'Fast e Slow divergem. Aguardar alinhamento.'
        elif abs(ofi_fast) < 0.1 and abs(ofi_slow) < 0.1:
            signal, sentiment = 'NEUTRAL', 'NEUTRAL'
            interpretation = 'Fluxo equilibrado. Sem pressao direcional clara.'
        else:
            signal = 'MIXED'
            sentiment = 'BULLISH' if ofi_fast > 0 else 'BEARISH'
            interpretation = 'Pressao direcional moderada. Monitorar evolucao do OFI_Fast.'

        return {
            'ofi_fast': ofi_fast,
            'ofi_slow': ofi_slow,
            'fast_buy_vol': round(fast_buy, 0),
            'fast_sell_vol': round(fast_sell, 0),
            'fast_trades': len(fast_window),
            'slow_buy_vol': round(slow_buy, 0),
            'slow_sell_vol': round(slow_sell, 0),
            'slow_trades': len(slow_window),
            'absorption_flag': absorption_flag,
            'absorption_side': absorption_side,
            'price_delta': round(price_delta, 4),
            'price_delta_ticks': price_delta_ticks,
            'tick_size': tick_size,
            'total_trades': total_trades,
            'ofi_series': ofi_series[-20:],
            'signal': signal,
            'sentiment': sentiment,
            'interpretation': interpretation,
            'source': 'databento_trades',
            'timestamp': now.isoformat()
        }

    @classmethod
    def _simulate_ofi(cls, symbol: str) -> Dict[str, Any]:
        """Fallback simulated OFI"""
        ofi_fast = round(float(np.random.uniform(-0.5, 0.5)), 4)
        ofi_slow = round(float(np.random.uniform(-0.3, 0.3)), 4)
        tick_size = SYMBOLS.get(symbol, {}).get('tick_size', 0.25)
        price_delta_ticks = round(float(np.random.uniform(0, 5)), 1)
        absorption = abs(ofi_fast) > 0.7 and price_delta_ticks < 2
        return {
            'ofi_fast': ofi_fast,
            'ofi_slow': ofi_slow,
            'fast_buy_vol': 250, 'fast_sell_vol': 250, 'fast_trades': 500,
            'slow_buy_vol': 1000, 'slow_sell_vol': 1000, 'slow_trades': 2000,
            'absorption_flag': absorption,
            'absorption_side': 'NONE',
            'price_delta': round(price_delta_ticks * tick_size, 4),
            'price_delta_ticks': price_delta_ticks,
            'tick_size': tick_size,
            'total_trades': 2000,
            'ofi_series': [],
            'signal': 'NEUTRAL',
            'sentiment': 'NEUTRAL',
            'interpretation': 'Dados simulados. Aguardar mercado aberto para OFI real.',
            'source': 'simulated',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

ofi_service = OFIService()

# ========== VOLUME PROFILE SERVICE ==========

class VolumeProfileService:
    """Calculate Volume Profile (POC, VAH, VAL) from OHLCV data.
    
    Uses tick-level bins for institutional precision. Each bin = 1 tick (0.25 for MES/MNQ).
    Value Area = 70% of total volume, expanded outward from POC.
    """

    @staticmethod
    def calculate_volume_profile(df: pd.DataFrame, num_bins: int = 0, value_area_pct: float = 0.70, tick_size: float = 0.25) -> Dict[str, Any]:
        """
        Calculate Volume Profile from OHLCV DataFrame using tick-level resolution.
        
        POC = Price level (tick) with most volume (Point of Control)
        VAH = Value Area High (top of 70% volume zone)
        VAL = Value Area Low (bottom of 70% volume zone)
        
        Args:
            df: OHLCV DataFrame with 'open', 'high', 'low', 'close', 'volume' columns
            num_bins: Ignored (kept for backward compat). Uses tick_size for bin resolution.
            value_area_pct: Percentage of total volume for Value Area (default 70%)
            tick_size: Minimum price increment (0.25 for MES/MNQ)
        """
        if df.empty or len(df) < 10:
            return {'poc': 0, 'vah': 0, 'val': 0, 'source': 'insufficient_data'}

        price_min = float(df['low'].min())
        price_max = float(df['high'].max())
        price_range = price_max - price_min

        if price_range <= 0:
            mid = float(df['close'].iloc[-1])
            return {'poc': mid, 'vah': mid, 'val': mid, 'source': 'no_range'}

        # Snap to tick grid: round down for min, round up for max
        grid_min = np.floor(price_min / tick_size) * tick_size
        grid_max = np.ceil(price_max / tick_size) * tick_size
        n_ticks = int(round((grid_max - grid_min) / tick_size)) + 1

        # Safety: cap bins at 2000 to prevent memory explosion on bad data
        if n_ticks > 2000:
            tick_size = (grid_max - grid_min) / 2000
            n_ticks = 2000

        # Build tick-level price grid
        tick_prices = np.array([grid_min + i * tick_size for i in range(n_ticks)])
        volume_at_tick = np.zeros(n_ticks)

        # Distribute each bar's volume across the ticks it covers
        for _, row in df.iterrows():
            bar_low = float(row['low'])
            bar_high = float(row['high'])
            bar_vol = float(row['volume'])
            if bar_vol <= 0 or bar_high <= bar_low:
                continue

            # Find tick indices covered by this bar
            low_idx = max(0, int(round((bar_low - grid_min) / tick_size)))
            high_idx = min(n_ticks - 1, int(round((bar_high - grid_min) / tick_size)))

            if low_idx == high_idx:
                volume_at_tick[low_idx] += bar_vol
            else:
                # Distribute proportionally across covered ticks
                bar_range = bar_high - bar_low
                for ti in range(low_idx, high_idx + 1):
                    tick_price = tick_prices[ti]
                    # Overlap: how much of this tick's range falls within the bar
                    tick_lo = tick_price - tick_size / 2
                    tick_hi = tick_price + tick_size / 2
                    overlap_lo = max(bar_low, tick_lo)
                    overlap_hi = min(bar_high, tick_hi)
                    if overlap_hi > overlap_lo:
                        overlap_pct = (overlap_hi - overlap_lo) / bar_range
                        volume_at_tick[ti] += bar_vol * overlap_pct

        total_volume = volume_at_tick.sum()
        if total_volume <= 0:
            mid = float(df['close'].iloc[-1])
            return {'poc': mid, 'vah': mid, 'val': mid, 'source': 'no_volume'}

        # POC = tick with highest volume
        poc_idx = int(np.argmax(volume_at_tick))
        poc = round(float(tick_prices[poc_idx]), 2)

        # Value Area: CBOT standard — expand from POC by comparing TWO price levels
        # at a time (above vs below). Add the pair with higher combined volume.
        # Repeat until 70% of total volume is captured.
        target_volume = total_volume * value_area_pct
        area_volume = volume_at_tick[poc_idx]
        low_idx = poc_idx
        high_idx = poc_idx

        while area_volume < target_volume:
            can_up = high_idx + 1 < n_ticks
            can_down = low_idx - 1 >= 0

            if not can_up and not can_down:
                break

            # Sum next TWO levels above (CBOT standard: compare pairs)
            up_sum = 0.0
            up_count = 0
            if can_up:
                up_sum += volume_at_tick[high_idx + 1]
                up_count = 1
                if high_idx + 2 < n_ticks:
                    up_sum += volume_at_tick[high_idx + 2]
                    up_count = 2

            # Sum next TWO levels below
            down_sum = 0.0
            down_count = 0
            if can_down:
                down_sum += volume_at_tick[low_idx - 1]
                down_count = 1
                if low_idx - 2 >= 0:
                    down_sum += volume_at_tick[low_idx - 2]
                    down_count = 2

            if up_sum == 0 and down_sum == 0:
                # No volume in adjacent levels — expand one step in any available direction
                if can_up:
                    high_idx += 1
                elif can_down:
                    low_idx -= 1
                else:
                    break
                continue

            if up_sum >= down_sum:
                # Expand upward by the number of levels we compared
                for _ in range(up_count):
                    if high_idx + 1 < n_ticks:
                        high_idx += 1
                        area_volume += volume_at_tick[high_idx]
            else:
                # Expand downward
                for _ in range(down_count):
                    if low_idx - 1 >= 0:
                        low_idx -= 1
                        area_volume += volume_at_tick[low_idx]

        vah = round(float(tick_prices[min(high_idx, n_ticks - 1)]), 2)
        val_level = round(float(tick_prices[max(low_idx, 0)]), 2)

        # Build profile histogram for visualization (top 20 levels)
        profile = []
        for i in range(n_ticks):
            if volume_at_tick[i] > 0:
                profile.append({
                    'price': round(float(tick_prices[i]), 2),
                    'volume': round(float(volume_at_tick[i]), 0),
                    'pct': round(float(volume_at_tick[i] / total_volume * 100), 1),
                    'is_poc': i == poc_idx,
                    'in_value_area': low_idx <= i <= high_idx
                })

        current_price = round(float(df['close'].iloc[-1]), 2)

        # Interpretation
        if current_price > vah:
            position = 'ABOVE_VA'
            interpretation = f'Preco (${current_price}) ACIMA da Value Area (${vah}): breakout bullish, volume de aceitacao necessario'
        elif current_price < val_level:
            position = 'BELOW_VA'
            interpretation = f'Preco (${current_price}) ABAIXO da Value Area (${val_level}): breakout bearish, rejeicao ou continuacao'
        else:
            position = 'INSIDE_VA'
            interpretation = f'Preco (${current_price}) DENTRO da Value Area (${val_level}-${vah}): rotacao, mean-reversion favorecida'

        # Signal based on position
        if position == 'INSIDE_VA':
            signal = 'NEUTRAL'
            score = 0.5
        elif position == 'ABOVE_VA':
            signal = 'CONFIRM'
            score = 0.7
        else:
            signal = 'CAUTION'
            score = 0.3

        return {
            'poc': poc,
            'vah': vah,
            'val': val_level,
            'current_price': current_price,
            'position': position,
            'interpretation': interpretation,
            'signal': signal,
            'score': round(score, 2),
            'total_volume': round(float(total_volume), 0),
            'value_area_volume_pct': round(float(area_volume / total_volume * 100), 1),
            'profile': profile,
            'source': 'databento_ohlcv'
        }

volume_profile_service = VolumeProfileService()

# ========== ECONOMIC CALENDAR SERVICE ==========

class EconomicCalendarService:
    """Detect real economic news events for VWAP anchoring"""
    CACHE_DURATION = 300  # 5 min cache
    _cache: Dict[str, Any] = {}
    _cache_time: Optional[datetime] = None

    # Major US economic release times (ET) — fallback when API unavailable
    KNOWN_RELEASE_TIMES_ET = [
        (8, 30),   # NFP, CPI, PPI, Retail Sales, GDP, Jobless Claims, PCE
        (10, 0),   # ISM, JOLTS, Consumer Sentiment, New Home Sales
        (14, 0),   # FOMC Rate Decision
        (14, 30),  # FOMC Press Conference
    ]

    @classmethod
    async def get_todays_events(cls) -> Dict[str, Any]:
        """Fetch today's high-impact economic events with LKG + Circuit Breaker"""
        now = datetime.now(timezone.utc)
        breaker = breaker_registry.get("economic_calendar", failure_threshold=3, recovery_timeout=120)

        if cls._cache and cls._cache_time:
            cache_age = (now - cls._cache_time).total_seconds()
            if cache_age < cls.CACHE_DURATION:
                return cls._cache

        if not breaker.can_execute():
            data, conf, _ = lkg_store.get("economic_calendar", cls.CACHE_DURATION)
            return data

        events = []
        source = 'none'

        # Primary: faireconomy.media (ForexFactory JSON mirror, free, rate-limited)
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; TradingBot/1.0)'}
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=headers) as client:
                resp = await client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
                if resp.status_code == 200:
                    raw = resp.json()
                    if isinstance(raw, list):
                        from zoneinfo import ZoneInfo
                        et_tz = ZoneInfo('America/New_York')
                        today_et = now.astimezone(et_tz).date()
                        for ev in raw:
                            impact = (ev.get('impact') or '').lower()
                            country = (ev.get('country') or '').upper()
                            # Only US RED (High impact) events
                            if country == 'USD' and impact == 'high':
                                # Parse date to check if it's today
                                date_str = ev.get('date', '')
                                try:
                                    from dateutil import parser as dtparser
                                    ev_dt = dtparser.parse(date_str)
                                    if ev_dt.tzinfo is None:
                                        ev_dt = ev_dt.replace(tzinfo=et_tz)
                                    ev_date = ev_dt.astimezone(et_tz).date()
                                    if ev_date != today_et:
                                        continue
                                    ev_utc = ev_dt.astimezone(timezone.utc)
                                    time_str = ev_dt.strftime('%I:%M%p').lstrip('0').lower()
                                except Exception:
                                    time_str = ''
                                    ev_utc = None

                                events.append({
                                    'title': ev.get('title') or 'Unknown',
                                    'time_str': time_str,
                                    'impact': 'high',
                                    'country': 'USD',
                                    'actual': ev.get('actual'),
                                    'forecast': ev.get('forecast'),
                                    'previous': ev.get('previous'),
                                    'timestamp_utc': ev_utc.isoformat() if ev_utc else None,
                                    'timestamp_et': ev_dt.strftime('%H:%M ET') if ev_utc else '',
                                })
                        source = 'forex_factory'
                elif resp.status_code == 429:
                    logger.info("FairEconomy rate limited (429) — falling back to jblanked")
        except Exception as e:
            logger.warning(f"FairEconomy calendar API error: {e}")

        # Fallback: jblanked.com (ForexFactory proxy, with currency+impact filter)
        if not events and source == 'none':
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                    resp = await client.get(
                        "https://www.jblanked.com/news/api/forex-factory/calendar/today/",
                        params={"currency": "USD", "impact": "High"}
                    )
                    if resp.status_code == 200:
                        raw = resp.json()
                        if isinstance(raw, list):
                            for ev in raw:
                                title = ev.get('title') or ev.get('Title') or ev.get('Name') or ev.get('event') or 'Unknown'
                                time_str = ev.get('time') or ev.get('Time') or ''
                                events.append({
                                    'title': title, 'time_str': time_str,
                                    'impact': 'high', 'country': 'USD',
                                    'actual': ev.get('actual') or ev.get('Actual'),
                                    'forecast': ev.get('forecast') or ev.get('Forecast'),
                                    'previous': ev.get('previous') or ev.get('Previous'),
                                })
                            source = 'jblanked_ff'
                    elif resp.status_code == 401:
                        logger.info("JBlanked calendar 401 — may need API key")
            except Exception as e:
                logger.warning(f"JBlanked calendar API error: {e}")

        if source == 'none':
            # Both APIs failed
            breaker.record_failure()
            data, conf, _ = lkg_store.get("economic_calendar", cls.CACHE_DURATION)
            return data
        
        breaker.record_success()

        # Parse event times to UTC datetime (for jblanked fallback events without timestamps)
        from zoneinfo import ZoneInfo
        et_tz = ZoneInfo('America/New_York')
        today_et = now.astimezone(et_tz).date()
        latest_news_utc = None
        latest_news_event = None

        for ev in events:
            # Skip if already has timestamp (faireconomy events come pre-parsed)
            if ev.get('timestamp_utc'):
                try:
                    from dateutil import parser as dtparser
                    ev_dt_utc = dtparser.parse(ev['timestamp_utc'])
                    if ev_dt_utc <= now:
                        if latest_news_utc is None or ev_dt_utc > latest_news_utc:
                            latest_news_utc = ev_dt_utc
                            latest_news_event = ev
                except Exception:
                    pass
                continue

            time_str = ev.get('time_str', '')
            try:
                # Parse time strings like "8:30am", "2:00pm", "10:00"
                ts = time_str.strip().lower().replace(' ', '')
                if 'am' in ts or 'pm' in ts:
                    t = datetime.strptime(ts, '%I:%M%p').time()
                elif ':' in ts:
                    parts = ts.split(':')
                    h, m = int(parts[0]), int(parts[1][:2])
                    t = datetime.min.replace(hour=h, minute=m).time()
                else:
                    continue

                ev_dt_et = datetime.combine(today_et, t, tzinfo=et_tz)
                ev_dt_utc = ev_dt_et.astimezone(timezone.utc)
                ev['timestamp_utc'] = ev_dt_utc.isoformat()
                ev['timestamp_et'] = ev_dt_et.strftime('%H:%M ET')

                # Track latest news that has already occurred
                if ev_dt_utc <= now:
                    if latest_news_utc is None or ev_dt_utc > latest_news_utc:
                        latest_news_utc = ev_dt_utc
                        latest_news_event = ev
            except Exception:
                continue

        result = {
            'events': events,
            'event_count': len(events),
            'has_news_today': len(events) > 0,
            'latest_released': latest_news_event,
            'latest_released_utc': latest_news_utc.isoformat() if latest_news_utc else None,
            'source': source,
            'timestamp': now.isoformat(),
            '_confidence': Confidence.LIVE.value,
            '_age_seconds': 0,
        }

        lkg_store.update("economic_calendar", result)
        cls._cache = result
        cls._cache_time = now
        return result

economic_calendar_service = EconomicCalendarService()

# ========== SESSION HELPERS ==========

def get_session_boundaries() -> Dict[str, datetime]:
    """Calculate session boundaries in UTC based on NY time, handling weekends"""
    from zoneinfo import ZoneInfo
    et_tz = ZoneInfo('America/New_York')
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(et_tz)

    today_et = now_et.date()
    weekday = now_et.weekday()  # 0=Mon, 5=Sat, 6=Sun

    # On weekends, roll back to Friday's session
    if weekday == 5:  # Saturday
        ref_date = today_et - timedelta(days=1)  # Friday
    elif weekday == 6:  # Sunday before market opens
        ref_date = today_et - timedelta(days=2)  # Friday
    else:
        ref_date = today_et

    # Current/most recent session: overnight start at 18:00 ET of day before ref_date
    if now_et.hour < 18 or weekday >= 5:
        overnight_start_date = ref_date - timedelta(days=1)
        # Skip weekends for overnight start
        if overnight_start_date.weekday() == 5:  # Saturday
            overnight_start_date -= timedelta(days=1)  # Friday
        elif overnight_start_date.weekday() == 6:  # Sunday
            overnight_start_date -= timedelta(days=2)  # Friday
    else:
        overnight_start_date = ref_date

    overnight_start_et = datetime.combine(overnight_start_date, datetime.min.replace(hour=18, minute=0).time(), tzinfo=et_tz)
    overnight_start_utc = overnight_start_et.astimezone(timezone.utc)

    # NY regular session on ref_date
    ny_open_et = datetime.combine(ref_date, datetime.min.replace(hour=9, minute=30).time(), tzinfo=et_tz)
    ny_close_et = datetime.combine(ref_date, datetime.min.replace(hour=16, minute=0).time(), tzinfo=et_tz)
    ny_open_utc = ny_open_et.astimezone(timezone.utc)
    ny_close_utc = ny_close_et.astimezone(timezone.utc)

    # Session end = 17:00 ET on ref_date
    session_end_et = datetime.combine(ref_date, datetime.min.replace(hour=17, minute=0).time(), tzinfo=et_tz)
    session_end_utc = session_end_et.astimezone(timezone.utc)

    # Previous session boundaries (for D-1 VP)
    # CME E-mini/Micro sessions:
    #   Monday session:  Sunday 18:00 ET → Monday 17:00 ET
    #   Tuesday session: Monday 18:00 ET → Tuesday 17:00 ET
    #   ...
    #   Friday session:  Thursday 18:00 ET → Friday 17:00 ET
    #   Weekend: Saturday CLOSED. CME reopens Sunday 18:00 ET.
    #
    # D-1 = the complete session that ended just before the current one started.
    # Current session started at overnight_start_date 18:00 ET.
    # D-1 ended at overnight_start_date 17:00 ET.
    # D-1 started the day before at 18:00 ET.
    #
    # CRITICAL: Sunday 18:00 ET IS a valid session start (CME opens Sunday evening).
    # Only Saturday is fully closed.

    prev_session_end_et = datetime.combine(overnight_start_date, datetime.min.replace(hour=17, minute=0).time(), tzinfo=et_tz)
    prev_session_end_utc = prev_session_end_et.astimezone(timezone.utc)

    d1_start_date = overnight_start_date - timedelta(days=1)
    # Only skip Saturday (CME fully closed). Sunday 18:00 ET is valid.
    if d1_start_date.weekday() == 5:  # Saturday → Friday
        d1_start_date -= timedelta(days=1)

    prev_overnight_start_et = datetime.combine(d1_start_date, datetime.min.replace(hour=18, minute=0).time(), tzinfo=et_tz)
    prev_overnight_start_utc = prev_overnight_start_et.astimezone(timezone.utc)

    # VP D-1 RTH window: 09:30-16:00 ET on overnight_start_date
    # (the RTH session of the calendar day when the CURRENT session started overnight)
    # Example: current session = Monday 18:00 ET → D-1 RTH = Monday 09:30-16:00 ET
    prev_rth_open_et  = datetime.combine(overnight_start_date, datetime.min.replace(hour=9, minute=30).time(), tzinfo=et_tz)
    prev_rth_close_et = datetime.combine(overnight_start_date, datetime.min.replace(hour=16, minute=0).time(), tzinfo=et_tz)
    prev_rth_open_utc  = prev_rth_open_et.astimezone(timezone.utc)
    prev_rth_close_utc = prev_rth_close_et.astimezone(timezone.utc)

    return {
        'overnight_start_utc': overnight_start_utc,
        'session_end_utc': session_end_utc,
        'ny_open_utc': ny_open_utc,
        'ny_close_utc': ny_close_utc,
        'prev_session_start_utc': prev_overnight_start_utc,
        'prev_session_end_utc': prev_session_end_utc,
        'prev_rth_open_utc': prev_rth_open_utc,
        'prev_rth_close_utc': prev_rth_close_utc,
        'now_utc': now_utc,
        'now_et': now_et.strftime('%H:%M ET'),
        'is_weekend': weekday >= 5,
        'ref_date': ref_date.isoformat(),
    }


def _get_current_session_type() -> str:
    """Returns 'rth' during Regular Trading Hours (09:30-16:00 ET weekdays), else 'globex'.
    Lightweight helper used by cache TTL logic across services."""
    from zoneinfo import ZoneInfo
    et_tz = ZoneInfo('America/New_York')
    now_et = datetime.now(timezone.utc).astimezone(et_tz)
    in_rth = (
        now_et.weekday() < 5
        and (now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 30))
        and now_et.hour < 16
    )
    return 'rth' if in_rth else 'globex'


# ========== ENHANCED VWAP SERVICE ==========

class VWAPService:
    """Calculate multiple VWAPs: Session NY, Intraday (overnight), Post-News, and Multi-day"""

    @staticmethod
    def calculate_vwap(df: pd.DataFrame, num_bands: int = 3, label: str = '') -> Dict[str, Any]:
        """
        Calculate VWAP + bands from OHLCV DataFrame.
        VWAP = cumsum(TP * Volume) / cumsum(Volume)   [TP = (H+L+C)/3]
        Bands = VWAP +/- n * VWSD   [Volume-Weighted Standard Deviation]
        VWSD = sqrt(Σ(vol_i × (TP_i - VWAP_i)²) / Σ(vol_i))  — expanding, session-anchored
        num_bands=3 includes ±1σ, ±2σ, ±3σ
        """
        if df.empty or len(df) < 3:
            return {'vwap': 0, 'source': 'insufficient_data', 'label': label, 'candle_count': 0}

        typical_price = (df['high'] + df['low'] + df['close']) / 3
        volume = df['volume'].replace(0, np.nan).fillna(1)

        cum_tp_vol = (typical_price * volume).cumsum()
        cum_vol = volume.cumsum()
        vwap_series = cum_tp_vol / cum_vol

        # Volume-Weighted Standard Deviation (VWSD):
        # σ² = Σ(vol_i × (TP_i - VWAP_i)²) / Σ(vol_i)   [expanding, session-anchored]
        # Cada barra pondera pelo seu volume — barras de abertura com volume alto
        # dominam o σ, reflectindo correctamente a dispersão real ponderada pelo fluxo.
        deviation = typical_price - vwap_series
        vw_variance = (volume * deviation ** 2).cumsum() / cum_vol
        std_series = np.sqrt(vw_variance.clip(lower=0)).fillna(0)

        vwap = round(float(vwap_series.iloc[-1]), 2)
        std_val = float(std_series.iloc[-1]) if len(std_series) >= 3 else 0.0
        current_price = round(float(df['close'].iloc[-1]), 2)

        bands = {}
        for n in range(1, num_bands + 1):
            bands[f'upper_{n}'] = round(vwap + n * std_val, 2)
            bands[f'lower_{n}'] = round(vwap - n * std_val, 2)

        distance = current_price - vwap
        distance_pct = round(distance / vwap * 100, 3) if vwap > 0 else 0
        distance_std = round(distance / std_val, 2) if std_val > 0 else 0

        if current_price > bands.get('upper_3', bands.get('upper_2', vwap)):
            position = 'ABOVE_3STD'
            signal = 'EXTREME'
        elif current_price > bands.get('upper_2', vwap):
            position = 'ABOVE_2STD'
            signal = 'CAUTION'
        elif current_price > bands.get('upper_1', vwap):
            position = 'ABOVE_1STD'
            signal = 'CONFIRM'
        elif current_price > vwap:
            position = 'ABOVE_VWAP'
            signal = 'CONFIRM'
        elif current_price > bands.get('lower_1', vwap):
            position = 'BELOW_VWAP'
            signal = 'CAUTION'
        elif current_price > bands.get('lower_2', vwap):
            position = 'BELOW_1STD'
            signal = 'CAUTION'
        elif current_price > bands.get('lower_3', bands.get('lower_2', vwap)):
            position = 'BELOW_2STD'
            signal = 'CONFIRM'
        else:
            position = 'BELOW_3STD'
            signal = 'EXTREME'

        return {
            'vwap': vwap,
            'std': round(std_val, 2),
            'upper_1': bands.get('upper_1', vwap),
            'upper_2': bands.get('upper_2', vwap),
            'upper_3': bands.get('upper_3', round(vwap + 3 * std_val, 2)),
            'lower_1': bands.get('lower_1', vwap),
            'lower_2': bands.get('lower_2', vwap),
            'lower_3': bands.get('lower_3', round(vwap - 3 * std_val, 2)),
            'current_price': current_price,
            'distance': round(distance, 2),
            'distance_pct': distance_pct,
            'distance_std': distance_std,
            'position': position,
            'signal': signal,
            'label': label,
            'candle_count': len(df),
            'source': 'databento_ohlcv'
        }

    @classmethod
    async def calculate_session_vwaps(cls, symbol: str, news_time_utc: Optional[datetime] = None) -> Dict[str, Any]:
        """Calculate all three VWAP variants from DataBento OHLCV data"""
        sessions = get_session_boundaries()
        now_utc = sessions['now_utc']

        # Use session_end or now-2h (whichever is earlier) as safe end for DataBento
        safe_end = min(sessions.get('session_end_utc', now_utc), now_utc - timedelta(hours=2))
        start_fetch = sessions['prev_session_start_utc']

        try:
            df = await databento_service.get_historical_data(
                symbol, '1M', start_fetch.isoformat(), safe_end.isoformat()
            )
        except Exception as e:
            logger.error(f"Error fetching session VWAP data: {e}")
            df = pd.DataFrame()

        result = {
            'session_ny': None,
            'globex': None,
            'post_news': None,
            'sessions': {
                'overnight_start': sessions['overnight_start_utc'].isoformat(),
                'ny_open': sessions['ny_open_utc'].isoformat(),
                'ny_close': sessions['ny_close_utc'].isoformat(),
                'now_et': sessions['now_et'],
                'is_weekend': sessions.get('is_weekend', False),
                'ref_date': sessions.get('ref_date', ''),
            }
        }

        if df.empty:
            return result

        # Ensure index is datetime for filtering
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'timestamp' in df.columns:
                df = df.set_index('timestamp')
            elif 'date' in df.columns:
                df = df.set_index('date')
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')

        # 1. VWAP Globex (nasce às 18:00 ET — sessão noturna inteira)
        globex_df = df[df.index >= sessions['overnight_start_utc']]
        if len(globex_df) >= 3:
            result['globex'] = cls.calculate_vwap(globex_df, label='VWAP Globex')

        # 2. VWAP NY / RTH (nasce às 09:30 ET — sessão regular)
        ny_df = df[(df.index >= sessions['ny_open_utc']) & (df.index <= sessions['ny_close_utc'])]
        if len(ny_df) >= 3:
            result['session_ny'] = cls.calculate_vwap(ny_df, label='VWAP NY (RTH)')

        # 3. Post-News VWAP (from news release time)
        if news_time_utc:
            news_df = df[df.index >= news_time_utc]
            if len(news_df) >= 3:
                result['post_news'] = cls.calculate_vwap(news_df, label='Post-News')

        return result

    @classmethod
    def calculate_session_vwaps_from_df(cls, df: pd.DataFrame, sessions: Dict, news_time_utc: Optional[datetime] = None) -> Dict[str, Any]:
        """Calculate VWAPs from pre-fetched DataFrame (no additional API calls).
        
        Two-session architecture with strict resets:
        - VWAP Globex: nasce às 18:00 ET, morre às 09:30 ET
        - VWAP NY (RTH): nasce às 09:30 ET, morre às 18:00 ET
        """
        result = {
            'session_ny': None,
            'globex': None,
            'post_news': None,
            'sessions': {
                'overnight_start': sessions['overnight_start_utc'].isoformat(),
                'ny_open': sessions['ny_open_utc'].isoformat(),
                'ny_close': sessions['ny_close_utc'].isoformat(),
                'now_et': sessions['now_et'],
                'is_weekend': sessions.get('is_weekend', False),
                'ref_date': sessions.get('ref_date', ''),
            }
        }

        if df.empty:
            return result

        if not isinstance(df.index, pd.DatetimeIndex):
            if 'timestamp' in df.columns:
                df = df.set_index('timestamp')
            elif 'date' in df.columns:
                df = df.set_index('date')
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')

        # VWAP Globex (nasce às 18:00 ET — toda a sessão noturna)
        globex_df = df[df.index >= sessions['overnight_start_utc']]
        if len(globex_df) >= 3:
            result['globex'] = cls.calculate_vwap(globex_df, label='VWAP Globex')

        # VWAP NY / RTH (nasce às 09:30 ET — sessão regular NYSE)
        ny_df = df[(df.index >= sessions['ny_open_utc']) & (df.index <= sessions['ny_close_utc'])]
        if len(ny_df) >= 3:
            result['session_ny'] = cls.calculate_vwap(ny_df, label='VWAP NY (RTH)')

        if news_time_utc:
            news_df = df[df.index >= news_time_utc]
            if len(news_df) >= 3:
                result['post_news'] = cls.calculate_vwap(news_df, label='Post-News')

        return result

vwap_service = VWAPService()

# ========== SESSION VOLUME PROFILE SERVICE ==========

class SessionVolumeProfileService:
    """Calculate Daily VP, Previous Day VP using session boundaries.
    
    Optimizations:
    - VP D-1: Cached per session (key: symbol_prev_session_start). Never re-fetched
      for the same session since D-1 data is immutable historical data.
    - VP Session (daily/rth): Cached for 5 minutes to avoid re-computation on every request.
    """
    # VP D-1 persistent cache — keyed by "{symbol}_{prev_rth_open_utc_iso}"
    _d1_cache: Dict[str, Any] = {}
    
    # VP Session cache (daily_vp + rth_vp) — 5 min TTL
    _session_vps_cache: Dict[str, Dict] = {}
    _session_vps_cache_time: Dict[str, datetime] = {}
    SESSION_VP_CACHE_TTL = 300  # 5 minutes

    @classmethod
    async def calculate_session_vps(cls, symbol: str) -> Dict[str, Any]:
        """Calculate session VPs following the two-session architecture:
        - VP Globex (18:00-09:30 ET): VP Session during overnight — from LIVE trades
        - VP NY / RTH (09:30-16:00 ET): VP Session during NY — from LIVE trades
        - VP D-1: RTH do pregão anterior (09:30-16:00 ET exclusivamente) — from historical API
        
        LIVE trades from the WebSocket buffer are used for VP Session (current session).
        Historical API is only used for VP D-1.
        Snapshot seed: _session_vps_cache seeded on startup; used as fallback when
        the live buffer has fewer than 5 candles (first ~5 min after restart).
        """
        sessions = get_session_boundaries()
        _now_utc  = datetime.now(timezone.utc)
        _vps_key  = f"vps_{symbol}"

        result = {
            'daily_vp': None,     # VP of current session (Globex or NY) — from live
            'rth_vp': None,       # VP from NY open only (09:30-16:00) — from live
            'prev_day_vp': None,  # VP D-1 RTH (09:30-16:00 ET pregão anterior) — from historical
        }

        # ── VP Session from LIVE trades buffer ──
        buf = live_data_service.buffers.get(symbol)
        _has_live_vp = False  # set to True when ≥5 live candles are computed

        if buf and len(buf.trades) > 0:
            # Build 1M candles from live trades since session start
            # Detect timestamp format: DataBento ts_event can be nanoseconds or seconds
            trades_snapshot = list(buf.trades)  # snapshot to avoid deque mutation during iteration
            sample_ts = trades_snapshot[-1].ts
            if sample_ts > 1e15:
                ts_divisor = 1_000_000_000  # nanoseconds
            elif sample_ts > 1e12:
                ts_divisor = 1_000  # milliseconds
            else:
                ts_divisor = 1  # already seconds

            overnight_start_s = int(sessions['overnight_start_utc'].timestamp())

            candles = {}  # minute_ts_s → {open, high, low, close, volume}
            trade_count_session = 0
            for t in trades_snapshot:
                ts_s = t.ts // ts_divisor if ts_divisor > 1 else t.ts
                if ts_s < overnight_start_s:
                    continue
                trade_count_session += 1
                minute_ts = ts_s - (ts_s % 60)
                if minute_ts not in candles:
                    candles[minute_ts] = {
                        'open': t.price, 'high': t.price,
                        'low': t.price, 'close': t.price, 'volume': t.size,
                    }
                else:
                    c = candles[minute_ts]
                    c['high'] = max(c['high'], t.price)
                    c['low'] = min(c['low'], t.price)
                    c['close'] = t.price
                    c['volume'] += t.size

            logger.info(f"VP calc: {symbol} buf_trades={len(buf.trades)} session_trades={trade_count_session} candles={len(candles)} ts_div={ts_divisor}")

            if len(candles) >= 5:
                live_df = pd.DataFrame.from_dict(candles, orient='index')
                live_df.index = pd.to_datetime(live_df.index, unit='s', utc=True)
                live_df = live_df.sort_index()

                # Daily VP = all candles since overnight start (VP Session for current session)
                daily_vp = volume_profile_service.calculate_volume_profile(live_df)
                daily_vp['session'] = 'live'
                daily_vp['session_start'] = sessions['overnight_start_utc'].isoformat()
                daily_vp['candle_count'] = len(live_df)
                result['daily_vp'] = daily_vp
                _has_live_vp = True

                # RTH VP = candles since NY open only
                rth_df = live_df[live_df.index >= pd.Timestamp(sessions['ny_open_utc'])]
                if len(rth_df) >= 5:
                    rth_vp = volume_profile_service.calculate_volume_profile(rth_df)
                    rth_vp['session'] = 'rth_live'
                    rth_vp['session_start'] = sessions['ny_open_utc'].isoformat()
                    rth_vp['candle_count'] = len(rth_df)
                    result['rth_vp'] = rth_vp

        # ── VP Session fallback: use snapshot seed when < 5 live candles ─────
        # Covers the first ~5 min after restart before the live buffer accumulates.
        if not _has_live_vp and _vps_key in cls._session_vps_cache:
            _seed      = cls._session_vps_cache[_vps_key]
            _seed_ts   = cls._session_vps_cache_time.get(_vps_key, _now_utc)
            _seed_age  = (_now_utc - _seed_ts).total_seconds()
            if _seed_age < 1800:  # keep seed valid for up to 30 min
                result['daily_vp'] = result['daily_vp'] or _seed.get('daily_vp')
                result['rth_vp']   = result['rth_vp']   or _seed.get('rth_vp')
                logger.debug(
                    "VP Session %s: using snapshot seed (age=%.0fs, <5 live candles)",
                    symbol, _seed_age,
                )

        # ── Proveniência VP Session — auditoria de qualidade de dados ──────────
        # "live"  → ≥5 candles ao vivo calculados neste ciclo
        # "seed"  → valor do snapshot anterior (warm-start), < 5 candles live
        # "none"  → sem dados disponíveis (buffer vazio ou sessão sem negócios)
        if _has_live_vp:
            result["_vp_session_source"] = "live"
        elif not _has_live_vp and _vps_key in cls._session_vps_cache:
            _seed_ts  = cls._session_vps_cache_time.get(_vps_key, _now_utc)
            _seed_age = (_now_utc - _seed_ts).total_seconds()
            result["_vp_session_source"] = "seed" if _seed_age < 1800 else "none"
        else:
            result["_vp_session_source"] = "none"

        # ── Update seed cache whenever we have live VP (so next seed is fresh) ─
        if _has_live_vp:
            cls._session_vps_cache[_vps_key]      = result.copy()
            cls._session_vps_cache_time[_vps_key] = _now_utc

        # ── VP D-1 from historical API — RTH only (09:30-16:00 ET do dia anterior) ──
        # REGRA: VP D-1 usa EXCLUSIVAMENTE dados RTH do pregão anterior.
        # NÃO inclui dados Globex/overnight. Janela: prev_rth_open_utc → prev_rth_close_utc.
        # OPTIMIZATION: VP D-1 é imutável para a sessão inteira — cache permanente por chave.
        d1_cache_key = f"{symbol}_{sessions['prev_rth_open_utc'].isoformat()}"
        if d1_cache_key in cls._d1_cache:
            result['prev_day_vp'] = cls._d1_cache[d1_cache_key]
            logger.debug("VP D-1 cache HIT for %s (RTH key: %s)", symbol, d1_cache_key)
        else:
            try:
                prev_rth_start = sessions['prev_rth_open_utc']
                prev_rth_end   = sessions['prev_rth_close_utc']
                hist_df = await databento_service.get_historical_data(
                    symbol, '1M',
                    prev_rth_start.isoformat(),
                    prev_rth_end.isoformat(),
                )
                if not hist_df.empty:
                    if not isinstance(hist_df.index, pd.DatetimeIndex):
                        if 'timestamp' in hist_df.columns:
                            hist_df = hist_df.set_index('timestamp')
                        elif 'date' in hist_df.columns:
                            hist_df = hist_df.set_index('date')
                    if hist_df.index.tz is None:
                        hist_df.index = hist_df.index.tz_localize('UTC')

                    prev_df = hist_df[
                        (hist_df.index >= prev_rth_start) &
                        (hist_df.index < prev_rth_end)
                    ]
                    tick_sz = SYMBOLS.get(symbol, {}).get('tick_size', 0.25)
                    if len(prev_df) >= 5:
                        prev_vp = volume_profile_service.calculate_volume_profile(prev_df, tick_size=tick_sz)
                        prev_vp['session']       = 'prev_day_rth'
                        prev_vp['session_start'] = prev_rth_start.isoformat()
                        prev_vp['session_end']   = prev_rth_end.isoformat()
                        # Extremos absolutos RTH D-1 — máximo e mínimo do pregão anterior
                        prev_vp['d1_high'] = float(prev_df['high'].max()) if 'high' in prev_df.columns else 0.0
                        prev_vp['d1_low']  = float(prev_df['low'].min())  if 'low'  in prev_df.columns else 0.0
                        result['prev_day_vp'] = prev_vp
                        cls._d1_cache[d1_cache_key] = prev_vp
                        logger.info("VP D-1 RTH COMPUTED and cached for %s (key: %s, candles=%d, d1_high=%.2f, d1_low=%.2f)",
                                    symbol, d1_cache_key, len(prev_df), prev_vp['d1_high'], prev_vp['d1_low'])
                    else:
                        logger.warning("VP D-1 RTH: candles insuficientes para %s (n=%d, need>=5)", symbol, len(prev_df))
            except Exception as e:
                logger.error(f"Error fetching D-1 VP RTH data for {symbol}: {e}")

        # ── Proveniência VP D-1 ───────────────────────────────────────────────
        # "live" → calculado da API histórica DataBento neste ciclo ou cache imutável
        # "none" → API falhou ou histórico insuficiente
        result["_vp_d1_source"] = "live" if result.get("prev_day_vp") else "none"

        return result

    @classmethod
    def calculate_session_vps_from_df(cls, df: pd.DataFrame, sessions: Dict, symbol: str = 'MES') -> Dict[str, Any]:
        """Calculate VPs from pre-fetched DataFrame (no additional API calls).
        
        Two-session architecture:
        - VP Globex (daily_vp): 18:00 ET → agora (toda a sessão desde o nascimento)
        - VP RTH (rth_vp): 09:30 ET → 16:00 ET (apenas dados da sessão NY)
        - VP D-1 (prev_day_vp): RTH do pregão anterior (09:30-16:00 ET exclusivamente)
        
        Optimizations:
        - Full result cached for 5 min (VP Session changes slowly).
        - VP D-1 cached permanently per session (immutable historical data).
        """
        now = datetime.now(timezone.utc)
        vps_cache_key = f"vps_{symbol}"

        # ── 5-min Session VP cache ──
        if vps_cache_key in cls._session_vps_cache and vps_cache_key in cls._session_vps_cache_time:
            cache_age = (now - cls._session_vps_cache_time[vps_cache_key]).total_seconds()
            if cache_age < cls.SESSION_VP_CACHE_TTL:
                logger.debug("VP Session cache HIT for %s (age=%.0fs)", symbol, cache_age)
                return cls._session_vps_cache[vps_cache_key]

        # Tick size per instrument (CME E-mini/Micro)
        TICK_SIZES = {'MES': 0.25, 'MNQ': 0.25, 'ES': 0.25, 'NQ': 0.25}
        tick_sz = TICK_SIZES.get(symbol, 0.25)

        result = {
            'daily_vp': None,
            'rth_vp': None,
            'prev_day_vp': None,
        }

        if df.empty:
            return result

        if not isinstance(df.index, pd.DatetimeIndex):
            if 'timestamp' in df.columns:
                df = df.set_index('timestamp')
            elif 'date' in df.columns:
                df = df.set_index('date')
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')

        # VP Globex / Full Session (desde 18:00 ET)
        daily_df = df[df.index >= sessions['overnight_start_utc']]
        if len(daily_df) >= 5:
            daily_vp = volume_profile_service.calculate_volume_profile(daily_df, tick_size=tick_sz)
            daily_vp['session'] = 'daily'
            daily_vp['session_start'] = sessions['overnight_start_utc'].isoformat()
            result['daily_vp'] = daily_vp

        # VP RTH / NY (nasce às 09:30 ET, morre às 16:00 ET)
        rth_df = df[(df.index >= sessions['ny_open_utc']) & (df.index <= sessions['ny_close_utc'])]
        if len(rth_df) >= 5:
            rth_vp = volume_profile_service.calculate_volume_profile(rth_df, tick_size=tick_sz)
            rth_vp['session'] = 'rth'
            rth_vp['session_start'] = sessions['ny_open_utc'].isoformat()
            rth_vp['session_end'] = sessions['ny_close_utc'].isoformat()
            result['rth_vp'] = rth_vp

        # VP D-1 (from_df) — RTH only (09:30-16:00 ET do dia anterior)
        # REGRA: VP D-1 usa EXCLUSIVAMENTE dados RTH — igual ao path API.
        d1_cache_key = f"{symbol}_{sessions['prev_rth_open_utc'].isoformat()}"
        if d1_cache_key in cls._d1_cache:
            result['prev_day_vp'] = cls._d1_cache[d1_cache_key]
        else:
            prev_rth_start = sessions['prev_rth_open_utc']
            prev_rth_end   = sessions['prev_rth_close_utc']
            prev_df = df[(df.index >= prev_rth_start) & (df.index < prev_rth_end)]
            if len(prev_df) >= 5:
                prev_vp = volume_profile_service.calculate_volume_profile(prev_df, tick_size=tick_sz)
                prev_vp['session']       = 'prev_day_rth'
                prev_vp['session_start'] = prev_rth_start.isoformat()
                prev_vp['session_end']   = prev_rth_end.isoformat()
                # Extremos absolutos RTH D-1 — máximo e mínimo do pregão anterior
                prev_vp['d1_high'] = float(prev_df['high'].max()) if 'high' in prev_df.columns else 0.0
                prev_vp['d1_low']  = float(prev_df['low'].min())  if 'low'  in prev_df.columns else 0.0
                result['prev_day_vp'] = prev_vp
                cls._d1_cache[d1_cache_key] = prev_vp
                logger.info("VP D-1 RTH COMPUTED and cached (from_df) for %s (key: %s, candles=%d, d1_high=%.2f, d1_low=%.2f)",
                            symbol, d1_cache_key, len(prev_df), prev_vp['d1_high'], prev_vp['d1_low'])
            else:
                logger.warning("VP D-1 RTH (from_df): candles insuficientes para %s (n=%d)", symbol, len(prev_df))

        # Store in session VP cache
        cls._session_vps_cache[vps_cache_key] = result
        cls._session_vps_cache_time[vps_cache_key] = now

        return result

session_vp_service = SessionVolumeProfileService()

# ========== VIX & GAMMA REAL DATA SERVICE ==========


# ========== API ENDPOINTS ==========

@api_router.get("/")
async def root():
    return {"message": "Quantum Trading System API", "version": "1.0.0"}

@api_router.get("/health")
async def health():
    return {
        "status": "healthy" if _backend_ready else "warming_up",
        "ready": _backend_ready,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@api_router.get("/ready")
async def readiness():
    """Kubernetes-style readiness probe.
    Returns 200 once warm_caches() has completed; 503 before that.
    Clients can poll this endpoint with exponential backoff during startup.
    """
    if not _backend_ready:
        raise HTTPException(
            status_code=503,
            detail="Backend is warming up — please retry in a few seconds",
            headers={"Retry-After": "5"},
        )
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}


@api_router.get("/live-data/status")
async def get_live_data_status():
    """Get DataBento Live WebSocket status and per-symbol metrics."""
    return live_data_service.get_status()


@api_router.get("/feed/health")
async def get_feed_health():
    """Get current feed health state for all symbols (real-time)."""
    return convert_numpy_types(feed_health_monitor.current_state)


@api_router.get("/system/status")
async def get_system_status():
    """Consolidated system status — feed, engine, session, auto-trader.
    Returns a single human-readable summary + per-subsystem breakdown.
    Designed to be polled every 15s by the frontend status panel.
    """
    now_ts = datetime.now(timezone.utc).isoformat()

    # ── 1. Backend readiness ──────────────────────────────────────────
    backend_ok = _backend_ready

    # ── 2. Feed health (already computed by FeedHealthMonitor) ────────
    raw_feed = convert_numpy_types(feed_health_monitor.current_state)
    feed: dict = {}
    for sym in ("MNQ", "MES"):
        s = raw_feed.get(sym, {})
        feed[sym] = {
            "state":           s.get("state", "UNKNOWN"),
            "reason":          s.get("reason", ""),
            "connected":       s.get("connected", False),
            "trades_received": s.get("trades_received", 0),
            "last_trade_age_s": s.get("last_trade_age_s"),
            "buffer_pct":      round(s.get("buffer_size", 0) / 1000, 1),
        }

    # ── 3. Trading session ────────────────────────────────────────────
    try:
        session_info = trading_calendar.get_session_info()
        cme_session  = session_info.get("cme_session", "unknown")
        is_weekend   = session_info.get("is_weekend", False)
        is_rth       = cme_session == "rth"
    except Exception:
        cme_session = "unknown"
        is_weekend  = False
        is_rth      = False

    # ── 4. Scalp engine last signal per symbol ────────────────────────
    from routes.scalp import _scalp_engine as _se, _scalp_auto_trader as _sat
    signals: dict = {}
    if _se is not None:
        for sym in ("MNQ", "MES"):
            sig = _se.get_last_signal(sym)
            if sig is not None:
                signals[sym] = {
                    "status":    sig.scalp_status,
                    "direction": sig.s1_direction,
                    "quality":   getattr(sig, "s2_quality", None) or getattr(sig, "zone_quality", None),
                    "score":     round(sig.score, 2) if getattr(sig, "score", None) else None,
                    "mode":      sig.mode,
                    "block_reason": getattr(sig, "block_reason", None),
                }
            else:
                signals[sym] = {"status": "PENDING", "direction": None, "quality": None, "score": None, "mode": None, "block_reason": None}
    else:
        for sym in ("MNQ", "MES"):
            signals[sym] = {"status": "PENDING", "direction": None, "quality": None, "score": None, "mode": None, "block_reason": None}

    # ── 5. Auto-trader state ──────────────────────────────────────────
    try:
        at_status = _sat.get_status() if _sat is not None else {}
        at_running = at_status.get("status") == "RUNNING"
        at_stats   = at_status.get("session_stats") or {}
        at_info    = {
            "running":               at_running,
            "trades_today":          at_stats.get("trades", 0),
            "pnl_today_usd":         round(at_stats.get("pnl_usd", 0.0), 2),
            "circuit_breaker":       at_stats.get("circuit_breaker_triggered", False),
            "open_positions":        at_status.get("open_positions", 0),
            "total_contracts_open":  at_status.get("total_contracts_open", 0),
        }
    except Exception:
        at_info = {"running": False, "trades_today": 0, "pnl_today_usd": 0.0, "circuit_breaker": False}

    # ── 6. Derive overall_health + summary_message ────────────────────
    feed_states = {s: feed[s]["state"] for s in ("MNQ", "MES")}
    any_ghost   = any(v in ("FEED_GHOST", "FEED_LOW_QUALITY") for v in feed_states.values())
    any_warming = any(v == "WARMING_UP" for v in feed_states.values())
    any_active  = any(signals[s]["status"] == "ACTIVE_SIGNAL" for s in ("MNQ", "MES"))
    any_blocked = any(signals[s]["status"] == "BLOCKED" for s in ("MNQ", "MES"))
    market_closed = all(signals[s]["status"] in ("MARKET_CLOSED", "PENDING") for s in ("MNQ", "MES"))

    if not backend_ok:
        overall = "warning"
        summary = "Backend a inicializar — aguarde alguns segundos"
    elif any_ghost:
        overall = "error"
        bad = [s for s in ("MNQ", "MES") if feed[s]["state"] in ("FEED_GHOST", "FEED_LOW_QUALITY")]
        summary = f"Feed DataBento instável ({', '.join(bad)}) — motor em pausa"
    elif is_weekend:
        overall = "idle"
        summary = "Fim de semana — mercados fechados, DataBento em standby"
    elif any_warming:
        warming = [s for s in ("MNQ", "MES") if feed[s]["state"] == "WARMING_UP"]
        overall = "warning"
        summary = f"Feed a aquecer ({', '.join(warming)}) — aguardando trades suficientes"
    elif market_closed and not is_rth and cme_session not in ("globex",):
        overall = "idle"
        cme_label = {"closed": "Fechado"}.get(cme_session, cme_session.upper())
        summary = f"Sessão {cme_label} — mercado encerrado"
    elif any_active:
        overall = "ok"
        active_syms = [s for s in ("MNQ", "MES") if signals[s]["status"] == "ACTIVE_SIGNAL"]
        dirs = " / ".join(f"{s} {signals[s]['direction']}" for s in active_syms if signals[s]['direction'])
        summary = f"Sinal ativo — {dirs}" if dirs else f"Sinal ativo — {', '.join(active_syms)}"
    elif any_blocked:
        overall = "warning"
        reasons = {s: signals[s].get("block_reason") for s in ("MNQ", "MES") if signals[s]["status"] == "BLOCKED"}
        reason_str = next((v for v in reasons.values() if v), "filtros de qualidade")
        summary = f"Sinal bloqueado — {reason_str}"
    elif is_rth:
        overall = "ok"
        summary = "Sistema operacional — RTH ativo, aguardando sinal"
    elif cme_session == "globex":
        overall = "ok"
        summary = "Sistema operacional — Globex ativo, aguardando sinal"
    else:
        overall = "ok"
        summary = "Sistema operacional — sem sinal no momento"

    if at_info["circuit_breaker"]:
        overall = "error"
        summary = "⚠ Circuit breaker ativo — trading pausado por perdas consecutivas"

    # ── 7. Atlas storage status (lightweight — apenas lê cache em memória) ─
    from services.atlas_storage_monitor import get_storage_status as _get_st
    _st = _get_st()
    storage_level = _st.get("level", "OK")
    storage_info  = {
        "level":      storage_level,
        "pct_used":   _st.get("pct_used"),
        "total_mb":   _st.get("total_mb"),
        "limit_mb":   _st.get("limit_mb"),
        "checked_at": _st.get("checked_at"),
    }
    if storage_level == "CRITICAL":
        overall = "error"
        summary = f"🔴 Atlas CRÍTICO — {_st.get('pct_used', 0) * 100:.0f}% do espaço usado"
    elif storage_level == "HIGH" and overall not in ("error",):
        overall = "warning"

    return {
        "timestamp":     now_ts,
        "overall":       overall,   # "ok" | "warning" | "error" | "idle"
        "summary":       summary,
        "backend_ready": backend_ok,
        "session": {
            "cme_session": cme_session,
            "is_weekend":  is_weekend,
            "is_rth":      is_rth,
        },
        "feed":          feed,
        "signals":       signals,
        "auto_trader":   at_info,
        "atlas_storage": storage_info,
    }


@api_router.get("/feed/health/report/{symbol}")
async def get_feed_health_report(symbol: str, hours: int = Query(default=24, ge=1, le=168)):
    """Get historical feed quality report for a symbol.
    Used by Auto-Tune section to evaluate DataBento feed reliability."""
    if symbol not in ('MNQ', 'MES'):
        return {"error": "Symbol not supported"}
    report = await feed_health_monitor.get_report(symbol, hours=hours)
    return convert_numpy_types(report)


@api_router.get("/symbols")
async def get_symbols():
    """Get available trading symbols"""
    return {"symbols": SYMBOLS}

@api_router.get("/indicator-weights")
async def get_indicator_weights():
    """Get indicator weights configuration"""
    return {"weights": INDICATOR_WEIGHTS}

@api_router.get("/vix")
async def get_vix_data():
    """Get real-time VIX data from Yahoo Finance"""
    try:
        vix_data = await volatility_service.get_vix_data()
        return {"vix": vix_data}
    except Exception as e:
        logger.error(f"Error fetching VIX: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/vxn")
async def get_vxn_data():
    """Get real-time VXN (Nasdaq-100 Volatility Index) data from Yahoo Finance"""
    try:
        vxn_data = await volatility_service.get_vxn_data()
        return {"vxn": vxn_data}
    except Exception as e:
        logger.error(f"Error fetching VXN: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/gamma/{symbol}")
async def get_gamma_exposure(symbol: str):
    """Get Gamma Exposure (GEX) data for a symbol"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        gamma_data = await volatility_service.get_gamma_exposure(symbol)
        return convert_numpy_types({"symbol": symbol, "gamma": gamma_data})
    except Exception as e:
        logger.error(f"Error fetching GAMMA: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/trading-calendar/session")
async def get_trading_session():
    """Get current trading session info (NYSE hours, DST, holidays)"""
    info = trading_calendar.get_session_info()
    return convert_numpy_types(info)

@api_router.get("/trading-calendar/news")
async def get_news_events():
    """Get today's economic news events and blackout windows"""
    config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    config = AutoTradingConfig(**(config_doc or {}))

    events = await trading_calendar.get_news_blackouts()
    is_blackout, blackout_info = trading_calendar.check_news_blackout(
        events,
        minutes_before=config.news_blackout_minutes_before,
        minutes_after=config.news_blackout_minutes_after,
    )
    upcoming = trading_calendar.get_upcoming_events(events)
    cal_status = trading_calendar.get_calendar_status()

    return {
        'events': events,
        'is_blackout': is_blackout,
        'blackout_info': blackout_info,
        'upcoming': upcoming,
        'config': {
            'news_blackout_enabled': config.news_blackout_enabled,
            'minutes_before': config.news_blackout_minutes_before,
            'minutes_after': config.news_blackout_minutes_after,
        },
        'calendar_status': cal_status,
    }

@api_router.get("/trading-calendar/status")
async def get_trading_status():
    """Get comprehensive trading status (hours + news + EOD)"""
    config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    config = AutoTradingConfig(**(config_doc or {}))

    session = trading_calendar.get_session_info()

    # Check auto hours (NYSE or Globex)
    globex_ok, globex_reason = trading_calendar.is_within_globex_auto_hours()
    if config.auto_hours_mode:
        hours_ok, hours_reason = trading_calendar.is_within_auto_trading_hours()
    else:
        # Manual mode: use config hours
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo('America/New_York'))
        h = now_et.hour
        hours_ok = config.trading_start_hour <= h < config.trading_end_hour
        hours_reason = 'Dentro do horario manual' if hours_ok else f'Fora do horario ({config.trading_start_hour}h-{config.trading_end_hour}h ET)'

    # Combined: NYSE Auto OR Globex Auto (if enabled)
    nyse_can = hours_ok
    globex_can = config.globex_auto_enabled and globex_ok
    effective_hours_ok = nyse_can or globex_can
    active_session = 'globex' if (globex_can and not nyse_can) else ('nyse' if nyse_can else 'closed')
    effective_hours_reason = hours_reason if nyse_can else (globex_reason if globex_can else hours_reason)

    # Check news blackout
    news_blackout = False
    blackout_info = None
    upcoming_events = []
    blackout_suspended = trading_calendar._blackout_suspended
    blackout_suspend_reason = trading_calendar._blackout_suspend_reason
    if config.news_blackout_enabled:
        events = await trading_calendar.get_news_blackouts()
        news_blackout, blackout_info = trading_calendar.check_news_blackout(
            events,
            minutes_before=config.news_blackout_minutes_before,
            minutes_after=config.news_blackout_minutes_after,
        )
        upcoming_events = trading_calendar.get_upcoming_events(events)

    # Final decision
    can_trade = effective_hours_ok and not news_blackout
    if not effective_hours_ok:
        status = 'CLOSED'
        reason = effective_hours_reason
    elif news_blackout:
        status = 'NEWS_BLACKOUT'
        reason = f'Blackout: {blackout_info["event"]} ({blackout_info["impact"]})'
    else:
        status = 'OPEN'
        reason = effective_hours_reason

    return {
        'can_trade': can_trade,
        'status': status,
        'reason': reason,
        'session': session,
        'session_type': active_session,
        'exchange_status': f'{"NYSE" if nyse_can else "Globex" if globex_can else "Fechado"}',
        'nyse_auto': {'ok': nyse_can, 'reason': hours_reason},
        'globex_auto': {'ok': globex_can, 'enabled': config.globex_auto_enabled, 'reason': globex_reason},
        'news_blackout': news_blackout,
        'blackout_info': blackout_info,
        'blackout_suspended': blackout_suspended,
        'blackout_suspend_reason': blackout_suspend_reason,
        'upcoming_events': upcoming_events,
        'calendar_status': trading_calendar.get_calendar_status(),
        'config': {
            'auto_hours_mode': config.auto_hours_mode,
            'globex_auto_enabled': config.globex_auto_enabled,
            'news_blackout_enabled': config.news_blackout_enabled,
            'eod_flatten_enabled': config.eod_flatten_enabled,
            'pre_close_flatten_minutes': config.pre_close_flatten_minutes,
            'globex_flatten_before_ny_minutes': config.globex_flatten_before_ny_minutes,
        }
    }

@api_router.post("/autotrading/flatten-all")
async def flatten_all_positions(reason: str = "manual"):
    """Flatten (close) all open positions and cancel pending orders.

    For live trading: sends 'close' action per symbol to close positions,
    then 'cancel' to clear any orphaned SL/TP/pending orders.
    """
    config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    config = AutoTradingConfig(**(config_doc or {}))

    results = []

    # Get all active positions (paper and live) from the unified positions collection
    active_positions = await database.positions.find(
        {'state': {'$in': [PositionState.OPEN.value, PositionState.MANAGING.value, PositionState.BREAK_EVEN.value]}},
        {'_id': 0}
    ).to_list(100)

    # Track symbols we've already sent close/cancel for (avoid duplicates)
    closed_symbols = set()

    for pos in active_positions:
        symbol = pos.get('trade_symbol', '') or pos.get('symbol', '')
        qty = pos.get('quantity', 1)
        is_paper = pos.get('paper', True)

        if not is_paper and config.webhook_url and symbol and symbol not in closed_symbols:
            try:
                close_resp = await send_signalstack_order(SignalStackOrderRequest(
                    webhook_url=config.webhook_url,
                    order=SignalStackOrder(
                        symbol=symbol, action="close",
                        quantity=qty,
                    ),
                ))
                cancel_resp = await send_signalstack_order(SignalStackOrderRequest(
                    webhook_url=config.webhook_url,
                    order=SignalStackOrder(
                        symbol=symbol, action="cancel", quantity=0,
                    ),
                ))
                results.append({
                    'close': convert_numpy_types(close_resp.model_dump()),
                    'cancel': convert_numpy_types(cancel_resp.model_dump()),
                    'close_reason': reason,
                })
                closed_symbols.add(symbol)
            except Exception as e:
                results.append({'error': str(e), 'symbol': symbol})
        else:
            results.append({'symbol': symbol, 'status': 'paper_closed', 'close_reason': reason})

        await database.positions.update_one(
            {'id': pos.get('id')},
            {'$set': {
                'state': PositionState.CLOSED.value,
                'closed_at': datetime.now(timezone.utc).isoformat(),
                'close_reason': reason,
            }}
        )

    # Clear in-memory state
    auto_trading_state.open_positions.clear()

    logger.info(f"FLATTEN ALL: {len(results)} positions closed. Reason: {reason}")

    return {
        'status': 'flattened',
        'positions_closed': len(results),
        'reason': reason,
        'orders': results,
    }

@api_router.get("/gamma/ratio/{symbol}")
async def get_gamma_ratio(symbol: str):
    """Get the D-1 ETF→Futures ratio for gamma level projection"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        data = await gamma_ratio_service.get_ratio(symbol)
        return convert_numpy_types(data)
    except Exception as e:
        logger.error(f"Error fetching gamma ratio: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/gamma/advanced/{symbol}")
async def get_advanced_gamma(symbol: str):
    """Get Advanced Gamma data: ZGL (Zero Gamma Level), Call Wall, Put Wall, GEX Profile"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        gamma_data = await volatility_service.get_gamma_exposure(symbol)
        spot = gamma_data.get('spot_price', 0)
        zgl = gamma_data.get('zgl')
        call_wall = gamma_data.get('call_wall')
        put_wall = gamma_data.get('put_wall')

        # ZGL interpretation
        zgl_interpretation = ''
        if zgl and spot:
            if spot > zgl:
                zgl_interpretation = 'Spot ACIMA do ZGL: dealers long gamma, volatilidade suprimida (mean-reversion)'
            else:
                zgl_interpretation = 'Spot ABAIXO do ZGL: dealers short gamma, volatilidade amplificada (trend-following)'

        # Call/Put Wall interpretation
        wall_interpretation = ''
        if call_wall and put_wall and spot:
            range_pct = round((call_wall - put_wall) / spot * 100, 1) if spot > 0 else 0
            wall_interpretation = f'Range de gamma: {put_wall:.0f} (suporte) → {call_wall:.0f} (resistência) | {range_pct}% do spot'

        return convert_numpy_types({
            "symbol": symbol,
            "spot_price": spot,
            "zgl": zgl,
            "zgl_signal": gamma_data.get('zgl_signal', 'NEUTRAL'),
            "zgl_interpretation": zgl_interpretation,
            "call_wall": call_wall,
            "call_wall_gex": gamma_data.get('call_wall_gex', 0),
            "put_wall": put_wall,
            "put_wall_gex": gamma_data.get('put_wall_gex', 0),
            "wall_interpretation": wall_interpretation,
            "net_gex": gamma_data.get('net_gex', 0),
            "call_gex": gamma_data.get('call_gex', 0),
            "put_gex": gamma_data.get('put_gex', 0),
            "sentiment": gamma_data.get('sentiment', 'NEUTRAL'),
            "gex_profile": gamma_data.get('gex_profile', []),
            "key_levels": gamma_data.get('key_levels', {}),
            "source": gamma_data.get('source', 'unknown'),
            "underlying": gamma_data.get('underlying', ''),
            "expiration": gamma_data.get('expiration', ''),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching advanced GAMMA: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/volatility/{symbol}")
async def get_volatility_data(symbol: str):
    """Get combined VIX, VXN and GAMMA data for a symbol"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        vix_data = await volatility_service.get_vix_data()
        vxn_data = await volatility_service.get_vxn_data()
        gamma_data = await volatility_service.get_gamma_exposure(symbol)
        term_structure = await volatility_service.get_term_structure()
        treasury = await volatility_service.get_treasury_data()
        return convert_numpy_types({
            "symbol": symbol,
            "vix": vix_data,
            "vxn": vxn_data,
            "gamma": gamma_data,
            "term_structure": term_structure,
            "treasury": treasury,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.error(f"Error fetching volatility data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/term-structure")
async def get_term_structure():
    """Get Volatility Term Structure (VIX vs VIX3M) - Contango/Backwardation indicator"""
    try:
        term_structure = await volatility_service.get_term_structure()
        return {"term_structure": convert_numpy_types(term_structure)}
    except Exception as e:
        logger.error(f"Error fetching term structure: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/treasury")
async def get_treasury_yields():
    """Get US10Y, US30Y (proxy 20Y) yields and spread - Yield Curve indicator"""
    try:
        treasury = await volatility_service.get_treasury_data()
        return convert_numpy_types({"treasury": treasury})
    except Exception as e:
        logger.error(f"Error fetching treasury data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/tick-index/{symbol}")
async def get_tick_index(symbol: str, lookback: int = 60):
    """Get TICK Index from DataBento trade data (uptick vs downtick)"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        tick_data = await tick_index_service.get_tick_index(symbol, lookback)
        return convert_numpy_types({"symbol": symbol, "tick_index": tick_data})
    except Exception as e:
        logger.error(f"Error fetching TICK index: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/volume-profile/{symbol}")
async def get_volume_profile(symbol: str, timeframe: str = "1H"):
    """Get Volume Profile (POC, VAH, VAL) from OHLCV data"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        end_date = datetime.now(timezone.utc).isoformat()
        lookback_map = {'5M': 3, '1H': 14, '4H': 30}
        lookback_days = lookback_map.get(timeframe, 14)
        start_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        df = await databento_service.get_historical_data(symbol, timeframe, start_dt.isoformat(), end_date)
        if df.empty:
            raise HTTPException(status_code=404, detail="No data available")

        vp = volume_profile_service.calculate_volume_profile(df)
        vp['symbol'] = symbol
        vp['timeframe'] = timeframe
        return convert_numpy_types(vp)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating volume profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/cvd/{symbol}")
async def get_cvd(symbol: str, lookback: int = 60):
    """Get Cumulative Volume Delta from DataBento trade data"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        cvd_data = await cvd_service.get_cvd(symbol, lookback)
        return convert_numpy_types({"symbol": symbol, "cvd": cvd_data})
    except Exception as e:
        logger.error(f"Error fetching CVD: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/ofi/{symbol}")
async def get_ofi(symbol: str, lookback: int = 5):
    """Get Order Flow Imbalance (OFI Fast/Slow + Absorption) from DataBento trades"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        ofi_data = await ofi_service.get_ofi(symbol, lookback)
        return convert_numpy_types({"symbol": symbol, "ofi": ofi_data})
    except Exception as e:
        logger.error(f"Error fetching OFI: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/vwap/{symbol}")
async def get_vwap(symbol: str, timeframe: str = "1H"):
    """Get multi-day VWAP and bands from OHLCV data"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        end_date = datetime.now(timezone.utc).isoformat()
        lookback_map = {'5M': 3, '1H': 14, '4H': 30}
        lookback_days = lookback_map.get(timeframe, 14)
        start_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        df = await databento_service.get_historical_data(symbol, timeframe, start_dt.isoformat(), end_date)
        if df.empty:
            raise HTTPException(status_code=404, detail="No data available")

        vwap_data = vwap_service.calculate_vwap(df, label='Multi-day')
        vwap_data['symbol'] = symbol
        vwap_data['timeframe'] = timeframe
        return convert_numpy_types(vwap_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating VWAP: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/session-vwaps/{symbol}")
async def get_session_vwaps(symbol: str):
    """Get Globex, Session NY, and Post-News VWAPs — merged com live buffer.
    
    UI-ONLY endpoint with 60s cache. N2/N3 NEVER read from here —
    they use analyze_market() → DataEpoch → in-memory state.
    """
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        # ── 60s TTL Cache ──
        cache_key = f"session_vwaps_standalone_{symbol}"
        _sc = getattr(get_session_vwaps, '_cache', {})
        _st = getattr(get_session_vwaps, '_cache_time', {})
        now_ts = datetime.now(timezone.utc)
        if cache_key in _sc and cache_key in _st and (now_ts - _st[cache_key]).total_seconds() <= 60:
            return _sc[cache_key]

        sessions = get_session_boundaries()
        safe_end = datetime.now(timezone.utc) - timedelta(minutes=20)

        # Fetch historical 1M data
        hist_df = pd.DataFrame()
        try:
            hist_df = await databento_service.get_historical_data(
                symbol, '1M',
                sessions['prev_session_start_utc'].isoformat(),
                safe_end.isoformat(),
            )
        except Exception as e:
            logger.error(f"session-vwaps hist fetch: {e}")

        # Merge with live buffer (same logic as main analyze path)
        merged_df = hist_df.copy() if not hist_df.empty else pd.DataFrame()
        buf = live_data_service.buffers.get(symbol)
        if buf and len(buf.trades) > 0:
            trades_snapshot = list(buf.trades)  # snapshot to avoid deque mutation
            sample_ts = trades_snapshot[-1].ts
            ts_divisor = 1_000_000_000 if sample_ts > 1e15 else (1_000 if sample_ts > 1e12 else 1)
            live_candles = {}
            for t in trades_snapshot:
                ts_s = t.ts // ts_divisor if ts_divisor > 1 else t.ts
                minute_ts = ts_s - (ts_s % 60)
                if minute_ts not in live_candles:
                    live_candles[minute_ts] = {'open': t.price, 'high': t.price, 'low': t.price, 'close': t.price, 'volume': t.size}
                else:
                    c = live_candles[minute_ts]
                    c['high'] = max(c['high'], t.price)
                    c['low'] = min(c['low'], t.price)
                    c['close'] = t.price
                    c['volume'] += t.size
            if live_candles:
                live_df = pd.DataFrame.from_dict(live_candles, orient='index')
                live_df.index = pd.to_datetime(live_df.index, unit='s', utc=True)
                live_df = live_df.sort_index()
                if not merged_df.empty:
                    if not isinstance(merged_df.index, pd.DatetimeIndex):
                        if 'timestamp' in merged_df.columns:
                            merged_df = merged_df.set_index('timestamp')
                    if merged_df.index.tz is None:
                        merged_df.index = merged_df.index.tz_localize('UTC')
                    cutoff = live_df.index.min()
                    merged_df = merged_df[merged_df.index < cutoff]
                    merged_df = pd.concat([merged_df, live_df])
                else:
                    merged_df = live_df

        econ = await economic_calendar_service.get_todays_events()
        news_utc = None
        if econ.get('latest_released_utc'):
            news_utc = datetime.fromisoformat(econ['latest_released_utc'])

        data = vwap_service.calculate_session_vwaps_from_df(merged_df, sessions, news_utc)
        data['economic_calendar'] = econ
        result = convert_numpy_types(data)

        # Store in cache — marca como dados live (não seed)
        if not hasattr(get_session_vwaps, '_cache'):
            get_session_vwaps._cache = {}
            get_session_vwaps._cache_time = {}
        result["_is_seed"] = False
        get_session_vwaps._cache[cache_key] = result
        get_session_vwaps._cache_time[cache_key] = now_ts

        return result
    except Exception as e:
        logger.error(f"Error fetching session VWAPs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/session-vps/{symbol}")
async def get_session_vps(symbol: str):
    """Get Daily and Previous Day Volume Profiles"""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    try:
        data = await session_vp_service.calculate_session_vps(symbol)
        data['symbol'] = symbol
        return convert_numpy_types(data)
    except Exception as e:
        logger.error(f"Error fetching session VPs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/economic-calendar")
async def get_economic_calendar():
    """Get today's economic events"""
    try:
        data = await economic_calendar_service.get_todays_events()
        return convert_numpy_types(data)
    except Exception as e:
        logger.error(f"Error fetching economic calendar: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== LIGHTWEIGHT INDICATORS ENDPOINT (no N1/N2/N3 re-execution) =====
@api_router.get("/market-data")
async def get_market_data_get(
    symbol: str = Query(...),
    timeframe: str = Query("5M"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """GET version of market-data (proxy-friendly, no body consumed)"""
    req = MarketDataRequest(symbol=symbol, timeframe=timeframe, start_date=start_date, end_date=end_date)
    return await get_market_data(req)

@api_router.post("/market-data")
async def get_market_data(request: MarketDataRequest):
    """Get historical market data for a symbol (with cache)"""
    end_date = request.end_date or datetime.now(timezone.utc).isoformat()
    
    # Default lookback based on timeframe (optimized for speed)
    lookback_map = {'5M': 3, '1H': 7, '4H': 30}
    lookback_days = lookback_map.get(request.timeframe, 30)
    
    if request.start_date:
        start_date = request.start_date
    else:
        start_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        start_date = start_dt.isoformat()
    
    # Cache for chart data (2min for 5M with live merge, 15min for 1H/4H)
    cache_ttl = 120 if request.timeframe == '5M' else 900
    cache_key = f"chart_{request.symbol}_{request.timeframe}"
    if not hasattr(get_market_data, '_cache'):
        get_market_data._cache = {}
        get_market_data._cache_time = {}
    
    now_ts = datetime.now(timezone.utc)
    if cache_key in get_market_data._cache and cache_key in get_market_data._cache_time:
        if (now_ts - get_market_data._cache_time[cache_key]).total_seconds() < cache_ttl:
            return get_market_data._cache[cache_key]
    
    try:
        needs_reaggregation = request.timeframe in ('5M', '4H')
        
        # For 5M/4H: fetch raw 1M/1H data to merge with live buffer BEFORE aggregation
        if needs_reaggregation:
            fetch_tf = '1M' if request.timeframe == '5M' else '1H'
            df = await databento_service.get_historical_data(
                request.symbol, fetch_tf, start_date, end_date
            )
        else:
            df = await databento_service.get_historical_data(
                request.symbol, request.timeframe, start_date, end_date
            )
        
        # ── Live buffer merge: fill gap between historical end and now ──
        buf = live_data_service.buffers.get(request.symbol)
        if buf and len(buf.trades) > 0:
            trades_snapshot = list(buf.trades)  # snapshot to avoid deque mutation
            sample_ts = trades_snapshot[-1].ts
            ts_divisor = 1_000_000_000 if sample_ts > 1e15 else (1_000 if sample_ts > 1e12 else 1)

            # Build 1M candles from live trades
            live_candles = {}
            for t in trades_snapshot:
                ts_s = t.ts // ts_divisor if ts_divisor > 1 else t.ts
                minute_ts = ts_s - (ts_s % 60)
                if minute_ts not in live_candles:
                    live_candles[minute_ts] = {
                        'open': t.price, 'high': t.price,
                        'low': t.price, 'close': t.price, 'volume': t.size,
                    }
                else:
                    c = live_candles[minute_ts]
                    c['high'] = max(c['high'], t.price)
                    c['low'] = min(c['low'], t.price)
                    c['close'] = t.price
                    c['volume'] += t.size

            if live_candles:
                live_df = pd.DataFrame.from_dict(live_candles, orient='index')
                live_df.index = pd.to_datetime(live_df.index, unit='s', utc=True)
                live_df = live_df.sort_index()

                if not df.empty:
                    if not isinstance(df.index, pd.DatetimeIndex):
                        if 'timestamp' in df.columns:
                            df = df.set_index('timestamp')
                    if df.index.tz is None:
                        df.index = df.index.tz_localize('UTC')
                    cutoff = live_df.index.min()
                    df = pd.concat([df[df.index < cutoff], live_df])
                else:
                    df = live_df

        # Aggregate merged data to target timeframe
        if not df.empty:
            if request.timeframe == '5M':
                df = databento_service._aggregate_to_5m(df)
            elif request.timeframe == '4H':
                df = databento_service._aggregate_to_4h(df)
            elif request.timeframe == '1H':
                if not isinstance(df.index, pd.DatetimeIndex):
                    if 'timestamp' in df.columns:
                        df = df.set_index('timestamp')
                if df.index.tz is None:
                    df.index = df.index.tz_localize('UTC')
                df = df.resample('1h').agg({
                    'open': 'first', 'high': 'max',
                    'low': 'min', 'close': 'last', 'volume': 'sum',
                }).dropna()

        if df.empty:
            return {"data": [], "count": 0}
        
        # Convert to list of dicts
        df = df.reset_index()
        df.columns = df.columns.astype(str)
        
        # Rename index column if present
        if 'index' in df.columns:
            df = df.rename(columns={'index': 'timestamp'})
        elif df.columns[0] not in ['timestamp', 'open', 'high', 'low', 'close', 'volume']:
            df = df.rename(columns={df.columns[0]: 'timestamp'})
        
        # Convert timestamp to string
        if 'timestamp' in df.columns:
            df['timestamp'] = df['timestamp'].astype(str)
        
        data = df.to_dict(orient='records')
        
        result = convert_numpy_types({"data": data, "count": len(data)})
        get_market_data._cache[cache_key] = result
        get_market_data._cache_time[cache_key] = now_ts
        return result
        
    except Exception as e:
        logger.error(f"Error fetching market data: {e}")
        raise HTTPException(status_code=500, detail=str(e))




@api_router.post("/positions/evaluate/{symbol}")
async def evaluate_position_params(symbol: str, side: str = "BUY"):
    """
    Preview position parameters without opening.
    Useful for the frontend to display SL/TP/Trailing before execution.
    """
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

    buf = live_data_service.buffers.get(symbol)
    entry_price = buf.last_price if buf and buf.last_price > 0 else 0.0

    regime = 'TRANSICAO'
    trade_symbol = symbol
    tick_size = SYMBOLS.get(symbol, {}).get('tick_size', 0.25)
    atr_m1 = 2.0

    levels = {
        'vwap': 0, 'poc': 0, 'vah': 0, 'val': 0,
        'call_wall': 0, 'put_wall': 0,
        'vwap_upper_1s': 0, 'vwap_lower_1s': 0,
    }

    pos_params = calculate_position_params(
        regime=regime,
        entry_price=entry_price,
        side=side.upper(),
        atr_m1=atr_m1,
        levels=levels,
        tick_size=tick_size,
    )

    # Compute risk in dollars using account config
    config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    config = AutoTradingConfig(**(config_doc or {}))
    sym_info = SYMBOLS.get(trade_symbol, SYMBOLS.get(symbol, {}))
    point_value = sym_info.get('point_value', 5.0)
    tick_value = sym_info.get('tick_value', 1.25)

    sl_distance_pts = abs(entry_price - pos_params['hard_stop'])
    tp_distance_pts = abs(pos_params['take_profit'] - entry_price) if pos_params.get('take_profit') else None
    sl_risk_usd = sl_distance_pts * point_value
    tp_reward_usd = tp_distance_pts * point_value if tp_distance_pts else None
    rr_ratio = round(tp_distance_pts / sl_distance_pts, 2) if tp_distance_pts and sl_distance_pts > 0 else None

    account_size = config.account_size
    risk_per_trade_pct = config.risk_per_trade_pct
    max_risk_usd = account_size * risk_per_trade_pct / 100
    max_qty = max(1, int(max_risk_usd / sl_risk_usd)) if sl_risk_usd > 0 else 1
    daily_loss_limit_usd = account_size * config.max_daily_loss_percent / 100

    return convert_numpy_types({
        "regime": regime,
        "symbol": symbol,
        "trade_symbol": trade_symbol,
        "entry_price": entry_price,
        "params": pos_params,
        "levels": levels,
        "risk": {
            "account_size": account_size,
            "risk_per_trade_pct": risk_per_trade_pct,
            "max_risk_usd": round(max_risk_usd, 2),
            "sl_distance_pts": round(sl_distance_pts, 2),
            "sl_risk_per_contract_usd": round(sl_risk_usd, 2),
            "tp_reward_per_contract_usd": round(tp_reward_usd, 2) if tp_reward_usd else None,
            "rr_ratio": rr_ratio,
            "max_qty_by_risk": max_qty,
            "point_value": point_value,
            "tick_value": tick_value,
            "daily_loss_limit_usd": round(daily_loss_limit_usd, 2),
        },
    })


@api_router.get("/signals/{symbol}")
async def get_current_signals(symbol: str):
    """Redirect to Scalp Engine signal — V3 TechnicalIndicators removed."""
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    from routes.scalp import _scalp_engine as _se
    if _se is None:
        return {"symbol": symbol, "signals": {}, "note": "ScalpEngine not ready"}
    try:
        sig = await _se.evaluate(symbol, quantity=1)
        return {
            "symbol": symbol,
            "signals": {
                "scalp": {
                    "status": sig.scalp_status,
                    "direction": sig.s1_direction,
                    "s1_confidence": sig.s1_confidence,
                    "s2_quality": sig.s2_quality.value if hasattr(sig.s2_quality, "value") else str(sig.s2_quality),
                    "mode": sig.mode,
                }
            },
            "note": "V3 signals removed — serving ScalpEngine output"
        }
    except Exception as e:
        return {"symbol": symbol, "signals": {}, "error": str(e)}

@api_router.get("/signals-history/{symbol}")
async def get_signals_history(symbol: str, limit: int = 50):
    """Get historical signals for a symbol"""
    cursor = database.trading_signals.find(
        {'symbol': symbol}, 
        {'_id': 0}
    ).sort('timestamp', -1).limit(limit)
    
    signals = await cursor.to_list(length=limit)
    return {"symbol": symbol, "history": signals}

# ========== SIGNALSTACK ENDPOINTS ==========

@api_router.post("/signalstack/config")
async def save_signalstack_config(config: SignalStackConfig):
    """Save SignalStack webhook configuration"""
    config_dict = config.model_dump()
    config_dict['created_at'] = config_dict['created_at'].isoformat()
    
    # Upsert config
    await database.signalstack_config.update_one(
        {'id': config.id},
        {'$set': config_dict},
        upsert=True
    )
    
    return {"status": "success", "config": config_dict}

@api_router.get("/signalstack/config")
async def get_signalstack_config():
    """Get SignalStack webhook configuration"""
    configs = await database.signalstack_config.find({}, {'_id': 0}).to_list(100)
    return {"configs": configs}

@api_router.delete("/signalstack/config/{config_id}")
async def delete_signalstack_config(config_id: str):
    """Delete SignalStack webhook configuration"""
    result = await database.signalstack_config.delete_one({'id': config_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"status": "deleted", "id": config_id}

def build_signalstack_payload(order: SignalStackOrder) -> Dict[str, Any]:
    """Build a JSON payload for SignalStack/Tradovate webhook.

    Core order types (per official SignalStack doc):
      - market:     symbol + action + quantity + class
      - limit:      + limit_price
      - stop:       + stop_price
      - stop_limit: + limit_price + stop_price

    Extended Tradovate features (documented by PickMyTrade/Tradovate API):
      - Trailing stop: trail_trigger, trail_stop, trail_freq
      - Break-even:    breakeven
      - Close/Cancel:  action="close" or action="cancel"
      - Update SL/TP:  update_sl, update_tp
    """
    action = order.action.lower()

    # Close / Cancel actions — minimal payload
    if action in ("close", "cancel"):
        payload: Dict[str, Any] = {
            "symbol": order.symbol,
            "action": action,
            "quantity": max(order.quantity, 1),
            "class": "future",
        }
        return payload

    # Standard buy/sell payload
    payload = {
        "symbol": order.symbol,
        "action": action,
        "quantity": order.quantity,
        "class": "future",
    }

    # Price fields per order type
    if order.order_type == "limit" and order.limit_price is not None:
        payload["limit_price"] = order.limit_price
    elif order.order_type == "stop" and order.stop_price is not None:
        payload["stop_price"] = order.stop_price
    elif order.order_type == "stop_limit":
        if order.limit_price is not None:
            payload["limit_price"] = order.limit_price
        if order.stop_price is not None:
            payload["stop_price"] = order.stop_price

    # Bracket order — OCO nativo Tradovate
    # take_profit_price + stop_loss_price criam child orders amarrados à entrada.
    # Quando um lado preenche, o Tradovate cancela o outro automaticamente na bolsa.
    if order.take_profit_price is not None:
        payload["take_profit_price"] = round(order.take_profit_price, 2)
    if order.stop_loss_price is not None:
        payload["stop_loss_price"] = round(order.stop_loss_price, 2)

    # Trailing Stop (Tradovate native)
    if order.trail_trigger is not None:
        payload["trail_trigger"] = order.trail_trigger
    if order.trail_stop is not None:
        payload["trail_stop"] = order.trail_stop
    if order.trail_freq is not None:
        payload["trail_freq"] = order.trail_freq

    # Break-even
    if order.breakeven is not None:
        payload["breakeven"] = order.breakeven

    # Update existing SL/TP
    if order.update_sl is not None:
        payload["update_sl"] = order.update_sl
    if order.update_tp is not None:
        payload["update_tp"] = order.update_tp

    return payload


def _get_live_price_for_trado_symbol(trado_symbol: str) -> float:
    """Extract base symbol from Tradovate format (e.g. MNQM6 → MNQ) and return live price."""
    import re as _re
    base = _re.sub(r'[A-Z]\d+$', '', trado_symbol.upper())  # strip contract month+year (e.g. M6, H6, Z5)
    if not base:
        base = trado_symbol.upper()[:3]
    try:
        buf = live_data_service.buffers.get(base)
        if buf and buf.last_price > 0:
            return float(buf.last_price)
    except Exception:
        pass
    return 0.0


@api_router.post("/signalstack/send-order", response_model=SignalStackOrderResponse)
async def send_signalstack_order(request: SignalStackOrderRequest):
    """Send order to SignalStack webhook for Tradovate execution.

    Builds payload per official SignalStack Tradovate Futures spec:
    https://help.signalstack.com/kb/tradovate/tradovate-future

    Pre-flight price guard: if limit_price or stop_price diverges > 12% from
    the current live market price the order is rejected locally before reaching
    Tradovate, preventing InvalidPrice errors from stale data.
    """
    order = request.order

    # ── Pre-flight price sanity check ──────────────────────────────────────
    # Market/cancel/close orders carry no price — skip.
    # Price-based orders (limit, stop, stop_limit, bracket) are validated against
    # the current live price. Tradovate's own band is ~10%; we use 12% as the gate
    # so legitimate far-out SL/TP still pass but clearly stale prices don't.
    _price_fields = {
        k: v for k, v in [
            ('limit_price',       order.limit_price),
            ('stop_price',        order.stop_price),
            ('take_profit_price', order.take_profit_price),
            ('stop_loss_price',   order.stop_loss_price),
        ]
        if v is not None and v > 0
    }
    if _price_fields and order.action.lower() not in ('cancel', 'close'):
        _live = _get_live_price_for_trado_symbol(order.symbol)
        if _live > 0:
            for _field, _price in _price_fields.items():
                _deviation = abs(_price - _live) / _live
                if _deviation > 0.12:   # 12% band
                    _msg = (
                        f"PRE-FLIGHT BLOQUEADO — {order.symbol} {_field}={_price:.2f} "
                        f"desvia {_deviation*100:.1f}% do mercado ({_live:.2f}). "
                        f"Ordem não enviada (dados desatualizados ou stale cache)."
                    )
                    logger.error(_msg)
                    _rejected = SignalStackOrderResponse(
                        symbol=order.symbol, action=order.action,
                        quantity=order.quantity, order_type=order.order_type,
                        limit_price=order.limit_price, stop_price=order.stop_price,
                        take_profit_price=order.take_profit_price,
                        stop_loss_price=order.stop_loss_price,
                        status="error", response_code=422,
                        response_message=_msg,
                        webhook_url=request.webhook_url,
                        payload=build_signalstack_payload(order),
                    )
                    _doc = convert_numpy_types(_rejected.model_dump())
                    _doc['sent_at'] = _doc['sent_at'].isoformat() if hasattr(_doc.get('sent_at', ''), 'isoformat') else str(_doc.get('sent_at', ''))
                    await database.signalstack_orders.insert_one(_doc)
                    return _rejected

    payload = build_signalstack_payload(order)

    logger.info(f"SignalStack OUT >> {payload}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(
                request.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            # Parse child_ids from bracket response (Tradovate returns child_ids for OCO orders)
            _child_ids = None
            try:
                _resp_json = response.json()
                _child_ids = _resp_json.get("child_ids") or None
            except Exception:
                pass

            response_data = SignalStackOrderResponse(
                symbol=order.symbol,
                action=order.action,
                quantity=order.quantity,
                order_type=order.order_type,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                take_profit_price=order.take_profit_price,
                stop_loss_price=order.stop_loss_price,
                status="success" if response.status_code in [200, 201, 202] else "error",
                response_code=response.status_code,
                response_message=response.text[:500] if response.text else "OK",
                child_ids=_child_ids,
                webhook_url=request.webhook_url,
                payload=payload,
            )

    except httpx.TimeoutException:
        response_data = SignalStackOrderResponse(
            symbol=order.symbol, action=order.action,
            quantity=order.quantity, order_type=order.order_type,
            limit_price=order.limit_price, stop_price=order.stop_price,
            take_profit_price=order.take_profit_price, stop_loss_price=order.stop_loss_price,
            status="error", response_code=408,
            response_message="Request timeout",
            webhook_url=request.webhook_url, payload=payload,
        )
    except Exception as e:
        response_data = SignalStackOrderResponse(
            symbol=order.symbol, action=order.action,
            quantity=order.quantity, order_type=order.order_type,
            limit_price=order.limit_price, stop_price=order.stop_price,
            take_profit_price=order.take_profit_price, stop_loss_price=order.stop_loss_price,
            status="error", response_code=500,
            response_message=str(e),
            webhook_url=request.webhook_url, payload=payload,
        )

    # Persist
    order_doc = convert_numpy_types(response_data.model_dump())
    order_doc['sent_at'] = order_doc['sent_at'].isoformat() if hasattr(order_doc.get('sent_at', ''), 'isoformat') else str(order_doc.get('sent_at', ''))
    await database.signalstack_orders.insert_one(order_doc)

    return response_data


@api_router.get("/signalstack/send-order/submit", response_model=SignalStackOrderResponse)
async def send_signalstack_order_get(payload: str):
    """GET companion for /signalstack/send-order — proxy-safe (no JSON body).
    payload = JSON-encoded SignalStackOrderRequest.
    """
    import json as _json
    try:
        data = _json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")
    request = SignalStackOrderRequest(**data)
    return await send_signalstack_order(request)


@api_router.post("/signalstack/send-signal")
async def send_signal_to_signalstack(
    symbol: str,
    action: str,
    quantity: int = 1,
    order_type: str = "market",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None
):
    """Send trading signal directly to all configured SignalStack webhooks"""
    
    # Get all enabled configs
    configs = await database.signalstack_config.find({'enabled': True}, {'_id': 0}).to_list(100)
    
    if not configs:
        raise HTTPException(status_code=400, detail="No SignalStack webhooks configured")
    
    # Get Tradovate symbol format
    tradovate_symbol = get_tradovate_symbol(symbol)
    
    results = []
    for config in configs:
        order = SignalStackOrder(
            symbol=tradovate_symbol,
            action=action,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price
        )
        
        request = SignalStackOrderRequest(
            webhook_url=config['webhook_url'],
            order=order
        )
        
        result = await send_signalstack_order(request)
        results.append({
            "config_name": config.get('name', 'Unknown'),
            "result": result.model_dump()
        })
    
    return {"symbol": symbol, "tradovate_symbol": tradovate_symbol, "results": results}

@api_router.get("/signalstack/orders")
async def get_signalstack_orders(limit: int = Query(default=50, ge=1, le=500), symbol: Optional[str] = None):
    """Get SignalStack order history — includes V3 paper trades for unified view."""
    import re as _re
    query = {}
    if symbol:
        # Sanitiza symbol: apenas caracteres alfanuméricos para evitar ReDoS / regex injection
        safe_sym = _re.sub(r'[^A-Za-z0-9]', '', symbol).upper()
        if safe_sym:
            query['symbol'] = safe_sym  # exact match — símbolo é sempre MNQ/MES/etc.
    
    # Fetch real SignalStack webhook orders
    cursor = database.signalstack_orders.find(query, {'_id': 0}).sort('sent_at', -1).limit(limit)
    ss_orders = await cursor.to_list(length=limit)
    
    # Also fetch V3 paper trades from positions collection to give a unified view
    pos_query = {'$or': [
        {'source': {'$in': ['v3_auto', 'paper']}},
        {'paper': True},
    ]}
    if symbol:
        safe_sym = _re.sub(r'[^A-Za-z0-9]', '', symbol).upper()
        if safe_sym:
            pos_query = {'$and': [pos_query, {'symbol': safe_sym}]}
    pos_cursor = database.positions.find(pos_query, {'_id': 0}).sort('opened_at', -1).limit(limit)
    paper_positions = await pos_cursor.to_list(length=limit)
    
    # Convert positions to order-like format for unified display
    v3_orders = []
    for p in paper_positions:
        v3_orders.append({
            'symbol': p.get('symbol', ''),
            'action': p.get('side', '').lower(),
            'quantity': p.get('quantity', 1),
            'order_type': 'market',
            'status': 'paper' if p.get('paper') else p.get('state', 'unknown'),
            'sent_at': p.get('opened_at', ''),
            'closed_at': p.get('closed_at', ''),
            'entry_price': p.get('entry_price'),
            'exit_price': p.get('exit_price'),
            'pnl': p.get('pnl', 0),
            'regime': p.get('regime', ''),
            'close_reason': p.get('close_reason', ''),
            'source': p.get('source', 'v3_auto'),
        })
    
    return {
        "orders": ss_orders,
        "v3_trades": v3_orders,
        "count": len(ss_orders),
        "v3_count": len(v3_orders),
    }

@api_router.get("/signalstack/orders/stats")
async def get_signalstack_order_stats():
    """Get SignalStack order statistics"""
    pipeline = [
        {
            '$group': {
                '_id': '$status',
                'count': {'$sum': 1}
            }
        }
    ]
    
    status_stats = await database.signalstack_orders.aggregate(pipeline).to_list(100)
    
    # Get recent orders count (last 24h)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    recent_count = await database.signalstack_orders.count_documents({
        'sent_at': {'$gte': yesterday}
    })
    
    # Get total count
    total_count = await database.signalstack_orders.count_documents({})
    
    return {
        "total_orders": total_count,
        "orders_last_24h": recent_count,
        "by_status": {item['_id']: item['count'] for item in status_stats}
    }

@api_router.get("/signalstack/symbols")
async def get_tradovate_symbols():
    """Get current Tradovate symbol mappings for all supported symbols"""
    symbols = {}
    for symbol in SYMBOLS.keys():
        symbols[symbol] = {
            "tradovate": get_tradovate_symbol(symbol),
            "name": SYMBOLS[symbol]['name']
        }
    return {"symbols": symbols}


@api_router.post("/signalstack/test-payloads")
async def test_signalstack_payloads(webhook_url: str = "", dry_run: bool = False):
    """Test all JSON payload types against SignalStack webhook.

    Uses current market price with safe offsets so price-based orders are
    accepted by Tradovate but will NOT fill immediately.

    dry_run=True: builds and returns payloads without sending to webhook.

    Order types tested:
    1. Market buy
    2. Limit sell  (current + 80 pts — above market, won't fill)
    3. Stop sell   (current - 80 pts — below market, protective stop)
    4. Stop-Limit buy (stop=current+80, limit=current+81 — buy-stop above market)
    5. Market buy + trailing stop
    6. Market buy + breakeven
    7. Cancel all
    8. Bracket OCO buy (stop_loss_price + take_profit_price — native OCO)
    """
    if not webhook_url and not dry_run:
        # Try to get from config
        config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
        if config_doc:
            webhook_url = config_doc.get('webhook_url', '')
        if not webhook_url:
            configs = await database.signalstack_config.find({'enabled': True}, {'_id': 0}).to_list(1)
            if configs:
                webhook_url = configs[0].get('webhook_url', '')
        if not webhook_url and not dry_run:
            return {"error": "Nenhuma webhook URL configurada. Passe webhook_url como parametro ou use dry_run=true."}

    test_symbol = get_tradovate_symbol('MNQ')  # e.g. MNQM6

    # ── fetch current market price for realistic offsets ────────────────────
    current_price: float = 0.0
    try:
        buf = live_data_service.buffers.get('MNQ')
        if buf and buf.last_price > 0:
            current_price = buf.last_price
    except Exception:
        pass
    if current_price <= 0:
        current_price = 25000.0  # last-resort static fallback

    # Round to nearest 0.25 tick
    def _tick(p: float) -> float:
        return round(round(p / 0.25) * 0.25, 2)

    offset = 80.0  # pts away from market — accepted by Tradovate, won't fill
    lim_sell  = _tick(current_price + offset)       # limit sell ABOVE market
    stop_sell = _tick(current_price - offset)       # stop sell BELOW market
    sl_stop   = _tick(current_price + offset)       # stop-limit BUY stop leg
    sl_limit  = _tick(current_price + offset + 0.25)  # limit leg 1 tick above

    test_cases = [
        {
            "name": "1. Market Buy",
            "order": SignalStackOrder(symbol=test_symbol, action="buy", quantity=1, order_type="market"),
        },
        {
            "name": "2. Limit Sell",
            "order": SignalStackOrder(symbol=test_symbol, action="sell", quantity=1, order_type="limit", limit_price=lim_sell),
        },
        {
            "name": "3. Stop Sell (SL)",
            "order": SignalStackOrder(symbol=test_symbol, action="sell", quantity=1, order_type="stop", stop_price=stop_sell),
        },
        {
            "name": "4. Stop-Limit Buy",
            "order": SignalStackOrder(symbol=test_symbol, action="buy", quantity=1, order_type="stop_limit", stop_price=sl_stop, limit_price=sl_limit),
        },
        {
            "name": "5. Market Buy + Trailing Stop",
            "order": SignalStackOrder(
                symbol=test_symbol, action="buy", quantity=1, order_type="market",
                trail_trigger=15.0, trail_stop=8.0, trail_freq=4.0,
            ),
        },
        {
            "name": "6. Market Buy + Breakeven",
            "order": SignalStackOrder(
                symbol=test_symbol, action="buy", quantity=1, order_type="market",
                breakeven=20.0,
            ),
        },
        {
            "name": "7. Cancel All Orders",
            "order": SignalStackOrder(symbol=test_symbol, action="cancel", quantity=1),
        },
        {
            "name": "8. Bracket OCO Buy (SL + TP nativo Tradovate)",
            "order": SignalStackOrder(
                symbol=test_symbol, action="buy", quantity=1, order_type="market",
                stop_loss_price=_tick(current_price - offset),      # hard stop below market
                take_profit_price=_tick(current_price + offset),    # TP above market
            ),
        },
    ]

    results = []
    for tc in test_cases:
        payload = build_signalstack_payload(tc["order"])
        if dry_run:
            results.append({
                "test": tc["name"],
                "payload_sent": payload,
                "response_code": None,
                "response_body": "dry_run — não enviado",
                "accepted": None,
            })
            continue
        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                results.append({
                    "test": tc["name"],
                    "payload_sent": payload,
                    "response_code": resp.status_code,
                    "response_body": resp.text[:500],
                    "accepted": resp.status_code in [200, 201, 202],
                })
        except Exception as e:
            results.append({
                "test": tc["name"],
                "payload_sent": payload,
                "response_code": None,
                "response_body": str(e),
                "accepted": False,
            })

    accepted_count = sum(1 for r in results if r.get("accepted") is True)
    rejected_count = sum(1 for r in results if r.get("accepted") is False)

    return {
        "webhook_url": webhook_url if not dry_run else "dry_run",
        "test_symbol": test_symbol,
        "reference_price": current_price,
        "dry_run": dry_run,
        "summary": "dry_run — payloads não enviados" if dry_run else f"{accepted_count}/{len(results)} aceitos, {rejected_count} rejeitados",
        "results": results,
    }


# ========== AUTO TRADING ENDPOINTS ==========

@api_router.post("/autotrading/config")
async def save_autotrading_config(config: AutoTradingConfig):
    """Save auto trading configuration"""
    config_dict = config.model_dump()
    config_dict['created_at'] = config_dict['created_at'].isoformat()
    config_dict['updated_at'] = datetime.now(timezone.utc).isoformat()
    
    # Always use 'default' as the single config ID
    config_dict['id'] = 'default'
    
    await database.autotrading_config.update_one(
        {'id': 'default'},
        {'$set': config_dict},
        upsert=True
    )
    _invalidate_at_config()
    return {"status": "success", "config": config_dict}

@api_router.get("/autotrading/config/save")
async def save_autotrading_config_get(config_json: str = Query(..., description="Full AutoTradingConfig as JSON string")):
    """Save auto trading configuration via GET (proxy-safe).
    Workaround: POST+JSON bodies are consumed by the dev proxy middleware.
    Frontend sends config as a JSON-encoded query parameter instead.
    """
    import json as _json
    try:
        config_data = _json.loads(config_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid config_json: {e}")
    config = AutoTradingConfig(**config_data)
    config_dict = config.model_dump()
    config_dict['created_at'] = config_dict['created_at'].isoformat()
    config_dict['updated_at'] = datetime.now(timezone.utc).isoformat()
    config_dict['id'] = 'default'
    await database.autotrading_config.update_one(
        {'id': 'default'},
        {'$set': config_dict},
        upsert=True
    )
    _invalidate_at_config()
    logger.info(f"AutoTrading config saved via GET: enabled={config.enabled}, paper_trading={config.paper_trading}")
    return {"status": "success", "config": config_dict}

@api_router.get("/autotrading/config")
async def get_autotrading_config():
    """Get auto trading configuration"""
    config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    
    if not config_doc:
        # Return default config
        default_config = AutoTradingConfig(webhook_url="https://app.signalstack.com/hook/a65Cvk39pE3HdZiutAi9rP")
        return {"config": default_config.model_dump()}
    
    # Merge stored config with Pydantic model to ensure new fields have defaults
    config = AutoTradingConfig(**config_doc)
    return {"config": config.model_dump()}

@api_router.post("/autotrading/evaluate")
async def evaluate_autotrading_signal(symbol: str):
    """Evaluate auto-trading conditions using the ScalpEngine (V3 indicators removed)."""
    global auto_trading_state

    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

    config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    config = AutoTradingConfig(**(config_doc or {}))

    from routes.scalp import _scalp_engine as _se
    if _se is None:
        return {"symbol": symbol, "signal": None, "reason": "ScalpEngine not ready"}

    try:
        sig = await _se.evaluate(symbol, quantity=config.default_quantity)
    except Exception as e:
        return {"symbol": symbol, "signal": None, "reason": f"ScalpEngine error: {e}"}

    active = sig.scalp_status == "ACTIVE_SIGNAL"
    direction = sig.s1_direction or "NEUTRAL"
    confidence = round((sig.s1_confidence or 0) / 10.0, 2)
    q_val = sig.s2_quality.value if hasattr(sig.s2_quality, "value") else str(sig.s2_quality)

    # Cooldown check
    last_trade = auto_trading_state.last_trade_time.get(symbol)
    cooldown_met = True
    if last_trade:
        minutes_since = (datetime.now(timezone.utc) - last_trade).total_seconds() / 60
        cooldown_met = minutes_since >= config.min_minutes_between_trades

    conditions = {
        "scalp_active_signal": {"required": "ACTIVE_SIGNAL", "actual": sig.scalp_status,        "met": active},
        "has_direction":        {"required": "LONG or SHORT",  "actual": direction,               "met": direction in ("LONG", "SHORT")},
        "s2_quality":           {"required": "MODERATE+",     "actual": q_val,                   "met": q_val in ("STRONG", "MODERATE")},
        "cooldown":             {"required": f"{config.min_minutes_between_trades}min", "actual": "OK" if cooldown_met else "too recent", "met": cooldown_met},
        "daily_limit":          {"required": f"<={config.max_daily_trades}", "actual": auto_trading_state.daily_trades, "met": auto_trading_state.daily_trades < config.max_daily_trades},
    }
    all_met = all(c["met"] for c in conditions.values())

    auto_signal = AutoTradingSignal(
        symbol=symbol,
        action=direction,
        confluence_score=confidence,
        confidence_score=confidence,
        mtf_alignment=active,
        mtf_signals={"scalp": direction},
        conditions_met={k: bool(v["met"]) for k, v in conditions.items()},
        all_conditions_met=bool(all_met),
        entry_price=float(sig.s3_entry_price)     if sig.s3_entry_price     else None,
        stop_loss=float(sig.s3_stop_loss_price)   if sig.s3_stop_loss_price else None,
        take_profit=float(sig.s3_take_profit_price) if sig.s3_take_profit_price else None,
        quantity=config.default_quantity,
        paper_trade=config.paper_trading,
        reason="All conditions met" if all_met else "Conditions not met: " + ", ".join(k for k, v in conditions.items() if not v["met"])
    )

    signal_doc = convert_numpy_types(auto_signal.model_dump())
    signal_doc["timestamp"] = signal_doc["timestamp"].isoformat() if hasattr(signal_doc.get("timestamp"), "isoformat") else signal_doc.get("timestamp", "")
    await database.autotrading_signals.insert_one(signal_doc)

    return {
        "symbol": symbol,
        "signal": convert_numpy_types(auto_signal.model_dump()),
        "conditions": conditions,
        "config_enabled": config.enabled,
        "paper_trading": config.paper_trading,
    }

@api_router.post("/autotrading/execute")
async def execute_autotrading_signal(symbol: str, force: bool = False):
    """Execute an auto trading signal if conditions are met"""
    global auto_trading_state
    
    # Get config
    config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    if config_doc:
        config = AutoTradingConfig(**config_doc)
    else:
        config = AutoTradingConfig(webhook_url="https://app.signalstack.com/hook/a65Cvk39pE3HdZiutAi9rP")
    
    if not config.enabled and not force:
        return {"status": "disabled", "message": "Auto trading is disabled"}

    # ── Trading Hours Guard ──
    if not force:
        if config.auto_hours_mode:
            hours_ok, hours_reason = trading_calendar.is_within_auto_trading_hours()
            if not hours_ok and config.globex_auto_enabled:
                hours_ok, hours_reason = trading_calendar.is_within_globex_auto_hours()
        else:
            from zoneinfo import ZoneInfo
            now_et = datetime.now(ZoneInfo('America/New_York'))
            h = now_et.hour
            hours_ok = config.trading_start_hour <= h < config.trading_end_hour
            hours_reason = 'Fora do horario manual' if not hours_ok else ''

        if not hours_ok:
            return {"status": "outside_hours", "message": hours_reason}

        # ── News Blackout Guard ──
        if config.news_blackout_enabled:
            events = await trading_calendar.get_news_blackouts()
            is_blackout, blackout_info = trading_calendar.check_news_blackout(
                events,
                minutes_before=config.news_blackout_minutes_before,
                minutes_after=config.news_blackout_minutes_after,
            )
            if is_blackout:
                return {
                    "status": "news_blackout",
                    "message": f"Blackout: {blackout_info['event']} ({blackout_info['impact']})",
                    "blackout_info": blackout_info,
                }
    
    # Evaluate signal
    evaluation = await evaluate_autotrading_signal(symbol)
    signal_data = evaluation.get('signal')
    
    if not signal_data:
        return {"status": "error", "message": "No signal data"}
    
    if not signal_data['all_conditions_met'] and not force:
        return {
            "status": "conditions_not_met",
            "signal": signal_data,
            "conditions": evaluation.get('conditions')
        }
    
    if signal_data['action'] == 'NEUTRAL':
        return {"status": "neutral", "message": "Signal is neutral, no action taken"}
    
    # Determine action
    action = 'buy' if signal_data['action'] == 'LONG' else 'sell'
    
    result = {
        "status": "executed" if not config.paper_trading else "paper_executed",
        "paper_trading": config.paper_trading,
        "signal": signal_data,
        "order": None
    }
    
    if config.paper_trading:
        # Paper trading (legacy evaluate/execute path) — log only, no persistence
        # N3 watcher uses open_n3_position() which writes to the positions collection
        paper_order = convert_numpy_types({
            "id": str(uuid.uuid4()),
            "symbol": get_tradovate_symbol(symbol),
            "action": action,
            "quantity": config.default_quantity,
            "entry_price": signal_data['entry_price'],
            "stop_loss": signal_data['stop_loss'],
            "take_profit": signal_data['take_profit'],
            "status": "paper_simulated",
            "paper_trade": True,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        result["order"] = paper_order
        
    else:
        # Real trading - send to SignalStack
        if not config.webhook_url:
            return {"status": "error", "message": "No webhook URL configured"}
        
        order = SignalStackOrder(
            symbol=get_tradovate_symbol(symbol),
            action=action,
            quantity=config.default_quantity,
            order_type="market"
        )
        
        request = SignalStackOrderRequest(
            webhook_url=config.webhook_url,
            order=order
        )
        
        order_response = await send_signalstack_order(request)
        result["order"] = order_response.model_dump()
    
    # Update state
    auto_trading_state.last_trade_time[symbol] = datetime.now(timezone.utc)
    auto_trading_state.daily_trades += 1
    
    if action == 'buy':
        auto_trading_state.open_positions[symbol] = {
            "action": action,
            "entry_price": signal_data['entry_price'],
            "quantity": config.default_quantity,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    return result

@api_router.post("/autotrading/evaluate-all")
async def evaluate_all_symbols():
    """Evaluate auto trading signals for all configured symbols"""
    config_doc = await database.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    if config_doc:
        config = AutoTradingConfig(**config_doc)
    else:
        config = AutoTradingConfig()
    
    results = {}
    for symbol in config.symbols:
        try:
            evaluation = await evaluate_autotrading_signal(symbol)
            results[symbol] = {
                "signal": evaluation.get('signal'),
                "conditions_met": evaluation.get('signal', {}).get('all_conditions_met', False)
            }
        except Exception as e:
            results[symbol] = {"error": str(e)}
    
    # Find actionable signals
    actionable = [s for s, data in results.items() if data.get('conditions_met')]
    
    return {
        "results": results,
        "actionable_symbols": actionable,
        "config_enabled": config.enabled,
        "paper_trading": config.paper_trading
    }

@api_router.get("/autotrading/signals")
async def get_autotrading_signals(limit: int = 50, symbol: Optional[str] = None):
    """Get auto trading signal history"""
    query = {}
    if symbol:
        query['symbol'] = symbol
    
    cursor = database.autotrading_signals.find(query, {'_id': 0}).sort('timestamp', -1).limit(limit)
    signals = await cursor.to_list(length=limit)
    
    return {"signals": signals, "count": len(signals)}

@api_router.get("/autotrading/paper-orders")
async def get_paper_orders(limit: int = 50):
    """Get paper trading positions from the unified positions collection."""
    cursor = database.positions.find({'paper': True}, {'_id': 0}).sort('opened_at', -1).limit(limit)
    orders = await cursor.to_list(length=limit)
    return {"orders": orders, "count": len(orders)}

@api_router.get("/autotrading/state")
async def get_autotrading_state():
    """Get current auto trading state"""
    global auto_trading_state
    
    # Reset daily stats if new day
    now = datetime.now(timezone.utc)
    if auto_trading_state.daily_start:
        if now.date() > auto_trading_state.daily_start.date():
            auto_trading_state.daily_trades = 0
            auto_trading_state.daily_pnl = 0.0
            auto_trading_state.daily_start = now
    else:
        auto_trading_state.daily_start = now
    
    return {
        "state": {
            "is_running": auto_trading_state.is_running,
            "last_check": auto_trading_state.last_check.isoformat() if auto_trading_state.last_check else None,
            "open_positions": auto_trading_state.open_positions,
            "daily_trades": auto_trading_state.daily_trades,
            "daily_pnl": auto_trading_state.daily_pnl,
            "last_trade_time": {k: v.isoformat() for k, v in auto_trading_state.last_trade_time.items()}
        }
    }

@api_router.post("/autotrading/reset-state")
async def reset_autotrading_state():
    """Reset auto trading state"""
    global auto_trading_state
    auto_trading_state = AutoTradingState()
    return {"status": "reset", "state": auto_trading_state.model_dump()}



# Include the router in the main app
app.include_router(api_router)

# ══════════════════════════════════════════════════════════════════
# WebSocket Live Price Feed — aggregates ticks every 1s per symbol
# ══════════════════════════════════════════════════════════════════

class LivePriceBroadcaster:
    """Manages WebSocket clients and broadcasts 1-second OHLCV aggregates."""

    def __init__(self):
        # symbol -> set of WebSocket connections
        self.clients: Dict[str, set] = {'MNQ': set(), 'MES': set()}

    async def connect(self, symbol: str, ws: WebSocket):
        await ws.accept()
        self.clients.setdefault(symbol, set()).add(ws)

    def disconnect(self, symbol: str, ws: WebSocket):
        self.clients.get(symbol, set()).discard(ws)

    async def broadcast(self, symbol: str, data: dict):
        # orjson é 3-5× mais rápido que json stdlib e suporta datetime/numpy natively
        payload = orjson.dumps(data).decode()
        dead = []
        for ws in self.clients.get(symbol, set()):
            try:
                # Timeout de 1s: cliente lento/travado não bloqueia o broadcast para todos
                await asyncio.wait_for(ws.send_text(payload), timeout=1.0)
            except (Exception, asyncio.TimeoutError):
                dead.append(ws)
        for ws in dead:
            self.clients.get(symbol, set()).discard(ws)

live_broadcaster = LivePriceBroadcaster()

# Referências das background tasks — necessário para cancelamento limpo no shutdown
_bg_tasks: list = []


async def live_price_broadcast_loop():
    """Background task: every 1s, read latest tick from buffers and broadcast.
    Also sends session_change heartbeats when CME session transitions (halt→globex, globex→rth, etc.)."""
    import time as _time

    # Track per-symbol 1-second candle state
    candle_state: Dict[str, dict] = {}
    _prev_cme_session: Optional[str] = None
    _heartbeat_counter = 0

    while True:
        await asyncio.sleep(1)
        try:
            _heartbeat_counter += 1

            # ── Session transition detection (every 15s) ──
            if _heartbeat_counter % 15 == 0:
                try:
                    current_session = _get_current_session_type()
                    from services.feed_health import is_cme_market_open
                    cme = is_cme_market_open()
                    cme_session = 'halted' if not cme['open'] else current_session

                    if _prev_cme_session is not None and cme_session != _prev_cme_session:
                        logger.info(
                            "SESSION TRANSITION detected: %s → %s (%s)",
                            _prev_cme_session, cme_session, cme.get('reason', '')
                        )

                        # ── Cache invalidation on session change ──
                        if _prev_cme_session == 'halted' and cme_session in ('rth', 'globex'):
                            logger.info("Session reopen: clearing stale caches (VP Session, VWAP, Analyze)")
                            # Clear VP Session cache (new session = new VP)
                            SessionVolumeProfileService._session_vps_cache.clear()
                            SessionVolumeProfileService._session_vps_cache_time.clear()
                            # Clear OHLCV cache on DataBento service
                            if hasattr(databento_service, '_ohlcv_cache'):
                                databento_service._ohlcv_cache.clear()
                                databento_service._ohlcv_cache_time.clear()

                        # Broadcast session_change to all WebSocket clients
                        change_payload = {
                            'type': 'session_change',
                            'session': cme_session,
                            'prev_session': _prev_cme_session,
                            'reason': cme.get('reason', ''),
                            'timestamp': int(_time.time()),
                        }
                        for symbol in ('MNQ', 'MES'):
                            await live_broadcaster.broadcast(symbol, change_payload)

                    _prev_cme_session = cme_session
                except Exception as e:
                    logger.warning(f"Session transition check error: {e}")

            for symbol in ('MNQ', 'MES'):
                if not live_broadcaster.clients.get(symbol):
                    continue  # no listeners, skip

                buf = live_data_service.buffers.get(symbol)
                if not buf or buf.last_price <= 0:
                    continue

                price = buf.last_price
                now_ts = int(_time.time())
                # Floor to current minute for candle timestamp
                candle_ts = now_ts - (now_ts % 60)

                prev = candle_state.get(symbol)
                if prev and prev['time'] == candle_ts:
                    # Same candle — update H/L/C and volume
                    prev['high'] = max(prev['high'], price)
                    prev['low'] = min(prev['low'], price)
                    prev['close'] = price
                    prev['volume'] = buf.total_trades
                else:
                    # New candle — start fresh
                    candle_state[symbol] = {
                        'time': candle_ts,
                        'open': price,
                        'high': price,
                        'low': price,
                        'close': price,
                        'volume': buf.total_trades,
                    }

                payload = {
                    'type': 'ohlcv_1s',
                    'symbol': symbol,
                    'candle': candle_state[symbol],
                    'last_price': price,
                    'timestamp': now_ts,
                }
                await live_broadcaster.broadcast(symbol, payload)
        except Exception as e:
            logger.warning(f"Live broadcast error: {e}")


@app.websocket("/api/ws/live/{symbol}")
async def websocket_live_price(websocket: WebSocket, symbol: str):
    """WebSocket endpoint for real-time 1s price updates."""
    if symbol not in ('MNQ', 'MES'):
        await websocket.close(code=4004, reason="Symbol not supported")
        return
    await live_broadcaster.connect(symbol, websocket)
    try:
        while True:
            # Keep connection alive; client doesn't need to send data
            # but we listen so we detect disconnect
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        live_broadcaster.disconnect(symbol, websocket)

_cors_origins_env = os.environ.get('CORS_ORIGINS', '')
_replit_domain = os.environ.get('REPLIT_DEV_DOMAIN', '')
_replit_domains = os.environ.get('REPLIT_DOMAINS', '')
_allowed_origins = []
if _cors_origins_env and _cors_origins_env != '*':
    _allowed_origins = [o.strip() for o in _cors_origins_env.split(',') if o.strip()]
else:
    # Always allow all Replit-proxied origins and localhost
    _allowed_origins = [
        "http://localhost:5000",
        "http://0.0.0.0:5000",
        "http://localhost:3000",
    ]
    if _replit_domain:
        _allowed_origins.append(f"https://{_replit_domain}")
        _allowed_origins.append(f"http://{_replit_domain}")
    for d in _replit_domains.split(','):
        d = d.strip()
        if d and f"https://{d}" not in _allowed_origins:
            _allowed_origins.append(f"https://{d}")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def scalp_signal_push_loop():
    """Background task: avalia sinal Scalp a cada 2s e faz push via WebSocket quando ACTIVE.
    Só publica quando há clientes conectados e o estado muda (evita flood de mensagens)."""
    _last_status: Dict[str, str] = {}
    while True:
        await asyncio.sleep(2)
        try:
            if scalp_engine is None:
                continue
            for sym in SCALP_SNAPSHOT_SYMBOLS:
                if not live_broadcaster.clients.get(sym):
                    continue  # nenhum cliente conectado — skip
                try:
                    sig = await scalp_engine.evaluate(sym)
                    status = sig.scalp_status
                    prev_status = _last_status.get(sym)
                    if status == prev_status:
                        continue  # sem mudança de estado
                    _last_status[sym] = status
                    if status == "ACTIVE_SIGNAL":
                        payload = {
                            "type":         "scalp_signal",
                            "symbol":       sym,
                            "scalp_status": status,
                            "direction":    sig.s1_direction,
                            "mode":         sig.mode,
                            "quality":      sig.s2_quality.value if sig.s2_quality else None,
                            "entry_price":  sig.s3_entry_price,
                            "stop_loss":    sig.s3_stop_loss_price,
                            "take_profit":  sig.s3_take_profit_price,
                            "timestamp":    sig.timestamp,
                        }
                        logger.info(f"ScalpSignalPush [{sym}]: ACTIVE_SIGNAL pushed via WebSocket")
                    elif prev_status == "ACTIVE_SIGNAL":
                        payload = {
                            "type":         "scalp_signal_expired",
                            "symbol":       sym,
                            "scalp_status": status,
                            "timestamp":    sig.timestamp,
                        }
                        logger.info(f"ScalpSignalPush [{sym}]: sinal expirou → {status}")
                    else:
                        continue  # transição entre dois estados não-ACTIVE — sem push
                    await live_broadcaster.broadcast(sym, payload)
                except Exception as e:
                    logger.debug(f"ScalpSignalPush [{sym}]: {e}")
        except Exception as e:
            logger.warning(f"ScalpSignalPush loop error: {e}")


async def scalp_snapshot_loop():
    """Background task: grava snapshots do ScalpEngine a cada 30s durante horas de mercado."""
    logger.info(
        f"Scalp Snapshot Loop: Starting ({SCALP_SNAPSHOT_INTERVAL}s interval, "
        f"{len(SCALP_SNAPSHOT_SYMBOLS)} symbols)"
    )
    _welford_cycle = 0
    _WELFORD_PERSIST_EVERY = 10  # a cada 10 ciclos × 30s = 5 minutos
    while True:
        try:
            await asyncio.sleep(SCALP_SNAPSHOT_INTERVAL)

            # ── Camada 1: guard de integridade ────────────────────────────────
            # Impede snapshots antes de warm_caches() completar.
            # Garante que VWAP e VP D-1 foram buscados da API DataBento
            # e que os seeds do startup foram sobrescritos com dados frescos.
            if not _backend_ready:
                logger.debug("Snapshot loop: aguardando backend_ready — snapshot ignorado")
                continue

            if trading_calendar:
                now = datetime.now(timezone.utc)
                session = trading_calendar.get_session_info(now)
                if session.get("is_cme_halted", False):
                    continue

            if scalp_engine is None:
                continue

            try:
                result = await record_scalp_snapshots(
                    database, scalp_engine, trading_calendar=trading_calendar
                )
                recorded = result.get("recorded", 0)
                errors   = result.get("errors", [])
                logger.info(
                    f"Scalp Snapshot Loop: {recorded}/{len(SCALP_SNAPSHOT_SYMBOLS)} "
                    f"gravados @ {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC "
                    f"| reason={result.get('reason')} errors={errors}"
                )
            except Exception as e:
                logger.warning(f"Scalp snapshot batch error: {e}")

            # ── Welford persistence: a cada 5 minutos ─────────────────────────
            # Garante que os accumuladores Z-Score sobrevivem a restarts.
            _welford_cycle += 1
            if _welford_cycle >= _WELFORD_PERSIST_EVERY:
                _welford_cycle = 0
                try:
                    await delta_zonal_service.persist_welford(database)
                    logger.debug("Welford accumulators persistidos (5min cycle)")
                except Exception as e:
                    logger.warning(f"Welford persist error: {e}")

        except Exception as e:
            logger.error(f"Scalp Snapshot Loop error: {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    # Para o auto trader e fecha o cliente HTTP persistente
    try:
        await scalp_auto_trader.stop()
    except Exception:
        pass
    # Persiste Welford antes de encerrar — garante que Z-Scores sobrevivem ao restart
    try:
        await delta_zonal_service.persist_welford(database)
        logger.info("Welford accumulators persistidos no shutdown")
    except Exception as e:
        logger.warning(f"Welford persist no shutdown falhou: {e}")
    # Cancela todas as background tasks registadas
    for task in _bg_tasks:
        if not task.done():
            task.cancel()
    if _bg_tasks:
        await asyncio.gather(*_bg_tasks, return_exceptions=True)
    _bg_tasks.clear()
    await feed_health_monitor.stop()
    await live_data_service.stop()
    client.close()

# Live Data Service startup (controlled by ENABLE_LIVE_DATA env var)
@app.on_event("startup")
async def startup_live_data():
    global scalp_engine, scalp_auto_trader
    enable_live = os.environ.get('ENABLE_LIVE_DATA', 'false').lower() == 'true'
    if enable_live and DATABENTO_API_KEY:
        logger.info("Starting DataBento Live Data Service...")
        asyncio.create_task(live_data_service.start())
    else:
        logger.info("Live Data Service disabled (set ENABLE_LIVE_DATA=true to enable)")

    # ── Tarefas V3 desabilitadas ───────────────────────────────────────────────
    # Sistema V3 não é utilizado. Scalp usa MacroContextService diretamente.
    # Nenhum trade é gerado pelo V3 → não há posições em `positions` para fechar.
    #
    # Para reativar, descomentar os blocos abaixo:
    # asyncio.create_task(eod_monitor_loop())
    # asyncio.create_task(snapshot_loop())
    #
    # n3_watcher = N3Watcher(
    #     v3_engine=v3_engine, execute_callback=execute_v3_signal,
    #     live_data_service=live_data_service, timeout_callback=_n3_timeout_callback,
    # )

    # Start Scalp Snapshot background task (walk-forward data for Auto-Tune)
    _bg_tasks.append(asyncio.create_task(scalp_snapshot_loop()))
    # Start Scalp Signal push loop (WebSocket push when signal becomes ACTIVE — 2s poll)
    _bg_tasks.append(asyncio.create_task(scalp_signal_push_loop()))

    # Initialize Overnight Inventory Service
    from services.overnight_inventory_service import overnight_inventory_service
    overnight_inventory_service.set_db(database)
    overnight_inventory_service.set_live_data_service(live_data_service)
    logger.info("Overnight Inventory Service initialized")

    # Start Live Price WebSocket broadcaster (1s aggregation)
    _bg_tasks.append(asyncio.create_task(live_price_broadcast_loop()))
    logger.info("Live Price Broadcaster started (1s interval)")

    # Start Feed Health Monitor (periodic health checks + MongoDB logging)
    _bg_tasks.append(asyncio.create_task(feed_health_monitor.start()))
    logger.info("Feed Health Monitor started (interval: 15s)")

    # Ensure MongoDB indexes for snapshots (Scalp only)
    await ensure_scalp_snapshot_indexes(database)

    # Load Welford accumulators from MongoDB (warm-start Z-Score)
    await delta_zonal_service.load_welford(database)
    # Seed cold accumulators from historical snapshots (last 30 days)
    await delta_zonal_service.seed_welford_from_snapshots(database, days=30)
    logger.info("Welford Z-Score accumulators loaded and seeded from MongoDB")

    # Initialize Scalp Engine + AutoTrader
    from services.scalp_engine import ScalpEngine
    from services.scalp_auto_trader import ScalpAutoTrader
    from routes.scalp import _get_scalp_config as _scalp_cfg_getter

    async def get_scalp_market_levels(symbol: str) -> dict:
        """
        Monta o contexto de mercado para o ScalpEngine:
        VWAP ±1σ/2σ/3σ, VP Sessão (POC/VAH/VAL), VP D-1, ONH/ONL.
        Usa os mesmos serviços do V3 — resultado cacheado internamente por cada serviço.
        """
        result = {}
        try:
            # ── VWAP + bandas de desvio padrão ──────────────────────────────
            # Usa get_session_vwaps (merge live buffer, 20-min lag, 60s cache)
            # em vez de calculate_session_vwaps (2h lag, sem buffer live).
            session_vwaps = await get_session_vwaps(symbol)
            from zoneinfo import ZoneInfo as _ZI
            _et_tz    = _ZI("America/New_York")
            now_local = datetime.now(_et_tz)
            hour      = now_local.hour + now_local.minute / 60

            if 9.5 <= hour < 16.0:
                active_vwap = session_vwaps.get("session_ny") or {}
            else:
                active_vwap = session_vwaps.get("globex") or {}

            vwap_val    = active_vwap.get("vwap", 0.0) or 0.0
            std_val     = active_vwap.get("std", 0.0) or 0.0
            vwap_is_seed = session_vwaps.get("_is_seed", False)
            if vwap_val > 0:
                result.update({
                    "vwap":         vwap_val,
                    "vwap_std":     std_val,
                    "vwap_upper_1": active_vwap.get("upper_1", round(vwap_val + std_val, 2)),
                    "vwap_lower_1": active_vwap.get("lower_1", round(vwap_val - std_val, 2)),
                    "vwap_upper_2": active_vwap.get("upper_2", round(vwap_val + 2 * std_val, 2)),
                    "vwap_lower_2": active_vwap.get("lower_2", round(vwap_val - 2 * std_val, 2)),
                    "vwap_upper_3": active_vwap.get("upper_3", round(vwap_val + 3 * std_val, 2)),
                    "vwap_lower_3": active_vwap.get("lower_3", round(vwap_val - 3 * std_val, 2)),
                    # Proveniência — flui até ScalpSignal.vwap_source → data_quality no snapshot
                    "vwap_source":  "seed" if vwap_is_seed else "live",
                })
        except Exception as e:
            logger.debug(f"get_scalp_market_levels: VWAP error for {symbol}: {e}")

        try:
            # ── Volume Profile Sessão + D-1 ──────────────────────────────────
            # VP Sessão ativa — determinado pelo horário ET (mesmo critério do VWAP):
            #   RTH   (09:30–16:00 ET) → rth_vp   (barras RTH apenas; nunca válido na Globex)
            #   Globex (qualquer outro) → daily_vp  (barras desde overnight start)
            # D-1 (d1_poc/d1_vah/d1_val): sempre da sessão anterior (histórico).
            vps      = await session_vp_service.calculate_session_vps(symbol)
            rth_vp   = vps.get("rth_vp")    or {}
            daily_vp = vps.get("daily_vp")  or {}
            prev_vp  = vps.get("prev_day_vp") or {}

            # Seleção explícita por sessão — sem fallback entre RTH e Globex
            is_rth     = 9.5 <= hour < 16.0   # reutiliza `hour` do bloco VWAP acima
            session_vp = rth_vp if is_rth else daily_vp
            _vp_session_src = vps.get("_vp_session_source", "none")
            if session_vp.get("poc", 0):
                result.update({
                    "poc":              session_vp.get("poc", 0.0),
                    "vah":              session_vp.get("vah", 0.0),
                    "val":              session_vp.get("val", 0.0),
                    "vp_session":       "rth" if is_rth else "globex",
                    # Proveniência — flui até ScalpSignal.vp_session_source
                    "vp_session_source": _vp_session_src,
                })
            if prev_vp.get("poc", 0):
                result.update({
                    "d1_poc":      prev_vp.get("poc",     0.0),
                    "d1_vah":      prev_vp.get("vah",     0.0),
                    "d1_val":      prev_vp.get("val",     0.0),
                    # Extremos absolutos RTH D-1 (máximo/mínimo do pregão anterior)
                    "d1_high":     prev_vp.get("d1_high", 0.0),
                    "d1_low":      prev_vp.get("d1_low",  0.0),
                    # Proveniência D-1 — flui até ScalpSignal.vp_d1_source
                    "vp_d1_source": vps.get("_vp_d1_source", "none"),
                })
        except Exception as e:
            logger.debug(f"get_scalp_market_levels: VP error for {symbol}: {e}")

        try:
            # ── Overnight Inventory (ONH / ONL / ON_POC) ─────────────────────
            from services.overnight_inventory_service import overnight_inventory_service as _ois
            inv = await _ois.get_today_inventory(symbol)
            if inv:
                result.update({
                    "onh":    inv.get("onh", 0.0),
                    "onl":    inv.get("onl", 0.0),
                    "on_poc": inv.get("on_poc", 0.0),
                })
        except Exception as e:
            logger.debug(f"get_scalp_market_levels: ONH/ONL error for {symbol}: {e}")

        try:
            # ── Initial Balance (IBH / IBL — cristaliza às 10:30 ET) ─────────
            from services.initial_balance_service import initial_balance_service as _ibs
            _ibs.set_live_data_service(live_data_service)
            ib = _ibs.get_ib_levels(symbol)
            result.update({
                "ibh":       ib.get("ibh", 0.0),
                "ibl":       ib.get("ibl", 0.0),
                "ib_locked": ib.get("ib_locked", False),
            })
        except Exception as e:
            logger.debug(f"get_scalp_market_levels: IBH/IBL error for {symbol}: {e}")

        return result

    # ── F5: Async macro_context_getter (Term Structure + Gamma Walls) ────────
    async def _scalp_macro_context_getter(symbol: str, futures_price: float = 0.0):
        """
        Closure que passa volatility_service + gamma_ratio_service ao engine
        sem acoplamento directo ao módulo routes/scalp.py.
        """
        from routes.scalp import _build_macro_context as _rmc, set_scalp_deps as _ssd
        # Garante que as deps estão injectadas (podem ainda não ter sido na primeira chamada)
        return await _rmc(symbol, futures_price=futures_price)

    scalp_engine = ScalpEngine(live_data_service, delta_zonal_service,
                               levels_getter=get_scalp_market_levels,
                               macro_context_getter=_scalp_macro_context_getter)
    # Restaura modo + SL/TP/BE salvos no config (FLOW mode usa SCALP_PARAMS in-memory)
    scalp_cfg_doc = None
    try:
        scalp_cfg_doc = await database["scalp_config"].find_one({"id": "default"}, {"_id": 0})
        if scalp_cfg_doc:
            if scalp_cfg_doc.get("mode"):
                scalp_engine.set_mode(scalp_cfg_doc["mode"])
            # Restaura SCALP_PARAMS para que o modo FLOW use os ticks configurados pelo utilizador
            from services.scalp_engine import SCALP_PARAMS
            for sym, sl_key, tp_key, be_key in [
                ("MNQ", "sl_ticks_mnq", "tp_ticks_mnq", "be_ticks_mnq"),
                ("MES", "sl_ticks_mes", "tp_ticks_mes", "be_ticks_mes"),
            ]:
                if scalp_cfg_doc.get(sl_key):
                    SCALP_PARAMS[sym]["sl_ticks"] = int(scalp_cfg_doc[sl_key])
                if scalp_cfg_doc.get(tp_key):
                    SCALP_PARAMS[sym]["tp_ticks"] = int(scalp_cfg_doc[tp_key])
                if scalp_cfg_doc.get(be_key):
                    SCALP_PARAMS[sym]["be_ticks"] = int(scalp_cfg_doc[be_key])
            logger.info(
                "Scalp SCALP_PARAMS restaurados: MNQ=%s MES=%s",
                SCALP_PARAMS.get("MNQ"), SCALP_PARAMS.get("MES"),
            )
        # Restaura session_params (NY / GLOBEX) para scalp_zones._SESSION_PARAMS
        sp_map = scalp_cfg_doc.get("session_params") or {} if scalp_cfg_doc else {}
        if sp_map:
            try:
                import services.scalp_zones as _sz
                for _sg, _sp in sp_map.items():
                    if _sg in ("NY", "GLOBEX") and isinstance(_sp, dict):
                        _sz.update_session_params(_sg, _sp)
                        logger.info("Scalp session_params restaurados: [%s] %s", _sg, list(_sp.keys()))
            except Exception as _esp:
                logger.warning("Scalp session_params restore warning: %s", _esp)

        # Restaura parâmetros zones per-símbolo (zones_mnq / zones_mes) para session_params
        _ZONES_LIVE_KEYS = [
            "score_strong_thresh", "score_moderate_thresh",
            "ofi_slow_fade_thresh", "ofi_slow_momentum_thresh",
        ]
        if scalp_cfg_doc:
            try:
                import services.scalp_zones as _sz2
                _sym_sp: dict = {}
                for _sym in ("mnq", "mes"):
                    _ov = scalp_cfg_doc.get(f"zones_{_sym}") or {}
                    for _k in _ZONES_LIVE_KEYS:
                        if _k in _ov:
                            _sym_sp[f"zones_{_k}_{_sym}"] = _ov[_k]
                if _sym_sp:
                    for _sg2 in ("NY", "GLOBEX"):
                        _sz2.update_session_params(_sg2, _sym_sp)
                    logger.info("Scalp zones per-símbolo restaurados: %s", list(_sym_sp.keys()))
            except Exception as _e2:
                logger.warning("Scalp zones per-symbol restore warning: %s", _e2)
    except Exception as _e:
        logger.warning("Scalp config restore warning: %s", _e)

    # ── ATR warm-start: seed _atr_cache do último snapshot válido ────────────
    # Evita WARMING_UP nos primeiros 6 minutos após todo restart.
    # O cache expira em 60s (ATR_CACHE_TTL) e o engine recalcula a partir de
    # barras reais quando o buffer tiver trades suficientes.
    try:
        import time as _time
        from services.scalp_engine import _atr_cache
        for _sym in ("MNQ", "MES"):
            _snap = await database["scalp_snapshots"].find_one(
                {"symbol": _sym, "indicators.atr_m1": {"$gt": 0}},
                sort=[("recorded_at", -1)],
                projection={"indicators.atr_m1": 1, "_id": 0},
            )
            if _snap:
                _atr_val = (_snap.get("indicators") or {}).get("atr_m1")
                if _atr_val and _atr_val > 0:
                    _atr_cache[_sym] = {"atr": _atr_val, "ts": _time.monotonic(), "from_bars": True}
                    logger.info("Scalp ATR warm-start: %s atr_m1=%.2f (seeded from last snapshot)", _sym, _atr_val)
    except Exception as _atr_e:
        logger.warning("Scalp ATR warm-start failed (non-critical): %s", _atr_e)

    # ── VWAP + VP warm-start: seed caches do último snapshot válido ──────────
    # Evita esperar 5 candles live (~5 min) para ter VWAP/POC/VAH/VAL.
    # Após 2s, warm_caches() sobrescreve com dados frescos da API histórica.
    try:
        for _sym in ("MNQ", "MES"):
            _snap = await database["scalp_snapshots"].find_one(
                {"symbol": _sym, "$or": [{"levels.vwap": {"$gt": 0}}, {"levels.poc": {"$gt": 0}}]},
                sort=[("recorded_at", -1)],
                projection={"levels": 1, "_id": 0},
            )
            if not _snap:
                continue
            _lvl = _snap.get("levels") or {}

            # ── Seed VWAP (get_session_vwaps function-level 60s cache) ──
            _vwap = _lvl.get("vwap", 0.0) or 0.0
            _std  = _lvl.get("vwap_std", 0.0) or 0.0
            if _vwap > 0:
                _vwap_seed = {
                    "vwap":    _vwap, "std": _std,
                    "upper_1": _lvl.get("vwap_upper_1", round(_vwap + _std, 2)),
                    "lower_1": _lvl.get("vwap_lower_1", round(_vwap - _std, 2)),
                    "upper_2": _lvl.get("vwap_upper_2", round(_vwap + 2*_std, 2)),
                    "lower_2": _lvl.get("vwap_lower_2", round(_vwap - 2*_std, 2)),
                    "upper_3": _lvl.get("vwap_upper_3", round(_vwap + 3*_std, 2)),
                    "lower_3": _lvl.get("vwap_lower_3", round(_vwap - 3*_std, 2)),
                    "source": "snapshot_seed", "label": "VWAP (seed)",
                }
                _ck = f"session_vwaps_standalone_{_sym}"
                if not hasattr(get_session_vwaps, '_cache'):
                    get_session_vwaps._cache      = {}
                    get_session_vwaps._cache_time  = {}
                # _is_seed=True: sinaliza ao get_scalp_market_levels que este VWAP
                # vem do snapshot anterior, não de um cálculo DataBento fresco.
                # Será substituído por _is_seed=False quando get_session_vwaps()
                # buscar dados reais (dentro de ~10s via warm_caches).
                get_session_vwaps._cache[_ck]      = {
                    "globex": _vwap_seed, "session_ny": _vwap_seed, "_is_seed": True,
                }
                get_session_vwaps._cache_time[_ck] = datetime.now(timezone.utc)
                logger.info("Scalp VWAP warm-start: %s vwap=%.2f (seeded from snapshot)", _sym, _vwap)

            # ── Seed VP Session cache (poc/vah/val para o engine) — session-aware ──
            # Globex (18:00–09:30 ET) → daily_vp   | RTH (09:30–16:00) → rth_vp
            # rth_vp NUNCA é válido durante Globex e vice-versa.
            _poc    = _lvl.get("poc",    0.0) or 0.0
            _d1_poc = _lvl.get("d1_poc", 0.0) or 0.0
            if _poc > 0 or _d1_poc > 0:
                from zoneinfo import ZoneInfo as _ZI2
                _et_hour_now  = (datetime.now(_ZI2("America/New_York")).hour
                                 + datetime.now(_ZI2("America/New_York")).minute / 60)
                _startup_is_rth = 9.5 <= _et_hour_now < 16.0
                _poc_seed = {"poc": _poc, "vah": _lvl.get("vah", 0.0), "val": _lvl.get("val", 0.0)} if _poc > 0 else None
                _d1_seed  = {"poc": _d1_poc, "vah": _lvl.get("d1_vah", 0.0), "val": _lvl.get("d1_val", 0.0)} if _d1_poc > 0 else None
                _vps_seed = {
                    "daily_vp":    _poc_seed if not _startup_is_rth else None,
                    "rth_vp":      _poc_seed if _startup_is_rth     else None,
                    "prev_day_vp": _d1_seed,
                }
                _vps_key  = f"vps_{_sym}"
                session_vp_service._session_vps_cache[_vps_key]      = _vps_seed
                session_vp_service._session_vps_cache_time[_vps_key] = datetime.now(timezone.utc)
                _sess_lbl = "RTH" if _startup_is_rth else "Globex"
                logger.info("Scalp VP warm-start: %s poc=%.2f d1_poc=%.2f (seeded=%s)", _sym, _poc, _d1_poc, _sess_lbl)
    except Exception as _wp_e:
        logger.warning("Scalp VWAP/VP warm-start failed (non-critical): %s", _wp_e)

    # ── RTH open price warm-start (Fix 3a + Fix 5) ───────────────────────────
    # O snapshot já guarda levels.rth_open_price mas o engine nunca o restaurava.
    # Sem este restore, o primeiro tick após restart sobrescreve o RTH open
    # com o preço mid-session, corrompendo Fix 3a (gap_open) e Fix 5 (RTH bias).
    # Filtra apenas snapshots DE HOJE em ET para não importar o open de ontem.
    try:
        from zoneinfo import ZoneInfo as _ZI_RTH
        _et_now_rth   = datetime.now(_ZI_RTH("America/New_York"))
        _today_et_rth = _et_now_rth.strftime("%Y-%m-%d")
        _et_day_start_rth = datetime.combine(
            _et_now_rth.date(), datetime.min.time(), tzinfo=_ZI_RTH("America/New_York")
        ).astimezone(timezone.utc)
        for _sym in ("MNQ", "MES"):
            _snap_rth = await database["scalp_snapshots"].find_one(
                {
                    "symbol": _sym,
                    "recorded_at": {"$gte": _et_day_start_rth},
                    "levels.rth_open_price": {"$gt": 0},
                },
                sort=[("recorded_at", -1)],
                projection={"levels.rth_open_price": 1, "_id": 0},
            )
            if _snap_rth:
                _rth_px = (_snap_rth.get("levels") or {}).get("rth_open_price")
                if _rth_px and _rth_px > 0:
                    _rth_key = f"{_sym}:{_today_et_rth}"
                    scalp_engine._rth_open_prices[_rth_key] = _rth_px
                    logger.info(
                        "Scalp RTH open warm-start: %s rth_open=%.2f key=%s",
                        _sym, _rth_px, _rth_key,
                    )
    except Exception as _rth_e:
        logger.warning("Scalp RTH open warm-start failed (non-critical): %s", _rth_e)

    # ── Session HOD/LOD warm-start (Fix 3c range_consumed) ───────────────────
    # Sem este restore, após restart o HOD=0 e LOD=∞ até novos extremos.
    # Fix 3c calcula (HOD-LOD)/D1_RANGE — com HOD=LOD=current_price o ratio
    # fica 0 e Fix 3c nunca suprime fades em dias de range extremo pós-restart.
    # Restaura max/min do last_price de todos os snapshots de hoje em ET.
    try:
        from zoneinfo import ZoneInfo as _ZI_HOD
        _et_now_hod     = datetime.now(_ZI_HOD("America/New_York"))
        _today_et_hod   = _et_now_hod.strftime("%Y-%m-%d")
        _et_day_start_hod = datetime.combine(
            _et_now_hod.date(), datetime.min.time(), tzinfo=_ZI_HOD("America/New_York")
        ).astimezone(timezone.utc)
        for _sym in ("MNQ", "MES"):
            _hod_prices: list = []
            async for _p_doc in database["scalp_snapshots"].find(
                {"symbol": _sym, "recorded_at": {"$gte": _et_day_start_hod}, "last_price": {"$gt": 0}},
                projection={"last_price": 1, "_id": 0},
            ):
                _lp = _p_doc.get("last_price")
                if _lp and _lp > 0:
                    _hod_prices.append(_lp)
            if _hod_prices:
                _hod_key = f"{_sym}:{_today_et_hod}"
                scalp_engine._session_hod[_hod_key] = max(_hod_prices)
                scalp_engine._session_lod[_hod_key] = min(_hod_prices)
                logger.info(
                    "Scalp HOD/LOD warm-start: %s HOD=%.2f LOD=%.2f (%d snapshots)",
                    _sym, max(_hod_prices), min(_hod_prices), len(_hod_prices),
                )
    except Exception as _hod_e:
        logger.warning("Scalp HOD/LOD warm-start failed (non-critical): %s", _hod_e)

    scalp_auto_trader = ScalpAutoTrader()
    # config_getter precisa do database; wira dependências primeiro
    set_scalp_deps(
        database, scalp_engine, live_data_service,
        auto_trader=scalp_auto_trader,
        gamma_ratio_service=gamma_ratio_service,
        # volatility_service removido: Scalp usa MacroContextService direto
    )
    scalp_auto_trader.set_deps(database, scalp_engine, live_data_service, _scalp_cfg_getter, trading_calendar=trading_calendar)

    # Cria índices na coleção scalp_trades
    try:
        await database["scalp_trades"].create_index([("created_at", -1)])
        await database["scalp_trades"].create_index([("state", 1)])
        await database["scalp_trades"].create_index([("symbol", 1)])
        await database["scalp_trades"].create_index([("id", 1)], unique=True)
        # Índice composto para dashboard: filtros comuns são symbol+state com ordenação por data
        await database["scalp_trades"].create_index(
            [("symbol", 1), ("state", 1), ("created_at", -1)],
            name="scalp_trades_sym_state_ts",
        )
        # Índice composto para separar paper vs live nas estatísticas
        await database["scalp_trades"].create_index(
            [("paper", 1), ("state", 1), ("created_at", -1)],
            name="scalp_trades_paper_state_ts",
        )
        # Índice de calibração granular — queries por zone_type para win rate por combinação
        await database["scalp_trades"].create_index(
            [("zone_type", 1), ("state", 1), ("created_at", -1)],
            name="scalp_trades_zone_state_ts",
        )
        # Índice de sessão — AutoTune por sessão (RTH_OPEN / RTH_MID / RTH_CLOSE / OVERNIGHT)
        await database["scalp_trades"].create_index(
            [("symbol", 1), ("session_label", 1), ("created_at", -1)],
            name="scalp_trades_sym_session_ts",
        )
    except Exception:
        pass

    # Índices de autenticação — TTL expira sessões automaticamente via MongoDB
    try:
        # TTL: MongoDB deleta documentos quando expires_at é atingido (expireAfterSeconds=0)
        await database["user_sessions"].create_index(
            "expires_at", expireAfterSeconds=0, name="ttl_session_expiry"
        )
        await database["user_sessions"].create_index(
            "session_token", unique=True, name="unique_session_token"
        )
        await database["users"].create_index(
            "email", unique=True, name="unique_user_email"
        )
    except Exception:
        pass

    # Auto-inicia o loop se configurado
    try:
        if scalp_cfg_doc and scalp_cfg_doc.get("auto_trade"):
            await scalp_auto_trader.start()
            logger.info("Scalp AutoTrader: auto-iniciado (auto_trade=True no config)")
    except Exception:
        pass

    logger.info("Scalp Engine initialized (S1/S2/S3, mode=%s)", scalp_engine.get_mode())

    # Start Walk-Forward Scheduler (if schedule exists) — V2 Legacy
    from services.scheduler import start_scheduler
    await start_scheduler(database)
    logger.info("Walk-Forward Scheduler initialized")

    # Start Scalp Auto-Tune Scheduler (if schedule exists)
    from services.scalp_scheduler import start_scalp_scheduler, ensure_scalp_schedule_indexes
    await ensure_scalp_schedule_indexes(database)
    await start_scalp_scheduler(database)
    logger.info("Scalp Auto-Tune Scheduler initialized")

    # Start Combined Analysis Scheduler (D1×D2)
    from services.scalp_combined_service import start_combined_scheduler
    await start_combined_scheduler(database)
    logger.info("Combined Analysis Scheduler initialized")

    # Start Atlas Storage Monitor (checks every 1h, sends Telegram when space is low)
    from services.atlas_storage_monitor import start_storage_monitor
    atlas_plan = os.environ.get("ATLAS_PLAN", "M0")
    storage_started = start_storage_monitor(database, plan=atlas_plan)
    if storage_started:
        logger.info("Atlas Storage Monitor iniciado (plano=%s)", atlas_plan)

    # Start GitHub Backup Scheduler (runs every 6h if GITHUB_TOKEN configured)
    from services.github_backup_service import start_backup_scheduler
    backup_started = start_backup_scheduler(database, interval_hours=6)
    if backup_started:
        logger.info("GitHub Backup Scheduler initialized (6h interval → ventonorte21/Quantum_v2)")
    else:
        logger.info("GitHub Backup Scheduler: GITHUB_TOKEN não configurado — backup automático inactivo")

    # Mark backend as ready immediately so the port health check passes.
    # warm_caches runs in the background — first requests may use seed/fallback data.
    global _backend_ready
    _backend_ready = True
    logger.info("Backend READY — port open, warm_caches starting in background")

    # Pre-warm all macro data caches in the background (non-blocking).
    async def _bg_warm():
        logger.info("Pre-warming macro data caches...")
        try:
            await asyncio.wait_for(warm_caches(), timeout=60)
        except asyncio.TimeoutError:
            logger.warning("Cache warm-up timed out after 60s — some sources may be FALLBACK")
        except Exception as e:
            logger.error("Cache warm-up error: %s", e)
        logger.info("Cache warm-up complete")

    asyncio.create_task(_bg_warm())

    # ── Loops V3 desabilitados ─────────────────────────────────────────────────
    # O sistema Scalp não usa V3. Term Structure e Gamma são fornecidos pelo
    # MacroContextService (backend/services/macro_context_service.py) com cache
    # próprio. Os loops abaixo consumiam ~80% do event loop em RTH sem benefício.
    #
    # Para reativar V3, descomentar as três linhas abaixo:
    # asyncio.create_task(macro_refresh_loop())
    # asyncio.create_task(analyze_refresh_loop())
    # asyncio.create_task(v3_signal_prewarming_loop())


async def warm_caches():
    """Pre-warm caches no startup para o Scalp Engine.

    Fontes aquecidas:
      - MacroContextService: Term Structure (VIX/VIX3M) + Gamma (QQQ/SPY)
      - Economic calendar: eventos do dia (gate de notícias do Scalp)
      - SharedTradesService: trades históricos dos últimos 60min (VWAP/VP)

    Loops V3 (VIX/VXN/treasury/analyze) não são iniciados — o Scalp usa
    MacroContextService diretamente, sem passar pelo sistema V3.
    """
    global scalp_engine, scalp_auto_trader
    from services.macro_context_service import MacroContextService
    try:
        await asyncio.sleep(2)  # Aguarda WS DataBento conectar

        # ── Invalida caches seed antes de chamar fontes live ─────────────────
        # O seed foi injetado com TTL de 60s. Se warm_caches chamar
        # get_session_vwaps dentro desse TTL, o cache ainda é válido e retorna
        # o seed sem buscar na DataBento — deixando _is_seed=True.
        # Invalidando o cache seed aqui, forçamos um re-fetch DataBento real.
        _sc = getattr(get_session_vwaps, '_cache', {})
        for _sym in SCALP_SNAPSHOT_SYMBOLS:
            _ck = f"session_vwaps_standalone_{_sym}"
            if _sc.get(_ck, {}).get('_is_seed', False):
                _sc.pop(_ck, None)
                logger.debug("warm_caches: invalidou cache VWAP seed de %s para forçar re-fetch live", _sym)

        results = await asyncio.gather(
            MacroContextService.get_context("MNQ"),
            MacroContextService.get_context("MES"),
            economic_calendar_service.get_todays_events(),
            shared_trades_service.get_trades("MNQ", 60),
            shared_trades_service.get_trades("MES", 60),
            get_session_vwaps("MNQ"),
            get_session_vwaps("MES"),
            session_vp_service.calculate_session_vps("MNQ"),
            session_vp_service.calculate_session_vps("MES"),
            return_exceptions=True,
        )
        ok   = sum(1 for r in results if not isinstance(r, Exception))
        fail = sum(1 for r in results if isinstance(r, Exception))
        logger.info(
            "Cache warm-up (Scalp): %d/%d fontes carregadas (%d falha)",
            ok, len(results), fail,
        )
        from services.macro_context_service import MacroContextService as _MCS
        logger.info("MacroContextService status: %s", _MCS.cache_status())
    except Exception as e:
        logger.error("Cache warm-up falhou: %s", e)


# ── AutoTrading config in-process cache ──────────────────────────────────────
# Avoids a MongoDB read every 30s (eod_monitor) + every 60s×N symbols (snapshot_loop).
# Invalidated automatically after AUTOTRADE_CFG_TTL seconds, or explicitly by
# calling _invalidate_at_config() whenever the config is updated via the API.

_at_config_cache: Optional[Dict] = None
_at_config_cache_ts: float = 0.0
AUTOTRADE_CFG_TTL: float = 30.0     # seconds


async def _get_cached_at_config(db) -> Optional[Dict]:
    """Return autotrading_config from in-process cache (30s TTL)."""
    global _at_config_cache, _at_config_cache_ts
    import time as _time
    now_m = _time.monotonic()
    if _at_config_cache is not None and (now_m - _at_config_cache_ts) < AUTOTRADE_CFG_TTL:
        return _at_config_cache
    doc = await db.autotrading_config.find_one({'id': 'default'}, {'_id': 0})
    _at_config_cache    = doc
    _at_config_cache_ts = now_m
    return doc


def _invalidate_at_config() -> None:
    """Force cache expiry so the next reader fetches fresh data from MongoDB."""
    global _at_config_cache_ts
    _at_config_cache_ts = 0.0


# ── Serve React SPA (production only) ────────────────────────────────────────
# When frontend/build exists, FastAPI serves the static assets and returns
# index.html for every unmatched GET — enabling client-side routing.
# This catch-all MUST be registered last so all /api/* routes take priority.

import pathlib as _pathlib
from fastapi.staticfiles import StaticFiles as _StaticFiles
from fastapi.responses import FileResponse as _FileResponse

_FRONTEND_BUILD = _pathlib.Path(__file__).parent.parent / "frontend" / "build"

if _FRONTEND_BUILD.is_dir():
    _static_dir = _FRONTEND_BUILD / "static"
    if _static_dir.is_dir():
        app.mount("/static", _StaticFiles(directory=str(_static_dir)), name="frontend-static")

    def _public_file(name: str) -> _FileResponse:
        f = _FRONTEND_BUILD / name
        return _FileResponse(str(f)) if f.is_file() else _FileResponse(str(_FRONTEND_BUILD / "index.html"))

    @app.get("/favicon.ico",   include_in_schema=False)
    async def _srv_favicon():   return _public_file("favicon.ico")

    @app.get("/manifest.json", include_in_schema=False)
    async def _srv_manifest():  return _public_file("manifest.json")

    @app.get("/logo192.png",   include_in_schema=False)
    async def _srv_logo192():   return _public_file("logo192.png")

    @app.get("/logo512.png",   include_in_schema=False)
    async def _srv_logo512():   return _public_file("logo512.png")

    @app.get("/robots.txt",    include_in_schema=False)
    async def _srv_robots():    return _public_file("robots.txt")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_spa(full_path: str):
        candidate = _FRONTEND_BUILD / full_path
        if candidate.is_file():
            return _FileResponse(str(candidate))
        return _FileResponse(str(_FRONTEND_BUILD / "index.html"))


