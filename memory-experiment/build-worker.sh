#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
worker_cc=${CC:-gcc}
"$worker_cc" -O3 -fopenmp -DSTREAM_ARRAY_SIZE=67108864 -DNTIMES=5 stream-worker.c -o stream-worker
sha256sum stream.c stream-worker.c stream-worker > build-sha256-worker.txt
"$worker_cc" --version > compiler-version-worker.txt
