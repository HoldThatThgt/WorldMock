# 人物、回合循环与只读问答的删除

> 本章属于[去掉 AgentSociety2：开发规格](../SPEC-agentsociety2-removal.md)，读法和叫法见那一页。

## 目标与依据

本章把提案「目标设计」里「人物与回合循环」「只读问答」两节落成实现。做完以后：LifeAgent 搬进 `src/worldmock/agents/life_agent.py`，每个决策新建一个实例、决定完就丢弃；它的冻结输入在运行开始时由 `persona.toml` 和运行清单一次建好；回合机制从 AgentSociety2 的 `agent_runtime.py` 与 LifeAgent 的覆写合成 WorldMock 自己的 `src/worldmock/agents/protocol/react_loop.py`；人物只经一个五方法的 `typing.Protocol` 接触引擎；只读问答整条删除。发给 DeepSeek 的每个人物回合请求，模型可见的内容逐字节不变。

下文的 `agentsociety2/…` 指锁定源码 `vendor/agentsociety2/packages/agentsociety2/agentsociety2/…`（摘要 `0a7b9b06…`，五份补丁已在其中）；`life_agent.py:N` 不带目录时指基线的 `custom/agents/life_agent.py`。行号都在基线 `74f1b949` 上逐条核对过。

实现的提案条文：

- 六「人物与回合循环」全部条目：搬迁与类名、每个决策新建、冻结输入、名字的相等测试、共用模型客户端与记忆存储、`react_loop.py` 的七项回合机制、删除的胶水、按最终键序直接构造请求字典。
- 六「只读问答」：删除整条路径；生活决策的请求字节和工具指纹不变，`prompt_sha256` 改变。
- 四「为了迁就 AgentSociety2 而存在的代码」里的三行：带结构的字符串 `HostFeedback`（`src/worldmock/agents/protocol/life_feedback.py:17-27`、`life_agent.py:143-157`）；同秒续接断链时就地改写消息列表（`src/worldmock/agents/protocol/life_continuation.py:102-108`）；每个决定都从磁盘重建人物。
- 七「哪些不变，哪些会变」表里人物一侧的三行：请求内容逐字节不变、`tool_schema_sha256` 不变、`prompt_sha256` 变。
- 十「风险」表的两行：搬回合循环时请求字节悄悄漂移；人设层的名字改从 `persona.toml` 取。

落实的裁定（[负责人裁定记录](../RULING-2026-11-17-owner.md)）：

| 裁定 | 本章的落实 |
| --- | --- |
| 2 推翻旧规定 | 人物循环由 WorldMock 自己承担，LifeAgent 不再继承 `PersonAgent` |
| 3 工作区缩成两个文件 | 人物不读写 `AGENT.json`、`config.json`、`life_prompt_snapshot.json`；卡片、名字、模型身份、回合上限改取冻结的 `persona.toml` 与运行清单 |
| 6 底稿只记交给客户端的键 | 人物回合的请求字典直接按六个键构造；删键本身与证据格式见[「模型客户端与模型请求底稿」](./04-model-client.md) |
| 7 删除只读问答 | 只读系统层、`finish` 工具、快照文件与全部只读分支删除；`prompt_sha256` 随之改变 |
| 8 原始错误直接上报 | 人物不包装异常，原样抛给运行宿主；人物自己的三个失败阶段码保留 |

依据的事实：

- 今天每个决策由 AgentSociety2 在 Ray 工作进程里依次执行 `from_workspace` → `bind_decision_context` → `step` → `to_workspace` → `close`（`agentsociety2/agent/runner.py:116-184`）。`restore` 每次读 `config.json` 与 `AGENT.json`（`agentsociety2/agent/base/agent.py:231-299`），step 编排在 `agentsociety2/agent/person.py:497-559`，回合循环在 `agentsociety2/agent/base/agent_runtime.py:514-540`、`:640-1059`。
- `agent_runtime.py` 由 failure-propagation 补丁新建，循环骨架源自上游 `670c94ff` 的 `agentsociety2/agent/base/agent.py:1574`（`run_react_loop`）、`:1744`（`_call_react_llm`）、`:1835`（`_complete_react_once`）；工具调用参数缺省 `"{}"` 的写法源自上游同一文件 `:1963`。
- LifeAgent 覆写了决定请求字节的全部钩子（`life_agent.py:143-555`）。AgentSociety2 实际贡献的字节只有：请求字典骨架与键序（`agent_runtime.py:988-996`）、assistant 消息与工具调用的形状（`:861-896`）、配对纠错的 tool 消息（`:756-780`）、消息历史的累积方式（`:826-858`）。
- 人物在决策之间不带内存状态：每次重建实例，`prepare_react_loop` 再把调用序号、上一请求和消息历史清零（`agent_runtime.py:640-650`，`life_agent.py:334-348`）；同秒续接完全由环境从证据重建（`src/worldmock/simulation_engine/decisions/continuation.py:48-76`）。
- 人设层的名字今天取自 `AGENT.json` 的 `name`（`agentsociety2/agent/base/agent.py:259`），写入时是 `_derive_name` 的 `str(profile["name"])`（`:181`、`:623-624`），`profile` 是 `persona.model_dump(mode="json")` 加几项（`src/worldmock/runtime/framework/setup.py:206-210`）；恢复校验不核对名字（`src/worldmock/runtime/lifecycle/runner.py:388-429`）。
- `MEMORY.md`「身份」段今天是 `persona_card_text(profile, name)`，即 profile 里卡片文本去掉首尾空白（`src/worldmock/agents/prompt/life_context.py:9-14`，调用于 `life_agent.py:306-308`）；profile 里的卡片文本是 `render_card(persona_card(persona))`（`setup.py:208`）。
- 只读问答的入口只在 AgentSociety2 的 `ask` 与 `query_agent_task`（`agentsociety2/agent/person.py:628`、`agentsociety2/agent/runner.py:299-343`），WorldMock 的命令行和代码都不调用；可每次生活决策照样写一份 `life_prompt_snapshot.json`（`life_agent.py:364-365`，`src/worldmock/agents/prompt/life_persona.py:68-71`）。提示指纹绑定生活与只读两份系统层（`src/worldmock/agents/prompt/life_prompt.py:208-213`）。
- 落库的模型请求是规范 JSON（键排序，`src/worldmock/foundation/serialization.py:13-16`；摘要见 `src/worldmock/simulation_engine/evidence/model.py:70`），请求字典的键序不进入库里的任何字节；响应与失败证据带来的请求必须与登记时的摘要相同（`model.py:101-116`）。

本章引用而不重述的事实：

| 事实 | 所属章 |
| --- | --- |
| 运行宿主每个决策新建 LifeAgent、按就绪顺序逐个 await、出错时中止本轮；三种启动顺序里调用 `person_inputs`；`DecisionContext`、`DirectToolCall` 搬到 `src/worldmock/state/decisions/round.py` | [「运行宿主与决策轮」](./01-run-host.md) |
| 引擎的只读查询方法、`observe` 与只读查询返回值的深拷贝、人物侧与引擎侧工具集合的一致性测试、`life_action_tool_names`/`life_query_tool_names` 的两个文档串 | [「去掉 LifeEnv：引擎门面、只读查询与提交结果」](./02-engine-facade.md) |
| `ModelClient.call(request)`、`REQUEST_KEYS`、`collect_transport_attempts`、请求底稿删去的三个键、零点计划与回顾的请求 | [「模型客户端与模型请求底稿」](./04-model-client.md) |
| 记忆存储 `WorldMockMemoryStore`、`MemoryStep`、`save_step`、`memory_update_evidence`、`MemoryUpdateEvidence` 与 `memory_updated` 的字段、人物工作区 | [「人物工作区、记忆文件与「记忆已更新」」](./05-workspace-memory.md) |
| 回合用完的英文文本、人物三个阶段码与两条日志的文本、运行宿主写下的中止原因、`status.json` 与运维日志文本 | [「故障文本」](./06-failure-text.md) |
| 来源与许可句的写法与覆盖测试、`custom/` 并入、`setup.py` 三处函数内导入与 `DELAYED_IMPORTS`、代码身份 | [「安装、代码身份、依赖与许可」](./07-install-identity.md) |
| 设计文档正文 | [「设计文档与手册」](./08-design-docs.md) |
| 测试枢纽夹具、AgentSociety2 专属测试的移植与删除、`tools/run-viewer-context.js` 的来源标签 | [「比较工具、查看器与测试迁移」](./09-tools-tests.md) |
| 录音重跑、`raw-order`、运行比对等验收 | [「实施、验收证据与完成判据」](./10-implementation.md) |

## 行为

条款编号【A1】…【A30】供「测试」一节引用。

### 人物实例

【A1】**一份定义。** LifeAgent 只定义在 `src/worldmock/agents/life_agent.py`，类名不变，不继承任何类；`custom/agents/life_agent.py` 删除。`worldmock.agents` 下任何模块都不导入 `worldmock.simulation_engine.engine`，也不导入 `worldmock.runtime` 下的任何模块。

【A2】**构造不做事。** `LifeAgent(inputs, engine, model_client, memory)` 只把四个参数存为同名公开属性，并把本次决策的内部状态置为「接口与文件」里状态表的初值；构造不读写文件，不调用引擎、模型客户端或记忆存储的任何方法。人物不持有工作区路径。`model_client` 与 `memory` 是运行宿主整次运行共用的对象（每个人物一个记忆存储），由宿主交给每个新建的 LifeAgent。

【A3】**一个实例一次决策。** 运行宿主为每个决策新建一个 LifeAgent，`await agent.decide(context)` 返回或抛出之后丢弃它；人物在决策之间没有任何内存状态，同秒续接只经观察里的续接链进来（【A21】）。宿主一侧的规则与测试见[「运行宿主与决策轮」](./01-run-host.md)。

【A4】**决策的步序。** `decide(context)` 依次：调用一次 `engine.observe(context)`，在它之前不发任何模型请求；`await run_turns(context, 观察)`；步尾保存。`run_turns(context, observation)` 把 `context` 记为本次决策的上下文，用这份观察准备六层、私有名称、容量预算与起始消息（`LifeAgent._prepare`），运行回合循环直到某一次回复登记了世界请求，返回登记的世界请求，不做步尾保存。步尾保存按[「人物工作区」](./05-workspace-memory.md)【W15】【W16】：`MemoryStep(inputs.persona_card.strip(), tuple(本决策的已核验经历), 观察事件身份, tuple(本决策的收尾记忆), context.observed_at)` 交给 `save_step(memory, step)`，再 `memory_update_evidence(context, memory, outcome)`，结果不是 `None` 时调用一次 `engine.record_memory_update(evidence)`。`decide` 返回 `None`；回合循环抛出时不做步尾保存。

【A5】**决策失败的日志。** `decide` 里抛出的任何 `Exception`，先写一条 `logging.exception("LifeAgent decision failed for agent %s", inputs.agent_id)`，再把同一个异常对象原样重新抛出，不包装、不改类型。

### 冻结输入

【A6】**PersonInputs。** 每个人物的冻结输入是 frozen dataclass `PersonInputs`，在一次运行开始时由 `person_inputs(persona, stage_identity)` 建一次，新建、普通恢复、反事实三种启动顺序一样：`agent_id = persona.agent_id`；`name = persona.name`，原字符串；`persona_card = render_card(persona_card(persona))`；`persona_card_struct = persona_card_struct(persona_card(persona))`；`model_identity = copy.deepcopy(stage_identity)`。`stage_identity` 是本运行清单里冻结的 `stage_model_identity`。三处卡片函数与今天新建运行写进 profile 的写法相同（`setup.py:205-210`）。

【A7】**开关与上限随模型身份传入。** 回合上限是 `inputs.model_identity["max_react_turns"]`；同秒续接开关是 `inputs.model_identity["stages"]["action"]["same_second_continuation"]`。两者都是冻结模型身份的成员（`setup.py:98-103`），随它一起作为构造参数传入，不另设字段。人物不读续接开关，续接与否由引擎按清单判定（`continuation.py:39-45`）。

【A8】**请求参数取自模型身份。** 请求的 `model` 是 `inputs.model_identity["requested_model"]`（今天是 AgentSociety2 客户端的模型名，取自环境变量 `AGENTSOCIETY_LLM_MODEL`，即同一个 `requested_model`，`setup.py:42`）；`max_tokens` 是 `inputs.model_identity["stages"]["action"]["max_tokens"]`；容量预检用 `inputs.model_identity["model_metadata"]`。

【A9】**名字。** 人设层由 `persona_layer(inputs.persona_card_struct, inputs.name)` 构建，已知人物由 `known_persons(inputs.persona_card_struct, …)` 构建。每个生活决策请求里人设层的 `identity.name` 等于 `persona.toml` 里 `name` 的原字符串，不去空白、不改写；新建运行和从检查点恢复的运行都一样。这就是今天 `AGENT.json` 带的值：`PersonaConfig.name` 必须非空且含非空白字符（`src/worldmock/configuration/inputs/models.py:160`），AgentSociety2 的 `_derive_name` 对它只走第一个分支，返回 `str(profile["name"])`。

### 引擎句柄

【A10】**五个方法。** 人物经 `EngineHandle`（`typing.Protocol`，定义在 `src/worldmock/agents/protocol/engine_handle.py`）接触引擎。它恰好声明五个同步方法，名字与参数名与 LifeEngine 的同名方法一致：`observe(context)`、`submit_action(call)`、`read_query(context, tool, request)`、`record_model_exchange(exchange)`、`record_memory_update(evidence)`。运行宿主把 LifeEngine 本身作为 `engine` 传给 LifeAgent；人物只调用这五个方法，调用时不 await。

### 回合循环

【A11】**回合与回合用完。** 回合号从 1 数到 `max_react_turns`，每个回合恰好一次模型调用；协议纠错回合和截断后的重试都各占一个回合。某一回合的回复通过校验、登记了至少一条世界请求，循环在这一回合结束，返回登记的世界请求。所有回合都用完而没有登记世界请求时，先写错误日志 `logging.error("LifeAgent %s used all %d model turns without a world request", inputs.agent_id, max_react_turns)`，再抛 `RuntimeError(TURNS_EXHAUSTED)`，`TURNS_EXHAUSTED` 是 `react_loop.py` 的模块常量 `"ReAct decision budget exhausted without a world request"`（文本与日志由[「故障文本」](./06-failure-text.md)【F4】规定，第 10 章「跨章裁定」）；之后不再发模型请求，也不登记世界请求。

【A12】**调用身份。** 第 t 回合的调用身份是 `f"{decision_id}:model:{t:04d}:attempt:1"`，`decision_id` 是 `context.decision_id`；三份证据的 `attempt` 恒为 1。这与今天每次调用把序号加一、`attempt` 恒为 1 的结果逐字相同（`agent_runtime.py:965-975`，`:739`）。

【A13】**每回合的工具面。** 第 t 回合的剩余回合数是 `max_react_turns - t + 1`，包括本回合。剩余 1 回合，或者本回合是截断重试（`retry_due(历史, 上一请求)` 为真），工具只有六个世界请求，按 `REQUEST_MODELS` 的次序；其余回合另加 `recall_task_summary` 与 `search_memory`，共八个，次序与今天相同（`life_agent.py:113-116`、`:296-303`）。每个工具的定义由 `_tool_schema` 生成，与今天逐字节相同。

【A14】**请求字典。** 请求字典由 `LifeAgent.turn_request` 直接按最终键序构造，恰好六个键，次序是[「模型客户端」](./04-model-client.md)的 `REQUEST_KEYS`：`model`、`messages`、`tools`、`tool_choice`、`max_tokens`、`extra_body`。`tool_choice` 平时是 `"auto"`，截断重试是 `"required"`；`extra_body` 平时是 `{"thinking": {"type": "enabled"}}`，截断重试是 `{"thinking": {"type": "disabled"}}`，都是常量的浅拷贝。今天交给 `dispatcher.call(**request)` 的字典有九个键，依次是 `model`、`messages`、`stream`、`tools`、`tool_choice`、`max_retries`、`extra_body`、`max_tokens`、`transport_max_retries`（`agent_runtime.py:988-996` 建骨架，`life_agent.py:274-279` 原位覆盖与追加）；客户端取走 `model`、`messages`、两个重试数并弹掉 `stream` 后，把其余按 `tools`、`tool_choice`、`extra_body`、`max_tokens` 的次序作为具名参数交给 litellm（`agentsociety2/config/llm_dispatcher.py:636-707`）。新字典与今天只差 `max_tokens` 和 `extra_body` 的相对次序；落库是规范 JSON，HTTP 正文由 SDK 按具名参数生成（「模型客户端」一章【M21】），这个次序不进入任何落库或发出的字节。请求里没有 `temperature`、`top_p`、`reasoning_effort`、`stream`、`max_retries`、`transport_max_retries`、`purpose`。

【A15】**每回合的消息与容量。** 每回合依次做：
- 从当前历史复制一份本回合消息列表；
- `send_check(消息, 预算)`，得到可能换过前缀的新列表与预算；
- 按【A14】构造请求，交给 `fit_request(请求, model_metadata, 预算)`；
- 预检结果的消息条数少于本回合消息条数（容量第 0 步结束了续接）时，`end_continuation(消息, 预算)` 换掉本回合消息的续接前缀，并改用不带续接的预算；
- 发出并留证的请求是预检结果里的请求；本回合结束后的新历史由本回合消息列表接上 assistant 或纠错构成，不取预检结果里的消息，所以容量浓缩过的进度层只出现在发出的请求里，不写回历史（今天同样如此：浓缩只改深拷贝，`src/worldmock/agents/prompt/prompt_capacity.py:148-153`，历史取的是原列表，`agent_runtime.py:753`、`:858`）。

【A16】**先留请求证据，再发送。** 本回合的请求证据 `ModelCallEvidence(context, call_id, "request", 1, 请求)` 在发送之前写入，不带失败发送记录。随后在 `collect_transport_attempts()` 里 `await person.model_client.call(请求)`：客户端收到的唯一参数就是写进请求证据的同一个字典。

【A17】**失败证据。** 发送抛出 `Exception`（不含 `asyncio.CancelledError` 等只属 `BaseException` 的异常）时，写一份失败证据：`phase` 为 `"failure"`，`request` 是同一个请求对象，`response` 是 `{"protocol_feedback": 报告}`，`error` 是 `"MODEL_SERVICE_FAILED: The model service failed before a response was available."`，`transport_attempts` 是收集到的失败发送；报告是 `stage_feedback("MODEL_SERVICE_FAILED", ("tool_calls",)).report.payload()`。随后把原异常原样重新抛出。原异常的 `repr` 不进证据。这与今天 LifeAgent 改写后的失败证据逐字节相同（`life_agent.py:145-150`）。发送被取消时不写失败证据。

【A18】**响应证据在解析与校验之后。** 发送成功后先解析、再校验（`LifeAgent.parse_reply`），然后写响应证据：`phase` 为 `"response"`，`request` 是本回合发出的同一个请求对象，`response` 是 `serialized_response(响应)`，有协议纠错时在它末尾追加键 `"protocol_feedback"`（值为纠错报告的 `payload()`），`error` 是纠错文字或 `None`，`transport_attempts` 是本次调用收集到的失败发送。`serialized_response` 抛出的 `TypeError` 原样上抛：不写响应证据，不改写成证据写入失败。响应证据写完之后，用本回合预检得到的 `prompt_tokens` 调用 `compare_usage`。

【A19】**证据写入失败。** 任何一份证据写入抛出 `Exception` 时，写错误日志 `Model evidence persistence failed: %s`，再 `raise stage_error("EVIDENCE_WRITE_FAILED", ("tool_calls",)) from 原异常`：异常是 `RuntimeError`，`str` 恰为 `EVIDENCE_WRITE_FAILED: Model evidence could not be persisted.`（【A23】）；之后不再发模型请求。

【A20】**接受回复。** 回复通过校验时，新历史是本回合消息加一条 assistant 消息：键依次为 `role`（`"assistant"`）、`content`（响应原值，`None` 或 `""` 不规范化）、`tool_calls`、`reasoning_content`（只在响应的 `reasoning_content` 为真值时追加在最后）。`tool_calls` 每项的键依次为 `id`（`str(id or "")`）、`type`（`"function"`）、`function`（`name` 为 `str(name or "")`，`arguments` 为 `str(arguments or "{}")`）。这两条规则只写在 `src/worldmock/state/model/exchange.py` 的 `wire_tool_call` 与 `assistant_message` 里，引擎侧从证据复原续接链的 `_final_assistant` 调用同一对函数（`continuation.py:175-179` 改为调用它们）。随后按回复中的先后执行每个调用：世界请求登记后不追加任何消息；只读查询追加一条 tool 消息，键依次为 `role`、`tool_call_id`、`content`，`content` 是 `json.dumps(结果, ensure_ascii=False, separators=(",", ":"))`，检索被拒时明细路径改写到实际调用位置（`life_agent.py:549-555` 原样搬迁）。

【A21】**续接起始消息与前缀替换。** 观察带续接链、`continuation_messages` 能组装时，本决策的起始历史就是组装出的消息，预算记下新快照消息（系统层 system 与此刻六层的 user）和链长；否则起始历史是新快照的两条消息。`send_check` 与 `end_continuation` 不改入参列表：返回 `(新消息列表, 新预算)`，新列表是新快照消息的副本接上入参从链长起的部分（替代 `life_continuation.py:102-108` 的就地改写）。断链之后本决策的每一回合都从新快照续写。

【A22】**两种纠错消息。** 回复带协议纠错时，纠错正文是 `report_content(报告)`：`json.dumps({**报告.payload(), "instruction": FEEDBACK_INSTRUCTION}, ensure_ascii=False)`，用缺省分隔符 `", "` 与 `": "`。本回合请求开启了思考（`thinking_enabled` 为真），且 `response_tool_calls(响应)` 不是 `None`（调用表是非空的列表或元组；每个调用的 `type` 是 `"function"`、`id` 是去空白后非空的字符串、`function.name` 是去空白后非空的字符串、`function.arguments` 是字符串；去空白后的 `id` 互不相同）时，新历史是本回合消息、一条按【A20】形状构造的 assistant、每个调用一条 `{"role": "tool", "tool_call_id": 调用 id, "content": 纠错正文}`。其余情况（没有工具调用、截断回合、截断重试回合、调用结构不完整）新历史是 `with_feedback(本回合消息, 纠错正文, 预算链长)`：去掉链长之后与它相同的 user 纠错，再在末尾接一条 `{"role": "user", "content": 纠错正文}`，链前缀逐字节不动。纠错写入历史之前写错误日志 `Agent %s ReAct protocol validation failed: %s`（人物编号、纠错文字）。

【A23】**协议纠错是结构。** 宿主的协议纠错是 frozen dataclass `ProtocolFeedback(report, text)`：`report` 是五字段的 `ErrorReport`，`text` 是 `f"{report.error_code}: {detail}"`。`host_feedback`、`merged_feedback`、`argument_feedback`、`truncation_feedback`、`stage_feedback` 都返回它，校验函数在没有错误时返回 `None`。证据里的 `error` 是 `text`，`protocol_feedback` 是 `report.payload()`，纠错正文由 `report` 生成；三者与今天的字节相同。阶段失败的异常由 `stage_error(code, path)` 构造：`RuntimeError(feedback.text)`，`args` 只有这一串，`repr` 与 `str` 与今天逐字相同；同一个 `ProtocolFeedback` 挂在异常的属性 `feedback` 上，带着调用位置。`HostFeedback`（`str` 子类）、`feedback_report`、`feedback_content` 删除。

【A24】**工具服务失败。** 执行一个调用时抛出的任何 `Exception`，先写 `logging.exception("LifeAgent tool dispatch failed: %s", 工具名)`，再 `raise stage_error("TOOL_SERVICE_FAILED", ("tool_calls", 调用位置)) from 原异常`：`str` 恰为 `TOOL_SERVICE_FAILED: The tool service failed before a result was available.`，`feedback.report` 的明细路径是 `["tool_calls", 调用位置]`。同一回复里先执行的调用已经生效，不回退；之后不再发模型请求。

【A25】**交出去的对象不再改动。** 回合循环交给引擎或客户端的请求字典、它的消息列表和其中每条消息，在交出之后都不再被原地改动：下一回合用新的列表，纠错与接受都构造新列表，只读查询的 tool 消息追加在本回合新建的历史列表上。

### 删除

【A26】**删除的胶水。** 下列东西在 `src` 里不再存在：`HostFeedback`、`feedback_report`、`feedback_content`；按对象身份配对容量预检结果（`_life_fitted` 与 `_usage_estimate`）；观察的包装与拆包（`_observation_record`、`_environment_observation_data`）；永远返回空的 `_validate_react_tool_names`；`description`、`init_description`；`_load_world_description`；技能、待办、决策自述前奏、追踪；`from_workspace`、`restore`、`to_workspace`、`persist_agent_json`；`life_context.py` 的 `persona_card_text`；走不到的核对：零回合报错（`_remaining_turns` 的 `ReAct decision requires 1 model turn but only … remain`）、接受回复时的两条一致性核对（`agent_runtime.py:851-856`）、`Model request evidence is unavailable`、`LLM is not initialized`、人物侧五处 `Environment is not initialized`（`life_agent.py:243`、`:395`、`:488`、`:504`、`:525`）、`Native tool call id is unavailable`（`life_agent.py:546-548`）、`LifeAgent readonly decision state is inconsistent`；没有调用者的 `_quoted`（`life_agent.py:797-799`）。

【A27】**只读问答删除。** 删除：`READONLY_SYSTEM_LAYER`、`readonly_messages`、`empty_memory_layer`、`TOOL_DESCRIPTIONS["finish"]`、`system_message` 的 `readonly` 参数（`life_prompt.py:121-131`、`:143`、`:151-159`、`:200-205`）；`PROMPT_SNAPSHOT_FILE`、`write_prompt_snapshot`、`readonly_layers`（`life_persona.py:25-26`、`:68-81`）；`_ToolRequest`、`ReadonlyFinishRequest` 与 LifeAgent 的全部只读分支（`life_agent.py:90-108`、`:299-300`、`:329-331`、`:340-344`、`:402-403`、`:447-448`、`:483-484`）。人物不再写 `life_prompt_snapshot.json`。

【A28】**提示指纹。** `life_prompt_sha256()` 是 `json.dumps({"life": LIFE_SYSTEM_LAYER, "layer_schemas": LAYER_SCHEMAS}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 的 UTF-8 字节的 SHA-256，值为 `cd454d1b18c8a211d8eeb0bb25aaba7ff2ef4b0732c9a2bd7aaa4385a5f67842`；基线值是 `1947c112bf46fd262cc159d4163a3831a0aa55d42c70ad7645ffd3ba40524a05`。运行清单 `model_identity.prompt_sha256` 随之改变。

【A29】**不变的字节。** `LIFE_SYSTEM_LAYER` 与 `LAYER_SCHEMAS` 一个字节不改：`system_message()["content"]` 的 SHA-256 是 `6f51d166a9f94a3fc12ff49f461c30ef76250a9458bace868d4a96f67f2cb5db`。`life_tool_schema_sha256()` 仍是 `aa0ed7fa01f00558ac94d6e25e71a36d983f25dbb06834c2c31911cffec5bd7e`（只取八个原生工具，删去的 `finish` 用途句本来就不在其中）。`TOOL_DESCRIPTIONS` 恰为八个工具的用途句，次序同 `REQUEST_MODELS` 加 `search_memory`。

### 来源

【A30】**来源与许可。** `src/worldmock/agents/protocol/react_loop.py` 与 `src/worldmock/state/model/exchange.py` 含从锁定源码移植的代码（循环骨架、调用身份、响应序列化、完整调用的判定与配对纠错、assistant 与工具调用的序列化），两者模块文档串的最后一段是[「安装、代码身份、依赖与许可」](./07-install-identity.md)【I24】的来源句，单独一行：`来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。`。本章其余文件是否另带这一句，由该章的来源核对判定。

## 接口与文件

### 文件

| 路径 | 动作 | 内容 |
| --- | --- | --- |
| `custom/agents/life_agent.py` | 删除 | 内容按下文「旧位置的去向」搬进新文件或删除 |
| `src/worldmock/agents/life_agent.py` | 新建 | `ACTION_THINKING`、`ACTION_TOOL_CHOICE`、`ACTION_MAX_TOKENS`、`PersonInputs`、`person_inputs`、`LifeAgent`，工具常量与工具函数、名称校验函数（原样搬迁的部分见去向表）；模块文档串重写为：一次决策一个实例，观察、回合循环、步尾三段，工具面与批次形状，名称校验与环境共用，截断重试，回合机制在 `react_loop.py`；不点名 AgentSociety2、`PersonAgent`、`LifeEnv` |
| `src/worldmock/agents/protocol/react_loop.py` | 新建 | `TURNS_EXHAUSTED`、`ReactDecision`、`ReactToolResult`、`model_call_id`、`serialized_response`、`response_tool_calls`、`accepted_assistant`、`ReactLoop`；带来源句（【A30】） |
| `src/worldmock/agents/protocol/engine_handle.py` | 新建 | `EngineHandle` |
| `src/worldmock/agents/protocol/life_feedback.py` | 修改 | 删 `HostFeedback`（`:17-27`）、`feedback_report`（`:58-63`）、`feedback_content`（`:71-73`）；新增 `ProtocolFeedback`、`stage_feedback`、`stage_error`；`host_feedback`（`:38-41`）、`merged_feedback`（`:44-50`）、`argument_feedback`（`:53-55`）改为返回 `ProtocolFeedback`，函数体其余不变；`report_content`、`FEEDBACK_INSTRUCTION`、`HostIssue` 不变 |
| `src/worldmock/agents/protocol/life_truncation.py` | 修改 | 导入改为 `ProtocolFeedback, host_feedback, report_content`（`:7`）；`truncation_feedback() -> ProtocolFeedback`（`:39-41`）；其余逐字节不变 |
| `src/worldmock/agents/protocol/life_continuation.py` | 修改 | `send_check`（`:89-99`）、`end_continuation`（`:102-108`）改为返回 `(新列表, 新预算)`、不改入参；`end_continuation` 文档串改为「提前结束本秒续接：返回把链前缀换成新快照消息的新列表与不带续接的预算，入参不变」，`_earlier_changes` 文档串（`:114`）的「宿主或 AS2 写的」改为「宿主写的」；其余不变 |
| `src/worldmock/agents/prompt/life_prompt.py` | 修改 | 删【A27】所列；`system_message() -> dict[str, str]` 只渲染 `LIFE_SYSTEM_LAYER`；`life_prompt_sha256` 的模板只剩 `life` 与 `layer_schemas`，文档串改为「提示指纹：绑定生活系统层与七层 Schema」；删 `assemble_layer` 的导入（`:19`）；模块文档串去掉只读问答（`:1` 的「生活与只读系统层」改为「生活系统层」，删 `:5-6` 的「只读问答的 user …原样给出。」）；`LIFE_SYSTEM_LAYER` 与其余八个用途句一个字节不改 |
| `src/worldmock/agents/prompt/life_persona.py` | 修改 | 删【A27】所列与 `Path`、`empty_memory_layer`、`IntegrityError` 的导入；`persona_layer(card, name)`、`known_persons(card, relationship_names)`、`card_struct(card)` 改为直接收结构化卡片的映射；`card_struct` 只做 `PersonaCardStruct.model_validate(card)`，删去 `Persona card structure is required` 分支；`persona_layer` 的 `@param profile` 改为 `@param card 结构化人物卡片`；模块文档串删去只读问答一句（`:6`）；人设层的构建与汉字预算逐字节不变 |
| `src/worldmock/agents/prompt/life_context.py` | 删除 | 唯一的调用者 `_persona_card_text` 删除；去首尾空白移到步尾（【A4】），「Persona card text is required」走不到（卡片文本恒有七行固定文字） |
| `src/worldmock/agents/prompt/prompt_schemas.py` | 修改 | 只改注释 `:164` 为「「可查」：能查到的最早日期；还没有可查的日期时是空对象。」；`LAYER_SCHEMAS` 的值不变 |
| `src/worldmock/state/model/exchange.py` | 修改 | 新增 `ModelCallEvidence`、`wire_tool_call`、`assistant_message`；`:17` 注释改为「行动请求才开放的世界请求工具（start_main_activity）。」；`is_action_request` 文档串（`:25-26`）删去「只读问答、记忆巩固与反思不带 start_main_activity」；带来源句（【A30】） |
| `src/worldmock/simulation_engine/decisions/continuation.py` | 修改 | `_final_assistant` 的组装（`:175-179`）改为 `assistant_message(message.get("content"), [wire_tool_call(call.get("id"), function["name"], function.get("arguments")) for call, function in zip(calls, functions, strict=True)], message["reasoning_content"])`，`:163-174` 的形状判定不变；注释 `:34-35` 改为「构造消息时的键序（tool 消息为 role、tool_call_id、content；assistant 为 role、content、tool_calls、reasoning_content；system 与 user 为 role、content）」，文档串 `:138` 的「宿主与 AS2 构造消息的键序固定」改为「宿主构造消息的键序固定」，`:148` 的「形状与 AS2 回传的工具调用相同」改为「形状同 exchange.wire_tool_call」，`:161` 的「与 AS2 回传的形状与键序相同」改为「经 exchange.assistant_message，与人物回合循环接受回复时的形状与键序相同」 |
| `src/worldmock/recording_engine/storage/store.py` | 修改 | 只改文档串 `:328`：「零点计划、工作评审与只读问答都不算」改为「零点计划与工作评审都不算」 |
| `src/worldmock/runtime/framework/setup.py` | 修改（三处导入的写法由「安装、代码身份」一章【I13】规定） | 本章只保证 `ACTION_THINKING`、`ACTION_TOOL_CHOICE`、`ACTION_MAX_TOKENS`、`life_tool_schema_sha256` 四个名字与值都在 `worldmock.agents.life_agent` |

搬进 `src` 的代码里，注释与文档串凡点名 AgentSociety2、AS2、`PersonAgent`、`LifeEnv`、`finish` 或只读问答的句子，改为点名新的承担者（引擎、回合循环、记忆存储）或删去（「安装、代码身份」一章【I26】）；函数体只按去向表改。

### PersonInputs 与 LifeAgent（`src/worldmock/agents/life_agent.py`）

```python
ACTION_THINKING = {"thinking": {"type": "enabled"}}     # 名字与值同 life_agent.py:80
ACTION_TOOL_CHOICE = "auto"                              # 同 :81
ACTION_MAX_TOKENS = 16000                                # 同 :84

@dataclass(frozen=True, slots=True)
class PersonInputs:
    agent_id: int                        # 人物编号，persona.agent_id
    name: str                            # persona.toml 的 name 原字符串
    persona_card: str                    # render_card(persona_card(persona))，与今天 profile 里的卡片文本相同
    persona_card_struct: dict[str, Any]  # persona_card_struct(persona_card(persona))，人设层与已知人物用
    model_identity: dict[str, Any]       # 清单 stage_model_identity 的深拷贝，含回合上限与同秒续接开关

def person_inputs(persona: PersonaConfig, stage_identity: Mapping[str, Any]) -> PersonInputs: ...

class LifeAgent:
    def __init__(self, inputs: PersonInputs, engine: EngineHandle, model_client: ModelClient,
                 memory: WorldMockMemoryStore) -> None: ...
    async def decide(self, context: DecisionContext) -> None: ...
    async def run_turns(self, context: DecisionContext, observation: dict[str, Any]) -> list[ReactDecision]: ...
    def turn_tools(self, remaining: int, retry: bool) -> list[dict[str, Any]]: ...
    def turn_request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                     retry: bool) -> dict[str, Any]: ...
    def parse_reply(self, response: Any) -> tuple[list[ReactDecision], ProtocolFeedback | None]: ...
    def execute_decisions(self, decisions: list[ReactDecision]) -> tuple[list[ReactDecision], list[dict[str, Any]]]: ...
```

- `ACTION_*` 上方的注释照搬 `life_agent.py:79`、`:82-83`，只把「只读问答与截断后的重试用同一个数」改为「截断后的重试用同一个数」。
- `ModelClient` 从 `worldmock.configuration.model.model_client` 导入，`WorldMockMemoryStore` 从 `worldmock.agents.memory.life_memory_store` 导入，`MemoryStep`、`save_step`、`memory_update_evidence` 从 `worldmock.agents.memory.life_memory` 导入（形状见[「人物工作区」](./05-workspace-memory.md)）。
- `decide`：`try:` 里 `observation = self.engine.observe(context)`；`await self.run_turns(context, observation)`；`self._record_memory(context)`。`except Exception:` 按【A5】记日志后 `raise`。
- `run_turns`：`self._context = context`；`budget, history = self._prepare(observation)`；`return await ReactLoop(self, context, budget, history).run()`。
- `turn_tools`：`queries = _QUERY_TOOL_MODELS if remaining > 1 and not retry else {}`；把 `{**_WORLD_TOOL_MODELS, **queries}` 存进 `_turn_models`，返回 `[_tool_schema(name, model) for name, model in self._turn_models.items()]`。
- `turn_request` 返回 `{"model": identity["requested_model"], "messages": messages, "tools": tools, "tool_choice": RETRY_TOOL_CHOICE if retry else ACTION_TOOL_CHOICE, "max_tokens": identity["stages"]["action"]["max_tokens"], "extra_body": dict(RETRY_THINKING if retry else ACTION_THINKING)}`，`identity` 是 `self.inputs.model_identity`，键序就是字面量的次序。
- `parse_reply`：`decisions, feedback = self._parse(response)`（今天的 `_parse_react_responses` 去掉只读与 `finish` 分支）；`feedback` 是 `None` 时 `feedback = self._validate(decisions)`（今天的 `validate_react_decisions` 去掉只读参数与核对）；返回 `(decisions, feedback)`。
- `execute_decisions`：按先后对每个决策调用 `_dispatch_decision(decision)`（工具服务失败按【A24】），`_tool_message(decision, result)` 不是 `None` 时收进消息表，世界请求收进登记表；登记表非空时 `self._memories = _conclusion_memories(登记表[0].action, 登记表[0].args)`；返回 `(登记表, 消息表)`。
- `_record_memory(context)`：`step = MemoryStep(self.inputs.persona_card.strip(), tuple(self._verified_experiences), self._observation_event_id, tuple(self._memories), context.observed_at)`；`outcome = save_step(self.memory, step)`；`evidence = memory_update_evidence(context, self.memory, outcome)`；`evidence` 不是 `None` 时 `self.engine.record_memory_update(evidence)`。同步执行。
- `_prepare(observation) -> tuple[ProgressBudget, list[dict[str, Any]]]`，步序与今天 `_prepare_life_action_state` 相同：`self._prepare_verified_experiences(observation)`；`_visible_wake_reasons(observation)`（只核对形状，不存结果）；`persona = persona_layer(self.inputs.persona_card_struct, self.inputs.name)`；`layers = life_user_layers(observation, persona)`；`budget = ProgressBudget(tuple(layers), tuple(_progress_meta(observation, last_progress_layer(layers))))`；`self._names = {**_visible_names(observation), "known_persons": known_persons(self.inputs.persona_card_struct, _relationship_persons(observation))}`；`fresh = [system_message(), user_message(layers)]`；`continued = continuation_messages(observation.get(CONTINUATION_KEY), layers)`；`continued` 不是 `None` 时返回 `(replace(budget, fresh_messages=tuple(fresh), chain_length=len(continued)), continued)`，否则返回 `(budget, fresh)`。不写快照文件。
- `_conclusion_memories(action, args)`：`action` 不是 `conclude_activity` 时返回 `[]`；否则 `request = _validate_request(ConcludeActivityRequest, args)`，返回 `[memory for item in control_items(action, request) for memory in item.memories]`，即 `control_items` 读出的存储形状原样（`src/worldmock/state/interaction/control_items.py:106-110`）；来源、认知状态与去掉 `subject` 都归记忆存储（「人物工作区」一章【W8】）。文档串照此改写。

本次决策的内部状态，`__init__` 置初值：

| 属性 | 初值 | 写入处 | 今天的对应 |
| --- | --- | --- | --- |
| `_context` | `None` | `run_turns` 开头 | AgentSociety2 的 `_decision_context` |
| `_names` | `{}` | `_prepare` | `_life_names` |
| `_verified_experiences` | `[]` | `_prepare_verified_experiences` | `_life_verified_experiences` |
| `_observation_event_id` | `""` | `_prepare_verified_experiences` | `_life_observation_event_id` |
| `_turn_models` | `{}` | `turn_tools` | `_life_request_models` |
| `_action_count` | `0` | `_submit_life_action` | `_life_action_count` |
| `_world_calls` | `0` | `_validate` | `_life_world_calls` |
| `_memory_searches` | `0` | `_search_memory` | `_life_memory_searches` |
| `_call_positions` | `{}` | `_validate` | `_life_call_positions` |
| `_memories` | `[]` | `execute_decisions` | `_last_finish_memories` |

### 回合循环（`src/worldmock/agents/protocol/react_loop.py`）

```python
TURNS_EXHAUSTED = "ReAct decision budget exhausted without a world request"

@dataclass(frozen=True, slots=True)
class ReactDecision:
    action: str            # 工具名
    args: dict[str, Any]   # 已解析的参数对象
    tool_call_id: str      # 供应商给出的调用身份

@dataclass(frozen=True, slots=True)
class ReactToolResult:
    ok: bool               # 查询是否成功（检索带 error_code 时为假）
    data: dict[str, Any]   # 结构化结果

def model_call_id(decision_id: str, turn: int) -> str: ...
def serialized_response(response: Any) -> dict[str, Any]: ...
def response_tool_calls(response: Any) -> list[dict[str, Any]] | None: ...
def accepted_assistant(response: Any) -> dict[str, Any]: ...

class ReactLoop:
    def __init__(self, person: LifeAgent, context: DecisionContext, budget: ProgressBudget,
                 history: list[dict[str, Any]]) -> None: ...
    async def run(self) -> list[ReactDecision]: ...
```

- `LifeAgent` 只在 `if TYPE_CHECKING:` 下导入；运行期 `react_loop` 不导入 `life_agent`，`life_agent` 在运行期导入 `react_loop`。
- `model_call_id` 返回 `f"{decision_id}:model:{turn:04d}:attempt:1"`。
- `serialized_response` 搬迁 `agent_runtime.py:1042-1059`：有 `model_dump` 时取 `model_dump(mode="json")`，是 `Mapping` 时取 `dict(response)`，否则写错误日志并抛 `TypeError("Model response is not serializable")`；结果不是 `dict` 时写错误日志并抛 `TypeError("Model response payload must be an object")`。两句日志与异常文字同为这两句英文。
- `response_tool_calls` 搬迁 `agent_runtime.py:782-808`，成立时返回各调用经 `wire_tool_call` 的结果。
- `accepted_assistant` 取 `choices[0].message`（`choices` 为空时为 `None`），返回 `assistant_message(getattr(message, "content", None), [wire_tool_call(...) for 每个调用], getattr(message, "reasoning_content", None))`，调用表取 `getattr(message, "tool_calls", None) or []`。
- `ReactLoop` 的状态：`_person`、`_context`、`_budget`、`_history`、`_last_request`（初值 `{}`）。方法：

| 方法 | 做什么 | 今天的来源 |
| --- | --- | --- |
| `run()` | `limit = person.inputs.model_identity["max_react_turns"]`；回合号 t 从 1 到 `limit` 依次 `await _turn(t, limit - t + 1)`；返回首个非空登记表；用完按【A11】：`logging.error("LifeAgent %s used all %d model turns without a world request", person.inputs.agent_id, limit)`，`raise RuntimeError(TURNS_EXHAUSTED)` | `agent_runtime.py:514-540` |
| `_turn(turn, remaining)` | `retry = retry_due(self._history, self._last_request)`；`tools = person.turn_tools(remaining, retry)`；`messages, fitted = _request(list(self._history), tools, retry)`；`call_id = model_call_id(context.decision_id, turn)`；`response, attempts = await _send(call_id, fitted.request)`；`decisions, feedback = person.parse_reply(response)`；`_record_response(call_id, fitted, response, feedback, attempts)`；有纠错时记【A22】的日志、`self._history = _corrected(messages, response, feedback)`、返回 `[]`；否则 `self._history = [*messages, accepted_assistant(response)]`，`registered, tool_messages = person.execute_decisions(decisions)`，`self._history.extend(tool_messages)`，返回 `registered` | `agent_runtime.py:727-754`，`life_agent.py:441-461` |
| `_request(messages, tools, retry)` | `messages, self._budget = send_check(messages, self._budget)`；`request = person.turn_request(messages, tools, retry)`；`fitted = fit_request(request, person.inputs.model_identity["model_metadata"], self._budget)`；`len(fitted.request["messages"]) < len(messages)` 时 `messages, self._budget = end_continuation(messages, self._budget)`；`self._last_request = fitted.request`；返回 `(messages, fitted)` | `life_agent.py:256-285` |
| `_send(call_id, request)` | 【A16】【A17】；成功时返回 `(response, tuple(attempts))` | `agent_runtime.py:931-963`，`life_agent.py:145-146` |
| `_record_response(call_id, fitted, response, feedback, attempts)` | 【A18】：证据的 `request` 是 `fitted.request`，`compare_usage` 用 `fitted.prompt_tokens`；五个参数，合风格门禁 | `agent_runtime.py:1014-1039`，`life_agent.py:147-157` |
| `_record(evidence)` | `person.engine.record_model_exchange(evidence)`，失败按【A19】 | `agent_runtime.py:998-1012`，`life_agent.py:151-155` |
| `_corrected(messages, response, feedback)` | 【A22】；判定思考读 `self._last_request`，链长读 `self._budget.chain_length` | `agent_runtime.py:756-780`，`life_agent.py:206-218` |

### 引擎句柄（`src/worldmock/agents/protocol/engine_handle.py`）

```python
class EngineHandle(Protocol):
    def observe(self, context: DecisionContext) -> dict[str, Any]: ...
    def submit_action(self, call: DirectToolCall) -> ActionReceipt: ...
    def read_query(self, context: DecisionContext, tool: str, request: BaseModel) -> dict[str, Any]: ...
    def record_model_exchange(self, exchange: ModelCallEvidence) -> None: ...
    def record_memory_update(self, evidence: MemoryUpdateEvidence) -> None: ...
```

每个方法体只有一句中文 `@brief` 文档串（不写 `...`）。`DecisionContext`、`DirectToolCall` 从 `worldmock.state.decisions.round` 导入，`ActionReceipt` 从 `worldmock.state.decisions.results`，`ModelCallEvidence` 从 `worldmock.state.model.exchange`，`MemoryUpdateEvidence` 从 `worldmock.recording_engine.events.memory_evidence`（「人物工作区」一章定义；`worldmock.agents` 可以导入 `recording_engine`，第 10 章「跨章裁定」），`BaseModel` 从 `pydantic`。

### 证据与共用的消息形状（`src/worldmock/state/model/exchange.py`）

```python
@dataclass(frozen=True, slots=True)
class ModelCallEvidence:
    context: DecisionContext                           # 本次决策上下文
    call_id: str                                       # model_call_id 给出的调用身份
    phase: Literal["request", "response", "failure"]   # 证据阶段
    attempt: int                                       # 恒为 1
    request: dict[str, Any]                            # 本回合发出的请求字典（同一对象）
    response: dict[str, Any] | None = None             # 序列化响应；失败时只有 protocol_feedback
    error: str | None = None                           # 协议纠错或失败阶段的文字
    transport_attempts: tuple[dict[str, Any], ...] = ()  # 结果之前失败的发送；请求证据恒为空

def wire_tool_call(identity: Any, name: Any, arguments: Any) -> dict[str, Any]:
    # {"id": str(identity or ""), "type": "function",
    #  "function": {"name": str(name or ""), "arguments": str(arguments or "{}")}}

def assistant_message(content: Any, tool_calls: list[dict[str, Any]], reasoning: Any) -> dict[str, Any]:
    # {"role": "assistant", "content": content, "tool_calls": tool_calls}，reasoning 为真值时追加 "reasoning_content"
```

`ModelCallEvidence` 的字段名、次序与缺省值同 `agentsociety2/agent/base/react.py:73-94`，引擎的 `record_model_exchange` 按属性读取，不改。`MemoryUpdateEvidence` 不在这个模块：它与 `MEMORY_UPDATE_KEYS`、`_memory_evidence_data` 一起定义在 `src/worldmock/recording_engine/events/memory_evidence.py`，字段由「人物工作区」一章规定。

### 协议纠错（`src/worldmock/agents/protocol/life_feedback.py`）

```python
@dataclass(frozen=True, slots=True)
class ProtocolFeedback:
    report: ErrorReport   # 五字段错误
    text: str             # 写进证据 error 的英文文字："<error_code>: <detail>"

def host_feedback(report: ErrorReport, detail: str) -> ProtocolFeedback: ...
def stage_feedback(code: str, path: tuple) -> ProtocolFeedback: ...
def stage_error(code: str, path: tuple) -> RuntimeError: ...
```

- `host_feedback` 照旧先写错误日志 `Life tool protocol rejected: %s: %s`，再返回 `ProtocolFeedback(report, f"{report.error_code}: {detail}")`。
- `stage_feedback` 搬迁 `life_agent.py:558-563`，去掉从未传值的 `detail` 参数：`report = host_report(code)`；`item = ErrorItem(path, None, code, expected=report.fix, why=report.reason)`；返回 `host_feedback(replace(report, items=(item,)), report.reason)`。三个阶段码的文字（`src/worldmock/state/interaction/errors.py:37-42`）不变：`MODEL_SERVICE_FAILED: The model service failed before a response was available.`、`EVIDENCE_WRITE_FAILED: Model evidence could not be persisted.`、`TOOL_SERVICE_FAILED: The tool service failed before a result was available.`。
- `stage_error`：`feedback = stage_feedback(code, path)`；`error = RuntimeError(feedback.text)`；`error.feedback = feedback`；返回 `error`。调用处写 `raise stage_error(...) from 原异常`。

### 续接前缀（`src/worldmock/agents/protocol/life_continuation.py`）

```python
def send_check(messages: list[dict], budget: ProgressBudget) -> tuple[list[dict], ProgressBudget]: ...
def end_continuation(messages: list[dict], budget: ProgressBudget) -> tuple[list[dict], ProgressBudget]: ...
```

`end_continuation` 返回 `([*(dict(item) for item in budget.fresh_messages), *messages[budget.chain_length:]], replace(budget, fresh_messages=None, chain_length=0))`。`send_check` 的判定、日志与报错文字不变，只是返回二元组：不带续接时返回 `(messages, budget)`；本决策内的 assistant 缺思考时照旧写日志 `An assistant turn of the current decision has no reasoning_content` 并抛 `RuntimeError("An assistant turn of the current decision is missing reasoning_content")`；链中缺思考时照旧写警告并返回 `end_continuation(messages, budget)`；链中不缺时返回 `(messages, budget)`。

### 旧位置的去向

| 基线位置 | 去向 |
| --- | --- |
| `life_agent.py:16-77` 导入 | 按新内容重写；不再导入 `agentsociety2`、`custom`、`life_context` |
| `life_agent.py:79-84` 三个 `ACTION_*` 常量 | 新 `life_agent.py`，名字与值不变，注释见上文 |
| `life_agent.py:86-87` `_CONCLUSION_MEMORY_SOURCE` | 删除（来源归记忆存储） |
| `life_agent.py:111-129` 工具常量、`MEMORY_SEARCHES_PER_DECISION`、`life_action_tool_names`、`life_query_tool_names` | 新 `life_agent.py`，函数体不变；两个函数的文档串按「去掉 LifeEnv」一章 |
| `life_agent.py:90-108` `_ToolRequest`、`ReadonlyFinishRequest` | 删除 |
| `life_agent.py:132-133` `class LifeAgent(PersonAgent)` 与类文档串 | `class LifeAgent:`，文档串「一次决策的生活人物：观察、回合循环与步尾」 |
| `life_agent.py:135-136`、`:159-167` `_life_fitted`、`_usage_estimate` | 删除；预检结果由 `ReactLoop._turn` 的局部变量持有 |
| `life_agent.py:138-141`、`:220-230` `description`、`init_description`、`_load_world_description` | 删除 |
| `life_agent.py:143-157` `_record_model_evidence` | `ReactLoop._send`、`_record_response`、`_record` |
| `life_agent.py:169-177` `_validate_react_tool_names` | 删除 |
| `life_agent.py:179-183` `_protocol_feedback_content` | 删除；直接用 `report_content(feedback.report)` |
| `life_agent.py:185-203` `_parse_react_responses` | `LifeAgent._parse(response)`，删去 `readonly` 参数与 `finish` 分支，`bind_report(feedback_report(error), index)` 改为 `bind_report(error.report, index)`，返回 `ProtocolFeedback | None` |
| `life_agent.py:206-218` `_append_react_protocol_feedback` | `ReactLoop._corrected` |
| `life_agent.py:232-238` `step` | `LifeAgent.decide`，日志不变 |
| `life_agent.py:240-245` `_observe_environment` | `decide` 里的 `self.engine.observe(context)` |
| `life_agent.py:247-254`、`:296-303` `build_react_tool_schemas`、`_request_tool_models` | `LifeAgent.turn_tools` |
| `life_agent.py:256-285` `_model_request` | `LifeAgent.turn_request` 与 `ReactLoop._request` |
| `life_agent.py:287-294` `_remaining_turns` | 删除；剩余回合数由回合号算出 |
| `life_agent.py:306-308` `_persona_card_text` | 删除；步尾 `MemoryStep` 的身份段取 `inputs.persona_card.strip()` |
| `life_agent.py:310-320`、`:572-579` `_observation_record`、`_environment_observation_data` | 删除 |
| `life_agent.py:322-325` `_new_memory_runtime` | 删除 |
| `life_agent.py:327-332` `build_react_messages` | 删除；新快照消息在 `_prepare` 里构造 |
| `life_agent.py:334-370` `prepare_react_loop`、`_prepare_life_action_state` | 状态初值进 `__init__`，其余进 `_prepare`（不写快照） |
| `life_agent.py:372-385` `_prepare_verified_experiences` | 不变，写 `_verified_experiences`、`_observation_event_id` |
| `life_agent.py:387-398` `_record_memory_update` | `LifeAgent._record_memory` |
| `life_agent.py:400-416` `validate_react_decisions` | `LifeAgent._validate(decisions)`，`feedback_report(x)` 改为 `x.report`，成功时返回 `None` |
| `life_agent.py:419-439` `_decision_error`、`_prepared_action_error` | 不变，返回 `ProtocolFeedback | None`；`_prepared_action_error` 文档串删去「、finish」 |
| `life_agent.py:441-461` `_run_react_turn` | `LifeAgent.execute_decisions` 与 `ReactLoop._turn`；不再向观察列表追加记录 |
| `life_agent.py:463-471` `_execute_react_tool` | `LifeAgent._dispatch_decision(decision)`，按【A24】 |
| `life_agent.py:473-537` `dispatch_react_tool`、`_search_memory`、`_submit_life_action`、`_require_open_decision` | `LifeAgent._dispatch(action, args)`、`_search_memory(args)`、`_submit_life_action(action, args)`、`_require_open_decision()`，全部同步；删去只读参数与环境为空的核对；`self._env.…` 换成 `self.engine.…`，不 await；`require_decision_context()` 换成 `self._context`；`ReactToolResult` 只带 `ok`、`data`；文档串里的 `LifeEnv` 换成引擎 |
| `life_agent.py:539-555` `_append_react_tool_result` | `LifeAgent._tool_message(decision, result) -> dict | None`：世界请求返回 `None`，删去走不到的 `Native tool call id is unavailable`（`:546-548`），其余原样，返回 tool 消息而不追加 |
| `life_agent.py:558-563` `_stage_feedback` | `life_feedback.stage_feedback` 与 `stage_error` |
| `life_agent.py:566-569`、`:582-806` 请求校验、观察核对与名称校验函数 | 新 `life_agent.py`，函数体不变，只把返回值 `""` 换成 `None`、`HostFeedback` 换成 `ProtocolFeedback`、`feedback_report(x)` 换成 `x.report`；`_batch_error` 删去 `or item.action == "finish"` 与文档串里的「（或 finish）」 |
| `life_agent.py:623-635` `_conclusion_memories` | 按上文改为原样返回 `control_items` 的存储形状记忆 |
| `life_agent.py:797-799` `_quoted` | 删除 |
| `life_agent.py:813-842` `_tool_schema`、`life_tool_schema_sha256`、`_combine_reports` | 新 `life_agent.py`，不变（`_combine_reports` 返回 `ProtocolFeedback`） |
| `life_agent.py:845-889` `_parse_model_calls`、`_parse_arguments` | 新 `life_agent.py`，构造 `ReactDecision(name, parsed, identity)` |

### 测试里旧入口的对应

现有测试按下表改写调用，断言主体不改（测试文件清单见「测试」一节与「比较工具」一章）：

| 旧写法 | 新写法 |
| --- | --- |
| `LifeAgent()` 再逐个设 `_id`、`_name`、`_profile`、`_env`、`_dispatcher`、`_model_name`、`_decision_context`、`_max_react_turns` | `LifeAgent(person_inputs(persona, stage_identity), engine, model_client, memory)` |
| `await agent.run_react_loop(ReactLoopRequest(0, t, [{"turn": 0, "action": "observe", "ok": True, "data": observed}]))` | `await agent.run_turns(context, observed)` |
| `agent.prepare_react_loop(request)`、`agent.build_react_messages(request)` | `budget, history = agent._prepare(observed)` |
| `agent.build_react_tool_schemas(readonly=False)` | `agent.turn_tools(remaining, retry)` |
| `agent._parse_react_responses(response)` | `agent._parse(response)` |
| `agent.validate_react_decisions(decisions, False)`，没有错误时 `== ""` | `agent._validate(decisions)`，没有错误时 `is None` |
| `feedback_report(x)` | `x.report` |
| `feedback_content(x)`、`LifeAgent._protocol_feedback_content(x)` | `report_content(x.report)` |
| `await agent.dispatch_react_tool(action, args)` | `agent._dispatch(action, args)` |
| `ReactDecision("", action, args, "", call_id)`、`ReactDecision("", action, args, "")` | `ReactDecision(action, args, call_id)`、`ReactDecision(action, args, "")` |
| `failure.value.args[0].report` | `failure.value.feedback.report` |
| `system_message(False)` | `system_message()` |
| `persona_layer(profile, name)`、`known_persons(profile, names)` | `persona_layer(profile["persona_card_struct"], name)`、`known_persons(profile["persona_card_struct"], names)` |
| `empty_memory_layer()` | `assemble_layer(None, {}, [])` |
| 桩模型 `async def call(self, **request)` | `async def call(self, request)` |
| `agent._last_finish_memories` | `agent._memories`（初值 `[]`，不再是 `None`） |
| `agent._react_messages` | 回合循环结束时的消息历史：人物不保存它，用例经「比较工具」一章【T37】的测试辅助 `turns_with_history` 取得 |

### 逐字节不变的东西

| 对象 | 依据 |
| --- | --- |
| 每个人物回合请求里模型可见的值：系统层、六层 user、续接消息、assistant、tool、纠错消息、工具定义、`tool_choice`、`extra_body`、`max_tokens`、`model` | 【A8】【A13】【A14】【A15】【A20】【A21】【A22】；线上正文另需「模型客户端与模型请求底稿」一章保证 |
| `call_id`、`action_id`（`{decision_id}:action:{n:04d}`） | 【A12】；`_submit_life_action` 原样搬迁 |
| `model_response` 事件的 `response`、`error`、`protocol_feedback`、`transport_attempts` 与失败事件 | 【A17】【A18】【A23】 |
| 阶段失败异常的类型、`str` 与 `repr` | 【A19】【A23】【A24】 |
| `decision_started` 随第一条行动请求证据一起落库 | 引擎一侧不变；请求证据仍在发送前写（【A16】） |
| `MEMORY.md`「身份」段 | 【A4】：`inputs.persona_card.strip()` 等于今天的 `persona_card_text(profile, name)` |
| `LIFE_SYSTEM_LAYER`、`LAYER_SCHEMAS`、`tool_schema_sha256` | 【A29】 |

## 设计文档

| 位置（基线） | 之后必须写明的事实 |
| --- | --- |
| `docs/design/00-overview.md:89` | `LifeAgent` 是数字人的代码实现（`src/worldmock/agents/life_agent.py`），每个决策新建一个实例；不再写「继承 AgentSociety 2 的 `PersonAgent`」 |
| `docs/design/01-architecture.md:172` | 数字人一行链接 `src/worldmock/agents/life_agent.py` 与 `src/worldmock/agents/protocol/react_loop.py`；删去「AgentSociety 2 人物与记忆模块」 |
| `docs/design/06-agent.md:34-39` | 卡片的两份派生物在每次运行开始时（新建、普通恢复、反事实）由冻结的 `persona.toml` 构造，连同 `persona.toml` 的 `name` 与清单里的 `stage_model_identity` 组成 LifeAgent 的冻结输入；不写进人物工作区，所以恢复和重放没有工作区卡片对 `persona.toml` 的比对，`IntegrityError("Restored persona card differs from persona.toml")` 一句删去；`MEMORY.md`「身份」段是卡片文本去掉首尾空白 |
| `docs/design/06-agent.md:61-62` | 删去只读问答 user 一句 |
| `docs/design/06-agent.md:76` | 删去「只读问答每个回合只开放 finish。」 |
| `docs/design/06-agent.md:106` | 删去只读系统层整段；在同一处写明提示指纹 `prompt_sha256` 绑定生活系统层与七层 Schema |
| `docs/design/06-agent.md:372` | 删去「只读问答尚无快照时的空记忆层里「可查」是空对象。」 |
| `docs/design/06-agent.md:406` | 删去「只读问答快照保存兜底以后的记忆层；」 |
| `docs/design/06-agent.md:748` | 「与新建运行写进工作区 profile 的 `persona_card` 逐字相同」改为「与运行开始时交给 LifeAgent 的卡片文本逐字相同」 |
| `docs/design/06-agent.md:884` | 「只读问答与截断后的那次重试用同一个数」改为「截断后的那次重试用同一个数」 |
| `docs/design/06-agent.md:892` | 「与 dispatcher 重试分别计数」改为「与模型客户端的重试分别计数」 |
| `docs/design/06-agent.md:893` | 改为：模型身份、两类重试预算与回合上限冻结在运行清单的 `stage_model_identity`；卡片与名字每次运行开始时由冻结的 `persona.toml` 构造；恢复与重放读同一份清单和同一份 `persona.toml` |
| `docs/design/06-agent.md`「宿主模型协议」，接在 `:895-903` 的步序与流程图之后 | 新写「回合协议」一段，写明：一个实例一次决策、决策之间没有内存状态（【A3】）；观察、回合、步尾的步序（【A4】）；冻结输入取自哪里（【A6】–【A9】）；人物只经五个引擎方法接触世界（【A10】）；回合与回合用完的运行故障及其英文文字（【A11】）；调用身份格式与每次调用加一（【A12】）；每回合工具面（【A13】）；请求字典的六个键与次序（【A14】）；容量预检与续接前缀替换、浓缩不写回历史（【A15】【A21】）；证据先后（【A16】–【A19】）；assistant 与工具调用的形状、键序、`arguments` 缺省 `"{}"`（【A20】）；两种纠错消息与相同纠错替换不叠加、纠错正文的缺省分隔符（【A22】）；三个失败阶段码与它们指向的调用位置（【A17】【A19】【A24】） |
| `docs/design/06-agent.md:908-909` | 由[「人物工作区、记忆文件与「记忆已更新」」](./05-workspace-memory.md)删除；「回合协议」不提记忆运行时的模型入口与 `sleep_boundary` |
| `docs/design/07-evaluation.md:147` | 「（截断重试是同一回合）」改为「（每次模型调用都分配新的 NNNN，截断重试与纠错回合也不例外）」 |
| `docs/design/09-io-formats.md:77-78` | 删去「人物卡片的两份派生物…保存在人物工作区 `AGENT.json` 的 `profile` 里，`AGENT.json` 的结构号是 1」；写明卡片每次运行开始时由 `persona.toml` 直接构造、不保存副本，没有单独的编译产物文件和结构号 |
| `docs/design/09-io-formats.md:87` | 删去「只读问答尚无快照时的空记忆层恰为 …」分句 |

## 测试

全部离线，不跳过、不预期失败。本章的新用例写在[「比较工具」](./09-tools-tests.md)一章新建的 `tests/integration/runtime/test_react_loop.py` 里（同一文件还装 `tests/integration/as2/test_agent_react_validation.py` 与 `tests/integration/as2/test_worldmock_life_hooks.py` 移植来的回合用例，移植表在该章），另有两个新文件 `tests/unit/agents/test_life_agent_shape.py` 与 `tests/integration/runtime/test_life_agent_engine.py`。`test_react_loop.py` 直接导入 `worldmock.agents.protocol.react_loop` 与 `worldmock.agents.life_agent`，用真实 LifeAgent、一个按五个方法记录调用的引擎替身（在 `record_model_exchange` 当场存下请求的规范 JSON）、一个按脚本应答的 `call(request)` 模型客户端替身，观察取 `tests/integration/runtime/test_life_agent_protocol.py` 现有的合成观察。`test_life_agent_engine.py` 用 `tests/engine_fixture.py` 的 `engine_factory` 建真实引擎与 SQLite，按 `person_inputs(inputs.persona, 引擎清单的 stage_model_identity)` 建真实 LifeAgent，模型客户端是脚本替身，记忆存储由「人物工作区」一章的 `memory_stores` 建在运行目录。现有协议测试改写时只按「测试里旧入口的对应」改调用与导入，以及去掉【A14】删去的三个键，断言主体不改。

| 条款 | 测试节点 | 新/改 |
| --- | --- | --- |
| 【A1】 | `tests/unit/engineering/test_import_graph.py::test_production_import_graph_obeys_all_boundaries`（`ENGINE_BOUNDARIES` 加一行 `"worldmock.agents": ("worldmock.runtime", "worldmock.simulation_engine.engine")`） | 改 |
| 【A1】 | `tests/unit/engineering/test_import_graph.py::test_engine_boundaries_reject_reverse_imports[worldmock.agents.life_agent-worldmock.simulation_engine.engine]` | 新（加参数） |
| 【A2】 | `tests/unit/agents/test_life_agent_shape.py::test_life_agent_construction_reads_no_file_and_calls_no_service` | 新 |
| 【A3】【A4】 | `tests/integration/runtime/test_life_agent_engine.py::test_a_decision_observes_once_before_any_model_call_and_ends_with_the_memory_step`（替身记录的调用次序为一次 `observe`、模型调用、证据、一次 `save_step` 写出的 `MEMORY.md`、`memory_updated`；同一人物的第二个决策用新实例，第一实例的状态不影响它） | 新 |
| 【A4】 | `tests/integration/runtime/test_life_agent_engine.py::test_run_turns_answers_on_the_given_observation_without_the_memory_step`（`run_turns` 返回登记的世界请求，引擎没有第二次观察，工作区没有写出 `MEMORY.md`） | 新 |
| 【A5】 | `tests/integration/runtime/test_life_agent_protocol.py::test_decision_failure_is_logged_once_and_reraised_unchanged` | 改 |
| 【A6】【A7】【A8】 | `tests/unit/agents/test_life_agent_shape.py::test_person_inputs_take_the_card_and_name_from_the_persona_table_and_the_identity_from_the_manifest`（字段逐值等于三处卡片函数与清单；`model_identity` 与传入的映射相等且不是同一对象，改其中嵌套值不影响另一方） | 新 |
| 【A9】 | `tests/integration/runtime/test_persona_profile.py::test_the_persona_layer_name_is_the_persona_table_name_on_new_and_resumed_runs`（`persona.toml` 写 `name = " 林舟 "`，经 `run_world` 新建、`resume_world` 从检查点恢复，两条运行第一个生活决策请求的人设层 `identity.name` 都恰为 `" 林舟 "`） | 新 |
| 【A10】 | `tests/unit/agents/test_life_agent_shape.py::test_engine_handle_declares_the_five_methods_that_life_engine_provides`（Protocol 成员恰为五个；LifeEngine 各有同名方法，参数名相同，都不是协程函数） | 新 |
| 【A11】 | `tests/integration/runtime/test_react_loop.py::test_used_up_turns_stop_with_the_budget_fault_and_no_world_request`（`str` 恰为 `TURNS_EXHAUSTED`，即 `ReAct decision budget exhausted without a world request`；有一条 ERROR 日志 `LifeAgent <人物编号> used all <回合上限> model turns without a world request`；模型调用恰为回合上限次，引擎没有登记） | 新 |
| 【A11】 | `tests/integration/runtime/test_output_truncation.py::test_a_retry_still_without_a_tool_call_follows_the_turn_budget` | 改 |
| 【A11】【A13】 | `tests/integration/runtime/test_life_agent_protocol.py::test_runtime_budget_handles_twelve_bad_responses_then_submits_once` | 改 |
| 【A12】 | `tests/integration/runtime/test_react_loop.py::test_every_model_call_is_a_turn_with_the_next_call_identity` | 新 |
| 【A12】【A16】【A18】 | `tests/integration/runtime/test_life_agent_engine.py::test_turn_evidence_reaches_sqlite_in_order_with_its_call_identities` | 新 |
| 【A13】 | `tests/integration/runtime/test_react_loop.py::test_tools_drop_the_read_queries_on_the_last_turn_and_on_the_truncation_retry` | 新 |
| 【A13】 | `tests/integration/runtime/test_life_agent_protocol.py::test_reserved_action_boundary_rejects_memory_without_extra_request` | 改 |
| 【A14】 | `tests/integration/runtime/test_react_loop.py::test_turn_request_has_the_six_client_keys_in_order`（平时与截断重试两种：`tuple(request) == REQUEST_KEYS`，六个值逐一断言） | 新 |
| 【A15】 | `tests/integration/runtime/test_react_loop.py::test_a_condensed_request_is_sent_but_the_history_keeps_the_original_progress_layer` | 新 |
| 【A15】【A21】 | `tests/integration/runtime/test_react_loop.py::test_capacity_ended_continuation_keeps_later_turns_on_the_fresh_snapshot` | 新 |
| 【A15】【A21】 | `tests/integration/runtime/test_same_second_continuation.py::test_budget_step_zero_starts_the_decision_from_a_fresh_snapshot_for_every_turn` | 改 |
| 【A16】【A18】 | `tests/integration/runtime/test_react_loop.py::test_request_evidence_comes_before_the_send_and_the_response_after_validation`（证据次序、客户端收到的唯一参数 `is` 请求证据里的字典） | 新 |
| 【A17】 | `tests/integration/runtime/test_react_loop.py::test_a_failed_send_records_the_model_service_stage_and_reraises_the_original` | 新 |
| 【A17】 | `tests/integration/runtime/test_react_loop.py::test_a_cancelled_send_records_no_failure_evidence` | 新 |
| 【A17】 | `tests/integration/runtime/test_host_failure_stages.py::test_model_service_failure_on_the_real_engine_is_logged_once_and_reraised_unchanged` | 改 |
| 【A18】 | `tests/integration/runtime/test_react_loop.py::test_serialized_response_accepts_a_model_dump_or_a_mapping_and_rejects_the_rest` | 新 |
| 【A18】 | `tests/integration/runtime/test_react_loop.py::test_an_unserializable_response_stops_with_its_type_error_and_no_response_evidence` | 新 |
| 【A18】 | `tests/integration/runtime/test_react_loop.py::test_the_usage_check_reuses_the_count_taken_before_sending` | 新 |
| 【A19】 | `tests/integration/runtime/test_react_loop.py::test_a_failed_evidence_write_stops_with_the_evidence_stage` | 新 |
| 【A19】 | `tests/integration/runtime/test_host_failure_stages.py::test_evidence_write_failure_of_the_footprint_store_is_raised_with_its_stage` | 改 |
| 【A20】 | `tests/integration/runtime/test_react_loop.py::test_an_accepted_reply_is_the_assistant_message_and_world_requests_add_no_tool_message` | 新 |
| 【A20】 | `tests/integration/runtime/test_react_loop.py::test_assistant_message_keeps_the_wire_key_order_and_default_arguments` | 新 |
| 【A20】 | `tests/unit/engineering/test_single_source.py::test_production_shared_rules_have_one_owner_and_no_business_clones`（`OWNERS` 加 `'src/worldmock/state/model/exchange.py': ('ModelCallEvidence', 'wire_tool_call', 'assistant_message')` 与 `'src/worldmock/agents/protocol/react_loop.py': ('TURNS_EXHAUSTED', 'model_call_id')`，`life_prompt.py` 一行删去 `'empty_memory_layer'`） | 改 |
| 【A20】【A21】 | `tests/integration/runtime/test_same_second_continuation.py::test_the_continued_request_starts_with_the_previous_request_byte_for_byte` | 改 |
| 【A20】【A22】 | `tests/integration/runtime/test_same_second_continuation.py::test_a_decision_with_memory_and_correction_turns_is_continued_byte_for_byte` | 改 |
| 【A21】 | `tests/integration/runtime/test_react_loop.py::test_end_continuation_returns_a_new_list_and_leaves_the_input_untouched` | 新 |
| 【A21】 | `tests/integration/runtime/test_same_second_continuation.py::test_the_pre_send_check_breaks_a_chain_whose_assistant_lacks_thinking` | 改 |
| 【A22】 | `tests/integration/runtime/test_react_loop.py::test_a_correction_after_a_thinking_request_with_complete_calls_is_paired` | 新 |
| 【A22】 | `tests/integration/runtime/test_react_loop.py::test_a_correction_without_complete_calls_is_one_host_user_message`（参数化：没有调用、`type` 不是 function、`id` 空白、名字空白、参数不是字符串、去空白后 `id` 重复） | 新 |
| 【A22】 | `tests/integration/runtime/test_react_loop.py::test_a_correction_in_a_thinking_disabled_retry_is_a_host_user_message` | 新 |
| 【A22】 | `tests/integration/runtime/test_react_loop.py::test_the_correction_body_uses_the_default_json_separators` | 新 |
| 【A22】 | `tests/integration/runtime/test_output_truncation.py::test_identical_corrections_replace_each_other_across_consecutive_truncations` | 改 |
| 【A22】 | `tests/integration/runtime/test_output_truncation.py::test_a_rejected_retry_turn_is_dropped_so_a_continued_decision_keeps_every_thought` | 改 |
| 【A23】 | `tests/integration/runtime/test_host_feedback.py::test_protocol_feedback_is_a_structure_with_its_evidence_text`（由 `test_feedback_structure_survives_copying_and_evidence_stays_a_plain_string` 改写：`ProtocolFeedback` 不是 `str`，`text` 与证据 `error` 相同，复制后相等） | 改 |
| 【A23】 | `tests/integration/runtime/test_host_feedback.py::test_protocol_feedback_is_the_shared_structure_with_the_fixed_instruction` | 改 |
| 【A23】 | `tests/integration/runtime/test_host_feedback.py::test_stage_errors_keep_their_text_and_carry_the_feedback`（三个阶段码：`type(error) is RuntimeError`，`error.args == (text,)`，`repr(error) == f"RuntimeError({text!r})"`，`error.feedback.report` 的码与路径） | 新 |
| 【A24】 | `tests/integration/runtime/test_react_loop.py::test_a_tool_service_failure_points_to_its_call_and_reraises_with_the_stage` | 新 |
| 【A24】 | `tests/integration/runtime/test_host_failure_stages.py::test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage` | 改 |
| 【A25】 | `tests/integration/runtime/test_react_loop.py::test_recorded_requests_are_never_changed_after_they_are_recorded`（三回合：检索、纠错、世界请求；结束时逐份比较证据里的请求与记录当时的规范 JSON） | 新 |
| 【A26】【A27】 | `tests/unit/agents/test_life_agent_shape.py::test_retired_person_glue_and_readonly_path_are_gone`（`ast` 扫描 `src/worldmock` 全部 `.py`：没有名为 `HostFeedback`、`feedback_report`、`feedback_content`、`ReadonlyFinishRequest`、`READONLY_SYSTEM_LAYER`、`readonly_messages`、`empty_memory_layer`、`PROMPT_SNAPSHOT_FILE`、`write_prompt_snapshot`、`readonly_layers`、`persona_card_text` 的函数、类或模块级赋值；源码文本里没有 `Environment is not initialized`、`LLM is not initialized`、`Model request evidence is unavailable`、`Native tool call id is unavailable`、`readonly decision state`、`ReAct decision requires`、`life_prompt_snapshot`；`LifeAgent` 没有属性 `description`、`init_description`、`_load_world_description`、`from_workspace`、`restore`、`to_workspace`、`persist_agent_json`、`step`、`ask`；`src/worldmock/agents/prompt/life_context.py` 不存在） | 新 |
| 【A27】 | `tests/integration/runtime/test_life_agent_engine.py::test_a_decision_writes_no_prompt_snapshot`（真实决策之后运行目录与临时目录下没有 `life_prompt_snapshot.json`） | 新 |
| 【A27】【A29】 | `tests/unit/start_interfaces/test_system_layer_text.py::test_tool_descriptions_are_the_eight_purpose_sentences`（由 `…_nine_purpose_sentences` 改名，次序里没有 `finish`） | 改 |
| 【A28】 | `tests/unit/start_interfaces/test_system_layer_text.py::test_prompt_fingerprint_binds_only_the_life_system_layer_and_the_layer_schemas`（钉住两个值与模板的两个键） | 新 |
| 【A29】 | `tests/unit/start_interfaces/test_system_layer_text.py::test_the_life_system_layer_bytes_and_the_tool_fingerprint_are_unchanged` | 新 |
| 【A29】 | `tests/unit/start_interfaces/test_system_layer_text.py::test_kept_rules_and_the_whole_list_match_their_digests` | 不改，继续钉住规则摘要 |
| 【A30】 | 「安装、代码身份、依赖与许可」一章的许可用例，覆盖本章列出的两个模块 | 见该章 |

随本章删除的测试（只测已删除的东西）：

| 测试 | 删除的原因 |
| --- | --- |
| `tests/integration/runtime/test_life_agent_protocol.py` 的 `test_readonly_schema_uses_its_independent_finish_contract`、`test_prompt_snapshot_stores_the_memory_layer_taken_by_name`、`test_readonly_budget_reserves_finish_and_resets_between_loops`、`test_readonly_state_mismatch_is_an_internal_failure` | 只读问答与快照 |
| `tests/integration/runtime/test_life_agent_protocol.py::test_native_react_rejects_a_decision_without_any_model_turn` | 零回合报错走不到，已删 |
| `tests/integration/runtime/test_life_agent_protocol.py::test_usage_check_refuses_a_count_taken_for_another_request` | 按对象身份配对已删 |
| `tests/integration/runtime/test_output_truncation.py::test_readonly_questions_keep_their_parameters_and_a_truncated_answer_is_retried_with_finish_only` | 只读问答 |
| `tests/integration/runtime/test_persona_layer_card_only.py::test_prompt_snapshot_holds_the_card_persona_layer` | 快照 |
| `tests/integration/runtime/test_host_feedback.py::test_feedback_rejects_bare_strings_instead_of_reconstructing_positions` | `feedback_report` 已删 |
| `tests/integration/runtime/test_agent_branch_coverage.py::test_recall_dispatch_without_an_environment_fails_closed` | 环境为空的核对已删 |
| `tests/integration/runtime/test_seven_layer_request.py::test_tool_identity_ignores_tools_outside_the_native_surface` | `finish` 用途句已删 |
| `tests/integration/runtime/test_public_runtime_boundaries.py::test_persona_text_requires_the_card_text`、`::test_readonly_prompt_uses_saved_snapshot_and_requires_the_persona_card` | `life_context.py` 与只读层已删 |
| `tests/unit/start_interfaces/test_system_layer_text.py::test_readonly_system_layer_is_the_four_rules_verbatim` | 只读系统层 |
| `tests/unit/agent_protocol/test_user_layer_order.py::test_empty_memory_layer_is_the_assembled_empty_layer` | `empty_memory_layer` 已删 |
| `tests/unit/agent_protocol/test_decision_context_protocol.py::test_readonly_loop_uses_only_persona_memory_and_question` | 只读问答 |

随本章改写的测试（除「测试里旧入口的对应」那些机械替换之外）：

| 测试 | 改法 |
| --- | --- |
| `tests/integration/runtime/test_life_agent_protocol.py::test_how_to_live_a_day_is_one_resident_section_of_both_decision_stages` | 删去只读系统层一半，改名 `test_how_to_live_a_day_is_one_resident_section_of_the_life_system_layer` |
| `tests/integration/runtime/test_life_agent_protocol.py` 的合成观察（`:205`） | `"memory_layer": assemble_layer(None, {}, [])` |
| `tests/integration/runtime/test_agent_branch_coverage.py` | 删去 `ReadonlyFinishRequest` 的导入与 `test_memory_entries_and_finish_keep_content_limits` 里 `finish` 那一段、「disabled in readonly mode」那一段，`_prepared`、`_functions` 去掉 `readonly` 参数 |
| `tests/integration/runtime/test_memory_search_tool.py::test_search_dispatch_without_an_environment_fails_closed` | 删去「Environment is not initialized」一段（`:208-211`），留下达到上限时不送引擎的断言，改名 `test_search_past_the_limit_is_answered_without_the_engine` |
| `tests/unit/agent_protocol/test_decision_context_protocol.py` | `test_prompt_fingerprint_binds_every_formal_template` 的参数化（`:42`）只剩 `LIFE_SYSTEM_LAYER`；`test_each_model_entry_preflights_the_complete_token_request`（`:209-221`）去掉 `readonly` 参数，只测行动入口；`test_long_system_message_passes_when_complete_request_is_below_threshold`（`:224-233`）改用 `LIFE_SYSTEM_LAYER` 与行动回复；`:205` 的 `agent._last_finish_memories is None` 改为 `agent._memories == []` |
| `tests/integration/runtime/test_conclude_memories.py::test_conclusion_memories_flatten_by_task_with_system_source` | 改名 `test_conclusion_memories_flatten_by_task_in_the_stored_shape`，文档串改为「宿主按项的顺序展平，原样交出 control_items 读出的存储形状记忆（每项先 memories、后 views，看法带 subject）；其他世界请求不写记忆。」；`written[0]` 的期望改为 `[memory for item in control_items("conclude_activity", ConcludeActivityRequest.model_validate(conclusion)) for memory in item.memories]`（带 `subject`，没有 `source`、`epistemic_status`，这两项按「人物工作区」一章【W8】由记忆存储写定）；`written[1] == []` 不变；`agent._last_finish_memories` 读作 `agent._memories` |
| `tests/unit/start_interfaces/test_system_layer_text.py` | `test_retired_wording_is_gone_from_the_system_layers_and_tool_descriptions` 与 `test_memory_rules_quote_existing_memory_layer_keys` 去掉只读规则；`_READONLY_RULES` 删除 |
| `tests/unit/agent_protocol/test_tool_description_references.py` | `:21` 的导入与 `:40` 的 `"finish": ReadonlyFinishRequest` 删去 |
| `tests/unit/life/test_memory_layer_schema.py`、`tests/unit/start_interfaces/test_persona_and_memory_schemas.py` | 改用 `assemble_layer(None, {}, [])`；文档串里的「只读空记忆层」改为「空记忆层」 |
| `tests/integration/runtime/test_public_runtime_boundaries.py` | 删去 `:15`、`:17` 里已删名字的导入；`test_persona_layer_rejects_actual_overlong_card_without_truncation` 改用 `persona_layer(prepared["persona_card_struct"], "林舟")` |
| `tests/integration/runtime/test_persona_layer_card_only.py:51`、`tests/integration/runtime/test_persona_profile.py:68`、`:103`、`:406` | `persona_layer` 改收结构化卡片 |
| `tests/integration/runtime/test_persona_profile.py:456` | 传 `profile["persona_card_struct"]` |
| `tests/unit/agent_protocol/test_decision_context_protocol.py:195` | 传人物的 `inputs.persona_card_struct`（`agent._profile` 不再存在） |
| `tests/unit/start_interfaces/test_persona_and_memory_schemas.py:24`、`tests/unit/profile/test_textgen.py:156` | 直接传 `persona_card_struct(…)` 的结果；断言主体不变 |
| `tests/integration/life/test_phone_records.py:389`、`tests/integration/runtime/test_persona_profile.py:73`、`tests/integration/runtime/test_seven_layer_request.py:29`、`tests/unit/agent_protocol/test_prompt_capacity_memory.py:44` | `system_message()` |
| `tests/integration/runtime/test_existing_relationship_subject.py:8`、`:42`，`tests/unit/agents/test_truncation_feedback.py:8`、`:21`，`tests/unit/agent_protocol/test_search_memory_request.py:19`、`:98` | `feedback_report`、`feedback_content`、`validate_react_decisions` 按对应表替换 |
| `tests/integration/runtime/test_host_failure_stages.py` 全部读 `failure.value.args[0].report` 的用例 | 改读 `failure.value.feedback.report`；`str(failure.value)` 的断言不变 |

新登记的源码变异（`tests/dataset_acceptance.py` 的 `SourceMutation`，锚点由实施者取实现后的真实源码、在符号内唯一）：

| 名字 | 符号 | 替换造成的破坏 | 杀手 |
| --- | --- | --- | --- |
| `continuation_end_keeps_the_chain_in_history` | `worldmock.agents.protocol.react_loop::ReactLoop._request` | 容量逼停续接后只换预算、不换本回合消息的前缀，下一回合的历史又带回旧链和它的思考 | `tests/integration/runtime/test_react_loop.py::test_capacity_ended_continuation_keeps_later_turns_on_the_fresh_snapshot`；`tests/integration/runtime/test_same_second_continuation.py::test_budget_step_zero_starts_the_decision_from_a_fresh_snapshot_for_every_turn` |
| `correction_paired_without_thinking` | `worldmock.agents.protocol.react_loop::ReactLoop._corrected` | 去掉「本回合请求开启了思考」这个条件，截断重试回合里带调用的回复也配对写回，历史里出现缺思考的 assistant | `tests/integration/runtime/test_react_loop.py::test_a_correction_in_a_thinking_disabled_retry_is_a_host_user_message`；`tests/integration/runtime/test_output_truncation.py::test_a_rejected_retry_turn_is_dropped_so_a_continued_decision_keeps_every_thought` |
| `call_identity_counts_from_zero` | `worldmock.agents.protocol.react_loop::model_call_id` | 回合号写成 `turn - 1`，调用身份与今天不同，评测按 `:model:NNNN:` 数回合也随之错位 | `tests/integration/runtime/test_react_loop.py::test_every_model_call_is_a_turn_with_the_next_call_identity`；`tests/integration/runtime/test_life_agent_engine.py::test_turn_evidence_reaches_sqlite_in_order_with_its_call_identities` |

受本章影响的现有变异：

| 现有变异 | 处理 |
| --- | --- |
| `action_output_budget_back_to_eight_thousand`（`custom.agents.life_agent::ACTION_MAX_TOKENS`） | 符号改为 `worldmock.agents.life_agent::ACTION_MAX_TOKENS`，原文与替换文不变，杀手不变 |
| `prompt_snapshot_takes_a_position`（`custom.agents.life_agent::LifeAgent._prepare_life_action_state`） | 删除：快照随只读问答删除 |
| `usage_estimate_of_another_request_accepted`（`custom.agents.life_agent::LifeAgent._usage_estimate`） | 删除：按对象身份配对的预检结果删除，杀手同删 |
| `truncation_content_built_through_the_logging_feedback`（`worldmock.agents.protocol.life_truncation::TRUNCATION_CONTENT`） | 不变：替换文 `truncation_feedback().report` 在 `ProtocolFeedback` 上照样成立 |

## 不在本章

| 事项 | 所属章 |
| --- | --- |
| 运行宿主为每个决策新建 LifeAgent 的代码与测试、三种启动顺序里调用 `person_inputs` 的位置、每个人物一个记忆存储的建立；清单 `persona_habit_responses` 照旧由 runner 的 `habit_responses(creation_inputs 的 specs)` 计算，不改读 `PersonInputs`（第 10 章「跨章裁定」） | [「运行宿主与决策轮」](./01-run-host.md) |
| runner 里读 `AGENT.json`、`config.json` 的恢复校验（`src/worldmock/runtime/lifecycle/runner.py:388-445`、`:569-581`）与 `creation_inputs`（`setup.py:198-215`）的删除或改写 | [「运行宿主与决策轮」](./01-run-host.md)、[「人物工作区、记忆文件与「记忆已更新」」](./05-workspace-memory.md) |
| LifeEngine 的只读查询方法、返回值深拷贝、LifeEnv 删除后的工具集合一致性测试、`life_action_tool_names`/`life_query_tool_names` 的文档串 | [「去掉 LifeEnv：引擎门面、只读查询与提交结果」](./02-engine-facade.md) |
| `ModelClient`、`REQUEST_KEYS`、`collect_transport_attempts`、litellm 的调用参数、请求底稿删去三个键的证据与查看器样例、`prompt_capacity` 的 `token_counter` 来源 | [「模型客户端与模型请求底稿」](./04-model-client.md) |
| 记忆存储类、`MemoryStep`、`save_step`、`memory_update_evidence`、`MemoryUpdateEvidence` 的字段、条目的来源与认知状态、`MEMORY.md` 的模板、`docs/design/06-agent.md:908-909` | [「人物工作区、记忆文件与「记忆已更新」」](./05-workspace-memory.md) |
| 宿主写下的中止原因与 `status.json`，回合用完时这些地方的新文本 | [「故障文本」](./06-failure-text.md) |
| 来源与许可句的写法与覆盖测试、`custom/` 目录与覆盖率、风格门禁里 `custom` 根的删除、`setup.py` 三处导入与 `DELAYED_IMPORTS` | [「安装、代码身份、依赖与许可」](./07-install-identity.md) |
| 上表所列设计文档的正文 | [「设计文档与手册」](./08-design-docs.md) |
| 测试枢纽 `tests/integration/life_host_fixtures.py`、`tests/integration/runtime/current_contract_fixtures.py`、`tests/integration/host_run_fixtures.py` 改用新构造；AgentSociety2 专属测试文件的移植与删除；`tools/run-viewer-context.js:94`、`:105` 的来源标签；`scripts/compare_runs.py:56-58` 与 `tests/dayrun_baseline.py:30,41` 对 `AGENT.json` 的屏蔽 | [「比较工具、查看器与测试迁移」](./09-tools-tests.md) |
| 录音重跑、`raw-order` 与运行比对对请求字节的最终证明 | [「实施、验收证据与完成判据」](./10-implementation.md) |

## 规格阶段的决定

| 事项 | 决定 | 理由 |
| --- | --- | --- |
| 构造参数的形状 | `LifeAgent(inputs, engine, model_client, memory)`，决策上下文经 `decide(context)` 传入 | 四个运行级对象一次传入，上下文是每次决策的值；参数不超过五个 |
| 冻结输入的类型与位置 | frozen dataclass `PersonInputs` 与 `person_inputs()` 放在 `life_agent.py` | 只有 LifeAgent 消费，放在使用处，不另建模块 |
| `PersonInputs.persona_card` 与 `MEMORY.md` 身份段 | `persona_card` 是不去空白的 `render_card(...)`，与今天 profile 里的值相同；去首尾空白留在步尾，`life_context.py` 删除 | 日回顾的卡片输入与 profile 的卡片逐字相同（`docs/design/06-agent.md:748`），`MEMORY.md` 用的是去空白后的文本，两种用法都保持今天的字节；唯一调用者删除后 `persona_card_text` 成了死代码，它的空文本报错走不到 |
| 同秒续接开关与回合上限 | 随模型身份传入，不另设字段 | 两者本来就是 `stage_model_identity` 的成员（`setup.py:98-103`），另设字段是同一事实的第二份；人物不读开关 |
| 模型身份的拷贝 | `person_inputs` 深拷贝 `stage_identity` | 今天经 `AGENT.json` 往返本来就是独立副本；深拷贝让人物与清单互不别名 |
| 人设层函数的参数 | `persona_layer`、`known_persons`、`card_struct` 改收结构化卡片的映射；删去「Persona card structure is required」分支 | 输入不再是工作区 profile；`PersonInputs` 必带卡片，缺失分支走不到 |
| 引擎句柄的名字与位置 | `EngineHandle`，`src/worldmock/agents/protocol/engine_handle.py`，五个同步方法 | 引擎方法全部同步执行（提案六「一个进程，一个事件循环」）；`react_loop` 与 `life_agent` 都引用它，单独成模块免得循环导入 |
| 回合循环怎样调用人物 | `ReactLoop` 直接调用 LifeAgent 的四个公开方法 `turn_tools`、`turn_request`、`parse_reply`、`execute_decisions`，只在 `TYPE_CHECKING` 下导入 LifeAgent | 只有一种人物实现，不为它另建协议（AGENTS.md「不增加防御性抽象」） |
| 请求字典与 `ACTION_*` 常量放在哪里 | 请求字典由 `LifeAgent.turn_request` 构造，三个 `ACTION_*` 常量留在 `life_agent.py`，名字与值不变；`react_loop` 只管回合机制 | 请求内容由人物决定、回合机制与人物无关，这正是今天 LifeAgent 覆写请求钩子的分工；常量留在原模块，`setup.py` 的导入名与现有变异只改模块前缀，与「安装、代码身份」「比较工具」两章一致；`react_loop` 运行期不导入 `life_agent`，没有循环 |
| 请求字典的键序 | 按「模型客户端」一章的 `REQUEST_KEYS`（`model`、`messages`、`tools`、`tool_choice`、`max_tokens`、`extra_body`） | 三条路径只有一条键序规则；与今天只差 `max_tokens`、`extra_body` 的相对次序，落库是规范 JSON、HTTP 正文由 SDK 按具名参数生成，没有字节依赖它 |
| 回合循环的入口 | `run_turns(context, observation)` 只跑回合循环、不做步尾；`decide` 在它前后加观察与步尾 | 「比较工具」一章的 `decide`/`person` 夹具在引擎上开轮、自己观察后只跑回合循环，今天它们调 `run_react_loop` 做同样的事 |
| `ModelCallEvidence` 的位置 | `src/worldmock/state/model/exchange.py`，字段同 `agentsociety2/agent/base/react.py:73-94` | 人物产生、引擎按属性读；`state` 是两边都能导入的下层（`tests/unit/engineering/test_import_graph.py` 的 `ENGINE_BOUNDARIES`） |
| assistant 共用实现的形状 | `wire_tool_call(identity, name, arguments)` 与 `assistant_message(content, tool_calls, reasoning)`，取值由两侧各自从响应对象或落库字典取出 | 人物侧拿的是 litellm 响应对象，引擎侧拿的是落库字典；共用的是键序与缺省规则，两侧字节与今天各自的实现相同 |
| `HostFeedback` 的替代 | frozen dataclass `ProtocolFeedback(report, text)`；删 `feedback_report`、`feedback_content`；`_stage_feedback` 搬为 `life_feedback.stage_feedback(code, path)`，去掉从不传值的 `detail` | 协议错误以结构传递；名字与证据键 `protocol_feedback` 同一概念 |
| 阶段失败异常怎样带调用位置 | `stage_error(code, path)` 构造 `RuntimeError(feedback.text)`，同一个 `ProtocolFeedback` 挂在属性 `feedback` 上 | 提案要保留带调用位置的分级，裁定 8 与「故障文本」一章要求异常类型与文字不变；今天调用位置只在 `str` 子类的参数里，参数换成普通字符串后，`repr` 不变、位置仍在异常上 |
| 续接前缀的替换 | `send_check`、`end_continuation` 返回 `(新列表, 新预算)`，不改入参 | 就地改写只为 AgentSociety2 的列表别名（提案四） |
| `ReactDecision`、`ReactToolResult` | 搬进 `react_loop.py`；只留 `action`、`args`、`tool_call_id` 与 `ok`、`data` | `thought`、`final` 只为 `finish`；`observation` 字符串只进追踪和观察列表，两者都删 |
| 走不到的核对 | 删去零回合报错、接受回复的两条一致性核对、`Model request evidence is unavailable`、`LLM is not initialized`、人物侧五处 `Environment is not initialized`、`Native tool call id is unavailable`、只读状态不一致 | 回合数由循环下标给出，模型身份保证上限至少 1（`src/worldmock/configuration/inputs/models.py:223`）；接受时的决策与调用出自同一次解析；构造必带引擎与客户端；`_parse_model_calls` 拒收空的或重复的调用身份，被接受的查询必有身份，那条核对是 AgentSociety2 同名核对（`agent_runtime.py:694-696`）的照抄 |
| 工具执行同步 | `execute_decisions`、派发、检索与登记都是同步函数 | 引擎方法同步；只有模型调用需要 await |
| 步尾调用 | `MemoryStep(...)`、`save_step(memory, step)`、`memory_update_evidence(context, memory, outcome)`，同步 | 「人物工作区」一章的接口（【W15】【W16】）；与今天步尾的输入相同（`agentsociety2/agent/person.py:546-553`），去掉 `tick`、`step_count`、`sleep_boundary` |
| 收尾记忆的形状 | `_conclusion_memories` 原样返回 `control_items` 的存储形状，删 `_CONCLUSION_MEMORY_SOURCE` | 「人物工作区」一章让存储吸收条目归一化（固定来源与认知状态、去掉 `subject`），一条规则一处 |
| 回合用完的文字 | `TURNS_EXHAUSTED = "ReAct decision budget exhausted without a world request"`；抛出前 `logging.error("LifeAgent %s used all %d model turns without a world request", 人物编号, 回合上限)`，再抛 `RuntimeError(TURNS_EXHAUSTED)` | 文本与日志由「故障文本」一章【F4】规定，本章只给出常量与日志的位置（第 10 章「跨章裁定」）；只把今天的「a valid finish」换成生活决策真正的结束条件，类型不变；AGENTS.md 要求异常分支有错误日志 |
| （问题）恢复时的卡片比对 | 随工作区副本一起删除 | 卡片每次运行开始都由冻结的 `persona.toml` 构造，没有副本可比；`persona.toml` 已由场景摘要绑定 |
| （问题）`docs/design/09-io-formats.md:87` 的空记忆层形状 | 只删只读问答分句，「可查」可以是空对象的 Schema 不变 | 空的「可查」在生活决策里照样出现（`src/worldmock/simulation_engine/observation/memory/layout.py:66-69`），改 Schema 会改 `prompt_sha256` 以外的东西 |
| `empty_memory_layer` | 删除，测试改用 `assemble_layer(None, {}, [])` | 生产中只有只读问答用它 |
| 唤醒原因 | 准备阶段照旧调用 `_visible_wake_reasons` 核对形状，不再存结果 | 今天存下的 `_life_wake_reasons` 没有读者；核对是观察损坏时的报错，保留 |
| `_quoted` | 搬迁时删除 | 没有调用者 |
| 名字相等的证明方式 | 新建与恢复两条运行，`persona.toml` 写首尾带空格的名字，核对第一个生活决策请求的人设层 `identity.name` 等于原字符串 | `AGENT.json` 不再存在，无法逐字对照；AgentSociety2 的取名规则在 `PersonaConfig` 的约束下就是原字符串，带空格的名字能咬住任何去空白或改写 |
| `life_action_tool_names`、`life_query_tool_names` | 保留，函数体不变 | 「去掉 LifeEnv」一章的工具集合一致性测试以它们为人物一侧的说法 |
| 删除的东西怎样由测试守住 | 一条源码扫描用例列出删去的名字与报错文字，并核对 LifeAgent 没有框架入口 | 负责人要求不留未做的工作并由测试强制；删掉的东西没有行为可测，只能查它们不在 |
| 新用例所在的文件 | 回合循环的用例写进「比较工具」一章新建的 `tests/integration/runtime/test_react_loop.py` | 该章把移植的回合用例也放在这个文件，一个主题一个文件 |
| 来源句覆盖的模块 | `react_loop.py` 与 `exchange.py` | 两者含从锁定源码移植的循环骨架、序列化与配对纠错；工具调用参数缺省 `"{}"` 源自上游 `670c94ff` 的 `agentsociety2/agent/base/agent.py:1963` |
| `_record_response` 的签名 | 五个参数 `(call_id, fitted, response, feedback, attempts)` | 风格门限五个参数；`FittedRequest` 同时带请求与 `prompt_tokens` |
