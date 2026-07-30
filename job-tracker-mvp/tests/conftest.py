"""Keep test discovery independent from developer runtime settings."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_runtime_root(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Route implicit Windows defaults to pytest-owned storage for every test."""
    root = tmp_path_factory.mktemp("runtime")
    previous_local_app_data = os.environ.get("LOCALAPPDATA")
    previous_data_dir = os.environ.pop("JOBTRACKER_DATA_DIR", None)
    os.environ["LOCALAPPDATA"] = str(root)
    try:
        yield
    finally:
        if previous_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous_local_app_data
        if previous_data_dir is not None:
            os.environ["JOBTRACKER_DATA_DIR"] = previous_data_dir


def pytest_sessionstart(session: pytest.Session) -> None:
    # Collection imports application modules, so isolate environment before it.
    root = Path(session.config._tmp_path_factory.getbasetemp()) / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    os.environ.pop("JOBTRACKER_DATA_DIR", None)
    os.environ["LOCALAPPDATA"] = str(root)
