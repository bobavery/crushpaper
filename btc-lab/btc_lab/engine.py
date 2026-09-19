"""The single fill / fee / position engine used by BOTH backtests and paper trading.

Design rules (these are what make the results honest):

* A strategy only ever sees bars up to and including the bar that just CLOSED.
* Orders produced on bar i are executed no earlier than bar i+1:
    - market orders fill at bar i+1 open, plus slippage, taker fee;
    - limit buys fill at the limit price if bar i+1 low <= limit (or at the open if the
      open already gapped below the limit), maker fee;
* Exits are evaluated on every bar against that bar's high/low:
    - take-profit is a resting limit sell: filled at tp price if high >= tp (maker fee);
    - stop-loss is a stop-market: filled at sl price minus slippage if low <= sl (taker fee),
      or at the open if the bar opened below the stop (gap);
    - if the bar OPENS at or beyond a level, that level fills at the open (a gap), because the
      open is known; if the bar opens between the two levels and touches both, we assume the
      STOP hit first (conservative; you cannot know the path inside a bar);
    - a time stop closes the position at that bar's close (taker fee) once max_bars elapsed.
* Fees are charged on notional at every fill; cash is debited/credited exactly.
* Equity is marked to market at each bar close.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class FeeModel:
    """Fees in percent of notional. Slippage is applied to market/stop fills only."""
    name: str = "custom"
    maker_pct: float = 0.40
    taker_pct: float = 0.60
    slippage_pct: float = 0.02  # half-spread + impact for a $5-10k market order on BTC-USD

    def fee(self, notional: float, maker: bool) -> float:
        return notional * (self.maker_pct if maker else self.taker_pct) / 100.0


# Presets. NOTE: exchange fee schedules change; verify against the exchange's fee page and
# override with --maker/--taker on the command line. The numbers below are the values used in
# the research brief (docs/RESEARCH.md) at the time of writing.
FEE_PRESETS = {
    "zero": FeeModel("zero", 0.0, 0.0, 0.0),
    "coinbase_intro": FeeModel("coinbase_intro", 0.40, 0.60, 0.02),
    "coinbase_10k": FeeModel("coinbase_10k", 0.25, 0.40, 0.02),
    "coinbase_50k": FeeModel("coinbase_50k", 0.15, 0.25, 0.02),
    "kraken_base": FeeModel("kraken_base", 0.25, 0.40, 0.02),
    "kraken_50k": FeeModel("kraken_50k", 0.16, 0.26, 0.02),
}


@dataclass
class Order:
    """An instruction produced by a strategy at the close of a bar."""
    side: str                      # "buy" or "sell"
    kind: str = "market"           # "market" or "limit"
    limit_price: Optional[float] = None
    cash: Optional[float] = None   # notional to deploy for buys (None = strategy default)
    tp_pct: Optional[float] = None  # take-profit above entry, in percent
    sl_pct: Optional[float] = None  # stop-loss below entry, in percent
    max_bars: Optional[int] = None  # time stop
    tag: str = ""
    ttl_bars: int = 1               # limit orders expire after this many bars unfilled


@dataclass
class Position:
    id: int
    entry_time: pd.Timestamp
    entry_bar: int
    entry_price: float
    qty: float
    cost: float                    # cash actually spent incl. fee
    tp_price: Optional[float]
    sl_price: Optional[float]
    max_bars: Optional[int]
    tag: str = ""


@dataclass
class Trade:
    id: int
    tag: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    bars_held: int
    exit_bar: int
    pnl: float                     # net of all fees
    ret_pct: float                 # pnl / cost, percent
    fees: float
    exit_reason: str


@dataclass
class EngineState:
    cash: float
    positions: list = field(default_factory=list)
    pending: list = field(default_factory=list)   # (Order, created_bar)
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)  # (time, equity)
    fees_paid: float = 0.0
    next_id: int = 1
    last_bar: int = -1
    bars_in_market: int = 0


class Engine:
    """Bar-by-bar simulator. Feed it a growing DataFrame and call step(i) for each new bar."""

    def __init__(self, strategy, fees: FeeModel, initial_cash: float = 10_000.0,
                 stake_fraction: float = 1.0, max_positions: int = 1):
        self.strategy = strategy
        self.fees = fees
        self.initial_cash = float(initial_cash)
        self.stake_fraction = float(stake_fraction)
        self.max_positions = int(max_positions)
        self.state = EngineState(cash=float(initial_cash))

    # ------------------------------------------------------------------ helpers
    def equity(self, price: float) -> float:
        st = self.state
        return st.cash + sum(p.qty * price for p in st.positions)

    def _record_trade(self, pos: Position, exit_time, exit_price: float, bar: int,
                      fee_out: float, reason: str) -> None:
        st = self.state
        proceeds = pos.qty * exit_price - fee_out
        pnl = proceeds - pos.cost
        st.cash += proceeds
        st.fees_paid += fee_out
        st.trades.append(Trade(
            id=pos.id, tag=pos.tag, entry_time=pos.entry_time, exit_time=exit_time,
            entry_price=pos.entry_price, exit_price=exit_price, qty=pos.qty,
            bars_held=bar - pos.entry_bar, exit_bar=bar, pnl=pnl, ret_pct=100.0 * pnl / pos.cost,
            fees=fee_out + (pos.cost - pos.qty * pos.entry_price), exit_reason=reason))

    def _open_position(self, order: Order, fill_price: float, maker: bool, time, bar: int) -> bool:
        st = self.state
        if len(st.positions) >= self.max_positions:
            return False
        cash_to_use = order.cash if order.cash is not None else self.stake_fraction * self.equity(fill_price)
        cash_to_use = min(cash_to_use, st.cash)
        if cash_to_use <= 1.0:
            return False
        # spend cash_to_use in total: qty * price + fee(qty*price) = cash_to_use
        rate = (self.fees.maker_pct if maker else self.fees.taker_pct) / 100.0
        notional = cash_to_use / (1.0 + rate)
        fee = notional * rate
        qty = notional / fill_price
        st.cash -= cash_to_use
        st.fees_paid += fee
        st.positions.append(Position(
            id=st.next_id, entry_time=time, entry_bar=bar, entry_price=fill_price, qty=qty,
            cost=cash_to_use,
            tp_price=fill_price * (1 + order.tp_pct / 100.0) if order.tp_pct else None,
            sl_price=fill_price * (1 - order.sl_pct / 100.0) if order.sl_pct else None,
            max_bars=order.max_bars, tag=order.tag))
        st.next_id += 1
        return True

    def _close_position(self, pos: Position, price: float, maker: bool, time, bar: int, reason: str,
                        slip: bool) -> None:
        if slip:
            price = price * (1 - self.fees.slippage_pct / 100.0)
        fee = self.fees.fee(pos.qty * price, maker)
        self._record_trade(pos, time, price, bar, fee, reason)
        self.state.positions.remove(pos)

    # ------------------------------------------------------------------ main step
    def _arrays(self, df: pd.DataFrame):
        key = frame_key(df)
        if getattr(self, "_arr_key", None) != key:
            self._arr = (df["time"].to_numpy(), df["open"].to_numpy(dtype=float), df["high"].to_numpy(dtype=float),
                         df["low"].to_numpy(dtype=float), df["close"].to_numpy(dtype=float))
            self._arr_key = key
            self._arr_df = df  # keep the frame alive so its id cannot be recycled while cached
        return self._arr

    def step(self, df: pd.DataFrame, i: int, act: bool = True) -> None:
        """Process bar i of df (time/open/high/low/close, ascending).

        act=False fills nothing new and asks the strategy for nothing: the bar is only used to
        warm indicators and mark equity (paper accounts use this for bars before they went live).
        """
        st = self.state
        if i <= st.last_bar:
            return
        times, opens, highs, lows, closes = self._arrays(df)
        t = pd.Timestamp(times[i])
        o, h, l, c = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])

        # 1) fill orders that were created on earlier bars (never on this bar)
        still_pending = []
        for order, created in st.pending:
            filled = False
            if order.side == "buy":
                if order.kind == "market":
                    px = o * (1 + self.fees.slippage_pct / 100.0)
                    filled = self._open_position(order, px, maker=False, time=t, bar=i)
                    if not filled:
                        continue  # market order rejected (no cash / max positions): drop it
                elif order.kind == "limit" and order.limit_price is not None:
                    if l <= order.limit_price:
                        px = min(o, order.limit_price)  # gap below the limit fills at the open
                        filled = self._open_position(order, px, maker=True, time=t, bar=i)
            elif order.side == "sell":
                # explicit sell of all positions at the open (used by trend/regime strategies)
                for pos in list(st.positions):
                    self._close_position(pos, o, maker=False, time=t, bar=i, reason="signal", slip=True)
                filled = True
            if not filled and (i - created) < order.ttl_bars:
                still_pending.append((order, created))
        st.pending = still_pending

        # 2) evaluate exits on this bar's range for positions opened on EARLIER bars.
        #    Positions opened at this bar's open are also exposed to this bar's range, but we
        #    only allow their STOP to trigger (not the target) to stay conservative.
        for pos in list(st.positions):
            opened_this_bar = pos.entry_bar == i
            if (not opened_this_bar) and pos.tp_price is not None and o >= pos.tp_price:
                # opened at/above the resting limit sell: it filled at the open before any intra-bar path
                self._close_position(pos, o, maker=True, time=t, bar=i, reason="target", slip=False)
                continue
            stop_hit = pos.sl_price is not None and l <= pos.sl_price
            tp_hit = (not opened_this_bar) and pos.tp_price is not None and h >= pos.tp_price
            if stop_hit:
                px = min(o, pos.sl_price)  # gap through the stop fills at the open
                self._close_position(pos, px, maker=False, time=t, bar=i, reason="stop", slip=True)
            elif tp_hit:
                self._close_position(pos, pos.tp_price, maker=True, time=t, bar=i, reason="target", slip=False)
            elif pos.max_bars is not None and (i - pos.entry_bar) >= pos.max_bars:
                self._close_position(pos, c, maker=False, time=t, bar=i, reason="time", slip=True)

        # 3) mark to market at the close
        st.equity_curve.append((t, self.equity(c)))
        if st.positions:
            st.bars_in_market += 1

        # 4) ask the strategy for new orders using data up to and including this bar
        if act:
            orders = self.strategy.on_bar(df, i, st) or []
            for order in orders:
                st.pending.append((order, i))
        st.last_bar = i

    def run(self, df: pd.DataFrame, start: int = 0) -> EngineState:
        for i in range(start, len(df)):
            self.step(df, i)
        return self.state

    def liquidate(self, df: pd.DataFrame) -> None:
        """Close everything at the last close so backtests can be compared fairly."""
        st = self.state
        if not st.positions:
            return
        i = len(df) - 1
        bar = df.iloc[i]
        for pos in list(st.positions):
            self._close_position(pos, float(bar["close"]), maker=False, time=bar["time"], bar=i,
                                 reason="end", slip=True)
        if st.equity_curve:
            st.equity_curve[-1] = (bar["time"], st.cash)

    # ------------------------------------------------------------------ serialisation
    def to_dict(self) -> dict:
        st = self.state
        return {
            "cash": st.cash, "fees_paid": st.fees_paid, "next_id": st.next_id, "last_bar": st.last_bar,
            "bars_in_market": st.bars_in_market,
            "positions": [{**asdict(p), "entry_time": str(p.entry_time)} for p in st.positions],
            "pending": [[asdict(o), created] for o, created in st.pending],
            "trades": [{**asdict(tr), "entry_time": str(tr.entry_time), "exit_time": str(tr.exit_time)}
                       for tr in st.trades],
            "equity_curve": [[str(t), e] for t, e in st.equity_curve],
        }

    def load_dict(self, d: dict) -> None:
        st = self.state
        st.cash = d["cash"]; st.fees_paid = d["fees_paid"]; st.next_id = d["next_id"]; st.last_bar = d["last_bar"]
        st.bars_in_market = d.get("bars_in_market", 0)
        st.positions = [Position(**{**p, "entry_time": pd.Timestamp(p["entry_time"])}) for p in d["positions"]]
        st.pending = [(Order(**o), created) for o, created in d["pending"]]
        st.trades = [Trade(**{**tr, "entry_time": pd.Timestamp(tr["entry_time"]),
                              "exit_time": pd.Timestamp(tr["exit_time"])}) for tr in d["trades"]]
        st.equity_curve = [(pd.Timestamp(t), e) for t, e in d["equity_curve"]]


def frame_key(df: pd.DataFrame) -> tuple:
    """Cache key for a candle frame: identity plus content of the last bar, so a recycled id or an
    in-place edit of the newest bar cannot return stale arrays."""
    n = len(df)
    if n == 0:
        return (id(df), 0)
    return (id(df), n, df["time"].iloc[-1], float(df["close"].iloc[-1]), df["time"].iloc[0])


def trades_frame(state: EngineState) -> pd.DataFrame:
    if not state.trades:
        return pd.DataFrame(columns=[f.name for f in Trade.__dataclass_fields__.values()])
    return pd.DataFrame([asdict(t) for t in state.trades])


def equity_frame(state: EngineState) -> pd.DataFrame:
    if not state.equity_curve:
        return pd.DataFrame(columns=["time", "equity"])
    return pd.DataFrame(state.equity_curve, columns=["time", "equity"])
