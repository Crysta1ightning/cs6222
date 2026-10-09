"""Pytest lifecycle timestamps; markers are for observation, not scheduling."""
import json
import os
import time

import pytest


def mark(kind, **extra):
    with open(os.environ["TEST_TIMELINE_FILE"], "a") as handle:
        handle.write(json.dumps({"time_ns": time.monotonic_ns(), "kind": kind, **extra}) + "\n")


def pytest_sessionstart(session):
    mark("collection_start")


def pytest_collection_finish(session):
    mark("collection_end", tests=len(session.items))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    mark("test_start", test=item.nodeid)
    yield
    mark("test_end", test=item.nodeid)


def pytest_runtest_logreport(report):
    mark("test_report", test=report.nodeid, phase=report.when, outcome=report.outcome)
