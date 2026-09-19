# Backtest results: can a $5–10k "flip" pool make 1.5–5% per trade?

All numbers below come from `btc_lab` runs on Bitstamp hourly BTC-USD candles (2017-01-01 →
2026-09-19), reproducible with `scripts/run_sweeps.sh`, `python -m btc_lab theory` and the
snippets in `results/`. Start capital $10,000 per test, all-in per trade. Fees are charged on every
fill; targets are limit orders (maker), entries and stops are market orders (taker + 0.02% slippage).

Fee presets used (percent of notional, maker / taker):

| preset | maker | taker | round trip (market in, limit out, slippage) |
|---|---|---|---|
| `zero` | 0 | 0 | 0.00% (impossible; shown to separate skill from cost) |
| `coinbase_50k` | 0.15 | 0.25 | 0.44% |
| `coinbase_intro` | 0.40 | 0.60 | 1.04% |

(`docs/RESEARCH.md` discusses the actual 2026 fee schedules; change presets with `--maker/--taker`.)

## 1. The theory first: a target is not an edge

For a price with no predictable drift, any exit rule with a bounded holding time has **zero**
expected return before costs (optional stopping). A take-profit only changes the *shape* of the
outcomes: `P(hit +a before −b) = b / (a + b)`. Target 2% / stop 4% wins 67% of the time and loses
twice as much when it loses. After costs every trade has expectation **minus the round-trip fee**.

Monte Carlo of one round trip (GBM, 45% annual volatility, which is what 2023–2026 realised;
`results/theory_notes.txt`):

| target / stop | P(target) | mean hours held | net per trade, zero fees | `coinbase_50k` | `coinbase_intro` |
|---|---|---|---|---|---|
| 1% / 1% | 0.50 | 5 | +0.01% | −0.43% | −1.03% |
| 1.5% / 1.5% | 0.51 | 11 | +0.04% | −0.40% | −1.00% |
| 2% / 2% | 0.50 | 19 | 0.00% | −0.44% | −1.04% |
| 2% / 4% | 0.66 | 38 | −0.04% | −0.48% | −1.08% |
| 3% / 3% | 0.49 | 41 | −0.04% | −0.48% | −1.08% |
| 5% / 5% | 0.49 | 113 | −0.07% | −0.51% | −1.11% |

At 2%/2% a pool that is always in the market makes about 38 round trips a month. At
`coinbase_intro` that is roughly **−40% per month** of expected drag; at `coinbase_50k` about −17%.
A 60%/year bull drift moves P(target) at 2%/2% from 0.500 to only 0.525.

Realised volatility by year (annualised, from hourly closes): 2022 64%, 2023 43%, 2024 53%,
2025 45%, 2026 to date 44%.

## 2. The sweep: 300 flip configurations × 8 settings

Each sweep runs 5 entry rules (`immediate`, `dip` limit 1% below close, `zscore` ≤ −1.5 on 24 bars,
`rsi(14)` ≤ 30, 24-bar `breakout`) × 6 targets (1, 1.5, 2, 3, 4, 5%) × 5 stops (none, 1, 2, 3, 5%) ×
trend filter (none / only above the 200-day average). Indicators are warmed up on 250 days of
earlier data, so trading starts exactly on the start date.

### 2023-01-01 → 2026-09-19 (bull run then a −30% year; buy-and-hold +385%)

| fees | configs that beat hold | positive at all | positive **with any stop-loss** | median total return | median expectancy / trade |
|---|---|---|---|---|---|
| `zero` | 2 / 300 | 176 / 300 | 116 / 240 | +12.7% | +0.045% |
| `coinbase_50k` | 0 / 300 | 60 / 300 | **0 / 240** | −84.6% | −0.400% |
| `coinbase_intro` | 0 / 300 | 48 / 300 | **0 / 240** | −98.7% | −1.007% |
| `coinbase_intro`, 4-hour bars | 0 / 300 | 45 / 300 | 0 / 240 | −88.7% | −0.965% |
| `coinbase_intro`, 72-hour time stop | 0 / 300 | 0 / 300 | 0 / 240 | −99.5% | −1.039% |

Read that middle column carefully. With realistic fees, **every configuration that used a
stop-loss lost money**, and the median configuration lost almost everything. The 48–60 "positive"
configurations all have *no stop*: they buy, wait for +3–5%, sell, and re-buy. That is buy-and-hold
with a fee-paying reset every few weeks; they show 97–98% win rates, 1 trade a month, the same −54%
drawdown as holding, and less profit than holding (best: +226% vs +385% at `coinbase_intro`).

Mean expectancy per trade by target (`coinbase_intro`, all entries/stops/trends):

| target | 1% | 1.5% | 2% | 3% | 4% | 5% |
|---|---|---|---|---|---|---|
| mean net per trade | −0.88% | −0.83% | −0.74% | −0.58% | −0.42% | −0.24% |
| mean win rate | 20% | 67% | 62% | 55% | 50% | 47% |
| mean trades in 3.7 years | 572 | 510 | 471 | 406 | 354 | 314 |

Wider targets lose less **only because they trade less**. None of them turns positive. The
"1.5% vs 2% vs 3–5%" question therefore has a clear answer: the target size changes how fast you
pay fees, not the sign of the edge.

Best result *with* a stop at each fee level (the honest "best case" for a flip with risk control):

| fees | config | total return | trades | win rate | expectancy / trade | max drawdown |
|---|---|---|---|---|---|---|
| `zero` | immediate, tp 5 / sl 5 | +223% | 314 | 55% | +0.50% | −54% |
| `coinbase_50k` | dip, tp 4 / sl 5 | −21% | 320 | 60% | +0.03% | −68% |
| `coinbase_intro` | rsi, tp 5 / sl 5, trend filter | −69% | 96 | 50% | −1.10% | −74% |

Even at zero fees the best stop-protected flip made 223% while holding made 391%, with the same
drawdown.

Entry rule averages at zero fees (skill without cost): `immediate` +127%, `dip` +55%, `breakout`
+51%, `zscore` +45%, `rsi` −9%. In other words the "signal" that made money was *being long during
a bull market*; the more selective the entry, the less of the bull market it captured. Mean
reversion (`rsi`, `zscore`) did not add value over just being in.

### 2022-01-01 → 2022-12-31 (bear year; buy-and-hold −64%)

| fees | positive configs | median total return | median expectancy / trade |
|---|---|---|---|
| `zero` | 0 / 300 | −4.8% | −0.29% |
| `coinbase_50k` | 0 / 300 | −28.6% | −0.74% |
| `coinbase_intro` | 0 / 300 | −30.0% | −1.38% |

Every configuration lost money in 2022, before fees. Most lost less than holding, which is what
sitting in cash half the time does in a bear market. The 200-day trend filter never allowed a
single entry in 2022 (price was below the average the whole year) and so was the "best" strategy:
it did nothing.

## 3. Grid bots (`results/baselines.txt`)

Spot grids with 5–10 levels 1–3% apart, 2023–2026 hourly:

| fees | grid | total return | hold | trades | win rate | fees paid / start capital | max drawdown |
|---|---|---|---|---|---|---|---|
| `zero` | 1% × 5 | +275% | +391% | 831 | 99.4% | 0% | −52% |
| `coinbase_intro` | 1% × 5 | **−1%** | +385% | 831 | 99.4% | 164% | −52% |
| `coinbase_intro` | 2% × 5 | +64% | +385% | 364 | 98.6% | 93% | −50% |
| `coinbase_intro` | 3% × 4 | +96% | +385% | 195 | 97.9% | 70% | −49% |
| `coinbase_intro`, 2022 | 1% × 5 | −64% | −65% | 41 | 87.8% | 6% | −66% |

A 99% win rate and a −52% drawdown in the same row is the whole story of grid bots: they harvest
small wins until the trend goes against them, then they are fully invested at the top.

## 4. The only thing that "worked": not trading in downtrends

Daily 200-day moving-average trend rule (long above, cash below, 2% band, `coinbase_intro`),
2017-06 → 2026-09: +387% vs +3,387% for holding, 18 trades, −63% max drawdown, but **0%** in 2022
vs −65% for holding. Trend rules lose to holding in bull markets and protect in bear markets; they
are a *portfolio* decision for the $70k position, not a flip strategy for the $5–10k pool.

## 5. What this means for the plan

1. Do not put real money into a fixed-target flip. The backtest, the theory and (see
   `docs/RESEARCH.md`) the academic evidence agree.
2. If you want to see it for yourself, run the paper accounts for 8 weeks
   (`README.md`, "Paper trade on live data"): four targets, same entry, same fees, plus a `hold`
   account. Judge them by expectancy per trade with its confidence interval, not by win rate.
3. The decision that actually matters for your outcome is how the $70k core position is managed
   through regimes. That is what the weekly check-in prompt is for.

## Caveats

* Bitstamp candles, not Coinbase; intra-bar path unknown (we assume the stop hits first when a bar
  contains both the stop and the target); fixed slippage; no taxes.
* 2023–2026 is dominated by a bull run. Flip strategies with no stop look good in it *because* it
  is a bull run. A sideways year with 30% volatility would be worse for every row above.
* Parameter sweeps flatter the best row (selection bias). We report medians and counts for that
  reason, and the best rows are still negative.
