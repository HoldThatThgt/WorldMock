> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/realism-root-cause-2026-09-17.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。
> 引用出处：`docs/agentsociety2-design.md` @ `9b7fbc84d4429c2f05ec4b526df53539f0ed5d78`

# WorldMock 人物行为"不像人"根因分析 —— 七视角综合报告

> 状态：调研证据（历史依据，不构成当前规范）｜ 日期：2026-09-17 ｜ 结论：四个假设不是并列的，是一条因果链（数据集上限 → 交换规则 → 呈现语言 → 画像通道），其中交换规则是主因也是放大器。

日期：2026-09-17。对象：`runs/` 下 40 个运行（重点 `runs/deepseek-day-affordance`，唯一由当前代码 `9b1125d7…` 产出的运行；2026-01-05 周一；陈曦 agent 1 / 李凡 agent 2）。
方法：七个独立子代理并行分析（A 游戏 AI、B 人格心理学、C 社会学/时间利用、D LLM 智能体/prompt、E 数据/语料、F 呈现层对照实验、G 跨 run 定量），各自只读访问 `runs/**` 与源码，产物在 `/tmp/wm-realism/<单元>/report.md` 与 30+ 个可复现脚本。跨单元共同引用的八条关键机制已由主会话逐条在源码/数据中复核（见附录 A）。
本文件是 2026-09-17 七视角审计的综合报告归档；单元报告与复现脚本产生于 `/tmp/wm-realism/`（非版本控制产物），关键结论已由主会话在源码与运行数据中逐条复核（附录 A）。其建议经当时的规格快照 `docs/agentsociety2-design.md`（已退役，存于 Git 提交 `9b7fbc8`；现行规格见 `docs/dev/SPEC-life-realism.md` 与 `docs/dev/SPEC-life-runtime.md`）《人物真实性修订合同（2026-09-18）》裁决后才成为规范。

---

## 一、裁决

| 假设 | 七单元平均权重 | 排第一的单元数 | 一句话 |
| --- | ---: | ---: | --- |
| **H2 交换规则** | **37.1%** | 6/7 | 主因也是放大器：15 分钟强制再决策、一轮一动作、活动中隐藏购买/进食工具、动机成本随时长线性、"睡眠债"= 醒着小时数、最廉餐候选、finish 强制写记忆 |
| **H1 数据集匮乏** | 22.9% | 0 | 缺的是**品类**不是条数：没有"人"、没有正餐、没有家里的早晨、剧集是英文视觉小说、工作是 4 份英文会议转写；已有的 192 条内容只用了 10 条、129 种食品只用了 5 种 |
| **H3 数据管线呈现** | 22.6% | 1（D） | a. 给模型看的（≈2/3 权重）：观察被发两遍、商品目录常驻、画像只占 0.1–1.7%、system prompt 86% 是协议、动机参数被当 reward；b. 给人看的（≈10%）：ID/continue 噪声让本就机械的行为读起来更像日志 |
| **H4 人物画像设定** | 17.4% | 0 | 画像**文本无罪**（模型 40–72% 的推理引用了它），但编译出的 9 参数在行为上是死通道、随机浮点和 lifestyle 启用清单与文本反向、缺关系/当下处境/时间锚点 |

各单元权重（H1/H2/H3/H4）：A 30/40/20/10 · B 15/40/20/25 · C 30/40/13/17 · D 15/30/35/20 · E 35/30/15/20 · F 20/35/30/15 · G 15/45/25/15。

**四个假设不是并列的，是一条因果链**：H1 决定"能做什么"的上限 → H2 在这个上限内把人物锁成"每 15 分钟自问要不要继续、饿了买同一碗面、把醒着的小时数当睡眠债"的状态机 → H3a 让模型用引擎的语言而不是人的语言思考 → H4 的结构化层没有任何通道把人格送到行为里。

---

## 二、症状 → 机制（全部在运行数据中核实）

### 1. 07:00 开车去 08:00 才开门的公司，在门口空等 1h45–1h52
- 家中 07:00 的工具面只有 `wait / sleep / lifestyle:*（需先 browse 才知道 id）/ play_game / browse / start_travel`，没有 purchase/consume（商品要先 browse 才可见）——早餐、温水、洗漱在世界里不存在（`model_request` seq 34/35）。
- 任务 `available_at 09:00`，07:00 任务栏为空；两人都推理到"公司 8 点才开门很奇怪"，但陈曦 seq 36 "Let me just pick travel to office. The engine will handle"，李凡 seq 43 先想"洗漱、吃早饭、cooking-research 40 分钟再出门"，随后 **"Hold on, let me reconsider using the motivation: weights … goal_task 0.16 (aspiration 16200s, currently 0 → gap!) So the biggest deficit is goal_task: I should work"**。
- 没有日程骨架；每次决策都是从当前状态出发的贪心反应。
- 跨 run：28/54 run-agent 早到；等待 99 分钟/天；21 个代码版本 Spearman ρ≈0，**从未改善**。
- 归因：H2 主（工具门控、无日程、参数暴露）· H1（家无可供性）· H4（画像"7 点起床"无后续锚点）。

### 2. 19:53 / 21:44 上床，计划睡 11.1h；"经常凌晨一点才睡"的李凡三次运行 21:44 / 23:19 / 18:55
- `catalog/resources.jsonl:3`：`awake_sleep_debt_per_hour: 1, sleep_recovery_per_hour: 2`，provenance `type: assumption`；`body.py:274-288` 线性累加、无饱和、无昼夜门控。于是 `sleep_debt_hours ≡ 醒着的小时数`，任何人傍晚都显示 10–16 小时"债"。
- 数字原样进提示。陈曦 seq 2067："12.9 hours of sleep debt is huge (almost 2 nights)… A 32-year-old PM going to bed at 8 PM? Unusual but if extremely sleep-deprived…"（同一段里她想过"瑜伽/打个电话再睡"，因"一轮一动作"放弃）。李凡 seq 2221："21:44 is unusually early for someone who sleeps at 1am; **but the debt signals he should**… Sleep debt weight 0.25… Clearly sleep dominates"。
- Borbély 两过程模型对照：07:00 起床，19:53 时 S=0.59，入睡阈值 0.67，约 23:48 才越阈——正好是画像里的正常就寝时段。
- 跨 run：3 个运行 5 次异常就寝全部同因；就寝时刻均值 19:10，36/43 在 22:00 前；就寝时债 ↔ 计划睡眠时长 r=0.52。
- `food_efficiency_curve` / `work_efficiency` 都以该"债"为输入 → 每个人从下午 15:00 起在模型里就开始"受损"，与真实的傍晚警觉高峰相反。
- 归因：H2（身体模型语义）+ H3a（原始数字 + 误导性标签直达模型）。

### 3. 三次同一碗 6.5 元红烧牛肉面；冰红茶三次想买三次放弃
- 数据（H1）：121 种可食商品里 ≥400 kcal 的 25 种，其中 7 碗方便面、其余薯片/饼干/坚果/巧克力；**0 份热食/餐馆菜/外卖正餐**；Open Food Facts 是包装食品条码库，`countries_tags=china` 全库只有 1,679 条。
- 规则（H2）：活动进行中 `_available_action_models` 只留 `continue / set_activity_state / start_basic / start_targeted / start_travel(/playback)`，**purchase、consume、browse_activities、submit_work 全部消失**（`life_agent.py:1084-1113`）→ 饿了只能 `start_travel(market)`；`validate_react_decisions` 拒绝一次响应两个生活动作 → 李凡 seq 615/1365/2181 三次写到"再来一瓶冰红茶… submit exactly one life action… I'll prioritize the noodle"；首次购买被 finish 写成 preference，下一轮作为"稳定偏好"读回（seq 1365 "my memory says I prefer cheap filling noodles"）。
- 动机层是死的：`_eat_candidate` key=`(price_fen, wait, id)` → 恒为 3.22 元西柚汁（59 kcal），rank 9，152 轮里只在手持食物时出现 3 次"Eat held …"。
- seq 2181 还出现：consume 工具 enum 里只剩残留 1e-09 份的 food-6920152460061，模型解读为"环境想让我买这个"。
- 跨 run：85–90% 的进食是同一商品（两套口径 46/54、111/124）；129 种食品覆盖率 4.1%。
- 归因：H1 45% · H2 40% · H4 15%（李凡画像确实写了"常选方便面或牛肉面"，被过度表达）。

### 4. 全天零社交：两人同一办公室清醒共处 10.86 小时，互不可见
- `engine._observation_data`（`engine.py:658-701`）29 个键里没有任何"同地点其他人物"字段；11 个工具没有 talk/message；3 封邮件是不可回复的模板句。
- **AS2 已有 `contrib/env/simple_social_space.py`（send_message / receive_messages / create_group / … 6 个工具），WorldMock 的 `DirectEnvActorSpec` 只挂 `("LifeEnv",)`（`runtime.py:555-558`），全仓 0 引用。**
- 当时的规格快照 `docs/agentsociety2-design.md`（已退役，存于 Git 提交 `9b7fbc8`；现行规格见 `docs/dev/SPEC-life-runtime.md`）的目标表里没有"社交"这一项；社交聚餐日实现为 `_calendar_context` 的一个布尔位。
- 画像编译没有社交通道：外向性 6 与 2 得到相同的 `attention_prior.social = 0.06`。
- 跨 run：MEMORY.md `Relationships: None recorded` 占 94.4%；推理中 "colleague" 仅 1 次且是猜邮件发件人。
- 归因：H1（世界无他人）+ H2（AS2 有而未接、无 social need）+ H4（无关系字段）。

### 5. 21 项 lifestyle 活动 40 个运行零命中
- `affordances.toml:273-290` lifestyle `relief = none` → 在"内稳态 + 习惯 + 情绪 − 成本"里是纯成本；`motivation.py:411-414` `cost = (|money| + β·hours·2400)/1000` 与时长**线性**，10 分钟看剧 cost 0.4 恒胜 60–120 分钟的生活活动（2.4–4.8）。
- `_lifestyle_candidates` 从**全库 183 条**（非人物启用集，docstring 自认）按 `attention_prior × 偏好(缺省 0.5)` 排序 → 全平 → 字母序：acroyoga、3d-printing、amateur-astronomy、board-games，4 条都不在启用集里，照做会被 `UNKNOWN_LIFESTYLE_ACTIVITY` 拒绝。
- `start_basic_activity` 工具描述只写 "Start wait or sleep"；lifestyle 因 `target_discovery=browse_result` 被排除在描述外；40 个运行 browse_activities 只被调用 14 次，lifestyle 品类 0 次。
- 启用清单与画像反向：合租程序员被分配遛狗/园艺；陈曦画像里可直接映射的温水/深呼吸/微信/约饭四项一个没启用。
- 模型在推理中认真考虑过 59 次，全部因"无效果 / 不合场景 / 一次一动作 / 不知道 id"否决。
- 跨 run：0/978 轮进候选，0/1260 次动作。
- 归因：H2 60% · H4 25% · H1 15%（语料条目只有 id/时长/MET/描述，没有地点/时段/伙伴/效果）。

### 6. 每 15 分钟一次"我要继续吗"，记忆变成遥测
- `activity_feedback_seconds = 900` 每到边界强制唤醒 LLM；152 次决策里 82 次（53.9%）最终动作是 `continue_activity`，9 次 wait，真正的新选择 51 次（33.6%）；continue 决策吃掉 53% 的 prompt token（9.58M/18.0M）。跨 run：51% 决策由边界触发，其中 **97.7% 选 continue**，占 62% token。
- `finish` 强制 ≥1 条记忆（`life_agent.py:377`；vendor `memory_runtime.py:325,354` 拒绝空列表）→ 一天 167/159 条 episode，45–56% 是"到边界、选择继续、能量约 826 千卡、睡眠债约 3.59 小时"的簿记；59%/82% 含身体数字；0/326 条提到任何一个"人"。
- 巩固闸门 `memory.py:793-811`：preference/identity 无条件放行、observation ≥0.85（本运行最大 0.7）、routine 永不 → 进入 MEMORY.md 的**全部是规则**；任一 preference 类记忆触发全文重写（一天 47/39 次）。
- 巩固 prompt `memory_runtime.py:26-86` 不传画像、示例 "28-year-old female, marketing analyst / AOI 500038964" → 李凡 MEMORY.md 第一条 **"Marketing analyst"**（他是后端工程师）；约半数运行的 MEMORY.md 含该模板残留（31/68 文件）。
- 陈曦 45 条 preference 中 41 条是把 system prompt 那句"办公室别公开看长视频"复述成"我再次确认自己的一贯偏好"，形成 规则→记忆→规则 的自我强化回路（seq 2067 引用 memory "goes to sleep on time" 支持 19:53 就寝）。
- 归因：H2（协议）+ H3（巩固模板）。

### 7. 两条时间线分不出人格
- 大五→9 参数（`persona-mapping.toml`）只作用于动机 hints；hints 97% 是"Watch … (~10 min)"（760 候选中 737 条），模型选择与 rank-1 一致 9.9–17.5%；`attention_prior` 的 9 个生活品类键因 lifestyle 从不入 top-5 而完全无效；tau 0.5 vs 2.0 只改变显示的 softmax 概率。
- 参数却原样进提示（`runtime.py:601-613` 整体 `model_dump`）：李凡 71% 的动作轮引用 weights/comfort/aspiration，"Let me think about the reward model. Weights: money 0.18, energy 0.24…"（seq 86）。
- 222 个 `entertainment_preferences` 是 U(0.4,0.8) 随机数（两人 r=−0.13；仓库无生成脚本）：李凡段子 0.44 < 陈曦 0.78、知乎 0.46 < 0.59，与画像"李凡压力大刷知乎段子"反向；陈曦"科幻悬疑"口味在内容无题材元数据下无处编码，rank-1 变成 drama-0002《Sakura Succubus 6》。
- 两人 `occupation / routine_preference / consumption_preference / risk_preference` 四个字段逐字相同。
- 画像 31 条行为特征：参数层能表达 ≤5，世界层可执行 14，都不能 12（温水、播客、约同事、题材口味、深呼吸、社交/知乎发帖、合租、心流、chronotype、indie/动漫、电子设备、朋友关系）。
- 跨 run：同 run 两人活动分布 JS 距离 0.178 < 同一人跨 run 0.284；同代码控制下同人 0.210 vs 异人 0.231（Δ=0.02）；去掉"只有 agent 1 收邮件"这一场景不对称后最近邻分类 59%（p=0.22）。唯一稳健信号"李凡玩游戏更多"（8/27，p=0.008）在最新 5 个 run 也归零。
- 归因：H4（编译层）+ H2。

### 8. 模型到底在想什么（H3a）
- 每次请求 min 1k / avg 44.7k / max 132k prompt tokens；user message 中 **observation 被序列化两遍**（vendor `person.py:555-570` 同时写 `observation` 字符串与 `data` 字典，`life_agent.build_react_messages` 原样 dumps）占 37–44%；一次 browse 后 129 件商品全表常驻（`engine.py:667` 无 TTL）占约 60%，把提示词从 4.7 万字符抬到 20.6 万；`background_story` 平均占 0.25%（最低 0.075%）。
- system prompt 291 词，86–88% 是工具协议 / ref 规则 / 回合预算，人物框架 16 词；vendor PersonAgent 的身份前言（"You are not an AI assistant — you are a human being…"）被 WorldMock 的 `_LIFE_INSTRUCTIONS` 替换掉。
- 152 条动作轮推理：身体数字 30.3 词/条、协议 29.4、生活语言 6.3；35% 一个生活词都没有；97–100% 讨论协议；模型在 18 次决策里推理"grader / evaluator / the engine wants what"（38 处）；48%/30% 的轮次困惑于"提示说有 browse_activities/consume，schema 里没有"。
- 但 40% 的动作轮直接引用中文画像原句，首轮推理 66–72% 提到温水/播客/同事/知乎——**不是模型不会演，是协议不给演**。
- `reasoning_content` 在 after_action 请求里 0 次回传（DeepSeek 文档要求回传），finish 轮看不到动作轮的思考，只能从 receipt + schema 重建 → 93% 是协议核对。
- 引擎内部字段 `end_time / world_state_version / affordance_contract / energy_feasibility / observation_event_id` 进了人物观察；模型据此算"世界几点结束"（seq 178/2141）。

### 9. 呈现对照实验（H3b）
- 原始 `inspect_run.py events --agent 1`：1047 行、661 个 hex32 ID、中文 0.1%；07:00–08:05 一小时占 102 行；`_brief()` 读 `tool` 而事件键是 `tool_name` → 所有动作显示 `?`。
- 合并 continue/wait 后的简洁时间线：37 行、0 个 ID、中文 31%。**15 分钟 continue 噪声、wait 分段、ID、模型往返、存档事件全部消失；但 9 项行为硬伤全部保留**（早到关门公司、19:53 就寝、三碗同款面、零社交、零生活活动、4h14m 连续工作、交付被打回后 0 秒重提、李凡工作 570 分钟 0 次提交收入 ¥0、晚间娱乐是《Sakura Succubus 6》），其中 3 项在真名呈现下**更**刺眼。
- 用 episodes 拼的"第一人称日记"也没有变得像人（62–72% 条目是边界簿记）。
- 归因：印象的约 1/3 来自呈现、2/3 来自行为；H3b 单独 ≈10%。

### 10. 与真人日常的距离（C 单元，分钟/日）
| 项目 | 陈曦 | 李凡 | 2018 CTUS 城镇/工作日 |
| --- | ---: | ---: | ---: |
| 睡眠 | 666 | 556 | 553 |
| 有酬工作 | 515 | 570 | 460（参与者） |
| 用餐 | 45 | 45 | 103 |
| 等待 | 112 | 105 | — |
| 社交 / 家务 / 个人护理 / 运动 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 21 / 79 / 52 / 41 |
| 娱乐 | 73 | 126 | 167 |
就寝：白皮书 2024 平均入睡 00:01、29% 在 23:00 前 vs 19:53/21:44；早餐：约 73% 职场人吃 vs 0/2。

### 11. 84 天正式场景的素材容量（E/C 单元静态摊算）
56 份合同全部 package/600 元，输入 ES2002a–d 四份英文转写各重复 14 次；144 封邮件只有 8 种正文，一句模板出现 46 次；日历特殊日全部是 `_calendar_context` 布尔位（病假日无身体变化、聚餐日无对象/地点/费用）；0 新商品、0 新内容项；meal=7、work=4、mail=8、drama=10 的基数第一周内全部重复。12 周里必然发生而这里没有的：发薪/房租、春节回乡或留沪、家人电话、朋友聚会、感冒、天气、新闻热点、周末结构。

---

## 三、业界/学界对照（各单元共同引用、机制具体）

- **The Sims（Smart Object advertisement）**：物件向每种 motive 广告效用，需求越低曲线越陡，满足即归零，按距离衰减，从前几名里带噪声挑选。对照：WorldMock 的候选 need=0 时五条并列、无饱和、无随机曝光、cost 线性压过一切。
- **Infinite Axis Utility System / dual utility（Dave Mark, Kevin Dill）**：consideration 经 response curve 相乘并补偿；时长是普通 consideration 而非从收益里线性减去；先按类别 rank 再类内加权随机。
- **RimWorld / Dwarf Fortress**：需求是 [0,1] saturation，阈值化为 Hungry/Tired 等第一人称文案；时段表就是日程骨架；job 跑到结束只被更高优先级打断。
- **Borbély 两过程模型（Daan/Beersma/Borbély 1984；SAFTE）**：S 饱和上升 τ≈18.2h、睡时 τ≈4.2h 下降，被昼夜 C 门控，傍晚有 wake-maintenance zone；输出的是倾向不是"债"。
- **GOAP（Orkin）**：只在计划失效/世界变化/更相关目标出现时重规划，"两次规划之间可以以分钟计"。
- **Stardew / Skyrim Radiant AI**：NPC 日程表按季节/星期/天气键控；像人的日常八成来自骨架、两成来自即兴。
- **Generative Agents（Park et al. 2023）**：先生成 5–8 段日计划递归到 5–15 分钟；反思在重要性累计 >150 时触发（每天 2–3 次）；评估用"一天回放 + 五类访谈"由人类 TrueSkill 排序。
- **Humanoid Agents（Wang et al. 2023）**：5 项需求 0–10，只在 ≤3 时翻译成 "slightly/very hungry" 注入；社交需求靠对话补足；每 15 分钟只在情绪非中性或需求越阈时才调 LLM。
- **AgentSociety / AS2**：Need→Plan→Behavior 序列；contrib 里已有 SimpleSocialSpace 消息环境。
- **Sotopia / CAMEL / 角色卡实践**：私有社会目标、示例言行比形容词有效、协议一多角色就塌（role flipping）。
- **Tam et al. 2024 "Let Me Speak Freely?"**：格式约束越严推理越差，先自然语言再映射到格式无损。
- **Fleeson 2001 / Mischel & Shoda CAPS / Peters & Matz 2024**：单日行为主要由情境与习惯决定，个体内方差 ≥ 个体间；文本→大五推断有效性上限 r≈0.3；要两人可分必须给不同情境与 if-then 脚本。
- **CTUS/ATUS 时间日记**：活动 + 地点 + 同伴三元组；ActivitySim 类模型先选人-日活动模式再生成出行链并用调查边际值校准。

---

## 四、修改建议（按性价比排序）

### 立即可做（几行到几十行，不改规格）
1. **去掉 observation 字符串副本**（`custom/agents/life_agent.py build_react_messages` 只保留 `data`，或覆写 vendor `_observation_record`）→ 每请求 −37~44% token（−6.7M/天），零行为风险。
2. **商品目录只在店内可见 + browse 后 TTL 遗忘**（`engine.py _observation_data / visible_product_ids`；purchase schema 保留 ID enum）→ 非店内请求 −44k 字符。
3. **从提示中移除引擎字段**：`profile.motivation`、`entertainment_preferences`、`end_time`、`world_state_version`、`affordance_contract`、`energy_feasibility`、`observation_event_id`（`life_agent.get_profile` 过滤 + `engine.py:670-694`）→ "用 weights 再想一遍"/"the engine wants" 归零。
4. **身体数值改分档体感**（不饿/有点饿/很饿；清醒/有点乏/很困），**睡眠压力改两过程模型**（饱和 + 昼夜门控 + 画像相位；至少醒着累积 ≤0.5h/h 并改名）（`body.py body_observation` + `catalog/resources.jsonl:3`）→ 19:53/21:44 就寝消失，李凡可以晚睡。
5. **重写 system prompt**：恢复 vendor 身份前言，协议规则移入各工具 description，删除回合预算 JSON，加一句"先用一两句第一人称写此刻的感受和想做的事，再选工具"（`life_agent._LIFE_INSTRUCTIONS`）→ 角色词占比 14%→≥50%。
6. **记忆回路**：continue 决策允许 0 条记忆；巩固不按 preference 逐条触发（改 importance ≥0.75 或 pending ≥10）；巩固 prompt 传入画像、删掉 "marketing analyst" 示例、允许保留 3–5 条具体事件；写入前按相似度去重（`life_agent FinishRequest` + vendor `memory.py:723-811` / `memory_runtime.py:26-86,325,354`）→ MEMORY.md 重写 47→2–5 次/天，身份漂移消失。
7. **动机层**：成本按单位时间归一（或对 cost 同做 ^0.88 凹变换）+ 修 money 双重计价；eat 候选加 ≥300 kcal 门槛并输出 top-3、近 24h 吃过的降权；娱乐同家族 ≤2 且 need<0.3 不生成；删除 "Sleep for 0.0h"；lifestyle 只从启用集采样；或干脆只注入一句驱力摘要，不给 rank/softmax（`motivation.py:400-417, 531-568, 886-953`；`affordances.toml`）。
8. **工具面一致性**：工具描述与实际 schema 一致；lifestyle 启用项直接列为 enum（8–11 项无需 browse）；家中免 browse 可见常用项；观察加一行"公司 08:00 开门、任务 09:00 可用"（`life_agent._life_tool_descriptions / _available_action_models`）。
9. **after_action 请求回传 `reasoning_content`**（`agent_runtime.py:738-764`）→ finish 轮的记忆从"schema 核对"变成"我刚才为什么这么做"。
10. **人向工具**：`inspect_run.py` 修 `tool_name`；把 F 单元 `render_diary.py` 的时间线/叙事投影并入 `scripts/` 或 run-viewer 新增"一天"视图。

### 需要规格变更（`docs/agentsociety2-design.md` 已退役；决策节律、身体语义与观察投影见 `docs/dev/SPEC-life-runtime.md`，两阶段决策、记忆、动机、日程与画像见 `docs/dev/SPEC-life-realism.md`）
11. **续行默认、打断为事件**：15 分钟仍结算，但只在需求越阈（带滞回）/ planned_end / 新消息 / 日程到点时唤醒模型 → 决策 152→~50，token −50% 以上，记忆遥测消失，"心流"成为可能。
12. **一次决策允许 2–3 步短序列**，或在反馈边界开放 purchase/consume/browse → "面+冰红茶""早饭→通勤""瑜伽→睡"不再被砍掉。
13. **日程骨架**：醒来时按画像时间锚点生成当日 5–8 段计划，引擎在无高优先级驱力时按骨架给默认动作，LLM 只在冲突/事件时介入并可改写。
14. **两阶段决策**：阶段 1 无工具自由文本（1–3 句第一人称"此刻我…想…"，thinking disabled + 高温采样），阶段 2 映射到恰好一个工具；阶段 1 文本作为 `inner_monologue` 落盘（同时修 H3b）。
15. **社交最小可行版**：观察加 `people_here`；工具加 `talk_to / send_message`（接入 AS2 SimpleSocialSpace）；画像加 `relationships`；身体加 `social_need`；12 周场景的"同事约饭"线程可回复并兑现。
16. **世界按"物件 + 可供性"扩容**：家（厨房/热水/浴室/早餐）、办公室（茶水间/同事/会议室）、餐饮 POI（`zhangjiang-probe.osm` 框内已有 cafe 7、restaurant 7、convenience 4 等 ≥30 个）、30–50 个正餐 SKU（15–45 元、20–40 分钟）、替换英文视觉小说剧集、职业匹配的中文工作材料（84 天 ≥20 份不同输入）、内容加题材/标题元数据。
17. **饥饿从 kcal 账本拆出**：新增 satiety（3–5h 排空 + 饭点昼夜项），energy 保留为长期储备；删除或论证"食物效率随睡眠债下降"。
18. **画像编译改 CAPS 式 if-then 签名 + 时间锚点 + 关系 + 当下处境**，渲染为一段自然语言进提示；大五只留在世界层做动力学（情绪幅度/衰减、节律相位）；随机浮点改题材抽取；准入时校验画像提及实体在世界中存在（本例 8 项缺 6 项）。
19. **情绪 OCC-lite + PAD 心境**：事件→评价→情绪脉冲→半衰期衰减；N 调制幅度；以一句体感进提示并加权记忆重要度 → 被打回后出现沮丧→段子/倾诉（李凡）或深呼吸（陈曦）的分叉。
20. **验收基线**：把 G 单元 `analyze_runs.py` 纳入 CI——有效自主决策率、等待分钟、就寝时刻、consume Simpson、JS(活动分布 vs CTUS)、人格最近邻准确率、盲区分 AUC、自传体记忆四维评分，按 `code_sha256` 分组 → 让规则改动与内容改动的效果可分离（目前 21 个版本只测出了内容扩容的效果）。

### 顺序
先做 1–10（预期 token −60%，19:53 就寝与"用权重再想一遍"消失，候选层变得可参考），同一场景跑 ≥3 次做 A/B；再决定 11–19 的规格变更。**补数据（16）放在 11–15 之后**：现行协议下模型对已有的 192 条内容只用了 10 条、129 种食品只用了 5 种，先扩容不会先改善"像人"。

---

## 五、局限
1. 所有"提示 → 行为"因果来自 `reasoning_content` 文本证据与源码机制，**没有做对照运行**（去重 / 去参数 / 换提示，需调用模型，超出只读纪律）。
2. 40 个运行横跨 31 个 `code_sha256`；跨 run 统计含代码变化混杂，人格效应 Δ≈0.02 是方向性证据（同代码控制只有 26 对）。
3. 关键词统计为子串匹配，用于量级比较；各单元间少数计数口径不同（consume 总数 46/54 vs 111/124；MEMORY.md 泄漏 38/54 vs 31/68），本报告对上述计数只作量级表述，不作点值断言。
4. 真人参照用全国/城镇边际值（CTUS 2018/2024、ATUS 2024、白皮书、智联、申工社），不是"上海张江白领"的微观日记；仓库 `realism-evaluation.md` 亦声明 `comparative_plausibility_only`。
5. 84 天正式场景没有运行足迹，相关判断来自 scenario JSON 静态摊算。
6. `entertainment_preferences` 的生成脚本不在仓库，"U(0.4,0.8) i.i.d." 是从分布反推。
7. 未在浏览器中实际打开 `tools/run-viewer.html`；ALMA 的大五→PAD 系数与 IAUS 补偿因子引自记忆，未核对原文。

---

## 附录 A：主会话复核清单（全部成立）
| 机制 | 位置 | 结果 |
| --- | --- | --- |
| observation 双份序列化 | vendor `person.py:555-570`；`life_agent.py:512-532` | `record["observation"]=json.dumps(obs)` 且 `record["data"]=obs`；`build_react_messages` 原样 dumps |
| 睡眠债 1:1 | `catalog/resources.jsonl:3` | `awake 1 / recovery 2`，provenance `assumption` |
| 记忆模板泄漏 | `memory_runtime.py:55`；`runs/deepseek-day-affordance/as2/agents/agent_0002/MEMORY.md:4` | "Marketing analyst"；31/68 文件含残留 |
| 成本随时长线性 | `motivation.py:409-413` | `cost = (|money_now| + β·hours·2400)/1000` |
| 活动中工具门控 | `life_agent.py:1084-1113` | active 时无 purchase/consume/browse_activities/submit_work |
| lifestyle 采样 | `motivation.py:886-953` | 全库 ∩ MET 表，`attention_prior × 偏好(0.5)`，字母序决胜；docstring 自认启用集不在上下文 |
| eat 候选 | `motivation.py:531-568` | `key=(price_fen, wait, product_id)` 最廉 |
| 剧集语料 | `corpora/drama/items.json` | 10 部英文视觉小说（含 Sakura Succubus 系列） |
| system prompt | `life_agent.py:367-390`；vendor `person_prompt.py:14-16` | 协议文本替换了 "You are not an AI assistant…" 前言 |

## 附录 B：产物索引
- `/tmp/wm-realism/BRIEF.md` —— 派发给七个单元的共享简报
- `/tmp/wm-realism/A-game-ai/report.md` + 7 个脚本（decisions / hints / affordance_width / world_inventory / reasoning_vocab / body_model_compare / tokens_and_boundaries）
- `/tmp/wm-realism/B-psychology/report.md` + 5 个脚本（q / timeline_stats / memory_analysis / hints_and_reasoning / prefs_stats）
- `/tmp/wm-realism/C-sociology/report.md` + 6 个脚本（timeuse / reasoning_scan / coverage / co_presence / products / hints）
- `/tmp/wm-realism/D-llm-agent/report.md` + 6 个脚本（prompt_anatomy / message_composition / reasoning_analysis / reasoning_patterns / memory_loop / lifestyle_and_social）
- `/tmp/wm-realism/E-data-corpus/report.md` + 6 个脚本（01_inventory … 06_capacity_matrix，含离线重放动机评分器）
- `/tmp/wm-realism/F-presentation/report.md` + render_diary.py / obs_field_stats.py / diary_vocab.py / reasoning_attention.py + `renders/`（三种重渲染全文、盲评条带、metrics.json）
- `/tmp/wm-realism/G-quantitative/report.md` + analyze_runs.py + 8 个 CSV/JSON + 28 张 PNG（`body_<run>.png`、`evolution.png`）
