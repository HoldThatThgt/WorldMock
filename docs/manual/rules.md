<a name="rules-调整世界规则与人物动机参数"></a>
# 调整世界规则与人物动机参数

> 2026-10-07：按[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)同步阶段 5 的工作主题累计参数。

> 2026-10-07：按[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订「品类契约、交换规则、供给与制作边界、检查与发布品类修改、出行时长、食品来源映射说明、配置入口与生效范围、身体规则、在独立目录中调参并验证」（阶段 1）；「活动声明、单个人物的参数、人物映射表」（阶段 3）；再次修订「活动声明」（阶段 4）。

> 更新日期：2026-10-07。[工具交互规格](../dev/SPEC-life-runtime.md#tool-interaction-工具请求)中的构建价格范围、完整类别供给、
> 正式食材配方和发布身份已实现；当前场景结构 6、品类契约与规则绑定 3，目录身份以 Dataset 环境清单为准；下班与到家锚点、卡片结构 3 见[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)，画像映射表不变。
> 2026-10-06：按[同时做两件事规格](../dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)同步「活动声明」的占用名（阶段 2）；仍带旧占用名副本的外部环境在装配时被拒绝，要由 WorldMock-Dataset 按新占用名重新发布。

人物初值、节律与动机在[场景](scenarios.md#scenarios-生活场景)调整；对象和绑定在[环境目录](resources.md#resources-定义地点门店供给与世界素材)调整。
分类、身体、食品、出行与交换常数由 WorldMock 包固定，研究规则变化需修改其唯一定义并发布规则版本。
本页命令使用显式相对环境根，修改只影响新的运行输入。

```mermaid
flowchart LR
    W[WorldMock 固定类型与常数] --> R[按版本发布系统规则]
    S[场景动机、初值与人物映射] --> V[校验新环境与场景]
    R --> V
    V --> E[新的真实运行]
```

<a name="rules-共享规则与可重建环境修订"></a>
## 共享规则与可重建环境修订

按[共享规则与可重建环境规格](../dev/SPEC-shared-rules-and-rebuildable-env.md)修订；验收结果独立记录。

一般活动的显示时长与受理时长共用推导，超过一天按一天；消费 60–14400 秒且越界拒绝。MET 键从活动注册表读取，效率曲线有限非负。sleep_channel 是当前声明字段，仍不驱动身体睡眠，不能靠配置该字段改变睡眠行为。

<a name="rules-配置入口与生效范围"></a>
## 配置入口与生效范围

| 入口 | 可调整的内容 | 生效边界 |
| --- | --- | --- |
| 场景 `agents[].motivation` | 单个人物的动机参数 | 新运行加载时冻结 |
| 人物映射文件 | 显式画像编译所用映射 | 只影响该次明确传参的编译；运行规则副本仍等于包资源 |
| 场景参数 | 身体初值、节律、工作安排、菜单、初始食品持有 | 严格场景结构 7；不能改引擎闭集或常数 |

分类、身体演化、食品热量、速度、车费、配送和地图生成常数归 `worldmock.exchange`，由 `RULES_VERSION` 绑定。包内 `affordances.toml`、`motivation.toml`、`persona-mapping.toml` 是唯一通用规则来源；环境副本只用于冻结身份，字节不符即拒绝。规则维护需修改 WorldMock 的定义和测试并升 `RULES_VERSION`，不通过环境 JSON 注册公式。

<a name="rules-品类契约"></a>
## 品类契约



<a name="rules-交换规则"></a>
## 交换规则



<a name="rules-供给与制作边界"></a>
### 供给与制作边界



<a name="rules-检查与发布品类修改"></a>
### 检查与发布品类修改



<a name="rules-活动声明"></a>
## 活动声明

[引擎包的 `affordances.toml`](../../src/worldmock/data/affordances.toml) 有两层：`kinds` 声明引擎活动，`families` 将活动分组用于内部动机评分。
此处是已有能力的配置，不是插件接口。`ActivityKind`、工具协议、生成器原型、支持资源和出行方式仍受代码闭集约束。

所有消费者读取安装包内同一份不可变活动声明。环境目录中的 `affordances.toml` 是用于内容身份绑定的冻结副本，
装配时须逐字节等于包资源，不能独立修改占用、反馈、候选参数或活动闭集。
内容标题直接取场景 `title`；具体活动的娱乐、社交与健康效果取环境 `lifestyle/items.json` 并在活动开始时冻结。
来源专属标题位置、目标语料和发现映射已删除。下表说明固定声明的格式；新增能力须随引擎实现与发布。

| 常调字段 | 合法写法与影响 |
| --- | --- |
| `kinds.<活动>.met_key` | 资源目录 MET 键；出行允许尾部 `{mode}`，生活活动允许尾部 `{item_id}` |
| `sleep_channel` | 解析值为 `recovery` 或 `awake`；当前身体结算仍按实际 `sleep` 活动分支执行，不靠改该字段切换睡眠行为 |
| `resource_claims` | 占用名恰为 `wait`、`work`、`use_app`、`read_mail`、`consume`、`lifestyle`、`talk`、`travel`（[时间与能量规格](../dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动)第五章「并行准入」占用表）：每种动作占自己的名字，只有同一种动作互斥；睡眠占全部八项；驾车除自身 `travel` 外追加 `wait`、`work`、`use_app`、`read_mail`、`consume`、`lifestyle`，只允许并行交谈；同一人物进行中活动的占用相交即 `RESOURCE_CONFLICT`；环境目录副本须与包字节一致 |
| `feedback_boundary` | app 使用与其他持续活动在内容完成或定期反馈边界结算；旧游戏专用边界和内容品类条件占用已经删除 |
| `action` | 启动该活动的内部标准动作名（动作闭集之一），`none` 表示不经 `start_activity` 启动；人物工具面固定，不按活动另设工具 |
| `families.<家族>.candidate` | 已登记生成器及其允许参数；未知生成器或参数会拒绝 |
| `habit_members` | 工具和活动类型的习惯归组；归并键是内部标准动作名（如 `travel_to`、`use_app`），不是模型看到的五个动词；按名精确归并，每个动作名至多归一个家族 |

例如以下是包内 `families.work` 的声明形状，供理解固定候选生成参数：

```toml
candidate = { generator = "task_slot", max_tasks = 2 }
```

它控制内部动机评分为工作候选枚举多少任务，不改变模型提示中的候选呈现上限。
用户可见候选数量由场景 `candidate_presentation_limit` 等参数控制，见[场景配置](scenarios.md#scenarios-生活场景)。

WorldMock 发布活动声明变更时，需要检查环境资源中的 MET/娱乐缓解键、固定规则和工具菜单是否一致。
单纯给 TOML 加一个新类型，并不能实现新的动作执行器。

食品使用固定类别常数、菜单与店铺 app 绑定，没有口味缓解参数或供给约束。娱乐统一使用 `use_app`，app 内容完成形成静默结算边界；不通过环境配置另加活动或占用名。

<a name="rules-出行时长"></a>
## 出行时长



<a name="rules-动机评分"></a>
## 动机评分

当前动机评分写入观察事件的 `motivation_internal`，供评估和研究使用；候选名次、概率和分数不会直接展示给模型。
因此调这里的权重，不等于给人物下达动作指令。

<a name="rules-跨人物的通用参数"></a>
### 跨人物的通用参数

[引擎包的 `motivation.toml`](../../src/worldmock/data/motivation.toml) 固定提供以下五组运行字段：

| 分组与字段 | 约束与单位 | 作用 |
| --- | --- | --- |
| `prospect.loss_exponent` | 大于 0、小于 1 | 损失段的价值曲线形状 |
| `homeostatic.satiation_exponent` | 大于 0、小于 1 | 收益段的边际递减形状 |
| `normalization.money_fen` | 正数，分 | 金钱归一尺度 |
| `normalization.energy_kcal` | 正数，千卡 | 能量归一尺度 |
| `normalization.sleep_debt_hours` | 正数，小时 | 睡眠债归一尺度 |
| `normalization.entertainment_need`、`goal_progress` | 正数 | 娱乐需要与目标进度归一尺度 |
| `cost.time_value_fen_per_hour` | 正数，分/小时 | 时间机会成本 |
| `habit.weight` | 0–1 | 习惯项权重 |
| `habit.discount` | 大于等于 0、小于 1 | 对更早事件的折扣 |

例如包内 `[habit]` 段的字段形状如下，解释的是引擎算法参数：

```toml
[habit]
weight = 0.2
discount = 0.9
```

`weight` 决定习惯频次对内部评分的影响。`version` 和 `provenance` 是包资源的声明字段，
修改参数须随引擎发布并同步来源说明，不能把任意 provenance 文本当作已核验研究证据。
环境冻结副本缺失、结构错误或字节不同都会阻止场景装配，不能删除文件来关闭评分仪表。

<a name="rules-单个人物的参数"></a>
### 单个人物的参数

人物差异由 `background_story` 与卡片散文呈现，睡眠需要、时型、餐时与身体初值按场景冻结字段读取；
工作主题只由 `work_topic_ids` 分配，目标工时来自目录，累计工时从实际工作活动秒数派生。
工作动机只读取 `daily_work_seconds`、`total_work_seconds` 与 `goal_work_seconds` 的实际秒数口径；
不声明 `attention_prior`、画像排序参数、口味数值权重或工作效率快照。

内部动机仪表与场景人物画像分别负责研究证据和生活决策。改变散文可以改变人物理解，不能改写目录食品价格、
热量、app 绑定、工作主题目标工时或世界准入；固定世界规则随 WorldMock 包发布。

<a name="rules-人物映射表"></a>
## 人物映射表

[引擎包的 `persona-mapping.toml`](../../src/worldmock/data/persona-mapping.toml) 当前要求 `version = 2`，
有 `big_five_center`、`world_dynamics`、`defaults` 三组。画像编译中的大五刻度是 **1–7**，
与上文场景 `motivation.big_five` 的 1–10 校验范围不同。

`big_five_center.center/span` 把量表分数归一化；每个 `world_dynamics` 段使用
`intercept + slope_<特质> × 归一分数`，然后按 `min/max` 限制范围并吸附到 `step` 格点。
必须完整保留以下八个段，斜率名称只能引用已有大五特质。

| 映射结果 | 编译模型允许范围 |
| --- | --- |
| `mood_pulse_amplitude` | 0–1 |
| `mood_halflife_minutes` | 10–180 分钟 |
| `social_need_growth_per_hour` | 大于 0、小于等于 0.5，每小时 |
| `conversation_relief_per_hour` | 0–1，每小时 |
| `diversity_weight`、`invitation_acceptance`、`reply_tendency`、`present_preference` | 0–1 |

以下是 `[defaults]` 和子表的声明形状，用于故事未明确提及时的编译缺省；通用默认值随引擎发布：

```toml
[defaults]
sleep_need_hours = 7.5
chronotype_midsleep = "04:00"

[defaults.time_anchors]
wake = "07:00-07:30"
leave_home = "08:00-08:30"
lunch = "12:00-13:00"
dinner = "18:30-19:30"
bedtime = "23:00-24:00"
```

这些缺省在故事没提到时补进卡片。场景人物写出 `chronotype_midsleep`、`sleep_need_hours`、`lunch_window`、`dinner_window`、
`bedtime_window`、`leave_work_window` 或 `arrive_home_window` 时，人设层改取场景值，对应的缺省只在场景没写该字段时进入人设层；`wake` 与 `leave_home`
没有场景字段，故事不提时总取这里的缺省，见[人物画像](personas.md#personas-正文与实际世界条件要一致)。
下班与到家（卡片的 `leave_work`、`arrive_home`）没有缺省：本表 `[defaults.time_anchors]` 仍只有五个键，`version` 仍为 2，目录身份不变；故事与场景都没写固定时刻时，卡片与人设层的这两项为 null。

编译默认调用 `load_mapping_config()` 读取包资源；明确的编译实验可调用
`load_mapping_config(Path("../WorldMock-Dataset/environment/my-world/persona-mapping.toml"))`，
再将所得 `config` 传给 `compile_persona`。不通过环境变量或当前目录搜索映射文件。
实际场景装配要求 `catalog/rules/persona-mapping.toml` 与包资源逐字节一致，不能将另一份映射当成可替换的运行规则。
当前运行时主要从编译结果接入人物卡片散文，**尚未把整份 `world_dynamics` 自动应用到世界状态**。
改变映射表可以改变编译输出，不能据此声称运行中的情绪、社交和邀约参数都已相应改变。
需要明确人物睡眠与作息时，仍在场景人物字段中填写，详见[人物画像](personas.md#personas-人物画像)。

`tastes` 只映射为卡片散文，不生成候选口味权重、注意力先验或食品价格参数；覆盖率读取登记目录的真实目标名。

<a name="rules-食品来源映射说明"></a>
## 食品来源映射说明



<a name="rules-身体规则"></a>
## 身体规则

身体演化读取 `worldmock.exchange.body.BODY_CONSTANTS`，`source_kind = worldmock:<RULES_VERSION>`。能量上限 2400 千卡，基础消耗 65 千卡/小时，饥饿和紧急阈值 800/400 千卡；食品利用效率曲线仍按睡眠不足求值。场景只提供体重、初始能量、初始睡眠债与初始娱乐需要。
食品每份固定热量与完整时长：零食 200 千卡/300 秒、饮品 100 千卡/180 秒、正餐 600 千卡/1200 秒。食用实际份量与共同身体结算分别留证；初值和常数不是人物能读取的准确身体数值，人物仍只接收体感分级。

<a name="rules-在独立目录中调参并验证"></a>
## 在独立目录中调参并验证

复制完整环境到 `experiments/my-world/`，调整场景初值、人物动机或明确的画像映射参数，更新场景的环境钉。环境对象或绑定发生变化时先刷新目录身份，再执行：

```bash
worldmock validate --environment experiments/my-world
worldmock validate --scenario experiments/my-world/scenarios/single-person.json
```

环境命令校验全部场景、图字节、语料与包规则副本；项目命令保留 AS2、LabelKit、依赖和覆盖率检查。规则常数的研究必须在 WorldMock 中改唯一常数定义并升级 `RULES_VERSION`，重新发布环境，不覆盖旧运行输入。
