# 系统层措辞

> 本章属于[手机优先的空闲时间与空闲时的死路：开发规格](../SPEC-free-time-dead-ends.md)，读法和叫法见那一页。

## 目标与依据

人物每次决策读到的第一条消息是系统层：一个角色句和二十条规则（`src/worldmock/agents/life_prompt.py:21-88`），整次运行逐字节不变。
提案在真实运行里看到的长思考，有一部分出在这二十条没说或说错的事实上：

- 什么时候会再请人物决策，系统层只在讲睡眠的那一条（`life_prompt.py:59-62`）说过。人物不知道没有活动时时间不走，也不知道外卖送到会叫醒醒着的人，
  等外卖时反复猜环境要她做什么（提案「Long deliberation」：思考超过 8,000 token 的调用里 63% 出现 the environment expects 一类的话）。
  环境数据集合同要求把这两件事写进系统层（`docs/dev/environment-dataset-contract/SPEC-environment-dataset-contract.md:1088-1095`），
  实现那次提交 `8e7cdd60` 删掉了旧句（`8e7cdd60^:src/worldmock/agents/life_prompt.py:36-39`），没有写新句。
- 自己写下的看法会变成人设层的偏好和关系里的看法（`src/worldmock/life/memory_layer.py:119-136`），角色句又说「依据人设层选择下一步行动」（`life_prompt.py:23`），
  于是「在家休息一天最多两段」「做完就起身，不接着再开一段」这类自定的规矩像规则一样排除掉剩下的选项（提案「Own views and repeated choices」）。
- 今日计划里写了家里做不到的家务，人物几天里一直想办法完成它；`plan_day` 不能查询，计划里的事要到决策时才发现做不到（提案「The day plan」）。
- 「给母亲打个电话」被写进个人事务七次。个人事务的 `item` 是「要取得的商品名称」（`src/worldmock/life/models.py:960`），
  新增时必须解析到唯一一件商品（`src/worldmock/life/errands.py:384-401`），每次都被退回。
- 做晚饭的系列在一集还差几秒做完时被结束，两顿饭没吃上：进行中的活动在工具层只显示「已做…，还剩…」（`src/worldmock/life/tool_layer.py:189-197`），
  哪一集供一餐只写在被买菜锁隐藏的单元行上。
- 内容 app 改成一条接一条地放（[「内容 app 自动接续」](01-autoplay.md)），这件事要在系统层说一次。

本章落实提案「One system-layer wording pass」一表的七行，以及「Meals and waiting」「Own views and repeated choices」「The day plan」三节里给出的系统层句子。
与本章有关的所有者答复（2026-10-15）：独立内容放完接一条没看过的可推送内容，系统层只说「放完一条自动接下一条」，不讲怎么挑；
一次 app 使用随时可以暂停，「活动自然结束后先用 conclude_activity 收尾」一条本来就写着 `pause_activity` 可以暂停内容 app 的使用，不改；
计划时不给「能做的事」，只靠「作息与习惯照人设层过」一条新接的一句；买菜套件的说法不改，「商品实际收到时自动吃掉或用掉」一条不动。

规则仍是二十条；角色句、只读问答的系统层（`life_prompt.py:91-100`）和工具用途句（`life_prompt.py:103-115`）都不改。
提示指纹（`life_prompt.py:161-166`）绑定两份系统层与七层 Schema，所以 `prompt_sha256` 改变；工具指纹 `tool_schema_sha256` 不变。
生活决策的系统消息（`system_message(False)` 的文字）从 2,911 字增长到 3,377 字，按本章定稿在内存里算出；没有用例钉它的长度。

## 行为规格

### 改动的七条：定稿全文

下面每个代码块是一条规则改动之后的完整文字，逐字，包括全角标点和半角空格；块里没有换行。
写进源码时照现有写法拆成相邻的字符串字面量，每行不超过 120 字符，拆在哪里不影响文字。

「每次决策以世界请求结束」一条（`rules[2]`）：原文一字不动，在「提交即结束本次决策。」之后接上两句。

```text
每次决策以世界请求结束：要么提交一个控制请求（report_status、pause_activity、conclude_activity、revise_day_plan 之一），要么开始活动。开始活动时 start_main_activity 与 start_side_activity 各至多调用一次；要同时开始两项，就在同一次回复里各调用一次，两项一起核验，任何一项不成立两项都不开始。提交前可以查询记忆或回看任务，只读查询可以同批；世界请求不与只读查询同批，开始活动也不与控制请求同批，提交即结束本次决策。没有进行中的活动时时间不会往前走，同一时刻会接着请你决策；等外卖或等某个钟点时，此刻能做的事都可以做，包括用工具层里可用的内容 app。醒着时，手上的事做完、下单的东西送到或到站可取、提醒到点、app 推送、身体感觉变了、到了上下班时刻或写明的休息时段开始或结束，都会再请你决策；一件事做得久，中途隔一阵也会请一次；没做完的活动照常进行。
```

「在地点做哪件事由你点名」一条（`rules[6]`）整条换成：

```text
在地点做哪件事由你点名：系列只写系列名称，从第一集做起，做完一集自动接下一集。用内容 app 时看到什么由环境选择，打开后放完一条自动接下一条，直到你结束它；没有可接的内容时停下，等你收尾。app 里的内容标题不是可写的名称。功能 app 不由你选择。
```

「世界请求登记后由环境结算」一条（`rules[13]`）整条换成：

```text
世界请求登记后由环境结算。进度层结果和记忆层 settled 是核验事实；自己写的 summary、self_note、self_notes 与看法（人设层 preferences 和关系里的看法也是）只是自己的想法，不能改变身体、资金、内容进度或订单结果，环境也不按它们限制你此刻能做什么；想法变了，就照现在的想法选，收尾时写下新的看法。
```

「解饿只有两种办法」一条（`rules[15]`）：只把「系列的 note 写的是第几集供一餐或供一份零食」换成「系列的 note 或进行中活动的 state 写的是第几集供一餐或供一份零食」，其余一字不动。改后全文：

```text
解饿只有两种办法：一是收到买来的食品或零食，收到的那一刻吃掉；二是在地点做完一件工具层 note 写着「供一餐」或「供一份零食」的事，做完的那一刻算吃了，系列的 note 或进行中活动的 state 写的是第几集供一餐或供一份零食，做完那一集时算。中途用 conclude_activity 结束的不算，暂停后恢复做完的照算。note 没写这两种说法的事不解饿，名称像吃饭也一样。
```

「作息与习惯照人设层过」一条（`rules[16]`）：原文之后接一句。

```text
作息与习惯照人设层过，当天特别安排照今日计划；没有相应习惯时按身体感觉安排。大致钟点只是大概，不会自动提醒。工作安排由环境给出，今日计划不能改变班次。今日计划只是自己的打算，环境不判定做没做完；发现计划里的事在工具层和拒绝原因里都找不到做法时，用 revise_day_plan 把它删掉或换成能做的事。
```

「工具层 available 为 false 的动作仍可提交」一条（`rules[17]`）：原文之后接一句，接在整条最后。

```text
工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。查询记忆和回看任务当场完成，不推进时间。available 为 true 的动作单独提交都会受理；同一件事做几次、先做哪件，环境都不另作处理。
```

「个人事务在三个活动控制工具的 errands 中维护」一条（`rules[18]`）：原文之后接一句。

```text
个人事务在三个活动控制工具的 errands 中维护，提醒到点只提醒一次且不会叫醒睡眠。办成由真实结算确认，自己说办完不算。个人事务只记要买到或取到的商品，item 写商品名；打电话、做家务这类没有商品的打算不记成个人事务。
```

各句依赖的引擎事实：

| 新句 | 依赖的事实 | 出处 |
| --- | --- | --- |
| 没有进行中的活动时时间不走，同一时刻接着决策 | 没有进行中的活动、有待收尾的活动，或有进行中的活动而这一轮被拒且未到同刻上限时，下一次决策排在同一秒 | `src/worldmock/life/same_moment.py:126-137` |
| 醒着时哪些事再请决策 | 唤醒表 `WAKE_TRIGGERS` 与醒着时全部保留的判定，见下文「唤醒句与唤醒表」 | `src/worldmock/life/schedule.py:43-55`、`:235-258` |
| 内容 app 放完一条接下一条，没有可接的就停下等收尾 | 一次 app 使用的自动接续与「没有可接的内容」那一行（使用停在 `completed`，等人物收尾） | [「内容 app 自动接续」](01-autoplay.md) |
| 进行中活动的 `state` 写第几集供一餐 | 工具层活动行的键就叫 `state`，阶段「进行中」在枚举里（`src/worldmock/life/prompt_schemas.py:80-85`）；进行中的地点系列在它前面写集数与供餐 | `tool_layer.py:195`；[「地点系列、暂停拒绝与关着的 app」](05-places-and-apps.md) |
| 看法包括人设层 preferences 和关系里的看法 | 收尾的 `views` 变成人设层的 `preferences` 与 `relationships[].view` | `memory_layer.py:119-136`、`prompt_schemas.py:143-147` |
| 今日计划由人物改，环境不判定 | 日计划只存自然语言目标与提醒，没有判定完成的路径；`revise_day_plan` 是控制请求之一 | 设计文档《模拟引擎》「唤醒、日计划与个人事务」；`life_prompt.py:29-30` |
| `available` 为 `true` 的单独提交都会受理 | 每行的可用性与受理走同一套检查，开始动作行按只含这一行的一组求值；引擎里没有按次数或先后限制受理的计数 | `tool_layer.py:84-100`；设计文档《人物》「提示与工具」 |
| 个人事务只记商品 | 「新增」的 `item` 是「要取得的商品名称」，必须解析到唯一一件目录商品；办成只看本人的 `goods_received` | `models.py:936`、`:960`；`errands.py:384-401`、`:235-242` |

### 二十条一览

| 序 | 开头 | 下标 | 本次 |
| --- | --- | --- | --- |
| 1 | 工具层写此刻的对象、动作、条件与代价 | `rules[0]` | 不改 |
| 2 | 天气只以环境层给出的事实为准 | `rules[1]` | 不改 |
| 3 | 每次决策以世界请求结束 | `rules[2]` | 改：接上时间与再次决策两句 |
| 4 | start_main_activity 开始占槽位 1 的活动 | `rules[3]` | 不改 |
| 5 | start_side_activity 开始占槽位 2 的活动 | `rules[4]` | 不改 |
| 6 | 名称要写完整 | `rules[5]` | 不改 |
| 7 | 在地点做哪件事由你点名 | `rules[6]` | 改：内容 app 自动接续 |
| 8 | 用内容 app 只是看这个 app 里的内容 | `rules[7]` | 不改 |
| 9 | 不知道买什么、在哪买，用 query | `rules[8]` | 不改 |
| 10 | 同时最多两项活动 | `rules[9]` | 不改 |
| 11 | sleep 写的分钟数在 10 到 720 之间 | `rules[10]` | 不改 |
| 12 | 出发前先暂停或结束必须在原地的活动 | `rules[11]` | 不改 |
| 13 | 活动自然结束后先用 conclude_activity 收尾 | `rules[12]` | 不改 |
| 14 | 世界请求登记后由环境结算 | `rules[13]` | 改：第二句重写 |
| 15 | 商品实际收到时自动吃掉或用掉 | `rules[14]` | 不改 |
| 16 | 解饿只有两种办法 | `rules[15]` | 改：系列那半句加上进行中活动的 `state` |
| 17 | 作息与习惯照人设层过 | `rules[16]` | 改：接上改计划的一句 |
| 18 | 工具层 available 为 false 的动作仍可提交 | `rules[17]` | 改：接上可用即受理的一句 |
| 19 | 个人事务在三个活动控制工具的 errands 中维护 | `rules[18]` | 改：接上只记商品的一句 |
| 20 | 正文可以留空 | `rules[19]` | 不改 |

不改的十三条与基线提交 `2ecd868d` 逐字相同。按定稿在内存里核过（基线代码上把七条换成定稿后求值）：

- 二十条互不相同，整层通过系统层 Schema。
- 现有用例钉住「只出现在一条里」的五句（`tests/integration/runtime/test_life_agent_protocol.py:429-431`）仍各只出现一次；七条新句里各取一段（见「测试要求」）也各只出现一次。
- 只有「工具层 available 为 false」那一条含 ASCII 的 `reason`；「世界请求登记后由环境结算」「available 为 false」各只在一条里；没有工具用途句是某条规则的子串。
- `test_life_agent_protocol.py:436-438` 与 `tests/unit/start_interfaces/test_system_layer_text.py:90-92` 列出的禁词、旧字都不出现，「正确答案」「环境期望」也不出现。
- 「请你决策」出现在「每次决策以世界请求结束」和讲睡眠的两条里，没有用例限制它。

### 唤醒句与唤醒表

「每次决策以世界请求结束」一条新接的两句，第一句说同一秒再决策，第二句把唤醒表里醒着时能命中的各行写成人话。
唤醒表是 `WAKE_TRIGGERS`（`src/worldmock/life/schedule.py:43-55`）；`evaluate_wake_reasons` 醒着时保留全部命中，睡着时只保留能叫醒睡眠的行（`schedule.py:235-258`）。
每一行由哪段措辞说出：

| 唤醒表名称 | 何时命中 | 说出它的措辞 | 所在的条 |
| --- | --- | --- | --- |
| `planned_end` | 本人有活动恰在这一边界做完，含做完自己结束的查询（`src/worldmock/life/rhythm.py:114-123`） | 手上的事做完 | 每次决策以世界请求结束 |
| `felt_band_change` | 四项体感里有一项的分级与上一次决策时不同（`schedule.py:245`） | 身体感觉变了 | 每次决策以世界请求结束 |
| `delivery_arrival` | 外卖送到，或快递到了驿站、转为可取（`src/worldmock/scheduling/effects.py:120-133`、`src/worldmock/life/commerce.py:19-22`） | 下单的东西送到或到站可取 | 每次决策以世界请求结束 |
| `reconsideration_interval` | 距上一次决策满重估间隔（运行配置 `reconsideration_interval_seconds`，缺省 5,400 秒；`schedule.py:240-241`） | 中途隔一阵也会请一次 | 每次决策以世界请求结束 |
| `rejection_feedback` | 同一秒里被拒之后的再决策（`same_moment.py:96-106`、`:135-136`；边界上恒不命中，`schedule.py:248`） | 同一时刻会接着请你决策 | 每次决策以世界请求结束 |
| `session_open` | 本人有一段睡眠恰在这一边界做完（`rhythm.py:122`、`:128`） | 睡着后到你写的时长才会再请你决策 | sleep 写的分钟数在 10 到 720 之间 |
| `progress_report_due` | 进行中的活动距播报锚点满播报间隔（运行配置 `progress_report_interval_seconds`，缺省 1,800 秒；`rhythm.py:186-189`） | 中途隔一阵也会请一次 | 每次决策以世界请求结束 |
| `no_active_activity` | 本人没有进行中的活动（`rhythm.py:129`、`same_moment.py:104-105`） | 没有进行中的活动时时间不会往前走 | 每次决策以世界请求结束 |
| `reminder_due` | 日计划提醒或个人事务提醒到点（`rhythm.py:130`） | 提醒到点 | 每次决策以世界请求结束 |
| `duty_boundary` | 班次起止或写明的休息时段起止（`rhythm.py:131`、`src/worldmock/life/duty_edges.py:23-48`） | 到了上下班时刻或写明的休息时段开始或结束 | 每次决策以世界请求结束 |
| `app_push` | 整点发出的 app 推送送到醒着的人物（`src/worldmock/scheduling/compile.py:37-39`、`effects.py:42-46`、`rhythm.py:132`） | app 推送 | 每次决策以世界请求结束 |

「提醒到点」在「个人事务在三个活动控制工具的 errands 中维护」一条里也出现，那里说的是提醒不叫醒睡眠；对照只认表里写的那一条。

守卫用例把这张表钉住：唤醒表增删一行或改名时，用例在措辞跟着改之前一直失败。

- 位置：`tests/unit/start_interfaces/test_system_layer_text.py::test_wake_sentence_names_every_wake_trigger`（新）。
- 写法：在同一文件加导入 `from worldmock.life.schedule import WAKE_TRIGGERS` 和模块常量 `_WAKE_PHRASES`，键是唤醒表名称，值是（规则下标，措辞），取值恰为上表：

  ```python
  #: 唤醒表每一行由系统层哪一条（下标）的哪段措辞说出；唤醒表增删行或改名时，这里与措辞一起改。
  _WAKE_PHRASES = {
      "planned_end": (2, "手上的事做完"),
      "felt_band_change": (2, "身体感觉变了"),
      "delivery_arrival": (2, "下单的东西送到或到站可取"),
      "reconsideration_interval": (2, "中途隔一阵也会请一次"),
      "rejection_feedback": (2, "同一时刻会接着请你决策"),
      "session_open": (10, "睡着后到你写的时长才会再请你决策"),
      "progress_report_due": (2, "中途隔一阵也会请一次"),
      "no_active_activity": (2, "没有进行中的活动时时间不会往前走"),
      "reminder_due": (2, "提醒到点"),
      "duty_boundary": (2, "到了上下班时刻或写明的休息时段开始或结束"),
      "app_push": (2, "app 推送"),
  }


  def test_wake_sentence_names_every_wake_trigger():
      """唤醒表的每一行都由系统层的一段措辞说出：对照表的键恰是唤醒表的全部名称，每段措辞都在所注的那一条里。"""
      names = [trigger.name for trigger in WAKE_TRIGGERS]
      assert set(_WAKE_PHRASES) == set(names) and len(_WAKE_PHRASES) == len(names)
      for name, (index, phrase) in _WAKE_PHRASES.items():
          assert phrase in LIFE_SYSTEM_LAYER["rules"][index], name
  ```

- 集合相等是双向的：唤醒表多一行、少一行或改了名都失败；措辞改动而对照表没跟上，子串断言失败。

### 与提案不同的地方

| 提案写的 | 本章 | 理由 |
| --- | --- | --- |
| 唤醒句写「下单的东西送到」 | 「下单的东西送到或到站可取」 | `delivery_arrival` 在快递到驿站时也命中（`commerce.py:19-22` 把驿站订单改为可取，`effects.py:132` 照样给出这个原因）；提案自己也说这一句对应的行包括快递到站（「Meals and waiting」）。只写「送到」，人物不会想到到站那一刻会被叫起 |
| 「在地点做哪件事由你点名」一条里插入「打开后放完一条自动接下一条，直到你结束它，没有可接的内容时自己结束」，没给标点 | 「…由环境选择，打开后放完一条自动接下一条，直到你结束它；没有可接的内容时停下，等你收尾。app 里的…」 | 「自己结束」在系统层专指做完不用收尾的查询（「不知道买什么、在哪买，用 query」一条）；没有可接内容的一次 app 使用停下后仍要收尾。照字面插入，句子会读成「由环境选择打开后…」 |
| 合同里「确实无事时从工具层选择此刻可做的低消耗活动」一句「被替换」 | 那句从没进过代码，只在冻结的开发记录里（`SPEC-environment-dataset-contract.md:1094`）；新的唤醒句取代合同这一行的要求，冻结记录不改 | 冻结的开发记录不回写 |
| 钉住系统层的用例列了四份 | 另有 `tests/system/lifecycle/test_run_viewer_recording.py`（录制样例里的系统消息必须等于现行宿主）与 `tests/unit/agent_protocol/test_tool_description_references.py::test_interaction_rules_live_only_in_the_system_layer`（不用改），见「测试要求」 | 提案的清单不全 |

其余五条的新句与提案逐字相同；「可用即受理」一句照提案接在那一条最后。

### 边界情形

- 被拒的再决策：有进行中的活动时，被拒之后同样在同一秒再决策（`same_moment.py:135`），唤醒句的第一句只把同一秒再决策和「没有进行中的活动」连在一起说；
  被拒之后先照原因处理，已由「工具层 available 为 false 的动作仍可提交」一条说出。对照表仍把 `rejection_feedback` 对到「同一时刻会接着请你决策」，见「本章定下的细节」。
- 同一秒的决策有轮数上限（三次开放加三次受限，`same_moment.py:109-146`）。句子不提上限；受限阶段只在工具层各行的 `available` 与 `reason` 里体现（`tool_layer.py:91-94`）。
- 「可用即受理」只说受理：同一件事做第几次、先后怎样，环境都照常受理；做的结果仍按身体结算，比如吃得饱时食品的利用率照常变化，这不是对重复的处理，系统层不展开。
- 进行中活动的 `state` 只在[「地点系列、暂停拒绝与关着的 app」](05-places-and-apps.md)一章的前缀落地之后才写集数与供餐；「note 没写这两种说法的事不解饿」一句不改，`state` 只是同一个供餐事实的另一处写法。

### 明确不做

- 不加新规则，不删规则，不调顺序；角色句、只读问答的系统层、工具用途句不改，工具指纹不变。
- 讲睡眠的那一条不改：睡着时只有自己的睡眠结束才叫醒，`session_open` 与睡眠结束的 `planned_end` 都由它说出。
- 不写同一秒决策的轮数上限；不为内容前提新加句子，哪些 app 此刻能用仍只看工具层的行（设计文档《人物》「提示与工具」：系统层不为前提新增句子）。
- 不加「等待」类活动，不加「没有圈套」「没有正确答案」这类句子；新句只陈述事实，不提评审、分数、测试或期望的答案。
- 不写买菜套件的说法（所有者答复：不改）；不加计划时的「能做的事」（所有者答复：不采纳）。
- 冻结的开发记录里逐字引用旧规则的地方（`docs/dev/first-real-day-run/spec/10-persona-rules.md` 等）不改；设计文档只转述话题，逐字原文只在代码和测试里。

### 本章定下的细节

| 事项 | 定下的做法 | 理由 |
| --- | --- | --- |
| 守卫用例的放法 | 放在逐字用例同一个文件；措辞按（下标，子串）对照；同一段措辞可以对几行：`progress_report_due` 与 `reconsideration_interval` 都对「中途隔一阵也会请一次」 | 两行在人物看来是同一件事；按子串对照，改措辞时只改常量里的一处 |
| `planned_end` 对哪一条 | 「每次决策以世界请求结束」一条的「手上的事做完」 | 这一行醒着时最常命中；睡眠做完那一刻的 `planned_end` 由讲睡眠的那一条说出，与 `session_open` 同在一刻 |
| `rejection_feedback`、`no_active_activity` 对哪一句 | 都对唤醒句的第一句：前者对「同一时刻会接着请你决策」，后者对「没有进行中的活动时时间不会往前走」 | 对照只取「每次决策以世界请求结束」与讲睡眠的两条；两者都是同一秒再决策的原因（`same_moment.py:96-106`），被拒之后怎样做已由「工具层 available 为 false」一条说出 |
| 源码排版 | 七条新文字照现有写法拆成相邻的字符串字面量，每行不超过 120 字符 | 行宽是风格门的规则；拆法不进文字 |
| `life_prompt.py` 两处常量注释（`:20`、`:90`，写着「规格第一章」） | 改为 `#: 生活决策的系统层（逐字原文只在这里；话题见 docs/design/06-agent.md「提示与工具」）。` 与 `#: 只读问答的系统层（docs/design/06-agent.md「提示与工具」）。` | 「规格第一章」指的是早已冻结的旧规格，注释应指向现行设计；注释不进提示指纹 |
| `test_retired_wording_is_gone_from_the_system_layer_and_tool_descriptions` 的退役字 | 不加新的退役字 | 改掉的旧句已由逐字用例整条钉住，再列一遍是重复 |

## 接口与文件指引

| 文件 | 改什么 |
| --- | --- |
| `src/worldmock/agents/life_prompt.py` | `LIFE_SYSTEM_LAYER["rules"]` 的下标 2、6、13、15、16、17、18 七条换成上文的定稿全文；`:20` 与 `:90` 两行注释照「本章定下的细节」改；其余十三条、`role`、`READONLY_SYSTEM_LAYER`、`TOOL_DESCRIPTIONS` 和全部函数不动 |

本章没有别的生产文件，实施归轨道 B（提示与计划）。

直接删除，不留兼容：七条的旧文字。不保留旧版本的常量、开关或按运行版本挑文字的分支；旧运行按 `prompt_sha256` 被拒，不能恢复或重放。

夹具：本章不改样例环境。录制样例（`tests/fixtures/viewer-context.json` 里的两处系统消息）和两份基线带着系统层原文，
只在收尾步按[「版本、身份与基线」](08-versions.md)一章用真实宿主重录、重冻一次，不手改。

约束：

- `life_prompt.py` 每行不超过 120 字符，文件不超过 2000 行（今天 166 行）；本章不新增函数，没有新的函数内导入，`tests/unit/engineering/test_import_graph.py` 的 `DELAYED_IMPORTS` 不变。
- `tests/dataset_acceptance.py` 里没有锚在 `life_prompt.py` 上的源码变异，本章也不登记；本章改到的现有用例都不是已登记的变异杀手，一律不改名。
- 两处新句说的是别的章实现的行为：内容 app 自动接续（[「内容 app 自动接续」](01-autoplay.md)）与进行中系列的 `state`（[「地点系列、暂停拒绝与关着的 app」](05-places-and-apps.md)）。
  轨道 B 先落文字，三条轨道并入之后这些句子才全部属实；对外以并入后的主干为准（[「实施顺序与验收」](09-implementation.md)）。
- 设计文档里这些话题的归宿见[「设计文档与手册」](07-design-docs.md)一章；本章不改设计文档。

## 测试要求

系统层的生产入口是宿主发给模型的请求：`tests/integration/runtime/test_seven_layer_request.py` 用 `engine_factory` 建真实引擎、经 `current_contract_fixtures.decide` 走真实宿主，取落库的那次请求；
单元用例钉住常量本身。两者合起来，每一条定稿都由真实请求逐字证明。

### 规则与用例

| 规则 | 层 | 测试 | 验收行为 |
| --- | --- | --- | --- |
| 七条改为定稿，其余十三条逐字不变，恰二十条且互不相同 | unit | `tests/unit/start_interfaces/test_system_layer_text.py::test_system_layer_is_the_twenty_rules_verbatim_in_order`（改） | 整表等于 `_RULES`，`_RULES` 里七项换成上文代码块的文字；整层通过系统层 Schema |
| 真实宿主请求带的就是这二十条 | integration | `tests/integration/runtime/test_seven_layer_request.py::test_current_host_request_has_one_system_layer_and_six_user_layers`（改） | 真实引擎、真实宿主的一次决策：请求 `messages[0]` 的 `rules` 恰二十条，等于常量，也等于逐字用例的 `_RULES` |
| 唤醒句覆盖唤醒表的每一行 | unit | `tests/unit/start_interfaces/test_system_layer_text.py::test_wake_sentence_names_every_wake_trigger`（新） | 对照表的键集等于 `WAKE_TRIGGERS` 的名称集合；每段措辞在所注的那一条里 |
| 「可用即受理」与 `reason` 只在一条里 | unit | `tests/unit/agent_protocol/test_decision_context_protocol.py::test_start_tool_descriptions_say_purpose_only_and_conditions_stay_in_the_tool_layer`（改） | 真实 `LifeAgent` 跑一次 ReAct 循环（只换模型提供方），请求系统层里含 `reason` 的规则恰一条，等于改后的全文 |
| 新句各只出现一次，禁词不出现；醒着时的再决策只在讲决策怎样结束的一条说 | integration | `tests/integration/runtime/test_life_agent_protocol.py::test_how_to_live_a_day_is_one_resident_section_of_both_decision_stages`（改） | 宿主构建的系统消息里，原有五句与新加的七段各只在一条里；「醒着时，手上的事做完」在 `rules[2]` 里；禁词表加「正确答案」「环境期望」后都不出现 |
| 机制只在系统层说一次 | unit | `tests/unit/agent_protocol/test_tool_description_references.py::test_interaction_rules_live_only_in_the_system_layer`（不改） | 「世界请求登记后由环境结算」「available 为 false」各只在一条里；没有工具用途句是规则的子串 |
| 改任何一条都改提示指纹 | integration | `tests/integration/runtime/test_seven_layer_request.py::test_prompt_identity_changes_when_system_layer_text_changes`（不改） | 逐条加探针，提示指纹都变，工具指纹不变 |
| 录制样例的系统消息与现行宿主一致 | system | `tests/system/lifecycle/test_run_viewer_recording.py::test_viewer_fixture_is_recorded_from_the_current_host`（不改；样例在收尾步重录） | 录制样例两处 `messages[0]` 等于现行系统消息 |

### 钉住今天文字、要改的现有用例

| 用例 | 钉的旧行为 | 处理 |
| --- | --- | --- |
| `tests/unit/start_interfaces/test_system_layer_text.py` 的 `_RULES`（`:12-75`）与 `:11` 的注释 | 七条旧文字；注释写「下标 0、1、3、10、13、14、16、18、19 九条与改动之前逐字相同」 | `_RULES` 的下标 2、6、13、15、16、17、18 七项换成定稿全文（照文件现有写法拆成相邻字面量）；注释改为「#: 系统层二十条的逐字定稿，次序即下标；下标 0、1、3、4、5、7、8、9、10、11、12、14、19 十三条与改动之前逐字相同。」；另加上文的导入、`_WAKE_PHRASES` 与守卫用例；模块说明（`:1`）在「提示指纹随文字改变」之前加「唤醒表每一行都有系统层的措辞说出；」 |
| `tests/unit/agent_protocol/test_decision_context_protocol.py:256-257` | 含 `reason` 的规则等于旧的整条 | 期望值换成「工具层 available 为 false 的动作仍可提交」一条的定稿全文：`"工具层 available 为 false 的动作仍可提交，会按 reason 拒绝；先照原因处理。查询记忆和回看任务当场完成，不推进时间。" "available 为 true 的动作单独提交都会受理；同一件事做几次、先做哪件，环境都不另作处理。"` |
| `tests/integration/runtime/test_seven_layer_request.py:31` | 只比条数与常量 | 加导入 `from tests.unit.start_interfaces.test_system_layer_text import _RULES`（跨层导入测试模块已有先例，如 `tests/integration/life/test_planning_and_errand_evidence.py:17`）；断言改为 `assert len(rules) == 20 and rules == LIFE_SYSTEM_LAYER["rules"] == list(_RULES)` |
| `tests/integration/runtime/test_life_agent_protocol.py:415-438` | 五句唯一、六个禁词 | 唯一句的元组在原有五句之后加七项：「没有进行中的活动时时间不会往前走」「打开后放完一条自动接下一条」「环境也不按它们限制你此刻能做什么」「系列的 note 或进行中活动的 state」「今日计划只是自己的打算」「available 为 true 的动作单独提交都会受理」「个人事务只记要买到或取到的商品」；在 `rules[9]` 那句断言之后加 `assert "醒着时，手上的事做完" in rules[2]`；禁词元组加「正确答案」「环境期望」；说明第二句改为「同时进行的两个槽位只在讲槽位的一条（rules[9]）说一次，醒着时何时再请决策只在讲决策怎样结束的一条（rules[2]）说一次，睡着后何时再请决策只在睡眠那一条说一次（docs/design/04-simulation-engine.md）。」 |
| `tests/unit/start_interfaces/test_system_layer_text.py::test_prompt_fingerprint_is_recomputable_and_follows_the_sleep_rule` | 改 `rules[10]` 的「10 到 720」做探针 | 不改：那一条不动 |
| `tests/integration/life/test_creation_model_identity.py:116-119`（`_edit_system_rule`） | 在「工具层写此刻的对象、动作、条件与代价」一条末尾加字做探针 | 不改：那一条不动 |
| `tests/fixtures/viewer-context.json` 与两份基线 | 旧系统层原文 | 不手改；收尾步重录、重冻，见[「版本、身份与基线」](08-versions.md) |

查看器的 Node 用例（`tests/system/viewer/run_viewer.test.cjs:250`、`run_viewer_context.test.cjs:115`）只从源码读角色句，角色句不改，不用动。

### 源码变异

本章不登记源码变异。系统层是数据而不是逻辑：七条定稿由逐字用例整条钉住，真实宿主请求又与逐字表比对，任何一字改动都会被这两条用例抓住；
唤醒句与唤醒表的对应由守卫用例保持。

| 定下的事项 | 把规则改坏的方式（原句 → 替换） | 被哪条测试杀死 | 层 |
| --- | --- | --- | --- |
| （无） | — | — | — |
