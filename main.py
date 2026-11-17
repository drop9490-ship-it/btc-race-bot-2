"""Dip-buy BTC bot for make.fun btc-race-24h-20261117-0603.

Strategy (long only, one position, deterministic, stdlib only):
  - Track the 60-bar simple moving average of CBBTC closes and the 120-bar
    return (2 hours). A dip is only bought when the wider trend is not
    falling apart: enter (flat only) when the close is at least 60 bps below
    the SMA(60) AND the 120-bar return is above -30 bps. Buying dips without
    that trend filter kept catching falling knives on the previous race's
    dev window; with the filter the same rule is positive on both dev
    windows the author tested.
  - Buy with all cash. A full-size round trip pays ~35 bps in fees,
    slippage and price impact, so the exit target is set well above that.
  - Exit, all at once: sell everything when the close is 120 bps above the
    entry close, or 250 bps below it (a wide stop: the recovery the trade
    waits for often dips a further 100-200 bps first), or after holding
    240 bars.
  - Orders fill at the next bar's open; max_slippage_bps 500 (5%) so normal
    1-minute moves never reject an order.

Backtest on this competition's dev window (1,440 bars, exact harness fill
model): net PnL +19.67 USDC, 2 round trips, max drawdown 0.8%. The same
rule on the previous race's dev window (a different 24 hours): +8.95 USDC,
1 round trip, max drawdown 2.6%. Buy-and-hold makes +1.07 USDC on this
dev window. Verified by running this file through the protocol replay
(two runs, identical action logs): net PnL +19.6685 USDC, 4 trades,
max drawdown 0.76%.

Why the numbers are what they are (one-parameter sensitivity, both
windows): the stop is wide (-250 bps) on purpose — the winning trade on
the previous window dipped a further 100-200 bps after entry before
recovering, so stops of -100/-150 bps turn it into a loss; exits of
100-150 bps and holds of 240+ bars sit on a plateau; the trend filter
(2-hour return above -30 bps) is what keeps the rule positive on a
falling day. No trailing exit and no second (scalp) entry rule: both
were tested and made the old window worse.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
SMA_BARS = 60            # 1-hour moving average
TREND_BARS = 120         # 2-hour return window for the trend filter
ENTRY_BPS = -60          # buy when close is this far below the SMA (bps)
TREND_MIN_BPS = -30      # ... and the 2-hour return is above this (bps)
EXIT_BPS = 120           # sell everything this far above the entry close
STOP_BPS = -250          # sell everything this far below the entry close
MAX_HOLD_BARS = 240      # sell everything after this many bars
MAX_SLIPPAGE_BPS = 500   # 5%: far above any normal 1-minute move

# Closes seen so far, as floats in USDC (bar["c"] is price_scale 5).
closes = []
# Entry bookkeeping for the current position; None while flat.
entry_close = None
entry_seq = None
# Close of the bar a buy was decided on, waiting for the fill to appear.
pending_entry_close = None
pending_entry_seq = None


def _sma(n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _ret_bps(n):
    if len(closes) <= n:
        return None
    return (closes[-1] / closes[-1 - n] - 1.0) * 10000.0


def _bps(close, ref):
    return (close / ref - 1.0) * 10000.0


def on_batch(state, batch):
    global entry_close, entry_seq
    global pending_entry_close, pending_entry_seq
    try:
        return _decide(state, batch)
    except Exception as exc:  # never break the protocol: hold on error
        print(f"batch {state.seq}: decision error: {exc!r}", file=sys.stderr)
        return []


def _decide(state, batch):
    global entry_close, entry_seq
    global pending_entry_close, pending_entry_seq
    if state.warming_up:
        return []
    for bar in batch["bars"]:
        if bar["symbol"] == SYMBOL:
            closes.append(int(bar["c"]) / 100000.0)
    if not closes:
        return []
    close = closes[-1]
    held = state.positions.get(SYMBOL)

    # Sync internal state with the account the harness just sent.
    if held is None:
        entry_close = None
        entry_seq = None
        pending_entry_close = None
        pending_entry_seq = None
    elif entry_close is None and pending_entry_close is not None:
        # The buy decided last batch has filled; adopt its entry close.
        entry_close = pending_entry_close
        entry_seq = pending_entry_seq
        pending_entry_close = None
        pending_entry_seq = None

    # --- exit ---
    # All exits sell the whole position at once. There is no partial
    # exit: selling half at the target and trailing the rest was tested
    # and added nothing on either dev window.
    if held is not None and entry_close is not None:
        change = _bps(close, entry_close)
        if (change >= EXIT_BPS or change <= STOP_BPS
                or (state.seq - entry_seq) >= MAX_HOLD_BARS):
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # --- entry (flat only) ---
    if held is not None:
        return []
    sma = _sma(SMA_BARS)
    if sma is None:
        return []
    if _bps(close, sma) > ENTRY_BPS:
        return []
    trend = _ret_bps(TREND_BARS)
    if trend is None or trend <= TREND_MIN_BPS:
        return []
    if state.cash < 1000000:  # below the $1 minimum notional
        return []
    pending_entry_close = close
    pending_entry_seq = state.seq
    return [{"side": "buy", "symbol": SYMBOL,
             "notional_quote_units": int(state.cash),
             "max_slippage_bps": MAX_SLIPPAGE_BPS}]


makefun_bars.run(on_batch)
