<a name="spec-map-and-storefront-地图数据集合与门店供给开发规格"></a>
# 地图数据集合与门店供给开发规格

> 引用出处：`tests/life/test_map_dataset.py::test_old_map_structure_version_is_rejected` @ `55e6a0f4c6ed6a015e334b01513767eefc227880`

> 按[资源与规则引擎简化规格](SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订「裁决原文的定义出处」（阶段 1）；该出处只定位原始定义，不证明历史执行；「产品行为与边界」、「冻结输入与身份」、「记录、恢复与交付」（阶段 2）；「按地点类别生成完整供给、冻结价格范围与门店差价、地点发现与候选呈现、整单购买与履约、按名调用与指名购买、统一名称解析、指名购买：`from` 是约束，不是语法、购买渠道：App 名解析为成员门店、名称类拒绝不计同刻轮数、更简单的购买请求（裁决 S10）、地点供给和价格、类别准入与逐店商品集合、范围内定价、验收台账、文件修改范围、版本表、营业、报价与访问条件」（阶段 3）。

> 引用出处：`src/worldmock/life/scenario.py::LifeScenario.phone_simulation_config` @ `bc42d02496fb13d4889363cfd10c3d223176454c`

> 状态：冻结特性合同；图与菜单按资源与规则引擎简化规格分阶段实现与验收。旧独立地图分支与品类主线组合的离线结果见 [主线组合验收](research/map-and-storefront-main-verification.md)，原 [独立分支验收](research/map-and-storefront-verification.md) 保留历史身份；项目级三项真实验收未完成。「地点发现与候选呈现」「记录、恢复与交付」「验收台账」中关于发现、暂停活动恢复与提示供给行的条款按 [时间与能量规格](SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动)「冻结条款修订」第 25 行修订，已随该规格实现并通过离线验收（证据见[时间与能量验收记录](../archive/time-and-energy/time-and-energy-verification.md)）；修订记录见「版本表」。
> 「地点发现与候选呈现」「整单购买与履约」「记录、恢复与交付」「验收台账」中关于购买资格、来源标记、到店餐食与供给去重的条款按[人物 Prompt 规格](SPEC-life-realism.md#persona-prompt-七层请求)修订，修订部分已实现并通过离线验收（证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md)）；修订记录见「版本表」。
> 「产品行为与边界」「冻结输入与身份」「地点发现与候选呈现」「整单购买与履约」「记录、恢复与交付」「验收台账」中关于挑店、同名地点与加载校验、配送路线状态、指名购买、可见期与浏览结果的条款按[一日运行保真规格](../VERIFICATION.md#verification-incremental-evidence)修订（一日运行保真 MS1）；修订部分已实现并通过离线验收，改动的台账行「地图唯一来源」「人物选择边界」「供给候选身份」「自提与配送」四行已改记“通过”。
> 按[同时做两件事规格](SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订「冻结输入与身份」：仍带旧占用名的已发布环境在加载时拒绝（阶段 2）。
> 日期：2026-10-07
> 「地点发现与候选呈现」「验收台账」中关于 `query_world` 六键、来源与可执行筛选的条款按[个人事务与工作安排规格](../VERIFICATION.md#verification-incremental-evidence)修订（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 MS1–MS4、MS11）；修订部分已实现并通过离线验收（证据见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)）；购买来源写法一处属更正既有表述。
> 授权：本次任务要求审查提案、修订最终规格并完整实现。本文采用提案的建议产品范围，并纠正已失效的现状与业界判断。
> 取代：[地图与门店供给提案](PROPOSAL-map-and-storefront.md)中待裁决的数据结构与实现范围。

当前环境归属按[环境数据归属规格](SPEC-environment-data-ownership.md)执行：具体 `corpora/`、`catalog/`、`scenarios/`
及制作工具由 WorldMock-Dataset 维护，正式环境为 `environment/zhangjiang-baseline/`；通用活动、动机与人物映射
由 WorldMock 包资源提供，环境冻结副本须逐字节一致。当前场景结构为 `7`，单人物文件为 `single-person.json`，环境身份由发布清单绑定。
本文中的新环境放行须另核对本次收据，历史验收数字不证明当前组合已经通过。

<a name="spec-map-and-storefront-产品行为与边界"></a>
## 产品行为与边界

人物看到店铺和食品，按路程、出行成本与履约方式选择，再付款、取得与消费。记录必须能解释人物当时看到了什么、为何选择这家店，以及实际如何到达或收货。

```mermaid
flowchart LR
    D[环境目录中的地点、食品与绑定] --> G[WorldMock 生成连通加权图]
    G --> P[Dataset 发布图与清单身份]
    P --> O[人物比较地点与共享菜单]
    O --> A[选择店铺与履约方式]
    A --> T[图最短路上的出行或配送]
    T --> F[记录付款、取得与消费证据]
```

地点名称与分店标签来自具体环境，距离、速度、费用与食品数值是合成模拟输入。图不表达真实道路、GPS、交通拥堵或路线几何。
同一食品店铺类型共享完全相同的菜单，食品价格为全店同价；`"all"` 只展开到食品店铺类型，住宅不出售食品。店铺全天服务，不保存营业日历、资格、报价与库存。
WorldMock 拥有生成器、固定规则和运行行为，Dataset 拥有对象、绑定与发布字节。生成或冻结规格本身不证明真实人物运行已经通过。

<a name="spec-map-and-storefront-冻结输入与身份"></a>
## 冻结输入与身份

| 输入 | 冻结合同 |
| --- | --- |
| 环境目录 | `manifest.json` 登记七个 JSON 文件、包资源副本和 `rules_version`，文件字节与摘要一致 |
| 地点 | 每行恰为 `location_id`、`name`、`branch_label`、`kind`、`app_ids`；八个固定地点类型，不含坐标、营业或资格字段 |
| 图 | `map.json` 结构 `1`，含整数种子、生成规则版本 `1`、排序节点和规范边；边长为正整数米，图连通 |
| 食品与菜单 | 食品定义价格与类别，`store_ids` 明确绑定店铺或写 `"all"`；同种店铺类型菜单相同，店铺 app 绑定只允许食品店铺 |
| 场景 | 结构 `7`，`environment` 钉清单，`locations[]` 只选择地点身份 |
| 运行与检查点 | 世界结构 `10`、运行清单 `3`，使用同一五字段 `environment_identity` |

`environment_identity` 包含 `environment_id`、`environment_version`、`rules_version`、`catalog_content_sha256`、`map_sha256`。目录内容变化发布新环境版本，规则变化升包 `RULES_VERSION`；不能覆盖已发布版本的内容。
图由 WorldMock 根据全目录地点与种子生成，Dataset 保存规范 JSON 与 LF 字节并发布。运行只读图；生成检查和环境校验另重生成比对。
加载拒绝少于两个节点、非法边与不连通图，分别报 `MAP_TOO_SMALL`、`MAP_EDGE_INVALID`、`MAP_DISCONNECTED`；重生成失配报 `MAP_NOT_REGENERATED`。没有道路资料未知或不可达状态。
场景选择不裁切图，最短路可经过其他目录节点；Dijkstra 等长时以完整路径元组字典序决胜，不依赖边插入次序。

<a name="spec-map-and-storefront-按地点类别生成完整供给"></a>
## 按地点类别生成完整供给

食品在 `foods.json` 定义 `food_id`、名称、`category`、固定 `price_fen` 与店铺绑定。类别只有 `snack`、`drink`、`meal`，同一食品全店同价，同类型食品店铺菜单完全相同。
`store_ids="all"` 只展开到食品店铺类型，住宅不出售食品；目录一次派生 `foods_by_store` 与店铺 app 索引。没有报价、价位、抖动、库存、营业或资格。

菜单不是逐店抽样：一种店铺类型的食品绑定必须全有或全无，部分绑定由登记拒绝；没有丰富度阈值或每店价格定位。

<a name="spec-map-and-storefront-冻结价格范围与门店差价"></a>
## 固定食品价格

食品只定义一个正整数 `price_fen`，单位为每份分，全店同价。没有上下界、门店差价、价格位置、抖动、报价版本或运行期调价。
外卖到手价为食品固定金额加每单 `500` 分，比较与余额检查使用同一金额；购买按实际份数结算，不引入品类配对系数。

<a name="spec-map-and-storefront-营业报价与访问条件"></a>
## 营业、报价与访问条件



<a name="spec-map-and-storefront-地点发现与候选呈现"></a>
## 地点发现与候选呈现

商品、生活活动与任务保留生成、资格、排序、轮转管线；商品候选 `kind="product"` 的 `object_id="<store_id>:<food_id>"`，持有按食品身份。不同店铺条目不合并。
商品目标来自当前店铺菜单和可外卖食品；app 名称始终列出。世界事实判断地点、在场、资源占用和时长，没有库存、下架、口味、报价或营业筛选。
画像和 `tastes` 只以散文进入人设层，不生成权重，不改变候选数值排序；内容娱乐消费者的迁移由所属 app 阶段完成。

`query_world(name, way, category, from, available_only, offset)` 六键必填，来源解析为目录店铺或 app，返回固定菜单的紧凑 `{matches, total_count, next_offset}`。
商品价格固定，类别标签来自食品类别；`available_only` 只按人在店内或店铺绑定 app 判断，不检查报价、营业、库存、配送区域或可见凭据。查询不揭示新世界事实、不推进时间、不占名额，只保存实际模型与 tool 交换证据。
查询与世界请求不得混批，先整批校验，再按原执行位置返回；有效交互菜单、同秒预算、名称纠错与只读权限保持原合同。

食品目标呈现完整标签「<店铺>出售的<食品>」，动作到店在场才可用；有 app 绑定才列远程下单。价格为到手价，说明写实际预计送达或 app 名，不显示报价版本或库存。

<a name="spec-map-and-storefront-整单购买与履约"></a>
## 整单购买与履约

`from` 只接受 `null`、登记店铺显示名或 `apps.json` 的 app 名，来源与购买共用同一名称解析；`null` 不额外限制，食品完整标签仍约束标签店铺。
到店必须人在店内，`app_id=null`；app 配到店以 `SUPPLY_SOURCE_MISMATCH` 拒绝。远程下单要求店铺绑定 app；没有任何 app 是 `FULFILLMENT_NOT_SUPPORTED`，所点 app 未绑定该店是 `APP_NOT_BOUND`，app 的店不卖该食品是 `SUPPLY_NOT_AT_STORE`。
`from=null` 且店铺绑定多个 app 时取最小 `app_id`，写进 `details.notes`。app 来源从目录绑定派生，不读取手机配置。

订单行以 `(store_id, food_id)` 标识，重复项以 `ORDER_DUPLICATE_ITEM` 拒绝；同单同店、同卖方、同履约与同收货地点。每单仅一笔人物到 `merchant:<store_id>` 的转账，金额为固定食品单价乘份数再加一次配送费。
到店即时交付；外卖每单配送费固定 `500` 分，预计时刻为 `now + 600 + ceil(最短距离 / 5.0)`。每单仅一个 `delivery_arrival`，重复结算不重复交付。
本人收货地点是当前停留地点，路途中自送或与出发同请求自送以 `ACTIVITY_PRECONDITION_UNMET` 拒绝，`unmet=delivery_destination`；给他人按真实收货地点。人物离开后仍送到订单地点，食品归收货人在当地持有。

校验先于整单扣款和订单写入，任一项拒绝不产生购买副作用；付款与取得分开留证，给予只转移食品持有，不增加摄入。

<a name="spec-map-and-storefront-记录恢复与交付"></a>
## 记录、恢复与交付

记录保存人物实际收到的地点、菜单与出行选择，原始观察和动作结果保留当时输入，不能用当前目录重建历史。
出行活动保存 `trip_id`、`path`、`distance_meters`，付款保存 `travel_payment{trip_id, fare_fen, …}`，到达保存 `arrival{location_id, access_status}`；事件与公开路线证据使用同一 `trip_id`。
图只给地点、路径与距离，记录不生成坐标或轨迹。查看器按运行内名称索引显示起点、终点与路径地点。

运行清单和检查点绑定五字段 `environment_identity`；共同检查点封存订单、待配送、持有、呈现状态、内容进度和会话推送。恢复、打开与反事实分支在副作用前核对身份，差异拒绝，分支不改父运行。
世界结构 `10` 不保存供给库存、报价、营业例外、资格或旧可见供给字段，旧世界与旧清单直接拒绝。记录结构和后续食品、app、导出消费者的交付按资源与规则引擎简化规格的所属阶段验收，不能以文档修订代替运行结果。
认证导出的冻结运行清单保留实际环境身份，交付链沿用固定截止、来源索引与原始证据认证。

<a name="dayrun-fidelity-按名调用与指名购买"></a>
## 按名调用与指名购买

名称解析、指名购买与请求级 `prefer` 的机制保持；实际对象改为登记食品、店铺和 app。模型只提交可读名称与方式，固定价格、图上 ETA 与内部身份由环境计算。
购买来源来自 `apps.json` 与地点 app 绑定，订单与 `DeliveryState` 都保存实际 `app_id`，没有手机配置或供给报价。

<a name="dayrun-fidelity-按名调用与指名购买-统一名称解析"></a>
### 统一名称解析

**规则。** 所有目录类对象（食品与供给、生活活动、内容、游戏、地点、联系人与人物、任务、活动）都可以直接写名称。
无损归一后唯一就受理；相似度只用来给候选，永不自动替换（人物 Prompt 规格裁决 #19）；候选按「此刻可行」过滤并写成可以照填的请求；
宿主逐项收集全部失败项，环境逐项解析后合并成一次拒绝。

**历史证据（2026-09）。** 名称检查分散在约 9 处：宿主 `custom/agents/life_agent.py`，路由 `src/worldmock/life/routing.py`（宿主 `custom/agents/life_agent.py` 与环境 `src/worldmock/life/session_actions.py` 共用），
交互菜单 `src/worldmock/life/interaction_menu.py`，环境 `src/worldmock/life/session_actions.py`（`_offered_selection` 找不到即裸 `ACTION_REQUEST_INVALID`，`_named_selection` 只管指名购买），
各处历史定位见[改造前固定提交原文](https://github.com/HoldThatThgt/WorldMock/blob/2cf230115f4084aef2376e9d77bc976efbed43ea/docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要)。每一处都是逐字匹配、遇到第一个错误就停（`routing.combination_error`，`src/worldmock/life/routing.py`，算出了全部逐项错误却只返回一个）；
宿主报错是英文；环境拒绝虽有中文 `reason`，`message` 仍是英文，除 `SUPPLY_*` 两码外都不给候选。只有食品有指名购买，且只在 `obtain` 且方式恰好是「到店」或「远程下单」时放行
（`custom/agents/life_agent.py`、`src/worldmock/life/interaction_menu.py`）。名称与组合类协议错误 WC 26 次、BIZ 216 次。

**设计。** 复用纯函数模块 `src/worldmock/life/name_resolution.py`，宿主与环境共用（与 `routing.py` 同一模式）：

1. **精确匹配。**
2. **无损归一**，结果唯一就受理，并写进 `details.resolved`（形如 `[{"written": "沈昊", "as": "联系人：沈昊"}]`）：
   - 统一全半角与括号；
   - 按方式推导的内部动词增补或去掉「地点：」「联系人：」「活动：」「内容：」「持有物：」「工作：」等前缀；
   - 去掉活动名里的「（9月28日 08:25 起）」（使用 `src/worldmock/life/object_labels.py::START_SUFFIX`）和邮件时间戳；
   - 比对时忽略「（模拟）」；
   - 「家」「住处」→ `home_location_id`（现行提示里家只以「地点：<场景名称>」出现，没有「家」的写法）；不新增地点别名字段：场景不复制地点名称
     （接口合同「冻结地图与场景引用」:418-420、地图与门店规格 :41），其余写法交给候选；
   - 每个现有方式只属于一个内部动词（`WAYS`，`src/worldmock/life/vocabulary.py::WAYS`）；由方式唯一推导动词，人物请求不再带 `actions`。不适用于目标的方式报 `WAY_NOT_OFFERED`，完整候选只含新请求字段；
   - 现有 `WAY_SYNONYMS` 仅是既有方式闭集的名称识别合同（登记进接口合同「请求模型的闭集与字面边界」），不新增旧接口或偏好兼容写法，只有在目标列出的方式里恰好命中一个时才受理；
   - 名称归一只识别现有名称；来源与去向严格按方式判定，食用的两端须为 `null`，不接受「自己」等固定值的兼容写法。
3. **相似度永不自动替换**：先按方式确定对象类别，在同类中计算单字加二字的 Dice 相似度，由 `src/worldmock/life/name_resolution.py::near_candidates` 生成带可行性过滤的候选；
   多字名称须至少共用一个二字词，单字名称只接受同类相同单字；可行的在前，至多 3 个候选；每个候选写成完整的 `{tool, arguments, note}`（`note` 为英文说明或 `null`，例如 `Estimated duration: 20 minutes`）。
4. **分工**：私有绑定和已冻结的地点表由宿主判定，重试不花钱（宿主协议错误不进引擎）；存在但没列出的对象（未呈现的供给、生活活动、内容，以及没有路线的地点）由环境判定。
   按方式唯一推导的动词与对象类别由 `src/worldmock/life/name_resolution.py::named_kind` 判定。宿主
   `custom/agents/life_agent.py::LifeAgent._prepared_action_error` 与环境
   `src/worldmock/life/session_actions.py::ActionHandlers._start_outcome` 共用
   `src/worldmock/life/name_resolution.py::resolve_start` 校验名称；未呈现对象由
   `src/worldmock/life/session_actions.py::ActionHandlers._named_selection` 解析。
   `src/worldmock/life/storefront_runtime.py::StorefrontRuntime._purchase` 保留 `named` 标志，指名购买跳过可见凭据。
5. **报告全部失败项**：宿主逐项收集；环境逐项解析后合并成一次拒绝（第四章的 `items[]`）。接口合同 :226「多条不成立时报第一条」改为「逐项报告全部参数与名称问题；业务前置条件中所有可独立确定的问题按既定准入顺序列出并定位参数」。
6. **同刻预算**：见 3.4。
7. **性质测试**：宿主判为可受理的，环境在同一冻结观察下也必须受理（`tests/life/test_name_resolution.py`）。

**预估**（在两次运行的真实失败项上离线模拟）：WC 约 55% 自动受理、10% 交给环境、35% 需要候选；BIZ 约 38% / 33% / 22%（22% 里约 8% 是
「只有一个方式的目标写了自由文本方式」，只能给候选）。不可达的地点和不存在的门店归一解决不了，只能变成更清楚的拒绝，根本修复在数据集方案。

**规格与合同。** 系统层第 12 条改为「名称照工具层、待办层中的写法填写；食品、生活活动、内容、地点都可以直接写名称，即使此刻没有展示；写得不准时会返回最接近的候选，不会替你改」。
「其余动词的目标须是本轮列出的名称，不是时由宿主按协议错误纠正」在冻结文档里有十处镜像（接口合同 :55-57、:226-228、:287-301、:691；人物 Prompt 规格 :456 与修订 #24、裁决 #26；
交互菜单规格 :108、:123；life-runtime :729；决策上下文规格 :61；时间与能量规格 :508、:1056；数字人交互规格 :246；人物规格 :342-343），统一改为
「宿主归一后唯一即受理，否则按第四章给候选」。数字人交互规格 :196「同名对象由自然语义消歧」不变。

来源候选由目录店铺显示名与 app 名构造，解析只做无损归一与唯一匹配，不按相似度自动替换。

<a name="dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法"></a>
### 指名购买：`from` 是约束，不是语法

M0 按食品名或完整店铺标签匹配；M1 以 `from` 限定登记店铺或 app 的成员店铺；M2 核对到店、远程下单或由「购买」派生的方式；M3 核对在场或店铺 app 绑定。
`from` 只接受 `null`、登记店铺显示名或 `apps.json` 的 app 名，来源与购买共用同一名称解析；`null` 不额外限制，食品完整标签仍约束标签店铺。
到店必须人在店内，`app_id=null`；app 配到店以 `SUPPLY_SOURCE_MISMATCH` 拒绝。远程下单要求店铺绑定 app；没有任何 app 是 `FULFILLMENT_NOT_SUPPORTED`，所点 app 未绑定该店是 `APP_NOT_BOUND`，app 的店不卖该食品是 `SUPPLY_NOT_AT_STORE`。
`from=null` 且店铺绑定多个 app 时取最小 `app_id`，写进 `details.notes`。app 来源从目录绑定派生，不读取手机配置。
名称、来源、履约与前置条件分段拒绝，候选使用可直接提交的食品请求，不替换所点食品。

<a name="dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店"></a>
### 购买渠道：App 名解析为成员门店

`from` 只接受 `null`、登记店铺显示名或 `apps.json` 的 app 名，来源与购买共用同一名称解析；`null` 不额外限制，食品完整标签仍约束标签店铺。
到店必须人在店内，`app_id=null`；app 配到店以 `SUPPLY_SOURCE_MISMATCH` 拒绝。远程下单要求店铺绑定 app；没有任何 app 是 `FULFILLMENT_NOT_SUPPORTED`，所点 app 未绑定该店是 `APP_NOT_BOUND`，app 的店不卖该食品是 `SUPPLY_NOT_AT_STORE`。
`from=null` 且店铺绑定多个 app 时取最小 `app_id`，写进 `details.notes`。app 来源从目录绑定派生，不读取手机配置。

`resolve_source` 从 `catalog.apps` 与 `stores_by_app` 构造 app 名、别名键和成员店铺；结果私有绑定使用 `app_id`，订单、送达状态与购买证据保存同一 app 身份。原手机配置裁决的来源定位保留，现行 app 来源合同由目录取代。

<a name="dayrun-fidelity-按名调用与指名购买-名称类拒绝不计同刻轮数"></a>
### 名称类拒绝不计同刻轮数

**规则**（裁决 D7）。名称类拒绝码不计同刻轮数，不施加 `service_refused` 情绪脉冲，每人每秒至多 2 次；第 3 次起照常计轮、照常施加脉冲。
名称类拒绝码闭集：宿主的 `NAME_NOT_FOUND`、`NAME_AMBIGUOUS`、`WAY_NOT_OFFERED`、`SUPPLY_SOURCE_MISMATCH`、`SUBJECT_NOT_KNOWN`；
环境的 `SUPPLY_NOT_FOUND`、`SUPPLY_NOT_AT_STORE`、`FULFILLMENT_NOT_SUPPORTED`、`SOURCE_NOT_FOUND`、`TASK_NOT_FOUND`。
「每秒 2 次」是实验假设，登记进 `docs/EXPERIMENT.md#experiment-worldmock-实验设置`。

**历史现状（2026-09）。** 每个 `BusinessRuleError` 都进 `_after_rejection`（[改造前固定提交原文](https://github.com/HoldThatThgt/WorldMock/blob/2cf230115f4084aef2376e9d77bc976efbed43ea/docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要)；当前归属 `src/worldmock/life/decision_coordinator.py::DecisionCoordinator._after_rejection`）：`count_round` 计一轮，`refusal_pulse_key`（`src/worldmock/life/same_moment.py::refusal_pulse_key`）
对除 `WORK_REQUIRES_REWORK` 与两个品类配对码以外的所有码给 `SERVICE_REFUSED`（同一秒至多一次）；宿主协议错误不进引擎，只耗回合。
同刻上限缺省 3（`src/worldmock/life/scenario.py::LifeScenario.max_same_moment_decisions`）加 3 轮受限轮（`src/worldmock/life/same_moment.py::RESTRICTED_ROUNDS`），当时受理、拒绝与重放都计轮（当前计数函数 `src/worldmock/life/same_moment.py::count_round`），受限轮关闭购买（`src/worldmock/life/same_moment.py::RESTRICTED_EXCLUDED_ACTIONS`）。
WC 18:15 的第 3 轮一用完，购买就在受限轮被关闭，原因写得再好也已经买不了了。改动点还包括 `latest_rejection_code` 与 `exhausted_schedule`（`src/worldmock/life/engine.py`）读最近拒绝码的逻辑。
3.2 受理 #882 之后这一次耗尽本不会发生；本条是对其余名称错误（BIZ 216 次）的兜底。

**冻结条款。** 接口合同「动作状态与动作结果」:212 与 :342、人物 Prompt 规格 :326（链长上界）与 :475、时间与能量规格第一章规则 3（:116-118）与裁决 #2、
数字人交互规格 :328 的「同刻轮数照旧计入成功、拒绝与重放」补「名称类拒绝不计，每秒至多 2 次」；life-runtime :346 与时间与能量规格「拒绝的情绪代价」:206 的
「业务拒绝 −0.05」补「名称类拒绝不施加」。

**验收。** 同一秒两次名称类拒绝后同刻轮数不变、情绪不变；第三次照常计轮；宿主与环境的名称类拒绝口径一致。

食品名称与来源使用目录食品、店铺和 app 名；名称类拒绝仍按既有预算机制处理，app 绑定或路途中自送拒绝是领域前置条件，不伪装为名称纠错。

<a name="dayrun-fidelity-按名调用与指名购买-更简单的购买请求裁决-s10"></a>
### 更简单的购买请求（裁决 S10）

用户提出、冻结时采纳（裁决 S10）：人物只写 `obtain`、商品名、`to` 与偏好（最快或最便宜），系统在全部门店里找可得的供给，按偏好挑一家成交；找不到就报错并给最接近的候选。本文的评估与定稿：

- **不再必须写的**：商品的完整标签「<门店>出售的<商品>」、`from`（改为可选约束）、`obtain` 的方式选择（写「购买」时到店、远程下单由系统按人物所在、收货人与可配送性决定：人在店里、东西在那里交得到收货人手上就到店，
  否则能送到收货地就远程下单，都不行就拒绝，`next_step` 写能到店买的最近门店，送给别人时写先送到自己这里再当面给）、浏览作为购买前提（本来就不是）。这些写法仍然有效，处理见下面的「收敛」。
- **保留的**：`to`（收货人）、指名不替换（找不到只给最接近的候选，裁决 #19）、结算与送达规则（备货加路线、当前报价、库存、余额）、渠道称呼作为可选 `from`、`制作` 方式。
- **新增的**：请求级 `prefer`（`最快拿到` | `到手价最低`，必填可空，购买时不接受空值），方式闭集增「购买」（表示由系统决定到店或远程下单）；`start_activity` 删除 `actions` 和旧平行数组，使用 `items[{target, way, from, to}]` 一至两项及请求级 `answering`、`prefer`。
- **真实性**：人物仍决定买什么、给谁、多急；「在哪家店买」交给偏好，与用 App 排序下单的日常一致；数据集的手机轨迹仍由成交门店的卖家→App 映射合成。失去的是人物主动挑店的情节，本次两份运行里没有出现过有效的挑店。
- **与本章的关系**：3.2 的分段匹配保留为错误报告的骨架（没有 `from` 与方式时 M1、M2 两段跳过），3.3 的「最近的一家」就是 `prefer=最快拿到` 的定义，`到手价最低` 按到手价；3.1 的名称归一与候选照用。
- **冻结条款**：改写地图与门店规格 :11「不替人物选择」、接口合同 :321、人物 Prompt 规格「来源与去向」表与接口合同「请求模型的闭集与字面边界」的 `obtain` 行、方式闭集与 `tool_schema_sha256`。
- **采纳**（裁决 S10）：本次名称与来源类错误里，写错标签、写错 `from`、方式与来源不配的那几类在结构上消失，规则比 3.2（b）（c）与 3.3 的解析规则少；3.2、3.3 按下面的「收敛」执行。另一选项（维持显式 `from`、方式与标签）记在「裁决记录」。

**收敛**（冻结时对「采纳后 3.2、3.3 按本节收敛」的定稿，实现照此执行）：

1. **请求形状。** `start_activity` 增一个请求级字段 `prefer`：取 `最快拿到` 或 `到手价最低`，严格模式下必填可空，购买时必须明确选择，非购买时必须 `null`。当前接口为 `items[{target, way, from, to}]` 一至两项与请求级 `answering`、`prefer`；含购买项必须选明确中文偏好，无购买项必须 `null`，`null` 不默认最快。删除旧平行数组、`actions` 和英文偏好，不读旧请求。
2. **方式「购买」。** 方式闭集增「购买」，属于 `obtain`，表示由系统定方式（在 `from` 限定的来源范围内判断），且不定一个交不到收货人手上的方式：人在出售该商品、支持到店的门店里，
   东西在那里交得到收货人手上（收货人是自己，或此刻也在那家店里：同世界人物在店里、联系人的收货地点就是那家店，与给予的同处规则一致）就到店；否则能送到收货地就远程下单。
   两者都不行以 `ACTIVITY_PRECONDITION_UNMET` 拒绝：送给自己而范围内有能到店买的出售门店时，`next_step` 写从当前地点出发路线最短的那家能到店买的出售门店（只能远程下单的门店去了也买不到，不写）；
   范围内的出售门店都只能远程下单时，与远程下单的拒绝同一说法，下一步只指向能送达且从此地可达的地点；送给别人时不指向门店，`next_step` 为英文 `Deliver the item to yourself first, then give it in person.`。
   「到店」「远程下单」仍可显式写，按所写方式处理。「购买」对任何商品目标都受理，不论目标是本轮列出的还是按名写的；工具层的呈现不变（商品目标的动作不增加「购买」）。
3. **`from` 是可选约束。** 取 `null`（全部门店）、地点显示名（该地点，含「家」「住处」）或渠道称呼（该渠道的成员门店，按 3.3 解析）；其他写法报 `SOURCE_NOT_FOUND`。
4. **分段匹配仍是错误骨架。** 3.2（c）的 M0–M3 照旧依次判定、各报各的码；`from` 为 `null` 时跳过 M1，方式为「购买」时跳过 M2；M3 的「能服务」对「购买」取第 2 条两种方式之一。
5. **多于一个可服务的匹配时按 `prefer` 选定。** `最快拿到`：远程下单取预计送达最早者（最慢备货加出发时刻的计划驾车时长），到店取从当前地点出发路线最短者；`到手价最低`：取到手价最低者；
   并列时先比到手价，再比门店标识。所选门店写进 `details.notes`。这是 3.3「最近的一家」的推广，不论 `from` 是 `null`、门店名还是渠道称呼；因此商品购买不再以 `SUPPLY_NAME_AMBIGUOUS` 拒绝，
   3.3 渠道与3.2分段匹配均采用此处的prefer选店规则；旧歧义规则直接删除。

**验收。** `prefer` 两个取值各一例，含并列时先比到手价、再比门店标识；「购买」在出售门店里到店、不在店里而能送到时远程下单、两者都不行时报 `ACTIVITY_PRECONDITION_UNMET` 且 `next_step` 为路线最短的能到店买的出售门店，各一例；
出售门店都只能远程下单时与远程下单的拒绝同一说法，收货人不在店里时不定为到店、都不行时 `next_step` 为先送到自己这里再当面给，各一例；
「购买」对本轮列出与按名写的商品目标都受理，工具层动作不含「购买」；`from` 为 `null`、门店名、渠道称呼与其他写法各一例（最后一种报 `SOURCE_NOT_FOUND`）；
多家可服务时按 `prefer` 选定并写进 `details.notes`，不报 `SUPPLY_NAME_AMBIGUOUS`；方式为「购买」时 M2 跳过、`from` 为 `null` 时 M1 跳过，其余各段照常报码。

资源与规则引擎简化规格保留上述请求机制，取代旧手机配置、报价与配送范围来源：app 从目录绑定解析，食品固定价格，最快拿到按外卖 ETA 或到店图上最短距离；并列按到手价再按 `store_id`。
实际订单与 `DeliveryState` 带 `app_id`；没有绑定多个 app 的默认随机选择，`from=null` 时取最小 app 身份并写 `details.notes`。

<a name="tool-interaction-地点供给和价格"></a>
## 地点供给和价格

食品在 `foods.json` 定义 `food_id`、名称、`category`、固定 `price_fen` 与店铺绑定。类别只有 `snack`、`drink`、`meal`，同一食品全店同价，同类型食品店铺菜单完全相同。
`store_ids="all"` 只展开到食品店铺类型，住宅不出售食品；目录一次派生 `foods_by_store` 与店铺 app 索引。没有报价、价位、抖动、库存、营业或资格。

`from` 只接受 `null`、登记店铺显示名或 `apps.json` 的 app 名，来源与购买共用同一名称解析；`null` 不额外限制，食品完整标签仍约束标签店铺。
到店必须人在店内，`app_id=null`；app 配到店以 `SUPPLY_SOURCE_MISMATCH` 拒绝。远程下单要求店铺绑定 app；没有任何 app 是 `FULFILLMENT_NOT_SUPPORTED`，所点 app 未绑定该店是 `APP_NOT_BOUND`，app 的店不卖该食品是 `SUPPLY_NOT_AT_STORE`。
`from=null` 且店铺绑定多个 app 时取最小 `app_id`，写进 `details.notes`。app 来源从目录绑定派生，不读取手机配置。

<a name="tool-interaction-地点供给和价格-类别准入与逐店商品集合"></a>
### 按类型共享菜单

食品在 `foods.json` 定义 `food_id`、名称、`category`、固定 `price_fen` 与店铺绑定。类别只有 `snack`、`drink`、`meal`，同一食品全店同价，同类型食品店铺菜单完全相同。
`store_ids="all"` 只展开到食品店铺类型，住宅不出售食品；目录一次派生 `foods_by_store` 与店铺 app 索引。没有报价、价位、抖动、库存、营业或资格。

类型菜单由显式绑定完整派生，运行不抽样、不补齐；旧逐店类别准入池与构建供给机制退役。

<a name="tool-interaction-地点供给和价格-范围内定价"></a>
### 固定价格与配送费

每个食品只有 `price_fen`，全店同价。没有种子、价格范围、店铺价格位置、随机抖动、Quote 或后续调价；规则版本绑定固定配送费与购买算子。
金额按固定食品单价和实际份数求值，货款与每单配送费一次转入商家账户；比较和付款使用同一口径。

<a name="spec-map-and-storefront-验收台账"></a>
## 验收台账

当前行为以资源与规则引擎简化规格的菜单与食品合同为准。历史独立地图与主线组合的执行记录保留原源码身份，不覆盖本次新目录或消费链。

| 行为 | 当前验收要求 | 实际结果边界 |
| --- | --- | --- |
| 类型菜单与固定价格 | 同类同菜单，食品全店同价，`"all"` 不含住宅 | 目录纯节点和本阶段食品节点分别留证 |
| 店铺与 app 来源 | null、店铺或 app 名；app 必须真实绑定，完整标签不放宽来源 | 名称与真实消费者节点留证 |
| 单订单结算 | 一笔扣款、同店同履约、固定配送费、重复食品拒绝 | 真实账本和订单节点留证 |
| 配送与持有 | 一个到期事件，重复不增持有；离开后仍送原地点；路途中自送拒绝 | 真实订单、时钟与状态继续节点留证 |
| 食品消费 | 类别热量、份量守恒、效率来自 BODY_CONSTANTS；事件使用 food_id | 真实消费与事件节点留证 |
| 完整集成 | 从 LifeEngine 创建到检查点恢复 | 共同集成波次闭合前不宣称通过 |

旧营业、报价、库存、供给可见期、制作和手机配置条款被取代，原执行事实仍见所属历史验收记录；不继承旧测试数字。

<a name="spec-map-and-storefront-文件修改范围"></a>
## 文件修改范围

| 职责 | 当前模块范围 |
| --- | --- |
| 目录与菜单 | `src/worldmock/catalog/` 的统一登记、食品与店铺 app 索引 |
| 固定价格、热量与配送 | `src/worldmock/exchange/` 的常数和 Decimal 算子 |
| 名称、来源与受理 | `src/worldmock/life/` 的名称解析、食品购买、订单与前置条件 |
| 消费与身体 | 逐地点食品持有、消费活动、实际区间结算与 BODY_CONSTANTS 效率 |
| 证据与验证 | 原始订单、消费事件、公开证据与对应真实测试；后续导出按记录阶段同步 |

只删除已退出的最后消费者；同阶段旧节点与规范所有权一起退役，不保留兼容读取或迁移。

<a name="spec-map-and-storefront-版本表"></a>
## 版本表

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| `2026-10-01-tool-interaction` | 2026-10-01 | 冻结尚未实现的类别准入池与逐店真实集合、丰富度和菜单差异、对象范围与门店定位小扰动、公开品类偏移查询；删除逐条稀疏供给手填模式和旧发现请求正文；购买仍仅使用独立供给当前报价 |
| `2026-09-24-time-and-energy` | 2026-09-24 | 新增版本表。按[时间与能量规格](SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动)「冻结条款修订」第 25 行修订：地点发现是 `interact` 的浏览对象，占一个名额，浏览结束同一秒收尾并再决策，不排「下一秒」；发现贯穿菜单领域动作 `browse`；暂停活动只在开始地点恢复，不查能量，在别处可以直接结束；提示中的供给行写完整行，不用增量引用；验收台账相应改写。补记未入表的就地改动：d998a31（2026-09-21）在「整单购买与履约」加入三家指定餐馆本店 `prepared-meal` 供给的场景假设与到店首轮呈现 |
| `2026-09-26-time-and-energy-stage-9` | 2026-09-26 | 时间与能量规格实现后（阶段 9）：状态头中按该规格「冻结条款修订」第 25 行修订的条款由「尚未实现，待重新验收」改为已实现并通过离线验收，链接时间与能量验收记录；条款正文不变 |
| `2026-09-27-persona-prompt` | 2026-09-27 | 按[人物 Prompt 规格](SPEC-life-realism.md#persona-prompt-七层请求)「冻结条款修订」第 24 行修订（人物 Prompt 台账 MS1、MS2），修订部分待实现：可见期、可见凭据与呈现窗口只管展示与比较，`obtain` 可以指名未呈现的商品，指名购买路径不查可见凭据，不存在报 `SUPPLY_NOT_FOUND` 并附近似候选、重名报 `SUPPLY_NAME_AMBIGUOUS`，不替换；来源标记落在工具层商品目标动作的 `note`；发现与浏览得到的供给作为工具层商品目标只列一次；到店餐食列为工具层商品目标，删本地可购摘要；「记录、恢复与交付」的供给去重改为七层唯一出现，浏览结果只列名称；验收台账「供给候选身份」改写并记「离线验收待执行」；文件头注明修订待实现 |
| `2026-09-28-persona-prompt-implemented` | 2026-09-28 | 人物 Prompt 七层规格实现后文档（人物 Prompt 台账阶段 8，按最终代码 `f798ea7` 核对）：状态头改为修订部分已实现并通过离线验收；验收台账「供给候选身份」行改记通过；来源标记一句改为按代码如实记录：来源标记与完整声明留在私有绑定输入、动作结果与清单，七层工具层商品目标不带来源标记，与本规格原条款的出入登记在人物 Prompt 验收记录；提示中的供给一句改为商品目标的实际字段，不再称完整行 |
| `2026-09-28-persona-prompt-spec-fixes` | 2026-09-28 | 「地点发现与候选呈现」：工具层门店商品目标第一个动作的 `note` 写供给声明的来源标记（类型、来源引用与事实截止日），每个目标一次，落实本规格原句与人物 Prompt 规格修订 24 的 MS1，删去实现后「不带来源标记」的现状注记（见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md)） |
| `2026-09-29-dayrun-fidelity` | 2026-09-29 | 按[一日运行保真规格](../VERIFICATION.md#verification-incremental-evidence)「冻结条款修订」第 11、12、17、22、27 行与其 3.5 修订（一日运行保真 MS1，随该规格冻结）：「产品行为与边界」的「不替人物选择」按 S10 改写：人物可以只写商品、收货人与 `prefer`，或写渠道称呼，把挑店交给系统，系统按偏好选定并写明所选门店，此外不替人物选择；「冻结输入与身份」地点增可选 `branch_label`，场景不设地点别名，发布校验后补场景加载时的地点类别与标签唯一校验、地图文件结构版本随 `branch_label` 升一次、配送路线不可达与可疑显示名只警告（D19）；「地点发现与候选呈现」注明 `unreachable`、`unknown` 是合法状态、两种配送原因分开，指名购买改为 `from` 可选约束、分段匹配与按 `prefer` 选定、不再报 `SUPPLY_NAME_AMBIGUOUS`，可见期不放宽、离开后凭完整标签按名下单（D17）；「整单购买与履约」配送注明两种原因；「记录、恢复与交付」浏览结果列完整标签；验收台账地图唯一来源、人物选择边界、供给候选身份、自提与配送四行改写，标注尚待本规格实现后重新验收；文件头补修订说明，日期改写 |
| `2026-09-29-dayrun-fidelity-implemented` | 2026-09-29 | 一日运行保真规格实现后（按 `e377669` 的代码与测试核对）：验收台账「地图唯一来源」「自提与配送」（`34a41ee` 重新验收）与「人物选择边界」「供给候选身份」（`e377669` 重新验收）四行改记“通过”，表前说明补各行的证据测试；「冻结输入与身份」的版本事实补地图结构随 `branch_label` 升为 `2`、结构 `1` 的地图加载时拒绝（冻结时漏改）；文件头改为修订部分已实现并通过离线验收 |
| `2026-10-02-errands-workday` | 2026-10-02 | 按[个人事务与工作安排规格](../VERIFICATION.md#verification-incremental-evidence)修订（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 MS1–MS5、MS8、MS11），修订部分在冻结时未随文实现（该规格阶段 4 实现后的核对见 `2026-10-04-errands-workday-implemented` 行）：`query_world` 改六键，`from` 与购买共用同一来源解析且只匹配供给条目，`available_only` 只留已确认可执行的条目（未知条目被滤掉但不改写为已知否定），筛选后计数与分页口径；验收台账「只读查询边界」行改写。逐店声明、禁止随机补齐、营业声明的核对日期与来源、付款不等于持有、交付入库、到店取得的物品留在门店等条款不变。补记与更正既有表述：工具交互修订的类别供给、门店定位与四键查询已于 2026-10-01 实现，台账三行状态此前未随实现改记，本次更正，证据见[工具交互验收记录](../archive/tool-interaction/tool-interaction-verification.md)；购买请求 `from` 的合法写法是地点显示名（含「家」「住处」）或渠道称呼，原写「门店名」；文件头补修订说明，日期改写 |
| `2026-10-04-errands-workday-implemented` | 2026-10-04 | 个人事务与工作安排规格实现后文档（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 MS9，按 `d8a41d3` 的代码与测试核对）：文件头改为修订部分已实现并通过离线验收，链接[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)；验收台账「只读查询边界」行改记“通过”，状态格列六键、来源与可执行筛选的证据测试节点，表前说明随之改写；`2026-10-02-errands-workday` 行里的冻结期状态注记改为指向本行；条款正文不变。登记「冻结输入与身份」现行版本事实的两次就地更新（作业 MS6、MS7，当时未入表）：`12a3ccb`（2026-10-03，该规格阶段 2）把世界结构由 `8` 改为 `9`，`b8f5d47`（2026-10-04，该规格阶段 3）把场景结构由 `4` 改为 `5`；目录 `5.3`、品类契约与规则绑定 `3`、地图结构 `3` 不变，首次受影响发布目标一句是工具交互的历史表述，不改。证据为离线测试，配对全天复跑（该规格阶段 6）未执行 |
| `2026-10-05-layering-and-test-tiers` | 2026-10-05 | 五项已验收增量行为并回所属正文；提示唯一文本、机器验收登记与稳定引用同步，历史过程归档 |
| 共享规则与可重建环境 | 2026-10-05 | 当前目录因可供性声明注释更正按既有字节身份发布 5.4；品类和规则绑定 3、地图结构 3、场景结构 5、世界结构 9 不变；保留历史版本记录 |
| `2026-10-06-parallel-activities-claims` | 2026-10-06 | 按[同时做两件事规格](SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)阶段 2 修订「冻结输入与身份」：可供性占用声明在引擎包资源 `src/worldmock/data/affordances.toml` 重列，环境冻结的副本仍声明旧占用名的已发布环境在加载时拒绝，须按新占用名重新发布；品类和规则绑定 3、地图结构 3、场景结构 6、世界结构 9 不变 |
| `2026-10-06-rrs-rules` | 2026-10-07 | 仅登记已退役手机配置字段的精确历史定义出处，原始裁决正文逐字保留；其他源码引用仍按当前工作树核验 |
| `2026-10-06-rrs-map` | 2026-10-07 | 随机连通加权图、最短距离出行与环境恢复身份；对应正文按资源与规则引擎简化规格地图阶段修订 |
| `2026-10-06-rrs-store` | 2026-10-07 | 食品、店铺 app 来源、固定价格与单订单结算；类别消费、六个输入警告和目录覆盖率，删除本阶段退役正文 |
