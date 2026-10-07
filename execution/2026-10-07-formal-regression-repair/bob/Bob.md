# Bob report

Status: `COMPLETE`

## Scope and authority

- Repository root: caller checkout `resource-rule-simplification/WorldMock`; review execution occurred only in detached worktree `/tmp/worldmock-bootstrap-bob.s2dk2L`.
- Baseline commit: `36603196cb6495d0b0854e249564ccd7f418e457`.
- Normative authority: `docs/manual/runtime.md` preparation contract at lines 30-41 and 53-54; stage 8 evidence and no-false-completion rules in `docs/dev/SPEC-resource-and-rule-simplification.md` at lines 1080-1081, 1202, 1320, and 1328-1335.
- Production scope: `scripts/bootstrap_environment.py` only. Tests, fixtures, documentation, locks, and generated files were never mutated.
- Reviewed behavior: actual Node SQLite bound write and JSON query result; production Chinese font selector in the root-environment child; release rejection for a missing component; dev receipt nonpassing state for a missing component; parent-process import boundary.

## Baseline

Declared unchanged oracle for all mutants:

```text
.venv/bin/python -m pytest -q --environment-profile release tests/test_bootstrap.py tests/test_environment_profiles.py -p no:cacheprovider
```

The locked bootstrap completed successfully in the detached worktree. Its receipt recorded CPython `3.12.13`, Node `v26.0.0`, `node_sqlite_available=true`, `chinese_chart_font_available=true`, no missing components, and `passed=true`.

The pre-mutation oracle exited `0`: `42 passed in 4.66s`. No skips, xfails, collection errors, or missing-capability exits occurred. The post-restoration rerun exited `0`: `42 passed in 5.39s`.

## Counts

| Measure | Count |
| --- | ---: |
| Mapped semantic requirements | 7 |
| Implementation missing | 0 |
| Implementation diverged | 0 |
| Killed mutants | 7 |
| Survived mutants | 0 |
| Invalid mutants | 0 |
| Inconclusive mutants | 0 |

## Implementation evidence

- `scripts/bootstrap_environment.py:22-30` performs an in-memory SQLite table creation, bound insert, `json_extract`, `json_each`, and exact returned-value assertion.
- `scripts/bootstrap_environment.py:124-141` treats probe exit status as capability state; a printable Node version remains identity only.
- `scripts/bootstrap_environment.py:144-156` invokes `worldmock.evaluation.reporting._chinese_font` through the root `.venv` Python child and maps child failure to unavailable.
- `scripts/bootstrap_environment.py:159-181` preserves the three-component missing set and rejects every nonempty set under release.
- `scripts/bootstrap_environment.py:184-200` writes `passed` from the actual missing-component set.
- `src/worldmock/evaluation/reporting.py:145-151` is the real production font selector used by chart generation.

No implementation drift was observed in the reviewed behavior.

## Mutation ledger

| Mutant | Production change | Causal outcome |
| --- | --- | --- |
| Node version substitutes for SQLite result | Returned availability from `node_version is not None` | `killed`; both injected module failure and wrong query result reached `_node_status`, logged probe failure, then failed the `node_sqlite_available is False` assertion; `2 failed, 40 passed` |
| SQLite module load substitutes for bound write and JSON queries | Replaced the probe program with `require('node:sqlite')` | `killed`; the injected wrong query became invisible and the availability assertion failed; `1 failed, 41 passed` |
| Query output is not checked | Retained the write and query but removed the exact `[[7, 7]]` assertion | `killed`; injected `[[8, 8]]` was accepted by the mutant and the availability assertion failed; `1 failed, 41 passed` |
| Font child failure is ignored | Returned `True` after the child exited nonzero | `killed`; the real production selector was faulted in the child and `chinese_chart_font_available is False` failed; `1 failed, 41 passed` |
| Release ignores a missing Chinese font | Limited release blockers to Node and LabelKit while preserving the detected font-missing state | `killed`; both the real font-failure receipt test and the profile policy test observed that release did not raise; `2 failed, 40 passed` |
| Dev receipt reports success with missing components | Wrote `passed: True` unconditionally | `killed`; five receipt assertions across LabelKit, SQLite, font, and generic dev-missing paths failed; `5 failed, 37 passed` |
| Production font selector enters the parent process | Imported and called `_chinese_font` in the parent before the unchanged child probe | `killed`; the clean-parent probe found `worldmock.evaluation.reporting` in `sys.modules`; `1 failed, 41 passed` |

Every failure occurred after the intended behavior path ran. None was caused by syntax, collection, import startup, missing capability, timeout, or an unrelated gate.

## Evidence artifacts

- `declared-oracles.md` records the oracle and all seven mutations before the first mutation.
- `bootstrap.log` records the locked environment bootstrap and capability receipt.
- `baseline.log` and `final-restoration.log` record the passing pre-mutation and post-restoration oracles.
- `mutant-01.diff` through `mutant-07.diff` contain the exact one-at-a-time production diffs.
- `mutant-01.log` through `mutant-07.log` contain the causal test failures.
- `cleanup.log` records detached-worktree removal and final caller identity checks.

## Restoration and caller state

Before mutation, and after every individual mutant, the detached worktree passed `git diff --exit-code` and complete porcelain status was empty. After the final mutant it remained at `36603196cb6495d0b0854e249564ccd7f418e457` with an empty diff and status.

`git worktree remove /tmp/worldmock-bootstrap-bob.s2dk2L` exited `0`; the path no longer exists and the worktree is no longer registered.

The restored scoped hashes were:

| File | SHA-256 |
| --- | --- |
| `scripts/bootstrap_environment.py` | `8b6d04655b6df0b4e3342ae3a1ddeb96959989fa934fdfaf2f855a2c9ee9761d` |
| `tests/test_bootstrap.py` | `ad21c3b2a663dbc9b2b453d59d50692d7cba53c8016d72bf5f8719de5792620a` |
| `tests/test_environment_profiles.py` | `67f692f0951da64f9ff95d6d3a1ed905e71d3917087e5f99dd7a4e004c7f17bc` |

The caller advanced externally during this review from `36603196cb6495d0b0854e249564ccd7f418e457` to `43e8d6dd3f2b66f74c19c90776bbef3b5c2f4237`. Its final complete porcelain status was empty, and all three scoped hashes remained byte-identical to the reviewed commit. This review made no caller source change.

## Residual limits

- The result is host-local macOS evidence. It does not establish Windows, Linux, or release-platform acceptance.
- The Chinese-font no-skip policy passed its existing assertions, but it was not given a production-source mutant because skip insertion is owned by `tests/conftest.py`, outside the parent-provided mutation scope.
- The review did not assess behavior outside the named bootstrap preparation contract and two named test files.

No parent decision is required for the reviewed production scope.
