> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/map-and-storefront-bob/discovery/bob-report.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

> 本页为审查报告阅读副本；导航链接指向原始证据包。包内保留未改动的原始报告、日志、补丁和复原记录。

**状态：COMPLETE**。本报告只覆盖下述九个发现、呈现与工具行为。九个有效变异全部被检出；另一次构造状态尝试为 invalid，保留原判定。没有把导入、语法或无关夹具错误算作 killed。

## 审查身份与边界

- 调用方仓库：`/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock`。
- 冻结基线：`2bce358a09046b84841bc5a2da68d82a66b79dbc`。
- 唯一规范：该提交中的 `docs/dev/SPEC-map-and-storefront.md`；本报告表中行号均指该提交。
- 隔离工作树：`/tmp/worldmock-map-bob-discovery-worktree.ihrV5l`，通过 `mktemp -d` 创建显式目录，再 `git worktree add --detach`。已恢复并移除。
- 输出目录：`/tmp/worldmock-map-bob-discovery/`。测试、文档、依赖、调用方源码均未修改；没有提交、安装、外网测试、付费调用或真实生产动作。
- 所有生产变异先解析与导入验证，再执行同一预声明 pytest 命令；每次立即还原并证明 diff/status 清空。

## 统计

| 指标 | 结果 |
| --- | --- |
| 映射的独立可观察要求 | 9 |
| 本范围内发现的 implementation missing / implementation diverged | 0 / 0 |
| 预声明基线命令 | 10；共 17 passed 测试实例，0 skipped |
| 变异尝试 | 10 |
| killed / survived / invalid / inconclusive | 9 / 0 / 1 / 0 |

相同测试可检验不同语义，因此测试实例数不表示唯一测试数。未计算规范覆盖率或 mutation score；本批没有穷举整个规范。

## 规范与当前实现映射

| 审查行为 | 规范精确锚点 | 生产路径及当前证据 |
| --- | --- | --- |
| 下一秒合法回灌 | `docs/dev/SPEC-map-and-storefront.md:62`：成功发现后安排下一秒的合法决策轮 | `src/worldmock/life/engine.py:822`；_after_success 明确 +1，并保存 discovery_result 唤醒原因。 |
| 保留同商品的多家供给 | `docs/dev/SPEC-map-and-storefront.md:64、72、76`：返回匹配供给，保留同商品不同门店身份与未知/不可达事实 | `src/worldmock/life/discovery.py:51`；_matching_rows 按地点和 supply_id 展开全部匹配，supply_row 保留商品引用。此反例检验不代替人物选定首家门店。 |
| 逐供给独立可见期 | `docs/dev/SPEC-map-and-storefront.md:76、78`：新呈现另一供给不能延长旧供给有效期 | `src/worldmock/life/discovery.py:170`；stage_visible_supplies 继承旧身份的原时刻，仅为本次身份写当前时间。 |
| 只授权实际呈现窗口 | `docs/dev/SPEC-map-and-storefront.md:68、78、80`：未呈现后续页不得授权；窗口外身份不得续期 | `src/worldmock/life/observe.py:372`；_discover_places 从实际返回行提取身份，再交 stage_visible_supplies。 |
| 游标查询、起点与地图绑定 | `docs/dev/SPEC-map-and-storefront.md:68`：游标绑定地图身份、查询和起点 | `src/worldmock/life/discovery.py:68`；_cursor_identity 纳入地图版本、哈希、选区、起点和排除 cursor 的查询；_cursor_offset 严格比较摘要。 |
| 世界版本推进后的游标（构造状态尝试） | `docs/dev/SPEC-map-and-storefront.md:68`：查询提交新世界版本不能使下一页失效 | `src/worldmock/life/discovery.py:68`；正常实现未将 mutable WorldState.version 或 current_time 纳入摘要。 |
| 菜单的环境重验 | `docs/dev/SPEC-map-and-storefront.md:82`：发现工具贯穿 Schema、本地校验与环境重验 | `src/worldmock/life/menu_execution.py:10`；require_interaction_menu 在当前地点重算菜单；实际模型 Schema/本地校验由同一现有测试断言。此变异单独打破环境层。 |
| 当前事实保留空值替换 | `docs/dev/SPEC-map-and-storefront.md:98`：changes 按键替换，空值是值，当前行可无损还原 | `src/worldmock/life/observe.py:834`；_current_supply_facts 比较所有键值，包括 None；观察投影调用该函数。 |
| 道路时间范围 | `docs/dev/SPEC-map-and-storefront.md:64、72、74`：按冻结道路的分时预计时长判定范围，不以距离替代时间 | `src/worldmock/life/discovery.py:141`；route_row 使用冻结路线和 travel_planned_seconds，range_status 比较 duration_seconds。 |

未在这些映射路径发现缺实现或直接违反规范的现有行为。该结论限定于表中可观察语义，不将文件存在、导入成功或基线通过等同为整体特性验收。

## 执行环境与基线

所有命令工作目录均为上述 detached 树，解释器固定为调用方的绝对 `.venv/bin/python`，没有使用 `uv run`。每次命令显式设置：

```text
PYTHONPATH=/tmp/worldmock-map-bob-discovery-worktree.ihrV5l/src:/tmp/worldmock-map-bob-discovery-worktree.ihrV5l
AGENTSOCIETY_LLM_API_KEY=test-only
LITELLM_LOCAL_MODEL_COST_MAP=True
PYTHONPYCACHEPREFIX=/tmp/worldmock-map-bob-discovery/pycache/<baseline-or-mutant>-<name>
```

每次使用独立 pycache 路径，避免同尺寸、同秒改写复用旧字节码。所有被改模块的 `module.__file__` 均位于临时树；逐次证明见对应 `*-import.json`，精确环境和值见 `*-pytest.json`。

| 预声明尝试 | 固定 pytest 命令 | 基线结果 |
| --- | --- | --- |
| 下一秒合法回灌 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q 'tests/life/test_storefront_recovery.py::test_discovery_purchase_consumption_and_recovery_preserve_storefront_evidence[pickup]'` | 1 passed in 2.22s |
| 保留同商品的多家供给 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_discovery_keeps_closed_sold_out_unreachable_unknown_and_outside` | 1 passed in 0.03s |
| 逐供给独立可见期 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_presenting_another_supply_does_not_renew_old_supply_visibility` | 1 passed in 0.03s |
| 只授权实际呈现窗口 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_real_discovery_round_reinjects_only_returned_supplies_and_fits_model_budget` | 1 passed in 0.48s |
| 游标查询、起点与地图绑定 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_discovery_rejects_cursor_for_other_conditions` | 4 passed in 0.03s |
| 世界版本推进后的游标（构造状态尝试） | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_discovery_paginates_every_match_across_world_versions_and_keeps_empty_places` | 1 passed in 0.03s |
| 菜单的环境重验 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_storefront_tools.py::test_menu_denies_discovery_in_schema_local_validation_and_environment` | 1 passed in 3.68s |
| 当前事实保留空值替换 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_changed_supply_fact_can_restore_null_nested_source_price_opening_and_stock` | 5 passed in 0.03s |
| 道路时间范围 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_discovery_keeps_closed_sold_out_unreachable_unknown_and_outside` | 1 passed in 0.03s |
| 世界版本推进后的游标（真实引擎尝试） | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_every_real_discovery_page_is_usable_after_world_advances` | 1 passed in 1.24s |

世界版本的真实引擎尝试是在原构造状态尝试被归类为 invalid 后，按父代理要求独立预声明、独立通过基线的追加尝试。原始 oracle 与 invalid 判定没有更改。曾声明但未执行的 current_time 方案保存在 `unexecuted-clock-plan.json`，不计基线或变异统计。

## 变异台账

下表命令严格使用上一节同名尝试的固定命令；每个日志均保存实际命令、环境、工作目录、退出码与完整输出。

| 变异 | 生产路径和单一行为改动 | 结果与因果证据 |
| --- | --- | --- |
| 下一秒合法回灌 | `src/worldmock/life/engine.py`：Delay successful discovery legal feedback from one second to two seconds. | **killed**；1 failed in 1.01s。真实发现后的 +1 秒调用 begin_decision_round 被 ProtocolError: Decision round contexts must match the scheduled agent order 拒绝；延迟到 +2 秒破坏规范要求的合法下一轮。该异常发生在真实动作链，非导入或启动错误。 `tests/life/test_storefront_recovery.py:194; src/worldmock/life/engine.py:277`。 |
| 保留同商品的多家供给 | `src/worldmock/life/discovery.py`：Retain only the first matching store/supply instead of the full matching set. | **killed**；1 failed in 0.12s。地点集合只剩 store-a，断言要求同商品的 store-a/store-b/store-u 全部保留。 `tests/life/test_storefront_discovery.py:94`。 |
| 逐供给独立可见期 | `src/worldmock/life/discovery.py`：Renew old supply timestamps when a different supply is presented. | **killed**；1 failed in 0.11s。旧 supply-a 的呈现时刻从 1767585600 被错误续期为 1767586500；字典精确断言失败。 `tests/life/test_storefront_discovery.py:155`。 |
| 只授权实际呈现窗口 | `src/worldmock/life/discovery.py`：Authorize every supply instead of only the actual presented page. | **killed**；1 failed in 0.55s。真实发现结算后的 visible_supply_ids 含未返回供给，无法等于实际页面 returned 身份集合。 `tests/life/test_storefront_discovery.py:279`。 |
| 游标查询、起点与地图绑定 | `src/worldmock/life/discovery.py`：Accept a structurally valid cursor without checking its query/origin/map identity. | **killed**；3 failed, 1 passed in 0.13s。query 与 map 两个参数均 DID NOT RAISE BusinessRuleError；非法条件游标被接受。origin 参数落入缺路线 fixture 的 StopIteration，单独不计有效杀伤；malformed 参数仍通过。 `tests/life/test_storefront_discovery.py:203`。 |
| 世界版本推进后的游标（构造状态尝试） | `src/worldmock/life/discovery.py`：Bind pagination cursor to mutable WorldState version. | **invalid**；1 failed in 0.12s。unit fixture 使用 model_construct，初始未声明 WorldState.version；第一页读取 version 触发 AttributeError，未进入翻页语义，因此不得算 killed。 `src/worldmock/life/discovery.py:71; tests/life/test_storefront_discovery.py:176`。 |
| 菜单的环境重验 | `src/worldmock/life/menu_execution.py`：Bypass environment menu recheck specifically for discover_places while schema/local restrictions remain. | **killed**；1 failed in 3.45s。实际 Schema 与本地校验仍禁止 discover_places，环境层绕过后 DID NOT RAISE INTERACTION_MENU_DENIED。 `tests/integration/test_storefront_tools.py:175`。 |
| 当前事实保留空值替换 | `src/worldmock/life/observe.py`：Omit null replacements from current-vs-historical supply facts. | **killed**；1 failed, 4 passed in 0.11s。quote、price_fen、price_version、quote_source 应替换为 null，却继承历史非空值；无损还原当前行断言失败，其他四个参数仍通过。 `tests/life/test_storefront_discovery.py:474`。 |
| 道路时间范围 | `src/worldmock/life/discovery.py`：Compare route distance in metres against a requested time in seconds. | **killed**；1 failed in 0.11s。400 秒路线应在 399 秒范围外；用 300 米比较后输出 inside，原断言要求 outside。 `tests/life/test_storefront_discovery.py:96`。 |
| 世界版本推进后的游标（真实引擎尝试） | `src/worldmock/life/discovery.py`：Independent predeclared real-engine attempt of WorldState.version cursor binding after the original unit-fixture attempt was invalid; original classification remains unchanged. | **killed**；1 failed in 0.60s。真实引擎的下一页返回 rejected，违反 completed 断言，日志明确为 DISCOVERY_CURSOR_INVALID；该独立尝试在父代理要求下另行预声明并通过基线，原 invalid 记录不改判。 `tests/life/test_storefront_discovery.py:312`。 |

每项完整补丁在 `mutant-<name>-patch.json`，完整测试输出在 `mutant-<name>-pytest.json`。可机读汇总为 `classified-results.json`，初始声明及追加声明为 `plan.json`；临时运行脚本为 `review_runner.py`。

## 测试判定边界

- **没有 survived mutant。** 本批不提出通过修改现有测试去掩盖失败或修复生产行为。
- 游标起点参数的构造夹具缺少改起点后的合法道路，删除绑定校验后出现 `StopIteration`。该异常没有被用作起点绑定的独立检出证据。当前绑定变异的 killed 来自 query/map 两个明确的 `DID NOT RAISE`；完整起点路线夹具与独立反例需要后续在新 clean 提交审查。
- 原世界版本尝试因初始构造状态缺 `version` 而 invalid。追加真实引擎测试已在第二页用 `completed → rejected / DISCOVERY_CURSOR_INVALID` 明确检出同一合同违规。
- 菜单尝试验证现有 Schema、本地校验和环境拒绝共同契约，并单独破坏环境重验；没有逐个枚举所有 Schema 字段或同时变异多个层次。
- 多门店尝试验证同商品替代供给不被静默裁成首家；没有穷举购买候选排序、全部轮转组合或所有资格条件。
- 道路时间尝试检出用距离单位替代时间的错误；没有运行在线路由服务，也没有声称本次重新验证原始路线真实性。
- 当前事实空值尝试直接调用生产差异投影函数；真实观察和实际模型消息预算另由本批真实发现页面基线经过，但本报告不声称逐个全链路变异了所有字段。
- 交易、日历、地图发布、长时仿真、外网与付费服务均不在本批变异范围。调用方全量门禁由父代理负责，本报告未借用其结果充当独立证据。

## 恢复与清理证明

每次变异后均执行：

```sh
git restore --source=2bce358a09046b84841bc5a2da68d82a66b79dbc -- <single-production-file>
git diff --exit-code
git status --porcelain=v1 --untracked-files=all
```

全部十次恢复退出码为 0，完整 porcelain 输出为空，且恢复后的文件字节与变异前完全相同。证据为 `restore-<name>.json`、`after-<name>-diff.json`、`after-<name>-status.json`。

最终 detached 检查：HEAD 等于冻结提交；`git diff --exit-code <baseline>` 为 0；full status 为空。随后从调用方仅执行：

```sh
git worktree remove /tmp/worldmock-map-bob-discovery-worktree.ihrV5l
```

该命令退出 0。目录不再存在，`git worktree list --porcelain` 无该注册。未使用 force 或广泛递归删除。证据为 `final-detached-*.json`、`worktree-remove.json`、`final-worktrees.json`。

清理后调用方重新确认：HEAD 仍为 `2bce358a09046b84841bc5a2da68d82a66b79dbc`，`git diff --exit-code` 为 0，`git status --porcelain=v1 --untracked-files=all` 完全为空。证据为 `final-caller-head.json`、`final-caller-diff.json`、`final-caller-status.json`。该确认已发给父代理，后续由父代理进行的新改动不属于本次审查恢复范围。
