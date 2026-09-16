# 出行分时系数标定摘录（anchored 快照）

- 拉取/取点日期：2026-09-16（本仓库引入 travel-time 规则时的一次性冻结）。
- 用途：`catalog/rules/travel-time.toml` 中 driving 分时系数的数值锚点；
  由 `scripts/calibrate_traffic.py` 解析本文件的锚点块并生成
  `catalog/snapshots/traffic/weekday-hour-factors.json`。
- 许可：数字为公开交通统计报告的事实性引用（合理使用），摘录文本为本仓库自有。

## 检索记录（为什么走"报告取点"而不是下载数据集）

按任务约束（≤200MB、优先预聚合）做了如下检索，均未获得合适的中国城市分时速度数据集：

- Hugging Face 数据集检索（2026-09-16，共 16 组检索词：chengdu taxi /
  taxi trajectory speed / traffic speed hour / congestion index city /
  beijing taxi / shanghai traffic / taxi gps / didi / road network speed /
  urban travel time / taxi order / chengdu didi / T-Drive / hangzhou taxi /
  shenzhen taxi / traffic index）：
  - `ywhaok/chengdu_taxi`：网约车请求图数据（graphml+pickle），非分时速度
    统计，无许可声明，弃用。
  - `seablue/DiDi_GAIA_dataset`：license apache-2.0，但为 5.66GB 原始轨迹
    rar（超预算且需重聚合），弃用。
  - 其余检索词无命中或仓库为空。
- Zenodo API 检索 "chengdu taxi speed"：仅安第斯城市驾驶循环、雷达反演等
  无关数据集；Figshare API 不可达（404）。
- 高德交通报告站（report.amap.com）与 TomTom 城市页均为 SPA 动态接口，
  静态抓取不到分时数据。

结论：采用任务书允许的回退路径——权威公开报告的分时拥堵指数人工取点。

## 取点依据（高德《中国主要城市交通分析报告》系列，公开季度报告）

以下为该系列报告长期、反复公开的结构性结论（跨 2021-2024 各季度版本一致）：

1. **指数定义**：拥堵延时指数 = 高峰出行旅行时长 ÷ 自由流（夜间）旅行
   时长。即本仓库 factor 的语义：factor = 拥堵时长 / 自由流时长 ≥ 1。
2. **自由流基准**：夜间 0-5 时接近自由流，指数 ≈ 1.0（定义基准时段）。
3. **双峰形态**：工作日早高峰 7-9 时（峰顶约 8 时）、晚高峰 17-19 时
   （峰顶约 18 时），晚高峰不低于早高峰。
4. **上海峰值区间**：上海长期居该榜单最拥堵城市前二，工作日高峰拥堵
   延时指数各季度在约 1.8-2.1 区间波动。
5. **午间平峰**：日间平峰时段指数约 1.2 上下。

**取点声明**：下表锚点为人工取点——夜谷取定义值 1.00，早/晚峰顶取上海
公开区间的代表值 1.85/1.90，肩部与平峰按上述形态取整值；非锚点小时由
标定脚本线性插值。锚点数值代表"典型工作日形态"，不对应任何单一季度的
精确发布值；如需精确到季度，应替换本快照并重跑标定。

## 锚点表（机器可读，标定脚本的唯一输入）

```json
{"traffic-calibration-anchors": {
  "timezone": "Asia/Shanghai",
  "day_type": "weekday",
  "index_definition": "factor = 高峰出行时长 / 自由流时长（高德拥堵延时指数口径）",
  "source_family": "高德《中国主要城市交通分析报告》（公开季度报告系列）",
  "anchors": {
    "00": "1.05", "02": "1.00", "05": "1.00", "06": "1.12", "07": "1.45",
    "08": "1.85", "09": "1.55", "12": "1.25", "14": "1.18", "16": "1.38",
    "18": "1.90", "19": "1.62", "21": "1.20", "23": "1.07"
  }
}}
```
