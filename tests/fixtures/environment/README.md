# 人工最小测试环境

这个声明的人造世界验证 WorldMock 的登记、加载、规则、状态、记录和导出；地点、人物、内容和数值是人工测试事实，app 名允许使用真实名称。它不包含正式地图、门店来源或真实人物实验。正式环境来源、数量与重建验收由 WorldMock-Dataset 管理。

| 输入 | 人工语义 |
| --- | --- |
| 地点 | 家、公司、一心面馆、二味小炒、三杯咖啡、四季便利、五里公园，共七个 |
| app | 美团、饿了么、抖音、哔哩哔哩，共四个 |
| 食品 | 薯片、柠檬茶、牛肉面、三明治、饭团，共五种；同类店铺同菜单，统一食品价格 |
| 内容 | 测试文章、图文、两条短视频、长视频、实况，共六条；摘要均声明 synthetic 与「测试用合成摘要」 |
| 工作主题 | 季度销售数据整理，目标八小时 |
| 地图 | 种子 7 的生成图，七节点、六条树边与三条额外边；最远点由实际最短路决定 |
| 生活活动 | 安静休息；显式娱乐、社交与健康效果及活动环境 |
| 场景 | 结构 7 的单人、双人、双人四小时三个入口；一人有工作安排与主题，另一人没有 |

`catalog/manifest.json` 使用环境 `artificial-test`、版本字符串 `1`、规则版本 1，精确绑定六个目录 JSON 和三份 TOML。场景 `environment` 钉住同一标识和版本，按相对路径读取语料；真实生产加载器核对所有字节与跨对象引用。初始食品持有是在家一份薯片；已选食品店铺各有 merchant 账户。

`catalog/affordances.toml`、`catalog/rules/motivation.toml` 和 `catalog/rules/persona-mapping.toml` 是 `worldmock.data` 包资源的冻结字节副本，仅绑定当前引擎规则身份，不能作为另一份人工维护规则。阶段 1 与阶段 4 按包资源各刷新一次。

```bash
worldmock environment generate-map --locations tests/fixtures/environment/catalog/locations.json \
  --seed 7 --output tests/fixtures/environment/catalog/map.json --check
worldmock validate --environment tests/fixtures/environment
```

目录与全部场景真实加载属于阶段 1。完整引擎的动作、关闭恢复、自然日和认证导出按冻结规格各实施阶段验证，不能以历史测试结果宣称新格式已完成。
