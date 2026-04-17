import { useRef, useEffect, useState, useCallback, useMemo } from "react";
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import { ChartBar, ArrowClockwise } from "@phosphor-icons/react";

// ══════════════════════════════════════════════════════════════
// LINE STYLE — phase + role aware
// ══════════════════════════════════════════════════════════════
// lineStyle: 0=Solid, 1=Dotted, 2=Dashed, 3=LargeDashed
function getLineConfig(lineRole, phase, isN3Trigger) {
  if (phase === 'IN_TRADE') {
    return { opacity: 0.10, lineWidth: 1, lineStyle: 3, axisLabel: false };
  }

  if (phase === 'TARGET_LOCK') {
    if (isN3Trigger) {
      return { opacity: 1.0, lineWidth: 3, lineStyle: 0, axisLabel: true };
    }
    if (lineRole === 'target') {
      return { opacity: 0.50, lineWidth: 1, lineStyle: 0, axisLabel: true };
    }
    return { opacity: 0.12, lineWidth: 1, lineStyle: 3, axisLabel: false };
  }

  // RECON phase — N2 evaluating
  if (lineRole === 'validation') {
    return { opacity: 1.0, lineWidth: 2, lineStyle: 0, axisLabel: true };
  }
  if (lineRole === 'target') {
    return { opacity: 0.30, lineWidth: 1, lineStyle: 2, axisLabel: true };
  }
  // context
  return { opacity: 0.15, lineWidth: 1, lineStyle: 3, axisLabel: false };
}

function applyOpacity(hexColor, opacity) {
  const raw = hexColor.replace(/^#/, '');
  const base = raw.length >= 8 ? raw.slice(0, 6) : raw.slice(0, 6).padStart(6, '0');
  const alphaHex = Math.round(opacity * 255).toString(16).padStart(2, '0');
  return `#${base}${alphaHex}`;
}

// ══════════════════════════════════════════════════════════════
// PHASE DETECTION
// ══════════════════════════════════════════════════════════════
function detectPhase(v3Signal, activePosition) {
  if (activePosition && activePosition.state !== 'CLOSED') return 'IN_TRADE';
  const n2 = v3Signal?.nivel_2;
  const n3 = v3Signal?.nivel_3;
  if (n2?.passed && n3?.action && n3.action !== 'WAIT') return 'TARGET_LOCK';
  return 'RECON';
}

// ══════════════════════════════════════════════════════════════
// COLOR MAP per level key
// ══════════════════════════════════════════════════════════════
const LEVEL_COLORS = {
  vah: '#34d399', val: '#f87171', poc: '#fbbf24',
  vwap: '#f59e0b',
  vwap_u1: '#f59e0b', vwap_l1: '#f59e0b',
  vwap_u3: '#ef4444', vwap_l3: '#ef4444',
  d1_vah: '#34d399', d1_val: '#f87171', d1_poc: '#fbbf24',
  call_wall: '#22d3ee', put_wall: '#f472b6', zgl: '#a78bfa',
  onh: '#fb923c', onl: '#fb923c', on_poc: '#fdba74',
};

// Role dot colors for toggle pills
const ROLE_DOT = {
  validation: 'bg-emerald-400',
  target: 'bg-amber-400',
  context: 'bg-zinc-500',
  hidden: 'bg-zinc-700',
};

// ══════════════════════════════════════════════════════════════
// COMPONENT
// ══════════════════════════════════════════════════════════════
export function DataBentoChart({ data, symbol, timeframe, analysis, activePosition, v3Signal, onSessionChange }) {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const wsRef = useRef(null);
  const [livePrice, setLivePrice] = useState(null);

  // Level visibility overrides: { key: true/false }
  // undefined = use backend default_visible
  const [levelOverrides, setLevelOverrides] = useState({});

  // Track regime for auto-reset
  const currentRegime = v3Signal?.nivel_1?.regime;
  const prevRegimeRef = useRef(currentRegime);
  useEffect(() => {
    if (prevRegimeRef.current !== currentRegime) {
      setLevelOverrides({});
      prevRegimeRef.current = currentRegime;
    }
  }, [currentRegime]);

  // Reset to context defaults
  const resetLevels = useCallback(() => {
    setLevelOverrides({});
  }, []);

  // Toggle a single level — explicit show/hide
  const toggleLevel = useCallback((key) => {
    setLevelOverrides(prev => {
      const next = { ...prev };
      if (next[key] !== undefined) {
        // Already overridden — remove override (back to default)
        delete next[key];
      } else {
        // First toggle — find the level's current effective state and flip it
        const lvl = (v3Signal?.chart_levels?.levels || []).find(l => l.key === key);
        const currentDefault = lvl?.default_visible ?? false;
        next[key] = !currentDefault; // Explicit: true=show, false=hide
      }
      return next;
    });
  }, [v3Signal]);

  // chart_levels from backend V3 signal
  const chartLevels = v3Signal?.chart_levels?.levels || [];
  const hasOverrides = Object.keys(levelOverrides).length > 0;

  // Compute effective visibility per level
  const effectiveLevels = useMemo(() => {
    return chartLevels.map(lvl => {
      const override = levelOverrides[lvl.key];
      const visible = override !== undefined ? override : lvl.default_visible;
      return { ...lvl, visible };
    });
  }, [chartLevels, levelOverrides]);

  // Detect phase
  const phase = useMemo(() => detectPhase(v3Signal, activePosition), [v3Signal, activePosition]);

  // N3 trigger line identification
  const n3TriggerLineId = useMemo(() => {
    if (phase !== 'TARGET_LOCK') return null;
    const n3 = v3Signal?.nivel_3;
    if (!n3) return null;
    const reason = (n3.reason || '').toLowerCase();
    if (reason.includes('+1s') || reason.includes('+1σ')) return 'vwap_u1';
    if (reason.includes('-1s') || reason.includes('-1σ')) return 'vwap_l1';
    if (reason.includes('-3sd') || reason.includes('-3σ')) return 'vwap_l3';
    if (reason.includes('+3sd') || reason.includes('+3σ')) return 'vwap_u3';
    if (reason.includes('put wall')) return 'put_wall';
    if (reason.includes('call wall')) return 'call_wall';
    if (reason.includes('onh')) return 'onh';
    if (reason.includes('onl')) return 'onl';
    if (reason.includes('d1_vah') || reason.includes('vah_d1')) return 'd1_vah';
    if (reason.includes('d1_val') || reason.includes('val_d1')) return 'd1_val';
    if (reason.includes('d1_poc') || reason.includes('poc_d1')) return 'd1_poc';
    if (reason.includes('vah')) return 'vah';
    if (reason.includes('val')) return 'val';
    if (reason.includes('poc')) return 'poc';
    return null;
  }, [phase, v3Signal]);

  // ── Chart init ──
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: 'solid', color: '#0a0a0c' },
        textColor: '#71717a',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#18181b' },
        horzLines: { color: '#18181b' },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: '#3f3f4680', width: 1, style: 3, labelVisible: true, labelBackgroundColor: '#27272a' },
        horzLine: { color: '#3f3f4680', width: 1, style: 3, labelVisible: true, labelBackgroundColor: '#27272a' },
      },
      rightPriceScale: { borderColor: '#27272a', scaleMargins: { top: 0.05, bottom: 0.15 }, autoScale: true },
      timeScale: {
        borderColor: '#27272a', timeVisible: true, secondsVisible: false,
        rightOffset: 5, barSpacing: 6,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    });

    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e80', wickDownColor: '#ef444480',
    });
    candleSeriesRef.current = candleSeries;

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    volumeSeriesRef.current = volumeSeries;
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
      visible: false,
    });

    let disposed = false;
    const handleResize = () => {
      if (disposed || !chartContainerRef.current) return;
      try {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      } catch (_) {}
    };
    const observer = new ResizeObserver(handleResize);
    observer.observe(chartContainerRef.current);

    return () => {
      disposed = true;
      observer.disconnect();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      try { chart.remove(); } catch (_) {}
    };
  }, []);

  // ── Data update ──
  useEffect(() => {
    if (!candleSeriesRef.current || !data || data.length === 0) return;

    const candleData = data
      .map(d => {
        let t = d.timestamp || d.time || d.date;
        if (typeof t === 'string') {
          const dateObj = new Date(t);
          t = Math.floor(dateObj.getTime() / 1000);
        }
        if (!t || isNaN(t)) return null;
        return { time: t, open: d.open, high: d.high, low: d.low, close: d.close };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);

    const volumeData = data
      .map(d => {
        let t = d.timestamp || d.time || d.date;
        if (typeof t === 'string') {
          const dateObj = new Date(t);
          t = Math.floor(dateObj.getTime() / 1000);
        }
        if (!t || isNaN(t)) return null;
        return { time: t, value: d.volume || 0, color: d.close >= d.open ? '#22c55e30' : '#ef444430' };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);

    try {
      candleSeriesRef.current.setData(candleData);
      volumeSeriesRef.current.setData(volumeData);
      if (candleData.length > 0 && chartRef.current) chartRef.current.timeScale().fitContent();
    } catch (_) {}
  }, [data, symbol]);

  // ── WebSocket Live Price Feed ──
  const liveCandleRef = useRef(null);
  useEffect(() => {
    if (!symbol) return;
    const API = process.env.REACT_APP_BACKEND_URL || '';
    const wsUrl = API.replace('https://', 'wss://').replace('http://', 'ws://') + '/api/ws/live/' + symbol;

    let reconnectTimeout;
    const connect = () => {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);

          // Handle session transition broadcasts
          if (msg.type === 'session_change') {
            console.info(`[WS] Session change: ${msg.prev_session} → ${msg.session} (${msg.reason})`);
            if (onSessionChange) onSessionChange(msg);
            return;
          }

          if (msg.type === 'trade' && msg.data && msg.data.price) {
            setLivePrice(msg.data.price);

            if (candleSeriesRef.current) {
              const now = Math.floor(Date.now() / 1000);
              const interval = timeframe === '5M' ? 300 : timeframe === '1H' ? 3600 : 14400;
              const candleTime = now - (now % interval);

              const prev = liveCandleRef.current;
              if (prev && prev.time === candleTime) {
                const updated = {
                  time: candleTime,
                  open: prev.open,
                  high: Math.max(prev.high, msg.data.price),
                  low: Math.min(prev.low, msg.data.price),
                  close: msg.data.price,
                };
                liveCandleRef.current = updated;
                candleSeriesRef.current.update(updated);
              } else {
                const newCandle = {
                  time: candleTime,
                  open: msg.data.price,
                  high: msg.data.price,
                  low: msg.data.price,
                  close: msg.data.price,
                };
                liveCandleRef.current = newCandle;
                candleSeriesRef.current.update(newCandle);
              }
            }
          }
        } catch { /* ignore parse errors */ }
      };

      ws.onclose = () => {
        liveCandleRef.current = null;
        reconnectTimeout = setTimeout(connect, 5000);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      clearTimeout(reconnectTimeout);
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); }
      liveCandleRef.current = null;
    };
  }, [symbol, timeframe, onSessionChange]);

  // ══════════════════════════════════════════════════════════════
  // PRICE LINES — driven by backend chart_levels + user overrides
  // ══════════════════════════════════════════════════════════════
  useEffect(() => {
    if (!candleSeriesRef.current) return;
    const series = candleSeriesRef.current;
    const createdLines = [];

    for (const lvl of effectiveLevels) {
      if (!lvl.visible || !lvl.value || lvl.value <= 0) continue;

      const isN3Trigger = (phase === 'TARGET_LOCK' && n3TriggerLineId === lvl.key);
      const cfg = getLineConfig(lvl.role, phase, isN3Trigger);
      const color = LEVEL_COLORS[lvl.key] || '#71717a';

      try {
        createdLines.push(series.createPriceLine({
          price: lvl.value,
          color: applyOpacity(color, cfg.opacity),
          title: isN3Trigger ? `\u25b6 ${lvl.label}` : lvl.label,
          lineWidth: cfg.lineWidth,
          lineStyle: cfg.lineStyle,
          axisLabelVisible: cfg.axisLabel,
        }));
      } catch (e) { console.warn('Could not create price line:', lvl.label, e); }
    }

    return () => {
      for (const pl of createdLines) {
        try { series.removePriceLine(pl); } catch { /* ignore */ }
      }
    };
  }, [effectiveLevels, phase, n3TriggerLineId]);

  // ── Position SL/TP/Entry overlay (always visible) ──
  useEffect(() => {
    if (!candleSeriesRef.current) return;
    const series = candleSeriesRef.current;
    const posLines = [];

    if (activePosition && activePosition.state !== 'CLOSED') {
      if (activePosition.entry_price) {
        try {
          posLines.push(series.createPriceLine({
            price: activePosition.entry_price, color: '#ffffff60',
            lineWidth: 1, lineStyle: 2, axisLabelVisible: true,
            title: `ENTRY ${activePosition.entry_price.toLocaleString()}`,
          }));
        } catch { /* ignore */ }
      }

      const currentStop = activePosition.current_stop || activePosition.hard_stop;
      if (currentStop) {
        try {
          posLines.push(series.createPriceLine({
            price: currentStop, color: '#ef4444',
            lineWidth: 2, lineStyle: 2, axisLabelVisible: true,
            title: `SL ${currentStop.toLocaleString()}`,
          }));
        } catch { /* ignore */ }
      }

      if (activePosition.take_profit) {
        try {
          posLines.push(series.createPriceLine({
            price: activePosition.take_profit, color: '#10b981',
            lineWidth: 2, lineStyle: 2, axisLabelVisible: true,
            title: `TP ${activePosition.take_profit.toLocaleString()}`,
          }));
        } catch { /* ignore */ }
      }

      if (activePosition.break_even?.threshold && !activePosition.break_even?.triggered) {
        try {
          posLines.push(series.createPriceLine({
            price: activePosition.break_even.threshold, color: '#f59e0b50',
            lineWidth: 1, lineStyle: 3, axisLabelVisible: false, title: 'BE',
          }));
        } catch { /* ignore */ }
      }
    }

    return () => {
      for (const pl of posLines) {
        try { series.removePriceLine(pl); } catch { /* ignore */ }
      }
    };
  }, [activePosition]);

  // ── Derived display values ──
  const lastCandle = data && data.length > 0 ? data[data.length - 1] : null;
  const prevCandle = data && data.length > 1 ? data[data.length - 2] : null;
  const displayPrice = livePrice || lastCandle?.close;
  const basePrice = prevCandle?.close || lastCandle?.open;
  const priceChange = displayPrice && basePrice ? displayPrice - basePrice : 0;
  const priceChangePercent = basePrice ? (priceChange / basePrice) * 100 : 0;

  // Phase label for HUD badge
  const phaseLabel = { RECON: 'RECON', TARGET_LOCK: 'TARGET', IN_TRADE: 'IN TRADE' }[phase];
  const phaseColor = {
    RECON: 'text-zinc-400 border-zinc-600',
    TARGET_LOCK: 'text-amber-400 border-amber-600',
    IN_TRADE: 'text-emerald-400 border-emerald-600',
  }[phase];

  // Feed health state
  const feedHealth = v3Signal?.data_quality?.feed_health;
  const feedState = feedHealth?.state || (livePrice ? 'LIVE' : 'CLOSED');
  const feedReason = feedHealth?.reason || '';

  const feedIndicator = {
    LIVE:   { color: 'bg-emerald-400', text: 'text-emerald-400', pulse: 'animate-pulse', label: 'LIVE' },
    CLOSED: { color: 'bg-amber-400',   text: 'text-amber-400',   pulse: '',              label: 'CLOSED' },
    STALE:  { color: 'bg-orange-400',  text: 'text-orange-400',  pulse: 'animate-pulse', label: 'STALE' },
    DEAD:   { color: 'bg-red-500',     text: 'text-red-500',     pulse: 'animate-pulse', label: 'DEAD' },
  }[feedState] || { color: 'bg-zinc-500', text: 'text-zinc-500', pulse: '', label: '?' };

  // Group levels by role for the toggle panel
  const levelGroups = useMemo(() => {
    const active = effectiveLevels.filter(l => l.role !== 'hidden' && l.value > 0);
    const hidden = effectiveLevels.filter(l => l.role === 'hidden' && l.value > 0);
    const validationCount = active.filter(l => l.role === 'validation').length;
    return { active, hidden, validationCount };
  }, [effectiveLevels]);

  return (
    <div className="relative w-full h-full">
      {/* Price HUD */}
      {(lastCandle || livePrice) && (
        <div className="absolute top-3 left-3 z-10 bg-[#0a0a0c]/90 border border-zinc-800/60 p-3">
          <div className="flex items-center gap-3">
            <div>
              <div className="text-[10px] text-zinc-500 uppercase flex items-center gap-1.5">
                {symbol} / {timeframe}
                <span className="flex items-center gap-1 group relative" data-testid="feed-health-indicator">
                  <span className={`w-1.5 h-1.5 rounded-full ${feedIndicator.color} ${feedIndicator.pulse} inline-block`} />
                  <span className={feedIndicator.text}>{feedIndicator.label}</span>
                  {feedReason && (
                    <span className="absolute left-0 top-full mt-1 hidden group-hover:block bg-zinc-900 border border-zinc-700 text-[9px] text-zinc-300 px-2 py-1 whitespace-nowrap z-50">
                      {feedReason}
                    </span>
                  )}
                </span>
              </div>
              <div className="text-xl font-mono font-bold text-zinc-100" data-testid="chart-live-price">
                {displayPrice?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
            </div>
            <div className={`text-sm font-mono ${priceChange >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)} ({priceChangePercent.toFixed(2)}%)
            </div>
          </div>
          {lastCandle && (
            <div className="grid grid-cols-4 gap-3 mt-2 text-[10px]">
              <div><span className="text-zinc-500">O:</span><span className="ml-1 font-mono text-zinc-300">{lastCandle.open?.toFixed(2)}</span></div>
              <div><span className="text-zinc-500">H:</span><span className="ml-1 font-mono text-emerald-400">{lastCandle.high?.toFixed(2)}</span></div>
              <div><span className="text-zinc-500">L:</span><span className="ml-1 font-mono text-red-400">{lastCandle.low?.toFixed(2)}</span></div>
              <div><span className="text-zinc-500">V:</span><span className="ml-1 font-mono text-blue-400">{lastCandle.volume?.toLocaleString()}</span></div>
            </div>
          )}
        </div>
      )}

      {/* Chart canvas */}
      <div ref={chartContainerRef} data-testid="databento-chart" className="w-full h-full" />

      {/* HUD Controls — top right */}
      {chartLevels.length > 0 && (
        <div data-testid="chart-level-filter" className="absolute top-3 right-3 z-10 flex flex-col items-end gap-1.5 max-w-[320px]">
          {/* Phase + Regime badge row */}
          <div className="flex items-center gap-1.5 mb-0.5">
            {currentRegime && (
              <span data-testid="hud-regime-badge" className="text-[8px] font-bold font-mono px-1.5 py-0.5 border border-zinc-600 bg-zinc-900/80 text-zinc-300">
                {currentRegime}
              </span>
            )}
            <span data-testid="hud-phase-badge" className={`text-[8px] font-bold font-mono px-1.5 py-0.5 border bg-zinc-900/80 ${phaseColor}`}>
              {phaseLabel}
            </span>
            {phase === 'RECON' && (
              <span data-testid="hud-n2-count" className="text-[7px] font-mono px-1 py-0.5 bg-zinc-900/60 border border-zinc-700/40 text-zinc-500">
                N2: {levelGroups.validationCount} niveis
              </span>
            )}
            {hasOverrides && (
              <button
                data-testid="reset-chart-levels-btn"
                onClick={resetLevels}
                className="flex items-center gap-0.5 text-[7px] font-mono px-1.5 py-0.5 border border-cyan-700/50 bg-cyan-900/30 text-cyan-400 hover:bg-cyan-800/40 transition-colors"
                title="Reset to context levels"
              >
                <ArrowClockwise size={8} />
                Reset
              </button>
            )}
          </div>

          {/* Active levels (validation + target + context) — clickable to hide */}
          <div className="flex flex-wrap gap-1 justify-end">
            {levelGroups.active.map(lvl => {
              const isVisible = lvl.visible;
              const dotColor = ROLE_DOT[lvl.role] || ROLE_DOT.context;
              const roleBadge = lvl.role === 'validation' ? 'N2' : lvl.role === 'target' ? 'N3' : '';

              return (
                <button
                  key={lvl.key}
                  data-testid={`level-toggle-${lvl.key}`}
                  onClick={() => toggleLevel(lvl.key)}
                  className={`flex items-center gap-1 px-1.5 py-0.5 text-[8px] font-medium border transition-all cursor-pointer select-none ${
                    isVisible
                      ? 'bg-zinc-800/80 border-zinc-600/60 text-zinc-200'
                      : 'bg-zinc-900/60 border-zinc-800/30 text-zinc-600 line-through'
                  }`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${dotColor} ${!isVisible ? 'opacity-30' : ''}`} />
                  {lvl.label}
                  {roleBadge && <span className="text-[6px] opacity-50 ml-0.5">{roleBadge}</span>}
                </button>
              );
            })}
          </div>

          {/* Hidden levels (extra) — clickable to show */}
          {levelGroups.hidden.length > 0 && (
            <div className="flex flex-wrap gap-1 justify-end mt-0.5 border-t border-zinc-800/40 pt-1">
              <span className="text-[7px] text-zinc-600 font-mono mr-1 self-center">EXTRA:</span>
              {levelGroups.hidden.map(lvl => {
                const isVisible = lvl.visible;
                return (
                  <button
                    key={lvl.key}
                    data-testid={`level-toggle-${lvl.key}`}
                    onClick={() => toggleLevel(lvl.key)}
                    className={`flex items-center gap-1 px-1.5 py-0.5 text-[8px] font-medium border transition-all cursor-pointer select-none ${
                      isVisible
                        ? 'bg-zinc-800/60 border-zinc-500/40 text-zinc-300 ring-1 ring-zinc-500/20'
                        : 'bg-zinc-900/40 border-zinc-800/20 text-zinc-600'
                    }`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full ${ROLE_DOT.hidden} ${isVisible ? 'opacity-80' : 'opacity-30'}`} />
                    {lvl.label}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Loading state */}
      {(!data || data.length === 0) && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#0a0a0c]">
          <div className="text-center">
            <ChartBar size={48} className="text-zinc-700 mx-auto mb-2" />
            <p className="text-zinc-500 text-sm">Loading chart data...</p>
          </div>
        </div>
      )}
    </div>
  );
}
