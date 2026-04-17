import React, { useState, useEffect, useCallback, useRef, useMemo, Fragment } from "react";
import axios from "axios";
import {
  Lightning, ArrowUp, ArrowDown, Minus, Warning,
  CheckCircle, XCircle, Spinner, ArrowsCounterClockwise,
  Gauge, ChartBar, TrendUp, Pulse, Robot, Play, Stop,
  PaperPlaneTilt, Clock, CurrencyDollar, Trophy,
  Funnel, Gear, Globe, Newspaper, Prohibit, ArrowsOut, ChartLineUp,
} from "@phosphor-icons/react";
import ScalpStatusBar from "./ScalpStatusBar";

const API = `${process.env.REACT_APP_BACKEND_URL || ""}/api`;
const SYMBOLS = ["MNQ", "MES"];
const POLL_MS = 4000;
const AT_POLL_MS = 3000;

// ── Helpers visuais ──
function statusColor(s) {
  if (s === "ACTIVE_SIGNAL") return "text-emerald-400";
  if (s === "BLOCKED")       return "text-red-400";
  if (s === "NO_SIGNAL")     return "text-zinc-500";
  return "text-zinc-600";
}
function statusBg(s) {
  if (s === "ACTIVE_SIGNAL") return "border-emerald-500/30 bg-emerald-500/5";
  if (s === "BLOCKED")       return "border-red-500/30 bg-red-500/5";
  return "border-zinc-800/40 bg-zinc-900/20";
}
function regimeColor(r) {
  if (!r) return "text-zinc-500";
  if (r.includes("BULL") || r.includes("ABSORPTION_BUY")) return "text-emerald-400";
  if (r.includes("BEAR") || r.includes("ABSORPTION_SEL")) return "text-red-400";
  if (r.includes("REVERSAL"))                             return "text-purple-400";
  return "text-zinc-400";
}
function qualityBadge(q) {
  if (q === "STRONG")   return <span className="px-1.5 py-0.5 text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">FORTE</span>;
  if (q === "MODERATE") return <span className="px-1.5 py-0.5 text-[10px] font-bold bg-yellow-500/20 text-yellow-400 border border-yellow-500/30">MODERADO</span>;
  if (q === "WEAK")     return <span className="px-1.5 py-0.5 text-[10px] font-bold bg-orange-500/20 text-orange-400 border border-orange-500/30">FRACO</span>;
  return <span className="px-1.5 py-0.5 text-[10px] font-bold bg-zinc-800/60 text-zinc-500 border border-zinc-700/40">SEM TRADE</span>;
}
function pnlColor(v) {
  if (v == null) return "text-zinc-500";
  return v > 0 ? "text-emerald-400" : v < 0 ? "text-red-400" : "text-zinc-400";
}
function fmtUSD(v) {
  if (v == null) return "—";
  const s = v >= 0 ? "+" : "";
  return `${s}$${Math.abs(v).toFixed(2)}`;
}
function fmtPts(v) {
  if (v == null) return "—";
  const s = v >= 0 ? "+" : "";
  return `${s}${v.toFixed(2)}`;
}
function fmtDur(sec) {
  if (sec == null) return "—";
  if (sec < 60) return `${sec.toFixed(0)}s`;
  return `${(sec / 60).toFixed(1)}m`;
}

function OFIBar({ value, label }) {
  const pct = Math.min(100, Math.abs(value || 0) * 200);
  const pos = (value || 0) >= 0;
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex justify-between text-[10px] font-mono">
        <span className="text-zinc-500">{label}</span>
        <span className={pos ? "text-emerald-400" : "text-red-400"}>{value >= 0 ? "+" : ""}{(value||0).toFixed(4)}</span>
      </div>
      <div className="h-1 bg-zinc-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${pos ? "bg-emerald-500" : "bg-red-500"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function FilterRow({ label, filter }) {
  if (!filter) return null;
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-[10px] text-zinc-500 font-mono">{label}</span>
      <div className="flex items-center gap-1.5">
        {filter.value !== undefined && filter.value !== null && (
          <span className="text-[10px] font-mono text-zinc-400">
            {typeof filter.value === "number" ? filter.value.toFixed(4) : String(filter.value)}
          </span>
        )}
        {filter.passed === true  && <CheckCircle size={11} className="text-emerald-400" weight="fill" />}
        {filter.passed === false && <XCircle    size={11} className="text-red-400"     weight="fill" />}
        {filter.passed === null  && <Minus      size={11} className="text-zinc-600"                  />}
      </div>
    </div>
  );
}

function SignalCard({ symbol, signal, onExecute, executing, config, mode }) {
  if (!signal) return (
    <div className="border border-zinc-800/40 bg-zinc-900/30 p-4 flex items-center justify-center h-48">
      <div className="flex items-center gap-2 text-zinc-600">
        <Spinner size={14} className="animate-spin" />
        <span className="text-xs">Carregando {symbol}...</span>
      </div>
    </div>
  );

  const s1 = signal.s1 || {};
  const s2 = signal.s2 || {};
  const s3 = signal.s3 || {};
  const ind = signal.indicators || {};
  const status    = signal.scalp_status || "NO_DATA";
  const direction = s1.direction;
  const isZones   = (signal.mode || mode) === "ZONES";
  const zones     = signal.zones || {};
  const zNearby   = zones.nearby || [];
  const zActive   = zones.active_zone;
  const zRegime   = zones.day_regime;
  const zQuality  = zones.quality;
  const zS3Extra  = zones.s3_extra || {};
  const zBreakdown = zones.score_breakdown || null;

  return (
    <div className={`border ${statusBg(status)} p-4 flex flex-col gap-3 transition-all duration-300`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-base font-bold font-mono text-zinc-100">{symbol}</span>
          {direction === "LONG"  && <ArrowUp   size={15} weight="bold" className="text-emerald-400" />}
          {direction === "SHORT" && <ArrowDown size={15} weight="bold" className="text-red-400" />}
          {!direction            && <Minus     size={15} className="text-zinc-600" />}
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs font-mono font-bold ${statusColor(status)}`}>{status}</span>
          {status === "ACTIVE_SIGNAL" && <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />}
        </div>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-xl font-mono font-bold text-zinc-100">
          {ind.last_price > 0 ? ind.last_price.toLocaleString("en-US", { minimumFractionDigits: 2 }) : "—"}
        </span>
        <div className="flex items-center gap-2">
          <div className={`w-1.5 h-1.5 rounded-full ${ind.feed_connected ? "bg-emerald-400 animate-pulse" : "bg-red-500"}`} />
          <span className="text-[10px] text-zinc-500 font-mono">{ind.feed_connected ? "LIVE" : "OFF"}</span>
        </div>
      </div>
      <div className="border-t border-zinc-800/40 pt-2">
        {!isZones ? (
          <>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] text-zinc-500 uppercase tracking-wide">S1 Regime</span>
              <span className="text-[10px] font-mono text-zinc-500">conf: {s1.confidence?.toFixed(1) ?? "—"}/10</span>
            </div>
            <span className={`text-xs font-mono font-semibold ${regimeColor(s1.regime)}`}>
              {(s1.regime || "NO_DATA").replace(/_/g, " ")}
            </span>
            <div className="h-1 bg-zinc-800 rounded-full mt-1 overflow-hidden">
              <div className={`h-full rounded-full ${s1.confidence >= 7 ? "bg-emerald-500" : s1.confidence >= 4.5 ? "bg-yellow-500" : "bg-red-500"}`}
                   style={{ width: `${((s1.confidence || 0) / 10) * 100}%`, transition: "width 0.5s" }} />
            </div>
          </>
        ) : (
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-zinc-500 uppercase tracking-wide">Zona VWAP</span>
            <span className="text-[10px] font-mono text-amber-400/80">
              {(signal.levels?.vwap_zone || "—").replace(/_/g, " ")}
            </span>
          </div>
        )}
      </div>
      {isZones ? (
        /* ── Painel de Zonas ── */
        <div className="flex flex-col gap-2">
          {/* Regime badge */}
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-zinc-500 uppercase tracking-wide">Regime do Dia</span>
            <span className={`text-[10px] font-bold font-mono px-2 py-0.5 border ${
              zRegime === "EXPANSION_BULL" ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-400"
              : zRegime === "EXPANSION_BEAR" ? "bg-red-500/15 border-red-500/40 text-red-400"
              : zRegime === "BREAKOUT_BULL" ? "bg-cyan-500/15 border-cyan-500/40 text-cyan-400"
              : zRegime === "BREAKOUT_BEAR" ? "bg-orange-500/15 border-orange-500/40 text-orange-400"
              : zRegime === "ROTATION" ? "bg-yellow-500/15 border-yellow-500/40 text-yellow-400"
              : "bg-zinc-800/40 border-zinc-700/40 text-zinc-500"
            }`}>{(zRegime || "UNDEFINED").replace(/_/g, " ")}</span>
          </div>
          {/* ATR info */}
          <div className="flex items-center gap-3 text-[10px] font-mono text-zinc-500">
            <span>ATR <span className="text-blue-400 font-semibold">{ind.atr_m1?.toFixed(2) ?? "5.00"}</span> pts</span>
            <span>SL <span className="text-red-400 font-semibold">{zS3Extra?.sl_pts?.toFixed(2) ?? "—"}</span> pts</span>
            <span>TP <span className="text-emerald-400 font-semibold">{zS3Extra?.tp_pts?.toFixed(2) ?? "—"}</span> pts</span>
            {zS3Extra?.rr_ratio && <span>R:R <span className="text-zinc-300 font-semibold">{zS3Extra.rr_ratio}</span></span>}
          </div>
          {/* Zona ativa + badges de score */}
          {zActive ? (
            <div className={`border px-2 py-1.5 text-[10px] font-mono ${
              zActive.direction === "LONG"
                ? "border-emerald-500/40 bg-emerald-500/8 text-emerald-400"
                : "border-red-500/40 bg-red-500/8 text-red-400"}`}>
              <div className="flex items-center justify-between gap-1">
                <span className="font-bold truncate">{zActive.label}</span>
                <div className="flex items-center gap-1 shrink-0">
                  {zBreakdown?.ofi_slow_penalty < 0 && (
                    <span className="px-1.5 py-0.5 text-[9px] font-bold bg-orange-500/20 text-orange-400 border border-orange-500/40">
                      OFI LAGGING
                    </span>
                  )}
                </div>
              </div>
              <div className="text-zinc-400 mt-0.5">→ {zActive.target_label} @ {zActive.target?.toFixed(2)}</div>
              {zBreakdown && (
                <div className="flex items-center gap-2 mt-1 pt-1 border-t border-current/20 text-[9px] opacity-70">
                  <span>base <span className="font-bold">{zBreakdown.base_score?.toFixed(1)}</span></span>
                  {zBreakdown.ofi_slow_penalty < 0 && (
                    <span className="text-orange-400">OFI <span className="font-bold">{zBreakdown.ofi_slow_penalty?.toFixed(1)}</span></span>
                  )}
                  <span>total <span className="font-bold">{zBreakdown.total_score?.toFixed(1)}</span></span>
                </div>
              )}
            </div>
          ) : (
            <div className="border border-zinc-800/40 px-2 py-1.5 text-[10px] font-mono text-zinc-600">
              Nenhuma zona ativa — preço fora dos níveis de interesse
            </div>
          )}
          {/* Zonas próximas */}
          {zNearby.length > 0 && (
            <div className="flex flex-col gap-0.5">
              <span className="text-[9px] text-zinc-600 uppercase tracking-wide">Zonas Próximas</span>
              {zNearby.slice(0, 5).map((z, i) => (
                <div key={i} className={`flex items-center justify-between text-[9px] font-mono px-1.5 py-0.5 ${z.in_zone ? "bg-yellow-500/10 text-yellow-300" : "text-zinc-500"}`}>
                  <span className={`w-10 font-bold ${z.direction === "LONG" ? "text-emerald-500" : "text-red-500"}`}>{z.direction === "LONG" ? "▲ BUY" : "▼ SELL"}</span>
                  <span className="flex-1 truncate px-1">{z.label}</span>
                  <span>{z.distance?.toFixed(1)}pts</span>
                </div>
              ))}
            </div>
          )}
          {/* OFI fast como confirmação */}
          <OFIBar value={ind.ofi_fast || 0} label="OFI Fast (confirm.)" />
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <OFIBar value={ind.ofi_fast || 0} label="OFI Fast" />
          <OFIBar value={ind.ofi_slow || 0} label="OFI Slow" />
          <div className="flex items-center justify-between text-[10px] font-mono">
            <span className="text-zinc-500">CVD</span>
            <span className={ind.cvd_trend === "RISING" ? "text-emerald-400" : ind.cvd_trend === "FALLING" ? "text-red-400" : "text-zinc-500"}>
              {ind.cvd > 0 ? "+" : ""}{(ind.cvd || 0).toFixed(0)} ({ind.cvd_trend})
            </span>
          </div>
          {ind.absorption_flag && (
            <div className="flex items-center gap-1.5 bg-purple-500/10 border border-purple-500/30 px-2 py-1">
              <Gauge size={12} className="text-purple-400" />
              <span className="text-[10px] font-mono text-purple-400">ABSORÇÃO: {ind.absorption_side}</span>
            </div>
          )}
        </div>
      )}
      <div className="border-t border-zinc-800/40 pt-2">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[10px] text-zinc-500 uppercase tracking-wide">{isZones ? "Confirmação" : "S2"}</span>
          {qualityBadge(isZones ? zQuality : s2.quality)}
        </div>
        {!isZones && (
          <div className="flex flex-col divide-y divide-zinc-800/40">
            <>
              <FilterRow label="OFI Fast"    filter={s2.filters?.ofi_fast} />
              <FilterRow label="OFI Slow"    filter={s2.filters?.ofi_slow} />
              <FilterRow label="CVD"         filter={s2.filters?.cvd} />
              <FilterRow label="Delta Ratio" filter={s2.filters?.delta_ratio} />
            </>
          </div>
        )}
        {s2.block_reasons?.length > 0 && (
          <div className="mt-1.5 flex flex-col gap-0.5">
            {s2.block_reasons.slice(0, 2).map((r, i) => (
              <div key={i} className="flex items-center gap-1 text-[10px] text-red-400/80">
                <Warning size={10} /><span className="font-mono">{r}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {status === "ACTIVE_SIGNAL" && s3.entry_price && (
        <div className="border-t border-zinc-800/40 pt-2">
          <div className="grid grid-cols-3 gap-2 text-[10px] font-mono">
            <div><span className="text-zinc-600 block">SL</span><span className="text-red-400 font-semibold">{s3.stop_loss_price?.toFixed(2)}</span></div>
            <div className="text-center"><span className="text-zinc-600 block">Entrada</span><span className="text-zinc-200 font-semibold">{s3.entry_price?.toFixed(2)}</span></div>
            <div className="text-right"><span className="text-zinc-600 block">TP</span><span className="text-emerald-400 font-semibold">{s3.take_profit_price?.toFixed(2)}</span></div>
          </div>
          <div className="flex items-center justify-between mt-1 text-[10px] font-mono text-zinc-500">
            <span>BE: +{s3.breakeven?.toFixed(2)} pts</span>
            <span>Qty: {s3.quantity || 1}</span>
          </div>
        </div>
      )}
      <button onClick={() => onExecute(symbol)} disabled={executing || !config?.enabled}
        className={`w-full py-2 text-xs font-bold transition-all border flex items-center justify-center gap-1.5 ${
          status === "ACTIVE_SIGNAL"
            ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-400 hover:bg-emerald-500/30"
            : "bg-zinc-800/30 border-zinc-700/40 text-zinc-500 hover:border-zinc-600"
        } disabled:opacity-40 disabled:cursor-not-allowed`}>
        {executing ? <><Spinner size={12} className="animate-spin" />Executando...</> : <><Lightning size={12} weight="fill" />{config?.paper_trading !== false ? "Executar (Paper)" : "Executar Scalp"}</>}
      </button>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// Auto Trader Panel
// ══════════════════════════════════════════════════════════════════

const AUTO_DEFAULTS = {
  paper_trading: true,
  webhook_url: "",
  symbols_enabled: ["MNQ", "MES"],
  mnq_quantity: 1,
  mes_quantity: 1,
  cooldown_sec: 60,
  max_positions: 2,
  max_per_symbol: 1,
  max_total_contracts: 0,
  auto_interval_sec: 5,
  // Sizing mode
  sizing_mode: "fixed",
  max_qty_risk_pct: 5,
  // Track B
  track_b_enabled: false,
  // Conta & Risco
  account_size: 50000,
  risk_per_trade_pct: 1.0,
  max_daily_loss_pct: 2.0,
  // ATR
  atr_stop_multiplier: 1.5,
  atr_target_multiplier: 3.0,
  // Horário de Trading
  auto_hours_mode: true,
  globex_auto_enabled: false,
  trading_start_hour: 9,
  trading_end_hour: 16,
  avoid_first_minutes: 15,
  avoid_last_minutes: 15,
  pre_close_flatten_minutes: 30,
  eod_flatten_enabled: true,
  globex_flatten_before_ny_minutes: 5,
  // News Blackout
  news_blackout_enabled: true,
  news_blackout_minutes_before: 15,
  news_blackout_minutes_after: 15,
  // G-1 (legado)
  rth_only: true,
  // Gate por modo
  auto_trade_flow:  true,
  auto_trade_zones: true,
  // G-2: Qualidade mínima
  min_quality_rth_mnq:         "STRONG",
  min_quality_rth_mes:         "STRONG",
  min_quality_overnight_mnq:   "MODERATE",
  min_quality_overnight_mes:   "STRONG",
  // G-3: Circuit Breaker
  max_daily_loss_usd: 200,
  max_consecutive_losses: 3,
  // G-4: Limite diário
  max_daily_trades: 10,
  // Zone Types desactivados
  disabled_zone_types: [],
};

function Toggle({ on, onChange }) {
  return (
    <button onClick={() => onChange(!on)}
      className={`w-10 h-5 rounded-full transition-all relative shrink-0 ${on ? "bg-emerald-500" : "bg-zinc-700"}`}>
      <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${on ? "left-5" : "left-0.5"}`} />
    </button>
  );
}

function SectionHeader({ icon, label }) {
  return (
    <div className="flex items-center gap-2 border-b border-zinc-800/40 pb-2 mb-1">
      {icon}
      <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">{label}</span>
    </div>
  );
}

function AutoTraderPanel({ atStatus, config, onStart, onStop, onSaveConfig, onPaperToggle, onResetCB, starting, stopping, saving, saveOk, paperSaving, tradingStatus, signals, onFlattenAll, mode }) {
  const running  = atStatus?.status === "RUNNING";
  const stats    = atStatus?.session_stats || {};
  const cbActive = !!stats.circuit_breaker_active;

  const [local, setLocal] = useState({ ...AUTO_DEFAULTS, ...(config || {}), webhook_url: config?.webhook_url || "" });
  useEffect(() => {
    setLocal({ ...AUTO_DEFAULTS, ...(config || {}), webhook_url: config?.webhook_url || "" });
  }, [config]);
  const set = (k, v) => setLocal(prev => ({ ...prev, [k]: v }));

  // O auto-trade está efectivamente activo neste modo só se o loop estiver a correr
  // E o toggle deste modo específico estiver ligado
  const modeAutoOn       = mode === "FLOW" ? !!local.auto_trade_flow : !!local.auto_trade_zones;
  const effectiveRunning = running && modeAutoOn;

  const riskUSD    = ((local.account_size || 50000) * (local.risk_per_trade_pct || 1) / 100);


  const toggleSymbol = (sym) => {
    const cur = local.symbols_enabled || ["MNQ", "MES"];
    set("symbols_enabled", cur.includes(sym) ? cur.filter(s => s !== sym) : [...cur, sym]);
  };

  const recentSignals = signals
    ? Object.entries(signals).flatMap(([sym, s]) => s?.scalp_status === "ACTIVE_SIGNAL" ? [{ sym, ...s }] : [])
    : [];

  return (
    <div className="flex flex-col gap-4">

      {/* ── STATUS & CONTROLO (full width) ── */}
      <div className={`border p-4 flex flex-col gap-3 ${effectiveRunning ? "border-emerald-500/30 bg-emerald-500/5" : "border-zinc-800/40 bg-zinc-900/20"}`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Robot size={16} className={effectiveRunning ? "text-emerald-400" : "text-zinc-500"} weight="fill" />
            <span className="text-sm font-bold text-zinc-200">Scalp Auto Trader</span>
            <span className={`text-[10px] font-bold px-2 py-0.5 border ${effectiveRunning ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" : running && !modeAutoOn ? "bg-amber-500/10 text-amber-400/70 border-amber-500/20" : "bg-zinc-800/60 text-zinc-500 border-zinc-700/40"}`}>
              {effectiveRunning ? "RUNNING" : running && !modeAutoOn ? "DESLIGADO NESTE MODO" : (atStatus?.status || "STOPPED")}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={onStart} disabled={running || starting}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-all">
              {starting ? <Spinner size={12} className="animate-spin" /> : <Play size={12} weight="fill" />}Iniciar
            </button>
            <button onClick={onStop} disabled={!running || stopping}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold bg-red-500/20 border border-red-500/40 text-red-400 hover:bg-red-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-all">
              {stopping ? <Spinner size={12} className="animate-spin" /> : <Stop size={12} weight="fill" />}Parar
            </button>
            {onFlattenAll && (
              <button onClick={onFlattenAll}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold bg-orange-500/20 border border-orange-500/40 text-orange-400 hover:bg-orange-500/30 transition-all">
                <ArrowsOut size={12} />Flatten All
              </button>
            )}
          </div>
        </div>

        {!local.paper_trading && (
          <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/30 px-3 py-2">
            <Warning size={14} className="text-red-400 mt-0.5 shrink-0" />
            <span className="text-xs text-red-400">Modo REAL — trades enviados ao Tradovate via SignalStack. Verifique o webhook antes de iniciar.</span>
          </div>
        )}

        {cbActive && (
          <div className="flex items-start justify-between gap-2 bg-red-500/10 border border-red-500/40 px-3 py-2">
            <div className="flex items-start gap-2">
              <Warning size={14} className="text-red-400 mt-0.5 shrink-0" />
              <div className="flex flex-col">
                <span className="text-xs font-bold text-red-400">CIRCUIT BREAKER ATIVO — loop suspenso</span>
                {stats.circuit_breaker_reason && <span className="text-[10px] text-red-400/70 font-mono">{stats.circuit_breaker_reason}</span>}
              </div>
            </div>
            {onResetCB && (
              <button onClick={onResetCB} className="shrink-0 px-2 py-1 text-[10px] font-bold bg-red-500/20 border border-red-500/40 text-red-300 hover:bg-red-500/30 transition-all">
                Resetar CB
              </button>
            )}
          </div>
        )}

        {effectiveRunning && (
          <>
            <div className="grid grid-cols-5 gap-2">
              {[
                ["Trades",  stats.total_trades ?? 0, "text-zinc-200"],
                ["Abertos", stats.open ?? 0,          "text-blue-400"],
                ["Wins",    stats.wins ?? 0,           "text-emerald-400"],
                ["Losses",  stats.losses ?? 0,         "text-red-400"],
                ["PnL",     fmtUSD(stats.pnl_usd ?? 0), pnlColor(stats.pnl_usd)],
              ].map(([label, value, cls]) => (
                <div key={label} className="border border-zinc-800/40 bg-zinc-900/40 px-2 py-1.5 flex flex-col gap-0.5">
                  <span className="text-[9px] text-zinc-600">{label}</span>
                  <span className={`text-sm font-mono font-bold ${cls}`}>{value}</span>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-3 gap-2">
              {[
                ["PnL Diário",     fmtUSD(stats.daily_pnl_usd ?? 0), pnlColor(stats.daily_pnl_usd)],
                ["Consec. Losses", stats.consecutive_losses ?? 0,    stats.consecutive_losses >= 2 ? "text-orange-400" : "text-zinc-400"],
                ["Trades Hoje",    stats.daily_trades ?? 0,          "text-zinc-400"],
              ].map(([label, value, cls]) => (
                <div key={label} className="border border-zinc-800/30 bg-zinc-900/20 px-2 py-1 flex flex-col gap-0.5">
                  <span className="text-[9px] text-zinc-600">{label}</span>
                  <span className={`text-xs font-mono font-bold ${cls}`}>{value}</span>
                </div>
              ))}
            </div>
          </>
        )}

        {atStatus?.last_errors?.length > 0 && (
          <div className="flex flex-col gap-1">
            {atStatus.last_errors.slice(-2).map((e, i) => (
              <div key={i} className="flex items-center gap-1 text-[10px] text-red-400/70 font-mono"><Warning size={10} />{e}</div>
            ))}
          </div>
        )}
      </div>

      {/* ── LINHA 1: EXECUÇÃO | CONTA & RISCO ── */}
      <div className="grid grid-cols-2 gap-4">

        {/* Card: Execução */}
        <div className="border border-zinc-800/40 bg-zinc-900/20 p-4 flex flex-col gap-3">
          <SectionHeader icon={<Gear size={13} className="text-zinc-500" />} label="Execução" />

          {/* Status mercado inline */}
          {tradingStatus && (() => {
            const ts = tradingStatus;
            // O estado real do exchange (CME) — independente das flags de auto-trading
            const cmeGlobex   = ts.session?.cme_session === 'globex';
            const isGlobexActive = ts.status === 'OPEN' && ts.session_type === 'globex';
            const isNyseOpen     = ts.status === 'OPEN' && !isGlobexActive;
            const isBlackout     = ts.status === 'NEWS_BLACKOUT';
            // Globex fisicamente aberto mas auto-trading não activo neste período
            const globexNoAuto   = cmeGlobex && !isGlobexActive && !isBlackout;

            const borderCls = isGlobexActive || globexNoAuto ? 'border-cyan-500/20 bg-cyan-500/5'
              : isNyseOpen   ? 'border-emerald-500/30 bg-emerald-500/5'
              : isBlackout   ? 'border-amber-500/30 bg-amber-500/5'
              : 'border-zinc-800/60 bg-zinc-900/40';
            const iconCls   = isGlobexActive ? 'text-cyan-400'
              : globexNoAuto ? 'text-cyan-400/50'
              : isNyseOpen   ? 'text-emerald-400'
              : isBlackout   ? 'text-amber-400'
              : 'text-zinc-500';
            const badgeCls  = isGlobexActive ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30'
              : globexNoAuto ? 'bg-cyan-500/10 text-cyan-400/50 border-cyan-500/20'
              : isNyseOpen   ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
              : isBlackout   ? 'bg-amber-500/20 text-amber-400 border-amber-500/30 animate-pulse'
              : 'bg-zinc-800/60 text-zinc-500 border-zinc-700/40';
            const label     = isGlobexActive || globexNoAuto ? 'GLOBEX'
              : isNyseOpen   ? 'ABERTO'
              : isBlackout   ? 'BLACKOUT'
              : 'FECHADO';
            const autoOff    = globexNoAuto && !ts.globex_auto?.enabled;
            const sessionLabel = globexNoAuto
              ? `globex${autoOff ? ' (auto off)' : ' (fora janela)'}`
              : ts.session_type || '—';

            return (
              <div className={`flex items-center justify-between px-3 py-2 border ${borderCls}`}>
                <div className="flex items-center gap-2">
                  <Clock size={12} className={iconCls} />
                  <span className="text-[10px] text-zinc-500 font-mono">{sessionLabel}</span>
                </div>
                <span className={`text-[10px] font-bold px-2 py-0.5 border ${badgeCls}`}>{label}</span>
              </div>
            );
          })()}

          {/* Paper / LIVE */}
          <div className="flex items-center justify-between">
            <div className="flex flex-col">
              <span className="text-xs text-zinc-300">Modo de Execução</span>
              <span className="text-[10px] text-zinc-600">{local.paper_trading ? "Paper — simulação sem ordens reais" : "LIVE — ordens reais via Tradovate"}</span>
            </div>
            <div className="flex border border-zinc-700/60 overflow-hidden">
              <button onClick={() => { set("paper_trading", true); onPaperToggle(true); }} disabled={paperSaving}
                className={`px-3 py-1.5 text-xs font-bold transition-all disabled:opacity-60 ${local.paper_trading ? "bg-yellow-500/20 text-yellow-400" : "text-zinc-500 hover:text-zinc-300"}`}>
                {paperSaving && local.paper_trading ? "..." : "Paper"}
              </button>
              <button onClick={() => { set("paper_trading", false); onPaperToggle(false); }} disabled={paperSaving}
                className={`px-3 py-1.5 text-xs font-bold transition-all border-l border-zinc-700/60 disabled:opacity-60 ${!local.paper_trading ? "bg-red-500/20 text-red-400" : "text-zinc-500 hover:text-zinc-300"}`}>
                {paperSaving && !local.paper_trading ? "..." : "LIVE"}
              </button>
            </div>
          </div>

          {!local.paper_trading && (
            <div className="flex flex-col gap-1">
              <span className="text-[10px] text-zinc-500">Webhook URL (SignalStack)</span>
              <input type="text" value={local.webhook_url || ""} onChange={e => set("webhook_url", e.target.value)}
                placeholder="https://app.signalstack.com/hook/..."
                className="bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono px-3 py-1.5 w-full" />
            </div>
          )}

          {/* Qualidade mínima — tabela 2×2 */}
          <div className="flex flex-col gap-1.5">
            <span className="text-[10px] text-zinc-500">Qualidade mínima (G-2)</span>
            {(() => {
              const OPTS = [
                { k: "STRONG",   cls: "text-emerald-400", bg: "bg-emerald-500/20" },
                { k: "MODERATE", cls: "text-yellow-400",  bg: "bg-yellow-500/20"  },
              ];
              const QBtn = ({ field, val }) => (
                <div className="flex border border-zinc-700/60 overflow-hidden w-full">
                  {OPTS.map(o => (
                    <button key={o.k} onClick={() => set(field, o.k)}
                      className={`flex-1 py-1 text-[10px] font-bold transition-all border-l border-zinc-700/60 first:border-l-0 ${
                        val === o.k ? `${o.bg} ${o.cls}` : "text-zinc-500 hover:text-zinc-300"
                      }`}>{o.k}</button>
                  ))}
                </div>
              );
              return (
                <div className="grid grid-cols-[40px_1fr_1fr] gap-x-1 gap-y-1 items-center">
                  {/* cabeçalho */}
                  <div />
                  <span className="text-[9px] text-zinc-500 text-center">MNQ</span>
                  <span className="text-[9px] text-zinc-500 text-center">MES</span>
                  {/* RTH — campo independente por símbolo */}
                  <span className="text-[9px] text-zinc-400 font-bold">RTH</span>
                  <QBtn field="min_quality_rth_mnq" val={local.min_quality_rth_mnq || "STRONG"} />
                  <QBtn field="min_quality_rth_mes" val={local.min_quality_rth_mes || "STRONG"} />
                  {/* OVNT — campo independente por símbolo */}
                  <span className="text-[9px] text-zinc-400 font-bold">OVNT</span>
                  <QBtn field="min_quality_overnight_mnq" val={local.min_quality_overnight_mnq || "MODERATE"} />
                  <QBtn field="min_quality_overnight_mes" val={local.min_quality_overnight_mes || "STRONG"} />
                </div>
              );
            })()}
          </div>

          {/* Sinais ativos */}
          {recentSignals.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[10px] text-zinc-500 uppercase tracking-wider">Sinais Activos</span>
              {recentSignals.map(s => (
                <div key={s.sym} className="flex items-center justify-between bg-emerald-500/5 border border-emerald-500/20 px-3 py-1.5 text-[10px]">
                  <span className="font-mono font-bold text-emerald-400">{s.sym}</span>
                  <span className={`font-mono font-bold ${s.s3_action === "buy" ? "text-emerald-400" : "text-red-400"}`}>{s.s3_action?.toUpperCase()}</span>
                  {qualityBadge(s.s2_quality)}
                  <span className="text-zinc-500">{s.mode}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Card: Conta & Risco */}
        <div className="border border-zinc-800/40 bg-zinc-900/20 p-4 flex flex-col gap-3">
          <SectionHeader icon={<CurrencyDollar size={13} className="text-zinc-500" />} label="Conta & Risco" />

          {/* Nota informativa — contexto muda conforme sizing_mode */}
          {local.sizing_mode === "risk_pct" ? (
            <div className="bg-violet-500/10 border border-violet-500/30 px-3 py-2 text-[9px] text-violet-300 leading-relaxed">
              Modo <span className="font-semibold">RISK %</span> activo — <span className="font-semibold">Conta</span> e <span className="font-semibold">Risco %</span> controlam o sizing real. A cada sinal, o engine calcula <span className="font-mono">qty = risk$ ÷ (stop_pts × point_value)</span> e aplica o cap de contratos definido em Símbolos & Loop.
            </div>
          ) : (
            <div className="bg-zinc-800/30 border border-zinc-700/30 px-3 py-2 text-[9px] text-zinc-500 leading-relaxed">
              Modo <span className="text-zinc-400 font-semibold">FIXO</span> activo — <span className="text-zinc-400 font-semibold">Conta</span> e <span className="text-zinc-400 font-semibold">Risco %</span> são referência visual. O sizing real é controlado pelos contratos / trade em Símbolos & Loop.
            </div>
          )}

          <div>
            <label className={`text-[10px] mb-0.5 block ${local.sizing_mode === "risk_pct" ? "text-violet-300" : "text-zinc-500"}`}>
              Tamanho da Conta (USD)
              {local.sizing_mode !== "risk_pct" && <span className="text-zinc-600 font-normal ml-1">(referência)</span>}
            </label>
            <div className="text-[9px] text-zinc-600 mb-1">
              {local.sizing_mode === "risk_pct" ? "Usado no cálculo do risco $ por trade." : "Referência para calcular exposição. Não afecta o engine."}
            </div>
            <input type="number" step="1000" min="1000" value={local.account_size || 50000}
              onChange={e => set("account_size", parseFloat(e.target.value))}
              className={`w-full px-2 py-1.5 bg-zinc-800/60 text-xs font-mono ${local.sizing_mode === "risk_pct" ? "border border-violet-500/40 text-violet-200" : "border border-zinc-700/40 text-zinc-200"}`} />
          </div>
          <div>
            <label className={`text-[10px] mb-0.5 block ${local.sizing_mode === "risk_pct" ? "text-violet-300" : "text-zinc-500"}`}>
              Risco / Trade %
              {local.sizing_mode !== "risk_pct" && <span className="text-zinc-600 font-normal ml-1">(referência)</span>}
            </label>
            <div className="text-[9px] text-zinc-600 mb-1">
              {local.sizing_mode === "risk_pct" ? "Define risk$ = Conta × Risco%. Usado para calcular qty dinamicamente." : "Só informativo — não sobrepõe contratos / trade configurados abaixo."}
            </div>
            <input type="number" step="0.25" min="0.25" max="5" value={local.risk_per_trade_pct || 1.0}
              onChange={e => set("risk_per_trade_pct", parseFloat(e.target.value))}
              className={`w-full px-2 py-1.5 bg-zinc-800/60 text-xs font-mono ${local.sizing_mode === "risk_pct" ? "border border-violet-500/40 text-violet-200" : "border border-zinc-700/40 text-zinc-200"}`} />
            <div className="text-[9px] text-emerald-400/80 mt-0.5 font-mono">≈ ${riskUSD.toLocaleString(undefined, {maximumFractionDigits:0})} / trade com o lote configurado</div>
          </div>
          {/* SL/TP real — por modo */}
          <div className="border-t border-zinc-800/40 pt-3 flex flex-col gap-2">
            <div className="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">
              SL / TP — Parâmetros Reais
              <span className={`ml-2 px-1.5 py-px rounded text-[9px] font-bold
                ${mode === "ZONES" ? "bg-amber-500/20 text-amber-400"
                : "bg-blue-500/20 text-blue-400"}`}>
                {mode || "FLOW"}
              </span>
            </div>

            {(mode === "ZONES" || !mode) && (
              <div className="flex flex-col gap-1.5">
                <div className="flex flex-col gap-1 bg-zinc-800/40 rounded px-2.5 py-2">
                  <div className="text-[9px] text-zinc-400 font-bold mb-0.5">STOP LOSS — por tipo de zona</div>
                  {[
                    ["Fade (VWAP, ONH/ONL, IB, POC)",   "0.7 × ATR", "text-amber-300"],
                    ["Pullback / Break",                  "0.8 × ATR", "text-amber-400/70"],
                  ].map(([label, val, cls]) => (
                    <div key={label} className="flex justify-between items-center">
                      <span className="text-[9px] text-zinc-500">{label}</span>
                      <span className={`text-[9px] font-mono font-bold ${cls}`}>{val}</span>
                    </div>
                  ))}
                </div>
                <div className="flex flex-col gap-1 bg-zinc-800/40 rounded px-2.5 py-2">
                  <div className="text-[9px] text-zinc-400 font-bold mb-0.5">TAKE PROFIT — target estrutural</div>
                  <div className="text-[9px] text-zinc-500 leading-relaxed">
                    Usa o <span className="text-zinc-300 font-bold">target estrutural da zona</span> (ex: VWAP Fade → POC, ONH Fade → D1 POC ou VWAP) quando dentro de range válido.
                  </div>
                  <div className="text-[9px] text-zinc-600 mt-0.5">
                    Fallback: <span className="font-mono text-zinc-400">1.5 × ATR</span> se target fora de range ou ausente.
                  </div>
                </div>
              </div>
            )}

            {mode === "FLOW" && (
              <div className="flex flex-col gap-1 bg-zinc-800/40 rounded px-2.5 py-2">
                <div className="text-[9px] text-zinc-400 font-bold mb-0.5">TICKS FIXOS — sem ATR</div>
                {[["MNQ", "SL 6t | TP 10t | BE 4t"], ["MES", "SL 4t | TP 8t | BE 3t"]].map(([sym, v]) => (
                  <div key={sym} className="flex justify-between items-center">
                    <span className="text-[9px] text-blue-400 font-bold font-mono">{sym}</span>
                    <span className="text-[9px] font-mono text-zinc-300">{v}</span>
                  </div>
                ))}
              </div>
            )}


            <div className="text-[9px] text-zinc-600 italic">
              Estes valores são calculados automaticamente pelo motor — não são configuráveis aqui.
            </div>
          </div>
        </div>
      </div>

      {/* ── LINHA 2: SÍMBOLOS & LOOP | PROTECÇÕES + NEWS BLACKOUT ── */}
      <div className="grid grid-cols-2 gap-4">

        {/* Card: Símbolos & Loop */}
        <div className="border border-zinc-800/40 bg-zinc-900/20 p-4 flex flex-col gap-3">
          <SectionHeader icon={<Gauge size={13} className="text-zinc-500" />} label="Símbolos & Loop" />
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-zinc-500 w-24 shrink-0">Símbolos ativos</span>
            {["MNQ", "MES"].map(sym => {
              const active = (local.symbols_enabled || ["MNQ","MES"]).includes(sym);
              return (
                <button key={sym} onClick={() => toggleSymbol(sym)}
                  className={`px-3 py-1 text-xs font-bold border transition-all ${active ? "bg-blue-500/20 text-blue-400 border-blue-500/40" : "text-zinc-500 border-zinc-700/40 hover:text-zinc-300"}`}>
                  {sym}
                </button>
              );
            })}
          </div>
          {/* Execução por Modo — toggle único contextual */}
          {(() => {
            const modeKey   = mode === "FLOW" ? "auto_trade_flow" : "auto_trade_zones";
            const modeLabel = mode === "FLOW" ? "FLOW" : "ZONES";
            const modeCls   = mode === "FLOW"
              ? { text: "text-blue-400",  badge: "bg-blue-500/10 border-blue-500/20" }
              : { text: "text-amber-400", badge: "bg-amber-500/10 border-amber-500/20" };
            const isOn = !!local[modeKey];
            return (
              <div className="flex flex-col gap-1.5 pt-1 border-t border-zinc-800/40">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold font-mono px-1.5 py-0.5 border ${modeCls.badge} ${modeCls.text}`}>{modeLabel}</span>
                    <span className="text-[10px] text-zinc-400 font-bold">Auto Trade</span>
                    <span className="text-[9px] text-zinc-600">execução automática neste modo</span>
                  </div>
                  <Toggle on={isOn} onChange={v => set(modeKey, v)} />
                </div>
                {!isOn && (
                  <div className="text-[9px] text-amber-400/70 bg-amber-500/5 border border-amber-500/20 px-2 py-1">
                    Auto Trade desligado para o modo {modeLabel} — sinais não serão executados.
                  </div>
                )}
              </div>
            );
          })()}

          {/* Track B toggle — só visível no modo ZONES */}
          {(mode === "ZONES" || !mode) && (
            <div className="flex flex-col gap-1.5 pt-1 border-t border-zinc-800/40">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-bold font-mono px-1.5 py-0.5 border bg-teal-500/10 border-teal-500/20 text-teal-400">B</span>
                  <span className="text-[10px] text-zinc-400 font-bold">Track B — Pullback Return</span>
                  <span className="text-[9px] text-zinc-600">entrada no retorno pós-pullback</span>
                </div>
                <Toggle on={!!local.track_b_enabled} onChange={v => set("track_b_enabled", v)} />
              </div>
              {local.track_b_enabled ? (
                <div className="text-[9px] text-teal-400/80 bg-teal-500/5 border border-teal-500/20 px-2 py-1">
                  Track B activo — executa paper trade quando preço confirma retorno pós-pullback.
                  {!local.paper_trading && (
                    <span className="text-amber-400 font-bold ml-1">⚠ Requer readiness N≥10 em modo LIVE.</span>
                  )}
                </div>
              ) : (
                <div className="text-[9px] text-zinc-600">
                  Desligado — oportunidades de pullback apenas observadas (log).
                </div>
              )}
            </div>
          )}

          {/* Toggle: Modo de Sizing */}
          <div className="flex flex-col gap-1.5">
            <span className="text-[10px] text-zinc-500">Modo de Sizing</span>
            <div className="flex border border-zinc-700/60 overflow-hidden">
              <button onClick={() => set("sizing_mode", "fixed")}
                className={`flex-1 py-1.5 text-[10px] font-bold transition-all ${local.sizing_mode !== "risk_pct" ? "bg-blue-500/20 text-blue-400" : "text-zinc-500 hover:text-zinc-300"}`}>
                FIXO
              </button>
              <button onClick={() => set("sizing_mode", "risk_pct")}
                className={`flex-1 py-1.5 text-[10px] font-bold transition-all border-l border-zinc-700/60 ${local.sizing_mode === "risk_pct" ? "bg-violet-500/20 text-violet-400" : "text-zinc-500 hover:text-zinc-300"}`}>
                RISK %
              </button>
            </div>
            {local.sizing_mode !== "risk_pct" ? (
              <div className="text-[9px] text-zinc-600">Envia sempre o número fixo de contratos configurado abaixo.</div>
            ) : (
              <div className="text-[9px] text-violet-400/70">Calculado dinamicamente: <span className="font-mono">risk$ ÷ (stop_pts × point_value)</span> — onde <span className="font-mono">stop_pts</span> é definido pelo motor com base no tipo de zona e modo activo.</div>
            )}
          </div>

          <div className="flex flex-col gap-2">
            {/* Contratos fixos — só no modo FIXO */}
            {local.sizing_mode !== "risk_pct" && (
              <>
                {[
                  ["Contratos MNQ / trade", "Lote enviado por sinal em MNQ", "mnq_quantity", 1, 10, 1],
                  ["Contratos MES / trade", "Lote enviado por sinal em MES", "mes_quantity", 1, 10, 1],
                ].map(([label, desc, key, min, max, step]) => (
                  <div key={key} className="flex items-center justify-between gap-2">
                    <div className="flex flex-col">
                      <span className="text-[10px] text-zinc-400">{label}</span>
                      <span className="text-[9px] text-zinc-600">{desc}</span>
                    </div>
                    <input type="number" step={step} value={local[key] ?? min}
                      onChange={e => set(key, parseInt(e.target.value) || min)}
                      min={min} max={max}
                      className="w-20 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono px-2 py-1 text-right shrink-0" />
                  </div>
                ))}
              </>
            )}

            {/* Cap de segurança — só no modo RISK % */}
            {local.sizing_mode === "risk_pct" && (
              <div className="flex items-center justify-between gap-2 bg-violet-500/5 border border-violet-500/20 px-3 py-2">
                <div className="flex flex-col">
                  <span className="text-[10px] text-violet-300">Máx contratos / trade (cap)</span>
                  <span className="text-[9px] text-zinc-600">Tecto de segurança — impede lotes excessivos se stop for pequeno</span>
                </div>
                <input type="number" step={1} min={1} max={20} value={local.max_qty_risk_pct ?? 5}
                  onChange={e => set("max_qty_risk_pct", parseInt(e.target.value) || 1)}
                  className="w-20 bg-zinc-800/60 border border-violet-500/30 text-violet-300 text-xs font-mono px-2 py-1 text-right shrink-0" />
              </div>
            )}

            {/* Parâmetros de loop — sempre visíveis */}
            {[
              ["Cooldown entre trades",  "Pausa obrigatória (seg) após fechar uma posição",        "cooldown_sec",      10, 600, 10],
              ["Máx trades abertos",     "Total de posições abertas simultâneas (MNQ + MES)",      "max_positions",     1,  10,  1 ],
              ["Máx trades / símbolo",   "Limite de posições abertas por símbolo individualmente", "max_per_symbol",    1,  5,   1 ],
              ["Intervalo do loop",      "Frequência (seg) com que o engine verifica novos sinais","auto_interval_sec", 2,  30,  1 ],
            ].map(([label, desc, key, min, max, step]) => (
              <div key={key} className="flex items-center justify-between gap-2">
                <div className="flex flex-col">
                  <span className="text-[10px] text-zinc-400">{label}</span>
                  <span className="text-[9px] text-zinc-600">{desc}</span>
                </div>
                <input type="number" step={step} value={local[key] ?? min}
                  onChange={e => set(key, parseInt(e.target.value) || min)}
                  min={min} max={max}
                  className="w-20 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono px-2 py-1 text-right shrink-0" />
              </div>
            ))}

            {/* G-5 */}
            <div className="flex items-center justify-between gap-2 pt-1 border-t border-zinc-800/40">
              <div className="flex flex-col">
                <span className="text-[10px] text-zinc-400">Máx contratos total (G-5)</span>
                <span className="text-[9px] text-zinc-600">Tecto global MNQ + MES combinados — 0 = sem limite</span>
              </div>
              <input type="number" step={1} min={0} max={20} value={local.max_total_contracts ?? 0}
                onChange={e => set("max_total_contracts", parseInt(e.target.value) || 0)}
                className="w-20 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono px-2 py-1 text-right shrink-0" />
            </div>
          </div>
        </div>

        {/* Card: Protecções + News Blackout */}
        <div className="border border-zinc-800/40 bg-zinc-900/20 p-4 flex flex-col gap-3">
          <SectionHeader icon={<Prohibit size={13} className="text-zinc-500" />} label="Protecções (G-3 / G-4)" />
          <div className="flex flex-col gap-2">
            {[
              ["Perda diária máx ($)",   "max_daily_loss_usd",     50,  50000, 50],
              ["Perdas consecutivas",    "max_consecutive_losses",  1,   9999,  1],
              ["Trades máx / dia",       "max_daily_trades",        1,   9999,  1],
              ["Cooldown pós-loss (seg)","cooldown_sec",            10,  3600, 10],
            ].map(([label, key, min, max, step]) => (
              <div key={key} className="flex items-center justify-between gap-2">
                <span className="text-[10px] text-zinc-500">{label}</span>
                <input type="number" step={step} min={min} max={max} value={local[key] ?? ""}
                  onChange={e => set(key, parseFloat(e.target.value) || min)}
                  className="w-20 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono px-2 py-1 text-right" />
              </div>
            ))}
          </div>

          <div className="border-t border-zinc-800/40 pt-3 flex flex-col gap-2">
            <SectionHeader icon={<Newspaper size={13} className="text-zinc-500" />} label="News Blackout" />
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-zinc-500">Suspende trading em torno de notícias</span>
              <Toggle on={!!local.news_blackout_enabled} onChange={v => set("news_blackout_enabled", v)} />
            </div>
            {local.news_blackout_enabled && (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] text-zinc-500 mb-1 block">Antes (min)</label>
                  <input type="number" min="5" max="60" value={local.news_blackout_minutes_before || 15}
                    onChange={e => { const v = parseInt(e.target.value); set("news_blackout_minutes_before", isNaN(v) ? 15 : v); }}
                    className="w-full px-2 py-1.5 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono" />
                </div>
                <div>
                  <label className="text-[10px] text-zinc-500 mb-1 block">Depois (min)</label>
                  <input type="number" min="5" max="60" value={local.news_blackout_minutes_after || 15}
                    onChange={e => { const v = parseInt(e.target.value); set("news_blackout_minutes_after", isNaN(v) ? 15 : v); }}
                    className="w-full px-2 py-1.5 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono" />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── LINHA 3: ZONE TYPES | HORÁRIO DE TRADING ── */}
      <div className="grid grid-cols-2 gap-4">

        {/* Card: Zone Types */}
        <div className="border border-zinc-800/40 bg-zinc-900/20 p-4 flex flex-col gap-3">
          <SectionHeader icon={<ChartLineUp size={13} className="text-zinc-500" />} label="Zone Types Activos" />
          <div className="grid grid-cols-2 gap-1.5">
            {[
              { key: "SIGMA2_FADE_BUY",    label: "SIGMA2 FADE ↑", wr: "60", pf: 8.9  },
              { key: "SIGMA2_FADE_SELL",   label: "SIGMA2 FADE ↓", wr: "50", pf: 6.0  },
              { key: "SIGMA1_PULLBACK_BUY",label: "SIGMA1 PB ↑",   wr: "33", pf: 2.9  },
              { key: "SESSION_VAL_FADE",   label: "SESSION VAL",   wr: "43", pf: 2.1  },
              { key: "SESSION_VAH_FADE",   label: "SESSION VAH",   wr: "33", pf: 0.5  },
              { key: "SESSION_POC_BUY",    label: "SESSION POC ↑", wr: "0",  pf: 0.0  },
              { key: "VWAP_PULLBACK_BUY",  label: "VWAP PB ↑",    wr: "17", pf: 0.3  },
              { key: "EMA_PULLBACK_BUY",   label: "EMA PB ↑",      wr: null, pf: null },
              { key: "EMA_PULLBACK_SELL",  label: "EMA PB ↓",      wr: null, pf: null },
            ].map(({ key, label, wr, pf }) => {
              const disabled  = (local.disabled_zone_types || []).includes(key);
              const noData    = pf === null;
              const toggle = () => {
                const cur = local.disabled_zone_types || [];
                set("disabled_zone_types", disabled ? cur.filter(k => k !== key) : [...cur, key]);
              };
              const dot    = noData ? "bg-zinc-500" : pf >= 3 ? "bg-emerald-400" : pf >= 1 ? "bg-blue-400" : "bg-red-400/70";
              const pfTxt  = noData ? "text-zinc-600" : pf >= 3 ? "text-emerald-400" : pf >= 1 ? "text-zinc-400" : "text-red-400";
              return (
                <div key={key} onClick={toggle}
                  className={`flex items-center justify-between px-2.5 py-2 border cursor-pointer transition-all select-none ${
                    disabled
                      ? "border-zinc-800/30 bg-zinc-900/10 opacity-35"
                      : "border-zinc-700/40 bg-zinc-800/10 hover:bg-zinc-800/30"
                  }`}>
                  <div>
                    <div className={`text-[10px] font-bold ${disabled ? "text-zinc-600" : "text-zinc-200"}`}>{label}</div>
                    <div className="text-[9px] text-zinc-600">
                      {noData
                        ? <span className="text-zinc-700">sem dados</span>
                        : <>WR {wr}% · PF <span className={pfTxt}>{pf.toFixed(1)}</span></>
                      }
                    </div>
                  </div>
                  <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${disabled ? "bg-zinc-700" : dot}`} />
                </div>
              );
            })}
          </div>
          {(local.disabled_zone_types || []).length > 0 && (
            <div className="text-[9px] text-amber-400/80 border border-amber-500/20 bg-amber-500/5 px-2 py-1.5">
              {(local.disabled_zone_types || []).length} zone type{(local.disabled_zone_types || []).length > 1 ? "s" : ""} desactivado{(local.disabled_zone_types || []).length > 1 ? "s" : ""}
            </div>
          )}
        </div>

        {/* Card: Horário de Trading */}
        <div className="border border-zinc-800/40 bg-zinc-900/20 p-4 flex flex-col gap-3">
          <SectionHeader icon={<Clock size={13} className="text-zinc-500" />} label="Horário de Trading" />
          <div className="flex items-center gap-2">
            <button onClick={() => set("auto_hours_mode", true)}
              className={`px-2.5 py-1 text-[9px] font-semibold border transition-colors ${local.auto_hours_mode !== false ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' : 'border-zinc-700 text-zinc-500'}`}>
              NYSE AUTO
            </button>
            <button onClick={() => set("globex_auto_enabled", !local.globex_auto_enabled)}
              className={`px-2.5 py-1 text-[9px] font-semibold border transition-colors ${local.globex_auto_enabled ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-400' : 'border-zinc-700 text-zinc-500'}`}>
              GLOBEX AUTO
            </button>
            <button onClick={() => set("auto_hours_mode", false)}
              className={`px-2.5 py-1 text-[9px] font-semibold border transition-colors ${local.auto_hours_mode === false ? 'bg-blue-500/10 border-blue-500/30 text-blue-400' : 'border-zinc-700 text-zinc-500'}`}>
              MANUAL
            </button>
          </div>

          {local.auto_hours_mode !== false ? (
            <div className="flex flex-col gap-2">
              <div className="bg-emerald-500/5 border border-emerald-500/20 px-3 py-2 text-[9px]">
                <span className="text-emerald-400 font-bold">NYSE AUTO</span>
                <span className="text-zinc-500 ml-1">DST-aware — Pre-market 4:00 AM ET → 30min antes do close</span>
              </div>
              {local.globex_auto_enabled && (
                <div className="bg-cyan-500/5 border border-cyan-500/20 px-3 py-2 text-[9px]">
                  <span className="text-cyan-400 font-bold">GLOBEX AUTO</span>
                  <span className="text-zinc-500 ml-1">18:00 ET → 09:25 ET. Halt 17-18h. Lote reduzido 50%.</span>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3 mt-1">
                <div>
                  <label className="text-[10px] text-zinc-500 mb-1 block">Flatten antes do close (min)</label>
                  <input type="number" min="5" max="60" value={local.pre_close_flatten_minutes || 30}
                    onChange={e => { const v = parseInt(e.target.value); set("pre_close_flatten_minutes", isNaN(v) ? 30 : v); }}
                    className="w-full px-2 py-1.5 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono" />
                </div>
                <div className="flex items-center justify-between px-3 py-2 border border-zinc-800/40 cursor-pointer hover:bg-zinc-800/20 transition-colors"
                  onClick={() => set("eod_flatten_enabled", !local.eod_flatten_enabled)}>
                  <div>
                    <div className="text-[10px] font-semibold text-zinc-300">EOD Flatten</div>
                    <div className="text-[9px] text-zinc-600">Fecha posições no fim da sessão</div>
                  </div>
                  <Toggle on={!!local.eod_flatten_enabled} onChange={v => set("eod_flatten_enabled", v)} />
                </div>
              </div>
              {local.globex_auto_enabled && (
                <div>
                  <label className="text-[10px] text-zinc-500 mb-1 block">Flatten Globex antes NY open (min)</label>
                  <input type="number" min="1" max="30" value={local.globex_flatten_before_ny_minutes || 5}
                    onChange={e => { const v = parseInt(e.target.value); set("globex_flatten_before_ny_minutes", isNaN(v) ? 5 : v); }}
                    className="w-full px-2 py-1.5 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono" />
                </div>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              {[
                ["Início (ET)",      "trading_start_hour",    0, 23],
                ["Fim (ET)",         "trading_end_hour",      0, 23],
                ["Skip Open (min)",  "avoid_first_minutes",   0, 60],
                ["Skip Close (min)", "avoid_last_minutes",    0, 60],
              ].map(([label, key, min, max]) => (
                <div key={key}>
                  <label className="text-[10px] text-zinc-500 mb-1 block">{label}</label>
                  <input type="number" min={min} max={max} value={local[key] ?? ""}
                    onChange={e => { const v = parseInt(e.target.value); set(key, isNaN(v) ? min : v); }}
                    className="w-full px-2 py-1.5 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono" />
                </div>
              ))}
            </div>
          )}
        </div>

      </div>

      {/* ── SAVE ── */}
      <button onClick={() => onSaveConfig(local)} disabled={saving}
        className={`w-full py-2.5 text-xs font-bold border transition-all disabled:opacity-40 flex items-center justify-center gap-1.5 ${
          saveOk
            ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-400"
            : "bg-blue-500/20 border-blue-500/40 text-blue-400 hover:bg-blue-500/30"
        }`}>
        {saving
          ? <><Spinner size={12} className="animate-spin" />Salvando...</>
          : saveOk
            ? <><CheckCircle size={12} weight="fill" />Guardado!</>
            : "Salvar & Aplicar"}
      </button>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// Trade Log Table
// ══════════════════════════════════════════════════════════════════

function TradeLogTable({
  trades, tradesTotal, tradeStats, onClose, closing, onRefresh,
  journalSession, journalDateFrom, journalDateTo, journalMode, journalSymbol,
  onSessionChange, onDateFromChange, onDateToChange, onModeChange, onSymbolChange,
}) {
  const [filter, setFilter] = useState("ALL");

  const filtered = filter === "ALL"    ? trades
    : filter === "OPEN"   ? trades.filter(t => t.state === "OPEN")
    : filter === "CLOSED" ? trades.filter(t => t.state === "CLOSED")
    : filter === "PAPER"  ? trades.filter(t => t.paper)
    : filter === "LIVE"   ? trades.filter(t => !t.paper)
    : filter === "AUTO"   ? trades.filter(t => t.source === "auto")
    : trades.filter(t => t.source === "manual");

  const hasServerFilter = journalSession || journalDateFrom || journalDateTo || journalMode || journalSymbol;

  // Calcula stats dinâmicas a partir dos trades já filtrados pelo servidor
  const stats = useMemo(() => {
    const closed = trades.filter(t => t.state === "CLOSED");
    const wins   = closed.filter(t => (t.pnl_pts ?? 0) > 0);
    const total_pnl_usd = closed.reduce((s, t) => s + (t.pnl_usd ?? 0), 0);
    const total_pnl_pts = closed.reduce((s, t) => s + (t.pnl_pts ?? 0), 0);
    const win_rate = closed.length > 0 ? Math.round(wins.length / closed.length * 100) : null;
    const avg_pnl_usd = closed.length > 0 ? +(total_pnl_usd / closed.length).toFixed(2) : null;
    const avg_pnl_pts = closed.length > 0 ? +(total_pnl_pts / closed.length).toFixed(2) : null;

    const by_symbol = {};
    const by_mode   = {};
    for (const t of closed) {
      const sym = t.symbol || "?";
      if (!by_symbol[sym]) by_symbol[sym] = { total: 0, wins: 0, pnl_usd: 0 };
      by_symbol[sym].total++;
      by_symbol[sym].pnl_usd += t.pnl_usd ?? 0;
      if ((t.pnl_pts ?? 0) > 0) by_symbol[sym].wins++;

      const m = t.mode || "FLOW";
      if (!by_mode[m]) by_mode[m] = { total: 0, pnl_usd: 0 };
      by_mode[m].total++;
      by_mode[m].pnl_usd += t.pnl_usd ?? 0;
    }

    return {
      total_trades: trades.length,
      closed_trades: closed.length,
      open_trades: trades.filter(t => t.state === "OPEN").length,
      wins: wins.length,
      win_rate,
      total_pnl_usd, total_pnl_pts,
      avg_pnl_usd, avg_pnl_pts,
      by_symbol, by_mode,
    };
  }, [trades]);

  return (
    <div className="flex flex-col gap-3">
      {/* Stats — calculadas dos trades já filtrados */}
      {trades.length >= 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {[
            { label: "Total Trades",    value: stats.total_trades ?? 0, icon: <ChartBar size={13} />, cls: "text-zinc-200" },
            { label: "Win Rate",        value: stats.win_rate != null ? `${stats.win_rate}%` : "—", icon: <Trophy size={13} />, cls: (stats.win_rate ?? 0) >= 50 ? "text-emerald-400" : "text-red-400" },
            { label: "PnL Total (USD)", value: fmtUSD(stats.total_pnl_usd), icon: <CurrencyDollar size={13} />, cls: pnlColor(stats.total_pnl_usd) },
            { label: "PnL Médio (USD)", value: fmtUSD(stats.avg_pnl_usd),   icon: <TrendUp size={13} />,       cls: pnlColor(stats.avg_pnl_usd) },
          ].map(({ label, value, icon, cls }) => (
            <div key={label} className="border border-zinc-800/40 bg-zinc-900/30 px-3 py-2 flex flex-col gap-0.5">
              <div className="flex items-center gap-1 text-[10px] text-zinc-500">{icon}{label}</div>
              <span className={`text-sm font-mono font-bold ${cls}`}>{value}</span>
            </div>
          ))}
        </div>
      )}

      {/* Stats por modo/símbolo — derivadas dos trades filtrados */}
      {(Object.keys(stats.by_symbol).length > 0 || Object.keys(stats.by_mode).length > 0) && (
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(stats.by_symbol).map(([sym, d]) => (
            <div key={sym} className="border border-zinc-800/40 bg-zinc-900/20 px-3 py-2 flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-zinc-300">{sym}</span>
              <div className="flex items-center gap-3 text-[10px] font-mono">
                <span className="text-zinc-500">{d.total} trades</span>
                <span className={pnlColor(d.pnl_usd)}>{fmtUSD(d.pnl_usd)}</span>
                {d.total > 0 && <span className={d.wins/d.total >= 0.5 ? "text-emerald-400" : "text-red-400"}>{(d.wins/d.total*100).toFixed(0)}% wins</span>}
              </div>
            </div>
          ))}
          {Object.entries(stats.by_mode).map(([m, d]) => (
            <div key={m} className="border border-zinc-800/40 bg-zinc-900/20 px-3 py-2 flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-blue-400">{m}</span>
              <div className="flex items-center gap-3 text-[10px] font-mono">
                <span className="text-zinc-500">{d.total} trades</span>
                <span className={pnlColor(d.pnl_usd)}>{fmtUSD(d.pnl_usd)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Filtros — sessão + modo + símbolo + data (server-side) */}
      <div className="flex flex-col gap-1.5">
        {/* Linha 1: Sessão + Datas */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] text-zinc-600 font-bold uppercase tracking-wider">Sessão</span>
          {[["ALL","—"], ["OVERNIGHT","Overnight"], ["RTH_ALL","RTH"], ["RTH_OPEN","RTH Open"], ["RTH_MID","RTH Mid"], ["RTH_CLOSE","RTH Close"]].map(([val, label]) => {
            const active = (journalSession || "") === (val === "ALL" ? "" : val);
            return (
              <button key={val} onClick={() => onSessionChange(val === "ALL" ? "" : val)}
                className={`px-2 py-0.5 text-[10px] font-bold border transition-all ${active ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/40" : "text-zinc-500 border-zinc-800 hover:text-zinc-300"}`}>
                {label}
              </button>
            );
          })}
          <span className="ml-3 text-[10px] text-zinc-600 font-bold uppercase tracking-wider">De</span>
          <input
            type="date"
            value={journalDateFrom || ""}
            onChange={e => onDateFromChange(e.target.value)}
            className="px-2 py-0.5 text-[10px] font-mono border border-zinc-800 bg-zinc-900 text-zinc-300 focus:outline-none focus:border-zinc-600"
          />
          <span className="text-[10px] text-zinc-600 font-bold uppercase tracking-wider">Até</span>
          <input
            type="date"
            value={journalDateTo || ""}
            onChange={e => onDateToChange(e.target.value)}
            className="px-2 py-0.5 text-[10px] font-mono border border-zinc-800 bg-zinc-900 text-zinc-300 focus:outline-none focus:border-zinc-600"
          />
          {hasServerFilter && (
            <button onClick={() => { onSessionChange(""); onDateFromChange(""); onDateToChange(""); onModeChange(""); onSymbolChange(""); }}
              className="px-2 py-0.5 text-[10px] font-bold border border-red-800/50 text-red-400 hover:border-red-600 transition-all">
              ✕ Limpar
            </button>
          )}
        </div>

        {/* Linha 2: Modo + Símbolo */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] text-zinc-600 font-bold uppercase tracking-wider">Modo</span>
          {[["","—"], ["ZONES","Zones"], ["FLOW","Flow"]].map(([val, label]) => {
            const active = (journalMode || "") === val;
            return (
              <button key={val} onClick={() => onModeChange(val)}
                className={`px-2 py-0.5 text-[10px] font-bold border transition-all ${active ? "bg-indigo-500/20 text-indigo-400 border-indigo-500/40" : "text-zinc-500 border-zinc-800 hover:text-zinc-300"}`}>
                {label}
              </button>
            );
          })}
          <span className="ml-4 text-[10px] text-zinc-600 font-bold uppercase tracking-wider">Símbolo</span>
          {[["","—"], ["MNQ","MNQ"], ["MES","MES"]].map(([val, label]) => {
            const active = (journalSymbol || "") === val;
            return (
              <button key={val} onClick={() => onSymbolChange(val)}
                className={`px-2 py-0.5 text-[10px] font-bold border transition-all ${active ? "bg-sky-500/20 text-sky-400 border-sky-500/40" : "text-zinc-500 border-zinc-800 hover:text-zinc-300"}`}>
                {label}
              </button>
            );
          })}
        </div>

        {/* Filtros locais (state/source/paper) + total + refresh */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1 flex-wrap">
            {["ALL","OPEN","CLOSED","PAPER","LIVE","AUTO","MANUAL"].map(f => (
              <button key={f} onClick={() => setFilter(f)}
                className={`px-2 py-1 text-[10px] font-bold border transition-all ${filter === f ? "bg-blue-500/20 text-blue-400 border-blue-500/40" : "text-zinc-500 border-zinc-800 hover:text-zinc-300"}`}>
                {f}
              </button>
            ))}
            <span className="ml-2 text-[10px] text-zinc-600 font-mono">
              {filtered.length}{tradesTotal > trades.length ? `/${tradesTotal}` : ""} trades
            </span>
          </div>
          <button onClick={onRefresh} className="flex items-center gap-1 px-2 py-1 text-[10px] text-zinc-400 border border-zinc-800 hover:border-zinc-600 transition-all">
            <ArrowsCounterClockwise size={11} /> Atualizar
          </button>
        </div>
      </div>

      {/* Tabela */}
      {filtered.length === 0 ? (
        <div className="border border-zinc-800/40 bg-zinc-900/20 p-6 text-center">
          <span className="text-xs text-zinc-600">Nenhum trade encontrado.</span>
        </div>
      ) : (
        <div className="border border-zinc-800/40 overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-zinc-800/40 text-zinc-500 text-[10px]">
                <th className="text-left px-3 py-2">Data/Hora</th>
                <th className="text-left px-3 py-2">Sess</th>
                <th className="text-left px-3 py-2">Sym</th>
                <th className="text-left px-3 py-2">Modo</th>
                <th className="text-left px-3 py-2">Zone</th>
                <th className="text-left px-3 py-2">Src</th>
                <th className="text-left px-3 py-2">Dir</th>
                <th className="text-right px-3 py-2">Entrada</th>
                <th className="text-right px-3 py-2">SL</th>
                <th className="text-right px-3 py-2">TP</th>
                <th className="text-right px-3 py-2">Saída</th>
                <th className="text-right px-3 py-2">PnL pts</th>
                <th className="text-right px-3 py-2">PnL USD</th>
                <th className="text-left px-3 py-2">Dur</th>
                <th className="text-left px-3 py-2">Estado</th>
                <th className="text-left px-3 py-2">Razão</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(t => (
                <tr key={t.id} className="border-b border-zinc-800/30 hover:bg-zinc-800/20 transition-colors">
                  <td className="px-3 py-2 text-zinc-500 text-[10px] whitespace-nowrap">
                    {t.created_at ? (
                      <>
                        <span className="text-zinc-600">{new Date(t.created_at).toLocaleDateString("pt-BR", {day:"2-digit",month:"2-digit"})}</span>
                        {" "}{new Date(t.created_at).toLocaleTimeString("pt-BR")}
                      </>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-2 text-[10px]">
                    {(() => {
                      const sl = t.session_label || "";
                      const abbr = sl === "OVERNIGHT" ? "OVN" : sl === "RTH_OPEN" ? "OPEN" : sl === "RTH_MID" ? "MID" : sl === "RTH_CLOSE" ? "CLOSE" : sl || "—";
                      const cls  = sl === "OVERNIGHT" ? "text-indigo-400" : sl.startsWith("RTH") ? "text-amber-400" : "text-zinc-500";
                      return <span className={`font-bold ${cls}`}>{abbr}</span>;
                    })()}
                  </td>
                  <td className="px-3 py-2 text-zinc-200 font-bold">{t.symbol}</td>
                  <td className="px-3 py-2 text-[10px]">
                    <span className="text-blue-400">{t.mode || "FLOW"}</span>
                  </td>
                  <td className="px-3 py-2 text-[10px] max-w-[100px]">
                    {t.zone_type ? (
                      <span className="text-purple-400 truncate block" title={t.zone_type}>{t.zone_type.replace(/_/g," ")}</span>
                    ) : <span className="text-zinc-700">—</span>}
                  </td>
                  <td className="px-3 py-2 text-[10px]">
                    <span className={t.source === "auto" ? "text-emerald-400" : "text-zinc-500"}>
                      {t.source === "auto" ? "🤖 auto" : "👤 manual"}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {t.action === "buy" ? <ArrowUp size={12} className="text-emerald-400" weight="bold" /> : <ArrowDown size={12} className="text-red-400" weight="bold" />}
                  </td>
                  <td className="px-3 py-2 text-right text-zinc-300">{t.entry_price?.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right text-red-400">{t.stop_loss_price?.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right text-emerald-400">{t.take_profit_price?.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right text-zinc-300">{t.exit_price?.toFixed(2) ?? "—"}</td>
                  <td className={`px-3 py-2 text-right font-bold ${pnlColor(t.pnl_pts)}`}>{fmtPts(t.pnl_pts)}</td>
                  <td className={`px-3 py-2 text-right font-bold ${pnlColor(t.pnl_usd)}`}>{fmtUSD(t.pnl_usd)}</td>
                  <td className="px-3 py-2 text-zinc-500 text-[10px]">{fmtDur(t.duration_sec)}</td>
                  <td className="px-3 py-2">
                    <span className={`text-[10px] font-bold ${t.state === "OPEN" ? "text-emerald-400" : "text-zinc-500"}`}>{t.state}</span>
                  </td>
                  <td className="px-3 py-2 text-[10px] text-zinc-500">{t.exit_reason || (t.paper ? "PAPER" : "LIVE")}</td>
                  <td className="px-3 py-2">
                    {t.state === "OPEN" && (
                      <button onClick={() => onClose(t.id)} disabled={closing === t.id}
                        className="text-[10px] text-red-400 hover:text-red-300 border border-red-500/30 px-2 py-0.5 hover:border-red-500/50 transition-all disabled:opacity-40">
                        {closing === t.id ? "..." : "Fechar"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// Config Panel (modo fluxo)
// ══════════════════════════════════════════════════════════════════

function ConfigPanel({ config, onSave, saving }) {
  const [local, setLocal] = useState(config || {});
  useEffect(() => { setLocal(config || {}); }, [config]);
  const set = (k, v) => setLocal(prev => ({ ...prev, [k]: v }));
  return (
    <div className="border border-zinc-800/40 bg-zinc-900/30 p-4 flex flex-col gap-4">
      <h3 className="text-xs font-bold text-zinc-300 uppercase tracking-wider">Config — SL/TP/BE (Modo Fluxo)</h3>
      <div className="grid grid-cols-2 gap-4">
        {[["MNQ",[["SL ticks","sl_ticks_mnq"],["TP ticks","tp_ticks_mnq"],["BE ticks","be_ticks_mnq"],["Qtd","mnq_quantity"]]],
          ["MES",[["SL ticks","sl_ticks_mes"],["TP ticks","tp_ticks_mes"],["BE ticks","be_ticks_mes"],["Qtd","mes_quantity"]]]
        ].map(([sym,fields]) => (
          <div key={sym} className="flex flex-col gap-2">
            <span className="text-[10px] text-zinc-500 font-mono uppercase">{sym}</span>
            {fields.map(([label,key]) => (
              <div key={key} className="flex items-center justify-between gap-2">
                <span className="text-[10px] text-zinc-500">{label}</span>
                <input type="number" value={local[key] ?? ""} onChange={e => { const v = parseInt(e.target.value); const minV = key.startsWith("be_") ? 0 : 1; set(key, isNaN(v) ? minV : Math.max(minV, v)); }}
                  className="w-16 bg-zinc-800/60 border border-zinc-700/40 text-zinc-200 text-xs font-mono px-2 py-1 text-right" min={1} />
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="border border-zinc-800/40 p-3 bg-zinc-900/50">
        <span className="text-[10px] text-zinc-600 font-mono">Modo Candle: SL=1.0×ATR(M1) | TP=1.5×ATR(M1) | BE=0.5×ATR(M1) — automático</span>
      </div>

      {/* ── Filtros R1-R4 ──────────────────────────────────────────── */}
      <div className="border border-amber-500/20 bg-amber-500/5 p-3 flex flex-col gap-2">
        <span className="text-[10px] font-bold text-amber-400/80 uppercase tracking-wider">Filtros R1-R4 — Análise Apr 14-17 (Δ +88 pts)</span>
        {[
          ["r1_moderate_rth_mid_block",      "R1", "MODERATE × RTH_MID",            "EV=−53.78 pts | WR=16.7%"],
          ["r2_bearish_rth_mid_close_block",  "R2", "BEARISH_FLOW × RTH_MID|CLOSE",  "EV=−49.81 pts | WR=27.3%"],
          ["r3_gamma_put_wall_disabled",      "R3", "Desactiva GAMMA_PUT_WALL",       "Performance negativa"],
          ["r4_vwap_pullback_disabled",       "R4", "Desactiva VWAP_PULLBACK",        "WR<40% consistente"],
        ].map(([key, badge, label, detail]) => (
          <div key={key} className="flex items-center gap-2">
            <button
              onClick={() => set(key, !(local[key] !== false))}
              className={`flex-shrink-0 w-8 h-4 rounded-full transition-colors ${local[key] !== false ? "bg-amber-500/70" : "bg-zinc-700"}`}
            >
              <div className={`w-3 h-3 rounded-full bg-white mx-0.5 transition-transform ${local[key] !== false ? "translate-x-4" : "translate-x-0"}`} />
            </button>
            <span className="text-[9px] font-bold text-amber-400/60 font-mono w-5">{badge}</span>
            <div className="flex flex-col min-w-0">
              <span className="text-[10px] text-zinc-300 leading-tight">{label}</span>
              <span className="text-[9px] text-zinc-600 font-mono">{detail}</span>
            </div>
          </div>
        ))}
      </div>

      <button onClick={() => onSave(local)} disabled={saving}
        className="w-full py-2 text-xs font-bold bg-blue-500/20 border border-blue-500/40 text-blue-400 hover:bg-blue-500/30 transition-all disabled:opacity-40 flex items-center justify-center gap-1.5">
        {saving ? <><Spinner size={12} className="animate-spin" />Salvando...</> : "Salvar"}
      </button>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// Daily Funnel Report
// ══════════════════════════════════════════════════════════════════

const GATE_LABELS = {
  MODE_BLOCKED: { label: "Modo desligado", color: "text-zinc-500",  bar: "bg-zinc-600" },
  G7_BLOCKED:   { label: "Warm-up (G-7)",  color: "text-yellow-500", bar: "bg-yellow-600" },
  G8_BLOCKED:   { label: "Bias diário (G-8)", color: "text-orange-500", bar: "bg-orange-600" },
  G2_BLOCKED:   { label: "Qualidade (G-2)", color: "text-red-400",   bar: "bg-red-600" },
  EXECUTED:     { label: "Executado",       color: "text-emerald-400", bar: "bg-emerald-600" },
};

function fmtZone(z) {
  return (z || "?")
    .replace("SIGMA2_FADE_BUY",  "σ2 Fade Buy")
    .replace("SIGMA2_FADE_SELL", "σ2 Fade Sell")
    .replace("SIGMA1_PULLBACK_BUY",  "σ1 PB Buy")
    .replace("SIGMA1_PULLBACK_SELL", "σ1 PB Sell")
    .replace("VWAP_PULLBACK_BUY",  "VWAP PB Buy")
    .replace("VWAP_PULLBACK_SELL", "VWAP PB Sell")
    .replace("SESSION_VAL_FADE",  "VAL Fade")
    .replace("SESSION_VAH_FADE",  "VAH Fade")
    .replace("SESSION_POC_BUY",   "POC Buy")
    .replace("SESSION_POC_SELL",  "POC Sell")
    .replace("GAMMA_PUT_WALL_BUY",  "γ Put Wall Buy")
    .replace("GAMMA_CALL_WALL_SELL","γ Call Wall Sell");
}

function fmtReg(r) {
  return (r || "?").replace("ScalpRegime.", "").replace("BULLISH_FLOW","BULL").replace("BEARISH_FLOW","BEAR").replace("NEUTRAL","NEUT");
}

function Pill({ children, color }) {
  const c = {
    green:  "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    red:    "bg-red-500/15 text-red-400 border-red-500/30",
    yellow: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30",
    zinc:   "bg-zinc-800/50 text-zinc-400 border-zinc-700/40",
    blue:   "bg-blue-500/15 text-blue-400 border-blue-500/30",
  }[color] || "bg-zinc-800/50 text-zinc-400 border-zinc-700/40";
  return <span className={`inline-flex items-center px-1.5 py-0.5 text-[9px] font-bold border rounded-sm ${c}`}>{children}</span>;
}

function FunnelPanel() {
  const today = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);

  const [selDate, setSelDate]     = useState(today);
  const [data, setData]           = useState(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const [showSignals, setShowSignals] = useState(false);
  const [symFilter, setSymFilter] = useState("");

  // Zone Outcomes state (independente do daily-funnel)
  const [zoDays, setZoDays]         = useState(30);
  const [zoData, setZoData]         = useState(null);
  const [zoLoading, setZoLoading]   = useState(false);
  const [showZo, setShowZo]         = useState(true);
  const [zoExpanded, setZoExpanded] = useState({});

  // Param Audit state
  const [paData, setPaData]       = useState(null);
  const [paLoading, setPaLoading] = useState(false);
  const [showPa, setShowPa]       = useState(true);

  const fetchFunnel = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const params = new URLSearchParams({ date: selDate });
      if (symFilter) params.set("symbol", symFilter);
      const r = await axios.get(`${API}/scalp/daily-funnel?${params}`);
      setData(r.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, [selDate, symFilter]);

  const fetchZoneOutcomes = useCallback(async () => {
    setZoLoading(true);
    try {
      const params = new URLSearchParams({ days: zoDays });
      if (symFilter) params.set("symbol", symFilter);
      const r = await axios.get(`${API}/scalp/zone-outcomes?${params}`);
      setZoData(r.data);
    } catch (_) {
      setZoData(null);
    } finally {
      setZoLoading(false);
    }
  }, [zoDays, symFilter]);

  const fetchParamAudit = useCallback(async () => {
    setPaLoading(true);
    try {
      const params = new URLSearchParams({ days: zoDays });
      if (symFilter) params.set("symbol", symFilter);
      const r = await axios.get(`${API}/scalp/param-audit?${params}`);
      setPaData(r.data);
    } catch (_) {
      setPaData(null);
    } finally {
      setPaLoading(false);
    }
  }, [zoDays, symFilter]);

  useEffect(() => { fetchFunnel(); }, [fetchFunnel]);
  useEffect(() => { fetchZoneOutcomes(); }, [fetchZoneOutcomes]);
  useEffect(() => { fetchParamAudit(); }, [fetchParamAudit]);

  const f    = data?.funnel || {};
  const n3s  = data?.n3_stats || {};
  const bm   = data?.benchmark || {};
  const n1   = f.n1_total || 0;

  const funnelSteps = [
    { label: "N1  Zona activa",    val: n1,            pct: 100,                              color: "bg-zinc-600",    text: "text-zinc-300" },
    { label: "N2  Sinal raw",      val: f.n2_raw || 0, pct: n1 ? (f.n2_raw || 0) / n1 * 100 : 0, color: "bg-blue-600",  text: "text-blue-400" },
    { label: "N2  Dedup (5min)",   val: f.n2_dedup||0, pct: n1 ? (f.n2_dedup||0) / n1 * 100 : 0, color: "bg-indigo-600",text: "text-indigo-400" },
    { label: "D30 Bloqueado",      val: f.d30_blocked||0, pct: n1 ? (f.d30_blocked||0) / n1 * 100 : 0, color: "bg-orange-700", text: "text-orange-400" },
    { label: "Outros bloqueios",   val: f.other_blocked||0, pct: n1 ? (f.other_blocked||0) / n1 * 100 : 0, color: "bg-red-700", text: "text-red-400" },
    { label: "N3  Trades",         val: f.n3_trades||0,pct: n1 ? (f.n3_trades||0) / n1 * 100 : 0, color: "bg-emerald-600",text: "text-emerald-400" },
  ];

  const sessions = useMemo(() => {
    const s = new Set();
    Object.values(data?.zone_session_matrix || {}).forEach(sess => Object.keys(sess).forEach(k => s.add(k)));
    return Array.from(s).sort();
  }, [data]);

  return (
    <div className="flex flex-col gap-4">
      {/* ── Header: date tabs + sym filter + refresh ── */}
      <div className="flex items-center gap-2 flex-wrap">
        {[today, yesterday].map(d => (
          <button key={d} onClick={() => setSelDate(d)}
            className={`px-3 py-1 text-[10px] font-mono border transition-all ${selDate === d ? "border-blue-500 text-blue-400 bg-blue-500/10" : "border-zinc-700 text-zinc-500 hover:border-zinc-500"}`}>
            {d === today ? `Hoje (${d})` : `Ontem (${d})`}
          </button>
        ))}
        <input type="date" value={selDate} onChange={e => setSelDate(e.target.value)}
          className="bg-zinc-900 border border-zinc-700/50 text-zinc-300 text-[10px] px-2 py-1" />
        <select value={symFilter} onChange={e => setSymFilter(e.target.value)}
          className="bg-zinc-900 border border-zinc-700/50 text-zinc-300 text-[10px] px-2 py-1">
          <option value="">Todos</option>
          <option value="MNQ">MNQ</option>
          <option value="MES">MES</option>
        </select>
        <button onClick={fetchFunnel} disabled={loading}
          className="flex items-center gap-1 px-3 py-1 text-xs border border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200 transition-all disabled:opacity-40 ml-auto">
          {loading ? <Spinner size={11} className="animate-spin" /> : <ArrowsCounterClockwise size={11} />}
          Actualizar
        </button>
      </div>

      {error && <div className="text-xs text-red-400 border border-red-500/20 bg-red-500/10 px-3 py-2">{error}</div>}

      {loading && !data && (
        <div className="flex items-center justify-center gap-2 py-10 text-zinc-600 text-xs">
          <Spinner size={14} className="animate-spin" /> A carregar…
        </div>
      )}

      {data && (
        <>
          {/* ── Row 1: Funnel + Benchmark ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">

            {/* Funil N1→N2→D30→N3 */}
            <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-2">
              <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">Funil do Dia — {selDate}</span>
              {n1 === 0 ? (
                <span className="text-xs text-zinc-600 py-4 text-center">Sem snapshots com zona activa neste dia.</span>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {funnelSteps.map(step => (
                    <div key={step.label} className="flex items-center gap-2">
                      <span className={`w-32 text-[10px] font-mono ${step.text} whitespace-nowrap`}>{step.label}</span>
                      <div className="flex-1 h-3.5 bg-zinc-800/50 relative overflow-hidden">
                        <div className={`h-full ${step.color} opacity-70 transition-all`}
                          style={{ width: `${Math.max(step.pct, step.val > 0 ? 2 : 0)}%` }} />
                        <span className="absolute inset-0 flex items-center px-1.5 text-[10px] font-mono text-white">
                          {step.val}
                        </span>
                      </div>
                      <span className="w-10 text-right text-[10px] font-mono text-zinc-600">{step.pct.toFixed(0)}%</span>
                    </div>
                  ))}
                  <div className="flex gap-4 pt-1 border-t border-zinc-800/40 mt-1">
                    <span className="text-[10px] text-zinc-600">N2→N3: <span className="text-zinc-400 font-bold">{f.n2_to_n3_pct ?? "—"}%</span></span>
                    <span className="text-[10px] text-zinc-600">N1→N2: <span className="text-zinc-400 font-bold">{f.n1_to_n2_pct ?? "—"}%</span></span>
                  </div>
                </div>
              )}
            </div>

            {/* N3 Real + Benchmark hipotético */}
            <div className="flex flex-col gap-2">
              {/* N3 Real */}
              <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-1.5">
                <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">N3 Real — Trades</span>
                <div className="grid grid-cols-4 gap-2">
                  {[
                    ["Total",  n3s.total ?? "—", "text-zinc-300"],
                    ["Wins",   n3s.wins  ?? "—", "text-emerald-400"],
                    ["Losses", n3s.losses?? "—", "text-red-400"],
                    ["WR",     n3s.wr_pct != null ? `${n3s.wr_pct}%` : "—",
                               (n3s.wr_pct||0) >= 50 ? "text-emerald-400" : "text-red-400"],
                  ].map(([l,v,c]) => (
                    <div key={l} className="flex flex-col gap-0.5">
                      <span className="text-[9px] text-zinc-600">{l}</span>
                      <span className={`text-sm font-mono font-bold ${c}`}>{v}</span>
                    </div>
                  ))}
                </div>
                <div className="flex gap-4 pt-1 border-t border-zinc-800/40">
                  <span className="text-[10px] text-zinc-500">PnL: <span className={`font-bold font-mono ${(n3s.pnl_pts||0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>{n3s.pnl_pts != null ? `${n3s.pnl_pts > 0 ? "+" : ""}${n3s.pnl_pts} pts` : "—"}</span></span>
                  <span className="text-[10px] text-zinc-500">USD: <span className={`font-bold font-mono ${(n3s.pnl_usd||0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>{n3s.pnl_usd != null ? `${n3s.pnl_usd > 0 ? "+" : ""}$${n3s.pnl_usd}` : "—"}</span></span>
                </div>
              </div>

              {/* Benchmark */}
              <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-bold text-blue-400 uppercase tracking-wider">Benchmark N2 Hipotético</span>
                  <span className="text-[9px] text-zinc-600">(forward TP/SL 30min)</span>
                </div>
                <div className="grid grid-cols-4 gap-2">
                  {[
                    ["Resolvidos", bm.resolved ?? "—", "text-zinc-300"],
                    ["TARGET",     bm.wins     ?? "—", "text-emerald-400"],
                    ["STOP",       bm.losses   ?? "—", "text-red-400"],
                    ["WR hypo",    bm.wr_pct != null ? `${bm.wr_pct}%` : "—",
                                   (bm.wr_pct||0) >= 50 ? "text-emerald-400" : "text-red-400"],
                  ].map(([l,v,c]) => (
                    <div key={l} className="flex flex-col gap-0.5">
                      <span className="text-[9px] text-zinc-600">{l}</span>
                      <span className={`text-sm font-mono font-bold ${c}`}>{v}</span>
                    </div>
                  ))}
                </div>
                <div className="flex gap-4 pt-1 border-t border-zinc-800/40">
                  <span className="text-[10px] text-zinc-500">PnL hypo: <span className={`font-bold font-mono ${(bm.pnl_pts||0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>{bm.pnl_pts != null ? `${bm.pnl_pts > 0 ? "+" : ""}${bm.pnl_pts} pts` : "—"}</span></span>
                  <span className="text-[10px] text-zinc-500">Pendentes: <span className="text-zinc-400 font-mono font-bold">{bm.pending ?? "—"}</span></span>
                </div>
              </div>
            </div>
          </div>

          {/* ── Row 1.5: N3 Contexto — breakdown por dimensão + tabela ── */}
          {data.n3_context && (data.n3_context.trades || []).length > 0 && (() => {
            const ctx = data.n3_context;
            const qualOrder = ["STRONG","MODERATE","WEAK","?"];
            const d30Order  = ["OK","RISK","BLOCKED","?","None","null"];
            const scoreOrder = ["4+","3-4","2-3","0-2","<0","?"];

            function DimTable({ title, data: dimData, order, colorFn }) {
              const rows = Object.entries(dimData).sort((a,b) => {
                const ia = order.indexOf(a[0]), ib = order.indexOf(b[0]);
                return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
              });
              if (rows.length === 0) return null;
              return (
                <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-1.5">
                  <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">{title}</span>
                  <div className="flex flex-col gap-1">
                    {rows.map(([key, v]) => {
                      const tot = v.wins + v.losses;
                      const wrPct = v.wr_pct ?? 0;
                      return (
                        <div key={key} className="flex items-center gap-2">
                          <span className={`w-20 text-[10px] font-mono ${colorFn(key)} font-bold truncate`}>{key}</span>
                          <div className="flex-1 h-3 bg-zinc-800/50 relative overflow-hidden">
                            <div className={`h-full ${wrPct >= 50 ? "bg-emerald-700" : "bg-red-800"} opacity-60`}
                              style={{ width: `${wrPct}%` }} />
                            <span className="absolute inset-0 flex items-center px-1 text-[9px] font-mono text-white">
                              {v.wins}W/{v.losses}L
                            </span>
                          </div>
                          <span className={`w-10 text-right text-[10px] font-mono font-bold ${wrPct >= 50 ? "text-emerald-400" : "text-red-400"}`}>
                            {v.wr_pct != null ? `${v.wr_pct}%` : "—"}
                          </span>
                          <span className={`w-12 text-right text-[10px] font-mono ${(v.pnl_pts||0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                            {v.pnl_pts != null ? `${v.pnl_pts > 0 ? "+" : ""}${v.pnl_pts}` : "—"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            }

            function qualColor(k) {
              if (k==="STRONG") return "text-emerald-400";
              if (k==="MODERATE") return "text-yellow-400";
              return "text-zinc-500";
            }
            function d30Color(k) {
              if (k==="OK") return "text-emerald-400";
              if (k==="RISK") return "text-yellow-400";
              if (k==="BLOCKED") return "text-red-400";
              return "text-zinc-500";
            }
            function scoreColor(k) {
              if (k==="4+") return "text-emerald-400";
              if (k==="3-4") return "text-blue-400";
              if (k==="2-3") return "text-yellow-400";
              return "text-red-400";
            }

            return (
              <>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <DimTable title="N3 por Qualidade" data={ctx.by_quality} order={qualOrder}  colorFn={qualColor} />
                  <DimTable title="N3 por D30 State"  data={ctx.by_d30}     order={d30Order}   colorFn={d30Color}  />
                  <DimTable title="N3 por Score"      data={ctx.by_score}   order={scoreOrder} colorFn={scoreColor}/>
                </div>

                {/* Tabela individual de trades */}
                <div className="border border-zinc-800/40 bg-zinc-900/30">
                  <div className="px-3 py-2 border-b border-zinc-800/40">
                    <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">
                      N3 Trades — Contexto de Entrada ({ctx.trades.length})
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-[10px] font-mono">
                      <thead>
                        <tr className="border-b border-zinc-800/40 text-zinc-600">
                          <th className="text-left py-1 px-2">Hora</th>
                          <th className="text-left py-1 px-2">Sym</th>
                          <th className="text-left py-1 px-2">Zona</th>
                          <th className="text-left py-1 px-2">Sess</th>
                          <th className="text-left py-1 px-2">Qual</th>
                          <th className="text-right py-1 px-2">Score</th>
                          <th className="text-left py-1 px-2">D30</th>
                          <th className="text-left py-1 px-2">VWAP</th>
                          <th className="text-left py-1 px-2">Saída</th>
                          <th className="text-right py-1 px-2">PnL</th>
                          <th className="text-left py-1 px-2">Resultado</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ctx.trades.map((t, i) => {
                          const isWin  = t.outcome === "WIN";
                          const isOpen = t.outcome === "OPEN";
                          return (
                            <tr key={i} className="border-b border-zinc-800/20 hover:bg-zinc-800/20">
                              <td className="py-0.5 px-2 text-zinc-500 whitespace-nowrap">
                                {t.created_at ? new Date(t.created_at).toLocaleTimeString("pt-BR") : "—"}
                              </td>
                              <td className="py-0.5 px-2 text-zinc-200">{t.symbol}</td>
                              <td className="py-0.5 px-2 text-zinc-300 whitespace-nowrap">{fmtZone(t.zone_type)}</td>
                              <td className="py-0.5 px-2 text-zinc-500">{t.session}</td>
                              <td className="py-0.5 px-2">
                                <Pill color={t.quality==="STRONG"?"green":t.quality==="MODERATE"?"yellow":"zinc"}>
                                  {t.quality||"?"}
                                </Pill>
                              </td>
                              <td className={`py-0.5 px-2 text-right font-bold ${
                                t.zone_score == null ? "text-zinc-600" :
                                t.zone_score >= 4 ? "text-emerald-400" :
                                t.zone_score >= 2 ? "text-blue-400" : "text-red-400"
                              }`}>
                                {t.zone_score != null ? t.zone_score.toFixed(2) : "—"}
                              </td>
                              <td className="py-0.5 px-2">
                                <Pill color={t.d30_state==="OK"?"green":t.d30_state==="RISK"?"yellow":t.d30_state==="BLOCKED"?"red":"zinc"}>
                                  {t.d30_state||"?"}
                                </Pill>
                              </td>
                              <td className="py-0.5 px-2 text-zinc-500 text-[9px] whitespace-nowrap">{t.vwap_zone||"—"}</td>
                              <td className="py-0.5 px-2 text-zinc-500 text-[9px] whitespace-nowrap truncate max-w-[80px]">{t.exit_reason||"—"}</td>
                              <td className={`py-0.5 px-2 text-right font-bold ${isWin?"text-emerald-400":isOpen?"text-zinc-500":"text-red-400"}`}>
                                {t.pnl_pts != null ? `${t.pnl_pts > 0 ? "+" : ""}${t.pnl_pts}` : "—"}
                              </td>
                              <td className="py-0.5 px-2">
                                <Pill color={isWin?"green":isOpen?"zinc":"red"}>{t.outcome}</Pill>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            );
          })()}

          {/* ── Row 2: Block Reasons ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* Gate blocks */}
            <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-2">
              <span className="text-[10px] font-bold text-red-400 uppercase tracking-wider">Bloqueios de Gate (NO_SIGNAL)</span>
              {(data.block_reasons?.gate_blocks || []).length === 0 ? (
                <span className="text-[10px] text-zinc-600">—</span>
              ) : (data.block_reasons.gate_blocks.map(({ reason, count }) => (
                <div key={reason} className="flex items-center gap-2">
                  <div className="flex-1 truncate text-[10px] font-mono text-zinc-400">{reason}</div>
                  <span className="text-[10px] font-mono text-red-400 font-bold whitespace-nowrap">{count}×</span>
                </div>
              )))}
            </div>

            {/* Score factors */}
            <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-2">
              <span className="text-[10px] font-bold text-orange-400 uppercase tracking-wider">Penalizações de Score</span>
              {(data.block_reasons?.score_factors || []).length === 0 ? (
                <span className="text-[10px] text-zinc-600">—</span>
              ) : (data.block_reasons.score_factors.map(({ reason, count }) => (
                <div key={reason} className="flex items-center gap-2">
                  <div className="flex-1 truncate text-[10px] font-mono text-zinc-400">{reason}</div>
                  <span className="text-[10px] font-mono text-orange-400 font-bold whitespace-nowrap">{count}×</span>
                </div>
              )))}
            </div>
          </div>

          {/* ── Row 3: Zona×Sessão Matrix + N1 Distribuição ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">

            {/* Zona × Sessão */}
            {Object.keys(data.zone_session_matrix || {}).length > 0 && (
              <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-2">
                <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">N2 Sinais — Zona × Sessão</span>
                <div className="overflow-x-auto">
                  <table className="w-full text-[10px] font-mono">
                    <thead>
                      <tr className="border-b border-zinc-800/40 text-zinc-600">
                        <th className="text-left pb-1">Zona</th>
                        {sessions.map(s => (
                          <th key={s} className="text-right pb-1 px-1">{s.replace("OVERNIGHT","OVNT").replace("RTH_","")}</th>
                        ))}
                        <th className="text-right pb-1 px-1">Tot</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(data.zone_session_matrix).sort((a, b) => {
                        const ta = Object.values(a[1]).reduce((s,v) => s+v,0);
                        const tb = Object.values(b[1]).reduce((s,v) => s+v,0);
                        return tb - ta;
                      }).map(([zone, sess]) => {
                        const tot = Object.values(sess).reduce((s,v) => s+v,0);
                        return (
                          <tr key={zone} className="border-b border-zinc-800/20 hover:bg-zinc-800/20">
                            <td className="py-0.5 text-zinc-300 whitespace-nowrap">{fmtZone(zone)}</td>
                            {sessions.map(s => (
                              <td key={s} className={`py-0.5 text-right px-1 ${sess[s] ? "text-blue-400 font-bold" : "text-zinc-700"}`}>
                                {sess[s] || "—"}
                              </td>
                            ))}
                            <td className="py-0.5 text-right px-1 text-zinc-400 font-bold">{tot}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* N1 por Regime + por Sessão */}
            <div className="flex flex-col gap-2">
              {Object.keys(data.n1_by_regime || {}).length > 0 && (
                <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-1.5">
                  <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">N1 por Regime</span>
                  {Object.entries(data.n1_by_regime).map(([reg, cnt]) => {
                    const pct = n1 ? Math.round(cnt / n1 * 100) : 0;
                    return (
                      <div key={reg} className="flex items-center gap-2">
                        <span className="w-28 text-[10px] font-mono text-zinc-400 truncate">{fmtReg(reg)}</span>
                        <div className="flex-1 h-2.5 bg-zinc-800/50 overflow-hidden">
                          <div className="h-full bg-zinc-600 opacity-70" style={{ width: `${pct}%` }} />
                        </div>
                        <span className="w-8 text-right text-[10px] font-mono text-zinc-500">{cnt}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              {Object.keys(data.n1_by_session || {}).length > 0 && (
                <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-1.5">
                  <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">N1 por Sessão</span>
                  {Object.entries(data.n1_by_session).map(([sess, cnt]) => {
                    const pct = n1 ? Math.round(cnt / n1 * 100) : 0;
                    return (
                      <div key={sess} className="flex items-center gap-2">
                        <span className="w-28 text-[10px] font-mono text-zinc-400 truncate">{sess}</span>
                        <div className="flex-1 h-2.5 bg-zinc-800/50 overflow-hidden">
                          <div className="h-full bg-indigo-700 opacity-70" style={{ width: `${pct}%` }} />
                        </div>
                        <span className="w-8 text-right text-[10px] font-mono text-zinc-500">{cnt}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              {/* D30 status */}
              {data.d30 && (
                <div className="border border-zinc-800/40 bg-zinc-900/30 p-3 flex flex-col gap-1">
                  <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">D30 (snapshots)</span>
                  <div className="flex gap-4 text-[10px] font-mono">
                    <span className="text-emerald-400">OK: <b>{data.d30.clear}</b></span>
                    <span className="text-yellow-400">RISK: <b>{data.d30.risk}</b></span>
                    <span className="text-red-400">BLOCKED: <b>{data.d30.blocked}</b></span>
                  </div>
                </div>
              )}

              {/* R1-R4 breakdown */}
              {data.r_filters && (
                <div className="border border-amber-500/20 bg-amber-500/5 p-3 flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-bold text-amber-400/80 uppercase tracking-wider">Filtros R1-R4 (snapshots ACTIVE)</span>
                    <span className="text-[10px] font-mono text-amber-400 font-bold">{data.r_filters.total}× total</span>
                  </div>
                  {[
                    ["R1", "MODERATE×RTH_MID",        data.r_filters.r1_moderate_rth_mid,      "bg-red-600"],
                    ["R2", "BEARISH×RTH_MID|CLOSE",   data.r_filters.r2_bearish_rth_mid_close, "bg-orange-600"],
                    ["R3", "GAMMA_PUT_WALL",           data.r_filters.r3_gamma_put_wall,        "bg-yellow-700"],
                    ["R4", "VWAP_PULLBACK",            data.r_filters.r4_vwap_pullback,         "bg-zinc-600"],
                  ].map(([badge, label, cnt, barColor]) => {
                    const maxCnt = Math.max(1, data.r_filters.total);
                    const pct    = Math.round((cnt || 0) / maxCnt * 100);
                    return (
                      <div key={badge} className="flex items-center gap-2">
                        <span className="text-[9px] font-bold text-amber-400/60 font-mono w-5 flex-shrink-0">{badge}</span>
                        <span className="w-32 text-[10px] font-mono text-zinc-400 truncate flex-shrink-0">{label}</span>
                        <div className="flex-1 h-2 bg-zinc-800/50 overflow-hidden">
                          <div className={`h-full ${barColor} opacity-70`} style={{ width: `${pct}%` }} />
                        </div>
                        <span className="w-8 text-right text-[10px] font-mono text-amber-400 font-bold flex-shrink-0">{cnt ?? 0}</span>
                      </div>
                    );
                  })}
                  <span className="text-[9px] text-zinc-600 font-mono mt-0.5">snapshots brutos (30s) — dividir por ~10 para eventos únicos</span>
                </div>
              )}
            </div>
          </div>

          {/* ── Row 3b: Track B Observer ── */}
          {data.track_b && (
            <div className="border border-blue-500/20 bg-blue-500/5 p-3 flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-bold text-blue-400/80 uppercase tracking-wider">Track B — Pullback Observer</span>
                {data.track_b.readiness.readiness_met ? (
                  <span className="text-[9px] font-bold text-emerald-400 border border-emerald-500/30 px-1.5 py-0.5">PRONTO ✓</span>
                ) : (
                  <span className="text-[9px] text-zinc-500 font-mono">
                    Observer-only — {data.track_b.readiness.n_return_all_time}/{data.track_b.readiness.target_returns} RETURN events | {data.track_b.readiness.distinct_dates}/{data.track_b.readiness.target_dates} datas
                  </span>
                )}
              </div>

              {/* Funil do dia */}
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <span className="text-[9px] text-zinc-600 uppercase tracking-wider font-mono">Funil do dia</span>
                  {(() => {
                    const td = data.track_b.today;
                    const max = Math.max(1, td.touched);
                    return [
                      ["TOUCHED",  td.touched,  "bg-blue-700",   "text-blue-400"],
                      ["PULLBACK", td.pullback, "bg-indigo-700", "text-indigo-400"],
                      ["RETURN",   td.return,   "bg-emerald-700","text-emerald-400"],
                    ].map(([label, cnt, bar, txt]) => (
                      <div key={label} className="flex items-center gap-2">
                        <span className="w-16 text-[9px] font-mono text-zinc-500 flex-shrink-0">{label}</span>
                        <div className="flex-1 h-2 bg-zinc-800/50 overflow-hidden">
                          <div className={`h-full ${bar} opacity-70`} style={{ width: `${Math.round(cnt / max * 100)}%` }} />
                        </div>
                        <span className={`w-6 text-right text-[10px] font-mono font-bold flex-shrink-0 ${txt}`}>{cnt}</span>
                      </div>
                    ));
                  })()}
                </div>

                {/* Métricas */}
                <div className="flex flex-col gap-1.5">
                  <span className="text-[9px] text-zinc-600 uppercase tracking-wider font-mono">RETURN quality</span>
                  <div className="flex flex-col gap-1 text-[10px] font-mono">
                    <div className="flex justify-between">
                      <span className="text-zinc-500">Would trigger</span>
                      <span className="text-emerald-400 font-bold">
                        {data.track_b.today.return > 0
                          ? `${data.track_b.today.would_trigger}/${data.track_b.today.return}`
                          : "—"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-zinc-500">Avg pullback</span>
                      <span className="text-blue-400 font-bold">
                        {data.track_b.today.avg_pullback_pts != null
                          ? `${data.track_b.today.avg_pullback_pts} pts`
                          : "—"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-zinc-500">Avg return %</span>
                      <span className="text-blue-400 font-bold">
                        {data.track_b.today.avg_return_pct != null
                          ? `${(data.track_b.today.avg_return_pct * 100).toFixed(1)}%`
                          : "—"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-zinc-500">Trades exec.</span>
                      <span className={`font-bold ${data.track_b.today.trades_executed > 0 ? "text-emerald-400" : "text-zinc-600"}`}>
                        {data.track_b.today.trades_executed}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Readiness progress bar */}
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                  <span className="text-[9px] text-zinc-600 font-mono w-28 flex-shrink-0">RETURN acum.</span>
                  <div className="flex-1 h-1.5 bg-zinc-800/50 overflow-hidden">
                    <div className="h-full bg-blue-600 opacity-70 transition-all"
                      style={{ width: `${Math.min(100, data.track_b.readiness.n_return_all_time / data.track_b.readiness.target_returns * 100)}%` }} />
                  </div>
                  <span className="text-[9px] font-mono text-blue-400 w-10 text-right flex-shrink-0">
                    {data.track_b.readiness.n_return_all_time}/{data.track_b.readiness.target_returns}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[9px] text-zinc-600 font-mono w-28 flex-shrink-0">Sessões distintas</span>
                  <div className="flex-1 h-1.5 bg-zinc-800/50 overflow-hidden">
                    <div className="h-full bg-indigo-600 opacity-70 transition-all"
                      style={{ width: `${Math.min(100, data.track_b.readiness.distinct_dates / data.track_b.readiness.target_dates * 100)}%` }} />
                  </div>
                  <span className="text-[9px] font-mono text-indigo-400 w-10 text-right flex-shrink-0">
                    {data.track_b.readiness.distinct_dates}/{data.track_b.readiness.target_dates}
                  </span>
                </div>
              </div>

              {/* RETURN events do dia */}
              {(data.track_b.return_events || []).length > 0 && (
                <div className="border border-blue-500/10 p-2 flex flex-col gap-1">
                  <span className="text-[9px] text-zinc-600 font-mono uppercase">RETURN events hoje</span>
                  {data.track_b.return_events.map((ev, i) => (
                    <div key={i} className="flex items-center gap-2 text-[9px] font-mono">
                      <span className="text-zinc-600">{ev.ts ? ev.ts.substring(11, 16) : "—"}</span>
                      <span className="text-zinc-400">{ev.symbol}</span>
                      <span className="text-zinc-500 truncate">{(ev.zone_type || "?").replace(/_/g," ")}</span>
                      <span className="text-blue-400 whitespace-nowrap">{ev.pullback_pts != null ? `${ev.pullback_pts}pts` : ""}</span>
                      <span className={`whitespace-nowrap font-bold ${ev.would_trigger ? "text-emerald-400" : "text-zinc-600"}`}>
                        {ev.would_trigger ? "✓ trigger" : "✗ skip"}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Row 3c: Zone Outcomes Auditoria 4 Camadas ── */}
          <div className="border border-violet-500/20 bg-violet-500/5">
            <button
              onClick={() => setShowZo(v => !v)}
              className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-bold text-violet-400/80 uppercase tracking-wider hover:bg-violet-500/10 transition-all">
              <span className="flex items-center gap-2 flex-wrap">
                Zone Outcomes — Auditoria Funil
                {zoData?.totals && !zoLoading && (() => {
                  const t = zoData.totals;
                  return (
                    <span className="font-normal normal-case flex gap-2 flex-wrap">
                      <span className="text-indigo-400" title="Passou S2 — benchmark disponível">N2 {t.n2_total}</span>
                      <span className="text-orange-400" title="Gate de sessão D30 (bloqueia antes de avaliar zonas)">D30gate {t.d30_total}</span>
                      <span className="text-red-400/70" title="Tinha zona, falhou score/gate S2">S2✗ {t.s2fail_total}</span>
                      <span className="text-emerald-400" title="Trades reais fechados">N3 {t.n3_total}</span>
                      <span className="text-violet-600">· {zoData.n_groups} grupos · {zoDays}d</span>
                    </span>
                  );
                })()}
                {zoLoading && <span className="ml-2 text-zinc-600 font-normal normal-case">carregando…</span>}
              </span>
              <div className="flex items-center gap-2">
                <div className="flex gap-1" onClick={e => e.stopPropagation()}>
                  {[7, 14, 30, 60].map(d => (
                    <button key={d} onClick={() => setZoDays(d)}
                      className={`px-1.5 py-0.5 text-[9px] font-mono border transition-all
                        ${zoDays === d
                          ? "border-violet-500/60 text-violet-300 bg-violet-500/15"
                          : "border-zinc-700/40 text-zinc-600 hover:text-zinc-400"}`}>
                      {d}d
                    </button>
                  ))}
                </div>
                <span className="text-zinc-600">{showZo ? "▲" : "▼"}</span>
              </div>
            </button>

            {showZo && (() => {
              const outcomes = zoData?.outcomes || [];
              const n3Zones  = zoData?.n3_by_zone || [];

              const fmtLayer = (layer, colorClass) => {
                if (!layer || layer.n === 0) return <span className="text-zinc-700">—</span>;
                const wr = layer.wr_pct;
                const wrC = wr == null ? "text-zinc-600" : wr >= 60 ? "text-emerald-400 font-bold" : wr <= 35 ? "text-red-400 font-bold" : "text-zinc-400";
                const pnlC = layer.avg_pnl_pts == null ? "text-zinc-600" : layer.avg_pnl_pts > 0 ? "text-emerald-400" : "text-red-400";
                return (
                  <span className={`${colorClass} font-mono`}>
                    <span className="font-bold">{layer.n}</span>
                    {wr != null && <span className={`ml-1 ${wrC}`}>{wr}%</span>}
                    {layer.avg_pnl_pts != null && (
                      <span className={`ml-1 ${pnlC}`}>{layer.avg_pnl_pts > 0 ? "+" : ""}{layer.avg_pnl_pts}</span>
                    )}
                  </span>
                );
              };

              return (
                <div className="border-t border-violet-500/10">
                  {outcomes.length === 0 && !zoLoading && (
                    <div className="px-3 py-4 text-center text-[10px] text-zinc-600">
                      Sem dados suficientes para os últimos {zoDays} dias.
                    </div>
                  )}

                  {outcomes.length > 0 && (
                    <div className="overflow-x-auto">
                      {/* Legend + D30 gate note */}
                      <div className="flex flex-wrap gap-3 px-3 py-1.5 border-b border-violet-500/10 text-[9px] text-zinc-600">
                        <span><span className="text-indigo-400 font-bold">N2</span> = passou S2 (benchmark hipotético)</span>
                        <span><span className="text-red-400/70 font-bold">S2✗</span> = tinha zona, falhou score/gate</span>
                        <span><span className="text-orange-400 font-bold">D30gate</span> = {zoData?.totals?.d30_total || 0} snapshots bloqueados por deslocamento 30min (gate de sessão — ocorre antes de avaliação de zonas)</span>
                      </div>
                      <table className="w-full text-[10px] font-mono">
                        <thead>
                          <tr className="border-b border-violet-500/10 text-zinc-600 text-[9px]">
                            <th className="text-left py-1 px-2">Sym</th>
                            <th className="text-left py-1 px-2">Zona / Tipo</th>
                            <th className="text-right py-1 px-2">Nível</th>
                            <th className="text-center py-1 px-2">Dir</th>
                            <th className="text-left py-1 px-2 text-indigo-400">N2 (n · WR · avg PnL)</th>
                            <th className="text-right py-1 px-2 text-red-400/70">S2✗</th>
                            <th className="text-right py-1 px-2 text-zinc-700">Último</th>
                            <th className="py-1 px-2 w-6"></th>
                          </tr>
                        </thead>
                        <tbody>
                          {outcomes.map((row, i) => {
                            const gk       = `${row.symbol}|${row.zone_type}|${row.level}`;
                            const expanded = zoExpanded[gk];
                            const n2       = row.n2    || {};
                            const s2f      = row.s2_fail || {};
                            const n2wr     = n2.wr_pct;
                            const rowBg    = n2wr == null ? "" : n2wr >= 60 ? "bg-emerald-500/5" : n2wr <= 35 ? "bg-red-500/5" : "";
                            return (
                              <Fragment key={i}>
                                <tr
                                  className={`border-b border-violet-500/5 hover:bg-violet-500/5 cursor-pointer ${rowBg}`}
                                  onClick={() => setZoExpanded(prev => ({...prev, [gk]: !prev[gk]}))}>
                                  <td className="py-0.5 px-2 text-zinc-300">{row.symbol}</td>
                                  <td className="py-0.5 px-2 text-zinc-300 whitespace-nowrap">{fmtZone(row.zone_type)}</td>
                                  <td className="py-0.5 px-2 text-right text-violet-300 font-bold">{row.level?.toFixed(2) ?? "—"}</td>
                                  <td className="py-0.5 px-2 text-center text-zinc-500">
                                    {row.direction === "BUY" ? "▲" : row.direction === "SELL" ? "▼" : "—"}
                                  </td>
                                  <td className="py-0.5 px-2">{fmtLayer(n2, "text-indigo-300")}</td>
                                  <td className="py-0.5 px-2 text-right text-red-400/70">{s2f.n > 0 ? s2f.n : <span className="text-zinc-700">—</span>}</td>
                                  <td className="py-0.5 px-2 text-right text-zinc-700 whitespace-nowrap">
                                    {row.last_seen ? row.last_seen.substring(0, 10) : "—"}
                                  </td>
                                  <td className="py-0.5 px-2 text-zinc-700 text-center">{expanded ? "▲" : "▼"}</td>
                                </tr>

                                {/* ── Expanded: N2 eventos + S2 razões ── */}
                                {expanded && (
                                  <tr className="border-b border-violet-500/10">
                                    <td colSpan={8} className="px-0 py-0">
                                      <div className="grid grid-cols-1 md:grid-cols-2 divide-x divide-violet-500/10 bg-zinc-900/70 text-[9px]">

                                        {/* N2 column */}
                                        <div className="p-2">
                                          <div className="text-indigo-400 font-bold mb-1 text-[9px] uppercase tracking-wider">
                                            N2 · Passou S2 · Benchmark {zoData?.window_min ?? 30}min
                                          </div>
                                          {(n2.events || []).length === 0
                                            ? <div className="text-zinc-600 italic">Sem eventos N2</div>
                                            : (n2.events || []).map((ev, j) => {
                                                const ec = ev.outcome === "TARGET" ? "text-emerald-400" : ev.outcome === "STOP" ? "text-red-400" : "text-zinc-500";
                                                return (
                                                  <div key={j} className="flex gap-1 items-center py-0.5 border-b border-zinc-800/30">
                                                    <span className="text-zinc-700 w-24 shrink-0">{ev.ts ? ev.ts.substring(5,16).replace("T"," ") : "—"}</span>
                                                    <span className="text-zinc-600">{ev.session?.substring(0,5)}</span>
                                                    <span className="text-zinc-600 ml-1">{ev.regime?.substring(0,4)}</span>
                                                    <span className="text-zinc-600 ml-auto">{ev.entry?.toFixed(1) ?? "—"}</span>
                                                    <span className={`font-bold w-14 text-right ${ec}`}>{ev.outcome === "TARGET" ? "✓TARGET" : ev.outcome === "STOP" ? "✗STOP" : ev.outcome?.substring(0,4)}</span>
                                                    <span className={`w-10 text-right font-bold ${ec}`}>
                                                      {ev.pnl_pts != null ? `${ev.pnl_pts > 0 ? "+" : ""}${ev.pnl_pts}` : "—"}
                                                    </span>
                                                  </div>
                                                );
                                              })
                                          }
                                        </div>

                                        {/* S2 fail column */}
                                        <div className="p-2">
                                          <div className="text-red-400/70 font-bold mb-1 text-[9px] uppercase tracking-wider">
                                            S2✗ · Motivos de Bloqueio · {s2f.n || 0} eventos
                                          </div>
                                          {(s2f.top_reasons || []).length === 0
                                            ? <div className="text-zinc-600 italic">Sem motivos registados</div>
                                            : (s2f.top_reasons || []).map((r, j) => (
                                                <div key={j} className="flex justify-between py-0.5 border-b border-zinc-800/30">
                                                  <span className="text-zinc-500 truncate max-w-52">{r.reason}</span>
                                                  <span className="text-red-400/50 font-bold ml-2 shrink-0">{r.count}×</span>
                                                </div>
                                              ))
                                          }
                                          <div className="mt-1 pt-1 border-t border-zinc-800/30 text-zinc-700 text-[8px]">
                                            Ratio S2✗/N2: {n2.n > 0 ? (s2f.n / n2.n).toFixed(1) : "—"}× mais bloqueios que sinais
                                          </div>
                                        </div>
                                      </div>
                                    </td>
                                  </tr>
                                )}
                              </Fragment>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* ── N3 Executados (por zone_type — outcome real) ── */}
                  {n3Zones.length > 0 && (
                    <div className="border-t border-violet-500/10 px-3 py-2">
                      <div className="text-[9px] text-emerald-400/80 font-bold uppercase tracking-wider mb-1">
                        N3 Executados — Resultado Real por Tipo de Zona
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {n3Zones.map((z, i) => {
                          const pnlC = z.pnl_pts > 0 ? "text-emerald-400" : z.pnl_pts < 0 ? "text-red-400" : "text-zinc-500";
                          const wrC  = z.wr_pct >= 60 ? "text-emerald-400" : z.wr_pct <= 35 ? "text-red-400" : "text-zinc-400";
                          return (
                            <div key={i} className="border border-emerald-500/15 bg-emerald-500/5 px-2 py-1 text-[9px] font-mono flex flex-col gap-0.5 min-w-32">
                              <div className="text-zinc-300 font-bold text-[8px]">{z.symbol} {fmtZone(z.zone_type)}</div>
                              <div className="flex gap-2 items-center">
                                <span className="text-zinc-600">n={z.n}</span>
                                <span className="text-emerald-400">✓{z.wins}</span>
                                <span className="text-red-400">✗{z.losses}</span>
                                <span className={`font-bold ${wrC}`}>{z.wr_pct}%</span>
                              </div>
                              <div className={`font-bold ${pnlC}`}>
                                {z.pnl_pts > 0 ? "+" : ""}{z.pnl_pts}pts total
                                <span className="text-zinc-600 font-normal ml-1">({z.avg_pnl_pts > 0 ? "+" : ""}{z.avg_pnl_pts}/trade)</span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}
          </div>

          {/* ── Row 3d: Param Audit — Sensibilidade Score×Threshold ── */}
          <div className="border border-teal-500/20 bg-teal-500/5">
            <button
              onClick={() => setShowPa(v => !v)}
              className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-bold text-teal-400/80 uppercase tracking-wider hover:bg-teal-500/10 transition-all">
              <span className="flex items-center gap-2 flex-wrap">
                Param Audit — Sensibilidade Score×Threshold
                {paData && !paLoading && (
                  <span className="font-normal normal-case flex gap-2 flex-wrap">
                    <span className="text-yellow-400" title="Score mínimo MODERATE">M≥{(paData.thresholds?.score_moderate ?? 2.5).toFixed(1)}</span>
                    <span className="text-emerald-400" title="Score mínimo STRONG">S≥{(paData.thresholds?.score_strong ?? 4.0).toFixed(1)}</span>
                    <span className="text-teal-400" title="Base Flow Gate">BFG {(paData.thresholds?.base_flow_gate ?? 1.2).toFixed(1)}</span>
                    <span className="text-teal-700">· {paData.zones?.length ?? 0} zonas · {paData.days}d</span>
                  </span>
                )}
                {paLoading && <span className="ml-2 text-zinc-600 font-normal normal-case">carregando…</span>}
              </span>
              <span className="text-zinc-600">{showPa ? "▲" : "▼"}</span>
            </button>
            {showPa && (() => {
              const zones = paData?.zones || [];
              const thr   = paData?.thresholds || {};
              const mod   = thr.score_moderate ?? 2.5;
              const str   = thr.score_strong   ?? 4.0;

              if (zones.length === 0 && !paLoading) {
                return (
                  <div className="px-4 py-3 text-[10px] text-zinc-600">
                    Sem dados de score ainda — campos preenchidos em snapshots a partir de agora.
                  </div>
                );
              }
              if (paLoading) return <div className="px-4 py-3 text-[10px] text-zinc-600">A carregar…</div>;

              return (
                <div className="px-3 pb-3 pt-1 space-y-2">
                  {/* Legenda */}
                  <div className="flex gap-4 text-[9px] text-zinc-500 flex-wrap pt-1">
                    <span>
                      <span className="inline-block w-2 h-2 bg-red-500/40 mr-1 align-middle" />
                      Bloqueado (score &lt; {mod.toFixed(1)})
                    </span>
                    <span>
                      <span className="inline-block w-2 h-2 bg-yellow-500/60 mr-1 align-middle" />
                      Marginal ({mod.toFixed(1)}–{str.toFixed(1)})
                    </span>
                    <span>
                      <span className="inline-block w-2 h-2 bg-emerald-500/70 mr-1 align-middle" />
                      STRONG (≥{str.toFixed(1)})
                    </span>
                    <span>
                      <span className="inline-block w-2 h-2 bg-emerald-400/80 mr-1 align-middle" />
                      Overlay = % ACTIVE_SIGNAL
                    </span>
                  </div>

                  {zones.map(z => {
                    const buckets = z.score_buckets || [];
                    const maxN    = Math.max(...buckets.map(b => b.n_total), 1);
                    return (
                      <div key={z.zone_type} className="border border-zinc-800/60 bg-zinc-900/40 p-2">
                        {/* Cabeçalho zona */}
                        <div className="flex items-center justify-between mb-1 flex-wrap gap-1">
                          <span className="text-[10px] font-bold text-zinc-300">{z.zone_type}</span>
                          <span className="flex gap-2 text-[9px] flex-wrap">
                            <span className="text-emerald-400" title="Passaram S2 (ACTIVE_SIGNAL)">✓ {z.n_active} activos</span>
                            <span className="text-red-400/70" title="Bloqueados S2">✗ {z.n_blocked} bloqueados</span>
                            {z.marginal_blocked > 0 && (
                              <span className="text-yellow-400/70" title={`Bloqueados com score ≥ MODERATE threshold (${mod.toFixed(1)}) — potencial se threshold baixar`}>
                                ⚠ {z.marginal_blocked} acima MODERATE bloqueados
                              </span>
                            )}
                          </span>
                        </div>

                        {/* Histograma de scores */}
                        {buckets.length > 0 ? (
                          <div className="flex items-end gap-[2px] mt-1" style={{ height: '36px' }}>
                            {buckets.map(b => {
                              const heightPct  = (b.n_total / maxN) * 100;
                              const activeRatio = b.n_total > 0 ? b.n_active / b.n_total : 0;
                              const bgColor    = b.bucket >= str ? "bg-emerald-500/60"
                                              : b.bucket >= mod ? "bg-yellow-500/50"
                                              : "bg-red-500/30";
                              return (
                                <div
                                  key={b.bucket}
                                  className="relative flex flex-col justify-end flex-shrink-0"
                                  style={{ width: '12px', height: '36px' }}
                                  title={`Score ${b.bucket.toFixed(1)} | total ${b.n_total} | activos ${b.n_active} | bloqueados ${b.n_blocked}`}
                                >
                                  <div className={`w-full ${bgColor}`} style={{ height: `${heightPct}%` }} />
                                  {activeRatio > 0 && (
                                    <div
                                      className="absolute bottom-0 w-full bg-emerald-400/80"
                                      style={{ height: `${heightPct * activeRatio}%` }}
                                    />
                                  )}
                                  <div className="absolute -bottom-3 left-0 right-0 text-center text-[6px] text-zinc-600 leading-none">
                                    {b.bucket.toFixed(1)}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <span className="text-[9px] text-zinc-600">sem score_breakdown (legado)</span>
                        )}

                        {/* Score médio linha */}
                        <div className="mt-4 flex gap-3 text-[9px] text-zinc-500 flex-wrap">
                          {z.avg_score_active  != null && <span>Avg activo: <span className="text-emerald-400 font-semibold">{z.avg_score_active.toFixed(2)}</span></span>}
                          {z.avg_score_blocked != null && <span>Avg bloqueado: <span className="text-red-400">{z.avg_score_blocked.toFixed(2)}</span></span>}
                          <span className="text-zinc-700">N total: {z.n_total}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })()}
          </div>

          {/* ── Row 4: Signal List (collapsible) ── */}
          {(data.signals || []).length > 0 && (
            <div className="border border-zinc-800/40 bg-zinc-900/30">
              <button
                onClick={() => setShowSignals(v => !v)}
                className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-bold text-zinc-400 uppercase tracking-wider hover:bg-zinc-800/20 transition-all">
                <span>N2 Sinais Dedup — {data.signals.length} eventos</span>
                <span className="text-zinc-600">{showSignals ? "▲" : "▼"}</span>
              </button>
              {showSignals && (
                <div className="overflow-x-auto border-t border-zinc-800/40">
                  <table className="w-full text-[10px] font-mono">
                    <thead>
                      <tr className="border-b border-zinc-800/40 text-zinc-600">
                        <th className="text-left pb-1 px-2">Hora</th>
                        <th className="text-left pb-1 px-2">Sym</th>
                        <th className="text-left pb-1 px-2">Zona</th>
                        <th className="text-right pb-1 px-2">Nível</th>
                        <th className="text-left pb-1 px-2">Sess</th>
                        <th className="text-left pb-1 px-2">Reg</th>
                        <th className="text-left pb-1 px-2">Qual</th>
                        <th className="text-right pb-1 px-2">Entry</th>
                        <th className="text-right pb-1 px-2">SL</th>
                        <th className="text-right pb-1 px-2">TP</th>
                        <th className="text-left pb-1 px-2">Bench</th>
                        <th className="text-right pb-1 px-2">PnL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.signals.map((s, i) => {
                        const isWin  = s.benchmark_outcome === "TARGET";
                        const isLoss = s.benchmark_outcome === "STOP";
                        const isPend = !isWin && !isLoss;
                        return (
                          <tr key={i} className="border-b border-zinc-800/20 hover:bg-zinc-800/20">
                            <td className="py-0.5 px-2 text-zinc-500 whitespace-nowrap">
                              {s.ts ? new Date(s.ts).toLocaleTimeString("pt-BR") : "—"}
                            </td>
                            <td className="py-0.5 px-2 text-zinc-200">{s.symbol}</td>
                            <td className="py-0.5 px-2 text-zinc-300 whitespace-nowrap">{fmtZone(s.zone_type)}</td>
                            <td className="py-0.5 px-2 text-right text-zinc-400 whitespace-nowrap font-mono">
                              {s.zone_level != null && s.zone_level !== 0 ? s.zone_level.toFixed(2) : "—"}
                            </td>
                            <td className="py-0.5 px-2 text-zinc-500 whitespace-nowrap">{s.session}</td>
                            <td className="py-0.5 px-2 text-zinc-600">{fmtReg(s.regime)}</td>
                            <td className="py-0.5 px-2">
                              <Pill color={s.quality === "STRONG" ? "green" : s.quality === "MODERATE" ? "yellow" : "zinc"}>
                                {s.quality || "—"}
                              </Pill>
                            </td>
                            <td className="py-0.5 px-2 text-right text-zinc-400">{s.entry?.toFixed(2) ?? "—"}</td>
                            <td className="py-0.5 px-2 text-right text-red-400">{s.sl?.toFixed(2) ?? "—"}</td>
                            <td className="py-0.5 px-2 text-right text-emerald-400">{s.tp?.toFixed(2) ?? "—"}</td>
                            <td className="py-0.5 px-2">
                              {isWin  && <Pill color="green">TARGET</Pill>}
                              {isLoss && <Pill color="red">STOP</Pill>}
                              {isPend && <Pill color="zinc">{s.benchmark_outcome || "?"}</Pill>}
                            </td>
                            <td className={`py-0.5 px-2 text-right font-bold ${isWin ? "text-emerald-400" : isLoss ? "text-red-400" : "text-zinc-600"}`}>
                              {s.benchmark_pnl_pts != null ? `${s.benchmark_pnl_pts > 0 ? "+" : ""}${s.benchmark_pnl_pts}` : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {!loading && !data && !error && (
        <div className="border border-zinc-800/40 bg-zinc-900/30 p-8 text-center">
          <span className="text-xs text-zinc-600">Sem dados para {selDate}. Seleccione outro dia ou verifique os snapshots.</span>
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// Main Dashboard
// ══════════════════════════════════════════════════════════════════

export default function ScalpDashboard({ tradingStatus, onFlattenAll }) {
  const [signals, setSignals]         = useState({});
  const [positions, setPositions]     = useState([]);
  const [stats, setStats]             = useState(null);
  const [config, setConfig]           = useState(null);
  const [mode, setMode]               = useState("FLOW");
  const [switchingMode, setSwitchingMode] = useState(false);
  // Auto Trader
  const [atStatus, setAtStatus]       = useState(null);
  const [trades, setTrades]           = useState([]);
  const [tradesTotal, setTradesTotal] = useState(0);
  const [tradeStats, setTradeStats]   = useState(null);
  const [journalSession,  setJournalSession]  = useState("");
  const [journalDateFrom, setJournalDateFrom] = useState("");
  const [journalDateTo,   setJournalDateTo]   = useState("");
  const [journalMode,     setJournalMode]     = useState("");
  const [journalSymbol,   setJournalSymbol]   = useState("");
  const [starting, setStarting]       = useState(false);
  const [stopping, setStopping]       = useState(false);
  // UI
  const [executing, setExecuting]     = useState(null);
  const [closing, setClosing]         = useState(null);
  const [saving, setSaving]           = useState(false);
  const [saveOk, setSaveOk]           = useState(false);
  const [paperSaving, setPaperSaving] = useState(false);
  const [activeTab, setActiveTab]     = useState("signals");
  const [lastRefresh, setLastRefresh] = useState(null);

  // Se o utilizador sair do modo FLOW enquanto está na aba Config, volta a Sinais
  useEffect(() => {
    if (mode !== "FLOW" && activeTab === "config") setActiveTab("signals");
  }, [mode, activeTab]);
  const [error, setError]             = useState(null);
  const pollRef       = useRef(null);
  const atPollRef     = useRef(null);
  const signalFailRef = useRef(0);

  const fetchSignals = useCallback(async () => {
    try {
      const results = await Promise.all(SYMBOLS.map(s => axios.get(`${API}/scalp/signal/${s}`)));
      const updated = {};
      SYMBOLS.forEach((s, i) => { updated[s] = results[i].data; });
      setSignals(updated);
      setLastRefresh(new Date());
      signalFailRef.current = 0;
      setError(prev => (prev && prev.startsWith("Erro ao buscar sinais")) ? null : prev);
    } catch {
      signalFailRef.current += 1;
      if (signalFailRef.current >= 3) setError("Erro ao buscar sinais — backend indisponível");
    }
  }, []);

  const fetchPositions = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/scalp/positions?limit=100`);
      setPositions(r.data.positions || []);
    } catch {}
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/scalp/positions/stats`);
      setStats(r.data);
    } catch {}
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const cr = await axios.get(`${API}/scalp/config`);
      if (cr.data?.config) setConfig(cr.data.config);
    } catch (e) { console.error("[fetchConfig] scalp/config failed:", e?.message); }
    try {
      const mr = await axios.get(`${API}/scalp/mode`);
      if (mr.data?.mode) setMode(mr.data.mode);
    } catch (e) { console.error("[fetchConfig] scalp/mode failed:", e?.message); }
  }, []);

  const fetchAutoTraderStatus = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/scalp/autotrader/status`);
      setAtStatus(r.data);
    } catch {}
  }, []);

  const fetchTrades = useCallback(async (opts = {}) => {
    try {
      const session  = opts.session  !== undefined ? opts.session  : journalSession;
      const dateFrom = opts.dateFrom !== undefined ? opts.dateFrom : journalDateFrom;
      const dateTo   = opts.dateTo   !== undefined ? opts.dateTo   : journalDateTo;
      const mode     = opts.mode     !== undefined ? opts.mode     : journalMode;
      const symbol   = opts.symbol   !== undefined ? opts.symbol   : journalSymbol;
      const params   = new URLSearchParams({ limit: 500 });
      if (session)  params.set("session",   session);
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo)   params.set("date_to",   dateTo);
      if (mode)     params.set("mode",      mode);
      if (symbol)   params.set("symbol",    symbol);
      // stats endpoint receives the same filters as the trades list
      const statsParams = new URLSearchParams();
      if (session)  statsParams.set("session",   session);
      if (dateFrom) statsParams.set("date_from", dateFrom);
      if (dateTo)   statsParams.set("date_to",   dateTo);
      if (mode)     statsParams.set("mode",      mode);
      if (symbol)   statsParams.set("symbol",    symbol);
      const [tr, sr] = await Promise.all([
        axios.get(`${API}/scalp/trades?${params}`),
        axios.get(`${API}/scalp/trades/stats?${statsParams}`),
      ]);
      setTrades(tr.data.trades || []);
      setTradesTotal(tr.data.total ?? 0);
      setTradeStats(sr.data);
    } catch {}
  }, [journalSession, journalDateFrom, journalDateTo, journalMode, journalSymbol]);

  useEffect(() => {
    fetchSignals(); fetchPositions(); fetchStats(); fetchConfig(); fetchAutoTraderStatus(); fetchTrades();
  }, [fetchSignals, fetchPositions, fetchStats, fetchConfig, fetchAutoTraderStatus, fetchTrades]);

  useEffect(() => {
    pollRef.current = setInterval(fetchSignals, POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [fetchSignals]);

  useEffect(() => {
    atPollRef.current = setInterval(() => {
      fetchAutoTraderStatus();
      fetchTrades();
    }, AT_POLL_MS);
    return () => clearInterval(atPollRef.current);
  }, [fetchAutoTraderStatus, fetchTrades]);

  const handleSwitchMode = async (newMode) => {
    if (newMode === mode || switchingMode) return;
    setSwitchingMode(true);
    try { await axios.post(`${API}/scalp/mode/${newMode}`); setMode(newMode); await fetchSignals(); }
    catch (e) { setError(`Erro ao trocar modo: ${e?.response?.data?.detail || e.message}`); }
    finally { setSwitchingMode(false); }
  };

  const handleExecute = async (symbol) => {
    setExecuting(symbol);
    try {
      await axios.post(`${API}/scalp/execute/${symbol}`);
      await Promise.all([fetchPositions(), fetchStats(), fetchSignals(), fetchTrades()]);
    } catch (e) { setError(`Erro ao executar ${symbol}: ${e?.response?.data?.detail || e.message}`); }
    finally { setExecuting(null); }
  };

  const handleClose = async (positionId) => {
    setClosing(positionId);
    try {
      await fetch(`${API}/scalp/close/${positionId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ position_id: positionId, reason: "manual" }) });
      await Promise.all([fetchPositions(), fetchStats()]);
    } catch (e) { setError(`Erro ao fechar: ${e?.response?.data?.detail || e.message}`); }
    finally { setClosing(null); }
  };

  const handleCloseTradeLog = async (tradeId) => {
    setClosing(tradeId);
    try {
      await axios.post(`${API}/scalp/trades/${tradeId}/close?reason=manual`);
      await Promise.all([fetchTrades(), fetchStats(), fetchPositions()]);
    } catch (e) { setError(`Erro ao fechar trade: ${e?.response?.data?.detail || e.message}`); }
    finally { setClosing(null); }
  };

  const handleSaveConfig = async (newConfig) => {
    setSaving(true);
    const fullConfig = { ...(config || {}), ...newConfig };
    if (fullConfig.webhook_url === "" || fullConfig.webhook_url === undefined) fullConfig.webhook_url = null;
    try {
      const token = localStorage.getItem("session_token");
      const r = await fetch("/api/scalp/config/save", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(fullConfig),
      });
      if (!r.ok) {
        const raw = await r.text().catch(() => "");
        let msg = `HTTP ${r.status}`;
        try {
          const err = JSON.parse(raw);
          const detail = err?.detail;
          if (Array.isArray(detail)) {
            const parts = detail.map(d => {
              const loc = Array.isArray(d.loc) ? d.loc.filter(x => x !== "body").join(".") : "";
              return loc ? `${loc}: ${d.msg}` : d.msg;
            }).filter(Boolean);
            if (parts.length) msg = parts.join(" | ");
          } else if (typeof detail === "string" && detail) {
            msg = detail;
          }
        } catch (_) {}
        throw new Error(msg);
      }
      const raw = await r.text().catch(() => "{}");
      const data = JSON.parse(raw);
      if (data?.config) setConfig(data.config);
      await fetchConfig();
      await fetchAutoTraderStatus();
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2500);
    } catch (e) {
      setError(`Erro ao salvar: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handlePaperModeToggle = async (isPaper) => {
    setPaperSaving(true);
    try {
      const token = localStorage.getItem("session_token");
      const r = await fetch("/api/scalp/config/paper-mode", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ paper_trading: isPaper }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${r.status}`);
      }
      const data = await r.json();
      if (data?.config) setConfig(data.config);
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2000);
    } catch (e) {
      setError(`Erro Paper/LIVE: ${e.message}`);
    } finally {
      setPaperSaving(false);
    }
  };

  const handleStartAT = async () => {
    setStarting(true);
    try { await axios.post(`${API}/scalp/autotrader/start`); await fetchAutoTraderStatus(); }
    catch (e) { setError(`Erro ao iniciar AutoTrader: ${e?.response?.data?.detail || e.message}`); }
    finally { setStarting(false); }
  };

  const handleStopAT = async () => {
    setStopping(true);
    try { await axios.post(`${API}/scalp/autotrader/stop`); await fetchAutoTraderStatus(); }
    catch (e) { setError(`Erro ao parar AutoTrader: ${e?.response?.data?.detail || e.message}`); }
    finally { setStopping(false); }
  };

  const handleResetCB = async () => {
    try { await axios.post(`${API}/scalp/autotrader/reset_circuit_breaker`); await fetchAutoTraderStatus(); }
    catch (e) { setError(`Erro ao resetar circuit breaker: ${e?.response?.data?.detail || e.message}`); }
  };

  const openCount    = positions.filter(p => p.state === "OPEN").length;
  const openTrades   = trades.filter(t => t.state === "OPEN").length;
  const atRunning         = atStatus?.status === "RUNNING";
  // Sinal verde só aparece se o loop estiver a correr E pelo menos um modo tiver auto-trade ligado
  const anyModeEnabled    = !!(config?.auto_trade_flow || config?.auto_trade_zones);
  const atEffectiveRunning = atRunning && anyModeEnabled;

  return (
    <div className="flex flex-col gap-4 p-4 max-w-[1920px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Lightning size={18} weight="fill" className="text-yellow-400" />
          <h2 className="text-sm font-bold text-zinc-200 uppercase tracking-wider">Scalp Engine</h2>
          <span className="text-[10px] text-zinc-600 font-mono">S1 / S2 / S3</span>
          {atEffectiveRunning && <span className="px-2 py-0.5 text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center gap-1"><Robot size={10} weight="fill" />AUTO</span>}
          {openTrades > 0 && <span className="px-2 py-0.5 text-[10px] font-bold bg-blue-500/20 text-blue-400 border border-blue-500/30">{openTrades} OPEN</span>}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-zinc-600 font-mono">MODO:</span>
          <div className="flex border border-zinc-700/60 overflow-hidden">
            <button onClick={() => handleSwitchMode("FLOW")} disabled={switchingMode}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold transition-all border-r border-zinc-700/60 ${mode === "FLOW" ? "bg-blue-500/20 text-blue-400" : "text-zinc-500 hover:text-zinc-300"} disabled:opacity-50`}>
              <Pulse size={12} />Fluxo
            </button>
            <button onClick={() => handleSwitchMode("ZONES")} disabled={switchingMode}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold transition-all ${mode === "ZONES" ? "bg-amber-500/20 text-amber-400" : "text-zinc-500 hover:text-zinc-300"} disabled:opacity-50`}>
              <ChartBar size={12} />Zonas
            </button>
          </div>
          {lastRefresh && <span className="text-[10px] text-zinc-600 font-mono">{lastRefresh.toLocaleTimeString("pt-BR")}</span>}
          <button onClick={() => { fetchSignals(); fetchPositions(); fetchTrades(); fetchAutoTraderStatus(); }}
            className="flex items-center gap-1 px-2 py-1 text-[10px] text-zinc-400 border border-zinc-800 hover:border-zinc-600 transition-all">
            <ArrowsCounterClockwise size={11} />
          </button>
        </div>
      </div>

      {/* Modo description */}
      <div className={`px-3 py-2 border text-[10px] font-mono ${
        mode === "ZONES" ? "border-amber-500/20 bg-amber-500/5 text-amber-400/80"
        : "border-blue-500/20 bg-blue-500/5 text-blue-400/80"
      }`}>
        {mode === "ZONES"
          ? "Modo Zonas — Regime + VWAP/VP D-1/VP Sessão/ONH-ONL como zonas de interesse. SL/TP/BE adaptativos por regime. OFI/Delta como confirmação de entrada na zona."
          : "Modo Fluxo — OFI fast/slow (500/2000 trades) + CVD + absorção. SL/TP fixos em ticks. Mercado ativo com fluxo direcional."}
      </div>

      {/* Status Bar — feed, sessão, engine, auto-trader */}
      <ScalpStatusBar />

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 px-3 py-2">
          <Warning size={14} className="text-red-400" />
          <span className="text-xs text-red-400">{error}</span>
          <button onClick={() => setError(null)} className="ml-auto text-zinc-500 text-xs">✕</button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-zinc-800/40">
        {[
          ["signals",  "Sinais",                    <Gauge size={12} />],
          ["auto",     `Auto${atEffectiveRunning ? " 🟢" : ""}`, <Robot size={12} />],
          ["trades",   `Journal (${openTrades})`,   <ChartBar size={12} />],
          ["positions",`Posições (${openCount})`,   <PaperPlaneTilt size={12} />],
          ["funnel",   "Funil",                     <Funnel size={12} />],
          ...(mode === "FLOW" ? [["config", "Config", <Lightning size={12} />]] : []),
        ].map(([key, label, icon]) => (
          <button key={key} onClick={() => setActiveTab(key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs font-semibold transition-all border-b-2 ${activeTab === key ? "border-blue-400 text-blue-400" : "border-transparent text-zinc-500 hover:text-zinc-300"}`}>
            {icon}{label}
          </button>
        ))}
      </div>

      {/* ── Tab: Sinais ── */}
      {activeTab === "signals" && (
        <div className="flex flex-col gap-4">
          {stats && (
            <div className="grid grid-cols-4 gap-3">
              {[["Trades", stats.total_trades ?? "—"], ["Win Rate", stats.win_rate != null ? `${stats.win_rate}%` : "—"], ["PnL médio", stats.avg_pnl_pts != null ? `${stats.avg_pnl_pts > 0 ? "+" : ""}${stats.avg_pnl_pts} pts` : "—"], ["Abertas", stats.open_positions ?? 0]].map(([label, value]) => (
                <div key={label} className="border border-zinc-800/40 bg-zinc-900/30 px-3 py-2 flex flex-col gap-0.5">
                  <span className="text-[10px] text-zinc-500">{label}</span>
                  <span className="text-sm font-mono font-bold text-zinc-200">{value}</span>
                </div>
              ))}
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {SYMBOLS.map(sym => (
              <SignalCard key={sym} symbol={sym} signal={signals[sym]} onExecute={handleExecute} executing={executing === sym} config={config} mode={mode} />
            ))}
          </div>
        </div>
      )}

      {/* ── Tab: Auto Trading ── */}
      {activeTab === "auto" && (
        <AutoTraderPanel
          atStatus={atStatus}
          config={config}
          onStart={handleStartAT}
          onStop={handleStopAT}
          onSaveConfig={handleSaveConfig}
          onPaperToggle={handlePaperModeToggle}
          onResetCB={handleResetCB}
          starting={starting}
          stopping={stopping}
          saving={saving}
          saveOk={saveOk}
          paperSaving={paperSaving}
          tradingStatus={tradingStatus}
          signals={signals}
          onFlattenAll={onFlattenAll}
          mode={mode}
        />
      )}

      {/* ── Tab: Trades (log unificado) ── */}
      {activeTab === "trades" && (
        <TradeLogTable
          trades={trades}
          tradesTotal={tradesTotal}
          tradeStats={tradeStats}
          onClose={handleCloseTradeLog}
          closing={closing}
          onRefresh={fetchTrades}
          journalSession={journalSession}
          journalDateFrom={journalDateFrom}
          journalDateTo={journalDateTo}
          journalMode={journalMode}
          journalSymbol={journalSymbol}
          onSessionChange={v  => { setJournalSession(v);  fetchTrades({ session: v,  dateFrom: journalDateFrom, dateTo: journalDateTo, mode: journalMode, symbol: journalSymbol }); }}
          onDateFromChange={v => { setJournalDateFrom(v); fetchTrades({ session: journalSession, dateFrom: v, dateTo: journalDateTo, mode: journalMode, symbol: journalSymbol }); }}
          onDateToChange={v   => { setJournalDateTo(v);   fetchTrades({ session: journalSession, dateFrom: journalDateFrom, dateTo: v, mode: journalMode, symbol: journalSymbol }); }}
          onModeChange={v     => { setJournalMode(v);     fetchTrades({ session: journalSession, dateFrom: journalDateFrom, dateTo: journalDateTo, mode: v, symbol: journalSymbol }); }}
          onSymbolChange={v   => { setJournalSymbol(v);   fetchTrades({ session: journalSession, dateFrom: journalDateFrom, dateTo: journalDateTo, mode: journalMode, symbol: v }); }}
        />
      )}

      {/* ── Tab: Posições (legacy) ── */}
      {activeTab === "positions" && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-zinc-400">{positions.length} posição(ões)</span>
            <button onClick={() => { fetchPositions(); fetchStats(); }}
              className="flex items-center gap-1 px-2 py-1 text-[10px] text-zinc-400 border border-zinc-800 hover:border-zinc-600">
              <ArrowsCounterClockwise size={11} /> Atualizar
            </button>
          </div>
          {positions.length === 0 ? (
            <div className="border border-zinc-800/40 bg-zinc-900/30 p-6 text-center">
              <span className="text-xs text-zinc-600">Nenhuma posição registrada.</span>
            </div>
          ) : (
            <div className="border border-zinc-800/40 overflow-x-auto">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="border-b border-zinc-800/40 text-zinc-500 text-[10px]">
                    <th className="text-left px-3 py-2">Hora</th><th className="text-left px-3 py-2">Sym</th>
                    <th className="text-left px-3 py-2">Dir</th><th className="text-right px-3 py-2">Entrada</th>
                    <th className="text-right px-3 py-2">SL</th><th className="text-right px-3 py-2">TP</th>
                    <th className="text-left px-3 py-2">Estado</th><th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map(pos => (
                    <tr key={pos.id} className="border-b border-zinc-800/30 hover:bg-zinc-800/20">
                      <td className="px-3 py-2 text-zinc-500 text-[10px]">{pos.created_at ? new Date(pos.created_at).toLocaleTimeString("pt-BR") : "—"}</td>
                      <td className="px-3 py-2 font-bold text-zinc-200">{pos.symbol}</td>
                      <td className="px-3 py-2">{pos.action === "buy" ? <ArrowUp size={12} className="text-emerald-400" weight="bold" /> : <ArrowDown size={12} className="text-red-400" weight="bold" />}</td>
                      <td className="px-3 py-2 text-right text-zinc-300">{pos.entry_price?.toFixed(2)}</td>
                      <td className="px-3 py-2 text-right text-red-400">{pos.stop_loss_price?.toFixed(2)}</td>
                      <td className="px-3 py-2 text-right text-emerald-400">{pos.take_profit_price?.toFixed(2)}</td>
                      <td className="px-3 py-2"><span className={`text-[10px] font-bold ${pos.state === "OPEN" ? "text-emerald-400" : "text-zinc-500"}`}>{pos.state}</span></td>
                      <td className="px-3 py-2">{pos.state === "OPEN" && <button onClick={() => handleClose(pos.id)} disabled={closing === pos.id} className="text-[10px] text-red-400 border border-red-500/30 px-2 py-0.5 hover:border-red-500/50 transition-all disabled:opacity-40">{closing === pos.id ? "..." : "Fechar"}</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Tab: Funil ── */}
      {activeTab === "funnel" && <FunnelPanel />}

      {/* ── Tab: Config ── */}
      {activeTab === "config" && (
        <ConfigPanel config={config} onSave={handleSaveConfig} saving={saving} />
      )}
    </div>
  );
}
