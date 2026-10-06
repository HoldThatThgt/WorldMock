# Subsystem map: Food energy, eating settlement, body energy, cooking and recipes

Worktree read: `D:/code/WorldMock/.claude/worktrees/rrs` (HEAD `6df927d`, proposal baseline `7e1ffab`). Read-only; nothing modified.
Proposal: `docs/dev/PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md`; evidence `docs/dev/research/resource-rule-simplification/{engine,dataset,industry}-evidence.md`.

Scope boundary used here: everything from "a food object exists in the catalog" through "energy lands in `BodyState.energy_kcal`", plus the body-energy drain/boundary machinery that consumes the same `BodyConfig`, plus the cooking (inventory-recipe) chain. Purchase/delivery (`basket.purchase_order`, storefront) and content/app are other subsystems; they are listed only where they share a file or a fixture.

---

## 1. Inventory

### 1.1 Production files in scope

| File | Lines | Responsibility (one line) |
| --- | --- | --- |
| `src/worldmock/life/body.py` | 658 | Per-interval body settlement (energy drain as `ResourceLedger` flows, two-process sleep via `sleep.py`, entertainment need), `apply_food` (intake × sleep-deficit utilization, capped at `maximum_energy_kcal`), motivation-only energy cost helpers, next-boundary prediction (800/400/100 kcal thresholds, hunger/sleepiness band scan, meal-window edges). |
| `src/worldmock/life/basket.py` | 396 | Lines 1–250: whole-order purchase + delivery planning (storefront subsystem). Lines 252–396: eating settlement — portion/duration derivation, `opened` bookkeeping, meal validation, `meal_food_rate`, progress-proportional intake via `apply_food`. |
| `src/worldmock/life/semantics.py` | 796 | Assembles `BodyConfig` from catalog `resources.jsonl` (energy `drain_rules`/`alert_rules`/`food_efficiency_curve`, contract `work_efficiency_factors`); compiles `ProductConfig`/`InventoryItemConfig` from `ExchangeObject` (energy from the compiled `consume` `SourceEdge`, serving, use duration); collects recipe-output objects. |
| `src/worldmock/life/scenario.py` | 1119 | Pydantic shapes: `BodyConfig` (threshold ordering validator), `InventoryItemConfig`/`ProductConfig` (food ⇔ serving fields, `energy_kcal_per_unit` only for `eat`), `BodyInstanceConfig`, `AgentConfig.initial_inventory/lunch_window/dinner_window`, `LifeScenario.recipe_output_bindings` + `_validate_recipe_bindings`, `RuntimeScenario.inventory_items/recipe_output_bindings`. |
| `src/worldmock/life/inventory_recipe.py` | 234 | Cooking: single-input→single-output inventory transform; plan at activity start (`prepare_inventory_recipe`), atomic ledger `ConvertEntry` on completion (`apply_inventory_recipe`), unit/category rechecks, payload persistence in `activity.data["inventory_recipe"]`. |
| `src/worldmock/life/felt.py` | 399 | Pure felt-state library: appetite formula (anchor 1200, floor 400, starving 100, circadian 0.17 @20h, meal-window +0.15), `MealWindows`, band tables, social ledger (incl. `SOCIAL_NEED_SHARED_MEAL_RATE`), mood pulses, hysteresis. |
| `src/worldmock/life/activities.py` | 440 | Activity lifecycle; `activity_factors` builds `ActivityFactors` incl. `_food_energy_rate` (reads `activity.data["food_energy_kcal_per_hour"]`), MET lookup, calls `settle_body`. |
| `src/worldmock/categories/rules.py` | 298 | Closed exchange-rule models: `RuleOperator` (incl. `FOOD_ENERGY_SOURCE`, `INVENTORY_TRANSFORM`, `DURABLE_USE`), `FoodEnergyRule(energy_slot, duration_slot)`, `InventoryTransformRule`, `ExchangeRulesDocument`, `build_exchange_rules`, `validate_rule_resource_domains` (food → `energy_kcal`/level/open/kcal). |
| `src/worldmock/life/sleep.py` | 244 | `FOOD_UTILIZATION_CURVE` (0→0.90, 8→0.90, 16→0.85, 24→0.80 on `sleep_deficit_hours`), `food_utilization_efficiency`. The catalog `food_efficiency_curve` is loaded but NOT used by `apply_food` (manual/rules.md says so explicitly). |
| `src/worldmock/life/activity_evidence.py` | 122 | `is_eating`, `discard_opened` (conclusion writes `discarded`), `conclusion_outcome` (eaten/discarded/remaining or consumed/produced for recipes), `meal_consumption_data`. |
| `src/worldmock/life/category_contract.py` | 242 | `require_item_rules` (person→product-category pairing for `consume:eat|use`), `admit_reachable_categories` (start-up closure incl. recipe outputs), `_validate_recipe_bindings`, `_recipe_output`, `_validate_recipe_output`. |
| `src/worldmock/categories/operators.py` | 166 | `evaluate_food_energy(energy, coefficient)` (Decimal exact), `scale_inventory_recipe` → `InventoryDelta`. |
| `src/worldmock/catalog/compiler.py` | 255 | `_edge_for_rule`: `FoodEnergyRule` → `SourceEdge(on="consume", amount=evaluate_food_energy(obj[energy_slot], coefficient), duration_s=obj[duration_slot])`; `_effective_duration`; `_validate_dynamic_rule_attributes` (recipe input category/unit). |
| `src/worldmock/catalog/models.py` | 431 | `DrainRules` (`basal_kcal_per_hour`, `activity_met`, `food_efficiency_curve`, `awake_sleep_debt_per_hour`, `sleep_recovery_per_hour`, `entertainment_*`), `AlertRule`, `SourceEdge`, `Serving`, `ExchangeObject` (serving/use_modes/consumable/effect/use_duration_seconds), `MealRequirements`, `PantryRequirements`, `CategoryRequirements.meals/home_pantry`. |
| `src/worldmock/catalog/admission.py` | 968 | `_check_meals`/`_check_meal_entry`: meals corpus must carry `energy_source{database,source_url,retrieved_at,unit_conversion,energy_kcal_per_100g}`, price/duration windows, dine-in/delivery, consumable catalog object; home-pantry `breakfast_servings_min` check. |
| `src/worldmock/life/session_actions.py` | 1719 | `ActionHandlers._consume` (lines 1352–1399): rule match, `validate_meal_use`, `meal_duration`, recipe plan or `validate_meal_inventory`, `open_meal`, writes `activity.data{request, consumed, food_energy_kcal_per_hour, inventory_recipe?, opened?}`; conclusion path calls `discard_opened`. |
| `src/worldmock/life/time_advance.py` | 507 | `_apply_consume_effect` (lines 371–392): `apply_inventory_recipe` on completion or `apply_meal_progress` each settlement; records `consumption.food_efficiency`; shared body settlement + `BoundaryClock` with `meal_windows_by_agent`. |
| `src/worldmock/life/tool_layer.py` | 543 | `_consume_check` (portion/duration/mode/recipe/held), consume facts (`meal_portion` → `quantity`/`duration`), `_product_state` (food → `food_energy_label`). |
| `src/worldmock/life/motivation.py` | 1183 | Internal scorer only: `MEAL_KCAL_FLOOR=300`, `MEAL_CANDIDATE_COUNT=3`, `meal_repeat_weight`, `_feasible_food_rows`, `_food_supply_row`, `_eat_candidates`, `_eat_candidate`, continuation energy delta from `food_energy_kcal_per_hour`. |
| `src/worldmock/life/projection.py` | 455 | `food_energy_label` (round to 10 kcal, "几乎不含热量" < 5), `body_projection`/`felt_values` (uses `felt.compute_appetite` + `EnergyReading`), `route_effort`. |
| `src/worldmock/life/rhythm.py` | 795 | Hunger bands order/`HUNGRY_BANDS` (incl. 饿得发慌), discomfort pulse billing, `meal_windows_by_agent` input for hysteresis reference. |
| `src/worldmock/life/observe.py` | 1107 | Builds `felt.MealWindows` for projection; detects held recipe inputs via `is_inventory_recipe` (way 「制作」). |
| `src/worldmock/life/world_ops.py` | 148 | `_body_settlement_data` (persists `food_energy_kcal`, `food_efficiency`), `meal_windows_by_agent` (public API, see layering ledger). |
| `src/worldmock/life/discovery.py` | 118 | Supply rows expose `energy_kcal_per_unit`, `use_duration_seconds`. |
| `src/worldmock/life/commerce.py` | 51 | `held_quantity`, `add_holding` (nested per-location inventory). |
| `src/worldmock/life/readable.py` | 101 | `portion_phrase` ("0.25 份"). |
| `src/worldmock/footprints/quality.py` | 1001 | `_food_efficiency_samples` (quality report `efficiency.food`). |
| `src/worldmock/evaluation/behavior_metrics.py` | 841 | `_meal_diversity` (Simpson/24h repeat, `use_mode == "eat"` with positive `interval_quantity`). |
| `src/worldmock/footprints/experiences.py`, `src/worldmock/life/errand_state.py`, `src/worldmock/evaluation/errand_metrics.py`, `src/worldmock/evaluation/public_evidence.py` | — | Downstream readers of eating facts: `consumed` verified experience, errand outcome `吃掉` (`OUTCOME_CODES`), eat/use classification. |
| `custom/agents/life_memory.py` (line 276), `scripts/inspect_run.py` (lines 39, 152–318) | — | Memory fact line for `consumed`; run inspector imports `APPETITE_BANDS` etc. and computes `lowest_energy_before_meal`. |

### 1.2 Key symbols (path::Name)

Body energy
- `src/worldmock/life/body.py::ActivityFactors` (`met`, `entertainment_relief_per_hour`, `work_skill_factor`, `work_tool_factor`, `food_energy_kcal_per_hour`)
- `src/worldmock/life/body.py::BodySettlement` (`before`, `after`, `duration_seconds`, `basal_energy_kcal`, `extra_energy_kcal`, `food_energy_kcal`, `food_efficiency`, `work_efficiency`)
- `src/worldmock/life/body.py::settle_body`, `::settle_shared_body`, `::_shared_entries`, `::_energy_cost_entry`, `::_settle_via_ledger`, `::body_resource_metas`, `::last_flows`
- `src/worldmock/life/body.py::apply_food` — `gained = calories × sleep.food_utilization_efficiency(deficit)`, capped to `maximum_energy_kcal`, flow cause `"food:intake"`
- `src/worldmock/life/body.py::minimum_energy_cost`, `::maximum_energy_duration_seconds`, `::ENERGY_RECOVERY_ACTIVITY_KINDS` (motivation only)
- `src/worldmock/life/body.py::next_body_boundary_seconds`, `::next_shared_body_boundary_seconds`, `::_net_burn_per_hour`, `::_energy_boundaries`, `::_deficit_curve_points`, `::_FeltProjection`, `::_felt_band_boundaries`, `::_first_band_change`, `::_meal_window_boundaries`, `::BoundaryClock`
- `src/worldmock/life/body.py::_work_efficiency`, `::_factor_snapshot`, `::_curve_average`, `::_resource_value`, `::initial_body`
- `src/worldmock/life/sleep.py::FOOD_UTILIZATION_CURVE`, `::food_utilization_efficiency`
- `src/worldmock/life/models.py::BodyState`, `::AgentState.inventory/active_effects/body_settled_at`, `::ConsumeItem`, `::ConsumeRequest` (`items[1..]`, `use_mode: "eat"|"use"`, `duration_seconds 60..CONSUME_DURATION_CAP_SECONDS=14400`), `::ActivityKind.CONSUME`

Hunger perception
- `src/worldmock/life/felt.py::APPETITE_ANCHOR_KCAL_DEFAULT`, `::APPETITE_KCAL_FLOOR`, `::APPETITE_STARVING_KCAL`, `::APPETITE_BANDS`, `::MealWindows`, `::compute_appetite`, `::appetite_level`, `::EnergyReading`, `::summarize_felt_levels`, `::SOCIAL_NEED_SHARED_MEAL_RATE`
- `src/worldmock/life/projection.py::food_energy_label`, `::FOOD_ENERGY_NEGLIGIBLE_KCAL`, `::FOOD_ENERGY_ROUNDING_KCAL`, `::body_projection`, `::felt_values`
- `src/worldmock/life/world_ops.py::meal_windows_by_agent`

Eating settlement
- `src/worldmock/life/basket.py::portion_duration`, `::meal_portion`, `::open_meal`, `::meal_duration`, `::validate_meal_use`, `::validate_meal_inventory`, `::meal_food_rate`, `::apply_meal_progress`, `::_meal_target`, `::_apply_item_delta`, `::change_inventory`, `::held_quantity`
- `src/worldmock/life/session_actions.py::ActionHandlers._consume`
- `src/worldmock/life/time_advance.py::TimeAdvanceCoordinator._apply_consume_effect` (lines 371–392)
- `src/worldmock/life/activities.py::activity_factors`, `::_food_energy_rate`, `::advance_activity`, `::advance_activity_shared`
- `src/worldmock/life/activity_evidence.py::is_eating`, `::discard_opened`, `::conclusion_outcome`, `::_item_change_outcome`, `::meal_consumption_data`
- `src/worldmock/life/tool_layer.py::_consume_check`, `::_product_state`
- `src/worldmock/life/motivation.py::MEAL_KCAL_FLOOR`, `::_feasible_food_rows`, `::_eat_candidates`, `::_eat_candidate`

Food definition / catalog→runtime
- `src/worldmock/life/scenario.py::BodyConfig`, `::BodyInstanceConfig`, `::InventoryItemConfig`, `::ProductConfig`, `::_PRODUCT_USE_MODES`, `::LifeScenario.recipe_output_bindings`, `::RuntimeScenario.inventory_items`
- `src/worldmock/life/semantics.py::ENERGY_RESOURCE`, `::ALERT_THRESHOLDS`, `::_assemble_body`, `::_assemble_items`, `::_inventory_values`, `::_inventory_payload`, `::_inventory_object_ids`, `::_consume_edge`, `::_alert_thresholds`, `::_drain_rules`, `::RuntimeItems`
- `src/worldmock/catalog/models.py::DrainRules`, `::AlertRule`, `::SourceEdge`, `::Serving`, `::ExchangeObject`, `::MealRequirements`, `::PantryRequirements`, `::CategoryRequirements`
- `src/worldmock/catalog/compiler.py::_edge_for_rule`, `::_effective_duration`, `::_validate_dynamic_rule_attributes`
- `src/worldmock/catalog/admission.py::_check_meals`, `::_check_meal_entry` (+ pantry check)
- `src/worldmock/categories/rules.py::RuleOperator`, `::FoodEnergyRule`, `::DurableUseRule`, `::InventoryTransformRule`, `::ExchangeRule`, `::ExchangeRulesDocument`, `::ExchangeRuleSet`, `::build_exchange_rules`, `::validate_rule_resource_domains`, `::_operator_resource_domain`, `::_validate_operator_action`
- `src/worldmock/categories/operators.py::evaluate_food_energy`, `::scale_inventory_recipe`, `::InventoryDelta`
- `src/worldmock/life/category_contract.py::require_item_rules`, `::require_runtime_rule`, `::admit_reachable_categories`, `::_consume_actions`

Cooking
- `src/worldmock/life/inventory_recipe.py::RecipeContext`, `::InventoryRecipePlan`, `::is_inventory_recipe`, `::prepare_inventory_recipe`, `::apply_inventory_recipe`, `::_apply_inventory_ledger`, `::_inventory_amounts`, `::_inventory_owner`, `::_inventory_from_balances`, `::_validate_categories`
- `src/worldmock/life/category_contract.py::_validate_recipe_bindings`, `::_recipe_output`, `::_validate_recipe_output`
- `src/worldmock/life/observe.py` (lines 215–227) recipe-holding detection → way 「制作」 (`obtain`)

### 1.3 Who imports these modules (rg over src/, custom/, scripts/, tests/)

`worldmock.life.body`: `life/activities.py`, `life/basket.py`, `life/motivation.py`, `life/social_actions.py`, `life/time_advance.py`, `catalog/affordances` consumer tests; tests: `tests/catalog/test_affordance_shadow.py`, `tests/life/test_activity_effects.py`, `test_body.py`, `test_body_flows.py`, `test_energy_feasibility.py`, `test_engine_branch_coverage.py`, `test_relief_floor.py`, `test_shared_body.py`, `test_sleep_two_process.py`, `test_wait_passive_entertainment.py`, `test_work_efficiency_factors.py`, `test_dayrun_inventory_regressions.py`, `test_motivation.py`, `tests/time_and_energy_acceptance.py`.

`worldmock.life.basket`: `life/session_actions.py`, `life/time_advance.py`, `life/tool_layer.py`, `life/activity_evidence.py`, `life/order_routing.py`, `life/purchase_offers.py`, `life/storefront_runtime.py`, `life/callable_index.py`, `life/observe.py`; tests: `tests/corpora/test_storefront_release.py`, `tests/scheduling/test_branch_coverage.py`, `tests/life/test_callable_index.py`, `test_commerce.py`, `test_meal_outcome.py`, `test_dayrun_inventory_regressions.py`, `test_domain_branch_coverage.py`, `test_storefront_*`, `test_named_purchase.py`, `test_purchase_*`, `test_wc_named_purchase_replay.py`, `tests/dayrun_fidelity_acceptance.py` (mutation file list), `tests/persona_prompt_acceptance.py` (mutation file list).

`worldmock.life.felt`: `life/body.py`, `life/activities.py`, `life/observe.py`, `life/projection.py`, `life/rhythm.py`, `life/world_ops.py`, `life/engine.py` (type), `scripts/inspect_run.py`; tests: `tests/life/test_felt_states.py`, `test_sleep_two_process.py`, `test_discomfort_and_sleep_messages.py`, `test_wake_reasons.py`, `tests/integration/test_seven_layer_request.py`, `tests/dayrun_fidelity_acceptance.py`.

`worldmock.life.inventory_recipe`: `life/session_actions.py`, `life/time_advance.py`, `life/tool_layer.py`, `life/observe.py`; tests: `tests/life/test_category_runtime_edges.py`, `test_category_runtime.py`, `test_tool_interaction_recipe.py`, `tests/integration/test_category_inventory_protocol.py`, `tests/tool_interaction_acceptance.py` (MUTATION rows name `worldmock.life.inventory_recipe::_inventory_from_balances`).

`worldmock.categories.rules`: `categories/__init__.py`, `categories/loader.py`, `categories/operators.py`, `categories/maintenance.py`, `catalog/compiler.py`, `life/category_contract.py`, `life/inventory_recipe.py` (via package); tests: `tests/categories/test_rules.py`, `test_catalog_integration.py`, `tests/catalog/test_storefront_admission.py`, `tests/life/test_category_runtime*.py`.

`worldmock.life.semantics` / `scenario` (BodyConfig/InventoryItemConfig): nearly every `life/*` module, `custom/envs/life_env.py`, `scripts/inspect_run.py`, `tests/frozen_inputs.py::clone_semantics` (deep-copies `body`), `tests/engine_fixture.py`, 8 test-local `body_config()` fixtures (`tests/life/test_activity_effects.py`, `test_body.py`, `test_body_flows.py`, `test_engine_branch_coverage.py`, `test_relief_floor.py`, `test_sleep_two_process.py`, `test_wait_passive_entertainment.py`, `test_work_efficiency_factors.py`).

No `custom/` or `scripts/` file imports `body`, `basket`, `inventory_recipe` or `categories.rules` directly; `scripts/inspect_run.py` imports `felt`.

---

## 2. Current contracts (by anchor)

### docs/CONTRACTS.md
- `<a name="contracts-资源与需求字段">` — `available_energy` kcal non-negative, settled at 0 floor; `sleep_deficit` independent of food.
- `<a name="contracts-个人事务">` — errand outcome 「吃掉」 counts only the agent's own eating `activity_progress` interval quantity; 「取得」 includes recipe output entering inventory.
- `<a name="contracts-request-settlement">` — `start_activity` merging: two held foods with `to=null` form one meal; 「制作」 never merges (one recipe per request); 「使用」 single item.
- `<a name="contracts-活动受理拒绝码">` — `ACTIVITY_DURATION_INVALID` for meals outside 60–14400 s (portion-scaled), `PRODUCT_NOT_HELD` semantics (zero consumable quantity; sub-portion accepted).
- `<a name="contracts-closed-request-models">` — way closed set incl. 「食用」「使用」「制作」「给予」; `consume` `from/to` null for eat/use; 做饭→制作, 吃→食用 mapping table.
- `<a name="contracts-action-results">` — conclusion `outcome` items (eaten/discarded/remaining; recipe consumed/produced).
- `<a name="contracts-领域枚举">` — activity `data.opened: dict[str, float]`; world schema version 8.
- `<a name="contracts-seven-layers">` — tool-layer product `state` writes 「每份约 N 千卡」; eating activity `state` 「已吃 X 份，剩 Y 份」; `actions[].quantity` may be fractional portions.
- `<a name="contracts-体感与动机证据">` — `body.values` keys incl. `energy_kcal`, `sleep_deficit_hours`; `levels` only to the prompt.
- `<a name="contracts-private-fields">` — event-level `inventory_items` rows (`product_id,name,use_modes,use_duration_seconds,required_location_id`); `consume.items[].quantity` is request portions, recipe input scaled by rule × coefficient; opened-portion eating rules; body numbers banned from prompt.
- `<a name="contracts-品类契约与目录字段">` — operators `food_energy_source(energy_slot,duration_slot)`, `inventory_transform(...)`; `recipe_output_bindings` map; `INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE`; contract/rules version 3, scenario schema 6.
- `<a name="contracts-语料内容目录字段">` — corpus `attributes` carries nutrition.
- `<a name="contracts-人物卡片覆盖率产出">` — `food_preference` coverage targets = meal corpus names + edible catalog object names.

### docs/dev/SPEC-life-runtime.md
- `<a name="spec-life-runtime-睡眠不足账与效率曲线">` — food utilization curve nodes (0,0.90)(8,0.90)(16,0.85)(24,0.80) on `sleep_deficit_hours`.
- `<a name="spec-life-runtime-其他体感分级">` — appetite formula, 1200/400 anchors, bands, meal-window +0.15, emergency floor.
- `<a name="spec-life-runtime-资源表达">` — energy in kcal, sleep deficit independent of food.
- `<a name="spec-life-runtime-转换效率算法">` — energy change = intake × utilization − basal × t − activity extra, max(0,…); parameter management (body/activity params centralised in scenario catalog with unit/source/range).
- `<a name="spec-life-runtime-具体活动结算">` — 进食 row: must hold food; `opened = min(1, held)`; intake only by `opened` × progress; pause keeps, conclude discards without intake; sub-portion duration `max(60, round(full × portion))`; 购物 row: food leaves stock at open.
- `<a name="spec-life-runtime-来源与准入">` — Open Food Facts as food source, Open Prices as price source.
- `<a name="spec-life-runtime-身体参数与单位">` — 65 kg, 1600/2400 kcal, basal 65 kcal/h, MET extra, food utilization nodes, energy efficiency nodes (0,0.5)(399.9,0.5)(400,0.8)(800,1.0).
- `<a name="spec-life-runtime-随身持有与远程订购">` — holdings are per-location; eat only where held.
- `<a name="time-and-energy-能量感知-饥饿在低能量时不失灵">` — <400 at least 饿得难受, <100 饿得发慌; `BodyConfig.validate_body` ordering; boundary thresholds 800/400/100.
- `<a name="time-and-energy-能量感知-商品热量与路线费力">` — food `state` kcal label from `energy_kcal_per_unit`, rounded to 10; serving comes from catalog serving declaration.
- `<a name="time-and-energy-能量不拦人-原则">`, `-删除的机制`, `-低能量的代价保留不新增惩罚`, `-让人物趁早吃` — energy never gates; hunger band crossings wake; eat paths expressed by tool layer.
- `<a name="persona-prompt-交互语义-进食">` — opened portion, pause/conclude semantics, sub-portion, conclusion outcome.
- `<a name="tool-interaction-料理与习惯">` — cooking = `inventory_recipe` single-input/single-output; `recipe_output_bindings`; `inventory_unit=item`; no multi-ingredient engine; conclusion always reports item changes.
- `<a name="dayrun-fidelity-身体与需要-区间事实只有一个来源社交账与共同用餐">` — shared-meal social relief uses `body_settlement.activity_ids`.
- `<a name="errands-workday-个人事务-完成依据与结算">` — eating/making evidence for errands.
- `<a name="time-and-energy-五类生活动作与并行准入-请求形状与菜单">` — `obtain` way 「制作」 for recipe inputs, `consume` 「食用」/「使用」.

### docs/dev/SPEC-category-contract.md
- `<a name="spec-category-contract-裁决依据">` — 变换配方 row (use action completes one input→one output category).
- `<a name="spec-category-contract-冻结产物与品类树">` — Dataset supplies `category-contract.json` + `exchange-rules.json`; 纯牛奶 under dairy.
- `<a name="spec-category-contract-属性槽位与对象增量">` — food density window is a category slot; objects declare deltas.
- `<a name="spec-category-contract-来源分类与审核证据">` — every static number (incl. recipe quantities) needs a source record.
- `<a name="spec-category-contract-配对解析与可触达检查">` — `consume:eat`/`consume:use` pairings; reachability closure includes recipe outputs.
- `<a name="spec-category-contract-闭集算子与真实结算">` — operator table: 食品能量来源 (category kcal × coefficient → eating duration, stock, body); 库存转换; food duration/effect compiled from rule-named slots.
- `<a name="spec-category-contract-库存转换配方">` — full cooking contract (ingredient pack → one published meal, `recipe_output_bindings`, item units, completion-only, idempotent).
- `<a name="spec-category-contract-验收与需求台账">` — rows 库存转换, 真实结算与系数, 算子及守恒.
- `<a name="spec-category-contract-文件修改清单">` — names `basket.py`, `inventory_recipe.py`, `semantics.py`, `scenario.py` responsibilities.

### docs/dev/SPEC-dayrun-behavior.md
- `<a name="spec-dayrun-behavior-共同时间与身体结算">` — one body settlement per interval; food energy booked once by actual eaten quantity; meal-window edges are boundaries.
- `<a name="spec-dayrun-behavior-餐馆的实际进食路径">` — restaurants bind `prepared-meal` products; per-portion kcal in tool-layer `state`; opened-portion rules; only positive consumption proves eating.

### docs/dev/SPEC-life-realism.md
- `<a name="spec-life-realism-动机层">` — eat candidates ≥300 kcal or closest to gap, top 3, repeat down-weight (internal scorer).
- `<a name="spec-life-realism-世界素材品类要求">` — 家的可供性 (7 breakfast servings in pantry), 正餐 (≥30 meals, kcal sourced from 中国食物成分表/USDA FNDDS, price 15–45 元, 20–40 min).
- `<a name="spec-life-realism-商务与进食">` — two held foods = one meal (longest duration, kcal/effects add), opened-portion rules, 1e-9 precision.

### docs/dev/SPEC-map-and-storefront.md (adjacent, for coordination)
- `<a name="spec-map-and-storefront-按地点类别生成完整供给">` and `<a name="tool-interaction-地点供给和价格-类别准入与逐店商品集合">` — kind→category admission pools (`restaurant: prepared-meal`, `convenience_store: food`, `supermarket/merchant: food, durable, ingredient`, `wet_market: ingredient`), menu-difference requirement.

### docs/dev/SPEC-environment-data-ownership.md
- heading 「数据准备归属」 row — 食品翻译/正式目录生成 owned by Dataset.

### docs/design/*.md
- `02-exchange-engine.md` `<a name="exchange-engine-食品交换的规则调用">` — sequence: eating request → condition → eaten quantity × utilization → resource delta; `<a name="exchange-engine-实现职责与证据入口">` names `basket.py`, `inventory_recipe.py`, `operators.py`.
- `03-resource-engine.md` `<a name="resource-engine-资源内容和语料的关系">` (food corpus → object: serving, kcal, source), `<a name="resource-engine-可计量资源定义">` (`energy_kcal` level resource with bounds/drain_rules/alert_rules), `<a name="resource-engine-交换对象与内容条目">` (food rows: spec, serving, kcal basis), `<a name="resource-engine-地点商品与门店供给">` (ingredient category, vegetable pack, single-recipe cooking), `<a name="resource-engine-文件职责">` (`off-foods.mapping.toml`).
- `04-simulation-engine.md` `<a name="simulation-engine-实际区间身体变化与效率">` — 进食 row; `body_settlement` once per interval; utilization computed by simulation engine.
- `05-recording-engine.md` `<a name="recording-engine-事件正文与资源证据">` (`action_result.data.outcome`, `discarded`), `<a name="recording-engine-activity_progress-事件">` (`consumption.food_efficiency`), `<a name="recording-engine-检查点创建">` (checkpoint seals `opened`), `<a name="recording-engine-质量报告">` (meal diversity only positive eat progress; `lowest_energy_before_meal`).
- `08-configuration.md` `<a name="configuration-加载与校验">` (`recipe_output_bindings`), `<a name="configuration-地图引用与门店供给初值">` (cooking declaration lines 306–308), `<a name="configuration-身体资源初值">` (`body` fields → `BodyState`), `<a name="configuration-人物差异与衍生体感">` (lunch/dinner windows drive appetite).
- `09-io-formats.md` `<a name="io-formats-bodystate身体状态">` — `BodyState` field table.
- `00-overview.md` `<a name="overview-需求映射表">` rows 「购买食品与实际进食分别结算」「能量与体感」「睡眠模型」.
- `07-evaluation.md` headings 「用餐指标」 / `meal_diversity` (lines 114–127, 189–209) — eat-only metric.

### docs/manual/*.md
- `rules.md` `<a name="rules-配置入口与生效范围">` (exchange-rules.json, resources.jsonl, off-foods.mapping.toml rows), `<a name="rules-品类契约">` (food-dairy density example, consume-food pairing), `<a name="rules-交换规则">` (`food_energy_source`, `inventory_transform` rows), `<a name="rules-供给与制作边界">`, `<a name="rules-食品来源映射说明">`, `<a name="rules-身体规则">` (max energy/thresholds from `resources.jsonl`, basal/MET from drain_rules, catalog `food_efficiency_curve` loaded but unused).
- `resources.md` `<a name="resources-新增商品对象">` (object with `energy_kcal_per_serving`, `energy_density_kcal_per_100`; kcal label rule), `<a name="resources-真实制作后再食用">`, `<a name="resources-编写门店供给与商品">`, corpora table rows 正餐 / 家庭储备 (lines 588–589).
- `scenarios.md` `<a name="scenarios-看懂场景的组成">` (`recipe_output_bindings`), `<a name="scenarios-修改人物身体与账户">` (`initial_energy_kcal`, emergency threshold note).
- `personas.md` `<a name="personas-在场景中填写人物">` (`lunch_window`, `dinner_window`, `initial_inventory`; cooking needs real recipe line 270).

### docs/VERIFICATION.md
- `<a name="verification-时间与能量特性门禁">` — mutation targets incl. energy truncation.
- `<a name="verification-人物-prompt-七层特性门禁">` — mutation targets 「给予不计摄入、进食丢弃不计摄入」.
- `<a name="verification-工具交互特性门禁">` — 料理 row (line 259): ingredient purchase, inventory making, output entering stock, eating, recovery.
- `<a name="verification-报告项report-only">` — `meal_diversity` (line 487).
- `<a name="verification-测试矩阵">` — rows 资源结算 (569), 身体语义 (581), 观察投影 (582: kcal labels), 个人事务 (588: making/eating evidence).

### docs/EXPERIMENT.md
- `<a name="experiment-身体初始值">`, `<a name="experiment-效率曲线">`, `<a name="experiment-需求状态">`, `<a name="experiment-能量感知标签">` — numbers above; 1e-9 precision.
- `<a name="experiment-场景参数">` — points at SPEC-life-runtime 身体参数与单位.

---

## 3. Current formats and fixtures

### 3.1 Catalog (Dataset-published, loaded by `catalog/loader.py` + `categories/loader.py`)
- `catalog/resources.jsonl` — `energy_kcal`: `{kind:"level", unit:"kcal", owner:"agent", bounds:{min:0,max:2400}, conservation:"open", drain_rules:{basal_kcal_per_hour, activity_met:{<met_key>:float…}, food_efficiency_curve:[{input_value,output_value}…]}, alert_rules:[{below:800,code:"HUNGRY"},{below:400,code:"EMERGENCY_ENERGY"}]}`; `sleep_debt_hours` drain_rules `{awake_sleep_debt_per_hour, sleep_recovery_per_hour}` (loaded, validated positive, not used by the two-process model); `entertainment_need` drain_rules.
- `catalog/category-contract.json` — `food` product node with slots `energy_kcal_per_serving, use_duration_seconds, use_modes, consumable, effect, purchase_price_fen_min/max, inventory_unit, serving_quantity, serving_unit` (+ Dataset `energy_density_kcal_per_100` per sub-category `food-dairy`, `food-soda`, …, `prepared-meal` with `serving_unit:"g"`), `ingredient`, `durable` nodes; pairings `person→food consume:eat defined rule_id coefficient`, `person→durable consume:use`, recipe pairing for ingredient.
- `catalog/exchange-rules.json` — `{"rule_id","category_id","action":"consume:eat","operator":"food_energy_source","resource_id":"energy_kcal","unit":"kcal","conservation":"open","energy_slot":"energy_kcal_per_serving","duration_slot":"use_duration_seconds"}`; `inventory_transform` rule `{input_category_id, output_category_id, input_quantity, output_quantity, inventory_unit:"item"}`; `durable_use {effect_slot?}`.
- `catalog/objects.jsonl` — `{"object_id","name","category_id","values":{"absolute":{"energy_kcal_per_serving":185,"energy_density_kcal_per_100":73.85,"serving_unit":"250ml", …},"relative":{},"proportional":{}},"provenance":[{source_id:"off-energy",type:"fact",producer:"data_source",source:"openfoodfacts:product/…",paths:[…]}],"version":1}` (real example from `D:/code/WorldMock-Dataset/requests/0001-white-collar-lianqiu/catalog/objects.jsonl`, read-only).
- `catalog/manifest.json` — `category_requirements.meals {min_count, energy_databases, price_fen_min/max, duration_minutes_min/max}`, `category_requirements.home_pantry {breakfast_servings_min, morning_wash_minutes}` (test fixture leaves `category_requirements` null).
- `catalog/rules/off-foods.mapping.toml` — identity-bound, never executed.
- Corpora read by admission: `corpora/meals/items.json` (`meal_id`, `energy_source{database,source_url,retrieved_at,unit_conversion,energy_kcal_per_100g}`, `price_fen`, `meal_duration_minutes`, `dine_in|delivery`), `corpora/scene-stock/home-pantry.json`, `corpora/ingredients/items.json`, `corpora/open-food-facts/products.json`, `corpora/artificial-map/category-assignment.json` (`kinds: {restaurant:[prepared-meal], convenience_store:[food], supermarket:[food,durable,ingredient], wet_market:[ingredient], merchant:[…]}`).

### 3.2 Scenario (schema 6)
- `body {mass_kg, initial_energy_kcal, initial_sleep_debt_hours, initial_entertainment_need, entertainment_relief_preference_floor?}` → `BodyInstanceConfig`.
- `agents[].initial_inventory {location_id: {object_id: float}}`, `agents[].lunch_window/dinner_window [start,end]` (hours).
- `products [{product_id}]`, `supplies [...]` (storefront), `recipe_output_bindings {output_category_id: object_id}` (default `{}`).

### 3.3 Runtime config/state
- `BodyConfig` (assembled): `mass_kg, initial_energy_kcal, maximum_energy_kcal, initial_sleep_debt_hours, basal_kcal_per_hour, awake_sleep_debt_per_hour, sleep_recovery_per_hour, entertainment_need_per_awake_hour, hunger_threshold_kcal, emergency_threshold_kcal, work_efficiency_factors, food_efficiency_curve, activity_met, entertainment_relief_per_hour, entertainment_relief_preference_floor, work_notes_multiplier, source_kind`.
- `InventoryItemConfig`: `product_id, name, category_id, serving_quantity?, serving_unit?, inventory_unit?, energy_kcal_per_unit, use_duration_seconds, use_modes, consumable, effect, required_location_id?, source_kind`; `ProductConfig` adds `purchase_price_fen_min/max`.
- `BodyState`: `energy_kcal, sleep_pressure, sleep_deficit_hours, sleep_need_hours, chronotype_midsleep, social_need, mood_valence, neuroticism_norm, entertainment_need`.
- `AgentState.inventory` nested per location; `WorldState.contact_holdings`.
- `ConsumeRequest {items:[{product_id, quantity:float 0<q≤100}], use_mode:"eat"|"use", duration_seconds:60..14400}`.

### 3.4 Event payload keys (SQLite footprints / checkpoints)
- `ActivityState.data` for CONSUME: `request` (ConsumeRequest JSON), `consumed {product_id: float}`, `opened {product_id: float}` (eat only), `discarded {…}` (set at conclusion), `food_energy_kcal_per_hour` (rate × utilization at start), `inventory_recipe` (`InventoryRecipePlan.payload()`: `rule_id, input_object_id, output_object_id, input_quantity, output_quantity, resource_id, inventory_unit` as decimal strings), `recipe_applied: true`.
- `environment_event.data.kind=body_settlement`: `interval_start/end, activity_ids, body_settlement{before, after, duration_seconds, basal_energy_kcal, extra_energy_kcal, food_energy_kcal, food_efficiency, work_efficiency}, resource_flows[{resource, owner:"agent:<id>", direction, amount, cause, occurred_at}]` with causes `settle:<kind>:energy_cost`, `settle:shared:energy_cost`, `food:intake`.
- `activity_progress.data.consumption {use_mode, items[{product_id, interval_quantity, cumulative_quantity}], food_efficiency}`.
- `action_result.data.outcome [{activity, outcome, items[{product, eaten, discarded, remaining}] | items[{product, consumed, produced, remaining}], note}]`.
- `verified_experiences` row kind `consumed {product_id, location_id, interval_quantity, cumulative_quantity}`.
- Observation `body.values {appetite, sleepiness, entertainment_need, social_need, mood_valence, sleep_pressure, energy_kcal, sleep_deficit_hours}`, `body.levels {appetite, sleepiness, entertainment, social, mood}`.
- Tool-layer target `state`: 「每份约 N 千卡」 / 「几乎不含热量」, 「持有 X 份」, 「已吃 X 份，剩 Y 份」; `actions[].quantity`, `duration` phrases.

### 3.5 Fixture files encoding these shapes
- `tests/fixtures/environment/catalog/category-contract.json` (food slots, pairings), `exchange-rules.json` (`eat-food` food_energy_source), `objects.jsonl` (`milk`: `energy_kcal_per_serving 185`, `use_duration_seconds 900`, `merchant_role`, `price_fen`), `resources.jsonl` (energy drain/alert rules, sleep/entertainment), `manifest.json`, `affordances.toml`, `rules/motivation.toml`, `rules/persona-mapping.toml`, `rules/travel-time.toml`.
- `tests/fixtures/environment/corpora/artificial-map/category-assignment.json` (kinds → food/prepared-meal/ingredient/durable), `supplies.json`, `locations.json`, `routes.json`, `corpora/lifestyle/items.json`, `corpora/manifest.json`.
- `tests/fixtures/environment/scenarios/single-person.json`, `multi-person.json`, `multi-person-half-day.json` (`body{mass 70, initial 2000}`, `products [milk]`, `initial_inventory {home:{milk:2.0}}`, no recipe bindings, no meal windows).
- Python-built fixtures that encode energy/recipe shapes: `tests/catalog/admission_fixtures.py`, `tests/catalog/conftest.py`, `tests/categories/conftest.py`, `tests/categories/scenario_fixtures.py`, `tests/life/purchase_worlds.py`, `tests/integration/storefront_fixtures.py`, `tests/engine_fixture.py`, `tests/frozen_inputs.py`, and the 8 local `body_config()` fixtures listed in 1.3.
- Ledgers/baselines touching this subsystem: `tests/fixtures/acceptance_requirements.json`, `tests/fixtures/mutation-symbol-baseline.json` (`tool:recipe_no_input_debit`, `tool:recipe_no_output_credit`, `errands:activity_start_counts_as_eaten`), `tests/fixtures/tool_interaction_habits.json` (ways 「食用」; cooking fixtures), `tests/fixtures/layering-public-api.json` (`meal_windows_by_agent` signature), `tests/fixtures/current-feature-owners.json` (energy anchors), `tests/fixtures/dayrun_work_reviews.json` (mentions energy text only).
- Day-run baselines (`tests/fixtures/dayrun-layering-baseline.json`, `dayrun-shared-rules-baseline.json`) do not embed `energy_kcal`/`opened` keys, but they are full-day fingerprints (structure-6 external inputs) and will change with any body/food rule change.

---

## 4. Tests that lock the current behavior

### 4.1 Test files (primary)
- `tests/life/test_body.py` — `test_food_uses_sleep_debt_curve_and_energy_cap`, `test_every_activity_truncates_at_zero`, `test_overdrawn_work_settles_to_zero_energy_without_error`, `test_exact_total_energy_survives_feedback_partition_rounding`, `test_energy_duration_uses_same_cost_at_sixty_second_boundary`, `test_recovery_activity_duration_is_not_masked_by_zero_energy`.
- `tests/life/test_body_flows.py` — `test_food_intake_records_single_energy_source`, `test_food_energy_cap_limits_source_amount_to_headroom`, `test_activity_energy_cost_records_combined_drain`, `test_overdrawn_settlement_drains_only_the_remaining_energy`, `test_full_history_replay_matches_incremental_settlement`, `test_resource_metas_declare_three_open_level_domains`.
- `tests/life/test_meal_outcome.py` — `test_ending_a_meal_at_300_of_1500_seconds_eats_0_2_discards_0_8_and_keeps_3`, `test_ending_a_meal_early_adds_no_energy_for_the_discarded_portion`, `test_pause_keeps_the_opened_remainder_and_resume_eats_it_after_another_meal_took_the_stock`, `test_ending_a_paused_meal_discards_the_opened_remainder_without_intake`, `test_concluding_a_failed_meal_names_the_failure_and_discards_the_remainder`, `test_less_than_one_portion_is_eaten_for_a_scaled_duration_and_energy`, `test_experiment_cutoff_truncation_discards_nothing`, `test_portion_duration_and_meal_portion_share_one_basis`, `test_meal_duration_is_validated_against_the_scaled_portion`, `test_open_meal_moves_the_portion_out_of_stock_only_for_eating`, `test_conclusion_outcome_names_failure_and_explicitly_reports_no_item_changes`.
- `tests/life/test_dayrun_inventory_regressions.py` — `test_meal_partitions_reach_zero_without_losing_quantity_or_energy`, `test_irregular_meal_progress_matches_one_settlement`, `test_meal_rounding_tolerance_eats_only_the_opened_quantity`, `test_eating_progress_ignores_stock_taken_while_the_meal_was_paused`.
- `tests/life/test_semantics.py` — `test_energy_thresholds_come_from_catalog_alert_rules`, `test_contract_math_matches_the_engine_settlement`, `test_assembly_rejects_catalog_met_drift`, `test_assembly_rejects_missing_alert_rules`, `test_assembly_rejects_unbounded_initial_below_minimum`, `test_products_and_supplies_resolve_their_separate_responsibilities`.
- `tests/life/test_tool_interaction_recipe.py` — `test_published_recipe_debits_input_and_creates_edible_output`, `test_recipe_without_real_input_is_rejected_and_narration_cannot_create_food`.
- `tests/life/test_energy_feasibility.py` — zero-energy acceptance set, `test_hunger_band_crossing_wakes_during_activity`, `test_hunger_band_crossing_wakes_during_chat`, `test_hunger_crossing_just_before_lunch_end_wakes_during_wait`, `test_energy_gate_symbols_are_gone_from_production_code`, `test_motivation_internal_still_weighs_energy`, `test_checkpoint_with_overdrawn_activity_restores`.
- `tests/integration/test_meal_checkpoint.py` — `test_paid_meal_pause_checkpoint_resume_preserves_actual_consumption`.
- `tests/integration/test_category_inventory_protocol.py` — `test_held_durable_recipe_survives_real_model_and_engine_roundtrip`, `test_held_food_is_offered_for_eating_without_a_purchase_quote`, `test_held_durable_preserves_location_use_rule`.
- `tests/life/test_felt_states.py` — `TestAppetiteFormula.*`, `TestMealWindowsValidation.*`, `TestLowEnergyAppetite.*`, `test_appetite_level_boundaries`, `test_body_config_rejects_emergency_threshold_not_above_starving`, `test_scenario_with_emergency_threshold_at_starving_constant_fails_to_load`, `TestSocialLedger.test_shared_meal_relief`.
- `tests/life/test_sleep_two_process.py` — `TestEfficiencyCurves.test_food_utilization_nodes_and_midpoints`, `test_food_utilization_boundary_extrapolation`, `test_frozen_curve_constants`.
- `tests/categories/test_rules.py` — `test_food_energy_rejects_float_overflow`, `test_every_operator_rejects_the_wrong_resource_shape`, `test_closed_inventory_requires_equal_quantities`, `test_inventory_transform_unit_must_match_resource_unit`, `test_recipe_scaling_preserves_exact_quantities_independently_of_decimal_context`, `test_recipe_scales_both_sides_once`, `test_recipe_quantities_reject_non_json_numbers`, `test_money_and_energy_rounding_operators`, `test_unknown_operator_and_formula_string_rejected`.
- `tests/categories/test_catalog_integration.py::test_food_duration_uses_rule_named_slot`.
- `tests/life/test_category_runtime.py` — `test_recipe_fixture_loads_with_output_category_binding`, `test_recipe_completes_once_after_readable_use`, `test_recipe_pairing_coefficient_scales_actual_inventory`, `test_invalid_recipe_refuses_before_run_artifacts`, `test_food_coefficient_changes_body_settlement`, `test_no_effect_use_rejects_without_inventory_change`, `test_recipe_completion_rechecks_inventory`.
- `tests/life/test_category_runtime_edges.py` — `test_recipe_plan_rejects_incomplete_or_nonnumeric_persisted_payload`, `test_prepare_recipe_*`, `test_apply_recipe_returns_same_state_when_activity_was_already_applied`, `test_recipe_completion_fixture_settles_both_balances_and_effects`, `test_recipe_completion_rechecks_pairing_before_inventory_or_effects`, `test_plain_meal_inventory_retains_insufficient_quantity_rejection`, `test_recipe_same_object_keeps_ledger_overlap_rejection_without_effects`.
- `tests/life/test_activity_effects.py` — `test_body_boundary_predicts_first_energy_threshold_second`, `test_body_boundary_uses_curve_node_and_food_net_energy`, `test_hunger_upward_crossing_is_not_later_than_actual`, `test_hunger_downward_crossing_at_meal_window_end`, `test_hunger_crossing_reverted_by_meal_window_end_is_not_skipped`, `test_meal_window_edges_are_boundary_candidates`, `test_chat_and_call_are_predicted_but_sleep_and_game_are_not`.
- `tests/life/test_work_efficiency_factors.py` — `test_food_efficiency_and_non_work_physics_stay_independent`, `test_energy_factor_threshold_outputs`, `test_work_interval_integrates_across_energy_thresholds`, `test_factors_assembled_from_catalog`.
- `tests/life/test_public_observation_contract.py` — `test_food_energy_label_rounds_half_up_to_ten_kcal`, `test_products_and_inventory_show_food_energy_and_elsewhere_holdings`, `test_low_energy_prompt_carries_levels_and_labels_but_no_body_numbers`.
- `tests/life/test_shared_body.py`, `test_sleep_billing.py::test_meal_finished_exactly_at_the_boundary_still_counts_as_shared`, `test_relief_floor.py`, `test_wait_passive_entertainment.py`, `test_discomfort_and_sleep_messages.py` (hunger pulse), `test_wake_reasons.py::test_low_energy_bands_match_the_decision_reference`.
- `tests/life/test_domain_branch_coverage.py` — `test_basket_change_inventory_rejects_over_consume`, `test_basket_meal_duration_enforces_longest_and_catalog_contract`, `test_basket_validate_meal_*`, `test_basket_meal_food_rate_is_zero_for_non_eating_use`, `test_basket_apply_meal_progress_durable_completion_and_paused`, `test_feasible_food_rows_drop_unroutable_delivery`, `test_assembly_rejects_duplicate_consume_edges`.
- `tests/life/test_motivation.py` — `test_meal_floor_and_top_three`, `test_sub_floor_foods_fall_back_to_closest_meal`, `test_repeated_food_downweighted_by_sensory_satiety`, `test_hungry_eat_scores_positive_energy_term`, `test_food_affordability_uses_category_amount_instead_of_raw_quote`, `test_no_effect_purchase_does_not_hide_held_food`.
- `tests/life/test_parallel_admission.py` — `test_two_foods_are_one_meal_and_only_same_way_obtains_are_one_order`, `test_holding_lists_obtain_make_for_inventory_recipes_and_consume_use_otherwise`, `test_two_makes_are_not_merged_and_two_uses_are_refused_with_a_reason`, `test_work_with_eating_watching_or_podcast_is_accepted_and_settles_one_shared_body`.
- `tests/life/test_errand_completion.py` — `test_eating_counts_the_actual_interval_quantity`, `test_making_or_using_an_item_is_not_eating_it`.
- `tests/life/test_tool_interaction_habits.py` — `test_every_frozen_habit_has_a_complete_explicit_native_fixture`, `test_declared_habit_runs_with_real_prerequisites_and_settlement` (cooking habit fixtures).
- `tests/catalog/test_category_admission.py` — `test_missing_meals_corpus`, `test_meals_below_contract_minimum`, `test_meals_missing_energy_source_annotation`, `test_meals_price_outside_window`, `test_meals_duration_outside_window`, `test_meals_without_dine_in_or_delivery`, `test_meals_catalog_object_missing`, `test_home_pantry_below_seven_servings`.
- `tests/life/test_scenario.py` — `test_recipe_output_binding_requires_nonblank_category_and_object`, `test_body_instance_and_rejection_ranges_are_enforced`, `test_initial_inventory_shape_rejects_invalid_holdings`, `test_runtime_inventory_cross_references_are_validated`.
- `tests/life/test_todo_layer.py::test_paused_meal_progress_is_only_the_eaten_and_left_portions`, `tests/life/test_commerce.py::test_food_progress_persists_partial_quantity_across_pause_and_completion`, `tests/life/test_giving.py::*nobody_eats*`, `tests/life/test_dayrun_restaurants.py::test_food_paths_are_discoverable_as_tool_layer_targets`.
- Downstream: `tests/footprints/test_verified_experiences.py::test_consumed_intervals_are_increments_and_projection_is_idempotent`, `tests/footprints/test_purchases.py::test_mixed_eat_and_buy_in_progress_result_counts_its_order_once`, `tests/evaluation/test_behavior_metrics.py::test_meal_diversity_simpson_and_repetition_anchors`, `tests/footprints/test_quality.py` (efficiency.food), `tests/life/test_rerun_metrics.py` (missed meals).

### 4.2 Acceptance ledgers that pin these tests (cost of deletion/renaming)
- `tests/fixtures/acceptance_requirements.json` + `tests/time_and_energy_acceptance.py` (checked by `tests/test_time_and_energy_completeness.py`): rows 「候选时刻」(hunger scan, meal windows), 「能量感知」(`_STAGE2_ENERGY_PERCEPTION_TESTS`: test_felt_states LowEnergy*, test_semantics::test_energy_thresholds_come_from_catalog_alert_rules, test_public_observation_contract food label tests), 「能量不拦人」(`_STAGE2/_STAGE4_ENERGY_NEVER_BLOCKS_TESTS`: test_energy_feasibility.*, test_body::test_every_activity_truncates_at_zero, test_parallel_admission::test_two_foods_are_one_meal…), 「绿色基线」(test_dayrun_inventory_regressions::test_meal_partitions…); `normative_requirements` 8–15 name the `time-and-energy-能量感知*`/`能量不拦人*` anchors; the completeness test also asserts removed symbols stay absent.
- `tests/persona_prompt_acceptance.py` (checked by `tests/test_persona_prompt_acceptance.py`): LEDGER rows 「进食」, 「不足一份的食物无法消费」, 「进食提前结束只显示「已收尾」」 → `tests/life/test_meal_outcome.py::*`, `test_dayrun_inventory_regressions::*`, `test_todo_layer::test_paused_meal_progress…`; mutation file list includes `src/worldmock/life/basket.py`; test-file list includes `tests/life/test_meal_outcome.py`; `acceptance_requirements.json["persona-prompt"]["裁决记录"]` rows 进食/暂停进食/结束进食.
- `tests/dayrun_fidelity_acceptance.py`: mutation scope lists `basket.py`, `body.py`, `felt.py`; row 「睡眠计费」 uses `test_sleep_billing::test_meal_finished_exactly_at_the_boundary_still_counts_as_shared`; 「困意档界」「不适脉冲与消息」 rows use `felt` tests; ledger text requires band names to match `felt.py` verbatim (`LIFE_SYSTEM_LAYER.rules` 18 rules).
- `tests/tool_interaction_acceptance.py` (+ `tests/test_tool_interaction_acceptance.py`): rows 「购买与食用」 and 「料理」 → `tests/life/test_tool_interaction_recipe.py::*`, `tests/life/test_tool_protocol.py::test_native_recipe_and_giving_aliases_keep_actual_route_conditions`; MUTATION rows `recipe_no_input_debit`, `recipe_no_output_credit` anchored at `worldmock.life.inventory_recipe::_inventory_from_balances`; hashes pinned in `tests/fixtures/mutation-symbol-baseline.json` (`tool:recipe_no_input_debit`, `tool:recipe_no_output_credit`) and checked by `tests/test_mutation_runner.py`.
- `tests/errands_workday_acceptance.py`: 「完成依据」 rows (制作产出算、进食按实际食用量) → `tests/life/test_errand_completion.py::test_eating_counts_the_actual_interval_quantity`, `::test_making_or_using_an_item_is_not_eating_it`; mutation `activity_start_counts_as_eaten` (baseline entry `errands:activity_start_counts_as_eaten`); mutation patch text references `activity.data["inventory_recipe"]` (line 1329).
- `tests/shared_rules_acceptance.py`: 「社交资源分录」 → `tests/life/test_shared_body.py::test_shared_activity_factors_use_social_met_and_declared_factors`.
- `tests/idle_fillers_acceptance.py` / `tests/fixtures/idle-fillers-acceptance.json`: rule 15 「等平常的饭点再吃正餐」 (system-layer text, meal timing only).
- `tests/fixtures/layering-public-api.json`: `meal_windows_by_agent` signature returning `dict[int, felt.MealWindows]` (public-API drift check `tests/test_layering_acceptance.py`).
- `tests/fixtures/current-feature-owners.json` (checked by `tests/test_current_feature_owners.py`): anchors `time-and-energy-能量感知`, `-饥饿在低能量时不失灵`, `-商品热量与路线费力`, `time-and-energy-能量不拦人*` must keep existing in `docs/dev/SPEC-life-runtime.md`.
- `tests/test_single_source.py::OWNERS`: `base_models.py` owns `CurvePoint`, `EfficiencyFactor`; `life/models.py` owns `CONSUME_DURATION_CAP_SECONDS`; `life/activities.py` owns `entertainment_relief_rate`/`relief_preference`; `categories/common.py` owns `Conservation`; `resources/models.py` owns `AMOUNT_TYPES`. No basket/body/felt symbols are single-source-owned, so moving them is free of that gate; adding a second copy of any curve table would trip it.
- `docs/dev/ANALYSIS-spec-coverage.md` rows 进食 (113), 能量感知 (141), 能量不拦人 (142), 候选时刻 (139), 料理 (176) list the test node names; `scripts/documentation_gate.py`/`tests/test_documentation_gate.py` resolve test references in docs.

---

## 5. What the proposal changes here

Proposal clauses driving this subsystem: §「三类食品只保留“份”和固定热量」, §「两个引擎各自只做一件事」 (rules fixed in WorldMock; Dataset cannot provide 热量覆盖值), §「本次重设计的五类目录和三种绑定」 (food = `标识、名称、零食／饮品／正餐、每份价格` + store binding or `all`), §「正式替换时复用与删除的范围」 (delete 逐食品营养、食材配方; keep 余额账本、持有数量、身体状态), decision table rows 「做饭是否保留」「食品常数」, acceptance counter-examples 「食品热量固定」「分类与规则归系统」.

### 5.1 Fixed food categories and constants (ADD)
- ADD `src/worldmock/life/food_rules.py` (engine-owned, no environment override):
  - `class FoodCategory(StrEnum): SNACK = "snack"; DRINK = "drink"; MEAL = "meal"` with display names `{"snack": "零食", "drink": "饮品", "meal": "正餐"}`.
  - `FOOD_RULES: Mapping[FoodCategory, FoodRule]` where `FoodRule(energy_kcal_per_portion: float, eat_seconds_per_portion: int)` = snack (200, 300), drink (100, 180), meal (600, 1200) — values are the proposal's 待冻结 assumptions (decision row 「食品常数」), registered as `experiment_assumption` in `docs/EXPERIMENT.md`.
  - Consume action by category is fixed: every food category → `eat`; durables (kept per proposal 「本表不删除其他现有生活服务和非食品用品」) → `use`. This replaces `person→food consume:eat` pairings and `require_item_rules`.
- The constants replace `FoodEnergyRule.energy_slot/duration_slot`, `evaluate_food_energy`, the compiled `SourceEdge(on="consume")`, and `InventoryItemConfig.energy_kcal_per_unit/use_duration_seconds` as catalog-sourced values.

### 5.2 Food catalog object (REWRITE of the object shape)
- Dataset food entry (new environment format): `{food_id, name, category: "snack"|"drink"|"meal", price_fen: int>0, stores: [location_id…] | "all", summary?: str}`. No `energy_kcal_per_serving`, `energy_density_kcal_per_100`, `serving_*`, `use_duration_seconds`, `use_modes`, `consumable`, `effect`, `inventory_unit`, `purchase_price_fen_min/max`, `provenance` numeric coverage for kcal.
- REWRITE `src/worldmock/life/scenario.py::InventoryItemConfig` → minimal runtime view `FoodConfig {food_id, name, category: FoodCategory, price_fen}` plus derived properties `energy_kcal_per_portion`/`eat_seconds` read from `FOOD_RULES` (not stored, so a Dataset value can never override them). Keep a separate `DurableConfig {object_id, name, use_seconds, effect}` only if durables survive (they are outside this subsystem's decision; see §8).
- DELETE `scenario.py::_PRODUCT_USE_MODES`, `InventoryItemConfig.validate_product` (serving consistency), `ProductConfig.purchase_price_fen_min/max` (price lives on the food object per proposal §「同类店铺同商品」).
- REWRITE `semantics.py::_assemble_items/_inventory_values/_inventory_payload/_consume_edge/_product_object` → build `FoodConfig` straight from the registered food catalog; DELETE `_consume_edge` and the `SourceEdge` reading.
- DELETE `catalog/compiler.py::_edge_for_rule` food branch and `_effective_duration`, `catalog/models.py::Serving`, `ExchangeObject.serving/use_duration_seconds/use_modes/consumable/effect`, `SourceEdge` for food (energy edges are no longer compiled per object; proposal §「分阶段路线图」: "不再为每个对象编译一份交换边").
- Registration validation (ADD, in the new environment loader): unique `food_id`, category in enum, positive `price_fen`, every store id exists and is a food-selling kind, `all` expands to food-selling kinds only; the same-type-store consistency check belongs to the storefront subsystem (proposal §「同类店铺同商品」) but must run on the food→store binding produced here. Old fields (`energy_kcal_per_serving`, `serving_unit`, `edges`, `values.absolute.*`) → hard reject with an English error log.

### 5.3 Exchange rules for food (DELETE)
- DELETE `src/worldmock/categories/rules.py::FoodEnergyRule`, `RuleOperator.FOOD_ENERGY_SOURCE`, `_operator_resource_domain` food branch, `_validate_operator_action` food row; DELETE `categories/operators.py::evaluate_food_energy`.
- The environment no longer ships `exchange-rules.json`/`category-contract.json` (proposal §「两个引擎各自只做一件事」 table row 1); `categories/loader.py`, `ExchangeRulesDocument`, `build_exchange_rules`, `validate_rule_resource_domains`, pairing resolution for `consume:*`, and `life/category_contract.py::require_item_rules/require_runtime_rule` (for food) go with it. `category_matches` in `ActionResult.data` (session_actions `_consume`) → DELETE.
- `Action.CONSUME_EAT/CONSUME_USE` enum members may stay as internal action names (they are used by menus/ways), but no pairing lookup.

### 5.4 Eating settlement (REWRITE in place, keep mechanisms)
- KEEP the whole `opened`/`consumed`/`discarded` ledger in `basket.py` and `activity_evidence.py` — it already implements the proposal's "按实际消费比例结算", "暂停后只保留剩余份量", "一整份分几次吃的原始摄入总量相同", "不能靠重复开始进食重复获得热量".
- REWRITE `basket.py::meal_food_rate` → `sum(FOOD_RULES[item.category].energy_kcal_per_portion × quantity) × 3600 / duration`; `meal_duration`/`meal_portion`/`portion_duration` → read `FOOD_RULES[category].eat_seconds_per_portion` instead of `product.use_duration_seconds` (same "longest of portion-scaled durations" rule, proposal §「同餐多项沿用按份量折算后取最长食用时长」).
- REWRITE `basket.py::_apply_item_delta` → `apply_food(body, FOOD_RULES[category].energy × quantity, config)`; drop `product.effect`/`consumable` handling for food (effects only for durables, if kept).
- REWRITE `basket.py::validate_meal_use` → eat only for `FoodConfig`, use only for durables; drop `required_location_id` for food.
- KEEP `body.py::apply_food` (efficiency × cap) unless decision §8-Q2 removes utilization; the proposal text keeps it ("身体净能量仍经过现有利用效率、上限和消耗结算").
- KEEP `session_actions.ActionHandlers._consume`, `time_advance._apply_consume_effect`, `activities._food_energy_rate`, `activity.data{request, consumed, opened, discarded, food_energy_kcal_per_hour}`; DELETE `inventory_recipe` and `recipe_applied` keys, `recipe = prepare_inventory_recipe(...)` branches, `category_matches`.
- ADD to `activity_progress.data.consumption` and to `action_result.data.outcome` items: `category` (snack/drink/meal) so足迹 records the fixed rule applied; `food_efficiency` stays if utilization stays.
- `tool_layer._product_state`: REWRITE kcal label to the category rule (「正餐，每份约 600 千卡」) or category only — see §8-Q4; `food_energy_label` can stay as a pure formatter.
- `motivation.py::_eat_candidates`: REWRITE floor/gap logic to category-aware (meal preferred, snack/drink fallback); `MEAL_KCAL_FLOOR` constant becomes a category test (internal scorer, no prompt impact).

### 5.5 Body energy configuration (REWRITE where the proposal fixes rules in WorldMock)
- The proposal fixes "资源单位与交换公式" and "食品热量…" in WorldMock and removes `exchange-rules.json`; the energy resource definition (`resources.jsonl` `energy_kcal` bounds, `basal_kcal_per_hour`, `activity_met`, `alert_rules`) is Dataset-published today. Smallest consistent design: move the body constants into an engine-packaged table (e.g. `src/worldmock/data/body.toml`, loaded like `affordances.toml`/`motivation.toml` under `worldmock.data`) with `maximum_energy_kcal=2400`, `basal_kcal_per_hour=65`, `hunger_threshold_kcal=800`, `emergency_threshold_kcal=400`, `activity_met{…}`; `semantics._assemble_body` reads it instead of catalog `resources.jsonl`; scenario keeps `body{mass_kg, initial_energy_kcal, initial_sleep_debt_hours, initial_entertainment_need, entertainment_relief_preference_floor}`. DELETE `semantics.ENERGY_RESOURCE/SLEEP_RESOURCE/ENTERTAINMENT_RESOURCE` catalog lookups, `_alert_thresholds`, `_drain_rules`, `catalog/models.py::DrainRules.food_efficiency_curve` (already dead: `apply_food` uses `sleep.FOOD_UTILIZATION_CURVE`), `BodyConfig.food_efficiency_curve`, `awake_sleep_debt_per_hour`, `sleep_recovery_per_hour` (dead since the two-process rewrite). This is flagged as §8-Q1 because the proposal names 热量/速度/娱乐效果/推送/配送 but not basal/MET explicitly.
- KEEP `body.py` settlement, `ResourceLedger` flows (`food:intake`, `settle:*:energy_cost`), boundary prediction, `felt.py` appetite and bands, `sleep.py` curves — none contradict the proposal.

### 5.6 Cooking and recipes (DELETE, pending decision row 「做饭是否保留」)
- DELETE `src/worldmock/life/inventory_recipe.py` entirely; `categories/rules.py::InventoryTransformRule`, `RuleOperator.INVENTORY_TRANSFORM`; `categories/operators.py::scale_inventory_recipe`, `InventoryDelta`; `catalog/compiler.py::_validate_dynamic_rule_attributes` recipe branch; `life/category_contract.py::_validate_recipe_bindings/_recipe_output/_validate_recipe_output` and the recipe closure in `admit_reachable_categories`; `scenario.py::LifeScenario.recipe_output_bindings`, `_validate_recipe_bindings`, `RuntimeScenario.recipe_output_bindings`, `semantics._inventory_object_ids` recipe union; `observe.py` recipe-holding detection (lines 215–227) and the way 「制作」 (`obtain` on a held item) in `tool_layer._consume_check`, `session_actions._consume`, `interaction_menu`/ways tables (CONTRACTS `contracts-closed-request-models` row 做饭→制作); `activity_evidence._item_change_outcome` recipe branch; `time_advance._apply_consume_effect` recipe branch; `ingredient` category and `wet_market: [ingredient]` admission pool (storefront subsystem to coordinate); `ResourceLedger.ConvertEntry` usage here (the ledger primitive itself stays for other uses — check `resources/` tests before deleting `ConvertEntry`).
- If the alternative (keep cooking as a fixed-duration lifestyle activity producing nothing) is chosen, nothing in this subsystem is kept: the activity would be a `lifestyle` item and must not produce food.

### 5.7 Dataset-side nutrition admission (DELETE)
- DELETE `catalog/admission.py::_check_meals/_check_meal_entry`, `catalog/models.py::MealRequirements`, `CategoryRequirements.meals`, `PantryRequirements`/`home_pantry` (7-breakfast-serving requirement) and the corpora `meals/items.json`, `scene-stock/home-pantry.json`, `ingredients/items.json`, `open-food-facts/products.json`, `catalog/rules/off-foods.mapping.toml` from the environment format; the proposal replaces corpus-sourced kcal with fixed category kcal and leaves initial holdings to the scenario (`agents[].initial_inventory` stays, now keyed by `food_id`).

### 5.8 Formats/events that change
- Environment: new food catalog file (name to be fixed by the spec, e.g. `catalog/foods.json`), no `exchange-rules.json`, no `category-contract.json`, no `objects.jsonl` food rows, no `resources.jsonl` energy rule (if §8-Q1 moves body constants into the engine). Old files → `ENVIRONMENT_FORMAT_REJECTED` style hard error (proposal: 旧格式直接拒绝).
- Scenario schema 6 → 7: drop `recipe_output_bindings`; `products[]` replaced by the food catalog + store bindings (storefront subsystem).
- World state schema 8 → 9: `ActivityState.data` loses `inventory_recipe`/`recipe_applied`; gains `consumption.category`.
- Checkpoint/footprints: `body_settlement` payload unchanged; `activity_progress.consumption` gains `category`; `action_result.data.outcome` recipe shape removed; `category_matches` removed.
- Run identity: the engine's fixed rule version must be bound (`FOOD_RULES` version + body constants) since they no longer hash through the environment (proposal §「主提案复核」 last paragraph).

---

## 6. Conflicts with frozen clauses

| Document | Anchor / heading | Gist of the frozen text the proposal contradicts |
| --- | --- | --- |
| `docs/dev/SPEC-category-contract.md` | `<a name="spec-category-contract-冻结产物与品类树">` | Dataset provides `category-contract.json` and `exchange-rules.json`; food objects sit in a category tree (纯牛奶→乳制品). Proposal: rules fixed in WorldMock, three fixed food categories. |
| `docs/dev/SPEC-category-contract.md` | `<a name="spec-category-contract-属性槽位与对象增量">` | Food density window is a category slot; objects carry kcal deltas. Proposal: no per-food nutrition. |
| `docs/dev/SPEC-category-contract.md` | `<a name="spec-category-contract-来源分类与审核证据">` | Every static number (kcal, recipe quantities) must have a source record. Proposal: kcal is an engine constant; prices/durations are "仿真数值" labelled synthetic, not sourced. |
| `docs/dev/SPEC-category-contract.md` | `<a name="spec-category-contract-配对解析与可触达检查">` | `consume:eat`/`consume:use` pairings and recipe-output closure drive admission. Proposal: type→behaviour direct mapping, no pairings. |
| `docs/dev/SPEC-category-contract.md` | `<a name="spec-category-contract-闭集算子与真实结算">` | Operator table incl. 食品能量来源 (category kcal × coefficient) and 库存转换; food duration/effect compiled from rule-named slots. Proposal: fixed kcal/duration per category, no coefficients, no operators from the environment. |
| `docs/dev/SPEC-category-contract.md` | `<a name="spec-category-contract-库存转换配方">` | Full cooking contract (ingredient pack → meal, `recipe_output_bindings`). Proposal recommends deleting cooking. |
| `docs/dev/SPEC-category-contract.md` | `<a name="spec-category-contract-裁决依据">` (变换配方 row), `<a name="spec-category-contract-验收与需求台账">` (库存转换, 真实结算与系数 rows) | Same as above. |
| `docs/dev/SPEC-life-runtime.md` | `<a name="spec-life-runtime-来源与准入">` | Food from Open Food Facts, prices from Open Prices. Proposal: real names + synthetic numbers, no nutrition DB. |
| `docs/dev/SPEC-life-runtime.md` | `<a name="spec-life-runtime-转换效率算法">` 「参数管理」 | Body/activity parameters live in the scenario-selected catalog with unit/source/range. Proposal (if §8-Q1 adopted): engine-fixed. |
| `docs/dev/SPEC-life-runtime.md` | `<a name="spec-life-runtime-身体参数与单位">` | "场景校验强制每项身体与活动参数带单位、来源类型与取值区间" via catalog. Same conflict. |
| `docs/dev/SPEC-life-runtime.md` | `<a name="time-and-energy-能量感知-商品热量与路线费力">` | Kcal label from `energy_kcal_per_unit` derived from catalog serving declaration ("数值来自目录的份量声明"). Proposal: fixed per category. |
| `docs/dev/SPEC-life-runtime.md` | `<a name="time-and-energy-能量感知-饥饿在低能量时不失灵">` | 400 threshold "取身体配置的 `emergency_threshold_kcal`（目录 `alert_rules` 中的 `EMERGENCY_ENERGY`）… 目录与数据集副本都不变". Conflict only if §8-Q1 moves alert thresholds into the engine. |
| `docs/dev/SPEC-life-runtime.md` | `<a name="tool-interaction-料理与习惯">` | Cooking via `inventory_recipe`; publish ingredient pack + rule + bindings. Proposal: delete. |
| `docs/dev/SPEC-life-runtime.md` | `<a name="time-and-energy-五类生活动作与并行准入-请求形状与菜单">` | `obtain` way 「制作」 for recipe inputs. Proposal: no 制作. |
| `docs/dev/SPEC-life-runtime.md` | `<a name="spec-life-runtime-具体活动结算">` 工作 row / `<a name="errands-workday-个人事务-完成依据与结算">` | 「取得」 counts recipe output entering inventory. Remove the making clause. |
| `docs/dev/SPEC-life-realism.md` | `<a name="spec-life-realism-世界素材品类要求">` | 家的可供性: 7 breakfast servings in pantry corpus; 正餐: ≥30 meals with kcal from 中国食物成分表/USDA, price 15–45 元, 20–40 min. Proposal: fixed 600 kcal/20 min meals, synthetic prices, no corpus. |
| `docs/dev/SPEC-life-realism.md` | `<a name="spec-life-realism-动机层">` | Eat candidates "热量不低于 300 kcal 或最接近缺口" over per-food kcal. Needs category restatement. |
| `docs/dev/SPEC-dayrun-behavior.md` | `<a name="spec-dayrun-behavior-餐馆的实际进食路径">` | Restaurants bind existing `prepared-meal` products; per-store menus/quotes/stock are scenario assumptions. Proposal: same-type stores share the menu; food binds stores or `all`. |
| `docs/dev/SPEC-map-and-storefront.md` | `<a name="spec-map-and-storefront-按地点类别生成完整供给">`, `<a name="tool-interaction-地点供给和价格-类别准入与逐店商品集合">` | Kind→category pools incl. `ingredient`/`wet_market`, menu-difference rule, per-store price positioning. (Storefront subsystem; listed because `ingredient` and `prepared-meal`/`food` categories are this subsystem's objects.) |
| `docs/CONTRACTS.md` | `<a name="contracts-品类契约与目录字段">` | `food_energy_source`, `inventory_transform` operators; `recipe_output_bindings`; `INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE`; contract version 3. |
| `docs/CONTRACTS.md` | `<a name="contracts-closed-request-models">` | Way 「制作」 and 做饭→制作 mapping. |
| `docs/CONTRACTS.md` | `<a name="contracts-request-settlement">` | 「制作」不合并 clause. |
| `docs/CONTRACTS.md` | `<a name="contracts-private-fields">` | "`consume.items[].quantity` 是请求份数，配方实际输入还要按冻结规则与配对系数缩放"; `inventory_items` rows carry `use_duration_seconds`. |
| `docs/CONTRACTS.md` | `<a name="contracts-action-results">` | Recipe outcome items (consumed/produced). |
| `docs/CONTRACTS.md` | `<a name="contracts-语料内容目录字段">` | Corpus `attributes` include nutrition. |
| `docs/CONTRACTS.md` | `<a name="contracts-人物卡片覆盖率产出">` | `food_preference` targets derived from meal corpus names. Must become food catalog names. |
| `docs/CONTRACTS.md` | `<a name="contracts-资源与需求字段">` | Only if §8-Q1: resource definitions move out of the environment. |
| `docs/EXPERIMENT.md` | `<a name="experiment-身体初始值">`, `<a name="experiment-能量感知标签">`, `<a name="experiment-需求状态">` | Body values "按体重"/catalog-sourced; kcal label rule from per-food kcal. New assumption rows for 200/100/600 kcal and 5/3/20 min. |
| `docs/VERIFICATION.md` | `<a name="verification-工具交互特性门禁">` 料理 row; `<a name="verification-测试矩阵">` rows 资源结算/身体语义/观察投影/个人事务 | Reference cooking chain and per-food kcal label tests. |
| `docs/manual/rules.md` | `<a name="rules-配置入口与生效范围">`, `<a name="rules-品类契约">`, `<a name="rules-交换规则">`, `<a name="rules-供给与制作边界">`, `<a name="rules-食品来源映射说明">`, `<a name="rules-身体规则">` | User-facing instructions for editing food density slots, operators, recipe rules, body thresholds in `resources.jsonl`. |
| `docs/manual/resources.md` | `<a name="resources-新增商品对象">`, `<a name="resources-真实制作后再食用">`, corpora table rows 正餐/家庭储备 | Object JSON with `energy_kcal_per_serving`; cooking how-to. |
| `docs/manual/scenarios.md` | `<a name="scenarios-看懂场景的组成">` | `recipe_output_bindings` field. |
| `docs/manual/personas.md` | `<a name="personas-在场景中填写人物">` line 270 | "做饭必须有真实食材包到正餐的库存转换". |
| `docs/design/02-exchange-engine.md` | `<a name="exchange-engine-食品交换的规则调用">`, `<a name="exchange-engine-实现职责与证据入口">` | Rule lookup for eating; `inventory_recipe.py` as calculator entry. |
| `docs/design/03-resource-engine.md` | `<a name="resource-engine-资源内容和语料的关系">`, `<a name="resource-engine-可计量资源定义">`, `<a name="resource-engine-交换对象与内容条目">`, `<a name="resource-engine-地点商品与门店供给">`, `<a name="resource-engine-文件职责">` | Food corpus→object with kcal basis; energy resource in catalog; ingredient/recipe publication; `off-foods.mapping.toml`. |
| `docs/design/04-simulation-engine.md` | `<a name="simulation-engine-实际区间身体变化与效率">` | 进食 row is compatible; only the "交换引擎使用…利用效率" wording needs the rule source restated. |
| `docs/design/08-configuration.md` | `<a name="configuration-加载与校验">`, `<a name="configuration-地图引用与门店供给初值">` (lines 306–308), `<a name="configuration-身体资源初值">` | `recipe_output_bindings`; cooking declaration; body initial values "受目录能量上界约束". |
| `docs/design/00-overview.md` | `<a name="overview-需求映射表">` rows 「购买食品与实际进食分别结算」「能量与体感」 | Reference 交换投入产出 for food quantity. |
| `docs/ARCHITECTURE.md` | line 49 「闭集规则与品类配方」 | Architecture diagram names category recipes. |

Clauses that do NOT conflict and can stay verbatim: `<a name="persona-prompt-交互语义-进食">`, `<a name="spec-life-runtime-具体活动结算">` 进食 row, `<a name="spec-life-realism-商务与进食">`, `<a name="spec-dayrun-behavior-共同时间与身体结算">`, `<a name="spec-life-runtime-其他体感分级">`, `<a name="spec-life-runtime-睡眠不足账与效率曲线">`, `<a name="time-and-energy-能量不拦人-*">`, `<a name="dayrun-fidelity-身体与需要-区间事实只有一个来源社交账与共同用餐">`, `<a name="io-formats-bodystate身体状态">`, `<a name="recording-engine-事件正文与资源证据">` (minus recipe outcome), `<a name="contracts-领域枚举">` `data.opened`.

---

## 7. Blast radius

Outside-subsystem code that reads the shapes being deleted/changed:
- Storefront/purchase: `basket.purchase_order` (same file), `purchase_offers.py`, `named_purchase.py`, `storefront_runtime.py`, `discovery.py` (`energy_kcal_per_unit`, `use_duration_seconds` in supply rows), `semantics.purchase_amount` (purchase pairing coefficient → gone with rules), `ProductConfig.purchase_price_fen_min/max` and `category-assignment.json` kinds incl. `ingredient`.
- Tool layer / observation / prompt: `tool_layer._product_state` kcal label, `callable_index.py`, `observe.py` recipe detection, `interaction_menu.py` + `affordances.toml` way 「制作」 (`families.eat`, `cheapest_feasible_meal` generator), `prompt_schemas.py`, `readable.py`; `tests/integration/test_seven_layer_request.py` fingerprint `prompt_sha256` re-pins if any system-layer food wording changes.
- Errands: `errand_state.py` (`吃掉`, making evidence), `errands.py`, `evaluation/errand_metrics.py`; `tests/life/test_errand_completion.py::test_making_or_using_an_item_is_not_eating_it` parametrised over recipe products.
- Footprints/evaluation/delivery: `footprints/quality.py::_food_efficiency_samples`, `footprints/experiences.py` consumed facts, `footprints/purchases.py`, `evaluation/behavior_metrics.py::_meal_diversity`, `evaluation/public_evidence.py` eat/use classification, `delivery/windows.py`; `scripts/inspect_run.py` (`lowest_energy_before_meal`, felt band import), `scripts/render_day.py`; `custom/agents/life_memory.py` consumed fact line; `custom/agents/life_persona.py` `food_preferences`; `profile/coverage` food-preference targets (`CONTRACTS contracts-人物卡片覆盖率产出`).
- Catalog/categories packages: `categories/{loader,maintenance,pairing,contract,identity,cli}.py` and `worldmock category check/refine/publish` CLI, `catalog/{loader,builder,admission,inspect}.py`, `run_identity.py` (contract/rules sha fields in run manifest, `catalog_content_sha256`), `footprints` run-manifest validation; `tests/categories/*`, `tests/catalog/*`, `tests/corpora/test_storefront_release.py`.
- Resource ledger: `resources/ledger.py::ConvertEntry` only used by `inventory_recipe.py` in production — verify `tests/resources/*` before removing.
- Fixtures that must be regenerated/rewritten: all of `tests/fixtures/environment/**` (catalog + scenarios), `tests/catalog/admission_fixtures.py`, `tests/categories/conftest.py`, `tests/categories/scenario_fixtures.py`, `tests/life/purchase_worlds.py`, `tests/integration/storefront_fixtures.py`, `tests/fixtures/tool_interaction_habits.json` (cooking cases), day-run baselines `tests/fixtures/dayrun-layering-baseline.json`, `dayrun-shared-rules-baseline.json`, `dayrun_fidelity_inputs.json`, `dayrun_wc_named_purchase.json` (structure-6 inputs → new structure), `tests/fixtures/mutation-symbol-baseline.json` (recipe entries), `tests/fixtures/layering-public-api.json` (if `meal_windows_by_agent` or exported names change), `tests/fixtures/current-feature-owners.json` (if energy anchors are renamed), `tests/test_single_source.py::OWNERS` (if `CurvePoint`/`EfficiencyFactor`/`CONSUME_DURATION_CAP_SECONDS` move).
- Tests that break outright: everything in §4.1 referencing `energy_kcal_per_unit`, `recipe_*`, `exchange-rules`, `alert_rules`; plus `tests/life/test_category_runtime*.py`, `tests/categories/test_rules.py`, `tests/catalog/test_category_admission.py` meals/pantry tests, `tests/life/test_tool_interaction_habits.py`, `tests/integration/test_category_inventory_protocol.py`, `tests/life/test_parallel_admission.py` recipe/make cases, `tests/life/test_motivation.py` meal floor tests, `tests/life/test_public_observation_contract.py` kcal label tests, `tests/integration/test_dayrun_complete_days.py` and `tests/test_dayrun_baseline.py` (fingerprints), `tests/test_time_and_energy_completeness.py` (registered node names), `tests/test_persona_prompt_acceptance.py`, `tests/test_tool_interaction_acceptance.py`, `tests/test_errands_workday_acceptance.py`, `tests/test_mutation_runner.py`, `tests/test_documentation_gate.py` (doc→test references), `tests/test_design_doc_build.py` (design HTML must be rebuilt after章节 edits).
- Docs outside the list in §6 that mention the deleted mechanics: `docs/dev/implementation-status.md` (真实料理 row, energy audit), `docs/dev/ANALYSIS-spec-coverage.md` rows 进食/能量感知/料理, `docs/dev/category-contract-*.md` ledgers, `docs/archive/tool-interaction/*` (history, leave), `docs/dev/SPEC-idle-fillers.md` rule 15 wording (平常的饭点再吃正餐 — compatible), `docs/design/worldmock-design.html` (regenerate), `docs/INDEX.md` table rows for category-contract docs.
- Cross-subsystem dependencies the spec must coordinate:
  1. Store→food binding and `all` expansion (storefront/map subsystem) defines the supply rows that `basket`/`motivation` consume; the food catalog is the single price source (no per-store quote/price_version) — affects `PurchaseItem.price_version`, `SupplyQuoteState`, `supply_stock`.
  2. App/delivery subsystem: delivered food enters `AgentState.inventory` via `commerce.add_holding` — keep that entry point.
  3. Durables (`durable` category, `use` mode, `active_effects`, `work_notes_multiplier`): the proposal keeps non-food goods under "系统固定类型"; the spec must say whether `DurableUseRule`/`effect_slot` become a fixed engine table or whether durables leave the catalog.
  4. Errand outcome 「取得」 loses the making branch; 「吃掉」 unchanged.
  5. Run identity must bind the engine rule version now that kcal/durations/body constants no longer hash through the environment.
  6. Prompt layer: system-layer rules 15/16 (hunger, meals) and tool-layer `state` wording — any text change re-pins `prompt_sha256`.

---

## 8. Open questions for the spec

- Q1 **Body constants ownership.** Proposal table says WorldMock owns "分类、允许的行为、服务能力、资源单位与交换公式" and "食品热量、交通速度、娱乐效果、推送策略、配送规则", but does not name `basal_kcal_per_hour`, `activity_met`, `maximum_energy_kcal`, `hunger/emergency_threshold_kcal` (today in Dataset `resources.jsonl`). Decide: move them into an engine-packaged table (consistent with "不新增可编辑规则配置层") or keep `resources.jsonl` as the one remaining environment rule file. Frozen clauses `time-and-energy-能量感知-饥饿在低能量时不失灵` ("目录 alert_rules 仍是 800、400 的来源") and `spec-life-runtime-身体参数与单位` hinge on this.
- Q2 **Food utilization efficiency.** Proposal: "身体净能量仍经过现有利用效率、上限和消耗结算… 是否删除效率应成为单独设计决定" (engine-evidence). Keep `sleep.food_utilization_efficiency` (0.90–0.80) or drop it so that 600 kcal → 600 kcal? Affects `apply_food`, `consumption.food_efficiency`, quality report `efficiency.food`, `food_energy_kcal_per_hour`.
- Q3 **Decision row 「做饭是否保留」** (recommended: delete). If "保留为固定耗时生活活动", decide its `lifestyle` item identity and that it never produces food; `inventory_recipe.py` is deleted either way.
- Q4 **Tool-layer kcal label.** `time-and-energy-能量感知-商品热量与路线费力` writes 「每份约 N 千卡」 per food. With fixed categories, write the category name (零食/饮品/正餐), the fixed kcal, or both? The proposal says the person chooses by "名称、价格、摘要和画像", suggesting category + name is enough.
- Q5 **Decision row 「食品常数」**: confirm 200/100/600 kcal and 5/3/20 min, and whether the 60-second minimum (`portion_duration`) and the 14400 s cap (`CONSUME_DURATION_CAP_SECONDS`) stay.
- Q6 **Durables.** Do non-food goods keep `use`/`effect`/`work_notes` semantics via a fixed engine type, or leave the catalog entirely? Determines whether `InventoryItemConfig` survives as a durable shape and whether `validate_meal_use`/`_apply_item_delta` keep a `use` branch.
- Q7 **Snack/drink and meal candidates in the internal scorer.** `MEAL_KCAL_FLOOR=300` would exclude snacks and drinks forever; restate `spec-life-realism-动机层` by category (meal first, snack/drink fallback) or drop the floor.
- Q8 **Motivation/errand "正餐只吃一次" wording** (system layer rule 15/16, idle-fillers): with a `meal` category available, should "每个饭点的正餐只吃一次" bind to the category `meal`? Only prompt text; confirm whether re-pinning `prompt_sha256` is in scope.
- Q9 **Home pantry and initial holdings.** `spec-life-realism-世界素材品类要求` 家的可供性 (7 breakfast servings) is corpus-driven today; keep as a scenario `initial_inventory` convention or drop the requirement?
- Q10 **Portion label precision.** `portion_phrase` and the 1e-9 inventory precision (`spec-life-realism-商务与进食`) are unchanged by the proposal; confirm they stay so that `test_dayrun_inventory_regressions` survives as the lock for "拆分、暂停、恢复总量不变".
- Q11 **Run identity.** Which version string binds the fixed food/body rules (package version, a `RULES_VERSION` constant, or a hash of the packaged table)? Proposal §「主提案复核」 requires it; today `run_identity.py` only hashes environment files.
