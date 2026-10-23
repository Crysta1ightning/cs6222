#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
stream_iterations="${1:-50}"
if [[ ! "$stream_iterations" =~ ^[0-9]+$ ]] || (( stream_iterations < 2 )); then
  printf 'Iterations must be an integer >= 2.\n' >&2
  exit 1
fi
stream_binary=stream
stream_suffix=""
if (( $# > 0 )); then
  stream_binary="stream-${stream_iterations}"
  stream_suffix="-${stream_iterations}"
fi
gcc -O3 -fopenmp -DSTREAM_ARRAY_SIZE=67108864 -DNTIMES="$stream_iterations" stream.c -o "$stream_binary"
sha256sum stream.c "$stream_binary" > "build-sha256${stream_suffix}.txt"
gcc --version > "compiler-version${stream_suffix}.txt"
printf 'Built %s: three 512 MiB arrays, 1.5 GiB total, %s iterations.\n' "$stream_binary" "$stream_iterations"
