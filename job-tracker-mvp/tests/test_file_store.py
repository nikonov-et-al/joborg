from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jobtracker.file_store import ManagedFileStore


class ManagedFileStoreTests(unittest.TestCase):
    def test_stores_verified_metadata_in_uuid_shards_without_name_collisions(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / "files"
            store = ManagedFileStore(root)

            first = store.store_bytes("../Résumé.PDF", b"first")
            second = store.store_bytes("../Résumé.PDF", b"second")

            self.assertEqual(first.original_filename, "Résumé.PDF")
            self.assertEqual(first.sha256, hashlib.sha256(b"first").hexdigest())
            self.assertEqual(first.size, 5)
            self.assertEqual(first.mime_type, "application/pdf")
            self.assertNotEqual(first.stored_path, second.stored_path)
            for managed, content in ((first, b"first"), (second, b"second")):
                relative = Path(managed.stored_path)
                self.assertEqual(len(relative.parts), 2)
                self.assertEqual(relative.parts[0], relative.stem[:2])
                self.assertEqual(relative.suffix, ".pdf")
                self.assertEqual(store.resolve(managed.stored_path).read_bytes(), content)

    def test_resolve_rejects_absolute_and_traversal_paths(self) -> None:
        with TemporaryDirectory() as temp:
            store = ManagedFileStore(Path(temp) / "files")

            for unsafe in (
                "../outside",
                "aa/../../outside",
                "C:outside",
                Path(temp) / "outside",
            ):
                with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                    store.resolve(unsafe)

    def test_hash_verification_failure_leaves_no_file_or_temp_artifact(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / "files"
            store = ManagedFileStore(root)

            with patch.object(store, "_hash_file", return_value="wrong"):
                with self.assertRaises(OSError):
                    store.store_bytes("proof.bin", b"proof")

            self.assertEqual([path for path in root.rglob("*") if path.is_file()], [])


if __name__ == "__main__":
    unittest.main()
