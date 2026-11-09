#!/usr/bin/env bash
# Launch in foreground; the caller may background it and save $! normally.
set -euo pipefail
MODE="${1:-normal}"
WRITE_POLICY="${EXP_HICACHE_WRITE_POLICY:-write_through}"
case "$WRITE_POLICY" in write_through|write_through_selective|write_back) ;; *) echo 'Invalid EXP_HICACHE_WRITE_POLICY' >&2; exit 2;; esac
case "$MODE" in normal|defer|skip) ;; *) echo 'Usage: launch-stream-aware.sh normal|defer|skip' >&2; exit 2;; esac
: "${SLURM_JOB_ID:?Run inside an allocated GPU compute shell}"
: "${EXP_SERVER_CPUS:?Set to CPUs assigned to SGLang within your allocation}"
: "${EXP_NUMA_NODE:?Set to the chosen host NUMA node}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
module load python/3.12.5 cuda/13.0.1
source "$PROJECT_DIR/.venv/bin/activate"
python - "$EXP_SERVER_CPUS" <<'PY'
import os, sys
selected = set()
for part in sys.argv[1].split(','):
    ends = part.split('-')
    selected.update(range(int(ends[0]), int(ends[-1]) + 1))
if not selected or not selected <= os.sched_getaffinity(0):
    raise SystemExit('EXP_SERVER_CPUS must be a subset of the CPUs allowed in this allocation')
PY
CONTROL_ROOT="${STREAM_CONTROL_ROOT:-${TMPDIR:-/tmp}/stream-aware-${USER}-${SLURM_JOB_ID}}"
mkdir -p "$CONTROL_ROOT"
chmod 700 "$CONTROL_ROOT"
RESULT_DIR="${STREAM_RESULT_ROOT:-$PROJECT_DIR/scripts/results/stream-aware-$SLURM_JOB_ID}/$MODE"
mkdir -p "$RESULT_DIR"
export SGLANG_STREAM_CONTROL_SOCKET="$CONTROL_ROOT/$MODE.sock"
export SGLANG_STREAM_CONTROLLER_MODE="$MODE"
export SGLANG_STREAM_QUEUE_POLICY="${EXP_QUEUE_POLICY:-all}"
export SGLANG_STREAM_CONTROLLER_LOG="${SGLANG_STREAM_CONTROLLER_LOG:-$RESULT_DIR/controller.jsonl}"
export SGLANG_STREAM_MAX_PENDING_TOKENS="${SGLANG_STREAM_MAX_PENDING_TOKENS:-4096}"
export SGLANG_STREAM_MAX_DEFER_SECONDS="${SGLANG_STREAM_MAX_DEFER_SECONDS:-2}"
printf 'Control socket: %s\nController log: %s\n' "$SGLANG_STREAM_CONTROL_SOCKET" "$SGLANG_STREAM_CONTROLLER_LOG"
# STREAM's OpenMP placement must not bind the server's importing/main thread
# to a single core: spawned scheduler/detokenizer workers inherit that affinity.
# numactl still restricts every server worker to EXP_SERVER_CPUS.
export OMP_PROC_BIND=FALSE
unset OMP_PLACES
backend_args=()
if [[ -n "${EXP_ATTENTION_BACKEND:-}" ]]; then
  backend_args+=(--attention-backend "$EXP_ATTENTION_BACKEND")
fi
if [[ -n "${EXP_SAMPLING_BACKEND:-}" ]]; then
  backend_args+=(--sampling-backend "$EXP_SAMPLING_BACKEND")
fi
if [[ -n "${EXP_RANDOM_SEED:-}" ]]; then
  backend_args+=(--random-seed "$EXP_RANDOM_SEED")
fi
exec numactl --physcpubind="$EXP_SERVER_CPUS" --membind="$EXP_NUMA_NODE" \
  sglang serve Qwen/Qwen3-0.6B --host 127.0.0.1 --port 30000 \
  "${backend_args[@]}" \
  --mem-fraction-static 0.30 --max-total-tokens "${EXP_MAX_TOTAL_TOKENS:-8192}" --context-length "${EXP_CONTEXT_LENGTH:-4096}" \
  --enable-metrics --disable-cuda-graph --enable-hierarchical-cache \
  --hicache-size 4 --hicache-write-policy "$WRITE_POLICY"
