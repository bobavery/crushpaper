"""Score logged forecasts (from prompts/01_forecast_v2.md) with Brier scores against realised prices.

Log format: one JSON object per line in forecasts.jsonl, e.g.
{"date":"2026-09-19","time_utc":"14:00","price":81000,"q1_p_up_48h":0.52,"q2_p_up_7d":0.55,
 "q3":{"up4":0.30,"flat":0.40,"down4":0.30},"prompt_version":"v2"}

Resolution uses hourly closes from data/btcusd_1h_bitstamp.csv.gz (rebuild it with
scripts/build_dataset.py) or, with --live, Coinbase candles fetched now.

Usage: python scripts/score_forecasts.py forecasts.jsonl [--live]
"""
import argparse, json, pathlib, sys
import numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from btc_lab import data as D  # noqa: E402


def price_at(df, t):
    """Close of the last bar that opened at or before t (None if not yet available)."""
    sub = df[df["time"] <= t]
    if sub.empty or (t - sub["time"].iloc[-1]) > pd.Timedelta(hours=2):
        return None
    return float(sub["close"].iloc[-1])


def brier_binary(p, outcome):
    return (p - outcome) ** 2


def brier_multi(probs, outcome_idx):
    o = np.zeros(len(probs)); o[outcome_idx] = 1
    return float(((np.array(probs) - o) ** 2).sum())


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("log"); ap.add_argument("--live", action="store_true")
    ap.add_argument("--csv", default=str(pathlib.Path(__file__).resolve().parents[1] / "data" / "btcusd_1h_bitstamp.csv.gz"))
    a = ap.parse_args()
    df = D.fetch_history(days=30) if a.live else D.load_csv(a.csv)
    rows = []
    for line in open(a.log):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = json.loads(line)
        t0 = pd.Timestamp(f["date"] + "T" + f.get("time_utc", "00:00") + ":00Z")
        p0 = float(f["price"])
        r = {"date": f["date"], "price": p0}
        p48 = price_at(df, t0 + pd.Timedelta(hours=48)); p7 = price_at(df, t0 + pd.Timedelta(days=7))
        if p48 is not None and "q1_p_up_48h" in f:
            up = 1.0 if p48 > p0 else 0.0
            r["q1_outcome"] = up; r["q1_brier"] = brier_binary(f["q1_p_up_48h"], up); r["q1_brier_50"] = brier_binary(0.5, up)
        if p7 is not None and "q2_p_up_7d" in f:
            up = 1.0 if p7 > p0 else 0.0
            r["q2_outcome"] = up; r["q2_brier"] = brier_binary(f["q2_p_up_7d"], up); r["q2_brier_50"] = brier_binary(0.5, up)
        if p7 is not None and "q3" in f:
            ret = p7 / p0 - 1
            idx = 0 if ret > 0.04 else (2 if ret < -0.04 else 1)
            q = f["q3"]; probs = [q["up4"], q["flat"], q["down4"]]
            r["q3_ret_pct"] = round(100 * ret, 2); r["q3_outcome"] = ["up4", "flat", "down4"][idx]
            r["q3_brier"] = brier_multi(probs, idx); r["q3_brier_uniform"] = brier_multi([1/3] * 3, idx)
        rows.append(r)
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    for q in ["q1", "q2", "q3"]:
        col = f"{q}_brier"
        if col in out and out[col].notna().any():
            base = out[f"{q}_brier_50"] if q != "q3" else out["q3_brier_uniform"]
            print(f"{q}: n={int(out[col].notna().sum())} mean Brier={out[col].mean():.4f} vs naive={base.mean():.4f} "
                  f"({'better' if out[col].mean() < base.mean() else 'NOT better'} than the naive baseline)")


if __name__ == "__main__":
    main()
