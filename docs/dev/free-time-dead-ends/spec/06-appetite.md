# 饭点的食欲加成

> 本章属于[手机优先的空闲时间与空闲时的死路：开发规格](../SPEC-free-time-dead-ends.md)，读法和叫法见那一页。

## 目标与依据

人物刚吃完饭，只要钟点还在自己写的午饭或晚饭时段里，环境层的饥饿感受就是「有点想吃」：吃过了却读到想吃，是提案列在吃饭这条死路里的假信号之一
（「What the run shows」的「Meals」一行，以吃饭为主的长思考共 86 分钟）。时段一开始，吃得饱饱的人通常也会被叫起来一次，饥饿感受从「不饿」变成「有点想吃」；时段结束时又被叫起来一次。
提案估计去掉这个假信号能省下约 28 次时段起止的唤醒（「Review decisions」的「Meal-window appetite bonus」一行）。

原因在饥饿体感的公式（`src/worldmock/life/felt.py:154-178`）：

- 基值 `clamp((锚定热量 − 能量) / (锚定热量 − 400), 0, 1)` 乘昼夜因子（`felt.py:128-137`）；能量不低于锚定热量（缺省 1,200 千卡，`felt.py:15`）时基值为 0。
- 饭点项 `meal_window_bonus`（`felt.py:140-151`）只看钟点：落在午饭或晚饭时段里就加 0.15（`felt.py:33`），不看能量。
- 0.15 正好是「有点想吃」的下界（`felt.py:39-44`），所以饭点时段里体感最低就是「有点想吃」，能量加满也一样。
- 唤醒表的 `felt_band_change` 在四项体感有一项的分级与上一次决策时不同时命中（`src/worldmock/life/schedule.py:245`）；饭点起止总是结算边界（`src/worldmock/life/body.py:206-209`、`:273-283`），
  所以吃饱的人每到一个起止，饥饿分级都要跳一档，与上一次决策时的分级不同，就被叫起一次。

首次真实模型单日运行规格当时把这个事实告诉了所有者，决定身体模型不改（`docs/dev/first-real-day-run/spec/07-meals.md:13`、`:221-232`、`:310`）。
本章落实提案「Review decisions」的「Meal-window appetite bonus」一行；所有者 2026-10-15 的答复是：**能量低于饥饿锚定热量时才加饭点项**。

## 行为规格

### 公式

记能量 E、锚定热量 A（两处生产调用都取缺省值 1,200 千卡：`body.py:244`、`src/worldmock/life/projection.py:24`）、昼夜因子 c(t)、饭点项 b(t)（钟点在时段里为 0.15，否则为 0）：

| | 饥饿体感 |
| --- | --- |
| 今天 | `clamp((A − E) / (A − 400), 0, 1) · c(t) + b(t)` |
| 改后 | `clamp((A − E) / (A − 400), 0, 1) · c(t) + (b(t) 若 E < A，否则 0)` |

E ≥ A 时基值本来就是 0，所以改后能量不低于锚定热量的人在任何钟点都是 0，读到「不饿」；E < A 时与今天完全相同。实现：`felt.py:176-178` 的返回换成两行

```python
    bonus = meal_window_bonus(time_hour, windows) if energy_kcal < appetite_anchor_kcal else 0.0
    return base * appetite_circadian_multiplier(time_hour) + bonus
```

其余不变：

- 分级表、两条只看能量的低能量保底（`felt.py:189-200`）、上限 1.32（E 为 400 时 1.17 + 0.15）。
- `meal_window_bonus` 本身不变，仍只回答「这个钟点在不在饭点时段里」；门槛加在 `compute_appetite` 里，用传入的 `appetite_anchor_kcal` 比较。
- 钟点合法性仍由 `compute_appetite` 先查（`felt.py:172`），E ≥ A 时不调用 `meal_window_bonus` 也不漏掉这项检查。
- 饭点时段的起止仍是结算边界：每到起止照常做一次共同身体结算，只是能量不低于锚定热量的人在那一刻分级不变，没有 `felt_band_change`。

### 算例

样例环境的人物午饭时段 12:00–13:00、晚饭时段 18:30–19:30（`tests/fixtures/dataset-environment/persona.toml`），12:00 的昼夜因子是 0.915，20:00 是 1.17。
下表的数都用基线代码与改后的公式在内存里算过：

| 情形 | 能量 | 钟点 | 今天 | 改后 |
| --- | --- | --- | --- | --- |
| 吃过午饭，还在午饭时段里 | 1,515.625 千卡 | 12:15 | 0.15，「有点想吃」 | 0，「不饿」 |
| 饿着进了午饭时段 | 1,000 千卡 | 12:00 | 0.25 × 0.915 + 0.15 = 0.37875，「饿」 | 同左 |
| 恰在锚定热量上 | 1,200 千卡 | 12:00 | 0.15，「有点想吃」 | 0，「不饿」 |
| 比锚定热量低 1 千卡 | 1,199 千卡 | 12:00 | 0.00125 × 0.915 + 0.15 = 0.15114375，「有点想吃」 | 同左 |
| 比锚定热量低 1 千卡，时段之外 | 1,199 千卡 | 11:59:24 | 约 0.0011，「不饿」 | 同左 |
| 晚饭时段里吃得很饱（现有用例钉住的一行） | 1,600 千卡，晚饭时段 20:00–21:00 | 20:00 | 0.15 | 0 |

「吃过午饭」那一行的 1,515.625 千卡来自现有用例的情形：能量 1,000 千卡的人 12:00 在 `meal_environment` 副本的家里做「测试休息」，12:15 做完那一刻吃到一餐
（`tests/integration/life/test_location_meal.py::test_appetite_after_the_meal_reads_the_new_energy`，在真实引擎上跑过）。

「恰在锚定热量上」与「比锚定热量低 1 千卡」两行之间是改后多出的一处跳变：在饭点时段里，能量跌到 1,200 千卡以下的那一秒，饥饿体感从 0 跳到 0.15 以上，「不饿」变成「有点想吃」。
饿着的人在饭点起止处加减 0.15 的跳变照旧。

### 对唤醒的影响

| 人物 | 今天 | 改后 |
| --- | --- | --- |
| 能量不低于 1,200 千卡，跨过饭点时段的起点 | 「不饿」→「有点想吃」，`felt_band_change` | 分级不变，没有 `felt_band_change`；这一刻有别的原因（播报、班次起止、整点推送）时照常决策 |
| 同上，跨过终点 | 「有点想吃」→「不饿」，`felt_band_change` | 分级不变，没有 `felt_band_change` |
| 能量低于 1,200 千卡，跨过起点或终点 | 加减 0.15，换档时 `felt_band_change` | 同左 |
| 在饭点时段里，能量跌破 1,200 千卡 | 一直是「有点想吃」，这一刻没有变化 | 这一秒「不饿」→「有点想吃」，`felt_band_change` |
| 在饭点时段里吃完，能量回到 1,200 千卡以上 | 「饿」之类 →「有点想吃」 | 「饿」之类 →「不饿」，同样换档 |

吃饱的人不再因饭点起止被叫起，叫起她的是真正饿了的那一刻。下一次身体边界的预测不改：饭点起止照旧列为候选（`body.py:206-207`），
饭点时段里跌破锚定热量的那一刻由现有的体感扫描找到（`body.py:208-209`、`:247-270`，以不超过 900 秒的步长扫描，再在步内二分到整秒）。

在样例环境副本上实测（「测试休息」改成两小时，做的期间每次决策只播报；基线代码与改后的公式各跑一次）：

| 情形 | 时刻 | 今天的唤醒原因 | 改后的唤醒原因 | 那一秒读到的饥饿感受（今天 → 改后） |
| --- | --- | --- | --- | --- |
| 能量 1,800 千卡，11:00 开始 | 12:00:00 | `felt_band_change`、`progress_report_due`、`duty_boundary`、`app_push` | `progress_report_due`、`duty_boundary`、`app_push` | 「有点想吃」→「不饿」 |
| 同上 | 13:00:00 | `planned_end`、`felt_band_change`、`no_active_activity`、`duty_boundary`、`app_push` | `planned_end`、`no_active_activity`、`duty_boundary`、`app_push` | — |
| 能量 1,000 千卡，11:00 开始 | 12:00:00、13:00:00 | 都含 `felt_band_change` | 同左 | 12:00 都是「饿」 |
| 能量 1,800 千卡（样例环境的初值），18:00 开始 | 18:30:00 | `felt_band_change`、`progress_report_due` | `progress_report_due` | 「有点想吃」→「不饿」 |
| 同上 | 19:30:00 | `felt_band_change`（这一秒唯一的原因） | 没有，`decision_offered` 为假 | — |
| 能量 1,210 千卡，12:00 开始 | 12:06:10 | 没有唤醒（12:00 起一直是「有点想吃」，下一次决策在 12:30:00 的播报） | `felt_band_change`，结算后能量 1,199.979 千卡 | 今天 12:00 起都读「有点想吃」；改后 12:00 读「不饿」，12:06:10 读「有点想吃」 |

### 边界情形

- 恰在锚定热量上（E = A）不加饭点项：基值在 E = A 时为 0，与「能量不低于锚定热量时饥饿体感为 0」同一个界。
- 两个时段重叠时仍只加一次（`felt.py:148-150`），门槛不改变这一点。
- 能量低于紧急阈值或 100 千卡时的两条保底只看能量，不受饭点项影响，照旧。
- 运行配置之外的锚定热量（单元用例直接传入的 `appetite_anchor_kcal`）按同一个门槛比较：例如锚定热量 1,600、能量 1,500、20:00 在晚饭时段里，体感是 100 / 1,200 × 1.17 + 0.15 = 0.2475。

### 明确不做

- 不改饭点项的幅度、时段的读法（`in_clock_window`）、昼夜因子、分级表和两条低能量保底。
- 不采用提案列的另两种做法：只拦掉饭点项单独引起的跨档唤醒而保留「有点想吃」，或保留今天的公式。
- 不改下一次身体边界的预测，不为锚定热量另加候选时刻。
- 不改人物读到的文字：环境层仍只给分级文字，系统层不承诺吃完就「不饿」（「解饿只有两种办法」一条的那半句只按[「系统层措辞」](03-system-layer.md)改）。
- 不改锚定热量的取值，也不把它变成人设表的字段。

### 本章定下的细节

| 事项 | 定下的做法 | 理由 |
| --- | --- | --- |
| 门槛的比较 | `energy_kcal < appetite_anchor_kcal`，严格小于 | 与基值为 0 的界相同，所有者的话是「低于」 |
| 门槛加在哪 | `compute_appetite` 里，`meal_window_bonus` 不变 | `meal_window_bonus` 是钟点判断，另有用例按钟点直接钉它（含变异杀手 `test_meal_windows_are_read_on_the_clock_without_any_range_rule`）；门槛是食欲公式的一部分 |
| 说明文字 | `compute_appetite` 说明里的公式写成「appetite = clamp((anchor − energy)/(anchor − 400), 0, 1) · 昼夜因子 + 餐时项（仅当能量低于锚定热量）」；`APPETITE_MEAL_WINDOW_BONUS` 上方的注释（`felt.py:30`）改为「餐时项幅度（窗口内且能量低于锚定热量时 +0.15，其余为零）。」 | 说明与公式一致 |
| `body._meal_window_boundaries` 的说明（`body.py:274`） | 不改 | 它说的是餐时项 `meal_window_bonus` 在起止处跳变，这仍成立；起止仍是候选边界，饥饿分级跳不跳由体感扫描按 `compute_appetite` 判定 |
| 实测用的副本 | 用例在 `tests/life/environment_inputs.py` 的 `copy_environment` 副本上用 `set_content(root, "c-rest", duration_seconds=7200)` 把「测试休息」改成两小时，再经 `engine_factory(root, mutate=...)` 改初始能量与开始钟点（开始与截止都移到同一整点） | 现有帮助函数就能造出跨两个饭点起止的一件事；样例环境本身不改 |

## 接口与文件指引

| 文件 | 改什么 |
| --- | --- |
| `src/worldmock/life/felt.py` | `compute_appetite` 的返回换成上文两行，改说明；`APPETITE_MEAL_WINDOW_BONUS` 上方的注释 |

本章没有别的生产文件，实施归轨道 B（提示与计划）。

直接删除，不留兼容：今天「饭点里一律加 0.15」的算法。不留开关、不按运行版本选公式；这是引擎固定规则的改变，规则版本随整次改动升到 6（[「版本、身份与基线」](08-versions.md)），
旧运行按代码指纹被拒，不能恢复或重放。

夹具：样例环境不改。用例在副本上把「测试休息」改成两小时（上表），用 `engine_factory(mutate=...)` 改初始能量和开始钟点。
两份基线和录制样例里有跨饭点的身体结算与决策，收尾步重冻、重录一次。

约束：

- `compute_appetite` 改后仍不超过 50 行、4 个参数；`felt.py` 每行不超过 120 字符（今天 252 行）。
- 已登记的变异 `meal_window_read_off_the_clock`（`felt.in_clock_window` 里的 `start, end = start % 24, end % 24`）的原文行不动，它的杀手 `tests/unit/life/test_body_felt_sleep_boundaries.py::test_meal_windows_are_read_on_the_clock_without_any_range_rule` 不改名、不改断言。
- 下文变异的原文行在实现里要逐字是 `bonus = meal_window_bonus(time_hour, windows) if energy_kcal < appetite_anchor_kcal else 0.0`，在 `compute_appetite` 里只出现一次。
- 没有新的公开函数，没有函数内导入；只改代码指纹，提示与工具指纹不变。
- 设计文档的家是《交换引擎》「身体共同结算」讲饭点项的句子，见[「设计文档与手册」](07-design-docs.md)；本章不改设计文档。

## 测试要求

行为用例经真实引擎：`engine_factory` 建副本引擎，`act` 开始「测试休息」，`until_ended` 推进（其间的决策只播报），读那一秒落库的共同身体结算记录（`environment_event`、`kind` 为 `body_settlement`，
同 `tests/integration/life/test_location_meal.py:71-75` 的 `settled_at`），以及 `store.read_agent_events("observation", 1)` 落库的观察。新的整合用例放在新文件 `tests/integration/life/test_meal_window_appetite.py`。

### 规则与用例

| 规则 | 层 | 测试 | 验收行为 |
| --- | --- | --- | --- |
| 饭点项只在能量低于锚定热量时加 | unit | `tests/unit/life/test_body_felt_sleep_boundaries.py::test_meal_bonus_is_added_only_below_the_hunger_anchor`（新，参数化） | 午饭 12:00–13:00、晚饭 18:30–19:30：1,515.625 千卡 12:15 → 0，「不饿」；1,200 千卡 12:00 → 0，「不饿」；1,199 千卡 12:00 → 0.15114375，「有点想吃」；1,000 千卡 12:00 → 0.37875，「饿」；1,199 千卡 11:59:24（11.99 时）→ 约 0.0011，「不饿」。锚定热量 1,600、晚饭 20:00–21:00、20:00：1,500 千卡 → 0.2475；1,600 千卡 → 0 |
| 公式其余部分不变 | unit | 同文件 `::test_appetite_uses_real_energy_day_phase_and_declared_meal_bonus`（改一行） | 四行参数照旧算，只有 `(1600, 20, 1200, MealWindows(dinner=(20, 21)))` 由 0.15 改为 0.0 |
| 吃饱的人跨过饭点起止不因饥饿被叫起，饿着的人照旧 | integration | `tests/integration/life/test_meal_window_appetite.py::test_crossing_the_lunch_window_edges_wakes_only_a_hungry_person`（新，两个参数：1,800 千卡、1,000 千卡） | 副本里「测试休息」两小时，11:00 开始做到 13:00。12:00:00 与 13:00:00 两次共同身体结算的 `wake_reasons`：1,800 千卡时都不含 `felt_band_change`，1,000 千卡时都含；12:00:00 落库的观察里环境层的 `appetite` 分别是「不饿」「饿」 |
| 吃饱的人在饭点结束那一秒没有决策 | integration | 同文件 `::test_a_fed_person_gets_no_decision_at_the_end_of_the_dinner_window`（新） | 同一副本，样例环境的初始能量，18:00 开始：18:30:00 的结算 `wake_reasons` 恰为 `["progress_report_due"]`；19:30:00 的结算 `decision_offered` 为假、`wake_reasons` 为空 |
| 在饭点里跌破锚定热量那一秒换档 | integration | 同文件 `::test_falling_below_the_anchor_inside_a_meal_window_is_a_felt_band_change`（新） | 同一副本，能量 1,210 千卡，12:00 开始：12:00:00 的观察读「不饿」；12:00 之后第一次带 `felt_band_change` 的结算早于 12:30:00，结算后能量在 1,199 到 1,200 千卡之间（实测 12:06:10、1,199.979 千卡），那一秒的观察读「有点想吃」 |
| 在饭点里吃完读到新的能量 | integration | `tests/integration/life/test_location_meal.py::test_appetite_after_the_meal_reads_the_new_energy`（改参数） | 08:00 与 12:00 两个参数做完都是「不饿」，那一秒的唤醒原因都含 `felt_band_change` |

### 钉住今天行为、要改的现有用例

| 用例 | 钉的旧行为 | 处理 |
| --- | --- | --- |
| `tests/unit/life/test_body_felt_sleep_boundaries.py::test_appetite_uses_real_energy_day_phase_and_declared_meal_bonus`（`:119`） | 1,600 千卡在晚饭时段里是 0.15 | 这一行的期望改为 0.0；函数名不改 |
| `tests/integration/life/test_location_meal.py::test_appetite_after_the_meal_reads_the_new_energy`（`:462-480`） | 12:00 在午饭时段里吃完仍是「有点想吃」，「不会到不饿」 | 参数 `(12, "饿", "有点想吃")` 改为 `(12, "饿", "不饿")`，`ids` 不变；说明改为「能量 1,000 开始做「测试休息」：08:00 与 12:00（午饭时段之内）做完后饥饿分级都降到「不饿」，那一秒的唤醒原因含 felt_band_change。」；去掉 `if appetite_after == "不饿":`，`felt_band_change` 的断言对两个参数都做 |
| 两份基线与录制样例 | 跨饭点时的身体结算与唤醒 | 不手改；收尾步重冻、重录，见[「版本、身份与基线」](08-versions.md) |

找这张表的办法：在内存里把 `felt.compute_appetite` 换成改后的公式，跑了 `tests/` 下提到饥饿、唤醒原因、决策时刻或饭点的 59 个用例文件（不含完整日基线与查看器录制），
因公式失败的只有表里那两条；另有三条失败只因内存里替换的函数没写错误日志（`test_appetite_requires_an_anchor_above_the_formula_floor` 两个参数、`test_negative_energy_is_rejected_before_an_appetite_is_reported` 查日志），真实改动保留那两行日志，不受影响。
这一轮不覆盖 Ray 工作进程里执行的代码，也不覆盖没提到这些词的文件；轨道并入后的第二级、第三级测试门照常全量核对（[「实施顺序与验收」](09-implementation.md)）。

### 源码变异

本章登记一条，登记进 `tests/dataset_acceptance.py` 轨道 B 的那一段。杀手直接导入 `worldmock.life.felt`（unit），或在主进程里经 `engine_factory` 的真实引擎执行它（integration）。

| 定下的事项 | 把规则改坏的方式（原句 → 替换） | 被哪条测试杀死 | 层 |
| --- | --- | --- | --- |
| 饭点项只在能量低于锚定热量时加（`meal_bonus_ignores_energy`，`worldmock.life.felt::compute_appetite`） | `bonus = meal_window_bonus(time_hour, windows) if energy_kcal < appetite_anchor_kcal else 0.0` → `bonus = meal_window_bonus(time_hour, windows)`（不论能量都加） | `tests/unit/life/test_body_felt_sleep_boundaries.py::test_meal_bonus_is_added_only_below_the_hunger_anchor`；`tests/integration/life/test_meal_window_appetite.py::test_crossing_the_lunch_window_edges_wakes_only_a_hungry_person`；`tests/integration/life/test_meal_window_appetite.py::test_falling_below_the_anchor_inside_a_meal_window_is_a_felt_band_change` | unit、integration |
