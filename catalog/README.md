# catalog/ — 静态资源与交换对象字典（冻结 JSONL，version 1）

- `resources.jsonl` — 4 条资源定义（money_fen 账户 + energy/sleep_debt/entertainment 三个 level）
- `objects.jsonl` — 163 条交换对象（121 食品 = 8 冻结基线 + 25 第一波扩容（foods/expansion.json）+ 88 第二波扩容（foods/expansion2.json，源自 open-food-facts/china-pool.json 548 条含能量池）+ 30 正餐（meals/items.json，USDA FNDDS 热量溯源）+ 8 耐用品 + travel-service + 3 合同模板）
- `rules/` — 人写的映射/公式声明（off-foods.mapping.toml、travel-fare.toml）
- `overrides/`、`snapshots/` — 预留（例外覆盖/墓碑、anchored 取值快照），v1 尚无内容

数据四分工：原料不进库（datasets）、字典进库（catalog）、证据进库（corpora）、快照进库（scenarios）。
每个静态数值逐参数标注溯源（fact / assumption / scenario），由生成器强制 100% 覆盖。

再生成（读 scenarios + corpora，重跑结果逐字节一致）：

```
python3 scripts/build_catalog.py           # 生成 resources.jsonl + objects.jsonl
python3 scripts/build_catalog.py --check   # 校验在库文件与重生成结果一致（CI 门禁）
```

rules/ 与本文件为人写，生成器只校验不覆写。设计依据：docs/dev/PROPOSAL-resource-registry.md。

再生成：`python3 scripts/build_catalog.py`（`--check` 校验字节一致）。
产物共四件：resources.jsonl、objects.jsonl、manifest.json（版本锚）、overrides/tombstones.jsonl（可空）。

品类准入（修订合同《世界素材品类要求》）：`catalog/manifest.json` 自 2.0 起携带`category_requirements` 版本化声明；`admission.validate_world_categories(catalog, corpora_root)`对正餐/地点/家与办公室可供性/内容元数据/工作材料/生活活动 v2 字段/联系人逐条执行，不满足即拒绝启动（fail-closed）。
