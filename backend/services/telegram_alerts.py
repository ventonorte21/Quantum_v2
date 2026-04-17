"""
Telegram Alerts Service
========================
Sends formatted trading alerts to Telegram via Bot API.
Direct httpx calls — no external SDK needed.

Alerts fired on:
  - Position opened (N3 execution)
  - Position closed (manual, SL, TP, EOD)
  - V3 signal generated (optional)
  - System events (feed health, DQ degradation)
"""

import os
import logging
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

_http_client: Optional[httpx.AsyncClient] = None


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=10.0)
    return _http_client


async def send_message(text: str, chat_id: str = "", parse_mode: str = "HTML") -> Dict[str, Any]:
    """Send a message to Telegram. Returns API response dict."""
    target = chat_id or CHAT_ID
    if not BOT_TOKEN or not target:
        logger.warning("Telegram not configured (missing BOT_TOKEN or CHAT_ID)")
        return {"ok": False, "error": "not_configured"}

    try:
        client = await _get_client()
        resp = await client.post(
            f"{API_BASE}/sendMessage",
            json={
                "chat_id": target,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"Telegram API error: {data}")
        return data
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return {"ok": False, "error": str(e)}


def _side_emoji(side: str) -> str:
    return "\U0001F7E2" if side in ("BUY", "LONG") else "\U0001F534"


def _regime_emoji(regime: str) -> str:
    m = {
        "BULL": "\U0001F402",
        "BEAR": "\U0001F43B",
        "TRANSICAO": "\u2194\uFE0F",
        "COMPLACENCIA": "\U0001F634",
        "CAPITULACAO": "\u26A1",
    }
    return m.get(regime, "\u2753")


async def alert_position_opened(position: Dict[str, Any], params: Dict[str, Any] = None):
    """Alert when a new position is opened."""
    side = position.get("side", "?")
    symbol = position.get("symbol", "?")
    regime = position.get("regime", "?")
    archetype = position.get("archetype", "?")
    entry = position.get("entry_price", 0)
    sl = position.get("hard_stop", 0)
    tp = position.get("take_profit", 0)
    qty = position.get("quantity", 1)
    paper = position.get("paper", True)

    tag = "[PAPER] " if paper else ""
    se = _side_emoji(side)
    re = _regime_emoji(regime)

    risk_pts = abs(entry - sl) if entry and sl else 0
    reward_pts = abs(tp - entry) if tp and entry else 0
    rr = round(reward_pts / risk_pts, 1) if risk_pts > 0 else 0

    text = (
        f"{se} <b>{tag}POSITION OPENED</b>\n\n"
        f"<b>{symbol}</b> {side} x{qty}\n"
        f"{re} Regime: <b>{regime}</b> ({archetype})\n\n"
        f"Entry: <code>{entry:.2f}</code>\n"
        f"SL: <code>{sl:.2f}</code> ({risk_pts:.1f} pts)\n"
        f"TP: <code>{tp:.2f}</code> ({reward_pts:.1f} pts)\n"
        f"R:R = <b>{rr}</b>\n\n"
        f"<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    return await send_message(text)


async def alert_position_closed(position: Dict[str, Any], pnl: float = 0, reason: str = "MANUAL"):
    """Alert when a position is closed."""
    side = position.get("side", "?")
    symbol = position.get("symbol", "?")
    regime = position.get("regime", "?")
    entry = position.get("entry_price", 0)
    qty = position.get("quantity", 1)
    paper = position.get("paper", True)

    tag = "[PAPER] " if paper else ""
    pnl_emoji = "\u2705" if pnl >= 0 else "\u274C"
    reason_map = {
        "TAKE_PROFIT": "TP Hit",
        "STOP_LOSS": "SL Hit",
        "TRAILING_STOP": "Trailing SL",
        "BREAK_EVEN": "Break-Even",
        "EOD_FLATTEN": "EOD Flatten",
        "MANUAL": "Manual Close",
    }
    reason_label = reason_map.get(reason, reason)

    text = (
        f"{pnl_emoji} <b>{tag}POSITION CLOSED</b>\n\n"
        f"<b>{symbol}</b> {side} x{qty}\n"
        f"Regime: {regime}\n"
        f"Entry: <code>{entry:.2f}</code>\n"
        f"Reason: <b>{reason_label}</b>\n\n"
        f"P&L: <b>${pnl:+.2f}</b>\n\n"
        f"<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    return await send_message(text)


async def alert_v3_signal(signal: Dict[str, Any]):
    """Alert when V3 generates a trading signal (optional)."""
    symbol = signal.get("symbol", "?")
    action = signal.get("action", "?")
    regime = signal.get("regime", "?")
    confidence = signal.get("confidence_score", 0)
    confluence = signal.get("confluence_score", 0)

    se = _side_emoji(action)
    re = _regime_emoji(regime)

    text = (
        f"{se} <b>V3 SIGNAL</b>\n\n"
        f"<b>{symbol}</b> {action}\n"
        f"{re} Regime: <b>{regime}</b>\n"
        f"Confidence: {confidence:.0%}\n"
        f"Confluence: {confluence:.1f}\n\n"
        f"<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    return await send_message(text)


async def alert_system_event(title: str, detail: str, level: str = "info"):
    """Alert for system events (feed health, DQ, etc.)."""
    emoji_map = {"info": "\u2139\uFE0F", "warning": "\u26A0\uFE0F", "error": "\U0001F6A8", "success": "\u2705"}
    emoji = emoji_map.get(level, "\u2139\uFE0F")

    text = (
        f"{emoji} <b>{title}</b>\n\n"
        f"{detail}\n\n"
        f"<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    return await send_message(text)


async def test_connection(chat_id: str = "") -> Dict[str, Any]:
    """Test Telegram bot connection by sending a test message."""
    text = (
        "\u2705 <b>Quantum Trading V3</b>\n\n"
        "Conexao Telegram ativa!\n"
        "Alertas de trading serao enviados aqui.\n\n"
        f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
    )
    return await send_message(text, chat_id=chat_id)
