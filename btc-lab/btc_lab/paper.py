"""Stateful paper trading on live public data, using the exact same Engine as the backtests.

State lives in one JSON file per account. Each `step()`:
  1. fetches completed candles since the last stored bar (Coinbase Exchange public API; Kraken as
     fallback for incremental fetches),
  2. appends only bars NEWER than the last processed bar (older or overlapping bars are never
     merged into the processed history; if their values differ from what was stored, a data
     warning is reported),
  3. detects holes in the candle stream and reports them (pending orders created before a hole are
     cancelled; open positions are flagged, because a resting order may have filled during the hole),
  4. runs Engine.step for each new bar: bars before the account went live (`trade_from`) only warm
     the indicators; from `trade_from` on, orders decided on bar i fill at bar i+1 open, as in backtests,
  5. trims the stored history to what the strategy needs (re-basing bar indices), and saves.

Run it from cron / Task Scheduler every 5-15 minutes; it only acts when a new bar has closed, so
running it more often than the bar size is harmless. Nothing here can place a real order.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Optional

import pandas as pd

from . import data as D
from .engine import Engine, FeeModel, FEE_PRESETS
from .metrics import summarize
from .strategies import make

KEEP_MIN_BARS = 400      # history kept beyond the strategy's warm-up
TRIM_SLACK = 200         # trim only when the frame exceeds keep + slack (avoid trimming every step)
MAX_BACKFILL_DAYS = 300  # longest incremental fetch


class PaperAccount:
    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self.cfg: dict = {}
        self.engine: Optional[Engine] = None
        self.bars: pd.DataFrame = pd.DataFrame(columns=D.COLUMNS)
        self.events: list = []

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def create(cls, path, strategy: str, params: dict, fees: str | dict = "coinbase_intro",
               initial_cash: float = 10_000.0, granularity: int = 3600, source: str = "coinbase",
               max_positions: int = 1, stake_fraction: float = 1.0, history_days: float = 45.0,
               name: str = "paper") -> "PaperAccount":
        if granularity not in D.LIVE_GRANULARITIES:
            raise ValueError(f"granularity must be one of {D.LIVE_GRANULARITIES} seconds (Coinbase and Kraken both serve it)")
        acct = cls(path)
        fee = FEE_PRESETS[fees] if isinstance(fees, str) else FeeModel(**fees)
        acct.cfg = {
            "name": name, "created": dt.datetime.now(dt.timezone.utc).isoformat(),
            "strategy": strategy, "params": params, "fees": fee.__dict__, "initial_cash": initial_cash,
            "granularity": granularity, "source": source, "max_positions": max_positions,
            "stake_fraction": stake_fraction, "history_days": history_days,
            "trade_from": None,   # time of the last closed bar at creation; the account acts from there on
            "last_time": None,    # time of the last processed bar (consistency check on load)
        }
        acct._build_engine()
        need_days = (acct.warmup * granularity) / 86400.0 + 3.0
        acct.cfg["history_days"] = max(float(history_days), need_days)
        acct.save()
        return acct

    @classmethod
    def load(cls, path) -> "PaperAccount":
        acct = cls(path)
        d = json.loads(pathlib.Path(path).read_text())
        acct.cfg = d["config"]
        acct._build_engine()
        acct.engine.load_dict(d["engine"])
        acct.bars = D.normalise(pd.DataFrame(d["bars"], columns=D.COLUMNS)) if d.get("bars") else pd.DataFrame(columns=D.COLUMNS)
        acct.events = d.get("events", [])
        if hasattr(acct.engine.strategy, "anchor"):
            acct.engine.strategy.anchor = d.get("strategy_state", {}).get("anchor")
        lb = acct.engine.state.last_bar
        if lb >= 0:
            if lb >= len(acct.bars):
                raise RuntimeError(f"{path}: engine last_bar {lb} beyond stored bars {len(acct.bars)}; state is corrupt")
            stored = str(acct.bars["time"].iloc[lb])
            if acct.cfg.get("last_time") and stored != acct.cfg["last_time"]:
                raise RuntimeError(f"{path}: stored last_time {acct.cfg['last_time']} != bars[{lb}] {stored}; refusing to run")
        return acct

    def _build_engine(self):
        strat = make(self.cfg["strategy"], **self.cfg["params"])
        self.engine = Engine(strat, FeeModel(**self.cfg["fees"]), self.cfg["initial_cash"],
                             stake_fraction=self.cfg["stake_fraction"], max_positions=self.cfg["max_positions"])

    @property
    def warmup(self) -> int:
        return int(getattr(self.engine.strategy, "warmup", 1))

    def save(self):
        d = {
            "config": self.cfg,
            "engine": self.engine.to_dict(),
            "bars": [[t.strftime("%Y-%m-%dT%H:%M:%SZ"), o, h, l, c, v] for t, o, h, l, c, v in
                     self.bars[D.COLUMNS].itertuples(index=False)],
            "strategy_state": {"anchor": getattr(self.engine.strategy, "anchor", None)},
            "events": self.events[-200:],
            "saved": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, separators=(",", ":"), default=float))
        tmp.replace(self.path)

    # ---------------------------------------------------------------- data
    def fetch(self) -> pd.DataFrame:
        g = self.cfg["granularity"]
        if self.bars.empty:
            # initial history: no fallback, so a crippled account (too little history) fails loudly
            return D.fetch_history(days=self.cfg["history_days"], granularity=g, source=self.cfg["source"])
        last = self.bars["time"].iloc[-1].to_pydatetime()
        days = min(MAX_BACKFILL_DAYS, (dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 86400.0 + 0.5)
        days = max(days, 3 * g / 86400.0)
        try:
            return D.fetch_history(days=days, granularity=g, source=self.cfg["source"])
        except Exception as e:  # noqa: BLE001
            alt = "kraken" if self.cfg["source"] == "coinbase" else "coinbase"
            self._event("fetch_fallback", f"{self.cfg['source']} failed ({e}); trying {alt}")
            return D.fetch_history(days=days, granularity=g, source=alt)

    def _event(self, kind: str, text: str, **extra):
        ev = {"time": dt.datetime.now(dt.timezone.utc).isoformat(), "kind": kind, "text": text, **extra}
        self.events.append(ev)
        print("[paper]", kind, text)

    # ---------------------------------------------------------------- stepping
    def step(self, bars: Optional[pd.DataFrame] = None, verbose: bool = True) -> dict:
        """Ingest new bars (fetched unless given) and run the engine on each new one."""
        incoming = D.normalise(bars if bars is not None else self.fetch())
        g = self.cfg["granularity"]
        st = self.engine.state
        n_before = len(self.bars)
        status: dict = {"data_warning": None, "gap_bars": 0, "ignored_old_bars": 0}

        if n_before:
            last_t = self.bars["time"].iloc[-1]
            overlap = incoming[incoming["time"] <= last_t]
            new = incoming[incoming["time"] > last_t].reset_index(drop=True)
            if len(overlap):
                # never rewrite processed history; report if the source now disagrees with it
                stored = self.bars.merge(overlap, on="time", suffixes=("", "_new"))
                if len(stored):
                    rel = ((stored[["open", "high", "low", "close"]].to_numpy()
                            - stored[["open_new", "high_new", "low_new", "close_new"]].to_numpy())
                           / stored[["open", "high", "low", "close"]].to_numpy())
                    worst = float(abs(rel).max()) if rel.size else 0.0
                    if worst > 1e-4:
                        status["data_warning"] = f"{int((abs(rel) > 1e-4).any(axis=1).sum())} stored bars differ from the source by up to {100 * worst:.3f}%"
                        self._event("data_warning", status["data_warning"])
                unknown_old = overlap[~overlap["time"].isin(self.bars["time"])]
                if len(unknown_old):
                    status["ignored_old_bars"] = int(len(unknown_old))
                    self._event("ignored_old_bars", f"ignored {len(unknown_old)} bars older than the last processed bar {last_t}")
        else:
            new = incoming.reset_index(drop=True)
            if len(new) <= self.warmup:
                raise RuntimeError(f"initial history too short: {len(new)} bars <= strategy warm-up {self.warmup}; "
                                   f"account not saved. Increase --history-days or check the data source.")
            if self.cfg.get("trade_from") is None:
                self.cfg["trade_from"] = str(new["time"].iloc[-1])   # act from the last closed bar at creation

        if len(new):
            # holes in the stream
            prev_t = self.bars["time"].iloc[-1] if n_before else new["time"].iloc[0]
            times = pd.concat([pd.Series([prev_t]), new["time"]], ignore_index=True)
            dts = times.diff().dt.total_seconds().iloc[1:]
            gaps = dts[dts > 1.5 * g]
            if len(gaps) and n_before:
                missing = int((gaps / g - 1).round().sum())
                status["gap_bars"] = missing
                first_gap_at = str(new["time"].iloc[int(gaps.index[0]) - 1])
                self._event("gap", f"{missing} missing bars in the candle stream before {first_gap_at}",
                            missing=missing, positions_open=len(st.positions), pending_cancelled=len(st.pending))
                st.pending = []   # an order resting through unseen price action cannot be honoured
            self.bars = pd.concat([self.bars, new], ignore_index=True) if n_before else new
            assert self.bars["time"].is_monotonic_increasing

        trade_from = pd.Timestamp(self.cfg["trade_from"])
        n_new = 0
        for i in range(st.last_bar + 1, len(self.bars)):
            self.engine.step(self.bars, i, act=bool(self.bars["time"].iloc[i] >= trade_from))
            n_new += 1
        self._trim()
        self.cfg["last_time"] = str(self.bars["time"].iloc[st.last_bar]) if st.last_bar >= 0 else None
        self.save()

        last_close = float(self.bars["close"].iloc[-1]) if len(self.bars) else float("nan")
        status.update({
            "time": str(self.bars["time"].iloc[-1]) if len(self.bars) else None,
            "live_since": self.cfg["trade_from"], "warm": len(self.bars) > self.warmup,
            "bars_total": len(self.bars), "bars_new": n_new, "bars_before": n_before, "last_close": last_close,
            "cash": round(st.cash, 2), "positions": len(st.positions), "pending": len(st.pending),
            "equity": round(self.engine.equity(last_close), 2), "trades": len(st.trades),
            "fees_paid": round(st.fees_paid, 2),
        })
        if verbose:
            print("[paper]", json.dumps(status))
        return status

    def _trim(self):
        """Keep only the history the strategy needs; re-base every stored bar index."""
        keep = max(self.warmup, KEEP_MIN_BARS) + KEEP_MIN_BARS // 4
        if len(self.bars) <= keep + TRIM_SLACK:
            return
        drop = len(self.bars) - keep
        st = self.engine.state
        st.last_bar -= drop
        for p in st.positions:
            p.entry_bar -= drop
        for t in st.trades:
            t.exit_bar -= drop
        st.pending = [(o, created - drop) for o, created in st.pending]
        self.bars = self.bars.iloc[drop:].reset_index(drop=True)

    # ---------------------------------------------------------------- reporting
    def report(self) -> dict:
        st = self.engine.state
        since = self.cfg.get("trade_from")
        summary = summarize(st, self.bars, self.cfg["initial_cash"],
                            bars_per_year=int(365 * 86400 / self.cfg["granularity"]),
                            fee_rt_pct_for_hold=2 * self.engine.fees.taker_pct, since=since)
        summary["live_since"] = since
        last_close = float(self.bars["close"].iloc[-1]) if len(self.bars) else float("nan")
        summary["open_positions"] = [
            {"id": p.id, "tag": p.tag, "entry_time": str(p.entry_time), "entry_price": round(p.entry_price, 2),
             "qty": round(p.qty, 6), "tp": round(p.tp_price, 2) if p.tp_price else None,
             "sl": round(p.sl_price, 2) if p.sl_price else None,
             "unrealised_pct": round(100 * (last_close * p.qty - p.cost) / p.cost, 3)}
            for p in st.positions]
        summary["pending_orders"] = [{"side": o.side, "kind": o.kind, "limit": o.limit_price, "tag": o.tag}
                                     for o, _ in st.pending]
        summary["last_trades"] = [
            {"exit_time": str(t.exit_time), "tag": t.tag, "entry": round(t.entry_price, 2),
             "exit": round(t.exit_price, 2), "ret_pct": round(t.ret_pct, 3), "reason": t.exit_reason}
            for t in st.trades[-10:]]
        summary["recent_events"] = self.events[-10:]
        summary["config"] = self.cfg
        return summary
