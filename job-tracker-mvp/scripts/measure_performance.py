"""Measure SQLite list/detail work against disposable synthetic data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from jobtracker.database import database_connection
from jobtracker.models import JobApplication
from jobtracker.sqlite_repository import SQLiteJobRepository, insert_application


def _seed(repository: SQLiteJobRepository, count: int) -> str:
    first_id = ""
    with database_connection(repository.database) as connection, connection:
        for index in range(count):
            application_id = f"synthetic-{index:06d}"
            first_id = first_id or application_id
            insert_application(
                connection,
                application_id,
                JobApplication(
                    company=f"Company {index:06d}",
                    role="Engineer",
                    date_applied=f"2026-{index % 12 + 1:02d}-{index % 28 + 1:02d}",
                    required_skills=["Python", "SQLite"],
                ),
                job_description="Synthetic measurement record",
                body="",
            )
    return first_id


def _measure_list(repository: SQLiteJobRepository) -> tuple[float, int]:
    queries = 0
    with database_connection(repository.database) as connection:
        def trace(statement: str) -> None:
            nonlocal queries
            if statement.lstrip().upper().startswith("SELECT"):
                queries += 1

        connection.set_trace_callback(trace)
        started = perf_counter()
        rows = connection.execute(
            "SELECT * FROM applications ORDER BY date_applied, id"
        ).fetchall()
        [(row["id"], repository._job(connection, row)) for row in rows]
        elapsed = perf_counter() - started
    return elapsed, queries


def _measure_detail(
    repository: SQLiteJobRepository,
    application_id: str,
) -> tuple[float, int]:
    queries = 0
    with database_connection(repository.database) as connection:
        def trace(statement: str) -> None:
            nonlocal queries
            if statement.lstrip().upper().startswith("SELECT"):
                queries += 1

        connection.set_trace_callback(trace)
        started = perf_counter()
        row = repository._row(connection, application_id)
        repository._job(connection, row)
        connection.execute(
            """
            SELECT content FROM document_versions
            WHERE application_id = ? AND document_type = 'cv'
            ORDER BY version DESC LIMIT 1
            """,
            (application_id,),
        ).fetchone()
        repository._documents(connection, application_id)
        elapsed = perf_counter() - started
    return elapsed, queries


def run_baseline(sizes: tuple[int, ...] = (50, 1000), runs: int = 3) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for size in sizes:
        with TemporaryDirectory(prefix="jobtracker-performance-") as temp:
            root = Path(temp)
            repository = SQLiteJobRepository(
                root / "jobtracker.sqlite3",
                root / "files",
            )
            first_id = _seed(repository, size)
            list_runs = [_measure_list(repository) for _ in range(runs)]
            detail_runs = [_measure_detail(repository, first_id) for _ in range(runs)]
            results.append(
                {
                    "records": size,
                    "list_median_ms": round(
                        median(value[0] for value in list_runs) * 1000,
                        3,
                    ),
                    "list_select_queries": list_runs[0][1],
                    "detail_median_ms": round(
                        median(value[0] for value in detail_runs) * 1000,
                        3,
                    ),
                    "detail_select_queries": detail_runs[0][1],
                }
            )
    return {
        "format_version": 1,
        "runs_per_size": runs,
        "temporary_data_only": True,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run_baseline()
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
