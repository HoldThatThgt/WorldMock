# 对抗审查 · 可实现性 · 第 2 轮

审查对象：`docs/dev/SPEC-resource-and-rule-simplification.md`，代码基线
`694cb55e6b9da958521031ae689db49dc654aff2`。审查开始时规范 SHA-256 为
`48a6ba2f6f154cab8ac7e57bb02f57328dc4bf9083c5b322fcf5cf19f4c48f77`。

结论：**BLOCKED，6 项阻断**。第 1 轮可实现性审查的八项发现中，七项已经真正闭合；原 B1 的整文件删除次序已经修正，
但阶段 1 的纯测试收集仍会经测试夹具导入完整引擎，并在已删声明与后续消费者之间断裂，因此 B1 整体仍不能判为关闭。本文只审查
规范是否能按阶段实现；尚未实现的生产代码本身不算发现。

## 输入与方法

完整阅读了规范、`handoff/resource-rule-simplification/rulings-final.md`、
`handoff/resource-rule-simplification/understand/SYNTHESIS.md` 的 adopted §3/§4、第一轮可实现性报告和
`revision-closure.md`。随后按当前源码核对 pytest 收集入口、生活引擎导入链、测试分层选择、导出生命周期、验收守卫、源码变异执行器和
完整日基线入口。没有修改规范、生产代码、测试、夹具或 Git 状态。

最可能推翻本审查的假设是：阶段表中的“拥有的文件”允许未列出的测试基础设施在任意阶段改写。规范同时要求文件所有权、同提交解除钉住处
和机器台账按真实阶段登记，所以本审查按表中显式所有权解释；若作者意图允许隐式所有权，必须把它写入表，否则实施者无法唯一落位。

## 第一轮八项关闭映射

| 原发现 | 第 2 轮判定 | 证据 |
| --- | --- | --- |
| B1 阶段 1 删除仍被后续导入的模块 | **未完全关闭** | B-33 和删除表已把整文件移到最后消费者阶段；但 `tests/life/conftest.py` 仍在收集时导入 `LifeEngine`，阶段 1 又删除 `ContractKind`、`InventoryItemConfig` 等仍被后续模块导入的声明。见下文“纯门禁不能收集”。 |
| B2 闭集成员早于最后消费者 | 已关闭 | `location_hours_change` 在阶段 3、`app_push` 在阶段 4、任务唤醒与预排在阶段 5；25 → 24 → 25 → 22 的次序、测试和查看器同步点一致。 |
| B3 推送首次打开与重开冲突 | 已关闭 | 娱乐生命周期统一为 `status != released`；首次 `pending → opened`，同 session 重开沿用 `opened`，完成或 session 失效后才失去资格。 |
| B4 `APP_NOT_BOUND` 原因与 `delivery_app` 冲突 | 已关闭 | B-14/B-28 和“活动受理拒绝码”给出唯一条件、原因、下一步，并明确该码不写 `unmet`。 |
| B5 不足一分钟工作文案与测试 | 已关闭 | D-50 给出唯一主题句和 `Choose another activity.`；阶段 5 同时拥有合同、grounded observation 与 paused slots 改写。 |
| B6 机器守卫读不到变异表 | 已关闭 | 机器登记明确用两个锚点分别读取台账/行为表和变异表。 |
| B7 `WAYS` 漏保留成员 | 已关闭 | 闭集列出最终十六个方式并由 T11/T42 守住。 |
| B8 不存在的测试路径与节点 | 已关闭 | 使用现有 `tests/integration/test_observation_agent_seam.py`，并保留实际进度 Schema 节点名只改断言。 |

## 阻断：阶段 1 声称测试最短路，但图实现和 T10 属于阶段 2

**观察事实**

- 规范“环境格式”把 `shortest_m(a, b)` 列为冻结在 `Catalog` 上的派生索引。
- “随实现一起改的测试与夹具”的阶段 1 目录测试明确包含“手工图最短路与决胜”。
- 验收台账把“最短路与完整路径决胜”及 T10 放在阶段 2；实施表也只在阶段 2 新建
  `src/worldmock/catalog/graph.py` 和 `tests/catalog/test_graph.py`。

**影响与推理**

阶段 1 若通过自己登记的测试，就必须提前实现阶段 2 的图；若遵守阶段 2 文件所有权，阶段 1 的测试没有真实定义可调用。用空方法、延迟异常或
旧地图实现都会违反 B-33 和无兼容要求。

**最小修复**

阶段 1 的派生索引和测试只覆盖非图索引；从阶段 1 测试行删除“手工图最短路与决胜”。在阶段 2 加入
`Catalog.shortest_m`、`graph.py`、`test_graph.py` 和 T10，保持现有依赖顺序。不要为了保留阶段 1 文字提前建立图占位接口。

## 阻断：阶段 1–4 的纯门禁和全仓收集仍会急切导入完整引擎

**观察事实**

- 阶段 1 测试行登记了 `tests/life/test_scenario.py`、`test_load_checks.py`、`test_session_state.py`、
  `test_state_serialization.py` 和 `test_semantics.py`，并称它们为纯模型与加载边界。
- pytest 收集这些文件前必定执行 `tests/life/conftest.py`；该文件在模块顶层导入
  `agentsociety2.society`、`worldmock.life.engine.LifeEngine`、`ModelIdentity` 和 `RunIdentity`
  （当前第 17–21 行）。根 `tests/conftest.py` 与 `tests/frozen_inputs.py` 没有这个问题：前者只加载环境 profile，后者把引擎导入放在方法内。
- `LifeEngine` 顶层导入 `session_actions`；`session_actions` 顶层导入 `category_contract`、`inventory_recipe`、`basket` 和工作链。
  `category_contract.py` 当前第 16 行导入 `ContractKind`，`inventory_recipe.py` 当前第 16 行导入
  `InventoryItemConfig`，`basket.py` 当前第 13、23、27 行导入旧地图、供给、报价与库存类型。
- 规范在阶段 1 删除 `ContractKind`、`InventoryItemConfig`、`MapDatasetReference`、供给和世界旧字段，而这些生产消费者分别到阶段 2、3、5
  才退出。`tests/life/test_semantics.py` 还在顶层直接导入 `ContractKind` 与 `map_data`；`test_load_checks.py`、
  `test_session_state.py` 顶层直接导入 `LifeEngine`。
- T42 属于阶段 4，却要求 macOS 的快速、标准、全量层均可收集。阶段 5 才改写的测试仍会导入
  `RuntimeWorkTask`、`WorkTaskConfig` 和完整引擎；保留旧声明、重导出或别名来维持收集均被规范禁止。

**影响与推理**

阶段 1 的“纯”节点在测试函数执行前就进入完整引擎导入链，无法形成规范声称的独立门禁。即使只把生产导入改成局部导入，阶段 4 的全仓收集
仍早于阶段 5 的最后工作消费者，只有保留旧类型或同步提前改写阶段 5 测试才能通过。前者是兼容层，后者违反文件所有权。

**最小修复**

- 把 `tests/life/conftest.py` 明确列为阶段 1 所有：生产引擎和 AS2 导入移入真正使用它们的 fixture/helper；类型标注用
  `TYPE_CHECKING` 或字符串，不在收集时导入运行链。
- 阶段 1 登记的纯测试模块不得在模块顶层导入 `LifeEngine` 或已经删除的声明；`runtime.py` 和 `validation.py` 的纯路径也要把完整运行依赖放到
  对应分支内。为这个边界登记一个真实 `--collect-only` 命令/测试。
- 阶段 1–4 只要求各阶段明确节点完整收集，不宣称全仓三层收集。把 T42 中“三层均能收集”移到阶段 5 集成波次结束后或 T56；阶段 4
  只验证 TextWorld、游戏码和游戏方式退出。
- 阶段 5 同步改写最后的工作消费者和其测试后，才第一次要求完整收集与 `LifeEngine` 持久化闭环。不得保留
  `ContractKind`、旧场景类或兼容别名过渡。

## 阻断：阶段 1 的变异行把阶段 3 的 T27 当作杀手

**观察事实**

变异表把“加载器接受目录食品行里的 `kcal` 字段并用它覆盖类别热量”登记在阶段 1，并写由 T5、T27 杀死。T27 的“食品热量固定”契约点和
完整进食行为属于阶段 3。机器登记要求阶段 1 就加入参数化变异执行，杀手在未变异源码上先通过，收集错误不得冒充杀死。

**影响与推理**

阶段 1 无法运行 T27 的进食、暂停恢复和检查点行为；把空节点或未来节点放入 `SourceMutation.tests` 会使变异基线收集失败。只运行 T5 又与表中
“被谁杀死”的最终要求不一致。

**最小修复**

从该阶段 1 变异行删除 T27，只保留 T5。T5 已直接拒绝食品的 `kcal` 字段，足以杀死这个加载器变异；T27 继续在阶段 3 验证固定热量和结算，
无需提前实现运行链。

## 阻断：阶段 5 要求标准层通过，但导出与仍含旧工作字段的消费者归阶段 7

**观察事实**

- `tests/tier_support.py::selected` 当前第 488–502 行规定：标准层选择除 `mutation`、`day_run` 外的全部测试，没有“尚未到阶段 7”的过滤。
- 规范要求阶段 5 跑标准层，同时明确阶段 5 只验运行与持久化，导出、重算、`validate_export` 到阶段 7 才验收。
- 当前 `tests/integration/test_external_environment_lifecycle.py::test_external_environment_survives_actions_reopen_and_export`
  在同一节点中先重开，再调用 `export_run`、`validate_export`；另一个 lifecycle 节点也在恢复后导出。阶段 5 若不拆节点，就无法只跑持久化。
- 标准层还会执行阶段 7 才拥有的现有测试。可直接证伪的例子包括：
  `tests/footprints/test_validation.py` 当前第 144 行仍要求阶段 `{action, work_review}`；
  `tests/footprints/test_quality.py` 当前第 914 行、`tests/evaluation/test_behavior_metrics.py` 当前第 274 行及
  `tests/delivery/test_windows.py` 当前第 94 行仍构造 `work_review` 或 `submit_work`。阶段 5 已要求删除这些生产分支。

**影响与推理**

遵守阶段 5 删除清单会让标准层中的旧消费者失败；提前完成阶段 7 的格式和测试又破坏“阶段 7 才验收导出”的边界。跳过、标记或改变标准层选择
都被规范禁止。

**最小修复**

阶段 5 使用明确的持久化节点和阶段 1–5 相关节点门禁，不运行标准层。把当前 lifecycle 用例拆成一个改名后的阶段 5 节点
（截止于 checkpoint/close/open/state equality/continue scheduling）和阶段 7 的导出/重算节点。所有因阶段 5 删除 `work_review`、
`submit_work` 而变化的非导出测试同阶段改写；第一轮标准层移到阶段 7 完成导出切换后，阶段 8 再跑标准、全量和覆盖率。另一种修复是把整个
P12 提前到阶段 5，但这会扩大最小引擎闭环并违背已经采用的阶段边界，不建议。

## 阻断：物理删除要求同提交解除守卫，但验收守卫又统一归阶段 8

**观察事实**

- 删除清单的总则要求每个 `[TI]`、`[DF]`、`[PP]`、`[TE]`、`[EW]`、`[IF]`、`[SR]`、`[LA]`、`[OWN]`、
  `[API]`、`[DOC]`、`[IG]`、`[QT]` 钉住处在删除的同一提交解除。
- “随实现一起改的测试与夹具”却把旧特性登记、分层夹具、公开 API、所有权夹具和 `fresh_check` 的逐项退役统一放在阶段 8；实施表也把
  “全部验收守卫及夹具”归阶段 8。
- 当前守卫会真实失败，而非只留下文字：`tests/test_tool_interaction_acceptance.py::FEATURE_SOURCES` 要求阶段 2/3 删除的
  `map_models.py`、`map_data.py`、`inventory_recipe.py`、`storefront_state.py`、`corpora/storefront.py` 仍存在；
  `tests/dayrun_fidelity_acceptance.py::FEATURE_PRODUCTION_FILES` 仍列 `map_data.py`、`map_models.py`、`coverage_apps.py`、`taste.py`；
  `tests/test_layering_acceptance.py::NEW_PRODUCTION` 仍列阶段 5 删除的 `work_review_runtime.py`。

**影响与推理**

实现者不能同时遵守“物理删除同提交解除钉住处”和“守卫文件阶段 8 才归属”。阶段 5 的完整收集或标准层也会在文件存在性守卫处失败。把守卫
留红到阶段 8 会把真实断链当成允许的中间状态，并与 B-33 的独立阶段提交、机器登记和无延期要求冲突。

**最小修复**

按删除表的标签把九个现行守卫、登记模块和夹具的具体行分配到实际删除阶段；每个旧文件、旧节点、旧变异和固定计数在最后消费者退出的同一
提交改指或删除。阶段 8 只拥有最终 `STAGES_DONE == {1…8}`、最终文件集合、完整日基线和“九守卫全部通过”的汇总判定。阶段 0 已拥有的
147 字面量放宽保持阶段 0；`NEW_PRODUCTION` 中 `work_review_runtime` 的退役则明确归阶段 5。

## 阻断：Python 源码变异器不能把 `tools/` 当作可导入的变异根

**观察事实**

- 机器登记与 T56 要求 `source_location` 对 `worldmock.*`、`custom.*`、`scripts.*`、`tools.*` 四根各有正例，并声明这些都是“可变异生产根”。
- `tests/mutation_runner.py` 当前第 53–65、105–141、176–203 行的机制是：模块名映射到 `.py`，`ast.parse` 找定义，
  `compile(..., "exec")` 编译，再以 `sys.meta_path` 在 Python 导入时注入。
- 当前 `tools/` 只有 `run-viewer.html`、`run-viewer-context.js`、`run-viewer-context-view.js`、
  `run-viewer-context.css`，没有可导入的 Python 模块；阶段 6 也只拥有这些 HTML/JavaScript/CSS 文件，没有新增 Python 工具模块或
  JavaScript 变异执行器。

**影响与推理**

`source_location("tools.x::symbol")` 最多能返回一个不存在的 `tools/x.py` 字符串；它无法通过 `mutated_source`、实际导入和杀手测试。
只测路径字符串会产生假绿；新增 JavaScript AST/加载执行器则是规范没有描述的额外机制。

**最小修复**

保留 `FEATURE_PRODUCTION_FILES` 的四个文件根，但把 Python `SourceMutation` 的可变异根改为三个：`worldmock.*`、`custom.*`、
`scripts.*`。T56 对这三个给正例，对 `tools.*` 与 `tests.*` 给拒绝例。查看器行为继续由现有 Node 用例验证；若产品确实要求 JavaScript
源码变异，必须另行规定成熟解析器、符号定位、隔离执行和回执，不能把它伪装成现有 Python 机制。

## 已验证可实施的边界与诚实缺口

- 阶段 5 的最早诚实持久化边界是：人工环境登记与加载 → `LifeEngine.create` → 真实观察后等待或生活活动 → 推进 → 有效人物快照与
  checkpoint → close/open 后身份和状态相等 → 继续调度。导出不属于这个边界；完成上面的标准层修复后，阶段 7 才加入
  `export_run`、重算和 `validate_export`。
- 纯 CLI 边界的方向可实施：`cli.py` 删除 category 子命令，`validation.py` 的 `--environment` 路径只局部导入目录与场景加载器，
  project 校验依赖也改成其分支内导入。规范需要同样明确测试夹具和 `runtime.py` 的导入隔离，不能只修 CLI。
- B-34 给出了可变异的生产函数、两个候选的固定形状、逐项相等条件、原子写和平台/锁文件身份；`scripts.*` 可由 Python 变异器支持。
  当前完整日测试仍参数化两个不同种子，阶段 8 必须改为同一场景、同一种子两次真实运行后再调用该函数。这个改写的最终合同已唯一，未发现
  新阻断。
- 当前审查主机实测为 `Darwin arm64`，系统 `python3` 为 3.9.6；本基线尚无 `.python-version`，规范要求阶段 1 新增唯一
  `3.12.13`。因此本轮没有伪称执行尚未实现的阶段 8 双跑。`tests/dayrun_baseline.py` 当前 SHA-256 为
  `0df08156ec91278d49794dad0fdd09344f12b55064fafd842940831b3e4d373e`，只读检查未改其字节。
- Windows、正式数据仓环境、其他平台时区数据库和换目录恢复均可按阶段 9 如实登记未执行；这不阻断阶段 1–8，也不能由 macOS 结果推定。

## 复核命令与结果

| 命令 | 结果 |
| --- | --- |
| `git rev-parse HEAD` | `694cb55e6b9da958521031ae689db49dc654aff2` |
| `shasum -a 256 docs/dev/SPEC-resource-and-rule-simplification.md`（开始） | `48a6ba2f6f154cab8ac7e57bb02f57328dc4bf9083c5b322fcf5cf19f4c48f77` |
| `rg`/`nl` 定位上述规范、源码与测试定义 | 六项阻断均有真实定义或阶段表证据；没有用文件名推断能力 |
| `find tools -type f` 与 `find tools -name '*.py'` | 四个查看器文件；Python 文件为零 |
| `python3 --version`、`uname -srm` | `Python 3.9.6`；`Darwin 27.2.0 arm64`，不冒充阶段 8 的 CPython 3.12.13 回执 |

没有运行实现测试：当前提交只有未冻结规范修订，阶段 1–8 的生产实现尚不存在；用旧代码的通过或失败不能证明新规范可执行。审查采用静态导入链、
pytest 选择规则和真实测试节点来判定阶段闭环。

规范结束 SHA-256：`48a6ba2f6f154cab8ac7e57bb02f57328dc4bf9083c5b322fcf5cf19f4c48f77`（必须与开始值相同）。
