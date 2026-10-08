<a name="resource-engine"></a>
# WorldMock 产品设计说明书 — 资源引擎

<a name="resource-engine-职责与边界"></a>
## 职责与边界

资源引擎负责加载、校验和查询冻结目录。WorldMock-Dataset 制作并发布环境，WorldMock 接受目录与场景，
不读取语料目录，不保存环境规则副本，不拥有运行中的订单、内容历史或活动进度。

```mermaid
flowchart LR
    D[数据仓制作环境] --> C[目录与场景]
    C --> V[身份、字段、绑定与图校验]
    V --> F[只读目录]
    F --> W[模拟引擎的活动与交易]
```

<a name="resource-engine-环境目录文件"></a>
## 环境目录文件

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | `format_version: 2`、环境标识与版本、`rules_version: 2`、六个文件的路径/哈希/字节数、`catalog_content_sha256` |
| `apps.json` | `app_id`、真实 `name`、`purposes`、`payment_app_ids`；用途与绑定列表去重并排序 |
| `locations.json` | `location_id`、`name`、可选 `branch_label`、`kind`、`app_ids`、必填可空的 `shipping_seconds`；非店铺及加油站的运输时长必须为 null |
| `goods.json` | `goods_id`、`name`、六个固定商品类别之一、正整数 `price_fen`、`store_ids` 列表或 `"all"` |
| `actions.json` | `action_id`、`name`、`description`、五个固定类别之一、`app_ids`、必填可空 `manner`、`communication_mode` |
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

登记只接受这六个文件与清单；环境规则副本、旧目录文件与额外清单路径以 `ENVIRONMENT_STALE_FILE` 拒绝。
目录按实际文件的路径、字节数和摘要绑定身份，发布使用原子写入。

<a name="resource-engine-注册校验与错误"></a>
## 注册校验与错误

`src/worldmock/catalog/admission.py` 仍是唯一的登记校验器，`load_catalog`、`worldmock validate --environment` 与数据仓的检查都调用它；失败抛 `RegistrationError`（`IntegrityError` 子类，带稳定 `code`），原因与日志一律英文，文字只定义在 `src/worldmock/catalog/errors.py` 的 `REGISTRATION_REASONS`。名称比较一律经 `channel_key`。严格模型的多余字段错误一律先映射为 `ENVIRONMENT_KIND_INVALID`，再按字段名映射其余错误（动作行上名为 `duration_seconds` 的多余字段因此是 `ENVIRONMENT_KIND_INVALID`，不是 `ENVIRONMENT_NUMBER_INVALID`）。下表按当前目录与场景准入条件定义错误。

| 码 | 条件 | 英文原因 |
| --- | --- | --- |
| `RULES_VERSION_MISMATCH` | 清单 `rules_version` ≠ `RULES_VERSION` | "The environment was published for a different rules version." |
| `ENVIRONMENT_STALE_FILE` | `catalog/` 下有六个目录文件与清单之外的文件（`foods.json`、`work_topics.json`、`affordances.toml`、`rules/` 等），或清单列出了六个之外的路径 | "The catalog contains a file that the manifest does not list." |
| `ENVIRONMENT_FILE_UNLISTED` | 清单缺少六个必需文件之一 | "The manifest does not list a required catalog file." |
| `ENVIRONMENT_IDENTITY_MISMATCH` | 文件 sha256 或字节数与清单不符；`catalog_content_sha256` 与重算不符（不再用于规则副本） | "A catalog file does not match the identity recorded in the manifest." |
| `ENVIRONMENT_PIN_MISMATCH` | 场景 `environment` 钉与正在加载的清单 `environment_id` 或 `environment_version` 不同 | "The scenario pins an environment that is not the one being loaded." |
| `ENVIRONMENT_ID_DUPLICATE` | 同一文件内标识重复；app 名、地点显示名或商品名在本文件内重复；同一绑定列表或清单路径重复 | "An identifier or display name is declared twice in one catalog file." |
| `ENVIRONMENT_REF_UNKNOWN` | `store_ids`、`app_ids`、`payment_app_ids` 或场景引用（已选地点、住处）指向未登记的对象 | "A binding refers to an object that is not registered." |
| `ENVIRONMENT_KIND_INVALID` | `kind`、`category`、`purposes`、`manner`、`summary_source.kind` 不在闭集；任何未知字段，含动作上的时长、时间窗、场所与效果字段；条目或绑定未按标识排序；非 `STORE_KINDS` 地点带 `app_ids`；内容带 `kind` 同样作为未知字段拒绝 | "A declared kind is outside the fixed set." |
| `ENVIRONMENT_NUMBER_INVALID` | `price_fen`、`length_m`、`series.episode` 不是正整数；`duration_seconds` 不是整数或小于 `CONTENT_MIN_SECONDS`；`size_bytes` 不是非负整数；`seed` 不是整数 | "A declared number is outside its allowed domain." |
| `GOODS_BINDING_MISSING` | 商品没有 `store_ids`，或显式列表为空 | "A goods item must be bound to stores or to all stores that may supply its category." |
| `GOODS_STORE_KIND_INVALID` | `store_ids` 含不属于 `STORE_KINDS` 的地点，或含类型不能供给该类别的店铺 | "A goods item is bound to a place that cannot supply its category." |
| `GOODS_BINDING_KIND_INCONSISTENT` | 某类型的店铺里有的绑定了该商品、有的没有 | "A goods item must be bound to every store of a kind or to none of them." |
| `APP_PURPOSE_INVALID` | `shopping ∈ purposes` 而没有店铺绑定该 app，或店铺绑定了没有 `shopping` 的 app；`payment_app_ids` 在非购物 app 上非空，或在购物 app 上为空；其中某项不是带 `payment` 用途的 app（app 可以列出自己） | "An app purpose does not match its store or payment bindings." |
| `APP_UNBOUND` | app 的 `purposes` 为空，且没有任何动作绑定它 | "An app must declare a purpose or be bound by at least one action." |
| `APP_NAME_COLLIDES_LOCATION` | app 名与地点显示名相同 | "An app name must differ from every place name." |
| `ACTION_APP_MANNER_INVALID` | `app_ids` 非空而 `manner` 为 null，或 `app_ids` 为空而 `manner` 非 null | "An action must declare a manner exactly when it is bound to apps." |
| `ACTION_CONTENT_EMPTY` | 某个动作没有内容，或某个（动作，app）对没有 `app_ids` 含该 app 的内容 | "An action and each of its apps must have at least one content." |
| `ACTION_NAME_COLLIDES` | 动作名与另一个动作、任一 app、地点显示名、商品名或 `WAYS` 的方式词相同 | "An action name must differ from every other action, app, place, goods and way name." |
| `CONTENT_ACTION_MISSING` | 内容没有 `action_id`，或它不是已登记的动作 | "A content must be bound to one registered action." |
| `CONTENT_APP_BINDING_INVALID` | 动作无 app 而内容 `app_ids` 非空；动作有 app 而内容 `app_ids` 为空或不是动作 `app_ids` 的子集 | "A content must list a non-empty subset of its action's apps, or none when the action has no app." |
| `CONTENT_SERIES_INVALID` | 同一 `series.title` 的各集不在同一个动作下，或 `app_ids` 不全相同；`(series.title, episode)` 重复 | "Episodes of a series must share one action and one app set and have unique episode numbers." |
| `CONTENT_SUMMARY_TOO_LONG` | `summary` 超过 `PUSH_SUMMARY_MAX_CHARS` | "A content summary exceeds the registration limit." |
| `CONTENT_SOURCE_URL_MISSING` | `summary_source.kind = real` 而 `url` 为空 | "A real summary must record its source url." |
| `MAP_TOO_SMALL` | 节点少于 `MAP_MIN_NODES` | "The map needs at least two places." |
| `MAP_EDGE_INVALID` | 自环、重复无序对、未知端点、非正长度、未按规范排序、`nodes` 与地点清单不等 | "The map has an edge that is not a sorted positive link between two registered places." |
| `MAP_DISCONNECTED` | BFS 不能到达全部节点 | "The map is not connected." |
| `MAP_NOT_REGENERATED` | `worldmock validate --environment` 或 `--check` 的重生成结果与发布的 `map.json` 不逐字节相同 | "The published map differs from the generator output for its seed." |
| `MERCHANT_ACCOUNT_MISSING` | 已选的 `STORE_KINDS` 地点没有 `merchant:<location_id>` 账户 | "A selected store has no merchant account." |
| `WORK_CONTENT_UNKNOWN` | 场景 `work_content_ids` 指向未登记的内容，或指向动作类别不是 `work_study` 的内容 | "A scenario assigns a work content that is not a registered work and study content." |
| `WORK_SCHEDULE_REQUIRED` | `work_content_ids` 非空的人物没有 `work_schedule` | "A person with assigned work content must declare a work schedule." |
| `WORK_CONTENT_REQUIRED` | 声明了 `work_schedule` 的人物 `work_content_ids` 为空 | "A person with a work schedule must be assigned at least one work content." |
| `WORK_CONTENT_INSUFFICIENT` | 人物被分配内容的 `duration_seconds` 之和小于其班次落在 `[start_at, end_at)` 内的秒数（班次按 `shift_on` 取，每个班次计 `starts` 到 `ends` 的全长） | "The assigned work content is shorter than the person's scheduled shifts in the run window." |
| `ACTION_COMMUNICATION_MODE_INVALID` | 绑定 app 的社交动作未声明 text 或 voice，或其他动作声明了通信方式 | "App-based social actions must declare text or voice communication; other actions must declare null." |
| `PARCEL_STATION_REQUIRED` | 长途配送场景缺少离人物家庭地址最近且被场景选中的驿站 | "The nearest parcel station to a persona home must be registered and selected by the scenario." |

<a name="resource-engine-图与地点关系准入"></a>
## 图与地点关系准入

| 校验 | 失败行为 |
| --- | --- |
| 节点至少两个 | `MAP_TOO_SMALL` |
| 节点与地点集合相等且排序；边无自环、重复或未知端点，`a < b`，边长正整数、边排序 | `MAP_EDGE_INVALID` |
| 图连通 | `MAP_DISCONNECTED` |
| 生成检查和环境校验重生成字节一致 | `MAP_NOT_REGENERATED` |

登记加载只验证发布图结构，不重生成。场景地点必须存在于目录，最短路可经过未选择地点；不存在道路范围、许可或未知路线准入。
对象与绑定继续由统一目录登记验证，同类型店铺菜单一致，`"all"` 不展开到住宅，店铺 app 绑定只允许具备供给能力的店铺。错误输出稳定码、英文原因和所属路径。
项目 `validate` 的覆盖率 0.8 门禁保留，输入覆盖不足不转成运行期默认内容。

<a name="resource-engine-加权连通图与路线"></a>
## 加权连通图与路线

- 生成规则（B-13）：数据仓调用命令或库，WorldMock 生成，数据仓发布；相同地点清单与种子在任何平台得到逐字节相同的 `map.json`。
- 校验：登记时查 `MAP_TOO_SMALL`、`MAP_EDGE_INVALID`、`MAP_DISCONNECTED`；`validate --environment` 与 `--check` 另查 `MAP_NOT_REGENERATED`；运行与恢复只读发布图。
- 最短路（B-12）由 `src/worldmock/catalog/graph.py` 提供 `shortest_paths`、`distance_meters(a, b)`、`path(a, b)`；`src/worldmock/life/wayfinding.py` 只剩图查询与 `go_step = "Go to <place>."`；多跳提示、`UNREACHABLE_STORE_STEP`、`UNREACHABLE_DELIVERY_STEP` 与不可达分支删除。
- 出行：`plan_trip(graph, origin, destination, mode)` 返回 `Trip(trip_id, path, distance_meters, duration_seconds, fare_fen)`；`duration_seconds = ceil(distance ÷ 方式速度)`；`src/worldmock/life/travel.py::planned_travel` 保留名字与单一来源地位，`pay_travel_fare` 只把 `route_id` 改成 `trip_id`，`update_travel` 读 `trip.distance_meters`；`ROUTE_DURATION_MISMATCH` 按图重算；同地点出行按 B-11 拒绝。
- 车费：步行 0；驾车 `max(DRIVING_FARE_MINIMUM_FEN, ceil(DRIVING_FARE_FEN_PER_METER × 米数))`，付给 `transport:service`。
- 出行身份：活动 `data{trip_id, path, distance_meters, travel_payment{trip_id, fare_fen, …}, arrival{location_id, access_status}}`；事件、`DeliveryState`、公开证据 `object_type = "route"` 的键与禁键表全部用 `trip_id`，`route_id` 不再出现。
- 工具层地点目标：每个其他已选地点各列一行，动作 步行、驾车 各带 `price`、`duration = ceil(长度 ÷ 速度)`、费力程度（MET 不变）；私有观察 `data.routes[{trip_id, origin_id, destination_id, mode, distance_meters, duration_seconds, fare_fen}]`、`data.locations[{location_id, name, kind}]`；坐标、`business_kind`、路线状态不再出现。
- 位置记录：出行事件与活动行引用 `origin_id`、`destination_id`、`path`、`distance_meters`；GPS 轨迹采样、球面距离、折线插值整段删除。

<a name="resource-engine-环境发布身份"></a>
## 环境发布身份

现行身份是目录格式 2、`RULES_VERSION = 2`、场景结构 8、世界结构 11、运行清单 4。
运行、检查点与恢复使用五字段 `environment_identity`：`environment_id`、`environment_version`、
`rules_version`、`catalog_content_sha256`、`map_sha256`。
规则文件只从引擎包读取，环境不发布副本，也不添加规则哈希；`code_sha256` 额外覆盖 `src/worldmock/data/` 下每个文件。
旧格式与旧结构号直接拒绝，不迁移、不双读。

新增 app、动作、内容与商品只需数据仓重新发布；新增类别、效果或规则须修改 WorldMock 并提升 `RULES_VERSION`。
地图结构和生成器规则版本都为 1；目录清单不列规则副本，也不带规则哈希。

<a name="resource-engine-目录发布"></a>
## 目录发布

`catalog.identity.refresh_manifest_identity` 安全读取实际文件，重算文件表与框架化目录摘要，经 `atomic_write` 写出规范 LF 清单。地图由 `worldmock environment generate-map` 发布；`--check` 只比较字节。
验收核对清单身份、原子发布失败保持旧目标，以及 `worldmock validate --environment` 的真实成功和逐类失败，不用声明摘要替代实际文件。

<a name="resource-engine-地点发现与候选呈现"></a>
## 地点发现与候选呈现

目录提供登记名称、类别与对象绑定；模拟引擎依据人物权限、实际地点和应用绑定构造冻结候选。
供给目录不保存人物库存、模型偏好权重或动态报价，人物可见名称与内部身份分离。
商品呈现窗口限制展示数量，不限制人物按登记名称购买未展示商品。

`query_world` 按冻结的公开候选求值，不揭示新世界事实、不推进时间、不占活动名额。
六键请求、应用筛选、实际可用性与分页统一见[模拟引擎的查询规则](04-simulation-engine.md#simulation-engine-查询筛选与返回精简)。
候选投影、排序键与游标统一见[人物的候选与呈现](06-agent.md#agent-候选与呈现合同)。

食品目标呈现完整标签「<店铺>出售的<食品>」，动作到店在场才可用；有 app 绑定才列远程下单。价格为到手价，说明写实际预计送达或 app 名，不显示报价版本或库存。

<a name="resource-engine-地点供给和价格"></a>
## 地点供给和价格

食品在 `foods.json` 定义 `food_id`、名称、`category`、固定 `price_fen` 与店铺绑定。类别只有 `snack`、`drink`、`meal`，同一食品全店同价，同类型店铺菜单完全相同。
`store_ids="all"` 只展开到食品店铺类型，住宅不出售食品；目录一次派生 `foods_by_store` 与店铺 app 索引。没有报价、价位、抖动、库存、营业或资格。

`from` 只接受 `null`、登记店铺显示名或 `apps.json` 的 app 名，来源与购买共用同一名称解析；`null` 不额外限制，食品完整标签仍约束标签店铺。
到店必须人在店内，`app_id=null`；app 配到店以 `SUPPLY_SOURCE_MISMATCH` 拒绝。远程下单要求店铺绑定 app；没有任何 app 是 `FULFILLMENT_NOT_SUPPORTED`，所点 app 未绑定该店是 `APP_NOT_BOUND`，app 的店不卖该食品是 `SUPPLY_NOT_AT_STORE`。
`from=null` 且店铺绑定多个 app 时取最小 `app_id`，写进 `details.notes`。app 来源从目录绑定派生，不读取手机配置。

<a name="resource-engine-按类型共享菜单"></a>
### 按类型共享菜单

类型菜单由显式绑定完整派生，运行不抽样、不补齐；旧逐店类别准入池与构建供给机制退役。

<a name="resource-engine-固定价格与配送费"></a>
### 固定价格与配送费

每个食品只有 `price_fen`，全店同价。没有种子、价格范围、店铺价格位置、随机抖动、Quote 或后续调价；规则版本绑定固定配送费与购买算子。
金额按固定食品单价和实际份数求值，货款与每单配送费一次转入商家账户；比较和付款使用同一口径。

<a name="resource-engine-验证边界"></a>
## 验证边界

加载检查必须验证真实文件的路径、字节数和摘要，严格模型拒绝未知字段、坏类型和无效绑定。
发布图的结构检查与按种子重新生成检查分别执行；场景选择不改变整张发布图的最短路。
场景准入在配置模块定义，商品资格和实际付款由模拟引擎再次核验。
