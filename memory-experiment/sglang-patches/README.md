# Reproduce the SGLang controller changes

This directory distributes our SGLang changes without pushing to the upstream
`sgl-project/sglang` repository. `base-commit.txt` records the upstream base;
`stream-aware-backups.patch` includes the tool-activity controller and the three
base write policies with dependency-aware deferred queue reclamation.

From the workspace parent (`~/cs6222`):

```bash
bash scripts/memory-experiment/sglang-patches/apply.sh "$PWD/sglang"
```

The helper verifies SHA256, recognizes an already applied patch, and refuses a
different base or dirty checkout. Preserve your existing changes before using
a fresh compatible checkout; the helper does not reset or pull it.

After editing the three controller files in the local SGLang checkout, refresh
the reproducible patch and checksum:

```bash
bash scripts/memory-experiment/sglang-patches/export.sh "$PWD/sglang"
```

CPU tests under `memory-experiment/test_queue_selection.py` verify victim
dependencies, active locks, completed-copy accounting and bounded queue progress.
See [the experiment manual](../../trace-replay/REPRODUCE.md) for the complete
setup, test, measurement and plotting workflow.
