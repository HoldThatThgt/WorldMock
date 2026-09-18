**Status: SUPERSEDED** — superseded_by: docs/INDEX.md

# WorldMock 设计文档组织方案

## LabelKit 文档结构分析

基于对 LabelKit 项目的观察，其文档组织遵循以下原则：

### LabelKit 的三层文档结构

```
docs/
├── CONTRACTS.md          # 跨模块接口合同（FROZEN 状态）
├── design/               # 设计成品（PDF/HTML）
├── dev/                  # 开发规格与提案
│   ├── SPEC-*.md        # 定稿的特性规格
│   ├── PROPOSAL-*.md    # 待裁决的提案
│   ├── ANALYSIS-*.md    # 分析文档
│   ├── BOB-*.md         # Uncle Bob 验证报告
│   └── research/        # 调研证据
└── manual/              # 用户手册
    ├── 01-what-is.md
    ├── 02-install.md
    └── ...
```

### 关键发现

**1. CONTRACTS.md 是唯一权威接口文档**
- 状态标记为 FROZEN
- 定义所有模块间的接口：名称、签名、类型、默认值、文件格式
- 其他 SPEC 文档负责算法和行为，CONTRACTS 负责具体实现合同
- 任何偏离都需要先修改这个文件

**2. SPEC 与 PROPOSAL 的区分**
- `SPEC-*.md`：已裁决、定稿的特性规格，是实施合同
- `PROPOSAL-*.md`：待裁决的方案，包含多个选项和权衡
- PROPOSAL 经裁决后变成 SPEC

**3. 裁决点的明确登记**
- 每个 SPEC 开头有裁决点登记表
- 标明状态、裁决者、裁决日期、裁决内容
- 每个裁决点有编号（A1、A2...）、名称、前因后果

**4. 设计文档的版本演进**
- 不维护多个版本文件（没有 v1、v2、v3）
- 通过状态标记和修订注记记录演进
- 设计成品在 `design/` 目录编译为 PDF/HTML

**5. 证据驱动的调研方式**
- 每个设计决策都引用调研证据
- 调研结果以"调查·XX"形式内联引用
- 外部依据直接引用文献和 URL

## WorldMock 当前问题分析

### 现有文档的问题

**1. 单体巨型文档**（`agentsociety2-design.md` 1769行）
- 混合了多个关注点：架构决策、接口定义、实验绑定、验收标准、人物真实性修订
- 读者难以快速定位自己关心的部分
- 修订时影响面不清晰

**2. 缺少职责分离**
- 接口合同（LifeEnv 工具、事件结构）混在架构讨论中
- 实验参数（模型版本、场景配置）混在设计决策中
- 验收标准散落各处

**3. 修订历史混乱**
- 在同一文档中标注"修订合同"
- 新旧条款冲突需要读者自己判断
- 没有清晰的"哪些是活文档、哪些已冻结"的状态

**4. 表格隐式引用问题**
- 大量长表格（验证矩阵、文件职责、改动清单）
- 后续引用时需要跳转回查
- 违反"禁止编号指代"原则的精神

## WorldMock 设计文档组织方案

### 建议的文档结构

```
docs/
├── ARCHITECTURE.md           # 架构决策记录（WHY）
├── CONTRACTS.md              # 接口合同（WHAT & HOW）
├── EXPERIMENT.md             # 实验绑定与配置
├── VERIFICATION.md           # 验收标准与测试策略
├── specs/                    # 特性规格
│   ├── life-realism-v2.md   # 人物真实性修订合同
│   ├── as2-integration.md   # AS2 集成规格
│   ├── labelkit-delivery.md # LabelKit 数据交付规格
│   └── footprints.md        # 数字足迹规格
├── research/                 # 调研与分析（已有）
│   ├── as2-feasibility.md
│   ├── life-data-feasibility.md
│   ├── realism-evaluation.md
│   └── realism-root-cause-2026-09-17.md
└── history/                  # 废弃的实验记录
    └── mobile-prototype-lessons.md
```

### 各文档职责

#### ARCHITECTURE.md - 架构决策记录
**职责**：回答"为什么这样设计"
- 采用 AS2 作为基础的原因
- LifeEnv 与 LifeAgent 的职责划分
- 数字足迹为什么用 SQLite 事务
- 为什么不用手机操作模式
- 整体数据流（语料→环境→足迹→LabelKit）

**内容组织**：
```markdown
# WorldMock 架构决策记录

## 核心决策总览
[用 mermaid 图展示整体架构]

## 决策1：采用 AS2 作为基础框架
**决策内容**：使用 AgentSociety 2 作为人物、模型调用、记忆和运行管理基础
**替代方案**：自建框架 / Concordia / LangGraph
**选择理由**：[详细说明]
**权衡**：[说明放弃了什么]

## 决策2：LifeEnv 作为唯一环境
...
```

#### CONTRACTS.md - 接口合同
**职责**：定义所有模块的接口（名称、签名、类型、行为）
- LifeEnv 的生命周期接口
- 所有人物工具的 Pydantic 模型定义
- 事件结构的完整 Schema
- FootprintStore / AssetStore 的存储接口
- 导出格式的 JSONL Schema

**状态标记**：FROZEN（实施期间）或 DRAFT（设计期间）

**内容组织**：
```markdown
# WorldMock 接口合同

**状态：FROZEN** (2026-09-18)
本文档定义所有模块间接口。实施期间任何偏离都需要先修改本文档。

## 1. LifeEnv 环境接口

### 1.1 生命周期接口

#### `__init__(scenario_path: str | None = None)`
**职责**：初始化环境实例，不创建世界
**参数**：
- `scenario_path`：场景文件路径，None 时仅用于注册发现
**返回**：无
**副作用**：保存配置路径，建立资源句柄
**错误**：无（延迟到 init 时检查）

#### `init(start_datetime: datetime)`
[详细定义]

### 1.2 人物工具接口

每个工具的完整 Pydantic 定义、字段约束、结算检查...

## 2. 事件结构合同

[完整的 Event Schema]

## 3. 存储接口

[FootprintStore 和 AssetStore 的所有方法]
```

#### EXPERIMENT.md - 实验绑定
**职责**：定义正式运行的全部固定参数
- 依赖版本（AS2、LiteLLM、Ray）
- 模型配置（端点、版本、参数）
- 场景参数（日期、人物、地点、语料）
- 身体参数（能量、睡眠、效率曲线）
- 命令行接口

**内容组织**：
```markdown
# WorldMock 实验绑定

## 依赖版本锁定
- AS2 基线：670c94fff7c64c4f79b632125f2ccf968155e746
- LiteLLM：1.89.2
- Ray：2.55.1

## 模型配置
**决策模型**：
- 端点：https://api.deepseek.com/beta
- 模型：deepseek-flash
- 独白阶段：thinking=disabled, temperature=1.3
- 行动阶段：thinking=enabled, tool_choice=auto

## 正式场景绑定
- 日期范围：2026-01-05 00:00:00 至 2026-03-30 00:00:00（84天）
- 时区：Asia/Shanghai
- 人物背景：上海张江白领
...

## 身体参数
[所有数值参数的定义]

## 命令行接口
[所有命令的签名和行为]
```

#### VERIFICATION.md - 验收标准
**职责**：定义如何验证实现的正确性
- 每类验收事项的具体方法
- 测试覆盖策略
- Uncle Bob 审查范围
- 最终门禁清单

**内容组织**：
```markdown
# WorldMock 验收标准

## 验收事项总览
本项目定义以下验收事项，每项都独立成节，完整说明必须覆盖的场景、验证方法和通过标准。

## AS2 集成验收

### 验收目标
确保 LifeEnv 能够被 AS2 正确发现、加载和调用。

### 必须覆盖的测试场景
1. **环境注册**：LifeEnv 类能被 AS2 的环境扫描器发现
   - 测试方法：`tests/integration/test_as2_discovery.py`
   - 通过标准：AS2 环境列表中包含 LifeEnv

2. **正确实例化**：传入场景路径后能够成功初始化世界状态
   [详细展开]

### 验证工具
- `tests/integration/` 目录的集成测试
- AS2 上游的 `tests/test_direct_router.py`
- 24小时双人短窗口运行

### 通过标准
所有测试通过，短窗口运行产生完整数字足迹，无警告或错误。

## 时间一致性验收
[完整展开，不需要读者跳转回查表格]

...
```

#### specs/ 目录 - 特性规格
每个独立的特性或子系统一个文件，包含：
- 背景和动机
- 设计决策
- 接口定义（引用 CONTRACTS.md）
- 实施清单
- 验收标准（引用 VERIFICATION.md）

**示例：`specs/life-realism-v2.md`**
```markdown
# 人物真实性修订合同 v2

**状态**：定稿（2026-09-18）
**前身**：原 agentsociety2-design.md 的《人物真实性修订合同》章节
**依赖**：本规格修改了 CONTRACTS.md 的观察接口和工具 Schema

## 背景与动机
[根因分析的简要总结，引用 research/realism-root-cause-2026-09-17.md]

## 核心修订

### 决策节律
[完整内容]

### 身体语义
[完整内容]

...

## 实施清单
- [ ] 修改 `custom/agents/life_agent.py` 的观察构建
- [ ] 新增 `src/worldmock/life/sleep.py` 实现两过程模型
- [ ] 更新 CONTRACTS.md 的观察接口定义
[完整的文件和修改清单]

## 验收标准
参见 VERIFICATION.md 的"行为指标与协议门禁"章节。
```

### 重写 agentsociety2-design.md 的方案

**方案1：拆分为多个文档**（推荐）
```
现有内容分布：
- 设计结论、整体架构 → ARCHITECTURE.md
- AS2 改动清单、LifeEnv 接口、事件结构 → CONTRACTS.md
- 实验绑定、模型配置、场景参数 → EXPERIMENT.md
- 验证矩阵、门禁清单 → VERIFICATION.md
- 人物真实性修订合同 → specs/life-realism-v2.md
- LabelKit 接入 → specs/labelkit-delivery.md
- 时间推进、消费生产模型 → specs/time-and-resources.md
```

**方案2：保留单文档，改进组织**
- 如果必须保持单文档，按照本分析开头的改进指南重写
- 消除表格隐式引用，每个事项完整说明
- 但仍然存在"混合关注点"的问题

## 实施建议

### 第一阶段：建立文档骨架（1-2小时）
1. 创建 `docs/ARCHITECTURE.md`，迁移架构决策内容
2. 创建 `docs/CONTRACTS.md`，迁移接口定义
3. 创建 `docs/EXPERIMENT.md`，迁移实验绑定
4. 创建 `docs/VERIFICATION.md`，迁移验收标准

### 第二阶段：细化特性规格（2-3小时）
1. 创建 `docs/specs/` 目录
2. 将"人物真实性修订合同"迁移到独立文件
3. 将 AS2 集成、LabelKit 交付、时间推进等主题各自成文

### 第三阶段：交叉引用检查（1小时）
1. 确保各文档间的引用清晰
2. 更新 README.md 增加文档导航
3. 删除 `agentsociety2-design-v2.md` 和 `design-improvement-guide.md`

### 原 agentsociety2-design.md 的处理
**选项1**：移动到 `docs/history/agentsociety2-design-v1.md` 作为历史记录
**选项2**：直接删除（新文档已包含所有内容）
**选项3**：替换为索引文档，指向新的文档结构

## 关键原则总结

1. **一个关注点一个文件**：接口定义、实验配置、验收标准各自独立
2. **CONTRACTS 是唯一实施权威**：所有接口定义集中在此
3. **状态标记清晰**：FROZEN / DRAFT / DEPRECATED
4. **消除表格隐式引用**：每个事项完整说明，引用时重述关键信息
5. **证据驱动**：设计决策引用调研文档
6. **裁决点登记**：重要决策明确记录裁决者和日期
7. **没有版本后缀**：通过状态标记和修订注记管理演进

这样组织后，每个文档职责清晰，读者能快速找到需要的信息，修订时影响面明确。
