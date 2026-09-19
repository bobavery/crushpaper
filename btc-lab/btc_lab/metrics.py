"""Performance statistics from an EngineState plus the price frame it was run on."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import EngineState, equity_frame, trades_frame

HOURS_PER_YEAR = 24 * 365


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min()) if len(dd) else 0.0


def summarize(state: EngineState, df: pd.DataFrame, initial_cash: float, bars_per_year: int = HOURS_PER_YEAR,
              fee_rt_pct_for_hold: float = 0.0) -> dict:
    eq = equity_frame(state)
    tr = trades_frame(state)
    if eq.empty:
        return {"error": "no bars processed"}
    equity = eq["equity"].astype(float)
    n_bars = len(equity)
    years = n_bars / bars_per_year
    final = float(equity.iloc[-1])
    total_ret = final / initial_cash - 1.0
    cagr = (final / initial_cash) ** (1.0 / years) - 1.0 if years > 0 and final > 0 else float("nan")
    rets = equity.pct_change().dropna()
    vol_ann = float(rets.std(ddof=0) * np.sqrt(bars_per_year)) if len(rets) > 1 else float("nan")
    sharpe = float(rets.mean() / rets.std(ddof=0) * np.sqrt(bars_per_year)) if len(rets) > 1 and rets.std(ddof=0) > 0 else float("nan")
    mdd = max_drawdown(equity)
    first_open = float(df["open"].iloc[0]); last_close = float(df["close"].iloc[-1])
    hold_ret = (last_close / first_open) * (1 - fee_rt_pct_for_hold / 100.0) - 1.0
    out = {
        "bars": n_bars, "years": round(years, 3),
        "start": str(eq["time"].iloc[0]), "end": str(eq["time"].iloc[-1]),
        "final_equity": round(final, 2), "total_return_pct": round(100 * total_ret, 2),
        "cagr_pct": round(100 * cagr, 2) if np.isfinite(cagr) else None,
        "buy_hold_return_pct": round(100 * hold_ret, 2),
        "excess_vs_hold_pct": round(100 * (total_ret - hold_ret), 2),
        "ann_vol_pct": round(100 * vol_ann, 2) if np.isfinite(vol_ann) else None,
        "sharpe": round(sharpe, 2) if np.isfinite(sharpe) else None,
        "max_drawdown_pct": round(100 * mdd, 2),
        "fees_paid": round(float(state.fees_paid), 2), "fees_pct_of_initial": round(100 * float(state.fees_paid) / initial_cash, 2),
        "n_trades": int(len(tr)),
    }
    if len(tr):
        wins = tr[tr["pnl"] > 0]; losses = tr[tr["pnl"] <= 0]
        gross_win = float(wins["pnl"].sum()); gross_loss = float(-losses["pnl"].sum())
        months = max(years * 12.0, 1e-9)
        out.update({
            "win_rate_pct": round(100 * len(wins) / len(tr), 1),
            "avg_win_pct": round(float(wins["ret_pct"].mean()), 3) if len(wins) else None,
            "avg_loss_pct": round(float(losses["ret_pct"].mean()), 3) if len(losses) else None,
            "expectancy_pct_per_trade": round(float(tr["ret_pct"].mean()), 3),
            "median_ret_pct": round(float(tr["ret_pct"].median()), 3),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "avg_bars_held": round(float(tr["bars_held"].mean()), 1),
            "median_bars_held": float(tr["bars_held"].median()),
            "trades_per_month": round(len(tr) / months, 1),
            "exposure_pct": round(100 * state.bars_in_market / n_bars, 1),
            "exit_reasons": tr["exit_reason"].value_counts().to_dict(),
            "worst_trade_pct": round(float(tr["ret_pct"].min()), 2),
            "best_trade_pct": round(float(tr["ret_pct"].max()), 2),
            "expectancy_ci95_pct": bootstrap_ci(tr["ret_pct"].to_numpy(dtype=float)),
        })
    return out


def bootstrap_ci(x: np.ndarray, n_boot: int = 2000, seed: int = 0) -> list:
    """95% bootstrap confidence interval for the mean per-trade return (percent)."""
    if len(x) < 5:
        return [None, None]
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n_boot, len(x)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return [round(float(lo), 3), round(float(hi), 3)]


def by_period(state: EngineState, df: pd.DataFrame, freq: str = "YS") -> pd.DataFrame:
    """Strategy return vs buy-and-hold per calendar period (freq 'YS' years, 'QS' quarters, 'MS' months)."""
    eq = equity_frame(state).set_index("time")["equity"].astype(float)
    px = df.set_index("time")["close"].astype(float)
    e = eq.resample(freq).agg(["first", "last"])
    p = px.resample(freq).agg(["first", "last"])
    out = pd.DataFrame({
        "strategy_pct": 100 * (e["last"] / e["first"] - 1.0),
        "hold_pct": 100 * (p["last"] / p["first"] - 1.0),
    })
    out["excess_pct"] = out["strategy_pct"] - out["hold_pct"]
    tr = trades_frame(state)
    if len(tr):
        tr = tr.copy(); tr["exit_time"] = pd.to_datetime(tr["exit_time"], utc=True)
        out["trades"] = tr.set_index("exit_time")["pnl"].resample(freq).count().reindex(out.index).fillna(0).astype(int)
    return out.round(2)
