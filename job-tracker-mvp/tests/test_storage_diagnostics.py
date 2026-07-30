from __future__ import annotations

from hashlib import sha256
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from jobtracker import desktop
from jobtracker.models import JobApplication
from jobtracker.paths import WorkspaceResolution
from jobtracker.repositories import build_application_service
from jobtracker.services import load_project_context


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.endswith(("-wal", "-shm"))
    }


class StorageDiagnosticsTests(unittest.TestCase):
    def test_health_is_read_only_and_export_contains_no_content_or_paths(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = load_project_context(data_dir=root, enforce_workspace=True)
            service = build_application_service(context)
            service.create_job(
                JobApplication(
                    company="Private Company",
                    role="Private Role",
                    date_applied="2026-07-30",
                ),
                "Private job description",
                [
                    SimpleNamespace(
                        filename="private-cv.pdf",
                        content=b"private document bytes",
                        category="cv",
                    )
                ],
            )
            bridge = desktop.WorkspaceBridge(
                Mock(),
                WorkspaceResolution("explicit", root),
                Mock(),
            )
            before = snapshot(root)

            healthy = bridge.get_storage_status()

            self.assertEqual(snapshot(root), before)
            self.assertTrue(healthy["read_only"])
            self.assertEqual(healthy["database_integrity"], "ok")
            self.assertEqual(healthy["foreign_key_violations"], 0)
            self.assertEqual(
                healthy["managed_files"],
                {"total": 1, "healthy": 1, "missing": 0, "corrupt": 0},
            )
            self.assertGreater(healthy["workspace_size_bytes"], 0)
            self.assertGreater(healthy["free_space_bytes"], 0)
            self.assertIsNone(healthy["latest_backup_at"])
            self.assertIsNone(healthy["backup_age_days"])

            managed = next((root / "files").rglob("*.pdf"))
            managed.write_bytes(b"changed")
            corrupt = bridge.get_storage_status()
            self.assertEqual(corrupt["managed_files"]["corrupt"], 1)

            result = bridge.export_diagnostics()
            exported = Path(result["path"])
            payload = json.loads(exported.read_text(encoding="utf-8"))
            encoded = json.dumps(payload)

            self.assertEqual(payload["format_version"], 1)
            self.assertEqual(payload["storage"]["managed_files"]["corrupt"], 1)
            self.assertNotIn(str(root), encoded)
            self.assertNotIn("Private Company", encoded)
            self.assertNotIn("Private Role", encoded)
            self.assertNotIn("private-cv.pdf", encoded)
            self.assertNotIn("private document bytes", encoded)

    def test_file_log_rotates_by_size(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            handler = desktop._configure_file_logging(
                root,
                max_bytes=160,
                backup_count=1,
            )
            logger = logging.getLogger("jobtracker.tests")
            try:
                for index in range(4):
                    logger.info("event=synthetic index=%s payload=%s", index, "x" * 120)
                handler.flush()
                self.assertTrue((root / "logs" / "jobtracker.log").is_file())
                self.assertTrue((root / "logs" / "jobtracker.log.1").is_file())
            finally:
                parent = logging.getLogger("jobtracker")
                parent.removeHandler(handler)
                handler.close()


if __name__ == "__main__":
    unittest.main()
