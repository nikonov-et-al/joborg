"""Reject staged runtime data, generated files, and secret-shaped values."""

from __future__ import annotations

import argparse
from collections import Counter
import re
import subprocess
import sys
from pathlib import PurePosixPath


FIXTURE_ROOT = ("job-tracker-mvp", "tests", "fixtures")
RUNTIME_COMPONENTS = frozenset(
    {"jobs", "profile", "reports", "input", "files", "backups", "logs"}
)
GENERATED_COMPONENTS = frozenset(
    {"__pycache__", ".pytest_cache", "build", "dist"}
)
FORBIDDEN_NAMES = frozenset({"config.yml"})
FORBIDDEN_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".bak",
    ".backup",
    ".log",
    ".rar",
    ".7z",
    ".zip",
    ".pdf",
    ".pyc",
    ".pyo",
)
CONFIG_SUFFIXES = (".cfg", ".env", ".ini", ".json", ".toml", ".yaml", ".yml")
SAFE_SECRET_VALUES = frozenset(
    {"", "false", "local-not-needed", "none", "null", "true"}
)
SECRET_PATTERNS = (
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("GitHub token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("OpenAI token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
)
SECRET_ASSIGNMENT = re.compile(
    r"""(?im)^\s*["']?(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|secret)["']?\s*[:=]\s*["']?([^"'#\s,}]+)"""
)


def path_violation(path: str) -> str | None:
    candidate = PurePosixPath(path.replace("\\", "/"))
    lowered = tuple(part.casefold() for part in candidate.parts)
    name = candidate.name.casefold()

    if any(part in GENERATED_COMPONENTS or part.endswith(".egg-info") for part in lowered):
        return "generated path"
    if name.endswith((".pyc", ".pyo")):
        return "generated file"

    is_fixture = lowered[: len(FIXTURE_ROOT)] == FIXTURE_ROOT
    if is_fixture:
        return None
    if any(part in RUNTIME_COMPONENTS for part in lowered):
        return "runtime data path"
    if name in FORBIDDEN_NAMES:
        return "local configuration"
    if name.endswith(FORBIDDEN_SUFFIXES):
        return "sensitive/generated file type"
    return None


def secret_violations(text: str) -> set[str]:
    found = token_violations(text)
    for match in SECRET_ASSIGNMENT.finditer(text):
        if match.group(2).casefold() not in SAFE_SECRET_VALUES:
            found.add(f"non-placeholder {match.group(1).casefold()}")
    return found


def token_violations(text: str) -> set[str]:
    return {label for label, pattern in SECRET_PATTERNS if pattern.search(text)}


def _git(*args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(
        ("git", *args),
        text=text,
        encoding="utf-8" if text else None,
    )


def staged_paths() -> list[str]:
    output = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    assert isinstance(output, str)
    return [path for path in output.split("\0") if path]


def staged_bytes(path: str) -> bytes:
    output = _git("show", f":{path}", text=False)
    assert isinstance(output, bytes)
    return output


def staged_check() -> int:
    failures: list[tuple[str, str]] = []
    for path in staged_paths():
        violation = path_violation(path)
        if violation:
            failures.append((path, violation))
            continue
        text = staged_bytes(path).decode("utf-8", errors="ignore")
        failures.extend((path, label) for label in sorted(secret_violations(text)))

    if failures:
        print("Repository privacy check failed:", file=sys.stderr)
        for path, reason in failures:
            print(f"- {path}: {reason}", file=sys.stderr)
        return 1
    print("Repository privacy check passed.")
    return 0


def history_check() -> int:
    output = _git("rev-list", "--objects", "--all")
    assert isinstance(output, str)
    objects = [line.split(" ", 1) for line in output.splitlines() if " " in line]
    violations = Counter(
        reason
        for _, path in objects
        for reason in [path_violation(path)]
        if reason
    )
    secrets: Counter[str] = Counter()
    seen_blobs: set[str] = set()
    for object_id, path in objects:
        if object_id in seen_blobs or _git("cat-file", "-t", object_id).strip() != "blob":
            continue
        seen_blobs.add(object_id)
        content = _git("cat-file", "-p", object_id, text=False)
        assert isinstance(content, bytes)
        text = content.decode("utf-8", errors="ignore")
        labels = token_violations(text)
        if path.casefold().endswith(CONFIG_SUFFIXES):
            labels.update(secret_violations(text))
        secrets.update(labels)

    if violations or secrets:
        print("Repository history privacy check failed:", file=sys.stderr)
        for reason, count in sorted(violations.items()):
            print(f"- {reason}: {count}", file=sys.stderr)
        for label, count in sorted(secrets.items()):
            print(f"- secret-shaped {label}: {count}", file=sys.stderr)
        return 1
    print("Repository history privacy check passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--history",
        action="store_true",
        help="check paths reachable from every local branch, remote ref, and tag",
    )
    args = parser.parse_args()
    return history_check() if args.history else staged_check()


if __name__ == "__main__":
    raise SystemExit(main())
