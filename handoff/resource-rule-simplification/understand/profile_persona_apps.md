# Subsystem map: Persona cards, coverage apps, taste, injection

Worktree read: `D:/code/WorldMock/.claude/worktrees/rrs` (read-only). Proposal: `docs/dev/PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md`.
Dataset repo consulted read-only for the app/phone chain: `D:/code/WorldMock-Dataset` (`tools/preflight_persona.py`, `tools/simulate_phone.py`, `tools/labelkit_input.py`, `tools/audit_sources.py`, `requests/0001-white-collar-lianqiu/corpora/phone-simulation.json`).

Focus question: how app names (`app_display_names` → `PhoneSimulationCoverage` → `verified_app_names` / `purchase_channels`) and taste/preferences enter the persona, the prompt and the engine today, and what changes when apps come from the environment catalog and food/content choice is left to the model.

---

## 1. Inventory

### 1.1 Production files in scope

| File | Lines | Responsibility |
| --- | --- | --- |
| `src/worldmock/profile/__init__.py` | 65 | Package façade; re-exports compile/coverage/extractor/injection/mapper/models/taste/textgen symbols. |
| `src/worldmock/profile/compile.py` | 93 | `background_story` → `CompiledPersona` orchestration; rejects legacy template fields. |
| `src/worldmock/profile/coverage.py` | 103 | Persona coverage check: habits/tastes vs world target sets, 0.8 threshold, `persona_coverage.json` shape. |
| `src/worldmock/profile/coverage_apps.py` | 131 | Verifies the scenario-pinned phone config (`PhoneSimulationCoverage`), yields verified app aliases and derives `PurchaseChannel`s (seller→App→display names). |
| `src/worldmock/profile/coverage_targets.py` | 75 | Builds `CoverageTargets` from lifestyle corpus, location names, content corpora `genre[]`, meals corpus + edible catalog products, content-kind labels, verified app names. |
| `src/worldmock/profile/extractor.py` | 343 | DeepSeek three-run consensus extraction of `PersonaCard` + Big Five; `work_anchors` consensus; retries. |
| `src/worldmock/profile/injection.py` | 397 | Run-input warnings (closed set of 10 codes) and scenario rhythm override of card values. |
| `src/worldmock/profile/mapper.py` | 93 | Big Five → `WorldDynamics` via `persona-mapping.toml` (package resource). |
| `src/worldmock/profile/models.py` | 434 | Card contract v3: `PersonaCard`, `PersonaCardStruct`, `Tastes`, `HabitSignature`, `TimeAnchors`, `WorldDynamics`, `CompiledPersona`, constants. |
| `src/worldmock/profile/taste.py` | 31 | `compute_taste_weights`: card `content_genres` × item `genre[]` → `{item_id: weight}`. |
| `src/worldmock/profile/textgen.py` | 158 | Deterministic card text (`render_card`, ≤600 hanzi), plus `hour_text`/`window_text`/`intimacy_text` used by the persona layer. |
| `src/worldmock/data/persona-mapping.toml` | 108 | v2 mapping table (`big_five_center`, eight `world_dynamics.*` sections, `defaults` for anchors/chronotype/sleep). |
| `custom/agents/life_persona.py` | 137 | Host-side persona layer JSON from `profile["persona_card_struct"]`, name, scenario contacts and `persona_views`; read-only snapshot. |
| `tests/fixtures/environment/catalog/rules/persona-mapping.toml` | 108 | Byte-identical frozen copy of the package resource (verified with `diff`). |

Adjacent production code that wires this subsystem (outside `profile/` but part of the behaviour):

| File | Symbols | Role |
| --- | --- | --- |
| `src/worldmock/run_setup.py` | `creation_inputs`, `input_warnings`, `_coverage_targets`, `_agent_specs`, `_render_card`, `_compile_persona`, `_compiled_parts`, `_taste_entries`, `_card_taste_weights`, `_content_genre_catalog` | New-run wiring: compile → rhythm override → `profile["persona_card"]`, `profile["persona_card_struct"]`, `profile["taste_weights"]`; computes warnings; coverage targets with `verified_app_names` (IntegrityError → no app names, warning log). |
| `src/worldmock/runtime.py` | `_society_specs`, `habit_responses`, `_validate_persona_card_struct`, `_no_persona_compilation` | Freezes `agent_taste_weights`, `input_warnings`, `persona_habit_responses` into `create_options`; restore validates `persona_card_struct` and rhythm fixed point. |
| `src/worldmock/validation.py` | `validate_project`, `validate_persona_coverage`, `_compile_persona` | `validate` / `profile validate`: writes `persona_coverage.json`, prints warnings, gates on 0.8. |
| `src/worldmock/cli.py` | `_profile_command`, `_profile_check_report`, `_compile_once`, `_read_persona_text` | `profile compile/check/validate`. |
| `src/worldmock/life/load_checks.py` | `load_purchase_channels`, `check_channel_admission`, `_channel_name_problems`, `_channel_store_problems` | Derives channels from the verified phone config at scenario load; admission (unique names ≠ location names, member stores selected and stocked). |
| `src/worldmock/life/semantics.py` | `ScenarioSemantics.purchase_channels` | Holds `PurchaseChannel` tuple. |
| `src/worldmock/life/world_setup.py` | `load_scenario` (attaches channels), `_initial_agent` (`taste_weights`), manifest builder (`input_warnings`, `persona_habit_responses`) | Engine-side consumers. |
| `src/worldmock/life/world_query.py` | `attach_world_query` → `observation["world_query_channels"]` | Channel index frozen into observation (blacklisted key). |
| `src/worldmock/life/purchase_offers.py` | `_channel_rows` | Channel table for source resolution. |
| `src/worldmock/life/name_resolution.py` | `channel_key`, `resolve_source`, `near_source_names`, `ChannelRow`, `SourceMatch` | `from=<App name>` resolution; `SOURCE_NOT_FOUND` candidates include app names. |
| `src/worldmock/life/named_purchase.py`, `session_actions.py`, `storefront_runtime.py`, `footprints/purchases.py` | `channel_id` in order payload / `purchases.jsonl` | Channel identity recorded on orders. |
| `src/worldmock/life/models.py` | `AgentState.taste_weights: dict[str, float]` | Persisted in world state / checkpoints. |
| `src/worldmock/life/motivation.py` | `MotivationContext.taste_weights`, `_preference` | Ranking inside the scorer. |
| `src/worldmock/life/candidates.py` | `CandidateInputs.taste_weights`, `_taste_weight`, `_bucket` | Sort key (non-zero taste, −taste×attention, −relevance, hash). |
| `src/worldmock/life/activities.py` | `relief_preference(w, f)`, `entertainment_relief_rate`, `content_entertainment_relief_rate` | `f + (1−f)·w` effective preference. |
| `src/worldmock/life/observe.py` | `_motivation_context`, `_sample_contents`, observation key `persona_views` | Taste weights into scorer and content sampling; persona views from memory layer. |
| `src/worldmock/life/memory_layer.py` | `persona_views` | Derives `{relationships:[{person,view}], preferences:[{subject,preference,noted_on}]}` from `conclude_activity` views. |
| `src/worldmock/life/presentation.py` | `CandidateInputs(taste_weights=…)` | Projection seam. |
| `src/worldmock/life/relevance.py` | `MANIFEST_KEY="persona_habit_responses"`, `HABIT_POINTS=1`, `frozen_habit_responses`, `manifest_habit_texts` | Habit signature → +1 relevance. |
| `src/worldmock/life/narration.py` | prompt blacklist incl. `taste_weights`, `chronotype_midsleep`, `sleep_need_hours`, `persona_views` | Keys that must not reach prompts. |
| `src/worldmock/life/scenario.py` | `AgentConfig` (`background_story`, `health_habits`, `social_style`, rhythm fields, `contacts`), `LifeScenario.phone_simulation_config`/`_sha256` (also on `RuntimeScenario`) | Scenario input. |
| `src/worldmock/life/day_plan_inputs.py` | `_persona` | Zero-point plan reads `background_story` + scenario `leave_work_window`/`arrive_home_window` (not the card). |
| `src/worldmock/footprints/quality.py` | `_input_warning_quality` | `quality.json` `input_warnings` section keyed by `INPUT_WARNING_CODES`. |
| `custom/agents/life_agent.py` | `_prepare_life_action_state`, `prepare_react_loop`, `_persona_views`, `_persona_card_text` | Builds persona layer per decision; snapshot for read-only Q&A. |
| `custom/agents/life_context.py` | `persona_card_text` | `profile["persona_card"]` text (fallback: name + `background_story`/`health_habits`/`social_style`) for memory consolidation/reflection. |
| `custom/agents/life_memory.py` | consolidation payload `persona_card`, `render_memory_view(...)` | Card text in subjective consolidation prompt. |
| `custom/agents/life_prompt.py` | `life_user_layers`, `readonly_messages` | Persona layer is user layer 2. |
| `scripts/inspect_run.py` | `_workspace_cards` | `workday` command reads `persona_card_struct.time_anchors` from `AGENT.json`. |

### 1.2 Key symbols (current behaviour)

Persona compilation
- `src/worldmock/profile/compile.py::compile_persona`, `::LegacyTemplateFieldError`, `::_resolve_story`, `::_warnings`
- `src/worldmock/profile/extractor.py::extract_persona`, `::PersonaExtraction`, `::ExtractionError`, `::resolve_api_key`, `::_SYSTEM_PROMPT` (tastes = `content_genres` ≤6 题材词, `food_preferences` ≤6 饮食喜好词, `media` ≤4 常用媒介词), `::_single_run`, `::_parse_payload`, `::_consensus`, `::_build_card`, `::_work_anchors`, `::PAYLOAD_KEYS`
- `src/worldmock/profile/models.py::PersonaCard`, `::PersonaCardStruct`, `::persona_card_struct`, `::Tastes` (`MAX_CONTENT_GENRES=6`, `MAX_FOOD_PREFERENCES=6`, `MAX_MEDIA=4`), `::HabitSignature`, `::TimeAnchors`, `::TimeWindow`, `::settle_work_anchors`, `::BigFiveProfile`, `::WorldDynamics`, `::CompiledPersona`, `::SCHEMA_VERSION=3`, `::PROMPT_VERSION="card-v3"`, `::PERSONA_CARD_HANZI_BUDGET=600`, `::LEGACY_TEMPLATE_FIELDS`
- `src/worldmock/profile/mapper.py::load_mapping_config`, `::map_world_dynamics`, `::DYNAMICS_FIELDS`
- `src/worldmock/profile/textgen.py::render_card`, `::_taste_line` ("口味：爱看…；喜欢吃…；常用…"), `::hour_text`, `::window_text`, `::intimacy_text`, `::count_hanzi`

Apps / coverage
- `src/worldmock/profile/coverage_apps.py::PhoneSimulationCoverage` (`schema_version: Literal[1]`, `scenario_id`, `agent_id`, `timezone`, `activity_apps: dict[str,str]`, `purchase_apps_by_seller: dict[str,str]`, `app_display_names: dict[str, tuple[str,...]]`, `extra="ignore"`)
- `::PurchaseChannel(channel_id, names, store_location_ids)`
- `::verified_phone_config(scenario, corpus_root)` — identity (manifest sha256 + scenario pin), schema, scenario identity, executable evidence; any failure → `IntegrityError`
- `::verified_app_names(scenario, corpus_root)` — sorted aliases of apps with evidence
- `::purchase_channels(config, scenario)` — one channel per purchase app; member stores = selected locations where the mapped sellers have supplies
- `::_executable_apps` — `driving_travel` needs a reachable driving route; `watch` needs scenario contents; purchase sellers need `initial_stock > 0`; every app needs a display name
- `src/worldmock/profile/coverage.py::CoverageTargets` (`habit_response_keywords`, `content_genres`, `food_kinds`, `media_kinds`, `app_names`), `::validate_coverage`, `::require_coverage`, `::PersonaCoverageReport`, `::PersonaCoverageError`, `::COVERAGE_THRESHOLD=0.8`, `::_keyword_hit`
- `src/worldmock/profile/coverage_targets.py::build_coverage_targets`, `::_MEDIA_KIND_LABELS` (feature_film→电影, drama_series→剧集, podcast_episode→播客, webnovel_excerpt→网文, news_feed→新闻, joke_feed→段子, subtitle_experience→字幕体验, game_session→游戏), `::_scenario_content_kinds`, `::_corpus_items`

Taste
- `src/worldmock/profile/taste.py::compute_taste_weights(content_genres, items)` — `|genres ∩ tastes| / |genres|`, 4 dp, 0 when no genre or no tastes
- `src/worldmock/run_setup.py::_card_taste_weights`, `::_taste_entries` (contents + games through the same genre table), `::_content_genre_catalog` (reads `CONTENT_CORPORA` from `src/worldmock/catalog/admission.py`)

Injection
- `src/worldmock/profile/injection.py::InputWarningCode` / `INPUT_WARNING_CODES` = `TASTE_GENRES_UNMATCHED`, `CONTENT_WEIGHTS_ALL_ZERO`, `CONTENT_EMPTY_WITH_MEDIA_TASTES`, `GAMES_WITHOUT_TASTE_WEIGHTS`, `COVERAGE_BELOW_THRESHOLD`, `CARD_FIELD_OVERRIDDEN`, `CARD_FIELD_CONFLICT`, `PERSONA_AGREEMENT_LOW`, `DELIVERY_ROUTE_UNAVAILABLE`, `LOCATION_NAME_SUSPICIOUS`
- `::InputWarning{code, agent_id, message, details}`, `::frozen_input_warnings`, `::apply_scenario_rhythm` / `::RhythmOverride` / `::RHYTHM_FIELDS` (7) / `::_WINDOW_SLOTS` (5), `::taste_warnings`, `::_empty_content_warnings`, `::game_weight_warnings`, `::coverage_warnings`, `::agreement_warnings`, `::delivery_route_warnings`, `::location_name_warnings`, `::suspicious_name_patterns`

Persona layer (host)
- `custom/agents/life_persona.py::persona_layer(profile, name, views)` → `{layer:"人设层", identity{name,age,occupation,living_arrangement}, rhythm{wake,leave_home,lunch,leave_work,arrive_home,dinner,bedtime,sleep_midpoint,sleep_need}, habits[], tastes{content_genres,food_preferences,media}, coping, relationships[{person,role,closeness,channels,view}], preferences[]}`
- `::card_struct`, `::known_persons`, `::write_prompt_snapshot`, `::readonly_layers`, `::_card_fields`, `::_relationships`, `::PROMPT_SNAPSHOT_FILE="life_prompt_snapshot.json"`

### 1.3 Importers (rg across src/, custom/, scripts/, tests/)

`worldmock.profile.*` is imported by:
- src: `src/worldmock/cli.py` (lazy: extractor, textgen, models, compile), `src/worldmock/footprints/quality.py` (injection.INPUT_WARNING_CODES), `src/worldmock/life/load_checks.py` (coverage_apps.purchase_channels, verified_phone_config), `src/worldmock/life/world_setup.py` (injection.frozen_input_warnings), `src/worldmock/run_setup.py` (injection, coverage, coverage_apps, coverage_targets, textgen, compile, taste, models), `src/worldmock/runtime.py` (injection.apply_scenario_rhythm, models.PersonaCardStruct), `src/worldmock/validation.py` (compile, coverage_targets, coverage).
- custom: `custom/agents/life_persona.py` (models.PersonaCardStruct, TimeWindow; textgen.hour_text, intimacy_text, window_text).
- scripts: `scripts/probe_deepseek_strict.py` (extractor.ExtractionError, resolve_api_key).
- tests (direct imports): `tests/profile/*` (all), `tests/catalog/test_affordance_shadow.py`, `tests/catalog/test_engine_package_resources.py` (mapper.load_mapping_config, RULE_SNAPSHOTS includes `rules/persona-mapping.toml`), `tests/errands_workday_acceptance.py`, `tests/dayrun_fidelity_acceptance.py` (comment), `tests/test_dayrun_fidelity_acceptance.py` (INPUT_WARNING_CODES), `tests/footprints/test_quality.py`, `tests/footprints/test_validation.py`, `tests/integration/test_host_branch_coverage.py`, `tests/integration/test_input_warnings.py`, `tests/integration/test_persona_profile.py`, `tests/integration/test_presentation_habits.py`, `tests/integration/test_runtime.py`, `tests/integration/test_strict_endpoint.py`, `tests/life/test_persona_layer.py`, `tests/life/test_persona_upgrade.py`, `tests/life/test_purchase_channels.py` (PurchaseChannel), `tests/life/test_relief_floor.py`, `tests/life/test_workload_report.py`, `tests/test_import_graph.py` (DELAYED_IMPORTS rows for cli→profile), `tests/test_probe_deepseek_strict.py`, `tests/test_shared_primitives.py` (verified_phone_config).

`custom.agents.life_persona` is imported by: `custom/agents/life_agent.py`, `tests/errands_workday_acceptance.py` (SourceMutation targets), `tests/integration/test_decision_context_protocol.py`, `tests/integration/test_seven_layer_request.py`, `tests/integration/test_unique_presence_run.py`, `tests/life/test_persona_layer.py`, `tests/persona_prompt_acceptance.py` (production file list).

### 1.4 How app names and taste enter the persona and prompts today (traced)

1. Dataset pins `phone_simulation_config` (+sha256) in the scenario (`LifeScenario`), pointing at `corpora/phone-simulation.json` registered in the corpus manifest. Real file (`requests/0001-white-collar-lianqiu/corpora/phone-simulation.json`) carries far more than WorldMock reads: `activity_apps`, `purchase_apps_by_seller`, `app_display_names`, `content_apps_by_id`, `game_apps_by_id`, `payment_app_id`, `payment_sellers`, `delivery_notification_sellers`, `message_apps_by_recipient`, `scheduled_sessions`, `*_session_seconds`, `persona_source`, `rules_source`. WorldMock's `PhoneSimulationCoverage` reads only three mappings and ignores the rest.
2. `coverage_apps.verified_phone_config` checks identity/structure/scenario-identity/execution evidence. App *names* only ever reach WorldMock as `app_display_names[app_id]` aliases of apps that have evidence.
3. Names enter the **engine** through `load_checks.load_purchase_channels` → `semantics.purchase_channels` → `observation["world_query_channels"]` (blacklisted) and `name_resolution.resolve_source` (`from=<App alias>` → member stores; `channel_id` on orders). The *person* sees app names only indirectly: they are accepted in `from` and offered as `SOURCE_NOT_FOUND` candidates (`near_source_names`). No tool-layer list of apps exists.
4. Names enter the **persona pipeline** only through coverage: `run_setup._coverage_targets` / `validation.validate_persona_coverage` → `build_coverage_targets(..., app_names=verified_app_names(...))` → `CoverageTargets.app_names`, matched exactly against `card.tastes.media` entries. Failure of the phone config makes `validate` fail and makes new runs count no app names (warning log; `COVERAGE_BELOW_THRESHOLD` possible).
5. Taste enters prompts as **prose only**: `card.tastes` (`content_genres`, `food_preferences`, `media`) are rendered verbatim into the persona layer `tastes` object (`life_persona._card_fields`) and into `render_card`'s 口味 line (used by memory consolidation via `persona_card_text`). Numeric `taste_weights` never reach prompts (blacklisted in `narration.py`).
6. Taste enters the **engine** as `profile["taste_weights"]` = `compute_taste_weights(card.tastes.content_genres, contents+games by genre)` → `create_options["agent_taste_weights"]` → `AgentState.taste_weights` → `MotivationContext.taste_weights` (`_preference`), `CandidateInputs.taste_weights` (sort key), `_sample_contents` (attention_prior × weight), and `relief_preference(w, f)` in entertainment settlement. Restore carries weights from the checkpoint (never recomputed).
7. Habits enter the engine as `persona_habit_responses` in the run manifest (`runtime.habit_responses`) → `relevance.HABIT_POINTS` (+1 when the response text contains the entry name/category).
8. `background_story` itself enters the zero-point plan (`day_plan_inputs._persona["背景"]`) and the consolidation fallback (`life_context.persona_card_text`), separately from the card.

---

## 2. Current contracts

Anchors quoted exactly as `<a name="...">`; headings given where no anchor exists.

### docs/CONTRACTS.md
| Anchor / heading | What it fixes for this subsystem |
| --- | --- |
| `contracts-lifeagent-人格与记忆字段` / `contracts-人格与卡片字段` | `background_story`, `PersonaCard`, `persona_card_struct` (saved on create, validated on restore, host renders persona layer); field table incl. `tastes` row: content genres × content/game `genre[]` → `taste_weights`, effective preference `f + (1−f)·w`; scenario rhythm override paragraph; `CompiledPersona` v3 / `card-v3`; persona-layer merge rules; "大五分数、九参数与任何权重不进入模型提示". |
| `contracts-记忆字段` | `conclude_activity` views → persona-layer `view`/`preferences`; purchase writes no preference. |
| `contracts-人物卡片覆盖率产出` | `persona_coverage.json` v1 shape; threshold 0.8; matching rule (genres & media exact, habits & food substring, App names only exact on media; "B站视频" rule); target-set derivation incl. phone-config app aliases; phone-config failure → `validate` fails, new run counts no app names. |
| `contracts-运行输入警告` | Entry shape `{code, agent_id, message, details}`; closed set of 10 codes with conditions and `details` keys; "运行路径永不因警告失败"; `validate` gate; run-viewer behaviour. |
| `contracts-model-identity` (lines 2125–2127) | Run manifest `schema_version` 2 carries `input_warnings` and `persona_habit_responses`. |
| `contracts-体感与动机证据` | `chronotype_midsleep`/`sleep_need_hours` are state, not prompt; `rhythm` phrases only. |
| `contracts-排序键与游标` (line 1691 `relevance` row) | Habit-signature +1 relevance. |
| `contracts-cli` | `validate`, `profile compile/check/validate` rows (coverage, warnings, `render_card` not in request). |
| `contracts-语料内容目录字段` | Corpus content `attributes`/`genre` identity that taste matching relies on. |

### docs/dev/SPEC-life-realism.md
| Anchor | What it fixes |
| --- | --- |
| `spec-life-realism-画像编译` | Card contents; three-run consensus; `persona_card_struct`; rhythm override (7 fields); "大五只进入世界层动力学"; "删除与替换" (`entertainment_preferences` deleted, `taste_weights` from genre match, `f + (1−f)·w`); "口味权重是排序信号，不是准入条件" (zero weight never excludes; `attention_prior[kind] > 0` gates, preference only sorts); "覆盖率校验" (`persona_coverage.json`, 80%, no App substring, `COVERAGE_BELOW_THRESHOLD` on create); "覆盖率与可达性不是同一件事". |
| `persona-prompt-七层请求-人设层` | Persona-layer fields (`identity`, `rhythm` 9 keys, `habits`, `tastes`, `coping`, `relationships[]`, `preferences[]`); sources; merge; in-place update; counter-examples. |
| `dayrun-fidelity-人设与系统层-场景作息覆盖卡片抽取值` | D16: scenario rhythm overrides card; one rhythm for body/plan/persona layer. |
| `errands-workday-作息补全与工作安排-下班与到家锚点` | `work_anchors` consensus, demotion to null, `PERSONA_AGREEMENT_LOW` pass-through. |
| `errands-workday-作息补全与工作安排-场景作息覆盖` | 7 override fields, 5 window slots, closed set stays 10. |
| `errands-workday-作息补全与工作安排-环境层-duty` | Persona layer must not render `work_schedule`. |
| `spec-life-realism-动机层` | Scorer internal; candidates/ranks never in prompt. |
| `spec-life-realism-世界素材品类要求` (内容 row) | Content metadata must carry `title`, `genre[]`, `language`, `age_rating` — the `genre[]` taste matching depends on. |
| `spec-life-realism-素材可达性` | Coverage ≠ reachability (independent gates). |
| heading 验收矩阵 rows 画像编译 / 人物卡片覆盖率 / 口味权重是排序信号 (lines ~1311–1314) | Acceptance wording for the above. |

### docs/dev/SPEC-life-runtime.md
| Anchor | What it fixes |
| --- | --- |
| `dayrun-fidelity-身体与需要-运行输入警告` | Module `profile/injection.py`; computed in `runtime.py`; manifest/quality keys; warning text lists card genres/media and top corpus genres (D1: genre vocabulary belongs to the dataset, system does exact match); the 10-code table; `validate` still fails under 80% (D4). |
| `dayrun-fidelity-身体与需要-下限缓解与累积口径` | `relief_preference(w, f) = f + (1−f)·w`, `w` from `compute_taste_weights`, `f` default 0.5; games same algorithm (S1); sorting uses raw `w`. |
| `persona-prompt-交互语义-来源与去向` | `from` may be a channel name (App name resolved through the pinned phone config). |
| `spec-life-runtime-特性规格lifeenv-运行时行为与结算规则` heading 字符预算 | 600-hanzi card budget (same constant in `narration.py`). |

### docs/dev/SPEC-map-and-storefront.md
| Anchor | What it fixes |
| --- | --- |
| `dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店` | D8/S7: channel = `{channel_id, names[], store_location_ids[]}` derived from `PhoneSimulationCoverage` (`purchase_apps_by_seller`, `app_display_names`, `_executable_apps`); no new scenario field; admission; resolution rules 1–3. |

### docs/dev/SPEC-interaction-menu.md
| Anchor | What it fixes |
| --- | --- |
| `errands-workday-查询筛选与返回精简-来源解析` | Shared pure-function source resolution; `world_query_channels` private key. |
| `dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序` | Relevance layer incl. habit-signature +1 (S4). |

### docs/dev/SPEC-labelkit-delivery.md
| Anchor | What it fixes |
| --- | --- |
| `persona-prompt-下游` | `world_query_channels` listed as prompt-forbidden key; manifest `stages`. |

### docs/dev/SPEC-layering-and-test-tiers.md
- line 173 (heading 延迟与基础层 cache key): environment cache key covers "手机配置".

### docs/design/*.md
| File / heading | What it fixes |
| --- | --- |
| `08-configuration.md` lines 198–199 (场景结构) and 261–266 (购买渠道由手机配置派生) | Scenario `phone_simulation_config` pair; channel derivation and admission. |
| `08-configuration.md` lines 536–537 (启动检查 table: 人物卡片覆盖率 / 运行输入警告) | Gate semantics. |
| `08-configuration.md` lines 370–372, 472–473 | Rhythm override; persona compile defaults. |
| `04-simulation-engine.md` lines 169–172, 212–216, 731–732 | Run entry compiles persona; persona layer by host; relief `f + (1−f)·w`. |
| `03-resource-engine.md` line 506 | Sort key (non-zero taste, −taste×attention, −relevance, hash). |
| `02-exchange-engine.md` line 325 (内容使用 row) | Content relief by taste weight / exposure. |
| `05-recording-engine.md` line 986 (质量报告 `input_warnings`), 1136 (`workday` reads `persona_card_struct`) | Reporting. |
| `09-io-formats.md` line 260 | `AgentState.taste_weights` field. |

### docs/manual/*.md
| Anchor | What it fixes |
| --- | --- |
| `personas-人物画像`, `personas-写出一个能够生活的人` (lines 61–64) | Genre exact match; platform words are not genres; weight 0 → floor relief. |
| `personas-在场景中填写人物` | Agent fields table incl. rhythm fields, `health_habits`/`social_style`. |
| `personas-正文与实际世界条件要一致` (line 187) | New run uses card + computed taste weights. |
| `personas-预览与校验` (lines 231–242) | Coverage matching rules incl. phone-config App names; warnings printed. |
| `personas-常见错误` (lines 253–260) | Warning-code troubleshooting rows. |
| `personas-习惯的行为核验目标` | Habit behaviour gate. |
| `scenarios-看懂场景的组成` (lines 63–73) | `phone_simulation_config` row; purchase channel paragraph ("现成场景都没有钉定手机配置"). |
| `scenarios-校验自己的场景` (lines 373, 387–389) | Warning printing; channel admission errors. |
| `rules-人物映射表` | `persona-mapping.toml` v2 structure. |
| `runtime.md` lines 166–177 (heading 从校验到新建运行) | `validate` JSON keys `persona_coverage`, `input_warnings`; run freezes warnings. |
| `resources.md` lines 258–299, 672–684 | `from` = App alias; channel errors at load. |

### docs/VERIFICATION.md / docs/EXPERIMENT.md
- VERIFICATION heading 候选与呈现一致性 (lines ~445–470): row 软偏好不出局 (`taste_weights` zero still present); line 448 cites `spec-life-realism-画像编译`.
- VERIFICATION line 592 row 画像编译 (coverage rejection, Big Five not in prompt, persona layer merge rules, rhythm override); lines 307–309 mutation list for persona layer.
- VERIFICATION line 203/211 `tests/persona_prompt_acceptance.py::LEDGER`.
- EXPERIMENT line 38 (mapping file may be used for research, not a replacement), line 440 (`f = 0.5` entertainment relief floor hypothesis).

---

## 3. Current formats and fixtures

### 3.1 Scenario inputs (`src/worldmock/life/scenario.py`)
- `LifeScenario` / `RuntimeScenario`: `phone_simulation_config: str|None`, `phone_simulation_config_sha256: str|None` (both or neither).
- `AgentConfig`: `background_story` (≥100 chars when present), `health_habits`, `social_style`, `chronotype_midsleep` (default 4.0), `sleep_need_hours` (7–9, default 7.5), `lunch_window`, `dinner_window`, `leave_work_window`, `arrive_home_window`, `bedtime_window` (`[start,end]` hours), `contacts[]`, `lifestyle_activities`, `motivation` (incl. `attention_prior` keyed by registry attention kinds).

### 3.2 Phone simulation config (dataset file; WorldMock projection)
```json
{"schema_version": 1, "scenario_id": "...", "agent_id": 1, "timezone": "Asia/Shanghai",
 "activity_apps": {"driving_travel": "amap", "watch": "bilibili"},
 "purchase_apps_by_seller": {"merchant:lunch": "meituan-takeaway", "merchant:coffee": "luckin"},
 "app_display_names": {"meituan-takeaway": ["美团外卖", "美团外卖App", "美团"], "bilibili": ["B站", "哔哩哔哩"]},
 "...": "extra keys ignored by WorldMock: content_apps_by_id, game_apps_by_id, payment_app_id, payment_sellers, delivery_notification_sellers, message_apps_by_recipient, scheduled_sessions, *_session_seconds, persona_source, rules_source, scenario_path"}
```
Registered in `corpora/manifest.json` (`entries[].path/sha256`); pinned by the scenario sha256.

### 3.3 Compile / coverage outputs
- `CompiledPersona` JSON: `schema_version: 3`, `card{identity, relationships[], situation, time_anchors{wake,leave_home,lunch,dinner,bedtime,leave_work|null,arrive_home|null}, chronotype_midsleep, sleep_need_hours, habits[{trigger,response,time_window{start_hour,end_hour}}], tastes{content_genres[],food_preferences[],media[]}, coping}`, `big_five{5×1–7}`, `world_dynamics{8 floats}`, `source{background_story_sha256, model, prompt_version, consensus{runs, agreement}}`, `warnings[]`. `profile compile` adds `persona_card_text`.
- `persona_coverage.json`: `{schema_version:1, scenario_id, threshold:0.8, agents:[{agent_id, schema_version:1, total_items, covered_items, executable_ratio, passed, missing:[{kind: habit_response|content_genre|food_preference|media, value}]}]}`.
- `CoverageTargets` sources: `lifestyle/items.json` `items[].name_zh` + scenario location names → `habit_response_keywords`; `CONTENT_CORPORA` (movies/drama/podcasts/novels/feed/jokes/games library/social feeds) `genre[]` → `content_genres`; `meals/items.json` `name_zh` + catalog products with `object_kind=="product"` and `"eat" in use_modes` → `food_kinds`; `_MEDIA_KIND_LABELS[scenario content kinds (+ game_session)]` → `media_kinds`; `verified_app_names` → `app_names`.

### 3.4 Workspace profile (AS2 `AGENT.json` → `profile`)
`{...agent dump, "id", "same_second_continuation", "persona_card": <render_card text>, "persona_card_struct": <PersonaCard JSON minus situation>, "taste_weights": {item_id|game_id: float}}`; people without `background_story` carry none of the three persona keys.

### 3.5 Run creation / manifest / state
- `create_options`: `agent_taste_weights {agent_id: {...}}`, `input_warnings [...]`, `persona_habit_responses {agent_id: [response,...]}`.
- Run manifest (`schema_version: 2`): `input_warnings: list|null`, `persona_habit_responses: {"<agent_id>": [str]}|null`.
- `AgentState.taste_weights: dict[str, float]` (world state, checkpoints).
- `InputWarning` entry: `{code, agent_id|null, message, details{...per code}}` (details keys per code are in CONTRACTS table).
- Ops log row: component `runtime`, level `warn`, `code`, `message`, `details`, `agent_id`.
- `quality.json["input_warnings"]`: `{recorded, warning_count, code_counts{<10 codes>}, unrecognized_code_counts, agent_counts, scenario_warning_count, method}`.

### 3.6 Observation / prompt payloads
- Observation private keys: `persona_views {relationships:[{person, view}], preferences:[{subject, preference, noted_on}]}`, `world_query_channels [{channel_id, names[], store_location_ids[]}]`, `world_query_bindings`.
- Persona layer (user layer 2): see §1.2. `rhythm` values are readable phrases (`hour_text`/`window_text`), `closeness` is a band word, `tastes` are the raw card lists.
- `life_prompt_snapshot.json`: `{"persona": <layer>, "memory": <memory layer>}`.
- Consolidation prompt payload key `persona_card` = `persona_card_text(profile, name)`.
- Order payload / `purchases.jsonl`: `channel_id` (App id or null).

### 3.7 Mapping table
`src/worldmock/data/persona-mapping.toml`: `version = 2`; `[big_five_center] center=4.0 span=3.0`; `[world_dynamics.<8 names>] intercept/slope_<trait>/min/max/step`; `[defaults] sleep_need_hours=7.5, chronotype_midsleep="04:00"`; `[defaults.time_anchors] wake/leave_home/lunch/dinner/bedtime`.

### 3.8 Fixture files encoding these shapes
| Fixture | Encodes |
| --- | --- |
| `tests/fixtures/environment/catalog/rules/persona-mapping.toml` | Frozen copy of the mapping table (`tests/catalog/test_engine_package_resources.py::RULE_SNAPSHOTS`). |
| `tests/fixtures/environment/scenarios/single-person.json`, `multi-person.json`, `multi-person-half-day.json` | Agents without `background_story`; `phone_simulation_config` absent; content kind `subtitle_experience` only; no games. |
| `tests/fixtures/dayrun_fidelity_inputs.json` | Real `persona_card_struct` (v2-era, five anchors), `taste_weights` per content id, `scenario_rhythm`, `contents[{item_id, genre[]}]`, `location_names`, `undeliverable[]` for WC and BIZ (source: WorldMock-Dataset requests 0001/0002). |
| `tests/fixtures/tool_interaction_habits.json` | Compiled habit signatures from a real run (`compiled_personas[].habits`) with native action mappings. |
| `tests/fixtures/acceptance_requirements.json` | Ledger rows: `dayrun-fidelity` 验收台账 "输入警告" (10 codes one each; App substring not a hit), "场景作息", "购买渠道"; 裁决记录 D1 (genre vocabulary), S1 (game weights), S7 (channels from phone config), D4 (coverage <80%); `persona-prompt` 人设层 rows; `errands-workday` rows for work anchors and persona layer. |
| `tests/fixtures/current-feature-owners.json` | Owner anchors for `persona-prompt` and `dayrun-fidelity` (`…运行输入警告`, `…购买渠道app-名解析为成员门店`). |
| `tests/fixtures/layering-acceptance.json` | `src/worldmock/profile/coverage_targets.py::build_coverage_targets` listed under 延迟与基础层 implementation. |
| In-test fixtures (no files): `tests/profile/conftest.py::make_card/make_payload` (tastes `("悬疑","纪录片")`, `("川菜","面食")`, media `("长视频","短视频")`); `tests/profile/test_verified_app_names.py::_CONFIG` + `_corpus`; `tests/life/purchase_worlds.py::phone_config/build_world`; `tests/integration/test_input_warnings.py::_phone_variant/_app_card/_platform_card/_scenario`. | Phone config and card shapes used by tests. |

No `tests/fixtures/**` or `tests/*/fixtures/**` file contains a phone config; the dataset repo's copies live under `WorldMock-Dataset/requests/*/corpora/phone-simulation.json` and `execution/2026-09-29-scenario-repair/.../corpora/phone-simulation.json`.

---

## 4. Tests that lock the current behaviour

### 4.1 In-scope test files (node names)
- `tests/profile/test_coverage.py` (110): `test_full_coverage_passes`, `test_below_threshold_rejected_with_missing_items`, `test_threshold_boundary_passes_at_exactly_80_percent`, `test_habit_response_keyword_substring_matching`, `test_app_name_substrings_no_longer_count_as_coverage_hits` (ledger), `test_empty_card_is_vacuously_covered`, `test_report_serializes_to_coverage_json_shape`.
- `tests/profile/test_coverage_apps.py` (47): `test_channels_group_sellers_by_app_and_list_the_places_where_they_have_supplies` (ledger), `test_a_scenario_without_a_pinned_phone_config_has_nothing_to_derive` (ledger).
- `tests/profile/test_verified_app_names.py` (134): `test_returns_sorted_aliases_of_apps_backed_by_scenario_actions`, `test_rejects_config_identity_not_pinned_by_corpus_and_scenario`, `test_rejects_pinned_config_with_invalid_schema`, `test_rejects_config_bound_to_another_scenario_or_person`, `test_rejects_blank_or_duplicate_app_names`, `test_rejects_app_mappings_without_scenario_evidence`, `test_rejects_config_path_outside_the_corpus`.
- `tests/profile/test_taste.py` (49): `test_weights_from_genre_matching`, `test_partial_match_weight`, `test_deterministic_and_sorted_output`, `test_no_tastes_yield_zero_weights_not_defaults`, `test_items_without_genre_get_zero`, `test_weights_stay_in_unit_interval`.
- `tests/profile/test_injection.py` (537): `test_the_warning_code_set_is_the_closed_ten` (ledger), `test_wc_platform_word_taste_matches_no_genre_and_all_weights_are_zero` (ledger TASTE_GENRES_UNMATCHED), `test_matching_tastes_raise_no_taste_warning` (ledger CONTENT_WEIGHTS_ALL_ZERO), `test_biz_has_no_content_but_the_card_and_habits_listen_and_scroll`, `test_a_habit_whose_trigger_watches_or_scrolls_counts_as_a_media_habit`, `test_games_without_weight_entries_are_reported_from_the_explicit_table`, `test_coverage_below_threshold_reuses_the_validate_report` (ledger), `test_persona_compiler_agreement_warnings_are_passed_through` (ledger), 14 rhythm-override tests (`test_wc_scenario_rhythm_overrides_the_extracted_card_values` … `test_a_scenario_bedtime_before_the_card_leave_work_keeps_the_card_leave_work`), `test_biz_undeliverable_supplies_are_grouped_by_store_and_destination`, `test_a_real_scenario_route_that_turns_unknown_is_reported_and_reachable_ones_are_not`, `test_suspicious_display_names_are_reported_only_for_coordinates_and_identifiers`, `test_frozen_warnings_keep_the_closed_shape_and_null_means_not_computed`.
- `tests/profile/test_compile.py` (191), `test_textgen.py` (207), `test_models.py` (386), `test_mapper.py` (118), `test_extractor.py` (515), `test_branch_coverage.py` (224): card/extraction/mapping/rendering contracts (unaffected by app/taste changes except `test_render_card_without_habits_or_tastes_uses_fallback_lines`, `test_render_carries_card_content`, `test_tastes_*`).
- `tests/integration/test_persona_profile.py` (73): `test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer` (persona-prompt ledger), `test_persona_traits_cannot_be_silently_omitted`, `test_deleted_template_fields_stay_out_of_documents`.
- `tests/life/test_persona_layer.py` (450): 13 tests incl. `test_card_path_renders_card_fields_and_merges_relationships_by_exact_name`, `test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent`, `test_rhythm_schema_requires_each_of_the_nine_keys`, `test_a_declared_work_schedule_is_not_rendered_in_the_persona_layer`, `test_persona_card_struct_is_saved_on_new_runs_and_validated_on_restore` (ledger), `test_structured_card_without_work_anchor_keys_is_rejected_on_restore`, `test_later_view_replaces_earlier_while_events_keep_both`, `test_first_purchase_writes_no_preference_and_leaves_no_trace_after_delivery_and_midnight`, `test_readonly_questions_use_the_latest_decision_snapshot`.
- `tests/life/test_taste_seam.py` (150): `test_life_env_init_injects_taste_weights_into_agent_state`, `test_life_env_init_without_taste_weights_leaves_empty_mapping`, `test_engine_open_restores_checkpoint_weights_without_recompute`, `test_life_env_open_existing_uses_checkpoint_weights`, `test_card_without_tastes_carries_all_zero_weights`, `test_motivation_context_reads_non_empty_taste_weights`.
- `tests/life/test_persona_upgrade.py` (259): `test_persona_layer_from_the_scenario_profile_reaches_the_native_llm_prompt` (ledger), `test_memory_view_identity_uses_the_card_text_or_the_scenario_persona_fields` (ledger), `test_real_background_story_compiles_to_v2_card`, `test_card_render_never_leaks_big_five_or_random_floats`, etc.
- `tests/integration/test_input_warnings.py` (722): `test_run_creation_freezes_input_warnings_into_the_manifest_ops_log_and_quality_report`, `test_map_problems_are_frozen_as_scenario_warnings_and_creation_still_succeeds`, `test_validate_prints_the_same_warnings_the_run_froze`, `test_validate_still_fails_below_the_coverage_threshold_after_printing_the_warnings` (ledger), `test_a_phone_config_that_fails_verification_never_fails_run_creation` (ledger), `test_a_scenario_declaring_only_the_midpoint_changes_only_the_midpoint_in_the_request`, `test_body_settlement_day_plan_inputs_and_the_persona_layer_read_the_same_rhythm` (ledger), `test_a_conflicting_scenario_window_keeps_the_card_value_and_is_frozen_as_a_conflict`, `test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input`, `test_card_only_work_anchors_never_enter_the_plan_input`, `test_extracted_work_anchors_reach_the_real_request_persona_layer`, `test_a_demotion_note_is_frozen_as_a_compiler_warning_that_does_not_blame_agreement`, `test_every_compiler_warning_reaches_the_manifest_and_the_ops_log_in_compile_order`, `test_restore_verifies_the_frozen_card_rhythm_against_the_scenario` (ledger).

### 4.2 Out-of-scope tests that pin this subsystem's outputs
`tests/life/test_purchase_channels.py` (613; 22 tests, 12 in ledger "购买渠道"), `tests/life/purchase_worlds.py` (phone config builder), `tests/life/test_relief_floor.py::test_actual_relief_rises_strictly_with_taste_weight` (ledger), `tests/life/test_state_write_recheck.py::test_motivation_context_uses_persisted_taste_weights`, `tests/life/test_coverage_closure_projection.py` (taste_weight fixed-point in projection), `tests/life/test_candidates.py::TestOrdering::test_relevance_orders_after_taste_and_before_the_hash` (ledger), `tests/life/test_presentation_fidelity.py::test_zero_taste_order_is_not_alphabetical_and_changes_with_the_date` (ledger), `tests/life/test_motivation.py` (`TestScorerContract` soft-preference rows), `tests/life/test_relevance.py` (3 habit tests), `tests/integration/test_presentation_habits.py::test_run_creation_freezes_habit_responses_and_ranks_the_hit_entry_first`, `tests/integration/test_runtime.py::test_agent_specs_inject_compiled_persona_card_text` (ledger), `::test_agent_specs_computes_taste_weights_from_the_compiled_card`, `::test_agent_specs_card_without_tastes_yields_all_zero_weights`, `::test_agent_specs_restore_mode_never_recompiles_tastes`, `tests/integration/test_host_branch_coverage.py::test_build_society_create_mode_injects_taste_weights` (ledger), `::test_persona_compile_skips_and_renders`, `::test_validate_persona_coverage_accepts_absolute_corpus_and_skips_plain_agents`, `tests/footprints/test_validation.py::test_validate_project_passes_and_writes_persona_coverage`, `::test_validate_project_rejects_coverage_below_threshold`, `::test_build_coverage_targets_derives_from_catalog_and_corpora`, `::test_profile_cli_compile_outputs_card_and_rendered_text`, `::test_profile_cli_validate_writes_coverage`, `tests/footprints/test_quality.py::test_input_warnings_section_counts_the_frozen_manifest_warnings` (ledger), `tests/integration/test_run_manifest.py::test_engine_direct_runs_keep_input_warnings_null_and_unrecorded` (ledger), `tests/integration/test_seven_layer_request.py::test_no_card_persona_layer_keeps_the_name_and_scene_contacts` (ledger), `::test_system_layers_persona_memory_and_tools_validate_against_the_layer_schemas`, `tests/integration/test_decision_context_protocol.py::test_readonly_loop_uses_only_persona_memory_and_question`, `tests/life/test_workload_report.py::test_workday_command_reads_each_persons_own_workspace_card`, `tests/as2/test_worldmock_life_hooks.py::test_consolidation_prompt_carries_persona_card_without_example_identity`, `tests/test_shared_primitives.py` (imports `verified_phone_config`), `tests/life/test_prompt_layers.py::test_memory_layer_and_persona_views_are_empty_at_run_start`, `tests/catalog/test_engine_package_resources.py` (persona-mapping snapshot), `tests/test_import_graph.py` (cli→profile lazy imports).

### 4.3 Acceptance ledgers that reference these tests/symbols (deletion cost)
- `tests/dayrun_fidelity_acceptance.py` (`tests/test_dayrun_fidelity_acceptance.py` guard): rows "输入警告" (`WARNING_CODE_TESTS` one per code → `test_injection.py`; `test_the_warning_code_set_is_the_closed_ten`; `test_input_warnings.py`; `test_quality.py`; `test_run_manifest.py`; `test_coverage.py::test_app_name_substrings_no_longer_count_as_coverage_hits`), "场景作息" (6 injection + 6 integration + runtime/persona-layer/upgrade tests), "下限缓解" (`test_relief_floor.py`, `test_host_branch_coverage.py::…taste_weights`), "购买渠道" (12 `test_purchase_channels.py` nodes, `test_coverage_apps.py` ×2, purchases/replay tests); `FEATURE_PRODUCTION_FILES` lists `src/worldmock/profile/{__init__,coverage,coverage_apps,extractor,injection,taste}.py`, `runtime.py`, `validation.py`; `FEATURE_TEST_FILES` lists `tests/profile/test_coverage_apps.py`, `tests/profile/test_injection.py`, `tests/life/test_purchase_channels.py`, `tests/life/test_relief_floor.py`, `tests/life/test_relevance.py`, `tests/life/purchase_worlds.py`, `tests/integration/test_input_warnings.py`, `tests/integration/test_presentation_habits.py`.
- `tests/errands_workday_acceptance.py`: `SourceMutation` rows targeting `worldmock.profile.extractor::_work_anchors` (×4), `worldmock.profile.models::TimeAnchors.normalize_wraparound.names`, `worldmock.profile.injection::_window_overrides` (`override_skips_null_card_slot`), `custom.agents.life_persona::_card_fields` (×2), `custom.agents.life_persona::persona_layer.layer` (`host_renders_work_schedule`); `FEATURE_PRODUCTION_FILES` includes `custom/agents/life_persona.py`; ledger row 4.1 lists persona-layer tests. (`tests/fixtures/mutation-symbol-baseline.json` carries the corresponding mutation ids `errands:out_of_order_work_anchor_fails_the_card`, `errands:persona_layer_drops_work_anchor`, `errands:persona_layer_drops_arrive_home`, `errands:override_skips_null_card_slot`.)
- `tests/persona_prompt_acceptance.py` (`tests/test_persona_prompt_acceptance.py` guard): rows 人设层 → `tests/life/test_persona_layer.py::*`, `tests/integration/test_persona_profile.py::test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer`, `tests/life/test_persona_upgrade.py::test_persona_layer_from_the_scenario_profile_reaches_the_native_llm_prompt`, `tests/profile/test_textgen.py::test_render_card_is_byte_identical_to_the_pre_persona_layer_renderer`; production file `custom/agents/life_persona.py`; test file `tests/life/test_persona_layer.py`.
- `tests/fixtures/acceptance_requirements.json`: normative requirement anchors `dayrun-fidelity/normative_requirements[3]` (`身体与需要-运行输入警告`), `[14]` (`按名调用与指名购买-购买渠道app-名解析为成员门店`), `persona-prompt/normative_requirements[7]` (`七层请求-人设层`); 裁决记录 rows D1/S1/S7/D4 quoted in §2.
- `tests/fixtures/layering-acceptance.json`: `src/worldmock/profile/coverage_targets.py::build_coverage_targets` (延迟与基础层 implementation).
- `tests/fixtures/layering-public-api.json`: no profile entries (only `LifeEngine` methods).
- `tests/test_single_source.py::OWNERS`: no profile entries; but `life/activities.py: (entertainment_relief_rate, content_entertainment_relief_rate, relief_preference)` and `life/candidates.py: (CANDIDATE_ACTIVITY_KINDS, CANDIDATE_SOURCES)` are owned there (relevant if `relief_preference` is deleted).
- `docs/dev/ANALYSIS-spec-coverage.md` rows 作息锚点 / 输入警告 / 场景作息 / 购买渠道 / 人设层 name the same nodes.

---

## 5. What the proposal changes here

Proposal requirements touching this subsystem: (a) app catalog from the environment, restaurants bind to apps, contents bind to apps; (b) person picks an app, system picks content uniformly at random ("不需要推荐画像或去重队列"); (c) food is three categories with fixed calories, person chooses by "名称、价格、摘要和画像", "系统不再根据食材标签计算口味排名"; (d) delivery reaches every registered location; (e) old formats rejected, no compatibility layers; (f) "手机导出直接读取实际动作中的 app、店铺和内容身份… 删除卖家到 app、内容到 app、付款 app 等事后推断映射，并同步替换手机输入校验和来源登记中的旧配置依赖"; (g) "保留人物画像、初始资金、作息和场景时区等既有场景输入" and "当前身体状态、人物画像、模型决策和足迹" are reused.

### 5.1 DELETE
| Target | Reason |
| --- | --- |
| `src/worldmock/profile/coverage_apps.py` entirely: `PhoneSimulationCoverage`, `verified_phone_config`, `verified_app_names`, `purchase_channels`, `_executable_apps` | App names and store↔app bindings come from the environment app catalog; the phone config is a dataset-side projection file. `_executable_apps` also depends on `scenario.routes` (driving reachability) and `supply.initial_stock`, both leaving. |
| `LifeScenario.phone_simulation_config`, `phone_simulation_config_sha256` (and `RuntimeScenario` mirror, `semantics.py` lines 496–497, the validator pairing rule) | Old format must be rejected; scenario schema version bumps. Dataset `labelkit_input.py`/`simulate_phone.py` must stop requiring it (dataset side). |
| `src/worldmock/profile/taste.py::compute_taste_weights`; `run_setup._card_taste_weights`, `_taste_entries`, `_content_genre_catalog`; `profile["taste_weights"]`; `create_options["agent_taste_weights"]`; `AgentState.taste_weights`; `MotivationContext.taste_weights`/`_preference`; `CandidateInputs.taste_weights`/`_taste_weight`/`_bucket` taste lookup; `observe._sample_contents` weight factor; `activities.relief_preference` and the `w` argument of `entertainment_relief_rate`/`content_entertainment_relief_rate`; `narration.py` blacklist entry `taste_weights`; `design/09` field row | With content selected uniformly by the system per app, and food/entertainment effects fixed by WorldMock ("食品热量、交通速度、娱乐效果…唯一维护方 WorldMock"), there is nothing left for a per-item preference weight to rank or scale. The persona's tastes stay as prose in the prompt (the model does the choosing). See open question Q1 — this is the one deletion that reaches the exchange engine's relief formula. |
| `CoverageTargets.content_genres` and the `content_genre` check in `validate_coverage`; `coverage_targets._MEDIA_KIND_LABELS` (eight old kinds) and `_scenario_content_kinds` game branch; `_corpus_items` reading `CONTENT_CORPORA`/`meals/items.json` | New content catalog has five kinds with summaries and no `genre[]`; old content corpora, meals corpus and games chain leave. |
| Warning codes `TASTE_GENRES_UNMATCHED`, `CONTENT_WEIGHTS_ALL_ZERO`, `GAMES_WITHOUT_TASTE_WEIGHTS`, `DELIVERY_ROUTE_UNAVAILABLE`; functions `injection.taste_warnings`, `game_weight_warnings`, `delivery_route_warnings`, `TOP_CONTENT_GENRES` | No genre matching, no games, no delivery whitelist/routes. |
| `load_checks._channel_store_problems` clause "member store has no supply" (uses `scenario.supplies`) and `load_purchase_channels`'s warning-and-empty path | Channels now come from catalog bindings validated at registration; a bad binding is a registration error, not a warning that silently drops the channel ("无效 App、缺失店铺、冲突绑定均在登记时直接失败"). |
| Scenario `games` references in persona code (`_scenario_content_kinds`, `game_weight_warnings`) | TextWorld chain exits per proposal. |
| Docs/manual text listed in §6 | Rewritten in place. |

### 5.2 REWRITE
| Target | Change |
| --- | --- |
| `PurchaseChannel` (dataclass) | Keep the shape `{channel_id, names, store_location_ids}` but move it out of `profile/` into the resource engine (e.g. `src/worldmock/catalog/apps.py::AppCatalog` or `src/worldmock/life/app_catalog.py`), derived at registration: `channel_id = app.app_id`, `names = (app.name,)` (plus nothing else — one real name per app; aliases, if wanted, are a dataset decision, see Q6), `store_location_ids = sorted(location_id for selected food-store locations whose app set contains app_id)`. `semantics.purchase_channels`, `world_query_channels`, `purchase_offers._channel_rows`, `name_resolution.*`, `named_purchase`, order `channel_id` keep their contracts unchanged. |
| `load_checks.check_channel_admission` | Keep name rules (non-blank, unique across apps after `channel_key`, not equal to any location display name) and "member store is a selected location". Replace "has supply" with "store type can sell food" (`all`-expansion rule) — or drop it, since a bound app with no food store is already a registration error. Reject, never warn. |
| `CoverageTargets` → `{habit_response_keywords, food_names, content_kind_labels, app_names}` | `food_names` = names of the food catalog (零食/饮品/正餐 items; substring match as today for `food_preferences`); `content_kind_labels` = fixed five labels (`文章`, `图文`, `短视频`, `长视频`, `实况`) for `media` entries; `app_names` = app catalog names (exact match on `media`, as today — "App 名只作媒介条目的精确命中" stays). Report `missing[].kind` set becomes `habit_response | food_preference | media` (drop `content_genre`). |
| `build_coverage_targets(scenario, catalog, corpora_root, app_names=None)` | Signature loses `corpora_root`/`app_names`; reads lifestyle names + location names from the environment, food names and app names from the registered catalog. `run_setup._coverage_targets` loses its try/except (no phone verification); `validation.validate_persona_coverage` the same. |
| `injection.InputWarningCode` closed set → 6 codes: `CONTENT_EMPTY_WITH_MEDIA_TASTES`, `COVERAGE_BELOW_THRESHOLD`, `CARD_FIELD_OVERRIDDEN`, `CARD_FIELD_CONFLICT`, `PERSONA_AGREEMENT_LOW`, `LOCATION_NAME_SUSPICIOUS` | `CONTENT_EMPTY_WITH_MEDIA_TASTES` is re-based on "no app in the catalog has bound content" (details keep `content_genres`, `media`, `habits`; or rename `content_genres` → `card_content_genres`). `quality.py` `code_counts` follows the constant automatically; `tests/profile/test_injection.py::test_the_warning_code_set_is_the_closed_ten` and the ledger row "十个警告码各一例" are rewritten. |
| `run_setup.input_warnings` | Drop `contents`/`games`/`weights` preparation; per agent: `_empty_content_warnings` (if catalog has no app-bound content), `coverage_warnings`, rhythm warnings, agreement warnings; scenario-level: `location_name_warnings` only. |
| `run_setup._agent_specs` | Stops writing `profile["taste_weights"]`; still writes `persona_card` and `persona_card_struct`. `runtime._society_specs` stops setting `agent_taste_weights`. `world_setup._initial_agent` stops reading it. |
| `extractor._SYSTEM_PROMPT` `tastes.media` hint "最多 4 个常用媒介词" | Optional: "常用 app 或内容形式"; behaviour, not wording, is the requirement — leave unless the spec wants app names explicitly in `media`. |
| `textgen._taste_line` / `life_persona._card_fields["tastes"]` | Unchanged shape (`content_genres`, `food_preferences`, `media`). The proposal keeps 画像 as the model's basis for choosing food/app. |
| `tests/fixtures/dayrun_fidelity_inputs.json` | Drop `taste_weights`, `contents[].genre`, `undeliverable`; keep `persona_card_struct`, `scenario_rhythm`, `location_names` (and add `leave_work`/`arrive_home` nulls if the fixture is still loaded through `PersonaCardStruct`). |
| `docs/manual/personas.md` 预览与校验 matching rules; `scenarios.md` phone row/paragraph; `resources.md` `from` examples; `runtime.md` | Rewritten to the catalog-based rules. |

### 5.3 ADD
| Addition | Where / shape |
| --- | --- |
| App catalog projection consumed by this subsystem | Resource engine object (owned by the catalog subsystem), minimally `apps: {app_id: {name}}`, `store_apps: {location_id: frozenset[app_id]}`, `content_apps: {content_id: frozenset[app_id]}`; this subsystem only reads `apps[*].name` (coverage `app_names`, channel `names`) and `store_apps` (channel members). |
| `coverage_targets.CONTENT_KIND_LABELS` | Fixed mapping of the five WorldMock content kinds to the Chinese words the card's `media` can hit (`article→文章`, `image_text→图文`, `short_video→短视频`, `long_video→长视频`, `live→实况`; enum names to be fixed by the content subsystem). |
| Registration-time binding errors (English messages, `IntegrityError`) replacing today's warning-and-drop path for apps | In the catalog loader, not in `profile/`. |
| Nothing new in the persona card, persona layer, run manifest, checkpoints or event queue is required by the proposal for this subsystem. Push notifications and app sessions live in the simulation engine (sessions/event queue) and only *read* `persona_card_struct` if a future rule wants it; the proposal says pushes are uniform random per eligible app, "不填写…人物定向配置". |

### 5.4 Smallest correct design (summary)
1. Delete `coverage_apps.py` and the scenario phone pin; derive `PurchaseChannel`s from the app catalog at registration (same dataclass, same downstream contracts).
2. Delete `taste.py` and every `taste_weights` seam (profile → create_options → AgentState → scorer/candidates/relief); content choice is random per app, food choice is the model's. Keep `card.tastes` prose in the persona layer and `render_card`.
3. Re-target coverage to catalog names (food names, five content-kind labels, app names); drop the genre kind; keep 0.8 and the `validate` gate; keep "App name only exact on `media`".
4. Shrink the warning closed set to six; keep rhythm/agreement/coverage/location-name behaviour byte-for-byte.
5. Leave compile/extractor/mapper/models/textgen/persona layer untouched except the two label changes above; `persona_card_struct` v3, restore validation, `persona_habit_responses` relevance all stay.

---

## 6. Conflicts with frozen clauses

| Document | Anchor / heading | Gist of the clause the proposal contradicts |
| --- | --- | --- |
| `docs/CONTRACTS.md` | `contracts-人物卡片覆盖率产出` | Matching rule row: "内容题材…与目标品类集精确相等"; target set: "内容题材取内容语料条目的题材闭集，饮食喜好取正餐语料名与目录中可进食商品名，媒介取世界实际可观看或可游玩的品类词；场景钉定的手机配置中有执行证据的应用别名只作媒介条目的精确命中"; "手机配置的身份、结构或执行证据核验失败时，`validate` 与 `profile validate` 失败；新建运行不因此失败，按没有应用别名计算覆盖率…"; `missing[].kind` includes `content_genre`. |
| `docs/CONTRACTS.md` | `contracts-运行输入警告` | Closed set of ten incl. `TASTE_GENRES_UNMATCHED`, `CONTENT_WEIGHTS_ALL_ZERO`, `GAMES_WITHOUT_TASTE_WEIGHTS`, `DELIVERY_ROUTE_UNAVAILABLE` with their conditions/details; "（手机配置核验失败时覆盖率按没有应用别名计算…）"; "计算警告本身也不使创建失败"; `CONTENT_EMPTY_WITH_MEDIA_TASTES` condition keyed on scenario contents. |
| `docs/CONTRACTS.md` | `contracts-人格与卡片字段` (`tastes` row) | "内容题材与内容、游戏条目的 `genre[]` 确定性精确匹配得到 `taste_weights`…；原始权重只作排序信号，实际缓解另按有效偏好 `f + (1 − f)·w` 调幅度". |
| `docs/CONTRACTS.md` | `contracts-model-identity` (line 2125) | Manifest `input_warnings` "警告码闭集见「运行输入警告」" (count changes; key stays). |
| `docs/CONTRACTS.md` | `contracts-cli` (`validate`, `profile validate` rows) | Coverage/phone semantics in command rows. |
| `docs/CONTRACTS.md` | `contracts-语料内容目录字段` | Content `attributes` with `genre` identity used by taste. |
| `docs/dev/SPEC-life-realism.md` | `spec-life-realism-画像编译` | "删除与替换" paragraph (`taste_weights` from genre match, games same algorithm); "口味权重是排序信号，不是准入条件" paragraph and its three bullets (`compute_taste_weights` domain, `attention_prior[kind] × preference`, the five zero-genre feed items); "覆盖率校验" paragraph (genre/App-substring wording; `COVERAGE_BELOW_THRESHOLD` "与口味对不上题材、权重全零等其余输入警告一起"). |
| `docs/dev/SPEC-life-realism.md` | `spec-life-realism-世界素材品类要求` (内容 row) | Content metadata must have `genre[]`. |
| `docs/dev/SPEC-life-realism.md` | heading 验收矩阵 rows 人物卡片覆盖率, 口味权重是排序信号 (lines ~1312–1314) | Acceptance wording tied to genre weights and App substring. |
| `docs/dev/SPEC-life-realism.md` | `errands-workday-作息补全与工作安排-场景作息覆盖` and `…-下班与到家锚点` | "警告码闭集仍为 10" (count statement only; behaviour of these codes unchanged). |
| `docs/dev/SPEC-life-runtime.md` | `dayrun-fidelity-身体与需要-运行输入警告` | Ten-code table; "题材词表归数据集，系统只做精确匹配：裁决 D1"; `_card_taste_weights` reference; WC 0.92 example. |
| `docs/dev/SPEC-life-runtime.md` | `dayrun-fidelity-身体与需要-下限缓解与累积口径` | `relief_preference(w, f)` with `w` from `compute_taste_weights`; games same algorithm (S1); sorting by raw `w`. |
| `docs/dev/SPEC-life-runtime.md` | `persona-prompt-交互语义-来源与去向` | `from` channel name "按场景钉定的手机配置解析为成员门店". |
| `docs/dev/SPEC-map-and-storefront.md` | `dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店` | "渠道的来源（裁决 S7）: 从场景钉定的手机配置派生，不新增场景字段" incl. `PhoneSimulationCoverage`, `_executable_apps`, "手机配置缺省时没有渠道，App 名报 `SOURCE_NOT_FOUND`"; resolution rule 3 "按到收货地的预计送达最早（最慢备货加出发时刻的计划驾车时长）" (route-based). |
| `docs/dev/SPEC-interaction-menu.md` | `errands-workday-查询筛选与返回精简-来源解析` | "来源的合法写法以现行实现为准（…渠道称呼）" — still true, but the channel table's origin sentence must change. |
| `docs/dev/SPEC-layering-and-test-tiers.md` | heading 延迟与基础层 (line 173) | Environment cache key lists "手机配置". |
| `docs/design/08-configuration.md` | lines 198–199, 261–266, 536–537 | Scenario phone pin; channel derivation from phone config; coverage/warning gate rows mentioning App aliases and phone failure. |
| `docs/design/04-simulation-engine.md` | lines 731–732 | Relief by `f + (1−f)·w`. |
| `docs/design/03-resource-engine.md` | line 506 | Sort key "非零口味，−口味×注意力". |
| `docs/design/02-exchange-engine.md` | line 325 内容使用 row | Taste-weighted relief; games by genre. |
| `docs/design/09-io-formats.md` | line 260 | `AgentState.taste_weights` required field. |
| `docs/manual/personas.md` | `personas-写出一个能够生活的人` (61–64), `personas-正文与实际世界条件要一致` (187), `personas-预览与校验` (231–236), `personas-常见错误` (255–257) | Genre matching, taste weights, phone-config App names, the four taste/game warning rows. |
| `docs/manual/scenarios.md` | `scenarios-看懂场景的组成` (63–73), `scenarios-校验自己的场景` (387–389) | Phone pin row and channel paragraph; phone verification errors. |
| `docs/manual/resources.md` | lines 258–299, 684 | `from`=App alias via phone config; phone verification error row. |
| `docs/manual/runtime.md` | lines 166–177 | `persona_coverage`/`input_warnings` wording (keys survive; code list changes). |
| `docs/VERIFICATION.md` | heading 候选与呈现一致性 row 软偏好不出局; line 592 row 画像编译 | `taste_weights` zero-still-present gate; "覆盖率不足拒绝" stays but matching basis changes. |
| `docs/EXPERIMENT.md` | line 440 | `f = 0.5` relief floor hypothesis (becomes moot if `w` is deleted). |
| `tests/fixtures/acceptance_requirements.json` | `dayrun-fidelity` 裁决记录 D1 (genre vocabulary), S1 (game weights), S7 (channels from phone config), 验收台账 "输入警告" ("十个警告码各一例"), "购买渠道" ("手机配置缺省时 App 名报 `SOURCE_NOT_FOUND`") | Ledger text that the spec rewrite must update together with the tests. |

Not in conflict (reused as-is): `contracts-人格与卡片字段` card fields other than `tastes`; `persona-prompt-七层请求-人设层`; `dayrun-fidelity-人设与系统层-场景作息覆盖卡片抽取值`; `errands-workday-…-下班与到家锚点` behaviour; `contracts-记忆字段`; `dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序` habit +1; `rules-人物映射表`; `persona-mapping.toml`.

---

## 7. Blast radius

### 7.1 Tests outside this subsystem that break
- `tests/life/test_purchase_channels.py` (whole file: builds phone configs via `tests/life/purchase_worlds.py::phone_config/build_world`; 12 nodes in the "购买渠道" ledger). Needs a catalog-based world builder; the resolution/`SOURCE_NOT_FOUND`/`channel_id` assertions can be kept.
- `tests/life/test_taste_seam.py` (6), `tests/life/test_state_write_recheck.py::test_motivation_context_uses_persisted_taste_weights`, `tests/life/test_relief_floor.py` (`…taste_weight`, `…game_weights…`, `…zero_weight_sorts_last…` — ledger "下限缓解"), `tests/life/test_coverage_closure_projection.py` (taste_weight fields in projection rows), `tests/life/test_candidates.py::TestOrdering`, `tests/life/test_presentation_fidelity.py::test_zero_taste_order_is_not_alphabetical_and_changes_with_the_date`, `tests/life/test_motivation.py::TestScorerContract` soft-preference rows, `tests/integration/test_runtime.py` (3 taste tests), `tests/integration/test_host_branch_coverage.py::test_build_society_create_mode_injects_taste_weights`, `tests/life/test_storefront_discovery.py::test_same_product_supplies_keep_distinct_window_identity_and_share_taste`, plus any `AgentState(...)` constructions passing `taste_weights=` in `tests/life/test_activity_effects.py`, `test_news_feed.py`, `test_shift_edge_runs.py`, `test_domain_branch_coverage.py`, `test_grounded_observation.py`, `test_narration.py` (blacklist key), `tests/engine_fixture.py`.
- `tests/integration/test_input_warnings.py` (`_phone_variant`, `_app_card`, `_platform_card`, `test_a_phone_config_that_fails_verification_never_fails_run_creation`, `test_map_problems_are_frozen_as_scenario_warnings_and_creation_still_succeeds`, `test_validate_prints_the_same_warnings_the_run_froze`), `tests/footprints/test_validation.py::test_build_coverage_targets_derives_from_catalog_and_corpora`, `tests/footprints/test_quality.py::test_input_warnings_section_counts_the_frozen_manifest_warnings` (uses `game_weight_warnings`), `tests/test_shared_primitives.py` (imports `verified_phone_config`), `tests/test_dayrun_fidelity_acceptance.py` (closed-set count), `tests/test_import_graph.py` (if `cli` lazy-import targets change).
- Ledgers: `tests/dayrun_fidelity_acceptance.py` rows 输入警告 / 下限缓解 / 购买渠道 / 场景作息 (node names), `FEATURE_PRODUCTION_FILES` (`profile/coverage_apps.py`, `profile/taste.py` deleted), `FEATURE_TEST_FILES` (`tests/profile/test_coverage_apps.py`, `tests/life/test_relief_floor.py`); `tests/fixtures/acceptance_requirements.json` rows; `docs/dev/ANALYSIS-spec-coverage.md` rows.
- Fixtures: `tests/fixtures/dayrun_fidelity_inputs.json` (`taste_weights`, `contents[].genre`, `undeliverable`), `tests/fixtures/tool_interaction_habits.json` (unaffected), `tests/fixtures/environment/scenarios/*.json` (schema version bump if `phone_simulation_config` fields are removed — they are absent there, so only the version constant changes).

### 7.2 Cross-subsystem dependencies the spec must coordinate
| Dependency | Direction | Note |
| --- | --- | --- |
| App catalog (resource engine) | provides `apps`, `store_apps`, `content_apps` | This subsystem reads names and store bindings; registration must reject duplicate/blank app names and names equal to location names (today's `check_channel_admission` rules) so that `name_resolution` stays unambiguous. |
| Content catalog (five kinds, summaries) | removes `genre[]`, `CONTENT_CORPORA`, `scenario.contents.kind` old enum, games | Coverage `media` labels and the empty-content warning depend on the new kind enum; `_MEDIA_KIND_LABELS` must be replaced by the content subsystem's enum. |
| Food catalog (零食/饮品/正餐) | removes `meals/items.json`, `use_modes`, nutrition | Coverage `food_preferences` substring target = food names. |
| Map (random weighted graph) | removes `scenario.routes`, `delivery_destination_ids` | `DELIVERY_ROUTE_UNAVAILABLE` and `_executable_apps` driving check disappear; `near_source_names` feasibility callback and store choice by "最快拿到" move to graph distance (storefront subsystem). |
| Supplies / stock | removes `initial_stock` (proposal suggests no inventory) | Channel admission "member store has supply" and `_executable_apps` purchase evidence disappear. |
| Exchange engine (entertainment relief) | `relief_preference(w, f)`, `entertainment_relief_preference_floor` | If `taste_weights` is deleted, relief becomes the fixed per-kind constant; `BodyInstanceConfig.entertainment_relief_preference_floor` (scenario field, EXPERIMENT hypothesis) becomes dead and must be removed with the format change. |
| Simulation engine (sessions, pushes, app usage activity) | new activity kind "use app" + push notifications | Reads nothing from the card; persona layer unchanged; `persona_habit_responses` relevance may be given the new activity's name/category to match habits like "刷B站". |
| Footprints / phone handoff (dataset `simulate_phone.py`, `labelkit_input.py`, `preflight_persona.py`, `audit_sources.py`) | consume `phone-simulation.json` and `app_display_names` | Must switch to run events carrying `app_id` (dataset work; `labelkit_input.verified_phone_source` demands `config_sha256`). `preflight_persona.issue_coverage_errors` checks app display names against ISSUE text — re-point at the app catalog. |
| Run viewer (`tools/run-viewer.html`), `quality.json`, ops log | `input_warnings` codes | Only the closed set changes. |
| `scripts/inspect_run.py workday` | reads `persona_card_struct.time_anchors` | Unchanged. |
| Narration blacklist (`src/worldmock/life/narration.py`) | key list | Remove `taste_weights`; keep `persona_views`, `world_query_channels`. |

---

## 8. Open questions (decisions the spec must make)

1. **Do `taste_weights` survive in any form?** The proposal says content is "从该 app 绑定内容均匀随机抽取" and "不需要推荐画像或去重队列", and food is chosen by the model "依据名称、价格、摘要和画像… 系统不再根据食材标签计算口味排名". That leaves no consumer for a per-item weight, but deleting it also deletes `relief_preference(w, f)` (frozen in `dayrun-fidelity-身体与需要-下限缓解与累积口径`, owned in `tests/test_single_source.py::OWNERS`) and the scenario field `entertainment_relief_preference_floor` (EXPERIMENT hypothesis `f = 0.5`). The proposal's ownership table puts "娱乐效果" under WorldMock-fixed constants; the spec must say explicitly that entertainment relief is a fixed per-kind rate with no persona factor, or keep `f`-only relief.
2. **Coverage target for `media` and the gone `content_genre` kind.** Options: (a) drop the `content_genre` check and match `media` against five kind labels + app names (recommended, smallest); (b) also match `content_genres` against content *summaries/titles* by substring (fragile, re-introduces vocabulary coupling the proposal removes). Decision table item "内容进度与重看" does not cover this; it is a new ruling.
3. **Keep the 0.8 `validate` gate (D4) and `COVERAGE_BELOW_THRESHOLD` on create?** Nothing in the proposal removes it; recommend keep, with the re-targeted sets. Note `docs/manual/personas.md#personas-习惯的行为核验目标` says keyword coverage never proves executability.
4. **Where do app names for coverage come from when the person has no evidence of using an app?** Today `verified_app_names` demands scenario execution evidence. With a catalog, every registered app name counts (an app bound to no store and no content is itself a registration error per proposal: "从店铺绑定和内容绑定得到可用入口… 仅有名字不会自动获得…能力"). Confirm: coverage counts all catalog apps, or only apps with ≥1 binding.
5. **App aliases.** Today `app_display_names` allows several aliases per app ("美团外卖", "美团外卖App", "美团"); the proposal's app object is "标识、真实名称" (one). `name_resolution.channel_key` and `SOURCE_NOT_FOUND` candidates work with a single name; decide whether the catalog may carry an `aliases` list (dataset "names… 稳定标识、真实或合成名称") or exactly one name.
6. **Channel admission "member store has supply".** With "店铺目录商品持续供应，删除逐店库存" (decision row 营业与供给) the check is meaningless; with the alternative option (fixed opening hours, no stock) it stays meaningless too. Recommend deleting the clause; the "`all` only expands to food-capable store types" rule guarantees members sell something.
7. **Does the persona card gain any app-specific field?** The proposal keeps 画像 as-is ("保留人物画像…既有场景输入"). Recommend no change to `Tastes`; `media` remains free text that may name apps. If the spec wants the extractor to prefer app names in `media`, that is a prompt wording change (`PROMPT_VERSION` bump not required by behaviour).
8. **Scenario schema version and `phone_simulation_config` removal.** The proposal requires old formats rejected; removing the two fields bumps `LifeScenario.schema_version` (currently 6) and `tests/fixtures/environment/scenarios/*.json`. The dataset handoff (`labelkit_input.py`, `simulate_phone.py`) must be re-sourced from run events in the same step (dataset-evidence "应用唯一目录覆盖导出链").
9. **`CONTENT_EMPTY_WITH_MEDIA_TASTES` re-basing.** Today: scenario has no contents. New: no catalog content bound to any app (or the person's eligible apps). Decide whether it is per-environment (scenario-level, `agent_id: null`) or per-person (card has 看/刷/听 but no app has content).
10. **Habit relevance for the new "use app" activity.** `relevance.HABIT_POINTS` matches response text against entry names/categories; the spec should state the new activity's visible name is the app name so "通勤时→听播客"/"刷B站" habits still score (+1) — otherwise the `persona_habit_responses` manifest key stays but never hits entertainment.
11. **Games.** Proposal lists "TextWorld 独立游戏链" under "应整体退出当前运行链". Confirm `scenario.games`, `game_session` media label and `GAMES_WITHOUT_TASTE_WEIGHTS` all go in the same step (ledger row S1 becomes void).
12. **Decision-table item "推送频率" / "session 的含义"** do not touch this subsystem; confirm pushes are not persona-targeted ("不填写推送脚本、时间表、推荐权重或人物定向配置") so that no new card field or warning is needed.
