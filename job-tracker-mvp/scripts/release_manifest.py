"""Write privacy-safe checksum metadata for one packaged executable."""

from __future__ import annotations

import argparse
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform


def _version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(artifact: Path) -> dict[str, object]:
    artifact = artifact.resolve(strict=True)
    if not artifact.is_file():
        raise ValueError("Artifact must be a file")
    return {
        "format_version": 1,
        "distribution": "unsigned-development",
        "application_version": _version("jobtracker"),
        "python_version": platform.python_version(),
        "build_tools": {
            "pyinstaller": _version("pyinstaller"),
            "pywebview": _version("pywebview"),
        },
        "artifact": {
            "name": artifact.name,
            "size": artifact.stat().st_size,
            "sha256": _hash(artifact),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist") / "release-manifest.json",
    )
    args = parser.parse_args()
    payload = build_manifest(args.artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
