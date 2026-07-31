# Milestone 10A Plan - Repository Privacy and Shared-Ref Closure

Status: complete; shared-history remediation moved to separate approval gate

Started: 2026-07-31

Completed: 2026-07-31

## Assumptions

- Current working copies are user data and must not be deleted or modified.
- The existing `PLAN.md` remediation audit is the accepted planning baseline.
- Current-tree cleanup is already present; verification must prove that state.
- `refs/codex/` are local-only checkpoint trees, not shared Git history.
- Remote inspection is read-only. No rewrite, force-push, release deletion, or
  credential rotation is authorized.

## Plan

1. Inventory `HEAD`, index, staged changes, working-tree categories, and refs
   without printing file contents -> verify: forbidden categories are counted
   and current tracked paths are clean.
2. Replace the staged-only privacy command with explicit `HEAD`, index, staged,
   shared-ref, and all-local-ref scopes -> verify: default checks current Git
   state; ref modes state exactly which refs they inspect.
3. Add regressions for tracked forbidden files, clean staged diffs, ref
   selection, and path-only failure output -> verify: focused tests pass.
4. Add missed root generated-output ignore rules and update privacy operations
   documentation -> verify: representative paths are ignored and release
   commands use the default current-state check.
5. Fetch and inspect remote refs when authentication permits -> verify: shared
   refs are current and pass, or unresolved external state is recorded without
   making a false completion claim.
6. Run focused, full, privacy, syntax, and diff checks; archive this plan and
   remove Milestone 10A from active `PLAN.md` -> verify: evidence is recorded
   and Milestone 10B becomes the current next step.

## Files likely touched

- `.gitignore`
- `PLAN.md`
- `MILESTONE_10A_PLAN.md`
- `docs/privacy.md`
- `docs/history/README.md`
- `job-tracker-mvp/scripts/check_repo_privacy.py`
- `job-tracker-mvp/tests/test_repo_privacy.py`

## Acceptance criteria

- No forbidden runtime/generated path is tracked in `HEAD` or the index.
- Default privacy verification checks `HEAD`, index, and staged additions.
- Shared-ref and all-local-ref modes have distinct, documented scopes.
- Failures expose path/category only, never matched content.
- Remote state is freshly verified or explicitly recorded as unresolved.
- User working copies and Git history remain unchanged except for new commits.

## Non-goals

- No live-file deletion or modification.
- No history rewrite, force-push, credential rotation, or remote mutation.
- No new dependency or third-party scanning service.

## Completion evidence

- Commit `bc27ece` removed two root logs from tracking while preserving both
  working files and their SHA-256 hashes.
- Runtime inventory remained 73 files with unchanged path/hash aggregate
  `52F189493081B17568DDCAF0E2288AC751E6D9CEAD017EAC86606739E39E9B1D`.
- `python scripts/check_repo_privacy.py` passed `HEAD`, index, and staged state.
- Focused privacy tests passed: 5 tests.
- Full isolated Python verification passed: 179 tests and 31 subtests.
- JavaScript syntax and Python bytecode checks passed.
- `git fetch --prune origin` refreshed `origin/master`; the shared-ref scan
  still found forbidden historical paths. No rewrite or remote mutation ran.
- Authenticated hosting metadata for visibility, forks, pull requests,
  releases, and archives remained unavailable. Fresh ref evidence superseded
  the prior shared-remediation claim.
- The unresolved rewrite/force-push work moved to
  [`../SHARED_HISTORY_REMEDIATION_PROPOSAL.md`](../SHARED_HISTORY_REMEDIATION_PROPOSAL.md)
  and requires explicit owner approval.
- The legacy setup-bridge behavior script failed on its stale inline-script
  regex; Milestone 10A changed no setup/desktop code. Milestone 10D retains that
  behavior-test gate.
