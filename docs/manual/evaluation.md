# 行为分析与运行检查

WorldMock 保留原有质量报告与工作、行为统计。独立候选比较和盲评命令已经删除。
统计口径见[行为分析设计](../design/07-evaluation.md)，认证导出见[记录引擎](../design/05-recording-engine.md)。

## 查看原有质量报告

按原有方式执行 `worldmock export`。输出目录仍包含 `quality.json`，导出认证仍核对其字节与完整重算结果。
原有字段和导出版本保持不变，不需要转换环境包或导出包。

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
