#!/usr/bin/env bash
# Run the non-curated validation set (PRD §13). Usage: scripts/run_validation.sh <out-dir> target...
set -u
OUT=$1; shift
mkdir -p "$OUT/logs"
for t in "$@"; do
  slug=$(echo "$t" | sed -E 's#https?://##; s#github.com/##; s#[^A-Za-z0-9]+#-#g')
  start=$(date +%s)
  uv run pigtail "$t" --no-open --output "$OUT/runs" > "$OUT/logs/$slug.log" 2>&1
  code=$?
  echo "{\"target\": \"$t\", \"exit_code\": $code, \"wall_seconds\": $(( $(date +%s) - start ))}" >> "$OUT/exit-codes.jsonl"
done
