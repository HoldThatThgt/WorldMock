# Bob report

Status: COMPLETE. Initial review: `d998a311ff51f518c975fb29eb1f631d13528d5a`. Final unchanged-patch recheck: `aa22dc830d6a66d13f14a22a04babae5cb41ec98`.

Repository root and caller checkout: `/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-bob-anchor-0axnuyu4`.
Detached review worktree: `/tmp/worldmock-bob-experiences-worktree-9FSF7k`.
Specification authority: `docs/dev/SPEC-dayrun-behavior.md`, clauses 33–53, 129–165, plus export/person-time acceptance in 210–223. No other document was used as product authority.

## Scope and baseline

The reviewed requirements are: real wait duration and no implicit entertainment; podcast segment disclosure only on the completed official anchor; arrival presentation and known-supply purchase authorization; positive incremental meal consumption and interrupted checkpoint recovery; the four verified experience kinds with own-agent, committed-source and cutoff boundaries; latest five in an observation versus full export prefix; and per-person elapsed interval union. Code and tests were mapped in `declared-mutants.json`. This is not an audit of parallel resource admission, memory consolidation, wake warnings, or full day runs.

The caller checkout was clean and at `d998a31` before isolation and after worktree registration. The review worktree imported WorldMock, AgentSociety2 and `custom.agents.life_agent` from its own paths. Local vendor repositories were copied from the supplied development tree at exact commits `8768e14d` and `d0afd606`, with no remote fetch. The prescribed Python virtual environment was used with `PYTHONPATH=src:.:vendor/agentsociety2/packages/agentsociety2`, test-only model configuration pointing to `127.0.0.1:9`, and telemetry off.

Declared baseline command: `python -m pytest -q tests/footprints/test_verified_experiences.py tests/footprints/test_export.py tests/life/test_dayrun_podcast_playback.py tests/life/test_dayrun_restaurants.py tests/life/test_wait_passive_entertainment.py tests/life/test_storefront_arrival.py tests/integration/test_meal_checkpoint.py tests/evaluation/test_behavior_metrics.py` — **54 passed in 7.83s**. The quality/export command `python -m pytest -q tests/footprints/test_quality.py tests/footprints/test_export.py` was baselined at **27 passed in 0.14s**. The one existing invisible-purchase command was separately baselined: `python -m pytest -q tests/life/test_engine_branch_coverage.py::test_purchase_rejects_invisible_product` — **1 passed in 0.69s**. Full command arguments, environment and logs are preserved in `run_mutants.py`, `run_hidden_supply_mutant.py`, `baseline.log`, and `baseline-purchase.log`. No network model or production run was executed.

Mapped requirement groups: 10. Observed production implementation missing/diverged: 0 in this bounded code mapping. Initial d998a31: **5 killed, 4 survived, 1 inconclusive, 0 invalid**. Final aa22dc8 on the same exact ten source patches: **10 killed, 0 survived, 0 invalid, 0 inconclusive**. No mutation score or clause coverage percentage is claimed.

## Fixed-snapshot recheck receipt

The new caller `/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-bob-recheck-anchor-io6or6l7` was clean at `aa22dc8` before and after a second detached worktree was registered. The second tree `/tmp/worldmock-bob-experiences-recheck-QFVDyL` imported WorldMock, AgentSociety2 and the actual `custom` LifeAgent from its own paths. The same local vendor commit identities and offline-only environment were used.

Combined baseline for the reviewed test files and invisible-purchase case was **79 passed in 8.38s** (`recheck-full-baseline.log`). All ten original `.patch` files applied cleanly, and each applied Git diff was byte-for-byte identical to its first-run patch; `run_recheck.py`, `run_recheck_remaining.py`, `recheck-results.json` and `recheck-remaining-results.json` record the patch SHA-256, exact original test command, exit, causal log and clean restoration. Every command below used the same declared test selection as in the first round:

| Original semantic mutant | Final aa22dc8 result | Direct causal assertion |
| --- | --- | --- |
| wait_relief | killed: 4 failed, 2 passed | Wait entertainment need became 0 instead of 0.48. |
| podcast_early_reveal | killed: 1 failed, 2 passed | Cue ending at 3 seconds appeared at position 1 second. |
| purchase_hidden_supply | killed: 2 failed | Both ghost and existing unpresented market supply cases rejected the bypass. |
| purchase_known_hidden | killed: 1 failed, 1 passed | Existing unpresented market supply purchase was completed rather than rejected. |
| consume_cumulative_as_increment | killed: 1 failed, 11 passed | Checkpoint meal total projected 2.06 instead of 1.0. |
| recent_oldest_five | killed: 2 failed, 9 passed | Eight- and forty-event histories returned older identities instead of the latest five. |
| export_only_five | killed: 1 failed, 8 passed | Six committed waits exported as five. |
| person_time_add_overlap | killed: 1 failed, 20 passed | Same-person behavior metric became 75 instead of union 55 minutes. |
| quality_person_time_add_overlap | killed: 1 failed, 27 passed | Actual SQLite export quality report became 75 instead of union 55 seconds. |
| model_source_accepted | killed: 1 failed, 10 passed | Model-origin source no longer raised the required integrity error. |

Every mutant remained runnable. Each was restored before the next, with `git diff --exit-code` and full porcelain status empty. The initial ghost-only purchase failure remains recorded below as an inconclusive first-round signal, while the final test's real unpresented-supply case causally kills the same original patch.

## Mutation ledger

| Mutation and specification anchor | Production path | Predeclared test command and exact result | Classification and causal evidence |
| --- | --- | --- | --- |
| Wait gains hidden entertainment relief, 35–39 | `src/worldmock/life/body.py` | `pytest -q tests/life/test_wait_passive_entertainment.py`: 4 failed, 2 passed, exit 1 | **Killed.** Real wait body outcome became 0.0 instead of 0.48, and entertainment flow assertions failed. `wait_relief.patch`, `wait_relief.log`. |
| Podcast cue appears on overlap before anchor completion, 51–53 | `src/worldmock/life/content.py` | `pytest -q tests/life/test_dayrun_podcast_playback.py`: 1 failed, 2 passed, exit 1 | **Killed.** At media position 1 second, cue ending at 3 seconds appeared; test requires an empty revealed list. `podcast_early_reveal.patch`, `.log`. |
| All supply visibility checks bypassed, 136–139 | `src/worldmock/life/storefront_runtime.py` | `pytest -q tests/life/test_engine_branch_coverage.py::test_purchase_rejects_invisible_product`: 1 failed, exit 1 | **Inconclusive for intended authorization.** Existing test used nonexistent `food-ghost`; the result stayed rejected and only its error changed from `SUPPLY_NOT_VISIBLE` to `SUPPLY_NOT_FOUND`. That failure does not detect a purchase of an existing but unpresented supply. `purchase_hidden_supply.patch`, `.log`. |
| Existing unpresented supply is admitted while ghost remains rejected, 136–139 | `src/worldmock/life/storefront_runtime.py` | Same predeclared purchase test: 1 passed, exit 0 | **Survived.** The exact patch and diagnostic are in `purchase_known_hidden.patch`, `.log` and `purchase_known_hidden_probe.log`. A scratch probe through the real `LifeEngine` decision round and formal market observed 20 shown and 139 purchasable unshown supplies. An unshown local supply was `rejected SUPPLY_NOT_VISIBLE` on original source with balance/stock `(800000, 120)` unchanged, but `completed` under this mutant with `(799450, 119)`. Existing oracle checks a ghost ID only. The probe and logs are `probe_hidden_known_supply_public.py`, `probe_hidden_known_supply_public_baseline.log` and `purchase_known_hidden_public_probe.log`. |
| Cumulative meal quantity is written as every interval's increment, 141–143 and 159–165 | `src/worldmock/life/activity_evidence.py` | `pytest -q tests/integration/test_meal_checkpoint.py tests/footprints/test_verified_experiences.py`: 1 failed, 9 passed, exit 1 | **Killed.** Interrupted checkpoint meal projected 2.06 eaten instead of 1.0. `consume_cumulative_as_increment.patch`, `.log`. |
| Observation returns oldest five from page, 149–153 | `src/worldmock/footprints/experiences.py` | `pytest -q tests/footprints/test_verified_experiences.py`: 9 passed, exit 0 | **Survived.** Current tests have at most four own-agent facts in the observed window. Add committed history beyond five and assert exact latest source IDs, order, own agent and observation cutoff. `recent_oldest_five.patch`, `.log`. |
| Export truncates full history to five, 153–154 | `src/worldmock/footprints/export.py` | `pytest -q tests/footprints/test_export.py`: 1 failed, 7 passed, exit 1 | **Killed.** Six committed waits produced only five exported rows. `export_only_five.patch`, `.log`. |
| Person elapsed time adds overlapping intervals, 41–42 | `src/worldmock/evaluation/behavior_metrics.py` | `pytest -q tests/evaluation/test_behavior_metrics.py`: 20 passed, exit 0 | **Survived.** Existing body-settlement time example uses adjacent intervals and does not distinguish union from sum. Add overlapping, contained and disjoint intervals with a separate agent. `person_time_add_overlap.patch`, `.log`. |
| Export quality person elapsed time adds overlapping intervals, 41–42 | `src/worldmock/footprints/quality.py` | `pytest -q tests/footprints/test_quality.py tests/footprints/test_export.py`: 27 passed, exit 0; baseline 27 passed | **Survived.** `quality.json.activities.person_elapsed_seconds_by_agent` has its own union implementation; neither existing quality nor export test distinguishes union from sum. `quality_person_time_add_overlap.patch`, `.log`, `baseline-quality-export.log`. |
| Model-origin activity source passes projection provenance, 149–154 | `src/worldmock/footprints/experiences.py` | `pytest -q tests/footprints/test_verified_experiences.py`: 1 failed, 8 passed, exit 1 | **Killed.** The explicit model-source rejection no longer raised `IntegrityError`. `model_source_accepted.patch`, `.log`. |

Each mutant was isolated, test command unchanged from its declaration, then restored before the next mutation. `results.json` records command, exit and clean-restoration flag. The scratch known-supply probe is diagnostic, not a replacement for the declared oracle; its initial fixture-discovery setup error was corrected in scratch before the original-versus-mutant comparison.

## Documentation consistency

The fixed tree's current `docs/design/05-recording-engine.md:549` and `docs/CONTRACTS.md:383` still call the world schema version `4`, while `docs/design/08-configuration.md:135`, `docs/CONTRACTS.md:286`, the new specification's 59–61 and the production `WorldState` use `5`. These are current design/contract prose, not historical change-log entries. The recording design's 260–269 matches the new specification's own committed projection, four kinds, latest-five window and full export. These documentation findings were passed to the parent. In the final `aa22dc8` snapshot, both lines now say version 5, matching the frozen specification and `docs/design/08-configuration.md:135`. A later wider read-only document audit, including remaining traceability drift, is `final-document-review.md`.

## Restoration and limits

Before each new mutant, `git diff --exit-code` and `git status --porcelain=v1 --untracked-files=all` in the detached worktree were empty. After the final mutant they were empty again. After the final mutation the detached worktree diff and full status were empty at `d998a31`; caller HEAD remained `d998a31` with empty full status. `git worktree remove /tmp/worldmock-bob-experiences-worktree-9FSF7k` failed with `working trees containing submodules cannot be moved or removed`. `git submodule deinit -f --all` removed local submodule work trees, but the normal removal retried with the same error. Per skill, the registered clean worktree is preserved at that exact path; no force-removal was attempted. Follow-up retest used `/tmp/worldmock-bob-experiences-recheck-QFVDyL`. Its final diff and full status were empty at `aa22dc8`, and the new caller remained clean at the same commit. Normal `git worktree remove` on the recheck tree also failed with `working trees containing submodules cannot be moved or removed`; per skill, both exact registered paths remain clean and preserved without force removal. The controlled known-supply probe also exercised the public decision round using an unpresented but purchasable local market supply; it staged location and time as a scenario test condition rather than driving an actual route. A permanent regression should assert the public observation window and no balance, inventory, order or stock change. Formal multiple-day and full repository gates belong to the parent and were not run by this bounded review.


归档说明：报告所列原始补丁、命令结果与恢复收据保存在 [证据包](evidence.tar.gz)，
逐文件 SHA-256 见 [清单](evidence-manifest.json)。压缩仅用于原样保留补丁和日志字节。
