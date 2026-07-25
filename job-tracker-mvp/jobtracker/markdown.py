from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml


class AtomicWriteError(OSError):
    """A safe, stage-specific error raised by :func:`atomic_write_text`."""

    def __init__(self, path: Path, stage: str) -> None:
        self.path = path
        self.stage = stage
        messages = {
            "temporary_file_creation": "Could not create a temporary file",
            "write": "Could not write the temporary file",
            "flush": "Could not flush the temporary file",
            "replace": "Could not replace the destination file",
            "cleanup": "Could not remove the temporary file",
        }
        super().__init__(f"{messages[stage]} for {path}")


def atomic_write_text(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        )
    except OSError as exc:
        raise AtomicWriteError(path, "temporary_file_creation") from exc

    temporary_path = Path(temporary_file.name)
    failure: BaseException | None = None
    try:
        try:
            temporary_file.write(content)
        except (OSError, UnicodeError) as exc:
            raise AtomicWriteError(path, "write") from exc

        try:
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_file.close()
        except OSError as exc:
            raise AtomicWriteError(path, "flush") from exc

        try:
            temporary_path.replace(path)
        except OSError as exc:
            raise AtomicWriteError(path, "replace") from exc
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if not temporary_file.closed:
            try:
                temporary_file.close()
            except OSError:
                pass
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as exc:
            if failure is None:
                raise AtomicWriteError(path, "cleanup") from exc


def write_markdown_with_frontmatter(path: Path, metadata: dict[str, Any], body: str) -> None:
    frontmatter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    content = f"---\n{frontmatter}---\n\n{body.strip()}\n"
    atomic_write_text(path, content)


def read_markdown_with_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    if not lines or lines[0].strip() != "---":
        return {}, "".join(lines)

    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if closing_index is None:
        raise ValueError(f"Unclosed frontmatter: {path}")

    metadata_text = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :]).lstrip("\r\n")
    metadata = yaml.safe_load(metadata_text) or {}

    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid frontmatter object: {path}")

    return metadata, body


def update_markdown_frontmatter(path: Path, updates: dict[str, Any]) -> None:
    metadata, body = read_markdown_with_frontmatter(path)
    metadata.update(updates)
    write_markdown_with_frontmatter(path, metadata, body)


def append_to_markdown_body(path: Path, text_to_append: str) -> None:
    metadata, body = read_markdown_with_frontmatter(path)
    new_body = body.rstrip() + "\n\n" + text_to_append.strip() + "\n"
    write_markdown_with_frontmatter(path, metadata, new_body)
