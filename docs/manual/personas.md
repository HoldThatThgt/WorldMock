# 人物画像

人物画像描述“这个人通常怎样生活、在意什么、遇到事情会怎样反应”。
正式运行从生活场景的 `agents` 数组读取人物，主要正文是 `background_story`。
可以用 UTF-8 文本文件单独起草，但场景没有 `persona_file` 或画像文件引用字段。

[返回手册](README.md) · [完整白领场景](../../scenarios/white-collar-12-weeks.json)

## 写出一个能够生活的人

```mermaid
flowchart LR
    STORY[自然语言背景故事] --> COMPILE[模型抽取人物卡片]
    COMPILE --> CARD[身份、关系、习惯与口味]
    CARD --> DECIDE[理解处境并自主选择行动]
    CONFIG[场景中的余额、睡眠参数和联系人] --> WORLD[实际身体、账户与社交条件]
    WORLD --> DECIDE
```

先写具体生活事实，再写性格倾向。下面的内容可写成连贯段落，不要求固定标题或 JSON 子字段。

| 内容 | 写法示例 |
| --- | --- |
| 身份与居住 | 32 岁产品经理，独居在张江 |
| 重要关系 | 经常与母亲通电话，和老友沈昊通过微信联系 |
| 当前处境 | 本周准备产品评审，担心加班影响睡眠；会编进卡片，但不进入人物提示 |
| 作息 | 通常七点起床、八点出门，中午十二点到一点吃饭，十一点左右睡觉；必须准点的事写明时刻，例如「工作日九点半的晨会不能迟到」 |
| 睡眠需要 | 通常睡七个半小时才能恢复；自由日的睡眠时间另行说明 |
| 触发与反应 | 下午饿了会买牛奶和坚果；工作受挫时先散步 |
| 口味与消费 | 偏爱科幻、悬疑和科技播客，愿意为舒适和品质付费 |
| 应对压力 | 先深呼吸或和朋友聊聊，再处理问题 |

下面是完整画像正文，可保存为 UTF-8 文件后用于编译预览，也可整体放入 `background_story`：

```text
陈曦，32岁，互联网公司的产品经理，独居在上海张江。本周正在准备产品评审，
希望方案得到认可，也担心工作挤占休息。与母亲关系亲近，周末经常打电话；
老友沈昊常通过微信联系，偶尔相约吃饭。
工作日通常七点左右起床，八点左右出门，中午十二点到一点吃午饭，
晚上七点到八点吃晚饭，晚上十点半到十一点半准备就寝，需要七个半小时睡眠。
自由日大约晚上十一点半睡、早上七点起，睡眠中点为凌晨三点十五分。
通勤时喜欢听科技播客，午休偶尔看短剧，晚上偏爱科幻和悬疑电影。
下午饿了会吃牛奶和坚果。消费理性，但愿意为舒适和品质付费。
工作受挫时通常先散步或深呼吸，平静后再处理问题。
```

正文里的当下处境（例如「本周正在准备产品评审」）会编进卡片，但不进入人物提示。需要人物在运行中知道的安排，
写进作息或习惯，或写成场景中的工作任务与消息。

将正文写成生活倾向和习惯窗口。人物的具体行动由模型根据当前情况选择；背景故事不能强制每天执行某套动作。
兴趣对应的素材需要在世界中存在，编译成功也不保证其世界覆盖率合格。

## 在场景中填写人物

下面是一个完整 `agents` 数组元素，替换既有场景中的一个人物即可；它不是完整场景文件。
例子沿用白领场景中的 `agent_id`、住处和任务技能，因此原有任务仍能指向这个人物。

```json
{
  "agent_id": 1,
  "name": "陈曦",
  "background_story": "陈曦，32岁，互联网公司的产品经理，独居在上海张江。本周准备产品评审，担心加班影响休息。与母亲关系亲近，周末经常打电话；老友沈昊常通过微信联系，偶尔约饭。通常七点起床、八点出门，十二点到一点吃午饭，十九点到二十点吃晚饭，二十二点半到二十三点半准备就寝，需要七个半小时睡眠。自由日睡眠中点为凌晨三点十五分。通勤爱听科技播客，午休偶尔看短剧，喜欢科幻和悬疑电影。下午饿了会吃牛奶和坚果。消费理性，愿意为品质付费，压力大时先散步或深呼吸。",
  "trait_source_kind": "experiment_assumption",
  "home_location_id": "home",
  "initial_balance_fen": 800000,
  "skill_levels": {"meeting_analysis": 0.8},
  "qualifications": [],
  "interaction_menu": null,
  "chronotype_midsleep": 3.25,
  "sleep_need_hours": 7.5,
  "lunch_window": [12.0, 13.0],
  "dinner_window": [19.0, 20.0],
  "bedtime_window": [22.5, 23.5],
  "contacts": [
    {
      "contact_id": "contact-chenxi-oldfriend",
      "salutation": "沈昊",
      "relation": "老友",
      "closeness": 26,
      "channels": ["wechat"],
      "reply_policy": "model"
    },
    {
      "contact_id": "contact-chenxi-mother",
      "salutation": "母亲",
      "relation": "家人",
      "closeness": 28,
      "channels": ["phone"],
      "reply_policy": "model"
    }
  ],
  "initial_inventory": {}
}
```

上例省略可选的 `motivation`，因此不启用该人物的动机评分档案。若要保留模板中的动机配置，
在原有人物上修改字段即可；若要调整评分参数，见[规则配置](rules.md)。

| 字段 | 是否必填 | 如何填写 |
| --- | --- | --- |
| `agent_id`、`name` | 是 | 正整数且人物间唯一；显示姓名非空 |
| `background_story` | 格式允许省略；自定义画像时填写 | 去掉首尾空白后至少 100 个字符；这是原文长度要求 |
| `trait_source_kind` | 是 | 固定 `experiment_assumption`，表示实验设定 |
| `home_location_id` | 是 | 已被场景选中的地点标识 |
| `initial_balance_fen` | 是 | 非负整数分；`800000` 表示 8000 元 |
| `skill_levels` | 是 | 技能名到 0–1 数值的映射；须覆盖分配任务所需技能 |
| `qualifications` | 是 | 资格字符串列表，无资格填 `[]`；不会从职业描述自动推断 |
| `interaction_menu` | 是 | `null` 表示沿用地点菜单；限制写法见[生活场景](scenarios.md#限制人物可以做什么) |
| `chronotype_midsleep` | 否 | 自由日睡眠中点，午夜起的小时数，范围 `[0,24)`，默认 `4.0` |
| `sleep_need_hours` | 否 | 睡眠需要，7–9 小时，默认 `7.5` |
| `lunch_window`、`dinner_window` | 否 | `[开始小时,结束小时]`，范围 0–24，开始小于结束；两餐窗口不重叠；窗口起止是食欲跳变的预测时刻，饥饿跨档才给决策 |
| `bedtime_window` | 否 | 开始小于 24，结束可到 30，跨度不超过 12 小时；声明晚餐时就寝开始不得早于晚餐结束 |
| `contacts` | 否 | 联系人列表，默认 `[]`；同一人物内 `contact_id` 唯一 |
| `initial_inventory` | 否 | 地点到商品及正数数量的映射，默认 `{}`；数量单位来自商品定义 |
| `lifestyle_activities` | 否 | `null` 或省略启用全部生活活动；非空列表只启用列出的活动标识，不能用空列表禁用全部 |
| `health_habits`、`social_style` | 否 | 可保存辅助说明；画像编译只读取正文，重要信息仍需写入 `background_story` |
| `motivation` | 否 | 独立的结构化动机配置，见[规则配置](rules.md)；不是画像编译结果的粘贴位置 |

`chronotype_midsleep` 不是起床时间，例如晚上 23:30 至早上 07:00 的中点为 `3.25`。
作息用小数小时：`7.5` 是 07:30，`25.0` 是次日 01:00。画像编译会将时型和睡眠需要吸附到
0.25 小时格点，卡片的时间窗口吸附到 0.5 小时格点；建议正文和场景声明按此口径保持一致。
写跨午夜就寝窗口时使用 `[23.0,25.0]`，不要写成开始大于结束的 `[23.0,1.0]`。

起点困意按作息反推：惯常起床时刻 `t_wake = (chronotype_midsleep + sleep_need_hours / 2) mod 24`，
取场景起点之前最近的一次；惯常睡眠时段是惯常起床时刻之前的 `sleep_need_hours` 小时。
系统假定人物在惯常起床时刻睡满醒来，此后一直清醒到场景起点，据此求起点的睡眠压力；不读 `bedtime_window`，
也不预置任何活动。起点落在惯常睡眠时段内或接近它时，人物以困倦状态开始，去不去睡由人物自己决定。
例如时型 `4.0`、睡眠需要 `7.5` 的人物惯常 07:45 起床，场景从 00:00 开始时已连续清醒约 16 小时，首个观察的困意在「困」以上。

联系人 `closeness` 为 0–30；`reply_policy` 为 `none`、`template` 或 `model`。
`none` 不自动回复，`template` 使用模板回复，`model` 调用模型生成回复。
渠道沿用现有示例的 `wechat`、`phone` 等机器名称，并保持双方声明一致。
联系人的 `closeness` 与 `channels` 不进入人物提示：人设层里只来自场景联系人的关系项，亲密度为 `null`、渠道为 `[]`；
只有卡片中的关系带亲密度与渠道。
同世界的另一个自主人物应在 `agents` 里声明，联系人条目不会额外启动一个自主人物。

## 正文与实际世界条件要一致

正文被提取成卡片后供人物理解身份和偏好：运行时卡片的结构化字段与人物姓名、场景联系人一起渲染成人设层 JSON，
不含当下处境 `situation`；关系的看法与偏好由人物结束活动时写下的记忆原地更新。身体睡眠常量、饭点、余额、资格、库存和可联系的人，
则由场景的结构化字段提供；编译卡片不会自动覆盖这些场景字段。
例如故事写“每天需要九小时睡眠”，就应同时把 `sleep_need_hours` 改成 `9.0`。

当日目标由零点计划生成：运行开始时和每个场景本地零点，系统把背景原文、作息窗口以及刚过去这段时间的经历与收尾记录
交给模型，为人物写出当天的目标。正文写明时刻的准点之事（例如晨会、赶车），零点计划可以据此给目标设 `remind_time` 提醒；
提醒到点只触发一次，能叫醒睡着的人。系统不从文字推断时刻，提醒是否设置由零点计划的生成结果决定；
只写“早起”“按时上班”而不写几点，零点计划就没有可依据的时刻。

`profile compile` 输出的 `CompiledPersona` 包含 `card`、`big_five`、`world_dynamics`、溯源和警告。
当前新建运行使用其中的卡片和由口味计算的内容排序权重，不会把整份产物写回场景，
也不会自动把 `world_dynamics` 替换到现有身体或动机参数。可用的参数调整入口见[规则配置](rules.md)。
不要把输出 JSON 填入 `agents[].motivation`：两者是不同结构。

## 预览与校验

先从自己的场景导出实际正文，避免预览文件与运行输入不同：

```bash
uv run python - <<'PY'
import json
from pathlib import Path

scene = json.loads(Path("scenarios/my-persona-day.json").read_text(encoding="utf-8"))
Path("/tmp/my-persona.txt").write_text(scene["agents"][0]["background_story"], encoding="utf-8")
PY
uv run worldmock profile compile \
  --text /tmp/my-persona.txt --out /tmp/my-persona.compiled.json
```

编译默认独立抽取三次，会调用模型。查看产物的 `persona_card_text`、`card` 和 `warnings`：
有没有遗漏的关系、错误的时间、没有在正文中提供的事实。生成卡片最多 600 汉字，超出即报错，
不会截断原文。这里的 600 汉字限制针对生成卡片，不是要求原始故事最多 600 字。
运行时人物看到的不是 `persona_card_text` 原文，而是由 `card` 结构化字段渲染的人设层 JSON（去掉 `situation`，关系与偏好原地更新）。

需要进一步检查提取稳定性时运行：

```bash
uv run worldmock profile check --text /tmp/my-persona.txt
```

它执行三轮独立编译，每轮默认三次抽取，正常情况共九次模型请求，解析重试会增加调用量。
`--runs` 控制每轮抽取次数，不是整个检查的总请求次数。

最后核对人物兴趣和习惯能否在这个世界中实现：

```bash
uv run worldmock profile validate \
  --scenario scenarios/my-persona-day.json \
  --coverage-out /tmp/my-persona-coverage.json
```

这一步也会调用模型。可执行比例必须至少为 80%；失败时根据缺失项修改表达或补充世界素材，
素材编写见[世界素材](resources.md)。默认覆盖率报告会写在场景旁，示例用 `--coverage-out` 显式指定位置。

## 常见错误

| 现象 | 检查什么 |
| --- | --- |
| `background_story must contain at least 100 characters` | 正文太短，补充具体身份、关系、作息和习惯 |
| 卡片不包含另写的健康或社交说明 | 将这些信息写入正文；编译不会合并辅助字段 |
| 故事里有朋友，运行时却无法联系 | 补齐 `contacts` 和渠道；故事不会创建世界对象 |
| 改了故事中的余额或睡眠，实际数值没变化 | 同步修改对应结构化字段 |
| 编译一致性不足或卡片超过预算 | 缩小矛盾描述，减少重复关系和过多兴趣，再编译检查 |
| 旧字段被拒绝 | `occupation`、`routine_preference`、`consumption_preference`、`risk_preference` 和 `entertainment_preferences` 不能作为人物顶层配置；对应内容写入正文 |

详细限制见[人物卡片合同](../CONTRACTS.md#人格与卡片字段)、
[画像规格](../dev/SPEC-life-realism.md#画像编译)和[人物配置加载器](../../src/worldmock/life/scenario.py)。
