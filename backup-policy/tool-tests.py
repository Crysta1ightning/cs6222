"""A fixed subset of the installed NumPy array-operation regression tests."""
import argparse
import json
import os
import random
import time
from pathlib import Path

SELECTION = "sort or partition or copy or take or concatenate or clip"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args()
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    random.seed(6222)
    import numpy as np
    import pytest
    np.random.seed(6222)
    test_file = Path(np.__file__).parent / "_core/tests/test_multiarray.py"
    if not test_file.exists():
        raise SystemExit(f"NumPy tests missing: {test_file}")
    summary = {"numpy_version": np.__version__, "test_file": str(test_file),
               "selection": SELECTION, "seed": 6222, "call_seconds": 0.0,
               "passed": 0, "failed": 0, "skipped": 0}

    class Observer:
        def pytest_sessionstart(self, session):
            summary["collection_start_ns"] = time.monotonic_ns()

        def pytest_collection_finish(self, session):
            summary["collection_end_ns"] = time.monotonic_ns()
            summary["selected_tests"] = len(session.items)

        def pytest_runtest_logreport(self, report):
            if report.when == "call":
                summary["call_seconds"] += report.duration
                summary[report.outcome] += 1
            elif report.failed:
                summary["failed"] += 1

    command = [str(test_file), "-q", "--import-mode=importlib", "-m", "not slow",
               "-k", SELECTION]
    if args.collect_only:
        command.append("--collect-only")
    code = pytest.main(command, plugins=[Observer()])
    summary["exit_code"] = int(code)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
