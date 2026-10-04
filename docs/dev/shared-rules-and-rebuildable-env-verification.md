# 共享规则与可重建环境验收记录

> 规格：[最终开发规格](SPEC-shared-rules-and-rebuildable-env.md)。
> 实施基线：`2cf230115f4084aef2376e9d77bc976efbed43ea`；分支 `codex/shared-rules-rebuildable-env`。
> 本记录只按实际命令和当前源码登记证据，不继承其它特性的通过数字。

## 方案裁决与文档一致性

| 主题 | 最终裁决 | 保留的现行合同 |
| --- | --- | --- |
| 规则共享 | 原始定义保留在所属模块，消费者导入同一实现，删除复制定义和旧导入名 | 工作纯字符串原文字节、场景输入格式、已有拒绝码 |
| 活动家族 | 统一声明与读取；缺品类保持未知 | 计划不猜进度、评估缺证、生产受理拒绝 |
| 时间与数值 | 时长上限一致，区间端点严格整数，曲线有限非负 | 一般活动 86400 秒、消费 60–14400 秒 |
| 模型纠正 | 共用解析与 Schema 错误格式 | 各模型通道的回显、指令与重试预算 |
| 记忆与 AS2 | WorldMock 维护同类记忆合并与观察单写业务，AS2 保留必要生命周期补丁 | 主观类型、来源字段、共同恢复、反思预算、故障传播 |
| 环境 | 固定公开源制品与有序补丁，独立 LabelKit 环境，标准库引导 | 实际生产字节身份，无 SSH、无隐式 uv、无旧来源分支 |
| 放行 | 不允许测试跳过取得通过，双平台和 Linux 语料证据合并 | 85% 分支覆盖率、零未覆盖公共接口、真实总体实验独立 |

全部设计章节、合同、安装手册、关联规格和验收门禁同步清单以最终规格为准。
历史版本表保留原始提交及当时结论；现行正文的安装来源、公共导入和职责按本特性更新。

AS2 完整历史补丁已重建出原摘要 `65a902ec66df42296d4e4c85e424bce3512e437133fc6be9fa597766fc30a334`；
业务移出后的最终摘要为 `bad0fe937ec4cbeaa8263674d8d7226f00109ef29a9ac86e4ce3af868b7c2358`，由 SERIES 和运行时直接核对。

## 实现与测试证据

机器登记位于 `tests/shared_rules_acceptance.py`，由 `tests/test_shared_rules_spec.py` 对照冻结规格逐行核对，
包括具体节点存在、无跳过标记、环境章节齐备和最终 AS2 身份一致。
最新共享活动、叶子规则、模型客户端、事件解析与规格登记的组合回归为 111 passed；
AS2、环境、共享活动和 Linux 边界测试的组合为 239 passed。
这些组合有重复节点，不能相加作为全量测试数。

| 规则组 | 实际入口与实现 | 具体行为测试 |
| --- | --- | --- |
| 摘要与安全文件 | `footprints.models`、`corpora.manifest`；请求、验收、品类、地图、内容和电话配置消费者 | `test_shared_primitives.py` 的字节同源、非有限拒绝、路径顺序和各原消费者符号链接拒绝 |
| 写入与错误 | `footprints.assets.atomic_write`、`errors.reject_value`、所属错误构造 | 发布顺序、失败清理、旧文件保持、单条日志及模型错误单条日志 |
| 时长、工作与出行 | `life.durations`、`work_start_refusal`、`planned_travel` | `test_shared_rules.py` 的工具层、按名清单与受理交叉测试，工作三入口、出行三入口 |
| 缓解与可供性 | `life.activities`、注册表 `met_lookup_key` | 非法接触次数两路径同时拒绝，合法速率相同、MET 同源 |
| 家族与计划 | `life.schedule.activity_family`、共享整数区间 | 缺品类不推进不加分、社交家族推进目标、非法区间不推进 |
| 词表、邮件与合同 | 唯一活动名称表、`mail_headers`、`CONTRACT_OBJECT_IDS` | 名称派生、真实揭示邮件与观察字段、换合同模板后的真实加载及资源倍率 |
| 候选与睡眠边界 | `CANDIDATE_ACTIVITY_KINDS` 与现有身体睡眠判定 | 五种候选的实际资源排除；睡眠恢复与无娱乐分录 |
| 区间、日期与事件 | `footprints.intervals`、`timeline_labels`、`evaluation.adapters` | 四类报告非法端点拒绝、主体过滤、23/25 小时本地日、适配只解析一次供两个消费者 |
| 主观记忆 | `life.memory_evidence`、WorldMock 记忆扩展 | 独立反思合法、共同快照、来源校验、24 小时与 Jaccard 精确边界 |
| 公共模型与客户端 | `base_models`、`IndependentModelClient`、`model_feedback` | 曲线布尔/非有限/负值拒绝、浅深冻结区分、三客户端身份与序列化、三通道原预算及纠正回显 |
| 防漂移 | `tests/test_single_source.py` | 真实全树无业务克隆；临时复制函数、规则表、错误归属实际失败 |
| 固定环境 | `bootstrap.py`、`bootstrap_sources.py`、`bootstrap_environment.py` | 旧与新真实源码重建、制品破坏拒绝、锁逐哈希比对、明确 Python、独立环境和 profile |
| 重建与门禁 | `fresh_check.py`、`coverage_gate.py`、CI | 无 SSH/缓存命令、平台摘要比较、分片恰一次、空/跳过/失败拒绝、真实跨卷字节复制 |

## 文件修改清单

| 文档组 | 实际修改文件 | 修订主题 |
| --- | --- | --- |
| 最终规格与验收 | 本规格、原提案、`shared-rules-and-rebuildable-env-verification.md`、`INDEX.md`、`implementation-status.md`、`VERIFICATION.md`、`coverage-baseline.md` | 唯一权威、完整范围、证据登记与平台放行 |
| 全部设计章节 | `docs/design/01-architecture.md` 至 `09-io-formats.md`、README、SUMMARY 与 `worldmock-design.html` | 唯一规则归宿、领域语义、生成源码来源及重新汇编 |
| 关联冻结规格 | AS2、时间与能量、一日运行保真、当日目标、生活真实性、生活运行时、品类合同、LabelKit 交付 | 删除现行旧来源/旧函数/错误职责，保留有意差异与旧实验边界 |
| 合同和手册 | `CONTRACTS.md`、`ARCHITECTURE.md`、`EXPERIMENT.md`、根 README、manual/runtime、resources、rules、evaluation | 引导参数与收据、数值/路径合同、安装、睡眠与评估边界 |

生产修改包含共享规则的所属模块及原消费者、WorldMock 记忆扩展、环境引导与覆盖率门禁；
新增补丁与哈希锁（LabelKit 应用锁和构建锁独立，避免 packaging 版本冲突），删除两个 gitlink 和 `.gitmodules`。
测试新增跨消费者行为、环境与 AS2 根测试、规格台账及防漂移扫描，旧 monkeypatch 和夹具指向当前真实入口。

## 工程与平台证据

实施前的定向基线为 60 passed；共享活动相关基线为 287 passed。
实施前全仓运行在启动并发修改前中止，已完成 1338 passed、1 failed，不能称为全绿。
该失败来自地图准备测试桩未声明现行 random_seed 参数，测试桩已改为真实接口。

## 独立源码变异审查

按 `uncle-bob-review`，只在干净提交的隔离 worktree 上执行生产源码语义变异。
记录每个变异的规格条款、源码改动、目标测试与 killed 或 survived 结果；存活变异必须补测后原样复验。

## 业界依据

哈希依赖安装按 [pip 的安全安装流程](https://pip.pypa.io/en/stable/topics/secure-installs/)，
锁导出按 [uv 官方命令](https://docs.astral.sh/uv/reference/cli/)。
固定换行按 [Git attributes](https://git-scm.com/docs/gitattributes)，源码重建使用
[Git apply](https://git-scm.com/docs/git-apply)，测试分片数据由
[coverage combine](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html) 合并。
平台声明以 [TextWorld 上游](https://github.com/microsoft/TextWorld) 的实际安装边界和锁中制品为证据。

## 版本表

| 版本 | 日期 | 修改 |
| --- | --- | --- |
| 共享规则与可重建环境 | 2026-10-05 | 记录并发方案裁决、完整实现和独立验收 |
