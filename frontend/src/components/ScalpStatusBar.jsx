import { useState, useEffect, useRef } from "react";
import {
  CheckCircle, Warning, XCircle, Clock,
  WifiHigh, WifiSlash, ArrowsCounterClockwise, Moon,
} from "@phosphor-icons/react";

const API = `${process.env.REACT_APP_BACKEND_URL || ""}/api`;
const POLL_MS = 15000;

function authHeaders() {
  const token = localStorage.getItem("session_token") || "";
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const OVERALL_STYLES = {
  ok:      { border: "border-emerald-500/25", bg: "bg-emerald-500/[0.04]", dot: "bg-emerald-400",  text: "text-emerald-400", pulse: true  },
  warning: { border: "border-yellow-500/30",  bg: "bg-yellow-500/[0.05]",  dot: "bg-yellow-400",   text: "text-yellow-400",  pulse: false },
  error:   { border: "border-red-500/30",     bg: "bg-red-500/[0.06]",     dot: "bg-red-400",      text: "text-red-400",     pulse: true  },
  closed:  { border: "border-zinc-700/30",    bg: "bg-transparent",        dot: "bg-zinc-600",     text: "text-zinc-500",    pulse: false },
  idle:    { border: "border-zinc-700/40",    bg: "bg-transparent",        dot: "bg-zinc-600",     text: "text-zinc-400",    pulse: false },
};

function overallIcon(overall) {
  if (overall === "ok")      return <CheckCircle size={12} weight="fill" className="text-emerald-400 shrink-0" />;
  if (overall === "warning") return <Warning     size={12} weight="fill" className="text-yellow-400 shrink-0" />;
  if (overall === "error")   return <XCircle     size={12} weight="fill" className="text-red-400    shrink-0" />;
  if (overall === "closed")  return <Moon        size={12} weight="fill" className="text-zinc-500   shrink-0" />;
  return                            <Clock       size={12}               className="text-zinc-500   shrink-0" />;
}

function isMarketClosed(session, feed) {
  if (!session && !feed) return false;
  if (session?.is_weekend) return true;
  const s = session?.cme_session;
  if (s === "halted" || s === "closed") return true;
  const feedStates = Object.values(feed || {}).map(f => f?.state);
  return feedStates.length > 0 && feedStates.every(st => st === "MARKET_CLOSED" || st === "CLOSED");
}

function nextSessionHint(session) {
  if (session?.is_weekend) return "Globex abre dom 18h ET";
  if (session?.cme_session === "halted") return "Globex abre 18h ET";
  return "Aguardando próxima sessão";
}

function feedChip(sym, f) {
  if (!f) return null;
  const ok     = f.state === "LIVE" || f.state === "OK";
  const closed = f.state === "CLOSED" || f.state === "MARKET_CLOSED";
  const ghost  = f.state === "FEED_GHOST" || f.state === "FEED_LOW_QUALITY";
  const warm   = f.state === "WARMING_UP";

  const chipColor = ok     ? "text-emerald-400 border-emerald-500/20 bg-emerald-500/5"
                  : ghost  ? "text-red-400     border-red-500/20     bg-red-500/5"
                  : warm   ? "text-yellow-400  border-yellow-500/20  bg-yellow-500/5"
                  : closed ? "text-zinc-600    border-zinc-700/30    bg-transparent"
                  :          "text-zinc-500    border-zinc-700/30    bg-transparent";

  const stateLabel = ok ? "LIVE" : warm ? "⚡ Aqu." : ghost ? "⚠ Ghost" : closed ? "—" : f.state;

  return (
    <div key={sym} className={`flex items-center gap-1 px-1.5 py-0.5 border text-[10px] font-mono ${chipColor}`}>
      {f.connected
        ? <WifiHigh  size={9} className={ok ? "text-emerald-500" : "text-zinc-500"} />
        : <WifiSlash size={9} className="text-zinc-600" />
      }
      <span className="font-semibold">{sym}</span>
      <span className="text-zinc-600 font-normal">·</span>
      <span>{stateLabel}</span>
      {ok && f.trades_received > 0 && (
        <span className="text-zinc-600 hidden xl:inline">
          {f.trades_received >= 1000 ? `${(f.trades_received/1000).toFixed(1)}k` : f.trades_received}t
        </span>
      )}
    </div>
  );
}

function sessionChip(session) {
  if (!session) return null;
  const { cme_session, is_weekend } = session;
  const isHalted = is_weekend || cme_session === "halted";
  const label = isHalted        ? "WE"
              : cme_session === "rth"    ? "RTH"
              : cme_session === "globex" ? "GLOBEX"
              : cme_session === "closed" ? "—"
              : (cme_session || "?").toUpperCase();
  const color = isHalted             ? "text-zinc-600 border-zinc-700/30"
              : cme_session === "rth" ? "text-emerald-400 border-emerald-500/25 bg-emerald-500/5"
              : cme_session === "globex" ? "text-blue-400 border-blue-500/25 bg-blue-500/5"
              : "text-zinc-600 border-zinc-700/30";
  return (
    <span className={`px-1.5 py-0.5 border text-[10px] font-mono font-bold ${color}`}>
      {label}
    </span>
  );
}

function atChip(at) {
  if (!at) return null;
  if (at.circuit_breaker) {
    return <span className="px-1.5 py-0.5 border border-red-500/30 bg-red-500/10 text-red-400 text-[10px] font-mono font-bold">CB!</span>;
  }
  if (at.running) {
    const pnl = at.pnl_today_usd;
    const pnlStr = pnl != null ? ` ${pnl >= 0 ? "+" : ""}$${Math.round(Math.abs(pnl))}` : "";
    return (
      <span className="px-1.5 py-0.5 border border-emerald-500/25 bg-emerald-500/5 text-emerald-400 text-[10px] font-mono font-semibold">
        AUTO{at.trades_today > 0 ? ` ${at.trades_today}t${pnlStr}` : ""}
      </span>
    );
  }
  return <span className="text-[10px] font-mono text-zinc-600">Auto: off</span>;
}

export default function ScalpStatusBar() {
  const [data,    setData]    = useState(null);
  const [err,     setErr]     = useState(false);
  const [age,     setAge]     = useState(null);
  const [errAge,  setErrAge]  = useState(0);
  const lastGoodRef           = useRef(null);
  const pollRef               = useRef(null);
  const ageRef                = useRef(null);
  const errAgeRef             = useRef(null);

  async function load() {
    try {
      const r = await fetch(`${API}/system/status`, { headers: authHeaders() });
      if (!r.ok) throw new Error(r.status);
      const json = await r.json();
      setData(json);
      lastGoodRef.current = json;
      setErr(false);
      setErrAge(0);
      clearInterval(errAgeRef.current);
      errAgeRef.current = null;
      setAge(0);
    } catch {
      setErr(true);
      errAgeRef.current = errAgeRef.current || setInterval(() => setErrAge(a => a + 1), 1000);
    }
  }

  useEffect(() => {
    load();
    pollRef.current = setInterval(load, POLL_MS);
    return () => { clearInterval(pollRef.current); clearInterval(errAgeRef.current); };
  }, []);

  useEffect(() => {
    if (age === null) return;
    ageRef.current = setInterval(() => setAge(a => a + 1), 1000);
    return () => clearInterval(ageRef.current);
  }, [age]);

  const last = lastGoodRef.current;

  let overall, summary, icon;

  if (!err) {
    overall = data?.overall || "idle";
    summary = data?.summary  || "Carregando…";
    if (isMarketClosed(data?.session, data?.feed)) {
      overall = "closed";
      summary = `Mercado fechado — ${nextSessionHint(data?.session)}`;
    }
  } else {
    if (isMarketClosed(last?.session, last?.feed)) {
      overall = "closed";
      const hint = nextSessionHint(last?.session);
      const stalePart = errAge > 30 ? ` · sem resposta há ${errAge}s` : "";
      summary = `Mercado fechado — ${hint}${stalePart}`;
    } else {
      overall = "error";
      summary = errAge > 0
        ? `Sem resposta do backend — ${errAge}s`
        : "Sem resposta do backend";
    }
  }

  const st = OVERALL_STYLES[overall] || OVERALL_STYLES.idle;
  const displayData = err ? last : data;

  return (
    <div className={`flex items-center gap-3 px-3 py-2 border ${st.border} ${st.bg} transition-colors duration-700`}>

      <div className="flex items-center gap-2 flex-1 min-w-0">
        <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${st.dot} ${st.pulse ? "animate-pulse" : ""}`} />
        {overallIcon(overall)}
        <span className={`text-[11px] font-medium truncate ${st.text}`}>{summary}</span>
      </div>

      <div className="flex items-center gap-1.5 shrink-0">
        {["MNQ", "MES"].map(sym => feedChip(sym, displayData?.feed?.[sym]))}
      </div>

      <div className="shrink-0">
        {sessionChip(displayData?.session)}
      </div>

      <div className="shrink-0">
        {displayData ? atChip(displayData.auto_trader) : null}
      </div>

      <button
        onClick={load}
        className="flex items-center gap-1 text-[9px] text-zinc-700 hover:text-zinc-500 transition-colors shrink-0"
        title="Atualizar status"
      >
        <ArrowsCounterClockwise size={10} />
        {age !== null ? `${age}s` : "—"}
      </button>
    </div>
  );
}
