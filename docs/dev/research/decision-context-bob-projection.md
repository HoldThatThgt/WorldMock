# Bob report

状态：COMPLETE。基线 204 passed；本报告映射 22 项验收要求，执行 29 个单一生产源码突变：killed 29，survived 0，invalid 0，inconclusive 0。未发现本范围 implementation missing 或 implementation diverged。这里不给整个规格的覆盖百分比或突变分数；其他协议与预算条款由另一审查负责。

原始日志和逐次差异保存在本任务工作区 `.work/decision-context/bob-projection/`；下列证据文件名相对于该目录。

## 隔离与范围

调用者仓库：`/Users/atishoo/.codex/worktrees/decision-context/WorldMock`
基线提交：`f8a55e21887b686b7432093f7e8c950e3f1fdf56`
规范：`docs/dev/SPEC-decision-context.md`；直接依赖：`docs/CONTRACTS.md`。下表 SPEC 行号均指该固定提交。
临时工作树：`/tmp/worldmock-bob-projection.3WMc1R`，由 `mktemp -d /tmp/worldmock-bob-projection.XXXXXX` 返回，随后 `git worktree add --detach` 固定提交。现已删除并解除登记。
AS2：从调用者子仓本地独立 clone，checkout `e41550957a50398e08570f10ecd9cdb3fc9dd37c`，不修改调用者子仓。

预检调用者 HEAD 有效，`git status --porcelain=v1 --untracked-files=all` 与 diff 为空。只在临时树修改生产源文件，每次一个突变；不编辑测试、依赖、文档或调用者源码，不提交。导入路径已核实 worldmock、custom.agents.life_agent、agentsociety2 全部来自临时树。

## 要求到执行证据

| 要求 | 权威条款 | 突变证据 |
| --- | --- | --- |
| 无上限且按事件顺序回灌 | SPEC:91–104 | result-cap、result-order |
| 回灌仅一次 | SPEC:104 | result-consumption |
| 验收准备不提前结算 | SPEC:74–75、95–104 | premature-review-settlement |
| 共同结果字段显式投影 | SPEC:93–97 | result-whole-tree |
| 嵌套工作反馈显式投影 | SPEC:95–103 | nested-review-copy |
| 递归禁止私有字段 | SPEC:102–103 | recursive-render-check |
| 评审解析私有身份/摘要拒绝及纠错 | SPEC:98–100 | review-parse-validation、review-private-reference、review-private-hash |
| 公开入口重验私有理由 | SPEC:98–99 | review-public-validation |
| 理由与拒绝原因完整保留 | SPEC:100–105 | full-reference-rationale、complete-rejection-reason |
| 最近三条与稳定时间/身份排序 | SPEC:115–117 | earliest-inbox-heads、inbox-stable-order |
| 仅未展示集合决定更早未读 | SPEC:116 | older-unread-subset |
| 地点名称营业可见、分钟不重复 | SPEC:109–110 | route-minutes-scene |
| 暂停活动保留进度 | SPEC:112 | paused-progress |
| 已开工任务仍有独立待办 | SPEC:113 | independent-todo |
| 事件自述字段与类型 | SPEC:51–55；CONTRACTS:575 | event-field-name、event-field-validation |
| 质量计数新名 | SPEC:55–56；CONTRACTS:576 | quality-field-name |
| dreaming 可读自述 | CONTRACTS:575 | dreaming-carrier |
| 动作/意图不可读自述 | CONTRACTS:575 | non-dreaming-leak、non-dreaming-runtime-filter |
| 新建清单持久化前校验 | SPEC:58；CONTRACTS:567 | new-manifest-prewrite-guard |
| 全部已有运行恢复入口校验 | SPEC:58；CONTRACTS:567 | restored-manifest-stage-guard |
| 恢复校验独立于代码身份 | SPEC:58；CONTRACTS:567 | runtime-restore-stage-guard |
| 阶段身份使用精确闭集 | SPEC:51–58；CONTRACTS:567 | stage-closed-set |

## 基线命令与结果

环境在每个测试命令中相同，所有值显式提供；test-key 是固定测试占位值，无真实模型请求。使用调用者虚拟环境解释器，仅复用已安装依赖，`PYTHONPATH` 指向临时源码。

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/tmp/worldmock-bob-projection.3WMc1R/src:/tmp/worldmock-bob-projection.3WMc1R:/tmp/worldmock-bob-projection.3WMc1R/vendor/agentsociety2/packages/agentsociety2
export WORKSPACE_PATH=/tmp/worldmock-bob-projection.3WMc1R
export MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True
export AGENTSOCIETY_LLM_API_KEY=test-key
export AGENTSOCIETY_LLM_API_BASE=https://api.deepseek.com/beta
cd /tmp/worldmock-bob-projection.3WMc1R
/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q \
  tests/life/test_decision_context_projection.py \
  tests/life/test_work_review.py \
  tests/life/test_narration.py \
  tests/life/test_grounded_observation.py \
  tests/footprints/test_event_fields.py \
  tests/footprints/test_quality.py \
  tests/delivery/test_windows.py \
  tests/integration/test_run_manifest.py \
  tests/life/test_restored_model_identity.py
```

结果：`204 passed in 24.39s`，退出码 0，无 skip。记录见 `baseline.log`、`baseline.command.json`、`baseline.json`。

## 突变台账

下表所有测试命令在对应突变前已声明到 `plan.json`；统一完整命令为上述相同环境与工作目录下的
`/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q <测试选择器>`。
每行同名 `.command.json` 保留实际完整参数，`.diff` 保留最小源码变化，`.log` 保留失败细节，
`.restoration.json` 保留该次根仓与 AS2 的干净证明。29 个测试进程退出码均为 1，失败均来自指定行为断言；没有语法、导入、启动失败计作 killed。

| 突变 | 生产路径 | 预声明测试选择器 | 结果与因果证据 |
| --- | --- | --- | --- |
| `result-cap.diff` | `src/worldmock/life/projection.py` | `tests/life/test_decision_context_projection.py::test_all_new_settlement_results_survive_and_are_consumed_once` | killed；7 条结算只剩 5 条，`assert len(public) == 7` 失败。 `result-cap.log` |
| `result-order.diff` | `src/worldmock/life/observe.py` | `tests/life/test_decision_context_projection.py::test_all_new_settlement_results_survive_and_are_consumed_once` | killed；公开首项变成 action-unobserved-6-1，期望 action-unobserved-0-1。 `result-order.log` |
| `result-consumption.diff` | `src/worldmock/life/observe.py` | `tests/life/test_decision_context_projection.py::test_all_new_settlement_results_survive_and_are_consumed_once` | killed；第二次无新结果观察仍返回 7 项，空列表断言失败。 `result-consumption.log` |
| `premature-review-settlement.diff` | `src/worldmock/life/engine.py` | `tests/life/test_work_review.py::test_prepared_work_review_is_private_until_round_settlement` | killed；评审准备完成即改变世界 version 42→43；接受情形已支付任务，接受/拒绝两分支都失败。 `premature-review-settlement.log` |
| `result-whole-tree.diff` | `src/worldmock/life/result_projection.py` | `tests/life/test_work_review.py::test_nested_private_fields_in_real_settlement_do_not_enter_public_results` | killed；公开结果多出 result、event_id、decision_id，精确字段集合断言失败。 `result-whole-tree.log` |
| `nested-review-copy.diff` | `src/worldmock/life/result_projection.py` | `tests/life/test_work_review.py::test_nested_private_fields_in_real_settlement_do_not_enter_public_results` | killed；required_fact_assessments 带出 accounts/activity 等注入私有项，与公开反馈预期不相等。 `nested-review-copy.log` |
| `recursive-render-check.diff` | `src/worldmock/life/narration.py` | `tests/life/test_decision_context_projection.py::test_nested_private_result_fields_fail_before_render_and_log` | killed；10 类嵌套私有字段均未抛 ForbiddenObservationFieldError。 `recursive-render-check.log` |
| `review-parse-validation.diff` | `src/worldmock/life/work_review.py` | `tests/life/test_work_review.py` | killed；非法理由未在解析时进入纠错，公开入口直接报错；4 个纠错用例与三次耗尽用例失败，37 项仍通过。 `review-parse-validation.log` |
| `review-public-validation.diff` | `src/worldmock/life/work_review.py` | `tests/life/test_work_review.py::test_reloaded_decision_cannot_bypass_private_rationale_validation` | killed；对象重建和 JSON 重建两入口的 12 种私有理由均未抛 WorkReviewOutputError。 `review-public-validation.log` |
| `review-private-reference.diff` | `src/worldmock/life/work_review.py` | `tests/life/test_work_review.py::test_private_rationale_is_rejected_in_parse_to_prompt_chain` | killed；两种 assessment 的内部参考身份未拒绝，2 项失败、6 项通过。 `review-private-reference.log` |
| `review-private-hash.diff` | `src/worldmock/life/work_review.py` | `tests/life/test_work_review.py::test_private_rationale_is_rejected_in_parse_to_prompt_chain` | killed；已知、任意和大写 SHA-256 在两种 assessment 中均未拒绝，6 项失败、2 项通过。 `review-private-hash.log` |
| `full-reference-rationale.diff` | `src/worldmock/life/work_review.py` | `tests/life/test_work_review.py::test_full_legal_rationale_survives_parse_projection_and_render` | killed；公开必需事实理由在 160 字符截断，与完整合法长理由不相等；交付理由对照仍通过。 `full-reference-rationale.log` |
| `complete-rejection-reason.diff` | `src/worldmock/life/models.py` | `tests/life/test_decision_context_projection.py::test_long_rejection_cause_remains_complete_through_settlement` | killed；真实结算的长拒绝原因缺失 FINAL_CAUSE_MUST_REMAIN，完整 message 断言失败。 `complete-rejection-reason.log` |
| `earliest-inbox-heads.diff` | `src/worldmock/life/projection.py` | `tests/life/test_decision_context_projection.py::test_latest_inbox_heads_use_time_and_identity_and_only_older_unread_is_flagged` | killed；头部返回 old-z/old-b，期望最近 new-c/new-d/new-e；两种未读状态均失败。 `earliest-inbox-heads.log` |
| `older-unread-subset.diff` | `src/worldmock/life/projection.py` | `tests/life/test_decision_context_projection.py::test_latest_inbox_heads_use_time_and_identity_and_only_older_unread_is_flagged` | killed；更早消息都已读时，错误读取最新集合导致 True is False。 `older-unread-subset.log` |
| `inbox-stable-order.diff` | `src/worldmock/life/projection.py` | `tests/life/test_decision_context_projection.py::test_latest_inbox_heads_use_time_and_identity_and_only_older_unread_is_flagged` | killed；完整收件列表首项 new-e，期望 old-z；两个参数用例失败。 `inbox-stable-order.log` |
| `route-minutes-scene.diff` | `src/worldmock/life/narration.py` | `tests/life/test_narration.py::TestRenderScene::test_reachable_places_do_not_render_route_minutes` | killed；场景变成“书店（营业中，13分钟）”，与保留名称营业状态的精确文本不相等。 `route-minutes-scene.log` |
| `paused-progress.diff` | `src/worldmock/life/narration.py` | `tests/life/test_work_review.py::test_paused_work_keeps_progress_and_has_an_independent_todo` | killed；真实暂停工作场景丢失“还剩约465分钟”，进度存在性断言失败。 `paused-progress.log` |
| `independent-todo.diff` | `src/worldmock/life/projection.py` | `tests/life/test_work_review.py::test_paused_work_keeps_progress_and_has_an_independent_todo` | killed；已开始但仍欠工时的任务丢失独立“有待办任务”提示。 `independent-todo.log` |
| `event-field-name.diff` | `src/worldmock/life/decision_start.py` | `tests/life/test_grounded_observation.py::test_decision_started_written_once_complete_with_decision_statement` | killed；真实 decision_started 不再有 decision_statement，测试读取该字段触发 KeyError。 `event-field-name.log` |
| `event-field-validation.diff` | `src/worldmock/footprints/models.py` | `tests/footprints/test_event_fields.py` | killed；创建、读取、持久化重校验共 7 个非法字段用例未抛 ValueError；20 项仍通过。 `event-field-validation.log` |
| `quality-field-name.diff` | `src/worldmock/footprints/quality.py` | `tests/footprints/test_quality.py::test_decision_rhythm_counts_silent_boundaries_wake_reasons_and_decision_statements` | killed；质量报告缺少 decision_started_with_decision_statement，公开键断言触发 KeyError。 `quality-field-name.log` |
| `dreaming-carrier.diff` | `src/worldmock/delivery/windows.py` | `tests/delivery/test_windows.py::test_dreaming_window_reads_decision_statement_and_scene` | killed；真实 dreaming 窗口自述行为空，期望保存的中文自述。 `dreaming-carrier.log` |
| `non-dreaming-leak.diff` | `src/worldmock/delivery/windows.py` | `tests/delivery/test_windows.py::test_action_and_intent_windows_still_exclude_the_decision_statement_carrier` | killed；明确私有事件集合断言检测到 decision_started 被移除；另以运行时过滤突变补充实际输出验证。 `non-dreaming-leak.log` |
| `new-manifest-prewrite-guard.diff` | `src/worldmock/life/engine.py` | `tests/integration/test_run_manifest.py::test_engine_create_rejects_retired_stage_before_writing_run_artifacts` | killed；后续校验虽报错，manifest.json 已写入；拒绝前不得落盘的断言失败。 `new-manifest-prewrite-guard.log` |
| `restored-manifest-stage-guard.diff` | `src/worldmock/life/engine.py` | `tests/life/test_restored_model_identity.py` | killed；open_existing、restore、from_workspace 对旧名/混入旧名/非法形状/错误参数及引擎直接打开共 13 项未拒绝，9 项对照通过。 `restored-manifest-stage-guard.log` |
| `runtime-restore-stage-guard.diff` | `src/worldmock/runtime.py` | `tests/integration/test_run_manifest.py::test_runtime_restore_rejects_retired_stage_even_when_code_identity_matches` | killed；源码身份相同时，旧阶段清单不再抛 IntegrityError。 `runtime-restore-stage-guard.log` |
| `stage-closed-set.diff` | `src/worldmock/validation.py` | `tests/integration/test_run_manifest.py::test_retired_stage_is_rejected_even_beside_the_current_stage` | killed；当前阶段旁加入 monologue 不再抛 IntegrityError；旧名替代当前名对照仍失败关口通过。 `stage-closed-set.log` |
| `non-dreaming-runtime-filter.diff` | `src/worldmock/delivery/windows.py` | `tests/delivery/test_windows.py::test_action_and_intent_windows_still_exclude_the_decision_statement_carrier` | killed；保留私有常量但放宽运行时过滤后，实际 action/intent 事件列表出现 decision_started，输出列表断言失败。 `non-dreaming-runtime-filter.log` |

## 恢复证明

每个突变后立即 `git restore --source=HEAD -- <唯一修改文件>`，然后分别对临时根仓和 AS2 执行：

```sh
git diff --exit-code
git status --porcelain=v1 --untracked-files=all
```

每次退出码均为 0，stdout/stderr 为空，才继续下一突变。AST 检查也在每个突变前通过。
最后核实临时根仓 HEAD 与 AS2 HEAD 仍为固定值、四项 diff/status 全空，再把自己创建的独立子仓 clone 移至独立临时目录，保留原空目录，用 `git worktree remove /tmp/worldmock-bob-projection.3WMc1R` 正常移除临时树，再删除自己的 clone 备份。未 force 删除注册工作树。

`cleanup.json` 记录临时树及 clone 备份都不存在、注册列表无本临时树，调用者及调用者 AS2 的 HEAD 不变、diff/status 仍为空。报告与日志只写到调用者已忽略的 `.work/decision-context/bob-projection/`。

调度过程中有三个待启动操作因首个测试仍运行，被干净状态预检阻止，未修改文件或运行测试，未计入突变台账；之后逐条等待测试完成和恢复确认。没有叠加两个突变。

## 边界

真实 LifeEngine、LifeEnv、SQLite 与窗口构造路径已经运行，模型仅用确定性测试传输或既存响应夹具。此报告不证明真实提供商网络行为、长周期生活实验、其余协议与预算条款或全项目覆盖率门禁。没有 survived，因此无待修补测试缺口；未延期本范围任何已列要求。
