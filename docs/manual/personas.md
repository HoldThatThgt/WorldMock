<a name="personas-编写-persona-toml"></a>
# 编写 persona.toml

在环境根的 persona.toml 声明唯一人物。身份、至少一百字符的 background_story、住处、初始地点、
资金、身体质量、能量、睡眠不足、初始放松需要和初始社交需要是当前输入合同。
可声明工作安排、节律覆盖、健康习惯与社交风格散文；完整字段见[配置](../design/08-configuration.md)。
住处与初始地点分别解析实际地点显示名，初始地点可以不同于住处。
初始资金使用整数分；两项心理初值在零至一内，身体值使用有限数值。
不要添加旧 occupation、routine_preference、consumption_preference、risk_preference 或多人 agents 数组。

```powershell
python -m worldmock profile compile --persona D:/datasets/my-world/persona.toml --out .work/persona.json
python -m worldmock profile check --persona D:/datasets/my-world/persona.toml
```

画像命令同时读取同目录 config.toml 的模型配置与实际服务元数据。编译执行三次独立抽取并保存共识卡片，
节律的显式人设声明覆盖对应锚点；输入与模型预算不进入人物可见请求。
检查报告关注卡片和字段一致性，详情见[人物设计](../design/06-agent.md)。
卡片的结构版本升号后，以前编译好的卡片不能再用，要重新编译一次。

## 作息时间照实填

午饭、晚饭、下班、到家、就寝五个时段，加上睡眠中点 `chronotype_midsleep` 和每日睡眠需要 `sleep_need_hours`，
都写成自午夜起的小时数，18.25 就是 18:15。时间照实填，不必对齐半小时或一刻钟：声明什么，人物卡片里就存什么，
人设层、零点计划、身体和评估读到同一个值，显示写到分钟。

```toml
chronotype_midsleep = 4.1
sleep_need_hours = 7.6
lunch_window = [11.75, 12.75]
leave_work_window = [18.0, 18.5]
arrive_home_window = [18.25, 18.75]
dinner_window = [19.0, 20.0]
bedtime_window = [23.5, 24.5]
```

同时声明的时段要排得进同一天，否则 `worldmock validate` 以 `PERSONA_INVALID` 拒绝并原样给出原因。
就寝起点小于 6 时读作次日凌晨，所以每晚 01:00 睡的人物写 `bedtime_window = [1.0, 1.5]` 也可以同时声明晚饭。
这样的就寝时段终点不要大于 6（次日 06:00）：`[5.5, 6.0]` 可以，`[5.5, 7.0]` 能通过检查但写不进人物卡片。

| 同时声明了 | 必须满足 |
| --- | --- |
| 下班、到家 | 下班起点不晚于到家起点 |
| 晚饭、就寝 | 就寝起点不早于晚饭终点 |
| 午饭、晚饭 | 午饭终点不晚于晚饭起点 |
| 午饭、就寝 | 午饭起点不晚于就寝起点 |
| 下班、就寝 | 下班起点不晚于就寝起点 |
| 到家、就寝 | 到家起点不晚于就寝起点 |

建议七项都声明。没有声明的值由模型从 `background_story` 读出并取整，人设层和评估用它，零点计划不显示这一项，
身体对睡眠中点和睡眠需要用缺省值 4.0 与 7.5、对饭点没有加成，四处就可能不一致。
唯一的例外是声明的时段写不进人物卡片：它和模型读出的起床、出门等时间排不进同一天，
或者就寝起点小于 6 而终点大于 6。这时卡片保留模型读出的值，运行照常进行，
运行清单的 `input_warnings` 里有一条 `CARD_FIELD_CONFLICT`；人设层和评估用卡片里的值，零点计划和身体仍用声明值。
声明值改变了卡片值时记 `CARD_FIELD_OVERRIDDEN`，这只是告知。

## 体重决定每天吃几顿

`body.mass_kg` 决定人物的消耗：什么都不做每小时消耗 `mass_kg` 千卡，一天的基础消耗是 `24 × mass_kg` 千卡，
活动再按强度加上超出静坐的部分。欠觉不超过 8 小时时，一件食品实得 540 千卡，一件零食实得 180 千卡；
用一天的消耗除以它们，就能估出人物一天要吃几顿。55 公斤的人一日三餐基本持平，70 公斤的人三餐之外还要约三件零食。
能量池上限是 2400 千卡，饥饿阈值对所有体重相同；一次买两份时，收货后超过 2400 的部分作废。
体重接近 100 公斤时，静息一天就耗尽整个能量池。
