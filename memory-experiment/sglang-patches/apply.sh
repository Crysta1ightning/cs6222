#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT="${1:-$HERE/../../../sglang}"
BASE="$(cat "$HERE/base-commit.txt")"
(cd "$HERE" && sha256sum -c stream-aware-backups.sha256)
if git -C "$CHECKOUT" apply --reverse --check "$HERE/stream-aware-backups.patch" 2>/dev/null; then
  echo 'Patch already applied.'
  exit 0
fi
test "$(git -C "$CHECKOUT" rev-parse HEAD)" = "$BASE" || { echo 'Checkout is not at the recorded base commit.' >&2; exit 1; }
git -C "$CHECKOUT" diff --quiet
git -C "$CHECKOUT" diff --cached --quiet
if [ "$(git -C "$CHECKOUT" branch --show-current)" != experiment/stream-aware-backups ]; then
  git -C "$CHECKOUT" switch -c experiment/stream-aware-backups
fi
git -C "$CHECKOUT" apply --check "$HERE/stream-aware-backups.patch"
git -C "$CHECKOUT" apply "$HERE/stream-aware-backups.patch"
