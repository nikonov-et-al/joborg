from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path
import re
from typing import BinaryIO
from uuid import uuid4


_CHUNK_SIZE = 1024 * 1024
_SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,16}")


@dataclass(frozen=True)
class StoredFile:
    original_filename: str
    stored_path: str
    sha256: str
    mime_type: str
    size: int


class ManagedFileStore:
    """Atomic UUID-sharded binary storage rooted at one managed directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @staticmethod
    def safe_original_filename(filename: str) -> str:
        if not isinstance(filename, str) or "\x00" in filename:
            raise ValueError("Invalid filename")
        normalized = filename.replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1].strip()
        if basename in {"", ".", ".."}:
            raise ValueError("Invalid filename")
        return basename

    @staticmethod
    def _suffix(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        return suffix if _SAFE_SUFFIX.fullmatch(suffix) else ""

    def resolve(self, stored_path: str | Path) -> Path:
        relative = Path(stored_path)
        if relative.is_absolute() or relative.drive or not relative.parts:
            raise ValueError(f"Invalid managed file path: {stored_path}")
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"Invalid managed file path: {stored_path}")
        destination = (self.root / relative).resolve()
        if not destination.is_relative_to(self.root):
            raise ValueError(f"Managed file path escapes storage root: {stored_path}")
        return destination

    def store_bytes(
        self,
        filename: str,
        content: bytes,
        *,
        expected_sha256: str | None = None,
    ) -> StoredFile:
        from io import BytesIO

        with BytesIO(content) as source:
            return self._store(source, filename, expected_sha256=expected_sha256)

    def store_file(
        self,
        source: Path,
        *,
        filename: str | None = None,
        expected_sha256: str | None = None,
    ) -> StoredFile:
        original_filename = filename or source.name
        with source.open("rb") as reader:
            return self._store(
                reader,
                original_filename,
                expected_sha256=expected_sha256,
            )

    def _store(
        self,
        source: BinaryIO,
        filename: str,
        *,
        expected_sha256: str | None,
    ) -> StoredFile:
        original_filename = self.safe_original_filename(filename)
        while True:
            token = uuid4().hex
            stored_path = Path(token[:2]) / f"{token}{self._suffix(original_filename)}"
            destination = self.resolve(stored_path)
            if not destination.exists():
                break

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as writer:
                while chunk := source.read(_CHUNK_SIZE):
                    writer.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            actual_sha256 = digest.hexdigest()
            if expected_sha256 is not None and actual_sha256 != expected_sha256:
                raise OSError(f"Managed file source hash mismatch: {original_filename}")
            if self._hash_file(temporary) != actual_sha256:
                raise OSError(f"Managed file destination hash mismatch: {original_filename}")
            temporary.replace(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)

        return StoredFile(
            original_filename=original_filename,
            stored_path=stored_path.as_posix(),
            sha256=actual_sha256,
            mime_type=mimetypes.guess_type(original_filename)[0]
            or "application/octet-stream",
            size=size,
        )

    def remove(self, stored_path: str | Path) -> None:
        destination = self.resolve(stored_path)
        destination.unlink(missing_ok=True)
        try:
            destination.parent.rmdir()
        except OSError:
            pass

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()
