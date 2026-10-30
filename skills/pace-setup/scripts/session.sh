#!/usr/bin/env bash
# This runs inside srun --pty; successful setup leaves an initialized shell.
set -euo pipefail
project="$1"
repair="$2"
setup_timeout="$3"
output="$4"
shift 4
mkdir -p "$output"
printf 'Checking environment and NUMA placement; log: %s/setup.log\n' "$output"
if timeout --signal=TERM --kill-after=10s "${setup_timeout}s" \
    bash -l "$(dirname "${BASH_SOURCE[0]}")/bootstrap.sh" "$project" "$repair" "$@" \
    >"$output/setup.log" 2>&1; then
  cat "$output/settings-summary.txt"
else
  result=$?
  rm -f -- "$output/env.sh" "$output/session.bashrc"
  tail -n 60 "$output/setup.log"
  exit "$result"
fi
exec bash --rcfile "$output/session.bashrc" -i
