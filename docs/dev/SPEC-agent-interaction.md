# 特性规格：数字人交互（会话层、上下文分层与稳定工具面）

> 状态：**FROZEN**；2026-09-23 按 issue #1 的运行证据及用户裁决修订；2026-09-24 按[时间与能量规格](SPEC-time-and-energy.md)修订，修订部分已实现并通过离线验收（证据见[时间与能量验收记录](research/time-and-energy-verification.md)）；2026-09-27 按人物 Prompt 规格修订（[人物 Prompt 规格](SPEC-persona-prompt.md)「冻结条款修订」第 7、8、12、13、15、16、20–22、24、26 行），修订部分已实现并通过离线验收（证据见[人物 Prompt 验收记录](persona-prompt-verification.md)）；2026-09-29 按[一日运行保真规格](SPEC-dayrun-fidelity.md#冻结条款修订)修订（「冻结条款修订」第 10、11、13、14、18 行与其 3.5：控制工具改为 `items`、按名调用、指名购买与 `prefer`、名称类拒绝不计轮、公开结果键），修订部分已实现并通过离线验收。
> 来源：[数字人交互方案](PROPOSAL-agent-interaction.md)。裁决后本规格取代该提案的全部行为定义。
> 字段与 Schema 由 [接口合同](../CONTRACTS.md) 承载；设计职责由既有设计文档承载。
> 本规格显式修订与旧交互工具、观察结果、预算和业务拒绝相关的冻结条款，见「冻结条款修订」。
> 原先阻断冻结的对象动作绑定、登记工具、目标层进展归属均已裁决；本版按最新裁决执行。
> 本规格描述最终行为；实现状态及文档同步以验收台账逐项核对，不能把规格冻结视为已实现。

> 2026-10-01 按[工具交互规格](SPEC-tool-interaction.md)修订当前工具请求、查询与错误合同；本次设计已冻结，生产代码已实现，旧验收证据不覆盖新增条款。
> 本特性首次受影响的发布升级为场景 4、地图 3、目录 5.0、品类与规则绑定 3，世界结构仍为 8。各阶段独立发布；后续格式或内容再次变化时另升相应版本，禁止同目录版本覆盖内容。

## 心智模型

人物过日子不是「一次做一件事、做完再想」，而是**同时挂着若干件事、隔一阵回看进展**。
系统掌握时间、身体、报价、结算与资源；人物选择可读对象和动作，撰写自己的进展与通信内容。

```mermaid
flowchart TB
    subgraph 三级["三级结构"]
        T["当日目标：我今天想推进什么<br/>（goal，零点计划生成）"]
        S["会话：一段地点停留或一段路<br/>（session，系统开始与结束）"]
        A["活动：真正占资源、被结算的最小单元<br/>（activity，占一个名额）"]
        T -->|"在一段段停留与路上逐步推进"| S
        S -->|"包含其间开始的活动与归入的暂停活动"| A
    end
    subgraph 归属["谁写什么"]
        SYS["系统：时钟 / 结算 / 资源 / 前置条件<br/>→ 不可改字段"]
        MOD["模型：在做什么、到哪了、卡在哪<br/>→ 自然语言自述字段"]
    end
    A --> SYS
    A --> MOD
```

**一条硬边界**：模型写的任何文字都不承载状态转移。活动的状态由系统字段推出；会话随出发与到达切换；
模型的进展自述只改变「人物怎么理解这件事」，永不作为完成证据。

**模型与引擎的执行契约**：`start_activity` 提交 `items[{target, way, from, to}]` 一至两项与请求级 `answering`、`prefer`，
每项直接关联同一对象的方式与来源去向；`items[i].way` 唯一推导内部动词，人物不再提交 `actions`。来源与去向按方式判定，食用、使用与制作的两端为 `null`。模型可读取系统公开的
路线方式、报价和前置条件以作选择，但不回填价格、报价版本、时长、数量或内部身份。
引擎从产生该次观察的冻结状态解析对象和动作，并计算执行参数。已结算动作的公开结果属于
**进度层**：系统写客观结果，人物写主观进展；这不增加上下文层。

## 权威来源与范围裁决

| 内容 | 权威 |
| --- | --- |
| 会话、决策轮循环 | 本规格 |
| 人物工具 Schema、字段闭集、枚举 | [接口合同](../CONTRACTS.md) |
| 状态归属、结算与资源、共享时钟 | [life-runtime 规格](SPEC-life-runtime.md) |
| 决策所见（七层请求与进度层）、单一行动阶段、同秒续接、预算 | [人物 Prompt 规格](SPEC-persona-prompt.md)；动作登记与结果通道见 [decision-context 规格](SPEC-decision-context.md) |
| 工具与活动类型准入、地点菜单 | [interaction-menu 规格](SPEC-interaction-menu.md) |
| 记忆合同、巩固与反思 | [life-realism 规格](SPEC-life-realism.md) |
| 当日目标 | [day-plan-goals 规格](SPEC-day-plan-goals.md) |
| 事件、检查点、导出 | [记录引擎设计](../design/05-recording-engine.md) |

**范围**：三级结构、活动生命周期、上下文分层与进度层、稳定工具面、决策轮循环与播报节律、
前置条件前置与前往提醒、并发上限、容量与午夜浓缩、游戏简化。

**跨规格边界**：评估、LabelKit 交付、画像、语料、品类与账本的领域规则分别由其规格负责；
本规格要求交付链读取与实际模型请求一致的公开投影，不能再从原始观察重组一份不同的输入。

## 三级结构与概念边界

系统里**已经有**三个概念：当日目标与活动各有生命周期，会话是按地点停留与路段分组的组织单位；本规格**不新增第四个**，而是明确它们的从属关系。

| 概念 | 谁创建 | 谁结束 | 可暂停 | 进展来源 | 生存期 | 状态位置 |
| --- | --- | --- | --- | --- | --- | --- |
| **任务**（当日目标 `PlanGoal`） | 零点计划 `plan_day`（人物可 `revise_day_plan` 修订） | 系统（跨日失效 `day_end`） | 否 | 环境活动证据（语义不变） | 当日 | `AgentState.day_plan`（冻结，不改） |
| **会话**（`SessionState`） | 系统（运行开始、出发、到达、途中结束出行） | 系统（出发、到达、途中结束出行、实验截止） | 否 | 无（组织单位：进度层 `events` 只含当前会话，`rejected_today` 保留当天跨会话拒绝备忘，结束的会话派生为记忆层的一条） | 一段地点停留或一段路 | `WorldState.sessions`（**新增**） |
| **活动**（`ActivityState`） | 人物（`start_activity`） | 系统（计划结束、提醒结束睡眠、到达或途中结束出行、交谈对方结束）或人物（`conclude_activity`）；系统结束的活动须在同一秒收尾 | 是（路上的活动与交谈、通话除外；只在开始地点恢复） | 结算证据 + 模型自述 | 开始至收尾，占一个名额 | `WorldState.activities`（增字段，见「会话字段」） |

### 任务与会话的关系

**任务由活动逐步推进，活动分属一段段会话。** 活动携带 `task_kind` 与 `task_ref` 指向它服务的任务：

| `task_kind` | `task_ref` 取值 | 说明 |
| --- | --- | --- |
| `"goal"` | `PlanGoal.intent_kind`（如 `"工作"`） | 当日目标；跨日随 `day_plan` 归零 |
| `"work_task"` | `task_id`（如 `backend-hourly-2026-01-05`） | 场景声明的多日工作合同 |
| `"none"` | `null` | 不服务任何已知任务（如临时散步） |

**目标层的证据规则不变**：`PlanGoal` 仍只由「正时长结算的活动」推进，`progress_activity_id` 仍是单值。
**会话不参与目标证据的判定**——它只是按地点停留与路段分组的组织单位。
（推论：服务同一目标的多项活动不会各自改写目标证据，目标证据仍由第一个匹配活动一次性盖章。）

### 会话与活动的关系

**每人两个名额，进行中、暂停中与待收尾的活动各占一个**；只有 `conclude_activity` 之后名额才释放。
**一个会话是一段地点停留或一段路**，由系统随出发与到达开始和结束，人物不手动开始或结束会话。
会话结束前，人物要结束或暂停其中仍在进行的活动：出发前须结束或暂停；路上开始的活动在到达或途中结束出行时
由世界结束，成为待收尾；暂停的活动随地点保留，回到该地点时归入那里的新会话并可恢复。

**一个活动可分多段完成**。「多段」在记录里对应既有的**暂停/恢复语义**，不新增概念：
一个 `ActivityState` 记录跨多次暂停/恢复累积 `elapsed_seconds`，
暂停期间不计入；每次恢复重算 `planned_end_at`。工作活动的恢复与开工受同一截止约束：恢复的计划时长取距截止与合同上限减已累计中的较小值，
不足 60 秒以 `ACTIVITY_DURATION_INVALID` 业务拒绝。

```mermaid
stateDiagram-v2
    [*] --> 进行中: start_activity（占一个名额）
    进行中 --> 进行中: report_status（每 1800 秒播报，带自述）
    进行中 --> 暂停: pause_activity（带自述）
    暂停 --> 进行中: report_status（带自述，在开始地点恢复）
    进行中 --> 待收尾: 计划结束 / 提醒结束睡眠 / 到达或途中结束出行 / 交谈对方结束
    待收尾 --> [*]: 同一秒 conclude_activity（带自述，释放名额）
    进行中 --> [*]: 人物主动 conclude_activity
    暂停 --> [*]: 人物主动 conclude_activity
    进行中 --> [*]: 实验截止（truncated，不收尾）
    暂停 --> [*]: 实验截止（truncated，不收尾）
```

**会话状态闭集**：`active` / `ended`；`kind` 取 `stay`（地点停留）或 `transit`（路上）。

### 会话的状态位置

会话住在 `WorldState` 内，随 `life` 快照一起提交与恢复——检查点参与者键集是
`{"life"} ∪ {agent:N}`（`footprints/validation.py:110-111`），**会话不需要新的快照键**。

| 项 | 值 |
| --- | --- |
| 字段 | `WorldState.sessions: dict[str, SessionState]`，带 `default_factory` |
| 结构版本 | `WORLD_SCHEMA_VERSION` **5 → 6**（本规格引入会话）；时间与能量规格升为 **7**（地点会话与活动新增字段）。定义在 `src/worldmock/life/models.py:24`，`WorldState.schema_version` 是 `Literal[WORLD_SCHEMA_VERSION]` 且**无默认值** |
| 场景声明 | **不需要**（会话是运行期生成物，同 `activities` / `deliveries`） |
| 旧检查点 | **拒绝加载**：`schema_version` 是 `Literal[WORLD_SCHEMA_VERSION]` 且**无默认值**；**不提供迁移或降级读取路径**，既有运行目录的检查点一律视为作废，需重跑而非恢复 |

## 会话字段

```json
{
  "session_id": "internal-session-key",
  "agent_id": 1,
  "kind": "stay",
  "location_id": "internal-location-key",
  "status": "active",
  "started_at": 1767585601,
  "ended_at": null,
  "activity_ids": ["activity-...-action:0001"],
  "name": "张江办公室（1月5日 12:00 起）"
}
```

| 模型与字段 | 归属 | 说明 |
| --- | --- | --- |
| 会话 `session_id` / `agent_id` / `kind` / `status` / `started_at` / `ended_at` | **系统，不可改** | 会话身份与生命周期；`kind` 取 `stay`（地点停留）或 `transit`（路上），`status` 取 `active` 或 `ended` |
| 会话 `location_id`（`stay`）或 `origin_id`、`destination_id`（`transit`） | **系统，不可改** | 停留地点，或这段路的出发地与目的地 |
| 会话 `activity_ids` | **系统，不可改** | 这段停留或路上开始的活动，以及回到该地点时归入的暂停活动 |
| 会话 `name` | **系统从冻结场景和开始时刻生成** | 停留「<地点>（<M月D日> HH:MM 起）」，路上「去<目的地>的路上（<M月D日> HH:MM 起）」 |
| 活动 `name` / `session_ids` / `concluded_at` / `report_anchor_at` | **系统，不可改** | 活动名「<做什么><对象>（<M月D日> HH:MM 起）」；先后所属的会话；收尾时刻；播报锚点 |
| 活动 `task_kind` / `task_ref` / `plan_goal_intent_kind` | **系统，不可改** | 从原会话移来，指向活动服务的任务或当日目标 |
| 活动 `progress_note` / `note_updated_at` / `note_history` | **模型撰写** | 从原会话移来；结论固定标**未经核验** |

会话上不再有任务、承载对象、分段区间、累计秒数、前置条件与自述字段；客观进度取活动随结算累加的秒数与任务累计，
进度与自述都挂在活动上。

上例是**引擎私有状态**，字段名和身份不得原样序列化给模型。模型所见的进度层的 `events` 只含当前 session 的有序记录：
调用连同公开结果、收到的消息、送达、截止与提醒；活动的客观进度在工具层活动目标的 `state`，暂停的活动在待办层，
更早的 session 在记忆层。原始结果、内部身份、活动分段、账户状态和检查点只保存在事件与世界状态；模型只得到可理解且允许公开的业务反馈。
人物写下的内容（`self_note`、`self_notes`）与环境核验的结果分开呈现，是否经核验由系统层规则统一说明。

| 容量 | 上限 | 承载物 |
| --- | --- | --- |
| **进展自述** | 500 字符 | **活动级**：`ActivityState.progress_note`，由模型在 `report_status` / `pause_activity` / `conclude_activity` 对应活动那一项的 `items[].summary` 里撰写 |
| **目标单句进展** | 500 字符 | **目标级**：关联当日 `PlanGoal.progress_note`（保留）；控制工具提交时写入同一段原文，仍是独立字段；照存，不进提示，待办层的目标行不显示人物自述 |
| 记忆层 | 8000 字符 | 记忆层 JSON 的窗口（新常量），**不删任何记录**（见下）；与整次请求的 85% 阈值分开 |

进度层的 `events` 只含当前 session，**没有固定的记录数量上限或总字符上限**。其 JSON 按下文的整次请求 token
阈值浓缩，权威会话和事件保持完整；不得静默截去已结算反馈。

**「进展自述」与「目标单句进展」属于两个独立对象**：前者挂在活动上、随活动收尾而归档；后者挂在当日目标上、跨日随目标归零。控制工具遇到关联当日目标的活动时，将对应原文同时写入两个对象；未关联目标时只更新活动。二者恰好共用 500 字符这一上限，客观目标证据仍仅由正时长活动结算推进。同一段 summary 在本 session 内是进度层中该调用的参数，session 结束后是记忆层的 `self_notes`；目标上的原文不进提示。

## 稳定工具面

世界请求工具为七个：四个活动动作工具、一个当日目标登记动作工具，
以及独立的工作交付和消息正文工具。工作成果与通信内容仍由人物创作；它们不挤进
`start_activity` 的对象与动作选择接口。

**名词定义**：**活动动作**指开始、播报、暂停与结束活动——`start_activity`、`report_status`、`pause_activity`、`conclude_activity`（会话是地点停留或一段路，由系统随出发与到达开始和结束，人物不手动开始或结束会话，见[时间与能量规格](SPEC-time-and-energy.md)第七章）；**登记动作**只剩 `revise_day_plan`，它只修订当日计划及其提醒、不启动活动、不消耗生活时间；当日计划由运行时的零点后台任务 `plan_day` 生成，`plan_day` 不是人物工具（见[时间与能量规格](SPEC-time-and-energy.md)第四章）。
任务回看、世界查询与记忆查询工具不是生活动作；按条目身份读取的 `memory_read` 删除，查询结果不带条目身份（时间与能量规格第七章）。生活决策没有 `finish`：一轮决策恰好一个世界请求，登记即结束本轮；人物的只读问答保留 AS2 的 `finish`。

原 17 个工具里的游戏命令和播放中变速删除；`revise_day_plan` 继续独立，`plan_day` 移出人物工具面，改为零点后台任务。
`submit_work` 和 `send_message` 使用独立工具名并复用既有领域处理器；不存在旧工具的兼容入口。

| 工具 | 可用条件 | 收 | 语义 |
| --- | --- | --- | --- |
| `start_activity` | 始终可用（同刻上限的受限轮除外） | 必填 `items[{target, way, from, to}]` 一至两项，必填可空 `answering`、`prefer`；含购买项偏好为「最快拿到」或「到手价最低」，无购买项为 `null` | 方式唯一推导内部动作；整组原子受理，旧平行数组与 `actions` 拒绝 |
| `recall_task_summary` | 查询回合预算允许时 | `task_name: str` | 即时只读回看本人已知的任务、当日目标或活动；不登记世界请求、不计同刻轮数，可与其他查询同批 |
| `query_world` | 查询回合预算允许时 | 必填 `name: str\|null`、`way: str\|null`、`category: str\|null`、`offset: int`，前三者至少一个非空白，偏移非负 | 查询同一冻结权限内全集，按二十项分页，不揭示新世界事实 |
| `report_status` | 有进行中或暂停的活动时 | `items: list[{activity_name, summary}]`（1–2 项；一日运行保真规格第六章，原为等长的 `task[]`、`summary[]`） | 各项对应一项活动；播报进行中活动；点名暂停的活动即恢复它，恢复只在开始地点 |
| `pause_activity` | 有进行中活动时 | `items: list[{activity_name, summary}]`（1–2 项） | 各项对应一项进行中活动；暂停所选活动，暂停仍占名额；路上的活动不能暂停 |
| `conclude_activity` | 有进行中、暂停或待收尾的活动时 | `items: list[{activity_name, summary, memories, views}]`（1–2 项；`memories` 为普通记忆，没有 `subject`；看法与偏好放 `views`，`about` 写对象，宿主转为存储的 `subject`；两者都必填可为空，合计不超过八条） | 各项对应一项活动；结束所选活动并写总结与记忆，释放名额；未选活动保持原状 |
| `submit_work` | 有可见的未付款任务时 | `task_name: str`、`deliverable: MeetingMinutes` | 提交人物撰写的成果，执行现有验收与返工结算；未达标以 `WORK_INCOMPLETE`、正在做的任务以 `WORK_TASK_IN_PROGRESS` 业务拒绝；结果归进度层 |
| `send_message` | 可联系人物可见时 | `recipient_name: str`、`text: str` | 发送人物撰写的正文，执行现有通讯结算；结果归进度层 |
| `revise_day_plan` | 当日已有计划时（**含空清单**） | `goals[]`（替换全部未推进目标；每条带必填可空的 `remind_time`） | **修订**当日目标及其提醒；提醒按时间与能量规格第四章保留、换成新提醒或取消 |

世界请求工具与查询工具始终都在原生 `tools` 中，定义整次运行不变（例外是本次决策只剩最后一个回合时与输出截断后的那次重试时去掉全部查询工具，后者见[一日运行保真规格](SPEC-dayrun-fidelity.md) 7.2），参数不带逐轮名称枚举；「可用条件」不满足时，工具层把该工具标为
`available=false` 并写 `reason`，提交时以同一原因业务拒绝。名称的合法性由宿主本地校验与环境受理判定。

`task_name`、各项的 `activity_name` 与 `recipient_name` 都取本轮七层中出现的唯一可读名称。
同名对象由类型、地点或自然语言开始时间消歧，不能用内部编号、哈希、奇怪字符或索引后缀。
三个活动控制工具的 `items[].activity_name` 只取活动名（「做什么＋对象＋开始时刻」，开始时刻用绝对日期；同一轮可见的名称重复时
追加「（第二次）」这类序号）；`recall_task_summary.task_name` 取任务、当日目标或活动名（名称只取七层中出现的，会话名
不在其中）；`submit_work.task_name` 仍取任务名；各类名称共享同一唯一性检查，冲突时不设隐含优先级。
名称至内部身份的映射只存于私有观察绑定，提交时按原观察解析；写得不准的名称按[一日运行保真规格](SPEC-dayrun-fidelity.md) 3.1
无损归一（全半角与括号、前缀、活动名里的开始时刻后缀等），归一后唯一即受理；仍不存在或不唯一则以名称类错误指出可照填的候选，
绝不任选一个、不按相似度替换。`items` 一至二项且活动不重复。
`summary` 和 `deliverable` 是人物原创内容，
不是系统状态回填；`revise_day_plan` 的目标内容仍由人物撰写。
播报先校验全部活动名与阶段，接受进行中或暂停中的活动；暂停只接受进行中的活动，路上的活动除外；结束接受
进行中、暂停中或待收尾的活动。整组通过才依对应关系写入各活动的 `progress_note`，有关联当日目标时把同一原文写入
该目标独立的 `progress_note`（照存，不进提示）；任一项不合法则整组业务拒绝且世界状态不变。
播报暂停中的活动即在其开始地点恢复它；结束只结束所选活动，未选活动保持原状。
没有可结束的活动时结束工具业务拒绝。有待收尾活动时做别的（包括只结束其他活动），以 `ACTIVITY_CONCLUSION_REQUIRED`
业务拒绝并点名先要收尾的活动。

**`plan_day` 与 `revise_day_plan` 保留为独立工具**（用户裁决，2026-09-22；已被取代，见段末）：
二者是**登记动作**，不启动活动、不消耗生活时间，与「会话动作」语义不同，
因此不属于 `start_activity` 可推导的生活动作。其请求形状、生命周期与修订规则不变，
沿用 [当日目标规格](SPEC-day-plan-goals.md) 与 [接口合同](../CONTRACTS.md)「当日目标请求」。
本段已由[时间与能量规格](SPEC-time-and-energy.md)第四章取代：`plan_day` 改为零点后台任务，登记动作只剩
`revise_day_plan`，条目增 `remind_time`。

### 对象与动作选择

冻结观察的工具层 `targets` 每项提供唯一可读的 `name`、`kind` 与 `actions[]`：每个动作是 `{action, way, available, reason, price, duration,
quantity, note}`，`action` 取五个动词，`way` 取方式闭集，不可用时 `reason` 写环境受理时的拒绝原文（可用时为 null）。
人可以直接理解每个活动，例如「地点：张江办公室」配 travel 的步行与驾车，「门店出售的午餐套餐」配 obtain 的到店与远程下单，
「工作：项目周会纪要」配 interact 的工作，「内容：Sintel」配 interact 的接着看。方式携带人物真正需要选择的出行方式和履约方式；
票价、版本、时长、库存、数量由系统在 `price`、`duration`、`quantity` 中告知或内部计算，不成为模型参数。`submit_work` 和
`send_message` 不列为 `start_activity` 动作；其名称、成果、正文由独立工具处理。

`obtain`、`consume` 另用 `items[i].from`、`items[i].to` 写来源与去向，其他动词两者都为 `null`：`obtain` 的 `from` 是可选约束，取 `null`、门店名或渠道称呼
（目标是完整标签「<门店>出售的<商品>」时为 `null`、该门店或包含该门店的渠道；方式为制作时为 `null`；其他写法报 `SOURCE_NOT_FOUND`），
方式可写「购买」由系统定到店或远程下单，多于一家可服务时按请求级 `prefer` 选定（一日运行保真规格 3.5），
`to` 为 `null`（本人）或他人收货人（写人物目标的名称，如「人物：王磊」），制作的 `to` 为 `null`，由引擎确定本人去向；
食用、使用与制作的 `from`、`to` 均为 `null`，本人来源与去向由引擎推导；给予的 `from` 为 `null`，`to` 为人物名。`to` 的候选是 `null`（本人）或工具层的他人人物目标，写目标的名称；不在工具层的同世界人物也可写「人物：<名>」
（私有 `person_bindings`，给予他以不同处拒绝，配送给他送到其所在地点）；写的名称不是任何人物时以 `RECIPIENT_NOT_FOUND` 业务拒绝；收货地点不作参数。请求级 `answering` 填一条可见直接消息的
规范名称「<发件人> HH:MM 的消息」，只关联 `to` 等于该消息发件人的那几项。

```mermaid
flowchart LR
    O["可读对象与方式<br/>模型可见"] --> S["start_activity(items, answering, prefer)<br/>逐项配对"]
    S --> B["本轮冻结观察的私有绑定"]
    B --> V["引擎校验占用与准入事实<br/>计算时长、数量、当前报价"]
    V -->|受理| A["启动活动并结算"]
    V -->|业务拒绝| R["公开原因及可执行建议<br/>下一次决策继续"]
```

冻结观察中的私有 `interaction_bindings` 只供宿主解析：（可读对象，动词，方式）映射到内部对象身份、
标准动作、路线方式、履约方式、`use_mode` 及该对象的冻结报价。`obtain` 的目标不在本轮列出时按场景供给指名解析
（一日运行保真规格 3.2、3.3、3.5）：分段匹配依次报 `SUPPLY_NOT_FOUND`（没有这件商品，附至多 3 个可照填的候选）、`SUPPLY_NOT_AT_STORE`、
`FULFILLMENT_NOT_SUPPORTED` 与 `ACTIVITY_PRECONDITION_UNMET`；多于一个可服务的匹配时按 `prefer` 选定一家并写进 `details.notes`，
不再报 `SUPPLY_NAME_AMBIGUOUS`，不自动替换。其余动词的目标也可以直接写名称：宿主与环境共用同一名称解析，无损归一后唯一即受理，
否则以名称类错误给可照填的候选（宿主判定的在本轮按协议纠错重试）。该绑定不进入模型提示或工具返回。
私有 `activity_bindings` 把本轮可读的活动名映射到内部身份（活动绑定另带阶段，同一轮重名时追加「（第二次）」这类
序号；另带 `note_labels`：记忆层 `self_notes` 中这项活动去掉开始时刻后缀的活动名，只作回看名称）；私有 `goal_bindings` 的名称就是
待办层写出的目标文字；会话名不在七层中出现，不绑定。
活动绑定另带 `resumable`：暂停的活动此刻能否恢复（人物正停留在它的开始地点）；人在别处时待办层把恢复标为不可用并点名开始地点，
提交时以 `ACTIVITY_PRECONDITION_UNMET` 业务拒绝，原因与待办层所写一字不差。
对同一个对象的多个合法动作逐一展示；`items[i].target`、`items[i].way`、`items[i].from` 与 `items[i].to` 逐项对应，
每个对象只需支持自己选中的动作；同一请求中只有 `to` 不同时才允许重复目标。两项可以是不同动作，例如步行与观看；
引擎验证组合能否并行，任一项不合法则整组不启动。
每项出行都须对应自己的目的地；每项购买都读取该供给自己的冻结报价（指名购买本轮未列出的商品按当前报价），不能把一件商品的
报价版本套给另一件。所选对象已不在本轮绑定或绑定不唯一，返回业务拒绝，不猜内部身份。

**引擎派生参数政策**：

| 动作 | 不由模型填写的执行参数 |
| --- | --- |
| 前往 | 由所选目的地与方式解析冻结路线，使用路线精确秒数和票价；展示分钟数仅供阅读 |
| 工作 | 时长取剩余合同秒数、距截止秒数与活动允许上限中的最小值；距截止与剩余合同都不少于 60 秒才开工；恢复同样受截止约束，计划时长取距截止与合同上限减已累计中的较小值，不足 60 秒以 `ACTIVITY_DURATION_INVALID` 业务拒绝；30 分钟播报边界可在活动进行中唤醒 |
| 观看 | 「接着看」取剩余内容秒数；已看完或剩余不足 60 秒时为「从头再看」，取完整内容时长；超过 86400 秒按 86400 秒受理；速度固定为 1 倍 |
| 游戏内容 | 取内容剩余时长，超过 86400 秒按 86400 秒受理；场景游戏取场景 `activity_feedback_seconds` |
| 生活活动 | 取目录 `typical_duration_minutes` 换算的秒数，超过 86400 秒按 86400 秒受理 |
| 浏览、阅读邮件、等待 | 取场景 `activity_feedback_seconds` |
| 交谈、通话 | 由场景 `activity_feedback_seconds` 派生后截取（通话 600–3600 秒、交谈 60–7200 秒）并取整到完整分钟，不再受请求模型 120 分钟上限约束 |
| 睡眠 | 人物当前 `sleep_need_hours × 3600`，按最近整数秒取整 |
| 购买、食用或使用持有物 | 购买每个所选对象数量为一份，使用各自冻结报价（指名购买按当前报价）及所选动作对应的履约方式，订单只有一个收货人，送达只入收货人的持有；持有物从目录 `use_modes` 展示「食用」或「使用」，引擎按冻结绑定的 `eat` / `use` 结算：开始时每件取 `min(1, 手边持有)` 移入本次活动，不足一份也可以吃，进食时长为 `max(60, round(整份时长 × 份量))` 秒，摄入只按开吃份量与实际进度计算；暂停保留开吃份量的剩余，结束丢弃剩余、不补算摄入，其他份数不动；余额、库存、地点和组合相容性在结算时重验 |
| 给予 | 瞬时，不占名额，无时长；从本人手边移出每件 `min(1, 持有)` 记入对方持有，任何人都不增加摄入；要求同处（同世界人物在同一地点；联系人则本人位于其收货地点） |

时长换算为正整数秒后，非出行活动最短 60 秒（进食时长的校验按份量折算）；观看、游戏内容与生活活动超过 86400 秒按 86400 秒受理并在结果写明
计划时长；出行超限以 `ROUTE_DURATION_INVALID` 拒绝；占用、地点、截止时间等准入事实照常校验，能量不参与。
无法成立时返回说明具体约束的业务拒绝（带原因与下一步），不改用猜测值。
无时长动作不虚构活动时长。领域内部请求仍可带 `planned_duration_seconds`、
`price_version` 等字段，但由引擎从同一冻结观察填充，模型永不回填。

`from=null` 在购买时表示不额外限定卖方或渠道，完整商品标签仍限定标签门店；其他方式的来源由引擎推导，只允许 `null`。
`to=null` 始终表示本人，购买收货地沿用当前地点规则，不等于本人住处；只有购买或给予给他人时写真实人物名称。
食用、使用、制作及其他方式的 `to` 只允许 `null`；给予必须写他人，不能给予本人。
不接受在 `to` 填「自己」或本人姓名的旧写法；出行目的地由 `target` 与方式确定，`from/to` 不承载路线起止地点。

**Schema 约束**：`start_activity` 只包含必填 `items[{target, way, from, to}]` 一至两项与必填可空的 `answering`、`prefer`。
每项四键显式必填，`from`、`to` 是 `str|null`；`prefer` 显式必填可空：只要含「购买」「到店」或「远程下单」项，就必须写「最快拿到」或「到手价最低」；没有购买项时必须为 `null`。`null` 不再默认最快，不接受旧英文或短名。
`items[i].way` 的合法性按 `items[i].target` 对象本轮列出的方式验证（`obtain` 指名本轮未列出的商品时按指名购买解析），
`from`、`to` 的组合按方式唯一推导的内部动词验证；参数不带逐轮名称枚举，不能在模型工具 Schema 中加入动作专属参数。

DeepSeek strict 模式**禁用六个 Schema 键**（`life/models.py:1161` `_DEEPSEEK_UNSUPPORTED_SCHEMA_KEYS`）：
`oneOf`、`minLength`、`maxLength`、`minItems`、`maxItems`、`default`。

由此产生一条硬约束：**字符上限与「至多 2 个」的并发上限都不能写在工具 Schema 里**，
只能在提示词与业务规则中表达。故并发上限的 Schema 侧无约束，
`SESSION_LIMIT_EXCEEDED` 由业务规则独立兜住。
所有属性必须必填（strict 模式要求），本地请求按严格 JSON 类型及各字段既有范围、格式校验，不进行类型转换，不设缺省值（`default` 亦在禁用清单内）；`conclude_activity.memories` 的数量与
长度上限同样只由本地校验兜住。

**一次决策仍只提交一个世界请求**；之前可同批执行只读查询，世界请求与查询不得混批。
一个 `start_activity` 请求可逐项启动一至两个活动；两项按[时间与能量规格](SPEC-time-and-energy.md)第五章「并行准入」
共同校验（名额、目标在场与实际地点、出行方式、注册表占用、物品状态，能量不参与），任一项不成立则整组拒绝。
`revise_day_plan` 是独立登记工具，**不是** `actions` 的取值。

### 保护项修订

保护集随工具面收敛为五类：

| 类别 | 成员 | 为什么必须保护 |
| --- | --- | --- |
| 始终提供 | `start_activity` | 人物可开始活动；同刻上限的受限轮中由工具层标为不可用 |
| 只读查询 | `recall_task_summary`、`query_world`、`memory_*` | 查询不受世界请求同刻阶段限制，仍受模型回合预算限制 |
| 活动退出通道 | `report_status`、`pause_activity`、`conclude_activity` | **有进行中、暂停或待收尾的活动时可用**（各自按活动阶段标注）：三者是恢复、暂停与结束活动的**唯一**手段，剪掉会让活动永久占着名额 |
| 「自身」对象的动作 | `wait`、`sleep`（内部标准动作；场景 `allowed_actions` 与菜单保护项沿用内部名，人物所见分别为 `interact` 的「自己」对象（方式「等待」）与动词 `sleep`） | 不保留则无法等待与睡眠 |
| 当日目标登记 | `revise_day_plan` | 不保留则无法修订当日目标与提醒；当日已有计划时可用（含空清单） |
| 创作内容出口 | `submit_work`、`send_message` | 可提交工作成果与通信正文，不能由 `start_activity` 捏造内容 |

**地点菜单不再剪裁工具，只剪裁对象的 `actions` 枚举。** `InteractionMenu` 的声明
只有 `allowed_actions`，地点与人物声明求交集后再与各对象能力结合。世界请求工具与查询工具始终都在原生 `tools` 中，
各自的使用条件由工具层 `available`、`reason` 标注，不放进地点菜单，也不恢复 `allowed_tools` 或 `allowed_activity_kinds`。
工具层的标注与环境受理出自同一判定：标为不可用的，提交时以 `reason` 所写的原因业务拒绝；标为可用的，宿主与环境都受理。

**同刻上限的受限轮收窄。** 同一人物同一模拟秒的决策轮数达到场景 `max_same_moment_decisions`
后，仍给至多三轮受限轮；环境按观察私有证据 `same_moment_stage` 只受理一类世界请求，工具层把其余动作与工具标为
`available=false`，`reason` 为同刻限制：
- `conclude_pending`（有待收尾活动）只受理点名待收尾活动的 `conclude_activity`，每项都可以收尾，一次收尾其中几项也受理，阶段持续到没有待收尾活动；
- `start_or_resume` 开始或恢复一项持续活动（等待始终可选，不含到店购买与远程下单）；
- `resume_or_conclude`（两个名额都被暂停的活动占着）恢复或结束其中一项；
- `conclude_only` 只受理结束；阶段闭集保留它，但开始或恢复被拒后留在原阶段，现行阶梯不再进入它。

开始或恢复被拒后留在原阶段（名额被占满时进入或留在 `resume_or_conclude`，不再进入 `conclude_only`），被拒的选项在工具层标原因；不再有「启动失败后只准等待」的阶段与升级。受限轮用完仍没有进行中活动，或仍有待收尾活动（例如待收尾活动旁另有进行中活动）时，
环境不替人物造活动：写一条 `environment_event`（`data.kind=decision_budget_exhausted`，`visibility=system`，带最近的拒绝码），
设 `next_decision_at = now + activity_feedback_seconds`，时钟可以推进（技术停顿，身体按静息结算），质量报告计数，不记成人物主动等待。

名称不带逐轮枚举，全部只读查询照常；形状错误按协议错误在本轮重试，不计轮数；越界请求以
`SAME_MOMENT_ACTION_RESTRICTED` 业务拒绝并计入同刻轮数，拒绝原因与工具层 `reason` 一字不差。同刻轮数照旧计入成功、拒绝与重放，
但名称类拒绝不计、也不施加 `service_refused` 情绪脉冲，每人每秒至多 2 次，第 3 次起照常（[一日运行保真规格](SPEC-dayrun-fidelity.md) 3.4）。阶段取值见[时间与能量规格](SPEC-time-and-energy.md)
第一章「规则」第 3 条与「实现约定」。

### 即时查询、完整纠错与紧凑呈现

`recall_task_summary`、`query_world` 与 `memory_recent`、`memory_search`、`memory_time_range` 属于只读查询，
在生活决策中可同批调用；整批参数通过校验后按请求顺序返回。查询不登记世界请求、不结束决策、不推进模拟时间、
不增加同刻轮数，每次实际模型回复仍占用 `MAX_REACT_TURNS=16` 的回合预算。世界请求必须单独调用，
与查询混批或含多个世界请求时整批拒绝，所有调用均未执行。
`query_world(name, way, category, offset)` 四键均必填；前三键为 `str|null`，至少一个非空白，
`offset` 为严格非负 JSON 整数（拒绝布尔值、数字字符串及浮点数），首批显式写 `0`，不能省略；`way` 取现有方式闭集，`category` 匹配公开 `callable_index` 的类别标签。
查询只读取本轮冻结观察、已有私有绑定与可调用索引权限内的完整匹配集合，
不读未揭示邮件正文、不公开超权限报价或世界库存，不替代耗时浏览的揭示行为。
`query_world` 的固定返回为 `{matches, total_count, next_offset}`；`matches` 保留权限内公开能力事实与完整请求候选，
每批沿用现有 `candidate_presentation_limit=20`；`total_count` 是完整权限内匹配数，`next_offset` 必填可空。
还有结果时返回下一批偏移，没有更多结果时为 `null`；同一冻结观察内重复传回偏移可遍历权限内全集。
空结果明确说明权限内无匹配或偏移已到末尾；不另设分页配置。
查询结果即时通过原生 tool 消息返回，留存于模型交互证据；外部只读问答仍只开放人物记忆查询与 `finish`。

所有接口错误均使用英文 `reason`、`fix`、`message`、`why` 与错误日志，中文对象名保留原样。
错误顶层沿用 `{error_code, reason, fix, items, unmet}`；`items` 必须定位实际错误，
每项为 `{path, written, error_code, allowed_values, expected, why, candidates}`，不保留旧 `index`、`field` 定位字段。
`path` 是从工具调用根开始的完整位置数组，如
`["tool_calls", 0, "function", "arguments", "items", 0, "views", 1, "type"]`。
有限枚举在 `allowed_values` 完整列出；无枚举时为 `[]`，`expected` 写明类型、范围、格式、字段关系或世界条件。
未知工具定位 `function.name` 并列出本次实际提供的合法工具；组合错误定位 `tool_calls` 并说明合法调用组合。
不完整 JSON 定位该调用的 `function.arguments`，`expected` 含解析器的行列与该工具参数所需的闭集，
不推测尚未成功解析的参数字段。所有可独立确定的参数与名称错误一并报告，不能用一个粗略顶层字段掩盖嵌套位置。
候选统一为 `{tool, arguments, note}`，`note` 为英文说明或 `null`；`arguments` 是该工具完整合法的新请求，候选可原样提交。
`prefer` 的错误枚举按已合法的方式判定：含购买项返回 `["最快拿到", "到手价最低"]`，
没有购买项返回 `[null]`；尚不能判明方式时列声明的三值并说明跨字段约束，不猜购买类型。
名称字段不是封闭枚举，不把全量商品名塞进 `allowed_values`。业务前置条件中所有可独立确定的问题按既定准入顺序列出，
定位有关请求参数并给合法方式或下一步；需要合法对象才能判定的条件不凭空推断。

进度层另有 `rejected_today` 当天操作备忘：从本人当天已持久化的 `model_response.data.protocol_feedback`
与非重放 `action_rejected` 确定性推导，跨时刻和地点会话保留，跨场景本地日期清空。
每条恰含 `written`、英文 `why`、`times`、`last` 与可空的 `accepted_as`；按原始证据中的相同工具、参数位置和原文合并，
最近使用的至多八条，每条英文 `why` 至多六十字符。它说明系统曾怎样拒绝，不重放整段调用或模型思考，
不新增检查点字段、不写入或迁入人物记忆；会话事件 `events` 的范围仍只限当前会话。
同秒续接对备忘的更新随 `changes.rejected_today` 整体替换，链首快照保持逐字节不变。

公开工具层恰为 `{layer, groups, callable_index}`；每组为 `{kind, action, way, shared, targets}`。
控制对象的 `action`、`way` 为 `null`，仍保留控制工具和状态；解组按名称合并并逐值恢复全部公开字段，
冲突元信息是渲染错误。只省略 `null` 和空集合，保留 `false`、零值与明确的未知原因。
工具层提示使用按对象类别与方式分组的紧凑 JSON：共同字段只写一次，名称、可用性及其差异留在组内，
空字段省略，共同不可用原因提至组级；续接 `changes` 使用同一确定性编码。
编码必须能无损还原目标、方式、可用性、原因、价格、时长、数量、任务合同和其他工具可用性；
冻结观察的工具层结构、私有绑定、可调用索引与世界权限不变。紧凑编码不是预算浓缩，不能减少对象或事实。

## 决策轮循环与播报节律

```mermaid
sequenceDiagram
    participant P as 人物
    participant W as 世界
    P->>W: start_activity(items逐项对象, answering, prefer)
    alt 前置条件不满足
        W-->>P: 业务拒绝 + 未满足项 + 建议前往的地点
    else 满足
        W->>W: 启动活动（各占一个名额）
        loop 每 1800 秒且活动进行中
            W->>P: progress_report_due 唤醒
            P->>W: report_status(可读活动名称数组, 对应自述数组)
        end
        W->>P: planned_end 唤醒（活动结束，待收尾）
        P->>W: 同一秒 conclude_activity(可读活动名称数组, 对应自述数组)
    end
```

| 触发 | 来源 | 是否调用模型 |
| --- | --- | --- |
| 活动进行超过 `progress_report_interval_seconds`（自开始、恢复或上次播报起计） | **新增** `progress_report_due` | 是（一次决策机会） |
| 活动返回结果（`planned_end`） | 既有成员 | 是 |
| 某任务的 DDL 临近 | 既有 `task_due_soon` + `commitment_threatened` | 是 |
| 没有进行中活动（边界结算后或同一秒再决策） | **新增** `no_active_activity`（时间与能量规格第一章） | 是（同一秒决策；不进睡眠允许集合） |
| 当日目标的提醒到期 | **新增** `plan_reminder_due`（时间与能量规格第四章） | 是（睡眠中也提供，并结束睡眠） |
| 活动暂停中 | 不触发 | 否 |
| 人物睡眠中 | 被 `SLEEP_ALLOWED_REASONS` 过滤 | 否 |

**实现要点**：`SLEEP_ALLOWED_REASONS`（`schedule.py:57-60`）存的是 **`WakeReason` 枚举成员**而非字符串，共 4 项（`planned_end`、`run_start`、`wake_from_sleep`、`plan_reminder_due`，末项由时间与能量规格加入）。过滤在 `schedule.py:350-351`，写法是**白名单**：

```python
if snap.is_asleep:
    reasons = [r for r in reasons if r in SLEEP_ALLOWED_REASONS]
```

因此新增的 `progress_report_due` **天然被睡眠过滤掉，不需要额外代码**。真正的风险是反向的：若有人把它加进 `SLEEP_ALLOWED_REASONS`，人物就会在睡眠中被播报唤醒。故验收断言是「**该成员不在集合内**」，而不是「加一份过滤逻辑」。

| 参数 | 值 | 性质 |
| --- | --- | --- |
| `progress_report_interval_seconds` | **1800** | **场景参数**（与 `reconsideration_interval_seconds` 同类）；非正整数被拒 |

**播报同时是一次决策机会**：人物可在播报时顺手暂停、切换或结束活动，与「一次决策一个动作」不冲突。
**沉默边界仍不调用人物决策模型**：结算边界照常产生 `activity_progress`，只有唤醒条件命中才提供决策。

**业务拒绝不是运行故障**：地点、资源、余额、库存、提交返工、名称不可解析等拒绝保留
`ActionStatus.REJECTED` 和公开的原因与下一步（`reason`、`next_step`），进入进度层并给人物下一次决策机会：按时间与能量规格第一章
在同一秒再决策，受场景参数 `max_same_moment_decisions` 约束，不能生成 `run_failed`。
交互对象的占用检查结果写成动作说明，不剪裁对象。
模型协议无效、无法计费或世界不变量损坏仍按各自真正的故障路径处理。

## 前置条件前置与前往提醒

工作场景输入只保留显式必填的 `appointment`：普通工作写 `null`，真实预约写 `{starts_at, reason}`；
删除输入 `available_at`、`availability_kind`、`appointment_reason`。内部 `RuntimeWorkTask.available_at`
由最早发给本人且关联该任务的邮件 `sent_at` 或预约 `starts_at` 派生，不写回输入、不增加检查点字段。
普通工作收到通知后，在地点及既有截止、技能、素材条件满足时即可开始；任务的 `contract.opens` 使用同一派生时刻。
每封 `related_task_id` 非空的邮件都必须关联已存在的任务，且收件人必须是该任务受派人；另外，每个任务都至少有一封本人关联邮件。
预约只要求最早本人关联通知的 `sent_at <= appointment.starts_at`，后续合法提醒可晚于预约开始，不改变内部 `available_at`。
邮件到达不公开正文；阅读结果保留实际所读正文，仅不重复任务合同时间、地点或验收答案。


**规则一：前置条件必须在动作前进入提示**，不靠撞墙学习。

| 前置条件 | 判定处 | 在提示中的位置 |
| --- | --- | --- |
| 必需地点 `required_location_id` | `work.py:76-79` `ACTIVITY_PRECONDITION_UNMET`（`unmet` 含 `required_location_id`） | 工具层 task 目标的工作动作：人不在该地点时 `available=false`，`reason` 点名地点 |
| 必需技能与门槛 | `work.py:74-79` `ACTIVITY_PRECONDITION_UNMET`（`unmet` 含 `required_skill`） | 技能不够的工作运行内不能满足，不列为目标 |
| 合同最长时长 | `work.py:111` `WORK_DURATION_EXCEEDS_CONTRACT` | task 目标的 `contract`（截止、有效工时的达标情况与前景）与工作动作的 `duration` |
| 地点营业状态 | `basket.py:144-146` | 地点目标的 `state` |
| 地点资格 `required_qualifications` | `basket.py:140` `LOCATION_ACCESS_DENIED` | 对应动作的 `available`、`reason` |

能量不是前置条件（时间与能量规格第三章）。

**规则二：前置条件不满足时，拒绝必须给出可执行的下一步，并点名具体地点。**
这是本规格新增的强制要求：人不是碰壁才知道，而是被明确告知「去那里」。

| 错误码 | `data` 必含 | 说明 |
| --- | --- | --- |
| `ACTIVITY_PRECONDITION_UNMET` | 引擎私有结果含未满足规则 `unmet` 与可读建议 `suggested`；公开结果（进度层 `tool_call.result.details`）含一句原因 `reason` 与下一步 `next_step`（要去的地点、要先处理的活动或做法，只用可读名称） | 未满足项含地点时必须点名具体可到达地点，不能暴露 `destination_id` |

**呈现规则**：
- 工具层 task 目标呈现任务名称与 `contract`（截止 `deadline`、有效工时的达标情况 `remaining`、前景 `outlook`），
  工作动作的可用性写在 `available`、`reason`；不呈现任务或地点内部身份；
- 冻结观察的工具层 `targets` 照常列出运行内可能满足条件的对象与动作，不按人物当前状态剪裁；永久不能满足的（没有路线、技能不够）不列；
  条件未满足的动作 `available=false`，`reason` 是环境受理时的拒绝原文，前置地点在 `reason` 中点名；不另写场景前往句。

## 游戏简化

**游戏不再有命令回路。** 人物选择一个游戏名称，之后按播报节律自述「玩的时候做了什么」。

| 项 | 处理 |
| --- | --- |
| `game_action` 工具 | **删除** |
| `GameActionRequest` | **删除** |
| `_game_action` / `_store_game_outcome` handler | **删除** |
| `run_game_command` 与 TextWorld 回放 | **删除** |
| `AgentState.game_sessions` / `GameProgress` | **删除** |
| `game_boundary` 合同（`engine.py:184`、`:198`、`:202`、`:213`；`catalog/affordances.py:101`、`:275-278`、`:444-458`） | **须一并收敛，但它是另一条链路**：`game_boundary` 表达「该活动允许继续交互」，与命令回路不是一回事。本次 11 路审计中「命名冲突」那一路特意分开了二者，实现时不要混同 |
| 启动期 `verify_game_progress`（`engine.py:733-734`） | **删除**（校验对象随 `game_sessions` 消失） |
| `play_game` 活动 | **保留**：与 `watch` 同形——有目标、有时长、有进度结算、有进展自述 |
| 游戏内容（30 个 `games` 声明 + 80 条 `game_session` 内容） | **保留**，成为 `interact` 的合法目标 |
| `playback_speed` | 固定为 1 倍；**播放中改速的能力删除**（`change_playback_speed` 一并删） |

## 上下文分层

提示由确定性代码渲染为七层 JSON，每项信息在整个请求中只有一处完整内容，其他位置只用名称引用（[人物 Prompt 规格](SPEC-persona-prompt.md#一七层请求)第一章；
字段见[接口合同「七层字段」](../CONTRACTS.md#七层字段)）。system 是系统层，user 是工具、人设、记忆、待办、环境、进度六层组成的 JSON 数组，
工具定义只经原生 `tools`；原 `persona_card` / `scene` / `facts` 三段划分废止。

| 层 | 落点 | 说明 |
| --- | --- | --- |
| 系统层 | system 消息 | 世界交互规则，每条机制只写一次，整次运行逐字节不变；身份前言、「怎样过一天」、预算附录与低预算提示都不再出现 |
| 工具层 | user 数组第 1 项 | 公开 `groups` 按类别、内部动作和方式组织目标；解组后的目标事实包含：各目标的动作与方式及其 `available`、`reason`、价格、耗时、数量；活动目标的 `phase` 与客观进度 `state`；task 目标的 `contract`；以目标名称为参数的其他工具及其可用性。工具定义本身只在原生 `tools` 中，稳定工具定义整次运行不变 |
| 人设层 | user 数组第 2 项 | 身份、作息、习惯、口味、关系与偏好；关系的 `view` 与 `preferences` 按对象原地更新 |
| 记忆层 | user 数组第 3 项 | 更早 session 与更早日期中已经发生的事：系统写的 `settled` 与人物写下的 `self_notes` 分列 |
| 待办层 | user 数组第 4 项 | 暂停的活动（地点、客观进度与可用的控制工具）与今日计划 `day_plan`；目标行只有意图、目标、是否推进与提醒时刻，不显示人物自述 |
| 环境层 | user 数组第 5 项 | 五项体感只给分级，数值仍不进提示；所在地点或路程的静态信息 |
| 进度层 | user 数组第 6 项 | 当前 session 的有序 `events` 与当天跨会话的 `rejected_today` |

**进度层**的 `events` 只含当前 session：`session_started_at` 与 `events`，按 `occurred_at` 排列，同一时刻按事件序号。
`tool_call` 把一次世界请求连同公开结果合为一条，回复正文作为该调用的 `self_note`，只到本 session 结束；
`message_arrival` 写直接消息与交谈邀请的全文、邮件的主题；`delivery` 写订单的送达进展；`task_deadline` 与 `plan_reminder`
各出现一次，截止时刻只在工具层、提醒时刻只在待办层；`condensed` 是预算浓缩产生的折叠记录。进度层不再按会话分组列出活动：
活动的客观进度只在工具层活动目标，暂停的活动只在待办层，session 结束后由系统派生一条记忆进入记忆层（`settled` 与 `self_notes`）。
同一秒接着决策时，链首快照之后同秒发生的事经续接的 tool 消息交还（每条 tool 消息只带上一决策观察之后的新记录，同一条不重复），不另写 `just_now` 一句。
模型不见内部身份、哈希、账户、段区间、报价版本和完整世界状态。
同一条规则适用于七层中的全部目标、记录与联系人：人物看到名称、地点、方式、时间、费用、数量等业务事实，
不看到 `task_id`、`route_id`、`supply_id`、`product_id`、`message_id`、
`contact_id` 等仅供引擎定位的键。全部模型工具根据冻结观察中的可读名称私有绑定解析（`obtain` 的指名购买按场景供给解析），
不得让人物从奇怪字符、编号或索引猜测对象。
工作任务名称取场景必填的 `WorkTaskConfig.title`，不缺省为内部 `task_id`；
提交工作时引擎使用冻结观察中的私有绑定定位合同。

`tool_call.result` 只含 `occurred_at`、`status`、`error_code`、`message` 与 `details`，工作结果另带公开 `work_review`。
`details` 只放本次新产生的可读事实（商品、份数、付款、送达地点与时刻、计划时长、进食的实际份量、终局 `outcome`、
近似候选 `candidates`；另有给予、到达、浏览得到的商品名、读完的邮件与已关联的订单，键闭集见接口合同「七层字段」），不复述参数；
按一日运行保真规格第四章，`details` 在拒绝时另可含 `fix`（一句可照做的改法）与逐项明细 `items[]`（每项至多 3 个可照填的候选），受理时另可含归一结果 `resolved` 与说明 `notes`，
`message` 直接取 `reason`，浏览得到的商品名写完整标签；`work_review` 沿用
既有 `public_work_review` 的公开判定和理由。`action_id`、`event_id`、`order_id`、
`product_id`、`supply_id`、`session_id`、`route_id`、`price_version`、账户身份、
私有评审参考、哈希和完整状态均不进入该记录。每条结果仍占一条记录，不能因去除内部字段
而丢失一个动作的反馈；结果在整个 session 内保留，不再有结果游标或观察水位。

## 容量、浓缩与遗忘

### 发送前 token 计数与进度浓缩

每次实际模型请求，包括行动和只读调用，在发送前按所选模型的上下文窗口计数。
计数对象是完整请求：原生 `tools`、系统层、各 user 层 JSON、续接消息（含回传的 `reasoning_content`）、
已发生的原生消息与工具返回，加上本次预留的最大输出 token。
不能用某一层的字符数代替整次请求 token 数，也不能假定选用模型一定有
256k 窗口；模型窗口与输出能力须在运行前从实际所用模型的权威元数据确定。
完整请求计数及达到阈值后浓缩的分界可参考
[Claude 官方上下文说明](https://platform.claude.com/docs/en/build-with-claude/context-windows)与
[阈值浓缩说明](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold)；
本规格的 85% 与浓缩顺序是本项目自己的裁决，不依赖某供应商专用接口。

当上述总数达到上下文窗口的 **85%** 时依次处理（[人物 Prompt 规格](SPEC-persona-prompt.md#四预算)第四章）：
先提前结束本秒续接，本次请求改为从新快照构造，思考不再出现；再从旧到新去掉未受保护记录的 `self_note`；
再把最旧一段未受保护、已有结果的记录折成一条 `condensed` 记录，其 `lines` 为 settled 写法的句子。
工具层的进行中与刚结束活动、待办层的暂停活动、未了结订单相关的记录与上次请求之后的新记录始终完整保留。
工具层、人设层、待办层与环境层不浓缩，记忆层只受自身 8000 字符窗口约束。合并前后均不让模型产生新的事实。
浓缩不改 `WorldState.sessions`、`WorldState.activities`、原始事件、人物目标或记忆。
每步后重新计算整次请求；若最终仍达到 **85%**，或超过模型输入额度，
以**容量故障**停止该次请求，按系统层、工具定义、各 user 层、续接消息、预留输出分层记录 token 用量与英文错误日志，
不静默删减。
旧 user 40000 字符、system 3000 字符、scene 800 汉字的独立致命容量闸门废止；
人物自述 500 字符等领域字段限额继续生效。
模型返回的实际 token 用量用于校核预估器；下一次请求仍须先完成全量计数。

### 午夜浓缩

模拟时钟跨过当日 `day_end_seconds` 时，仍执行既定记忆巩固与目标单句更新；巩固保留，其摘要不进提示。
记忆层按场景本地日期分区，零点不切断 session：`earlier_today` 取结束时刻落在今天的已结束 session，
`earlier_days` 只保留起源于该日且此刻仍未了结的事项与归属该日的就寝反思，已了结的旧事项自动消失。
午夜处理与上述 **85% 发送前浓缩**是两个不同触发条件，均不得改写客观结算、已核验证据或原始事件。

### 遗忘是派生视图，不是销毁

记忆层上限 **8000 字符**（新常量）由构建提示时的窗口实现，**不删任何记录**：超出时依次删去最旧日期的反思、
再把最旧的今日条目缩成只剩未了结的 settled 句；未了结事项与最近一个 session 的条目永不删去；仍超出时写英文日志，
交给整次请求的 85% 阈值处理。

- `episodes.jsonl` 只追加，`episode_counter == len(records)` 不变；
- `episodes_sha256` 证据链不变；
- 记忆层由环境从已提交事件与世界状态派生，模型写下的内容只进入 `self_notes`，不能改写 `settled`；
- 更早的 summary 可按活动名（记忆层 `self_notes` 所写的活动名即可）用 `recall_task_summary` 回看，结束活动写下的记忆与反思可用记忆查询取得。

## 事件与重建约束

硬约束：**SQLite 记录是唯一提交权威，导出时活动会从事件重建并逐字节比对**
（`footprints/validation.py:33-34`），质量报告也必须能仅从事件复现（`:42-54`）。

**结论：不需要新增事件类型。** 世界请求沿用既有动作记录路径，查询与纠错存于模型交互证据，
`revise_day_plan` 走既有动作路径；零点计划 `plan_day` 的计划登记写 `environment_event`
（`data.kind="day_plan_generated"`），不产生 `action_requested`；
人物动作请求随既有 `action_requested` 事件落库
（含完整 `request` 与 `request_sha256`）：

| 内容 | 审计路径 |
| --- | --- |
| 会话的开始与结束 | 由系统随出发与到达开始和结束，只进世界状态（`WorldState.sessions`，随 `life` 快照保存），不写事件；活动所属会话由活动快照的 `session_ids` 记录 |
| 活动的开始 / 暂停 / 恢复 / 结束 | `action_requested`（`tool_name` + `request`）；暂停、恢复与结束另逐活动写一条 `environment_event`（`data.kind` 取 `activity_paused`、`activity_resumed`、`activity_concluded`，带活动快照 `data.activity`，`caused_by` 含本次请求事件） |
| 活动的实验截止截断 | 不经人物请求；每项被截断的活动（进行中或暂停）写一条 `environment_event`（`data.kind` 取 `activity_truncated`，带截断后的活动快照 `data.activity`，`caused_by` 为开始该活动的请求事件）；同源两项的聚合终态不带快照，导出据逐活动快照重建，与最终检查点逐项一致 |
| 模型的进展自述 | 同上；播报、暂停、结束都写 `request.items[]`，每项 `{activity_name, summary}`（结束另带该项的 `memories` 与 `views`；读者只接受当前 `items` 形状） |
| 活动的客观结算 | 既有 `activity_progress` |
| 零点计划 | `model_request`、`model_response` 带 `data.purpose="plan_day"`（`visibility=system`，不带 `decision_id` 与 `action_id`）；计划写 `environment_event`（`data.kind="day_plan_generated"`，带 `data.day_plan` 与输入来源 `data.source_event_ids`），与世界状态中的计划、提醒和 `plan_submitted_dates` 同一次提交 |
| 会话快照 | 世界状态，随 `life` 快照保存 |
| 可读名称至内部身份及冻结报价 | 私有观察绑定与已登记动作证据，不能进入模型提示 |
| 实际模型请求与交付输入 | 保存同一份最终公开请求视图；交付时不从原始观察重组另一版本 |

**这同时确认**：进展自述一旦提交就永久留在 append-only 记录里，提示层的浓缩只改变
下一次所见，不会删除原始记录。

## 冻结条款修订

| # | 被修订条款 | 原文 | 修订后 | 连带改动 |
| --- | --- | --- | --- | --- |
| 1 | `WakeReason` 冻结词表 | 「唤醒原因是冻结词表，成员不可增删」 | **增加成员** `progress_report_due`，并把条款改为「不可改名、不可删除，新增须走版本表」；时间与能量规格再依次追加 `no_active_activity`、`plan_reminder_due` | **代码同步点共五处**（三处生产代码 + 两处测试），唯一权威定义见[文档影响](research/agent-interaction-document-impact.md)「唤醒词表的五处同步点」节。其中 `footprints/quality.py:384-399` 的「未识别」分桶最易漏——**它不报错，只让质量报告的数字变错**。另需同步 `CONTRACTS` 枚举表与 life-runtime 冻结词表 |
| 2 | 活动组合准入 | 「活动准入**仅**按占用交集判定」 | 保留占用交集，**追加**名额上限：进行中、暂停中与待收尾的活动合计 ≤2（常量），超限报 `SESSION_LIMIT_EXCEEDED` | `rules.validate_start`、`interaction_menu`、错误码闭集 |
| 3 | 主观巩固触发 | 「为**三选一**」 | 改为**四选一**，增加「跨过当日 `day_end_seconds` 边界」 | `should_consolidate`、`SPEC-life-realism.md`「记忆合同」、`CONTRACTS` |
| 4 | 观察投影三段**与不得进入提示的内容** | 「user 消息由 `persona_card`、`scene`、`facts` 三部分组成」+「完整可供性合同不得进入提示」 | **三段划分不变**；只公开逐对象可读 `actions`，私有 `interaction_bindings` 与完整可供性合同仍禁止进入提示。**三段划分已由[人物 Prompt 规格](SPEC-persona-prompt.md)「冻结条款修订」第 8 行取代**：system 为系统层，user 为六层 JSON 数组；宿主渲染七层 JSON 与续接 tool 消息时按禁止键表逐键校验，原生 `tools` 是整次运行不变的常量，由测试校验 | `projection.py`、`narration.py`、`CONTRACTS`「观察投影字段」与「禁止进入提示的字段」 |
| 5 | 决策结果回灌 | `SPEC-decision-context.md` 规定最近公开结果独立进入事实清单且场景概括最近一条 | 改为 `facts.progress.results` 归进度层；`scene` 不复制结果；私有观察仍可存原始关联证据。**已由人物 Prompt 规格「冻结条款修订」第 4、22 行取代**：结果是进度层 `tool_call.result`，在本 session 内一直保留，session 结束后进入记忆层 | `projection.py`、`result_projection.py`、`SPEC-decision-context.md`、`CONTRACTS.md` |
| 6 | 字符预算与超限 | `SPEC-decision-context.md` 以 user 40000 字符、system 3000 字符、scene 800 汉字单独熔断 | 模型调用以完整请求 token 加预留输出计数；达到所用模型窗口 85% 先只浓缩进度派生投影，重算仍达 85% 即报告容量故障；人物自述等字段语义上限另按其合同执行。**浓缩顺序已由人物 Prompt 规格「冻结条款修订」第 7 行取代**：先提前结束本秒续接，再浓缩进度层 JSON，仍超限按容量故障停止 | `life_context.py`、`life_agent.py`、`narration.py`、`SPEC-decision-context.md`、`CONTRACTS.md` |
| 7 | 连续业务拒绝 | 业务拒绝连续达到阈值终止整次运行 | 保留拒绝事实与阈值观察，按既有 `activity_feedback_seconds` 冷却再给决策机会；不生成 `run_failed`。**已由时间与能量规格修订 2 取代** | `engine.py`、`SPEC-life-runtime.md`、测试 |

**未被修订的既有语义**：只有环境能改世界状态；一次决策一个生活动作；沉默边界不调用人物决策模型；
体感数值不进提示；已核验经历只由已提交事件确定性投影；SQLite 记录是唯一提交权威。

## 实现顺序

用户裁决：**一次性替换旧人物工具面**，不分期、不保留兼容入口。
建议的内部顺序仅描述同一变更集内的依赖：

```mermaid
flowchart LR
    A["三级结构<br/>WorldState.sessions + SessionState"] --> B["进度层<br/>projection + narration"]
    B --> C["前置条件前置<br/>工具层 contract + available、reason"]
    C --> D["播报节律<br/>WakeReason + 场景参数"]
    D --> E["工具面收敛<br/>七个世界请求、五个只读查询，选择与创作分开"]
    E --> F["删除<br/>game_action / change_playback_speed / GameProgress"]
```

## 与既有文档的一致性核对

本规格是交互行为的权威定义；同一变更集必须同步下列合同，不能留下互相矛盾的旧表述。

| 文档 | 必须同步的内容 |
| --- | --- |
| `docs/CONTRACTS.md` | 世界请求与查询工具的精确字段（含 `from`、`to`、`answering`）与可读名称绑定；七层字段与进度层 `tool_call.result` 的公开字段；完整请求 token 预算与禁用内部身份；工作评审结果 |
| `docs/dev/SPEC-decision-context.md` | 结果在本 session 内留在进度层、session 结束后进入记忆层；达到阈值先提前结束续接再浓缩进度层；旧字符闸门与新 token 预算关系 |
| `docs/dev/SPEC-life-runtime.md` | 业务拒绝后同一秒再决策（同刻上限）、引擎派生执行参数、会话状态和播报原因 |
| `docs/dev/SPEC-life-realism.md` | 午夜记忆巩固仍不篡改事件与已核验经历；游戏命令回路删除 |
| `docs/dev/SPEC-labelkit-delivery.md` 与 `docs/EXPERIMENT.md` | 交付输入采用实际发送给模型的公开请求视图，保留其记忆与自述来源；不得把私有观察或工作评审请求混进样本 |
| `docs/design/04-simulation-engine.md` | 对象与动作选择、冻结报价、引擎派生时长和资源前置条件、非致命业务拒绝 |
| `docs/design/05-recording-engine.md` | 会话和公开结果的事件归属、模型请求及交付输入的可重建性、世界结构版本 |
| `docs/design/06-delivery.md` | LabelKit 输入只采用实际人物请求的公开视图，私有观察、重复前置状态和独立工作评审请求不混入 |
| `docs/design/08-configuration.md` | 保护项改为世界请求工具与只读查询工具，场景已有节律参数的归属 |
| `docs/design/09-io-formats.md` | 模型请求与引擎内部请求分离、私有名称绑定、公开进度结构、世界结构版本 |
| `docs/design/00-overview.md`、`docs/design/01-architecture.md` | 七层请求（system 为系统层，user 为六层 JSON）取代三段消息载体、规格修订权威 |

原提案和只读审计是历史证据，不再作为最终行为合同。`docs/dev/implementation-status.md`
记录当前代码实况，仅在行为实现并验证后更新，不能提前写成已落地。

## 文件修改清单

| 职责 | 文件 |
| --- | --- |
| 模型工具与可读名称绑定 | `custom/agents/life_agent.py`、`src/worldmock/life/models.py`、`src/worldmock/life/observe.py`、`src/worldmock/life/session_actions.py` |
| 对象、进度和容量投影 | `src/worldmock/life/projection.py`、`src/worldmock/life/narration.py`、`custom/agents/life_context.py` |
| 结算、失败分类和执行反馈 | `src/worldmock/life/engine.py`、`src/worldmock/life/rules.py`、`src/worldmock/life/result_projection.py` |
| 交付输入与公开结果一致性 | `src/worldmock/delivery/windows.py`、`training.py`、`pipeline.py` 与 `src/worldmock/evaluation/public_evidence.py`；不新增独立输入副本 |
| 合同与设计文档 | 上节表中的所有文档，以及 `docs/dev/implementation-status.md` 的实际实现状态 |
| 验证 | `tests/life/` 的对象绑定、活动参数、非致命拒绝、进度容量测试；`tests/integration/` 的真实人物工具与交付输入往返测试 |

## 验收台账

| 契约点 | 必须通过的验证 |
| --- | --- |
| 会话与活动生命周期 | 会话随出发与到达自动切换，人物不手动开始或结束会话；每人两个名额，进行中、暂停中与待收尾的活动各占一个；三个控制工具的 `items[]` 每项以 `activity_name` 点名一项活动并带自己的 `summary`（一日运行保真规格第六章）；播报能在开始地点恢复暂停的活动，暂停与恢复累计活动时段；结束只影响所选活动，暂停的活动可直接结束；自述不改变客观结算字段 |
| 目标与活动自述 | 每条播报、暂停或结束摘要各自写入所选活动的 `ActivityState.progress_note`；关联当日目标时同一原文也写该目标独立的 `PlanGoal.progress_note`，该原文不进提示，待办层目标行不显示人物自述；整组校验失败不部分写入 |
| 模型工具 Schema | `start_activity` 只含必填的 `items[{target, way, from, to}]` 一至两项与必填可空的 `answering`、`prefer`，方式唯一推导内部动词，旧 `actions` 字段被拒绝，`from`、`to` 的合法与非法组合分别受理、业务拒绝或按协议纠错，不含内部身份、价格、报价版本、时长、数量、正文和成果；原生工具整次运行不变，不带逐轮名称枚举；`report_status` / `pause_activity` 只含 `items[{activity_name, summary}]`，`conclude_activity` 为 `items[{activity_name, summary, memories, views}]`，`views` 写入的记忆带 `subject`，`control_items` 只读当前 `items` 形状，旧平行数组直接拒绝；`submit_work` / `send_message` 另有可读名称和原创内容字段 |
| 名称绑定 | 同名对象由自然语义消歧；所有模型可见对象与动作可被人理解；冻结观察能唯一解析，写得不准的名称无损归一后唯一即受理，仍未知或歧义时以名称类错误给可照填的候选、不替换（一日运行保真规格 3.1）；内部编号不出现在提示、工具参数或公开结果 |
| 冻结执行参数 | 出行按所选方式使用冻结路线的精确秒数与票价；双供给购买各自使用冻结报价；各活动时长和单份数量按本规格表计算，状态变化时仍经引擎校验 |
| 工作与消息创作 | `submit_work(task_name, deliverable)` 进入现有工作评审，返工为业务反馈；`send_message(recipient_name, text)` 使用既有通讯结算；二者不经过 `start_activity` 的隐藏内容参数 |
| 业务拒绝 | 业务拒绝后在同一秒再决策，轮数受同刻上限 `max_same_moment_decisions` 约束，达到上限仍无 `run_failed`；不再出现 `wait_only`，受限轮用完仍无进行中活动或仍有待收尾活动时写 `decision_budget_exhausted`、不造等待活动、时钟推进；工具层每个 `available=false` 的动作提交后，拒绝原因与 `reason` 一字不差；真正协议或世界故障保持故障路径；拒绝原因和可执行建议在进度层该调用的结果中出现，同秒续接时随 tool 消息返回；名称类拒绝不计同刻轮数、不施加情绪脉冲，每人每秒至多 2 次，第 3 次起照常（一日运行保真规格 3.4） |
| 进度层归属 | 进度层的 `events` 只含当前 session 的调用连同结果、消息全文、送达、截止与提醒，结果在整个 session 保留；活动的客观进度只在工具层活动目标，暂停的活动只在待办层，更早的 session 在记忆层；请求中没有 `scene`、`facts` 与 `just_now` |
| 完整 token 计数 | 对行动、只读的每次实际请求，计入原生 `tools`、系统层、各 user 层、续接消息、历史消息与预留输出；与提供者实际 usage 抽样核对 |
| 85% 进度浓缩 | 阈值时先提前结束续接，再依次去 `self_note`、折叠为 `condensed`；受保护项完整保留；事件和权威状态字节不变；仍超限则带分层 token 用量报容量故障；记忆层按自身 8000 字符窗口截取 |
| 决策节律 | 播报间隔缺省 1800 秒；睡眠中不因播报唤醒；沉默边界不调用人物决策模型；到截止或活动结束提供下一次决策机会 |
| 前置条件 | 任务所需地点与合同条件先于动作进入工具层（技能不够的工作不列）；不满足时 `reason` 具体说明去向；名额与准入分别验证 |
| 游戏与旧接口 | `game_action`、播放中变速和旧模型工具签名全链路不存在，游戏仍可选择、结算和自述 |
| 记忆与检查点 | 午夜目标单句与记忆巩固不改已核验证据与事件，巩固摘要不进提示；记忆层按场景本地日期分区，未了结事项处理后消失；旧世界结构检查点被拒，未做迁移或兼容读取 |
| 事件与交付 | 人物请求及系统结算可由事件审计；交付输入与实际发送给模型的公开请求视图一致，私有观察和评审模型请求不进入人物样本 |
| 文档一致性 | 上节每份冻结合同同步；相互链接与锚点可解析，旧 7 工具、单会话 4000 字符、只计 user 字符、独立结果层、三段消息载体、`scene`、`facts`、身份前言与「怎样过一天」的表述均不存在或注明被取代 |

## 版本表

以下记录保留当时的变更与验证证据；当前接口约束以正文和工具交互规格为准。

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| 1.0–1.2 | 2026-09-22 | 三级结构、七层上下文、旧七工具方案、播报、并发与午夜巩固的历史裁决 |
| 1.3 | 2026-09-23 | issue #1 后收敛模型与引擎的执行契约；九工具区分选择和原创内容；进度层承载执行结果；整次请求 token 计数与 85% 进度浓缩；业务拒绝不终止运行；交付输入一致性 |
| 1.4 | 2026-09-24 | 按[时间与能量规格](SPEC-time-and-energy.md)「冻结条款修订」第 17–21 行修订，修订部分待实现：三级结构改为当日目标 → 地点会话 → 活动，进度与自述挂在活动上，会话字段按该规格第七章重写；`start_activity` 为 `targets`、`actions`、`ways` 三个等长数组；播报只针对进行中活动，唤醒表增 `no_active_activity`、`plan_reminder_due`，业务拒绝后同一秒再决策；能量不是前置条件，交互对象照常列出并带说明；进度层按会话分组列活动，浓缩单位改为会话与已收尾活动；前置条件表的判定处按代码更正为 `ACTIVITY_PRECONDITION_UNMET`（既有漂移，原文 `WORK_LOCATION_REQUIRED`、`WORK_SKILL_INSUFFICIENT`），并刷新 `models.py`、`schedule.py`、`work.py` 行号；system 加「怎样过一天」，饿时补吃饭途径句；游戏为 `interact` 的合法目标；验收台账相应改写；修订 #2 改为常量名额，修订 #7 被取代；人物工具八个。「稳定工具面」「保护项修订」「对象与动作选择」「事件与重建约束」随代码修订。补记未入表的就地改动：`eee89fc` 在「决策轮循环与播报节律」加入 `RESOURCE_CONFLICT` 的延迟排期与按执行器资源声明检查占用；`778beca` 在「前置条件前置与前往提醒」改为交互对象只列当前可执行的对象与动作；二者由本版取代 |
| 1.5 | 2026-09-25 | 按时间与能量规格阶段 1（AI8）：「保护项修订」注明永恒可见的两件工具在同刻上限的受限轮中例外，新增受限轮收窄一段：按观察私有证据 `same_moment_stage`，`start_or_resume` 只开放开始或恢复一项持续活动（等待始终可选，不含到店购买与远程下单），`wait_only` 只开放等待；依赖名额与待收尾的三个阶段按最终行为写明：`conclude_pending` 只开放 `conclude_activity`，名称只列待收尾活动且须一次点名全部，`resume_or_conclude` 在两个名额都被暂停活动占着时恢复或结束其中一项，`conclude_only` 在名额满时开始或恢复被拒后只开放结束（名称只列占名额的活动）、此后只开放等待；名称枚举只列当下可执行项，记忆查询照常，形状错误按协议错误在本轮重试；越界请求以 `SAME_MOMENT_ACTION_RESTRICTED` 拒绝并计入同刻轮数；「稳定工具面」表中 `start_activity`、`recall_task_summary` 的「永恒可见」注明受限轮除外 |
| 1.6 | 2026-09-25 | 按时间与能量规格阶段 3（AI6、AI8、AI15）：「稳定工具面」的五个工具改称活动动作，控制对象由会话改为活动（`task[]` 只取活动名，回看可取任务、当日目标、活动或会话名，结果在下一次观察的 `details.recall` 中呈现），`report_status` 在有进行中或暂停的活动时可见并在开始地点恢复暂停的活动，`pause_activity` 不暂停路上的活动，`conclude_activity` 收 `memories` 并可结束进行中、暂停或待收尾的活动，生活决策没有 `finish`；「保护项修订」的退出通道改为活动退出通道；「对象与动作选择」的私有名称绑定改为活动绑定与会话绑定；「事件与重建约束」写明会话由系统开始和结束、只进世界状态，暂停、恢复与结束逐活动写 `environment_event`；「对象与动作选择」注明活动绑定的 `resumable`（暂停的活动此刻在开始地点、可以恢复；受限轮的恢复名称枚举只列这些）；「事件与重建约束」增实验截止截断的逐活动记录 `activity_truncated`（带截断后的活动快照）；「保护项修订」的当日目标登记：日首尚无计划但有待收尾活动（例如醒来时刚结束的睡眠）时不只开放 `plan_day` |
| 1.7 | 2026-09-25 | 按时间与能量规格阶段 4（AI6、AI7、AI8）：`start_activity` 收 `targets`、`actions`、`ways` 三个等长数组，`actions` 取五个动词；「对象与动作选择」的公开行改为 `{object, actions: [{action, note}]}`、示例改为动词加方式，私有绑定另记履约方式与 `use_mode`，引擎按占用与准入事实校验；派生参数表补开工 60 秒、「接着看 / 从头再看」、86400 秒上限与交谈、通话的截取区间；「Schema 约束」改为三个必填数组；保护项为五类，`wait`、`sleep` 注明人物所见的动词与方式 |
| 1.8 | 2026-09-25 | 按时间与能量规格阶段 5（AI6、AI8、AI15）：「稳定工具面」收敛为 8 个工具，登记动作只剩 `revise_day_plan`，删 `plan_day` 行（当日计划由零点后台任务生成，`plan_day` 不是人物工具）；`revise_day_plan` 在当日已有计划时可见（含空清单），条目带必填可空的 `remind_time`；「`plan_day` 与 `revise_day_plan` 保留为独立工具」一段注明已由该规格第四章取代；「保护项修订」的当日目标登记只含 `revise_day_plan`，工具条件可见性改为八个工具；「事件与重建约束」写明零点计划的登记写 `environment_event`（`data.kind="day_plan_generated"`）、不产生 `action_requested`，审计表增零点计划行（带 `purpose="plan_day"` 的模型请求与响应） |
| 1.9 | 2026-09-26 | 时间与能量规格阶段 7 后的文档一致性核对，按最终代码与该规格修正：`models.py`、`schedule.py`、`work.py` 引用行号刷新；「稳定工具面」记忆查询删去 `memory_read`、结果不带条目身份，`submit_work` 在有可见的未付款任务时可见（未达标 `WORK_INCOMPLETE`，正在做 `WORK_TASK_IN_PROGRESS`），补有待收尾活动时做别的以 `ACTIVITY_CONCLUSION_REQUIRED` 拒绝（该规格修订 18）；「对象与动作选择」无时长动作不虚构活动时长；「决策轮循环与播报节律」播报自开始、恢复或上次播报起计，业务拒绝公开原因与下一步（`reason`、`next_step`）；「前置条件前置与前往提醒」公开结果为 `reason` 与 `next_step`，`unmet`、`suggested` 只在私有结果；「上下文分层」`scene` 不复制完整结果、同刻轮次另有 `just_now`，进度层阶段说法改为进行中、已暂停、刚结束（待收尾）、已收尾、已结束，`alarms` 补当日目标提醒句；一致性核对与验收台账去掉拒绝冷却的旧表述；「稳定工具面」中 `plan_day` 与 `revise_day_plan` 保留为独立工具一段的首句注明已被取代 |
| 1.10 | 2026-09-26 | 时间与能量规格实现后（阶段 9）：状态头「修订部分待实现」改为已实现并通过离线验收，链接时间与能量验收记录；「午夜浓缩」一节及其修订 #3 按裁定保持原文 |
| 1.11 | 2026-09-27 | 按[人物 Prompt 规格](SPEC-persona-prompt.md)「冻结条款修订」第 7、8、12、13、15、16、20–22、24、26 行修订，修订部分待实现（人物 Prompt 台账 AI1–AI7）。理由：请求改为七层 JSON，三段载体、`scene` 与 `facts` 退出，进度层只含当前 session；`start_activity` 增来源与去向；原生工具整次运行不变，受限与可用性只由工具层表达。改动：文件头；「心智模型」的执行契约为五个等长数组与 `answering`；「权威来源与范围裁决」决策所见、单一行动阶段、同秒续接与预算指向人物 Prompt 规格；工作恢复与开工受同一截止约束；「会话字段」进度层只含当前 session，目标单句进展照存、不进提示，8000 字符为记忆层窗口，同一段 summary 只在进度层或记忆层；「稳定工具面」可见性改为可用条件，八个工具始终在原生 `tools` 中、不带逐轮名称枚举，名称只取七层中出现的，回看结果进 `tool_call.result.details.recall`；「对象与动作选择」改为工具层 `targets`，写 `from`、`to`、`answering` 与新方式「给予」，`obtain` 指名购买，派生参数表写开吃份量、小数份与时长折算、给予，「Schema 约束」为五个数组；「保护项修订」删「启动失败后只准等待」（`wait_only` 阶段及其升级），受限轮用完写 `decision_budget_exhausted` 并技术停顿，可用性与环境受理一致；「前置条件前置与前往提醒」改由 `contract` 与 `available`、`reason` 表达，删场景前往句；「上下文分层」以七层替换旧分层，「发送前 token 计数与进度浓缩」按人物 Prompt 规格第四章，「午夜浓缩」为记忆层零点分区，「遗忘是派生视图」写记忆层窗口；冻结条款修订 #4–#6 注明被取代；一致性核对与验收台账改写 |
| 1.12 | 2026-09-28 | 人物 Prompt 七层规格实现后文档（人物 Prompt 台账阶段 8，按最终代码 `f798ea7` 核对）：状态头改为修订部分已实现并通过离线验收；受限轮一节写明 `conclude_only` 只留在闭集中（开始或恢复被拒后留在原阶段，名额占满时进入或留在 `resume_or_conclude`），受限轮用完仍有待收尾活动时同样写 `decision_budget_exhausted`；稳定工具面注明最后一个回合去掉记忆查询工具；`to` 写人物目标的名称，不在工具层的同世界人物可写「人物：<名>」；续接的 tool 消息只带上一决策观察之后的新记录；`details` 指向接口合同的键闭集；禁止键校验的覆盖范围按代码写明；验收台账「业务拒绝」行补待收尾情形 |
| 1.13 | 2026-09-28 | 人物 Prompt 七层规格最后一批代码与规格出入的修复（见[人物 Prompt 验收记录](persona-prompt-verification.md#代码与规格的出入)）：受限轮 `conclude_pending` 每项待收尾活动都可以收尾、一次收尾其中几项也受理；私有 `goal_bindings` 的名称是待办层写出的目标文字，`activity_bindings` 另带 `note_labels`，更早的 summary 可按记忆层 `self_notes` 所写的活动名回看 |
| 1.14 | 2026-09-29 | 按[一日运行保真规格](SPEC-dayrun-fidelity.md#冻结条款修订)「冻结条款修订」第 10、11、13、14、18 行与其 3.5 修订（一日运行保真 AI1，随该规格冻结）：「稳定工具面」`start_activity` 增请求级 `prefer` 与 `obtain` 的方式「购买」，`report_status`、`pause_activity` 改为 `items[{activity_name, summary}]`，`conclude_activity` 改为 `items[{activity_name, summary, memories, views}]`（看法与偏好放 `views`，`about` 对应存储的 `subject`）；名称一段改为控制工具取 `items[].activity_name`，写得不准的名称无损归一后唯一即受理、否则以名称类错误给候选（:196「同名对象由自然语义消歧」不变）；「对象与动作选择」`obtain` 的 `from` 改为可选约束并可写渠道称呼，私有绑定一段的指名购买改为分段匹配与按 `prefer` 选定、不再报 `SUPPLY_NAME_AMBIGUOUS`，其余动词也按名调用；「保护项修订」补名称类拒绝不计同刻轮数、不施加脉冲；「上下文分层」的公开结果补 `details` 的 `fix`、`items`、`resolved`、`notes` 与 `message` 取 `reason`；验收台账会话与活动生命周期、模型工具 Schema、名称绑定、业务拒绝四行改写；文件头补修订说明 |
| 1.15 | 2026-09-29 | 一日运行保真规格实现后（按 `e377669` 的代码核对）：「稳定工具面」原生工具的例外补输出截断后的那次重试同样去掉记忆查询工具（该规格 7.2）；「会话字段」容量表的「进展自述」与「事件与重建约束」的「模型的进展自述」改为控制工具各项的 `items[].summary`（旧形状经 `control_items` 读出，冻结时漏改）；文件头改为修订部分（控制工具的 `items`、按名调用、指名购买与 `prefer`、名称类拒绝不计轮、公开结果键）已实现并通过离线验收；验收台账不改 |
| `2026-10-01-tool-interaction` | 2026-10-01 | 按工具交互规格直接修订当前条款：逐项对象请求、方式推导、严格中文购买枚举与完整错误定位、即时只读查询、当天操作备忘及紧凑呈现；合同已实现，验收证据另列，历史验收不证明新增功能。 |
| `2026-10-01-tool-interaction-review` | 2026-10-01 | 按附件复核定稿：开始活动改逐项对象、购买偏好用精确中文枚举且非购买为 null、to=null 统一本人、查询四键与二十项分页；合同已实现，验收证据另列，错误语言仍待澄清且暂维持英文。 |
