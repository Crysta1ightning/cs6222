#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
worker_cc=${CC:-gcc}
worker_iterations=${STREAM_NTIMES:-2}
if ! [[ "$worker_iterations" =~ ^[0-9]+$ ]] || ((10#$worker_iterations < 2 || 10#$worker_iterations > 100)); then
  echo 'STREAM_NTIMES must be an integer from 2 through 100' >&2
  exit 2
fi
worker_iterations=$((10#$worker_iterations))
worker_flags=(-O3 -fopenmp -DSTREAM_ARRAY_SIZE=67108864 "-DNTIMES=$worker_iterations")
printf '%q ' "$worker_cc" "${worker_flags[@]}" stream-worker.c -o stream-worker > build-command-worker.txt
printf '\n' >> build-command-worker.txt
"$worker_cc" "${worker_flags[@]}" stream-worker.c -o stream-worker
sha256sum stream.c stream-worker.c stream-worker > build-sha256-worker.txt
"$worker_cc" --version > compiler-version-worker.txt
