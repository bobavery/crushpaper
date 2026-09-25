#!/usr/bin/env bash
# Preflight: can this session fetch web pages?
# Run before any research so a blocked network is discovered in the first minute,
# not after the search budget is spent. Exit 0 = fetch OK, 1 = partial, 2 = blocked.
set -u

hosts=(
  "https://www.etsjets.org/"
  "https://www.thegospelcoalition.org/"
  "https://journal.rts.edu/"
  "https://www.modernreformation.org/"
  "https://scholar.google.com/"
  "https://api.crossref.org/works?query=test&rows=1"
  "https://api.openalex.org/works?search=test&per-page=1"
  "https://www.academia.edu/"
  "https://brill.com/"
  "https://onlinelibrary.wiley.com/"
  "https://archive.org/"
  "https://en.wikipedia.org/"
)

ok=0; blocked=0
printf "%-62s %s\n" "host" "result"
for u in "${hosts[@]}"; do
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 "$u" 2>/dev/null || echo 000)
  case "$code" in
    2*|3*|401|403|404|429) res="reachable ($code)"; ok=$((ok+1)) ;;
    *)                      res="BLOCKED ($code)";   blocked=$((blocked+1)) ;;
  esac
  printf "%-62s %s\n" "$u" "$res"
done

if [[ -n "${HTTPS_PROXY:-}" ]]; then
  status=$(curl -sS --max-time 10 "$HTTPS_PROXY/__agentproxy/status" 2>/dev/null || true)
  if [[ -n "$status" ]] && command -v jq >/dev/null 2>&1; then
    echo
    echo "Proxy relay failures (most recent):"
    echo "$status" | jq -r '(.recentRelayFailures // [])[-5:][] | "  \(.host): \(.detail)"' 2>/dev/null || true
  fi
fi

echo
if (( ok == 0 )); then
  cat <<'EOF'
VERDICT: FETCH BLOCKED. Page fetches are denied by this environment's network policy.
Web search still works (it runs on Anthropic's side), but no page, PDF, or API can be opened here.

Permanent fix (the user makes it once, and every later session in this environment inherits it):
  cloud environment menu in the session title bar > Edit > Network access > Full access
  (or add the specific domains). Then start a new session.

Until then, work in DEGRADED MODE (SKILL.md): snippet-only evidence, [S]/UNVERIFIED labels,
about 50 searches per agent, and a fetch list at the top of the deliverable.
EOF
  exit 2
elif (( blocked > ok )); then
  echo "VERDICT: PARTIAL. Some hosts are blocked; note which, and add anything unopenable to the fetch list."
  exit 1
else
  echo "VERDICT: FETCH OK."
  exit 0
fi
