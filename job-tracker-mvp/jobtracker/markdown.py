from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def write_markdown_with_frontmatter(path: Path, metadata: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    content = f"---\n{frontmatter}---\n\n{body.strip()}\n"
    path.write_text(content, encoding="utf-8")


def read_markdown_with_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")

    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    metadata_text = parts[1]
    body = parts[2].lstrip("\n")
    metadata = yaml.safe_load(metadata_text) or {}
    return metadata, body


def update_markdown_frontmatter(path: Path, updates: dict[str, Any]) -> None:
    metadata, body = read_markdown_with_frontmatter(path)
    metadata.update(updates)
    write_markdown_with_frontmatter(path, metadata, body)


def append_to_markdown_body(path: Path, text_to_append: str) -> None:
    metadata, body = read_markdown_with_frontmatter(path)
    new_body = body.rstrip() + "\n\n" + text_to_append.strip() + "\n"
    write_markdown_with_frontmatter(path, metadata, new_body)
