"""Offline tests of the live-fetch code paths (network is mocked)."""
import datetime as dt
import pandas as pd
from btc_lab import data as D


def test_coinbase_pagination_and_ordering(monkeypatch):
    calls = []

    def fake_get(url, params, **kw):
        calls.append(params)
        start = dt.datetime.fromisoformat(params["start"]); end = dt.datetime.fromisoformat(params["end"])
        out = []
        t = start
        while t < end:  # Coinbase returns newest first: [time, low, high, open, close, volume]
            ts = int(t.timestamp())
            out.append([ts, 99.0, 101.0, 100.0, 100.5, 1.0])
            t += dt.timedelta(hours=1)
        return list(reversed(out))

    monkeypatch.setattr(D, "_get", fake_get)
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    end = dt.datetime(2026, 9, 19, 12, tzinfo=dt.timezone.utc)
    start = end - dt.timedelta(hours=700)
    df = D.fetch_coinbase_candles("BTC-USD", 3600, start, end)
    assert len(calls) == 3  # 700 hours in windows of 300
    assert len(df) == 700 and df["time"].is_monotonic_increasing
    assert list(df.columns) == D.COLUMNS
    assert df["open"].iloc[0] == 100.0 and df["low"].iloc[0] == 99.0 and df["high"].iloc[0] == 101.0


def test_fetch_history_drops_forming_bar(monkeypatch):
    now = dt.datetime(2026, 9, 19, 12, 20, tzinfo=dt.timezone.utc)

    def fake_get(url, params, **kw):
        start = dt.datetime.fromisoformat(params["start"]); end = dt.datetime.fromisoformat(params["end"])
        t = start.replace(minute=0, second=0, microsecond=0)
        out = []
        while t <= end:
            out.append([int(t.timestamp()), 1, 2, 1, 1, 1]); t += dt.timedelta(hours=1)
        return out

    monkeypatch.setattr(D, "_get", fake_get)
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    df = D.fetch_history(days=1, granularity=3600, end=now)
    assert df["time"].iloc[-1] == pd.Timestamp("2026-09-19 11:00", tz="UTC")  # 12:00 bar is still forming


def test_kraken_parsing(monkeypatch):
    def fake_get(url, params, **kw):
        return {"error": [], "result": {"XXBTZUSD": [[1758240000, "100", "101", "99", "100.5", "100.2", "5.5", 12],
                                                      [1758243600, "100.5", "102", "100", "101", "101", "3.0", 9]], "last": 1758243600}}
    monkeypatch.setattr(D, "_get", fake_get)
    df = D.fetch_kraken_ohlc()
    assert len(df) == 2 and df["close"].iloc[1] == 101.0 and df["volume"].iloc[0] == 5.5
