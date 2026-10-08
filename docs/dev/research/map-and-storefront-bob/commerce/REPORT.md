> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/map-and-storefront-bob/commerce/REPORT.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

> 本页为审查报告阅读副本；导航链接指向原始证据包。包内保留未改动的原始报告、日志、补丁和复原记录。

status: **COMPLETE**

本轮严格针对下表列出的九组要求，执行十一个保持可运行的单一语义变异。**killed 11，survived 0，invalid 0，inconclusive 0；implementation missing 0，implementation diverged 0。** 未评估整个特性的全部规范，不报告变异分数或规格覆盖百分比。

## 基线与隔离

- Caller：`/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock`
- 基线提交：`2bce358a09046b84841bc5a2da68d82a66b79dbc`
- 唯一行为规范：`docs/dev/SPEC-map-and-storefront.md`，以下行号均属于上述提交。
- Detached 临时树：`/tmp/worldmock-map-bob-commerce-tree.Cf8AeE`，通过 `mktemp -d` 取得显式路径，再以 `git worktree add --detach` 建立；现已移除。
- 用户明确授权临时生产语义变异和本地功能集成验证。本轮没有提交、stash、远程请求、付费调用或真实生产动作；没有编辑调用方、测试、文档、夹具、锁文件或生成文件。
- 调用方前后 `git status --porcelain=v1 --untracked-files=all` 均为空，HEAD 不变；证据为 [preflight.json（包内 preflight.json）](evidence.zip) 和 [cleanup.json（包内 cleanup.json）](evidence.zip)。

基线及每次变异先导入受影响模块并断言路径属于临时树。实际导入路径：

```json
{
  "worldmock.life.basket": "/private/tmp/worldmock-map-bob-commerce-tree.Cf8AeE/src/worldmock/life/basket.py",
  "worldmock.life.commerce": "/private/tmp/worldmock-map-bob-commerce-tree.Cf8AeE/src/worldmock/life/commerce.py",
  "worldmock.life.opening_hours": "/private/tmp/worldmock-map-bob-commerce-tree.Cf8AeE/src/worldmock/life/opening_hours.py"
}
```

所有命令的固定执行环境：

```sh
cd /tmp/worldmock-map-bob-commerce-tree.Cf8AeE
export PYTHONDONTWRITEBYTECODE=1
export LITELLM_LOCAL_MODEL_COST_MAP=True
export AGENTSOCIETY_LLM_API_KEY=test-only
export PYTHONPATH=/tmp/worldmock-map-bob-commerce-tree.Cf8AeE/src:/tmp/worldmock-map-bob-commerce-tree.Cf8AeE
```

使用调用方 `.venv/bin/python` 的绝对路径，未执行 `uv run`、安装依赖或改写 editable 路径。已安装的固定版本依赖沿用原环境；生产模块全部来自临时树。

## 规范映射与实现证据

| 要求 | 规范原文与行锚 | 当前生产路径 | 本轮范围 |
| --- | --- | --- | --- |
| 所选门店库存与卖方 | 第 90 行：“只扣所选供给库存、只给其卖方记账” | `basket.py:59`、`:69`、`:75` | 两个独立变异分别检查库存身份和收款账户 |
| 同账户跨地点整单拒绝 | 第 88 行：“同账户跨地点也必须拒绝” | `basket.py:117`、`:119` | 保留卖方检查，单独删除地点检查 |
| 重复项原子拒绝 | 第 88、90 行：“明确拒绝重复项”；任意项失败无购买副作用 | `basket.py:97`，检查先于结算 | 现有测试要求准确错误且输入快照不变 |
| 购买重验报价版本 | 第 56、90 行：“购买时重验……报价版本” | `basket.py:105`、`:108` | 第二项旧版本不得绕过检查 |
| 营业结束端点 | 第 52、54、56 行：半开区间、分钟级开放、购买时重验营业 | `opening_hours.py:73`、`basket.py:125` | 确切关店分钟必须拒绝购买 |
| 供给资格 | 第 56 行：“购买时重验……供给资格” | `basket.py:112` | 缺资格拒绝；基线也确认有资格可买 |
| 整单备货与交通时刻 | 第 92 行：“分时路线时长和供给处理时长”；“最迟备货时刻一次配送” | `basket.py:139`、`:150`、`:152` | 分别破坏最大备货和实际出发时交通 |
| 交付幂等 | 第 92、94 行：“重复到期事件不重复增加库存” | `commerce.py:9`、`scheduling/effects.py:65` | 通过实际调度效果重放，要求世界快照与库存不变 |
| 持有后才能消费 | 第 25、92 行：物品交付后位于目的地；付款不意味着持有 | `basket.py:264`、`commerce.py:29` | 真实引擎当前地点无持有量必须拒绝消费；另作待交付场景只读探针 |

这些范围内均找到真实生产行为，没有用接口存在代替行为判断，也没有发现缺实现或可见的规范背离。

独立性边界：`basket.py`、`commerce.py`、调度效果和本轮主要商务测试并非本代理主实现。`opening_hours.py` 曾在本代理的共享基础所有权内；其 closing_boundary 变异及杀伤证据如实保留，但该项不能称为完全独立作者交叉审查。父代理已另行安排独立复核；此报告不提前代替或引用其结果。

## 预声明命令与基线

[declared-commands.md（包内 declared-commands.md）](evidence.zip) 在任何基线或变异执行前写入，逐项保存测试命令和预期违规含义。九条不同的最窄现有 pytest 命令逐一运行，全部 **1 passed**，无跳过；重复使用同一命令的变异共享该命令的 green baseline。详见 [baseline.json（包内 baseline.json）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_single_supply_changes_only_selected_shop_and_preserves_sources
```

结果：exit 0，1 passed；[baseline-selected_stock.log（包内 baseline-selected_stock.log）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider 'tests/life/test_storefront_commerce.py::test_whole_order_rejections_are_atomic[location-ORDER_LOCATION_MISMATCH]'
```

结果：exit 0，1 passed；[baseline-same_account_cross_location.log（包内 baseline-same_account_cross_location.log）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider 'tests/life/test_storefront_commerce.py::test_whole_order_rejections_are_atomic[duplicate-ORDER_DUPLICATE_SUPPLY]'
```

结果：exit 0，1 passed；[baseline-duplicate_supply.log（包内 baseline-duplicate_supply.log）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider 'tests/life/test_storefront_commerce.py::test_whole_order_rejections_are_atomic[price-PRICE_VERSION_MISMATCH]'
```

结果：exit 0，1 passed；[baseline-obsolete_quote.log（包内 baseline-obsolete_quote.log）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_purchase_uses_minute_boundary_and_world_opening_override
```

结果：exit 0，1 passed；[baseline-closing_boundary.log（包内 baseline-closing_boundary.log）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider 'tests/life/test_storefront_commerce.py::test_purchase_requires_declared_supply_and_location_qualification[supply-SUPPLY_QUALIFICATION_REQUIRED]'
```

结果：exit 0，1 passed；[baseline-supply_qualification.log（包内 baseline-supply_qualification.log）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_whole_basket_uses_maximum_preparation_and_departure_traffic_once
```

结果：exit 0，1 passed；[baseline-maximum_preparation.log（包内 baseline-maximum_preparation.log）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_delivery_is_due_atomic_and_idempotent_with_truthful_evidence
```

结果：exit 0，1 passed；[baseline-delivery_idempotence.log（包内 baseline-delivery_idempotence.log）](evidence.zip)。

```sh
/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_commerce.py::test_consume_rejects_inventory_held_at_another_location
```

结果：exit 0，1 passed；[baseline-unheld_consumption.log（包内 baseline-unheld_consumption.log）](evidence.zip)。

## 变异台账

每次只修改一处生产语义；先验证源码可编译、模块可导入，再运行原样预声明命令。测试失败后立即恢复原文件字节，并证明 `git diff --exit-code` 为 0、完整 porcelain status 为空，才开始下一次变异。没有把语法、导入或启动失败计为 killed。

| 变异 | 规范行锚 | 生产位置与变更 | 原样测试命令 | 结果与因果证据 |
| --- | --- | --- | --- | --- |
| `selected_stock` | 90 | `src/worldmock/life/basket.py:75`；`stock[order.supply_id] -= order.quantity` → `stock[semantics.scenario.supplies[0].supply_id] -= order.quantity` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_single_supply_changes_only_selected_shop_and_preserves_sources` | **killed**，pytest exit 1；所选供给应从 12 减为 10，实际仍为 12；扣错门店库存被直接发现。 断言：`tests/life/test_storefront_commerce.py:81`。[日志（包内 mutant-selected_stock.log）](evidence.zip)、[补丁（包内 selected_stock.diff）](evidence.zip)、[逐项复原（包内 selected_stock.json）](evidence.zip)。 |
| `selected_seller` | 90 | `src/worldmock/life/basket.py:69`；`rows[0].supply.seller_account_id, total, "purchase:order"` → `semantics.scenario.supplies[0].seller_account_id, total, "purchase:order"` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_single_supply_changes_only_selected_shop_and_preserves_sources` | **killed**，pytest exit 1；所选卖方应增加 1760 分，实际仍为 0；资金被记给错误卖方。 断言：`tests/life/test_storefront_commerce.py:84`。[日志（包内 mutant-selected_seller.log）](evidence.zip)、[补丁（包内 selected_seller.diff）](evidence.zip)、[逐项复原（包内 selected_seller.json）](evidence.zip)。 |
| `same_account_cross_location` | 88 | `src/worldmock/life/basket.py:119`；`if len({row.supply.location_id for row in rows}) != 1:` → `if False:` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider 'tests/life/test_storefront_commerce.py::test_whole_order_rejections_are_atomic[location-ORDER_LOCATION_MISMATCH]'` | **killed**，pytest exit 1；同一卖方账户、不同地点的整单未抛出 ORDER_LOCATION_MISMATCH。 断言：`tests/life/test_storefront_commerce.py:69`。[日志（包内 mutant-same_account_cross_location.log）](evidence.zip)、[补丁（包内 same_account_cross_location.diff）](evidence.zip)、[逐项复原（包内 same_account_cross_location.json）](evidence.zip)。 |
| `duplicate_supply` | 88-90 | `src/worldmock/life/basket.py:97`；`if len({item.supply_id for item in request.items}) != len(request.items):` → `if False:` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider 'tests/life/test_storefront_commerce.py::test_whole_order_rejections_are_atomic[duplicate-ORDER_DUPLICATE_SUPPLY]'` | **killed**，pytest exit 1；重复供给项未抛出 ORDER_DUPLICATE_SUPPLY，测试发现错误受理。 断言：`tests/life/test_storefront_commerce.py:69`。[日志（包内 mutant-duplicate_supply.log）](evidence.zip)、[补丁（包内 duplicate_supply.diff）](evidence.zip)、[逐项复原（包内 duplicate_supply.json）](evidence.zip)。 |
| `obsolete_quote` | 56,90 | `src/worldmock/life/basket.py:108`；`if item.price_version != quote.price_version:` → `if False:` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider 'tests/life/test_storefront_commerce.py::test_whole_order_rejections_are_atomic[price-PRICE_VERSION_MISMATCH]'` | **killed**，pytest exit 1；第二项旧版本 obsolete 被接受，未抛出 PRICE_VERSION_MISMATCH。 断言：`tests/life/test_storefront_commerce.py:69`。[日志（包内 mutant-obsolete_quote.log）](evidence.zip)、[补丁（包内 obsolete_quote.diff）](evidence.zip)、[逐项复原（包内 obsolete_quote.json）](evidence.zip)。 |
| `closing_boundary` | 52,54,56 | `src/worldmock/life/opening_hours.py:76`；`< (midnight + timedelta(minutes=item.closes)).timestamp() for item in intervals)` → `<= (midnight + timedelta(minutes=item.closes)).timestamp() for item in intervals)` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_purchase_uses_minute_boundary_and_world_opening_override` | **killed**，pytest exit 1；12:01 的明确关店端点仍接受购买，未抛出 MERCHANT_CLOSED。 断言：`tests/life/test_storefront_commerce.py:266`。[日志（包内 mutant-closing_boundary.log）](evidence.zip)、[补丁（包内 closing_boundary.diff）](evidence.zip)、[逐项复原（包内 closing_boundary.json）](evidence.zip)。 |
| `supply_qualification` | 56 | `src/worldmock/life/basket.py:112`；`if not set(supply.required_qualifications) <= set(agent.qualifications):` → `if False:` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider 'tests/life/test_storefront_commerce.py::test_purchase_requires_declared_supply_and_location_qualification[supply-SUPPLY_QUALIFICATION_REQUIRED]'` | **killed**，pytest exit 1；缺少 member 资格仍接受购买，未抛出 SUPPLY_QUALIFICATION_REQUIRED。 断言：`tests/life/test_storefront_commerce.py:242`。[日志（包内 mutant-supply_qualification.log）](evidence.zip)、[补丁（包内 supply_qualification.diff）](evidence.zip)、[逐项复原（包内 supply_qualification.json）](evidence.zip)。 |
| `maximum_preparation` | 92 | `src/worldmock/life/basket.py:150`；`state.current_time + max(row.supply.delivery_delay_seconds for row in rows)` → `state.current_time + min(row.supply.delivery_delay_seconds for row in rows)` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_whole_basket_uses_maximum_preparation_and_departure_traffic_once` | **killed**，pytest exit 1；整单预计到达变为 1767571541，期望为 1767571781，提前 240 秒。 断言：`tests/life/test_storefront_commerce.py:110`。[日志（包内 mutant-maximum_preparation.log）](evidence.zip)、[补丁（包内 maximum_preparation.diff）](evidence.zip)、[逐项复原（包内 maximum_preparation.json）](evidence.zip)。 |
| `departure_traffic` | 92 | `src/worldmock/life/basket.py:152`；`semantics.travel_planned_seconds("driving", route.duration_seconds, departure)` → `semantics.travel_planned_seconds("driving", route.duration_seconds, state.current_time)` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_whole_basket_uses_maximum_preparation_and_departure_traffic_once` | **killed**，pytest exit 1；用下单时交通计算得到 1767571707，实际出发时应为 1767571781，差 74 秒。 断言：`tests/life/test_storefront_commerce.py:110`。[日志（包内 mutant-departure_traffic.log）](evidence.zip)、[补丁（包内 departure_traffic.diff）](evidence.zip)、[逐项复原（包内 departure_traffic.json）](evidence.zip)。 |
| `delivery_idempotence` | 92,94 | `src/worldmock/life/commerce.py:14`；`if order.status != "pending":` → `if order.status == "cancelled":` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_storefront_commerce.py::test_delivery_is_due_atomic_and_idempotent_with_truthful_evidence` | **killed**，pytest exit 1；首次交付成功后重放改变人物库存，整个世界快照相等断言失败。 断言：`tests/life/test_storefront_commerce.py:140`。[日志（包内 mutant-delivery_idempotence.log）](evidence.zip)、[补丁（包内 delivery_idempotence.diff）](evidence.zip)、[逐项复原（包内 delivery_idempotence.json）](evidence.zip)。 |
| `unheld_consumption` | 25,92 | `src/worldmock/life/basket.py:264`；`if held_quantity(agent, product.product_id) + 1e-9 < item.quantity:` → `if False:` | `/Users/atishoo/.codex/worktrees/map-and-storefront/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider tests/life/test_commerce.py::test_consume_rejects_inventory_held_at_another_location` | **killed**，pytest exit 1；实际引擎在当前位置无商品时启动消费，status 从预期 rejected 变为 in_progress。 断言：`tests/life/test_commerce.py:206`。[日志（包内 mutant-unheld_consumption.log）](evidence.zip)、[补丁（包内 unheld_consumption.diff）](evidence.zip)、[逐项复原（包内 unheld_consumption.json）](evidence.zip)。 |

所有十一个测试失败均直接暴露所设计的业务错误；没有 survived，因此本轮没有 surviving-mutant 测试判断缺口。这个结论仅针对已声明并实际执行的变异。

## 附加只读行为确认

本轮消费变异的既有 oracle 是“商品在另一地点，当前位置无持有量”的真实 LifeEngine 测试。为区分该测试与“尚有待交付订单”的具体情形，恢复全部生产源码后，另执行 [pending_consumption_probe.py（包内 pending_consumption_probe.py）](evidence.zip)：实际发现 → 购买形成 pending 配送 → 预先声明的邮件事件唤醒下一决策 → 到货前尝试消费，得到 `rejected / PRODUCT_NOT_HELD`，订单仍 pending、人物库存仍为零。[成功日志（包内 pending-consumption-probe.log）](evidence.zip)、[命令与结果（包内 pending-consumption-probe.json）](evidence.zip)。

该探针没有修改仓库测试，不用于追认或替换变异的预声明 oracle，也不计入基线 pytest 数量。第一次补充探针把“下一环境边界”误当成“到货前决策”，实际直接推进到了配送时刻，导致测试搭建断言失败；为明确时间条件，改为在场景中预先声明到货前邮件事件后通过。原失败保存在 [setup failure log（包内 pending-consumption-probe-setup-failure.log）](evidence.zip)，未算作产品问题或 killed。启动探针的初次辅助驱动导入也因 CLI 参数依赖失败，此后改为直接绝对解释器调用；均与正式九条基线及十一个变异无关。

## 清理与未验证边界

- 每个变异的 JSON 均记录恢复后 HEAD 为基线提交、`diff_exit=0`、`status=""`。
- 最后一次变异及附加探针结束，临时树再次确认同一 HEAD、零 diff、零完整 status。
- `git worktree remove /tmp/worldmock-map-bob-commerce-tree.Cf8AeE` exit 0；该路径不再存在，也不再注册。没有强制删除或广泛递归删除。
- 调用方最终 HEAD 仍为 `2bce358a09046b84841bc5a2da68d82a66b79dbc`，diff exit 0，完整 status 为空。完整结果见 [cleanup.json（包内 cleanup.json）](evidence.zip)。
- 全部报告和日志仅留在 `/tmp/worldmock-map-bob-commerce/`；未复制回调用方。
- 本轮未执行全量测试、外网、真实零售服务、其他未映射规范分支；没有把主代理全量结果作为本报告的独立证据。地点到达资格、未纳入的恢复/呈现条款及完整营业历规则由其他审查范围覆盖与否，本报告不作推定。
