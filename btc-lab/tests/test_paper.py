import json
import pandas as pd
from btc_lab.paper import PaperAccount
from btc_lab.data import synthetic
from btc_lab.engine import Engine
from btc_lab.strategies import Flip


def test_paper_offline_step_matches_backtest(tmp_path):
    df = synthetic(500, seed=7)
    params = {"tp_pct": 1.5, "sl_pct": 1.5, "entry": "zscore", "n": 24}
    acct = PaperAccount.create(tmp_path / "a.json", "flip", params, "coinbase_intro", 5000, 3600, "coinbase")
    # feed bars in three chunks, as a cron job would see them
    acct.step(df.iloc[:200], verbose=False)
    acct = PaperAccount.load(tmp_path / "a.json")
    acct.step(df.iloc[150:400], verbose=False)   # overlapping chunk: duplicates must be ignored
    acct = PaperAccount.load(tmp_path / "a.json")
    st = acct.step(df.iloc[380:], verbose=False)
    assert st["bars_total"] == 500
    from btc_lab.engine import FEE_PRESETS
    eng = Engine(Flip(**params), FEE_PRESETS["coinbase_intro"], 5000)
    eng.run(df)
    a = [(t.entry_price, t.exit_price) for t in acct.engine.state.trades]
    b = [(t.entry_price, t.exit_price) for t in eng.state.trades]
    assert a == b
    rep = acct.report()
    assert rep["n_trades"] == len(b)
    assert json.loads((tmp_path / "a.json").read_text())["config"]["strategy"] == "flip"
