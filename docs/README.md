# Job Tracker Documentation

Status: active index

Last updated: 2026-07-30

## Purpose

Keep project knowledge easy for people and language models to locate, verify,
and update without scanning obsolete plans or reverse-engineering the source.

## Read order

1. Active agent instructions and development plans are local and intentionally
   not tracked.
2. [`privacy.md`](privacy.md) - repository data boundary, safe exposure
   inventory, and history-remediation state.
3. `architecture/` - current system boundaries, data flow, persistence, and
   runtime lifecycle.
4. `decisions/` - accepted architecture decision records and their rationale.
5. `guides/` - task-oriented operating, development, release, migration, and
   recovery instructions.
6. `reference/` - schemas, API contracts, configuration, terminology, and
   invariants.
7. [`history/`](history/README.md) - completed/superseded plans retained for
   evidence. History is not current instruction.

Only `history/` exists initially because it has real content. Create the other
directories when the first useful document is added; do not add empty
placeholder trees.

## Intended structure

```text
docs/
|-- README.md
|-- architecture/
|   |-- overview.md
|   |-- data-and-storage.md
|   `-- security-model.md
|-- decisions/
|   `-- ADR-0001-short-title.md
|-- guides/
|   |-- development.md
|   |-- release.md
|   `-- recovery.md
|-- reference/
|   |-- configuration.md
|   |-- database-schema.md
|   `-- api-contracts.md
`-- history/
    |-- README.md
    `-- completed and superseded plans
```

The names above are routing guidance, not a requirement to create every file.

## LLM-friendly documentation rules

- Put status and last-updated date near the top.
- State purpose, scope, invariants, and non-goals explicitly.
- Keep one authoritative source and link to it instead of copying content.
- Use stable headings and exact repository-relative paths.
- Distinguish current behavior, planned behavior, and historical behavior.
- Record decisions in ADRs; do not silently rewrite why a decision was made.
- Keep acceptance evidence in history after a milestone is complete.
- Prefer tables and lists for exact mappings; prefer prose for rationale.
- Include commands only when verified, and state the expected result.
- Never include personal job data, CV content, secrets, live database paths, or
  sensitive remediation details.
- Update documentation in the same accepted step as the behavior it describes.

## Document lifecycle

- Active agent instructions and plans remain local and untracked.
- Completed milestone detail moves to `docs/history/`.
- Current architecture docs describe what the accepted code does now.
- Proposed architecture belongs in a local plan or an ADR marked `proposed`.
- Superseded docs receive a banner linking to their replacement; they are not
  deleted when they provide useful decision or verification history.
