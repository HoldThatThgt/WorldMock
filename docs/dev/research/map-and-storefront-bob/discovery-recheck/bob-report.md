# Bob report

> 本页为审查报告阅读副本；导航链接指向原始证据包。包内保留未改动的原始报告、日志、补丁和复原记录。

**状态：COMPLETE。** 游标起点绑定和跨世界版本连续分页的两次独立语义变异均为 **killed**。此前起点夹具的下游缺路线异常与初始版本字段缺失均已从本次测试夹具中消除；历史报告的原始判定不变。

## 身份与范围

| 项目 | 本次独立核对结果 |
| --- | --- |
| 调用方 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock` |
| 干净基线 | `017d49c4a246c0c524e541571220e1a07b16ce87` |
| 唯一规范 | 该提交的 `docs/dev/SPEC-map-and-storefront.md:68` |
| 起点要求 | “游标绑定地图身份、查询和起点”中的起点绑定 |
| 世界版本要求 | “不能因查询本身提交新世界版本而使所有下一页失效” |
| 生产路径 | `src/worldmock/life/discovery.py:68` 的 `_cursor_identity` 以及下游 `_cursor_offset` |
| 临时 detached 树 | `/tmp/worldmock-map-bob-discovery-recheck-worktree.wyH0Lf`，已恢复并移除 |
| 证据目录 | `/tmp/worldmock-map-bob-discovery-recheck/` |

新提交相对 `2bce358a09046b84841bc5a2da68d82a66b79dbc` 的 `git diff --name-only` 仅有 `tests/life/test_map_dataset.py`、`tests/life/test_storefront_discovery.py`、`tests/life/test_storefront_recovery.py`，生产源码与规范未变。本次没有修改调用方、测试、文档、依赖，也没有提交、安装、外网测试、付费调用或生产动作。

## 基线与精确命令

先预声明 `plan.json`，再在未变异的 detached 树分别执行下列命令。两组均 **1 passed**，没有跳过。

所有测试的工作目录固定为临时树；解释器使用调用方绝对路径，不运行 `uv run`。每次均显式设置：

```text
PYTHONPATH=/tmp/worldmock-map-bob-discovery-recheck-worktree.wyH0Lf/src:/tmp/worldmock-map-bob-discovery-recheck-worktree.wyH0Lf
AGENTSOCIETY_LLM_API_KEY=test-only
LITELLM_LOCAL_MODEL_COST_MAP=True
PYTHONPYCACHEPREFIX=/tmp/worldmock-map-bob-discovery-recheck/pycache/<baseline-or-mutant>-<name>
```

独立 pycache 避免复用变异前字节码。每次变异先通过 Python 解析与导入验证；`worldmock.life.discovery.__file__` 均为 `/tmp/worldmock-map-bob-discovery-recheck-worktree.wyH0Lf/src/worldmock/life/discovery.py`，不存在导入调用方同名模块的情况。

| 要求 | 预声明并原样执行的命令 | 基线 |
| --- | --- | --- |
| 起点绑定 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q 'tests/life/test_storefront_discovery.py::test_discovery_rejects_cursor_for_other_conditions[origin]'` | 1 passed in 0.03s |
| 跨世界版本连续分页 | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_discovery.py::test_discovery_paginates_every_match_across_world_versions_and_keeps_empty_places` | 1 passed in 0.03s |

## 变异台账

| 要求 | 单一生产变异 | 结果及因果证据 |
| --- | --- | --- |
| 起点绑定 | 仅从 `_cursor_identity` payload 删除 `"origin": context.agent.location_id`。地图、查询、选区与游标结构校验均保留。 | **killed**；1 failed in 0.14s。改变人物起点后无游标查询成功且 route.origin_id 为 store-a；仅删除摘要中的 origin 后，旧游标被接受，test_storefront_discovery.py:205 出现 DID NOT RAISE BusinessRuleError。不存在缺路线 StopIteration。 |
| 跨世界版本连续分页 | 将 payload 的版本值从 `dataset.version` 改为 `[dataset.version, context.state.version]`，沿用首次审查的同一操作。 | **killed**；1 failed in 0.14s。初始 version=0 完成第一页；版本推进为 1 后第二页在 test_storefront_discovery.py:177 触发 DISCOVERY_CURSOR_INVALID，违反跨世界版本连续分页。无初始 AttributeError。 |

起点测试在改变起点后先完成一次无游标正常查询，再验证旧游标必须拒绝。删除起点绑定后该正常查询仍成功，而旧游标未报错，因此 killed 由缺少应有业务拒绝直接证明，不依赖 `StopIteration`。

分页测试初始 `WorldState.version=0`，完成第一页后推进到版本 1；变异令第二页的摘要不再匹配，触发业务错误。该路径已越过初始状态与第一页，不再是原报告中的 `AttributeError`。真实引擎逐页 oracle 的独立 killed 证据仍保留在原报告，本次没有改写或重复计入。

## 统计与边界

| 指标 | 结果 |
| --- | --- |
| 映射的可观察要求 | 2 |
| implementation missing / implementation diverged | 0 / 0 |
| 基线 | 2 条命令，2 passed，0 skipped |
| killed / survived / invalid / inconclusive | 2 / 0 / 0 / 0 |

**没有 survived mutant。** 本报告没有扩大到所有游标格式、所有分页排序、地图发布或完整特性验收，也未重跑调用方全量门禁。未计算规范覆盖率或 mutation score。规范当前实现仍包含起点身份且排除 mutable 世界版本，未发现这两个要求的实现偏离。

完整单项记录为 `baseline-<name>-pytest.json`、`mutant-<name>-pytest.json`、`mutant-<name>-import.json` 与 `mutant-<name>-patch.json`；每项包含精确命令、工作目录、环境、返回码与原始输出。`classified-results.json` 提供判定和因果证据。

## 恢复、隔离与原报告保留

每次变异均只修改临时树的一个生产文件，立即执行：

```sh
git restore --source=017d49c4a246c0c524e541571220e1a07b16ce87 -- src/worldmock/life/discovery.py
git diff --exit-code
git status --porcelain=v1 --untracked-files=all
```

两次恢复后原文件字节完全一致，diff 返回 0，完整 status 为空，再开始下一次操作。最终还验证 HEAD 与 `git diff --exit-code 017d49c4a246c0c524e541571220e1a07b16ce87`，均与干净基线一致。

随后仅执行显式 `git worktree remove /tmp/worldmock-map-bob-discovery-recheck-worktree.wyH0Lf`，退出 0；该目录与 Git 注册均不存在。没有强制删除或广泛递归清理。

清理后调用方 HEAD 仍为 `017d49c4a246c0c524e541571220e1a07b16ce87`，diff 返回 0，full porcelain 输出为空；证据在 `final-caller-*.json`、`final-detached-*.json`、`worktree-remove.json`、`final-worktrees.json`。已将此确认发送给父代理，之后由其进行的文档编辑不属于本次审查变更。

原报告 `/tmp/worldmock-map-bob-discovery/bob-report.md` 完全未修改，SHA-256 仍为 `0957cc113682dca86ac9fbdf34c311115dda657d038c132dcc7d2c53d2f49110`；证明在 `original-report-unchanged.json`。此前的 1 invalid 与起点检验边界记录保持原样，本复验作为新提交的新证据补充。
