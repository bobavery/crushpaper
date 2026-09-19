import json
import pandas as pd
import pytest
from btc_lab.paper import PaperAccount
from btc_lab.data import synthetic
from btc_lab.engine import Engine, FEE_PRESETS
from btc_lab.strategies import Flip

PARAMS = {"tp_pct": 1.5, "sl_pct": 1.5, "entry": "zscore", "n": 24}


def backtest_from(df, start, params=PARAMS, cash=5000):
    eng = Engine(Flip(**params), FEE_PRESETS["coinbase_intro"], cash)
    eng.run(df, start=start)
    return [(t.entry_price, t.exit_price, t.exit_reason) for t in eng.state.trades]


def paper_trades(acct):
    return [(t.entry_price, t.exit_price, t.exit_reason) for t in acct.engine.state.trades]


def test_paper_offline_step_matches_backtest_from_live_bar(tmp_path):
    df = synthetic(500, seed=7)
    acct = PaperAccount.create(tmp_path / "a.json", "flip", PARAMS, "coinbase_intro", 5000, 3600, "coinbase")
    acct.step(df.iloc[:200], verbose=False)           # creation: acts from bar 199 (last closed bar) onwards
    assert acct.cfg["trade_from"] == str(df["time"].iloc[199])
    acct = PaperAccount.load(tmp_path / "a.json")
    acct.step(df.iloc[150:400], verbose=False)        # overlapping chunk: identical bars ignored, no warning
    acct = PaperAccount.load(tmp_path / "a.json")
    st = acct.step(df.iloc[380:], verbose=False)
    assert st["bars_total"] == 500 and st["data_warning"] is None and st["gap_bars"] == 0
    assert paper_trades(acct) == backtest_from(df, 199) and len(paper_trades(acct)) > 0
    rep = acct.report()
    assert rep["n_trades"] == len(paper_trades(acct)) and rep["live_since"] == acct.cfg["trade_from"]
    assert rep["start"] == str(df["time"].iloc[199])   # statistics cover the live window only
    assert json.loads((tmp_path / "a.json").read_text())["config"]["strategy"] == "flip"


def test_no_trades_before_going_live(tmp_path):
    df = synthetic(300, seed=2)
    acct = PaperAccount.create(tmp_path / "b.json", "flip", {"tp_pct": 1.0, "entry": "immediate"}, "zero", 1000)
    acct.step(df, verbose=False)
    assert acct.engine.state.trades == [] and len(acct.engine.state.positions) == 0
    assert len(acct.engine.state.pending) == 1       # decided on the live bar, fills on the next bar
    hold = PaperAccount.create(tmp_path / "h.json", "hold", {}, "zero", 1000)
    hold.step(df, verbose=False)
    assert hold.engine.state.positions == [] and len(hold.engine.state.pending) == 1


def test_older_bars_arriving_later_are_ignored_not_merged(tmp_path):
    df = synthetic(300, seed=3)
    acct = PaperAccount.create(tmp_path / "c.json", "flip", PARAMS, "coinbase_intro", 5000)
    first = pd.concat([df.iloc[:200], df.iloc[230:260]], ignore_index=True)  # hole 200-229 in the first fetch
    acct.step(first, verbose=False)
    acct = PaperAccount.load(tmp_path / "c.json")
    st = acct.step(df.iloc[195:300], verbose=False)   # the hole's bars now arrive, too late
    assert st["ignored_old_bars"] == 30
    times = [t for t, _ in acct.engine.state.equity_curve]
    assert len(times) == len(set(times)) and times == sorted(times)
    assert all(t.exit_time >= t.entry_time for t in acct.engine.state.trades)
    seen = pd.concat([first, df.iloc[260:300]], ignore_index=True)
    assert paper_trades(acct) == backtest_from(seen, 229)  # trade_from = last bar of the first fetch (bar 259 of df)
    # the reference: the account went live at the last bar of `first` (index 229 in `seen`)


def test_gap_is_reported_and_pending_orders_cancelled(tmp_path):
    df = synthetic(400, seed=4)
    acct = PaperAccount.create(tmp_path / "d.json", "flip", {"tp_pct": 1.0, "entry": "immediate"}, "zero", 1000)
    acct.step(df.iloc[:200], verbose=False)
    assert len(acct.engine.state.pending) == 1
    acct = PaperAccount.load(tmp_path / "d.json")
    st = acct.step(df.iloc[300:], verbose=False)
    assert st["gap_bars"] == 100
    assert acct.events[-1]["kind"] == "gap" and acct.events[-1]["pending_cancelled"] == 1
    # the order pending across the hole was cancelled; the strategy re-enters on the first post-gap bar
    st_ = acct.engine.state
    first_entry = st_.trades[0].entry_time if st_.trades else st_.positions[0].entry_time
    assert first_entry == df["time"].iloc[301]


def test_history_is_trimmed_and_indices_rebased(tmp_path):
    df = synthetic(1500, seed=5)
    acct = PaperAccount.create(tmp_path / "e.json", "flip", PARAMS, "coinbase_intro", 5000)
    acct.step(df.iloc[:100], verbose=False)
    for k in range(100, 1500, 250):
        acct = PaperAccount.load(tmp_path / "e.json")
        acct.step(df.iloc[k - 5:k + 250], verbose=False)
    assert len(acct.bars) < 1500
    assert acct.engine.state.last_bar == len(acct.bars) - 1
    assert acct.cfg["last_time"] == str(acct.bars["time"].iloc[-1])
    assert paper_trades(acct) == backtest_from(df, 99)


def test_short_initial_history_fails_loudly(tmp_path):
    df = synthetic(20, seed=1)
    acct = PaperAccount.create(tmp_path / "f.json", "flip", PARAMS, "coinbase_intro", 5000)
    with pytest.raises(RuntimeError):
        acct.step(df, verbose=False)


def test_differing_overlap_warns_but_does_not_rewrite(tmp_path):
    df = synthetic(300, seed=8)
    acct = PaperAccount.create(tmp_path / "g.json", "flip", PARAMS, "coinbase_intro", 5000)
    acct.step(df.iloc[:200], verbose=False)
    stored = acct.bars["close"].iloc[150:200].to_numpy().copy()
    revised = df.iloc[150:300].copy()
    revised.loc[revised.index[:50], "close"] *= 1.01
    acct = PaperAccount.load(tmp_path / "g.json")
    st = acct.step(revised, verbose=False)
    assert st["data_warning"] and "50 stored bars differ" in st["data_warning"]
    assert (acct.bars["close"].iloc[150:200].to_numpy() == stored).all()


def test_invalid_granularity_rejected(tmp_path):
    with pytest.raises(ValueError):
        PaperAccount.create(tmp_path / "x.json", "flip", PARAMS, "zero", 1000, granularity=7200)
