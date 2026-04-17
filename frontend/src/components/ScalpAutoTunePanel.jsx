import React, { useState, useEffect, useRef, useCallback } from "react";

const API = "/api/scalp";

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(v, d = 2) {
  if (v === null || v === undefined) return "—";
  return typeof v === "number" ? v.toFixed(d) : v;
}
function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

// ── Badge ─────────────────────────────────────────────────────────────────────
function Badge({ label, color }) {
  const cls =
    color === "green" ? "bg-green-950 text-green-400 border-green-500/20"
    : color === "red" ? "bg-red-950 text-red-400 border-red-500/20"
    : "bg-zinc-800 text-amber-400 border-amber-500/20";
  return (
    <span className={`inline-block px-1.5 py-px rounded text-[11px] font-bold border ${cls}`}>
      {label}
    </span>
  );
}

// ── Metric ────────────────────────────────────────────────────────────────────
function Metric({ label, value, color, size = "normal" }) {
  const cText =
    color === "green" ? "text-green-400"
    : color === "red" ? "text-red-400"
    : color === "amber" ? "text-amber-400"
    : "text-zinc-100";
  const sz = size === "large" ? "text-[22px]" : "text-base";
  return (
    <div className="text-center">
      <div className={`${cText} font-bold font-mono ${sz}`}>{value}</div>
      <div className="text-zinc-500 text-[10px] mt-0.5">{label}</div>
    </div>
  );
}

// ── Equity Curve SVG ──────────────────────────────────────────────────────────
function EquityCurve({ data }) {
  if (!data || data.length < 2)
    return (
      <div className="h-20 flex items-center justify-center text-zinc-500 text-xs">
        Sem dados de equity
      </div>
    );

  const w = 380, h = 80, pad = 6;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;

  const pts = data.map((v, i) => {
    const x = pad + (i / (data.length - 1)) * (w - 2 * pad);
    const y = pad + (1 - (v - min) / range) * (h - 2 * pad);
    return `${x},${y}`;
  }).join(" ");

  const first = data[0], last = data[data.length - 1];
  const color = last >= first ? "#4ade80" : "#f87171";

  return (
    <svg width={w} height={h} style={{ maxWidth: "100%" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} />
      <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke="#27272a" strokeWidth={0.5} />
    </svg>
  );
}

// ── Progress Bar ──────────────────────────────────────────────────────────────
function ProgressBar({ pct, label, method }) {
  const isBayesian = method === "BAYESIAN_GP";
  return (
    <div>
      <div className="flex justify-between mb-1 text-[11px]">
        <span className="text-zinc-500">{label}</span>
        <span className={`font-mono ${isBayesian ? "text-indigo-400" : "text-amber-400"}`}>{pct?.toFixed(1)}%</span>
      </div>
      <div className="bg-zinc-800 rounded h-1.5">
        <div
          className={`h-1.5 rounded transition-all duration-300 ${isBayesian ? "bg-indigo-500" : "bg-amber-500"}`}
          style={{ width: `${pct || 0}%` }}
        />
      </div>
    </div>
  );
}

// ── Param Config Row (3-state: OPT / FIX / OFF) ───────────────────────────────
const MODE_CYCLE  = { opt: "fix", fix: "off", off: "opt" };
const MODE_LABELS = { opt: "OPT", fix: "FIX", off: "OFF" };

function ParamRow({ label, paramKey, state, onChange, min, max, isInt }) {
  const mode     = state?.mode ?? "off";
  const value    = state?.value ?? (min + max) / 2;
  const rangeMin = state?.rangeMin ?? min;
  const rangeMax = state?.rangeMax ?? max;
  const step     = isInt ? 1 : parseFloat(((max - min) / 20).toFixed(4));

  function cycleMode() { onChange(paramKey, "mode", MODE_CYCLE[mode]); }

  const btnCls =
    mode === "opt" ? "bg-green-500 text-black"
    : mode === "fix" ? "bg-amber-500 text-black"
    : "bg-zinc-700 text-zinc-500";

  return (
    <div className={`flex items-center gap-1.5 py-[3px] ${mode === "off" ? "opacity-40" : ""}`}>
      <button
        onClick={cycleMode}
        className={`w-[34px] h-[18px] border-0 rounded-sm cursor-pointer text-[9px] font-bold shrink-0 p-0 leading-none ${btnCls}`}
      >
        {MODE_LABELS[mode]}
      </button>
      <div className="flex-1 text-zinc-100 text-[10px] min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
        {label}
      </div>
      {mode === "opt" && (
        <div className="flex items-center gap-1">
          <input
            type="number" value={rangeMin}
            onChange={e => onChange(paramKey, "rangeMin", isInt ? parseInt(e.target.value) : parseFloat(e.target.value))}
            className="w-[46px] bg-zinc-800 border-0 text-zinc-100 rounded-sm text-[9px] px-1 py-0.5 text-center"
          />
          <span className="text-zinc-500 text-[9px]">–</span>
          <input
            type="number" value={rangeMax}
            onChange={e => onChange(paramKey, "rangeMax", isInt ? parseInt(e.target.value) : parseFloat(e.target.value))}
            className="w-[46px] bg-zinc-800 border-0 text-zinc-100 rounded-sm text-[9px] px-1 py-0.5 text-center"
          />
        </div>
      )}
      {mode === "fix" && (
        <div className="flex items-center gap-1">
          <input
            type="range" min={min} max={max} step={step} value={value}
            onChange={e => onChange(paramKey, "value", isInt ? parseInt(e.target.value) : parseFloat(e.target.value))}
            className="w-20 accent-amber-400"
          />
          <span className="text-zinc-100 text-[10px] w-10 text-right shrink-0">
            {isInt ? Math.round(value) : fmt(value, 3)}
          </span>
        </div>
      )}
      {mode === "off" && <div className="w-[134px]" />}
    </div>
  );
}

// ── Catálogo de parâmetros agrupado ───────────────────────────────────────────
const GROUP_ORDER     = ["quality", "zones", "zones_mnq", "zones_mes", "flow", "risk_core", "risk_extra"];
const GROUP_LABELS_UI = {
  quality:    "◈ QUALIDADE",
  zones:      "◈ ZONES (base)",
  zones_mnq:  "◈ ZONES MNQ",
  zones_mes:  "◈ ZONES MES",
  flow:       "◈ FLOW",
  risk_core:  "◈ RISCO CORE",
  risk_extra: "◈ RISCO EXTRA",
};

function CatalogueRows({ catalogue, paramStates, onChange }) {
  if (!catalogue) return null;
  const byGroup = {};
  Object.entries(catalogue).forEach(([k, p]) => {
    (byGroup[p.group] = byGroup[p.group] || []).push([k, p]);
  });
  return (
    <>
      {GROUP_ORDER.filter(g => byGroup[g]).map(g => (
        <div key={g}>
          <div className="text-amber-400 text-[9px] font-bold py-1.5 tracking-widest">
            {GROUP_LABELS_UI[g] || g}
          </div>
          {byGroup[g].map(([k, p]) => (
            <ParamRow key={k}
              label={p.label || k}
              paramKey={k}
              state={paramStates[k]}
              onChange={onChange}
              min={p.min} max={p.max} isInt={p.is_int}
            />
          ))}
        </div>
      ))}
    </>
  );
}

// ── Helpers de assessment ─────────────────────────────────────────────────────
function assessTextColor(a) {
  return a === "OVER_BLOCKING" ? "text-red-400"
       : a === "UNDER_FILTERING" ? "text-amber-400"
       : a === "BALANCED" ? "text-green-400"
       : "text-zinc-500";
}
function assessBorderColor(a) {
  return a === "OVER_BLOCKING" ? "border-red-500/25"
       : a === "UNDER_FILTERING" ? "border-amber-500/25"
       : a === "BALANCED" ? "border-green-500/25"
       : "border-zinc-800";
}

// ── Main Component ─────────────────────────────────────────────────────────────
export default function ScalpAutoTunePanel({ onClose }) {
  const [tab,       setTab]       = useState("optimize");
  const [mode,      setMode]      = useState("ZONES");
  const [method,    setMethod]    = useState("BAYESIAN");
  const [objective, setObjective] = useState("sharpe");
  const [symbol,    setSymbol]    = useState("MNQ");
  const [nRandom,   setNRandom]   = useState(15);
  const [nIter,     setNIter]     = useState(30);

  const [spaceData,   setSpaceData]   = useState(null);
  const [paramStates, setParamStates] = useState({});

  const [optStatus,    setOptStatus]    = useState(null);
  const [optResult,    setOptResult]    = useState(null);
  const [replayResult, setReplayResult] = useState(null);
  const [wfResult,     setWfResult]     = useState(null);

  const [loading,       setLoading]       = useState(false);
  const [error,         setError]         = useState(null);
  const [snapshotStats, setSnapshotStats] = useState(null);

  const [wfTrainDays, setWfTrainDays] = useState(10);
  const [wfTestDays,  setWfTestDays]  = useState(3);
  const [wfFolds,     setWfFolds]     = useState(4);

  const [useDatabentoCandles, setUseDatabentoCandles] = useState(false);

  const [scheduleDoc,     setScheduleDoc]     = useState(null);
  const [scheduleHistory, setScheduleHistory] = useState([]);
  const [scheduleSaving,  setScheduleSaving]  = useState(false);
  const [scheduleRunning, setScheduleRunning] = useState(false);
  const [scheduleError,   setScheduleError]   = useState(null);
  const [schedCfg, setSchedCfg] = useState({
    enabled: true, frequency: "weekly", custom_hours: 24, day_of_week: 6,
    hour_utc: 6, mode: "FLOW", method: "BAYESIAN", objective: "sharpe",
    symbol: "MNQ", train_days: 10, test_days: 3, n_folds: 4,
    n_random: 5, n_iter: 15, min_snapshots: 50, auto_apply: false,
    improvement_threshold_pct: 5.0,
  });

  const [dateFrom,      setDateFrom]      = useState("");
  const [dateTo,        setDateTo]        = useState("");
  const [sessionFilter, setSessionFilter] = useState("");

  const [calibData,    setCalibData]    = useState(null);
  const [calibLoading, setCalibLoading] = useState(false);
  const [calibSymbol,  setCalibSymbol]  = useState("MNQ");
  const [calibDays,    setCalibDays]    = useState(90);
  const [calibMinN,    setCalibMinN]    = useState(20);

  const [diagData,    setDiagData]    = useState(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const [diagSymbol,  setDiagSymbol]  = useState("MNQ");
  const [diagDays,    setDiagDays]    = useState(30);
  const [diagMode,    setDiagMode]    = useState("");
  const [diagSession, setDiagSession] = useState("");

  const [combData,      setCombData]      = useState(null);
  const [combLoading,   setCombLoading]   = useState(false);
  const [combSymbol,    setCombSymbol]    = useState("MNQ");
  const [combSession,   setCombSession]   = useState("");
  const [combDaysDiag,  setCombDaysDiag]  = useState(30);
  const [combDaysCal,   setCombDaysCal]   = useState(90);
  const [combMaxDelta,  setCombMaxDelta]  = useState(0.05);
  const [combSched,     setCombSched]     = useState(null);
  const [combHistory,   setCombHistory]   = useState([]);
  const [combApplying,  setCombApplying]  = useState(false);
  const [combApplyMsg,  setCombApplyMsg]  = useState(null);
  const [combSchedCfg,  setCombSchedCfg]  = useState({
    enabled: true, frequency: "daily", hour_utc: 6, day_of_week: 6,
    custom_hours: 24, days_diag: 30, days_cal: 90,
    max_delta: 0.05, min_snapshots: 30, min_trades: 10, auto_apply: false,
  });

  const pollRef    = useRef(null);
  const statsTimer = useRef(null);

  // ── Snapshot stats ────────────────────────────────────────────────────────
  const fetchSnapshotStats = useCallback(() => {
    fetch(`${API}/snapshots/stats`)
      .then(r => r.json()).then(d => setSnapshotStats(d)).catch(() => {});
  }, []);

  useEffect(() => {
    fetchSnapshotStats();
    statsTimer.current = setInterval(fetchSnapshotStats, 30000);
    return () => clearInterval(statsTimer.current);
  }, [fetchSnapshotStats]);

  // ── Schedule ───────────────────────────────────────────────────────────────
  const fetchSchedule = useCallback(() => {
    fetch(`${API}/tune/schedule`).then(r => r.json()).then(d => {
      if (d && !d.message) { setScheduleDoc(d); setSchedCfg(prev => ({ ...prev, ...d })); }
    }).catch(() => {});
    fetch(`${API}/tune/schedule/history?limit=10`).then(r => r.json())
      .then(d => setScheduleHistory(d.history || [])).catch(() => {});
  }, []);

  useEffect(() => { if (tab === "schedule") fetchSchedule(); }, [tab, fetchSchedule]);

  // ── Calibração ────────────────────────────────────────────────────────────
  const fetchCalibration = useCallback(() => {
    setCalibLoading(true);
    fetch(`${API}/tune/calibration?symbol=${calibSymbol}&days=${calibDays}&min_n=${calibMinN}`)
      .then(r => r.json()).then(d => { setCalibData(d); setCalibLoading(false); })
      .catch(() => setCalibLoading(false));
  }, [calibSymbol, calibDays, calibMinN]);

  useEffect(() => { if (tab === "calibration") fetchCalibration(); }, [tab, fetchCalibration]);

  // ── Diagnóstico ────────────────────────────────────────────────────────────
  const fetchDiagnostics = useCallback(() => {
    setDiagLoading(true);
    const modeParam    = diagMode    ? `&mode_filter=${diagMode}`    : "";
    const sessionParam = diagSession ? `&session=${diagSession}`     : "";
    fetch(`${API}/tune/diagnostics?symbol=${diagSymbol}&days=${diagDays}${modeParam}${sessionParam}`)
      .then(r => r.json()).then(d => { setDiagData(d); setDiagLoading(false); })
      .catch(() => setDiagLoading(false));
  }, [diagSymbol, diagDays, diagMode, diagSession]);

  useEffect(() => { if (tab === "diagnostics") fetchDiagnostics(); }, [tab, fetchDiagnostics]);

  // ── Análise Combinada ─────────────────────────────────────────────────────
  const fetchCombined = useCallback(() => {
    setCombLoading(true);
    const sessionParam = combSession ? `&session=${combSession}` : "";
    fetch(`${API}/tune/combined?symbol=${combSymbol}&days_diag=${combDaysDiag}&days_cal=${combDaysCal}&max_delta=${combMaxDelta}${sessionParam}`)
      .then(r => r.json()).then(d => { setCombData(d); setCombLoading(false); })
      .catch(() => setCombLoading(false));
  }, [combSymbol, combSession, combDaysDiag, combDaysCal, combMaxDelta]);

  const fetchCombinedSchedule = useCallback(() => {
    fetch(`${API}/tune/combined/schedule`).then(r => r.json())
      .then(d => { if (d.schedule) { setCombSched(d.schedule); setCombSchedCfg(s => ({ ...s, ...d.schedule })); } })
      .catch(() => {});
    fetch(`${API}/tune/combined/history?symbol=${combSymbol}&limit=10`).then(r => r.json())
      .then(d => setCombHistory(d.history || [])).catch(() => {});
  }, [combSymbol]);

  useEffect(() => {
    if (tab === "combined") { fetchCombined(); fetchCombinedSchedule(); }
  }, [tab, fetchCombined, fetchCombinedSchedule]);

  const handleSaveCombinedSchedule = async () => {
    await fetch(`${API}/tune/combined/schedule`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...combSchedCfg, symbol: combSymbol }),
    });
    fetchCombinedSchedule();
  };

  const handleRunCombinedNow = async () => {
    setCombLoading(true);
    const r = await fetch(`${API}/tune/combined/run-now?symbol=${combSymbol}`, { method: "POST" });
    const d = await r.json();
    if (d.analysis) setCombData(d.analysis);
    fetchCombinedSchedule();
    setCombLoading(false);
  };

  const handleApplyCombined = async (dryRun) => {
    if (!combData || !combData.suggestions || combData.suggestions.length === 0) return;
    setCombApplying(true); setCombApplyMsg(null);
    const r = await fetch(`${API}/tune/combined/apply`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ suggestions: combData.suggestions, dry_run: dryRun }),
    });
    const d = await r.json();
    setCombApplyMsg(d); setCombApplying(false);
    if (!dryRun) fetchCombined();
  };

  const handleSaveSchedule = async () => {
    setScheduleSaving(true);
    setScheduleError(null);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);
    try {
      const r = await fetch(`${API}/tune/schedule`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(schedCfg),
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        const detail = err?.detail;
        const msg = Array.isArray(detail) ? detail.map(d => d.msg || d).join("; ") : detail || `HTTP ${r.status}`;
        throw new Error(msg);
      }
      const d = await r.json();
      setScheduleDoc(d);
      setSchedCfg(prev => ({ ...prev, ...d }));
    } catch (e) {
      clearTimeout(timer);
      setScheduleError(e.name === "AbortError" ? "Timeout: o servidor não respondeu em 15s." : e.message);
    } finally {
      setScheduleSaving(false);
    }
  };

  const handleDisableSchedule = async () => {
    if (!window.confirm("Desactivar o schedule automático?")) return;
    await fetch(`${API}/tune/schedule`, { method: "DELETE" });
    setScheduleDoc(null);
  };

  const handleRunNow = async () => {
    if (!scheduleDoc) return;
    setScheduleRunning(true);
    try {
      await fetch(`${API}/tune/schedule/run-now`, { method: "POST" });
      setTimeout(fetchSchedule, 2000);
    } catch { }
    setScheduleRunning(false);
  };

  // ── Carrega espaço de parâmetros ──────────────────────────────────────────
  useEffect(() => {
    // Para ZONES com símbolo específico, usa espaço per-símbolo (zones_mnq.* / zones_mes.*)
    const spaceMode = (mode === "ZONES" && symbol) ? `ZONES_${symbol}` : mode;
    fetch(`${API}/tune/params/space?mode=${spaceMode}`)
      .then(r => r.json())
      .then(data => {
        setSpaceData(data);
        const init = {};
        const source = data.catalogue || data.params || {};
        Object.entries(source).forEach(([k, p]) => {
          init[k] = {
            mode:     p.in_mode !== false ? "opt" : "off",
            value:    (p.min + p.max) / 2,
            rangeMin: p.min,
            rangeMax: p.max,
          };
        });
        setParamStates(prev => {
          const merged = { ...init };
          Object.keys(init).forEach(k => { if (prev[k]) merged[k] = prev[k]; });
          return merged;
        });
      }).catch(() => {});
  }, [mode, symbol]);

  // ── Polling de status ─────────────────────────────────────────────────────
  const startPolling = useCallback(() => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const st = await fetch(`${API}/tune/optimize/status`).then(r => r.json());
        setOptStatus(st);
        if (st.status === "completed") {
          clearInterval(pollRef.current);
          const res = await fetch(`${API}/tune/optimize/result`).then(r => r.json());
          setOptResult(res); setLoading(false);
        } else if (st.status === "cancelled" || st.status === "idle") {
          clearInterval(pollRef.current); setLoading(false);
        }
      } catch { clearInterval(pollRef.current); setLoading(false); }
    }, 1500);
  }, []);

  useEffect(() => () => clearInterval(pollRef.current), []);

  function withDates(cfg) {
    if (dateFrom)      cfg.start_date     = dateFrom;
    if (dateTo)        cfg.end_date       = dateTo;
    if (sessionFilter) cfg.session_filter = sessionFilter;
    return cfg;
  }

  function _setNested(cfg, key, val) {
    if (key.includes(".")) {
      const [sec, param] = key.split(".");
      if (!cfg[sec]) cfg[sec] = {};
      cfg[sec][param] = val;
    } else {
      cfg[key] = val;
    }
  }

  function applyParamsFix(cfg) {
    Object.entries(paramStates).forEach(([key, state]) => {
      if (state.mode !== "fix") return;
      _setNested(cfg, key, state.value);
    });
    return cfg;
  }

  function applyParamsReplay(cfg) {
    Object.entries(paramStates).forEach(([key, state]) => {
      if (state.mode === "off") return;
      const val = state.mode === "fix"
        ? state.value
        : ((state.rangeMin + state.rangeMax) / 2);
      _setNested(cfg, key, val);
    });
    return cfg;
  }

  function buildCustomSpace() {
    const space = {};
    Object.entries(paramStates).forEach(([key, state]) => {
      if (state.mode !== "opt") return;
      space[key] = [state.rangeMin, state.rangeMax];
    });
    return Object.keys(space).length > 0 ? space : null;
  }

  function buildBaseConfig() {
    return applyParamsFix(withDates({ symbol, mode_filter: mode, use_databento_candles: useDatabentoCandles }));
  }

  function buildReplayConfig() {
    return applyParamsReplay(withDates({ symbol, mode_filter: mode, use_databento_candles: useDatabentoCandles }));
  }

  function handleParamChange(paramKey, field, val) {
    setParamStates(prev => ({
      ...prev,
      [paramKey]: { ...prev[paramKey], [field]: val },
    }));
  }

  async function handleStartOptimize() {
    setLoading(true); setError(null); setOptResult(null); setOptStatus(null);
    try {
      const body = {
        method, mode,
        base_config:  buildBaseConfig(),
        custom_space: buildCustomSpace(),
        objective, n_random: nRandom, n_iter: nIter, min_snapshots: 3,
      };
      const resp = await fetch(`${API}/tune/optimize`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        let detail = text;
        try { detail = JSON.parse(text)?.detail || text; } catch {}
        throw new Error(detail || `Erro ao iniciar optimização (HTTP ${resp.status})`);
      }
      startPolling();
    } catch (e) { setError(e.message); setLoading(false); }
  }

  async function handleCancelOptimize() {
    await fetch(`${API}/tune/optimize/cancel`, { method: "POST" });
    clearInterval(pollRef.current); setLoading(false);
  }

  async function handleRunReplay() {
    setLoading(true); setError(null); setReplayResult(null);
    try {
      const resp = await fetch(`${API}/tune/replay`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: buildReplayConfig() }),
      });
      const text = await resp.text().catch(() => "");
      let data;
      try { data = JSON.parse(text); } catch { data = {}; }
      if (!resp.ok) throw new Error(data?.detail || text || `Erro no replay (HTTP ${resp.status})`);
      setReplayResult(data);
    } catch (e) { setError(e.message); }
    setLoading(false);
  }

  async function handleRunWalkForward() {
    setLoading(true); setError(null); setWfResult(null);
    try {
      const body = {
        base_config:  buildBaseConfig(),
        custom_space: buildCustomSpace(),
        method, mode, objective,
        train_days: wfTrainDays, test_days: wfTestDays, n_folds: wfFolds,
        n_random: Math.min(nRandom, 5), n_iter: Math.min(nIter, 15),
      };
      const resp = await fetch(`${API}/tune/walk-forward`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const text = await resp.text().catch(() => "");
      let data;
      try { data = JSON.parse(text); } catch { data = {}; }
      if (!resp.ok) throw new Error(data?.detail || text || `Erro no walk-forward (HTTP ${resp.status})`);
      setWfResult(data);
    } catch (e) { setError(e.message); }
    setLoading(false);
  }

  function applyBestParams(params) {
    if (!params) return;
    const next = { ...paramStates };
    Object.entries(params).forEach(([k, v]) => {
      const cat = spaceData?.catalogue?.[k] ?? spaceData?.params?.[k];
      next[k] = {
        mode:     "fix",
        value:    v,
        rangeMin: next[k]?.rangeMin ?? cat?.min ?? v,
        rangeMax: next[k]?.rangeMax ?? cat?.max ?? v,
      };
    });
    setParamStates(next);
    setTab("replay");
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Shared classes
  const inputCls = "bg-zinc-800 border-0 text-zinc-100 rounded px-1.5 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500";
  const selectCls = "bg-zinc-800 border-0 text-zinc-100 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500";
  const cardCls = "bg-zinc-900 border border-zinc-800 rounded-lg";
  const tabBtnCls = (active) =>
    `px-4 py-2 border-0 cursor-pointer text-[11px] font-bold bg-transparent transition-colors ` +
    (active ? "text-indigo-400 border-b-2 border-indigo-500 -mb-px" : "text-zinc-500 border-b-2 border-transparent hover:text-zinc-300");

  const TABS = [
    ["optimize",    "🔬 Optimizar"],
    ["replay",      "▶ Replay"],
    ["walkforward", "📊 Walk-Forward"],
    ["schedule",    "⏱ Schedule"],
    ["calibration", "📐 Calibração"],
    ["diagnostics", "🔍 Diagnóstico"],
    ["combined",    "🔄 Combinada"],
  ];

  // ─────────────────────────────────────────────────────────────────────────────
  return (
    <div className="bg-zinc-950 min-h-screen text-zinc-100 overflow-y-auto">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="sticky top-0 z-10 bg-zinc-900 border-b border-zinc-800 px-4 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="text-indigo-400 font-bold text-sm">⚡ SCALP AUTO-TUNE</span>
          <Badge label={mode} color="amber" />
          <Badge label={method} color={method === "BAYESIAN" ? "green" : "amber"} />
          {sessionFilter && (
            <Badge label={sessionFilter === "OVERNIGHT" ? "GLOBEX" : sessionFilter} color={sessionFilter === "OVERNIGHT" ? "green" : "amber"} />
          )}
        </div>
        <button
          onClick={onClose}
          className="bg-transparent border-0 text-zinc-500 hover:text-zinc-200 cursor-pointer text-xl px-1 leading-none transition-colors"
        >×</button>
      </div>

      {/* ── Controls ───────────────────────────────────────────────────────── */}
      <div className="px-4 py-2.5 border-b border-zinc-800 bg-zinc-900 flex flex-wrap gap-2 items-center">

        <span className="text-[10px] text-zinc-500">SÍMBOLO</span>
        {["MNQ", "MES"].map(s => (
          <button key={s} onClick={() => setSymbol(s)}
            className={`px-2.5 py-0.5 rounded text-[11px] font-bold cursor-pointer border-0 transition-colors
              ${symbol === s ? "bg-indigo-600 text-white" : "bg-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
            {s}
          </button>
        ))}

        <span className="text-[10px] text-zinc-500 ml-2">MODO</span>
        {["ZONES", "FLOW"].map(m => (
          <button key={m} onClick={() => setMode(m)}
            className={`px-2.5 py-0.5 rounded text-[11px] font-bold cursor-pointer border-0 transition-colors
              ${mode === m ? "bg-amber-900 text-amber-400" : "bg-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
            {m}
          </button>
        ))}

        <span className="text-[10px] text-zinc-500 ml-2">MÉTODO</span>
        {["BAYESIAN", "GRID"].map(mt => (
          <button key={mt} onClick={() => setMethod(mt)}
            className={`px-2.5 py-0.5 rounded text-[11px] font-bold cursor-pointer border-0 transition-colors
              ${method === mt ? "bg-blue-900 text-blue-400" : "bg-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
            {mt}
          </button>
        ))}

        <span className="text-[10px] text-zinc-500 ml-2">OBJECTIVO</span>
        <select value={objective} onChange={e => setObjective(e.target.value)} className={selectCls}>
          {["sharpe","sortino","calmar","profit_factor","net_pnl","expectancy","min_drawdown"].map(o => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>

        {/* Filtro de sessão */}
        <div className="flex items-center gap-1 ml-2">
          <span className="text-[10px] text-zinc-500 whitespace-nowrap">SESSÃO</span>
          {[
            ["", "ALL"],
            ["OVERNIGHT", "GLOBEX"],
            ["RTH_OPEN",  "RTH-O"],
            ["RTH_MID",   "RTH-M"],
            ["RTH_CLOSE", "RTH-C"],
          ].map(([val, label]) => (
            <button key={val} onClick={() => setSessionFilter(val)}
              title={val || "Todas as sessões"}
              className={`px-2 py-0.5 rounded text-[10px] font-bold cursor-pointer border-0 transition-colors whitespace-nowrap
                ${sessionFilter === val
                  ? val === "" ? "bg-zinc-600 text-zinc-100"
                    : val === "OVERNIGHT" ? "bg-cyan-900 text-cyan-300"
                    : "bg-green-900 text-green-400"
                  : "bg-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
              {label}
            </button>
          ))}
        </div>

        {/* Filtro de datas */}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-[10px] text-zinc-500 whitespace-nowrap">PERÍODO</span>
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            title="Data inicial (inclusive)"
            style={{ colorScheme: "dark" }}
            className={`text-[10px] px-1.5 py-0.5 rounded border font-mono bg-zinc-800 focus:outline-none
              ${dateFrom ? "text-zinc-100 border-amber-500/60" : "text-zinc-500 border-zinc-700"}`}
          />
          <span className="text-zinc-500 text-[10px]">→</span>
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            title="Data final (inclusive)"
            style={{ colorScheme: "dark" }}
            className={`text-[10px] px-1.5 py-0.5 rounded border font-mono bg-zinc-800 focus:outline-none
              ${dateTo ? "text-zinc-100 border-amber-500/60" : "text-zinc-500 border-zinc-700"}`}
          />
          {(dateFrom || dateTo || sessionFilter) && (
            <button onClick={() => { setDateFrom(""); setDateTo(""); setSessionFilter(""); }}
              title="Limpar todos os filtros"
              className="border border-zinc-700 text-zinc-500 hover:text-zinc-200 text-[10px] px-1.5 py-0.5 rounded cursor-pointer bg-transparent transition-colors">
              ✕
            </button>
          )}
        </div>
      </div>

      {/* ── Snapshot Stats ─────────────────────────────────────────────────── */}
      <div className="px-4 py-1.5 border-b border-zinc-800 bg-zinc-950 flex flex-wrap gap-3.5 items-center">
        <span className="text-zinc-500 text-[10px] font-bold tracking-widest">SNAPSHOTS</span>
        {snapshotStats ? (
          <>
            <span className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full inline-block ${snapshotStats.total_snapshots > 0 ? "bg-green-500 shadow-[0_0_6px_#22c55e]" : "bg-red-500"}`} />
              <span className="text-zinc-100 font-bold text-xs">{snapshotStats.total_snapshots?.toLocaleString() ?? "—"}</span>
              <span className="text-zinc-500 text-[10px]">total</span>
            </span>
            {Object.entries(snapshotStats.by_symbol || {}).map(([sym, cnt]) => (
              <span key={sym} className="text-[10px]">
                <span className="text-amber-400 font-bold">{sym}</span>
                <span className="text-zinc-500"> {cnt?.toLocaleString()}</span>
              </span>
            ))}
            <span className="text-zinc-500 text-[10px]">
              intervalo <span className="text-zinc-100">{snapshotStats.interval_seconds}s</span>
            </span>
            <span className="text-zinc-500 text-[10px]">
              TTL <span className="text-zinc-100">{snapshotStats.ttl_days}d</span>
            </span>
            {snapshotStats.newest && (
              <span className="text-zinc-500 text-[10px]">
                último <span className="text-zinc-100">
                  {new Date(snapshotStats.newest).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
              </span>
            )}
            <span className="text-zinc-500 text-[10px]">
              ~<span className="text-zinc-100">{snapshotStats.estimated_size_mb} MB</span>
            </span>
            <button onClick={fetchSnapshotStats}
              className="ml-auto border border-zinc-700 text-zinc-500 hover:text-zinc-200 rounded px-2 py-0.5 text-[10px] cursor-pointer bg-transparent transition-colors">
              ↻
            </button>
          </>
        ) : (
          <span className="text-zinc-500 text-[10px]">A carregar...</span>
        )}
      </div>

      {/* ── Tabs ───────────────────────────────────────────────────────────── */}
      <div className="flex border-b border-zinc-800 bg-zinc-900 overflow-x-auto">
        {TABS.map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} className={tabBtnCls(tab === id)}>
            {label}
          </button>
        ))}
      </div>

      {/* ── Error ──────────────────────────────────────────────────────────── */}
      {error && (
        <div className="mx-4 mt-2.5 px-3 py-2 bg-red-950/60 border border-red-500/30 rounded-md text-red-400 text-xs">
          ⚠ {error}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════ */}
      {/* TAB: Optimizar                                                      */}
      {/* ════════════════════════════════════════════════════════════════════ */}
      {tab === "optimize" && (
        <div className="p-4">

          {method === "BAYESIAN" && (
            <div className={`${cardCls} p-3 mb-3`}>
              <div className="text-indigo-400 font-bold text-[11px] mb-2">GP BAYESIAN — CONFIGURAÇÃO</div>
              <div className="flex gap-4 flex-wrap">
                <div>
                  <div className="text-[10px] text-zinc-500 mb-1">Avaliações Random (Warm Start)</div>
                  <input type="number" min={3} max={20} value={nRandom} onChange={e => setNRandom(+e.target.value)}
                    className={`${inputCls} w-16`} />
                </div>
                <div>
                  <div className="text-[10px] text-zinc-500 mb-1">Iterações Guiadas (GP+EI)</div>
                  <input type="number" min={5} max={60} value={nIter} onChange={e => setNIter(+e.target.value)}
                    className={`${inputCls} w-16`} />
                </div>
                <div className="self-end">
                  <div className="text-[10px] text-zinc-500">
                    Total Avaliações: <span className="text-zinc-100 font-bold">{nRandom + nIter}</span>
                  </div>
                </div>
              </div>
              <div className="mt-2 px-2.5 py-1.5 bg-slate-950 rounded text-[10px] text-zinc-500">
                Fases: <span className="text-amber-400">{nRandom} random</span> → <span className="text-indigo-400">{nIter} GP/EI</span>.
                O GP aprende a superfície de resposta dos parâmetros e dirige a busca para regiões prometedoras.
              </div>
            </div>
          )}

          {spaceData?.catalogue && (
            <div className={`${cardCls} p-3 mb-3`}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="text-zinc-500 font-bold text-[11px]">ESPAÇO DE BUSCA</div>
                <div className="text-[9px] text-zinc-500">OPT=optimizar intervalo · FIX=valor fixo · OFF=ignora</div>
              </div>
              <CatalogueRows catalogue={spaceData.catalogue} paramStates={paramStates} onChange={handleParamChange} />
            </div>
          )}

          <div className="flex items-center gap-3 mb-3 px-1">
            <button
              onClick={() => setUseDatabentoCandles(v => !v)}
              className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 transition-colors duration-150
                ${useDatabentoCandles ? "bg-sky-600 border-sky-600" : "bg-zinc-700 border-zinc-700"}`}>
              <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform duration-150
                ${useDatabentoCandles ? "translate-x-4" : "translate-x-0"}`} />
            </button>
            <div>
              <span className={`text-[11px] font-bold ${useDatabentoCandles ? "text-sky-400" : "text-zinc-500"}`}>
                Candles M1 reais (DataBento)
              </span>
              <span className="ml-1.5 text-[10px] text-zinc-600">
                {useDatabentoCandles ? "outcomes via High/Low real de cada candle" : "modo snapshot (preço 30s)"}
              </span>
            </div>
          </div>

          <div className="flex gap-2 mb-4">
            {!loading ? (
              <button onClick={handleStartOptimize}
                className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white border-0 rounded-md font-bold text-xs cursor-pointer transition-colors">
                ▶ Iniciar {method}
              </button>
            ) : (
              <button onClick={handleCancelOptimize}
                className="px-5 py-2 bg-red-950 hover:bg-red-900 text-red-400 border-0 rounded-md font-bold text-xs cursor-pointer transition-colors">
                ◼ Cancelar
              </button>
            )}
          </div>

          {optStatus?.status === "running" && optStatus.progress && (
            <div className={`${cardCls} p-3 mb-3`}>
              <ProgressBar pct={optStatus.progress.pct}
                label={`${optStatus.progress.current}/${optStatus.progress.total} — ${optStatus.progress.method || method}`}
                method={optStatus.progress.method} />
              {optStatus.progress.best_objective !== undefined && (
                <div className="mt-2 text-[11px] text-zinc-500">
                  Melhor <span className="text-green-400">{objective}</span>:{" "}
                  <span className="text-zinc-100 font-bold">{fmt(optStatus.progress.best_objective, 4)}</span>
                </div>
              )}
            </div>
          )}

          {optResult && (
            <div>
              <div className="bg-green-950/40 border border-green-500/20 rounded-lg p-3 mb-3">
                <div className="flex justify-between items-center mb-2">
                  <span className="text-green-400 font-bold text-xs">★ MELHORES PARÂMETROS — {optResult.method}</span>
                  <button onClick={() => applyBestParams(optResult.best?.params)}
                    className="bg-indigo-600 hover:bg-indigo-700 text-white border-0 rounded px-2.5 py-0.5 text-[10px] cursor-pointer font-bold transition-colors">
                    Aplicar ao Replay
                  </button>
                </div>
                {optResult.best?.params && (
                  <div className="flex flex-wrap gap-x-4 gap-y-1">
                    {Object.entries(optResult.best.params).map(([k, v]) => (
                      <div key={k} className="text-[10px]">
                        <span className="text-zinc-500">{spaceData?.catalogue?.[k]?.label || k}: </span>
                        <span className="text-zinc-100 font-bold">
                          {typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(3)) : v}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex gap-5 mt-2.5">
                  <Metric label="Sharpe"   value={fmt(optResult.best?.metrics?.sharpe_ratio, 3)}   color="green" />
                  <Metric label="Sortino"  value={fmt(optResult.best?.metrics?.sortino_ratio, 3)}  color="green" />
                  <Metric label="Calmar"   value={fmt(optResult.best?.metrics?.calmar_ratio, 3)}   color={(optResult.best?.metrics?.calmar_ratio ?? 0) >= 0.5 ? "green" : "amber"} />
                  <Metric label="Win Rate" value={fmtPct(optResult.best?.metrics?.win_rate)}        color="green" />
                  <Metric label="PnL"      value={`$${fmt(optResult.best?.metrics?.total_pnl, 0)}`} color={optResult.best?.metrics?.total_pnl >= 0 ? "green" : "red"} />
                  <Metric label="PF"       value={fmt(optResult.best?.metrics?.profit_factor, 2)}   color="green" />
                  <Metric label="Trades"   value={optResult.best?.metrics?.total_trades ?? "—"}     color="text" />
                  <Metric label="Max DD"   value={`$${fmt(optResult.best?.metrics?.max_drawdown, 0)}`} color="red" />
                </div>
              </div>

              <div className={`${cardCls} overflow-hidden`}>
                <div className="px-3 py-2 border-b border-zinc-800 text-[11px] text-zinc-500 font-bold">
                  TOP {Math.min(optResult.results?.length || 0, 20)} RESULTADOS
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-[10px]">
                    <thead>
                      <tr className="border-b border-zinc-800">
                        <th className="px-2 py-1 text-zinc-500 text-left">#</th>
                        <th className="px-2 py-1 text-zinc-500 text-right">{objective}</th>
                        <th className="px-2 py-1 text-zinc-500 text-right">Win%</th>
                        <th className="px-2 py-1 text-zinc-500 text-right">PnL</th>
                        <th className="px-2 py-1 text-zinc-500 text-right">PF</th>
                        <th className="px-2 py-1 text-zinc-500 text-right">Trades</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(optResult.results || []).slice(0, 20).map((r, i) => (
                        <tr key={i} className="border-b border-zinc-800/20 hover:bg-zinc-800/30 cursor-pointer transition-colors"
                            onClick={() => applyBestParams(r.params)}>
                          <td className={`px-2 py-0.5 font-mono ${i === 0 ? "text-amber-400" : "text-zinc-500"}`}>{r.rank}</td>
                          <td className="px-2 py-0.5 text-green-400 text-right font-mono">{fmt(r.objective_value, 4)}</td>
                          <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{fmtPct(r.metrics?.win_rate)}</td>
                          <td className={`px-2 py-0.5 text-right font-mono ${(r.metrics?.total_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                            ${fmt(r.metrics?.total_pnl, 0)}
                          </td>
                          <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{fmt(r.metrics?.profit_factor, 2)}</td>
                          <td className="px-2 py-0.5 text-zinc-500 text-right font-mono">{r.metrics?.total_trades}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════ */}
      {/* TAB: Replay                                                         */}
      {/* ════════════════════════════════════════════════════════════════════ */}
      {tab === "replay" && (
        <div className="p-4">
          {spaceData?.catalogue && (
            <div className={`${cardCls} p-3 mb-3`}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="text-zinc-500 font-bold text-[11px]">PARÂMETROS — {mode}</div>
                <div className="text-[9px] text-zinc-500">OPT=midpoint · FIX=valor · OFF=ignora</div>
              </div>
              <CatalogueRows catalogue={spaceData.catalogue} paramStates={paramStates} onChange={handleParamChange} />
            </div>
          )}

          <div className="flex items-center gap-3 mb-3 px-1">
            <button
              onClick={() => setUseDatabentoCandles(v => !v)}
              className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 transition-colors duration-150
                ${useDatabentoCandles ? "bg-sky-600 border-sky-600" : "bg-zinc-700 border-zinc-700"}`}>
              <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform duration-150
                ${useDatabentoCandles ? "translate-x-4" : "translate-x-0"}`} />
            </button>
            <div>
              <span className={`text-[11px] font-bold ${useDatabentoCandles ? "text-sky-400" : "text-zinc-500"}`}>
                Candles M1 reais (DataBento)
              </span>
              <span className="ml-1.5 text-[10px] text-zinc-600">
                {useDatabentoCandles ? "outcomes via High/Low real de cada candle" : "modo snapshot (preço 30s)"}
              </span>
            </div>
          </div>

          <button onClick={handleRunReplay} disabled={loading}
            className={`px-5 py-2 border-0 rounded-md font-bold text-xs mb-4 transition-colors
              ${loading ? "bg-zinc-800 text-zinc-500 cursor-default" : "bg-green-600 hover:bg-green-700 text-black cursor-pointer"}`}>
            {loading ? "A executar..." : "▶ Executar Replay"}
          </button>

          {replayResult && (
            <div>
              <div className={`${cardCls} p-3 mb-3`}>
                <div className="text-zinc-500 text-[10px] mb-2">
                  {replayResult.snapshots_used} snapshots · {replayResult.metrics.total_trades} trades
                </div>
                <div className="flex flex-wrap gap-5 mb-3">
                  <Metric label="Sharpe"    value={fmt(replayResult.metrics.sharpe_ratio, 3)}   color="green" size="large" />
                  <Metric label="Sortino"   value={fmt(replayResult.metrics.sortino_ratio, 3)}  color="green" />
                  <Metric label="Calmar"    value={fmt(replayResult.metrics.calmar_ratio, 3)}   color={(replayResult.metrics.calmar_ratio ?? 0) >= 0.5 ? "green" : "amber"} />
                  <Metric label="Win Rate"  value={fmtPct(replayResult.metrics.win_rate)}        color={replayResult.metrics.win_rate > 0.5 ? "green" : "amber"} />
                  <Metric label="PnL"       value={`$${fmt(replayResult.metrics.total_pnl, 0)}`} color={replayResult.metrics.total_pnl >= 0 ? "green" : "red"} />
                  <Metric label="PF"        value={fmt(replayResult.metrics.profit_factor, 2)}   color={replayResult.metrics.profit_factor > 1.2 ? "green" : "amber"} />
                  <Metric label="Expectancy" value={`$${fmt(replayResult.metrics.expectancy, 1)}`} color={replayResult.metrics.expectancy > 0 ? "green" : "red"} />
                  <Metric label="Max DD"    value={`$${fmt(replayResult.metrics.max_drawdown, 0)}`} color="red" />
                </div>
                <EquityCurve data={replayResult.equity_curve} />
              </div>

              {replayResult.metrics.by_mode && Object.keys(replayResult.metrics.by_mode).length > 0 && (
                <div className={`${cardCls} p-3 mb-3`}>
                  <div className="text-zinc-500 text-[10px] font-bold mb-2">POR MODO</div>
                  <div className="flex gap-4">
                    {Object.entries(replayResult.metrics.by_mode).map(([m, s]) => (
                      <div key={m} className="bg-zinc-800 rounded-md px-3 py-1.5 text-center">
                        <div className="text-amber-400 font-bold text-[11px]">{m}</div>
                        <div className="text-zinc-100 text-[10px]">{s.n} trades</div>
                        <div className={`text-[10px] ${s.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>${fmt(s.pnl, 0)}</div>
                        <div className="text-zinc-500 text-[10px]">{s.n > 0 ? fmtPct(s.wins / s.n) : "—"}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {replayResult.trades?.length > 0 && (
                <div className={`${cardCls} overflow-hidden`}>
                  <div className="px-3 py-2 border-b border-zinc-800 text-[11px] text-zinc-500 font-bold">
                    ÚLTIMOS {Math.min(replayResult.trades.length, 30)} TRADES
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-[10px]">
                      <thead>
                        <tr className="border-b border-zinc-800">
                          {["MODO","DIR","ENTRY","EXIT","SAÍDA","PnL","ORIGEM"].map(h => (
                            <th key={h} className={`px-2 py-1 text-zinc-500 ${["ENTRY","EXIT","SAÍDA","PnL"].includes(h) ? "text-right" : "text-left"}`}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {replayResult.trades.slice(-30).reverse().map((t, i) => (
                          <tr key={i} className="border-b border-zinc-800/10">
                            <td className="px-2 py-0.5 text-amber-400 font-mono">{t.mode}</td>
                            <td className={`px-2 py-0.5 font-mono ${t.action === "BUY" ? "text-green-400" : "text-red-400"}`}>{t.action}</td>
                            <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{fmt(t.entry, 2)}</td>
                            <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{fmt(t.exit, 2)}</td>
                            <td className={`px-2 py-0.5 text-right font-mono ${t.exit_reason === "TP" ? "text-green-400" : t.exit_reason === "SL" ? "text-red-400" : "text-amber-400"}`}>
                              {t.exit_reason}
                            </td>
                            <td className={`px-2 py-0.5 text-right font-bold font-mono ${t.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                              ${fmt(t.pnl, 1)}
                            </td>
                            <td className="px-2 py-0.5">
                              {t.outcome_src === "m1_candle"
                                ? <span className="text-sky-400 font-bold">M1</span>
                                : <span className="text-zinc-600">snap</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════ */}
      {/* TAB: Walk-Forward                                                   */}
      {/* ════════════════════════════════════════════════════════════════════ */}
      {tab === "walkforward" && (
        <div className="p-4">
          {spaceData?.catalogue && (
            <div className={`${cardCls} p-3 mb-3`}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="text-zinc-500 font-bold text-[11px]">ESPAÇO DE BUSCA (WF)</div>
                <div className="text-[9px] text-zinc-500">OPT · FIX · OFF</div>
              </div>
              <CatalogueRows catalogue={spaceData.catalogue} paramStates={paramStates} onChange={handleParamChange} />
            </div>
          )}

          <div className={`${cardCls} p-3 mb-3`}>
            <div className="text-zinc-500 font-bold text-[11px] mb-2.5">WALK-FORWARD — JANELAS DE TREINO/TESTE</div>
            <div className="flex gap-4 flex-wrap">
              {[
                ["Treino (dias)", wfTrainDays, setWfTrainDays, 3, 30],
                ["Teste (dias)",  wfTestDays,  setWfTestDays,  1, 10],
                ["Nº Folds",      wfFolds,     setWfFolds,     2,  8],
              ].map(([label, val, set, min, max]) => (
                <div key={label}>
                  <div className="text-[10px] text-zinc-500 mb-1">{label}</div>
                  <input type="number" min={min} max={max} value={val} onChange={e => set(+e.target.value)}
                    className={`${inputCls} w-14`} />
                </div>
              ))}
              <div className="self-end text-[10px] text-zinc-500">
                Total dias: <span className="text-zinc-100 font-bold">{wfTrainDays + wfFolds * wfTestDays}</span>
              </div>
            </div>
            <div className="mt-2 px-2.5 py-1.5 bg-slate-950 rounded text-[10px] text-zinc-500">
              Para cada fold: optimiza em <span className="text-amber-400">{wfTrainDays} dias</span> de treino,
              avalia out-of-sample em <span className="text-green-400">{wfTestDays} dia(s)</span> de teste.
              Resultado: métricas OOS agregadas de <span className="text-indigo-400">{wfFolds} folds</span>.
            </div>
          </div>

          <button onClick={handleRunWalkForward} disabled={loading}
            className={`px-5 py-2 border-0 rounded-md font-bold text-xs mb-4 transition-colors
              ${loading ? "bg-zinc-800 text-zinc-500 cursor-default" : "bg-indigo-600 hover:bg-indigo-700 text-white cursor-pointer"}`}>
            {loading ? "A processar folds..." : "▶ Executar Walk-Forward"}
          </button>

          {wfResult && (
            <div>
              {wfResult.aggregate && (
                <div className="bg-green-950/40 border border-green-500/20 rounded-lg p-3 mb-3">
                  <div className="text-green-400 font-bold text-[11px] mb-2.5">
                    MÉTRICAS OOS AGREGADAS — {wfResult.n_folds} FOLDS
                  </div>
                  <div className="flex flex-wrap gap-5">
                    <Metric label="Sharpe Médio"   value={fmt(wfResult.aggregate.avg_sharpe, 3)}          color="green" size="large" />
                    <Metric label="Sortino Médio"  value={fmt(wfResult.aggregate.avg_sortino, 3)}          color="green" />
                    <Metric label="Calmar Médio"   value={fmt(wfResult.aggregate.avg_calmar, 3)}           color={(wfResult.aggregate.avg_calmar ?? 0) >= 0.5 ? "green" : "amber"} />
                    <Metric label="Win Rate Médio" value={fmtPct(wfResult.aggregate.avg_win_rate)}         color="green" />
                    <Metric label="PnL OOS Total"  value={`$${fmt(wfResult.aggregate.total_oos_pnl, 0)}`}  color={wfResult.aggregate.total_oos_pnl >= 0 ? "green" : "red"} />
                    <Metric label="PF Médio"       value={fmt(wfResult.aggregate.avg_profit_factor, 2)}    color={wfResult.aggregate.avg_profit_factor > 1.2 ? "green" : "amber"} />
                    <Metric label="Trades OOS"     value={wfResult.aggregate.total_oos_trades}             color="text" />
                    <Metric label="Folds c/ Trades" value={`${wfResult.aggregate.n_folds_with_trades}/${wfResult.n_folds}`} color="amber" />
                    <Metric label="Expectancy"     value={`$${fmt(wfResult.aggregate.avg_expectancy, 1)}`}  color={wfResult.aggregate.avg_expectancy > 0 ? "green" : "red"} />
                    <Metric label="Max DD Médio"   value={`$${fmt(wfResult.aggregate.avg_max_drawdown, 0)}`} color="red" />
                  </div>
                </div>
              )}

              <div className={`${cardCls} overflow-hidden`}>
                <div className="px-3 py-2 border-b border-zinc-800 text-[11px] text-zinc-500 font-bold">RESULTADOS POR FOLD</div>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-[10px]">
                    <thead>
                      <tr className="border-b border-zinc-800">
                        {["Fold","Treino","Teste","Trades OOS","Sharpe","Calmar","Win%","PnL OOS"].map((h, i) => (
                          <th key={h} className={`px-2 py-1 text-zinc-500 ${i >= 3 ? "text-right" : "text-left"}`}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(wfResult.folds || []).map((f, i) => (
                        <tr key={i} className="border-b border-zinc-800/20">
                          <td className="px-2 py-0.5 text-amber-400 font-bold font-mono">{f.fold}</td>
                          <td className="px-2 py-0.5 text-zinc-500 font-mono">{f.train_start?.slice(0,10)} → {f.train_end?.slice(0,10)}</td>
                          <td className="px-2 py-0.5 text-zinc-500 font-mono">{f.test_start?.slice(0,10)} → {f.test_end?.slice(0,10)}</td>
                          <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{f.oos_trades}</td>
                          <td className={`px-2 py-0.5 text-right font-mono ${(f.oos_metrics?.sharpe_ratio ?? 0) > 0 ? "text-green-400" : "text-red-400"}`}>
                            {fmt(f.oos_metrics?.sharpe_ratio, 3)}
                          </td>
                          <td className={`px-2 py-0.5 text-right font-mono ${(f.oos_metrics?.calmar_ratio ?? 0) >= 0.5 ? "text-green-400" : "text-amber-400"}`}>
                            {fmt(f.oos_metrics?.calmar_ratio, 3)}
                          </td>
                          <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{fmtPct(f.oos_metrics?.win_rate)}</td>
                          <td className={`px-2 py-0.5 text-right font-bold font-mono ${(f.oos_metrics?.total_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                            ${fmt(f.oos_metrics?.total_pnl, 0)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {wfResult.folds?.some(f => f.best_params) && (
                  <div className="p-3 border-t border-zinc-800">
                    <div className="text-zinc-500 text-[10px] font-bold mb-2">MELHORES PARÂMETROS IN-SAMPLE POR FOLD</div>
                    {wfResult.folds.map((f, i) => f.best_params && (
                      <div key={i} className="mb-1.5">
                        <span className="text-amber-400 text-[10px] font-bold">Fold {f.fold}: </span>
                        {Object.entries(f.best_params).map(([k, v]) => (
                          <span key={k} className="text-[10px] mr-2.5">
                            <span className="text-zinc-500">{spaceData?.catalogue?.[k]?.label || k}=</span>
                            <span className="text-zinc-100">{typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(3)) : v}</span>
                          </span>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════ */}
      {/* TAB: Schedule                                                       */}
      {/* ════════════════════════════════════════════════════════════════════ */}
      {tab === "schedule" && (
        <div className="p-4">

          <div className={`${cardCls} p-3 mb-3`}>
            <div className="flex items-center justify-between mb-2.5">
              <div className="text-zinc-500 font-bold text-[11px]">ESTADO DO SCHEDULE</div>
              {scheduleDoc?.active && (
                <div className="flex gap-2">
                  <button onClick={handleRunNow} disabled={scheduleRunning}
                    className={`px-2.5 py-0.5 border rounded text-[10px] font-bold cursor-pointer transition-colors
                      ${scheduleRunning ? "bg-zinc-800 border-zinc-700 text-zinc-500" : "bg-green-950/50 border-green-500/30 text-green-400 hover:bg-green-950"}`}>
                    {scheduleRunning ? "A iniciar..." : "▶ Run Agora"}
                  </button>
                  <button onClick={handleDisableSchedule}
                    className="px-2.5 py-0.5 bg-red-950/50 border border-red-500/30 rounded text-red-400 text-[10px] font-bold cursor-pointer hover:bg-red-950 transition-colors">
                    Desactivar
                  </button>
                </div>
              )}
            </div>
            {scheduleDoc?.active ? (
              <div className="flex flex-wrap gap-4">
                <Metric label="Status"      value="ACTIVO" color="green" />
                <Metric label="Próximo Run" value={(scheduleDoc.next_run_at?.slice(0,16)?.replace("T"," ") ?? "—") + " UTC"} color="amber" />
                <Metric label="Último Run"  value={scheduleDoc.last_run_at ? scheduleDoc.last_run_at.slice(0,16).replace("T"," ")+" UTC" : "Nunca"} color="text" />
                <Metric label="Frequência"  value={scheduleDoc.frequency} color="text" />
                <Metric label="Modo"        value={scheduleDoc.mode} color="text" />
                <Metric label="Auto-Apply"  value={scheduleDoc.auto_apply ? "SIM" : "NÃO"} color={scheduleDoc.auto_apply ? "green" : "text"} />
              </div>
            ) : (
              <div className="text-zinc-500 text-[11px]">Nenhum schedule activo.</div>
            )}
          </div>

          <div className={`${cardCls} p-3 mb-3`}>
            <div className="text-zinc-500 font-bold text-[11px] mb-2.5">CONFIGURAR SCHEDULE</div>
            <div className="flex flex-wrap gap-3 mb-3">
              <div>
                <div className="text-[10px] text-zinc-500 mb-1">Frequência</div>
                <select value={schedCfg.frequency} onChange={e => setSchedCfg(p => ({ ...p, frequency: e.target.value }))} className={selectCls}>
                  <option value="daily">Diário</option>
                  <option value="weekly">Semanal</option>
                  <option value="custom_hours">A cada N horas</option>
                </select>
              </div>
              {schedCfg.frequency === "weekly" && (
                <div>
                  <div className="text-[10px] text-zinc-500 mb-1">Dia da semana</div>
                  <select value={schedCfg.day_of_week} onChange={e => setSchedCfg(p => ({ ...p, day_of_week: +e.target.value }))} className={selectCls}>
                    {["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"].map((d, i) => <option key={i} value={i}>{d}</option>)}
                  </select>
                </div>
              )}
              {schedCfg.frequency === "custom_hours" && (
                <div>
                  <div className="text-[10px] text-zinc-500 mb-1">Intervalo (horas)</div>
                  <input type="number" min={1} max={168} value={schedCfg.custom_hours}
                    onChange={e => setSchedCfg(p => ({ ...p, custom_hours: +e.target.value }))}
                    className={`${inputCls} w-14`} />
                </div>
              )}
              {schedCfg.frequency !== "custom_hours" && (
                <div>
                  <div className="text-[10px] text-zinc-500 mb-1">Hora UTC</div>
                  <input type="number" min={0} max={23} value={schedCfg.hour_utc}
                    onChange={e => setSchedCfg(p => ({ ...p, hour_utc: +e.target.value }))}
                    className={`${inputCls} w-12`} />
                </div>
              )}
              <div>
                <div className="text-[10px] text-zinc-500 mb-1">Modo</div>
                <select value={schedCfg.mode} onChange={e => setSchedCfg(p => ({ ...p, mode: e.target.value }))} className={selectCls}>
                  {["ZONES","FLOW"].map(m => <option key={m}>{m}</option>)}
                </select>
              </div>
              <div>
                <div className="text-[10px] text-zinc-500 mb-1">Método</div>
                <select value={schedCfg.method} onChange={e => setSchedCfg(p => ({ ...p, method: e.target.value }))} className={selectCls}>
                  <option value="BAYESIAN">BAYESIAN</option>
                  <option value="GRID">GRID</option>
                </select>
              </div>
              <div>
                <div className="text-[10px] text-zinc-500 mb-1">Objectivo</div>
                <select value={schedCfg.objective} onChange={e => setSchedCfg(p => ({ ...p, objective: e.target.value }))} className={selectCls}>
                  {(spaceData?.objectives || ["sharpe","sortino","calmar","profit_factor","win_rate"]).map(o => <option key={o}>{o}</option>)}
                </select>
              </div>
              <div>
                <div className="text-[10px] text-zinc-500 mb-1">Símbolo</div>
                <select value={schedCfg.symbol} onChange={e => setSchedCfg(p => ({ ...p, symbol: e.target.value }))} className={selectCls}>
                  <option>MNQ</option><option>MES</option>
                </select>
              </div>
            </div>

            <div className="flex flex-wrap gap-3 mb-3">
              {[
                ["Treino (dias)", "train_days",     3,   30],
                ["Teste (dias)",  "test_days",      1,   10],
                ["Nº Folds",      "n_folds",        2,    8],
                ["N Random",      "n_random",       3,   15],
                ["N Iter (BO)",   "n_iter",         5,   40],
                ["Min Snapshots", "min_snapshots", 10,  500],
              ].map(([label, key, min, max]) => (
                <div key={key}>
                  <div className="text-[10px] text-zinc-500 mb-1">{label}</div>
                  <input type="number" min={min} max={max} value={schedCfg[key]}
                    onChange={e => setSchedCfg(p => ({ ...p, [key]: +e.target.value }))}
                    className={`${inputCls} w-16`} />
                </div>
              ))}
            </div>

            <div className="px-2.5 py-2 bg-slate-950 rounded mb-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={schedCfg.auto_apply}
                  onChange={e => setSchedCfg(p => ({ ...p, auto_apply: e.target.checked }))} />
                <span className="text-[11px] text-zinc-100 font-bold">Auto-Apply: aplicar params automaticamente</span>
              </label>
              {schedCfg.auto_apply && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-[10px] text-zinc-500">Limiar de melhoria:</span>
                  <input type="number" min={0} max={100} step={0.5} value={schedCfg.improvement_threshold_pct}
                    onChange={e => setSchedCfg(p => ({ ...p, improvement_threshold_pct: +e.target.value }))}
                    className={`${inputCls} w-14`} />
                  <span className="text-[10px] text-zinc-500">%</span>
                </div>
              )}
            </div>

            <div className="flex items-center gap-3 flex-wrap">
              <button onClick={handleSaveSchedule} disabled={scheduleSaving}
                className={`px-5 py-2 border-0 rounded-md font-bold text-xs transition-colors
                  ${scheduleSaving ? "bg-zinc-800 text-zinc-500 cursor-default" : "bg-indigo-600 hover:bg-indigo-700 text-white cursor-pointer"}`}>
                {scheduleSaving ? "A guardar..." : "💾 Guardar Schedule"}
              </button>
              {scheduleError && (
                <span className="text-red-400 text-[11px]">⚠ {scheduleError}</span>
              )}
              {scheduleDoc?.active && !scheduleError && !scheduleSaving && (
                <span className="text-green-400 text-[11px]">✓ Schedule guardado</span>
              )}
            </div>
          </div>

          {scheduleHistory.length > 0 && (
            <div className={`${cardCls} overflow-hidden`}>
              <div className="px-3 py-2 border-b border-zinc-800 text-[11px] text-zinc-500 font-bold">HISTÓRICO DE RUNS</div>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-[10px]">
                  <thead>
                    <tr className="border-b border-zinc-800">
                      {["ID","Início","Dur (s)","Snaps","Folds","Sharpe Médio","Applied","Status"].map((h, i) => (
                        <th key={h} className={`px-2 py-1 text-zinc-500 ${i >= 2 ? "text-right" : "text-left"}`}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {scheduleHistory.map((h, i) => (
                      <tr key={i} className="border-b border-zinc-800/20">
                        <td className="px-2 py-0.5 text-zinc-500 font-mono">{h.run_id}</td>
                        <td className="px-2 py-0.5 text-zinc-100 font-mono">{h.started_at?.slice(0,16)?.replace("T"," ")}</td>
                        <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{h.duration_s}</td>
                        <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{h.n_snapshots}</td>
                        <td className="px-2 py-0.5 text-zinc-100 text-right font-mono">{h.n_folds}</td>
                        <td className={`px-2 py-0.5 text-right font-mono ${(h.aggregate?.avg_sharpe ?? 0) > 0 ? "text-green-400" : "text-red-400"}`}>
                          {h.aggregate?.avg_sharpe != null ? h.aggregate.avg_sharpe.toFixed(3) : "—"}
                        </td>
                        <td className={`px-2 py-0.5 text-center ${h.auto_applied ? "text-green-400" : "text-zinc-500"}`}>
                          {h.auto_applied ? "✓" : "—"}
                        </td>
                        <td className="px-2 py-0.5 text-center">
                          <span className={`px-1.5 py-px rounded text-[10px]
                            ${h.status === "error" ? "bg-red-950 text-red-400"
                              : h.status === "applied" ? "bg-green-950 text-green-400"
                              : "bg-zinc-800 text-amber-400"}`}>
                            {h.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════ */}
      {/* TAB: Calibração                                                     */}
      {/* ════════════════════════════════════════════════════════════════════ */}
      {tab === "calibration" && (
        <div className="p-4">

          <div className="flex gap-3 flex-wrap items-end mb-4">
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Símbolo</div>
              <select value={calibSymbol} onChange={e => setCalibSymbol(e.target.value)} className={selectCls}>
                <option>MNQ</option><option>MES</option>
              </select>
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Janela (dias)</div>
              <select value={calibDays} onChange={e => setCalibDays(Number(e.target.value))} className={selectCls}>
                {[30,60,90,180,365].map(d => <option key={d} value={d}>{d}d</option>)}
              </select>
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">n mínimo por célula</div>
              <select value={calibMinN} onChange={e => setCalibMinN(Number(e.target.value))} className={selectCls}>
                {[5,10,15,20,30,50].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <button onClick={fetchCalibration} disabled={calibLoading}
              className={`px-3.5 py-1.5 border-0 rounded font-bold text-xs transition-colors
                ${calibLoading ? "bg-zinc-800 text-zinc-500 cursor-default" : "bg-indigo-600 hover:bg-indigo-700 text-white cursor-pointer"}`}>
              {calibLoading ? "A carregar..." : "Actualizar"}
            </button>
          </div>

          {calibData?.summary && (
            <div className={`${cardCls} p-3 mb-4 flex gap-5 flex-wrap`}>
              <Metric label="Trades fechados" value={calibData.summary.total_trades} />
              <Metric label="Zone types"       value={calibData.summary.zone_types_found} />
              <Metric label="Células OK"       value={calibData.summary.cells_sufficient} color={calibData.summary.cells_sufficient > 0 ? "green" : "amber"} />
              <Metric label="Colapsadas"       value={calibData.summary.cells_collapsed}   color="amber" />
              <Metric label="Insuficientes"    value={calibData.summary.cells_insufficient} color="red" />
              <Metric label="n mínimo"         value={calibData.summary.min_n} />
            </div>
          )}

          {calibData && calibData.summary?.total_trades === 0 && (
            <div className={`${cardCls} p-5 text-center`}>
              <div className="text-3xl mb-2">📊</div>
              <div className="text-zinc-500 text-xs">
                Sem trades fechados com <code className="text-amber-400">zone_type</code> gravado.
              </div>
              <div className="mt-1.5 text-[11px] text-zinc-500">
                O campo foi adicionado hoje — os dados acumulam a partir do próximo pregão.
              </div>
            </div>
          )}

          {calibData?.cells?.length > 0 && (() => {
            const grouped = {};
            for (const c of calibData.cells) {
              if (!grouped[c.zone_type]) grouped[c.zone_type] = [];
              grouped[c.zone_type].push(c);
            }
            return (
              <div className="flex flex-col gap-3">
                {Object.entries(grouped).map(([zt, cells]) => {
                  const ztWins  = cells.reduce((s, c) => s + c.wins, 0);
                  const ztTotal = cells.reduce((s, c) => s + c.n, 0);
                  const ztWR    = ztTotal > 0 ? ztWins / ztTotal : null;
                  const ztCls   = ztWR === null ? "text-zinc-500"
                                : ztWR >= 0.60 ? "text-green-400"
                                : ztWR >= 0.40 ? "text-amber-400" : "text-red-400";
                  return (
                    <div key={zt} className={`${cardCls} overflow-hidden`}>
                      <div className="px-3 py-2 border-b border-zinc-800 flex items-center gap-3">
                        <span className="font-mono text-[11px] font-bold text-zinc-100">{zt}</span>
                        <span className={`text-xs font-bold ${ztCls}`}>
                          {ztWR !== null ? `${(ztWR * 100).toFixed(1)}%` : "—"}
                        </span>
                        <span className="text-zinc-500 text-[10px]">{ztTotal} trades</span>
                      </div>
                      <div className="overflow-x-auto">
                        <table className="w-full border-collapse text-[11px]">
                          <thead>
                            <tr className="bg-zinc-950/60">
                              {["Quality","Regime","Fase","n","Wins","Losses","Win Rate",""].map(h => (
                                <th key={h} className={`px-2.5 py-1 text-zinc-500 font-semibold whitespace-nowrap
                                  ${["n","Wins","Losses","Win Rate"].includes(h) ? "text-right" : "text-left"}`}>{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {cells.map((c, i) => {
                              const wr = c.win_rate;
                              const wrCls = wr === null ? "text-zinc-500"
                                          : wr >= 0.60 ? "text-green-400"
                                          : wr >= 0.40 ? "text-amber-400" : "text-red-400";
                              const isInsuf     = c.insufficient;
                              const isCollapsed = c.collapsed_dims?.length > 0;
                              return (
                                <tr key={i} className={`border-b border-zinc-800/20 ${isInsuf ? "opacity-55" : ""}`}>
                                  <td className="px-2.5 py-1 text-zinc-100">
                                    {c.s2_quality === "*" ? <span className="text-zinc-500">*</span> : c.s2_quality}
                                  </td>
                                  <td className="px-2.5 py-1 text-zinc-500 font-mono text-[10px]">
                                    {c.s1_regime === "*" ? <span className="text-zinc-600">*</span> : c.s1_regime}
                                  </td>
                                  <td className="px-2.5 py-1 text-zinc-500">
                                    {c.session_phase === "*" ? <span className="text-zinc-600">*</span> : c.session_phase}
                                  </td>
                                  <td className="px-2.5 py-1 text-zinc-100 text-right font-mono">{c.n}</td>
                                  <td className="px-2.5 py-1 text-green-400 text-right font-mono">{c.wins}</td>
                                  <td className="px-2.5 py-1 text-red-400 text-right font-mono">{c.losses}</td>
                                  <td className={`px-2.5 py-1 text-right font-bold font-mono ${wrCls}`}>
                                    {wr !== null ? `${(wr * 100).toFixed(1)}%` : "—"}
                                  </td>
                                  <td className="px-2.5 py-1">
                                    {isInsuf && (
                                      <span className="text-[9px] text-red-400 border border-red-500/30 rounded px-1 py-px">
                                        n&lt;{calibMinN}
                                      </span>
                                    )}
                                    {isCollapsed && !isInsuf && (
                                      <span className="text-[9px] text-amber-400 border border-amber-500/30 rounded px-1 py-px"
                                        title={`Colapsou: ${c.collapsed_dims.join(", ")}`}>
                                        ↓{c.collapsed_dims.length}D
                                      </span>
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })()}

          {calibData?.cells?.length > 0 && (
            <div className={`${cardCls} p-3 mt-3 text-[10px] text-zinc-500`}>
              <strong className="text-zinc-100">Legenda: </strong>
              <span className="text-green-400">■ ≥60% </span>
              <span className="text-amber-400">■ 40–60% </span>
              <span className="text-red-400">■ &lt;40% </span>
              &nbsp;·&nbsp; <span className="text-amber-400">↓1D/2D/3D</span> = dimensões colapsadas por n insuficiente
              &nbsp;·&nbsp; <span>* = dimensão colapsada (todas as categorias combinadas)</span>
              &nbsp;·&nbsp; <strong>Fases:</strong> OPEN &lt;30min · MID 30–120min · LATE &gt;120min
            </div>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════ */}
      {/* TAB: Diagnóstico                                                    */}
      {/* ════════════════════════════════════════════════════════════════════ */}
      {tab === "diagnostics" && (
        <div className="p-4 overflow-y-auto">

          <div className="flex gap-3 flex-wrap items-end mb-4">
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Símbolo</div>
              <select value={diagSymbol} onChange={e => setDiagSymbol(e.target.value)} className={selectCls}>
                <option>MNQ</option><option>MES</option>
              </select>
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Janela (dias)</div>
              <select value={diagDays} onChange={e => setDiagDays(Number(e.target.value))} className={selectCls}>
                {[7,15,30,60,90].map(d => <option key={d} value={d}>{d}d</option>)}
              </select>
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Modo</div>
              <select value={diagMode} onChange={e => setDiagMode(e.target.value)} className={selectCls}>
                <option value="">Todos</option>
                <option value="ZONES">ZONES</option>
                <option value="FLOW">FLOW</option>
              </select>
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Sessão</div>
              <select value={diagSession} onChange={e => setDiagSession(e.target.value)} className={selectCls}>
                <option value="">Todas</option>
                <option value="NY">Sessão NY (RTH)</option>
                <option value="GLOBEX">Globex (Overnight)</option>
                <option disabled>──────────</option>
                <option value="RTH_OPEN">  RTH Open (09:30–10:29)</option>
                <option value="RTH_MID">  RTH Mid (10:30–13:59)</option>
                <option value="RTH_CLOSE">  RTH Close (14:00–16:14)</option>
                <option value="OVERNIGHT">  Overnight</option>
                <option value="HALTED">  Halted (17:00–17:59)</option>
              </select>
            </div>
            <button onClick={fetchDiagnostics} disabled={diagLoading}
              className={`px-3.5 py-1.5 border-0 rounded font-bold text-xs transition-colors
                ${diagLoading ? "bg-zinc-800 text-zinc-500 cursor-default" : "bg-indigo-600 hover:bg-indigo-700 text-white cursor-pointer"}`}>
              {diagLoading ? "A carregar..." : "Actualizar"}
            </button>
          </div>

          {diagData && diagData.outcome_distribution?.total_snapshots === 0 && (
            <div className={`${cardCls} p-6 text-center`}>
              <div className="text-3xl mb-2">🔍</div>
              <div className="text-zinc-500 text-xs">Sem snapshots no período seleccionado.</div>
              <div className="mt-1.5 text-[11px] text-zinc-500">
                Os snapshots acumulam a cada 30s durante o pregão. Volte após o primeiro dia de trading.
              </div>
            </div>
          )}

          {diagData && diagData.outcome_distribution?.total_snapshots > 0 && (() => {
            const d        = diagData;
            const outcomes = d.outcome_distribution;
            const reasons  = d.s2_block_reasons;
            const zq       = d.zone_quality_distribution;
            const ofi      = d.ofi_slow_impact;
            const sug      = d.suggestions || [];
            const minSample = d.min_sample || 30;

            const qualityOrder = ["STRONG","MODERATE","WEAK","NO_TRADE","NONE"];
            const qualityCls   = {
              STRONG: "text-green-400", MODERATE: "text-amber-400",
              WEAK: "text-red-400", NO_TRADE: "text-zinc-500", NONE: "text-zinc-500",
            };

            return (
              <div className="flex flex-col gap-4">

                {d.session && (
                  <div className="flex items-center gap-2 text-[11px] text-zinc-500">
                    <span>Análise filtrada por sessão:</span>
                    <span className="bg-blue-500/20 text-blue-400 rounded px-2 py-px font-bold text-[11px]">
                      📊 {{ RTH_ALL: "Sessão NY (RTH)", RTH_OPEN: "RTH Open", RTH_MID: "RTH Mid",
                            RTH_CLOSE: "RTH Close", OVERNIGHT: "Overnight", HALTED: "Halted" }[d.session] ?? d.session}
                    </span>
                    <span>· {outcomes.total_snapshots} snapshots</span>
                  </div>
                )}

                {sug.length > 0 && (
                  <div className="flex flex-col gap-1.5">
                    {sug.map((s, i) => {
                      const isOk = s.level === "ok";
                      return (
                        <div key={i} className={`px-3.5 py-2.5 rounded-md border text-[11px] text-zinc-100 flex gap-2.5
                          ${isOk ? "bg-green-950/30 border-green-500/25" : "bg-amber-950/30 border-amber-500/25"}`}>
                          <span>{isOk ? "✅" : "⚠"}</span>
                          <span>{s.message}</span>
                        </div>
                      );
                    })}
                    {outcomes.total_snapshots < minSample && (
                      <div className="text-[10px] text-zinc-500 italic">
                        Sugestões activas a partir de {minSample} snapshots. Actual: {outcomes.total_snapshots}.
                      </div>
                    )}
                  </div>
                )}

                <div className={`${cardCls} p-3 flex gap-5 flex-wrap`}>
                  <Metric label="Snapshots analisados"   value={outcomes.total_snapshots} />
                  <Metric label="Sinais READY"            value={outcomes.total_ready} color="green" />
                  <Metric label="Taxa de disparo global"
                    value={`${outcomes.global_fire_rate}%`}
                    color={outcomes.global_fire_rate >= 20 ? "green" : outcomes.global_fire_rate >= 10 ? "amber" : "red"} />
                  <Metric label="Gates bloqueados (total)" value={reasons.total_mentions} color="red" />
                </div>

                <div className={`${cardCls} overflow-hidden`}>
                  <div className="px-3 py-2 border-b border-zinc-800 text-indigo-400 font-bold text-[11px]">
                    DISTRIBUIÇÃO DE OUTCOME POR REGIME
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-[11px]">
                      <thead>
                        <tr className="border-b border-zinc-800">
                          {["Regime","Total","READY","S2_BLOCKED","S1_NO_DATA","Taxa disparo"].map(h => (
                            <th key={h} className={`px-2.5 py-1.5 text-zinc-500 font-semibold text-[10px] ${h === "Regime" ? "text-left" : "text-right"}`}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {outcomes.rows.map(row => {
                          const frCls = row.fire_rate >= 20 ? "text-green-400" : row.fire_rate >= 10 ? "text-amber-400" : "text-red-400";
                          return (
                            <tr key={row.regime} className="border-b border-zinc-800/20">
                              <td className="px-2.5 py-1 font-mono text-zinc-100 font-bold text-[10px]">{row.regime}</td>
                              <td className="px-2.5 py-1 text-zinc-500 text-right font-mono">{row.total}</td>
                              <td className="px-2.5 py-1 text-green-400 text-right font-mono">{row.ready}</td>
                              <td className="px-2.5 py-1 text-red-400 text-right font-mono">{row.s2_blocked}</td>
                              <td className="px-2.5 py-1 text-zinc-500 text-right font-mono">{row.s1_no_data}</td>
                              <td className={`px-2.5 py-1 text-right font-bold font-mono ${frCls}`}>{row.fire_rate}%</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>

                {reasons.rows.length > 0 && (
                  <div className={`${cardCls} overflow-hidden`}>
                    <div className="px-3 py-2 border-b border-zinc-800 text-indigo-400 font-bold text-[11px]">
                      GATES DE BLOQUEIO S2 — {reasons.total_mentions} menções
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full border-collapse text-[11px]">
                        <thead>
                          <tr className="border-b border-zinc-800">
                            {["Gate / Motivo","Total","% do total","Por regime"].map(h => (
                              <th key={h} className={`px-2.5 py-1.5 text-zinc-500 font-semibold text-[10px] ${h === "Gate / Motivo" ? "text-left" : "text-right"}`}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {reasons.rows.map((row, i) => {
                            const pctCls = row.pct > 55 ? "text-red-400" : row.pct > 30 ? "text-amber-400" : "text-zinc-500";
                            const pctBg  = row.pct > 55 ? "bg-red-400" : row.pct > 30 ? "bg-amber-400" : "bg-zinc-500";
                            const regimenStr = Object.entries(row.by_regime || {})
                              .sort((a,b) => b[1]-a[1]).slice(0,3)
                              .map(([reg, cnt]) => `${reg}: ${cnt}`).join(" · ");
                            return (
                              <tr key={i} className="border-b border-zinc-800/20">
                                <td className="px-2.5 py-1 text-zinc-100 max-w-[240px]">{row.reason}</td>
                                <td className="px-2.5 py-1 text-red-400 text-right">{row.total}</td>
                                <td className={`px-2.5 py-1 text-right font-bold ${pctCls}`}>
                                  {row.pct}%
                                  <span className={`inline-block ml-1.5 h-1.5 rounded-sm opacity-60 align-middle ${pctBg}`}
                                    style={{ width: Math.round(row.pct * 0.6) }} />
                                </td>
                                <td className="px-2.5 py-1 text-zinc-500 text-right text-[9px] font-mono">{regimenStr}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                <div className="flex gap-4 flex-wrap">
                  <div className={`flex-1 min-w-[240px] ${cardCls} overflow-hidden`}>
                    <div className="px-3 py-2 border-b border-zinc-800 text-indigo-400 font-bold text-[11px]">
                      QUALIDADE DE ZONA (ZONES)
                    </div>
                    <div className="p-3">
                      {qualityOrder.filter(q => (zq.quality_totals || {})[q] > 0).map(q => {
                        const cnt = (zq.quality_totals || {})[q] || 0;
                        const pct = (zq.quality_pct || {})[q] || 0;
                        const barCls = qualityCls[q] || "text-zinc-500";
                        const barBg  = q === "STRONG" ? "bg-green-500" : q === "MODERATE" ? "bg-amber-500" : q === "WEAK" ? "bg-red-500" : "bg-zinc-500";
                        return (
                          <div key={q} className="mb-2">
                            <div className="flex justify-between mb-0.5 text-[10px]">
                              <span className={`font-bold ${barCls}`}>{q}</span>
                              <span className="text-zinc-500">{cnt} ({pct}%)</span>
                            </div>
                            <div className="h-1.5 bg-zinc-800 rounded-sm">
                              <div className={`h-full rounded-sm transition-all duration-300 ${barBg}`} style={{ width: `${Math.min(pct,100)}%` }} />
                            </div>
                          </div>
                        );
                      })}
                      {Object.keys(zq.quality_totals || {}).length === 0 && (
                        <div className="text-zinc-500 text-[11px] text-center py-3">Sem dados de qualidade de zona</div>
                      )}
                    </div>
                  </div>

                  <div className={`flex-1 min-w-[240px] ${cardCls} overflow-hidden`}>
                    <div className="px-3 py-2 border-b border-zinc-800 text-indigo-400 font-bold text-[11px]">
                      IMPACTO PENALIDADE OFI SLOW (ZONES)
                    </div>
                    <div className="p-3">
                      {ofi.total > 0 ? (
                        <div className="flex flex-col gap-2.5">
                          {[
                            ["Avaliações ZONES",     ofi.total,                null],
                            ["Com penalty aplicado", `${ofi.with_penalty} (${ofi.penalty_rate}%)`, ofi.penalty_rate > 50 ? "amber" : null],
                            ["Penalty → bloqueio",   `${ofi.would_pass} (${ofi.tipped_into_block_rate}%)`, ofi.tipped_into_block_rate > 40 ? "red" : null],
                          ].map(([lbl, val, col]) => (
                            <div key={lbl} className="flex justify-between items-center">
                              <span className="text-zinc-500 text-[11px]">{lbl}</span>
                              <span className={`text-xs font-bold ${col === "red" ? "text-red-400" : col === "amber" ? "text-amber-400" : "text-zinc-100"}`}>{val}</span>
                            </div>
                          ))}
                          <div className="flex justify-between items-center">
                            <span className="text-zinc-500 text-[11px]">Penalty médio</span>
                            <span className="text-red-400 text-xs font-mono">{ofi.avg_penalty}</span>
                          </div>
                          <div className="flex justify-between items-center">
                            <span className="text-zinc-500 text-[11px]">Score médio</span>
                            <span className="text-zinc-100 text-xs font-mono">{ofi.avg_score}</span>
                          </div>
                          {ofi.tipped_into_block_rate > 40 ? (
                            <div className="mt-1 px-2.5 py-1.5 bg-red-950/40 border border-red-500/25 rounded text-[10px] text-red-400">
                              ⚠ Alta taxa de bloqueio por penalty OFI Slow. Considere relaxar os thresholds no Auto Tune.
                            </div>
                          ) : ofi.with_penalty > 0 ? (
                            <div className="mt-1 px-2.5 py-1.5 bg-green-950/40 border border-green-500/25 rounded text-[10px] text-green-400">
                              ✓ Penalty OFI Slow dentro de parâmetros razoáveis.
                            </div>
                          ) : null}
                        </div>
                      ) : (
                        <div className="text-zinc-500 text-[11px] text-center py-3">
                          Sem avaliações ZONES no período.<br/>
                          <span className="text-[10px]">Filtre por modo ZONES ou aguarde pregões com ZONES activo.</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className={`${cardCls} px-3 py-2 text-[10px] text-zinc-500`}>
                  <strong className="text-zinc-100">Como usar com Auto Tune: </strong>
                  Gates com {'>'} 55% dos bloqueios sugerem ajuste de threshold.
                  Combine com o Replay ZONES para verificar se relaxar o gate mantém win rate.
                  OFI Slow penalty → bloqueio {'>'} 40% sugere reduzir{" "}
                  <span className="text-amber-400">ofi_slow_fade_thresh</span> ou{" "}
                  <span className="text-amber-400">ofi_slow_momentum_thresh</span>.
                </div>

              </div>
            );
          })()}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════ */}
      {/* TAB: Análise Combinada D1×D2                                       */}
      {/* ════════════════════════════════════════════════════════════════════ */}
      {tab === "combined" && (
        <div className="p-4 overflow-y-auto">

          <div className="flex gap-2 flex-wrap mb-3 items-end">
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Símbolo</div>
              <select value={combSymbol} onChange={e => setCombSymbol(e.target.value)} className={selectCls}>
                {["MNQ","MES"].map(o => <option key={o}>{o}</option>)}
              </select>
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Sessão</div>
              <select value={combSession} onChange={e => setCombSession(e.target.value)} className={selectCls}>
                <option value="">Todas (NY + Globex)</option>
                <option value="NY">Sessão NY (RTH)</option>
                <option value="GLOBEX">Globex (Overnight)</option>
                <option disabled>──────────</option>
                <option value="RTH_OPEN">  RTH Open (09:30–10:29 ET)</option>
                <option value="RTH_MID">  RTH Mid (10:30–13:59 ET)</option>
                <option value="RTH_CLOSE">  RTH Close (14:00–16:14 ET)</option>
                <option value="OVERNIGHT">  Overnight</option>
                <option value="HALTED">  Halted (17:00–17:59 ET)</option>
              </select>
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Janela D1 (dias)</div>
              <input type="number" value={combDaysDiag} onChange={e => setCombDaysDiag(+e.target.value)} min={7} max={180}
                className={`${inputCls} w-16`} />
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Janela D2 (dias)</div>
              <input type="number" value={combDaysCal} onChange={e => setCombDaysCal(+e.target.value)} min={7} max={365}
                className={`${inputCls} w-16`} />
            </div>
            <div>
              <div className="text-[10px] text-zinc-500 mb-1">Δ máx/run</div>
              <input type="number" value={combMaxDelta} onChange={e => setCombMaxDelta(+e.target.value)} min={0.01} max={0.20} step={0.01}
                className={`${inputCls} w-16`} />
            </div>
            <button onClick={fetchCombined} disabled={combLoading}
              className={`px-3.5 py-1.5 border-0 rounded font-bold text-xs transition-colors
                ${combLoading ? "bg-zinc-800 text-zinc-500 cursor-default" : "bg-indigo-600 hover:bg-indigo-700 text-white cursor-pointer"}`}>
              {combLoading ? "A analisar..." : "🔄 Analisar"}
            </button>
          </div>

          {!combData && !combLoading && (
            <div className="text-center py-10 text-zinc-500">
              <div className="text-3xl mb-2">🔄</div>
              <div className="text-[13px]">Clique em Analisar para cruzar D1 (diagnóstico) × D2 (calibração)</div>
            </div>
          )}

          {combData && (() => {
            if (combData.mode === "ALL") {
              const renderPanel = (label, borderCls, txtCls, panel) => {
                if (!panel) return null;
                const a   = panel.net_assessment || "INSUFFICIENT_DATA";
                const aCls = assessTextColor(a);
                const al   = { OVER_BLOCKING: "Over-blocking", UNDER_FILTERING: "Under-filtering",
                               BALANCED: "Balanced", INSUFFICIENT_DATA: "Insuf. dados" }[a] || a;
                const sug  = panel.suggestions || [];
                return (
                  <div className={`bg-zinc-900 border ${borderCls} rounded-md p-3`}>
                    <div className={`font-bold text-xs mb-2 ${txtCls}`}>{label}</div>
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-base">{a === "BALANCED" ? "✅" : a === "INSUFFICIENT_DATA" ? "⏳" : "⚠"}</span>
                      <span className={`font-bold text-xs ${aCls}`}>{al}</span>
                    </div>
                    <div className="text-zinc-500 text-[11px] mb-2">
                      {panel.sample?.n_snapshots ?? 0} snapshots · {panel.sample?.n_trades ?? 0} trades
                    </div>
                    <div className="text-zinc-500 text-[10px] mb-1.5">
                      D1: FR={((panel.dimension_1?.fire_rate ?? 0)*100).toFixed(1)}% · D2: WR={((panel.dimension_2?.overall_win_rate ?? 0)*100).toFixed(0)}% PF={panel.dimension_2?.overall_profit_factor?.toFixed(2) ?? "—"}
                    </div>
                    {sug.length === 0
                      ? <div className="text-zinc-500 text-[11px]">Sem sugestões</div>
                      : sug.slice(0,4).map((s, i) => (
                          <div key={i} className="mb-1 text-[11px]">
                            <span className="text-zinc-100 font-bold">{s.param}: </span>
                            <span className="text-zinc-500">
                              {typeof s.current === "number" ? s.current.toFixed(3) : s.current} →{" "}
                            </span>
                            <span className={`font-bold ${s.delta < 0 ? "text-green-400" : "text-red-400"}`}>
                              {typeof s.suggested === "number" ? s.suggested.toFixed(3) : s.suggested}
                            </span>
                          </div>
                        ))
                    }
                    {sug.length > 4 && <div className="text-zinc-500 text-[10px] mt-1">+{sug.length - 4} mais sugestões</div>}
                  </div>
                );
              };
              return (
                <>
                  <div className={`${cardCls} px-3.5 py-2 mb-2.5 flex items-center gap-2`}>
                    <span className="text-[15px]">🔀</span>
                    <div>
                      <span className="font-bold text-xs text-zinc-100">Análise Dual — NY + Globex</span>
                      <span className="text-zinc-500 text-[11px] ml-2.5">
                        gerado {new Date(combData.generated_at).toLocaleTimeString()}
                      </span>
                    </div>
                    <div className="ml-auto text-zinc-500 text-[11px]">
                      Seleccione "Sessão NY" ou "Globex" para ver params e aplicar
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2.5">
                    {renderPanel("Sessão NY (RTH)",     "border-blue-500/30",   "text-blue-400",   combData.ny)}
                    {renderPanel("Globex (Overnight)",  "border-purple-500/30", "text-purple-400", combData.globex)}
                  </div>
                </>
              );
            }

            const d1  = combData.dimension_1 || {};
            const d2  = combData.dimension_2 || {};
            const cur = combData.current_params || {};
            const sug = combData.suggestions || [];
            const assessment = combData.net_assessment || "INSUFFICIENT_DATA";

            const aCls  = assessTextColor(assessment);
            const aBdr  = assessBorderColor(assessment);
            const aLabel = {
              OVER_BLOCKING:     "Over-blocking — sistema demasiado restritivo",
              UNDER_FILTERING:   "Under-filtering — sistema demasiado permissivo",
              BALANCED:          "Balanced — parâmetros equilibrados",
              INSUFFICIENT_DATA: "Dados insuficientes para avaliação",
            }[assessment] || assessment;

            return (
              <>
                <div className={`bg-zinc-900 border ${aBdr} rounded-md px-3.5 py-2.5 mb-3 flex items-center gap-2.5`}>
                  <span className="text-lg">{assessment === "BALANCED" ? "✅" : assessment === "INSUFFICIENT_DATA" ? "⏳" : "⚠"}</span>
                  <div>
                    <div className={`font-bold text-[13px] ${aCls}`}>{aLabel}</div>
                    <div className="text-zinc-500 text-[11px] mt-0.5">
                      {combData.sample?.n_snapshots ?? 0} snapshots (D1) · {combData.sample?.n_trades ?? 0} trades (D2)
                      {combData.session && (
                        <span className="ml-2 bg-blue-500/20 text-blue-400 rounded px-1.5 font-bold text-[10px]">
                          📊 {{ NY: "Sessão NY (RTH)", GLOBEX: "Globex", RTH_ALL: "Sessão NY (RTH)", RTH_OPEN: "RTH Open", RTH_MID: "RTH Mid",
                               RTH_CLOSE: "RTH Close", OVERNIGHT: "Overnight", HALTED: "Halted" }[combData.session] ?? combData.session}
                        </span>
                      )}
                      {" · gerado "}{new Date(combData.generated_at).toLocaleTimeString()}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2.5 mb-3">
                  <div className={`${cardCls} p-3`}>
                    <div className="text-indigo-400 font-bold text-[11px] mb-2.5">D1 — Diagnóstico de Sinais ({combDaysDiag}d)</div>
                    {[
                      ["Fire Rate Global",  `${d1.global_fire_rate ?? "—"}%`,   d1.global_fire_rate < 20 ? "red" : d1.global_fire_rate > 55 ? "amber" : "green"],
                      ["S2 Bloqueado",      `${d1.s2_blocked_rate ?? "—"}%`,    d1.s2_blocked_rate > 40 ? "red" : "green"],
                      ["OFI Slow Tipping",  `${d1.ofi_slow_tipped_rate ?? "—"}%`, d1.ofi_slow_tipped_rate > 40 ? "amber" : "green"],
                      ["Top Block Reason",  d1.top_block_reason || "—",         ""],
                    ].map(([lbl, val, col]) => (
                      <div key={lbl} className="flex justify-between items-center mb-1.5">
                        <span className="text-zinc-500 text-[11px]">{lbl}</span>
                        <span className={`font-semibold text-[11px] max-w-[140px] text-right
                          ${col === "red" ? "text-red-400" : col === "amber" ? "text-amber-400" : col === "green" ? "text-green-400" : "text-zinc-100"}`}>
                          {val}
                        </span>
                      </div>
                    ))}
                  </div>

                  <div className={`${cardCls} p-3`}>
                    <div className="text-amber-400 font-bold text-[11px] mb-2.5">D2 — Calibração de Edge ({combDaysCal}d)</div>
                    <div className="text-[10px] text-zinc-500 font-semibold mb-1 tracking-widest">GLOBAL (n={d2.n_trades ?? 0})</div>
                    {[
                      ["Expectativa (EV)", d2.overall_avg_pnl != null ? `${d2.overall_avg_pnl >= 0 ? "+" : ""}${d2.overall_avg_pnl.toFixed(3)} pts` : "—",
                        d2.overall_avg_pnl == null ? "" : d2.overall_avg_pnl < 0 ? "red" : d2.overall_avg_pnl >= 0.30 ? "green" : "amber"],
                      ["Profit Factor", d2.overall_profit_factor != null ? d2.overall_profit_factor.toFixed(2) : "—",
                        d2.overall_profit_factor == null ? "" : d2.overall_profit_factor < 1.0 ? "red" : d2.overall_profit_factor >= 1.15 ? "green" : "amber"],
                      ["Win/Loss ratio", d2.overall_wl_ratio != null ? d2.overall_wl_ratio.toFixed(2) : "—",
                        d2.overall_wl_ratio == null ? "" : d2.overall_wl_ratio < 1.0 ? "red" : d2.overall_wl_ratio >= 1.10 ? "green" : "amber"],
                      ["Win Rate", d2.overall_win_rate != null ? `${d2.overall_win_rate}%` : "—",
                        d2.overall_win_rate == null ? "" : d2.overall_win_rate < 40 ? "red" : d2.overall_win_rate >= 45 ? "green" : "amber"],
                    ].map(([lbl, val, col]) => (
                      <div key={lbl} className="flex justify-between items-center mb-1">
                        <span className="text-zinc-500 text-[11px]">{lbl}</span>
                        <span className={`font-semibold text-[11px] ${col === "red" ? "text-red-400" : col === "amber" ? "text-amber-400" : col === "green" ? "text-green-400" : "text-zinc-100"}`}>{val}</span>
                      </div>
                    ))}
                    <div className="border-t border-zinc-800 my-2" />
                    <div className="text-[10px] text-zinc-500 font-semibold mb-1.5 tracking-widest">POR SEGMENTO</div>
                    {[
                      ["ZONES",    d2.zones_avg_pnl,    d2.zones_profit_factor,    d2.zones_wl_ratio,    d2.zones_win_rate,    d2.n_zones],
                      ["STRONG",   d2.strong_avg_pnl,   d2.strong_profit_factor,   null,                 d2.strong_win_rate,   d2.n_strong],
                      ["MODERATE", d2.moderate_avg_pnl, d2.moderate_profit_factor, d2.moderate_wl_ratio, d2.moderate_win_rate, d2.n_moderate],
                    ].map(([seg, ev, pf, wl, wr, n]) => {
                      const evCls = ev  == null ? "text-zinc-500" : ev  < 0    ? "text-red-400" : ev  >= 0.30 ? "text-green-400" : "text-amber-400";
                      const pfCls = pf  == null ? "text-zinc-500" : pf  < 1.0  ? "text-red-400" : pf  >= 1.15 ? "text-green-400" : "text-amber-400";
                      const wlCls = wl  == null ? "text-zinc-500" : wl  < 1.0  ? "text-red-400" : wl  >= 1.10 ? "text-green-400" : "text-amber-400";
                      const wrCls = wr  == null ? "text-zinc-500" : wr  < 40   ? "text-red-400" : wr  >= 45   ? "text-green-400" : "text-amber-400";
                      return (
                        <div key={seg} className="mb-2">
                          <div className="text-zinc-100 font-bold text-[10px] mb-0.5">
                            {seg} <span className="text-zinc-500 font-normal">n={n ?? 0}</span>
                          </div>
                          <div className="flex gap-2 flex-wrap">
                            <span className="text-[10px]"><span className="text-zinc-500">EV </span><span className={`font-semibold ${evCls}`}>{ev != null ? `${ev >= 0 ? "+" : ""}${ev.toFixed(3)}` : "—"}</span></span>
                            <span className="text-[10px]"><span className="text-zinc-500">PF </span><span className={`font-semibold ${pfCls}`}>{pf != null ? pf.toFixed(2) : "—"}</span></span>
                            {wl != null && <span className="text-[10px]"><span className="text-zinc-500">W/L </span><span className={`font-semibold ${wlCls}`}>{wl.toFixed(2)}</span></span>}
                            <span className="text-[10px]"><span className="text-zinc-500">WR </span><span className={`font-semibold ${wrCls}`}>{wr != null ? `${wr}%` : "—"}</span></span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className={`${cardCls} p-3 mb-3`}>
                  <div className="text-zinc-500 font-bold text-[11px] mb-2">PARÂMETROS ACTUAIS</div>
                  <div className="flex gap-4 flex-wrap">
                    {Object.entries(cur).map(([k, v]) => (
                      <div key={k} className="text-center">
                        <div className="text-zinc-100 font-bold text-[13px]">
                          {v !== null && v !== undefined ? (typeof v === "number" ? v.toFixed(3) : v) : "—"}
                        </div>
                        <div className="text-zinc-500 text-[9px] mt-0.5">{k.replace("zones_","").replace(/_/g," ")}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {sug.length === 0 ? (
                  <div className="bg-green-950/40 border border-green-500/25 rounded-md px-3.5 py-2.5 text-green-400 text-xs mb-3">
                    ✅ Sem sugestões — parâmetros dentro dos limites esperados para os dados actuais.
                  </div>
                ) : (
                  <div className="mb-3">
                    <div className="text-zinc-100 font-bold text-[11px] mb-2">SUGESTÕES DE AJUSTE ({sug.length})</div>
                    {sug.map((s, i) => (
                      <div key={i} className="bg-zinc-900 border border-amber-500/25 rounded-md p-3 mb-2">
                        <div className="flex justify-between items-center mb-1.5">
                          <span className="text-amber-400 font-bold text-xs">
                            {s.param.replace("zones_","").replace(/_/g," ").toUpperCase()}
                          </span>
                          <span className="flex gap-2 items-center">
                            <Badge label={s.confidence === "high" ? "Alta confiança" : "Média confiança"} color={s.confidence === "high" ? "green" : "amber"} />
                            <Badge label={s.dimension} color="" />
                          </span>
                        </div>
                        <div className="flex gap-4 mb-2">
                          <div className="text-center">
                            <div className="text-zinc-500 text-[10px]">ACTUAL</div>
                            <div className="text-zinc-100 font-bold text-sm">{s.current !== null ? (typeof s.current === "number" ? s.current.toFixed(3) : s.current) : "—"}</div>
                          </div>
                          <div className="text-zinc-500 text-lg leading-8">→</div>
                          <div className="text-center">
                            <div className="text-zinc-500 text-[10px]">SUGERIDO</div>
                            <div className={`font-bold text-sm ${s.delta < 0 ? "text-green-400" : "text-red-400"}`}>
                              {typeof s.suggested === "number" ? s.suggested.toFixed(3) : s.suggested}
                            </div>
                          </div>
                          {s.delta !== null && (
                            <div className="text-center">
                              <div className="text-zinc-500 text-[10px]">DELTA</div>
                              <div className={`font-bold text-sm ${s.delta < 0 ? "text-green-400" : "text-red-400"}`}>
                                {s.delta > 0 ? "+" : ""}{typeof s.delta === "number" ? s.delta.toFixed(3) : s.delta}
                              </div>
                            </div>
                          )}
                        </div>
                        <div className="text-zinc-500 text-[11px] leading-relaxed">{s.rationale}</div>
                      </div>
                    ))}

                    <div className="flex gap-2 mt-2.5">
                      <button onClick={() => handleApplyCombined(true)} disabled={combApplying}
                        className="bg-zinc-900 text-zinc-100 border border-zinc-700 rounded px-3.5 py-1.5 cursor-pointer text-xs hover:bg-zinc-800 transition-colors">
                        {combApplying ? "..." : "🔍 Simular (dry run)"}
                      </button>
                      <button onClick={() => handleApplyCombined(false)} disabled={combApplying}
                        className="bg-red-600 hover:bg-red-700 text-white border-0 rounded px-3.5 py-1.5 cursor-pointer text-xs font-bold transition-colors">
                        {combApplying ? "A aplicar..." : "⚡ Aplicar agora"}
                      </button>
                    </div>

                    {combApplyMsg && (
                      <div className={`mt-2.5 ${cardCls} p-2.5 text-[11px]`}>
                        {combApplyMsg.dry_run ? (
                          <div className="text-amber-400">
                            Simulação — seriam aplicados: {combApplyMsg.would_apply?.join(", ") || "nenhum"}
                          </div>
                        ) : (
                          <>
                            <div className="text-green-400">
                              Aplicados: {combApplyMsg.applied?.map(a => `${a.param}=${a.new_value}`).join(", ") || "nenhum"}
                            </div>
                            {combApplyMsg.skipped?.length > 0 && (
                              <div className="text-amber-400 mt-1">
                                Ignorados: {combApplyMsg.skipped.map(s => s.param).join(", ")}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </>
            );
          })()}

          {/* ── Schedule Combinado ────────────────────────────────────────── */}
          <div className="border-t border-zinc-800 pt-3.5 mt-1">
            <div className="text-zinc-500 font-bold text-[11px] mb-2.5">SCHEDULE AUTOMÁTICO D1×D2</div>

            {combSched && (
              <div className={`${cardCls} px-3 py-2 mb-2.5 text-[11px] text-zinc-500`}>
                Próximo run: <strong className="text-zinc-100">{combSched.next_run_at ? new Date(combSched.next_run_at).toLocaleString() : "—"}</strong>
                {" · "}Último: <strong className="text-zinc-100">{combSched.last_run_at ? new Date(combSched.last_run_at).toLocaleString() : "—"}</strong>
                {" · "}Auto-apply: <strong className={combSched.auto_apply ? "text-green-400" : "text-zinc-500"}>{combSched.auto_apply ? "ON" : "OFF"}</strong>
              </div>
            )}

            <div className="grid grid-cols-3 gap-2 mb-2.5">
              {[
                ["Frequência",     "frequency",    "select", ["daily","weekly","custom_hours"]],
                ["Hora UTC",       "hour_utc",     "number"],
                ["Δ máx/run",      "max_delta",    "number"],
                ["Min Snapshots",  "min_snapshots","number"],
                ["Min Trades",     "min_trades",   "number"],
                ["Janela D1 (dias)","days_diag",   "number"],
                ["Janela D2 (dias)","days_cal",    "number"],
              ].map(([lbl, key, type, opts]) => (
                <div key={key}>
                  <div className="text-[10px] text-zinc-500 mb-1">{lbl}</div>
                  {type === "select" ? (
                    <select value={combSchedCfg[key] || ""} onChange={e => setCombSchedCfg(s => ({ ...s, [key]: e.target.value }))}
                      className={`${selectCls} w-full`}>
                      {(opts || []).map(o => <option key={o}>{o}</option>)}
                    </select>
                  ) : (
                    <input type={type} value={combSchedCfg[key] ?? ""} step={key === "max_delta" ? 0.01 : 1}
                      onChange={e => setCombSchedCfg(s => ({ ...s, [key]: +e.target.value }))}
                      className={`${inputCls} w-full`} />
                  )}
                </div>
              ))}
              <div className="flex flex-col justify-center">
                <label className="flex items-center gap-1.5 text-xs text-zinc-100 cursor-pointer">
                  <input type="checkbox" checked={!!combSchedCfg.auto_apply}
                    onChange={e => setCombSchedCfg(s => ({ ...s, auto_apply: e.target.checked }))} />
                  Auto-apply
                </label>
                <div className="text-[10px] text-zinc-500 mt-0.5">Aplica sugestões automaticamente</div>
              </div>
            </div>

            <div className="flex gap-2">
              <button onClick={handleSaveCombinedSchedule}
                className="bg-indigo-600 hover:bg-indigo-700 text-white border-0 rounded px-3.5 py-1.5 cursor-pointer text-xs font-bold transition-colors">
                💾 Salvar Schedule
              </button>
              <button onClick={handleRunCombinedNow} disabled={combLoading}
                className={`border border-zinc-700 rounded px-3.5 py-1.5 cursor-pointer text-xs transition-colors
                  ${combLoading ? "bg-zinc-800 text-zinc-500" : "bg-zinc-900 text-zinc-100 hover:bg-zinc-800"}`}>
                ▶ Executar agora
              </button>
              <button onClick={() => fetch(`${API}/tune/combined/schedule`, { method: "DELETE" }).then(fetchCombinedSchedule)}
                className="bg-transparent border border-red-500/30 text-red-400 hover:bg-red-950/30 rounded px-2.5 py-1.5 cursor-pointer text-xs transition-colors">
                Desactivar
              </button>
            </div>

            {combHistory.length > 0 && (
              <div className="mt-3.5">
                <div className="text-zinc-500 font-bold text-[11px] mb-2">HISTÓRICO DE RUNS</div>
                {combHistory.map((h, i) => (
                  <div key={i} className={`${cardCls} px-2.5 py-1.5 mb-1.5 flex justify-between items-center`}>
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-500 text-[10px]">{new Date(h.run_at).toLocaleString()}</span>
                      {h.triggered === "manual" && <Badge label="manual" color="" />}
                      {h.auto_applied && <Badge label="auto-aplicado" color="green" />}
                    </div>
                    <div className="flex gap-3">
                      <span className="text-[11px]">
                        {h.analysis?.net_assessment ? (
                          <span className={
                            h.analysis.net_assessment === "BALANCED" ? "text-green-400"
                            : h.analysis.net_assessment === "INSUFFICIENT_DATA" ? "text-zinc-500"
                            : "text-amber-400"
                          }>
                            {h.analysis.net_assessment}
                          </span>
                        ) : "—"}
                      </span>
                      <span className="text-zinc-500 text-[11px]">{h.analysis?.suggestions?.length ?? 0} sugestões</span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="mt-3.5 bg-indigo-950/30 border border-indigo-500/20 rounded px-3.5 py-2.5">
              <div className="text-indigo-400 font-bold text-[11px] mb-1">Como funciona a Análise Combinada</div>
              <div className="text-zinc-500 text-[11px] leading-relaxed">
                <strong className="text-zinc-100">D1 (Diagnóstico)</strong> mede a frequência e causas de bloqueio via snapshots:
                fire rate, S2 block reasons, OFI Slow tipping.
                <br />
                <strong className="text-zinc-100">D2 (Calibração)</strong> mede o edge real via trades fechados: expectativa/trade (EV pts),
                profit factor (gross_profit/gross_loss), win/loss ratio, e win rate — globais e por segmento (ZONES / STRONG / MODERATE).
                <br /><br />
                As regras de inferência exigem acordo entre as duas dimensões antes de gerar uma sugestão.
                Por exemplo: só sugere relaxar OFI Slow threshold se <em>simultaneamente</em> o tipping rate for alto (D1) E o edge
                em ZONES for confirmado por EV + PF (D2). Cada run com auto_apply aplica um delta máximo por parâmetro, prevenindo oscilações.
              </div>
            </div>
          </div>

        </div>
      )}

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <div className="px-4 py-2 border-t border-zinc-800 text-zinc-500 text-[10px] flex gap-4">
        <span>Modo: <strong className="text-amber-400">{mode}</strong></span>
        <span>Método: <strong className="text-indigo-400">{method}</strong></span>
        <span>Objectivo: <strong className="text-zinc-100">{objective}</strong></span>
        <span>Símbolo: <strong className="text-zinc-100">{symbol}</strong></span>
      </div>
    </div>
  );
}
