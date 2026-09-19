"""btc_lab: a small, honest research + paper-trading lab for short-horizon Bitcoin strategies.

Modules
-------
data        fetch/load BTC-USD candles (Coinbase Exchange / Kraken public APIs, local CSVs)
indicators  simple indicator helpers (SMA/EMA/RSI/ATR/Bollinger/z-score/realized vol)
strategies  entry/exit rules that emit Orders (flip, grid, trend baselines, buy-and-hold)
engine      the ONE fill/fee/position engine shared by backtests and paper trading
metrics     performance statistics from equity curves and trade logs
theory      barrier-hitting math: what a fixed take-profit target can and cannot do
paper       stateful paper-trading loop on live public data
cli         command line entry points (python -m btc_lab ...)
"""
__version__ = "0.1.0"
