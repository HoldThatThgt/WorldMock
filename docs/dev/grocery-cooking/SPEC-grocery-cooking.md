<a name="spec-grocery-cooking-买菜做饭规格"></a>
# 买菜做饭规格

> 状态：已冻结（2026-10-12）。所有者指示与[首次真实模型单日运行规格](../first-real-day-run/SPEC-first-real-day-run.md)一同实施，提案「按建议定下的细节」照建议执行；
> 两份规格「在地点做的事能不能解饿」的冲突由所有者裁决为以 `completion_effects.food` 为准，见「与首次真实模型单日运行规格的合并」，那一节优先于本规格其余各节和那份规格的对应条文。
> 日期：2026-10-12。
> 依据：[买菜做饭提案](PROPOSAL-grocery-cooking.md)。本规格把它落成可以直接实现和测试的规则；两者不一致时以提案为准。
> 代码基线：主干提交 `79304ff7`。文中的「今天」「现行」都指这个提交。
> 证据层级：读代码。没有调用模型，没有做过真实运行。
> 叫法：「状态」指 `callbacks.jsonl` 登记的回调状态；「凭状态的内容」指写了 `consumes_state` 的地点内容；
> 「开放」指人物身上这个状态的值不小于 1；「完成效果」指 `completion_effects` 声明、内容做完时结算一次的效果；
> 「单元」「工具层」等沿用设计文档《模拟引擎》《人物》的叫法；
> 「设计文档《某某》」指 `docs/design/` 下的同名模块正文，实施时先改它再改代码。

<a name="spec-grocery-cooking-目录"></a>
## 目录

| 章 | 内容 |
| --- | --- |
| [与首次真实模型单日运行规格的合并](#spec-grocery-cooking-合并) | 所有者裁决：供一餐并入完成效果；两份规格一同实施时各处的写法 |
| [目录行](#spec-grocery-cooking-目录行) | 内容行的两个新字段、完成效果的档位、行模型、登记校验与错误 |
| [列出与名称解析](#spec-grocery-cooking-列出与名称解析) | 未开放的单元不列出、照写仍能解析、不进候选 |
| [受理与用掉](#spec-grocery-cooking-受理与用掉) | 拒绝的次序与文字、开始时减 1、证据事件、阈值规则、暂停与结束 |
| [完成效果](#spec-grocery-cooking-完成效果) | 结算的时刻、各档位的数值与算法、证据、系列与截止 |
| [人物看到什么](#spec-grocery-cooking-人物看到什么) | 提示的变化与指纹 |
| [评估](#spec-grocery-cooking-评估) | 用餐与利用率指标不变 |
| [版本、身份与基线](#spec-grocery-cooking-版本、身份与基线) | 升号、旧环境包与旧运行、夹具与基线 |
| [设计文档与手册](#spec-grocery-cooking-设计文档与手册) | 要改的模块正文章节与手册原文 |
| [源码指引](#spec-grocery-cooking-源码指引) | 改哪些文件 |
| [测试要求](#spec-grocery-cooking-测试要求) | unit、integration、system 各测什么 |

<a name="spec-grocery-cooking-合并"></a>
## 与首次真实模型单日运行规格的合并

### 所有者的裁决

两份规格都给「在地点做完一件事算吃了一餐」定了写法：首次真实模型单日运行规格[「在地点吃的一餐」](../first-real-day-run/spec/07-meals.md)一章是每条地点内容必填的布尔 `provides_meal`，
本规格是可选的 `completion_effects.food`。两者同时实现时一行可以吃两顿，本规格手册里的例子行也会因没写 `provides_meal` 被拒。
所有者 2026-10-12 裁决：**以 `completion_effects.food` 为准，删除 `provides_meal`**。字段可选，省略就是做完不补能量；
「必填、引擎不补值」那一条随 `provides_meal` 一起撤销。那一章的其余规则（何时进账、按哪张表进账、提前结束与截止、证据落在哪条事件、
工具层 `note`、系统层讲解饿的一条、检查工具的进食指标）都保留，只是条件从「`provides_meal` 恰为 `true`」换成「`completion_effects.food` 已声明」。

下面逐项写定合并后的做法。凡首次真实模型单日运行规格写 `provides_meal`、`data.meal`、`settle_meal`、「供一餐」之处，按本节执行；本节没有提到的，两份规格各自照写。

### 字段与登记

- 内容行没有 `provides_meal` 字段，写了就按未知键以 `ENVIRONMENT_KIND_INVALID` 拒绝（行解析现有的分支）。不新增 `_check_meal_declaration`，不新增那两句登记详情。
- `completion_effects` 的成员、档位、行模型与登记检查照本规格「目录行」一节；「app 内容写了 `completion_effects`」由那一节登记检查的第 1 条拒绝。
- 样例环境 `tests/fixtures/dataset-environment/catalog/content-catalog.jsonl` 的四条地点内容不加任何键（不写 `provides_meal`，也不写两个新字段）；
  `tests/fixtures/display-wording-cases.json` 里的目录行同样不加。清单只因版本提交的三个版本字段和内容轨道删 `browse_duration_seconds` 而刷新。
- 首次真实模型单日运行规格「环境数据集字段的变化」表里 `provides_meal` 那一行作废，换成：`content-catalog.jsonl` 的地点内容可以新写可选的 `consumes_state`、`completion_effects`（本规格「目录行」）；
  不用的环境包只改两个版本号。交给数据集仓库的事里「给每条地点内容写供不供一餐」改成「需要供餐的地点内容写 `completion_effects.food`」。

### 结算

| 事项 | 合并后的做法 |
| --- | --- |
| 何时 | 一条写了 `completion_effects` 的地点内容做到末端的那一秒（内容证据 `completed` 为真的那次结算），在同一秒共同身体结算之后。提前结束、暂停期间、运行截止截断都不结算；恰在截止那一秒做完照常结算；检查点后重开不重复。即「在地点吃的一餐」一章的「暂停、恢复、提前结束、反复做」表，条件换成「写了 `completion_effects`」 |
| `food` | 数额 `GOODS_ENERGY_KCAL[档位]`（`meal` 600、`snack` 200），经 `life/body.py` 新增的公开函数 `food_intake(body, calories, config)` 结算；`food_intake` 照那一章的定义（调 `apply_food`、自己取走这一笔的分录、`applied` 取分录数额之和），食品收货的 `receipt._food_effect` 同样改调它，生产代码里只有它调用 `apply_food` |
| 两项缓解 | 本规格「完成效果」一节的算法：需要减去 `RELIEF_PER_HOUR[档位]`，不低于 0，`applied` 取登记的数额 |
| 次序 | `food`、`relaxation_relief`、`social_relief` |
| 落点 | 新模块 `life/completion_effects.py` 放三个成员的结算（纯函数，输入身体、声明、身体配置，返回新身体、证据与分录）；`InteractivityRuntime.settle_completion(state, activity, evidence)` 取代那一章的 `settle_meal`，读冻结目录里刚做完那条内容的 `completion_effects`，没有声明、`evidence` 为空或 `completed` 不为真时原样返回；`time_advance._apply_activity_effect` 只加调用与合并 |
| 分录 | 每个实得不为 0 的成员一条，排在这条 `activity_progress` 的 `resource_flows` 最前，次序同上：`food` 是 `{"resource": "energy_kcal", "owner": "agent:<人物身份>", "direction": "source", "amount": <applied>, "cause": "settle:shared:energy_kcal"}`；两项缓解是 `relaxation_need`、`social_need` 的 `drain`，`cause` 为 `content:completion`。分录由结算自己从缓冲取走，不留给同一秒后面的结算；同一秒的 `body_settlement` 不含完成效果（依赖「十条缺陷与思考额度」一章身体结算记录的修法），本规格「完成效果」一节「已知的牵连」那一段作废 |
| 不算的 | 不花钱、不产生 `goods_received`、不进购买投影、不重置回调状态、不累计个人事务、不算行为评估的食品多样性和质量报告的食品效率；与那一章「连带口径」表相同 |

### 证据

- 不加 `data.meal`。证据只有本规格「完成效果」一节的 `data.content.completion_effects`：没有结算时为 `null`；结算了是恰含 `food`、`relaxation_relief`、`social_relief` 三键的对象，
  未声明的成员为 `null`；`food` 是 `{tier, provided, applied, efficiency}`，两项缓解是 `{tier, provided, applied}`。
- 形状检查放在 `footprints/app_usage.py` 内容证据检查的同一个 `activity_progress` 分支里，内容证据字段闭集加入 `completion_effects`。下面任何一条不成立都是运行故障，
  写英文错误日志并抛 `IntegrityError`，文字只有一句 `Activity completion effects must use the current location completion contract`：
  值为 `null` 或对象；为对象时键恰为三个成员、`data.content.completed` 为真、`data.content.app_id` 为 `null`；每个非空成员的键恰如上述、`tier` 在该成员的档位表内。

### 人物读到什么

- 工具层 `note`（`location_activities.unit_facts`）照「在地点吃的一餐」一章的规则写，条件换成 `completion_effects.food`，并为 `snack` 档多一种说法：

  | 这件事 | 条件 | 多出来的说法 |
  | --- | --- | --- |
  | 独立的一件事 | `food` 为 `meal` | `供一餐` |
  | 独立的一件事 | `food` 为 `snack` | `供一份零食` |
  | 系列 | 有集的 `food` 为 `meal` | `第 K 集供一餐`，K 为这些集号，升序，以「、」相连 |
  | 系列 | 有集的 `food` 为 `snack` | `第 K 集供一份零食`，写法同上 |
  | 任何单元 | 没有 `food`，或只声明了两项缓解 | 不写 |

  说法的先后：第几集、接着做、供一餐、供一份零食，以「；」相连，都没有时 `note` 为 `null`。两项缓解成员与 `consumes_state` 不进 `note`，也不进任何提示层。
- 系统层讲解饿的一条（「人物读到的规则」一章的逐字定稿）改为下面这句，位置与其余十九条不变：

  > 解饿只有两种办法：一是收到买来的食品或零食，收到的那一刻吃掉；二是在地点做完一件工具层 note 写着「供一餐」或「供一份零食」的事，做完的那一刻算吃了，系列的 note 写的是第几集供一餐或供一份零食，做完那一集时算。中途用 conclude_activity 结束的不算，暂停后恢复做完的照算。note 没写这两种说法的事不解饿，名称像吃饭也一样。

  「人物读到的规则」一章其余转述「供一餐」的句子照此理解，不另改系统层。
- 本规格「人物看到什么」一节「系统层与工具说明一字不改，指纹不变」作废：系统层与工具层随首次真实模型单日运行规格一起变。代之以：
  同一环境的两份副本只差 `consumes_state`（已开放）与两项缓解成员时，人物各层提示逐字节相同。

### 检查工具

`scripts/inspect_run.py` 的 `_location_meals(conn)` 取 `data.content.completion_effects.food` 不为 `null` 的 `activity_progress`（两个档位都算一次进食）；`meal_count`、`lowest_energy_before_meal` 照「在地点吃的一餐」一章的口径用它。

### 测试与变异

- 「在地点吃的一餐」一章的测试照写，构造方式换成：`tests/life/environment_inputs.py` 的 `meal_environment(destination)` 给「测试休息」（`c-rest`）写 `"completion_effects": {"food": "meal"}`，
  「做晚饭」三集系列只有《吃晚饭》（`dinner-2-eat`）写 `{"food": "meal"}`；需要零食档或缓解档的用例在这份副本上再改一行。
  那一章「地点内容必须写」「值不是布尔」「app 内容不得写 `provides_meal`」「检查排在前提之后」「样例环境四条都是 `false`」几条测试不写；
  对应的约束由本规格「测试要求」里目录的各条覆盖，另加一条：写了 `provides_meal` 的行以 `ENVIRONMENT_KIND_INVALID` 被拒。
- 那一章的五条变异改为：「app 内容写了 `completion_effects` 也接受」（本规格登记检查第 1 条改成恒假）；「写了 `completion_effects` 的内容做完不结算」；
  「没做到末端也结算」；「工具层不写供一餐」。「地点内容没写时不拒绝」随必填撤销而不登记。
- 本规格另登记下面几条，各取改坏中心规则的那一种，杀手是本规格「测试要求」里对应的行为测试：
  `consumes_state` 名称未登记也接受；系列各集 `consumes_state` 不同也接受；未开放的单元仍列在工具层；未开放的单元能开始；
  开始时不减状态；恢复暂停的活动再减一次；未开放的单元进入名称候选；`consume_on_start` 的起因不改指受理结果。

### 提前结束之后

本规格「没做完的进度沿用今天的规则」写于首次真实模型单日运行规格之前。那份规格把在地点做的事改成「结束」就算做完一轮、下次点名从头做起，
所以合并后照它执行：凭状态的事提前结束时已减的不退、没有完成效果，下次开放后再点名是从头做起（系列从第一集），开始时再减 1，做完结算完成效果。
本规格「测试要求」里「下次开放后再点名从保存的进度接着做」一句按此改为「从头做起」；「暂停后恢复不减、不再拒绝」不变。

### 版本与实施轨道

- 本规格的两个号（规则版本 4 改 5、目录格式 4 改 5）与首次真实模型单日运行规格的版本提交是同一次升号，不另升；主干上各变一次。
- 本规格的实现归那份规格的内容轨道，排在「打开 app 与 app 目录字段」之后，与「在地点吃的一餐」合成一段做；设计文档与手册的改动并入「文档先行」那一步。
- 本规格提到 `query_world` 的两处（未开放的单元不出现在它的结果里）随 `query_world` 删除而不再适用；新的查询只列商品，不列地点单元。

<a name="spec-grocery-cooking-目录行"></a>
## 目录行

### 两个新字段

`content-catalog.jsonl` 的内容行新增两个可选字段，其余字段不变：

| 字段 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `consumes_state` | 字符串 | 只能写在顶层 `location_kinds` 非空的内容行上；非空白；须是 `callbacks.jsonl` 登记的一个状态名；省略表示不凭状态，显式 `null` 拒绝 | 这件事只在人物的这个状态不小于 1 时列出和开始，开始时这个状态减 1 |
| `completion_effects` | 对象 | 只能写在顶层 `location_kinds` 非空的内容行上；至少声明一个成员；省略表示做完没有效果，显式 `null` 拒绝 | 这条内容做完时结算一次的效果 |

两个字段互不要求：只写 `consumes_state` 是「开放一次、做完没有效果」的事，只写 `completion_effects` 是「随时能做、做完有效果」的事。
一个状态可以被多条内容写进 `consumes_state`，开放后人物做其中任何一件都用掉它。

### 完成效果的成员与档位

`completion_effects` 只有下面三个成员，其他键拒绝。成员的值只写档位名，不写数值；每个成员都可以省略，省略就是没有这项效果；
成员写成 `null` 拒绝，档位 `none` 不收，「没有」只有省略一种写法。

| 成员 | 档位 | 做完时的效果 | 数值的唯一出处 |
| --- | --- | --- | --- |
| `food` | `snack`、`meal` | 补 200 或 600 千卡，按食品利用率和能量上限结算 | 商品类别的收货热量，`exchange/constants.py::GOODS_ENERGY_KCAL` |
| `relaxation_relief` | `low`、`medium`、`high` | 放松需要一次减少 0.10、0.30、0.50，不低于 0 | 内容按小时缓解的档位，`life/interact.py::RELIEF_PER_HOUR` |
| `social_relief` | `low`、`medium`、`high` | 社交需要一次减少 0.10、0.30、0.50，不低于 0 | 同上 |

档位名在整个系统里只有一种含义：`food` 的档位与商品类别同名同热量；两项缓解的档位与内容行顶层的同名字段同一套，
做完一次的量等于这一档缓解一小时的量。实现读这两张既有的表，不复制数值。
内容行顶层的 `exertion`、`relaxation_relief`、`social_relief` 不变，仍按实际做了多久结算；完成效果另外结算，与它们相加。

### 行模型

`catalog/models.py` 新增严格冻结模型 `CompletionEffects`，`Content` 增加两个成员：

- `Content.consumes_state: NonEmptyString | None = None`，以 `mode="before"` 校验器拒绝显式 `null`，原因 `A declared consumed state must be a state name.`；
- `Content.completion_effects: CompletionEffects | None = None`，以 `mode="before"` 校验器拒绝显式 `null`，原因 `A declared completion effect must be an object.`；
- `CompletionEffects.food: Literal["snack", "meal"] | None = None`；
- `CompletionEffects.relaxation_relief: Literal["low", "medium", "high"] | None = None`；
- `CompletionEffects.social_relief: Literal["low", "medium", "high"] | None = None`；
- 三个成员各以 `mode="before"` 校验器拒绝显式 `null`，原因 `A declared completion effect tier must be a tier name.`。

拒绝 `null` 的写法与 `prerequisite` 相同（`require_declared_prerequisite`）。目录不为两个字段新建索引。

例（文件里各是一行，这里折开显示）：

```json
{"state": "groceries", "initial": 0, "cap": 1, "reset_on_goods": ["meat-pack", "veg-pack"]}
{"content_id": "cook", "title": "做饭", "location_kinds": ["home"], "duration_seconds": 3600,
 "exertion": "light", "pushable": false, "relaxation_relief": "low", "social_relief": "none",
 "summary": "用买来的食材做一顿饭，吃完收拾。",
 "summary_source": {"kind": "synthetic", "url": null, "notice": "数据集作者编写"},
 "consumes_state": "groceries", "completion_effects": {"food": "meal"}}
```

### 行解析的错误码

- 内容行或 `completion_effects` 里的未知键照旧报 `ENVIRONMENT_KIND_INVALID`。
- `consumes_state` 类型不对、空白或显式 `null` 报 `ENVIRONMENT_KIND_INVALID`。
- `completion_effects` 不是对象或显式 `null`，成员不是表内档位（含 `none`、数字和 `null`），报 `ENVIRONMENT_KIND_INVALID`。
- 两个字段都没有数值，`catalog/loader.py::parse_catalog_model` 的数值字段集合不变。
- 详情照旧以 `content-catalog.jsonl:<行号>` 开头。

### 登记校验

`catalog/admission.py::_check_contents` 多收一个参数：登记的全部状态名，取 `{row.state for row in rows["callback_states"]}`；
没有 `callbacks.jsonl` 时为空集。状态行此时已解析并核过次序与重复，`initial`、`cap` 的核对仍在 `_check_callbacks`。

每行内容按现有次序检查，新检查排在前提检查之后、系列检查之前，先遇到的先报：

| 次序 | 检查 | 登记码 | 详情 |
| --- | --- | --- | --- |
| 1 | 写了 `consumes_state` 或 `completion_effects`，而这是 app 内容（`app_ids` 非空） | `ENVIRONMENT_KIND_INVALID` | `<content_id> is an app content; consumes_state and completion_effects belong to location contents.` |
| 2 | `completion_effects` 三个成员都省略 | `ENVIRONMENT_KIND_INVALID` | `<content_id> declares an empty completion_effects.` |
| 3 | `consumes_state` 不是登记的状态名 | `ENVIRONMENT_REF_UNKNOWN` | `<content_id> consumes_state names <状态名>.` |

`_check_series` 在核对前提相同之后，再核对同一系列各集的 `consumes_state` 相同（都不写，或写同一个状态名），
不同报 `CONTENT_SERIES_INVALID`，详情 `<系列名> episodes declare different consumed states.`。
`completion_effects` 各集各写，不要求相同。

不另加的检查：

- 状态的 `cap` 为 0 时，凭它的内容永远不会开放；与 `opens` 一个时段都不写一样是合法写法，登记不拦，手册提醒。
- `reset_on_goods` 为空的状态也可以被凭；这样的事只靠 `initial` 或回调的状态增量开放。
- 不检查 `reset_on_goods` 里的商品类别；写成零食或食品时收货照旧补能，手册提醒。

设计文档《资源引擎》「注册校验与错误」的拒绝码表：`ENVIRONMENT_KIND_INVALID` 一行补「app 内容写了 `consumes_state` 或 `completion_effects`；
`completion_effects` 为空、写成 `null`、有未知成员或档位不在表内」，`ENVIRONMENT_REF_UNKNOWN` 一行补「`consumes_state` 不是登记的状态」，
`CONTENT_SERIES_INVALID` 所在说明补「各集的 `consumes_state` 不同」。

<a name="spec-grocery-cooking-列出与名称解析"></a>
## 列出与名称解析

一个单元凭状态，当且仅当它的内容写了 `consumes_state`（系列各集相同，取任一集）。
凭状态的单元未开放，当且仅当本人 `AgentState.callback_states[<状态名>] < 1`。

- **不列出。** 人物停留在某地点时，该地点目标行下未开放的单元没有 interact 行；其余单元照旧完整列出、按单元键升序。
  `query_world` 查到的行同样不含它。其他地点本来就不列单元，不变。
- **照写仍能解析。** 私有绑定照旧含每个单元，未开放的单元在私有绑定里带不进提示的键 `locked: true`，其余单元为 `false`。
  名称解析（`name_resolution.py::name_book`）照旧在私有绑定的 interact 选项里解析 `detail`，所以人物照写一个未开放的单元名能解析到它，交给受理拒绝。
  这与按名称购买未展示的商品同类：未展示，按名称仍能找到。
- **不进候选。** 名称解析失败时给出的候选不含未开放的单元，即使它与写错的名称最接近。
- **开放与否只看本人此刻的状态值**，与这个单元是否被暂停的活动占着无关：暂停中凭状态的活动已用掉开放，状态为 0 时单元不列出，
  恢复走待办层 `paused_activities` 的 report_status，与今天相同；状态再次开放时单元照旧列出，被占时按「单元被占」显示不可用。
- 完成效果不改变列出：工具层的行不提完成效果，行的字段、时长和说明与其他单元相同。

设计文档《人物》「工具层」的「在地点做的一件事」表加一行，排在「被本人暂停的活动占着」之后：

| 在地点做的一件事 | 工具层该单元的行 | 另外出现在 |
| --- | --- | --- |
| 凭状态而未开放 | 不列出 | 无 |

<a name="spec-grocery-cooking-受理与用掉"></a>
## 受理与用掉

### 拒绝

`life/admission.py::_location_refusal` 的检查次序变为下表，新检查排在最后。
「单元被占」排在它前面，是为了让人物照写一件已暂停的事时得到「先恢复」的原因，而不是「没开放」。

| 次序 | 检查 | 拒绝码 | 何时不成立 |
| --- | --- | --- | --- |
| 1 | 营业 | `LOCATION_CLOSED` | 地点声明了营业时段而此刻不在其内 |
| 2 | 取件 | `PARCEL_NOT_READY` | 地点是驿站，而本人在此没有已到站的包裹 |
| 3 | 单元被占 | `RESOURCE_CONFLICT` | 该单元的某条内容是本人一项暂停中活动的当前内容 |
| 4 | 未开放 | `ACTIVITY_PRECONDITION_UNMET` | 单元凭状态，而本人这个状态小于 1 |

未开放的拒绝：

| 字段 | 取值 |
| --- | --- |
| `error_code` | `ACTIVITY_PRECONDITION_UNMET` |
| `reason` | 状态的 `reset_on_goods` 非空时 `<单元名> is not available now; receiving <商品名> makes it available.`，商品名按 `goods_id` 升序用 ` or ` 连接；为空时 `<单元名> is not available now.` |
| `next_step` | 无 |
| `fix` | 无；这是世界条件造成的拒绝 |
| `unmet` | `("callback_state",)` |

这是普通业务拒绝：整组不开始、不扣款、不消耗随机流、不改状态，照常计入同刻决策轮数，不属于名称类拒绝。
拒绝由 `location_activities.py` 里一个新函数产生，工具层与受理共用它；未开放的单元不出现在工具层，所以这段文字只在受理结果里出现。

### 开始时减 1

整组受理成立后，凭状态的单元开始时，在同一个世界副本、同一次提交里把本人这个状态减 1：`after = before - 1`。
受理已保证 `before >= 1`，所以不需要下限钳位；`after < 0` 是运行故障，记英文错误日志后抛 `IntegrityError("Consumed state fell below zero")`。

- **位置。** 在 `life/interact.py::InteractivityRuntime.start` 里，紧接驿站取件（`_collect`）的同一位置：活动放进世界之后，取件之后。
  同一次受理在驿站开始一件凭状态的事时，先取件（收货可能把某个状态置为上限），再减 1。
- **只在开始时。** 恢复暂停的活动、系列自动接下一集、提前结束、运行截止都不减、不退。下次点名这个单元是新的开始，再减 1。
- **整组被拒不减。** 世界副本整份丢弃，与今天的取件相同。

### 证据事件

每次减 1 写一条 `callback_effect`，新增一种效果正文，加进 `footprints/callback_contract.py` 和 `footprints/environment_events.py` 的正文对照表：

| 字段 | 取值 |
| --- | --- |
| `kind` | `callback_effect` |
| `type` | `consume_on_start` |
| `rule_id` | `null` |
| `state` | 状态名 |
| `content_id` | 开始的那条内容 |
| `before`、`after` | 减 1 前后的值，都不小于 0 |

信封与另外两种状态效果相同：`environment_event`，来源 `environment`，可见性 `system`，`resource_changes` 为空，`caused_by` 恰一项。
起因是这次开始调用的 `action_result`：在 `decision_coordinator._accept_results` 里，与取件的 `goods_received` 一样，
把还没有起因的这条草稿改指主调用的受理结果。

同一处为这条草稿登记回调原因 `CallbackCause("action", "interact", <agent_id>, <该 action_result 的事件身份>, "<action_id>:consume")`，
放进 `context.callback_causes`。锚点用 `:consume` 结尾，与动作回调的锚点 `<action_id>:0` 区分。

### 阈值规则与提醒

减 1 是一次真实的状态变化，沿用回调的既有规则，不另写：

- 提交前 `compare_callback_states` 比较提交前后的状态值，越过阈值时由 `_crossing_source` 找到这条 `consume_on_start` 作为来源，
  阈值规则的起因是这次开始调用的受理结果；同一次提交里其他效果抵消了这次变化时不触发。
- 阈值提醒按状态的值判定，到下一个合格的整点照旧可能发出。
- 订阅 `interact` 的动作规则照旧对这次开始触发一次，与凭不凭状态无关。

设计文档《模拟引擎》「回调事务」补一句：凭状态的地点内容开始时减 1，和状态增量一样可以成为阈值触发的来源。
《资源引擎》「声明式回调」补一句：状态除了被回调效果和收货重置改变，还被凭它的地点内容在开始时减 1。

<a name="spec-grocery-cooking-完成效果"></a>
## 完成效果

- **时刻。** 一条写了 `completion_effects` 的地点内容在某次推进里做完（`interact.py::InteractivityRuntime.advance` 里 `completed` 为真）时，
  在同一次推进、该活动的进度结算里结算。它落在该人物这段区间的共同身体结算之后，与到店购买收货补能的位置相同。
- **只在做完时。** 提前结束、暂停、运行截止截断而没做完的内容没有完成效果，不按做了多久折算。恰在运行截止那一秒做完的内容照常结算。
- **系列。** 每一集做完时结算这一集自己的 `completion_effects`；没写的那集没有。
- **只限地点内容。** app 内容不能写这个字段，见「目录行」。

三个成员按 `food`、`relaxation_relief`、`social_relief` 的次序结算，互不影响：

| 成员 | 算法 | 实得 |
| --- | --- | --- |
| `food` | 调用 `life/body.py::apply_food(body, GOODS_ENERGY_KCAL[档位], config)`：利用率按此刻欠的觉取食品利用率曲线，能量不超过 2400 千卡上限，超出部分作废；与食品收货完全相同，不另设参数 | `min(声明热量 × 利用率, 2400 − 结算前的能量)` |
| `relaxation_relief` | 放松需要减去 `RELIEF_PER_HOUR[档位]`，不低于 0 | `min(档位的量, 结算前的放松需要)` |
| `social_relief` | 社交需要减去 `RELIEF_PER_HOUR[档位]`，不低于 0 | `min(档位的量, 结算前的社交需要)` |

### 证据

活动进度事件 `activity_progress` 的 `data.content` 增加一个键 `completion_effects`，`footprints/app_usage.py` 里内容证据的字段闭集随之加入它：

| 情形 | `completion_effects` |
| --- | --- |
| 这次推进没有做完内容，或做完的内容没写 `completion_effects`，或是 app 内容 | `null` |
| 这次推进做完了一条写了 `completion_effects` 的地点内容 | 恰含 `food`、`relaxation_relief`、`social_relief` 三个键的对象；没有声明的成员为 `null` |

| 成员的证据 | 字段 |
| --- | --- |
| `food` | `{tier, provided, applied, efficiency}`：`tier` 是档位名；`provided` 是档位的热量（整数）；`applied` 是这一笔实际登记的补能数额，到上限时为 0；`efficiency` 是这一笔用的食品利用率 |
| `relaxation_relief`、`social_relief` | `{tier, provided, applied}`：`provided` 是档位的量；`applied` 是实际减少的量，需要已为 0 时为 0 |

`applied` 一律取登记的数额，不用结算前后的值相减。
身体分录留在缓冲区，由时间推进照今天的做法写进这条 `activity_progress` 的 `resource_flows`：
补能方向 `source`，原因沿用 `apply_food` 的 `settle:shared:energy_kcal`；两项需要的减少方向 `drain`，原因 `content:completion`；实得为 0 的成员不写分录。

已知的牵连：今天每一步末尾 `rhythm.settle_engine_rhythm` 把这一步的 `body_settlement.after` 换成人物最终的身体，
所以同一步里的收货补能已经会出现在 `body_settlement.after` 里；完成效果同样如此。本规格不改这一点；
新测试不得在有完成效果的那一步断言 `body_settlement` 的「结算后等于结算前减消耗」。

设计文档《交换引擎》「身体共同结算」的「真实食品收货才增加能量」改为「真实食品收货和地点内容的完成效果才增加能量」；
「放松与社交需要」在内容缓解档位那一段之后补完成效果的一次性减少，数值引用同一张档位表；
「收货与费用」补一句完成效果的 `food` 与同名商品类别同热量、同一条利用率曲线和上限。
《记录引擎》登记 `consume_on_start` 正文与 `completion_effects` 证据键。

<a name="spec-grocery-cooking-人物看到什么"></a>
## 人物看到什么

- 工具层：未开放的单元不列出，开放后照常列出，行的字段、时长和说明与其他单元相同，不提完成效果。
- 受理结果：照写未开放的单元得到上文的拒绝原因；开始结果与其他地点内容相同，不提状态。
- 进度层、记忆层：照旧展示活动的结算与结束；完成效果体现在饥饿、放松与社交体感上，不另写句子。
  状态的数值、`consume_on_start` 事件和完成效果的证据都不进任何提示层。
- 系统层与工具说明一字不改。`prompt_sha256` 与 `tool_schema_sha256` 不变；实现后重算确认。

<a name="spec-grocery-cooking-评估"></a>
## 评估

行为评估的 `meal_diversity` 与质量报告的 `conversion_efficiency` 都只读 `goods_received`，本规格不改：

- `meal_diversity` 量的是买来的食品的种类；做饭没有菜品身份，每晚的「做饭」若算成同一件商品，会被读成天天吃同一道菜。
- `conversion_efficiency` 的样本只取收货；完成效果的 `food` 用同一条利用率曲线，不提供新信息。

行为评估标识 `behavior-metrics-4` 与质量报告结构号都不变。设计文档《行为评估》补一句：地点内容的完成效果不算用餐收货。

<a name="spec-grocery-cooking-版本、身份与基线"></a>
## 版本、身份与基线

升号原则沿用[环境数据集合同修订规格](../environment-contract-revision/SPEC-environment-contract-revision.md)「版本、身份与基线」一章：
结构的字段集合、取值闭集、字段含义或可接受的取值域变了才升号，各升一次，旧值严格拒绝，不写迁移。

| 名称 | 定义处 | 今天 | 改后 | 为什么 |
| --- | --- | --- | --- | --- |
| 规则版本 `RULES_VERSION` | `src/worldmock/exchange/constants.py` | 4 | 5 | 引擎多了固定规则：地点内容开始时减状态、做完结算完成效果 |
| 清单格式 `format_version` | `src/worldmock/catalog/models.py::CatalogManifest` | 4 | 5 | 内容行的字段集合变了 |
| 世界状态 `WORLD_SCHEMA_VERSION` | `src/worldmock/life/models.py` | 不变 | 不变 | 状态已经保存在 `AgentState.callback_states`，活动私有数据不变 |
| 导出清单 `schema_version` | `src/worldmock/footprints/export.py` | 不变 | 不变 | 工件集合与清单字段不变；新增的效果正文与证据键由规则版本和代码身份守住 |
| 质量报告、行为评估标识 | 见「评估」 | 不变 | 不变 | 键与指标不变 |
| `persona.toml`、`config.toml` | `src/worldmock/input_models.py` | 不变 | 不变 | 不加字段 |

- 两个号只在设计文档《资源引擎》「环境目录文件」维护；手册《设计环境数据集》里「写 4」的两处改成 5。
- 全部环境包改两个版本号、刷新清单后重新发布，旧号的包以 `RULES_VERSION_MISMATCH` 拒绝；不用这个功能的包只改版本号。
- 已有运行不能恢复、重放，也通不过导出认证：规则版本和代码身份都变了。
- 测试夹具 `tests/fixtures/dataset-environment/` 改版本号并刷新清单，不使用两个新字段；用到新字段的情形由测试在夹具副本上构造并刷新清单。
- 两份完整日基线和查看器的录制样本按既定办法重新冻结：夹具的目录摘要和代码身份变了，行为不变。
- 数据集仓库的环境包重新发布属于数据集仓库的工作，不在本仓库实施范围内。

<a name="spec-grocery-cooking-设计文档与手册"></a>
## 设计文档与手册

先改设计文档：

- 《资源引擎》「环境目录文件」：两个版本号改 5。「app、内容与候选呈现」：两个新字段与完成效果三个成员的约束与含义。「注册校验与错误」：上文各登记码的补充。「声明式回调」：状态被凭它的地点内容减 1。
- 《模拟引擎》「出行、内容与目录浏览」：在地点做事的检查次序表加「未开放」一行；单元的列出规则；开始时减 1；做完结算完成效果。「回调事务」：减 1 是阈值触发的来源之一。
- 《交换引擎》「身体共同结算」「放松与社交需要」「收货与费用」：见「完成效果」。
- 《记录引擎》：`consume_on_start` 正文与 `completion_effects` 证据键。
- 《人物》「工具层」：「在地点做的一件事」表的新行。
- 《行为评估》：完成效果不算用餐收货。
- 《概述》：「零食和食品按收货当秒的利用率与能量上限补充能量」之后补「地点内容可以声明做完时的一次性效果」，「不创建库存或持有物」一句不变。

手册《设计环境数据集》（`docs/manual/resources.md`）在 app 内容前提的条目之后、流程图之前加入下面一段：

````markdown
用 location_kinds 绑定到地点的内容可以多写两个字段：consumes_state 让这件事收到某些商品之后才出现、做一次就用掉，
completion_effects 写这件事做完时的一次性效果。「买菜做饭」就这样写：

```json
{"state":"groceries","initial":0,"cap":1,"reset_on_goods":["meat-pack","veg-pack"]}
{"category":"goods","goods_id":"veg-pack","name":"蔬菜包","price_fen":2500,"store_kinds":["supermarket"]}
{"content_id":"cook","title":"做饭","location_kinds":["home"],"duration_seconds":3600,"exertion":"light","pushable":false,"relaxation_relief":"low","social_relief":"none","summary":"用买来的食材做一顿饭，吃完收拾。","summary_source":{"kind":"synthetic","url":null,"notice":"数据集作者编写"},"consumes_state":"groceries","completion_effects":{"food":"meal"}}
```

第一行写在 callbacks.jsonl，第二行写在 merchant-catalog.jsonl，第三行写在 content-catalog.jsonl。

- 状态的 cap 写 1：买一件还是十件、买一次还是三次，收货后都只开放一次，开始做饭就用掉，想再做要再买。
  initial 写 0 表示运行开始时家里没菜，写 1 表示有。
- 食材商品的类别写 goods。写成 snack 或 meal 会在收货时补能，人物就等于吃了两顿；goods 收货时只让放松需要减 0.10。
- 没开放时人物在家看不到「做饭」；照写也做不了，原因里列出收到哪些商品能开放它。开始之后它不再列出，直到下一次收到食材。
- completion_effects 只写档位名，不写数值，三个成员都可以省略，但至少写一个：
  food 写 snack 或 meal，做完补的能量与收到一件零食或食品相同；
  relaxation_relief、social_relief 写 low、medium 或 high，做完时放松或社交需要一次减少的量，等于内容行上同名档位缓解一小时。
  没有某项效果就省略它，不要写 none 或 null。
- 内容行原有的 exertion 和两项缓解照旧按实际做了多久结算；completion_effects 只在做完时结算一次，提前结束或运行截止都没有。
  系列每集做完时结算那一集写的效果。
- 两个字段只能写在地点内容上，互不要求：只写 completion_effects 就是随时能做、做完有效果的事，例如父母家的「吃饭」写
  {"food":"meal","social_relief":"high"}。不要让同一顿饭算两次：公司的「吃午饭」写了 food，人物点了外卖再去吃午饭就吃了两顿。
- 系列各集的 consumes_state 必须相同；开始这件事减一次，自动接下一集不再减。
- 状态的 cap 写 0 时这件事永远不会出现；状态被用掉时也能触发回调的阈值规则，例如食材用完时让买菜 app 推一条提醒。
````

<a name="spec-grocery-cooking-源码指引"></a>
## 源码指引

- `src/worldmock/catalog/models.py`：`CompletionEffects`；`Content` 的两个成员；各处拒绝 `null` 的校验器；`CatalogManifest.format_version` 改为 5。
- `src/worldmock/catalog/admission.py`：`_check_contents` 收状态名集合；新增一个检查函数放三条新检查；`_check_series` 多核对 `consumes_state`。
- `src/worldmock/exchange/constants.py`：`RULES_VERSION` 改为 5；`GOODS_ENERGY_KCAL` 原样被完成效果读取。
- `src/worldmock/life/location_activities.py`：单元是否凭状态、是否开放、未开放拒绝的生成；`unit_binding` 带 `locked`。
- `src/worldmock/life/observe.py`：`_catalog_bindings` 按本人状态写 `locked`。
- `src/worldmock/life/tool_layer.py`、`src/worldmock/life/world_query.py`：跳过 `locked` 为真的行。
- `src/worldmock/life/name_resolution.py`：`locked` 的选项能解析，不进候选。
- `src/worldmock/life/admission.py`：`_location_refusal` 的第四项检查。
- `src/worldmock/life/interact.py`：开始时减 1 并产生草稿；做完时调用完成效果并把证据写进内容证据。`RELIEF_PER_HOUR` 原样被完成效果读取。
- 新模块 `src/worldmock/life/completion_effects.py`：按档位结算三个成员、产生分录与证据，纯函数，输入身体、声明与身体配置。
- `src/worldmock/life/decision_coordinator.py`：`_accept_results` 为 `consume_on_start` 草稿改指起因并登记回调原因。
- `src/worldmock/footprints/callback_contract.py`、`src/worldmock/footprints/environment_events.py`：`ConsumeOnStartResult` 与正文对照表。
- `src/worldmock/footprints/app_usage.py`：内容证据字段闭集加 `completion_effects`，并核对它的形状。
- `tools/run-viewer.html`、`scripts/inspect_run.py`：按效果类型显示回调效果的地方补 `consume_on_start`。
- 函数不超过 50 行、5 个参数，注释用中文，日志与报错用英文；`life/engine.py` 不加业务逻辑。

<a name="spec-grocery-cooking-测试要求"></a>
## 测试要求

- unit，目录：两个字段合法的行通过；`consumes_state` 或 `completion_effects` 写在 app 内容上，报 `ENVIRONMENT_KIND_INVALID` 和上文详情；
  状态名未登记、没有 `callbacks.jsonl` 时写了 `consumes_state`，报 `ENVIRONMENT_REF_UNKNOWN`；
  `completion_effects` 为 `null`、`{}`、数组、含未知成员、成员为 `null`、`food` 为 `none` 或 `600` 或 `"dinner"`、`relaxation_relief` 为 `none` 或 `0.3`，各报 `ENVIRONMENT_KIND_INVALID`；
  `consumes_state` 为 `null`、空白、数字，报 `ENVIRONMENT_KIND_INVALID`；
  系列各集 `consumes_state` 不同报 `CONTENT_SERIES_INVALID`，`completion_effects` 不同可以通过；新检查与前提检查、系列检查的先后次序；
  `cap` 为 0 的状态被凭可以通过；旧的 `format_version` 4 与 `rules_version` 4 被拒绝。
- unit，列出与解析：状态为 0 时单元不在工具层、在私有绑定里 `locked` 为真；状态为 1 时照常列出；名称解析能解析到未开放的单元；
  写错的名称的候选不含未开放的单元；`query_world` 不含它。
- unit，拒绝：未开放拒绝的四个字段，`reset_on_goods` 非空与为空两种原因文字，多个商品名的连接与次序；
  被暂停的活动占着且状态为 0 时报单元被占，不报未开放。
- unit，完成效果：`food` 两个档位的 `provided` 与 `GOODS_ENERGY_KCAL` 相同，欠觉不超过 8 小时时 `meal` 实得 540；到上限时 `applied` 为 0 且没有分录；
  两项缓解三个档位的 `provided` 与 `RELIEF_PER_HOUR` 相同；需要低于档位的量时减到 0，`applied` 等于结算前的需要；三个成员的结算次序与互不影响；`applied` 取登记值。
- integration：真实引擎走完「收货—列出—开始—做完」：外卖或到店收到食材后，在家工具层出现单元；开始的那次提交里状态从 1 到 0，
  有一条 `consume_on_start`，起因是这次开始调用的 `action_result`；开始后单元不再列出；做完那一步 `activity_progress` 的 `completion_effects` 与能量变化一致，
  `resource_flows` 有一条 `source` 的能量分录。
- integration：只写 `completion_effects`、不凭状态的地点内容随时列出，做完结算；写了缓解档位的，放松或社交需要按档位减少，分录原因 `content:completion`。
- integration：收到两件食材、收货两次，状态仍为 1；做一次之后状态为 0。
- integration：整组被拒（例如同组的副调用余额不足）时状态不变，没有 `consume_on_start`。
- integration：暂停后恢复不减、不再拒绝；提前结束不退、没有完成效果；下次开放后再点名从头做起（见「与首次真实模型单日运行规格的合并」「提前结束之后」），再减 1，做完结算完成效果。
- integration：系列凭状态：开始减一次，自动接下一集不减；只有写了 `completion_effects` 的那集做完时结算。
- integration：阈值规则：`below` 为 1 的状态规则在开始做饭的那次提交里触发一次，起因指向这次开始调用的受理结果；同一次提交里另有效果把状态加回来时不触发。
- integration：驿站上一件凭状态的事：同一次受理先取件，取件里的收货把状态置为上限，再减 1。
- integration：运行截止恰在内容做完那一秒时结算完成效果；截止截断没做完的内容没有。
- integration：导出认证通过，含 `consume_on_start` 与带 `completion_effects` 的内容证据；改写已导出事件（`consume_on_start` 多一个键、`before` 为负、`caused_by` 两项、
  `completion_effects` 少一个成员键、成员证据多一个键、`tier` 不在表内）各报对应的英文错误。
- integration：恢复与重放：在开始做饭之后、做完之前的检查点恢复，状态值、后续完成效果与不间断运行相同，不重复减。
- integration：同一世界在本规格实施前后、不用两个新字段时，人物各层提示逐字节相同；`prompt_sha256` 与 `tool_schema_sha256` 不变。
- system：夹具改版本号后完整验证通过；重冻的两份完整日基线通过导出自查。
