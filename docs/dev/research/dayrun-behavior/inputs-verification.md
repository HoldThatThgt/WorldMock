> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-behavior/inputs-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 正式餐馆与 NASA 播客输入验收记录

本记录对应 [冻结规格](../../SPEC-dayrun-behavior.md)的真实餐馆进食路径和真实音频来源与揭示。
这两类输入已进入三个正式场景。此记录只说明输入、内容揭示及对应局部端到端验证；
完整自然日和人物模型行为由整体门禁另行验收。

## 交付文件

- 地图供给：`corpora/zhangjiang-map/supplies.json`、`supply-assumptions.json`、`dataset.json`。
  三家正式餐馆分别关联 `meal-0018`、`meal-0022`、`meal-0026`；供给均有正库存及自提。
  关联、报价和库存声明为 WorldMock 场景假设，不宣称真实门店菜单。
- 三个正式场景：`scenarios/white-collar-12-weeks.json`、`multi-person.json`、
  `multi-person-half-day.json` 选中上述供给和同一播客；注意力画像含 `podcast_episode`。
- 音频语料：`corpora/podcasts/nasa-space-habitat.source.html`、
  `nasa-space-habitat.excerpt.mp3`、`nasa-space-habitat.identity.json`、
  `podcast-0001.srt`、`items.json`；身份及使用条件见 `corpora/LICENSES.md`。
- 生成和运行：`scripts/build_nasa_podcast.py`、`scripts/build_catalog.py`、
  `scripts/check_reachability.py`、`src/worldmock/catalog/admission.py`、
  `src/worldmock/life/{scenario,content,dining}.py`、`src/worldmock/validation.py`。
- 文档：`docs/design/{03-resource-engine,08-configuration}.md`、
  `docs/dev/SPEC-map-and-storefront.md`、`docs/manual/{resources,scenarios}.md`。
- 测试：`tests/corpora/{test_corpora,test_dayrun_podcast,test_category_corpora}.py`、
  `tests/catalog/{test_category_admission,test_reachability_admission}.py`、
  `tests/life/{test_scenario,test_dayrun_restaurants,test_dayrun_podcast_playback,`
  `test_activity_menu,test_content_catalog_titles,test_retrieval}.py`、
  `tests/footprints/test_validation.py`。

## 来源与身份

[NASA 官方节目页](https://www.nasa.gov/podcasts/houston-we-have-a-podcast/space-habitat/)
标识 Houston We Have a Podcast 的 *Space Habitat*、第 1 季第 24 集。
本地材料截取官方音轨的 00:01:52 至 00:14:58，共 786 秒。原始节目 MP3 URL、
原始音频 SHA、官方页 SHA、所有选中原始段落锚点及段落在选段内的时间轴均进入身份文件。
片段用 ffmpeg 9.0.1 自官方 MP3 提取，片段 SHA 为
`f298a5f15c489c2efbc1e730647665532bb1a8ff3737ea5453a5e08ca44fff25`。
原始音频 SHA 为
`55bf6bb73f4d5d58e50319f3a3db8c93cf396565e988621dd991d3fc434b6e7c`。
段落只以下一官方锚点确定完成时刻；没有逐词转录或词速推断。

[NASA 媒体使用规范](https://www.nasa.gov/nasa-brand-center/images-and-media/)
说明其音频等内容通常在美国不受版权保护，可按规范用于教育或资讯用途；
须承认 NASA 是素材来源，不暗示 NASA 审核、授权或背书项目；
第三方权利和可识别人物的商业使用另有条件。NASA 的 AI 应用规范不允许把模型生成输出归因于 NASA。
此处 NASA 仅是冻结原始素材的来源，人物主观文字不是 NASA 输出。

## 可复核结果

- 语料清单 `corpora/manifest.json`：
  `965c9fafd40845e40a449e497f1ed40b97f20c8a064ed5526c910f81254ff5bf`，
  `load_manifest(Path('corpora'))` 返回 `life-dayrun-behavior-2026-09-21`、970 项。
- 地图 `corpora/zhangjiang-map/dataset.json`：
  `5adabfd2aade46211b8d71045b47342694166ca1bc1eb44af2107ff52f91d0f6`。
- `.venv/bin/python scripts/build_nasa_podcast.py --check`：通过，三项派生输出逐字节匹配。
- `.venv/bin/python scripts/build_catalog.py --check`：目录四项产物逐字节匹配。
- `.venv/bin/python scripts/check_reachability.py --scenario scenarios/multi-person-half-day.json`：退出码 0。
- `pytest -q tests/corpora tests/catalog/test_category_admission.py`
  `tests/catalog/test_reachability_admission.py tests/life/test_scenario.py`
  `tests/life/test_dayrun_restaurants.py tests/life/test_activity_menu.py`
  `tests/life/test_content_catalog_titles.py tests/life/test_retrieval.py`
  `tests/footprints/test_validation.py::test_build_coverage_targets_derives_from_catalog_and_corpora`：
  242 passed。之后餐馆空菜单、闭店、售罄及完整播放跨锚点用例新增，
  `pytest -q tests/life/test_dayrun_restaurants.py tests/life/test_dayrun_podcast_playback.py`：4 passed。
  `pytest -q tests/life/test_dayrun_podcast_playback.py tests/corpora/test_dayrun_podcast.py`：5 passed。
  新增播放与等待独立控制回归后，`pytest -q tests/life/test_dayrun_podcast_playback.py`：3 passed。

真实到店用例通过路线、到达、开门、首轮观察可见供给、自提付款、库存扣减和正时长消费。
一般餐馆没有选中供给时场景仍合法，观察如实报空；闭店及售罄不报可购。
播客用例通过真实 `watch` 检查暂停期间媒体位置不动、变速后完整跨段才揭示，
以及 786 秒末锚点完成。期间曾发现通用字幕去重规则丢弃跨结算区间完成的播客段落，
由活动引擎归属工作者修正后上述五项用例通过。

目前尚未人工逐字听校 786 秒音频与官方段落；使用官方页公开的段落级锚点精度，
时间轴不声称逐词精度。完整自然日和供应商长跑不由本局部门禁证明。

## 人物工具集成接续验收

额外接手了人物层旧夹具适配：`tests/integration/test_grounded_targets.py`、
`test_activity_schema.py`、`test_interaction_menu.py`、`test_agent_branch_coverage.py`，
并扩展 `tests/integration/activity_fixtures.py`。新增
`tests/integration/test_dayrun_parallel_agent.py` 验证真实动态组合 Schema 的子项可见身份、
本地配对、资源相交原子拒绝、一次类型化宿主提交、两个活动各自控制、社交仅取消和畸形观察失败关闭。
五组一起运行：227 passed。对应真实引擎的播放加等待独立启动和点名暂停在上述播放用例中执行。

人物层当前严格验证 `occupied_resources` 的类型、闭集、排序和去重；
它不从八键活动控制行重建完整资源并集。环境按真实活动资源声明最终校验并拒绝冲突，
整体实现负责人确认无需增加 travel 来源协议。畸形结构在人物层失败关闭，
同闭集但语义错误的资源投影由环境最终拒绝，不将人物层视为授权事实源。

输入/内容/餐馆与这五组人物工具测试的一次联合运行结果：473 passed，21.64 秒。

## 组合活动与库存协议接续验收

`docs/design/08-configuration.md` 的世界状态版本表已改为 5，与当前状态模型及其他设计一致。
库存人物协议原测试把所有观察供给都当作可买，正式场景新增异地且仅能自提的餐食后该断言失真；
模型购买枚举现在按真实协议收窄到 `purchase_allowed` 且具有可执行配送或本地自提的供给。

新增 `tests/life/test_parallel_acceptance.py` 只使用正式 `start_parallel_activities` 决策动作建活动，
覆盖播放分别结合步行、食用、使用、等待及生活活动；先到达、先播完、同时结束；
两个子活动的独立暂停、恢复、取消和播放变速；组合原动作在所有子项终结后只写一次聚合终态；
实验截止逐项截断；库存不足及游戏／社交资源冲突没有留下活动、库存、资金、游戏或邀请半成品。
该测试以正式场景的隔离副本设置白天时刻和初始持有物，不在状态里预置两个 ACTIVE。

`AGENTSOCIETY_LLM_API_KEY=test-only ... .venv/bin/python -m pytest -q
tests/integration/test_category_inventory_protocol.py tests/life/test_parallel_acceptance.py
tests/life/test_parallel_activities.py tests/integration/test_dayrun_parallel_agent.py`：
63 passed，11.78 秒。新文件单独为 14 passed，6.40 秒；`git diff --check` 通过。
