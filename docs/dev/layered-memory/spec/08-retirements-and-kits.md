# 08 退役项、食材包句与环境包

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。

## 目标与依据

本章实现冻结提案 docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md（下称提案）中以下各处的最终状态：

- 第二章「压缩：模型写的回顾段落」末尾的「退役的东西」表，以及表后两段（episodes.jsonl、MEMORY.md、两道主观记忆门禁）中由人物一侧和环境一侧代码承担的部分。本章负责表中前两行（阈值巩固与睡醒巩固；睡醒反思、`due_reflection`、`life_reflection.py`、反思登记的写入侧）；第三行（8000 字单一窗口）的删除与取代它的 14,000 字预算、兜底属于 01，本章只删记忆层的反思读者 `_reflections` 与 06-agent.md「进度、待办与记忆」里窗口裁感想的句子。第四行（三个 `memory_*` 工具）属于 05，第五行（人设层看法）属于 07。
- 第二章「归日规则」第三点：「睡醒日减一」（simulation_engine/evidence/memory.py:31-34）与睡醒反思同期退役；质量检查按它重算反思来源的部分只用于重新认证历史运行。
- 第二章「家里做的饭叫得出名字」整节。
- 第三章「代码」表中 agents/memory/life_memory.py、agents/memory/life_memory_store.py、custom/agents/life_agent.py（反思与记忆调用部分）、custom/envs/life_env.py（删除 `due_reflection`）、simulation_engine/engine.py（删除 `due_reflection`）、runtime/framework/read_query.py、simulation_engine/evidence/model.py、simulation_engine/evidence/memory.py、observation/wording/settled_text.py、observation/layers/result_projection.py、resource_engine/catalog/models.py（`CatalogManifest.format_version`）、WorldMock-Dataset 包 0001–0005、LabelKit-DataSet 各行。
- 第三章「事件、版本与下游」表中 memory_updated、目录、LabelKit、检查点和恢复各行；「设计文档」表与「测试与门禁」中与上述各项对应的条目。
- 第一章「心智模型没提到、却决定设计走向的机制」中关于睡醒反思、阈值巩固和家里做饭不写菜名的事实，作为退役与新句式的依据。

适用的裁定：

| 裁定 | 本章落实的内容 |
| --- | --- |
| 第一批「压缩一律由模型写成段落」 | 人物一侧的阈值巩固、睡醒巩固与睡醒反思退役；往日整理由 04 的日、月、年回顾承担 |
| 第 6 条（选项 A） | 目录格式 5→6，`CatalogManifest.format_version` 为 `Literal[6]`；构建器 `FORMAT_VERSION = 6`，包 0001–0005 全部按格式 6 重建、各升 `environment_version`；只有包 0001 加覆盖；LabelKit 的 `CATALOG_FORMAT_VERSION` 在同一次发布里改成 6，格式 5 的旧导出从此不被它接收 |
| 第 13 条 | 日回顾无条件上线，取代睡醒反思 |

全局决定的落实：本章内容全部属于提案的 P1，按最终状态一次交付；LabelKit-DataSet 不在本仓修改，本章给出它要做的改动和通知原文；WorldMock-Dataset 的包和构建器按本章修改；「字」按 Python `len()` 计。

## 行为

### 人物一侧的记忆不再调用模型

**步尾。** `WorldMockMemoryRuntime.after_step` 依次做三件事，不调用模型：

1. 校验并保存这一步收尾写下的主观条目（`_validate_subjective_memories`、`_append_subjective`，规则与现行相同：九种主观类型、`epistemic_status` 为 `subjective`、来源为 `activity_conclusion`，存储执行近二十四小时同类型相似合并）；
2. 用 `render_memory_view(人物卡片文本, 本次观察冻结的事实窗口, store.read_subjective_summary())` 重写 `MEMORY.md`；
3. 返回 `MemoryStepOutcome(created, merged, 步前 MEMORY.md 的 SHA-256, False, 观察身份, 事实来源事件身份)`。

不存在按条数（`_PENDING_THRESHOLD = 10`）或重要度合计（`_IMPORTANCE_THRESHOLD = 3.0`）触发的巩固，也不存在睡醒时不看阈值的巩固。`consolidated` 恒为 `False`。主观摘要（AS2 存储 state.json 的 `subjective_summary`）在新运行里从不写入，保持初值空串，所以 `MEMORY.md` 的「人物自述（未经核验）」一段恒为一行 `> 暂无人物自述。`。`MEMORY.md` 的三段结构、4000 字符预算和「身份」「已核验经历」两段的写法不变。`sleep_boundary` 参数照旧由锁定的 AS2 传入，不读。

**没有睡醒反思。** LifeAgent 的一步是观察、决策、步尾三段：

- `_observe_environment` 只做环境存在性检查，取 `context = self.require_decision_context()`，然后 `return await self._env.observe(context)`；取观察之前不向环境询问任何事，也不调用模型。
- 当天第一次睡醒那一步与其他步骤完全相同。
- 环境一侧不再有「此刻该不该反思」的判定：Life 扩展通道（`LifeEnvActor`、`LifeEnvProxy`）、`LifeEnv`、`LifeEngine` 都没有 `due_reflection`。通道上的只读方法只剩 `read_query`（它回答哪些工具见 05）。

**记忆运行时的模型入口。** AS2 的 `PersonMemoryRuntime.__init__` 要求必填的 `dispatch_llm`。`WorldMockMemoryRuntime` 传入模块函数 `_refuse_model_call`：它以 `logging.error("WorldMock memory runtime does not call the model")` 记一条错误日志，再抛出 `RuntimeError("WorldMock memory runtime does not call the model")`。AS2 记忆运行时里唯一调用 `_dispatch_llm` 的通用巩固 `_consolidate_memory_md` 只从 AS2 的 `after_step` 进入，而这个方法被覆写，所以正常运行永远到不了这里；到了就是宿主编排错误。LifeAgent 不再覆写 AS2 的 `PersonAgent._dispatch_memory_llm`，也不再把它交给记忆运行时。

**memory_updated。** 操作（`append_episodes`、`refresh_summary`）、写入点闭集 `MEMORY_WRITE_POINTS = ("activity_conclusion", "reflection")`、事件 `data` 的键集与快照形状全部不变。环境只接受步尾登记，要求如下，任一条不成立先记英文错误日志 `Memory update evidence rejected: <原因>`，再抛 `ProtocolError("Memory update evidence is invalid")`：

| 键 | 要求 |
| --- | --- |
| `observation_event_id` | 非空字符串，是本决策的冻结观察（`_memory_evidence_data` 拒绝 `null`，原因 `Memory update payload is invalid`） |
| `consolidated` | 恰为 `false`（`_memory_evidence_data` 拒绝 `true`，原因同上） |
| `trigger` | 环境推导：`[]` 或 `["activity_conclusion"]`（`memory_write_points`，规则不变） |
| `reflection` | 环境写入 `null` |
| 其余 | 观察属于本决策、事实窗口与观察里的 `verified_experiences` 一致；快照里没有来源为 `reflection` 的条目（`Step memory update carries a reflection episode`） |

每一步至多一条 `memory_updated`。人物工作区的存储里不会出现来源为 `reflection` 的条目，宿主写出的快照恰为四个基本键 `id`、`type`、`text`、`epistemic_status`。

**8000 字窗口与反思读者。** 记忆层的反思读者（`_reflections`，memory_layer.py:306-316，连同它的错误 `IntegrityError("Bedtime reflection has no filing date")`）删除。8000 字单一窗口（`MEMORY_WINDOW_CHARS`、`_fit`，memory_layer.py:35-36、:319-336）的删除与取代它的新预算、兜底都在 01。

**保留的东西。**

| 保留项 | 规则 |
| --- | --- |
| episodes.jsonl | 照常写入；恢复时 `AgentMemoryStore.validate_existing` 照常核验（runner.py:422-428） |
| MEMORY.md | 渲染机制不变（`render_memory_view`）；`memory_md_identity_residue` 照常只检查示例身份残留（tools/analysis/behavior/metrics.py:616-631） |
| `memory_repetition_ratio` | 照常读 memory_updated 的快照（metrics.py:601-609），与 episodes.jsonl 无关 |
| state.json | AS2 存储的键集与计数照旧（`append_episodes` 照旧累加 `pending_episode_count`），工作区快照格式不变 |
| 历史运行的认证 | `valid_subjective_snapshot` 仍接受 `source=reflection` 的快照；质量报告的 `_reflection_sourced`、`_valid_update_binding`、`_check_write_points` 代码不变，只在重新认证本规格交付之前的代码生成的运行时遇到反思登记；新运行里反思登记为零 |
| 调用类 | 质量报告的 `CALL_CLASSES` 保留 `consolidation` 与 `reflection` 两类，判定规则不变（`_call_class`，quality.py:795-812）；新运行里两类的请求事件数和提供商响应数都是 0。新增的 `memory_digest` 类和质量版本 10 见 04 |
| `REFLECTION_TOOL` | state/model/reflection.py 保留，只供质量报告判定历史运行的调用类 |
| `reflection_validation_max_retries` | 字段保留，改由回顾使用，见 04 |
| 记忆类型 `reflection` | `SUBJECTIVE_TYPES` 与收尾记忆的 `type` 取值闭集（state/interaction/requests.py:456）不变；这是一种主观条目类型，不是睡醒反思 |

### 家里做的饭叫得出名字

**食品状态与食材包状态。** 两者都是冻结目录的派生量，由 `Catalog` 的两个方法给出：

- 食品状态 `Catalog.food_states()`：至少被一条 `completion_effects.food` 不为空的内容凭着（`consumes_state`）的回调状态。系列各集凭同一状态（resource_engine/registration/admission.py:251 已核），只要有一集写了 food，这个状态就是食品状态。食品状态只由 `Catalog.food_states()` 判定：03 的 categories.py 构造类别推导输入时调用 `catalog.food_states()`，03 第 2 节「食品状态」一条改为引用本节，不另写判定条件。
- 食材包状态 `Catalog.kit_states()`：`reset_on_goods` 非空的食品状态。

在包 0001 里，`groceries`（做饭三集都凭它，《吃饭》一集写了 food，22 个食材包重置它）是食材包状态；`team_dinner`（和同事聚餐凭它并写了 food，没有重置商品）是食品状态但不是食材包状态；`detergent`（洗衣服凭它，没有 food）不是食品状态。

**一项活动用了哪份食材包。** 只从已提交事件推出，不另外存储：

- 输入是本人的 `environment_event` 中 `data.kind == "callback_effect"` 的两种效果，按事件序号：`reset_to_cap`（收货把状态置为上限，带 `goods_id`，callbacks.py:255-268）和 `consume_on_start`（凭状态的地点内容开始时减 1，interact.py:98-122）。只看 `data.state` 属于食材包状态的效果。
- 读到 `reset_to_cap` 时，记下这个状态的「最近一次重置」：商品 `goods_id`、收到时刻为这条事件的 `occurred_at`。后读到的重置覆盖先读到的，所以 cap 为 1 时两份先后收到，用的是后一份。
- 读到 `consume_on_start` 时，它的 `caused_by` 恰一项，是这次开始调用的受理 `action_result`（05-recording-engine.md:57-58；decision_coordinator.py:350-374）。这条结果事件信封上的 `activity_id` 就是用掉状态的活动。这项活动所用的食材包是此刻这个状态的「最近一次重置」；这个状态此前从未被商品重置过（运行开始时 `initial` 已不小于 1）时，是「家里原有的食材」（商品与时刻都为空）。
- 受理结果与 `consume_on_start` 在同一次提交里写入；记忆层的增量读取以提交为界，所以同一批读到的 `action_result` 里一定有它。找不到时先记 `Grocery kit use has no start result: <consume_on_start 的 event_id>`，再抛 `IntegrityError("Grocery kit use has no start result")`。
- 每项活动至多开始一次，所以每个活动身份至多一条记录；一项活动没有食材包状态的 `consume_on_start`，就没有记录，它的句子不写食材包。开始时用掉食材包状态的活动都有记录，不论它的内容是否属于系列（包 0004 的 `home-lunch-cook` 不属于系列，同样凭 `groceries`）。
- 推导是事件的纯函数：分批增量推导与一次读完全部事件的结果相同；恢复、重新打开存储和分支以后从事件重算，结果逐值相同。

**三种写法。** M、D 是收到时刻在运行时区的本地月与日，不补零，不论收到那天是不是今天都写；商品名是目录商品的 `name`。

| 写法 | 有食材包 | 家里原有的食材 | 用在哪里 |
| --- | --- | --- | --- |
| 完整式 | `，用的是M月D日收到的〈商品名〉` | `，用的是家里原有的食材` | 活动结束句的末尾（`activity_sentence`，经 `ending_lines`）：零点计划输入的「动作结果」 |
| 短式 | `，用M月D日收到的〈商品名〉` | `，用家里原有的食材` | 今天条目「核验」里消耗食材的做饭那一行的末尾，由 02 的条目生成调用 |
| 结果式 | `用的是M月D日收到的〈商品名〉。` | `用的是家里原有的食材。` | 接在结果明细里一行内容的 `summary` 末尾，规则见下 |

**结果明细。** `public_result` 投影 interact 活动的结果时，取这项活动所用的食材包（按快照行的 `activity_id` 查）。有记录时：

- 开始结果（`in_progress`）的 `details.content`：开始的那条内容凭的状态等于食材包记录的状态时，`summary` 为目录摘要原文后接结果式；
- 终结结果（`completed`、`cancelled`、`failed`）的 `details.contents`：写出的每一行（至多三行），凭的状态等于食材包记录的状态时，`summary` 同样接结果式；
- 其余行和没有记录的活动，`summary` 是目录摘要原文。

`content` 与 `contents` 行的键集和次序不变（04-simulation-engine.md:433-438）。进度层的带入记录和同秒续接的 `tool_results` 都经 06 的 `tool_call_record` 调用 `public_result`，第三个参数是同一次观察的 `ActivityFacts.kits`，所以开始结果和终结结果在这两处带同一句。`consumes_state`、状态的数值和 `consume_on_start` 事件本身仍不进任何提示层。

**示例（V 的事件链，事件序号取自 V 的 footprints.sqlite）。**

| 做饭开始 | consume_on_start | 此前同一状态最近一条 reset_to_cap | 完整式 |
| --- | --- | --- | --- |
| 10-08 18:05 | #1061 | 无 | `，用的是家里原有的食材` |
| 10-09 18:00 | #2421 | #1232，10-08 19:41，可乐鸡翅配清炒菌菇一人份食材包 | `，用的是10月8日收到的可乐鸡翅配清炒菌菇一人份食材包` |
| 10-10 18:15 | #3804 | #2580，10-09 20:20，香煎鸡胸肉配清炒时蔬一人份食材包 | `，用的是10月9日收到的香煎鸡胸肉配清炒时蔬一人份食材包` |
| 10-11 11:30 | #4646 | #4001，10-10 20:26，板栗烧鸡配清炒娃娃菜一人份食材包 | `，用的是10月10日收到的板栗烧鸡配清炒娃娃菜一人份食材包` |
| 10-12 18:30 | #6513 | #5325，10-11 19:55，土豆烧牛肉配清炒菌菇一人份食材包 | `，用的是10月11日收到的土豆烧牛肉配清炒菌菇一人份食材包` |

### 目录格式 6 与环境包

**引擎。** `CatalogManifest.format_version` 为 `Literal[6]`（resource_engine/catalog/models.py:51）。加载与纯校验（`load_catalog`、`validate_catalog_files`，loader.py:54-95）的次序不变：清单原始对象的 `rules_version` 是严格整数且不等于 6 时先报 `RULES_VERSION_MISMATCH`；然后严格清单模型拒绝 `format_version` 不是 6 的清单（含 5、4、字符串和缺失），报 `ENVIRONMENT_KIND_INVALID`，原因 `A declared kind is outside the fixed set.`，详情含 `format_version`（03-resource-engine.md:59 的现行规则，代码不变）。格式 5 的包一律被拒，没有兼容读取，没有迁移；规则版本 `RULES_VERSION` 保持 6。`refresh_manifest_identity` 照旧原样保留清单的 `format_version`。内容行和 app 行的可选字段 `memory_category` 的定义、取值和推导规则见 03。

**WorldMock 的测试环境。** tests/fixtures/dataset-environment/catalog/manifest.json 的 `format_version` 改为 6，再用 `refresh_manifest_identity` 重算文件表与 `catalog_content_sha256`；`environment_id` 仍为 `artificial-test`，`environment_version` 仍为 `"4"`，目录 JSONL 不变。

**WorldMock-Dataset。** 构建器 tools/build_environment.py 的 `FORMAT_VERSION = 6`（:38）。五个包全部用实现本规格的 WorldMock 重建，各包 sources/manifest.json 的 `environment_version` 加一：

| 包 | `environment_id` | 版本 | 目录行改动 |
| --- | --- | --- | --- |
| 0001-white-collar-lianqiu | `qingpu-lianqiu-white-collar` | `"9"` → `"10"` | `office-eat-lunch` 加 `"memory_category": "饮食"`；`home-dinner-1-cook` 的 `summary` 改写 |
| 0002-business-sales-zhaoming | `shanghai-cbd-business-sales` | `"5"` → `"6"` | 无 |
| 0003-female-student-chensiqi | `songjiang-campus-female-student` | `"6"` → `"7"` | 无 |
| 0004-homemaker-wangxiulan | `jiading-community-homemaker` | `"5"` → `"6"` | 无 |
| 0005-male-student-lihaoran | `songjiang-campus-male-student` | `"6"` → `"7"` | 无 |

- 0002–0005 的目录 JSONL、人设、配置、种子与地图逐字节不变，只有清单的 `format_version`、`environment_version`、`catalog_content_sha256` 变。
- environment/0002-social-corpus-texts 是没有发布包的采集语料作者输入（只有 sources/，tools/README.md:121 称「独立环境」），不在五个包之内：不重建、不升 `environment_version`，作者输入不改。
- 包 0001 的 `home-dinner-1-cook`（sources/catalog/contents/life.json:328 起的那一行）的 `summary` 恰为 `在家洗菜切配，照手头的食材做一两道家常菜，同时用电饭煲煮好米饭。`（提案示例原文，32 字）。
- 包 0001 的 `office-eat-lunch`（sources/catalog/contents/life.json:491 起的那一行）加键 `"memory_category": "饮食"`；包里其他行都不带 `memory_category`，0002–0005 的任何行都不带。
- 包 0001 的来源账本 sources/provenance/contents-life.json：
  - `home-dinner-1-cook` 的派生删去 `source_id` 为 `life-request-0001-dinner-shopping-plan`、`fields` 为 `["summary"]` 的那条事实；这个来源从此没有任何事实引用，同时从该分片的 `sources` 中删去它的声明（`unused_source_problems` 要求声明的来源都被引用）；
  - 同一派生的 `synthetic.summary` 改为：`Authored cooking description that names no dish: which ingredient kit she cooks with is written by the engine at the start and end of cooking. Until version 7 it began 下班回家后, which was wrong on rest days, when she also cooks; since version 8 it says only that she cooks at home; until version 9 it named 番茄炒蛋 and 清炒上海青 from the shopping plan as examples, which she then copied into her own notes.`
  - `office-eat-lunch` 的派生在 `synthetic` 加键 `memory_category`：`Override since version 10: eating the delivered lunch at the office has no food signal of its own (the meal is credited when the delivery is received), so the derivation would file it under 工作 by place; it is marked 饮食 so that lunch notes and conclusions are filed with food.`
- 重建后每个包 `python -m tools.build_environment --environment <包> --check` 逐字节一致、来源问题 0 条、内容质量问题 0 条，`--validate` 为 `valid`。

**LabelKit-DataSet。** 本仓不修改它。它要在 WorldMock 这次发布的同一次发布里，把 persona/src/labelkit_dataset/datasets/persona/export_source.py:21 的 `CATALOG_FORMAT_VERSION` 由 5 改为 6，并由它的会话验收。发给它的通知原文如下（交付报告原样附上）：

> 致 LabelKit-DataSet 会话：WorldMock 的分层记忆交付把环境目录格式从 5 升到 6，需要你们在同一次发布里改一处钉住并验收。
>
> 1. 必改：persona/src/labelkit_dataset/datasets/persona/export_source.py:21 的 `CATALOG_FORMAT_VERSION` 由 5 改为 6；:589-590 的核对（不符时报 `Unsupported frozen catalog version`）随之只接收格式 6。测试夹具 persona/tests/export_fixtures.py:132 的 `"format_version": 5` 改为 6；persona/tests/test_worldmock_source.py:829、:1502 把格式写成 4 的拒绝用例照旧。
> 2. 后果：改成 6 以后，格式 5 的导出一律被拒，包括 WorldMock-Dataset 本机的 execution/20261029T023604Z-white-collar-food-variety、execution/20261029T023604Z-white-collar-food-baseline，以及你们已保存的源快照。需要这些数据时用新引擎重新运行。
> 3. 目录行：内容行和 app 行可以带可选字段 `memory_category`，取值是「饮食」「工作」「生活」之一。你们只按身份索引目录行（source.py:92-101），不核对行字段，不用改。目前只有包 0001 的 `office-eat-lunch` 带它。
> 4. 五个包都按格式 6 重新发布：0001 `qingpu-lianqiu-white-collar` 版本 10、0002 `shanghai-cbd-business-sales` 版本 6、0003 `songjiang-campus-female-student` 版本 7、0004 `jiading-community-homemaker` 版本 6、0005 `songjiang-campus-male-student` 版本 7。只有 0001 改了目录行：`office-eat-lunch` 加了覆盖，`home-dinner-1-cook` 的摘要不再点名菜式。
> 5. 不变：导出清单 8、运行清单 7、世界 15、检查点 1、规则 6，事件类型仍是 14 种。质量报告版本由 9 升到 10，你们不核对它。
> 6. 事件与观察：新增 `environment_event` 种类 `memory_digest_generated`（日、月、年回顾），按你们现行规则裁成只剩 `{kind}`（export_source.py:347-359）；`memory_updated` 不再出现 `consolidated: true`，也不再有 `observation_event_id` 为空、`reflection` 非空的就寝反思登记；观察里的 `memory_layer` 整层换了形状（键用中文），待办层多一个 `orders` 键，这两层你们都不读。
> 7. 验收后请回告改动提交与测试结果。

### 本章内的实施顺序

本章各项的实施次序见 10「三　实施次序」：目录格式 6、`Catalog.food_states()`/`kit_states()` 与 WorldMock 测试环境清单在「P1 类别与归日」；食材包推导与三种写法在「P1 食材包句」；记忆层反思读者 `_reflections` 的删除在「P1 今天条目与记忆层」；人物一侧的巩固与睡醒反思、`due_reflection` 通道与写入侧反思登记的退役在「P1 检索与退役」（日回顾之后）；WorldMock-Dataset 的改动在「环境包」（包 0001 的作者输入可在「环境包作者输入」一线先做）。每一步都与对应的设计文档和测试同一次提交。

## 接口与文件

### src/worldmock/agents/memory/life_memory.py

- 删除：`from worldmock.agents.memory.life_reflection import ...`（:16-22）；`_PENDING_THRESHOLD`、`_IMPORTANCE_THRESHOLD`（:29-31）；`_REFLECTION_SOURCE`（:34）；`_SUBJECTIVE_PROMPT`（:37-42）；方法 `wake_reflection`（:127-143）、`_run_reflection`（:162-173）、`_validated_reflection`（:175-200）、`_consolidation_due`（:202-212）、`_consolidate_subjective`（:240-262）、`_reflection_episodes`（:264-267）、`_reflection_messages`（:269-275）；随之不再使用的导入（`json`、`xml_block`、`ProtocolError`）。
- `_CONCLUSION_SOURCE`（:33）保留，注释改为「结束活动写入点的记忆来源（系统固定，与 memory_updated.data.trigger 的取值同名）」。
- 新增模块函数：

  ```python
  async def _refuse_model_call(**request: Any) -> Any:
      """@brief WorldMock 记忆运行时不调用模型：AS2 的通用巩固入口已被步尾覆写，到达这里是宿主编排错误。"""
  ```

  行为见「人物一侧的记忆不再调用模型」。
- `WorldMockMemoryRuntime.__init__`（:48-56）：`dispatch_llm=_refuse_model_call`，其余参数不变。类文档改为「保留 AS2 存储与检查点，替换步尾保存与视图装配；人物一侧的记忆不调用模型。」
- `build_context`（:79-92）：行为不变，文档去掉「阈值巩固的摘要不进提示，裁决 #36」，改为「生活决策提示由七层组成，不读这里的条目与摘要」。
- `after_step`（:94-125）：签名不变，按「步尾」三步实现；删除 :114-117 的阈值判定与巩固、:122 的来源过滤；返回值的 `consolidated` 写字面量 `False`；`known = store.episode_ids()` 这一行保持原文（源码变异 `step_end_copies_every_memory_again` 以它为锚点）。文档改为「步尾：保存结束活动写下的主观条目，再确定性渲染当前事实窗口；不调用模型」，并说明 finish_memories 的来源与 sleep_boundary 不读。
- `_classify_episodes`（:319-326）：行为不变，文档改为「按本步前的稳定身份区分新建与合并」。
- `memory_update_evidence`（:329-348）：行为不变，文档去掉反思阶段，改为「步尾已落盘记忆的证据……既没有新增或合并条目、视图也没有变化时返回 None」。
- `_episode_snapshot`（:351-359）：删除 :354-355 的 `source` 分支，快照恰为四个基本键，仍经 `valid_subjective_snapshot` 校验。

### src/worldmock/agents/memory/life_memory_store.py

- 删除 `unconsolidated_view`（:98-110）。其余不变。

### src/worldmock/agents/memory/life_reflection.py

- 整个文件删除。

### src/worldmock/state/model/reflection.py 与 src/worldmock/state/model/memory.py

- reflection.py:8 的注释改为「历史运行里就寝反思提交洞见的唯一工具名；质量报告据此判定 reflection 调用类，新运行没有这类请求。」常量不变。
- memory.py:11 的注释改为「主观快照的键集：四个基本键；本规格交付之前的历史运行里，就寝反思的快照另带来源 source=reflection，质量报告与行为工具仍接受。」函数不变。

### custom/agents/life_agent.py

- 模块文档 :13-14「每个本地日第一次睡醒的那一步……一次决策恰有一份观察。」改为「一次决策恰有一份观察，取观察之前不做任何模型调用（docs/design/06-agent.md「宿主模型协议」）。」
- `ACTION_MAX_TOKENS` 的注释（:81-82）去掉「与记忆调用」，常量（:83）不变。
- `_usage_estimate` 文档（:230-231）的「（_model_request 与 _dispatch_memory_llm 写入）」改为「（_model_request 写入）」，行为不变。
- `step`（:302-309）：删除 `self._life_tick = tick`；文档改为「保留人物决策失败的完整异常链，便于定位真实服务故障。」
- `_observe_environment`（:311-322）：删除 :320-321；文档改为「使用 runner 绑定上下文直接读取 LifeEnv，取本次决策唯一的一份观察。」
- 删除 `_wake_reflection`（:324-337）、`_memory_call_id`（:339-346）、`_dispatch_memory_llm`（:411-434）和只被它使用的导入 `collect_transport_attempts`（:33）。`ModelCallEvidence` 仍由 `_record_model_evidence` 使用，`fit_request` 仍由 `_model_request` 使用。
- `_persona_card_text` 文档（:408）改为「从公开人物档案字段确定性渲染人物卡片文本（工作区 MEMORY.md 的身份段使用）。」
- `_record_memory_update` 文档（:510-511）去掉「就寝反思在取观察之前另行登记」。
- 记忆查询工具、只读路径与工具表的改动属于 05，层的取法属于 07，不在本章。

### custom/envs/life_env.py、src/worldmock/simulation_engine/engine.py、src/worldmock/runtime/framework/read_query.py

- life_env.py：删除 `due_reflection`（:147-149）。
- engine.py：删除导入（:17）与方法 `due_reflection`（:93-97）。
- read_query.py：删除 `LifeEnvActor.due_reflection`（:31-33）与 `LifeEnvProxy.due_reflection`（:47-49）。模块文档 :3-5 改为「这是锁定 AS2 提交上的 Life 扩展：不 monkey patch、不改 AS2、不做多版本兼容。两个子类各只新增一个只读方法 read_query，在冻结观察上回答人物的只读查询。运行装配（worldmock.runtime._build_society）以它们建立环境 actor 与代理；装配 AS2 原类时这个调用到不了环境。」:7-9 三个耦合点的说明改为单数（「LifeEnvActor.read_query 经它取环境」「LifeEnvActor.read_query 调用」「LifeEnvProxy.read_query 经它发起远端调用」）；两个类文档改为「相对 AS2 的 DirectEnvActor（EnvRouterProxy）只新增 read_query」。`read_query` 本身的转发不变；它回答哪些工具见 05。

### src/worldmock/simulation_engine/evidence/memory.py

- 整个文件删除（只含 `due_reflection` 及其「睡醒日减一」）。

### src/worldmock/simulation_engine/evidence/model.py

- 删除导入（:18）与 `_reflection_registration`（:173-184）。
- `record_memory_update`（:139-170）：`data.update(self._tail_registration(evidence.context, data), decision_id=evidence.context.decision_id)`，不再按观察是否为空分路；文档改为「保存人物已落盘记忆的枚举操作、内容身份与写入点（trigger）；只接受步尾登记，由环境按已提交事实核对，reflection 写 null。」异常捕获与日志不变。
- `_tail_registration`（:186-206）不变。

### src/worldmock/recording_engine/events/memory_evidence.py

- `_memory_evidence_data`（:49-71）：`observation_event_id` 必须是非空字符串；`consolidated` 必须恰为 `False`（`evidence.consolidated is not False` 即拒绝）。不满足时与其他形状错误相同：`logging.error("Memory update evidence is invalid")`，`raise ProtocolError("Memory update payload is invalid")`。返回的字典键集与次序不变。文档改为「核验实际记忆操作、主观条目快照及四个文件身份；来源观察为非空字符串，consolidated 恒为假」。
- `memory_write_points`（:24-42）行为不变，文档 :27 改为「写入点 reflection 只出现在历史运行的反思登记里，新运行不再产生」。

### src/worldmock/recording_engine/reports/quality.py

- 代码不变。`_memory_quality`（:386 起）、`_reflection_sourced`（:478-490）、`_valid_update_binding`（:593 起）、`_call_class`（:795-812）的文档各加一句「就寝反思（与记忆巩固）的登记和请求只出现在本规格交付之前的历史运行里，新运行中为零」。`CALL_CLASSES` 的增补与注释「六类」的改写见 04。

### src/worldmock/simulation_engine/observation/layers/memory_layer.py

- 删除 `_reflections`（:306-316）及它的错误 `Bedtime reflection has no filing date`。`MEMORY_WINDOW_CHARS`（:35-36）与 `_fit`（:319-336）由 01 删除，`_day_entries`（:225-237，含其中读反思的循环 :233-236）由 02 删除，本章不重复。记忆层整体改为 01 规定的薄门面，本人事件的增量缓存移到 observation/memory/entries.py（02）；食材包推导在缓存里的位置见下面 entries.py 一节。

### src/worldmock/resource_engine/catalog/models.py

- `CatalogManifest.format_version: Literal[6]`（:51）。
- `Catalog` 新增两个方法：

  ```python
  def food_states(self) -> frozenset[str]:
      """@brief 食品状态：至少被一条 completion_effects.food 不为空的内容凭着的回调状态。"""

  def kit_states(self) -> frozenset[str]:
      """@brief 食材包状态：reset_on_goods 非空的食品状态；只有凭它们的活动写所用食材包。"""
  ```

  `food_states` 遍历 `self.contents.values()`，取 `consumes_state` 不为 None、`completion_effects` 不为 None 且 `completion_effects.food` 不为 None 的内容的 `consumes_state`；`kit_states` 取 `food_states()` 中 `self.callback_states[name].reset_on_goods` 非空的名字。
- 食品状态只由 `Catalog.food_states()` 判定：03 的 categories.py 构造类别推导输入（食品商品、食品单元）时调用 `catalog.food_states()`，不另写判定条件。
- `Content.memory_category`、`App.memory_category` 见 03。

### src/worldmock/foundation/time/readable.py

- `month_day_phrase(epoch: int, timezone_name: str) -> str`（场景本地日期「M月D日」，月与日不补零）由 02 新增（02「readable.py」），本章使用它，不另定义。
- `clock_phrase`（:45-50）跨日分支改为 `f"{month_day_phrase(epoch, timezone_name)} {moment:%H:%M}"`，输出逐字节不变。`kit_phrase` 与 `clock_phrase` 的日期都经 `month_day_phrase`；simulation_engine/commerce/purchase/storefront_runtime.py:156、simulation_engine/scheduling/plans/day_plan_inputs.py:60、simulation_engine/activities/lifecycle/sessions.py:27 各自格式化的「M月D日 HH:MM」与 02 的 `entry_clock` 不在本章改动之内。

### src/worldmock/simulation_engine/observation/wording/settled_text.py

- 新增（放在 `ActivityFacts` 之前）：

  ```python
  @dataclass(frozen=True, slots=True)
  class KitUse:
      """@brief 一项凭食材包状态的活动开始时所用的食材包：state 是被减 1 的状态；goods_id 与 received_at 是此前同一
      状态最近一次被商品重置的商品与时刻，从未重置时同为 None（家里原有的食材）。"""

      state: str
      goods_id: str | None
      received_at: int | None


  @dataclass(frozen=True, slots=True)
  class KitTrack:
      """@brief 食材包的增量推导结果：latest 是各食材包状态最近一次被商品重置，uses 是活动身份 → 所用食材包。"""

      latest: Mapping[str, KitUse]
      uses: Mapping[str, KitUse]


  #: 还没有读过任何事件时的食材包推导结果。
  EMPTY_KIT_TRACK = KitTrack({}, {})
  ```

- `ActivityFacts`（:34-41）末尾新增字段 `kits: Mapping[str, KitUse]`（注释「活动身份 → 所用食材包；只含开始时用掉食材包状态的活动」），类文档补上这一项。
- 新增：

  ```python
  def track_kits(effects: Iterable[Event], results: Iterable[Event], previous: KitTrack,
                 states: frozenset[str]) -> KitTrack:
      """@brief 按事件序号读本人 callback_effect，推导食材包状态最近一次重置与各活动所用食材包；results 是同一批读到的
      本人 action_result，consume_on_start 的唯一起因必须在其中。"""
  ```

  输入约定：`effects` 与 `results` 是按事件序号升序的本人事件（`store.read_agent_events` 的返回次序）。算法：`starts = {event.event_id: event.activity_id for event in results if event.activity_id is not None}`；`latest, uses = dict(previous.latest), dict(previous.uses)`；按 `effects` 的次序逐条处理其中 `data.get("kind") == "callback_effect"` 且 `data.get("state") in states` 的事件：`type` 为 `reset_to_cap` 时执行 `latest[data["state"]] = KitUse(data["state"], data["goods_id"], event.occurred_at)`（这一行保持原文，供源码变异锚定）；`type` 为 `consume_on_start` 时取 `starts.get(event.caused_by[0])`（`caused_by` 不是恰一项，或取不到，都按「找不到」记日志并抛错），`uses[activity_id] = latest.get(data["state"], KitUse(data["state"], None, None))`；其他 `type` 跳过。返回 `KitTrack(latest, uses)`。
- 新增：

  ```python
  def kit_phrase(kit: KitUse, catalog: Any, timezone: str, short: bool) -> str:
      """@brief 所用食材包的短语（不带前导逗号与句末标点）：完整式「用的是M月D日收到的〈商品名〉」「用的是家里原有的食材」；
      short 为真时为短式「用M月D日收到的〈商品名〉」「用家里原有的食材」。"""
  ```

  动词取 `"用" if short else "用的是"`；`kit.goods_id` 为 None 时返回动词加 `家里原有的食材`，否则返回动词加 `month_day_phrase(kit.received_at, timezone)`、`收到的`、`catalog.goods[kit.goods_id].name`。
- `activity_sentence`（:63-81）签名改为 `activity_sentence(context, activity, pauses=(), app_progress=None, kits=None)`，`kits: Mapping[str, KitUse] | None`。interact 活动在 `app_progress` 检查之后检查 `kits`：为 None 时记 `Action activity sentence has no grocery kit evidence` 并抛 `IntegrityError("Action activity sentence has no grocery kit evidence")`。内容标题句写完之后：

  ```python
          if activity.activity_id in kits:
              sentence += "，" + kit_phrase(kits[activity.activity_id], context.catalog, zone, False)
  ```

  （第二行保持原文，供源码变异锚定。）非 interact 活动不读 `kits`。
- `ending_lines`（:172-178）调用 `activity_sentence` 时第五个参数传 `facts.kits`。06 删去 `_tool_lines` 的 conclude_activity 分支（:209-213）、02 删去 memory_layer.py 的 `_session_lines`（:240-257，其中 :249 调用 `ending_lines`）之后，`ending_lines` 是 `activity_sentence` 唯一的调用方，它唯一的调用方是零点计划（day_plan_inputs.py:206）。

### src/worldmock/simulation_engine/observation/layers/result_projection.py

- `public_result(result: dict[str, Any], inputs: EnvironmentInputs, kits: Mapping[str, KitUse]) -> dict[str, Any]`（:24-28）：`kits` 必填，没有食材包的调用方传 `{}`。
- `_details(result, inputs, kits)`；其中两处调用改为 `_interact_details(_activity_rows(payload), inputs, kits)` 与 `details.update(_done_contents(_activity_rows(payload), inputs, kits))`。
- `_interact_details(activities, inputs, kits)`：`"summary": _summary(content, kits.get(activity["activity_id"]), inputs)`。
- `_done_contents(activities, inputs, kits)`：`kit = kits.get(activity["activity_id"])`，每行 `"summary": _summary(contents[key], kit, inputs)`；`done = [(item["content_id"], True) for item in data["played"]]` 与 `for key, finished in done[:TITLE_LIST_LIMIT]]` 两处原文保留（两条现有源码变异以它们为锚点）。
- 新增：

  ```python
  def _summary(content: Any, kit: KitUse | None, inputs: EnvironmentInputs) -> str:
      """@brief 结果明细里一行内容的摘要：这项活动用过食材包、且这条内容凭的就是那个状态时，目录摘要后接结果式食材包句。"""
  ```

  `kit` 为 None 或 `content.consumes_state != kit.state` 时返回 `content.summary`，否则返回 `content.summary + kit_phrase(kit, inputs.catalog, inputs.config.timezone, False) + "。"`。
- 模块文档补一句「凭食材包状态的内容行，摘要后接所用食材包（docs/design/04-simulation-engine.md「出行、内容与查询」）」。

### 进度层与同秒续接的结果投影（接在 06 的最终接口上）

- 每次调用 `public_result` 都以同一次观察的 `ActivityFacts.kits` 作第三个参数。06 的接口为此带 `kits: Mapping[str, KitUse]`：`progress_view(context, store, published, kits)` 由 observe.py 传入同一次观察记忆层所用的 `ActivityFacts.kits`，再交给它产生的每条带入记录；`tool_call_record(context: LayerContext, request: Event, result: Event, kits: Mapping[str, KitUse]) -> dict` 以 `public_result(result.data, context.inputs, kits)` 投影 `result`。
- 06【C1】同秒续接投影上一决策的结果时，`kits` 是由 `agent_events(store, agent_id, context.catalog.kit_states())` 构造的 `ActivityFacts.kits`：与同一次观察的记忆层是同一缓存、同一 `last_event_seq`，所以是同一份。
- 本章不改 `_tool_records`、`_session_calls`、`_carried_calls`（06 删除），也不给 `record_lines`、`_tool_lines` 传 `kits`（06 删去它们的 `facts` 参数与 conclude_activity 分支，进度层不再写结束句）。
- 06 的测试里直接调用的 `public_result(…)` 一律加第三个参数：没有食材包的用例传 `{}`，比较同一次观察的投影时传那次观察的 `ActivityFacts.kits`。

### src/worldmock/simulation_engine/scheduling/plans/day_plan_inputs.py

- 导入 `EMPTY_KIT_TRACK`、`track_kits`；:203 改为 `ActivityFacts(pause_intervals(environment), endings, app_progress(progress, {}), track_kits(environment, outcomes, EMPTY_KIT_TRACK, context.catalog.kit_states()).uses)`。零点计划的「动作结果」因此带完整式食材包句。昨日窗口改用共享归日函数见 03。

### src/worldmock/simulation_engine/observation/memory/entries.py（02 新建）

- 02 的 `AgentEvents` 带字段 `kits: KitTrack = EMPTY_KIT_TRACK`（注释「食材包的增量推导」）。02 的 `agent_events` 签名为 `agent_events(store: FootprintStore, agent_id: int, states: frozenset[str]) -> AgentEvents`，在更新 `app_progress` 的同一增量步骤里执行 `cached.kits = track_kits(fresh["environment_event"], fresh["action_result"], cached.kits, states)`（`fresh` 是这一步按 `after=cached.last_seq` 读到的本人事件，序号升序）。02、03、04、05、06 中每个调用方传 `inputs.catalog.kit_states()`（或 `context.catalog.kit_states()`）；同一足迹库对象上的各次调用传的是同一份冻结目录的结果。02 由缓存构造 `ActivityFacts` 时第四个参数传 `cached.kits.uses`。
- 今天条目「核验」里，活动身份在 `ActivityFacts.kits` 里的那项活动的结束行（02 的「消耗食材的做饭」），末尾接 `"，" + kit_phrase(kits[activity_id], catalog, timezone, True)`，`kits` 取同一次观察的 `ActivityFacts.kits`；行的其余写法和条目全文里核验完整行的写法见 02。

### tools/run-viewer-context.js

- 不由本章改：`rules.reflection`（:116-125）的删除与换成 `digest` 键、记忆层规则说明（:41、:44）的改写都在 09「八　查看器」，这个文件只由 09 改。

### WorldMock 测试环境

- tests/fixtures/dataset-environment/catalog/manifest.json：见「目录格式 6 与环境包」。

### WorldMock-Dataset

| 文件 | 改动 |
| --- | --- |
| tools/build_environment.py | `FORMAT_VERSION = 6`（:38）；模块文档 :1「格式 5」→「格式 6」；argparse 描述 `Build a format-5 environment package from its sources` → `Build a format-6 environment package from its sources` |
| tools/environment_sources.py:3、tools/search_map_seed.py:3 | 「格式 5」→「格式 6」 |
| environment/0001-white-collar-lianqiu/sources/catalog/contents/life.json | 两行改动见上 |
| environment/0001-white-collar-lianqiu/sources/provenance/contents-life.json | 见上 |
| environment/000{1..5}-*/sources/manifest.json | `environment_version` 见上表 |
| environment/000{1..5}-*/package/ | 用 `python -m tools.build_environment --environment environment/<包>` 重建，不手改 |
| environment/README.md:3、README.md:4、README.md:16、tools/README.md:3-4、docs/design/filesystem.md:4、docs/manual/execution.md:3 | 当前格式的陈述「格式 5」改为「格式 6」；README.md:16、tools/README.md:3-4 与 environment/README.md:3 的引擎写成重建所用的 WorldMock 检出的 8 位短哈希（下称〈H〉），即 10「三　实施次序」「工具与查看器」一步之后、已含全部生产改动的提交（此后的「重冻」提交不改生产文件），并写明制作、离线校验与执行都要求〈H〉起的引擎；environment/README.md:3 改为「当前有五个单人环境包，全部采用 WorldMock 环境数据集合同格式 6、规则版本 6（WorldMock `〈H〉` 起）：」 |
| 各包 README.md | 见「设计文档」一节的数据集文档 |

## 设计文档

文档、实现与测试同一次提交（docs/INDEX.md:18）。每处写明改写后必须陈述的内容。

### docs/design/06-agent.md

- 「人物身份与画像」:35：「卡片文本 `persona_card` 供记忆巩固与就寝反思使用」改为「卡片文本 `persona_card` 供工作区 `MEMORY.md` 的「身份」段使用；日回顾的「人物卡片」输入由环境一侧从 persona.toml 构造（`render_card(persona_card(persona))`），与它逐字相同（见「进度、待办与记忆」的「日、月、年回顾」）」。04 不另写这一行（04 设计文档表「人物身份与画像」:33-35 一行）。
- 「提示与工具」:121-122 整句改为：「完成效果不改变列出：行的字段与时长和其他单元相同，`note` 只为 `food` 多出上表那两种说法；完成效果的两项缓解和完成效果的证据不进任何提示层，完成效果体现在饥饿、放松与社交体感上。凭食材包状态（`reset_on_goods` 非空的食品状态）的内容，在开始结果与终结结果的内容摘要、今天条目的核验行和零点计划输入的结束句里写出所用食材包和收到日期，运行开始以来还没收到过时写家里原有的食材；`consumes_state`、状态的数值和 `consume_on_start` 事件本身仍不进任何提示层，写法见[模拟引擎](04-simulation-engine.md)「出行、内容与查询」。」
- 「今天条目」（02 新增的小节）写〈食材段〉时链到[模拟引擎](04-simulation-engine.md)「出行、内容与查询」的三种写法表，不另写短式的字样。
- 「进度、待办与记忆」:285-304（就寝反思的判定表、归属日期、`earlier_days` 里的感想、8000 字符窗口裁感想、`Bedtime reflection has no filing date`、「反思与人物自述分开记录」一句）删除；:305「删除自然语言目标自动进展……」保留。这一节其余的重写见 01、02、03、04、06。
- 「宿主模型协议」:311：改为「人物一次决策每个模型回合的输出上限是引擎固定的 16,000 与实际服务输出上限中较小的一个，思考与回答共用；只读问答与截断后的那次重试用同一个数，零点计划与回顾各用自己的 8,000。这个数是引擎固定值，不在 `config.toml` 里。」（回顾的 8,000 由 04 定义。）
- 「宿主模型协议」:322-347 改为：一步按观察、决策、步尾三段，一次决策恰有一份冻结观察；:323-325 冻结观察的说明不变；流程图换成三个节点「取本次决策唯一的一份观察 → 决策 → 步尾：保存收尾记忆，重写 MEMORY.md」；其后一段陈述：步尾保存这次收尾写下的主观条目并重写 `MEMORY.md`，有变化时登记一条 `memory_updated`（见[记录引擎](05-recording-engine.md)「事件与来源」）；人物一侧的记忆不调用模型，没有按条数或重要度触发的记忆巩固，也没有睡醒时的就寝反思，主观摘要从不更新，`MEMORY.md` 的「人物自述（未经核验）」一段恒为「暂无人物自述。」；LifeAgent 交给 AgentSociety2 记忆运行时的模型调用入口只会以 `WorldMock memory runtime does not call the model` 失败；锁定的 AgentSociety2 仍向步尾传 `sleep_boundary`，LifeAgent 不覆写取值钩子，这个值恒为假且没有读者；往日的整理由环境在零点生成的回顾承担（见「进度、待办与记忆」）。反思阶段的调用身份、`submit_reflection` 请求、`daily reflection` 原因文字和反思校验耗尽的运行故障等说法全部删除。

### docs/design/04-simulation-engine.md

- :9：「那一刻没有一段睡眠结束，不做就寝反思，也不触发唤醒类别回调」改为「那一刻没有一段睡眠结束，不触发唤醒类别回调」。
- 「出行、内容与查询」在 :286-291（减 1 与 `consume_on_start`）之后新增一段，陈述「家里做的饭叫得出名字」一节的全部规则：食品状态与食材包状态的定义及 `Catalog.food_states()`、`Catalog.kit_states()`；所用食材包取开始那条 `consume_on_start` 之前同一状态最近一条 `reset_to_cap` 的商品与收到日期，没有重置时为家里原有的食材，cap 为 1 时后收到的一份顶掉前一份；`reset_on_goods` 为空的食品状态和非食品状态不写，是否属于系列不作条件；三种写法的表（完整式、短式、结果式，含用在哪里）原样列出；M、D 不补零，商品名取目录 `name`；`consumes_state`、状态的数值和 `consume_on_start` 事件本身不进提示层。这是三种写法唯一的规范位置，06-agent.md 和今天条目的说明引用这里。
- 「出行、内容与查询」:433-442：`details.content` 与 `details.contents` 的键集不变，补一句「`summary` 是目录摘要原文；这项活动开始时用掉了食材包状态、而这一行的内容凭的正是那个状态时，摘要后接结果式食材包句，见上文」。

### docs/design/05-recording-engine.md

- 「事件与来源」:80-96 改写为：memory_updated 的键集照旧列出；环境只接受步尾登记，以一张表列出「行为」一节 memory_updated 表中的五项要求；任一条不满足先记英文错误日志，再以 `ProtocolError("Memory update evidence is invalid")` 作为运行故障拒绝；每一步至多一条 `memory_updated`。删除「反思登记」一行（:86）和「睡醒那一步最多有两个 `memory_updated`」一句（:90）。新增一段：键 `consolidated` 与 `reflection` 保留，使本规格交付之前的历史运行仍按同一形状重新认证——那时人物一侧有记忆巩固（`consolidated` 可为真）和睡醒时的反思登记（`observation_event_id` 为 `null`、`trigger` 恰为 `["reflection"]`、`reflection` 为 `{date, sleep_activity_id}`）；:92-95 质量报告对 `reflection` 非空的记忆更新独立重算四件事的规则原文保留，并注明只用于重新认证这些历史运行，新运行里没有这类登记；:96「`reflection` 为空的更新照旧核对观察与事实窗口」保留。
- :100：「零点计划和就寝反思的输入」改为「零点计划和回顾的输入」。
- 「完整导出认证」调用类表 :642 `consolidation` 行改为「没有 purpose，不带工具：记忆巩固；只出现在本规格交付之前的历史运行里，新运行恒为 0」；:643 `reflection` 行改为「没有 purpose，工具恰为 `submit_reflection`：就寝反思及其校验重试；只出现在历史运行里，新运行恒为 0」。新增 `memory_digest` 行与「七类」的改写见 04。

### docs/design/03-resource-engine.md

- :31 `manifest.json` 一行的 `format_version: 5` 改为 `format_version: 6`；:40 改为「规则版本 `RULES_VERSION` 是 6，目录格式 `format_version` 是 6；这两个号只在本节维护。」；:59 的规则文字不变。这一文件里 `memory_category` 的各处（:32、:35、:40 之后的新段、拒绝码表）由 03 改写。

### docs/design/01-architecture.md

- :63 数字人的职责写「画像、七层提示组装、行动选择与收尾记忆」。
- :73 写「默认不写回仍在运行的数字人；数字人运行中的收尾记忆由人物模块维护，今天条目与日、月、年回顾由模拟引擎从已提交事件和零点的模型调用生成。」
- :172 数字人一行把 `[就寝反思工具](../../src/worldmock/agents/memory/life_reflection.py)` 换成 `[日、月、年回顾](../../src/worldmock/simulation_engine/evidence/memory_digest.py)`（该文件由 04 新建；文档门禁 tests/unit/engineering/test_documentation_gate.py:51-52 核对链接有效）。

### docs/design/00-overview.md、docs/design/07-evaluation.md、docs/design/10-runtime.md

- 00-overview.md:138「引用完整性」里「就寝反思每条洞见的 `refs` 非空、条内不重复且全部取自本次输入的记忆身份，不合合同的反思不写入任何洞见；」一句由 04 改写为回顾 `refs` 的规则（04 设计文档表「docs/design/00-overview.md :138」一行），本章不另写；改写之后这一格不再出现就寝反思。同一格里 `subject` 的改写见 07。
- 07-evaluation.md:71 删去「这个门槛与就寝反思无关，反思只看是不是当天第一次睡醒。」
- 10-runtime.md:22「生成请求与后台日计划、反思共用相同 dispatcher 和冻结身份」改为「生成请求与零点的日计划、回顾共用相同 dispatcher 和冻结身份」。
- 10-runtime.md:79-91 的终稿文字照 05（05 设计文档第 3 条，它同时是删除 `due_reflection` 之后的终稿），本章不另写；本章只要求改写之后 10-runtime.md 不再出现 `due_reflection`、「先反思、再做当天第一次决策」和取观察之前对环境的询问。

### docs/manual

- docs/manual/personas.md:105 `situation` 一行的「用途」改为「工作区 `MEMORY.md` 身份段与日回顾读的卡片文本（日回顾的输入见[人物](../design/06-agent.md)「日、月、年回顾」）」。
- docs/manual/resources.md:9 写「清单的 format_version 写 6，rules_version 写 6。」
- docs/manual/resources.md:169 改为「报 RULES_VERSION_MISMATCH 时把清单的 format_version 改成 6、rules_version 改成 6，并按当前格式改完各文件；」并在其后加一行「格式 5 的包报 ENVIRONMENT_KIND_INVALID：把清单的 format_version 改成 6 再刷新清单，目录行不用改（格式 6 只多出可选的 memory_category）。」
- docs/manual/resources.md「买菜做饭」的要点列表（:128-142）在「系列各集的 consumes_state 必须相同」之后加一条：「状态被一件供餐的内容凭着（凭它的内容里有一条写了 completion_effects.food）、又写了 reset_on_goods 时，人物开始做饭的结果、做完的结束句和零点计划都写出这次用的是哪天收到的哪件商品，例如「用的是1月5日收到的蔬菜包」；运行开始以来还没收到过时写「用的是家里原有的食材」。」

### WorldMock-Dataset 文档

- 各包 README.md 的首段：版本号改为新版本，「格式 5（清单 `format_version` 5、`rules_version` 6……）」改为「格式 6（清单 `format_version` 6、`rules_version` 6）」；0001 的这一句另把「引擎主线 `600c50cf`」改为「引擎 WorldMock `〈H〉`」，0005 标题的「（format 5）」改为「（format 6）」。
- 各包 README.md 在最后一个版本段之后新增一段：
  - 0002–0005：「版本 `N` 只把清单的 `format_version` 由 5 改为 6，跟随 WorldMock `〈H〉`（目录行可带可选的 `memory_category`）；目录 JSONL、人设、配置、种子与地图都与版本 `N−1` 逐字节相同。格式 5 的引擎拒绝加载版本 `N`，格式 6 的引擎拒绝加载版本 `N−1` 及更早的包（`ENVIRONMENT_KIND_INVALID`）。」N 取上表的新版本。
  - 0001：「版本 `10` 跟随 WorldMock `〈H〉` 的分层记忆，清单的 `format_version` 由 5 改为 6。吃午饭（`office-eat-lunch`）加 `memory_category` 覆盖，标为「饮食」：它在班次地点、本身没有食品信号，按推导会落到工作。备菜做饭（`home-dinner-1-cook`）的摘要不再点名菜式，改为「在家洗菜切配，照手头的食材做一两道家常菜，同时用电饭煲煮好米饭。」；此前的「比如番茄炒蛋、清炒上海青」在每次开始做饭时出现在人物读到的结果里，被照抄进自述，而做的是哪份食材包由引擎在开始结果、结束句和零点计划里写出。其余行、人设、配置、种子与地图都与版本 `9` 相同。」
- 各包 README.md 记录清单与 `catalog_content_sha256` 的那句（0001 :193 起、0002 :75、0003 :90、0004 :170、0005 :93）写新版本、新格式号和重建后的摘要，把原摘要按该句现有的括注体例记为上一版本的值；校验证据处新增一句「版本 `N` 在 WorldMock `〈H〉` 上：`--check` 逐字节一致、来源问题 0 条、内容质量问题 0 条，`--validate` 为 `valid`」，括号里的计数照 `--validate` 的实际输出写。
- 0001 README.md :167 流程图节点「封闭格式 5 环境根」改为「封闭格式 6 环境根」；:234「安装了格式 5、规则版本 6 WorldMock 的 Python（例如引擎仓 `600c50cf`……）」改为格式 6 与〈H〉。
- 其余写到「格式 5」的历史版本说明（「格式 5 迁移」「格式 5 判例」等）是当时的记录，不改。

## 测试

### 新增

**tests/integration/runtime/test_memory_retirement.py**（新文件，使用 tests/integration/host_run_fixtures.py 的真实宿主运行）：

| 测试 | 钉住的行为 |
| --- | --- |
| `test_first_wake_up_of_the_day_sends_only_action_requests` | 周一 20:00 开始的运行里，做两次「测试休息」，每次收尾各写 6 条互不相似、重要度 0.7 的记忆（合计 12 条、重要度 8.4，超过已退役的两条阈值；一项至多 8 条由 AS2 的 `MemoryExtractionResult` 限定），再睡 480 分钟到当天第一次睡醒并完成那一步的决策：脚本模型收到的每个请求的工具都含 `start_main_activity`；库里 `data` 不带 `purpose` 的每条 `model_request` 都是行动请求，其余 `model_request` 的 `purpose` 只有零点的 `plan_day` 与 `memory_digest`；`store.read_subjective_summary() == ""`，`MEMORY.md` 含 `> 暂无人物自述。` |
| `test_memory_updates_are_step_tail_registrations_only` | 同一运行里每条 `memory_updated`：`observation_event_id` 等于本决策观察的事件身份，`reflection` 为 `None`，`consolidated` 为 `False`，`trigger` 是 `[]` 或 `["activity_conclusion"]`；同一 `decision_id` 至多一条；快照恰为四个基本键 |
| `test_memory_update_evidence_without_an_observation_is_rejected` | 把一条合法的步尾证据改成 `observation_event_id=None` 交给 `LifeEngine.record_memory_update`：`ProtocolError("Memory update evidence is invalid")`，错误日志含 `Memory update evidence rejected`，`last_event_seq` 不变 |
| `test_consolidated_memory_update_evidence_is_rejected` | 同上，改成 `consolidated=True` |
| `test_life_channel_offers_only_read_query` | `LifeEnvActor`、`LifeEnvProxy`、`LifeEnv`、`LifeEngine` 都没有属性 `due_reflection`；前三者有 `read_query` |
| `test_retired_memory_paths_are_gone` | `WorldMockMemoryRuntime` 没有 `wake_reflection`、`_run_reflection`、`_validated_reflection`、`_consolidation_due`、`_consolidate_subjective`、`_reflection_episodes`、`_reflection_messages`；life_memory 模块没有 `_PENDING_THRESHOLD`、`_IMPORTANCE_THRESHOLD`、`_REFLECTION_SOURCE`、`_SUBJECTIVE_PROMPT`；`WorldMockMemoryStore` 没有 `unconsolidated_view`；`LifeAgent` 没有 `_wake_reflection`、`_memory_call_id`，且 `LifeAgent._dispatch_memory_llm is PersonAgent._dispatch_memory_llm`（AS2 基类的方法，LifeAgent 不覆写）；`ModelEvidenceRecorder` 没有 `_reflection_registration`；memory_layer 模块没有 `MEMORY_WINDOW_CHARS`、`_reflections`、`_fit` |

**tests/integration/as2/test_worldmock_life_hooks.py**（增加）：

| 测试 | 钉住的行为 |
| --- | --- |
| `test_step_end_past_the_retired_thresholds_calls_no_model` | 先后两次 `after_step` 各写入 6 条互不相似、重要度 0.7 的收尾记忆：`consolidated is False`，`store.read_subjective_summary() == ""`，没有异常（记忆运行时的模型入口一旦被调用就会抛错） |
| `test_memory_runtime_refuses_every_model_call` | `await runtime._dispatch_llm(model="m", messages=[])` 抛 `RuntimeError("WorldMock memory runtime does not call the model")`，错误日志恰有这一句 |

**tests/unit/catalog/test_food_states.py**（新文件，在 `grocery_environment` 副本上加两个状态后 `load_catalog`）：

| 测试 | 钉住的行为 |
| --- | --- |
| `test_food_states_are_states_a_food_content_consumes` | 副本：`groceries`（「做饭」凭它、写 food、重置商品扑克牌）；新增 `team_dinner`（一件凭它、写 food 的地点内容，`reset_on_goods` 为空）；新增 `detergent`（一件凭它、不写 food 的地点内容，重置商品纸巾）。`food_states() == {"groceries", "team_dinner"}`，`kit_states() == {"groceries"}` |
| `test_a_series_is_a_food_state_when_any_episode_feeds` | 三集系列各集凭同一状态、只有第 2 集写 food：这个状态在 `food_states()` 里 |

**tests/unit/life/test_kit_sentence.py**（新文件，纯函数；事件用 `SimpleNamespace(event_id, event_seq, occurred_at, activity_id, caused_by, data)` 构造）：

| 测试 | 钉住的行为 |
| --- | --- |
| `test_a_start_without_an_earlier_reset_uses_the_food_already_at_home` | 只有一条 `consume_on_start`：`uses[活动] == KitUse("groceries", None, None)` |
| `test_the_latest_reset_before_the_start_names_the_kit` | 重置 A、重置 B、开始：用的是 B 与 B 的时刻；开始之后的重置 C 不影响这次开始 |
| `test_effects_of_other_states_are_ignored` | 状态不在 `states` 里的重置与减 1 不产生记录；`kind` 不是 `callback_effect` 的事件跳过 |
| `test_a_use_whose_start_result_is_missing_is_an_integrity_error` | `consume_on_start` 的起因不在 `results` 里：`IntegrityError("Grocery kit use has no start result")`，错误日志含该事件身份 |
| `test_incremental_tracking_equals_one_pass` | 同一串事件按提交边界分两批 `track_kits` 的结果与一次读完相同 |
| `test_kit_phrase_has_full_and_short_forms` | 有商品时完整式 `用的是1月5日收到的扑克牌`、短式 `用1月5日收到的扑克牌`；无商品时 `用的是家里原有的食材`、`用家里原有的食材` |
| `test_month_day_phrase_does_not_pad` | `month_day_phrase`（02 新增）给出 `10月10日`；`clock_phrase` 跨日时仍是 `1月5日 08:00`，同日时仍是 `08:00` |

**tests/unit/life/test_shared_observation_contract.py**（增加）：

| 测试 | 钉住的行为 |
| --- | --- |
| `test_interact_sentence_requires_grocery_kit_evidence` | interact 活动给了 `app_progress={}` 而 `kits=None`：`IntegrityError("Action activity sentence has no grocery kit evidence")` |

**tests/integration/life/test_grocery_cooking.py**（增加，复用文件里的 `grocery_engine`、`receive`、`cook`、`until_ended`）。开始结果与终结结果的 `details` 一律从 `engine.observe(context(engine, …))["progress_layer"]` 里这项活动的那条 `tool_call` 记录（06 的带入记录：做饭进行中时 `result` 是开始结果，做完待收尾时是终结结果）的 `result.details` 读取，不在测试里直接调用 `public_result`：

| 测试 | 钉住的行为 |
| --- | --- |
| `test_cooking_with_the_food_at_home_names_it_in_results_and_the_day_plan` | `initial=1`，做饭：开始结果 `details.content.summary == "用买来的食材做一顿饭，吃完收拾。用的是家里原有的食材。"`；做完后终结结果 `contents` 唯一一行的 `summary` 同样以 `用的是家里原有的食材。` 结尾；推进到 2026-01-06 00:00（样例环境的运行窗口到 01-06 08:00），`plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["动作结果"]` 里做饭的结束句以 `，用的是家里原有的食材` 结尾（下面几条的「零点计划」都按这个取法） |
| `test_cooking_after_a_kit_arrives_names_the_kit_and_the_day_it_arrived` | `initial=0`，周一收到扑克牌后做饭：开始结果摘要以 `用的是1月5日收到的扑克牌。` 结尾，零点计划的结束句以 `，用的是1月5日收到的扑克牌` 结尾 |
| `test_the_second_of_two_kits_received_before_cooking_is_the_one_used` | `goods=("playing-cards", "tissue")`，并用 `change` 把副本 merchant-catalog.jsonl 里纸巾的 `store_kinds` 改为 `["cafe", "convenience_store"]`（样例环境的便利店没有 app，纸巾原本送不到家）；先收纸巾、再收扑克牌、再做饭：开始结果、终结结果与零点计划的句子都写扑克牌，不写纸巾 |
| `test_a_state_without_reset_goods_adds_no_kit_sentence` | `goods=()`、`initial=1`：开始结果、终结结果和零点计划里都没有「用的是」 |
| `test_the_kit_sentence_is_the_same_after_reopening_the_store` | 收到扑克牌并开始做饭后写检查点、关闭并重新打开存储：在重开的引擎上取一次观察，进度层开始结果的 `summary` 与不重开的同一脚本逐字节相同（重开的存储对象缓存为空，从事件重算）；做完后零点计划的结束句同样逐字节相同 |

**tests/dataset_acceptance.py**（增加两条源码变异）：

| 名称 | 目标与改动 | 必须被杀死的测试 |
| --- | --- | --- |
| `kit_sentence_left_out` | `worldmock.simulation_engine.observation.wording.settled_text::activity_sentence`：`sentence += "，" + kit_phrase(kits[activity.activity_id], context.catalog, zone, False)` → `pass` | `tests/integration/life/test_grocery_cooking.py::test_cooking_after_a_kit_arrives_names_the_kit_and_the_day_it_arrived` |
| `kit_from_the_earliest_reset` | `worldmock.simulation_engine.observation.wording.settled_text::track_kits`：`latest[data["state"]] = KitUse(data["state"], data["goods_id"], event.occurred_at)` → `latest.setdefault(data["state"], KitUse(data["state"], data["goods_id"], event.occurred_at))` | `tests/unit/life/test_kit_sentence.py::test_the_latest_reset_before_the_start_names_the_kit` |

**WorldMock-Dataset**：

| 文件与测试 | 钉住的行为 |
| --- | --- |
| tests/test_environment_packages.py `test_format_6_release_bumped_every_environment_version`（按 `sorted(FORMAT6_FIRST_VERSIONS)` 参数化，不用 `environment` 夹具：夹具的 `ENVIRONMENTS` 含没有发布包的 0002-social-corpus-texts） | 新常量 `FORMAT6_FIRST_VERSIONS = {"0001-white-collar-lianqiu": 10, "0002-business-sales-zhaoming": 6, "0003-female-student-chensiqi": 7, "0004-homemaker-wangxiulan": 6, "0005-male-student-lihaoran": 7}`；作者清单的 `int(environment_version)` 不小于表中值，且发布清单的 `format_version == 6` |
| tests/test_environment_packages.py `test_only_package_0001_overrides_a_memory_category` | 五个包的发布内容行与 app 行中带 `memory_category` 的恰为 `[("0001-white-collar-lianqiu", "office-eat-lunch", "饮食")]` |
| tests/test_white_collar_package.py `test_cooking_summary_names_no_dish` | 作者输入与发布行里 `home-dinner-1-cook` 的 `summary` 都恰为新摘要 |
| tests/test_white_collar_package.py `test_office_lunch_is_filed_under_food` | 作者输入与发布行里 `office-eat-lunch` 的 `memory_category` 都是 `饮食` |

### 修改

**WorldMock：**

| 测试 | 改动 |
| --- | --- |
| tests/integration/reflection_fixtures.py | 改名为 tests/integration/host_run_fixtures.py：`DayModel.call` 只答行动请求，请求的工具不含 `start_main_activity` 时抛 `AssertionError("LifeAgent sent a non-action model request")`；删去 `REFLECTION_TOOL`、`CONSOLIDATED`、`INSIGHTS`、`REFLECTION_NOTE`、`reflection_input`、`submit_reflection`、`reflection_replies`、`HostRun.reflections`，以及只被已删除的 test_daily_reflection.py 使用、按位置取记忆层的 `memory_layer_of`（:175-177）；从 tests/integration/runtime/test_strict_reflection.py 移入 `_evening` 与 `waking_decision(engine_factory, tmp_path, note="这一晚歇得很踏实。")`（去掉 `*replies` 与 `reflection_replies` 的赋值） |
| tests/integration/footprints/test_inspect_current_activity.py:17、:206 | 改从 host_run_fixtures 导入；文档里的文件名同改 |
| tests/integration/runtime/test_host_failure_stages.py:38 及 :183、:216、:257、:301 | 改用 host_run_fixtures 的 `waking_decision(engine_factory, tmp_path, note=None)` |
| tests/system/lifecycle/test_sleep_and_reflection_run.py | 改名为 tests/system/lifecycle/test_sleep_runs.py；`test_run_with_naps_exports_clean_memory_sources_and_a_resume_at_wake_up_reflects_once` 改名为 `test_run_with_naps_exports_clean_memory_sources_and_makes_no_memory_model_call`：场景不变，删去反思归属的断言，改为原运行与分支运行的脚本模型都只收到行动请求；导出的 `by_call_class` 里 `consolidation`、`reflection` 两行在且 `request_events`、`provider_responses` 都为 0；导出里每条 `memory_updated` 的 `reflection` 为 null、`consolidated` 为 false；`memory_references` 无问题，`write_points_checked == 3`（原运行里带记忆的三次收尾：两次 `rest_and_note` 与一次 `conclude(memory(...))`；quality.py:545 按 `len(trigger)` 计，不带记忆的收尾 `trigger` 为 `[]`，`run_to_end` 不登记 `memory_updated`；退役前另有两条反思登记） |
| tests/integration/as2/test_worldmock_life_hooks.py | 删除 `test_consolidation_failure_propagates`（:465）、`test_consolidation_triggers_importance_accumulation`（:487）、`test_consolidation_triggers_pending_threshold`（:509）、`test_sleep_boundary_no_longer_forces_consolidation_at_the_step_tail`（:538）、`test_single_preference_does_not_trigger_consolidation`（:567）、`test_consolidation_prompt_carries_persona_card_without_example_identity`（:598）、`test_reflection_persists_cited_insights`（:702）、`test_reflection_rejects_unknown_reference`（:736）、`test_reflection_rejects_more_than_five_insights`（:751）；`test_step_end_below_the_threshold_reads_memory_without_copying_it`（:776）改名为 `test_step_end_reads_memory_without_copying_it`，内容不变；`_runtime` 的替身人物去掉 `_dispatch_memory_llm` |
| tests/integration/as2/test_worldmock_memory_boundaries.py | 删除 `test_cache_reads_equal_the_copying_reads_at_every_cursor`（:176）、`test_a_dangling_consolidation_cursor_is_refused`（:189）、`test_cache_reads_return_the_cached_records_without_copying`（:199）；:25 替身去掉 `_dispatch_memory_llm` |
| tests/integration/runtime/test_verified_memory.py | 删除 :22 导入、`test_sleep_reflection_is_subjective_and_its_id_is_reported`（:189）、`test_insight_absorbed_by_the_person_s_own_similar_note_is_not_reported_as_a_reflection`（:236）、`test_conclusion_memory_absorbed_by_an_earlier_insight_is_not_reported_by_the_step_tail`（:259）；`test_memory_runtime_without_a_workspace_does_not_claim_a_persisted_update` 删去 :75-76 的 `wake_reflection` 两行；`_runtime` 替身去掉 `_dispatch_memory_llm` |
| tests/integration/runtime/test_persona_profile.py | `test_memory_prompts_carry_the_card_text_rendered_from_the_persona_table`（:288）改名为 `test_memory_view_identity_is_the_card_text_rendered_from_the_persona_table`：不再调用 `_consolidate_subjective`、不再替换 `_dispatch_memory_llm`，改为在真实 `LifeAgent` 的记忆运行时上做一次 `after_step`（`_life_verified_experiences` 为空列表、`_life_observation_event_id` 为任一非空串），断言 `profile["persona_card"] == render_card(persona_card(declared.persona))`，且这段文本逐行以 `> ` 引用出现在 `MEMORY.md` 的「## 身份」段里，原有五个短语都在其中 |
| tests/integration/runtime/test_model_exchange_attempts.py | 删除 `test_memory_call_evidence_carries_attempts`（:70-90） |
| tests/integration/runtime/test_life_agent_protocol.py | 删除 `test_life_tool_schema_fingerprint_is_the_person_tool_surface_without_the_reflection_tool`（:353；05 的测试表同样把它的删除归本章；「指纹等于人物工具面规范 JSON 的 SHA-256」照旧由 tests/integration/runtime/test_seven_layer_request.py:47 钉住，工具次序由 05 的 `test_life_tool_surface_has_eight_tools_in_fingerprint_and_request_order` 钉住）、`_StepEnvironment.due_reflection`（:828-831）、`_WakingStepEnvironment`、`_InvalidReflectionDispatcher` 与 `test_reflection_contract_failure_aborts_society_before_commit`（:1664）。替身环境不再有 `due_reflection`，其余经真实 `LifeAgent.step` 的测试因此证明取观察之前不询问环境 |
| tests/integration/runtime/test_conclude_memories.py | 删除 `test_threshold_consolidation_counts_conclusion_entries_but_not_bedtime_reflection`（:885）及只为它存在的 `_CONCLUSION_MEMORIES`（:814）、`_INSIGHTS`（:831）、`_DayModel`（:835）、`_DayEnvironment`（:866，含 `due_reflection`）；:101 文档去掉巩固与反思的说法；:379 拒绝 `{"source": "reflection"}` 收尾记忆的参数保留 |
| tests/integration/runtime/test_read_query_runtime.py | 模块文档删去 :9-10 关于 `due_reflection` 的一段；`test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query` 删去 `due` 替身与 :273-276 的断言，改为 `assert not hasattr(agent_side, 'due_reflection')`；删除 `test_unwrapped_actor_due_reflection_equals_direct_environment_call`（:280）；:121-122 的注释与 `due_reflection` 断言删除 |
| tests/integration/life/test_committed_world_guard.py:118-122 | 公开方法集合去掉 `"due_reflection"`（新增的 `register_memory_digest`、`search_memory` 见 04、05） |
| tests/integration/life/test_sleep_runtime.py:402 | 删去 `engine.due_reflection(woke)` 的断言，文档 :384-385 去掉「就寝反思的归属日期是刚过去的那一天」 |
| tests/unit/engineering/test_shared_model_clients.py | 删除 :13 导入；`test_plan_and_reflection_corrections_keep_tools_budgets_and_channel_echo` 改名为 `test_plan_corrections_keep_tools_budgets_and_channel_echo`，只核对零点计划；:112 起的循环只留零点计划；删去 :133 与 `life_reflection._schema_feedback` 的比较 |
| tests/unit/life/test_memory_progress_contract.py | 删除 `test_saved_reflection_is_filed_by_its_recorded_date_and_needs_one`（:142）；:184-227 六处 `public_result(...)` 加第三个参数 `{}` |
| tests/unit/life/test_result_contents.py:160、tests/integration/life/test_result_contents.py:183、:196、tests/integration/life/test_query_activity.py:458、tests/integration/life/test_app_opening.py:57、:202、tests/integration/life/test_content_start_evidence.py:60、:69、:81、:101、:108、:117 | `public_result(...)` 加第三个参数 `{}` |
| tests/integration/life/test_grocery_cooking.py:470 | `test_an_open_consumed_state_and_relief_members_change_no_prompt_layer` 改名为 `test_an_open_consumed_state_adds_only_the_kit_sentence_to_the_prompt_layers`：开始之前那次观察两份副本各层逐字节相同；做到一半那次观察，把凭状态副本的 `progress_layer` 规范 JSON 里的 `用买来的食材做一顿饭，吃完收拾。用的是家里原有的食材。` 恰替换一次为 `用买来的食材做一顿饭，吃完收拾。` 之后，各层与不凭状态的副本逐字节相同；`(groceries(consuming), groceries(plain)) == (1, 2)` 照旧 |
| tests/unit/catalog/test_manifest_version.py:37、:43 | 参数 `({"format_version": 6}, ...)` 改为 `({"format_version": 5}, "ENVIRONMENT_KIND_INVALID", "A declared kind is outside the fixed set.")`；`(manifest["rules_version"], manifest["format_version"]) == (6, 6)` |
| tests/unit/catalog/test_loader.py:84、tests/unit/catalog/test_identity.py:115 | `"format_version": 6` |
| tests/fixtures/dataset-environment/catalog/manifest.json | 见「目录格式 6 与环境包」；tests/fixtures/dayrun-{activity,callback}-baseline.json 里钉住的 `catalog_content_sha256` 随之变化，两份基线的重冻见 10 |
| tests/dataset_acceptance.py | 删除变异 `reflection_on_every_wake`、`reflection_ignores_last_decision`、`reflection_after_observation`、`reflection_filed_by_sleep_start`（:188-223）与 `unconsolidated_view_keeps_the_cursor_episode`（:1250-1254）；`step_end_copies_every_memory_again` 的杀死测试改为 `tests/integration/as2/test_worldmock_life_hooks.py::test_step_end_reads_memory_without_copying_it`；`catalog_format_version_still_four`（:291-292）改名为 `catalog_format_version_still_five`，原文 `format_version: Literal[6]`、变异 `format_version: Literal[5]`，杀死测试不变；`terminal_result_lists_no_contents`（:388-389）的原文改为 `details.update(_done_contents(_activity_rows(payload), inputs, kits))` |

**WorldMock-Dataset：**

| 测试 | 改动 |
| --- | --- |
| tests/test_environment_packages.py:219 | `assert FORMAT_VERSION == 6 and RULES_VERSION == 6` |
| tests/test_environment_packages.py:335 | `manifest["format_version"] == 6` |
| tests/test_environment_packages.py、tests/test_white_collar_package.py、tests/test_white_collar_native_day.py 的文档与跳过原因 | 「格式 5」「format 5」改为「格式 6」「format 6」，跳过判定的代码不变 |
| tests/test_white_collar_content_behavior.py | `CHANGED["office-eat-lunch"]` 改为 `{"social_relief": "low", "memory_category": "饮食"}`，注释补「白领版本 10：吃午饭标为饮食（`memory_category` 覆盖）」；摘要属于 `MUTABLE_FIELDS`，不另声明；模块文档补「格式 6 起已有内容可加 `memory_category`，同样在 CHANGED 中声明」 |

### 删除

| 文件 | 理由 |
| --- | --- |
| tests/integration/runtime/test_daily_reflection.py | 全文测睡醒反思的触发、登记与环境拒绝 |
| tests/integration/runtime/test_strict_reflection.py | 全文测严格反思工具与重试；其中 `_evening`、`_waking_decision` 移入 host_run_fixtures |
| tests/unit/life/test_daily_reflection_trigger.py | 全文测 `due_reflection` |

### 必须照旧通过

- tests/unit/footprints/test_quality.py 的反思来源测试（:426、:440、:455、:461、:474、:875、:1037）：质量报告仍按原规则认证历史运行。
- tests/unit/engineering/test_shared_primitives.py:326 `test_independent_reflection_snapshot_is_valid_across_memory_quality_and_behavior`。
- tests/unit/evaluation/test_behavior_metrics.py:312。
- tests/unit/as2/test_memory.py（测锁定 AS2 自身的存储与巩固触发，与 WorldMock 运行时无关）。
- tests/unit/life/test_shared_observation_contract.py:200 `test_content_sentence_requires_authenticated_progress_and_stage_feedback_uses_same_contract`（`app_progress` 检查排在 `kits` 检查之前）。
- 风格门禁 tests/unit/engineering/test_style_gate.py；覆盖率门禁 scripts/coverage_gate.py：删掉的代码没有残留，新增公开接口（`KitUse`、`KitTrack`、`track_kits`、`kit_phrase`、`Catalog.food_states`、`Catalog.kit_states`）都被上表的测试覆盖；删掉的公开函数（`due_reflection`、life_reflection.py 的各函数、`unconsolidated_view`）若有残留即成为未覆盖的公开接口，门禁失败；私有残留由 `test_retired_memory_paths_are_gone` 拦下。

## 不在本章

- 记忆层的形状、14,000 字预算、兜底与层序：01。
- 今天条目的形状、核验短句的其余各行、规则 B：02（短式食材包句的写法在本章，调用在 02）。
- `memory_category` 字段、类别推导、`tools/analysis/memory_categories.py`、归日规则与零点计划昨日窗口：03。
- 日、月、年回顾的调用、提示、守卫、事件、`memory_digest` 调用类、质量版本 10、`reflection_validation_max_retries` 的新语义（08-configuration.md:135、09-io-formats.md:80）：04。
- 三个 `memory_*` 工具、只读路径上的记忆工具、`_dispatch_strict_memory`、`search_memory` 与工具面：05。
- 进度层的已发布界、带入规则、续接与进度层脱钩：06（本章只要求它调用 `public_result` 时传 `kits`）。
- 人设层看法移出、层名取层、系统层规则：07。
- 行为工具对回顾正文报告的两项（相邻日字二元组 Jaccard、示例身份标记）、查看器的新规则、`compare_runs.py`、`memory_pilot.py`：09。
- 两候选基线与查看器上下文的重冻、版本与指纹汇总：10。
- 为包 0002–0005 补 `memory_category` 覆盖：提案 P4，不做。
- LabelKit-DataSet 的实际修改与验收：由它的会话执行；本章只给通知原文。
- docs/dev/ 下已冻结的旧规格（SPEC-as2-integration.md、SPEC-life-realism.md 等）引用被删测试的地方不回写（AGENTS.md「文档维护」）。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 删除 LifeAgent 的 `_dispatch_memory_llm` 后，AS2 记忆运行时必填的 `dispatch_llm` 传什么 | 传模块函数 `_refuse_model_call`，调用即记错误日志并抛 `RuntimeError("WorldMock memory runtime does not call the model")` | 不传它，`owner._dispatch_memory_llm` 会解析到 AS2 基类（person.py:237-275：`max_retries` 为 0、AS2 调用身份、无 `transport_attempts`），成为未登记的旁路；AGENTS.md 禁止 fallback |
| 写入侧还接不接受 `observation_event_id` 为空或 `consolidated` 为真的证据 | 不接受，`_memory_evidence_data` 以 `Memory update payload is invalid` 拒绝；质量报告仍接受 | 提案「P1 起不再出现 `consolidated: true`，也不再写反思；校验器继续接受历史运行里的这两种取值」：写入侧只服务新运行，认证侧服务历史运行 |
| simulation_engine/evidence/memory.py 删到什么程度 | 整个文件删除 | 文件只有 `due_reflection`；提案要求删除它和「睡醒日减一」 |
| 记忆层读反思的 `_reflections` 与 `Bedtime reflection has no filing date` | 随反思一并删除 | 提案只点名 8000 字窗口；新运行没有反思登记，新代码不能续跑旧运行（提案「指纹」行），这段代码不可达 |
| life_memory.py 里的反思来源分支 | 删除 `_REFLECTION_SOURCE`、步尾的来源过滤和快照的 `source` 键 | 新运行的存储里不会有来源为 `reflection` 的条目；`valid_subjective_snapshot` 仍为历史运行接受它 |
| 「食品状态」怎样判定 | 至少被一条 `completion_effects.food` 不为空的内容凭着的状态；食材包状态是其中 `reset_on_goods` 非空的；作为 `Catalog.food_states()`、`Catalog.kit_states()`；03 的 categories.py 调用 `catalog.food_states()`，03 第 2 节「食品状态」一条改为引用本章，不另写判定条件 | 提案「消耗食品状态」「同事聚餐的状态不是由商品重置的，不加这一句」；包 0001 的 groceries、team_dinner、detergent 分别得到「写」「不写」「不写」；03 第 2 节与本章原先各写一遍同一定义，一条规则只在一处 |
| 「活动 → 所用食材包」怎样连到活动 | 以 `consume_on_start` 唯一起因（开始受理 `action_result`）的信封 `activity_id` 为键；起因不在同一批结果里即 `IntegrityError("Grocery kit use has no start result")` | consume_on_start 正文不带活动身份（callback_contract.py:36-46），起因恰一项是现行合同（05-recording-engine.md:57-58）；受理结果与减 1 同一次提交（decision_coordinator.py:311-347） |
| 结果明细「加同一句」加在哪里 | 接在该行 `summary` 末尾，写成结果式「用的是…。」，不加新键 | `result.details` 与 `content`、`contents` 行都是闭合键集（06-agent.md:233；04-simulation-engine.md:433-438）；提案说「加同一句」，提案的完整式以「，」起头，接在以「。」结尾的摘要后要改成以「。」收尾的一句 |
| 结果明细里哪些行加 | 这项活动有食材包记录、且这一行的内容凭的正是那个状态的行 | 提案「做饭系列的各行」；系列各集凭同一状态（admission.py:251），以凭的状态判定不依赖标题 |
| 「M月D日」的日期 | 取 `reset_to_cap` 事件发生时刻在运行时区的本地日期，不补零，当天收到也写日期 | 重置与收货同一提交（callbacks.py:255-268）；提案句式固定写日期；与 `clock_phrase` 的跨日写法同一 `month_day_phrase`（02 新增，本章不另定义） |
| `public_result` 新参数要不要缺省 | 必填，没有食材包的调用方显式传 `{}` | 缺省值会让漏传的调用方静默不写食材包 |
| `activity_sentence` 的 `kits` 缺省为 None 时怎样 | interact 活动以 `IntegrityError("Action activity sentence has no grocery kit evidence")` 失败，排在 `app_progress` 检查之后 | 与现行 `app_progress` 的做法相同（settled_text.py:75-78），不静默省略 |
| 包 0001 新摘要的原文 | `在家洗菜切配，照手头的食材做一两道家常菜，同时用电饭煲煮好米饭。` | 提案给出的示例原文 |
| 包 0001 的来源账本怎样随摘要改 | 删去摘要对购物计划的那条事实和随之无人引用的来源声明，改写 `synthetic.summary`；`office-eat-lunch` 加 `synthetic.memory_category` | 购物计划只为摘要里的菜名作证（来源 notice 原文）；`unused_source_problems` 要求声明的来源都被引用；每个发布字段都要有来源（check_provenance） |
| 各包 `environment_version` 升到几 | 各加一：0001 10、0002 6、0003 7、0004 6、0005 7 | environment/README.md:18「每次重新发布都把 `environment_version` 加一」 |
| 数据集测试怎样钉住版本已升 | 断言不小于格式 6 首发的版本，不断言相等 | 以后的发布还会加一；只要求这次的升级已经发生 |
| WorldMock 测试环境的 `environment_version` | 保持 `"4"`，只改格式号并重算摘要 | 它是 WorldMock 自己的测试环境，不是数据集发布包；多处测试钉住 `"4"`（test_environment_inputs.py:88 等）；提案只要求格式号改成 6 |
| 提案未列出的格式 5 钉住 | tests/unit/catalog/test_manifest_version.py:37、:43 一并改 | 这两处同样钉住格式 5，不改则测试失败 |
| `CatalogManifest.format_version` 归哪一章 | 本章；03 只加 `memory_category` 字段 | 格式号是裁定 6 的版本后果，与包重建、夹具和 LabelKit 通知同属一处 |
| 数据集文档改哪些 | 只改陈述当前格式的句子和各包的版本、摘要、校验证据；历史版本段落不改；引擎提交〈H〉写重建所用的 WorldMock 检出的 8 位短哈希，即「工具与查看器」一步之后、已含全部生产改动的提交 | 历史段落记录的是当时的事实；提交哈希在实现时才确定 |
| 01-architecture.md:172 的链接 | 换成回顾所在模块 simulation_engine/evidence/memory_digest.py | 提案「改链到回顾所在模块」 |
| 卡片文本的读者怎样写 | 06-agent.md:35 写 `MEMORY.md` 身份段，并写日回顾的人物卡片由环境一侧构造、与它逐字相同；手册写这两个读者 | 退役后 `_persona_card_text` 只剩 `MEMORY.md` 一个读者；日回顾输入由 04 构造（`render_card(persona_card(persona))`），04 设计文档表把 :35 的文字交给本章 |
| `LifeAgent._persona_card_text` | 保留，只改文档 | `after_step` 用它渲染 `MEMORY.md` 的身份段（life_memory.py:118-120）；提案「MEMORY.md 的渲染机制不变」；07 的「不在本章」把它的删除归本章，本章不删 |
| 与 04、05 重叠的设计文档改写 | 06-agent.md:35、:311，05-recording-engine.md:100，10-runtime.md:22，01-architecture.md:63、:73、:172，docs/manual/personas.md:105 的终稿在本章；00-overview.md:138 的终稿在 04；10-runtime.md:79-91 的终稿在 05 | 04 的设计文档表与决定表（「与第 08 章重叠的设计文档改写」）把前一组交给本章、00-overview.md:138 留给自己；05 设计文档第 3 条写明它的通道段是删除 `due_reflection` 之后的终稿；同一行只有一个终稿 |
| 查看器的 `rules.reflection` | 由 09 删除并换成 `digest` 键（10 章跨章裁定：tools/run-viewer-context.js 只由 09 改） | 它只描述已退役的睡醒反思；块的 `rule` 键不引用它（run-viewer-context.js:192、:264-268） |
| `LifeAgent.step` 覆写 | 保留，只删 `_life_tick` | 它还承担失败时的完整异常日志（life_agent.py:305-309） |
| 测试怎样证明代码已删 | 以可观察行为断言（只发行动请求、通道没有 `due_reflection`、写入侧拒绝反思形状的证据），另以 `test_retired_memory_paths_are_gone` 断言退役的私有方法与常量不存在；删掉的公开函数若有残留由覆盖率门禁拦下 | 覆盖率门禁只核对公开接口（scripts/coverage_gate.py:139、:156 跳过下划线开头的名字）与 85% 的总分支阈值（:24），拦不住私有残留；06 的 `test_the_session_record_path_is_deleted` 是同一做法；不检查文件是否存在（AGENTS.md「不将历史文件清单……当作产品合同」） |
| 本章的源码变异 | 新增 `kit_sentence_left_out`、`kit_from_the_earliest_reset`，并在两处实现里保留锚定原文 | 证明食材包句与「后收到的顶掉先收到的」由测试守住 |
| 提案「消耗食品状态的系列活动」里的「系列」 | 不作条件：凡开始时用掉食材包状态的活动都写食材包句 | 系列各集凭同一状态（admission.py:251），句子按所凭状态判定；提案改写 06-agent.md:121-122 的句子「消耗食品状态的内容写出所用食材包」本身不带「系列」；包 0004 的 `home-lunch-cook` 不属于系列而用掉同一份 `groceries`，按「系列」字面会漏写 |
| 06-agent.md:121-122 的改写句 | 不照搬提案的「消耗食品状态的内容写出所用食材包和收到日期」，写成「凭食材包状态（`reset_on_goods` 非空的食品状态）的内容……运行开始以来还没收到过时写家里原有的食材」，并把 `consumes_state` 留在不进提示层的名单里 | 提案同一节「同事聚餐的状态不是由商品重置的，不加这一句」与「如果之前没有任何重置……写「，用的是家里原有的食材」」：照搬的句子会说聚餐也写、且总有日期；食材包句只写商品名与日期，不写状态名，现行句（06-agent.md:121-122）列出的 `consumes_state` 仍不进提示层 |
| 进度层与续接怎样拿到 `kits` | 按 06 的最终接口：`progress_view(context, store, published, kits)` 与 `tool_call_record(context, request, result, kits)` 带同一次观察的 `ActivityFacts.kits`；续接从同一缓存取 | 全局决定只建最终状态：06 删去进度层的结束句与 `facts` 参数后，带入记录和续接结果是开始结果、终结结果进入提示的仅有两处；同一次观察里两次取缓存之间没有提交，取到的是同一份 |
| 缓存怎样知道哪些是食材包状态 | 02 的 `agent_events` 增加参数 `states: frozenset[str]`，各调用方传冻结目录的 `kit_states()` | 缓存按足迹库对象持有、不持有目录（02【E42】）；`track_kits` 只看食材包状态，`team_dinner` 这类没有重置商品的状态若也记录，会被写成「家里原有的食材」 |
| environment/0002-social-corpus-texts | 不重建、不升版本；数据集版本测试按 `FORMAT6_FIRST_VERSIONS` 参数化 | 它只有 sources/，没有发布包（`git ls-files` 已核），作者清单只有 `environment_id` 与 `environment_version`；提案与裁定 6 只涉及包 0001–0005；`ENVIRONMENTS`（tests/test_environment_packages.py:23）按 sources/manifest.json 收集，含它 |
