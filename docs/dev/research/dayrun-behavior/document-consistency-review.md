> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-behavior/document-consistency-review.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 一日行为文档双向一致性审查

> 审查对象：当前未提交工作树的 `SPEC-dayrun-behavior.md`、`CONTRACTS.md`、本次修改的设计、旧规格、手册与文档入口。
> 本页保存首次诊断，不替代冻结规格或固定源码验收收据；原问题的最终关闭见文末。

## 必须同步的矛盾与遗漏

| 文件与位置 | 观察到的冲突或遗漏 | 必须改动的句子或内容 |
| --- | --- | --- |
| `docs/design/08-configuration.md:135` | 世界状态版本表仍写 `4`；`src/worldmock/life/models.py:21,438`、`docs/design/03-resource-engine.md:315`、`docs/design/09-io-formats.md:75` 已是 `5`。当前新状态含活动集合派生、`body_settled_at`、威胁签名。 | 将该行改为“世界状态 `schema_version`｜5｜旧检查点拒绝；活动唯一事实源、共同身体结算时刻、持续威胁交付签名，以及供给状态随检查点保存”。不要新增旧状态读取或迁移。 |
| `docs/VERIFICATION.md:471` | “使用当前世界状态 `schema_version=4`”与当前合同和实现冲突。 | 将 `4` 改为 `5`，并把“引擎与状态”阶段说明补上同人活动集合、共同身体区间与恢复；旧格式仍直接拒绝。此处是项目级门禁文档，不可只改设计版本表。 |
| `docs/dev/implementation-status.md:205-209` | 标题和正文两次断言“当前版本 4”，虽页首提示旧测试数字是历史快照，此小节明确自称“当前世界格式”，易误导恢复操作者。 | 标题改为“当前世界格式：`WorldState.schema_version` 为 5”；正文区分“地图供给当时升至 4”与“本次活动集合及共同身体时刻升至 5”，恢复段改为“必须使用版本 5”；历史验收收据保留原身份，不冒称已在版本 5 复验。 |
| `docs/dev/ANALYSIS-spec-coverage.md:589,678` | 本页是现行追溯矩阵，仍以旧测试名和版本 4 标注 `covered`，并称“当前”为 4。 | 将两处当前版本表述改为 5；替换失效的 `test_world_state_schema_version_four_rejects_old_checkpoints` 为实际版本 5 回归及活动集合恢复测试路径，并只在固定源码门禁实跑后标 `covered`。旧版本 4 的地图阶段证据可单列历史快照。 |
| `docs/design/05-recording-engine.md:273-278` | 文字称记忆更新记录“三个文件哈希”，而实际 `MemoryUpdateEvidence` 在 `vendor/agentsociety2/.../react.py:96-124` 与 `src/worldmock/life/memory_evidence.py:37-44` 含 `memory_before_sha256`、`memory_sha256`、`episodes_sha256`、`state_sha256` 四个哈希字段；现行记录设计未列封闭证据形状与两种操作名。 | 将“三个文件哈希”改为“写前和写后 MEMORY.md、episodes.jsonl、memory/state.json 的四个 SHA-256 字段”。在“已核验经历与主观记忆”后明确 `MemoryUpdateEvidence` 的 `operation=append_episodes/refresh_summary`、新增与合并身份、对应主观快照、`consolidated`、`observation_event_id`、`verified_event_ids` 和四哈希；说明追加须有身份、仅刷新摘要须有视图变化，写盘失败不记成功事件。`CONTRACTS.md` 的“记忆字段”应链接到这段封闭事件格式。 |
| `docs/design/04-simulation-engine.md:409-410` 与 `docs/design/05-recording-engine.md:248-249` | 两处仍称实际食品利用效率是“尚未形成独立持久化字段”的缺口；当前 `src/worldmock/life/engine.py:1685-1698,1904-1913` 已在食用进度和共同身体结算证据输出 `food_efficiency`，`BodySettlement` 也有该字段。 | 删除“尚未形成…缺口／需把…随事件提交”的现时判断。改为“食用进度保留该次实际食品利用效率；共同身体结算保留区间效率字段，缺失测量保持未知，不由记录引擎补算”。若联合测试推翻当前代码证据，再按实测修正，不能保留已过时的缺口表述。 |
| `docs/INDEX.md:57-58,262-278` | 文档地图状态栏使用 `FROZEN，实施中` 与 `实施中`，但同页规定状态栏“只用下表取值”，表中只有 `FROZEN` 与 `实施台账` 等单独词。 | 规格行状态改为 `FROZEN`，台账行改为 `实施台账`；实施进度保留在规格抬头和台账正文。增加版本表记录该两行入口与状态约定同步。 |
| `docs/VERIFICATION.md:355-370` | 现行离线验收矩阵已有菜单和门店门禁，但没有本次一日行为的项目级行；新规格要求多个完整自然日、双人物、不同种子、非法模型输出扰动、真实 LifeAgent→SQLite→导出、覆盖率、身份与 Uncle Bob 审查。仅有 `docs/dev/research/dayrun-behavior/verification.md` 的实施中台账，不能取代项目放行门禁。 | 在现行矩阵加入“一日行为可信性”行，直接引用冻结规格“实现顺序与完整验收”，列出上述可执行观察与证据台账，初始状态为“待本次固定源码完整验收”；固定源码、命令和结果齐备后才更新。 |
| `docs/dev/PROPOSAL-dayrun-behavior.md:117-143` | 标题已是“提案基线的实现差距”，但正文仍称“当前同一人物只有一个主活动”“当前核验来源身份”“当前已有静默续行”，在已裁决记录中易被误读成今日现状。 | 在该节第一句加“以下‘当前’均指本提案调研时的基线，不描述冻结规格实施后的工作树”；更稳妥的是将这些句子的“当前”逐处改成“提案基线中”。保留原判断作为历史证据，不重写为当前实现结论。 |

## 应随最终验收收口的入口与历史记录

- `docs/INDEX.md:1-3` 日期仍是 2026-09-18，但本页已登记 2026-09-21 的新规格和台账；刷新页眉日期，并在版本表登记新增入口与状态修正。页首“确定性代码与测试层已就绪”应明确是此前项目基线，本次一日行为仍以台账为准，避免提前声称全部新增门禁已过。
- `docs/dev/implementation-status.md` 页首承认旧测试数字是历史快照，但正文尚无本次特性的独立现状入口。完成固定源码测试后新增一段链接 `research/dayrun-behavior/verification.md`，只登记实际门禁、完整自然日、独立突变与尚未满足的外部验收；不要把旧版本 4 的地图验收数字搬成版本 5 成绩。
- `docs/CONTRACTS.md:1269` 的七键 `active_activity` 是带日期的历史变更行，本身可保留；在该行末尾标注“此历史合同已由上文八键 `active_activities` 集合及世界版本 5 取代”，避免与当前 `CONTRACTS.md:537-565` 混读。
- `docs/dev/SPEC-life-realism.md:480-482` 的记忆写入／巩固仍标“本特性待验证”。这是诚实的过渡状态；只有 AS2 持久化、反思、恢复与完整人物链验收实跑后，才按实际证据更新。`docs/dev/research/dayrun-behavior/verification.md` 目前全部条款处于实施中，亦须由最终固定源码结果逐项刷新。
- `docs/dev/ANALYSIS-spec-coverage.md:215,284` 旧测试映射仍指“同一时刻不能工作／进食”“释放槽位”；本次规格允许资源相容的并行，并已删除单槽。应检查这些用例究竟在证明单动作边界还是旧全局互斥，修正条款说明与真实测试名，不可原样保留 `covered` 充当新组合证明。

## 已对齐的主链及审查边界

本次逐项读取了修改中的 `docs/design/01-architecture.md`、`02-exchange-engine.md`、`03-resource-engine.md`、`04-simulation-engine.md`、`05-recording-engine.md`、`08-configuration.md`、`09-io-formats.md`，以及 `SPEC-dayrun-behavior.md`、`CONTRACTS.md`、`SPEC-life-runtime.md`、`SPEC-life-realism.md`、`SPEC-day-plan-goals.md`、`SPEC-interaction-menu.md`、`SPEC-map-and-storefront.md`、`docs/manual/resources.md`、`docs/manual/scenarios.md`、`docs/INDEX.md`、`docs/VERIFICATION.md` 和现行状态／追溯页。资源声明交集、ACTIVE 集合与八键控制行、每人每区间唯一 `body_settlement`、NASA 官方段落边界、三家正式餐馆供给与通用空菜单在新规格、合同及对应模块设计之间相容；上表列的是仍需处理的实质差异。

本审查只核对当前可见文档与少量真实模型字段／事件入口；并发代码仍在变动。它不宣称完整自然日、全仓门禁或突变审查通过。待生产与台账固定后，应执行文档链接门禁，再对新增字段、事件和手册命令作最终双向复读。


## 最终复核

首次清单中的版本、事件载荷、记忆证据、入口状态和旧测试名已同步修正。
后续独立审查又校正了现行 CONTRACTS 与记录设计中的两处版本、追溯表的公共身体字段归属，
以及评估表对活动分钟和人物时间并集的口径。逐项复读结论见
[最终独立文档复核](bob-experiences/final-document-review.md)。
最终测试数字及特性状态统一从[实施台账](verification.md)读取，旧收据不改写成当前结果。
