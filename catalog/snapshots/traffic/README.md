# traffic 快照：工作日分时出行系数

本目录是 travel-time 规则（`catalog/rules/travel-time.toml`）的 anchored
数值快照，遵循目录溯源设计（快照 + 可重跑标定脚本 + 系数表三件套）。

| 文件 | 角色 |
| --- | --- |
| `amap-report-excerpt.md` | 来源摘录 + 机器可读锚点块（标定唯一输入） |
| `weekday-hour-factors.json` | 标定产物：工作日 24 小时系数表（字符串两位小数） |
| `scripts/calibrate_traffic.py` | 标定脚本：锚点 → 线性插值 → 系数表（仓库根 scripts/） |

- 来源：高德《中国主要城市交通分析报告》公开数字人工取点（回退路径），
  检索记录与取点声明见摘录文件；provenance.type = anchored。
- 门禁：`python3 scripts/calibrate_traffic.py --check` 断言系数表与锚点
  字节一致；`scripts/build_catalog.py --check` 断言本目录关键文件在场；
  `tests/life/test_travel_time.py` 断言 TOML 公式分支与系数表一致。
