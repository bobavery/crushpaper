# btc-lab — an honest Bitcoin short-horizon research and paper-trading lab

`btc-lab` is a small Python toolkit that answers one question with real data instead of hope:
**can a $5,000–$10,000 pool "flip" Bitcoin for 1.5–5% per trade, several times a week, and come out
ahead after fees?** It contains

* a **backtester** with a strict no-look-ahead fill engine (fees, slippage, stops, targets, time stops),
* **strategies**: fixed-target flips with five entry triggers, spot grids, a daily trend baseline, buy-and-hold,
* a **theory** module (barrier-hitting math + Monte Carlo) that shows what a take-profit target can and cannot do,
* a **paper trader** that runs the *same engine* on live public data (Coinbase Exchange, Kraken fallback),
  keeps its state in a JSON file, and is safe to run from cron every few minutes,
* ten years of hourly BTC-USD candles (Bitstamp, 2017 → today) in `data/`, with the script that rebuilds them,
* the **prompts** in `prompts/` (forecast v2, weekly holder check-in, daily desk supervisor) and the
  **research** in `docs/`.

Nothing in this repository can place a real order.

## Bottom line (details in `docs/RESULTS.md` and `docs/RESEARCH.md`)

1. A fixed take-profit target does not create expected profit. For a price with no predictable
   drift, every exit rule has zero expected return before costs and **minus the round-trip fee after
   costs, on every trade** (`python -m btc_lab theory`).
2. On 2023-2026 hourly data, **0 of 300** flip configurations beat buy-and-hold under any real fee
   schedule, and every configuration that used a stop-loss lost money after fees. The only positive
   ones had no stop, which makes them diluted buy-and-hold with the same −54% drawdown.
3. Fees, not targets, decide the outcome. At Coinbase's entry tier a 2% target nets about +1.0%
   when it hits and a 2% stop costs about −3.2% when it hits. You need a 76% hit rate just to break
   even; the realised hit rate is ~50%.
4. A wider target "does better" only because it trades less and holds longer, i.e. it is closer to
   holding. Wanting 3-5% instead of 1.5-2% does not change the sign of the edge, only the fee bill.
5. What *did* help historically: staying out of downtrends (a 200-day moving-average filter kept
   every strategy flat through 2022) and simply holding through uptrends. Neither is "flipping".

The paper trader exists so you can watch this happen on live prices with fictitious money before
deciding anything with real money.

## Install

```bash
cd btc-lab
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                                      # 15 tests, ~1 s
```

Python 3.10+; pandas, numpy, requests.

## Data

`data/btcusd_1h_bitstamp.csv.gz` — hourly candles 2017-01-01 → 2026-09-19, built from Bitstamp's
1-minute data (mirror: github.com/ff137/bitstamp-btcusd-minute-data). Rebuild/refresh with

```bash
python scripts/build_dataset.py            # downloads ~95 MB, writes data/btcusd_1h_bitstamp.csv.gz
```

Live data comes from Coinbase Exchange's public candle endpoint (no key), Kraken as fallback.

## Backtest one idea

```bash
python -m btc_lab backtest --csv data/btcusd_1h_bitstamp.csv.gz --start 2023-01-01 \
    --strategy flip --entry zscore --tp 2 --sl 2 --fees coinbase_intro
```

Options: `--entry immediate|dip|zscore|rsi|breakout`, `--tp`, `--sl`, `--max-bars` (time stop),
`--trend-n 4800` (only buy above the 200-day average on hourly bars), `--fees zero|coinbase_intro|
coinbase_10k|coinbase_50k|kraken_base|kraken_50k` or `--maker/--taker/--slippage` in percent,
`--resample 4h`, `--strategy grid --step 1 --levels 5`, `--strategy trend --n 200 --resample 1D`.
`--start` keeps 250 days of earlier data for indicators (`--warmup-days`), trading begins at `--start`.

## Sweep the whole grid

```bash
python -m btc_lab sweep --csv data/btcusd_1h_bitstamp.csv.gz --start 2023-01-01 \
    --fees coinbase_intro --jobs 4 --out results/my_sweep.csv
scripts/run_sweeps.sh          # the exact sweeps quoted in docs/RESULTS.md (~5 min on 4 cores)
```

## Theory: what a target can do

```bash
python -m btc_lab theory --fees coinbase_intro --vol 0.45
```

prints hit probabilities, expected holding time, and expected net return per trade for
targets 1–5% with stops at 1x and 2x the target, with and without a 72-hour time stop.

## Paper trade on live data

```bash
# one command: six accounts (z-score entry with 1.5/2/3/5% targets, a 1% dip-limit entry, buy-and-hold),
# $10k each, Coinbase entry-tier fees, plus paper/step_all.sh for the scheduler
scripts/paper_setup.sh coinbase_intro 10000

# or by hand
python -m btc_lab paper create --state paper/zscore_tp2.json --strategy flip \
    --entry zscore --tp 2 --sl 2 --fees coinbase_intro --cash 10000
python -m btc_lab paper create --state paper/hold.json --strategy hold --cash 10000

# every 5-15 minutes (cron / launchd / Task Scheduler): only acts when an hourly bar has closed
python -m btc_lab paper step --state paper/zscore_tp2.json

# or keep one process running
python -m btc_lab paper run --state paper/zscore_tp2.json --interval 300

# what happened
python -m btc_lab paper report  --state paper/zscore_tp2.json
python -m btc_lab paper trades  --state paper/zscore_tp2.json
python -m btc_lab paper compare --states paper/*.json
```

Cron line (Linux/macOS), runs every 10 minutes:

```
*/10 * * * * cd /path/to/btc-lab && .venv/bin/python -m btc_lab paper step --state paper/zscore_tp2.json >> paper/step.log 2>&1
```

Fills follow the backtest rules exactly: an order decided at the close of bar *i* fills at the open
of bar *i+1*; targets are resting limit orders (maker fee), stops are stop-markets (taker fee +
slippage); if a bar touches both the stop and the target we assume the stop hit first.

### Go/no-go rule for real money (write it down before you start)

After **at least 8 weeks and 100 closed trades** on paper, an account is a candidate only if

* expectancy per trade after fees is positive **and** the 95% bootstrap interval
  (`expectancy_ci95_pct` in the report) excludes zero,
* max drawdown stayed inside the limit you set in advance (the desk prompt uses 15%),
* it beat the `hold` account over the same window,
* the result holds on both halves of the window.

If any test fails, the answer is no. Do not lower the bar afterwards.

## Layout

```
btc_lab/engine.py      the one fill/fee/position engine (backtest and paper share it)
btc_lab/strategies.py  flip / grid / trend / hold
btc_lab/theory.py      barrier probabilities and Monte Carlo of one round trip
btc_lab/paper.py       stateful live paper trading
btc_lab/cli.py         python -m btc_lab ...
scripts/               dataset builder, sweep runner
results/               sweep CSVs and summaries quoted in the docs
prompts/               01 forecast v2, 02 weekly holder check-in, 03 daily desk supervisor
docs/                  RESULTS.md (backtests), RESEARCH.md (evidence + platforms), CRITIQUE.md (prompt review)
tests/                 engine, strategy, data and paper-trading tests
```

## Limitations you should know

* Backtests use Bitstamp hourly candles; you would trade on Coinbase or Kraken. Prices differ by a
  few dollars, fills inside a bar are unknowable, and slippage is a fixed 0.02%.
* Fee presets are snapshots; check the exchange's fee page and pass `--maker/--taker`.
* Taxes are not modelled. Every profitable flip is a short-term capital gain.
* Past volatility regimes are not future ones. The sweeps cover a bear year (2022) and a bull run
  (2023-2026); they do not cover a flat year with low volatility.
