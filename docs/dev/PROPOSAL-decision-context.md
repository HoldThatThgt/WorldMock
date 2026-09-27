# 人物 Prompt 七层重整方案

> 设计审阅稿。本轮更新设计、七层 JSON Schema 和图示，运行逻辑随后统一修改。
> 本文是新方案的唯一入口；[设计细节与来源](research/persona-prompt-seven-layers.md)提供依据。
> [交互图示 HTML](research/persona-prompt-framework.html)｜[七层字段设计](#七层-json-字段设计)｜[完整 JSON 示例](research/persona-prompt-schemas/examples.json)

## 心智模型与项目框架的映射

### 用户要求

> 系统层（身份与行为准则）：formal & concise，目的是告诉LLMs这是什么，并且让LLMs知道如何与这个世界交互。
>
> 工具层（工具定义与可选目标）。
>
> 人设层（人物卡片）：人物基础信息和性格习惯。
>
> 记忆层：一部分来自于昨天和更早的记忆总结，另一部分是当日更早session的总结。
>
> 待办层：从老到新罗列之前paused的activity以及今日的day_plan。
>
> 环境层（此刻场景）：仅保留身体状况和所在环境的静态信息。
>
> 进度层：当前session从开始到现在输出的决策（不包括thinking）以及工具调用结果，按时间排序。
>
> 每层应该都使用json格式并且由自己的json schema维护。

后续确认：关系和偏好在人设层原地更新，当日处境不放进去。新消息、配送送达和截止提醒进入进度层。
session 沿用地点停留或路程；进行中的 activity 不能跨出行。记忆保存需要延续的摘要，不建立逐 session 的档案。

### 七层各自回答什么

```mermaid
flowchart TB
    S[系统层：如何在世界中行动] --> T[工具层：现在可以调用什么、操作什么]
    T --> P[人设层：我是谁，有什么性格、习惯、关系和偏好]
    P --> M[记忆层：之前发生过什么]
    M --> D[待办层：还有什么要继续做、今天打算做什么]
    D --> E[环境层：身体怎样，现在身处哪里]
    E --> G[进度层：这次 session 已经决定和做了什么]
    G --> L[LLM 根据这些内容作出下一步决策]
```

每层都填成 JSON。同一条消息、同一份工具说明、同一段历史结果只有一处完整内容。
其他地方可以提到名称，例如待办中的工作名称，不必再复制工作过程。

## 提升后的心智模型

### 业界方案概览

成熟 agent 的上下文组织可借鉴两点：把不同用途的信息分开；当前过程连续保留，较早过程整理为摘要。
本方案按用户指定的七层落实，参考来源与采用范围见[设计细节](research/persona-prompt-seven-layers.md#参考来源)。

### 提升后的设计

| 标准名称 | 填什么 | 怎样更新 |
| --- | --- | --- |
| 系统层（身份与行为准则） | 正式、简洁的执行规则：如何使用工具，怎样区分请求登记和执行结果 | 固定复用；具体人物姓名只在人设层 |
| 工具层（工具定义与可选目标） | 工具定义；每个目标对应的动作、方式、条件、价格、数量、耗时 | 当前可用内容更新后替换旧内容 |
| 人设层（人物卡片） | 基础信息、性格、习惯、关系、偏好 | 关系和偏好发生变化时原地更新；不追加当日经历或工作进度 |
| 记忆层 | 昨天及更早的摘要；当日更早的摘要 | 整理较早内容后加入相应部分；不要求一段 session 对应一份档案 |
| 待办层 | 暂停的 activity；今日 day_plan | 暂停项按暂停时间从老到新；恢复或结束后移出暂停项；计划保持原定顺序 |
| 环境层（此刻场景） | 身体状况、当前地点或路程、场所静态信息 | 替换为当前观察；不放可达地点目录、消息和活动经过 |
| 进度层 | 本 session 的决策、工具调用与结果、来信、送达、提醒 | 按实际发生顺序追加；不包括 thinking；session 结束后整理到记忆层 |

每层自己的 JSON Schema 就是这层的字段说明：字段叫什么、什么类型、可以填写什么。
它的目的在于让格式固定，避免再次混用散文、JSON 和类似 TOML 的文本。

### 图示留档

[交互图示 HTML](research/persona-prompt-framework.html)与本文一起保存在仓库中，可以直接用浏览器打开。
页面包含“七层与填充”“session 流转”“删除与归属”三种视图；每层都能切换查看 JSON 示例和 JSON Schema。
图示内嵌展示所需的数据，不读取会话目录或本机文件。

七份 Schema 分别维护各层字段，[examples.json](research/persona-prompt-schemas/examples.json)保存按七层顺序排列的
完整示例。字段或示例调整时，同步更新本文和 HTML；这些示例用于审阅设计，不是实际运行记录。

### 七层 JSON 字段设计

所有层都有字符串字段 `layer`，值固定为对应的中文层名。下面逐层记录字段含义，完整类型和允许值由所链接的
Schema 定义。没有内容的列表写 `[]`；允许为空的数量、耗时和提醒时间写 `null`，不填编造的值。

#### 系统层（身份与行为准则）

字段定义：[system.schema.json](research/persona-prompt-schemas/system.schema.json)。

| 字段 | 类型与含义 |
| --- | --- |
| `role` | 字符串，说明人物在模拟世界中的执行职责 |
| `rules` | 字符串列表，正式、简洁的行为规则，例如“动作登记不代表执行完成” |

#### 工具层（工具定义与可选目标）

字段定义：[tools.schema.json](research/persona-prompt-schemas/tools.schema.json)。

| 字段 | 类型与含义 |
| --- | --- |
| `definitions` | 工具定义列表；每项有 `type` 和 `function`，`type` 固定为 `function` |
| `definitions[].function` | `name` 是工具名，`description` 说明用途，`parameters` 定义参数，`strict` 表示是否严格按参数定义输出 |
| `targets` | 可选目标列表；每项有可读名称 `name`、类别 `kind`、对应动作列表 `actions` |
| `targets[].kind` | 地点、商品、人物、活动、内容、任务、自己或消息，使用 Schema 中对应的英文值 |
| `targets[].actions[]` | 一项动作及其方式：`action` 取 `travel/interact/obtain/consume/sleep`，`way` 是目标提供的方式名称 |
| `actions[].available`、`unavailable_reason` | 是否可执行，以及不能执行的原因；可以执行时原因为空 |
| `actions[].requirements` | 条件列表；每项用 `condition` 描述条件，`satisfied` 表示目前是否满足 |
| `actions[].price_fen` | 整数或空，价格，单位为分 |
| `actions[].available_quantity`、`quantity_unit` | 数量或空，以及对应单位；数量允许小数，例如 `0.5` 份 |
| `actions[].estimated_duration_seconds` | 整数或空，预计耗时，单位为秒 |

动作、方式和条件始终跟随所属目标，不能拆成互不对应的名称列表。

#### 人设层（人物卡片）

字段定义：[persona.schema.json](research/persona-prompt-schemas/persona.schema.json)。

| 字段 | 类型与含义 |
| --- | --- |
| `identity` | 基础信息对象：`name` 为姓名，`birth_year` 为出生年份 |
| `background` | 字符串列表，已经形成的背景经历 |
| `traits` | 字符串列表，性格特点 |
| `habits` | 习惯列表，每项有名称 `name` 和习惯描述 `pattern` |
| `relationships` | 关系列表，每项有对方 `person` 和当前关系 `relationship`；关系变化时更新原项 |
| `preferences` | 偏好列表，每项有对象 `subject` 和当前偏好 `preference`；偏好变化时更新原项 |

#### 记忆层

字段定义：[memory.schema.json](research/persona-prompt-schemas/memory.schema.json)。

| 字段 | 类型与含义 |
| --- | --- |
| `earlier_days` | 昨天及更早的摘要列表；每项仅有日期 `date` 和摘要 `summary` |
| `earlier_today` | 当日更早的摘要列表；每项仅有整理时刻 `recorded_at` 和摘要 `summary` |

摘要中直接写清必要事实，例如“已替王磊订一份牛肉炒面，当时等待配送”。不另存 session 状态或活动档案。

#### 待办层

字段定义：[todo.schema.json](research/persona-prompt-schemas/todo.schema.json)。

| 字段 | 类型与含义 |
| --- | --- |
| `paused_activities` | 按暂停时间从老到新排列的 activity 列表 |
| `paused_activities[].name`、`paused_at` | 活动的可读名称与实际暂停时刻 |
| `paused_activities[].resume_conditions` | 字符串列表，恢复这项活动的条件 |
| `paused_activities[].remaining` | `description` 说明剩余事项，`estimated_duration_seconds` 为剩余秒数，未知时为空 |
| `day_plan` | 今日计划对象，包含日期 `date` 和按计划顺序排列的 `goals` |
| `day_plan.goals[]` | `intent_kind` 为意图类型，`goal` 为目标，`progressed` 表示是否已推进，`remind_at` 为提醒时刻或空 |

`progressed` 为真只表示目标已经得到推进，不等同于目标完成。

#### 环境层（此刻场景）

字段定义：[environment.schema.json](research/persona-prompt-schemas/environment.schema.json)。

| 字段 | 类型与含义 |
| --- | --- |
| `observed_at` | 当前观察时刻 |
| `body` | 身体感受：`appetite` 为食欲，`sleepiness` 为困意，`energy` 为体力感受；都使用可读描述 |
| `location` | 当前地点或路程的信息对象 |
| `location.session_kind` | `stay` 表示地点停留，`transit` 表示路程 |
| `location.name`、`place_type` | 当前地点或路程的名称、场所类型 |
| `location.fixed_rules` | 字符串列表，场所的固定规则；动作的执行条件仍只在工具层描述 |

#### 进度层

字段定义：[progress.schema.json](research/persona-prompt-schemas/progress.schema.json)。

| 字段 | 类型与含义 |
| --- | --- |
| `session_started_at` | 当前 session 的开始时刻 |
| `events` | 按实际发生顺序排列的记录列表；同一时刻的记录保留先后顺序 |
| `events[].occurred_at`、`type` | 每条记录都有发生时刻和事件类型；其余字段按下表确定 |

| `type` | 内容字段 | 含义 |
| --- | --- | --- |
| `decision` | `decision` | 模型输出的决策，不含 thinking |
| `tool_call` | `call_id`、`tool_name`、`arguments` | 一次工具调用的名称和完整参数 |
| `registration` | `call_id`、`tool_name`、`status`、`message` | 请求登记信息；`status` 固定为 `registered` |
| `tool_result` | `call_id`、`tool_name`、`result` | 对应调用的公开结果 |
| `message_arrival` | `sender`、`channel`、`message` | 新消息的发送者、渠道和正文 |
| `delivery` | `order`、`recipient`、`delivery_status`、`message` | 订单、收货人及配送进展；状态取 `in_transit/delivered/failed` |
| `deadline` | `task`、`deadline_at`、`deadline_status`、`message` | 哪项事情何时截止；状态取 `due_soon/due/expired` |

`call_id` 用来对应一次调用及其结果。`result` 包含结果时刻 `occurred_at`、状态 `status`、
错误码或空 `error_code`、结果说明 `message` 和公开细节 `details`；工作结果还可以带公开反馈 `work_review`。
公开细节保留商品名称、数量、付款、收货人和送达地点等信息，不放内部身份或资源账户。

### session 结束时怎样整理进度

[原规格](SPEC-time-and-energy.md#三层结构)已经规定：出发前先结束或暂停手上的进行中活动；
到达时，出行和路上开始的活动一起结束，并在同一秒收尾。暂停的活动留在原地点，回去后可以恢复。

```mermaid
flowchart TB
    A[办公室 session：工作、下单等过程进入进度层]
    A --> B[出发前：进行中的 activity 全部结束或暂停]
    B --> C[出发：办公室 session 结束，开始路程 session]
    C --> D[到达：出行与路上 activity 全部结束并收尾]
    D --> E[开始新地点 session]
    B --> T[暂停的 activity 留在待办层，回原地点后恢复]
    C --> M[已有 summary 和需保留的结果，整理进记忆层]
    D --> M
```

这里的 session 结束是**整理记忆的时机**。记忆本身只有“昨天及更早”“当日更早”两部分，
里面放可读摘要，不复制 session 的状态、活动列表和整套运行记录。

整理时使用已有 pause/conclude 的 summary，并保留订单等仍有后续影响的结果，不新增工具或模型总结请求。
例如摘要写清“已替王磊订牛肉炒面，等待送达”；收到送达消息后，进度层记录送达结果。
下一次整理则保留已送达的结果，不能让旧代订请求成为唯一留下的信息。

一次操作被拒绝，就在进度层记录拒绝原因；它本身不会结束 session。

### 删除与去重

| 旧内容 | 处理 |
| --- | --- |
| 系统提示里的具体人物身份、经历与口语化生活劝导 | 身份只在人设层；经历进入记忆或进度；删除生活劝导 |
| 环境里的可达地点、商品、操作目录 | 完整放在工具层，保留目标与动作的对应关系 |
| 人物卡片里的当日处境与工作进展 | 当日已发生的过程放进度层，较早的内容放记忆层 |
| 已核验经历、主观归纳、最近人物自述等独立内容块 | 删除这些独立块，必要内容按时间进入记忆层或进度层 |
| 整包当前事实清单 | 删除；需要的内容分别放进指定的七层 |
| 专门生成再回贴的“刚才形成的自述” | 删除额外阶段，直接保留模型本次输出的决策 |
| 多处重复的消息正文和结果 | 只在进度层出现一次，之后按需整理入记忆 |
| 独立预算附录、调试清单、thinking | 不放进七层正文 |

## 当前实现的差距

### 差距归属图

```mermaid
flowchart LR
    S[系统层] --> SG[系统层职责与表达混杂]
    T[工具层] --> TG[可选目标分散与重复]
    P[人设层] --> PG[人物卡片混入可变处境]
    M[记忆层] --> MG[记忆缺少清晰的时间分区]
    D[待办层] --> DG[待办没有独立归属]
    E[环境层] --> EG[环境混入动态记录]
    G[进度层] --> GG[进度不是连续决策记录]
    S & T & P & M & D & E & G --> FG[格式与命名不统一]
```

### 差距明细

| 差距名称 | 当前问题 | 目标 |
| --- | --- | --- |
| 系统层职责与表达混杂 | 身份介绍、生活建议和自述要求混在一起 | 只保留正式的执行准则 |
| 可选目标分散与重复 | 场景、事实和工具多次描述目标 | 工具层完整描述一次 |
| 人物卡片混入可变处境 | 卡片混入本周任务和当日处境 | 保留人物信息；关系与偏好原地更新 |
| 记忆缺少清晰的时间分区 | 主观归纳和零散条目混杂 | 明确区分昨天及更早、当日更早 |
| 待办没有独立归属 | 暂停活动和 day_plan 分散 | 独立列出暂停事项与今日计划 |
| 环境混入动态记录 | 场景夹带消息、活动、目标和可达地点 | 只保留身体与当前场所信息 |
| 进度不是连续决策记录 | 过程被活动快照、最近自述与部分结果替代 | 本 session 的决策与结果连续可见 |
| 格式与命名不统一 | 格式和段名混用 | 按七层命名，全部 JSON，各自 Schema |

### 后续顺序

```mermaid
flowchart LR
    A[本轮：审阅七层设计与图示] --> B[实现一条完整决策与记忆更新流程]
    B --> C[统一修复已登记错误：截断、工时、工作、小数份消费]
    C --> D[真实运行与重复下单场景验收]
```

本轮不改模拟运行规则，不把设计示例或既有查看器测试当作新 Prompt 已生效的证明。
