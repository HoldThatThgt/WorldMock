> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/day-plan-goals-bob-metrics.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 当日目标 Uncle Bob 审查：metrics

状态：COMPLETE（本轮审查已完成；发现三个测试判定缺口，尚未在本基线关闭）。

## 审查身份与边界

- 调用方仓库：`/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-day-goals-review-zit1ffsd/baseline`。
- 基线提交：`2d97d3dbe8cd6120e5e3cb707f8387fd22deb601`。
- 唯一产品规范：`docs/dev/SPEC-day-plan-goals.md`，主要锚点为第 42–51、98–162 行。
- 本轮独立隔离目录：`/tmp/worldmock-bob-metrics.hjk3RI/worktree`，从上述提交创建 detached worktree，已删除。
- 审查范围：全部登记目标的分母、登记后真实正时长家族与活动证据、单活动单目标、跨运行隔离、习惯首次正结算及跨日条件、外部约束拒绝与缺证据门禁、幂等去重、质量导出和行为评估接线。
- 仅修改过 `src/worldmock/evaluation/goal_metrics.py`、`src/worldmock/evaluation/behavior_metrics.py`、`src/worldmock/footprints/quality.py`；所有突变都恢复。未改测试、夹具、文档、锁文件或调用方源码，未提交、推送、使用网络或真实模型。
- `vendor/labelkit/.venv` 仅在临时 worktree 中链接到现有冻结环境；清理时已先移除该链接。

## 固定测试命令与基线

下列完整命令在任何突变之前已声明；台账的每一行均原样执行此命令，不因结果更换测试判定依据：

```sh
PYTHONPATH=src:.:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/agentsociety2/packages/agentsociety2:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/labelkit/src /Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest tests/evaluation/test_goal_metrics.py tests/evaluation/test_behavior_metrics.py tests/footprints/test_goal_export.py tests/footprints/test_quality.py -q
```

执行目录为本轮独立隔离目录。未突变基线结果为 `89 passed in 8.53s`，退出码 0；全部突变恢复后再次执行同一命令，结果为 `89 passed in 7.62s`，退出码 0。无 skip 或 xfail。`test_goal_export.py` 实际运行本地 SQLite、导出命令、真实 LifeEngine 午夜结算与 `quality.json` 检查，不依赖网络模型。

## 结果计数

| 范围 | 结果 |
| --- | --- |
| 已映射的具体行为约束 | 16 |
| implementation missing | 0 |
| implementation diverged | 0 |
| 有效独立语义突变 | 16 |
| killed | 13 |
| survived | 3 |
| invalid | 0 |
| inconclusive | 0 |

未发现被审查实现违反规范。三个 survived 表示现有测试允许正确实现未来退化，并不表示当前生产实现已经错误。所有失败均为已加载并执行的业务输出不符合断言，无语法、导入或启动失败。这里不计算覆盖百分比或突变得分。

## 语义突变台账

每一行的命令都是上面的完整固定测试命令。生产路径中未注明模块者均为 `src/worldmock/evaluation/goal_metrics.py`。每次运行后均立即还原原文件字节，并取得 `git diff --exit-code` 退出 0 和完整 `git status --porcelain=v1 --untracked-files=all` 空输出，才开始下一项。

| 规范锚点 | 生产路径与唯一语义改变 | 退出结果与分类 | 直接因果证据 |
| --- | --- | --- | --- |
| 104、144–150 行，修订不能洗掉分母 | `_goal_counts` 第 266 行：`len(goals)` 改为只数非 discarded 条目 | 退出 1；killed；1 failed, 88 passed | `test_revision_history_preserves_denominator_and_initial_priority` 的 `goal_count == 4` 得到 2 |
| 42–45、146 行，证据必须发生在登记后 | `_record_progress` 第 252 行：去掉登记时刻下界 | 退出 1；killed；1 failed, 88 passed | `test_goal_progress_requires_matching_actual_positive_settlement[before_registration]` 预期 0，得到 1 |
| 46、146、162 行，实际家族必须匹配 | `_record_progress` 第 250 行：移除意图家族匹配条件 | 退出 1；killed；2 failed, 87 passed | `...[wrong_family]` 预期 0，得到 1；修订历史测试也失败 |
| 146 行，推进标识必须关联实际活动 | `_record_progress` 第 256 行：移除 `progress_activity_id == activity_id` | 退出 1；killed；1 failed, 88 passed | `...[wrong_activity]` 预期 0，得到 1 |
| 102、125–126 行，只取正时长结算 | `_progress_evidence` 第 91 行：`end > start` 改为 `end >= start` | 退出 0；**survived**；89 passed | 现有零时长反例只验证目标推进，没有验证习惯样本；补充诊断确认零时长样本数从 0 变为 1 |
| 47–48、146 行，同一活动后续结算不得重复推进目标 | `_record_progress` 第 246 行：禁用已消费 `identity` 的早退 | 退出 0；**survived**；89 passed | 后续区间提供合法的新推进时刻时，补充诊断确认推进数从 1 变为 2 |
| 102、127 行，至少两个不同本地日 | `_habit_group` 第 140 行：`days >= 2` 改为 `days >= 1` | 退出 0；**survived**；89 passed | 同一天两个不同活动的补充诊断，从 0 可比对/null 比率变为 2 可比对/1.0 比率 |
| 125–126 行，每活动只取首次结算 | `_habitual_consistency` 第 112 行：禁用已见活动早退 | 退出 1；killed；2 failed, 87 passed | `test_habit_uses_first_settlement_context_two_days_and_weighted_pairs` 和 `test_habit_does_not_replace_missing_first_settlement_with_later_known_context` 的样本结果错误 |
| 98、151 行，外部动作跨 run 隔离 | `_external_constraints` 第 175 行：动作去重键的 run_id 改为共同常量 | 退出 1；killed；1 failed, 88 passed | `test_external_constraint_actions_are_run_scoped_and_protocol_rejections_are_separate` 的终态动作数预期 5，得到 4 |
| 157–160 行，拒绝证据缺失不能通过门禁 | `_external_constraints` 第 183 行：`evidence_complete` 固定为 true | 退出 1；killed；3 failed, 86 passed | `test_missing_rejection_evidence_fails_closed` 三个参数分别检出缺 action_id、缺 error_code、空 error_code |
| 103、151 行，幂等重放不能新增拒绝 | `_action_evidence` 第 151 行：禁用 reused 过滤 | 退出 1；killed；1 failed, 88 passed | `test_terminal_action_replay_does_not_turn_cancelled_activity_into_rejection` 的接受率预期 1，得到 0 |
| 103、151 行，同一动作多条终态证据必须去重 | `_external_constraints` 第 175 行：去重键加入 event_id | 退出 1；killed；13 failed, 76 passed | `test_all_frozen_external_constraint_codes_block_gate_once` 的全部十三种错误码参数检出同动作被重复统计 |
| 47–48、146 行，同一次结算只推进一个目标 | `_record_progress` 第 261 行：首目标推进后 `return` 改为 `continue` | 退出 1；killed；1 failed, 88 passed | `test_shared_family_uses_one_activity_once_and_first_matching_goal` 的第二目标预期 pending，实际 progressed |
| 115–121 行，导出采用冻结 manifest 的代码摘要 | `src/worldmock/footprints/quality.py` 第 71 行：注入 code_sha256 改为 None | 退出 1；killed；2 failed, 87 passed | 两个真实导出测试完成导出后发现预期代码摘要组缺失；不是导入或启动错误 |
| 109、118–121 行，三层报告实际进入 quality.json | `src/worldmock/footprints/quality.py` 第 70–73 行：三层报告调用替换为空对象 | 退出 1；killed；2 failed, 87 passed | `test_export_command_uses_authenticated_cutoff_and_manifest_code` 和 `test_real_engine_midnight_settlement_exports_old_day_goal_progress` 发现导出报告组缺失 |
| 109、122 行，既有行为评估接入同一报告 | `src/worldmock/evaluation/behavior_metrics.py` 第 114 行：三层 reports 更新替换为空更新 | 退出 1；killed；1 failed, 88 passed | `test_empty_samples_are_explicit_and_shared_behavior_entrypoint_matches` 发现 habitual_consistency 缺失 |

## Survived 的缺失断言

**零时长习惯样本**：应直接断言零时长结算不进入习惯 groups、不产生 sample_count，且不能占据同一 activity_id 的首次有效样本位置。现有测试仅断言零时长不能推进目标；目标自己的 `lower < upper` 校验掩盖了习惯取样回归。

**跨结算重复消费同一活动**：应让第一次结算推进通勤目标，随后同一个活动在另一正时长区间携带外出目标及落在新区间内的合法 progressed_at，仍断言总推进目标只有一个。现有共享家族测试后一次结算保留了旧 progressed_at，时间合法性判定单独就能挡住第二次计数，未有效约束已消费活动账本。

**同一天重复活动**：应构造同一 run、人物、地点和小时中同一天的两个不同活动，明确断言 `comparable_pair_count == 0`、`consistency_rate is None` 和缺跨日样本提示。现有单日情境只有一个活动，配对数量天然为零，无法杀死跨日条件退化。

以上三个问题已经立即反馈父代理。本轮没有改任何测试。父代理后续新增测试属于新基线，不能追溯改写本轮结果。

## 额外诊断与复原证明

额外诊断只用于证明 survived 确实可执行并改变规范相关输出，不替换固定测试命令，不将 survived 改称 killed。诊断脚本放在 worktree 外，每次也单独突变、立即恢复并检查 diff/status。

| 外部可见结果 | 原实现 | 对应 survived 实现 |
| --- | --- | --- |
| 零时长活动进入习惯的样本数 | 0 | 1 |
| 同一活动两次合法区间后的目标推进数 | 1 | 2 |
| 同日重复活动可比有序对数量 | 0 | 2 |
| 同日重复活动一致率 | null | 1.0 |

临时日志和诊断保存在 `/tmp/worldmock-bob-metrics.hjk3RI/`；源 worktree 子目录已删除。清理证据：先取得独立 worktree HEAD 与基线一致、diff 退出 0、完整 status 空；移除自己创建的 `.venv` 链接；`git worktree remove /tmp/worldmock-bob-metrics.hjk3RI/worktree` 退出 0；`git worktree list --porcelain` 不再含该路径。调用方最终 `git diff --exit-code` 退出 0、完整 status 空、HEAD 仍为 `2d97d3dbe8cd6120e5e3cb707f8387fd22deb601`。

未检查领域引擎和人物工具实现的突变强度；这两部分由另外的独立审查承担。未运行全项目测试、线上场景、真实模型或性能试验。本报告仅对所列十六个行为约束和固定本地测试命令负责。

## 原始 survived 补丁身份

以下统一 diff 由原提交与本轮已执行的精确 before/after 替换生成；后续复验必须原样使用。

| 补丁 | SHA-256 |
| --- | --- |
| `positive_duration` | `2ef88be0410ad549b0af440b24a8c6ea81f79aba2eda2524668cd9895a18c8b6` |
| `activity_reuse` | `38aa0eeb971a759cd5026b0dfb9b65fdabd949d08dd1fc39a3199c7956b7cc5d` |
| `two_days` | `869b3a40760721b37e3a196da273a2d8c9fa0adc009f28d1438762bd4d34ccbe` |
