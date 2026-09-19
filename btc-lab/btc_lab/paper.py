"""Stateful paper trading on live public data, using the exact same Engine as the backtests.

State lives in one JSON file per account. Each `step()`:
  1. fetches recent completed candles (Coinbase Exchange public API; Kraken as fallback),
  2. appends bars newer than the last processed one,
  3. runs Engine.step for each new bar (orders from bar i fill at bar i+1 open, as in backtests),
  4. saves state and prints a short status line.

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


class PaperAccount:
    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self.cfg: dict = {}
        self.engine: Optional[Engine] = None
        self.bars: pd.DataFrame = pd.DataFrame(columns=D.COLUMNS)

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def create(cls, path, strategy: str, params: dict, fees: str | dict = "coinbase_intro",
               initial_cash: float = 10_000.0, granularity: int = 3600, source: str = "coinbase",
               max_positions: int = 1, stake_fraction: float = 1.0, history_days: float = 45.0,
               name: str = "paper") -> "PaperAccount":
        acct = cls(path)
        fee = FEE_PRESETS[fees] if isinstance(fees, str) else FeeModel(**fees)
        acct.cfg = {
            "name": name, "created": dt.datetime.now(dt.timezone.utc).isoformat(),
            "strategy": strategy, "params": params, "fees": fee.__dict__, "initial_cash": initial_cash,
            "granularity": granularity, "source": source, "max_positions": max_positions,
            "stake_fraction": stake_fraction, "history_days": history_days,
        }
        acct._build_engine()
        # enough history for the strategy's indicators (e.g. a 4800-bar trend filter needs 200 days)
        need_days = (getattr(acct.engine.strategy, "warmup", 1) * granularity) / 86400.0 + 3.0
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
        # restore strategy anchor for grid
        if hasattr(acct.engine.strategy, "anchor"):
            acct.engine.strategy.anchor = d.get("strategy_state", {}).get("anchor")
        return acct

    def _build_engine(self):
        strat = make(self.cfg["strategy"], **self.cfg["params"])
        self.engine = Engine(strat, FeeModel(**self.cfg["fees"]), self.cfg["initial_cash"],
                             stake_fraction=self.cfg["stake_fraction"], max_positions=self.cfg["max_positions"])

    def save(self):
        d = {
            "config": self.cfg,
            "engine": self.engine.to_dict(),
            "bars": [[t.strftime("%Y-%m-%dT%H:%M:%SZ"), o, h, l, c, v] for t, o, h, l, c, v in
                     self.bars[D.COLUMNS].itertuples(index=False)],
            "strategy_state": {"anchor": getattr(self.engine.strategy, "anchor", None)},
            "saved": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1, default=float))
        tmp.replace(self.path)

    # ---------------------------------------------------------------- stepping
    def fetch(self) -> pd.DataFrame:
        g = self.cfg["granularity"]
        days = self.cfg["history_days"] if self.bars.empty else max(3.0, 2 * g * 400 / 86400)
        try:
            new = D.fetch_history(days=days, granularity=g, source=self.cfg["source"])
        except Exception as e:  # noqa: BLE001
            alt = "kraken" if self.cfg["source"] == "coinbase" else "coinbase"
            print(f"[paper] {self.cfg['source']} failed ({e}); trying {alt}")
            new = D.fetch_history(days=days, granularity=g, source=alt)
        return new

    def step(self, bars: Optional[pd.DataFrame] = None, verbose: bool = True) -> dict:
        """Ingest new bars (fetched unless given) and run the engine on each new one."""
        new = bars if bars is not None else self.fetch()
        merged = D.normalise(pd.concat([self.bars, new], ignore_index=True)) if len(self.bars) else new
        n_before = len(self.bars)
        self.bars = merged
        start = self.engine.state.last_bar + 1
        # a fresh account needs warm-up history: process everything but only "act" once warm
        n_new = 0
        for i in range(start, len(self.bars)):
            self.engine.step(self.bars, i)
            n_new += 1
        self.save()
        st = self.engine.state
        last_close = float(self.bars["close"].iloc[-1]) if len(self.bars) else float("nan")
        status = {
            "time": str(self.bars["time"].iloc[-1]) if len(self.bars) else None,
            "bars_total": len(self.bars), "bars_new": n_new, "bars_before": n_before, "last_close": last_close,
            "cash": round(st.cash, 2), "positions": len(st.positions), "pending": len(st.pending),
            "equity": round(self.engine.equity(last_close), 2), "trades": len(st.trades),
            "fees_paid": round(st.fees_paid, 2),
        }
        if verbose:
            print("[paper]", json.dumps(status))
        return status

    # ---------------------------------------------------------------- reporting
    def report(self) -> dict:
        st = self.engine.state
        summary = summarize(st, self.bars, self.cfg["initial_cash"],
                            bars_per_year=int(365 * 86400 / self.cfg["granularity"]))
        summary["open_positions"] = [
            {"id": p.id, "tag": p.tag, "entry_time": str(p.entry_time), "entry_price": round(p.entry_price, 2),
             "qty": round(p.qty, 6), "tp": round(p.tp_price, 2) if p.tp_price else None,
             "sl": round(p.sl_price, 2) if p.sl_price else None,
             "unrealised_pct": round(100 * (float(self.bars['close'].iloc[-1]) * p.qty - p.cost) / p.cost, 3)}
            for p in st.positions]
        summary["pending_orders"] = [{"side": o.side, "kind": o.kind, "limit": o.limit_price, "tag": o.tag}
                                     for o, _ in st.pending]
        summary["last_trades"] = [
            {"exit_time": str(t.exit_time), "tag": t.tag, "entry": round(t.entry_price, 2),
             "exit": round(t.exit_price, 2), "ret_pct": round(t.ret_pct, 3), "reason": t.exit_reason}
            for t in st.trades[-10:]]
        summary["config"] = self.cfg
        return summary
