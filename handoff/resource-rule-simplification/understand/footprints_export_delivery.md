# Subsystem map: event store, world state models, checkpoints, export, quality, LabelKit delivery, evaluation

Read-only survey of the worktree `D:/code/WorldMock/.claude/worktrees/rrs` (HEAD `35d0b61`, which is the proposal's code
baseline `7e1ffab` plus the Windows path hardening of `footprints/checkpoint.py`) for the proposal
`docs/dev/PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md`. The WorldMock-Dataset checkout on this machine
(`D:/code/WorldMock-Dataset`, `main` at `2cff671`, 2026-10-06 13:15 +08:00) now has `environment/zhangjiang-baseline/` and the
repository-level phone-handoff scripts `tools/labelkit_input.py` and `tools/simulate_phone.py`, which are the ones read below
(`requests/0001-white-collar-lianqiu/prepare_labelkit_input.py` is now a shim that delegates to `tools/labelkit_input.py`). The
three formal scenarios in `environment/zhangjiang-baseline/scenarios/` (structure 6) carry `phone_simulation_config = null`, so
`tools/simulate_phone.py::bound_to_scenario` cannot run against them today and no purchase channels are derived for them
(`life/load_checks` returns an empty channel set). An earlier pass of this survey read an older dataset checkout; those
statements were corrected in sections 3.7 and 5.8.

Terminology below: "footprints" = `src/worldmock/footprints`, "delivery" = `src/worldmock/delivery`, "evaluation" =
`src/worldmock/evaluation`, WS9 = `WorldState.schema_version == 9`, WS10 = the proposed next version.

---

## 1. Inventory

### 1.1 Production files in scope (line counts from `wc -l`)

| File | Lines | One-line responsibility |
| --- | --- | --- |
| `src/worldmock/footprints/__init__.py` | 1 | package marker |
| `src/worldmock/footprints/models.py` | 212 | Event/EventDraft/Checkpoint/AssetRef pydantic contracts, `EventType` closed set, `WAKE_REASON_NAMES` closed set, `resource_flows` and `decision_started` validators, `canonical_json`/`sha256_json`/`is_sha256` |
| `src/worldmock/footprints/store.py` | 334 | `FootprintStore`: SQLite schema (append-only `events`, singleton `world_state`, `artifacts`, immutable `checkpoints`), atomic `commit(state, drafts)`, version/clock/causality checks, agent-indexed read queries, `checkpoint`/`get_checkpoint` |
| `src/worldmock/footprints/ledger.py` | 79 | money conservation per commit (`validate_account_transition`) and per export (`validate_account_history`) from `data.resource_changes["account:*"]` |
| `src/worldmock/footprints/assets.py` | 72 | `atomic_write` (fsync + replace) and content-addressed `AssetStore` (`put`, `put_json`, `read`) |
| `src/worldmock/footprints/checkpoint.py` | 159 | workspace tar snapshot/restore (member-name safety checks shared by snapshot and restore) and `restore_branch` (resume / counterfactual child run from a checkpoint) |
| `src/worldmock/footprints/evidence.py` | 26 | `redact_evidence` for model request/response secrets |
| `src/worldmock/footprints/intervals.py` | 28 | `valid_interval`, `is_body_settlement`, `body_settlements` shared by quality and evaluation |
| `src/worldmock/footprints/experiences.py` | 277 | verified experiences: validation of the 5-row observation window and projection of `waited`/`consumed`/`arrived`/`played` facts from `activity_progress` |
| `src/worldmock/footprints/purchases.py` | 104 | `purchases.jsonl` projection: `purchase` (from `action_result.data.orders[]`/`activity_results[].orders[]`, with `channel_id`), `order_attempt` (by way synonyms), `app_browse` (browse activities) |
| `src/worldmock/footprints/export.py` | 184 | `export_run`: `events.jsonl`, `activities.jsonl` (`reconstruct_activities`), `verified-experiences.jsonl`, `purchases.jsonl`, `asset-index.jsonl`, `quality.json`, `timeline/<agent>-<date>.md`, export `manifest.json` (schema 2) |
| `src/worldmock/footprints/validation.py` | 174 | `validate_export`: re-authenticates a complete export (manifest schema 2, final checkpoint, event order, assets, ledger, recomputation of activities/experiences/purchases/quality) |
| `src/worldmock/footprints/quality.py` | 1001 | `build_quality_report` (schema 4): run outcome, actions, activities, content contact (repeat watch), conversion efficiency (work/food), decision rhythm, memory references, model usage/cost, acceleration, day-plan/errand metrics, input warnings, timeline labels, contact reply guard, name rejections, truncations |
| `src/worldmock/footprints/timeline_labels.py` | 115 | timeline label check (English kind names / internal ids), `window_days`, `local_day_bounds`, `split_local_days`, loader of `scripts/render_day.py` |
| `src/worldmock/delivery/__init__.py` | 1 | package marker |
| `src/worldmock/delivery/windows.py` | 361 | `prepare_windows`: action / intent / dreaming evidence windows from a validated export; decision-prompt selection; public field projection of events; progress evidence expansion |
| `src/worldmock/delivery/evidence_encoding.py` | 250 | RFC 6901 field-dictionary encoding/decoding of model inputs (`encode_model_record`/`decode_model_record`) |
| `src/worldmock/delivery/pipeline.py` | 348 | `deliver`: WS version gate, LabelKit CLI execution, source index/trace binding, semantic verification, training loader, rule fixtures, delivery `manifest.json` (schema 2, copies `frozen_run_manifest.map_dataset`) |
| `src/worldmock/delivery/trace_validation.py` | 302 | LabelKit trace lifecycle/processing validation |
| `src/worldmock/delivery/semantics.py` | 101 | `validate_annotation`/`attach_facts` for semantic candidates (claims cite allowed evidence ids) |
| `src/worldmock/delivery/rules.py` | 100 | causal checks over rule-dict events; `execute_fixture` with `rule_engine` |
| `src/worldmock/delivery/training.py` | 96 | HF `datasets` loading, split assignment by source connected components |
| `src/worldmock/delivery/generation.py` | 106 | rules catalog from the first action window; verification of LabelKit sequence generation; rule fixtures |
| `src/worldmock/delivery/replay.py` | 106 | dreaming windows from a counterfactual replay export |
| `src/worldmock/evaluation/__init__.py` | 1 | package marker |
| `src/worldmock/evaluation/adapters.py` | 805 | LabelKit legacy and WorldMock exports -> common `EvidenceEvent`/`ActivitySession`/`DayRecord`; strict activity row field set |
| `src/worldmock/evaluation/public_evidence.py` | 676 | closed-set public structure projection (`PublicEvidence`) from legacy phone data and WorldMock action/result/environment events |
| `src/worldmock/evaluation/behavior_metrics.py` | 841 | protocol gates and report-only behaviour metrics from events (content contact, meals, sleep, families, JS distance, workload hook) |
| `src/worldmock/evaluation/workload_metrics.py` | 461 | `workload` / `routine_deviation` from shifts, task sites, work activity `activity_efficiency`, task payment settlement |
| `src/worldmock/evaluation/errand_metrics.py` | 263 | errand metrics (`registered_count`...) and `conclusion_memories` |
| `src/worldmock/evaluation/goal_metrics.py` | 320 | day-plan metrics (habit, external constraints, deliberate goals) |
| `src/worldmock/evaluation/metrics.py` | 909 | comparability, support, distributions, statistics between two `DatasetView`s |
| `src/worldmock/evaluation/review.py` | 1034 | blind review packets (identity key rejection list includes `route_id`, `product_id`, `order_id`, ...), ingest/unblind |
| `src/worldmock/evaluation/models.py` | 316 | common dataclasses (`TimeWindow`, `EvidenceEvent`, `ActivitySession`, `PublicEvidence`, `DayRecord`, `DatasetView`, `EvaluationScenario`) |
| `src/worldmock/evaluation/validation.py` | 382 | legacy LabelKit delivery chain validation; canonical WorldMock export validation wrapper |
| `src/worldmock/evaluation/runner.py` | 309 | `prepare_evaluation` / `finalize_evaluation` |
| `src/worldmock/evaluation/reporting.py` | 345 | report rendering and charts |
| `src/worldmock/evaluation/scenario.py` | 220 | evaluation scenario loader (`category_mappings.worldmock_activity`, `official_domain_map`, `expected_labelkit`) |
| `src/worldmock/evaluation/aggregate_reference.py` | 152 | official aggregate diagnostic |
| `src/worldmock/evaluation/causality.py` | 56 | runs `delivery.rules.evaluate_causal_events` |
| `src/worldmock/evaluation/decision.py` | 156 | result status decision |
| `src/worldmock/evaluation/io.py` | 136 | json/jsonl readers, atomic writers |
| `src/worldmock/evaluation/errors.py` | 15 | `EvaluationError` |
| `src/worldmock/life/models.py` | 1601 | all persisted life models: `WORLD_SCHEMA_VERSION = 9`, enums, `BodyState`, `ContentProgress`, `SessionState`, `AgentState`, `ActivityState`, `DeliveryState`, `SupplyQuoteState`, `WorldState` + invariants, request models (`PurchaseOrderRequest`, `ConsumeRequest`, `SubmitWorkRequest`, `MeetingMinutes`, control tools), `ActionResult`, DeepSeek strict schema check |
| `src/worldmock/life/sessions.py` | 192 | location sessions (stay/transit) lifecycle in `WorldState.sessions`, no events |
| `src/worldmock/life/state_commit.py` | 94 | commit choke-point (`commit_state` revalidates `WorldState`), presentation staging/flush, supply-visibility flush |
| `scripts/render_day.py` | 334 | deterministic `timeline/<agent>-<date>.md` renderer from `activity_progress` + name index from observations/results |
| `scripts/inspect_run.py` | 869 | read-only run browser: summary/events/money/body/flows, `rerun` (8 metrics incl. walking share via `data.routes`), `continuation`, `workday`, `queries` |

Adjacent but not listed in the scope hints and read by this subsystem's tests: `src/worldmock/status.py` (reads `world_state` and
events for `status.json`; `tests/footprints/test_status.py`), `src/worldmock/life/readable.py` (Chinese label tables used by
`render_day.py` and `timeline_labels.py`), `src/worldmock/life/activity_titles.py::activity_snapshot` (the activity snapshot written
into every activity-bearing event and thus into `activities.jsonl`).

### 1.2 Key symbols that implement the current behaviour

Event store and contracts
- `src/worldmock/footprints/models.py::EventType`, `::EventDraft`, `::Event` (schema 1), `::Checkpoint` (schema 1), `::AssetRef`,
  `::RESOURCE_FLOWS_FIELD`, `::validate_resource_flows`, `::WAKE_REASON_NAMES` (25 members incl. `delivery_arrival`,
  `task_available`, `task_due_soon`, `work_review_result`, `discovery_result`), `::validate_decision_rhythm`,
  `::DECISION_STARTED_FIELDS`, `::validate_decision_started`, `::MESSAGE_ARRIVAL_KIND`, `::canonical_json`, `::sha256_json`, `::is_sha256`
- `src/worldmock/footprints/store.py::SCHEMA`, `::FootprintStore.create/commit/_validate_state/_build_events/_validate_references/checkpoint/get_checkpoint/_validate_participants/last_agent_action_request_seq/read_recent_experience_sources/commit_last_seq`
- `src/worldmock/footprints/ledger.py::validate_account_transition`, `::validate_account_history`, `::_money_changes`
- `src/worldmock/footprints/checkpoint.py::snapshot_directory`, `::_workspace_files` (rejects symlinks and junctions), `::restore_directory`, `::_check_members` (regular files only, portable names, no case/Unicode aliases, no file-as-parent), `::restore_branch`, `::_populate_branch`, `::_branch_metadata`
- `src/worldmock/footprints/assets.py::atomic_write`, `::AssetStore`

World state
- `src/worldmock/life/models.py::WORLD_SCHEMA_VERSION`, `::ActivityKind` (wait, sleep, work, watch, play_game, travel, read_mail, browse, consume, lifestyle, chat, call), `::TravelMode`, `::FulfillmentKind`, `::ContractKind`, `::ContentProgress`, `::SessionState`, `::AgentState`, `::ActivityState`, `::DeliveryState`, `::SupplyQuoteState`, `::SameMomentPresentation`, `::WorldState` + `::_validate_activity_collection`, `::_validate_session_collection`, `::_expected_activity_claims`, `::_validate_supply_visibility`, `::_validate_opening_exceptions`, `::PurchaseItem`, `::PurchaseOrderRequest`, `::ConsumeItem`, `::ConsumeRequest`, `::MeetingMinutes`, `::SubmitWorkRequest`, `::SubmitWorkActionRequest`, `::StartActivityRequest`, `::REQUEST_MODELS`, `::ActionResult`, `::DataSource`, `::OpeningInterval`
- `src/worldmock/life/sessions.py::current_session`, `::open_stay`, `::begin_transit`, `::end_transit`, `::route_ended`, `::assign`, `::end_all`
- `src/worldmock/life/state_commit.py::validated_payload`, `::commit_state`, `::stage_presentation`, `::flush_presentation`, `::flush_supply_visibility`
- `src/worldmock/life/storefront_state.py::map_provenance` (the `map_dataset` block frozen into the run manifest: `dataset_id`, `version`, `random_seed`, `category_assignment_sha256`, `coordinate_system`, `timezone`, `prepared_at`, `source`, `locations_file`, `supplies_file`, `routes_file`) and `::validate_storefront_state` (open/restore identity check)

Export, quality, timeline
- `src/worldmock/footprints/export.py::export_run`, `::_write_export`, `::_export_identity` (manifest schema 2), `::reconstruct_activities`, `::_activity_record`, `::_write_timeline_projections`, `::write_jsonl`
- `src/worldmock/footprints/validation.py::validate_export`, `::_validate_identity` (artifact closed set `{events, activities, verified_experiences, purchases}`), `::_validate_checkpoint`, `::_validate_events`, `::_validate_assets`, `::_validate_quality`
- `src/worldmock/footprints/experiences.py::_KIND_FIELDS` (waited/consumed/arrived/played), `::validate_verified_experiences`, `::project_verified_experiences`, `::recent_verified_experiences`, `::_consumed` (reads `data.consumption.items[].{product_id, interval_quantity, cumulative_quantity}`), `::_played` (reads `activity.data.revealed.position_before/after_seconds`, `target_id` as `content_id`), `::_arrival` (reads `activity.data.arrival.location_id/access_status`)
- `src/worldmock/footprints/purchases.py::project_purchase_facts`, `::_purchases` (`supply_id`, `channel_id`), `::_order_attempts`, `::_app_browses`, `::ORDER_WAYS`, `::BROWSE_ACTIVITY_KIND`
- `src/worldmock/footprints/quality.py::build_quality_report` (schema 4), `::_content_quality` (`_CONTENT_TYPES = {watch, read_mail, play_game, browse}`), `::_watch_repeats` (`data.repeat`), `::_efficiency_quality`, `::_work_efficiency_samples` (`activity_efficiency`), `::_food_efficiency_samples` (`consumption.food_efficiency`), `::_decision_rhythm_quality`, `::_memory_quality`, `::_model_quality`/`::_usage_by_purpose` (`work_review`, `plan_day`), `::_timeline_label_report` via `timeline_labels`, `::_name_rejection_quality`, `::_truncation_quality`, `::_PRICING`
- `src/worldmock/footprints/timeline_labels.py::timeline_label_report`, `::label_findings`, `::window_days`, `::local_day_bounds`, `::split_local_days`, `::_load_render_day`
- `scripts/render_day.py::NAME_KINDS` (`products, locations, messages, contents, lifestyles, tasks, people`), `::_absorb_observation`, `::name_index`, `::_LABELERS` (travel/watch/play_game/read_mail/consume/browse/lifestyle/work/call/chat), `::day_rows`, `::render_day_markdown`
- `scripts/inspect_run.py::rerun_metrics` (`walking_share` reads `observation.data.routes[].mode`; `eat_progress` reads `consumption`), `::continuation`/`::_repeat_orders` (purchase facts by `product_id`), `::workday` (scenario tasks, payment settlement), `::query_cost_report`

Delivery
- `src/worldmock/delivery/windows.py::PRIVATE_EVENTS`, `::OBSERVATION_LAYER_KEYS`, `::PROGRESS_EVIDENCE_EVENTS`, `::prepare_windows`, `::_action_windows`, `::_decision_prompt_source`, `::_attach_decision_layers`, `::_record`, `::_visible_event` (expands `artifact_refs` text/json materials), `::_public_event_data`, `::_attach_evidence`, `::_window_identity`, `::_dream_windows`, `::_activity_boundaries`
- `src/worldmock/delivery/pipeline.py::deliver`, `::_require_current_world_schema` (== `WORLD_SCHEMA_VERSION`), `::_execute_delivery`, `::run_labelkit`, `::write_model_inputs`, `::verify_process`, `::_model_source`
- `src/worldmock/delivery/generation.py::create_catalog`, `::generation_fixtures`, `::EXPECTED_VARIANTS`, `::EXPRESSIONS`
- `src/worldmock/delivery/training.py::load_training`, `::assign_splits`, `::_source_identities`
- `src/worldmock/delivery/replay.py::replay_windows`

Evaluation
- `src/worldmock/evaluation/adapters.py::_DIGITAL_ACTIVITY_TYPES` (`work, watch, play_game, read_mail, browse`), `::_adapt_world_activity`, `::_require_activity_fields` (closed set incl. `effective_work_seconds`, `media_position_seconds`, `route_progress_meters`, `travel_mode`, `playback_speed`), `::_world_common_channel`, `::parse_world_event`
- `src/worldmock/evaluation/public_evidence.py::_LEGACY_ACTIONS`, `::_TARGETED_ACTIONS` (`watch -> content`, `play -> game`, `browse -> content`), `::_ACTION_OF_KIND`, `::_start_shape` (travel object_key = `activity.data.route_id`), `::_activity_object_key` (`route_id`), `::_purchase_result`, `::_work_result` (`work_review.accepted`, `payment_fen`), `::_world_environment` (`mail_arrival`, `delivery_ready/arrival`, `work_available`, `task_expired`), `::_world_request_shape` (`submit_work`), `::worldmock_activity_category`
- `src/worldmock/evaluation/behavior_metrics.py::_content_contact_minutes` (`revealed`), `::_meal_sessions` (`consumption.items[].product_id`), `::_family_of`, `::_activity_distribution`, `::behavior_metrics_report`
- `src/worldmock/evaluation/workload_metrics.py::WORKLOAD_KEYS` (`effective_work_seconds`), `::TaskSite`, `::workday_inputs`, `::_settled_tasks` (reads `task_id`+`payment_fen` in action results), `::workday_reports`
- `src/worldmock/evaluation/review.py::_reject_identity_keys` (forbids `route_id`, `product_id`, `order_id`, ... in blind packets)
- `src/worldmock/evaluation/scenario.py::_ROOT_KEYS` / `category_mappings.worldmock_activity`

### 1.3 Importers (rg over `src/`, `custom/`, `scripts/`, `tests/`)

`worldmock.footprints` is imported by 70 production modules and 125 test modules. Production importers outside the package:
`custom/agents/life_agent.py` (`experiences.validate_verified_experiences`), `custom/envs/life_env.py` (`checkpoint.snapshot_directory`),
`scripts/inspect_run.py`, `scripts/render_day.py`, `src/worldmock/{cli,status,validation,runtime,run_setup,model_feedback}.py`,
`src/worldmock/catalog/builder.py`, `src/worldmock/categories/{identity,maintenance}.py`, all of `src/worldmock/delivery/*`,
`src/worldmock/evaluation/{adapters,behavior_metrics,goal_metrics,io,public_evidence,review,scenario,validation,workload_metrics}.py`,
and 34 modules under `src/worldmock/life/` (`engine`, `engine_context`, `world_setup`, `world_ops`, `time_advance`, `travel`,
`observe`, `session_actions`, `work_review`, `counterfactual_runtime`, `run_identity`, `memory_*`, `schedule`, ...), mostly for
`EventDraft`, `canonical_json`/`sha256_json`, `RESOURCE_FLOWS_FIELD`, `WAKE_REASON_NAMES`.

`worldmock.delivery` importers: `src/worldmock/cli.py` (`deliver`), `src/worldmock/validation.py` (`generation.EXPRESSIONS`,
`rules.execute_fixture`), `src/worldmock/evaluation/{causality,runner}.py`, and tests `tests/delivery/*`, `tests/evaluation/{test_branch_coverage,test_runner}.py`,
`tests/footprints/test_validation.py`, `tests/integration/{test_dayrun_complete_days,test_errand_delivery_windows,test_host_branch_coverage,test_same_second_continuation}.py`,
`tests/life/test_export_integration.py`, `tests/errands_workday_acceptance.py`, `tests/test_import_graph.py`.

`worldmock.evaluation` importers: `scripts/inspect_run.py` (`workload_metrics`, `behavior_metrics`), `src/worldmock/cli.py`
(`evaluate`), `src/worldmock/footprints/quality.py` (`errand_metrics`, `goal_metrics`), tests `tests/evaluation/*`,
`tests/footprints/test_errand_export.py`, `tests/integration/{test_dayrun_complete_days,test_host_branch_coverage,test_same_second_continuation}.py`,
`tests/life/{test_candidate_constants,test_goal_around_time,test_parallel_admission,test_workload_report}.py`, `tests/test_shared_primitives.py`.

`worldmock.life.models` is imported by 232 files (every life module, custom, scripts, most tests). `life.sessions` /
`life.state_commit` importers: `life/{activity_control,counterfactual_runtime,decision_coordinator,engine,model_evidence,time_advance,work_review_runtime,world_setup}.py`,
`src/worldmock/runtime.py`, tests `tests/integration/test_dayrun_parallel_agent.py`, `tests/life/{test_presentation_fidelity,test_progress_layer_projection}.py`.

`scripts/render_day.py` is loaded by path from `footprints/timeline_labels.py::_load_render_day` (used by `export.py` and `quality.py`);
`scripts/inspect_run.py` is exercised by `tests/footprints/test_render_day.py`, `tests/life/{test_continuation_metrics,test_rerun_metrics,test_workload_report,test_query_cost_report}.py`.

---

## 2. Current contracts (anchor-exact)

### 2.1 `docs/CONTRACTS.md`

| Anchor | What it fixes |
| --- | --- |
| `contracts-记录引擎接口导航` | navigation only: recording-engine contracts live in `docs/design/05-recording-engine.md` |
| `contracts-数字足迹事件结构`, `contracts-数字足迹存储接口`, `contracts-资金事件字段`, `contracts-共同检查点与恢复`, `contracts-持久化结构` | pointers to the 05 sections (event fields, store calls, money conservation, checkpoint/restore, run directory tables) |
| `contracts-领域枚举` | `ActivityKind`/`ActivityStatus`/`ActivityChange`/`TravelMode`/`FulfillmentKind`/`ContractKind` closed sets; `DeliveryState.status`; **`WorldState.schema_version = 9`**; `ScheduledKind` closed set (`delivery_arrival`, `work_available`, `task_expired`, `payroll`, `price_change`, `supply_quote_change`, `product_release`, ...); WS8-vs-7 and WS9-vs-8 change tables |
| `contracts-冻结地图与场景引用` | `MapDatasetReference`, scenario `locations/products/supplies` reference-only, runtime scenario carries full `map_dataset/locations/routes/products/supplies`; WGS84; version border "地图文件结构 3、品类契约版本 3、交换规则绑定版本 3、场景结构 6、世界状态结构 9" |
| `contracts-类别供给的构建合同` | `random_seed`, `category_assignment_sha256`, per-store menus and price-position quote formula |
| `contracts-营业与报价声明` | `QuoteConfig`, temporal declarations, `OpeningCalendar`, `DataSource` |
| `contracts-地点发现与购买身份` | `query_world`, internal settlement rows by `supply_id`+`price_version`, purchase pairing, `evaluate_purchase`; **purchase channels `{channel_id, names, store_location_ids}` derived from the scenario-pinned phone config** |
| `contracts-持久化当下事实` | `supply_stock`, `supply_quotes`, `opening_exceptions`, `presentation_state`; "世界结构版本为 `9`"; `visible_supply_ids`/`supply_browsed_at`/`supply_browsed_location` key-set equality |
| `contracts-供给结算与事件载荷` | `price_change`/`supply_quote_change`/`product_release` payloads; one `delivery_arrival` per order with `order_ids`, `orders`, `inventory_changes`; order keeps `supply_id/product_id/location_id/seller_account_id/price_version/route_id/source`, `recipient_id`, `request_message_id`; `channel_id` only in `orders[]` and `purchases.jsonl`, not in `DeliveryState` |
| `contracts-private-fields` | prompt-forbidden keys incl. `route_id`, `supply_id`, `price_version`, numeric body keys, `energy_kcal`; "路线坐标与几何不得进入提示"; `inventory_items` row shape; eating by `opened` portions |
| `contracts-语料内容目录字段` | corpus item fields `item_id, kind, source, segments, attributes (营养...), assets, access, evaluation_refs` |
| `contracts-环境标题与活动效果` | `ContentItemConfig.title` mandatory (scenario structure 6), `LifestyleItem.effects` frozen into `ActivityState.data["effects"]` |
| `contracts-品类契约与目录字段` | `category_contract_version`, `category_contract_sha256`, `exchange_rules_sha256`, `catalog_content_sha256` frozen in the run manifest and preserved by checkpoint/export |
| `contracts-labelkit-输入结构与窗口编码` / `contracts-输入结构示例` / `contracts-窗口声明与字段字典编码` | window fields, decision-prompt authority, `self_note`, `decision_layers`, control-tool projection, RFC 6901 encoding contract |
| `contracts-labelkit-训练记录与交付格式` / `contracts-交付格式` / `contracts-数据集字段与划分` / `contracts-反事实与重演字段` / `contracts-运行报告` | `task/input/target/provenance` records, rule fixtures, delivery manifest keys, split by source connected components, `world_kind`/`environment_replay`, `quality.json` reference |
| `contracts-labelkit-语义候选与标签值` | claim limits and label vocabulary |
| `contracts-labelkit-调用配置` | LabelKit process configuration |
| `contracts-seven-layers` (rows for `state` "每份热量", `contract.remaining`, `submit_work` tool) and `contracts-seven-layer-schema` | tool-layer wording that quality `timeline_labels` and windows rely on indirectly |
| `contracts-版本表` | historical revision table (last rows record WS9, quality 4, export manifest 2) |

### 2.2 `docs/design/05-recording-engine.md`

| Anchor | What it fixes |
| --- | --- |
| `recording-engine-必须保存的内容` | what must be evidenced per life process (observation, decision, background plan_day, reflection, action, state change, memory, errands, failure) |
| `recording-engine-事件字段` | `Event`/`EventDraft` envelope (`schema_version` 1), observation `data` = layer texts + private bindings (`world_query_channels`, `supply` rows keyed by `location_id`) |
| `recording-engine-共同身体结算与决策证据` | `body_settlement` payload (`interval_*`, `activity_ids`, `body_settlement`, `resource_flows`, `decision_offered`, `wake_reasons`), wake-reason closed set incl. `errand_reminder_due`, `shift_*`, `break_*` |
| `recording-engine-事件类型枚举` | the 14 `EventType` members and the `environment_event.data.kind` family |
| `recording-engine-事件正文与资源证据` | per-event-type payload rules: `action_result.data.orders[]` with `channel_id`, `data.given`, `discarded`, `data.outcome`; `environment_event` kinds; `delivery_arrival`; `model_npc_reply`; `memory_updated` |
| `recording-engine-地图与门店供给证据` | map identity in run and export manifests; discovery results (routes, quotes, stock); purchase/delivery evidence (`supply_id`, `seller`, `price_version`, `channel_id`); `data.supplies` rows (`price_fen/price_version/quote`, `purchase_pairing_state`, ...); same-location zero-delay delivery |
| `recording-engine-activity_progress-事件` | `activity_progress` payload: `location_id` is start location, arrival from `data.arrival.location_id`; `consumption.items[].interval_quantity/cumulative_quantity`, `consumption.food_efficiency`, work `activity_efficiency` |
| `recording-engine-已核验经历与主观记忆` | the four verified-experience kinds `waited/consumed/arrived/played` (consumed carries product+location+quantity; played carries content id + media positions); `memory_updated.data` fields |
| `recording-engine-持久化结构` | run dir layout, run manifest freezes catalog identity and `code_sha256`; SQLite tables and triggers |
| `recording-engine-数字足迹存储接口` / `recording-engine-存储调用` / `recording-engine-存储接口行为` / `recording-engine-事务边界` / `recording-engine-状态版本与连续性` / `recording-engine-资金守恒验证` | `commit` semantics, version +0/+1, event seq, money conservation |
| `recording-engine-检查点创建` | checkpoint fields; **"世界结构版本为 `9`"**; what WS8 and WS9 seal (`opened_by_action_id`, `paused_at`, `opened`, `recipient_id`, `contact_holdings`, `outbox`, `errands`, `same_moment_*`, sessions, `day_plan`) |
| `recording-engine-地图与供给的恢复身份` | checkpoint must seal `supply_stock`, `supply_quotes`, `opening_exceptions`, `qualifications`, `visible_supply_ids`, `supply_browsed_at`, `supply_browsed_location`, `presentation_state`; map identity mismatch rejects open/restore/branch |
| `recording-engine-恢复行为` ... `recording-engine-分支恢复接口` | `restore_branch`, counterfactual metadata |
| `recording-engine-导出行为` | export files and manifest schema 2; `purchases.jsonl` row fields (`supply_id`, `channel_id`, `app_browse`); timeline rendered from `readable.py` labels; `frozen_run_manifest` keeps map identity |
| `recording-engine-完整性验证` / `recording-engine-导出认证` | authentication rules |
| `recording-engine-质量报告` | quality schema 4 section list and definitions (`content_contact` repeat watch, `conversion_efficiency` food/work, `model_usage.by_purpose` with `work_review`, `timeline_labels`, `errand_metrics`, ...) |
| `recording-engine-调试工具` | `render_day.py`, `inspect_run.py` subcommands (`rerun` 8 metrics incl. walking share, `continuation`, `workday`, `queries`) |
| `recording-engine-版本表` | revision history (WS7->8->9, quality 2->3->4) |

### 2.3 `docs/design/06-delivery.md`

| Anchor | What it fixes |
| --- | --- |
| `delivery-接收记录引擎导出` | delivery checks `state.schema_version == WORLD_SCHEMA_VERSION (9)`; delivery manifest schema 2; per-stage identity history (map 3, scenario 6, WS9, quality 4) |
| `delivery-三类窗口` / `delivery-窗口构造流程` / `delivery-动作窗口提取规则` | window semantics, visibility rules, actual-prompt authority |
| `delivery-地图与门店供给的证据窗口` | purchase evidence by supply identity; only actually received routes/quotes/stock enter windows; map version, digest, per-source licence and notice flow from export into delivery manifest |
| `delivery-dreaming-窗口提取规则` | dreaming windows |
| `delivery-字段字典编码` ... `delivery-解码校验` | RFC 6901 encoding |
| `delivery-manifestjson-结构` | manifest example; "清单还必须保留认证来源导出中冻结的地图集合身份、版本、摘要、逐来源许可及通知" |
| `delivery-provenance-字段` / `delivery-来源连通组` / `delivery-素材引用` | provenance and split rules |
| `delivery-版本历史` | revision history |

### 2.4 `docs/design/09-io-formats.md`

| Anchor | What it fixes |
| --- | --- |
| `io-formats-worldstate世界状态` | `WorldState` field table (`schema_version` Literal[9], `supply_stock`, `supply_quotes`, `opening_exceptions`, `deliveries` w/o `channel_id`, `contact_holdings`, `same_moment_presentation.pinned_supply_ids`, ...); `SessionState` table; `SupplyQuoteState`; public `data.products`/`data.supplies` and tool-layer price/energy wording |
| `io-formats-当前工具请求查询与错误格式` | `start_activity` items/`prefer`, control tools `errands`, `query_world` six keys, error shape, work input `appointment` |
| `io-formats-agentstate人物状态` | `AgentState` table (`inventory`, `visible_supply_ids`, `supply_browsed_*`, `content_progress`, `errands`, `material_reveals` noted as internal) |
| `io-formats-bodystate身体状态` | `BodyState` numeric fields |
| `io-formats-activitystate活动状态` | `ActivityState` table (`effective_work_seconds`, `media_position_seconds`, `route_progress_meters`, `playback_speed`, `data.opened/discarded/effects`, boundary/interruption closed sets) |
| `io-formats-labelkit-交付格式` | pointer to CONTRACTS |

### 2.5 `docs/dev/SPEC-labelkit-delivery.md`

| Anchor | What it fixes |
| --- | --- |
| `spec-labelkit-delivery-产品行为` / `spec-labelkit-delivery-设计裁决` | delivery pipeline invariants |
| `spec-labelkit-delivery-确定输入范围与加工责任` | who produces what |
| `spec-labelkit-delivery-证据窗口与输入结构` | window construction rules (prompt authority, materials expansion, public projection, errand events) |
| `spec-labelkit-delivery-来源关联` / `spec-labelkit-delivery-信息范围与标签` / `spec-labelkit-delivery-交付产物与发布边界` / `spec-labelkit-delivery-长序列截断与划分` | provenance, labels, artifacts, splits |
| `persona-prompt-下游` | downstream effects of the seven-layer prompt: `purchases.jsonl` with `purchase/order_attempt/app_browse`, `channel_id` not in world state, quality 4, WS9, delivery manifest 2 |
| `dayrun-fidelity-下游与质量报告` | `quality.json` new sections; `action_result.data.orders[] += channel_id`; **Dataset `simulate_phone.py` reads `channel_id` first; `prepare_labelkit_input.py` trims memories** |
| `spec-labelkit-delivery-验收矩阵` | test node ledger for delivery |

### 2.6 `docs/design/07-evaluation.md`

| Anchor | What it fixes |
| --- | --- |
| `evaluation-协议门禁gates` / `evaluation-报告项reports` | gates and report keys (`meal_diversity` by `product_id`, `workload` by `effective_work_seconds`) |
| `evaluation-三层数据模型` / `evaluation-证据事件publicevidence` / `evaluation-活动activitysession` | common model; closed public values include `object_type: route, content, game, product, task` |
| `evaluation-适配器职责` | WorldMock adapter reads `action_requested/action_result/environment_event`, `start_activity.items[]`, orders/payment/fulfilment states |
| `evaluation-门店选择与供给假设的比较边界` | map set, supply assumptions, routes are experiment conditions; diversity by `product_id`, store choice by `supply_id` |
| `evaluation-输入认证` | `--worldmock` must be a completed export |
| `evaluation-版本历史` | history |

### 2.7 `docs/VERIFICATION.md`

| Anchor | What it fixes |
| --- | --- |
| `verification-地图与门店供给` | gates: unique frozen materials, route modes, store/supply separation, temporal quotes, visible supplies, atomic orders, self-pickup vs delivery scope, "来源与许可贯穿导出", mutation list (stock isolation, quote version, route mode, delivery idempotence) |
| `verification-报告项report-only` | report keys (`meal_diversity`, `workload`, `routine_deviation`, `wait_minutes`, ...) and `queries` subcommand |
| `verification-测试矩阵` | rows 资源结算, 工作生产 (materials/review), 内容知识 (subtitle isolation, hidden reference, game restore), 观察投影 (商品热量与路线费力标签), 指名购买 (`channel_id`), 世界查询 |
| `errands-workday-度量与下游-质量报告` / `errands-workday-度量与下游-行为评估` / `errands-workday-度量与下游-查询成本只报告` / `errands-workday-度量与下游-首次受影响发布身份` | quality schema 4 groups; workload/routine deviation inputs; `queries`; which digest binds which change |

### 2.8 `docs/EXPERIMENT.md`

| Anchor | What it fixes |
| --- | --- |
| `experiment-运行绑定与消费` / `experiment-数据流与依赖` / `experiment-命令入口` / `experiment-训练与规则消费` | `export` -> `deliver` -> `evaluate` chain, datasets 5.0.1, rule-engine 5.0.2 |
| `experiment-能量感知标签` | food energy label rounding (per-product kcal) |
| `experiment-内容与语料绑定` | Open Food Facts nutrition, Sintel SRT, TextWorld games, AMI work materials, real map routes |
| `experiment-地图与门店供给的实验身份` | `map_dataset` identity, real coordinates, two seeded maps, stage identities (WS9, quality 4, export manifest 2, run manifest 2) |
| `experiment-样本编码与来源分组` | pointer to CONTRACTS |

### 2.9 Manuals

| Anchor | What it fixes |
| --- | --- |
| `docs/manual/delivery.md#delivery-文件怎么分工` / `#delivery-修改规则数据` / `#delivery-离线检查` / `#delivery-正式交付与检查结果` | LabelKit project files, rules catalog from `prepare_windows`, `worldmock export`/`deliver` usage |
| `docs/manual/evaluation.md#evaluation-修改类别映射和公开参照` | `category_mappings.worldmock_activity` keys (`purchase, travel, watch, ...`), `consume` fixed `eat`/`product_use` |
| `docs/manual/evaluation.md#evaluation-绑定真实候选输入` | `--worldmock` export requirements, legacy LabelKit file family |

### 2.10 Other specs whose clauses this subsystem implements

- `docs/dev/SPEC-life-realism.md#spec-life-realism-呈现与导出` (timeline projection, `verified-experiences.jsonl`, blind review location/companion), `#spec-life-realism-商务与进食`
- `docs/dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-已核验经历与人物自述` / `#spec-dayrun-behavior-事件投影` (the 4 experience kinds), `#spec-dayrun-behavior-等待与内容接触`, `#spec-dayrun-behavior-真实音频来源与揭示`
- `docs/dev/SPEC-life-runtime.md#spec-life-runtime-导航与配送` (location track along real route geometry; delivery ETA = preparation + road transport), `#spec-life-runtime-具体活动结算` (work: materials/outputs/review traceable), `#spec-life-runtime-内容提供行为` (work materials revealed with progress)
- `docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-记录恢复与交付` (export keeps map version/source/licence; checkpoint round-trips stock/quotes/exceptions/visibility), `#dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店` (channel = phone config projection; `channel_id` written to `orders[]`/`purchases.jsonl`, not `DeliveryState`)
- `docs/dev/SPEC-environment-data-ownership.md` (table rows 历史证据不可改写, 双仓端到端, 测试输入归属)

---

## 3. Current formats and fixtures

### 3.1 Event envelope and store (unchanged by the proposal unless noted)

`Event` (schema 1): `event_id, event_type, occurred_at, agent_id, decision_id, action_id, activity_id, caused_by[], data{},
artifact_refs[AssetRef{sha256,size_bytes,media_type,source}], visibility{agent|evaluation|system}, source_kind{environment|model|scenario|external},
observed_at, run_id, event_seq, recorded_at, wall_elapsed_ms, state_version_before/after`. SQLite: `events(event_seq, event_id, body)`,
`world_state(singleton, version, body)`, `artifacts(sha256, body)`, `checkpoints(checkpoint_id, body, record_sha256)`.
`Checkpoint` (schema 1): `checkpoint_id, run_id, current_time, state_version, last_event_seq, state{WorldState JSON}, snapshots{"life", "agent:<id>"}, manifest_sha256, record_sha256`.

Run manifest (`world_setup._run_manifest`, schema 2): `run_id, source_group_id, scenario_id, map_dataset{dataset_id, version, random_seed,
category_assignment_sha256, coordinate_system: "WGS84", timezone, prepared_at, source{DataSource}, locations_file{path,sha256}, supplies_file, routes_file},
scenario_sha256, corpus_manifest_sha256, agent_ids, code_sha256, git_commit, git_dirty, model_identity, stage_model_identity, input_warnings,
persona_habit_responses, window{start,end,timezone}, category_contract_version, category_contract_sha256, exchange_rules_sha256, catalog_content_sha256`.

### 3.2 Where coordinates / trajectories / routes live

| Carrier | Keys |
| --- | --- |
| `environment_event` `data.kind = "location_track"` (produced by `life/travel.py::sample_location_track`; `visibility=agent`) | `route_id, route_response_path, route_response_sha256, sample_index, sample_count, position_lonlat [lon, lat], distance_along_route_meters, sampling_rule{method, sample_interval_seconds, departure_epoch, arrival_epoch, origin_id, destination_id, mode, sample_count}`; event id = sha256 of the sampling seed |
| `observation.data.locations[]` (`observe._location_catalog`) | `location_id, name, kind, business_kind, location_kinds, longitude, latitude` |
| `observation.data.routes[]` (`observe._route_catalog`) | `route_id, origin_id, destination_id, mode, status, reason, distance_meters, duration_seconds, free_flow_duration_seconds, travel_time_factor, fare_fen, fare_source_kind` (read by `inspect_run.walking_share`) |
| travel `ActivityState` | `route_progress_meters`, `travel_mode`, `destination_id`, `data.route_id`, `data.travel_payment`, `data.arrival{location_id, access_status, missing_qualifications, source}` |
| `DeliveryState` / `orders[]` | `route_id`, `source.route{DataSource}` |
| run manifest `map_dataset` (+ export `frozen_run_manifest`, delivery manifest `map_dataset`, dataset `tools/labelkit_input.py` provenance) | coordinate system, routes file digest |
| verified experience `arrived` | `location_id, access_status` (no coordinates) |
| evaluation `public_evidence` travel | `object_type="route"`, `object_key=route_id`, attribute `travel_mode` |
| map models (other subsystem, consumed here) | `LocationConfig.longitude/latitude`, `RouteConfig{route_id, origin_id, destination_id, mode, status, distance_meters, duration_seconds, fare_fen, response_path, response_sha256, routing_instance, routing_profile}` |

### 3.3 Supplies / offers / inventory

| Carrier | Keys |
| --- | --- |
| `WorldState` | `supply_stock{supply_id: int}`, `supply_quotes{supply_id: SupplyQuoteState{price_fen, price_version, effective_from, effective_to, source} | null}`, `opening_exceptions{location_id: {date: [[opens, closes]]}}`, `deliveries{order_id: DeliveryState}`, `contact_holdings`, `same_moment_presentation[agent].pinned_supply_ids` |
| `AgentState` | `inventory{location_id: {product_id: float}}`, `visible_supply_ids`, `supply_browsed_at{supply_id: epoch}`, `supply_browsed_location{supply_id: location_id}`, `qualifications` |
| `DeliveryState` | `order_id, agent_id, product_id, supply_id, location_id, seller_account_id, price_version, route_id, source{supply, quote, route}, quantity, destination_id, fulfillment, created_at, expected_at, status{pending|ready|delivered|cancelled}, total_price_fen, recipient_id, request_message_id` |
| `action_result.data.orders[]` (and `activity_results[].orders[]`) | `DeliveryState` JSON + `channel_id` (App id of the channel resolved from `from`, else null); result also carries `total_price_fen`, `inventory_changes`, `category_matches`, `resolved`, `notes`, `answering` |
| `environment_event` kinds | `delivery_arrival{order_ids, orders[], inventory_changes[]}`, `price_change{supply_id, price_version, amount_fen, source}`, `supply_quote_change`, `product_release{supply_id, stock, source}` |
| `observation.data.supplies[]` (`discovery.supply_row`) | `supply_id, product_id, name, location_id, location_name, seller_account_id, price_fen, purchase_pairing_state, purchase_coefficient, purchase_price_fen, price_version, quote_status, quote, current_stock, fulfillment_modes, delivery_destination_ids, delivery_delay_seconds, opening_status, access_allowed, required_qualifications, missing_qualifications, service_allowed, purchase_allowed, pickup_allowed` |
| `observation.data.products[]`, `inventory`, `available_inventory`, `inventory_items[]`, `pending_deliveries[]`, `discovery_catalog{product_ids, business_kinds}`, `market_summary`, private `world_query_channels` | product definitions and holdings |
| browse activity `data.revealed{items[{supply_id, ...}]}` / `action_result.data.browse_result` | revealed supplies |
| `purchases.jsonl` `purchase` row | `agent_id, order_id, product_id, supply_id, quantity, total_price_fen, fulfillment, recipient_id, request_message_id, channel_id, created_at, action_id, source_event_id`; `order_attempt` row `agent_id, action_id, request_event_id, way, status, error_code, occurred_at`; `app_browse` row `agent_id, activity_id, started_at, status, source_event_id` |
| `PurchaseItem` / `PurchaseOrderRequest` | `supply_id, quantity(1..100), price_version, recipient_id, request_message_id`; `fulfillment`, `destination_id` |
| quality / evaluation | `meal_diversity` by `product_id`; `review._reject_identity_keys` forbids `product_id`, `order_id`, `route_id` |

### 3.4 Nutrition

| Carrier | Keys |
| --- | --- |
| scenario `InventoryItemConfig`/`ProductConfig` (observation `data.products[]`) | `energy_kcal_per_unit, serving_quantity, serving_unit, inventory_unit, use_duration_seconds, use_modes, consumable, effect, purchase_price_fen_min/max` |
| consume `ActivityState.data` | `request{items[{product_id, quantity}], use_mode, duration_seconds}`, `opened{product_id: portions}`, `consumed{product_id: cumulative}`, `discarded`, `inventory_recipe{..., output_object_id}`, `food_energy_kcal_per_hour` |
| `activity_progress.data.consumption` | `use_mode, items[{product_id, interval_quantity, cumulative_quantity}], location_id, food_efficiency` |
| `environment_event body_settlement.data.body_settlement` | `before/after{BodyState incl. energy_kcal}, duration_seconds, basal_energy_kcal, extra_energy_kcal, food_energy_kcal, food_efficiency, work_efficiency` plus `resource_flows[]` |
| `AgentState.taste_weights`, `BodyState.energy_kcal` | taste ranking, energy |
| verified experience `consumed` | `product_id, location_id, interval_quantity, cumulative_quantity, interval_start, interval_end` |
| quality `conversion_efficiency.food`, behaviour `meal_diversity` / `_meal_sessions`, `inspect_run` `lowest_energy_before_meal`/`meal_count` | read the above |
| tool layer (`contracts-seven-layers`) | `state` "每份约 N 千卡" |

### 3.5 Content segments

| Carrier | Keys |
| --- | --- |
| scenario `ContentItemConfig` | `item_id, title, kind ∈ CONTENT_KINDS (feature_film, drama_series, podcast_episode, webnovel_excerpt, news_feed, joke_feed, subtitle_experience, game_session), duration_seconds, timeline_path/timeline_sha256 (SRT), audio_path/audio_sha256, entertainment_relief_per_hour, experience_label`; `GameConfig{game_id, title, seed, game_path, walkthrough_sha256}` |
| watch `ActivityState` | `media_position_seconds`, `playback_speed`, `target_id = item_id`, `data.content_kind`, `data.start_position_seconds`, `data.entertainment_relief_per_hour`, `data.exposure_count`, `data.repeat`, `data.revealed{experience, position_before_seconds, position_after_seconds, cues...}` (progress), `resource_claims` from `content_kind` (podcast = ears only) |
| `AgentState.content_progress{item_id: ContentProgress{item_id, position_seconds, exposure_count}}`; observation `data.content_progress[item_id]{..., remaining_media_seconds, title}`, `data.items[]`, `data.game_catalog[{game_id, seed}]`, `data.browse_targets` | progress and catalog |
| verified experience `played` | `content_id, position_before_seconds, position_after_seconds, interval_start, interval_end` |
| quality `content_contact` (`_CONTENT_TYPES = watch/read_mail/play_game/browse`, `repeat_watch` by `data.repeat`), behaviour `_content_contact_minutes` (needs `revealed` positions), `render_day._media_label` ("看《title》"/"玩游戏《title》"), `readable.BROWSE_LABELS` (content kind labels), evaluation `_TARGETED_ACTIONS` (`watch->content`, `play->game`, `browse->content`) | consumers |

### 3.6 Work materials and reviews

| Carrier | Keys |
| --- | --- |
| scenario `WorkTaskConfig` | `task_id, title, assignee_agent_id, contract_kind{package|hourly|piece}, appointment, due_at, maximum_duration_seconds, pay_fen, employer_account_id, required_location_id, required_skill, minimum_skill, required_effective_seconds, input_path/sha256, oracle_path/sha256` |
| `AgentState` | `visible_task_ids`, `known_task_ids`, `material_reveals[]`, `skill_levels` |
| `WorldState.paid_task_ids`; `PendingAction.work_review`, `work_review_event_id` | settlement bookkeeping |
| work `ActivityState` | `effective_work_seconds`, `target_id = task_id`, `task_kind=work_task/task_ref`, `data.required_skill/skill_level/work_notes_multiplier` |
| `activity_progress.data` | `activity_efficiency`, `work_material{...}` (revealed text ranges) |
| observation `data.tasks[]` (`_visible_task`) | task config minus oracle/input paths + `deliverable_schema (MeetingMinutes)`, `material`, `worked_seconds`, `effective_seconds`, `remaining_contract_seconds`; `data.work_progress[]`; private `task_bindings` |
| `action_requested` tool `submit_work{task_name, deliverable{summary, decisions[], action_items[]}}`; `model_request/response` with `purpose="work_review"`; `action_result.data{work_review{accepted, required_fact_total/passed/missing, missing_content_categories, reference_assessments, delivery_assessments}, payment_fen, task_id}` | review chain |
| `environment_event` kinds `work_available`, `task_expired`, `mail_arrival{related_task_id}`; wake reasons `task_available`, `task_due_soon`, `work_review_result`; `ScheduledKind` `work_available`, `task_expired`, `payroll` | scheduling |
| quality `conversion_efficiency.work`, `model_usage.by_purpose["work_review"]`; evaluation `_work_result`, `_world_environment(work_available/task_expired)`, `_world_request_shape(submit_work)`, `workload_metrics` (`effective_work_seconds`, `TaskSite`, payment settlement); `render_day._work_label` ("工作「title」"); `inspect_run workday` | consumers |

### 3.7 App identities

| Carrier | Keys |
| --- | --- |
| scenario | `phone_simulation_config` + `phone_simulation_config_sha256` (optional) -> `profile/coverage_apps.PhoneSimulationCoverage{activity_apps, purchase_apps_by_seller, app_display_names}` -> `PurchaseChannel{channel_id, names, store_location_ids}` |
| events | `action_result.data.orders[].channel_id`; observation private `world_query_channels`; `action_requested.request.items[].from` (channel name text) |
| export | `purchases.jsonl` `purchase.channel_id`; `app_browse` facts (browse activities, not apps) |
| world state | none (explicitly: `channel_id` not in `DeliveryState`) |
| dataset handoff | `WorldMock-Dataset/tools/simulate_phone.py` (at `2cff671`): config keys `agent_id`, `scenario_id`, `scenario_path`, `persona_source{path,sha256}`, `timezone`, `home_location_id`, `content_apps_by_id`, `game_apps_by_id`, `activity_apps{driving_travel, <kind>}`, `message_apps_by_recipient`, `message_session_seconds`, `purchase_apps_by_seller`, `payment_sellers`, `payment_app_id`, `payment_session_seconds`, `merchant_session_seconds`, `delivery_notification_sellers`, `scheduled_sessions[{session_id, app_id, start_at, end_at, required_location_id, source_section}]`; it reads export `manifest.json` (`frozen_run_manifest.{scenario_sha256, agent_ids}`, `artifacts.events/activities` sha+rows, `window`), `events.jsonl` (`action_result` with `data.data.orders[]`/`activity_results[].orders[]` via `worldmock.footprints.purchases.project_purchase_facts`, order `seller_account_id`/`order_id`; `environment_event kind=delivery_arrival` `data.orders[].{seller_account_id,status,order_id}`; `action_result.data.data.{message_id, recipient_id}`), `activities.jsonl` (`activity_type`, `status`, `ended_at`, `destination_id`, `segments[].{started_at,ended_at}`, `travel_mode`, `target_id`, `source_event_ids`) and the scenario's `phone_simulation_config{,_sha256}` pin; it still does **not** read `channel_id` (seller map only, despite `dayrun-fidelity-下游与质量报告` saying it should prefer `channel_id`). `WorldMock-Dataset/tools/labelkit_input.py`: `validate_export`; `VISIBLE_EVENT_TYPES` = request/accepted/rejected/progress/result/environment_event; `handoff_event` projects the three control tools to `request.items[{activity_name, summary}]` (drops `memories`, `views`, request-level `errands`; rejects a request without the `errands` key) and the six errand `environment_event` kinds to `data: {}`; activity rows need `segments` and `source_event_ids`; phone rows must have `source_kind == "scenario"` and `source_event_ids` inside the export; provenance copies `frozen_run_manifest.{scenario_id, scenario_sha256, map_dataset, corpus_manifest_sha256, window.timezone}`. Dataset preflight (`WorldMock-Dataset/tools/preflight_persona.py`, `preflight_runtime.py`, `preflight_routes.py`, `preflight_data.py`) cross-checks `purchase_apps_by_seller`/`app_display_names` against `purchase_channels{channel_id, names, store_choice}` and `semantics.purchase_channels`; `WorldMock-Dataset/tests/test_phone_purchase_projection.py` locks the seller-based projection |
| evaluation legacy adapter | `appUsageEvent` IN/OUT -> `interaction open/close`, `object_type application`; `notificationEvent` -> `notify` |

### 3.8 WS9 field summary and what WS10 would carry

`WorldState` (WS9): `schema_version, version, current_time, end_time, run_id, timezone, scenario_sha256, content_manifest_sha256, agents, accounts,
supply_stock, supply_quotes, opening_exceptions, activities, sessions, deliveries, contact_holdings, pending_events, processed_actions, paid_task_ids,
presentation_state, same_moment_presentation, random_seed, random_state`.
`AgentState`: `agent_id, name, location_id, balance_account_id, skill_levels, qualifications, taste_weights, body, inventory, active_effects,
body_settled_at, delivered_warning_signatures, next_decision_at, consecutive_rejections, same_moment_rounds, same_moment_stage, content_progress,
read_message_ids, visible_message_ids, visible_task_ids, visible_supply_ids, last_decision_time, felt_bands_at_last_decision, people_at_last_decision,
known_task_ids, pending_wake_reasons, day_plan, plan_submitted_dates, errands, inbox, read_inbox_ids, outbox, mood_updated_at, discomfort_started_at,
discomfort_pulse_hours, supply_browsed_at, supply_browsed_location, material_reveals`.
`SessionState`: `session_id, agent_id, kind{stay|transit}, location_id, origin_id, destination_id, status, started_at, ended_at, activity_ids, name, opened_by_action_id`.
`ActivityState`: see 09 table (`effective_work_seconds`, `media_position_seconds`, `route_progress_meters`, `playback_speed`, `target_id`, `destination_id`, `travel_mode`, `data`, lifecycle fields).
The version gate is enforced in three places: `WorldState.schema_version: Literal[WORLD_SCHEMA_VERSION]` (open/restore/`commit_state`),
`delivery/pipeline.py::_require_current_world_schema`, and `tests/life/test_session_state.py` (`test_world_state_schema_version_9_is_valid`, 7/8 rejected).
Section 5 lists the WS10 delta.

### 3.9 Export, delivery, evaluation file shapes

- Export dir: `manifest.json` (schema 2: `committed, run_id, source_group_id, source_run_ids, cutoff_event_seq, experiment_completed, window, daily_coverage, artifacts{events, activities, verified_experiences, purchases}{path, rows, sha256}, asset_index, frozen_run_manifest, quality_report{path,size_bytes,sha256}, timeline[], checkpoint`), `events.jsonl`, `activities.jsonl` (activity snapshot with `activity_type` instead of `kind`, `segments[]`, `source_event_ids[]`, `title`, `category`), `verified-experiences.jsonl`, `purchases.jsonl`, `asset-index.jsonl`, `assets/<sha256>`, `quality.json` (schema 4), `timeline/<agent>-<date>.md`.
- Delivery dir: `manifest.json` (schema 2: `artifacts{training, rules}, labelkit{action,intent,dreaming,rules}, training_receipt, rule_results, semantic_review, source_export, map_dataset (copied), replay_exports, files`), `labelkit/` workspace (`data/<task>.jsonl`, `<task>.source-index.jsonl`, `out/<task>.jsonl`, `out/<task>.trace.jsonl`, `rules-catalog.jsonl`), `training.jsonl`, `training/` DatasetDict, `rules.jsonl`, `rule-results.json`, `rule-expressions.json`.
- Training record: `{task, input{events[{event_id, event_type, occurred_at, agent_id, data (public projection), materials[]}], window_start, window_end, knowledge_cutoff_event_seq, activity_ids, prerequisite_state, activity_boundaries, allowed_evidence_event_ids, boundary_type, decision_prompt, decision_layers, mode, person_id, date, counterfactual}, target{claims, intent_candidates, uncertainty, facts{request, outcomes | mode, environment_replay}}, provenance{run_id, source_group_id, source_event_ids, cutoff_event_seq, model_identity, window_id, decision_prompt_event_id, decision_prompt_sha256, decision_layer_sources, annotation_model, labelkit_record_id, model_input}}`.
- Evaluation scenario root keys: `scenario_id, schema_version, window, bootstrap, review, shared_profile, special_dates, category_mappings{labelkit_sequence_class, labelkit_pattern, worldmock_activity}, official_domain_map, official_reference, expected_labelkit`.

### 3.10 Fixture files encoding these shapes

- `tests/fixtures/environment/` (artificial engine environment; map structure 3 with WGS84 coordinates and empty supplies/routes): `catalog/{affordances.toml, category-contract.json, exchange-rules.json, manifest.json, objects.jsonl, resources.jsonl, rules/{motivation.toml, persona-mapping.toml, travel-time.toml}}`, `corpora/artificial-map/{category-assignment.json, dataset.json, locations.json, routes.json, supplies.json}`, `corpora/content/test.srt`, `corpora/lifestyle/items.json`, `corpora/manifest.json`, `scenarios/{single-person.json, multi-person.json, multi-person-half-day.json}` (scenario structure 6: `map_dataset{path,sha256,dataset_id,version}`, `contents[{item_id,title,kind:subtitle_experience,duration_seconds,timeline_path,timeline_sha256,entertainment_relief_per_hour,experience_label}]`, `products[{product_id}]`, `supplies: []`, `tasks: []`, `games: []`), `README.md`.
- `tests/delivery/fixtures/labelkit-action.json` (recorded DeepSeek preflight: action window input + annotation; carries `request.data.request{activity_type: sleep, planned_duration_seconds}` only).
- `tests/fixtures/dayrun-biz-timeline.json` (real business-run observation with `tasks[{task_id,title}]`, `interaction_bindings`; locks `render_day` work/call/chat rows), `tests/fixtures/dayrun-contact-replies.json`, `tests/fixtures/dayrun_fidelity_inputs.json` (persona cards, taste weights, "delivery (supply, destination) pairs without a reachable driving route"), `tests/fixtures/dayrun_truncations.json`, `tests/fixtures/dayrun_wc_named_purchase.json` (named purchase requests against `frozen-inputs/corpora/phone-simulation.json` and map files), `tests/fixtures/dayrun_work_reviews.json` (real `work_review` requests/responses), `tests/fixtures/dayrun-layering-baseline.json` and `tests/fixtures/dayrun-shared-rules-baseline.json` (sha256 of `activities.jsonl`, `asset-index.jsonl`, `assets/*`, etc. for the two complete-day cases; compared by `tests/dayrun_baseline.py::day_baseline_signature` in `tests/integration/test_dayrun_complete_days.py`), `tests/fixtures/viewer-context.json` (recorded seven-layer requests), `tests/fixtures/control-request-generations.json`, `tests/fixtures/tool_interaction_habits.json`, `tests/fixtures/archive-assets.json`, `tests/fixtures/current-feature-owners.json`.
- Acceptance ledgers (section 4): `tests/fixtures/acceptance_requirements.json`, `tests/fixtures/idle-fillers-acceptance.json`, `tests/fixtures/layering-acceptance.json`, `tests/fixtures/layering-public-api.json` (LifeEngine method signatures only), `tests/fixtures/mutation-symbol-baseline.json` (147 mutation ids with sha256 of generated mutants, e.g. `errands:version_8_export_passes_delivery_gate`, `errands:checkpoint_omits_errands`, `tool:pagination_supply_window`, `tool:native_purchase_route_loses_way`, `tool:invalid_request_purchase_projection`).
- In-test fixture builders that hard-code the shapes: `tests/evaluation/conftest.py::_world_activity/_progress_event/_write_canonical` (activity rows with `route_progress_meters`, `effective_work_seconds`, `playback_speed`, manifest artifacts incl. `purchases.jsonl`), `tests/footprints/test_purchases.py::_ORDER` (full `DeliveryState` with `supply_id`, `seller_account_id`, `price_version`, `route_id`), `tests/delivery/prompt_fixtures.py`, `tests/life/test_state_serialization.py::_world_state` (`visible_supply_ids`, `supply_browsed_*`), `tests/life/test_session_state.py` factories, `tests/integration/storefront_fixtures.py`, `tests/life/routing_fixtures.py`.

---

## 4. Tests that lock the current behaviour

### 4.1 Footprints (`tests/footprints/`, 186 tests)

- `test_store.py` (19): store commit/version/causality/checkpoint/restore; `test_action_request_cutoff_counts_only_thinking_requests_with_the_action_tools` (ledger: time-and-energy), `test_complete_checkpoint_branch_preserves_parent_and_exact_workspace`.
- `test_event_fields.py` (17): `test_event_type_vocabulary_is_the_frozen_spec_set`, `test_wake_reason_vocabulary_is_the_frozen_spec_set`, `test_wake_reason_vocabulary_matches_the_engine_side_enum`, `test_arrival_kind_constants_match_the_spec_names`, `test_decision_started_data_is_the_closed_four_field_set` (ledgers: idle-fillers T18, time-and-energy, errands, persona-prompt).
- `test_export.py` (9): `test_export_uses_cutoff_and_excludes_pause_from_duration`, `test_export_writes_full_committed_experience_prefix`, `test_checkpoint_requires_every_participant`, `test_export_writes_hash_bound_timeline_projections`, `test_export_timeline_projection_matches_render_day_output`.
- `test_validation.py` (11): `test_production_windows_require_and_accept_complete_authenticated_export`, `test_incomplete_or_tampered_export_never_reaches_labelkit`, `test_quality_report_requires_bytes_and_actual_event_recomputation` (+ profile/coverage CLI tests that live here).
- `test_purchases.py` (11): `test_purchase_rows_carry_the_order_channel_or_no_channel`, `test_browse_only_counts_as_app_browse`, `test_order_ways_and_browse_kind_are_the_interaction_menu_values`, `test_export_writes_purchase_facts_under_manifest_version_two`, `test_real_run_export_counts_eat_and_remote_order_once_despite_a_replay`, `test_validation_recomputes_purchase_facts_and_rejects_tampering` (ledgers: dayrun-fidelity `_PURCHASES`, persona-prompt `_PURCHASES`, tool-interaction mutation `worldmock.footprints.purchases::_order_attempts`).
- `test_verified_experiences.py` (11): `test_consumed_intervals_are_increments_and_projection_is_idempotent`, `test_zero_playback_and_unfinished_travel_do_not_become_completed_facts`, `test_arrival_snapshot_at_origin_still_projects_arrived` (ledger: time-and-energy).
- `test_quality.py` (38): `test_report_sections_are_the_documented_grouping_without_a_language_count` (ledger: errands), `test_repeat_watch_counts_only_sessions_restarted_from_the_beginning` (time-and-energy), `test_model_usage_counts_plan_day_and_lists_it_by_purpose`, `test_decision_rhythm_counts_same_moment_rounds`, `test_errand_reminder_due_is_a_known_wake_reason`, `test_timeline_labels_count_english_kind_names_and_internal_ids_in_the_rendered_timeline`, `test_name_rejections_are_counted_apart_from_other_protocol_errors`, `test_protocol_quality_uses_structured_evidence_without_log_inference` (tool-interaction mutation `_name_rejection_quality.code`), `test_truncations_match_the_recorded_day_runs` (fixture `dayrun_truncations.json`).
- `test_render_day.py` (14): `test_resolves_names_from_seven_layer_observation_evidence_keys` (persona-prompt), `test_work_call_and_chat_rows_name_the_task_contact_and_person`, `test_real_biz_run_rows_name_the_calls_and_the_work_task` (fixture `dayrun-biz-timeline.json`), `test_inspect_run_body_reads_values_and_levels`.
- `test_branch_coverage.py` (38), `test_ledger.py` (5), `test_platform_branches.py` (4), `test_status.py` (2), `test_goal_export.py` (3: `test_export_command_uses_authenticated_cutoff_and_manifest_code` in errands ledger), `test_errand_export.py` (4, errands ledger `_ERRAND_EXPORT`).

### 4.2 Delivery (`tests/delivery/`, 132 tests)

- `test_publication.py` (6): `test_delivery_rejects_export_whose_final_checkpoint_has_another_world_schema_version`, `test_delivery_gate_is_world_schema_eight_and_rejects_version_seven_exports` (name stale; asserts the current gate), `test_delivery_gate_rejects_version_eight_exports`, `test_delivery_checks_world_schema_before_preparing_windows` (time-and-energy ledger), `test_manifest_inventory_binds_every_gate_and_consumer_file`.
- `test_windows.py` (22): decision-prompt authority, projections (`test_intent_and_dreaming_projections_drop_memories_views_and_errands_and_leave_the_event_intact`, `test_the_action_target_keeps_the_full_control_request`, `test_control_requests_without_errands_are_rejected_by_delivery`, `test_reflection_requests_after_the_action_never_become_the_decision_prompt` — errands ledger; mutation anchors `worldmock.delivery.windows::_public_event_data.public`, `::_decision_prompt_source.candidates`).
- `test_host_delivery_coverage.py` (53): `test_deliver_success_path_publishes_full_manifest` (asserts `map_dataset` copy), replay/generation/trace rejections.
- `test_labelkit_process.py` (13, real LabelKit CLI), `test_training.py` (11), `test_evidence_encoding.py` (11), `test_semantic_schema.py` (8), `test_replay.py` (4), `test_rules.py` (2), `test_generation.py` (2).

### 4.3 Evaluation (`tests/evaluation/`, 240 tests)

- `test_adapters.py` (19): `test_consume_category_uses_actual_closed_use_mode_for_evidence_and_duration`, `test_digital_interaction_follows_activity_type_regardless_of_claim_names`, `test_start_items_follow_the_started_activity_kind_then_the_order_then_the_way` (time-and-energy ledger), `test_purchase_payment_uses_canonical_resource_changes_and_preserves_actual_states`, `test_mail_and_work_deadline_keep_public_facts_without_raw_text_or_failure_inference`, `test_parallel_travel_and_watch_project_two_actual_activities`.
- `test_branch_coverage.py` (87): `test_world_activity_rows_are_strictly_validated`, `test_start_travel_projects_route_identity_mode_and_payment`, `test_game_and_targeted_keys_come_from_actual_activity_data`, `test_purchase_and_work_results_follow_actual_object_states`, `test_environment_kinds_outside_the_closed_set_leave_no_evidence`, `test_activity_category_requires_object_request_with_closed_use_mode`.
- `test_behavior_metrics.py` (31): `test_parallel_activity_time_does_not_double_person_elapsed_or_fake_content` (needs `revealed`), `test_meal_diversity_simpson_and_repetition_anchors`, `test_activity_distribution_matches_reference_exactly`, `test_wait_is_judged_by_the_started_activity_kind_not_the_request`, `test_reflection_requests_and_their_retries_are_not_decision_requests` (errands ledger).
- `test_workload_metrics.py` (30, errands ledger `_WORKLOAD`): `test_workload_fields_follow_their_definitions_on_a_hand_built_day`, `test_effective_work_counts_only_the_part_of_an_interval_inside_the_shift`, `test_visiting_a_settled_task_site_and_walking_back_is_absence`, `test_workday_inputs_take_shifts_tasks_home_windows_and_interval_from_the_scenario_and_cards`.
- `test_errand_metrics.py` (10, errands ledger `_ERRAND_METRICS`; `test_a_making_request_points_at_the_recipe_output_not_at_the_kit_it_uses_up` depends on `inventory_recipe`), `test_goal_metrics.py` (29), `test_metrics.py` (9), `test_review.py` (12), `test_runner.py` (6), `test_shared_event_parsing.py` (2, shared-rules ledger), `test_decision.py` (2), `test_evaluation_scenario.py` (3).

### 4.4 Life models / sessions / export integration

- `tests/life/test_session_state.py` (48): `test_world_state_schema_version_9_is_valid`, `test_world_state_schema_version_7_is_rejected`, `test_world_state_schema_version_8_is_rejected`, `test_run_with_version_8_world_state_is_rejected_on_open`, `test_removed_session_fields_are_rejected`, session/activity field contract tests.
- `tests/life/test_state_serialization.py` (3): `test_json_mode_sorts_every_persisted_string_set` (constructs `visible_supply_ids`, `supply_browsed_*`), `test_open_errands_round_trip_through_json_with_a_byte_stable_encoding`, `test_readonly_commit_is_identical_across_python_hash_seeds`.
- `tests/life/test_export_integration.py` (8): `test_completed_activity_exports_terminal_outcome_and_training_windows`, `test_export_contains_hash_bound_timeline_projections`, `test_truncated_activity_exports_experiment_end_reason`, `test_lifecycle_records_and_export_match_the_final_checkpoint`, contact reply loop tests.
- `tests/life/test_location_sessions.py` (27), `tests/life/test_state_write_recheck.py` (5), `tests/life/test_storefront_recovery.py` (7: map identity substitution, quote boundaries), `tests/life/test_storefront_state_validation.py` (3), `tests/life/test_travel_trajectory.py` (7: `location_track` events on frozen geometry), `tests/life/test_timeline_labels.py` (1).
- Integration tests pinning export bytes: `tests/integration/test_dayrun_complete_days.py::test_complete_two_person_day_uses_real_tools_memory_and_restoration` (compares `activities.jsonl`/assets sha256 with `dayrun-layering-baseline.json` / `dayrun-shared-rules-baseline.json`, recomputes timelines), `tests/integration/test_external_environment_lifecycle.py` (3), `tests/integration/test_errand_delivery_windows.py`, `tests/integration/test_meal_checkpoint.py`, `tests/integration/test_errand_checkpoint.py`, `tests/integration/test_run_manifest.py` (9), `tests/integration/test_same_second_continuation.py`.

### 4.5 Ledgers that reference this subsystem (deletion/rename cost)

- `tests/fixtures/acceptance_requirements.json`: requirement rows citing `tests/footprints`, `tests/delivery`, `src/worldmock/footprints/models.py` + `quality.py` wake-reason handling, `scripts/inspect_run.py` `rerun` metrics, `purchases.jsonl` dedup, `inspect_run continuation`.
- `tests/fixtures/idle-fillers-acceptance.json` and `tests/idle_fillers_acceptance.py`: T18 -> `test_event_fields.py::test_wake_reason_vocabulary_*`; mutation `vocabulary_lacks_<member>` on `worldmock.footprints.models::WAKE_REASON_NAMES`; anchors `footprints/models.py::validate_decision_rhythm`, `quality.py::build_quality_report`, `evaluation/workload_metrics.py`.
- `tests/errands_workday_acceptance.py`: evidence nodes in `tests/delivery/test_windows.py`, `tests/delivery/test_publication.py::`, `tests/footprints/test_errand_export.py::`, `tests/footprints/test_quality.py`, `tests/footprints/test_goal_export.py`, `tests/evaluation/test_errand_metrics.py::`, `tests/evaluation/test_workload_metrics.py::`; MUTATION rows `worldmock.life.models::WorldState.schema_version`, `worldmock.delivery.pipeline::_require_current_world_schema`, `worldmock.life.models::AgentState.errands`, `worldmock.life.models::ErrandChange.change`, `worldmock.life.models::_validate_plan_goals*`, `worldmock.delivery.windows::_public_event_data.public`, `worldmock.delivery.windows::_decision_prompt_source.candidates`, `worldmock.evaluation.errand_metrics::errand_metrics.registered`, `worldmock.footprints.models::WAKE_REASON_NAMES`; owned-file lists include `scripts/inspect_run.py`, `src/worldmock/delivery/{pipeline,windows}.py`, `src/worldmock/evaluation/{behavior_metrics,errand_metrics,workload_metrics}.py`, `src/worldmock/footprints/{models,quality}.py`, `src/worldmock/life/models.py`.
- `tests/dayrun_fidelity_acceptance.py`: prefixes `_QUALITY`, `_PURCHASES`, `_RENDER`, `_GOAL_METRICS`, `_GOAL_EXPORT`; `tests/delivery/test_windows.py::test_retired_control_request_shapes_are_rejected_by_delivery`; owned files `scripts/render_day.py`, `delivery/windows.py`, `evaluation/{goal_metrics,public_evidence}.py`, `footprints/{purchases,quality,timeline_labels}.py`, `life/models.py`.
- `tests/persona_prompt_acceptance.py`: `_PURCHASES`, `_EVENTS`, `_QUALITY`, `_WINDOWS`, `_BEHAVIOR`; `tests/footprints/test_store.py::`, `tests/delivery/test_publication.py::`, `tests/footprints/test_render_day.py::test_resolves_names_from_seven_layer_observation_evidence_keys`; owned files include `delivery/{pipeline,replay,windows}.py`, `evaluation/{behavior_metrics,validation}.py`, `footprints/{export,models,purchases,quality,store,validation}.py`, `life/{models,sessions}.py`, `tests/delivery/prompt_fixtures.py`, `tests/footprints/test_purchases.py`.
- `tests/time_and_energy_acceptance.py`: `tests/footprints/test_verified_experiences.py::test_arrival_snapshot_at_origin_still_projects_arrived`, `tests/delivery/test_publication.py::test_delivery_checks_world_schema_before_preparing_windows`, `test_event_fields.py::*wake_reason*`, `test_quality.py::test_decision_rhythm_counts_same_moment_rounds`, `test_quality.py::test_repeat_watch_counts_only_sessions_restarted_from_the_beginning`, `test_store.py::test_action_request_cutoff_counts_only_thinking_requests_with_the_action_tools`, `tests/evaluation/test_adapters.py::test_start_items_follow_the_started_activity_kind_then_the_order_then_the_way`, `tests/evaluation/test_behavior_metrics.py::test_wait_is_judged_by_the_started_activity_kind_not_the_request`.
- `tests/tool_interaction_acceptance.py`: mutations `worldmock.footprints.quality::_name_rejection_quality.code` (evidence `test_quality.py::test_protocol_quality_uses_structured_evidence_wit...`), `worldmock.footprints.purchases::_order_attempts`; `tests/test_tool_interaction_acceptance.py` owned files `life/models.py`, `footprints/{purchases,quality}.py`.
- `tests/shared_rules_acceptance.py`: `tests/evaluation/test_shared_event_parsing.py::test_metric_and_review_date_blocks_share_full_window_partition`, `::test_canonical_adapter_validates_each_event_once_for_statistics_and_public_evidence`.
- `tests/fixtures/mutation-symbol-baseline.json` (source_commit `2cf2301`): ids `errands:version_8_world_state_accepted`, `errands:version_8_export_passes_delivery_gate`, `errands:checkpoint_omits_errands`, `errands:reminder_recorded_without_delivery`, `errands:query_*`, `tool:pagination_supply_window`, `tool:assortment_window_cut`, `tool:native_purchase_route_loses_way`, `tool:invalid_request_purchase_projection`, `tool:quality_discards_structured_code` bind sha256 of generated mutants of the current source; any edit to the anchored symbols changes the baseline.
- `tests/fixtures/layering-public-api.json`: LifeEngine method signatures (`checkpoint`, `apply_counterfactual`, `prepare_pending_work_review`, `read_task_summary`, `record_model_exchange`, ...). Removing `prepare_pending_work_review` changes it.
- `tests/test_single_source.py` OWNERS: `footprints/models.py: (canonical_json, sha256_json, is_sha256)`, `footprints/assets.py: (atomic_write,)`, `footprints/intervals.py: (valid_interval, is_body_settlement, body_settlements)`, `footprints/timeline_labels.py: (window_days, local_day_bounds, split_local_days)`, `life/models.py: (DERIVED_DURATION_CAP_SECONDS, CONSUME_DURATION_CAP_SECONDS)`, `evaluation/metrics.py: (date_blocks,)`, `evaluation/adapters.py: (parse_world_event,)`.
- `docs/dev/test-durations.json` lists every node (incl. `worldmock.life.travel.sample_location_track` coverage rows); regenerated by the full runner, not hand-edited.

---

## 5. What the proposal changes here

Legend: DELETE / REWRITE / ADD. "Smallest design" reuses: `FootprintStore` + `EventDraft` (no new event types; new facts go in `environment_event.data.kind` and `activity_progress.data`), `WorldState.sessions` (push eligibility), `pending_events`/`ScheduledKind` (hourly push ticks), `random_state` (content draws), checkpoints (state only), `activities.elapsed_seconds` (work hours), `purchases.jsonl`-style projections (new facts files).

### 5.1 Random weighted connected map replaces the real map (位置和出行导出同步改为地点、路径和距离)

- DELETE `src/worldmock/life/travel.py::sample_location_track`, `LocationTrackRequest`, `LOCATION_TRACK_KIND`, `_route_geometry`, `_cumulative_distances`, `_interpolate`, `_haversine_meters` and the `environment_event kind=location_track` payload (today this sampler has no production caller: only `tests/life/test_travel_trajectory.py` and `test_domain_branch_coverage.py` invoke it, as `docs/dev/research/dayrun-behavior/concurrency-design-review.md` already notes; no run ever persisted a `location_track` event, so no export/handoff reads one) (`position_lonlat`, `route_response_*`, `sampling_rule`); DELETE `tests/life/test_travel_trajectory.py` and `tests/life/test_domain_branch_coverage.py::test_location_track_rejects_broken_geometry_and_paths`; DELETE the 05 design row and `spec-life-runtime-导航与配送` bullets that require geometry sampling.
- REWRITE travel `ActivityState`: DELETE `route_progress_meters` (keep progress as `elapsed_seconds/planned_duration_seconds`), keep `travel_mode`, `destination_id`; `data.route_id` -> `data.path: list[str]` (node ids, origin first), `data.distance_meters: int`, `data.speed_mps` (system constant for the mode), keep `data.arrival{location_id, access_status}` (drop `missing_qualifications/source` only if qualifications leave the resource engine; otherwise keep). `activities.jsonl` and `adapters._require_activity_fields` closed set change accordingly.
- REWRITE `DeliveryState`/`orders[]`: DELETE `route_id`, `source.route`; ADD `path: list[str]`, `distance_meters: int`, `prep_seconds: int` (evidence for `expected_at = created_at + prep + ceil(distance / delivery_speed)`).
- REWRITE run manifest `map_dataset` (`storefront_state.map_provenance`) to the graph identity: `{dataset_id, version, random_seed, graph_file{path, sha256}, node_count, edge_count, rules_version}`; DELETE `coordinate_system`, `prepared_at`, `source`, `locations_file`, `supplies_file`, `routes_file`, `category_assignment_sha256`. `delivery/pipeline.py::deliver` keeps copying `frozen_run_manifest["map_dataset"]` (no code change beyond the key set); `WorldMock-Dataset/tools/labelkit_input.py` provenance keeps the same key.
- REWRITE observation keys read by `scripts/render_day.py::_absorb_observation` (`locations[]` without `longitude/latitude`) and `scripts/inspect_run.py::walking_share` (`$.data.routes[].mode` disappears -> derive available modes from the system constant table or from `activity_progress` travel modes only).
- REWRITE `evaluation/public_evidence.py::_start_shape`/`_activity_object_key`: travel `object_key` = `data.path` digest or `destination_id` (closed-set `object_type="route"` can stay as the label of "a path"); `review._reject_identity_keys` keeps `route_id` (harmless) and ADDs `path`, `location_id` already covered.
- Verified experience `arrived` unchanged (`location_id`, `access_status`).
- Export/quality: no new file; `timeline` travel label stays "<方式>去<地名>".

### 5.2 Same-type stores share one menu; no per-store stock/quotes (建议); delivery reaches every registered location

- DELETE from `WorldState`: `supply_stock`, `supply_quotes` (and `SupplyQuoteState`, `_validate_supply_visibility`, `supply_stock.keys()==supply_quotes.keys()` invariant); `opening_exceptions` + `_validate_opening_exceptions` + `OpeningInterval` unless the decision point "营业与供给" keeps type-level hours (then keep a per-type constant, not state). DELETE `same_moment_presentation.pinned_supply_ids` (no browse of supplies).
- DELETE from `AgentState`: `visible_supply_ids`, `supply_browsed_at`, `supply_browsed_location` (and `state_commit.flush_supply_visibility`, `_validate_supply_visibility`); DELETE `taste_weights` (proposal: no taste ranking from ingredient tags; the field is only a presentation ranking signal — keep only if the persona subsystem still needs ordering; recommend DELETE).
- DELETE `ScheduledKind` `price_change`, `supply_quote_change`, `product_release` and their `environment_event` payloads; DELETE `PurchaseItem.price_version`, `PurchaseItem.supply_id` -> `food_id` + `store_id`; `PurchaseOrderRequest` ADD `app_id: str | None` (null = in-store).
- REWRITE `DeliveryState` to `{order_id, agent_id, food_id, store_id, app_id | null, quantity, unit_price_fen, delivery_fee_fen, total_price_fen, destination_id, fulfillment{delivery|pickup}, recipient_id, request_message_id, created_at, expected_at, status, path, distance_meters}`; DELETE `supply_id`, `location_id` (-> `store_id`), `seller_account_id` (payee account derives from the store type/registry; if accounts per store remain in the ledger keep `seller_account_id` as the ledger counterparty — see open question), `price_version`, `source`.
- `delivery_arrival` payload unchanged (`order_ids`, `orders[]`, `inventory_changes[]`); the "every registered location" rule is enforced in basket/exchange, not here. The event `data.orders[]` rows follow the new `DeliveryState`.
- REWRITE `footprints/purchases.py`: `purchase` row -> `agent_id, order_id, food_id, store_id, app_id, quantity, total_price_fen, fulfillment, recipient_id, request_message_id, created_at, action_id, source_event_id` (DELETE `supply_id`, `channel_id`); DELETE `app_browse` fact kind and `BROWSE_ACTIVITY_KIND` (replaced by `app_use` in 5.4); keep `order_attempt`.
- REWRITE observation private rows read by `render_day.name_index`: `products` -> `foods[{food_id, name, category}]`; `supplies` disappears; `locations[]` keeps `location_id, name, kind`.
- Quality: `meal_diversity` keyed by `food_id` (rename only); `review._reject_identity_keys` ADD `food_id`, `store_id`, `app_id`.
- Checkpoint sealing list in `recording-engine-地图与供给的恢复身份` shrinks to `deliveries`, `inventory`, `contact_holdings`, `presentation_state`.

### 5.3 Three food categories with fixed kcal; food binds to stores or `all`

- REWRITE `activity_progress.data.consumption` -> `{use_mode: "eat", items[{food_id, category ∈ {snack, drink, meal}, interval_portion, cumulative_portion, interval_kcal}], location_id, food_efficiency}` where `interval_kcal = CATEGORY_KCAL[category] * interval_portion` (system constants in WorldMock, e.g. `life/food_rules.py::FOOD_CATEGORIES = {snack: (200, 300s), drink: (100, 180s), meal: (600, 1200s)}`); keep `food_efficiency` (proposal keeps utilisation efficiency and body cap).
- REWRITE consume `ActivityState.data`: keep `request{items[{food_id, quantity}], use_mode, duration_seconds}`, `opened`, `consumed`, `discarded`; DELETE `inventory_recipe` and `food_energy_kcal_per_hour` (cooking exits per recommendation; see open question); `ConsumeRequest.duration_seconds` must equal `sum/max` of fixed category duration × portions (validation moves to the exchange engine).
- REWRITE verified experience `consumed` row: `product_id` -> `food_id`, `interval_quantity/cumulative_quantity` -> `interval_portion/cumulative_portion`, ADD `category`; `experiences._KIND_FIELDS["consumed"]` and `_consumption_item` change; `validate_verified_experiences` (used by `custom/agents/life_agent.py`) follows.
- REWRITE `body_settlement.data.body_settlement.food_energy_kcal` stays (net energy), no new field.
- `quality._food_efficiency_samples`, `behavior_metrics._meal_sessions`, `inspect_run.eat_progress` read the renamed keys; `errand_metrics._consumed` drops the recipe branch (`test_a_making_request_points_at_the_recipe_output_not_at_the_kit_it_uses_up` deleted with cooking).
- DELETE scenario/product fields consumed here: `energy_kcal_per_unit`, `serving_quantity`, `serving_unit`, `purchase_price_fen_min/max`; `observation.data.products[]` -> `foods[]` with `price_fen` on the food.

### 5.4 Apps: catalog from the environment; person picks an app, system picks content; five content types with summaries

- REWRITE `ActivityKind`: DELETE `play_game`, `browse` (TextWorld chain and supply browsing leave the running chain; "刷手机却不记录 app" entries are replaced); REWRITE `watch` -> `use_app` (recommended rename; keeping the literal `watch` is the alternative with less churn in `readable.py`, evaluation maps and tests, but the semantic is "use an app" and `target_id` becomes `app_id`, not a content id). `ActivityStartParams.target_id` for `use_app` = `app_id`; `_TARGETED_ACTIVITY_KINDS` updated. DELETE `ActivityChange.CHANGE_SPEED` and `ActivityState.playback_speed` (consumption time is the fixed content duration; see open question).
- ADD on `use_app` `ActivityState.data`: `app_id`, `content_id` (chosen by the system at start or after completion), `content_category ∈ {article, image_text, short_video, long_video, live}`, `summary` (copied from the catalog so the event is self-contained), `content_duration_seconds`, `push_id | null` (set when the designated pushed content was taken), `draw_sequence: list[content_id]` (every content drawn inside this activity, for "完整结束后若使用时长尚未耗尽，随机抽取下一条"). Progress payload `activity_progress.data.content` = `{content_id, app_id, position_before_seconds, position_after_seconds, completed: bool, completed_count_after: int}` (replaces `data.revealed`/cues).
- REWRITE `ContentProgress` -> `{content_id, consumed_seconds, completed_count}` (`position_seconds` keeps "resume from where it stopped"; `exposure_count` -> `completed_count` counts completed rounds). `AgentState.content_progress` keeps the shape `dict[content_id, ContentProgress]` (shared across apps, as the proposal requires).
- REWRITE verified experience `played` -> keep kind name `played` (or `consumed_content`) with `content_id, app_id, position_before_seconds, position_after_seconds, interval_start, interval_end`; reads `activity.data` + `activity_progress.data.content`.
- REWRITE `quality._CONTENT_TYPES` -> `{use_app, read_mail}`; `_watch_repeats` -> `repeat_watch` counts rounds from `completed_count` (DELETE `data.repeat`); ADD `content_contact.by_app` and `by_content_category`.
- REWRITE `render_day.py`: DELETE `_browse_label`, `play_game` branch; `_media_label` -> "用<app 名>看《<content title>》" (name index gains `apps{app_id: name}` from observation `data.apps[]`; `contents{content_id: title}` from `data.contents[]`); `readable.BROWSE_LABELS` -> the five content category labels + app label; `ACTIVITY_KIND_LABELS["use_app"]`.
- REWRITE `evaluation/public_evidence.py`: `_TARGETED_ACTIONS` -> `use_app -> ("entertainment", "use", "application", object_key=app_id, attributes{content_category})` plus a second `PublicEvidence` row `("entertainment", "view", "content", content_id)` per content completion; DELETE `play`, `browse`; `_ACTION_OF_KIND` updated; this aligns WorldMock with the legacy `appUsageEvent` (`open/close application`) and `notificationEvent` rows so `metrics` finds shared `interaction/object_type` support. `adapters._DIGITAL_ACTIVITY_TYPES` -> `{work, use_app, read_mail}`; `manual/evaluation.md` `worldmock_activity` keys and `tests/fixtures`/dataset `scenarios/evaluation.json` mappings follow.
- Export: ADD artifact `app-usage.jsonl` (manifest `artifacts` closed set becomes 5 keys; export manifest schema 2 -> 3; `validation._validate_identity` set and `_read_artifact` updated) with rows `fact_kind ∈ {app_use, content_consumed, push_delivered, push_consumed, push_released}`: `app_use{agent_id, activity_id, app_id, started_at, ended_at|null, status, source_event_id}`, `content_consumed{agent_id, activity_id, app_id, content_id, category, position_before, position_after, completed, push_id, source_event_id}`, `push_*{agent_id, push_id, app_id, content_id, session_id, occurred_at, source_event_id}`. Rationale: the phone handoff (`simulate_phone.py`) today reverse-engineers apps from sellers and `activity_apps`; with this file it reads foreground sessions and notifications directly ("手机导出直接读取实际动作中的 app、店铺和内容身份"). Alternative with less surface: fold `app_use` into `purchases.jsonl` — rejected because the file name and `project_purchase_facts` consumers (dataset, `inspect_run._repeat_orders`, `_purchase_settlements`) are purchase-specific.

### 5.5 Push notifications 08:00–22:00 local, pinned to the same session

- ADD `ScheduledKind.APP_PUSH` and compile one `ScheduledEvent{kind: app_push, due_at: each local hour 08:00..21:00 of each day, agent_id, data{}}` per person at run creation (same mechanism as `payroll`/`bill_due` in `scheduling/compile.py`), so restores never re-generate: the tick is consumed from `pending_events` exactly once.
- ADD `environment_event` `data.kind = "app_push"` (`visibility=agent`, `agent_id` = person, `caused_by` = []): `{kind, push_id, app_id, content_id, category, summary, session_id, pushed_at}`; `push_id = sha256(run_id, agent_id, session_id, app_id, due_at)` (deterministic). No push when no eligible app (eligible = bound contents and not already pushed in this session); nothing is written then.
- ADD on `SessionState` (reuses location/transit sessions per the recommendation): `pending_pushes: dict[str, PendingPush]` keyed by `app_id` with `PendingPush{push_id, content_id, pushed_at}` and `pushed_app_ids: list[str]`. A new session object starts empty, so "session 切换时直接清除其推送资格" needs no extra code in `sessions.open_stay/begin_transit/end_transit`; `_validate_session_collection` ADDs: pending pushes only on `active` sessions, `pushed_app_ids ⊇ keys(pending_pushes)`. Checkpoints seal them automatically (sessions are in `WorldState`).
- Consumption rule in the simulation engine: starting `use_app` with `app_id` in `session.pending_pushes` takes that `content_id` and writes `data.push_id`; completion of that content in any app (`activity_progress.data.content.completed == true`) removes every pending push pointing at it in the current session and writes `environment_event kind=app_push_released{push_id, content_id, reason: completed}`; `app-usage.jsonl` projects these.
- Wake reasons: ADD `app_push` to `WAKE_REASON_NAMES` (footprints) and `schedule.WakeReason` (same batch, like `errand_reminder_due`), not in the sleep-allowed set; the push is a notification entering the visible context (progress layer source event) and the current rule "message_arrival offers a decision" is the closest precedent. This changes `test_event_fields.py` vocabulary tests, idle-fillers acceptance anchor `WAKE_REASON_NAMES`, and `quality._decision_rhythm_quality` counts automatically.
- Quality: ADD `quality.app_pushes{pushed, opened_in_session, consumed_designated, released_by_completion, expired_with_session, outside_window_violations (must be 0)}` (schema 4 -> 5). Delivery windows: `app_push` is an `environment_event` with `visibility=agent`, so `windows._public_event_data` returns `{}` for it unless the spec wants the summary visible to LabelKit; recommend projecting `{kind, app_name, category, summary}` (public, no ids) — this is the first environment_event with a non-empty public projection, so `PROGRESS_EVIDENCE_EVENTS` already covers it.

### 5.6 Work = topic + target hours; the model narrates at progress reports

- DELETE `MeetingMinutes`, `SubmitWorkRequest`, `SubmitWorkActionRequest`, `REQUEST_MODELS["submit_work"]`, `ContractKind`, `PendingAction.work_review`/`work_review_event_id`, `WorldState.paid_task_ids`, `AgentState.material_reveals`, `visible_task_ids`, `known_task_ids` (if topics are assigned by the scenario they are always visible; keep `visible_task_ids` only if mail-based availability stays — open question), wake reasons `work_review_result` (DELETE), `task_available`/`task_due_soon` (keep only if topics keep deadlines/mail), `ScheduledKind.work_available/task_expired` (same), `model_request.purpose == "work_review"` (quality `_usage_by_purpose` just stops seeing it), `activity_progress.data.work_material`, `data.activity_efficiency` (effort factor leaves work completion; keep `body_settlement.work_efficiency` only if sleep-based efficiency still affects fatigue, which the proposal keeps for body), `ActivityState.effective_work_seconds`, `data.required_skill/skill_level/work_notes_multiplier`, `action_result.data.work_review/payment_fen/task_id`, `environment_event work_available/task_expired`, evaluation `_work_result`, `_world_request_shape(submit_work)`, `_world_environment(work_available/task_expired)`, `workload_metrics._settled_tasks`/`effective_work_seconds`/`TaskSite.appointment_at`, `quality.conversion_efficiency.work`, `render_day._work_label` task titles -> topic title, fixtures `tests/fixtures/dayrun_work_reviews.json`, `tests/life/fixtures/work_review_deepseek_responses.json`.
- ADD work `ActivityState.data`: `topic_id`, `topic`, `target_seconds`, `worked_before_seconds` (sum of earlier work activities on the topic at start; derived via existing `work.work_totals` over `activities`). Completion = `sum(elapsed_seconds of work activities with target_id == topic_id) >= target_seconds` (pure derivation from `WorldState.activities`, no new state). Narrative = existing `report_status.items[].summary` -> `ActivityState.progress_note`/`note_history` (already persisted and exported in `activities.jsonl`); ADD `activity_progress.data.work_report{topic_id, worked_seconds, remaining_seconds, previous_note, note}` when the progress report that caused the settlement carried a summary, so `quality`/`workload` can count narratives without parsing prompts. ADD `quality.work_topics{topics, completed, worked_seconds_by_topic, reports}`.
- `workload_metrics`: `WORKLOAD_KEYS` drop `effective_work_seconds`; `task_outing_seconds` reinterpretation: topics have no required location other than the scenario workplace, so DELETE the task-outing exception (or keep for appointment-style topics if the scenario keeps them — open question).
- Payroll unchanged (`ScheduledKind.payroll`, `scheduling/effects.py`), ledger unchanged.

### 5.7 Classification and exchange rules fixed in WorldMock; old formats rejected

- Run manifest: ADD `rules_version` (WorldMock constant, e.g. `"worldmock-rules-1"`) next to `code_sha256`; DELETE `category_contract_version`, `category_contract_sha256`, `exchange_rules_sha256`, `catalog_content_sha256` (replace with a single `environment_sha256` = digest of the published environment manifest); `recording-engine-持久化结构` paragraph and `contracts-品类契约与目录字段` rows rewritten; `checkpoint.manifest_sha256` continues to bind it.
- `WORLD_SCHEMA_VERSION = 10`; WS9 (and earlier) rejected by `WorldState.schema_version`, `pipeline._require_current_world_schema`, and the tests in `tests/life/test_session_state.py` (`test_world_state_schema_version_10_is_valid`, `..._9_is_rejected`), `tests/delivery/test_publication.py` (`..._rejects_version_nine_exports`). `Event.schema_version` stays 1 (envelope unchanged); export manifest 2 -> 3 (new artifact); quality 4 -> 5; delivery manifest 2 -> 3 (new `map_dataset` shape, new `app-usage` source descriptor if copied). No migration or dual-read anywhere; `validate_export._validate_identity` rejects manifest schema 2.
- WS10 delta table (for `contracts-领域枚举` and `recording-engine-检查点创建`): DELETE `WorldState.supply_stock/supply_quotes/opening_exceptions/paid_task_ids`, `same_moment_presentation.pinned_supply_ids`, `AgentState.visible_supply_ids/supply_browsed_at/supply_browsed_location/material_reveals/taste_weights/visible_task_ids/known_task_ids`, `ActivityState.effective_work_seconds/route_progress_meters/playback_speed`, `ActivityKind.play_game/browse`, `ActivityChange.change_speed`, `ContractKind`, `SupplyQuoteState`; REWRITE `DeliveryState` (5.2), `ContentProgress` (5.4), `ActivityKind.watch -> use_app`, `ScheduledKind` (DELETE `price_change/supply_quote_change/product_release/work_available/task_expired`, ADD `app_push`); ADD `SessionState.pending_pushes/pushed_app_ids`, `environment_event kinds app_push, app_push_released`, wake reason `app_push`.

### 5.8 Phone handoff (WorldMock-Dataset) — interface this subsystem must offer

- `WorldMock-Dataset/tools/simulate_phone.py`: DELETE the phone config and everything derived from it — `content_apps_by_id`, `game_apps_by_id`, `activity_apps`, `purchase_apps_by_seller`, `payment_sellers`, `payment_app_id`, `message_apps_by_recipient`, `scheduled_sessions`, `delivery_notification_sellers`, `activity_app`, `location_at`, `bound_to_scenario` and the `persona_source` pin; READ `app-usage.jsonl` (`app_use` foreground sessions with `app_id`/`content_id`, `push_delivered` notifications) and `purchases.jsonl` (`app_id` on orders; delivery notifications from `delivery_arrival.orders[].app_id`); app display names from the environment app catalog (`frozen_run_manifest` must carry the environment identity, see 5.7, so the dataset can locate the catalog). Keep the `scenario_sha256`/`events`/`activities` digest binding and the `source_kind="scenario"` marker on synthetic rows unless the spec renames it (the phone rows are then projections of recorded app facts, not scenario inventions, so `source_kind="environment"` or a new value is the honest choice; `labelkit_input.phone_rows` currently rejects anything but `scenario`). Today the formal structure-6 scenarios have no phone config at all, so this script is already inoperable on them — the proposal's "删除卖家到 app、内容到 app、付款 app 等事后推断映射" removes dead configuration, not a live path.
- `WorldMock-Dataset/tools/labelkit_input.py`: `handoff_event` already projects control requests to `items[{activity_name, summary}]` and rejects a missing `errands` key, matching `delivery/windows._public_event_data`; KEEP that; REWRITE `provenance.map_dataset` to the graph identity and `phone_rows` to the new row shape/`source_kind`; `VISIBLE_EVENT_TYPES` unchanged (the new `app_push`/`app_use` facts are `environment_event`/`activity_progress` rows and pass the filter as-is). The dataset preflight scripts (`preflight_persona.py`, `preflight_runtime.py`, `preflight_routes.py`, `preflight_data.py`) that cross-check `purchase_apps_by_seller`/`app_display_names` against `purchase_channels` go with the phone config; `WorldMock-Dataset/tests/test_phone_purchase_projection.py` is rewritten against `app-usage.jsonl`/`purchases.jsonl`.
- DELETE `scenario.phone_simulation_config/_sha256`, `profile/coverage_apps.PhoneSimulationCoverage/purchase_channels`, `life/load_checks` channel admission, `world_query_channels` private key (replaced by the app catalog in the resource engine; outside this subsystem but it is the source of `channel_id`).

---

## 6. Conflicts with frozen clauses

| Document | Anchor / heading | Gist of the clause the proposal contradicts |
| --- | --- | --- |
| `docs/CONTRACTS.md` | `contracts-领域枚举` | `WorldState.schema_version = 9`; `ActivityKind` includes `play_game`, `browse`, `watch`; `ActivityChange.change_speed`; `ContractKind`; `ScheduledKind` includes `price_change`, `supply_quote_change`, `product_release`, `work_available`, `task_expired` |
| `docs/CONTRACTS.md` | `contracts-冻结地图与场景引用` | real map (`MapDatasetReference`, WGS84, routes file, map structure 3), scenario `supplies` references, "世界状态结构 9" border |
| `docs/CONTRACTS.md` | `contracts-类别供给的构建合同` | per-store menus differ, price-position quote formula, `category_assignment_sha256` |
| `docs/CONTRACTS.md` | `contracts-营业与报价声明` | per-supply quotes with versions and temporal declarations |
| `docs/CONTRACTS.md` | `contracts-地点发现与购买身份` | settlement by `supply_id`+`price_version`, purchase pairing coefficients; channels derived from phone config; `channel_id` |
| `docs/CONTRACTS.md` | `contracts-持久化当下事实` | `supply_stock`, `supply_quotes`, `opening_exceptions`, `visible_supply_ids`/`supply_browsed_*` must persist; "世界结构版本为 9" |
| `docs/CONTRACTS.md` | `contracts-供给结算与事件载荷` | `price_change/supply_quote_change/product_release` payloads; order keeps `supply_id/seller_account_id/price_version/route_id/source`; `channel_id` only in `orders[]`/`purchases.jsonl` and not in world state |
| `docs/CONTRACTS.md` | `contracts-private-fields` | forbidden-key list names `route_id`, `supply_id`, `price_version`, `taste_weights`; "路线坐标与几何不得进入提示" presumes geometry exists |
| `docs/CONTRACTS.md` | `contracts-语料内容目录字段` | corpus `segments` (media ranges), `attributes` (nutrition), `assets`, `evaluation_refs` |
| `docs/CONTRACTS.md` | `contracts-品类契约与目录字段` | run manifest/checkpoint/export freeze `category_contract_*`, `exchange_rules_sha256`, `catalog_content_sha256`; purchase/food/work operators |
| `docs/CONTRACTS.md` | `contracts-seven-layers` (rows `state`, `submit_work`) and `contracts-person-tools` (`submit_work` row) | per-serving kcal in `state`; `submit_work` tool with `deliverable` |
| `docs/design/05-recording-engine.md` | `recording-engine-事件正文与资源证据` | `action_result.data.orders[].channel_id`; `work_review`; `environment_event` kinds `work_available`/`task_expired`; no `app_push` kind |
| `docs/design/05-recording-engine.md` | `recording-engine-地图与门店供给证据` | map identity with per-source licences; discovery results with routes/quotes/stock; purchase evidence by supply/seller/price_version/channel_id; `data.supplies` rows |
| `docs/design/05-recording-engine.md` | `recording-engine-activity_progress-事件` | `consumption.items[].interval_quantity/cumulative_quantity` by `product_id`; work `activity_efficiency` |
| `docs/design/05-recording-engine.md` | `recording-engine-已核验经历与主观记忆` | `consumed` carries product/location/quantity; `played` carries content id from media positions revealed by subtitles |
| `docs/design/05-recording-engine.md` | `recording-engine-检查点创建` | "世界结构版本为 9"; sealed-field lists for WS8/WS9 |
| `docs/design/05-recording-engine.md` | `recording-engine-地图与供给的恢复身份` | checkpoint must seal `supply_stock`, `supply_quotes`, `opening_exceptions`, `visible_supply_ids`, `supply_browsed_*`; map identity mismatch rejects open |
| `docs/design/05-recording-engine.md` | `recording-engine-持久化结构` | run manifest freezes catalog identity quadruple |
| `docs/design/05-recording-engine.md` | `recording-engine-导出行为` | export manifest schema 2 with exactly four artifacts; `purchases.jsonl` columns `supply_id`, `channel_id`, `app_browse`; `frozen_run_manifest` keeps map version, digests, per-source licences |
| `docs/design/05-recording-engine.md` | `recording-engine-质量报告` | schema 4 group list; `content_contact` repeat watch by `data.repeat`; `conversion_efficiency.work`; `model_usage.by_purpose` `work_review` |
| `docs/design/05-recording-engine.md` | `recording-engine-调试工具` | `rerun` walking share from `data.routes`; `workday` from scenario tasks/payment |
| `docs/design/06-delivery.md` | `delivery-接收记录引擎导出` | gate `schema_version == 9`; delivery manifest schema 2; stage identity sentences (map 3, scenario 6, WS9) |
| `docs/design/06-delivery.md` | `delivery-地图与门店供给的证据窗口` | purchase evidence by supply identity; only actually received routes/quotes/stock; map version/licence pass-through |
| `docs/design/06-delivery.md` | `delivery-manifestjson-结构` | manifest must keep map set identity, digest, per-source licence and notice |
| `docs/design/09-io-formats.md` | `io-formats-worldstate世界状态` | `schema_version` Literal[9], `supply_stock`, `supply_quotes`, `opening_exceptions`, `DeliveryState` without channel, `pinned_supply_ids`, `SupplyQuoteState`, `data.supplies` rows and kcal/price wording |
| `docs/design/09-io-formats.md` | `io-formats-agentstate人物状态` | `visible_supply_ids`, `supply_browsed_at/location`, `taste_weights`, `visible_task_ids`, `content_progress` (position/exposure), `material_reveals` |
| `docs/design/09-io-formats.md` | `io-formats-activitystate活动状态` | `effective_work_seconds`, `media_position_seconds`, `route_progress_meters`, `playback_speed`, `data.opened` semantics tied to per-product servings |
| `docs/design/09-io-formats.md` | `io-formats-当前工具请求查询与错误格式` | `prefer`/`from`/`query_world` against supplies and channels; work input `appointment` |
| `docs/dev/SPEC-labelkit-delivery.md` | `persona-prompt-下游` | `purchases.jsonl` with `app_browse`, `channel_id` not in world state, WS9, quality 4, delivery manifest 2 |
| `docs/dev/SPEC-labelkit-delivery.md` | `dayrun-fidelity-下游与质量报告` | `orders[] += channel_id`; `simulate_phone.py` reads `channel_id`; `prepare_labelkit_input.py` memory trimming rule |
| `docs/dev/SPEC-labelkit-delivery.md` | `spec-labelkit-delivery-证据窗口与输入结构` | "六种个人事务事件 ... environment_event 公开投影为空对象" generalises to all environment events; an `app_push` projection would be the first non-empty one |
| `docs/design/07-evaluation.md` | `evaluation-适配器职责` | object types `content/game/route/task`; `submit_work` result states; order/payment/fulfilment by supply |
| `docs/design/07-evaluation.md` | `evaluation-门店选择与供给假设的比较边界` | map set, supply assumptions, routes, purchase pairing as experiment conditions; diversity by `product_id`, store choice by `supply_id` |
| `docs/design/07-evaluation.md` | `evaluation-报告项reports` (and `docs/VERIFICATION.md#verification-报告项report-only`) | `workload.effective_work_seconds`, `task_outing_seconds` from task sites, `meal_diversity` by `product_id` |
| `docs/VERIFICATION.md` | `verification-地图与门店供给` | every gate (unique frozen real map, route modes, per-store stock/quotes, visible supplies, self-pickup vs delivery scope, mutation list) |
| `docs/VERIFICATION.md` | `verification-测试矩阵` rows 工作生产, 内容知识, 观察投影, 指名购买, 资源结算 | work materials/review, subtitle isolation and game restore, per-product kcal labels, `channel_id`, recipe inventory |
| `docs/VERIFICATION.md` | `errands-workday-度量与下游-首次受影响发布身份` | "世界状态 ... 当前结构边界以 `contracts-冻结地图与场景引用` 为准" and the digest-binding table (adds `rules_version`) |
| `docs/EXPERIMENT.md` | `experiment-内容与语料绑定` | Open Food Facts nutrition, Sintel SRT, TextWorld, AMI work materials, real map routes |
| `docs/EXPERIMENT.md` | `experiment-地图与门店供给的实验身份` | real coordinates, two seeded maps, per-store menus, quote formula, WS9/quality 4/export 2 identities |
| `docs/EXPERIMENT.md` | `experiment-能量感知标签` | per-product kcal rounding label |
| `docs/dev/SPEC-life-runtime.md` | `spec-life-runtime-导航与配送` | location track along real route geometry; delivery ETA from merchant prep + queue + road transport; traffic data per date |
| `docs/dev/SPEC-life-runtime.md` | `spec-life-runtime-具体活动结算` (rows 工作, 看节目, 游戏, 购物) | work materials/output/review; subtitles by playback range; game engine; purchase by supply/stock |
| `docs/dev/SPEC-life-runtime.md` | `spec-life-runtime-内容目录契约`, `spec-life-runtime-内容提供行为` | per-category attribute schemas (nutrition), work materials revealed by progress, oracle summaries |
| `docs/dev/SPEC-life-realism.md` | `spec-life-realism-呈现与导出` | `verified-experiences.jsonl` content-contact semantics; (timeline rule stays) |
| `docs/dev/SPEC-dayrun-behavior.md` | `spec-dayrun-behavior-事件投影`, `spec-dayrun-behavior-真实音频来源与揭示` | `consumed` by product/quantity, `played` by real media position; podcast segment reveal |
| `docs/dev/SPEC-map-and-storefront.md` | `spec-map-and-storefront-记录恢复与交付` | export keeps map version/source/licence; checkpoint round-trips stock/quotes/exceptions/visible supplies; `query_world` against `data.supplies` |
| `docs/dev/SPEC-map-and-storefront.md` | `dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店` | channels from phone config; `channel_id` in `orders[]`/`purchases.jsonl` not `DeliveryState`; "数据集手机合成优先用它，卖家映射作为回退" |
| `docs/manual/evaluation.md` | `evaluation-修改类别映射和公开参照` | `worldmock_activity` keys `watch/...` and the fixed `consume` rule |
| `docs/manual/delivery.md` | `delivery-离线检查`, `delivery-修改规则数据` | unchanged in substance; example code imports `prepare_windows`/`create_catalog` (survive) |

---

## 7. Blast radius

Outside this subsystem, breaking on the format changes above:

- Life engine producers (other subsystem, but they write what this subsystem persists/exports): `life/travel.py` (location track, route payment), `life/basket.py` (orders, consumption, delivery planning by `delivery_destination_ids`), `life/time_advance.py` (`_apply_activity_effect` writes `revealed`, `work_material`, `consumption`), `life/observe.py` (observation keys `supplies/products/routes/locations/tasks/content_progress/game_catalog/browse_targets/world_query_channels`), `life/result_projection.py` (`browse_result`, `work_review` public projection, purchase details), `life/session_actions.py` (`_parallel_result_data`, `activity_snapshot`, watch/work start data), `life/work.py`, `life/work_review*.py`, `life/storefront_runtime.py`, `life/storefront_state.py` (`map_provenance`, `validate_storefront_state`), `life/content.py`, `life/game.py`, `life/inventory_recipe.py`, `life/schedule.py` (`WakeReason`, `ACTIVITY_KIND_FAMILIES` includes `watch/play_game/browse`), `life/readable.py`, `life/activity_titles.py`, `life/named_purchase.py`, `life/order_routing.py`, `life/purchase_offers.py`, `life/discovery.py`, `life/world_query.py`, `profile/coverage_apps.py`, `scheduling/{compile,effects,events}.py` (`ScheduledKind`), `custom/agents/life_agent.py` (`validate_verified_experiences` shape, tool schemas incl. `submit_work`), `custom/envs/life_env.py`, `tools/run-viewer.html` (reads `revealed`, `work_review`, orders).
- Tests outside the subsystem that assert these payloads: `tests/life/test_travel_trajectory.py`, `tests/life/test_domain_branch_coverage.py` (location track), `tests/life/test_storefront_*.py`, `tests/life/test_commerce.py`, `tests/life/test_purchase_channels.py`, `tests/life/test_named_purchase.py`, `tests/life/test_purchase_stages.py`, `tests/life/test_wc_named_purchase_replay.py` (fixture `dayrun_wc_named_purchase.json`), `tests/life/test_meal_outcome.py`, `tests/life/test_energy_feasibility.py`, `tests/life/test_content_game.py`, `tests/life/test_news_feed.py`, `tests/life/test_work_contracts.py`, `tests/life/test_work_review*.py`, `tests/life/test_work_schedule.py`, `tests/life/test_workload_report.py`, `tests/life/test_browse_and_hints.py`, `tests/life/test_wayfinding.py`, `tests/life/test_dayrun_restaurants.py`, `tests/life/test_coverage_closure_projection.py`, `tests/integration/{storefront_fixtures,test_storefront_tools,test_minimum_complete_path,test_input_warnings,test_unique_presence_run,test_host_feedback,test_life_agent_protocol,test_seven_layer_request,test_tool_description_references}.py`, `tests/catalog/*`, `tests/categories/*`, `tests/resources/*`, `tests/corpora/test_corpora.py`, `tests/profile/*` (coverage apps).
- Complete-day baselines: `tests/fixtures/dayrun-layering-baseline.json`, `tests/fixtures/dayrun-shared-rules-baseline.json`, `tests/dayrun_baseline.py`, `tests/integration/test_dayrun_complete_days.py` and the two historical "structure 6" proofs referenced by `docs/dev/PROPOSAL_2026-10-05_CROSS_PLATFORM_DAY_BASELINE.md` (the proposal itself says historical runs stay immutable; the baseline tests must be re-generated from the new environment, not kept).
- Mutation baseline `tests/fixtures/mutation-symbol-baseline.json` (anchors on `WorldState.schema_version`, `_require_current_world_schema`, purchase projections, pagination/supply window symbols) and `tests/fixtures/layering-public-api.json` (`LifeEngine.prepare_pending_work_review`, `read_task_summary`).
- Acceptance ledgers in section 4.5: every deleted test node or renamed symbol must be removed from the corresponding ledger row (`tests/fixtures/acceptance_requirements.json`, `*_acceptance.py` MUTATION rows and owned-file lists, `idle-fillers-acceptance.json`, `layering-acceptance.json`), otherwise `tests/test_*_acceptance.py` fail on missing nodes.
- Docs with line/anchor references that the documentation gate checks (`scripts/documentation_gate.py`, `documentation_refs.py`, `tests/fixtures/current-feature-owners.json`): all anchors in section 6 plus `docs/INDEX.md` rows, `docs/dev/implementation-status.md`, `docs/dev/coverage-report.json`, `docs/dev/test-durations.json`.
- Cross-subsystem dependencies the spec must coordinate: (a) resource engine must publish the app catalog, food/store bindings, content catalog and the graph so that `frozen_run_manifest` can bind one `environment_sha256` + `rules_version`; (b) exchange engine must emit the new `orders[]`/`consumption` payloads and the fixed kcal/duration constants that `quality`/`experiences` read; (c) simulation engine owns push ticks, content draws (`random_state`) and session hooks; this subsystem only persists `SessionState.pending_pushes` and projects the facts; (d) LabelKit projects (`labelkit-projects/*.toml`, `schemas/semantic-target.json`) are unaffected unless the public projection of `app_push` adds a new event shape; (e) WorldMock-Dataset must rewrite `WorldMock-Dataset/tools/simulate_phone.py`, `WorldMock-Dataset/tools/labelkit_input.py`, the four `WorldMock-Dataset/tools/preflight_*.py` channel cross-checks and `WorldMock-Dataset/tests/test_phone_purchase_projection.py` against the new facts files; (f) evaluation scenario files in the dataset (`scenarios/evaluation.json`) must change `category_mappings.worldmock_activity`.

---

## 8. Open questions the spec must decide

1. **Rename or keep `watch`?** `ActivityKind.WATCH` is referenced by `readable.py`, `schedule.ACTIVITY_KIND_FAMILIES`, evaluation maps, `quality._CONTENT_TYPES`, `render_day._LABELERS`, dataset evaluation scenarios. Renaming to `use_app` is the honest model (`target_id` = app); keeping `watch` saves churn but leaves `target_id` semantics flipped. Decision needed before the enum table in `contracts-领域枚举` is rewritten.
2. **Is `app_push` a wake reason?** The proposal says pushes "复用既有调度与唤醒规则，遵守当前睡眠和活动中断约束" and "作为通知进入人物可见上下文". Precedent `message_arrival` offers a decision; if `app_push` does not wake, pushes only become visible at the next boundary. Affects `WAKE_REASON_NAMES`, idle-fillers acceptance anchors, `quality.decision_rhythm`.
3. **Public projection of `app_push` for LabelKit.** Every `environment_event` today projects to `{}` in windows (`windows._public_event_data`). If the pushed summary is part of what the person saw, the window must carry `{kind, app_name, category, summary}`; otherwise the prompt (`decision_prompt`/layers) is the only place it appears. Decision table row "推送频率" and the sentence "推送保存人物、app、内容、摘要、发出时间和当前 session" imply the event carries the summary; whether windows show it is a spec choice.
4. **Session for push eligibility** — proposal recommends reusing location/transit sessions ("session 的含义"). If a dedicated app session is chosen instead, `pending_pushes` cannot live on `SessionState` and a new `AgentState.app_session` is needed (adds state and checkpoint rules).
5. **Push frequency** ("每人每小时至多一条、每 app 每 session 至多一条" recommended) determines whether `pushed_app_ids` per session suffices or a per-app-per-day set is needed.
6. **营业与供给**: "不设库存、店铺全天服务" (recommended) lets `opening_exceptions`, `OpeningInterval`, `_validate_opening_exceptions` and `MERCHANT_CLOSED` paths go; the alternative keeps type-level fixed hours as a WorldMock constant (no state) — either way `supply_stock/supply_quotes` go if "删除逐店库存、动态报价" is accepted.
7. **做饭是否保留**: deleting cooking removes `inventory_recipe`, `recipe_output_bindings`, `errand_metrics` recipe branch, `ConsumeRequest` 制作 way; keeping it as a fixed-duration lifestyle activity means `consume` never produces food and `errands` "取得 via 制作" evidence rows disappear anyway.
8. **外卖时间**: "固定备餐＋图上配送时间" (recommended) requires `DeliveryState.path/distance_meters/prep_seconds` evidence; "全部固定送达时间" needs only `expected_at`.
9. **工作工时**: "实际工作秒数" (recommended) lets `effective_work_seconds`, `activity_efficiency`, `body_settlement.work_efficiency` leave work completion; "有效工作秒数" keeps `effective_work_seconds` and the efficiency curve in `workload_metrics`.
10. **Do work topics keep deadlines, required locations and mail-based availability?** The proposal says "沿用场景的工作地点和班次" and deletes materials/review/payment; it is silent on `due_at`, `visible_task_ids`, `mail_arrival.related_task_id`, wake reasons `task_available`/`task_due_soon`, `ScheduledKind.work_available/task_expired`, `workload.task_outing_seconds`. The spec must list which of these survive.
11. **Seller accounts and the ledger.** Today each order pays `seller_account_id` from the supply; with stores sharing menus the payee is the store (or store type). `ledger.validate_account_transition` needs an account per payee; decide whether stores keep per-store accounts (`AgentState.balance_account_id` precedent) or a single "merchants" account per store type, and whether the delivery fee goes to a separate account.
12. **Content progress and re-watch** ("跨 app 共享进度、允许重看" recommended): decides whether `ContentProgress.completed_count` suffices or an exclusion set is needed; also whether `quality.content_contact.repeat_watch` is kept as "completed rounds > 1".
13. **Playback speed / `ActivityChange.change_speed`.** Not mentioned in the proposal; fixed consumption durations make speed a leftover of the subtitle era. Delete or keep as a no-op control.
14. **`taste_weights` and persona coverage apps.** The proposal removes taste-tag ranking; `taste_weights` is persisted in `AgentState` and used by `motivation`/relevance ordering, and `profile/coverage_apps` app aliases feed persona coverage warnings. Decide whether ordering by persona stays a WorldMock constant (then `taste_weights` can go) and how coverage uses the app catalog.
15. **Export artifact for app facts**: new `app-usage.jsonl` (recommended, manifest schema 3) vs. extending `purchases.jsonl`; and whether the dataset's phone synthesis still needs `activities.jsonl` travel segments (it does for "in_transit").
16. **Version numbers to publish together**: WS10, export manifest 3, quality 5, delivery manifest 3, run manifest 3 (new `map_dataset` shape, `rules_version`, `environment_sha256`), `Event.schema_version` unchanged (1), `Checkpoint.schema_version` unchanged (1). The spec's "旧条款删除清单" should state these in one table so `contracts-版本表`, `recording-engine-版本表`, `delivery-版本历史`, `evaluation-版本历史` and `EXPERIMENT` stage identities are rewritten consistently.
17. **Historical fixtures**: `tests/fixtures/dayrun_*`, `dayrun-*.json` and `tests/delivery/fixtures/labelkit-action.json` encode WS8/WS9-era runs. The proposal keeps historical runs as immutable evidence but forbids compatibility layers; the spec must say which fixture-driven tests are deleted (those that assert old payloads through current code) versus kept as byte-only evidence.
