# 规格审查（第 1 轮）：与现行冻结规格、合同与设计的冲突

- 被审文件：`D:/code/WorldMock/.claude/worktrees/rrs/docs/dev/SPEC-resource-and-rule-simplification.md`
- 代码基线核对：`git rev-parse HEAD` = `35d0b610fb4a35304e66619048d7500d2d1e05c7`，与文件头一致。
- 审查方法：
  1. 抽出规格里全部 `path#anchor` 引用（425 处）逐一在目标文档核对 `<a name>`：**全部存在**（含 `verification-品类契约特性门禁` 等待删除节）。脚本见 `scratchpad/design/`（临时）。
  2. 抽出全部 `path::Name` 引用（38 处）在基线源码核对定义：只有 `worldmock.life.semantics::validate_assortments` 不是定义（它是对 `corpora/storefront.py` 的调用）。
  3. 对 SYNTHESIS §2 点名的每份文档与任务点名的全部规格、合同、设计章节、手册、`tests/fixtures/environment/README.md`，按 `<a name>` 分节扫描被删概念词（真实地图/坐标/路线、供给/报价/库存、品类树、交换规则、营养、做饭/配方、字幕/分段/游戏/TextWorld、工作材料/评审/submit_work、手机配置、可见凭据、口味权重、四个删除的唤醒原因、删除的预排类型与错误码、删除的活动类型与方式、截止/预约/技能/倍速），列出规格「现行文档原位改写」各表**没有引用**的节；逐节读原文确认是否真冲突。原始扫描结果在 `scratchpad/design/conflict-scan.txt`、命中行在 `conflict-lines.txt`。
  4. 把「系统层」定稿逐条对照 `tests/integration/test_seven_layer_request.py`、`test_tool_description_references.py`、`test_life_agent_protocol.py` 的断言，以及 `tests/idle_fillers_acceptance.py` 与 `tests/life/test_paused_slots.py` 等并行活动用例登记的变异原文。

## 通过的部分（不再赘述）

- 系统层六条改写满足全部既有钉住：十八条；「上班安排」只在第 1、14 条；「待办」只在第 1、4、6、12 条；`reason` 只在第 11 条；「只是登记」「available 为 false 的会被拒绝」各一次；「同时进行的最多两项」「暂停的不算」「恢复暂停的活动要有空位」「环境只拦同一种的两件」「原来那件照常进行」只在第 8 条；「到时也会请你决策，手上的活动照常进行」「等待只留给几分钟内就有事请你决策的时候」只在第 7 条；第 14、15、16 条逐字不动；无语言指定词；`test_how_to_live_a_day…` 的禁词（评审、评分、引擎期望、budget、based_on_action_id、supply_id）不出现；`_RETIRED_IDLE_READINGS`、`_RETIRED_SLOT_READING` 不出现。
- 让「等待」成为例外登记的变异原文（「手上的活动照常进行」「不为凑钟点干等」「还没到就寝时间就照常过晚上」「正餐留到平常的饭点前后吃」、第 14 条整句插入句、`SLEEP_ALLOWED_REASONS` 末行、`WAKE_REASON_NAMES` 的四名一行）在改写后都仍唯一；`app_push` 追加在 `break_end` 之后且按同步点 2「另起一行」即可保持 `_VOCABULARY_LINE` 字节不变。
- 唤醒闭集 25 → 22 的算术与 `SLEEP_ALLOWED_REASONS` 四项核对无误；退役占用名 9 + 4 = 13 与规格一致（SYNTHESIS 写的「twelve」是错的，规格没有沿用）。
- D-33 依据的 `LifeScenario._validate_accounts`、`transport:service`、`employer:*` 角色与 `DeliveryState.seller_account_id` 机制在基线存在。

## 阻断性发现

### B1 阶段归属与指纹口径自相矛盾（阶段 4–6 不可测）

现行事实：`custom/agents/life_agent.py::life_tool_schema_sha256` 绑定 `_TOOL_MODELS` 的全部 Schema（含 `REQUEST_MODELS["submit_work"]`）与注册表字节；`custom/agents/life_prompt.py::life_prompt_sha256` 绑定 `LAYER_SCHEMAS`（`prompt_schemas.py`）；`tests/integration/test_tool_description_references.py::test_every_native_tool_has_a_purpose_only_description` 要求 `set(_PARAMETER_MODELS) == set(TOOL_DESCRIPTIONS)`；`tests/life/test_prompt_layers.py` 用 `validate_layer` 核对真实观察的工具层、进度层；`tests/integration/test_seven_layer_request.py::test_prompt_fingerprint_binds_the_callable_index_schema` 钉 `callable_index` 四键 `{lifestyle, contents, goods, query_categories}`。

规格里的矛盾：

1. 「删除清单 · 生产符号」阶段 5 行删除 `models.REQUEST_MODELS["submit_work"]`、`prompt_schemas.TARGET_TOOLS 的 submit_work`、`_RESULT.work_review`、`life_prompt.TOOL_DESCRIPTIONS["submit_work"]`，T47（阶段 5）要求「没有 `submit_work` 工具与模型」；但「实施阶段与提交」把 `custom/agents/life_prompt.py` 与 `src/worldmock/life/prompt_schemas.py` 划给阶段 6，`src/worldmock/life/models.py` 只划给阶段 2、4。
2. B-19 说 `tool_schema_sha256` 只在阶段 1、4、6 变、`prompt_sha256` 只在阶段 6 变。可是删 `REQUEST_MODELS["submit_work"]`（阶段 5）必变工具指纹；阶段 4 工具层开始出现 `kind: "app"` 目标、`callable_index` 去掉 `contents`、进度层出现 `app_push` 记录、结果出现 `details.app_content`，阶段 5 出现 `contract` 四键、`last_report`、`work_completed`，这些都在 `LAYER_SCHEMAS` 里——`prompt_schemas.py` 若按表留到阶段 6，`test_prompt_layers.py` 对真实观察的校验从阶段 4 起必红，而它不在允许暂红的清单（五个特性守卫、`test_layering_acceptance.py`、`test_current_feature_owners.py`）里；若 `prompt_schemas.py` 随阶段 4、5 改，`prompt_sha256` 就不止在阶段 6 变。
3. 阶段 5 删了 `submit_work` 工具而系统层第 2 条（阶段 6 才改）仍列出 `submit_work`，模型会被告知一个不存在的工具。

修正（择一，推荐 a）：
- a. 把 `prompt_schemas.py` 的结构部分（`TARGET_KINDS` +app −content −game −catalog、`_CALLABLE_INDEX` 三键、进度记录类型 +`app_push` −`task_deadline`、`_RESULT` +`app_content`）划给阶段 4，（`_CONTRACT` 四键、活动目标 `last_report`、+`work_completed`、−`work_review`、`TARGET_TOOLS` −`submit_work`、`REQUEST_MODELS` −`submit_work`、`TOOL_DESCRIPTIONS` −`submit_work`、系统层第 2 条）划给阶段 5；阶段 6 只剩第 7、8、12、13、17 条与查看器。B-19 改写为：「`tool_schema_sha256` 在阶段 1、4、5 各变一次（阶段 6 不变）；`prompt_sha256` 在阶段 4、5、6 各变一次」。阶段 5 文件清单补 `src/worldmock/life/models.py`、`src/worldmock/life/prompt_schemas.py`、`custom/agents/life_prompt.py`（第 2 条与 `TOOL_DESCRIPTIONS`）；阶段 4 文件清单补 `src/worldmock/life/prompt_schemas.py`；`tests/life/test_prompt_layers.py`、`tests/integration/test_seven_layer_request.py::test_prompt_fingerprint_binds_the_callable_index_schema`、`test_tool_description_references.py` 相应移到阶段 4/5 的测试行。
- b. 保持 B-19，把 T47 的「没有 `submit_work` 工具与模型」移到 T50（阶段 6），阶段 5 只删评审链与引擎侧 `submit_work` 处理，并在「门禁与平台」写明阶段 4–5 的观察不满足 `LAYER_SCHEMAS`、`test_prompt_layers.py` 暂红——这与「正式切换不暴露半套新协议」相悖，不推荐。

### B2 D-50「R4 逐字保留」与同时做两件事规格的现行条款冲突

`docs/dev/SPEC-parallel-activities.md` 裁决 R4、`spec-parallel-activities-名额落点`（「`_report_refusal` 在工作截止之后、占用冲突之前调用」）、`spec-parallel-activities-测试`（「恢复的检查次序（工作截止先于名额…）」）、`docs/design/04-simulation-engine.md#simulation-engine-校验与占用安排`（「依次核对开始地点、工作截止与合同、空名额与占用冲突」）与 `tests/life/test_paused_slots.py` 模块文档都写「工作截止与约束」。规格 D-50 说「恢复次序（地点 → 主题上限 → 名额 → 占用）逐字保留」，但第二步的文字必然改变，而前言只说 R5、R6 由本规格裁决记录取代。

修正：D-50 定稿改为「恢复次序四步逐字保留，第二步『工作截止与约束』改为『主题上限』」；前言「同时做两件事规格的 R5、R6」改为「R4、R5、R6」；阶段 5 表加两行：`docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-名额落点`（`_report_refusal` 在主题上限之后、占用冲突之前）与 `docs/design/04-simulation-engine.md#simulation-engine-校验与占用安排`（恢复次序第二步为主题上限；去掉 `channel_id`、可见凭据、游戏、观看、资格的说法）；阶段 4 表的 SPEC-parallel-activities 行把 `spec-parallel-activities-丙第8条` 写成正式表行（锚点保留，正文注明第 8 条定稿由本规格「系统层」取代、约束条不变），并加 `spec-parallel-activities-测试` 行（判据文字「工作截止先于名额」改「主题上限先于名额」、「观看占用断言」改「app 使用占用断言」）。

### B3 生活运行时规格遗漏的冲突节

以下节含被删条款，规格各表都没有引用：

| 锚点 | 冲突内容 | 建议行 |
| --- | --- | --- |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-验收矩阵` | 41 处：TextWorld、`channel_id`、`delivery_route`、`location_kinds`、`opening_*`、segment、`work_review`、下限缓解、可见期、字幕、库存、截止、报价、曝光、浏览、游戏、评审、购买渠道、资格、预约 | 阶段 8：矩阵行按本规格各节改写或删除（SYNTHESIS §2 已点名「`spec-life-runtime-验收矩阵` rows」） |
| `#spec-life-runtime-两条排除纪律` | 可排除原因列「营业关闭或未知、资格不足、无有效报价、库存为零或已下架」；软偏好一段以 `compute_taste_weights`、`attention_prior` 为例 | 阶段 3：可排除的世界事实只剩地点不可用、地点类交集为空、资源占用与时长；软偏好一段改为「画像只以散文进入人设层，不进入排序键」 |
| `#spec-life-runtime-状态归属规则` | 世界行「营业与工作日历」；工作行「任务、输入材料、截止时间、交付物、验收、合同及付款状态」；内容接触行「游戏状态及素材区间」 | 阶段 5（工作行）、阶段 4（内容接触行、世界行） |
| `#tool-interaction-工具请求-开始活动` | 方式表含「制作」行（输出归本人库存）；「不得提交…报价版本」 | 阶段 3：删制作行、去掉报价版本；阶段 4 加「打开」行 |
| `#tool-interaction-工具请求-只读查询` | 「报价未知、营业未知的条目也被滤掉」「标签由现有品类树和内容类别生成」 | 阶段 3：`available_only` 只按在场或店铺绑定 app 判定；标签 = 食品类别标签 |
| `#spec-life-runtime-加速` | 「人物选的媒体倍速与程序加速分开记录」 | 阶段 4：删媒体倍速一句（`playback_speed` 删除） |
| `#spec-life-runtime-结算间隔与续行` | 「游戏沿用明确的回合反馈边界」 | 阶段 4：改为 app 使用的内容完成是静默边界（`next_interaction_at`） |
| `#spec-life-runtime-不进入提示的字段` | 「`game_catalog` 只在人物在家且询问娱乐时以名称列表出现」 | 阶段 4：删除该句 |
| `#spec-life-runtime-社交最小可行版` | 「可交谈判定为对方清醒且不在主动游戏」 | 阶段 4：改为「对方清醒」 |
| `#time-and-energy-决策不耗时时间只在做事时流动-同一秒内的呈现与刚才` | 「本秒首轮回灌的浏览结果，在后续轮次中继续置顶」「对象只因世界事实（库存、营业、地点、在场）变化而离开窗口」 | 阶段 3/4：删浏览置顶；世界事实只剩地点与在场 |
| `#spec-life-runtime-资源表达` | 「工作需要：截止时间、合同、任务与经济目标」 | 阶段 5：改为主题与目标工时 |

### B4 人物决策与画像规格遗漏的冲突节

| 锚点 | 冲突内容 | 建议行 |
| --- | --- | --- |
| `docs/dev/SPEC-life-realism.md#spec-life-realism-location_kinds-的运行期强制语义` | 整节：`location_kinds` 必须来自地点目录的显式非空声明；分区责任交给内容条目的题材、语言与时长段 | 阶段 1：地点的活动环境由 `kind` 经 `PLACE_KIND_ENVIRONMENTS` 派生；条目侧 `location_kinds` 不变；内容分区一段删除（D-28） |
| `#persona-prompt-七层请求-工具层的紧凑编码与即时查询` | 「报价或营业未知的也滤掉」「不公开超权限报价或世界库存」「不产生可见凭据」 | 阶段 3 |
| `#spec-life-realism-呈现与导出` | 「生活活动、观看、游戏、工作…都有标题，种类名取 readable.py 的中文种类表」 | 阶段 4：改为 app 使用、工作等十个类型 |
| `#spec-life-realism-与相邻规格的边界` | 边界表「商品可见期、材料与字幕增量」 | 阶段 4 |
| `#spec-life-realism-单一行动阶段` | 「不由人格特征、口味权重或动机评分推导」 | 阶段 3：删「口味权重」 |

### B5 数字人交互规格遗漏的冲突节

| 锚点 | 冲突内容 | 建议行 |
| --- | --- | --- |
| `docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-会话与活动的关系` | 「工作活动的恢复与开工受同一截止约束：恢复的计划时长取距截止与合同上限减已累计中的较小值」 | 阶段 5：改为距主题目标与 `DERIVED_DURATION_CAP_SECONDS` 的较小值 |
| `#spec-agent-interaction-上下文分层` | 进度层 `task_deadline` 记录、「截止时刻只在工具层」、「报价版本」、不看到 `route_id`/`supply_id`/`product_id`、工作评审 | 阶段 4/5：记录类型按 B-4；禁键按「状态、事件、检查点与导出」 |
| `#spec-agent-interaction-会话字段` | 「分段区间」「截止」 | 阶段 4/5 |
| `#spec-agent-interaction-即时查询完整纠错与紧凑呈现` | 「不公开超权限报价或世界库存，不替代耗时浏览的揭示行为」 | 阶段 3 |
| `#spec-agent-interaction-与既有文档的一致性核对` | 「工作评审结果」「冻结报价」「游戏命令回路删除」行 | 阶段 5/8 |

### B6 决策上下文规格遗漏的冲突节

`docs/dev/SPEC-decision-context.md#spec-decision-context-地点发现与供给比较`（供给报价、路线预计时间、报价版本、轮转与可见期、渠道称呼）、`#spec-decision-context-即时查询操作备忘与紧凑结果`（浏览揭示、未知世界库存）、`#spec-decision-context-预算与失败`（「材料揭示与商品可见期遵循原合同」）、`#spec-decision-context-同秒续接与只读路径`（无来源事件的 `task_deadline`）。建议阶段 3（前三者）与阶段 5（`task_deadline` 删除）各加一行。

### B7 交互菜单规格遗漏的冲突节

`docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-只看此刻能做的`（「报价未知、营业未知的条目也被滤掉」「配送不通时就是空页」）、`#errands-workday-查询筛选与返回精简-执行位置与保留约束`（「不产生可见凭据」）、`#spec-interaction-menu-验收台账`（「游戏与工作动作正确区分」「工作拒绝不调用评审」「他人私有库存不泄露」）。建议阶段 3 与阶段 5 各加一行。

### B8 当日目标规格遗漏的冲突节

`docs/dev/SPEC-day-plan-goals.md#time-and-energy-零点计划与计划提醒-计划与提醒` 明写「运行清单的阶段键为 `action`、`work_review` 两个」（与 D-48 直接冲突）；`#spec-day-plan-goals-当前工具交互约束`（「普通工作从通知送达即开放，预约活动才有约定开始时刻」）；`#spec-day-plan-goals-心智模型与范围`（「环境任务截止与营业变化 → 提供决策机会」）。建议阶段 5 加三行。

### B9 让「等待」成为例外规格：遗漏的节与台账行

1. `docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-系统层`（A）给出第 7 条定稿全文「…有工作可以开始做了…」并以「任务开放」「回合制游戏只能在自己的交互边界上暂停或结束」作为引擎依据。它不是裁决原文，而是与本规格「系统层」第 7 条并存的第二份定稿。应在阶段 6 表加一行：锚点保留，注明第 7 条定稿由本规格「系统层」取代（「有工作可以开始做了」→「有 app 推送」），引擎依据的「任务开放」改「app 推送」、删游戏一句。
2. `#spec-idle-fillers-班次边界同步点` 第 5 项是 `tests/life/test_work_contracts.py` 的有序成员表（阶段 5 删除该文件）；第 9 项要求新增成员时核对第 7 条。规格「删除清单」只改了台账（[IF] 同步点 5），没有改这一节。应在阶段 4 表加一行（第 5 项改指 `tests/life/test_wake_reasons.py`；写明本规格按第 9 项核对了第 7 条）。
3. `#spec-idle-fillers-班次边界情形`「回合制游戏」行与验收台账 T10、T12（`tests/life/test_shift_edge_runs.py::test_t10_a_game_turn_after_the_edge_does_not_hide_the_edge_from_the_clock`、`::test_t12_in_a_game_the_turn_stays_and_the_game_only_stops_at_its_own_turn`，用 `_start_game` 开真实游戏）在阶段 4 失去前提。规格的「删除清单 · 测试节点」与「测试与夹具」都没有点名这两个节点和 `tests/fixtures/idle-fillers-acceptance.json` 的对应行。应补：T10、T12 改用 `use_app` 的内容完成边界（`next_interaction_at`）重写并同步登记 [IF]，`班次边界情形` 的「回合制游戏」行改为「app 使用的内容完成边界」。

### B10 接口合同遗漏的冲突节

| 锚点 | 冲突内容 | 建议行 |
| --- | --- | --- |
| `docs/CONTRACTS.md#contracts-交互菜单声明与有效菜单` | 「推进游戏、发送消息或调用工作验收」「与候选资格使用的 `location_kinds`…」 | 阶段 2（键为八个 `LocationKind`、动作取新 `START_ACTIVITY_ACTIONS`）与阶段 5（删工作验收） |
| `#contracts-day-goal-request` | 家族映射表「娱乐 \| `watch`, `play_game`, `browse`, `lifestyle:hobby`」 | 阶段 4：`use_app`, `lifestyle:hobby`（与 `spec-day-plan-goals-度量输出合同` 同一提交） |
| `#contracts-交谈与通话` | 「对方可交谈（对方清醒且未在主动游戏）」「可与观看、播客等其他种类的活动并行」 | 阶段 4 |
| `#contracts-个人事务` | 「取得」来源列「制作产出入库（活动进度的库存转换输出）」 | 阶段 3：删该项（与 `errands-workday-个人事务-完成依据与结算` 同一提交） |

### B11 设计章节遗漏的冲突节（SYNTHESIS §2 多数已点名）

| 锚点 | 冲突内容 |
| --- | --- |
| `docs/design/04-simulation-engine.md#simulation-engine-校验与占用安排` | 见 B2；另含 `channel_id`、可见凭据、库存、游戏、观看、资格 |
| `docs/design/04-simulation-engine.md#simulation-engine-验收重点` | 「工作通知」行（`task_available`、预约、关联邮件）、「唤醒」行「睡眠与游戏遵守各自边界」、「班次不改变任务开放与截止」 |
| `docs/design/04-simulation-engine.md#simulation-engine-调用并等待人物决策` | 「报价未知、营业未知的也被滤掉」「不刷新世界或浏览凭据」 |
| `docs/design/05-recording-engine.md#recording-engine-工具纠错只读查询与当天操作备忘的证据` | `appointment`、`available_at`、`RuntimeWorkTask`、截止、技能、素材、`contract.opens` |
| `docs/design/05-recording-engine.md#recording-engine-事件字段` | 观察事件字段 `content_refs`、`material_ranges` |
| `docs/design/09-io-formats.md#io-formats-职责与边界` | 链接 `contracts-品类契约与目录字段`（正文删除）、`category-contract.json` 与 `exchange-rules.json` 为独立冻结文件、库存输出绑定 |
| `docs/design/00-overview.md#overview-地图中的消费行为` | 「比较同一商品在不同门店的报价和道路路程」「当前报价、库存及营业例外由模拟引擎维护」 |
| `docs/design/00-overview.md#overview-非功能约束` | 平台行「完整放行还须合并 Linux x86_64 TextWorld 语料成功证据」 |
| `docs/design/01-architecture.md#architecture-状态归属与写入边界` | 「按供给报价与库存、营业例外」 |
| `docs/design/02-exchange-engine.md#exchange-engine-返回规则结果` | 「制作结果必须列明真实消耗…`inventory/item`」「读取…报价事实」 |
| `docs/design/03-resource-engine.md#resource-engine-职责约定`、`#resource-engine-准入失败的处理`、`#resource-engine-资源校验的实现与验收` | 「所需内容品类及引用」「品类与可达性准入」「静态可达性 `life/retrieval.py` 检索维度」「不以曝光率…」 |
| `docs/design/07-evaluation.md#evaluation-活动activitysession` | `segments` 分段区间字段 |

各行按所属阶段加入对应表（阶段 2/3/4/5），改写语义按本规格对应节。

### B12 手册遗漏的冲突节

| 锚点 | 冲突内容 |
| --- | --- |
| `docs/manual/rules.md#rules-活动声明` | `relief.channel` 的 `preference_rate`/`content_rate` 公式（有效偏好、接触次数、口味权重）、`resource_claims` 十一个名、`game_boundary`、`attention` 与 `attention_prior`、`content_kinds` |
| `docs/manual/rules.md#rules-单个人物的参数` | `aspiration.daily_effective_work_seconds`、`attention_prior` 示例 |
| `docs/manual/rules.md#rules-人物映射表` | 「接入人物卡片和口味权重」 |
| `docs/manual/scenarios.md#scenarios-修改人物身体与账户` | `entertainment_relief_preference_floor` 整段与口味权重缓解公式 |
| `docs/manual/personas.md#personas-在场景中填写人物` | 示例含 `skill_levels`、`qualifications`、「任务技能」 |

建议分别加入阶段 3（口味、下限、技能、资格）、阶段 4（活动声明、占用名、注意力）、阶段 5（`aspiration`，见 B13）。

### B13 动机层的有效工时目标缺口未裁决

基线 `src/worldmock/life/motivation.py` 以 `profile.aspiration["daily_effective_work_seconds"]`、`MotivationContext.total_effective_seconds`、`CandidateEntry.goal_effective_seconds` 与 `_work_efficiency_snapshot` 计算工作目标缺口；`src/worldmock/life/scenario.py` 要求 `motivation.aspiration` 恰好是 `daily_effective_work_seconds`。规格 W1/D10 删 `effective_work_seconds` 与效率因子，「场景结构 7」又写 `motivation` 只减 `attention_prior`，对这个键与公式只字未提——实现者无法决定是保留名字改口径、还是改名或删除。

修正：在「本规格补定」加一条（例如 B-21）：`aspiration.daily_effective_work_seconds` 改名 `daily_work_seconds`（或保留键名）并以实际工作秒数计目标缺口；`MotivationContext.total_effective_seconds` → `total_work_seconds`、`CandidateEntry.goal_effective_seconds` → `goal_work_seconds`、`_work_efficiency_snapshot` 删除；场景结构 7 的「保留」句同步；`rules-单个人物的参数` 行写进阶段 5 表；T43/T44 补一句「动机层目标缺口按实际工作秒数」。

### B14 引用了不存在的测试节点

「测试与夹具」阶段 6 行写「`test_progress_schema_registers_the_nine_record_types` 改为新计数」。基线 `tests/life/test_prompt_layers.py` 里没有这个节点，实际是 `test_progress_schema_registers_the_eight_record_types_with_both_errand_records`。按原名改写并给出新计数（八 − `task_deadline` + `app_push` + `work_completed` = 九）。

### B15 其他现行文档遗漏的冲突节

| 锚点 | 冲突内容 | 建议阶段 |
| --- | --- | --- |
| `docs/VERIFICATION.md#verification-真实性评估与结论边界` | 「工作截止事件只证明截止时间到达，已验收付款任务仍保留其真实完成状态」「游戏终局读取实际 `done`」 | 5 |
| `docs/VERIFICATION.md#verification-共享规则与可重建环境特性门禁` | 「Linux x86_64 的 TextWorld 语料验收必须另外成功」 | 4 |
| `docs/ARCHITECTURE.md#architecture-必须达到的行为` | 「职业、技能、工作机会和合同影响产出或报酬」「覆盖饮食、节目、游戏、工作记录…」 | 5 |
| `docs/dev/SPEC-layering-and-test-tiers.md#layering-imports` | 导入图行「`models` 与 `map_models` 从同一处导入」「工作材料校验」 | 1/5 |
| `docs/dev/SPEC-layering-and-test-tiers.md#layering-tiers` | 「需要…TextWorld 执行的用例标为 external」 | 4 |
| `docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-文档原子同步清单` | 00-overview 行的 TextWorld 门禁；`SPEC-category-contract.md` 链接 | 1/4 |

## 次要发现

| 编号 | 位置 | 问题 | 修正 |
| --- | --- | --- | --- |
| M1 | 删除清单 · 生产符号 阶段 1 `worldmock.life.semantics` 行 | `validate_assortments` 定义在 `corpora/storefront.py`，`semantics.py` 只是调用；文档门禁只认定义处的 `path::Name` | 写成「`worldmock.life.semantics` 对 `corpora.storefront::validate_assortments` 的调用」 |
| M2 | D-23、阶段 4 表 SPEC-idle-fillers 行 | 引号里的「成员永不删除」不是原文；`spec-idle-fillers-班次边界规则` 原句是「由 21 个增到 25 个；名称与次序落地后永久冻结」 | 引用原句，改写后写「由 25 个改为 22 个；成员只经版本表与九个同步点增删」 |
| M3 | 阶段 8 表 `implementation-status` 行 | 未点名 `implementation-status-个人事务与工作安排开发状态`（世界 9、截止、评审）、`-人物-prompt-七层开发状态`（`work_review`、浏览）、`-共享规则与可重建环境开发状态`（TextWorld）、`-同时做两件事开发状态`（截止、播客）、`-规格追溯与突变守卫`、`-历史静态可达性缺陷记录`、`-候选特性历史门禁输出原命令与关键行` | 在同一行补这些节：被取代处改写为历史并指向本规格 |
| M4 | 阶段 8 表 `ANALYSIS-spec-coverage` 行 | 「退役行从五个特性的映射删除」没有点名 `analysis-spec-coverage-个人事务与工作安排增量映射`、`-人物-prompt-七层增量映射`、`-时间与能量增量映射` | 把三个锚点写进该行 |
| M5 | 阶段 1/3 表 | `docs/EXPERIMENT.md#experiment-实验身份与范围`「世界日历只约束营业、工作机会和消息到达」；`docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明`「命名与单位沿用营业日历」；`docs/manual/README.md#readme-修改配置前要知道的约定`「营业时间等特殊单位」；`docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-摘要安全路径持久写入与错误`（品类身份、工作材料、出行响应与文字游戏的入口） | 各加一行，措辞改写 |
| M6 | 阶段 4 表 | `docs/dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-共同时间与身体结算`「游戏交互」、`#spec-dayrun-behavior-事件投影` played 行「目录浏览」；`docs/dev/SPEC-life-runtime.md#time-and-energy-能量不拦人-让人物趁早吃`「游戏按回合」、`#time-and-energy-会话活动与记忆提示中不出现内部身份-活动的生命周期` 边走边听播客例子、`#spec-life-runtime-数据进入环境的过程`（字幕、文字游戏）、`#spec-life-runtime-运行观测`「重复观看」 | 各加一行 |
| M7 | 阶段 4 表 `footprints/models.py::WAKE_REASON_NAMES` | 只写「`app_push` 追加在 `break_end` 之后」，没有写「另起一行」；`tests/idle_fillers_acceptance.py::_VOCABULARY_LINE` 登记的四名一行必须字节不变 | 在「闭集」表或阶段 4 行写明「`app_push` 另起一行，现有末行逐字节不变（同步点 2）」 |
| M8 | 裁决早期备忘表 W2 | 写「不能再开始已完成主题的码见 B-6」，B-6 说不设新码、用 `ACTIVITY_DURATION_INVALID`；一致，但 `WORK_TOPIC_COMPLETED` 在 W2 行文里像是存在的码 | W2 行补「（`WORK_TOPIC_COMPLETED` 不设）」 |
| M9 | 阶段 4 表 SPEC-parallel-activities 行 | `spec-parallel-activities-名额情形`「名额被一项通话与一项观看占满」例子 | 与 `tests/life/test_paused_slots.py::test_full_slots_with_a_call_say_pause_only_the_pausable_one` 的改写同一行点名 |

## 无需处理（核对过的非冲突）

- `docs/CONTRACTS.md#contracts-labelkit-*`、`contracts-运行报告`、`contracts-窗口声明与字段字典编码`、`docs/design/06-delivery.md`、`docs/design/07-evaluation.md` 其余节、`docs/manual/delivery.md`、`docs/manual/evaluation.md` 的「评审」都是 LabelKit 语义评审，不是工作评审。
- 各文档「版本表/版本历史」节出现旧词属历史记录，规格前言已约定不回改。
- `docs/dev/SPEC-category-contract.md` 全文整体归档，已覆盖。
- `docs/dev/SPEC-environment-data-ownership.md` 没有 `<a name>`，规格按标题引用可行。
