# 当日目标 Uncle Bob 审查：metrics-recheck

状态：COMPLETE。原三层指标审查的三个 survived 补丁在新增行为断言后全部 killed；无剩余 survived。

## 身份、规范与复验范围

- 干净调用方：`/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-day-goals-review-zit1ffsd/recheck`。
- 基线提交：`b53079d67e87e5ff075c083f1bd96238b6bf4d99`。
- 规范：`docs/dev/SPEC-day-plan-goals.md` 第 47–48、102、125–127、146 行。
- 原报告：[指标初审](day-plan-goals-bob-metrics.md)，原基线 `2d97d3dbe8cd6120e5e3cb707f8387fd22deb601`。
- 本轮只复验：零时长不能成为习惯样本、同一活动不能在后续区间推进另一个目标、同日重复活动不能成为跨日习惯证据。
- 独立临时 worktree：`/tmp/worldmock-bob-metrics-recheck.3q36RG/worktree`，detached，已移除。
- 通过 Git diff 实际确认三个被审生产模块在新旧提交之间没有变化。本轮没有修改测试或保留任何生产修复；仅在新临时 worktree 中分别应用原三个源代码补丁。未运行网络或真实模型。

## 固定命令与未突变基线

本次继续使用原报告已经声明的完整命令，未更换测试文件或测试选择条件：

```sh
PYTHONPATH=src:.:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/agentsociety2/packages/agentsociety2:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/labelkit/src /Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest tests/evaluation/test_goal_metrics.py tests/evaluation/test_behavior_metrics.py tests/footprints/test_goal_export.py tests/footprints/test_quality.py -q
```

执行目录为本轮独立临时 worktree。未突变基线：`92 passed in 8.38s`，退出 0。全部突变逐个恢复后再次执行：`92 passed in 7.24s`，退出 0。无 skip、xfail 或环境故障。

## 原补丁身份与结果

三个补丁都使用原报告登记的统一 diff 文件；应用前逐个校验 SHA-256，再原样 `git apply`。没有因为新增测试改变突变方式、修改原补丁或另造更容易被检出的补丁。每一行均使用上述完整固定命令。

| 原补丁 | SHA-256 | 生产行为变化 | 本次结果 |
| --- | --- | --- | --- |
| positive_duration | `2ef88be0410ad549b0af440b24a8c6ea81f79aba2eda2524668cd9895a18c8b6` | `goal_metrics.py:_progress_evidence` 的 `end > start` 放宽为 `end >= start` | killed；退出 1；1 failed, 91 passed in 8.08s |
| activity_reuse | `38aa0eeb971a759cd5026b0dfb9b65fdabd949d08dd1fc39a3199c7956b7cc5d` | `goal_metrics.py:_record_progress` 禁用已消费活动标识的早退 | killed；退出 1；1 failed, 91 passed in 7.34s |
| two_days | `869b3a40760721b37e3a196da273a2d8c9fa0adc009f28d1438762bd4d34ccbe` | `goal_metrics.py:_habit_group` 的 `days >= 2` 放宽为 `days >= 1` | killed；退出 1；1 failed, 91 passed in 7.32s |

## 失败因果与缺口关闭

| 规范行为 | 检出的测试与可见结果 | 结论 |
| --- | --- | --- |
| 每活动首次正时长结算才进入习惯 | `tests/evaluation/test_goal_metrics.py:226`，`test_habit_zero_duration_cannot_create_or_claim_first_activity_sample`：预期空 groups，突变后出现零时长活动样本 | 新断言直接约束习惯层取样，没有依靠目标推进的另一个时间守卫代为挡错 |
| 同一 activity_id 后续合法区间不能再次推进目标 | `tests/evaluation/test_goal_metrics.py:164`，`test_later_valid_interval_cannot_reuse_activity_for_another_goal`：预期 `[progressed, pending]`，突变后为 `[progressed, progressed]` | 新例子的第二次 progressed_at 落在后续合法区间，确实检验活动消费账本 |
| 只有一天不能计算习惯一致率 | `tests/evaluation/test_goal_metrics.py:244`，`test_same_day_repetition_is_not_cross_day_habit_evidence`：同日两个活动的预期可比对为 0，突变后为 2 | 新断言使单日配对样本足够多，确实检验跨日条件 |

三个突变都完成解析、导入、报告计算并在预期业务结果断言上失败；不是语法、导入、初始化或无关错误。没有新增实现缺失或实现偏离发现。

本轮映射约束 3；implementation missing 0；implementation diverged 0；killed 3；survived 0；invalid 0；inconclusive 0。原轮其余十三个已 killed 的补丁没有在本轮重复执行；原报告保留其当时结果。本轮不声明整个规范穷尽覆盖，也不计算突变得分。

## 恢复与清理证据

- 每次突变后立即 `git apply -R` 同一补丁，`git diff --exit-code` 退出 0，完整 `git status --porcelain=v1 --untracked-files=all` 空输出，才开始下一项。
- 全部恢复后的独立 worktree HEAD 仍为 `b53079d67e87e5ff075c083f1bd96238b6bf4d99`，diff/status 干净，固定 92 项再次通过。
- 临时 vendor 环境链接已移除。`git worktree remove /tmp/worldmock-bob-metrics-recheck.3q36RG/worktree` 退出 0，注册 worktree 列表不再包含该路径。
- 调用方最终 HEAD 仍为 `b53079d67e87e5ff075c083f1bd96238b6bf4d99`，`git diff --exit-code` 退出 0，完整 status 空。没有动父开发工作树的生产或测试文件。
- 临时补丁与日志分别保存在 `/tmp/worldmock-bob-metrics.hjk3RI/` 和 `/tmp/worldmock-bob-metrics-recheck.3q36RG/`，两者均不再含注册的源 worktree。

领域引擎、工具 Schema、全项目门禁与真实长周期运行不属于本次指标复验范围，仍由相应验收证据负责。
