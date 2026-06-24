from __future__ import annotations

from pathlib import Path
import shutil


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2

    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def list_input_files(input_dir: Path) -> list[str]:
    input_dir.mkdir(parents=True, exist_ok=True)
    return sorted(p.name for p in input_dir.iterdir() if p.is_file())


def move_file(input_dir: Path, filename: str, destination: Path) -> Path:
    source = input_dir / filename
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    final_destination = unique_path(destination)
    shutil.move(str(source), str(final_destination))
    return final_destination


def copy_or_move_file(input_dir: Path, filename: str, destination: Path, move: bool = True) -> Path:
    source = input_dir / filename
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    final_destination = unique_path(destination)

    if move:
        shutil.move(str(source), str(final_destination))
    else:
        shutil.copy2(str(source), str(final_destination))

    return final_destination
