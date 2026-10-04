> 代码基线：ef13dc58770af52000a1c2e81883561e85a0c904
> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/time-and-energy-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。
> 引用出处：`tests/corpora/test_text_encoding.py` @ `2cf230115f4084aef2376e9d77bc976efbed43ea`
> 引用出处说明：该源码引用来自归档快照，不是原执行代码基线下的测试执行证明。

# 时间与能量特性验收记录

> 状态：**验收记录**。阶段 0–7 全部合入；每个阶段经独立只读评审，发现的缺陷在后续提交中修复并补测。最终代码通过全量测试、覆盖率、风格、文档与完整性门禁，VF4 的 29 个语义突变全部被测试杀死。阶段 8 真实复验未执行（本机没有模型凭据，r14 的数据在另一台机器上），步骤与对照基线见「真实复验（阶段 8，只报告）」。阶段 9 见「实现后文档（阶段 9）」，实现状态文档（IS1）最后单独提交。
> 日期：2026-09-26
> 依据：[时间与能量特性规格](../SPEC-time-and-energy.md)（v1.0，FROZEN）的[验收台账](../SPEC-time-and-energy.md#验收台账)；阶段划分、文档同步作业与文件清单见[实施台账](time-and-energy-implementation-plan.md)。行为以规格为准，本记录只登记证据，不另定行为。

本记录是实施台账「文件修改清单」中「验收记录」一行的交付物：阶段 7 建档，写入台账逐行登记、全量门禁与覆盖率、VF4 突变结果与源码身份；阶段 8 的两份真实复验结果写入同一记录；阶段 9 由模拟引擎设计、验收标准、文档索引与规格覆盖分析引用。

## 源码身份

| 身份 | 固定值 |
| --- | --- |
| 最终代码提交 | `ef13dc58770af52000a1c2e81883561e85a0c904`（`ef13dc5`）。覆盖率门禁在此提交上运行；此后不改生产代码，只提交覆盖率报告、文档与完整性守卫 |
| 覆盖率报告提交 | `82e16981a340e49a071b9851350327ee8e0a53c0`（`82e1698`），只含 `docs/dev/coverage-report.json` |
| 实施基线 | `778becae4271fb29d167dbdd51a811a15ec3ec60`（`778beca`） |
| 规格冻结 | `e560cfccf1e2baa0fc5258cf0d364b076d9e4fae`（`e560cfc`，规格 v1.0） |
| AS2 | 锁定提交 `8768e14`；运行身份按源码摘要 `65a902ec66df42296d4e4c85e424bce3512e437133fc6be9fa597766fc30a334` 比对 |
| 覆盖率快照 | `snapshot_id` 为 `a4ff3a51-d4a1-497e-b3ff-92774e503478`，`manifest_hash` 为 `671ce6d4d188df1ecd05acbe0fab374b425b31e8098558f371ff3036ffe3903b`，`source_identity` 为 `ef13dc5` 的完整哈希，生成于 `2026-09-26T16:30:56+00:00` |
| 数据集仓库 `WorldMock-Dataset` | `bb186a9`：练秋湖与招明两个场景删 `max_consecutive_rejections`（阶段 1）；`befe2fc`：交接 LabelKit 前裁去 `conclude_activity` 的记忆（阶段 3）；`9071822`：阶段 4 目录变更后重建数据集（两份目录副本为 4.3、4.5） |

`git log --first-parent 778beca..82e1698` 的提交，按合入顺序：

| 提交 | 阶段 | 内容 |
| --- | --- | --- |
| `c777fc1`、`dbdae24`、`5822b54` | 规格草稿 | 草稿登记文档索引；修订到 v0.12 并拆出实施计划；零点计划 `plan_day` 改为独立的零点后台任务 |
| `e560cfc` | 冻结 | 规格冻结为 v1.0，落地全部【冻】作业；变更集只含文档 |
| `37c3f7a` | 0 绿色基线 | issue #1：空交互对象层合法、夹具、世界结构版本 7、出行快照地点、目标回看、跨任务提交、交付核对结构版本 |
| `745ebd8`；评审修复 `01f5232` | 1 同刻决策与唤醒词表 | 同刻轮次与受限轮阶梯、唤醒词表加两员、删 `max_consecutive_rejections` |
| `04892f5`（合并 `f56074d`、`ae88f03`） | 6 运行身份 | 运行身份覆盖实际导入的 AS2，清单含 `git_commit`、`git_dirty`；`ae88f03` 是评审修复 |
| `8e70d31`（合并 `b31b0bd`）；评审修复 `317bf14` | 2 身体与能量 | 能量感知、能量不拦人、作息初值、商品热量与路线费力、配送时刻 |
| `80b5fdd`（合并 `40c8675`）；评审修复 `eb3ffee` | 3 活动、会话与记忆 | 地点会话、活动生命周期与待收尾、`finish` 并入 `conclude_activity`、交付裁去记忆 |
| `ecdc130`（合并 `11f61ac`）；评审修复 `811fadd` | 5 零点计划、提醒与常驻提示 | 零点后台任务 `plan_day`、计划提醒、常驻生活提示「怎样过一天」 |
| `4013df3`（合并 `922d74e`）；评审修复 `e0dd1a0` | 4 生活动作与并行准入 | 五个动词与方式、并行准入、「刚才」、接着看、唯一一次目录变更与数据集重建 |
| `44c8725`（合并 `9d908f3`） | 7 查看器与全量门禁 | 查看器按最终事件形状显示；`scripts/inspect_run.py` 新增只读子命令 `rerun` |
| `1dab733` | 7 | 文档一致性核对，见「文档一致性」 |
| `ef13dc5` | 7 | 补测覆盖率门禁报告的未完整执行公共接口，加固两处突变的检出；不改生产代码 |
| `82e1698` | 7 | 在 `ef13dc5` 上重新生成并提交覆盖率报告 |

## 评审过程与修复

每个阶段合入后由 2–3 名互相独立的只读评审分别核对：合规与范围（对照规格条款与实施台账，查越界与遗漏）、正确性（读代码并用引擎探针复现）、文档与工程（【同】作业、风格与门禁）。评审发现在下表的修复提交中改正，每项修复都带有去掉修复即失败的用例。阶段 0 没有单独的评审修复提交；阶段 7 的后续提交见上表。

| 阶段 | 修复提交 | 发现并修复的主要缺陷 |
| --- | --- | --- |
| 1 同刻决策 | `01f5232` | 消息收件人被重复唤醒：本轮观察呈现过的消息在提交时记为已读，受限轮的收件人留到下一边界再看；被拒的成对开始留下幻影交谈邀请：投递改为返回新状态，拒绝时不留邀请；游戏节律：进行中的游戏按决策节律（规则 7 与播报），排有游戏回合时除外 |
| 2 身体与能量 | `317bf14` | 饥饿跨档扫描漏掉紧挨餐时窗口边缘的跨档：扫描另查边缘前一秒与边缘本身，预测不晚于实际变化；补吃饭途径第 4、5 档的观察级用例 |
| 3 活动、会话与记忆 | `eb3ffee` | 日首门控在睡眠待收尾时藏起 `conclude_activity`，收尾被挡；实验截止时被截断的活动（含暂停中的）逐项写 `activity_truncated` 快照，导出与最终检查点逐项一致；回看报出活动最近一段会话的地点；有活动待收尾时不发起工作评审模型调用；`SESSION_LIMIT_EXCEEDED` 说明两个名额都已占满还是只剩一个 |
| 5 零点计划与提醒 | `811fadd` | 夏令时跳过 00:00 的日子以本地日期变更的那一秒为零点，照常生成计划并后移节律；零点计划输入只列已受理、非重放的收尾；运行恰在本地零点开始时回顾时段为空；补宿主在零点步与初始检查点两处失败路径的用例；接口合同写明零点计划的人物输入是场景声明的人物画像 |
| 4 生活动作与并行准入 | `e0dd1a0` | 两种食物或同方式的两次购买合并为一餐或一单并通过准入；准入按组开始后人物所在处判定持有物与生活物品，与条目顺序无关；同一轮被接受又被对方结束的交谈在时钟推进前收尾；到店取得与远程下单不再按人物位置裁剪，照常列出并附说明，引导性拒绝点明去处；瞬时购买可与计时活动在同一请求中开始；公开原因只来自引导性拒绝，内部原因不外泄；删除冻结观察缓存，选择在每次分派时解析一次 |
| 6 运行身份 | `ae88f03` | 补不打补丁、直接检查实际导入 AS2 的锁定测试；`git_commit`、`git_dirty` 对 Git 检出内的任一项目根取 `git rev-parse HEAD` 与 `git status --porcelain`，只在检出外或 git 失败时为空；读取或哈希 AS2 文件的任何失败统一为规格的拒绝信息 `AS2 runtime identity differs from the locked source` |

## 全量门禁与覆盖率

| 检查 | 命令或入口 | 结果 |
| --- | --- | --- |
| 覆盖率门禁（含全量测试） | `python scripts/coverage_gate.py`（内部运行 `uv run coverage run -m pytest -q`），在 `ef13dc5` 上 | 3978 passed、8 skipped（既有平台跳过）；分支覆盖率 94.57%（covered 7104、missing 408、total 7512，阈值 85.00）；未覆盖公共接口 0；判定 PASSED |
| 覆盖率报告 | [coverage-report.json](../coverage-report.json)，随 `82e1698` 提交 | 顶层 `passed` 为 true，`uncovered_public_interfaces` 为空，`source_identity` 为 `ef13dc5` |
| 分片全量回归 | 每次阶段合入后分两次调用、各 8 片，跑 `tests/life` 与其余测试目录（含风格、文档、覆盖率脚本与完整性守卫的用例） | 每次合入后全绿；跳过只有 8 项既有平台跳过 |
| 风格门禁 | `tests/test_style_gate.py` | 零新增违规，`_OUTSTANDING` 的记账与实际一致 |
| 文档门禁 | `python scripts/documentation_gate.py` | PASSED，失效引用 0；阶段 7 与阶段 9 的扫描数见「文档一致性」 |
| 完整性守卫 | `tests/test_time_and_energy_completeness.py` | 本记录同批改动后通过：登记表恰好覆盖台账 16 行；阶段 0–7 都在 `STAGES_DONE` 中，其行都列出存在且未被跳过的测试；无延期标记；各阶段登记的删除符号已消失、新增符号已出现 |
| 查看器回归 | `tests/tools/test_run_viewer.py`（经 Node 执行 `tests/tools/run_viewer.test.cjs`） | 通过，含在全量测试中 |

8 项跳过都是既有的平台或产物条件跳过；本机单独运行完整性守卫豁免跳过标记的六个文件复核为 218 passed、8 skipped：TextWorld 1.7.0 在项目锁定文件中没有 Windows wheel 1 项（`tests/corpora/test_branch_coverage.py`）；Windows 账户没有创建符号链接的权限 3 项（`tests/delivery/test_host_delivery_coverage.py`、`tests/footprints/test_branch_coverage.py`、`tests/integration/test_host_branch_coverage.py`）；资金锚定运行产物不在本机 4 项（`tests/resources/test_equivalence_anchor.py`）。本特性新增与改写的用例没有跳过，完整性守卫也禁止登记的用例被跳过。

本记录同批只改文档、验收登记表（`tests/time_and_energy_acceptance.py`：阶段 7 记为完成，「无新增故障」「文档一致性」「工程门禁」三行补齐用例，登记本记录的必需符号）与完整性守卫（新增「实施阶段 0–7 全部完成」一项用例），不改生产代码，覆盖率报告不重新生成；完整性守卫、风格门禁与文档门禁的用例在这些改动上重跑通过。

## 验收台账逐行登记

台账每一行的归属阶段与证据测试以 `tests/time_and_energy_acceptance.py` 为准，下表由该文件生成（共 367 个不重复的测试节点）。归属阶段是整行完成后才可完整验证的阶段；「真实复验」行归阶段 8，只报告、不作门禁，登记的是 `rerun` 子命令在合成运行库上的用例。

| 台账行 | 归属阶段 | 登记用例数 | 用例所在文件 |
| --- | --- | --- | --- |
| 绿色基线 | 0 | 17 | `tests/life/test_dayrun_inventory_regressions.py`、`tests/integration/test_dayrun_complete_days.py`、`tests/integration/test_agent_branch_coverage.py`、`tests/life/test_grounded_observation.py`、`tests/life/test_session_state.py`、`tests/footprints/test_verified_experiences.py`、`tests/life/test_travel_time.py`、`tests/life/test_day_plan.py`、`tests/life/test_work_review.py`、`tests/integration/test_life_agent_protocol.py`、`tests/life/test_engine_branch_coverage.py` |
| 同刻决策 | 4 | 64 | `tests/life/test_same_moment_decisions.py`、`tests/life/test_scenario.py`、`tests/life/test_engine_feedback.py`、`tests/life/test_engine_branch_coverage.py`、`tests/life/test_motivation.py`、`tests/integration/test_agent_branch_coverage.py`、`tests/life/test_export_integration.py`、`tests/integration/test_runtime.py`、`tests/life/test_wake_reasons.py`、`tests/integration/test_dayrun_complete_days.py`、`tests/life/test_location_sessions.py`、`tests/life/test_social_rounds.py`、`tests/life/test_resource_conflict_progress.py` |
| 候选时刻 | 2 | 13 | `tests/life/test_same_moment_decisions.py`、`tests/life/test_wake_reasons.py`、`tests/life/test_sleep_two_process.py`、`tests/life/test_activity_effects.py`、`tests/life/test_energy_feasibility.py`、`tests/life/test_engine_branch_coverage.py` |
| 唤醒词表 | 2 | 14 | `tests/footprints/test_event_fields.py`、`tests/life/test_wake_reasons.py`、`tests/footprints/test_quality.py`、`tests/evaluation/test_behavior_metrics.py` |
| 能量感知 | 2 | 16 | `tests/life/test_felt_states.py`、`tests/life/test_engine_branch_coverage.py`、`tests/life/test_semantics.py`、`tests/life/test_wake_reasons.py`、`tests/life/test_public_observation_contract.py`、`tests/life/test_energy_feasibility.py` |
| 能量不拦人 | 4 | 19 | `tests/life/test_energy_feasibility.py`、`tests/life/test_body.py`、`tests/life/test_dayrun_restaurants.py`、`tests/life/test_affordance_contract.py`、`tests/catalog/test_affordance_shadow.py`、`tests/life/test_parallel_admission.py` |
| 零点计划与提醒 | 5 | 42 | `tests/life/test_day_planning.py`、`tests/integration/test_runtime.py`、`tests/integration/test_host_branch_coverage.py`、`tests/integration/test_life_agent_protocol.py`、`tests/footprints/test_store.py`、`tests/evaluation/test_behavior_metrics.py`、`tests/footprints/test_quality.py`、`tests/delivery/test_windows.py`、`tests/evaluation/test_goal_metrics.py`、`tests/footprints/test_goal_export.py`、`tests/life/test_day_plan.py`、`tests/life/test_revision_contract.py`、`tests/integration/test_activity_schema.py`、`tests/integration/test_grounded_targets.py`、`tests/integration/test_env_lifecycle_coverage.py` |
| 五个动词与并行 | 4 | 78 | `tests/life/test_parallel_admission.py`、`tests/life/test_social_rounds.py`、`tests/test_start_activity_contract.py`、`tests/life/test_interaction_menu_invariants.py`、`tests/catalog/test_affordance_shadow.py`、`tests/evaluation/test_goal_metrics.py`、`tests/evaluation/test_adapters.py`、`tests/evaluation/test_behavior_metrics.py`、`tests/life/test_candidates.py`、`tests/life/test_activity_location_kinds.py`、`tests/life/test_domain_branch_coverage.py`、`tests/life/test_engine_branch_coverage.py`、`tests/life/test_news_feed.py`、`tests/life/test_parallel_acceptance.py`、`tests/life/test_opslog.py`、`tests/integration/test_parallel_rejection_contract.py`、`tests/integration/test_grounded_targets.py`、`tests/integration/test_agent_branch_coverage.py`、`tests/integration/test_storefront_tools.py`、`tests/integration/test_decision_context_protocol.py`、`tests/life/test_interaction_menu_runtime.py`、`tests/life/test_public_observation_contract.py`、`tests/life/test_work_contracts.py`、`tests/life/test_content_game.py` |
| 常驻提示 | 5 | 2 | `tests/integration/test_life_agent_protocol.py` |
| 会话、活动与记忆 | 4 | 61 | `tests/life/test_location_sessions.py`、`tests/integration/test_conclude_memories.py`、`tests/life/test_experience_sentences.py`、`tests/life/test_memory_evidence.py`、`tests/footprints/test_quality.py`、`tests/life/test_export_integration.py`、`tests/test_start_activity_contract.py`、`tests/integration/test_dayrun_complete_days.py`、`tests/life/test_progress_layer_projection.py`、`tests/life/test_narration.py`、`tests/life/test_engine_feedback.py`、`tests/life/test_engine_branch_coverage.py`、`tests/life/test_news_feed.py`、`tests/life/test_motivation.py`、`tests/life/test_parallel_admission.py` |
| 本机运行修复 | 7 | 32 | `tests/integration/test_runtime.py`、`tests/integration/test_run_manifest.py`、`tests/integration/test_host_branch_coverage.py`、`tests/life/test_sleep_two_process.py`、`tests/life/test_dayrun_restaurants.py`、`tests/life/test_wake_reasons.py`、`tests/life/test_progress_layer_projection.py`、`tests/life/test_narration.py`、`tests/life/test_location_sessions.py`、`tests/life/test_export_integration.py`、`tests/life/test_work_review.py`、`tests/life/test_news_feed.py`、`tests/life/test_grounded_observation.py`、`tests/tools/test_run_viewer.py` |
| 交付 | 3 | 4 | `tests/delivery/test_publication.py`、`tests/delivery/test_windows.py`、`tests/life/test_export_integration.py` |
| 无新增故障 | 7 | 23 | `tests/life/test_same_moment_decisions.py`、`tests/life/test_location_sessions.py`、`tests/life/test_day_planning.py`、`tests/life/test_energy_feasibility.py`、`tests/life/test_narration.py`、`tests/life/test_social_rounds.py`、`tests/integration/test_agent_branch_coverage.py`、`tests/life/test_grounded_observation.py` |
| 真实复验 | 8 | 3 | `tests/life/test_rerun_metrics.py` |
| 文档一致性 | 7 | 3 | `tests/test_documentation_gate.py`、`tests/test_time_and_energy_completeness.py` |
| 工程门禁 | 7 | 5 | `tests/test_style_gate.py`、`tests/test_documentation_gate.py`、`tests/test_coverage_gate.py`、`tests/tools/test_run_viewer.py` |

## 无新增故障

台账「无新增故障」行要求下列极端输入下都没有 `run_failed`。各情形与该行登记的用例如下；阶段 7 把此前只登记在其他行的能量为 0、对象全被挡、出发与到达同秒、同轮可见名称重复四种情形的用例补入本行。

| 台账情形 | 登记的用例 |
| --- | --- |
| 能量为 0 | `tests/life/test_energy_feasibility.py::test_zero_energy_observation_offers_every_action_without_energy_projection`、`::test_zero_energy_travel_and_work_are_accepted`、`::test_zero_energy_resume_is_accepted` |
| 对象全被挡 | `tests/integration/test_agent_branch_coverage.py::test_empty_interactive_layer_decision_continues_without_failure`；`tests/life/test_grounded_observation.py::test_real_observation_during_active_activity_keeps_decision_open`（工作、驾车、浏览进行中） |
| 提醒与活动结束同秒 | `tests/life/test_day_planning.py::test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end` |
| 出发与到达同秒 | `tests/life/test_location_sessions.py::test_departure_and_arrival_switch_sessions_and_end_road_activities`；`tests/life/test_same_moment_decisions.py::test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three` |
| 检查点恢复 | `tests/life/test_same_moment_decisions.py::test_same_moment_state_survives_reopen`、`::test_presentation_staging_survives_reopen`；`tests/life/test_location_sessions.py::test_sessions_and_pending_conclusion_survive_reopen` |
| 交谈对方结束 | `tests/life/test_same_moment_decisions.py::test_partner_ended_chat_redecides_same_second_with_planned_end`；`tests/life/test_social_rounds.py::test_chat_accepted_and_ended_by_the_partner_in_one_round_is_concluded_before_the_clock_moves`、`::test_late_acceptance_is_noted_and_refused_with_a_reason_and_next_step` |
| 反馈间隔为 60 秒 | `tests/life/test_same_moment_decisions.py::test_feedback_interval_60_bounds_same_second_rounds`；`tests/life/test_social_rounds.py::test_chat_call_and_wait_run_without_failure_at_any_feedback_interval` |
| 同轮可见名称重复 | `tests/life/test_location_sessions.py::test_same_named_activities_get_ordinals_instead_of_failing`、`::test_cross_day_duplicate_session_and_activity_names_get_ordinals` |
| 到达被拒 | `tests/life/test_location_sessions.py::test_arrival_denied_fails_travel_and_starts_stay_at_destination` |
| 途中结束出行 | `tests/life/test_location_sessions.py::test_ending_travel_midway_ends_road_activities_and_stays_at_origin` |
| 零点计划与决策同秒 | `tests/life/test_day_planning.py::test_midnight_settles_the_old_day_then_plans_then_judges_the_second`；运行终点恰为零点：`::test_run_ending_at_midnight_runs_no_plan_day_and_nothing_follows_run_ended` |

本行另登记阶段 2 消除的路线键白名单抛错点：`tests/life/test_narration.py::TestFieldBlacklist::test_route_whitelist_includes_effort_label`。实施台账「工程约束」列出的其余既有抛错点由阶段 0–4 各自消除，逐项见各阶段的完成判据。

## VF4 语义突变

判据来自[验收标准](../../VERIFICATION.md)「时间与能量特性门禁」与实施台账阶段 7 的完成判据「突变测试」：在干净提交的隔离工作树上，覆盖同刻上限、能量截断、唤醒闭集、提醒一次、名额释放、身份不进提示六项，每项突变都被测试杀死；存活突变须补测后以原补丁复验。

结果：29 个源码语义突变逐个执行，29 个 killed、0 个 survived，没有无效或无法判定的执行，因此没有需要补测复验的存活突变。

### 源码身份与隔离

| 项目 | 值 |
| --- | --- |
| 源码提交 | `44c8725`（阶段 0–7 均已合入；`ef13dc5` 之前的最后一个生产代码状态，二者生产代码相同） |
| 工作树 | 独立 git worktree，开始前在干净状态执行 `git reset --hard 44c8725` |
| 解释器 | 仓库虚拟环境的 Python，pytest 9.1.1，Windows 11；测试进程中 `worldmock` 与 `custom` 均从该工作树导入 |
| 突变方式 | 每次只改一处生产代码（`src/`、`custom/`），脚本做精确字符串替换，替换前断言原文在文件中恰好出现一次 |
| 还原方式 | 每次跑完立即 `git checkout -- <被改文件>`，再确认 `git status --porcelain` 为空；29 次还原后均为空 |
| 结束状态 | 工作树干净，HEAD 仍为 `44c8725`，没有提交任何突变 |

前置修正：该工作树建立时 `catalog/` 与 `corpora/` 以 CRLF 检出，加载目录时报 `CATALOG_CONTENT_IDENTITY_MISMATCH`；删除这两个目录后以 `git checkout -- catalog corpora` 重新检出为 LF，`git status` 仍为空，之后才运行基线与突变。

### 基线与测试集

广集为 `tests/life`、`tests/integration`、`tests/footprints`、`tests/evaluation` 与 `tests/test_start_activity_contract.py`，共 2912 个节点。在未突变源码上，首轮 11 个并行进程有 13 项因并行资源竞争失败（11 项 `MemoryError`，1 项语料清单校验，1 项 LabelKit 错误信息正则），这 13 项（按节点展开为 16 个）以 3 个并行进程复跑全部通过；5 项跳过与本特性无关（Windows 无符号链接权限 2 项，隔离的 LabelKit CLI 未安装 3 项）。突变执行统一用 6 个并行进程，未再出现资源类失败；下列六个测试集的并集（678 个节点）在未突变源码上 678 passed。

每个突变跑所属领域的测试集。节点先用 `--co` 收集，再按测试目录分组分到 6 个并行进程（同目录共享 conftest），每个进程执行 `python -m pytest -c pyproject.toml --rootdir=. -q -p no:cacheprovider -o addopts= --tb=line --junitxml=<输出> @<节点清单>`，失败计数取自各进程的 JUnit XML。

| 测试集 | 用于 | 文件 |
| --- | --- | --- |
| S1 同刻集 | M1-* | `tests/life/test_same_moment_decisions.py`、`tests/life/test_social_rounds.py`、`tests/life/test_engine_feedback.py`（64 个节点） |
| S2 能量集 | M2-* | `tests/life/test_energy_feasibility.py`、`test_body.py`、`test_shared_body.py`、`test_felt_states.py`、`test_public_observation_contract.py`、`test_dayrun_restaurants.py`、`test_semantics.py`、`test_wake_reasons.py`（均在 `tests/life/`，245 个节点） |
| S3 唤醒集 | M3-* | `tests/life/test_wake_reasons.py`、`tests/life/test_same_moment_decisions.py`、`tests/footprints/test_event_fields.py`、`tests/footprints/test_quality.py`、`tests/life/test_day_planning.py`（190 个节点） |
| S4 提醒集 | M4-* | `tests/life/test_day_planning.py`、`test_day_plan.py`、`test_revision_contract.py`、`test_wake_reasons.py`（均在 `tests/life/`，125 个节点） |
| S5 名额集 | M5-* | `tests/life/test_location_sessions.py`、`tests/life/test_parallel_admission.py`、`tests/life/test_same_moment_decisions.py`（126 个节点） |
| S6 身份集 | M6-* | `tests/integration/test_conclude_memories.py`、`tests/integration/test_verified_memory.py`、`tests/footprints/test_verified_experiences.py`、`tests/life/test_experience_sentences.py`、`test_memory_evidence.py`、`test_model_input_killpoints.py`、`test_progress_layer_projection.py`、`test_narration.py`（163 个节点） |

### 计数

| 领域 | 突变 | killed | survived |
| --- | --- | --- | --- |
| 同刻上限 | 5 | 5 | 0 |
| 能量截断 | 4 | 4 | 0 |
| 唤醒闭集 | 6 | 6 | 0 |
| 提醒一次 | 6 | 6 | 0 |
| 名额释放 | 4 | 4 | 0 |
| 身份不进提示 | 4 | 4 | 0 |
| **合计** | **29** | **29** | **0** |

### 逐项台账

行号为 `44c8725` 中被改代码的首行，`ef13dc5` 未改生产代码，行号不变；路径中的 `same_moment.py`、`body.py` 等均在 `src/worldmock/life/` 下（另注明者除外）。「杀死的测试」列出代表用例，标「未登记」者当时不在 `tests/time_and_energy_acceptance.py` 中；「覆盖的验收台账行」按该突变全部失败用例在登记表中的归属汇总。

| 编号 | 领域 | 生产位置 | 精确改动 / 语义 | 杀死的测试（代表） | 因果证据 | 覆盖的验收台账行 | 结果 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1-1 | 同刻上限 | `src/worldmock/life/same_moment.py:120`<br>same_moment.py:`next_stage` | `same_moment_rounds < outcome.limit` → `<=`<br>同刻上限差一：受限轮晚一轮开始，允许 limit+1 个开放轮 | `test_same_moment_decisions::test_feedback_interval_60_bounds_same_second_rounds`<br>`test_same_moment_decisions::test_restricted_rounds_open_start_then_wait_only`<br>`test_same_moment_decisions::test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three`<br>（共 11 项失败，全表见附录） | 阶段在上限处仍为 `open`；`assert 12 == (3 * 3)`，60 秒反馈间隔下同刻轮数越界 | 同刻决策、无新增故障 | **killed**<br>11/64 失败 |
| M1-2 | 同刻上限 | `src/worldmock/life/same_moment.py:59`<br>same_moment.py:`_LADDER` | `(OPEN, False): START_OR_RESUME` → `WAIT_ONLY`<br>受限阶梯跳过「开始或恢复」一级，直接只开放等待 | `test_same_moment_decisions::test_restricted_rounds_open_start_then_wait_only`<br>`test_same_moment_decisions::test_start_or_resume_schema_matches_engine_dispatch`<br>`test_same_moment_decisions::test_restricted_observation_carries_stage_and_scene_note`<br>（共 4 项失败，全表见附录） | 上限后阶段为 `wait_only`，期望 `start_or_resume` | 同刻决策、无新增故障 | **killed**<br>4/64 失败 |
| M1-3 | 同刻上限 | `src/worldmock/life/same_moment.py:139`<br>same_moment.py:`schedule_after_round` | `if not outcome.active or outcome.pending or …` → 删去 `not outcome.active or`<br>没有进行中活动的人按节律排期，时钟在其空闲时推进 | `test_same_moment_decisions::test_clock_advances_only_when_everyone_has_an_active_activity`<br>`test_same_moment_decisions::test_run_start_and_instant_action_redecide_in_same_second`<br>`test_same_moment_decisions::test_activity_end_boundary_offers_no_active_activity_and_redecides`<br>（共 28 项失败，全表见附录） | 空闲人物的下一次决策落到节律时刻，时钟推进断言失败；另有多项 `ProtocolError: Decision round cannot be started` | 五个动词与并行、会话、活动与记忆、同刻决策、无新增故障 | **killed**<br>28/64 失败 |
| M1-4 | 同刻上限 | `src/worldmock/life/same_moment.py:139`<br>same_moment.py:`schedule_after_round` | `if not outcome.active or outcome.pending or …` → 删去 `outcome.pending or`<br>有待收尾活动（另有进行中活动）时按节律排期，时钟在待收尾时推进 | `test_same_moment_decisions::test_conclusion_and_slot_branches_of_the_ladder` | `Failed: pending conclusion must redecide now`（阶梯单元断言直接调用 `schedule_after_round`） | 同刻决策 | **killed**<br>1/64 失败 |
| M1-5 | 同刻上限 | `src/worldmock/life/same_moment.py:247`<br>same_moment.py:`merge_message_readiness` | `same_moment_rounds >= limit` → `> limit`<br>已达同刻上限的收件人仍在同秒获得消息机会，同刻轮数越过上限 | `test_same_moment_decisions::test_recipient_at_limit_gets_message_at_next_boundary` | 收件人下一次决策在当前秒 1767542460，期望下一边界 1767543300 | 同刻决策 | **killed**<br>1/64 失败 |
| M2-1 | 能量截断 | `src/worldmock/life/body.py:227`<br>body.py:`_shared_entries` | `min(cost, body.energy_kcal)` → `cost`<br>共同身体结算去掉 0 截断：透支时能量为负，账本报错（NEGATIVE_BALANCE） | `test_energy_feasibility::test_derived_duration_beyond_energy_is_accepted_and_settles_at_zero`<br>`test_shared_body::test_shared_settlement_truncates_overdrawn_activity_sets_at_zero`（未登记） | `ResourceLedgerError: NEGATIVE_BALANCE: ('energy_kcal', 'body') balance -10.5625 is negative` | 能量不拦人 | **killed**<br>2/245 失败 |
| M2-2 | 能量截断 | `src/worldmock/life/body.py:225`<br>body.py:`_shared_entries` | `if cost > 0 and body.energy_kcal > 0:` → `if cost > 0:`<br>能量恰为 0 时仍生成 0 额消耗分录：零能量结算重新报错（AMOUNT_NOT_POSITIVE） | `test_energy_feasibility::test_zero_energy_travel_and_work_are_accepted`<br>`test_energy_feasibility::test_zero_energy_resume_is_accepted`<br>`test_energy_feasibility::test_zero_energy_wait_settles_after_instant_action`（未登记）<br>（共 5 项失败，全表见附录） | `ResourceLedgerError: AMOUNT_NOT_POSITIVE: drain on energy_kcal must be positive, got 0` | 能量不拦人 | **killed**<br>5/245 失败 |
| M2-3 | 能量截断 | `src/worldmock/life/felt.py:258`<br>felt.py:`appetite_level` | `energy_kcal < emergency_kcal` → `energy_kcal < emergency_kcal / 2`<br>「饿得难受」保底阈值由 400 kcal 移到 200 kcal | `test_felt_states::TestLowEnergyAppetite::test_below_emergency_threshold_is_at_least_unbearable`<br>`test_wake_reasons::test_low_energy_bands_match_the_decision_reference`<br>`test_energy_feasibility::test_hunger_band_crossing_wakes_during_activity`<br>（共 8 项失败，全表见附录） | 300 kcal 时分级不是「饿得难受」；进行中活动跨 400 kcal 不唤醒（`assert () == (1,)`） | 能量不拦人、能量感知 | **killed**<br>8/245 失败 |
| M2-4 | 能量截断 | `src/worldmock/life/felt.py:256`<br>felt.py:`appetite_level` | `energy_kcal < APPETITE_STARVING_KCAL` → `energy_kcal < APPETITE_STARVING_KCAL / 2`<br>「饿得发慌」阈值由 100 kcal 移到 50 kcal | `test_felt_states::TestLowEnergyAppetite::test_below_starving_constant_is_starving`<br>`test_public_observation_contract::test_low_energy_prompt_carries_levels_and_labels_but_no_body_numbers`<br>`test_energy_feasibility::test_hunger_band_crossing_wakes_during_activity`<br>（共 9 项失败，全表见附录） | 60、99.99 kcal 时仍为「饿得难受」而非「饿得发慌」；跨 100 kcal 不唤醒 | 能量不拦人、能量感知 | **killed**<br>9/245 失败 |
| M3-1 | 唤醒闭集 | `src/worldmock/life/rhythm.py:427`<br>rhythm.py:`_wake_snapshot` | `no_active_activity=not activities` → `no_active_activity=False`<br>结算边界（同步点）不再给 no_active_activity | `test_same_moment_decisions::test_activity_end_boundary_offers_no_active_activity_and_redecides`<br>`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once` | `{'no_active_activity', 'planned_end'} <= {'planned_end'}` 不成立 | 同刻决策、零点计划与提醒 | **killed**<br>2/190 失败 |
| M3-2 | 唤醒闭集 | `src/worldmock/life/rhythm.py:428`<br>rhythm.py:`_wake_snapshot` | `plan_reminder_due=reminders.reminder_due(agent, now)` → `plan_reminder_due=False`<br>结算边界（同步点）不再给 plan_reminder_due | `test_day_planning::test_reminders_wake_once_merge_and_fire_after_the_goal_progressed`<br>`test_day_planning::test_reminder_staged_without_a_rhythm_pass_is_the_next_boundary_and_ends_the_sleep`<br>`test_day_planning::test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end`<br>（共 5 项失败，全表见附录） | 提醒时刻不提供决策（`1767544200 == 1767543600` 不成立）；原因中缺 `plan_reminder_due` | 无新增故障、零点计划与提醒 | **killed**<br>5/190 失败 |
| M3-3 | 唤醒闭集 | `src/worldmock/life/schedule.py:59`<br>schedule.py:`SLEEP_ALLOWED_REASONS` | 集合追加 `WakeReason.NO_ACTIVE_ACTIVITY`<br>no_active_activity 可以唤醒睡眠中的人 | `test_same_moment_decisions::test_sleep_does_not_wake_for_no_active_activity`<br>`test_wake_reasons::test_sleep_allowed_reasons_add_only_plan_reminder_due`<br>`test_wake_reasons::test_sleep_accepts_only_existing_sleep_reasons`（未登记） | 睡眠快照给出 `['no_active_activity']`；`SLEEP_ALLOWED_REASONS` 集合断言失败 | 同刻决策、唤醒词表 | **killed**<br>3/190 失败 |
| M3-4 | 唤醒闭集 | `src/worldmock/life/same_moment.py:140`<br>same_moment.py:`schedule_after_round` | 同秒再决策的原因改为 `list(dict.fromkeys([*outcome.agent.pending_wake_reasons, *round_wake_reasons(…)]))`<br>上一轮原因带入同刻轮次 | `test_same_moment_decisions::test_wake_reasons_replaced_per_round_single_bedtime_reflection`<br>`test_same_moment_decisions::test_business_rejection_redecides_in_same_second_with_rejection_feedback`<br>`test_same_moment_decisions::test_run_start_and_instant_action_redecide_in_same_second`<br>（共 10 项失败，全表见附录） | 同刻轮原因带入 `run_start`、`planned_end`、`message_arrival` 等上一轮原因 | 同刻决策、零点计划与提醒 | **killed**<br>10/190 失败 |
| M3-5 | 唤醒闭集 | `src/worldmock/footprints/models.py:91`<br>footprints/models.py:`validate_decision_rhythm` | `if not isinstance(reason, str) or reason not in WAKE_REASON_NAMES:` → `if not isinstance(reason, str):`<br>写入 decision_started / body_settlement 时不再拒绝闭集外原因 | `test_event_fields::test_unknown_wake_reason_is_rejected_on_decision_started_write`<br>`test_event_fields::test_unknown_wake_reason_is_rejected_on_body_settlement_write`<br>`test_event_fields::test_wake_reasons_outside_the_frozen_vocabulary_are_rejected`（未登记） | `Failed: DID NOT RAISE ValueError`（写 body_settlement 一例在后续处以 TypeError 失败） | 唤醒词表 | **killed**<br>3/190 失败 |
| M3-6 | 唤醒闭集 | `src/worldmock/life/same_moment.py:247`<br>same_moment.py:`merge_message_readiness` | `if asleep or same_moment_rounds >= limit:` → `if same_moment_rounds >= limit:`<br>人物消息在同一秒唤醒睡眠中的收件人 | `test_same_moment_decisions::test_sleeping_recipient_is_not_woken_by_person_message` | 睡眠中的收件人 `next_decision_at` 变为当前秒（`1767542400 > 1767542400` 不成立） | 同刻决策 | **killed**<br>1/190 失败 |
| M4-1 | 提醒一次 | `src/worldmock/life/rhythm.py:131`<br>rhythm.py:`decide_decision_rhythm` | `agent = reminders.mark_reminded(read_new_messages(agent), …)` → `agent = read_new_messages(agent)`<br>到点提醒不写 reminded_at | `test_day_planning::test_reminders_wake_once_merge_and_fire_after_the_goal_progressed`<br>`test_day_planning::test_revision_keeps_replaces_and_cancels_reminders`<br>`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`<br>（共 5 项失败，全表见附录） | 提醒句缺失（`assert [] == [提醒句]`）；`reminded_at` 为 `None` | 无新增故障、零点计划与提醒 | **killed**<br>5/125 失败 |
| M4-2 | 提醒一次 | `src/worldmock/life/reminders.py:60`<br>reminders.py:`_due` | 删去 `goal.reminded_at is None and`<br>已触发的提醒在之后每个边界再次触发 | `test_day_planning::test_reminders_wake_once_merge_and_fire_after_the_goal_progressed`<br>`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`<br>`test_day_planning::test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end` | 已触发的提醒在后续边界再次成为原因（`'plan_reminder_due' not in [...]` 不成立），提醒句重复出现 | 无新增故障、零点计划与提醒 | **killed**<br>3/125 失败 |
| M4-3 | 提醒一次 | `src/worldmock/life/schedule.py:58`<br>schedule.py:`SLEEP_ALLOWED_REASONS` | 删去 `WakeReason.PLAN_REMINDER_DUE`<br>睡眠中过滤掉 plan_reminder_due | `test_wake_reasons::test_sleep_allowed_reasons_add_only_plan_reminder_due` | `SLEEP_ALLOWED_REASONS` 集合断言失败（引擎路径上提醒先结束睡眠，见说明 2） | 唤醒词表 | **killed**<br>1/125 失败 |
| M4-4 | 提醒一次 | `src/worldmock/life/schedule.py:408`<br>schedule.py:`revise_day_plan` | `if old is not None and old.remind_at == goal.remind_at:` → `if old is not None:`<br>修订改了提醒时刻时沿用旧条目：旧提醒不取消、新提醒不生效 | `test_day_planning::test_revision_keeps_replaces_and_cancels_reminders` | 只改提醒的「吃夜宵」仍是旧条目：`(start, None) == (start, start + 3600)` 不成立 | 零点计划与提醒 | **killed**<br>1/125 失败 |
| M4-5 | 提醒一次 | `src/worldmock/life/schedule.py:414`<br>schedule.py:`revise_day_plan` | 返回前把新目标里不再出现的未推进旧目标（连同提醒）追加回计划<br>被删除或替换的目标，旧提醒不取消 | `test_day_planning::test_revision_keeps_replaces_and_cancels_reminders`<br>`test_day_plan::test_revision_keeps_progressed_prefix_and_original_registration_for_unchanged_goals`（未登记）<br>`test_revision_contract::test_plan_day_registration_and_retry_after_rejection` | 修订后计划仍含被删除的「社交」目标及其 +7200 秒提醒；另两项因重复条目校验失败或修订被拒 | 零点计划与提醒 | **killed**<br>3/125 失败 |
| M4-6 | 提醒一次 | `src/worldmock/life/engine.py:1463`<br>engine.py:`_advance_one_activity` | `if reminders.reminder_due(agent, target):` → `if False:`<br>提醒到点不结束睡眠（睡眠中忽略提醒） | `test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`<br>`test_day_planning::test_reminder_staged_without_a_rhythm_pass_is_the_next_boundary_and_ends_the_sleep` | 睡眠未以 `plan_reminder` 结束，状态仍为 active | 零点计划与提醒 | **killed**<br>2/125 失败 |
| M5-1 | 名额释放 | `src/worldmock/life/activity_control.py:74`<br>activity_control.py:`occupies_slot` | `or is_pending_conclusion(activity)` → `or activity.status in CONCLUDABLE_TERMINAL_STATUSES`<br>已收尾的活动仍占名额（conclude_activity 不释放名额） | `test_location_sessions::test_two_slots_full_block_new_activities_until_conclusion`<br>`test_location_sessions::test_paused_activity_can_be_concluded_elsewhere`<br>`test_same_moment_decisions::test_rejected_resume_with_full_slots_opens_conclude_only_then_wait_only`<br>（共 13 项失败，全表见附录） | 收尾后开始新活动仍以 `SESSION_LIMIT_EXCEEDED` 拒绝；阶梯误入 `conclude_only` | 会话、活动与记忆、同刻决策、无新增故障、本机运行修复 | **killed**<br>13/126 失败 |
| M5-2 | 名额释放 | `src/worldmock/life/activity_control.py:74`<br>activity_control.py:`occupies_slot` | `{ACTIVE, PAUSED}` → `{ACTIVE}`<br>暂停中的活动不计名额 | `test_location_sessions::test_two_slots_full_block_new_activities_until_conclusion`<br>`test_location_sessions::test_both_paused_redecide_same_second_until_one_resumes`<br>`test_location_sessions::test_travel_with_full_slots_is_rejected_for_slots_before_the_departure_check`<br>（共 10 项失败，全表见附录） | 两项暂停时新开始被受理（`in_progress`，期望 `rejected`）；结束暂停活动以 `ACTION_REQUEST_INVALID` 被拒 | 会话、活动与记忆、同刻决策 | **killed**<br>10/126 失败 |
| M5-3 | 名额释放 | `src/worldmock/life/activity_control.py:74`<br>activity_control.py:`occupies_slot` | 删去 `or is_pending_conclusion(activity)`<br>待收尾的活动不计名额 | `test_location_sessions::test_just_ended_requires_conclusion_in_the_same_second`<br>`test_location_sessions::test_other_activity_keeps_running_while_the_ended_one_is_concluded`<br>`test_same_moment_decisions::test_conclude_pending_stage_accepts_only_concluding_every_pending_activity`<br>（共 23 项失败，全表见附录） | 给待收尾活动收尾以 `ACTION_REQUEST_INVALID` 被拒（`rejected`，期望 `completed`） | 五个动词与并行、会话、活动与记忆、同刻决策、无新增故障 | **killed**<br>23/126 失败 |
| M5-4 | 名额释放 | `src/worldmock/life/activity_control.py:34`<br>activity_control.py:`SLOT_LIMIT` | `SLOT_LIMIT = 2` → `SLOT_LIMIT = 3`<br>每人名额上限变成 3 | `test_location_sessions::test_two_slots_full_block_new_activities_until_conclusion`<br>`test_location_sessions::test_two_item_start_with_one_free_slot_says_only_one_slot_is_left`<br>`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude`<br>（共 7 项失败，全表见附录） | 两个名额占满时第三项被受理（`in_progress`，期望 `rejected`） | 会话、活动与记忆、同刻决策 | **killed**<br>7/126 失败 |
| M6-1 | 身份不进提示 | `src/worldmock/life/projection.py:335`<br>projection.py:`_experience_sentence` | 到达句末尾追加 `（{fact['activity_id']}）`<br>已核验经历句泄漏 activity_id | `test_experience_sentences::test_every_experience_kind_is_written_as_a_sentence_without_identities`<br>`test_experience_sentences::test_observation_experience_sentences_follow_the_private_window` | 经历句末多出 `（activity-…）`，与逐字期望不符 | 会话、活动与记忆 | **killed**<br>2/163 失败 |
| M6-2 | 身份不进提示 | `src/worldmock/life/activity_control.py:164`<br>activity_control.py:`visible_names` | 活动名追加 `f"#{item.activity_id}"`<br>活动可读名（进入 conclude/report/pause 工具枚举与进度层）泄漏 activity_id | `test_progress_layer_projection::test_control_names_resolve_to_one_own_activity_of_this_round`（未登记）<br>`test_memory_evidence::test_waking_round_lists_conclusion_then_reflection`<br>`test_progress_layer_projection::test_recall_reads_task_activity_and_session_without_identities`<br>（共 12 项失败，全表见附录） | 活动名带 `#activity-…`，名称与回看断言失败；`test_nothing_sent…` 在剧本模型处中断（IndexError，见说明 3） | 会话、活动与记忆、本机运行修复 | **killed**<br>12/163 失败 |
| M6-3 | 身份不进提示 | `custom/agents/life_agent.py:780`<br>custom/agents/life_agent.py:`_append_react_tool_result` | `if decision.action in REQUEST_MODELS:` → `if False:`<br>生活动作的工具返回给模型完整回执（含 action_id 等）而非登记确认 | `test_conclude_memories::test_nothing_sent_to_the_person_model_carries_an_internal_identity` | 模型可见文本检出内部身份（决策身份、`…:action:0001` 等），`assert [...] == []` | 会话、活动与记忆 | **killed**<br>1/163 失败 |
| M6-4 | 身份不进提示 | `custom/agents/life_agent.py:180`<br>custom/agents/life_agent.py:`_MEMORY_RESULT_FIELDS` | 元组前加 `"id"`<br>记忆查询返回条目身份 | `test_conclude_memories::test_nothing_sent_to_the_person_model_carries_an_internal_identity` | 模型可见文本检出记忆条目身份（`00000000…`），`assert [...] == []` | 会话、活动与记忆 | **killed**<br>1/163 失败 |

### 说明与加固

1. **M3-3、M4-3 是防御性成员，只能由直接断言检出。** 引擎中「睡着」意味着有进行中的睡眠活动，所以 `no_active_activity` 与 `is_asleep` 在同一快照里互斥；提醒到点时 `end_sleep_by_reminder` 先结束睡眠，结算边界上 `is_asleep` 已为假。因此 `SLEEP_ALLOWED_REASONS` 增减这两个成员不改变引擎行为，只能由直接断言集合与快照判定的用例检出（`test_sleep_allowed_reasons_add_only_plan_reminder_due`、`test_sleep_does_not_wake_for_no_active_activity`，均已登记）。引擎层的「睡眠中忽略提醒」另以 M4-6 检验，被两项引擎用例检出。
2. **M1-4 在 `44c8725` 只在单元层检出，`ef13dc5` 补引擎级用例。** 当时的检出用例 `test_conclusion_and_slot_branches_of_the_ladder` 直接调用引擎排期的唯一出口 `schedule_after_round`，断言有待收尾即同秒再决策；没有引擎级用例走到能区分该突变的路径（达到同刻上限后仍被业务拒绝，同时一项活动待收尾、另一项仍在进行）。`ef13dc5` 增 `tests/life/test_same_moment_decisions.py::test_pending_conclusion_beside_a_running_activity_holds_the_clock_past_the_limit`，登记在「同刻决策」行：同一请求开始等待与观看，等待先到点待收尾、观看仍在进行，做别的屡次被拒到同刻上限后人物仍在当秒就绪、时钟不推进，收尾后才按决策节律推进。M1-4 现由这条引擎级用例检出。
3. **M6-2 在 `44c8725` 的专项身份扫描没有走到断言，`ef13dc5` 让它走到。** 当时 `tests/integration/test_conclude_memories.py::test_nothing_sent_to_the_person_model_carries_an_internal_identity` 在剧本模型处以 `IndexError` 提前中断（名称改变使剧本对话偏离），该突变由名称逐字断言、回看与收尾用例检出。`ef13dc5` 改为按本轮提供给模型的 `conclude_activity` 枚举中的名称构造收尾调用，名称带出内部身份时扫描照常走完、在身份断言处失败：M6-2 现由身份扫描的断言本身检出。M6-3、M6-4 在 `44c8725` 已由该扫描的身份断言检出。
4. 突变执行本身没有新增或修改测试；以上两处加固随 `ef13dc5` 提交，不改生产代码。

### 附录：各突变的全部失败用例

- **M1-1**（同刻集，64 个节点，11 项失败，还原后工作树干净：是）：`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[conclude]`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[resume]`、`test_same_moment_decisions::test_conclusion_and_slot_branches_of_the_ladder`、`test_same_moment_decisions::test_feedback_interval_60_bounds_same_second_rounds`、`test_same_moment_decisions::test_other_request_in_resume_or_conclude_narrows_to_conclude_only_then_wait_only`、`test_same_moment_decisions::test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three`、`test_same_moment_decisions::test_rejected_resume_with_full_slots_opens_conclude_only_then_wait_only`、`test_same_moment_decisions::test_restricted_observation_carries_stage_and_scene_note`、`test_same_moment_decisions::test_restricted_rounds_open_start_then_wait_only`、`test_same_moment_decisions::test_same_moment_state_survives_reopen`、`test_same_moment_decisions::test_start_or_resume_schema_matches_engine_dispatch`
- **M1-2**（同刻集，64 个节点，4 项失败，还原后工作树干净：是）：`test_same_moment_decisions::test_restricted_observation_carries_stage_and_scene_note`、`test_same_moment_decisions::test_restricted_rounds_open_start_then_wait_only`、`test_same_moment_decisions::test_same_moment_state_survives_reopen`、`test_same_moment_decisions::test_start_or_resume_schema_matches_engine_dispatch`
- **M1-3**（同刻集，64 个节点，28 项失败，还原后工作树干净：是）：`test_engine_feedback::test_pause_report_resume_and_conclude_name_the_activity`、`test_same_moment_decisions::test_activity_end_boundary_offers_no_active_activity_and_redecides`、`test_same_moment_decisions::test_awake_recipient_gets_same_second_opportunity`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[conclude]`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[resume]`、`test_same_moment_decisions::test_browse_results_stay_pinned_in_later_same_second_rounds`、`test_same_moment_decisions::test_clock_advances_only_when_everyone_has_an_active_activity`、`test_same_moment_decisions::test_conclusion_only_round_does_not_use_up_message_opportunity`、`test_same_moment_decisions::test_contact_reply_arrival_filtered_during_sleep`、`test_same_moment_decisions::test_feedback_interval_60_bounds_same_second_rounds`、`test_same_moment_decisions::test_just_now_writes_the_previous_own_request_result_and_is_empty_in_first_rounds`、`test_same_moment_decisions::test_other_request_in_resume_or_conclude_narrows_to_conclude_only_then_wait_only`、`test_same_moment_decisions::test_out_of_bounds_request_is_rejected_and_counted`、`test_same_moment_decisions::test_partner_ended_chat_redecides_same_second_with_planned_end`、`test_same_moment_decisions::test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three`、`test_same_moment_decisions::test_recipient_at_limit_gets_message_at_next_boundary`、`test_same_moment_decisions::test_recipient_in_same_round_is_not_overwritten_by_its_own_success`、`test_same_moment_decisions::test_rejected_paired_start_leaves_no_chat_invite`、`test_same_moment_decisions::test_rejected_resume_with_full_slots_opens_conclude_only_then_wait_only`、`test_same_moment_decisions::test_restricted_round_does_not_use_up_message_opportunity`、`test_same_moment_decisions::test_restricted_rounds_open_start_then_wait_only`、`test_same_moment_decisions::test_run_start_and_instant_action_redecide_in_same_second`、`test_same_moment_decisions::test_same_moment_state_survives_reopen`、`test_same_moment_decisions::test_sleeping_recipient_is_not_woken_by_person_message`、`test_same_moment_decisions::test_start_or_resume_accepts_start_and_resume_but_not_other_reports`、`test_same_moment_decisions::test_start_or_resume_schema_matches_engine_dispatch`、`test_same_moment_decisions::test_wake_reasons_replaced_per_round_single_bedtime_reflection`、`test_social_rounds::test_none_policy_delivers_without_reply_and_template_policy_posts_a_placeholder`
- **M1-4**（同刻集，64 个节点，1 项失败，还原后工作树干净：是）：`test_same_moment_decisions::test_conclusion_and_slot_branches_of_the_ladder`
- **M1-5**（同刻集，64 个节点，1 项失败，还原后工作树干净：是）：`test_same_moment_decisions::test_recipient_at_limit_gets_message_at_next_boundary`
- **M2-1**（能量集，245 个节点，2 项失败，还原后工作树干净：是）：`test_energy_feasibility::test_derived_duration_beyond_energy_is_accepted_and_settles_at_zero`、`test_shared_body::test_shared_settlement_truncates_overdrawn_activity_sets_at_zero`
- **M2-2**（能量集，245 个节点，5 项失败，还原后工作树干净：是）：`test_energy_feasibility::test_checkpoint_with_overdrawn_activity_restores`、`test_energy_feasibility::test_zero_energy_resume_is_accepted`、`test_energy_feasibility::test_zero_energy_travel_and_work_are_accepted`、`test_energy_feasibility::test_zero_energy_wait_settles_after_instant_action`、`test_shared_body::test_shared_settlement_and_activity_progress_reject_invalid_intervals`
- **M2-3**（能量集，245 个节点，8 项失败，还原后工作树干净：是）：`test_energy_feasibility::test_hunger_band_crossing_wakes_during_activity[call-405.0-400.0-\u5f88\u997f-\u997f\u5f97\u96be\u53d7]`、`test_energy_feasibility::test_hunger_band_crossing_wakes_during_activity[watch-405.0-400.0-\u5f88\u997f-\u997f\u5f97\u96be\u53d7]`、`test_energy_feasibility::test_hunger_band_crossing_wakes_during_chat`、`test_felt_states::TestLowEnergyAppetite::test_below_emergency_threshold_is_at_least_unbearable[8.0]`、`test_felt_states::TestLowEnergyAppetite::test_emergency_floor_ignores_meal_and_circadian_terms`、`test_felt_states::TestLowEnergyAppetite::test_summary_applies_the_same_rules`、`test_felt_states::TestLowEnergyAppetite::test_threshold_edges_are_half_open`、`test_wake_reasons::test_low_energy_bands_match_the_decision_reference[300.0-\u997f\u5f97\u96be\u53d7]`
- **M2-4**（能量集，245 个节点，9 项失败，还原后工作树干净：是）：`test_energy_feasibility::test_hunger_band_crossing_wakes_during_activity[call-104.0-100.0-\u997f\u5f97\u96be\u53d7-\u997f\u5f97\u53d1\u614c]`、`test_energy_feasibility::test_hunger_band_crossing_wakes_during_activity[watch-104.0-100.0-\u997f\u5f97\u96be\u53d7-\u997f\u5f97\u53d1\u614c]`、`test_felt_states::TestLowEnergyAppetite::test_below_starving_constant_is_starving[50.0-20.0]`、`test_felt_states::TestLowEnergyAppetite::test_below_starving_constant_is_starving[50.0-8.0]`、`test_felt_states::TestLowEnergyAppetite::test_below_starving_constant_is_starving[99.99-20.0]`、`test_felt_states::TestLowEnergyAppetite::test_below_starving_constant_is_starving[99.99-8.0]`、`test_felt_states::TestLowEnergyAppetite::test_summary_applies_the_same_rules`、`test_public_observation_contract::test_low_energy_prompt_carries_levels_and_labels_but_no_body_numbers`、`test_wake_reasons::test_low_energy_bands_match_the_decision_reference[60.0-\u997f\u5f97\u53d1\u614c]`
- **M3-1**（唤醒集，190 个节点，2 项失败，还原后工作树干净：是）：`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`、`test_same_moment_decisions::test_activity_end_boundary_offers_no_active_activity_and_redecides`
- **M3-2**（唤醒集，190 个节点，5 项失败，还原后工作树干净：是）：`test_day_planning::test_reminder_staged_without_a_rhythm_pass_is_the_next_boundary_and_ends_the_sleep`、`test_day_planning::test_reminders_wake_once_merge_and_fire_after_the_goal_progressed`、`test_day_planning::test_revision_keeps_replaces_and_cancels_reminders`、`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`、`test_day_planning::test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end`
- **M3-3**（唤醒集，190 个节点，3 项失败，还原后工作树干净：是）：`test_same_moment_decisions::test_sleep_does_not_wake_for_no_active_activity`、`test_wake_reasons::test_sleep_accepts_only_existing_sleep_reasons`、`test_wake_reasons::test_sleep_allowed_reasons_add_only_plan_reminder_due`
- **M3-4**（唤醒集，190 个节点，10 项失败，还原后工作树干净：是）：`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`、`test_same_moment_decisions::test_activity_end_boundary_offers_no_active_activity_and_redecides`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[conclude]`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[resume]`、`test_same_moment_decisions::test_business_rejection_redecides_in_same_second_with_rejection_feedback`、`test_same_moment_decisions::test_other_request_in_resume_or_conclude_narrows_to_conclude_only_then_wait_only`、`test_same_moment_decisions::test_rejection_with_active_activity_retries_until_limit_then_follows_rhythm`、`test_same_moment_decisions::test_replay_round_counts_toward_same_moment_rounds`、`test_same_moment_decisions::test_run_start_and_instant_action_redecide_in_same_second`、`test_same_moment_decisions::test_wake_reasons_replaced_per_round_single_bedtime_reflection`
- **M3-5**（唤醒集，190 个节点，3 项失败，还原后工作树干净：是）：`test_event_fields::test_unknown_wake_reason_is_rejected_on_body_settlement_write`、`test_event_fields::test_unknown_wake_reason_is_rejected_on_decision_started_write`、`test_event_fields::test_wake_reasons_outside_the_frozen_vocabulary_are_rejected[reasons0]`
- **M3-6**（唤醒集，190 个节点，1 项失败，还原后工作树干净：是）：`test_same_moment_decisions::test_sleeping_recipient_is_not_woken_by_person_message`
- **M4-1**（提醒集，125 个节点，5 项失败，还原后工作树干净：是）：`test_day_planning::test_reminder_staged_without_a_rhythm_pass_is_the_next_boundary_and_ends_the_sleep`、`test_day_planning::test_reminders_wake_once_merge_and_fire_after_the_goal_progressed`、`test_day_planning::test_revision_keeps_replaces_and_cancels_reminders`、`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`、`test_day_planning::test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end`
- **M4-2**（提醒集，125 个节点，3 项失败，还原后工作树干净：是）：`test_day_planning::test_reminders_wake_once_merge_and_fire_after_the_goal_progressed`、`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`、`test_day_planning::test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end`
- **M4-3**（提醒集，125 个节点，1 项失败，还原后工作树干净：是）：`test_wake_reasons::test_sleep_allowed_reasons_add_only_plan_reminder_due`
- **M4-4**（提醒集，125 个节点，1 项失败，还原后工作树干净：是）：`test_day_planning::test_revision_keeps_replaces_and_cancels_reminders`
- **M4-5**（提醒集，125 个节点，3 项失败，还原后工作树干净：是）：`test_day_plan::test_revision_keeps_progressed_prefix_and_original_registration_for_unchanged_goals`、`test_day_planning::test_revision_keeps_replaces_and_cancels_reminders`、`test_revision_contract::test_plan_day_registration_and_retry_after_rejection`
- **M4-6**（提醒集，125 个节点，2 项失败，还原后工作树干净：是）：`test_day_planning::test_reminder_staged_without_a_rhythm_pass_is_the_next_boundary_and_ends_the_sleep`、`test_day_planning::test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once`
- **M5-1**（名额集，126 个节点，13 项失败，还原后工作树干净：是）：`test_location_sessions::test_experiment_end_export_matches_the_final_checkpoint_for_every_truncated_activity[concluded]`、`test_location_sessions::test_paused_activity_can_be_concluded_elsewhere`、`test_location_sessions::test_paused_activity_resumes_only_at_its_start_location`、`test_location_sessions::test_two_slots_full_block_new_activities_until_conclusion`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[conclude]`、`test_same_moment_decisions::test_clock_advances_only_when_everyone_has_an_active_activity`、`test_same_moment_decisions::test_feedback_interval_60_bounds_same_second_rounds`、`test_same_moment_decisions::test_other_request_in_resume_or_conclude_narrows_to_conclude_only_then_wait_only`、`test_same_moment_decisions::test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three`、`test_same_moment_decisions::test_rejected_paired_start_leaves_no_chat_invite`、`test_same_moment_decisions::test_rejected_resume_with_full_slots_opens_conclude_only_then_wait_only`、`test_same_moment_decisions::test_restricted_conclusions_endings_and_waits_pass_the_stage_check`、`test_same_moment_decisions::test_start_or_resume_accepts_start_and_resume_but_not_other_reports`
- **M5-2**（名额集，126 个节点，10 项失败，还原后工作树干净：是）：`test_location_sessions::test_both_paused_redecide_same_second_until_one_resumes`、`test_location_sessions::test_paused_activity_can_be_concluded_elsewhere`、`test_location_sessions::test_travel_with_full_slots_is_rejected_for_slots_before_the_departure_check`、`test_location_sessions::test_two_item_start_with_one_free_slot_says_only_one_slot_is_left`、`test_location_sessions::test_two_slots_full_block_new_activities_until_conclusion`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[conclude]`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[resume]`、`test_same_moment_decisions::test_other_request_in_resume_or_conclude_narrows_to_conclude_only_then_wait_only`、`test_same_moment_decisions::test_rejected_resume_with_full_slots_opens_conclude_only_then_wait_only`、`test_same_moment_decisions::test_restricted_conclusions_endings_and_waits_pass_the_stage_check`
- **M5-3**（名额集，126 个节点，23 项失败，还原后工作树干净：是）：`test_location_sessions::test_arrival_denied_fails_travel_and_starts_stay_at_destination`、`test_location_sessions::test_ending_travel_midway_ends_road_activities_and_stays_at_origin`、`test_location_sessions::test_just_ended_requires_conclusion_in_the_same_second`、`test_location_sessions::test_other_activity_keeps_running_while_the_ended_one_is_concluded`、`test_location_sessions::test_paused_activity_can_be_concluded_elsewhere`、`test_location_sessions::test_paused_activity_resumes_only_at_its_start_location`、`test_location_sessions::test_recall_of_content_started_with_a_walk_names_the_road`、`test_location_sessions::test_sessions_and_pending_conclusion_survive_reopen`、`test_location_sessions::test_waking_with_the_sleep_awaiting_conclusion_offers_its_conclusion`、`test_parallel_admission::test_walking_accepts_messages_and_remote_orders_delivered_to_the_origin`、`test_same_moment_decisions::test_activity_end_boundary_offers_no_active_activity_and_redecides`、`test_same_moment_decisions::test_browse_results_stay_pinned_in_later_same_second_rounds`、`test_same_moment_decisions::test_clock_advances_only_when_everyone_has_an_active_activity`、`test_same_moment_decisions::test_conclude_pending_stage_accepts_only_concluding_every_pending_activity`、`test_same_moment_decisions::test_conclusion_only_round_does_not_use_up_message_opportunity`、`test_same_moment_decisions::test_feedback_interval_60_bounds_same_second_rounds`、`test_same_moment_decisions::test_partner_ended_at_the_limit_restarts_the_ladder_from_conclusion`、`test_same_moment_decisions::test_partner_ended_chat_redecides_same_second_with_planned_end`、`test_same_moment_decisions::test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three`、`test_same_moment_decisions::test_rejected_resume_with_full_slots_opens_conclude_only_then_wait_only`、`test_same_moment_decisions::test_restricted_conclusions_endings_and_waits_pass_the_stage_check`、`test_same_moment_decisions::test_start_or_resume_accepts_start_and_resume_but_not_other_reports`、`test_same_moment_decisions::test_wake_reasons_replaced_per_round_single_bedtime_reflection`
- **M5-4**（名额集，126 个节点，7 项失败，还原后工作树干净：是）：`test_location_sessions::test_travel_with_full_slots_is_rejected_for_slots_before_the_departure_check`、`test_location_sessions::test_two_item_start_with_one_free_slot_says_only_one_slot_is_left`、`test_location_sessions::test_two_slots_full_block_new_activities_until_conclusion`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[conclude]`、`test_same_moment_decisions::test_both_slots_paused_open_resume_or_conclude[resume]`、`test_same_moment_decisions::test_other_request_in_resume_or_conclude_narrows_to_conclude_only_then_wait_only`、`test_same_moment_decisions::test_rejected_resume_with_full_slots_opens_conclude_only_then_wait_only`
- **M6-1**（身份集，163 个节点，2 项失败，还原后工作树干净：是）：`test_experience_sentences::test_every_experience_kind_is_written_as_a_sentence_without_identities`、`test_experience_sentences::test_observation_experience_sentences_follow_the_private_window`
- **M6-2**（身份集，163 个节点，12 项失败，还原后工作树干净：是）：`test_conclude_memories::test_conclusion_rejected_at_settlement_keeps_its_memories`、`test_conclude_memories::test_conclusion_sources_are_registered_by_the_system`、`test_conclude_memories::test_nothing_sent_to_the_person_model_carries_an_internal_identity`、`test_memory_evidence::test_rejected_conclusion_keeps_its_memories_as_self_report`、`test_memory_evidence::test_waking_round_lists_conclusion_then_reflection`、`test_progress_layer_projection::test_conclude_marks_named_just_ended_activities_and_keeps_their_status`、`test_progress_layer_projection::test_control_names_resolve_to_one_own_activity_of_this_round`、`test_progress_layer_projection::test_progress_rows_follow_pause_resume_end_and_conclusion`、`test_progress_layer_projection::test_recall_reads_task_activity_and_session_without_identities`、`test_progress_layer_projection::test_report_status_maps_each_activity_to_its_own_note_and_goal_note`、`test_progress_layer_projection::test_sessions_are_ordered_by_start_and_group_activities_under_their_latest_session`、`test_progress_layer_projection::test_work_objective_progress_grows_with_settlement`
- **M6-3**（身份集，163 个节点，1 项失败，还原后工作树干净：是）：`test_conclude_memories::test_nothing_sent_to_the_person_model_carries_an_internal_identity`
- **M6-4**（身份集，163 个节点，1 项失败，还原后工作树干净：是）：`test_conclude_memories::test_nothing_sent_to_the_person_model_carries_an_internal_identity`

### 复现

突变目录、执行与汇总脚本为临时脚本，不入库。单个突变的等价手工步骤：在干净工作树上 `git reset --hard 44c8725`；按「逐项台账」的精确改动修改一处生产代码；用仓库虚拟环境运行 `python -m pytest -c pyproject.toml --rootdir=. -q -p no:cacheprovider -o addopts= <该领域测试集>`；再 `git checkout -- <被改文件>` 还原，并确认 `git status --porcelain` 为空。

## 文档一致性

依据规格「冻结条款修订」、验收台账「文档一致性」行与实施台账「文档同步作业」。核对在 `44c8725` 上进行，改动随 `1dab733` 提交：按实施台账的文档节分给 10 个并行核对者，逐作业对照已提交文档、冻结规格与最终代码（只读 grep 代码、测试与锁定 AS2 源码），只改分到的文档；另设一个只读交叉核对，全文扫描 `plan_day`、日首计划、`finish`、`refs` 与工具个数；最后逐处复核全部改动并与代码对照。

### 冻结条款修订与文档同步作业

- 「冻结条款修订」30 行括号内的作业编号全部能在实施台账中找到。台账作业中没有被任何一行引用的只有 AI1、DR1、DR12、LR1，它们是文件头与版本表作业，由表后说明覆盖。各行所列作业全部落地，逐行对得上；行末句原先缺的只有修订 18 与修订 20 两处，已随 AI6、AI9 补上。
- 实施台账共 301 项作业：【冻】213、【同】72、【冻→后】2、【条·#22】1、【条·#23】2、【条·#25】4、【后】6、【后·最后】1。阶段 7 核对时，除【后】作业以及 E4-21、E5-19 的【后】部分外全部落地；【后】作业由阶段 9 落地（见「实现后文档（阶段 9）」），IS1 最后提交。
- 各阶段登记的文档符号由完整性守卫判定：阶段 7 核对时只读求值，删除符号 22 条全无命中，新增符号 84 条全部命中；现在以守卫用例判定，见「全量门禁与覆盖率」。

阶段 7 核对时各文档的结论：

| 文档 | 作业 | 结论 |
| --- | --- | --- |
| `docs/CONTRACTS.md` | C1–C35、C37–C39 | 已落地；C14、C18 补全，C19 按代码改措辞 |
| `docs/dev/SPEC-life-runtime.md` | LR1–LR32 | 已落地；LR31 的证据改引现有用例 |
| `docs/dev/SPEC-dayrun-behavior.md` | DR1–DR12 | 已落地；DR9 同节一句按代码改 |
| `docs/dev/SPEC-agent-interaction.md` | AI1–AI13、AI15–AI19 | 已落地；AI6、AI9 补全，AI4、AI7、AI10、AI12 按代码改行号与措辞 |
| `docs/dev/SPEC-day-plan-goals.md` | DP1–DP9 | 已落地；DP6 补提醒时刻 |
| `docs/dev/SPEC-life-realism.md` | RL1–RL3、RL5–RL8 | 已落地；RL6 按 #23 推荐方案不改 |
| `docs/dev/SPEC-decision-context.md` | DC1–DC9 | 已落地，无改动 |
| `docs/dev/SPEC-interaction-menu.md` | IM1–IM9 | 已落地；IM5 补当日目标名 |
| `docs/dev/SPEC-map-and-storefront.md` | MS1–MS4 | 已落地，无改动（文件头状态在阶段 9 改为已实现） |
| `docs/dev/SPEC-as2-integration.md` | AS1–AS9 | 已落地；AS2 伪代码按锁定 AS2 对齐，AS7 证据换成现有用例 |
| `docs/dev/SPEC-labelkit-delivery.md` | LK2、LK3 | 已落地；LK2 证据换成现有用例 |
| `docs/dev/SPEC-category-contract.md` | CC1 | 核对无需改 |
| `docs/design/04-simulation-engine.md` | E4-1–E4-24 | E4-1–E4-20、E4-22、E4-23 已落地（E4-22 按代码改）；E4-21 冻结部分在；E4-24【后】 |
| `docs/design/05-recording-engine.md` | E5-1–E5-20 | E5-1–E5-18 已落地（E5-16 按代码改）；E5-19 冻结部分在；E5-20【后】 |
| `docs/design/06-delivery.md` | E6-1–E6-5 | 已落地；E6-3 所在节的示意代码补可见性过滤 |
| `docs/design/07-evaluation.md` | E7-1–E7-5 | 已落地，无改动 |
| `docs/design/08-configuration.md` | E8-1–E8-7 | 已落地；E8-7 目录只升次版本（4.0→4.1），按作业不改 |
| `docs/design/09-io-formats.md` | E9-1–E9-5 | 已落地；E9-3 名额说明按代码改 |
| `docs/design/00-overview.md`、`01-architecture.md` | E0-2–E0-7；E1-1、E1-3–E1-6、E1-8–E1-10 | 已落地，无改动 |
| `docs/design/02-exchange-engine.md`、`03-resource-engine.md` | E2-1–E2-6；E3-1–E3-3 | 已落地，无改动（E3-3 目录为 4.1） |
| `docs/design/README.md`、`SUMMARY.md` | ER1–ER3；ES1–ES3 | 已落地，无改动 |
| `docs/EXPERIMENT.md` | EX1–EX3、EX5–EX7、EX8a、EX8b、EX9–EX13 | 已落地，无改动 |
| `docs/VERIFICATION.md` | VF1–VF7、VF9–VF14 | 已落地，无改动（VF6 按 #23 不改；VF4 文本在）；VF12【后】 |
| `docs/ARCHITECTURE.md` | AR2–AR7 | 已落地，无改动 |
| `docs/manual/*`、根 `README.md` | MA1–MA13、RM1–RM2 | 已落地；MA8、MA13 按代码改 |
| `docs/INDEX.md` | IX1–IX6 | IX1–IX4、IX6 已落地；IX5【后】 |
| 阶段 9 文档 | AN1、CB1、IS1 | 【后】，未动 |

按最终代码补正的作业（行号为 `1dab733` 中的文件）：

| 作业 | 位置 | 补正 |
| --- | --- | --- |
| C14 | `docs/CONTRACTS.md`:202 | 注明 `max_same_moment_decisions` 取代 `max_consecutive_rejections`（合同本无此键） |
| C18 | `docs/CONTRACTS.md`:296 | 「表中的人物工具」改为「表中的八个人物工具」 |
| C19 | `docs/CONTRACTS.md`:236 | `ACTIVITY_DURATION_INVALID` 的开工原因改为举例；剩余合同不足时原因句不同（`work.py`） |
| LR31 | `docs/dev/SPEC-life-runtime.md`:980 | 原证据用例 `test_consume_rejects_inventory_held_at_another_location` 已不存在，改引两条现有用例 |
| DR9 | `docs/dev/SPEC-dayrun-behavior.md`:199-200 | 阈值巩固改为由专用记忆运行时自行判定（`life_memory._consolidation_due`），阈值沿用 AS2，就寝反思不计 |
| AI4 | `docs/dev/SPEC-agent-interaction.md`:119 | `models.py:23` 改为 `:24` |
| AI6 | `docs/dev/SPEC-agent-interaction.md`:175、:187、:204-205 | 补修订 18 末句：有待收尾活动时做别的（含只结束别的活动）以 `ACTIVITY_CONCLUSION_REQUIRED` 拒绝。`memory_read` 已删，查询结果不带条目身份。`submit_work` 在有可见未付款任务时开放 |
| AI7 | `docs/dev/SPEC-agent-interaction.md`:261 | 「会话时长」改为「活动时长」 |
| AI9 | `docs/dev/SPEC-agent-interaction.md`:332、:340、:357 | 补修订 20：播报自开始、恢复或上次播报起计；拒绝公开 `reason` 与 `next_step`；行号刷新 |
| AI10 | `docs/dev/SPEC-agent-interaction.md`:366-387 | `work.py` 行号刷新；公开结果为 `reason`、`next_step`，`unmet`、`suggested` 只在私有结果 |
| AI12 | `docs/dev/SPEC-agent-interaction.md`:426、:438、:442-443 | 补 `just_now`；阶段说法改为代码取值；`alarms` 补当日目标提醒句 |
| DP6 | `docs/dev/SPEC-day-plan-goals.md`:100 | 场景叙述补「提醒时刻」（`narration._plan_sentence`） |
| IM5 | `docs/dev/SPEC-interaction-menu.md`:106 | `recall_task_summary` 补「当日目标名」 |
| AS2 | `docs/dev/SPEC-as2-integration.md`:88-94 | 伪代码对齐锁定 AS2 `Society.step`（`society.py:224-240`，已逐行核对）：执行一轮，再查询，无人就绪才推进，每步一个检查点 |
| AS7 | `docs/dev/SPEC-as2-integration.md`:377 | 证据列的「随阶段 3 落地」占位换成两条现有用例 |
| LK2 | `docs/dev/SPEC-labelkit-delivery.md`:228 | 证据列占位换成 `test_conclude_activity_with_memories_does_not_leak_memory_text` |
| E4-22 | `docs/design/04-simulation-engine.md`:657 | 改为交互对象不剪裁；工具按数字人交互规格「稳定工具面」的可见条件开放 |
| E5-16 | `docs/design/05-recording-engine.md`:755 | 保存的目标字段由请求参数名 `remind_time` 改为 `remind_at`、`reminded_at` |
| E9-3 | `docs/design/09-io-formats.md`:168 | 待收尾限定为 `completed`、`failed`、`cancelled` 且未收尾；`truncated` 不收尾 |
| MA8 | `docs/manual/rules.md`:203 | `habit_members` 改为按名精确归并，每个动作名至多归一个家族 |
| MA13 | `docs/manual/resources.md`:348-350 | 接触次数只在第一次看与从头再看时加一（`rules.py`） |

E7-4：设计文档写的终态含「失败」，与作业原文不同；以代码 `public_evidence.py` 为准，不改。核对者另有两处超出所列类别、但有代码或规格依据的修正，复核后保留：`submit_work` 可见性、记忆工具、阶段说法、`just_now`、`alarms`、`reason` 与 `next_step` 的表述；用户手册 `rules.md` 删去已不存在的 `relief.channel` 取值 `intrinsic`。

### 现行条款中的过时表述

`1dab733` 清除的过时表述（行号为 `1dab733` 中的文件）：

| 位置（改后） | 原表述 → 改为 |
| --- | --- |
| `docs/CONTRACTS.md`:61 | `report_status` 恢复措辞 → 点名暂停的活动即恢复它（只在其开始地点） |
| `docs/CONTRACTS.md`:609 | 「供 LifeAgent 冻结继续、暂停、取消和变速的目标身份」（旧工具面）→ 人物按可读活动名播报、暂停、恢复与结束，经私有 `activity_bindings` 解析 |
| `docs/dev/SPEC-life-runtime.md`:141、:651 | 按人物「已提交的计划」续行 → 按人物已开始的活动续行 |
| `docs/dev/SPEC-life-runtime.md`:668 | 「退出保留已完成部分」（旧会话退出）→「提前结束保留已完成部分」 |
| `docs/dev/SPEC-agent-interaction.md`:207 | 「`plan_day` 与 `revise_day_plan` 保留为独立工具」段首是现在时 → 首句注明「已被取代，见段末」 |
| `docs/dev/SPEC-agent-interaction.md`:556 | 「业务拒绝冷却后继续」→ 业务拒绝后同一秒再决策（同刻上限） |
| `docs/dev/SPEC-day-plan-goals.md`:95 | 「提交清单」→「零点计划」 |
| `docs/dev/SPEC-life-realism.md`:172、:350 | 人物自己表达或生成当日计划 → 由零点计划生成，人物可修订 |
| `docs/dev/SPEC-life-realism.md`:292 | 候选排除含「身体条件不允许」（已删的能量排除）→ 删去，补「能量不构成排除」 |
| `docs/design/06-delivery.md`:172-175 | dreaming 窗口示意代码未过滤可见性 → 补 `visibility == "agent"`（`delivery/windows.py`） |
| `docs/manual/rules.md`:199 | `tool.surface` 按活动另设工具面（旧工具面，该键已不合法）→ `action` 行：内部标准动作名，人物工具面固定 |

清除后，现行条款中下列说法都已不存在：人物 `plan_day` 工具与日首计划；生活决策 `finish`、`finish.memories` 与记忆 `refs`；能量闸门（`INSUFFICIENT_ENERGY`、能量可行性、恢复查能量）；一秒边界、下一秒与连续拒绝冷却；`max_consecutive_rejections`（只剩「含该键的场景被拒」这类说明）；非 8 的人物工具数；人物手动开关会话；「只列当前可执行」剪裁；运行时 Dreaming。保留并判定为正确的：零点后台任务 `plan_day` 与只读问答的 `finish`；明示删除或取代的说明、版本表历史行与离线 dreaming；`AgentState.consecutive_rejections`（代码仍在，只作挫败感输入）；锁定 AS2 的上游测试名 `test_step_finish_with_empty_memories_is_legal`（锁定提交不改）。

按用户裁定不改的一处：[数字人交互规格](../SPEC-agent-interaction.md)「午夜浓缩」一节及其修订 #3 保持原文（实施台账版本 0.2 行：数字人交互规格的午夜浓缩与修订 #3 不改）。该节仍写零点执行记忆巩固；现行行为以本规格第四、七章为准：零点只由后台任务 `plan_day` 生成当日计划，不读写人物工作区与记忆，零点不触发记忆巩固。

### 标题引用与锚点核对

实施台账要求一次性脚本核对本规格同步任务触及的文件（门禁不检查锚点）。脚本不入库；文件集合为 `git diff --name-only 5822b54 HEAD -- docs README.md`、工作区改动与未跟踪文档的并集。核对的引用形式：路径加「标题」（反引号路径、裸文件名或 Markdown 链接后紧跟「标题」，以及紧随其后的连写「标题」）；指向其他文档的带锚点链接，按 GitHub 规则生成锚点，重名标题加 `-N`；附带核对同文件内的锚点链接。

| 核对时点 | 文件数 | 路径加「标题」：总数 / 指向现有标题 / 失效 / 目标不在仓库 | 带锚点链接：总数 / 有效 | 同文件锚点链接：总数 / 有效 |
| --- | --- | --- | --- | --- |
| 阶段 7（`1dab733` 的改动在工作区时） | 35 | 243 / 225 / 2 / 16 | 188 / 188 | 20 / 20 |
| 阶段 9（本记录与【后】作业在工作区时） | 38 | 253 / 235 / 2 / 16 | 200 / 200 | 22 / 22 |

- 「指向现有标题」含省略章节序号的写法（如「推进共享时钟」对「5. 推进共享时钟」）。
- 两次核对的 2 处失效相同，都在历史版本行，按约定不改写：生活现实感规格版本行中引用的接口合同「日程骨架请求」，生活运行规格版本行中引用的记录引擎设计「决策节律增量字段」。
- 两次核对的 16 处目标不在仓库的引用相同：15 处指向仓库外的调研稿 `.work/object-model-reanalysis/` 下的 `REPORT.md` 与 `LOCATION-GRANULARITY.md`，条数与 `5822b54` 时相同，不是本特性引入的；1 处是实施台账「文件修改清单」转述测试注释里的失效引用，台账原文就写明该文件不存在并给出改引。
- 结论：本规格同步任务触及的文件中，现行文字里指向仓库内文档的「标题」引用与锚点链接全部指向现有标题；本特性的改动没有新增失效引用。

文档门禁 `python scripts/documentation_gate.py`：阶段 7 改动后扫描 119 份文档，失效引用 0，PASSED；阶段 9 改动后扫描 120 份文档，失效引用 0，PASSED。

## 真实复验（阶段 8，只报告）

**未执行。** 本机没有模型凭据（`AGENTSOCIETY_LLM_API_KEY`），r14 的运行数据在另一台机器上。按实施台账，阶段 8 只报告、不作门禁；完成判据「两份复验的八项指标、定性观察与运行身份写入本特性验收记录」待执行后补入本节。执行步骤：

1. 在干净提交上运行，运行清单的 `git_dirty` 须为假，`git_commit` 即该提交。练秋湖使用阶段 4 重建后的数据集（`WorldMock-Dataset` 的 `9071822` 或其后），在 WorldMock 根目录执行：

   ```bash
   uv run worldmock run \
     --scenario ../WorldMock-Dataset/requests/0001-white-collar-lianqiu/scenarios/qingpu-lianqiu-white-collar.json \
     --output <run_dir>
   python scripts/inspect_run.py <run_dir> rerun
   ```

2. 在持有 r14 数据的机器上，以同一提交复跑 r14 场景（issue #2），同样执行 `python scripts/inspect_run.py <run_dir> rerun`。
3. 把两份运行的八项指标、规格第六章的定性观察（饿时是否发现浏览、取得、等交付、食用的路径并说明取舍；是否虚构商品、重复下单或把自述当成食用）与运行身份（`code_sha256`、`git_commit`）写入本节。复验中出现 `run_failed` 时按缺陷回到所属阶段修复，修复后重跑阶段 7 的门禁与本阶段。

对照基线：`rerun` 子命令在旧代码的练秋湖运行 `2026-09-28-deepseek-flash-after-wake-fix`（`WorldMock-Dataset` 的 `requests/0001-white-collar-lianqiu/runs/` 下；清单 `code_sha256` 为 `989e1fb06d856970f972eaed1f092d7ed8af93452227ebd9672bf776a56699ee`，旧代码的清单没有 `git_commit`）上的输出：

| 指标 | `-after-wake-fix` |
| --- | --- |
| 无进行中活动总时长 | 10 小时 12 分（36767 秒） |
| 同刻轮数分布 | 1 轮 × 53 |
| 播报唤醒占比 | 50/53（94%）；仅因播报 22/53（42%） |
| 最低饥饿分级 | 饿得难受 |
| 首次进食前最低能量 | 2026-09-28：692.4 kcal；2026-09-29：0.0 kcal（当日无进食） |
| 进食次数 | 1 |
| 步行占比 | 不适用（只有驾车） |
| 零点计划中早于 08:30 的提醒 | 不适用（旧代码没有零点计划） |

播报唤醒占比与规格验收台账「真实复验」行给出的对照值（50/53、22/53）一致。r14 在本机没有基线。

## 实现后文档（阶段 9）

阶段 9 的【后】作业除 IS1 外与本记录同批落地：

| 作业 | 文件 | 改动 |
| --- | --- | --- |
| E4-21、E4-24 | `docs/design/04-simulation-engine.md` | 「当前实现边界」删去冻结时补的「时间与能量规格尚未实现」一句；「验收重点」补「时间与能量的逐条证据见时间与能量验收记录」并链接本记录；版本 v1.8 |
| E5-19、E5-20 | `docs/design/05-recording-engine.md` | 按最终代码与测试核对差距已闭合后，删去本规格的实现差距行（结束与暂停无快照、结算改写出行地点；同刻决策证据、零点计划后台任务与系统登记入口、记忆来源、经历句子），两节各改为一句「本主题当前没有未闭合的实现差距」；对应的五个验收行由「待实现」改为「已实现」并链接本记录；「调试工具」写明 `body` 显示观察事件中的 `body.values` 与 `body.levels`，补只读子命令 `rerun`，删已兑现的身体浏览差距行 |
| VF12 | `docs/VERIFICATION.md` | 「时间与能量特性门禁」末尾补「证据统一见本特性验收记录」并链接本记录；测试矩阵中冻结时改写或新增、记「离线验收待执行」的时间一致、资源结算、决策协议、LabelKit 输入、同刻决策、身体语义、观察投影、两阶段决策、社交九行改回「离线覆盖」，其中 LabelKit 输入行的证据位置补 `tests/delivery/test_publication.py`，观察投影行补 `tests/life/test_public_observation_contract.py` 与 `tests/life/test_dayrun_restaurants.py`；决策节律一行起初仍记「离线验收待执行」：其「`discovery_result` 保留在闭集中、当前无来源，验证其不产生」一句当时没有用例（`tests/life/test_wake_reasons.py` 只验证求值函数在标志置位时产生它，没有断言引擎从不置位）；随后补引擎级用例 `tests/life/test_wake_reasons.py::test_browse_results_return_in_observation_never_as_discovery_result`（登记在台账「唤醒词表」行）：真实引擎走独自浏览到计划结束并收尾、被本人提前结束、在进行中的等待旁浏览三种情形，决策开始、观察与共同身体结算的唤醒原因都不含它，节律为各边界装配的唤醒判定输入从不置位，浏览结果置顶于随后的观察；三处临时突变（开始浏览时按旧行为排下一秒并记它、节律在活动结束的边界置位、同刻轮记它）各使该用例失败，生产代码随即还原。该行证据位置补 `tests/life/test_day_planning.py`、`tests/life/test_same_moment_decisions.py`，状态改为「离线覆盖」 |
| IX5 | `docs/INDEX.md` | 文档地图登记本记录（状态 `验收记录`）；「按任务读」时间与能量一行改为已实现并补本记录；当日目标规格一行删去 IX4 加入的「，修订条款待实现」 |
| AN1 | `docs/dev/ANALYSIS-spec-coverage.md` | 新增「时间与能量增量映射」一节，置于一日行为增量映射之前：说明本规格已实现并指向其验收台账、登记表、完整性守卫与本记录；列出被「冻结条款修订」改写、历史映射不再代表现行条款的 26 行（A5、A14、E3、E14、E16、E18、E24、R6、R8、C7、C8、T4、T7、X9、DEV38、DEV39、DEV43、DEV48、DEV49、RC1、RC3、RC11、RC17、RC28、RC30、CC10）与「抽样自验（20/20）」第 4 条，逐行注明取代它的规格章节与修订编号；历史行原样保留，新条款不混入旧统计分母 |
| CB1 | `docs/dev/coverage-baseline.md` | 「基线记录」首节改为 `ef13dc5` 的完整门禁（3978 passed、8 skipped、分支 94.57%、未覆盖公共接口 0），取代 2026-09-22 的中断运行报告；原地图与品类主线组合一节改为历史基线 |
| 状态标记 | 六份冻结规格 | 本规格修订在各冻结规格中留下的待验收标记按实现结果改写：数字人交互、一日行为、地图与门店三份的状态头改为已实现并通过离线验收；当日目标「验收台账」两行「待重验」改为「通过」；生活现实感验收矩阵的记忆写入、记忆巩固两行「待重验」改为 `代码层已验证`；LifeEnv 运行时验收矩阵八行「本次离线验收待执行」改为「代码层已验证」，「社交」行按改写后的社交行为补观察与证据，状态头改为受影响条款已重新验收 |
| 台账 | `docs/dev/research/time-and-energy-implementation-plan.md` | 阶段 7 记后续提交；阶段 8 记未执行及原因；阶段 9 记落地情况 |
| IS1 | `docs/dev/implementation-status.md` | 不在本批；按实施台账作为本特性最后一个提交单独落地 |

改到的文档都追加了版本行：设计文档 04 为 v1.8，05 为 `2026-09-26-time-and-energy-stage-9`；验收标准与 LifeEnv 运行时、一日行为、地图与门店、当日目标、生活现实感规格为 `2026-09-26-time-and-energy-stage-9`，数字人交互规格为 1.10；文档索引为 `2026-09-26-time-and-energy-verification`，覆盖率基线为 `2026-09-26-time-and-energy`，规格覆盖分析为「时间与能量增量映射」行；实施台账按其约定只在各阶段小节记落地情况。

## 本特性范围外的已知问题（未修）

下列问题在实施与评审中发现，不属于本规格，未修：

- `src/worldmock/corpora/map_prepare.py:25` 与 `src/worldmock/corpora/fetch.py:269` 以不带编码参数的 `read_text()` 读 UTF-8 JSON，在区域编码为 cp936 的 Windows 上有解码失败风险。**本记录之后已修复**：两处改为显式 `encoding="utf-8"`；`tests/corpora/test_text_encoding.py` 在模拟的 GBK 区域设置下核对发布的 `dataset.json` 署名（「©」）与路线清单来源逐字不变，并核对 corpora 包内全部文本读写都显式给出编码。
- WorldMock 源码部分的 `code_sha256` 随检出的换行（CRLF 或 LF）而变；AS2 部分按锁定源码摘要比对，不受影响。
- 每轮开销随运行时长增长（例如联系人回复结算每轮重读全部事件），同刻轮次又提高了轮数；长运行的耗时须另行关注。
- `docs/dev/SPEC-map-and-storefront.md` 第 49 行仍写「当前组合发布为目录 4.0」：按 MS1 保留该发布说明。
- 零点计划 `plan_day` 的人物输入取场景声明的人物画像，因为编译后的人物卡只在人物工作区中；接口合同写明了这一解释。
- 数据集生成器在 Windows 上写出 CRLF 换行。

文档中的既有漂移（阶段 7 核对时发现，非本特性引入，建议另行处理；行号为 `1dab733` 中的文件）：

- `docs/design/03-resource-engine.md`:443：「注册表 `surface` 不构成全部生活工具名清单」，`surface` 已于 `3880738` 删除。
- `docs/VERIFICATION.md`：
  - :181、:183 的菜单措辞仍是「工具或活动类型」，菜单现只声明 `allowed_actions`；
  - :286 的 `play_game` 实例已于 `e2b8c6b` 删除；
  - :299 的键名表与 `reports` 输出不符。
- `docs/dev/SPEC-decision-context.md`:62：自述生成参数写 1.3/400，代码与合同为 1.0/8000。
- `docs/dev/SPEC-interaction-menu.md`:130：`BROWSE_CATEGORY_NOT_FOUND` 已删。
- `docs/dev/SPEC-map-and-storefront.md`:77、:138：发现查询字段已删。
- `docs/dev/SPEC-life-runtime.md`:366：身体数值自 `d998a31` 起在 `body_settlement` 事件中。
- `docs/CONTRACTS.md`：
  - :492 交谈缓解速率待核实；
  - :556-557 字段表漏列 `current_epoch`、`precondition_hints`、`location`；
  - :523 `CALL_DURATION_OUT_OF_RANGE` 代码已删，合同行按 C25 写「不再触发」。
- `docs/design/00-overview.md`：:140-142 的「§」式引用（08 没有「场景文件规格」标题）；:178、:197、:207 写「v1.0 单人物」。
- `docs/dev/SPEC-life-realism.md`:494：当日目标行的验收状态。
- `docs/design/05-recording-engine.md`:777-778：质量报告分组未列 `day_plan_metrics`。
- `docs/VERIFICATION.md` 测试矩阵的「时间一致」行：所称媒体倍速在该行所列测试文件中早已没有对应用例（早于 `778beca`；阶段 9 核对 VF12 时发现）。
