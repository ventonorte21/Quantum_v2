import { useState, useEffect, useCallback } from "react";
import {
  ArrowLeft, ListBullets, BellRinging,
  CheckCircle, XCircle, CaretDown, CaretUp, Funnel,
  ArrowCounterClockwise, DownloadSimple, ChartBar, Target, Warning
} from "@phosphor-icons/react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { ScrollArea } from "../components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Switch } from "../components/ui/switch";
import { Label } from "../components/ui/label";
import { Separator } from "../components/ui/separator";

const API = `${process.env.REACT_APP_BACKEND_URL || ""}/api`;

// ── Stat Card ──
function StatCard({ label, value, sub, color = "text-zinc-100" }) {
  return (
    <div className="bg-zinc-800/60 border border-zinc-700/50 rounded-lg p-3 text-center" data-testid={`stat-${label.toLowerCase().replace(/\s/g, '-')}`}>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">{label}</div>
      <div className={`text-lg font-bold ${color}`}>{value}</div>
      {sub && <div className="text-[10px] text-zinc-500 mt-0.5">{sub}</div>}
    </div>
  );
}

// ── Position Row ──
function PositionRow({ pos, expanded, onToggle }) {
  const side = pos.side || "?";
  const isBuy = side === "BUY" || side === "LONG";
  const state = pos.state || "?";
  const isPaper = pos.paper === true;
  const stateColor = {
    OPEN: "text-emerald-400 border-emerald-700",
    MANAGING: "text-amber-400 border-amber-700",
    BREAK_EVEN: "text-cyan-400 border-cyan-700",
    CLOSED: "text-zinc-500 border-zinc-700",
  }[state] || "text-zinc-500 border-zinc-700";

  const pnl = pos.realized_pnl || 0;
  const fmtDate = (iso) => {
    if (!iso) return "-";
    try { return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }); }
    catch { return "-"; }
  };

  return (
    <div className={`border-b border-zinc-800/50 ${isPaper ? "bg-indigo-950/10" : ""}`} data-testid={`position-row-${pos.id}`}>
      <button
        onClick={onToggle}
        className="w-full text-left px-3 py-2.5 hover:bg-zinc-800/30 transition flex items-center gap-3 text-xs"
      >
        <div className="w-5 text-zinc-600">
          {expanded ? <CaretUp size={12} /> : <CaretDown size={12} />}
        </div>
        {isPaper && (
          <Badge variant="outline" className="text-[9px] px-1 py-0 border-indigo-700 text-indigo-400">
            PAPER
          </Badge>
        )}
        <Badge variant="outline" className={`${isBuy ? "border-emerald-700 text-emerald-400" : "border-red-700 text-red-400"} text-[10px] w-10 justify-center`}>
          {side}
        </Badge>
        <span className="font-mono text-zinc-300 w-10">{pos.symbol}</span>
        <span className="font-mono text-zinc-400 w-8 text-right">x{pos.quantity || 1}</span>
        <Badge variant="outline" className={`${stateColor} text-[9px]`}>{state}</Badge>
        <span className="text-zinc-500 w-14 text-right">{pos.archetype || "-"}</span>
        <span className="text-zinc-500 w-14">{pos.regime || "-"}</span>
        <span className="font-mono text-zinc-400 w-20 text-right">{pos.entry_price?.toFixed(2) || "-"}</span>
        <span className="font-mono text-zinc-400 w-20 text-right">{pos.exit_price?.toFixed(2) || "-"}</span>
        <span className={`font-mono font-semibold w-20 text-right ${pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
          {state === "CLOSED" ? `$${pnl.toFixed(2)}` : "-"}
        </span>
        <span className="text-zinc-600 text-[10px] ml-auto">{fmtDate(pos.opened_at)}</span>
      </button>

      {expanded && (
        <div className="px-8 pb-3 space-y-2">
          <div className="grid grid-cols-4 gap-2 text-[10px]">
            <div>
              <span className="text-zinc-600">Hard Stop: </span>
              <span className="text-red-400 font-mono">{pos.hard_stop?.toFixed(2) || "-"}</span>
            </div>
            <div>
              <span className="text-zinc-600">Take Profit: </span>
              <span className="text-emerald-400 font-mono">{pos.take_profit?.toFixed(2) || "-"}</span>
            </div>
            <div>
              <span className="text-zinc-600">Current Stop: </span>
              <span className="text-amber-400 font-mono">{pos.current_stop?.toFixed(2) || pos.hard_stop?.toFixed(2) || "-"}</span>
            </div>
            <div>
              <span className="text-zinc-600">Close Reason: </span>
              <span className="text-zinc-300">{pos.close_reason || "-"}</span>
            </div>
          </div>

          {pos.events && pos.events.length > 0 && (
            <div className="mt-2">
              <div className="text-[10px] text-zinc-600 uppercase tracking-wider mb-1">Timeline</div>
              <div className="space-y-1 max-h-[120px] overflow-y-auto">
                {pos.events.map((ev, i) => {
                  const evColor = {
                    OPENED: "text-emerald-500", SS_OCO_SENT: "text-cyan-500",
                    FILL_CONFIRMED: "text-emerald-400", STOP_UPDATED: "text-amber-500",
                    CLOSED: "text-red-400", SS_ERROR: "text-red-500",
                  }[ev.type] || "text-zinc-500";
                  return (
                    <div key={`ev-${ev.ts}-${ev.type}-${i}`} className="flex items-start gap-2 text-[10px]">
                      <span className="text-zinc-700 font-mono w-28 shrink-0">
                        {ev.ts ? new Date(ev.ts).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "-"}
                      </span>
                      <span className={`font-semibold w-32 shrink-0 ${evColor}`}>{ev.type}</span>
                      <span className="text-zinc-500 truncate">{ev.detail}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {pos.orders && pos.orders.length > 0 && (
            <div className="mt-2">
              <div className="text-[10px] text-zinc-600 uppercase tracking-wider mb-1">SignalStack Orders</div>
              <div className="space-y-1">
                {pos.orders.map((o, i) => (
                  <div key={o.order_id || `ord-${o.action}-${o.symbol}-${i}`} className="flex items-center gap-3 text-[10px] bg-zinc-800/40 rounded px-2 py-1">
                    <Badge variant="outline" className={`text-[9px] ${o.status === "success" ? "border-emerald-700 text-emerald-400" : "border-red-700 text-red-400"}`}>
                      {o.status}
                    </Badge>
                    <span className="text-zinc-400">{o.action}</span>
                    <span className="font-mono text-zinc-300">{o.symbol}</span>
                    <span className="text-zinc-500">x{o.quantity}</span>
                    {o.stop_price && <span className="text-zinc-500">SL:{o.stop_price}</span>}
                    {o.limit_price && <span className="text-zinc-500">Limit:{o.limit_price}</span>}
                    <span className="text-zinc-700 ml-auto font-mono">{o.sent_at?.substring(11, 19) || ""}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── helpers ──
function computeStop(sig) {
  const atr = sig.atr_m5 || 0;
  const buffer = Math.max(1.5 * atr, 2.0);
  if (sig.direction === "SHORT" && sig.vah > 0) return +(sig.vah + buffer).toFixed(2);
  if (sig.direction === "LONG"  && sig.val > 0) return +(sig.val - buffer).toFixed(2);
  return null;
}

// ── Signal Log Row ──
function SignalLogRow({ sig }) {
  const isShort = sig.direction === "SHORT";
  const outcome = sig.outcome;
  const pnl = sig.outcome_pnl_pts;
  const n3ok = sig.n3_executed === true;

  // Entry and stop: prefer N3 actual values when confirmed, else use logged/computed
  const entryPrice   = n3ok && sig.n3_actual_entry  ? sig.n3_actual_entry  : sig.price;
  const stopPrice    = n3ok && sig.n3_actual_stop    ? sig.n3_actual_stop   : computeStop(sig);
  const targetPrice  = n3ok && sig.n3_actual_target  ? sig.n3_actual_target : sig.target_price;

  // Risk/Reward ratio
  let rrRatio = null;
  if (entryPrice && stopPrice && targetPrice) {
    const risk   = Math.abs(entryPrice - stopPrice);
    const reward = Math.abs(targetPrice - entryPrice);
    if (risk > 0) rrRatio = (reward / risk).toFixed(1);
  }

  const outcomeStyle = {
    TARGET_HIT:   { color: "text-emerald-400", border: "border-emerald-700",  label: "TARGET"      },
    STOP_HIT:     { color: "text-red-400",     border: "border-red-700",      label: "STOP"        },
    EXPIRED:      { color: "text-zinc-500",     border: "border-zinc-700",     label: "EXPIRADO"    },
    BREAKEVEN:    { color: "text-cyan-400",     border: "border-cyan-700",     label: "BE"          },
    MANUAL_CLOSE: { color: "text-amber-400",    border: "border-amber-700",    label: "MANUAL"      },
    NOT_TRADED:   { color: "text-violet-400",   border: "border-violet-700",   label: "N3 NÃO CONF" },
  }[outcome] || { color: "text-amber-400", border: "border-amber-700/50", label: "PENDENTE" };

  const iqColor = {
    DEFERRED_TO_N3:       "text-violet-400",
    STRUCTURAL_REJECTION: "text-cyan-400",
    ANTI_FADE_BLOCKED:    "text-orange-400",
    NO_REJECTION:         "text-zinc-500",
    NAO_CONFIRMADO:       "text-zinc-500",
    SEM_DADOS:            "text-zinc-500",
    UNKNOWN:              "text-zinc-500",
  }[sig.n2_interaction_quality] || "text-zinc-400";

  const fmtTime = (iso) => {
    if (!iso) return "-";
    try {
      return new Date(iso).toLocaleString("pt-BR", {
        day: "2-digit", month: "2-digit",
        hour: "2-digit", minute: "2-digit"
      });
    } catch { return "-"; }
  };

  return (
    <div className={`border-b border-zinc-800/50 px-3 py-2 hover:bg-zinc-800/20 transition flex items-center gap-2 text-[11px] ${n3ok ? "bg-emerald-950/10" : ""}`}>
      <span className="text-zinc-600 font-mono w-24 shrink-0">{fmtTime(sig.timestamp)}</span>

      {/* N3 confirmation badge */}
      <div className="w-14 shrink-0">
        {n3ok ? (
          <Badge className={`text-[8px] px-1 py-0 ${sig.n3_paper ? "bg-indigo-900/40 text-indigo-300 border-indigo-700" : "bg-emerald-900/40 text-emerald-300 border-emerald-700"}`}>
            N3 {sig.n3_paper ? "SIM" : "LIVE"}
          </Badge>
        ) : (
          <Badge variant="outline" className="text-[8px] px-1 py-0 border-zinc-700 text-zinc-600">
            SIM
          </Badge>
        )}
      </div>

      <span className="font-mono text-zinc-300 w-8 shrink-0">{sig.symbol}</span>

      <Badge variant="outline" className={`${isShort ? "border-red-700 text-red-400" : "border-emerald-700 text-emerald-400"} text-[9px] w-10 justify-center shrink-0`}>
        {sig.direction || "?"}
      </Badge>

      <span className="text-zinc-500 w-20 shrink-0 truncate">{sig.n1_regime || "-"}</span>

      <span className="text-zinc-600 w-10 shrink-0 font-mono text-[10px]">{(sig.n2_border || "-").toUpperCase()}</span>

      <span className="font-mono text-zinc-400 w-8 text-right shrink-0">
        {sig.z_score != null ? sig.z_score.toFixed(1) : "-"}
      </span>

      <span className="font-mono text-zinc-500 w-10 text-right shrink-0">
        {sig.delta_ratio != null ? sig.delta_ratio.toFixed(3) : "-"}
      </span>

      {/* Entry */}
      <span className="font-mono text-zinc-300 w-16 text-right shrink-0">
        {entryPrice?.toFixed(2) || "-"}
      </span>

      {/* Stop — red */}
      <span className="font-mono text-red-400/70 w-16 text-right shrink-0">
        {stopPrice?.toFixed(2) || "-"}
      </span>

      {/* Target — green */}
      <span className="font-mono text-emerald-400/70 w-16 text-right shrink-0">
        {targetPrice?.toFixed(2) || "-"}
      </span>

      {/* R:R */}
      <span className="font-mono text-zinc-600 w-10 text-right shrink-0">
        {rrRatio ? `${rrRatio}R` : "-"}
      </span>

      <div className="w-20 shrink-0 flex justify-center">
        <Badge variant="outline" className={`${outcomeStyle.border} ${outcomeStyle.color} text-[9px]`}>
          {outcomeStyle.label}
        </Badge>
      </div>

      <span className={`font-mono font-semibold w-14 text-right shrink-0 ${pnl == null ? "text-zinc-600" : pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
        {pnl != null ? `${pnl > 0 ? "+" : ""}${pnl.toFixed(1)}pts` : "-"}
      </span>
    </div>
  );
}

// ── Telegram Config Panel ──
function TelegramPanel() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch(`${API}/fills/telegram/status`, { credentials: "include" });
      if (resp.ok) setStatus(await resp.json());
    } catch (e) { console.error("Telegram status error", e); }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const toggleAlerts = async (enabled) => {
    try {
      await fetch(`${API}/fills/telegram/toggle?enabled=${enabled}`, { method: "POST", credentials: "include" });
      fetchStatus();
    } catch (e) { console.error("Toggle error", e); }
  };

  const testTelegram = async () => {
    setLoading(true);
    setTestResult(null);
    try {
      const resp = await fetch(`${API}/fills/telegram/test`, { method: "POST", credentials: "include" });
      const data = await resp.json();
      setTestResult(data);
    } catch (e) { setTestResult({ status: "error", error: String(e) }); }
    setLoading(false);
  };

  if (!status) return <div className="text-zinc-600 text-xs p-4">Carregando...</div>;

  return (
    <div className="space-y-4 p-1" data-testid="telegram-panel">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {status.configured ? (
            <CheckCircle size={16} className="text-emerald-400" weight="fill" />
          ) : (
            <XCircle size={16} className="text-red-400" weight="fill" />
          )}
          <span className="text-xs text-zinc-300">
            {status.configured ? "Bot configurado" : "Bot nao configurado"}
          </span>
        </div>
      </div>

      <div className="flex items-center justify-between bg-zinc-800/40 rounded-lg p-3">
        <div>
          <Label className="text-xs text-zinc-300">Alertas Telegram</Label>
          <div className="text-[10px] text-zinc-600 mt-0.5">Receber notificacoes de trades</div>
        </div>
        <Switch
          data-testid="telegram-toggle"
          checked={status.enabled}
          onCheckedChange={toggleAlerts}
          disabled={!status.configured}
        />
      </div>

      <Button
        data-testid="telegram-test-btn"
        variant="outline"
        size="sm"
        className="w-full text-xs border-zinc-700 text-zinc-300 hover:bg-zinc-800"
        onClick={testTelegram}
        disabled={!status.configured || loading}
      >
        {loading ? "Enviando..." : "Enviar Mensagem de Teste"}
      </Button>

      {testResult && (
        <div className={`text-[10px] p-2 rounded ${testResult.status === "sent" ? "bg-emerald-900/30 text-emerald-400" : "bg-red-900/30 text-red-400"}`}>
          {testResult.status === "sent" ? "Mensagem enviada com sucesso!" : `Erro: ${testResult.result?.error || "desconhecido"}`}
        </div>
      )}

      <Separator className="bg-zinc-800" />

      <div className="text-[10px] text-zinc-600 space-y-1">
        <div>Bot Token: {status.bot_token_set ? <span className="text-emerald-500">configurado</span> : <span className="text-red-400">ausente</span>}</div>
        <div>Chat ID: {status.chat_id_set ? <span className="text-emerald-500">configurado</span> : <span className="text-red-400">ausente</span>}</div>
      </div>
    </div>
  );
}

// ── Main Component ──
export default function FillMonitor({ onBack }) {
  // Positions state
  const [positions, setPositions] = useState([]);
  const [stats, setStats] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  // Signal log state
  const [logSignals, setLogSignals] = useState([]);
  const [logStats, setLogStats] = useState(null);
  const [filterLogOutcome, setFilterLogOutcome] = useState("all");
  const [filterLogRegime, setFilterLogRegime] = useState("all");
  const [filterN3, setFilterN3] = useState("all");

  // Shared state
  const [activeTab, setActiveTab] = useState("journal");
  const [filterSymbol, setFilterSymbol] = useState("all");
  const [filterStatus, setFilterStatus] = useState("all");
  const [filterDays, setFilterDays] = useState("30");
  const [filterTradeType, setFilterTradeType] = useState("all");
  const [loading, setLoading] = useState(false);

  const isLogView = filterTradeType === "log";

  // ── Fetch positions ──
  const fetchJournal = useCallback(async () => {
    if (isLogView) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (filterSymbol !== "all") params.set("symbol", filterSymbol);
      if (filterStatus !== "all") params.set("status", filterStatus);
      if (filterDays !== "all") params.set("days", filterDays);
      if (filterTradeType !== "all") params.set("trade_type", filterTradeType);
      const resp = await fetch(`${API}/fills/journal?${params}`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setPositions(data.positions || []);
      }
    } catch (e) { console.error("Journal fetch error", e); }
    setLoading(false);
  }, [filterSymbol, filterStatus, filterDays, filterTradeType, isLogView]);

  const fetchStats = useCallback(async () => {
    if (isLogView) return;
    try {
      const days = filterDays !== "all" ? filterDays : "30";
      const params = new URLSearchParams({ days });
      if (filterTradeType !== "all") params.set("trade_type", filterTradeType);
      const resp = await fetch(`${API}/fills/stats?${params}`, { credentials: "include" });
      if (resp.ok) setStats(await resp.json());
    } catch (e) { console.error("Stats fetch error", e); }
  }, [filterDays, filterTradeType, isLogView]);

  // ── Fetch signal log ──
  const fetchSignalLog = useCallback(async () => {
    if (!isLogView) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "200" });
      if (filterSymbol !== "all") params.set("symbol", filterSymbol);
      if (filterLogOutcome !== "all") params.set("outcome", filterLogOutcome);
      if (filterLogRegime !== "all") params.set("regime", filterLogRegime);
      if (filterN3 !== "all") params.set("n3_filter", filterN3);
      const resp = await fetch(`${API}/signals/log?${params}`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setLogSignals(data.signals || []);
      }
    } catch (e) { console.error("Signal log fetch error", e); }
    setLoading(false);
  }, [isLogView, filterSymbol, filterLogOutcome, filterLogRegime, filterN3]);

  const fetchLogStats = useCallback(async () => {
    if (!isLogView) return;
    try {
      const params = new URLSearchParams();
      if (filterSymbol !== "all") params.set("symbol", filterSymbol);
      const resp = await fetch(`${API}/signals/stats?${params}`, { credentials: "include" });
      if (resp.ok) setLogStats(await resp.json());
    } catch (e) { console.error("Log stats fetch error", e); }
  }, [isLogView, filterSymbol]);

  useEffect(() => {
    if (isLogView) {
      fetchSignalLog();
      fetchLogStats();
    } else {
      fetchJournal();
      fetchStats();
    }
  }, [isLogView, fetchJournal, fetchStats, fetchSignalLog, fetchLogStats]);

  const s = stats || {};
  const closedCount = positions.filter(p => p.state === "CLOSED").length;
  const activeCount = positions.filter(p => p.state !== "CLOSED").length;
  const ls = logStats || {};

  return (
    <div className="min-h-screen bg-[#09090B] text-zinc-100" data-testid="fill-monitor">
      {/* Header */}
      <div className="sticky top-0 z-20 bg-[#09090B]/95 backdrop-blur border-b border-zinc-800 px-4 py-2.5">
        <div className="flex items-center justify-between max-w-[1600px] mx-auto">
          <div className="flex items-center gap-3">
            <button onClick={onBack} className="text-zinc-500 hover:text-zinc-300 transition" data-testid="fills-back-btn">
              <ArrowLeft size={18} />
            </button>
            {isLogView
              ? <ChartBar size={20} className="text-violet-400" />
              : <ListBullets size={20} className="text-cyan-400" />
            }
            <span className="font-semibold text-sm tracking-tight">
              {isLogView ? "Signal Log" : "Trade Journal"}
            </span>
            <Badge variant="outline" className={`text-[10px] ml-2 ${isLogView ? "border-violet-800 text-violet-400" : "border-cyan-800 text-cyan-400"}`}>
              {isLogView ? "OutcomeTracker" : "Fill Monitor"}
            </Badge>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <Button
              variant="ghost"
              size="sm"
              className="text-zinc-500 hover:text-zinc-300 h-7 px-2"
              onClick={() => isLogView ? (fetchSignalLog(), fetchLogStats()) : (fetchJournal(), fetchStats())}
              data-testid="refresh-btn"
            >
              <ArrowCounterClockwise size={14} className="mr-1" />
              Refresh
            </Button>
            {!isLogView && (() => {
              const params = new URLSearchParams();
              if (filterSymbol !== "all") params.set("symbol", filterSymbol);
              if (filterStatus !== "all") params.set("status", filterStatus);
              if (filterDays !== "all") params.set("days", filterDays);
              if (filterTradeType !== "all") params.set("trade_type", filterTradeType);
              const qs = params.toString() ? `&${params.toString()}` : "";
              return (
                <>
                  <a href={`${API}/fills/export?format=csv${qs}`} download
                    className="px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition text-[10px] flex items-center gap-1 no-underline border border-zinc-700"
                    data-testid="export-csv-btn">
                    <DownloadSimple size={12} /> CSV
                  </a>
                  <a href={`${API}/fills/export?format=xlsx${qs}`} download
                    className="px-2 py-1 rounded bg-emerald-900/40 hover:bg-emerald-900/60 text-emerald-400 hover:text-emerald-300 transition text-[10px] flex items-center gap-1 no-underline border border-emerald-800/50"
                    data-testid="export-xlsx-btn">
                    <DownloadSimple size={12} /> Excel
                  </a>
                </>
              );
            })()}
            {!isLogView && activeCount > 0 && (
              <Badge className="bg-emerald-500/20 text-emerald-400 border-emerald-600 text-[10px]">
                {activeCount} ativa{activeCount > 1 ? "s" : ""}
              </Badge>
            )}
            {isLogView && (
              <Badge className="bg-violet-500/20 text-violet-400 border-violet-600 text-[10px]">
                {logSignals.length} sinais
              </Badge>
            )}
          </div>
        </div>
      </div>

      <div className="max-w-[1600px] mx-auto p-4">
        {/* Stats Row */}
        {!isLogView ? (
          <div className="grid grid-cols-4 md:grid-cols-8 gap-2 mb-4" data-testid="stats-grid">
            <StatCard label="Trades" value={s.total_trades || 0} />
            <StatCard label="Win Rate" value={`${s.win_rate || 0}%`} color={(s.win_rate || 0) >= 50 ? "text-emerald-400" : "text-amber-400"} />
            <StatCard label="P&L Total" value={`$${(s.total_pnl || 0).toFixed(0)}`} color={(s.total_pnl || 0) >= 0 ? "text-emerald-400" : "text-red-400"} />
            <StatCard label="Vitorias" value={s.winners || 0} color="text-emerald-400" />
            <StatCard label="Derrotas" value={s.losers || 0} color="text-red-400" />
            <StatCard label="Avg Win" value={`$${(s.avg_winner || 0).toFixed(0)}`} color="text-emerald-400" />
            <StatCard label="Avg Loss" value={`$${(s.avg_loser || 0).toFixed(0)}`} color="text-red-400" />
            <StatCard label="SS Orders" value={s.signalstack_orders_total || 0} sub={`${s.signalstack_orders_period || 0} periodo`} />
          </div>
        ) : (
          <div className="grid grid-cols-4 md:grid-cols-8 gap-2 mb-4" data-testid="log-stats-grid">
            <StatCard label="Sinais" value={ls.total_signals || 0} />
            <StatCard label="Resolvidos" value={ls.resolved || 0} color="text-zinc-300" />
            <StatCard
              label="Pendentes"
              value={ls.pending || 0}
              color={(ls.pending || 0) > 0 ? "text-amber-400" : "text-zinc-500"}
            />
            <StatCard
              label="Win Rate"
              value={ls.total_signals > 0 ? `${ls.win_rate_pct ?? 0}%` : "-"}
              color={(ls.win_rate_pct || 0) >= 50 ? "text-emerald-400" : "text-amber-400"}
            />
            <StatCard label="Wins" value={ls.wins || 0} color="text-emerald-400" />
            <StatCard label="Losses" value={ls.losses || 0} color="text-red-400" />
            <StatCard
              label="Avg PnL"
              value={ls.avg_pnl_pts != null ? `${ls.avg_pnl_pts > 0 ? "+" : ""}${ls.avg_pnl_pts?.toFixed(1)}pts` : "-"}
              color={(ls.avg_pnl_pts || 0) >= 0 ? "text-emerald-400" : "text-red-400"}
            />
            <StatCard
              label="Cobertura"
              value={ls.total_signals > 0 ? `${Math.round(((ls.resolved || 0) / ls.total_signals) * 100)}%` : "-"}
              sub="resolvidos"
              color="text-zinc-300"
            />
          </div>
        )}

        <div className="grid grid-cols-12 gap-4">
          {/* Left Panel: Filters + Telegram */}
          <div className="col-span-12 lg:col-span-3">
            <Tabs value={activeTab} onValueChange={setActiveTab}>
              <TabsList className="w-full bg-zinc-800/50 mb-3 h-auto gap-0.5 p-0.5">
                <TabsTrigger value="journal" className="flex-1 text-[10px] data-[state=active]:bg-zinc-700 h-7">
                  <Funnel size={11} className="mr-1" />Filtros
                </TabsTrigger>
                <TabsTrigger value="telegram" className="flex-1 text-[10px] data-[state=active]:bg-cyan-900/60 h-7">
                  <BellRinging size={11} className="mr-1" />Telegram
                </TabsTrigger>
              </TabsList>

              <TabsContent value="journal">
                <Card className="bg-zinc-900/80 border-zinc-800">
                  <CardContent className="p-3 space-y-3">
                    {/* Tipo filter — Real / Paper / Log */}
                    <div>
                      <Label className="text-[10px] text-zinc-500">Tipo</Label>
                      <Select value={filterTradeType} onValueChange={(v) => { setFilterTradeType(v); setFilterLogOutcome("all"); setFilterLogRegime("all"); setFilterN3("all"); }}>
                        <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-200 h-8 text-xs mt-1" data-testid="filter-trade-type">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">Todos</SelectItem>
                          <SelectItem value="real">Real</SelectItem>
                          <SelectItem value="paper">Paper</SelectItem>
                          <SelectItem value="log">
                            <span className="flex items-center gap-1.5">
                              <ChartBar size={11} className="text-violet-400" />
                              Log
                            </span>
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    {/* Symbol — always visible */}
                    <div>
                      <Label className="text-[10px] text-zinc-500">Simbolo</Label>
                      <Select value={filterSymbol} onValueChange={setFilterSymbol}>
                        <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-200 h-8 text-xs mt-1" data-testid="filter-symbol">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">Todos</SelectItem>
                          <SelectItem value="MNQ">MNQ</SelectItem>
                          <SelectItem value="MES">MES</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    {/* Log-specific filters */}
                    {isLogView && (
                      <>
                        <div>
                          <Label className="text-[10px] text-zinc-500">Execução N3</Label>
                          <Select value={filterN3} onValueChange={setFilterN3}>
                            <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-200 h-8 text-xs mt-1">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">Todos</SelectItem>
                              <SelectItem value="confirmed">
                                <span className="flex items-center gap-1.5">
                                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
                                  N3 Confirmado
                                </span>
                              </SelectItem>
                              <SelectItem value="simulated">
                                <span className="flex items-center gap-1.5">
                                  <span className="w-1.5 h-1.5 rounded-full bg-zinc-500 inline-block" />
                                  Apenas Simulado
                                </span>
                              </SelectItem>
                            </SelectContent>
                          </Select>
                        </div>

                        <div>
                          <Label className="text-[10px] text-zinc-500">Outcome</Label>
                          <Select value={filterLogOutcome} onValueChange={setFilterLogOutcome}>
                            <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-200 h-8 text-xs mt-1">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">Todos</SelectItem>
                              <SelectItem value="pending">Pendente</SelectItem>
                              <SelectItem value="TARGET_HIT">Target Hit</SelectItem>
                              <SelectItem value="STOP_HIT">Stop Hit</SelectItem>
                              <SelectItem value="EXPIRED">Expirado</SelectItem>
                              <SelectItem value="BREAKEVEN">Break-Even</SelectItem>
                              <SelectItem value="MANUAL_CLOSE">Manual</SelectItem>
                              <SelectItem value="NOT_TRADED">N3 Não Confirmou</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>

                        <div>
                          <Label className="text-[10px] text-zinc-500">Regime</Label>
                          <Select value={filterLogRegime} onValueChange={setFilterLogRegime}>
                            <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-200 h-8 text-xs mt-1">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">Todos</SelectItem>
                              <SelectItem value="TRANSICAO">Transição</SelectItem>
                              <SelectItem value="BULL">Bull</SelectItem>
                              <SelectItem value="BEAR">Bear</SelectItem>
                              <SelectItem value="COMPLACENCIA">Complacência</SelectItem>
                              <SelectItem value="CAPITULACAO">Capitulação</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </>
                    )}

                    {/* Position-specific filters */}
                    {!isLogView && (
                      <>
                        <div>
                          <Label className="text-[10px] text-zinc-500">Status</Label>
                          <Select value={filterStatus} onValueChange={setFilterStatus}>
                            <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-200 h-8 text-xs mt-1" data-testid="filter-status">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">Todos</SelectItem>
                              <SelectItem value="OPEN">Aberta</SelectItem>
                              <SelectItem value="MANAGING">Gerenciando</SelectItem>
                              <SelectItem value="BREAK_EVEN">Break-Even</SelectItem>
                              <SelectItem value="CLOSED">Fechada</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>

                        <div>
                          <Label className="text-[10px] text-zinc-500">Periodo</Label>
                          <Select value={filterDays} onValueChange={setFilterDays}>
                            <SelectTrigger className="bg-zinc-800 border-zinc-700 text-zinc-200 h-8 text-xs mt-1" data-testid="filter-days">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="1">Hoje</SelectItem>
                              <SelectItem value="7">7 dias</SelectItem>
                              <SelectItem value="30">30 dias</SelectItem>
                              <SelectItem value="90">90 dias</SelectItem>
                              <SelectItem value="all">Todos</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </>
                    )}

                    <Separator className="bg-zinc-800" />

                    {/* By-regime breakdown */}
                    {!isLogView && s.by_regime && Object.keys(s.by_regime).length > 0 && (
                      <div>
                        <div className="text-[10px] text-zinc-600 uppercase tracking-wider mb-2">P&L por Regime</div>
                        {Object.entries(s.by_regime).map(([regime, data]) => (
                          <div key={regime} className="flex justify-between text-[10px] py-0.5">
                            <span className="text-zinc-400">{regime}</span>
                            <div className="flex gap-3">
                              <span className="text-zinc-500">{data.count}t</span>
                              <span className={data.pnl >= 0 ? "text-emerald-400" : "text-red-400"}>
                                ${data.pnl?.toFixed(0) || 0}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {isLogView && ls.by_regime && ls.by_regime.length > 0 && (
                      <div>
                        <div className="text-[10px] text-zinc-600 uppercase tracking-wider mb-2">Win Rate por Regime</div>
                        {ls.by_regime.map((r) => (
                          <div key={r.regime} className="flex justify-between text-[10px] py-0.5">
                            <span className="text-zinc-400">{r.regime}</span>
                            <div className="flex gap-3">
                              <span className="text-zinc-500">{r.total}s</span>
                              <span className={r.win_rate_pct >= 50 ? "text-emerald-400" : "text-amber-400"}>
                                {r.win_rate_pct ?? 0}%
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="telegram">
                <Card className="bg-zinc-900/80 border-zinc-800">
                  <CardContent className="p-3">
                    <TelegramPanel />
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </div>

          {/* Right Panel */}
          <div className="col-span-12 lg:col-span-9">
            <Card className="bg-zinc-900/80 border-zinc-800">
              <CardHeader className="py-3 px-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xs text-zinc-400 flex items-center gap-2">
                    {isLogView
                      ? <><ChartBar size={14} className="text-violet-400" /> Signal Log ({logSignals.length})</>
                      : <><ListBullets size={14} className="text-cyan-400" /> Posicoes ({positions.length})</>
                    }
                  </CardTitle>
                  {!isLogView && (
                    <div className="flex gap-2 text-[10px]">
                      {activeCount > 0 && <Badge className="bg-emerald-900/30 text-emerald-400 border-emerald-700">{activeCount} ativa{activeCount > 1 ? "s" : ""}</Badge>}
                      <Badge variant="outline" className="border-zinc-700 text-zinc-500">{closedCount} fechada{closedCount > 1 ? "s" : ""}</Badge>
                    </div>
                  )}
                  {isLogView && (
                    <div className="flex gap-2 text-[10px]">
                      <Badge variant="outline" className="border-amber-700/50 text-amber-400">{ls.pending || 0} pendentes</Badge>
                      <Badge variant="outline" className="border-zinc-700 text-zinc-500">{ls.resolved || 0} resolvidos</Badge>
                    </div>
                  )}
                </div>
              </CardHeader>
              <CardContent className="px-0 pb-0">
                {loading ? (
                  <div className="text-center py-8 text-zinc-600 text-sm">Carregando...</div>
                ) : isLogView ? (
                  /* ── Signal Log Table ── */
                  logSignals.length === 0 ? (
                    <div className="text-center py-12">
                      <ChartBar size={36} className="text-zinc-800 mx-auto mb-3" />
                      <div className="text-zinc-600 text-sm">Nenhum sinal no log</div>
                      <div className="text-zinc-700 text-xs mt-1">
                        Setups N2 detectados aparecerão aqui automaticamente
                      </div>
                    </div>
                  ) : (
                    <ScrollArea className="h-[600px]">
                        {/* Log Table Header */}
                      <div className="sticky top-0 bg-zinc-900 z-10 px-3 py-2 flex items-center gap-2 text-[9px] text-zinc-600 uppercase tracking-wider border-b border-zinc-800">
                        <div className="w-24 shrink-0">Data/Hora</div>
                        <div className="w-14 shrink-0">N3</div>
                        <div className="w-8 shrink-0">Sym</div>
                        <div className="w-10 shrink-0">Dir</div>
                        <div className="w-20 shrink-0">Regime</div>
                        <div className="w-10 shrink-0">Borda</div>
                        <div className="w-8 text-right shrink-0">Z</div>
                        <div className="w-10 text-right shrink-0">DR</div>
                        <div className="w-16 text-right shrink-0">Entry</div>
                        <div className="w-16 text-right shrink-0 text-red-500/60">Stop</div>
                        <div className="w-16 text-right shrink-0 text-emerald-500/60">Target</div>
                        <div className="w-10 text-right shrink-0">R:R</div>
                        <div className="w-20 text-center shrink-0">Outcome</div>
                        <div className="w-14 text-right shrink-0">PnL pts</div>
                      </div>
                      {logSignals.map((sig) => (
                        <SignalLogRow key={sig._id || sig.timestamp} sig={sig} />
                      ))}
                    </ScrollArea>
                  )
                ) : (
                  /* ── Positions Table ── */
                  positions.length === 0 ? (
                    <div className="text-center py-12">
                      <ListBullets size={36} className="text-zinc-800 mx-auto mb-3" />
                      <div className="text-zinc-600 text-sm">Nenhuma posicao encontrada</div>
                      <div className="text-zinc-700 text-xs mt-1">Execute trades via V3 para populr o journal</div>
                    </div>
                  ) : (
                    <ScrollArea className="h-[600px]">
                      <div className="sticky top-0 bg-zinc-900 z-10 px-3 py-2 flex items-center gap-3 text-[10px] text-zinc-600 uppercase tracking-wider border-b border-zinc-800">
                        <div className="w-5" />
                        <div className="w-10">Side</div>
                        <div className="w-10">Sym</div>
                        <div className="w-8 text-right">Qty</div>
                        <div className="w-16">Status</div>
                        <div className="w-14 text-right">Arch</div>
                        <div className="w-14">Regime</div>
                        <div className="w-20 text-right">Entry</div>
                        <div className="w-20 text-right">Exit</div>
                        <div className="w-20 text-right">P&L</div>
                        <div className="ml-auto">Data</div>
                      </div>
                      {positions.map(pos => (
                        <PositionRow
                          key={pos.id}
                          pos={pos}
                          expanded={expandedId === pos.id}
                          onToggle={() => setExpandedId(expandedId === pos.id ? null : pos.id)}
                        />
                      ))}
                    </ScrollArea>
                  )
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
