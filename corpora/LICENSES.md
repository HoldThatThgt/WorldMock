# Corpus notices

- Open Food Facts data: ODbL 1.0; https://world.openfoodfacts.org/terms-of-use
- Sintel subtitle and film identity: CC BY 3.0; https://durian.blender.org/sharing/
- AMI annotations: CC BY 4.0; https://groups.inf.ed.ac.uk/ami/download/
- OpenStreetMap and derived routes: ODbL 1.0; https://www.openstreetmap.org/copyright
- TextWorld software generated games: Microsoft TextWorld 1.7.0, MIT license.
- WorldMock mail and catalog rules: project-authored scenario facts, WorldMock project terms.
- Chinese news feed (title/summary pairs): Apache-2.0 per dataset card; https://huggingface.co/datasets/feilongfl/ChineseNewsSummary (GPT-3.5-written Chinese summaries of RSS-crawled news)
- Douban movie metadata and synopses (movie library cards): research-use, no license stated; https://huggingface.co/datasets/MangoGoes/douban_movie_info (douban.com crawled facts)
- Chinese webnovel excerpts: Apache-2.0; https://huggingface.co/datasets/wdndev/webnovel-chinese
- Visual novel dialogue excerpts (drama series): Apache-2.0; https://huggingface.co/datasets/alpindale/visual-novels (PygmalionAI parsed scripts)
- Chinese joke feed: Apache-2.0; https://huggingface.co/datasets/notsobad9527/chinese-joke
- Video game library cards (game sessions): curated public catalog facts, descriptions project-authored; CC-BY-4.0; no upstream dataset within the 100MB budget carried usable metadata (see corpora/games selector note)
- Lifestyle activity catalog backbone: MIT; https://huggingface.co/datasets/alperugurcan/Hobbies (synthetic hobby rows, ChatGPT-generated per dataset card)
- Calisthenics category aggregation (sport activities): Apache-2.0; https://huggingface.co/datasets/Martjn/calisthenics_exercises
- Meditation themes (wellness meditation activities): CC-BY-4.0; https://huggingface.co/datasets/berdaniera/meditation
- Outing culture activity types: derived type keywords only (no event content reproduced); https://huggingface.co/datasets/verdantdavid/gsv3_upcoming_events
- MET values: Compendium of Physical Activities 2011 (Ainsworth et al., MSSE 2011 supplement); exact codes cited where reproducible, nearest-match values marked (assumption) per item in source field

## 地图与门店供给发布

`zhangjiang-map/dataset.json` 固定 WGS84、选区、时区、版本及地点、门店供给和路线文件哈希。
地图地点及道路来源为 OpenStreetMap，保留 **© OpenStreetMap contributors**，许可为
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/)，归属说明见
[OpenStreetMap copyright](https://www.openstreetmap.org/copyright)。许可判断依据来源及实际交付形态，
不以是否包含共享条款的布尔值判断不同资料能否混合。

每条路线保存实际请求、路由实例、foot/car 配置、取得时刻与不可变响应哈希；二百二十条冻结路线
不等于二百二十份同日取得的资料，原始有效响应保留原取得日期。公开实例无法固定历史路网版本，
因此路线和地点快照只说明取得时的空间资料，不能当作 2026 年一月至三月实验日期的历史事实。

`locations.json` 内的营业日历及 `supplies.json`、`supply-assumptions.json` 中的商品分配、价格、
库存、处理时长和履约范围是 WorldMock 场景假设，采用 `WorldMock-project` 来源声明；它们不代表
具名现实门店的已核实供给。每份营业与报价声明保留生效区间、核对日期、可信截止日及来源限制。
源资料和场景假设在运行及交付清单中分别保留，地图衍生内容的对外使用须保留以上归属与许可通知。

准备新路线使用 `uv run python scripts/prepare_map.py --source-root corpora --destination NEW_CORPUS`。
该命令以当前已审阅地点与供给为输入，按完整有向地点对查询路由，每秒至多一次请求，验证响应
实际出行方式后原子发布新目录；任何未知响应或方式不符都会中止，不以驾驶路线代替步行路线。
