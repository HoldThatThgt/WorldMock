# Brief for writing docs/dev/SPEC-resource-and-rule-simplification.md

Target file (in the worktree D:/code/WorldMock/.claude/worktrees/rrs): `docs/dev/SPEC-resource-and-rule-simplification.md`.
Language: Chinese prose like every other `docs/dev/SPEC-*.md` (code identifiers and error codes stay English).
Templates to imitate for structure, tone and conventions: `docs/dev/SPEC-idle-fillers.md` (most recent frozen feature spec,
with 裁决记录 D/R rows, 现行文档原位改写 tables, 随实现一起改的测试与夹具, 验收台账, 变异登记, 实施阶段与提交, 门禁与平台)
and `docs/dev/SPEC-parallel-activities.md` (shorter, same conventions). The proposal is
`docs/dev/PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md`; evidence in `docs/dev/research/resource-rule-simplification/`.

Inputs the writer must read completely before writing: the proposal and its three evidence files; `scratchpad/rulings-draft.md`,
`scratchpad/rulings-maps.md`, `scratchpad/references.md` (citations for constants and conventions); `scratchpad/understand/SYNTHESIS.md`
and, for any subsystem detail, the sixteen maps in `scratchpad/understand/*.md`; the two template specs; `docs/INDEX.md` 状态与变更约定
and 权威层级; `docs/design/README.md` 更新约定; `AGENTS.md`.

## House conventions (violations fail the documentation gate `scripts/documentation_gate.py` and `tests/test_documentation_gate.py`)

- Every heading gets an explicit anchor line immediately above it: `<a name="spec-rrs-<slug>"></a>`; anchors unique in the file; the title anchor is `spec-rrs-资源与规则引擎简化开发规格`.
- File header lines (blockquote): `> 状态：FROZEN。…`, `> 日期：2026-10-06。`, `> 代码基线：<full 40-hex commit of the worktree HEAD>。`, `> 来源：[提案](PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md)…`, `> 范围：…`. Historical-document rules do not apply (this is a current FROZEN spec), but the code baseline must be a real commit.
- Do not cite source lines by number (`file.py:123` is forbidden by the gate). Cite definitions as `path::Name` only when the definition exists at the baseline commit; new modules are cited by path only, new tests only by file path or in the machine ledger (rule R9 of the idle-fillers spec).
- Links to other repo documents must resolve: `[text](SPEC-x.md#anchor)` with existing anchors (check with grep), or backtick paths with `#anchor`.
- Tables: Markdown pipes; no stray blank line inside a version table; the version table is the last section, new rows appended at the end.
- Prose must not contain 延期/跳过/待实现 markers for this feature; stages that are not executed on this machine are stated as such in 门禁与平台, never as "待".
- Mermaid diagrams are allowed and used by the other specs for the mental model.

## Required sections, in order

1. 标题与文件头.
2. 用户得到什么 (one paragraph + a mermaid flow + explicit non-goals).
3. 裁决记录: a table with the proposal's ten decision rows (D1–D10, each adopting the recommended option) and all gap rulings from rulings-draft.md (R-A…R-Q, Q1–Q15) and rulings-maps.md (M*, S*, F*, W*, C*, O*, A*, E*). Each row: 编号 | 定稿 | 依据 (the reference from references.md or the proposal evidence; when a value is a simulation assumption say 实验假设 and name the closest reference). Keep the identifiers so the implementation and the ledger can cite them.
4. 固定规则与常数 (WorldMock-owned, `RULES_VERSION`): closed sets (PlaceKind, FOOD_STORE_KINDS, FoodCategory, ContentKind, ActivityKind after the change, WAYS additions/removals, ScheduledKind, WakeReason additions, error-code additions/removals) and the constants table (name, value, unit, 依据).
5. 环境格式 (the dataset contract): `manifest.json` (root), `catalog/apps.json`, `catalog/locations.json`, `catalog/map.json`, `catalog/foods.json`, `catalog/contents.json`, `corpora/lifestyle/items.json`, scenario structure 7 (full field list: what stays, what is deleted, `work_topics`, `environment_id`, `environment_manifest_sha256`, implicit accounts); registration checks and the English error for each; the `worldmock validate --environment` and `worldmock map generate` commands.
6. 地图与出行: generation rule, validation, shortest path, travel time and fare, trip identity in evidence, next-step wording.
7. 店铺、食品与外卖: same-kind menus, `all`, app bindings, named purchase (M0–M3 over foods/stores/apps), prefer, in-store purchase, delivery (ETA, fee, destination rules incl. en route), orders and DeliveryState, implicit accounts, holdings.
8. 三类食品与进食: fixed kcal and durations, settlement, body efficiency, tool-layer state, motivation rule.
9. 娱乐：app、内容与推送: app targets, way 打开, app_use activity lifecycle (draw order, continuation, early stop, pause/resume), content progress and re-watch, relief constants, push scheduling (hourly grid, eligibility, session scope, storage on SessionState, wake reason, progress record, expiry), determinism of draws, APP_NO_CONTENT.
10. 工作主题与叙述: topics, opening/deadline, accrual, completion event, narration via progress reports (tool-layer state fields), wages, deletions (materials, submit_work, review).
11. 观察与工具面: tool-layer target kinds after the change (add app; remove content/game/catalog), callable_index keys, system-layer rules that change (list rule numbers and give the final text for each changed rule verbatim; keep 18 rules; respect the existing constraints listed in SPEC-idle-fillers A and SPEC-parallel-activities 丙), prompt/tool fingerprint consequences.
12. 状态、事件、检查点与导出: WorldState 10 field changes (add/remove), SessionState.pushes, AgentState.work_progress, ContentProgress, DeliveryState, ScheduledKind, environment_event kinds, export artifacts (app_usage.jsonl, purchases.jsonl columns), manifest versions, old-format rejection.
13. 删除清单: production modules, symbols, scenario/catalog fields, error codes, wake reasons retained-without-source, tests and fixtures to delete, with the pinning ledger/fixture named for each pinned item (from SYNTHESIS.md §1).
14. 现行文档原位改写: one table per stage listing every current document and section (anchor) that conflicts (from SYNTHESIS.md §2 and the maps' §6) and the replacement semantics in one line; include INDEX.md (register this spec as FROZEN, mark the proposal 已裁决), implementation-status.md (new section), VERIFICATION.md (new gate section + matrix rows), EXPERIMENT.md (constants), ANALYSIS-spec-coverage.md (new mapping section), CONTRACTS.md sections, design chapters 00–09 with version-row rule (R8 of idle-fillers: version row id `2026-10-06-rrs-<stage>`, design chapters bump the header version once), manuals (file-header dated note), README.md, `tests/fixtures/environment/README.md`.
15. 随实现一起改的测试与夹具: per stage, files added/rewritten/deleted; the artificial environment fixture contents; acceptance ledgers of earlier features whose rows are retired or whose 判据 text is corrected (同步登记, never silently).
16. 验收台账: contract points → stage → 必须通过的验证 (named T-cases with one-line intent), registered in `tests/fixtures/rrs-acceptance.json` with `tests/rrs_acceptance.py` and the guard `tests/test_rrs_acceptance.py` (same shape as the idle-fillers ledger: contract point → {stage, spec anchor, tests[], implementation[]}; STAGES_DONE; FEATURE_PRODUCTION_FILES; FEATURE_TEST_FILES; MUTATION_ROWS). Cover every 必须钉住的行为 row of the proposal's 验收反例 table.
17. 变异登记: table 变异 | 阶段 | 被谁杀死, one or more per contract point, each describing a source mutation that changes behaviour.
18. 数据仓交接: what WorldMock-Dataset must produce (file list, counts expected for the formal environment, real-name requirements, validation command, acceptance), stated as dataset-repo work with its own commits; what cannot be verified in this repo and how it is recorded.
19. 实施阶段与提交: stage table (0 冻结, 1 固定规则与环境格式, 2 地图与出行, 3 店铺食品外卖, 4 娱乐与推送, 5 工作, 6 观察工具面与提示, 7 记录导出与查看器, 8 文档与登记收口, 9 数据仓交接与完整回归) with commit prefixes; each stage's files it owns.
20. 门禁与平台: targeted pytest on Windows per stage; standard/full tiers and coverage gate on POSIX; documentation gate and design-doc rebuild on every doc commit; what cannot run here (full tier with `--environment-root`, TextWorld removal makes the Windows collection work again — state it if true after implementation).
21. 已知局限.
22. 版本表 (one row `2026-10-06-rrs-freeze`).

## Scope guards

- Do not invent features outside the proposal; where the proposal is silent, the rulings files decide; if neither covers a point, choose the smallest option consistent with "WorldMock fixes the rules, the dataset supplies names, summaries, synthetic numbers and bindings; no compatibility layers", and add it to 裁决记录 with 依据 "本规格补定".
- Everything in the spec must be implementable and testable in this repository except the dataset re-make, which is the handoff section.
- The spec must be self-consistent: every closed-set member, field, constant, error code and event kind mentioned anywhere must appear in sections 4, 5 or 12 exactly once as the definition.
