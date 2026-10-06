# 对抗审查 r1 — 镜头：需求与裁决覆盖

> 对象：`D:/code/WorldMock/.claude/worktrees/rrs/docs/dev/SPEC-resource-and-rule-simplification.md`（工作树 HEAD `35d0b610fb4a35304e66619048d7500d2d1e05c7`，文件头的代码基线与之一致）。
> 输入：spec-brief、rulings-final（含 §C 取代清单）、rulings-maps、rulings-draft、references、SYNTHESIS §1–§7、提案与三份证据、两份模板规格、INDEX 状态词表与权威层级、design/README 更新约定、AGENTS.md，以及对仓库的只读核对（`custom/agents/life_prompt.py`、`life/vocabulary.py`、`life/schedule.py`、`life/prompt_schemas.py`、`custom/agents/life_agent.py`、`life/sleep.py`、`life/scenario.py`、`opslog.py`、两份 `resources.jsonl`、`scripts/documentation_gate.py` 实跑）。
> 判定口径：会让实现做错、不可测、与未改写的冻结条款矛盾、违反文档门禁、或需求/裁决缺失或被静默改变的为阻断；措辞、顺序与形式问题为次要。

## 0. 结论摘要

- 需求与裁决的**名义覆盖**完整：用户原话每句、提案十个决策点、提案「必须钉住的行为」十三行、SYNTHESIS D-11 … D-52、rulings-final C-1 … C-8 与 §C 的全部取代，都能在裁决记录或正文里找到对应行（见 §1 核对表）。
- **阻断性问题 11 项**，集中在三类：(a) 阶段划分与删除清单、文件归属、夹具、台账互相矛盾，阶段 1 结束时仓库跑不起来；(b) 业务拒绝码、身体常数、账户角色、方式闭集等「唯一定义」缺失或自相矛盾；(c) 两处冻结条款的取代与一处提案原话的改变没有被裁决记录登记，一处指纹断言（B-19）按现行代码不成立。
- 文档门禁 `python scripts/documentation_gate.py` 在本工作树实跑：154 份文档、0 问题、PASSED（锚点与 `path::Name` 引用可解析）。

## 1. 覆盖核对表

### 1.1 用户原话（提案第一章）→ 规格

| 原话（摘） | 规格落点 | 判定 |
| --- | --- | --- |
| 不采用真实地图，随机边联通（每个节点至少一条边），边有长度用于计算移动时间 | D1、C-2、B-12、B-13、「地图与出行」 | 覆盖（「至少一条边」被 D1 收紧为连通，有依据） |
| 地点按类型提供商品，同类型完全一样；餐饮地点可绑定 app，一个 app 多店；无 app 必须到店 | D2、D-29、「店铺、食品与外卖」同类同菜单/app 绑定 | 覆盖 |
| 三类食品固定热量；环境只提供每类 catalog；吃什么由 LLM 按名称与画像决定 | D8、C-4、D-18、D-40、「三类食品与进食」动机层 | 覆盖 |
| 食品必须绑定店铺或 `all`；店铺绑定 app 则可外卖 | D2、`foods.json`、`FOOD_BINDING_MISSING` | 覆盖 |
| 娱乐五类；合成/真实摘要 | `ContentKind`、C-8、`contents.json` | 覆盖 |
| 内容绑定多个 app；persona 只选 app；使用时随机推送绑定内容 | D-12、D-14、「娱乐」活动生命周期 | 覆盖 |
| 早 8–晚 10 推送摘要；session 内打开即指定消费；消费完成后继续随机 | D4、C-6、B-16、「推送与消费的关联」 | 覆盖 |
| 工作只保留主题与工时，播报时 LLM 编造进度 | D10、D-20、D-22、D-49、「工作主题与叙述」 | 覆盖 |
| 环境注册时提供 app catalog 与绑定关系 | `apps.json`、R-F、`APP_UNBOUND` | 覆盖 |
| 两个引擎大幅简化；真实 app/剧集/电影名 + 合成或真实摘要 + 合成数值；分类与交换规则钉死在 WorldMock | D-24、D-25、D-42、D-43、B-17、「数据仓交接」 | 覆盖 |
| 追加：外卖全地图可达 | D-30、D5、T23 | 覆盖 |

### 1.2 提案「必须钉住的行为」十三行 → 验收台账

分类与规则归系统（T1/T2/T5）、地图连通且长度可计时（T8–T10）、同类店铺同商品（T6）、全地图外卖（T23/T25/T26）、app 与店铺绑定有效（T21/T22/T24/T32）、食品热量固定（T27–T29）、娱乐只从所选 app 取内容（T33–T35/T40）、推送与消费关联（T37/T41）、时间窗口精确（T36/T39）、工作由时间决定（T43–T46）、随机和状态可恢复（T38 + T23/T27 断点分句）、app 足迹来自真实动作（T52–T54）、位置记录与地图一致（T20）。十三行各有契约点，括注与提案行名一致。T24 的一个分支在人工环境上不可构造（见阻断 B5）。

### 1.3 提案十个决策点、D-11 … D-52、C-1 … C-8

- D1–D10：十行齐全，每行采纳推荐项，依据列引用提案章节或 references。
- D-11 … D-52：四十二行齐全。逐条与 SYNTHESIS §3 比对，被 C 覆盖处（D-5→C-1、D-8→C-4、D-15→C-5、D-4/16/17→C-6、D-46→C-7、D-42→C-8）均已写「以 C 为准」或直接写 C 值；`foods.json` 没有 `summary`/`summary_source`（C-8 覆盖 SYNTHESIS §4.1）正确。
- C-1 … C-8：八行齐全；常数表的值与 C-1 … C-5 逐项相符（1.2/7.0/5.0/600/500；2、⌊(n−1)/2⌋、[200, 2000]；0.2/0.2/0.3/0.4/0.3；200/300、100/180、600/1200；1800；8 … 21、200 字符）。
- rulings-final §C 的十四条取代（M6、W2/W3、M3/R-D、M4、M2、S3/Q11、S5、S12/A13、Q10、Q15/R-M、W5/C2、R-H、R-O、O9）在「两份早期备忘的条目与处置」里各有「被 X 取代」或「并入」的行。
- rulings-final §D 四个留给规格的点：进度层与 `details` 字段名（B-4）、新错误码英文原因（B-5，但见阻断 B3：引用的表不存在）、系统层中文定稿（B-1 … B-3 与「系统层」节；按 `custom/agents/life_prompt.py` 现行文本核对，B-1 的位置换算 11/12/16 → 12/13/17 正确，`rules[12]` 以「start_activity 一项一个对象」开头、`rules[11]` 含「最接近的候选，不会替你改」的既有断言仍成立）、人工环境内容（B-7）。

### 1.4 提案第二章其余钉住句

「人物离开后仍送至订单地点」「不能靠重复开始进食重复获得热量」「同一人物同一时刻只进行一个娱乐 app 使用活动」「空内容集合明确显示」「摘要可完整展示但不等于消费完成」「session 切换时直接清除推送资格」「工资复用预排事件」「电视剧按集、电影按部」「名称用于展示、内部用标识、同名店铺按地点区分」「登记时一次性检查…」「旧格式直接拒绝、历史运行不可变」「格式切换同时覆盖环境、场景、检查点、足迹和手机交接」「uncle-bob-review 变异」「Windows、macOS、Linux 与换目录恢复分别取证」——均有落点。两处例外见阻断 B9（非食品用品）与次要 M8（仿真数值的声明位置）。

## 2. 阻断性发现

### B1. 阶段划分与删除清单、文件归属、夹具和台账互相矛盾；阶段 1 结束时仓库不能加载任何场景

- 「实施阶段与提交」把场景 7、世界 10、运行身份与加载链（SYNTHESIS P3）放在阶段 2，阶段 1 拥有的文件只有 `exchange/*`、`catalog/*`、`cli.py`、`validation.py`、`data/affordances.toml`、`corpora/`、`scripts/check_reachability.py` 与测试、夹具。
- 但「删除清单 › 生产符号」把几十个 `worldmock.life.*`、`scheduling.*`、`profile.*`、`validation`、`runtime`、`run_setup` 符号标为阶段 1（travel、wayfinding、load_checks、models、state_commit、semantics、scenario、world_setup、engine_context、motivation、candidates、activities、basket、admission、order_routing、named_purchase、purchase_offers、session_actions、vocabulary、time_advance、observe、tool_layer、callable_index、world_query、retrieval、run_identity …），阶段 1 却不拥有这些文件。`life/semantics.py`、`life/category_contract.py` 等直接导入 `worldmock.categories`，阶段 1 删除 `categories/` 而不动它们，包无法导入。
- 「测试与夹具」阶段 1 把三份场景夹具改成结构 7，而 `LifeScenario.schema_version` 的 `Literal[7]` 在阶段 2；T13（阶段 1）要求 `validate --environment` 「加载 scenarios/ 下每个场景并跑场景加载检查」；SYNTHESIS §5.2(5) 要求 `tests/integration/test_external_environment_lifecycle.py`（[QT]）从阶段 1 起通过。三者在阶段 1 都不可能成立。
- 验收台账「环境登记与错误码 | 1 | T3、T4、T7、T13」引用 T4「覆盖注册校验与错误全表」与 T13，而全表含场景级码 `WORK_TOPIC_UNKNOWN`、`MERCHANT_ACCOUNT_MISSING`、`WORK_SCHEDULE_REQUIRED` 与 `ENVIRONMENT_REF_UNKNOWN` 的「场景引用」半句，这些在阶段 2 之前没有定义；同一行还曾引用 T14（阶段 2 的结构 7 用例）。守卫「引用某 T 的台账行所在阶段完成时该 T 已有节点」会在阶段 1 收口时失败或迫使阶段 2 工作提前。

修复（任选其一，全文一致）：
1. 采用 SYNTHESIS 的分组：阶段 1 = P1 + P2 + P3（固定规则、环境格式、场景 7、世界 10、运行身份、加载链，一并清掉对 `worldmock.categories` 的全部导入），阶段 2 = P4（地图与出行），阶段 3 = P5 + P6 + P11；「实施阶段」阶段 1 的文件列表补 `life/{scenario, models, world_setup, engine_context, run_identity, semantics, load_checks, readable, environment_layer}.py`、`runtime.py`、`run_setup.py`；验收台账把「场景 7 与世界 10 的拒绝边界」改为阶段 1（T14–T16）；删除清单里标阶段 1 的 `life.*` 行按文件归属改标 1/2/3；文档改写表的「阶段 2（地图与出行；场景 7、世界 10）」标题随改。
2. 或者保留现行阶段表，但把「测试与夹具」阶段 1 的「三份场景改结构 7」、T13 的场景加载半句、T4 的三个场景级码移到阶段 2，并在「实施阶段与提交」写明阶段 1 只能以目录层 API 验证、`test_external_environment_lifecycle.py` 在阶段 2 恢复——这与「隔离分支上先跑通最小生活闭环」及 SYNTHESIS §5.2(5) 冲突，不推荐。

### B2. 唤醒原因与预排类型的删除阶段早于其来源的删除阶段

- 「删除清单 › 生产符号」阶段 4 行：`WakeReason.TASK_AVAILABLE`、`TASK_DUE_SOON`、`WORK_REVIEW_RESULT`、`LOCATION_HOURS_CHANGE` 与 `WAKE_REASON_NAMES` 对应行；「错误码与唤醒、预排名字」阶段 4 行：预排 `work_available`、`task_expired`。
- 但产生 `work_review_result` 的 `work_review_runtime.py`、产生 `task_available`/`task_due_soon` 的 `rhythm.py`/`schedule.py` 任务输入、`scheduling.events::WORK_AVAILABLE/TASK_EXPIRED` 与 `compile_schedule` 的任务循环，都列在阶段 5 行（同一张表第 577 行）。阶段 4 删掉枚举成员而阶段 5 才删引用它们的代码，阶段 4 提交导入期失败；`ScheduledKind` 的两个成员在两张表里分属阶段 4 与 5，自相矛盾。
- T39（22 个名字）登记在阶段 4 契约点「时间窗口精确」，与上面同样依赖工作链先退出。

修复：把 `task_available`、`task_due_soon`、`work_review_result` 的删除、`work_available`/`task_expired` 的删除、`rhythm`/`schedule` 的任务输入删除统一到阶段 5（与工作链同一提交），阶段 4 只做「加 `app_push`、删 `location_hours_change`（来源已在阶段 1/3 删除）」；T39 改写为「阶段 4 核对 23 个（25 − 1 + 1），阶段 5 核对 22 个」或把 T39 整体移到阶段 5 的「工作面与阶段身份」行；闭集表 `WakeReason` 行加一句分阶段说明。若不愿拆成两步，则把阶段 4 与 5 合并为一个阶段（SYNTHESIS 原本把 P7、P8、P9 放在同一阶段）。

### B3. 业务拒绝码没有唯一定义处；B-5 引用的「活动受理拒绝码」表在本文件里不存在

- B-5：「新错误码的英文原因与下一步见「注册校验与错误」与「活动受理拒绝码」两张表」。本文件只有前者；后者只出现在文档改写表里（指 `docs/CONTRACTS.md#contracts-活动受理拒绝码`），不是本规格的定义。
- `APP_NOT_BOUND`（D-35 新增）在「店铺、食品与外卖」只写了触发条件，没有英文原因、下一步与 `unmet` 值；B-14 的 `unmet` 闭集含 `delivery_app`，全文没有任何码使用它（SYNTHESIS §4.8 把它配给 `APP_NOT_BOUND`，但规格未写）。
- `WORK_TOPIC_NOT_FOUND` 的原因与下一步写在「删除清单」改名列；`APP_CONTENT_EMPTY` 在「娱乐」节；`ORDER_DUPLICATE_ITEM` 在「店铺」节；`already_at_destination` 在 B-11；`FULFILLMENT_NOT_SUPPORTED` 的新原因在「店铺」节与 S12 行。定义散在五处，违反本规格自己写的「本节与「环境格式」「状态、事件、检查点与导出」是…错误码…的唯一定义处」。
- 场景 `environment{environment_id, environment_version}` 与清单不符（T14「environment 钉与清单不符拒绝」）没有码：`ENVIRONMENT_IDENTITY_MISMATCH` 的条件只写文件摘要与 `catalog_content_sha256`。
- `ENVIRONMENT_ID_DUPLICATE`（「app 名与地点显示名经 channel_key 归一后冲突」）与 `APP_NAME_COLLIDES_LOCATION`（「app 名与地点名归一后相同」）条件重叠，T4「每个登记码一个最小反例、码与英文原因相符」无法确定抛哪个。

修复：在「固定规则与常数」下新增小节「活动受理拒绝码」（锚点 `spec-rrs-活动受理拒绝码`），一张表列出本特性新增、改名、改义的全部业务码：`码 | 条件 | 英文原因 | 下一步 | unmet`——`APP_CONTENT_EMPTY`（"This app has no content to use." / "Choose another app."）、`APP_NOT_BOUND`（"This store cannot be ordered through this app." / "Order through an app bound to this store, or go to the store." / `delivery_app`）、`FULFILLMENT_NOT_SUPPORTED`（"This store has no delivery app." / "Go to <store>."）、`WORK_TOPIC_NOT_FOUND`（现有文字）、`ORDER_DUPLICATE_ITEM`（现有文字）、`ACTIVITY_PRECONDITION_UNMET` 的三个 `unmet`（`required_location_id` 现行句、`delivery_destination` "Place the order before departing or after arriving."、`already_at_destination` "You are already at this place."）、`ACTIVITY_DURATION_INVALID`（D-50 句）、`SUPPLY_NOT_AT_STORE`/`SUPPLY_SOURCE_MISMATCH`/`SOURCE_NOT_FOUND`/`SUPPLY_NOT_FOUND`（含义改到食品与店铺的一句）；B-5、B-11、B-14、「店铺」「娱乐」「工作」「删除清单」各处只引用它。注册表补 `ENVIRONMENT_PIN_MISMATCH`（"The scenario pins an environment that is not the one being loaded."）或把它并入 `ENVIRONMENT_IDENTITY_MISMATCH` 的条件；`ENVIRONMENT_ID_DUPLICATE` 条件改为「同一文件内标识或显示名重复」，跨文件 app 名与地点名冲突只归 `APP_NAME_COLLIDES_LOCATION`。

### B4. `BODY_CONSTANTS` 的数值没有定稿，「抄自今天的 `resources.jsonl`」有两份不同的来源

- 常数表只给出 `maximum_energy_kcal = 2400`、`basal_kcal_per_hour = 65`、`hunger 800`、`emergency 400`，其余写「各自单位 … 数值逐项抄自今天的 `resources.jsonl`，不改值」。
- 仓库里有两份：`tests/fixtures/environment/catalog/resources.jsonl`（basal 70、全部 MET 1.0、`awake_sleep_debt_per_hour` 0.1、`sleep_recovery_per_hour` 1.0、`entertainment_need_per_awake_hour` 0.1、效率曲线恒 1.0）与数据仓 `environment/zhangjiang-baseline/catalog/resources.jsonl`（basal 65、MET wait 1.3 / sleep 0.95 / work 1.5 / watch 1.0 / play_game 1.3 / read_mail 1.3 / browse 1.3 / consume 1.5 / driving 1.5 / walking 3.5、债 1 / 恢复 2、需要 0.08）。65 只与数据仓相符，说明写的是数据仓，但规格没点名；两份都会在阶段 1 删除，之后「今天的」无处可查。
- 新活动类型 `use_app` 的 MET 没有任何来源（watch 1.0 还是 browse 1.3）；`chat`、`call` 的 MET 键也未列。
- 这些值进入 T1（OWNERS 单一来源）、T27（净能量）、T44（疲劳）与完整日基线，缺值即不可实现、不可测。

修复：常数表把 `BODY_CONSTANTS` 展开成逐键行：`basal_kcal_per_hour = 65`、`activity_met = {wait: 1.3, sleep: 0.95, work: 1.5, use_app: 1.0, read_mail: 1.3, consume: 1.5, chat: <值>, call: <值>, travel:walking: 3.5, travel:driving: 1.5}`（生活活动用条目自身 `met_value`）、`awake_sleep_debt_per_hour = 1`、`sleep_recovery_per_hour = 2`、`entertainment_need_per_awake_hour = 0.08`、`maximum_energy_kcal = 2400`、`hunger_threshold_kcal = 800`、`emergency_threshold_kcal = 400`，依据列写「抄自 WorldMock-Dataset `environment/zhangjiang-baseline/catalog/resources.jsonl`（数据仓 `2cff671`）；`use_app` 取 `watch` 的 1.0，实验假设」；`chat`/`call` 按今天 `social.py`/注册表的实际 MET 键写值；登记到 `docs/EXPERIMENT.md#experiment-实验假设声明`。

### B5. 人工环境无法构造 T24 的 `SUPPLY_NOT_AT_STORE` 分支（T32 的同类分支同样）

- B-7/「人工环境」：美团绑定 `noodle-1`（牛肉面、薯片、柠檬茶）与 `cafe`（三明治、薯片、柠檬茶）。四种食品都在某家美团店铺的菜单上。
- T24 要求「经美团买三明治以外的、美团店铺不卖的食品 → `SUPPLY_NOT_AT_STORE`」——不存在这样的食品。T32「写无绑定的 app → `SOURCE_NOT_FOUND` 或 `SUPPLY_NOT_AT_STORE`」同理；变异行「指名购买把无绑定 app 当作成功来源」也需要它。

修复：`catalog/foods.json` 加第五种食品，例如 `rice-ball` 饭团（meal，1000 分，`[store]`，只在无 app 的四季便利出售），B-7 改「五种食品」，`manifest.json` 与 README 的计数随改；T24 改为「经美团买饭团 → `SUPPLY_NOT_AT_STORE`」，T32 的分支同样点名饭团。

### B6. B-19 的指纹阶段断言与现行代码不符

- B-19：`tool_schema_sha256` 只在阶段 1、4、6 变；`prompt_sha256` 只在阶段 6 变。「观察与工具面 › 指纹」与阶段 2、6 的文档改写行都重复了它。
- 现行 `custom/agents/life_prompt.py::life_prompt_sha256` 绑定两份系统层与 `prompt_schemas.LAYER_SCHEMAS`；`src/worldmock/life/prompt_schemas.py` 第 35 行 `WAY_LABELS = tuple(way for way in WAYS if way != "购买")` 直接读 `vocabulary.WAYS`，`TARGET_KINDS`、`LIFE_VERBS` 等同样是闭集的活引用（模块头注明「Schema 不维护字面副本」）。阶段 3 删 `WAYS["制作"]`、阶段 4 删观看类方式加「打开」、阶段 5 删 `submit_work` 请求模型，每一步都改变 `prompt_sha256`。
- `custom/agents/life_agent.py::life_tool_schema_sha256` 绑定 `_TOOL_MODELS` 的全部工具 Schema 加注册表字节；阶段 5 删 `submit_work` 即改变 `tool_schema_sha256`，不在 B-19 的「1、4、6」里。

修复：B-19 改为「`tool_schema_sha256` 在改变 `affordances.toml` 字节或 `_TOOL_MODELS` 的每个阶段变（1、4、5）；`prompt_sha256` 在改变系统层文本或七层 Schema 所读闭集（方式、目标类、活动类型、进度记录类型、请求模型）的每个阶段变（3、4、5、6）；`code_sha256` 每个代码阶段都变；没有测试或文档钉字面值，只有落地后的最终值进入运行清单」；「观察与工具面 › 指纹」、阶段 2 与阶段 6 文档改写行、`docs/CONTRACTS.md#contracts-seven-layer-schema` 的分阶段叙述同句改写。

### B7. 闭集表的 `WAYS` 行漏掉八个保留方式

- 闭集表：`WAYS` = 到店、远程下单、购买、打开、食用、使用、给予、进行（8 个）。
- 现行 `src/worldmock/life/vocabulary.py::WAYS` 还有 步行、驾车（`travel_to`）、工作、交谈、通话、阅读、等待、睡觉；同表 `START_ACTIVITY_ACTIONS` 仍含 `travel_to`、`work`、`chat`、`call`、`read`、`wait`、`sleep`，「地图与出行」的地点目标动作写「步行、驾车」。按字面实现 `WAYS` 行会删掉出行、工作、交谈、通话、读信、等待、睡觉的方式，与全文矛盾。

修复：`WAYS` 行改为十六个：步行、驾车、工作、交谈、通话、阅读、进行、等待、睡觉、到店、远程下单、购买、打开、食用、使用、给予；变化列保持「删 观看、接着看、从头再看、接着听、从头再听、制作、浏览、游玩；加 打开→（`interact`, `use_app`）」；补一句同义表：「查看」只剩 {阅读}，「做饭」「看」「收看」「听」「收听」「玩」「逛」「逛逛」「继续看」「重看」「重听」等随被删方式一起删除。

### B8. 本规格声称取代的三条冻结裁决没有在裁决记录里登记

- 「现行文档原位改写」前言：「同时做两件事规格的 R5、R6 与让「等待」成为例外规格的 R12 由本规格的裁决记录取代，不回改」。裁决记录（五张表）里没有任何一行写到 SPEC-parallel-activities R5（驾车一行「只能再听播客或和人说话」）、R6（退役名清单）或 SPEC-idle-fillers R12（「环境不替人物选择内容」）。SYNTHESIS §2 也要求把它们记在新规格的裁决记录里。
- 另外 SPEC-idle-fillers D7「不改动钉死 147 个变异名的守卫」被 D-47（守卫放宽为 登记 ∩ 基线）取代，同样未登记。

修复：「本规格补定」加三行：B-21「同时做两件事规格 R5 的驾车一行改为只能和人说话（播客随 `watch` 退出），R6 的退役名清单加 `watch`、`screen`、`play_game`、`browse`；该规格的乙表与丙文由本规格「娱乐 › 占用」与「系统层」第 8 条取代」；B-22「让「等待」成为例外规格 R12「环境不替人物选择内容」改为「环境在人物所选 app 内选内容」，等待仍始终列出、照常受理；D7 的 147 守卫由 D-47 放宽」；依据列写「本规格补定；SYNTHESIS §2」。

### B9. 提案原话「本表不删除其他现有生活服务和非食品用品」被静默改变

- 提案第二章：「本表不删除其他现有生活服务和非食品用品；它们使用系统固定类型，保留生活所需行为，退出通用品类继承和交换配对」。
- 规格：R-E「非食品商品与原料退出目录」、F6「耐用品退出目录；`initial_inventory` 只装食品」、「三类食品与进食 › 删除：…耐用品使用」。R-E 的依据写「并入 D7、「环境格式」」，D7 是做饭，与耐用品无关；没有一行说明提案这句被谁、为什么取代。用户采纳的是提案全部推荐，提案本身把非食品用品写为保留。

修复：R-E 行的依据改为「取代提案第二章「本表不删除…非食品用品」的后半句：SYNTHESIS §4.1 的五类目录没有耐用品，持有物只有食品；生活活动（生活服务）照旧保留在 `corpora/lifestyle/items.json`」，并在「用户得到什么 › 不做的事」加「不保留耐用品与非食品商品」。若裁决方不接受这一改变，则需另定 `goods.json` 之类的第六类目录——那是更大的范围变化，应由用户裁决而不是由规格默认。

### B10. 场景 7 把 `buyer` 写成 `accounts[]` 的声明角色，与现行隐式人物账户矛盾

- 「场景结构 7 › 新增」：「`accounts[]` 的角色 `buyer`、`merchant:<location_id>`…」；同节末尾「不设隐式账户：卖家、雇主、交通与外部账户都是场景声明」。
- 现行 `src/worldmock/life/scenario.py` 第 595 行：「人物账户（buyer/traveler/worker 三个能力角色）由 agents[] 隐式声明」，`agents[].initial_balance_fen` 建人物账户；人工环境的 `accounts[]` 今天只有 employer、transport、merchant、external。规格「不做的事」又写「不改变…账本…的机制」。要求场景显式声明 `buyer` 账户是未经裁决的机制改变，且 T14「结构 7 受理」与夹具描述（账户 `buyer`、`merchant:noodle-1`…）会按此实现。

修复：「场景结构 7」改为「`accounts[]` 只声明 `merchant:<location_id>`（每个已选食品店铺一个）、`employer:*`、`transport:service`、`external:scenario`；人物账户仍由 `agents[]` 的 `initial_balance_fen` 隐式建立，角色 `buyer`/`traveler`/`worker` 不变」；「人工环境」三份场景的账户清单去掉 `buyer`。

### B11. `ENVIRONMENT_NUMBER_INVALID` 的条件与 `work_topics.json` 的 `target_hours: 正数` 矛盾

- 目录文件表：`target_hours: 正数`，`target_seconds = round(target_hours × 3600)`（允许 2.5）。
- 注册码表：`ENVIRONMENT_NUMBER_INVALID`——「`price_fen`、`duration_seconds`、`target_hours`、`length_m`、`seed` 不是有限正数或不是整数」，把 `target_hours` 也要求为整数；`seed` 又被要求为正数，而生成器种子只需是整数。T4 的反例无法确定 2.5 小时是受理还是拒绝。

修复：条件改为「`price_fen`、`duration_seconds`、`length_m` 不是正整数；`target_hours` 不是有限正数；`seed` 不是整数」；英文原因改为 "A declared number is outside its allowed domain."。

## 3. 次要发现

| 编号 | 位置 | 问题 | 修复 |
| --- | --- | --- | --- |
| M1 | 文档阶段 8 表 `docs/INDEX.md` 行、「数据仓交接」末段 | 「阶段 9 待数据仓发布后登记回执」「等发布后补的回执」用了 待/等 标记，brief 要求未执行事实只写「未执行」 | 改为「阶段 9 的回执在数据仓发布后登记；发布前写「未执行」」 |
| M2 | 裁决记录 O11 行 | 写「被「观察与工具面」占用表取代」，占用表实际在「娱乐：app、内容与推送 › 占用」 | 改指「娱乐 › 占用」 |
| M3 | 文档阶段 4 表（`contracts-运行诊断输出`「`OPS_COMPONENTS` 加 `push`」）与阶段 7 表（减 `review`） | `OPS_COMPONENTS` 的新成员只出现在文档改写表，没有定义处 | 在「状态、事件、检查点与导出」加一行：`OPS_COMPONENTS` = engine、agent_runtime、memory、commerce、travel、checkpoint、runtime、push（删 review） |
| M4 | 目录文件表 | `environment_version`、`files[].size_bytes`、`summary_source.url/notice`、`series.episode` 没有类型；`app_ids`、`store_ids` 是否允许重复未写 | 补类型（`environment_version: str`、`size_bytes: int ≥ 0`、`url: str 或 null`、`notice: str 或 null`、`episode: 正整数`）与「列表元素唯一、按标识排序」 |
| M5 | 「店铺 › 持有」「`initial_inventory` 的键必须是目录食品」 | 未写拒绝码 | 并入 `ENVIRONMENT_REF_UNKNOWN` 的条件列 |
| M6 | 「娱乐 › 活动生命周期」 | 内容恰在 `planned_end_at` 到达 `duration_seconds` 时是否记完成未写；首条内容的 `next_interaction_at` 初值未写 | 写「到达时刻 ≤ `planned_end_at` 即完成（含相等）」「开始时 `next_interaction_at = min(start + 内容剩余, planned_end_at)`」 |
| M7 | 质量报告 5 `content_contact.repeat_watch = completed_rounds > 1` | 与「娱乐」的 `repeat = 开始时 completed_rounds ≥ 1` 口径不同且时点不明 | 定义为「`content_consumed` 事实中开始时 `completed_rounds ≥ 1` 的条数」 |
| M8 | D-42「清单层面声明一次」 | 价格、时长、边长为仿真数值的声明落在哪份文件未写 | 在「数据仓交接」与 `tests/fixtures/environment/README.md` 行写明：环境 README 声明一次 |
| M9 | `PUSH_SUMMARY_MAX_CHARS` | 「字符」未定义 | 写 `len(summary)`（Unicode 码点） |
| M10 | `DeliveryState` | 到店订单的 `path`、`distance_meters`、`prep_seconds` 取值未写 | 写 `path = [store_id]`、`distance_meters = 0`、`prep_seconds = 0` |
| M11 | 「场景结构 7」`interaction_menus_by_location_kind.allowed_actions` 取自新 `START_ACTIVITY_ACTIONS` | 阶段 2 时 `use_app` 尚未存在 | 加「`use_app` 自阶段 4 起可写」 |
| M12 | 「删除清单 › 测试文件」 | SYNTHESIS §1 列出的 `tests/life/test_category_inventory_protocol.py`（耐用品与配方部分）、`test_storefront_recovery.py`（→ 环境身份）、`tests/integration/test_minimum_complete_path.py`（硬编码张江）未出现 | 补入阶段 1 行 |
| M13 | D-39 | `CONTENT_EMPTY_WITH_MEDIA_TASTES` 的 `details` 键未写（SYNTHESIS 写 `{card_content_genres, media, habits}`，genre 已删） | 写 `details{media, habits}` |
| M14 | 「场景结构 7 › 新增」 | `agents[].initial_inventory` 是既有字段改形，不是新增 | 移到「保留」并注明键改为 `{location_id: {food_id: 份数}}` |
| M15 | B-8 | 未带 idle-fillers R8 的「表外行并回表内」规则 | B-8 补一句 |
| M16 | T36 | 夏令时用例需要带 DST 时区的场景，人工环境时区未写 | 「人工环境」写时区（如 Asia/Shanghai），T36 注明以 `mutate` 改时区 |
| M17 | 「观察与工具面 › 工具」 | 「`_STABLE_TOOL_SURFACE` 八个名字」正确，但「原生工具 11 个」与 `_TOOL_MODELS` 的 `finish`（只读问答）关系未写 | 注明 `finish` 不在生活决策工具面 |

## 4. 已核对无问题的点（摘要）

- 文件头五行齐全，`代码基线` 等于工作树 HEAD；状态词 `FROZEN` 在 INDEX 词表内；版本表一行、在文末。
- 唤醒闭集 22 个的声明次序 = 现行 `schedule.py::WakeReason` 去掉四个成员再追加 `app_push`；`SLEEP_ALLOWED_REASONS` 四项不动。
- `ActivityKind` 10、`START_ACTIVITY_ACTIONS` 11、`ScheduledKind` 7、`TARGET_KINDS` 11、`RESOURCE_CLAIM_SLOTS` 8 + 13 个退役名、输入警告 6 个、`FoodCategory` 3、`ContentKind` 5、`LocationKind` 8 与 `FOOD_STORE_KINDS` 4，全文一致。
- 系统层：B-1 的位置换算与现行 `LIFE_SYSTEM_LAYER` 相符（第 2 条含 `submit_work`，第 12 条含「内容」，第 13 条含「渠道」，第 17 条含「有效工时」）；改写后仍满足 `rules[12]` 以「start_activity 一项一个对象」开头、`rules[11]` 含「最接近的候选，不会替你改」、「只是登记」「available 为 false 的会被拒绝」各一次等既有断言；第 7、8、14、15、16 条的登记变异原文逐字保留。
- 每个契约点至少一个变异，变异表各行可对应到 T 编号；「删除枚举成员属导入期失败」与模板一致。
- 数据仓交接列出了文件、删除、改写工具、投影、校验命令与最低数量，并把回执登记为未执行。
- 文档门禁实跑通过。
