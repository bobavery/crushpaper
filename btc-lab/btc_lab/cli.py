"""Command line interface.

  python -m btc_lab backtest --csv data/btcusd_1h_bitstamp.csv.gz --strategy flip --tp 2 --sl 2 --entry zscore
  python -m btc_lab sweep    --csv data/btcusd_1h_bitstamp.csv.gz --start 2023-01-01 --out results/sweep.csv
  python -m btc_lab theory   --fees coinbase_intro --vol 0.55
  python -m btc_lab paper create --state paper/flip2.json --strategy flip --tp 2 --sl 2 --entry zscore
  python -m btc_lab paper step   --state paper/flip2.json
  python -m btc_lab paper report --state paper/flip2.json
  python -m btc_lab price
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import sys
import time

import pandas as pd

from . import data as D
from .engine import Engine, FeeModel, FEE_PRESETS, trades_frame
from .metrics import summarize, by_period
from .strategies import make
from . import theory as T


def _fees(a) -> FeeModel:
    fee = FEE_PRESETS[a.fees]
    if getattr(a, "maker", None) is not None or getattr(a, "taker", None) is not None or getattr(a, "slippage", None) is not None:
        fee = FeeModel("custom", a.maker if a.maker is not None else fee.maker_pct,
                       a.taker if a.taker is not None else fee.taker_pct,
                       a.slippage if a.slippage is not None else fee.slippage_pct)
    return fee


def _strategy_params(a) -> dict:
    if a.strategy == "flip":
        return {k: getattr(a, k) for k in ["tp_pct", "sl_pct", "max_bars", "entry", "n", "z_k", "rsi_n", "rsi_k",
                                            "dip_pct", "ttl", "trend_n", "cooldown", "vol_n", "vol_max"]}
    if a.strategy == "grid":
        return {"step_pct": a.step_pct, "levels": a.levels, "tp_pct": a.tp_pct, "sl_pct": a.sl_pct}
    if a.strategy == "trend":
        return {"n": a.n, "band_pct": a.band_pct}
    return {}


def add_strategy_args(p):
    p.add_argument("--strategy", default="flip", choices=["flip", "grid", "trend", "hold"])
    p.add_argument("--tp", dest="tp_pct", type=float, default=2.0)
    p.add_argument("--sl", dest="sl_pct", type=float, default=None)
    p.add_argument("--max-bars", dest="max_bars", type=int, default=None)
    p.add_argument("--entry", default="immediate", choices=["immediate", "dip", "zscore", "rsi", "breakout"])
    p.add_argument("--n", type=int, default=24)
    p.add_argument("--z-k", dest="z_k", type=float, default=-1.5)
    p.add_argument("--rsi-n", dest="rsi_n", type=int, default=14)
    p.add_argument("--rsi-k", dest="rsi_k", type=float, default=30.0)
    p.add_argument("--dip", dest="dip_pct", type=float, default=1.0)
    p.add_argument("--ttl", type=int, default=6)
    p.add_argument("--trend-n", dest="trend_n", type=int, default=None)
    p.add_argument("--cooldown", type=int, default=0)
    p.add_argument("--vol-n", dest="vol_n", type=int, default=None)
    p.add_argument("--vol-max", dest="vol_max", type=float, default=None)
    p.add_argument("--step", dest="step_pct", type=float, default=1.0)
    p.add_argument("--levels", type=int, default=5)
    p.add_argument("--band", dest="band_pct", type=float, default=0.0)
    p.add_argument("--fees", default="coinbase_intro", choices=sorted(FEE_PRESETS))
    p.add_argument("--maker", type=float, default=None); p.add_argument("--taker", type=float, default=None)
    p.add_argument("--slippage", type=float, default=None)
    p.add_argument("--cash", type=float, default=10_000.0)
    p.add_argument("--stake", dest="stake_fraction", type=float, default=1.0)
    p.add_argument("--max-positions", dest="max_positions", type=int, default=None)


def load_prices(a) -> pd.DataFrame:
    """Load candles. When --start is given, `warmup_days` of earlier data are kept so indicators
    (e.g. a 200-day trend filter) are valid from the first trading bar; trading begins at --start."""
    if a.csv:
        df = D.load_csv(a.csv, rule=a.resample)
    else:
        df = D.fetch_history(days=a.days, granularity=a.granularity, source=a.source)
    warm = getattr(a, "warmup_days", 0) or 0
    load_start = (pd.Timestamp(a.start, tz="UTC") - pd.Timedelta(days=warm)).isoformat() if a.start else None
    return D.slice_range(df, load_start, a.end)


def run_backtest(df: pd.DataFrame, strategy_name: str, params: dict, fee: FeeModel, cash: float,
                 stake_fraction: float = 1.0, max_positions: int | None = None, bars_per_year: int | None = None,
                 trade_start: str | None = None):
    """Run a strategy over df. Bars before `trade_start` are used for indicators only."""
    strat = make(strategy_name, **params)
    mp = max_positions or (params.get("levels", 1) if strategy_name == "grid" else 1)
    eng = Engine(strat, fee, cash, stake_fraction=stake_fraction, max_positions=mp)
    i0 = 0
    if trade_start:
        hits = df.index[df["time"] >= pd.Timestamp(trade_start, tz="UTC")]
        i0 = int(hits[0]) if len(hits) else len(df)
    eng.run(df, start=i0)
    eng.liquidate(df)
    bpy = bars_per_year or _bars_per_year(df)
    traded = df.iloc[i0:].reset_index(drop=True) if i0 else df
    summary = summarize(eng.state, traded, cash, bars_per_year=bpy, fee_rt_pct_for_hold=fee.taker_pct * 2)
    return eng, summary


def _bars_per_year(df) -> int:
    if len(df) < 3:
        return 24 * 365
    step = float(df["time"].diff().dt.total_seconds().median())
    return int(round(365 * 86400 / step))


def cmd_backtest(a):
    df = load_prices(a)
    fee = _fees(a)
    params = _strategy_params(a)
    eng, summary = run_backtest(df, a.strategy, params, fee, a.cash, a.stake_fraction, a.max_positions,
                                trade_start=a.start)
    print(json.dumps({"strategy": a.strategy, "params": params, "fees": fee.__dict__}, indent=1))
    print(json.dumps(summary, indent=1))
    print(by_period(eng.state, D.slice_range(df, a.start), a.period).to_string())
    if a.trades_out:
        trades_frame(eng.state).to_csv(a.trades_out, index=False)
        print("trades ->", a.trades_out)


def _sweep_one(job):
    df, name, params, fee, cash, trade_start = job
    _, s = run_backtest(df, name, params, fee, cash, trade_start=trade_start)
    return s


def cmd_sweep(a):
    df = load_prices(a)
    fee = _fees(a)
    rows = []
    targets = [float(x) for x in a.targets.split(",")]
    stops = [None if x in ("none", "") else float(x) for x in a.stops.split(",")]
    entries = a.entries.split(",")
    trend = [None if x in ("none", "") else int(x) for x in a.trends.split(",")]
    combos = list(itertools.product(entries, targets, stops, trend))
    print(f"sweep: {len(combos)} combinations; data {df['time'].iloc[0]} -> {df['time'].iloc[-1]} ({len(df)} bars), trading from {a.start or 'first bar'}", file=sys.stderr)
    jobs = []
    for entry, tp, sl, tr in combos:
        params = {"tp_pct": tp, "sl_pct": sl, "max_bars": a.max_bars, "entry": entry, "n": a.n, "z_k": a.z_k,
                  "rsi_n": a.rsi_n, "rsi_k": a.rsi_k, "dip_pct": a.dip_pct, "ttl": a.ttl, "trend_n": tr,
                  "cooldown": a.cooldown, "vol_n": a.vol_n, "vol_max": a.vol_max}
        jobs.append((df, "flip", params, fee, a.cash, a.start))
    if a.jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            results = list(ex.map(_sweep_one, jobs, chunksize=4))
    else:
        results = [_sweep_one(j) for j in jobs]
    for (entry, tp, sl, tr), s in zip(combos, results):
        rows.append({"entry": entry, "tp": tp, "sl": sl, "trend_n": tr, "max_bars": a.max_bars, "fees": fee.name, **s})
        print(f"{entry:9s} tp={tp:<4} sl={str(sl):<5} trend={str(tr):<5} ret={s['total_return_pct']:8.2f}% hold={s['buy_hold_return_pct']:8.2f}% "
              f"trades={s['n_trades']:5d} win={s.get('win_rate_pct')} exp/trade={s.get('expectancy_pct_per_trade')} mdd={s['max_drawdown_pct']}", file=sys.stderr)
    out = pd.DataFrame(rows)
    if a.out:
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(a.out, index=False)
        print("->", a.out, file=sys.stderr)
    else:
        print(out.to_string())


def cmd_theory(a):
    fee = _fees(a)
    fee_rt = fee.maker_pct + fee.taker_pct + 2 * fee.slippage_pct  # limit exit, market entry
    print(f"fees preset {fee.name}: round trip (market in, limit out, slippage) = {fee_rt:.3f}%")
    print(f"annual vol {a.vol:.0%}, annual drift {a.drift:.0%}\n")
    rows = T.target_table(fee_rt, a.vol, a.drift, n_paths=a.paths)
    df = pd.DataFrame(rows)[["tp_pct", "sl_pct", "max_hours", "p_target", "p_stop", "p_time", "mean_net_ret_pct",
                             "mean_hours", "trades_per_month_if_always_in", "monthly_expectancy_pct_if_always_in"]]
    print(df.round(3).to_string(index=False))
    print("\nclosed form, driftless: P(+a before -b) = b/(a+b); E[hours] = a*b/sigma_h^2")
    sig_h = a.vol / (24 * 365) ** 0.5
    for tp in (1.0, 1.5, 2.0, 3.0, 5.0):
        a_, b_ = tp / 100, tp / 100
        print(f"  tp=sl={tp}%  P(target)={T.p_up_first(a_, b_):.3f}  E[hours]={T.expected_hit_time(a_, b_, sig_h):.1f}")


def cmd_price(a):
    px, t = D.spot_price(a.source)
    print(json.dumps({"source": a.source, "price": px, "time": t}))


def cmd_paper(a):
    from .paper import PaperAccount
    if a.paper_cmd == "create":
        params = _strategy_params(a)
        fee = _fees(a)
        mp = a.max_positions or (params.get("levels", 1) if a.strategy == "grid" else 1)
        acct = PaperAccount.create(a.state, a.strategy, params, fee.__dict__, a.cash, a.granularity, a.source,
                                   mp, a.stake_fraction, a.history_days, name=pathlib.Path(a.state).stem)
        print("created", a.state); print(json.dumps(acct.cfg, indent=1))
        if not a.no_step:
            acct.step()
    elif a.paper_cmd == "step":
        acct = PaperAccount.load(a.state)
        acct.step()
    elif a.paper_cmd == "run":
        acct = PaperAccount.load(a.state)
        while True:
            try:
                acct.step()
            except Exception as e:  # noqa: BLE001
                print("[paper] step failed:", e)
            time.sleep(a.interval)
    elif a.paper_cmd == "report":
        acct = PaperAccount.load(a.state)
        print(json.dumps(acct.report(), indent=1, default=str))
    elif a.paper_cmd == "trades":
        acct = PaperAccount.load(a.state)
        print(trades_frame(acct.engine.state).to_string())
    elif a.paper_cmd == "compare":
        rows = []
        for path in a.states:
            acct = PaperAccount.load(path)
            r = acct.report()
            rows.append({"account": pathlib.Path(path).stem, "strategy": acct.cfg["strategy"],
                         "tp": acct.cfg["params"].get("tp_pct"), "sl": acct.cfg["params"].get("sl_pct"),
                         "entry": acct.cfg["params"].get("entry"), "fees": acct.cfg["fees"]["name"],
                         **{k: r.get(k) for k in ["start", "end", "total_return_pct", "buy_hold_return_pct", "n_trades",
                                                 "win_rate_pct", "expectancy_pct_per_trade", "expectancy_ci95_pct",
                                                 "max_drawdown_pct", "fees_pct_of_initial", "exposure_pct"]}})
        print(pd.DataFrame(rows).to_string(index=False))


def build_parser():
    p = argparse.ArgumentParser(prog="btc_lab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_data_args(q):
        q.add_argument("--csv", default=None, help="local candle CSV (.csv or .csv.gz)")
        q.add_argument("--resample", default=None, help="e.g. 4h or 1D to aggregate the CSV")
        q.add_argument("--days", type=float, default=30); q.add_argument("--granularity", type=int, default=3600)
        q.add_argument("--source", default="coinbase", choices=["coinbase", "kraken"])
        q.add_argument("--start", default=None); q.add_argument("--end", default=None)
        q.add_argument("--warmup-days", dest="warmup_days", type=float, default=250,
                       help="extra history loaded before --start for indicators (no trading)")

    b = sub.add_parser("backtest"); add_data_args(b); add_strategy_args(b)
    b.add_argument("--period", default="YS"); b.add_argument("--trades-out", default=None)
    b.set_defaults(fn=cmd_backtest)

    s = sub.add_parser("sweep"); add_data_args(s); add_strategy_args(s)
    s.add_argument("--targets", default="1,1.5,2,3,4,5"); s.add_argument("--stops", default="none,1,2,3,5")
    s.add_argument("--entries", default="immediate,dip,zscore,rsi,breakout"); s.add_argument("--trends", default="none,4800")
    s.add_argument("--out", default=None); s.add_argument("--jobs", type=int, default=1)
    s.set_defaults(fn=cmd_sweep)

    t = sub.add_parser("theory"); add_strategy_args(t)
    t.add_argument("--vol", type=float, default=0.55); t.add_argument("--drift", type=float, default=0.0)
    t.add_argument("--paths", type=int, default=10000)
    t.set_defaults(fn=cmd_theory)

    pr = sub.add_parser("price"); pr.add_argument("--source", default="coinbase", choices=["coinbase", "kraken"])
    pr.set_defaults(fn=cmd_price)

    pp = sub.add_parser("paper"); psub = pp.add_subparsers(dest="paper_cmd", required=True)
    for name in ["create", "step", "run", "report", "trades", "compare"]:
        q = psub.add_parser(name)
        if name == "compare":
            q.add_argument("--states", nargs="+", required=True); continue
        q.add_argument("--state", required=True)
        if name == "create":
            add_strategy_args(q); q.add_argument("--granularity", type=int, default=3600)
            q.add_argument("--source", default="coinbase", choices=["coinbase", "kraken"])
            q.add_argument("--history-days", dest="history_days", type=float, default=45.0)
            q.add_argument("--no-step", dest="no_step", action="store_true")
        if name == "run":
            q.add_argument("--interval", type=int, default=300)
    pp.set_defaults(fn=cmd_paper)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
