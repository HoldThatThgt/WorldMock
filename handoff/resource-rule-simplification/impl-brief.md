# Shared brief for implementation agents (resource-and-rule simplification)

You implement one work package of the frozen spec `docs/dev/SPEC-resource-and-rule-simplification.md` of the WorldMock repository.
Authority: the spec (rulings, data contracts, deletions, ledger) > `scratchpad/understand/SYNTHESIS.md` (file-change list, §4 shapes, §7 packages) > the subsystem maps in `scratchpad/understand/*.md` (where the code is today). The proposal is background only.

## Where you work

- Integration branch: `resource-rule-simplification` in the worktree `D:/code/WorldMock/.claude/worktrees/rrs` (do not edit files there directly unless the orchestrator says your package runs in that worktree).
- Your package runs in its own git worktree created from the integration branch; the orchestrator gives you the path and the branch name. Before starting, run `git merge --ff-only resource-rule-simplification` in your worktree so you start from the latest integration commit. Commit on your branch only; never touch `D:/code/WorldMock` (the main checkout), other worktrees, or `D:/code/WorldMock-Dataset`.
- Never use bare `git stash`/`git stash pop` (the stash is shared across worktrees).
- Python: `D:/code/WorldMock/.venv/Scripts/python.exe` (do not use `uv run`). Tests: `D:/code/WorldMock/.venv/Scripts/python.exe -m pytest <files> -q --environment-profile dev -p no:cacheprovider` from your worktree root. The dev profile skips the LabelKit/Node-only tests; everything else must pass. The whole suite is ~2.5 h; run only the test files your package owns plus the gate files named below, unless told otherwise.
- `AGENTSOCIETY_LLM_API_KEY` is set by the test conftest; tests never call a model.

## House rules that gates enforce (`tests/test_style_gate.py`, `tests/test_import_graph.py`, `tests/test_single_source.py`, `tests/test_documentation_gate.py`, `tests/test_design_doc_build.py`)

- Production code under `src/worldmock` and `custom`: every function (also private) has a docstring containing Chinese (Doxygen style `@brief` is the convention), ≤ 50 lines per function, ≤ 5 parameters, ≤ 120 columns, ≤ 2000 lines per file. Code, logs and every error/reason/fix/expected string are English; comments Chinese. No new violations: the style gate has a frozen per-file violation ledger (`_OUTSTANDING`) that must only shrink.
- No duplicate constant tables or enum lists (the single-source gate's clone detector): define each closed set once and import it; register new owner modules in `tests/test_single_source.py::OWNERS` when the spec says so.
- Import direction rules in `tests/test_import_graph.py` (`FOUNDATION`, moved declarations, delayed imports); new foundation modules must be registered there.
- No deferral markers in production or test files of this feature (TODO, FIXME, skip, xfail, "待实现", `pytest.skip`), no empty-shell functions.
- Old formats are rejected, never migrated: no compatibility branches, no fallback reads, no "if old key present" code.
- Docs: any edit to `docs/design/0*.md` or `docs/CONTRACTS.md` requires `python scripts/build_design_doc.py` in the same commit (commit the regenerated `docs/design/worldmock-design.html`); `--check` must pass. Explicit anchors `<a name="..."></a>` above headings; no `file.py:123` line references in prose; links must resolve.
- Every behaviour you add or change needs a test that fails without it (the ledger's mutation rows describe the expected kill); tests go through real request handling, `LifeEngine` and SQLite where the spec says so (`engine_factory` with `mutate` for scenarios).

## Commit conventions

- Small logical commits with conventional prefixes used in this repo (`feat(life):`, `feat(catalog):`, `refactor:`, `test:`, `docs:`); message body in English; end every commit message with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Do not commit generated test receipts or `.work/` contents.

## Reporting

Return: the branch name and head commit, the files changed, the exact pytest commands you ran with pass/fail counts, any spec clause you could not implement as written (quote it and say why), and anything another package must know (interface names you fixed, shapes you emit).

## Line endings (Windows)

The machine's global `core.autocrlf=true` converts fixture JSON to CRLF and breaks the manifest hashes
(`Scenario corpus manifest identity is invalid`). Every worktree for this feature is created with
`git -c core.autocrlf=false worktree add <path> -b <branch> resource-rule-simplification` followed by
`git -C <path> config --worktree core.autocrlf false` (never plain `git config core.autocrlf`, which edits the shared repo config).
Write all new files with LF endings (`newline="\n"` in Python, `.gitattributes` already covers `*.py`, `*.toml`, `corpora/**`, `catalog/**`).

## Test environment recipe for a package worktree (Windows)

1. `mkdir vendor` then `cmd /c "mklink /J vendor\agentsociety2 D:\code\WorldMock\vendor\agentsociety2"` and the same for `vendor\labelkit` (read-only junctions to the main checkout; needed by the documentation gate's source references and the LabelKit tests). Remove with `rmdir vendor\agentsociety2` (never `rm -r`, which would delete the main checkout's files).
2. Always set `PYTHONDONTWRITEBYTECODE=1` so no `.pyc` is written into the main checkout through the junction.
3. Always pass `--basetemp D:/code/wm-test-tmp/<your-branch>` (same drive as the repo); a temp dir on `C:` makes `engine_factory` copy corpora and breaks path assertions.
4. Known machine-only failures to ignore (pre-existing at the feature baseline): `tests/test_documentation_gate.py` synthetic-repository cases `test_archive_snapshot_disclaims_unreconstructible_execution`, `test_archive_relative_links_resolve_from_original_path_without_rewriting_body`, `test_exact_reference_source_is_explicit_and_does_not_search_other_baselines` (they build tmp git repos under the global autocrlf=true). The real-repository case `test_real_repository_documents_and_source_references_resolve` must pass.
