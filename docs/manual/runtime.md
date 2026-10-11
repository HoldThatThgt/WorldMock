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

完整验证先核对全部输入和实际模型元数据，失败时按所属路径与英文原因修正。
run 不接受 --scenario；不读取固定供应商私密文件，不使用默认模型或另一份目录路径。
凭据正文不写进输入包、运行清单或报告。

<a name="runtime-编写-config-toml"></a>
## 编写 config.toml

在环境根的 config.toml 声明时间、唯一种子、活动结算、重估与进度播报节奏、商品呈现轮转、日历及 model 表。
完整字段、重试预算、回合上限和合法范围见[配置设计](../design/08-configuration.md)。
工作安排和每日差异在[人物](personas.md#personas-编写-persona-toml)声明。

start_time 与 stop_time 使用带偏移的 TOML 原生日期时间，按自身偏移整点并与 timezone 一致。
stop_time 必须晚于开始时刻。唯一整数 seed 控制地图、天气、内容、功能 app、购买时长与推送。

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
start_side_activity 开始购买或使用一个内容 app；要同时开始两项，就在同一次回复里两个工具各调用一次。

```text
start_main_activity  {"action": "travel",   "target": "公司", "detail": "驾车"}
start_main_activity  {"action": "interact", "target": "家",   "detail": "测试休息"}
start_main_activity  {"action": "sleep",    "target": "470",  "detail": null}
start_side_activity  {"action": "purchase", "target": ["牛肉面", "柠檬茶", "柠檬茶"]}
start_side_activity  {"action": "interact", "target": ["哔哩哔哩"]}
```

出行的 detail 照工具层该目的地下列出的出行方式名称写（步行，以及数据集定义的方式）；在地点做事的 detail 照工具层当前地点下列出的名称写。
睡眠的 target 写 10 到 720 的分钟数，只写数字，detail 写 null。购买逐份写商品名，同一个名称写两次就是两份。
例子里的地点、内容、商品和 app 名称取自所用的环境数据集。
query_world 使用 action 查询当下实际地点、营业时段、路线或商品；查询不推进时间。
用内容 app 时看到什么由环境选择，功能 app 由环境执行，人物按真实活动名称报告、暂停与结束。
工具字段、名称解析、槽位与提醒规则见[模拟引擎](../design/04-simulation-engine.md)。
实际模型请求和事件可在运行查看器中核对，人物可见范围见[人物设计](../design/06-agent.md)。

## 从检查点创建分支

父运行的共同检查点同时冻结世界和人物工作区。恢复必须使用同一完整输入及实际服务身份，
新输出目录不能覆盖父运行。

```powershell
worldmock resume --environment D:/datasets/my-world --parent .work/runs/my-world --checkpoint CHECKPOINT_ID --output .work/runs/resumed
worldmock replay --environment D:/datasets/my-world --parent .work/runs/my-world --checkpoint CHECKPOINT_ID --intervention .work/intervention.json --output .work/runs/replay
```

检查点身份从实际运行记录读取，不猜测身份。只读历史查询使用冻结事实；恢复生成调用核验完整模型身份和实际元数据。
没有旧结构迁移或失败后的替代执行。完整运行要求正常终态和成功最终检查点；失败或只到截止时间不能宣称完整。

## 验证与报告

```powershell
python -m pytest tests
python scripts/build_design_doc.py
```

机器报告、覆盖率、执行回执与生成设计 HTML 在 .work。当前设计从[文档入口](../INDEX.md)阅读，
真实性对比与盲评操作见[评价手册](evaluation.md)。
