# 行为分析与运行检查

WorldMock 保留原有质量报告与工作、行为统计。独立候选比较和盲评命令已经删除。
统计口径见[行为分析设计](../design/07-evaluation.md)，认证导出见[记录引擎](../design/05-recording-engine.md)。

## 查看原有质量报告

按原有方式执行 `worldmock export`。输出目录仍包含 `quality.json`，导出认证仍核对其字节与完整重算结果。
导出清单版本不变；`quality.json` 的结构版本是 10，比 9 多出日、月、年回顾的 `memory_digest` 调用类，原有调用类不变。
格式 5 的环境包被拒收，先按[设计环境数据集](resources.md)改成格式 6；旧代码写出的导出认证不过，要用当前代码重新运行再导出。

## 检查一次运行的工作和作息

在仓库根使用原有检查命令，完整环境的身份必须与运行一致：

```powershell
python scripts/inspect_run.py <运行目录> workday --environment <完整环境目录>
```

命令继续返回 `{run_id, workload, routine_deviation, notes}`；输入不匹配时拒绝，不用人物工作区代替输入。
运行中的统计只说明当前已记录事实，不表示完整自然日或运行已经结束。

## 在仓库中调用行为统计

Python 调用方使用 `tools.analysis.behavior.metrics.behavior_metrics_report`；
工作输入和工作报告使用 `tools.analysis.behavior.workday.workday_inputs`、`workday_reports`。
函数参数、返回字段、缺失说明与 `behavior-metrics-4` 标识保持原样。
事件必须先经过记录引擎认证；按代码身份分组时，身份来自对应冻结运行清单。

缺少地点、记忆文本或工作输入时，相关指标仍按原合同返回未知值和说明，不能据此宣称行为真实或检查通过。
分析函数是仓库工具，不是模拟运行的前置条件，也不增加新的模型任务。

## 计算分层记忆试点指标

在仓库根对一次运行的导出目录执行：

```powershell
uv run python -m tools.analysis.memory_pilot --export <导出目录> [--output <文件>]
```

命令先认证导出，再计算；认证不过、导出不是单人运行或冻结目录文件不全时写错误日志并返回 1，不产生报告。
只接收当前代码认证得过的单人导出，旧代码写出的导出认证不过，要用当前代码重新运行再导出。
不给 `--output` 时报告写到标准输出；给了时文件必须还不存在，已存在时拒绝且不覆盖。

报告的 `metrics` 是 M1–M12，口径见[行为分析设计](../design/07-evaluation.md)「分层记忆试点指标」。
`checks` 每项写明路径、比较、阈值、实际值与 `pass`：`pass` 为 true 表示达到阈值，false 表示没达到，
null 表示该项的实际值为 null、无从判定（这次运行里没有可算的样本或分母，或该类调用的模型用量无法计价）。
`digest_review` 逐日逐类列出日回顾正文、状态（`written`、`unsorted`、`no_records`）和当天这一类的全部核验行，
供人工对照回顾有没有写错。报告只描述已记录的运行，不修改运行或导出，也不调用模型。
