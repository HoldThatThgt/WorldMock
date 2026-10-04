# catalog/ 使用说明

> 更新日期：2026-10-02。当前文件是目录 5.3、品类契约与规则绑定 3；配套的场景、地图与世界状态结构版本见[配置设计「文件位置与合同」的发布目标表](../docs/design/08-configuration.md#31-文件位置与合同)。
> [工具交互规格](../docs/dev/SPEC-tool-interaction.md)的构建价格范围、地点类别完整供给和正式食材配方均已实现并发布，
> 下文描述的是现行状态。

本目录是运行时读取的 5.3 冻结目录；文件一律以 LF 换行检出，目录内容摘要按原始字节计算。现行行为契约见
[品类契约与交换规则开发规格](../docs/dev/SPEC-category-contract.md)。
对象只保存身份、单一品类、使用属性增量、来源和对象版本；加载器把品类继承值和闭集规则编译成
LifeEngine 使用的只读对象。契约版本与规则绑定版本均为 3。旧 `edges`、内联公式和对象内品类结构不是合法原始格式。

| 文件 | 用途 | 维护方式 |
| --- | --- | --- |
| `category-contract.json` | 品类树、槽位约束、共享值与稀疏配对 | 冻结审核后手动维护 |
| `exchange-rules.json` | 无表达式的闭集交换算子 | 冻结审核后手动维护 |
| `resources.jsonl`、`objects.jsonl` | 资源定义与原生对象增量 | 正式基线由生成器输出；构建原语可追加版本，品类发布可确认对象归属 |
| `manifest.json` | 目录版本、内容摘要、契约摘要与世界素材要求 | 生成器、构建原语与品类发布入口按各自变更原子刷新 |
| `overrides/tombstones.jsonl` | 对象墓碑记录 | 当前由生成器输出空文件 |
| `affordances.toml` | 可供性定义 | 手动维护 |
| `rules/travel-time.toml` | 闭集数值出行时长表 | 手动维护 |
| `rules/` 其他文件 | 数据映射、动机与人物映射规则 | 手动维护 |
| `snapshots/traffic/` | 出行时长数值的交通锚点与校准产物 | 数据源快照与可复现脚本维护 |

在仓库根目录执行：

```sh
uv run python scripts/build_catalog.py           # 重新生成目录数据
uv run python scripts/build_catalog.py --check   # 校验生成结果与在库文件逐字节一致
```

生成器读取 `scripts/catalog-snapshot-legacy-scenario.json`、`corpora/`（含食材 `corpora/ingredients/items.json`
与构建价格范围 `corpora/foods/purchase-price-ranges.json`）、冻结品类契约和闭集规则。
食品密度校验直接读取品类槽位约束，生成器没有第二份密度常量表。
`--check` 同时验证 172 个原生对象可复现、生成资源与对象的每个数值恰好有一份来源，以及清单摘要绑定
`category-contract.json`、`exchange-rules.json`、`affordances.toml` 和所有 `rules/*.toml` 的原始字节。契约与规则自身的严格来源校验由
目录加载器执行。

## 商品与构建价格范围

商品不携带实际购买报价、卖方、供给库存或备货时长；这些事实由冻结地图的门店供给声明。
购买按供给当前报价、数量及品类配对系数调用闭集算子，在最终金额处向上取整分。
购买编译为动态规则引用，使用和配方仍从商品品类解析；详见
[地图与门店供给开发规格](../docs/dev/SPEC-map-and-storefront.md)。

对象有效属性带有来源的 `purchase_price_fen_min/purchase_price_fen_max`，
通过品类槽位继承或对象差异解析：同币种正整数分，最小值不大于最大值，相等合法。
范围只是离线构建输入，不编译为静态购买转账边；实际付款仍按供给当前 Quote、数量和配对系数求值。

## 地点类别与逐店供给

门店供给不由本目录的生成器产生，由地图发布入口 `scripts/prepare_map.py` 按生成种子离线构建并冻结。
冻结 `corpora/zhangjiang-map/category-assignment.json` 按现有地点kind定义完整准入池，同一文件逐店声明
`location_id/category_ids/object_ids/price_position_percent/source`，五键全部必填。
文件恰含必填 `kinds/locations/source`，全部地图kind包括不售卖地点的空数组均显式声明，
顶层与逐店来源均沿用完整 `DataSource`；实际供给为逐店类别及全部后代对象与显式对象的并集，
全体仍须在kind准入池内，重合不重复；不随机抽几件、不把完整池复制到每店、不让所有地点出售所有商品。
当前正餐无菜系属性，正式菜单子类或具体对象必须有来源；包装食品店选择适用品类，不自动带进全部餐馆正餐。
正式发布须通过[丰富度和餐馆菜单差异验收](../docs/dev/SPEC-map-and-storefront.md#按地点类别生成完整供给)及真实到店购买、制作食用链，
不足或未知素材拒绝发布，不随机补齐。这些阈值是当前正式场景假设，不是行业事实。
库存、卖方和履约继续在 `corpora/zhangjiang-map/supply-assumptions.json` 显式声明；分类是固定数据，不新增运行配置层、缺项默认值或旧稀疏模式的回退读取。
构建对 `[random_seed, location_id, object_id]` 使用
`json.dumps(..., ensure_ascii=False, separators=(',', ':'))` 后编码为 UTF-8，取 SHA-256 摘要的无符号大端整数 `h`，
逐店price_position_percent为有来源的整数5至95，固定 `jitter = h % 11 - 5`，
以 `min + ((max - min) * (price_position_percent + jitter) + 50) // 100` 生成一次报价，不为每商品独立抽整个范围。
本正式地图中的门店按同一地区比较，所有便利店定位至少高于地图内普通超市10个百分点，
同商品报价不得更低，整数舍入同价合法；不新增扰动配置或地区字段。
地图结构 3 必填生成种子与分类哈希，
不同正式场景种子分别发布并钉定供给，地点和路线来源可以共享：白领场景使用种子 20260105，
两份多人场景使用种子 20260106，各自的 `dataset.json` 与 `supplies.json` 位于 `corpora/zhangjiang-map/<种子>/`。
观察、查询、购买、调价检查和恢复不抽价。
正式场景 `products/supplies` 显式包含选中门店全部声明商品及生成供给，加载和正式验收核对实际集合，
缺项拒绝，不自动选择或补齐旧稀疏列表。

## 食材与料理

目录含真实 `ingredient` 品类及其蔬菜原料、粮油调料、食材包三个子类，共九个有来源的食材对象。
规则 `cook-vegetable-stir-fry` 复用单输入单输出库存转换，把一份「素炒什锦食材包」制作成一份
`prepared-meal-vegetable-stir-fry` 品类的正餐；正式场景显式携带 `recipe_output_bindings`，
把该输出品类明确绑定到已发布可食用正餐 `meal-0030`（素炒什锦），不按名称猜测产物。
双方 `inventory_unit=item`，库存资源仍为 `inventory/item`，
制作完成才消耗原料并产出，食用才摄入热量。
不保留无产出的重复做饭绑定，不开发多材料引擎，不以零价供给代替家庭库存。

## 发布身份

工具交互特性首次受影响的发布为目录 5.0、品类和规则绑定 3、场景 4、地图 3，世界状态结构保持 8；
其后的目录内容变更另发版本，现行目录为 5.3。料理或后续目录内容变更按既有规则发布新版本，
最终认证记录实际版本和摘要，禁止同版覆盖；旧输入按身份直接拒绝，不提供迁移。
