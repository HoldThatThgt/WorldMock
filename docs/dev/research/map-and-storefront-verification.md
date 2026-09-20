# 地图与门店供给验收记录

> 对应合同：[地图与门店供给规格](../SPEC-map-and-storefront.md)。
> 实现位置：独立工作树 `codex/map-and-storefront`。原始开发目录由其他会话使用，未覆盖其并发修改。
> 状态：功能和定向行为测试已实现，完整覆盖率门禁及独立变异审查正在执行；不得把本状态解释为最终验收通过。

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

定向测试已覆盖生产行为，最终完整门禁执行结果和变异审查收据在完成后按实际输出记录。
目录 `scripts/build_catalog.py --check` 已确认资源、商品、清单和墓碑文件均逐字节一致；文档门已确认五十一份文档零断链。

历史运行锚定用例 `tests/resources/test_equivalence_anchor.py` 依赖未随仓库提交的 `runs/deepseek-multi-47931e06/footprints.sqlite`，隔离工作树没有该历史产物。
这些历史用例的跳过与本特性新增用例分开计数；本特性没有跳过、预期失败或延期实现项。
未发起新的付费模型长跑；确定性工具驱动的真实引擎流程不能证明长期人物选择质量或自然度。
