from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PROJECT = Path(__file__).parents[1]


def load_release_manifest_module():
    path = PROJECT / "scripts" / "release_manifest.py"
    spec = importlib.util.spec_from_file_location("release_manifest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseToolingTests(unittest.TestCase):
    def test_manifest_is_reproducible_and_contains_no_local_path(self) -> None:
        module = load_release_manifest_module()
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "JobTracker.exe"
            artifact.write_bytes(b"packaged bytes")

            first = module.build_manifest(artifact)
            second = module.build_manifest(artifact)

        self.assertEqual(first, second)
        self.assertEqual(first["distribution"], "unsigned-development")
        self.assertEqual(first["artifact"]["name"], "JobTracker.exe")
        self.assertEqual(first["artifact"]["size"], 14)
        self.assertEqual(first["artifact"]["sha256"], sha256(b"packaged bytes").hexdigest())
        self.assertNotIn(temp, json.dumps(first))

    def test_release_constraints_are_exact_and_path_free(self) -> None:
        lines = [
            line.strip()
            for line in (PROJECT / "constraints-release.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertTrue(lines)
        self.assertTrue(all(line.count("==") == 1 for line in lines))
        self.assertFalse(any(":\\" in line or "file:" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
