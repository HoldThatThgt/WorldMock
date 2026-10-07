<a name="resource-engine-worldmock-产品设计说明书-资源引擎"></a>
# WorldMock 产品设计说明书 — 资源引擎

> **状态**：DRAFT，整合设计；接口与行为仍以冻结合同和规格为准；按人物 Prompt 规格修订，修订部分已实现（证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md)）；按一日运行保真规格同步（一日运行保真 RS1），已实现\
> **日期**：2026-10-07
> **版本**：v1.17\
> **工具交互修订**：类别完整供给、冻结价格范围及真实食材配方设计已冻结，已实现；本次生产验收单独记录；以[工具交互规格](../dev/SPEC-life-runtime.md#tool-interaction-工具请求)为本次合同。
> **个人事务与工作安排修订**：按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)分阶段同步（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md) D3 作业），已实现（离线验收，证据见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)）；该规格阶段 1 不改本文。
> **同时做两件事修订**：按[同时做两件事规格](../dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订「构建与版本」的可供性占用声明（阶段 2）。
> **范围**：目录登记、严格校验、只读对象与派生索引查询

> 按[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订「可计量资源定义、交换对象与内容条目、资源、内容和语料的关系、文件职责、构建与版本、文件身份校验、准入边界、语料清单与文件身份、组织与发布目录、目录发布的实现与验收、职责约定、准入失败的处理、资源校验的实现与验收、基础类型与资源装配」（阶段 1）；「随机地图的冻结」、「随机地图的生成与发布」、「图与地点关系准入」（阶段 2）；「地点、商品与门店供给、地点发现与供给比较、检索呈现的实现与验收、无结果与范围限制」（阶段 3）；「职责约定、准入失败的处理、资源校验的实现与验收、检索与呈现、app 内容」（阶段 4）。

资源引擎登记并校验环境对象、绑定与图，回答“这个世界有什么，如何按实际绑定查询”。
它提供冻结对象与派生索引；资源定义和规则常数归交换引擎。

接口字段引用[接口合同](../CONTRACTS.md#contracts-worldmock-接口合同)，行为引用
[运行时规格](../dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则)、[生活真实性规格](../dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像)与
[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)。具体活动行为以
[一日行为规格](../dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-一日行为可信性特性开发规格)为准。
本文定义环境目录登记、校验和只读查询职责；具体世界的数据准备归 Dataset。

> 按[动作与内容简化规格](../dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订「固定规则、目录格式与加载边界、收货结算」（阶段 1、2）。

具体环境及制作工具按[环境数据归属规格](../dev/SPEC-environment-data-ownership.md)归 WorldMock-Dataset，
正式重制环境路径由 WorldMock-Dataset 发布决定；WorldMock 提供引擎及包内固定通用规则。
本章的 `catalog/`、`scenarios/` 指外部环境目录，命令以 WorldMock 为当前工作目录并显式传路径。
当前场景结构 8、世界结构 11；类别、使用方式、效果与供给表由 WorldMock 固定，
环境只登记对象、绑定与价格、内容时长、集数三种数；新组合放行另核对实际收据。

<a name="design-03-resource-engine-共享规则与可重建环境修订"></a>
## 共享规则与可重建环境修订

按[共享规则与可重建环境规格](../dev/SPEC-shared-rules-and-rebuildable-env.md)修订；验收结果独立记录。

语料文件只接受根内安全相对路径：拒绝绝对路径（含 Windows 上无盘符的根路径）、任何 .. 组件、反斜杠、任一平台不能原样保存的名字（以点或空格结尾、Windows 设备名、禁用字符与数据流冒号、控制字符、孤立代理码点、一级超过 255 字节），以及符号链接、目录联接与非普通文件。曲线输入和输出必须是有限非负数且拒绝布尔值，效率资源名必须含非空白字符；生活与目录读取同一模型。目录发布与品类身份统一以字节原子写入。

<a name="resource-engine-职责与边界"></a>
## 1. 职责与边界

<a name="resource-engine-项目框架映射"></a>
### 项目框架映射

资源引擎回答“世界中有哪些对象，它们如何绑定”，只登记与查询冻结目录。

```mermaid
flowchart LR
    D[数据仓整理名称、摘要与数值] --> C[登记 app、地点、商品、动作、内容与图]
    C --> V[严格校验文件身份和绑定]
    V --> I[冻结对象与反向索引]
    I --> Q[只读目录与图查询]
    Q --> S[模拟引擎选择内容并维护进度与历史]
    X[交换引擎固定规则与常数] --> V
```

<a name="resource-engine-职责约定"></a>
### 职责约定

`worldmock.catalog` 负责登记、严格校验、只读查询与反向索引；`worldmock.exchange` 负责固定分类、规则与常数。
`Catalog` 冻结 app、地点、商品、动作、内容及图，索引在登记通过后一次生成。
内容选择、随机抽取、消费进度、推送与观看历史由模拟引擎负责，资源引擎不保存它们。

<a name="resource-engine-整理与固化语料"></a>
## 2. 整理与固化语料

<a name="resource-engine-整理与固化流程"></a>
### 整理与固化流程

语料整理、固化与来源记录归 WorldMock-Dataset。引擎不读取、校验或钉住任何 `corpora/` 文件，场景没有语料根与语料清单摘要。
运行内容来自 `contents.json`：真实摘要保留 URL，合成摘要保留声明；来源合同见[环境目录](../CONTRACTS.md#contracts-环境目录文件)。

<a name="resource-engine-来源分类"></a>
### 来源分类

语料整理、固化与来源记录归 WorldMock-Dataset。引擎不读取、校验或钉住任何 `corpora/` 文件，场景没有语料根与语料清单摘要。
运行内容来自 `contents.json`：真实摘要保留 URL，合成摘要保留声明；来源合同见[环境目录](../CONTRACTS.md#contracts-环境目录文件)。

<a name="resource-engine-语料清单与文件身份"></a>
### 语料清单与文件身份

语料整理、固化与来源记录归 WorldMock-Dataset。引擎不读取、校验或钉住任何 `corpora/` 文件，场景没有语料根与语料清单摘要。
运行内容来自 `contents.json`：真实摘要保留 URL，合成摘要保留声明；来源合同见[环境目录](../CONTRACTS.md#contracts-环境目录文件)。

<a name="resource-engine-语料整理的实现与验收"></a>
### 语料整理的实现与验收

语料整理、固化与来源记录归 WorldMock-Dataset。引擎不读取、校验或钉住任何 `corpora/` 文件，场景没有语料根与语料清单摘要。
运行内容来自 `contents.json`：真实摘要保留 URL，合成摘要保留声明；来源合同见[环境目录](../CONTRACTS.md#contracts-环境目录文件)。

<a name="resource-engine-地图资料的冻结与许可"></a>
### 随机地图的冻结

地图是合成的连通加权无向图，使用全目录地点身份，不包含真实道路、坐标、路线响应、许可或道路范围。
地点名称与分店标签保持具体环境的真实名称语义，数值明确属于模拟假设；正式对象与发布材料由 Dataset 维护。
图字节纳入目录身份，运行只读固定图，不访问在线路由、库存或报价接口。

<a name="resource-engine-定义资源与对象"></a>
## 3. 定义资源与对象

<a name="resource-engine-资源内容和语料的关系"></a>
### 资源、内容和语料的关系

语料整理、固化与来源记录归 WorldMock-Dataset。引擎不读取、校验或钉住任何 `corpora/` 文件，场景没有语料根与语料清单摘要。
运行内容来自 `contents.json`：真实摘要保留 URL，合成摘要保留声明；来源合同见[环境目录](../CONTRACTS.md#contracts-环境目录文件)。

<a name="resource-engine-可计量资源定义"></a>
### 可计量资源定义

资源元信息和身体规则由 `worldmock.exchange.body` 固定，环境不提供资源定义或效率系数。
金钱是封闭账本资源；能量、睡眠债与娱乐需要是开放身体资源。资源引擎只登记对象，不保存订单、内容接触历史或进度。
环境能填的数只有商品价格、内容时长与系列集数；场景保留人物与账户的初始状态。

<a name="resource-engine-交换对象与内容条目"></a>
### 交换对象与内容条目

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | `format_version: 2`、环境标识与版本、`rules_version: 2`、六个文件的路径/哈希/字节数、`catalog_content_sha256` |
| `apps.json` | `app_id`、真实 `name`、`purposes`、`payment_app_ids`；用途与绑定列表去重并排序 |
| `locations.json` | `location_id`、`name`、可选 `branch_label`、`kind`、`app_ids`；仅店铺允许绑定购物 app |
| `goods.json` | `goods_id`、`name`、五个固定类别之一、正整数 `price_fen`、`store_ids` 列表或 `"all"` |
| `actions.json` | `action_id`、`name`、`description`、五个固定类别之一、`app_ids`、必填可空 `manner` |
| `contents.json` | `content_id`、`action_id`、`title`、摘要与来源、`duration_seconds`、`app_ids`、可选 `series` |
| `map.json` | 地图结构 1、种子、生成器规则版本 1、节点与规范无向边 |

内容必须绑定恰好一个动作；动作可以不绑定 app。动作的 `app_ids` 非空时，`manner` 必须是一种固定使用方式，
每条内容的 `app_ids` 是该动作 app 集合的非空子集；动作没有 app 时，`manner` 为 null、内容 app 集合为空。
每个动作及每个动作与 app 的组合至少有一条内容。空集合从不表示全部 app。
内容 `duration_seconds` 是正整数且不小于 60 秒；动作不登记时长、时间窗、场所或效果。
内容不登记分类；环境维护动作名称、说明及其内容。
系列以 `(action_id, series.title)` 识别，各集具有相同动作与 app 集合，集数为唯一的正整数。

商品绑定店铺，店铺绑定购物 app。`GOODS_CATEGORY_STORE_KINDS` 固定类型可供给的类别，
`"all"` 展开到全部可供给该类别的店铺；显式绑定列表去重排序，同类型店铺同菜单。
购物用途存在当且仅当至少一家店铺绑定该 app；支付绑定非空当且仅当有购物用途，
绑定目标具有支付用途，允许绑定自己。导航与支付 app 可以没有内容；app 必须有用途或动作绑定。

<a name="resource-engine-地点商品与门店供给"></a>
### 地点、商品与门店供给

商品有正餐、零食、饮品、休闲用品与日常用品五类，每个商品只有一个固定价。
餐馆供给正餐、零食与饮品，咖啡馆供给零食与饮品，便利店和超市可供给五类。
绑定只允许可供给该类别的店铺；`"all"` 展开到全部合格店铺，显式列表按身份排序，同类型店铺同菜单。
店铺的 `app_ids` 指向购物 app；没有 app 绑定的店铺只能到店购买。

模拟引擎只保存实际订单与收货状态，没有食品持有。

<a name="resource-engine-冻结地图的准备与发布"></a>
### 随机地图的生成与发布

数据仓以已登记地点与种子调用 `worldmock environment generate-map`，发布随机连通加权图。
`worldmock environment check` 重新生成后逐值核对规范节点与边，并检查目录登记。
`worldmock validate --environment` 登记完整目录、重生成比对地图并加载全部场景，成功结果为 `{valid, environment_id, environment_version, scenarios}`。
环境只有 `catalog/` 与 `scenarios/`，不含语料清单或包规则副本。

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

发布环境只有并列的 `catalog/` 与 `scenarios/`。数据仓准备名称、摘要与合成数值，发布登记目录和场景。
引擎目录中的来源仅为内容 `summary_source`；运行不读取或核验语料。

<a name="resource-engine-文件职责"></a>
### 文件职责

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | `format_version: 2`、环境标识与版本、`rules_version: 2`、六个文件的路径/哈希/字节数、`catalog_content_sha256` |
| `apps.json` | `app_id`、真实 `name`、`purposes`、`payment_app_ids`；用途与绑定列表去重并排序 |
| `locations.json` | `location_id`、`name`、可选 `branch_label`、`kind`、`app_ids`；仅店铺允许绑定购物 app |
| `goods.json` | `goods_id`、`name`、五个固定类别之一、正整数 `price_fen`、`store_ids` 列表或 `"all"` |
| `actions.json` | `action_id`、`name`、`description`、五个固定类别之一、`app_ids`、必填可空 `manner` |
| `contents.json` | `content_id`、`action_id`、`title`、摘要与来源、`duration_seconds`、`app_ids`、可选 `series` |
| `map.json` | 地图结构 1、种子、生成器规则版本 1、节点与规范无向边 |

<a name="resource-engine-构建与版本"></a>
### 构建与版本

现行身份是目录格式 2、`RULES_VERSION = 2`、场景结构 8、世界结构 11、运行清单 4。
运行、检查点与恢复使用五字段 `environment_identity`：`environment_id`、`environment_version`、
`rules_version`、`catalog_content_sha256`、`map_sha256`。
规则文件只从引擎包读取，环境不发布副本，也不添加规则哈希；`code_sha256` 额外覆盖 `src/worldmock/data/` 下每个文件。
旧格式与旧结构号直接拒绝，不迁移、不双读。

新增 app、动作、内容与商品只需数据仓重新发布；新增类别、效果或规则须修改 WorldMock 并提升 `RULES_VERSION`。
地图结构和生成器规则版本都为 1；目录清单不列规则副本，也不带规则哈希。

<a name="resource-engine-目录发布的实现与验收"></a>
### 目录发布的实现与验收

`catalog.identity.refresh_manifest_identity` 安全读取实际文件，重算文件表与框架化目录摘要，经 `atomic_write` 写出规范 LF 清单。地图由 `worldmock environment generate-map` 发布；`--check` 只比较字节。
验收核对清单身份、原子发布失败保持旧目标，以及 `worldmock validate --environment` 的真实成功和逐类失败，不用声明摘要替代实际文件。

<a name="resource-engine-校验资源可用性"></a>
## 5. 校验资源可用性

<a name="resource-engine-文件身份校验"></a>
### 文件身份校验

清单的 `files` 恰好登记六个文件，各自绑定路径、sha256 和字节数，目录内容摘要沿用框架化算法。
严格模型拒绝清单额外字段；未登记文件、缺失必需文件、字节漂移与环境钉不一致分别以登记码拒绝。
包规则只从包资源读取，不比对环境副本；五字段环境身份与代码身份共同保证恢复一致性。

<a name="resource-engine-准入边界"></a>
### 准入边界

内容必须绑定恰好一个动作；动作可以不绑定 app。动作的 `app_ids` 非空时，`manner` 必须是一种固定使用方式，
每条内容的 `app_ids` 是该动作 app 集合的非空子集；动作没有 app 时，`manner` 为 null、内容 app 集合为空。
每个动作及每个动作与 app 的组合至少有一条内容。空集合从不表示全部 app。
内容 `duration_seconds` 是正整数且不小于 60 秒；动作不登记时长、时间窗、场所或效果。
内容不登记分类；环境维护动作名称、说明及其内容。
系列以 `(action_id, series.title)` 识别，各集具有相同动作与 app 集合，集数为唯一的正整数。

商品绑定店铺，店铺绑定购物 app。`GOODS_CATEGORY_STORE_KINDS` 固定类型可供给的类别，
`"all"` 展开到全部可供给该类别的店铺；显式绑定列表去重排序，同类型店铺同菜单。
购物用途存在当且仅当至少一家店铺绑定该 app；支付绑定非空当且仅当有购物用途，
绑定目标具有支付用途，允许绑定自己。导航与支付 app 可以没有内容；app 必须有用途或动作绑定。

`catalog.admission` 是唯一目录登记校验器；严格模型拒绝未知字段，图和跨对象引用一并校验。
场景工作分配另执行四个 `WORK_*` 检查，完整 32 个登记码及原因见[登记错误](../CONTRACTS.md#contracts-环境目录与绑定)。

<a name="resource-engine-地图关系与道路范围准入"></a>
### 图与地点关系准入

| 校验 | 失败行为 |
| --- | --- |
| 节点至少两个 | `MAP_TOO_SMALL` |
| 节点与地点集合相等且排序；边无自环、重复或未知端点，`a < b`，边长正整数、边排序 | `MAP_EDGE_INVALID` |
| 图连通 | `MAP_DISCONNECTED` |
| 生成检查和环境校验重生成字节一致 | `MAP_NOT_REGENERATED` |

登记加载只验证发布图结构，不重生成。场景地点必须存在于目录，最短路可经过未选择地点；不存在道路范围、许可或未知路线准入。
对象与绑定继续由统一目录登记验证，同类型食品店铺菜单一致，`"all"` 不展开到住宅，店铺 app 绑定只允许食品店铺。错误输出稳定码、英文原因和所属路径。
项目 `validate` 的覆盖率 0.8 门禁保留，输入覆盖不足不转成运行期默认内容。

<a name="resource-engine-准入失败的处理"></a>
### 准入失败的处理

`RegistrationError` 继承 `IntegrityError`，保留稳定登记码与英文原因，并记录错误日志。
不补造对象、不跳过失效绑定、不改读其他环境。错误与修复入口按[注册校验与错误](../dev/SPEC-action-and-content-simplification.md#spec-acs-注册校验与错误)判断。

<a name="resource-engine-资源校验的实现与验收"></a>
### 资源校验的实现与验收

目录严格模型、登记校验与文件身份分别在 `catalog.models`、`catalog.admission`、`catalog.identity` 实现。
`tests/catalog/` 使用真实人工环境制造反例，验证绑定、闭集、最小内容时长、供给类别、目录身份与发布命令。
纯加载测试通过不代表完整引擎执行或正式环境已验收。

<a name="resource-engine-检索与呈现"></a>
## 6. 检索与呈现

候选生成与呈现只处理 `product`、`lifestyle`、`task` 三种对象。资格、稳定排序、呈现上限、分页和轮转继续作用于这三种候选；商品目标来自当前店铺菜单与可经 app 外卖的食品，生活活动继续按地点类型准入。app 不进入候选、不轮转、不进 `callable_index`，而是在工具层始终列出；没有绑定内容的 app 仍列出，以 `APP_CONTENT_EMPTY` 标为不可用。

工具层的 `callable_index` 恰为 `lifestyle`、`goods`、`query_categories` 三键。索引只给人物按名称调用的范围，不计入候选呈现上限，也不改变活动受理资格。内容离开候选、检索分区、facet、游标和轮转管线；每条内容只要绑定至少一个 app 即可达。

<a name="resource-engine-地点发现与供给比较"></a>
### 地点发现与供给比较

`from` 只接受 `null`、登记店铺显示名或 `apps.json` 的 app 名，来源与购买共用同一名称解析；`null` 不额外限制，食品完整标签仍约束标签店铺。
到店必须人在店内，`app_id=null`；app 配到店以 `SUPPLY_SOURCE_MISMATCH` 拒绝。远程下单要求店铺绑定 app；没有任何 app 是 `FULFILLMENT_NOT_SUPPORTED`，所点 app 未绑定该店是 `APP_NOT_BOUND`，app 的店不卖该食品是 `SUPPLY_NOT_AT_STORE`。
`from=null` 且店铺绑定多个 app 时取最小 `app_id`，写进 `details.notes`。app 来源从目录绑定派生，不读取手机配置。

`query_world(name, way, category, from, available_only, offset)` 六键必填，来源解析为目录店铺或 app，返回固定菜单的紧凑 `{matches, total_count, next_offset}`。
商品价格固定，类别标签来自食品类别；`available_only` 只按人在店内或店铺绑定 app 判断，不检查报价、营业、库存、配送区域或可见凭据。查询不揭示新世界事实、不推进时间、不占名额，只保存实际模型与 tool 交换证据。
查询与世界请求不得混批，先整批校验，再按原执行位置返回；有效交互菜单、同秒预算、名称纠错与只读权限保持原合同。

发现按图最短距离，食品比较按固定单价、每单配送费与图上预计取得时间；购买条目身份 `<store_id>:<food_id>`，消费与多样性按食品身份。没有浏览揭示或可见凭据，提示未列出仍可按名购买；错误候选给完整可填写工具调用，不自动替换食品。

<a name="resource-engine-无结果与范围限制"></a>
### 无结果与范围限制

| 情况 | 处理方式 |
| --- | --- |
| 当前查询无匹配项 | 返回真实空结果和可用查询信息，不编造条目补齐 |
| 条目不在调用方给定的候选或读取范围内 | 不纳入本次结果，保留相应筛选依据；不自行作出活动拒绝，也不删除目录食品 |

交互菜单是模拟引擎的入口准入约束，不是资源候选的世界事实排除条件。资源引擎仍返回完整候选、排除
证据与目录事实；菜单可以让模拟引擎撤下某个下钻入口或启动资格，但不能让资源引擎删除条目、改写排除
原因计数或缩小 `affordance_contract`。活动类型名称引用现有可供性注册表的 `kinds`；注册表 `surface`
不构成全部生活工具名清单。边界见[交互菜单规格](../dev/SPEC-interaction-menu.md#spec-interaction-menu-特性规格交互菜单)。

合法店铺或 app 来源没有匹配食品时返回真实空页，不切换来源；地点比较只用图距离与到手价，不从无结果推断缺货或闭店。

<a name="resource-engine-检索呈现的实现与验收"></a>
### 检索呈现的实现与验收

商品、生活活动与任务保留生成、资格、排序、轮转管线；商品候选 `kind="product"` 的
`object_id="<store_id>:<food_id>"`，持有按食品身份。不同店铺条目不合并。商品目标来自当前店铺菜单和
可外卖食品；app 名称始终列出，没有绑定内容时仍列出并标为不可用。世界事实判断地点、在场、资源占用和时长，
没有库存、下架、口味、报价或营业筛选。画像和 `tastes` 只以散文进入人设层，不生成权重，不改变候选数值排序。

| 事项 | 承载与要求 |
| --- | --- |
| 固定菜单 | 目录食品绑定一次派生同类型菜单与店铺 app 索引，读取不改世界状态 |
| 查询与呈现 | 冻结观察的名称、来源与类别筛选；稳定顺序、完整分页与实际曝光轮转分别留证 |
| 比较 | 图最短距离、完整路径与到手价，数据来源不可伪装成真实价格和路网 |
| 验收 | 同输入同结果，分页无重无漏，固定菜单食品按名可调用，app 名称全部列出；娱乐管线的退役由其所属阶段验证 |


<a name="design-resource-engine-current-responsibility"></a>
## 基础类型与资源装配

`worldmock.base_models` 保留严格模型与深冻结基础；`worldmock.exchange.body` 固定身体常数与资源元信息。
`Catalog` 只装配 app、地点、商品、动作、内容与发布图，不读取语料或环境规则副本。
反向索引为 `menus_by_kind`、`goods_by_store`、`stores_by_app`、`apps_by_store`、`actions_by_app`、`contents_by_action`、`contents_by_action_app`、`contents_by_series`。

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
| `2026-10-06-windows-path-safety` | 2026-10-06 | 语料路径一句补 Windows 下的声明规则：无盘符的根路径算绝对路径，反斜杠与任一平台不能原样保存的名字一并拒绝，目录联接与符号链接同样拒绝；版本表去掉隔开末两行的空行 |
| `2026-10-06-parallel-activities-claims` | 2026-10-06 | 按[同时做两件事规格](../dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)阶段 2 同步：「构建与版本」补可供性占用声明在包资源 `src/worldmock/data/affordances.toml` 中重列，字节由 `tool_schema_sha256` 绑定，测试环境副本逐字节同步并刷新目录内容摘要，仍带旧占用名副本的外部发布环境在装配时被拒绝、要由 WorldMock-Dataset 按新占用名重新发布；品类契约与规则绑定 3、场景结构 6、地图结构 3、世界结构 9 不变；文件头增该规格修订行，版本升为 v1.15；版本表去掉隔开末三行的空行 |
| `2026-10-06-rrs-rules` | 2026-10-07 | 阶段 1：固定规则与 RULES_VERSION、七份环境目录、真实环境命令和五字段运行身份；旧品类合同归档，后续消费者按阶段闭合 |
| `2026-10-06-rrs-map` | 2026-10-07 | 随机连通加权图、最短距离出行与环境恢复身份；对应正文按资源与规则引擎简化规格地图阶段修订 |
| `2026-10-06-rrs-store` | 2026-10-07 | 食品、店铺 app 来源、固定价格与单订单结算；类别消费、六个输入警告和目录覆盖率，删除本阶段退役正文 |
| `2026-10-06-rrs-app` | 2026-10-07 | app 与 app 内容引用、内容可达性和三键可调用索引；内容退出候选、facet、分段读取与资产揭示管线；版本保持 v1.16 |
| `2026-10-07-acs-rules` | 2026-10-07 | 固定规则版本 2；目录格式 2、场景结构 8、世界结构 11、运行清单 4；环境仅发布目录与场景，规则只从包读取。 |
| `2026-10-07-acs-receipt` | 2026-10-07 | 付款与收货分别留证，收货由唯一函数恰好结算一次，效果与证据归收货人；个人事务七键，用餐目标按本人 meal 收货推进。 |
