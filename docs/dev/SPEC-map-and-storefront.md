<a name="spec-map-and-storefront-地图数据集合与门店供给开发规格"></a>
# 地图数据集合与门店供给开发规格

> 状态：冻结特性合同；独立地图分支与品类主线组合均已通过离线验收。当前结果见 [主线组合验收](research/map-and-storefront-main-verification.md)，原 [独立分支验收](research/map-and-storefront-verification.md) 保留历史身份；项目级三项真实验收未完成。「地点发现与候选呈现」「记录、恢复与交付」「验收台账」中关于发现、暂停活动恢复与提示供给行的条款按 [时间与能量规格](SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动)「冻结条款修订」第 25 行修订，已随该规格实现并通过离线验收（证据见[时间与能量验收记录](../archive/time-and-energy/time-and-energy-verification.md)）；修订记录见「版本表」。
> 「地点发现与候选呈现」「整单购买与履约」「记录、恢复与交付」「验收台账」中关于购买资格、来源标记、到店餐食与供给去重的条款按[人物 Prompt 规格](SPEC-life-realism.md#persona-prompt-七层请求)修订，修订部分已实现并通过离线验收（证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md)）；修订记录见「版本表」。
> 「产品行为与边界」「冻结输入与身份」「地点发现与候选呈现」「整单购买与履约」「记录、恢复与交付」「验收台账」中关于挑店、同名地点与加载校验、配送路线状态、指名购买、可见期与浏览结果的条款按[一日运行保真规格](../VERIFICATION.md#verification-incremental-evidence)修订（一日运行保真 MS1）；修订部分已实现并通过离线验收，改动的台账行「地图唯一来源」「人物选择边界」「供给候选身份」「自提与配送」四行已改记“通过”。
> 按[同时做两件事规格](SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订「冻结输入与身份」的当前目录版本（阶段 2）。
> 日期：2026-10-06
> 「地点发现与候选呈现」「验收台账」中关于 `query_world` 六键、来源与可执行筛选的条款按[个人事务与工作安排规格](../VERIFICATION.md#verification-incremental-evidence)修订（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 MS1–MS4、MS11）；修订部分已实现并通过离线验收（证据见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)）；购买来源写法一处属更正既有表述。
> 授权：本次任务要求审查提案、修订最终规格并完整实现。本文采用提案的建议产品范围，并纠正已失效的现状与业界判断。
> 取代：[地图与门店供给提案](PROPOSAL-map-and-storefront.md)中待裁决的数据结构与实现范围。

<a name="spec-map-and-storefront-产品行为与边界"></a>
## 产品行为与边界

同一件商品可以在不同地点由不同卖方销售。人物实际看到地点、道路路程、营业状态、独立报价、库存和履约条件，自己选择去哪家或从哪家配送；
也可以只写商品、收货人与请求级偏好prefer（“最快拿到”或“到手价最低”），to=null表示本人，或在from写渠道称呼，把挑店交给系统：多于一家可服务时，系统按该偏好选定一家，
并在结果中写明所选门店（[一日运行保真规格](SPEC-life-runtime.md#dayrun-fidelity-身体与需要) 3.3、3.5）。除此之外世界仅判断条件并结算，不替人物选择买什么、给谁与多急。

```mermaid
flowchart LR
    D[冻结地图与商品定义] --> Q[人物发现与比较门店供给]
    Q --> T[人物选择出行或配送]
    T --> V[环境按当时事实重验]
    V --> P[整单付款与门店库存变化]
    P --> F[到店取得或配送交付]
    F --> U[取得地点的商品使用]
    Q & V & F --> E[观察、事件、来源与恢复证据]
```

本次使用现有张江区域，保留真实地点来源，门店商品、价格、库存、营业及访问规则可以是明确标记的场景假设。审查发现现有地图有十一处地点之间的二百二十份按方式命名的响应，其中一百零四份步行响应错误复用驾车结果。实现必须重新取得或明确改成资料未知，不能按文件名宣称方式已核实，也不得继续声称八处地点没有任何路线文件。

本次全部实现发现、比较、出行、购买、自提、配送、资格判断、营业时间、调价与补货、恢复和来源记录。访问资格是场景给定的事实，可以限制进入或服务；本次没有购买会员、预约办理、排队、背包或物品搬运动作。到店取得的物品留在该地点，配送物品在交付后位于目的地。这是完整行为范围，不是待实现占位。

商品、地点、门店供给、品牌、经营业态、活动环境保持各自含义；不创建第五个引擎。运行期间禁止在线地点、路由、库存或报价请求。不得保留旧结构适配、迁移命令或默认回退路径。

<a name="spec-map-and-storefront-冻结输入与身份"></a>
## 冻结输入与身份

| 对象 | 必须表达的内容 | 权威位置 |
| --- | --- | --- |
| 地图数据集合 | 稳定标识、版本、WGS84 坐标系、时区、区域、来源时间、许可与通知、文件哈希 | 冻结地图文件与语料清单 |
| 地点 | 标识、名称、可选消歧标签 `branch_label`（同名地点的显示名为「名称（branch_label）」）、坐标、经营业态、可选品牌、活动环境、营业声明、访问要求 | 地图地点目录 |
| 商品 | 身份、规格、用途、消费与使用效果 | 商品目录 |
| 门店供给 | 标识、地点、商品、卖方账户、报价声明、初始库存、履约方式与范围、处理时长、资格及来源 | 地图门店供给目录 |
| 场景引用 | 固定地图身份，选中的地点、商品、供给及库存初值，人物资格与账户实例 | 场景文档 |
| 运行时事实 | 按供给区分的库存和有效报价，营业例外，人物位置、资格、账户与已交付物品 | WorldState |

场景不重复地点的名称、坐标、营业声明或路线。地图文件引用冻结路线响应及路由实例、profile、取得时间和计算依据。地图可以保留未被当前场景选择的地点；运行时只允许引用已选地点和供给。
同名地点只由地图的 `branch_label` 消歧，场景不新增地点别名字段；人物写的地点名按一日运行保真规格 3.1 无损归一后唯一即受理，「家」「住处」指人物的家。

地点保留单值 `kind` 作为现有交互菜单分类，显式 `business_kind` 表示经营业态，非空 `location_kinds` 表示活动环境。活动环境闭集为 `home/office/outdoor/fitness/venue/anywhere`，不得从名称、品牌或经营业态自动推断；`anywhere` 是活动侧无地点限制的声明，不是地点资料缺失时的默认值。地点缺失活动环境必须拒绝。

门店供给使用稳定 `supply_id`；`(location_id, product_id, seller_account_id)` 必须唯一。多家供给可以引用同一个商品。卖方按 `account_id` 绑定，多个账户允许共享经营角色，人物账户和服务账户的身份仍必须唯一。

商品定义删除门店、卖方、库存、实际购买报价、报价版本和配送时长。商品有效属性允许声明构建供给所需的
`purchase_price_fen_min/purchase_price_fen_max` 冻结范围；该范围不是报价，购买与价格排序只读取当前供给报价。
商品目录不保留静态购买转账边，仍保留品类购买配对及其 `RuleEdge`；不发生消费交换的耐用品不得伪造消费占位边。
运行时价格和库存不得从商品对象推断。
运行期 `ProductConfig` 继承库存使用定义，保留 `category_id`、`inventory_unit`、规格与使用效果；不把报价或卖方塞回商品。

当前实现使用目录 `5.5`、品类契约与交换规则绑定版本 `3`、地图结构 `3`、场景结构 `5`、世界结构 `9`。
本次首次受影响发布目标为目录 `5.0`、品类契约与交换规则绑定版本 `3`、场景结构 `4`、地图结构 `3`；世界结构 `8`
保持现有字段形状。地图声明新增必填 `random_seed/category_assignment_sha256`，绑定生成种子与分类文件。
独立阶段只更新受影响产物，料理及后续目录内容变更发布新版本；最终认证记录实际版本和摘要，禁止同版覆盖。
地图资料版本和文件哈希因新供给变化；旧目录、地图、场景、模板或代码身份直接拒绝，禁止兼容读取和迁移。

地图发布必须验证版本与哈希、坐标边界、时区、来源/许可声明、字段日期、地点与供给唯一性、商品和账户引用、路线端点与方式、路线响应状态及完整选择范围。同一来源的事实时间和模拟日期不同必须显式标记适用限制。过期核对声明、缺必需关系、未知旧字段均拒绝并记录英文错误。
场景加载（`load_runtime_scenario`）另查所选地点：`kind` 必须属于环境层的地点类型表，越界即拒绝并写明地点与类别；可见标签必须唯一，
重名且无法由 `branch_label` 消歧即拒绝；地图文件结构版本随 `branch_label` 升一次。配送供给到声明目的地没有 `reachable` 驾车路线、
显示名带坐标或标识形态时，只发运行输入警告 `DELIVERY_ROUTE_UNAVAILABLE`、`LOCATION_NAME_SUSPICIOUS`，不拒绝（一日运行保真规格 8.1，裁决 D19）。

<a name="spec-map-and-storefront-按地点类别生成完整供给"></a>
## 按地点类别生成完整供给

地点类别沿用 `LocationConfig.kind` 的现有词表，对象类别沿用品类契约的 `category_id` 与父子关系。
分类声明保存在冻结 `corpora/zhangjiang-map/category-assignment.json`，同一文件分别声明地点类别准入池和每店实际供给，
不增加另一套地点或对象分类、供给引擎或运行策略层。
文件恰含必填 `kinds/locations/source`：`kinds` 为现有地点 `kind` 到 `category_id` 数组的准入表，
`locations` 为逐店声明数组；顶层与逐店 `source` 均复用完整 `DataSource`，不另造来源字符串。
`DataSource` 保留 `kind/url/retrieved_at/license/notice/fact_cutoff/applicability` 的事实或场景假设结构。
库存、卖方、履约及来源继续显式使用供给假设材料，不将它们塞入分类词表或人物偏好。
`business_kind` 仍表示经营业态，`location_kinds` 仍表示活动环境；类别分配不推断活动环境、访问资格或人物偏好。

| 地点类别 | 可进入实际供给的对象准入池 |
| --- | --- |
| `restaurant` | 全部准入的 `prepared-meal` 及其子类对象 |
| `convenience_store` | 全部准入的 `food` 及其子类对象 |
| `supermarket` | 全部准入的 `food`、`durable`、`ingredient` 及其子类对象 |
| `wet_market` | 全部准入的 `ingredient` 及其子类对象 |
| `merchant` | 全部准入的 `food`、`durable`、`ingredient` 及其子类对象 |
| `home`、`office`、`fitness`、`park` | 不生成售卖供给；已有持有物、地点可供性和生活活动照常提供 |

`ingredient` 是品类树中的普通原料品类，需随真实原料对象、来源和库存转换规则发布，不能以空品类通过覆盖验收。
类别准入池不是每店菜单。同一冻结文件的逐店声明只有
`location_id/category_ids/object_ids/price_position_percent/source`，五个键全部必填；售卖地点必须声明，缺项拒绝。
全部地图地点 `kind` 均须在准入表显式声明，住宅、办公室、健身场所及公园写空数组，不能因漏配默认不售卖。
每店实际集合是 `category_ids` 及其后代中的全部准入对象与显式 `object_ids` 的并集，
全体必须仍属于该地点 `kind` 准入池，父子类及显式对象重合只生成一次独立供给。
无售卖类别的地点不制造零价供给。不得随机抽几件、按首屏裁剪、将准入池全量复制到每店，或让所有地点出售所有商品。
包装食品门店显式选择适用食品子类，不能以 `food` 根将所有餐馆正餐自动带进便利店。
当前正餐全在 `prepared-meal`，没有菜系属性；正式餐馆须发布有来源的菜单子类或明确较完整的对象菜单，
不得根据店名猜菜系。新增子类仍在已有品类树中，不另造菜单分类体系。

以下是本次正式场景的丰富度验收假设，不是行业事实；不足、未知素材或无真实来源直接拒绝发布，不随机补齐。

| 售卖地点 | 正式场景最低实际供给 |
| --- | --- |
| 每家餐馆 | 至少 8 个正餐；每对餐馆各自至少有 3 个不在对方菜单中的正餐 |
| 便利店 | 至少 40 个对象，覆盖至少 6 个最终对象品类，包含食品及饮品 |
| 超市 | 至少 80 个对象，覆盖至少 8 个最终对象品类 |
| 商户 | 至少 40 个对象，覆盖至少 6 个最终对象品类，包含日用品 |
| 菜市场 | 至少 8 个真实原料对象，覆盖至少 2 个已发布原料子类 |

统计按不同object_id和最终category_id去重，品类祖先不重复计数。正式场景的 `products/supplies` 显式选择
必须包含选中售卖地点全部声明商品和生成供给；加载与正式验收核对选择后的实际集合，库存覆盖仍用现有字段。
旧稀疏选择列表不得裁掉新集合，缺项直接拒绝，不自动选择或补齐。
行为夹具还须验证到店营业、有货、规则已定义、菜单准入和真实购买；
餐食交付后必须可食用，原料必须接通制作链，不能只通过集合计数。

离线构建复用已有 Pydantic 严格模型、品类祖先查询和 `hashlib.sha256`，不增加依赖。
卖方账户、初始库存、资格、履约方式和范围、处理时长继续显式声明；类别匹配不能自动创造这些事实。
原逐对象手抄稀疏供给与 `restaurant_offers` 模式直接删除，构建结果仍落为现有独立 `SupplyConfig` 和 `QuoteConfig`。
构建顺序按地点与对象身份排序，供给身份由地点、对象与卖方确定；同输入重复构建结果必须逐字相同。

<a name="spec-map-and-storefront-冻结价格范围与门店差价"></a>
## 冻结价格范围与门店差价

`purchase_price_fen_min/purchase_price_fen_max` 是同币种、同精度的正整数分，最小值不得大于最大值，
相等表示固定价格。范围来自对象有效属性，可由现有品类槽位继承并由对象差异覆盖，数值与覆盖依据必须有来源。
范围与 `purchase` 规则的 `money_fen/fen` 单位一致；现行合同不支持混合币种或小于一分的报价。

构建读取场景已有 `random_seed`，对 `[random_seed, location_id, object_id]` 使用
`json.dumps(..., ensure_ascii=False, separators=(',', ':'))` 编码，再将结果编码为 UTF-8 字节取 SHA-256。
摘要按无符号大端整数解释为 `h`，逐店 `price_position_percent` 为有来源的整数 5 至 95；
固定 `jitter = h % 11 - 5`，计算 `min + ((max - min) * (price_position_percent + jitter) + 50) // 100`，
以整数 half-up 生成一次初始报价。微小扰动不可配置，不再为每个商品独立抽整个价格范围。
本正式地图中的门店按同一地区比较，所有便利店定位至少高于地图内普通超市 10 个百分点，作为有来源场景假设；
不新增地区字段。
同商品的便利店报价不得更低，整数舍入同价合法。其他门店定位按事实或明确假设声明，不从名称推断。
同对象在不同地点各自计算，可存在同价；不要求在合法范围不足以区分门店时伪造差价。
运行时观察、查询、购买、`refresh/evaluate` 和恢复均不重新抽价；`evaluate_purchase` 仍仅解释当前供给报价。
有意变更价格只能发布新的冻结报价或通过已声明的调价事件，所有报价与调价金额均须在对象冻结范围内。
没有范围、单位不符、范围倒置或越界时拒绝构建或准入，返回具体字段路径、合法范围与英文错误并记录日志。

地图声明必填 `random_seed/category_assignment_sha256`，语料清单同时钉定分类文件哈希，
构建与准入核对地图种子与场景 `random_seed` 相等、分类文件字节及哈希一致。
范围来源仍位于对象来源，报价的 `source` 明确引用生成依据，不增加重复范围摘要或运行策略字段。
场景不得引用由另一种子生成的供给集合。
正式白领与多人场景当前使用不同 `random_seed`，必须分别发布并钉定对应种子的地图供给集合；
地点和路线来源可以共享，报价集合不能跨种子复用，不为共用一份地图擅改既有实验种子。
分类声明、种子、范围或供给变化必须重新发布地图及相应场景身份，恢复只读取已持久化事实。

<a name="spec-map-and-storefront-营业报价与访问条件"></a>
## 营业、报价与访问条件

营业与报价声明具有 `effective_from/effective_to`、`reviewed_on/still_valid_on` 及来源，生效区间为半开区间。所有时间明确时区，核对日期不得晚于可信截止日。相邻区间可以无缝切换，同类声明重叠必须拒绝；首个区间之前或声明间隙返回未知，不能推断全天营业、免费或可买。

营业按地点所在地图时区解释，支持每周多个分钟级开放区间、跨午夜、全天开放、明确闭店日期和日期例外。例外优先于每周规则；某日期明确闭店时不得继承前一日的跨午夜营业。营业结果为 `open/closed/unknown`，闭店和未知原因不同。营业例外作为世界事实保存，恢复不得丢失。

到达地点时重验访问资格，购买时重验营业、访问与供给资格、报价版本、库存、余额及履约条件。出发时的预测不构成到达或购买成功凭据；已完成的旅行不能因后续购买失败被抹掉。

有效报价与库存分别保存在 `WorldState.supply_quotes` 和 `WorldState.supply_stock`，键为供给身份。定义、观察与结算使用同一报价解释入口。调价、报价生效/失效和补货按模拟事件推进；营业变化进入既有事件时钟边界，禁止周期轮询。恢复必须继续使用持久化报价及库存，不能重载初始报价。

<a name="spec-map-and-storefront-地点发现与候选呈现"></a>
## 地点发现与候选呈现

现有商品目录浏览仍是 `interact` 的浏览对象，占一个活动名额，遵循现有登记、批次结算和下一次观察回灌；
浏览结束的同一秒收尾并再决策，不另排下一秒。浏览揭示事实、更新实际呈现留痕的效果继续保留。

固定只读工具 `query_world` 可在同一决策内查询冻结观察允许读取的地点、任务、对象与供给事实，
不占活动名额、不推进时钟、不登记世界请求、不授予可见凭据或购买资格；仍受地点菜单、访问资格、人物归属与读取边界约束。
它不会把未告知任务、不可访问门店的供给或别人的库存泄露给人物，也不替代有时长浏览及其呈现、轮转和留痕。

`query_world(name, way, category, from, available_only, offset)` 六个键全部必填；name、way、category、from 可为 null，但至少一个非 null，非 null 字符串不得为空白；`available_only` 为严格布尔，不单独构成查询条件。
`category` 只接受本轮 `callable_index` 已展示的公开品类标签，错误返回本轮全部合法标签；名称和方式沿用已有解析；`from` 写地点显示名或渠道称呼，与购买请求的 `from` 同名同义并共用同一个来源解析纯函数，只匹配供给条目（六键、来源与可执行筛选见个人事务与工作安排规格第五章，阶段 4 落地）。
`offset` 为非负整数，首次写0，结果为 `{matches, total_count, next_offset}`，`next_offset` 必填可空。
多条件取交集，同一冻结结果按公开完整名称及内部稳定身份排序，内部身份不返回模型。
offset等于total_count返回空末页，大于total_count按参数错误返回合法范围。
每页使用既有 `candidate_presentation_limit`，当前正式场景为 20；筛选在排序与切页之前施加，`total_count` 为全部筛选交集后的完整匹配数；同一冻结观察和筛选下依次提交 `next_offset` 可遍历全集，
最后一页为 null，不永久只保留前二十项，不新增游标编码或分页配置。它查询本人已经有权读取的对象和可调用方式，
不是按商品需求搜索门店并计算路线的另一套工具，`from` 只按人物写出的来源（地点或渠道）缩小结果，不按需求反查门店、不计算路线、不按距离或价格排序；不恢复已删除的 `discover_places` 请求。
未知条件仍保持未知，不伪装为已知否定：`available_only=false` 时报价或营业未知的条目照常返回并带未知原因；`available_only=true` 只留已确认此刻可执行的条目，未知条目被滤掉，空页只表示没有已确认可执行的条目，不等于都不能买。可执行取冻结观察里该动作的 `available`，与工具层和环境受理同源，成功仍以结算时重验为准。

公开供给行保留原始 `price_fen`、`price_version` 与 `quote`，同时提供 `purchase_pairing_state`、`purchase_coefficient` 和 `purchase_price_fen`。
配对为 `defined` 时，系数来自冻结声明，`purchase_price_fen` 通过同一 `purchase_amount` 入口计算购买一份的实际金额；报价未知时金额为 `null`。
配对不是 `defined` 时保留原始报价和配对状态，系数及实际金额均为 `null`，`purchase_allowed=false`；动机跳过该供给，但不隐藏已持有物品。
人物的余额判断和价格排序也使用该入口计算的一份金额。多份请求必须用原始报价、数量和系数重新求整行金额，不能把已取整的一份金额乘以数量。
原始报价保持正整数分；明确的 `defined` 配对可以有零系数并产生零额付款，仍正常扣供给库存和履约，不能把 `no_effect` 当作零价购买。

供给行的 `purchase_allowed` 只表达购买配对为 `defined`、当前营业、地点访问资格、供给资格、有效报价和正库存均满足，不表示余额、路线或特定履约请求已经通过结算校验。`pickup_allowed` 同时要求该供给支持自提且人物当前位于门店；成功购买仍以执行时的完整重验为准。

查询分批与来源、可执行筛选都只缩小并切分同一冻结权限下的稳定结果列表，不授予呈现凭据或购买资格，不更新 `last_presented_at`。
呈现仍只管展示与比较，不限制按名称指名购买（[人物 Prompt 规格](SPEC-life-realism.md#persona-prompt-七层请求)第五章「指名购买」）。
查询与真实浏览、购买共用已有地点和供给事实解释入口，不能建立另一套报价或资格判断。
来源解析同样只有一个入口：查询的 `from` 与购买的 `from` 调用同一纯函数，同一写法得到的门店集合逐一相等；`from` 解析成功但该来源没有权限内供给时返回合法空页，不使用购买拒绝码，不泄露不可访问门店。

模型中的来源使用类型、来源引用与事实截止日：工具层门店商品目标第一个动作的 `note` 写
「来源：<事实或场景假设>（<来源引用>），事实截止 <YYYY-MM-DD>」，每个目标只写一次；
完整声明保留在对应观察的私有绑定输入、动作结果和清单。既有路线、报价与履约证据不能删掉。
有时长浏览回灌的公开 `excluded` 按全部非 `object_id` 属性分组，用 `object_ids` 保留每个对象身份；
必须能还原全部逐项排除事实，原始结算证据保持逐项行。

地图必须为全部所选有向端点与方式声明 `reachable/unreachable/unknown` 三种状态之一，真正漏记录拒绝发布。地点存在但该方式不可达时，仍可被发现并明确标记 `unreachable`；尚未取得或无法核实资料的显式记录标记 `unknown` 并说明原因，不得冒充不可达。道路时间范围只依据冻结道路和已有分时公式；超范围、不可达与资料未知分别解释。明确未知的结果保留身份和原因，不能因无法计算距离就消失。已准备不可达记录必须有原始响应与哈希，出行动作给出明确拒绝原因；未知记录不允许出行。`unreachable` 与 `unknown` 是地图的合法状态：
配送路线属于这两种状态时场景照常加载、只发输入警告，人物在远程下单时看到「配送路线缺失」这一原因，与「送达地不对」分开（一日运行保真规格 5.3、8.1）。

空间索引只可以粗筛地点；AS2 的 protobuf Map 和数据库不是现有 OSM JSON 的直接适配接口。本次使用已有依赖与冻结地点数据，按明确道路信息查询，无须新增在线地图服务或空间基础设施。任何直线距离都不得充当道路可达时间。地图准备与运行时交通费都通过品类 `TravelFareRule` 和 `evaluate_travel_fare` 求值，不恢复已删除的文本费率公式。

购买候选按门店供给区分身份，保留 `product_id` 作为商品引用。可见期、排序、排除统计、呈现窗口、换新轮转和可见凭据均以 `supply_id` 区分；同商品不同门店不能互相覆盖或共享可见凭据。可见期与可见凭据只管展示与比较，指名购买路径不查可见凭据，按场景供给与当前报价解析；按[一日运行保真规格](SPEC-life-runtime.md#dayrun-fidelity-身体与需要) 3.2、3.3、3.5：
`from` 是可选约束（`null`、地点显示名（含「家」「住处」）或渠道称呼），分段匹配依次以 `SUPPLY_NOT_FOUND`（没有这件商品，附至多 3 个可照填的候选）、`SUPPLY_NOT_AT_STORE`、
`FULFILLMENT_NOT_SUPPORTED` 与 `ACTIVITY_PRECONDITION_UNMET` 拒绝；多于一个可服务的匹配时按请求级 `prefer` 选定一家并写进 `details.notes`，
不再以 `SUPPLY_NAME_AMBIGUOUS` 拒绝；不自动换成别的商品。持有物品与消费仍按商品身份记录。

`supply_browsed_at` 与 `supply_browsed_location` 分别按供给保存呈现时刻与呈现地点。新呈现另一条供给不能延长旧供给的有效期；已看见供给集合与这两份记录的键必须一致。通用浏览活动完成时也只把实际窗口记为已呈现，并在结果中保存该窗口。
可见期不放宽（一日运行保真规格裁决 D17）：离开呈现地点或超过 1800 秒后商品目标照常从工具层消失，人物凭进度层、记忆层中带店名的浏览结果按名下单。

单次模型观察共用一个供给呈现窗口：本次发现或浏览得到的实际供给优先作为工具层商品目标占用 `candidate_presentation_limit` 名额，按当前事实写出，其余候选只补剩余名额。同一供给在工具层只列一次，只占一个身份名额。可见凭据和呈现时刻只更新实际列入工具层的身份，可见凭据不是购买资格，宿主 `data.supplies` 保存的已知供给集不等于本次呈现集，不能据此更新窗口外供给的凭据。

交互菜单继续按地点 `kind` 与人物声明求交集；商品目录浏览及相应供给读取贯穿菜单领域动作 `browse`、实际模型 Schema、
本地校验、环境重验和正式场景菜单。已公开地图事实不因菜单改变，浏览动作仍可以被菜单禁止。
活动环境门只读取显式 `location_kinds`，覆盖候选、启动和恢复路径。

生活活动的目录声明必须为 `feedback_boundary=true`，复用既有反馈边界与实际暂停、继续、恢复控制。暂停保留已结算进度并释放当前活动占用，人物可随后出行；恢复只在活动开始的地点，重新验证原活动的显式环境要求，同时执行既有菜单与归属条件，能量不参与。在别处恢复以 `ACTIVITY_PRECONDITION_UNMET` 拒绝并点名开始地点，人在别处可以直接结束该活动；环境不满足时明确拒绝并保留暂停进度，不得把恢复门留成无法通过真实暂停进入的分支。验收必须经过真实引擎的启动、反馈、暂停、换地点和恢复请求，不能用辅助函数调用代替接线证明。

<a name="spec-map-and-storefront-整单购买与履约"></a>
## 整单购买与履约

人物购买通过start_activity的items表达，每项恰为{target,way,from,to}；含购买项时prefer必须是“最快拿到”或“到手价最低”，
无购买项时为null，null不默认最快。to=null统一表示本人，实名表示其他人物，“自己”字符串直接拒绝。
引擎内部结算订单行使用supply_id/quantity/price_version；整单包含一至五行，必须为同一地点和同一卖方账户，
同账户跨地点也必须拒绝。人物不提交内部供给身份、数量和价格版本；重复供给行不得绕过库存检查。

购买继续通过品类 `purchase` 配对与闭集算子；未定义、歧义或已审核不作用不能靠有效供给绕过。
每个订单行调用 `evaluate_purchase(quote.price_fen, quantity, coefficient)`，以 Decimal 对整行乘积向上取整分，形成该行的 `total_price_fen`；整单 `total_price_fen` 为各行金额之和。不得先对单价取整再乘数量。
`price_fen` 与 `price_version` 表达原始供给报价，交易行的实际付款和所选 `seller_account_id` 分别记录；规则不再声明商品 `price_slot`，不能用静态转账边缓存动态报价。

整单逐供给重验后一次付款；任意一项失败时账户、所有门店库存、人物库存、订单和事件均不发生购买副作用。成功时只扣所选供给库存、只给其卖方记账。单件与多件订单使用同一个权威购买路径，删除重复旧购买实现。

自提要求人物已在所选门店，目的地也为该门店；付款与交付证据分别明确，即时自提不得额外检查配送路线。配送要求供给支持该方式、目的地在声明范围内且有已冻结可行运输路线
（目的地不在范围与路线缺失是两个原因，分别以 `unmet=delivery_destination`、`delivery_route` 说明，一日运行保真规格 5.3）；时长使用既有分时路线时长和供给处理时长。同单按最迟备货时刻一次配送。付款并不意味着人物已持有，只有交付时增加目的地商品库存；重复到期事件不重复增加库存。

三个正式场景中的三家指定餐馆各有按逐店菜单声明生成并显式选中的全部本店 `prepared-meal` 供给，
并选中其商品；每条供给都有正初始库存、有效报价和自提方式。
这是带来源的场景假设，不代表真实餐馆菜单。未声明售卖类别的通用地点可无供给；
正式售卖地点必须覆盖逐店声明的实际集合并通过丰富度和差异验收，闭店或缺货不在运行时生成替代餐食。
到店后的首轮真实观察沿既有供给候选窗口把可购餐食列为工具层商品目标，人物可直接购买，
也可以按名称指名购买未呈现的本店商品；不另写本地可购摘要，呈现不扩大发现窗口。付款、交付和实际食用分别留证，
仅正消费区间证明吃过餐食。完整行为与验收见[一日行为规格](SPEC-dayrun-behavior.md#spec-dayrun-behavior-一日行为可信性特性开发规格)。

交易结果和配送记录保存供给、商品、地点、卖方、实际报价版本、金额、路线和来源。已交付物品按商品合并，同一商品来自不同门店不能改变商品规格或消费效果。既有资金守恒、数量守恒、幂等、原子提交与错误分类继续适用。

<a name="spec-map-and-storefront-记录恢复与交付"></a>
## 记录、恢复与交付

观察中的供给按七层唯一出现：浏览得到的供给只作为工具层商品目标出现一次（名称、每份热量与各动作的价格、耗时、数量、说明和可用性），浏览调用的结果只列名称，名称写完整标签「<门店>出售的<商品>」（一日运行保真规格 5.3），不重复比较行，也不用 `supply_id/based_on_action_id/changes` 引用（[人物 Prompt 规格](SPEC-life-realism.md#persona-prompt-七层请求)第一章「唯一出现原则」）。完整当前行保留在原始观察 `data.supplies`，动作结果保留历史完整证据。
`query_world` 直接返回模型工具消息及其证据，不添加观察商品目标、不改写 `data.supplies`、呈现时刻或可见凭据。
观察记录实际呈现的比较事实，浏览结果记录实际筛选条件与实际返回行，不能在导出时用当前目录重建历史。选择记录引用人物真正接触过的供给与报价。查询、旅行、购买、交付、使用仍是不同事实。

运行清单和导出清单保留地图版本、来源、许可及通知；逐条比较与交易可关联对应来源，场景假设不得标成真实门店事实。许可判断按来源和实际组合方式处理，不能仅以一个 share-alike 布尔值推断许可兼容。

世界提交失败时，权威世界、持久状态、事件序号与待提交的呈现及供给可见凭据都必须保持原状；
只有提交成功后才能清空这些观察缓存，重试不得丢失人物已经看到的供给。

世界结构版本更新，旧场景、旧购买请求和旧检查点直接拒绝。新建、打开、共同检查点恢复及分支都验证地图与代码身份；供给库存、报价、营业例外、可见供给和呈现轮转必须完整往返。地图或供给篡改拒绝，分支不能修改父目录。


<a name="dayrun-fidelity-按名调用与指名购买"></a>
## 按名调用与指名购买

<a name="dayrun-fidelity-按名调用与指名购买-统一名称解析"></a>
### 统一名称解析

**规则。** 所有目录类对象（商品与供给、生活活动、内容、游戏、地点、联系人与人物、任务、活动）都可以直接写名称。
无损归一后唯一就受理；相似度只用来给候选，永不自动替换（人物 Prompt 规格裁决 #19）；候选按「此刻可行」过滤并写成可以照填的请求；
宿主逐项收集全部失败项，环境逐项解析后合并成一次拒绝。

**历史证据（2026-09）。** 名称检查分散在约 9 处：宿主 `custom/agents/life_agent.py`，路由 `src/worldmock/life/routing.py`（宿主 `custom/agents/life_agent.py` 与环境 `src/worldmock/life/session_actions.py` 共用），
交互菜单 `src/worldmock/life/interaction_menu.py`，环境 `src/worldmock/life/session_actions.py`（`_offered_selection` 找不到即裸 `ACTION_REQUEST_INVALID`，`_named_selection` 只管指名购买），
各处历史定位见[改造前固定提交原文](https://github.com/HoldThatThgt/WorldMock/blob/2cf230115f4084aef2376e9d77bc976efbed43ea/docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要)。每一处都是逐字匹配、遇到第一个错误就停（`routing.combination_error`，`src/worldmock/life/routing.py`，算出了全部逐项错误却只返回一个）；
宿主报错是英文；环境拒绝虽有中文 `reason`，`message` 仍是英文，除 `SUPPLY_*` 两码外都不给候选。只有商品有指名购买，且只在 `obtain` 且方式恰好是「到店」或「远程下单」时放行
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
   - 每个现有方式只属于一个内部动词（`WAYS`，`src/worldmock/life/vocabulary.py::WAYS`，「制作」属于 `obtain`）；由方式唯一推导动词，人物请求不再带 `actions`。不适用于目标的方式报 `WAY_NOT_OFFERED`，完整候选只含新请求字段；
   - 现有 `WAY_SYNONYMS` 仅是既有方式闭集的名称识别合同（登记进接口合同「请求模型的闭集与字面边界」），不新增旧接口或偏好兼容写法，只有在目标列出的方式里恰好命中一个时才受理；
   - 名称归一只识别现有名称；来源与去向严格按方式判定，食用、使用、制作的两端须为 `null`，不接受「自己」等固定值的兼容写法。
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

**规格与合同。** 系统层第 12 条改为「名称照工具层、待办层中的写法填写；商品、生活活动、内容、地点都可以直接写名称，即使此刻没有展示；写得不准时会返回最接近的候选，不会替你改」。
「其余动词的目标须是本轮列出的名称，不是时由宿主按协议错误纠正」在冻结文档里有十处镜像（接口合同 :55-57、:226-228、:287-301、:691；人物 Prompt 规格 :456 与修订 #24、裁决 #26；
交互菜单规格 :108、:123；life-runtime :729；决策上下文规格 :61；时间与能量规格 :508、:1056；数字人交互规格 :246；人物规格 :342-343），统一改为
「宿主归一后唯一即受理，否则按第四章给候选」。数字人交互规格 :196「同名对象由自然语义消歧」不变。

<a name="dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法"></a>
### 指名购买：`from` 是约束，不是语法

**历史现状（2026-09）。** 指名购买（裁决 #19）已实现并给候选；本次失败在 `from` 与标签的规则和判定顺序（白领根因研究 :259「咖啡和盒马食材没有购买｜未进入轮转窗口」在本次运行里的原因已变）。
WC 18:15 请求番茄、鸡蛋并写 `from:"盒马App"`：`src/worldmock/life/named_purchase.py` 先按 `from` 与能否服务过滤，再判断「没有这个商品」，于是能送到家的番茄被说成
`SUPPLY_NOT_FOUND`「没有找到这个名字的商品」；照抄候选并写与标签一致的 `from` 又撞上裸 `ACTION_REQUEST_INVALID`（`src/worldmock/life/named_purchase.py` → `src/worldmock/life/guidance.py` 的默认句）；
同刻受限轮随即关闭购买（`src/worldmock/life/same_moment.py`）。三样能送到家的菜全天都没买成。
对**列出的**标签写 `from` 时，宿主给免费纠错（`src/worldmock/life/routing.py`，"from is null when the target already names its store"），模型每次都立刻改对。
`_serves`（`src/worldmock/life/named_purchase.py`）要求到店供给在人物当前地点、配送目的地在 `delivery_destination_ids`，所以另一家店的到店商品也被报成 `SUPPLY_NOT_FOUND`，
而不是「去那家店」；当时的 `near_candidates` 扫全部供给，不做可行性过滤，定位见[改造前固定提交原文](https://github.com/HoldThatThgt/WorldMock/blob/2cf230115f4084aef2376e9d77bc976efbed43ea/docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要)。
#890 的拒绝正是人物 Prompt 规格「来源与去向」表（:422，目标本身是「<门店>出售的<商品>」时 `from` 为 null）与接口合同 :311、:278-279 所规定的；本条改写该表。

**设计。**

- **(a) 放宽门槛**：用 3.1 的 `named_kind` 加方式同义表；「点冰美式」「下单购买」只要方式能映射到「到店」或「远程下单」，就按 `obtain` 处理
  （接口合同 :295「指名购买取「到店」或「远程下单」」随之写入同义表）。
- **(b) `from` 当作约束**：

  | 写法 | 处理 |
  | --- | --- |
  | 完整标签，`from` 是同一家店 | 受理，`details.notes` 说明 `from` 可以写 null |
  | 完整标签，`from` 是另一家已知门店 | `SUPPLY_SOURCE_MISMATCH`，候选就是这个标签 |
  | 商品名，`from` 是已知门店但它不卖 | `SUPPLY_NOT_AT_STORE`；该店完全没有供给时原因写英文 `No products are available for purchase at this store.` |
  | `from` 不是任何门店的名字（如 App 名） | 见 3.3 |

  人物 Prompt 规格「来源与去向」表与接口合同「请求模型的闭集与字面边界」的 `obtain` 行改为「`from` 为 null，或与标签门店一致」。
- **(c) 分段匹配**（替换 `src/worldmock/life/named_purchase.py`）：
  1. M0 按名称匹配，为空才报 `SUPPLY_NOT_FOUND`（只表示「没有这件商品」）；
  2. M1 = M0 ∩ `from`，为空报 `SUPPLY_NOT_AT_STORE`；
  3. M2 = M1 ∩ 所选方式，为空报 `FULFILLMENT_NOT_SUPPORTED`，并说明英文原因（如 `Remote ordering is the only supported fulfillment method.`）；
  4. M3 = M2 ∩ 能服务（到店：人在店里；远程下单：能送到收货地），为空时以 `ACTIVITY_PRECONDITION_UNMET` 说明要去哪里、或哪家送不到哪里，并附能送到的同名候选；
  5. M3 多于一个时按请求级 prefer 选定一家，details.notes 说明门店与方式；不以商品重名拒绝。
     prefer 的 最快拿到/到手价最低/null 闭集与决胜顺序见购买请求合同；删除旧歧义规则和分类分支。
- **(d) 多件购买**：逐项解析、逐项报错（第四章 `items[]`）；受理仍然整组原子。
- 三个新码登记进接口合同「活动受理拒绝码」（:257-275），按时间与能量规格「对新增行为的约束」（:1081-1085）的要求全部登记。

按此设计，WC 18:15 的三次请求（#882、#890、#994）都会被受理。

**先行的小修**（不依赖渠道，约 20 行，阶段 1）：完整标签加上一致的 `from` 照常受理（环境 `src/worldmock/life/named_purchase.py` 与宿主 `src/worldmock/life/routing.py` 两处，宿主也要停止拒绝
「列出的标签加同店 `from`」）；写了不存在的门店或 App 时，候选给门店名与 App 称呼，而不是商品标签。

<a name="dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店"></a>
### 购买渠道：App 名解析为成员门店

**规则。** App 是门店的入口，不是交付对象（裁决 D8：把 App 连到门店，不给 App 订单默认配送时长）。`from=<App 称呼>` 解析为该渠道成员门店的供给；
送达时刻仍由供给的备货时长与冻结路线决定（`src/worldmock/life/basket.py`：最慢备货加出发时刻的计划驾车时长），不设服务范围与 SLA 字段；
App 级默认时长只用于数据集构建与质检（数据集方案 3.4）。渠道内多家成员门店都能服务时按请求级 prefer 选定一家；
渠道内外多家可服务均按请求级 prefer 选定，不保留商品重名拒绝。

**渠道的来源**（裁决 S7）。一个渠道是 `{channel_id, names[], store_location_ids[]}`。

- **采用（裁决 S7）：从场景钉定的手机配置派生，不新增场景字段。** 场景已可选钉定手机配置（`phone_simulation_config` 与其 sha256，`src/worldmock/life/scenario.py::LifeScenario.phone_simulation_config`），WorldMock 只读它的投影
  `PhoneSimulationCoverage`（`src/worldmock/profile/coverage_apps.py::PhoneSimulationCoverage`）：`purchase_apps_by_seller`（卖家账户→App）与 `app_display_names`（App→称呼），并已核验每个卖家有在售供给
  （`src/worldmock/profile/coverage_apps.py::_executable_apps`）。渠道由此派生：`channel_id` 取 App 标识，`names` 取其称呼，成员门店取该卖家有供给的地点。手机配置缺省时没有渠道，App 名报 `SOURCE_NOT_FOUND`。
  该投影现只用于覆盖率核验，本条使它参与运行行为；它已由 sha256 钉进场景与清单，运行身份不变。
- **另一选项：场景新增可选字段 `purchase_channels`**（提案 G9 的形状，含 `store_choice` 与 `source`）：要与手机配置重复维护卖家→App 映射，两处可能不一致，
  场景结构版本随之升级（接口合同「冻结地图与场景引用」）。
- **准入**（两种来源相同）：`names` 在渠道之间不重复，也不等于任何地点的名称；成员门店必须是场景里选入的地点，并且至少有一条供给。

**解析规则**（`named_purchase.resolve_named_supply` 及其调用处）：

1. `from` 决定来源范围，依次判断：为空→全部门店；等于地点显示名（精确、无损归一或去掉「地点：」前缀后唯一，「家」「住处」指本人住处）→该地点；等于渠道称呼（去掉空白、不分大小写，其余逐字比较）→该渠道的成员门店，并记下 `channel_id`；
   都不是→新码 `SOURCE_NOT_FOUND`（登记进「活动受理拒绝码」），候选为最相近的门店名与 App 称呼（名称类拒绝，3.4）。
   `query_world` 的 `from` 与本条经同一个不依赖环境上下文的纯函数解析（入参为所写来源、地点名表、住处、渠道表），同一写法在购买侧与查询侧得到的门店集合逐一相等
   （个人事务与工作安排规格 5.2，阶段 4 落地）；查询侧解析不到时是查询参数错误 `ARGUMENTS_INVALID`（`path` 为 `from`），不带环境码 `SOURCE_NOT_FOUND`；解析成功而该来源没有权限内供给时是合法空页。
2. 目标是完整标签：`from` 为空、等于该门店、或是包含该门店的渠道时照常受理，否则按 3.2 处理。
3. 目标是商品名：在来源范围内按 3.2 分段匹配；多家成员门店都卖时选最近的一家（裁决 D8，用户裁定）：远程下单按到收货地的预计送达最早（最慢备货加出发时刻的计划驾车时长），
   到店按从当前地点出发的路线最短；并列时取到手价最低，再取门店标识；所选门店写进 `details.notes`。这在渠道内改写地图与门店规格 :11「不替人物选择」与接口合同 :321、:274：
   写了 App 称呼就是把挑店交给平台。单一成员门店的渠道（本次两份场景都是）不受影响。
   〔3.5「收敛」第 5 条把本条推广为按请求级 `prefer` 选定：`prefer` 明确为 `最快拿到` 时即本条，为 `到手价最低` 时取到手价最低的一家；`from` 为空或方式为「购买」时同样适用。〕
4. 订单记录 `channel_id`：写在 `action_result.data.orders[]` 与 `purchases.jsonl`（`src/worldmock/footprints/purchases.py`）各一列，不进 `DeliveryState`（`src/worldmock/life/models.py::DeliveryState`，持久化进世界状态），
   世界结构版本不因它升级；数据集手机合成优先用它，卖家映射作为回退。

**以 WC 18:15 走一遍**：「番茄、鸡蛋」`from=盒马App` 命中盒马渠道，范围是模拟生鲜出货点；两样商品各自唯一、都能送到家，合成一单，`channel_id` 为盒马，约 18:46 送到。

**冻结条款**：life-runtime 规格「随身持有与远程订购」的「远程订购复用既有交付语义，不新增渠道对象」（:933）改为「渠道只是门店集合的称呼，不带交付语义」；
接口合同「供给结算与事件载荷」（:508-511）的订单字段加 `channel_id`。

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

<a name="tool-interaction-地点供给和价格"></a>
## 地点供给和价格

<a name="tool-interaction-地点供给和价格-类别准入与逐店商品集合"></a>
### 类别准入与逐店商品集合

同一冻结 `corpora/zhangjiang-map/category-assignment.json` 恰含 kinds、locations、source 三键：
kinds 为 kind 到 category_id 数组的准入表，locations 为逐店声明数组，source 沿用现有 DataSource。
kind 使用现有 LocationConfig.kind，品类引用现有 category_id 并包含后代，由 is_descendant 判断。
地点类别只决定允许售卖的范围，不把这个范围的全部对象复制到每一家店。

| 地点 kind | 允许的对象品类 |
| --- | --- |
| restaurant | prepared-meal 及后代 |
| convenience_store | food 及后代；实际选择包装食品品类，不将正餐根集合全部纳入 |
| supermarket | food、durable、ingredient 及后代 |
| wet_market | ingredient 及后代 |
| merchant | food、durable、ingredient 及后代 |
| home、office、fitness、park 及其他不售卖地点 | 空集合；保留真实库存、饮水、内容和生活活动 |

每个售卖地点恰有 `{location_id, category_ids, object_ids, price_position_percent, source}` 声明，所有键必填；
source 复用现有 DataSource 的事实/场景、许可、截止与适用边界，不另建来源字符串结构。
实际商品集合是 category_ids 的全部准入后代对象与 object_ids 显式对象的并集，再校验全部属于该 kind 准入池。
品类批量展开，必要的菜单商品显式列出，去重后为每店构建全部声明对象；无按比例随机抽样或稀疏抽样策略。
超市、便利店选择具体食品子品类；不能因为商品根是 food 就把所有正餐和食品塞进所有店。
正式 prepared-meal 已发布有来源的菜单子品类，正餐归入实际发布的品类，不按店名推断菜系。
正式发布必须提供有来源的菜单子品类或完整显式菜单，禁止根据店名或现有 meal_class 猜菜系。
新增 ingredient、原料子品类及有来源对象属于正式数据发布，未发布前不得声称菜市场已丰富。

下列数量是当前正式场景的验收假设，必须在数据来源中注明，不是行业通用规则，也不做可配置策略层。

| 售卖地点 | 最低商品与品类覆盖 |
| --- | --- |
| 每家餐馆 | 八个可购买正餐；每对餐馆菜单不同，双方各至少有三个对方没有的菜 |
| 每家便利店 | 四十个商品、六个最终商品品类，覆盖食品及饮品 |
| 每家超市 | 八十个商品、八个最终商品品类 |
| 每家商户 | 四十个商品、六个最终商品品类，包含日用品 |
| 每家菜市场 | 八个真实原料对象、两个已发布原料子品类 |

统计按不同 object_id 及其最终 category_id 去重，品类祖先不重复计数；正式声明不足直接拒绝发布，
不能临时复制商品、生成假对象或随机补齐。行为夹具须在正常营业时到店，验证足量对象有货、存在购买配对，
食品可实际购买并食用，食材可用于真实配方；目录存在不等于本店可取得。正式场景的 products、supplies 显式选择必须包含选中售卖地点的
全部声明商品及生成供给，库存覆盖仍沿用现有字段；不得只发布丰富地图而运行仍选旧稀疏 supply_id 清单。
加载与正式场景验收核对选择后的实际集合，不新增自动选择或缺项补齐分支。
所有 kind 必须显式声明，包括空集合；每个售卖地点必须声明菜单和来源，漏配不作为空集合默认值。
同商品保持一个对象定义，各店有独立 supply_id、卖方、Quote、库存和履约。
既有稳定身份与去重规则保证构建顺序无关，营业、资格、配送、路线和卖方账户显式绑定，缺资料拒绝构建。

<a name="tool-interaction-地点供给和价格-范围内定价"></a>
### 范围内定价

对象有效属性 purchase_price_fen_min/purchase_price_fen_max 为有来源的正整数人民币分边界，min<=max；
可由品类继承、对象覆盖，min==max 合法。它们是构建输入，不是实际 Quote，对象不携带卖方、库存和报价版本。
非货币交换和家庭初始库存不伪造零价供给。

每家店声明整数 price_position_percent，取 5 至 95，表示在同一商品价格范围内的位置，来源注明事实或仿真假设。
本正式地图中的门店按同一地区比较，所有便利店定位比地图内普通超市至少高十个百分点。
这是本场景价格假设，不宣称行业必然规律，不新增地区策略字段。
不同门店可有不同菜单和价格定位，不能将每个商品的整段范围独立均匀抽价。

以 canonical JSON `[random_seed, location_id, object_id]`（ensure_ascii=False、separators=(",", ":")）的 UTF-8
字节计算 SHA-256，digest_integer 为无符号大端整数。固定公式如下，不开放额外抖动配置：

```text
jitter_percent = digest_integer % 11 - 5
percent = price_position_percent + jitter_percent
price_fen = min + ((max - min) * percent + 50) // 100
```

采用整数四舍五入，percent 始终在 0 至 100，相同输入跨机器、跨平台得到同报价。
差价可因整数分舍入相同，不强制每对门店每件商品都不同；定位相差至少十个百分点时，同商品不会逆转高低顺序。
min==max 始终得到该值。扰动只在构建时应用一次，生成带来源、有效期与 price_version 的冻结 Quote。
MapDataset 必填 random_seed、category_assignment_sha256，哈希同时绑定 kind、逐店集合和价格定位，加载核对。

观察、查询、候选、购买、结算和恢复读取实际有效 Quote，不重新抽价。
显式调价沿用报价事件且仍在商品范围内，配送费单列不算商品范围；已受理订单保留成交价。
恢复沿用持久化当前报价和库存。价格定位决定初始构建报价，真实调价不被伪装为重新构建。


<a name="spec-map-and-storefront-验收台账"></a>
## 验收台账

每项都必须有真实生产入口上的行为断言。无占位、TODO、skip、xfail 或以接口存在替代行为验收；当前完整结果见 [主线组合验收](research/map-and-storefront-main-verification.md)。
下表“通过”包含独立特性与本次主线组合的离线验收，具体源码和命令分别保留。组合全量的四个跳过用例均因既有历史产物缺失，本特性与组合新增用例没有跳过；不得把这些离线结果解释为十二周真实运行通过。按人物 Prompt 规格修订、尚未重新验收的行记“离线验收待执行”（「供给候选身份」行已在最终代码上重新验收，改记通过）。
按一日运行保真规格修订的四行在重新验收之前同样另记待验收一档，判据另见[该规格验收台账](../VERIFICATION.md#verification-incremental-evidence)的「指名购买」「购买渠道」「浏览与提示」「加载校验」行：
「地图唯一来源」（`tests/life/test_load_checks.py`、`tests/life/test_map_dataset.py::test_old_map_structure_version_is_rejected`、
`tests/integration/test_input_warnings.py::test_map_problems_are_frozen_as_scenario_warnings_and_creation_still_succeeds`）与「自提与配送」
（`tests/life/test_browse_and_hints.py` 的两种配送原因用例）在 `34a41ee` 重新验收，「人物选择边界」
（`tests/life/test_purchase_channels.py` 的 `test_several_member_stores_choose_the_nearest_and_note_it`、`test_member_store_ties_break_by_landed_price_then_store_identity`，`tests/life/test_named_purchase.py::test_several_servable_matches_follow_prefer_and_never_report_ambiguity`）
与「供给候选身份」（`tests/life/test_purchase_stages.py`、`tests/life/test_wc_named_purchase_replay.py`）在 `e377669` 重新验收，都已改记通过。
按个人事务与工作安排规格修订的「只读查询边界」行，六键部分在该规格阶段 4 实现之前另记未实现一档；阶段 4 已实现，在最终代码与测试提交 `a7e2fdc` 上通过离线验收，该行改记“通过”，六键、来源与可执行筛选的证据测试列在该行状态格，四键部分的通过证据不变。

| 要求 | 必须证明的行为 | 状态 |
| --- | --- | --- |
| 地点类别与每店实际供给 | kind只定义准入池；逐店类别及对象并集完整生成，父子类重合不重复；丰富度、餐馆菜单差异和真实取得链通过；首屏不裁剪实际供给 | 通过（证据见[工具交互验收记录](../archive/tool-interaction/tool-interaction-verification.md)） |
| 冻结范围与门店定位 | 同种子与内容重复构建逐字相同；定位5至95与固定小扰动生成全部初始价；同地图视为同一地区，所有便利店定位至少高地图内普通超市10个百分点、同商品不更低；全部报价和调价在范围内，固定范围合法；查询、购买与恢复不抽价；独立报价库存 | 通过（证据见[工具交互验收记录](../archive/tool-interaction/tool-interaction-verification.md)） |
| 只读查询边界 | `query_world` 以 name/way/category/from/available_only/offset 查询冻结可读事实，任意筛选组合沿 next_offset 遍历等于完整匹配集；查询与购买对同一 `from` 得到的门店集合逐一相等；不推进世界或授予凭据，菜单、访问、归属和读取范围不能被绕过；返回错误包含字段路径和合法约束 | 通过（四键合同的证据见[工具交互验收记录](../archive/tool-interaction/tool-interaction-verification.md)；六键见 `tests/life/test_world_query.py` 的 `test_world_query_requires_every_declared_field`、`test_world_query_rejects_the_retired_four_key_request`、`test_world_query_reports_all_dynamic_errors_and_full_legal_values`；来源筛选见 `tests/life/test_purchase_channels.py::test_query_and_purchase_resolve_each_from_form_to_the_same_stores` 与 `tests/life/test_world_query_filters.py::test_from_alone_is_a_legal_query_matching_only_that_stores_supplies`；可执行筛选见 `tests/life/test_world_query_filters.py` 的 `test_available_only_judges_only_the_actions_of_the_written_way`、`test_unknown_quote_and_unknown_opening_are_filtered_out_but_stay_listed_with_their_reason`；遍历与只读见同文件的 `test_filtered_traversal_equals_the_complete_match_set_and_counts_after_filtering`、`test_filtered_queries_leave_world_clock_events_credentials_and_presentation_unchanged`；见[个人事务与工作安排规格](../VERIFICATION.md#verification-incremental-evidence)验收台账阶段 4 各行） |
| 地图唯一来源 | 正式场景只存引用；地图身份与选区解析；旧重复字段及篡改拒绝；所选地点 `kind` 越界与可见标签重名在场景加载时被拒并写明原因，`branch_label` 消歧后同名地点可加载且可区分，配送路线 unknown 与可疑显示名只发输入警告 | 通过 |
| 地点分类分离 | 经营业态、菜单分类、活动环境互不推断；缺声明拒绝；生活活动反馈控制可实际使用，换地点后只能结束暂停活动，回原地点可恢复并保留暂停进度 | 通过 |
| 多门店独立供给 | 同商品多报价、多卖方、多库存；只改变所选门店；同角色账户合法 | 通过 |
| 时态声明 | 半开端点、间隙未知、过期核对、重叠拒绝、分段与跨午夜、日期例外、时区 | 通过 |
| 地点发现 | 需求和业态筛选、道路范围、可达/不可达/未知、关店仍发现、实际结果回灌 | 通过 |
| 人物选择边界 | 实际请求得到可比较事实；无环境代选（人物写方式「购买」、渠道称呼或 `from` 为空而多于一家可服务时，按请求级 `prefer` 选定并写明所选门店，这是人物交给系统的选择）；菜单 Schema 和环境重验一致 | 通过 |
| 供给候选身份 | 同商品多门店不互相覆盖；可见期、窗口与轮转只管展示；被轮转隐藏但存在的商品按名称可买并按当前报价结算，不存在时给至多 3 个近似候选且不替换，多于一家可服务时按 `prefer` 选定并写进 `details.notes`，不报歧义；离开浏览地点后凭完整标签仍可按名下单 | 通过 |
| 时间重验 | 出发可行，到达访问受限或购买闭店/售罄/调价后明确失败；旅行事实保留 | 通过 |
| 整单原子结算 | 跨地点或跨卖方、重复项、坏报价、缺库存、缺余额均无副作用 | 通过 |
| 自提与配送 | 远程自提拒绝、配送范围与路线重验、一次配送、交付前不可消费、重复交付幂等；送达地不对与配送路线缺失是两个可区分的原因 | 通过 |
| 世界恢复 | 调价、补货、营业例外、可见供给、库存和轮转保存；打开/恢复/分支身份拒绝 | 通过 |
| 状态提交与缓存 | 真实 SQL 失败保持世界、持久状态、事件和两种观察缓存不变；成功重试清空两种缓存；原存活变异以相同补丁独立复验检出 | 通过 |
| 来源与交付 | 观察/事件/运行及导出清单保存地图和供给来源；假设与真实资料可区分 | 通过 |
| 最小完整流程 | 正式冻结场景运行发现→比较→出行→自提与远程配送→消费→SQLite→恢复 | 通过 |
| 工程验证 | 定向与全量测试、分支/公共接口覆盖、风格、文档门禁、独立突变审查 | 通过 |
| 品类购买与供给交汇 | 保留配对拒绝、动态报价与卖方；非作用供给仍可比较但不可购买，已定义零系数可零额履约；公开原价、配对状态、系数与一份实际金额，余额和排序共用计价；非整数系数和多份数量只在整行取整；准备及运行交通费共用闭集算子；目录 4.0 与契约 2 身份完整恢复 | 通过 |

<a name="spec-map-and-storefront-文件修改范围"></a>
## 文件修改范围

| 范围 | 必须同步的文件或消费者 |
| --- | --- |
| 权威文档 | `docs/CONTRACTS.md#contracts-worldmock-接口合同`、`docs/VERIFICATION.md#verification-worldmock-验收标准`、`docs/INDEX.md#index-worldmock-文档入口`、`docs/EXPERIMENT.md#experiment-worldmock-实验设置`、设计目录首页与 overview/architecture/exchange/resource/simulation/recording/configuration/io-formats |
| 关联规格 | category-contract、life-runtime、life-realism、interaction-menu、decision-context 的购买身份、候选、工具、结构版本与来源条款；implementation-status 与 spec-coverage 台账 |
| 品类与目录 | `src/worldmock/categories/rules.py`、`operators.py`、`src/worldmock/catalog/compiler.py`；正式契约、交换规则、对象与清单的新版本，保留库存转换与已解析商品使用合同 |
| 数据与准入 | `corpora/zhangjiang-map/category-assignment.json`、按正式场景种子分别发布的地图供给集合、语料清单、许可通知、目录商品/合同、三份生活场景钉定引用、地图/目录/场景关系验证与准备脚本 |
| 运行时 | scenario、semantics、models、commerce、basket、travel、candidates、observe、engine、motivation、rhythm、scheduling、state_commit |
| 人物边界 | tools 请求注册、LifeEnv、LifeAgent 动态 Schema/校验、interaction_menu、menu_execution、narration/projection |
| 记录与交付 | 运行清单、footprints 存取与导出、checkpoint/branch、delivery manifest/trace validation 中受影响的身份及来源 |
| 测试 | 旧结构 fixture 全量切换，新行为与异常断言，正式场景端到端，独立 Bob 语义突变与逐条要求映射 |

详细来源、现状差异与设计裁决分别见 `research/map-and-storefront-feasibility.md`、`research/map-and-storefront-document-impact.md`。修改清单必须在实现后按实际差异再核对，不能用本表证明代码已经完成。

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
| `2026-10-06-parallel-activities-claims` | 2026-10-06 | 按[同时做两件事规格](SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)阶段 2 修订「冻结输入与身份」：当前目录随可供性占用声明重列发布为 5.5；品类和规则绑定 3、地图结构 3、场景结构 5、世界结构 9 不变 |
