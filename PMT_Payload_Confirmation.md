# PickMyTrade Webhook Payload Confirmation Request
**System:** QuantumScalp — Automated Scalping System  
**Broker:** Tradovate (Demo account: DEMO2091727)  
**Instruments:** MNQ1! (Micro Nasdaq) and MES1! (Micro S&P 500)  
**Endpoint:** `https://api.pickmytrade.trade/v2/add-trade-data-latest`

---

## Overview

Our system sends webhook alerts directly from a Python backend (not TradingView) to PickMyTrade, which routes orders to Tradovate. We have four distinct use cases and need confirmation that each payload is correct for our scenario.

---

## Use Case 1 — Open a New Position (Market Entry with SL/TP Bracket + Native Trailing Stop)

**When it fires:** A scalp signal is detected. The system opens a new long or short position at market with a bracket (SL + TP) and a native Tradovate trailing stop that activates once the trade reaches breakeven.

**Intent:**
- Enter at market price (`price: 0`)
- Set a fixed Stop Loss (`sl`) and Take Profit (`tp`) in points relative to fill price
- Enable native Tradovate trailing stop (`trail: 1`) that activates at `trail_trigger` points of profit and trails by `trail_stop` points in `trail_freq` increments
- If already in the same direction, ignore the signal (`same_direction_ignore: true`)
- If in the opposite direction, close that position and reverse (`reverse_order_close: true`)

**JSON payload (BUY example — MNQ):**
```json
{
    "symbol": "MNQ1!",
    "strategy_name": "QuantumScalp",
    "date": "2026-04-15T01:39:00Z",
    "data": "buy",
    "quantity": 1,
    "risk_percentage": 0,
    "price": 0,
    "tp": 12.5,
    "percentage_tp": 0,
    "dollar_tp": 0,
    "sl": 6.0,
    "dollar_sl": 0,
    "percentage_sl": 0,
    "trail": 1,
    "trail_stop": 4.0,
    "trail_trigger": 4.0,
    "trail_freq": 0.25,
    "update_tp": false,
    "update_sl": false,
    "breakeven": 0,
    "breakeven_offset": 0,
    "token": "NTcaee1ec24d8b531590a0",
    "account_id": "DEMO2091727",
    "pyramid": false,
    "same_direction_ignore": true,
    "reverse_order_close": true,
    "multiple_accounts": [
        {
            "token": "NTcaee1ec24d8b531590a0",
            "account_id": "DEMO2091727",
            "risk_percentage": 0,
            "quantity_multiplier": 1
        }
    ]
}
```

**Questions for PMT support:**
1. Is `price: 0` the correct way to request a market order?
2. Are `sl` and `tp` correctly interpreted as points relative to the fill price (not absolute prices)?
3. Does `trail: 1` enable the native Tradovate trailing stop? Is `trail_trigger` the profit threshold (in points) before the trail activates, and `trail_stop` the distance the trail maintains?
4. Is `trail_freq: 0.25` (one tick for MNQ) the correct increment for the trail to update?
5. With `same_direction_ignore: true` and `pyramid: false`, if we are already long and send another BUY, will it be silently ignored?
6. With `reverse_order_close: true`, if we are short and send a BUY, will it close the short and open a long?

---

## Use Case 2 — Close an Individual Trade (SL/TP Hit or Manual Close)

**When it fires:** Our system detects that a trade's SL or TP was hit (via internal monitoring), or the user manually closes a trade from the dashboard. We send the opposite direction to close the position.

**Intent:**
- Close the existing position by sending the opposite action (sell to close a long, buy to close a short)
- Do NOT open a new position in the opposite direction
- The associated bracket orders (SL/TP) should be cancelled automatically by Tradovate when the position closes

**JSON payload (closing a LONG — sending SELL):**
```json
{
    "symbol": "MNQ1!",
    "strategy_name": "QuantumScalp",
    "date": "2026-04-15T01:45:00Z",
    "data": "sell",
    "quantity": 1,
    "price": 0,
    "tp": 0,
    "sl": 0,
    "trail": 0,
    "token": "NTcaee1ec24d8b531590a0",
    "account_id": "DEMO2091727",
    "pyramid": false,
    "same_direction_ignore": true,
    "reverse_order_close": false,
    "multiple_accounts": [
        {
            "token": "NTcaee1ec24d8b531590a0",
            "account_id": "DEMO2091727",
            "risk_percentage": 0,
            "quantity_multiplier": 1
        }
    ]
}
```

**Questions for PMT support:**
1. With `reverse_order_close: false`, will the SELL only close the existing LONG without opening a new SHORT?
2. When the position closes via this SELL, does Tradovate automatically cancel the associated bracket orders (SL stop + TP limit)? Or do we need to cancel them explicitly first?
3. Should we use `data: "close"` instead of `data: "sell"` for this scenario? We tested `data: "close"` and it showed "Position Closed" in Tradovate — is that the preferred approach?

---

## Use Case 3 — End-of-Day Flatten / News Blackout Flatten

**When it fires:** At the end of the trading session (e.g., 4:00 PM ET) or before a high-impact news event, the system must close all open positions and cancel all associated pending orders (SL/TP brackets) for a given symbol.

**Intent:**
- Close any open position for the symbol
- Cancel all working orders (SL stop, TP limit) associated with the position
- One single call that handles everything atomically
- We tested `data: "flat"` and it worked — confirmed position closed and brackets disappeared

**JSON payload:**
```json
{
    "symbol": "MNQ1!",
    "strategy_name": "QuantumScalp",
    "date": "2026-04-15T16:00:00Z",
    "data": "flat",
    "quantity": 1,
    "price": 0,
    "tp": 0,
    "sl": 0,
    "trail": 0,
    "token": "NTcaee1ec24d8b531590a0",
    "account_id": "DEMO2091727",
    "pyramid": false,
    "same_direction_ignore": true,
    "reverse_order_close": false,
    "multiple_accounts": [
        {
            "token": "NTcaee1ec24d8b531590a0",
            "account_id": "DEMO2091727",
            "risk_percentage": 0,
            "quantity_multiplier": 1
        }
    ]
}
```

**Questions for PMT support:**
1. Does `data: "flat"` cancel all working orders (SL/TP brackets) AND close the open position in a single call?
2. If the account is already flat (no open position), does `data: "flat"` do nothing safely (no error, no unintended orders)?
3. We previously tried `data: "cancel"` for cancelling pending orders only, but PMT returned: *"Wrong Action, we support BUY, SELL, CLOSE, FLAT, LONG, SHORT"*. Is `data: "flat"` the correct replacement for cancelling all working orders on a symbol?
4. Should we send `data: "flat"` per symbol (one call for MNQ1!, one for MES1!) or is there a way to flatten the entire account in one call?

---

## Use Case 4 — Signal Reversal (Long to Short or Short to Long)

**When it fires:** A new signal fires in the opposite direction of the current open position (e.g., system is long MNQ and a SELL signal fires).

**Intent:**
- Close the existing position
- Open a new position in the opposite direction
- Cancel the old SL/TP brackets and create new ones for the new position
- All in a single atomic call

**JSON payload (reversing from LONG to SHORT):**
```json
{
    "symbol": "MNQ1!",
    "strategy_name": "QuantumScalp",
    "date": "2026-04-15T10:30:00Z",
    "data": "sell",
    "quantity": 1,
    "risk_percentage": 0,
    "price": 0,
    "tp": 12.5,
    "sl": 6.0,
    "trail": 1,
    "trail_stop": 4.0,
    "trail_trigger": 4.0,
    "trail_freq": 0.25,
    "update_tp": false,
    "update_sl": false,
    "breakeven": 0,
    "breakeven_offset": 0,
    "token": "NTcaee1ec24d8b531590a0",
    "account_id": "DEMO2091727",
    "pyramid": false,
    "same_direction_ignore": true,
    "reverse_order_close": true,
    "multiple_accounts": [
        {
            "token": "NTcaee1ec24d8b531590a0",
            "account_id": "DEMO2091727",
            "risk_percentage": 0,
            "quantity_multiplier": 1
        }
    ]
}
```

**Questions for PMT support:**
1. With `reverse_order_close: true`, will this SELL close the existing LONG and open a new SHORT in one atomic operation?
2. Will the old LONG bracket orders (SL stop + TP limit) be cancelled automatically before the new SHORT brackets are created?
3. Is there any risk of the old brackets executing against the new SHORT position before they are cancelled?

---

## Additional General Questions

1. **Payload completeness:** We previously sent `data: "close"` and `data: "flat"` with minimal payloads (only `symbol`, `token`, `account_id`, `data`) and received 200 OK but "wrong alert" errors. We found that including all fields (`quantity`, `price`, `tp`, `sl`, `trail`, `pyramid`, `same_direction_ignore`, `reverse_order_close`, `multiple_accounts`) resolves this. Is there a minimum required set of fields for each action type?

2. **Queue behavior:** During testing, we noticed that a `data: "close"` sent when no position was open appeared to queue in PMT and then execute against the next position that opened. Is this expected behavior? If so, how do we prevent stale commands from affecting new positions?

3. **`data: "close"` vs opposite direction:** For closing a position, should we prefer `data: "close"` or sending the opposite direction (`data: "sell"` to close a long)? We confirmed both produce a "Position Closed" event in Tradovate but want to know the recommended approach.

4. **MES1! symbol:** We trade both MNQ1! and MES1!. Does the same payload structure apply identically to MES1!?
