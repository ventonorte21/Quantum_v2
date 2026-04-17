import { useMemo } from "react";
import {
  Gauge, Heartbeat, ChartLineUp, ChartBar, Warning, Lightning,
  CaretRight, CaretDown
} from "@phosphor-icons/react";

const INDICATOR_ICONS = {
  'RSI': Gauge, 'MACD': Heartbeat, 'EMA_CROSS': ChartLineUp,
  'BOLLINGER': ChartBar, 'VOLUME_PROFILE': ChartBar,
  'VIX': Warning, 'GAMMA_EXPOSURE': Lightning,
};

const SIGNAL_COLORS = {
  'LONG': 'bg-emerald-500/20 text-emerald-400',
  'SHORT': 'bg-red-500/20 text-red-400',
  'CONFIRM': 'bg-emerald-500/20 text-emerald-400',
  'WEAK': 'bg-zinc-500/20 text-zinc-400',
  'CAUTION': 'bg-yellow-500/20 text-yellow-400',
  'NEUTRAL': 'bg-zinc-500/20 text-zinc-400',
};

function IndicatorDetails({ data }) {
  const entries = useMemo(
    () => Object.entries(data).filter(([k]) => !['signal', 'score'].includes(k)),
    [data]
  );
  return (
    <div className="grid grid-cols-2 gap-2 mt-2 text-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="flex justify-between">
          <span className="text-zinc-500 capitalize">{key.replace('_', ' ')}:</span>
          <span className="font-mono text-zinc-300">
            {typeof value === 'number' ? value.toLocaleString() : String(value)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function IndicatorCard({ name, data, expanded, onToggle, isReal }) {
  const Icon = INDICATOR_ICONS[name] || Heartbeat;
  const badgeColor = SIGNAL_COLORS[data.signal] || SIGNAL_COLORS['NEUTRAL'];

  return (
    <div className={`border bg-[#121214]/50 ${isReal ? 'border-blue-500/30' : 'border-zinc-800/40'}`}>
      <button
        onClick={onToggle}
        data-testid={`indicator-${name.toLowerCase()}`}
        className="w-full p-3 flex items-center justify-between hover:bg-zinc-800/30 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-blue-400">
            <Icon size={14} />
          </span>
          <span className="text-xs font-semibold">{name.replace('_', ' ')}</span>
          {isReal && data.source && (
            <span className="text-[8px] px-1 py-0.5 bg-blue-500/20 text-blue-400 uppercase">REAL</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] px-2 py-0.5 ${badgeColor}`}>{data.signal}</span>
          <span className="text-xs font-mono text-zinc-400">{(data.score * 100).toFixed(0)}%</span>
          {expanded ? <CaretDown size={12} /> : <CaretRight size={12} />}
        </div>
      </button>

      {expanded && (
        <div className="px-3 pb-3 pt-0 border-t border-zinc-800/40">
          <IndicatorDetails data={data} />
        </div>
      )}
    </div>
  );
}
