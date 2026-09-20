# catalog/ 使用说明

资源定义、来源、目录发布与语料设计统一见[资源引擎设计](../docs/design/03-resource-engine.md)；
交换对象、可供性和结算规则见[交换引擎设计](../docs/design/02-exchange-engine.md)。
本目录保存运行时读取的静态定义与规则。

| 文件 | 用途 | 维护方式 |
| --- | --- | --- |
| `resources.jsonl`、`objects.jsonl` | 资源与交换对象定义 | 由生成器输出 |
| `manifest.json` | 目录版本与世界素材品类要求 | 由生成器输出 |
| `overrides/tombstones.jsonl` | 对象墓碑记录 | 当前由生成器输出空文件 |
| `affordances.toml` | 可供性定义 | 手动维护 |
| `rules/` | 数据映射、计费、通行时间、动机与人物映射规则 | 手动维护 |
| `snapshots/` | 保留给取值快照的目录 | 当前尚无内容 |

在仓库根目录执行：

```sh
.venv/bin/python scripts/build_catalog.py           # 重新生成目录数据
.venv/bin/python scripts/build_catalog.py --check   # 校验生成结果与在库文件逐字节一致
```

生成器读取 `scripts/catalog-snapshot-legacy-scenario.json` 和 `corpora/` 中的冻结素材，
不会读取当前场景文件，也不会覆写 `affordances.toml`、`rules/` 与本说明文件。
