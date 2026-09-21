# 一日行为实施与验收台账

> 实施中。基线 `74edeaddccf1e051a0f7c98024ec95eeb6c48514`，分支 `codex/dayrun-behavior`。
> 工作树 `/Users/atishoo/.codex/worktrees/dayrun-behavior/WorldMock`；不修改并发主工作树。
> 行为权威为 [特性规格](../../SPEC-dayrun-behavior.md)，接口归 [CONTRACTS](../../../CONTRACTS.md)，
> 记录格式归 [记录引擎设计](../../../design/05-recording-engine.md)。本表不能替代生产验证。完整逐文件清单见[文件清单](files.md)。

## 独立方案核查

[活动与身体](concurrency-design-review.md)、[记忆与记录](memory-design-review.md)、
[餐食与音频](dining-design-review.md)保存实施前核查与来源。报告中的旧代码差距属于基线证据，
最终裁决以冻结规格为准。

## 条款、生产行为与可执行验证

下表按冻结规格的小节和可观察合同分组，覆盖本次全部行为；测试路径均相对仓库。
生产已固定为 `d998a31`，最终门禁及独立审查尚在执行。表内的行为映射不代替本页末尾的固定源码收据。
设计文件的具体责任与全部实际修改文件另见[文件清单](files.md)。

| 规格范围与可观察合同 | 生产入口 | 可执行验证 |
| --- | --- | --- |
| 等待与空白只结算清醒体感，不凭自由文字制造目的、内容或进食 | life/body、motivation、activity_evidence；footprints/experiences | tests/life/test_wait_passive_entertainment.py；tests/footprints/test_verified_experiences.py |
| 真实播放独立产生娱乐与媒体接触；人物经过时间取并集 | life/content；evaluation/behavior_metrics；footprints/quality | tests/evaluation/test_behavior_metrics.py::test_parallel_activity_time_does_not_double_person_elapsed_or_fake_content；tests/life/test_dayrun_podcast_playback.py |
| NASA 官方原页、音频身份、786 秒选段及原始锚点可复现 | scripts/build_nasa_podcast.py；corpora/podcasts；life/content、scenario | tests/corpora/test_dayrun_podcast.py；build_nasa_podcast.py --check |
| 完整段落结束才揭示；暂停、倍速、恢复与媒体终点遵循真实位置 | life/content、activities | tests/life/test_dayrun_podcast_playback.py；tests/life/test_parallel_activities.py::test_ordinary_long_cue_does_not_repeat_at_next_settlement |
| activities 唯一事实源，世界版本 5；ACTIVE/暂停集合完整、排序稳定且独立投影 | life/models、observe、projection；custom/agents/life_activity_controls | tests/life/test_state_serialization.py；tests/integration/test_dayrun_parallel_agent.py；tests/integration/test_observation_agent_seam.py |
| 资源只取显式声明；暂停/终态释放，人物间独立；原地点、资格、数量条件保留 | catalog/affordances；life/engine、activity_validation | tests/life/test_parallel_activities.py；tests/life/test_parallel_acceptance.py；tests/life/test_activity_location_kinds.py |
| 每种允许播放组合由资源交集得出，冲突组合拒绝 | life/engine；custom/agents/life_activity_controls | tests/life/test_parallel_acceptance.py::test_each_allowed_watch_partner_starts_through_atomic_tool；tests/integration/test_dayrun_parallel_agent.py::test_parallel_rejects_shared_resources_before_dispatch |
| 原生组合工具只收持续活动子项、至少两项；单轮一个动作加 finish | life/models、engine；LifeAgent、LifeEnv | tests/integration/test_dayrun_parallel_agent.py；tests/integration/test_activity_schema.py；tests/integration/test_dayrun_complete_days.py |
| 组合与每个子项分别满足有效菜单和真实可见目标，摘要无授权 | life/interaction_menu；custom/agents/life_menu、life_activity_controls | tests/integration/test_parallel_rejection_contract.py；tests/integration/test_interaction_menu.py；tests/integration/test_grounded_targets.py |
| 整组暂存校验，拒绝无余额、库存、邀请、游戏、活动或草稿残留 | life/engine、action_evidence | tests/life/test_parallel_acceptance.py::test_rejected_group_has_no_activity_money_game_or_invitation_effects；tests/life/test_parallel_activities.py::test_parallel_resource_conflict_rejects_whole_group |
| 组合幂等、各子项独立身份与终态、全部终结才写一次聚合终态 | life/action_evidence、engine；footprints/store | tests/life/test_parallel_acceptance.py::test_simultaneous_completion_has_one_body_and_one_aggregate_terminal；tests/life/test_parallel_acceptance.py::test_experiment_end_truncates_both_and_fails_group |
| 继续、暂停、恢复、取消、变速只影响点名活动；真实决策可操作；社交仅取消、游戏保留命令 | life/engine、activities、social_actions；LifeAgent | tests/life/test_parallel_acceptance.py；tests/integration/test_dayrun_parallel_agent.py；tests/life/test_shared_body.py |
| 恢复重验地点、目标、资源、时间及剩余能量；失败保持暂停进度 | life/activity_validation、models、engine | tests/life/test_parallel_activities.py::test_reopen_rejects_tampered_parallel_activity；tests/life/test_energy_feasibility.py；tests/integration/test_meal_checkpoint.py |
| 行走与播放先后结束、同刻结束、各自暂停取消、实验截止截断 | life/engine、activities、travel、action_evidence | tests/life/test_parallel_acceptance.py；tests/life/test_dayrun_podcast_playback.py |
| body_settled_at 共同时刻；全部活动、内容、游戏、身体、环境、截止共同选边界 | life/engine、rhythm、models | tests/life/test_shared_body.py；tests/life/test_parallel_acceptance.py；tests/integration/test_dayrun_complete_days.py |
| 每人区间一次基础代谢、睡压、欠眠与需求；空白及零能量不制造活动 | life/body、engine | tests/life/test_shared_body.py；tests/life/test_energy_feasibility.py；tests/integration/test_dayrun_complete_days.py |
| 最大 MET、按结束点切分，准入、剩余预测、实际阈值同式；恢复项不豁免混合耗能项 | life/body、activities、engine；life/observe | tests/life/test_shared_body.py；tests/life/test_parallel_activities.py::test_recovery_activity_cannot_sponsor_unaffordable_watch；tests/life/test_energy_feasibility.py |
| 独立效果按 ACTIVE 秒数汇总后限幅，排序无关；工作共享身体，食品热量实际一次 | life/engine、body、activity_evidence | tests/life/test_shared_body.py::test_real_parallel_body_uses_maximum_met_and_is_order_independent；tests/integration/test_meal_checkpoint.py |
| 每人区间唯一 body_settlement，活动仅引用公共事件并记自己的进度 | life/engine、activity_evidence；footprints/validation | tests/life/test_parallel_activities.py；tests/life/test_parallel_acceptance.py；tests/integration/test_dayrun_complete_days.py |
| 所有本人正活动进度参与目标匹配，首次推进和情绪脉冲仍只一次 | life/rhythm、schedule | tests/life/test_day_plan.py::test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time；tests/life/test_day_plan.py |
| 三个正式场景的三家餐馆显式已有餐食、可自提和初始库存；通用空菜单合法 | corpora/zhangjiang-map；scenarios；life/dining | tests/corpora/test_category_corpora.py；tests/catalog/test_category_admission.py；tests/life/test_dayrun_restaurants.py |
| 到达首轮本店候选可直接购买，完整本地摘要不授予候选外权限 | life/dining、observe、projection、presentation；life/engine | tests/life/test_dayrun_restaurants.py::test_restaurant_arrival_immediately_exposes_pickup_and_consumption；tests/life/test_storefront_arrival.py；tests/integration/test_category_inventory_protocol.py |
| 空菜单、售罄、闭店及不满足购买条件如实进入提示，遵守预算 | life/dining、narration；LifeAgent | tests/life/test_dayrun_restaurants.py；tests/life/test_narration.py；tests/integration/test_decision_context_protocol.py |
| 自提付款扣库并加持有，配送等真实交付；只有正食用区间证明吃过 | life/engine、basket、commerce；footprints/experiences | tests/life/test_storefront_commerce.py；tests/life/test_dayrun_restaurants.py；tests/integration/test_meal_checkpoint.py |
| 消费中断/恢复与检查点不重新付费、交付或重复消费；不可购不撤销真实行程 | life/engine、basket；footprints/store | tests/integration/test_meal_checkpoint.py；tests/life/test_storefront_arrival.py |
| 四种经历只投影本人已提交截止前事件，观察只取前五条正序，观察身份固定 | footprints/experiences、store；life/observe；LifeAgent | tests/footprints/test_verified_experiences.py；tests/integration/test_verified_memory.py |
| 等待、食用、到达、播放的事实须来自真实正区间或到达结果；请求与自述不产生事实 | footprints/experiences；life/activity_evidence | tests/footprints/test_verified_experiences.py；tests/footprints/test_quality.py |
| interval_quantity 为增量，累计量另存；重读、暂停、检查点不重计，不同人物隔离 | life/engine、basket；footprints/experiences | tests/footprints/test_verified_experiences.py::test_consumed_intervals_are_increments_and_projection_is_idempotent；tests/integration/test_meal_checkpoint.py；tests/integration/test_dayrun_complete_days.py |
| 应有但畸形的结算失败并记日志；完整导出独立重算来源和哈希 | footprints/experiences、export、quality、validation | tests/footprints/test_verified_experiences.py；tests/footprints/test_export.py；tests/footprints/test_quality.py |
| finish 零至八条九种主观类型，所有写入、查询、反思固定 subjective；删除旧事件类型 | LifeAgent；life_memory；AS2 memory store/runtime | tests/integration/test_activity_schema.py；tests/integration/test_verified_memory.py；tests/integration/test_life_agent_protocol.py |
| 只可引用本轮可见来源；引用有效不证明文字真实，主观内容不进事实指标 | LifeAgent；life/memory_evidence；footprints/quality；evaluation | tests/integration/test_verified_memory.py；tests/life/test_memory_evidence.py；tests/footprints/test_quality.py |
| MEMORY 身份、近期事件事实、主观内容确定性装配；提示只有本轮事实，不重复旧视图 | custom/agents/life_memory；LifeAgent；AS2 runtime factory | tests/integration/test_verified_memory.py::test_fact_view_only_uses_current_observation_and_does_not_reset_subjective_counter；tests/integration/test_dayrun_complete_days.py |
| 复用检索存储阈值及检查点；视图刷新不重置巩固计数，归纳单独持久化 | life_memory；AS2 memory runtime/store | tests/integration/test_verified_memory.py；AS2 全部测试；tests/integration/test_dayrun_complete_days.py |
| 反思新身份进入证据；零输入合法、新增/合并区分、未变可不写，写盘失败不成功 | life_memory；AS2 MemoryStepOutcome；life/memory_evidence | tests/integration/test_verified_memory.py；tests/integration/test_life_agent_protocol.py::test_memory_persistence_failure_aborts_society_before_commit；tests/footprints/test_quality.py::test_merged_memory_identity_is_not_reported_as_a_duplicate |
| 静默边界推进全部活动，不调用模型/观察/记忆；每人边界至多一次决策 | life/engine、rhythm；LifeEnv | tests/life/test_parallel_acceptance.py；tests/life/test_scheduling.py；tests/integration/test_dayrun_complete_days.py |
| 全部可见任务截止前三十分钟，真正受威胁本地承诺闭店前十五分钟 | life/rhythm、schedule | tests/life/test_wake_reasons.py::test_second_task_new_warning_is_not_hidden_by_first；tests/life/test_wake_reasons.py::test_engine_schedules_closing_warning_only_for_local_task |
| 签名绑定具体任务/截止/地点/闭店/承诺；持续同因静默，新事实才重提醒 | life/rhythm、schedule；models/delivered_warning_signatures | tests/life/test_wake_reasons.py；tests/life/test_day_plan.py |
| 活动不可行离散原因，条件消失清除；睡眠过滤不消费提醒；实际事件仍独立唤醒 | life/rhythm、schedule、engine | tests/life/test_wake_reasons.py::test_sleep_filtered_warning_is_delivered_after_waking；tests/life/test_wake_reasons.py；tests/life/test_energy_feasibility.py |
| 检查点保留身体时刻与交付签名，不重扣区间或重交同一预警 | life/models、engine、rhythm | tests/life/test_wake_reasons.py::test_checkpoint_restore_does_not_redeliver_same_task_warning；tests/integration/test_dayrun_complete_days.py |
| 两种子、双人完整自然日与非法模型输出，真实工具到 SQLite、记忆、恢复、导出 | LifeAgent → LifeEnv → LifeEngine → FootprintStore → memory/export | tests/integration/test_dayrun_complete_days.py，种子 17、54，每人 86400 秒 |
| 覆盖、目录/语料身份、文档一致性与独立语义变异闭环 | scripts/coverage_gate、build_catalog、build_nasa_podcast、documentation_gate | 固定源码收据和独立 Bob 报告见下文，未完成前不放行 |

## 集成中发现并关闭的问题

- 真实控制行、记忆字段与旧测试夹具不一致：按冻结合同更新，保留非法输入拒绝和动态工具身份断言。
- 跨结算区间的官方播客段落被通用字幕去重误删：按段落完成锚点交付，真实暂停、倍速与末锚点回归通过。
- 多个食物事实共享一个消费事件：记忆证据按首次出现去重来源，完整事实仍保留各食物增量。
- 到达效果应用后的路线里程未进入终态动作快照：终态统一取世界表最终活动，原到店强断言通过。
- 活动快照能篡改资源、目标和时间后恢复：结构与冻结目录共同校验，实际持久化后重新打开拒绝。
- 无活动且零能量的瞬时动作后无法推进：空白时间基础代谢到零后仍演化睡压与需要，不创建假等待。
- 其他地点任务或没有地点的自由目标会触发本店闭门提醒：只据明确地点约束构成威胁。
  独立审查确认持续活动的实际启动与恢复没有营业准入条件；规格删除没有当前实例的条件分支，
  明确活动本身不触发闭店威胁，不为此新增营业限制。
- 恢复活动误为混合耗能活动豁免能量准入：只有全组恢复项才享有原豁免，恢复时同样核对剩余成本。
- 活动集合进度迭代遗漏人物归属检查：逐活动核对本人，保留跨人物证据不得推进目标的强断言。

这些修复对应规格已有的事实、恢复及共同时间要求，不新增替人物行动或自由文字推断功能。

## 当前证据

独立工作树初始化正确的可编辑依赖；核心回归基线：
`tests/life/test_engine_round.py tests/life/test_wait_passive_entertainment.py
 tests/integration/test_observation_agent_seam.py tests/integration/test_run_manifest.py`：
41 passed，10.52 秒。该基线不证明新增特性已完成。

多个完整日和中断恢复已实跑：`tests/integration/test_dayrun_complete_days.py` 两种子、双人、
每人 86400 秒共同身体区间，2 passed；`tests/integration/test_meal_checkpoint.py` 实际自提进食中断、
检查点恢复、剩余量/库存/金额/食品热量/导出守恒，1 passed。

首次整合覆盖门禁是在并发实现尚未冻结时运行，75 failed、3709 passed、4 个既有历史产物缺失跳过；
分支 92.26%，公共接口有 33 项未完全执行。此轮是定位回归和边界测试缺口的失败证据，
不是放行结果；缺口正在按独占文件修复，最终必须以固定源码重新执行。

目录四项逐字节复现、NASA 派生文件检查通过；正式十二周场景 7386 条素材、143 个分区全部可达。
素材可达不证明真实十二周模型运行完成。


## 固定实现后的门禁与独立反例

实现提交 `d998a31` 的完整回归为 3827 passed、4 个既有跳过、24 warnings，658.04 秒。
分支覆盖 95.80%（7043/7352），剩五个公共接口缺口，因此该轮仍未放行。
公共接口补测覆盖了正在工作的提交不评审、无效观察身份、未来身体结算时刻与组合最小数量；
同时删除共同身体事件保证存在后不可达的旧 `time_advanced` 分支，未新增兼容或回退。
相关组合、记忆来源、导出与评审边界 39 passed，样式门禁通过。

独立 Bob 初轮发现的测试漏检均保留精确补丁，补测不修改规格要求：

| 漏检行为 | 新增强断言 |
| --- | --- |
| 最近五条被替换为最早五条 | 八条与四十条历史、另一人物、截止后事件，精确断言最近来源身份与顺序 |
| 评估人物时长对重叠区间简单相加 | 相交、包含、分离区间与不同人物的明确并集分钟 |
| 导出质量报告另一处时间并集被替换为求和 | 真实 SQLite→export→quality.json 的逐人物秒数及文件哈希 |
| 真实存在但未呈现的供给被授权购买 | 有限窗口外真实本地供给经观察和提交拒绝，账户、库存、持有、订单和事件不变 |
| 冻结画像身份在 MEMORY 中倒序 | 持久化身份段逐字来自冻结人物画像 |
| 主观归纳在实际提示被标成已核验经历 | 决策与行动提示都明确保留主观标题 |
| 合并已写入存储但更新结果漏报身份 | 两次 after_step 后旧身份、merge_count 与 merged_episodes 同时核对 |
| 同区间只处理第一条活动进度 | 真实播放与等待各自推进对应目标、保留各自活动身份和实际时刻 |

这些补测的干净快照将用于原始补丁复验；最终结果以随后收据为准。
