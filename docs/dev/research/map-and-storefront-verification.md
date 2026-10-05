> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/map-and-storefront-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 地图与门店供给验收记录

> 对应合同：[地图与门店供给规格](../SPEC-map-and-storefront.md)。
> 实现位置：独立工作树 `codex/map-and-storefront`。原始开发目录由其他会话使用，未覆盖其并发修改。
> 并发范围：实现基于当时快照 `83698bdf339ec3dd0265844391716d398a02ce14`；其他会话的后续提交不属于本次验收身份。
> 状态：本特性全部范围已实现并通过离线验收，无延期实现项。项目级真实长跑与自然度评价另行判断。
> 验收提交：`017d49c4a246c0c524e541571220e1a07b16ce87`，日期：2026-09-20。

## 行为与测试对应

每个测试文件中的命名用例直接调用生产入口。真实引擎用例使用正式冻结地图、真实 SQLite、事件时钟及共同检查点；模型调用由确定性工具请求驱动。

| 规格要求 | 行为证据 | 主要测试 |
| --- | --- | --- |
| 地图唯一来源 | 场景引用固定地图；正确哈希下仍验证闭集、关系和选区；文件或版本篡改拒绝 | [地图准入](../../../tests/life/test_map_dataset.py)、[场景合同](../../../tests/life/test_scenario.py) |
| 地点分类分离 | 缺活动环境拒绝；菜单、业态与活动环境各自生效；实际暂停后换地点恢复被拒且进度保留 | [地图准入](../../../tests/life/test_map_dataset.py)、[到达与恢复](../../../tests/life/test_storefront_arrival.py) |
| 多门店独立供给 | 同商品独立报价、库存、卖方；只改变选中门店；已交付物品按商品合并 | [整单交易](../../../tests/life/test_storefront_commerce.py)、[目录准入](../../../tests/catalog/test_storefront_admission.py) |
| 时态声明 | 半开端点、未知间隙、日期来源、重叠、跨午夜、例外及地图时区；报价事件替换整份声明 | [营业与报价](../../../tests/life/test_opening_hours.py)、[供给事件](../../../tests/life/test_storefront_schedule.py) |
| 地点发现 | 需求与业态筛选；道路时间边界；闭店、不可达、未知和超范围仍明确解释；实际提交后回灌 | [发现与呈现](../../../tests/life/test_storefront_discovery.py)、[工具接线](../../../tests/integration/test_storefront_tools.py) |
| 人物选择边界 | 主动请求比较事实；宿主身份不进入模型参数；菜单、动态 Schema、本地校验和环境拒绝一致 | [工具接线](../../../tests/integration/test_storefront_tools.py) |
| 供给候选身份 | 同商品门店不互相覆盖；逐供给可见期；实际窗口之外无凭据；游标跨世界版本可继续；比较变化无损还原 | [发现与呈现](../../../tests/life/test_storefront_discovery.py)、[动机可行性](../../../tests/life/test_storefront_motivation.py) |
| 时间重验 | 实际旅行途中发生闭店、售罄或调价后购买失败，位置、行程和费用保留；到达资格不足有公开结果及即时决策机会 | [到达与恢复](../../../tests/life/test_storefront_arrival.py) |
| 整单原子结算 | 跨地点、跨卖方、重复供给、旧报价、缺库存和缺余额全部拒绝；断言账户、库存、订单、事件无部分变化 | [整单交易](../../../tests/life/test_storefront_commerce.py) |
| 自提与配送 | 自提不检查配送路；远程自提拒绝；配送范围和实际道路重验；整单最迟备货后一次配送；交付幂等 | [整单交易](../../../tests/life/test_storefront_commerce.py)、[恢复全流程](../../../tests/life/test_storefront_recovery.py) |
| 世界恢复 | 调价、补货、营业例外、逐供给可见凭据与呈现记录往返；篡改报价时间线、来源、身份及资格拒绝；分支不写父目录 | [恢复全流程](../../../tests/life/test_storefront_recovery.py)、[状态拒绝](../../../tests/life/test_storefront_state_validation.py) |
| 来源与交付 | 观察与订单保留实际来源；清单固定地图版本、许可通知和文件身份；已付款历史报价不被新价覆盖 | [恢复全流程](../../../tests/life/test_storefront_recovery.py)、[交付流水线](../../../tests/delivery/test_host_delivery_coverage.py) |
| 最小完整流程 | 两个独立分支运行发现、比较、真实出行自提或远程配送、消费、关库重开及共同检查点分支重放 | [恢复全流程](../../../tests/life/test_storefront_recovery.py) |
| 工程验证 | 定向与完整测试、覆盖率、风格、目录重建、文档引用及源码语义变异 | 下方工程收据与独立审查记录 |

## 冻结数据核验

正式地图包含十一个地点、二百二十条有向路线、一百六十一条门店供给；一百五十九个商品定义保持独立。
原有一百零四份错误复用驾车结果的步行响应已重新取得真实 `routed-foot` 响应并核验步骤方式，原始响应、请求来源和内容哈希已冻结。
其余六条步行路线的方式依据仍来自原有步行实例，全部一百一十条步行路线与一百一十条驾车路线进入地图准入验证。

来源时间为二〇二六年九月，模拟时间为二〇二六年一月；营业、供给、报价、库存及访问条件明确标记为场景假设，不宣称历史门店事实。
可复现范围是消费已冻结响应，公开路由响应未提供完整上游路网快照，不能声称重新请求必然返回同一结果。
业界依据与限制见[可行性核验](map-and-storefront-feasibility.md)，设计和消费者修改范围见[文件影响矩阵](map-and-storefront-document-impact.md)。

## 工程收据

首次完整实现提交为 `2bce358a09046b84841bc5a2da68d82a66b79dbc`。
独立审查后只补强三个测试文件，形成上述最终验收提交；两次提交的 `src/custom/catalog/corpora/scenarios/scripts` 完全一致。
随后仅更新验收、审查证据和文档状态，不改变被测生产代码、数据或测试。
按相对路径排序，逐一拼接 `src` 与 `custom` 的 Python 文件路径、零字节分隔符、内容及零字节分隔符所得 SHA-256 为
`8a9e412f38498a2e9551fcaeb55f2e3028eb374af26e49d0ea9e542bf07475c6`。

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| `uv run python scripts/coverage_gate.py` | **3240 passed、4 skipped**，退出码 0，门禁通过 | [完整输出](map-and-storefront-bob/full-suite.log) |
| 分支覆盖与公共接口 | **98.49%**，6341/6438 分支；未覆盖公共接口 **0** | [机器报告](map-and-storefront-bob/coverage.json) |
| 测试补强与生产风格门 | 地图、发现、恢复及风格共 **120 passed**；风格门也包含于完整套件 | [定向输出](map-and-storefront-bob/regression-tests.log) |
| 目录重建 | `scripts/build_catalog.py --check` 的四份生成物逐字节一致 | [构建收据](map-and-storefront-bob/catalog-check.log) |
| 文档引用 | `scripts/documentation_gate.py` 扫描 **57** 份文档，零断链 | [文档收据](map-and-storefront-bob/documentation-check.log) |
| 正式十二周场景素材静态可达性 | `scripts/check_reachability.py --scenario scenarios/white-collar-12-weeks.json`：**7384** 条全部可达，**142** 个分区 | [可达性收据](map-and-storefront-bob/reachability.log) |

覆盖率报告绑定的源码身份为最终验收提交，快照标识为 `35468ba3-5b0f-48bf-8946-d46923fd9b7a`，
覆盖数据摘要为 `d9b3b0c437f0570bd4ac9994bdb7f0d63280c21655903819968739cc942ab72c`。
这些覆盖率数字不替代上方逐项行为断言，也不代表全部可能输入都已验证。

## 独立变异审查与补测闭合

三个代理交叉审查，全部变异在指定提交的独立临时工作树完成。先固定现有测试命令并取得通过基线，
每次仅改变一个生产语义，再原样运行测试并完整恢复。实际导入路径、补丁、失败原因和零差异恢复记录均随报告保存。
报告阅读副本指向各组原始证据包；包内文件与审查输出逐字节比对，摘要见 [证据包清单](map-and-storefront-bob/archive-manifest.json)。

| 审查范围 | 基线实例 | 实际变异结果 | 报告 |
| --- | --- | --- | --- |
| 门店交易、营业、履约与持有量 | 9 passed | 11 killed，无 survived/invalid | [交易报告](map-and-storefront-bob/commerce/REPORT.md) |
| 发现、呈现、菜单与游标 | 17 passed | 9 killed、1 invalid，无 survived | [发现报告](map-and-storefront-bob/discovery/bob-report.md) |
| 地图、到达、活动环境与恢复 | 35 passed | 10 killed、2 survived，无 invalid | [地图与恢复报告](map-and-storefront-bob/map-recovery/report.md) |
| 地图必填字段与父目录保护复验 | 8 passed | 两项原 survived 均 killed，缺口关闭 | [缺口复验](map-and-storefront-bob/map-recovery-recheck/report.md) |
| 游标起点与世界版本复验 | 2 passed | 2 killed，无 survived/invalid | [游标复验](map-and-storefront-bob/discovery-recheck/bob-report.md) |

首次漏检的“缺失活动环境偷偷补默认值”由正确文件哈希下的真实地图准入测试捕获。
首次漏检的“恢复时给父目录新增文件”由自提和配送两条真实流程的全部路径与文件 SHA-256 比较捕获，
同时检查恢复后和子运行继续后的父目录；没有排除任何父目录文件或文件夹。
首次游标试验因夹具缺世界版本而 invalid，保留原分类；真实引擎及补完整的窄夹具随后分别获得直接语义检出证据。
起点变化的测试先证明新起点可以正常查询，再检查旧游标拒绝，排除了路线缺失导致的无关失败。

营业端点在交易组与地图组重复运行一次，后者由未编写该模块和商务测试的代理独立复验。
不把重复试验当作额外规范覆盖，不改写初次 survived 或 invalid 记录，也不报告整份规格的变异覆盖百分比。
最终不存在未关闭的已发现测试缺口；五个本任务审查临时工作树均已还原、删除并注销，清理时调用方提交与状态均被核验。

## 验收边界

历史运行锚定用例 `tests/resources/test_equivalence_anchor.py` 依赖未随仓库提交的 `runs/deepseek-multi-47931e06/footprints.sqlite`，隔离工作树没有该历史产物。
这些历史用例的跳过与本特性新增用例分开计数；本特性没有跳过、预期失败或延期实现项。
未发起新的付费模型长跑；确定性工具驱动的真实引擎流程不能证明长期人物选择质量或自然度。
