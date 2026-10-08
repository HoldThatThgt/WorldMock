> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-behavior/verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 一日行为实施与验收台账

> 本特性全部实现并通过离线验收，无特性延期项。基线 `74edeaddccf1e051a0f7c98024ec95eeb6c48514`，分支 `codex/dayrun-behavior`。
> 工作树 `/Users/atishoo/.codex/worktrees/dayrun-behavior/WorldMock`；不修改并发主工作树。
> 行为权威为 [特性规格](../../SPEC-dayrun-behavior.md)，接口归 [CONTRACTS](../../../CONTRACTS.md)，
> 记录格式归 [记录引擎设计](../../../design/05-recording-engine.md)。本表不能替代生产验证。完整逐文件清单见[文件清单](files.md)。

## 独立方案核查

[活动与身体](concurrency-design-review.md)、[记忆与记录](memory-design-review.md)、
[餐食与音频](dining-design-review.md)保存实施前核查与来源。报告中的旧代码差距属于基线证据，
最终裁决以冻结规格为准。

## 条款、生产行为与可执行验证

下表按冻结规格的小节和可观察合同分组，覆盖本次全部行为；测试路径均相对仓库。
最终被测生产与测试固定为 `9a62963`，全仓门禁和独立语义审查均已通过。表内的行为映射与本页末尾的固定源码收据共同构成验收依据。
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
| 所有本人正活动进度参与目标匹配，首次推进和情绪脉冲仍只一次 | life/rhythm、schedule | tests/life/test_day_plan.py::test_goal_pulse_requires_actual_own_positive_evidence_and_is_one_time；tests/life/test_parallel_acceptance.py::test_every_parallel_activity_progresses_its_matching_day_goal |
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
| 签名绑定具体任务/截止/地点/闭店/承诺；持续同因静默，新事实才重提醒 | life/rhythm、schedule；models/delivered_warning_signatures | tests/life/test_wake_reasons.py::test_changed_task_deadline_warns_again_and_completed_task_clears_signature；tests/life/test_wake_reasons.py::test_changed_closing_time_and_new_local_commitment_warn_independently |
| 活动不可行离散原因，条件消失清除；睡眠过滤不消费提醒；实际事件仍独立唤醒 | life/rhythm、schedule、engine | tests/life/test_wake_reasons.py::test_sleep_filtered_warning_is_delivered_after_waking；tests/life/test_wake_reasons.py::test_infeasibility_warns_once_clears_and_can_reappear；tests/life/test_energy_feasibility.py |
| 检查点保留身体时刻与交付签名，不重扣区间或重交同一预警 | life/models、engine、rhythm | tests/life/test_wake_reasons.py::test_checkpoint_restore_does_not_redeliver_same_task_warning；tests/integration/test_dayrun_complete_days.py |
| 两种子、双人完整自然日与非法模型输出，真实工具到 SQLite、记忆、恢复、导出 | LifeAgent → LifeEnv → LifeEngine → FootprintStore → memory/export | tests/integration/test_dayrun_complete_days.py，种子 17、54，每人 86400 秒 |
| 覆盖、目录/语料身份、文档一致性与独立语义变异闭环 | scripts/coverage_gate.py、build_catalog.py、build_nasa_podcast.py、documentation_gate.py | 固定源码收据和独立 Bob 报告见下文，全部通过 |

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

## 初期集成证据与历史边界

独立工作树初始化正确的可编辑依赖；核心回归基线：
`tests/life/test_engine_round.py tests/life/test_wait_passive_entertainment.py
 tests/integration/test_observation_agent_seam.py tests/integration/test_run_manifest.py`：
41 passed，10.52 秒。该基线不证明新增特性已完成。

多个完整日和中断恢复已实跑：`tests/integration/test_dayrun_complete_days.py` 两种子、双人、
每人 86400 秒共同身体区间，2 passed；`tests/integration/test_meal_checkpoint.py` 实际自提进食中断、
检查点恢复、剩余量/库存/金额/食品热量/导出守恒，1 passed。

首次整合覆盖门禁是在并发实现尚未冻结时运行，75 failed、3709 passed、4 个既有历史产物缺失跳过；
分支 92.26%，公共接口有 33 项未完全执行。此轮是定位回归和边界测试缺口的失败证据，
不是放行结果；该轮缺口随后全部关闭，最终固定源码结果见末节。

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

这些补测已在干净快照上完成原始补丁复验；精确补丁及失败断言保留在末节的独立报告中。


预警签名补充审查另发现同任务截止、同地点闭店时刻、具体本地承诺及威胁消失清理四项漏检。
`tests/life/test_wake_reasons.py` 已补给定权威事实变化的公开节律入口回归，整文件 45 passed：
截止改变重新提醒、闭店改变重新提醒、同闭店新增承诺独立提醒、同因进度变化静默，
能量恢复或活动暂停/终态清除签名，可行后再次不可行重新提醒。
这些测试不冒充新增运行中改期管理工具；本特性没有该工具。

额外记忆闭环：`test_finish_rejects_retired_fact_like_memory_types` 逐一在服务 Schema、本地模型和人物校验
拒绝 `event`/`observation`；Schema 文件 38 passed。
`test_native_memory_update_records_real_subjective_merge_without_duplicate` 经真实记忆运行时、
PersonAgent 原生记录入口和宿主证据捕获，证明合并身份确实进入最终事件载荷；协议文件 36 passed。
这条测试不伪造 MemoryStepOutcome，也不声称宿主捕获等于实际写入事件库；环境记录与导出另有整链测试。


`aa22dc830d6a66d13f14a22a04babae5cb41ec98` 的覆盖门禁已通过：
3839 passed、4 个既有历史产物跳过、24 warnings，653.36 秒；
分支 95.86%（7046/7350），公共接口未覆盖数 0。
随后预警条件转换、旧记忆类型拒绝及原生合并载荷的测试补强不修改生产，
已在最终补测提交重新执行完整门禁；下列收据包含新增用例。


## 最终固定源码验收

所有 SPEC 功能同批完成，无待实现或延期条款。固定被测提交为
`9a62963841694cb668cc49a039c59f39caa757a6`，AS2 为
`8768e14df5c887c710fe96bf24981a2d8508346e`，LabelKit 保持
`d0afd606decbc3f4aa64c64ef5339586120c5a7a`。此后仅更新文档与证据，生产、测试、脚本、输入和依赖不变。

| 验收范围 | 实际命令或入口 | 最终结果 |
| --- | --- | --- |
| 全仓测试与覆盖 | `MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python scripts/coverage_gate.py` | 3847 passed、4 个既有历史产物缺失跳过、24 warnings，651.72 秒；分支 95.89%（7048/7350），公共接口缺口 0，PASSED；新增特性用例无跳过 |
| 完整自然日 | `tests/integration/test_dayrun_complete_days.py`，种子 17 与 54，每种子双人 | 2 passed，已纳入最终全仓门禁；真实 LifeAgent、工具、环境、SQLite、观察、记忆、恢复与导出，每人 86400 秒共同身体区间 |
| 实际进食恢复 | `tests/integration/test_meal_checkpoint.py` | 1 passed，已纳入最终全仓门禁；真实到店、自提、半程进食、暂停、关库恢复和剩余份额结算守恒 |
| 维护依赖 | `pytest -q vendor/agentsociety2/packages/agentsociety2/tests` | 480 passed、2 项依赖警告；独立固定 AS2 提交的原始上下文见[记忆验收](memory-verification.md) |
| 目录身份 | `scripts/build_catalog.py --check` | 四项正式产物逐字节一致 |
| 官方播客派生 | `scripts/build_nasa_podcast.py --check` | 正式选段及派生文件一致 |
| 正式素材准入 | `.venv/bin/python scripts/check_reachability.py --scenario scenarios/white-collar-12-weeks.json` | 7386 条素材、143 个分区，全部可达 |
| 文档与样式 | `scripts/documentation_gate.py`、`tests/test_style_gate.py`，另核查本次 Markdown 本地链接 | 文档最终扫描及链接数量见[门禁收据](final-gates.json)；样式通过，生产没有新增冻结例外 |

覆盖原始机器报告见[coverage-report.json](../../coverage-report.json)。最终门禁命令、来源身份与日志哈希见
[门禁收据](final-gates.json)，原始输出保存在[门禁证据包](final-gates.tar.gz)。

| 独立语义审查 | 固定源码与选择集 | 结果与精确证据 |
| --- | --- | --- |
| 活动、身体、目标与预警 | 初轮 `d998a31`，补测 `aa22dc8`，最终 `9a62963` 六文件 125 passed | 十六个有效变异全部关闭；五个曾存活补丁在最终源码原样杀死，见[活动审查](bob-activity/report.md) |
| 人物入口与主观记忆 | 初轮 `d998a31`，补测 `aa22dc8` 七文件 102 passed | 十八个有效变异全部关闭；三个原存活补丁原样杀死；一项没有经过实际提示入口的无效变异排除，见[记忆审查](bob-memory/report.md) |
| 经历、餐食、音频与评估 | 初轮 `d998a31`，补测 `aa22dc8` 相关基线 79 passed | 十个原始补丁全部重新运行并杀死，见[经历审查](bob-experiences/report.md) |

合计四十四个有效语义变异，无存活项；这不是“每条 SPEC 都做过变异”的声明。
全部条款的生产行为与普通回归由上方逐条表负责，独立变异用于检验关键断言是否能识别真实行为倒退。
记忆审查初始报告内关于旧类型拒绝和原生合并载荷的测试边界，已由最终提交的三条新增用例补足，
不倒改审查员原始快照判断。最后一轮文档语义复核见[独立文档报告](bob-experiences/final-document-review.md)。

三个父级只读审查入口已正常移除；七个包含子模块的临时审查树均已恢复干净，
Git 拒绝普通移除后按审查规则保留，精确路径见三份报告与门禁证据。未强制删除，也未触动其他会话工作树。

本次使用可控模型输出验证两个完整自然日，未运行真实云端模型长跑；它证明工程路径与规格验收，
不证明人物行为质量、真实十二周运行、LabelKit 项目交付或三名独立盲评。这些原有项目级门禁继续由
[项目实现状态](../../implementation-status.md)记录，不属于本次延期功能。
