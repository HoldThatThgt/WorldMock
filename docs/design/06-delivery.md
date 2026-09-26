# WorldMock 产品设计说明书 — 数据交付

> **状态**：DRAFT  
> **日期**：2026-09-26  
> **版本**：v1.5
> **目标读者**：算法工程师、开发工程师

## 1. 模块概述

### 1.1 职责与边界

数据交付模块（Delivery）负责将数字足迹转换为 LabelKit 训练数据与规则夹具，是 WorldMock 与外部数据消费者的唯一边界。

**职责**：
- 从认证导出构造证据窗口（动作、意图、dreaming）
- 字段字典编码：无损压缩重复证据字段
- 调用独立 LabelKit CLI 生成语义候选
- 组装事实目标：真实动作、时间、来源
- 执行真实训练加载器与规则引擎校验
- 发布原子性交付清单：绑定全部输入输出文件

**边界**：
- 不读取运行中数据库（只消费认证导出快照）
- 不修改原始事件（导出保持只读）
- 不自动裁剪输入（超限明确失败）
- 不补造校验失败结果（失败即失败）
- 不单独交付未完整产物（发布清单前全部校验通过）

### 1.2 与其他模块的关系

```mermaid
flowchart TB
    FOOT[FootprintStore<br/>数字足迹] -->|export 命令| EXP[认证导出<br/>只读快照]
    EXP --> WIN[窗口构造器<br/>证据窗口]
    WIN --> ENC[字段字典编码<br/>RFC 6901]
    ENC --> LK[LabelKit CLI<br/>语义候选]
    LK --> SEM[语义校验器<br/>结构与引用]
    SEM --> FACT[事实组装器<br/>真实目标]
    FACT --> TRAIN[训练加载器<br/>DatasetDict]
    EXP --> RULE[规则夹具生成]
    RULE --> LKG[LabelKit sequence<br/>扰动变体]
    LKG --> EXEC[规则引擎执行]
    TRAIN --> MAN[交付清单<br/>SHA-256 绑定]
    EXEC --> MAN
```

### 1.3 核心设计原则

| 原则 | 含义 | 体现 |
| --- | --- | --- |
| **只读快照** | LabelKit 永不读取运行中数据 | 导出是认证的不可变快照 |
| **原子性发布** | 全部校验通过才写成功清单 | 失败写 failure.json 并传播异常 |
| **事实权威** | 真实动作时间来自环境 | LabelKit 只生成语义候选 |
| **来源关联** | 每个样本绑定事件与运行 | 支持溯源与连通组划分 |
| **fail-fast** | 超限或不一致立即失败 | 不静默截断，不补造结果 |

## 2. 接收记录引擎导出

记录引擎负责固定截止、事件与素材导出、共同检查点和导出完整性认证，
格式与发布流程统一见[记录引擎的固定截止导出](05-recording-engine.md#固定截止导出)。
交付模块只消费认证快照；不完整导出用于诊断，不能进入正式交付。
交付先核对导出最终检查点的 `state.schema_version` 等于当前 `WORLD_SCHEMA_VERSION`，不符即拒绝，不构造窗口：交付时用当前代码的场景渲染重建已存观察，旧结构的场景不能按新渲染解释。
交付从下面的证据窗口开始，训练记录和规则夹具属于 LabelKit 加工产物。

## 3. 证据窗口

### 3.1 三类窗口

| 窗口类型 | 知识截止 | 边界 | 用途 |
| --- | --- | --- | --- |
| **动作窗口** | 动作请求前最后观察 | `action_requested` 事件前 | 训练下一动作预测 |
| **意图窗口** | 动作结果已可见 | 包含可见结果事件 | 训练意图识别 |
| **dreaming 窗口** | 人物自然日结束 | 按当日 00:00-23:59 | 训练经验归纳与反思 |

### 3.2 窗口构造流程

```mermaid
flowchart TB
    E[认证导出] --> A[按 action_id 分组]
    E --> D[按 decision_id 分组]
    E --> DAY[按人物自然日分组]
    A --> AW[动作窗口]
    A --> IW[意图窗口]
    D --> AW
    D --> IW
    DAY --> DW[dreaming 窗口]
    AW --> P[按目标实际可见范围投影字段]
    IW --> P
    DW --> P
    P --> F[字段字典编码]
```

### 3.3 动作窗口提取规则

动作窗口读取目标动作前实际发送给该人物模型的行动请求作为 prompt，
包含当时的 system、user、工具定义与消息历史；不能从结构化观察推测人物卡片或记忆。
意图窗口沿用这一真实请求，并可包含截止前已经发生且实际可见的动作与结果。
原请求事件身份和 canonical 摘要进入来源索引；请求缺失、跨人物、跨决策或与目标动作不匹配时失败。
同一窗口的事件必须归属同一数字人，并满足知识截止。私有事件与字段的边界遵循
[交付规格](../dev/SPEC-labelkit-delivery.md)和[记录引擎设计](05-recording-engine.md)「来源与可见性」。

```mermaid
flowchart LR
    EVENTS[按数字人与动作关联的事件] --> CUTOFF[按训练目标确定知识截止]
    CUTOFF --> VISIBLE[选择当时实际可见的事件]
    VISIBLE --> FIELDS[按目标信息范围裁剪字段与素材]
    FIELDS --> INPUT[模型可见输入]
    EVENTS --> FACTS[独立保存事实目标与审计证据]
```

| 检查层次 | 规则 |
| --- | --- |
| 事件可见性 | 动作与意图输入排除模型请求、模型响应、记忆更新与决策自述；dreaming 对决策自述的读取遵循其专用合同；`conclude_activity.memories` 属记忆写入，意图与 `dreaming` 输入裁去该参数 |
| 身体字段可见性 | 同一观察中，体感分级可供数字人理解处境；`body.values` 和 `motivation_internal` 只保留在内部证据中 |
| 结算字段可见性 | `body_settlement` 等内部身体变化不能因所在事件标为 `agent` 就整体复制到输入；只提供当时实际呈现的结果 |
| 实际 prompt | 只从本人行动阶段的 `model_request` 提取模型可见消息与工具；私有工作评审请求、响应及 reasoning 不进输入或目标；零点计划的请求带 `purpose="plan_day"`，不作窗口 prompt |
| 前置状态 | 当观察已经出现在窗口事件中，只保留前置观察引用，不复制整份 `events[0].data` |
| 目标结果 | 只收该动作公开结算事实；私有 `model_request`、`model_response` 和记忆更新不得成为 `target.outcomes`；动作窗口的目标保留完整请求（含 `conclude_activity.memories`），记忆更新事件仍不进 `target.outcomes` |
| 来源关联 | 裁剪后的输入保留原事件引用，完整事实与内部数值仍可用于审计；不得据此向模型补充隐藏状态 |

事件的 `visibility` 标记不能代替字段级投影。进入 LabelKit 的具体信息范围以训练目标为准，
不得默认下游设备能够读到内部身体数值。投影后的内容再进行字段字典编码，不能靠编码隐藏敏感字段。

窗口构造以公开字段投影和实际 prompt 来源双重核验：结构化观察中即使保存了菜单、
内部身份（旧导出另含已删除的能量可行性）或可供性合同，也不能把原始 `data` 整棵复制进训练输入。
测试必须证明禁用字段、重复前置状态和私有工作评审请求均不泄漏，且人物卡片、记忆与场景与所选实际请求一致。

### 地图与门店供给的证据窗口

购买动作按供给身份绑定门店、卖方和当时报价，同时保留商品身份供已交付物品及消费解释。
地点发现的筛选、实际结果和后续选择按真实事件先后进入窗口，不用当前地图目录补写历史查询结果。
只纳入人物实际收到的路线、营业、资格、报价和供给库存，隐藏库存、未呈现供给、未来报价及未来营业声明不进入训练输入。
真实地点来源与供给假设分别保留；地图版本、摘要、逐来源许可及通知从认证导出传递到交付清单。
来源完整只证明可追溯，公开交付的许可适用仍按具体来源、组合与使用方式判断。


### 3.4 dreaming 窗口提取规则

离线 Dreaming 由数据交付负责：从认证导出构造经历窗口，通过 LabelKit 生成经历归纳、反思或假设推演。
材料按场景时区与人物自然日组织，保留来源事件和知识截止，不混入未来经历；
私有材料遵循 Dreaming 专用合同，不能因此进入动作或意图任务。生成结果默认不写回运行中的人物。
零点计划的模型交换与 `day_plan_generated` 登记（系统可见）不进任何窗口；零点及其后的事件归入新一日。

| 结果 | 证据与标记 |
| --- | --- |
| 经历归纳或反思 | 引用已发生且符合知识截止的经历；归纳结果不改变原始事实 |
| 模型想象的反事实 | 明确假设，使用原世界证据；`world_kind=observed`、`environment_replay=null` |
| 实际重新仿真的反事实 | 父检查点、新分支、干预、实际结果与最终检查点均通过认证，才可标为 `world_kind=resimulated` |

需要真实反事实结果时，由[模拟引擎执行明确的反事实运行](04-simulation-engine.md#9-恢复与反事实运行编排)，
记录引擎保存分支并认证导出。交付通过 `deliver --replay-export` 消费完成的分支；
父检查点前历史、分支身份、干预摘要和可见来源必须对应，生成文本或分支文件名不能代替实际执行证据。
字段定义见[反事实与重演字段](../CONTRACTS.md#反事实与重演字段)。

```python
def _dream_windows(events: list[dict], manifest: dict) -> list[dict]:
    """按人物自然日构造 dreaming 窗口
    
    每个窗口包含：
    - 当日全部可见事件（含部分私有事件）
    - decision_started 中的 decision_statement
    - 观察事件的 scene 描述
    """
    by_agent_day = defaultdict(list)
    for event in events:
        if event["agent_id"]:
            day = datetime.fromisoformat(event["occurred_at"]).date()
            by_agent_day[(event["agent_id"], day)].append(event)
    
    windows = []
    for (agent_id, day), day_events in by_agent_day.items():
        # 只取人物可见事件，并过滤 dreaming 不可见的私有事件
        visible = [e for e in day_events
                   if e["visibility"] == "agent"
                   and e["event_type"] not in DREAMING_PRIVATE_EVENTS]
        window = _record("dreaming", visible, {}, manifest, {})
        windows.append(window)
    
    return windows
```

## 4. 字段字典编码

### 4.1 编码目标

重复出现的证据字段值（如素材文本、观察场景）占用大量字节，字段字典编码通过 RFC 6901 JSON Pointer 实现无损压缩。

**编码原则**：
- 只在单个窗口内共享字段值
- 使用 canonical JSON 字节计算收益
- 收益不足时保留原值、清空字典
- 解码后必须字节相同

### 4.2 编码示例

**原始输入**（重复 scene 字段）：
```json
{
  "events": [
    {"event_id": "e1", "data": {"scene": "你在家中客厅，时间是上午9点..."}},
    {"event_id": "e2", "data": {"scene": "你在家中客厅，时间是上午9点..."}}
  ]
}
```

**编码后**：
```json
{
  "observation_value_encoding": {
    "kind": "rfc6901_field_dictionary",
    "version": 1,
    "rule": "Each observation data field whose value is an object containing only value_pointer...",
    "decoded_input_sha256": "abc123..."
  },
  "observation_value_dictionary": [
    {
      "field_name": "scene",
      "value_sha256": "def456...",
      "value": "你在家中客厅，时间是上午9点..."
    }
  ],
  "events": [
    {"event_id": "e1", "data": {"scene": {"value_pointer": "/observation_value_dictionary/0/value"}}},
    {"event_id": "e2", "data": {"scene": {"value_pointer": "/observation_value_dictionary/0/value"}}}
  ]
}
```

### 4.3 编码算法

```python
def encode_model_input(input_data: dict) -> dict:
    """为一个完整逻辑窗口生成确定性模型输入表示
    
    1. 收集候选：遍历 events[].data 的字段值
    2. 计算收益：重复值总字节 vs 字典项 + 指针总字节
    3. 选择有利：只共享收益为正的字段值
    4. 渲染编码：替换为 value_pointer
    5. 验证往返：decode 后必须与原输入 canonical JSON 相同
    """
    candidates = _collect_candidates(input_data)
    selected = _select_profitable_candidates(candidates)
    encoded = _render_encoding(input_data, candidates, selected)
    
    # 收益不足时保留原值
    if len(canonical_json(encoded)) >= len(canonical_json(input_data)):
        return input_data  # 不编码
    
    # 验证往返
    decoded = decode_model_input(encoded)
    assert canonical_json(decoded) == canonical_json(input_data)
    
    return encoded
```

### 4.4 解码校验

```python
def decode_model_input(encoded: dict) -> dict:
    """严格解析单跳字段引用并恢复完整逻辑窗口
    
    校验规则：
    - 字典项摘要必须匹配
    - 指针只能出现在 events[].data 字段
    - 不允许嵌套引用
    - 所有字典项必须被使用
    - 解码后摘要必须匹配 decoded_input_sha256
    """
    dictionary = encoded["observation_value_dictionary"]
    result = copy.deepcopy(encoded)
    used = set()
    
    for event in result["events"]:
        for field, value in event["data"].items():
            if isinstance(value, dict) and "value_pointer" in value:
                # 解析 /observation_value_dictionary/N/value
                index = _parse_pointer(value["value_pointer"])
                entry = dictionary[index]
                
                # 校验摘要
                actual_sha256 = hashlib.sha256(canonical_json(entry["value"]).encode()).hexdigest()
                if actual_sha256 != entry["value_sha256"]:
                    raise IntegrityError("Dictionary entry digest mismatch")
                
                event["data"][field] = entry["value"]
                used.add(index)
    
    # 校验所有字典项被使用
    if used != set(range(len(dictionary))):
        raise IntegrityError("Unused dictionary entries")
    
    result.pop("observation_value_encoding")
    result.pop("observation_value_dictionary")
    
    return result
```

## 5. LabelKit 集成

### 5.1 项目配置结构

```
labelkit-projects/
├── config.toml            # 全局配置：LLM、并发、超时
├── action.toml            # 动作任务配置
├── intent.toml            # 意图任务配置
├── dreaming.toml          # dreaming 任务配置
├── rules.toml             # 规则夹具生成配置
├── schemas/               # JSON Schema 定义
├── data/                  # 输入数据（运行时生成）
└── out/                   # 输出结果（运行时生成）
```

### 5.2 config.toml 示例

```toml
schema_version = 1

[tool]
log_level = "info"
log_format = "text"

[llm.default]
provider = "openai_compatible"
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
api_key_env = "DEEPSEEK_API_KEY"
max_concurrency = 12
timeout_s = 180
max_output_tokens = 131072
context_window = 1000000
temperature = 0.3

[llm.judge]
provider = "openai_compatible"
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
api_key_env = "DEEPSEEK_API_KEY"
max_concurrency = 12
timeout_s = 360
temperature = 0.0
thinking = "enabled"
```

### 5.3 任务配置示例（action.toml）

```toml
schema_version = 1

[run]
input = "./data/action.jsonl"
output = "./out/action.jsonl"
modality = "text"
mode = "process"
batch_size = 16

[input]
text_field = "input"

[dedup]
enabled = false

[annotate]
enabled = true
llm = "default"
instruction = """
根据动作发生前的可见历史，归纳此时的需求与不确定性。
不能知道尚未发生的下一动作，不猜测隐藏结果。
代码负责真实下一动作和参数。
仅输出 target 中的 claims、intent_candidates 和 uncertainty。
"""

[verify]
enabled = true
llm = "judge"
policy = "repair"
max_repair_rounds = 3

[trace]
enabled = true
channels = ["llm", "verify", "schema"]
content = "full"
```

### 5.4 调用流程

```python
def run_labelkit(task: str, workspace: Path, project_root: Path) -> dict:
    """调用独立 LabelKit CLI，凭据只通过环境变量传递
    
    :param task: 任务名（action/intent/dreaming/rules）
    :param workspace: 工作目录（含 config.toml 和任务 toml）
    :param project_root: 项目根目录
    :return: 执行回执（退出码、耗时、配置哈希）
    """
    binary = project_root / "vendor/labelkit/.venv/bin/labelkit"
    command = [
        str(binary), "run",
        "--config", str(workspace / "config.toml"),
        "--project", str(workspace / f"{task}.toml"),
        "--strict", "--console", "plain"
    ]
    
    # 加载凭据
    credentials = dotenv_values(Path.home() / ".config/worldmock/deepseek.env")
    environment = dict(os.environ)
    environment["DEEPSEEK_API_KEY"] = credentials["DEEPSEEK_API_KEY"]
    
    # 执行
    started = time.monotonic()
    result = subprocess.run(command, env=environment, check=False)
    
    if result.returncode != 0:
        raise IntegrityError(f"LabelKit {task} exited with status {result.returncode}")
    
    # 返回回执
    return {
        "exit_code": result.returncode,
        "wall_seconds": round(time.monotonic() - started, 3),
        "project_sha256": hashlib.sha256((workspace / f"{task}.toml").read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256((workspace / "config.toml").read_bytes()).hexdigest()
    }
```

## 6. 事实组装与校验

### 6.1 语义候选结构

LabelKit 只生成待标注的语义候选，不包含真实动作与来源：

```json
{
  "target": {
    "claims": [
      {
        "text": "数字人的观察中显示饥饿体感为“饿”",
        "kind": "fact",
        "evidence_event_ids": ["obs_123"]
      }
    ],
    "intent_candidates": ["satisfy_hunger", "continue_work", "unknown"],
    "uncertainty": "无法确定是否会立即进食或继续工作"
  }
}
```

### 6.2 事实组装流程

上述示例要求 `obs_123` 的实际可见内容包含“饿”这一体感分级。
准确身体数值属于世界事实；交付时按[记录引擎设计](05-recording-engine.md)「来源与可见性」裁剪，
不得把环境内部能量数值或派生分数补成数字人已知的事实。

```mermaid
sequenceDiagram
    participant LK as LabelKit
    participant SEM as 语义校验器
    participant FACT as 事实组装器
    participant TRAIN as 训练加载器
    
    LK->>SEM: 语义候选
    SEM->>SEM: 结构校验（Schema）
    SEM->>SEM: 引用校验（event_ids 存在）
    SEM->>SEM: 语义评审（LLM judge）
    SEM-->>FACT: 通过候选
    FACT->>FACT: 组装真实目标
    FACT->>FACT: 添加 task, input, provenance
    FACT-->>TRAIN: 完整训练记录
    TRAIN->>TRAIN: 加载为 DatasetDict
    TRAIN->>TRAIN: 验证往返一致性
```

### 6.3 事实组装实现

```python
def attach_facts(candidate: dict, window: dict) -> dict:
    """评审通过后组装真实动作、时间与来源字段
    
    :param candidate: LabelKit 输出的语义候选
    :param window: 原始证据窗口（含 facts 字段）
    :return: 完整训练记录
    """
    facts = window["facts"]
    request = facts["request"]  # action_requested 事件
    
    record = {
        "task": window["task"],
        "input": window["input"],  # 编码后的证据窗口
        "target": {
            **candidate["target"],  # 语义候选
            "action": request["data"]["tool_name"],  # 真实动作名
            "parameters": request["data"]["arguments"],  # 真实参数
            "timestamp": request["occurred_at"]  # 真实时间
        },
        "provenance": {
            "run_id": window["provenance"]["run_id"],
            "agent_id": window["provenance"]["agent_id"],
            "source_event_ids": window["provenance"]["source_event_ids"],
            "source_group_id": window["provenance"]["source_group_id"]
        }
    }
    
    return record
```

### 6.4 语义校验规则

```python
def validate_annotation(candidate: dict, window: dict) -> tuple[bool, list[str]]:
    """校验语义候选的结构与引用
    
    :return: (是否通过, 失败原因列表)
    """
    errors = []
    
    # 结构校验
    if not isinstance(candidate.get("target"), dict):
        errors.append("Missing target object")
    
    target = candidate["target"]
    
    # claims 上限
    if len(target.get("claims", [])) > 32:
        errors.append(f"Too many claims: {len(target['claims'])} > 32")
    
    # 引用校验
    allowed = set(window["input"]["allowed_evidence_event_ids"])
    for claim in target.get("claims", []):
        for event_id in claim.get("evidence_event_ids", []):
            if event_id not in allowed:
                errors.append(f"Claim references unknown event: {event_id}")
    
    # intent_candidates 上限与去重
    candidates = target.get("intent_candidates", [])
    if len(candidates) > 8:
        errors.append(f"Too many intent candidates: {len(candidates)} > 8")
    if len(candidates) != len(set(candidates)):
        errors.append("Duplicate intent candidates")
    
    return len(errors) == 0, errors
```

## 7. 发布协议

### 7.1 deliver 命令

```python
def deliver(
    export_dir: Path,
    destination: Path,
    project_root: Path,
    replay_exports: tuple[Path, ...] = ()
) -> dict:
    """执行完整交付流程，全部校验通过才发布成功清单
    
    :param export_dir: 认证导出目录
    :param destination: 交付目标目录（不得已存在）
    :param project_root: 项目根目录（含 labelkit-projects/）
    :param replay_exports: 反事实分支导出（可选）
    :return: 交付清单
    :raises IntegrityError: 任何环节失败
    """
    if destination.exists():
        raise IntegrityError("Delivery destination already exists")
    _require_current_world_schema(export_dir)  # 最终检查点 state.schema_version 必须等于 WORLD_SCHEMA_VERSION
    
    # 构造窗口
    windows = prepare_windows(export_dir)
    for replay_dir in replay_exports:
        windows["dreaming"].extend(replay_windows(export_dir, replay_dir))
    
    destination.mkdir(parents=True)
    
    try:
        # 执行交付
        manifest = _execute_delivery(windows, destination, project_root)
        
        # 绑定来源
        manifest["source_export"] = _export_reference(export_dir)
        manifest["replay_exports"] = [_export_reference(p) for p in replay_exports]
        
        # 文件清单
        manifest["files"] = _file_inventory(destination)
        
        # 发布成功清单
        atomic_write(destination / "manifest.json", canonical_json(manifest))
        
        return manifest
    
    except Exception as error:
        # 失败时写 failure.json 并传播异常
        logging.exception("Dataset delivery failed before publication")
        failure = {"status": "failed", "error_type": type(error).__name__}
        atomic_write(destination / "failure.json", canonical_json(failure))
        raise
```

### 7.2 原子性保证

```mermaid
flowchart TB
    A[开始交付] --> B{目标已存在?}
    B -->|是| Z[失败：拒绝覆盖]
    B -->|否| V{最终检查点世界结构版本等于当前?}
    V -->|否| Y[失败：版本不符，拒绝交付]
    V -->|是| C[创建目标目录]
    C --> D[准备窗口]
    D --> E[调用 LabelKit]
    E --> F[语义校验]
    F --> G[事实组装]
    G --> H[训练加载]
    H --> I[规则执行]
    I --> J{全部通过?}
    J -->|否| K[写 failure.json]
    K --> L[传播异常]
    J -->|是| M[写 manifest.json]
    M --> N[成功]
```

**原子性规则**：
- 成功清单（`manifest.json`）最后写入
- 失败立即写 `failure.json` 并中止
- 已存在目标拒绝执行（防止覆盖）
- 部分产物不单独交付

### 7.3 manifest.json 结构

下列片段展示原有清单组织，不是完整 Schema。地图与门店供给特性的清单还必须保留认证来源导出中
冻结的地图集合身份、版本、摘要、逐来源许可及通知，不能仅留下商品名称或生成文本中的署名。

```json
{
  "schema_version": 1,
  "committed": true,
  "run_id": "run_20260920_001",
  "source_group_id": "group_001",
  "source_export": {
    "path": "/absolute/path/to/export",
    "manifest_sha256": "abc123..."
  },
  "replay_exports": [
    {"path": "/path/to/replay", "manifest_sha256": "def456..."}
  ],
  "artifacts": {
    "training": {"path": "training.jsonl", "row_count": 1234, "sha256": "..."},
    "rules": {"path": "rules.jsonl", "row_count": 56, "sha256": "..."}
  },
  "labelkit": {
    "action": {"exit_code": 0, "wall_seconds": 45.2, "project_sha256": "...", "trace_sha256": "..."},
    "intent": {"exit_code": 0, "wall_seconds": 38.1, "project_sha256": "...", "trace_sha256": "..."},
    "dreaming": {"exit_code": 0, "wall_seconds": 52.3, "project_sha256": "...", "trace_sha256": "..."},
    "rules": {"exit_code": 0, "wall_seconds": 12.1, "project_sha256": "..."}
  },
  "training_receipt": {
    "dataset_info": {...},
    "split_info": {...}
  },
  "rule_results": [
    {"fixture_id": "f1", "passed": true, "actual": "...", "expected": "..."}
  ],
  "semantic_review": {
    "method": "LabelKit separate verify calls",
    "same_model_bias": true
  },
  "files": {
    "training.jsonl": {"size_bytes": 12345, "sha256": "..."},
    "manifest.json": {"size_bytes": 6789, "sha256": "..."}
  }
}
```

### 7.4 文件清单绑定

```python
def _file_inventory(directory: Path) -> dict:
    """最终清单绑定所有加工输入、输出、评审及消费者文件
    
    :return: {相对路径: {size_bytes, sha256}}
    :raises IntegrityError: 发现符号链接
    """
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise IntegrityError("Delivery contains a symbolic file")
        if path.is_file():
            raw = path.read_bytes()
            files[path.relative_to(directory).as_posix()] = {
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest()
            }
    return files
```

## 8. 来源关联

### 8.1 provenance 字段

每个训练记录携带完整来源链：

```json
{
  "provenance": {
    "run_id": "run_20260920_001",
    "agent_id": "agent_alice",
    "source_event_ids": ["e1", "e2", "e3"],
    "source_group_id": "group_001",
    "window_id": "action_e1_abc123"
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `run_id` | 运行唯一标识 |
| `agent_id` | 人物唯一标识 |
| `source_event_ids` | 证据事件列表 |
| `source_group_id` | 来源连通组 |
| `window_id` | 窗口唯一标识 |

### 8.2 来源连通组

**连通组规则**：
- 同一活动的多次动作属于同一组
- 同一人物同一天的 dreaming 属于同一组
- 跨活动的意图可引用多个组

**数据划分约束**：
```python
def split_by_source_group(records: list[dict], splits: dict[str, float]) -> dict:
    """按来源连通组划分数据集，防止泄漏
    
    :param splits: {"train": 0.8, "val": 0.1, "test": 0.1}
    :return: {"train": [...], "val": [...], "test": [...]}
    """
    by_group = defaultdict(list)
    for record in records:
        group_id = record["provenance"]["source_group_id"]
        by_group[group_id].append(record)
    
    # 整组划分
    groups = list(by_group.keys())
    random.shuffle(groups)
    
    train_count = int(len(groups) * splits["train"])
    val_count = int(len(groups) * splits["val"])
    
    train_groups = groups[:train_count]
    val_groups = groups[train_count:train_count + val_count]
    test_groups = groups[train_count + val_count:]
    
    return {
        "train": [r for g in train_groups for r in by_group[g]],
        "val": [r for g in val_groups for r in by_group[g]],
        "test": [r for g in test_groups for r in by_group[g]]
    }
```

### 8.3 素材引用

```json
{
  "event_id": "e1",
  "event_type": "content_accessed",
  "data": {
    "content_reference": {
      "sha256": "abc123...",
      "size_bytes": 4096,
      "media_type": "text/plain",
      "source": "file"
    }
  }
}
```

**素材溯源**：
- 每个素材按 SHA-256 存储在 `assets/`
- 事件引用素材摘要，不直接嵌入内容
- 导出时展开为实际证据
- 相同内容去重存储

## 9. 质量保证

### 9.1 完整性校验清单

| 校验项 | 时机 | 失败行为 |
| --- | --- | --- |
| 目标不存在 | deliver 开始 | 拒绝执行 |
| 最终检查点世界结构版本 | deliver 开始（准备窗口前） | 抛出 IntegrityError，不构造窗口 |
| 导出认证 | prepare_windows | 抛出 IntegrityError |
| 跨人物分组 | 窗口构造 | 拒绝证据分组 |
| 编码往返 | encode_model_input | 断言失败 |
| LabelKit 退出码 | run_labelkit | 抛出 IntegrityError |
| 语义候选结构 | validate_annotation | 记录错误并拒绝 |
| 引用事件存在 | validate_annotation | 记录错误并拒绝 |
| 训练加载往返 | load_training | 抛出异常 |
| 规则执行一致 | execute_fixture | 记录结果 |
| 文件清单 | _file_inventory | 拒绝符号链接 |

### 9.2 SHA-256 哈希链

```mermaid
flowchart LR
    E[events.jsonl] -->|SHA-256| M1[export manifest]
    A[assets/*] -->|SHA-256| M1
    M1 -->|SHA-256| M2[delivery manifest]
    T[training.jsonl] -->|SHA-256| M2
    R[rules.jsonl] -->|SHA-256| M2
    LK[labelkit logs] -->|SHA-256| M2
```

**哈希绑定规则**：
- 导出清单绑定事件流与素材哈希
- 交付清单绑定导出清单哈希
- 交付清单绑定全部产物文件哈希
- 任何文件修改导致清单校验失败

### 9.3 字段验证

```python
def validate_training_record(record: dict) -> list[str]:
    """验证训练记录字段完整性与类型
    
    :return: 错误列表（空列表表示通过）
    """
    errors = []
    
    # 必需顶层字段
    for field in ["task", "input", "target", "provenance"]:
        if field not in record:
            errors.append(f"Missing required field: {field}")
    
    # task 取值
    if record.get("task") not in {"action", "intent", "dreaming"}:
        errors.append(f"Invalid task: {record.get('task')}")
    
    # input 必须是对象
    if not isinstance(record.get("input"), dict):
        errors.append("input must be an object")
    
    # target 必须包含 action 或 claims
    target = record.get("target", {})
    if "action" not in target and "claims" not in target:
        errors.append("target must contain action or claims")
    
    # provenance 必需字段
    prov = record.get("provenance", {})
    for field in ["run_id", "agent_id", "source_event_ids", "source_group_id"]:
        if field not in prov:
            errors.append(f"provenance missing: {field}")
    
    return errors
```

### 9.4 数据加载验证

```python
def load_training(records: list[dict], output: Path) -> tuple[DatasetDict, dict]:
    """加载为真实 DatasetDict 并验证往返一致性
    
    :return: (DatasetDict, 加载回执)
    """
    # 按连通组划分
    splits = split_by_source_group(records, {"train": 0.8, "val": 0.1, "test": 0.1})
    
    # 创建 Dataset
    dataset_dict = DatasetDict({
        split_name: Dataset.from_list(rows)
        for split_name, rows in splits.items()
    })
    
    # 保存到磁盘
    dataset_dict.save_to_disk(output)
    
    # 重新加载验证
    reloaded = DatasetDict.load_from_disk(output)
    
    # 验证行数守恒
    for split_name in splits.keys():
        if len(reloaded[split_name]) != len(dataset_dict[split_name]):
            raise IntegrityError(f"Row count mismatch after reload: {split_name}")
    
    receipt = {
        "dataset_info": {
            "total_rows": sum(len(ds) for ds in dataset_dict.values()),
            "splits": {name: len(ds) for name, ds in dataset_dict.items()}
        },
        "split_info": {
            name: {"row_count": len(ds), "features": list(ds.features.keys())}
            for name, ds in dataset_dict.items()
        }
    }
    
    return dataset_dict, receipt
```

## 10. 端到端流程图

```mermaid
flowchart TB
    START[worldmock export] --> EXP[认证导出快照]
    EXP --> WIN[准备证据窗口]
    WIN --> ACT[动作窗口]
    WIN --> INT[意图窗口]
    WIN --> DRM[dreaming 窗口]
    
    ACT --> ENC1[字段字典编码]
    INT --> ENC2[字段字典编码]
    DRM --> ENC3[字段字典编码]
    
    ENC1 --> LK1[LabelKit action]
    ENC2 --> LK2[LabelKit intent]
    ENC3 --> LK3[LabelKit dreaming]
    
    LK1 --> VAL1[语义校验]
    LK2 --> VAL2[语义校验]
    LK3 --> VAL3[语义校验]
    
    VAL1 --> FACT[事实组装]
    VAL2 --> FACT
    VAL3 --> FACT
    
    FACT --> TRAIN[训练加载器]
    TRAIN --> DS[DatasetDict]
    
    EXP --> RULE[规则夹具生成]
    RULE --> LKR[LabelKit sequence]
    LKR --> EXEC[规则引擎执行]
    
    DS --> MAN[交付清单]
    EXEC --> MAN
    MAN --> PUB[发布 manifest.json]
    PUB --> END[完成]
```

## 版本历史

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| v1.0 | 2026-09-20 | 初版：模块概述、导出流程、证据窗口、字段字典编码、LabelKit 集成、事实组装、发布协议、来源关联、质量保证、端到端流程 |
| v1.1 | 2026-09-20 | 身体事实示例改为实际可见的体感分级，明确内部数值的交付边界 |
| v1.2 | 2026-09-20 | 足迹导出的流程与格式归入记录引擎，本文从认证快照的消费开始 |
| v1.3 | 2026-09-24 | 按时间与能量规格：意图与 dreaming 输入裁去 `conclude_activity.memories`，动作目标保留完整请求；零点计划的请求与登记不进窗口，零点起归入新一日；观察中不得整棵复制的内容由能量可行性改为内部身份（旧导出另含能量可行性） |
| v1.4 | 2026-09-25 | 按时间与能量规格第九章：交付先核对导出最终检查点的世界结构版本，不符即拒绝，不构造窗口 |
| v1.5 | 2026-09-26 | 按时间与能量规格阶段 7 文档一致性核对：dreaming 窗口示意代码补人物可见性过滤，与零点计划登记等系统可见事件不进窗口一致 |

---

**背书**：

- LabelKit v1.21 接口规范
- RFC 6901 JSON Pointer 标准
- Hugging Face Datasets 库 `save_to_disk` / `load_from_disk` API
- 数据划分按来源连通组防止泄漏（标准机器学习实践）
