# 零点计划、上班安排与明天

> 本章属于[手机优先的空闲时间与空闲时的死路：开发规格](../SPEC-free-time-dead-ends.md)，读法和叫法见那一页。

## 目标与依据

提案看的那次运行从 9 月 28 日跑到 10 月 10 日；10 月 1 日到 7 日是国庆假期，10 月 10 日（星期六）调休上班。零点计划在每一个「上班安排」与星期几不一致的日子都跟着星期几走：
10 月 1 日写了上班的目标，10 月 2 日和 5 日到 7 日写了完整的工作日计划，10 月 10 日写成了周末。人物第二天早上在决策时发现不对，前后改了六次计划，约 24,800 个思考 token；
假期的晚上，她记下的打算又常常默认第二天要上班（提案「What the run shows」与「Review decisions」的「Tomorrow's arrangement」一行）。原因在模板与输入里：

- 计划模板 `PLAN_DAY_PROMPT`（`src/worldmock/life/day_planning.py:38-48`）讲工作安排的只有一句（`:47`）「工作安排是固定世界事实，今日目标不能改变班次；不要安排在班次中离开工作地点，已声明休息时段除外。」，
  没说哪天上不上班看什么；输入里「生成时刻」紧挨着写星期几（`src/worldmock/life/day_plan_inputs.py:76`，名称表 `:26`）。
- 输入「上班安排」是 `work_schedule.day_arrangement` 给出的四键字典（`src/worldmock/life/work_schedule.py:65-74`，经 `day_plan_inputs.py:128-133`），休息日只写「休息日」，看不出这一天本来是星期几的班。
- 零点计划思考关闭、只有一个严格工具、不能查询（`day_planning.py:74-83`），计划时得不到别的提示。
- 环境层只有今天的 `duty`（`src/worldmock/life/environment_layer.py:12-16`、`:32-39`），人物不知道明天上不上班。
- 「给母亲打个电话」被写进个人事务七次（提案「The day plan」）。个人事务今天就只收商品，但设计文档没写这条规则，系统层也没说。

本章落实提案「The day plan」一节里归 WorldMock 仓库的三件事：模板换句、上班安排加说明、个人事务只记商品；以及「Review decisions」里「Tomorrow's arrangement」一行。
所有者 2026-10-15 的答复：明天的安排「要，日期，周几，明天要不要上班」；计划时的「能做的事」不采纳，只靠决策时改计划的那句规则（句子见[「系统层措辞」](03-system-layer.md)）。

相关的代码事实：

| 事实 | 位置 |
| --- | --- |
| 例外优先于每周声明，空数组表示休息；没有声明的星期几不当班 | `work_schedule.py:31-36` |
| 整份工作安排只有一个工作地点 `workplace`，班次不带地点；例外是一个班次或空数组 | `src/worldmock/input_models.py:48-84` |
| `day_arrangement` 的 `places` 参数不用，`place` 直接取 `schedule.workplace` | `work_schedule.py:65-74` |
| 环境层 `duty` 是当天的安排加 `status`；零点计划输入「上班安排」是计划日期那一天的安排；两处都经 `day_arrangement` | `work_schedule.py:77-85`、`environment_layer.py:32-39`、`day_plan_inputs.py:128-133` |
| `duty` 的 Schema 是封闭对象，每个键必填 | `src/worldmock/life/prompt_schemas.py:48-51`、`:186-192`、`:193-203` |
| 七层 Schema 进提示指纹，计划模板不进 | `src/worldmock/agents/life_prompt.py:161-166` |
| 计划输入以 `sort_keys=True` 写成 JSON；环境层按字典次序渲染，不排序 | `day_planning.py:80`、`src/worldmock/life/narration.py:175` |
| 同一秒续接的 `changes` 只取环境层的体感 | `src/worldmock/agents/life_continuation.py:111-120` |
| 星期名称表只有计划输入里一份 | `day_plan_inputs.py:26` |
| 模块说明说「加载时的日历交叉核对」也经这里取班次，实际没有这样的核对（加载时只查工作地点能否解析）；属性表写着不存在的 `workplace_location_id` | `work_schedule.py:5-6`、`:8`；`src/worldmock/environment.py:168-171` |
| 个人事务「新增」的 `item` 是「要取得的商品名称」，解析不到唯一商品时宿主整条纠错、环境整条拒绝 | `src/worldmock/life/models.py:936`、`:960`；`src/worldmock/life/errands.py:384-401`、`:455-467`；`custom/agents/life_agent.py:818-833`；`src/worldmock/life/session_actions.py:228-242` |

## 行为规格

### 零点计划模板

替换：把「工作安排是固定世界事实，今日目标不能改变班次；不要安排在班次中离开工作地点，已声明休息时段除外。」换成

```text
上班安排的 day 是今天上不上班的唯一依据，不看星期几：休息日没有通勤、打卡和上班，即使是星期一到星期五；工作日照 hours 与 place 上班，即使是周末。工作安排是固定世界事实，今日目标不能改变班次；不要安排在班次中离开工作地点，已声明休息时段除外。
```

这一句在模板里占一行，130 字，超过源码一行 120 字符的宽度。源码在两半之间用行尾反斜杠续行：反斜杠加换行在 Python 字符串里被丢掉，模板的文字仍是上面这一整行，源码里旧的一行原样留着。
`day_planning.py:37-48` 改后逐字如下（以「上班安排的 day」开头的那一行源码末尾是一个反斜杠）：

```python
#: 零点计划的中文模板（docs/design/06-agent.md「零点计划」）。
PLAN_DAY_PROMPT = """
根据一个人的真实档案与背景、当天工作安排、已核验经历、已结算动作结果、明确为主观的收尾记录、
未完成个人事务、本人在途订单以及上一天的目标，为他写今天的目标清单。
只调用一次 submit_day_plan，提交完整的零至八条中文目标，按优先级排列，意图不重复。记录中没有的事情不要编造。
goal 写今天特别要做的事情，不超过三百字；平常每天的作息习惯不重复列目标。
remind_time 是当天本地 HH:MM，必须晚于生成时刻且早于次日零点，不需要提醒时填 null。
提醒只是醒着时到点请他决策，不会打断睡眠。around_time 写当天大致 HH:MM 或 null，不触发提醒；
正文尽量用上午、午饭后或先后关系说明时段。
个人事务已经记在待办中，无需抄成目标；计划不创建、完成或取消个人事务，也不判定目标是否办成。
上班安排的 day 是今天上不上班的唯一依据，不看星期几：休息日没有通勤、打卡和上班，即使是星期一到星期五；工作日照 hours 与 place 上班，即使是周末。\
工作安排是固定世界事实，今日目标不能改变班次；不要安排在班次中离开工作地点，已声明休息时段除外。
""".strip()
```

两行源码各 83 字符和 48 字符。`.strip()` 之后模板仍是九行，最后一行恰是上面那一整句，「工作安排是固定世界事实」只出现一次（按这段源码在内存里求值核过）。
模板开头列出输入的那一句不改。模板不进提示指纹，这一处只改代码指纹；请求的形状（系统消息是模板、用户消息是输入的 JSON、唯一严格工具、思考关闭）不变。

### 上班安排的说明 `note`

`work_schedule.day_arrangement` 返回的字典在 `breaks` 之后多一个键 `note`，工作日、休息日都有。键的次序：

| 读者 | 键的次序 |
| --- | --- |
| `day_arrangement`（零点计划输入「上班安排」） | `day`、`hours`、`place`、`breaks`、`note` |
| `duty`（环境层，照旧在安排之后接 `status`） | `day`、`hours`、`place`、`breaks`、`note`、`status` |

`note` 只在一种情形下不为 `null`：这一天写在 `exceptions` 里，并且例外定下的上班或休息与 `weekly` 对这个星期几的声明不同。两种写法：

| `weekly` 对这个星期几 | 这一天的例外 | `note` |
| --- | --- | --- |
| 有班次 | 空数组 | 「平常星期X上班，今天按工作安排休息」 |
| 没有班次 | 一个班次 | 「平常星期X休息，今天按工作安排上班」 |

X 按这一天的 `date.weekday()` 从 `WEEKDAY_NAMES` 取，写成「星期一」到「星期日」。其余情形一律为 `null`：

| 情形 | `day` | `note` |
| --- | --- | --- |
| 没有例外，`weekly` 有班次 | 工作日 | `null` |
| 没有例外，`weekly` 没有班次 | 休息日 | `null` |
| 例外是班次，`weekly` 也有班次，时段或休息时段不同 | 工作日，`hours`、`breaks` 是例外的 | `null`：当天的时段已经写在 `hours` 与 `breaks` 里 |
| 例外是班次，与 `weekly` 的班次相同 | 工作日 | `null` |
| 例外是空数组，`weekly` 也没有班次 | 休息日 | `null` |

工作地点不会因例外不同：整份工作安排只有一个 `workplace`（`input_models.py:67-72`），不为它设说法。没有写 `work_schedule` 时 `day_arrangement` 仍返回 `None`，`duty` 仍为 `null`。

判定只比两件事：`weekly` 这个星期几有没有班次，`shift_on(schedule, day)` 有没有班次。两者相同就没有说明。没有例外时 `shift_on` 取的就是 `weekly`，两者必然相同，所以不另查 `exceptions`。
实现是 `work_schedule.py` 里的私有函数，由 `day_arrangement` 调用；两个读者都从这一份字典得到 `note`，零点计划输入不另算：

```python
def _exception_note(schedule: Any, day: date) -> str | None:
    """@brief 日期例外把这一天的上班或休息改得与每周声明不同时的一句说明；其余情形为 None。"""
    usual = schedule.weekly.get(str(day.weekday())) is not None
    if usual == (shift_on(schedule, day) is not None):
        return None
    name = WEEKDAY_NAMES[day.weekday()]
    return f"平常{name}上班，今天按工作安排休息" if usual else f"平常{name}休息，今天按工作安排上班"
```

Schema：`_DUTY`（`prompt_schemas.py:187-188`）在 `breaks` 与 `status` 之间加 `"note": _NULLABLE_TEXT`，`_object` 把它列为必填；`note` 是非空文字或 `null`。
休息日与工作日的 `if`/`then`/`else`（`:189-192`）不约束 `note`，两种日子都可以有说明。上方注释（`:186`）改为 `#: 工作日写班次、工作地点名与四种状态之一；note 是日期例外改了上不上班时的一句说明，其余为 null。`

### 星期名称表

`day_plan_inputs._WEEKDAYS`（`day_plan_inputs.py:26`）移到 `work_schedule.py`，改名为公开常量 `WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")`，下标是 `date.weekday()`。
读它的地方：计划输入的「生成时刻」（`day_plan_inputs.py:76` 改为 `work_schedule.WEEKDAY_NAMES[local.weekday()]`，这个模块已经导入 `work_schedule`）、`_exception_note`、`tomorrow`（下一节）、
`prompt_schemas.py` 的 `weekday` 枚举（从 `work_schedule` 导入，与已有的 `DUTY_DAYS`、`DUTY_STATUSES` 同一行导入）。`day_plan_inputs.py` 里的旧表删除。`work_schedule.py` 仍只依赖标准库。

同一次改动更正 `work_schedule.py` 的模块说明：讲读者的那半句（`:5-6`）改为「环境层 `duty` 与 `tomorrow`、零点计划输入「上班安排」都经这里取同一天的班次。」，去掉「加载时的日历交叉核对」；
属性表（`:8`）里的 `workplace_location_id` 改为 `workplace`。`day_arrangement` 的说明写明五个键。

### 环境层的明天 `tomorrow`

环境层在 `duty` 之后多一个键 `tomorrow`，总是出现，是环境层的最后一个键。环境层的键次序变为 `layer`、`observed_at`、`body`、`location`、`weather`、`duty`、`tomorrow`。
`tomorrow` 是封闭对象，键依次是：

| 键 | 取值 |
| --- | --- |
| `date` | 观察时刻所在本地日的下一天，`YYYY-MM-DD`。本地日按运行配置的时区算，加一天按日历算 |
| `weekday` | 这一天的星期名，取自 `WEEKDAY_NAMES` |
| `day` | 这一天 `day_arrangement(...)["day"]`，即「工作日」或「休息日」；没有写 `work_schedule` 时为 `null` |

- 实现：`work_schedule.tomorrow(schedule, now, timezone_name, places) -> dict`，参数与 `duty` 相同：

  ```python
  def tomorrow(schedule: Any, now: int, timezone_name: str, places: Mapping[str, str]) -> dict[str, Any]:
      """@brief 环境层 tomorrow：此刻所在本地日的下一天的日期、星期名与上不上班；没有工作安排时 day 为 None。"""
      day = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date() + timedelta(days=1)
      arrangement = day_arrangement(schedule, day, places)
      return {"date": day.isoformat(), "weekday": WEEKDAY_NAMES[day.weekday()],
              "day": None if arrangement is None else arrangement["day"]}
  ```

  `environment_layer._tomorrow(context) -> dict` 像 `_duty` 一样在函数内导入它，按目录拼出地点显示名后调用；`environment_layer` 在 `"duty": _duty(context)` 之后接 `"tomorrow": _tomorrow(context)`。
- 明天只写这三项，不写时段、地点和说明。明天的说明在那一天成了今天时由 `duty` 给出。
- Schema：新增 `_TOMORROW = _object({"date": _DATE, "weekday": {"enum": list(WEEKDAY_NAMES)}, "day": {"enum": [*DUTY_DAYS, None]}})`，封闭、三键必填，`_DATE` 是现有的本地日期写法（`prompt_schemas.py:25`）；
  `ENVIRONMENT_LAYER_SCHEMA` 在 `"duty"` 之后加 `"tomorrow": _TOMORROW`。
- 同一秒里时钟不动，`tomorrow` 与 `duty` 一样在续接链里不变，也不进续接的 `changes`：续接只比环境层的体感（`life_continuation.py:111-120`），续接的逻辑不改。
- 零点计划不读 `tomorrow`：计划只管计划日期那一天，那一天的安排已经在「上班安排」里。

### 个人事务只记商品

引擎今天就是这样，不改代码：「新增」的 `item` 必须解析到唯一一件目录商品（精确或无损规整后唯一，从不换成近似的名称），办成只看本人真实收到的这件商品。
写成没有商品的打算时，宿主一侧整条控制请求合并成一次纠错，环境一侧以 `ACTION_REQUEST_INVALID` 拒绝整条请求，什么都不登记。规则在设计文档里落在《模拟引擎》「唤醒、日计划与个人事务」，
系统层的说法是「个人事务在三个活动控制工具的 errands 中维护」一条新接的一句（[「系统层措辞」](03-system-layer.md)）。

样例环境上的实例（基线代码上跑过）：运行开始在家做「测试休息」，做完后活动名为「进行测试休息（1月5日 08:00 起）」、待收尾；收尾时带一条 `item` 为「给母亲打个电话」的新增事务，结果是：

| 字段 | 值 |
| --- | --- |
| `status`、`error_code` | `rejected`、`ACTION_REQUEST_INVALID` |
| `reason` | `No product is named 「给母亲打个电话」.` |
| `next_step`、`fix` | `Choose a known object and an offered action.`、`Use an exact name.` |
| `unmet` | 空数组 |
| `items[0]` | `path` 为 `["tool_calls", 0, "function", "arguments", "errands", 0, "item"]`，`written` 为「给母亲打个电话」，`error_code` 为 `NAME_NOT_FOUND`，`expected` 为 `An exact known product name is required.`，`why` 同 `reason`，`allowed_values` 与 `candidates` 为空数组 |

那项活动仍待收尾（`concluded_at` 为 `null`），人物的个人事务仍为空。再提交同样的收尾、新增事务改为名称「纸巾」、`item` 「纸巾」：受理，登记一条 `product_id` 为 `tissue`、`quantity` 为 1 的事务。

### 为什么不加「能做的事」

提案建议给零点计划加一项输入「能做的事」，列出各地点能做的事、当天开放时段和有时段的内容 app；所有者 2026-10-15 答复不采纳，只靠决策时改计划的规则。
所以零点计划仍只读现有的输入（`day_plan_inputs.py:74-85` 加上 `day_planning.py:95` 的「已核验经历」），不读目录里的地点、单元、app 与商品；
计划写了做不到的事，由人物决策时照「作息与习惯照人设层过」一条新接的一句，用 `revise_day_plan` 删掉或换掉。

### 算例：国庆与调休

人设的工作安排：工作地点「公司」，星期一到星期五 09:00–18:00，12:00–13:00 休息；两个日期例外，10 月 1 日（星期四）休息，10 月 10 日（星期六）上班。时区 Asia/Shanghai。

```toml
[work_schedule]
workplace = "公司"

[work_schedule.weekly."0"]
starts = 540
ends = 1080
breaks = [[720, 780]]
# "1" 到 "4" 与 "0" 相同

[work_schedule.exceptions]
"2026-10-01" = []

[work_schedule.exceptions."2026-10-10"]
starts = 540
ends = 1080
breaks = [[720, 780]]
```

环境层的 `duty` 与 `tomorrow`（按渲染的键次序；用上文的写法在内存里算过）：

| 观察时刻 | `duty` | `tomorrow` |
| --- | --- | --- |
| 2026-09-30 21:00（星期三） | `{"day": "工作日", "hours": "09:00–18:00", "place": "公司", "breaks": ["12:00–13:00"], "note": null, "status": "已下班"}` | `{"date": "2026-10-01", "weekday": "星期四", "day": "休息日"}` |
| 2026-10-01 09:00（星期四） | `{"day": "休息日", "hours": null, "place": null, "breaks": [], "note": "平常星期四上班，今天按工作安排休息", "status": null}` | `{"date": "2026-10-02", "weekday": "星期五", "day": "工作日"}` |
| 2026-10-09 21:00（星期五） | `{"day": "工作日", "hours": "09:00–18:00", "place": "公司", "breaks": ["12:00–13:00"], "note": null, "status": "已下班"}` | `{"date": "2026-10-10", "weekday": "星期六", "day": "工作日"}` |
| 2026-10-10 00:00（星期六） | `{"day": "工作日", "hours": "09:00–18:00", "place": "公司", "breaks": ["12:00–13:00"], "note": "平常星期六休息，今天按工作安排上班", "status": "未到上班时间"}` | `{"date": "2026-10-11", "weekday": "星期日", "day": "休息日"}` |

这份人设只写了两个例外，所以 10 月 2 日照每周声明是工作日、`note` 为 `null`；真实数据集的假期每天都有例外。
9 月 30 日 23:59:59 观察，明天仍是 10 月 1 日；10 月 1 日 00:00:00 观察，明天就是 10 月 2 日。

10 月 10 日零点的零点计划，用户消息（JSON 按键名排序）里与本章有关的两项：

```json
{"上班安排": {"breaks": ["12:00–13:00"], "day": "工作日", "hours": "09:00–18:00", "note": "平常星期六休息，今天按工作安排上班", "place": "公司"},
 "生成时刻": "10月10日 00:00（星期六）"}
```

模板写明 `day` 是唯一依据、周末也照 `hours` 与 `place` 上班，「生成时刻」里的「星期六」与说明里的「平常星期六休息」不再互相打架。

### 算例：样例环境

样例环境的人物星期一到星期五 09:00–18:00、12:00–13:00 休息，没有例外；运行从 2026-01-05（星期一）08:00 开始（`tests/fixtures/dataset-environment/persona.toml:60-87`、`config.toml`）。
用例用 `engine_factory(mutate=...)` 在副本上改人设，不改样例环境本身：

```python
def holiday_monday(persona, config):
    """人设：2026-01-05（星期一）按日期例外休息。"""
    persona["work_schedule"]["exceptions"] = {"2026-01-05": []}
```

运行开始那一刻（经 `settle_day_plans` 与 `observe_only`，用上文的写法在真实引擎上跑过）：

| 读到的地方 | 内容 |
| --- | --- |
| 零点计划请求的用户消息里的「上班安排」 | `{"breaks": [], "day": "休息日", "hours": null, "note": "平常星期一上班，今天按工作安排休息", "place": null}`；「生成时刻」是「1月5日 08:00（星期一）」 |
| 环境层 `duty` | `{"day": "休息日", "hours": null, "place": null, "breaks": [], "note": "平常星期一上班，今天按工作安排休息", "status": null}` |
| 环境层 `tomorrow` | `{"date": "2026-01-06", "weekday": "星期二", "day": "工作日"}` |

另外几个副本（同样跑过）：

| 副本 | `duty` 与计划输入 | `tomorrow` |
| --- | --- | --- |
| 原样 | 工作日 09:00–18:00、`note` 为 `null`、`status` 为「未到上班时间」 | `{"date": "2026-01-06", "weekday": "星期二", "day": "工作日"}` |
| 去掉 `weekly."0"`，例外 `"2026-01-05"` 写一个 09:00–18:00、12:00–13:00 休息的班次 | 工作日，`note` 为「平常星期一休息，今天按工作安排上班」，`status` 为「未到上班时间」 | 同上 |
| 例外 `"2026-01-05"` 写 10:00–17:00、不带休息 | 工作日，`hours` 为「10:00–17:00」，`breaks` 为空，`note` 为 `null` | 同上 |
| 例外 `"2026-01-06" = []` | 今天工作日，`note` 为 `null` | `{"date": "2026-01-06", "weekday": "星期二", "day": "休息日"}` |
| 去掉整个 `work_schedule` | `duty` 为 `null`，计划输入「上班安排」为 `null` | `{"date": "2026-01-06", "weekday": "星期二", "day": null}` |

### 边界情形

- 零点：观察时刻恰在本地零点时，`duty` 已是新的一天，`tomorrow` 是再下一天。
- 时区：本地日按运行配置的时区算，与 UTC 的日期无关。例如 UTC 2026-09-30 16:30 是上海的 10 月 1 日 00:30，明天是 10 月 2 日。
- 夏令时：加一天是在本地日期上加，不在时刻上加 86,400 秒。
- 运行的最后一天：`tomorrow` 照样写下一天，哪怕运行在那之前截止；它只是日历与人设上的事实。
- 例外日期写得与 `weekly` 同为上班、只差时段，或同为休息：没有说明，当天的时段照 `hours` 与 `breaks` 写。

### 明确不做

- 不加「能做的事」输入，模板的输入清单不改（所有者答复）。
- 不给 `tomorrow` 加时段、地点或说明，也不把明天放进零点计划的输入。
- 不从运行配置的 `calendar` 或别的来源推断上不上班，哪天上班只看 `weekly` 与 `exceptions`；不新增加载时的日历核对。
- 例外与 `weekly` 同为上班、只差时段或休息时段时不写说明；不为调换工作地点设说法（不会发生）。
- 不删「生成时刻」里的星期几：它仍是事实，模板和说明告诉模型以 `day` 为准。
- 个人事务不改代码，不加「打算」类事务，不放宽商品名的解析。
- 提案说「改过的计划和它的原因会进第二天的计划」：`revise_day_plan` 没有原因字段（`models.py:816-826`），第二天读到的只有前一天最后一份计划的意图与目标（`day_plan_inputs.py:254-275`），本章不加原因字段。
- `day_arrangement` 与 `duty` 那个不用的 `places` 参数不在本章处理。

### 本章定下的细节

| 事项 | 定下的做法 | 理由 |
| --- | --- | --- |
| 模板里那一句的源码排版 | 新的一半与旧的一句之间用行尾反斜杠续行；模板文字里没有换行 | 模板里是一整句；合在一行源码有 130 字符，超过风格门的 120。续行让模板文字与上文定稿逐字相同，旧的一行在源码里原样保留；模板加一个换行则会让文字与定稿不同 |
| `note` 的判定写法 | 比 `weekly` 这个星期几有没有班次与 `shift_on` 有没有班次，不另查 `exceptions` | 没有例外时两者必然相同，结果与上文的规则一致，少一个分支 |
| `note` 的实现位置 | `work_schedule._exception_note`，私有，由 `day_arrangement` 调用 | 两个读者都经 `day_arrangement` 取字典，说明只算一处 |
| `tomorrow` 的函数与参数 | `work_schedule.tomorrow(schedule, now, timezone_name, places)`，与 `duty` 同一组参数，`day` 经 `day_arrangement` 取；`environment_layer._tomorrow` 在函数内导入，与 `_duty` 同一写法 | 明天上不上班与今天走同一个函数；`places` 只为照 `day_arrangement` 的签名传下去；函数内导入沿用 `_duty` 已登记的做法，新的一条照样登记 |
| `WEEKDAY_NAMES` 的唯一归宿 | 登记进 `tests/unit/engineering/test_single_source.py` 的 `OWNERS`：`'life/work_schedule.py': ('WEEKDAY_NAMES',)` | 搬家的目的就是一张表；别处再定义同名常量时由单一归宿检查拒绝 |
| 模块说明里的属性名 | `workplace_location_id` 改为 `workplace` | 除日历核对那半句之外，同一段说明里的属性名也与代码不符（`WorkSchedule` 的字段是 `workplace`，`work_schedule.py:73` 读的也是它） |
| 查看器上下文页对环境层的说明 | `tools/run-viewer-context.js` 环境层的 `fill` 改为「观察时刻、身体感受文字、所在地点（停留或路程、地点类型、固定规则）、今天的上班安排（没有工作安排为 null）与明天的日期、星期和上不上班。」；`steps` 在「上班安排」之后加两条：`['例外说明', '日期例外改了这一天上不上班时，duty 的 note 写一句说明，例如「平常星期一上班，今天按工作安排休息」。']` 与 `['明天', 'tomorrow 写下一个本地日的日期、星期和那天是工作日还是休息日；没有工作安排时 day 为 null。']`；原有「上班安排」一条不改 | 查看器向读者讲每一层有什么，不改就漏了两项；原有那一条被 Node 用例逐字钉着 |
| 人设副本的写法 | 用例用到的人设变更函数放进新的帮助模块 `tests/life/work_arrangement_inputs.py`（`holiday_monday`、`makeup_monday`、`shorter_monday`、`rest_tuesday`、`no_work_schedule`），各带中文说明 | 夹具副本由 `tests/life/` 的帮助函数构建；单开一个模块，不与另外两条轨道共用的帮助文件冲突 |
| 个人事务只记商品的用例 | 新写一条经真实引擎的用例证明今天的行为，不改生产代码 | 每条规则都要有经生产入口的行为用例；检索 `tests/` 没有断言这条拒绝的用例 |

## 接口与文件指引

| 文件 | 改什么 |
| --- | --- |
| `src/worldmock/life/work_schedule.py` | 新增公开常量 `WEEKDAY_NAMES`、公开函数 `tomorrow(schedule, now, timezone_name, places) -> dict`、私有函数 `_exception_note(schedule, day) -> str \| None`；`day_arrangement` 两种日子都带 `note`；模块说明与 `day_arrangement` 的说明更正 |
| `src/worldmock/life/day_plan_inputs.py` | 删 `_WEEKDAYS`；「生成时刻」读 `work_schedule.WEEKDAY_NAMES`；「上班安排」不另改，自动带上 `note` |
| `src/worldmock/life/day_planning.py` | `PLAN_DAY_PROMPT` 照上文改；`:37` 的注释改引《人物》「零点计划」 |
| `src/worldmock/life/environment_layer.py` | `environment_layer` 在 `duty` 之后加 `"tomorrow": _tomorrow(context)`；新增私有函数 `_tomorrow(context) -> dict`；模块说明（`:1`）改为「环境层只显示时钟、四项体感、真实位置、天气、今天的工作安排与明天的日期、星期和上不上班。」 |
| `src/worldmock/life/prompt_schemas.py` | `_DUTY` 加 `note` 并改上方注释；新增 `_TOMORROW`；`ENVIRONMENT_LAYER_SCHEMA` 加 `tomorrow`；从 `work_schedule` 多导入 `WEEKDAY_NAMES` |
| `tools/run-viewer-context.js` | 环境层的 `fill` 与两条 `steps`（「本章定下的细节」） |

实施归轨道 B（提示与计划）。同一轨道顺手把测试助手 `tests/life/work_schedule_fixtures.py:1` 说明里不存在的锚点 `06-agent.md#agent-工作安排声明` 改指 `06-agent.md#agent-环境层-duty`，
把 `tests/integration/evaluation/test_errand_metrics.py:362` 与 `tests/unit/footprints/test_quality.py:692` 说明里不存在的锚点 `04-simulation-engine.md#simulation-engine-提醒时刻` 改指 `04-simulation-engine.md#simulation-engine-唤醒-日计划与个人事务`；只改说明，不改断言。

直接删除，不留兼容：`day_plan_inputs._WEEKDAYS`；四键的上班安排与没有 `tomorrow` 的环境层，不留按旧形状读写的分支。旧运行的观察事件没有这两项，旧运行按提示指纹被拒，不能恢复或重放。

夹具：样例环境 `tests/fixtures/dataset-environment/` 不改（只有[「版本、身份与基线」](08-versions.md)一章的版本提交会碰它的清单）；
例外、去掉的班次、去掉的工作安排都由 `tests/life/work_arrangement_inputs.py` 的人设变更函数经 `engine_factory(mutate=...)` 写在副本上。
观察事件带环境层，录制样例与两份基线会因 `note: null` 与 `tomorrow` 改变，只在收尾步重录、重冻一次。

约束：

- 每个函数不超过 50 行、5 个参数（`tomorrow` 四个），每行不超过 120 字符，文件不超过 2000 行（今天 `work_schedule.py` 90 行、`prompt_schemas.py` 307 行、`environment_layer.py` 39 行）；每个函数（含私有的）有带中文的 `@brief` 说明。
- `tomorrow` 是公开函数，覆盖率门要求函数体每一行都被执行，`schedule` 为 `None` 的情形由用例走到。
- `work_schedule.py` 只依赖标准库（`date`、`datetime`、`timedelta`、`ZoneInfo` 已经导入）。
- 新的函数内导入只有 `environment_layer._tomorrow` 一处，登记进 `tests/unit/engineering/test_import_graph.py` 的 `DELAYED_IMPORTS`：`('worldmock.life.environment_layer', '_tomorrow', 'worldmock.life.work_schedule'): '按执行职责延迟加载，保持输入纯加载边界'`。
- 下文两条源码变异的原文行在实现里要逐字如下，并且在各自的符号里只出现一次：`_exception_note` 里的 `if usual == (shift_on(schedule, day) is not None):`；`tomorrow` 里的 `day = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date() + timedelta(days=1)`。
- `prompt_schemas.py` 里已登记的变异原文 `"query": _QUERY,`（`_RESULT_DETAILS`）不动；本章新增的杀手用例不改名。
- `_DUTY`、`_TOMORROW` 进七层 Schema，`prompt_sha256` 改变；模板只改代码指纹；工具指纹不变。
- 设计文档里的家（《人物》「环境层」小节，锚点 `agent-环境层-duty`，与「零点计划」小节；《配置》「人物声明」的说明句与删掉的日历核对句；《模拟引擎》个人事务只记商品）见[「设计文档与手册」](07-design-docs.md)一章；本章不改设计文档。

## 测试要求

行为用例经真实引擎：`engine_factory(mutate=...)` 建副本引擎；零点计划经生产入口 `runtime.settle_pending_day_plans`（`tests/life/conftest.py` 的 `settle_day_plans` 加 `StubPlanDayClient`，读桩记下的那次请求）；
环境层读 `observe_only` 的观察，以及 `store.read_agent_events("observation", 1)` 落库的观察；个人事务经 `act` 提交真实的 `conclude_activity`。新的整合用例都放在新文件 `tests/integration/life/test_day_plan_arrangement.py`。

### 规则与用例

| 规则 | 层 | 测试 | 验收行为 |
| --- | --- | --- | --- |
| 模板写明 `day` 是唯一依据 | unit | `tests/unit/life/test_planning_and_errand_boundaries.py::test_plan_day_template_takes_the_day_kind_from_the_arrangement`（新） | `PLAN_DAY_PROMPT` 含决定表那一整句；它是模板的最后一行；「工作安排是固定世界事实」只出现一次；`prepare_plan_day({}, "test-model")["messages"][0]["content"]` 等于 `PLAN_DAY_PROMPT` |
| 真实的零点计划请求带这份模板与带说明的上班安排；环境层带说明与明天 | integration | `tests/integration/life/test_day_plan_arrangement.py::test_a_holiday_exception_note_reaches_the_plan_and_the_environment_layer`（新） | 副本 `holiday_monday`：桩记下的请求 `messages[0]["content"]` 等于 `PLAN_DAY_PROMPT`；用户消息里「上班安排」恰为算例的五键字典，「生成时刻」为「1月5日 08:00（星期一）」；观察的环境层键次序恰为七键；`duty` 恰为算例的六键字典且键次序如表；`tomorrow` 恰为 `{"date": "2026-01-06", "weekday": "星期二", "day": "工作日"}`；整层通过 `validate_layer`；落库的这次观察里 `duty`、`tomorrow` 与返回的相同 |
| 调休上班的说明 | integration | 同文件 `::test_a_make_up_workday_note_reaches_the_plan_and_the_environment_layer`（新） | 副本 `makeup_monday`：计划输入与 `duty` 的 `note` 都是「平常星期一休息，今天按工作安排上班」，`duty` 的 `status` 为「未到上班时间」 |
| 没有改上不上班的日子没有说明 | integration | 同文件 `::test_no_note_without_a_change_of_work_or_rest`（新，两个参数：样例环境原样；`shorter_monday`） | 计划输入与 `duty` 的 `note` 都是 `null`；第二个参数的 `hours` 为「10:00–17:00」、`breaks` 为空 |
| 说明的判定（全部情形）与星期名 | unit | `tests/unit/life/test_work_schedule_boundaries.py::test_note_is_written_only_when_an_exception_changes_work_or_rest`（新，参数化） | `weekly` 只声明星期一、星期三（09:00–18:00、12:00–13:00 休息），例外 01-05 空数组、01-06 班次、01-07 10:00–17:00、01-08 空数组、01-11 班次、01-14 与 `weekly` 相同的班次：依次是「平常星期一上班，今天按工作安排休息」「平常星期二休息，今天按工作安排上班」、`null`、`null`、「平常星期日休息，今天按工作安排上班」、`null`；没有例外的 01-12、01-13 是 `null` |
| 键次序 | unit | 同文件 `::test_arrangement_keys_end_with_the_note_and_duty_appends_status`（新） | `list(day_arrangement(...))` 为 `day`、`hours`、`place`、`breaks`、`note`（工作日、休息日各一次）；`list(duty(...))` 再加 `status` |
| 国庆算例 | unit | 同文件 `::test_holiday_and_make_up_day_read_as_the_worked_example`（新，参数化） | 用算例的工作安排，2026-09-30 21:00、10-01 09:00、10-09 21:00、10-10 00:00 四个时刻，`duty` 与 `tomorrow` 恰为算例表里的字典与键次序；`day_arrangement(..., date(2026, 10, 10), {})` 按键名排序后的 JSON 恰为算例的「上班安排」 |
| 明天是下一个本地日，带星期名与上不上班 | unit | 同文件 `::test_tomorrow_is_the_next_local_date_with_its_weekday_and_day_kind`（新，参数化） | 算例的工作安排：2026-09-30 23:59:59 → 10-01、星期四、休息日；10-01 00:00:00 → 10-02、星期五、工作日；UTC 2026-09-30 16:30（本地 10-01 00:30）→ 10-02；`schedule` 为 `None` → 日期、星期照写，`day` 为 `None`；`WEEKDAY_NAMES` 恰为七个名称 |
| 环境层的明天跟着下一天的安排 | integration | `tests/integration/life/test_day_plan_arrangement.py::test_tomorrow_reads_the_arrangement_of_the_next_local_date`（新） | 副本 `rest_tuesday`：今天 `duty` 是工作日、`note` 为 `null`；`tomorrow` 恰为 `{"date": "2026-01-06", "weekday": "星期二", "day": "休息日"}`；落库的这次观察的 `tomorrow` 与返回的相同 |
| 没有工作安排时明天也在，`day` 为 `null` | integration | 同文件 `::test_tomorrow_has_no_day_without_a_work_schedule`（新） | 副本 `no_work_schedule`：`duty` 为 `null`，`tomorrow` 为 `{"date": "2026-01-06", "weekday": "星期二", "day": null}`，计划输入「上班安排」为 `null`，环境层通过 `validate_layer` |
| `note` 与 `tomorrow` 是封闭 Schema 里的必填键 | unit | `tests/unit/life/test_environment_layer_schema.py::test_duty_note_and_tomorrow_are_required_and_closed`（新） | 对一份合格的环境层逐项改坏：缺 `note`、`note` 为空串、缺 `tomorrow`、`tomorrow` 多一个键、缺 `day`、`weekday` 写「周一」、`date` 带时刻、`day` 写「调休」，各自被 `validate_layer` 拒绝；`note` 为文字或 `null`、`day` 为 `null`、`duty` 为 `null` 都通过（全部情形在基线代码加上本章 Schema 后核过） |
| 明天在同一秒里不变，不进续接的 `changes` | integration | `tests/integration/runtime/test_same_second_continuation.py::test_duty_is_constant_within_a_same_second_chain_and_never_enters_changes`（改） | 链首环境层的 `tomorrow` 为 `{"date": "2026-01-06", "weekday": "星期二", "day": "工作日"}`；两次落库观察的 `tomorrow` 与它相同；续接的 tool 消息不含 `tomorrow` |
| 个人事务只记商品 | integration | `tests/integration/life/test_day_plan_arrangement.py::test_an_errand_without_goods_is_refused_and_a_product_errand_is_kept`（新） | 做「测试休息」到待收尾，收尾时新增 `item` 为「给母亲打个电话」：结果恰如上文的表，活动仍待收尾，个人事务为空；再收尾、新增 `item` 为「纸巾」：受理，登记一条 `product_id` 为 `tissue`、`quantity` 为 1 的事务 |
| 查看器上下文页讲明天与例外说明 | system | `tests/system/viewer/test_run_viewer.py::test_run_viewer_recorded_names`（改：它跑的 Node 用例 `run_viewer.test.cjs` 里规则列表加两项） | 真实页面里选环境层，能查到「日期例外改了这一天上不上班时，duty 的 note 写一句说明」与「tomorrow 写下一个本地日的日期、星期和那天是工作日还是休息日」 |

### 钉住今天行为、要改的现有用例

| 用例 | 钉的旧行为 | 处理 |
| --- | --- | --- |
| `tests/unit/life/test_work_schedule_boundaries.py::test_dates_override_weekly_shift_and_absent_declaration_has_no_duty`（`:22-23`） | 休息日的安排恰为四键 | 期望值加 `"note": "平常星期一上班，今天按工作安排休息"`（那份安排 `weekly` 声明了星期一，例外 2026-01-05 是空数组） |
| `tests/integration/runtime/test_same_second_continuation.py::test_duty_is_constant_within_a_same_second_chain_and_never_enters_changes`（`:257-273`） | 只比 `duty` | 加上表那一行的三处断言；说明补「明天也一样」 |
| `tests/system/viewer/run_viewer.test.cjs:187-191` | 环境层只查「上班安排」一句 | 规则列表加两项（上表） |
| `tests/fixtures/viewer-context.json` 与两份基线 | 四键 `duty`，没有 `tomorrow` | 不手改；收尾步重录、重冻，见[「版本、身份与基线」](08-versions.md) |

检索 `tests/` 没有别的逐键比对环境层或上班安排的用例：`tests/integration/life/test_planning_and_errand_evidence.py:111` 只读 `place`，`test_same_second_continuation.py` 其余断言只读单个键，
`tests/unit/life/test_narration.py:111` 用的是手写的层。

### 源码变异

本章登记两条，各取改坏中心规则的那一种，登记进 `tests/dataset_acceptance.py` 轨道 B 的那一段。杀手都直接导入 `worldmock.life.work_schedule`（unit），或在主进程里经 `engine_factory` 的真实引擎执行它（integration）。

| 定下的事项 | 把规则改坏的方式（原句 → 替换） | 被哪条测试杀死 | 层 |
| --- | --- | --- | --- |
| 说明只在例外改了上不上班时写（`arrangement_note_written_without_a_change`，`worldmock.life.work_schedule::_exception_note`） | `if usual == (shift_on(schedule, day) is not None):` → `if False:`（每一天都写说明，没有例外的日子也写） | `tests/unit/life/test_work_schedule_boundaries.py::test_note_is_written_only_when_an_exception_changes_work_or_rest`；`tests/integration/life/test_day_plan_arrangement.py::test_no_note_without_a_change_of_work_or_rest` | unit、integration |
| 明天是观察本地日的下一天（`tomorrow_reads_today`，`worldmock.life.work_schedule::tomorrow`） | `day = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date() + timedelta(days=1)` → `day = datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date()` | `tests/unit/life/test_work_schedule_boundaries.py::test_tomorrow_is_the_next_local_date_with_its_weekday_and_day_kind`；`tests/integration/life/test_day_plan_arrangement.py::test_tomorrow_reads_the_arrangement_of_the_next_local_date` | unit、integration |

模板换句、`WEEKDAY_NAMES` 的搬家和 Schema 不登记变异：模板由逐字断言守着，搬家由单一归宿检查守着，Schema 由上表的 Schema 用例与整合用例里的 `validate_layer` 守着；
个人事务只记商品没有新代码，由行为用例守着。
