"""Candle data: public exchange APIs (no key needed) and local CSV files.

Sources
-------
coinbase  Coinbase Exchange public REST API   https://api.exchange.coinbase.com
          GET /products/BTC-USD/candles?granularity=3600&start=ISO&end=ISO
          -> [[time, low, high, open, close, volume], ...] newest first, max 300 per call
kraken    Kraken public REST API              https://api.kraken.com/0/public
          GET /0/public/OHLC?pair=XBTUSD&interval=60&since=unix
          -> {"result": {"XXBTZUSD": [[time, open, high, low, close, vwap, volume, count], ...]}}
          up to 720 candles per call
csv       any CSV with (time|timestamp|UNIX_TIMESTAMP|DATETIME), open, high, low, close[, volume]

Everything is normalised to a DataFrame with columns
    time (UTC tz-aware Timestamp, bar OPEN time), open, high, low, close, volume
sorted ascending with no duplicate bars.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import time
from typing import Optional

import numpy as np
import pandas as pd

try:  # requests is only needed for live fetching
    import requests
except ImportError:  # pragma: no cover
    requests = None

COINBASE_URL = "https://api.exchange.coinbase.com"
KRAKEN_URL = "https://api.kraken.com/0/public"
COLUMNS = ["time", "open", "high", "low", "close", "volume"]
UA = {"User-Agent": "btc-lab/0.1 (paper-trading research)"}
COINBASE_GRANULARITIES = {60, 300, 900, 3600, 21600, 86400}          # seconds, per Coinbase Exchange docs
KRAKEN_INTERVALS = {60: 1, 300: 5, 900: 15, 1800: 30, 3600: 60, 14400: 240, 86400: 1440, 604800: 10080}
LIVE_GRANULARITIES = sorted(COINBASE_GRANULARITIES & set(KRAKEN_INTERVALS))  # usable with the Kraken fallback
SETTLE_SECONDS = 90  # a bar is treated as closed only this long after its end, so late revisions are not ingested


# ----------------------------------------------------------------------------- normalise
def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce any of the supported layouts into the canonical column set."""
    cols = {c.lower(): c for c in df.columns}
    out = pd.DataFrame()
    if "time" in cols:
        t = df[cols["time"]]
    elif "timestamp" in cols:
        t = df[cols["timestamp"]]
    elif "unix_timestamp" in cols:
        t = df[cols["unix_timestamp"]]
    elif "datetime" in cols:
        t = df[cols["datetime"]]
    elif "date" in cols:
        t = df[cols["date"]]
    else:
        raise ValueError(f"no time column in {list(df.columns)}")
    if pd.api.types.is_datetime64_any_dtype(t):
        out["time"] = pd.to_datetime(t, utc=True)
    elif pd.api.types.is_numeric_dtype(t):
        unit = "ms" if float(t.iloc[-1]) > 1e11 else "s"
        out["time"] = pd.to_datetime(t.astype("int64"), unit=unit, utc=True)
    else:
        out["time"] = pd.to_datetime(t, utc=True)
    for c in ["open", "high", "low", "close"]:
        if c not in cols:
            raise ValueError(f"missing column {c}")
        out[c] = pd.to_numeric(df[cols[c]], errors="coerce")
    out["volume"] = pd.to_numeric(df[cols["volume"]], errors="coerce") if "volume" in cols else 0.0
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out.sort_values("time", kind="stable").drop_duplicates("time", keep="last").reset_index(drop=True)
    return out[COLUMNS]


def resample(df: pd.DataFrame, rule: str = "1h") -> pd.DataFrame:
    """Aggregate finer candles into coarser ones (e.g. 1-minute -> 1h). rule uses pandas offsets."""
    g = df.set_index("time").resample(rule, label="left", closed="left")
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
    }).dropna(subset=["open"]).reset_index()
    return out[COLUMNS]


def slice_range(df: pd.DataFrame, start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    if start:
        df = df[df["time"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["time"] < pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------- csv
def load_csv(path: str | pathlib.Path, rule: Optional[str] = None) -> pd.DataFrame:
    path = pathlib.Path(path)
    df = pd.read_csv(path, compression="gzip" if path.suffix == ".gz" else None)
    df = normalise(df)
    if rule:
        df = resample(df, rule)
    return df


def save_csv(df: pd.DataFrame, path: str | pathlib.Path) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["time"] = out["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(path, index=False, compression="gzip" if path.suffix == ".gz" else None)


# ----------------------------------------------------------------------------- live fetch
def _get(url: str, params: dict, retries: int = 4, timeout: int = 20):
    if requests is None:
        raise RuntimeError("pip install requests")
    last = None
    for k in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"GET {url} failed after {retries} tries: {last}")


def fetch_coinbase_candles(product: str = "BTC-USD", granularity: int = 3600,
                           start: Optional[dt.datetime] = None, end: Optional[dt.datetime] = None,
                           pause: float = 0.15) -> pd.DataFrame:
    """Fetch candles from Coinbase Exchange public API, paginating in 300-candle windows."""
    end = end or dt.datetime.now(dt.timezone.utc)
    start = start or (end - dt.timedelta(seconds=granularity * 300))
    rows = []
    window = dt.timedelta(seconds=granularity * 300)
    cur = start
    while cur < end:
        nxt = min(cur + window, end)
        data = _get(f"{COINBASE_URL}/products/{product}/candles",
                    {"granularity": granularity, "start": cur.isoformat(), "end": nxt.isoformat()})
        for t, low, high, open_, close, vol in data:
            rows.append((t, open_, high, low, close, vol))
        cur = nxt
        time.sleep(pause)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return normalise(df) if len(df) else pd.DataFrame(columns=COLUMNS)


def fetch_kraken_ohlc(pair: str = "XBTUSD", interval: int = 60,
                      since: Optional[dt.datetime] = None) -> pd.DataFrame:
    """Fetch up to 720 candles from Kraken's public OHLC endpoint."""
    params = {"pair": pair, "interval": interval}
    if since is not None:
        params["since"] = int(since.timestamp())
    data = _get(f"{KRAKEN_URL}/OHLC", params)
    if data.get("error"):
        raise RuntimeError(f"kraken error: {data['error']}")
    result = data["result"]
    key = next(k for k in result if k != "last")
    rows = [(r[0], r[1], r[2], r[3], r[4], r[6]) for r in result[key]]
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return normalise(df) if len(df) else pd.DataFrame(columns=COLUMNS)


def spot_price(source: str = "coinbase") -> tuple[float, str]:
    """Latest trade price and the source's timestamp string."""
    if source == "kraken":
        data = _get(f"{KRAKEN_URL}/Ticker", {"pair": "XBTUSD"})
        key = next(k for k in data["result"])
        return float(data["result"][key]["c"][0]), dt.datetime.now(dt.timezone.utc).isoformat()
    data = _get(f"{COINBASE_URL}/products/BTC-USD/ticker", {})
    return float(data["price"]), data.get("time", "")


def fetch_history(days: float = 30, granularity: int = 3600, source: str = "coinbase",
                  end: Optional[dt.datetime] = None, settle_seconds: int = SETTLE_SECONDS) -> pd.DataFrame:
    """Fetch `days` of candles ending now (or at `end`), dropping the still-open bar and any bar that
    closed less than `settle_seconds` ago (exchanges revise the newest candle for a few seconds)."""
    end = end or dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    if source == "coinbase":
        if granularity not in COINBASE_GRANULARITIES:
            raise ValueError(f"coinbase granularity must be one of {sorted(COINBASE_GRANULARITIES)}")
        df = fetch_coinbase_candles("BTC-USD", granularity, start, end)
    elif source == "kraken":
        if granularity not in KRAKEN_INTERVALS:
            raise ValueError(f"kraken has no {granularity}s candles; use one of {sorted(KRAKEN_INTERVALS)}")
        df = fetch_kraken_ohlc("XBTUSD", KRAKEN_INTERVALS[granularity], since=start)
    else:
        raise ValueError(source)
    settled = pd.Timestamp(end) - pd.Timedelta(seconds=settle_seconds)
    cutoff = settled.floor(f"{granularity}s") if granularity < 86400 else settled.floor("D")
    return df[df["time"] < cutoff].reset_index(drop=True)


def update_cache(path: str | pathlib.Path, granularity: int = 3600, source: str = "coinbase",
                 min_days: float = 30) -> pd.DataFrame:
    """Load a cached CSV (if any), fetch what is missing since its last bar, merge, save, return."""
    path = pathlib.Path(path)
    old = load_csv(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
    now = dt.datetime.now(dt.timezone.utc)
    if len(old):
        last = old["time"].iloc[-1].to_pydatetime()
        days = max((now - last).total_seconds() / 86400.0 + 1.0, 1.0)
    else:
        days = min_days
    new = fetch_history(days=days, granularity=granularity, source=source, end=now)
    merged = normalise(pd.concat([old, new], ignore_index=True)) if len(old) else new
    save_csv(merged, path)
    return merged


def synthetic(n_bars: int = 24 * 365, seed: int = 0, annual_vol: float = 0.55, annual_drift: float = 0.0,
              start: str = "2025-01-01", bar_seconds: int = 3600, start_price: float = 80_000.0) -> pd.DataFrame:
    """Geometric-Brownian synthetic candles for tests and for the theory module."""
    rng = np.random.default_rng(seed)
    bars_per_year = 365 * 86400 / bar_seconds
    sig = annual_vol / np.sqrt(bars_per_year)
    mu = annual_drift / bars_per_year - 0.5 * sig ** 2
    steps = 8  # intra-bar sub-steps to build realistic high/low
    r = rng.normal(mu / steps, sig / np.sqrt(steps), size=(n_bars, steps))
    path = start_price * np.exp(np.cumsum(r.reshape(-1)))
    path = path.reshape(n_bars, steps)
    opens = np.concatenate([[start_price], path[:-1, -1]])
    closes = path[:, -1]
    highs = np.maximum(path.max(axis=1), opens)
    lows = np.minimum(path.min(axis=1), opens)
    t = pd.date_range(start, periods=n_bars, freq=f"{bar_seconds}s", tz="UTC")
    return pd.DataFrame({"time": t, "open": opens, "high": highs, "low": lows, "close": closes,
                         "volume": rng.uniform(1, 100, n_bars)})
