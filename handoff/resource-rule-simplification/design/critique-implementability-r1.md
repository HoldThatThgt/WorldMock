# 对抗审查 · 可实现性、可测试性与门禁合规 · 第 1 轮

审查对象：`D:/code/WorldMock/.claude/worktrees/rrs/docs/dev/SPEC-resource-and-rule-simplification.md`（未跟踪文件，HEAD `35d0b610fb4a35304e66619048d7500d2d1e05c7`）。
输入全部读完：spec-brief、rulings-final、SYNTHESIS §1–§7、rulings-maps、rulings-draft、references、提案与三份证据、两份模板规格、INDEX 状态与权威层级、design/README 更新约定、idle-fillers 台账三件套、相关子系统图；代码核对全部在工作树上以只读方式进行。

## 0. 机械检查结果（通过项）

| 检查 | 结果 |
| --- | --- |
| `python scripts/documentation_gate.py`（工作树） | 扫描 154 份文档（`rglob("docs/**/*.md")`，含未跟踪的本规格），problems 0，PASSED |
| `pytest tests/test_documentation_gate.py -q` | 59 passed，3 failed：`test_archive_snapshot_disclaims_unreconstructible_execution`、`test_archive_relative_links_resolve_from_original_path_without_rewriting_body`、`test_exact_reference_source_is_explicit_and_does_not_search_other_baselines`。三者都是 `tmp_path` 上临时 git 仓库的归档快照用例，在主检出 `D:/code/WorldMock`（没有本规格）同样失败，与本规格无关，没有任何失败点名本规格 |
| 锚点 | 53 个 `<a name="spec-rrs-…">`，无重复；每个标题上一行都有锚点；标题锚点 `spec-rrs-资源与规则引擎简化开发规格` |
| 文件头 | `状态：FROZEN`、`日期：2026-10-06`、`代码基线：35d0b610…`（= `git rev-parse HEAD`）、`来源：[提案](…)`、`范围：` 齐全 |
| 链接与锚点引用 | Markdown 链接全部解析；405 处反引号 `path#anchor` 全部在目标文件里存在 |
| `path::Name` | 39 处 `::` 引用中 38 处在基线存在；1 处见 M2 |
| 禁用写法 | 没有 `file.py:123` 行号引用；`延期/跳过/待实现` 只出现在「不留延期项」「未被跳过」「夏令时跳过的小时」这类既有规格同款用语里，不命中 `tests/acceptance_guard.py::MARKERS`（待定/尚需定稿/待实现）与 `test_docs_carry_no_pending_markers_of_this_feature` 的四个标记 |
| 版本表 | 末节、一行 `2026-10-06-rrs-freeze`、表内无空行 |
| 常数与依据 | C-1（1.2 / 7.0 / 5.0 / 600 / 500、车费 `max(500, ceil(0.2×m))`）、C-2（2、⌊(n−1)/2⌋、[200, 2000]、`random_labeled_tree`）、C-3（0.2/0.2/0.3/0.4/0.3）、C-4（200/300、100/180、600/1200）、C-5（1800）、C-6（08–21、每人每小时 1、每 app 每 session 1、200 字符）、C-7（Windows 基线）、C-8（`summary_source`）与 rulings-final §B 逐项相等；依据列的参考与 references.md §1–§5、§7、§8 一致，实验假设都标明 |
| 提案「必须钉住的行为」十三行 | 契约点表逐行覆盖（括注原行名） |
| 台账形状 | `tests/rrs_acceptance.py` 的 `STAGE_IDS / IMPLEMENTATION_STAGES / REPORT_ONLY_STAGES / STAGES_DONE / FEATURE_PRODUCTION_FILES / FEATURE_TEST_FILES / BEHAVIOUR_CASES / MUTATION_ROWS / MUTATIONS / RECEIPTS` 与 `tests/idle_fillers_acceptance.py` 同形；JSON 行 `{stage, spec, tests[], implementation[]}` 与 `idle-fillers-acceptance.json` 同形；只报告行含「只报告」；阶段集合满足守卫的 `IMPLEMENTATION_STAGES < STAGE_IDS` 与互斥断言 |

## 1. 规则到代码路径的追溯（18 条）

| 规则 | 追溯到的基线代码 | 结论 |
| --- | --- | --- |
| D-3 / C1 推送资格取发出时刻的 session；到达当秒的推送属于新停留 session | `src/worldmock/life/time_advance.py::TimeAdvanceCoordinator.advance` 先 `_advance_activities`（内含 `_switch_session_on_arrival`，第 242 行）再 `_process_scheduled`（第 129–130 行）；`sessions.current_session` 取唯一 ACTIVE session | 与今天的执行次序一致，无歧义 |
| C5 / T36 夏令时按 `shift_span` 换算 | `work_schedule.shift_span` 以当地零点 + `timedelta(minutes)` 构造再 `timestamp()`（fold=0：跳过的小时取跳变前偏移、重复的取第一次） | 可实现；夹具时区 `Asia/Shanghai` 无夏令时，T36 需经 `mutate` 换时区（规格已允许） |
| `ScheduledOutcome.wake/source_kind` 取代 `_SCHEDULED_WAKE_FLAGS` | `scheduling/effects.py::ScheduledOutcome{state, data}`；`time_advance.py:75,407` 的 `_SCHEDULED_WAKE_FLAGS` | 可实现 |
| D-23 唤醒词表 25 → 22 | `life/schedule.py::WakeReason` 25 个；`SLEEP_ALLOWED_REASONS` 四项（第 72 行）；四个被删成员的源在 `schedule.py:397–408`、`rhythm.py:512,514` | 可实现，但阶段归属矛盾（见 B2） |
| `ScheduledKind` 删五加一 | `scheduling/events.py:23–37`；`WORK_AVAILABLE/TASK_EXPIRED` 的消费者只有 `scheduling/compile.py`、`effects.py` | 可实现，阶段归属矛盾（见 B2） |
| D-48 阶段身份只剩 `action`、拒绝 `work_review` | `run_setup.validate_stage_model_identity` 要求 `set(stages) == {"action","work_review"}`，退役阶段名经 `RETIRED_DECISION_STATEMENT`（`footprints/models.py`）拒绝 | 机制可复用；规格点名的常量名错（见 M1） |
| D-10 / D-20 工时由活动推导 | `life/work.py:280 work_totals(state, agent_id, task_id)` 已存在；`ActivityState.elapsed_seconds`（models.py:476）由 `activities._advanced_activity` 推进（第 370 行），暂停的活动不推进 | 可实现 |
| D-50 / B-6 不足 60 秒拒绝 | `work.py:107,109` 今天是两句「…before the deadline…」「…in the contract…」，下一步「Submit the completed work.」；`tests/life/test_grounded_observation.py:222–223`、`tests/life/test_paused_slots.py:143` 钉旧句 | 见 B5 |
| D-35 / B-5 拒绝码与原因配对 | `life/error_contract.py::NAME_REJECTION_CODES`；`tests/life/test_error_contract.py::test_every_business_rejection_code_raised_in_the_source_has_a_specific_reason` | `APP_NOT_BOUND` 没有原因（见 B4） |
| D-14 / 观察 目标类与可调用索引 | `prompt_schemas.py:32 TARGET_KINDS`（含 content、game、catalog）；`callable_index.py:44 INDEX_KEYS = (lifestyle, contents, goods, query_categories)` | 可实现 |
| B-1 系统层按位置改写 2、7、8、12、13、17 | `custom/agents/life_prompt.py::LIFE_SYSTEM_LAYER` 第 12 条 =「名称照工具层…」、第 13 条 =「start_activity 一项一个对象…」、第 17 条 = 工作条；`docs/CONTRACTS.md#contracts-model-request` JSON 块 == 模块常量（本机核对为 True）。定稿文本核对 `tests/integration/test_seven_layer_request.py`：「待办」只在 [0,3,5,11]（新第 12 条含「待办层」）、「上班安排」只在 [0,13]、第 8 条四短语、第 7 条两短语、「最接近的候选，不会替你改」在 rules[11]、无语言词 | 满足全部既有断言；位置映射正确（SYNTHESIS 的 11/12/16 确为 12/13/17） |
| D-33 商家账户 | 夹具账户角色 `employer:test`、`transport:service`、`merchant`、`external:scenario`；`semantics.py:201` 购买转账角色 buyer→merchant | 可实现（`merchant` → `merchant:<location_id>`） |
| D-32 / B-11 出行 | `travel.planned_travel`（travel.py:49）、`guidance.py:90 ROUTE_DURATION_MISMATCH` | 可实现；原因句过时（M12） |
| D-28 / D-29 地点类 | `readable.PLACE_TYPES`（含 merchant）、`retrieval.LOCATION_KINDS` 六个活动环境 | 可实现 |
| 占用闭集 7→8、十三个退役名 | `catalog/affordances.py:44 RESOURCE_CLAIM_SLOTS`、`:49 _CLAIM_CONDITION_DIMENSIONS={"mode","content_kind"}`、`game_boundary`、`attention_required_kinds` | 可实现 |
| D-47 守卫放宽 | `tests/test_layering_acceptance.py:139 assert len(expected) == 147`；归档断言钉 `2cf23011…`；`tests/test_current_feature_owners.py:35` OWNERS 恰为五个 | 可实现 |
| B-13 生成器 | `pyproject.toml` `networkx==3.6.1`（uv.lock 锁定；`random_labeled_tree` 自 3.2 起存在） | 可实现（见 M14） |
| D-52 `random_state` | `WorldState.random_state: str`（sha256，`world_setup.py:126` 写一次），只被 `time_advance._ready_agent_ids` 读 | 可实现；措辞见 M11 |
| D-8 / F7 食品常数与动机 | `motivation.py:106 MEAL_KCAL_FLOOR`、`sleep.food_utilization_efficiency`、`projection.food_energy_label(kcal_per_unit)`、`basket.py:117 ORDER_DUPLICATE_SUPPLY` | 可实现 |
| 环境格式 / 旧文件拒绝 | 夹具 `catalog/` 今天恰有 `category-contract.json`、`exchange-rules.json`、`objects.jsonl`、`resources.jsonl`、`rules/`；`manifest.json` 带 `version_major/minor` 与四个品类身份字段 | 与 `ENVIRONMENT_STALE_FILE` 清单一致 |

## 2. 阻断性发现

### B1 阶段 1 删除的整文件被阶段 2–5 的文件导入：阶段 1 提交后引擎不可导入，T13、[QT] 与 `test_import_graph.py` 无法通过

阶段表把 `src/worldmock/categories/`、`life/map_models.py`、`map_data.py`、`travel_time.py`、`opening_hours.py`、`storefront_state.py`、`inventory_recipe.py`、`category_contract.py`、`corpora/storefront.py`、`profile/coverage_apps.py`、`profile/taste.py` 的删除放在阶段 1，而它们在基线的导入方（`rg` 核对）归阶段 2–5：

- `worldmock.categories` ← `life/semantics.py`、`world_setup.py`（阶段 2）、`basket.py`、`session_actions.py`、`tool_layer.py`（阶段 3/4/5）
- `life.opening_hours` ← `scheduling/effects.py`、`basket.py`、`discovery.py`、`motivation.py`、`presentation.py`、`rhythm.py`、`tool_layer.py`
- `life.storefront_state` ← `engine_context.py`、`world_setup.py`、`semantics.py`、`basket.py`、`purchase_offers.py`、`tool_layer.py`、`effects.py`
- `life.inventory_recipe` ← `observe.py`、`session_actions.py`、`time_advance.py`、`tool_layer.py`
- `life.map_models` ← `scenario.py`、`semantics.py`、`motivation.py`、`order_routing.py`；`profile.coverage_apps` ← `run_setup.py`、`coverage_targets.py`、`load_checks.py`；`profile.taste` ← `run_setup.py`、`profile/__init__.py`

后果：阶段 1 提交后 `import worldmock.life`、`worldmock.scheduling.effects`、`worldmock.run_setup`、`worldmock.validation`（`validation.py:26` 导入 `world_setup`）全部 ImportError。T13（`worldmock validate --environment` 要「加载 `scenarios/` 下每个场景」，走 `cli.py:121 validate_project`）、T11 的包资源核对经 `catalog` 尚可，但阶段 1 拥有的 `tests/test_import_graph.py`、`tests/test_environment_profiles.py` 与 `[QT] tests/integration/test_external_environment_lifecycle.py`（SYNTHESIS §7 P2 明示「must keep passing on the new fixture from stage 1」）都不可能绿；同时阶段 1 把三份夹具场景改成结构 7，而接受结构 7 的 `life/scenario.py` 归阶段 2，T3/T13 在阶段 1 的「人工环境」实际是加载器拒绝的环境。阶段表声明只允许「五个既有特性守卫、layering、owners 暂红」，没有覆盖这一状态。

修复（二选一，建议前者）：
1. 把 SYNTHESIS §7 的 P3 并回阶段 1（与 §7 一致）：阶段 1 另拥有 `src/worldmock/life/{scenario, models（结构与 DeliveryState 部分）, world_setup, engine_context, run_identity, semantics, load_checks}.py`、`src/worldmock/runtime.py`、`src/worldmock/validation.py`、`src/worldmock/run_setup.py`（清单 3、口味注入删除），并在同一提交对 `basket.py`、`rhythm.py`、`scheduling/effects.py`、`discovery.py`、`motivation.py`、`presentation.py`、`purchase_offers.py`、`order_routing.py`、`observe.py`、`session_actions.py`、`time_advance.py`、`tool_layer.py`、`profile/coverage_targets.py` 只做「删除对被删模块的导入与调用」的最小改动（阶段表写明「阶段 1 只删导入与调用，行为改写仍归各自阶段」）；契约点「场景 7 与世界 10 的拒绝边界」（T14–T16）与 `tests/integration/test_external_environment_lifecycle.py` 改为阶段 1；阶段 2 只剩 P4。
2. 或者把每个整文件删除挪到删除其最后一个导入方的阶段（`categories/` 与 `map_*`、`travel_time` → 阶段 2；`opening_hours`、`storefront_state`、`inventory_recipe`、`category_contract` → 阶段 3；`coverage_apps`、`taste` → 阶段 3），并同步改「删除清单·生产模块」表的阶段列与 `ENVIRONMENT_STALE_FILE` 的生效阶段。

### B2 闭集成员的删除阶段与其最后消费者的删除阶段不一致（WakeReason、ScheduledKind、环境事件类型、`location_hours_change` 的来源）

- 「错误码与唤醒、预排名字」表与「生产符号」表都把 `WakeReason.TASK_AVAILABLE/TASK_DUE_SOON/WORK_REVIEW_RESULT/LOCATION_HOURS_CHANGE`、预排 `work_available/task_expired`、`environment_event.data.kind` 的 `work_available/task_expired` 放在阶段 4；但它们的消费者 `schedule.py:397–408`（`_clock_reasons`）、`rhythm.py:512,514`、`WakeSnapshot.task_newly_visible/next_task_due_at/work_review_returned`、`scheduling/compile.py` 任务循环、`effects._reveal_event` 全部列在阶段 5 的符号行。阶段 4 提交删除枚举成员即 AttributeError。
- `LOCATION_HOURS_CHANGE` 的来源 `rhythm._closing_at/_just_opened/_location_change_times`、`WakeSnapshot.location_closing_at`、`NextDecisionQuery.location_hours_change_times` 列在阶段 5，而 `rhythm.py:32` 导入的 `opening_hours.py` 在阶段 1 删除（与 B1 同源）。
- T39「唤醒词表 22 个」登记在阶段 4，而按上述归属 22 只能在阶段 5 达到（阶段 1 删 1、阶段 4 加 1、阶段 5 删 3）。

修复：把四个 WakeReason 成员中的三个任务类成员、两个 ScheduledKind 成员、两个环境事件类型改到阶段 5 行（与任务循环同一提交）；把 `WakeReason.LOCATION_HOURS_CHANGE`、`rhythm._closing_at/_just_opened/_location_change_times/_warning_signatures["closing"]`、`WakeSnapshot.location_closing_at/location_just_opened`、`NextDecisionQuery.location_hours_change_times/location_closing_at`、`schedule.CLOSING_WARNING_SECONDS` 改到阶段 1 行（与 `opening_hours.py` 同一提交）；「闭集」表 `WakeReason` 行补「阶段 1 为 24、阶段 4 为 25（加 `app_push`）、阶段 5 为 22」；T39 与「足迹侧词表、事件字段用例、查看器计数一致」改登记为阶段 5 的契约点「唤醒词表 22」（「时间窗口精确」只留 T36），`tests/life/test_wake_reasons.py`、`tests/footprints/test_event_fields.py`、`tests/tools/run_viewer*.test.cjs` 的 22 计数改写挪到阶段 5 的测试行；D-23 的「九个同步点」注明在阶段 4（加）与阶段 5（删）各执行一次。

### B3 推送与打开的关联规则自相矛盾：「只有 `pending` 才取推送内容」与「提前停止后重开仍命中」不能同时成立

「娱乐·活动生命周期」：「当前 session 的 `app_pushes[app_id]` 为 `pending` 时取推送内容（`pinned_by_push_id`，状态置 `opened`）」。「推送与消费的关联」与 T37：「中途停止、本 session 再打开仍从该内容剩余部分继续（仍是 pending→opened 的同一条推送）」。首次打开后状态已是 `opened`，再打开按生命周期规则会走随机抽取，T37 必失败；按关联规则实现则生命周期句为假。两处都在「定义」层面，实现者无法判定。

修复：生命周期句改为「当前 session 的 `app_pushes[app_id]` 存在且 `status != released` 时取推送内容（首次打开把 `pending` 置 `opened`，再次打开沿用 `opened`，`pinned_by_push_id` 都写该 `push_id`）」；关联句括注改为「（同一条推送，状态自首次打开起为 `opened`）」；质量报告 `opened_in_session` 定义为「状态曾置 `opened`」。

### B4 `APP_NOT_BOUND` 没有英文原因与下一步；B-5 指向的「活动受理拒绝码」表在本规格里不存在；B-14 的 `unmet = delivery_app` 没有任何规则使用

B-5 写「新错误码的英文原因与下一步见「注册校验与错误」与「活动受理拒绝码」两张表」，全文只有前一张表（登记码）。`APP_NOT_BOUND`（D-35、外卖、T24）全文没有原因句；`ACTIVITY_PRECONDITION_UNMET` 的 `unmet = delivery_app`（B-14）没有触发规则——而 D-35 把 `APP_NOT_BOUND` 定成独立码，SYNTHESIS §4.8 又写成 `APP_NOT_BOUND … / unmet delivery_app`，规格两种写法并存。`tests/life/test_error_contract.py::test_every_business_rejection_code_raised_in_the_source_has_a_specific_reason` 要求每个抛出的码有专属原因，T24 要断言原因，现状不可测。

修复：在「店铺、食品与外卖」末尾新增 `<a name="spec-rrs-活动受理拒绝码"></a>### 活动受理拒绝码` 表（码 | 条件 | 英文原因 | 下一步），至少含：`APP_NOT_BOUND` ｜ 远程下单的 app 不在 `apps_by_store[store_id]` ｜ "This store cannot be ordered through that app." ｜ "Order through <app bound to the store>."（店铺无 app 时走 `FULFILLMENT_NOT_SUPPORTED`）；`APP_CONTENT_EMPTY` ｜ "This app has no content to use." ｜ "Choose another app."；`FULFILLMENT_NOT_SUPPORTED` ｜ "This store has no delivery app." ｜ "Go to <store>."；`ACTIVITY_PRECONDITION_UNMET`（`delivery_destination`、`already_at_destination`、`required_location_id` 三句）；`ACTIVITY_DURATION_INVALID`（见 B5）；`WORK_TOPIC_NOT_FOUND`、`ORDER_DUPLICATE_ITEM`（把现在散在各节的句子集中并保留原处引用）。B-14 删去 `delivery_app`（或改写为「`APP_NOT_BOUND` 不写 `data.unmet`」）。

### B5 D-50 / B-6 的主题不足 60 秒拒绝：句子是新句而非「保留」，下一步未定义，钉旧句的测试与合同行未列入阶段 5

基线 `work.py:107,109` 的两句是「Less than one minute remains before the deadline; work cannot start.」「…remains in the contract; …」，下一步「Submit the completed work.」（`docs/CONTRACTS.md#contracts-活动受理拒绝码` `ACTIVITY_DURATION_INVALID` 行）。规格 D-50 写「`work_start_refusal` 保留 "Less than one minute of the topic remains; work cannot start."」——这是替换；新下一步没有定义（旧下一步随 `submit_work` 消失）；钉旧句的 `tests/life/test_grounded_observation.py:222–223`（参数化 `contract`/`deadline`）与 `tests/life/test_paused_slots.py:143` 不在阶段 5 的测试行，合同 `ACTIVITY_DURATION_INVALID` 行不在阶段 5 的文档行。

修复：D-50 改「`work_start_refusal` 的原因改为 "Less than one minute of the topic remains; work cannot start."，下一步 "Choose another activity."，取代今天的截止句与合同句」；阶段 5 测试行加 `tests/life/test_grounded_observation.py`（参数化改为单一 `topic` 句）；阶段 5 文档行 `docs/CONTRACTS.md#contracts-活动受理拒绝码` 补「`ACTIVITY_DURATION_INVALID` 工作分句改主题剩余与新下一步」。

### B6 登记守卫「同形复制」读不到变异表：`section_text(…, "spec-rrs-验收台账")` 到下一个 `##` 为止，而变异表在独立的 `## 变异登记`

`scripts/documentation_refs.section_text` 取到「下一同级或更高标题为止」（本机核对：验收台账一节含「行为用例」「机器登记」两个 `###`，不含 `| 变异 | 阶段 | 被谁杀死 |`）。`tests/test_idle_fillers_acceptance.py::_spec_table(MUTATION_HEADER)` 在台账一节内解包唯一一张表，复制到 `tests/test_rrs_acceptance.py` 会在 `test_mutation_rows_match_the_spec_table_and_cover_done_stages` 处 ValueError。规格「机器登记」没有说明这一差异。

修复：「机器登记」第三条补「守卫以两个锚点常量读表：契约点表与行为用例表取 `spec-rrs-验收台账`，变异表取 `spec-rrs-变异登记`（`section_text` 到下一同级标题为止，变异登记是独立的二级节）」；或把变异表改成「验收台账」下的 `### 变异登记`（锚点不变）。

### B7 「闭集」表的 `WAYS` 行只列 8 个方式，而基线 `vocabulary.WAYS` 保留的还有 步行、驾车、工作、交谈、通话、阅读、等待、睡觉

本节自称「闭集…的唯一定义处，其余各节只引用」；按该行实现会删掉八个仍在用的方式（`START_ACTIVITY_ACTIONS` 行却保留了它们的动作）。

修复：`WAYS` 行成员改为「步行、驾车、工作、交谈、通话、阅读、进行、等待、睡觉、到店、远程下单、购买、打开、食用、使用、给予（16 个）」，变化列不变；`ACTION_WAYS` 补 `use_app → 打开`、删 `browse/watch/play`。

### B8 基线不存在的测试名与路径被当作既有定义引用

- 「测试与夹具」阶段 6：`test_progress_schema_registers_the_nine_record_types` 不存在；基线是 `tests/life/test_prompt_layers.py::test_progress_schema_registers_the_eight_record_types_with_both_errand_records`。
- 「测试节点（文件保留）」阶段 4：`tests/life/test_observation_agent_seam.py` 不存在；节点 `test_real_game_observation_starts_visible_game_and_restores_activity` 在 `tests/integration/test_observation_agent_seam.py`。

修复：两处改为上述真实名称与路径（R9：不引用不存在的定义）。

## 3. 次要发现

| 编号 | 位置 | 问题 | 修复 |
| --- | --- | --- | --- |
| M1 | 状态、事件…「运行清单 3」 | 「`validate_stage_model_identity` 拒绝 `work_review`（`RETIRED_STAGE_IDENTITY_KEY` 加入）」：该常量是清单键 `two_stage_model_identity`；退役阶段名经 `footprints/models.py::RETIRED_DECISION_STATEMENT` 判定 | 改为「与 `RETIRED_DECISION_STATEMENT` 同一机制：新增退役阶段名常量含 `work_review`，`set(stages) == {"action"}`」 |
| M2 | 生产符号表阶段 1 | `worldmock.life.semantics::validate_assortments` 调用——定义在 `worldmock.corpora.storefront::validate_assortments`，`semantics` 只是调用点 | 写「`worldmock.life.semantics` 对 `corpora.storefront::validate_assortments` 的调用」 |
| M3 | 娱乐·缓解；生产符号表 | 「`entertainment_relief_rate` 与 `content_entertainment_relief_rate` 去掉 `w` 参数」：基线签名是 `(activity, preference, config)` 与 `(rate, exposure_count, preference, config)` | 写「去掉 `preference` 与 `exposure_count` 参数」 |
| M4 | B-19 | 「`tool_schema_sha256` 在阶段 1、4、6 各变一次」不成立：`custom/agents/life_agent.py::life_tool_schema_sha256` 哈希 `_TOOL_MODELS` 全部 Schema + 注册表摘要，阶段 5 删 `REQUEST_MODELS["submit_work"]`（`_TOOL_MODELS` 随之变）也会移动它 | 改为「每个改动工具 Schema 或注册表字节的阶段都变：1、4、5、6」，设计章节的分阶段身份叙述随之 |
| M5 | 机器登记 `FEATURE_PRODUCTION_FILES` | 同形守卫断言生产文件根只在 `{src, custom, tools}`；本特性改 `scripts/render_day.py`、`scripts/inspect_run.py`、`scripts/bootstrap_environment.py`、`pyproject.toml` | 写明「守卫的生产根集合为 `{src, custom, tools, scripts}`；`pyproject.toml` 与锁文件不列入」 |
| M6 | 场景结构 7 | `agents[].initial_inventory` 列在「新增」，但 `AgentConfig.initial_inventory: dict[str, dict[str, float]]` 已在基线 | 挪到「保留」并注明键改为目录 `food_id` |
| M7 | 实施阶段、T56 | 「六个登记守卫」没有点名：现有守卫文件是 `test_tool_interaction_acceptance.py`、`test_dayrun_fidelity_acceptance.py`、`test_persona_prompt_acceptance.py`、`test_errands_workday_acceptance.py`、`test_idle_fillers_acceptance.py`、`test_time_and_energy_completeness.py`、`test_shared_rules_spec.py`、`test_layering_acceptance.py` | 列出文件名（含本特性的 `test_rrs_acceptance.py`），别处只引用 |
| M8 | 验收台账 | 提案反例「相同…动作序列得到相同地图」只由 T8（阶段 1 行）覆盖、「恢复不会重复产生通知」只由 T38 覆盖，但「随机和状态可恢复」「时间窗口精确」两行没有引用它们 | 「随机和状态可恢复」加 T8，「时间窗口精确」加 T38 |
| M9 | 变异登记阶段 8 | 「`FOOD_CONSTANTS` 在 `life/basket.py` 里再抄一份并从那里读取」不改变行为，违反本节「改变行为的源码变异」 | 换成行为变异（如 `evaluate_food_energy` 对三类都取 `meal` 热量，T27 杀死）或标注为结构变异并单列 |
| M10 | 测试与夹具阶段 1 vs 3 | 阶段 1 的 `tests/test_environment_profiles.py`「购买世界节点改在新构造器上」依赖阶段 3 才重写的 `tests/life/purchase_worlds.py` | `purchase_worlds.py` 重写（只编辑 `foods.json`/`locations.json` 并重钉清单）挪到阶段 1 |
| M11 | D-52、娱乐·确定性 | 「`random_state` 只在今天推进它的地方推进」——基线从不推进（`world_setup.py:126` 写一次，只被 `_ready_agent_ids` 读）；`draw_index` 的起值未定 | 改写为「`random_state` 不推进（基线只在 `world_setup` 写一次）」；写明「打开时 `draw_index = 0`，接续抽取从 1 起递增」 |
| M12 | 地图与出行 | `guidance.py:90` `ROUTE_DURATION_MISMATCH` 的原因「The persisted route duration differs from the frozen route.」提到冻结路线，规格只说「按图重算」 | 阶段 2 补一句原因改写（如 "The persisted route duration differs from the graph."） |
| M13 | T26 / 外卖 | 自送没有收货地参数，`DESTINATION_NOT_FOUND` 的触发路径未写 | 写明「给他人下单而收货人的收货地点不在场景 `locations[]` 时」 |
| M14 | B-13 | 只把 NetworkX 版本绑进 `MAP_GENERATOR_RULE_VERSION`；额外边用 `random.Random.sample`，Python 版本也决定序列 | 写明「以 `uv.lock` 的 NetworkX 与 `.python-version` 一起绑定」或改为逐对 `randrange` 的自写不放回抽样 |
| M15 | 门禁与平台 | `pytest tests/test_documentation_gate.py` 在本机有 3 个与本规格无关的既有失败（归档快照 tmp_path 用例，主检出同样失败） | 「门禁与平台」本机一行注明「文档门禁以 `scripts/documentation_gate.py` 的判定为准；`tests/test_documentation_gate.py` 的三个归档快照用例在本机既有失败，与本特性无关」，避免阶段 8「全绿」被误读 |

## 4. 其他核对（无发现）

- 阶段与 SYNTHESIS §7 的包映射：P0→0、P1/P2→1、P3/P4→2、P5/P6/P11→3、P7/P8→4、P9→5、P10→6、P12→7、P13/P14→8、P15→9；共享文件（`life/models.py`、`rules.py`、`time_advance.py`、`session_actions.py`、`observe.py`、`tool_layer.py`、`scenario.py`、`footprints/quality.py`）由阶段号给出顺序，「按节/按函数分工」句与 SYNTHESIS 一致；除 B1/B2 的删除归属外没有同函数无序改动。
- 人工环境：七地点、四 app、四食品、六内容、一主题、种子 7（6 树边 + 3 额外边 = 9）与 M12/B-7 一致；每个已选食品店铺（含无 app 的 `noodle-2`、`store`）都声明 `merchant:*`；`cafe` 的最小 `app_id` 是 `eleme` 与 T24 一致。
- 系统层定稿文本通过 `test_tool_description_references.py` 的约束（没有工具用途句、「只是登记」「available 为 false 的会被拒绝」各一次）。
- 删除清单里 463 个裸符号名除新名与测试内常量外都在基线源码出现；166 个文件路径除规格声明的新文件外都在基线存在（B8 两处除外）。
