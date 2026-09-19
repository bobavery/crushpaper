"""Strategies. Each one turns closed bars into Orders; the Engine does all filling and accounting.

A strategy must never read df beyond index i (the bar that just closed). Indicator columns are
computed with causal rolling windows and cached per DataFrame length, so paper trading (a growing
frame) and backtesting (a fixed frame) produce identical decisions for identical bars.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .engine import Order, frame_key


class Strategy:
    name = "base"
    warmup = 1  # bars needed before the first decision

    def __init__(self):
        self._cache_key = None
        self._feat = None

    def features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Override to compute causal indicator columns. Cached per (id(df), len(df))."""
        return pd.DataFrame(index=df.index)

    def _features(self, df: pd.DataFrame) -> dict:
        """Causal feature arrays (numpy) plus 'close'/'high'/'low'/'time', cached per frame length."""
        key = frame_key(df)
        if key != self._cache_key:
            feat = self.features(df)
            self._feat = {c: feat[c].to_numpy(dtype=float) for c in feat.columns}
            self._feat["close"] = df["close"].to_numpy(dtype=float)
            self._feat["high"] = df["high"].to_numpy(dtype=float)
            self._feat["low"] = df["low"].to_numpy(dtype=float)
            self._cache_key = key
            self._cache_df = df  # keep the frame alive so its id cannot be recycled while cached
        return self._feat

    def on_bar(self, df: pd.DataFrame, i: int, state) -> list[Order]:
        return []

    def describe(self) -> dict:
        return {"name": self.name}


class BuyAndHold(Strategy):
    name = "buy_and_hold"

    def on_bar(self, df, i, state):
        if not state.positions and not state.pending and not state.trades:
            return [Order("buy", "market", tag="hold")]
        return []


class Flip(Strategy):
    """Buy on a trigger, sell at +tp% (limit), -sl% (stop) or after max_bars (time stop).

    entry:
      immediate  re-enter at the next open whenever flat (pure flipping)
      dip        rest a limit buy dip_pct below the last close (maker fee), expiring after ttl bars
      zscore     enter when (close - SMA(n)) / SD(n) <= z_k          (mean reversion)
      rsi        enter when RSI(rsi_n) <= rsi_k                        (mean reversion)
      breakout   enter when close >= highest high of the previous n bars (momentum)
    trend_n:  if set, only enter when close > SMA(trend_n)  (regime filter, in bars)
    cooldown: bars to wait after a stop-out before the next entry
    """
    name = "flip"

    def __init__(self, tp_pct: float = 2.0, sl_pct: Optional[float] = None, max_bars: Optional[int] = None,
                 entry: str = "immediate", n: int = 24, z_k: float = -1.5, rsi_n: int = 14, rsi_k: float = 30.0,
                 dip_pct: float = 1.0, ttl: int = 6, trend_n: Optional[int] = None, cooldown: int = 0,
                 vol_n: Optional[int] = None, vol_max: Optional[float] = None):
        super().__init__()
        self.tp_pct, self.sl_pct, self.max_bars = tp_pct, sl_pct, max_bars
        self.entry, self.n, self.z_k, self.rsi_n, self.rsi_k = entry, n, z_k, rsi_n, rsi_k
        self.dip_pct, self.ttl, self.trend_n, self.cooldown = dip_pct, ttl, trend_n, cooldown
        self.vol_n, self.vol_max = vol_n, vol_max
        self.warmup = max(n, rsi_n, trend_n or 0, vol_n or 0) + 2

    def features(self, df):
        f = pd.DataFrame(index=df.index)
        c = df["close"]
        if self.entry == "zscore":
            f["z"] = ind.zscore(c, self.n)
        elif self.entry == "rsi":
            f["rsi"] = ind.rsi(c, self.rsi_n)
        elif self.entry == "breakout":
            f["hh"] = ind.rolling_high(df["high"], self.n).shift(1)
        if self.trend_n:
            f["trend"] = ind.sma(c, self.trend_n)
        if self.vol_n:
            f["vol"] = ind.realized_vol(c, self.vol_n)
        return f

    def _blocked(self, df, i, state) -> bool:
        if i < self.warmup or state.positions or state.pending:
            return True
        if self.cooldown and state.trades:
            last = state.trades[-1]
            if last.exit_reason == "stop" and (i - last.exit_bar) < self.cooldown:
                return True
        f = self._features(df)
        c = f["close"][i]
        if self.trend_n and not (c > f["trend"][i]):
            return True
        if self.vol_n and self.vol_max is not None and not (f["vol"][i] <= self.vol_max):
            return True
        return False

    def _order(self, kind="market", limit=None) -> Order:
        return Order("buy", kind, limit_price=limit, tp_pct=self.tp_pct, sl_pct=self.sl_pct,
                     max_bars=self.max_bars, tag=self.entry, ttl_bars=self.ttl if kind == "limit" else 1)

    def on_bar(self, df, i, state):
        if self._blocked(df, i, state):
            return []
        f = self._features(df)
        c = f["close"][i]
        if self.entry == "immediate":
            return [self._order()]
        if self.entry == "dip":
            return [self._order("limit", c * (1 - self.dip_pct / 100.0))]
        if self.entry == "zscore":
            z = f["z"][i]
            return [self._order()] if np.isfinite(z) and z <= self.z_k else []
        if self.entry == "rsi":
            r = f["rsi"][i]
            return [self._order()] if np.isfinite(r) and r <= self.rsi_k else []
        if self.entry == "breakout":
            hh = f["hh"][i]
            return [self._order()] if np.isfinite(hh) and c >= hh else []
        raise ValueError(self.entry)

    def describe(self):
        return {"name": self.name, "entry": self.entry, "tp_pct": self.tp_pct, "sl_pct": self.sl_pct,
                "max_bars": self.max_bars, "n": self.n, "z_k": self.z_k, "rsi_n": self.rsi_n, "rsi_k": self.rsi_k,
                "dip_pct": self.dip_pct, "ttl": self.ttl, "trend_n": self.trend_n, "cooldown": self.cooldown,
                "vol_n": self.vol_n, "vol_max": self.vol_max}


class Grid(Strategy):
    """Spot grid: `levels` limit buys spaced `step_pct` apart below an anchor price.

    Each filled level sells at +tp_pct (default = step). The anchor trails UP when price rises
    a full step above it with nothing held; it never trails down, which is exactly why grids get
    stuck fully invested in downtrends. Use max_positions=levels in the Engine.
    """
    name = "grid"

    def __init__(self, step_pct: float = 1.0, levels: int = 5, tp_pct: Optional[float] = None,
                 sl_pct: Optional[float] = None, cash_per_level: Optional[float] = None):
        super().__init__()
        self.step_pct, self.levels = step_pct, levels
        self.tp_pct = tp_pct if tp_pct is not None else step_pct
        self.sl_pct = sl_pct
        self.cash_per_level = cash_per_level
        self.anchor = None
        self.warmup = 1

    def on_bar(self, df, i, state):
        c = self._features(df)["close"][i]
        if self.anchor is None:
            self.anchor = c
        if not state.positions and c > self.anchor * (1 + self.step_pct / 100.0):
            self.anchor = c
        held = {p.tag for p in state.positions} | {o.tag for o, _ in state.pending}
        per_level = self.cash_per_level or (state.cash + sum(p.cost for p in state.positions)) / self.levels
        orders = []
        for k in range(1, self.levels + 1):
            tag = f"L{k}"
            if tag in held:
                continue
            price = self.anchor * (1 - self.step_pct / 100.0) ** k
            orders.append(Order("buy", "limit", limit_price=price, cash=min(per_level, state.cash),
                                tp_pct=self.tp_pct, sl_pct=self.sl_pct, tag=tag, ttl_bars=1))
        return orders

    def describe(self):
        return {"name": self.name, "step_pct": self.step_pct, "levels": self.levels, "tp_pct": self.tp_pct,
                "sl_pct": self.sl_pct}


class TrendFollow(Strategy):
    """Long when close > SMA(n), flat otherwise. The classic regime baseline (n=200 on daily bars)."""
    name = "trend"

    def __init__(self, n: int = 200, band_pct: float = 0.0):
        super().__init__()
        self.n, self.band_pct = n, band_pct
        self.warmup = n + 1

    def features(self, df):
        return pd.DataFrame({"ma": ind.sma(df["close"], self.n)}, index=df.index)

    def on_bar(self, df, i, state):
        if i < self.warmup:
            return []
        f = self._features(df)
        ma = f["ma"][i]
        c = f["close"][i]
        long = bool(state.positions) or any(o.side == "buy" for o, _ in state.pending)
        if not long and c > ma * (1 + self.band_pct / 100.0):
            return [Order("buy", "market", tag="trend")]
        if long and c < ma * (1 - self.band_pct / 100.0) and not any(o.side == "sell" for o, _ in state.pending):
            return [Order("sell", "market", tag="trend")]
        return []

    def describe(self):
        return {"name": self.name, "n": self.n, "band_pct": self.band_pct}


def make(name: str, **kw) -> Strategy:
    table = {"flip": Flip, "grid": Grid, "trend": TrendFollow, "buy_and_hold": BuyAndHold, "hold": BuyAndHold}
    if name not in table:
        raise ValueError(f"unknown strategy {name}; choose from {sorted(table)}")
    return table[name](**kw)
