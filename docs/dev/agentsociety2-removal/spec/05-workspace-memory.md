# 人物工作区、记忆文件与「记忆已更新」

> 本章属于[去掉 AgentSociety2：开发规格](../SPEC-agentsociety2-removal.md)，读法和叫法见那一页。

## 目标与依据

本章把人物工作区缩成 WorldMock 自己的两个文件，把记忆存储从 AgentSociety2 的基类里搬出来，并改写「记忆已更新」`memory_updated` 的数据形状。模型请求不受影响：记忆层和 `search_memory` 只读足迹库里的事件，从不读工作区文件（src/worldmock/simulation_engine/observation/memory/entries.py:562-576、src/worldmock/simulation_engine/observation/memory/search.py:1、:140-163）。

依据：

| 来源 | 本章落实的内容 |
| --- | --- |
| 提案第六章「人物状态与记忆文件」 | 目录 `run_dir/agents/agent_NNNN/`；只留 `MEMORY.md` 与 `memory/episodes.jsonl`；LF；内容不变不写；不写英文模板；去掉 `tick`、`tick_duration_sec`、`refs`、`step_index`、`step_count`；编号 `m_000001` 连续，恢复时校验；删 `AGENT.json`、`config.json`、`memory/state.json`、`life_prompt_snapshot.json`；人物快照与 tar 格式保留，恢复只校验一遍；`memory_updated` 何时写不变；去掉三个键，`trigger` 闭集去掉 `reflection`；首条 `memory_before_sha256` 为 `null` |
| 提案第四章两行 | 记忆运行时传给框架的「只会报错的模型调用函数」（src/worldmock/agents/memory/life_memory.py:23-44）删除；`AGENT.json` 的墙钟与本机绝对路径消失后，`scripts/compare_runs.py:56-58` 的屏蔽和 `tests/dayrun_baseline.py:30,41` 的打桩随之删除 |
| 提案第七章三行 | `memory_updated` 变；共同检查点里的人物快照内容变成两个文件、格式与参与者不变；运行目录 `as2/agents/agent_NNNN` 改为 `agents/agent_NNNN`，不再生成 `env/LifeEnv` |
| 提案第六章「安装、代码身份与依赖」许可一条 | 吸收上游代码的两个模块在文档串注明来源与许可 |
| 裁定记录第 3、4、5 行 | 缩成两个文件（「从事件推导、取消人物快照」不在本次）；三个恒定键随这次删；目录改名 |

本章引用而不重述的事实：

| 事实 | 所属章 |
| --- | --- |
| `RunHost` 的三种启动顺序、每步一个共同检查点、零点任务触发点 | [「运行宿主与决策轮」](./01-run-host.md) |
| `DecisionContext` 搬到 `src/worldmock/state/decisions/round.py` | [「运行宿主与决策轮」](./01-run-host.md) |
| `LifeEngine.record_memory_update` 作为门面方法保留 | [「去掉 LifeEnv：引擎门面、只读查询与提交结果」](./02-engine-facade.md) |
| LifeAgent 每个决策新建、冻结输入、回合循环、人物侧五方法 Protocol、只读问答与 `life_prompt_snapshot.json` 的代码删除 | [「人物、回合循环与只读问答的删除」](./03-agent-loop.md) |
| 许可原文文件 `LICENSES/AgentSociety2-Apache-2.0.txt`、来源核对 | [「安装、代码身份、依赖与许可」](./07-install-identity.md) |
| 设计文档全文 | [「设计文档与手册」](./08-design-docs.md) |
| 运行比对、查看器、测试夹具的整体迁移 | [「比较工具、查看器与测试迁移」](./09-tools-tests.md) |
| 两份完整日基线重冻、跨仓库交接 | [「实施、验收证据与完成判据」](./10-implementation.md) |

下文把「人物、回合循环与只读问答的删除」简称「人物一章」，把「比较工具、查看器与测试迁移」简称「工具一章」。

## 行为

条款编号【W1】至【W20】供「测试」一节引用。本章的「字」是 Python `len()` 的字符数。

### 目录与成员

【W1】**路径。** 人物 N 的工作区是 `run_dir / "agents" / f"agent_{N:04d}"`，由 `agent_workspace(run_dir, N)` 给出，生产代码里只有这一处拼这条路径。运行目录下不再出现 `as2/`（今天由 vendor/agentsociety2/packages/agentsociety2/agentsociety2/society/society.py:123 拼出）与 `env/`（今天由 AgentSociety2 的环境路由建出：vendor/agentsociety2/packages/agentsociety2/agentsociety2/env/direct_env_actor.py:69-71、env/router_base.py:398-399，随框架消失，WorldMock 不写替代）。

【W2】**新建。** 新建运行由 `RunHost` 在 `LifeEngine.create` 之前调用 `create_workspaces(run_dir, agent_ids)`，`agent_ids` 是运行身份的 `agent_ids`。对每个人物目录 `path`：已存在时先写错误日志 `Agent workspace already exists: <path>`，再抛 `FileExistsError(f"Agent workspace already exists: {path}")`；否则 `path.mkdir(parents=True)`，建出时为空。新建运行的初始检查点里，每个 `agent:<id>` 快照是空 tar 的 gzip（格式见【W20】）。

【W3】**成员。** 人物第一次决策的步尾保存之前，工作区为空；此后任何时刻，工作区里恰有两个普通文件：`MEMORY.md` 与 `memory/episodes.jsonl`。WorldMock 不往工作区写任何其他文件，`AGENT.json`、`config.json`、`memory/state.json`、`life_prompt_snapshot.json` 都不再出现。

### MEMORY.md

【W4】**模板。** `render_memory_view(identity, facts)` 返回的视图恰为

```text
"# Memory\n\n## 身份\n" + 引用(identity)
+ "\n\n## 已核验经历\n" + 事实行
+ "\n\n## 人物自述（未经核验）\n> 暂无人物自述。\n"
```

`引用(text)` 是 `"\n".join("> " + line for line in text.splitlines())`（src/worldmock/agents/memory/life_memory.py:163-165，不变）。`事实行` 是 `facts` 每条一行、以 `"\n"` 相连，没有事实时恰为 `- 暂无已核验经历。`；单行写法沿用 `_fact_line` 与 `_amount`（life_memory.py:168-189，不变）。「人物自述（未经核验）」一段恒为 `> 暂无人物自述。`：工作区没有状态文件，也没有主观归纳，函数不再有 `summary` 参数。`identity` 是人物卡片文本，取值与今天 `persona_card_text(profile, name)`（src/worldmock/agents/prompt/life_context.py:9-14）相同。新视图总长不超过 4000 字（也就是三段标题加身份与事实行组成的文件头不超过 3990 字）时，与今天 `summary` 为空串时的输出（life_memory.py:151-160）逐字相同。今天文件头为 3991 至 3995 字时，自述引用被截短后照样写出；新写法按【W5】拒绝。

【W5】**预算。** `render_memory_view` 是纯函数，不碰文件。视图总长超过 4000 字时，先写错误日志 `Verified memory view exceeds its character budget`，再抛 `ValueError("Verified memory view exceeds its character budget")`。步尾保存在写任何文件之前渲染视图（【W16】），所以预算不通过时一个文件都不写。这一条取代今天的两条：文件头不短于 3996 字时报错（life_memory.py:156-158）与 AgentSociety2 写盘时的 4000 字上限（vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/memory.py:413-422）。

【W6】**写法。** `MEMORY.md` 按 UTF-8 编码、LF 换行直接写字节；文件不存在或现有字节与新视图的字节不同时才写，否则不动文件。任何时候都不写 AgentSociety2 的英文模板（今天首次写入的 `DEFAULT_MEMORY_MD`，vendor/…/agent/memory.py:52-71、:282-283）。今天在 Windows 上 `write_text` 写出 CRLF（vendor/…/agent/memory.py:421），所以 Windows 上的文件字节与 `memory_sha256` 会变，文字不变。

### memory/episodes.jsonl

【W7】**文件写法。** 文件内容恰为全部条目按编号顺序、每条 `json.dumps(record, ensure_ascii=False) + "\n"` 相连，按 UTF-8 编码写字节，换行只有 LF；键依次是 `id`、`agent_id`、`time`、`type`、`importance`、`keywords`、`text`、`source`、`merge_count`、`epistemic_status`，`json.dumps` 用默认分隔符。今天的调用另带 `default=str`（src/worldmock/agents/memory/life_memory_store.py:107、:144），条目里只有 `str`、`int`、`float` 与字符串列表，输出与不带时相同。内容的字节与现有文件不同时才整份写出，相同时不动文件；人物第一次步尾保存时还没有条目，就写出一个空文件。新建与合并走同一条写出路径；在同一组条目上，它写出的文字与今天「新建时追加、合并时重写」（life_memory_store.py:98-107、:138-147）相同，差别只有今天在 Windows 上以文本模式写出的 CRLF 变为 LF。

【W8】**条目构造。** 一条新条目由一条记忆（`control_items` 读出的存储形状 `{type, text, importance, keywords, subject}`，src/worldmock/state/interaction/control_items.py:106-110）构造：

| 键 | 取值 |
| --- | --- |
| `id` | 见【W9】 |
| `agent_id` | 本存储的人物编号 |
| `time` | 本次决策观察时刻 `now.isoformat()`；`now` 是 `DecisionContext.observed_at`，与今天传给 `step` 的时刻相同（vendor/…/agent/runner.py:169） |
| `type` | 记忆的 `type`，原样 |
| `importance` | `float(记忆的 importance)` |
| `keywords` | 每项 `str(item).strip()`，去掉空串，按首次出现去重，保持次序 |
| `text` | `" ".join(记忆的 text.split())` |
| `source` | 常量 `activity_conclusion` |
| `merge_count` | `1` |
| `epistemic_status` | 常量 `subjective` |

`subject` 不写入。这组取值与今天经 AgentSociety2 归一化（vendor/…/agent/memory.py:106-159）再由存储构造（vendor/…/agent/memory.py:503-541）得出的值逐值相同，只是少了被删的五个键。AgentSociety2 那一层的上限（每次至多 8 条、关键词至多 12 个、正文至多 1000 字、`importance` 在 [0, 1]、类型属于它的十一种）今天从不触发：`ConcludeActivityRequest` 已按同样或更严的上限校验（src/worldmock/state/interaction/requests.py:443-447、:489-586），所以去掉那一层不改变任何结果。

【W9】**编号。** 文件第 n 行条目的 `id` 恰为 `f"m_{n:06d}"`，从 `m_000001` 起连续；新条目的编号是当前条目数加一；合并不新建条目，也不占编号。

【W10】**合并。** 合并规则不变：每条记忆按输入次序，从最新一条往前找类型相同、新时刻减去条目时刻落在闭区间 [0, 24 小时]、规范化正文的相邻字符对 Jaccard 不小于 0.6 的第一条（`text_bigrams`、`bigram_jaccard`、`MEMORY_MERGE_JACCARD_THRESHOLD`、`MEMORY_MERGE_WINDOW`，life_memory_store.py:13-43，不变）；两个时刻无法相减（时区可比性不同）时按窗口外处理；找到就把它的 `merge_count` 加一、`time` 改为 `now.isoformat()`，找不到就新建。本次调用里先新建的条目也参加后面记忆的合并判定（与今天 life_memory_store.py:84 相同）。`append_episodes` 返回新建条目与合并目标的深拷贝，新建在前、合并在后（与今天 :92 相同）。

【W11】**写前校验。** 本次新建的每条条目在写出之前，先经与严格读回同一个校验函数 `_validate_record`（【W12】）核对，n 是它将占的行号；任何一条不通过时，文件和存储里的条目都保持调用之前的样子。合并只在条目的副本上进行，文件写成之后才换下存储里的条目。

### 严格读回与恢复

【W12】**严格读回。** `read_episodes(workspace, agent_id)` 读 `memory/episodes.jsonl` 的字节，按 UTF-8 解码，以 `"\n"` 切行：`content.split("\n")` 的最后一段为空串时丢掉它（文件为空或以换行结尾），其余各段依次是第 1 行、第 2 行……；每行 `json.loads`，返回条目元组。不用 `splitlines()`：`json.dumps(ensure_ascii=False)` 把关键词里的 U+2028、U+2029、U+0085 原样写出，`splitlines()` 会在这些字符处断行，读不回存储自己写出的文件。下列条件按表中次序核对，任一不成立先写同文英文错误日志，再抛 `ValueError`（`{n}` 是从 1 起的行号）：

| 条件 | 错误文字 |
| --- | --- |
| 该行去掉空白后为空 | `Memory episode line {n} is blank` |
| 不是对象，或键集合不等于【W7】的十个键 | `Memory episode line {n} has an invalid schema` |
| `id` 不等于 `f"m_{n:06d}"` | `Memory episode line {n} has a non-contiguous identity` |
| `type(item["agent_id"]) is not int`，或不等于参数 `agent_id` | `Memory episode line {n} has inconsistent identity` |
| `type(item["merge_count"]) is not int`，或小于 1 | `Memory episode line {n} has invalid counters` |
| `type` 不在 `SUBJECTIVE_TYPES`（src/worldmock/state/model/memory.py:37-38） | `Memory episode line {n} has an invalid type` |
| `epistemic_status` 不是 `subjective` | `Memory episode line {n} has an invalid epistemic status` |
| `source` 不是 `activity_conclusion` | `Memory episode line {n} has an invalid source` |
| `importance` 是 `bool`、不是 `int` 或 `float`，或不在 [0, 1] | `Memory episode line {n} has invalid importance` |
| `time` 或 `text` 不是非空字符串 | `Memory episode line {n} has empty text fields` |
| `keywords` 不是列表，或有一项不是字符串 | `Memory episode line {n} has invalid string lists` |

不能按 UTF-8 解码与不是 JSON 的行，照原异常（`UnicodeDecodeError`、`json.JSONDecodeError`）抛出。编号一条同时保证连续与唯一。读回不修复、不补默认值、不跳过任何一行。

【W13】**恢复。** 恢复与重放在暂存目录里调用 `restore_workspaces(checkpoint.snapshots, child.assets, staged_run, agent_ids)`，`agent_ids` 照今天写成 `tuple(sorted(int(item) for item in child.manifest["agent_ids"]))`（runner.py:377），按这个次序对每个人物依次：

| 步骤 | 不成立时 |
| --- | --- |
| 取 `snapshots[f"agent:{id}"]` | 先写英文错误日志，再抛 `IntegrityError("Checkpoint is missing an agent workspace snapshot")`（文字同今天 src/worldmock/runtime/lifecycle/runner.py:379-381） |
| `restore_directory` 展开到 `agent_workspace(staged_run, id)`（src/worldmock/recording_engine/storage/checkpoint.py:68-91，不变） | 照它自己的错误 |
| 工作区普通文件的相对路径集合恰为空集，或恰为 `{"MEMORY.md", "memory/episodes.jsonl"}` | 先写英文错误日志，再抛 `IntegrityError("Restored agent workspace members are invalid")` |
| 成员为空时，这个人物的条目是空元组；否则 `MEMORY.md` 按 UTF-8 读出、去掉空白后不为空，再 `read_episodes` | `MEMORY.md` 为空抛 `ValueError("MEMORY.md must not be empty")`；本步任何 `OSError`、`UnicodeError`、`ValueError` 先写英文错误日志 `Restored agent memory is invalid: <原异常>`，再抛 `IntegrityError("Restored agent memory is invalid")`，原异常作 `__cause__` |

全部人物通过之后才发布子目录（发布次序与今天 runner.py:343-351 相同）。函数返回「人物编号 → 条目元组」。这是一次恢复里对工作区的唯一一次校验：`RunHost` 用这份返回值经 `memory_stores` 为每个人物建一个存储，整次运行共用，建存储时不读条目文件；此后各次决策不再校验工作区（存储写出前比较现有字节，见【W7】，那不是校验）。今天一次恢复校验三遍：暂存目录里的 `_restore_agents`（runner.py:384-385）、装配人物时的 `_restored_specs`（runner.py:577-578），以及每个决策重建人物时 AgentSociety2 的 `restore_store`（life_memory.py:55-65）。

【W14】**恢复不再核对身份文件。** 工作区里没有人物卡片、名字、模型身份、续接开关与回合上限的副本，恢复不再逐项核对它们；这些值由冻结的 `persona.toml` 与运行清单给出，它们与当前输入的一致性由已有的 `require_environment_identity`（src/worldmock/simulation_engine/world/run_identity.py:13-23，核对 `persona_sha256` 等，失败报 `Runtime environment identity mismatch`）与 `_require_runtime_identity`（runner.py:278-291，核对模型身份与代码身份）保证。随之消失的错误文字：`Restored agent identity artifact is missing or unsafe`、`Restored agent identity artifact is invalid`、`Restored agent identity does not match the manifest`、`Restored agent same_second_continuation must match the parent manifest`、`Restored LifeAgent must keep memory enabled`、`Restored LifeAgent model configuration must match the parent manifest`、`Restored LifeAgent max_react_turns must match the frozen runtime budget`、`Restored persona profile contains retired taste weights`、`Restored persona card differs from persona.toml`（runner.py:395-445），以及它们的同文日志。

### 步尾保存与「记忆已更新」

【W15】**时机。** 一次决策的回合循环登记了世界请求之后、这一轮提交之前，LifeAgent 依次：建 `MemoryStep`；`save_step(store, step)`；`memory_update_evidence(context, store, outcome)`；结果不为 `None` 时经人物侧 Protocol 的「写记忆证据」方法交给 `LifeEngine.record_memory_update`。这与今天 AgentSociety2 `_run_person_step` 的次序相同（vendor/…/agent/person.py:544-553，LifeAgent 覆写见 custom/agents/life_agent.py:387-398）。回合循环出错时不做步尾保存。证据只在有新建或合并的条目、或者 `MEMORY.md` 的摘要变了时才登记，所以一次运行里 `memory_updated` 的条数与位置都与今天相同。

【W16】**步尾保存。** `save_step` 依次：

| 次序 | 动作 |
| --- | --- |
| 一 | `memory_before_sha256`：`MEMORY.md` 存在时是它当前字节的 SHA-256，不存在时是 `None` |
| 二 | 记下步前的条目编号集合 `known = store.episode_ids()` |
| 三 | `view = render_memory_view(step.identity, step.facts)`（【W4】【W5】；预算不通过在这里抛错，此时还没有写任何文件） |
| 四 | `store.append_episodes(step.memories, step.now)`（【W7】至【W11】；没有记忆时只在文件缺失时写出空文件） |
| 五 | `store.write_memory_view(view)`（【W6】） |
| 六 | 返回的条目按 `id` 去重后分成两组：`id` 不在 `known` 里的是新建，在的是合并（`_classify_episodes`，life_memory.py:192-199，不变） |

返回 `MemoryStepOutcome`，其中 `verified_event_ids` 是 `step.facts` 各条 `source_event_id` 按首次出现去重。今天的次序是先写条目、再渲染（life_memory.py:98-101）；渲染不读存储，提前一步只影响预算不通过这条故障路径，此时运行停止。步尾保存不调用模型，不再核对事实窗口（窗口在取观察时已由 `validate_verified_experiences` 核对，见 [「人物、回合循环与只读问答的删除」](./03-agent-loop.md)），同步执行，不经 `asyncio.to_thread`。今天 `_append_subjective` 在存储返回空列表时报的 `Memory store did not persist finish memories`（life_memory.py:145-147）随之删除：存储由 WorldMock 自己实现，新建与合并的条目按构造返回。

【W17】**数据形状。** 引擎写下的 `memory_updated.data` 恰有十一个键，构造次序（`MEMORY_UPDATE_KEYS`）为：

| 键 | 取值 |
| --- | --- |
| `operation` | 有新建或合并时为 `append_episodes`，否则为 `refresh_summary` |
| `created_episode_ids` | 新建条目的 `id` 列表 |
| `merged_episode_ids` | 合并条目的 `id` 列表 |
| `episodes` | 新建在前、合并在后，每条恰为四键 `{id, type, text, epistemic_status}`，值取 `str()` |
| `memory_before_sha256` | 【W16】的写入前摘要；人物在整条运行谱系里的第一条 `memory_updated` 为 `null`，此后每条都等于本人上一条的 `memory_sha256` |
| `memory_sha256` | 写入后 `MEMORY.md` 字节的 SHA-256 |
| `episodes_sha256` | 写入后 `memory/episodes.jsonl` 字节的 SHA-256 |
| `observation_event_id` | 本决策冻结观察的事件身份 |
| `verified_event_ids` | 【W16】的 `verified_event_ids` |
| `trigger` | 环境推导：`[]` 或 `["activity_conclusion"]` |
| `decision_id` | 本决策身份 |

`consolidated`、`reflection`、`state_sha256` 三个键不再出现。事件以规范 JSON 落库（键按字典序），构造次序只是代码里的写法。

【W18】**环境核验。** `LifeEngine.record_memory_update` 照今天的位置核验（src/worldmock/simulation_engine/evidence/model.py:138-188），规则改为：`operation` 属于 `{append_episodes, refresh_summary}`；`memory_before_sha256` 为 `None` 或六十四位小写十六进制，`memory_sha256`、`episodes_sha256` 为六十四位小写十六进制；`observation_event_id` 为非空字符串；新建与合并的编号、已核验事件身份都是非空字符串，新建与合并的编号合起来无重复，已核验事件身份无重复；`append_episodes` 至少有一个编号；`refresh_summary` 没有编号，且写入前后摘要不同；快照恰为四键并与编号一一对应；观察属于本决策，`verified_event_ids` 等于观察里 `verified_experiences` 的来源集合；写入点闭集 `MEMORY_WRITE_POINTS` 恰为 `("activity_conclusion",)`。任一不成立先写日志 `Memory update evidence rejected: <原因>`，再抛 `ProtocolError("Memory update evidence is invalid")`（与今天相同，model.py:145-147），库里不多一条事件；`_memory_evidence_data` 内部的日志 `Memory update evidence is invalid` 与 `ProtocolError("Memory update payload is invalid")`（memory_evidence.py:69-70）、观察与窗口的两种 `ProtocolError` 文字 `Memory update observation is invalid`（model.py:177、:180）与 `Memory update source window is invalid`（model.py:183）都不变。带第五个键 `source` 的快照不再是合法快照，今天的专门拒绝 `Step memory update carries a reflection episode`（model.py:184-185）随之删除。运维日志 `MEMORY_UPDATED` 的文字不变（model.py:158-166）。

【W19】**质量报告。** 质量报告的记忆核对（src/worldmock/recording_engine/reports/quality.py:392-607）对每条 `memory_updated`：数据键集合不等于 `MEMORY_UPDATE_KEYS` 的集合、`memory_before_sha256` 既不是 `null` 也不是摘要、或其余形状不符时，记 `invalid_memory_update`；`trigger` 只认闭集 `("activity_conclusion",)`，含 `reflection` 的列表按形状不符记 `invalid_memory_update`。就寝反思的重算（睡醒、本地日、归属日期、同刻只登记一次）全部删除，每条更新都核对观察与事实窗口。报告的键与问题码集合不变：`memory_update_events`、`persisted_episode_ids`、`write_points_checked`、`problem_count`、`problems`；`memory_update_without_source` 只剩「收尾写入点没有同一决策的 conclude_activity」和「写入了条目却没有写入点」两种来源。质量报告结构号不变。

### 共同检查点

【W20】**人物快照。** 每个共同检查点由 `RunHost` 调 `snapshot_workspaces(run_dir, agent_ids, store.assets)` 得到 `{f"agent:{id}": AssetRef}`，再交给 `LifeEngine.checkpoint`（src/worldmock/simulation_engine/engine.py:172-191，不变）。快照格式一字不改（checkpoint.py:29-65）：只收普通文件，按相对路径排序，tar 成员权限 0600、修改时刻 0，gzip 级别 6、头部固定 `1f 8b 08 00 00 00 00 00 00 ff`，素材 `media_type` 为 `application/gzip`、来源 `{kind: workspace}`；检查点参与者恰为全部 `agent:<id>` 与 `life`（src/worldmock/recording_engine/storage/store.py:576-580，不变）。导出与认证仍把快照当按摘要和字节数核对的不透明素材（src/worldmock/recording_engine/validation/validation.py:198-199、src/worldmock/recording_engine/export/export.py:112-118，不变）。

## 接口与文件

### 生产文件

| 路径 | 动作 | 内容 |
| --- | --- | --- |
| `src/worldmock/agents/memory/workspace.py` | 新建 | 人物工作区：路径、新建、快照、恢复（唯一一次校验）、建存储，接口见下 |
| `src/worldmock/agents/memory/life_memory_store.py` | 改写 | 去掉对 `agentsociety2.agent.memory.AgentMemoryStore` 的继承与导入（:11）；`WorldMockMemoryStore` 改为不继承的类；吸收条目构造、归一化与严格读回；保留 :13-43 原样 |
| `src/worldmock/agents/memory/life_memory.py` | 改写 | 删除 :5 的 `import asyncio`、:9 的 `TYPE_CHECKING` 与 :16-17 对 `custom.agents.life_agent` 的类型导入、:11-12 的两行 AgentSociety2 导入、:23-30 的 `_NO_MODEL_CALL` 与 `_refuse_model_call`、:33-148 的 `WorldMockMemoryRuntime` 整类（含 `ensure_store`、`restore_store`、`build_context`、`after_step`、`_current_facts`、`_validate_subjective_memories`、`_append_subjective`）、:19-20 的 `_CONCLUSION_SOURCE`；:14 只留 `valid_subjective_snapshot` 的导入（`SUBJECTIVE_TYPES` 只被被删的方法用）；新增 `MemoryStep`、`MemoryStepOutcome`、`save_step`；`render_memory_view` 去掉 `summary` 参数；`memory_update_evidence` 改为构造 WorldMock 的 `MemoryUpdateEvidence`；:21-22 的 `_SNAPSHOT_FIELDS`、:163-199 与 :221-232 的私有函数保留；模块文档串改为「WorldMock 人物的步尾记忆保存与工作区视图」 |
| `src/worldmock/recording_engine/events/memory_evidence.py` | 改 | `MEMORY_WRITE_POINTS` 改为 `("activity_conclusion",)`（:14）；新增 `MEMORY_UPDATE_KEYS` 与 `MemoryUpdateEvidence`；`memory_write_points` 行为不变、文档串删去反思一句（:27）；`_memory_evidence_data`（:49-79）按【W18】改，文档串（:50）改为「三个文件身份；写入前摘要可为空」并删去 `consolidated` 一句，返回按 `MEMORY_UPDATE_KEYS` 前九个键次序构造的字典 |
| `src/worldmock/simulation_engine/evidence/model.py` | 改 | `record_memory_update` 文档串删去「reflection 写 null」（:139-140）；`_tail_registration` 删去 :184-185 的反思快照拒绝，:188 改为 `return {"trigger": memory_write_points(requests, context.decision_id, written)}`；文档串（:169）删去「没有反思条目」 |
| `src/worldmock/state/model/memory.py` | 改 | `valid_subjective_snapshot`（:45-54）只接受恰为四键的快照，删去 :50-51 的 `source == "reflection"` 分支；:12 与 :40 两条注释、:46 的文档串改为「主观快照恰为四个基本键」 |
| `src/worldmock/recording_engine/reports/quality.py` | 改 | 见下文「质量报告的改动」 |
| `src/worldmock/runtime/lifecycle/runner.py` | 改（与「运行宿主与决策轮」一章同改此文件，本章只管下列各处） | 删除 `_restore_agents`（:371-385）、`_validate_agent_workspace`（:388-429）、`_validate_persona_card_struct`（:432-445）、`_restored_specs`（:569-581），以及随之不再使用的 `import json`（:8）与类型导入 `PersonaConfig`（:28）；`_restore_branch`（:328-358）去掉 `inputs` 参数，在函数内与 `restore_branch`、`FootprintStore` 一样局部导入 `restore_workspaces`，:345 改为 `records = restore_workspaces(checkpoint.snapshots, child.assets, staged_run, tuple(sorted(int(item) for item in child.manifest["agent_ids"])))`，返回 `(manifest, state, records)`，文档串（:335）的「人物的结构化卡片须与冻结的人设表一致」改为「人物工作区按快照恢复并严格读回」；`_PreparedRun` 增字段 `memory_records: Mapping[int, tuple[dict[str, Any], ...]]`（在类里的位置随「运行宿主与决策轮」一章对这个类的改写）；`_prepare_new_run` 填 `{agent_id: () for agent_id in identity.agent_ids}`；`_prepare_resume` 的 :254 改为 `manifest, state, records = _restore_branch(parent, checkpoint, destination, metadata)` 并填 `records`，文档串（:232）删去「人物卡片沿用工作区里冻结的那一份」 |
| `src/worldmock/runtime/framework/setup.py` | 改（与「人物、回合循环与只读问答的删除」「安装、代码身份、依赖与许可」两章同改此文件，本章只管 `config`） | `creation_inputs`（:198-215）删去 :211-213 的 `config` 字典，:214 的人物记录去掉 `"config"` 键，文档串删去「原生工作区输入」 |
| `custom/agents/life_agent.py` | 由「人物、回合循环与只读问答的删除」一章搬迁改写 | 本章删除的读写点：:17、:31、:44 三行导入，:87 `_CONCLUSION_MEMORY_SOURCE`，:306-308 `_persona_card_text`，:322-325 `_new_memory_runtime`，:387-398 `_record_memory_update`，:460 写 `_last_finish_memories`，:623-635 `_conclusion_memories` 加来源与认知状态、剥 `subject` 的部分；那一章照【W15】调用本章接口 |
| `custom/envs/life_env.py` | 由「去掉 LifeEnv」一章删除 | 本章接手的部分：:215-231 `_checkpoint` 里的逐人物 `snapshot_directory` 改由 `snapshot_workspaces` 承担；:198-200 的转发由引擎方法直接承担 |
| `src/worldmock/agents/prompt/life_persona.py` | 由「人物、回合循环与只读问答的删除」一章改 | `life_prompt_snapshot.json` 的写方 `write_prompt_snapshot`（:68-71）、读方 `readonly_layers`（:74-81）与 `PROMPT_SNAPSHOT_FILE`（:25-26）随只读问答删除 |

### 被删四个文件的全部读写点

| 文件 | 写方（今天） | 读方（今天） | 之后 |
| --- | --- | --- | --- |
| `AGENT.json` | AgentSociety2 `create`、`persist_agent_json`（vendor/…/agent/base/agent.py:160-210、:720-781，vendor/…/agent/person.py:279-322、:484、:535）；人物卡片来自 `creation_inputs` 的 `profile`（setup.py:204-210）；测试夹具 `write_agent_workspace`（tests/integration/runtime_fixtures.py:12-24） | AgentSociety2 `restore`（vendor/…/agent/base/agent.py:231-299）供 LifeAgent `get_profile()`（life_agent.py:275-281、:308、:342、:358、:362）；`_validate_agent_workspace`（runner.py:395-422）；`_restored_specs`（runner.py:579）；运行比对 `AGENT_MASKS`（scripts/compare_runs.py:56-58、:671-673）；完整日基线打桩（tests/dayrun_baseline.py:30、:41） | 不再存在；卡片、名字、模型身份、续接开关由人物一章的冻结输入给出；比对屏蔽由工具一章删除；打桩由本章删除 |
| `config.json` | AgentSociety2 `create`（vendor/…/agent/base/agent.py:176-179）写 `creation_inputs` 的 `config`（setup.py:211-213）；测试夹具 | AgentSociety2 `restore`（vendor/…/agent/base/agent.py:250-252、:275-279）取回合上限；`_validate_agent_workspace`（runner.py:396-422）；`_restored_specs`（runner.py:580） | 不再存在；`creation_inputs` 不产 `config`；回合上限取运行清单 `stage_model_identity.max_react_turns`（人物一章） |
| `memory/state.json` | AgentSociety2 `ensure`、`write_state`（vendor/…/agent/memory.py:284-285、:362-375），计数由 `WorldMockMemoryStore.append_episodes` 更新（life_memory_store.py:61-62、:87-91） | `read_subjective_summary`（life_memory.py:100）；`state_sha256`（life_memory.py:216）；`validate_existing`（vendor/…/agent/memory.py:314-334，经 runner.py:423-429 与每次决策的 `restore_store`） | 不再存在；条目数就是文件行数（【W9】、【W12】）；人物自述是常量（【W4】） |
| `life_prompt_snapshot.json` | `write_prompt_snapshot`（life_persona.py:68-71），调用于 life_agent.py:364-365 | `readonly_layers`（life_persona.py:74-81），调用于 life_agent.py:342 | 随只读问答删除（人物一章） |

### 接口

`src/worldmock/agents/memory/life_memory_store.py`：

```python
MEMORY_MERGE_JACCARD_THRESHOLD = 0.6               # 不变
MEMORY_MERGE_WINDOW = timedelta(hours=24)          # 不变
#: 工作区里的记忆视图与条目文件（相对工作区根，POSIX 写法）。
MEMORY_VIEW_FILE = "MEMORY.md"
EPISODES_FILE = "memory/episodes.jsonl"
#: 条目的来源，由系统按写入点固定（与 memory_updated.data.trigger 的取值同名）。
EPISODE_SOURCE = "activity_conclusion"
#: 一条条目的十个键，按写出次序。
PERSISTED_EPISODE_FIELDS = ("id", "agent_id", "time", "type", "importance", "keywords",
                            "text", "source", "merge_count", "epistemic_status")

def text_bigrams(text: str) -> set[tuple[str, str]]: ...           # 不变
def bigram_jaccard(left: str, right: str) -> float: ...             # 不变
def read_episodes(workspace: Path, agent_id: int) -> tuple[dict[str, Any], ...]: ...   # 【W12】

class WorldMockMemoryStore:
    def __init__(self, workspace: Path, agent_id: int, records: Sequence[Mapping[str, Any]]) -> None: ...
    workspace: Path          # 人物工作区根
    agent_id: int            # 人物编号
    @property
    def memory_md_path(self) -> Path: ...      # workspace / MEMORY_VIEW_FILE
    @property
    def episodes_path(self) -> Path: ...       # workspace / EPISODES_FILE
    def episode_ids(self) -> set[str]: ...     # 存储里全部条目的 id，直接读内存条目，不复制
    def append_episodes(self, memories: Sequence[Mapping[str, Any]], now: datetime) -> list[dict[str, Any]]: ...
    def write_memory_view(self, view: str) -> bool: ...   # 写了返回 True，字节相同返回 False（【W6】）

def _validate_record(item: Any, line_number: int, agent_id: int) -> None: ...   # 【W12】表中自第二行起的逐条核对
def _write_if_changed(path: Path, text: str) -> bool: ...
```

`_write_if_changed` 把 `text.encode("utf-8")` 与现有文件比较：文件存在且字节相同返回 `False`；否则 `path.parent.mkdir(parents=True, exist_ok=True)`、`path.write_bytes(...)`、返回 `True`。`MEMORY.md` 与条目文件都经它写，这是存储里唯一的写文件处。

构造函数深拷贝 `records` 存为私有列表 `_records`，不读任何文件。私有方法 `_new_record(memory, number, now)` 构造【W8】的条目，`_find_merge_target`、`_within_merge_window` 保留今天的算法（life_memory_store.py:109-136），后者照旧是静态方法。`append_episodes` 的步骤：深拷贝 `_records` 得到工作副本；按【W10】逐条记忆在副本上合并或新建，新建条目经 `_validate_record` 核对；全部通过后把副本按【W7】序列化，经 `_write_if_changed` 写出；写成之后副本换下 `_records`；返回新建与合并目标的深拷贝。每处抛错之前写同文英文错误日志。

`src/worldmock/agents/memory/life_memory.py`：

```python
@dataclass(frozen=True, slots=True)
class MemoryStep:
    """@brief 一次决策步尾保存的全部输入。"""
    identity: str                              # 人物卡片文本，MEMORY.md「身份」段
    facts: tuple[dict[str, Any], ...]          # 本决策冻结观察的已核验经历，至多五条，已按事件序排好
    observation_event_id: str                  # 本决策冻结观察的事件身份
    memories: tuple[Mapping[str, Any], ...]    # conclude_activity 经 control_items 读出的存储形状记忆，原样
    now: datetime                              # 本决策的观察时刻（DecisionContext.observed_at）

@dataclass(frozen=True, slots=True)
class MemoryStepOutcome:
    """@brief 步尾保存的结果。"""
    created_episodes: tuple[dict[str, Any], ...]   # 本步新建的条目
    merged_episodes: tuple[dict[str, Any], ...]    # 本步合并的既有条目
    memory_before_sha256: str | None               # 写入前 MEMORY.md 的摘要，文件不存在为 None
    observation_event_id: str                      # 本决策冻结观察
    verified_event_ids: tuple[str, ...]            # 进入事实视图的来源事件身份

def save_step(store: WorldMockMemoryStore, step: MemoryStep) -> MemoryStepOutcome: ...       # 【W16】
def render_memory_view(identity: str, facts: Sequence[Mapping[str, Any]]) -> str: ...       # 【W4】【W5】
def memory_update_evidence(context: DecisionContext, store: WorldMockMemoryStore,
                           outcome: MemoryStepOutcome) -> MemoryUpdateEvidence | None: ...
```

`memory_update_evidence` 的判定与今天相同（life_memory.py:202-218）：没有新建、没有合并、写入前摘要等于当前 `MEMORY.md` 摘要时返回 `None`；否则以 `outcome.memory_before_sha256` 原值（可为 `None`）、当前 `MEMORY.md` 与条目文件的摘要构造证据，快照经 `_episode_snapshot`（仍以 `RuntimeError("Subjective memory snapshot is invalid")` 拒绝不合法快照）。

`src/worldmock/agents/memory/workspace.py`：

```python
#: 运行目录下人物工作区的根目录名。
WORKSPACES_DIR = "agents"
#: 第一次决策之后工作区的完整成员；第一次决策之前工作区为空。
WORKSPACE_MEMBERS = frozenset({MEMORY_VIEW_FILE, EPISODES_FILE})

def agent_workspace(run_dir: Path, agent_id: int) -> Path: ...                        # 【W1】
def create_workspaces(run_dir: Path, agent_ids: Sequence[int]) -> None: ...           # 【W2】
def snapshot_workspaces(run_dir: Path, agent_ids: Sequence[int],
                        assets: AssetStore) -> dict[str, AssetRef]: ...               # 【W20】
def restore_workspaces(snapshots: Mapping[str, AssetRef], assets: AssetStore, run_dir: Path,
                       agent_ids: Sequence[int]) -> dict[int, tuple[dict[str, Any], ...]]: ...   # 【W13】
def memory_stores(run_dir: Path, records: Mapping[int, Sequence[Mapping[str, Any]]]
                  ) -> dict[int, WorldMockMemoryStore]: ...
```

`snapshot_workspaces` 按 `agent_ids` 的次序返回 `{f"agent:{id}": snapshot_directory(agent_workspace(run_dir, id), assets)}`（`snapshot_directory` 不变）。`restore_workspaces` 对每个人物调 `restore_directory(snapshots[f"agent:{id}"], assets, agent_workspace(run_dir, id))`（不变）后做【W13】的核对。`memory_stores` 按人物编号升序返回 `{id: WorldMockMemoryStore(agent_workspace(run_dir, id), id, records[id])}`，人物集合就是 `records` 的键。私有函数 `_workspace_members(workspace) -> frozenset[str]` 列出工作区全部普通文件的相对 POSIX 路径；`_restored_records(workspace, agent_id, members)` 做【W13】表中末行。模块导入 `snapshot_directory`、`restore_directory`（`worldmock.recording_engine.storage.checkpoint`）、`AssetStore`、`AssetRef`、`IntegrityError` 与 `life_memory_store` 的 `MEMORY_VIEW_FILE`、`EPISODES_FILE`、`read_episodes`、`WorldMockMemoryStore`，不导入引擎。

`src/worldmock/recording_engine/events/memory_evidence.py`：

```python
#: 记忆写入点闭集，按此顺序列出本步实际发生的写入点。
MEMORY_WRITE_POINTS = ("activity_conclusion",)
#: memory_updated.data 的十一个键，按引擎构造次序。
MEMORY_UPDATE_KEYS = ("operation", "created_episode_ids", "merged_episode_ids", "episodes",
                      "memory_before_sha256", "memory_sha256", "episodes_sha256",
                      "observation_event_id", "verified_event_ids", "trigger", "decision_id")

@dataclass(frozen=True, slots=True)
class MemoryUpdateEvidence:
    """@brief 人物工作区已落盘记忆的内容身份（步尾登记）。"""
    context: DecisionContext                          # 本决策的上下文
    operation: Literal["append_episodes", "refresh_summary"]
    created_episode_ids: tuple[str, ...]
    merged_episode_ids: tuple[str, ...]
    episodes: tuple[dict[str, str], ...]              # 四键快照，新建在前
    memory_before_sha256: str | None
    memory_sha256: str
    episodes_sha256: str
    observation_event_id: str
    verified_event_ids: tuple[str, ...]
```

字段次序是 AgentSociety2 `MemoryUpdateEvidence`（vendor/…/agent/base/react.py:97-126）去掉 `state_sha256` 与 `consolidated`。`DecisionContext` 从 `worldmock.state.decisions.round` 导入（见「运行宿主与决策轮」）。

### 质量报告的改动

| 位置 | 改动 |
| --- | --- |
| quality.py:46 | 删去这条孤立注释（「…结束活动与就寝反思」） |
| quality.py:107 | 调用改为 `_memory_quality(events)` |
| quality.py:392-423 `_memory_quality` | 去掉参数 `timezone_name`；状态字典去掉 `wakes`、`reflected`、`zone`；检查器表去掉 `"activity_progress": _inspect_sleep_wake`；文档串只写结束活动一个写入点 |
| quality.py:477-482 `_inspect_sleep_wake`、:485-497 `_reflection_sourced`、:600-607 `_valid_update_binding` | 删除 |
| quality.py:511-533 `_inspect_memory_update` | :530 去掉 `data.get("reflection") is None and` |
| quality.py:546-562 `_check_write_points` | 删去 :559-560；文档串删去反思一句 |
| quality.py:565-591 `_valid_memory_update_payload` | 开头核对 `set(data) == set(MEMORY_UPDATE_KEYS)`；摘要核对改为 `memory_sha256`、`episodes_sha256` 是摘要，`memory_before_sha256` 是摘要或 `None`；去掉 `consolidated` 的类型核对；`observation_event_id` 须为非空字符串（取代 `_valid_update_binding`） |
| quality.py 导入 | :15 加 `MEMORY_UPDATE_KEYS`；`ZoneInfo`（:24，只用在 :402 的 `zone`）与 `timedelta`（:22，只用在 :497）随之删去导入，`datetime`、`timezone` 仍有他用（:154、:733、:740） |

调用类 `consolidation`、`reflection`（quality.py:54、:823）属于模型请求分类，不读 `memory_updated`，本章不动。

### 随本章删除、移动或新增的错误文字

| 文字 | 今天的位置 | 之后 |
| --- | --- | --- |
| `WorldMock memory runtime does not call the model` | life_memory.py:24、:27-30 | 删除 |
| `Memory store was already bound` | life_memory.py:60-61 | 删除 |
| `Current verified experience window is unavailable`、`Current verified experience window is invalid`、`Current verified experience order is invalid` | life_memory.py:113-121 | 删除（【W16】） |
| `WorldMock conclusion memory payload is invalid`、`WorldMock conclusion memory must be subjective and system-sourced` | life_memory.py:129、:133-134 | 删除；非主观类型由写前校验以 `Memory episode line {n} has an invalid type` 拒绝（【W11】） |
| `Memory store did not persist finish memories` | life_memory.py:146-147；vendor/…/agent/memory_runtime.py:373 | 删除（【W16】） |
| `Memory store received an empty memory text`（`RuntimeError`） | life_memory_store.py:69-70 | 改为 `ValueError("Memory episode line {n} has empty text fields")`（【W11】） |
| `Memory workspace artifact is missing or unsafe: {path}`、`Memory state schema is invalid`、`Memory state schema version is invalid`、`Subjective memory summary must be a string`、`Memory state counters must be non-negative integers`、`Last consolidated tick is invalid`、`Last consolidated memory identity is invalid`、`Pending memory count exceeds the episode counter`、`Memory episode identities must be unique`、`Memory episode counter does not match persisted records`、`Last consolidated memory episode does not exist`、`Memory view is invalid or exceeds its character budget` | vendor/…/agent/memory.py:314-360、:417 | 随 AgentSociety2 消失 |
| `Memory episode line {n} is blank`、`has an invalid schema`、`has inconsistent identity`、`has invalid counters`、`has an invalid type`、`has an invalid epistemic status`、`has invalid importance`、`has empty text fields`、`has invalid string lists` | vendor/…/agent/memory.py:580-622 | 文字不变，移进 `life_memory_store.py`（【W12】） |
| `Memory episode line {n} has a non-contiguous identity`、`Memory episode line {n} has an invalid source` | — | 新增（【W12】） |
| `MEMORY.md must not be empty` | vendor/…/agent/memory.py:323 | 文字不变，移进 `workspace.py`（【W13】） |
| `Restored agent workspace members are invalid` | — | 新增，`IntegrityError`（【W13】） |
| `Checkpoint is missing an agent workspace snapshot` | runner.py:381 | 文字不变，移进 `workspace.py`，抛出前先写同文日志（【W13】） |
| `Restored agent memory is invalid` | runner.py:429 | 文字不变，移进 `workspace.py`，抛出前先写日志 `Restored agent memory is invalid: <原异常>`（【W13】） |
| `Agent workspace already exists: {path}` | vendor/…/society/society.py:204 | 文字不变，由 `workspace.py` 按人物目录抛出（【W2】） |
| 九条 `Restored agent …`、`Restored LifeAgent …`、`Restored persona …` | runner.py:395-445 | 删除（【W14】） |
| `Step memory update carries a reflection episode` | model.py:184-185 | 删除（【W18】） |
| `Verified memory view exceeds its character budget` | life_memory.py:156-158 | 文字不变，条件改为视图总长超过 4000 字（【W5】） |

### 许可注明

`life_memory_store.py`（记忆条目的归一化与严格读回，改写自锁定源码的 agentsociety2/agent/memory.py）与 `workspace.py`（人物工作区的建立与重建，改写自 agentsociety2/agent/base/agent.py 与 society/society.py）两个模块的文档串，最后一段单独一行，逐字写[「安装、代码身份、依赖与许可」](./07-install-identity.md)【I24】规定的那一句，不另写来源句：

```text
来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。
```

两个模块与 `life_memory.py` 的其余文档串、注释都不点名 AgentSociety2、`agentsociety` 或 AS2（07 章【I26】）。

### 逐字节不变的部分

| 对象 | 说明 |
| --- | --- |
| 发给 DeepSeek 的请求 | 记忆层与 `search_memory` 不读工作区 |
| `MEMORY.md` 的文字 | 【W4】；字节差别只有 Windows 上原来的 CRLF 变为 LF |
| 条目的保留字段取值、键序与合并结果 | 【W7】、【W8】、【W10】；差别只有去掉的五个键、编号写法与 Windows 上的换行 |
| `memory_updated` 的条数与位置、运维日志 `MEMORY_UPDATED` | 【W15】、【W18】 |
| 快照格式、检查点载荷格式与参与者、导出与认证代码 | 【W20】 |
| `checkpoint.py`、`store.py` 的检查点代码、`engine.py:172-191` | 一行不改 |

### 测试辅助文件

| 路径 | 动作 | 内容 |
| --- | --- | --- |
| `tests/integration/runtime_fixtures.py` | 改 | `write_agent_workspace(workspace: Path) -> Path` 只剩一个参数：`workspace.mkdir(parents=True)` 后返回，得到首次决策之前的空工作区；删去 `agent_document`、`config_document`、`missing`、`initialized` 四个参数、:5 的 `import json` 与 `_created_persona_profile`（:27-35）；带这些参数的调用处 `tests/integration/runtime/test_runtime_inputs_contract.py:48` 与 `tests/system/cli/test_cli_clean_process.py:98` 去掉这些参数，`test_runtime_inputs_contract.py:218` 随它所在的用例被取代（【W13】）；其余只传目录的调用处（共 18 个文件引用这个函数）不改 |
| `tests/dayrun_baseline.py` | 改 | 删去 :30 的导入与 :41 的打桩；文档串里「固定记录身份与计时」不变 |

## 设计文档

全文由[「设计文档与手册」](./08-design-docs.md)写；本章列出各处之后必须陈述的事实。

| 位置（基线行号） | 之后必须写明 |
| --- | --- |
| docs/design/05-recording-engine.md:104-106 | `memory_updated` 的十一个键及次序（【W17】），「三个文件身份」取代「四个文件身份」 |
| docs/design/05-recording-engine.md:108-114 | 要求表：`observation_event_id` 非空且是本决策的观察；`trigger` 为 `[]` 或 `["activity_conclusion"]`；`memory_before_sha256` 为摘要或 `null`，人物第一条为 `null`、此后等于本人上一条的 `memory_sha256`；快照恰为四键；删去 `consolidated`、`reflection` 两行与「没有来源为 reflection 的条目」 |
| docs/design/05-recording-engine.md:116 | 保留：拒绝文字与「每一步至多一条」 |
| docs/design/05-recording-engine.md:118-124 | 整段删除（历史运行的重新认证与反思来源重算）；写明质量报告对每条更新核对键集合、观察与事实窗口（【W19】） |
| docs/design/05-recording-engine.md:440 | 路径改为 `agents/agent_<编号>/MEMORY.md` |
| docs/design/05-recording-engine.md:625-627 | 恢复只校验一次：成员为空或恰为两个文件、`MEMORY.md` 非空、条目逐行严格读回（键集合、编号自 `m_000001` 起连续、人物、类型、来源、认知状态、重要度、文字与关键词），错误文字 `Restored agent workspace members are invalid`、`Restored agent memory is invalid`，在发布子目录之前停止；人物卡片与模型身份不在工作区，由运行清单与 `persona.toml` 摘要核对。:620-624 的快照格式不变 |
| docs/design/05-recording-engine.md:763、:766 | 删去「人物工作区 AGENT.json 的 `initialized_at`」「人物工作区里的模型服务地址」「AGENT.json 的 `workspace.root`」；:757 的 `MEMORY.md` 来源事件映射保留 |
| docs/design/09-io-formats.md:77-78 | 删去「保存在人物工作区 `AGENT.json` 的 `profile` 里，`AGENT.json` 的结构号是 1」；改为卡片在运行开始时由 `persona.toml` 直接构造、不写进人物工作区 |
| docs/design/09-io-formats.md「事件与检查点」之后（:117-124 之后新起一节「人物工作区」） | 路径（【W1】）、成员（【W3】）、`MEMORY.md` 模板与预算（【W4】【W5】）、UTF-8 与 LF、内容不变不写（【W6】【W7】）、条目十个键及次序与取值（【W7】【W8】）、编号格式（【W9】）、合并规则（【W10】）、条目文件按 `"\n"` 切行的严格读回及其错误文字（【W12】） |
| docs/design/06-agent.md:34-39 | 新建运行不把卡片写进工作区；两份派生物作为冻结输入交给 LifeAgent，卡片文本供 `MEMORY.md`「身份」段；删去恢复时核对工作区卡片的一句与错误文字，改述 `persona.toml` 摘要由运行清单核对（`Runtime environment identity mismatch`） |
| docs/design/06-agent.md:729 | 「不读写人物工作区与记忆存储」取代「…与记忆运行时」 |
| docs/design/06-agent.md:748 | 「与新建运行写进工作区 profile 的 `persona_card` 逐字相同」改为「与运行开始时交给 LifeAgent 的卡片文本逐字相同」（人物一章的写法；`MEMORY.md`「身份」段用的是去掉首尾空白的卡片文本，第 10 章「跨章裁定」） |
| docs/design/06-agent.md:893 | 身份、预算与卡片冻结在运行清单与 `persona.toml` 里，不在工作区 |
| docs/design/06-agent.md:903-910 | 步尾保存的次序与登记条件（【W15】【W16】）；「人物自述（未经核验）」恒为「暂无人物自述。」保留；删去 :908（模型调用入口只会失败）与 :909（`sleep_boundary`） |
| docs/design/08-configuration.md:179-180 | 恢复和重放核对冻结的 `persona.toml` 摘要与运行清单一致，取代「核对工作区里的卡片」 |
| docs/manual/runtime.md:78 | 不变（「人物的 MEMORY.md 总结要同时拖入运行目录」仍然成立） |

## 测试

全部离线，不跳过、不标 xfail。引擎相关条款走真实入口：`engine_factory`（tests/engine_fixture.py）或运行级的 `native_runtime` 夹具（tests/integration/runtime/native_runtime_fixtures.py，由工具一章改为驱动 `RunHost` 与脚本化 `ModelClient`），以及真实请求、引擎与 SQLite；`host_run`（tests/integration/host_run_fixtures.py，由工具一章改写为新 LifeAgent）驱动人物决策。

### 条款与测试

| 条款 | 测试 | 新/改 |
| --- | --- | --- |
| W1 | tests/integration/runtime/test_agent_workspace.py::test_a_new_run_keeps_agent_workspaces_under_agents_only | 新 |
| W2 | tests/integration/runtime/test_agent_workspace.py::test_new_run_workspaces_start_empty_and_the_initial_snapshot_is_an_empty_archive | 新 |
| W2 | tests/integration/runtime/test_agent_workspace.py::test_creating_an_existing_workspace_is_refused | 新 |
| W3 | tests/integration/runtime/test_agent_workspace.py::test_a_decided_run_leaves_exactly_the_two_memory_files | 新 |
| W3 | tests/integration/runtime/test_native_dataset_lifecycle.py::test_native_dataset_run_resume_and_replay_finish_with_real_frozen_workspaces（:53-54 的 `AGENT.json` 读取改为断言工作区恰为两个文件；模型元数据改由运行清单 `stage_model_identity.model_metadata` 断言） | 改 |
| W3 | tests/integration/runtime/test_persona_profile.py::test_new_run_makes_no_model_request_for_the_persona_and_shows_the_table_in_the_persona_layer（`_created_run` 的 :215 改为断言工作区恰为两个文件；`run["profile"]` 一句删去，人设层断言保留） | 改 |
| W4 | tests/unit/agents/test_life_memory.py::test_memory_view_matches_the_exact_template_with_the_constant_self_description | 新 |
| W4 | tests/unit/agents/test_life_memory.py::test_received_memory_line_preserves_actual_goods_quantity_and_source（去掉第三个参数，断言自述段为常量） | 改 |
| W4 | tests/unit/agents/test_life_memory.py::test_retired_consumed_kind_cannot_render_as_a_verified_fact（去掉第三个参数） | 改 |
| W4 | tests/integration/runtime/test_persona_profile.py::test_memory_view_identity_is_the_card_text_rendered_from_the_persona_table（身份取人物一章由 `persona.toml` 建出的冻结输入里的卡片文本，经 `save_step` 写进临时工作区；「## 身份」段逐行等于 `render_card(persona_card(persona))` 的引用，原有短语断言保留） | 改 |
| W5 | tests/unit/agents/test_life_memory.py::test_a_view_over_4000_characters_is_refused_and_nothing_is_written（身份文本使视图恰为 4001 字、带一条记忆调 `save_step`：抛 `ValueError`，工作区里没有 `MEMORY.md` 也没有条目文件；视图恰为 4000 字时照常写出） | 新 |
| W5 | tests/integration/runtime/test_verified_memory.py::test_oversized_fact_view_fails_instead_of_truncating_verified_evidence（去掉第三个参数） | 改 |
| W6 | tests/unit/agents/test_life_memory.py::test_first_step_save_has_no_previous_view_digest_and_writes_no_template | 新 |
| W6 | tests/unit/agents/test_life_memory.py::test_view_is_written_only_when_its_content_changes | 新 |
| W7 | tests/unit/agents/test_life_memory_store.py::test_new_records_have_the_ten_fields_in_order_on_lf_lines | 新 |
| W7 | tests/unit/agents/test_life_memory_store.py::test_episodes_file_is_written_only_when_its_bytes_change | 新 |
| W8 | tests/unit/agents/test_life_memory_store.py::test_record_normalization_collapses_text_and_dedupes_keywords | 新 |
| W8 | tests/integration/runtime/test_conclude_memories.py::test_views_persist_as_memories_byte_identical_to_the_subject_shape（改为：旧请求形状（`memories` 里带 `subject`）与现行形状（`views` 带 `about`）经 `control_items` 读出的同一组记忆各经 `save_step` 写进两个临时工作区，两份 `memory/episodes.jsonl` 逐字节相同、不含 `"subject"`；第一行等于测试里写定的字面行，这一行是基线代码对同一条记忆写出的行去掉五个键、编号改为 `m_000001` 所得） | 改 |
| W8 | tests/integration/runtime/test_conclude_memories.py::test_store_fixes_source_and_status_and_refuses_non_subjective_types（取代 `test_memory_runtime_accepts_only_system_sourced_conclusion_memories`：输入里的 `source`、`epistemic_status` 不被读取，`type` 为 `event` 时以 `has an invalid type` 拒绝且文件不变） | 改 |
| W9 | tests/unit/agents/test_life_memory_store.py::test_entry_identities_are_contiguous_and_merges_take_no_identity | 新 |
| W10 | tests/unit/agents/test_life_memory_store.py::test_merge_window_has_a_closed_twenty_four_hour_boundary | 新（移植自 tests/integration/as2/test_worldmock_memory_boundaries.py:33） |
| W10 | tests/unit/agents/test_life_memory_store.py::test_merge_similarity_uses_the_frozen_threshold | 新（移植自同文件 :44） |
| W10 | tests/unit/agents/test_life_memory_store.py::test_memories_with_incomparable_time_zones_are_not_merged | 新（移植自同文件 :144） |
| W10 | tests/unit/agents/test_life_memory_store.py::test_short_or_empty_text_cannot_count_as_similar | 新（移植自同文件 :118 与 tests/integration/as2/test_worldmock_life_hooks.py:479） |
| W10 | tests/unit/agents/test_life_memory_store.py::test_memories_of_different_types_are_not_merged | 新（移植自 tests/integration/as2/test_worldmock_life_hooks.py:529 的类型部分） |
| W10 | tests/unit/agents/test_life_memory_store.py::test_a_memory_merges_into_one_created_earlier_in_the_same_call（同一次调用里两条相似记忆：只新建一条，`merge_count` 为 2，`save_step` 把它只算作新建） | 新 |
| W10 | tests/integration/runtime/test_verified_memory.py::test_repeated_subjective_memory_reports_merge_identity_without_duplicate（改用 `save_step`） | 改 |
| W11 | tests/unit/agents/test_life_memory_store.py::test_an_invalid_new_record_leaves_file_and_records_unchanged（已有一条条目后，一次调用先给一条合法记忆、再给一条正文为 `""` 或 `" \t\n"` 的记忆：抛 `ValueError` 含 `has empty text fields`，条目文件字节与 `episode_ids()` 都与调用前相同） | 新 |
| W11 | tests/unit/agents/test_life_memory_store.py::test_returned_records_cannot_change_the_store | 新（移植自 tests/integration/runtime/test_verified_memory.py:190） |
| W12 | tests/unit/agents/test_life_memory_store.py::test_read_back_accepts_what_the_store_wrote（含一条关键词里带 U+2028 与 U+0085 的记忆和一次合并；`read_episodes` 的结果等于存储里的条目） | 新 |
| W12 | tests/unit/agents/test_life_memory_store.py::test_read_back_rejects_each_corruption_without_repair（逐项参数化【W12】表中各行） | 新（覆盖 tests/unit/as2/test_memory_restore.py 的三例） |
| W12 | tests/unit/agents/test_life_memory_store.py::test_read_back_rejects_non_contiguous_or_duplicate_identities（断号、重号、从 `m_000002` 起、旧式 `m_00000000_000001`） | 新 |
| W13 | tests/integration/runtime/test_agent_workspace.py::test_restore_hands_the_written_records_to_the_stores（`engine_factory` 引擎写出工作区、检查点、`restore_branch` 加 `restore_workspaces`，返回的条目等于写出的条目，`memory_stores` 不读文件） | 新 |
| W13 | tests/integration/runtime/test_agent_workspace.py::test_restore_rejects_unexpected_members_and_corrupt_memory（成员多出 `AGENT.json`、只有 `MEMORY.md`、`MEMORY.md` 为空、条目断号、缺 `agent:1` 快照，各得对应 `IntegrityError`） | 新 |
| W13 | tests/integration/runtime/test_agent_workspace.py::test_resume_validates_each_workspace_exactly_once（`native_runtime` 跑出父运行，取第一次决策之后的检查点 `resume_world` 跑到结束；`life_memory_store.read_episodes` 与 `workspace` 模块里的同名绑定都包上同一个计数器，整次恢复运行计数恰为人物数） | 新 |
| W13 | tests/integration/runtime/test_runtime_inputs_contract.py::test_resume_rejects_a_corrupt_memory_workspace_before_publishing（取代 `test_resume_rejects_a_frozen_card_that_differs_from_the_persona_table`：经 `_prepare_resume` 真实路径，子目录不存在） | 改 |
| W13 | tests/integration/runtime/test_runtime_inputs_contract.py::test_resume_restores_exact_world_card_and_full_model_from_the_frozen_workspace（:171-173 经 `_society_specs` 读恢复工作区的三句改为断言 `prepared.memory_records == {1: ()}`，夹具写的是空工作区；卡片与模型身份的断言由人物一章按冻结输入改写；:174-184 的世界状态断言不变） | 改 |
| W13 | tests/integration/runtime/test_verified_memory.py::test_restore_preserves_subjective_identity_and_rejects_status_tampering（改用 `read_episodes`，错误文字 `has an invalid epistemic status`） | 改 |
| W14 | tests/integration/runtime/test_environment_identity.py::test_open_rejects_changed_persona_without_mutating_original_run（改过的 `persona.toml` 打开已有运行时以环境身份不符拒绝） | 不变 |
| W14 | tests/integration/runtime/test_environment_identity.py::test_each_frozen_input_identity_is_required（`persona_sha256` 等任一缺失报 `Runtime environment identity mismatch`） | 不变 |
| W14 | tests/integration/runtime/test_runtime_inputs_contract.py::test_resume_rejects_changed_original_bytes_before_publishing_child（改过的 `persona.toml` 或 `config.toml` 在发布子目录之前被拒） | 不变 |
| W15 | tests/integration/runtime/test_memory_retirement.py::test_memory_updates_are_step_tail_registrations_only（每决策至多一条、来源观察是本决策的观察） | 改 |
| W15 | tests/integration/runtime/test_memory_retirement.py::test_first_wake_up_of_the_day_sends_only_action_requests（主观摘要断言改为读 `MEMORY.md` 含 `> 暂无人物自述。`） | 改 |
| W16 | tests/unit/agents/test_life_memory.py::test_step_save_classifies_created_and_merged_against_identities_before_the_step | 新 |
| W16 | tests/unit/agents/test_life_memory.py::test_no_update_evidence_when_nothing_was_written_and_the_view_is_unchanged | 新 |
| W16 | tests/integration/runtime/test_verified_memory.py::test_fact_view_only_uses_the_current_observation（取代 `test_fact_view_only_uses_current_observation_and_does_not_reset_subjective_counter`，去掉计数与 `build_context`） | 改 |
| W16 | tests/integration/runtime/test_verified_memory.py::test_actual_app_progress_is_written_to_verified_memory_with_source_and_cursor（改用 `save_step`） | 改 |
| W17 | tests/integration/runtime/test_memory_retirement.py::test_memory_updates_are_step_tail_registrations_only（键集合恰为 `MEMORY_UPDATE_KEYS`，十一个；无三个旧键） | 改 |
| W17 | tests/integration/runtime/test_memory_retirement.py::test_first_memory_update_of_the_agent_has_a_null_previous_view_digest（第一条为 `null`，此后每条等于上一条的 `memory_sha256`） | 新 |
| W17 | tests/integration/runtime/test_verified_memory.py::test_multiple_receipts_keep_goods_and_their_source_references（构造新 `MemoryUpdateEvidence`，`_memory_evidence_data` 的键为 `MEMORY_UPDATE_KEYS` 前九个） | 改 |
| W17 | tests/unit/agents/test_life_memory.py::test_memory_snapshots_are_exactly_four_subjective_keys（移植自 tests/integration/as2/test_worldmock_memory_boundaries.py:154 与 :158：`valid_subjective_snapshot` 拒绝 `None`、`[]`、字符串与整数；`_episode_snapshot` 对合法条目返回四键快照，`type` 为 `event` 时以 `Subjective memory snapshot is invalid` 拒绝；带 `source: reflection` 的五键快照不合法） | 新 |
| W17 | tests/unit/life/test_current_lifecycle_coverage.py::test_memory_evidence_cannot_claim_another_observation_or_source_window、::test_summary_refresh_is_recorded_without_a_life_transition_or_fabricated_write_point（`MemoryUpdateEvidence` 改从 `worldmock.recording_engine.events.memory_evidence` 导入，`:196-200` 与 `:213-216` 的构造删去 `state_sha256=` 与 `consolidated=` 两个关键字；断言不变） | 改 |
| W16、W17 | tests/integration/runtime/test_life_agent_protocol.py::test_native_memory_update_hashes_real_workspace_files（`AgentMemoryStore` 与 `MemoryStepOutcome` 换成 `store = WorldMockMemoryStore(tmp_path / "agents" / "agent_0007", 7, ())` 与 `outcome = save_step(store, MemoryStep("林舟", (), "observation-current", tuple(_stored_memories(_conclude_args(_WAIT))), _context().observed_at))`，证据由 `memory_update_evidence(_context(), store, outcome)` 构造后交给 `_Environment.record_memory_update`；`written` 取 `outcome.created_episodes`；删去 `consolidated` 与 `state_sha256` 两项断言，另断言 `memory_before_sha256 is None`、`created_episode_ids == ("m_000001",)`） | 改 |
| W10、W16、W17 | tests/integration/runtime/test_life_agent_protocol.py::test_native_memory_update_records_real_subjective_merge_without_duplicate（`LifeAgent.from_workspace`、`WorldMockMemoryRuntime.after_step` 与 `_record_memory_update` 换成同一个 `WorldMockMemoryStore(tmp_path / "agents" / "agent_0007", 7, ())` 上两次 `save_step`，`MemoryStep` 的事实与观察事件身份取原用例交给观察的值、记忆为 `conclusion`，第二次的 `now` 晚一分钟，每次的结果经 `memory_update_evidence(_context(), store, outcome)` 交给 `_StepEnvironment.record_memory_update`；`store.iter_episodes()` 换成 `read_episodes(store.workspace, 7)`；摘要核对删去 `state_path` 一项；其余断言不变） | 改 |
| W8、W15 | tests/integration/runtime/test_life_agent_protocol.py::test_person_step_records_conclusion_memory_update（装配按工具一章【T53】，经 `LifeAgent.decide` 跑一次决策；条目文件按 `"\n"` 切行读，条目断言的元组去掉 `refs`：`[(item["text"], item["source"], item["epistemic_status"]) for item in episodes] == [(_memory()["text"], "activity_conclusion", "subjective")]`） | 改 |
| W7、W10 | tests/integration/runtime/test_conclude_memories.py::test_nothing_sent_to_the_person_model_carries_an_internal_identity（`store = person.memory`，即工具一章【T56】交给 LifeAgent 的 `WorldMockMemoryStore`；`:670` 改为 `store.append_episodes(_stored_memories(earlier), _OBSERVED_AT - timedelta(hours=10))`；`store.iter_episodes()` 换成 `read_episodes(store.workspace, store.agent_id)`） | 改 |
| W7 | tests/integration/runtime/test_conclude_memories.py::test_the_reply_body_and_the_conclusion_stay_out_of_the_next_progress_layer（`:716-718` 改为 `person.memory.append_episodes(_stored_memories(...), _OBSERVED_AT - timedelta(hours=10))`，记忆参数不变） | 改 |
| W18 | tests/integration/runtime/test_memory_retirement.py::test_memory_update_evidence_without_an_observation_is_rejected | 不变 |
| W18 | tests/integration/runtime/test_memory_retirement.py::test_a_five_key_snapshot_in_memory_update_evidence_is_rejected（取代 `test_consolidated_memory_update_evidence_is_rejected`） | 改 |
| W18 | tests/unit/engineering/test_shared_primitives.py::test_a_reflection_sourced_snapshot_is_rejected_across_memory_quality_and_behavior（取代 `test_independent_reflection_snapshot_is_valid_across_memory_quality_and_behavior`：五键快照在三处都不合法，行为工具记一条畸形） | 改 |
| W19 | tests/unit/footprints/test_quality.py::test_memory_update_payload_must_have_exactly_the_eleven_keys（参数化：多 `consolidated`、多 `reflection`、多 `state_sha256`、缺 `decision_id`） | 新 |
| W19 | tests/unit/footprints/test_quality.py::test_a_null_previous_view_digest_is_a_valid_memory_update | 新 |
| W19 | tests/unit/footprints/test_quality.py::test_malformed_trigger_or_decision_is_an_invalid_memory_update（加 `{"trigger": ["reflection"]}` 一行） | 改 |
| W19 | tests/unit/footprints/test_quality.py::test_memory_update_whose_write_point_has_no_registration_has_no_source（删去含 `reflection` 的参数行） | 改 |
| W20 | tests/integration/runtime/test_agent_workspace.py::test_a_decided_run_leaves_exactly_the_two_memory_files（最后一个检查点的 `agent:1` 快照成员与字节等于终态工作区） | 新 |
| W20 | tests/unit/footprints/test_workspace_snapshot.py 全部用例 | 不变 |
| W20 | tests/unit/footprints/test_branch_coverage.py::test_snapshot_and_restore_keep_every_workspace_file_byte_for_byte（文件集改为 `MEMORY.md` 与 `memory/episodes.jsonl`） | 改 |

### 删除与移植

| 测试 | 动作 |
| --- | --- |
| tests/integration/as2/test_worldmock_memory_boundaries.py | 删除。:33、:44、:118、:144 移植如上表；:105（损坏条目不修复）由 `test_read_back_rejects_each_corruption_without_repair` 承担；:123（空正文拒绝且不改已写条目）由 `test_an_invalid_new_record_leaves_file_and_records_unchanged` 承担；:134（空写入不改文件）由 `test_episodes_file_is_written_only_when_its_bytes_change` 承担；:154、:158（共用快照校验）移植为 `test_memory_snapshots_are_exactly_four_subjective_keys`；:52（通用框架不做合并）、:60、:81、:91（绑定 AgentSociety2 运行时与存储）、:73（关掉记忆的开关）随概念消失 |
| tests/unit/as2/test_memory_restore.py | 删除；三例由 `test_read_back_rejects_each_corruption_without_repair` 与 `test_read_back_accepts_what_the_store_wrote` 承担 |
| tests/unit/as2/test_memory.py | 删除，不移植（AgentSociety2 的巩固与检索，WorldMock 不用） |
| tests/integration/as2/test_worldmock_life_hooks.py:381-592 的记忆部分 | :479 与 :484、:511、:529 三个合并用例移植如上表；:381（空记忆列表）由 `test_first_step_save_has_no_previous_view_digest_and_writes_no_template` 承担；:408（记忆校验失败上抛）与 :467（拒绝 `event`、`observation` 类型）由 `test_store_fixes_source_and_status_and_refuses_non_subjective_types` 承担；:421（落盘失败上抛）由「故障文本」一章的 `test_memory_persistence_failure_aborts_society_before_commit` 的 `exception` 一种承担；:397（AgentSociety2 的 finish 记忆上限）、:439（静默零写入）、:555（不复制读取）、:571、:586（记忆模型入口）随概念消失。文件其余部分由人物一章与工具一章处理 |
| tests/integration/runtime/test_verified_memory.py::test_memory_runtime_without_a_workspace_does_not_claim_a_persisted_update、::test_zero_recent_memory_limit_returns_no_recent_entries、::test_memory_store_results_cannot_mutate_persisted_nested_episode_data | 删除前两例（无工作区的运行时、`build_context` 已不存在）；第三例移植为 `test_returned_records_cannot_change_the_store` |
| tests/integration/runtime/test_memory_retirement.py::test_retired_memory_paths_are_gone | 删除（断言的是已删框架的内部成员） |
| tests/unit/agents/test_life_memory.py::test_subjective_receipt_claims_cannot_create_verified_goods_receipts | 删除（`summary` 参数已不存在）；视图里只有常量自述，由 `test_memory_view_matches_the_exact_template_with_the_constant_self_description` 覆盖 |
| tests/unit/footprints/test_quality.py 的 `test_reflection_before_the_observation_and_conclusion_after_it_are_each_matched`、`test_reflection_registration_without_its_first_wake_up_of_the_day_has_no_source`、`test_second_reflection_registration_at_the_same_moment_has_no_source`、`test_a_wake_up_just_before_local_midnight_does_not_take_the_next_day_s_reflection`、`test_reflection_registration_with_a_malformed_binding_is_an_invalid_memory_update`、`test_bedtime_reflection_snapshot_may_carry_its_source`、`test_old_wake_name_does_not_prove_reflection_without_sleep_completion` | 删除；只被它们使用的辅助函数（`_reflected` 等）一并删除；`_memory_update`（:78-86）去掉 `state_sha256`、`consolidated` |
| tests/integration/runtime/test_persona_profile.py 的 `test_restored_persona_profile_rejects_retired_taste_weights`、`test_restored_card_must_equal_the_card_built_from_the_frozen_persona`、`test_a_card_frozen_for_another_persona_table_does_not_restore` | 删除（被测函数已删除，W14） |

### 源码变异

在 `tests/dataset_acceptance.py` 登记三条新变异；锚点文字由实施者从真实源码里选，在所属符号内唯一。

| 名称 | 符号 | 替换破坏什么 | 杀手 |
| --- | --- | --- | --- |
| `memory_entry_identity_not_contiguous` | `worldmock.agents.memory.life_memory_store::_validate_record` | 编号核对的条件换成恒假：读回接受断号、重号与旧式编号 | tests/unit/agents/test_life_memory_store.py::test_read_back_rejects_non_contiguous_or_duplicate_identities |
| `first_memory_view_digest_not_null` | `worldmock.agents.memory.life_memory::save_step` | `MEMORY.md` 不存在时的写入前摘要换成空字节的 SHA-256：人物第一条 `memory_updated` 的 `memory_before_sha256` 不再是 `null` | tests/unit/agents/test_life_memory.py::test_first_step_save_has_no_previous_view_digest_and_writes_no_template；tests/integration/runtime/test_memory_retirement.py::test_first_memory_update_of_the_agent_has_a_null_previous_view_digest |
| `memory_update_keeps_a_retired_key` | `worldmock.recording_engine.events.memory_evidence::_memory_evidence_data` | 返回的字典里加回 `"consolidated": False`：`memory_updated` 多出一个键 | tests/integration/runtime/test_memory_retirement.py::test_memory_updates_are_step_tail_registrations_only；tests/integration/runtime/test_verified_memory.py::test_multiple_receipts_keep_goods_and_their_source_references |

杀手测试文件与被变异模块的关系：`test_life_memory_store.py` 直接导入 `life_memory_store`；`test_life_memory.py` 直接导入 `life_memory`（`save_step`）；`test_memory_retirement.py` 直接导入 `memory_evidence`（`MEMORY_UPDATE_KEYS`），它的用例经 `host_run` 的真实 LifeAgent 走到 `life_memory.save_step`；`test_verified_memory.py` 直接导入 `memory_evidence`（`_memory_evidence_data`、`MemoryUpdateEvidence`）与 `life_memory`。变异运行器在子进程里替换被变异模块后跑全部杀手，任一失败即算杀死（tests/mutation_runner.py:142-172）。台账里随本章删除的旧变异：`restored_card_not_compared`（`worldmock.runtime.lifecycle.runner::_validate_persona_card_struct`，符号与杀手都删除）、`step_end_copies_every_memory_again`（`worldmock.agents.memory.life_memory::WorldMockMemoryRuntime.after_step`，所防的「经 AgentSociety2 复制式读取全部条目」在新存储里没有对应读取，杀手文件删除）。

## 不在本章

| 事项 | 归属 |
| --- | --- |
| LifeAgent 的构造、冻结输入（卡片、名字、模型身份、续接开关、回合上限）与「名字取自 `persona.toml` 等于今天取值」的测试；`_conclusion_memories` 改为原样交出 `control_items` 的存储形状记忆（不加 `source`、`epistemic_status`，不去 `subject`），以及断言旧展平结果的 `tests/integration/runtime/test_conclude_memories.py::test_conclusion_memories_flatten_by_task_with_system_source`；只读问答与 `life_prompt_snapshot.json` 读写代码的删除 | [「人物、回合循环与只读问答的删除」](./03-agent-loop.md) |
| `RunHost` 何时调 `create_workspaces`、`snapshot_workspaces`、`memory_stores`，`_PreparedRun` 其余字段，`SocietyCheckpointRequest` 及其身份、时刻核对的删除 | [「运行宿主与决策轮」](./01-run-host.md) |
| `LifeEngine.record_memory_update` 门面签名 | [「去掉 LifeEnv：引擎门面、只读查询与提交结果」](./02-engine-facade.md) |
| 许可原文文件与合并前来源核对；行为分析里 `MEMORY.md` 示例身份残留门禁的删除（tools/analysis/behavior/metrics.py:39-42） | [「安装、代码身份、依赖与许可」](./07-install-identity.md) |
| 运行比对、查看器与夹具整体改写（所需内容见下方「交给工具一章的内容」） | [「比较工具、查看器与测试迁移」](./09-tools-tests.md) |
| 两份完整日基线重冻（夹具快照变成空 tar）、`viewer-context.json` 重录、数据集一侧 `tools/native_day.py:212` 改写 | [「实施、验收证据与完成判据」](./10-implementation.md) |
| 主观条目完全从事件推导、取消人物快照 | 裁定第 3 行定为不在本次 |
| 质量报告的模型调用类 `consolidation`、`reflection` | 属于模型请求分类，提案未列入，不改 |

### 交给工具一章的内容

| 工具或测试 | 需要的改动 |
| --- | --- |
| `scripts/compare_runs.py` | 删去 `AGENT_MASKS`（:56-58）与 `_workspace_listing` 里 `AGENT.json` 分支（:671-673）及文档串里的 AGENT.json 一句（:662）；`MEMORY.md` 来源事件映射（:91-95、:674-689）保留；`ALLOWED_RULE_TARGETS` 加入本章声明的格式规则：工作区成员恰为两个文件、两个文件的 LF 与被删的五个条目键、连续条目编号、`memory_updated` 键集合与条目编号、首条 `memory_before_sha256` 为 `null`、`episodes_sha256` |
| `tools/run-viewer.html` | :553-556 不再取 `$.data.consolidated`，:710 去掉「· 已巩固」；按文件名 `MEMORY.md` 与路径正则 `agent_?0*(\d+)`（:1796-1799）认人物的规则对新路径照常成立 |
| `tools/run-viewer-context.js` | `memory_view` 一项（:143-151）：:144 的 `source` 改为 `src/worldmock/agents/memory/life_memory.py · render_memory_view、save_step`（`build_context` 已删除）；:145 的说明改为「工作区 MEMORY.md 由身份、至多五条已核验经历和恒为「暂无人物自述。」的自述段组成，最多四千字符。」；:147 与 :149 两步删去「主观归纳」的说法 |
| tests/system/viewer/run_viewer.test.cjs:1266、tests/system/viewer/run_viewer_identity.test.cjs:141 | 运行目录样例的 `as2/agents/agent_0001` 改为 `agents/agent_0001`；两份测试里没有 `consolidated` 样例，不用改别处 |
| tests/integration/runtime/test_life_agent_protocol.py:1517、tests/integration/runtime/test_conclude_memories.py:766 | 按工具一章【T53】【T56】：`_create_step_workspace` 删除，工作区是运行宿主经 `memory_stores` 建在 `run_dir/agents/agent_0007` 的那一个（`:1517` 的路径随之改为 `tmp_path / "agents" / "agent_0007"`，`:766` 的创建删去） |
| tests/unit/engineering/test_import_graph.py | `DELAYED_IMPORTS` 删去 runner 的 `_restore_agents`（:96-97）、`_validate_agent_workspace`（:106-107）、`_validate_persona_card_struct`（:108）、`_restored_specs`（:100）各条，加 `('worldmock.runtime.lifecycle.runner', '_restore_branch', 'worldmock.agents.memory.workspace')`；:106 一条与「安装、代码身份、依赖与许可」一章列的同一条是同一处删除 |
| tests/unit/engineering/test_single_source.py | :38 `memory_evidence.py` 一行加 `MEMORY_UPDATE_KEYS`；:67 `src/worldmock/state/model/memory.py` 一行的名字集合不变 |

## 规格阶段的决定

| 事项 | 决定 | 理由 |
| --- | --- | --- |
| 工作区模块的位置 | `src/worldmock/agents/memory/workspace.py`，与记忆存储同包 | 工作区里只有记忆文件；`agents` 可以导入 `recording_engine` 的检查点函数，运行宿主可以导入 `agents`，不触犯引擎边界（tests/unit/engineering/test_import_graph.py:387-399） |
| 条目文件何时出现 | 人物第一次步尾保存时写出（没有条目就是空文件），新建时工作区为空 | 提案写「此时可以为空」；`episodes_sha256` 因此总是一个真实文件的摘要，没有条目时等于今天空文件的摘要 |
| 恢复只校验一遍的落法 | 暂存目录里 `restore_workspaces` 是唯一一次严格读回，返回的条目经 `_PreparedRun.memory_records` 交给 `RunHost` 建存储，建存储不读文件 | 既保住「完整恢复、校验通过才发布子目录」，又让一次恢复只读回一遍，与提案「以后只校验一遍」一致 |
| 存储整次运行共用 | 每个人物一个 `WorldMockMemoryStore`，`RunHost` 在启动时建好，每个决策交给新建的 LifeAgent | 提案第六章「记忆存储是整次运行共用的对象」；人物在决策之间不带内存状态 |
| 视图预算 | 只留一条：视图总长不超过 4000 字 | 自述段已是常量，截断常量没有意义；今天的 4000 字写盘上限与 3996 字文件头上限合成一条规则，放一处。文件头不超过 3990 字时输出逐字不变；只有 3991 至 3995 字时今天截短自述写出、新规则拒绝，而卡片文本至多 600 汉字、事实至多五行，实际视图远在这个长度以下 |
| 渲染先于写条目 | `save_step` 先渲染视图，再写条目文件，最后写视图 | 渲染不读存储；预算不通过时一个文件都不写，【W5】的「不写文件」在步尾保存里同样成立；成功路径写出的字节不变 |
| 条目文件的切行 | 读回按 `"\n"` 切行，不用 `splitlines()` | 文件只用 LF；关键词里的 U+2028、U+2029、U+0085 被 `json.dumps(ensure_ascii=False)` 原样写出，`splitlines()` 会把一条条目切成两行，存储写出的文件就读不回 |
| `MEMORY_UPDATE_KEYS` 的归宿 | 登记进 `tests/unit/engineering/test_single_source.py` 的单一归宿表，归 `memory_evidence.py` | 引擎构造与质量报告核对共用这一组键，一条规则一处 |
| 步尾保存同步执行 | 不用 `asyncio.to_thread` | 单进程单事件循环、引擎方法同步，宿主逐个等待；线程化没有收益 |
| 记忆的归一化放在哪里 | 存储从 `control_items` 的存储形状直接构造条目：固定来源与认知状态、去掉 `subject`、归一化正文与关键词；LifeAgent 原样交出记忆 | 今天这件事分在 LifeAgent、AgentSociety2 的 pydantic 校验、存储三处；提案要求存储吸收「条目的归一化」，一条规则放一处 |
| 写前校验 | 新条目写出前过一遍读回用的 `_validate_record`，取代 AgentSociety2 的 pydantic 校验与 `WorldMock conclusion memory must be subjective and system-sourced` 一检 | 写出的文件恰好是读回接受的文件；只留一份条目格式规则 |
| 空正文的错误文字 | 由 `Memory store received an empty memory text` 改为校验函数的 `Memory episode line {n} has empty text fields` | 请求模型已保证正文非空，这条只防格式；与读回共用一份规则 |
| 读回的类型集与来源 | 类型只认 `SUBJECTIVE_TYPES`，来源只认 `activity_conclusion`，新增错误文字 `has an invalid source` | 读回核对的是 WorldMock 实际写出的格式；上游接受的 `event`、`observation` 与任意来源 WorldMock 从不写 |
| 编号核对的错误文字 | `Memory episode line {n} has a non-contiguous identity`，一条同时保证连续与唯一 | 逐行写法与其余读回错误一致；「连续」蕴含「唯一」，不另设一条 |
| 条目文件的写出路径 | 只有一条：内容变了就整份写出 | 在同一组条目上与「追加或重写」写出的文字相同，少一个分支 |
| `importance` 的取值 | `float(原值)`，不夹到 [0, 1] | 请求模型已限定范围，读回再核对；夹值只会掩盖格式错误 |
| 五键 `source=reflection` 快照 | 从 `valid_subjective_snapshot` 删去，环境与质量报告都只认四键 | 与 `trigger` 去掉 `reflection` 是同一件事：只为重新认证历史运行而留；新键集合本已拒绝所有历史运行，这条分支没有输入 |
| 质量报告的反思分支 | 删去睡醒与本地日重算、`_valid_update_binding`，`_memory_quality` 不再取时区，开头核对十一个键 | 裁定第 4 行删的正是为重新认证历史运行而留的键；键集合核对让旧格式记录明确记为问题，而不是被部分读法悄悄放过 |
| `memory_before_sha256` 为 `null` 的约束 | 引擎与质量报告接受「摘要或 `null`」；「只有第一条为 `null`、此后衔接上一条」由构造保证并由集成测试核对，不另加运行期核对 | 文件一旦写出就一直存在，恢复要求成员为空或两个文件，`null` 只可能出现在第一条；不加防御性核对 |
| 步尾不再核对事实窗口 | 删去 `_current_facts` 与它的三条错误文字 | 窗口在取观察时已由 `validate_verified_experiences` 核对（custom/agents/life_agent.py:379-385），它已覆盖条数、人物、来源身份与次序；今天的重复核对只因框架经 `getattr` 伸进人物对象取值 |
| `MemoryUpdateEvidence` 的位置 | `src/worldmock/recording_engine/events/memory_evidence.py`，与核验它的 `_memory_evidence_data` 同处，并在同处定义 `MEMORY_UPDATE_KEYS` | 人物与引擎都能导入而人物不导入引擎；证据形状与核验规则放一处 |
| 已存在工作区的错误 | `FileExistsError(f"Agent workspace already exists: {path}")`，沿用 AgentSociety2 的文字（vendor/…/society/society.py:203-204），按人物目录核对与报出 | 运维看到的说法不变；AgentSociety2 核对的是全部工作区的根目录 `as2/agents`，新目录下每个人物的路径由 `agent_workspace` 给出，按人物核对不需要另拼根目录 |
| 旧变异 `step_end_copies_every_memory_again` | 删除，不改指 | 它防的是 AgentSociety2 存储的复制式读取 `iter_episodes`；新存储没有这种读取，杀手文件也删除 |
| （问题）条目里的 `agent_id` 留不留 | 留 | 提案只列了五个要删的字段；`agent_id` 在读回时核对工作区归属，有 WorldMock 含义 |
| （问题）旧格式快照（含 `AGENT.json` 等）恢复时怎么办 | 成员核对直接以 `Restored agent workspace members are invalid` 拒绝，不迁移 | AGENTS.md「不保留向后兼容」；旧运行本已因代码身份不能恢复 |
| （问题）`creation_inputs` 的 `config` 由哪章删 | 本章删 `config` 键，函数其余部分归人物一章 | `config` 只是 `config.json` 的内容；回合上限改取运行清单 |
| （问题）`WorldMockMemoryStore` 要不要改名 | 不改名 | AGENTS.md「同一个概念全程保持同一个名称」；去掉基类不改变概念 |
