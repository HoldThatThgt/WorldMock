Continue the resource-and-rule simplification of WorldMock (proposal `docs/dev/PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md`).

Get the work: `git fetch origin` and check out branch `resource-rule-simplification` (pushed 2026-10-06; based on origin/main `35d0b61`). Work on that branch, ideally in its own worktree. Other sessions push to main concurrently.

State left by the previous session:
- Draft spec: `docs/dev/SPEC-resource-and-rule-simplification.md` (status: unfrozen draft). Its section 「第 1 轮审查的阻断发现」 lists 34 blocking findings from three adversarial reviews (conflicts with frozen specs, requirement coverage, implementability). External references for its constants: `docs/dev/research/resource-rule-simplification/references.md`.
- Working materials in `handoff/resource-rule-simplification/` on the branch:
  - `rulings-final.md` (authoritative rulings and their precedence over the older `rulings-maps.md` and `rulings-draft.md`).
  - `understand/SYNTHESIS.md` (file-change list, conflicting clauses, decisions D-1..D-52, data contracts, fixture blast radius, work packages P0-P15) plus sixteen subsystem maps.
  - `design/critique-*-r1.md` (full review reports with the fixes for each finding).
  - `spec-brief.md` (required spec structure and documentation-gate conventions).
  - `impl-brief.md` (rules for implementation agents; its Windows paths describe the previous PC).
  - `references.md` (working copy of the references).
  Delete `handoff/` before merging to main.

Do, in order:
1. Revise the spec so every one of the 34 blocking findings is closed. Use the fixes in `design/critique-*-r1.md` and keep the rulings. When a finding contradicts a ruling, keep the ruling and add a 裁决记录 row explaining why. Then remove the findings section or mark each item closed.
2. Re-run a three-lens adversarial review (conflicts, coverage, implementability) and revise until no blocking finding remains.
3. Freeze: set the status to FROZEN and register it in `docs/INDEX.md`: spec row FROZEN, proposal row 已裁决, each revised spec's row noting the stage that implements its revision, and a version-table row. Run `python scripts/documentation_gate.py` and `python scripts/build_design_doc.py --check`.
4. Implement stages 1-8 exactly as the spec says, with the acceptance ledger and mutation tests the spec defines. Do not defer anything, and enforce that with tests. Do not build anything the spec does not describe. Stage 9 (re-publishing the formal environment in WorldMock-Dataset) is a dataset-repo handoff; record what could not be run.
5. When done: `git fetch origin`, merge origin/main into the branch, re-run the gates, delete `handoff/`, then push to main carefully. Three sessions share this repo, so fast-forward only and re-fetch right before pushing.

Environment notes:
- Set up with `python scripts/bootstrap.py --profile dev` (or `release` when Node and LabelKit are present). Run tests with the repo's venv: `python -m pytest <files> -q --environment-profile dev -p no:cacheprovider`. The full tier is `python scripts/run_tests.py --tier full`; complete-environment regression additionally needs `--environment-root ../WorldMock-Dataset/environment/zhangjiang-baseline` (clone WorldMock-Dataset as a sibling).
- On Windows: check out with `core.autocrlf=false`, set as worktree config with `git config --worktree core.autocrlf false`, never plain `git config`. CRLF fixtures break the manifest hashes ("Scenario corpus manifest identity is invalid"). Use a `--basetemp` on the same drive as the repo. Three synthetic-repo cases in `tests/test_documentation_gate.py` fail under a global `autocrlf=true`; the real-repository case must pass.
- Baseline at `35d0b61`: without `--environment-root` about 1,300 environment-dependent tests fail, and the published Dataset environment is rejected after the parallel-activities merge. There is no green baseline; the spec's rich artificial fixture is meant to become the first one.
- Model preference: Fable for design and adversarial review, Opus for implementation and editing.
