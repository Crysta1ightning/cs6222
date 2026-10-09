#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
gcc -O3 -fopenmp -DSTREAM_ARRAY_SIZE=67108864 -DNTIMES=50 stream.c -o stream
sha256sum stream.c stream > build-sha256.txt
gcc --version > compiler-version.txt
printf 'Built STREAM: three 512 MiB arrays, 1.5 GiB total, 50 iterations.\n'
