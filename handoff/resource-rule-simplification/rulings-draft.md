# Draft rulings for the resource-and-rule simplification spec (orchestrator memo, 2026-10-06)

Status: draft input for the spec. Proposal decision table: adopt every recommended option (same pattern as the
previous freezes: "采纳提案全部推荐"). Additional rulings below fill gaps the proposal leaves open.

## Proposal decision table (adopt recommended)

| # | Decision | Ruling |
| --- | --- | --- |
| D1 | 地图是否全连通 | 全连通：随机生成树 + 少量额外边；≥2 地点；无自环、无重边；正整数米 |
| D2 | 同类店铺的食品绑定 | 精确逐店绑定，注册校验同类型店铺菜单完全一致；`all` 展开到全部食品店铺类型的地点 |
| D3 | session 含义 | 复用现有地点停留 / 路途 session（`SessionState`），不新建 app session |
| D4 | 推送频率 | 当地时间 08:00–21:00 每个整点，每人至多一条；同一 session 同一 app 至多一条；均匀随机选一个合格 app，再均匀随机选其内容 |
| D5 | 外卖时间 | 固定备餐时间 + 店铺到收货地最短路长度 ÷ 固定配送速度，向上取整到秒 |
| D6 | 营业与供给 | 不设库存、不设营业时间、不设报价版本：店铺全天服务、商品持续供应；删除 opening calendars / exceptions / quotes / stock / price_change / supply_quote_change / product_release |
| D7 | 做饭 | 删除做饭、食材、配方（`制作` 方式、`inventory_recipe`、`recipe_output_bindings`、`ingredient`） |
| D8 | 食品常数 | 零食 200 kcal / 5 min，饮品 100 kcal / 3 min，正餐 600 kcal / 20 min（每份）；登记为实验假设 |
| D9 | 内容进度与重看 | 跨 app 共享进度（按 content_id）、允许重看；完成后可重新抽到 |
| D10 | 工作工时 | 实际处于工作活动的秒数累计；效率不再作为完成门槛；目标工时达到即完成 |

## Additional rulings (gaps)

- R-A 价格：每个食品一个 `price_fen`（每份，正整数分），全店同价；外卖费 = 商品价 + 系统固定配送费 `DELIVERY_FEE_FEN`（登记实验假设）。
- R-B 速度与时间常数（WorldMock 固定，登记实验假设）：步行 `WALKING_METERS_PER_SECOND`、驾车 `DRIVING_METERS_PER_SECOND`、配送 `DELIVERY_METERS_PER_SECOND`、备餐 `DELIVERY_PREPARATION_SECONDS`；出行耗时 = 最短路长度 ÷ 速度，向上取整到秒；同地点不出行；交通费沿用闭集算子（驾车最低 500 分 + 0.2 分/米；步行 0）但常数归 WorldMock。
- R-C 地图生成规则（WorldMock 固定）：按稳定排序的地点标识生成随机标号树（seed），再加 ⌊(n−1)/2⌋ 条不重复、非自环的随机边，长度为 [LENGTH_MIN, LENGTH_MAX] 的随机整数米；发布为无向加权图文件；加载核对「发布图 == 生成规则(地点清单, seed) 的输出」且连通、无孤立点、正长度；运行与恢复只读图。
- R-D 地点：字段只保留 `location_id`、`name`、可选 `branch_label`、`kind`（PlaceKind 闭集：home、office、restaurant、cafe、convenience_store、supermarket、fitness、park）、`location_kinds`（活动环境闭集，既有，生活活动仍用）、`apps`（app_id 列表，只有食品店铺类型可非空）。删除坐标、`business_kind`、品牌、营业声明、`required_qualifications`、`affordances`、逐地点 source。食品店铺类型：restaurant、cafe、convenience_store、supermarket；home/office/fitness/park 不售食品。`merchant`、`wet_market` 删除。
- R-E 非食品商品（durable）与原料：退出目录。五类目录（地点、食品、娱乐内容、工作主题、app）是闭集；生活活动（lifestyle items）保留。
- R-F app 目录：`apps.json` `[{app_id, name}]`；店铺→app 写在地点上；内容→app 写在内容上；食品→店铺写在食品上（`stores: [location_id] | "all"`）。反向索引由注册派生。
- R-G 内容目录：`[{content_id, title, kind ∈ {article, image_text, short_video, long_video, live}, summary, summary_origin ∈ {real, synthetic}, duration_seconds, apps}]`；电视剧按集登记（title 含集数）；实况为有限时长片段。不再要求字幕、音轨、分段。
- R-H 工作主题：场景字段 `work_topics: [{topic_id, topic, target_hours, assignee_agent_id, opens_at, due_at}]` 取代 `tasks`；删除 input/oracle、`submit_work`、独立评审、返工与任务付款；工资沿用 `payrolls`；`WORK_AVAILABLE` 事件沿用开放；完成 = 累计实际工作秒数 ≥ 目标工时；叙述 = 工作活动的 `report_status`/`pause`/`conclude` summary（已有机制），工具层工作活动目标的 `state` 写主题、已做、剩余；系统层第 17 条改写为按主题描述做了什么。
- R-I 娱乐：新活动类型 `app_use`（方式「打开」，内部动作 `use_app`），目标为 app（工具层 kind `app`），派生时长 `APP_USE_SECONDS` 固定；活动内按「推送优先，其余均匀随机」顺序消费内容，内容结束且时长未尽则抽下一条；暂停继续同一内容；提前结束照常收尾。删除 `watch`、`play_game`、`browse` 活动类型与 TextWorld。
- R-J 推送：`ScheduledKind.APP_PUSH` 每日 08:00–21:00 整点事件（按场景时区）；到期为每个清醒或睡眠中的人物各至多一条（睡眠中照常不唤醒，推送仍记入？→ 推送只在人物清醒时生成；睡眠中不产生）；状态 `AgentState.app_pushes: list[PushState{push_id, app_id, content_id, summary, issued_at, session_id}]`；只保留当前 session 的；session 切换即清除；打开该 app 首次消费推送内容；任一绑定 app 完成该内容即解除。唤醒原因新增 `app_push`（进睡眠允许集合？否）。推送进入进度层记录 `app_push {app, summary}`。
- R-K 外卖：全地图可达，删除 `delivery_destination_ids`、配送路线、`route_id`；订单 `{order_id, agent_id, food_id, store_id, app_id, quantity, destination_id, recipient_id, created_at, expected_at, status, total_price_fen, request_message_id}`；到店购买即时交付。
- R-L 购买请求形状：`start_activity` 的 `obtain` 方式仍为「到店」「远程下单」「购买」；`from` 为 null / 店铺显示名 / app 名；`prefer` 沿用；无库存、无营业、无报价版本后 M3「能服务」= 到店：人在该店；远程下单：店铺绑定了 app（且 `from` 为 app 时是该 app）。
- R-M 环境格式：新目录版本 `catalog/manifest.json` 升主版本（6.0），场景结构升到 7，世界结构升到 10，地图结构升到 4；旧版本一律拒绝。
- R-N 删除品类契约与交换规则文件（`category-contract.json`、`exchange-rules.json`、`objects.jsonl`、`resources.jsonl`？）→ 资源定义（货币、能量、睡眠债、娱乐需要）归 WorldMock 固定；身体参数仍由场景 `body` 给出？（既有场景输入保留）。待 synthesis 确认哪些资源定义字段仍需环境提供。
- R-O 手机交接：`purchases.jsonl` 与 `action_result.data.orders[]` 带 `app_id`；娱乐事件带 `app_id` 与 `content_id`；删除 `phone_simulation_config`、`PhoneSimulationCoverage`、`coverage_apps.py`。
- R-P 位置导出：出行事件与活动记录引用 `origin_id`、`destination_id`、`path: [location_id...]`、`distance_meters`；删除轨迹采样与坐标。
- R-Q Dataset 正式环境：WorldMock-Dataset 本地检出已快进到远端 `2cff671`，`environment/zhangjiang-baseline/`（目录 5.5、场景结构 6、四份场景）与 `tools/environment_prepare/`、`tests/zhangjiang/` 都在。本仓库只保留人工最小测试环境（新格式）并给出环境格式合同、`worldmock validate` 的环境校验与交接清单；正式环境按新格式重制（真实 app 名、真实作品名 + 合成摘要、随机图、食品绑定、工作主题）是数据仓的工作，在数据仓分支上进行、不在本仓库；完整层 `--environment-root` 回归在该环境发布后执行，如实登记。

## Answers to the open questions raised by the subsystem maps (categories_catalog)

| Q | Ruling |
| --- | --- |
| Q1 package layout | New package `worldmock.exchange` (fixed types, constants, operators, errors). Delete `worldmock.categories`. `CategoryError` becomes `ExchangeError` (error codes stay stable); `DeepFrozenModel`, `Conservation`, `require_finite_number` move with it; provenance types are deleted. Update `tests/test_single_source.py` OWNERS accordingly. |
| Q2 place kinds | `PlaceKind` closed set: `home`, `office`, `restaurant`, `cafe`, `convenience_store`, `supermarket`, `fitness`, `park`. Delete `merchant`, `wet_market`. `FOOD_STORE_KINDS = {restaurant, cafe, convenience_store, supermarket}` is what `all` expands to and the only kinds whose locations may bind apps. Activity-environment closed set `location_kinds` (`home/office/outdoor/fitness/venue/anywhere`) is unchanged and separate. |
| Q3 body parameters | WorldMock constants (`worldmock.exchange.body_rules` or a package TOML): basal rate, MET table per activity kind, hunger thresholds, sleep/entertainment rates, efficiency curves. `resources.jsonl` and the per-resource drain rules are deleted; lifestyle items keep their own `met_value`. Scenario keeps the body *instance* (`body` block: mass, initial energy, initial sleep debt, initial entertainment need, relief floor). |
| Q4 food constants and price | Adopt 200/100/600 kcal and 5/3/20 min. One `price_fen` per food; identical across every store that offers it (identical menu per kind implies identical price). |
| Q5 binding shape | Explicit `stores: [location_id, …]` or the string `"all"` on each food; registration rejects with `FOOD_BINDING_KIND_INCONSISTENT` when two stores of one kind differ. |
| Q6 cooking | Deleted entirely: `inventory_transform` operator, inventory/item resource, `recipe_output_bindings`, `ingredient`, way `制作`, `inventory_recipe.py`. A dataset may still declare a lifestyle item named 做饭; it produces no food. |
| Q7 inventory and opening | No stock, no opening hours, no quotes: stores serve all day, foods are always available; delete `supply_stock`, `supply_quotes`, `opening_exceptions`, `OpeningCalendar`, `price_change`, `supply_quote_change`, `product_release`, `opening_hours.py`, `storefront_state.py`. |
| Q8 delivery ETA | `DELIVERY_PREPARATION_SECONDS + ceil(shortest_path_meters / DELIVERY_METERS_PER_SECOND)`. |
| Q9 provenance | Contents carry `summary_origin ∈ {real, synthetic}` and `source` (non-empty string when real, null when synthetic). Prices, durations and edge lengths are simulation values by definition (documented once in the environment README/spec, no per-object field). Environment identity = `environment_id`, `version`, file list with SHA-256. `SourceRecord`/JSON-Pointer coverage and `ReviewRecord` are deleted. |
| Q10 manifest | One `manifest.json` at the environment root listing every catalog and corpora file with path, size and sha256 (reuse the `CorpusEntry` shape); `catalog/manifest.json` and `corpora/manifest.json` are deleted; `tests/data_paths.select_environment` requires `manifest.json`, `catalog/`, `scenarios/single-person.json`. |
| Q11 merchant accounts | Implicit ledger accounts created at world setup: `store:<location_id>` for every food store and `app:<app_id>` for every app (delivery fee goes to the app); initial balance 0; scenario `accounts` no longer declares merchant or transport accounts (transport fare goes to implicit `transport` account created the same way). Money stays a closed domain. |
| Q12 `category_matches` | Delete outright. |
| Q13 world-material floors | Keep the semantics of SPEC-life-realism「世界素材品类要求」that still apply (every selected place kind present, pantry, lifestyle fields, contacts) as WorldMock constants checked by `worldmock validate`; delete the dataset-declared `category_requirements` block. Numbers that only the formal environment must meet are recorded as environment acceptance in the spec, not as engine constants. |
| Q14 fares and fees | WorldMock constants with `RULES_VERSION`: driving fare min 500 fen + 0.2 fen/m (ceil), walking 0, `DELIVERY_FEE_FEN` fixed. |
| Q15 scenario pin | Scenario pins `environment_id` and `environment_manifest_sha256` (replacing `catalog_version` and `corpus_manifest_sha256`); `rules_version` is recorded in the run manifest and checkpoints, not pinned in the scenario. |

## Constant values decided (from references.md "Suggested constants"; cite the anchor named there)

| constant | value | status |
| --- | --- | --- |
| WALKING_METERS_PER_SECOND | 1.2 | anchored (MUTCD / Browning band) |
| DRIVING_METERS_PER_SECOND | 7.0 | simulation assumption (Amap 2024 free-flow 48.75 km/h as upper reference) |
| DELIVERY_METERS_PER_SECOND | 5.0 | below GB 17761-2018 25 km/h cap; Meituan ~17 min riding |
| DELIVERY_PREPARATION_SECONDS | 600 | Meituan ETA includes 出餐; rider wait 5.9–9.8 min |
| DELIVERY_FEE_FEN | 500 | Meituan per-order delivery revenue 4.71–7 元 |
| DRIVING_FARE_MIN_FEN / DRIVING_FARE_FEN_PER_METER | 500 / 0.2 (ceil), walking 0 | existing closed-set operator values |
| EXTRA_EDGES(n) | ⌊(n−1)/2⌋ | WorldMock choice (mean degree ≈ 3) |
| EDGE_LENGTH_MIN_METERS / EDGE_LENGTH_MAX_METERS | 200 / 2000 | Shanghai 7.5-min access ~550 m; Amap transit access 1,185 m |
| APP_USE_SECONDS | 1800 | assumption (CNSA 156 min/day short video over several sessions) |
| push grid | hourly on the hour 08:00–21:00 local; ≤1 per person per hour; ≤1 per app per session | Braze quiet hours / frequency capping + user rule |
| FOOD kcal per serving | 零食 200 / 饮品 100 / 正餐 600 | 膳食指南 2022 snack 10% and lunch 30–40% of 2000 kcal; drink is an assumption |
| FOOD seconds per serving | 零食 300 / 饮品 180 / 正餐 1200 | meal anchored on NBS 2024 103 min/day eating; snack and drink are assumptions |
| content relief per hour (entertainment_need units) | article 0.15, image_text 0.15, short_video 0.25, long_video 0.25, live 0.20 | WorldMock assumption replacing per-item rates (the current items use 0.2 as the common value); registered in EXPERIMENT.md |
| push summary cap | 200 characters | registration rule (本规格补定) |
