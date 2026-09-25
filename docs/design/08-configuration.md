# WorldMock 产品设计说明书 — 配置规格

> **状态**：DRAFT  
> **日期**：2026-09-25\
> **版本**：v1.8\
> **目标读者**：算法工程师、开发工程师、实验设计者

配置文件的编写步骤和可复制示例见[用户配置手册](../manual/README.md)。本文维护配置职责与设计边界。

## 版本历史

| 版本 | 日期 | 作者 | 说明 |
| --- | --- | --- | --- |
| v1.0 | 2026-09-20 | Claude | 初版：配置层级、场景文件、目录结构、语料配置、规则配置、凭证管理、校验机制 |
| v1.1 | 2026-09-20 | Codex | 身体初值、资源目录和规则来源对齐实际输入，明确已有实现差异 |
| v1.2 | 2026-09-20 | Codex | 资源、内容和语料设计归入资源引擎；配置仅保留场景引用与初值 |
| v1.3 | 2026-09-20 | Codex | 增加地点类与人物交互菜单声明、失败关闭校验和场景身份边界；删除会被误读为实测收据的固定校验示例 |
| v1.4 | 2026-09-21 | Codex | 接入用户配置手册，对齐模型凭据的实际检查边界，删除过时加载伪代码 |
| v1.5 | 2026-09-21 | Codex | 合并当日目标的双重估间隔及已冻结密钥池合同，统一手册与运行校验边界 |
| v1.6 | 2026-09-24 | Claude | 按时间与能量规格：交互菜单取内部标准动作，人物所选动词与方式映射后判定，途中按出发地菜单解析并由出行方式收窄；同时开始两项须两个名额空闲，按注册表占用与准入事实判定，能量不参与；初始睡眠压力按惯常起床时刻反推 |
| v1.7 | 2026-09-25 | Claude | 世界状态结构版本改为 7（时间与能量规格第九章），旧检查点以版本不符拒绝 |
| v1.8 | 2026-09-25 | Claude | 按时间与能量规格阶段 1：进行中的等待不算在手承诺；新增同刻决策上限 `max_same_moment_decisions`（缺省 3），`activity_feedback_seconds` 下限改为 60；含旧键 `max_consecutive_rejections` 的场景按未知字段拒绝；世界状态保存同刻计数与按人物暂存的呈现留痕 |

## 1. 模块概述

### 1.1 职责与边界

配置负责定义一次实验的人物、时间、地点类与人物交互菜单、初值、资源与语料版本引用，以及模型凭证。
资源、内容和语料的定义与发布统一见[资源引擎设计](03-resource-engine.md)。

**职责**：
- 定义场景参数（人物、时间窗口、地点、交互菜单、初始状态）
- 绑定资源目录版本与语料清单哈希
- 选择场景参数，并引用资源引擎和交换引擎的规则
- 提供模型凭证的安全存储与加载机制
- 校验配置完整性与一致性

**边界**：
- 不负责运行时状态维护（由 LifeEngine 承担）
- 不负责内容检索与呈现（由资源引擎承担）
- 不负责配置热更新（配置在运行前加载，运行中不可变）

### 1.2 与其他模块的关系

```mermaid
flowchart TB
    CLI[CLI 命令行] -->|加载场景| CONF[配置模块]
    CONF -->|场景参数| SEM[语义验证器]
    CONF -->|目录结构| CAT[目录准入器]
    CONF -->|语料清单| CORP[语料加载器]
    CONF -->|规则表| ENG[LifeEngine]
    CONF -->|模型凭证| AGENT[LifeAgent]
    SEM -->|校验通过| RUN[Runtime]
    CAT -->|已校验定义| CONT[资源引擎：检索与内容提供]
    CORP -->|内容| CONT
```

### 1.3 核心设计原则

| 原则 | 含义 | 体现 |
| --- | --- | --- |
| **显式版本** | 配置文件携带版本号 | 场景使用整数 `schema_version`，具体约束由场景模型定义 |
| **Fail-Closed** | 未识别字段拒绝启动 | 拼写错误不会被静默忽略 |
| **溯源分类** | 参数注明来源 | 分类与字段见[资源引擎的来源分类](03-resource-engine.md#来源分类) |
| **确定性加载** | 相同配置产生相同行为 | 禁止运行时随机化 |
| **凭证隔离** | 敏感信息不进版本控制 | 凭证位于 `~/.config/worldmock/` |

## 2. 配置层级与加载顺序

### 2.1 配置层级

WorldMock 采用四层配置结构：

```mermaid
flowchart TB
    A[场景文件<br/>scenarios/*.json] -->|引用| B[目录配置<br/>catalog/]
    A -->|引用| C[语料配置<br/>corpora/]
    B -->|规则表| D[规则文件<br/>catalog/rules/*.toml]
    E[模型凭证<br/>~/.config/worldmock/] -.->|不进版本控制| F[运行时]
    A -->|validate| F
    B -->|catalog admission| F
    C -->|corpus manifest| F
    D -->|规则加载| F
    E -->|凭证解析| F
```

### 2.2 加载与校验

场景先通过自身模型与交互菜单闭集校验，再解析固定目录和语料引用；在启动前完成引用、文件身份和品类准入。
场景的 `catalog_version` 选择当前原生目录版本；`recipe_output_bindings` 明确将库存配方的输出品类
绑定到输出对象，没有配方时为空对象。全部商品与供给关联商品、初始库存、未来供给发布、任务、出行方式和配方输出闭包
共同决定需要通过的配对，不由调用方提供缩小后的触达清单。
品类配置及三态的精确定义见[接口合同](../CONTRACTS.md#品类契约与目录字段)与
[品类契约规格](../dev/SPEC-category-contract.md)。
实际入口见[场景加载](../../src/worldmock/life/scenario.py)与
[项目校验](../../src/worldmock/validation.py)。目录和语料的加载细节统一见
[资源引擎的准入边界](03-resource-engine.md#准入边界)，本节不另设通用配置装配类。

### 2.3 参数归属

身体资源的配置分为场景实例初值、人物档案常量和目录规则；各自职责见
[场景与身体规则](../dev/SPEC-life-runtime.md#场景与身体规则)。场景不通过一个通用
`constants` 对象覆盖所有身体算法。

```mermaid
flowchart LR
    SCENE[场景 body：体重与资源初值] --> ASSEMBLE[组装身体运行参数]
    CATALOG[目录：单位、边界、代谢与活动规则] --> ASSEMBLE
    PERSON[人物档案：时型、睡眠需要、餐时窗口] --> BODY[模拟引擎计算身体变化与体感]
    ASSEMBLE --> BODY
    SPEC[冻结规格：睡眠与体感算法] --> BODY
```

## 3. 场景配置文件

### 3.1 文件位置与合同

场景位于 `scenarios/`，完整示例见
[白领场景](../../scenarios/white-collar-12-weeks.json)。当前输入结构由
[LifeScenario、BodyInstanceConfig 与 AgentConfig](../../src/worldmock/life/scenario.py)
校验；资源语义引用 [资源与需求字段](../CONTRACTS.md#资源与需求字段)。

当日目标重估沿用生活场景配置：`reconsideration_interval_seconds` 默认 5400，
`idle_reconsideration_interval_seconds` 默认 10800，均为正整数且不超过 86400，空闲间隔不能短于常规间隔。
进行中的等待不算在手承诺，人物只在等待且没有其他在手承诺时用空闲间隔。
其选择与环境威胁边界见 [当日目标规格](../dev/SPEC-day-plan-goals.md#事件驱动的决策节律)。
同刻决策上限 `max_same_moment_decisions` 为正整数，缺省 3；`activity_feedback_seconds` 取 60–86400 秒，下限 60
保证等待（计划时长取它）能被受理。含旧键 `max_consecutive_rejections` 的场景按未知字段拒绝。
人物目标条目不接收时间或调度参数。

场景使用整数 `schema_version`，人物清单为 `agents`，时间字段为 `start_at`、`end_at`；
身体资源初值放在顶层 `body`，人物档案常量放在各自的 `agents` 条目。
本文不重新定义完整场景 Schema。

| 格式 | 当前版本 | 拒绝边界 |
| --- | --- | --- |
| 地图文件 `schema_version` | 1 | 声明与文件身份、完整路线关系共同校验 |
| 商品目录 `version_major` | 4 | 原生品类对象与独立供给共同发布；两条分支旧目录 3.0 均不作为新身份 |
| 品类与交换规则 `contract_version` | 2 | 购买规则删除商品价格槽，保留动态供给报价的闭集求值 |
| 生活场景 `schema_version` | 3 | 地点、商品与供给按身份引用，旧内嵌地点和路线拒绝；含旧键 `max_consecutive_rejections` 拒绝 |
| 世界状态 `schema_version` | 7 | 旧检查点（含版本 6）以版本不符拒绝；活动集合、共同身体时刻、威胁交付签名、同刻计数、按人物暂存的呈现留痕与供给事实完整保存；活动快照地点为开始地点 |

目录通过 `scripts/build_catalog.py` 生成，地图通过 `scripts/prepare_map.py` 从显式输入准备与发布。
地图准备的发布流程见[资源引擎](03-resource-engine.md#冻结地图的准备与发布)。不保留旧场景迁移脚本或兼容输入层。

### 3.2 交互菜单声明

场景根对象必须具有 `interaction_menus_by_location_kind`，键集合恰好等于所选地点经地图解析后的 `kind` 不同值
集合。每个地点类都有一个非空菜单，不接受 `null`、通配键、缺项或额外键。每个 `agents[]` 条目必须
显式具有 `interaction_menu`，取同形菜单或 `null`；`null` 表示人物不在地点菜单之外追加限制，字段缺失
仍是配置错误。

菜单只有 `allowed_actions` 一个必填数组。数组非空、元素无重复且为非空白字符串，
不接受其他字段；动作取内部标准动作 `START_ACTIVITY_ACTIONS` 闭集，不是人物动词。
人物所选的（对象，动词，方式）经私有绑定映射为内部动作后按菜单判定，场景文件不改。
地点菜单只剪裁对象动作，不剪裁模型工具；`wait`、`sleep` 是必须保留的自身动作。完整闭集、
保护集合和 `null` 语义见[交互菜单合同](../CONTRACTS.md#交互菜单声明与有效菜单)。

运行时按人物权威 `location_id` 找到地点的单值 `kind`，再将该地点菜单与人物菜单逐字段求交集。
途中按出发地菜单解析，再由出行方式的准入收窄。
解析后地点的 `kind` 只用于选择交互菜单，和候选资格的多值 `location_kinds` 不是同一字段；不能以其中一个
代替另一个。菜单是显式准入声明，不从人物画像、偏好、动机或目录 `surface` 推导。

所有版本控制内的正式场景和相应测试输入都使用当前字段。旧文件缺字段时拒绝，不生成缺省全量菜单，
不添加迁移或兼容读取。实现与验收以[冻结特性规格](../dev/SPEC-interaction-menu.md)为准。

### 地图引用与门店供给初值

场景 `map_dataset` 使用 `dataset_id/version/path/sha256` 绑定唯一冻结地图。
`locations` 只写 `location_id`，`products` 只写 `product_id`，`supplies` 写 `supply_id` 及可选 `initial_stock`。
地点名称、坐标、营业、路线和供给定义从固定资料装配，不在场景手抄副本，也不添加额外配置层。
人物 `qualifications` 显式声明；访问与供给资格不能从人物画像推断。

完整运行时场景包含解析后的地图、地点、路线、商品和供给。地图和场景选区共同校验账户、产品、
供给组合和有向路线覆盖。每个端点/方式组合必须有可达、不可达或带来源的未知记录；漏项拒绝。
营业与报价的有效期、核对日期、可信截止日及来源在发布前验证。旧整点营业和旧商品单店字段直接拒绝。
地点发现由 `start_activity` 的可读对象与动作表达；允许发现的正式地点菜单必须显式纳入
`browse` 标准动作，人物菜单仍可进一步限制。
三个正式场景选中的三家餐馆各自选中明确关联的 `prepared-meal` 供给和商品，
供给初值为正、支持本店自提，且地点与人物的实际菜单允许购买和消费。
此覆盖属于正式输入验收，不增加通用场景的餐馆必有餐食约束；空菜单依然合法。
人物如需同时启动两个持续活动，两个名额都须空闲，名额不足报 `SESSION_LIMIT_EXCEEDED`；
正式地点和人物菜单分别允许这两项映射后的标准动作，本轮等长的请求数组逐项通过冻结菜单与可见性校验，
并按注册表占用与准入事实判定，能量不参与，整组原子受理。


### 3.3 身体资源初值

以下是当前白领场景的 `body` 字段摘录，仅展示这一字段，不是可独立运行的完整场景：

```json
{
  "body": {
    "mass_kg": 65,
    "initial_energy_kcal": 1600,
    "initial_sleep_debt_hours": 0,
    "initial_entertainment_need": 0
  }
}
```

| 配置内容 | 含义与运行时去向 |
| --- | --- |
| `mass_kg` | 体重，单位 kg；用于活动额外能量消耗 |
| `initial_energy_kcal` | 可用能量初值，单位 kcal；成为 `BodyState.energy_kcal`，受目录能量上界约束 |
| `initial_sleep_debt_hours` | 当前场景输入使用的键名；其值初始化唯一的睡眠不足账 `BodyState.sleep_deficit_hours`，不新增另一份睡眠状态 |
| `initial_entertainment_need` | 当前场景声明的娱乐需要初值，范围为零到一；见下文实现差异 |

**当前实现差异**：场景准入读取 `initial_entertainment_need`，但运行时初始化仍把娱乐需要设为零，
非零声明尚未接通。上面的摘录使用零值，不将非零初值描述为已生效能力。
依据为 [场景语义组装](../../src/worldmock/life/semantics.py) 与
[身体初始化](../../src/worldmock/life/body.py)。

### 3.4 人物差异与衍生体感

人物的 `chronotype_midsleep`、`sleep_need_hours`、`lunch_window`、`dinner_window`
分别驱动睡眠节律、睡眠不足累积和饥饿体感的餐时项；完整约束引用
[人物档案合同](../CONTRACTS.md#人格与卡片字段) 与 `AgentConfig`。

初始睡眠压力按惯常起床时刻反推。饥饿与困意由资源、时刻和人物参数派生，
场景不另存可自由改写的饥饿条或疲劳条；身体变化与体感的执行边界见
[模拟引擎](04-simulation-engine.md#实际区间身体变化与效率)。人物余额使用 `initial_balance_fen`，单位为整数分。

### 3.5 规则来源与配置边界

能量上界、基础代谢、活动 MET、娱乐需要变化规则从冻结目录装配到 `BodyConfig`。
睡眠压力、睡眠不足累积与恢复及食品利用效率由
[睡眠模型实现](../../src/worldmock/life/sleep.py)按冻结规格计算。
工作效率因子从合同对象目录装配到 `work_efficiency_factors`，由身体结算按输入资源曲线计算区间平均；
正式场景的睡眠因子读取 `sleep_deficit_hours`。装配与计算见
[场景语义](../../src/worldmock/life/semantics.py)和[身体结算](../../src/worldmock/life/body.py)。
目录中仍有 `sleep_debt_hours` 标识及旧变化率，不能据此宣称它们能覆盖现行睡眠算法；
目录中的资源标识与属性见[资源引擎的可计量资源定义](03-resource-engine.md#可计量资源定义)。

## 4. 资源目录引用

场景以 `catalog_version.major/minor` 绑定目录版本；场景保存账户、对象、地点等实例与引用，
资源定义、单位、边界、目录结构和发布规则统一见
[资源引擎设计](03-resource-engine.md#4-组织与发布目录)。
交换条件、投入产出与资源约束统一见[交换引擎设计](02-exchange-engine.md)。

## 5. 语料引用

场景通过 `corpus_root` 指定本地语料根目录，通过 `corpus_manifest_sha256` 绑定清单。
清单结构、来源、文件身份、内容品类和准入规则统一见
[资源引擎设计](03-resource-engine.md#2-整理与固化语料)，此处不维护第二份语料 Schema 或示例。
正式场景选中 NASA `podcast_episode` 选段，并绑定原始转写页、官方音频身份、
从官方锚点派生的时间轴及其哈希。播客使用现有播放活动，不配置独立播放器。

## 6. 规则配置入口

资源目录文件与来源映射见[资源引擎](03-resource-engine.md#文件职责)，
交换条件与投入产出规则见[交换引擎](02-exchange-engine.md)，
活动与身体过程见[模拟引擎](04-simulation-engine.md)。
人物差异的提取与参数编译见[人物画像规格](../dev/SPEC-life-realism.md#画像编译)；
配置加载不另定义一套对象效果、动机规则或画像映射格式。

## 7. 模型凭证

### 7.1 凭证文件位置

模型 API 凭证存储在用户主目录，**禁止进入版本控制**：

```
~/.config/worldmock/
└── deepseek.env
```

### 7.2 格式与加载边界

当前运行凭据仅允许 `DEEPSEEK_NODE_KEYS`，填写至少一把非空密钥，多把用逗号分隔；
正式接口见[模型凭据合同](../CONTRACTS.md#人物决策提示与生成参数)，
填写示例和权限设置见[运行配置手册](../manual/runtime.md#填写模型凭据)。

[运行启动器](../../src/worldmock/runtime.py)拒绝缺失文件、符号链接、未知键、空值和空密钥段，
逐段去除首尾空白并按出现顺序去重后，把密钥池交给 LiteLLM Router 分摊请求。
`0600` 是推荐权限，当前加载器没有权限位强制检查，也没有密钥长度大于 20 的检查。
画像抽取与模拟启动的凭据读取顺序不同，详见手册；不在此处保留与实际实现不同的加载伪代码。

## 8. 配置验证

### 8.1 validate 命令

`worldmock validate` 在运行前校验配置完整性：

```bash
uv run worldmock validate --scenario scenarios/white-collar-12-weeks.json
```

### 8.2 校验清单

运行凭据项在模拟启动器中检查，不由 `worldmock validate` 执行。
画像抽取和 LabelKit 的凭据读取边界见[运行配置手册](../manual/runtime.md)，不能视为执行同一套文件校验。

| 校验项 | 检查内容 | 失败行为 |
| --- | --- | --- |
| Schema 版本 | 满足当前场景模型的整数版本约束 | 拒绝启动，说明不受支持的版本 |
| 必填字段 | 所有必填字段存在且类型正确 | 拒绝启动，列出缺失字段 |
| 时间窗口 | `end_at > start_at` | 拒绝启动 |
| 初始状态 | 按资源单位与目录边界校验能量、睡眠不足、娱乐需要；运行时继续满足各状态约束 | 拒绝启动 |
| 地点引用 | `agents[].home_location_id` 指向已声明地点 | 拒绝启动 |
| 交互菜单 | 地点类映射完整且无额外键；人物字段显式存在；数组非空无重复；名称来自权威闭集并包含保护项 | 以 `IntegrityError` 拒绝启动，保留英文校验原因 |
| 目录准入 | 满足 `category_requirements`，内容摘要一致，全部可能触达配对已定义或已审核不作用 | 未定义、歧义或文件篡改拒绝启动，给出具体来源 |
| 语料哈希 | 文件 SHA-256 与清单一致 | 拒绝启动，列出不匹配文件 |
| 规则完整性 | 所有规则文件存在且可解析 | 拒绝启动 |
| 运行凭据 | 固定文件存在且非符号链接；仅含 `DEEPSEEK_NODE_KEYS`，值及密钥段非空 | 文件缺失或不安全时报 `FileNotFoundError`，内容无效时报 `IntegrityError`；`0600` 仅为推荐权限 |

### 8.3 校验报告

交互菜单准入由场景加载完成：`load_scenario` 成功即已通过菜单字段与覆盖检查，失败由
`IntegrityError` 和英文日志给出原因；现有报告不另加独立菜单结果字段。
报告只记录实际结果，不能用固定数量或示例 `passed` 值冒充一次真实校验。可复现的命令、源码身份和精确输出进入
[交互菜单验收记录](../dev/research/interaction-menu-verification.md)。

### 8.4 错误分类与提示

| 错误类型 | 示例 | 用户提示 |
| --- | --- | --- |
| Schema 版本错误 | 不受支持的 `schema_version` | "Unsupported scenario schema version" |
| 字段缺失 | 缺少 `agents` | "Required field 'agents' is missing" |
| 类型错误 | `initial_balance_fen: "invalid"` | "Field 'initial_balance_fen' must be an integer" |
| 引用错误 | `home_location_id: "unknown"` | "Location 'unknown' is not declared" |
| 菜单声明错误 | 地点类缺项、人物字段缺失、未知或重复值、空数组、缺保护项 | `IntegrityError` 与具体英文 Pydantic 原因；不宣称存在统一注册表错误码 |
| 哈希不匹配 | 语料文件被修改 | "Corpus entry identity mismatch"；核对已绑定版本与来源文件后重新发布 |
| 凭据内容错误 | 未知键、空值或空密钥段 | "DeepSeek credential file has an invalid schema" |

## 9. 配置版本与运行绑定

场景使用当前定义并在运行前校验，资源与可供性的发布边界见
[资源引擎设计](03-resource-engine.md#构建与版本)。
旧字段和失效配置直接删除，不提供格式迁移或兼容层；实验身份与历史证据的保存遵循
[实验定义](../EXPERIMENT.md)。
菜单声明属于场景原始 JSON 字节并进入 `scenario_sha256`。修改地点或人物菜单会产生新的实验身份；
打开已有运行、恢复与反事实分支不得接受不同场景身份，也不新增与场景哈希重复的菜单版本字段。
地图集合身份、版本与文件摘要同时进入运行绑定；变更供给、路线或营业资料须得到新的身份，不能重用旧运行。

## 10. 配置最佳实践

### 10.1 场景设计

| 原则 | 说明 |
| --- | --- |
| **明确实验目标** | `experiment_id` 描述清晰 |
| **固定随机种子** | 保证实验可复现 |
| **合理时间窗口** | 12 周覆盖完整行为模式 |
| **真实初始状态** | 按能量、睡眠不足与需要的实际单位设置初值，满足各自边界并保留实验假设来源 |
| **显式交互菜单** | 每个地点类与人物都按当前合同声明；人物无限制使用显式 `null`，不省略字段 |

### 10.2 语料管理

| 原则 | 说明 |
| --- | --- |
| **版本固化** | 语料更新后重新生成 `manifest.json` |
| **哈希校验** | 每次运行前校验语料完整性 |
| **来源追溯** | 记录数据集名称、版本、许可 |
| **禁止运行时修改** | 语料在 `prepare-corpora` 后只读 |

### 10.3 规则维护

| 原则 | 说明 |
| --- | --- |
| **溯源分类** | 参数注明 fact/assumption/scenario |
| **版本号管理** | 规则文件携带版本号 |
| **代码引用** | 规则文件在代码中显式引用，避免隐式依赖 |
| **单元测试** | 规则变更后运行 `pytest tests/catalog/` |

## 11. 参考文献

1. 语义化版本号：https://semver.org/
2. TOML v1.0.0：https://toml.io/
3. JSON Schema：https://json-schema.org/
4. ISO 8601 时间格式：https://www.iso.org/iso-8601-date-and-time-format.html
5. IANA 时区数据库：https://www.iana.org/time-zones
6. SHA-256 哈希算法：FIPS 180-4
