import {
  PaperPlaneTilt, ArrowLeft, Target, Play, Stop,
  CheckCircle, XCircle, ChartPie, ListBullets, Gear
} from "@phosphor-icons/react";

const DEFAULT_WEBHOOK = "https://app.signalstack.com/hook/a65Cvk39pE3HdZiutAi9rP";

export function SignalStackPanel({
  chartSymbol,
  signalStackOrders,
  signalStackStats,
  tradovateSymbols,
  orderQuantity, setOrderQuantity,
  orderType, setOrderType,
  limitPrice, setLimitPrice,
  stopPrice, setStopPrice,
  sendingOrder,
  orderSuccess,
  expandedOrderJson, setExpandedOrderJson,
  webhookUrl, setWebhookUrl,
  onClose,
  onSendOrder,
  onRefreshOrders,
}) {
  return (
    <div className="fixed inset-0 bg-[#09090B] z-50 overflow-auto" data-testid="signalstack-panel">
      <div className="max-w-5xl mx-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <PaperPlaneTilt size={24} weight="bold" className="text-orange-400" />
            <div>
              <h2 className="text-lg font-bold font-['Chivo']">SignalStack / Tradovate</h2>
              <p className="text-xs text-zinc-500">Send orders via webhook</p>
            </div>
          </div>
          <button onClick={onClose} className="flex items-center gap-2 px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-300 border border-zinc-700 hover:border-zinc-600 transition-colors" data-testid="close-signalstack-btn">
            <ArrowLeft size={14} />
            <span>Voltar</span>
          </button>
        </div>
        <div className="grid grid-cols-2 gap-6">
          <div className="space-y-4">
            <div className="border border-zinc-800/40 p-4">
              <h3 className="text-sm font-semibold mb-4 flex items-center gap-2"><Target size={16} className="text-blue-400" />New Order</h3>
              <div className="bg-zinc-900/50 p-3 mb-4 border border-zinc-800/40">
                <div className="flex justify-between items-center">
                  <span className="text-xs text-zinc-500">Symbol</span>
                  <span className="font-mono font-bold text-blue-400">{chartSymbol}</span>
                </div>
                <div className="flex justify-between items-center mt-1">
                  <span className="text-xs text-zinc-500">Tradovate</span>
                  <span className="font-mono text-sm">{tradovateSymbols[chartSymbol]?.tradovate || 'Loading...'}</span>
                </div>
              </div>
              <div className="mb-4">
                <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Order Type</label>
                <div className="grid grid-cols-4 gap-1">
                  {['market', 'limit', 'stop', 'stop_limit'].map(type => (
                    <button key={type} onClick={() => setOrderType(type)} className={`px-2 py-1.5 text-xs font-mono transition-all border ${orderType === type ? 'bg-blue-500/10 border-blue-500/30 text-blue-400' : 'border-zinc-800 hover:border-zinc-700 text-zinc-400'}`}>
                      {type.replace('_', '-')}
                    </button>
                  ))}
                </div>
              </div>
              <div className="mb-4">
                <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Quantity</label>
                <input type="number" value={orderQuantity} onChange={(e) => setOrderQuantity(parseInt(e.target.value) || 1)} min="1" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="order-quantity-input" />
              </div>
              {(orderType === 'limit' || orderType === 'stop_limit') && (
                <div className="mb-4">
                  <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Limit Price</label>
                  <input type="number" value={limitPrice} onChange={(e) => setLimitPrice(e.target.value)} step="0.01" placeholder="Enter limit price" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="limit-price-input" />
                </div>
              )}
              {(orderType === 'stop' || orderType === 'stop_limit') && (
                <div className="mb-4">
                  <label className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold mb-2 block">Stop Price</label>
                  <input type="number" value={stopPrice} onChange={(e) => setStopPrice(e.target.value)} step="0.01" placeholder="Enter stop price" className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-sm font-mono focus:border-blue-500 focus:outline-none" data-testid="stop-price-input" />
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <button onClick={() => onSendOrder('buy')} disabled={sendingOrder} className="px-4 py-3 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-semibold hover:bg-emerald-500/20 transition-all disabled:opacity-50 flex items-center justify-center gap-2" data-testid="buy-order-btn"><Play size={16} weight="fill" />BUY</button>
                <button onClick={() => onSendOrder('sell')} disabled={sendingOrder} className="px-4 py-3 bg-red-500/10 border border-red-500/30 text-red-400 font-semibold hover:bg-red-500/20 transition-all disabled:opacity-50 flex items-center justify-center gap-2" data-testid="sell-order-btn"><Stop size={16} weight="fill" />SELL</button>
              </div>
              {orderSuccess && (
                <div className={`mt-4 p-3 border ${orderSuccess.type === 'success' ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-red-500/10 border-red-500/30'}`}>
                  <div className="flex items-center gap-2">
                    {orderSuccess.type === 'success' ? <CheckCircle size={16} className="text-emerald-400" /> : <XCircle size={16} className="text-red-400" />}
                    <span className={`text-sm ${orderSuccess.type === 'success' ? 'text-emerald-400' : 'text-red-400'}`}>{orderSuccess.message}</span>
                  </div>
                </div>
              )}
            </div>
            {signalStackStats && (
              <div className="border border-zinc-800/40 p-4">
                <h3 className="text-sm font-semibold mb-3 flex items-center gap-2"><ChartPie size={16} className="text-purple-400" />Order Statistics</h3>
                <div className="grid grid-cols-3 gap-3">
                  <div className="bg-zinc-900/50 p-3 text-center"><div className="text-xl font-mono font-bold">{signalStackStats.total_orders || 0}</div><div className="text-[10px] text-zinc-500 uppercase">Total</div></div>
                  <div className="bg-zinc-900/50 p-3 text-center"><div className="text-xl font-mono font-bold text-emerald-400">{signalStackStats.by_status?.success || 0}</div><div className="text-[10px] text-zinc-500 uppercase">Success</div></div>
                  <div className="bg-zinc-900/50 p-3 text-center"><div className="text-xl font-mono font-bold text-red-400">{signalStackStats.by_status?.error || 0}</div><div className="text-[10px] text-zinc-500 uppercase">Failed</div></div>
                </div>
              </div>
            )}
          </div>
          <div className="border border-zinc-800/40 p-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold flex items-center gap-2"><ListBullets size={16} className="text-zinc-400" />Recent Orders</h3>
              <button onClick={onRefreshOrders} className="text-xs text-zinc-500 hover:text-zinc-300">Refresh</button>
            </div>
            <div className="space-y-2 max-h-[400px] overflow-y-auto">
              {signalStackOrders.length === 0 ? (
                <div className="text-center text-zinc-500 text-sm py-8">No orders yet</div>
              ) : (
                signalStackOrders.map((order, idx) => (
                  <div key={order.order_id || order.sent_at || order.symbol + order.action + idx} className="bg-zinc-900/50 border border-zinc-800/40">
                    <div className="p-3">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className={`text-xs font-mono font-bold ${order.action === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>{order.action?.toUpperCase()}</span>
                          <span className="text-xs font-mono">{order.symbol}</span>
                          <span className="text-xs text-zinc-500">x{order.quantity}</span>
                          {order.source && (
                            <span className="text-[9px] px-1.5 py-0.5 bg-blue-500/10 border border-blue-500/30 text-blue-400">{order.source === 'v3_auto' ? 'V3 Paper' : order.source}</span>
                          )}
                          {order.regime && (
                            <span className="text-[9px] px-1.5 py-0.5 bg-zinc-800 text-zinc-400">{order.regime}</span>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          {order.pnl !== undefined && order.pnl !== null && (
                            <span className={`text-[10px] font-mono font-bold ${order.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {order.pnl >= 0 ? '+' : ''}{typeof order.pnl === 'number' ? order.pnl.toFixed(2) : order.pnl}
                            </span>
                          )}
                          <button
                            onClick={() => setExpandedOrderJson(expandedOrderJson === idx ? null : idx)}
                            className={`text-[9px] px-1.5 py-0.5 font-mono transition-colors border ${expandedOrderJson === idx ? 'bg-orange-500/10 border-orange-500/30 text-orange-400' : 'border-zinc-700 text-zinc-500 hover:text-zinc-300'}`}
                            data-testid={`order-json-toggle-${idx}`}
                          >JSON</button>
                          <span className={`text-[10px] px-2 py-0.5 ${
                            order.status === 'success' ? 'bg-emerald-500/20 text-emerald-400' : 
                            order.status === 'paper' ? 'bg-blue-500/20 text-blue-400' :
                            order.status === 'CLOSED' ? 'bg-zinc-700/50 text-zinc-300' :
                            'bg-red-500/20 text-red-400'
                          }`}>{order.status}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 text-[10px] text-zinc-500">
                        <span>{order.sent_at ? new Date(order.sent_at).toLocaleString() : 'N/A'}</span>
                        {order.entry_price && <span>Entry: {order.entry_price}</span>}
                        {order.exit_price && <span>Exit: {order.exit_price}</span>}
                        {order.close_reason && <span className="text-zinc-400">{order.close_reason}</span>}
                      </div>
                    </div>
                    {expandedOrderJson === idx && (
                      <div className="px-3 pb-3">
                        <pre className="text-[9px] font-mono bg-black/40 border border-zinc-800/60 p-2.5 overflow-x-auto text-zinc-400 max-h-48 overflow-y-auto" data-testid={`order-json-content-${idx}`}>{JSON.stringify(order.payload || {
                          symbol: order.symbol, action: order.action?.toLowerCase(), quantity: order.quantity, class: "future",
                          ...(order.limit_price ? { limit_price: order.limit_price } : {}),
                          ...(order.stop_price ? { stop_price: order.stop_price } : {})
                        }, null, 2)}</pre>
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
          <div className="mt-4 p-3 bg-zinc-900/30 border border-zinc-800/40">
            <div className="flex items-center gap-2 mb-2"><Gear size={14} className="text-zinc-500" /><span className="text-[10px] uppercase tracking-widest text-zinc-500 font-semibold">Webhook URL</span></div>
            <input
              type="text" value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)}
              placeholder="https://app.signalstack.com/hook/..."
              className="w-full px-3 py-2 bg-zinc-900 border border-zinc-800 text-xs font-mono text-zinc-300 focus:border-orange-500/50 focus:outline-none"
              data-testid="webhook-url-input"
            />
            {webhookUrl !== DEFAULT_WEBHOOK && (
              <button onClick={() => setWebhookUrl(DEFAULT_WEBHOOK)} className="mt-1.5 text-[9px] text-zinc-600 hover:text-zinc-400 transition-colors">Restaurar padrao</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
