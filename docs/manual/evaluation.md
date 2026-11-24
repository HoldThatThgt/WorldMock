# 行为分析与运行检查

WorldMock 保留原有质量报告与工作、行为统计。独立候选比较和盲评命令已经删除。
统计口径见[行为分析设计](../design/07-evaluation.md)，认证导出见[记录引擎](../design/05-recording-engine.md)。

## 查看原有质量报告

按原有方式执行 `worldmock export`（导出格式版本 `EXPORT_SCHEMA_VERSION = 9`）。输出目录仍包含 `quality.json`，导出认证仍核对其字节与完整重算结果。
导出清单同步冻结为运行清单 v8（`model_identity`、`persona_sha256s`）与 `EXPORT_SCHEMA_VERSION = 9`；`quality.json` 的结构版本是 `11`，在既有调用类之外单列 `search_session`（`LLM Search Session` 搜索子会话调用，与 `action` 决策轮分列），并采用 `wakeup_id` 计数口径（删除退役的 `same_moment_round_counts` 字段）。
旧格式环境包与旧代码写出的导出都通不过当前严格准入与导出认证，请使用当前格式的环境包重新运行并导出。

## 检查一次运行的工作和作息

在仓库根使用原有检查命令，完整环境的身份必须与运行一致：

```powershell
python scripts/inspect_run.py <运行目录> workday --environment <完整环境目录>
```

命令支持多人物环境（按 `personas/*.toml` 与 `persona_sha256s` 核对身份），继续按人物返回 `{run_id, workload, routine_deviation, notes}`；输入不匹配时拒绝，不用人物工作区代替输入。
运行中的统计只说明当前已记录事实，不表示完整自然日或运行已经结束。

## 在仓库中调用行为统计

Python 调用方使用 `tools.analysis.behavior.metrics.behavior_metrics_report`；
工作输入和工作报告使用 `tools.analysis.behavior.workday.workday_inputs`、`workday_reports`。
`behavior_metrics_report` 的参数是事件与关键字参数 `timezone`、`locations`、`workday`，不接受 `MEMORY.md` 文本；返回字段与缺失说明保持原样，只是 `gates` 里没有 `memory_md_identity_residue`；`behavior-metrics-4` 标识不变。
事件必须先经过记录引擎认证；按代码身份分组时，身份来自对应冻结运行清单。

缺少地点或工作输入时，相关指标仍按原合同返回未知值和说明，不能据此宣称行为真实或检查通过。
分析函数是仓库工具，不是模拟运行的前置条件，也不增加新的模型任务。

## 计算分层记忆试点指标

在仓库根对一次运行的导出目录执行：

```powershell
uv run python -m tools.analysis.memory_pilot --export <导出目录> [--output <文件>]
```

命令先按运行清单 v8（`ModelIdentity`）、导出 v9 与质量报告 v11 认证导出，再以 `wakeup_id` 替代退役 `decision_id` 计算指标；认证不过、导出不是单人运行或冻结目录文件不全时写错误日志并返回 1，不产生报告。
只接收当前代码认证得过的单人导出（`agent_ids` 恰含一名人物），旧代码写出的导出认证不过，要用当前代码重新运行再导出。
不给 `--output` 时报告写到标准输出；给了时文件必须还不存在，已存在时拒绝且不覆盖。

报告的 `metrics` 是 M1–M12，口径见[行为分析设计](../design/07-evaluation.md)「分层记忆试点指标」。
`checks` 每项写明路径、比较、阈值、实际值与 `pass`：`pass` 为 true 表示达到阈值，false 表示没达到，
null 表示该项的实际值为 null、无从判定（这次运行里没有可算的样本或分母，或该类调用的模型用量无法计价）。
`digest_review` 逐日逐类列出日回顾正文、状态（`written`、`unsorted`、`no_records`）和当天这一类的全部核验行，
供人工对照回顾有没有写错：运行时只查回顾的格式与字数，不核对内容，回顾写得准不准要看这张表和 M11。
报告只描述已记录的运行，不修改运行或导出，也不调用模型。
