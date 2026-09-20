# Bob report

Status: **COMPLETE**。原存活突变已闭合；本次两个独立语义突变均为 killed。

## 范围与冻结身份

- Caller：`/Users/atishoo/.codex/worktrees/map-and-storefront-main/WorldMock`。
- 本次基线：`6fe3ab7b1711c5d7bee9d0bf3f4f22d48a53cb82`，隔离创建前后完整 Git status 均为空。
- 临时 detached worktree：`/tmp/worldmock-map-main-bob-state-recheck-tree-6oRHDf`，结束后已移除。
- 规范：`docs/dev/SPEC-map-and-storefront.md`「记录、恢复与交付」115–116 行。原文要求失败时世界、持久状态、事件序号、待提交呈现与供给凭据保持原状，“只有提交成功后才能清空这些观察缓存，重试不得丢失人物已经看到的供给”。本次仅复核审查输入明确的成功提交清理行为。
- 授权：父任务明确要求隔离源码突变复检和现有真实 SQLite 失败→重试用例，不扩展到其他行为。
- 生产路径：`src/worldmock/life/engine.py::LifeEngine.advance`，成功 `commit_state` 后的两条缓存清空语句，基线 600–601 行。

`git diff --name-status 70af9a70f82ebe9b2b33d5f4d7327f59de150002 6fe3ab7b1711c5d7bee9d0bf3f4f22d48a53cb82`
实测只含 `docs/dev/coverage-report.json` 和 `tests/life/test_category_runtime.py`。后者只增加成功重试后的两条缓存断言。
`src`、`custom`、`catalog`、`corpora`、`scenarios`、`scripts` 的比较完全为空。当前实现具备正确清空，没有发现本次范围内实现缺失或偏离。

## 固定 oracle 与绿色基线

两个突变都在 `predeclared-recheck.json` 中先固定为同一个现有测试，之后才执行基线或变异：

```sh
cd /tmp/worldmock-map-main-bob-state-recheck-tree-6oRHDf
export PYTHONPATH=/tmp/worldmock-map-main-bob-state-recheck-tree-6oRHDf/src:/tmp/worldmock-map-main-bob-state-recheck-tree-6oRHDf
export PYTHONDONTWRITEBYTECODE=1
/Users/atishoo/.codex/worktrees/map-and-storefront-main/WorldMock/.venv/bin/python -B -m pytest -q tests/life/test_category_runtime.py::test_recipe_completion_sql_failure_keeps_engine_and_store_unchanged
```

未变异基线：**1 passed in 0.53s**，退出码 0，无 skip。两个突变使用完全相同命令，未改变选择范围或解释 oracle。
基线及每次变异均先导入并编译模块，实际 `worldmock.life.engine.__file__` 为：

```text
/private/tmp/worldmock-map-main-bob-state-recheck-tree-6oRHDf/src/worldmock/life/engine.py
```

`/tmp` 和 `/private/tmp` 是此主机上的同一物理路径，包含关系按 resolved 路径验证。没有从 caller 的 editable 安装错误加载源码。

## 突变台账

| 突变 | 唯一生产变更 | 命令结果 | 因果证据 |
| --- | --- | --- | --- |
| `advance_success_keeps_stale_presentation_cache` | 只删除成功提交后的 `self._pending_presentation = None`；与初次存活补丁逐字节相同 | killed，退出码 1；1 failed in 0.64s | 测试 1059 行断言呈现缓存应为空，发现旧呈现字典仍存在；前面的失败回滚与成功库存断言均通过 |
| `advance_success_keeps_stale_supply_cache` | 独立删除成功提交后的 `self._pending_visible_supplies = {}`；保留呈现清理 | killed，退出码 1；1 failed in 0.92s | 测试 1060 行断言供给缓存应为空，发现一条旧供给凭据；失败回滚、成功库存和呈现清理断言均通过 |

两个变异都可解析、可导入且实际触达成功重试路径；失败来自规范要求对应的状态断言，不是语法、启动、导入、代码身份或无关环境故障。
每项的原始命令、stdout、stderr、退出码、patch、导入路径及恢复证据都以同名文件保存。

## 原存活项闭合

初次审查保留在 `/tmp/worldmock-map-main-bob-state-8RnVvy/bob-report.md`，其 **survived** 结论保持不变。
本次将同一生产变异应用于仅加固测试后的新基线，变异 patch 字节完全相同，检查见 `original-patch-byte-comparison.json`。
原报告和原补丁的审查前后 SHA-256 一致；没有覆盖或改判旧证据。

新增断言分别检查成功后的呈现缓存与供给缓存，确实检出两种独立的遗漏清理。因此该存活项的测试缺口在本次新基线中闭合。

## 计数

| 项目 | 数量 |
| --- | ---: |
| 已映射成功清理要求 | 2 |
| implementation missing | 0 |
| implementation diverged | 0 |
| 基线现有用例 | 1 passed |
| killed | 2 |
| survived | 0 |
| invalid | 0 |
| inconclusive | 0 |

## 还原与清理

每项之后按精确生产路径执行：

```sh
git restore --source=6fe3ab7b1711c5d7bee9d0bf3f4f22d48a53cb82 --worktree -- src/worldmock/life/engine.py
git diff --exit-code
git status --porcelain=v1 --untracked-files=all
```

每次 diff 退出 0，完整 status 为空；下一次变异前重新核对，没有叠加修改。
最终临时 HEAD 保持基线，diff/status 为空。明确路径的 `git worktree remove` 退出 0；目录不存在且注册清单无该工作树。
清理时 caller 仍为 `6fe3ab7b1711c5d7bee9d0bf3f4f22d48a53cb82`，工作区 diff、暂存 diff 均为 0，完整 status 为空。
`cleanup-summary.json` 与 `cleanup-*` 保存实际命令证据。已在完成清理后通知父任务可进行文档归档，后续父任务修改不属于本审查。

## 未执行范围

本次没有重跑全仓覆盖率、修改测试或规范、修改正式生产源码、创建提交，也没有联网、付费或真实生产操作。
没有重复之前的失败原子性、品类或地图身份变异；既有四项 killed 保留原提交的独立证据。
本报告只证明这两个成功缓存清理反例被加固后的现有真实 SQL 重试测试检出，不给出全规格覆盖率或变异分数。

原样复验 patch SHA-256：`4e993c64e7c33b390bddb009483ce6b50e8c94c6401f5c30ea7bdf0a8a386a11`。
