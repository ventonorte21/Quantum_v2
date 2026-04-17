"""
Fill Monitor / Trade Journal Routes
====================================
Unified view of all positions and SignalStack orders.
Provides trade journal, stats, and webhook endpoint for fill confirmations.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import io
import csv

fills_router = APIRouter(prefix="/api/fills", tags=["fills"])

_database = None


def set_fills_db(db):
    global _database
    _database = db


# ── Models ──

class FillWebhookPayload(BaseModel):
    """Payload received from SignalStack/broker confirming a fill."""
    order_id: Optional[str] = None
    symbol: str
    side: str
    quantity: int = 1
    fill_price: float
    fill_time: Optional[str] = None
    status: str = "filled"
    commission: float = 0.0
    extra: Dict[str, Any] = Field(default_factory=dict)


# ── Journal ──

PV_MAP = {"MNQ": 2.0, "MES": 5.0, "MYM": 0.5, "M2K": 5.0}


def _compute_pnl(pos: dict) -> float:
    if pos.get("state") == "CLOSED" and pos.get("exit_price") and pos.get("entry_price"):
        sign = 1 if pos.get("side") in ("BUY", "LONG") else -1
        pts = (pos["exit_price"] - pos["entry_price"]) * sign
        pv = PV_MAP.get(pos.get("symbol", ""), 2.0)
        return round(pts * pv * (pos.get("quantity", 1)), 2)
    return pos.get("realized_pnl") or 0.0



@fills_router.get("/journal")
async def get_trade_journal(
    limit: int = 50,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    days: Optional[int] = None,
    trade_type: Optional[str] = None,
):
    """
    Unified trade journal: positions + fill events.
    trade_type: 'all' | 'real' | 'paper'  (default: 'all')
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    tt = (trade_type or "all").lower()

    # Build position query
    pos_query: dict = {}
    if symbol:
        pos_query["symbol"] = symbol
    if status:
        pos_query["state"] = status
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        pos_query["opened_at"] = {"$gte": cutoff}
    if tt == "real":
        pos_query["paper"] = {"$ne": True}
    elif tt == "paper":
        pos_query["paper"] = True

    # Fetch from positions collection
    positions = await _database.positions.find(
        pos_query, {"_id": 0}
    ).sort("opened_at", -1).to_list(length=limit)

    # Enrich with SignalStack orders and P&L
    for pos in positions:
        if not pos.get("orders"):
            trade_sym = pos.get("trade_symbol", "")
            if trade_sym:
                orders = await _database.signalstack_orders.find(
                    {"symbol": {"$regex": f"^{trade_sym[:3]}", "$options": "i"}},
                    {"_id": 0}
                ).sort("sent_at", -1).to_list(length=10)
                pos["orders"] = orders[:5]
            else:
                pos["orders"] = []
        pos["realized_pnl"] = _compute_pnl(pos)

    return {
        "positions": positions,
        "count": len(positions),
    }


# ── Export ──

@fills_router.get("/export")
async def export_journal(
    format: str = "csv",
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    days: Optional[int] = None,
    trade_type: Optional[str] = None,
):
    """Export trade journal as CSV or XLSX."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    tt = (trade_type or "all").lower()
    pos_query: dict = {}
    if symbol:
        pos_query["symbol"] = symbol
    if status:
        pos_query["state"] = status
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        pos_query["opened_at"] = {"$gte": cutoff}
    if tt == "real":
        pos_query["paper"] = {"$ne": True}
    elif tt == "paper":
        pos_query["paper"] = True

    positions = await _database.positions.find(
        pos_query, {"_id": 0}
    ).sort("opened_at", -1).to_list(length=500)

    pv_map = {"MNQ": 2.0, "MES": 5.0, "MYM": 0.5, "M2K": 5.0}

    columns = [
        "id", "symbol", "side", "quantity", "regime", "archetype", "state",
        "entry_price", "exit_price", "hard_stop", "take_profit", "current_stop",
        "realized_pnl", "close_reason", "paper",
        "opened_at", "closed_at", "trade_symbol",
    ]

    rows = []
    for p in positions:
        # Compute P&L
        rpnl = 0.0
        if p.get("state") == "CLOSED" and p.get("exit_price") and p.get("entry_price"):
            sign = 1 if p.get("side") in ("BUY", "LONG") else -1
            pts = (p["exit_price"] - p["entry_price"]) * sign
            pv = pv_map.get(p.get("symbol", ""), 2.0)
            rpnl = round(pts * pv * (p.get("quantity", 1)), 2)

        row = {}
        for c in columns:
            if c == "realized_pnl":
                row[c] = rpnl
            else:
                val = p.get(c, "")
                if isinstance(val, datetime):
                    val = val.isoformat()
                row[c] = val
        rows.append(row)

    filename = f"trade_journal_{datetime.now(timezone.utc).strftime('%Y%m%d')}"

    if format == "xlsx":
        import pandas as pd

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            if rows:
                df = pd.DataFrame(rows, columns=columns)
                df.to_excel(writer, sheet_name="Journal", index=False)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
        )
    else:
        output = io.StringIO()
        if rows:
            w = csv.DictWriter(output, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
        content = output.getvalue()
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )


# ── Stats ──

@fills_router.get("/stats")
async def get_fill_stats(days: int = 30, trade_type: Optional[str] = None):
    """Aggregated trade statistics."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    tt = (trade_type or "all").lower()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    pos_query: dict = {"opened_at": {"$gte": cutoff}}
    if tt == "real":
        pos_query["paper"] = {"$ne": True}
    elif tt == "paper":
        pos_query["paper"] = True

    # All positions in period from unified collection
    positions = await _database.positions.find(
        pos_query,
        {"_id": 0}
    ).to_list(length=500)

    closed = [p for p in positions if p.get("state") == "CLOSED"]
    active = [p for p in positions if p.get("state") != "CLOSED"]

    # Compute P&L for closed positions
    pv_map = {"MNQ": 2.0, "MES": 5.0, "MYM": 0.5, "M2K": 5.0}
    pnls = []
    for p in closed:
        if p.get("exit_price") and p.get("entry_price"):
            sign = 1 if p.get("side") in ("BUY", "LONG") else -1
            pts = (p["exit_price"] - p["entry_price"]) * sign
            pv = pv_map.get(p.get("symbol", ""), 2.0)
            qty = p.get("quantity", 1)
            pnls.append(round(pts * pv * qty, 2))
        elif p.get("realized_pnl"):
            pnls.append(p["realized_pnl"])

    winners = [x for x in pnls if x > 0]
    losers = [x for x in pnls if x <= 0]
    total_pnl = sum(pnls)
    win_rate = round(len(winners) / len(pnls) * 100, 1) if pnls else 0

    # Trades by day
    trades_by_day = {}
    for p in closed:
        day = (p.get("opened_at") or "")[:10]
        if day:
            trades_by_day[day] = trades_by_day.get(day, 0) + 1

    # By regime
    by_regime = {}
    for p in closed:
        regime = p.get("regime", "UNKNOWN")
        if regime not in by_regime:
            by_regime[regime] = {"count": 0, "pnl": 0}
        by_regime[regime]["count"] += 1
        if p.get("exit_price") and p.get("entry_price"):
            sign = 1 if p.get("side") in ("BUY", "LONG") else -1
            pts = (p["exit_price"] - p["entry_price"]) * sign
            pv = pv_map.get(p.get("symbol", ""), 2.0)
            qty = p.get("quantity", 1)
            by_regime[regime]["pnl"] += round(pts * pv * qty, 2)

    # By symbol
    by_symbol = {}
    for p in closed:
        sym = p.get("symbol", "?")
        if sym not in by_symbol:
            by_symbol[sym] = {"count": 0, "pnl": 0}
        by_symbol[sym]["count"] += 1

    # SignalStack order stats
    order_count = await _database.signalstack_orders.count_documents({})
    recent_orders = await _database.signalstack_orders.count_documents(
        {"sent_at": {"$gte": cutoff}}
    )

    return {
        "period_days": days,
        "total_trades": len(closed),
        "active_positions": len(active),
        "total_pnl": round(total_pnl, 2),
        "win_rate": win_rate,
        "winners": len(winners),
        "losers": len(losers),
        "avg_winner": round(sum(winners) / len(winners), 2) if winners else 0,
        "avg_loser": round(sum(losers) / len(losers), 2) if losers else 0,
        "best_trade": round(max(pnls), 2) if pnls else 0,
        "worst_trade": round(min(pnls), 2) if pnls else 0,
        "trades_by_day": trades_by_day,
        "by_regime": by_regime,
        "by_symbol": by_symbol,
        "signalstack_orders_total": order_count,
        "signalstack_orders_period": recent_orders,
    }


# ── Webhook (fill confirmation from broker) ──

@fills_router.post("/webhook")
async def receive_fill_webhook(payload: FillWebhookPayload):
    """
    Receive fill confirmation from SignalStack/broker.
    Updates the position with actual fill price and persists fill record.
    """
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    fill_doc = {
        "order_id": payload.order_id,
        "symbol": payload.symbol,
        "side": payload.side,
        "quantity": payload.quantity,
        "fill_price": payload.fill_price,
        "fill_time": payload.fill_time or datetime.now(timezone.utc).isoformat(),
        "status": payload.status,
        "commission": payload.commission,
        "extra": payload.extra,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }

    await _database.trade_fills.insert_one(fill_doc)
    fill_doc.pop("_id", None)

    # Try to match and update position
    matched = False
    if payload.symbol:
        active_pos = await _database.positions.find_one(
            {
                "trade_symbol": {"$regex": f"^{payload.symbol[:3]}", "$options": "i"},
                "state": {"$ne": "CLOSED"},
            },
            {"_id": 0},
        )
        if active_pos:
            event = {
                "type": "FILL_CONFIRMED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "detail": f"Fill @ {payload.fill_price} x{payload.quantity} ({payload.status})",
            }
            await _database.positions.update_one(
                {"id": active_pos["id"]},
                {"$push": {"events": event}, "$set": {"fill_price": payload.fill_price}},
            )
            matched = True

    return {
        "status": "received",
        "matched_position": matched,
        "fill": fill_doc,
    }


# ── Fill History ──

@fills_router.get("/history")
async def get_fill_history(limit: int = 50):
    """Get raw fill records from webhook."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    fills = await _database.trade_fills.find(
        {}, {"_id": 0}
    ).sort("received_at", -1).to_list(length=limit)

    return {"fills": fills, "count": len(fills)}


# ── Telegram config endpoints ──

@fills_router.get("/telegram/status")
async def get_telegram_status():
    """Check if Telegram alerts are configured."""
    import os
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    configured = bool(bot_token and chat_id)

    # Check alert toggle in autotrading config
    enabled = False
    if _database is not None:
        config = await _database.autotrading_config.find_one({"id": "default"}, {"_id": 0})
        if config:
            enabled = config.get("telegram_alerts_enabled", False)

    return {
        "configured": configured,
        "enabled": enabled,
        "chat_id_set": bool(chat_id),
        "bot_token_set": bool(bot_token),
    }


@fills_router.post("/telegram/test")
async def test_telegram_alert():
    """Send a test message to verify Telegram is working."""
    from services.telegram_alerts import test_connection
    result = await test_connection()
    return {"status": "sent" if result.get("ok") else "error", "result": result}


@fills_router.post("/telegram/toggle")
async def toggle_telegram_alerts(enabled: bool = True):
    """Enable/disable Telegram alerts."""
    if _database is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    await _database.autotrading_config.update_one(
        {"id": "default"},
        {"$set": {"telegram_alerts_enabled": enabled}},
        upsert=True,
    )
    return {"telegram_alerts_enabled": enabled}
