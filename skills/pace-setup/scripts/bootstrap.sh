#!/usr/bin/env bash
# Run only as the setup Slurm step, before the compute-side Python helper.
set -euo pipefail
: "${SLURM_JOB_ID:?Run via the setup helper in a Slurm step}"
project="$1"
repair="$2"
shift 2
cd "$project"
module load python/3.12.5 cuda/13.0.1
fresh=0
if [[ ! -f sglang/.git && ! -d sglang/.git ]]; then
  if [[ -e sglang ]]; then echo 'sglang exists but is not a Git checkout; inspect before setup' >&2; exit 1; fi
  git clone --branch v0.5.21 https://github.com/sgl-project/sglang.git sglang
  if [[ -f scripts/memory-experiment/sglang-patches/apply.sh ]]; then
    bash scripts/memory-experiment/sglang-patches/apply.sh "$project/sglang"
  fi
  fresh=1
fi
git -C sglang merge-base --is-ancestor e00930c5489053f26d86b179cee0d087f846acbb HEAD || {
  echo 'SGLang is not based on the recorded commit; checkout was preserved' >&2; exit 1;
}
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  fresh=1
fi
source .venv/bin/activate
if [[ "$fresh" == 1 || "$repair" == 1 ]]; then
  python -m pip install --no-cache-dir --upgrade pip
  SGLANG_BUILD_RUST_EXTS=none python -m pip install --no-cache-dir -e ./sglang/python \
    'torch==2.13.0+cu130' --extra-index-url https://download.pytorch.org/whl/cu130
  python -m pip install --no-cache-dir numpy requests psutil pytest
fi
python -m pip check
exec "$@"
