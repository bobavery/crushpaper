#!/usr/bin/env bash
# Create the recommended paper-trading family: same entry rule, four targets, plus buy-and-hold.
# Usage: scripts/paper_setup.sh [fees_preset] [cash]      e.g. scripts/paper_setup.sh coinbase_intro 10000
set -euo pipefail
cd "$(dirname "$0")/.."
FEES=${1:-coinbase_intro}; CASH=${2:-10000}
mkdir -p paper
for tp in 1.5 2 3 5; do
  python -m btc_lab paper create --state "paper/zscore_tp${tp}.json" --strategy flip --entry zscore --n 24 --z-k -1.5 \
      --tp "$tp" --sl "$tp" --fees "$FEES" --cash "$CASH" --no-step
done
python -m btc_lab paper create --state paper/dip_tp2.json --strategy flip --entry dip --dip 1 --tp 2 --sl 2 --fees "$FEES" --cash "$CASH" --no-step
python -m btc_lab paper create --state paper/hold.json --strategy hold --fees "$FEES" --cash "$CASH" --no-step
cat > paper/step_all.sh <<'EOS'
#!/usr/bin/env bash
# Run from cron every 10 minutes:  */10 * * * * /path/to/btc-lab/paper/step_all.sh >> /path/to/btc-lab/paper/step.log 2>&1
cd "$(dirname "$0")/.."
for s in paper/*.json; do python -m btc_lab paper step --state "$s" || echo "step failed: $s"; done
EOS
chmod +x paper/step_all.sh
echo "created 6 accounts in paper/. Next: paper/step_all.sh (schedule it), then: python -m btc_lab paper compare --states paper/*.json"
