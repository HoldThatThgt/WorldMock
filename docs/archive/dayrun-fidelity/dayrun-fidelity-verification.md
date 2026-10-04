> 代码基线：147843655aa00621488a306e68ecf98c5273b3bf
> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/dayrun-fidelity-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 一日运行保真特性验收记录

> 状态：**验收记录**。实施阶段 1–5 全部合入；合入过程中的集成核查、实现后文档审计与独立语义突变审查发现的缺陷由四个修复分支按裁决修复并补测，阶段 6 的三路独立语义突变审查、验收登记表与全量门禁完成，阶段 8 的实现后文档随本记录收尾。最终代码 `1478436` 通过全量测试、风格、覆盖率、文档与完整性门禁；三路独立语义突变共 356 个，除 4 个等价突变外全部被测试杀死（52 个初次存活，补测后杀死）。规格 7.5 的 strict 端点探针结论记在本记录。阶段 7（真实复验，只报告）未执行：它需要数据集仓库按数据集方案修复后的输入与用户的模型账号。实现状态文档（文档同步作业 IS1）由最后一个提交单独更新，不在本记录的提交内。
> 日期：2026-09-29
> 依据：[一日运行保真特性规格](SPEC-dayrun-fidelity.md)（FROZEN，版本表 0.7）的[验收台账](SPEC-dayrun-fidelity.md#验收台账)；阶段、文档同步作业与文件修改清单见规格「实施计划」「文档同步作业」「文件修改清单」三章（冻结时留在规格内作实施台账）。行为以规格为准，本记录只登记证据，不另定行为。

本记录是规格 7.5（探针结论写入验收记录）、「实施计划」阶段 7（复跑指标写入验收记录）与文档同步作业 IX1（验收记录入口）所指的特性验收记录：
登记源码身份与提交、评审与修复、全量门禁与覆盖率、验收台账的证据登记、独立语义突变审查、strict 端点探针、代码与规格的出入、
文档一致性与锚点核对，以及未执行的真实复验。验收台账各行的证据节点取自 `tests/dayrun_fidelity_acceptance.py`，
由 `tests/test_dayrun_fidelity_acceptance.py` 逐行判定；本记录不另建一份清单。

## 源码身份

| 身份 | 固定值 |
| --- | --- |
| 最终代码提交 | `147843655aa00621488a306e68ecf98c5273b3bf`（`1478436`）：阶段 1–5、全部修复、三路突变审查的补测与验收登记表合入后，为覆盖率门禁标出的五个接口补行为测试的提交，是全量测试与覆盖率门禁的被测源码。其父提交 `9bdc95e` 删去 `body.apply_discomfort_pulse` 中自 `04c215b` 起没有调用方能到达的零幅度提前返回，是 `9a5f508` 之后唯一的生产代码改动；三路突变审查运行时这一行还在，它不可达，审查结论不受影响 |
| 覆盖率报告提交 | `b7f85f9cc810a553868634f2724f9830c9f3e556`（`b7f85f9`），只含 `docs/dev/coverage-report.json`，经 `8ac56a0` 合入集成分支；此后到本记录的提交只改文档 |
| 实施基线 | `26d626b59902bccf7b3cdc5ba74037e5feee5f72`（`26d626b`，规格草稿提交）；规格与台账中的代码行号取 `4a5b0e6`（`98eb34b` 与 `26d626b` 只增加文档，代码相同） |
| 规格冻结 | 冻结提交 `4f9ac80`（规格冻结、提案标为已裁决）、`50f1efa`（冻结条款修订落到各特性规格）、`4c3e050`（实验假设、门禁与架构说明），经 `f7f9bfd` 合入集成分支，规格 0.5；冻结后补登台账行「更简单的购买请求」（0.6，`b734e8a`），实现复核后细化 3.5「收敛」第 2 条（0.7，`10693f8`） |
| 分支 | 集成分支 `dayrun-fidelity-impl` 自特性分支 `dayrun-fidelity` 的 `185cad7` 起，逐批合入各实现、修复、审查与文档分支；`dayrun-fidelity` 自 `26d626b` 起，其上的 `14a6cfe`、`185cad7` 是另一会话的设计说明书汇编（`scripts/build_design_doc.py` 与 `docs/design/worldmock-design.html`，并给规格补登作业 DD1），不属本特性 |
| AS2 | 锁定提交 `8768e14`，未改动；本机 `vendor/agentsociety2` 子模块未检出。第四章与 7.2 要求的三个覆写点（`_protocol_feedback_content`、`_parse_react_responses`、`_append_react_protocol_feedback`）都在 `custom/agents/life_agent.py` 的 `LifeAgent` 中 |
| 覆盖率快照 | `snapshot_id` 为 `e100130f-4612-41e3-aa1d-9280298c99f6`，`manifest_hash` 为 `d810f515db96e449f5083bb5484af3e91a6008421d451db3cda8223af670396f`，`source_identity` 为 `1478436` 的完整哈希，生成于 `2026-09-30T01:12:32+00:00` |
| 数据集仓库 `WorldMock-Dataset` | 未改动：本特性只写接口（规格第九章「数据集仓库」一行），`prepare_labelkit_input.py` 与 `simulate_phone.py` 的改动在数据集仓库另行提交；测试不读数据集仓库，所用的两次真实运行的记录复制为 `tests/fixtures/` 下的最小夹具 |

`git log --first-parent 26d626b..8ac56a0` 的提交，按合入顺序（「重新汇编」指合入后按作业 DD1 重新生成 `docs/design/worldmock-design.html`）：

| 提交 | 阶段与规格条款 | 内容 |
| --- | --- | --- |
| `14a6cfe`、`185cad7` | 不属本特性 | 另一会话的设计说明书汇编（见上表「分支」行） |
| `f7f9bfd` | 冻结 | 规格冻结与全部【冻】作业（`4f9ac80`、`50f1efa`、`4c3e050`） |
| `3d8ab21`、`8e5126d` | 2：1.2（裁决 D3、S1） | 下限缓解 `f + (1 − f)·w`、有缓解的活动进行时不累积娱乐需要、游戏按内容的算法算口味权重；重新汇编 |
| `e529a1b`、`4a8a6ab` | 1、2：1.1、1.4、1.5 | 社交账与共同用餐按本区间 `body_settlement.activity_ids` 判定清醒，困意档界按 KSS 等价刻度重标定，不适脉冲的两小时门槛只判一次，睡眠中不读消息；重新汇编 |
| `9f44b4e`、`a32efb0` | 1：8.1、7.1、7.5 探针 | 地点类别越界与重名标签加载即拒，`branch_label` 与地图结构 2，中文摘录按字数核对，只读的 strict 探针脚本；重新汇编 |
| `8920e88`、`ac848bb` | 2：1.3、2.1 | 运行输入警告（含 8.1 的两个警告码）冻结进运行清单（清单 `schema_version` 升 2）、ops 日志与 `quality.json`（`schema_version` 升 3），`validate` 打印同一组警告；场景显式声明的作息覆盖卡片抽取值；重新汇编 |
| `8fd8525`、`debd5cc` | 2、5：2.2、2.3、8.2 | 系统层 18 条规则，零点计划只写与平常不同的事，例行意图不入推进比例，目标大致时刻 `around_time`；重新汇编 |
| `9681f93`、`f48db1d` | 3：第四章 | 宿主协议纠错与环境拒绝共用错误结构，每个会抛出的码都有具体原因；重新汇编 |
| `ca5c02a` | 5：7.3、7.4 | 联系人回复只远程往来，请求补发件人、此刻、远程与收货地，后验守卫与兜底句；时间线只出现中文名称，质量报告两节 |
| `e60a6e7` | 5：第六章 | 结束、播报、暂停改为一项一个对象，看法与偏好放 `views`，全部读者经 `control_items` |
| `398a782` | 修复 | FIXUP-1（见「评审与修复」） |
| `ca4bf59` | 5：7.2 | 截断回合丢弃、纠错替换不叠加、关闭思考并 `tool_choice=required` 重试一次，`quality.json` 的 `truncations` 一节 |
| `7d7ed65` | 5：7.5 | 探针改用能区分两端的判据；`base_url` 切到 `https://api.deepseek.com/beta`，`validate`、恢复与打开核对新端点 |
| `bd91d41` | 5：7.5 | 按完整探针改写接口合同「模型身份」的 `base_url` 行与 `model_endpoint.py` 的注释：`/beta` 在请求时校验 Schema，但不保证输出取值受约束 |
| `86edb54` | 4：5.1、5.3 | 生活活动施加地点类资格，相关度与哈希决胜，按每个对象的 `last_presented_at` 轮转；浏览结果列完整标签，下一步提示只指向可达地点，两种配送原因 |
| `34a41ee` | 3：3.1、3.4 | 统一名称解析（宿主与环境共用），名称类拒绝不计同刻轮数、不施加脉冲 |
| `5810e6b` | 6 | 突变审查 MUT-B 的补测 |
| `9b97ca8` | 第九章「查看器」 | 运行查看器总览列出清单冻结的输入警告 |
| `1a8d5e5` | 6 | 突变审查 MUT-A 的补测 |
| `fa611cf` | 修复 | FIXUP-2 |
| `f68b6fb` | 8 | 实现后文档：【同】作业逐节按代码核对，MN1–MN5、AN1 与 IX1 的【后】部分，测试矩阵与各规格冻结时写下的标记按实现改写 |
| `ba5028e` | 4：5.2 | 工具层只列名称的可调用索引 `callable_index` |
| `f6cb273` | 修复 | FIXUP-3 |
| `e377669` | 3：3.2、3.3、3.5（含阶段 1 的 3.2 先行小修） | `from` 为可选约束与分段匹配 M0–M3，App 名按场景钉定的手机配置解析为渠道，请求级 `prefer` 与方式「购买」，订单与 `purchases.jsonl` 记 `channel_id` |
| `cc66419` | 8 | 实现后文档续：3.2、3.3、3.5、5.2 的合同、设计章节与手册，余下的冻结时标记按实现改写 |
| `8a0c7c6` | 6 | 验收登记表与完整性守卫；规格补登台账行「更简单的购买请求」（0.6） |
| `8af2efa` | 修复 | FIXUP-4 |
| `9a5f508` | 6 | 突变审查 MUT-C 的补测（含对 FIXUP-4 新逻辑的突变） |
| `0e508c0` | 6 | 登记 FIXUP-4 与 MUT-C 的测试；规格 3.5「收敛」第 2 条按实现细化（0.7），人物 Prompt、时间与能量、一日行为三份规格的镜像句同步 |
| `8ac56a0` | 6 | 全量门禁：`9bdc95e`、`1478436` 与覆盖率报告 `b7f85f9` |

实现不按阶段编号依次合入，而按文件重叠分批并行：第一批睡眠与需要、下限缓解、输入警告、加载校验、系统层与计划、错误合同、控制工具、
联系人与时间线八个分支（与冻结文档分支同时），第二批呈现、截断、名称解析与端点切换，第三批指名购买与渠道、可调用索引。
每个分支合入前在自己的 worktree 跑所改模块及其导入方的测试与风格门禁；阶段 0 的实施前全量基线没有单独执行，全量证据是阶段 6 在最终代码上的运行。

## 评审与修复

各批合入后，编排方的集成核查、实现后文档的逐节审计（DOCS-POST 与其续审）与独立语义突变审查 MUT-B 发现的缺陷，按规格裁决分给四个修复分支；
每项修复都补了改回旧代码即失败的行为测试。MUT-A 与 MUT-C 没有发现生产缺陷。

| 修复 | 发现方 | 缺陷与修法 | 回归用例 |
| --- | --- | --- | --- |
| FIXUP-1（`398a782`） | 第一批合入后的集成核查 | 创建运行时计算输入警告要核验场景钉定的手机配置，身份、Schema 或可执行性有问题时抛 `IntegrityError`，给创建运行添了一条失败路径，违背 1.3「运行路径永不因警告失败」与「不新增故障路径」：运行路径核验失败时记一条英文日志警告、不带 App 别名计算覆盖率（`validate` 照旧失败）。档界边界预测在有缓解的活动进行时仍按清醒累积速率外推娱乐需要，观看时预测出向上跨档的边界（1.2）：改用两条结算路径共用的净速率。`LOCATION_NAME_SUSPICIOUS` 检查的显示名含 `branch_label`：补测确认普通分店标签不报，标识或坐标形态的标签照报 | `tests/integration/test_input_warnings.py::test_a_phone_config_that_fails_verification_never_fails_run_creation`、`tests/life/test_relief_floor.py::test_a_watch_session_creates_no_upward_entertainment_band_boundary`、`tests/life/test_relief_floor.py::test_plain_awake_time_still_predicts_the_upward_entertainment_crossing`、`tests/life/test_load_checks.py::test_branch_labels_are_checked_as_part_of_the_display_name` |
| FIXUP-2（`fa611cf`） | MUT-B 突变审查 | `conclude_activity` 只报第一条关于未知人物的看法，一项里未知的 `activity_name` 还会掩盖另一项的 `SUBJECT_NOT_KNOWN`（3.1 第 5 条「全部名称类失败项都报」、第四章）：控制工具一支收集全部活动名问题与每个未知 `about` 各一条 `SUBJECT_NOT_KNOWN`（所在项的 `index`，字段 `about`），合并成一次纠错 | `tests/integration/test_host_feedback.py::test_every_unknown_relationship_view_is_reported_at_its_own_item`、`tests/integration/test_host_feedback.py::test_an_unknown_activity_name_does_not_hide_an_unknown_view_in_another_item` |
| FIXUP-3（`f6cb273`） | 编排方复核 FIXUP-2 时发现的环境一侧同类缺口；实现后文档审计 | 绕过宿主的 `report_status`、`pause_activity`、`conclude_activity` 在环境侧遇到第一个未知活动名就以不带 `items` 的 `ACTION_REQUEST_INVALID` 拒绝（3.1 第 5 条「环境逐项解析后合并成一次拒绝」）：逐项解析、合并为一次带 `items[]` 的拒绝，明细与宿主相同，顶层码仍计同刻轮数。为别人远程下单送不到时一律写「暂时送不到<名>那里」（5.3 的两种配送原因只分在给自己的一侧）：按收货地不在配送范围与没有能走的配送路线分成两句。`observe._menu_windows` 的说明写生活活动窗口按地点与此刻取候选，实际只施加地点类资格：改正说明，行为不变 | `tests/life/test_control_name_rejections.py::test_two_unknown_activity_names_are_both_reported_in_one_rejection`、`tests/life/test_control_name_rejections.py::test_an_ambiguous_and_an_unknown_name_are_reported_with_their_own_codes`、`tests/life/test_control_name_rejections.py::test_the_restricted_round_recheck_reports_every_unknown_name_too`、`tests/life/test_control_name_rejections.py::test_a_known_and_an_unknown_name_report_only_the_unknown_second_item`、`tests/life/test_giving.py::test_remote_order_for_someone_outside_the_delivery_area_is_a_destination_refusal`、`tests/life/test_giving.py::test_remote_order_for_someone_without_a_delivery_route_is_a_route_refusal` |
| FIXUP-4（`8af2efa`） | 实现后文档的续审 | 方式「购买」人在出售门店里就定为到店，即使收货人不在店里，结算随即拒绝：到店只在东西在店里交得到收货人手上时才算能服务，否则远程下单，候选可行性与 M3 候选同一判定。「购买」两者都不行时下一步可能指向只能远程下单的门店：改指路线最短的能到店买的出售门店，没有就与远程下单的拒绝同一说法，送给别人时为「先送到自己这里，再当面给」；规格 3.5「收敛」第 2 条随后按实现细化（0.7）。`purchases.jsonl` 只把字面的到店、远程下单、购买记为尝试下单，同义写法不计：按方式同义表归一后计入，`way` 列记归一后的方式 | `tests/life/test_giving.py::test_system_purchase_for_someone_goes_in_store_only_when_they_are_there`、`tests/life/test_giving.py::test_system_purchase_for_someone_it_cannot_reach_says_to_buy_for_oneself_first`、`tests/life/test_purchase_stages.py::test_system_purchase_next_step_names_the_nearest_store_that_sells_it_in_store`、`tests/footprints/test_purchases.py::test_real_run_export_counts_orders_written_with_way_synonyms`、`tests/footprints/test_purchases.py::test_order_attempts_classify_written_ways_through_the_way_synonym_table`；改写 `tests/life/test_wc_named_purchase_replay.py::test_goods_that_cannot_reach_the_receiving_place_say_why_and_where_the_store_delivers`（原先断言的正是指向只能配送门店的下一步） |

另有两处补漏：第九章「查看器」一行（`tools/run-viewer.html` 显示清单的 `input_warnings`）在第一批分工中没有指派，集成核查发现后由单独的分支补上
（`9b97ca8`，查看器用例经 `tests/tools/test_run_viewer.py::test_run_viewer_recorded_names` 运行）；首次覆盖率门禁标出的六个未覆盖公共接口见下节。

## 全量门禁与覆盖率

| 门禁 | 结果 |
| --- | --- |
| 全量测试 | 在 `1478436` 的代码上（`b7f85f9` 只多覆盖率报告），覆盖率下六个分片：4,987 passed、7 skipped、0 failed、25 warnings（警告数与人物 Prompt 特性的门禁相同）；252 个测试文件，收集 4,993 项另加一个模块级跳过；pytest 用时合计约 2:42:50，六个分片并行、墙钟约 40.5 分钟。跳过都是既有的平台或产物条件跳过：TextWorld 在锁定文件中没有 Windows wheel 1 项、Windows 账户没有符号链接权限 2 项、资金锚定运行产物不在本机 4 项；本特性新增用例无跳过，`b9f88c3` 还去掉了一项符号链接跳过。没有另跑一次不带覆盖率的 `pytest tests`，分片覆盖率运行就是全量证据 |
| 首次门禁（已取代） | 在 `9a5f508` 的代码上同样六个分片：4,982 passed、7 skipped、0 failed；门禁只因 6 个未覆盖公共接口判 FAILED（分支 94.96%，7,910/8,330）：`body.apply_discomfort_pulse` 的零幅度提前返回不可达，`9bdc95e` 删去；`name_resolution.dice`、`name_resolution.written_place`、`named_purchase.resolve_named_rows`、`routing.fill_route` 与 `life_truncation.truncated` 的防护或回退行没有测试走到，`1478436` 各补一个走真实请求的行为测试（`tests/life/test_name_resolution.py::test_a_blank_name_is_no_object_and_gets_no_candidates`、`tests/life/test_name_resolution.py::test_candidates_for_a_held_item_are_filled_with_the_consume_route`、`tests/life/test_purchase_stages.py::test_home_written_as_from_is_the_persons_home_where_nothing_is_sold`、`tests/life/test_purchase_stages.py::test_a_listed_item_and_a_named_item_resolve_item_by_item_and_are_bought_together`，以及 `tests/integration/test_output_truncation.py::test_only_a_reply_that_ran_out_while_thinking_is_a_truncation` 的 `no_choices` 一例）；没有发现产品缺陷 |
| 风格门禁 | `tests/test_style_gate.py::test_style_gate_admits_no_new_violations` 通过，没有新增违规 |
| 覆盖率门禁 | 分支覆盖率 95.05%（covered=7,916，missing=412，total=8,328；阈值 85.00），未覆盖公共接口 0，`passed` 为 true；报告随 `b7f85f9` 提交，口径与基线见[覆盖率门禁口径与基线](coverage-baseline.md) |
| 文档门禁 | `8ac56a0`：`scripts/documentation_gate.py` 扫描 130 份文档，0 处断链；`scripts/build_design_doc.py --check` 通过；本记录所在提交见「文档一致性」 |
| 完整性守卫 | `8ac56a0` 的树上 89 项通过：风格 1、文档门禁 6、设计说明书汇编 11、一日运行保真完整性守卫 28、人物 Prompt 完整性守卫 25、时间与能量完整性守卫 8、覆盖率门禁 10 |

| 分片 | 测试文件 | 结果 | pytest 用时 |
| --- | --- | --- | --- |
| life1 | 42 | 796 passed | 1824.52 秒 |
| life2 | 42 | 889 passed | 1859.63 秒 |
| life3a（`tests/life/test_callable_index.py`、`tests/life/test_tool_layer.py`） | 2 | 32 passed | 1505.42 秒 |
| life3b | 41 | 861 passed | 2403.44 秒 |
| integration | 37 | 504 passed | 1434.53 秒 |
| misc（delivery、evaluation、footprints、`tests/life/test_engine_branch_coverage.py`、顶层、catalog、categories、corpora、profile、resources、scheduling、tools） | 88 | 1,905 passed、7 skipped | 742.07 秒 |

本机 `uv run` 不可用（AS2 子模块未检出，`uv` 找不到可编辑依赖），测试一律用虚拟环境解释器执行，并经全机共用的并发限制器（至多六个 pytest 进程）排队。
分片时每个目录的测试文件在同一分片内连续：同一次调用里某目录的文件被别的目录隔开时，pytest 8 会报 `fixture 'engine_factory' not found`。
查看器回归经 VS Code 自带的 Electron 以 Node 方式运行。LabelKit 用例经目录联接借用主工作区的 LabelKit 虚拟环境运行
（`PYTHONDONTWRITEBYTECODE=1`）：`tests/delivery/test_labelkit_process.py` 的 43 项与 `tests/footprints/test_validation.py` 中依赖 LabelKit 的 4 项都执行并通过，
核对主工作区的 `vendor/labelkit` 没有新建或改动任何文件，联接随后删除。各分片合并数据（`coverage combine`）后，以虚拟环境解释器调用 `scripts/coverage_gate.py` 的 `main()`：
以分片的测试退出码（全部为 0）代替其内部的全量测试，以虚拟环境的 `python -m coverage json` 代替 `uv run coverage json`，判定逻辑、阈值与报告格式不变；
报告中的 `command` 字段仍是脚本常量 `uv run coverage run -m pytest -q`。

## 验收台账逐行登记

完整性守卫 `tests/test_dayrun_fidelity_acceptance.py`（28 项）以 `tests/dayrun_fidelity_acceptance.py` 为数据源判定：登记表的键与规格「验收台账」各行一一对应
（台账增删行而登记表不跟随即失败）；「裁决记录」第一张表恰为 D1、U1–U4，第二张表恰为 D2–D19、S1–S11，除 D13（登记）与 S2（撤回）外都已定，
定下了要建之物的已定裁决都登记了测试；「复跑指标（只报告）」在规格中仍写明只报告；输入警告的十个码逐码各一例，且测试体断言该码；
每个节点都解析到存在、会被收集且未被跳过的测试（含类中的测试与参数化节点）；本特性新增或改动的 85 个生产文件（含查看器页面）与新增的 32 个测试文件
没有延期标记，Python 文件没有空壳函数；`docs/` 不再留有本特性冻结时写下的临时标记；带锚点的文档链接都指向现有标题。

下表逐行列出登记表为该行登记的节点数与所在测试文件（括号内为该文件的节点数），节点本身以登记表为准，全部在上节的全量测试中通过。
各行节点合计 409 个（一个节点可守卫几行），去重后 379 个，另有 1 个只为裁决登记，登记表共 380 个不重复节点，分布在 78 个测试文件中。

| 契约点 | 节点数 | 证据所在 |
| --- | --- | --- |
| 睡眠计费 | 7 | `tests/life/test_sleep_billing.py`（7） |
| 下限缓解 | 22 | `tests/life/test_relief_floor.py`（12）、`tests/life/test_scenario.py`（5）、`tests/life/test_motivation.py`（4）、`tests/integration/test_host_branch_coverage.py`（1） |
| 输入警告 | 22 | `tests/profile/test_injection.py`（14）、`tests/integration/test_input_warnings.py`（5）、`tests/footprints/test_quality.py`、`tests/integration/test_run_manifest.py`、`tests/profile/test_coverage.py`（各 1） |
| 困意档界 | 11 | `tests/life/test_sleep_two_process.py`（9）、`tests/life/test_felt_states.py`（2） |
| 不适脉冲与消息 | 9 | `tests/life/test_discomfort_and_sleep_messages.py`（6）、`tests/life/test_same_moment_decisions.py`（2）、`tests/life/test_felt_states.py`（1） |
| 场景作息 | 15 | `tests/profile/test_injection.py`（6）、`tests/integration/test_input_warnings.py`（5）、`tests/life/test_scenario.py`、`tests/integration/test_runtime.py`、`tests/life/test_persona_layer.py`、`tests/life/test_persona_upgrade.py`（各 1） |
| 系统层 | 7 | `tests/integration/test_seven_layer_request.py`（4）、`tests/life/test_day_planning.py`（2）、`tests/life/test_day_plan.py`（1） |
| 名称解析 | 32 | `tests/life/test_name_resolution.py`（19）、`tests/life/test_control_name_rejections.py`（4）、`tests/integration/test_host_feedback.py`（3）、`tests/life/test_giving.py`（2）、`tests/life/test_request_linking.py`、`tests/life/test_interaction_menu_invariants.py`、`tests/test_start_activity_contract.py`、`tests/life/test_engine_feedback.py`（各 1） |
| 指名购买 | 25 | `tests/life/test_named_purchase.py`（11）、`tests/life/test_purchase_stages.py`（6）、`tests/life/test_error_contract.py`（3）、`tests/life/test_wc_named_purchase_replay.py`（2）、`tests/test_start_activity_contract.py`、`tests/life/test_purchase_channels.py`、`tests/life/test_giving.py`（各 1） |
| 购买渠道 | 16 | `tests/life/test_purchase_channels.py`（12）、`tests/profile/test_coverage_apps.py`（2）、`tests/footprints/test_purchases.py`、`tests/life/test_wc_named_purchase_replay.py`（各 1） |
| 名称类预算 | 7 | `tests/life/test_name_resolution.py`（6）、`tests/footprints/test_quality.py`（1） |
| 更简单的购买请求 | 30 | `tests/life/test_purchase_stages.py`（8）、`tests/life/test_named_purchase.py`（6）、`tests/life/test_purchase_channels.py`（4）、`tests/life/test_giving.py`（4）、`tests/test_start_activity_contract.py`（2）、`tests/integration/test_activity_schema.py`、`tests/integration/test_storefront_tools.py`、`tests/life/test_prompt_layers.py`、`tests/life/test_callable_index.py`、`tests/life/test_wc_named_purchase_replay.py`、`tests/footprints/test_purchases.py`（各 1） |
| 错误合同 | 20 | `tests/life/test_error_contract.py`（8）、`tests/integration/test_host_feedback.py`（8）、`tests/life/test_control_name_rejections.py`、`tests/life/test_name_resolution.py`、`tests/life/test_parallel_admission.py`、`tests/integration/test_activity_schema.py`（各 1） |
| 呈现 | 37 | `tests/life/test_callable_index.py`（9）、`tests/life/test_presentation_fidelity.py`（8）、`tests/life/test_rotation.py`（6）、`tests/life/test_candidates.py`（4）、`tests/life/test_relevance.py`（3）、`tests/integration/test_seven_layer_request.py`（2）、`tests/integration/test_presentation_habits.py`、`tests/life/test_coverage_closure_projection.py`、`tests/life/test_same_moment_decisions.py`、`tests/life/test_prompt_layers.py`、`tests/life/test_tool_layer.py`（各 1） |
| 浏览与提示 | 16 | `tests/life/test_browse_and_hints.py`（8）、`tests/life/test_wayfinding.py`（4）、`tests/life/test_giving.py`（2）、`tests/life/test_prompt_layers.py`、`tests/life/test_tool_layer.py`（各 1） |
| 控制工具 | 19 | `tests/life/test_control_items.py`（5）、`tests/integration/test_conclude_memories.py`（4）、`tests/integration/test_activity_schema.py`（2）、`tests/test_start_activity_contract.py`、`tests/life/test_progress_layer_projection.py`、`tests/life/test_persona_layer.py`、`tests/tools/test_run_viewer.py`、`tests/delivery/test_windows.py`、`tests/footprints/test_quality.py`、`tests/life/test_progress_events.py`、`tests/life/test_memory_evidence.py`（各 1） |
| 中文摘录 | 12 | `tests/life/test_work_review_excerpts.py`（12） |
| 截断处理 | 13 | `tests/integration/test_output_truncation.py`（10）、`tests/footprints/test_quality.py`（3） |
| 联系人回复 | 11 | `tests/life/test_contact_reply_guard.py`（10）、`tests/footprints/test_quality.py`（1） |
| 时间线 | 10 | `tests/footprints/test_render_day.py`（3）、`tests/life/test_activity_title_embedding.py`（3）、`tests/footprints/test_quality.py`（2）、`tests/life/test_timeline_labels.py`、`tests/life/test_readable.py`（各 1） |
| 加载校验 | 15 | `tests/life/test_load_checks.py`（8）、`tests/life/test_map_dataset.py`（3）、`tests/profile/test_injection.py`（2）、`tests/life/test_tool_layer.py`、`tests/integration/test_input_warnings.py`（各 1） |
| 目标推进 | 15 | `tests/life/test_day_plan.py`（5）、`tests/life/test_goal_around_time.py`（3）、`tests/evaluation/test_goal_metrics.py`（3）、`tests/life/test_day_planning.py`（2）、`tests/life/test_todo_layer.py`、`tests/footprints/test_goal_export.py`（各 1） |
| strict 端点 | 18 | `tests/test_probe_deepseek_strict.py`（12）、`tests/integration/test_strict_endpoint.py`（3）、`tests/integration/test_runtime.py`、`tests/life/test_coverage_closure_models.py`、`tests/profile/test_extractor.py`（各 1） |
| 复跑指标（只报告） | 12 | `tests/footprints/test_quality.py`（8）、`tests/footprints/test_purchases.py`（2）、`tests/evaluation/test_goal_metrics.py`、`tests/footprints/test_goal_export.py`（各 1） |
| 文档一致性 | 4 | `tests/test_dayrun_fidelity_acceptance.py`（2）、`tests/test_documentation_gate.py`、`tests/test_design_doc_build.py`（各 1） |
| 工程门禁 | 4 | `tests/test_coverage_gate.py`（2）、`tests/test_style_gate.py`、`tests/test_documentation_gate.py`（各 1） |

- 「输入警告」一行写「九个警告码各一例」，按 1.3 的表行数计（`CARD_FIELD_OVERRIDDEN` 与 `CARD_FIELD_CONFLICT` 同行）；警告码闭集是 1.3 的八个码加 8.1 的
  `DELIVERY_ROUTE_UNAVAILABLE`、`LOCATION_NAME_SUSPICIOUS`，十个码各登记一例。
- 「更简单的购买请求」是冻结后补登的行（规格 0.6），判据取 3.5 的验收句，随 0.7 的细化同步；登记的测试覆盖细化后的行为。
- 「strict 端点」一行登记探针脚本与端点切换的测试；「探针在主站与 `/beta` 的结果写入验收记录」一半见本记录「strict 端点探针」。
- 「复跑指标（只报告）」登记的是复跑要读的质量报告各节与导出的 `purchases.jsonl` 的测试，真实复跑本身未执行（见「真实复验（阶段 7，只报告）」）。
- 「文档一致性」一行除文档链接门禁与设计说明书汇编外，登记守卫的临时标记与锚点两项判定，一次性核对的结果见「文档一致性」；「工程门禁」一行的结果见上节。
- 「裁决记录」：D1、U1–U3、D2–D12、D14–D19、S1、S3–S11 共 31 条各登记 2–9 个节点（S10 九个）；U4（已定，没有要建之物）、D13（登记，范围外）、S2（撤回）只核对状态。

## 独立语义突变审查（阶段 6）

按验收标准「一日运行保真特性门禁」，三路互相独立的审查各自在干净提交的镜像副本或隔离 worktree 上做 Uncle Bob 语义突变：每个突变是对生产源码的一处精确替换，
恢复规格「根因」或「阶段 6 突变审查的目标」所述的旧错误，不改测试；先跑最可能检出的测试，存活再扩到相关目录；存活的突变补一个走真实引擎、宿主或导出路径的
行为测试，确认它在突变下失败、还原后通过，再以原补丁复验；每次还原逐字节核对。突变脚本、补丁与日志保存在编排会话的临时目录，不进仓库。

| 审查 | 基线 | 覆盖的条款 | 突变 | 既有测试杀死 | 补测后杀死 | 等价 |
| --- | --- | --- | --- | --- | --- | --- |
| MUT-A | `86edb54` | 1.1、1.2、1.4、1.5、2.2、8.2、5.1、5.3、8.1、7.1 | 113 | 103 | 9 | 1 |
| MUT-B | `86edb54` | 第四章、第六章、7.2、7.3、7.4、1.3、2.1、7.5 | 104 | 88 | 15 | 1 |
| MUT-C | `e377669`；分段匹配、渠道、「购买」与导出另在合入 FIXUP-4 后的代码上重跑 | 3.1、3.4、3.2、3.3、3.5、5.2 与 FIXUP-4 | 139 | 109 | 28 | 2 |
| **合计** | | | **356** | **300** | **52** | **4** |

MUT-B 报告的总述写作 106 个突变、90 个被既有测试杀死，与它逐组列出的突变表（19、25、12、19、29 个，合 104）和结果文件不符；上表按逐组表与结果文件计。

**MUT-A**（各目标的突变数；初次存活的补测）

| 目标 | 突变 | 初次存活与补测 |
| --- | --- | --- |
| 睡眠计费的清醒分类（1.1） | 11 | 交谈与通话的覆盖按推进后的集合判定：`tests/life/test_sleep_billing.py::test_a_call_ending_at_the_boundary_is_billed_once_by_the_call_ledger` |
| 下限缓解与累积口径（1.2） | 20 | 共同结算取并行活动缓解的最大值而非总和：`tests/life/test_relief_floor.py::test_parallel_relieving_activities_add_up_in_the_shared_settlement`；另 1 个等价（见下） |
| 困意档界、不适脉冲与睡眠中的消息（1.4、1.5） | 15 | 按累计而非新增的可计费小时施加脉冲、不适结束时不清零起点与已计小时、「有点乏」也算困意不适、饥饿不适漏掉「饿」四个：`tests/life/test_discomfort_and_sleep_messages.py::test_each_boundary_is_billed_only_for_its_new_hours_past_the_threshold`、`tests/life/test_discomfort_and_sleep_messages.py::test_discomfort_counts_only_the_hungry_and_sleepy_bands` |
| 系统层条数与档位名、推进的开始时刻（2.2、8.2） | 19 | 无 |
| 地点类资格、哈希决胜、轮转判据与可达提示（5.1、5.3） | 29 | 换新名额也受标签相似度上限、配送提示不查配送路线两个：`tests/life/test_rotation.py::test_quota_is_capped_by_subtype_count_only_not_by_tag_similarity`、`tests/life/test_wayfinding.py::test_delivery_step_skips_places_without_a_working_delivery_route`，另加真实引擎用例 `tests/life/test_browse_and_hints.py::test_remote_order_hint_skips_reachable_places_the_store_cannot_deliver_to` |
| 加载拒绝与摘录字数门槛（8.1、7.1） | 19 | 评审模型接受时交付评估跳过摘录核对：`tests/life/test_work_review_excerpts.py::test_every_assessment_excerpt_is_checked_whatever_the_reviewer_decides` |

**MUT-B**

| 目标 | 突变 | 初次存活与补测 |
| --- | --- | --- |
| 错误结构键闭集（第四章） | 19 | 合并的环境拒绝取最后一项的码：`tests/life/test_error_contract.py::test_a_mixed_item_rejection_takes_the_first_items_code_and_next_step` |
| 控制工具的上限校验与 `views`→`subject`（第六章） | 25 | 恰 500 字的 summary 被拒、旧平行数组的记忆组数不核对、`SUBJECT_NOT_KNOWN` 总指向第一项三个：`tests/integration/test_activity_schema.py::test_control_summaries_are_limited_to_500_characters_by_the_local_check`、`tests/life/test_control_items.py::test_unrecognised_or_malformed_requests_fail_as_integrity_errors`（新增一例）、`tests/integration/test_host_feedback.py::test_a_view_about_an_unknown_person_in_the_second_item_is_reported_at_index_one`；审查同时发现宿主只报第一条未知看法，转 FIXUP-2 |
| 截断回合丢弃（7.2） | 12 | 截断判定漏掉「`finish_reason=length`」「正文为空」「没有工具调用」之一的三个：`tests/integration/test_output_truncation.py::test_only_a_reply_that_ran_out_while_thinking_is_a_truncation` |
| 守卫命中即重试与时间线标签（7.3、7.4） | 19 | 删去一个未被真实样本钉住的守卫分支、标识形态漏掉长十六进制串或 `#rrggbb` 三个：`tests/life/test_contact_reply_guard.py::test_each_registered_alternative_catches_its_short_form`（25 个分支各一例）、`tests/footprints/test_quality.py::test_timeline_labels_count_hexadecimal_and_hash_identifiers_as_internal_ids` |
| 输入警告、场景作息与 strict 端点（1.3、2.1、7.5） | 29 | 习惯触发词不查看、刷、听，引擎直驱的清单冻结 `[]` 而非 null，探针凭任意失败下三项结论，共五个：`tests/profile/test_injection.py::test_a_habit_whose_trigger_watches_or_scrolls_counts_as_a_media_habit`、`tests/integration/test_run_manifest.py::test_engine_direct_runs_keep_input_warnings_null_and_unrecorded`、`tests/test_probe_deepseek_strict.py::test_conclusions_need_the_specific_outcomes_not_just_any_failure`；另 1 个等价 |

**MUT-C**

| 目标 | 突变 | 初次存活与补测 |
| --- | --- | --- |
| 名称归一唯一才受理、候选不替换（3.1） | 46 | 十二个：无损折叠一级被跳过，「家」与前缀增删不看动词与方式，冗余判定出错（`from` 对着住处而非此地比较、出行的 `to` 等于此地也算冗余），没有路线的地点成为候选，「（模拟）」与各种括号不归一，未列出活动的候选缺时长说明，`to` 与 `answering` 的名称不解析。补测：`tests/life/test_name_resolution.py::test_a_mail_written_in_other_brackets_is_that_mail_and_a_bare_subject_is_ambiguous`、`tests/life/test_name_resolution.py::test_the_verb_and_way_decide_the_prefix_so_a_shared_bare_name_stays_unique`、`tests/life/test_name_resolution.py::test_simulated_marks_and_other_brackets_are_normalised_away`、`tests/life/test_name_resolution.py::test_candidates_for_unlisted_activities_carry_the_catalog_duration_as_their_note`、`tests/life/test_name_resolution.py::test_places_without_a_listed_route_are_never_candidates`、`tests/life/test_name_resolution.py::test_redundancy_is_judged_against_the_current_place_not_home`，加强 `tests/life/test_name_resolution.py::test_home_words_map_to_the_home_location_and_other_spellings_only_get_candidates`，`tests/life/test_giving.py::test_a_recipient_written_without_the_prefix_is_resolved_to_that_person`、`tests/life/test_request_linking.py::test_an_answering_name_written_without_spaces_is_resolved_to_that_message` |
| 名称类拒绝不计轮（3.4） | 16 | 免计额度不按人物分（两种）、不按秒重置、重放的名称类拒绝也免计四个：`tests/life/test_name_resolution.py::test_each_person_has_their_own_two_free_name_rejections`、`tests/life/test_name_resolution.py::test_the_two_free_name_rejections_start_again_in_the_next_second`、`tests/life/test_name_resolution.py::test_a_replayed_name_rejection_always_counts_a_round`；另 1 个等价 |
| 分段匹配的段序（3.2） | 19 | M1 先于 M0、合并拒绝的码取第一项而不看是否名称类、M3 候选只取 `from` 范围三个：`tests/life/test_purchase_stages.py::test_the_name_stage_is_judged_before_the_source`、`tests/life/test_purchase_stages.py::test_a_name_class_item_leads_the_merged_rejection_even_when_it_comes_second`、`tests/life/test_purchase_stages.py::test_an_unservable_source_offers_the_same_goods_that_can_be_served_elsewhere`；「购买」旧下一步的那一个在合入 FIXUP-4 前已被杀死，其目标行随后被 FIXUP-4 改写（被取代），新结构由下面 FIXUP-4 一组覆盖 |
| 渠道、`prefer` 与「购买」（3.3、3.5） | 30 | 渠道称呼逐字比较或多做宽度与括号折叠、到手价未知的排在最前、途中算在刚离开的门店里、列出的标签带 App 名不按渠道重新解析五个：`tests/life/test_purchase_channels.py::test_a_channel_name_is_compared_without_spaces_or_case`、`tests/life/test_named_purchase.py::test_a_store_whose_price_is_unknown_is_never_the_cheapest`、`tests/life/test_purchase_stages.py::test_system_purchase_on_the_road_is_not_buying_in_the_store_just_left`、`tests/life/test_purchase_channels.py::test_a_listed_label_with_an_app_in_from_is_resolved_against_that_channel`；「最近的门店」按标识选或选最远两个在基线存活，合入 FIXUP-4 后被其用例与新增的 `tests/life/test_purchase_stages.py::test_goods_sold_by_several_stores_point_to_the_store_with_the_shortest_route` 杀死 |
| FIXUP-4 的交付判定与同义方式 | 10 | 只能到店的供给当作能送达、候选可行性不看能否交到收货人手上两个：`tests/life/test_giving.py::test_system_purchase_for_someone_of_goods_sold_only_in_store_cannot_reach_them`、`tests/life/test_giving.py::test_a_pickup_candidate_for_someone_not_at_the_store_is_not_ranked_as_feasible` |
| 可调用索引（5.2）与收货人的配送原因（FIXUP-3） | 18 | 先查路线后查送达地、为别人到店也按配送原因拆分两个：`tests/life/test_giving.py::test_named_remote_order_for_someone_it_cannot_reach_says_why_like_a_listed_one`（新增一例）、`tests/life/test_giving.py::test_pickup_for_someone_else_needs_them_at_the_store`（加强）；另 1 个等价 |

**等价突变。**

- MUT-A 下限缓解组：评分器按有效偏好而不是原始权重给娱乐候选排序。同一内容种类的条目先验相同，按先验乘 `w` 与按先验乘 `f + (1 − f)·w` 排出的顺序一致；
  候选池上限 14 恰为 7 个种类各 2 个，池子从不截断；最终顺序按（−得分，摘要），这一中间排序不到达任何输出。
- MUT-B 输入警告组：配送路线警告把「恰好一条可达路线」放宽为「其中有可达路线」。重复的路线键在加载时已被拒绝（`RuntimeScenario._validate_route_keys`、
  `map_data.validate_map_dataset`），构造不出能区分两者的输入。
- MUT-C 名称类预算组：引擎把免计的名称类拒绝码写进预算耗尽事件。免计的名称类拒绝只出现在开放阶段，此时同刻轮数低于上限，读取它的预算耗尽分支不可达；
  受限阶段里购买、提交与回看在抛出名称类码之前就以 `SAME_MOMENT_ACTION_RESTRICTED` 拒绝。
- MUT-C 可调用索引组：索引按对象身份而不是可写名称计数。只有同一品类里两个条目同名时才有区别，而 8.1 的加载校验已拒绝同一前缀（内容、游戏、持有物）下的重复标签。

规格「阶段 6 突变审查的目标」所列各项都有突变覆盖：睡眠计费的清醒分类、有效偏好的单调性与下限、档界常数、系统层条数与档位名、地点类资格、哈希决胜、轮转判据、
摘录字数门槛、加载拒绝与推进的开始时刻（MUT-A），错误结构键闭集、控制工具的上限校验与 `views`→`subject`、截断回合丢弃与守卫命中即重试（MUT-B），
名称归一唯一才受理、候选不替换、分段匹配的段序与名称类拒绝不计轮（MUT-C）；验收标准另加的按 `prefer` 选定与其决胜也由 MUT-C 覆盖。
52 个初次存活的突变补测后都以原补丁复验杀死。

## strict 端点探针（7.5）

探针脚本 `scripts/probe_deepseek_strict.py` 只读、只报告，绕过本地的 `deepseek_strict_schema` 直接发送；三轮探针都在 2026-09-29 进行，用用户的 DeepSeek 密钥，模型 `deepseek-flash`。
原始报告保存在编排会话的临时目录（不含密钥），不进仓库。

| 探针 | 条件 | 结果 |
| --- | --- | --- |
| 快速探针（`--runs 3`，11:57 UTC） | 当时配置主站；收尾用旧的三组平行数组形状 | 带 `minLength` 的 strict 函数：主站与 `/beta` 都返回 200，`minLength` 不能区分两端；`/beta` 思考模式收尾 3 次全部是非法 JSON（`summary` 写成不带引号的裸字符串，另把 `subject` 填在普通记忆上）；`tool_choice=required`：开启思考两端都返回 400「Thinking mode does not support this tool_choice」，关闭思考两端都返回 200 并调用了工具 |
| 临时核对 | 主站与 `/beta` 各发 | 对象属性没有全部列进 `required` 的 strict Schema：主站 200 并调用工具，`/beta` 400「Required properties must match all properties in the object」；枚举只许 red、blue 却要求 green（关闭思考）：主站 3 次中 1 次越界，`/beta` 0 次；`pattern`：两端都越界；开启思考时模型在简单提示下不调用工具，没有结论 |
| 完整探针（`--runs 50`，14:09 UTC） | 代码 `7d7ed65`，配置 `/beta`；收尾用新的 `items` 形状 | 判据行（属性没有全部列进 `required`）：主站 200、`/beta` 400，主站不执行 strict，`/beta` 在请求时校验 strict Schema；`minLength` 信息行：两端都 200；枚举（关闭思考、`tool_choice=required`、要求 green）：主站与 `/beta` 各 3 次中 1 次越界，`/beta` 不保证输出取值受约束；`/beta` 思考模式收尾 50 次：49 次合法、0 次非法 JSON、1 次形状错误（一条看法缺 `about`）、0 次宿主拒绝，50 次都以工具调用结束；`tool_choice=required`：开启思考两端 400，关闭思考两端 200 并调用工具 |

处理：

- **判据（规则 1）。** 规格要的「文档不支持的关键字」在快速探针中不能区分两端，完整探针改以文档写明的 strict 规则（对象的每个属性都要列进 `required`）为判据，
  `minLength` 留作信息行（`8bc092c`）。枚举一行按新增的检查计越界、非法 JSON、未调用与 HTTP 错误；三项结论只从各自的特定结果得出（MUT-B 补测钉住）。
- **切换（规则 2）。** 探针成立：`DEEPSEEK_BASE_URL` 改为 `https://api.deepseek.com/beta`（`a80ed39`，经 `7d7ed65` 合入），画像抽取随之切换；接口合同「模型身份」的
  `base_url` 行与代码注释按完整探针写明「请求时校验 Schema、不保证取值受约束」（`bd91d41`）。`validate` 核对的模型身份含新端点，冻结在主站的清单不能打开、恢复或重放：
  `tests/integration/test_strict_endpoint.py::test_validate_checks_a_stage_identity_on_the_strict_endpoint`、`tests/integration/test_strict_endpoint.py::test_validate_refuses_the_main_site`、
  `tests/integration/test_strict_endpoint.py::test_resume_refuses_a_parent_run_frozen_on_the_main_site`、`tests/integration/test_runtime.py::test_model_identity_freezes_complete_strict_endpoint_and_request`。
- **不做在线自检（规则 3）。** 没有新增每次运行的自检请求；端点冻结在阶段身份中，由 `validate` 核对。
- **本地校验仍是最终权威（规则 4）。** 完整探针的那次形状错误与 `/beta` 的枚举越界正是本地校验要拦下的；`life_agent.py` 的本地校验与 `strict: true` 声明不变。
- 收尾改为 `items` 形状后，`/beta` 思考模式下的非法 JSON 由快速探针的 3/3 降到完整探针的 0/50（两次样本量不同）；截断重试关闭思考后才能用 `tool_choice=required`，与探针一致（7.2）。

## 代码与规格的出入

以下各项是实现与复核中发现的规格字面与实现的出入，都已按裁决或冻结条款处理并写进现行文档；「处理」列写落点与回归用例。

| 项 | 规格 | 实现与出入 | 处理 |
| --- | --- | --- | --- |
| 方式「购买」都不行时的下一步 | 3.5「收敛」第 2 条（0.5）：`next_step` 写从当前地点出发路线最短的出售门店 | 照字面会指向只能远程下单的门店（去了也买不到），送给不在店里的人时去了也交不到对方手上 | 规格按实现细化（0.7，`10693f8`；FIXUP-4 `d58ca21`）：到店只在交得到收货人手上时才算能服务；送给自己时指向路线最短的能到店买的出售门店，出售门店都只能远程下单时与远程下单的拒绝同一说法，送给别人时为「先送到自己这里，再当面给」。`tests/life/test_purchase_stages.py::test_system_purchase_next_step_names_the_nearest_store_that_sells_it_in_store`、`tests/life/test_giving.py::test_system_purchase_for_someone_it_cannot_reach_says_to_buy_for_oneself_first` |
| strict 探针的判据 | 7.5 规则 1：在 Schema 里故意放一个文档不支持的关键字 | `minLength` 主站与 `/beta` 都受理，不能区分两端 | 判据改为「属性没有全部列进 `required`」，`minLength` 留作信息行（见上节）；`tests/test_probe_deepseek_strict.py::test_report_sends_every_probe_request_and_classifies_the_results` |
| 截断重试开放的工具 | 7.2 规则 2：重试关闭思考并 `tool_choice=required` | 重试若调用记忆查询，它的回合没有 `reasoning_content`，此后同一决策中开启思考的请求会因缺思考的 assistant 被提供商以 400 拒收（续接链中宿主发送前自检亦抛错） | 重试只开放世界请求工具，只读问答只剩 `finish`；被拒的重试回合与截断回合一样不留在历史中；接口合同「回合预算」与各规格的镜像句写明。`tests/integration/test_output_truncation.py::test_a_truncated_turn_is_dropped_and_retried_once_without_thinking`、`tests/integration/test_output_truncation.py::test_readonly_questions_keep_their_parameters_and_a_truncated_answer_is_retried_with_finish_only`、`tests/integration/test_output_truncation.py::test_a_rejected_retry_turn_is_dropped_so_a_continued_decision_keeps_every_thought` |
| 输入警告的码数 | 验收台账「输入警告」：九个警告码各一例 | 警告码闭集为十个（1.3 表八个码，8.1 另加两个） | 规格不改：「九个」按 1.3 的表行数计；十个码各登记一例，`tests/test_dayrun_fidelity_acceptance.py::test_every_input_warning_code_has_one_registered_example` 判定 |
| 生活活动窗口的时刻 | 5.1 设计第 1 条：按当前地点与时刻取候选，地点类不符的条目不进窗口 | 窗口只施加地点类资格；目录 `time_window` 覆盖此刻只进相关度（+2，裁决 S4），不排除条目 | 按裁决 S4 与「软偏好只改序不出局」取此读法，`observe._menu_windows` 的说明改正（FIXUP-3 `e4d4002`）；`tests/life/test_presentation_fidelity.py::test_lifestyle_window_excludes_location_kind_mismatches_with_the_reason_code`、`tests/life/test_presentation_fidelity.py::test_relevance_puts_time_window_goal_family_and_habit_hits_ahead` |
| 名称类闭集的归属 | 3.4：宿主五码、环境六码 | `SUPPLY_SOURCE_MISMATCH` 宿主与环境都有：本轮列出的标签写了另一个已知地点由宿主纠正，按名写的标签与配了不含该门店的渠道的标签由环境拒绝（3.2（b）），环境闭集为七码；合并后的闭集仍是规格的十一码 | 接口合同「活动受理拒绝码」写明；`tests/life/test_name_resolution.py::test_host_and_environment_share_one_name_class_closed_set` |
| `FULFILLMENT_NOT_SUPPORTED` 的免计 | 3.4 把它列入名称类闭集，没有限定在分段匹配的 M2 | 按码判定：凡抛出这个码都不计同刻轮数、不施加脉冲、计入 `name_rejections`，包括指名购买以外的抛出处（购物篮与订单路由） | 按闭集执行，接口合同「活动受理拒绝码」的名称类闭集按码登记 |
| 并列时的决胜 | 3.5「收敛」第 5 条：并列时先比到手价，再比门店标识 | 再比供给标识（同一门店有两条同名供给时）；到手价未知的排在最后 | 接口合同写明；`tests/life/test_purchase_channels.py::test_member_store_ties_break_by_landed_price_then_store_identity`、`tests/life/test_named_purchase.py::test_a_store_whose_price_is_unknown_is_never_the_cheapest` |

规格没有写明、代码按裁决或既有冻结条款实现、文档已按代码写明的细节（不算出入）：

- **S10 与「收敛」。** 裁决 S10 按 3.5「收敛」实现：`start_activity` 增必填可空的请求级 `prefer`；方式「购买」只写在请求里，工具层 `actions[].way` 不列出，
  对本轮列出与按名写的商品目标都受理，所以它不改变 `prompt_sha256`，而 `prefer`（字段说明兼述「购买」）使 `tool_schema_sha256` 重钉；商品多于一处能服务时按 `prefer` 选定，
  不再抛 `SUPPLY_NAME_AMBIGUOUS`（码留在闭集里归类旧运行的拒绝）；到店的 `fastest` 只在人物所在的门店成立，「路线最短」只用于拒绝时的下一步
  （`tests/life/test_purchase_stages.py::test_system_purchase_is_accepted_for_listed_and_named_goods_and_never_listed`、`tests/life/test_named_purchase.py::test_several_servable_matches_follow_prefer_and_never_report_ambiguity`）。
- **购买渠道。** 渠道派生宽松、准入严格：钉定的手机配置核验失败时不派生渠道、只记一条日志警告，创建照常；渠道准入违规在加载时拒绝
  （`tests/life/test_purchase_channels.py::test_a_phone_config_that_fails_verification_derives_no_channels`、`tests/life/test_purchase_channels.py::test_channel_admission_violations_are_rejected_at_load`）。
  六条准入原因里有四条经手机配置派生到达不了，只作防御。选定只看 M3 的能否服务，营业与库存留给结算；两项解析到不同门店时仍以 `ORDER_LOCATION_MISMATCH`
  或 `ORDER_SELLER_MISMATCH` 拒绝，不拆单。
- **`from` 的其他写法。** `from` 可写任何所选地点的显示名：不是门店时报 `SUPPLY_NOT_AT_STORE`「这里没有可买的商品」，「家」「住处」解析为住处；
  给没有收货地的收货人远程下单在 M0 之前以「暂时送不到<名>那里」拒绝，不带逐项明细。
- **相关度的时点与习惯反应。** 相关度的当日目标家族取本秒开始时的当日计划，同一秒各轮沿用同一呈现窗口
  （`tests/life/test_presentation_fidelity.py::test_goal_families_keep_the_plan_at_the_start_of_the_second`）；习惯签名的反应文本在新建运行时从人物卡片取出，
  冻结进运行清单 `persona_habit_responses`，引擎直驱为 null（`tests/integration/test_presentation_habits.py::test_run_creation_freezes_habit_responses_and_ranks_the_hit_entry_first`）。
- **清单、报告与接口。** `LifeEngine.create(..., create_options)` 由运行时传入口味权重、输入警告与习惯反应文本；运行清单 `schema_version` 由 1 升到 2
  （新键 `input_warnings`、`persona_habit_responses`），`quality.json` 由 2 升到 3（新节 `input_warnings`、`truncations`、`timeline_labels`、`contact_reply_guard`、
  `name_rejections`，都只报告）；世界结构版本不升，仍为 8，`channel_id` 不进 `DeliveryState`。
- **截断的计数口径。** `truncations` 计全部 `finish_reason=length` 的回复（任何用途，旧运行也能计），行动决策的截断率以其中 `decision` 的次数除以
  `model_usage.by_purpose` 中 `decision` 的 `provider_responses`（`tests/footprints/test_quality.py::test_truncations_count_length_responses_with_their_output_and_reasoning_tokens`）；
  重试请求关闭思考，不算行动请求，不会是决策的首个请求。
- **两种同刻轮数。** 按 `decision_started` 数出的同刻轮次（`quality.json` 的 `same_moment_round_counts`）可以多于引擎的 `same_moment_rounds`：免计的名称类拒绝之后仍有新一轮决策。
- **睡眠中不读的字段。** 睡眠中跳过的是直接消息的 `read_inbox_ids`；`read_message_ids` 是阅读活动读过的邮件。
- **既有的合同缺口（未改）。** 接口合同「模型身份」没有写 `tool_schema_sha256` 也绑定 `catalog/affordances.toml` 的摘要，早于本特性。

## 文档一致性

- **冻结条款修订与文档同步作业。** 「冻结条款修订」27 行随冻结生效（`f7f9bfd`：【冻】作业 LR1–LR8、RL1–RL4、TE1–TE7、PP1–PP5、DP1、MS1、IM1、AI1、DC1、AS1、DR1、RV1、
  EXP1、VF1、AR1 与 IX1 的冻结部分）；【同】作业 C1–C20、SE1–SE8、RE1–RE6、EX1、RS1、DL1、EV1、CF1、IO1 随各批代码落地，并由实现后文档按 `34a41ee` 与 `e377669`
  的代码逐节核对，改动写在各文档的版本表；DD1 随每次设计章节或接口合同改动重新汇编；【后】作业见「实现后文档」。3.5「收敛」第 2 条的细化（0.7）
  同步了人物 Prompt、时间与能量、一日行为三份规格的镜像句，接口合同、设计章节与资源手册已随 FIXUP-4 改写。
- **现行条款中的过时表述。** 一次性脚本（只读，不进仓库）扫描现行文档（除 `docs/dev/research/`、提案、本规格与各版本表）中被本规格取代的写法：
  「暂时送不到这里」、「饿了趁早」「趁早吃」、旧收尾形状 `task[]`、`summary[]`、`memories[][]`、「17 条」、主站端点、`_rotation_offset` 与「地图结构 `1`」。
  命中的行都是「不再」「原为」「旧运行」的说明、被取代条款的历史行或对被修订条款的注记；只有实现状态文档「能量不阻断」一行仍把「饿了趁早弄吃的」
  写成现行系统层规则，在 IS1 提交改为第 15 条的写法。该文档「地图与品类组合的历史结论」记的地图结构 `1` 是那次组合的固定身份，保留原样，IS1 一节注明现行结构为 2。
- **测试名。** 现行文档（除 `docs/dev/research/`）以 `tests/<文件>::<名称>` 写出的测试节点逐个解析到 `tests/`：本特性改动的行与本记录引用的节点都解析到存在、
  会被收集且未被跳过的测试。另有 12 个旧测试名早于本特性、位于其他特性的历史行（规格覆盖追溯的历史映射 9 个，人物规格与 life-runtime 规格的旧验收说明 3 个），
  不在本特性的改动范围内，保留原样。
- **锚点核对。** 完整性守卫的锚点判定按 GitHub 标题锚点规则解析 `docs/` 下全部带锚点的链接（本规格各章、「验收台账」「冻结条款修订」等被大量引用的锚点在内）；
  在本记录所在的提交上，含本记录在内的 438 个带锚点的链接都指向现有标题，0 处失效。
- **文档门禁。** 在本记录所在的提交上，`scripts/documentation_gate.py` 扫描 131 份文档（含本记录），0 处断链；`scripts/build_design_doc.py --check` 通过；
  风格、文档门禁、设计说明书汇编、三份完整性守卫与覆盖率门禁的测试共 89 项通过（依次 1、6、11、28、25、8、10 项）。

## 实现后文档（阶段 8）

| 作业 | 落地 |
| --- | --- |
| MN1–MN5 | 用户手册五页按实现写明（`f68b6fb`，续 `cc66419`）；MN6 按规格无需改动 |
| AN1 | 规格覆盖追溯新增「一日运行保真增量映射」：台账逐行到测试、strict 探针结论与被取代的 20 行历史映射（`f68b6fb`，续 `cc66419`）；本记录所在提交补「更简单的购买请求」一行与登记表、守卫一句，并链接本记录 |
| IX1 | 冻结部分随 `f7f9bfd`；【后】部分（十二行修订改为已实现、规格一行改为条款已全部实现、阅读路径）随 `f68b6fb` 与 `cc66419`；验收记录入口与阅读路径在本记录所在的分支登记 |
| 冻结时标记 | 各规格状态格、文件头、`docs/VERIFICATION.md` 测试矩阵与文档地图中冻结时写下的临时标记随 `f68b6fb` 与 `cc66419` 按实现改回；完整性守卫 `tests/test_dayrun_fidelity_acceptance.py::test_docs_carry_no_pending_markers_of_this_feature` 判定不再留有 |
| 覆盖率基线 | `docs/dev/coverage-baseline.md` 的「基线记录」改为 `1478436` 的门禁结果：覆盖率报告已随 `b7f85f9` 改写，基线记录随之改写（规格没有单列这一作业） |
| 验收标准 | `docs/VERIFICATION.md`「一日运行保真特性门禁」补验收登记表与完整性守卫，并链接本记录 |
| IS1 | `docs/dev/implementation-status.md` 由本分支最后一个提交更新，不在本记录的提交内 |

## 真实复验（阶段 7，只报告）

未执行。规格「实施计划」阶段 7 用同一份模型身份（`deepseek-flash`，`https://api.deepseek.com/beta`）和数据集仓库修复后的输入复跑白领（WC）与商务（BIZ）全天；
它只报告、不作门禁，需要用户的模型账号，也要等数据集仓库按数据集方案改完：两次运行冻结的数据集输入在现行代码下加载即被拒
（地图文件结构为 1，WC 另有可到达地点的类别不在环境层的地点类表内；`tests/life/test_map_dataset.py::test_old_map_structure_version_is_rejected`、
`tests/life/test_load_checks.py::test_location_kind_outside_the_place_types_is_rejected_at_load`）。
`tests/dayrun_fidelity_acceptance.py` 为「复跑指标（只报告）」登记的是复跑要读的质量报告各节与 `purchases.jsonl` 的测试。

复跑后逐项与 `4a5b0e6` 两次运行的数值对照（台账「复跑指标（只报告）」一行）：

| 复跑指标 | `4a5b0e6`（WC / BIZ） | 怎样读 |
| --- | --- | --- |
| 协议错误按收尾形状类、名称类分别计数并显著下降 | 63/181、390/606（分母含零点计划与评审调用） | `quality.json`：`name_rejections` 分列宿主名称类纠错（逐码）、其余协议错误（逐码，收尾形状类在 `ARGUMENTS_INVALID`、`ARGUMENTS_NOT_JSON` 之中）与环境名称类拒绝（逐码与免计次数）；`memory_references` 的 `memory_protocol_error` 计提到记忆与看法字段的协议错误 |
| 截断下降 | 3/177、26/599（分母只计行动决策） | `quality.json` 的 `truncations`：次数、按用途、浪费的输出 token 与其中的思考 token；截断率以 `decision` 的次数除以 `model_usage.by_purpose` 中 `decision` 的 `provider_responses` |
| 联系人回复越界 0 | 5/6、1 | `quality.json` 的 `contact_reply_guard`：命中、重试与兜底 |
| 规格消费全部可成交 | WC 三样能送到家的菜全天没买成；BIZ 的咖啡与晚餐配送路线未知，全天送不到 | 导出的 `purchases.jsonl`：浏览、按方式同义表计入的尝试下单与成功购买分行 |
| 醒来时社交需要 ≈ 0 | 0.30 / 0.26 | 醒来前后的 `body_settlement`（社交账） |
| 晚间「很想放松」看内容后能回落 | 连看 6 部短剧仍「很想放松」（WC） | 观看区间的 `body_settlement` 与体感分级 |
| 首次「困」距惯常就寝约 −0:45 | −2:25 / −3:04 | 困意分级跨档的时刻与场景作息覆盖后的就寝窗口 |
| 午饭在规格窗口 ±30 分钟内 | 10:20 / 15:19 | 进食活动的开始时刻与场景的午餐窗口 |
| 就寝在惯常 ±45 分钟且在家 | 22:45 / 21:13（餐馆） | 睡眠活动的开始时刻与地点，评估报告项 `sleep_schedule` |

另外 `quality.json` 新增的 `input_warnings`（清单冻结的输入警告，按数据集修复逐条核对）、`timeline_labels`（渲染后时间线里的英文种类名与内部标识形态，规格 7.4 要求为零）
与 `day_plan_metrics` 的非例行推进比例随复跑一并报告。复跑结果补入本记录本节。

## 范围外

- **数据集仓库。** 规格第九章「数据集仓库」一行：`prepare_labelkit_input.py` 改用同一归一规则裁掉记忆与看法、`simulate_phone.py` 优先读 `channel_id`，
  在数据集仓库单独提交，本次未做；数据集方案的路线、供给、命名、题材词表、活动池、地点类别与地图结构 2 同属数据集仓库。
- **其余范围外项**：规格「范围外登记」的工作材料正文进提示（G21，裁决 D13）、社交账的在场折减与醒来减半、饥饿随惯常饭点起伏、S/C 常数按人物标定、
  输出上限提高到 16000、`start_activity` 改形（3.5 只加请求级 `prefer`），以及数据集方案第五章中本规格不做的事项，都不在本次范围。
