"""Build btc-lab/data/btcusd_1h_bitstamp.csv.gz from the ff137/bitstamp-btcusd-minute-data mirror.

Usage:  python scripts/build_dataset.py [--bulk PATH.gz] [--updates PATH.csv] [--since 2017-01-01]
Downloads the files from raw.githubusercontent.com if paths are not given.
"""
import argparse, pathlib, sys, urllib.request
import pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from btc_lab.data import normalise, resample, save_csv, slice_range  # noqa: E402

BASE = "https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/main/data/"
BULK = BASE + "historical/btcusd_bitstamp_1min_2012-2025.csv.gz"
UPD = BASE + "updates/btcusd_bitstamp_1min_latest.csv"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk"); ap.add_argument("--updates"); ap.add_argument("--since", default="2017-01-01")
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parents[1] / "data" / "btcusd_1h_bitstamp.csv.gz"))
    a = ap.parse_args()
    bulk = a.bulk or "btcusd_bitstamp_1min_2012-2025.csv.gz"
    upd = a.updates or "btcusd_bitstamp_1min_latest.csv"
    if not a.bulk and not pathlib.Path(bulk).exists():
        print("downloading", BULK); urllib.request.urlretrieve(BULK, bulk)
    if not a.updates and not pathlib.Path(upd).exists():
        print("downloading", UPD); urllib.request.urlretrieve(UPD, upd)
    frames = []
    for p in (bulk, upd):
        raw = pd.read_csv(p, compression="gzip" if str(p).endswith(".gz") else None)
        raw = raw[raw["timestamp"] >= pd.Timestamp(a.since, tz="UTC").timestamp()]
        df = normalise(raw)
        # drop synthetic zero-volume flat candles (exchange downtime) before aggregating
        df = df[~((df["volume"] == 0) & (df["high"] == df["low"]))]
        frames.append(resample(df, "1h"))
        print(p, len(raw), "minute rows ->", len(frames[-1]), "hourly bars")
    hourly = normalise(pd.concat(frames, ignore_index=True))
    hourly = slice_range(hourly, a.since)
    save_csv(hourly, a.out)
    print("wrote", a.out, len(hourly), "bars", hourly["time"].iloc[0], "->", hourly["time"].iloc[-1])

if __name__ == "__main__":
    main()
