> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-behavior/files.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 一日行为实际文件修改清单

相对开发基线 `74edeaddccf1e051a0f7c98024ec95eeb6c48514` 的独立工作树差异。生成物、测试、设计与来源均列出，
新增文件与已修改文件同属本次完整交付。最终验证状态见[实施台账](verification.md)。

## 设计文档的最终修改责任

| 文档 | 本次修改内容与权威边界 |
| --- | --- |
| 特性 SPEC、已裁决 PROPOSAL | SPEC 冻结全部人物行为和验收；提案只保留基线诊断和裁决来源，不再作为实施要求 |
| CONTRACTS | 世界版本、完整活动控制行、资源只读投影、组合请求与结果、主观记忆输入和可见引用；记录载荷链接记录引擎 |
| 总体架构与设计总览 | 保持交换、模拟、资源、记录四层职责；记录事件证明事实，人物只贡献主观表达 |
| 交换引擎设计 | 显式个人资源、组合逐子项菜单校验、暂存原子受理、独立控制和一次聚合终态 |
| 模拟引擎设计 | 活动集合推进、共同身体时刻、最大 MET 假设、效果合并、一次公共事件、具体威胁交付 |
| 资源引擎设计 | 官方音频及时间锚点身份、复用播放内容生命周期、正式餐馆显式餐食供给、通用空菜单合法 |
| 记录引擎设计 | 唯一身体区间事件、四种已核验经历、本人截止窗口、增量消费、主观记忆确定性视图和完整更新证据 |
| 评估设计 | 人物时间区间并集、活动时长允许重叠、真实食用与播放指标、事实和主观质量证据分别评价 |
| 配置设计 | 世界结构版本 5、正式场景菜单显式启用组合和子项、餐食供给覆盖、音频来源绑定 |
| 输入输出设计 | 删除旧活动槽，保存身体时刻与预警签名；当前活动集合、资源并集和已核验经历字段 |
| 生命周期与当日目标 SPEC | 同人并行、所有本人活动参与目标匹配、保持一次推进；持续威胁签名与交付语义 |
| 决策上下文与交互菜单 SPEC | 主观归纳和本轮事实窗口分开显示；组合不绕过子项菜单、动态可见目标与资源限制 |
| 真实性与地图门店 SPEC | 等待不伪造娱乐/进食；九种主观类型；餐馆实际发现、购买、交付与消费仍各自留证 |
| 素材与场景手册 | 正式三家餐馆供给身份、NASA 选段及重建命令、开启组合所需实际菜单配置 |
| INDEX、VERIFICATION、implementation-status、ANALYSIS-spec-coverage | 新入口、现行版本与验收门禁、条款测试追溯；历史收据保留原身份，不冒充本次结果 |

本次共涉及 182 个仓库路径；维护依赖的子模块内部文件另见末节。

## 设计与现行合同

- `docs/ARCHITECTURE.md`
- `docs/CONTRACTS.md`
- `docs/INDEX.md`
- `docs/VERIFICATION.md`
- `docs/design/01-architecture.md`
- `docs/design/02-exchange-engine.md`
- `docs/design/03-resource-engine.md`
- `docs/design/04-simulation-engine.md`
- `docs/design/05-recording-engine.md`
- `docs/design/07-evaluation.md`
- `docs/design/08-configuration.md`
- `docs/design/09-io-formats.md`
- `docs/design/README.md`
- `docs/design/SUMMARY.md`
- `docs/dev/ANALYSIS-spec-coverage.md`
- `docs/dev/PROPOSAL-dayrun-behavior.md`
- `docs/dev/SPEC-day-plan-goals.md`
- `docs/dev/SPEC-dayrun-behavior.md`
- `docs/dev/SPEC-decision-context.md`
- `docs/dev/SPEC-interaction-menu.md`
- `docs/dev/SPEC-life-realism.md`
- `docs/dev/SPEC-life-runtime.md`
- `docs/dev/SPEC-map-and-storefront.md`
- `docs/dev/coverage-report.json`
- `docs/dev/implementation-status.md`
- `docs/manual/resources.md`
- `docs/manual/scenarios.md`

## 实施证据与审查

- `docs/dev/research/dayrun-behavior/bob-activity/evidence-manifest.json`
- `docs/dev/research/dayrun-behavior/bob-activity/evidence.tar.gz`
- `docs/dev/research/dayrun-behavior/bob-activity/report.md`
- `docs/dev/research/dayrun-behavior/bob-experiences/evidence-manifest.json`
- `docs/dev/research/dayrun-behavior/bob-experiences/evidence.tar.gz`
- `docs/dev/research/dayrun-behavior/bob-experiences/final-document-review.md`
- `docs/dev/research/dayrun-behavior/bob-experiences/report.md`
- `docs/dev/research/dayrun-behavior/bob-memory/evidence-manifest.json`
- `docs/dev/research/dayrun-behavior/bob-memory/evidence.tar.gz`
- `docs/dev/research/dayrun-behavior/bob-memory/report.md`
- `docs/dev/research/dayrun-behavior/concurrency-design-review.md`
- `docs/dev/research/dayrun-behavior/dining-design-review.md`
- `docs/dev/research/dayrun-behavior/document-consistency-review.md`
- `docs/dev/research/dayrun-behavior/files.md`
- `docs/dev/research/dayrun-behavior/final-gates.json`
- `docs/dev/research/dayrun-behavior/final-gates.tar.gz`
- `docs/dev/research/dayrun-behavior/inputs-verification.md`
- `docs/dev/research/dayrun-behavior/memory-design-review.md`
- `docs/dev/research/dayrun-behavior/memory-verification.md`
- `docs/dev/research/dayrun-behavior/production-consistency-review.md`
- `docs/dev/research/dayrun-behavior/verification.md`

## 生产实现与维护依赖

- `custom/agents/life_activity_controls.py`
- `custom/agents/life_agent.py`
- `custom/agents/life_memory.py`
- `custom/agents/life_menu.py`
- `custom/envs/life_env.py`
- `scripts/build_catalog.py`
- `scripts/build_nasa_podcast.py`
- `scripts/check_reachability.py`
- `scripts/render_day.py`
- `src/worldmock/catalog/admission.py`
- `src/worldmock/catalog/affordances.py`
- `src/worldmock/evaluation/behavior_metrics.py`
- `src/worldmock/evaluation/validation.py`
- `src/worldmock/footprints/experiences.py`
- `src/worldmock/footprints/export.py`
- `src/worldmock/footprints/quality.py`
- `src/worldmock/footprints/store.py`
- `src/worldmock/footprints/validation.py`
- `src/worldmock/life/action_evidence.py`
- `src/worldmock/life/activities.py`
- `src/worldmock/life/activity_evidence.py`
- `src/worldmock/life/activity_validation.py`
- `src/worldmock/life/body.py`
- `src/worldmock/life/candidates.py`
- `src/worldmock/life/content.py`
- `src/worldmock/life/dining.py`
- `src/worldmock/life/engine.py`
- `src/worldmock/life/interaction_menu.py`
- `src/worldmock/life/memory_evidence.py`
- `src/worldmock/life/models.py`
- `src/worldmock/life/motivation.py`
- `src/worldmock/life/narration.py`
- `src/worldmock/life/observe.py`
- `src/worldmock/life/presentation.py`
- `src/worldmock/life/projection.py`
- `src/worldmock/life/rhythm.py`
- `src/worldmock/life/scenario.py`
- `src/worldmock/life/social_actions.py`
- `src/worldmock/status.py`
- `src/worldmock/validation.py`
- `vendor/agentsociety2`

## 正式输入与生成物

- `catalog/affordances.toml`
- `catalog/manifest.json`
- `catalog/resources.jsonl`
- `corpora/LICENSES.md`
- `corpora/manifest.json`
- `corpora/podcasts/items.json`
- `corpora/podcasts/nasa-space-habitat.excerpt.mp3`
- `corpora/podcasts/nasa-space-habitat.identity.json`
- `corpora/podcasts/nasa-space-habitat.source.html`
- `corpora/podcasts/podcast-0001.srt`
- `corpora/zhangjiang-map/dataset.json`
- `corpora/zhangjiang-map/supplies.json`
- `corpora/zhangjiang-map/supply-assumptions.json`
- `scenarios/multi-person-half-day.json`
- `scenarios/multi-person.json`
- `scenarios/white-collar-12-weeks.json`

## 回归与验收测试

- `tests/catalog/test_affordance_shadow.py`
- `tests/catalog/test_category_admission.py`
- `tests/catalog/test_reachability_admission.py`
- `tests/corpora/test_category_corpora.py`
- `tests/corpora/test_corpora.py`
- `tests/corpora/test_dayrun_podcast.py`
- `tests/evaluation/conftest.py`
- `tests/evaluation/test_behavior_metrics.py`
- `tests/evaluation/test_branch_coverage.py`
- `tests/footprints/test_branch_coverage.py`
- `tests/footprints/test_export.py`
- `tests/footprints/test_goal_export.py`
- `tests/footprints/test_quality.py`
- `tests/footprints/test_render_day.py`
- `tests/footprints/test_status.py`
- `tests/footprints/test_validation.py`
- `tests/footprints/test_verified_experiences.py`
- `tests/integration/activity_fixtures.py`
- `tests/integration/test_activity_schema.py`
- `tests/integration/test_agent_branch_coverage.py`
- `tests/integration/test_category_inventory_protocol.py`
- `tests/integration/test_dayrun_complete_days.py`
- `tests/integration/test_dayrun_parallel_agent.py`
- `tests/integration/test_decision_context_protocol.py`
- `tests/integration/test_env_lifecycle_coverage.py`
- `tests/integration/test_grounded_targets.py`
- `tests/integration/test_host_branch_coverage.py`
- `tests/integration/test_interaction_menu.py`
- `tests/integration/test_interaction_menu_roundtrip.py`
- `tests/integration/test_life_agent_protocol.py`
- `tests/integration/test_meal_checkpoint.py`
- `tests/integration/test_observation_agent_seam.py`
- `tests/integration/test_parallel_rejection_contract.py`
- `tests/integration/test_tool_description_references.py`
- `tests/integration/test_verified_memory.py`
- `tests/life/test_activities.py`
- `tests/life/test_activity_effects.py`
- `tests/life/test_activity_location_kinds.py`
- `tests/life/test_activity_menu.py`
- `tests/life/test_affordance_contract.py`
- `tests/life/test_candidate_wiring.py`
- `tests/life/test_candidates.py`
- `tests/life/test_category_runtime.py`
- `tests/life/test_category_runtime_edges.py`
- `tests/life/test_content_catalog_titles.py`
- `tests/life/test_day_plan.py`
- `tests/life/test_dayrun_podcast_playback.py`
- `tests/life/test_dayrun_restaurants.py`
- `tests/life/test_domain_branch_coverage.py`
- `tests/life/test_energy_feasibility.py`
- `tests/life/test_engine_branch_coverage.py`
- `tests/life/test_engine_feedback.py`
- `tests/life/test_export_integration.py`
- `tests/life/test_grounded_observation.py`
- `tests/life/test_interaction_menu_runtime.py`
- `tests/life/test_lifestyle.py`
- `tests/life/test_memory_evidence.py`
- `tests/life/test_money_settlement.py`
- `tests/life/test_motivation.py`
- `tests/life/test_narration.py`
- `tests/life/test_news_feed.py`
- `tests/life/test_opslog.py`
- `tests/life/test_parallel_acceptance.py`
- `tests/life/test_parallel_activities.py`
- `tests/life/test_retrieval.py`
- `tests/life/test_revision_contract.py`
- `tests/life/test_scenario.py`
- `tests/life/test_scheduling.py`
- `tests/life/test_shared_body.py`
- `tests/life/test_state_serialization.py`
- `tests/life/test_storefront_arrival.py`
- `tests/life/test_storefront_commerce.py`
- `tests/life/test_wait_passive_entertainment.py`
- `tests/life/test_wake_reasons.py`
- `tests/life/test_work_contracts.py`
- `tests/life/test_work_review.py`
- `tests/test_style_gate.py`

## AS2 子模块内部修改

基线 `ae3224ce982d52f1eb773a4f70521a7b60e19cba` → 本次 `8768e14df5c887c710fe96bf24981a2d8508346e`，共 5 个内部路径：

- `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/react.py`
- `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/memory.py`
- `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/memory_runtime.py`
- `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/person.py`
- `vendor/agentsociety2/packages/agentsociety2/tests/test_worldmock_life_hooks.py`
