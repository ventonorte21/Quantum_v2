import {
  Lightning, ArrowLeft, Gauge, Gear, Funnel
} from "@phosphor-icons/react";

export function AutoTradingPanel({
  autoTradingConfig,
  tradingStatus,
  autoTradingSignals,
  v3Signal,
  savingConfig,
  loading,
  onClose,
  onUpdateConfigField,
  onEvaluate,
  onExecute,
  onFlatten,
  onSaveConfig,
  onRefreshSignals,
}) {
  return (
    <div className="fixed inset-0 bg-[#09090B] z-50 overflow-auto" data-testid="autotrading-panel">
      <div className="max-w-5xl mx-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <Lightning size={24} weight="fill" className="text-emerald-400" />
            <div>
              <h2 className="text-lg font-bold font-['Chivo']">Auto Trading V3</h2>
              <p className="text-xs text-zinc-500">Configuracao baseada na Arquitetura V3</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {autoTradingConfig?.paper_trading && (
              <span className="text-xs px-2 py-1 bg-yellow-500/10 border border-yellow-500/30 text-yellow-400">PAPER MODE</span>
            )}
            {autoTradingConfig?.enabled && (
              <span className="text-xs px-2 py-1 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 animate-pulse">ATIVO</span>
            )}
            <button onClick={onClose} className="flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-300 border border-zinc-700 hover:border-zinc-600 transition-colors" data-testid="close-autotrading-btn">
              <ArrowLeft size={14} />
              <span>Voltar</span>
            </button>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-4">
          <div className="col-span-2 space-y-4">
            {/* Regime Table */}
            {v3Signal?.nivel_1 && (() => {
              const n1 = v3Signal.nivel_1;
              const regimeStyles = {
                'COMPLACENCIA': 'border-blue-500/40 bg-blue-500/10',
                'BULL': 'border-emerald-500/40 bg-emerald-500/10',
                'TRANSICAO': 'border-amber-500/40 bg-amber-500/10',
                'BEAR': 'border-red-500/40 bg-red-500/10',
                'CAPITULACAO': 'border-fuchsia-500/40 bg-fuchsia-500/10',
              };
              const allRegimes = [
                { key: 'COMPLACENCIA', symbol: 'MNQ', tactic: 'Trend Defensivo', lot: 75, dir: 'LONG', score: '11-13' },
                { key: 'BULL', symbol: 'MNQ', tactic: 'Trend Following', lot: 100, dir: 'LONG', score: '8-10' },
                { key: 'TRANSICAO', symbol: 'MES', tactic: 'Range Scalping', lot: 50, dir: 'COUNTER', score: '5-7' },
                { key: 'BEAR', symbol: 'MNQ', tactic: 'Momentum Short', lot: 100, dir: 'SHORT', score: '2-4' },
                { key: 'CAPITULACAO', symbol: 'MES', tactic: 'Fading (Exaustao)', lot: 50, dir: 'REVERSAL', score: '0-1' },
              ];
              return (
                <div className={`border-2 p-4 ${regimeStyles[n1.regime] || 'border-zinc-700'}`}>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Gauge size={16} className="text-fuchsia-400" />
                      <span className="text-[10px] uppercase tracking-widest text-zinc-400 font-semibold">Regime Corrente</span>
                    </div>
                    <span className="text-lg font-bold font-mono">{n1.regime}</span>
                  </div>
                  <div className="grid grid-cols-4 gap-3 mb-3">
                    <div className="bg-zinc-900/50 p-2.5 text-center">
                      <div className="text-[8px] text-zinc-500 uppercase">Ativo</div>
                      <div className="text-sm font-mono font-bold text-zinc-100">{n1.target_symbol}</div>
                    </div>
                    <div className="bg-zinc-900/50 p-2.5 text-center">
                      <div className="text-[8px] text-zinc-500 uppercase">Tatica</div>
                      <div className="text-[10px] font-semibold text-zinc-300">{n1.tactic}</div>
                    </div>
                    <div className="bg-zinc-900/50 p-2.5 text-center">
                      <div className="text-[8px] text-zinc-500 uppercase">Lote</div>
                      <div className="text-sm font-mono font-bold text-zinc-100">{n1.lot_pct}%</div>
                    </div>
                    <div className="bg-zinc-900/50 p-2.5 text-center">
                      <div className="text-[8px] text-zinc-500 uppercase">Score</div>
                      <div className="text-sm font-mono font-bold text-zinc-100">{n1.macro_score}/{n1.max_score}</div>
                    </div>
                  </div>
                  <div className="text-[9px] text-zinc-500 mb-1.5">Tabela de Regimes:</div>
                  <div className="space-y-[2px]">
                    {allRegimes.map(r => (
                      <div key={r.key} className={`grid grid-cols-5 gap-2 px-2 py-1 text-[9px] ${r.key === n1.regime ? 'bg-zinc-700/30 border border-zinc-600/40' : 'bg-zinc-900/30'}`}>
                        <span className={`font-semibold ${r.key === n1.regime ? 'text-zinc-100' : 'text-zinc-500'}`}>{r.key}</span>
                        <span className="font-mono text-zinc-400">{r.symbol}</span>
                        <span className="text-zinc-400">{r.tactic}</span>
                        <span className="font-mono text-zinc-400">{r.lot}%</span>
                        <span className="font-mono text-zinc-500">{r.score}</span>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Config Panel */}
            <div className="border border-zinc-800/40 p-4">
              <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
                <Gear size={16} className="text-blue-400" />
                Configuracoes de Execucao
              </h3>
              <div className="grid grid-cols-2 gap-4 mb-4">
                <div className="flex items-center justify-between p-2 border border-zinc-800/40 hover:bg-zinc-800/20 transition-colors cursor-pointer"
                  onClick={() => onUpdateConfigField('enabled', !autoTradingConfig?.enabled)} data-testid="auto-enabled-toggle">
                  <div>
                    <span className="text-xs font-semibold">Auto Trading Ativo</span>
                    <div className="text-[9px] text-zinc-500">Executa automaticamente quando ACTIVE_SIGNAL</div>
                  </div>
                  <div className={`relative w-10 h-5 rounded-full transition-colors duration-200 ${autoTradingConfig?.enabled ? 'bg-emerald-500' : 'bg-zinc-700'}`}>
                    <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${autoTradingConfig?.enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
                  </div>
                </div>
                <div className="flex items-center justify-between p-2 border border-zinc-800/40 hover:bg-zinc-800/20 transition-colors cursor-pointer"
                  onClick={() => onUpdateConfigField('paper_trading', !(autoTradingConfig?.paper_trading !== false))} data-testid="paper-trading-toggle">
                  <div>
                    <span className="text-xs font-semibold">Paper Trading</span>
                    <div className="text-[9px] text-zinc-500">Simula ordens sem enviar para SignalStack</div>
                  </div>
                  <div className={`relative w-10 h-5 rounded-full transition-colors duration-200 ${autoTradingConfig?.paper_trading !== false ? 'bg-yellow-500' : 'bg-zinc-700'}`}>
                    <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${autoTradingConfig?.paper_trading !== false ? 'translate-x-5' : 'translate-x-0.5'}`} />
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div>
                  <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Tamanho da Conta (USD)</label>
                  <input type="number" step="1000" value={autoTradingConfig?.account_size || 50000} onChange={(e) => onUpdateConfigField('account_size', parseFloat(e.target.value))} min="1000" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="account-size-input" />
                </div>
                <div>
                  <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Risco por Trade %</label>
                  <input type="number" step="0.25" value={autoTradingConfig?.risk_per_trade_pct || 1.0} onChange={(e) => onUpdateConfigField('risk_per_trade_pct', parseFloat(e.target.value))} min="0.25" max="5" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="risk-per-trade-input" />
                  <div className="text-[8px] text-emerald-400/80 mt-1 font-mono">= ${((autoTradingConfig?.account_size || 50000) * (autoTradingConfig?.risk_per_trade_pct || 1.0) / 100).toLocaleString()} / trade</div>
                </div>
                <div>
                  <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Max Loss Diario %</label>
                  <input type="number" step="0.5" value={autoTradingConfig?.max_daily_loss_percent || 2.0} onChange={(e) => onUpdateConfigField('max_daily_loss_percent', parseFloat(e.target.value))} className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="max-loss-input" />
                  <div className="text-[8px] text-red-400/80 mt-1 font-mono">= ${((autoTradingConfig?.account_size || 50000) * (autoTradingConfig?.max_daily_loss_percent || 2.0) / 100).toLocaleString()} / dia</div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div>
                  <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Qty Base</label>
                  <input type="number" value={autoTradingConfig?.default_quantity || 1} onChange={(e) => onUpdateConfigField('default_quantity', parseInt(e.target.value))} min="1" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="qty-base-input" />
                  <div className="text-[8px] text-zinc-600 mt-1">Qty final = base x lot% do regime</div>
                </div>
                <div>
                  <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Max Trades/Dia</label>
                  <input type="number" value={autoTradingConfig?.max_daily_trades || 10} onChange={(e) => onUpdateConfigField('max_daily_trades', parseInt(e.target.value))} min="1" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="max-daily-input" />
                </div>
                <div>
                  <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Max Posicoes</label>
                  <input type="number" value={autoTradingConfig?.max_total_positions || 4} onChange={(e) => onUpdateConfigField('max_total_positions', parseInt(e.target.value))} min="1" max="10" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="max-positions-input" />
                </div>
              </div>
              <div className="border-t border-zinc-800/40 pt-4 mb-4">
                <div className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-3">Gerenciamento de Risco</div>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="text-[10px] text-zinc-500 mb-1.5 block">ATR Stop Multi</label>
                    <input type="number" step="0.1" value={autoTradingConfig?.atr_stop_multiplier || 1.5} onChange={(e) => onUpdateConfigField('atr_stop_multiplier', parseFloat(e.target.value))} className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="atr-stop-input" />
                  </div>
                  <div>
                    <label className="text-[10px] text-zinc-500 mb-1.5 block">ATR Target Multi</label>
                    <input type="number" step="0.1" value={autoTradingConfig?.atr_target_multiplier || 3.0} onChange={(e) => onUpdateConfigField('atr_target_multiplier', parseFloat(e.target.value))} className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="atr-target-input" />
                  </div>
                  <div>
                    <label className="text-[10px] text-zinc-500 mb-1.5 block">Cooldown (min)</label>
                    <input type="number" value={autoTradingConfig?.min_minutes_between_trades || 5} onChange={(e) => onUpdateConfigField('min_minutes_between_trades', parseInt(e.target.value))} className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="cooldown-input" />
                  </div>
                </div>
              </div>
              <div className="border-t border-zinc-800/40 pt-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold">Horario de Trading</div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => onUpdateConfigField('auto_hours_mode', true)} className={`px-2.5 py-1 text-[9px] font-semibold transition-colors border ${autoTradingConfig?.auto_hours_mode !== false ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' : 'border-zinc-700 text-zinc-500'}`} data-testid="auto-hours-btn">NYSE AUTO</button>
                    <button onClick={() => onUpdateConfigField('globex_auto_enabled', !autoTradingConfig?.globex_auto_enabled)} className={`px-2.5 py-1 text-[9px] font-semibold transition-colors border ${autoTradingConfig?.globex_auto_enabled ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-400' : 'border-zinc-700 text-zinc-500'}`} data-testid="globex-auto-btn">GLOBEX AUTO</button>
                    <button onClick={() => onUpdateConfigField('auto_hours_mode', false)} className={`px-2.5 py-1 text-[9px] font-semibold transition-colors border ${autoTradingConfig?.auto_hours_mode === false ? 'bg-blue-500/10 border-blue-500/30 text-blue-400' : 'border-zinc-700 text-zinc-500'}`} data-testid="manual-hours-btn">MANUAL</button>
                  </div>
                </div>
                {autoTradingConfig?.auto_hours_mode !== false ? (
                  <div className="space-y-2">
                    <div className="bg-emerald-500/5 border border-emerald-500/20 p-2.5 text-[9px]">
                      <div className="flex items-center gap-1.5 mb-1"><span className="text-emerald-400 font-bold">NYSE AUTO</span><span className="text-zinc-500">DST-aware</span></div>
                      <div className="text-zinc-400">Pre-market (4:00 AM ET) &rarr; 30min antes do close. Feriados, early closes e weekends detectados automaticamente.</div>
                    </div>
                    {autoTradingConfig?.globex_auto_enabled && (
                      <div className="bg-cyan-500/5 border border-cyan-500/20 p-2.5 text-[9px]">
                        <div className="flex items-center gap-1.5 mb-1"><span className="text-cyan-400 font-bold">GLOBEX AUTO</span><span className="text-zinc-500">18:00 ET &rarr; 09:25 ET</span></div>
                        <div className="text-zinc-400">Sessao noturna CME. Halt 17-18h ET. Flatten automatico antes do NY open. Feriados, early closes e weekends detectados. Lote reduzido a 50% (thin market).</div>
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] text-zinc-500 mb-1.5 block">Flatten antes do close (min)</label>
                        <input type="number" value={autoTradingConfig?.pre_close_flatten_minutes || 30} onChange={(e) => onUpdateConfigField('pre_close_flatten_minutes', parseInt(e.target.value))} min="5" max="60" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="pre-close-minutes-input" />
                      </div>
                      <label className="flex items-center gap-3 cursor-pointer p-2 border border-zinc-800/40 hover:bg-zinc-800/20 transition-colors"
                        onClick={() => onUpdateConfigField('eod_flatten_enabled', !(autoTradingConfig?.eod_flatten_enabled !== false))} data-testid="eod-flatten-toggle">
                        <div className="flex-1">
                          <span className="text-[10px] font-semibold">EOD Flatten</span>
                          <div className="text-[8px] text-zinc-500">Fecha posicoes no fim da sessao</div>
                        </div>
                        <div className={`relative w-9 h-4.5 rounded-full transition-colors duration-200 ${autoTradingConfig?.eod_flatten_enabled !== false ? 'bg-red-500' : 'bg-zinc-700'}`} style={{width:'36px',height:'18px'}}>
                          <div className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white shadow transition-transform duration-200 ${autoTradingConfig?.eod_flatten_enabled !== false ? 'translate-x-[18px]' : 'translate-x-0.5'}`} style={{width:'14px',height:'14px'}} />
                        </div>
                      </label>
                    </div>
                    {autoTradingConfig?.globex_auto_enabled && (
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-[10px] text-zinc-500 mb-1.5 block">Flatten Globex antes NY (min)</label>
                          <input type="number" value={autoTradingConfig?.globex_flatten_before_ny_minutes || 5} onChange={(e) => onUpdateConfigField('globex_flatten_before_ny_minutes', parseInt(e.target.value))} min="1" max="30" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="globex-flatten-minutes-input" />
                          <div className="text-[8px] text-cyan-400/80 mt-1">Fecha posicoes Globex N min antes da abertura NYSE</div>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="grid grid-cols-4 gap-3">
                    <div>
                      <label className="text-[10px] text-zinc-500 mb-1.5 block">Inicio (ET)</label>
                      <input type="number" value={autoTradingConfig?.trading_start_hour || 9} onChange={(e) => onUpdateConfigField('trading_start_hour', parseInt(e.target.value))} min="0" max="23" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="start-hour-input" />
                    </div>
                    <div>
                      <label className="text-[10px] text-zinc-500 mb-1.5 block">Fim (ET)</label>
                      <input type="number" value={autoTradingConfig?.trading_end_hour || 16} onChange={(e) => onUpdateConfigField('trading_end_hour', parseInt(e.target.value))} min="0" max="23" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="end-hour-input" />
                    </div>
                    <div>
                      <label className="text-[10px] text-zinc-500 mb-1.5 block">Skip Open (min)</label>
                      <input type="number" value={autoTradingConfig?.avoid_first_minutes || 15} onChange={(e) => onUpdateConfigField('avoid_first_minutes', parseInt(e.target.value))} className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" />
                    </div>
                    <div>
                      <label className="text-[10px] text-zinc-500 mb-1.5 block">Skip Close (min)</label>
                      <input type="number" value={autoTradingConfig?.avoid_last_minutes || 15} onChange={(e) => onUpdateConfigField('avoid_last_minutes', parseInt(e.target.value))} className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" />
                    </div>
                  </div>
                )}
                <div className="mt-3 border-t border-zinc-800/30 pt-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold">News Blackout</div>
                    <label className="flex items-center gap-2 cursor-pointer"
                      onClick={() => onUpdateConfigField('news_blackout_enabled', !(autoTradingConfig?.news_blackout_enabled !== false))} data-testid="news-blackout-toggle">
                      <span className="text-[9px] text-zinc-400">Ativo</span>
                      <div className={`relative rounded-full transition-colors duration-200 ${autoTradingConfig?.news_blackout_enabled !== false ? 'bg-amber-500' : 'bg-zinc-700'}`} style={{width:'32px',height:'16px'}}>
                        <div className={`absolute top-0.5 rounded-full bg-white shadow transition-transform duration-200 ${autoTradingConfig?.news_blackout_enabled !== false ? 'translate-x-[16px]' : 'translate-x-0.5'}`} style={{width:'12px',height:'12px'}} />
                      </div>
                    </label>
                  </div>
                  {autoTradingConfig?.news_blackout_enabled !== false && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] text-zinc-500 mb-1.5 block">Antes da noticia (min)</label>
                        <input type="number" value={autoTradingConfig?.news_blackout_minutes_before || 15} onChange={(e) => onUpdateConfigField('news_blackout_minutes_before', parseInt(e.target.value))} min="5" max="60" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="news-before-input" />
                      </div>
                      <div>
                        <label className="text-[10px] text-zinc-500 mb-1.5 block">Depois da noticia (min)</label>
                        <input type="number" value={autoTradingConfig?.news_blackout_minutes_after || 15} onChange={(e) => onUpdateConfigField('news_blackout_minutes_after', parseInt(e.target.value))} min="5" max="60" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="news-after-input" />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Right Column: Status + Execution + Signals */}
          <div className="space-y-4">
            <div className="border border-zinc-800/40 p-4" data-testid="trading-status-panel">
              <h3 className="text-sm font-semibold mb-3">Horario de Trading</h3>
              {tradingStatus ? (
                <div className="space-y-2">
                  <div className={`px-3 py-2 text-center font-bold text-sm ${
                    tradingStatus.status === 'OPEN' && tradingStatus.session_type === 'globex' ? 'bg-cyan-500/10 border border-cyan-500/30 text-cyan-400' :
                    tradingStatus.status === 'OPEN' ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400' :
                    tradingStatus.status === 'NEWS_BLACKOUT' ? 'bg-amber-500/10 border border-amber-500/30 text-amber-400 animate-pulse' :
                    'bg-red-500/10 border border-red-500/30 text-red-400'
                  }`} data-testid="trading-status-badge">
                    {tradingStatus.status === 'OPEN' && tradingStatus.session_type === 'globex' ? 'Globex Ativo' :
                     tradingStatus.status === 'OPEN' ? 'NYSE Ativo' :
                     tradingStatus.status === 'NEWS_BLACKOUT' ? 'Blackout (News)' :
                     'Desabilitado'}
                  </div>
                  <div className="text-[10px] text-zinc-500 space-y-1">
                    {tradingStatus.session_type && <div>Sessao: <span className="text-zinc-300">{tradingStatus.session_type}</span></div>}
                    {tradingStatus.exchange_status && <div>Exchange: <span className="text-zinc-300">{tradingStatus.exchange_status}</span></div>}
                  </div>
                </div>
              ) : (
                <div className="text-xs text-zinc-500">Carregando...</div>
              )}
            </div>

            <div className="border border-zinc-800/40 p-4">
              <h3 className="text-sm font-semibold mb-3 flex items-center gap-2"><Funnel size={16} className="text-blue-400" />Execucao</h3>
              <div className="space-y-2">
                <button onClick={onEvaluate} disabled={loading} className="w-full px-3 py-2 text-xs font-semibold border border-blue-500/30 text-blue-400 bg-blue-500/10 hover:bg-blue-500/20 transition-all disabled:opacity-50" data-testid="evaluate-btn">
                  Avaliar Condicoes
                </button>
                <button onClick={() => onExecute(true)} disabled={loading} className="w-full px-3 py-2 text-xs font-semibold border border-amber-500/30 text-amber-400 bg-amber-500/10 hover:bg-amber-500/20 transition-all disabled:opacity-50" data-testid="force-execute-btn">
                  Force Execute
                </button>
                <button onClick={() => onFlatten('manual')} className="w-full px-3 py-2 text-xs font-semibold border border-red-500/30 text-red-400 bg-red-500/10 hover:bg-red-500/20 transition-all" data-testid="flatten-btn">
                  Flatten All
                </button>
              </div>
            </div>

            <div className="border border-zinc-800/40 p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold">Ultimos Sinais</h3>
                <button onClick={onRefreshSignals} className="text-[9px] text-zinc-500 hover:text-zinc-300">Refresh</button>
              </div>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {autoTradingSignals.length === 0 ? (
                  <div className="text-xs text-zinc-600 text-center py-3">Sem sinais</div>
                ) : autoTradingSignals.map((sig) => (
                  <div key={sig.id || sig.executed_at || `${sig.symbol}-${sig.side}`} className="bg-zinc-900/50 px-2 py-1.5 text-[10px]">
                    <div className="flex items-center justify-between">
                      <span className={`font-mono font-bold ${sig.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>{sig.side}</span>
                      <span className="text-zinc-500">{sig.symbol}</span>
                      <span className={`px-1 py-0.5 ${sig.status === 'paper_executed' || sig.status === 'executed' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-zinc-800 text-zinc-400'}`}>{sig.status}</span>
                    </div>
                    {sig.executed_at && <div className="text-zinc-600 mt-0.5">{new Date(sig.executed_at).toLocaleString()}</div>}
                  </div>
                ))}
              </div>
            </div>

            <button
              onClick={() => onSaveConfig(autoTradingConfig)}
              disabled={savingConfig}
              className="w-full px-4 py-3 bg-blue-500/10 border-2 border-blue-500/30 text-blue-400 font-bold text-sm hover:bg-blue-500/20 transition-all disabled:opacity-50"
              data-testid="save-config-btn"
            >
              {savingConfig ? 'Salvando...' : 'Salvar Configuracao'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
