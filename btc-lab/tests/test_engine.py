import math
import pandas as pd
import pytest

from btc_lab.engine import Engine, FeeModel, Order, FEE_PRESETS
from btc_lab.strategies import Strategy, Flip, Grid, BuyAndHold, TrendFollow
from btc_lab.data import synthetic, normalise, resample
from btc_lab.metrics import summarize


def bars(rows, start="2025-01-01"):
    t = pd.date_range(start, periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame({"time": t, "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows], "volume": 1.0})


class OneShot(Strategy):
    """Emit one given order on bar `at`."""
    def __init__(self, order, at=0):
        super().__init__(); self.order = order; self.at = at
    def on_bar(self, df, i, state):
        return [self.order] if i == self.at else []


ZERO = FEE_PRESETS["zero"]
FEE = FeeModel("t", maker_pct=0.40, taker_pct=0.60, slippage_pct=0.0)


def test_market_order_fills_next_open_not_same_bar():
    df = bars([(100, 101, 99, 100), (110, 111, 109, 110), (120, 121, 119, 120)])
    eng = Engine(OneShot(Order("buy", "market"), at=0), ZERO, 1000)
    eng.run(df)
    pos = eng.state.positions[0]
    assert pos.entry_price == 110 and pos.entry_bar == 1  # bar 1 open, not bar 0 close


def test_fee_math_exact():
    df = bars([(100, 100, 100, 100), (100, 100, 100, 100), (101, 101, 101, 101)])
    eng = Engine(OneShot(Order("buy", "market", tp_pct=2.0), at=0), FEE, 1000)
    eng.run(df)
    # entry: spend 1000 total: notional = 1000/1.006, fee = notional*0.006
    notional = 1000 / 1.006
    assert math.isclose(eng.state.positions[0].qty, notional / 100)
    assert math.isclose(eng.state.fees_paid, notional * 0.006)
    eng2 = Engine(OneShot(Order("buy", "market", tp_pct=2.0), at=0), FEE, 1000)
    df2 = bars([(100, 100, 100, 100), (100, 100, 100, 100), (101, 103, 100, 103)])
    eng2.run(df2)
    assert len(eng2.state.trades) == 1
    t = eng2.state.trades[0]
    assert t.exit_price == 102 and t.exit_reason == "target"
    qty = notional / 100
    proceeds = qty * 102 * (1 - 0.004)
    assert math.isclose(eng2.state.cash, proceeds)
    assert math.isclose(t.pnl, proceeds - 1000)


def test_stop_beats_target_when_both_in_bar():
    df = bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 105, 95, 100)])
    eng = Engine(OneShot(Order("buy", "market", tp_pct=2.0, sl_pct=2.0), at=0), ZERO, 1000)
    eng.run(df)
    assert eng.state.trades[0].exit_reason == "stop"
    assert eng.state.trades[0].exit_price == 98


def test_gap_through_stop_fills_at_open():
    df = bars([(100, 100, 100, 100), (100, 100, 100, 100), (90, 91, 89, 90)])
    eng = Engine(OneShot(Order("buy", "market", sl_pct=2.0), at=0), ZERO, 1000)
    eng.run(df)
    assert eng.state.trades[0].exit_price == 90


def test_target_not_checked_on_entry_bar_but_stop_is():
    # bar 1: entry at open 100, high 103 (would hit +2%), low 100 -> no exit (target ignored on entry bar)
    df = bars([(100, 100, 100, 100), (100, 103, 100, 101), (101, 101, 101, 101)])
    eng = Engine(OneShot(Order("buy", "market", tp_pct=2.0), at=0), ZERO, 1000)
    eng.run(df)
    assert len(eng.state.trades) == 0
    df2 = bars([(100, 100, 100, 100), (100, 100, 97, 99), (99, 99, 99, 99)])
    eng2 = Engine(OneShot(Order("buy", "market", sl_pct=2.0), at=0), ZERO, 1000)
    eng2.run(df2)
    assert eng2.state.trades[0].exit_reason == "stop"


def test_limit_buy_fills_only_when_touched_and_expires():
    df = bars([(100, 100, 100, 100), (100, 101, 99.5, 100), (100, 101, 98, 99), (99, 99, 99, 99)])
    eng = Engine(OneShot(Order("buy", "limit", limit_price=99.0, ttl_bars=3), at=0), ZERO, 1000)
    eng.run(df)
    assert eng.state.positions[0].entry_price == 99.0 and eng.state.positions[0].entry_bar == 2
    eng2 = Engine(OneShot(Order("buy", "limit", limit_price=90.0, ttl_bars=2), at=0), ZERO, 1000)
    eng2.run(df)
    assert not eng2.state.positions and not eng2.state.pending


def test_time_stop():
    df = bars([(100, 100, 100, 100)] * 6)
    eng = Engine(OneShot(Order("buy", "market", max_bars=3), at=0), ZERO, 1000)
    eng.run(df)
    assert eng.state.trades[0].exit_reason == "time" and eng.state.trades[0].bars_held == 3


def test_immediate_flip_equals_hold_minus_fees_when_price_only_rises():
    # monotone rise: each flip pays fees; net must be below buy and hold
    rows = [(100 + i, 100 + i + 0.5, 100 + i - 0.2, 100 + i + 0.4) for i in range(200)]
    df = bars(rows)
    eng = Engine(Flip(tp_pct=1.0, entry="immediate"), FEE, 1000)
    eng.run(df); eng.liquidate(df)
    s = summarize(eng.state, df, 1000, fee_rt_pct_for_hold=1.2)
    assert s["n_trades"] > 20
    assert s["total_return_pct"] < s["buy_hold_return_pct"]


def test_no_lookahead_flip_decisions_identical_incremental_vs_full():
    df = synthetic(600, seed=3)
    full = Engine(Flip(tp_pct=1.5, sl_pct=1.5, entry="zscore", n=24), FEE, 5000)
    full.run(df)
    inc = Engine(Flip(tp_pct=1.5, sl_pct=1.5, entry="zscore", n=24), FEE, 5000)
    for i in range(len(df)):
        inc.step(df.iloc[: i + 1].reset_index(drop=True), i)
    a = [(t.entry_price, t.exit_price, t.exit_reason) for t in full.state.trades]
    b = [(t.entry_price, t.exit_price, t.exit_reason) for t in inc.state.trades]
    assert a == b and len(a) > 0
    assert math.isclose(full.state.cash, inc.state.cash)


def test_grid_places_levels_and_sells_each_at_target():
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 97.9, 98), (98, 100.1, 98, 100), (100, 100, 100, 100)]
    df = bars(rows)
    g = Grid(step_pct=1.0, levels=3)
    eng = Engine(g, ZERO, 3000, max_positions=3)
    eng.run(df)
    # bar 2 low 97.9 touches levels 99, 98.01 (not 97.03)
    assert len(eng.state.trades) == 2 and all(t.exit_reason == "target" for t in eng.state.trades)
    assert all(abs(t.ret_pct - 1.0) < 1e-9 for t in eng.state.trades)


def test_serialisation_roundtrip():
    df = synthetic(300, seed=1)
    eng = Engine(Flip(tp_pct=1.0, sl_pct=2.0, entry="immediate"), FEE, 1000)
    eng.run(df)
    d = eng.to_dict()
    eng2 = Engine(Flip(tp_pct=1.0, sl_pct=2.0, entry="immediate"), FEE, 1000)
    eng2.load_dict(d)
    assert eng2.state.cash == eng.state.cash and len(eng2.state.trades) == len(eng.state.trades)
    assert eng2.state.last_bar == eng.state.last_bar


def test_trend_follow_daily():
    df = synthetic(400, seed=5, bar_seconds=86400, annual_drift=0.8)
    eng = Engine(TrendFollow(n=50), FEE, 1000)
    eng.run(df); eng.liquidate(df)
    assert eng.state.equity_curve[-1][1] > 0


def test_resample_minute_to_hour():
    t = pd.date_range("2025-01-01", periods=120, freq="1min", tz="UTC")
    df = pd.DataFrame({"time": t, "open": range(120), "high": [x + 1 for x in range(120)],
                       "low": range(120), "close": range(120), "volume": 1.0})
    h = resample(df, "1h")
    assert len(h) == 2 and h["open"].iloc[0] == 0 and h["close"].iloc[0] == 59 and h["high"].iloc[1] == 120
    assert h["volume"].iloc[0] == 60


def test_normalise_layouts():
    a = normalise(pd.DataFrame({"timestamp": [1736208060, 1736208120], "open": [1, 2], "high": [2, 3], "low": [0, 1], "close": [1, 2], "volume": [1, 1]}))
    b = normalise(pd.DataFrame({"UNIX_TIMESTAMP": [1736208060], "OPEN": [1], "HIGH": [2], "LOW": [0], "CLOSE": [1], "VOLUME": [1]}))
    c = normalise(pd.DataFrame({"time": ["2025-01-07T00:01:00Z"], "open": [1], "high": [2], "low": [0], "close": [1]}))
    assert str(a["time"].iloc[0]) == "2025-01-07 00:01:00+00:00" == str(b["time"].iloc[0]) == str(c["time"].iloc[0])
