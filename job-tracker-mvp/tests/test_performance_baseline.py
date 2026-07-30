from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).parents[1]


def load_measurement_module():
    path = PROJECT / "scripts" / "measure_performance.py"
    spec = importlib.util.spec_from_file_location("measure_performance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PerformanceBaselineTests(unittest.TestCase):
    def test_measurement_uses_disposable_data_not_runtime_environment(self) -> None:
        module = load_measurement_module()
        with TemporaryDirectory() as temp:
            live = Path(temp) / "live"
            live.mkdir()
            sentinel = live / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with patch.dict(os.environ, {"JOBTRACKER_DATA_DIR": str(live)}):
                payload = module.run_baseline((3,), runs=1)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertEqual(list(live.iterdir()), [sentinel])

        self.assertTrue(payload["temporary_data_only"])
        result = payload["results"][0]
        self.assertEqual(result["records"], 3)
        self.assertEqual(result["list_select_queries"], 4)
        self.assertEqual(result["detail_select_queries"], 4)


if __name__ == "__main__":
    unittest.main()
