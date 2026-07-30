from __future__ import annotations

import os
from pathlib import Path

from jobtracker.paths import AppPaths


def test_implicit_runtime_path_uses_pytest_owned_local_app_data() -> None:
    assert "JOBTRACKER_DATA_DIR" not in os.environ
    assert AppPaths.resolve().data_dir == Path(os.environ["LOCALAPPDATA"]) / "jobtracker"
