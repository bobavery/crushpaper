#!/usr/bin/env bash
# Reproduce the flip-strategy sweeps quoted in docs/RESULTS.md. ~10-15 min on 4 cores.
set -euo pipefail
cd "$(dirname "$0")/.."
CSV=data/btcusd_1h_bitstamp.csv.gz
J=${JOBS:-4}
for FEES in zero coinbase_intro coinbase_50k; do
  python -m btc_lab sweep --csv $CSV --start 2023-01-01 --fees $FEES --jobs $J --out results/sweep_2023-2026_${FEES}.csv 2> results/sweep_2023-2026_${FEES}.log
  python -m btc_lab sweep --csv $CSV --start 2022-01-01 --end 2023-01-01 --fees $FEES --jobs $J --out results/sweep_2022_${FEES}.csv 2> results/sweep_2022_${FEES}.log
done
# time-stopped variants (72h) and 4h bars
python -m btc_lab sweep --csv $CSV --start 2023-01-01 --fees coinbase_intro --max-bars 72 --jobs $J --out results/sweep_2023-2026_coinbase_intro_t72.csv 2> results/sweep_2023-2026_coinbase_intro_t72.log
python -m btc_lab sweep --csv $CSV --resample 4h --start 2023-01-01 --fees coinbase_intro --n 24 --trends none,1200 --jobs $J --out results/sweep_2023-2026_coinbase_intro_4h.csv 2> results/sweep_2023-2026_coinbase_intro_4h.log
echo done
