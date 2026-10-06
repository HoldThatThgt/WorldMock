# Subsystem map: category contract, exchange rules, catalog compile chain, resource ledger

Worktree: `D:/code/WorldMock/.claude/worktrees/rrs` at `6df927d495a475a9ae0dbc8037dbcdb276740ab4` (read-only survey, 2026-10-06).
Proposal: `docs/dev/PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md`; evidence: `docs/dev/research/resource-rule-simplification/{engine,dataset,industry}-evidence.md`.
All paths below are repo-relative unless absolute. "DS" = WorldMock-Dataset (not inspected; its working tree is out of bounds).

---

## 1. Inventory

### 1.1 Production files in scope

| File | Lines | Responsibility (one line) |
| --- | --- | --- |
| `src/worldmock/categories/__init__.py` | 114 | Public re-export surface of the category/rules package (57 names). |
| `src/worldmock/categories/common.py` | 179 | `CategoryError` (logs + stable code), `DeepFrozenModel` (extra=forbid, recursive freeze), enums `Action`, `PairingState`, `CategoryKind`, `Conservation`, `ProvenanceType`, `ProducerType`, `ReviewerType`; `SourceRecord`, `ReviewRecord`; `require_finite_number`. |
| `src/worldmock/categories/models.py` | 203 | Raw contract document models (`AttributeValues` abs/relative/proportional, `AttributeSlot` JSON-Schema slot, `CategoryNode`, `PairingDeclaration` 3-state, `CategoryContractDocument`) and resolved views (`ResolvedSlot/Category/Object`, `CategoryContract.require_category/is_descendant`, `PairingResolution`). |
| `src/worldmock/categories/contract.py` | 361 | Tree resolution: single root `entity`, `person` node required, cycle/orphan/cross-kind checks, slot schema accumulation, defaults, numeric delta application, redundancy rejection, `purchase_price_fen_min/max` ordering; `resolve_object_values`, `validate_category_attributes`. |
| `src/worldmock/categories/rules.py` | 298 | `exchange-rules.json` models: 8 operators (`purchase_transfer`, `food_energy_source`, `durable_use`, `travel_fare`, `work_hourly`, `work_piece`, `work_package`, `inventory_transform`), `ExchangeRulesDocument`, `ExchangeRuleSet`, `build_exchange_rules` (version/pairing cross-check), `validate_rule_resource_domains` (money_fen/energy_kcal/item domain table). |
| `src/worldmock/categories/operators.py` | 166 | Closed-set Decimal evaluators: `evaluate_purchase`, `evaluate_food_energy`, `evaluate_travel_fare`, `evaluate_hourly_pay`, `evaluate_piece_pay`, `evaluate_package_pay`, `scale_inventory_recipe`, `InventoryDelta`. |
| `src/worldmock/categories/pairing.py` | 98 | `resolve_pairing`: most-specific directed (actor, target, action) hit along both ancestor chains; ambiguity => `PAIRING_AMBIGUOUS`. |
| `src/worldmock/categories/provenance.py` | 112 | `validate_numeric_sources`: every numeric leaf of a JSON doc must be covered by exactly one `SourceRecord.paths` JSON Pointer; `NUMERIC_SOURCE_METADATA_ROOTS`. |
| `src/worldmock/categories/identity.py` | 150 | `CatalogIdentity` (4 fields), `compute_catalog_identity` (framed SHA-256 over resources/objects/tombstones/contract/rules/affordances + `rules/*.toml` + manifest minus self), `refresh_manifest_identity` (atomic write). |
| `src/worldmock/categories/loader.py` | 82 | `load_category_contract`, `load_exchange_rules`; JSON object loader rejecting duplicate keys. |
| `src/worldmock/categories/maintenance.py` | 286 | `worldmock category check/refine/publish`: pairing query report, refinement suggestions from object attributes, run `category_touches` from `action_result.data.category_matches`, atomic exclusive-rename publication of a new catalog dir with scenario admission. |
| `src/worldmock/categories/cli.py` | 48 | argparse wiring for the `category` subcommand (lazy import of maintenance). |
| `src/worldmock/catalog/__init__.py` | 102 | Public surface of the catalog package. |
| `src/worldmock/catalog/models.py` | 431 | `ResourceDefinition` (+`ResourceBounds`, `AlertRule`, `DrainRules`, `Provenance`), compiled edges (`TransferEdge`, `SourceEdge`, `DrainEdge`, `RuleEdge`), `ObjectDeclaration` (raw delta row), `ExchangeObject` (compiled view with `edges`, `serving`, `acceptance`, `efficiency`, `use_modes`, `required_location_id`), `Tombstone`, `CategoryRequirements` (+ `MealRequirements`, `ContentMetadataRequirements`, `WorkMaterialRequirements`, `ContactRequirements`, `PantryRequirements`), `CatalogManifest`, `Catalog` dataclass with `pairing()`/`rule_for()`. |
| `src/worldmock/catalog/loader.py` | 234 | `load_catalog`: manifest -> identity recompute/compare -> contract -> rules -> resources.jsonl -> rule/resource domain check -> objects.jsonl latest versions -> compile -> tombstones -> optional `rules/travel-time.toml` bytes. |
| `src/worldmock/catalog/compiler.py` | 255 | `compile_exchange_object`: resolves object values, iterates all `Action`s, resolves `person -> category` pairing, projects rules into `RuleEdge`/`SourceEdge` (food energy precomputed), derives `effect`, `use_duration_seconds`, `serving`, `acceptance`, `efficiency`. |
| `src/worldmock/catalog/builder.py` | 151 | `add_resource/add_object/deprecate`: staged copy, append JSONL line, bump minor, refresh identity, reload, commit pair of files with restore on failure. |
| `src/worldmock/catalog/inspect.py` | 128 | `edges_of`, `objects_affecting`, `lineage` read-only queries over compiled edges and provenance. |
| `src/worldmock/catalog/admission.py` | 968 | Two halves: (a) `validate_scenario_against_catalog` over `ScenarioCatalogRefs` (version pin, reference integrity, units, conservation domains, initial bounds, tombstones, schedule, rule refs); (b) `validate_world_categories` over corpora (meals/OFF energy source, location kinds floor, home pantry, office affordances, content metadata + age rating + drama zh, AMI work materials, lifestyle v2 fields, contacts, retrieval partition keys and feasibility) and `validate_task_assignees`. |
| `src/worldmock/catalog/affordances.py` | 638 | Engine-owned `affordances.toml` registry: `KindSpec`, `FamilySpec`, `ContentKindSpec`, `AffordanceView` (MET keys, relief channels, resource claims incl. `content_kind:` conditionals, habit routes, target discovery, feedback/game boundaries); `load_affordances`, `engine_affordances()` (lru_cache over package resource). |
| `src/worldmock/resources/__init__.py` | 38 | Public surface of ledger package. |
| `src/worldmock/resources/models.py` | 203 | `ResourceMeta`, `ResourceLeg`, `TransferEntry` (closed), `FlowEntry` (open), `ConvertEntry` (atomic cross-resource), `ResourceLedgerError`, `AMOUNT_TYPES`; cause and integer `occurred_at` mandatory. |
| `src/worldmock/resources/ledger.py` | 169 | `ResourceLedger.apply/validate_history`: pure-functional balances, closed-domain zero-sum, bounds/non-negative guards with 1e-9 clamp, anchor owners may go negative. |
| `src/worldmock/resources/audit.py` | 143 | `audit_closed_domain`, `audit_level_flows`: full-history replay and reconciliation reports. |
| `src/worldmock/data/__init__.py` | 1 | Package marker for `importlib.resources`. |
| `src/worldmock/data/affordances.toml` | 277 | Engine closed set of activity kinds (wait, sleep, work, watch, play_game, travel, read_mail, browse, consume, lifestyle) and motivation families (eat, sleep, continue, work, travel, browse, entertainment, lifestyle); entertainment `content_kinds` = feature_film, drama_series, podcast_episode, webnovel_excerpt, subtitle_experience, news_feed, joke_feed, game_session. |
| `src/worldmock/data/motivation.toml` | 43 | Cross-persona scoring transforms (prospect, homeostatic, normalization, cost, habit) with provenance. |
| `src/worldmock/data/persona-mapping.toml` | 108 | Big-Five -> world dynamics mapping and defaults (persona compiler). |
| `src/worldmock/corpora/__init__.py` | 5 | Package marker. |
| `src/worldmock/corpora/manifest.py` | 114 | `CorpusManifest/CorpusEntry/CorpusSource`, `load_manifest` (sha+size per file), `safe_file` (single-source owner of root-bounded regular-file check). |
| `src/worldmock/corpora/storefront.py` | 137 | Per-store assortment expansion (`assortment_objects` via `is_descendant`), `frozen_price` (seed/location/object SHA jitter), `price_range`, `validate_assortments` (kind admission table, richness thresholds, restaurant menu pairwise >=3 distinct dishes, quote formula, convenience>supermarket+10 positions). |
| `src/worldmock/life/category_contract.py` | 242 | Runtime pairing recheck (`require_runtime_rule`, `optional_defined_rule`, `require_item_rules`, `match_rows` -> `category_matches` rows), `admit_reachable_categories` (closure over products, releases, holdings, contracts, routes, recipe outputs), recipe binding validation; constants `TRAVEL_OBJECT_ID="travel-service"`, `CONTRACT_OBJECT_IDS` (contract-hourly/piece/package). |
| `scripts/check_reachability.py` | 165 | Static retrieval-partition reachability over `CONTENT_CORPORA` (13 corpus files) and scenario `contents[]`, same pool as `admission._retrieval_entries`. |

Related but outside the hint list (touched by this subsystem's symbols): `src/worldmock/cli.py` (registers `category` subcommand), `src/worldmock/life/run_identity.py` (copies the 4 identity fields), `src/worldmock/life/semantics.py` (assembly: `validate_assortments`, pin check, `_assemble_body` from `resources.jsonl`, `_assemble_items` from compiled objects, `admit_reachable_categories`, `ScenarioCatalogRefs`, operator calls), `src/worldmock/life/inventory_recipe.py` (recipe via `ConvertEntry`), `src/worldmock/life/body.py`/`settlement.py` (ledger users), `src/worldmock/validation.py` (`validate_world_categories`, `validate_task_assignees`).

### 1.2 Key symbols implementing current behaviour

- `src/worldmock/categories/common.py::Action` (purchase, consume:eat, consume:use, work:settle, travel:walking, travel:driving) — the closed action set shared by pairing, compiler, runtime.
- `src/worldmock/categories/models.py::CategoryContract.is_descendant` — used by storefront assortment, recipe validation, callable index, world_query; mutation `tool:category_inheritance_exact_only` targets it.
- `src/worldmock/categories/contract.py::build_category_contract`, `::resolve_object_values`, `::_validate_values` (mutation target for price-range inheritance).
- `src/worldmock/categories/pairing.py::resolve_pairing`.
- `src/worldmock/categories/rules.py::ExchangeRuleSet.require`, `::build_exchange_rules`, `::validate_rule_resource_domains`, `::_operator_resource_domain`.
- `src/worldmock/categories/operators.py::evaluate_purchase`, `::evaluate_travel_fare`, `::evaluate_food_energy`, `::evaluate_hourly_pay`, `::evaluate_piece_pay`, `::evaluate_package_pay`, `::scale_inventory_recipe`.
- `src/worldmock/categories/provenance.py::validate_numeric_sources`.
- `src/worldmock/categories/identity.py::compute_catalog_identity`, `::refresh_manifest_identity`, `::CatalogIdentity`.
- `src/worldmock/categories/maintenance.py::check_catalog`, `::refinement_report`, `::publish_catalog`, `::category_touches`, `::_rename_exclusive`.
- `src/worldmock/catalog/loader.py::load_catalog`, `::load_object_declarations`, `::_validate_identity`, `::_validate_versions`, `::_load_tombstones`.
- `src/worldmock/catalog/compiler.py::compile_exchange_object`, `::_compile_edges`, `::_edge_for_rule`.
- `src/worldmock/catalog/models.py::Catalog.pairing`, `::Catalog.rule_for`, `::ExchangeObject.validate_purchase_edges`, `::ResourceDefinition`, `::CatalogManifest`, `::CategoryRequirements`.
- `src/worldmock/catalog/admission.py::validate_scenario_against_catalog`, `::ScenarioCatalogRefs`, `::validate_world_categories`, `::_check_meals`, `::_check_partition_keys`, `::_check_partition_feasibility`, `::CONTENT_CORPORA`, `::validate_task_assignees`.
- `src/worldmock/catalog/affordances.py::engine_affordances`, `::load_affordances`, `::AffordanceView.resource_claims`, `::AffordanceView.attention_required_kinds`, `::START_ACTIVITY_ACTIONS`, `::REFERENCEABLE_RESOURCES`, `::GENERATOR_PARAM_KEYS`.
- `src/worldmock/catalog/builder.py::add_object`, `::add_resource`, `::deprecate`, `::_stage_and_publish`.
- `src/worldmock/resources/ledger.py::ResourceLedger.apply`, `::ResourceLedger.validate_history`; `src/worldmock/resources/models.py::ResourceMeta`, `::TransferEntry`, `::FlowEntry`, `::ConvertEntry`; `src/worldmock/resources/audit.py::audit_closed_domain`, `::audit_level_flows`.
- `src/worldmock/corpora/storefront.py::validate_assortments`, `::assortment_objects`, `::frozen_price`, `::_validate_menus`, `::_validate_richness`, `::_validate_admission`, `::_validate_positions`.
- `src/worldmock/corpora/manifest.py::load_manifest`, `::safe_file`.
- `src/worldmock/life/category_contract.py::require_runtime_rule`, `::admit_reachable_categories`, `::match_rows`, `::CONTRACT_OBJECT_IDS`, `::TRAVEL_OBJECT_ID`.
- `scripts/check_reachability.py::corpus_entries`, `::scenario_entries`, `::main`.

### 1.3 Importers (rg over src/, custom/, scripts/, tests/)

`worldmock.categories` is imported by:
- src: `catalog/{builder,compiler,inspect,loader,models}.py`, `cli.py`, `corpora/storefront.py`, `life/{basket,category_contract,inventory_recipe,semantics,session_actions,tool_layer,world_setup}.py`.
- tests: `tests/catalog/{admission_fixtures,test_branch_coverage,test_builder,test_category_admission,test_engine_package_resources,test_loader,test_storefront_admission}.py`, all of `tests/categories/*`, `tests/corpora/test_storefront_release.py`, `tests/integration/{test_category_identity,test_category_inventory_protocol}.py`, `tests/life/{purchase_worlds,test_category_runtime,test_category_runtime_edges,test_category_semantic_edges,test_coverage_closure_models,test_coverage_closure_projection,test_domain_branch_coverage,test_felt_states,test_motivation,test_parallel_admission,test_storefront_category_integration,test_storefront_discovery}.py`, `tests/test_import_graph.py`, `tests/test_shared_primitives.py`, `tests/tool_interaction_acceptance.py`.

`worldmock.catalog` is imported by:
- src: `categories/maintenance.py`, `life/{activities,admission,body,candidates,engine_context,environment_layer,interaction_menu,models,motivation,scenario,semantics,tool_layer,world_setup}.py`, `profile/coverage_targets.py`, `run_setup.py`, `runtime.py`, `validation.py`.
- custom: `custom/agents/life_agent.py` (`AFFORDANCES_FILE` digest for the tool-schema fingerprint).
- tests: all of `tests/catalog/*`, `tests/categories/{test_catalog_integration,test_contract,test_maintenance,test_rules}.py`, `tests/corpora/test_storefront_release.py`, `tests/footprints/test_validation.py`, `tests/integration/{activity_fixtures,test_life_agent_protocol}.py`, `tests/life/{test_affordance_contract,test_candidates,test_category_runtime_edges,test_coverage_closure_models,test_coverage_closure_projection,test_domain_branch_coverage,test_environment_effects,test_interaction_menu_invariants,test_load_checks,test_motivation,test_parallel_acceptance,test_scenario,test_scheduling,test_semantics,test_shared_body,test_shared_rules,test_travel_time,test_work_review,test_work_schedule}.py`, `tests/test_import_graph.py`, `tests/test_shared_primitives.py`.

`worldmock.resources` is imported by: `footprints/models.py`, `life/{body,inventory_recipe,settlement}.py`; tests `tests/life/{test_body_flows,test_money_settlement}.py`, all of `tests/resources/*`.

`worldmock.life.category_contract` is imported by: `life/{basket,inventory_recipe,semantics,session_actions,tool_layer}.py`; tests `tests/life/{test_category_runtime_edges,test_category_semantic_edges,test_coverage_closure_models,test_shared_rules}.py`.

`worldmock.corpora` (manifest/storefront) is imported by: `categories/identity.py`, `life/{content,game,map_data,semantics,travel,work,world_setup}.py`, `profile/coverage_apps.py`, `validation.py`; tests `tests/corpora/*`, `tests/test_shared_primitives.py`, `tests/tool_interaction_acceptance.py`.

`worldmock.data` package resources are read by: `catalog/affordances.py:323`, `life/world_setup.py:83` (byte-equality of the three frozen copies), `profile/mapper.py:36`, `custom/agents/life_agent.py:949`; tests `tests/catalog/test_engine_package_resources.py`, `tests/integration/test_life_agent_protocol.py:377`, `tests/profile/{test_branch_coverage,test_mapper}.py`.

---

## 2. Current contracts (frozen clauses defining this behaviour)

Anchors quoted exactly; "heading" when no anchor exists.

`docs/CONTRACTS.md`
- `<a name="contracts-资源与需求字段"></a>` — money integer fen, energy kcal, sleep hours, entertainment [0,1]; unit vocabulary the ledger metas implement.
- `<a name="contracts-类别供给的构建合同"></a>` — `category-assignment.json` (`kinds/locations/source`), kind admission pools (restaurant→prepared-meal etc.), richness floors, per-pair menu difference, `purchase_price_fen_min/max`, `price_position_percent`, SHA jitter quote formula, seeds.
- `<a name="contracts-营业与报价声明"></a>` — `QuoteConfig`, opening calendars, no purchase without a quote.
- `<a name="contracts-地点发现与购买身份"></a>` — supply rows expose `purchase_pairing_state/purchase_coefficient/purchase_price_fen`; `evaluate_purchase(quote.price_fen, quantity, coefficient)` per line; purchase channels derived from phone config.
- `<a name="contracts-持久化当下事实"></a>` — `supply_stock`, `supply_quotes`, world schema 9.
- `<a name="contracts-供给结算与事件载荷"></a>` — `price_change`, `product_release`, `delivery_arrival`, order `channel_id`.
- `<a name="contracts-资金事件字段"></a>` — pointer to recording engine conservation.
- `<a name="contracts-品类契约与目录字段"></a>` — top-level fields of `category-contract.json`, `exchange-rules.json`, `objects.jsonl`, `manifest.json`; closed enums; operator-specific fields table; `from_role=buyer`, `RuleEdge` compile; `recipe_output_bindings`; `INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE`; the four identity fields shared by catalog and run manifest.
- `<a name="contracts-语料内容目录字段"></a>` — `item_id/kind/source/.../attributes (营养)` field table.
- `<a name="contracts-环境标题与活动效果"></a>` — scenario `title`, `LifestyleItem.effects`.
- `<a name="contracts-领域枚举"></a>` — `WorldState.schema_version` 9, `catalog_version` unchanged across recent bumps.
- `<a name="contracts-cli"></a>` — `worldmock category check/refine/publish` inputs.

`docs/dev/SPEC-category-contract.md` (status FROZEN)
- `<a name="spec-category-contract-产品行为与边界"></a>` — objects belong to one category; shared attributes and rules on categories; both engines read one frozen contract.
- `<a name="spec-category-contract-裁决依据"></a>` — undefined vs no_effect, sparse pairings, most-specific wins, reachable-undefined rejects start, raw objects store deltas.
- `<a name="spec-category-contract-冻结产物与品类树"></a>` — Dataset provides `catalog/category-contract.json` + `catalog/exchange-rules.json`; manifest binds both SHA-256; single root, `person` node, product/service/contract subtrees.
- `<a name="spec-category-contract-属性槽位与对象增量"></a>` — JSON Schema slots, absolute/relative/proportional, `objects.jsonl` row shape, compile to `ExchangeObject` + typed edges, `ProductConfig` inherits `InventoryItemConfig`.
- `<a name="spec-category-contract-来源分类与审核证据"></a>` — fact/anchored/assumption/scenario; every static number covered exactly once; review records with `delegated_agent`.
- `<a name="spec-category-contract-配对解析与可触达检查"></a>` — directed pairing key, three states, ancestor-chain resolution, reachable closure from products/supplies/holdings/releases/tasks/routes/recipes.
- `<a name="spec-category-contract-闭集算子与真实结算"></a>` — the eight operators, Decimal rounding rules, price-range build bounds, per-line `evaluate_purchase`, contract slots bound by rule-named slots.
- `<a name="spec-category-contract-库存转换配方"></a>` — single-input single-output `consume:use` recipe, `ingredient` → `prepared-meal`, `inventory_unit=item`.
- `<a name="spec-category-contract-新品类细化与发布"></a>` — `worldmock category` check/refine/publish behaviour.
- `<a name="spec-category-contract-运行身份与导出"></a>` — run manifest records contract version + 3 digests; open/resume/replay reject same-version content drift.
- `<a name="spec-category-contract-文件修改清单"></a>`, `<a name="spec-category-contract-验收与需求台账"></a>` — file list and 15-row acceptance table.

`docs/design/02-exchange-engine.md`
- `<a name="exchange-engine-注册表登记什么"></a>` — registry = resource definitions + objects/corpora + usage rules by id.
- `<a name="exchange-engine-新增状态资源对象或语料"></a>` / `<a name="exchange-engine-修改与删除"></a>` — add/modify/retire flow, tombstones.
- `<a name="exchange-engine-查询适用规则"></a>` — resolve category → unique most-specific pairing → operator; `<a name="exchange-engine-交换对象与资源变化声明"></a>` — supply/object/activity/resource query table.
- `<a name="exchange-engine-可供性与活动声明"></a>` — resource claims closed set (idle/travel/eyes/ears/hands/voice/work).
- `<a name="exchange-engine-交换规则的加载与准入"></a>`.
- `<a name="exchange-engine-门店供给的时态与履约判断"></a>` — supply_id purchases, build price range, quote formula, delivery destination/route reasons, `prefer` selection.
- `<a name="exchange-engine-资源变化的计算"></a>` / `<a name="exchange-engine-不同交换的计算依据"></a>` — transfer/source/drain/convert; food energy by served fraction, work pay by contract.
- `<a name="exchange-engine-校验资源分录"></a>` — ledger invariants (the `resources/` package contract).
- `<a name="exchange-engine-食品交换的规则调用"></a>` — sequence diagram incl. food utilisation efficiency.

`docs/design/03-resource-engine.md`
- `<a name="resource-engine-语料清单与文件身份"></a>` — corpora manifest contract (`corpora/manifest.py`).
- `<a name="resource-engine-可计量资源定义"></a>` — `ResourceDefinition` fields and the four resources.
- `<a name="resource-engine-交换对象与内容条目"></a>` — `ObjectDeclaration`/`ExchangeObject`, per-content-type materials table (films need timelines, podcasts audio, games TextWorld, work bodies).
- `<a name="resource-engine-地点商品与门店供给"></a>` — kind admission pools, per-store assortments, `ingredient` cooking chain, quote formula.
- `<a name="resource-engine-文件职责"></a>` — catalog file table (`manifest.json`, `resources.jsonl`, `objects.jsonl`, `category-contract.json`, `exchange-rules.json`, `rules/off-foods.mapping.toml`, `overrides/tombstones.jsonl`).
- `<a name="resource-engine-构建与版本"></a>` — `catalog_version.major/minor`, append versions, tombstones, catalog 5.x identity.
- `<a name="resource-engine-目录发布的实现与验收"></a>`, `<a name="resource-engine-文件身份校验"></a>`, `<a name="resource-engine-准入边界"></a>` (five rows incl. world-material categories and retrieval reachability), `<a name="resource-engine-地图关系与道路范围准入"></a>`, `<a name="resource-engine-资源校验的实现与验收"></a>`.

`docs/manual/rules.md`
- `<a name="rules-配置入口与生效范围"></a>` — table of environment-configurable files incl. `category-contract.json`, `exchange-rules.json`, `rules/travel-time.toml`, `resources.jsonl`.
- `<a name="rules-品类契约"></a>` — authoring guide, `food-dairy` density slot example, pairing JSON.
- `<a name="rules-交换规则"></a>` — operator table, `purchase-product` example, price-range/quote formula.
- `<a name="rules-供给与制作边界"></a>` — category-assignment, cooking via `inventory_transform`.
- `<a name="rules-检查与发布品类修改"></a>` — `worldmock category` commands.
- `<a name="rules-食品来源映射说明"></a>` — `off-foods.mapping.toml`.
- `<a name="rules-身体规则"></a>` — body parameters live in `resources.jsonl` (`energy_kcal.bounds/alert_rules/drain_rules`, MET table) and contract category attributes (`work_efficiency_factors`, `work_notes_multiplier`).

`docs/manual/resources.md`
- `<a name="resources-编写门店供给与商品"></a>`, `<a name="resources-完整类别供给与冻结价格"></a>`, `<a name="resources-当前供给字段与发布注意事项"></a>` (`delivery_destination_ids`, `delivery_delay_seconds`), `<a name="resources-新增商品对象"></a>` (raw object example with `energy_kcal_per_serving`, `energy_density_kcal_per_100`), `<a name="resources-真实制作后再食用"></a>`, `<a name="resources-正餐家庭储备和联系人"></a>` (meals corpus with `energy_source`), `<a name="resources-修改后更新文件身份"></a>`.

`docs/VERIFICATION.md`
- `<a name="verification-品类契约特性门禁"></a>` — category-contract gate; "不得以原始对象格式兼容、无关配对的全矩阵要求、表达式兜底或未接线算子缩减验收范围".
- `<a name="verification-地图与门店供给"></a>` — row 「品类与供给组合」 (quote into pairing, coefficient, 4.0/binding 2 identity), 「自提与配送」 (delivery range), 「正式完整流程」.

`docs/EXPERIMENT.md`
- `<a name="experiment-环境发布与运行身份"></a>` — "身体、交通、品类及具体商品系数仍由环境绑定".
- `<a name="experiment-经济参数"></a>` — package contract 60000 fen; `travel_fare` driving min 500 fen / 0.2 fen per m from `exchange-rules.json`.
- `<a name="experiment-内容与语料绑定"></a>` — Open Food Facts foods, Sintel, TextWorld, AMI, commodity catalogue.
- `<a name="experiment-地图与门店供给的实验身份"></a>` — catalog 5.0/5.3/5.4, binding 3, category-assignment hash, quote formula.

Other frozen specs this subsystem implements (owned elsewhere but cite our modules): `docs/dev/SPEC-environment-data-ownership.md` (headings 「职责与接口」 rows 通用规则归属 / 测试输入归属 / 历史证据不可改写; no anchors), `docs/dev/SPEC-map-and-storefront.md` `<a name="spec-map-and-storefront-按地点类别生成完整供给"></a>`, `<a name="spec-map-and-storefront-冻结价格范围与门店差价"></a>`, `<a name="tool-interaction-地点供给和价格-类别准入与逐店商品集合"></a>`, `<a name="tool-interaction-地点供给和价格-范围内定价"></a>`; `docs/dev/SPEC-life-realism.md` `<a name="spec-life-realism-世界素材品类要求"></a>`, `<a name="spec-life-realism-素材可达性"></a>` (implemented by `admission.validate_world_categories` and `scripts/check_reachability.py`); `docs/dev/SPEC-shared-rules-and-rebuildable-env.md` `<a name="shared-rules-摘要安全路径持久写入与错误"></a>` (`safe_file`, `CategoryError`) and `<a name="shared-rules-其余已存在的同义规则表"></a>`; `docs/design/05-recording-engine.md` `<a name="recording-engine-资金守恒验证"></a>` (`resource_changes` ledger audit) and lines 577-578 (run manifest freezes the four identity fields); `docs/design/09-io-formats.md` `<a name="io-formats-职责与边界"></a>` (pointer to the category contract); `docs/design/08-configuration.md` `<a name="configuration-规则来源与配置边界"></a>`, `<a name="configuration-资源目录引用"></a>`.

---

## 3. Current formats and fixtures

### 3.1 Catalog directory (`<env>/catalog/`, loaded by `catalog/loader.py`)

| File | Shape |
| --- | --- |
| `manifest.json` | `{version_major, version_minor, category_contract_version, category_contract_sha256, exchange_rules_sha256, catalog_content_sha256, category_requirements?}`; `category_requirements` = `{declared_at, spec, meals{min_count, price_fen_min/max, duration_minutes_min/max, energy_databases[]}, location_required_kinds[], location_min_per_kind{}, home_pantry{breakfast_servings_min, morning_wash_minutes}, office_affordances[], content_metadata{fields[], max_age_rating}, work_materials{min_distinct, language, horizon_days}, lifestyle_fields[], contacts{min_per_persona, colleague_reply_policy, other_reply_policy}}`. |
| `category-contract.json` | `{format_version:1, contract_version, categories[{category_id, name, parent_id, kind, slots{<slot>:{schema, required, unit}}, values{absolute, relative, proportional}}], pairings[{actor_category_id, target_category_id, action, state, rule_id?, coefficient?, review?}], sources[SourceRecord], review}`. |
| `exchange-rules.json` | `{format_version:1, contract_version, rules[{rule_id, category_id, action, operator, resource_id, unit, conservation, +operator fields}], sources, review}`. Operator fields: `from_role/to_role`; `energy_slot/duration_slot`; `effect_slot`; `minimum_fen/fen_per_meter`; `rate_slot/maximum_duration_slot`; `rate_slot`; `amount_slot`; `input_category_id/output_category_id/input_quantity/output_quantity/inventory_unit`. |
| `resources.jsonl` | one `ResourceDefinition` per line: `{resource_id, kind: account|level, unit, owner, bounds{min,max|null}?, conservation: open|closed, alert_rules[{below|above, code}], drain_rules{basal_kcal_per_hour, activity_met{<met_key>:float}, food_efficiency_curve[{input_value, output_value}], awake_sleep_debt_per_hour, sleep_recovery_per_hour, entertainment_need_per_awake_hour, entertainment_relief_per_hour{watch, play_game}}?, provenance[{part, type, source, artifact?, method?, paths[], note?}], version}`; append history, latest version wins. |
| `objects.jsonl` | `{object_id, name, category_id, values{absolute, relative, proportional}, provenance[SourceRecord], version}`; compiled slots consumed by runtime: `price_fen` (fixture only), `merchant_role`, `energy_kcal_per_serving`, `use_duration_seconds`, `use_modes`, `consumable`, `effect`, `purchase_price_fen_min/max`, `inventory_unit`, `serving_quantity`, `serving_unit`, `required_location_id`, contract slots `acceptance_required_effective_seconds`, `acceptance_materials_fully_revealed`, `acceptance_review_pass_required`, `work_notes_multiplier`, `work_efficiency_factors`, and dataset-only slots (`energy_density_kcal_per_100`, rate/amount slots). |
| `overrides/tombstones.jsonl` | `{target_id, kind: resource|object, at_version, reason, superseded_by?}` (optional). |
| `affordances.toml`, `rules/motivation.toml`, `rules/persona-mapping.toml` | byte copies of `worldmock.data` package resources; `world_setup.py` rejects any byte difference. |
| `rules/travel-time.toml` | `version=1`, `[factors]` walking + `driving_00..driving_23`, `[provenance.factors]`; read as raw bytes by loader, parsed by `life/travel_time.py`. |
| `rules/off-foods.mapping.toml` (DS only) | source mapping note, hashed into identity, never executed. |

Identity: `catalog_content_sha256` = SHA-256 over frames `path\0len(8 bytes)content` for `resources.jsonl, objects.jsonl, overrides/tombstones.jsonl, category-contract.json, exchange-rules.json, affordances.toml`, then sorted `rules/*.toml`, then canonical JSON of manifest minus `catalog_content_sha256`.

### 3.2 Scenario fields read by this subsystem

`catalog_version {major, minor}` (pin), `products[{product_id}]`, `supplies[]` (map supply ids), `recipe_output_bindings{<category_id>: <object_id>}`, `agents[].initial_inventory{<location>: {<object_id>: qty}}`, `accounts[{account_id, money_fen, role}]` (roles `buyer`, `merchant:*`, `employer:*`, `transport:service`, `external:scenario`), `body{mass_kg, initial_energy_kcal, initial_sleep_debt_hours, initial_entertainment_need, entertainment_relief_preference_floor?}`, `tasks[].contract_kind` (hourly|piece|package → `contract-hourly/piece/package` objects), map routes → `travel-service` object and `travel:<mode>` action.

Runtime views compiled from the catalog: `life/scenario.py::InventoryItemConfig` (`product_id, name, category_id, serving_quantity, serving_unit, inventory_unit, energy_kcal_per_unit, use_duration_seconds, use_modes, consumable, effect, required_location_id, source_kind`), `ProductConfig` (+`purchase_price_fen_min/max`), `BodyConfig` (`maximum_energy_kcal`, `basal_kcal_per_hour`, `activity_met`, `food_efficiency_curve`, `hunger/emergency_threshold_kcal`, `work_efficiency_factors`, `work_notes_multiplier`, `entertainment_relief_per_hour`, `source_kind="catalog:M.m"`).

### 3.3 Map/corpora files consumed by `corpora/storefront.py` and `catalog/admission.py`

- `corpora/<map>/category-assignment.json`: `{kinds{<kind>: [category_id]}, locations[{location_id, category_ids[], object_ids[], price_position_percent, source}], source}`; `dataset.json` carries `random_seed`, `category_assignment_sha256`; `supplies.json` rows `{supply_id, location_id, product_id, seller_account_id, quotes[{price_version, price_fen, ...}], initial_stock, fulfillment_modes[pickup|delivery], delivery_destination_ids[], delivery_delay_seconds, required_qualifications[], source}`.
- `corpora/manifest.json`: `{schema_version, corpus_id, created_at(tz-aware), transformer_version, entries[{entry_id, path, sha256, size_bytes, media_type, purpose, source{kind, url?, retrieved_at?, license, selector, fact_cutoff?}}]}`.
- World-category admission reads `meals/items.json` (`meal_id, price_fen, meal_duration_minutes, dine_in, delivery, energy_source{database, source_url, retrieved_at, unit_conversion, energy_kcal_per_100g}`), `scene-stock/home-pantry.json`, `scenario-contacts/contacts.json`, `scenario-work/materials.json`, `lifestyle/items.json`, `<locations>.json` (`kind, business_kind, location_kinds, affordances{pantry_water, meeting_room, colleagues_present}`), the 13 `CONTENT_CORPORA` files (`item_id, title, genre[], language, age_rating, duration_seconds|estimated_read_seconds, location_kinds`), and `manifest.json` entries starting with `ami-es2002/`.

### 3.4 Event payload / state keys owned here

- `action_result.data.category_matches[] = {category_id, rule_id, action}` (written by `session_actions.py:1274/1398/1431`, `storefront_runtime.py:53`, `order_routing.py:67`; copied by `action_evidence.py:38`; aggregated by `maintenance.category_touches`).
- `Event.data.resource_changes` (integer fen; audited by `resources/audit.py` users in footprints).
- Ledger entries: `TransferEntry(resource, from_owner, to_owner, amount, cause, occurred_at)`, `FlowEntry(resource, owner, direction, amount, cause, occurred_at)`, `ConvertEntry(inputs, outputs, cause, occurred_at)`; owners `account:<id>`, `external:scenario` anchor, `agent:<id>`.
- Run manifest / checkpoint manifest / export `frozen_run_manifest`: `category_contract_version`, `category_contract_sha256`, `exchange_rules_sha256`, `catalog_content_sha256` (`life/run_identity.py:16-19`).
- `BodyConfig.source_kind = "catalog:<major>.<minor>"`, `InventoryItemConfig.source_kind = "catalog:<object_id>:v<version>"`.

### 3.5 Fixture files encoding these formats

- `tests/fixtures/environment/catalog/manifest.json` (3.0, contract 1), `category-contract.json` (entity/person/product/food/service/contract/prepared-meal/durable/ingredient; 2 pairings), `exchange-rules.json` (`buy-product`, `eat-food`), `resources.jsonl` (money_fen, energy_kcal with MET table incl. `lifestyle:quiet-rest`, sleep_debt_hours, entertainment_need), `objects.jsonl` (milk, travel-service, contract-hourly/piece/package), `affordances.toml`, `rules/{motivation,persona-mapping,travel-time}.toml`.
- `tests/fixtures/environment/corpora/manifest.json`, `artificial-map/{category-assignment,dataset,locations,routes,supplies}.json`, `lifestyle/items.json`, `content/test.srt`; `tests/fixtures/environment/scenarios/{single-person,multi-person,multi-person-half-day}.json` (`catalog_version {3,0}`, `products [milk]`, `supplies []`); `tests/fixtures/environment/README.md`.
- Programmatic builders: `tests/categories/conftest.py` (`contract_document`, `rules_document`, `raw_object`, `write_catalog`, `valid_catalog_dir`), `tests/categories/scenario_fixtures.py` (adds `test-driving` travel rule, driving routes), `tests/catalog/admission_fixtures.py` (`category_requirements()`, `write_category_catalog`, `write_category_corpora`, `CONTENT_PATHS`), `tests/catalog/conftest.py` (`valid_refs`), `tests/resources/conftest.py` (`metas`, `ledger`), `tests/resources/fixtures/anchor-money.json` (day-run money anchor: accounts + `resource_changes` events), `tests/life/purchase_worlds.py`, `tests/life/test_category_runtime.py::_fixture/_add_recipe_catalog` (recipe catalog with `ingredient` + `inventory_transform`), `tests/integration/test_category_identity.py::_expected_identity`.
- Ledger/baseline JSON: `tests/fixtures/mutation-symbol-baseline.json` (`tool:category_inheritance_exact_only`, `tool:restaurant_menus_identical`, `tool:assortment_window_cut`, `tool:price_position_ignored`, `tool:price_outside_bounds`, `tool:quote_bound_check_removed`, …), `tests/fixtures/acceptance_requirements.json["tool-interaction"]` rows 类别供给 / 地点定价 / 购买与食用 / 料理.

---

## 4. Tests locking current behaviour

### 4.1 Test files (node names for the most important)

- `tests/categories/test_contract.py` (24 tests): tree/slot/delta semantics — `test_invalid_category_tree_rejected`, `test_relative_child_tracks_parent_value`, `test_proportional_values_multiply_the_inherited_base`, `test_child_schema_keeps_all_ancestor_constraints`, `test_missing_and_duplicate_numeric_sources_rejected`, `test_model_fact_and_unreviewed_agent_rejected`, `test_raw_object_rejects_edges_and_formula`.
- `tests/categories/test_pairing.py` (10): `test_missing_pairing_is_undefined_without_materializing_matrix`, `test_explicit_undefined_shadows_ancestor_definition`, `test_incomparable_double_specialization_is_ambiguous`, `test_no_effect_requires_review`.
- `tests/categories/test_rules.py` (26): `test_unknown_operator_and_formula_string_rejected`, `test_every_operator_rejects_the_wrong_resource_shape`, `test_fixed_runtime_resources_reject_same_domain_aliases`, `test_closed_inventory_requires_equal_quantities`, `test_money_and_energy_rounding_operators`, `test_travel_uses_raw_distance_and_rounds_final_fen`, `test_recipe_scales_both_sides_once`, `test_money_operators_preserve_exact_amounts_independently_of_decimal_context`.
- `tests/categories/test_catalog_integration.py` (9): `test_real_loader_compiles_raw_object_to_effective_view`, `test_same_version_content_tamper_is_rejected`, `test_manifest_binds_contract_and_catalog_content`, `test_food_duration_uses_rule_named_slot`, `test_durable_effect_uses_rule_named_slot_and_default`.
- `tests/categories/test_maintenance.py` (16) and `test_publication_atomic.py` (6): `worldmock category` check/refine/publish, exclusive rename on win32/darwin/linux.
- `tests/catalog/test_loader.py` (12): `test_manifest_identity_publication_failure_keeps_previous_bytes` (in shared-rules ledger), `test_old_edges_raw_schema_rejected_after_identity_refresh`, `test_travel_time_bytes_are_captured_at_load`, `test_any_rule_toml_changes_catalog_content_identity`, `test_affordances_changes_catalog_content_identity`, `test_same_version_dependency_tamper_changes_digest_and_is_rejected_by_loader`, `test_unknown_rule_resource_rejected`.
- `tests/catalog/test_admission.py` (9), `test_branch_coverage.py` (6), `test_builder.py` (6), `test_inspect.py` (5), `test_models.py` (7), `test_storefront_admission.py` (7: `test_product_cannot_smuggle_a_purchase_price_edge`, `test_purchase_rule_rejects_the_removed_product_price_slot`).
- `tests/catalog/test_category_admission.py` (27): every `validate_world_categories` rule (`test_meals_missing_energy_source_annotation`, `test_location_restaurant_floor_of_two`, `test_home_pantry_below_seven_servings`, `test_content_age_rating_eighteen_rejected`, `test_drama_corpus_must_be_chinese`, `test_work_ami_metadata_retained_enforced`, `test_contacts_colleague_mapping_invalid`, `test_task_assignee_agent_id_required`).
- `tests/catalog/test_reachability_admission.py` (8): partition keys/feasibility (`test_content_item_missing_partition_dimension_is_unreachable`, `test_partition_with_no_feasible_place_is_unreachable`).
- `tests/catalog/test_affordance_shadow.py` (43): registry ↔ engine closed sets (`test_resource_claims_match_activities`, `test_attention_required_keys`, `test_habit_routes_cover_every_way`, `test_claim_slots_are_the_seven_spec_slots_and_retired_names_are_rejected`, `test_conditional_claims_reject_unknown_dimension_and_undeclared_content_kind`, `test_energy_projection_is_gone_from_registry_and_observation_contract`).
- `tests/catalog/test_engine_package_resources.py` (10): package-resource reads, byte-identical frozen copies, `test_built_wheel_reads_all_rules_after_installation`.
- `tests/resources/test_resource_ledger.py` (19), `test_resource_models.py` (12), `test_resource_audit.py` (8), `test_branch_coverage.py` (20), `test_equivalence_anchor.py` (4: replay of `anchor-money.json`).
- `tests/life/test_category_runtime.py` (9: `test_reachable_undefined_category_pairing_refuses_before_run`, `test_purchase_transfer_requires_buyer_account`, `test_food_coefficient_changes_body_settlement`, `test_no_effect_use_rejects_without_inventory_change`, recipe tests), `test_category_runtime_edges.py` (16), `test_category_semantic_edges.py` (8: travel-time parser, purchase role recheck), `test_affordance_contract.py` (3), `test_storefront_category_integration.py` (4), `test_storefront_discovery.py::test_category_price_does_not_rewrite_source_quote`, `test_coverage_closure_models.py::test_assembly_pairing_lookup_leaves_non_defined_pairings_to_admission`, `test_shared_rules.py::test_contract_templates_match_scenario_loading_and_resource_mapping`, `test_semantics.py::test_energy_thresholds_come_from_catalog_alert_rules`.
- `tests/integration/test_category_identity.py` (4: identity in create/open/resume/replay), `test_category_inventory_protocol.py` (2).
- `tests/corpora/test_storefront_release.py` (21: assortment, richness, menu difference, price formula, positions), `test_corpora.py`, `test_branch_coverage.py`, `test_windows_coverage.py` (manifest/`safe_file`).
- `tests/footprints/test_validation.py::test_validate_project_runs_world_category_admission`, `::test_build_coverage_targets_derives_from_catalog_and_corpora`.
- `tests/test_shared_primitives.py` (`CategoryError`, `DeepFrozenModel`, `_read_content_file`, CurvePoint shared), `tests/test_single_source.py` (OWNERS), `tests/test_import_graph.py` (lazy-import exemptions for `categories.cli`).

### 4.2 Acceptance-ledger membership (deletion/rename cost)

- `tests/tool_interaction_acceptance.py` BEHAVIOR rows 类别供给, 地点定价, 购买与食用, 料理 → killer nodes in `tests/corpora/test_storefront_release.py`; `MUTATIONS` target `worldmock.corpora.storefront::assortment_objects` (window cut; product-only), `::validate_assortments` (`tool:restaurant_menus_identical`), `::frozen_price` (`tool:price_position_ignored`, `.percent`), `::_validate_quotes` (`tool:quote_bound_check_removed`), `worldmock.categories.models::CategoryContract.is_descendant` (`tool:category_inheritance_exact_only`), `worldmock.categories.contract::_validate_values` (price range), `worldmock.life.scenario::_check_catalog_references`. Their mutant digests are pinned in `tests/fixtures/mutation-symbol-baseline.json`; `tests/test_tool_interaction_acceptance.py:19` lists `src/worldmock/corpora/storefront.py` as a frozen production file; the row names are mirrored in `tests/fixtures/acceptance_requirements.json["tool-interaction"]` (section 实施... tables) and checked bidirectionally.
- `tests/shared_rules_acceptance.py` RULES: '原子写' → `tests/catalog/test_loader.py::test_manifest_identity_publication_failure_keeps_previous_bytes`; '品类与目录错误' and '安全文件' → `tests/test_shared_primitives.py::test_original_safe_file_consumers_reject_symlink_before_hash_or_schema` (exercises `categories.identity._read_content_file` and `corpora.manifest.safe_file`); '公共枚举' → `tests/test_single_source.py::test_production_shared_rules_have_one_owner_and_no_business_clones`; '曲线与效率因子' → `test_curve_models_shared_by_life_and_catalog_reject_invalid_numbers`; '合同对象集合' → `tests/life/test_shared_rules.py::test_contract_templates_match_scenario_loading_and_resource_mapping`.
- `tests/test_single_source.py::OWNERS` pins owners: `categories/common.py` (`CategoryError`, `DeepFrozenModel`, `ProvenanceType`, `Conservation`), `categories/provenance.py` (`NUMERIC_SOURCE_METADATA_ROOTS`), `resources/models.py` (`AMOUNT_TYPES`), `corpora/manifest.py` (`safe_file`). Moving/deleting these requires editing OWNERS (any clone elsewhere is a gate failure).
- `tests/time_and_energy_acceptance.py` registers `tests/catalog/test_affordance_shadow.py::{test_energy_projection_is_gone_from_registry_and_observation_contract, test_claim_slots_are_the_seven_spec_slots_and_retired_names_are_rejected, test_conditional_claims_reject_unknown_dimension_and_undeclared_content_kind, test_resource_claims_match_activities, test_habit_routes_cover_every_way, test_habit_routing_reads_ways_not_verbs_or_retired_phrases}` and `tests/life/test_semantics.py::test_energy_thresholds_come_from_catalog_alert_rules`; `tests/fixtures/acceptance_requirements.json["time-and-energy"]` carries the matching clauses.
- `tests/fixtures/layering-public-api.json` and `layering-acceptance.json`: no direct rows for this subsystem (only `storefront_runtime.py::StorefrontRuntime`, outside scope). `tests/fixtures/current-feature-owners.json`: no rows.
- `tests/test_import_graph.py:23-39` whitelists the lazy imports `worldmock.categories.cli → maintenance` and `worldmock.cli → categories.cli`; deleting the command requires removing those rows.
- Documentation gates (`scripts/documentation_gate.py`, `tests/test_documentation_gate.py`): every anchor listed in §2 is link-checked from many documents (41 docs reference `SPEC-category-contract`), so anchors must be rewritten in place or redirected, not dropped.

---

## 5. What the proposal changes here

Guiding reading of the proposal for this subsystem: "分类规则和交换规则在 WorldMock 系统端钉死", "资源目录只存对象及其关系，运行时按内置类型直接处理动作；不再为每个对象编译一份交换边", "删除 ... 数据集品类树、属性继承、交换规则配对与规则覆盖", "旧格式直接拒绝 ... 不新增 ... 兼容层、迁移工具", "登记时一次性检查唯一标识、引用存在、类型有效、正数价格与时长、绑定完整性和同类店铺商品一致性", "保留环境版本、文件清单、内容哈希与系统规则版本".

### 5.1 Fixed classification and rules → WorldMock (DELETE the dataset rule layer, ADD a constants module)

DELETE (whole files): `src/worldmock/categories/{models,contract,pairing,rules,provenance,loader,maintenance,cli}.py`; `src/worldmock/catalog/{compiler,builder,inspect}.py`; `src/worldmock/life/category_contract.py`; `src/worldmock/corpora/storefront.py`; `worldmock category` subcommand in `src/worldmock/cli.py` and the import-graph exemptions.

DELETE (symbols): `catalog/models.py::{AttributeValues-based ObjectDeclaration, ExchangeObject, CompiledEdge, TransferEdge, SourceEdge, DrainEdge, RuleEdge, Tombstone, CatalogEntryKind, Acceptance, Efficiency, Serving, CategoryRequirements, MealRequirements, ContentMetadataRequirements, WorkMaterialRequirements, PantryRequirements, Catalog.pairing, Catalog.rule_for, is_role_placeholder}`; `categories/common.py::{PairingState, CategoryKind, ProducerType, ReviewerType, SourceRecord, ReviewRecord}`; `categories/operators.py::{evaluate_hourly_pay, evaluate_piece_pay, evaluate_package_pay, scale_inventory_recipe, InventoryDelta}` (work pay goes through the existing payroll schedule event; recipes exit if decision 做饭是否保留 = delete); `catalog/admission.py::{_check_rule_refs, _check_tombstones, _referenced_edges, _edge_resource_ids, _check_edge_units, _check_transfer_roles, RULE_REF_UNKNOWN, TOMBSTONED_REFERENCE, ScenarioScheduleRef.object_id/object_version}`.

KEEP as-is: `categories/common.py::{CategoryError, DeepFrozenModel, Conservation, require_finite_number}` (OWNERS entries; `ProvenanceType` can go if no summary-origin enum reuses it — see §8); `categories/operators.py::{evaluate_purchase, evaluate_travel_fare, evaluate_food_energy, _multiply_exact, _decimal_*}` with simplified signatures (no pairing coefficient: `evaluate_purchase(unit_price_fen, quantity)`, `evaluate_travel_fare(mode, distance_meters)` reading a fixed table, `evaluate_food_energy(category, consumed_fraction)`); the whole `resources/` package; `corpora/manifest.py`.

ADD `src/worldmock/exchange/fixed.py` (package name is a spec decision; could also replace `worldmock.categories` in place — but the name "categories" would be misleading once there is no tree). Contents, all module-level frozen constants with `RULES_VERSION: int` (bump on any change; proposal: "运行身份同时绑定引擎规则版本和环境内容摘要"):

```python
class LocationKind(StrEnum):   # proposal: 餐馆、咖啡店、便利店、超市 sell food; 住宅、工作地点、公园 do not
    HOME="home"; OFFICE="office"; PARK="park"; RESTAURANT="restaurant"; CAFE="cafe"
    CONVENIENCE_STORE="convenience_store"; SUPERMARKET="supermarket"   # + FITNESS/WET_MARKET only if the map spec keeps them
FOOD_STORE_KINDS = frozenset({RESTAURANT, CAFE, CONVENIENCE_STORE, SUPERMARKET})   # expansion set of `all`
class FoodCategory(StrEnum): SNACK="snack"; DRINK="drink"; MEAL="meal"             # 零食/饮品/正餐
FOOD_CONSTANTS = {SNACK: (200 kcal, 300 s), DRINK: (100, 180), MEAL: (600, 1200)}   # proposal table, pending 决策 食品常数
class ContentKind(StrEnum): ARTICLE="article"; IMAGE_TEXT="image_text"; SHORT_VIDEO="short_video"; LONG_VIDEO="long_video"; LIVE="live"
class Action(StrEnum): PURCHASE, CONSUME_EAT, TRAVEL_WALKING, TRAVEL_DRIVING (+ USE_APP, WORK if the spec wants one closed list)
TRAVEL_SPEED_M_PER_S = {walking: ..., driving: ...}; TRAVEL_FARE = {walking: (0, 0.0), driving: (500, 0.2)}   # from EXPERIMENT 经济参数
DELIVERY_PREP_SECONDS, DELIVERY_SPEED_M_PER_S, DELIVERY_FEE_FEN                   # pending 决策 外卖时间
PUSH_WINDOW_LOCAL = (8, 22)                                                        # entertainment subsystem reads it
```

Action admission becomes a direct function of built-in type: `food purchasable at any store that binds it`, `food edible when held`, `content usable through a bound app`; no pairing resolution, no coefficient, no `category_matches`.

### 5.2 New catalog format (REWRITE `catalog/models.py`, `catalog/loader.py`; dataset files)

Replace `objects.jsonl` + `category-contract.json` + `exchange-rules.json` + `overrides/tombstones.jsonl` + `rules/travel-time.toml` + `category-assignment.json` + `supplies.json` + `meals/items.json` + `off-foods.mapping.toml` with five catalog files, one immutable snapshot per environment publication (no append history, no minor bumps, no tombstones):

| File | Row shape (strict `DeepFrozenModel`, extra=forbid) |
| --- | --- |
| `catalog/apps.json` | `{apps:[{app_id, name}]}` |
| `catalog/locations.json` | `{locations:[{location_id, name, kind: LocationKind, app_ids:[app_id]}]}`; `app_ids` only legal when `kind in FOOD_STORE_KINDS`; empty list = dine-in only. (Coordinates/business_kind/location_kinds/affordances/opening calendars are map-subsystem decisions; the catalog row above is what this subsystem validates.) |
| `catalog/foods.json` | `{foods:[{food_id, name, category: FoodCategory, price_fen: int>0, store_ids: [location_id] \| "all", summary?: str, origin: "real"\|"synthetic"}]}` |
| `catalog/contents.json` | `{contents:[{content_id, name, kind: ContentKind, summary, summary_origin: "real"\|"synthetic", source?: str, duration_seconds: int>0, app_ids:[app_id] (min 1), series?: {title, episode}}]}` |
| `catalog/work_topics.json` | `{work_topics:[{topic_id, topic, target_hours: number>0}]}` |
| `catalog/map.json` | generated at registration (map subsystem): `{nodes:[location_id], edges:[{a, b, length_m:int>0}]}`; listed in the manifest like every other file. |
| `catalog/manifest.json` | `{format_version: 1, environment_id, environment_version, rules_version (must equal WorldMock RULES_VERSION), files:[{path, sha256, size_bytes}], catalog_content_sha256}`. Reuse `corpora/manifest.py::CorpusEntry` shape and `safe_file`; drop the custom framing digest (or keep `_update_frame` over the listed files — either is fine, choose one). |

Registration checks (`catalog/admission.py` REWRITE, run once in `load_catalog`, all fail-closed with stable English codes): `CATALOG_ID_DUPLICATE`, `CATALOG_REF_UNKNOWN` (store_ids/app_ids/location kinds), `CATALOG_KIND_INVALID` (unknown enum member or extra field → old formats rejected by `extra=forbid` plus `format_version`), `CATALOG_NUMBER_INVALID` (price/duration/target_hours/length must be positive finite), `FOOD_BINDING_MISSING` (empty store set and not `all`), `FOOD_STORE_NOT_FOOD_STORE` (bound location kind outside FOOD_STORE_KINDS), `FOOD_BINDING_KIND_INCONSISTENT` (for every kind k and food f: f bound to some store of kind k ⇒ f bound to every store of kind k; `all` expands to all FOOD_STORE_KINDS stores), `CONTENT_APP_MISSING`, `APP_UNUSED` is *not* an error (an app may have no bindings). Derived indexes built once and frozen on the `Catalog` dataclass: `menus_by_kind: {LocationKind: frozenset[food_id]}`, `foods_by_store: {location_id: frozenset[food_id]}`, `stores_by_app`, `contents_by_app`, `apps_by_store`. These replace `assortment_objects`, `validate_assortments`, `frozen_price`, `supply_stock/supply_quotes`.

`Catalog` dataclass fields: `environment_id, environment_version, rules_version, catalog_content_sha256, apps, locations, foods, contents, work_topics, map, <indexes>, source_dir`. DELETE: `version_major/minor`, `category_contract_version`, `category_contract_sha256`, `exchange_rules_sha256`, `resources`, `raw_objects`, `objects`, `tombstones`, `category_contract`, `exchange_rules`, `travel_time_rules_bytes`, `category_requirements`.

Scenario (life subsystem, coordinated here): `catalog_version{major,minor}` → `environment_id` + `catalog_content_sha256` pin; DELETE `products`, `supplies`, `recipe_output_bindings`; `agents[].initial_inventory` keys become `food_id` with integer servings; `accounts[]` keep `buyer/employer/transport/external` roles and add one merchant account per food-store location (`role: "merchant:<location_id>"`) — simplest reuse of `TransferEntry` buyer→store.

### 5.3 Resource definitions and ledger

KEEP `resources/{models,ledger,audit}.py` unchanged (proposal row "余额账本、持有数量、订单状态和原子提交 | 可直接复用").

`resources.jsonl` (`ResourceDefinition`, `DrainRules`, `AlertRule`, `ResourceBounds`): the proposal moves "资源单位与交换公式" and "食品热量、交通速度、娱乐效果" to WorldMock but is silent on body parameters (basal rate, MET table, thresholds, sleep/entertainment rates). Recommended smallest design: move the four `ResourceMeta`s and the body parameter table into `exchange/fixed.py` (`RESOURCE_METAS`, `BODY_DEFAULTS`) because the MET keys already must equal the engine-owned `affordances.toml` and because the env copy mechanism exists only to pin identity; delete `ResourceDefinition/DrainRules/AlertRule/Provenance(part)` and `semantics._assemble_body`'s catalog reads. If the spec decides body parameters stay environment-owned, keep `resources.jsonl` as the one remaining dataset "numbers" file but strip `provenance[].paths` coverage to the `origin` marker. See §8 Q3.

`inventory/item` level resource and `ConvertEntry` use in `life/inventory_recipe.py`: DELETE if cooking is removed (decision 做饭是否保留); food holdings remain plain `agents[].inventory` counts (no ledger resource needed, as today for non-recipe foods).

### 5.4 Identity (REWRITE `categories/identity.py` → `catalog/identity.py`)

`CatalogIdentity` becomes `{environment_id, environment_version, rules_version, catalog_content_sha256}`; `compute_catalog_identity` = digest over the manifest file list; `refresh_manifest_identity` keeps its atomic write (`footprints.assets.atomic_write`). `life/run_identity.py`, checkpoint manifest, export `frozen_run_manifest`, `tests/integration/test_category_identity.py::_expected_identity` and `docs/design/05-recording-engine.md:577` switch to these fields; `open/resume/replay` keep rejecting same-version content drift (mechanism unchanged).

### 5.5 Affordance registry (`src/worldmock/data/affordances.toml`, `catalog/affordances.py`) — REWRITE in place

- `families.entertainment.content_kinds` → exactly `article, image_text, short_video, long_video, live` (all `attention_required = true`, or drop attention sampling entirely if the entertainment spec removes profile attention — proposal: "不需要推荐画像"); `kinds.watch.resource_claims.conditional` → eyes for all five (live also ears); `kinds.play_game`, `game_boundary`, generator `catalog_visibility`/family `browse` (store menus are static and identical per kind, so probing a store has nothing to reveal) → DELETE pending map/storefront spec; `kinds.work.target_discovery` → `{field="work_topics", id_key="topic_id"}`; `kinds.watch.target_discovery` → `{source="catalog_field", field="apps", id_key="app_id"}` (person picks the app); `families.eat.candidate = cheapest_feasible_meal` keeps its name but reads `foods_by_store`.
- `affordances.py` code: remove `game_boundary` mutex, `content_rate` relief requirement tied to browse discovery, `GENERATOR_PARAM_KEYS["catalog_visibility"]`; keep parsing/validation structure, `engine_affordances()`, `REFERENCEABLE_RESOURCES`.
- `tests/fixtures/environment/catalog/affordances.toml` and the dataset copy must be regenerated byte-for-byte (`world_setup.py` identity check stays).
- `motivation.toml`, `persona-mapping.toml`: unchanged; the persona compiler's `attention_prior` keys follow the new content kinds (profile subsystem).

### 5.6 World-material admission and reachability

`catalog/admission.py::validate_world_categories` → DELETE `_check_meals` (OFF energy sources, price/duration windows, `MEALS_CORPUS`), `_check_content`/`_check_content_item`/`_check_drama_chinese`/`CONTENT_CORPORA`, `_check_work` (AMI, materials), `_check_partition_keys`/`_check_partition_feasibility`/`_retrieval_entries`/`_partitions_of`/`_place_kinds` and the `worldmock.life.retrieval` import; DELETE `scripts/check_reachability.py` (content is chosen uniformly from the app's binding set; there is no retrieval partition to be unreachable). KEEP (outside this proposal's scope, so untouched): `_check_locations` floor per kind (rewired to `LocationKind`), `_check_home` pantry, `_check_office`, `_check_lifestyle`, `_check_contacts`, `validate_task_assignees` → rewired to work topics (`assignee_agent_id` stays). `CategoryRequirements` shrinks to `{location_required_kinds, location_min_per_kind, home_pantry, office_affordances, lifestyle_fields, contacts}` or moves into WorldMock constants (the proposal says floors are not dataset rules).

`validate_scenario_against_catalog` keeps: environment pin, account→closed money resource, initial level values within bounds, schedule strictly increasing with declared accounts (`bill_due`, `payroll`); drops everything edge/rule/tombstone related.

### 5.7 Runtime wiring in `life/` that this subsystem must hand over (cross-subsystem, listed so the spec can coordinate)

`semantics.py`: delete `validate_assortments`, pin on major/minor, `admit_reachable_categories`, `purchase_pairing/purchase_coefficient`, `contract_payment/contract_match`, `travel_match`, `_assemble_items` from compiled objects, `_consume_edge`, `_catalog_refs` object list; `purchase_amount(food_id, quantity)` = `evaluate_purchase(food.price_fen, quantity)`; `travel_fare(mode, distance)` from fixed table; energy on eating = `FOOD_CONSTANTS[category].kcal × consumed_fraction` then existing `body.apply_food` efficiency/cap. `basket.py:343/389` use `energy_kcal_per_unit` → replace with category constant; `tool_layer.py:498` food label from constant; `session_actions.py` / `storefront_runtime.py` / `order_routing.py` / `action_evidence.py` drop `category_matches`; `inventory_recipe.py` deleted with cooking; `work.py`/`work_review*.py` contract payment path deleted (work subsystem); `travel_time.py` parser of `travel-time.toml` deleted (map subsystem).

### 5.8 Docs in scope to rewrite/delete (implementation-time, per proposal "旧规则和相关现行文档在实现时同步删除或改写")

Rewrite in place: `docs/CONTRACTS.md` `contracts-品类契约与目录字段` (becomes "目录文件与固定类型"), `contracts-类别供给的构建合同`, `contracts-语料内容目录字段`, `contracts-cli` (drop `category`); `docs/dev/SPEC-category-contract.md` → superseded by the new spec (keep file with a supersession note or delete and redirect the 41 referencing docs); `docs/design/02-exchange-engine.md` §2 and `docs/design/03-resource-engine.md` §3–5; `docs/manual/rules.md` sections 品类契约/交换规则/供给与制作边界/检查与发布品类修改/食品来源映射说明; `docs/manual/resources.md` 门店供给/新增商品对象/真实制作后再食用; `docs/VERIFICATION.md` `verification-品类契约特性门禁`; `docs/EXPERIMENT.md` 经济参数/内容与语料绑定/地图与门店供给的实验身份; `docs/INDEX.md:340,352` module table rows; `docs/design/worldmock-design.html` regenerated via `scripts/build_design_doc.py` (project rule: compiled design book follows sources). Archive-only: `docs/dev/category-contract-*.md`, `docs/dev/PROPOSAL-category-contract.md` (historical evidence, keep).

---

## 6. Conflicts with frozen clauses

| Document | Anchor / heading | Gist of the frozen clause the proposal contradicts |
| --- | --- | --- |
| `docs/dev/SPEC-category-contract.md` | `<a name="spec-category-contract-冻结产物与品类树"></a>` | Dataset provides `category-contract.json` + `exchange-rules.json`; manifest binds both SHA-256; single-root tree with product/service/contract subtrees. Proposal: no dataset tree or rule file; fixed enums. |
| same | `<a name="spec-category-contract-属性槽位与对象增量"></a>` | JSON-Schema slots, absolute/relative/proportional deltas, `objects.jsonl` compiled into `ExchangeObject` + typed edges. Proposal: objects carry only id/name/type/number/bindings; no compile. |
| same | `<a name="spec-category-contract-来源分类与审核证据"></a>` | Every static number covered exactly once by a `SourceRecord` JSON Pointer; review records for publication. Proposal: only `origin: real/synthetic` marking; no rule publication to review. |
| same | `<a name="spec-category-contract-配对解析与可触达检查"></a>` | Directed three-state pairings, most-specific resolution, reachable-closure admission. Proposal: type→behaviour direct map, nothing to resolve. |
| same | `<a name="spec-category-contract-闭集算子与真实结算"></a>` | Eight dataset-selected operators with coefficients; `food_energy_source` reads object `energy_slot/duration_slot`; `work_hourly/piece/package`; `purchase_price_fen_min/max` + quote jitter formula; per-line `evaluate_purchase(quote, qty, coefficient)`. Proposal: fixed kcal/time per food category, price on the food, no coefficient, work pay via payroll only. |
| same | `<a name="spec-category-contract-库存转换配方"></a>` | `ingredient` → `prepared-meal` single-input recipe via `recipe_output_bindings`. Proposal recommends deleting cooking (decision row 做饭是否保留); foods are only 零食/饮品/正餐. |
| same | `<a name="spec-category-contract-新品类细化与发布"></a>` | `worldmock category check/refine/publish`, refinement suggestions, touch statistics. Proposal: no refinement/publication commands, single immutable snapshot. |
| same | `<a name="spec-category-contract-运行身份与导出"></a>` | Run manifest records `category_contract_version` + contract/rules/content digests. Proposal: environment content hash + WorldMock rules version. |
| same | `<a name="spec-category-contract-文件修改清单"></a>`, `<a name="spec-category-contract-验收与需求台账"></a>` | File ownership and 15 acceptance rows (tree, deltas, pairings, operators, recipes, refinement, publication). All superseded. |
| `docs/CONTRACTS.md` | `<a name="contracts-品类契约与目录字段"></a>` | Field tables for the four catalog files, operator-specific fields, `recipe_output_bindings`, four identity fields. |
| same | `<a name="contracts-类别供给的构建合同"></a>` | `category-assignment.json`, kind admission pools, richness floors, pairwise restaurant menu difference ≥3, `price_position_percent`, SHA jitter quotes, seeds. Proposal: same-kind identical menus, one price per food, no per-store quotes. |
| same | `<a name="contracts-营业与报价声明"></a>` | `QuoteConfig`/`price_version`, no purchase without quote. Proposal: price lives on the food. |
| same | `<a name="contracts-地点发现与购买身份"></a>` | Supply rows with `purchase_pairing_state/purchase_coefficient/purchase_price_fen`; purchase channels derived from phone config. Proposal: app catalog is the only entry source; no pairing state. |
| same | `<a name="contracts-持久化当下事实"></a>`, `<a name="contracts-供给结算与事件载荷"></a>` | `supply_stock`, `supply_quotes`, `price_change`, `product_release`, `delivery_destination_ids`. Proposal: no store inventory/quotes (recommendation), delivery to every location. |
| same | `<a name="contracts-语料内容目录字段"></a>` | Content catalog fields include `attributes` (营养), `segments`, `assets`, `evaluation_refs`. Proposal: summary + duration only; foods carry no nutrition. |
| same | `<a name="contracts-cli"></a>` | `worldmock category` commands. |
| `docs/design/02-exchange-engine.md` | `<a name="exchange-engine-查询适用规则"></a>` | Resolve category → unique most-specific pairing → operator; undefined/ambiguous rejects. |
| same | `<a name="exchange-engine-门店供给的时态与履约判断"></a>` | `supply_id` purchases, build price range, quote formula, `delivery_destination` and `delivery_route` refusal reasons. |
| same | `<a name="exchange-engine-不同交换的计算依据"></a>` | Eating: "食品属性与适用的食品利用效率"; work: contract/acceptance pay. Proposal: category constants; work has no pay path. |
| same | `<a name="exchange-engine-修改与删除"></a>` | Tombstone retirement and append publication. Proposal: immutable snapshots, old formats rejected. |
| `docs/design/03-resource-engine.md` | `<a name="resource-engine-可计量资源定义"></a>` | `resources.jsonl` with `alert_rules`, `drain_rules` provided by the dataset (conflicts only if the spec moves body parameters to WorldMock — Q3). |
| same | `<a name="resource-engine-交换对象与内容条目"></a>` | `ObjectDeclaration/ExchangeObject`; films need timelines/subtitles, podcasts audio, games TextWorld files, work bodies. Proposal: summary-only contents of five kinds. |
| same | `<a name="resource-engine-地点商品与门店供给"></a>` | Kind pools, per-store assortments differ, `ingredient` chain, quote formula. |
| same | `<a name="resource-engine-文件职责"></a>`, `<a name="resource-engine-构建与版本"></a>` | Catalog file table (`category-contract.json`, `exchange-rules.json`, `objects.jsonl`, `overrides/tombstones.jsonl`, `off-foods.mapping.toml`), `catalog_version.major/minor`, append versions. |
| same | `<a name="resource-engine-准入边界"></a>` | Rows 世界素材 (meals/content metadata/work materials) and 内容可达性 (retrieval partitions). |
| `docs/manual/rules.md` | `<a name="rules-配置入口与生效范围"></a>` | Environment may configure category inheritance, slots, pairings, operator coefficients, `travel-time.toml` multipliers, `resources.jsonl` body parameters. |
| same | `<a name="rules-品类契约"></a>`, `<a name="rules-交换规则"></a>`, `<a name="rules-供给与制作边界"></a>`, `<a name="rules-检查与发布品类修改"></a>`, `<a name="rules-食品来源映射说明"></a>` | Authoring of the dataset rule files, OFF mapping, cooking recipe. |
| `docs/manual/resources.md` | `<a name="resources-完整类别供给与冻结价格"></a>`, `<a name="resources-当前供给字段与发布注意事项"></a>`, `<a name="resources-新增商品对象"></a>`, `<a name="resources-真实制作后再食用"></a>`, `<a name="resources-正餐家庭储备和联系人"></a>` | Per-store assortments/quotes, `delivery_destination_ids`, raw object with `energy_kcal_per_serving`/density, cooking, meals corpus with `energy_source`. |
| `docs/VERIFICATION.md` | `<a name="verification-品类契约特性门禁"></a>` | Forbids reducing the category-contract acceptance scope ("不得以原始对象格式兼容…缩减验收范围"). |
| same | `<a name="verification-地图与门店供给"></a>` | Rows 品类与供给组合 (quote into pairing, coefficient), 自提与配送 (delivery range/route), 状态与来源 (quotes/stock restored). |
| `docs/EXPERIMENT.md` | `<a name="experiment-环境发布与运行身份"></a>` | "身体、交通、品类及具体商品系数仍由环境绑定". |
| same | `<a name="experiment-经济参数"></a>` | Package contract 60000 fen per accepted package; travel fare from `exchange-rules.json`. |
| same | `<a name="experiment-内容与语料绑定"></a>` | Open Food Facts foods, Sintel SRT, TextWorld, AMI. |
| same | `<a name="experiment-地图与门店供给的实验身份"></a>` | Catalog 5.x, binding 3, `category-assignment.json` hash, quote formula, seeds per scenario. |
| `docs/dev/SPEC-life-realism.md` | `<a name="spec-life-realism-世界素材品类要求"></a>` | ≥30 meals with 中国食物成分表/USDA sources, content `genre/language/age_rating` partition keys, 20 AMI work materials, `drama` corpus replacement. |
| same | `<a name="spec-life-realism-素材可达性"></a>` | Every content item must belong to a reachable retrieval partition (implemented by `admission._check_partition_*` and `scripts/check_reachability.py`). Proposal: uniform random pick from app binding, no partitions. |
| `docs/dev/SPEC-map-and-storefront.md` | `<a name="spec-map-and-storefront-按地点类别生成完整供给"></a>`, `<a name="spec-map-and-storefront-冻结价格范围与门店差价"></a>`, `<a name="tool-interaction-地点供给和价格-类别准入与逐店商品集合"></a>`, `<a name="tool-interaction-地点供给和价格-范围内定价"></a>` | "类别准入池不是每店菜单", pairwise menu differences, SHA jitter pricing, convenience ≥ supermarket + 10 points. |
| `docs/dev/SPEC-environment-data-ownership.md` | heading 「职责与接口」 row 通用规则归属 | Only activity closed set, motivation parameters and persona mapping are engine-owned; categories/rules remain dataset-owned (the proposal widens engine ownership). |
| `docs/design/05-recording-engine.md` | lines 577-578 under `<a name="recording-engine-检查点创建"></a>` context ("运行清单冻结 `category_contract_version`…") | Run manifest freezes the four category identity fields. |

---

## 7. Blast radius

### 7.1 Tests outside this subsystem that break

- Every test that builds a catalog through `tests/categories/conftest.py::write_catalog` or `tests/catalog/admission_fixtures.py` (used by `tests/life/*`, `tests/integration/*`, `tests/footprints/test_validation.py`, `tests/profile/*` via `coverage_targets`). The artificial environment `tests/fixtures/environment/**` must be re-authored in the new format (catalog, corpora manifest, three scenarios, README); `tests/data_paths.py` roots stay.
- `tests/life/test_semantics.py::test_energy_thresholds_come_from_catalog_alert_rules` and `tests/life/test_shared_rules.py::test_contract_templates_match_scenario_loading_and_resource_mapping` (contract templates vanish), `tests/life/test_travel_time.py` (travel-time.toml), `tests/life/test_work_review.py`, `tests/life/test_tool_interaction_recipe.py`, `tests/life/test_storefront_*`, `tests/life/test_purchase_channels.py`, `tests/integration/test_life_agent_protocol.py::test_life_tool_schema_fingerprint_pins_registry_digest` (affordances bytes change → fingerprint changes), `tests/integration/test_category_identity.py`, `tests/footprints/*` reading `category_matches` or identity fields, `tests/profile/test_mapper.py` (attention keys), `tests/corpora/test_storefront_release.py` (deleted with storefront.py).
- Acceptance ledgers: `tests/tool_interaction_acceptance.py` (BEHAVIOR rows 类别供给/地点定价/购买与食用/料理, seven MUTATIONS listed in §4.2) + `tests/fixtures/mutation-symbol-baseline.json` + `tests/fixtures/acceptance_requirements.json["tool-interaction"]` + `tests/test_tool_interaction_acceptance.py:19` frozen file list; `tests/shared_rules_acceptance.py` rows '原子写', '品类与目录错误', '合同对象集合'; `tests/time_and_energy_acceptance.py` `_SHADOW` rows (content-kind conditional claims); `tests/test_single_source.py::OWNERS` (four entries); `tests/test_import_graph.py` exemptions; `tests/dayrun_fidelity_acceptance.py` and `tests/errands_workday_acceptance.py` nodes that run the artificial environment end-to-end.
- Day-run baselines pinned to current exports (`tests/fixtures/dayrun-layering-baseline.json`, `dayrun-shared-rules-baseline.json`, `tests/resources/fixtures/anchor-money.json` stays valid only if the money event shape is unchanged — it is).

### 7.2 Production modules outside scope that depend on deleted symbols

`life/semantics.py` (assembly), `life/basket.py` (`require_runtime_rule`, `energy_kcal_per_unit`), `life/session_actions.py`, `life/tool_layer.py`, `life/storefront_runtime.py`, `life/order_routing.py`, `life/action_evidence.py` (`category_matches`), `life/inventory_recipe.py`, `life/work.py` + `work_review*.py` (contract pay), `life/travel_time.py` (`travel_time_rules_bytes`), `life/map_data.py` (`category_assignment`, `random_seed`), `life/map_models.py` (`SupplyConfig`, `QuoteConfig`, `CategoryAssignment`), `life/scenario.py` (`ProductConfig`, `catalog_version`, `recipe_output_bindings`, `_check_catalog_references`), `life/world_setup.py` (`load_catalog`, byte-identical rule copies), `life/run_identity.py`, `life/callable_index.py:156` and `life/world_query.py:154-155` (category names for `query_world.category`), `life/retrieval.py` (partition entries), `profile/coverage_targets.py:41` (`obj.object_kind == "product"`), `profile/coverage_apps.py` (phone-config channels → app catalog), `validation.py`, `run_setup.py`, `runtime.py:261`, `cli.py`, `footprints/{models,quality}.py` (manifest identity fields, `actual_model_has_no_frozen_price`), `delivery/` export of `purchases.jsonl` columns (`supply_id`, `price_version`, `channel_id`), `custom/agents/life_agent.py` (`AFFORDANCES_FILE` digest, callable index labels).

### 7.3 Cross-subsystem dependencies the spec must coordinate

1. **Map subsystem** owns `locations.json` row shape and `map.json`; this subsystem needs `LocationKind` and `app_ids` on food stores and the shortest-path length for delivery ETA. `travel-time.toml`, `SupplyConfig`, `QuoteConfig`, `category-assignment.json`, `supplies.json`, `supply_stock/supply_quotes`, `price_change/product_release` events are map/storefront deletions triggered by the catalog format change.
2. **Entertainment/app subsystem** consumes `contents_by_app`, `stores_by_app`, `ContentKind`, `PUSH_WINDOW_LOCAL`; `affordances.toml` content kinds and `watch` target discovery change here but their candidate generator and push state live there.
3. **Work subsystem**: `work_topics.json` + `target_hours` replace contract objects/slots; `evaluate_*_pay` deletion implies work pay only via `scheduling` payroll (`scheduling/compile.py`, `effects.py`), `work_review*` deleted.
4. **Body/food settlement** (`life/body.py::apply_food`, `basket.py`): kcal source becomes `FOOD_CONSTANTS`; efficiency curve and energy cap remain (proposal: "固定热量不能默默取消利用效率和上限"); where the curve's numbers live depends on Q3.
5. **Scenario format** bump (schema 6 → 7) and **world state** bump (9 → 10 if `supply_*` fields are dropped and push state is added); checkpoints/exports/handoff reject old versions; `labelkit` input validation of phone sources (dataset-evidence §app) updated.
6. **Recording/export**: run manifest, checkpoint manifest, `frozen_run_manifest` field rename; `category_matches` removal from `action_result` and from quality statistics; `purchases.jsonl` columns.
7. **Dataset repo**: new builders for the five files, deletion of `environment_prepare/catalog.py` rule copying, OFF/meal/AMI/Sintel/TextWorld preparation; the artificial test environment in WorldMock is the only in-repo fixture and must be hand-rewritten first (proposal stage 最小生活闭环).
8. **Documentation gate**: 41 documents link `SPEC-category-contract.md`; anchors in §6 must be rewritten in place; `docs/design/worldmock-design.html` rebuilt.

---

## 8. Open questions the spec must decide

1. **Package layout**: delete `worldmock.categories` and add `worldmock.exchange` (fixed types, constants, operators), or gut `categories` in place keeping its import path? Affects `OWNERS` in `tests/test_single_source.py` and 30+ import sites. Recommendation: new `worldmock.exchange`, move `CategoryError/DeepFrozenModel/Conservation/require_finite_number` there (rename `CategoryError` → keep name for stable codes).
2. **Location kinds closed set**: proposal "建议餐馆、咖啡店、便利店、超市分别作为内置食品店铺类型 … 住宅、工作地点、公园不出售食品，其他地点沿用各自系统固定服务". Do `fitness`, `wet_market`, `merchant`, `venue` survive? Which kinds does `all` expand to (FOOD_STORE_KINDS)? Must align with map spec and `location_kinds` activity environments.
3. **Where body parameters live** (`resources.jsonl`: `energy_kcal.bounds/alert_rules/drain_rules` incl. MET table, sleep/entertainment rates, `food_efficiency_curve`; contract-category `work_efficiency_factors`/`work_notes_multiplier`). Proposal table assigns "资源单位与交换公式" and "娱乐效果" to WorldMock and keeps "人物画像、初始资金、作息、时区" as scenario inputs, but does not name basal/MET/thresholds. Decide: WorldMock constants (recommended; removes the last dataset numbers file and the env byte-copy of rule TOMLs becomes unnecessary) or a trimmed `resources.jsonl` with `origin` marks.
4. **Food constants** — decision row 「食品常数」: adopt 200/100/600 kcal and 5/3/20 min, or recalibrate; also whether `price_fen` is per food (proposal: "商品价格定义在商品上，不设店铺覆盖价格") — confirmed, but should drinks at a cafe and a convenience store share one price? Under identical-menu-per-kind they must, unless the spec allows the same food to carry different prices by kind (it does not today).
5. **Explicit binding vs by-kind binding** — decision row 「同类店铺的食品绑定」: `store_ids: [...]` with `FOOD_BINDING_KIND_INCONSISTENT` rejection (proposal's recommended option) or `store_kinds: [...]` (changes the dataset authoring shape). The admission rule in §5.2 implements the recommended option.
6. **Cooking** — decision row 「做饭是否保留」: deleting removes `inventory_transform`, `scale_inventory_recipe`, `ConvertEntry` use, `inventory/item` resource, `recipe_output_bindings`, `ingredient`; keeping as a timed lifestyle activity means no food object is produced and no ledger convert — spec must say so explicitly ("不能只删 ingredient 分类而留下制作动作及空配方入口").
7. **Inventory/opening** — decision row 「营业与供给」: no stock and all-day service (then `supply_stock`, `product_release`, opening calendars for food stores all go) vs. fixed opening hours per kind (then `OpeningCalendar` stays as a WorldMock constant table, not dataset data).
8. **Delivery ETA** — decision row 「外卖时间」: prep + graph distance / fixed speed (needs `map.json` shortest path in this subsystem's constants) vs. fixed ETA.
9. **Provenance marking**: minimal `origin: real|synthetic` (+ optional `source` string) on summaries/prices/durations as proposed ("真实摘要保留来源信息，合成摘要明确标记合成；价格、时长和地图长度标记为仿真数值"), replacing `SourceRecord`/JSON-Pointer coverage and `ReviewRecord`. Is a review record still required for environment publication, or is the environment id + hash + git evidence enough?
10. **Manifest unification**: one environment manifest (corpora + catalog files, `CorpusEntry` shape) vs. keeping `catalog/manifest.json` separate with the framed digest. Recommendation: one manifest; `compute_catalog_identity` becomes a thin wrapper over `load_manifest`.
11. **Merchant accounts**: one ledger account per food-store location (`merchant:<location_id>`), one per app, or one global merchant? Affects `accounts[]` in scenarios, `TransferEntry.to_owner`, `audit_closed_domain` expectations, `anchor-money.json`-style fixtures.
12. **`category_matches` payload**: delete outright (recommended) or replace with `{kind, action, rules_version}` for footprint auditing. `maintenance.category_touches` consumers disappear either way.
13. **World-material floors** (`CategoryRequirements`): keep the remaining non-food checks (locations per kind, pantry, office affordances, lifestyle fields, contacts) as a dataset-declared block or move the floors into WorldMock constants (consistent with "分类规则 … 钉死")?
14. **Delivery fee / travel fare constants**: EXPERIMENT 经济参数 currently cites driving min 500 fen + 0.2 fen/m from the dataset rule file; the spec must restate these as WorldMock constants with `RULES_VERSION` and decide the walking fare (0).
15. **Scenario pin**: `catalog_version{major,minor}` → `environment_id` + `catalog_content_sha256` (recommended) or keep an integer version pair; also whether `rules_version` is pinned in the scenario or only recorded in the run manifest.
