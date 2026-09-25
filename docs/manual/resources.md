# 定义地点、门店供给与世界素材

要让人物去新的地点、买新的商品、参加新的活动或接触自己的内容，需要同时准备**素材定义、场景引用和文件清单**。
先从现有场景选择素材，确实需要改变素材本身时，再修改对应的目录或语料文件。
本页命令都在仓库根目录执行；场景基础写法见[场景配置](scenarios.md)。

```mermaid
flowchart LR
    C[资源目录 catalog] --> P[商品的身份、品类与使用效果]
    M[地图 dataset] --> L[地点、门店供给与路线]
    R[语料 corpora] --> A[生活活动、内容、工作材料]
    P --> S[场景选择商品]
    L --> S
    A --> S
    H[清单与 SHA-256] --> V[加载和准入校验]
    S --> V
    V --> W[人物可接触的世界]
```

## 先找到应该修改的文件

| 想改变什么 | 编辑位置 | 场景如何引用 |
| --- | --- | --- |
| 这次实验选哪些地点、商品和供给 | 场景的 `locations`、`products`、`supplies` | 只填写已有身份；供给允许覆盖 `initial_stock` |
| 地点名称、坐标、营业时间、活动环境 | 地图的 `locations.json` | `locations[].location_id` |
| 同一商品在某家门店的价格、库存、配送范围 | 地图的 `supplies.json` | `supplies[].supply_id` |
| 商品的份量、热量、使用方式 | `catalog/objects.jsonl` 和品类契约 | `products[].product_id`；实际取目录中的 `object_id` |
| 可参加的生活活动、地点要求、时间窗口 | `corpora/lifestyle/enrichment.json` 与生活活动生成器 | 运行时固定读取 `lifestyle/items.json` |
| 可观看或阅读的具体内容 | 内容元数据、SRT 时间轴和场景的 `contents` | `item_id`、`timeline_path`、`timeline_sha256` |
| 工作的实际材料和验收答案 | 语料中的输入、答案文件和场景的 `tasks` | `input_path`、`oracle_path` 及各自的 SHA-256 |
| 全局身体、品类或交换规则 | `catalog/` 中的规则与资源定义 | 见[规则配置](rules.md) |

### 哪些路径可以换

场景的 `corpus_root` 相对**场景文件所在目录**解析，也可以写绝对路径。
`map_dataset.path`、内容时间轴、工作材料和游戏路径均相对这个语料根目录解析。
地图内部的 `locations_file.path`、`supplies_file.path`、`routes_file.path` 也相对语料根目录，
并不是相对 `dataset.json`。

目录位置由场景路径决定：`scenarios/example.json` 使用其上两级目录下的 `catalog/`。
`catalog_version` 只钉定版本，不是目录路径。运行命令目前没有 `--catalog` 参数；
需要独立目录时，可以采用 `experiments/my-world/scenarios/` 与 `experiments/my-world/catalog/` 的并列布局。

**地图加载器支持 `map_dataset.path`，但完整世界素材校验仍固定读取 `zhangjiang-map/locations.json`。**
其他固定语料路径包括 `lifestyle/items.json`、`meals/items.json`、`scene-stock/home-pantry.json`、
`scenario-contacts/contacts.json`、`scenario-work/materials.json`，以及下文列出的内容元数据路径。
自定义语料根目录应保留这些布局；只改地图路径不能让所有校验器同时使用另一套文件名。

生活活动的名称与 MET 对应检查会使用场景语料根目录，但活动 `effects` 当前仍从**仓库内**
`corpora/lifestyle/items.json` 读取并缓存。只切换 `corpus_root`，不能保证活动效果也切换到了另一份语料。

### 输入与生成文件

| 文件 | 当前维护方式 | 修改时的注意点 |
| --- | --- | --- |
| `catalog/category-contract.json`、`exchange-rules.json`、`affordances.toml`、`rules/*.toml` | 人工维护的规则输入 | 修改后重新生成或刷新目录身份；见[规则配置](rules.md) |
| `catalog/resources.jsonl`、`objects.jsonl`、`manifest.json`、`overrides/tombstones.jsonl` | 正式基线由 `scripts/build_catalog.py` 生成 | 基线生成器会重写这些文件；独立自定义目录不要再用它覆盖 |
| `scripts/catalog-snapshot-legacy-scenario.json` | 目录生成器仍在读取的冻结输入 | 文件名中的 `legacy` 不代表已停止使用；它不是运行场景 |
| `corpora/lifestyle/enrichment.json` | 生活活动的人工补充字段 | `entries` 必须与生成器的活动身份一一对应 |
| `corpora/lifestyle/items.json` | 生活活动生成结果 | 重建后还要更新语料清单及目录中的活动 MET 表 |
| 地图 JSON 与路线响应 | 已发布的地图数据 | 修改后发布新的地图版本并更新逐层哈希 |
| `corpora/manifest.json` | 全部语料的文件身份与来源清单 | 修改现有条目后更新大小、哈希和来源；新增文件必须新增清单条目 |

## 先完成一个最小修改：减少一家门店的初始库存

下面是完整可运行命令。它复制现有场景，在新场景中把一条供给的初始库存设为 `12`，
再通过真实加载器校验。不修改地图、商品或目录，不调用模型，也不启动模拟。

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from worldmock.life.scenario import load_scenario

source = Path("scenarios/white-collar-12-weeks.json")
target = Path("scenarios/my-resources.json")
document = json.loads(source.read_text(encoding="utf-8"))
document["scenario_id"] = "my-resources"
supply = next(item for item in document["supplies"]
              if item["supply_id"] == "supply-market-food-6923644266066")
supply["initial_stock"] = 12
with target.open("x", encoding="utf-8") as handle:
    handle.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
scenario, _ = load_scenario(target)
assert next(item for item in scenario.supplies
            if item.supply_id == supply["supply_id"]).initial_stock == 12
print("Scenario resource override is valid")
PY
```

`open("x")` 要求目标不存在；已经创建过时，直接编辑该场景即可。
这个例子验证了文件加载与跨引用；正式运行前的完整校验见[验证修改](#验证修改)。

## 编写地点与营业时间

地图主文件声明身份、版本、坐标系、时区、边界和三个子文件的路径与哈希。
现有完整模板见[地图声明](../../corpora/zhangjiang-map/dataset.json)和[地点文件](../../corpora/zhangjiang-map/locations.json)。
地点文件的顶层必须是 `{"schema_version": 1, "locations": [...]}`。

| 地点字段 | 必填与取值 | 含义和关联 |
| --- | --- | --- |
| `location_id`、`name` | 必填非空字符串；身份唯一 | 场景、路线、供给、人物住处和任务地点用这个身份关联 |
| `kind` | 必填字符串 | 对应场景 `interaction_menus_by_location_kind` 的键；新增分类要同步菜单 |
| `business_kind` | 必填字符串 | 地点业态说明，与活动环境不同 |
| `longitude`、`latitude` | 必填有限数值，WGS84 经度/纬度 | 还必须落在地图 `bounds` 内 |
| `location_kinds` | 必填非空列表，无重复 | 只允许 `home`、`office`、`outdoor`、`fitness`、`venue`；地点不能写 `anywhere` |
| `opening_calendars` | 必填列表，可为空 | 为空表示没有营业时间声明；不能理解为全天营业 |
| `required_qualifications` | 必填字符串列表，可为空 | 进入地点需要人物持有的资格 |
| `brand` | 必填，可为 `null` | 有可靠品牌身份时才填 `brand_id`、`name`、`source` |
| `source` | 必填来源对象 | 区分事实和实验假设，见下文 |
| `affordances` | 必填对象 | 保留现有住所、办公室等世界素材要求；任意新增键不会自动创造运行能力 |

营业日历中 `weekly` 必须包含字符串键 `"0"` 至 `"6"`，依次对应周一至周日。
每日区间用当地零点起算的**分钟数**：`540` 是 09:00，`1320` 是 22:00。
一天可有多个不重叠区间；`[]` 表示明确休息。`exceptions` 用 `YYYY-MM-DD` 日期覆盖当天周计划。

以下是一个日历对象中的**局部片段**，不能独立替代完整日历：

```json
{
  "weekly": {
    "0": [{"opens": 540, "closes": 1320}],
    "1": [{"opens": 540, "closes": 1320}],
    "2": [{"opens": 540, "closes": 1320}],
    "3": [{"opens": 540, "closes": 1320}],
    "4": [{"opens": 540, "closes": 1320}],
    "5": [{"opens": 600, "closes": 1200}],
    "6": []
  },
  "exceptions": {"2026-01-01": []}
}
```

跨午夜营业可写 `{"opens": 1200, "closes": 1560}`，表示当晚 20:00 到次日 02:00。
单个区间必须正向且不超过一天，相邻两天的跨日区间也不能重叠。

### 来源与有效期

地点和供给的 `source` 都要求 `kind`、`url`、`retrieved_at`、`license`、`notice`、`fact_cutoff`、`applicability`。
`kind` 只接受 `fact` 或 `scenario`；人为设定的价格、库存、营业时间写 `scenario`。
来源取得时间必须带时区，且不晚于地图的 `prepared_at`；事实截止日期不能晚于取得日期。

营业日历和报价额外要求 `effective_from`、`effective_to`、`reviewed_on`、`still_valid_on` 和自己的 `source`。
`effective_to: null` 表示未声明截止；同一对象的有效期不能重叠。
地图发布日必须处于核对日与有效日之间。历史模拟日期和资料取得日期可以不同，但要在 `applicability` 中如实解释。

### 添加地点时还要补路线

每个场景选中的**有向地点对 × 地图出行方式**都必须有一条路线记录。
`walking` 对应 `foot` 路由配置，`driving` 对应 `car`；不能只写单向路线。

| 路线状态 | 必须提供 |
| --- | --- |
| `reachable` | 正距离（米）、正时长（秒）、非负车费（分），以及带几何和实际出行方式的冻结成功响应 |
| `unreachable` | 冻结的 `NoRoute` 响应；不能填写距离、时长或车费 |
| `unknown` | 非空 `reason`；不能声称已有响应，也不能填写可达指标 |

基础时长来自冻结响应；实际计划时长还乘[出行时长规则](rules.md#出行时长)。
路线车费必须与当前交换规则重新计算的金额一致。

当前地图准备命令是对**已通过加载校验的张江地图**重新取得路线，需联网：

```bash
uv run python scripts/prepare_map.py \
  --source-root corpora \
  --destination /tmp/worldmock-map-release
```

目标必须不存在。命令固定使用 `zhangjiang-map/dataset.json`，不提供城市搜索或任意地点配置参数，
也不补齐完整语料目录或更新场景。新增地点前，要先在源地图中补齐对应的显式路线状态；
准备完地图后，把发布产物纳入完整语料目录，并更新下文的身份引用。

## 编写门店供给与商品

**商品描述“它是什么、怎么使用”，供给描述“哪家门店以什么条件出售”。**
同一商品可以出现在多家门店；各门店的价格和库存由各自的 `supply_id` 区分。
商品对象不能内嵌价格、商家或库存。

供给文件顶层是 `{"schema_version": 1, "supplies": [...]}`，完整模板见[门店供给](../../corpora/zhangjiang-map/supplies.json)。

| 供给字段 | 必填与约束 |
| --- | --- |
| `supply_id` | 全地图唯一；`location_id + product_id + seller_account_id` 的组合也必须唯一 |
| `location_id`、`product_id`、`seller_account_id` | 必须分别选入场景地点、商品和显式服务账户 |
| `quotes` | 必填报价列表；每条有唯一 `price_version`、正整数 `price_fen` 及完整有效期和来源 |
| `initial_stock` | 非负整数；场景同名字段可覆盖这次运行的初值 |
| `fulfillment_modes` | 非空列表，只允许 `pickup`、`delivery`，不能重复 |
| `delivery_destination_ids` | 支持配送时必须非空；不支持配送时必须为空；所有目的地都要选入场景 |
| `delivery_delay_seconds` | 非负整数秒；是备货时长。人物看到的是人读送达时刻（例如「约 13 小时后送达，明天 11:16」），按整单最慢备货加出发时刻的驾车规划时长求得，与实际送达一致；送达地不在可配送目的地或配送路线未知、不可达时写「暂时送不到这里」 |
| `required_qualifications`、`source` | 显式资格列表和来源对象 |

已有报价的局部修改示例：

```json
{
  "price_version": "milk-experiment-price",
  "price_fen": 750
}
```

把这两个字段合并进目标供给的完整报价，同时更新该报价的假设来源。
`750` 表示 7.50 元；保留原有效期前要确认它覆盖本次模拟。
运行中的定时调价和补货用场景 `price_changes`、`product_releases`，详见[场景配置](scenarios.md)。

三个正式场景选中的餐馆供给均为明确的场景假设，不是实际门店菜单：

| 餐馆地点 | 选中正餐商品 | 供给身份 |
| --- | --- | --- |
| `poi-3538660453` | `meal-0018` 鸡汤面 | `supply-poi-3538660453-meal-0018` |
| `poi-3538682404` | `meal-0022` 糖醋里脊 | `supply-poi-3538682404-meal-0022` |
| `poi-3538682408` | `meal-0026` 糖醋肉盖饭 | `supply-poi-3538682408-meal-0026` |

映射与库存依据保存在[供给假设](../../corpora/zhangjiang-map/supply-assumptions.json)。
这些供给支持本店自提；其他地图地点可以没有餐食，售罄或闭店时也不自动补货。

### 新增商品对象

优先选已有品类，让使用方式、单位和规则继承下来。以下是**一条完整原始对象**；
展示时换行，写入 `objects.jsonl` 时需压成一行。它是明确的实验食品假设，不能改标成真实营养事实。

```json
{
  "object_id": "custom-yogurt",
  "name": "实验酸奶",
  "category_id": "food-dairy",
  "values": {
    "absolute": {
      "energy_kcal_per_serving": 120,
      "energy_density_kcal_per_100": 60,
      "serving_unit": "200g"
    },
    "relative": {},
    "proportional": {}
  },
  "provenance": [{
    "source_id": "custom-yogurt-assumption",
    "type": "scenario",
    "producer": "human",
    "source": "Experiment serving and energy assumptions",
    "paths": ["/values"]
  }],
  "version": 1
}
```

原始对象必填 `object_id`、`name`、`category_id`、`provenance`、正整数 `version`；
`values` 可省略以完全继承。三个属性表的含义见[品类规则](rules.md#品类契约)。
对象不能写编译后的 `edges` 或公式。每个数值必须恰好被一份来源记录覆盖；
来源路径用 JSON Pointer，例如 `/values/absolute/energy_kcal_per_serving`。

新增对象后还要在地图增加供给、在场景选择商品和供给，并刷新目录身份与地图身份。
如果只是初始持有且不可购买，可通过人物 `initial_inventory` 引用对象，无需假造门店供给。
食品的每份热量会显示给人物：按 5 进位取整到 10 千卡后写成「每份约 N 千卡」，不足 5 千卡写「几乎不含热量」；
所以份量要按一份实际吃掉的量声明，不要把按 100 克计的热量当成一份。
正式基线的食品生成器包含冻结白名单和品类映射；向食品语料随意追加一行不会自动得到完整可购买商品。

## 编写生活活动

活动目录固定读取 `lifestyle/items.json`，与资源目录 `activity_met` 中的 `lifestyle:<activity_id>` 必须一一对应。
只添加活动 JSON、遗漏 MET，或只加 MET、遗漏活动，都会拒绝加载。

| 活动字段 | 写法 |
| --- | --- |
| `activity_id`、`name_zh`、`category` | 身份和名称唯一；品类为 `sport`、`hobby`、`learning`、`household`、`social`、`wellness`、`pet`、`volunteer`、`outdoor` |
| `typical_duration_minutes` | 生成器要求 5–1440 分钟 |
| `description_zh`、`source` | 描述实际做什么，并写明活动、时长和 MET 的来源或假设 |
| `met_value` | 生成器要求 1–16；身体耗能使用的活动强度 |
| `location_kinds` | 非空闭集列表；活动允许额外使用 `anywhere` |
| `time_window` | 整数小时 `start_hour < end_hour`，范围 0–24；目前不能用一个区间表达跨午夜 |
| `needs_partner` | 布尔值 |
| `effects` | 恰好包含 `entertainment`、`social`、`health`，每项 0–1 |

当前 `location_kinds` 参与活动地点准入；`time_window` 和 `needs_partner` 已做数据形状校验，
但尚未接成运行时的时间或伙伴受理限制。`effects` 分别折算为娱乐缓解、社交缓解和健康相关情绪变化，
读取位置受上文的仓库固定路径限制；健康效果并不创建独立的“健康值”资源。

下面是 `enrichment.json` 的 `entries` 中一个**完整条目值**，可替换已有 `3d-printing` 条目的值：

```json
{
  "location_kinds": ["home", "venue"],
  "time_window": {"start_hour": 8, "end_hour": 23},
  "needs_partner": false,
  "effects": {"entertainment": 0.7, "social": 0.1, "health": 0.1}
}
```

补充文件只负责这四组字段；名称、描述、时长、MET 和活动清单仍由
[`scripts/build_lifestyle_corpus.py`](../../scripts/build_lifestyle_corpus.py) 内的来源映射生成。
当前生成器约束总量为 180–200 条，并要求 `/tmp/la_hf/` 中存在已核验的
`hobbies.csv`、`exercises.jsonl`、`meditation-dataset.json`、`events.csv`。
脚本当前没有可用的 `--hf-dir` 参数，也不会自动下载这些输入。

输入齐备后执行：

```bash
uv run python scripts/build_lifestyle_corpus.py
uv run python scripts/build_lifestyle_corpus.py --check
```

随后更新语料清单，重建资源目录并校验：

```bash
uv run python scripts/build_catalog.py
uv run python scripts/build_catalog.py --check
```

这些构建命令固定写仓库内的正式基线；它们不是任意自定义目录的通用导入器。

## 编写内容与工作材料

内容需要两层声明：语料文件提供元数据和时间轴，场景 `contents` 决定这次实验实际开放哪些条目。
只把文件放进 `corpora/` 不会让人物自动看到它。

| 素材 | 当前读取位置与格式 |
| --- | --- |
| 电影、剧集、网文元数据 | `movies/items.json`、`drama/items.json`、`novels/items.json`，顶层列表 |
| 新闻和段子 | `feed/items.json`、`jokes/items.json`；各自的 `feed-items.json` 还声明整条流的检索元数据 |
| 社交流 | `social/weibo/feed-items.json`、`social/zhihu/feed-items.json`、`social/movie_reviews/feed-items.json` |
| 游戏 | `games/library/items.json`，预生成文件由场景 `games` 引用 |
| 字幕体验 | `sintel/media-identity.json`，单个对象 |
| NASA 播客选段 | `podcasts/items.json`、官方页快照、音频选段与原始锚点身份文件；见下文 |
| 工作材料 | `scenario-work/materials.json` 的 `materials` 列表；任务另外引用实际输入文件和隐藏验收文件 |

内容元数据至少包含非空 `title`、非空列表 `genre`、非空 `language`、非负整数 `age_rating`。
检索还需要 `duration_seconds`；新闻、段子等阅读条目可用 `estimated_read_seconds`。
`genres` 这样的展示文本不能代替机器读取的 `genre` 列表。
当前世界素材准入要求剧集 `language: "zh"`，并执行目录清单中声明的年龄分级上限。

场景 `contents[]` 的完整条目形状如下；哈希必须从实际时间轴计算，不能照抄其他文件的值：

```json
{
  "item_id": "sintel-en-subtitles",
  "kind": "subtitle_experience",
  "duration_seconds": 630,
  "timeline_path": "sintel/sintel_en.srt",
  "timeline_sha256": "4ed7e1f1bc5ff69fe33606960e4c048ba6766237bf33b132fef76e1a92cb64b2",
  "entertainment_relief_per_hour": 0.3,
  "experience_label": "subtitle_experience"
}
```

`kind` 只接受 `feature_film`、`drama_series`、`podcast_episode`、`webnovel_excerpt`、`news_feed`、`joke_feed`、
`subtitle_experience`、`game_session`。时长为正整数秒，娱乐缓解率为每小时 0–1。
时间轴使用 SRT：片段编号、`HH:MM:SS,mmm --> HH:MM:SS,mmm`、正文，片段间空行；必须非空且不重叠。
人物按实际接触进度看到正文，不会直接看到整份文件。

现有 NASA `podcast_episode` 使用 Houston We Have a Podcast — Space Habitat 的
00:01:52–00:14:58 英文访谈选段，选段媒体时长 786 秒。
[官方页快照](../../corpora/podcasts/nasa-space-habitat.source.html)、
[原始锚点与音频身份](../../corpora/podcasts/nasa-space-habitat.identity.json)、
[本地音频选段](../../corpora/podcasts/nasa-space-habitat.excerpt.mp3)和
[派生时间轴](../../corpora/podcasts/podcast-0001.srt)随语料清单绑定。
`python scripts/build_nasa_podcast.py --check` 可按官方段落锚点复核 SRT 与身份文件。
只在媒体位置完整越过下一官方锚点后揭示该段文字；该时间轴没有逐词精度。
来源使用条件见[语料通知](../../corpora/LICENSES.md)，不得暗示 NASA 认可本项目。

### 配置可玩的游戏

`games/library/items.json` 是游戏的元数据目录；可执行的 TextWorld 游戏文件由场景 `games[]` 另行指定。
元数据条目不会自动生成可玩的游戏。以下是 `games[]` 中的**完整现有条目**：

```json
{
  "game_id": "textworld-1201",
  "seed": 1201,
  "game_path": "textworld/game-1201.json",
  "game_sha256": "4e0fc25651a001168e3c20f1b618f7c168a4cfb17e86fce82f40dcb6688cef43",
  "walkthrough_sha256": "9fb16a3c81cbe9c8146f927e8354bd4c2430072e9bd371f789978bc3afe15f6a"
}
```

五个字段都必填：`game_id` 非空且在场景中唯一，`seed` 为正整数，`game_path` 相对语料根目录。
游戏文件必须是能够被 TextWorld 启动的 JSON，并包含 `metadata.walkthrough`。
更改场景 `seed` 不会重新生成游戏；需要先准备对应的可执行文件。
`game_sha256` 是完整游戏文件的字节哈希，`walkthrough_sha256` 是攻略值规范序列化后的哈希，两者不同。

计算两个哈希的完整命令：

```bash
uv run python - <<'PY'
import hashlib
import json
from pathlib import Path

content = Path("corpora/textworld/game-1201.json").read_bytes()
walkthrough = json.loads(content)["metadata"]["walkthrough"]
encoded = json.dumps(walkthrough, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
print(json.dumps({"game_sha256": hashlib.sha256(content).hexdigest(),
                  "walkthrough_sha256": hashlib.sha256(encoded).hexdigest()}, indent=2))
PY
```

游戏文件还要加入语料清单。真正的启动校验见[游戏加载器](../../src/worldmock/life/game.py)；
仅 JSON 格式和哈希正确，不能代替 TextWorld 可启动性检查。

### 工作材料

工作材料要与任务中的 `input_path/input_sha256` 和 `oracle_path/oracle_sha256` 对应。
材料清单本身不会创建工作合同；还需在场景分配任务、地点、技能和受理人。
当前世界准入还要求中文、`type: "scenario"`、足量且互不重复的材料，并保留 AMI 元数据。

### 编写工作验收答案

工作输入是人物逐步接触的 UTF-8 正文。隐藏答案由一份 oracle JSON 和它引用的参考摘要文件组成，
不能把答案放进人物输入。可从[现有输入](../../corpora/scenario-work/inputs/work-0001.txt)、
[现有答案](../../corpora/scenario-work/references/work-0001.oracle.json)和
[现有参考摘要](../../corpora/scenario-work/references/work-0001.reference.json)复制。

下面是自定义 `scenario-work/references/custom-work.oracle.json` 的**完整示例**：

```json
{
  "type": "scenario",
  "material_id": "custom-work",
  "source_reference": "references/custom-work.reference.json",
  "expected": {
    "material_id": "custom-work",
    "decisions": ["首期仅开放关注关系内的图文发布。"],
    "action_items": ["产品经理更新需求说明中的发布范围。"]
  },
  "piece_count": 1
}
```

对应 `scenario-work/references/custom-work.reference.json` 的**完整示例**：

```json
{
  "type": "scenario",
  "material_id": "custom-work",
  "language": "zh",
  "summary_sentences": ["评审确定首期发布范围，产品经理负责同步需求说明。"]
}
```

`expected` 必须是对象；语义验收读取 `decisions`、`action_items` 两个列表，
并从参考文件加载非空字符串列表 `summary_sentences` 作为摘要。
`source_reference` 相对 oracle 路径的**第一个目录分量**解析：本例的基准是 `scenario-work/`，不是 `references/`。
计件合同必须提供非负整数 `piece_count`，实际付款按它计算；时薪和总包合同不用它确定金额。
输入、oracle 和参考文件都要进入语料清单，任务的 oracle 哈希只填写 oracle 文件本身的 SHA-256。
这些答案应准确对应输入正文；加载成功不等于模型语义验收一定通过。
当前工作交付仍固定为会议纪要结构 `MeetingMinutes`，包括摘要、决定和行动项；
换一个 `required_skill` 名称不会变成任意格式的文案、代码或其他交付任务。

### 正餐、家庭储备和联系人

这些文件还受目录 `category_requirements` 的世界素材要求约束。
它们与场景初值并存，修改素材清单不会自动改写人物的初始状态。

| 文件 | 写法与需要同步的内容 |
| --- | --- |
| [正餐](../../corpora/meals/items.json) | 顶层 `items`；每餐有 `meal_id`、名称、份量、每份热量、`energy_source`、`price_fen`、`meal_duration_minutes`、堂食或配送声明。热量来源需有数据库、URL、取得日期、每百克热量和换算说明，并有同名可消费目录对象 |
| [家庭储备](../../corpora/scene-stock/home-pantry.json) | `items[]` 的 `object_id` 必须是可消费目录对象，`quantity` 是份数；总量满足目录早餐储备要求。实际人物库存仍写 `agents[].initial_inventory` |
| [联系人素材](../../corpora/scenario-contacts/contacts.json) | `personas` 列表声明人物身份，`contacts[]` 写所属 `persona_id`、`contact_id`、`contact_type`、名称、渠道、映射和回复策略。同事映射已有人物、策略为 `persona`；老友和家人映射为 `null`、策略为 `model` |

联系人类型只支持 `colleague`、`old_friend`、`family`。真正用于运行的联系人仍需写入人物 `contacts`，
详见[人物画像](personas.md)。正餐语料中的价格是素材声明；实际交易取地图供给的报价，修改它不会自动更新门店。
从完整现有语料复制修改，可以保留当前要求的素材类别；不能只因这次场景未选择就删除固定准入所需文件。

## 修改后更新文件身份

哈希针对**实际文件字节**计算，缩进或换行变化同样会改变哈希。更新顺序如下：

```mermaid
flowchart TD
    F[编辑素材并更新来源说明] --> D[更新 dataset 中子文件 SHA-256]
    D --> M[更新语料 manifest 的大小和 SHA-256]
    M --> S[更新场景 corpus_manifest_sha256]
    D --> P[更新场景 map_dataset 版本和 SHA-256]
    F --> T[更新场景中的时间轴或任务文件 SHA-256]
    F --> C[对象或规则变化时刷新 catalog 身份]
    S --> V[运行加载与准入校验]
    P --> V
    T --> V
    C --> V
```

`corpora/manifest.json` 的每条记录要求 `entry_id`、`path`、`sha256`、`size_bytes`、`media_type`、`purpose` 和 `source`。
身份与路径均不能重复；路径不能逃出语料目录，不能包含符号链接。
`source` 必填 `kind`、`license`、`selector`，可填写 `url`、`retrieved_at`、`fact_cutoff`。
新增或替换素材时，要同步真实来源和使用许可，不能只刷新哈希。

计算单个文件身份的完整命令：

```bash
uv run python - <<'PY'
import hashlib
import json
from pathlib import Path

path = Path("corpora/sintel/sintel_en.srt")
content = path.read_bytes()
print(json.dumps({"sha256": hashlib.sha256(content).hexdigest(),
                  "size_bytes": len(content)}, indent=2))
PY
```

目前没有“给任意语料目录自动补清单”的通用 CLI。
`uv run worldmock prepare-corpora --output /tmp/worldmock-corpora` 会联网重新取得固定来源子集，
要求输出不存在；它不是扫描自定义文件并注册素材的命令。

## 验证修改

下面的完整离线命令针对前面创建的场景，检查实际加载、全部语料文件身份和世界素材要求：

```bash
uv run python - <<'PY'
from pathlib import Path
from worldmock.catalog import load_catalog
from worldmock.catalog.admission import validate_world_categories
from worldmock.corpora.manifest import load_manifest
from worldmock.life.scenario import load_scenario

path = Path("scenarios/my-resources.json").resolve()
scenario, _ = load_scenario(path)
root = (path.parent / scenario.corpus_root).resolve()
manifest = load_manifest(root, scenario.corpus_manifest_sha256)
catalog = load_catalog(path.parent.parent / "catalog")
result = validate_world_categories(catalog, root)
assert result.ok, [(item.code, item.detail) for item in result.violations]
print(f"Validated {len(manifest.entries)} corpus entries")
PY
```

正式运行前再执行完整项目校验：

```bash
uv run worldmock validate --scenario scenarios/my-resources.json
```

人物有 `background_story` 时，完整校验会调用模型编译画像并检查素材覆盖率，还会写出覆盖率报告。
它不属于纯离线检查。模型配置见[运行配置](runtime.md)。

| 常见错误 | 应检查什么 |
| --- | --- |
| `Corpus entry identity mismatch` | 清单中的大小、哈希是否对应修改后的文件 |
| `Map file hash mismatch` | 地图子文件和场景地图引用是否按依赖顺序更新 |
| `Selected map route coverage is incomplete` | 新增地点后的每个有向地点对、每个方式是否都有状态 |
| `Selected supply references an unselected product` | 场景是否同时选择了供给、商品、地点和卖方账户 |
| `Lifestyle catalog ids do not match activity_met lifestyle keys` | 活动生成结果与资源目录是否同步重建 |
| `CATALOG_CONTENT_IDENTITY_MISMATCH` | 规则或对象改变后是否刷新了目录清单 |
| `CATEGORY_*` 素材准入错误 | 固定路径、必填元数据、世界素材数量或可达性要求是否满足 |

实现细节可查[地图模型](../../src/worldmock/life/map_models.py)、[地图加载器](../../src/worldmock/life/map_data.py)、
[语料清单模型](../../src/worldmock/corpora/manifest.py)、[世界素材准入](../../src/worldmock/catalog/admission.py)
和[地图与门店供给规格](../dev/SPEC-map-and-storefront.md)。

[返回配置手册](README.md) · [场景配置](scenarios.md) · [人物画像](personas.md) · [规则配置](rules.md)
