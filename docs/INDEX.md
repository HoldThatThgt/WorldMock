<!-- worldmock-doc -->
**Status: FROZEN** | **Owner: worldmock-core**

# WorldMock 文档索引与职责分配

本文件是 WorldMock 项目的根索引，定义文档体系结构、文件归属和迁移来源。

## 文档体系

| 文档 | 职责 | 状态 |
| --- | --- | --- |
| `INDEX.md` (本文件) | 文档索引、文件归属、迁移映射 | FROZEN |
| `ARCHITECTURE.md` | 设计理由、架构决策、WHY | FROZEN |
| `CONTRACTS.md` | 接口签名、字段定义、Schema、CLI | FROZEN |
| `EXPERIMENT.md` | 实验设置、依赖版本、场景绑定 | FROZEN |
| `VERIFICATION.md` | 测试矩阵、验收门禁、质量标准 | FROZEN |
| `dev/SPEC-*.md` | 各特性的详细规格与算法实现 | DRAFT/FROZEN |
| `history/*.md` | 历史设计文档、研究证据 | ARCHIVED |

## 来源映射 {#worldmock-migration}

```json
{
  "migration_source": "docs/history/agentsociety2-design.md",
  "migration_date": "2026-09-18",
  "responsibilities_mapped": [
    {
      "responsibility": "设计结论、目标、范围、整体数据流",
      "source_location": "docs/history/agentsociety2-design.md:11-96",
      "target": "docs/ARCHITECTURE.md",
      "rule": "保留 WHY、边界和总流程；接口字段转交 CONTRACTS"
    },
    {
      "responsibility": "AS2 选型、LifeEnv/LifeAgent 分层和改动理由",
      "source_location": "docs/history/agentsociety2-design.md:52-133",
      "target": "docs/ARCHITECTURE.md, docs/dev/SPEC-as2-integration.md",
      "rule": "WHY 进入 ARCHITECTURE；确定性行为和算法进入特性 SPEC"
    },
    {
      "responsibility": "LifeEnv 状态归属、生命周期、工具和动作结果",
      "source_location": "docs/history/agentsociety2-design.md:136-230",
      "target": "docs/CONTRACTS.md, docs/dev/SPEC-life-runtime.md",
      "rule": "类型、签名、枚举和结果字段只进 CONTRACTS；行为规则进 life-runtime"
    },
    {
      "responsibility": "资源、需求、活动结算、共享时钟、并发和加速",
      "source_location": "docs/history/agentsociety2-design.md:232-373",
      "target": "docs/dev/SPEC-life-runtime.md",
      "rule": "只保留已裁决算法；接口引用 CONTRACTS，不复制字段定义"
    },
    {
      "responsibility": "语料进入环境、内容目录、工作反馈、导航和配送",
      "source_location": "docs/history/agentsociety2-design.md:375-462",
      "target": "docs/ARCHITECTURE.md, docs/dev/SPEC-life-runtime.md",
      "rule": "数据流和选择理由进 ARCHITECTURE；准入和结算行为进 life-runtime"
    },
    {
      "responsibility": "数字足迹保存边界、事件结构、持久化、事务、检查点和恢复",
      "source_location": "docs/history/agentsociety2-design.md:463-565",
      "target": "docs/CONTRACTS.md, docs/dev/SPEC-footprints.md",
      "rule": "事件/文件/恢复字段只进 CONTRACTS；一致性和算法进 footprints"
    },
    {
      "responsibility": "LifeAgent 人格、记忆和 dreaming 边界",
      "source_location": "docs/history/agentsociety2-design.md:567-585",
      "target": "docs/CONTRACTS.md, docs/dev/SPEC-life-realism.md",
      "rule": "可见字段和窗口格式进 CONTRACTS；边界和决策逻辑进 life-realism"
    },
    {
      "responsibility": "LabelKit 输入范围、加工责任、来源关联、标签和交付格式",
      "source_location": "docs/history/agentsociety2-design.md:587-712",
      "target": "docs/CONTRACTS.md, docs/dev/SPEC-labelkit-delivery.md",
      "rule": "数据合同唯一落 CONTRACTS；加工流程和验收行为落 labelkit-delivery"
    },
    {
      "responsibility": "一条完整生活经历的数据流",
      "source_location": "docs/history/agentsociety2-design.md:714-732",
      "target": "docs/ARCHITECTURE.md",
      "rule": "只保留从人物决策到足迹、LabelKit 和交付的系统流"
    },
    {
      "responsibility": "实现组织和交付顺序",
      "source_location": "docs/history/agentsociety2-design.md:735-758",
      "target": "docs/INDEX.md, docs/VERIFICATION.md",
      "rule": "文件 owner 进 INDEX；可验证顺序和门禁进 VERIFICATION"
    },
    {
      "responsibility": "验证矩阵、测试入口和覆盖口径",
      "source_location": "docs/history/agentsociety2-design.md:760-790",
      "target": "docs/VERIFICATION.md",
      "rule": "转成机器可执行 TEST-MATRIX；不得只保留表格描述"
    },
    {
      "responsibility": "实验边界、依赖和外部参照",
      "source_location": "docs/history/agentsociety2-design.md:792-817",
      "target": "docs/EXPERIMENT.md",
      "rule": "实验身份与样本绑定 evidence manifest"
    },
    {
      "responsibility": "实验与依赖、运行数据接口、场景绑定",
      "source_location": "docs/history/agentsociety2-design.md:819-1017",
      "target": "docs/EXPERIMENT.md, docs/CONTRACTS.md",
      "rule": "实验设置进 EXPERIMENT；接口定义进 CONTRACTS"
    }
  ]
}
```

## 文件归属

### 核心系统

| 路径 | Owner | 职责 |
| --- | --- | --- |
| `custom/envs/life_env.py` | worldmock-env | AS2 环境适配层 |
| `custom/agents/life_agent.py` | worldmock-agent | AS2 人物适配层 |
| `src/worldmock/life/` | worldmock-life | 生活规则、身体、活动 |
| `src/worldmock/footprints/` | worldmock-footprints | 事件存储、检查点 |
| `src/worldmock/profile/` | worldmock-profile | 人物卡片编译 |
| `src/worldmock/corpora/` | worldmock-corpora | 语料整理 |
| `src/worldmock/delivery/` | worldmock-delivery | LabelKit 交付 |
| `src/worldmock/evaluation/` | worldmock-evaluation | 质量评估 |

### 测试

| 路径 | Owner | 职责 |
| --- | --- | --- |
| `tests/life/` | worldmock-life | 生活规则测试 |
| `tests/footprints/` | worldmock-footprints | 存储测试 |
| `tests/integration/` | worldmock-integration | 集成测试 |
| `tests/delivery/` | worldmock-delivery | 交付测试 |
| `tests/evaluation/` | worldmock-evaluation | 评估测试 |

### 场景与配置

| 路径 | Owner | 职责 |
| --- | --- | --- |
| `scenarios/*.json` | worldmock-scenario | 实验场景定义 |
| `corpora/` | worldmock-corpora | 固化语料素材 |
| `labelkit-projects/` | worldmock-delivery | LabelKit 项目配置 |

## 状态定义

- **FROZEN**: 已冻结，作为实现合同，不得随意修改
- **DRAFT**: 草稿阶段，待评审冻结
- **ARCHIVED**: 已归档的历史文档，仅作参考
- **SUPERSEDED**: 已被新文档取代

## 变更原则

1. FROZEN 文档的修改需要显式版本号和变更理由
2. 接口变更必须先修改 CONTRACTS.md
3. 新增特性需要创建对应 SPEC 文档并在此处登记
4. 历史文档移入 `history/` 目录并标记 SUPERSEDED
