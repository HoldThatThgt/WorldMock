# catalog/ 使用说明

本目录是运行时读取的 3.0 冻结目录。现行行为契约见
[品类契约与交换规则开发规格](../docs/dev/SPEC-category-contract.md)。
对象只保存身份、单一品类、属性增量、来源和对象版本；加载器把品类继承值和闭集规则编译成
LifeEngine 使用的只读对象。旧 `edges`、内联公式和对象内品类结构不是合法原始格式。

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
.venv/bin/python scripts/build_catalog.py           # 重新生成目录数据
.venv/bin/python scripts/build_catalog.py --check   # 校验生成结果与在库文件逐字节一致
```

生成器读取 `scripts/catalog-snapshot-legacy-scenario.json`、`corpora/`、冻结品类契约和闭集规则。
食品密度校验直接读取品类槽位约束，生成器没有第二份密度常量表。
`--check` 同时验证 163 个原生对象可复现、生成资源与对象的每个数值恰好有一份来源，以及清单摘要绑定
`category-contract.json`、`exchange-rules.json`、`affordances.toml` 和所有 `rules/*.toml` 的原始字节。契约与规则自身的严格来源校验由
目录加载器执行。

正式场景显式携带 `recipe_output_bindings`；当前冻结场景为空映射。库存转换的闭集算子通过隔离场景中的
明确输入、输出对象与绑定验证，不为正式生活目录虚构产物。
