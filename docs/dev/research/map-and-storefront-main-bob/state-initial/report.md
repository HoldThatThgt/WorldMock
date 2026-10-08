> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/map-and-storefront-main-bob/state-initial/report.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

Status: **COMPLETE**。审查已完成，发现一个有效存活突变；这不是全部规范验收通过的结论。

- Caller：`/Users/atishoo/.codex/worktrees/map-and-storefront-main/WorldMock`。
- Baseline commit：`70af9a70f82ebe9b2b33d5f4d7327f59de150002`。
- 临时 detached worktree：`/tmp/worldmock-map-main-bob-state-tree-8EIuWC`，审查后已移除。
- 规范：`docs/dev/SPEC-map-and-storefront.md`；品类身份定义沿该规范保留的关联合同读取 `docs/dev/SPEC-category-contract.md`。
- 授权：父任务明确授权隔离生产源码语义突变与真实本地 SQLite/恢复测试。未进行联网、付费、真实生产、测试修改、源码修复或提交。

## 规范与实现映射

| 审查要求 | 精确条款 | 当前生产路径与观察 |
| --- | --- | --- |
| 决策提交失败保留供给缓存 | 地图规格「记录、恢复与交付」115–116 行：失败时待提交呈现及供给凭据保持原状，提交成功后才清空 | `engine.py:409–434` 在 `commit_state` 成功后赋权威状态并清缓存；真实 SQL 触发器测试覆盖失败与重试 |
| 时间推进失败保留呈现缓存 | 同上 115–116 行 | `engine.py:569–602` 同样在提交成功后清缓存；配方完成过程使用真实 SQLite 故障 |
| 成功推进后清空呈现缓存 | 同上 116 行及本次审查输入明确的“成功清空”要求 | `engine.py:600` 已实现；当前重试测试缺成功后缓存断言 |
| 恢复必须保留品类契约版本 | 地图规格「冻结输入与身份」49 行；所保留品类合同「运行身份与导出」189–191 行：版本与摘要冻结，恢复前核对 | `run_identity.py:22–27` 比较四个身份字段，`LifeEngine.open` 经 `open_category_validated_store` |
| 恢复拒绝地图供给身份替换 | 地图规格 51 行验证版本与哈希；「记录、恢复与交付」118 行明确地图或供给篡改拒绝 | `storefront_state.py:117–118` 比较完整地图来源身份，恢复测试实际篡改运行清单中的供给摘要 |

没有发现这五个已映射要求的实现缺失或现行实现偏离。记录的存活项是现有测试未检出的行为违例，不代表生产当前遗漏清空。

## 基线与模块来源

预声明文件为 `predeclared-mutants.json`；成功清空的独立补检在原四项结束后，以 `predeclared-additional-mutant.json` 先声明、再运行独立绿色基线、最后变异。没有因看到变异结果而更改 oracle。

所有命令均使用下列工作目录与环境，`pytest` 参数在基线和变异时完全相同：

```sh
cd /tmp/worldmock-map-main-bob-state-tree-8EIuWC
export PYTHONPATH=/tmp/worldmock-map-main-bob-state-tree-8EIuWC/src:/tmp/worldmock-map-main-bob-state-tree-8EIuWC
export PYTHONDONTWRITEBYTECODE=1
```

| 基线 | 结果 |
| --- | --- |
| `decision_visible_cache_early_clear` | 1 passed，退出码 0，无 skip |
| `advance_presentation_cache_early_clear` | 1 passed，退出码 0，无 skip |
| `missing_category_version_is_accepted` | 1 passed，退出码 0，无 skip |
| `map_supply_hash_is_not_compared` | 1 passed，退出码 0，无 skip |
| `advance_success_keeps_stale_presentation_cache` | 1 passed，退出码 0，无 skip |

共有四个不同的现有 test node；追加成功路径复用其中一个 node，重新执行基线，因此共五次单测试绿色基线。
基线及每个变异前均验证模块可导入、可编译，并记录 `module.__file__`。三个受变异模块全部来自临时树：

```text
/private/tmp/worldmock-map-main-bob-state-tree-8EIuWC/src/worldmock/life/engine.py
/private/tmp/worldmock-map-main-bob-state-tree-8EIuWC/src/worldmock/life/run_identity.py
/private/tmp/worldmock-map-main-bob-state-tree-8EIuWC/src/worldmock/life/storefront_state.py
```

`/tmp` 在此主机解析为 `/private/tmp`。最初审查脚本未规范化临时根路径，包含关系断言失败；模块本身已从正确临时树导入，尚未运行 pytest 或变异。仅修正 caller 外的审查脚本路径规范化，重跑导入检查通过。失败预检保存在 `preflight-import-alias-check.*`，说明在 `preflight-import-alias-note.txt`，不算基线测试失败或 killed mutant。

## 计数

| 项目 | 数量 |
| --- | ---: |
| 已映射要求 | 5 |
| implementation missing | 0 |
| implementation diverged | 0 |
| 有效执行突变 | 5 |
| killed | 4 |
| survived | 1 |
| invalid | 0 |
| inconclusive | 0 |

## 突变台账

| 突变名称 | 最小生产变更 | 结果与因果证据 |
| --- | --- | --- |
| `decision_visible_cache_early_clear` | `src/worldmock/life/engine.py`：只在 commit_decision_round 中把 _pending_visible_supplies = {} 移到 commit_state 之前；呈现缓存和权威状态顺序不变。 | **killed**；tests/life/test_storefront_recovery.py:424；SQL 触发器正常拒绝提交，世界、持久状态及事件序号断言先通过，随后供给缓存比较失败：{} != 原三条供给凭据。 |
| `advance_presentation_cache_early_clear` | `src/worldmock/life/engine.py`：只在 advance 中把 _pending_presentation = None 移到 commit_state 之前；供给缓存和权威状态顺序不变。 | **killed**；tests/life/test_category_runtime.py:1054；真实 SQL 提交失败后世界、持久状态、事件序号及事件列表不变，呈现缓存比较失败：None != {1: {sealed-item: 1767542400}}。 |
| `missing_category_version_is_accepted` | `src/worldmock/life/run_identity.py`：require_category_identity 比较前仅从 expected 移除 category_contract_version；保留所有摘要校验。 | **killed**；tests/integration/test_category_identity.py:203；新建与清单缺字段操作正常完成，LifeEngine.open 不再抛出预期 IntegrityError，失败为 DID NOT RAISE。 |
| `map_supply_hash_is_not_compared` | `src/worldmock/life/storefront_state.py`：仅让地图 provenance 比较忽略 supplies_file，其余地图身份及供给世界状态验证仍执行。 | **killed**；tests/life/test_storefront_recovery.py:294；真实清单 supplies_file.sha256 替换后 LifeEngine.open 被放行，失败为 DID NOT RAISE。 |
| `advance_success_keeps_stale_presentation_cache` | `src/worldmock/life/engine.py`：只删除 advance 成功 commit_state 之后的 _pending_presentation = None；失败路径、供给缓存和状态提交不变。 | **survived**；原 SQL 失败和成功重试 oracle 仍 1 passed；成功路径已完成库存转换，但测试只断言库存，不检查成功后的 _pending_presentation is None。 |

每个突变的精确命令如下；stdout、stderr、退出码、命令参数、补丁与前后状态分别保存在同名文件。

**decision_visible_cache_early_clear**

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront-main/WorldMock/.venv/bin/python -B -m pytest -q 'tests/life/test_storefront_recovery.py::test_failed_decision_commit_preserves_visible_supply_credentials'
```

**advance_presentation_cache_early_clear**

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront-main/WorldMock/.venv/bin/python -B -m pytest -q 'tests/life/test_category_runtime.py::test_recipe_completion_sql_failure_keeps_engine_and_store_unchanged'
```

**missing_category_version_is_accepted**

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront-main/WorldMock/.venv/bin/python -B -m pytest -q 'tests/integration/test_category_identity.py::test_open_rejects_missing_category_identity_fields[category_contract_version]'
```

**map_supply_hash_is_not_compared**

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront-main/WorldMock/.venv/bin/python -B -m pytest -q 'tests/life/test_storefront_recovery.py::test_reopening_rejects_map_identity_substitution'
```

**advance_success_keeps_stale_presentation_cache**

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront-main/WorldMock/.venv/bin/python -B -m pytest -q 'tests/life/test_category_runtime.py::test_recipe_completion_sql_failure_keeps_engine_and_store_unchanged'
```

## 存活项与缺失断言

**`advance_success_keeps_stale_presentation_cache` 存活。** 删除 `advance` 成功提交后的 `_pending_presentation = None`，仍通过固定的真实 SQL 失败与成功重试测试。
变异保持语法、导入和执行有效；现有测试已经执行成功重试，并在 `tests/life/test_category_runtime.py:1058` 验证配方库存转换完成，证明推进成功路径被实际触达。
该测试在失败后检查缓存不变，但成功后只检查库存。需要在相同真实成功重试之后断言 `_pending_presentation is None`；同时明确断言 `_pending_visible_supplies == {}` 能覆盖同一成功清理合同。审查没有修改测试或生产实现。
原始存活结果已经保留；后续加固测试只能作为新提交、新基线、新审查的独立复验证据，不能改写本次结果。

## 还原与清理

每项立即执行精确文件级 `git restore --source=70af9a70f82ebe9b2b33d5f4d7327f59de150002 --worktree -- <生产路径>`。
每项之后的 `git diff --exit-code` 都为 0，完整 `git status --porcelain=v1 --untracked-files=all` 都为空；下一突变前重复验证。没有堆叠变异。
最后 detached HEAD 仍为基线，工作树 diff/status 为空；`git worktree remove /tmp/worldmock-map-main-bob-state-tree-8EIuWC` 退出 0，路径不存在且注册清单中已无此工作树。
清理时 caller HEAD 仍为 `70af9a70f82ebe9b2b33d5f4d7327f59de150002`，工作区 diff、暂存 diff 均为 0，完整 status 为空。此时未出现外部 coverage-report 修改。所有核对记录保存在 `cleanup-*` 与 `cleanup-summary.json`。
清理完成已通知父任务可以补测试；其后的 caller 修改由父任务持有，不属于本审查修改。报告只写在 caller 外。

## 明确边界

本次只审查所列五个要求，没有重复全地图特性审查，没有重跑全仓覆盖率，没有审查所有 SQL 故障点或所有身份字段。
品类审查仅突变运行恢复中的契约版本必需校验，地图审查仅突变供给来源身份比较；不能由这两项推出所有创建、分支与导出身份路径都已重新审查。
现有基线同时观察权威世界、持久状态、事件序号、部分事件列表与缓存，但本次没有单独突变每一种持久状态事务守卫。
未给出变异分数或规范覆盖百分比。原始证据完整留在本报告目录，没有复制回 caller，也没有提交。
