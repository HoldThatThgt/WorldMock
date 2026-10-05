> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-behavior/dining-design-review.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 餐馆进食路径只读设计核查

> 核查位置：`/Users/atishoo/.codex/worktrees/dayrun-behavior/WorldMock`，起始 HEAD `74edead`。只读核查与 JSON 统计；未执行测试、真实模型调用、外部地图请求，未改仓库。本文是父层冻结规格的输入，不是实现或验收通过声明。

## 结论与父层裁决

沿用已有 `purchase` → 自提交付/配送交付 → `consume`。正式三个场景选中的三家餐馆都显式配置已有正餐商品供给，到场显示真实供给；无可购餐食时明确说明。通用地图的空菜单合法，不能要求每家餐馆始终有货，不能解析自由目标文字生成食物，不新增“一次用餐”动作。

```mermaid
flowchart LR
    C[已有正餐商品与营养来源] --> S[显式门店供给与场景假设]
    S --> O[真实到达后的观察窗口]
    O --> P[人物购买并付款]
    P --> F{履约方式}
    F -->|自提| H[即时增加店内持有量]
    F -->|配送| D[等待既有交付事件]
    D --> H2[增加目的地持有量]
    H --> E[人物 consume 并实际耗时]
    H2 --> E
    E --> R[扣实际份量并留下进食证据]
    O -->|当前无可购餐食| N[如实说明 无食物生成与付款]
```

## 当前生产事实

| 环节 | 已确认事实及证据 | 对本次的含义 |
| --- | --- | --- |
| 正式地点 | 三个正式场景均选入 `poi-3538660453` 菜饭骨头汤、`poi-3538682404` 鼎盛川菜、`poi-3538682408` 三十六道金牌煲仔饭；三家 `business_kind=restaurant`，各自供给数量均为零。场景各有十一地点、一百五十九产品、一百六十一供给 | 真实缺口是商品与餐馆未关联，不是缺少餐馆名称 |
| 正餐商品 | `corpora/meals/items.json` 已有三十条，均声明堂食/配送；`scripts/build_catalog.py:663` 校验来源、价格与用餐时长，`:686` 编译 `prepared-meal`。`semantics.py:413`、`:439` 组装为可 `eat` 的商品，保留热量、时长、份量、类别 | 不需要抓取新食物，不需要增加商品类别或规则 |
| 正餐供给 | 三十条全部是 `supply-market-meal-*`，地点 `market`，卖方 `merchant:restaurant`，每条初始四十份，支持 pickup/delivery；三家餐馆没有任何供给。价格、库存与供给归属属于明确场景假设 | 供给必须以 product_id 和 location_id 明确编排，不能按店名猜菜 |
| 来源边界 | 真实餐馆坐标/名字与假设营业/菜单分开。`supply-assumptions.json` 当前是商品级价格、库存、卖方说明，未列门店分配。`supplies.json` 才是运行供给权威 | 新增门店关联也应写清楚是场景假设，不能伪称真实门店菜单 |
| 到场可见 | `observe.py:69` 的普通观察走候选窗口；`:507` 把本地供给与仍有效的可见供给合并；`:889` 按实际 location_id 取本地供给。`:47` 仅暂存实际窗口身份，提交时才授予可见凭据 | 到达后无需强制再调用 discover；先补正式输入，再用真实接缝证明本地菜品首轮可买，不能另造全菜单授权通道 |
| 空菜单与售罄 | `discovery.py:55` 无 product 筛选时保留无供给地点，返回 `supplies=[]`；指定商品无匹配则无结果。候选 `candidates.py:447` 排除零库存。`projection.py:202`、`:226` 未提供本地餐食空缺说明；`observe.py:213` 仅有全世界 supply_count，且不进 scene/facts | 普通到店观察确实缺少“当前无可购餐食”的直接表达。空窗口也可能因窗口名额，不能据 `facts.products==[]` 推断无餐食 |
| 购买工具 | `custom/agents/life_menu.py:236` 读取实际供给合同，先检查 purchase_allowed，再按履约地点过滤；三场景 restaurant 的显式菜单已允许 purchase、consume、travel、发现 | 不需新增工具或因 restaurant 名称放开权限；场景菜单保持明确声明 |
| 付款与交付 | `basket.py:64` 一次验证整单、转账、扣供给库存。`:156` 自提无等待；`:185` 要求人物与目的地均在门店。配送按备货时间与真实驾车路线安排事件；付款不等于持有。库存归交付地点 | 堂食复用 pickup 即时交付。这是既有仿真合同，并不新增厨师备餐/座位/排队机制 |
| 实际进食 | `engine.py:1169` 受理 consume；`basket.py:252` 要求多件食品时长取最长，`:282` 要求本地持有；`:295` 按实际经过时间扣份量、只按实际扣量加热量。`schedule.py:98` 用餐只由 consume 家族证明 | 到达、发现、购买、等待配送都不能算已经吃饭；中断后保留尚未食用份量，恢复不能重扣 |
| 现有准入 | `map_data.py:180` 校验已选供给的地点、商品、卖方与配送范围；`semantics.py:318` 校验品类动作配对。`catalog/admission.py:620` 检查正餐，`:667` 单独数餐馆，没有二者的关联覆盖 | 正式输入必须补交叉覆盖校验/测试，不能只重复“餐馆数足够”和“正餐数足够” |
| 准入入口区别 | `validation.py:42` 执行世界品类准入，但只有显式 CLI validate 调它（`cli.py:121`）。普通运行经过 `runtime.py:266` → `load_runtime_scenario` → `semantics.py:295` | 若某条规则需要所有新运行强制执行，不能只加 validate_project。父层已裁决通用空菜单合法，不能把正式三个场景的输入覆盖要求误做全局 RuntimeScenario 不变量 |

## 最简单的输入与观察方案

正式数据发布时明确给三家现有餐馆分配已有 `prepared-meal` 商品；采用现有供给结构，明确 `supply_id/location_id/product_id/seller_account_id/quotes/initial_stock/fulfillment_modes/source`。每家至少一条可在本店取得并进食的正餐，实际选哪些菜与多少条通过一份显式映射冻结；价格、库存、配送备货假设沿用已核对的原料，不从名字推断。“每家至少一条”是供给可执行性的最低要求，不是建议三家都只有同一道菜；具体映射由父层冻结为实际输入。

可以新增三家门店供给并保留 market 的独立供给，也可以显式重新分配既有餐食供给；这属于世界输入决策，应在最终规格/数据表写清楚，不能实现时暗中复制全部菜单。当前无 price_changes/product_releases 引用 meal 供给，改归属不牵涉现有正餐预排事件。共享 merchant:restaurant 账户是已有合法模型，同一卖方跨店仍分别保留库存；不必新增多余商户账户。

输入覆盖应按三个正式场景**最终选中**的地点、供给和产品关联检查，不能只检查原始全量 map：每个正式餐馆至少有选中的 prepared-meal，商品可 eat、可消耗且没有冲突的 required_location_id；供给支持 pickup、正初始库存、已声明有效报价与明确卖方；人物与地点的实际菜单允许购买及 consume。营业、访问、供给资格和付款仍在执行时重验。通用 map 保留合法空供给；正式输入发布/验收失败必须阻止合并发布。无需新建 dining_destinations 配置、绕过品类定义或通过自由目标文字识别“计划餐馆”。

普通观察继续使用现有 `facts.products` 供给窗口与 `data.supplies/data.products/inventory_items` 工具合同，不能直接把全表传给模型或授权。仅在 scene 增加一句基于实际当前位置、已声明食物供给与当时购买条件的餐食说明；没有可购餐食时明确说明，足够表达父层裁决，无需新动作或新世界状态。具体 scene 字段名在合同冻结时一次确定，派生于本地供给而非 `products` 窗口长度；售罄、闭店、未知报价等沿用已有行事实与原因。正面菜品仍由实际窗口呈现，未进入窗口者不能获得购买凭据。若测试发现首轮到店窗口确有挤出，再在现有窗口契约下修正，不能先增设第二套菜单链路。

## 必须修改的文件

| 类别 | 文件 | 改动 |
| --- | --- | --- |
| 供给权威 | `corpora/zhangjiang-map/supplies.json` | 三家餐馆显式正餐供给；稳定供给/报价身份、库存、履约、来源 |
| 假设依据 | `corpora/zhangjiang-map/supply-assumptions.json` | 记录明确餐馆与 meal 身份映射及场景假设；不声称观察到真实餐馆菜单 |
| 地图发布 | `corpora/zhangjiang-map/dataset.json` | 更新 supplies_file 的 SHA；更新地图版本与发布说明，其他已冻结路线/地点字节不重抓 |
| 语料身份 | `corpora/manifest.json` | 更新改动文件的 sha256、size_bytes 与发布身份/来源；覆盖 dataset、supplies、assumptions |
| 正式选择 | `scenarios/white-collar-12-weeks.json`、`scenarios/multi-person.json`、`scenarios/multi-person-half-day.json` | 更新 selected supply ids、地图 version/SHA 与 corpus_manifest_sha256 |
| 观察组装 | `src/worldmock/life/observe.py` | 同一当前世界切片产生本地餐食状态；保持既有候选窗口与购买可见性 |
| 观察投影 | `src/worldmock/life/projection.py` | 把本地餐食说明投影进 scene；只描述当前观察，不保存第二份世界状态 |
| 中文渲染 | `src/worldmock/life/narration.py` | 确定性渲染空菜单/暂无可购餐食说明，沿用字符预算 |
| 输入检查 | `tests/life/test_scenario.py` | 三个正式场景逐餐馆检查最终所选可堂食正餐；替换仅数餐馆/正餐即可证明可用性的不足断言 |
| 正式语料身份 | `tests/corpora/test_corpora.py` | 新发布 corpus_id/hash 链验证；输入计数变化按实际结果更新，不删除完整性检查 |
| 真实闭环 | 新 `tests/life/test_restaurant_dining.py` | 真实出行→首轮观察→购买→消耗→恢复；只引用正式供给，不手工注入库存/授权代替流程 |
| 空缺呈现 | `tests/life/test_storefront_discovery.py`、`tests/life/test_narration.py` | 空供给、全售罄、闭店/未知、窗口未呈现的区分；模型实际消息与预算 |
| 宿主接缝 | `tests/integration/test_storefront_tools.py` | 将真实餐馆观察送入真实 LifeAgent Schema/本地校验/派发准备，验证实际呈现供给进入 purchase，交付后 eat 进入 consume |

输入覆盖父层若决定必须由可执行生产发布检查承担，可在 `src/worldmock/validation.py` 增加针对完整正式输入的关联检查，并补 `tests/footprints/test_validation.py`；不要把它描述为所有运行必经门禁，不能以新通用配置区别“正式/非正式”。现有运行地图引用/品类/状态不变量继续由生产装配层承担。若不新增发布命令，正式数据逐场景可执行测试已经是最小发布门禁，需在交付验收明确命令。

现成 `scripts/prepare_map.py` / `corpora/map_prepare.py:32` 只复制供给声明并重新取得道路路线，不是供给生成器；不能为加餐食调用它去重抓二百二十条路线。`scripts/build_catalog.py` 不负责门店归属，本次不改 meal 商品时无需重编目录。`catalog/manifest.json`、三十条商品定义、`corpora/meals/items.json`、地点与路线无需改。供给输入修改使用现有 JSON 结构与 SHA 发布流程即可，不新建通用生成组件。

## 设计与规格文件清单

| 文档 | 必需同步的内容 |
| --- | --- |
| 新冻结 `docs/dev/SPEC-dayrun-behavior.md`（最终文件名由父层统一） | 三个正式场景的餐馆覆盖、首轮到店观察、无可购餐食、购买/交付/消费各自含义、输入与运行验收；不加入 dine 动作 |
| `docs/dev/PROPOSAL-dayrun-behavior.md` | 冻结后按父层文档治理决策替换或归档，不能保留相冲突权威 |
| `docs/dev/SPEC-map-and-storefront.md` | 在正式输入覆盖与到店观察补餐馆条款；通用空菜单仍合法；不改共享窗口/供给授权纪律 |
| `docs/dev/SPEC-life-realism.md` | “世界素材品类要求”从两个独立数量要求补到真实可执行餐馆关联；“商务与进食”明确到场/付款不等于消费 |
| `docs/dev/SPEC-life-runtime.md` | 观察与具体进食结算交接；明确空菜单 scene 与供给行事实，指向统一餐馆验收 |
| `docs/CONTRACTS.md` | scene 新餐食说明字段的单一形状与来源；沿用 purchase/consume 请求与订单/库存含义，不新增 action kind |
| `docs/design/03-resource-engine.md` | 资源引擎负责已有正餐与门店供给的显式关联、场景假设和发布哈希；空结果/未知不生造食物 |
| `docs/design/04-simulation-engine.md` | 实际到场观察、等待交付、consume 启动/进度、错误后人物继续选择；不把多步强制自动执行 |
| `docs/design/02-exchange-engine.md` | 到店自提即时付款与取得；配送付款后等待；实际食用结算，交易失败原子性及中断留存 |
| `docs/design/08-configuration.md` | 正式三场景的餐馆供给覆盖、地图与语料新版本选择、与空菜单通用模型的区别 |
| `docs/design/09-io-formats.md` | scene 新餐食说明字段与既有 facts.products/inventory；不额外复制全量菜单 |
| `docs/design/05-recording-engine.md` | 观察供给→付款订单→实际交付→消费份量证据链；恢复保持相同供给与库存/进度，不推断“吃完” |
| `docs/design/01-architecture.md` | 在既有四引擎地图交接处简要对齐餐食供给准备、交易计算、生活执行和记录职责；不增加组件 |
| `docs/VERIFICATION.md`、`docs/dev/implementation-status.md` | 新餐馆正式输入与真实动作门禁证据；静态/离线通过与真人格真实长跑区分 |
| `docs/INDEX.md`、`docs/design/README.md`、`docs/design/SUMMARY.md` | 仅在新增规格/验收文件或章节索引变化时同步导航 |

`SPEC-interaction-menu.md`、LabelKit 最终格式、delivery 导出、evaluation 算法没有本次必需新行为；按既有门禁验证其不被破坏即可，不能为了文件清单齐全强改。

## 真正端到端验收

| 场景 | 操作与必要断言 |
| --- | --- |
| 正式三家覆盖 | 三个正式 scenario 经真实 load_runtime_scenario 解析；每家餐馆存在 selected prepared-meal 的 pickup 路径，确认 actual kind/menu/category 允许。预检不得因 products 中有三十条 meal 而放过餐馆零供给 |
| 到店首轮即可买 | 人物从 home 按正式冻结路线 start_travel，经过真实时间边界/continue 到达开放餐馆。**到达后不再 discover**，真实 observe 的 facts 与生成模型 user 消息包含本店供给；实际动态 purchase Schema 可选这些身份。到达并未增加食品持有量、没有食物付款、没有 consume 证据 |
| 自提付款与持有 | 采用观察报价购买一至三份。断言买方余额减少、正确卖方增加、供给库存减少、订单 delivered、expected_at=created_at、目的地就是餐馆、本地持有增加；餐食热量和用餐目标尚不因购买推进 |
| 实际消费 | 下一合法决策以持有 product_id 发 consume:eat，真实边界完成。断言累计 consumed、实际库存、身体结算、活动 elapsed 与用餐证据一致；不能只断言 result.status |
| 配送原路径 | 已有餐食 delivery 供给远程购买，付款时零持有，交付前 consume 拒绝；真实 delivery_arrival 后只在目的地持有，人物必须在该地点才可 eat。不新增餐馆专属配送机制 |
| 空菜单与售罄 | 使用合法通用空供给地点，真实到达后渲染明确暂无可购餐食，purchase 不获得凭空供给；已有供给通过真实 product_release=0 事件或购买耗尽后同样清楚表达。无付款/新增持有/consume；不得用把仓库正式菜品改成零供给来规避正式覆盖门禁 |
| 窗口限制 | 本地食物存在但某项因窗口名额没呈现时，不把该项当作世界不存在，不因统计摘要授予购买凭据。首轮餐馆已展示项可购，未展示同店另一个供给不能借同 product_id 授权 |
| 中断与恢复 | 在自提后、实际进食中段分别建立 SQLite/共同检查点并重开；恢复后原地点、供给库存、买卖双方余额、订单状态、已吃份额、剩余持有量与父线一致。继续真实 consume 不再付款、不重复交付或重吃已消费份额；父目录保持不变 |
| 拒绝仍保留出行 | 出发后真实闭店/调价/耗尽，购买按既有原因拒绝，不能撤销位置、旅行耗时/交通费，也不能因此虚构别店餐食 |

现有 `tests/life/test_storefront_recovery.py:180` 已覆盖便利店食品的购买/配送/恢复，但 `:122` 到店后重新 discover；`tests/life/test_storefront_arrival.py:169` 已有旅途中状态改变后的购买拒绝。它们是可复用辅助和回归，不是餐馆首轮供给真实接线的现成证明。最终新功能应交给独立 Bob 按新冻结条款做行为突变，重点破坏餐馆关联、到店可见、零持有消费、扣量、恢复重复交付与空菜单表达。

## 音频素材只读补充

仓库目前没有可直接复用的播客/音频节目条目，也没有冻结 MP3/WAV/OGG 音频文件。对 corpus manifest、文件名与现有内容配置检索，唯一带 audio 的来源是 `benozz/Chinese_drama_audio`；`corpora/drama/items.json` 将它明确记录为“华语短剧台词选段”，跨多个源剧集的台词串，不是公开播客。不得把它或既有电影/Sintel 改名播客来通过“通勤听播客”。现有 `ContentItemConfig.kind` 与 `catalog/affordances.toml` 也没有 podcast；素材添加与父层并行活动规格中的音频类别接线是两个都必须完成的工作。

推荐最小来源是 NASA 官方音频节目 **Houston We Have a Podcast — Space Habitat**。本次已读取[官方节目页面](https://www.nasa.gov/podcasts/houston-we-have-a-podcast/space-habitat/)，确认其为官方播客，有带 `HH:MM:SS` 标记的完整转写；同页原始 HTML 明确链接音频 `https://www.nasa.gov/wp-content/uploads/2017/12/ep24_space_habitat.mp3`。这是一条真正的音频节目来源，不是从视频改名。NASA 对其素材的使用要求见[官方媒体使用说明](https://www.nasa.gov/nasa-brand-center/images-and-media/)，来源身份保留 NASA，不能写成项目获 NASA 背书；不要把第三方音乐/商标等泛称项目自有素材。

只读解析官方 HTML 的时间标记得到：全页二百零一个标记，顺序无倒退，首标记零秒，末标记三千三百二十四秒。可先冻结访谈选段 `00:01:52–00:14:58`，时长七百八十六秒；两个边界都来自官方锚点，该范围有六十九个时间标记（含同秒标记），最长单段转写一千零九十四字符。选段可明确标为英文访谈选段；不自动翻译为一段并不存在的中文音轨。

转换只需把同秒锚点文本合并，下一严格递增官方锚点作为上一片段结束，减去选段起点后写既有 SRT。不要按字数猜时间、按等长区间分配段落、把无尾锚点文本凑成节目结尾，亦不需要引入语音识别或强制对齐库。这里是官方段落级时间轴，不是逐词精确对齐；内容接触和事实约束须在规格注明其粒度。现有 `content.py:145` 在区间与 cue 相交时揭示整条 cue，长段落会把尚未听到的后半段一并暴露：音频接线验收必须覆盖中途停止的材料边界，不能宣称原函数原封不动就满足“只记住实际听到内容”。最简单保守选择是完整播到片段结束才给该片段文字，保留源精度，不编造细时间点；最终由父层统一内容揭示规则。

| 最小冻结文件 | 内容 |
| --- | --- |
| `corpora/podcasts/nasa-space-habitat.source.html`（新增） | 官方原始转写页快照，记录获取 URL/时间与 SHA；本次仅在内存探查，尚未下载发布 |
| `corpora/podcasts/nasa-space-habitat.identity.json`（新增） | 节目/集身份、官方 audio URL、语言、选段原始起止时间、来源使用说明与转换方法；不得把选段长度标成整集长度 |
| `corpora/podcasts/podcast-0001.srt`（新增） | 由已冻结官方时间锚点确定性转换，保持原文字、源段落索引与归零后时间；排除片段外内容 |
| `corpora/podcasts/items.json`（新增） | 明确标题为该节目英文访谈选段，语言 en、主题、时长、timeline_path 与 SHA；其他元数据的场景假设应单独标明 |
| `corpora/manifest.json` 与三个正式场景 | 加入新素材身份、来源、hash 和选择引用；与餐食发布同一轮生成最终 manifest，避免两方反复覆盖哈希 |
| 来源转换/内容接触测试 | 源锚点→SRT 首尾/顺序/同秒合并验证，真实 listen/媒体动作越过与尚未越过片段末尾的文字权限，暂停/恢复不提前/重复揭示 |

本次没有读取或播放整个 MP3，因此尚未独立验证音频二进制时长和逐段声文同步；实现发布时应保留这一区别。对 transcript＋时间轴路线，官方节目页、官方时间标记和节目 audio 身份已经给出足够具体的可冻结输入；若验收要求逐词听觉同步，则仍需原音频校对，不能靠当前段落时间戳伪称完成。

另已核查 [Changelog 官方 transcript 仓库](https://github.com/thechangelog/transcripts)：提供带时间标记的 Markdown，且 [README 明确 CC BY-SA 4.0](https://github.com/thechangelog/transcripts/blob/master/README.md#license--copyright)。它可作后续独立音频来源，但抽查节目转写标记较稀疏，当前 NASA 段落锚点更直接，不建议为最小首集同时接入第二来源。网站代码许可证不能代替节目文字/音频许可证。

