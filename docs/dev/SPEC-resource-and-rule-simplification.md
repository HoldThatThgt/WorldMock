<a name="spec-rrs-资源与规则引擎简化开发规格"></a>
# 资源与规则引擎简化开发规格

> 状态：未冻结草稿。2026-10-06 第 1 轮对抗审查发现 34 项阻断问题，原文登记在「第 1 轮审查的阻断发现」；逐项修订并经复审确认没有阻断问题之后才冻结为 FROZEN，此前任何代码、测试与验收结论不得依赖本文件（`docs/INDEX.md` 权威层级「未裁决方向」）。
> 日期：2026-10-06。
> 代码基线：35d0b610fb4a35304e66619048d7500d2d1e05c7。
> 来源：[提案](PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md)（用户原话、业界方案、十个设计决策点、差距与路线、必须钉住的行为）与它的三份证据 [引擎证据](research/resource-rule-simplification/engine-evidence.md)、[数据证据](research/resource-rule-simplification/dataset-evidence.md)、[成熟机制证据](research/resource-rule-simplification/industry-evidence.md)；本文按 2026-10-06 的用户裁决（采纳提案全部推荐）与对现行合同、规格、设计和代码的逐条核对收敛，提案中的建议值在「裁决记录」逐项定稿。
> 范围：分类与交换规则归 WorldMock 固定（`RULES_VERSION`）；环境只登记 app、地点、食品、娱乐内容、工作主题与三种绑定；随机加权连通图取代真实地图；同类店铺同菜单、外卖全地图可达；三类食品固定热量；娱乐只选 app、系统选内容、整点推送；工作只计工时、模型叙述；旧格式与失去生产用途的模块一并删除；数据仓按新格式重制正式环境。

<a name="spec-rrs-用户得到什么"></a>
## 用户得到什么

环境只需要登记「有什么」：地点有类型，食品分三类并绑定店铺，店铺绑定 app，娱乐内容绑定 app，工作只有主题和目标工时；名字真实、数值合成、规则固定。人物在一张随机连通的小地图上走路或开车，到店买吃的，或者通过美团这样的 app 让任何一家有 app 的店把饭送到地图上的任何地点；想娱乐就打开抖音这样的 app，看什么由 app 决定，整点收到推送后打开那个 app 先看推送的内容；工作时环境只数小时，怎么做的由人物自己在播报里说。WorldMock 定死每一类东西的行为和数值，WorldMock-Dataset 只提供名字、摘要、合成数值和绑定，不再维护品类树、交换规则、真实路线、营养成分、字幕和工作材料。

```mermaid
flowchart LR
    R[阶段 1 固定规则与环境格式<br/>exchange 常数、目录登记、随机图生成器] --> M[阶段 2 地图与出行<br/>场景 7、世界 10、最短路出行]
    M --> S[阶段 3 店铺、食品与外卖<br/>同类同菜单、app 外卖、三类热量]
    S --> A[阶段 4 娱乐与推送<br/>use_app、整点推送、内容进度]
    A --> W[阶段 5 工作主题<br/>工时累计、播报叙述]
    W --> P[阶段 6 观察、工具面与提示<br/>app 目标、十八条系统层]
    P --> E[阶段 7 记录、导出与查看器<br/>app-usage.jsonl、清单 3、质量 5]
    E --> D[阶段 8 文档与登记收口] --> X[阶段 9 数据仓交接与完整回归]
```

不做的事：不引入推荐系统、规则解释器、插件、兼容层、迁移工具或旧格式的读取分支；不模拟库存、营业时间、报价版本、做饭、游戏（TextWorld）、字幕、音轨、工作材料与独立验收；不给数据集任何覆盖热量、速度、费用、推送策略或配送规则的字段；不新造 app session、社交系统或第二套调度器；不改变人物画像、作息、时区、账本、检查点与导出的机制；不把真实 app 与作品名称当作对真实平台的任何承诺。

<a name="spec-rrs-裁决记录"></a>
## 裁决记录

用户于 2026-10-06 指示冻结本特性并采纳提案十个设计决策点的全部推荐。下面五张表合起来是本特性的全部裁决；编号在实现、台账与登记里原样引用。依据列写可引用的出处：提案章节、证据文件、参考文献（标题与 URL）；值是仿真假设的写「实验假设」并点名最近的参考。rulings-final 的 C-1 … C-8 优先于 SYNTHESIS 的 D-11 … D-52，后者优先于两份早期备忘（M、S、F、W、C、O、A、E 与 R-A … R-Q、Q1 … Q15）；被取代的早期条目在第四张表里注明取代它的编号。

<a name="spec-rrs-裁决提案决策表"></a>
### 提案的十个决策点

| 编号 | 定稿 | 依据 |
| --- | --- | --- |
| D1 | 地图全连通：随机标号树加 ⌊(n−1)/2⌋ 条额外边；至少两个地点；无自环、无重边、长度为正整数米；孤立点、多个连通分量、非正长度在登记时拒绝 | 提案第二章「地图只表达可达性和时间成本」；[引擎证据](research/resource-rule-simplification/engine-evidence.md)「地图依赖真实坐标与路线响应」（只查「至少一条边」不保证连通）；NetworkX `is_connected` 定义，见[成熟机制证据](research/resource-rule-simplification/industry-evidence.md)「加权连通图」 |
| D2 | 食品精确逐店绑定（`store_ids` 显式列表或 `"all"`），登记校验同类型店铺菜单完全一致，不一致以 `FOOD_BINDING_KIND_INCONSISTENT` 拒绝；`"all"` 只展开到食品店铺类型的地点 | 提案第二章「同类店铺同商品，外卖全地图可达」与「必须钉住的行为」（只给同类型的一家绑定必须拒绝；`all` 不包含住宅）；commercetools Product Selections 与 Saleor/Shopify 的共享目录机制（references §6：Saleor Channels https://docs.saleor.io/developer/channels/overview ；Shopify Multi-managed inventory https://help.shopify.com/en/manual/products/inventory/setup/multi-managed-inventory ） |
| D3 | session 复用现有地点停留与路途 session（`SessionState`），推送存在 session 上，不新建 app session | 提案第二章「推送沿用当前 session 和模拟时钟」；[数据证据](research/resource-rule-simplification/dataset-evidence.md)「缺少同 session 推送指定消费」对 `sessions.py` 边界的核对 |
| D4 | 当地时间 08:00 … 21:00 每个整点各一次推送机会；每人每小时至多一条；同一 session 同一 app 至多一条；均匀随机选一个合格 app，再均匀随机选它的一条内容；没有合格 app 就不产生、不补发 | 提案第二章「推送沿用当前 session 和模拟时钟」；用户原话「早 8-晚 10」；Braze Quiet hours https://www.braze.com/docs/user_guide/messaging/messaging_fundamentals/quiet_hours 与 Rate limiting and frequency capping https://www.braze.com/docs/user_guide/engagement_tools/campaigns/building_campaigns/rate-limiting/ ；Pielot 等 2014「An in-situ study of mobile phone notifications」https://dl.acm.org/doi/10.1145/2628363.2628364 （日均 63.5 条，说明上限保守） |
| D5 | 外卖送达 = 固定备餐 `DELIVERY_PREP_SECONDS` + 店铺到收货地最短路长度 ÷ `DELIVERY_METERS_PER_SECOND`，向上取整到秒；费用 = 商品价 + 每单一次 `DELIVERY_FEE_FEN`；距离只影响时间、不限制资格 | 提案第二章「同类店铺同商品，外卖全地图可达」；常数值见 C-1 |
| D6 | 不设库存、营业时间与报价版本：店铺全天服务、商品持续供应；删除 opening calendars、exceptions、quotes、stock、`price_change`、`supply_quote_change`、`product_release` | 提案「设计决策点」营业与供给（推荐项：最少状态）；[引擎证据](research/resource-rule-simplification/engine-evidence.md)「同类店铺允许差异供给」 |
| D7 | 删除做饭：`制作` 方式、`inventory_recipe`、`recipe_output_bindings`、食材与配方一并删除；数据集仍可声明名为做饭的生活活动，它不产生食品 | 提案「设计决策点」做饭是否保留（推荐项）；[引擎证据](research/resource-rule-simplification/engine-evidence.md)「食品依赖个体营养与配方」末段 |
| D8 | 三类食品每份固定常数：零食 200 千卡 / 300 秒，饮品 100 千卡 / 180 秒，正餐 600 千卡 / 1200 秒；登记为实验假设；`portion_duration`、60–14400 秒食用窗口、食品利用效率与能量上限保留 | 提案第二章「三类食品只保留“份”和固定热量」；值的锚点见 C-4 |
| D9 | 内容进度按 `content_id` 跨 app 共享；允许重看，完成后可再次抽到并记新一轮；不设排除集合与耗尽停止规则 | 提案「设计决策点」内容进度与重看（推荐项） |
| D10 | 工作工时 = 处于工作活动中的实际秒数累计；效率不再是完成门槛；累计达到目标工时即完成 | 提案「设计决策点」工作工时（推荐项）；Jira 的 Time Spent 对 Original Estimate 口径（Atlassian「Configuring estimation and tracking」https://confluence.atlassian.com/spaces/JIRASOFTWARESERVER/pages/938845319/Configuring+estimation+and+tracking ） |

<a name="spec-rrs-裁决综合决定"></a>
### 综合阶段的决定 D-11 … D-52（全部采纳，C-1 … C-8 覆盖处以 C 为准）

| 编号 | 定稿 | 依据 |
| --- | --- | --- |
| D-11 | 路途中给自己下外卖以 `ACTIVITY_PRECONDITION_UNMET` 拒绝（`unmet = delivery_destination`，原因 "Place the order before departing or after arriving."）；给他人的订单照旧送到收货人的收货地点；`start_activity` 不加收货地点参数 | 提案第二章「订单明确付款人、收货人和收货地点…路途中下单必须明确一个地点」与「收货地点不作参数」的现行合同 |
| D-12 | 一种活动类型 `use_app` 取代 `watch`、`play_game`、`browse`；方式「打开」，同义词 刷、刷一刷、用一下、打开看看；对象前缀「应用：」 | 提案第二章「娱乐选择 app，系统选择内容」；[数据证据](research/resource-rule-simplification/dataset-evidence.md)「娱乐仍直接选择内容并读取分段素材」 |
| D-13 | 删除浏览活动与供给可见性（`ActivityKind.BROWSE`、`BROWSE_TARGETS`、可见凭据与 TTL、`SUPPLY_NOT_VISIBLE`、`app_browse` 足迹、`catalog` 目标类）；菜单按类型固定，由 `query_world` 与按名购买发现 | 提案第三章「应整体退出当前运行链」；[引擎证据](research/resource-rule-simplification/engine-evidence.md)「同类店铺允许差异供给」 |
| D-14 | app 是始终列出的工具层目标（`kind = app`），不是候选、不轮转、不进 `callable_index`；没有内容的 app 列出但 `available=false`（`APP_CONTENT_EMPTY`） | 提案第二章「空内容集合明确显示该 app 没有娱乐内容」 |
| D-15 | app 使用时长固定 `APP_USE_DEFAULT_SECONDS`，活动内内容完成即抽下一条（接续）；不加时长参数；暂停保留内容；计划结束或主动结束才结束；重新打开是新活动、推送优先 | 提案第二章「完整结束后若使用时长尚未耗尽，随机抽取下一条并继续消耗剩余时长」；值见 C-5 |
| D-16 | `app_push` 是唤醒原因（只给醒着的人；不进睡眠允许集合；只在有合格 app 时产生），同时是带完整摘要的进度层记录 | 提案第二章「推送事件复用既有调度与唤醒规则」；`message_arrival` 先例 |
| D-17 | 睡眠中照常生成推送并存到 session 上（醒来后可见），不唤醒 | 提案第二章「遵守当前睡眠和活动中断约束」 |
| D-18 | 娱乐缓解为按内容类型的固定速率 `CONTENT_RELIEF_PER_HOUR[kind]`；删除 `taste_weights`、`relief_preference`、曝光除数与 `entertainment_relief_preference_floor` | 提案第二章「Dataset 不再提交…食品偏好权重」与「人物依据名称、价格、摘要和画像选择」；值见 C-3 |
| D-19 | `ContentProgress{content_id, position_seconds, completed_rounds}`；活动 `data.played[]`；`activity_progress.data.content` 取代 `revealed` | 提案第二章「五种内容统一使用“摘要＋消费时长＋已消费时间”」 |
| D-20 | 工作主题是目录文件 `work_topics.json{topic_id, topic, target_hours}`，场景只以 `agents[].work_topic_ids` 分配；没有截止、预约、通知邮件与要求地点（地点 = `work_schedule.workplace_location_id`，有主题的人物必须声明工作安排）；完成由活动推导；完成写 `work_topic_completed` 事件与 `work_completed` 进度记录；删除 `WORK_AVAILABLE`、`TASK_EXPIRED`、`related_task_id`、`task_available`、`task_due_soon` | 提案第二章「工作只计工时，模型负责描述」与「本次重设计的五类目录和三种绑定」（工作主题由场景分配、沿用工作地点和班次） |
| D-21 | 删除游戏与 TextWorld 链（`textworld` 依赖、Linux-x86_64 条款、`play_game`）；Windows 因此能收集各测试层 | 提案第三章「TextWorld 独立游戏链…应整体退出」 |
| D-22 | 叙述通道仍是 `report_status.items[].summary`；工作活动目标带 `last_report`（上一次 `progress_note`），任务目标 `contract{topic, target, done, remaining}`；不写记忆层；系统层工作一条改写 | 提案第二章「同一段工作只在既有进度播报时生成描述」 |
| D-23 | 唤醒词表删除 `task_available`、`task_due_soon`、`work_review_result`、`location_hours_change`，追加 `app_push`，共 22 个；冻结句「成员永不删除」改为「只经版本表与九个同步点增删」 | 提案第三章「删除失去生产用途的…字段」；世界结构 10 拒绝全部旧运行，保留死名字没有用途 |
| D-24 | 身体常数归 WorldMock Python 常数（`exchange/body.py`），不读 `resources.jsonl`、不新增 TOML；`BodyConfig.source_kind = "worldmock:<RULES_VERSION>"` | 提案第二章「两个引擎各自只做一件事」归属表 |
| D-25 | 固定规则打包为 `src/worldmock/exchange/`，带 `RULES_VERSION`；运行清单 `environment_identity{environment_id, environment_version, rules_version, catalog_content_sha256, map_sha256}` 取代四个品类身份字段与 `map_dataset`；场景钉 `environment{environment_id, environment_version}` | 提案第二章「保留环境版本、文件清单、内容哈希与系统规则版本等必要身份信息」；[数据证据](research/resource-rule-simplification/dataset-evidence.md)「主提案复核」末段 |
| D-26 | 注册表就地重写 `src/worldmock/catalog/`（`affordances.py` 保留），删除 `src/worldmock/categories/`；地图模型放在 `catalog/models.py`，删除 `life/map_models.py`、`life/map_data.py` | 导入扰动最小；提案第三章「资源目录只存对象及其关系」 |
| D-27 | `progress_report_interval_seconds` 与候选常数仍是场景参数（节律，不是分类或交换） | 提案第二章「保留…作息和场景时区等既有场景输入」 |
| D-28 | 地点不再声明 `location_kinds`，由 `kind` 经 `PLACE_KIND_ENVIRONMENTS` 派生；生活活动条目保留自己的 `location_kinds` | 提案第二章五类目录表（地点最小内容只有标识、名称、类型） |
| D-29 | `LocationKind` 闭集 `home, office, park, fitness, restaurant, cafe, convenience_store, supermarket`；`merchant`、`wet_market` 退役；后四个是 `FOOD_STORE_KINDS` | 提案第二章「建议餐馆、咖啡店、便利店、超市分别作为内置食品店铺类型」 |
| D-30 | 场景保留 `locations[]` 选择；整张发布图加载，最短路可经过未选地点；登记校验跑全目录；「全地图可达」= 全部已选地点 | 提案追加原话「外卖全地图可达」与「“全地图可达”覆盖所有已注册地点」 |
| D-31 | 生成器是库 `catalog/mapgen.py` 加命令 `worldmock environment generate-map`；运行时加载器只校验结构、不重生成；`worldmock validate --environment` 与数据仓 `--check` 逐字节比对发布的 `map.json` 与重生成结果 | 提案第二章「加载和恢复直接读取固定图，不依据种子重新生成」 |
| D-32 | `trip_id = "<origin>-><destination>:<mode>"` 处处取代 `route_id`；活动数据保留 `path` 与 `distance_meters`；`route_progress_meters` 仍是图上米数 | 提案第二章「位置和出行导出同步改为地点、路径和距离」 |
| D-33 | 每个已选食品店铺在场景 `accounts[]` 声明一个 `merchant:<location_id>` 账户（缺则加载拒绝）；配送费与货款在同一笔转账付给该商家；`DeliveryState.seller_account_id` 保留为证据 | 现行 `_validate_accounts` 对卖家账户的机制；提案第二章「费用为商品价加系统固定配送费」 |
| D-34 | `from` 为 null 且店铺绑定多个 app 时取最小 `app_id`，写进 `details.notes`；`from=<app>` 配方式「到店」以 `SUPPLY_SOURCE_MISMATCH` 拒绝 | 确定性优先；提案第二章「从 app 选择店铺与食品」 |
| D-35 | 保留 `SUPPLY_NOT_FOUND`、`SOURCE_NOT_FOUND`、`SUPPLY_SOURCE_MISMATCH`、`SUPPLY_NOT_AT_STORE`、`FULFILLMENT_NOT_SUPPORTED`（稳定码，含义改到食品与店铺上）；新增 `APP_NOT_BOUND`、`APP_CONTENT_EMPTY`；登记错误码归 `RegistrationError` | 宿主 `NAME_REJECTION_CODES` 与质量报告 `name_rejections` 读这些名字 |
| D-36 | 既无店铺也无内容绑定的 app 在登记时以 `APP_UNBOUND` 拒绝；覆盖率计全部登记 app 名 | 提案第二章「仅有名字不会自动获得外卖、娱乐或其他系统能力」 |
| D-37 | 每个 app 恰好一个真实 `name`；`channel_key` 归一处理空格与大小写；不设别名表 | 提案第二章「app：标识、真实名称」 |
| D-38 | 覆盖率目标：习惯关键词、食品名、媒体 = 五个内容类型标签 + app 名（精确）；删除 `content_genre`；0.8 门槛不变 | `docs/CONTRACTS.md#contracts-人物卡片覆盖率产出` 的现行门槛 |
| D-39 | 运行输入警告闭集六个码：`CONTENT_EMPTY_WITH_MEDIA_TASTES`（改为「没有任何 app 绑定内容」）、`COVERAGE_BELOW_THRESHOLD`、`CARD_FIELD_OVERRIDDEN`、`CARD_FIELD_CONFLICT`、`PERSONA_AGREEMENT_LOW`、`LOCATION_NAME_SUSPICIOUS` | 四个口味、游戏、路线码随其来源删除；坐标状名字仍是质量警告 |
| D-40 | 人物卡片不变（`tastes` 仍是散文） | 提案第二章「保留人物画像」 |
| D-41 | 场景 `messages[]` 保留为普通邮件，删 `related_task_id`；`read_mail` 与 `mail_headers` 归属不变 | 提案第二章「已有消息等行为使用同一 app 身份；本提案不另造社交系统」 |
| D-42 | 摘要来源：`summary_source{kind: real 或 synthetic, url, notice}`；价格、时长、边长按合同即合成（清单层面声明一次，不逐字段标记）；不设发布评审记录 | 提案第二章「真实摘要保留来源信息，合成摘要明确标记合成；价格、时长和地图长度标记为仿真数值」；形状以 C-8 为准 |
| D-43 | 真实电视剧按集登记一个 `content_id` 并带 `series{title, episode}`，电影一部一个；显示名可重复，身份不重复 | 提案第二章「真实电视剧按集登记，电影按部登记」「同名内容按作品与集数区分」 |
| D-44 | 新导出工件 `app-usage.jsonl`（清单 3）；数据仓 `simulate_phone.py` 变成它与 `purchases.jsonl.app_id` 的纯投影；`labelkit_input.phone_rows` 接受 `source_kind = "worldmock"` | 提案第三章「手机导出直接读取实际动作中的 app、店铺和内容身份」 |
| D-45 | LabelKit 窗口里 `app_push` 的公开投影 `{kind, app, kind_label, summary}`（第一种非空的环境事件投影） | `docs/dev/SPEC-labelkit-delivery.md#spec-labelkit-delivery-证据窗口与输入结构` 的窗口机制 |
| D-46 | 一份足够丰富的人工环境让全部行为用例与完整日用例不带 `--environment-root` 通过；重制后的正式环境仍是全量层的回归输入；第三份完整日基线在人工环境上冻结（平台见 C-7） | 提案第三章「最小生活闭环：人工小环境含同类门店、有／无 app 店铺和远距离收货点」 |
| D-47 | 第六个特性台账：`tests/fixtures/rrs-acceptance.json`、`tests/rrs_acceptance.py`、`tests/test_rrs_acceptance.py`，机器登记键 `resource-rule-simplification`；五个既有特性的矛盾行随文删除；`mutation-symbol-baseline.json` 字节冻结，守卫放宽为「登记 ∩ 基线」 | `docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-验收台账` 的台账形状 |
| D-48 | 模型阶段只有 `action`；校验器拒绝带 `work_review` 的清单（与 `decision_statement` 同列）；叙述不设新阶段键 | 提案第二章「不为每秒、每个片段新增独立模型调用」 |
| D-49 | 播报输入：任务 `contract` 的主题、已做、剩余，活动目标的 `last_report`（跨 session 保留）；`recall_task_summary` 仍返回 `note_history` | 提案第二章「已有进度播报时点传入主题、已做时间、剩余时间和上次描述」 |
| D-50 | `work_start_refusal` 保留 "Less than one minute of the topic remains; work cannot start."（`ACTIVITY_DURATION_INVALID`），同时做两件事规格 R4 的恢复次序（地点 → 主题上限 → 名额 → 占用）逐字保留 | `docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-裁决记录` R4 |
| D-51 | app 目标的 `state` 只写待消费推送或内容条数；逐内容进度只出现在结果与 `settled` 句里，不列标题清单 | 提案「必须钉住的行为」个人不能直接指定内容 |
| D-52 | 随机抽取用 `WorldState.random_state` 的哈希链：推送键 `scheduled_id`，打开键 `action_id:app_id`，接续键 `activity_id:draw_index`；`random_state` 只在今天推进它的地方推进；恢复与重放抽到同一序列 | 提案「必须钉住的行为」随机和状态可恢复；NumPy Bit generators https://numpy.org/doc/stable/reference/random/bit_generators/index.html 与 Mesa `Model` seed 约定 https://mesa.readthedocs.io/stable/apis/model.html |

<a name="spec-rrs-裁决最终覆盖"></a>
### 最终覆盖 C-1 … C-8

| 编号 | 定稿 | 依据 |
| --- | --- | --- |
| C-1 | `WALKING_METERS_PER_SECOND = 1.2`、`DRIVING_METERS_PER_SECOND = 7.0`、`DELIVERY_METERS_PER_SECOND = 5.0`、`DELIVERY_PREP_SECONDS = 600`、`DELIVERY_FEE_FEN = 500`；驾车车费 `max(500, ceil(0.2 × 米))` 分，步行 0 | 步行：FHWA MUTCD 4I.06「a walking speed of 3.5 feet per second」https://up.codes/s/pedestrian-intervals-and-signal-phases 与 Browning 等 2006 偏好步速 1.4–1.47 m/s https://onlinelibrary.wiley.com/doi/abs/10.1038/oby.2005.103 ；驾车：高德《2024 年中国主要城市交通分析报告》自由流最高 48.75 km/h 为上界 https://www.sohu.com/a/862338037_122020073 （实验假设）；配送：GB 17761-2018 电动自行车 25 km/h 上限 https://codeofchina.com/standard/GB17761-2018.html （实验假设）；备餐：美团「订单分配」算法公开 https://www.meituan.com/news/NN25011005301105X 与央广网美团配送时间规则 http://china.cnr.cn/gdgg/20210912/t20210912_525598982.shtml （出餐是独立阶段；实验假设）；配送费：美团外卖佣金说明 https://www.meituan.com/news/NN241225051008799 与 CBNData 每单配送收入 4.71 元 https://www.cbndata.com/information/240527 （实验假设）；车费沿用现行闭集算子值 |
| C-2 | `MAP_MIN_NODES = 2`；额外边 ⌊(n−1)/2⌋（两地点图恰一条边、无重边）；边长为 [200, 2000] 米的均匀随机整数；生成器 = 带种子的 NetworkX `random_labeled_tree` 加不重复、非自环的随机额外边 | 上海市第六次综合交通调查（步行接驳约 7.5 分钟）https://jtw.sh.gov.cn/xwfb2/20220216/442a7885f34b4d2996ba1a48333d808d.html ；高德 2024 年三季度报告（超大城市步行接驳 1,185 m）http://www.news.cn/tech/20241115/53cbaa0ce8d24f8093f8b0bcb056d203/c.html ；树加额外边的配方 https://gist.github.com/bwbaugh/4602818 ；NetworkX `random_labeled_tree` 见[成熟机制证据](research/resource-rule-simplification/industry-evidence.md)；额外边数是实验假设（平均度约 3） |
| C-3 | `CONTENT_RELIEF_PER_HOUR`：article 0.2、image_text 0.2、short_video 0.3、long_video 0.4、live 0.3（每小时消费的娱乐需要单位） | 实验假设：现行环境条目聚集在每小时 0.2；登记到 `docs/EXPERIMENT.md#experiment-实验假设声明` |
| C-4 | 零食 200 千卡 / 300 秒、饮品 100 千卡 / 180 秒、正餐 600 千卡 / 1200 秒；`portion_duration`、60–14400 秒食用窗口、食品利用效率与能量上限保留 | 中国营养学会《中国居民膳食指南 2022》午餐占全日能量 30–40 % http://dg.cnsoc.org/article/04/wDCyy7cWSJCN6pwKHOo5Dw.html ；中国学龄儿童膳食指南 2022 零食不超过全日 10 % http://dghb.dg.gov.cn/zsjg/dzsjbyfkzzx/jkzt/spaqyhjws/content/post_4206592.html ；国家统计局第三次全国时间利用调查 用餐 1 小时 43 分/日 https://www.stats.gov.cn/sj/zxfb/202410/t20241031_1957216.html ；饮品热量与零食、饮品时长是实验假设 |
| C-5 | `APP_USE_DEFAULT_SECONDS = 1800`，活动内内容接续 | 中国网络视听协会《中国网络视听发展研究报告（2025）》短视频人均单日 156 分钟分多次 http://sc.people.com.cn/n2/2025/0326/c345167-41176696.html ；实验假设 |
| C-6 | 推送网格：当地整点 08:00 … 21:00；每人每小时至多一条；每 app 每 session 至多一条；`app_push` 是醒着的人的唤醒原因；睡眠中照常产生并存储、不唤醒；摘要在登记时不超过 200 字符 | 同 D4；08:00–22:00 窗口是用户要求 |
| C-7 | 第三份完整日基线在本机（Windows）由人工环境上两次一致运行生成，记录平台；POSIX 与 macOS 回执在「门禁与平台」登记为未执行（跨平台提案未实施） | `docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-门禁与平台` 的平台口径 |
| C-8 | 每条内容必带 `summary_source{kind: real 或 synthetic, url: str 或 null, notice: str 或 null}`，`kind = real` 时 `url` 必填；食品不带摘要（只有名称与价格） | 提案第二章「真实摘要保留来源信息，合成摘要明确标记合成」；用户原话未要求食品摘要 |

<a name="spec-rrs-裁决早期备忘"></a>
### 两份早期备忘的条目与处置

每条写它并入哪个编号或被谁取代；被取代的条目不再是依据。

| 编号 | 定稿 | 依据 |
| --- | --- | --- |
| R-A | 每个食品一个 `price_fen`（每份正整数分），全店同价；外卖费 = 商品价 + `DELIVERY_FEE_FEN` | 并入 D2、D5、C-1 |
| R-B | 出行耗时 = 最短路长度 ÷ 方式速度向上取整；同地点不出行；车费闭集算子常数归 WorldMock | 并入 D5、C-1；同地点的拒绝见 B-11 |
| R-C | 稳定排序的地点标识 + 种子生成随机标号树再加额外边；发布无向加权图；加载核对发布图等于重生成；运行与恢复只读图 | 并入 D1、D-31、C-2 |
| R-D | 地点字段只保留标识、名称、`branch_label`、`kind`、app 列表；坐标、`business_kind`、品牌、营业声明、资格、`affordances`、逐地点来源删除；`location_kinds` 逐地点保留一项被 D-28 取代 | 并入 D-28、D-29 |
| R-E | 非食品商品与原料退出目录；五类目录是闭集；生活活动保留 | 并入 D7、「环境格式」 |
| R-F | `apps.json` 只有标识与名称；店铺→app 写在地点，内容→app 写在内容，食品→店铺写在食品；反向索引登记时派生 | 并入「环境格式」 |
| R-G | 内容目录字段；电视剧按集登记；实况为有限时长片段；不要求字幕、音轨、分段。`summary_origin` 字段名被 C-8 的 `summary_source` 取代 | 并入 D-43、C-8 |
| R-H | 工作主题写在场景并带 `opens_at`、`due_at` 一项被 D-20 取代（目录文件、场景只绑定标识、无截止） | 被 D-20 取代 |
| R-I | 新活动类型、方式「打开」、固定时长、推送优先其余随机、暂停继续、提前结束照常；删除 `watch`、`play_game`、`browse` 与 TextWorld | 并入 D-12、D-15、D-21 |
| R-J | 整点推送事件、只保留当前 session 的推送、打开先消费推送内容、任一绑定 app 完成即解除、唤醒原因 `app_push` 不进睡眠允许集合、进度层记录。「睡眠中不产生」一问被 D-17 取代（照常产生、不唤醒）；状态落点被 D-3 取代（`SessionState.app_pushes`） | 并入 D4、D-16、D-17、C-6 |
| R-K | 外卖全地图可达，删除 `delivery_destination_ids`、配送路线、`route_id`；到店购买即时交付 | 并入 D5、D-30、D-32 |
| R-L | 购买请求形状不变（方式 到店、远程下单、购买；`from` 为 null、店铺显示名或 app 名；`prefer` 沿用）；M3「能服务」= 到店：人在该店；远程下单：店铺绑定了 app（`from` 为 app 时是该 app） | 并入「店铺、食品与外卖」 |
| R-M | 结构版本整体升级、旧版本一律拒绝；具体号由 D-25 与「状态、事件、检查点与导出」给出（目录清单 `format_version: 1` 取代目录 6.0；地图 `schema_version: 1` 取代地图 4） | 并入 D-25；号被取代 |
| R-N | 删除品类契约与交换规则文件；资源定义归 WorldMock；身体实例仍由场景 `body` 给出 | 并入 D-24、Q3 |
| R-O | 手机交接：`purchases.jsonl` 与订单带 `app_id`；删除 `phone_simulation_config`、`PhoneSimulationCoverage`、`coverage_apps.py`。「只在 purchases 加列」被 D-44 取代（另加 `app-usage.jsonl`） | 并入 D-44 |
| R-P | 出行事件与活动记录引用 `origin_id`、`destination_id`、`path`、`distance_meters`；删除轨迹采样与坐标 | 并入 D-32 |
| R-Q | 数据仓正式环境按新格式重制是数据仓的工作；本仓库只保留人工最小测试环境、环境格式合同、`worldmock validate` 的环境校验与交接清单；完整层 `--environment-root` 回归在发布后执行、如实登记 | 并入 D-46、「数据仓交接」 |
| Q1 | 新包 `worldmock.exchange`；删除 `worldmock.categories`；`CategoryError` 的后继是 `RegistrationError`（名字由 D-35 定）；`DeepFrozenModel`、`require_finite_number` 移到 `base_models.py`，`Conservation` 移到 `resources/models.py`；来源指针类型删除 | 并入 D-25、D-26、D-35 |
| Q2 | `PlaceKind` 闭集八个；`merchant`、`wet_market` 删除；`FOOD_STORE_KINDS` 四个；活动环境闭集不变 | 并入 D-29（枚举名定为 `LocationKind`） |
| Q3 | 身体参数为 WorldMock 常数：基础代谢、各活动 MET、饥饿阈值、睡眠与娱乐速率；`resources.jsonl` 与逐资源消耗规则删除；生活活动保留自己的 `met_value`；场景保留身体实例（质量、初始能量、初始睡眠债、初始娱乐需要）。效率曲线归 `sleep.py` 现行常数；`entertainment_relief_preference_floor` 随 D-18 删除 | 并入 D-24 |
| Q4 | 200/100/600 千卡与 5/3/20 分钟；一个 `price_fen` 全店相同 | 并入 D8、C-4 |
| Q5 | 显式 `store_ids` 列表或 `"all"`；`FOOD_BINDING_KIND_INCONSISTENT` | 并入 D2 |
| Q6 | 做饭整段删除；数据集可声明名为做饭的生活活动，不产生食品 | 并入 D7 |
| Q7 | 无库存、无营业时间、无报价 | 并入 D6 |
| Q8 | 送达 = 备餐 + ceil(最短路米数 ÷ 配送速度) | 并入 D5 |
| Q9 | 内容来源字段（名为 `summary_origin` 与 `source`）被 C-8 取代；价格、时长、边长按合同合成；环境身份 = `environment_id`、版本、文件清单与 SHA-256；`SourceRecord`、JSON-Pointer 覆盖与 `ReviewRecord` 删除 | 并入 D-42、C-8 |
| Q10 | 单一根 `manifest.json` 一项被取代：`catalog/manifest.json` 与 `corpora/manifest.json` 仍是两份文件；`catalog/` 下未列出的文件以 `ENVIRONMENT_STALE_FILE` 拒绝 | 被「环境格式」取代 |
| Q11 | 隐式账户一项被 D-33 取代：场景显式声明 `merchant:<location_id>`、`employer:*`、`transport:service`、`external:scenario` | 被 D-33 取代 |
| Q12 | `category_matches` 整体删除 | 并入「删除清单」 |
| Q13 | 世界素材品类要求里仍成立的部分（每种已选地点类型存在、生活活动字段、联系人）作为 WorldMock 常数由 `worldmock validate` 检查；数据集声明的 `category_requirements` 删除；只对正式环境成立的数量写进「数据仓交接」 | 并入「环境格式」「数据仓交接」 |
| Q14 | 车费与配送费为 WorldMock 常数并入 `RULES_VERSION` | 并入 C-1 |
| Q15 | 场景钉 `environment_manifest_sha256` 一项被 D-25 取代（钉 `environment_id` 与 `environment_version`；完整身份在运行清单） | 被 D-25 取代 |
| M1 | 发布图必须连通；加载检查 ≥ 2 节点、连通、无孤立点、无自环、无重边、正整数边长 | 并入 D1、C-2 |
| M2 | WorldMock 拥有生成器；校验与世界装配核对发布边集等于重生成；运行与恢复只读图。「世界装配也重生成比对」被 D-31 取代为「加载器只校验结构」 | 并入 D-31 |
| M3 | `location_kinds` 逐地点保留 一项被 D-28 取代 | 被 D-28 取代 |
| M4 | 删除场景 `locations[]` 一项被 D-30 取代 | 被 D-30 取代 |
| M5 | 全连通后多跳提示与 UNREACHABLE 常量删除；下一步只写地点 | 并入「地图与出行」 |
| M6 | 路途中订单送到路途目的地 一项被 D-11 取代（拒绝） | 被 D-11 取代 |
| M7 | 常数清单归 WorldMock 并登记 EXPERIMENT.md、绑定 `RULES_VERSION` | 并入 C-1、C-2 |
| M8 | 车费由 WorldMock 常数计算；`TravelFareRule`、出行服务对象与出行配对删除；车费付给 `transport:service` 账户 | 并入 C-1、D-33 |
| M9 | 营业时间删除 | 并入 D6 |
| M10 | 出行身份 `trip_id`、`path`、`distance_meters`；`route_id` 从事件、`DeliveryState` 与禁键表消失 | 并入 D-32 |
| M11 | 查看器显示起点、终点与路径名称，不画图 | 并入「记录、导出与查看器」 |
| M12 | 人工环境至少 5 个地点、生成边、两个 app、三类食品、五类内容、一个工作主题 | 并入 D-46、B-7 |
| S1 | `opening_hours.py`、`MERCHANT_CLOSED`、`MERCHANT_OPENING_UNKNOWN`、`ELIGIBILITY_CLOSED_HOURS`、`location_hours_change` 删除 | 并入 D6、D-23 |
| S2 | 配送常数同 M7；EXPERIMENT.md 的配送假设改写 | 并入 C-1 |
| S3 | 隐式 `store:`、`app:` 账户 一项被 D-33 取代；`required_qualifications` 与 `LOCATION_ACCESS_DENIED` 删除照旧 | 被 D-33 取代；删除并入「删除清单」 |
| S4 | 配送费付给 app 账户 一项被 D-33 取代（付给商家） | 被 D-33 取代 |
| S5 | 多 app 取声明顺序首个 一项被 D-34 取代（最小 `app_id`）；`from=<app>` 配到店被 D-34 定为拒绝 | 被 D-34 取代 |
| S6 | 可见凭据删除；商品呈现用现有候选窗口与按 `food_id` 轮转；按名购买不受限 | 并入 D-13 |
| S7 | 到店购买并进食由逐地点持有实现；不设合并动作 | 并入「店铺、食品与外卖」 |
| S8 | 同 M6 | 被 D-11 取代 |
| S9 | 显式 `stores` 或 `"all"`；`FOOD_BINDING_KIND_INCONSISTENT` | 并入 D2 |
| S10 | 做饭删除 | 并入 D7 |
| S11 | 食品常数 | 并入 D8 |
| S12 | 保留五个稳定码；无 app 店铺不能外卖：`FULFILLMENT_NOT_SUPPORTED`，原因 "This store has no delivery app."；「不设 `APP_NOT_BOUND`」被 D-35 取代 | 并入 D-35；部分被取代 |
| S13 | 运行清单身份字段 一项被 D-25 取代（`environment_identity`）；`purchases.jsonl` 带 `app_id`（到店为 null），只记真实订单 | 并入 D-25、D-44 |
| F1 | 身体常数归 WorldMock；`resources.jsonl` 删除 | 并入 D-24 |
| F2 | 利用效率、能量上限与消耗保留 | 并入 D8 |
| F3 | 做饭删除 | 并入 D7 |
| F4 | 工具层食品 `state` 写类别与固定热量 | 并入「三类食品与进食」 |
| F5 | 60 秒下限与 14400 秒上限保留 | 并入 D8 |
| F6 | 耐用品退出目录；`initial_inventory` 只装食品 | 并入 R-E、「环境格式」 |
| F7 | 动机层正餐候选由类别判定 | 并入「三类食品与进食」 |
| F8 | 系统层第 15、16 条保持原意与分类名；`prompt_sha256` 重钉。本规格进一步定为第 14、15、16 条逐字不动（B-1） | 并入 B-1 |
| F9 | 家庭储备沿用 `initial_inventory`；引擎下限检查删除 | 并入「环境格式」 |
| F10 | 份量措辞与 1e-9 库存精度不变 | 并入「三类食品与进食」 |
| F11 | `RULES_VERSION` 记入运行清单与检查点，不符拒绝打开、恢复与重放 | 并入 D-25（`RULES_VERSION` 是整数常数） |
| W1 | 实际秒数；`effective_work_seconds`、效率因子、笔记倍数与负荷键删除；疲劳仍是身体规则 | 并入 D10 |
| W2 | `AgentState.work_progress` 一项被 D-20 取代（由活动推导，不新增状态）；完成事件保留；「不能再开始已完成主题」的码见 B-6 | 被 D-20 取代 |
| W3 | 截止与预约保留 一项被 D-20 取代 | 被 D-20 取代 |
| W4 | `required_skill`、`minimum_skill`、`AgentConfig.skill_levels` 删除 | 并入「删除清单」 |
| W5 | 保留死成员 一项被 D-23 取代；`app_push` 追加在 `break_end` 之后照旧 | 被 D-23 取代 |
| W6 | 工作目标 `state` 写主题、已做、剩余与上次叙述 | 并入 D-22、D-49 |
| W7 | 叙述持久化不变（`progress_note`、`self_notes`），不新增导出工件 | 并入 D-22 |
| W8 | 雇主账户仍是场景声明账户 | 并入 D-33 |
| W9 | 材料规则与 `WorkMaterialRequirements` 删除；主题按人物可选 | 并入 D-20 |
| W10 | 人工环境带主题、工资与工作安排 | 并入 B-7 |
| W11 | 用过 `submit_work`、材料与评审的台账行连同测试退役，机器登记同一提交更新 | 并入「测试与夹具」 |
| C1 | 推送资格看发出时刻的当前 session；到达当秒的推送属于新的停留 session | 并入 D-3 |
| C2 | 保留无来源成员 一项被 D-23 取代 | 被 D-23 取代 |
| C3 | `app_push` 给醒着的人决策机会；活动内内容完成静默（下一条，不决策） | 并入 D-15、D-16 |
| C4 | 睡眠中照常产生并存储、不唤醒 | 并入 D-17 |
| C5 | 夏令时按 `duty_edges`/`clock_epoch` 先例（跳过的小时取跳变前偏移，重复的取第一次） | 并入「娱乐」 |
| C6 | 整点 08:00 … 21:00 每日 14 个；编译行满足 start < due < end | 并入 C-6 |
| C7 | 推送环境事件沿用 `_process_scheduled` 约定，只对被推送的人可见 | 并入「状态、事件、检查点与导出」 |
| C8 | 删除三种供给类预排事件；新增 `APP_PUSH` | 并入 D6、「状态、事件、检查点与导出」 |
| C9 | 工时按经过秒数；`activity_efficiency` 一项被 D10 取代（随效率因子删除） | 部分被 D10 取代 |
| C10 | 某整点没有合格 app：事件结算为未推送，不写人物可见记录 | 并入「娱乐」 |
| C11 | 删除与被删特性绑定的变异行；让「等待」成为例外的登记原文逐字保留 | 并入「测试与夹具」 |
| O1 | 时长 30 分钟常数，内容结束接续，播报照常 | 并入 D-15、C-5 |
| O2 | 同 S5 | 被 D-34 取代 |
| O3 | app 始终列出；有内容的列方式「打开」；只有店铺的 app 不列动作；`state` 写待消费推送或 null。`state` 的取值被 D-51 细化 | 并入 D-14、D-51 |
| O4 | 逐内容进度不进工具层；`ContentProgress` 字段名被 D-19 取代（`completed_rounds`） | 并入 D-51；字段名被取代 |
| O5 | 主题自开放时刻可见、经 `WORK_AVAILABLE` 一项被 D-20 取代（运行开始即可见，无通知） | 被 D-20 取代 |
| O6 | 商品浏览删除 | 并入 D-13 |
| O7 | 营业时间与库存删除 | 并入 D6 |
| O8 | 做饭删除 | 并入 D7 |
| O9 | 内容不带 genre；口味权重、下限与有效偏好公式删除；缓解为固定速率。具体值被 C-3 取代 | 并入 D-18、C-3 |
| O10 | 进度层 `app_push` 记录带 app 与完整摘要（登记时 ≤ 200 字符），按容量折叠 | 并入 D-16、C-6 |
| O11 | `use_app` 占用 `use_app` 与 `screen`（驾车排除）一项被「占用声明」取代：只占 `use_app`，驾车一行另占 `use_app` | 被「观察与工具面」占用表取代 |
| O12 | `PlaceKind` 同 Q2 | 并入 D-29 |
| O13 | 上次叙述取 `ActivityState.progress_note` | 并入 D-22 |
| A1 | 内容类型五个 | 并入「固定规则与常数」 |
| A2 | 方式「打开」→（`interact`, `use_app`）；同义词；前缀「应用：」。同义词表被 D-12 定为 刷、刷一刷、用一下、打开看看 | 并入 D-12 |
| A3 | 同 O1 | 并入 D-15 |
| A4 | 同 C3 | 并入 D-16 |
| A5 | 同 M6 | 被 D-11 取代 |
| A6 | `appointment`、`related_task_id` 删除；主题 `opens_at`/`due_at` 一项被 D-20 取代 | 部分被 D-20 取代 |
| A7 | 实际秒数 | 并入 D10 |
| A8 | 做饭删除 | 并入 D7 |
| A9 | 常数与食用窗口 | 并入 D8 |
| A10 | 缓解同 O9 | 并入 D-18 |
| A11 | 随机抽取用 `sha256(random_seed, key)` 一项被 D-52 取代为 `random_state` 哈希链 | 被 D-52 取代 |
| A12 | 路上收到的推送到达即失效（session 切换）；打开时检查资格；不设 session 结束钩子 | 并入 D-3 |
| A13 | 新码名 `APP_NO_CONTENT` 一项被 D-35 取代为 `APP_CONTENT_EMPTY`；通过不卖该食品的 app 下单是 `SUPPLY_NOT_AT_STORE`；无 app 店铺外卖是 `FULFILLMENT_NOT_SUPPORTED` | 被 D-35 取代；其余并入「店铺、食品与外卖」 |
| A14 | 身份升级：世界 10、场景 7、环境清单、地图 1、两个指纹变化；人工环境的 `affordances.toml` 与包资源逐字节相同。「环境清单取代目录 5.x 与语料清单」被「环境格式」取代（语料清单保留） | 并入 D-25；部分被取代 |
| E1 | `watch` → `app_use`（命名被 D-12 取代为 `use_app`）；`play_game`、`browse`、`ActivityChange.change_speed`、`playback_speed` 删除 | 并入 D-12；名字被取代 |
| E2 | `app_push` 进 `WAKE_REASON_NAMES`；同步点按让「等待」成为例外规格的清单执行。计数被 D-23 取代（25 → 22） | 并入 D-23 |
| E3 | LabelKit 窗口照常投影环境事件；app 事实经 `app-usage.jsonl` 与 `purchases.jsonl` 进入手机合成 | 并入 D-44、D-45 |
| E4 | `SessionState.pushes: list[PushRecord]` 一项被 D-3 取代为 `SessionState.app_pushes`（按 `app_id` 键） | 被 D-3 取代 |
| E5 | 频率同 C6 加每 app 每 session 一条 | 并入 C-6 |
| E6 | `completed_count` 一项被 D-19 取代为 `completed_rounds`；允许重看；`repeat` = 开始时轮数 ≥ 1 | 并入 D9、D-19 |
| E7 | 倍速删除 | 并入 E1 |
| E8 | 覆盖率按环境 app 目录名计 app 提及；`coverage_apps.py`、`PhoneSimulationCoverage`、`phone_simulation_config` 删除 | 并入 D-36、D-38 |
| E9 | 导出：新 `app_usage.jsonl`（文件名被 D-44 定为 `app-usage.jsonl`）；`purchases.jsonl` 带 `app_id`；出行行保留起点、终点、路径、距离 | 并入 D-44 |
| E10 | 版本：世界 10、导出清单 3、质量报告 5、交付清单 3、运行清单 3；事件与检查点结构仍为 1 | 并入「状态、事件、检查点与导出」 |
| E11 | 只校验字节的历史完整日证明保留；驱动已删行为的夹具随测试删除 | 并入「测试与夹具」 |

<a name="spec-rrs-裁决本规格补定"></a>
### 本规格补定

rulings-final 留给本文决定的点，取与「WorldMock 定规则、数据集给名字与绑定、不设兼容层」一致的最小选项。

| 编号 | 定稿 | 依据 |
| --- | --- | --- |
| B-1 | 系统层按位置编号（第 N 条 = `rules[N−1]`）：改写第 2、7、8、12、13、17 条；第 1、3–6、9–11、14–16、18 条逐字不动。SYNTHESIS 写的「第 11、12、16 条」按位置核对分别是第 12、13、17 条。让「等待」成为例外与同时做两件事两个台账登记的变异原文（第 7 条「手上的活动照常进行」、第 14 条整句插入句、第 15 条「正餐留到平常的饭点前后吃」、第 16 条「还没到就寝时间就照常过晚上」、第 8 条「原来那件照常进行」）在改写后逐字保留 | 本规格补定 |
| B-2 | 娱乐一句放在第 12 条末尾：「娱乐只选 app、方式写打开，看什么由 app 决定；这段停留或路程里收到推送后打开那个 app，先看的是推送的内容，不打开也可以。」十八条条数不变 | 本规格补定 |
| B-3 | 第 8 条删去做饭、播客、浏览与游戏的说法：「吃、用东西算一种」「用手机 app 算一种」「读信、等待、工作、出行、和人说话各算一种」；「开车时只能再和人说话」；「只适合再配听的」一句删去 | 本规格补定 |
| B-4 | 进度层记录 `app_push{occurred_at, type: "app_push", app, title, kind, summary}`（`kind` 是内容类型标签，`summary` 是完整摘要）与 `work_completed{occurred_at, type: "work_completed", topic}`；结果 `details.app_content{app, title, kind, summary, pushed}`、`details.work[{name, done, remaining}]`；系统代选 app 时 `details.notes` 写「经<app>下单」 | 本规格补定 |
| B-5 | 新错误码的英文原因与下一步见「注册校验与错误」与「活动受理拒绝码」两张表：一句话、不含内部标识 | 本规格补定 |
| B-6 | 已完成的主题不再列在工具层；按名开始它时剩余秒数为 0，以 `ACTIVITY_DURATION_INVALID` 与 D-50 的同一句拒绝；不设 `WORK_TOPIC_COMPLETED` 码 | 本规格补定 |
| B-7 | 人工环境的内容：七个地点（家、公司、一心面馆、二味小炒、三杯咖啡、四季便利、五里公园）、四个 app（美团、饿了么、抖音、哔哩哔哩）、四种食品、六条内容、一个工作主题、种子 7 的生成图；全文见「人工环境」 | 本规格补定 |
| B-8 | 版本表行标识 `2026-10-06-rrs-<阶段>`，阶段写 `freeze`、`rules`、`map`、`store`、`app`、`work`、`prompt`、`record`、`docs`、`dataset`；设计章节在本特性第一次改写它的阶段把文件头「版本」末位加一（全特性只加一次） | `docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-裁决记录` R8 的写法 |
| B-9 | 常数名：`WALKING_METERS_PER_SECOND`、`DRIVING_METERS_PER_SECOND`、`DELIVERY_METERS_PER_SECOND`、`DELIVERY_PREP_SECONDS`、`DELIVERY_FEE_FEN`、`DRIVING_FARE_MINIMUM_FEN`、`DRIVING_FARE_FEN_PER_METER`、`MAP_MIN_NODES`、`MAP_EXTRA_EDGES`、`MAP_EDGE_LENGTH_M`、`MAP_GENERATOR_RULE_VERSION`、`PUSH_HOURS_LOCAL`、`PUSH_SUMMARY_MAX_CHARS`、`APP_USE_DEFAULT_SECONDS`、`CONTENT_RELIEF_PER_HOUR`、`FOOD_CONSTANTS`、`RULES_VERSION`；全部定义在 `src/worldmock/exchange/constants.py`，别处只引用 | 本规格补定 |
| B-10 | `ENVIRONMENT_STALE_FILE` = `catalog/` 下存在清单未列出的文件；`ENVIRONMENT_FILE_UNLISTED` = 清单没有列出某个必需文件；`ENVIRONMENT_IDENTITY_MISMATCH` = 某文件的 sha256 或字节数与清单不符，或 `catalog_content_sha256` 与重算不符 | 本规格补定 |
| B-11 | 同地点不出行：目标是当前地点的出行以 `ACTIVITY_PRECONDITION_UNMET` 拒绝，原因 "You are already at this place."，`unmet = already_at_destination`；工具层不把当前地点列为出行目标 | 本规格补定 |
| B-12 | 最短路：Dijkstra，堆键 `(米数, 地点标识)`；等长时取地点标识序列字典序较小的路径；`distance_meters(a, a) = 0` | 本规格补定 |
| B-13 | 生成器步骤固定：对排序后的地点标识以 NetworkX `random_labeled_tree(n, seed=seed)` 得树边（标号 i 对应第 i 个标识）；再以 `random.Random(seed)` 从全部非树无序对中不放回均匀抽 ⌊(n−1)/2⌋ 对；边集按 `(a, b)` 规范排序后，用同一个 `random.Random` 依次为每条边抽 `randint(200, 2000)`；`MAP_GENERATOR_RULE_VERSION = 1` 绑定这一步骤与 `uv.lock` 锁定的 NetworkX 版本 | 本规格补定 |
| B-14 | `data.unmet` 闭集为 `required_location_id`、`delivery_destination`、`delivery_app`、`already_at_destination` | 本规格补定 |
| B-15 | 本文未点名的拒绝码保留；一个码的最后一个抛出点随被删模块消失时，在同一提交删除该码并写进该阶段的删除行 | 本规格补定 |
| B-16 | 推送抽取的事件标识 `scheduled_id = "push-<agent_id>-<YYYY-MM-DD>T<HH>"`；合格 app = 有绑定内容且当前 session 的 `app_pushes` 里没有它 | 本规格补定 |
| B-17 | 正式环境的最低数量与真实名称要求写在「数据仓交接」，由数据仓的测试判定，不进引擎常数 | 本规格补定 |
| B-18 | 变异回执目录 `.work/rrs/mutation-receipts`，与既有台账分开 | 本规格补定 |
| B-19 | 指纹变动的阶段：`tool_schema_sha256` 在阶段 1（`affordances.toml` 删旧类型）、阶段 4（加 `use_app`）、阶段 6（工具面）各变一次；`prompt_sha256` 只在阶段 6 变；`code_sha256` 每个代码阶段都变 | 本规格补定 |
| B-20 | 登记码在 SYNTHESIS 清单之外补四个：`CONTENT_SUMMARY_TOO_LONG`（C-6 的 200 字符上限）、`CONTENT_SOURCE_URL_MISSING`（C-8 的 `url` 必填）、`MAP_NOT_REGENERATED`（D-31 的字节比对）、`WORK_SCHEDULE_REQUIRED`（D-20 的安排要求）；全部定义在「注册校验与错误」 | 本规格补定 |

<a name="spec-rrs-固定规则与常数"></a>
## 固定规则与常数

WorldMock 拥有全部分类、常数与交换公式，定义在新包 `src/worldmock/exchange/`（`types.py` 闭集、`constants.py` 常数、`operators.py` Decimal 算子、`body.py` 身体常数）；`RULES_VERSION` 写进运行清单与检查点的 `environment_identity.rules_version`，与目录清单的 `rules_version` 不相等即拒绝加载、恢复与重放。三份既有包 TOML（`affordances.toml`、`rules/motivation.toml`、`rules/persona-mapping.toml`）的字节副本机制不变。本节与「环境格式」「状态、事件、检查点与导出」是闭集、字段、常数、错误码、事件类型与唤醒原因的唯一定义处，其余各节只引用。

<a name="spec-rrs-闭集"></a>
### 闭集

| 闭集 | 成员 | 变化 |
| --- | --- | --- |
| `LocationKind` | `home`、`office`、`park`、`fitness`、`restaurant`、`cafe`、`convenience_store`、`supermarket` | 取代现行 `PLACE_TYPES` 九个（`merchant`、`wet_market` 退役，`cafe` 新增） |
| `FOOD_STORE_KINDS` | `restaurant`、`cafe`、`convenience_store`、`supermarket` | 只有这四类地点可绑定 app、可出现在食品绑定里、`"all"` 只展开到它们 |
| `PLACE_KIND_ENVIRONMENTS` | home→`home`；office→`office`；park→`outdoor`；fitness→`fitness`；restaurant、cafe、convenience_store、supermarket→`venue` | 派生今天的 `location_kinds`；活动环境闭集（`home/office/outdoor/fitness/venue/anywhere`，`src/worldmock/life/retrieval.py::LOCATION_KINDS`）不变 |
| `FoodCategory` | `snack`、`drink`、`meal`；标签 `FOOD_CATEGORY_LABELS` 零食、饮品、正餐 | 新 |
| `ContentKind` | `article`、`image_text`、`short_video`、`long_video`、`live`；标签 `CONTENT_KIND_LABELS` 文章、图文、短视频、长视频、实况 | 取代八种娱乐类型 |
| `SummarySourceKind` | `real`、`synthetic` | 新 |
| `ActivityKind` | `wait`、`sleep`、`work`、`use_app`、`travel`、`read_mail`、`consume`、`lifestyle`、`chat`、`call` | 删 `watch`、`play_game`、`browse`；加 `use_app` |
| `RESOURCE_CLAIM_SLOTS` | `wait`、`work`、`use_app`、`read_mail`、`consume`、`lifestyle`、`talk`、`travel`（八个） | 阶段 1 先成七个（无 `use_app`），阶段 4 加 `use_app`；退役名 `position`、`attention`、`device`、`vehicle`、`idle`、`eyes`、`ears`、`hands`、`voice`、`watch`、`screen`、`play_game`、`browse` 解析时拒绝 |
| `WAYS` | 到店、远程下单、购买、打开、食用、使用、给予、进行 | 删 观看、接着看、从头再看、接着听、从头再听、制作、浏览、游玩；加 打开→（`interact`, `use_app`），同义词 刷、刷一刷、用一下、打开看看 |
| `START_ACTIVITY_ACTIONS` | `travel_to`、`work`、`purchase`、`consume`、`use_app`、`chat`、`call`、`read`、`do`、`wait`、`sleep` | 删 `watch`、`play`、`browse`；加 `use_app` |
| `ScheduledKind` | `mail_arrival`、`delivery_arrival`、`calendar_flag`、`bill_due`、`payroll`、`message_arrival`、`app_push` | 删 `work_available`、`task_expired`、`price_change`、`supply_quote_change`、`product_release`；加 `app_push` |
| `WakeReason`（22，声明次序即输出次序） | `planned_end`、`felt_band_change`、`message_arrival`、`delivery_arrival`、`commitment_threatened`、`reconsideration_interval`、`person_arrived`、`person_left`、`activity_infeasible`、`rejection_feedback`、`discovery_result`、`wake_from_sleep`、`run_start`、`progress_report_due`、`no_active_activity`、`plan_reminder_due`、`errand_reminder_due`、`shift_start`、`shift_end`、`break_start`、`break_end`、`app_push` | 删 `task_available`、`task_due_soon`、`work_review_result`、`location_hours_change`；`app_push` 追加在 `break_end` 之后；`src/worldmock/life/schedule.py::SLEEP_ALLOWED_REASONS` 仍为四项 |
| `CandidateKind` | `product`、`lifestyle`、`task` | 删 `content`、`game`；app 不是候选 |
| 工具层目标类 `TARGET_KINDS` | `location`、`product`、`holding`、`person`、`contact`、`self`、`task`、`activity`、`message`、`app`、`lifestyle` | 删 `content`、`game`、`catalog`；加 `app` |
| 运行输入警告 | D-39 的六个码 | 删 `TASTE_GENRES_UNMATCHED`、`CONTENT_WEIGHTS_ALL_ZERO`、`GAMES_WITHOUT_TASTE_WEIGHTS`、`DELIVERY_ROUTE_UNAVAILABLE` |
| `environment_event.data.kind` | 现有成员减 `location_track`、`price_change`、`supply_quote_change`、`product_release`、`work_available`、`task_expired`，加 `app_push`、`work_topic_completed` | 定义见「状态、事件、检查点与导出」 |

<a name="spec-rrs-常数表"></a>
### 常数表

全部是仿真假设，登记到 `docs/EXPERIMENT.md#experiment-实验假设声明`；由 `RULES_VERSION = 1` 一起绑定进运行身份；`tests/test_single_source.py` 的 `OWNERS` 为每张表登记唯一定义处。

| 名称 | 值 | 单位 | 依据 |
| --- | --- | --- | --- |
| `RULES_VERSION` | 1 | — | D-25 |
| `FOOD_CONSTANTS[snack]` | 200 千卡，300 秒 | 每份 | C-4（零食热量锚于膳食指南 10 %；时长为实验假设） |
| `FOOD_CONSTANTS[drink]` | 100 千卡，180 秒 | 每份 | C-4（实验假设） |
| `FOOD_CONSTANTS[meal]` | 600 千卡，1200 秒 | 每份 | C-4（午餐占比 30–40 %；用餐 103 分/日） |
| `WALKING_METERS_PER_SECOND` | 1.2 | 米/秒 | C-1（MUTCD 1.07 与 Browning 1.4–1.47 之间） |
| `DRIVING_METERS_PER_SECOND` | 7.0 | 米/秒 | C-1（实验假设；高德自由流 48.75 km/h 为上界） |
| `DRIVING_FARE_MINIMUM_FEN` | 500 | 分 | C-1（现行闭集算子值） |
| `DRIVING_FARE_FEN_PER_METER` | `Decimal("0.2")`，向上取整 | 分/米 | C-1（现行闭集算子值）；步行车费 0 |
| `DELIVERY_METERS_PER_SECOND` | 5.0 | 米/秒 | C-1（GB 17761 25 km/h 上限之下；实验假设） |
| `DELIVERY_PREP_SECONDS` | 600 | 秒 | C-1（美团出餐为独立阶段；实验假设） |
| `DELIVERY_FEE_FEN` | 500 | 分/单 | C-1（美团每单配送收入 4.71–7 元；实验假设） |
| `MAP_MIN_NODES` | 2 | 个 | C-2 |
| `MAP_EXTRA_EDGES(n)` | ⌊(n−1)/2⌋ | 条 | C-2（实验假设，平均度约 3） |
| `MAP_EDGE_LENGTH_M` | (200, 2000) 闭区间整数 | 米 | C-2（上海步行接驳 7.5 分钟；高德 1,185 m） |
| `MAP_GENERATOR_RULE_VERSION` | 1 | — | B-13 |
| `PUSH_HOURS_LOCAL` | 8 … 21（`range(8, 22)`） | 当地整点 | C-6 |
| `PUSH_SUMMARY_MAX_CHARS` | 200 | 字符 | C-6 |
| `APP_USE_DEFAULT_SECONDS` | 1800 | 秒 | C-5 |
| `CONTENT_RELIEF_PER_HOUR` | article 0.2、image_text 0.2、short_video 0.3、long_video 0.4、live 0.3 | 娱乐需要单位/小时 | C-3（实验假设） |
| `BODY_CONSTANTS` | `maximum_energy_kcal = 2400`、`basal_kcal_per_hour = 65`、`hunger_threshold_kcal = 800`、`emergency_threshold_kcal = 400`、`activity_met` 按注册表 MET 键（含 `lifestyle:<id>` 前缀规则）、`awake_sleep_debt_per_hour`、`sleep_recovery_per_hour`、`entertainment_need_per_awake_hour` | 各自单位 | D-24：数值逐项抄自今天的 `resources.jsonl`，不改值 |
| `RESOURCE_METAS` | `money_fen`（账户、闭域）、`energy_kcal`、`sleep_debt_hours`、`entertainment_need`（水平、开域） | — | D-24 |

算子（`src/worldmock/exchange/operators.py`，Decimal 精确舍入沿用今天的 `categories/operators.py`）：`evaluate_purchase(unit_price_fen, quantity)`、`evaluate_food_energy(category, consumed_fraction)`、`evaluate_travel_fare(mode, distance_m)`、`travel_seconds(mode, distance_m) = ceil(distance_m ÷ 速度)`、`delivery_seconds(distance_m) = DELIVERY_PREP_SECONDS + ceil(distance_m ÷ DELIVERY_METERS_PER_SECOND)`、`delivery_fee_fen() = DELIVERY_FEE_FEN`。签名不带系数：环境没有任何字段能进入这些函数。

<a name="spec-rrs-环境格式"></a>
## 环境格式

发布环境是一个目录：`catalog/`（登记的对象、绑定、图与清单）、`corpora/`（生活活动条目与数据仓的联系人、邮件语料，`corpora/manifest.json` 不变）、`scenarios/`。`tests/data_paths.py` 的 `select_environment` 仍要求 `catalog/manifest.json`、`corpora/manifest.json`、`scenarios/single-person.json`。全部 JSON 为严格模型（`DeepFrozenModel`，`extra="forbid"`），LF，按标识排序；旧字段直接报错，不忽略、不猜测、不补齐。

<a name="spec-rrs-环境目录文件"></a>
### 目录文件

| 文件 | 内容 |
| --- | --- |
| `catalog/manifest.json` | `{format_version: 1, environment_id, environment_version, rules_version, files: [{path, sha256, size_bytes}], catalog_content_sha256}`；`files` 恰好列出六个目录文件与 `affordances.toml`、`rules/motivation.toml`、`rules/persona-mapping.toml`；`rules_version` 必须等于 `RULES_VERSION`；`catalog_content_sha256` 是对清单文件表的框架化摘要（`src/worldmock/catalog/identity.py`，由 `categories/identity.py` 迁入），发布经 `src/worldmock/footprints/assets.py::atomic_write` |
| `catalog/apps.json` | `{apps: [{app_id, name}]}`；一个真实名称；`channel_key(name)` 唯一且不等于任何地点显示名 |
| `catalog/locations.json` | `{locations: [{location_id, name, branch_label（可选）, kind: LocationKind, app_ids: [app_id]}]}`；`app_ids` 只在 `kind ∈ FOOD_STORE_KINDS` 时允许非空；空列表 = 只能到店 |
| `catalog/foods.json` | `{foods: [{food_id, name, category: FoodCategory, price_fen: 正整数, store_ids: [location_id] 或 "all"}]}`；没有摘要、热量、时长、营养或来源字段 |
| `catalog/contents.json` | `{contents: [{content_id, title, kind: ContentKind, summary, summary_source: {kind, url, notice}, duration_seconds: 正整数, app_ids: [app_id]（≥ 1）, series（可选）: {title, episode}}]}`；`summary` 登记时不超过 `PUSH_SUMMARY_MAX_CHARS` |
| `catalog/work_topics.json` | `{work_topics: [{topic_id, topic, target_hours: 正数}]}`；运行时 `target_seconds = round(target_hours × 3600)` |
| `catalog/map.json` | `{schema_version: 1, seed: 整数, generator_rule_version: 1, nodes: [location_id…]（排序）, edges: [{a, b, length_m: 正整数}]（a < b，按 (a, b) 排序，无重复）}`；由 `src/worldmock/catalog/mapgen.py` 生成、数据仓发布、加载时不重生成 |
| `catalog/affordances.toml`、`catalog/rules/motivation.toml`、`catalog/rules/persona-mapping.toml` | 与 `worldmock.data` 包资源逐字节相同（`src/worldmock/life/world_setup.py::_require_engine_rules` 机制不变） |
| `corpora/manifest.json`、`corpora/lifestyle/items.json` | 不变（生活活动条目保留 `effects`、`location_kinds`、`time_window`、`met_value`）；数据仓的 `scenario-contacts/`、`scenario-mail/` 语料照旧 |

登记时冻结在 `Catalog` 上的派生索引：`menus_by_kind`、`foods_by_store`、`stores_by_app`、`contents_by_app`、`apps_by_store`、`location_kinds(kind)`、`shortest_m(a, b)`。目录里不再有资源定义、品类树、交换规则、对象增量、墓碑、来源指针、检索分区、营业日历、报价、库存、路线、坐标、字幕、音轨、游戏与工作材料。

<a name="spec-rrs-场景结构7"></a>
### 场景结构 7

- 保留：`scenario_id`、`timezone`、`start_at`、`end_at`、`random_seed`、`activity_feedback_seconds`、`reconsideration_interval_seconds`、`idle_reconsideration_interval_seconds`、`progress_report_interval_seconds`、`max_same_moment_decisions`、`candidate_presentation_limit`、`candidate_pool_limit`、`rotation_quota_ratio`、`rotation_stale_seconds`、`corpus_root`、`corpus_manifest_sha256`、`accounts[]`、`body{mass_kg, initial_energy_kcal, initial_sleep_debt_hours, initial_entertainment_need}`、`agents[]`（人物画像、节律窗口、`work_schedule`、`interaction_menu`、`lifestyle_activities`、`contacts`、`home_location_id`、`initial_balance_fen`、`motivation` 减 `attention_prior`）、`locations[]{location_id}`、`interaction_menus_by_location_kind`（键为八个 `LocationKind`，`allowed_actions` 取自新的 `START_ACTIVITY_ACTIONS`）、`messages[]`（减 `related_task_id`）、`calendar`、`bills[]`、`payrolls[]`。
- 新增：`schema_version: 7`；`environment{environment_id, environment_version}`（取代 `catalog_version`）；`agents[].work_topic_ids: [topic_id]`（非空时该人物必须声明 `work_schedule`）；`agents[].initial_inventory{location_id: {food_id: 份数}}`；`accounts[]` 的角色 `buyer`、`merchant:<location_id>`（每个已选食品店铺一个）、`employer:*`、`transport:service`、`external:scenario`。
- 删除：`map_dataset`、`products`、`supplies`、`tasks`、`contents`、`games`、`price_changes`、`product_releases`、`recipe_output_bindings`、`phone_simulation_config`、`phone_simulation_config_sha256`、`catalog_version`、`retrieval_index_threshold`、`agents[].skill_levels`、`agents[].qualifications`、`body.entertainment_relief_preference_floor`、`messages[].related_task_id`。
- 不设隐式账户：卖家、雇主、交通与外部账户都是场景声明；配送费与货款同一笔付给 `merchant:<store_id>`，车费付给 `transport:service`。

<a name="spec-rrs-注册校验与错误"></a>
### 注册校验与错误

`src/worldmock/catalog/admission.py` 是唯一的登记校验器，`load_catalog`、`worldmock validate --environment` 与数据仓的 `--check` 都调用它；失败抛 `RegistrationError`（`IntegrityError` 子类，带稳定 `code`），日志与原因一律英文。

| 码 | 条件 | 英文原因 |
| --- | --- | --- |
| `RULES_VERSION_MISMATCH` | 清单 `rules_version` ≠ `RULES_VERSION` | "The environment was published for a different rules version." |
| `ENVIRONMENT_STALE_FILE` | `catalog/` 下有清单未列出的文件（`category-contract.json`、`exchange-rules.json`、`objects.jsonl`、`resources.jsonl`、`overrides/`、`rules/travel-time.toml`、`rules/off-foods.mapping.toml`、`snapshots/` 等） | "The catalog contains a file that the manifest does not list." |
| `ENVIRONMENT_FILE_UNLISTED` | 清单缺少某个必需文件 | "The manifest does not list a required catalog file." |
| `ENVIRONMENT_IDENTITY_MISMATCH` | 文件 sha256 或字节数与清单不符；`catalog_content_sha256` 与重算不符 | "A catalog file does not match the identity recorded in the manifest." |
| `ENVIRONMENT_ID_DUPLICATE` | 同一文件内标识重复；app 名与地点显示名经 `channel_key` 归一后冲突 | "An identifier or display name is declared twice." |
| `ENVIRONMENT_REF_UNKNOWN` | `store_ids`、`app_ids`、`nodes`、边端点或场景引用指向不存在的对象 | "A binding refers to an object that is not registered." |
| `ENVIRONMENT_KIND_INVALID` | `kind`、`category`、`summary_source.kind` 不在闭集；非食品店铺带 `app_ids` | "A declared kind is outside the fixed set." |
| `ENVIRONMENT_NUMBER_INVALID` | `price_fen`、`duration_seconds`、`target_hours`、`length_m`、`seed` 不是有限正数或不是整数 | "A declared number is not a positive finite value." |
| `FOOD_BINDING_MISSING` | 食品没有 `store_ids`，或显式列表为空 | "A food must be bound to stores or to all food stores." |
| `FOOD_STORE_NOT_FOOD_STORE` | `store_ids` 含非食品店铺类型的地点 | "A food is bound to a place that does not sell food." |
| `FOOD_BINDING_KIND_INCONSISTENT` | 某类型的店铺里有的绑定了该食品、有的没有 | "A food must be bound to every store of a kind or to none of them." |
| `CONTENT_APP_MISSING` | 内容 `app_ids` 为空 | "A content must be bound to at least one app." |
| `CONTENT_SUMMARY_TOO_LONG` | `summary` 超过 `PUSH_SUMMARY_MAX_CHARS` | "A content summary exceeds the registration limit." |
| `CONTENT_SOURCE_URL_MISSING` | `summary_source.kind = real` 而 `url` 为空 | "A real summary must record its source url." |
| `APP_UNBOUND` | app 既没有店铺也没有内容绑定 | "An app must be bound to at least one store or content." |
| `APP_NAME_COLLIDES_LOCATION` | app 名与地点名归一后相同 | "An app name must differ from every place name." |
| `MAP_TOO_SMALL` | 节点少于 `MAP_MIN_NODES` | "The map needs at least two places." |
| `MAP_EDGE_INVALID` | 自环、重复无序对、未知端点、非正长度、未按规范排序、`nodes` 与地点清单不等 | "The map has an edge that is not a sorted positive link between two registered places." |
| `MAP_DISCONNECTED` | BFS 不能到达全部节点 | "The map is not connected." |
| `MAP_NOT_REGENERATED` | `worldmock validate --environment` 或 `--check` 的重生成结果与发布的 `map.json` 不逐字节相同 | "The published map differs from the generator output for its seed." |
| `WORK_TOPIC_UNKNOWN` | 场景 `work_topic_ids` 指向目录没有的主题 | "A scenario assigns a work topic that is not registered." |
| `MERCHANT_ACCOUNT_MISSING` | 已选食品店铺没有 `merchant:<location_id>` 账户 | "A selected food store has no merchant account." |
| `WORK_SCHEDULE_REQUIRED` | 有主题的人物没有 `work_schedule` | "A person with work topics must declare a work schedule." |

场景加载还执行既有的 `src/worldmock/life/load_checks.py::check_work_schedules`、`check_location_kinds`（改对 `LocationKind`）与可见标签唯一性（地点、食品、app「应用：<name>」）；app 名与地点名冲突是登记错误，不再是警告。世界结构 9、场景结构 6、目录 5.x 与地图结构 3 在每个入口直接拒绝（加载器严格模型、`WorldState.schema_version` 字面量、运行清单校验器、交付门禁），没有迁移与双读。

<a name="spec-rrs-环境命令"></a>
### 环境命令

- `worldmock environment generate-map --locations <locations.json> --seed <整数> --output <map.json>`：读地点清单，按 B-13 生成并以 LF 写出；加 `--check` 时不写文件，与已存在的输出逐字节比对，不同则非零退出并打印 `MAP_NOT_REGENERATED`。
- `worldmock validate --environment <环境根>`：登记校验全目录、重生成比对 `map.json`、校验 `corpora/manifest.json` 与三个包 TOML 字节、加载 `scenarios/` 下每个场景并跑场景加载检查；输出每个失败的码与英文原因，任何失败非零退出。`worldmock validate` 既有的 AS2 锁定、LabelKit Schema、依赖版本、阶段身份与覆盖率 0.8 检查不变。
- `worldmock category` 子命令删除。

<a name="spec-rrs-地图与出行"></a>
## 地图与出行

- 生成规则（B-13）：数据仓调用命令或库，WorldMock 生成，数据仓发布；相同地点清单与种子在任何平台得到逐字节相同的 `map.json`。
- 校验：登记时查 `MAP_TOO_SMALL`、`MAP_EDGE_INVALID`、`MAP_DISCONNECTED`；`validate --environment` 与 `--check` 另查 `MAP_NOT_REGENERATED`；运行与恢复只读发布图。
- 最短路（B-12）由 `src/worldmock/catalog/graph.py` 提供 `shortest_paths`、`distance_meters(a, b)`、`path(a, b)`；`src/worldmock/life/wayfinding.py` 只剩图查询与 `go_step = "Go to <place>."`；多跳提示、`UNREACHABLE_STORE_STEP`、`UNREACHABLE_DELIVERY_STEP` 与不可达分支删除。
- 出行：`plan_trip(graph, origin, destination, mode)` 返回 `Trip(trip_id, path, distance_meters, duration_seconds, fare_fen)`；`duration_seconds = ceil(distance ÷ 方式速度)`；`src/worldmock/life/travel.py::planned_travel` 保留名字与单一来源地位，`pay_travel_fare` 只把 `route_id` 改成 `trip_id`，`update_travel` 读 `trip.distance_meters`；`ROUTE_DURATION_MISMATCH` 按图重算；同地点出行按 B-11 拒绝。
- 车费：步行 0；驾车 `max(DRIVING_FARE_MINIMUM_FEN, ceil(DRIVING_FARE_FEN_PER_METER × 米数))`，付给 `transport:service`。
- 出行身份：活动 `data{trip_id, path, distance_meters, travel_payment{trip_id, fare_fen, …}, arrival{location_id, access_status}}`；事件、`DeliveryState`、公开证据 `object_type = "route"` 的键与禁键表全部用 `trip_id`，`route_id` 不再出现。
- 工具层地点目标：每个其他已选地点各列一行，动作 步行、驾车 各带 `price`、`duration = ceil(长度 ÷ 速度)`、费力程度（MET 不变）；私有观察 `data.routes[{trip_id, origin_id, destination_id, mode, distance_meters, duration_seconds, fare_fen}]`、`data.locations[{location_id, name, kind}]`；坐标、`business_kind`、路线状态不再出现。
- 位置记录：出行事件与活动行引用 `origin_id`、`destination_id`、`path`、`distance_meters`；GPS 轨迹采样、球面距离、折线插值整段删除。

<a name="spec-rrs-店铺食品与外卖"></a>
## 店铺、食品与外卖

- 同类同菜单：某类型店铺的菜单 = 绑定到该类型任一店铺的食品集合；显式绑定对每种类型要么全部、要么全无；`"all"` 展开到全部 `FOOD_STORE_KINDS` 地点；价格定义在食品上，无店铺覆盖价、无价格位置、无抖动、无种子。
- app 绑定：店铺 `app_ids` 决定能否外卖；一个 app 绑定多家店；内容绑定 app 与店铺绑定 app 互不影响；无 app 的店只能到店。
- 指名购买（M0–M3）不变地跑在食品、店铺与 app 上：`from` 为 null、店铺显示名或 app 名（app 行由 `src/worldmock/life/name_resolution.py::resolve_source` 从 `catalog.apps` 构造 `(app_id, (name,), stores_by_app[app_id])`，`channel_key` 不变）；M3「能服务」= 到店：人在该店；远程下单：店铺绑定了 app，`from` 为 app 时必须是该 app；下一步写 `Go to <最近的在售店铺>.` 或 `Order through <app>.`；绑定行 `channel_id` 改 `app_id`，`purchase_note` 写「经<app>远程下单」。
- `prefer`：最快拿到 = 外卖按最早 `expected_at`、到店按图上最短距离；到手价最低 = `price_fen`（外卖加 `DELIVERY_FEE_FEN`）；并列按到手价再按 `store_id`；`from` 为 null 且店铺绑定多个 app 时取最小 `app_id` 并写 `details.notes`（D-34）。
- 到店购买：人在店里，`fulfillment = pickup`，即时交付到当地持有，不记 app（`app_id = null`）；`from=<app>` 配到店以 `SUPPLY_SOURCE_MISMATCH` 拒绝；到店购买与进食用现有逐地点持有，不设合并动作。
- 外卖：`fulfillment = delivery`，要求 `app_id ∈ apps_by_store[store_id]`（否则 `APP_NOT_BOUND`），店铺没有 app 时显式远程下单是 `FULFILLMENT_NOT_SUPPORTED`（"This store has no delivery app."）；通过 app 下单而该 app 的店不卖该食品是 `SUPPLY_NOT_AT_STORE`；收货地点：给自己 = 当前停留地点，路途中按 D-11 拒绝，给他人 = 收货人的收货地点；`DESTINATION_NOT_FOUND` 不变；`expected_at = now + delivery_seconds(shortest_m(store, destination))`；人物离开后仍送到订单地点，食品归收货人在该地点持有。
- 结算：每单一笔 `settle_money_transfer(buyer → merchant:<store_id>, 货款 + 配送费)`；订单行 `(store_id, food_id)` 由 `foods_by_store` 解析（菜单没有则 `SUPPLY_NOT_FOUND`）；`ORDER_DUPLICATE_SUPPLY` 改名 `ORDER_DUPLICATE_ITEM`（"The same food from the same store appears twice in one order."）；`ORDER_LOCATION_MISMATCH` 不变；没有库存、报价、资格、营业检查；送达幂等（一个 `delivery_arrival` 一单）。
- `DeliveryState` 与订单行见「状态、事件、检查点与导出」；工具层食品目标的动作 到店（在场才可用）、远程下单（店铺有 app 才列出），`price` 为到手价，`note` 写「预计 HH:MM 送到」或「经<app>」；`callable_index.goods` 按食品类别标签分组，`obtainable_here = purchase_allowed 且（在场 或 店铺有 app）`。
- 持有：`AgentState.inventory{location_id: {food_id: 份数}}`；`initial_inventory` 的键必须是目录食品；只有食品可持有、可给予。

<a name="spec-rrs-三类食品与进食"></a>
## 三类食品与进食

- 每份固定：`FOOD_CONSTANTS[category]` 给热量与完整食用时长；`portion_duration = max(60, round(完整时长 × 份数))`；食用请求的 60–14400 秒窗口不变；同餐多项按份量折算后取最长时长。
- 结算：原始摄入 = 类别热量 × 实际消费比例（`evaluate_food_energy`）；净能量经 `src/worldmock/life/sleep.py::food_utilization_efficiency` 与 `maximum_energy_kcal` 上限；一份分几次吃原始摄入总量相同；暂停只保留剩余份量；重复开始进食不重复获得；购买不增加能量。`opened/consumed/discarded` 记账、份量措辞与 1e-9 库存精度不变。
- 工具层：食品目标 `state` 写「<类别>，每份约 N 千卡；可用<app1>、<app2>下单」（无 app 时省略后半句）；`food_energy_label` 由类别给出。
- 动机层：正餐候选按类别（`meal` 算正餐，`snack`、`drink` 是轻食后备），`MEAL_KCAL_FLOOR` 改为类别判定；外卖等待 = `delivery_seconds`。
- 进食证据：`activity_progress.data.consumption{use_mode, location_id, items[{food_id, category, interval_portion, cumulative_portion, interval_kcal}], food_efficiency}`；已核验经历 `consumed{food_id, category, location_id, interval_portion, cumulative_portion}`；质量报告 `meal_diversity` 按 `food_id`。
- 删除：逐食品热量密度、营养来源、`energy_kcal_per_unit`、`energy_kcal_per_serving`、食品算子的槽位、做饭与配方、耐用品使用、家庭储备下限检查。

<a name="spec-rrs-娱乐app内容与推送"></a>
## 娱乐：app、内容与推送

- 目标与方式：app 是始终列出的工具层目标 `{name: "应用：<name>", kind: "app", phase: null, state, actions: [{verb: "interact", way: "打开", label, duration: "约 30 分钟", note: null, available, reason}], tools: []}`；`state` 为「有推送：《<title>》（<类型标签>）」（当前 session 有待消费推送）或「<N> 条内容」；没有绑定内容的 app 列出但 `available=false`，`reason` 为 `APP_CONTENT_EMPTY` 的原因 "This app has no content to use."，下一步 "Choose another app."；只有店铺的 app 不列其他动作（购买走食品目标的 `from=<app>`）。人物不能指定内容：`start_activity` 的 `use_app` 没有内容参数。
- 活动生命周期：开始时在该 app 的绑定内容里选一条——当前 session 的 `app_pushes[app_id]` 为 `pending` 时取推送内容（`pinned_by_push_id`，状态置 `opened`），否则以 `sha256(f"{random_state}:{action_id}:{app_id}")` 均匀抽取；起始位置取 `content_progress[content_id].position_seconds`（已完成或没有记录为 0）；计划时长 `APP_USE_DEFAULT_SECONDS`；`media_position_seconds` 按经过秒数推进；内容在 `planned_end_at` 之前到达 `duration_seconds` 时：`completed_rounds += 1`、位置归零、解除当前 session 里指向它的全部推送（`released`）、把 `{content_id, kind, from_position, to_position}` 追加到 `data.played[]`、以 `sha256(f"{random_state}:{activity_id}:{draw_index}")` 抽下一条并把 `next_interaction_at` 设为 `min(now + 新内容剩余, planned_end_at)`；内容完成是静默边界（不决策）。暂停保留当前内容，恢复不重抽；计划结束或 `conclude_activity` 结束活动并把当前位置写回 `content_progress`；提前结束照常收尾；重新打开是新活动，重新按「推送优先、其余随机」选。
- 进度与重看：`AgentState.content_progress{content_id: ContentProgress{content_id, position_seconds, completed_rounds}}` 跨 app 共享；未完成的从原位置继续，已完成的开始新一轮；`repeat` = 开始时 `completed_rounds ≥ 1`；没有排除集合。
- 缓解：`use_app` 的娱乐缓解速率 = `CONTENT_RELIEF_PER_HOUR[当前内容类型]`，没有口味因子、曝光除数与下限；`src/worldmock/life/activities.py::entertainment_relief_rate` 与 `content_entertainment_relief_rate` 去掉 `w` 参数，`relief_preference` 删除。
- 占用：`[kinds.use_app]` 只占 `use_app`；驾车一行另占 `wait`、`work`、`use_app`、`read_mail`、`consume`、`lifestyle`（开车时只能和人说话）；睡眠占全部八个；两项 app 使用互斥（同种），吃饭 ∥ app 使用受理。
- 推送（D4、C-6、B-16）：编译期为每个人物、`[start, end)` 触及的每个当地日、`PUSH_HOURS_LOCAL` 的每个整点各排一个 `app_push` 预排事件（`start < due < end`；夏令时跳过的时刻按跳变前偏移、重复的取第一次，与 `src/worldmock/life/work_schedule.py::shift_span` 同一换算）。到期时：session = `src/worldmock/life/sessions.py::current_session`；合格 app 按 B-16；没有合格 app 则事件结算为 `{kind: "app_push", pushed: false, session_id}`（系统可见、不唤醒、不写人物记录）；否则以 `random.Random(sha256(f"{random_state}:{scheduled_id}"))` 先均匀选 app 再均匀选它的一条内容，写 `session.app_pushes[app_id] = AppPush(push_id, content_id, issued_at, status: pending)`，写人物可见的环境事件（见「状态、事件、检查点与导出」）、进度层记录 `app_push`（B-4）并以 `app_push` 唤醒醒着的人；睡眠中照常写入、不唤醒（`SLEEP_ALLOWED_REASONS` 不变）。到达当秒的推送属于新的停留 session（效果在 `_switch_session_on_arrival` 之后执行）。
- 推送与消费的关联：本 session 首次打开对应 app 先消费推送内容；中途停止、本 session 再打开仍从该内容剩余部分继续（仍是 `pending`→`opened` 的同一条推送）；指定内容在任一绑定 app 完成即解除本 session 内指向它的推送；通知之前的完成不解除新推送；app 已在使用中时收到推送不打断；session 结束（出发或到达）即失去资格，内容进度保留；22:00 之后同 session 仍可按推送消费，窗口只限制新推送；没有 session 结束钩子，`app_pushes` 随 session 开始为空即是资格清除。
- 确定性：抽取只读 `WorldState.random_state` 与固定键，不推进它；恢复与反事实重演抽到同一序列；推送事件在提交前弹出，恢复不重发。
- `APP_CONTENT_EMPTY`：打开没有内容的 app 在受理时拒绝，与工具层 `reason` 同文；不从别的 app 借内容、不临时合成。

<a name="spec-rrs-工作主题与叙述"></a>
## 工作主题与叙述

- 主题：`work_topics.json` 登记，场景 `agents[].work_topic_ids` 分配；没有开放时刻、截止、预约、通知邮件与材料；人物自运行开始就看到分配给他的、尚未完成的主题；工作地点 = `work_schedule.workplace_location_id`，在场才能开始。
- 累计：`worked_seconds(agent, topic)` = 该人物 `target_id == topic_id` 的全部非失败工作活动的 `elapsed_seconds` 之和（`src/worldmock/life/work.py` 的 `work_totals`，派生，不新增状态）；暂停、休息与并行的非工作活动不计；没有效率折扣；疲劳仍是按 MET 的身体消耗。
- 开始与恢复：派生时长 = `min(target_seconds − worked, DERIVED_DURATION_CAP_SECONDS)`；剩余不足 60 秒以 `ACTIVITY_DURATION_INVALID` 与 D-50 的句子拒绝；恢复次序按同时做两件事规格 R4，主题上限在第二位；已完成的主题离开工具层（B-6）。
- 完成：某次结算使 `worked ≥ target_seconds` 时，活动以 `boundary_type = "planned_end"` 结束，写 `environment_event{kind: "work_topic_completed", topic_id, worked_seconds}`（只对本人可见）与进度层记录 `work_completed`；`activity_progress.data.work{topic_id, worked_seconds, remaining_seconds}` 每次结算都写。
- 叙述：播报仍走 `report_status.items[].summary`（`with_note(anchor=True)` 存 `progress_note` 与 `note_history`）；工具层任务目标 `contract{topic, target: "约 8 小时", done: "约 2.5 小时", remaining: "约 5.5 小时"}`，工作活动目标 `state = "已做约 X 小时，还差约 Y 小时"` 与 `last_report`（上一次 `progress_note`，跨 session 保留）；`report_status` 的结果带 `details.work[{name, done, remaining}]`；模型写的百分比或「已完成」不改变工时、不触发完成；`recall_task_summary` 仍返回 `note_history`。
- 工资：沿用场景 `payrolls[]` 的 `payroll` 预排事件与雇主账户；主题没有报酬字段；没有任务付款、验收付款与返工。
- 删除：材料、输入与答案文件、逐段揭示、`submit_work`、独立语义验收、`WORK_AVAILABLE`、`TASK_EXPIRED`、任务通知邮件、技能与效率因子、`effective_work_seconds`、`activity_efficiency`、`BodySettlement.work_efficiency`、`sleep.work_efficiency`、模型阶段 `work_review`（校验器与 `decision_statement` 同列拒绝）。

<a name="spec-rrs-观察与工具面"></a>
## 观察与工具面

- 目标类：`TARGET_KINDS` 加 `app`，删 `content`、`game`、`catalog`；app 目标形状见「娱乐」；食品目标见「店铺」；任务与工作活动目标见「工作」；地点目标见「地图」；`activity` 目标可选 `last_report`。
- 工具：原生工具 11 个（`start_activity`、`send_message`、`recall_task_summary`、`report_status`、`pause_activity`、`conclude_activity`、`revise_day_plan`、`query_world`、`memory_recent`、`memory_search`、`memory_time_range`）；`custom/envs/life_env.py::_STABLE_TOOL_SURFACE` 八个名字（六个世界请求 + `recall_task_summary` + `query_world`）；`REQUEST_MODELS` 与 `TOOL_DESCRIPTIONS` 删 `submit_work`；`TARGET_TOOLS` = 控制工具 + `send_message`；`_tools_for("task")` 不再带 `submit_work`。
- `callable_index` 三个键：`lifestyle`、`goods`（按食品类别标签分组）、`query_categories`；没有 `contents`、`apps`。
- 候选与呈现：候选种类 `product`、`lifestyle`、`task`；商品 `object_id = "<store_id>:<food_id>"`（标签「<店>出售的<食品>」不变），商品目标 = 当前店铺菜单 + 可经 app 外卖的食品，受既有呈现上限与按 `food_id` 的轮转；排序键无口味、无 `retrieval_index_threshold`；`ELIGIBILITY_OUT_OF_STOCK`、`ELIGIBILITY_DELISTED`、`ELIGIBILITY_CLOSED_HOURS` 删除；生活活动的地点类资格不变。
- 私有观察：`data.foods[]`、`data.apps[{app_id, name, content_count}]`、`data.contents[{content_id, title, kind}]`、`data.work_topics[{topic_id, topic, target_seconds, worked_seconds}]`、`data.locations[]`、`data.routes[]`、`app_pushes`（当前 session）、`world_query_apps[{app_id, name, store_location_ids}]`（取代 `world_query_channels`）；`discovery_catalog`、`game_catalog`、字幕与材料揭示不再出现。
- `query_world`：六键不变；来源解析接受店铺或 app（`world_query_apps`）；`_category` 为食品类别标签。
- 标题与可读名：`ACTIVITY_KIND_LABELS["use_app"] = "用手机"`、`ACTIVITY_DOING_LABELS["use_app"] = "刷手机"`、`ACTION_PHRASES["use_app"] = "用手机 app"`；活动标题：app 使用取 app 名（快照带 `content_title`），工作取主题；`OBJECT_PREFIXES["app"] = "应用"`；`src/worldmock/life/readable.py::PLACE_TYPES` 换成八个 `LocationKind` 的中文名。
- 进度层记录类型：加 `app_push`、`work_completed`，删 `task_deadline`；记录形状按 B-4，`app_push` 不受保护、按容量折叠。
- 指纹：`tool_schema_sha256` 与 `prompt_sha256` 按 B-19 的阶段变动；`code_sha256` 随每个代码阶段变动；落地后此前的运行、检查点与导出都不能再恢复或重放，也不可合并比较；没有测试、夹具或现行文档钉指纹字面值。

<a name="spec-rrs-系统层"></a>
### 系统层

十八条与下标不变；按 B-1 改写第 2、7、8、12、13、17 条，其余逐字不动。落地后唯一权威文本在 `docs/CONTRACTS.md#contracts-model-request` 的系统层 JSON 块，模块常量 `custom/agents/life_prompt.py::LIFE_SYSTEM_LAYER` 与之逐字相同；只读系统层不变。

```text
第 2 条
每次决策恰好提交一个世界请求：start_activity、report_status、pause_activity、conclude_activity、revise_day_plan、send_message 之一；提交前可以查询自己的记忆、回看任务或查询已知世界。只读查询可以同批，世界请求单独提交。

第 7 条
时间只在你有进行中的活动时推进；没有进行中的活动时，同一时刻会继续请你决策。等待与睡觉也是活动。醒着时，活动（等待也一样）一般要做满工具层写的时长才会再请你决策，做得久的中途隔一阵也会请一次；自己下的单送到、提醒到点、有人找你、有 app 推送、到了定下的上下班时刻、写明的休息时段开始或结束，到时也会请你决策，手上的活动照常进行，哪一次想停都可以结束。所以空下来不必干等，像平常人那样照自己的习惯做点别的，刷刷手机、看点东西、收拾一下都算，不用等闷了才做；等待只留给几分钟内就有事请你决策的时候。

第 8 条
你可以同时做两件事。环境只拦同一种的两件：吃、用东西算一种，工具层里方式为「进行」的都算生活活动这一种（不看名称），用手机 app 算一种，读信、等待、工作、出行、和人说话各算一种。不同种的两件环境不拦，但没拦不等于做得到，做不做得到由你照常理判断：边吃边看、边吃边刷手机、边做家务边听，做得到；两只手都占着的事，比如洗漱、洗碗、打扫，配不了吃东西，也配不了拿着手机看；你自己觉得别扭的组合就不要一起做，一件做完再做另一件。想一起吃或喝的两样东西，在同一个 start_activity 里写成两项，算一顿；已经在吃了，另一样等这一顿吃完再开始。开车时只能再和人说话，睡觉时不做别的。手上有一件在进行时，轮到你决策就可以再开始一件，原来那件照常进行。同时进行的最多两项，刚结束还没收尾的也算，暂停的不算，恢复暂停的活动要有空位；活动一结束，同一时刻先用 conclude_activity 收尾，再做别的。

第 12 条
名称照工具层、待办层中的写法填写；商品、生活活动、app、地点都可以直接写名称，即使此刻没有展示；写得不准时会返回最接近的候选，不会替你改。娱乐只选 app、方式写打开，看什么由 app 决定；这段停留或路程里收到推送后打开那个 app，先看的是推送的内容，不打开也可以。

第 13 条
start_activity 一项一个对象，写 target、way、from、to；方式决定动作。to 写 null 统一表示本人，给他人写实名；给予必须写他人，其他非转移动作的 from、to 都写 null。购买 from 可写门店或 app，prefer 必须写最快拿到或到手价最低；不购买时 prefer 写 null。替别人办事时用 answering 关联未回复消息。

第 17 条
工作要在要求的地点做，按主题计工时：播报时用自己的话写这段做了什么、做到哪里；写的进度不改变工时，做满目标工时环境会结束它。
```

文本必须同时满足 `tests/integration/test_seven_layer_request.py`、`tests/integration/test_tool_description_references.py` 与 `tests/integration/test_life_agent_protocol.py` 的既有约束：十八条；「上班安排」只在第 1、14 条；「待办」只在第 1、4、6、12 条；`reason` 只在第 11 条；「只是登记」「available 为 false 的会被拒绝」各一次；「同时进行的最多两项」「暂停的不算」「恢复暂停的活动要有空位」「环境只拦同一种的两件」「原来那件照常进行」只在第 8 条；「到时也会请你决策，手上的活动照常进行」「等待只留给几分钟内就有事请你决策的时候」只在第 7 条；「到时不一定会请你决策」「不为凑钟点干等」只在第 14 条；「不用撑到某个钟点」只在第 16 条；第 15 条的「」引语恰好三处；不写工具用途句、不含语言指定词、不新增行动次序；被取代的旧读法（`tests/integration/test_seven_layer_request.py` 登记的名额、饭点、送达与就寝四处旧句）不再出现。模块常量里第 7、14、15、16 条的登记原文各落在同一源码行内（B-1）。`TOOL_DESCRIPTIONS` 删 `submit_work`，其余用途句不变。

<a name="spec-rrs-状态事件检查点与导出"></a>
## 状态、事件、检查点与导出

- `WorldState`（`WORLD_SCHEMA_VERSION = 10`，`src/worldmock/life/models.py::WORLD_SCHEMA_VERSION`）：保留 `schema_version`、`version`、`current_time`、`end_time`、`run_id`、`timezone`、`scenario_sha256`、`content_manifest_sha256`、`agents`、`accounts`、`activities`、`sessions`、`deliveries`、`contact_holdings`、`pending_events`、`processed_actions`、`presentation_state`、`same_moment_presentation`（减 `pinned_supply_ids`）、`random_seed`、`random_state`；删除 `supply_stock`、`supply_quotes`、`opening_exceptions`、`paid_task_ids`；结构 9 及更早在打开、恢复、重放与交付门禁处拒绝。
- `AgentState`：删除 `visible_supply_ids`、`supply_browsed_at`、`supply_browsed_location`、`taste_weights`、`skill_levels`、`qualifications`、`visible_task_ids`、`known_task_ids`、`material_reveals`；`inventory{location_id: {food_id: 份数}}`；新增 `content_progress{content_id: ContentProgress}`；不新增 `work_progress`（工时由活动推导）。
- `SessionState`：新增 `app_pushes: dict[app_id, AppPush{push_id, content_id, issued_at, status: pending 或 opened 或 released}]`，默认空；session 校验补「推送只引用目录里的 app 与内容」；已结束的 session 保留条目作历史。
- `ActivityState`：删除 `effective_work_seconds`、`playback_speed`；`ActivityChange` = `pause`、`resume`、`cancel`；`boundary_type` 删 `game_feedback`；`data` 按类型：travel `{trip_id, path, distance_meters, travel_payment, arrival}`；use_app `{app_id, content_id, content_kind, summary, start_position_seconds, pinned_by_push_id, draw_index, played: [{content_id, kind, from_position, to_position}]}`；work `{topic_id, worked_before_seconds}`；consume `{request, consumed, opened, discarded, food_energy_kcal_per_hour}`；lifestyle `{effects}`；chat、call 不变。
- `DeliveryState{order_id, agent_id, food_id, store_id, app_id（到店为 null）, seller_account_id, quantity, unit_price_fen, delivery_fee_fen, total_price_fen, destination_id, fulfillment: delivery 或 pickup, recipient_id, request_message_id, created_at, expected_at, status, path, distance_meters, prep_seconds}`；`PurchaseItem{store_id, food_id, quantity, recipient_id, request_message_id}`；`PurchaseOrderRequest{items, fulfillment, destination_id, app_id}`；`ConsumeRequest` 不变；`ContractKind` 删除，`FulfillmentKind`、`TravelMode` 不变。
- 预排事件：`app_push` 的 `scheduled_id` 按 B-16，`due_at` 为当地整点，`data = {}`；`ScheduledOutcome` 新增 `wake: str 或 None` 与 `source_kind`，`src/worldmock/life/time_advance.py::TimeAdvanceCoordinator._process_scheduled` 读它们，`_SCHEDULED_WAKE_FLAGS` 删除（类型→唤醒只有一处来源）。
- 环境事件（`environment_event.data.kind`）：`app_push{push_id, app_id, app, content_id, title, kind, summary, session_id, issued_at}`（人物可见，`source_kind = "environment"`，唤醒 `app_push`）或 `{pushed: false, session_id}`（系统可见）；`work_topic_completed{topic_id, worked_seconds}`（人物可见）；删除 `location_track`、`price_change`、`supply_quote_change`、`product_release`、`work_available`、`task_expired`；`Event` 与 `Checkpoint` 结构仍为 1，卡片结构 3 不变。
- `activity_progress.data`：`content{content_id, app_id, kind, position_before_seconds, position_after_seconds, completed, completed_rounds_after}`（取代 `revealed`）、`work{topic_id, worked_seconds, remaining_seconds}`（取代 `activity_efficiency`、`work_material`）、`consumption`（见「三类食品与进食」）、`arrival` 不变。
- `action_result.data`：删除 `category_matches`、`work_review`、`payment_fen`、`task_id`、`browse_result`；`orders[]` = `DeliveryState` 的 JSON；`inventory_changes[{order_id, store_id, food_id, location_id, quantity}]`；`details` 键 `purchases`、`given`、`planned`、`arrival`、`mail`、`app_content`、`work`、`outcome`（`items[{food, eaten, discarded, remaining}]`）、`notes`、`linked_orders`。
- 已核验经历：`consumed`（见「三类食品与进食」）、`played{content_id, app_id, position_before_seconds, position_after_seconds}`、`arrived`、`waited` 不变；记忆层 `settled` 句写「用<app>看了《A》《B》（《C》看了一部分）」，工作句不带有效工时，没有提交句。
- 运行清单 `schema_version: 3`：`environment_identity{environment_id, environment_version, rules_version, catalog_content_sha256, map_sha256}` 取代 `map_dataset`、`category_contract_version`、`category_contract_sha256`、`exchange_rules_sha256`、`catalog_content_sha256`；`stage_model_identity.stages = {action}`；`src/worldmock/run_setup.py::validate_stage_model_identity` 拒绝 `work_review`（`RETIRED_STAGE_IDENTITY_KEY` 加入）；`src/worldmock/life/run_identity.py` 的 `category_identity`、`require_category_identity`、`open_category_validated_store` 改名 `environment_identity`、`require_environment_identity`、`open_environment_validated_store`，机制不变（不符即 `IntegrityError("Runtime environment identity mismatch")`）；`EngineContext.validate` 另查持有的食品标识存在。
- 导出清单 3：工件 `events`、`activities`、`verified_experiences`、`purchases`、`app_usage`；`purchases.jsonl` 行 `{agent_id, order_id, food_id, store_id, app_id, quantity, unit_price_fen, delivery_fee_fen, total_price_fen, fulfillment, recipient_id, request_message_id, created_at, action_id, source_event_id}`（`order_attempt` 行保留，`app_browse` 删除）；`app-usage.jsonl` 事实类型 `app_use{agent_id, activity_id, app_id, started_at, ended_at, status, source_event_id}`、`content_consumed{agent_id, activity_id, app_id, content_id, kind, position_before, position_after, completed, push_id, source_event_id}`、`push_delivered`、`push_opened`、`push_released`（各 `{agent_id, push_id, app_id, content_id, session_id, occurred_at, source_event_id}`）；`activities.jsonl` 出行行保留起点、终点、路径、距离，行要求 `route_progress_meters`、`destination_id`、`travel_mode`、`media_position_seconds`，不再要求 `effective_work_seconds`、`playback_speed`；`frozen_run_manifest` 带 `environment_identity`。
- 质量报告 5：新增 `content_contact{by_app, by_kind, repeat_watch}`（`repeat_watch = completed_rounds > 1`）、`app_pushes{pushed, opened_in_session, consumed_pinned, released_by_completion, expired_with_session, outside_window_violations}`、`work_topics{topics, completed, worked_seconds_by_topic, reports}`；`model_usage.by_purpose` 无 `work_review`；`conversion_efficiency.work` 删除；唤醒原因分桶读足迹侧 22 个名字。
- 交付清单 3：复制 `environment_identity`，列出 `app_usage`；交付门禁 `schema_version == 10`；LabelKit 窗口的 `app_push` 公开投影按 D-45，手机行来自 `app-usage.jsonl`。
- 私有字段禁键表：加 `app_id`、`content_id`、`push_id`、`trip_id`、`path`、`food_id`、`store_id`、`topic_id`；删 `route_id`、`supply_id`、`price_version`、`taste_weights`、`game_catalog`、`material_reveals`。
- 查看器：`tools/run-viewer.html` 与 `run-viewer-context.js` 的标签加 `use_app`、「打开」、`app_push`（「app 推送」）、`work_topic_completed`，删被删事件与码的标签，唤醒标签 22 个；名称索引读 `foods`、`apps`、`contents`、`work_topics`；出行显示起点、终点与路径名称，不画图；`scripts/render_day.py` 的 `NAME_KINDS` 为 `foods`、`locations`、`messages`、`apps`、`contents`、`work_topics`、`people`，媒体标签「用<app>看《title》」，工作标签写主题；`scripts/inspect_run.py` 的 `walking_share` 从 `activity_progress` 的出行方式算。
- 结构版本一次升级：世界 10、场景 7、运行清单 3、导出清单 3、质量报告 5、交付清单 3、目录清单 `format_version: 1`、地图 `schema_version: 1`、`RULES_VERSION = 1`；每个入口拒绝旧版本，不迁移、不双读。

<a name="spec-rrs-删除清单"></a>
## 删除清单

删除失去生产用途的模块、符号、字段、码、名字、测试与夹具；通用库另有现行用途的保留。「钉住处」写今天把该项钉在台账、夹具或守卫里的位置，删除时同一提交解除：[TI] `tests/tool_interaction_acceptance.py` 与 `tests/test_tool_interaction_acceptance.py`；[DF] `tests/dayrun_fidelity_acceptance.py`；[PP] `tests/persona_prompt_acceptance.py`；[TE] `tests/time_and_energy_acceptance.py`；[EW] `tests/errands_workday_acceptance.py`；[IF] `tests/idle_fillers_acceptance.py` 与 `tests/fixtures/idle-fillers-acceptance.json`；[SR] `tests/shared_rules_acceptance.py`（行来自 `docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-共享规则`）；[LA] `tests/fixtures/layering-acceptance.json` 与 `tests/test_layering_acceptance.py`；[OWN] `tests/test_single_source.py` 的 `OWNERS`；[API] `tests/fixtures/layering-public-api.json`；[MB] `tests/fixtures/mutation-symbol-baseline.json`（字节不动，守卫放宽）；[DOC] `tests/fixtures/current-feature-owners.json` 与 `tests/fixtures/acceptance_requirements.json`；[IG] `tests/test_import_graph.py`；[QT] `scripts/fresh_check.py` 的 `QUICK_TESTS`。符号以 `模块::名字` 写，指删除前的定义处。

<a name="spec-rrs-删除生产模块"></a>
### 生产模块（整个文件）

| 阶段 | 文件 | 钉住处 |
| --- | --- | --- |
| 1 | `src/worldmock/categories/`（`__init__`、`common`、`models`、`contract`、`rules`、`operators`、`pairing`、`provenance`、`identity`、`loader`、`maintenance`、`cli`）；幸存定义迁移：`DeepFrozenModel`、`require_finite_number` → `src/worldmock/base_models.py`，`Conservation` → `src/worldmock/resources/models.py`，`CategoryError` → `src/worldmock/catalog/errors.py` 的 `RegistrationError`，算子 → `src/worldmock/exchange/operators.py`，身份函数 → `src/worldmock/catalog/identity.py` | [OWN] `categories/common.py`、`categories/provenance.py` 行；[SR] 品类与目录错误、安全文件、公共枚举 行；[IG] `worldmock.cli → worldmock.categories.cli` 延迟导入行；[TI] 变异 `categories.models::CategoryContract.is_descendant`、`categories.contract::_validate_values`；[MB] `tool:category_inheritance_exact_only`、`tool:price_outside_bounds`、`tool:effective_product_price_range_reversed` |
| 1 | `src/worldmock/catalog/compiler.py`、`builder.py`、`inspect.py` | — |
| 1 | `src/worldmock/corpora/storefront.py` | [TI] 行 类别供给、地点定价；变异 `assortment_objects`、`validate_assortments`、`frozen_price`、`frozen_price.percent`、`_validate_quotes`；`FEATURE_SOURCES`；[MB] `tool:restaurant_menus_identical`、`tool:assortment_window_cut`、`tool:assortment_full_pool`、`tool:price_position_ignored`、`tool:quote_bound_check_removed` |
| 1 | `src/worldmock/life/category_contract.py` | — |
| 1 | `src/worldmock/life/map_models.py`、`src/worldmock/life/map_data.py` | [TI] `FEATURE_SOURCES`、变异 `map_data::select_map`；[MB] `tool:selection_incomplete_allowed`；[DF] `FEATURE_PRODUCTION_FILES`、`_MAP` 行；[IG] 迁移声明行 `worldmock.life.map_models`（`DataSource`、`OpeningInterval`） |
| 1 | `src/worldmock/life/travel_time.py` | [TE] `tests/life/test_travel_time.py` 的出行进度节点（改指 `tests/life/test_travel_graph.py` 同名节点） |
| 1 | `src/worldmock/life/opening_hours.py` | — |
| 1 | `src/worldmock/life/storefront_state.py` | [TI] 变异 `storefront_state::changed_quote`、`FEATURE_SOURCES`；[PP] 文件清单 |
| 1 | `src/worldmock/life/inventory_recipe.py` | [TI] 行 料理；变异 `inventory_recipe::_inventory_from_balances` ×2；`FEATURE_SOURCES`；[MB] `tool:recipe_no_input_debit`、`tool:recipe_no_output_credit` |
| 1 | `src/worldmock/profile/coverage_apps.py` | [DF] `_COVERAGE_APPS`、`_CHANNELS`、`FEATURE_PRODUCTION_FILES`、`FEATURE_TEST_FILES`（`tests/profile/test_coverage_apps.py`）；[DOC] 裁决 S7 |
| 1 | `src/worldmock/profile/taste.py` | [DF] 下限缓解 行（经 `tests/life/test_relief_floor.py`） |
| 1 | `scripts/check_reachability.py` | — |
| 4 | `src/worldmock/life/game.py` | [TE] 游戏会话节点；`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-游戏简化` 被取代 |
| 5 | `src/worldmock/life/work_review.py`、`src/worldmock/life/work_review_runtime.py` | [API] `prepare_pending_work_review`；[LA] `NEW_PRODUCTION`、显式引擎协作 实现行；[DF] `FEATURE_PRODUCTION_FILES`；[TE] `_WORK_REVIEW` 行 |
| 4 | `pyproject.toml` 的 `textworld==1.7.0` 与 `external` 标记条款；`scripts/bootstrap_environment.py` 的 TextWorld/Inform7 步骤 | `tests/test_test_tiers.py` 的 TextWorld 用例 |

<a name="spec-rrs-删除生产符号"></a>
### 生产符号（文件保留）

| 阶段 | 符号 | 钉住处 |
| --- | --- | --- |
| 1 | `worldmock.life.travel::sample_location_track`、`LocationTrackRequest`、`_route_geometry`、`_haversine_meters`、`_cumulative_distances`、`_interpolate`、`LOCATION_TRACK_KIND`、`TRACK_SAMPLING_METHOD`、`TRACK_EARTH_RADIUS_METERS`、`route_for`、`validate_routes` | — |
| 1 | `worldmock.life.wayfinding::reachable_places`、`delivery_route_code`、`delivery_step`、`UNREACHABLE_STORE_STEP`、`UNREACHABLE_DELIVERY_STEP`，`go_step` 的不可达分支 | [DF] 浏览与提示 行（`tests/life/test_wayfinding.py` 四节点） |
| 1 | `worldmock.profile.injection::taste_warnings`、`game_weight_warnings`、`delivery_route_warnings`、`TOP_CONTENT_GENRES` | [DF] `WARNING_CODE_TESTS` 四个码；行 输入警告「十个警告码各一例」→「六个」 |
| 1 | `worldmock.life.load_checks::load_purchase_channels`、`check_channel_admission`、`_channel_name_problems`、`_channel_store_problems` | [DF] 加载校验 行 |
| 1 | `worldmock.catalog.admission::validate_world_categories` 及 `_check_meals`、`_check_content*`、`_check_drama_chinese`、`_check_partition_*`、`_retrieval_entries`、`_check_work`、`_check_home`、`_check_office`、`CONTENT_CORPORA`、`CategoryRequirements`、`MealRequirements`、`ContentMetadataRequirements`、`WorkMaterialRequirements`、`PantryRequirements`、`validate_scenario_against_catalog` 的边、规则与墓碑半部、`validate_task_assignees` | [TI] 变异 `scenario::_check_catalog_references` |
| 1 | `worldmock.scheduling.events::ScheduledKind.PRICE_CHANGE`、`SUPPLY_QUOTE_CHANGE`、`PRODUCT_RELEASE`；`scheduling.compile::_quote_change_rows`、`_price_change_rows`、`_product_release_rows`；`scheduling.effects::_price_change`、`_quote_change`、`_product_release`、`_scheduled_source` | [TI] 变异 `scheduling.effects::_price_change`（`tests/life/test_storefront_schedule.py` 节点） |
| 1 | `worldmock.life.models::SupplyQuoteState`、`WorldState.supply_stock`、`supply_quotes`、`opening_exceptions`、`_validate_supply_visibility`、`_validate_opening_exceptions`、`OpeningInterval`、`DataSource`、`AgentState.visible_supply_ids`、`supply_browsed_at`、`supply_browsed_location`、`qualifications`、`taste_weights`、`same_moment_presentation.pinned_supply_ids`、`PurchaseItem.supply_id`、`price_version`、`DeliveryState.supply_id`、`location_id`、`price_version`、`route_id`、`source`、`ContractKind` | [EW] 变异 `models::WorldState.schema_version` 行文、`errands:version_8_world_state_accepted`；[DF] 文件清单 |
| 1 | `worldmock.life.state_commit::flush_supply_visibility`；`projection::visible_supplies`、`SUPPLY_VISIBILITY_TTL_SECONDS`；`discovery::stage_visible_supplies`、`range_status`；`observe::_complete_product_browse`、`_shared_supply_window`、`BROWSE_TARGETS`；`storefront_runtime` 的可见性门与 `SUPPLY_NOT_VISIBLE`；`ActivityKind.BROWSE`；`footprints.purchases::_app_browses`、`BROWSE_ACTIVITY_KIND` | [DF] 浏览与提示 行（`tests/life/test_browse_and_hints.py` 八节点）、裁决 D17、S9；[MB] `tool:pagination_supply_window` |
| 1 | `worldmock.life.semantics::validate_assortments` 调用、主次版本钉、`admit_reachable_categories`、`purchase_pairing`、`purchase_coefficient`、`contract_payment`、`contract_match`、`travel_match`、`travel_fare`、`_travel_fare`、`travel_time_factor`、`travel_planned_seconds`、`travel_time_profile`、`_check_route_fares`、`_check_contract_bindings`、`_contract_*`、`_check_scheduled_prices`、编译对象的 `_assemble_items`、`_consume_edge`、`_inventory_object_ids` 配方并集、`_catalog_refs`、三个资源的目录查找、`_alert_thresholds`、`_drain_rules`、`TRAVEL_OBJECT`、`CONTRACT_OBJECT_IDS`、`ScenarioSemantics.catalog/refs/travel_time_profile/purchase_channels` | [SR] 行 合同对象集合、出行时长（归属符号保留、依据改写）；[DF]、[EW]、[PP] 来源清单 |
| 1 | `worldmock.life.scenario::InventoryItemConfig`、`ProductConfig`、`ProductReference`、`SupplyReference`、`_PRODUCT_USE_MODES`、`MapDatasetReference`、`CatalogVersionPin`、`PriceChangeConfig`、`ProductReleaseConfig`、`LifeScenario.map_dataset/products/supplies/price_changes/product_releases/recipe_output_bindings/phone_simulation_config(+_sha256)`、`_validate_recipe_bindings`、`_check_catalog_references` 的对象半部、`AgentConfig.qualifications`、`MessageConfig.related_task_id`、`BodyConfig.work_efficiency_factors/work_notes_multiplier/food_efficiency_curve/awake_sleep_debt_per_hour/sleep_recovery_per_hour` | [EW] `_SCENARIO_SOURCE`、变异 `errands:scenario_structure_four_admitted` 行文；[TI] `test_tool_interaction_declared_interface_contains_only_new_parameters` 的 `WorkTaskConfig` 断言 |
| 1 | `worldmock.life.world_setup::_check_work_material`、`initial_supply_state` 接线、`load_catalog`/`load_map_dataset`/`load_purchase_channels` 调用、`_run_manifest` 的 `map_provenance` | [TI] 变异 `world_setup::load_runtime_scenario`（`work_material_unchecked`）；[MB] `tool:work_material_unchecked` |
| 1 | `worldmock.life.engine_context::EngineContext.validate` 对 `validate_storefront_state`、`validate_routes`、`validate_game` 的调用 | — |
| 1 | `worldmock.cli` 的 `category` 子命令（`add_category_arguments`） | [IG] 延迟导入行 |
| 1 | `worldmock.validation::_require_world_categories`、`_require_task_assignees`、`load_catalog` 调用；`runtime::_admit_scenario`；`run_setup::_content_genre_catalog`、`_taste_entries`、`_card_taste_weights`、口味权重注入；`runtime._society_specs` 的口味注入；`world_setup._initial_agent` 的口味读取 | [DF] 场景作息 行；`tests/integration/test_host_branch_coverage.py` 的口味节点 |
| 1 | `worldmock.life.motivation::_delivery_route_seconds`、`_food_supply_row` 的报价/库存/目的地读取、`MotivationContext.routes/purchase_amount/purchase_pairing_states/taste_weights`、`_preference`；`candidates` 的口味桶（`_taste_weight`、`_bucket`）、`ELIGIBILITY_OUT_OF_STOCK`、`ELIGIBILITY_DELISTED`；`presentation::PoolEntry.stock`、`_stock_reason` | [DF] `_INDEX`、`_PRESENT` 行 |
| 1 | `worldmock.life.activities::relief_preference`、`entertainment_relief_rate` 与 `content_entertainment_relief_rate` 的 `w` 参数、`_work_factors`、`_advanced_activity` 的工作分支、`playback_speed` 处理；`sleep::work_efficiency` 曲线；`body::_work_efficiency`、`BodySettlement.work_efficiency`；`BodyInstanceConfig.entertainment_relief_preference_floor` | [OWN] `life/activities.py: relief_preference` 行；[DF] `_RELIEF`、`_PREVIEW` 行 |
| 1 | `worldmock.life.basket::_Fulfillment.route`、`_delivery_route`、`_validate_storefront`（营业、库存、报价、资格）、`require_runtime_rule` 调用、`energy_kcal_per_unit` 读取、`effect/consumable` 食品处理、`validate_meal_inventory` 的配方分支 | [DF] 变异范围 `basket.py`；[PP] `_MEAL` 行（`tests/life/test_meal_outcome.py` 节点保留） |
| 1 | `worldmock.life.admission::_delivery_refusal`、`Item.destinations/store`、`Context.routes` 的配送用途；`order_routing::undeliverable` 路线原因、`_destination` 范围检查；`named_purchase::_unreached`、`_undelivered`、`_undeliverable`、`_delivery_gap`、`_delivery_next_step` 的路线分支；`purchase_offers::_channel_rows` 的手机配置来源与 `supply_id` 决胜 | [PP] `_NAMED`、`_GIVING` 行；[DF] `_STAGES`、`_NAMED`、`_GIVING` 行 |
| 1 | `worldmock.life.action_evidence` 的 `category_matches` 副本；`session_actions` 的 `category_matches` 写入（购买、食用、出行）、`_consume` 配方分支、`WAYS["制作"]` 分派；`interaction_menu::merged_selection` 的制作分句；`vocabulary` 方式 `制作` 与同义词 `做饭`；`activity_evidence::_item_change_outcome` 配方分支、`meal_consumption_data.inventory_transform`；`time_advance::_apply_consume_effect` 配方分支；`observe` 的配方持有检测、`_recipe_product_ids`；`tool_layer::_consume_check` 的配方与 `require_item_rules` 路径、`_source_note`、`_SOURCE_KINDS`、`_purchase_check` 的目的地/营业/库存分支；`callable_index::_goods_entries` 的品类树查找；`world_query::_category` 的对象查找、`world_query_channels` 键（→ `world_query_apps`）；`result_projection::public_supply_row._source_reference` | [TI] 变异 `routing::_item_errors.purchase` 保留；[MB] `tool:native_purchase_route_loses_way` 不变 |
| 1 | `src/worldmock/data/affordances.toml` 条目 `[kinds.watch]`、`[kinds.play_game]`、`[kinds.browse]`、`game_boundary`、`families.entertainment.content_kinds.*`、生成器 `catalog_visibility`、家族 `browse`、`kinds.work.target_discovery`（任务）；`worldmock.catalog.affordances` 的 `content_kind:` 条件维度（`_check_claim_content_kinds`、`_CLAIM_CONDITION_DIMENSIONS`）、`game_boundary`、`GENERATOR_PARAM_KEYS["catalog_visibility"]`、`attention_required_kinds` | [TE] `_SHADOW` 行；`tests/catalog/test_engine_package_resources.py` |
| 1 | `worldmock.life.retrieval::RetrievalEntry`、`RetrievalIndex`、`build_index`、`search`、`facet_counts`、`reachable`、`reachability_report`、`duration_bucket`、`genre_values`、`entry_from_content`、`FACET_NAMES`、`CATEGORY_PARTITION_*`（`LOCATION_KINDS`、`ANYWHERE_KIND`、`place_kinds`、`item_kinds`、`feasible_kinds` 保留） | — |
| 1 | `worldmock.life.run_identity::category_identity`、`require_category_identity`、`open_category_validated_store`（改名，见「状态、事件、检查点与导出」） | — |
| 4 | `worldmock.life.content::SubtitleCue`、`load_subtitles`、`_parse_block`、`_srt_seconds`、`reveal_watch`、播客音轨哈希分支、`CONTENT_RESUME_MINIMUM_SECONDS`；`activity_evidence.dedupe_revealed_cues`；`ActivityKind.WATCH`、`PLAY_GAME`；`CONTENT_KINDS` 八成员字面量；`ContentItemConfig.timeline_path/timeline_sha256/audio_path/audio_sha256/entertainment_relief_per_hour/experience_label`；`LifeScenario.contents/games`；`vocabulary.CONTENT_WAYS` 与方式 观看、接着看、从头再看、接着听、从头再听、游玩 及同义词；`readable.BROWSE_LABELS`、`ACTIVITY_KIND_LABELS["watch"/"play_game"]`、`ACTION_PHRASES["watch"/"play"]`；`ActivityState.playback_speed`、`ActivityChange.CHANGE_SPEED`、`ChangeActivityRequest.change_speed`；`observe::_content_pool/_content_catalog/_sample_contents/_content_progress` 的分段字段、`_game_pool/_game_catalog`、`data["discovery_catalog"]`、`data["game_catalog"]`；`projection::_content_object_rows/_watch_row/_game_object_rows`；`named_catalog::_content_rows/_game_rows`；`callable_index::_content_entries` 与 `contents` 键；`tool_layer::_content_state`、`_timed_check`/`_derived_seconds` 的观看与游戏分支；`durations.content_remaining_seconds/content_duration_seconds` 与 `derived_activity_duration` 的 WATCH 分支；`candidates.CandidateEntry.content_kind`、`CandidateKind "content"/"game"`、`_resource_reason` 的内容类型分句；`motivation._entertainment_candidates/_watch_candidate/content_segments/content_category_counts`；`MotivationProfile.attention_prior`；`footprints.quality._watch_repeats`；`evaluation.public_evidence._TARGETED_ACTIONS` 的 `watch/play/browse` 行；`custom.agents.life_memory._fact_line` 的 `played` 措辞；`rules.games/game()/GAME_NOT_FOUND`、`engine_context.validate_game`、`presentation.GAME_CATEGORY`、`world_ops._put_agent_and_defer_game_turn`、`decision_coordinator._after_success` 的游戏回合查找、`coverage_targets._MEDIA_KIND_LABELS["game_session"]`、`boundary_type == "game_feedback"` 分支 | [TI] 变异 `content::reveal_watch.changed`（`habit_media_progress_not_persisted`）；[MB] `tool:habit_media_progress_not_persisted`；[OWN] `life/durations.py: content_duration_seconds` 行；[SR] 行 活动时长（内容半部）；[DF] `_INDEX` 内容节点；[TE] `_NEWS_FEED` 行、游戏会话与驾车播客两节点 |
| 5 | `custom.envs.life_env::submit_action` 的评审等待与 `_STABLE_TOOL_SURFACE["submit_work"]`；`action_evidence._work_review_{request,response,failure,terminal}_draft`；`PendingAction.work_review/work_review_event_id`；`decision_coordinator._OPS_COMPONENTS["submit_work"]` 与 `WORK_REQUIRES_REWORK` 分支；`session_actions._submit_work/_submit_work_action/public_work_review`；`result_projection._public_review/WORK_REVIEW_SUMMARY_FIELDS/REFERENCE_ASSESSMENT_FIELDS/DELIVERY_ASSESSMENT_FIELDS`；`models.MeetingMinutes/SubmitWorkRequest/SubmitWorkActionRequest/REQUEST_MODELS["submit_work"]`；`prompt_schemas.TARGET_TOOLS` 的 `submit_work`、`_RESULT.work_review`；`life_prompt.TOOL_DESCRIPTIONS["submit_work"]`；`life_agent` 的 `submit_work` 名称校验；`run_setup._WORK_REVIEW_THINKING/_WORK_REVIEW_TOOL_CHOICE` 与阶段键 `work_review`；`model_feedback.tool_arguments` 的 `work_review` 标志；`footprints.quality`、`delivery.windows`、`evaluation.behavior_metrics`、`decision_start`、`scripts/inspect_run.py` 的 `purpose == "work_review"` 分支；`work::load_work_input`、`visible_work_material`、`reveal_work_progress`、`load_work_oracle`、`_load_reference_summary`、`_load_scenario_reference_summary`、`_load_ami_reference_summary`、`_read_hashed_file`、`_read_regular_file`、`_local_name`、`prepare_work_submission`、`settle_work`、`submission_refusal`、`completion_outlook`、`validate_work_duration`、`_pay`、`WorkOutcome`、`WorkMetrics`、`WorkSubmission`；`time_advance` 的工作揭示分支；`activity_evidence.record_material_reveal`；`AgentState.material_reveals/visible_task_ids/known_task_ids`、`WorldState.paid_task_ids`、`ActivityState.effective_work_seconds`、`data.material_position_chars/required_skill/skill_level/work_notes_multiplier`、`AgentConfig.skill_levels`、`AgentState.skill_levels`；`scheduling.events::WORK_AVAILABLE/TASK_EXPIRED`、`compile_schedule` 的任务循环、`effects._reveal_event` 的工作分支；`rhythm::_task_signature`、`_next_task_due`、`_warning_signatures["task"]`、`_commitment_signatures` 的任务分句、`_goal_and_deadline_pulses`/`_has_commitments` 的任务部分、`RhythmInputs.task_due`；`schedule::TASK_DUE_SOON_SECONDS`、`CLOSING_WARNING_SECONDS`、`WakeSnapshot.task_newly_visible/next_task_due_at/work_review_returned/location_closing_at/location_just_opened`、`NextDecisionQuery.next_task_due_at/location_hours_change_times/location_closing_at`、`rhythm._closing_at/_just_opened/_location_change_times/_warning_signatures["closing"]`；情绪脉冲 `work_review_passed/returned`、`task_deadline_missed`；`progress_layer._derived_records` 的 `task_deadline`；`memory_layer._world_lines` 的 `work_available` 句、`settled_text` 的提交句与有效工时分句；`activity_control.objective_progress` 的有效分句；`business_feedback` 的返工路径；`same_moment`/`HABIT_ACTIONS` 的 `submit_work`；`affordances.toml families.work.habit_members` 的 `submit_work`；`evaluation.public_evidence._world_request_shape(submit_work)`、`_work_result`、`_world_environment(work_available/task_expired)`；`evaluation.workload_metrics._settled_tasks`、`TaskSite.appointment_at`、`WORKLOAD_KEYS["effective_work_seconds"]`；`footprints.quality._work_efficiency_samples`、`conversion_efficiency.work`；`scripts/inspect_run.py` 里 `workday` 的任务与付款部分；`life.day_plan_inputs` 的任务待办与工作验收措辞；`opslog.OPS_COMPONENTS` 的 `review` | [TE] 绿色基线 六个评审节点、`_WORK_REVIEW` 行；[PP] 程序错误 恢复工作越过合同截止、工时达标与提交状态不一致、逾期工作从观察和工具中消失；[DF] `_EXCERPTS` 行 中文摘录；[EW] 变异 `duty_vanishes_after_work_submission`（改为 `duty_vanishes_after_topic_completion`）、`shift_gates_task_opening`（作废）；[TI] 变异 `scenario::runtime_tasks.opening`、`scenario::WorkTaskConfig.appointment`、`scheduling.effects::_reveal_event`、`rhythm::_wake_snapshot`（任务行）、`scheduling.compile::compile_schedule`（任务行）；[MB] `tool:work_delay_nine`、`tool:wrong_notification_recipient`、`tool:duplicate_input_opening`、`tool:appointment_mail_false_opening`、`tool:reminder_changes_appointment_opening`；[IF] 同步点 5（`tests/life/test_work_contracts.py` 的成员表移到 `tests/life/test_wake_reasons.py`） |
| 4 | `worldmock.life.schedule::WakeReason.TASK_AVAILABLE`、`TASK_DUE_SOON`、`WORK_REVIEW_RESULT`、`LOCATION_HOURS_CHANGE` 与 `footprints.models.WAKE_REASON_NAMES` 的对应行 | [IF] 九个同步点；[EW] `WAKE_REASON_NAMES` 变异行 |

<a name="spec-rrs-删除场景与目录字段"></a>
### 场景与目录字段

| 阶段 | 字段 |
| --- | --- |
| 1 | 目录：`category-contract.json`、`exchange-rules.json`、`objects.jsonl`、`resources.jsonl`、`overrides/`、`rules/travel-time.toml`、`rules/off-foods.mapping.toml`、`snapshots/`、`category_requirements`、`category_contract_version`、`category_contract_sha256`、`exchange_rules_sha256`、目录 `version_major/version_minor`；地图语料 `dataset.json`、`routes.json`、`supplies.json`、`category-assignment.json`、`supply-assumptions.json`、坐标、`business_kind`、`location_kinds`（地点）、`opening_calendars`、`required_qualifications`、`brand`、`affordances`、逐地点 `source`、`seller_account_id`、`initial_stock`、`fulfillment_modes`、`delivery_destination_ids`、`delivery_delay_seconds`；内容的 `genre`、`language`、`age_rating`、`timeline_path`、`audio_path`、分段与检索分区；`meals`、`ingredients`、`home-pantry`、`scene-stock`、`scenario-work`、`games`、`textworld`、`sintel`、`ami-es2002`、`open-food-facts`、`foods` 扩展与价格范围语料 |
| 2 | 场景：「场景结构 7」删除列表里的全部字段；`interaction_menus_by_location_kind` 的旧键 `merchant`、`wet_market` 与旧动作 `watch`、`play`、`browse` |

<a name="spec-rrs-删除错误码与名字"></a>
### 错误码与唤醒、预排名字

| 阶段 | 删除 | 改名 |
| --- | --- | --- |
| 1 | `DELIVERY_DESTINATION_NOT_SUPPORTED`、`DELIVERY_ROUTE_NOT_FOUND`、`DELIVERY_ROUTE_UNREACHABLE`、`DELIVERY_ROUTE_INFORMATION_UNKNOWN`、`FROZEN_ROUTE_NOT_FOUND`、`ROUTE_UNREACHABLE`、`ROUTE_INFORMATION_UNKNOWN`、`LOCATION_TRACK_WINDOW_INVALID`、`MERCHANT_CLOSED`、`MERCHANT_OPENING_UNKNOWN`、`SUPPLY_QUALIFICATION_REQUIRED`、`LOCATION_ACCESS_DENIED`、`SUPPLY_NOT_VISIBLE`、`SUPPLY_QUOTE_UNKNOWN`、`OUT_OF_STOCK`、`PRICE_VERSION_MISMATCH`、`ORDER_SELLER_MISMATCH`、`BROWSE_TARGET_NOT_FOUND`、`INVENTORY_RECIPE_INPUT_CATEGORY_MISMATCH`、`INVENTORY_RECIPE_OUTPUT_CATEGORY_MISMATCH`、`INVENTORY_RECIPE_OUTPUT_UNBOUND`、`INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE`、`INVENTORY_RECIPE_RESOURCE_UNKNOWN`、`INVENTORY_RECIPE_RULE_MISMATCH`、`INVENTORY_RECIPE_UNIT_MISMATCH`、`CATEGORY_NUMBER_INVALID`、`CATEGORY_PAIRING_NO_EFFECT`、`CATEGORY_PAIRING_UNDEFINED`、`RULE_REF_UNKNOWN`、`TOMBSTONED_REFERENCE`、`CATEGORY_WORK_*`、`CATEGORY_PARTITION_*`、`unmet` 值 `delivery_route`；输入警告 `TASTE_GENRES_UNMATCHED`、`CONTENT_WEIGHTS_ALL_ZERO`、`GAMES_WITHOUT_TASTE_WEIGHTS`、`DELIVERY_ROUTE_UNAVAILABLE`；预排 `price_change`、`supply_quote_change`、`product_release` | `ORDER_DUPLICATE_SUPPLY` → `ORDER_DUPLICATE_ITEM` |
| 4 | `CONTENT_TIMELINE_NOT_FOUND`、`CONTENT_END_EXCEEDED`、`PLAYBACK_SPEED_NOT_ALLOWED`、`GAME_NOT_FOUND`、`CONTENT_NOT_FOUND`；唤醒 `task_available`、`task_due_soon`、`work_review_result`、`location_hours_change`；预排 `work_available`、`task_expired`；`environment_event.data.kind` 的 `location_track`、`price_change`、`supply_quote_change`、`product_release`、`work_available`、`task_expired` | — |
| 5 | `WORK_INCOMPLETE`、`WORK_TASK_IN_PROGRESS`、`WORK_SUBMISSION_LATE`、`WORK_TASK_ALREADY_PAID`、`WORK_REQUIRES_REWORK`、`WORK_DURATION_EXCEEDS_CONTRACT`、`WORK_WOULD_MISS_DEADLINE`、`WORK_TASK_NOT_AVAILABLE`、`WORK_TASK_NOT_VISIBLE`、`EMPLOYER_FUNDS_INSUFFICIENT`；模型阶段键 `work_review` | `WORK_TASK_NOT_FOUND` → `WORK_TOPIC_NOT_FOUND`（"The named work topic is not assigned to you."，下一步 "Use an exact known work topic name."） |

<a name="spec-rrs-删除测试文件"></a>
### 测试文件

| 阶段 | 删除的测试文件 | 钉住处 |
| --- | --- | --- |
| 1 | `tests/categories/`（含 `conftest.py`、`scenario_fixtures.py`）、`tests/catalog/test_builder.py`、`test_inspect.py`、`test_category_admission.py`、`test_reachability_admission.py`、`test_storefront_admission.py`、`admission_fixtures.py`（`tests/catalog/conftest.py` 重写）、`tests/corpora/test_storefront_release.py`、`tests/life/test_category_runtime.py`、`test_category_runtime_edges.py`、`test_category_semantic_edges.py`、`test_storefront_category_integration.py`、`test_tool_interaction_recipe.py`、`tests/integration/test_category_identity.py`（→ `test_environment_identity.py`）、`tests/life/test_map_dataset.py`、`test_travel_trajectory.py`、`test_travel_time.py`（→ `test_travel_graph.py`）、`test_storefront_state_validation.py`、`test_storefront_schedule.py`、`test_opening_hours.py`、`test_storefront_arrival.py`、`test_purchase_channels.py`、`test_wc_named_purchase_replay.py`、`test_browse_and_hints.py`、`test_storefront_discovery.py`（→ `test_menu_discovery.py`）、`test_storefront_motivation.py`、`tests/profile/test_coverage_apps.py`、`test_verified_app_names.py`、`test_taste.py`、`tests/life/test_taste_seam.py`、`test_relief_floor.py`、`tests/integration/storefront_fixtures.py`；`tests/life/purchase_worlds.py` 与 `tests/life/test_coverage_closure_models.py` 重写 | [TI]（`test_storefront_release`、`test_storefront_schedule`、`test_tool_interaction_recipe`）；[DF]（`_MAP` ×3、购买渠道 ×12、`_REPLAY`、浏览与提示 ×8、`_RELIEF`、`_PREVIEW`、`_COVERAGE_APPS`）；[TE]（`test_travel_time` 节点改指）；[SR]（`tests/test_environment_profiles.py` 的购买世界节点重写） |
| 4 | `tests/life/test_content_catalog_titles.py`、`test_content_game.py`（邮件节点移到 `test_mail_reading.py`）、`test_content_progress_presentation.py`、`test_news_feed.py`、`test_dayrun_podcast_playback.py`、`tests/corpora/test_dayrun_podcast.py`、`tests/integration/test_unavailable_content.py`、`tests/test_test_tiers.py` 的 TextWorld 用例 | [QT]（`test_content_catalog_titles` 由 `tests/catalog/test_loader.py` 取代）；[TE] `_NEWS_FEED`；[DF] `_INDEX` 内容节点 |
| 5 | `tests/life/test_work_contracts.py`（有序成员表移到 `test_wake_reasons.py`）、`test_work_review.py`、`test_work_review_protocol.py`、`test_work_review_excerpts.py`、`test_work_efficiency_factors.py`、`test_tool_interaction_runtime.py` 的材料与预约节点 | [IF] 同步点 5、T18；[TE] 绿色基线 六节点、`_WORK_REVIEW`；[DF] `_EXCERPTS`；[PP] 三个程序错误行 |

<a name="spec-rrs-删除测试节点"></a>
### 测试节点（文件保留）

| 阶段 | 节点 | 钉住处 |
| --- | --- | --- |
| 1 | `tests/life/test_domain_branch_coverage.py` 的 `route_for`、`validate_routes`、`location_track`、`travel_time_profile`、装配出行时长漂移、配方节点；`tests/life/test_semantics.py` 的车费规则、合同数学、MET 漂移、告警规则节点（重写为 WorldMock 身体常数）；`tests/life/test_scenario.py` 的路线、车费、报价、库存、配方节点；`tests/life/test_load_checks.py` 的地图变体节点（改在人工环境上重写）；`tests/life/test_wayfinding.py` 的配送节点；`tests/life/test_giving.py` 的配送范围与路线拒绝节点；`tests/life/test_purchase_stages.py` 按路线取最近店铺的节点（改按图距离）；`tests/life/test_named_purchase.py` 的路线节点；`tests/integration/test_input_warnings.py` 的地图与手机节点；`tests/profile/test_injection.py` 的口味、游戏、路线节点；`tests/life/test_motivation.py` 的口味与门店节点；`tests/life/test_candidates.py` 的库存、下架、口味节点；`tests/life/test_presentation_fidelity.py` 的零口味排序节点；`tests/integration/test_runtime.py` 的口味节点；`tests/integration/test_host_branch_coverage.py` 的口味注入节点；`tests/life/test_storefront_commerce.py` 的路线与范围节点；`tests/life/test_dayrun_restaurants.py` 的「无目的地或路线」节点 | [DF] 浏览与提示、`_WARNINGS`、`WARNING_CODE_TESTS`、`_STAGES`、口味排序；[PP] `_NAMED`、`_GIVING`；[TE] 能量感知（重写为类别热量） |
| 4 | `tests/life/test_observation_agent_seam.py` 的真实游戏节点；`tests/delivery/test_windows.py` 的评审提示节点；`tests/life/test_activity_effects.py` 的重看曝光节点；`tests/life/test_parallel_admission.py` 的播客、驾车、游戏、制作节点（改写为 app 使用的同种与跨种行）；`tests/life/test_duration_boundaries.py` 的游戏节点；`tests/life/test_shared_rules.py` 的内容时长节点；`tests/life/test_candidates.py` 的驾车只留播客节点；`tests/life/test_paused_slots.py` 的通话与观看占满节点（改用 `use_app`）；`tests/integration/test_life_agent_protocol.py` 的未知游戏工具节点；`tests/integration/test_host_branch_coverage.py` 的游戏类型节点；`tests/integration/test_runtime.py` 的 genre 目录节点；`tests/life/test_tool_interaction_habits.py` 做饭与点名数据集内容的用例 | [SR] 活动时长；[TE] `_ADMISSION`、`_LOCATION`；[PP] |
| 5 | `tests/test_shared_model_clients.py` 的 `WorkReviewer` 参数化；`tests/integration/test_activity_schema.py` 的 `submit_work` 节点；`tests/integration/test_strict_endpoint.py` 的工作部分；`tests/life/test_engine_branch_coverage.py` 的评审与任务节点；`tests/life/test_domain_branch_coverage.py` 的评审、材料、游戏、倍速节点；`tests/life/test_wake_reasons.py` 的任务预警与打烊节点；`tests/life/test_activity_wake_facts.py` 的任务开放节点；`tests/life/test_scheduling.py` 的工作、价格、发布节点；`tests/scheduling/test_branch_coverage.py` 的工作与价格节点；`tests/life/test_progress_events.py` 的截止与提交节点；`tests/life/test_session_attribution.py` 的到期提醒节点；`tests/life/test_tool_layer.py` 的提交与逾期节点；`tests/life/test_activity_effects.py` 的技能与笔记节点；`tests/life/test_paused_slots.py` 的工作截止节点（改为主题上限）；`tests/integration/test_life_agent_protocol.py` 的未完成提交节点；`tests/integration/test_host_branch_coverage.py` 的未分配任务节点；`tests/evaluation/test_errand_metrics.py` 的配方产出节点 | [IF] 第 7 条引擎证据 行里的任务开放节点（改指推送节点）；[PP]；[EW] `_ERRAND_METRICS`、`_WORKLOAD` |

<a name="spec-rrs-删除夹具"></a>
### 夹具

| 阶段 | 夹具 | 处置 |
| --- | --- | --- |
| 1 | `tests/fixtures/environment/catalog/{category-contract.json, exchange-rules.json, objects.jsonl, resources.jsonl, rules/travel-time.toml}`、`corpora/artificial-map/*`、`corpora/content/test.srt` | 删除；目录文件按「人工环境」重写 |
| 1 | `tests/fixtures/dayrun_wc_named_purchase.json` | 随 `test_wc_named_purchase_replay.py` 删除（[DF] `_REPLAY`） |
| 1 | `tests/fixtures/dayrun_fidelity_inputs.json` | 删 `taste_weights`、`contents`、`undeliverable`，保留 `persona_card_struct`、`scenario_rhythm`、`location_names` |
| 4 | `tests/fixtures/tool_interaction_habits.json`、`tests/fixtures/viewer-context.json` | 重写（做饭与数据集内容链删除；`action`、`continuation` 从新格式运行重录，`legacy_*` 不动）（[IF] 测试与夹具 行） |
| 5 | `tests/fixtures/dayrun_work_reviews.json`、`tests/life/fixtures/work_review_deepseek_responses.json` | 删除（[DF] `_EXCERPTS`、[TE]） |
| 7 | `tests/fixtures/dayrun-biz-timeline.json` | 从新格式夹具运行重新生成 |
| 8 | `tests/fixtures/layering-public-api.json`、`layering-acceptance.json`、`current-feature-owners.json`、`acceptance_requirements.json` | 按「测试与夹具」编辑；`mutation-symbol-baseline.json`、`dayrun-layering-baseline.json`、`dayrun-shared-rules-baseline.json`、`dayrun_truncations.json`、`dayrun-contact-replies.json`、`control-request-generations.json`、`archive-assets.json`、`tests/resources/fixtures/anchor-money.json`、`tests/delivery/fixtures/labelkit-action.json` 字节不动 |

<a name="spec-rrs-现行文档原位改写"></a>
## 现行文档原位改写

冲突条款在所属正文原位改写，不留两套同时有效的要求；锚点保留，整节被取代的只删正文并在同一提交删除它的归属行（[DOC]）。每份有版本表的现行文档在改写它的阶段在表内末行加一行，行标识 `2026-10-06-rrs-<阶段>`（B-8），写被改写条款的当前语义；设计章节（05 除外）在本特性第一次改写它的阶段把文件头「版本」末位加一并改写日期，后续阶段只改日期；记录引擎设计（05）照更新约定用特性标识行、版本号不变；每份被改写的设计章节、规格、接口合同、实验设置与验收标准在文件头只保留一行本特性说明「按资源与规则引擎简化规格修订「<节名>」（阶段 N）」，规格名链接到本文件标题锚点 `spec-rrs-资源与规则引擎简化开发规格`，后续阶段在同一行补；手册沿用带日期的文件头修订说明。设计章节或接口合同改动的每个提交运行 `python scripts/build_design_doc.py` 并提交重新汇编的 `docs/design/worldmock-design.html`。其他文档提到本特性时写「资源与规则引擎简化规格」并链接标题锚点。归档目录、调研目录、已裁决与未冻结的提案、既有验收记录、冻结的其他特性规格的裁决原文不改（同时做两件事规格的 R5、R6 与让「等待」成为例外规格的 R12 由本规格的裁决记录取代，不回改）。SPEC-category-contract 一族按「阶段 1」的归档行搬到 `docs/archive/category-contract/`。

<a name="spec-rrs-文档阶段0"></a>
### 阶段 0（冻结）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/INDEX.md#index-特性规格与提案` | 登记本规格（`FROZEN`）；提案一行状态改 `已裁决`，职责列末尾改为「已收敛为冻结规格资源与规则引擎简化规格（链接本文件标题锚点），本文只保留原话、业界方案与取舍依据」；生活运行时、人物规格、一日行为、交互菜单、数字人交互、AS2 集成、决策上下文、当日目标、LabelKit 交付、地图与门店供给、查看器对象名称、查看器上下文、让「等待」成为例外、同时做两件事、环境数据归属各行注明被本规格修订的部分与阶段，写「随阶段 N 提交实现」；品类契约规格与它的四份记录仍登记在原路径（阶段 1 搬移后改为 `归档`） |
| `docs/INDEX.md#index-按任务读`、`docs/INDEX.md#index-权威层级`、`docs/INDEX.md#index-场景与配置`、`docs/INDEX.md#index-版本表` | 「按任务读」加一行指向本规格、验收台账与机器登记；「权威层级」补「分类、常数与交换公式归 `worldmock.exchange`（`RULES_VERSION`），环境只提供对象与绑定」；「场景与配置」的数据仓目录说明改为重制后的环境路径由数据仓发布决定；版本表加行 |
| `docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-规格测试用例覆盖率追溯矩阵` | 新增「资源与规则引擎简化当前映射」一节，写机器登记 `tests/rrs_acceptance.py` 与夹具键 `resource-rule-simplification`，冻结时各行只有阶段与锚点；版本表加行 |
| `docs/dev/SPEC-layering-and-test-tiers.md#layering-mutations` | 「全部一百四十七项登记直接写现行定义」改为「基线 JSON 字节冻结；守卫核对 登记 ∩ 基线」；同一提交 `tests/test_layering_acceptance.py` 去掉 147 字面量（D-47） |
| `docs/dev/SPEC-layering-and-test-tiers.md#layering-archive`、`docs/dev/SPEC-layering-and-test-tiers.md#layering-acceptance` | 「正文与归档」一行：归档断言只对已归档特性成立；第六个特性的规格是现行文件 |

<a name="spec-rrs-文档阶段1"></a>
### 阶段 1（固定规则与环境格式）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md#contracts-环境数据归属合同` | 分类、常数与交换规则归 WorldMock（`worldmock.exchange`、`RULES_VERSION`）；环境提供 app、地点、食品、内容、工作主题、发布图与绑定；引擎测试用重写的人工环境；完整回归以 `--environment-root` 指向重制后的环境 |
| `docs/CONTRACTS.md` 的 `contracts-品类契约与目录字段` | 正文删除，取代节 `contracts-环境目录文件`（七个目录文件、清单身份、登记码与英文原因，与本规格「环境格式」同文） |
| `docs/CONTRACTS.md` 的 `contracts-类别供给的构建合同` | 正文删除，取代节 `contracts-环境目录与绑定`（同类同菜单由 `foods[].store_ids` 或 `"all"` 派生、价格在食品上、`FOOD_BINDING_KIND_INCONSISTENT`） |
| `docs/CONTRACTS.md` 的 `contracts-营业与报价声明` | 正文删除（无报价、无日历；店铺全天服务） |
| `docs/CONTRACTS.md#contracts-地点类词表` | `LocationKind` 八个内置类型；活动环境由 `PLACE_KIND_ENVIRONMENTS` 派生；生活活动条目保留自己的 `location_kinds` |
| `docs/CONTRACTS.md#contracts-cli` | 删 `category` 子命令；加 `environment generate-map` 与 `validate --environment` |
| `docs/CONTRACTS.md#contracts-环境与检查命令合同` | 环境校验命令与登记码表指向「环境目录文件」 |
| `docs/CONTRACTS.md#contracts-版本表` | 加行：目录清单 `format_version: 1`、地图 1、`RULES_VERSION = 1`（场景 7、世界 10、清单 3、导出 3、质量 5、交付 3 的行在各自阶段加） |
| `SPEC-category-contract.md`、`PROPOSAL-category-contract.md`、`category-contract-verification.md`、`category-contract-main-verification.md`、`category-contract-files.md`、`category-contract-bob-review.md`（`docs/dev/` 下） | 整体搬到 `docs/archive/category-contract/`；规格文件头改为 `> 代码基线：7e1ffab3b49971922069c0c1fa400ffcbb075beb`、`> 归档基线：<搬移前一个提交的完整哈希>`、`> 原始路径：docs/dev/SPEC-category-contract.md`、「此提交仅定位归档原文快照，不是审查执行证明」；正文与归档基线处的原文逐字节相同；四份记录保留已有的归档头；`docs/INDEX.md` 各行改 `归档`；41 份引用它的文档把链接改指归档路径；`tests/fixtures/archive-assets.json` 登记新归档资产；`docs/VERIFICATION.md` 的 `verification-品类契约特性门禁` 正文删除 |
| `docs/dev/SPEC-environment-data-ownership.md` 「职责与接口」各行（通用规则归属、环境系数与对象、标题来自环境、数据准备归属、测试输入归属、双仓端到端）、「发布与约束」、「实施结果与验收入口」 | 规则归引擎；标题来自 `contents.json`；数据仓准备七个目录文件；人工环境重写；完整门禁路径为重制后的环境 |
| `docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-活动规则`、`docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-共享规则` | 行 品类与目录错误 → 环境登记错误 `RegistrationError`；合同对象集合 → 环境登记身份；出行时长 依据改图；安全文件、公共枚举 行改指新归属；[SR] 读的表同一提交改 |
| `docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-防漂移守卫`、`docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-新机器检查` | `OWNERS` 行与 `QUICK_TESTS` 内容按「测试与夹具」 |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-来源与准入` | 来源：真实 app、作品与主题名，真实或合成摘要，合成数值；语料清单哈希只对生活活动与联系人、邮件 |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-身体参数与单位`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-场景与身体规则`、`docs/dev/SPEC-life-runtime.md#time-and-energy-能量感知-饥饿在低能量时不失灵` | 身体常数来自 `worldmock.exchange.body`（`source_kind = worldmock:<RULES_VERSION>`）；800/400 阈值是 WorldMock 常数 |
| `docs/dev/SPEC-life-realism.md#spec-life-realism-世界素材品类要求` | 地点 行：八个类型与发布图；正餐 行：三类固定热量；数据集声明的 `category_requirements` 删除（内容、工作 行在阶段 4、5 改） |
| `docs/ARCHITECTURE.md#architecture-环境数据与通用规则的归属`、`docs/ARCHITECTURE.md#architecture-核心决策`、`docs/ARCHITECTURE.md#architecture-离线语料如何进入环境` | 规则归引擎；核心决策表的类型池、报价、预约与结构版本行改写；语料进入环境改为目录登记 |
| `docs/ARCHITECTURE.md` 的 `architecture-品类契约的独立归属` | 正文删除（品类契约已归档）；「闭集规则与品类配方」图节点改为「固定类型与常数」 |
| `docs/EXPERIMENT.md#experiment-环境发布与运行身份`、`docs/EXPERIMENT.md#experiment-经济参数`、`docs/EXPERIMENT.md#experiment-能量感知标签`、`docs/EXPERIMENT.md#experiment-身体初始值`、`docs/EXPERIMENT.md#experiment-效率曲线`、`docs/EXPERIMENT.md#experiment-需求状态`、`docs/EXPERIMENT.md#experiment-实验假设声明` | 运行身份 = `environment_identity`；经济参数 = 车费与配送费常数（无工作包、计时计件）；能量标签 = 类别热量；身体常数来自 WorldMock；实验假设声明新增行：食品常数、速度、配送、地图生成器、推送窗口、app 使用时长、按类型缓解（后三行在阶段 4 写值） |
| `docs/VERIFICATION.md#verification-工程门禁` | 素材可达性 行改为「内容可达 ⇔ 绑定 ≥ 1 个 app」；品类契约门禁一节正文删除 |
| `docs/design/02-exchange-engine.md` 的 `exchange-engine-维护交换规则`、`exchange-engine-注册表登记什么`、`exchange-engine-新增状态资源对象或语料`、`exchange-engine-修改与删除`、`exchange-engine-查询适用规则`、`exchange-engine-交换规则的加载与准入` | 正文删除，由新节 `exchange-engine-系统固定类型与常数` 取代（闭集、常数表、算子） |
| `docs/design/02-exchange-engine.md#exchange-engine-交换对象与资源变化声明`、`docs/design/02-exchange-engine.md#exchange-engine-可供性与活动声明`、`docs/design/02-exchange-engine.md#exchange-engine-校验交换条件`、`docs/design/02-exchange-engine.md#exchange-engine-门店供给的时态与履约判断`、`docs/design/02-exchange-engine.md#exchange-engine-资源变化的计算`、`docs/design/02-exchange-engine.md#exchange-engine-不同交换的计算依据`、`docs/design/02-exchange-engine.md#exchange-engine-校验资源分录`、`docs/design/02-exchange-engine.md#exchange-engine-食品交换的规则调用`、`docs/design/02-exchange-engine.md#exchange-engine-实现职责与证据入口`、`docs/design/02-exchange-engine.md#exchange-engine-当前差距`、`docs/design/02-exchange-engine.md#exchange-engine-分阶段验证与完成条件`、`docs/design/02-exchange-engine.md#design-exchange-engine-current-responsibility` | 条件判断与结算只读 `exchange` 常数；没有店铺时态；文件头版本加一、版本历史加行 |
| `docs/design/03-resource-engine.md#resource-engine-可计量资源定义`、`docs/design/03-resource-engine.md#resource-engine-交换对象与内容条目`、`docs/design/03-resource-engine.md#resource-engine-资源内容和语料的关系`、`docs/design/03-resource-engine.md#resource-engine-文件职责`、`docs/design/03-resource-engine.md#resource-engine-构建与版本`、`docs/design/03-resource-engine.md#resource-engine-文件身份校验`、`docs/design/03-resource-engine.md#resource-engine-准入边界`、`docs/design/03-resource-engine.md#resource-engine-语料清单与文件身份`、`docs/design/03-resource-engine.md#design-resource-engine-current-responsibility` | 资源引擎 = 登记、校验、只读查询；七个目录文件与派生索引；资源定义归交换引擎 |
| `docs/design/03-resource-engine.md` 的 `resource-engine-组织与发布目录`、`resource-engine-目录发布的实现与验收` | 正文缩为目录清单身份与原子发布 |
| `docs/design/08-configuration.md#configuration-规则来源与配置边界`、`docs/design/08-configuration.md#configuration-资源目录引用`、`docs/design/08-configuration.md#configuration-规则配置入口`、`docs/design/08-configuration.md#configuration-校验清单`、`docs/design/08-configuration.md#configuration-校验报告`、`docs/design/08-configuration.md#configuration-错误分类与提示`、`docs/design/08-configuration.md#configuration-配置版本与运行绑定`、`docs/design/08-configuration.md#configuration-规则维护` | 环境只登记对象与绑定；登记码表；`validate --environment`；运行绑定 `environment_identity` 与 `RULES_VERSION`；规则维护 = 改 WorldMock 常数并升 `RULES_VERSION`；版本表四列、作者列写 `Claude` |
| `docs/manual/rules.md#rules-配置入口与生效范围`、`docs/manual/rules.md#rules-身体规则`、`docs/manual/rules.md#rules-在独立目录中调参并验证` | 可改的只有动机、人物映射与场景参数；身体与交换常数在包内；验证用 `validate --environment` |
| `docs/manual/rules.md` 的 `rules-品类契约`、`rules-交换规则`、`rules-供给与制作边界`、`rules-检查与发布品类修改`、`rules-出行时长`、`rules-食品来源映射说明` | 正文删除 |
| `docs/manual/resources.md#resources-定义地点门店供给与世界素材`、`docs/manual/resources.md#resources-先找到应该修改的文件`、`docs/manual/resources.md#resources-哪些路径可以换`、`docs/manual/resources.md#resources-输入与生成文件`、`docs/manual/resources.md#resources-修改后更新文件身份`、`docs/manual/resources.md#resources-验证修改` | 新写「编写店铺类型、食品、app 绑定与生成地图」；文件清单为七个目录文件；身份刷新与验证命令 |
| `docs/manual/resources.md` 的 `resources-先完成一个最小修改减少一家门店的初始库存`、`resources-编写门店供给与商品`、`resources-完整类别供给与冻结价格`、`resources-当前供给字段与发布注意事项`、`resources-新增商品对象`、`resources-真实制作后再食用` | 正文删除 |
| `docs/manual/scenarios.md#scenarios-校验自己的场景` | 校验命令与登记码 |
| `README.md`、`docs/manual/README.md#readme-文件放在哪里`、`docs/manual/runtime.md#runtime-准备环境`、`docs/manual/runtime.md#runtime-使用已安装的环境` | 命令行与环境目录说明改为新格式与新命令 |
| `tests/fixtures/environment/README.md` | 按「人工环境」重写：七个地点、四个 app、四种食品、六条内容、一个主题、种子 7 的图；三份包 TOML 副本的说明保留 |

<a name="spec-rrs-文档阶段2"></a>
### 阶段 2（地图与出行；场景 7、世界 10）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md#contracts-冻结地图与场景引用`、`docs/CONTRACTS.md#contracts-地图与门店供给合同` | `map.json`（结构 1、种子、排序节点、规范边与整数米）取代 `MapDatasetReference`、WGS84 与路线；场景只选 `locations[]`；版本边界 场景 7 / 世界 10 / 目录清单 1 / 规则 1 |
| `docs/CONTRACTS.md#contracts-持久化当下事实`、`docs/CONTRACTS.md#contracts-lifeenv-状态字段`、`docs/CONTRACTS.md#contracts-资源与需求字段` | 世界 10 事实：`deliveries`、`contact_holdings`、`inventory`、`presentation_state`、`sessions[].app_pushes`、`content_progress`；没有 `supply_*`、`opening_exceptions`、`visible_supply_*`（推送与内容进度字段在阶段 4 生效，合同一次写全并注明阶段） |
| `docs/CONTRACTS.md#contracts-领域枚举` | 世界 9→10 对照表；`ContractKind` 删除（`ActivityKind`、`ScheduledKind`、`boundary_type` 在阶段 4 改） |
| `docs/CONTRACTS.md#contracts-model-identity` | 运行清单 3 与 `environment_identity`（阶段键 `{action}` 在阶段 5 改） |
| `docs/CONTRACTS.md#contracts-private-fields` | 禁键表按「状态、事件、检查点与导出」；路线坐标与几何一句删除 |
| `docs/CONTRACTS.md#contracts-共同检查点与恢复`、`docs/CONTRACTS.md#contracts-持久化结构` | 检查点封存订单、持有、内容进度、推送；恢复核对 `environment_identity` |
| `docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-产品行为与边界`、`docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-冻结输入与身份`、`docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-记录恢复与交付` | 规格就地改写为图与菜单规格：随机连通加权图由 WorldMock 生成、数据仓发布；身份 = 目录清单身份；地点字段五个；检查点与清单带 `environment_identity`；文件头与版本表随改（标题锚点保留） |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-导航与配送`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-地图与门店供给行为`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-共享时钟` | 出行 = 图最短路 ÷ 方式速度；配送 = 备餐 + 距离 ÷ 配送速度；无 GPS、无交通数据；边界来源删打烊变化、加图上送达 |
| `docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性`、`docs/dev/SPEC-interaction-menu.md#spec-interaction-menu-地图地点与发现工具` | 提示只写 `Go to <place>.`；没有配送区域原因；地点发现从图取 |
| `docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-对象与动作选择`、`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-前置条件前置与前往提醒`、`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-会话的状态位置` | 前往 行按图；没有技能与合同行；世界结构 10 的历史句 |
| `docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-场景加载时就该拒绝的问题` | 场景加载拒绝的问题按「注册校验与错误」 |
| `docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-身份与基线`、`docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-身份与基线` | 世界 10、质量 5、场景 7；允许新格式的第三份基线；加一行：本规格移动 `tool_schema_sha256`、`prompt_sha256` 与 `code_sha256`，重设计前的检查点被结构 10 与占用核对拒绝 |
| `docs/dev/SPEC-layering-and-test-tiers.md#layering-cache`、`docs/dev/SPEC-layering-and-test-tiers.md#layering-engine`、`docs/dev/SPEC-layering-and-test-tiers.md#layering-baseline` | 缓存键覆盖目录文件、生活活动语料与场景；`EngineContext` 字段集不变；结构 7 完整日与第三份基线，两份历史 JSON 保留字节 |
| `docs/design/01-architecture.md#architecture-地图与门店供给的交接`、`docs/design/01-architecture.md#architecture-准备与初始化`、`docs/design/01-architecture.md#architecture-配置与运行入口`、`docs/design/01-architecture.md#architecture-状态与记录`、`docs/design/01-architecture.md#architecture-现有代码与职责的对应关系`、`docs/design/01-architecture.md#architecture-系统定位`、`docs/design/01-architecture.md#architecture-观察行动与执行`、`docs/design/01-architecture.md#architecture-已采用的设计决策`、`docs/design/01-architecture.md#design-architecture-current-responsibility` | 初始化 = 登记 + 图；分阶段身份一段写本规格各阶段的指纹变化（B-19）；模块对应表按新包 |
| `docs/design/03-resource-engine.md#resource-engine-地图资料的冻结与许可`、`docs/design/03-resource-engine.md#resource-engine-冻结地图的准备与发布`、`docs/design/03-resource-engine.md#resource-engine-地图关系与道路范围准入` | 随机图的生成、发布与校验；没有许可与道路范围 |
| `docs/design/04-simulation-engine.md#simulation-engine-输入与启动流程`、`docs/design/04-simulation-engine.md#simulation-engine-地图与供给初始化`、`docs/design/04-simulation-engine.md#simulation-engine-恢复与反事实运行编排`、`docs/design/04-simulation-engine.md#simulation-engine-当前实现入口`、`docs/design/04-simulation-engine.md#simulation-engine-失败处理` | 启动 = 登记目录、读图、场景 7；恢复核对 `environment_identity` |
| `docs/design/05-recording-engine.md#recording-engine-地图与门店供给证据`、`docs/design/05-recording-engine.md#recording-engine-地图与供给的恢复身份`、`docs/design/05-recording-engine.md#recording-engine-检查点创建`、`docs/design/05-recording-engine.md#recording-engine-持久化结构`、`docs/design/05-recording-engine.md#recording-engine-必须保存的内容` | 出行证据 `trip_id`、`path`、`distance_meters`；恢复身份 = `environment_identity`；世界 10 的保存内容 |
| `docs/design/06-delivery.md#delivery-manifestjson-结构`、`docs/design/06-delivery.md#delivery-接收记录引擎导出`、`docs/design/06-delivery.md#design-06-delivery-共享规则与可重建环境修订`、`docs/design/06-delivery.md#design-delivery-current-responsibility` | 清单复制 `environment_identity`；分阶段身份补本规格 |
| `docs/design/08-configuration.md#configuration-配置层级`、`docs/design/08-configuration.md#configuration-加载与校验`、`docs/design/08-configuration.md#configuration-参数归属`、`docs/design/08-configuration.md#configuration-文件位置与合同`、`docs/design/08-configuration.md#configuration-地图引用与门店供给初值`、`docs/design/08-configuration.md#configuration-语料引用`、`docs/design/08-configuration.md#configuration-交互菜单声明`、`docs/design/08-configuration.md#configuration-身体资源初值` | 场景 7 字段表；`environment` 钉；菜单键为八个类型；没有地图引用与供给初值 |
| `docs/design/09-io-formats.md#io-formats-worldstate世界状态`、`docs/design/09-io-formats.md#io-formats-agentstate人物状态`、`docs/design/09-io-formats.md#io-formats-activitystate活动状态`、`docs/design/09-io-formats.md#io-formats-当前工具请求查询与错误格式` | 按「状态、事件、检查点与导出」的字段表 |
| `docs/manual/scenarios.md#scenarios-看懂场景的组成`、`docs/manual/scenarios.md#scenarios-修改日期与模拟节奏`、`docs/manual/scenarios.md#scenarios-限制人物可以做什么`、`docs/manual/scenarios.md#scenarios-配置日历和预排事件` | 结构 7 的字段与例子；菜单键与动作；没有价格与发布事件 |
| `docs/manual/resources.md#resources-编写地点与营业时间`、`docs/manual/resources.md#resources-来源与有效期` | 改为「编写地点」（五个字段、八个类型）与「名称真实、数值合成」 |
| `docs/manual/resources.md` 的 `resources-添加地点时还要补路线` | 正文删除 |
| `docs/manual/runtime.md#runtime-从校验到新建运行`、`docs/manual/runtime.md#runtime-从检查点恢复`、`docs/manual/runtime.md#runtime-常见错误` | 新命令、`environment_identity` 不符的错误 |
| `docs/EXPERIMENT.md#experiment-地图与门店供给的实验身份` | 图种子、生成规则版本、按类型菜单 |
| `docs/VERIFICATION.md#verification-地图与门店供给` | 改写为门禁表：图连通、同类同菜单、`all` 不含住宅、全地图外卖、app 绑定（标题改「随机地图与共享菜单」，锚点保留） |
| `docs/dev/SPEC-run-viewer-names.md#spec-run-viewer-names-地点名称`、`docs/dev/SPEC-run-viewer-names.md#spec-run-viewer-names-运行内对象名称`、`docs/dev/SPEC-run-viewer-context.md#spec-run-viewer-context-结构与原文` | 出行显示起点、终点与路径名称；名称索引读 `foods`、`apps` |

<a name="spec-rrs-文档阶段3"></a>
### 阶段 3（店铺、食品与外卖；画像）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md#contracts-地点发现与购买身份` | 菜单行字段；`from` = 店铺显示名或 `apps.json` 的 app 名；没有配对状态与系数；购买渠道段改为 app 绑定 |
| `docs/CONTRACTS.md#contracts-供给结算与事件载荷` | 订单行按 `DeliveryState`；一单一个 `delivery_arrival`；没有价格、报价、发布事件 |
| `docs/CONTRACTS.md#contracts-活动受理拒绝码` | 删 `DELIVERY_*`、`MERCHANT_*`、`SUPPLY_NOT_VISIBLE`、`LOCATION_ACCESS_DENIED` 等（表见「删除清单」）；`ORDER_DUPLICATE_ITEM`；加 `APP_NOT_BOUND`；`ACTIVITY_PRECONDITION_UNMET` 下一步 `Go to <place>.`，`unmet` 闭集按 B-14；路途中自送拒绝 |
| `docs/CONTRACTS.md#contracts-request-settlement`、`docs/CONTRACTS.md#contracts-closed-request-models` | 指名购买 M3 = 店铺有（所点名的）app 或人在场；`from` 表：null、地点名、app 名；方式表删 制作、浏览、观看类、游玩（打开 在阶段 4 加）；来源与去向：路途中自送拒绝 |
| `docs/CONTRACTS.md#contracts-候选与呈现合同`、`docs/CONTRACTS.md#contracts-候选对象与来源`、`docs/CONTRACTS.md#contracts-排除原因码`、`docs/CONTRACTS.md#contracts-排序键与游标`、`docs/CONTRACTS.md#contracts-下推返回体`、`docs/CONTRACTS.md#contracts-场景常量`、`docs/CONTRACTS.md#contracts-派生量字段` | 候选种类 `product`、`lifestyle`、`task`；商品 `object_id = "<store_id>:<food_id>"`；没有库存、下架、口味键与 `retrieval_index_threshold`；app 始终列出 |
| `docs/CONTRACTS.md#contracts-可见期与提示预算` | 没有材料上限与供给可见期（`app_push` 记录折叠在阶段 4 补） |
| `docs/CONTRACTS.md#contracts-运行输入警告`、`docs/CONTRACTS.md#contracts-人物卡片覆盖率产出`、`docs/CONTRACTS.md#contracts-体感与动机证据`、`docs/CONTRACTS.md#contracts-人格与卡片字段` | 六个码；覆盖率目标 = 习惯关键词、食品名、五个类型标签 + app 名；没有 `taste_weights`；`tastes` 只是散文 |
| `docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-按地点类别生成完整供给`、`docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-冻结价格范围与门店差价`、`docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-地点发现与候选呈现`、`docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-整单购买与履约` | 菜单 = 类型的绑定食品；一个 `price_fen`；商品目标 = 当前店铺菜单 + 可外卖食品；订单行 `(store_id, food_id)`、商家收款、外卖只需绑定与付款、ETA 与费用固定 |
| `docs/dev/SPEC-map-and-storefront.md` 的 `spec-map-and-storefront-营业报价与访问条件` | 正文删除（全天服务、无资格） |
| `docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买`、`docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析`、`docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法`、`docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店`、`docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-名称类拒绝不计同刻轮数`、`docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-更简单的购买请求裁决-s10` | 机制不变；`from` 可写 app 名；app = 目录条目；`app_id` 进订单与 `DeliveryState`；没有手机配置（裁决 S7 被取代）；`prefer` 以图 ETA 与距离为据，并列按到手价再按 `store_id` |
| `docs/dev/SPEC-map-and-storefront.md#tool-interaction-地点供给和价格`、`docs/dev/SPEC-map-and-storefront.md#tool-interaction-地点供给和价格-类别准入与逐店商品集合`、`docs/dev/SPEC-map-and-storefront.md#tool-interaction-地点供给和价格-范围内定价` | 同类型店铺菜单完全相同；价格定义在食品上（[DOC] 行 类别供给、地点定价 删除，其余行文改写） |
| `docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-验收台账`、`docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-文件修改范围`、`docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-版本表` | 行与文件按新实现；版本表加行 |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-随身持有与远程订购`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-转换效率算法`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-具体活动结算`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-资金与合同` | 外卖经 app 绑定店铺送到任何已选地点、自送只能在停留中；能量 = 类别热量 × 份额 × 利用率（缓解在阶段 4 改）；购物、进食、出行 行；资金只有工资与购买 |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-候选契约与呈现规则`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-统一候选契约生成资格排序三段`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-两级上限`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-下推两层游标返回体`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-曝光轮转`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-禁止永久退役`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-地点语义与检索分区` | 商品、生活活动、任务保留管线；没有库存、口味；分区删除（内容离开管线在阶段 4） |
| `docs/dev/SPEC-life-runtime.md#persona-prompt-交互语义-来源与去向`、`docs/dev/SPEC-life-runtime.md#persona-prompt-交互语义-指名购买`、`docs/dev/SPEC-life-runtime.md#persona-prompt-交互语义-进食` | app 名来自目录；M3 按绑定；路途中拒绝；进食按类别时长 |
| `docs/dev/SPEC-life-runtime.md#time-and-energy-能量感知-商品热量与路线费力`、`docs/dev/SPEC-life-runtime.md#time-and-energy-五类生活动作与并行准入-请求形状与菜单`、`docs/dev/SPEC-life-runtime.md#time-and-energy-五类生活动作与并行准入-并行准入`、`docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-完成依据与结算`、`docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告` | 商品状态 = 类别 + 固定热量，可达性恒成立；方式表；远程下单的路途规则；「取得」不含配方产出；六个码、无 `_card_taste_weights` |
| `docs/dev/SPEC-life-runtime.md` 的 `tool-interaction-料理与习惯` | 正文删除（[DOC] 行 料理、真实料理 删除） |
| `docs/dev/SPEC-life-realism.md#spec-life-realism-画像编译`、`docs/dev/SPEC-life-realism.md#spec-life-realism-地图门店供给与活动环境`、`docs/dev/SPEC-life-realism.md#spec-life-realism-商务与进食`、`docs/dev/SPEC-life-realism.md#spec-life-realism-动机层`、`docs/dev/SPEC-life-realism.md#spec-life-realism-候选确定性`、`docs/dev/SPEC-life-realism.md#spec-life-realism-呈现上限与下推游标`、`docs/dev/SPEC-life-realism.md#spec-life-realism-呈现轮转`、`docs/dev/SPEC-life-realism.md#spec-life-realism-素材淘汰与检索机制的禁止项`、`docs/dev/SPEC-life-realism.md#spec-life-realism-验收矩阵` | 没有口味权重，覆盖率按目录名；共享菜单；合并规则 = 同店同履约；进食候选按类别；三种候选；禁止项保留；矩阵行 口味权重是排序信号 删除、人物卡片覆盖率 改写 |
| `docs/dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-餐馆的实际进食路径`、`docs/dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-唯一状态与资源准入` | 餐馆卖类型菜单；没有配送路线状态；自送只在停留中（占用名在阶段 4 改） |
| `docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-来源解析` | 来源 = 目录里的店铺或 app（`world_query_apps`） |
| `docs/design/03-resource-engine.md#resource-engine-地点商品与门店供给`、`docs/design/03-resource-engine.md#resource-engine-地点发现与供给比较`、`docs/design/03-resource-engine.md#resource-engine-检索呈现的实现与验收`、`docs/design/03-resource-engine.md#resource-engine-无结果与范围限制` | 菜单由绑定派生；发现与比较按图距离与到手价 |
| `docs/design/04-simulation-engine.md#simulation-engine-发现与比较门店供给`、`docs/design/04-simulation-engine.md#simulation-engine-预排事件` | 同上；预排事件删三种供给类（`app_push` 在阶段 4 加） |
| `docs/design/04-simulation-engine.md` 的 `simulation-engine-门店营业与报价事件` | 正文删除 |
| `docs/design/07-evaluation.md#evaluation-门店选择与供给假设的比较边界` | 比较边界按共享菜单与固定配送 |
| `docs/design/08-configuration.md#configuration-人物差异与衍生体感` | 没有缓解下限 |
| `docs/manual/resources.md#resources-正餐家庭储备和联系人`、`docs/manual/personas.md#personas-习惯的行为核验目标`、`docs/manual/personas.md#personas-写出一个能够生活的人`、`docs/manual/personas.md#personas-正文与实际世界条件要一致`、`docs/manual/personas.md#personas-预览与校验`、`docs/manual/personas.md#personas-常见错误` | 储备只写食品份数；做饭一句删除；画像对照的是地点、食品、app 与内容名；覆盖率按目录名 |
| `docs/EXPERIMENT.md#experiment-内容与语料绑定` | 食品与地图行：真实名称、合成价格与边长（内容行在阶段 4） |
| `docs/VERIFICATION.md#verification-工具交互特性门禁`、`docs/VERIFICATION.md#verification-一日运行保真特性门禁`、`docs/VERIFICATION.md#verification-测试矩阵` | 行 地点供给、供给价格、料理 改写或删除；矩阵行 资源结算、指名购买、世界查询、观察投影、地图与门店供给 改写 |

<a name="spec-rrs-文档阶段4"></a>
### 阶段 4（娱乐与推送）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md#contracts-语料内容目录字段`、`docs/CONTRACTS.md#contracts-环境标题与活动效果` | 内容行字段（摘要、来源、时长、`app_ids`、`series`）；标题来自 `contents.json`；没有分段、资产、属性、评估引用 |
| `docs/CONTRACTS.md#contracts-领域枚举`、`docs/CONTRACTS.md#contracts-活动控制行`、`docs/CONTRACTS.md#contracts-状态归属` | `ActivityKind` 十个、`ActivityChange` 三个、`boundary_type` 无 `game_feedback`、`ScheduledKind` 七个；活动标题：app 使用取 app 名（+ 当前内容标题）；`occupied_resources` = 八个名字；状态归属 人物活动（八个名）、内容接触 `{content_progress, played}`、地点会话加 `app_pushes` |
| `docs/CONTRACTS.md#contracts-活动受理拒绝码`、`docs/CONTRACTS.md#contracts-request-settlement`、`docs/CONTRACTS.md#contracts-closed-request-models`、`docs/CONTRACTS.md#contracts-action-results` | 加 `APP_CONTENT_EMPTY`；`use_app` 行（抽内容、推送优先）；方式 打开；`details.app_content`；没有 `browse_items` |
| `docs/CONTRACTS.md#contracts-可见期与提示预算`、`docs/CONTRACTS.md#contracts-记忆字段`、`docs/CONTRACTS.md#contracts-运行诊断输出`、`docs/CONTRACTS.md#contracts-labelkit-输入结构与窗口编码`、`docs/CONTRACTS.md#contracts-交付格式` | `app_push` 记录不受保护、折叠；app 使用的 `settled` 句；`OPS_COMPONENTS` 加 `push`；`app_push` 公开投影 |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-决策节律结算边界与唤醒条件分离`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-决策机会规则`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-共享时钟`、`docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动-规则`、`docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动-不变项` | 词表 22 个（删四加一，「成员只经版本表与九个同步点增删」）；触发表加 `app_push` 行；边界来源加推送整点与内容完成（`next_interaction_at`），删打烊变化与工作截止；规则 6 补 `app_push` |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-内容目录契约`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-内容提供行为`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-检索确定性索引与-facet`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-观察投影行为规则` | 内容 = 摘要 + 时长 + 绑定，`summary_source` 标记合成；内容提供只剩邮件半部；没有检索索引与 facet；投影没有字幕与材料揭示，有 app 目标 |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-转换效率算法`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-具体活动结算`、`docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径` | 缓解 = 按内容类型固定速率；看节目、游戏 行 → app 使用按经过时间；没有下限与 `w` |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-活动组合政策`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-并发与占用`、`docs/dev/SPEC-life-runtime.md#time-and-energy-五类生活动作与并行准入-人物选择什么`、`docs/dev/SPEC-life-runtime.md#time-and-energy-五类生活动作与并行准入-派生时长上限`、`docs/dev/SPEC-life-runtime.md#time-and-energy-五类生活动作与并行准入-动作照常列出做错了再说明`、`docs/dev/SPEC-life-runtime.md#time-and-energy-会话活动与记忆提示中不出现内部身份-三层结构`、`docs/dev/SPEC-life-runtime.md#time-and-energy-会话活动与记忆提示中不出现内部身份-会话与活动的字段` | 八个占用名与组合表；app 使用时长 = `APP_USE_DEFAULT_SECONDS`；session 带 `app_pushes`；没有播客例子 |
| `docs/dev/SPEC-life-realism.md#spec-life-realism-世界素材品类要求`、`docs/dev/SPEC-life-realism.md#spec-life-realism-素材可达性`、`docs/dev/SPEC-life-realism.md#spec-life-realism-动机层` | 内容 行：五类、摘要、≥ 1 个 app；可达 ⇔ 绑定 ≥ 1 个 app，无分区；娱乐候选是 app |
| `docs/dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-等待与内容接触`、`docs/dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-人物入口与独立控制`、`docs/dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-持续活动与必要唤醒`、`docs/dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-唯一状态与资源准入` | 环境在人物所选 app 内选内容，等待仍不产出；没有游戏对象；八个占用名 |
| `docs/dev/SPEC-dayrun-behavior.md` 的 `spec-dayrun-behavior-真实音频来源与揭示` | 正文删除 |
| `docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-对象与动作选择`、`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-决策轮循环与播报节律`、`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-冻结条款修订`、`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-游戏简化` | 打开 行（app、固定时长）；触发表 `app_push` 行；第 1 行写明同步点已为 `app_push` 与四个删除执行；游戏简化一节注明被本规格取代（锚点保留） |
| `docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-占用声明` | 十一个名 → 八个；观看与收听、玩游戏、浏览 行 → 用 app；驾车一行加 `use_app`（无播客例外）；文件头与版本表注明被本规格取代的乙表与丙文 |
| `docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界规则`、`docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界机制`、`docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-测试与夹具`、`docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-用户得到什么` | 「21 → 25」改「25 → 22 并带删除规则」；任务输入删除处的登记原文重新登记；`viewer-context.json` 重录；「环境不替人物选择内容」改「环境在人物所选 app 内选内容」，刷手机填充由数据集撤回 |
| `docs/dev/SPEC-day-plan-goals.md#spec-day-plan-goals-度量输出合同` | 家族映射 `use_app → entertainment` |
| `docs/dev/SPEC-labelkit-delivery.md#spec-labelkit-delivery-证据窗口与输入结构` | `app_push` 是唯一有公开投影的环境事件 |
| `docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-活动规则`、`docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-门禁可选部件与操作系统` | 行 活动时长（生活活动 + app 常数）、缓解速率（固定）、候选种类（三种）；TextWorld 条款删除，Windows 可运行各层 |
| `docs/design/00-overview.md#overview-需求映射表`、`docs/design/00-overview.md#overview-核心术语`、`docs/design/00-overview.md#overview-算法与工程背书总表`、`docs/design/00-overview.md#overview-设计决策`、`docs/design/00-overview.md#overview-设计假设若不成立需回到设计层`、`docs/design/00-overview.md#overview-开放问题后续版本议题`、`docs/design/00-overview.md#overview-参考文献` | 内容呈现 行与参考文献第 7 项改写；术语加 app、推送、工作主题（工作验收 行在阶段 5） |
| `docs/design/03-resource-engine.md` 的 `resource-engine-提供实际接触的内容`、`resource-engine-按指定范围提供内容`、`resource-engine-返回内容与来源`、`resource-engine-内容读取失败`、`resource-engine-内容提供的实现与验收`、`resource-engine-从目录到候选`、`resource-engine-轮转与可达性` | 正文删除或缩为「内容 = 摘要 + 时长，由 app 抽取」 |
| `docs/design/04-simulation-engine.md#simulation-engine-人物所见与可做之事`、`docs/design/04-simulation-engine.md#simulation-engine-程序加速与媒体倍速`、`docs/design/04-simulation-engine.md#simulation-engine-活动生命周期`、`docs/design/04-simulation-engine.md#simulation-engine-反馈续行与结束`、`docs/design/04-simulation-engine.md#simulation-engine-判断唤醒条件`、`docs/design/04-simulation-engine.md#simulation-engine-结算边界与决策机会`、`docs/design/04-simulation-engine.md#simulation-engine-将真实结果交还人物`、`docs/design/04-simulation-engine.md#simulation-engine-预排事件` | app 目标；没有倍速；app 使用生命周期与接续；`app_push` 唤醒与边界；`details.app_content`；推送预排 |
| `docs/design/05-recording-engine.md#recording-engine-事件类型枚举`、`docs/design/05-recording-engine.md#recording-engine-事件正文与资源证据`、`docs/design/05-recording-engine.md#recording-engine-activity_progress-事件`、`docs/design/05-recording-engine.md#recording-engine-已核验经历与主观记忆`、`docs/design/05-recording-engine.md#recording-engine-共同身体结算与决策证据` | 子类型加 `app_push`、`work_topic_completed`，删供给与任务类；`data.content`；`played` 形状；22 个唤醒名的登记 |
| `docs/manual/resources.md#resources-编写内容与工作材料`、`docs/manual/resources.md#resources-只读查询与有时长浏览`、`docs/manual/resources.md#resources-编写生活活动` | 改为「编写 app 目录与内容」（五类、摘要来源、绑定）；浏览一节改为只读查询；生活活动一节不变但删刷手机示例 |
| `docs/manual/resources.md` 的 `resources-配置可玩的游戏` | 正文删除 |
| `docs/EXPERIMENT.md#experiment-内容与语料绑定`、`docs/EXPERIMENT.md#experiment-决策点节奏`、`docs/EXPERIMENT.md#experiment-实验假设声明`、`docs/EXPERIMENT.md#experiment-交互菜单与运行身份` | 内容行：真实作品名、摘要来源，无 OFF、Sintel、TextWorld、AMI、OSM；触发点补推送整点；假设行写推送窗口、app 使用时长、按类型缓解的值；占用与 `use_app` 改变 `tool_schema_sha256` |
| `docs/VERIFICATION.md#verification-一日行为可信性特性门禁`、`docs/VERIFICATION.md#verification-工程门禁`、`docs/VERIFICATION.md#verification-测试矩阵` | 播客行改写；`play_game` 例子换 `use_app`；TextWorld 前言删除；矩阵行 内容知识、候选与呈现一致性 改写 |
| `docs/dev/SPEC-run-viewer-names.md#spec-run-viewer-names-活动标题`、`docs/dev/SPEC-run-viewer-names.md#spec-run-viewer-names-运行内对象名称` | app 使用标题取 app 名；`app_push` 用中文说法「app 推送」显示 |

<a name="spec-rrs-文档阶段5"></a>
### 阶段 5（工作主题）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md` 的 `contracts-工作通知与开放合同` | 正文删除（主题在场景分配、运行开始即可见；归属行一并退役） |
| `docs/CONTRACTS.md#contracts-工作安排声明` | 保留；补一句有主题的人物必须声明工作安排、主题的地点就是工作地点 |
| `docs/CONTRACTS.md#contracts-状态归属`、`docs/CONTRACTS.md#contracts-活动受理拒绝码`、`docs/CONTRACTS.md#contracts-request-settlement`、`docs/CONTRACTS.md#contracts-closed-request-models`、`docs/CONTRACTS.md#contracts-action-results` | 工作 行 `{topic, target_seconds, worked_seconds, last_report}`；没有材料、交付、评审、付款状态；`WORK_*` 删除、`WORK_TOPIC_NOT_FOUND`；没有 `submit_work` 行与字段；`details.work[]`、`outcome` 只有吃掉、丢弃、剩余；没有 `work_review` |
| `docs/CONTRACTS.md#contracts-tool-result-channel`、`docs/CONTRACTS.md#contracts-generation-parameters`、`docs/CONTRACTS.md#contracts-model-identity` | 删「工作验收可在提交期间准备…」；没有工作验收阶段行，`purpose` 闭集 `{plan_day}`；阶段键恰为 `action`，校验器拒绝 `work_review` |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-具体活动结算`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-资金与合同`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-其他体感分级`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-交互菜单的运行边界`、`docs/dev/SPEC-life-runtime.md#tool-interaction-普通工作与预约活动`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-决策节律结算边界与唤醒条件分离` | 工作 行 = 主题工时；只有工资；没有验收脉冲与评审条款；普通工作一节 = 主题由场景分配、无预约、通知与材料（锚点保留，[DOC] 规范与四条验收行改写）；触发表删任务与打烊行 |
| `docs/dev/SPEC-life-realism.md#spec-life-realism-世界素材品类要求`、`docs/dev/SPEC-life-realism.md#persona-prompt-进度与记忆的更新-settled-的写法`、`docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-与工作任务合同的边界`、`docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-效力呈现事实不新增拒绝` | 工作 行 = 主题 + 目标工时；`settled` 无有效工时；「不改变开工与恢复判定」，未结清 → 未完成 |
| `docs/dev/SPEC-as2-integration.md#spec-as2-integration-lifeenv-职责`、`docs/dev/SPEC-as2-integration.md#persona-prompt-统一修复的程序错误`、`docs/dev/SPEC-as2-integration.md#spec-as2-integration-验收矩阵` | 没有评审等待；app 与推送职责；三行程序错误随台账删除；矩阵「六个世界请求工具」 |
| `docs/dev/SPEC-as2-integration.md` 的 `dayrun-fidelity-评审模型调用与叙述-中文逐字摘录按字数核对` | 正文删除（[DOC] 行 中文摘录 删除） |
| `docs/dev/SPEC-decision-context.md#spec-decision-context-权威来源与裁决`、`docs/dev/SPEC-decision-context.md#spec-decision-context-登记确认与结算结果`、`docs/dev/SPEC-decision-context.md#spec-decision-context-验收台账` | 工作验收当轮回灌 行、`work_review` 通道与 单一观察与批量结算 行删除；邮件结果不带任务引用 |
| `docs/dev/SPEC-day-plan-goals.md#spec-day-plan-goals-事件驱动的决策节律`、`docs/dev/SPEC-day-plan-goals.md#time-and-energy-零点计划与计划提醒-输入调用失败与记录` | 承诺不含工作任务、没有关门威胁；阶段键 `{action}`；零点计划输入的待办任务 = 主题与目标、已做 |
| `docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-稳定工具面`、`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-保护项修订`、`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-验收台账` | 八个工具（无 `submit_work`）；创作出口只有 `send_message`；行 模型工具 Schema、工作与消息创作 改写 |
| `docs/dev/SPEC-labelkit-delivery.md#persona-prompt-下游` | 阶段 `{action}`（其余在阶段 7） |
| `docs/design/00-overview.md#overview-算法与工程背书总表`、`docs/design/04-simulation-engine.md#simulation-engine-实际区间身体变化与效率`、`docs/design/04-simulation-engine.md#simulation-engine-从实际进度到世界变化`、`docs/design/05-recording-engine.md#recording-engine-质量报告`、`docs/design/07-evaluation.md#evaluation-报告项reports`、`docs/design/08-configuration.md#configuration-工作安排声明`、`docs/design/09-io-formats.md#io-formats-activitystate活动状态` | 工作验收 行删除；没有效率产出；`work_topic_completed`；质量报告 `work_topics`；`workload` 按实际工作秒数；工作安排声明补主题；活动状态的工作数据 |
| `docs/design/04-simulation-engine.md` 的 `simulation-engine-工作通知与开放时刻` | 正文删除 |
| `docs/manual/scenarios.md#scenarios-配置工作任务和消息`、`docs/manual/scenarios.md#scenarios-工作通知与目标发布`、`docs/manual/scenarios.md#scenarios-声明工作安排` | 改为「分配工作主题与消息」；通知与目标发布一节改为主题自运行开始可见；工作安排一节补主题地点 |
| `docs/manual/resources.md` 的 `resources-工作材料`、`resources-编写工作验收答案` | 正文删除 |
| `docs/EXPERIMENT.md#experiment-工作参数` | 没有评审调用与笔记倍数；主题与目标工时 |
| `docs/VERIFICATION.md#verification-报告项report-only`、`docs/VERIFICATION.md#verification-个人事务与工作安排特性门禁`、`docs/VERIFICATION.md#verification-测试矩阵`、`docs/VERIFICATION.md#errands-workday-度量与下游-行为评估` | `workload` 按工作秒数；退役行删除；矩阵行 工作生产、工作截止与提交 改写；行为评估的工时口径 |

<a name="spec-rrs-文档阶段6"></a>
### 阶段 6（观察、工具面与提示）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md#contracts-model-request` | 系统层 JSON 块第 2、7、8、12、13、17 条换成「系统层」定稿（与模块常量同一提交）；零点计划输入的待办任务 = 主题与目标、已做，动作结果不含工作验收 |
| `docs/CONTRACTS.md#contracts-person-tools`、`docs/CONTRACTS.md#contracts-seven-layers`、`docs/CONTRACTS.md#contracts-seven-layer-schema`、`docs/CONTRACTS.md#contracts-观察投影字段`、`docs/CONTRACTS.md#contracts-工具交互公开投影` | 六个世界工具、app 目标（按名调用不含内容）；目标类 +app −content −game −catalog；食品 `state`、任务 `contract` 四键、活动 `last_report`、`tools[]` 无 `submit_work`、`callable_index` 三键、进度记录类型；分阶段指纹叙述补本规格各阶段（B-19） |
| `docs/dev/SPEC-life-realism.md#persona-prompt-七层请求-工具层`、`docs/dev/SPEC-life-realism.md#persona-prompt-七层请求-进度层`、`docs/dev/SPEC-life-realism.md#persona-prompt-七层请求-人设层`、`docs/dev/SPEC-life-realism.md#persona-prompt-七层请求-唯一出现原则`、`docs/dev/SPEC-life-realism.md#persona-prompt-七层请求-系统层` | 按「观察与工具面」的形状；人设层 `tastes` 只是散文；系统层改写六条 |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-系统层`、`docs/dev/SPEC-life-runtime.md#spec-life-runtime-七层请求` | 指向合同的定稿；工具层 app 目标 |
| `docs/dev/SPEC-interaction-menu.md#spec-interaction-menu-观察与人物工具`、`docs/dev/SPEC-interaction-menu.md#spec-interaction-menu-声明与准入`、`docs/dev/SPEC-interaction-menu.md#spec-interaction-menu-环境执行与拒绝`、`docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引`、`docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序` | 六个世界工具；`use_app` 动作；没有评审条款；可调用索引三键；地点类资格按派生的活动环境 |
| `docs/dev/SPEC-run-viewer-context.md#spec-run-viewer-context-请求选择与关联`、`docs/dev/SPEC-run-viewer-context.md#spec-run-viewer-context-结构与原文`、`docs/dev/SPEC-run-viewer-context.md#spec-run-viewer-context-增长与退出` | 工具条目 11 个（6 世界 + 2 查询 + 3 记忆）；进度层规则文字；夹具重录 |
| `docs/EXPERIMENT.md#experiment-交互菜单与运行身份`、`docs/dev/SPEC-labelkit-delivery.md#persona-prompt-下游`、`docs/design/04-simulation-engine.md#simulation-engine-当前实现边界`、`docs/design/01-architecture.md#architecture-状态与记录`、`docs/design/06-delivery.md#delivery-接收记录引擎导出` | 系统层六条改写与工具面改变 `prompt_sha256` 与 `tool_schema_sha256`；实施前后的运行不可合并比较 |
| `docs/VERIFICATION.md#verification-人物-prompt-七层特性门禁`、`docs/VERIFICATION.md#verification-测试矩阵` | 七层提示 行括注补本规格改写的六条；退役行删除 |

<a name="spec-rrs-文档阶段7"></a>
### 阶段 7（记录、导出与查看器）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md#contracts-labelkit-输入结构与窗口编码`、`docs/CONTRACTS.md#contracts-labelkit-训练记录与交付格式`、`docs/CONTRACTS.md#contracts-交付格式`、`docs/CONTRACTS.md#contracts-版本表`、`docs/CONTRACTS.md#contracts-运行诊断输出` | 手机行来自 `app-usage.jsonl`；交付清单 3；版本表行 导出 3、质量 5、交付 3；`OPS_COMPONENTS` 减 `review` |
| `docs/dev/SPEC-labelkit-delivery.md#persona-prompt-下游`、`docs/dev/SPEC-labelkit-delivery.md#dayrun-fidelity-下游与质量报告`、`docs/dev/SPEC-labelkit-delivery.md#spec-labelkit-delivery-验收矩阵` | `purchases.jsonl` 列、`app-usage.jsonl`、世界 10、质量 5、交付 3；订单带 `app_id`；手机合成读 `app-usage.jsonl`；矩阵行 |
| `docs/design/05-recording-engine.md#recording-engine-导出行为`、`docs/design/05-recording-engine.md#recording-engine-调试工具`、`docs/design/05-recording-engine.md#recording-engine-版本表` | 清单 3 与五个工件；查看器标签；特性标识版本行 |
| `docs/design/06-delivery.md#delivery-地图与门店供给的证据窗口`、`docs/design/06-delivery.md#delivery-版本历史` | 窗口里的出行与推送投影；版本行 |
| `docs/design/07-evaluation.md#evaluation-协议门禁gates`、`docs/design/07-evaluation.md#evaluation-证据事件publicevidence`、`docs/design/07-evaluation.md#evaluation-适配器职责`、`docs/design/07-evaluation.md#evaluation-版本历史` | 证据形状（`use_app`、`route` 键 `trip_id`）；适配器闭集 |
| `docs/design/09-io-formats.md#io-formats-labelkit-交付格式` | 交付格式 3 |
| `docs/VERIFICATION.md#verification-协议门禁`、`docs/VERIFICATION.md#verification-交付阶段与通过条件` | 新质量键；行 内容与出行 |
| `docs/dev/SPEC-run-viewer-names.md#spec-run-viewer-names-验证`、`docs/dev/SPEC-run-viewer-context.md#spec-run-viewer-context-验证与边界` | Node 用例的计数（唤醒 22、工具 11）与标签 |
| `docs/manual/evaluation.md#evaluation-修改类别映射和公开参照`、`docs/manual/delivery.md#delivery-修改规则数据` | 类别映射按十个活动类型；规则数据的来源路径 |

<a name="spec-rrs-文档阶段8"></a>
### 阶段 8（文档与登记收口）

| 文档与位置 | 改写后的语义 |
| --- | --- |
| `docs/INDEX.md#index-特性规格与提案`、`docs/INDEX.md#index-按任务读`、`docs/INDEX.md#index-核心系统`、`docs/INDEX.md#index-测试`、`docs/INDEX.md#index-场景与配置`、`docs/INDEX.md#index-版本表` | 各行的「随阶段 N 提交实现」改「修订条款已实现」；本规格一行注明阶段 1–8 已实现、阶段 9 待数据仓发布后登记回执（写成未执行事实，不写成通过）；模块行加 `src/worldmock/exchange/`、改 `src/worldmock/catalog/` 与 `src/worldmock/corpora/` 的职责；测试行加 `tests/exchange/`；版本表加行 |
| `docs/dev/implementation-status.md#implementation-status-worldmock-实现状态` | 新增「资源与规则引擎简化开发状态」一节：范围、各阶段提交、证据层级与平台、未执行的 POSIX 与 macOS 回执、数据仓发布前的全量层状态；`implementation-status-当前世界格式worldstateschema_version-为-9` 一节改为 10；工具交互、一日运行保真、地图与门店供给、地图与品类组合、候选与呈现、口味权重、数据日志与模型接入、项目级真实验收边界、已冻结条款与确定性证据 各节把被取代的条款改写为历史并指向本规格；文件头状态行补本特性 |
| `docs/VERIFICATION.md#verification-worldmock-验收标准`、`docs/VERIFICATION.md#verification-测试矩阵`、`docs/VERIFICATION.md#errands-workday-度量与下游-首次受影响发布身份`、`docs/VERIFICATION.md#verification-版本表` | 新增「资源与规则引擎简化特性门禁」一节（台账、登记、守卫、全量与覆盖率的执行平台、文档与汇编门禁、变异审查）；矩阵「本次特性」表加行，矩阵行 共同恢复 改写；首次受影响发布身份写本特性的世界 10、场景 7、两个指纹；版本表加行 |
| `docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-规格测试用例覆盖率追溯矩阵`、`docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-工具交互增量映射`、`docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-一日运行保真增量映射`、`docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-其他现行特性的证据归属`、`docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-版本表` | 本特性映射由机器登记逐行生成；退役行从五个特性的映射删除；版本表加行 |
| `docs/design/README.md#readme-文档清单`、`docs/design/README.md#readme-工具交互设计同步`、`docs/design/README.md#readme-版本`、`docs/design/SUMMARY.md#summary-exchange-enginemd`、`docs/design/SUMMARY.md#summary-resource-enginemd`、`docs/design/SUMMARY.md#summary-configurationmd`、`docs/design/SUMMARY.md#summary-地图与门店供给修订` | 新增「资源与规则引擎简化修订」一节并列出各阶段受影响章节；文档清单的交换与资源引擎职责改写；版本表加行；`docs/design/worldmock-design.html` 重新汇编 |
| `docs/ARCHITECTURE.md#architecture-一条完整生活经历如何形成数据`、`docs/ARCHITECTURE.md#architecture-版本表` | 数据流写 app 使用、推送与主题工时；版本表加行 |
| `README.md`、`docs/manual/README.md#readme-按你想改变的事情选择入口`、`docs/manual/README.md#readme-创建自己的第一个场景`、`docs/manual/runtime.md#runtime-工具交互的运行检查`、`docs/INDEX.md#index-用户配置手册` | 命令与「实验与数据」一句；手册入口按新页名（世界素材一行改为「店铺、食品、app 与内容」，规则配置一行删「编写环境品类与交换规则」）；运行检查的命令；TextWorld 与 x86_64 的说法删除 |
| `docs/dev/coverage-baseline.md#coverage-baseline-基线记录` | 阶段 9 的全量与覆盖率回执发布后追加（阶段 8 只写「未执行」） |

<a name="spec-rrs-测试与夹具"></a>
## 随实现一起改的测试与夹具

改写的测试只在原位修改断言与说明，不改节点名；含义已不成立的用例改名并同步登记它所在的台账（同步登记，不静默）。新测试只写文件路径，节点登记在机器台账。

| 阶段 | 位置 | 改动 |
| --- | --- | --- |
| 0 | `tests/fixtures/rrs-acceptance.json`、`tests/rrs_acceptance.py`、`tests/test_rrs_acceptance.py`（新增） | 台账、登记与守卫（见「机器登记」）；`STAGES_DONE` 为空 |
| 0 | `tests/fixtures/acceptance_requirements.json`、`tests/fixtures/current-feature-owners.json` | 新键 `resource-rule-simplification`：`验收台账` 表行、`normative_requirements`（本规格每个 `<a name>` 节一条）、`mutation_requirements`（变异表每行一句）；归属映射指向本文件锚点 |
| 0 | `tests/test_current_feature_owners.py`、`tests/test_layering_acceptance.py` | `OWNERS` 与 `FEATURES` 接纳第六个现行特性；归档断言只对已归档特性成立；147 字面量去掉，守卫核对 登记 ∩ 基线 |
| 1 | `tests/fixtures/environment/**` | 目录文件按「人工环境」重写：删五个旧目录文件、`corpora/artificial-map/*`、`corpora/content/test.srt`；`corpora/manifest.json` 只列生活活动；三份场景改结构 7；`README.md` 重写 |
| 1 | `tests/exchange/test_types.py`、`test_constants.py`、`test_operators.py`（新增） | 闭集与标签、`RULES_VERSION` 钉为 1、常数表单一来源、Decimal 舍入、车费下限、按比例能量、送达与出行向上取整 |
| 1 | `tests/catalog/test_loader.py`、`test_admission.py`、`test_mapgen.py`、`test_graph.py`、`test_identity.py`、`test_affordance_shadow.py`、`test_engine_package_resources.py`（重写或新增）；`tests/catalog/conftest.py` 改为注册目录构造器 | 登记往返、每个登记码一个反例、旧文件拒绝、`all` 展开、同类一致、连通性、规则版本不符；生成器确定性、树与额外边、长度范围、规范排序、字节稳定；手工图最短路与决胜；原子发布失败保留旧字节（[SR] 原子写 行改指）；闭集七个（阶段 4 改八个）、退役名拒绝、包资源字节 |
| 1 | `tests/frozen_inputs.py`、`tests/test_frozen_inputs.py` | 补丁目标只剩 `world_setup.load_runtime_scenario`、`load_manifest`、`engine_context.load_lifestyle_items`；缓存键覆盖新文件；参数化改名 `map.json`、`foods.json`、`contents.json` |
| 1 | `tests/test_single_source.py`、`tests/test_import_graph.py`、`tests/test_environment_profiles.py` | `OWNERS` 删 `categories/*` 与 `relief_preference` 行，加 `base_models`、`resources/models`、`exchange/*`、`catalog/errors` 行；导入图删 `categories.cli` 延迟行与 `map_models` 迁移行，`FOUNDATION` 加 `worldmock.exchange.types`；购买世界节点改在新构造器上 |
| 2 | `tests/life/test_scenario.py`、`test_load_checks.py`、`test_session_state.py`、`test_state_serialization.py`、`test_semantics.py`、`tests/integration/test_environment_identity.py`（由 `test_category_identity.py` 改）、`test_run_manifest.py`、`test_runtime.py`、`tests/delivery/test_publication.py`、`tests/integration/test_external_environment_lifecycle.py` | 结构 7 受理、结构 6 字段逐个拒绝、环境钉不符、商家账户缺失；世界 10 有效、9 在打开、恢复、重放与交付门禁拒绝；清单 3 与 `environment_identity`；能量阈值来自 WorldMock 身体常数（[TE] 节点改名 `..._come_from_worldmock_body_constants`）；外部环境生命周期用例在新夹具上通过（[QT]） |
| 2 | `tests/life/test_travel_graph.py`、`test_travel_cost.py`（新增）、`test_wayfinding.py`、`test_location_sessions.py`、`tests/life/test_domain_branch_coverage.py`、`tests/evaluation/test_branch_coverage.py`、`tests/evaluation/test_adapters.py` | 时长 = ceil(长度 ÷ 速度)、车费、同地点拒绝、`ROUTE_DURATION_MISMATCH`、进度快照保留起点（[TE] 节点改指同名新节点）、图查询、出行证据键 |
| 3 | `tests/life/test_menu_discovery.py`（由 `test_storefront_discovery.py` 改）、`test_storefront_commerce.py`、`test_purchase_stages.py`、`test_named_purchase.py`、`test_giving.py`、`test_purchase_apps.py`（新增）、`test_commerce.py`、`test_request_linking.py`、`test_name_resolution.py`、`test_world_query*.py`、`test_callable_index.py`、`test_error_contract.py`、`tests/life/purchase_worlds.py`（改为编辑 `foods.json` 绑定与 `locations.json` 的 app 并重钉清单）、`tests/integration/test_storefront_tools.py`、`tests/footprints/test_purchases.py`、`tests/life/test_scheduling.py` | 同类同菜单；逐地点送达（含最远点与离开后的家）、费用、原子拒绝、送达幂等；app 绑定外卖、`APP_NOT_BOUND`、只到店的店、`from=<app>` 解析、多 app 取最小标识；路途中自送拒绝；`ORDER_DUPLICATE_ITEM`；来源解析接受 app；错误合同的码与原因配对 |
| 3 | `tests/life/test_meal_outcome.py`、`test_dayrun_inventory_regressions.py`、`test_body.py`、`test_body_flows.py`、`test_felt_states.py`、`test_energy_feasibility.py`、`test_activity_effects.py`、`tests/integration/test_meal_checkpoint.py`、`tests/life/test_dayrun_restaurants.py`、`tests/footprints/test_verified_experiences.py`、`tests/life/test_motivation.py`、`test_tool_layer.py` | 断言改为 `FOOD_CONSTANTS` 与身体常数；节点名保留（[PP] `_MEAL`、[TE] 能量感知、能量不拦人、绿色基线 行）；类别候选；商品 `state` 文案 |
| 3 | `tests/profile/test_injection.py`、`test_coverage.py`、`tests/integration/test_input_warnings.py`、`tests/footprints/test_validation.py`、`tests/integration/test_persona_profile.py`、`tests/footprints/test_quality.py` | 闭集六个码；覆盖率目标由目录派生；没有口味、游戏、路线节点（[DF] 输入警告、场景作息 行改写） |
| 4 | `tests/life/test_app_use.py`、`test_app_push.py`（新增） | 行为用例 T33–T41 |
| 4 | `tests/catalog/test_affordance_shadow.py`、`tests/life/test_parallel_admission.py`、`test_parallel_acceptance.py`、`test_parallel_activities.py`、`test_paused_slots.py`、`test_candidates.py`、`test_presentation_fidelity.py`、`test_relevance.py`、`test_retrieval.py`、`test_readable.py`、`test_lifestyle.py`、`test_mail_reading.py`、`test_duration_boundaries.py`、`test_shared_rules.py`、`test_activity_title_embedding.py`、`test_activities.py`、`test_activity_effects.py`、`tests/test_test_tiers.py`、`tests/life/test_tool_interaction_habits.py` 与 `tests/fixtures/tool_interaction_habits.json` | 八个占用名、十三个退役名、同种配对；app 使用的同种与跨种行；候选三种；可读名；app 使用时长单一来源（[SR] 活动时长 行改为 `test_app_use_duration_matches_tool_layer_and_admission`）；习惯链改为 打开、到店、远程下单、食用 |
| 4 | `tests/life/test_wake_reasons.py`、`tests/footprints/test_event_fields.py`、`tests/tools/run_viewer*.test.cjs`、`tests/life/test_scheduling.py`、`tests/scheduling/test_branch_coverage.py`、`test_progress_events.py`、`test_session_attribution.py`、`test_same_moment_decisions.py`、`test_activity_wake_facts.py`、`test_discomfort_and_sleep_messages.py`、`test_day_planning.py` | 22 个名字与次序（有序表由 `test_work_contracts.py` 迁来）；`app_push` 记录；推送行；任务开放节点改为推送节点（[IF] 第 7 条引擎证据 行改指） |
| 5 | `tests/life/test_work_topics.py`（新增） | 行为用例 T43–T46 |
| 5 | `tests/life/test_paused_slots.py`（`..._checks_the_work_deadline_before_the_free_slot` 改名 `..._checks_the_topic_cap_before_the_free_slot`）、`test_tool_layer.py`、`test_progress_layer_projection.py`、`test_memory_layer.py`、`test_day_planning.py`、`test_workload_report.py`、`tests/evaluation/test_workload_metrics.py`、`tests/integration/test_run_manifest.py`、`tests/life/test_restored_model_identity.py`、`tests/test_shared_model_clients.py`、`tests/delivery/test_windows.py`、`tests/integration/test_activity_schema.py`、`test_strict_endpoint.py`、`tests/life/test_engine_branch_coverage.py` | 主题上限次序；`last_report`；记忆与待办输入；负荷按工作秒数与完成事件（[EW] `_WORKLOAD` 行、`duty_vanishes_after_topic_completion`）；阶段 `{action}`；没有评审客户端 |
| 6 | `tests/integration/test_seven_layer_request.py`、`test_tool_description_references.py`、`test_life_agent_protocol.py`、`test_activity_schema.py`、`test_decision_context_protocol.py`、`tests/life/test_prompt_layers.py`、`test_narration.py`、`tests/tools/test_run_viewer.py`、`tests/fixtures/viewer-context.json` | 六条改写的正向与反向断言（T49）；工具数与指纹；Schema 闭集（`test_progress_schema_registers_the_nine_record_types` 改为新计数）；查看器夹具 `action`、`continuation` 从新格式运行重录 |
| 7 | `tests/footprints/test_export.py`、`test_validation.py`、`test_quality.py`、`test_verified_experiences.py`、`test_render_day.py`、`tests/delivery/test_publication.py`、`test_windows.py`、`test_host_delivery_coverage.py`、`tests/evaluation/test_adapters.py`、`test_branch_coverage.py`、`test_behavior_metrics.py`、`test_workload_metrics.py`、`tests/life/test_export_integration.py`、`test_rerun_metrics.py`、`test_continuation_metrics.py`、`test_query_cost_report.py`、`tests/fixtures/dayrun-biz-timeline.json` | 清单 3、五个工件、`app-usage.jsonl` 事实、质量 5 新节（[TE] 重看节点改名 `..._counts_completed_rounds`）、交付清单 3、`app_push` 投影、`trip_id` 证据键；时间线夹具重录 |
| 8 | `tests/integration/test_dayrun_complete_days.py`、`tests/fixtures/dayrun-resource-rule-baseline.json`（新增）、`tests/dayrun_baseline.py`（不变） | 脚本日改在人工环境上：走到面馆到店买正餐并吃、打开 app 并收到一次推送、经美团向最远点下外卖、工作一个主题并播报两次、睡觉；结构断言（86400 秒、身体结算分区、同秒上限、零点计划、决策事件、反思、动作请求、活动协议、导出与时间线重算）保留；两次一致运行后冻结第三份基线（平台见 C-7）；两份历史 JSON 字节不动 |
| 8 | `tests/tool_interaction_acceptance.py`、`tests/dayrun_fidelity_acceptance.py`、`tests/persona_prompt_acceptance.py`、`tests/time_and_energy_acceptance.py`、`tests/errands_workday_acceptance.py`、`tests/idle_fillers_acceptance.py`、`tests/shared_rules_acceptance.py`、`tests/fixtures/idle-fillers-acceptance.json`、`tests/fixtures/layering-acceptance.json`、`tests/fixtures/layering-public-api.json`、`tests/fixtures/acceptance_requirements.json`、`tests/fixtures/current-feature-owners.json`、`scripts/fresh_check.py`、`docs/dev/test-durations.json` | 「删除清单」点名的行、变异与文件逐项退役或改指（[TI] 删 类别供给、地点定价、料理 与十二个变异，改写 购买与食用、查询与预算；[DF] 删 购买渠道、加载校验地图行、`_MAP`、`_COVERAGE_APPS`、`_CHANNELS`、`_REPLAY`、`_RELIEF`、`_PREVIEW`、中文摘录，改写 指名购买、更简单的购买请求、到店与外卖提示、输入警告、场景作息，裁决 D8、D17、D19、S7、S9、S10、S11 标为被本规格取代；[PP] 改写 进食、指名购买、代点关联、不足一份，删三个工作程序错误；[TE] 改写 能量感知，`REMOVED_SYMBOLS` 新增阶段列出被删符号；[EW] 改写 完成依据、来源筛选、可执行筛选、分页与返回，重登记 `world_query` 变异原文；[IF] 九个同步点与登记原文核对；[SR] 行文随本规格各阶段；[LA] 公开 API 基线重录（−`prepare_pending_work_review`）、`NEW_PRODUCTION` 与实现行；`QUICK_TESTS` 以 `tests/catalog/test_loader.py` 取代 `tests/life/test_content_catalog_titles.py`；耗时文件重录）；每处改动的判据文字随文更正，节点名、变异名与变异原文只在本表点名处改 |

<a name="spec-rrs-人工环境"></a>
### 人工环境

`tests/fixtures/environment/` 是声明的人造世界，名字是普通测试名，app 名允许用真实名字（B-7）；它必须让全部行为用例与完整日用例不带 `--environment-root` 通过（D-46）。

| 文件 | 内容 |
| --- | --- |
| `catalog/apps.json` | `meituan` 美团、`eleme` 饿了么、`douyin` 抖音、`bilibili` 哔哩哔哩 |
| `catalog/locations.json` | `home` 家（home）、`office` 公司（office）、`noodle-1` 一心面馆（restaurant，`app_ids: [meituan]`）、`noodle-2` 二味小炒（restaurant，无 app）、`cafe` 三杯咖啡（cafe，`app_ids: [meituan, eleme]`）、`store` 四季便利（convenience_store，无 app）、`park` 五里公园（park） |
| `catalog/foods.json` | `chips` 薯片（snack，800 分，`"all"`）、`lemon-tea` 柠檬茶（drink，1200 分，`"all"`）、`beef-noodles` 牛肉面（meal，2800 分，`[noodle-1, noodle-2]`）、`sandwich` 三明治（meal，2500 分，`[cafe]`） |
| `catalog/contents.json` | `c-article` 《测试文章》（article，600 秒，`[bilibili]`）、`c-image` 《测试图文》（image_text，300 秒，`[bilibili]`）、`c-short` 《测试短视频》（short_video，120 秒，`[douyin]`）、`c-short-2` 《测试短视频二》（short_video，90 秒，`[douyin]`）、`c-long` 《测试长视频》（long_video，2400 秒，`[douyin, bilibili]`）、`c-live` 《测试实况》（live，1500 秒，`[douyin]`）；摘要均为合成（`summary_source.kind = synthetic`，`notice` 写「测试用合成摘要」） |
| `catalog/work_topics.json` | `topic-sales` 季度销售数据整理（`target_hours: 8`） |
| `catalog/map.json` | 由 `worldmock environment generate-map --seed 7` 生成（七个节点、六条树边加三条额外边）；最远点由图决定，用例按最短路选取 |
| `catalog/manifest.json` | `environment_id: "artificial-test"`、`environment_version: "1"`、`rules_version: 1`、九个文件的清单与摘要 |
| `catalog/affordances.toml`、`catalog/rules/*.toml` | 与包资源逐字节相同（阶段 1 与阶段 4 各刷新一次，清单摘要随之） |
| `corpora/manifest.json`、`corpora/lifestyle/items.json` | 只有生活活动（安静休息，显式声明娱乐、社交与健康效果） |
| `scenarios/single-person.json` | 结构 7；一个人物，家在 `home`，工作安排为工作日 09:00–18:00 在 `office`，`work_topic_ids: [topic-sales]`，`initial_inventory` 在家一份薯片；账户 `buyer`、`merchant:noodle-1`、`merchant:noodle-2`、`merchant:cafe`、`merchant:store`、`employer:test`、`transport:service`、`external:scenario`；`payrolls[]` 一条；菜单键为八个类型 |
| `scenarios/multi-person.json`、`scenarios/multi-person-half-day.json` | 两个人物（一人有工作安排与主题，一人没有），其余同上；半天版窗口为四小时 |

<a name="spec-rrs-验收台账"></a>
## 验收台账

所有下列要求必须实现并有可收集的行为或结构用例，登记到 `tests/fixtures/rrs-acceptance.json`；登记与本表一一对应，每行写阶段、规格锚点、测试节点与实现符号。用例经真实请求、引擎与 SQLite 的，以 `engine_factory` 加 `mutate` 在人工环境上构造场景。提案「必须钉住的行为」十三行各由至少一个契约点覆盖，括号里是对应的行。

| 契约点 | 阶段 | 必须通过的验证 |
| --- | --- | --- |
| 分类与规则归系统（分类与规则归系统） | 1 | T1、T2、T5：闭集与常数单一来源，环境里的热量覆盖、表达式与自定义类型被拒绝 |
| 环境登记与错误码 | 1 | T3、T4、T7、T13 |
| 同类店铺同商品（同类店铺同商品） | 1 | T6：只给同类型的一家绑定被拒；`all` 不含住宅 |
| 地图连通且长度可计时（地图连通且长度可计时） | 1 | T8、T9、T10：孤立点、多个连通分量、非正长度被拒；已知小图的最短路与耗时 |
| 占用闭集与包资源 | 1 | T11、T12 |
| 场景 7 与世界 10 的拒绝边界 | 2 | T14、T15、T16 |
| 出行按图计时计费 | 2 | T17、T18、T19 |
| 位置记录与地图一致（位置记录与地图一致） | 2 | T20 |
| 全地图外卖（全地图外卖） | 3 | T23、T25、T26 |
| app 与店铺绑定有效（app 与店铺绑定有效） | 3 | T21、T22、T24、T32 |
| 食品热量固定（食品热量固定） | 3 | T27、T28、T29 |
| 购买足迹与画像 | 3 | T30、T31 |
| 娱乐只从所选 app 取内容（娱乐只从所选 app 取内容） | 4 | T33、T34、T35、T40 |
| 推送与消费关联（推送与消费关联） | 4 | T37、T41 |
| 时间窗口精确（时间窗口精确） | 4 | T36、T39 |
| 随机和状态可恢复（随机和状态可恢复） | 4 | T38；T23 的断点前后无重扣款，T27 的断点前后无双倍热量 |
| 游戏链退出 | 4 | T42 |
| 工作由时间决定（工作由时间决定） | 5 | T43、T44、T45、T46 |
| 工作面与阶段身份 | 5 | T47、T48 |
| 系统层逐字 | 6 | T49 |
| 工具面与 Schema | 6 | T50、T51 |
| app 足迹来自真实动作（app 足迹来自真实动作） | 7 | T52、T53、T54 |
| 完整日基线 | 8 | T55 |
| 工程门禁与文档一致性 | 8 | T56 |
| 完整回归与数据仓回执（只报告） | 9 | 「数据仓交接」与「门禁与平台」；不登记测试节点 |

<a name="spec-rrs-行为用例"></a>
### 行为用例

| 编号 | 用例验证什么 |
| --- | --- |
| T1 | `exchange.types` 的闭集成员与标签、`RULES_VERSION == 1`、常数表与身体常数只在 `exchange` 定义（`OWNERS` 核对），别处没有第二份 |
| T2 | 算子：Decimal 舍入与今天的品类算子逐值相同；驾车车费下限 500 与每米 0.2 向上取整、步行 0；按比例能量；送达与出行秒数向上取整 |
| T3 | 人工环境登记往返：七个目录文件加载后派生索引（按类型菜单、按店食品、按 app 店铺与内容、按店 app、最短路）与手工期望相等 |
| T4 | 每个登记码一个最小反例（参数化，覆盖「注册校验与错误」全表），码与英文原因相符，没有一条被静默通过 |
| T5 | 旧格式拒绝：目录下残留 `category-contract.json`、`exchange-rules.json`、`objects.jsonl`、`resources.jsonl`、`rules/travel-time.toml` 各以 `ENVIRONMENT_STALE_FILE` 拒绝；食品带 `kcal` 字段、内容带 `genre`、地点带坐标各被严格模型拒绝；`rules_version: 2` 以 `RULES_VERSION_MISMATCH` 拒绝 |
| T6 | 只给 `noodle-1` 绑定一个正餐而 `noodle-2` 不绑 → `FOOD_BINDING_KIND_INCONSISTENT`；`"all"` 展开后 `home`、`office`、`park` 的菜单为空；两家面馆菜单逐项相同；价格来自食品 |
| T7 | 没有任何绑定的 app → `APP_UNBOUND`；app 名与地点名归一后相同 → `APP_NAME_COLLIDES_LOCATION`；摘要 201 字符 → `CONTENT_SUMMARY_TOO_LONG`；`real` 摘要无 `url` → `CONTENT_SOURCE_URL_MISSING`；非食品店铺带 `app_ids` → `ENVIRONMENT_KIND_INVALID` |
| T8 | 生成器：同地点清单与种子两次生成逐字节相同，不同种子不同；边数 = (n−1) + ⌊(n−1)/2⌋，n=2 恰一条、n=3 恰三条且无重复；长度都在 [200, 2000]；边按 (a, b) 规范排序；标号 i 对应第 i 个排序标识 |
| T9 | 校验：单节点 → `MAP_TOO_SMALL`；自环、重复对、未知端点、长度 0、未排序各 → `MAP_EDGE_INVALID`；两个分量 → `MAP_DISCONNECTED`；篡改一条边长后 `generate-map --check` 与 `validate --environment` 都报 `MAP_NOT_REGENERATED`，而运行时加载器只校验结构、不重生成 |
| T10 | 手工图：最短路长度与路径与手算相等；等长路径取标识字典序较小者；`distance_meters(a, a) == 0`；路径可经过未被场景选择的节点 |
| T11 | 闭集七个名（阶段 4 改八个）、十三个退役名解析时拒绝；只有同种、睡眠一行与驾车一行有交集；包资源与人工环境副本逐字节相同，清单摘要随之 |
| T12 | 清单身份发布失败时旧字节保留（原子写） |
| T13 | 命令：`generate-map` 写出与库相同的字节；`validate --environment` 对人工环境退出码 0，对每个反例非零并打印码 |
| T14 | 结构 7 受理；结构 6 的每个已删字段各被 `extra="forbid"` 拒绝；`environment` 钉与清单不符拒绝；缺 `merchant:cafe` → `MERCHANT_ACCOUNT_MISSING`；未知主题 → `WORK_TOPIC_UNKNOWN`；有主题无安排 → `WORK_SCHEDULE_REQUIRED` |
| T15 | 世界 10 校验通过；结构 9 的检查点在打开、恢复、重放与交付门禁处各被拒绝 |
| T16 | 运行清单 3 带五个身份字段；篡改目录一字节后恢复以 "Runtime environment identity mismatch" 拒绝；持有不存在的食品标识在 `EngineContext.validate` 处拒绝 |
| T17 | 步行与驾车：时长 = ceil(最短路 ÷ 速度)；车费按常数付给 `transport:service`，步行不扣款；活动数据带 `trip_id`、`path`、`distance_meters`；`ROUTE_DURATION_MISMATCH` 按图重算 |
| T18 | 目标为当前地点 → `ACTIVITY_PRECONDITION_UNMET`（`already_at_destination`）；工具层列出全部其他已选地点各两种方式及价格与时长，不列当前地点 |
| T19 | 出行进度快照保留起点、到达经核验；路途中检查点恢复后继续走完同一路径 |
| T20 | 事件、活动行、公开证据与查看器名称索引只有 `trip_id`、`path`、`distance_meters`、`origin_id`、`destination_id`；任何工件里没有 `route_id`、坐标或轨迹 |
| T21 | 观察里两家面馆的商品目标集合逐项相同；没有库存、营业、报价字段；`price` 来自食品 |
| T22 | 在 `noodle-2`（无 app）到店买牛肉面成功、即时进入当地持有、`app_id` 为 null；`from=美团` 配方式到店 → `SUPPLY_SOURCE_MISMATCH` |
| T23 | 从 `noodle-1` 经美团向每个已选地点逐一下单（含最远点、`park` 与下单后离开的家）都受理并送达；`expected_at = now + 600 + ceil(d ÷ 5)`；每单费用 = 货款 + 500；一笔转账给 `merchant:noodle-1`；送达前检查点恢复后只送一次、不重扣款 |
| T24 | 经饿了么向 `noodle-1` 下单 → `APP_NOT_BOUND`；对 `noodle-2` 显式远程下单 → `FULFILLMENT_NOT_SUPPORTED`（"This store has no delivery app."）；经美团买三明治以外的、美团店铺不卖的食品 → `SUPPLY_NOT_AT_STORE`；`from` 为 null 向三杯咖啡下单取 `eleme`（最小标识）并写进 `details.notes`；`prefer` 最快拿到按 ETA 或距离、到手价最低含配送费，并列按 `store_id` |
| T25 | 路途中给自己下外卖 → `ACTIVITY_PRECONDITION_UNMET`（`delivery_destination`，"Place the order before departing or after arriving."）；给他人下单送到收货人的收货地点 |
| T26 | 同店同食品两行 → `ORDER_DUPLICATE_ITEM`；未知收货地 → `DESTINATION_NOT_FOUND`；余额不足整单拒绝且没有部分扣款 |
| T27 | 薯片与另一种零食等量食用原始摄入相同；一份牛肉面分两次吃与一次吃总摄入相同；暂停、恢复不多算；净能量经利用效率与上限；`portion_duration` 与 60 秒下限、14400 秒上限；断点前后不出现双倍热量 |
| T28 | 食品目标 `state` 为「正餐，每份约 600 千卡；可用美团下单」，无 app 的店省略后半句；`callable_index.goods` 按类别标签分组；`obtainable_here` 只在在场或店铺有 app 时为真 |
| T29 | 动机层正餐候选只含 `meal`，零食与饮品是轻食后备；外卖等待 = 备餐 + 图上时间 |
| T30 | `purchases.jsonl` 的购买行带 `app_id`（到店为 null）、`delivery_fee_fen`、`store_id`、`food_id`；进食证据与已核验经历带类别与份额 |
| T31 | 输入警告闭集恰为六个；覆盖率目标 = 习惯关键词、食品名、五个类型标签 + app 名；任何状态、卡片与观察里没有 `taste_weights`；坐标状地点名仍发 `LOCATION_NAME_SUSPICIOUS` |
| T32 | 指名购买 M0–M3：写店名在场即到店；写 app 名走该 app 绑定的最近店铺；写无绑定的 app → `SOURCE_NOT_FOUND` 或 `SUPPLY_NOT_AT_STORE`；下一步写 `Go to <店>.` 或 `Order through <app>.` |
| T33 | 打开抖音只抽到抖音绑定的内容（种子穷举 200 次没有哔哩哔哩独有的内容）；打开美团 → `APP_CONTENT_EMPTY`，工具层同码同原因且 `available=false`；`start_activity` 的 `use_app` 没有内容参数 |
| T34 | 《测试长视频》在抖音看到 1000 秒后在哔哩哔哩打开从 1000 秒继续；看完后 `completed_rounds` 为 1，再抽到时从 0 开始并记第二轮 |
| T35 | 一次 30 分钟活动内短视频结束后接着抽下一条并消耗剩余时长；暂停、恢复不重抽；主动结束后重新打开重新抽（有推送时先推送）；计划结束时位置写回进度 |
| T36 | 推送网格：08:00 产生、07:00 与 22:00 不产生；按场景时区；夏令时跳过的小时按跳变前偏移、重复的取第一次；每人每小时至多一条；同 session 同 app 至多一条；没有合格 app 时事件结算 `pushed: false`、无人物记录、不唤醒 |
| T37 | 同 session 打开命中推送内容；提前停止后重开仍命中；在另一个绑定 app 完成后解除；出发或到达后失效但进度保留；睡眠中收到的推送存在 session 上、不唤醒、醒后可见；使用中收到推送不打断；22:00 之后同 session 仍按推送消费 |
| T38 | 相同环境、种子与动作序列得到相同的推送与内容序列；活动中途检查点恢复不重抽；反事实重演相同；推送事件在提交前弹出、恢复不重发 |
| T39 | 唤醒词表 22 个按声明次序，`app_push` 在 `break_end` 之后、不在睡眠允许集合；足迹侧词表、事件字段用例、查看器计数一致 |
| T40 | 缓解速率按内容类型固定、与口味无关；驾车时打开 app → `RESOURCE_CONFLICT`；吃饭 ∥ 打开 app 受理；两个 app 使用同种拒绝 |
| T41 | 进度层 `app_push` 记录带 app、标题、类型标签与完整摘要并随容量折叠；`details.app_content` 带 `pushed`；质量报告 `app_pushes` 六个计数与实际一致 |
| T42 | `pyproject.toml` 没有 `textworld`；`scripts/run_tests.py` 的快速层在 Windows 上完成收集；游戏码与方式不再存在 |
| T43 | 主题自运行开始列在工具层；在 `office` 开始受理、在家 → `ACTIVITY_PRECONDITION_UNMET`（`required_location_id`）；开始时长 = min(目标 − 已做, 上限)；剩余 59 秒 → `ACTIVITY_DURATION_INVALID` 与 D-50 的句子；恢复次序 地点 → 主题上限 → 名额 → 占用 |
| T44 | 暂停 30 分钟与并行进食不增加工时；`worked_seconds` 等于工作活动经过秒数之和 |
| T45 | `report_status` 写「已完成」不改变工时与完成；`last_report` 在换 session 后仍是上一次播报；`contract` 四键文案 |
| T46 | 累计满 8 小时时活动以 `planned_end` 结束，写 `work_topic_completed` 与 `work_completed`；主题离开工具层；按名再开始 → `ACTIVITY_DURATION_INVALID` |
| T47 | 工资事件照常入账；没有 `submit_work` 工具与模型；阶段身份恰为 `{action}`，带 `work_review` 的清单被拒 |
| T48 | 零点计划输入的待办任务 = 主题、目标与已做；动作结果输入没有工作验收 |
| T49 | 实际请求、接口合同与模块常量三者相等，十八条；「系统层」一节的全部既有约束通过；「有 app 推送」只在第 7 条，「娱乐只选 app」只在第 12 条，「按主题计工时」只在第 17 条；旧读法反向断言 |
| T50 | 原生工具 11 个、稳定面 8 个、没有 `submit_work`；工具指纹绑定注册表字节；`TARGET_KINDS`、`callable_index` 三键、进度记录类型的 Schema 闭集；提示指纹随六条改写变化 |
| T51 | 查看器夹具两处系统层与宿主系统消息逐字相等；查看器 Node 用例（唤醒 22、工具 11、新标签）通过 |
| T52 | 导出清单 3 列五个工件；`app-usage.jsonl` 五种事实与实际活动、推送一致；`validate_export` 通过；交付清单 3 复制 `environment_identity`；窗口里 `app_push` 投影四键；质量报告 5 的三个新节 |
| T53 | `render_day` 与 `inspect_run` 在新格式运行上输出主题与 app 标签；时间线夹具与重算逐字节相同 |
| T54 | 世界 10 的导出通过交付门禁；结构 9 的导出被拒 |
| T55 | 人工环境上的脚本日（两粒种子）保留全部结构断言；两次一致运行后第三份基线的摘要相等；两份历史 JSON 字节不变 |
| T56 | 风格门禁、导入图、单一来源、设计说明书汇编 `--check`、文档门禁、六个特性登记守卫与归属守卫全部通过 |

<a name="spec-rrs-机器登记"></a>
### 机器登记

- `tests/fixtures/rrs-acceptance.json`：契约点 → `{stage, spec, tests[], implementation[]}`，与上表首列一一对应、顺序相同；`spec` 是本文件的锚点；只报告的行名含「只报告」且两个列表为空。
- `tests/rrs_acceptance.py`：`STAGE_IDS = {"0" … "9"}`、`IMPLEMENTATION_STAGES = {"1" … "8"}`、`REPORT_ONLY_STAGES = {"9"}`、`STAGES_DONE`（冻结时为空，随阶段提交增长）、`FEATURE_PRODUCTION_FILES`（本特性新增或改动的生产文件）、`FEATURE_TEST_FILES`（含登记与守卫本身）、`BEHAVIOUR_CASES`（T 编号 → 测试节点）、`MUTATION_ROWS`（变异表行首列 → `{stage, clauses}`）、`MUTATIONS`（`SourceMutation` 元组，经 `tests/mutation_runner.py` 在隔离子进程里执行）、`RECEIPTS = .work/rrs/mutation-receipts`（B-18）。
- `tests/test_rrs_acceptance.py`（让「等待」成为例外守卫的同形复制）按阶段核对：登记的契约点与台账表首列一致、阶段相等；锚点存在；已完成阶段的每行都有会被收集、未被跳过、不是空壳的测试节点与解析到真实定义的实现符号；特性生产与测试文件没有延期标记与空壳函数；T 编号集合等于行为用例表首列，引用某 T 的台账行所在阶段完成时该 T 已有节点且列在该行；变异表各行与 `MUTATION_ROWS` 一一对应且顺序相同、阶段相等，已完成阶段的每行每个分句至少一个变异，每个变异名唯一、原文在所登记的定义内恰好出现一次且变异后能编译，杀手都登记在台账里；全部杀手在未变异源码上先通过，随后每个变异被杀死（退出码 1，没有收集错误）。阶段 1 提交加入按变异参数化的执行用例；阶段 8 提交加入「全部实施阶段已完成」的判定。
- `tests/fixtures/acceptance_requirements.json["resource-rule-simplification"]` 与 `tests/fixtures/current-feature-owners.json` 的同名键由 `scripts/documentation_owners.py` 核对正文归属。

<a name="spec-rrs-变异登记"></a>
## 变异登记

每个契约点至少一个改变行为的源码变异；每个变异必须被其登记的用例杀死，未杀死即补测试后重审。变异执行在全量层运行（由分层机制自动标记）。

| 变异 | 阶段 | 被谁杀死 |
| --- | --- | --- |
| 加载器接受目录食品行里的 `kcal` 字段并用它覆盖类别热量 | 1 | T5；T27 |
| `RULES_VERSION` 不符时只记警告不拒绝 | 1 | T5 |
| 清单未列出的旧文件被静默忽略 | 1 | T5 |
| `"all"` 展开到全部地点（含住宅） | 1 | T6 |
| 同类绑定不一致时把绑定扩散到另一家而不拒绝 | 1 | T6 |
| 没有绑定的 app 被接受 | 1 | T7 |
| 不连通的图被接受；自环被接受 | 1 | T9 |
| 生成器额外边数改为 n 条；长度下限改为 1 米 | 1 | T8 |
| `--check` 与 `validate --environment` 不比对重生成结果 | 1 | T9 |
| 最短路等长时取字典序较大的路径 | 1 | T10 |
| 退役占用名 `watch` 被接受 | 1 | T11 |
| 结构 9 的世界状态被接受 | 2 | T15 |
| 恢复时不核对 `environment_identity` | 2 | T16 |
| 出行时长改为向下取整；驾车车费下限去掉 | 2 | T17 |
| 目标为当前地点的出行被受理 | 2 | T18 |
| 出行事件与活动行把 `trip_id` 写成 `route_id`；活动数据丢掉 `path` | 2 | T20 |
| 外卖只在店铺与收货地相邻时受理（重新启用配送范围） | 3 | T23 |
| 送达事件在恢复后再送一次（重复送达） | 3 | T23 |
| 配送费付给 `transport:service` 而不是商家 | 3 | T23 |
| 无 app 的店铺可以经其他 app 下单 | 3 | T24 |
| 未绑定该店的 app 下单被受理 | 3 | T24 |
| 路途中给自己下外卖被受理并送到出发地 | 3 | T25 |
| 同店同食品两行被合并而不拒绝 | 3 | T26 |
| 饮品热量改为 200 千卡；分两次吃第二次重复计满一份 | 3 | T27 |
| 商品 `state` 不写类别与热量 | 3 | T28 |
| 零食计入正餐候选 | 3 | T29 |
| 到店购买行写了 app | 3 | T30 |
| 警告闭集多出 `TASTE_GENRES_UNMATCHED` | 3 | T31 |
| 指名购买把无绑定 app 当作成功来源 | 3 | T32 |
| 抽取从全部内容而不是该 app 绑定的内容里抽 | 4 | T33 |
| 空内容 app 的打开被受理并抽到别的 app 的内容 | 4 | T33 |
| 内容进度按 app 分开存（换 app 从 0 开始） | 4 | T34 |
| 看完的内容从进度池排除（不再抽到） | 4 | T34 |
| 内容结束后不抽下一条、活动空转到计划结束；暂停恢复时重抽 | 4 | T35 |
| 推送网格包含 22:00；网格按 UTC 而不是场景时区 | 4 | T36 |
| 同 session 同 app 第二次推送被允许 | 4 | T36 |
| 没有合格 app 时仍写人物可见的推送记录 | 4 | T36 |
| 打开 app 时不优先推送内容；在另一个 app 完成不解除推送 | 4 | T37 |
| session 切换后旧 session 的推送仍可命中 | 4 | T37 |
| 推送在睡眠中唤醒人物 | 4 | T37 |
| 抽取推进 `random_state`（恢复后序列不同） | 4 | T38 |
| 推送事件在提交后才弹出（恢复后重发） | 4 | T38 |
| `app_push` 进入睡眠允许集合；足迹侧词表缺 `app_push` | 4 | T39 |
| 缓解速率乘以口味因子；驾车一行不占 `use_app` | 4 | T40 |
| `app_push` 记录只写摘要前 20 字 | 4 | T41 |
| 方式表加回「游玩」→（`interact`, `play`） | 4 | T42；T11 |
| 暂停的工作活动继续累计工时；并行进食计入工时 | 5 | T44 |
| `report_status` 写「已完成」即把主题标记完成 | 5 | T45 |
| 累计满目标后活动不结束、不写完成事件 | 5 | T46 |
| 已完成的主题仍列在工具层并可再开始 | 5 | T46 |
| 阶段身份接受 `work_review` | 5 | T47 |
| 第 7 条「有 app 推送」改回「有工作可以开始做了」 | 6 | T49 |
| 第 12 条的娱乐句移到第 13 条 | 6 | T49 |
| 第 17 条改回「留意截止时间与已完成的有效工时」 | 6 | T49 |
| 工具面保留 `submit_work` | 6 | T50 |
| `app-usage.jsonl` 不写 `content_consumed` 事实；`purchases.jsonl` 丢掉 `app_id` | 7 | T52 |
| 交付门禁接受结构 9 的导出 | 7 | T54 |
| 完整日的第三份基线由单次运行写入 | 8 | T55 |
| `FOOD_CONSTANTS` 在 `life/basket.py` 里再抄一份并从那里读取 | 8 | T56（单一来源守卫与克隆检测）；T1 |

删除枚举成员属导入期失败，不登记为变异；`WakeReason` 与 `ActivityKind` 的声明次序由 T39 与 T11 的有序表守住。

<a name="spec-rrs-数据仓交接"></a>
## 数据仓交接

正式环境的重制是 WorldMock-Dataset 的工作，在数据仓分支上以它自己的提交进行，本仓库不写它的文件；本仓库交付的是环境格式合同（「环境格式」）、登记校验器与命令、人工环境的样例，以及下面的清单。

- 要产出的文件：`environment/<新标识>/catalog/{manifest.json, apps.json, locations.json, foods.json, contents.json, work_topics.json, map.json, affordances.toml, rules/motivation.toml, rules/persona-mapping.toml}`；`corpora/manifest.json` 与 `corpora/{lifestyle, scenario-contacts, scenario-mail}/`；`scenarios/{single-person, multi-person, multi-person-half-day, evaluation}.json`（结构 7，`agents[].work_topic_ids`，每个已选食品店铺的商家账户，菜单键为八个类型）；各 `requests/*` 改为从同一份环境选择地点与 app 的场景，不再各自带目录覆盖；`requests/*/inputs/**`、`runs/**`、`execution/**` 作为不可变证据保留。
- 要删除的文件：`catalog/{category-contract.json, exchange-rules.json, resources.jsonl, objects.jsonl, overrides/, rules/off-foods.mapping.toml, rules/travel-time.toml, snapshots/}`、除 `lifestyle/`、`scenario-contacts/`、`scenario-mail/` 以外的全部语料（地图、路线、供给、字幕、音轨、游戏、材料、菜谱、营养）、`environment/{videos.json, podcasts*, catalog-names.json, home-pantry.json}`（并入 `contents.json`、`foods.json` 或删除）、`environment/idle-fillers.json` 的三条刷手机填充、`vocab/content-genres.json`、`tools/environment_prepare/{storefront_build, map_prepare, traffic, fetch, corpora_cli, nasa_podcast}.py`、`tools/{prepare_map, calibrate_traffic, prepare_corpora, build_nasa_podcast, runtime_storefronts, preflight_routes, catalog_publish}.py`、`tools/catalog-snapshot-legacy-scenario.json`。
- 要改写的工具：`tools/environment_prepare/catalog.py`（作者 JSON → `worldmock.catalog.admission` 校验 → `worldmock environment generate-map` → LF 写出 → 刷新清单身份；`--check` 逐字节比对）、`tools/environment_ownership.py`（只核身份）、`tools/scenario_build.py`、`tools/environment_baseline.py`、`tools/shared_media.py`（产出 `contents.json` 行）、`tools/preflight_*.py` 与 `tools/dataset_preflight.py`（app 标识在 `apps.json`；行程边在 `map.json` 上有限；按 app 购买；没有 genre、配方与渠道）、`tools/labelkit_input.py`（`phone_rows` 接受 `source_kind == "worldmock"`，来源记录带 `environment_identity`，没有 `phone_config_sha256`）、`tools/run_day.py`（没有 `--phone-config`）、`tools/audit_sources.py`、`tools/verify_day_run.py`（推送事件不是决策）、`tools/verify_environment_ownership.py`、`tools/action_chain.py`、`tools/verify_scenario_repairs.py`。
- 手机投影：`tools/simulate_phone.py` 改为 `app-usage.jsonl`、`purchases.jsonl.app_id` 与 `delivery_arrival.orders[].app_id` 的纯投影；配置缩为 `{agent_id, timezone, scenario_path}`；`content_apps_by_id`、`game_apps_by_id`、`purchase_apps_by_seller`、`payment_app_id`、`delivery_notification_sellers`、`scheduled_sessions`、`activity_apps`、`message_apps_by_recipient` 全部删除；app 名只从 `apps.json` 查；LabelKit-DataSet 的 `phone_event` 载荷随之改形。
- 校验命令与通过条件：`worldmock validate --environment environment/<新标识>` 退出码 0；`tools/environment_prepare/catalog.py --check` 与 `worldmock environment generate-map --check` 都报告逐字节相同；数据仓的 `tests/zhangjiang/*` 重写为登记往返、重生成字节稳定、每条内容至少绑定一个 app、同类菜单相同、图连通、每个场景能以安装的引擎加载；`verification.json`、`assertion-transfer.json` 重新生成。
- 数量与真实名称（B-17，由数据仓测试判定）：八个地点类型各至少一个，餐馆至少两家且至少一家绑定 app、至少一家不绑定；app 至少四个并用真实名称（外卖类与内容类各至少一个）；食品至少三十种、每类至少五种、价格为合成分值；内容至少五十条、每类至少五条，标题用真实作品名（电视剧按集、带 `series`），摘要按 `summary_source` 标记真实（带 `url`）或合成；工作主题至少四个，分配给某人物的主题目标工时之和不少于该人物在实验窗内班次总工时；地图种子登记在 `map.json`；真实名称不代表对真实平台的任何承诺，README 写明。
- 本仓库登记为未执行、等发布后补的回执：全量层 `python scripts/run_tests.py --tier full --workers 6 --environment-root ../WorldMock-Dataset/environment/<新标识>` 与 `scripts/coverage_gate.py --from-json`（`docs/dev/coverage-report.json`、`docs/dev/test-durations.json` 重录）、POSIX 与 macOS 的完整日与换目录恢复回执、`docs/CONTRACTS.md#contracts-环境数据归属合同` 与手册里的环境路径。发布前这些行在实现状态里写「未执行」，不写成通过。

<a name="spec-rrs-实施阶段"></a>
## 实施阶段与提交

隔离分支上先跑通最小生活闭环再补齐；每个阶段的提交前以针对文件的 pytest 运行该阶段改动与新增的测试文件、直接相关的测试文件与六个登记守卫，设计汇编 `--check`；阶段 1–7 的中间提交允许五个既有特性守卫、`tests/test_layering_acceptance.py` 与 `tests/test_current_feature_owners.py` 暂红，阶段 8 提交必须全绿；正式切换不暴露半套新协议。SYNTHESIS 的工作包按下表映射到阶段。

| 阶段 | 内容（工作包） | 拥有的文件 | 提交 |
| --- | --- | --- | --- |
| 0 冻结 | 本规格；INDEX 登记并把提案改 `已裁决`；登记守卫与空的阶段集合；层次规格的 147 守卫放宽（P0） | `docs/dev/SPEC-resource-and-rule-simplification.md`、`docs/INDEX.md`、`docs/dev/ANALYSIS-spec-coverage.md`、`docs/dev/SPEC-layering-and-test-tiers.md`、`tests/fixtures/rrs-acceptance.json`、`tests/rrs_acceptance.py`、`tests/test_rrs_acceptance.py`、`tests/fixtures/acceptance_requirements.json`、`tests/fixtures/current-feature-owners.json`、`tests/test_current_feature_owners.py`、`tests/test_layering_acceptance.py` | `docs:` |
| 1 固定规则与环境格式 | `exchange` 包与基础模型迁移（P1）；目录注册表、生成器、图查询、登记错误、人工环境、`environment generate-map` 与 `validate --environment`、删除 `categories` 与品类编译链（P2）；阶段 1 文档 | `src/worldmock/exchange/*`、`src/worldmock/base_models.py`、`src/worldmock/resources/models.py`、`src/worldmock/catalog/{models, loader, admission, identity, mapgen, graph, errors, affordances}.py`、`src/worldmock/cli.py`、`src/worldmock/validation.py`、`src/worldmock/data/affordances.toml`（删旧类型）、`src/worldmock/corpora/`（删 `storefront.py`）、`scripts/check_reachability.py`（删）、`tests/exchange/*`、`tests/catalog/*`、`tests/fixtures/environment/**`、`tests/frozen_inputs.py`、`tests/test_frozen_inputs.py`、`tests/test_single_source.py`、`tests/test_import_graph.py`、`tests/test_environment_profiles.py`、`tests/corpora/*`、阶段 1 表的文档 | `feat(exchange):`、`feat(catalog):`、`docs:` |
| 2 地图与出行 | 场景 7、世界 10、运行身份与加载链（P3）；图上出行、寻路、出行证据（P4）；阶段 2 文档 | `src/worldmock/life/{scenario, models, world_setup, engine_context, run_identity, semantics, load_checks, readable, travel, wayfinding, discovery, observe, tool_layer, motivation, activity_validation, session_actions, time_advance}.py`（各自的出行与结构部分）、删 `life/{map_models, map_data, travel_time}.py`、`src/worldmock/runtime.py`、`src/worldmock/run_setup.py`（清单 3）、`src/worldmock/profile/injection.py`（路线警告）、`src/worldmock/evaluation/{public_evidence, review}.py`、`scripts/inspect_run.py`、对应测试与阶段 2 表的文档 | `feat(life):`、`docs:` |
| 3 店铺、食品与外卖 | 菜单、购买、外卖、app 来源（P5）；三类食品与身体（P6）；画像与警告（P11）；阶段 3 文档 | `src/worldmock/life/{basket, purchase_offers, named_purchase, order_routing, commerce, storefront_runtime, giving, recipients, request_linking, routing, name_resolution, world_query, callable_index, projection, admission, apps, body, sleep, activity_evidence, activities, vocabulary, interaction_menu, guidance, error_contract}.py`、删 `life/{opening_hours, storefront_state, inventory_recipe, category_contract}.py`、`life/state_commit.py`、`scheduling/{events, compile, effects}.py`（删供给类）、`footprints/{purchases, experiences, quality}.py`（购买与进食部分）、`evaluation/behavior_metrics.py`、`profile/{coverage_targets, coverage, injection}.py`、删 `profile/{coverage_apps, taste}.py`、对应测试与阶段 3 表的文档 | `feat(life):`、`feat(profile):`、`docs:` |
| 4 娱乐与推送 | app 使用与内容（P7）；推送与节律（P8）；TextWorld 退出；阶段 4 文档 | `src/worldmock/life/{models, rules, content, activities, time_advance, session_actions, durations, candidates, presentation, rotation, relevance, retrieval, named_catalog, readable, vocabulary, object_labels, activity_titles, activity_validation, settled_text, result_projection, observe, tool_layer, admission, schedule, rhythm, progress_layer, app_push}.py`、删 `life/game.py`、`scheduling/{events, compile, effects}.py`（`app_push`）、`footprints/models.py`、`data/affordances.toml`（加 `use_app`）、`catalog/affordances.py`（八个名）、`profile/coverage_targets.py`、`pyproject.toml` 与锁文件、`scripts/bootstrap_environment.py`、`tools/run-viewer.html`（唤醒标签）、对应测试与阶段 4 表的文档 | `feat(life):`、`feat(scheduling):`、`chore(deps):`、`docs:` |
| 5 工作主题 | 主题工时与叙述、评审链删除（P9）；阶段 5 文档 | `src/worldmock/life/{work, rules, time_advance, session_actions, activity_control, action_evidence, decision_coordinator, result_projection, scenario, observe, day_plan_inputs, day_planning, memory_layer, settled_text, felt, business_feedback, engine}.py`、删 `life/{work_review, work_review_runtime}.py`、`custom/envs/life_env.py`、`custom/agents/life_agent.py`（`submit_work` 校验）、`src/worldmock/run_setup.py`（阶段身份）、`model_feedback.py`、`evaluation/{workload_metrics, public_evidence}.py`、`footprints/quality.py`（工作节）、`scripts/inspect_run.py`、`src/worldmock/opslog.py`、对应测试与阶段 5 表的文档 | `feat(life):`、`feat(host):`、`docs:` |
| 6 观察、工具面与提示 | 系统层、工具描述、Schema、查看器（P10）；阶段 6 文档 | `custom/agents/{life_prompt, life_memory}.py`、`src/worldmock/life/{prompt_schemas, narration, tool_groups}.py`、`tools/run-viewer*.{html, js, css}`、`tests/fixtures/viewer-context.json`、`docs/CONTRACTS.md`（`contracts-model-request` 与常量同一提交）、对应测试与阶段 6 表的文档 | `feat(prompt):`、`docs:` |
| 7 记录、导出与查看器 | 导出、校验、质量报告、交付、评估、渲染（P12）；阶段 7 文档 | `src/worldmock/footprints/{export, validation, quality, experiences, timeline_labels}.py`、`src/worldmock/delivery/{pipeline, windows}.py`、`src/worldmock/evaluation/{adapters, public_evidence, behavior_metrics, review, scenario}.py`、`scripts/render_day.py`、`tools/run-viewer*`、`tests/fixtures/dayrun-biz-timeline.json`、对应测试与阶段 7 表的文档 | `feat(footprints):`、`feat(delivery):`、`docs:` |
| 8 文档与登记收口 | 完整日、台账、基础设施（P13）；全部文档收口与设计汇编（P14）；登记守卫加「全部实施阶段已完成」的判定；在该提交上执行标准层、变异审查与文档、汇编门禁 | `tests/integration/test_dayrun_complete_days.py`、`tests/fixtures/dayrun-resource-rule-baseline.json`、全部 `tests/*_acceptance.py` 与其夹具、`tests/fixtures/{layering-acceptance, layering-public-api, acceptance_requirements, current-feature-owners}.json`、`scripts/fresh_check.py`、`docs/dev/test-durations.json`、`docs/dev/implementation-status.md`、`docs/VERIFICATION.md`、`docs/INDEX.md`、`docs/dev/ANALYSIS-spec-coverage.md`、`docs/design/README.md`、`docs/design/SUMMARY.md`、`docs/design/worldmock-design.html`、`README.md`、手册 | `test:`、`docs:` |
| 9 数据仓交接与完整回归 | 数据仓重制与发布（P15，数据仓分支的提交）；发布后在本仓库以 `--environment-root` 运行全量层、覆盖率门禁与完整日，取 POSIX、macOS 与换目录恢复回执，登记进实现状态、覆盖率基线与 INDEX | 本仓库只改 `docs/dev/implementation-status.md`、`docs/dev/coverage-baseline.md`、`docs/dev/coverage-report.json`、`docs/dev/test-durations.json`、`docs/INDEX.md`、`docs/CONTRACTS.md`（环境路径）、手册的路径 | 数据仓：`feat(environment):`；本仓库：`docs:` |

P1 与 P2 可并行（先定枚举名）；P3 等 P1、P2；P4、P5、P6、P11 在 P3 之后并行（`basket.py` 按两半、`tool_layer.py` 按函数分工）；P7、P8、P9 在 P3 之后并行（`life/models.py` 与 `rules.py` 按节分工），P10 最后整合；P12 跟随 P5–P9 的载荷形状；P13 收阶段 8；P15 收阶段 9。

<a name="spec-rrs-门禁与平台"></a>
## 门禁与平台

迭代用针对文件的 pytest；合并前跑标准层；特性验收跑全量层与覆盖率门禁（分支覆盖率不低于 85 %、未覆盖公共接口为零）；另有风格门禁（新函数带中文文档字符串、不超过 50 行、5 个参数、120 列，文件不超过 2000 行，克隆检测禁止第二份枚举或常数表）、导入图与单一来源守卫、文档门禁、设计汇编核对。不用延期标记。

- 本机（Windows）：每个阶段以针对文件与节点的 pytest 执行该阶段登记的节点及其所在与直接相关的测试文件；阶段 4 删除 TextWorld 之后，`scripts/run_tests.py` 的快速、标准与全量层在本机都能完成收集（T42 核对），之后的阶段在本机另以标准层作为本机回归证据；文档门禁与字节相关的检查在 `core.autocrlf=false` 的检出里执行（本工作树即是）。
- 第三份完整日基线按 C-7 在本机由两次一致运行生成并记录平台；POSIX 与 macOS 的完整日、全量层、覆盖率门禁与换目录恢复回执在本机不执行，由阶段 9 取得；取得之前实现状态与验收记录写「未执行」，不从历史结果推定通过；完整放行需要两个平台的证据（`docs/dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-门禁可选部件与操作系统`）。
- 带 `--environment-root` 的全量层以数据仓重制后的环境为输入，在它发布之前不能运行；`docs/dev/coverage-report.json` 与 `docs/dev/test-durations.json` 在阶段 9 重录。
- 变异审查：`tests/rrs_acceptance.py` 的全部变异在全量层执行并各自被杀死；跳到别的平台或改用历史回执都不算。
- 文档门禁、设计汇编 `--check`、`tests/test_dayrun_fidelity_acceptance.py` 的现行文档临时标记检查对每个文档提交成立。

<a name="spec-rrs-第1轮审查的阻断发现"></a>
## 第 1 轮审查的阻断发现

2026-10-06 对本文件初稿做了三个视角的对抗审查。下列阻断发现照审查报告原文登记（只去掉了链接、行号与跨文件锚点的写法），每一条都要在冻结前修订本文件或改写所指的条款，并在复审中确认关闭；与「裁决记录」冲突的发现，保留裁决并补一行裁决说明理由。

**视角：与现行冻结规格、合同与设计的冲突（15 项）**

**冲突-B1　阶段归属与指纹口径自相矛盾（阶段 4–6 不可测）**

现行事实：`custom/agents/life_agent.py 中的 life_tool_schema_sha256` 绑定 `_TOOL_MODELS` 的全部 Schema（含 `REQUEST_MODELS["submit_work"]`）与注册表字节；`custom/agents/life_prompt.py 中的 life_prompt_sha256` 绑定 `LAYER_SCHEMAS`（`prompt_schemas.py`）；`tests/integration/test_tool_description_references.py 中的 test_every_native_tool_has_a_purpose_only_description` 要求 `set(_PARAMETER_MODELS) == set(TOOL_DESCRIPTIONS)`；`tests/life/test_prompt_layers.py` 用 `validate_layer` 核对真实观察的工具层、进度层；`tests/integration/test_seven_layer_request.py 中的 test_prompt_fingerprint_binds_the_callable_index_schema` 钉 `callable_index` 四键 `{lifestyle, contents, goods, query_categories}`。

规格里的矛盾：

1. 「删除清单 · 生产符号」阶段 5 行删除 `models.REQUEST_MODELS["submit_work"]`、`prompt_schemas.TARGET_TOOLS 的 submit_work`、`_RESULT.work_review`、`life_prompt.TOOL_DESCRIPTIONS["submit_work"]`，T47（阶段 5）要求「没有 `submit_work` 工具与模型」；但「实施阶段与提交」把 `custom/agents/life_prompt.py` 与 `src/worldmock/life/prompt_schemas.py` 划给阶段 6，`src/worldmock/life/models.py` 只划给阶段 2、4。
2. B-19 说 `tool_schema_sha256` 只在阶段 1、4、6 变、`prompt_sha256` 只在阶段 6 变。可是删 `REQUEST_MODELS["submit_work"]`（阶段 5）必变工具指纹；阶段 4 工具层开始出现 `kind: "app"` 目标、`callable_index` 去掉 `contents`、进度层出现 `app_push` 记录、结果出现 `details.app_content`，阶段 5 出现 `contract` 四键、`last_report`、`work_completed`，这些都在 `LAYER_SCHEMAS` 里——`prompt_schemas.py` 若按表留到阶段 6，`test_prompt_layers.py` 对真实观察的校验从阶段 4 起必红，而它不在允许暂红的清单（五个特性守卫、`test_layering_acceptance.py`、`test_current_feature_owners.py`）里；若 `prompt_schemas.py` 随阶段 4、5 改，`prompt_sha256` 就不止在阶段 6 变。
3. 阶段 5 删了 `submit_work` 工具而系统层第 2 条（阶段 6 才改）仍列出 `submit_work`，模型会被告知一个不存在的工具。

修正（择一，推荐 a）：
- a. 把 `prompt_schemas.py` 的结构部分（`TARGET_KINDS` +app −content −game −catalog、`_CALLABLE_INDEX` 三键、进度记录类型 +`app_push` −`task_deadline`、`_RESULT` +`app_content`）划给阶段 4，（`_CONTRACT` 四键、活动目标 `last_report`、+`work_completed`、−`work_review`、`TARGET_TOOLS` −`submit_work`、`REQUEST_MODELS` −`submit_work`、`TOOL_DESCRIPTIONS` −`submit_work`、系统层第 2 条）划给阶段 5；阶段 6 只剩第 7、8、12、13、17 条与查看器。B-19 改写为：「`tool_schema_sha256` 在阶段 1、4、5 各变一次（阶段 6 不变）；`prompt_sha256` 在阶段 4、5、6 各变一次」。阶段 5 文件清单补 `src/worldmock/life/models.py`、`src/worldmock/life/prompt_schemas.py`、`custom/agents/life_prompt.py`（第 2 条与 `TOOL_DESCRIPTIONS`）；阶段 4 文件清单补 `src/worldmock/life/prompt_schemas.py`；`tests/life/test_prompt_layers.py`、`tests/integration/test_seven_layer_request.py 中的 test_prompt_fingerprint_binds_the_callable_index_schema`、`test_tool_description_references.py` 相应移到阶段 4/5 的测试行。
- b. 保持 B-19，把 T47 的「没有 `submit_work` 工具与模型」移到 T50（阶段 6），阶段 5 只删评审链与引擎侧 `submit_work` 处理，并在「门禁与平台」写明阶段 4–5 的观察不满足 `LAYER_SCHEMAS`、`test_prompt_layers.py` 暂红——这与「正式切换不暴露半套新协议」相悖，不推荐。

**冲突-B2　D-50「R4 逐字保留」与同时做两件事规格的现行条款冲突**

`docs/dev/SPEC-parallel-activities.md` 裁决 R4、`spec-parallel-activities-名额落点`（「`_report_refusal` 在工作截止之后、占用冲突之前调用」）、`spec-parallel-activities-测试`（「恢复的检查次序（工作截止先于名额…）」）、`docs/design/04-simulation-engine.md「simulation-engine-校验与占用安排」`（「依次核对开始地点、工作截止与合同、空名额与占用冲突」）与 `tests/life/test_paused_slots.py` 模块文档都写「工作截止与约束」。规格 D-50 说「恢复次序（地点 → 主题上限 → 名额 → 占用）逐字保留」，但第二步的文字必然改变，而前言只说 R5、R6 由本规格裁决记录取代。

修正：D-50 定稿改为「恢复次序四步逐字保留，第二步『工作截止与约束』改为『主题上限』」；前言「同时做两件事规格的 R5、R6」改为「R4、R5、R6」；阶段 5 表加两行：`docs/dev/SPEC-parallel-activities.md「spec-parallel-activities-名额落点」`（`_report_refusal` 在主题上限之后、占用冲突之前）与 `docs/design/04-simulation-engine.md「simulation-engine-校验与占用安排」`（恢复次序第二步为主题上限；去掉 `channel_id`、可见凭据、游戏、观看、资格的说法）；阶段 4 表的 SPEC-parallel-activities 行把 `spec-parallel-activities-丙第8条` 写成正式表行（锚点保留，正文注明第 8 条定稿由本规格「系统层」取代、约束条不变），并加 `spec-parallel-activities-测试` 行（判据文字「工作截止先于名额」改「主题上限先于名额」、「观看占用断言」改「app 使用占用断言」）。

**冲突-B3　生活运行时规格遗漏的冲突节**

以下节含被删条款，规格各表都没有引用：

| 锚点 | 冲突内容 | 建议行 |
| --- | --- | --- |
| `docs/dev/SPEC-life-runtime.md「spec-life-runtime-验收矩阵」` | 41 处：TextWorld、`channel_id`、`delivery_route`、`location_kinds`、`opening_*`、segment、`work_review`、下限缓解、可见期、字幕、库存、截止、报价、曝光、浏览、游戏、评审、购买渠道、资格、预约 | 阶段 8：矩阵行按本规格各节改写或删除（SYNTHESIS §2 已点名「`spec-life-runtime-验收矩阵` rows」） |
| `#spec-life-runtime-两条排除纪律` | 可排除原因列「营业关闭或未知、资格不足、无有效报价、库存为零或已下架」；软偏好一段以 `compute_taste_weights`、`attention_prior` 为例 | 阶段 3：可排除的世界事实只剩地点不可用、地点类交集为空、资源占用与时长；软偏好一段改为「画像只以散文进入人设层，不进入排序键」 |
| `#spec-life-runtime-状态归属规则` | 世界行「营业与工作日历」；工作行「任务、输入材料、截止时间、交付物、验收、合同及付款状态」；内容接触行「游戏状态及素材区间」 | 阶段 5（工作行）、阶段 4（内容接触行、世界行） |
| `#tool-interaction-工具请求-开始活动` | 方式表含「制作」行（输出归本人库存）；「不得提交…报价版本」 | 阶段 3：删制作行、去掉报价版本；阶段 4 加「打开」行 |
| `#tool-interaction-工具请求-只读查询` | 「报价未知、营业未知的条目也被滤掉」「标签由现有品类树和内容类别生成」 | 阶段 3：`available_only` 只按在场或店铺绑定 app 判定；标签 = 食品类别标签 |
| `#spec-life-runtime-加速` | 「人物选的媒体倍速与程序加速分开记录」 | 阶段 4：删媒体倍速一句（`playback_speed` 删除） |
| `#spec-life-runtime-结算间隔与续行` | 「游戏沿用明确的回合反馈边界」 | 阶段 4：改为 app 使用的内容完成是静默边界（`next_interaction_at`） |
| `#spec-life-runtime-不进入提示的字段` | 「`game_catalog` 只在人物在家且询问娱乐时以名称列表出现」 | 阶段 4：删除该句 |
| `#spec-life-runtime-社交最小可行版` | 「可交谈判定为对方清醒且不在主动游戏」 | 阶段 4：改为「对方清醒」 |
| `#time-and-energy-决策不耗时时间只在做事时流动-同一秒内的呈现与刚才` | 「本秒首轮回灌的浏览结果，在后续轮次中继续置顶」「对象只因世界事实（库存、营业、地点、在场）变化而离开窗口」 | 阶段 3/4：删浏览置顶；世界事实只剩地点与在场 |
| `#spec-life-runtime-资源表达` | 「工作需要：截止时间、合同、任务与经济目标」 | 阶段 5：改为主题与目标工时 |

**冲突-B4　人物决策与画像规格遗漏的冲突节**

| 锚点 | 冲突内容 | 建议行 |
| --- | --- | --- |
| `docs/dev/SPEC-life-realism.md「spec-life-realism-location_kinds-的运行期强制语义」` | 整节：`location_kinds` 必须来自地点目录的显式非空声明；分区责任交给内容条目的题材、语言与时长段 | 阶段 1：地点的活动环境由 `kind` 经 `PLACE_KIND_ENVIRONMENTS` 派生；条目侧 `location_kinds` 不变；内容分区一段删除（D-28） |
| `#persona-prompt-七层请求-工具层的紧凑编码与即时查询` | 「报价或营业未知的也滤掉」「不公开超权限报价或世界库存」「不产生可见凭据」 | 阶段 3 |
| `#spec-life-realism-呈现与导出` | 「生活活动、观看、游戏、工作…都有标题，种类名取 readable.py 的中文种类表」 | 阶段 4：改为 app 使用、工作等十个类型 |
| `#spec-life-realism-与相邻规格的边界` | 边界表「商品可见期、材料与字幕增量」 | 阶段 4 |
| `#spec-life-realism-单一行动阶段` | 「不由人格特征、口味权重或动机评分推导」 | 阶段 3：删「口味权重」 |

**冲突-B5　数字人交互规格遗漏的冲突节**

| 锚点 | 冲突内容 | 建议行 |
| --- | --- | --- |
| `docs/dev/SPEC-agent-interaction.md「spec-agent-interaction-会话与活动的关系」` | 「工作活动的恢复与开工受同一截止约束：恢复的计划时长取距截止与合同上限减已累计中的较小值」 | 阶段 5：改为距主题目标与 `DERIVED_DURATION_CAP_SECONDS` 的较小值 |
| `#spec-agent-interaction-上下文分层` | 进度层 `task_deadline` 记录、「截止时刻只在工具层」、「报价版本」、不看到 `route_id`/`supply_id`/`product_id`、工作评审 | 阶段 4/5：记录类型按 B-4；禁键按「状态、事件、检查点与导出」 |
| `#spec-agent-interaction-会话字段` | 「分段区间」「截止」 | 阶段 4/5 |
| `#spec-agent-interaction-即时查询完整纠错与紧凑呈现` | 「不公开超权限报价或世界库存，不替代耗时浏览的揭示行为」 | 阶段 3 |
| `#spec-agent-interaction-与既有文档的一致性核对` | 「工作评审结果」「冻结报价」「游戏命令回路删除」行 | 阶段 5/8 |

**冲突-B6　决策上下文规格遗漏的冲突节**

`docs/dev/SPEC-decision-context.md「spec-decision-context-地点发现与供给比较」`（供给报价、路线预计时间、报价版本、轮转与可见期、渠道称呼）、`#spec-decision-context-即时查询操作备忘与紧凑结果`（浏览揭示、未知世界库存）、`#spec-decision-context-预算与失败`（「材料揭示与商品可见期遵循原合同」）、`#spec-decision-context-同秒续接与只读路径`（无来源事件的 `task_deadline`）。建议阶段 3（前三者）与阶段 5（`task_deadline` 删除）各加一行。

**冲突-B7　交互菜单规格遗漏的冲突节**

`docs/dev/SPEC-interaction-menu.md「errands-workday-查询筛选与返回精简-只看此刻能做的」`（「报价未知、营业未知的条目也被滤掉」「配送不通时就是空页」）、`#errands-workday-查询筛选与返回精简-执行位置与保留约束`（「不产生可见凭据」）、`#spec-interaction-menu-验收台账`（「游戏与工作动作正确区分」「工作拒绝不调用评审」「他人私有库存不泄露」）。建议阶段 3 与阶段 5 各加一行。

**冲突-B8　当日目标规格遗漏的冲突节**

`docs/dev/SPEC-day-plan-goals.md「time-and-energy-零点计划与计划提醒-计划与提醒」` 明写「运行清单的阶段键为 `action`、`work_review` 两个」（与 D-48 直接冲突）；`#spec-day-plan-goals-当前工具交互约束`（「普通工作从通知送达即开放，预约活动才有约定开始时刻」）；`#spec-day-plan-goals-心智模型与范围`（「环境任务截止与营业变化 → 提供决策机会」）。建议阶段 5 加三行。

**冲突-B9　让「等待」成为例外规格：遗漏的节与台账行**

1. `docs/dev/SPEC-idle-fillers.md「spec-idle-fillers-系统层」`（A）给出第 7 条定稿全文「…有工作可以开始做了…」并以「任务开放」「回合制游戏只能在自己的交互边界上暂停或结束」作为引擎依据。它不是裁决原文，而是与本规格「系统层」第 7 条并存的第二份定稿。应在阶段 6 表加一行：锚点保留，注明第 7 条定稿由本规格「系统层」取代（「有工作可以开始做了」→「有 app 推送」），引擎依据的「任务开放」改「app 推送」、删游戏一句。
2. `#spec-idle-fillers-班次边界同步点` 第 5 项是 `tests/life/test_work_contracts.py` 的有序成员表（阶段 5 删除该文件）；第 9 项要求新增成员时核对第 7 条。规格「删除清单」只改了台账（[IF] 同步点 5），没有改这一节。应在阶段 4 表加一行（第 5 项改指 `tests/life/test_wake_reasons.py`；写明本规格按第 9 项核对了第 7 条）。
3. `#spec-idle-fillers-班次边界情形`「回合制游戏」行与验收台账 T10、T12（`tests/life/test_shift_edge_runs.py 中的 test_t10_a_game_turn_after_the_edge_does_not_hide_the_edge_from_the_clock`、`::test_t12_in_a_game_the_turn_stays_and_the_game_only_stops_at_its_own_turn`，用 `_start_game` 开真实游戏）在阶段 4 失去前提。规格的「删除清单 · 测试节点」与「测试与夹具」都没有点名这两个节点和 `tests/fixtures/idle-fillers-acceptance.json` 的对应行。应补：T10、T12 改用 `use_app` 的内容完成边界（`next_interaction_at`）重写并同步登记 [IF]，`班次边界情形` 的「回合制游戏」行改为「app 使用的内容完成边界」。

**冲突-B10　接口合同遗漏的冲突节**

| 锚点 | 冲突内容 | 建议行 |
| --- | --- | --- |
| `docs/CONTRACTS.md「contracts-交互菜单声明与有效菜单」` | 「推进游戏、发送消息或调用工作验收」「与候选资格使用的 `location_kinds`…」 | 阶段 2（键为八个 `LocationKind`、动作取新 `START_ACTIVITY_ACTIONS`）与阶段 5（删工作验收） |
| `#contracts-day-goal-request` | 家族映射表「娱乐 \| `watch`, `play_game`, `browse`, `lifestyle:hobby`」 | 阶段 4：`use_app`, `lifestyle:hobby`（与 `spec-day-plan-goals-度量输出合同` 同一提交） |
| `#contracts-交谈与通话` | 「对方可交谈（对方清醒且未在主动游戏）」「可与观看、播客等其他种类的活动并行」 | 阶段 4 |
| `#contracts-个人事务` | 「取得」来源列「制作产出入库（活动进度的库存转换输出）」 | 阶段 3：删该项（与 `errands-workday-个人事务-完成依据与结算` 同一提交） |

**冲突-B11　设计章节遗漏的冲突节（SYNTHESIS §2 多数已点名）**

| 锚点 | 冲突内容 |
| --- | --- |
| `docs/design/04-simulation-engine.md「simulation-engine-校验与占用安排」` | 见 B2；另含 `channel_id`、可见凭据、库存、游戏、观看、资格 |
| `docs/design/04-simulation-engine.md「simulation-engine-验收重点」` | 「工作通知」行（`task_available`、预约、关联邮件）、「唤醒」行「睡眠与游戏遵守各自边界」、「班次不改变任务开放与截止」 |
| `docs/design/04-simulation-engine.md「simulation-engine-调用并等待人物决策」` | 「报价未知、营业未知的也被滤掉」「不刷新世界或浏览凭据」 |
| `docs/design/05-recording-engine.md「recording-engine-工具纠错只读查询与当天操作备忘的证据」` | `appointment`、`available_at`、`RuntimeWorkTask`、截止、技能、素材、`contract.opens` |
| `docs/design/05-recording-engine.md「recording-engine-事件字段」` | 观察事件字段 `content_refs`、`material_ranges` |
| `docs/design/09-io-formats.md「io-formats-职责与边界」` | 链接 `contracts-品类契约与目录字段`（正文删除）、`category-contract.json` 与 `exchange-rules.json` 为独立冻结文件、库存输出绑定 |
| `docs/design/00-overview.md「overview-地图中的消费行为」` | 「比较同一商品在不同门店的报价和道路路程」「当前报价、库存及营业例外由模拟引擎维护」 |
| `docs/design/00-overview.md「overview-非功能约束」` | 平台行「完整放行还须合并 Linux x86_64 TextWorld 语料成功证据」 |
| `docs/design/01-architecture.md「architecture-状态归属与写入边界」` | 「按供给报价与库存、营业例外」 |
| `docs/design/02-exchange-engine.md「exchange-engine-返回规则结果」` | 「制作结果必须列明真实消耗…`inventory/item`」「读取…报价事实」 |
| `docs/design/03-resource-engine.md「resource-engine-职责约定」`、`#resource-engine-准入失败的处理`、`#resource-engine-资源校验的实现与验收` | 「所需内容品类及引用」「品类与可达性准入」「静态可达性 `life/retrieval.py` 检索维度」「不以曝光率…」 |
| `docs/design/07-evaluation.md「evaluation-活动activitysession」` | `segments` 分段区间字段 |

各行按所属阶段加入对应表（阶段 2/3/4/5），改写语义按本规格对应节。

**冲突-B12　手册遗漏的冲突节**

| 锚点 | 冲突内容 |
| --- | --- |
| `docs/manual/rules.md「rules-活动声明」` | `relief.channel` 的 `preference_rate`/`content_rate` 公式（有效偏好、接触次数、口味权重）、`resource_claims` 十一个名、`game_boundary`、`attention` 与 `attention_prior`、`content_kinds` |
| `docs/manual/rules.md「rules-单个人物的参数」` | `aspiration.daily_effective_work_seconds`、`attention_prior` 示例 |
| `docs/manual/rules.md「rules-人物映射表」` | 「接入人物卡片和口味权重」 |
| `docs/manual/scenarios.md「scenarios-修改人物身体与账户」` | `entertainment_relief_preference_floor` 整段与口味权重缓解公式 |
| `docs/manual/personas.md「personas-在场景中填写人物」` | 示例含 `skill_levels`、`qualifications`、「任务技能」 |

建议分别加入阶段 3（口味、下限、技能、资格）、阶段 4（活动声明、占用名、注意力）、阶段 5（`aspiration`，见 B13）。

**冲突-B13　动机层的有效工时目标缺口未裁决**

基线 `src/worldmock/life/motivation.py` 以 `profile.aspiration["daily_effective_work_seconds"]`、`MotivationContext.total_effective_seconds`、`CandidateEntry.goal_effective_seconds` 与 `_work_efficiency_snapshot` 计算工作目标缺口；`src/worldmock/life/scenario.py` 要求 `motivation.aspiration` 恰好是 `daily_effective_work_seconds`。规格 W1/D10 删 `effective_work_seconds` 与效率因子，「场景结构 7」又写 `motivation` 只减 `attention_prior`，对这个键与公式只字未提——实现者无法决定是保留名字改口径、还是改名或删除。

修正：在「本规格补定」加一条（例如 B-21）：`aspiration.daily_effective_work_seconds` 改名 `daily_work_seconds`（或保留键名）并以实际工作秒数计目标缺口；`MotivationContext.total_effective_seconds` → `total_work_seconds`、`CandidateEntry.goal_effective_seconds` → `goal_work_seconds`、`_work_efficiency_snapshot` 删除；场景结构 7 的「保留」句同步；`rules-单个人物的参数` 行写进阶段 5 表；T43/T44 补一句「动机层目标缺口按实际工作秒数」。

**冲突-B14　引用了不存在的测试节点**

「测试与夹具」阶段 6 行写「`test_progress_schema_registers_the_nine_record_types` 改为新计数」。基线 `tests/life/test_prompt_layers.py` 里没有这个节点，实际是 `test_progress_schema_registers_the_eight_record_types_with_both_errand_records`。按原名改写并给出新计数（八 − `task_deadline` + `app_push` + `work_completed` = 九）。

**冲突-B15　其他现行文档遗漏的冲突节**

| 锚点 | 冲突内容 | 建议阶段 |
| --- | --- | --- |
| `docs/VERIFICATION.md「verification-真实性评估与结论边界」` | 「工作截止事件只证明截止时间到达，已验收付款任务仍保留其真实完成状态」「游戏终局读取实际 `done`」 | 5 |
| `docs/VERIFICATION.md「verification-共享规则与可重建环境特性门禁」` | 「Linux x86_64 的 TextWorld 语料验收必须另外成功」 | 4 |
| `docs/ARCHITECTURE.md「architecture-必须达到的行为」` | 「职业、技能、工作机会和合同影响产出或报酬」「覆盖饮食、节目、游戏、工作记录…」 | 5 |
| `docs/dev/SPEC-layering-and-test-tiers.md「layering-imports」` | 导入图行「`models` 与 `map_models` 从同一处导入」「工作材料校验」 | 1/5 |
| `docs/dev/SPEC-layering-and-test-tiers.md「layering-tiers」` | 「需要…TextWorld 执行的用例标为 external」 | 4 |
| `docs/dev/SPEC-shared-rules-and-rebuildable-env.md「shared-rules-文档原子同步清单」` | 00-overview 行的 TextWorld 门禁；`SPEC-category-contract.md` 链接 | 1/4 |

**视角：需求与裁决覆盖（11 项）**

**覆盖-B1　阶段划分与删除清单、文件归属、夹具和台账互相矛盾；阶段 1 结束时仓库不能加载任何场景**

- 「实施阶段与提交」把场景 7、世界 10、运行身份与加载链（SYNTHESIS P3）放在阶段 2，阶段 1 拥有的文件只有 `exchange/*`、`catalog/*`、`cli.py`、`validation.py`、`data/affordances.toml`、`corpora/`、`scripts/check_reachability.py` 与测试、夹具。
- 但「删除清单 › 生产符号」把几十个 `worldmock.life.*`、`scheduling.*`、`profile.*`、`validation`、`runtime`、`run_setup` 符号标为阶段 1（travel、wayfinding、load_checks、models、state_commit、semantics、scenario、world_setup、engine_context、motivation、candidates、activities、basket、admission、order_routing、named_purchase、purchase_offers、session_actions、vocabulary、time_advance、observe、tool_layer、callable_index、world_query、retrieval、run_identity …），阶段 1 却不拥有这些文件。`life/semantics.py`、`life/category_contract.py` 等直接导入 `worldmock.categories`，阶段 1 删除 `categories/` 而不动它们，包无法导入。
- 「测试与夹具」阶段 1 把三份场景夹具改成结构 7，而 `LifeScenario.schema_version` 的 `Literal[7]` 在阶段 2；T13（阶段 1）要求 `validate --environment` 「加载 scenarios/ 下每个场景并跑场景加载检查」；SYNTHESIS §5.2(5) 要求 `tests/integration/test_external_environment_lifecycle.py`（[QT]）从阶段 1 起通过。三者在阶段 1 都不可能成立。
- 验收台账「环境登记与错误码 | 1 | T3、T4、T7、T13」引用 T4「覆盖注册校验与错误全表」与 T13，而全表含场景级码 `WORK_TOPIC_UNKNOWN`、`MERCHANT_ACCOUNT_MISSING`、`WORK_SCHEDULE_REQUIRED` 与 `ENVIRONMENT_REF_UNKNOWN` 的「场景引用」半句，这些在阶段 2 之前没有定义；同一行还曾引用 T14（阶段 2 的结构 7 用例）。守卫「引用某 T 的台账行所在阶段完成时该 T 已有节点」会在阶段 1 收口时失败或迫使阶段 2 工作提前。

修复（任选其一，全文一致）：
1. 采用 SYNTHESIS 的分组：阶段 1 = P1 + P2 + P3（固定规则、环境格式、场景 7、世界 10、运行身份、加载链，一并清掉对 `worldmock.categories` 的全部导入），阶段 2 = P4（地图与出行），阶段 3 = P5 + P6 + P11；「实施阶段」阶段 1 的文件列表补 `life/{scenario, models, world_setup, engine_context, run_identity, semantics, load_checks, readable, environment_layer}.py`、`runtime.py`、`run_setup.py`；验收台账把「场景 7 与世界 10 的拒绝边界」改为阶段 1（T14–T16）；删除清单里标阶段 1 的 `life.*` 行按文件归属改标 1/2/3；文档改写表的「阶段 2（地图与出行；场景 7、世界 10）」标题随改。
2. 或者保留现行阶段表，但把「测试与夹具」阶段 1 的「三份场景改结构 7」、T13 的场景加载半句、T4 的三个场景级码移到阶段 2，并在「实施阶段与提交」写明阶段 1 只能以目录层 API 验证、`test_external_environment_lifecycle.py` 在阶段 2 恢复——这与「隔离分支上先跑通最小生活闭环」及 SYNTHESIS §5.2(5) 冲突，不推荐。

**覆盖-B2　唤醒原因与预排类型的删除阶段早于其来源的删除阶段**

- 「删除清单 › 生产符号」阶段 4 行：`WakeReason.TASK_AVAILABLE`、`TASK_DUE_SOON`、`WORK_REVIEW_RESULT`、`LOCATION_HOURS_CHANGE` 与 `WAKE_REASON_NAMES` 对应行；「错误码与唤醒、预排名字」阶段 4 行：预排 `work_available`、`task_expired`。
- 但产生 `work_review_result` 的 `work_review_runtime.py`、产生 `task_available`/`task_due_soon` 的 `rhythm.py`/`schedule.py` 任务输入、`scheduling.events::WORK_AVAILABLE/TASK_EXPIRED` 与 `compile_schedule` 的任务循环，都列在阶段 5 行（同一张表）。阶段 4 删掉枚举成员而阶段 5 才删引用它们的代码，阶段 4 提交导入期失败；`ScheduledKind` 的两个成员在两张表里分属阶段 4 与 5，自相矛盾。
- T39（22 个名字）登记在阶段 4 契约点「时间窗口精确」，与上面同样依赖工作链先退出。

修复：把 `task_available`、`task_due_soon`、`work_review_result` 的删除、`work_available`/`task_expired` 的删除、`rhythm`/`schedule` 的任务输入删除统一到阶段 5（与工作链同一提交），阶段 4 只做「加 `app_push`、删 `location_hours_change`（来源已在阶段 1/3 删除）」；T39 改写为「阶段 4 核对 23 个（25 − 1 + 1），阶段 5 核对 22 个」或把 T39 整体移到阶段 5 的「工作面与阶段身份」行；闭集表 `WakeReason` 行加一句分阶段说明。若不愿拆成两步，则把阶段 4 与 5 合并为一个阶段（SYNTHESIS 原本把 P7、P8、P9 放在同一阶段）。

**覆盖-B3　业务拒绝码没有唯一定义处；B-5 引用的「活动受理拒绝码」表在本文件里不存在**

- B-5：「新错误码的英文原因与下一步见「注册校验与错误」与「活动受理拒绝码」两张表」。本文件只有前者；后者只出现在文档改写表里（指 `docs/CONTRACTS.md「contracts-活动受理拒绝码」`），不是本规格的定义。
- `APP_NOT_BOUND`（D-35 新增）在「店铺、食品与外卖」只写了触发条件，没有英文原因、下一步与 `unmet` 值；B-14 的 `unmet` 闭集含 `delivery_app`，全文没有任何码使用它（SYNTHESIS §4.8 把它配给 `APP_NOT_BOUND`，但规格未写）。
- `WORK_TOPIC_NOT_FOUND` 的原因与下一步写在「删除清单」改名列；`APP_CONTENT_EMPTY` 在「娱乐」节；`ORDER_DUPLICATE_ITEM` 在「店铺」节；`already_at_destination` 在 B-11；`FULFILLMENT_NOT_SUPPORTED` 的新原因在「店铺」节与 S12 行。定义散在五处，违反本规格自己写的「本节与「环境格式」「状态、事件、检查点与导出」是…错误码…的唯一定义处」。
- 场景 `environment{environment_id, environment_version}` 与清单不符（T14「environment 钉与清单不符拒绝」）没有码：`ENVIRONMENT_IDENTITY_MISMATCH` 的条件只写文件摘要与 `catalog_content_sha256`。
- `ENVIRONMENT_ID_DUPLICATE`（「app 名与地点显示名经 channel_key 归一后冲突」）与 `APP_NAME_COLLIDES_LOCATION`（「app 名与地点名归一后相同」）条件重叠，T4「每个登记码一个最小反例、码与英文原因相符」无法确定抛哪个。

修复：在「固定规则与常数」下新增小节「活动受理拒绝码」（锚点 `spec-rrs-活动受理拒绝码`），一张表列出本特性新增、改名、改义的全部业务码：`码 | 条件 | 英文原因 | 下一步 | unmet`——`APP_CONTENT_EMPTY`（"This app has no content to use." / "Choose another app."）、`APP_NOT_BOUND`（"This store cannot be ordered through this app." / "Order through an app bound to this store, or go to the store." / `delivery_app`）、`FULFILLMENT_NOT_SUPPORTED`（"This store has no delivery app." / "Go to <store>."）、`WORK_TOPIC_NOT_FOUND`（现有文字）、`ORDER_DUPLICATE_ITEM`（现有文字）、`ACTIVITY_PRECONDITION_UNMET` 的三个 `unmet`（`required_location_id` 现行句、`delivery_destination` "Place the order before departing or after arriving."、`already_at_destination` "You are already at this place."）、`ACTIVITY_DURATION_INVALID`（D-50 句）、`SUPPLY_NOT_AT_STORE`/`SUPPLY_SOURCE_MISMATCH`/`SOURCE_NOT_FOUND`/`SUPPLY_NOT_FOUND`（含义改到食品与店铺的一句）；B-5、B-11、B-14、「店铺」「娱乐」「工作」「删除清单」各处只引用它。注册表补 `ENVIRONMENT_PIN_MISMATCH`（"The scenario pins an environment that is not the one being loaded."）或把它并入 `ENVIRONMENT_IDENTITY_MISMATCH` 的条件；`ENVIRONMENT_ID_DUPLICATE` 条件改为「同一文件内标识或显示名重复」，跨文件 app 名与地点名冲突只归 `APP_NAME_COLLIDES_LOCATION`。

**覆盖-B4　`BODY_CONSTANTS` 的数值没有定稿，「抄自今天的 `resources.jsonl`」有两份不同的来源**

- 常数表只给出 `maximum_energy_kcal = 2400`、`basal_kcal_per_hour = 65`、`hunger 800`、`emergency 400`，其余写「各自单位 … 数值逐项抄自今天的 `resources.jsonl`，不改值」。
- 仓库里有两份：`tests/fixtures/environment/catalog/resources.jsonl`（basal 70、全部 MET 1.0、`awake_sleep_debt_per_hour` 0.1、`sleep_recovery_per_hour` 1.0、`entertainment_need_per_awake_hour` 0.1、效率曲线恒 1.0）与数据仓 `environment/zhangjiang-baseline/catalog/resources.jsonl`（basal 65、MET wait 1.3 / sleep 0.95 / work 1.5 / watch 1.0 / play_game 1.3 / read_mail 1.3 / browse 1.3 / consume 1.5 / driving 1.5 / walking 3.5、债 1 / 恢复 2、需要 0.08）。65 只与数据仓相符，说明写的是数据仓，但规格没点名；两份都会在阶段 1 删除，之后「今天的」无处可查。
- 新活动类型 `use_app` 的 MET 没有任何来源（watch 1.0 还是 browse 1.3）；`chat`、`call` 的 MET 键也未列。
- 这些值进入 T1（OWNERS 单一来源）、T27（净能量）、T44（疲劳）与完整日基线，缺值即不可实现、不可测。

修复：常数表把 `BODY_CONSTANTS` 展开成逐键行：`basal_kcal_per_hour = 65`、`activity_met = {wait: 1.3, sleep: 0.95, work: 1.5, use_app: 1.0, read_mail: 1.3, consume: 1.5, chat: <值>, call: <值>, travel:walking: 3.5, travel:driving: 1.5}`（生活活动用条目自身 `met_value`）、`awake_sleep_debt_per_hour = 1`、`sleep_recovery_per_hour = 2`、`entertainment_need_per_awake_hour = 0.08`、`maximum_energy_kcal = 2400`、`hunger_threshold_kcal = 800`、`emergency_threshold_kcal = 400`，依据列写「抄自 WorldMock-Dataset `environment/zhangjiang-baseline/catalog/resources.jsonl`（数据仓 `2cff671`）；`use_app` 取 `watch` 的 1.0，实验假设」；`chat`/`call` 按今天 `social.py`/注册表的实际 MET 键写值；登记到 `docs/EXPERIMENT.md「experiment-实验假设声明」`。

**覆盖-B5　人工环境无法构造 T24 的 `SUPPLY_NOT_AT_STORE` 分支（T32 的同类分支同样）**

- B-7/「人工环境」：美团绑定 `noodle-1`（牛肉面、薯片、柠檬茶）与 `cafe`（三明治、薯片、柠檬茶）。四种食品都在某家美团店铺的菜单上。
- T24 要求「经美团买三明治以外的、美团店铺不卖的食品 → `SUPPLY_NOT_AT_STORE`」——不存在这样的食品。T32「写无绑定的 app → `SOURCE_NOT_FOUND` 或 `SUPPLY_NOT_AT_STORE`」同理；变异行「指名购买把无绑定 app 当作成功来源」也需要它。

修复：`catalog/foods.json` 加第五种食品，例如 `rice-ball` 饭团（meal，1000 分，`[store]`，只在无 app 的四季便利出售），B-7 改「五种食品」，`manifest.json` 与 README 的计数随改；T24 改为「经美团买饭团 → `SUPPLY_NOT_AT_STORE`」，T32 的分支同样点名饭团。

**覆盖-B6　B-19 的指纹阶段断言与现行代码不符**

- B-19：`tool_schema_sha256` 只在阶段 1、4、6 变；`prompt_sha256` 只在阶段 6 变。「观察与工具面 › 指纹」与阶段 2、6 的文档改写行都重复了它。
- 现行 `custom/agents/life_prompt.py 中的 life_prompt_sha256` 绑定两份系统层与 `prompt_schemas.LAYER_SCHEMAS`；`src/worldmock/life/prompt_schemas.py`  `WAY_LABELS = tuple(way for way in WAYS if way != "购买")` 直接读 `vocabulary.WAYS`，`TARGET_KINDS`、`LIFE_VERBS` 等同样是闭集的活引用（模块头注明「Schema 不维护字面副本」）。阶段 3 删 `WAYS["制作"]`、阶段 4 删观看类方式加「打开」、阶段 5 删 `submit_work` 请求模型，每一步都改变 `prompt_sha256`。
- `custom/agents/life_agent.py 中的 life_tool_schema_sha256` 绑定 `_TOOL_MODELS` 的全部工具 Schema 加注册表字节；阶段 5 删 `submit_work` 即改变 `tool_schema_sha256`，不在 B-19 的「1、4、6」里。

修复：B-19 改为「`tool_schema_sha256` 在改变 `affordances.toml` 字节或 `_TOOL_MODELS` 的每个阶段变（1、4、5）；`prompt_sha256` 在改变系统层文本或七层 Schema 所读闭集（方式、目标类、活动类型、进度记录类型、请求模型）的每个阶段变（3、4、5、6）；`code_sha256` 每个代码阶段都变；没有测试或文档钉字面值，只有落地后的最终值进入运行清单」；「观察与工具面 › 指纹」、阶段 2 与阶段 6 文档改写行、`docs/CONTRACTS.md「contracts-seven-layer-schema」` 的分阶段叙述同句改写。

**覆盖-B7　闭集表的 `WAYS` 行漏掉八个保留方式**

- 闭集表：`WAYS` = 到店、远程下单、购买、打开、食用、使用、给予、进行（8 个）。
- 现行 `src/worldmock/life/vocabulary.py 中的 WAYS` 还有 步行、驾车（`travel_to`）、工作、交谈、通话、阅读、等待、睡觉；同表 `START_ACTIVITY_ACTIONS` 仍含 `travel_to`、`work`、`chat`、`call`、`read`、`wait`、`sleep`，「地图与出行」的地点目标动作写「步行、驾车」。按字面实现 `WAYS` 行会删掉出行、工作、交谈、通话、读信、等待、睡觉的方式，与全文矛盾。

修复：`WAYS` 行改为十六个：步行、驾车、工作、交谈、通话、阅读、进行、等待、睡觉、到店、远程下单、购买、打开、食用、使用、给予；变化列保持「删 观看、接着看、从头再看、接着听、从头再听、制作、浏览、游玩；加 打开→（`interact`, `use_app`）」；补一句同义表：「查看」只剩 {阅读}，「做饭」「看」「收看」「听」「收听」「玩」「逛」「逛逛」「继续看」「重看」「重听」等随被删方式一起删除。

**覆盖-B8　本规格声称取代的三条冻结裁决没有在裁决记录里登记**

- 「现行文档原位改写」前言：「同时做两件事规格的 R5、R6 与让「等待」成为例外规格的 R12 由本规格的裁决记录取代，不回改」。裁决记录（五张表）里没有任何一行写到 SPEC-parallel-activities R5（驾车一行「只能再听播客或和人说话」）、R6（退役名清单）或 SPEC-idle-fillers R12（「环境不替人物选择内容」）。SYNTHESIS §2 也要求把它们记在新规格的裁决记录里。
- 另外 SPEC-idle-fillers D7「不改动钉死 147 个变异名的守卫」被 D-47（守卫放宽为 登记 ∩ 基线）取代，同样未登记。

修复：「本规格补定」加三行：B-21「同时做两件事规格 R5 的驾车一行改为只能和人说话（播客随 `watch` 退出），R6 的退役名清单加 `watch`、`screen`、`play_game`、`browse`；该规格的乙表与丙文由本规格「娱乐 › 占用」与「系统层」第 8 条取代」；B-22「让「等待」成为例外规格 R12「环境不替人物选择内容」改为「环境在人物所选 app 内选内容」，等待仍始终列出、照常受理；D7 的 147 守卫由 D-47 放宽」；依据列写「本规格补定；SYNTHESIS §2」。

**覆盖-B9　提案原话「本表不删除其他现有生活服务和非食品用品」被静默改变**

- 提案第二章：「本表不删除其他现有生活服务和非食品用品；它们使用系统固定类型，保留生活所需行为，退出通用品类继承和交换配对」。
- 规格：R-E「非食品商品与原料退出目录」、F6「耐用品退出目录；`initial_inventory` 只装食品」、「三类食品与进食 › 删除：…耐用品使用」。R-E 的依据写「并入 D7、「环境格式」」，D7 是做饭，与耐用品无关；没有一行说明提案这句被谁、为什么取代。用户采纳的是提案全部推荐，提案本身把非食品用品写为保留。

修复：R-E 行的依据改为「取代提案第二章「本表不删除…非食品用品」的后半句：SYNTHESIS §4.1 的五类目录没有耐用品，持有物只有食品；生活活动（生活服务）照旧保留在 `corpora/lifestyle/items.json`」，并在「用户得到什么 › 不做的事」加「不保留耐用品与非食品商品」。若裁决方不接受这一改变，则需另定 `goods.json` 之类的第六类目录——那是更大的范围变化，应由用户裁决而不是由规格默认。

**覆盖-B10　场景 7 把 `buyer` 写成 `accounts[]` 的声明角色，与现行隐式人物账户矛盾**

- 「场景结构 7 › 新增」：「`accounts[]` 的角色 `buyer`、`merchant:<location_id>`…」；同节末尾「不设隐式账户：卖家、雇主、交通与外部账户都是场景声明」。
- 现行 `src/worldmock/life/scenario.py` ：「人物账户（buyer/traveler/worker 三个能力角色）由 agents[] 隐式声明」，`agents[].initial_balance_fen` 建人物账户；人工环境的 `accounts[]` 今天只有 employer、transport、merchant、external。规格「不做的事」又写「不改变…账本…的机制」。要求场景显式声明 `buyer` 账户是未经裁决的机制改变，且 T14「结构 7 受理」与夹具描述（账户 `buyer`、`merchant:noodle-1`…）会按此实现。

修复：「场景结构 7」改为「`accounts[]` 只声明 `merchant:<location_id>`（每个已选食品店铺一个）、`employer:*`、`transport:service`、`external:scenario`；人物账户仍由 `agents[]` 的 `initial_balance_fen` 隐式建立，角色 `buyer`/`traveler`/`worker` 不变」；「人工环境」三份场景的账户清单去掉 `buyer`。

**覆盖-B11　`ENVIRONMENT_NUMBER_INVALID` 的条件与 `work_topics.json` 的 `target_hours: 正数` 矛盾**

- 目录文件表：`target_hours: 正数`，`target_seconds = round(target_hours × 3600)`（允许 2.5）。
- 注册码表：`ENVIRONMENT_NUMBER_INVALID`——「`price_fen`、`duration_seconds`、`target_hours`、`length_m`、`seed` 不是有限正数或不是整数」，把 `target_hours` 也要求为整数；`seed` 又被要求为正数，而生成器种子只需是整数。T4 的反例无法确定 2.5 小时是受理还是拒绝。

修复：条件改为「`price_fen`、`duration_seconds`、`length_m` 不是正整数；`target_hours` 不是有限正数；`seed` 不是整数」；英文原因改为 "A declared number is outside its allowed domain."。

**视角：可实现性、可测试性与门禁合规（8 项）**

**实现-B1　阶段 1 删除的整文件被阶段 2–5 的文件导入：阶段 1 提交后引擎不可导入，T13、[QT] 与 `test_import_graph.py` 无法通过**

阶段表把 `src/worldmock/categories/`、`life/map_models.py`、`map_data.py`、`travel_time.py`、`opening_hours.py`、`storefront_state.py`、`inventory_recipe.py`、`category_contract.py`、`corpora/storefront.py`、`profile/coverage_apps.py`、`profile/taste.py` 的删除放在阶段 1，而它们在基线的导入方（`rg` 核对）归阶段 2–5：

- `worldmock.categories` ← `life/semantics.py`、`world_setup.py`（阶段 2）、`basket.py`、`session_actions.py`、`tool_layer.py`（阶段 3/4/5）
- `life.opening_hours` ← `scheduling/effects.py`、`basket.py`、`discovery.py`、`motivation.py`、`presentation.py`、`rhythm.py`、`tool_layer.py`
- `life.storefront_state` ← `engine_context.py`、`world_setup.py`、`semantics.py`、`basket.py`、`purchase_offers.py`、`tool_layer.py`、`effects.py`
- `life.inventory_recipe` ← `observe.py`、`session_actions.py`、`time_advance.py`、`tool_layer.py`
- `life.map_models` ← `scenario.py`、`semantics.py`、`motivation.py`、`order_routing.py`；`profile.coverage_apps` ← `run_setup.py`、`coverage_targets.py`、`load_checks.py`；`profile.taste` ← `run_setup.py`、`profile/__init__.py`

后果：阶段 1 提交后 `import worldmock.life`、`worldmock.scheduling.effects`、`worldmock.run_setup`、`worldmock.validation`（`validation.py` 导入 `world_setup`）全部 ImportError。T13（`worldmock validate --environment` 要「加载 `scenarios/` 下每个场景」，走 `cli.py validate_project`）、T11 的包资源核对经 `catalog` 尚可，但阶段 1 拥有的 `tests/test_import_graph.py`、`tests/test_environment_profiles.py` 与 `[QT] tests/integration/test_external_environment_lifecycle.py`（SYNTHESIS §7 P2 明示「must keep passing on the new fixture from stage 1」）都不可能绿；同时阶段 1 把三份夹具场景改成结构 7，而接受结构 7 的 `life/scenario.py` 归阶段 2，T3/T13 在阶段 1 的「人工环境」实际是加载器拒绝的环境。阶段表声明只允许「五个既有特性守卫、layering、owners 暂红」，没有覆盖这一状态。

修复（二选一，建议前者）：
1. 把 SYNTHESIS §7 的 P3 并回阶段 1（与 §7 一致）：阶段 1 另拥有 `src/worldmock/life/{scenario, models（结构与 DeliveryState 部分）, world_setup, engine_context, run_identity, semantics, load_checks}.py`、`src/worldmock/runtime.py`、`src/worldmock/validation.py`、`src/worldmock/run_setup.py`（清单 3、口味注入删除），并在同一提交对 `basket.py`、`rhythm.py`、`scheduling/effects.py`、`discovery.py`、`motivation.py`、`presentation.py`、`purchase_offers.py`、`order_routing.py`、`observe.py`、`session_actions.py`、`time_advance.py`、`tool_layer.py`、`profile/coverage_targets.py` 只做「删除对被删模块的导入与调用」的最小改动（阶段表写明「阶段 1 只删导入与调用，行为改写仍归各自阶段」）；契约点「场景 7 与世界 10 的拒绝边界」（T14–T16）与 `tests/integration/test_external_environment_lifecycle.py` 改为阶段 1；阶段 2 只剩 P4。
2. 或者把每个整文件删除挪到删除其最后一个导入方的阶段（`categories/` 与 `map_*`、`travel_time` → 阶段 2；`opening_hours`、`storefront_state`、`inventory_recipe`、`category_contract` → 阶段 3；`coverage_apps`、`taste` → 阶段 3），并同步改「删除清单·生产模块」表的阶段列与 `ENVIRONMENT_STALE_FILE` 的生效阶段。

**实现-B2　闭集成员的删除阶段与其最后消费者的删除阶段不一致（WakeReason、ScheduledKind、环境事件类型、`location_hours_change` 的来源）**

- 「错误码与唤醒、预排名字」表与「生产符号」表都把 `WakeReason.TASK_AVAILABLE/TASK_DUE_SOON/WORK_REVIEW_RESULT/LOCATION_HOURS_CHANGE`、预排 `work_available/task_expired`、`environment_event.data.kind` 的 `work_available/task_expired` 放在阶段 4；但它们的消费者 `schedule.py`（`_clock_reasons`）、`rhythm.py,514`、`WakeSnapshot.task_newly_visible/next_task_due_at/work_review_returned`、`scheduling/compile.py` 任务循环、`effects._reveal_event` 全部列在阶段 5 的符号行。阶段 4 提交删除枚举成员即 AttributeError。
- `LOCATION_HOURS_CHANGE` 的来源 `rhythm._closing_at/_just_opened/_location_change_times`、`WakeSnapshot.location_closing_at`、`NextDecisionQuery.location_hours_change_times` 列在阶段 5，而 `rhythm.py` 导入的 `opening_hours.py` 在阶段 1 删除（与 B1 同源）。
- T39「唤醒词表 22 个」登记在阶段 4，而按上述归属 22 只能在阶段 5 达到（阶段 1 删 1、阶段 4 加 1、阶段 5 删 3）。

修复：把四个 WakeReason 成员中的三个任务类成员、两个 ScheduledKind 成员、两个环境事件类型改到阶段 5 行（与任务循环同一提交）；把 `WakeReason.LOCATION_HOURS_CHANGE`、`rhythm._closing_at/_just_opened/_location_change_times/_warning_signatures["closing"]`、`WakeSnapshot.location_closing_at/location_just_opened`、`NextDecisionQuery.location_hours_change_times/location_closing_at`、`schedule.CLOSING_WARNING_SECONDS` 改到阶段 1 行（与 `opening_hours.py` 同一提交）；「闭集」表 `WakeReason` 行补「阶段 1 为 24、阶段 4 为 25（加 `app_push`）、阶段 5 为 22」；T39 与「足迹侧词表、事件字段用例、查看器计数一致」改登记为阶段 5 的契约点「唤醒词表 22」（「时间窗口精确」只留 T36），`tests/life/test_wake_reasons.py`、`tests/footprints/test_event_fields.py`、`tests/tools/run_viewer*.test.cjs` 的 22 计数改写挪到阶段 5 的测试行；D-23 的「九个同步点」注明在阶段 4（加）与阶段 5（删）各执行一次。

**实现-B3　推送与打开的关联规则自相矛盾：「只有 `pending` 才取推送内容」与「提前停止后重开仍命中」不能同时成立**

「娱乐·活动生命周期」：「当前 session 的 `app_pushes[app_id]` 为 `pending` 时取推送内容（`pinned_by_push_id`，状态置 `opened`）」。「推送与消费的关联」与 T37：「中途停止、本 session 再打开仍从该内容剩余部分继续（仍是 pending→opened 的同一条推送）」。首次打开后状态已是 `opened`，再打开按生命周期规则会走随机抽取，T37 必失败；按关联规则实现则生命周期句为假。两处都在「定义」层面，实现者无法判定。

修复：生命周期句改为「当前 session 的 `app_pushes[app_id]` 存在且 `status != released` 时取推送内容（首次打开把 `pending` 置 `opened`，再次打开沿用 `opened`，`pinned_by_push_id` 都写该 `push_id`）」；关联句括注改为「（同一条推送，状态自首次打开起为 `opened`）」；质量报告 `opened_in_session` 定义为「状态曾置 `opened`」。

**实现-B4　`APP_NOT_BOUND` 没有英文原因与下一步；B-5 指向的「活动受理拒绝码」表在本规格里不存在；B-14 的 `unmet = delivery_app` 没有任何规则使用**

B-5 写「新错误码的英文原因与下一步见「注册校验与错误」与「活动受理拒绝码」两张表」，全文只有前一张表（登记码）。`APP_NOT_BOUND`（D-35、外卖、T24）全文没有原因句；`ACTIVITY_PRECONDITION_UNMET` 的 `unmet = delivery_app`（B-14）没有触发规则——而 D-35 把 `APP_NOT_BOUND` 定成独立码，SYNTHESIS §4.8 又写成 `APP_NOT_BOUND … / unmet delivery_app`，规格两种写法并存。`tests/life/test_error_contract.py 中的 test_every_business_rejection_code_raised_in_the_source_has_a_specific_reason` 要求每个抛出的码有专属原因，T24 要断言原因，现状不可测。

修复：在「店铺、食品与外卖」末尾新增 `<a name="spec-rrs-活动受理拒绝码"></a>### 活动受理拒绝码` 表（码 | 条件 | 英文原因 | 下一步），至少含：`APP_NOT_BOUND` ｜ 远程下单的 app 不在 `apps_by_store[store_id]` ｜ "This store cannot be ordered through that app." ｜ "Order through <app bound to the store>."（店铺无 app 时走 `FULFILLMENT_NOT_SUPPORTED`）；`APP_CONTENT_EMPTY` ｜ "This app has no content to use." ｜ "Choose another app."；`FULFILLMENT_NOT_SUPPORTED` ｜ "This store has no delivery app." ｜ "Go to <store>."；`ACTIVITY_PRECONDITION_UNMET`（`delivery_destination`、`already_at_destination`、`required_location_id` 三句）；`ACTIVITY_DURATION_INVALID`（见 B5）；`WORK_TOPIC_NOT_FOUND`、`ORDER_DUPLICATE_ITEM`（把现在散在各节的句子集中并保留原处引用）。B-14 删去 `delivery_app`（或改写为「`APP_NOT_BOUND` 不写 `data.unmet`」）。

**实现-B5　D-50 / B-6 的主题不足 60 秒拒绝：句子是新句而非「保留」，下一步未定义，钉旧句的测试与合同行未列入阶段 5**

基线 `work.py,109` 的两句是「Less than one minute remains before the deadline; work cannot start.」「…remains in the contract; …」，下一步「Submit the completed work.」（`docs/CONTRACTS.md「contracts-活动受理拒绝码」` `ACTIVITY_DURATION_INVALID` 行）。规格 D-50 写「`work_start_refusal` 保留 "Less than one minute of the topic remains; work cannot start."」——这是替换；新下一步没有定义（旧下一步随 `submit_work` 消失）；钉旧句的 `tests/life/test_grounded_observation.py`（参数化 `contract`/`deadline`）与 `tests/life/test_paused_slots.py` 不在阶段 5 的测试行，合同 `ACTIVITY_DURATION_INVALID` 行不在阶段 5 的文档行。

修复：D-50 改「`work_start_refusal` 的原因改为 "Less than one minute of the topic remains; work cannot start."，下一步 "Choose another activity."，取代今天的截止句与合同句」；阶段 5 测试行加 `tests/life/test_grounded_observation.py`（参数化改为单一 `topic` 句）；阶段 5 文档行 `docs/CONTRACTS.md「contracts-活动受理拒绝码」` 补「`ACTIVITY_DURATION_INVALID` 工作分句改主题剩余与新下一步」。

**实现-B6　登记守卫「同形复制」读不到变异表：`section_text(…, "spec-rrs-验收台账")` 到下一个 `##` 为止，而变异表在独立的 `## 变异登记`**

`scripts/documentation_refs.section_text` 取到「下一同级或更高标题为止」（本机核对：验收台账一节含「行为用例」「机器登记」两个 `###`，不含 `| 变异 | 阶段 | 被谁杀死 |`）。`tests/test_idle_fillers_acceptance.py 中的 _spec_table(MUTATION_HEADER)` 在台账一节内解包唯一一张表，复制到 `tests/test_rrs_acceptance.py` 会在 `test_mutation_rows_match_the_spec_table_and_cover_done_stages` 处 ValueError。规格「机器登记」没有说明这一差异。

修复：「机器登记」第三条补「守卫以两个锚点常量读表：契约点表与行为用例表取 `spec-rrs-验收台账`，变异表取 `spec-rrs-变异登记`（`section_text` 到下一同级标题为止，变异登记是独立的二级节）」；或把变异表改成「验收台账」下的 `### 变异登记`（锚点不变）。

**实现-B7　「闭集」表的 `WAYS` 行只列 8 个方式，而基线 `vocabulary.WAYS` 保留的还有 步行、驾车、工作、交谈、通话、阅读、等待、睡觉**

本节自称「闭集…的唯一定义处，其余各节只引用」；按该行实现会删掉八个仍在用的方式（`START_ACTIVITY_ACTIONS` 行却保留了它们的动作）。

修复：`WAYS` 行成员改为「步行、驾车、工作、交谈、通话、阅读、进行、等待、睡觉、到店、远程下单、购买、打开、食用、使用、给予（16 个）」，变化列不变；`ACTION_WAYS` 补 `use_app → 打开`、删 `browse/watch/play`。

**实现-B8　基线不存在的测试名与路径被当作既有定义引用**

- 「测试与夹具」阶段 6：`test_progress_schema_registers_the_nine_record_types` 不存在；基线是 `tests/life/test_prompt_layers.py 中的 test_progress_schema_registers_the_eight_record_types_with_both_errand_records`。
- 「测试节点（文件保留）」阶段 4：`tests/life/test_observation_agent_seam.py` 不存在；节点 `test_real_game_observation_starts_visible_game_and_restores_activity` 在 `tests/integration/test_observation_agent_seam.py`。

修复：两处改为上述真实名称与路径（R9：不引用不存在的定义）。

<a name="spec-rrs-已知局限"></a>
## 已知局限

- 常数都是实验假设（C-1 … C-5）：步行、驾车、配送速度，备餐与配送费，三类食品的热量与时长，app 使用时长，按类型的缓解速率；效果看正式环境上的复跑，本规格不承诺行为指标。
- 推送作为唤醒原因每人每日最多增加 14 次决策机会；需要时可降为只记录不唤醒，不改状态结构（D-16）。
- 同类同菜单意味着同类型店铺的差异只在 app 绑定与位置；要不同菜单只能用不同类型。
- 内容只有摘要与时长，没有正文、字幕或逐段素材；模型看到的是摘要，不是作品本身。
- 工作叙述是模型文字，不改变世界事实；工时只数活动秒数，疲劳只经身体消耗体现。
- 推送、打开与接续的随机序列绑定 `random_state` 与固定键；改变键格式会改变序列，属 `RULES_VERSION` 范围。
- 数据仓的正式环境重制与 LabelKit-DataSet 的 `phone_event` 改形不在本仓库完成，完整回归在它们发布后才有回执。

<a name="spec-rrs-版本表"></a>
## 版本表

| 版本 | 日期 | 修改 |
| --- | --- | --- |
| `2026-10-06-rrs-draft` | 2026-10-06 | 初稿：采纳提案十个决策点的全部推荐（D1–D10），记录综合决定 D-11 … D-52、最终覆盖 C-1 … C-8、两份早期备忘的处置与本规格补定 B-1 … B-20；写全固定规则与常数、环境格式、各主题规则、删除清单、各阶段的文档改写、测试与夹具、验收台账、变异登记、数据仓交接、实施阶段与门禁口径 |
| `2026-10-06-rrs-review-r1` | 2026-10-06 | 第 1 轮对抗审查（冲突、覆盖、可实现性三个视角）发现 34 项阻断问题，原文登记在「第 1 轮审查的阻断发现」；状态改为未冻结草稿，修订从下一会话开始 |
