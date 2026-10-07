# Restore source alignment verification

Status: `targeted_source_alignment_verified`.

Only test source paths changed across the 13 exclusively owned files. No production source, frozen specification,
global environment selector, or published formal data was edited by this worker. No commit was created.

Factory-created artificial checkpoints and reports now use the matching artificial source. Independent selected
formal loader, assembly, direct lifecycle, and entrypoint cases retain the selected source. The mixed lifecycle
helper in `test_restored_model_identity.py` carries its actual source path with the saved run.

## Actual verification

- HEAD: `c0265684bb2015f20353b3f3b55ab70123b9bd1c`.
- Exit code: `0`.
- Pytest: `365 passed, 2 warnings in 27.47s`.
- Wall duration: `29.688` seconds.
- Guard: all 234 original test function names, 698 assertion AST nodes, parameterization/decorator AST,
  error contracts, loop bounds, and numeric literals are unchanged from HEAD.
- Frozen specification digest matches the authoritative digest; production source diff is empty.

```sh
.venv/bin/python -m pytest tests/integration/test_errand_checkpoint.py tests/integration/test_observation_agent_seam.py tests/integration/test_same_second_continuation.py tests/integration/test_read_query_runtime.py tests/life/test_coverage_closure_scenario.py tests/life/test_day_plan.py tests/life/test_energy_feasibility.py tests/life/test_engine_branch_coverage.py tests/life/test_engine_feedback.py tests/life/test_restored_model_identity.py tests/life/test_session_state.py tests/life/test_workload_report.py tests/life/test_counterfactual.py -q --environment-profile dev --environment-root /Users/atishoo/Project/WorldMock-Dataset/environment/shanghai-simplified -p no:cacheprovider
```

The exact environment, argv, per-file guard checks, and SHA-256 digests are recorded in `restore-alignment.json`.
The log, command receipt, original contracts, final contracts, and exclusive diff are adjacent ignored artifacts.

## Remaining parent responsibility

This is a targeted verification. The parent owns full regression, the original 5701-node reconciliation,
generic formal witnesses, and final gates.
