# 安装、运行与恢复

从仓库根使用锁定解释器与依赖，正式环境包括查看器需要的 Node.js SQLite 能力和中文图表字体。

```powershell
python scripts/bootstrap.py --profile release
```

先按[环境素材](resources.md#resources-设计环境数据集)、[人物](personas.md#personas-编写-persona-toml)
和[运行配置](#runtime-编写-config-toml)准备完整环境。下列路径 `D:/datasets/my-world` 是需要自行准备的外部环境。
按 config.toml 的 model.api_key_env 提供进程凭据；例如声明 WORLDMOCK_API_KEY 时：

```powershell
$env:WORLDMOCK_API_KEY = 'your-key'
worldmock validate --environment D:/datasets/my-world
worldmock run --environment D:/datasets/my-world --output .work/runs/my-world
worldmock status --run-dir .work/runs/my-world
worldmock export --run-dir .work/runs/my-world --output .work/exports/my-world
```

status 的 `liveness` 是 `stale` 时驱动进程已经不在（例如被结束），用 `recent_checkpoints` 里的检查点恢复；
是 `waiting_for_model` 时运行正在等模型服务：断网时引擎原样重发同一请求至多十二小时，不需要人工恢复；
运行失败时 `runtime.error_message` 写着失败的根因。

完整验证先核对全部输入和实际模型元数据，失败时按所属路径与英文原因修正。
这些命令可以在任何目录执行，不需要设置 `AGENTSOCIETY_*` 或 `PYTHONPATH`，只需要 model.api_key_env 指的那个变量。
run 不接受 --scenario；不读取固定供应商私密文件，不使用默认模型或另一份目录路径。
凭据正文不写进输入包、运行清单或报告。

<a name="runtime-编写-config-toml"></a>
## 编写 config.toml

在环境根的 config.toml 声明时间、唯一种子、活动结算、重估与进度播报节奏、商品呈现轮转、日历及 model 表。
顶层先写 `schema_version = 2`；写其他值的文件被 `worldmock validate` 以 `RUN_CONFIG_INVALID` 拒绝，原因是 `schema_version: Input should be 2`。
model 表里没有 `persona_validation_max_retries`：人物卡片由 persona.toml 直接填写，写了这个键的文件同样被拒绝。
完整字段、重试预算、回合上限和合法范围见[配置设计](../design/08-configuration.md)。
工作安排和每日差异在[人物](personas.md#personas-编写-persona-toml)声明。

start_time 与 stop_time 使用带偏移的 TOML 原生日期时间，按自身偏移整点并与 timezone 一致。
stop_time 必须晚于开始时刻。唯一整数 seed 控制地图、天气、内容、功能 app、购买时长、查询时长、查询所在的 app 与推送。

model 声明实际 OpenAI 兼容服务、base_url、requested_model、api_key_env、metadata_url 与 context_window。
api_key_env 填进程环境变量的名称。实际服务必须返回唯一匹配模型的上下文容量与输出上限；
容量不足、缺失或身份不一致时停止，不能通过填写更大的声明绕过核验。

```mermaid
flowchart LR
    Config[运行与模型声明] --> Metadata[核验实际服务容量]
    Metadata --> Identity[冻结输入和模型身份]
    Identity --> Run[真实运行]
```

## 检查工具交互

人物用两个开始工具开始活动，每个参数都要写。start_main_activity 开始出行、在当前地点做一件事或睡觉，
start_side_activity 开始购买、查询或使用一个内容 app；要同时开始两项，就在同一次回复里两个工具各调用一次。

```text
start_main_activity  {"action": "travel",   "target": "公司", "detail": "驾车"}
start_main_activity  {"action": "interact", "target": "家",   "detail": "测试休息"}
start_main_activity  {"action": "sleep",    "target": "470",  "detail": null}
start_side_activity  {"action": "purchase", "target": ["牛肉面", "柠檬茶", "柠檬茶"], "prefer": "美团"}
start_side_activity  {"action": "query",    "target": ["食品"], "prefer": null}
start_side_activity  {"action": "interact", "target": ["哔哩哔哩"], "prefer": null}
```

出行的 detail 照工具层该目的地下列出的出行方式名称写（步行，以及数据集定义的方式）；在地点做事的 detail 照工具层当前地点下列出的名称写。
睡眠的 target 写 10 到 720 的分钟数，只写数字，detail 写 null。购买逐份写商品名，同一个名称写两次就是两份。
购买和查询的 prefer 写想用的 app 的完整名称，照工具层 prefer_apps 列出的写，没有想法写 null；使用内容 app 时 prefer 写 null。
查询的 target 写商品名或类别名（零食、食品、饮料、商品）。
例子里的地点、内容、商品和 app 名称取自所用的环境数据集。
查询占槽位 2，要 5 到 10 分钟，不能暂停，答案在查询做完时写进这次请求的结果。
没有当场作答、不花时间的世界查询工具；记忆查询和回看任务仍然当场完成。
用内容 app 时看到什么由环境选择，功能 app 由环境执行，人物按真实活动名称报告、暂停与结束。
工具字段、名称解析、槽位与提醒规则见[模拟引擎](../design/04-simulation-engine.md)。
实际模型请求和事件可在运行查看器（tools/run-viewer.html）中核对：先用 worldmock export 导出（运行中或中途停下的运行加
--cutoff 导出到某个事件），再把导出目录拖进去；查看器在页面里从 events.jsonl 建索引，拖放区显示读取进度。对象名称取自
导出冻结的环境目录；人物的 MEMORY.md 总结要同时拖入运行目录。SQL 控制台查的是这个索引，三类大事件的正文已裁剪，完整的行用
sqlite3 或 scripts/inspect_run.py 读。分发查看器时带上同目录的全部 run-viewer 脚本和样式文件。
人物可见范围见[人物设计](../design/06-agent.md)。

<a name="runtime-长时间运行的启动时刻"></a>
## 长时间运行的启动时刻

DeepSeek 在 UTC 周一至周五 01:00–04:00 与 06:00–10:00（北京时间 09:00–12:00 与 14:00–18:00）按峰时价计费，其余
时段半价；按供应商的说明，中国法定节假日不算峰时。人物决策输入不读墙钟，人物经历的一切与启动时刻无关，启动时刻只影响费用。

长时间的模型运行尽量放在非峰时段：

| 时段（UTC） | 北京时间 | 长度 |
| --- | --- | --- |
| 周五 10:00 至周一 01:00 | 周五 18:00 至周一 09:00 | 连续 63 小时 |
| 工作日 10:00 至次日 01:00 | 18:00 至次日 09:00 | 15 小时 |
| 工作日 04:00 至 06:00 | 12:00 至 14:00 | 2 小时 |

省下的钱是落在峰时的那部分调用费用的一半。一周 168 小时里有 35 小时是峰时，随意时刻启动的运行约有两成调用落在
峰时；整段放进非峰时段，比随意时刻启动平均省约 16%。例如一次 30 天白领运行共 19.70 美元，35% 的调用在峰时，
整段放在非峰时可省 4.86 美元。模型时间每模拟日约半小时，30 天的运行至少要十几个小时；放不进一个非峰时段的运行
会跨过峰时，照常完成，只是那段费用高一倍。

导出后在 `quality.json` 的 `model_usage.by_call_class` 里看每类调用的 `peak_share`：整段落在非峰时段的运行，有可计价响应的类都是 0；没有可计价响应的类为 null。
质量报告按 UTC 工作日时段判定峰时，不读节假日：落在工作日法定节假日的调用实际按非峰时计费，报告里仍按峰时估价。
启动前先看一眼供应商的价格页（https://api-docs.deepseek.com/quick_start/pricing/），时段或价格变了以价格页为准；
质量报告按冻结的 `pricing_snapshot`（核对日期写在 `checked_at`）估价。

## 从检查点创建分支

父运行的共同检查点同时冻结世界和人物工作区。恢复必须使用同一完整输入及实际服务身份，
新输出目录不能覆盖父运行。

```powershell
worldmock resume --environment D:/datasets/my-world --parent .work/runs/my-world --checkpoint CHECKPOINT_ID --output .work/runs/resumed
worldmock replay --environment D:/datasets/my-world --parent .work/runs/my-world --checkpoint CHECKPOINT_ID --intervention .work/intervention.json --output .work/runs/replay
```

检查点身份从实际运行记录读取，不猜测身份。只读历史查询使用冻结事实；恢复与重放使用父运行冻结的模型元数据，不访问模型服务的 /models；检查点身份不对时在复制任何文件之前报错。
没有旧结构迁移或失败后的替代执行。完整运行要求正常终态和成功最终检查点；失败或只到截止时间不能宣称完整。

## 验证与报告

```powershell
python -m pytest tests
python scripts/build_design_doc.py
```

机器报告、覆盖率、执行回执与生成设计 HTML 在 .work。当前设计从[文档入口](../INDEX.md)阅读，
真实性对比与盲评操作见[评价手册](evaluation.md)。
