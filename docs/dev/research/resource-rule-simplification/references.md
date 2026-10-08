> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

# 资源与规则引擎简化：常数与约定的外部依据

> 代码基线：35d0b610fb4a35304e66619048d7500d2d1e05c7

本记录是规格裁决记录引用的外部依据，检索日期 2026-10-06；表内数值是当时来源的事实或标明的仿真假设，不是已实现行为的证明。

## Citable references (English working notes)
> Scope: references for the constants and conventions in the rulings of the resource-and-rule simplification specification (D1, D2, D4, D5, D8, D10, R-A, R-B, R-C, R-I, R-J, R-M, Q8).
> Access date for every row: 2026-10-06 (Asia/Shanghai). Searches ran from a US-located search endpoint.
> Not duplicated here (already in `industry-evidence.md`): NetworkX `random_labeled_tree` / `dijkstra_path_length` / `is_connected`, commercetools Product Selections, Firebase Cloud Messaging data payloads and click actions, SimPy time and scheduling.
> Quotes are kept under 15 words. Where a page body could not be fetched (noted in the table), the wording comes from search-engine summaries of that page and is marked "paraphrase"; these rows are marked as paraphrase and are not quoted in the specification.
> Source-quality tags: **[P]** primary / official; **[S]** secondary (press, analyst, blog) reporting a primary figure; **[A]** simulation assumption with no authoritative source.

---

## 1. Travel speeds, preparation time, delivery fee

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [P] FHWA MUTCD, Section 4I.06 "Pedestrian Intervals and Signal Phases" (as reproduced by UpCodes and Caltrans CA-MUTCD Chapter 4I) | https://up.codes/s/pedestrian-intervals-and-signal-phases ; https://dot.ca.gov/-/media/dot-media/programs/safety-programs/documents/ca-mutcd/2026/2026-camutcd-2026-4i-a11y.pdf | Engineering design walking speed used for signal clearance; a conservative lower bound for an adult | "a walking speed of 3.5 feet per second" (= 1.07 m/s) | 2026-10-06 |
| [P] Browning, Baker, Herron, Kram (2006), "Effects of obesity and sex on the energetic cost and preferred speed of walking", J Appl Physiol 100(2):390–398; and Browning & Kram (2005), Obesity Research (Wiley) | https://onlinelibrary.wiley.com/doi/abs/10.1038/oby.2005.103 | Laboratory preferred walking speed of healthy adults | normal-weight subjects preferred 1.47 m/s; obese 1.40 m/s (paraphrase of abstract) | 2026-10-06 |
| [S] ScienceDirect topic page "Walking Speed" (aggregates pedestrian studies) | https://www.sciencedirect.com/topics/medicine-and-dentistry/walking-speed | Typical adult range for travel-time estimation | "between 1.2 and 1.4 m/s" (paraphrase) | 2026-10-06 |
| [P] GB 17761-2018 "Safety technical specification for electric bicycle" (Code of China English edition page) | https://codeofchina.com/standard/GB17761-2018.html | Legal maximum e-bike speed in China, hence an upper bound for delivery-rider speed | "maximum design speed shall not exceed 25 km/h"; superseded by GB 17761-2024 (in force 2025-12-01) with the same 25 km/h cap | 2026-10-06 |
| [S] TMTPost, "China Caps E-Bike Speeds at 25 km/h in New Safety Overhaul" | https://en.tmtpost.com/post/7641611 | Confirms the 25 km/h cap carried into GB 17761-2024 | "25 km/h" | 2026-10-06 |
| [P] Amap (高德地图) & partners, 2024 年中国主要城市交通分析报告 (summary republished on Sohu; full PDF on fxbaogao) | https://www.sohu.com/a/862338037_122020073 ; https://www.fxbaogao.com/detail/4705698 | Urban car speeds in Chinese cities; only the top free-flow figure was extractable from the summary page | Suzhou highest free-flow speed 48.75 km/h; peak-hour speeds are lower (no city value in the summary page fetched) | 2026-10-06 |
| [P] Xinhua, 高德地图发布 2024 年三季度交通报告 | http://www.news.cn/tech/20241115/53cbaa0ce8d24f8093f8b0bcb056d203/c.html | Average walking access distance to transit in Chinese megacities (also used in item 5) | 超大城市 1,185 m; 特大城市 1,146 m | 2026-10-06 |
| [P] Meituan news, 第二次算法公开：美团外卖"订单分配"算法公开 (2025-01) | https://www.meituan.com/news/NN25011005301105X | Restaurant preparation is a distinct, modelled stage of delivery; dispatch after 出餐 | "骑手平均等餐时长下降了51%" (pilot of 出餐后调度) | 2026-10-06 |
| [S] CNR (央广网), 美团公开骑手配送时间计算规则 (2021-09) | http://china.cnr.cn/gdgg/20210912/t20210912_525598982.shtml | Meituan's ETA model explicitly includes 商家出餐时间 as a component | ETA picks among "模型预估时间"、"商家出餐时间"、"预期配送时间" (paraphrase) | 2026-10-06 |
| [S] Zhihu analyst note, 美团研究报告：外卖 UE 的优化逻辑及空间测算 (decomposes Meituan per-order time) | https://zhuanlan.zhihu.com/p/528101498 | Typical per-order wait-for-food time and riding time | 等餐 5.88–9.80 min/order; riding 17.4 min/order; total 28 min (2016) | 2026-10-06 |
| [S] Meituan news, 美团无人机 2022 年沪深两地配送超 10 万单 | https://www.meituan.com/news/NN230117001043072 | Official per-order end-to-end time reference (drone, lower bound) | "平均每单 12 分钟" | 2026-10-06 |
| [P] Meituan news, 美团外卖的真实佣金比例究竟是多少 (2024-12) | https://www.meituan.com/news/NN241225051008799 | Delivery fee is priced independently of commission, tiered by distance / price / time / weather; H1-2024 delivery revenue vs cost | "佣金标准为 6%-8%"; "配送服务收入 441 亿元，配送相关成本 480 亿元" | 2026-10-06 |
| [S] CBNData / 财联社 analyses of Meituan annual reports | https://www.cbndata.com/information/240527 ; https://m.cls.cn/detail/479714 | Per-order delivery-fee revenue (consumer+merchant paid) | 2019: 4.71 元/单; 2021: delivery revenue 542 亿元 ≈ 7.72% of GTV | 2026-10-06 |

Notes for item 1
- Walking: 1.07 m/s (MUTCD engineering floor) to 1.47 m/s (lab preferred); 1.2–1.4 m/s is the consensus "typical adult" band.
- Urban car speed: no authoritative peak-hour km/h figure was extractable within budget. The Amap report is the right primary source; the full PDF (fxbaogao link) lists per-city 高峰平均速度. Treat the chosen value as **[A]** until someone reads the PDF.
- Delivery rider speed: no published mean speed; the 25 km/h legal cap is the only hard number. Meituan's 17.4 min riding per order with typical 3 km radius implies roughly 10–12 km/h effective (includes stops), so a straight-line speed around 15–20 km/h is defensible. **[A]**
- Preparation time: 6–10 min wait observed by riders (which is after dispatch, so true prep time is longer); 10 min is a reasonable fixed value. **[A]** anchored to [S].
- Delivery fee: consumer-facing fee is tiered (official); per-order revenue 4.7–7 元 (secondary). A flat 5 元 fee is within the observed band. **[A]** anchored to [S].

---

## 2. Food energy per serving and eating durations

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [P] China CDC, "Revision and explanation of Chinese Food Guide Pagoda and Plate (2022)" | https://en.chinacdc.cn/health_topics/nutrition_health/202206/t20220622_259773.html | Daily energy reference used to scale per-serving constants | adult range 1600–2400 kcal/day; pagoda quantities at 2000 kcal level | 2026-10-06 |
| [P] Chinese Nutrition Society, 中国居民膳食指南 2022 (official site) | http://dg.cnsoc.org/article/04/wDCyy7cWSJCN6pwKHOo5Dw.html | Three-meal energy split that makes ~600 kcal per main meal reasonable | 早餐 25%–30%，午餐 30%–40%，晚餐 30%–35% (of daily energy) → 500–800 kcal per meal at 2000 kcal | 2026-10-06 |
| [S] 光明网, 怎样才算规律进餐 (restates the guideline split and meal windows) | https://m.gmw.cn/2023-11/28/content_1303584872.htm | Same split plus recommended meal windows | 早餐 6:30–8:30，午餐 11:30–13:30，晚餐 18:00–20:00 | 2026-10-06 |
| [P] 中国学龄儿童膳食指南 (2022), core recommendation (reprinted by Dongguan health commission) | http://dghb.dg.gov.cn/zsjg/dzsjbyfkzzx/jkzt/spaqyhjws/content/post_4206592.html | Snack energy ceiling that makes ~200 kcal per snack reasonable | 零食总量不超过全天总能量的 10% → ≤200 kcal at 2000 kcal | 2026-10-06 |
| [P] China CDC, 《中国儿童青少年零食指南（2018）》发布 | https://www.chinacdc.cn/jkyj/yyyjk2/jswj13949/201805/t20180523_296925.html | Observed snack share of daily energy in China | 零食提供能量约占每日总能量的 10% (paraphrase) | 2026-10-06 |
| [P] 国家统计局, 第三次全国时间利用调查公报（第二号）(2024-10-31) | https://www.stats.gov.cn/sj/zxfb/202410/t20241031_1957216.html | Daily time spent eating (all meals and snacks) | 用餐或其他饮食活动 1 小时 43 分钟/日，参与率 99.8% | 2026-10-06 |
| [P] 国家统计局, 2018 年全国时间利用调查公报 | https://www.stats.gov.cn/sj/zxfb/202302/t20230203_1900224.html | Earlier wave, same order of magnitude | 用餐或其他饮食 105 分钟/日 | 2026-10-06 |

Notes for item 2
- 600 kcal per main meal sits inside the guideline's 30–40% lunch band at a 2000 kcal day. **[P]-anchored assumption**.
- 200 kcal per snack equals the 10% snack ceiling at 2000 kcal. **[P]-anchored assumption**.
- 100 kcal per drink has no guideline figure; closest reference point is the WHO free-sugars guideline (<10% of energy, i.e. ≤200 kcal/day from free sugars; https://www.who.int/publications/i/item/9789241549028 — URL from the WHO catalogue, not fetched in this session) and the fact that a 330 ml sugared soft drink is ~140 kcal. **[A]**.
- Durations: 103 min/day across roughly three meals plus snacks gives ~25–30 min per main meal, so 20 min is a conservative meal duration **[P]-anchored assumption**; 5 min snack and 3 min drink have no source at all **[A]**.

---

## 3. Entertainment content taxonomy and durations

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [P] CNNIC, 第 55 次《中国互联网络发展状况统计报告》(2025-01; PDF mirror and Xinhua summary) | https://pdf.dfcfw.com/pdf/H3_AP202501191642341023_1.pdf ; http://www.news.cn/tech/20250121/90a851eebba244f5989055a8b6957e4f/c.html | Official split: 网络视频 / 短视频 / 微短剧 / 网络直播 as separate categories with user counts | 网络视频 10.70 亿 (96.6%)；短视频 10.40 亿 (93.8%)；微短剧 6.62 亿；网络直播 8.33 亿 (75.2%) | 2026-10-06 |
| [P] CNNIC report index (official site, 56th report PDF, 2025-07) | https://www.cnnic.net.cn/NMediaFile/2025/0730/MAIN1753846666507QEK67ZS9DH.pdf | Newer wave of the same taxonomy (not read in detail) | — | 2026-10-06 |
| [P] 中国网络视听节目服务协会 (CNSA), 《中国网络视听发展研究报告（2025）》(2025-03; via People's Daily / 财联社 summaries) | http://sc.people.com.cn/n2/2025/0326/c345167-41176696.html ; https://www.cls.cn/detail/1984522 | Short-video vs long-video vs live as distinct segments; daily usage | 短视频人均单日 156 分钟；长视频用户 7.52 亿 (67.9%)；微短剧人均单日 101 分钟 | 2026-10-06 |
| [P] 微信公众平台开发文档, 素材管理 / 上传永久素材 | https://developers.weixin.qq.com/doc/subscription/guide/product/asset.html ; https://developers.weixin.qq.com/doc/service/api/material/permanent/api_addmaterial.html | "图文消息" (image-text) is a first-class content type in WeChat Official Accounts API | material types image / voice / video / thumb; 图文消息 with inline images (paraphrase) | 2026-10-06 |
| [P] 哔哩哔哩 帮助中心 (创作中心 section) | https://www.bilibili.com/blackboard/help.html ; https://www.bilibili.com/read/home | Bilibili separates 视频 (video), 专栏 (article), 直播 (live) as creator product lines | help center groups 专栏相关 / 直播相关 / 投稿规则 (paraphrase) | 2026-10-06 |
| [S] Xiaohongshu creator guidance (secondary restatement; official 社区规范 at agree.xiaohongshu.com) | https://agree.xiaohongshu.com/h5/terms/ZXXY20221213003/-1 ; https://www.ixunke.com/article/756 | Xiaohongshu's note types: 图文笔记 vs 视频笔记 (+ 直播) | "图文笔记 … 视频笔记" as the two note kinds (paraphrase) | 2026-10-06 |
| [S] 21 财经, 打破 15 秒的时长限制，抖音全面开放 1 分钟视频权限 (2019) and later tiers | https://m.21jingji.com/article/20190425/herald/e7cec65b8609aca464cddc2bf2fcb2ad.html | Short-video platform duration tiers (15 s → 60 s → 5 min → 15 min → 30 min cap) | "15 秒 … 1 分钟" (paraphrase); 30 min upper cap per secondary sources | 2026-10-06 |
| [P] 国家广播电视总局, 微短剧发展管理办法（征求意见稿）(Xinhua, 2026-06) | https://www.news.cn/politics/20260624/5794e5cd877e440cb678a7f27112ffc7/c.html | Regulator's duration boundary between micro-drama (short-form) and conventional drama | 微短剧 = 单集时长少于二十分钟 (earlier 2022 definition: 几十秒到 15 分钟左右) | 2026-10-06 |

Notes for item 3
- The five-way split 文章 / 图文 / 短视频 / 长视频 / 实况 is a union of platform vocabularies: CNNIC and CNSA use 短视频 / 长视频(网络视频) / 网络直播; WeChat and Xiaohongshu use 图文; Bilibili and WeChat use 专栏/文章. No single source uses exactly the five labels; cite the union, not one page.
- Durations: no authoritative *median* short-video length was found. Hard numbers available: platform tiers 15 s / 60 s / 5 min / 15 min / 30 min; regulator boundary 20 min per episode for micro-drama; CNSA daily usage 156 min short video. Dataset content durations remain **[A]** labelled synthetic (consistent with Q9).

---

## 4. Push-notification cadence, quiet hours, deep-linking

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [P] Apple Human Interface Guidelines, "Notifications" (page body did not render via fetch; wording from search summaries of the same page) | https://developer.apple.com/design/human-interface-guidelines/notifications | Keep frequency low; one notification per thing; tapping opens the related content | "Avoid sending multiple notifications for the same thing" (paraphrase; re-verify) | 2026-10-06 |
| [P] Android Developers, "Notifications" design guide | https://developer.android.com/design/ui/mobile/guides/home-screen/notifications | Do not notify unless time-sensitive and personal; update rather than add | "Don't notify … information that is not truly time sensitive" (paraphrase) | 2026-10-06 |
| [P] Android Developers, "Create a notification" | https://developer.android.com/develop/ui/compose/notifications/create-notification | A tap must have an action that opens the app at relevant content (deep link) | section "Set the notification's tap action" (paraphrase) | 2026-10-06 |
| [P] Braze docs, "Quiet hours" | https://www.braze.com/docs/user_guide/messaging/messaging_fundamentals/quiet_hours | Industry convention: suppress sends in a nightly local-time window; defer to window end | example window 10 pm–6 am, evaluated "in each user's local time zone" (paraphrase) | 2026-10-06 |
| [P] Braze docs, "Rate limiting and frequency capping" | https://www.braze.com/docs/user_guide/engagement_tools/campaigns/building_campaigns/rate-limiting/ | Per-user frequency caps (e.g. N pushes per day) are a standard control | frequency capping per channel per user (paraphrase) | 2026-10-06 |
| [P] Pielot, Church, de Oliveira (2014), "An in-situ study of mobile phone notifications", MobileHCI '14, ACM | https://dl.acm.org/doi/10.1145/2628363.2628364 ; https://pielot.org/pubs/Pielot2014-MobileHCI-Notifications.pdf | Empirical real-world notification load; more notifications → more negative affect | "63.5 notifications on average per day" | 2026-10-06 |

Notes for item 4
- A cap of one push per person per hour, 08:00–21:00 (≤14/day), is far below the observed ~64/day load and matches "frequency capping" practice. The exact window is a WorldMock choice **[A]**, consistent with Braze's quiet-hour examples (night suppression in local time) and the proposal's 08:00–22:00 user requirement.
- Deep-link convention: Firebase (already cited) + Android "tap action" + Apple HIG.

---

## 5. Random connected map generation and trip distances

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [S] bwbaugh gist, "Generate a randomly connected graph with N nodes and E edges" (Python, widely referenced) | https://gist.github.com/bwbaugh/4602818 | "random spanning tree first, then random extra edges" as the standard recipe for guaranteed connectivity | builds a random tree then adds edges until E (paraphrase) | 2026-10-06 |
| [S] nokyotsu, "Generating Random Trees and Connected Graphs" | https://nokyotsu.com/qscripts/2008/05/generating-random-trees-and-connected.html | Same recipe with Broder/Aldous random-walk tree; edge count sampled in [n−1, n(n−1)/2] | "add random new edges … ensures the generated graph is always connected" (paraphrase) | 2026-10-06 |
| [P] NetworkX docs, `gnm_random_graph` (G(n,m) model: fixed number of edges) | https://networkx.org/documentation/stable/reference/generated/networkx.generators.random_graphs.gnm_random_graph.html | Standard way to add a fixed count of uniformly random non-duplicate edges with a seed (not cited in industry-evidence) | G(n,m): m edges chosen uniformly from all pairs, `seed` parameter (paraphrase; URL from NetworkX reference, not fetched this session) | 2026-10-06 |
| [S] Wikipedia, "Maze generation algorithm" | https://en.wikipedia.org/wiki/Maze_generation_algorithm | Spanning tree = connected acyclic subgraph with n−1 edges; procedural-generation precedent | a maze is a spanning tree of the cell graph (paraphrase) | 2026-10-06 |
| [P] 上海市交通委员会, 上海市第六次综合交通调查成果发布 (2022-02) | https://jtw.sh.gov.cn/xwfb2/20220216/442a7885f34b4d2996ba1a48333d808d.html | Walking access to transit in Shanghai central city | 中心城 49% 步行 10 分钟可达轨交站；两端步行接驳平均约 7.5 分钟 (paraphrase) | 2026-10-06 |
| [P] Xinhua / Amap Q3-2024 report | http://www.news.cn/tech/20241115/53cbaa0ce8d24f8093f8b0bcb056d203/c.html | Average walking distance when using transit in megacities | 超大城市 1,185 m；特大城市 1,146 m | 2026-10-06 |
| [S] Statista (citing a 2018 survey), average walking distance with public transport by city tier | https://www.statista.com/statistics/976274/china-average-walking-distance-with-public-transports-by-city-tier/ | Same metric, second-tier cities | ~1,170 m | 2026-10-06 |
| [P] Shanghai central-city pedestrian survey (Transportation Research Procedia, 2017; 2,861 responses, 21 neighbourhoods) — ScienceDirect returned HTTP 403 in this session | https://www.sciencedirect.com/science/article/pii/S2352146517307470 | Walking-trip purpose split in Shanghai; distance figures not extracted | 36.8% exercise, 35.6% commute, 29.7% non-work walking trips (from abstract summary) | 2026-10-06 |

Notes for item 5
- Connectivity recipe: tree + extra edges is folklore with no standards body; the gist/blog plus NetworkX `gnm_random_graph` for the extra-edge step is the best citation. The ⌊(n−1)/2⌋ extra-edge count is a WorldMock choice **[A]** (gives average degree ≈ 3).
- Edge lengths: walking access distances of ~600 m (7.5 min) to ~1,200 m (Amap) justify an edge-length band in the hundreds of metres to ~2 km for an intra-district synthetic map. **[P]-anchored assumption**.

---

## 6. Shared catalog with per-location availability

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [P] Saleor docs, "Channels" overview and "Products API guide" (`ProductChannelListing`) | https://docs.saleor.io/developer/channels/overview ; https://docs.saleor.io/developer/products/api | One product definition; availability/publication set per channel; warehouses assigned to channels | "data can be customized and shared across different channels" (paraphrase); `isAvailableForPurchase` per channel | 2026-10-06 |
| [P] Saleor docs, "Channel Configuration" | https://docs.saleor.io/developer/channels/configuration | Setup order: channel → warehouses → availability | "assign it to warehouses to make stock available" (paraphrase) | 2026-10-06 |
| [P] Shopify Help Center, "Multi-managed inventory" / "Set up locations" | https://help.shopify.com/en/manual/products/inventory/setup/multi-managed-inventory ; https://help.shopify.com/en/manual/fulfillment/setup/locations/setup | A product is created once and is automatically assigned to all locations; per-location stocking is a separate flag | "automatically assigned to all your locations" (paraphrase) | 2026-10-06 |

Notes for item 6
- Both systems support the WorldMock shape: single food catalog, explicit store binding, `all` as a batch binding. Both *allow* per-location differences; "identical menu per store kind" is stricter and is WorldMock's own invariant (same caveat as industry-evidence.md makes for commercetools).

---

## 7. Work tracked as hours against a target

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [P] Atlassian, "Configuring estimation and tracking" (Jira Software) | https://confluence.atlassian.com/spaces/JIRASOFTWARESERVER/pages/938845319/Configuring+estimation+and+tracking | Progress = Time Spent against Original Estimate; Remaining Estimate derived | fields "Original Estimate", "Remaining Estimate", "Time Spent" | 2026-10-06 |
| [P] Atlassian, "Log time on a work item" | https://support.atlassian.com/jira-service-management-cloud/docs/log-time-on-an-issue/ | Each work log = duration + free-text description (narrative is free text) | worklog has time spent and a description (paraphrase) | 2026-10-06 |
| [P] Atlassian KB, "Importing Original Estimate, Time Spent, Worklogs … Using CSV" | https://confluence.atlassian.com/jirakb/importing-original-estimate-time-spent-worklogs-and-remaining-time-using-csv-in-jira-1540724408.html | Canonical storage unit is seconds | "Time tracking fields must be in seconds" (paraphrase) | 2026-10-06 |
| [P] ISO 8601 durations (Wikipedia summary of the standard) | https://en.wikipedia.org/wiki/ISO_8601#Durations | Interchange format if target hours ever need serialising as durations | `PnDTnHnMnS` | 2026-10-06 |

Notes for item 7
- Supports D10 / R-H: accumulate actual seconds in work activity, compare to `target_hours × 3600`, keep the LLM narrative as the worklog description. Toggl/Harvest docs were not fetched within budget; Jira alone is sufficient.

---

## 8. Deterministic seeded RNG and checkpointed state

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [P] NumPy manual, "Bit generators" | https://numpy.org/doc/stable/reference/random/bit_generators/index.html | Seeding gives reproducible streams; BitGenerator exposes get/set state for checkpointing | "support setting their initial state via a seed"; state get/set (paraphrase) | 2026-10-06 |
| [P] NumPy manual, "Legacy random generation" | https://numpy.org/doc/stable/reference/random/legacy.html | Caveat: save the full generator state, not only the bit-generator state, when cached values exist | "use RandomState.get_state, and not the underlying bit generator's state" (paraphrase) | 2026-10-06 |
| [P] Python docs, `random.getstate()` / `random.setstate()` | https://docs.python.org/3/library/random.html#random.getstate | Stdlib RNG state is a picklable object that can be stored and restored | "Return an object capturing the current internal state" (paraphrase; URL from Python docs, not fetched this session) | 2026-10-06 |
| [P] Mesa docs, `Model` API (`seed` / `rng`, `model.random`, `model.rng`) | https://mesa.readthedocs.io/stable/apis/model.html | Agent-based-simulation convention: one seeded RNG owned by the model for reproducible runs | "pass either seed or rng, but not both" (paraphrase) | 2026-10-06 |

---

## 9. Schema versioning with hard rejection, no migrations

| source | URL | supports | figure / quote | date |
| --- | --- | --- | --- | --- |
| [P] Pydantic docs, Configuration (`extra='forbid'`, `strict=True`) | https://docs.pydantic.dev/latest/api/config/ | Reject unknown/legacy fields outright instead of silently ignoring | "Extra inputs are not permitted [type=extra_forbidden]" | 2026-10-06 |
| [P] Git, "hash-function-transition" documentation (repository format version) | https://git-scm.com/docs/hash-function-transition | A well-maintained project that refuses to operate on an on-disk format version it does not understand | "MUST NOT operate on that repository" | 2026-10-06 |
| [P] git/git commit 00a09d5, "introduce extensions form of core.repositoryformatversion" | https://github.com/git/git/commit/00a09d57eb8a041e6a6b0470c53533719c049bab | Older readers bail on newer format; unknown extensions are refused, not migrated | "Older versions of git will not understand format 1 and bail" (paraphrase) | 2026-10-06 |

Notes for item 9
- Supports R-M: bump major versions, reject older ones with an explicit error; Pydantic `extra='forbid'` makes stray legacy fields fail validation (matches the proposal's "旧规则字段直接报错").

---

## Suggested constants

All values are WorldMock simulation constants. "Anchor" names the reference that makes the value defensible; **[A]** marks values with no authoritative source.

| ruling | constant | suggested value | anchor / rationale | status |
| --- | --- | --- | --- | --- |
| R-B | `WALKING_METERS_PER_SECOND` | 1.2 m/s (4.3 km/h) | Between MUTCD 1.07 m/s design floor and Browning 1.40–1.47 m/s lab preferred; ScienceDirect "1.2–1.4 m/s" band (item 1) | [P]-anchored |
| R-B | `DRIVING_METERS_PER_SECOND` | 7.0 m/s (≈25 km/h) | Amap 2024: best free-flow 48.75 km/h; peak-hour urban-core speeds are well below that. Exact peak figure not extracted; read the Amap PDF to confirm | [A] (Amap PDF pending) |
| R-B | `DELIVERY_METERS_PER_SECOND` | 5.0 m/s (18 km/h) | Below GB 17761 25 km/h legal cap; consistent with Meituan ~17 min riding per order | [P] cap + [A] value |
| R-B / Q8 | `DELIVERY_PREPARATION_SECONDS` | 600 s (10 min) | Meituan ETA includes 商家出餐时间; rider wait 5.9–9.8 min (secondary) so true prep ≥ that | [S]-anchored |
| R-B | driving fare `500 fen + 0.2 fen/m` (existing operator) | keep | Not researched here (not requested); carried over from the current closed-set operator | existing |
| R-A | `DELIVERY_FEE_FEN` | 500 fen (5 元) | Meituan per-order delivery revenue 4.71 元 (2019) to ~7 元 (2021); official pricing is tiered, flat value is a simplification | [S]-anchored |
| R-A | `price_fen` per food | dataset value (no constant) | Q9: prices are simulation values; suggest dataset guidance snack 500–1500, drink 800–3000, meal 1500–4500 fen | [A] guidance only |
| R-C | extra edges | ⌊(n−1)/2⌋ | Tree + random extra edges (item 5); count is a WorldMock choice giving mean degree ≈ 3 | [A] |
| R-C | `LENGTH_MIN` | 200 m | Shortest plausible inter-place walk; below Shanghai 7.5-min access (~550 m at 1.2 m/s) | [P]-anchored |
| R-C | `LENGTH_MAX` | 2000 m | Above Amap megacity transit access distance 1,185 m; keeps worst-case shortest paths intra-district | [P]-anchored |
| R-I | `APP_USE_SECONDS` | 1800 s (30 min) | CNSA: 156 min/day short video spread over several sessions; one activity ≈ 30 min. No session-length source found | [A] |
| D4 / R-J | push window and cap | hourly on the hour, 08:00–21:00 local, ≤1 per person per hour, ≤1 per app per session | Braze quiet hours (local-time night suppression) and frequency capping; Pielot 63.5/day shows the cap is conservative; window is the user's requirement (08:00–22:00 new pushes) | [P] convention + user rule |
| D8 | snack energy | 200 kcal/serving | 10% snack ceiling of 2000 kcal (学龄儿童膳食指南 2022) | [P]-anchored |
| D8 | drink energy | 100 kcal/serving | No guideline figure; WHO free-sugar ceiling (≤200 kcal/day) and ~140 kcal per 330 ml soft drink as reference points | [A] |
| D8 | meal energy | 600 kcal/serving | 30–40% lunch share of 2000 kcal (膳食指南 2022) = 600–800 kcal | [P]-anchored |
| D8 | snack duration | 300 s (5 min) | No source | [A] |
| D8 | drink duration | 180 s (3 min) | No source | [A] |
| D8 | meal duration | 1200 s (20 min) | NBS 2024: 103 min/day eating across all meals and snacks → 20 min per main meal is conservative | [P]-anchored |

### Verification gaps to close before freezing wording
1. Apple HIG "Notifications" page body did not render through the fetch tool; quote is from search summaries. Open the page and copy the exact sentence.
2. Amap 2024 full report (fxbaogao PDF) for a per-city 高峰平均速度 value, so `DRIVING_METERS_PER_SECOND` can move from [A] to [P]-anchored.
3. ScienceDirect Shanghai pedestrian study returned 403; if a walking-trip distance is wanted for `LENGTH_MIN/MAX`, fetch it through a browser.
4. NetworkX `gnm_random_graph` and Python `random.getstate` URLs are standard reference paths cited from memory, not fetched in this session.
