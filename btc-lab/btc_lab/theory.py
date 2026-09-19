"""What a fixed take-profit target can and cannot do: barrier-hitting mathematics.

For a price that is a martingale (no predictable drift), any exit rule with bounded holding
time has ZERO expected profit before costs (optional stopping theorem). Choosing +2% target /
-4% stop just trades a high win rate for a bad loss when it comes. After fees the expectation is
minus the round-trip cost, every trade. Profit therefore has to come from (a) drift you are exposed
to while long, or (b) genuine short-horizon predictability. This module quantifies both.
"""
from __future__ import annotations

import math

import numpy as np

HOURS_PER_YEAR = 24 * 365


def p_up_first(a: float, b: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """P(log price hits +a before -b) for Brownian motion with drift mu and vol sigma (same time unit)."""
    if a <= 0 or b <= 0:
        raise ValueError("barriers must be positive")
    if abs(mu) < 1e-12:
        return b / (a + b)
    k = 2.0 * mu / sigma ** 2
    return (1.0 - math.exp(k * b)) / (math.exp(-k * a) - math.exp(k * b))


def expected_hit_time(a: float, b: float, sigma: float) -> float:
    """Driftless expected time (in the unit of sigma) to hit either barrier: a*b/sigma^2."""
    return a * b / sigma ** 2


def simulate_flip(tp_pct: float, sl_pct: float | None, fee_target_pct: float, annual_vol: float = 0.55,
                  annual_drift: float = 0.0, max_hours: float | None = None, n_paths: int = 20000,
                  seed: int = 0, step_minutes: int = 5, fee_stop_pct: float | None = None) -> dict:
    """Monte Carlo of ONE round trip: buy now, exit at +tp, -sl, or time stop. GBM, no predictability.

    fee_target_pct is the round-trip cost when the exit is the resting limit target (market entry +
    limit exit); fee_stop_pct (default: same) is the cost when the exit is a stop or time stop
    (market entry + market exit, slippage both ways), matching the engine's fill model.
    Returns hit probabilities, mean net return per trade (percent), and mean holding hours.
    """
    fee_stop_pct = fee_target_pct if fee_stop_pct is None else fee_stop_pct
    rng = np.random.default_rng(seed)
    dt_years = step_minutes / 60.0 / HOURS_PER_YEAR
    sig = annual_vol * math.sqrt(dt_years)
    mu = (annual_drift - 0.5 * annual_vol ** 2) * dt_years
    horizon_steps = int((max_hours or 24 * 30) * 60 / step_minutes)
    up = math.log(1 + tp_pct / 100.0)
    dn = -math.log(1 - sl_pct / 100.0) if sl_pct else None
    alive = np.ones(n_paths, dtype=bool)
    x = np.zeros(n_paths)
    exit_ret = np.zeros(n_paths)
    exit_step = np.full(n_paths, horizon_steps)
    reason = np.zeros(n_paths, dtype=np.int8)  # 1 tp, 2 sl, 3 time
    chunk = 2000
    for s0 in range(0, horizon_steps, chunk):
        n = min(chunk, horizon_steps - s0)
        idx = np.where(alive)[0]
        if len(idx) == 0:
            break
        z = rng.normal(mu, sig, size=(len(idx), n))
        paths = x[idx, None] + np.cumsum(z, axis=1)
        hit_up = paths >= up
        hit_dn = (paths <= -dn) if dn is not None else np.zeros_like(hit_up)
        any_hit = hit_up | hit_dn
        first = np.where(any_hit.any(axis=1), any_hit.argmax(axis=1), -1)
        for j, (pi, fi) in enumerate(zip(idx, first)):
            if fi >= 0:
                if hit_dn[j, fi]:
                    exit_ret[pi] = -dn; reason[pi] = 2
                else:
                    exit_ret[pi] = up; reason[pi] = 1
                exit_step[pi] = s0 + fi + 1
                alive[pi] = False
            else:
                x[pi] = paths[j, -1]
    # time-stopped paths exit at the current log price
    still = alive
    exit_ret[still] = x[still]; reason[still] = 3
    fees = np.where(reason == 1, fee_target_pct, fee_stop_pct)
    net = (np.exp(exit_ret) - 1.0) * 100.0 - fees
    hours = exit_step * step_minutes / 60.0
    return {
        "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hours": max_hours,
        "fee_target_pct": fee_target_pct, "fee_stop_pct": fee_stop_pct,
        "annual_vol": annual_vol, "annual_drift": annual_drift,
        "p_target": float((reason == 1).mean()), "p_stop": float((reason == 2).mean()), "p_time": float((reason == 3).mean()),
        "mean_net_ret_pct": float(net.mean()), "median_net_ret_pct": float(np.median(net)),
        "mean_hours": float(hours.mean()), "median_hours": float(np.median(hours)),
        "trades_per_month_if_always_in": float(24 * 30 / hours.mean()),
        "monthly_expectancy_pct_if_always_in": float(net.mean() * 24 * 30 / hours.mean()),
    }


def target_table(fee_target_pct: float, annual_vol: float = 0.55, annual_drift: float = 0.0,
                 targets=(1.0, 1.5, 2.0, 3.0, 4.0, 5.0), stop_mult=(1.0, 2.0), max_hours=(None, 72),
                 n_paths: int = 10000, fee_stop_pct: float | None = None) -> list[dict]:
    rows = []
    for tp in targets:
        for m in stop_mult:
            for mh in max_hours:
                rows.append(simulate_flip(tp, tp * m, fee_target_pct, annual_vol, annual_drift, mh, n_paths,
                                          fee_stop_pct=fee_stop_pct))
    return rows


def realized_annual_vol(close, bars_per_year: int = HOURS_PER_YEAR) -> float:
    r = np.diff(np.log(np.asarray(close, dtype=float)))
    return float(r.std(ddof=0) * math.sqrt(bars_per_year))
