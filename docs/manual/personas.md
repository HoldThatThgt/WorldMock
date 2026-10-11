<a name="personas-编写-persona-toml"></a>
# 编写 persona.toml

环境根的 persona.toml 是一张表，声明唯一的人物。表里的每个字段都由你直接填，引擎照写的样子使用：
它不读 `background_story` 来猜年龄、作息或口味，不调用模型，也不替你补缺省值或改数字。
标了必填的字段缺一个，`worldmock validate` 就以 `PERSONA_INVALID` 拒绝，并指出是哪个字段。
字段的完整规则见[配置](../design/08-configuration.md#configuration-人物声明)。

## 一份完整的例子

```toml
schema_version = 2
agent_id = 1
name = "林悦"
background_story = "林悦在一家互联网公司做产品经理，和伴侣合租在公司附近。工作日朝九晚六，周末喜欢去公园慢跑。"
health_habits = "每周慢跑三次，晚饭后不再喝咖啡。"
social_style = "熟人面前话多，不主动约不熟的人。"
home_location = "家"
initial_location = "家"
initial_balance_fen = 200000
initial_relaxation_need = 0.2
initial_social_need = 0.2
social_need_growth_per_hour = 0.06
chronotype_midsleep = 3.75
sleep_need_hours = 7.5
wake_window = [7.0, 7.5]
leave_home_window = [8.0, 8.5]
lunch_window = [12.0, 13.0]
leave_work_window = [18.0, 18.5]
arrive_home_window = [18.25, 18.75]
dinner_window = [19.0, 20.0]
bedtime_window = [23.5, 24.5]
coping = "拼高达模型"

[identity]
age = 32
occupation = "产品经理"
living_arrangement = "与伴侣合租两居室"

[situation]
busy_this_week = "新版上线收尾"
cares_about = "父母的体检结果"

[[relationships]]
name_or_appellation = "母亲"
role = "家人"
intimacy = 25
channels = ["电话", "微信"]

[[relationships]]
name_or_appellation = "老周"
role = "老友"
intimacy = 18
channels = ["微信", "见面"]

[[habits]]
trigger = "下班到家"
response = "公园慢跑半小时"
time_window = [19.0, 20.0]

[tastes]
content_genres = ["悬疑", "纪录片"]
food_preferences = ["川菜", "面食"]
media = ["长视频", "短视频"]

[body]
mass_kg = 55.0
initial_energy_kcal = 1800.0
initial_sleep_debt_hours = 0.0

[work_schedule]
workplace = "公司"

[work_schedule.weekly."0"]
starts = 540
ends = 1080
breaks = [[720, 780]]
```

TOML 要求顶层的键写在所有表之前，所以 `coping` 和各个时段放在 `[identity]` 上面。
文件用 UTF-8、不带 BOM、换行只用 LF。

## 每一栏填什么

| 栏 | 必填 | 怎么填 | 谁在用 |
| --- | --- | --- | --- |
| `schema_version` | 是 | 写 `2` | 加载 |
| `agent_id`、`name` | 是 | 正整数、姓名 | 全程 |
| `background_story` | 是 | 人物的来历和日常，长短不限 | 每天零点的计划原样读它 |
| `health_habits`、`social_style` | 否 | 一两句话 | 每天零点的计划 |
| `home_location`、`initial_location` | 是 | 环境里地点的完整显示名；住处要标了能住、能睡 | 世界初始化 |
| `initial_balance_fen` | 是 | 整数分 | 资金 |
| `initial_relaxation_need`、`initial_social_need` | 是 | 0 到 1 | 身体初值 |
| `social_need_growth_per_hour` | 是 | 0.02 到 0.10；越大越容易想找人说话，0.06 是不内向也不外向 | 身体 |
| `chronotype_midsleep` | 是 | 不上班的日子里一觉的正中是几点，0 到 24 之间 | 困意 |
| `sleep_need_hours` | 是 | 7 到 9；写人物实际每晚睡多久 | 欠的觉 |
| `wake_window`、`leave_home_window` | 是 | 时段 | 人物每次决策看到的作息 |
| `lunch_window`、`dinner_window` | 是 | 时段 | 人物看到的作息、零点计划、饭点时更容易饿 |
| `bedtime_window` | 是 | 开始就寝的时段，不是整夜 | 人物看到的作息、零点计划 |
| `leave_work_window`、`arrive_home_window` | 否 | 时段；不写就是没有固定时刻 | 人物看到的作息、零点计划、评估里的下班与到家偏离 |
| `coping` | 是 | 压力时做什么，不超过 60 个字符 | 人物看到的自己 |
| `identity` | 是 | `age` 16 到 100，`occupation` 不超过 20 个字符，`living_arrangement` 不超过 30 个字符 | 人物看到的自己 |
| `situation` | 是 | `busy_this_week`、`cares_about` 各不超过 60 个字符 | 记忆巩固与就寝反思读的卡片文本 |
| `relationships` | 是 | 至多 4 个人，没有就写 `relationships = []` | 人物看到的关系，以及记忆里能写到的人 |
| `habits` | 是 | 至多 5 条，没有就写 `habits = []` | 人物看到的习惯 |
| `tastes` | 是 | 三个数组都要写，可以是空数组 | 人物看到的口味 |
| `body` | 是 | 体重、初始能量、初始欠的觉 | 身体 |
| `work_schedule` | 否 | 工作地点与每周班次 | 上班安排 |

住处与初始地点分别解析实际地点显示名，初始地点可以不同于住处。
不要添加旧的顶层 occupation、routine_preference、consumption_preference、risk_preference 或多人 agents 数组。

## 作息时间照实填

时段写成 `[起点, 终点]`，单位是自午夜起的小时，18.25 就是 18:15。照实填，不必对齐半小时或一刻钟。
每个时段只有一条要求：两个数，起点小于终点。人物每次决策看到的、零点计划里的、身体和评估用的都是你写的这个数，显示写到分钟。

越过午夜的时刻写成大于 24 的小时数：23:30 到次日 00:30 的就寝写 `[23.5, 24.5]`，人物看到「23点半到0点半」；
夜班人物凌晨一点半下班可以写 `leave_work_window = [25.5, 26.0]`，评估也按钟面读它：凌晨 01:40 离开工作地点算准时。
引擎不检查时段之间是否合理：就寝写在晚饭之前、午饭和晚饭重叠、一个时段跨十几个小时都能加载，人物看到的就是这样的作息。
这些时间合不合情理由你负责。

`sleep_need_hours` 不决定人物睡多久，只用来算欠的觉；每晚实际睡得比它短，欠的觉会逐日累加，
见[理解固定世界规则](rules.md)。下班、到家时段和 `work_schedule` 互不相干：不上班的人物也可以写，上班的人物也可以不写。

## 关系、习惯与口味

每个关系写姓名或称谓、角色、亲密度和联系渠道。亲密度是 0 到 30 的整数，人物看到的是四档：
22 以上「很亲近」，14 以上「较亲近」，7 以上「一般」，其余「疏远」。渠道写一到三个，用词由你定，
例如「电话」「微信」「见面」，也可以写「飞书」「邮件」；人物看到的就是你写的词，引擎不替你改写或去重。
每条习惯写什么情境下（`trigger`）做什么（`response`），以及大致在哪个时段。
口味的三个数组分别是爱看的内容题材、喜欢吃的东西和常用的媒介；写了媒介而环境里没有任何 app 绑定内容时，
运行清单会记一条 `CONTENT_EMPTY_WITH_MEDIA_TASTES` 警告，运行照常进行。

身份、当前处境、作息、习惯、口味、应对方式和关系会拼成一段卡片文本，这段文本不能超过 600 个汉字。
超出时 `worldmock validate` 以 `PERSONA_INVALID` 拒绝并写出实际的汉字数，引擎不会替你截断。
每一栏都写到字数上限、条数也写满时会超过这个数，口味数组里的词也没有单独的字数上限；被拒绝时把习惯、关系或口味写短一些。

## 体重决定每天吃几顿

`body.mass_kg` 决定人物的消耗：什么都不做每小时消耗 `mass_kg` 千卡，一天的基础消耗是 `24 × mass_kg` 千卡，
活动再按强度加上超出静坐的部分。欠觉不超过 8 小时时，一件食品实得 540 千卡，一件零食实得 180 千卡；
用一天的消耗除以它们，就能估出人物一天要吃几顿。55 公斤的人一日三餐基本持平，70 公斤的人三餐之外还要约三件零食。
能量池上限是 2400 千卡，饥饿阈值对所有体重相同；一次买两份时，收货后超过 2400 的部分作废。
体重接近 100 公斤时，静息一天就耗尽整个能量池。
