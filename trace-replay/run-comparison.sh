#!/usr/bin/env bash
# Run only inside an existing GPU allocation. Own and clean up each server.
set -euo pipefail
set +m
: "${SLURM_JOB_ID:?Enter an allocated GPU compute shell first}"
: "${EXP_SERVER_CPUS:?Set disjoint allowed physical cores for SGLang}"
: "${EXP_TOOL_CPUS:?Set disjoint allowed physical cores for replay/tools}"
: "${EXP_NUMA_NODE:?Choose one GPU-local NUMA node}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$HERE/../.." && pwd)"
PLAN="${1:?Usage: run-comparison.sh PLAN.json [delay|stream]}"
KIND="${2:-delay}"
RUNTIME="${TRACE_TOOL_RUNTIME:-prepared}"
case "$RUNTIME" in prepared|subprocess) ;; *) echo 'TRACE_TOOL_RUNTIME must be prepared or subprocess' >&2; exit 2;; esac
timing_args=()
if [[ "${TRACE_REQUIRE_TIMING_FIDELITY:-0}" == 1 ]]; then
  timing_args+=(--require-timing-fidelity)
fi
case "$KIND" in delay|stream) ;; *) echo 'Tool workload must be delay or stream' >&2; exit 2;; esac
source "$PROJECT/.venv/bin/activate"
control_base="${STREAM_CONTROL_ROOT:-${TMPDIR:-/tmp}/trace-aware-$USER-$SLURM_JOB_ID}"
mkdir -p "$control_base"
# An owned server's forced shutdown can leave its socket inode behind. Use a
# fresh private namespace per comparison instead of reusing or unlinking a
# possibly live scheduler's socket on a subsequent invocation.
export STREAM_CONTROL_ROOT="$(mktemp -d "$control_base/trace-XXXXXXXX")"
export STREAM_RESULT_ROOT="${TRACE_RESULT_ROOT:-$PROJECT/scripts/results/tracelab-$SLURM_JOB_ID-$KIND}"
mkdir -p "$STREAM_RESULT_ROOT"
python - <<'PY'
import os
from pathlib import Path
def cpus(key):
    result=set()
    for part in os.environ[key].split(','):
        ends=part.split('-');result.update(range(int(ends[0]),int(ends[-1])+1))
    return result
server,tool=cpus('EXP_SERVER_CPUS'),cpus('EXP_TOOL_CPUS')
if not server or not tool or server & tool or not server|tool <= os.sched_getaffinity(0):
    raise SystemExit('CPU sets must be nonempty, disjoint, and within the allocation')
node=os.environ['EXP_NUMA_NODE']
cores=set()
for cpu in server|tool:
    root=Path(f'/sys/devices/system/cpu/cpu{cpu}')
    if not (root/f'node{node}').exists():
        raise SystemExit(f'CPU {cpu} is not on NUMA node {node}')
    identity=tuple((root/'topology'/name).read_text().strip() for name in ['physical_package_id','core_id'])
    if identity in cores:
        raise SystemExit('Choose distinct physical cores, not sibling hardware threads')
    cores.add(identity)
PY
if [[ "$KIND" == stream ]]; then
  if [[ "$RUNTIME" == prepared ]]; then
    bash "$PROJECT/scripts/memory-experiment/build-worker.sh"
  else
    bash "$PROJECT/scripts/memory-experiment/build.sh" 5
  fi
fi
server_pid=''
controller_journal=''
controller_archive=''
stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill -TERM -- "-$server_pid" 2>/dev/null || true
    for ((n=0; n<100; n++)); do
      if ! kill -0 -- "-$server_pid" 2>/dev/null; then break; fi
      sleep 0.1
    done
    kill -KILL -- "-$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=''
  fi
  if [[ -n "$controller_journal" && -f "$controller_journal" ]]; then
    cp "$controller_journal" "$controller_archive"
  fi
}
trap stop_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for mode in ${TRACE_MODES:-normal defer skip}; do
  case "$mode" in normal|defer|skip) ;; *) echo "Invalid mode: $mode" >&2; exit 2;; esac
  python - <<'PY'
import errno, socket
try:
    connection=socket.create_connection(('127.0.0.1',30000),timeout=1)
except OSError as exc:
    if exc.errno != errno.ECONNREFUSED:
        raise
else:
    connection.close()
    raise SystemExit('Port 30000 is already occupied. Stop your existing server before this comparison.')
PY
  mkdir -p "$STREAM_RESULT_ROOT/$mode"
  # Keep synchronous scheduler journal writes off the shared home filesystem.
  # Archive after shutdown; replay reads this local journal during each trial.
  controller_journal="$STREAM_CONTROL_ROOT/$mode.jsonl"
  controller_archive="$STREAM_RESULT_ROOT/$mode/controller.jsonl"
  SGLANG_STREAM_CONTROLLER_LOG="$controller_journal" \
    setsid bash "$PROJECT/scripts/memory-experiment/launch-stream-aware.sh" "$mode" \
    >"$STREAM_RESULT_ROOT/$mode/server.log" 2>&1 &
  server_pid=$!
  ready=0
  for ((n=0; n<300; n++)); do
    if ! kill -0 "$server_pid" 2>/dev/null; then break; fi
    if curl --max-time 2 -fsS http://127.0.0.1:30000/health >/dev/null 2>&1; then
      ready=1; break
    fi
    sleep 2
  done
  if [[ "$ready" != 1 ]]; then
    tail -n 60 "$STREAM_RESULT_ROOT/$mode/server.log"
    exit 1
  fi
  # Torch/OpenMP imports must not pin replay to its first tool core. Tool
  # subprocesses set their own OpenMP binding after inheriting this full set.
  OMP_PROC_BIND=FALSE numactl --physcpubind="$EXP_TOOL_CPUS" --membind="$EXP_NUMA_NODE" \
    python "$HERE/replay.py" --plan "$PLAN" --controller-mode "$mode" \
      --control-socket "$STREAM_CONTROL_ROOT/$mode.sock" --tool-workload "$KIND" \
      --tool-runtime "$RUNTIME" \
      --max-seconds "${TRACE_MAX_SECONDS:-600}" "${timing_args[@]}" \
      --threads "${EXP_THREADS:-1}" --trials "${TRACE_TRIALS:-3}" \
      --max-active-sessions "${TRACE_MAX_ACTIVE_SESSIONS:-4}" \
      --max-stream-processes "${TRACE_MAX_STREAM_PROCESSES:-1}" \
      --output-dir "$STREAM_RESULT_ROOT/$mode"
  stop_server
done
python "$HERE/compare.py" "$STREAM_RESULT_ROOT" --output-dir "$STREAM_RESULT_ROOT/plots"
