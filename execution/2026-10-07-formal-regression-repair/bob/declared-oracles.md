# Declared mutation oracles

Baseline commit: `36603196cb6495d0b0854e249564ccd7f418e457`

The unchanged oracle for every mutant is:

```text
.venv/bin/python -m pytest -q --environment-profile release tests/test_bootstrap.py tests/test_environment_profiles.py -p no:cacheprovider
```

The baseline completed with `42 passed in 4.66s`; no skips, xfails, collection errors, or capability omissions were reported.

The reviewed requirements are the preparation contract in `docs/manual/runtime.md`, lines 30-41 and 53-54, together with the stage 8 no-false-completion rules in `docs/dev/SPEC-resource-and-rule-simplification.md`, lines 1080-1081, 1202, 1320, and 1328-1335.

The planned one-at-a-time production mutants are:

- accept a printable Node version when the SQLite probe exits nonzero;
- replace the bound SQLite write and JSON query with module loading only;
- retain the write and query but stop checking the returned JSON values;
- report the Chinese font capability as available after the child selector fails;
- let release ignore a missing Chinese font while preserving the detected missing component;
- write `passed: true` in a dev receipt despite a real missing component;
- call and import the production font selector in the parent before the existing child probe.

Each mutation changes only `scripts/bootstrap_environment.py`. Tests, fixtures, documentation, dependency locks, and generated files remain unchanged. After each oracle run, the source is restored from the recorded commit and both `git diff --exit-code` and complete porcelain status must be clean before the next mutant.
