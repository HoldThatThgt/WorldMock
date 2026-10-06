<a name="resource-engine-worldmock-产品设计说明书-资源引擎"></a>
# WorldMock 产品设计说明书 — 资源引擎

> **状态**：DRAFT，整合设计；接口与行为仍以冻结合同和规格为准；按人物 Prompt 规格修订，修订部分已实现（证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md)）；按一日运行保真规格同步（一日运行保真 RS1），已实现\
> **日期**：2026-10-06
> **版本**：v1.15\
> **工具交互修订**：类别完整供给、冻结价格范围及真实食材配方设计已冻结，已实现；本次生产验收单独记录；以[工具交互规格](../dev/SPEC-life-runtime.md#tool-interaction-工具请求)为本次合同。
> **个人事务与工作安排修订**：按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)分阶段同步（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md) D3 作业），已实现（离线验收，证据见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)）；该规格阶段 1 不改本文。
> **同时做两件事修订**：按[同时做两件事规格](../dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订「构建与版本」的可供性占用声明（阶段 2）。
> **范围**：资源定义、内容与语料、目录发布、来源校验、检索和提供

资源引擎管理资源定义、对象属性、内容与固化语料，回答“有哪些资源、来自哪里、如何查询和读取”。
它接收明确的版本、查询条件和内容范围，返回定义、条目、内容片段及来源。

接口字段引用[接口合同](../CONTRACTS.md#contracts-worldmock-接口合同)，行为引用
[运行时规格](../dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则)、[生活真实性规格](../dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像)与
[品类契约规格](../dev/SPEC-category-contract.md#spec-category-contract-品类契约与交换规则开发规格)。餐馆与音频的本次行为以
[一日行为规格](../dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-一日行为可信性特性开发规格)为准。
本文只定义资源数据的准备、发布、校验、查询和读取职责。

具体环境及制作工具按[环境数据归属规格](../dev/SPEC-environment-data-ownership.md)归 WorldMock-Dataset，
正式环境为 `environment/zhangjiang-baseline/`；WorldMock 提供引擎及包内固定通用规则。
本章的 `catalog/`、`corpora/`、`scenarios/` 指外部环境目录，命令以 WorldMock 为当前工作目录并显式传路径。
当前场景结构 6，内容标题必填，活动效果随环境加载并在启动时冻结；新组合放行另核对实际收据。

<a name="design-03-resource-engine-共享规则与可重建环境修订"></a>
## 共享规则与可重建环境修订

按[共享规则与可重建环境规格](../dev/SPEC-shared-rules-and-rebuildable-env.md)修订；验收结果独立记录。

语料文件只接受根内安全相对路径：拒绝绝对路径、任何 .. 组件、符号链接与非普通文件。曲线输入和输出必须是有限非负数且拒绝布尔值，效率资源名必须含非空白字符；生活与目录读取同一模型。目录发布与品类身份统一以字节原子写入。

<a name="resource-engine-职责与边界"></a>
## 1. 职责与边界

<a name="resource-engine-项目框架映射"></a>
### 项目框架映射

承接 [overview 的解决方案](00-overview.md#overview-解决方案)，只展开资源引擎内部功能，其他引擎作为协作节点：

```mermaid
flowchart TB
    SOURCES["数据集与素材来源"] --> PREPARE

    subgraph RESOURCE["资源引擎：世界里有什么，如何找到与接触"]
        PREPARE["整理与固化语料<br/>记录来源、许可、文件身份与版本"]
        DEFINE["定义资源与对象<br/>金钱、时间、身体资源、商品与内容<br/>声明身份、单位、边界与属性"]
        CATALOG["组织与发布目录<br/>关联资源、对象、内容与来源语料"]
        CHECK["校验资源可用性<br/>核验版本、文件完整性、引用与检索可达性"]
        QUERY["检索与呈现<br/>按属性查询、筛选、排序、轮转与分页"]
        PROVIDE["提供实际接触的内容<br/>按调用方给定的可见范围与接触区间<br/>读取正文、片段及来源"]

        PREPARE --> CATALOG
        DEFINE --> CATALOG
        CATALOG --> CHECK
        CHECK --> QUERY
        QUERY --> PROVIDE
    end

    EXCHANGE["交换引擎"]
    SIMULATION["模拟引擎"]

    CHECK -->|资源与对象定义| EXCHANGE
    SIMULATION -->|查询条件、候选范围与呈现历史| QUERY
    QUERY -->|可见资源与候选| SIMULATION
    SIMULATION -->|内容身份、允许范围与实际接触区间| PROVIDE
    PROVIDE -->|内容片段、素材引用与来源| SIMULATION
```

<a name="resource-engine-职责约定"></a>
### 职责约定

资源引擎维护的是固定版本的资源定义、目录与来源语料。查询和读取使用调用方提供的只读输入，
返回结果及来源，不拥有运行中的余额、库存、身体状态、活动进度或人物记忆。
后续正文按图中的六项功能展开，实现差距与验收要求只覆盖这六项功能。

| 协作方 | 资源引擎接收什么 | 资源引擎交付什么 | 对方负责的边界 |
| --- | --- | --- | --- |
| [模拟引擎](04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎) | 指定版本、查询条件、允许的候选或内容范围、排序输入、呈现历史和实际接触区间 | 匹配条目、呈现窗口、内容片段、来源及素材引用 | 决策编排、活动与身体过程、世界状态更新，以及将实际结果提交给记录引擎 |
| [交换引擎](02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎) | 资源或对象标识、指定版本与属性查询 | 资源类型、单位、边界、对象属性与关联定义 | 活动条件、投入产出、资源变化和分录规则的判断与计算 |
| [场景配置](08-configuration.md#configuration-worldmock-产品设计说明书-配置规格) | 资源和语料版本要求、所需内容品类及引用 | 可加载的资源数据与静态校验结果 | 实例初值、运行参数、资源版本选择与世界初始化 |

资源引擎返回的素材引用供调用方使用；证据落盘、事务、检查点与导出由
[记录引擎](05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎)承载，本文不展开其实现。

职责按处理内容划分，不按代码文件或目录划分。现有 `catalog` 与 `life` 模块包含多个引擎的处理逻辑，
本文只引用其中的资源部分；尚未分开的实现会在对应功能下说明。

<a name="resource-engine-整理与固化语料"></a>
## 2. 整理与固化语料

<a name="resource-engine-整理与固化流程"></a>
### 整理与固化流程

```mermaid
flowchart LR
    INPUT[原始数据与许可] --> NORMALIZE[字段映射、单位换算、身份关联]
    NORMALIZE --> TRACE[记录字段来源与变换依据]
    TRACE --> FILES[固定内容与元数据文件]
    FILES --> MANIFEST[生成语料清单与哈希]
    MANIFEST --> VERIFY[校验准备好的语料]
    VERIFY --> PUBLISH[发布固定语料文件]
```

运行消费已固化的本地语料。外部抓取、清洗和转换在准备阶段完成；
运行中不在线补齐缺失素材，也不让模型编造来源事实。
采集、清洗、转换和正式语料发布由 Dataset 制作工具负责；WorldMock 不提供来源准备器。
引擎用实际清单核验文件和引用，不能用摘要正确代替来源与重建验收。
不同数据集提供不同字段时，先用稳定身份建立关联，再按明确映射组合。
缺少关联依据不能靠名称相似度推断为同一物品；缺失必需字段应在准入阶段暴露。

<a name="resource-engine-来源分类"></a>
### 来源分类

目录的来源分类与语料的文件来源是两种记录，分别回答“数值怎么来”和“文件从哪里来”。

| 目录 `provenance.type` | 含义 | 应留下的依据 |
| --- | --- | --- |
| `fact` | 可追溯的来源事实 | 来源文件、字段或公开资料定位 |
| `anchored` | 在来源事实基础上换算或锚定 | 原始锚点、单位换算或变换方法 |
| `assumption` | 仿真使用的假设 | 假设理由与适用边界，不包装成实测事实 |
| `scenario` | 场景明确给定的设定 | 场景来源与相应字段 |

来源记录用 `part` 区分组成部分，并可用 `paths` 定位字段、`artifact` 定位材料、
`method` 记录变换。类型与必填项见[目录模型](../../src/worldmock/catalog/models.py)。
条目属性可以来自不同依据，必须按字段说明；有引用字符串不等于来源文件已归档。

语料的 `source.kind` 是来源种类说明，当前模型不把它限制为上述四种目录分类。
文件来源还包含 `license`、`selector` 及按来源适用的 `url`、`retrieved_at`、`fact_cutoff`。
准入政策要求明确许可和来源，不能把“网上可见”当作许可依据。

<a name="resource-engine-语料清单与文件身份"></a>
### 语料清单与文件身份

corpora/manifest.json（`../WorldMock-Dataset/environment/zhangjiang-baseline/corpora/manifest.json`） 的实际结构由
[CorpusManifest](../../src/worldmock/corpora/manifest.py) 定义：

| 层级 | 字段 | 含义 |
| --- | --- | --- |
| 清单 | `schema_version`、`corpus_id`、`created_at`、`transformer_version` | 清单结构、语料身份、带时区的创建时刻、转换版本 |
| 文件集合 | `entries` | 已固化文件的清单 |
| 单个文件 | `entry_id`、`path`、`sha256`、`size_bytes` | 唯一身份、相对路径、完整内容摘要和字节数 |
| 单个文件 | `media_type`、`purpose`、`source` | 文件类型、用途和来源 |

场景通过 `corpus_manifest_sha256` 绑定整份清单，清单再绑定各文件。
加载时的完整性检查见[文件身份校验](03-resource-engine.md#resource-engine-文件身份校验)。

<a name="resource-engine-语料整理的实现与验收"></a>
### 语料整理的实现与验收

| 事项 | 当前承载与要求 |
| --- | --- |
| 准备与发布 | Dataset 制作工具负责来源整理、构建和发布；正式来源、数量与可复现重建在数据仓验收 |
| 清单与来源 | [corpora/manifest.py](../../src/worldmock/corpora/manifest.py)承载文件身份与来源模型 |
| 来源归档差距 | 部分目录来源记录仍标有 `evidence snapshot pending`，不能把来源标签当作证据文件已经归档 |
| 验收 | 核对固定文件、来源、许可与转换依据；用重复身份、路径越界、大小和哈希漂移反例验证清单校验。相关用例见 [tests/corpora](../../tests/corpora) |

清单组织借鉴 [Frictionless Data Package](https://specs.frictionlessdata.io/guides/data-package/)
集中描述文件、来源、许可和结构的方式，继续使用现有模型与清单。

<a name="resource-engine-地图资料的冻结与许可"></a>
### 地图资料的冻结与许可

地图数据集合固定区域、WGS84、时区、稳定身份、版本、文件哈希以及逐来源时间、许可和通知。
真实地点坐标、路线响应与场景营业/供给假设分别保留来源，不能用一个文件级假设标签覆盖外部事实来源。
运行只读固定资料，不访问在线地点、库存、报价或路由接口。

已核查基线有二百二十份按方式命名的路线响应，其中一百零四份步行响应复用了同端点驾车响应。
文件数量只证明材料存在，不能证明方式正确；发布前须修复或显式标记未知，并核验方式来源。
冻结结果可被重复消费，不意味着公共路由服务日后能重建同一历史路网结果。

许可按具体来源、数据库组合及公开使用方式判定；保留来源与通知是本项目的追溯要求。
产出作品署名不能替代其派生数据库可能承担的义务，也不能用共享条款布尔值判断许可兼容。
准确行业依据见 [可行性核验](../dev/research/map-and-storefront-feasibility.md)。

<a name="resource-engine-定义资源与对象"></a>
## 3. 定义资源与对象

<a name="resource-engine-资源内容和语料的关系"></a>
### 资源、内容和语料的关系

三者属于同一设计范围，但保存的信息不同。可计量资源保存单位和变化约束；
内容是数字人能够接触的信息；语料是经过整理并固定版本的来源文件集合。
同一个商品可以同时引用资源规则、内容描述与来源语料，不需要复制成三份对象。

```mermaid
flowchart LR
    RAW[食品来源语料] --> OBJECT[食品条目：身份、份量、热量、来源]
    MONEY[金钱定义：整数分] --> OBJECT
    ENERGY[能量定义：千卡] --> OBJECT
    OBJECT --> DISPLAY[浏览时看到商品信息]
    MEDIA[影片语料与时间轴] --> CONTENT[内容条目：身份、时长、题材、素材引用]
    CONTENT --> FIND[检索时看到元数据]
    CONTENT --> WATCH[观看时提供实际接触的片段]
```

<a name="resource-engine-可计量资源定义"></a>
### 可计量资源定义

[资源定义模型](../../src/worldmock/catalog/models.py)与
资源注册表（`../WorldMock-Dataset/environment/zhangjiang-baseline/catalog/resources.jsonl`）是字段来源。

| 字段 | 含义 |
| --- | --- |
| `resource_id` | 稳定的资源类型标识，区别于某个数字人的状态或账户实例 |
| `kind` | `account` 为账户型，`level` 为存量型 |
| `unit`、`owner` | 计量单位与资源归属声明 |
| `bounds` | 存量型必须声明的取值边界；无上界可显式使用空值 |
| `conservation` | `closed` 要求域内守恒；`open` 允许产生和消耗 |
| `provenance` | 定义与数值字段的来源记录 |
| `version` | 同一资源标识下的条目版本 |

| 资源 | 目录中的定义与属性 |
| --- | --- |
| 金钱 | `money_fen`，账户型，单位为分，封闭域 |
| 可用能量 | `energy_kcal`，存量型，单位为 kcal，声明有效取值范围 |
| 娱乐需要 | `entertainment_need`，存量型，范围为零到一，表示需要程度 |
| 睡眠不足 | 当前目录标识为 `sleep_debt_hours`，单位为小时 |
| 时间 | 当前没有独立资源注册条目，相关对象以明确单位声明时长 |

上述内容定义资源身份与属性。实际资源值由调用方维护。
现有资源条目还携带 `alert_rules`、`drain_rules` 等规则字段，资源引擎负责其结构加载、版本和来源关联；
规则的含义与执行由规则使用方负责，本文不展开阈值触发、身体演化或结算算法。

<a name="resource-engine-交换对象与内容条目"></a>
### 交换对象与内容条目

商品、服务和合同由 [ObjectDeclaration](../../src/worldmock/catalog/models.py) 声明身份、品类、
属性、版本和来源；`ExchangeObject` 是合并品类属性与交换规则后的编译运行视图。
交换条件与投入产出见[交换引擎](02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎)。
资源引擎负责这些条目的加载、属性查询与来源追踪。交换条件和资源变化声明作为关联规则提供，
其计算语义由交换引擎维护。

内容保留各自需要的结构，不强制所有 `items.json` 使用同一个 Schema：

| 内容 | 资源引擎维护与提供的材料 |
| --- | --- |
| 影片、剧集、网文、新闻与字幕体验 | 条目标识、类别、标题、时长、时间轴与文件身份，以及指定范围内的片段 |
| 游戏 | 固定游戏文件、种子、文件哈希与展示元信息；运行中产生的游戏反馈由模拟侧提供 |
| 工作文档与邮件 | 正文、附件、来源和文件身份，以及调用方允许读取的内容 |
| 食品与其他商品 | 对象标识、规格、份量、热量依据及目录属性；当前报价和持有数量由调用方提供 |
| 生活活动 | 活动素材的标识、名称、品类、典型时长、地点类声明及来源 |
| 地点与路线 | 地点标识、地点类、路线与营业时间声明等素材事实 |
| 官方播客选段 | 节目与音频身份、原始转写锚点、派生段落时间轴、时长和使用条件；播放状态由模拟引擎持有 |

纯商品的运行视图与独立库存物共用 `InventoryItemConfig` 的品类、库存单位和使用语义；门店供给额外提供卖方、报价与可购买性，不能反向修改纯商品。

当前内容引用由 [ContentItemConfig、GameConfig 等场景模型](../../src/worldmock/life/scenario.py)承载，
展示元数据由语料提供。资源引擎检查引用能否解析，不因条目存在而判定活动可以执行。

<a name="resource-engine-地点商品与门店供给"></a>
### 地点、商品与门店供给

```mermaid
flowchart LR
    DATA[地图数据集合] --> PLACE[地点身份坐标与营业声明]
    PRODUCT[商品规格用途与消费效果] --> SUPPLY[供给身份地点商品卖方]
    PLACE --> SUPPLY
    SUPPLY --> DECL[报价资格履约与来源声明]
    SELECT[场景选择身份与初值] --> RUNTIME[组装只读运行定义]
    DATA & PRODUCT & DECL --> RUNTIME
```

地点单值 `kind` 用于交互菜单，`business_kind` 用于经营业态，非空 `location_kinds` 用于活动环境。
品牌是可选属性，缺品牌不影响地点或供给有效。活动环境必须显式声明，不能从名称或业态自动推断。
商品不包含门店、卖方、库存、购买报价或配送处理时长；供给使用独立身份及唯一地点/商品/卖方组合。
商品有效属性可以包含有来源的 `purchase_price_fen_min/purchase_price_fen_max` 构建边界，
同币种正整数分、最小值不大于最大值；相等合法，范围不参与运行交易报价求值。
供给声明提供初值和有效期，运行库存、当前报价和营业例外仍由模拟引擎持有，资源查询不改状态。
地点类别沿用 `LocationConfig.kind`，对象类别沿用 `category_id` 和品类树；在冻结 `category-assignment.json` 中声明映射，
文件恰含必填 `kinds/locations/source`，`kinds` 为现有地点 kind 到品类数组的准入表，全部地图 kind 包括空集合均显式声明。
地点类别只形成完整准入池，每店在同一文件恰含必填 `location_id/category_ids/object_ids/price_position_percent/source`，
顶层与逐店来源沿用完整 `DataSource`。
实际供给为逐店类别及全部后代对象与显式对象的并集，仍须全部属于准入池，不随机抽几件、不把准入池全量复制给每家店。
餐馆准入正餐，便利店准入食品，超市与商户准入食品、日用品和原料，菜市场准入原料；
住宅、办公室、健身场所和公园不造售卖供给。精确类别表以[地图供给规格](../dev/SPEC-map-and-storefront.md#spec-map-and-storefront-按地点类别生成完整供给)为准。
真实料理已发布有来源的 `ingredient` 品类、蔬菜食材包对象与单输入单输出配方；输出绑定与库存单位严格验证，
输入和输出的 `inventory_unit=item`，库存资源仍为 `inventory/item`，
制作完成后才出现能吃的产物。已有无产出的「家常做饭」不能作为可吃晚饭的替代。

```mermaid
flowchart LR
    K[已有地点类别] --> M[完整对象准入池]
    C[已准入对象] --> M
    M --> A[逐店类别和对象声明生成实际供给]
    R[对象范围门店定位与场景种子] --> Q[固定小扰动生成一次报价]
    A & Q --> S[冻结独立供给报价库存与履约]
    S --> W[运行只读定义与持久化当前事实]
```

构建复用已有 Pydantic、品类祖先查询与 SHA-256；不新增随机配置层、第三方包或兼容稀疏手填的回退路径。
每条供给独立声明真实账户、正库存及适用履约；分类、差价与数量是场景假设，不能伪装成真实门店菜单。
场景选择逐店声明的全部实际供给，单次提示仍按窗口分页；呈现数量不裁剪冻结世界供给。
正式场景须满足餐馆至少8正餐且每对菜单各有至少3项差异、便利店40对象/6品类、超市80/8、商户40/6且含日用品、
菜市场8真实原料/2原料子类。这是当前正式场景验收，不是行业事实；不足拒绝发布，不随机补齐或生成伪商品。
菜单所需正餐子类或具体对象必须有来源，不从店名猜菜系；真实到店购买及制作食用链还须通过行为夹具。
价格构建对 `[random_seed, location_id, object_id]` 使用
`json.dumps(..., ensure_ascii=False, separators=(',', ':'))` 后编码为 UTF-8，取 SHA-256 摘要的无符号大端整数 `h`，
逐店 `price_position_percent` 为有来源的整数 5 至 95，固定 `jitter = h % 11 - 5`，
以 `min + ((max - min) * (price_position_percent + jitter) + 50) // 100` 生成一次，不对每商品独立抽整个范围。
本正式地图中的门店按同一地区比较，所有便利店定位至少高于地图内普通超市10个百分点，
同商品报价不得更低，舍入同价合法；不新增扰动配置或地区字段。
地图结构 3 必填 `random_seed/category_assignment_sha256`，
分类文件由地图和语料清单共同钉定，范围来源保留于对象与报价来源，不加重复摘要配置。
正式场景 `products/supplies` 显式包含选中门店全部声明商品与生成供给，加载和正式验收核对实际集合，
不能让旧稀疏选择列表裁掉丰富供给，不自动选择或补齐缺项。
运行查询与交换求值不重新抽价；通用无售卖类别地点可无供给，缺货或闭店不生成替代餐食。

<a name="resource-engine-冻结地图的准备与发布"></a>
### 冻结地图的准备与发布

地图准备读取指定种子的张江地图声明及相邻商品目录，复制已审阅地点、类别与逐店声明及其来源，
按每店完整集合重建冻结供给，再逐条取得声明范围内的道路响应。
地图加载与场景引用按数据集合身份校验；准备命令的现有输入布局见其参数，不把本次地域准备流程扩写为通用地图服务。
成功响应必须同时有完整几何、正距离与时长以及与声明方式相符的实际路段；`NoRoute` 才能证明不可达，
资料未知则保留显式原因。所选有向地点与方式的任何漏项都拒绝发布。

```mermaid
flowchart LR
    I[指定源目录中的张江地图与目录] --> S[独立暂存目录]
    S --> R[获取并校验全部道路响应]
    R --> H[更新版本文件哈希与来源时间]
    H --> A[重新执行地图准入]
    A --> P[发布冻结数据集合]
```

地图准备与道路重新取得在 Dataset 执行，操作以数据仓工具说明为准；验证文件和引用后发布新环境身份，
失败不能覆盖已有发布物。运行模拟仅消费冻结文件，来源结构、许可通知、可信截止日和适用范围须原样保留。
新道路事实保存自身取得时间，供给假设不能随路线重新取得而变成真实零售资料。
正式种子分别冻结独立报价；不同种子可以共享地点、路线和类别来源，不能复用另一种子的供给身份。

地图准备按出行方式解析冻结品类配对，以 `TravelFareRule` 和 `evaluate_travel_fare` 计算费用，
与运行时共享闭集语义，不重新引入文本费率公式。准备器仍负责冻结道路资料，不拥有交换规则。

<a name="resource-engine-资源定义的实现与验收"></a>
### 资源定义的实现与验收

| 事项 | 当前承载与要求 |
| --- | --- |
| 类型与字段 | [catalog/models.py](../../src/worldmock/catalog/models.py)定义资源、交换对象、来源和单位约束；场景模型承载实际内容引用 |
| 现有规则字段 | 资源文件同时保存部分规则参数，加载同一文件不意味着资源引擎拥有这些参数的计算语义 |
| 内容结构边界 | 各类内容保留各自的字段与读取路径，不宣称已经存在统一资源类或全部内容共用的数据表 |
| 验收 | 加载真实定义，验证身份、类型、单位、边界与来源；按指定标识和版本查询时返回对应属性。相关用例见 [tests/catalog](../../tests/catalog) |

<a name="resource-engine-组织与发布目录"></a>
## 4. 组织与发布目录

新增状态资源、对象或语料时，资源引擎维护定义与素材，交换引擎维护关联的使用条件和投入产出规则。
新增、修改、删除及引用检查的整体流程见[注册表维护设计](02-exchange-engine.md#exchange-engine-维护交换规则)。

<a name="resource-engine-文件职责"></a>
### 文件职责

本节只列资源定义、来源语料与发布清单。同一存储目录中的其他规则文件由其所属引擎维护。

| 文件或目录 | 保存内容 | 维护入口 |
| --- | --- | --- |
| catalog/manifest.json（`../WorldMock-Dataset/environment/zhangjiang-baseline/catalog/manifest.json`） | 主次版本、`category_requirements`、契约版本及契约、规则、目录内容摘要 | 目录生成器与受验证的发布入口 |
| catalog/resources.jsonl（`../WorldMock-Dataset/environment/zhangjiang-baseline/catalog/resources.jsonl`） | 可计量资源定义 | 目录生成器 |
| catalog/objects.jsonl（`../WorldMock-Dataset/environment/zhangjiang-baseline/catalog/objects.jsonl`） | 身份、品类、属性增量、来源与对象版本；不含内联交换规则 | 目录生成器与构建原语 |
| catalog/category-contract.json（`../WorldMock-Dataset/environment/zhangjiang-baseline/catalog/category-contract.json`） | 品类树、槽位、共享值与配对状态 | 品类维护与审核发布 |
| catalog/exchange-rules.json（`../WorldMock-Dataset/environment/zhangjiang-baseline/catalog/exchange-rules.json`） | 引用品类的闭集交换规则 | 交换引擎维护与审核发布 |
| catalog/rules/off-foods.mapping.toml（`../WorldMock-Dataset/environment/zhangjiang-baseline/catalog/rules/off-foods.mapping.toml`） | 食品来源字段映射 | 人工维护 |
| corpora/manifest.json（`../WorldMock-Dataset/environment/zhangjiang-baseline/corpora/manifest.json`） 与 corpora（`../WorldMock-Dataset/environment/zhangjiang-baseline/corpora`） | 文件身份、元数据、正文、时间轴及来源证据 | 离线整理与固化 |
| catalog/overrides/tombstones.jsonl（`../WorldMock-Dataset/environment/zhangjiang-baseline/catalog/overrides/tombstones.jsonl`） | 目录条目的退役记录 | 当前生成器输出空文件；构建原语支持追加 |

`catalog/manifest.json` 不保存语料文件清单，也没有通用 `defaults` 对象。
`corpora/manifest.json` 不负责定义资源单位、活动规则或闭集交换规则。
资源引擎按调用方指定的两份清单加载数据，不混用版本字段。
固定活动与候选声明由[包资源](../../src/worldmock/data/affordances.toml)提供，环境目录的副本仅绑定冻结身份，
必须逐字节一致。来源专属标题映射、目标语料映射和发现映射已删除；内容名称直接投影场景必填 `title`。
生活活动 `effects` 必填 entertainment/social/health 三键，从实际语料根载入，启动复制到活动状态，推进与恢复只读冻结值。

<a name="resource-engine-构建与版本"></a>
### 构建与版本

下列命令使用[运行配置](../manual/runtime.md#runtime-使用已安装的环境)中已经激活的 WorldMock 环境。

正式目录生成与可复现检查在 Dataset 执行，来源、对象数目和许可由数据仓验收；具体操作见数据仓制作工具说明。
[目录构建原语](../../src/worldmock/catalog/builder.py)负责通用对象追加与退役；
`worldmock category check/refine/publish` 提供品类检查、细化影响分析和新目录原子发布，
命令输入和输出见[接口合同](../CONTRACTS.md#contracts-cli)。

调用方以 `catalog_version.major/minor` 指定目录；条目使用自己的整数 `version`。
目录的具体版本和摘要由 Dataset 清单绑定，品类契约和交换规则绑定版本为 `3`；生活场景结构为 `6`、地图结构为 `3`，
世界状态结构为 `9`（个人事务与工作安排规格阶段 2：人物状态增未完成的个人事务）。正式对象全集、商品选择与逐种子供给数量在 Dataset 验收，引擎验证所选环境的实际完整性。
可供性占用声明按[同时做两件事规格](../dev/SPEC-parallel-activities.md#spec-parallel-activities-占用声明)在[包资源](../../src/worldmock/data/affordances.toml)中重列，其字节由 `tool_schema_sha256` 绑定；仓库人工测试环境 `tests/fixtures/environment/` 的副本逐字节同步、清单的目录内容摘要随之刷新，仍带旧占用名副本的外部发布环境在装配时被拒绝，要由 WorldMock-Dataset 按新占用名重新发布。
完整供给阶段已按目录 `5.0` 独立验收并冻结，真实料理及后续目录内容修改按现行规则发布为新版本。
当前生产身份取自正式清单；旧输入和检查点通过版本、内容、模板与代码身份直接拒绝。

这些格式直接拒绝旧输入，不保留旧场景转换脚本，也不能以重建目录代替创建新格式运行。
加载器拒绝同一标识的非递增条目版本，也拒绝新引用使用已退役条目。
已加载的目录视图保持固定。发布物同时绑定版本与内容摘要，加载和运行恢复会核对文件内容；
同版本异内容明确拒绝。
条目退役保留历史定义可查询性。

<a name="resource-engine-目录发布的实现与验收"></a>
### 目录发布的实现与验收

| 事项 | 当前承载与要求 |
| --- | --- |
| 生成与检视 | 生成器、构建原语、检视函数与 `worldmock category` 命令分别承载确定性生成、对象维护和品类发布 |
| 增量发布 | 构建原语保留世界素材品类要求与所有清单字段，失败恢复原文件；品类发布在独立暂存目录校验并原子公开新路径 |
| 内容身份 | 绑定资源、对象、契约、交换规则、活动声明与规则文件，身份贯通运行清单、检查点和导出 |
| 验收 | [专项台账](../dev/category-contract-verification.md)记录再生成、版本保留、身份篡改、发布失败与真实场景准入测试 |

目录身份与实际素材位置分离的组织方式参考
[Unity 运行时资源管理](https://docs.unity.com/en-us/engine/6000.7/manual/assets-and-media/assets-managing-runtime/introduction)。
本项目复用已有目录与素材引用，不新增资源管理服务。

<a name="resource-engine-校验资源可用性"></a>
## 5. 校验资源可用性

<a name="resource-engine-文件身份校验"></a>
### 文件身份校验

加载时检查清单身份、重复标识与路径，以及每个文件的路径边界、符号链接、大小和哈希。
素材被覆盖、缺失或替换时拒绝加载，不能静默跳过条目。
具体校验入口是 [load_manifest](../../src/worldmock/corpora/manifest.py)。
当前加载器检查清单内文件，不额外遍历根目录拒绝所有清单外文件。

<a name="resource-engine-准入边界"></a>
### 准入边界

| 校验对象 | 必须满足的条件 | 证据入口 |
| --- | --- | --- |
| 目录结构 | 字段合法，标识与版本合法，资源单位和边界有定义 | [目录模型与加载器](../../src/worldmock/catalog/loader.py) |
| 消费者引用 | 请求的版本、对象和资源存在，单位声明匹配，关联定义可解析 | [目录准入中的引用检查](../../src/worldmock/catalog/admission.py) |
| 语料身份 | 清单与每个文件的路径、大小和哈希匹配 | [语料加载器](../../src/worldmock/corpora/manifest.py) |
| 世界素材 | 正餐、地点、内容元数据、工作材料、联系人等符合版本化品类要求 | [世界素材准入](../../src/worldmock/catalog/admission.py) |
| 内容可达性 | 检索维度完整，条目至少存在一条可行检索路径 | [检索与可达性](../../src/worldmock/life/retrieval.py) |

具体品类阈值以目录清单和[世界素材规格](../dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像)为准。
这里的可用性指资源数据完整、引用有效、存在检索路径；活动此刻能否执行由调用方按交换规则判断。
静态可达性检查可使用调用方提供的地点类等配置，但不据此替代运行时资格判断。

<a name="resource-engine-地图关系与道路范围准入"></a>
### 地图关系与道路范围准入

选择的地点、商品、供给及账户引用必须完整；同一供给组合不能重复。场景不得复制地点定义或路线。
每个所选有向地点对与 walking/driving 方式都有显式 `reachable/unreachable/unknown` 记录。
成功及已证不可达记录绑定原始响应和哈希；未知记录带来源说明且不可执行出行。漏记录拒绝发布。
报价及营业有效区间不得重叠，核对日期和可信截止日合法且未过期；缺活动环境、错误时区或坐标拒绝发布。
构建类别和对象引用必须真实存在，全部供给报价与未来调价均在对象冻结范围内；
生成材料、目录与场景种子一致，逐店实际集合完整且符合准入池、丰富度和差异验收，卖方和履约明确。
失败记录英文具体路径、合法枚举或范围。
正式数据集发布以明确的习惯动作夹具验证「买了再用」「做了再吃」完整链；现有关键词覆盖率不证明行为可执行。
运行覆盖不足仍是输入警告，`validate` 的 80% 门禁保留，不将发布行为门禁改成运行期失败。

<a name="resource-engine-准入失败的处理"></a>
### 准入失败的处理

| 问题 | 处理方式 |
| --- | --- |
| 清单不匹配、文件损坏、路径越界或必需素材缺失 | 完整性错误，拒绝加载并保留错误原因 |
| 非法资源定义、单位或来源引用 | 准入失败，修正输入再运行 |
| 身份存在但必要检索元数据不完整 | 在品类与可达性准入中报告，不能用展示标题代替真实元数据 |

<a name="resource-engine-资源校验的实现与验收"></a>
### 资源校验的实现与验收

| 事项 | 当前承载与要求 |
| --- | --- |
| 结构与引用 | 使用 [catalog/loader.py](../../src/worldmock/catalog/loader.py)和 [catalog/admission.py](../../src/worldmock/catalog/admission.py)中的目录结构、资源引用及品类检查；同模块的结算约束和运行初值检查不属于本节 |
| 文件完整性 | [corpora/manifest.py](../../src/worldmock/corpora/manifest.py)核验清单内文件；当前不会额外遍历根目录拒绝所有清单外文件 |
| 静态可达性 | [life/retrieval.py](../../src/worldmock/life/retrieval.py)检查条目是否具有有效检索维度及至少一条可行路径 |
| 验收 | 用版本错配、非法单位、失效引用和损坏素材验证拒绝路径；对合格条目检查至少一条检索路径，不以曝光率作为静态准入判据 |

相关用例见 [tests/catalog](../../tests/catalog)与 [tests/corpora](../../tests/corpora)。

<a name="resource-engine-检索与呈现"></a>
## 6. 检索与呈现

<a name="resource-engine-从目录到候选"></a>
### 从目录到候选

下图表达完整设计流程。当前运行已共用候选、排序与呈现模块，倒排检索与 facet
已有独立实现，但尚未接入运行中的浏览链路，差距见下文“检索呈现的实现与验收”。

```mermaid
flowchart LR
    ALL[固定版本的目录条目] --> QUERY[按题材、语言、时长段匹配]
    INPUT[调用方给定的候选范围与排序输入] --> FILTER[按给定范围筛选]
    QUERY --> FILTER
    FILTER --> ORDER[确定性排序与轮转]
    HISTORY[调用方给定的呈现历史与游标] --> ORDER
    ORDER --> PAGE[返回当前呈现窗口与下一页游标]
```

检索维度为 `genre`、`language`、`duration_bucket`。题材允许多值，时长使用固定整数秒边界，
桶标签由 [duration_bucket](../../src/worldmock/life/retrieval.py) 生成；地点不作为检索分区键。
调用方区分目录浏览与当前可执行选项，并给出相应候选范围和可见性限制。
资源引擎按这些输入筛选条目，匹配查询与生成呈现窗口；活动受理判断由调用方完成。
排序使用给定的偏好和注意力输入，零偏好不作为删除条目的依据。

同一冻结目录、候选范围、排序输入、查询、游标和呈现历史应得到相同有序结果。
按传入的 `retrieval_index_threshold` 选择线性扫描或确定性倒排索引，两条路径使用相同查询语义。
当前合同采用可枚举的词项与 facet，不使用向量检索或近似最近邻，保证查询结果可复现。

<a name="resource-engine-轮转与可达性"></a>
### 轮转与可达性

候选数量受 `candidate_pool_limit` 与 `candidate_presentation_limit` 约束。
资格先于排序：生活活动窗口按人物当前地点施加地点类资格，地点类不符的条目不进窗口，以
`ELIGIBILITY_LOCATION_KIND_MISMATCH` 记为排除项（一日运行保真规格 5.1，裁决 S8）。
排序键为（非零口味，−口味×注意力，−相关度，哈希决胜键）：相关度只取目录时间窗、当日未推进目标的活动家族与人设层习惯签名
（目标取该秒开始时的今日计划，同一秒各轮的呈现窗口不变），
决胜键是场景、人物、本地日期与对象标识的 `sha256`，零偏好时的顺序不是对象标识的字母序，并随日期变化；排序键版本为 2（裁决 S4）。
轮转按检索分区内每个对象的 `last_presented_at`、`rotation_quota_ratio` 与 `rotation_stale_seconds` 工作：
换新名额按上次呈现时刻从早到晚留给该分区内长期未呈现的对象（从未呈现的最先），其余名额按排序键；
不用人物级共享偏移量，也不设分区游标，`⌈N ÷ k⌉` 的上界按分区成立。
本次没有进入窗口的条目仍在目录内，排除原因包含 `ROTATION_DEFERRED`。

呈现窗口之外，工具层另附只列名称的可调用索引 `callable_index`（[一日运行保真规格](../dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引) 5.2，
裁决 S5），工具层因此是 `{layer, targets, callable_index}`。索引恰好四键：`lifestyle` 列出生活活动窗口资格段保留的全部名称（呈现项与
`ROTATION_DEFERRED` 项，不带「活动：」前缀），即此地此刻可做的条目，地点类不符等被资格段排除的不在其中；`contents` 与 `goods` 按品类给
`{category, count, examples}`：内容取内容与游戏两个窗口资格段保留、有标题的条目（游戏会话与可玩游戏同为「游戏」），商品取此刻从这里
能得到的（门店开着、报价已知、有货、人物有资格且购买规则已定义，并且到店时门店就在这里、或远程下单能送到这里）。
`query_categories` 列出 `query_world` 的 `category` 可写的公开品类标签与各自的条目数 `{category, count}`，取本轮查询权限内的完整条目，
不按呈现窗口截断；`query_world` 的 `from`、`available_only` 筛选不改变这一计数口径。`contents`、`goods` 的计数是可以写的名称
个数（同名只计一次），写成「N 个」；示例至多 3 个（商品写商品名，不带门店），先取本轮没有列为目标的，与另一件内容或游戏同名的不作示例，
示例与 `lifestyle` 的顺序都用同一哈希决胜键。有效菜单不许做生活活动、观看（可玩游戏为游玩）或买东西时，对应部分为空。
索引不是候选条目：不计入 `candidate_presentation_limit`，不写 `last_presented_at`，也不影响轮转；它在 user 消息里，计入整次请求的
容量计数。它让人物知道本轮没有呈现、但可以按名调用的名称，写在索引里的名称按下文的按名调用解析，受理时照常做资格与准入判定。

轮转只改变呈现窗口，不按点击次数、偏好分或互动次数永久移除对象；它也不限制指名购买，
本轮未呈现但存在的商品仍可由人物按名称购买，解析与结算由模拟引擎组织。
静态可达性只保证“存在一条能找到该条目的路径”；实际曝光还依赖人物到达地点、查询分区
及决策次数，不能宣称每次有限时长运行都会呈现全部素材。
呈现历史由调用方提供；本次窗口、呈现标识和游标作为结果返回，运行状态的更新与保存由调用方负责。
规则依据见[候选与呈现合同](../CONTRACTS.md#contracts-候选与呈现合同)。

<a name="resource-engine-地点发现与供给比较"></a>
### 地点发现与供给比较

地点发现返回匹配地点，公园等没有供给的地点仍可发现；购买比较按 `supply_id` 区分，保留 `product_id` 引用。
道路范围按冻结路线和分时系数判断，直线距离只可用于粗筛；已知超范围、不可达和未知分别解释。
关闭、售罄或缺品牌不删除地点身份。无结果与未知条件不能混为不存在。
购买排序、分页、轮转、排除统计与可见凭据按供给身份，持有和使用按商品身份。
展示与比较以实际呈现为界，不能借浏览一次展示整份商品目录；可见凭据只管展示与比较，按名称购买本轮未呈现的商品不需要它。
按名调用推广到全部目录类对象（[一日运行保真规格](../dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析) 3.1）：名称先精确匹配，再按统一
名称解析无损归一后唯一即受理，本轮未呈现的生活活动、内容与游戏以及没有列出路线的地点同样可以写名称，受理时照常做资格与准入判定。
指名购买按「<门店>出售的<商品>」或商品名（同一归一口径）分段匹配（[一日运行保真规格](../dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法)
3.2、3.3、3.5），各段各报各的码：M0 按名称，为空报 `SUPPLY_NOT_FOUND`（只表示没有这件商品）；M1 按 `from` 这一可选约束限定来源（null
为全部门店，本段跳过；地点显示名（含「家」「住处」）为该地点；渠道称呼即 App 名，去掉空白、不分大小写比较，为该渠道的成员门店，渠道由场景钉定的手机配置派生；
都不是时报 `SOURCE_NOT_FOUND`，候选为最相近的门店名与 App 称呼），标签写明的门店不在范围里报 `SUPPLY_SOURCE_MISMATCH`，商品名在范围里
没有报 `SUPPLY_NOT_AT_STORE`；M2 按所写方式，为空报 `FULFILLMENT_NOT_SUPPORTED`（方式为「购买」时跳过）；M3 按此刻能否服务（到店要人
在那家店里，远程下单要能送到收货地，「购买」人在出售它的店里、东西在那里交得到收货人手上——收货人是自己或此刻也在那家店里——就到店、
否则能送到收货地就远程下单），为空以 `ACTIVITY_PRECONDITION_UNMET` 写明要去哪里（只指向能到店买的出售门店）或哪家送不到哪里（送给
别人时下一步是先送到自己这里再当面给）。多家都能服务时按请求级 `prefer` 选定一家（“最快拿到”或“到手价最低”，并列时先比到手价、再比门店标识），所选写进受理结果
`details.notes`，不报歧义。找不到时给出的
至多 3 个候选先按请求方式和已知意图限定对象类别，再按当前可行性与确定性词项相似度排名。
候选采用完整 `{tool, arguments, note}`：`arguments` 是该工具可直接验证的完整请求，start_activity包含items/answering/prefer，
每个items对象恰为全键必填的{target,way,from,to}；to=null统一表示本人，含购买项时prefer必为“最快拿到”或“到手价最低”，否则为null，
不使用旧 `{target, action, way, from, to}` 片段。候选必须属于人物可读、可调用范围，不从相似字面推荐无关跨类别对象，
不替换人物写的名称；同样遵守上文不使用向量检索或近似最近邻的约定，相同输入必须有相同结果及顺序。
引擎已经知道是对象不存在、任务尚未到约定时刻、此地没有原料或配方无产物时，返回真实原因与必要条件，
不能只给相似名称让人物反复猜测。错误 `items[]` 每项包含
`path/written/error_code/allowed_values/expected/why/candidates`；`path` 为从本次模型工具调用根开始、
保留全部下标的完整位置数组，`why` 写参数、名称或世界前置条件的英文原因。
有时长浏览的登记及后轮反馈由模拟引擎组织，实际返回结果由记录引擎保存。
固定 `query_world` 查询由模拟引擎按冻结观察授权读取，资源引擎只返回该授权范围的定义与事实；
不开放跨人物私有库存或未告知任务，不更新供给可见凭据，不推进活动或世界时间。
请求为六键全部必填、无缺省的 `query_world(name, way, category, from, available_only, offset)`：`name`、`way`、`category`、`from`
可空但至少一个非空，`available_only` 为布尔且不单独构成查询条件，offset 为非负整数。`from` 写购买来源（地点显示名，含「家」「住处」，
或渠道称呼），与购买请求的 `from` 用同一个来源解析函数，只匹配供给条目；`available_only` 为 true 时只留冻结观察里已确认此刻可执行的
条目，由模拟引擎读取同一观察的动作与工具 `available` 完成，资源引擎不判断可执行。category 取本轮 callable_index 的 `query_categories`
已展示的公开品类标签，非法时给全部合法标签；筛选在排序与切页之前施加，结果为 matches/total_count/next_offset，`total_count` 是全部筛选
交集后的匹配数，next_offset 必填可空，每页大小取场景的 `candidate_presentation_limit`（现行三份场景均为 20），同一冻结列表可按偏移遍历
到末尾，不永久截断或新增游标层；逐项不再附候选。除两种参数错误（`from` 既不是地点显示名也不是渠道称呼；`from` 为 null、`name` 无任何
匹配且恰是一个渠道称呼）外，任何筛选组合无匹配都返回空页，不自动放宽、不换来源；来源解析成功但没有权限内供给同样是空页，不泄露
不可访问的门店。它不是按商品需求发现门店和路线的工具，`from` 不反查门店、不计算路线、不按距离或价格排序，不恢复已删除的
discover_places 接口。

<a name="resource-engine-无结果与范围限制"></a>
### 无结果与范围限制

| 情况 | 处理方式 |
| --- | --- |
| 当前查询无匹配项 | 返回真实空结果和可用查询信息，不编造条目补齐 |
| 条目不在调用方给定的候选或可见范围内 | 不纳入本次结果，保留相应筛选依据；不自行作出活动拒绝，也不删除目录条目 |

交互菜单是模拟引擎的入口准入约束，不是资源候选的世界事实排除条件。资源引擎仍返回完整候选、排除
证据与目录事实；菜单可以让模拟引擎撤下某个下钻入口或启动资格，但不能让资源引擎删除条目、改写排除
原因计数或缩小 `affordance_contract`。活动类型名称引用现有可供性注册表的 `kinds`；注册表 `surface`
不构成全部生活工具名清单。边界见[交互菜单规格](../dev/SPEC-interaction-menu.md#spec-interaction-menu-特性规格交互菜单)。

<a name="resource-engine-检索呈现的实现与验收"></a>
### 检索呈现的实现与验收

| 事项 | 当前承载与要求 |
| --- | --- |
| 检索与可达性 | [life/retrieval.py](../../src/worldmock/life/retrieval.py)已有倒排索引、查询和 facet 纯函数 |
| 候选与呈现 | [presentation.py](../../src/worldmock/life/presentation.py)与 [rotation.py](../../src/worldmock/life/rotation.py)承载排序、窗口和轮转；[callable_index.py](../../src/worldmock/life/callable_index.py)从同一批窗口的资格段保留条目构建工具层的可调用索引 |
| 职责分离差距 | 当前 `presentation.present` 调用 `candidates.recall`，候选链仍包含运行资格判断；设计上资源引擎只消费调用方确定的范围，现有代码尚未完全分开 |
| 运行接入差距 | `presentation.present` 与生活浏览动作尚未调用倒排检索链路，当前浏览结果不能宣称已提供 facet 返回体 |
| 验收 | 相同目录与只读查询输入应产生相同结果；索引与扫描结果一致，轮转不改目录、长期未呈现对象不被永久排除；可调用索引里的每个名称都能按名调用，不含被地点类资格排除的活动，容量计数含索引。相关用例见 [tests/life](../../tests/life) |

<a name="resource-engine-提供实际接触的内容"></a>
## 7. 提供实际接触的内容

<a name="resource-engine-按指定范围提供内容"></a>
### 按指定范围提供内容

调用方给定内容身份、版本或哈希、允许读取的范围，以及已经确认的实际接触区间。
资源引擎核验文件身份，读取相应正文或时间轴片段，返回内容与来源。
权限和接触区间由调用方确定，资源引擎在读取时执行给定的范围限制。

```mermaid
sequenceDiagram
    participant S as 模拟引擎
    participant R as 资源引擎
    S->>R: 内容身份、指定版本、允许范围与实际接触区间
    R->>R: 核验文件身份与时间轴
    R->>R: 读取范围内的正文或片段
    R-->>S: 内容、片段范围、素材身份与来源
```

一般字幕取给定区间与时间轴相交的片段；NASA 播客选段按官方段落锚点读取，
只有真实媒体位置完整跨过该段的下一锚点才揭示段落文字。暂停、倍速和恢复使用同一媒体位置，
不按字数推算逐词时刻。文档或邮件正文按给定的读取范围返回。
当前查询只需要元数据时，不返回完整正文。游戏素材与既有文本可以读取，运行中产生的游戏反馈由调用方提供。

<a name="resource-engine-返回内容与来源"></a>
### 返回内容与来源

| 返回信息 | 资源引擎保证 |
| --- | --- |
| 条目身份与内容版本 | 与请求的固定资源相对应 |
| 正文或内容片段 | 来自已核验的来源文件，符合给定的可见范围与接触区间 |
| 素材身份与来源 | 提供可定位来源的版本、哈希或已有素材引用，保留相应来源信息 |
| 片段范围与媒体类型 | 如实说明提供的内容范围和模态，字幕不标成视频帧或音轨 |

返回内容表示本次提供了哪些材料。它不自行推进活动、更新续看位置或写入人物知识；
实际交付与状态提交由调用方完成。

<a name="resource-engine-内容读取失败"></a>
### 内容读取失败

实际读取时再次发现文件缺失、路径非法、大小或哈希漂移，应拒绝该次读取并保留错误原因。
不能用占位正文、跳过片段或编造结果替代缺失内容；未提供的内容也不能计为已接触。

<a name="resource-engine-内容提供的实现与验收"></a>
### 内容提供的实现与验收

| 事项 | 当前承载与要求 |
| --- | --- |
| 内容与时间轴 | [life/content.py](../../src/worldmock/life/content.py)承载固定语料、字幕时间轴和邮件正文的读取 |
| 职责分离差距 | 当前 `reveal_watch` 与 `reveal_mail` 同时处理内容和人物阅读状态，`reveal_watch` 还改写活动中的揭示位置；资源读取与运行状态更新尚未完全分开 |
| 验收 | 指定身份、版本和范围时返回对应片段与来源；范围外内容不泄露，缺失或损坏素材明确失败；资源接口自身不推进活动或提交运行状态。相关用例见 [tests/life](../../tests/life) |


<a name="design-resource-engine-current-responsibility"></a>
## 基础类型与资源装配

来源和营业区间使用独立基础类型；人物状态模型与地图模型共用这些定义。DataSource 的 `kind`、`url`、`retrieved_at`、`license`、`notice`、`fact_cutoff`、`applicability` 七个字段保持不变。
世界装配由 `src/worldmock/life/world_setup.py` 编排场景读取、语义组装、准入与工作材料校验；底层解析、基础类型和纯语义模块不回调装配入口。移动定义不改变冻结目录、地图、手机配置或语料的字节身份。

<a name="resource-engine-版本历史"></a>
## 版本历史

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| v1.3 | 2026-09-25 | 同步版本事实：世界状态结构改为 7（时间与能量规格第九章）；生活场景结构保持 3 |
| v1.4 | 2026-09-25 | 同步版本事实：时间与能量规格阶段 4 改可供性注册表（删 `energy_projection`、按第五章重列占用），当前目录升为 `4.1` |
| v1.5 | 2026-09-27 | 同步人物 Prompt 七层规格（人物 Prompt 台账 E3-1–E3-3）：文件头注明修订部分待实现；轮转不限制指名购买；地点发现与供给比较按指名购买改写，可见凭据只管展示与比较，近似候选用确定性词项相似度，遵守不使用向量检索与近似最近邻的约定；版本事实同步世界状态结构 8（`catalog_version` 不变） |
| v1.6 | 2026-09-28 | 人物 Prompt 七层规格实现后文档：文件头注明修订部分已实现，链接人物 Prompt 验收记录；正文不变 |
| v1.7 | 2026-09-29 | 按[一日运行保真规格](../dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要)同步（一日运行保真 RS1，随阶段 1、3、4 的代码落地，按 `e377669` 核对）：版本事实改为地图结构 2（地点可带 `branch_label`）；「轮转与可达性」生活活动窗口先施加地点类资格（不符的记 `ELIGIBILITY_LOCATION_KIND_MISMATCH`），排序键加相关度（目录时间窗、当日未推进目标的活动家族、习惯签名）与场景、人物、日期、对象的 `sha256` 决胜，排序键版本 2，轮转按分区内每个对象的 `last_presented_at` 留换新名额、不用人物级共享偏移量与分区游标、`⌈N ÷ k⌉` 按分区；工具层另附只列名称的可调用索引 `callable_index`（`lifestyle` 为资格段保留的生活活动名称，`contents`、`goods` 按品类给「N 个」与至多 3 个示例，不是候选、不计入呈现上限、不写 `last_presented_at`，计入整次请求的容量计数，其中的名称可以按名调用），实现与验收表补 `callable_index.py` 与 5.2 的验收点；「地点发现与供给比较」按名调用推广到全部目录类对象与没有列出路线的地点，无损归一后唯一即受理，候选（商品取全部供给，其余名称跨类别）用确定性词项相似度、从不替换，指名购买按分段匹配 M0–M3 各报其码（`from` 为 null 跳过 M1，渠道称呼去掉空白、不分大小写比较，方式「购买」跳过 M2 并在 M3 定到店或远程下单），多家都能服务时按 `prefer` 选定并写进 `details.notes`；文件头补修订说明，日期改写 |
| v1.8 | 2026-10-01 | 工具交互设计冻结并已实现：按已有地点类别完整分配品类对象、种子确定性范围内门店报价、有来源的单食材包做饭链与正式数据集行为门禁；独立供给和运行警告边界保留 |
| v1.9 | 2026-10-03 | 同步个人事务与工作安排规格阶段 2（实施计划 D3-01、D3-02、D3-06，按 `d09d574` 的代码核对）：版本事实改为世界状态结构 9（人物状态增未完成的个人事务），目录 `5.3`、绑定版本 `3`、场景结构 `4`、地图结构 `3` 与对象、商品、供给数目不变；文件头增修订行，日期改写；版本表改为末行最新（原插在表首的 v1.8 行移到 v1.7 之后，行内容不改） |
| v1.10 | 2026-10-03 | 同步个人事务与工作安排规格阶段 4（实施计划 D3-04、D3-05、D3-06、D3-11）：`query_world` 改六键，来源筛选与购买同一解析，可执行筛选归模拟引擎，筛选先于切页、`total_count` 为筛选后总数，结果不附候选，除两种参数错误外无匹配为空页；可调用索引更正为四键并写明 `query_categories`（筛选不改变其计数口径）；指名购买来源的写法更正为地点显示名；每页大小改写为取场景参数；文件头修订行改为已落地阶段 2 与阶段 4（阶段 4 与阶段 2 并行开发，在阶段 2 之后并入） |
| v1.11 | 2026-10-03 | 同步个人事务与工作安排规格阶段 3（实施计划 D3-03）：版本事实改为生活场景结构 5（人物配置增必填可空的 `work_schedule`；目录保持 5.3），世界状态结构照阶段 2 为 9；文件头修订行改为已落地阶段 2–4（阶段 3 在自己的分支上开发，在阶段 2、4 之后并入） |
| v1.12 | 2026-10-04 | 个人事务与工作安排规格实现后文档（实施计划 D3-10）：文件头修订行注明已实现并链接个人事务与工作安排验收记录（证据为离线测试，规格阶段 6 的配对全天复跑未执行）；正文不变 |
| 环境数据归属 | 2026-10-05 | 按环境数据归属规格同步双仓职责、包内通用规则与显式环境路径；场景结构 6、必填标题和冻结活动效果按正文说明，最终验收另记实际收据 |
| `2026-10-05-layering-and-test-tiers` | 2026-10-05 | 当前职责对象与依赖方向、五项行为正文归属、固定历史来源和稳定引用；业务与持久合同保持不变 |
| 共享规则与可重建环境 | 2026-10-05 | 同步规则归属、源码重建与验收合同；目录按可供性声明注释的字节身份发布 5.4，业务数值与品类契约、规则绑定版本不变，历史发布记录保留原版本 |
| `2026-10-06-parallel-activities-claims` | 2026-10-06 | 按[同时做两件事规格](../dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)阶段 2 同步：「构建与版本」补可供性占用声明在包资源 `src/worldmock/data/affordances.toml` 中重列，字节由 `tool_schema_sha256` 绑定，测试环境副本逐字节同步并刷新目录内容摘要，仍带旧占用名副本的外部发布环境在装配时被拒绝、要由 WorldMock-Dataset 按新占用名重新发布；品类契约与规则绑定 3、场景结构 6、地图结构 3、世界结构 9 不变；文件头增该规格修订行，版本升为 v1.15；版本表去掉隔开末三行的空行 |
