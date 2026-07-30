# Milestone 6 Plan - Repository Privacy and Data Separation

Status: complete

Completed: 2026-07-30

Started: 2026-07-30

## Assumptions

- Files under runtime `jobs/`, `profile/`, `reports/`, and `input/` paths are
  user data even when they contain no obvious secret.
- Existing user files must remain on disk while Git stops tracking them.
- Local refs are evidence for the inventory; GitHub visibility and distribution
  state require authenticated remote evidence.
- History rewriting must occur only from a recoverable copy after its rules and
  result are reviewed.

## Plan

1. Inventory sensitive/generated paths and secret-shaped configuration across
   all local refs; inspect authenticated remote visibility and distribution
   points -> verify: report contains safe counts/patterns only and names every
   evidence source.
2. Add deny-by-default ignore rules, a secret-free example config, and one
   standard-library staged privacy checker -> verify: representative forbidden
   paths fail, synthetic test fixtures remain allowed, no real content appears
   in test output.
3. Make runtime configuration independent of the checkout and remove forbidden
   generated/runtime paths from Git's index without deleting working copies ->
   verify: runtime config tests pass, ignored files remain on disk, Git no
   longer tracks them.
4. Prepare a recoverable history-rewrite copy and reviewed `git filter-repo`
   rules; inspect every rewritten branch/tag before any shared-ref replacement
   -> verify: known forbidden paths are absent and source history remains.
5. Apply the reviewed remote remediation, re-check visibility/assets/refs, and
   record residual exposure and collaborator recovery steps -> verify: remote
   refs match reviewed history and no credential remediation remains open.
6. Run focused and full relevant checks, archive this plan with evidence, then
   mark Milestone 6 complete in `PLAN.md` -> verify: acceptance criteria are
   supported by recorded commands rather than assertion.

## Files likely touched

- `.gitignore`
- `MILESTONE_6_PLAN.md`
- `PLAN.md`
- `docs/history/README.md`
- `docs/privacy.md`
- `job-tracker-mvp/config.example.yml`
- `job-tracker-mvp/Makefile`
- `job-tracker-mvp/jobtracker/config.py`
- `job-tracker-mvp/scripts/check_repo_privacy.py`
- `job-tracker-mvp/tests/test_repo_privacy.py`
- focused configuration/path tests if behavior requires it

## Acceptance criteria

- Personal runtime data and generated artifacts are ignored and untracked
  without deletion from disk.
- Staged forbidden paths and common secret assignments fail a deterministic
  repository check.
- Runtime configuration defaults outside the source checkout.
- Every rewritten local and remote branch/tag contains zero known forbidden
  runtime/generated paths.
- Authenticated repository visibility, known distribution points, residual
  exposure, credential disposition, and recovery procedure are documented
  without personal content.
- Relevant tests pass without accessing live application data.

## Non-goals

- No deletion or mutation of live application data.
- No new dependency, hook manager, secret-scanning service, or CI framework.
- No promise that third-party clones or caches can be erased.
- No unrelated schema, AI, API, or UI work.

## Progress - 2026-07-30

- Inventory completed across all local refs with safe category counts.
- Authenticated GitHub visibility/distribution evidence unavailable; `gh` and
  non-interactive credentials were absent.
- Ignore rules, safe example config, staged privacy check, all-ref history
  check, tests, and operator target added.
- 101 forbidden files removed from the Git index; working copies preserved.
- External recovery bundle created and verified.
- Reviewed filter specification applied to a disposable mirror.
- Nine Codex-only checkpoint tree refs were excluded from the disposable
  remediated ref set because `git-filter-repo` rewrites commit refs, not raw tree
  refs.
- Disposable mirror verification passed with 10 commits, 23 Python source
  files, 21 Python test files, and one remediated `master` ref.
- Isolated full suite passed: 160 tests.
- Repository owner independently verified the shared remediation and requested
  milestone completion on 2026-07-30.
