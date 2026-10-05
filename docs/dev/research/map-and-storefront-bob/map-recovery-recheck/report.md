> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/map-and-storefront-bob/map-recovery-recheck/report.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

> 本页为审查报告阅读副本；导航链接指向原始证据包。包内保留未改动的原始报告、日志、补丁和复原记录。

状态：**COMPLETE**。两个首次 survived 的测试缺口在新提交上均被原语义突变杀死，标记为 **closed**；首次发现记录完整保留。

## 审查身份

- Caller：`/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock`。
- 本轮基线提交：`017d49c4a246c0c524e541571220e1a07b16ce87`。
- 原审查提交：`2bce358a09046b84841bc5a2da68d82a66b79dbc`。
- 唯一规范：`docs/dev/SPEC-map-and-storefront.md`，SHA-256 `f5929dbf7a578b997f212758111dc1e02f5a9a3799e267eb4bc801ae7de12693`。
- 适用流程：`/Users/atishoo/.agents/skills/uncle-bob-review/SKILL.md`。本轮沿用已完整读取的技能，重新完成 clean caller、隔离、预声明、绿色基线和清理步骤。
- 独立 detached 临时树：`/tmp/worldmock-bob-map-recovery-recheck.mH7Byu`，已删除并注销。
- 本轮仅执行授权的本地测试和临时生产语义突变；未编辑 caller、测试、文档、fixture、依赖或提交；未执行网络、付费或真实生产动作。

## 精确复验范围与实现证据

| 语义 | 规范锚点 | 当前实现 | 新增可观察断言 |
| --- | --- | --- | --- |
| 缺失地点活动环境 | SPEC 第 42 行：“地点缺失活动环境必须拒绝”，不得推断环境 | `src/worldmock/life/map_models.py:153` 的 `location_kinds` 必填且非空 | `tests/life/test_map_dataset.py:171` 经正确哈希的 `load_map_dataset` 入口拒绝缺字段，验证必填字段错误和英文加载日志 |
| 分支父目录不变 | SPEC 第 102 行：“分支不能修改父目录” | `src/worldmock/footprints/checkpoint.py:110` 仅向子 staging 目录写恢复元数据 | `tests/life/test_storefront_recovery.py:152` 快照全部相对路径、目录身份和文件 SHA-256；第 210、220 行在恢复后、子分支继续后分别比较 |

本轮映射要求 2 项，missing implementation = 0，diverged implementation = 0。新旧提交之间 `src/custom/catalog/corpora/scenarios/scripts` 的 `git diff --exit-code` 返回 0；两个受变生产位置字节未变。未以测试实现作为规范来源，也未将完整测试通过当作穷举证明。

## 基线与导入来源

两条预声明命令都在任何突变前运行：缺字段命令 **5 passed**，父目录命令 **3 passed**；合计 **8 passed，0 failed，0 skipped**。输出见 `baseline-results.json` 与 `baseline-*.pytest.log`。

基线及每个突变均先使用 caller 的绝对 `.venv/bin/python`，通过新临时树 `PYTHONPATH` 导入受变模块并断言 `__file__` 落在该临时树，再执行 Python `compile`。`map_models.py` 和 `footprints/checkpoint.py` 均实际来自 `/private/tmp/worldmock-bob-map-recovery-recheck.mH7Byu/src/...`。导入/解析均 exit 0，没有使用 uv 或改变 editable 路径。

## 复验台账

本轮 **2 killed，0 survived，0 invalid，0 inconclusive**。两个失败都由预期行为差异导致，没有语法、导入、启动或环境失败。

| 语义名称 | 与首次完全同义的单一突变 | 本轮结果 | 因果证据 | 缺口状态 |
| --- | --- | --- | --- | --- |
| `missing_location_environment` | `Field(min_length=1)` 改为 `Field(default=("venue",), min_length=1)`，仅增加非法推断默认值 | killed；1 failed, 4 passed | 新真实地图准入测试应拒绝却成功返回，`DID NOT RAISE IntegrityError`；额外诊断再次确认缺字段实际变为 `("venue",)` | closed |
| `branch_parent_directory` | 保留子目录产物，额外把同一恢复元数据写入父 `store.run_dir` | killed；2 failed, 1 passed | 自提和配送父目录快照均出现额外 `restored-checkpoint.json`；不是子分支产物缺失或业务启动失败 | closed |

`NAME.diff`、`NAME.import.log`、`NAME.pytest.log`、`NAME.result.json` 和 `after-NAME.clean.json` 保存每个单独变异的补丁、真实导入、完整错误、因果分类与即时还原证明。两个补丁与首次对应 `.diff` 完全一致。

## 预声明且原样复跑的命令

### missing_location_environment

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery-recheck.mH7Byu/src:/tmp/worldmock-bob-map-recovery-recheck.mH7Byu PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_map_dataset.py::test_locations_reject_old_or_inferred_activity_environments tests/life/test_map_dataset.py::test_map_publication_rejects_missing_activity_environment_with_valid_file_identity --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery-recheck/pytest-missing_location_environment
```

基线 exit 0：`5 passed in 0.02s`。
受变测试 exit 1：1 failed, 4 passed。新测试通过正确文件哈希进入 load_map_dataset，错误默认 venue 使应拒绝的地图成功返回，pytest 报 DID NOT RAISE IntegrityError。原四例仍通过，因果明确来自新增缺字段断言。

### branch_parent_directory

```sh
PYTHONPATH=/tmp/worldmock-bob-map-recovery-recheck.mH7Byu/src:/tmp/worldmock-bob-map-recovery-recheck.mH7Byu PYTHONDONTWRITEBYTECODE=1 LITELLM_LOCAL_MODEL_COST_MAP=True AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q tests/life/test_storefront_recovery.py::test_discovery_purchase_consumption_and_recovery_preserve_storefront_evidence tests/footprints/test_branch_coverage.py::test_restore_branch_carries_event_artifacts_into_child_run --tb=short --basetemp=/tmp/worldmock-map-bob-map-recovery-recheck/pytest-branch_parent_directory
```

基线 exit 0：`3 passed in 2.44s`。
受变测试 exit 1：2 failed, 1 passed。真实自提与配送恢复在比较全部父目录快照时都发现新增 restored-checkpoint.json，分别失败于 test_storefront_recovery.py:210；既有子分支素材测试仍通过。

## 首次发现与本轮闭合历史

| 审查提交 | 缺失活动环境 | 父目录写入 | 报告 |
| --- | --- | --- | --- |
| `2bce358a09046b84841bc5a2da68d82a66b79dbc` | survived；原四例通过 | survived；原三例通过 | `/tmp/worldmock-map-bob-map-recovery/report.md` |
| `017d49c4a246c0c524e541571220e1a07b16ce87` | killed；新增真实准入断言命中 | killed；两种履约全部父目录快照命中 | 本报告 |

首次报告、原始 survived 输出与诊断没有被覆盖。新提交只收紧已有行为断言；本轮没有修改生产行为来消除错误复现。关闭的含义是这两个已证明的 oracle 缺口已有能杀死原突变的测试，不是宣称所有可能错误都被覆盖。

## 恢复与清理

- 每次突变后，仅还原当次生产文件到 `017d49c4a246c0c524e541571220e1a07b16ce87`；`git diff --exit-code` 均返回 0，完整 porcelain status 均为空，HEAD 未变。
- `pre-cleanup.json` 同时记录临时树和 caller 为该提交、diff/status 均空。
- `git worktree remove /tmp/worldmock-bob-map-recovery-recheck.mH7Byu` 成功返回 0；没有强制或广域删除。
- `cleanup.json` 记录 UTC `2026-09-20T12:35:56Z`：临时路径不存在且不再注册；caller HEAD 仍为 `017d49c4a246c0c524e541571220e1a07b16ce87`，diff 返回 0，完整 status 为空。

## 未扩大结论的边界

本轮只复验这两个原 survived 突变，没有重跑其他十个已 killed 突变，也没有替代父任务的新提交官方全量门禁；两轮结果必须按各自提交保留。原生产代码在相关目录间无差异是单独验证的事实，不等同于在新提交重复全部变异。本报告不提供整份 SPEC 覆盖率或 mutation score。
