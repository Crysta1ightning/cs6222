#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT="${1:-$HERE/../../../sglang}"
BASE="$(cat "$HERE/base-commit.txt")"
CHECKOUT="$(cd "$CHECKOUT" && pwd)"
git -C "$CHECKOUT" diff --binary "$BASE" -- \
  python/sglang/srt/managers/cache_controller.py \
  python/sglang/srt/mem_cache/unified_radix_cache.py \
  python/sglang/srt/mem_cache/stream_activity.py > "$HERE/stream-aware-backups.patch"
if ! git -C "$CHECKOUT" ls-files --error-unmatch python/sglang/srt/mem_cache/stream_activity.py > /dev/null 2>&1; then
  set +e
  (cd "$CHECKOUT" && git diff --no-index --binary -- /dev/null python/sglang/srt/mem_cache/stream_activity.py) >> "$HERE/stream-aware-backups.patch"
  RESULT=$?
  set -e
  test "$RESULT" -eq 1
fi
(cd "$HERE" && sha256sum stream-aware-backups.patch > stream-aware-backups.sha256)
