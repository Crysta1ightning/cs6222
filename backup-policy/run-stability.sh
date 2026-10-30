#!/bin/bash -l
# Node-local, prepared-worker stability pilot. Submit reproducible.sbatch.
set -euo pipefail
cd /home/hice1/twu474/cs6222
job_id=${SLURM_JOB_ID:?Run inside Slurm}
archive="$PWD/results/stability-${job_id}"
local_run=$(mktemp -d "/tmp/cs6222-stability-${job_id}-XXXXXX")
exec 3>&1
printf 'Node-local work: %s; final archive: %s\n' "$local_run" "$archive" >&3
exec >"$local_run/worker.log" 2>&1
telemetry_pid=''
stage_out() {
    run_status=$?
    trap - EXIT
    if [[ -n "$telemetry_pid" ]]; then
        kill "$telemetry_pid" 2>/dev/null || true
        wait "$telemetry_pid" 2>/dev/null || true
    fi
    mkdir -p "$archive"
    for item in results worker.log manifest.json gpu-telemetry.csv gpu-telemetry-errors.log lscpu.txt toolchain.txt runtime-stage.json; do
        if [[ -e "$local_run/$item" ]] && ! cp -a "$local_run/$item" "$archive/"; then
            printf 'Archive failed; node-local files remain at %s\n' "$local_run" >&3
            exit 1
        fi
    done
    printf 'Experiment exit %s; results archived at %s\n' "$run_status" "$archive" >&3
    rm -rf -- "$local_run"
    exit "$run_status"
}
trap stage_out EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

module load python/3.12.5 cuda/13.0.1
source .venv/bin/activate
# Avoid NFS-hosted GCC C++ headers during runtime CUDA compilation.
unset GCCROOT GCC_ROOT CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export NVCC_CCBIN=/usr/bin/g++
export FLASHINFER_EXTRA_CUDAFLAGS=--compiler-bindir=/usr/bin/g++
export HF_HUB_OFFLINE=1
unset SGLANG_ENABLE_NVTX_SCHEDULER SGLANG_ENABLE_NVTX_OPERATIONS
unset CS6222_TOOL_STATE CS6222_BACKUP_LOG CS6222_BACKUP_MAX_DELAY CS6222_BACKUP_MAX_BYTES
export CS6222_RESULTS_DIR="$local_run/results"
export CS6222_PROGRESS_FD=3
export FLASHINFER_WORKSPACE_BASE="$local_run/runtime"
export TRITON_CACHE_DIR="$local_run/triton"
export TORCH_EXTENSIONS_DIR="$local_run/torch-extensions"
export CUDA_CACHE_PATH="$local_run/cuda-cache"
mkdir -p "$CS6222_RESULTS_DIR"
runtime_archive="$PWD/.runtime-archives/flashinfer-tvm-runtime.tar"
expected_sha=$(cut -d ' ' -f 1 "$PWD/.runtime-archives/flashinfer-tvm-runtime.sha256")
actual_sha=$(sha256sum "$runtime_archive" | cut -d ' ' -f 1)
[[ "$actual_sha" == "$expected_sha" ]]
mkdir -p "$local_run/python"
tar -xf "$runtime_archive" -C "$local_run/python"
export PYTHONPATH="$local_run/python${PYTHONPATH:+:$PYTHONPATH}"
python - <<'PY' >"$local_run/runtime-stage.json"
import hashlib, json, os
from pathlib import Path
import flashinfer, tvm_ffi
import torch
from flashinfer.jit import env as jit_env
from tvm_ffi import libinfo
local = Path(os.environ['PYTHONPATH'].split(':')[0]).resolve()
paths = [Path(module.__file__).resolve() for module in (flashinfer, tvm_ffi)]
paths.extend(Path(path).resolve() for path in (jit_env.FLASHINFER_INCLUDE_DIR,
    libinfo.find_include_path(), libinfo.find_libtvm_ffi()))
assert all(local in path.parents for path in paths), paths
print(json.dumps({'local_runtime_paths': list(map(str, paths)),
                  'gpu': torch.cuda.get_device_name(0), 'capability': torch.cuda.get_device_capability(0),
                  'archive_sha256': hashlib.sha256(Path('.runtime-archives/flashinfer-tvm-runtime.tar').read_bytes()).hexdigest()}))
PY
printf 'Unchanged FlashInfer/FFI packages staged locally.\n' >&3
printf '{"local_root":"%s","archive_root":"%s","protocol":"prepared-loop-v1"}\n' \
    "$local_run" "$archive" >"$local_run/manifest.json"

"$CXX" --version >"$local_run/toolchain.txt"
cat >"$local_run/compiler-check.cu" <<'CU'
#include <vector>
#include <cuda_runtime.h>
__global__ void cs6222_compiler_check() {}
CU
timeout 90 nvcc --compiler-bindir="$CXX" -std=c++17 -c \
    "$local_run/compiler-check.cu" -o "$local_run/compiler-check.o"
printf 'Local host-compiler CUDA check passed.\n' >&3

python backup-policy/apply.py
python -m unittest discover -s backup-policy -p 'test_*.py'
bash memory-experiment/build-worker.sh
if [[ ! -x memory-experiment/stream ]]; then bash memory-experiment/build.sh; fi
if [[ ! -x memory-experiment/stream-5 ]]; then bash memory-experiment/build.sh 5; fi
python -u memory-experiment/check-numa.py
bindings=$(python - <<'PY'
import json, os
from pathlib import Path
root = max(Path(os.environ['CS6222_RESULTS_DIR']).glob('numa-check-*'), key=lambda p: p.stat().st_mtime_ns)
assert json.loads((root/'verification.json').read_text())['placement_check_passed']
config = json.loads((root/'bindings.json').read_text())
print(','.join(map(str,config['server_cpus'])), ','.join(map(str,config['tool_cpus'])), config['gpu_numa_node'], config['tool_threads'])
PY
)
read -r server_cpus tool_cpus numa_node tool_threads <<< "$bindings"
nvidia-smi --query-gpu=timestamp,uuid,clocks.sm,clocks.mem,power.draw,temperature.gpu,utilization.gpu \
    --format=csv --loop=1 >"$local_run/gpu-telemetry.csv" 2>"$local_run/gpu-telemetry-errors.log" &
telemetry_pid=$!
lscpu >"$local_run/lscpu.txt"
port=$(python - <<'PY'
import socket
with socket.socket() as probe:
    probe.bind(('127.0.0.1',0)); print(probe.getsockname()[1])
PY
)
common=(--output-dir "$CS6222_RESULTS_DIR" --port "$port" --followup 0
    --tool stream --stream-binary "$PWD/memory-experiment/stream-5" --tool-threads "$tool_threads"
    --server-cpus "$server_cpus" --tool-cpus "$tool_cpus" --tool-schedule requests
    --prompt-tokens 1024 --output-tokens 16 --gpu-tokens 32768 --warmup-requests 3
    --max-delay 0 --max-bytes 0)

# Logged gate diagnostic is separate from every performance measurement.
numactl --membind="$numa_node" python -u backup-policy/experiment.py "${common[@]}" \
    --verify-only --requests 4 --stream-rounds 1 --stream-at-requests 1

# Four alternating pairs give ABBA ABBA order. Each arm gets a fresh, fully
# warmed server; a runtime policy switch is intentionally not introduced here.
for comparison in repeat-default deferred; do
    numactl --membind="$numa_node" python -u backup-policy/experiment.py "${common[@]}" \
        --comparison "$comparison" --pairs 4 --requests 128 --policy-log-mode off \
        --stream-worker "$PWD/memory-experiment/stream-worker" --require-trigger-overlap --stream-rounds 10 \
        --stream-at-requests 1,13,25,37,49,61,73,85,97,109
done
