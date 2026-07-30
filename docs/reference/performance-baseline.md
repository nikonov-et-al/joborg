# Performance Baseline

Status: current

Last updated: 2026-07-30

## Method

Run from `job-tracker-mvp/`:

```powershell
.\.venv\Scripts\python.exe scripts\measure_performance.py
```

The command creates SQLite databases only inside Python temporary directories,
uses synthetic records, runs each measurement three times, reports the median,
and deletes the data. It does not resolve application settings or
`JOBTRACKER_DATA_DIR`.

Recorded environment: CPython 3.14.4, SQLite via the Python standard library,
Windows local filesystem.

## Baseline

| Records | List median | List SELECTs | Detail median | Detail SELECTs |
|---:|---:|---:|---:|---:|
| 50 | 2.419 ms | 51 | 0.217 ms | 4 |
| 1,000 | 70.612 ms | 1,001 | 0.362 ms | 4 |

## Decision

List query count grows per record because skill rows are loaded separately.
At 1,000 synthetic records the measured median remains below 100 ms, so no
batch-query refactor, cache, pagination, FTS, or new index is justified now.
Rerun this baseline when normal list interaction exceeds an agreed target or
representative datasets grow materially.

Timing values are machine-specific evidence, not pass/fail test thresholds.
