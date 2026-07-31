"""Reject runtime data, generated files, and secret-shaped values in Git."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath


FIXTURE_ROOT = ("job-tracker-mvp", "tests", "fixtures")
RUNTIME_COMPONENTS = frozenset(
    {"jobs", "profile", "reports", "input", "files", "backups", "logs", "tmp"}
)
GENERATED_COMPONENTS = frozenset(
    {"__pycache__", ".pytest_cache", "build", "dist"}
)
FORBIDDEN_NAMES = frozenset({"config.yml"})
FORBIDDEN_PATHS = frozenset(
    {
        "agents.md",
        "docs/privacy.md",
        "docs/shared_history_remediation_proposal.md",
        "job-tracker-mvp/report.md",
        "plan.md",
        "report.md",
    }
)
ALLOWED_HIDDEN_COMPONENTS = frozenset({".github"})
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
    ".doc",
    ".docx",
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
SHARED_REF_PREFIXES = ("refs/heads/", "refs/remotes/", "refs/tags/")


def path_violation(path: str) -> str | None:
    candidate = PurePosixPath(path.replace("\\", "/"))
    lowered = tuple(part.casefold() for part in candidate.parts)
    name = candidate.name.casefold()

    if any(
        part.startswith(".") and part not in ALLOWED_HIDDEN_COMPONENTS
        for part in lowered[:-1]
    ):
        return "hidden local directory"
    if any(part in GENERATED_COMPONENTS or part.endswith(".egg-info") for part in lowered):
        return "generated path"
    if name.endswith((".pyc", ".pyo")):
        return "generated file"

    is_fixture = lowered[: len(FIXTURE_ROOT)] == FIXTURE_ROOT
    if is_fixture:
        return None
    if any(part in RUNTIME_COMPONENTS for part in lowered):
        return "runtime data path"
    if candidate.as_posix().casefold() in FORBIDDEN_PATHS:
        return "generated report"
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


def _nul_paths(*args: str) -> list[str]:
    output = _git(*args)
    assert isinstance(output, str)
    return [path for path in output.split("\0") if path]


def head_paths() -> list[str]:
    return _nul_paths("ls-tree", "-r", "--name-only", "--full-tree", "-z", "HEAD")


def index_paths() -> list[str]:
    return _nul_paths("ls-files", "--full-name", "-z")


def staged_paths() -> list[str]:
    return _nul_paths(
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACMR",
        "-z",
        "--",
        ":(top)",
    )


def git_bytes(spec: str) -> bytes:
    output = _git("show", spec, text=False)
    assert isinstance(output, bytes)
    return output


def source_failures(
    scope: str,
    paths: Iterable[str],
    read_bytes: Callable[[str], bytes],
) -> list[tuple[str, str, str]]:
    failures: list[tuple[str, str, str]] = []
    for path in paths:
        violation = path_violation(path)
        if violation:
            failures.append((scope, path, violation))
            continue
        text = read_bytes(path).decode("utf-8", errors="ignore")
        labels = token_violations(text)
        if path.casefold().endswith(CONFIG_SUFFIXES):
            labels.update(secret_violations(text))
        failures.extend(
            (scope, path, label) for label in sorted(labels)
        )
    return failures


def current_check(scope: str) -> int:
    checks = {
        "head": lambda: source_failures(
            "HEAD", head_paths(), lambda path: git_bytes(f"HEAD:{path}")
        ),
        "index": lambda: source_failures(
            "index", index_paths(), lambda path: git_bytes(f":{path}")
        ),
        "staged": lambda: source_failures(
            "staged", staged_paths(), lambda path: git_bytes(f":{path}")
        ),
    }
    selected = ("head", "index", "staged") if scope == "default" else (scope,)
    failures = [failure for name in selected for failure in checks[name]()]
    if failures:
        print("Repository privacy check failed:", file=sys.stderr)
        for source, path, reason in failures:
            print(f"- {source}: {path}: {reason}", file=sys.stderr)
        return 1
    checked = ", ".join(selected)
    print(f"Repository privacy check passed ({checked}).")
    return 0


def all_ref_names() -> list[str]:
    output = _git("for-each-ref", "--format=%(refname)")
    assert isinstance(output, str)
    return [ref for ref in output.splitlines() if ref]


def refs_for_scope(scope: str) -> list[str]:
    refs = all_ref_names()
    if scope == "shared-refs":
        return [ref for ref in refs if ref.startswith(SHARED_REF_PREFIXES)]
    return refs


def history_check(scope: str) -> int:
    refs = refs_for_scope(scope)
    if not refs:
        print(f"Repository privacy check passed ({scope}: no refs).")
        return 0

    output = _git("rev-list", "--objects", *refs)
    assert isinstance(output, str)
    objects = [line.split(" ", 1) for line in output.splitlines() if " " in line]
    violations = Counter(
        reason
        for _, path in objects
        for reason in [path_violation(path)]
        if reason
    )
    object_paths: dict[str, list[str]] = defaultdict(list)
    for object_id, path in objects:
        object_paths[object_id].append(path)

    secrets: Counter[str] = Counter()
    for object_id, paths in object_paths.items():
        if _git("cat-file", "-t", object_id).strip() != "blob":
            continue
        content = _git("cat-file", "-p", object_id, text=False)
        assert isinstance(content, bytes)
        text = content.decode("utf-8", errors="ignore")
        labels = token_violations(text)
        if any(path.casefold().endswith(CONFIG_SUFFIXES) for path in paths):
            labels.update(secret_violations(text))
        secrets.update(labels)

    if violations or secrets:
        print(f"Repository privacy check failed ({scope}):", file=sys.stderr)
        for reason, count in sorted(violations.items()):
            print(f"- {reason}: {count}", file=sys.stderr)
        for label, count in sorted(secrets.items()):
            print(f"- secret-shaped {label}: {count}", file=sys.stderr)
        return 1
    print(f"Repository privacy check passed ({scope}, {len(refs)} refs).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scope",
        choices=("default", "head", "index", "staged", "shared-refs", "all-local-refs"),
        default="default",
        help="Git state to inspect; default checks HEAD, index, and staged additions",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="deprecated alias for --scope all-local-refs",
    )
    args = parser.parse_args()
    if args.history and args.scope != "default":
        parser.error("--history cannot be combined with --scope")
    scope = "all-local-refs" if args.history else args.scope
    if scope in {"shared-refs", "all-local-refs"}:
        return history_check(scope)
    return current_check(scope)


if __name__ == "__main__":
    raise SystemExit(main())
