<!-- worldmock-doc: {"authority":"experiment","canonical_path":"docs/EXPERIMENT.md","implementation_state":"NOT-COMPLETE","kind":"experiment","owner":"worldmock-core","replaces":[],"schema":"worldmock.document.metadata.v1","status":"FROZEN","superseded_by":null,"version":"2026-09-18"} -->
**Status: FROZEN** | **Owner: worldmock-core**

# WorldMock 实验设置

本文件定义 WorldMock 的实验参数、依赖版本、场景绑定和内容来源。

**来源**: 从 `docs/history/agentsociety2-design.md` 提取实验边界、依赖冻结和场景绑定部分

## 实验身份与范围

| 项目 | 冻结值 |
| --- | --- |
| 仿真类型 | 单机 AS2 原生 Ray actor 与人物 runner；正式单人物十二周，同时交付多人测试与实际短运行 |
| 正式日期 | Asia/Shanghai，2026-01-05 00:00:00 到 2026-03-30 00:00:00，左闭右开，共 84 天 |
| 人物共同背景 | 上海张江生活区居住、张江高科技园区工作的白领；与旧数据对齐到可证明的程度 |
| 未共同定义的属性 | 年龄、工资、初始存款、身体参数单独声明为新实验假设，不能当作旧数据已匹配条件 |

**来源**: `docs/history/agentsociety2-design.md:828-833`

## 依赖版本

### 模型

| 组件 | 版本/端点 | 说明 |
| --- | --- | --- |
| 人物生成模型 | DeepSeek 官方 OpenAI-compatible 端点，`deepseek-flash` | 保存实际返回模型名、fingerprint、请求参数和 token 使用 |
| 模型冻结 | - | 固定端点、角色、提示、Pydantic 基础工具合同、枚举绑定算法和模型参数；每轮枚举只来自当前观察，运行中不切换模型或代选行动 |

**来源**: `docs/history/agentsociety2-design.md:834-835`

### 框架依赖

| 组件 | 版本 | 位置 |
| --- | --- | --- |
| AS2 基线 | 基于 `670c94fff7c64c4f79b632125f2ccf968155e746` | `vendor/agentsociety2` 的维护分支 `worldmock-life` |
| MCP | 1.26.0 | 使用 AS2 上游提交的锁文件 |
| LiteLLM | 1.89.2 | 使用 AS2 上游提交的锁文件 |
| Ray | 2.55.1 | 使用 AS2 上游提交的锁文件 |
| 新增依赖 | - | 统一进入 WorldMock 锁文件 |

**来源**: `docs/history/agentsociety2-design.md:836-837`

### 自定义模块位置

| 模块 | 路径 |
| --- | --- |
| AS2 环境扩展 | `custom/envs/life_env.py` |
| AS2 人物扩展 | `custom/agents/life_agent.py` |
| 运行目录 | `runs/` |

**来源**: `docs/history/agentsociety2-design.md:838`

## 基线数据参照

| 项目 | 值 |
| --- | --- |
| 旧数据来源 | Dataset-Person 的 `archive/white-collar-12-weeks-deepseek16-glm52/repaired/` |
| 旧数据验证 | 原 manifest、修复报告和最终审计共同验证 |
| 旧数据身份 | run `ec5742eff472d320dac4f98c6d1fd478`；main 4,380 行、stream 16,320 行、84 个自然日 |

**说明**: 正式仿真不预设与旧数据相同的事件数量或意图比例。人物根据可见内容、需求、合同和记忆做出选择。世界日历约束营业、工作机会和消息到达，不编写人物每天必须执行的活动序列。

**来源**: `docs/history/agentsociety2-design.md:839-843`

## 场景参数

### 身体初始值

| 参数 | 值 | 单位 | 说明 |
| --- | --- | --- | --- |
| 体重 | 65 | kg | 用于基础消耗计算 |
| 初始可用能量 | 1600 | kcal | 实验假设 |
| 可用能量上限 | 2400 | kcal | 实验假设 |
| 初始睡眠不足 | 0 | hours | 实验假设 |
| 基础消耗率 | 65 | kcal/hour | 按体重 |
| 数值精度 | 1e-9 | kcal | 累计舍入容差 |

**来源**: `docs/history/agentsociety2-design.md:963-970`

### 睡眠模型参数 (两过程模型)

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `sleep_pressure` 上升时间常数 | 18.2 | hours (清醒时趋近 1) |
| `sleep_pressure` 下降时间常数 | 4.2 | hours (睡眠时衰减) |
| 入睡阈值 | 0.67 | 按昼夜相位振幅 0.12 摆动 |
| 醒来阈值 | 0.17 | 按昼夜相位振幅 0.12 摆动 |
| `sleep_deficit_hours` 清醒累积率 | ~0.45 | hours/hour (sleep_need=7.5h 时) |
| `sleep_deficit_hours` 睡眠恢复率 | 1.0 | hours/hour |
| `sleep_deficit_hours` 下限 | 0 | hours |
| `sleep_deficit_hours` 上限 | 24 | hours |

**来源**: `docs/history/agentsociety2-design.md:971-973`

### 效率曲线

**工作效率** (输入: `sleep_deficit_hours`):
- 节点: `(0, 1.0)、(8, 1.0)、(16, 0.85)、(24, 0.65)`
- 插值: 线性

**食品利用效率** (输入: `sleep_deficit_hours`):
- 节点: `(0, 0.90)、(8, 0.90)、(16, 0.85)、(24, 0.80)`
- 插值: 线性

**来源**: `docs/history/agentsociety2-design.md:974-976`

### 需求状态

| 需求 | 范围 | 增长 | 减少 |
| --- | --- | --- | --- |
| 饥饿阈值 | - | - | 800 kcal (饥饿), 400 kcal (紧急) |
| 娱乐需要 | [0, 1] | 清醒每小时 +0.08 | 按偏好和实际接触时长减少 |
| 社交需要 | [0, 1] | 清醒每小时 +0.04 | 交谈、通话与共同用餐按明确规则减少 |

**说明**: 人物收到体感分级而非数值，分级不替人物选择动作。重复接触次数与效果全部留痕。

**来源**: `docs/history/agentsociety2-design.md:976-979`

### 经济参数

| 账户 | 初始余额 | 币种 |
| --- | --- | --- |
| 人物 | 800000 | 分 (CNY) |
| 雇主 | 4000000 | 分 (CNY) |
| 商家 A | 10000000 | 分 (CNY) |
| 商家 B | 10000000 | 分 (CNY) |
| 交通服务 | 10000000 | 分 (CNY) |

**工作合同**:
- 正式主合同: 按验收通过的工作包支付 60000 分
- 工作机会: 每个工作日提供一个最多八小时的工作机会
- 补充场景: 必须同时覆盖计时与计件合同、不同技能、独立内容验收、无资金、延期和返工

**交通报价** (driving):
- 公式: `max(500, ceil(distance_meters * 0.2))` 分
- walking: 0 分
- 来源标记: `fare_source_kind=scenario`

**来源**: `docs/history/agentsociety2-design.md:981-1037`

### 工作参数

| 参数 | 值 | 说明 |
| --- | --- | --- |
| 工作速率 | 任务所需技能的场景等级 | 直接使用 |
| 工作笔记倍率 | 1.10 | `body.work_notes_multiplier` |
| 工作评审最大调用次数 | 3 | 初次调用 + 最多2次输出协议纠正 |

**来源**: `docs/history/agentsociety2-design.md:1029-1030, 986-996`

### 活动与时间

| 参数 | 值 | 说明 |
| --- | --- | --- |
| 活动反馈间隔 | 900 | 秒 (15分钟) |
| 最大连续业务拒绝 | 10 | 达到后记录人物决策失败并停止 |
| 最小活动持续时间 | 60 | 秒 |

**来源**: `docs/history/agentsociety2-design.md:908-910, 328-329`

### 日历事件

| 日期 | 事件 | 说明 |
| --- | --- | --- |
| 2026-02-15 至 02-23 | 春节情境 | 只改变机会与约束，不保证人物执行 |
| 2026-02-14, 02-28 | 调休 | - |
| 2026-01-24, 03-28 | 加班机会 | - |
| 2026-03-12 | 病假情境 | - |
| 2026-03-18 | 事假情境 | - |
| 2026-03-03 | 社交聚餐机会 | - |

**来源**: `docs/history/agentsociety2-design.md:998-999`

## 内容与语料绑定

| 内容类型 | 本次确定方案 |
| --- | --- |
| 食品 | Open Food Facts 具备已验证营养、计量单位的实际产品；冻结条码白名单 |
| 节目 | Blender Sintel 官方完整 SRT 与影片身份；记录为字幕体验 |
| 游戏 | TextWorld 1.7.0，固定 30 个种子 1201–1230 |
| 工作 | AMI ES2002a–d 连续会议材料 |
| 邮件 | 48 个冻结场景线程，明确标为场景创作 |
| 商品 | 食品事实来自来源目录；报价、库存、耐用品效果和使用条件为有版本的场景规则 |
| 地图与路线 | 准备阶段调用 OSRM 获取冻结地点、方式之间的真实道路路线响应；正式运行只读冻结路线 |
| 交通与天气 | 静态道路条件和明确标为场景假设的天气、准备、排队参数 |

**说明**: 具体产品白名单、地点坐标与全部素材哈希在 `corpora/manifest.json` 完成实际取得后冻结。正式运行启动前必须全量核验，不允许带缺失内容启动。

**来源**: `docs/history/agentsociety2-design.md:944-961`

## 数据消费者版本

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| `datasets` | 5.0.1 | 训练加载: `load_dataset("json", features=...)`，实际加载全部成功样本并形成 DatasetDict |
| `rule-engine` | 5.0.2 | 规则消费: 严格 Context 读取时序事实并执行冻结表达式 |

**来源**: `docs/history/agentsociety2-design.md:956-957`

## LabelKit 数据合同

### 处理模式

- 动作、意图和 dreaming: LabelKit 普通 process，固定输入文件且关闭去重、重新语义分段和分类
- 规则扰动: LabelKit 现有 sequence generation，覆盖缺失、乱序和超时

**来源**: `docs/history/agentsociety2-design.md:1040-1053`

### DatasetDict 编码

- `input`, `target`, `provenance`: Datasets 原生 `Json(decode=False)` 保存 JSON 文本
- 核对: 以标准 `json.loads` 逐记录核对原值，保留缺失与 null 的区别和浮点数
- `prompt`, `completion`: 使用已核验 JSON 文本
- 验证: 保存后实际重载并逐项核对

**来源**: `docs/history/agentsociety2-design.md:1062-1067`

### 来源分组

- 相同原始内容、人物运行、恢复分支和 dreaming 派生数据按关联组划分训练、验证和测试
- 所有来源连通组只能进入一个 split
- 只有一个连通组时允许全部进入同一 split，不能为了比例而制造泄漏

**来源**: `docs/history/agentsociety2-design.md:714, 1060`

## 实验假设声明

以下参数为实验假设，不宣称来自已验证的人类生理或心理模型：

- 睡眠影响食品利用效率
- 工作效率与睡眠不足的关系
- 娱乐需要增长与减少规则
- 社交需要增长与减少规则
- 重复接触对娱乐效果的影响

**来源**: `docs/history/agentsociety2-design.md:979, 1031`

## 验收范围

全部能力均属于本次开发；外部参照的人群适用性、模型自身选择与来源数据的限制应如实报告，不能用来隐藏缺失实现。

**来源**: `docs/history/agentsociety2-design.md:796-797`
