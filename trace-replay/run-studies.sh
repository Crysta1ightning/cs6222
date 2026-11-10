#!/usr/bin/env bash
# Run both complete profiles sequentially in one verified allocation.
set -euo pipefail
: "${SLURM_JOB_ID:?Enter the prepared GPU allocation}"
: "${EXP_ENV_FILE:?Run the PACE setup helper first}"
source "$EXP_ENV_FILE"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAN_DIR="${1:?Usage: run-studies.sh PLAN_DIRECTORY OUTPUT_DIRECTORY}"
OUTPUT_DIR="${2:?Usage: run-studies.sh PLAN_DIRECTORY OUTPUT_DIRECTORY}"
mkdir -p "$OUTPUT_DIR"
for profile in baseline token4x; do
  printf '%s Starting/resuming %s in job %s\n' "$(date -Is)" "$profile" "$SLURM_JOB_ID" | tee -a "$OUTPUT_DIR/study-driver.log"
  python -u "$SCRIPT_DIR/study.py" run \
    --plan "$PLAN_DIR/$profile.json" --profile "$profile" \
    --stream-worker "$SCRIPT_DIR/../memory-experiment/stream-worker" \
    --output-dir "$OUTPUT_DIR/$profile" --trials 3 --resume \
    2>&1 | tee -a "$OUTPUT_DIR/study-driver.log"
  printf '%s Completed %s\n' "$(date -Is)" "$profile" | tee -a "$OUTPUT_DIR/study-driver.log"
done
