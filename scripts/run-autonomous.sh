#!/usr/bin/env bash
# Runs headless Claude Code sessions in a loop until ops/STATE.md says DONE or BLOCKED.
#
# AGENT_BACKEND=subscription (default): uses your Claude plan via Claude Code's own login
#   (/login, or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`). ANTHROPIC_API_KEY is stripped,
#   because it would override the subscription. Do NOT use --bare: it ignores the OAuth token.
# AGENT_BACKEND=api: uses ANTHROPIC_API_KEY (API billing).
#
# Run ONLY inside an isolated VM/container: this skips permission prompts.
# Check the flags against the current Claude Code docs before first use, and confirm billing with /status.
set -euo pipefail
cd "$(dirname "$0")/.."
AGENT_BACKEND="${AGENT_BACKEND:-subscription}"
MAX_SESSIONS="${MAX_SESSIONS:-500}"
PAUSE_SECONDS="${PAUSE_SECONDS:-30}"
LIMIT_SLEEP_SECONDS="${LIMIT_SLEEP_SECONDS:-1800}"   # wait time after a usage-limit hit before retrying
mkdir -p ops/sessions   # session logs are gitignored (they may echo data); summaries go to ops/RUNLOG.md

if [[ "$AGENT_BACKEND" == "subscription" ]]; then
  CLAUDE=(env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
    -u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_VERTEX -u CLAUDE_CODE_USE_FOUNDRY claude)
elif [[ "$AGENT_BACKEND" == "api" ]]; then
  : "${ANTHROPIC_API_KEY:?AGENT_BACKEND=api requires ANTHROPIC_API_KEY}"
  CLAUDE=(claude)
else
  echo "AGENT_BACKEND must be subscription or api"; exit 1
fi
echo "Agent backend: $AGENT_BACKEND"

for i in $(seq 1 "$MAX_SESSIONS"); do
  status="$(grep -m1 '^status:' ops/STATE.md | awk '{print $2}')"
  if [[ "$status" == "DONE" || "$status" == "BLOCKED" ]]; then
    echo "Stopping: status=$status"; break
  fi
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  log="ops/sessions/$ts.log"
  echo "=== Session $i ($ts, $AGENT_BACKEND) ==="
  set +e
  "${CLAUDE[@]}" -p "Run one work session following docs/WORK_ORDER.md §3 exactly. Session $i, started $ts, agent backend $AGENT_BACKEND. Delegate independent tasks to subagents in parallel. Keep the session resumable. End by updating the ops files and committing." \
    --dangerously-skip-permissions 2>&1 | tee "$log"
  set -e
  # Usage-limit handling: pause, then continue. The work is resumable by design.
  # Only the CLI's own final lines are checked, so sessions that merely *discuss* connector
  # rate limits don't trigger a pause.
  if tail -n 5 "$log" | grep -qiE "usage limit reached|you've hit your limit|limit will reset|out of extra usage"; then
    echo "Usage limit detected; sleeping ${LIMIT_SLEEP_SECONDS}s."
    echo "- $(date -u +%FT%TZ) usage limit hit in session $i ($AGENT_BACKEND); paused ${LIMIT_SLEEP_SECONDS}s" >> ops/RUNLOG.md
    sleep "$LIMIT_SLEEP_SECONDS"; continue
  fi
  # WORK_ORDER §3.5: push only after the private-data and secret scans pass.
  if python3 scripts/private_data_scan.py && { ! command -v gitleaks >/dev/null || gitleaks git --log-opts="@{u}..HEAD" --no-banner; }; then
    git push || echo "- $(date -u +%FT%TZ) push failed after session $i" >> ops/RUNLOG.md
  else
    echo "- $(date -u +%FT%TZ) push BLOCKED by private-data/secret scan after session $i" >> ops/RUNLOG.md
    echo "Scan failed; not pushing. Fix before the next session."; exit 1
  fi
  sleep "$PAUSE_SECONDS"
done
