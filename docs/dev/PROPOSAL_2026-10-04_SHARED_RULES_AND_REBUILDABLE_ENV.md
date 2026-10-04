# 共享规则单一归宿与可重建环境提案

> 状态：历史提案，已由[最终特性规格](SPEC-shared-rules-and-rebuildable-env.md)于 2026-10-05 裁决并取代；下文保留提案时的证据，不描述当前机器或最终实现。
> 代码基线：`94595ca`（main，2026-10-04）。文中数字都按该提交实测，方法见「本次核查做了什么」。
> 范围：一、被另写了多份的共享规则，以及其中已经不一致的部分；二、开发与测试环境能否从零重建：AS2 分支改动、安装步骤、门禁对 `uv` 与私有地址的依赖、跨操作系统的运行身份。
> 建议：一、先上防漂移守卫，再按风险由低到高把同一规则合并到一处，已经不一致的先定规则再改代码；二、统一换行，把 AS2 改动变成仓库内的补丁集，提供不依赖 `uv` 与 SSH 的引导脚本，并用新机器检查证明可以重建。
> 编号：小节编号 4.x、5.x 沿用 2026-10-04 可维护性评估的第 4、5 项；第 1–3 项见[代码分层、文档测试解耦与测试分层提案](PROPOSAL_2026-10-04_LAYERING_AND_TEST_TIERS.md)。

## 为什么现在做

- **正式十二周运行还没开始**（见 `docs/VERIFICATION.md`）。本文有几项会一次性改变运行身份：统一换行、缩小 AS2 补丁集、改动依赖锁。在长运行开始之前做，代价最小。
- **漂移已经造成错误：** 行为指标的记忆重复率门禁把就寝反思写入的快照当作畸形，凡有就寝反思的运行这道门禁一律不通过（已另立任务修复）。同类的「改了一份、漏了另一份」还有十余处，见第一节。
- **环境无法按说明重建：** 新机器按安装说明拿不到私有子模块；本机的 AS2 装在系统临时目录里，清理临时文件就会失效。

## 一、每条共享规则只有一个家

### 现状

字面重复不多：AST 克隆检测只找到约 200–280 行重复函数，pylint 的重复代码检查只找到 6 处 5 行以上的重复块，死代码约 19 行。问题在于同一条规则被另写了几份，其中一部分已经不一致。下表是基线 `94595ca` 上逐项复核的结果。分类：**a** 为同一规则写了几份、目前一致；**b** 为有意的变体；**c** 为几份已经不一致（漂移）。

| 项 | 分类 | 归宿 | 说明与约束 |
| --- | --- | --- | --- |
| A1 规范 JSON 摘要 | c（只差 NaN） | `footprints.models` 的 `canonical_json` 与一个 `sha256_json` | `candidates._digest`、`engine._request_sha`、`work_review._digest`、品类身份与 `trace_validation` 的摘要都没写 `allow_nan=False`；有限值下字节相同，`docs/design/09-io-formats.md` §9.3 不允许 NaN，以 `canonical_json` 为准。`work_review._digest` 对纯字符串按原文哈希，且结果已持久化，属 b，保留 |
| A2 `_is_sha256`（3 份，另有 2 处正则） | a | `footprints.models.is_sha256` | 已有测试钉住规范形式 |
| A3 符号链接与安全文件检查（各 5 份） | a；`map_data`、`coverage_apps` 为 c | `corpora.manifest` | 错误信息改为参数。`map_data` 与 `coverage_apps` 的检查顺序不同，`coverage_apps` 还拒绝 `..`；资源手册没有说明哪个对，需先定规则 |
| A4 原子写（4 份） | c，文档未规定 | `footprints.assets.atomic_write` | fsync、父目录 fsync、建目录、临时文件命名与错误类型各不相同。品类身份的写入用文本模式，在 Windows 上写出 CRLF |
| A5 先记日志再抛出的包装（11 个文件 12 个） | a，且每次失败记两遍日志 | 直接 `raise IntegrityError` | `WorldMockError` 构造时已经记日志。有一个测试断言 `caplog` 里的原始消息，需同步修改 |
| B1 活动时长推导；`86_400`（5 处）与 `14_400`（3 处） | a；`named_catalog` 为 c | 新的 `life/durations.py`；上限常量放 `life/models` | 工具层与会话动作各写一份推导。会话动作那份随条目逐个改变状态后重新核对，两个调用点都要保留。按名调用清单的说明不封顶：超过一天的内容列出完整剩余时长，受理时却按 86,400 秒封顶。`REQUIRED_SYMBOLS` 钉住 `session_actions.py` 里的 `DERIVED_DURATION_CAP_SECONDS = 86_400` |
| B2 开工阻断转拒绝（3 份） | a | `work.work_start_refusal` | 已有工具层测试钉住 |
| B3 出行时长（4 份） | a | `travel.planned_travel` | 引擎重算时长以报 `ROUTE_DURATION_MISMATCH`。有测试按字符串路径 monkeypatch `session_actions.route_for` |
| B4 缓解预估与 MET 键 | 公式 a；失败放行 b；缺省计数 c | `activities` 的速率函数；注册表的 `met_lookup_key` | 缺 `exposure_count` 时，预估按 1 计，结算却报错；一日运行保真规格 1.2 要求两者一致。活动种类写死而不是读注册表；MET 键模板在 `motivation` 与 `tool_layer` 共写死 4 处 |
| B5 候选种类映射；活动家族键（4 份） | a；缺品类时 c | `candidates`；`schedule.activity_family` | `presentation.ACTIVITY_OF_KIND` 没有任何使用者，删除。缺品类时四处行为不同：计划不记进度、会话动作报 `LIFESTYLE_ACTIVITY_NOT_FOUND`、目标指标返回空、相关度不检查 |
| B6 `tool_layer._DOING` | c | `readable` | 与 `readable.py` 的「中文种类表只在这里定义」（一日运行保真规格 7.4）相矛盾；`test_readable` 只检查有名字的表，所以没发现 |
| B7 注册表字段 `sleep_channel` | b | — | `docs/manual/rules.md` 写明它目前不起作用；`body.py` 写死睡眠 |
| B8 `tool_layer` 模块说明 | c | — | 说明称受理路径不调用本模块，实际 `_require_individual_start` 调用 `refusal_for`；改说明即可 |
| C1 区间有效性；身体结算筛选（3 份） | 有效性 c；筛选 a | 新的 `footprints/intervals.py` | 质量报告只接受整数，行为指标接受整数、浮点与布尔（NaN 也能通过），目标指标接受有限非布尔值，工作负荷不检查。记录引擎设计用整数秒，以质量报告为准 |
| C2 按本地日切分（4 份） | a；`render_day` c | `timeline_labels` 的公开函数 | `scripts/render_day.py` 写死 Asia/Shanghai，而场景有自己的 `timezone` 字段；现有场景都是 Asia/Shanghai，所以目前没有可见错误 |
| C3 主观记忆快照校验 | 判定 a；失败处理 b | `life.memory_evidence` | 主观类型集合写了 3 份，写入点 2 份；第 4 份在 `behavior_metrics._episode_pairs`，就是已确认的缺陷（另有任务在修） |
| C4 `_date_blocks`；`_parse_world_event` 与 `_event` | a | `evaluation.metrics`；在 `adapters` 解析一次 | 每行事件被校验两次 |
| C5 行为指标的家族映射缺 chat/call | c（已知） | `schedule` | 与上面的已确认缺陷同一任务处理 |
| D1 模型客户端（3 份） | a | `model_endpoint` 里的基类 | `REQUIRED_SYMBOLS` 要求 `runtime.py` 里有 `class PlanDayClient`，子类保留即可 |
| D2 纠正消息、`_schema_feedback`、`_tool_arguments`（各 3 份） | 外壳 a；回显、指令与重试常量 b（`CONTRACTS.md` 规定）；工作验收的反馈格式 c（文档未规定） | `correction_message()` | 三条反思突变以这些函数名为锚点，保留函数名 |
| E1–E2 错误类与枚举 | `CatalogError` 与 `CategoryError`、两个枚举为 a；`EvaluationError`、`ResourceLedgerError` 为 c | `categories.common` | `catalog` 再导出；枚举只用 `==` 比较 |
| E3–E5 | `CurvePoint`/`EfficiencyFactor` 为 c（life 要求 `ge=0`，目录要求非空模式）；`LineageEntry` 为 b；各包的基础模型为 a | 新的叶子模块 `worldmock/base_models.py` | 基础模型各写一份是为了避免导入环；`categories.FrozenModel` 会深度冻结，语义不同，应改名而不是合并 |

另有三组检测器找到、前次调研没列出的克隆：`content.mail_headers` 与 `observe._mail_headers`，`body.apply_social_ledger` 与 `social_actions._apply_ledger`，`CONTRACT_OBJECT_IDS` 与 `CONTRACT_OBJECTS`。

**这些不是行数问题。** a 类全部合并约净减 300 行，扣除新增的守卫约净减 260 行，不到 `src` 的 1%。价值在于 c 类：它们都是「改了一份、漏了另一份」，其中一处已经造成门禁误判（记忆重复率门禁，另有任务在修）。

### 方案

**4.1 先上防漂移守卫。** 新增 `tests/test_single_source.py`：

- 登记「单一归宿」表（规则名 → 唯一允许定义它的文件）；在别处定义同名函数、类或模块级常量即失败。
- 用函数体的 AST 形状检测克隆：两个以上的函数（去掉 docstring 后至少两条语句）形状相同即失败。今天的 12 组克隆写进「已知克隆」表，表只减不增。
- 扩展到模块级赋值，也能抓到复制的表与常量（例如 `_DOING` 这类）。

**4.2 按风险由低到高分步合并 a 类。** 每步可以单独合入，每步都跑 `tests/test_style_gate.py`：

| 步 | 项 | 估计净行数 | 风险 |
| --- | --- | --- | --- |
| 0 | 守卫 | +45 | 低 |
| 1 | A1、A2（加一个字节级比对与 NaN 测试） | −15 | 低 |
| 2 | A3 | −45 | 低 |
| 3 | A5 | −36 | 低 |
| 4 | B5–B8 | −15 | 低 |
| 5 | C3、C4（在 `_episode_pairs` 缺陷修复之后） | −45 | 低 |
| 6 | B2、B3 与上限常量 | −25 | 低–中 |
| 7 | B1（加性质测试：列出的秒数等于受理的秒数） | −30 | 中 |
| 8 | B4 | −20 | 中 |
| 9 | D | −45 | 低–中 |
| 10 | E | −30 | 低 |

**4.3 c 类逐项先定规则再改代码。** 每一项都会改变可观察的输出，按 `AGENTS.md` 先改所属合同或规格，再改代码，一项一个提交：

- A4：原子写统一为写字节、fsync 文件与父目录、失败抛所属模块的错误类型；品类身份写入改为二进制模式。
- C1：区间统一为「整数秒、起点小于终点」；补一个四处共用的有效性测试。
- C2：`render_day` 改读场景时区。
- B1：按名调用清单的说明与受理同一封顶口径。
- B4：缺 `exposure_count` 时预估与结算同样报错，或在规格里写明预估的缺省值。
- D2：工作验收的反馈格式与另外两条通道对齐，或在 `CONTRACTS.md` 写明差异。
- E3：`CurvePoint`、`EfficiencyFactor` 统一约束。

### 风险

- **突变与文本钉：** 147 条验收突变里只有 3 条反思突变落在要移动的代码里，保留函数名即可不动。`REQUIRED_SYMBOLS` 钉住的几处（`DERIVED_DURATION_CAP_SECONDS`、`MEMORY_WRITE_POINTS`、`class PlanDayClient`）要么保留原位置，要么等[代码分层、文档测试解耦与测试分层提案](PROPOSAL_2026-10-04_LAYERING_AND_TEST_TIERS.md)第二节把文本钉改成导入断言之后再动。
- **按字符串路径的 monkeypatch：** 测试用 `monkeypatch.setattr("模块.名字", …)` 打桩；函数搬走后，旧路径上的打桩可能静默不生效。每一步都要检查被移动名字的打桩点。
- **导入环限制归宿的位置：** `session_actions` → `tool_layer`、`life.models` → `social`/`scheduling` 等现有方向决定了归宿只能放在更底层；与第一份提案的分层规则一并考虑。
- **文档引用：** 一日运行保真规格两次点名 `_entertainment_relief` 与 `progress_day_plan`，时间与能量规格点名 `_targeted_duration`，规则手册点名 `sleep_channel`；冻结规格里的行号引用会过期（处理办法见第一份提案第二节）。

### 验收

- `tests/test_single_source.py` 通过，「已知克隆」表为空。
- 每个 c 类项都有规格或合同条款，并有一个测试同时覆盖原先不一致的各个调用点。
- 全量测试与覆盖率门禁通过。

## 二、环境可以从零重建

### 现状

1. **安装步骤依赖私有 SSH 地址。** 安装说明（`README.md` 与 [运行配置手册](../manual/runtime.md)）要求先 `git submodule update --init vendor/agentsociety2 vendor/labelkit`，再两次 `uv sync --frozen`。两个子模块的地址都是 `git@github.com:HoldThatThgt/…` 形式的私有 SSH 地址，而仓库本身用 HTTPS。没有 SSH 密钥的新机器拿不到子模块，`uv sync --frozen` 随即失败（AS2 是 `[tool.uv.sources]` 里的可编辑路径依赖）；改用 `pip install -e .` 会装上 PyPI 的上游 AS2 2.8.7，WorldMock 在导入分支独有的模块（`society.decision_round` 等）时失败，`require_locked_as2()` 也拒绝启动。
2. **本机环境是手工拼出来的。** 两个子模块目录都是空的，`uv run` 因此失败（`uv` 0.11.26 本身已安装）。`.venv` 里的 AS2 是可编辑安装，指向 `%TEMP%\worldmock-as2-8768e14-source`：系统清理临时目录，环境就坏了。两个虚拟环境都用 uv 自带的 CPython 3.12.13（项目要求 `>=3.12,<3.13`，系统 Python 是 3.13）。
3. **门禁假定有 `uv`。** `scripts/coverage_gate.py` 把测试与报告命令写死为 `uv run coverage …`；以往的门禁运行改用虚拟环境的 Python 分片执行再合并（见 `docs/dev/coverage-baseline.md`）。文档链接与设计说明书两道门禁只用标准库，没有这个问题。仓库没有任何 CI。
4. **可选部件的处理不一致。** Node（运行查看器回归，`docs/VERIFICATION.md` 要求）缺失时测试直接失败；LabelKit 虚拟环境缺失时 `tests/delivery/test_labelkit_process.py` 失败，而 `tests/test_validation.py` 有 4 个用例静默跳过；TextWorld 1.7.0 没有 Windows wheel，`tests/corpora/test_branch_coverage.py` 在 Windows 上整模块跳过。`docs/VERIFICATION.md` 的「规范闭合」门禁明确不允许以 skip 或 xfail 代替验收，与后两者冲突。另有测试假定临时目录与仓库在同一磁盘卷：`tests/life/test_storefront_arrival.py` 用 `os.link` 硬链接复制语料，仓库在 D 盘、临时目录在 C 盘时准备阶段报错（本次全量计时中的唯一错误即此，重跑可复现）；`tests/life/purchase_worlds.py` 里已有跨卷时改为复制的 `_link_or_copy`，这个测试没有用它。
5. **AS2 分支的改动不小，也不在 WorldMock 的门禁里。** 把分支 `8768e14` 的包与 uv 缓存里的上游 2.8.7 wheel 对比（两者的基线是否逐字节相同尚未核实）：修改 15 个文件、新增 3 个、删除 1 个，忽略空白约 +3.3k/−3.4k 行。分支里另有约 2.6k 行 WorldMock 专用测试，WorldMock 的 `testpaths` 不收集它们，也没有门禁运行它们。
6. **同一提交在 Windows 与 Linux 上得到不同的运行身份。** `runtime_code_sha256` 按原始字节哈希 `src`、`custom` 下的 `*.py`、`pyproject.toml`、`uv.lock` 与 AS2 包的全部文件（AS2 按相对路径标注，与安装位置无关）。本机 `core.autocrlf=true`（系统与全局配置），工作区里这 203 个文件有 194 个是 CRLF、8 个混合；`.gitattributes` 只把 `corpora/**` 与 `catalog/**` 固定为 LF。结果是同一提交的代码身份随操作系统变化，检查点不能在两种系统之间续跑。（AS2 临时副本是 LF，摘要与锁定值 `65a902ec…` 一致；若 AS2 被检出为 CRLF，锁定核对会拒绝。）

AS2 分支相对上游的改动，按 [AS2 集成规格](SPEC-as2-integration.md) 的章节归类：

| 规格章节 | 涉及的 AS2 文件 | 处理 |
| --- | --- | --- |
| 决策轮次、时间约定与身份绑定 | `society.py` 重写，新增 `decision_round.py`、`router_direct.py`、`direct_env_actor.py`，`env_router_proxy.py`、`runner.py` | 核心补丁；规格禁止复制调度器 |
| 故障传播 | `agent.py` 拆为 `agent.py` 与 `agent_runtime.py`，去掉合成兜底 | 核心补丁 |
| 恢复与检查点 | `society.py`、`memory.py`、`memory_runtime.py`、`person.py` | 核心补丁 |
| 同秒续接 | `agent_runtime.py`（保留 `reasoning_content`，`prepare_react_loop` 钩子） | 小钩子补丁，注入逻辑已在 `custom/agents/life_agent.py` |
| 记忆运行时第 1–5 项 | `memory_runtime.py`、`memory.py` | **可移出**：经已有的 `_new_memory_runtime` 钩子放进 `custom/agents/life_memory.py` |
| 模型客户端生命周期 | `llm_dispatcher.py`、`service_proxy.py`、`runner.py` | 核心补丁，可以尝试提交上游 |
| 观察只写一次 | `person.py` 的 `_observation_record` | 小钩子补丁加 LifeAgent 覆写 |
| 规格未记载 | 传输重试与多密钥池（`llm_dispatcher.py`，属于模型身份的一部分）、`router_base.py`；`compatibility.py`（WorldMock 未用）；删除 `society/cli.py` | 补丁；`compatibility.py` 可以去掉，并补记进规格 |

### 方案

**5.1 先统一换行（在正式十二周运行之前做）。** 在 `.gitattributes` 加 `*.py text eol=lf`、`*.toml text eol=lf`、`uv.lock text eol=lf`，用一次 `git add --renormalize .` 提交。之后同一提交在任何系统上字节相同、代码身份相同。这会让 Windows 上的代码身份一次性改变（变得与 Linux 相同），所以要在没有进行中长运行时合入。不推荐改为「哈希时规范化换行」：那要修改冻结的运行身份条款，而属性文件的改法不改合同。

**5.2 AS2 改动改成仓库里的补丁集。**

1. 由分支维护者一次性导出 `git diff 670c94ff 8768e14 -- packages/agentsociety2`，按上表的规格章节拆成 `patches/agentsociety2/NNNN-<章节>.patch`；新增文件（含 `pyproject.toml`、README 与 6 个示例文件，它们在上游 wheel 里被 `.gitignore` 排除）放 `patches/agentsociety2/files/`。
2. `patches/agentsociety2/SERIES` 写明上游来源（地址与 sha256）、补丁顺序和应用后的期望摘要（即 `AS2_LOCKED_SOURCE_SHA256`）。`.gitattributes` 加 `patches/** -text`，保证补丁逐字节不变。
3. 第一步必须精确复现锁定摘要 `65a902ec…`，运行身份因此不变。若上游基线无法取得或与 `670c94ff` 不一致，就把基线快照提交进仓库（AS2 为 Apache-2.0，允许再分发，需保留许可与修改说明）。
4. 把分支里约 2.6k 行 WorldMock 专用测试移到 `tests/as2/`，由 WorldMock 的门禁运行。
5. 之后再单独、分版本地缩小补丁集：记忆运行时第 1–5 项移入 `custom/`，去掉 `compatibility.py`，模型客户端生命周期尝试提交上游。每次缩小都会改变 AS2 摘要，按运行身份的版本规则登记。

**5.3 不依赖 `uv` 与 SSH 的引导脚本。** 新增 `scripts/bootstrap.py`（只用标准库），参数 `--profile release|dev`、`--wheelhouse`、`--use-uv`：

1. 找到 Python 3.12（Windows 用 `py -3.12`，其他系统用 `python3.12`），建立 `.venv`。
2. `pip install --require-hashes -r requirements/lock.txt`。该文件由 `uv.lock` 导出，另加一个测试保证两者一致。
3. 子模块目录为空时，取上游基线并按 `SERIES` 应用补丁，生成到原路径 `vendor/agentsociety2/packages/agentsociety2`。这样 `pyproject.toml` 与 `uv.lock` 不必改，`git status` 也看不到差异。
4. 核对摘要等于锁定值，然后 `pip install -e --no-deps` 安装 AS2 与 WorldMock。
5. LabelKit 从不需要 SSH 的制品安装（是否公开、以何种许可分发由仓库所有者决定）。
6. 写一份环境收据：Python 版本、依赖锁摘要、AS2 摘要、Node 版本、缺失的可选部件。

`uv sync --frozen` 仍是快捷路径，使用同一份锁。

**5.4 门禁不依赖 `uv`。** `scripts/coverage_gate.py` 改用 `sys.executable -m coverage`，并增加 `--shards N`，按目录分片（每个目录的测试文件保持连续）后合并结果。

**5.5 可选部件按档位处理。** `release` 档位下，Node 与 LabelKit 缺失即失败，因为验收规则不允许以跳过代替。`dev` 档位下以固定原因跳过；门禁看到跳过时拒绝给出 `passed: true`，只记录缺了什么。`tests/test_validation.py` 的静默跳过改为同一规则。TextWorld 在 Windows 上没有 wheel：要么在 `docs/VERIFICATION.md` 写明这部分验收在 Linux 上执行，要么为 Windows 提供替代安装方式。测试复制仓库文件统一用一个「同卷硬链接、跨卷复制」的辅助函数，并加一个守卫禁止直接 `copy_function=os.link`。

**5.6 新机器检查。** 增加 CI（Ubuntu 与 Windows 各一个任务，`submodules: false`，不配置任何密钥），依次运行引导脚本、AS2 身份核对与提交前快速层测试（见[代码分层、文档测试解耦与测试分层提案](PROPOSAL_2026-10-04_LAYERING_AND_TEST_TIERS.md)第三节）。另提供本地版 `scripts/fresh_check.py`：用 `--no-recurse-submodules` 克隆到临时目录，设 `GIT_SSH_COMMAND=false`，不用 pip 缓存，跑同样的步骤。

**5.7 同步安装说明与被钉文本。** `README.md`、`docs/manual/runtime.md` 改写安装步骤。`tests/time_and_energy_acceptance.py` 逐字钉住了子模块命令、AS2 锁定摘要与 AS2 集成规格里「子模块指针」的写法，必须在同一提交更新。

### 风险

- **运行身份：** 5.1 会一次性改变 Windows 上的代码身份；5.2 第一步必须复现锁定摘要；`pyproject.toml` 与 `uv.lock` 的任何改动都会改变身份。这几项都应在正式十二周运行开始之前集中完成。
- **上游基线：** 能否取得与 `670c94ff` 一致的上游源码尚未核实；不一致时退回到提交基线快照。
- **许可：** AS2 为 Apache-2.0，补丁可以随仓库分发，但修改文件需带修改说明（尚未逐条核对）。LabelKit 没有许可文件且是私有仓库，是否分发由所有者决定。
- **补丁集不会让改动变小：** 约 3.3k 行的差异无论以分支还是补丁形式存在，将来升级上游都会大量冲突。补丁集只是让差异可见、可审查；真正降低升级成本的是 5.2 第 5 步的缩小。
- **Python 3.12：** Windows 新机器需要装 Python 3.12（或使用 uv 管理的 Python）；引导脚本找不到时应明确报错，不能退回 3.13。

### 验收

- 在没有 SSH 密钥、没有 `uv` 的全新 Windows 与 Linux 机器上，引导脚本完成，AS2 摘要等于锁定值，快速层测试通过。
- 同一提交在 Windows 与 Linux 上的 `code_sha256` 相同。
- 覆盖率门禁只用虚拟环境的 Python 即可运行。
- `release` 档位缺 Node 或 LabelKit 时失败；`dev` 档位记录缺失且不报通过。

## 实施顺序

1. **先做低风险、立刻见效的：** 5.1 统一换行（在正式长运行之前）、5.4 覆盖率门禁不依赖 `uv`、4.1 防漂移守卫，以及 4.2 的第 1–5 步。
2. **让环境可以重建：** 5.2 第 1–4 步（补丁集必须复现锁定摘要，运行身份不变）、5.3 引导脚本、5.5 档位、5.6 新机器检查、5.7 同步安装说明与被钉文本。
3. **合并其余共享规则：** 4.2 第 6–10 步；4.3 的不一致项逐项先改规格或合同，再改代码。
4. **缩小 AS2 补丁集：** 5.2 第 5 步，每次缩小单独登记一个 AS2 摘要版本。

与[代码分层、文档测试解耦与测试分层提案](PROPOSAL_2026-10-04_LAYERING_AND_TEST_TIERS.md)的衔接：

- 那份提案第二节把文本钉改成导入断言之后，4.2 中被 `REQUIRED_SYMBOLS` 钉住的几项可以自由移动；在那之前，这几项保留原位置。
- 那份提案第三节的 `scripts/run_tests.py` 与本文 5.4 是同一个脚本，一起实施。
- 4.2 选定的归宿要满足那份提案第一节的分层规则，归宿只能放在比使用者更低的层。

## 本次核查做了什么

- **重复与漂移：** 用 AST 克隆检测、pylint 的重复代码检查与 vulture 的死代码检查扫描 `src` 与 `custom`（工具装在临时虚拟环境里，没有改动项目的 `.venv`）。随后逐项读了相关模块、测试与规格，判定 a、b、c 类。这一部分没有运行测试。
- **环境：** 读取 `pyproject.toml`、`uv.lock`、`.venv` 的安装记录与 AS2 临时副本；把 AS2 分支与 uv 缓存中的上游 2.8.7 wheel 逐文件比较；核对 `runtime_code_sha256` 的输入与本机工作区的换行。没有初始化子模块，也没有访问网络。
- **未核实：** 上游 2.8.7 wheel 与基线 `670c94ff` 是否逐字节相同；Linux 上的代码身份是按 LF 字节推算的，没有在 Linux 上实测；若干 c 类边界情况（例如缺品类）在真实运行中是否可达；`model_dump(mode="json")` 能否产出 NaN。
