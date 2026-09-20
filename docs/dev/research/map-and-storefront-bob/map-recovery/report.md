# Bob report

> 本页为审查报告阅读副本；导航链接指向原始证据包。包内保留未改动的原始报告、日志、补丁和复原记录。

状态：**COMPLETE**。本轮审查执行完成；存在 **两个 survived 测试断言缺口**，不表示验收已经闭合。

## 审查身份与隔离

- Caller：`/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock`。
- 基线提交：`2bce358a09046b84841bc5a2da68d82a66b79dbc`。
- 唯一规范：`docs/dev/SPEC-map-and-storefront.md`，SHA-256 `f5929dbf7a578b997f212758111dc1e02f5a9a3799e267eb4bc801ae7de12693`。
- 技能：`/Users/atishoo/.agents/skills/uncle-bob-review/SKILL.md`，本轮完整读取并遵循。
- 授权：父任务明确授权本地集成测试与隔离临时生产语义突变；没有外网、付费服务或生产系统操作。
- Detached 临时工作区：`/tmp/worldmock-bob-map-recovery.UkJseg`，已删除并注销。
- 报告与日志：`/tmp/worldmock-map-bob-map-recovery/`；没有复制回 caller，没有编辑测试、文档、依赖、fixture 或提交。
- 交叉审查范围为其他工作者实现的 map_data/map_models/travel/storefront_runtime/engine/storefront_state/checkpoint/opening_hours 行为；没有将本人主实现的发现与呈现代码作为本组独立对象。

## 规范映射与范围

这里映射十二个具体行为要求，不宣称覆盖整份 SPEC 的全部规范。规范行号均基于上面的冻结提交。

| 语义名称 | 精确规范锚点与要求 | 当前生产证据 |
| --- | --- | --- |
| 地图文件哈希 | 第 33、48 行：冻结文件哈希；发布验证版本与哈希 | `life/map_data.py:54-56` 在 JSON 解析前核验 SHA-256 |
| 场景地图身份固定 | 第 37、48 行：场景固定地图身份；发布验证版本 | `life/map_data.py:21-23` 比对 dataset_id 和 version |
| 实际步行模式 | 第 23、40、48 行：不能用 walking 文件名证明模式；保存真实路由与验证响应 | `life/map_data.py:140-143` 核对响应 steps.mode 与 profile |
| 显式地点活动环境 | 第 42 行：“地点缺失活动环境必须拒绝”且不得自动推断 | `life/map_models.py:153` 必填、非空 location_kinds |
| 核对日期可信截止 | 第 52 行：“核对日期不得晚于可信截止日” | `life/map_models.py:68-69` reviewed_on <= source.fact_cutoff |
| 完整选区路线 | 第 48、72 行：每个所选有向端点与方式均有显式记录，漏记录拒绝 | `life/map_data.py:170-176` 比较期望与实际路线键集合 |
| 未知路线拒绝出行 | 第 72 行：“未知记录不允许出行” | `life/travel.py:39-42` 非 reachable 选择失败并明确错误 |
| 到达访问资格 | 第 56 行：到达重验访问资格，旅行事实不能抹掉 | `life/storefront_runtime.py:38-51` 在已完成行程后记录实际位置、资格和失败 |
| 跨地点恢复活动环境 | 第 82、84 行：恢复按当前显式环境重验，真实暂停出行链路可达 | `life/engine.py:1094-1095` 恢复前重验活动环境 |
| 恢复持久报价时间线 | 第 58、102 行：持久事实完整往返，供给篡改拒绝 | `life/storefront_state.py:119-120` 比对持久报价与完整事件时间线 |
| 分支父目录隔离 | 第 102 行：“分支不能修改父目录” | `footprints/checkpoint.py:110-111` 恢复元数据仅写子 staging 目录 |
| 营业结束边界 | 第 52、54、56 行：半开区间、分钟营业例外、购买时重验 | `life/opening_hours.py:73-77` 结束边界严格小于 |

在以上映射中没有发现缺失或已偏离规范的当前生产实现：missing implementation = 0，diverged implementation = 0。两个问题属于已有正确实现的回归保护不足。

## 基线

所有十二条命令在任何突变发生前均完整运行，合计 **35 passed，0 failed，0 skipped**。`baseline-results.json` 保存逐条命令、返回码、耗时和输出；`baseline-complete.clean.json` 证明基线后仍为干净原提交。

每项先使用绝对 caller 虚拟环境解释器，设置临时树 `PYTHONPATH`，导入受变模块并断言解析后的 `__file__` 位于临时树。所有导入均成功，路径为 `/private/tmp/worldmock-bob-map-recovery.UkJseg/src/...`；同时通过 Python `compile` 验证源文件可解析。`PYTHONDONTWRITEBYTECODE=1` 禁止跨突变字节码复用，没有执行 uv 或重装 editable 依赖。

## 变异台账

共映射并执行 12 个单一语义突变：**10 killed，2 survived，0 invalid，0 inconclusive**。没有因语法、导入、启动或无关环境错误计为 killed。没有给出整份规范覆盖率或 mutation score。

| 语义名称 | 单一突变 | 命令索引 | 结果 | 因果证据 |
| --- | --- | --- | --- | --- |
| `map_hash` | Bypass frozen map file hash validation. | 下文同名命令 | **killed**；1 failed | 供应文件字节被改写后未先报 hash mismatch，转而报 schema invalid；测试明确捕获哈希验证优先于解析的遗漏。 |
| `map_identity` | Accept a map whose declared version differs from its scenario pin. | 下文同名命令 | **killed**；1 failed | 错误版本地图被接受，预期 IntegrityError 未发生。 |
| `actual_walking_mode` | Accept geometry whose actual step mode disagrees with the declared profile. | 下文同名命令 | **killed**；2 failed, 3 passed | 无 step mode 和实际 driving 的 walking 声明均错误通过，两个真实加载断言未得到 IntegrityError。 |
| `missing_location_environment` | Infer venue when a place omits the mandatory activity environment; retain nonempty validation. | 下文同名命令 | **survived**；4 passed | 四例仅覆盖空环境、anywhere、旧字段和非法环境。额外诊断删掉 location_kinds 后成功得到默认 venue，证明有效错误行为可达。 |
| `review_fact_cutoff` | Permit a declaration reviewed after its source fact cutoff. | 下文同名命令 | **killed**；2 failed | 报价与营业声明的 reviewed_on 晚于 source.fact_cutoff 均错误通过，预期 IntegrityError 未发生。 |
| `complete_route_matrix` | Accept a selected directed location/mode pair that has no explicit route status record. | 下文同名命令 | **killed**；1 failed | 缺一个所选有向路线记录仍返回选区，预期 coverage is incomplete 未发生。 |
| `unknown_route_travel` | Allow an explicitly unknown route to pass travel route selection. | 下文同名命令 | **killed**；1 failed, 1 passed | unknown 路线被 route_for 返回，预期 ROUTE_INFORMATION_UNKNOWN 未发生；unreachable 仍拒绝。 |
| `arrival_access` | Ignore missing access qualifications at actual arrival while leaving completed trip processing intact. | 下文同名命令 | **killed**；1 failed | 真实引擎完成到达后把缺资格访问错误记为 completed，测试要求 failed / LOCATION_ACCESS_DENIED。 |
| `lifestyle_resume_environment` | Skip the activity environment recheck on resume after an actual pause and trip. | 下文同名命令 | **killed**；1 failed | 真实启动、反馈、暂停和旅行后，跨环境恢复错误返回 completed，测试要求 rejected / ELIGIBILITY_LOCATION_KIND_MISMATCH。 |
| `restored_quote_timeline` | Accept persisted quote price/version/source/validity facts that disagree with the declared event timeline. | 下文同名命令 | **killed**；6 failed, 7 passed | 真实 LifeEngine.open 错误接受报价金额、版本、关联来源、起点、终点和伪造未知值；六个恢复拒绝断言失败。 |
| `branch_parent_directory` | Also write restoration metadata into the parent run directory when creating a branch. | 下文同名命令 | **survived**；3 passed | 自提与配送的真实恢复流程和子分支素材测试均通过；落盘诊断确认三个父运行目录均新增 restored-checkpoint.json。 |
| `closing_boundary` | Treat the exact closing minute as still open during actual purchase settlement. | 下文同名命令 | **killed**；1 failed | 实际 purchase_order 在结束秒仍可买，预期 MERCHANT_CLOSED 未发生。 |

每项的 `NAME.diff` 保存唯一生产修改，`NAME.import.log` 证明实际加载临时源码，`NAME.pytest.log` 保存完整测试输出，`NAME.result.json` 保存分类及原因，`after-NAME.clean.json` 证明恢复后 diff/status 均为空。下一项只有在恢复证明成功后才开始。

## Survived：缺失地点活动环境

**规范**：第 42 行要求拒绝缺失 `location_kinds`，不得自动推断。当前字段定义没有默认值，行为正确。

**突变**：仅把 `Field(min_length=1)` 改为 `Field(default=("venue",), min_length=1)`；保留非空和闭集验证。既有 `test_locations_reject_old_or_inferred_activity_environments` 四个参数仍全部通过。

**到达性证据**：在同一受变源码上将合法 LocationConfig 输入的 `location_kinds` 删除，模型成功输出 `("venue",)`。该诊断只说明变异有效且行为可达，不被用来更换预声明 oracle。详见 `missing_location_environment.diagnostic.log`。

**缺少的可观察断言**：经地图实际准入入口提交一个缺失活动环境的地点，必须拒绝；空数组、anywhere、旧字段和非法值的测试不覆盖字段完全不存在。

## Survived：分支父目录新增文件

**规范**：第 102 行要求分支不能修改父目录。当前实现仅写子 staging 目录，行为正确。

**突变**：在原有子目录写入之后，仅增加把相同 `restored-checkpoint.json` 写入 `store.run_dir`。子运行及其元数据依然完整，唯一新增语义为污染父目录。

**到达性证据**：真实发现、购买、消费、检查点、分支恢复、子分支继续消费的两种履约测试，以及已有素材恢复测试，合计三例全部通过。读取落盘产物确认三个父目录都新增恢复元数据，父 manifest 的 `parent_run_id` 均为空；对应子目录仍有自己的完整产物。详见 `branch_parent_directory.diagnostic.json`。

**缺少的可观察断言**：分支恢复和子分支继续活动前后，父目录全部持久文件路径集合与内容身份保持不变。当前 E2E 只比父 business_state 和 manifest 哈希，所以不检测额外文件或其他父文件变化；SQLite 临时文件需按其实际生命周期精确处理，不能笼统豁免持久数据库与资料。

## 预声明测试命令和基线输出

以下命令均先取得绿色基线，并在对应突变后原样重复。`--basetemp` 的日志目录为本审查新建的独立测试产物目录。

### map_hash

生产模块：`src/worldmock/life/map_data.py`。基线：`1 passed in 0.02s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_map_dataset.py::test_file_tampering_is_rejected_before_parsing --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-map_hash
```

### map_identity

生产模块：`src/worldmock/life/map_data.py`。基线：`1 passed in 0.03s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_map_dataset.py::test_map_identity_pin_is_checked --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-map_identity
```

### actual_walking_mode

生产模块：`src/worldmock/life/map_data.py`。基线：`5 passed in 0.03s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_map_dataset.py::test_reachable_routes_require_actual_geometry_mode_and_metrics --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-actual_walking_mode
```

### missing_location_environment

生产模块：`src/worldmock/life/map_models.py`。基线：`4 passed in 0.02s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_map_dataset.py::test_locations_reject_old_or_inferred_activity_environments --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-missing_location_environment
```

### review_fact_cutoff

生产模块：`src/worldmock/life/map_models.py`。基线：`2 passed in 0.02s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_map_dataset.py::test_review_cannot_claim_facts_after_the_source_cutoff --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-review_fact_cutoff
```

### complete_route_matrix

生产模块：`src/worldmock/life/map_data.py`。基线：`1 passed in 0.02s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_map_dataset.py::test_missing_route_record_cannot_mean_unreachable --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-complete_route_matrix
```

### unknown_route_travel

生产模块：`src/worldmock/life/travel.py`。基线：`2 passed in 0.83s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_state_validation.py::test_route_selection_rejects_explicit_non_reachable_status --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-unknown_route_travel
```

### arrival_access

生产模块：`src/worldmock/life/storefront_runtime.py`。基线：`1 passed in 0.74s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_arrival.py::test_arrival_rechecks_access_and_records_denial_without_undoing_real_travel --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-arrival_access
```

### lifestyle_resume_environment

生产模块：`src/worldmock/life/engine.py`。基线：`1 passed in 0.60s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_arrival.py::test_paused_lifestyle_cannot_resume_after_real_travel_to_incompatible_environment --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-lifestyle_resume_environment
```

### restored_quote_timeline

生产模块：`src/worldmock/life/storefront_state.py`。基线：`13 passed in 6.82s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_recovery.py::test_reopening_rejects_corrupted_storefront_facts --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-restored_quote_timeline
```

### branch_parent_directory

生产模块：`src/worldmock/footprints/checkpoint.py`。基线：`3 passed in 2.69s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_recovery.py::test_discovery_purchase_consumption_and_recovery_preserve_storefront_evidence tests/footprints/test_branch_coverage.py::test_restore_branch_carries_event_artifacts_into_child_run --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-branch_parent_directory
```

### closing_boundary

生产模块：`src/worldmock/life/opening_hours.py`。基线：`1 passed in 0.07s`；exit 0。

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery.UkJseg/src:/tmp/worldmock-bob-map-recovery.UkJseg PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_commerce.py::test_purchase_uses_minute_boundary_and_world_opening_override --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery/pytest-closing_boundary
```

## 恢复与清理证明

- 每个突变结束后立即 `git restore --source 2bce358a09046b84841bc5a2da68d82a66b79dbc -- <仅本项生产文件>`；逐项 `git diff --exit-code` 返回 0、完整 porcelain status 为空，HEAD 不变。
- `pre-cleanup.json`：临时树与 caller 的 HEAD 均为原提交，diff/status 均为空。
- 执行 `git worktree remove /tmp/worldmock-bob-map-recovery.UkJseg` 返回 0，没有强制删除或广域递归清理。
- `cleanup.json`：UTC `2026-09-20T12:26:16Z`，临时路径不存在且不再注册；caller HEAD 仍为原提交，diff 返回 0，完整 status 为空。
- 仅外部报告目录保留审查命令、差异、测试结果与临时测试产物；没有留下任何 production mutation。

## 明确未覆盖的边界

- 这是所列十二个语义的定向审查，不是完整 SPEC 的穷举验证，也未在本组重跑官方全量覆盖率门禁。父任务给出的全量结果未作为本报告基线替代。
- `map_hash` 的现有 oracle 以损坏 envelope 的文件证明先验证哈希再解析，杀死证据是错误类别/顺序变化；本项不能单独证明所有仍为合法 JSON 的篡改都会被测试识别。
- unknown 路线 oracle 使用真实场景建立的状态调用正式 `route_for` 选择入口；本项杀死的是路线选择允许 unknown，不宣称独立覆盖全部上层动作与轨迹状态机。
- 到达和跨地点恢复分别经过真实 LifeEngine 时钟与动作路径。到达测试基线包含保留位置、行程、失败公开事实；本项突变单独针对资格重验，不能替代对每个公开字段的独立突变。
- 两个 survived 已即时上报，测试修复与新提交上的复验尚未在本报告中发生；初次失败证据应保留，后续复验另列新提交与绿色基线。
