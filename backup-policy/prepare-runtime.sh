#!/bin/bash
# Create one sequential-read archive of the installed runtime and its headers.
# Run on the login node before submitting; do not rebuild during an active job.
set -euo pipefail
cd /home/hice1/twu474/cs6222
site_dir="$PWD/.venv/lib/python3.12/site-packages"
mkdir -p .runtime-archives
packages=(flashinfer tvm_ffi)
for metadata in "$site_dir"/flashinfer_python-*.dist-info "$site_dir"/apache_tvm_ffi-*.dist-info; do
    if [[ -d "$metadata" ]]; then packages+=("${metadata##*/}"); fi
done
tar --dereference --exclude='*/__pycache__' -C "$site_dir" \
    -cf .runtime-archives/flashinfer-tvm-runtime.tar "${packages[@]}"
sha256sum .runtime-archives/flashinfer-tvm-runtime.tar > .runtime-archives/flashinfer-tvm-runtime.sha256
