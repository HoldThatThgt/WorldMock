<!-- worldmock-doc: {"authority":"interface","canonical_path":"docs/CONTRACTS.md","implementation_state":"NOT-COMPLETE","kind":"contracts","owner":"worldmock-core","replaces":[],"schema":"worldmock.document.metadata.v1","status":"FROZEN","superseded_by":null,"version":"2026-09-18"} -->
**Status: FROZEN** | **Owner: worldmock-core**

# WorldMock 接口合同

本文件定义 WorldMock 的接口签名、字段定义、Schema 和 CLI 命令。

**来源**: 从 `docs/history/agentsociety2-design.md` 提取所有接口、字段、枚举和 Schema 定义

## LifeEnv 状态字段

### 状态归属

| 状态 | 内容 | 修改权限 |
| --- | --- | --- |
| 世界 | `simulation_time`, `locations`, `weather`, `corpus_version`, `calendar`, `scheduled_events` | 环境规则 |
| 人物身体 | `available_energy_kcal`, `sleep_deficit_hours`, `entertainment_need`, `recent_activities`, `physiological_state` | 时间推进、进食、睡眠和活动结算 |
| 人物资产 | `account_balance`, `inventory`, `consumables_count`, `access_rights` | 交易、交付、使用与合同结算 |
| 人物活动 | `activity_type`, `start_time`, `progress`, `next_decision_time`, `occupied_resources`, `pause_reason`, `end_reason` | 人物申请，环境验证并执行 |
| 工作 | `task`, `materials`, `deadline`, `deliverable`, `review_result`, `contract`, `payment_status` | 任务来源、人物交付与独立验收规则 |
| 内容接触 | `visible_catalog`, `read_emails`, `played_segments`, `game_state`, `material_ranges` | 实际接触行为与内容提供过程 |
| 记忆 | AS2 人物记忆及其版本、`source_event`, `retrieval_results` | 人物记忆流程 |

**来源**: `docs/history/agentsociety2-design.md:136-149`

### 资源与需求字段

| 量 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `time` | UTC 整数秒 | seconds | 场景时区，同秒事件有严格序号 |
| `money` | 整数 | 最小货币单位 (分) | 避免浮点累计误差 |
| `available_energy` | 浮点 | kcal | 实验中的可用能量状态 |
| `sleep_deficit` | 浮点 | hours | 与食品能量独立 |
| `entertainment_need` | 浮点 | 归一化 [0,1] | 随生活积累 |
| `work_need` | 复合对象 | - | `deadline`, `contract`, `task`, `economic_goal` |

**来源**: `docs/history/agentsociety2-design.md:236-245`

## LifeEnv 生命周期接口

| 接口 | 签名 | 行为 |
| --- | --- | --- |
| `__init__` | `(scenario_path: str | None = None)` | 仅保存配置路径和建立资源句柄 |
| `init` | `(start_datetime: datetime)` | 核验内容目录、规则、初始世界与时间；初始化数据库和事件计划 |
| `step` | `(tick: int, t: int)` | `tick` 为推进秒数，`t` 统一为结束时间；满足 `t = 上次已确认时刻 + tick` |
| `to_workspace` | `(workspace_path: str | None = None)` | 遵循 AS2 签名；持久化必要状态与检查点引用；错误必须传播 |
| `restore` | `(workspace_path: str)` | 读取明确指定的有效检查点，核对代码、规则、数据、时间和人物状态版本 |
| `close` | `()` | 关闭数据库等资源，保留运行终态与未完成活动 |

**来源**: `docs/history/agentsociety2-design.md:154-163`

## 模型工具接口

所有模型工具经 `submit_action(call)` 统一登记，宿主附加 `agent_id`, `decision_id`, `action_id`。

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `observe` | `context` (宿主只读 RPC) | 当前可见状态、已到达通知和已完成的内容接触结果 |
| `start_basic_activity` | `activity_type`, `planned_duration` | `status: "submitted"`, `action_id` |
| `start_targeted_activity` | `target_id`, `activity_type`, `planned_duration`, `playback_speed` | `status: "submitted"`, `action_id` |
| `start_travel` | `destination_id`, `travel_mode`, `planned_duration` | `status: "submitted"`, `action_id` |
| `set_activity_state` | `activity_id`, `operation` (`pause`/`resume`/`exit`) | `status: "submitted"` |
| `change_playback_speed` | `activity_id`, `speed` | `status: "submitted"` |
| `continue_activity` | `activity_id` | `status: "submitted"` (只安排后续反馈，不额外推进时间) |
| `purchase` | `items: List[{product_id, quantity, fulfillment, delivery_location}]` | `status: "submitted"`, `order_id` |
| `consume` | `items: List[{item_id, quantity, usage_type}]` (最多3件) | `status: "submitted"` |
| `submit_work` | `task_id`, `deliverable_ref` | `status: "submitted"` |
| `game_action` | `game_instance_id`, `action`, `params` | `status: "submitted"` |
| `plan_day` | `segments: List[{start_time, activity, duration}]` (5–8段) | `status: "submitted"` |
| `revise_day_plan` | `segments: List[...]` | `status: "submitted"` |
| `send_message` | `recipient_id`, `content` | `status: "submitted"` |
| `start_social_activity` | `target_id`, `activity_type`, `planned_duration` | `status: "submitted"` |

**管理接口** (不向模型暴露):
| 接口 | 参数 | 返回 |
| --- | --- | --- |
| `scheduling_state` | - | `decidable_agents: List[str]`, `next_event_time: int` |
| `commit_decision_round` | `round_id: str` | 按预定顺序结算请求 |
| `checkpoint` | `request` | 保存共同检查点 |

**来源**: `docs/history/agentsociety2-design.md:169-191`

### 请求字段与结算检查

| 请求 | 主要业务字段 | 结算时的关键检查 |
| --- | --- | --- |
| 启动活动 | `activity_type`, `target` / `task`, `planned_duration`; 出行另有 `destination`, `travel_mode` | 内容已可访问、位置合法、没有冲突占用；持续时间正数 |
| 修改活动 | `activity_id`, `operation` (`pause`/`resume`/`exit`/`change_speed`), `speed` (适用时) | 活动属于调用者，转换合法；倍速在已声明范围内 |
| 购买 | `product_id`, `quantity`, `fulfillment`, `delivery_location` | 价格版本、资金、库存、营业和配送范围 |
| 使用 | `item_id`, `quantity`, `usage_type` | 物品已取得、数量足够、用途符合物品定义 |
| 提交工作 | `task_id`, `deliverable_ref` | 任务归属、进度、截止规则、素材存在及未重复结算 |
| 游戏动作 | `game_instance_id`, `action`, `params` | 实例归属、当前局面和游戏进度 |

**来源**: `docs/history/agentsociety2-design.md:204-213`

### 动作状态枚举

```python
ActionStatus = Literal[
    "submitted",      # 已提交待结算
    "accepted",       # 已接受
    "in_progress",    # 进行中
    "completed",      # 完成
    "rejected",       # 拒绝
    "cancelled",      # 取消
    "failed",         # 失败
    "unknown"         # 结果未知
]
```

**来源**: `docs/history/agentsociety2-design.md:220`

## 数字足迹事件结构

### 事件字段

| 字段组 | 必填字段 |
| --- | --- |
| 记录身份 | `schema_version`, `run_id`, `event_id`, `event_seq` (严格递增), `event_type` |
| 参与与关联 | `agent_id`; 适用时有 `decision_id`, `action_id`, `activity_id`; `caused_by` 连接直接相关事件 |
| 时间 | `occurred_at` (模拟时间), `observed_at` (观察事件), `recorded_at` (程序时间), `wall_elapsed_ms` |
| 观察 | 实际可见字段、`content_refs`, `material_ranges`; `observation_version`，不含未来或未授权内容 |
| 请求与执行 | `action_name`, `params`, `status`, `start_time`, `end_time`, `progress`, `interruption_reason` |
| 结果 | `feedback`, `resource_changes`, `deliverable`, `counterparty`, `review_result`, `failure_reason` |
| 状态关联 | `state_version_before`, `state_version_after` (可回到检查点和事件重建状态) |
| 来源与可见性 | `source_type`, `material_sha256`, `generation_version`, `visible_to_agent`, `evaluation_only` |

**来源**: `docs/history/agentsociety2-design.md:490-501`

### 事件类型枚举

```python
EventType = Literal[
    "observation",           # 观察交付
    "decision_started",      # 人物决策
    "model_request",         # 模型请求
    "model_response",        # 模型响应
    "action_requested",      # 动作请求
    "action_accepted",       # 动作接受
    "action_rejected",       # 动作拒绝
    "activity_progress",     # 活动进度
    "action_result",         # 动作结果
    "environment_event",     # 环境自主事件
    "memory_updated",        # 记忆变化
    "checkpoint_created",    # 检查点
    "run_ended",             # 运行结束
    "run_failed"             # 运行异常
]
```

**来源**: `docs/history/agentsociety2-design.md:503-504, line 864-866`

### 持久化结构

```
run/
  manifest.json                 # 运行身份与代码、模型、提示、规则、数据版本
  footprints.sqlite             # 事件、当前结算状态、素材索引与检查点
  assets/<sha256>               # 实际内容、模型证据、人物状态快照与交付物
  as2/                          # AS2 人物工作区、trace 与 replay
  exports/<export-id>/          # 固定输入、来源索引和导出清单
  labelkit/<labelkit-run-id>/   # 配置、输出、报告及拒收记录
```

**数据表**:
| 表名 | 责任 |
| --- | --- |
| `events` | 不覆盖的事件序列；可按人物、活动、动作和时间查询 |
| `world_state` | 当前已确认世界、人物资源、活动与待发生事件；带状态版本 |
| `artifacts` | 哈希、文件位置、大小、模态、来源和使用条件 |
| `checkpoints` | 完整世界快照与 AS2 人物快照引用、时钟、随机状态和最后事件序号 |

**来源**: `docs/history/agentsociety2-design.md:510-527`

## 存储接口

| 调用 | 签名 | 合同 |
| --- | --- | --- |
| `AssetStore.put` | `(content: bytes, metadata: dict) -> AssetRef` | 内容先原子落盘并核验，返回不可变引用 |
| `AssetStore.read` | `(reference: AssetRef) -> bytes` | 验证引用、大小与 SHA-256；缺失或损坏立即失败 |
| `FootprintStore.commit` | `(state: WorldState, events: List[Event]) -> None` | 一次事务更新状态、资金与事件 |
| `FootprintStore.read_events` | `(cutoff: int) -> List[Event]` | 按确定事件序号读取固定截止范围 |
| `FootprintStore.checkpoint` | `(state: WorldState, snapshots: List[AssetRef]) -> CheckpointId` | 先校验全部素材，再提交共同检查点 |
| `FootprintStore.restore` | `(checkpoint: CheckpointId, destination: str, branch_metadata: dict | None) -> WorldState` | 校验版本与全部素材后，在新目录恢复 |

**来源**: `docs/history/agentsociety2-design.md:874-881`

## 语料内容目录字段

| 字段 | 含义 |
| --- | --- |
| `item_id`, `kind` | 稳定内容标识与食品、节目、游戏、工作、邮件、商品、路线等类别 |
| `source` | 数据集、版本、原始记录标识与文件哈希 |
| `language`, `region`, `valid_time` | 语言、适用地区和数据时间 |
| `assets` | 原始文字、图像、音视频、任务附件等文件引用及哈希 |
| `segments` | 内容区间、媒体起止位置、顺序与是否具有可靠时间依据 |
| `attributes` | 营养、价格、类别、技能要求等已核对属性及单位 |
| `access` | 人物、地点、时间、持有权和前置事件等访问条件 |
| `evaluation_refs` | 参考答案或评价依据，仅验收端可用 |
| `usage_rights` | 可用于内部处理、再分发和训练的条件及核对结果 |

**来源**: `docs/history/agentsociety2-design.md:415-427`

## LabelKit 输入输出格式

### 输入示例结构

```json
{
  "sample_id": "string",
  "run_id": "string",
  "agent_id": "string",
  "source_event_ids": ["string"],
  "task": "action_sequence" | "intent" | "dreaming" | "rule_test",
  "evidence": {
    "window_start": "ISO8601",
    "window_end": "ISO8601",
    "boundary": "activity_completed" | "interrupted" | "time_cutoff",
    "events": [
      {
        "event_id": "string",
        "time": "ISO8601",
        "type": "EventType",
        "content": "string"
      }
    ]
  }
}
```

**来源**: `docs/history/agentsociety2-design.md:612-640`

### 交付格式

| 产物 | 最小字段 |
| --- | --- |
| 动作训练记录 | `task`, `input` (允许看到的历史), `target` (下一动作及参数), `provenance` (来源引用), `terminal` / `truncated` |
| 意图训练记录 | `input` (允许看到的足迹), `target` (意图标签), `evidence_events`, `annotation_source`, `ambiguity_state` |
| dreaming 训练记录 | `input` (截止时刻的经历), `target` (归纳或反思目标), `hypothesis`, `branch_source` |
| 规则测试夹具 | `initial_state`, `events` (有序输入), `expected` (预期命中或不命中), `rule_version`, `trigger_time`, `state_assertions`, `provenance` |
| 交付清单 | `input_hash`, `output_hash`, `schema_version`, `rule_version`, `model_version`, `count`, `rejection_reasons`, `source_groups`, `loader_result` |

**来源**: `docs/history/agentsociety2-design.md:684-705`

## CLI 命令接口

| 命令 | 参数 | 行为 |
| --- | --- | --- |
| `prepare-corpora` | - | 获取明确来源子集、核对文件、形成规范化目录与来源清单 |
| `validate` | `scenario_path` | 校验场景、语料、依赖、规则和目标数据合同，不启动人物 |
| `run` | `scenario_path`, `output_dir` | 从明确场景新建运行目录，经 AS2 执行到场景终点 |
| `resume` | `parent_checkpoint`, `output_dir` | 指定父运行和完整检查点，在新的运行目录恢复 |
| `replay` | `parent_checkpoint`, `intervention`, `output_dir` | 指定父运行、共同检查点及一次余额条件干预，在新反事实分支实际执行 |
| `status` | `run_dir` | 只读显示已确认模拟时间、阶段、人物活动与调用统计 |
| `export` | `run_dir`, `cutoff_event`, `output_dir` | 固定截止事件和活动边界，形成完整证据 JSONL、来源索引与哈希 |
| `deliver` | `export_dir`, `labelkit_project`, `output_dir` | 执行 LabelKit、独立校验、训练加载与规则执行，最后发布成功清单 |
| `evaluate` | `baseline_dir`, `experiment_dir`, `output_dir` | 核对两套数据身份与可比范围，产出机器报告、图表和中文结论 |

**来源**: `docs/history/agentsociety2-design.md:846-858`

### 运行状态枚举

```python
RunStatus = Literal[
    "created",      # 已创建
    "running",      # 运行中
    "completed",    # 完成
    "failed",       # 失败
    "interrupted"   # 中断
]

RunPhase = Literal[
    "deciding",     # 决策中
    "resolving",    # 结算中
    "advancing",    # 推进中
    "checkpointing",# 检查点中
    "stopped"       # 已停止
]
```

**来源**: `docs/history/agentsociety2-design.md:861-862`

## LifeAgent 人格字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `background_story` | str | 人物背景故事 |
| `PersonaCard` | 编译产物 | 身份、关系、当下处境、时间锚点、时型、习惯签名、口味与应对方式 |
| 卡片长度 | - | 不超过 600 汉字的中文文本进入实际模型请求 |
| 删除字段 | - | `occupation`, `routine_preference`, `consumption_preference`, `risk_preference` 模板字段与随机内容偏好浮点删除 |

**来源**: `docs/history/agentsociety2-design.md:571-576`

### 记忆字段

- 每条记忆必须有 `source_event` (来源事件) 和 `formation_time` (形成时刻)
- 保存每次决策实际检索到的记忆，不仅保存最后一份 `MEMORY.md`

**来源**: `docs/history/agentsociety2-design.md:580-583`
