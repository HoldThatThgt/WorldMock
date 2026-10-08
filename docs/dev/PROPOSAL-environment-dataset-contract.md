# 环境数据集合同与引擎交互的差距分析与修改提案

> 状态：未冻结提案。本文只做差距分析与变更排序，不改变任何现行行为；现行行为以已冻结规格为准。
> 日期：2026-10-08。
> 代码基线：72f004b（main，分析时工作树干净）。
> **引用基线警告（2026-10-09 合并后补记）**：本文所有 `文件:行号` 引用反映**合并前基线 `72f004b`**。
> 2026-10-09 的合并删除了 `docs/CONTRACTS.md`（-2143 行）、`docs/ARCHITECTURE.md`、`docs/EXPERIMENT.md`、
> `docs/VERIFICATION.md`，并把 `tests/` 重组为 `unit/` `integration/` `system/` 三层。
> 失效清单与迁移去向见 Q25。
> 证据来源：九路只读分面审计（D1 载体与加载链路、D2 地点类型与地点、D3 app 模型、D4 商品与购买配送、
> D5 内容与消费方式、D6 动作空间与 target、D7 周期性状态与回调、D8 画像与模型配置、D9 记录导出与契约治理）
> 与一轮对抗性交叉验证。凡验证结论与取证相冲突处，本文以验证结论为准并在相应位置写明更正。
> §1.2 另收作者对 16 个决策点的答复与三份交付物（未涵盖清单、具体动作清单、地点类型替代设计）。
> 本次会话中作者就「闪频」词义、商品绑定互斥与其基数、到店/外卖判定方式、平台 app 归属、`location-type.json` 扩展名**五项**给出澄清，作为 §1.1 附于原文之后，并已并入 D2、D4、C4b、Q1、Q1b、Q5 与第五章。
> 取证复核发现的既有文档漂移（`README.md:87` 与 `CLAUDE.md:67-76` 的脚本清单、`docs/CONTRACTS.md:180` 的「八个
> 地点类型」、`docs/CONTRACTS.md:693` 与 `:1668` 的「五个商品类别」）在第三章逐条标注，因为按旧文改文档会误删
> `fuel`、驿站与两个地点类型。
> 相关文档：[接口合同](../CONTRACTS.md#contracts-worldmock-接口合同)、
> [环境数据归属规格](SPEC-environment-data-ownership.md)、
> [动作与内容简化规格](SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)、
> [记录引擎设计](../design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎)。

---

<a name="proposal-envdataset-心智模型原文"></a>
## 一、心智模型原文（逐字摘录）

以下为本次分析所依据的原文，一字未改；其中的列表层级、全角标点与用词（含「闪频」等疑似笔误处）全部保留，
不在原文中添加任何注释。原文的歧义在第六章作为待裁决事项单列。
作者已于本次会话就其中**五处**给出澄清（§1.1 澄清一至五），作为独立小节附于原文之后。

```text
我心中环境数据集和引擎的交互心智模型应该是：
环境数据集按顺序提供以下信息：
- 地点类型，其中persona的住宿地点类型必须包含，命名为location-type.json
- app catalog，persona在这次模拟的过程中需要使用的app名称，app分为内容app和功能app两种分类，功能app需要绑定到某个具体的动作，命名为app-catalog.jsonl
- 地点名称，地点名称必须绑定地点类型，可选绑定app，绑定app的地点可以支持persona远程下单购买地点类型下包含的闪频，命名为location.jsonl
- 商品catalog，persona在模拟中可购买的商品，系统固定提供四个分类[零食，饮料，食品，商品]，商品必须与某个分类绑定（其中零食，食品解决人物的饥饿需求，饮料，商品解决人物的心里需求，系统采用每个分类固定数值的方式维护persona消耗对应分类物品后persona状态维护），商品至少绑定一个地点类型/app：
	- 如果绑定地点类型中所有地点没有对应绑定的app，persona需要到地点后才能购买对应商品
	- 如果绑定地点类型有地点对应绑定app，persona购买该商品时根据persona当前的地理位置系统自动判定是到店购买还是外卖配送，没有绑定app的地点则需要到店消费
	- 如果绑定app，则persona的购买行为视为网购，即自动视为运送时长为 2-5天（由系统随机，数据集不配置），默认使用驿站领取流程
命名为merchant-catalog.jsonl
- 内容catalog，persona在app中可以消费的内容，内容至少绑定一个app，然后按照当前系统设计的消费方式推送给persona进行消费；内容需要声明消费时长，如果总时长超过30mins，需要以30mins为切分点提供每30mins消费的内容片段，用于系统做进度和记忆管理，命名为content-catalog.jsonl
- 商品&内容回调：支持环境集针对周期性消费商品/内容（如燃油，闹钟，水电费）注册额外的persona状态（仍然不引入库存系统），回调可以订阅系统提供的某个动作，在动作发生时变化状态，当状态到某个阈值时推送提醒persona再次消费商品/内容，但是如果persona没有购买这些商品也不影响persona的正常动作（比如车子出行需要燃油，商品中燃油可以绑定travel_to + by_car这动作，每天使用了车子后，燃油可支撑天数 - 1， 当可用燃油天数<3天时，每天在app可推送信息时间段内给persona推送加油通知，persona加油之后燃油可使用天数回到7天，但是如果persona确实没有加油，乘车出行不会被禁止），命名为item_callback.py（多个回调用一个文件承载）
- 人设信息，就是我们当前的人物画像文件，命名为persona.toml
- LLM接入信息以及一些重试配置，要求模型支持256k+上下文，命名为config.toml

系统侧提供：
出行，睡眠，购买，使用，四个动作类型 - 对LLM侧展现的是这四个类型下的具体动作
内容和功能两个app类型，内容app是"使用"这个动作下persona可见的app，而功能app则是和动作绑定，即persona使用app只能从内容app中挑选，而功能app则是系统在persona做出某个动作时自动触发的（比如，高德地图绑定到出行这个动作，那么persona在出行时自动绑定使用高德地图进行导航这个系统记录，支付宝/微信支付绑定到购买这个动作，那么persona在执行购买动作时系统自动随机选择支付宝/微信进行支付这个系统记录）
出行只能以地点为target，购买只能以商品作为target，使用只能以内容app为target - 需要注意的是，类似于美团外卖这样的平台app，persona也是可以浏览的，浏览的内容就是persona可以在美团外卖app购买的商品catalog，需要把这种关联方式写成manual让数据集侧设计数据时知道可以这么做）
```

### 1.0.1 本次会话新增的设计（agent 综述，非作者原文）

> **本节不属于上方摘录**。上方围栏是作者首条消息的逐字原文；以下内容由 agent 根据会话中作者的后续答复起草，
> 并经作者在 2026-10-09 明确「采纳」。逐条标注来源，以便区分作者原意与 agent 补充。

| 设计点 | 来源 |
| --- | --- |
| 接口由 `(target, way)` 改为 `(action, target)` | **作者**（2026-10-08：「是不是可以变成 (action, target) 就行了？」；2026-10-09：「1. 采纳」） |
| 动作集 = `walk`/`drive`/`take_taxi`/`take_bus`/`purchase`/`sleep`/`interact` | **作者**（2026-10-08 逐条给出四类动作与具体动作） |
| `interact` 的 target 可以是 app 或地点 | **作者**（2026-10-08：「如果 interact 的 target 是 app，那么就是使用 app…如果 target 是当前地点，那么内容就是比如跑步，工作这些」） |
| 工作、跑步是「内容」而非「动作」 | **作者**（同上：「跑步和工作我们都定义的是内容，不是动作」） |
| 内容可绑 `app_ids` 或 `location_kinds` | **agent 起草**（`location_kinds` 由 agent 提出，作者于 2026-10-09 采纳；类型级 vs 实例级绑定未讨论过） |
| 具体内容由系统按推荐逻辑随机选，persona 不能指定 | **作者**（2026-10-08：「返回的内容是随机的，persona 不能决定」） |
| 推荐优先序（推送→未完成→系列→未看→轮次最少） | **agent 补充**（复述 `content_select.select_content` 的既有实现，非作者原文） |
| 删除 `wait`/`do`/`collect_parcel` | **部分有据**：`wait` 有作者原话（「删掉，我老讨厌这个动作了」）；`do`/`collect_parcel` 仅有作者的反问（「之前不是讨论过…都要淘汰了吗」），**本会话无逐项留痕裁决**。见 Q24 |

### 1.1 作者澄清（2026-10-08，本次会话）

原文有**五处**在本次会话中被作者明确澄清（澄清一至五）。它们只影响**解释**，不改动上方摘录的任何字符。

**澄清一：「闪频」为笔误，实为「商品」。**

第 3 条「绑定app的地点可以支持persona远程下单购买地点类型下包含的闪频」中的「闪频」确认为笔误，应读作「商品」。
该条因此为：绑定 app 的地点支持 persona 远程下单购买**该地点类型下包含的商品**。第六章 Q1 随之结案。

**澄清二：商品绑定「地点侧」与「app 侧」互斥，但两侧各自可绑多个。**

第 4 条「商品至少绑定一个地点类型/app」中的「/」不是弱读的「或」，而是**两侧取其一**。要点有二：

1. **互斥的是「侧」，不是个数**：一件商品可以绑定**多个**地点（类型），也可以绑定**多个** app，
   但**不得**同时出现在两侧——不存在「一部分绑地点、一部分绑 app」的混合形态。
2. **每一侧的下界是 1**：地点侧非空则 app 侧必须为空，反之亦然；两侧同时为空即不合法。

两条分支的履约含义不同：

| 商品的绑定（每侧可多个） | 是否可远程下单 | 履约方式 |
| --- | --- | --- |
| 地点（类型）侧，且这些地点中**没有**任何地点绑 app | 否 | 必须到店 |
| 地点（类型）侧，且这些地点中**有**地点绑 app | 是，走「地图内远程下单」（闪送 / 外卖） | 由系统按 persona 当前位置在**到店 / 外卖**之间判定（见澄清三） |
| app 侧 | 不适用 | 只能网购快递：运送时长 2–5 天（系统随机，数据集不配置），默认走驿站领取流程 |

该澄清一并回答了原本的开放点：「绑 app = 快递」与「地点绑 app = 可远程下单」覆盖的是**互不相交**的商品集合，
因此第 3 条与第 4 条不构成冲突；商品侧也**不存在**「商品绑定的地点绑了 app 就算商品绑了 app」这种间接读法。

**澄清三：到店 / 外卖的判定是「位置是否就在该地点」，不是距离阈值。**

在澄清二的地点侧第二行里，「系统按 persona 当前位置判定」取**离散读法**：persona 当前所处地点若就是商品所绑的
地点之一，判**到店**；否则判**外卖（远程下单）**。不引入距离阈值，也不需要数据集提供任何参数。

> **与取证结论的差异（实施时必须对齐）**：作者认为现有系统即为该读法，但九路取证的一致结论是**现状根本不自动判定**——
> 请求侧已删除 `from` 与 `prefer`，履约渠道由 persona 显式写出的 `app` 决定（ACS P-16，
> `docs/dev/SPEC-action-and-content-simplification.md:84`；`docs/dev/SPEC-as2-integration.md:437`）。
> 因此本条不是「补一个分支」，而是要么把渠道判定交回引擎（需重开 P-16），要么维持人物点名并改写心智模型。
> 二者互斥，列入 Q1c，不在此处代选。

**澄清四：平台 app（美团外卖）是内容 app，两条路径并存且不冲突。**

原文末句的「美团外卖」确认为**内容 app**——只有内容 app 才能被「使用」动作选中，persona 才能在其界面里浏览到
商品 catalog；功能 app 不能被「使用」选中（见澄清一与 D3）。它同时可以作为**地点所绑的 app** 出现。于是在澄清二的
XOR 之下，两条路径并存且互不冲突：

- 商品绑定到**地点**，而该地点绑定了美团外卖这个 app → 购买该商品时运送方式为**外卖**（远程下单）；
- 商品**直接绑定到美团外卖这个 app** → 按 app 侧走**网购快递**（2–5 天 + 驿站）。

第六章 Q1b 据此结案。「写成 manual」是数据字段还是文档约定，仍见 Q14。

**澄清五：`location-type.json` 为笔误，实为 `location-type.jsonl`。**

第 1 条中的文件名为笔误，应与第 3、4、5 条一致使用 `.jsonl` 扩展名。§1.1 的两处笔误（「闪频」→「商品」、
`location-type.json` → `location-type.jsonl`）均只影响解释，不改动上方摘录的字符。



<a name="proposal-envdataset-裁决记录"></a>
## 1.2 作者裁决记录（2026-10-08）

本章记录作者对 §1.2.1 所提决策点的逐条答复。**裁决即设计输入**：凡与第三章取证结论冲突者，冲突以裁决为准、
取证以「现状」保留，第四章的变更项须据此重写。第六章各 Q 的结案状态见 1.2.4。

| 编号 | 裁决 |
| --- | --- |
| D1 | **八文件包要真建**，按该分类组织；设计未涵盖的内容见 1.2.1 |
| D2 | **四个动作类型成立**（出行/购买/睡眠/使用）；「使用」下**不再细分具体动作**——persona 只说「使用 + 内容 app」，具体动作由内容条目派生；`wait` **删除**（见 1.2.7-D2） |
| D3 | **到店/外卖由系统判定**，persona 不判定。persona 只声明「要买什么」这一边界；**用哪个 app 买、支付 app 是什么，都由系统随机决定**（种子定）——故不需要 `fastest`/`cheapest` 偏好层。「使用」的粒度为 **只写「使用 + 内容 app」，内容由系统推送**（1.2.8-Q1 取 a） |
| D4 | **功能 app 自动触发要做**；所有随机绑定到 `config.toml` 声明的种子，以固定重放 |
| D5 | **就是四分类**；`fuel` 属「商品」，其周期语义由回调维护 |
| D6 | **`drink` 走心理需求**；热量只由零食/食品承担 |
| D7 | 地点类型只为「商品在多处有售」而存在，须探索更简设计甚至**去掉类型**（见 1.2.3）。**取证修正（1.2.16 结论一/三）**：`kind` 字段删不得（六件事的载体），可删的只是 `PLACE_KIND_ENVIRONMENTS` 那张表；而「商品绑类型」事实上已由「同类型全绑或全不绑」不变量达成，缺的只是书写语法 |
| D8 | 见 D7（粒度问题随 D7 的设计一并消解） |
| D9 | **声明式阈值规则**，回调暴露越少越好 |
| D10 | 优先**引擎侧派生**（见 1.2.4 对进度总结的分析） |
| D11 | `persona.toml` **替换**场景 `background_story` |
| D12 | `config.toml` 放非密参数 + **256k 加载期断言** |
| D13 | **无油不阻断**，只持续推送提醒 |
| D14 | **不要求按顺序提供**，齐全即可 |
| D15 | `manual` 指 `docs/manual/` 下教使用者构造环境数据集的文档 |
| D16 | **修**既有文档漂移，保持一致性 |
| D17 | **删除 `set_alarm` 与 `send_message` 两个工具**（见 1.2.11） |
| D18 | **收敛唤醒原因** |
| D19 | **回调的定位是给 persona 减负**：多阶段现实行为压成一次选择，其余由系统记足迹（见 1.2.13） |
| D20 | **终裁：删 2 个死成员，`WakeReason` 枚举整个去掉**，改用「触发字段即身份」（见 1.2.14）。**不保留占位**，旧格式不兼容 |
| D21 | 回调接口 = **一张声明式规则表 + 一个求值器**，无 per-app/per-action 函数（见 1.2.15） |
| D22 | **废弃 `actions.json`**，其内容并入 `content-catalog.jsonl`；`Content` 承接 `category`/`manner`，`action_id` 消失（见 Q26 与 §1.2.18） |
| D23 | **删除 `communication_mode`**（随社交路径一起移除） |
| D24 | **`wait` 删除已定**（作者：「删掉，我老讨厌这个动作了」）；删除面见 Q27 |
| D25 | **不保留任何 idle 兜底原语**（作者：「不保留，直接删除，耗尽就查问题，不要用兜底掩盖」） |
| N1 | **地点条目可声明数量**，系统按声明在地图放置 N 个实例；对 persona 展示为「地点名称-地址」（地址设计见 1.2.5） |

<a name="proposal-envdataset-d1-未覆盖"></a>
### 1.2.1 D1 交付物：心智模型未涵盖的内容

以下项目在八文件包的设计里**没有归属**，但模拟跑起来必然需要。按「缺了就跑不动」的程度排序。

| # | 未涵盖项 | 为什么必须有 | 建议归属 |
| --- | --- | --- | --- |
| U1 | **地址生成规则**（N1 直接引出） | 声明了数量就要给出可区分的实例标识与展示名 | **已定**：系统按种子生成，见 1.2.5 |
| U2 | **多实例的选择规则** | 数量 >1 时，persona 说「去便利店」指哪一个？ | **已定**：persona 必须声明「地点名称-地址」，不存在歧义，见 1.2.5 |
| U3 | **营业时间 / 可进入时段** | 22:00 去餐馆是否受理？ | **已定**：`locations.jsonl` 新增字段，见 1.2.5 |
| U4 | **售罄与供给上限** | — | **已定：不存在**。无库存系统，故无售罄/供给上限，须在合同里写明这是有意省略 |
| U5 | **天气** | 影响出行方式与时长 | **已定**：**系统随机**，种子绑定 `config.toml`；**仅作为「出行基础速度 × 天气系数」的挂件**。天气**是否**影响周期性状态（如驾车日）**不是系统决定的，而是由数据集自己的回调订阅什么决定**——系统只提供订阅能力（见 1.2.9） |
| U6 | **交通方式与时长的完整集** | 现只有步行/驾车（`operators.py:88` 硬限两种） | **已定**：四种 = **步行 / 公共交通 / 打车 / 驾车** |
| U7 | **地图与坐标** | 出行时长依赖距离；声明数量的地点需要落点 | **已定**：边有长度，每方式「基础速度 × 天气系数」；坐标系系统内维护，见 1.2.5 |
| U8 | **随机源清单** | D4 要求「所有随机绑定种子」 | **已定**：全部并入 `config.toml` 的**单个种子**，清单见 1.2.6 |
| U9 | **persona 初始状态** | 初始余额、体力、情绪、位置、有无车 | **已定**：归 `persona.toml` |
| U10 | **其他 persona 与消息** | 消息、通话、社交对象从哪来 | **已定：不做**。聊天按工作处理——只给一个 topic（聊了什么、进展如何由 persona 自己在 prompt 提示下编） |
| U11 | **模拟终止条件与时长** | 跑几天？何时结束？ | **已定**：由 `config.toml` 声明启动时间与停止时间（精确到小时） |
| U12 | **失败与降级路径** | 模型调用失败、目标不可达时 persona 看到什么 | **已定**：一套简单的降级重试系统，重试耗尽即停止运行。不要复杂化 |
| U13 | **版本与升级路径** | — | **已定：不维护兼容性**，旧格式一律失效 |
| U14 | **观测与隐私边界** | 见 1.2.7-U14 的解释 | **已实现，非缺口**：只需在合同里写明范围 |
| U15 | **单位与货币精度** | — | **已定**：沿用现有精度（`price_fen` 等） |

<a name="proposal-envdataset-d2-动作清单"></a>
### 1.2.2 D2 交付物：persona 可见的具体动作

四个**动作类型**固定；LLM 侧看到的是类型下的**具体动作**。现行闭集见
`src/worldmock/exchange/types.py:112-114`（六个起始动作）与 `src/worldmock/life/vocabulary.py:9-13`（方式表）。

| 动作类型 | persona 可见的具体动作 | target | 系统侧自动绑定 |
| --- | --- | --- | --- |
| **出行** | 步行前往〈地点〉／公共交通前往〈地点〉／打车前往〈地点〉／驾车前往〈地点〉 | **地点**（唯一） | 数据集把导航类功能 app 绑定到**具体动作**（如高德 → 步行、驾车）才触发；未绑则不记 app |
| **睡眠** | 睡觉 | **地点**（home） | 无 |
| *（四类之外）* | `set_alarm` 设一次性闹钟 | 触发时刻 | 无（persona 原生请求，见 1.2.10） |
| **购买** | 购买〈商品〉；领取〈快递〉 | **商品**／待领订单 | 数据集把支付类功能 app 绑到**具体动作**才触发；**具体选哪个由系统按种子随机** |
| **活动** | **interact（进行活动）** | **app 名或地点名** | 无（见下方说明） |

**活动类的特殊说明**：

原心智模型使用「使用」作为第四类，本提案改为「活动」（interact），理由如下：
1. **target 的双重语义**：
   - target 是 app：persona 说「我要用抖音」→ 系统从抖音绑定的内容中按推荐逻辑随机选一个
   - target 是地点：persona 当前在公园，说「我要在公园（进行活动）」→ 系统从公园绑定的内容中随机选一个（如"跑步"）
2. **内容的两种绑定**：
   - 绑定 app 的内容（看剧、游戏）：`content.app_ids = ["douyin"]`
   - 绑定地点类型的内容（工作、跑步）：`content.location_kinds = ["office"]` 或 `["park", "fitness"]`
3. **工作、跑步是内容而非动作**：它们是可消费的内容条目，通过 interact 触发，而不是独立的动作类型

映射到现有词表：

- 出行 ＝ `travel_to`（way ∈ {步行, 驾车, 公共交通, 打车}）；**新增公共交通与打车两种**；现行还额外收紧为「场景已选地点」，须裁决是否放宽（Q15）。
- 睡眠 ＝ `sleep`，target 是地点（home）。
- 购买 ＝ `purchase` ＋ `collect_parcel`（收货归入购买类）。
- 活动 ＝ **新增 `interact`**；target 可以是 app（使用 app）或地点（在地点进行某活动）；**具体内容由系统按推荐逻辑推送**。
  内容条目仍须携带**效果档案**（见 1.2.7-D2），否则体力系数、地点资格、推送资格无来源。
- **`wait`（等待）无归属**：现行六个起始动作里它不属于四类中任何一类。建议删除（已在 D2 裁决中确认删除）。
- **`do`（进行）的归宿**：当前 `do` 承载所有内容消费，新模型下改为 `interact`，语义更清晰。
- **`actions.json` 的去留（已裁决 D22）**：该文件**废弃**，其内容并入 `content-catalog.jsonl`。
  理由：`actions.json` 装的是数据集定义的**具体活动**（刷推荐、办公、跑步、学课程），而新设计里这些就是**内容**；作者最初列的八文件包里**本来就没有 actions 文件**。
  连带：`Content` 承接 `category`/`manner`（它们仍是承重的——体力系数、运动门禁、推送资格都挂在上面），
  `Content.action_id` 字段消失（内容自己就是那个活动）。详见 §1.2.18。
- **`communication_mode` 删除（已裁决 D23）**：U10 定了不做其他 persona、聊天按工作处理，D17 又删了 `send_message`，社交路径已无，该字段随之移除。连带 `ActionCategory.SOCIAL` 的去留见 Q26。

**接口变更（详见 Q21）**：
- 当前：`{"target": "X", "way": "进行"}` → 系统查 action 目录
- 新模型：`{"action": "interact", "target": "抖音"}` 或 `{"action": "interact", "target": "公园"}`
- 出行方式：`{"action": "walk", "target": "彩虹桥"}` 替代 `{"target": "彩虹桥", "way": "步行"}`

<a name="proposal-envdataset-d7-地点类型"></a>
### 1.2.3 D7 交付物：地点类型的替代设计

**问题的本质**：类型在数据集里承担的**唯一**职责，是让一件商品不必逐个商店声明。而 N1 已经给出更直接的答案——
**地点条目本身声明数量，条目就是那个集合**。类型因此不再有独立语义。

推荐设计（标记为**建议**，待作者确认）：

```text
location.jsonl 每条 = 一个可售分组
  - name         地点名称（分组名）
  - kind         系统枚举（行为属性：环境、可售类别、气象响应）
  - count        在地图上放置的实例数，默认 1
  - app_ids      可选的 app 绑定（决定能否远程下单）
  - opens        可选的营业时段
商品直接绑 location 条目的 id —— 自动覆盖该条目的全部 count 个实例。
展示名 = "地点名称-地址"，地址由系统按带键随机流生成。
```

由此：

- **D8 的「类型 vs 实例」二选一自然消失**：只有条目 id 一种绑定。
- 货要出现在两类地点（薯片在便利店与超市）→ 绑两个条目，与今日 `store_ids: [...]` 同形。
- **`kind` 保留但降级**：它仍是 `GOODS_CATEGORY_STORE_KINDS`（哪类地点能卖哪类商品）与
  `PLACE_KIND_ENVIRONMENTS`（室内/室外）的载体，只是**不再由数据集文件定义类型集合**。
- `location-type.jsonl` 二选一：
  - **(a) 去掉**：`kind` 作为 `location.jsonl` 条目的必填属性，取值是**数据集自由文本**（已按 Q23 裁决；推荐，类型已无独立语义）。
  - (b) 保留但改义：声明本环境用到哪些 `kind` 及每 `kind` 的属性。若采纳，属性应挂在**条目**上（各店营业时间不同）而非类型上。





<a name="proposal-envdataset-回调节点"></a>
### 1.2.9 回调能订阅什么（答复「能不能订阅天气、能不能订阅别的事件发 hooks」）

先答结论：**回调不能订阅「天气」，因为天气目前不是一个事件；但回调能订阅的事件面比现在用到的宽得多，而推送通道已经存在。**

**（1）可订阅的事件面：8 种系统排期事件**

`ScheduledKind`（`src/worldmock/scheduling/events.py:16-29`）是环境自主到期事件的**封闭集合**，8 个成员：

| 事件 | 用途 |
| --- | --- |
| `mail_arrival` | 邮件到达 |
| `delivery_arrival` | 配送到达 |
| `calendar_flag` | 日历特殊日 |
| `bill_due` | 账单到期 |
| `payroll` | 发薪 |
| `message_arrival` | 联系人回复送达 |
| `app_push` | 应用整点推送机会 |
| `alarm_ring` | 本人设置的一次性闹钟 |

**（2）可订阅的唤醒面：23 种唤醒原因**

`WAKE_REASON_NAMES`（`src/worldmock/footprints/models.py:40-50`）是冻结词表，23 个成员，涵盖
`planned_end`、`felt_band_change`、`message_arrival`、`delivery_arrival`、`commitment_threatened`、
`reconsideration_interval`、`person_arrived`、`person_left`、`activity_infeasible`、`rejection_feedback`、
`discovery_result`、`wake_from_sleep`、`run_start`、`progress_report_due`、`no_active_activity`、
`plan_reminder_due`、`errand_reminder_due`、`shift_start`、`shift_end`、`break_start`、`break_end`、
`app_push`、`alarm_ring`。

**这是比「动作发生」丰富得多的订阅面**：它包含时间类（班次开始/结束、休息开始/结束）、状态类（体感越界、
承诺受威胁）、社会类（人到达/离开）、失败类（活动不可行、请求被拒）。若回调允许订阅唤醒原因，
则可表达「人在到岗时检查燃油」「体感越界时提醒喝水」这类规则。

**（3）推送通道已存在**

推送不是需要新建的能力：`ScheduledKind.APP_PUSH` 是「应用整点推送机会」，可推送时段是
`PUSH_HOURS_LOCAL = range(8, 22)`（`src/worldmock/exchange/constants.py:36`，当地 08:00–21:00 整点）。
回调要达到「到阈值时推送提醒」的效果，**复用这个通道即可**，不需要新的通知机制。

**（4）要做「订阅天气」，前提是先把天气做成事件或状态**

这是本节最实际的一条：**天气今天既不是 `ScheduledKind` 成员，也不是已注册的世界状态**，
所以「把回调订阅到天气」在现有结构里无处落笔。两条路：

- **(a) 把天气做成每日排期事件**（如新增 `ScheduledKind.WEATHER_SHIFT`，或接入既有 `calendar_flag` 的形态）：
  这样「订阅天气」就是订阅一个已有事件，与订阅 `bill_due` 同构，改动最小。
- **(b) 把天气做成一个世界状态值**（像 `driving_days` 那样）：回调按「状态值变化」触发，而不是按事件触发。
  这需要回调支持「状态订阅」这一新触发类。

**结论**：回调的**订阅方式**应支持四类触发—— **动作触发 / 时间触发 / 状态阈值触发 / 事件触发**。
天气只是「事件触发」或「状态触发」的一个实例，不是特例。按作者 D9「回调暴露越少越好」的要求，
推荐把订阅键统一表达为 `(触发类, 键)`，其中事件类的键取自上面的 8 个 `ScheduledKind`，
状态类的键取自已注册状态（含 `driving_days`）。

<a name="proposal-envdataset-闹钟"></a>
### 1.2.10 闹钟怎么真的生效（答复「persona 想设闹钟怎么办」）

**已有的实现，不需要新设计。** 闹钟是 **persona 原生的一次性排期动作**，与功能 app 无关。

链路（全部已存在）：

1. **persona 可调用**：`set_alarm` 已在世界请求工具名单里——
   `src/worldmock/agents/life_prompt.py:28`（工具枚举）、`:62`（提示文字：「在某个时刻起来，可以用 set_alarm 设置一次闹钟」）、
   `:86`（工具描述）；注册处 `src/worldmock/life/models.py:1322`。
2. **受理与排期**：`src/worldmock/life/alarm.py:15` 的 `set_alarm` 校验时刻必须是「未来且在本轮结束之前」的整秒，
   否则以 `ALARM_TIME_INVALID` 拒绝；通过后创建一条 `ScheduledEvent(kind=ScheduledKind.ALARM_RING)`
   （`alarm.py:29`），身份为 `alarm-{action_id}`，**一次性、不可复用**（`alarm.py:25-26` 对同身份不同请求抛 `ProtocolError`）。
3. **到期生效**：`src/worldmock/scheduling/effects.py:44-45` 在 `ALARM_RING` 到期时返回唤醒原因 `alarm_ring`；
   `src/worldmock/life/time_advance.py:266` 记录 `interruption_reason=alarm_ring` 并结束当前活动。
4. **能唤醒睡眠**：`ScheduledKind.ALARM_RING` 的注释明确写「本人设置的一次性闹钟，**到期可唤醒睡眠**」（`events.py:28`），
   提示文字也说「睡着时只有提醒、闹钟或睡够了才会醒」（`life_prompt.py:62`）。

**所以「persona 想设闹钟」的答案是**：persona 调 `set_alarm(at=...)`，环境排一条 `alarm_ring`，
到期把它唤醒。它**不进 app catalog，不被数据集绑定，也不挂在「睡眠」动作下**——它是四类动作之外的
一条 persona 原生请求。§1.2 表格中原先写的「闹钟（功能 app，绑到『睡眠』）」是错的，已修正。




<a name="proposal-envdataset-第二轮取证"></a>
### 1.2.16 第二轮取证结论（U14 可见清单 + D7 五条质疑的核实）

九路取证之外的第二轮：5 路并行取证 + 1 轮对抗性核查（含对 `WorldMock-Dataset` 执行记录的检索）。
**结论有三条推翻了本文先前的写法，一条推翻了作者 D7 判断的一半。**

#### 结论一：`kind` 不能删，但「室内/室外」那一层可以换掉

**`PLACE_KIND_ENVIRONMENTS` 恰有一个真实消费者**：`src/worldmock/life/rules.py:57` 的**运动活动地点资格判定**
（经 `action_run.py:55` 与 `tool_layer.py:243` 两条入口）。home/park/fitness 通过，office 与六种 venue 被拒并抛
`ELIGIBILITY_LOCATION_KIND_MISMATCH`。所以「室内/室外会不会改变模拟结果」的答案是**会，但只限「运动能不能在这里做」这一件事**——
不改变出行、不改变结算、不改变身体模型，也**不与天气发生任何关系**（全仓 Python 零 weather 命中）。

**但 `kind` 字段本身删不得**，它是六件事的共同载体：交互菜单（`interaction_menu.py:55-58`）、
人设提示里的场所中文名（`environment_layer.py:63-66`）、事件级地点目录（`observe.py:420`）、
商品菜单与目录准入（`loader.py:85`、`admission.py:80,83,90,97,220`）、场景菜单全覆盖校验（`scenario.py:377`）、
加载校验（`load_checks.py:42-48`）。删字段会直接坏掉运行。

**可删的只是那张表**：单独删 `PLACE_KIND_ENVIRONMENTS` 只坏三处代码 + 两处测试。
**正确做法**是把「可运动场所」改成从 `PLACE_TYPES` 或一个显式的可运动集合派生，
而不是保留这张把 venue 一锅端的映射表。另注：`candidates.py:336-351` 那条地点类过滤路径
因 `observe.py:221` 恒传 `moment=False` 且 `apply_location_kinds` 全仓从未置真，属**计算了却不可达的死语义**。

#### 结论二：菜单机制今天是空转的，且原理上无法表达「办公只在工作地点」

`interaction_menus_by_location_kind` 是**场景文档**的必填映射（键=10 个 `LocationKind`，值=`allowed_actions`），
取值只能来自 **6 个内部规范动作**，经 `resolve_interaction_menu` 与人物菜单求交后在三层硬拒绝
（宿主 `life_agent.py:491`、工具层 `tool_layer.py:89-91`、授权结算 `menu_execution.py:10-19` 抛 `INTERACTION_MENU_DENIED`）。

**两个关键事实**：

1. **今天对模拟结果零影响**：三份场景夹具都为本 10 个地点类声明同一份全集
   `["collect_parcel","do","purchase","sleep","travel_to","wait"]`，且所有 `agents[].interaction_menu` 都是 `null`，
   有效菜单处处等于全集，剪裁为零。
2. **原理上无法表达有意义的地点约束**：菜单的判定单位是**规范动作名**，而 `do` 一个名字同时承载办公、跑步、看剧、游戏。
   所以「工作学习只在工作地点」这类约束**不可能**用菜单写出来。

**真正把「做什么」绑到地点的是两条硬编码规则**，与菜单无关：

- 运动类动作只在 home/outdoor/fitness 可行（`rules.py:53-61`，测试固定 `tests/integration/life/test_action_run.py:136`）；
- 指派工作内容要求人在 `workplace_location_id`，缺席以 `ACTIVITY_PRECONDITION_UNMET` 拒绝（`admission.py:109-119`、`:166-179`，
  测试固定 `tests/integration/life/test_action_run.py:120`）。

**所以「我们的动作什么时候和地点绑定了」的答案是：从来没有，在作者关心的粒度上。**
菜单只在「6 个规范动作 × 10 个地点类」这一层绑定，且今天恒为全集；有意义的约束硬编码在系统里。
这与作者「功能 app 绑定什么动作由环境数据集定义，系统只提供能力」的主张**方向一致**，
但要落地就得新建一条「**数据集动作 → 地点集合**」的目录级声明——`menus_by_kind` 是商品索引且无生产消费者，不能复用。

**附带的文档漂移（第三、四处）**：`docs/CONTRACTS.md:180` 与 `docs/dev/SPEC-interaction-menu.md:78` 写「八个 `LocationKind`」，
实际十类；`docs/dev/SPEC-interaction-menu.md:45` 列标准动作闭集只有 5 个，实际 6 个（缺 `collect_parcel`）。

#### 结论三：商品的类型级绑定**事实上已经达成**，缺的只是语法

商品侧今天**唯一**的声明字段是 `Goods.store_ids`：地点实例 id 的有序去重元组，或字面量 `"all"`。
`"all"` 不是「所有地点」，而是「**类型能供给该类别的全部店铺**」（`loader.py:83-88`，
判别表 `GOODS_CATEGORY_STORE_KINDS` 是引擎固定常量）。

但登记期有一条不变量：**同类型店铺「全绑或全不绑」**（`admission.py:99-101`，否则 `GOODS_BINDING_KIND_INCONSISTENT`）。
于是**实例级列举在效果上等于类型级声明**——`menus_by_kind` 正是架在这条不变量上。

**真实数据的书写结果印证了这点**：`WorldMock-Dataset/environment/shanghai-simplified/catalog/goods.json`
共 157 件商品，**152 件用显式实例列表，长度分布 26/50/60**，仅 5 件用 `"all"`。
即绝大多数商品是把 50–60 个 `location_id` **逐字写进** `store_ids`。

**结论**：作者要的「商品绑地点类型、不必逐店列举」在**语义上已经成立**（靠那条不变量），
今天缺的是**书写语法**。新增 `store_kinds` 字段（C4）是把隐式等价变成显式语法，
而非引入新能力——这降低了 C4 的风险等级。

#### 结论四：「每个环境集每种文件只有一份」**今天不成立**

正式数据仓环境 `shanghai-simplified` **同时存在** `catalog/goods.json` 与 `sources/catalog/goods.json`，
且 `apps/locations/goods/actions/contents` 五个文件**全部逐字节不同**；该环境根另有 10 个场景与
`sources/scenarios/`、`sources/corpora/`。

`docs/CONTRACTS.md:49`/`:57` 的「环境根只有 `catalog/` 与 `scenarios/`」是**文档声明而非被校验的不变量**：
`src/worldmock/validation.py:117-134` 只读 `root/catalog` 与 `root/scenarios`，对根下其他目录既不读取也不拒绝，
`tests/test_validation.py:97-104` 甚至把这一点固化为行为断言。被封闭的只有 `catalog/` 内部（多一文件即 `ENVIRONMENT_STALE_FILE`）。

**作者 N1 那条「每个环境集应该都只有一份」据此需要裁决**：是要**新增校验**把它变成不变量（推荐，
否则 `sources/` 里的影子目录会持续造成「改了一份、跑的是另一份」的风险），还是维持现状的宽松。

#### 结论五：天气从未实现，且模型自造的天气会回灌形成自证循环

**引擎侧天气零命中**：`src/worldmock` 与 `custom` 全量 grep `天气|weather|气温` 为 **0**；
观察 schema 49 个键里无 weather 类字段；环境数据无天气声明。模型请求里出现的 `天气|outdoor|室内|室外` 亦为 0。

**模型输出里的「天气」全部是它自己编的**，来源有三：自造（2026-10-06 week run:1220 `"妈，……你们那边天气转凉了记得添衣。"`）、
短剧字幕（`{"text": "[spk12] 苏母，今天外面天气好。"}`）、以及语料段子。
**且自造内容会被写进记忆再回灌 prompt**（`memory_updated` 里的「问问家里天气、叮嘱添衣」又出现在后续 prompt），
**形成自证循环**。

**关于室内/室外**：`location_kinds` 写进了 observation 证据段，却**从未进入 model_request 的 messages**
（3065 条记录，0 命中）；模型看到的地点只有 `地点：彩虹桥` 这种裸名。唯一一次把场所类送到模型眼前，
是 2026-09-28 早期 run 把它**写死在地点名字里**（`"青西郊野公园（户外活动环境）"`），2026-09-29 起已移除。

**所以作者 D7 的判断是对的**：模型从未「考虑过」室内/室外，因为它没有这个输入；
能观察到的只是模型自己从「不可用清单」反推（business run:2334）或靠地名猜（week run:5914）。
删掉它对**提示角度零损失**；但它仍有运行期资格门的作用（结论一）。

**对 U5 的影响**：天气是**全新特性**，不是对现有能力的修改。且落地时要顺带处理那条自证循环——
否则新加的天气事实会与模型自造的天气在记忆里并存，质量报告分不清哪个是环境给的。

#### U14 交付物：persona 现在实际能看到什么（清单）

**请求的物理组成**：原生 tools 定义 + 一条 system 消息（系统层 JSON，整次运行逐字节不变，18 条规则，**不写任何世界事实**）
+ 一条 user 消息（六层 JSON 数组）+ 同秒续接追加的 assistant/tool 消息。

**七层请求的完整形状**（权威定义 `docs/dev/SPEC-life-realism.md:566`，代码入口 `life_prompt.py:115`）：

| 层 | 键序 | 内容 | 来源 |
|---|---|---|---|
| system 消息 | — | 18 条规则（交互协议），**不写任何世界事实** | `life_prompt.py:21-104` |
| **工具层** | 1 | 分组紧凑 JSON：可达地点（出行方式、路费、图上时长、费力）、商品（单价、预计送达）、动作目标；每个动作带 `available`/`reason` | `tool_layer.py:80-86` |
| **人设层** | 2 | 人物卡片（identity/rhythm/habits/tastes/coping/relationships/preferences），派生字段合计 ≤600 汉字 | `life_persona.py:35-55` |
| **记忆层** | 3 | `earlier_days` 与 `earlier_today`；`settled` 系统句、`self_notes` 人物自述；窗口 8000 字符 | `memory_layer.py:117-386` |
| **待办层** | 4 | 暂停活动、个人事务、当日计划 | `todo_layer.py` |
| **环境层** | 5 | 体感**分级文字**（五组各五档）、当前地点名与场所类型中文标签、`fixed_rules`、`duty`（上班安排） | `environment_layer.py:31-66` |
| **进度层** | 6 | 本 session 的工具调用与结果、消息全文、推送与送达进展、截止与提醒推导 | `progress_layer.py` |

**黑名单（fail-closed，命中即抛错）**：`balance_fen`、`energy_kcal`、`sleep_pressure`、`sleep_deficit_hours`、
五项 `body.values`、`agent_id`、`activity_id`、`location_id`、`store_id` 等内部身份（`narration.py:46-62`）。

**看不到的（关键）**：

- **自己的余额**——`balance_fen` 在 `narration.py` 黑名单里，**fail-closed**（命中即抛错，不是过滤）；余额不足只能靠事后拒绝码 `INSUFFICIENT_FUNDS` 得知。
- **任何身体数值**——`energy_kcal`/`sleep_pressure`/`sleep_deficit_hours`/五项 `body.values` 全在黑名单，只给分级文字。
- **地点坐标与地图几何**、**室内/室外属性**、**内容目录全表**（只在该 app 有未释放推送时露标题）、**一切内部身份**。
- **天气**——不存在。

**改造注意**：要新增「室内/室外」「天气」这类观测，必须**同时**改环境层构造器、`prompt_schemas` 的环境层 Schema、
提示指纹、以及系统层文案里对应的白话规则；否则 persona 会拿到一个它没有语言去理解的新字段（fail-closed 会抛错）。

<a name="proposal-envdataset-唤醒真合并"></a>
### 1.2.14 唤醒原因的真实合并（答复第二轮追问）

上一节（1.2.12）只做了分组，**数量没有减少**——作者指出得对。本节给出真正的合并路径。

**发现一：两个成员已经是死成员（且已裁决删除）。**

`WakeReason` 的 23 个成员里，**`COMMITMENT_THREATENED` 与 `ACTIVITY_INFEASIBLE` 在全仓 `src/` 下
`WakeReason.*` 引用数均为 0**，且 `"commitment_threatened"` / `"activity_infeasible"` 字面量除
`src/worldmock/footprints/models.py:42`、`:44` 的词表登记外**没有任何生产者**。
二者既不在 `_event_reasons`、也不在 `_clock_reasons` 的产出里。
**作者裁决：删除，不保留占位。**这两个名字看起来像未实现特性的预留（「承诺受威胁时唤醒」
「活动不可行时唤醒」），但既然无生产者、统计恒为 0，保留只增加维护面。若日后确需，按新增流程重加即可。

**发现二：`WakeReason` 与 `WakeSnapshot` 的字段是近 1:1 重复。**

对比 `src/worldmock/life/schedule.py:194-230`（`WakeSnapshot` 的字段）与 `:51-79`（`WakeReason` 的成员）：
`_event_reasons`（`:375-394`）与 `_clock_reasons`（`:397-419`）是**逐字段直译**——每个成员都对应一个已有别名的快照字段：

| 快照字段 | 对应成员 |
| --- | --- |
| `planned_end` | `PLANNED_END` |
| `felt_bands_now` / `felt_bands_at_last_decision` | `FELT_BAND_CHANGE` |
| `message_arrived` | `MESSAGE_ARRIVAL` |
| `delivery_arrived` | `DELIVERY_ARRIVAL` |
| `people_present_now` / `people_present_before` | `PERSON_ARRIVED`、`PERSON_LEFT` |
| `discovery_result_ready` | `DISCOVERY_RESULT` |
| `wake_from_sleep` | `WAKE_FROM_SLEEP` |
| `run_start` | `RUN_START` |
| `progress_report_due` | `PROGRESS_REPORT_DUE` |
| `no_active_activity` | `NO_ACTIVE_ACTIVITY` |
| `plan_reminder_due` | `PLAN_REMINDER_DUE` |
| `errand_reminder_due` | `ERRAND_REMINDER_DUE` |
| `duty_edges_now` | `SHIFT_START`、`SHIFT_END`、`BREAK_START`、`BREAK_END` |
| `app_push_arrived` | `APP_PUSH` |
| `alarm_rang` | `ALARM_RING` |

**这就是「控制不住」的真正原因**：同一件事被命名了两遍，改一处要同步另一处，而两侧还由测试互锁防漂移。

**合并方案（已裁决，2026-10-08 第二轮）：不要维护第二套词表，让触发字段本身成为身份。**

即：**删掉 `WakeReason` 枚举作为独立词表**，边界记录里直接落**触发字段名**（`planned_end`、`app_push`、……）。
身份既然已由产生它的字段携带，枚举就是冗余。其余两件事：

1. **类别（供统计与订阅）改为派生属性**：由一张**静态表**（触发字段 → 6 类）算出，不是第三个词表。
   统计（`footprints/quality.py:358`）与订阅（1.2.9）都读这张派生结果。
2. **睡眠许否改为逐触发器的布尔标志**（现为 `SLEEP_ALLOWED_REASONS` 成员资格，`schedule.py:82-85`）。
   标志跟着触发器定义走，不再需要单独维护一个 frozenset 并保持与词表同步。

**数量对比**：

| 口径 | 数量 |
| --- | --- |
| 现状成员 | **23** |
| 删 2 个死成员 | 21 |
| 删 2 个随工具下线（`alarm_ring`、`message_arrival`，见 1.2.11） | 19 |
| 删 2 个随 U10 下线（`person_arrived`、`person_left`） | 17 |
| **4 个班次边界合成 1 个**（`duty_edges_now` 本就是一个集合字段，按组消费、无一可唤醒睡眠） | 14 |
| **`wake_from_sleep` + `run_start` 合成 1 个会话开启** | 13 |
| **两个提醒合成 1 个**（`plan_reminder_due` + `errand_reminder_due`） | **12** |
| **维护的枚举成员**（若采纳「字段即身份」） | **0**（只剩派生出的 6 个类别） |

**诚实说明下限**：`planned_end`、`app_push`、`delivery_arrival` 这类**性质确实不同**的条件不能硬合，
合了会丢信息。所以「不同条件」的下限约 **12**；能压到 6 的是**维护的枚举成员数**，不是条件数。
两个数字别混为一谈——“数量降下来”在枚举口径下降到底，在条件口径下降 23→12。

**上一节 `person_arrived`/`person_left` 的问题据此结案：删除。**


<a name="proposal-envdataset-枚举删除面"></a>
#### `WakeReason` 枚举删除的改动面（已裁决执行）

删除枚举不是「少写一个类」，而是把身份的载体从枚举换到字段，涉及以下位置：

| 位置 | 改法 |
| --- | --- |
| `src/worldmock/life/schedule.py:51-79` | 删 `WakeReason` 类 |
| `src/worldmock/life/schedule.py:82-85` | `SLEEP_ALLOWED_REASONS` 改为逐触发器布尔标志（挂到触发器定义上） |
| `src/worldmock/life/schedule.py:375-419` | `_event_reasons` / `_clock_reasons` 返回值由枚举改为**触发字段名** |
| `src/worldmock/life/schedule.py:88-90` | `DUTY_EDGE_REASONS` 四个班次成员合成一个 `duty_boundary` 字段 |
| `src/worldmock/life/duty_edges.py:20`、`:46-55` | 产出改为字段名 |
| `src/worldmock/footprints/models.py:40-50` | `WAKE_REASON_NAMES` 词表**删除**（不再需要第二套词表） |
| `src/worldmock/footprints/quality.py:22`、`:358` | 统计改读字段名 + 静态表派生类别 |
| `src/worldmock/life/observe.py:209`、`rhythm.py:169`、`decision_coordinator.py:424-436` | 落字段名 |
| `models.py:35` | `WORLD_SCHEMA_VERSION` 11 → **升号**（旧检查点与旧导出按版本拒绝） |
| 两侧互锁测试 | 随词表一并删除 |

**类别表（派生，非词表）**：`time_boundary` / `duty_boundary` / `reminder_due` / `event_arrival` /
`state_change` / `outcome_feedback` 六类，由静态映射从触发字段算出，供质量统计与回调订阅（1.2.9）读取。

**顺带回答 1.2.12 留下的问题**：`person_arrived` / `person_left` **已裁决删除**（随 U10 不做其他人物）。

<a name="proposal-envdataset-回调接口"></a>
### 1.2.15 回调的接口定义（答复第二轮追问）

**先纠正提问本身：这个二分不成立。** 回调不是「每个 app 一个函数」「每个动作一个函数」或「每个事件一个函数」——
**一个函数都不写**，因为 D9 已定为声明式。正确的说法是：**一张声明式规则表，一个求值器**。

三者的关系：

- **app 是值，不是键**——规则里引用 app，规则本身不按 app 组织；
- **「按动作」是触发器的一类**，不是全部；
- **「按事件」是触发器的另一类**，也不是全部。

**统一形状：一条规则 = `(触发器, 效果)`**，触发器取四类之一：

| 触发类 | 键的取值 | 用途 |
| --- | --- | --- |
| `action` | 具体动作（`驾车`、`打车`、`购买`、`睡觉`…，见 1.2.8 全表） | 动作发生时变化 |
| `time` | 整点/每日/运行起止（承接 `PUSH_HOURS_LOCAL` 与 U11 的启停时刻） | 周期性变化 |
| `state` | 已注册状态名（含 `driving_days`） | 状态越阈值时变化 |
| `event` | `ScheduledKind` 的 8 个成员（见 1.2.9） | 系统事件到达时变化 |

**效果也取三类**——这正好覆盖你给的两个例子：

| 效果类 | 语义 | 对应场景 |
| --- | --- | --- |
| `state_delta` | 改一个已注册状态（可选带阈值与提醒） | **燃油**：`驾车 → driving_days -1`，`< 3 天` 时经 `app_push` 提醒 |
| `footprint_chain` | **写 N 条足迹、0～1 条可见提示** | **打车**：`打车 → [浏览足迹（选车 app）, 支付足迹（支付 app）+ 资源流]` |
| `transfer` | 跨资源转换（走 `resources/ledger.py` 的原子转换） | 出行 → 货币支出 |

**于是「燃油」与「打车」是同一张表的两行**，只是效果类不同——不是两套机制。规则草案：

```jsonc
// 一行 = 一条规则；规则表本身是数据，不是代码
{
  "rule_id": "fuel-weekly",
  "trigger": {"class": "action", "key": "驾车"},
  "effect": {"class": "state_delta", "state": "driving_days", "delta": -1},
  "notify": {"when": {"state": "driving_days", "below": 3},
             "channel": "app_push", "text": "该加油了"}
},
{
  "rule_id": "ride-hailing",
  "trigger": {"class": "action", "key": "打车"},
  "effect": {"class": "footprint_chain",
             "steps": [
               {"kind": "browse", "app_pool": ["amap", "didi"], "pick": "random"},
               {"kind": "payment", "app_pool": ["alipay", "wechat"], "pick": "random"}
             ]},
  "notify": {"visible": false}   // persona 只看到余额减少
}
```

**关键性质**：

- **无用户代码**：数据集只填键与参数，语义与求值归引擎。满足 D9「暴露越少越好」，也不碰
  「数据仓不得携带回调脚本」的禁令（`docs/dev/SPEC-action-and-content-simplification.md:320`、`docs/ARCHITECTURE.md:270`）。
- **`app_pool` + `pick: random`**：功能 app 的自动匹配在这里落成，抽样走带键随机流（1.2.6 的 R 系列），可重放。
- **`footprint_chain` 复用既有能力**：`visibility=system`（`scheduling/effects.py:72`）与 app 事件的
  `environment` 来源校验（`footprints/app_usage.py:114-153`）都已存在，足迹落在既有契约内。
- **可校验**：规则表在加载期可做 fail-closed 校验（键必须存在、状态必须已注册、app 必须在 catalog 里）。

**文件名与 D9 冲突，须改**：心智模型原定 `item_callback.py`（「多个回调用一个文件承载」），
但 D9 已裁为声明式，**`.py` 载体与该裁决矛盾**——加载 `.py` 就等于允许数据仓携带可执行代码，正是禁令所禁。
建议改为数据文件（如 `callbacks.jsonl`），并在 `docs/manual/` 里写清四类触发器与三类效果的填法（D15）。

**求值时机**：动作触发在受理时求值；时间触发与事件触发接入既有排程（`scheduling/compile.py` 已按
`PUSH_HOURS_LOCAL` 编排整点推送，`:94`）；状态触发在结算后比对。**不新增调度器**——AS2 集成规格禁止复制调度器
（`docs/dev/SPEC-as2-integration.md`）。

<a name="proposal-envdataset-删工具"></a>
### 1.2.11 删除 `set_alarm` 与 `send_message`（答复第二轮 Q1）

**结论：两个都能删，但删除面差别很大——闹钟是局部删除，消息是一整个子系统。**

**`set_alarm` 删除面（14 处，可完整收敛）**

| 位置 | 内容 |
| --- | --- |
| `src/worldmock/life/alarm.py` | 整个模块（33 行） |
| `src/worldmock/life/models.py:774`、`:1322` | `SetAlarmRequest` 与工具注册 |
| `src/worldmock/life/session_actions.py:613`、`:985` | `_set_alarm` 与分发表项 |
| `src/worldmock/scheduling/events.py:29` | `ScheduledKind.ALARM_RING` 成员 |
| `src/worldmock/scheduling/effects.py:44-45` | `ALARM_RING` 到期处理 |
| `src/worldmock/life/schedule.py:79`、`:84`、`:418` | `WakeReason.ALARM_RING`、睡眠允许集合成员、追加点 |
| `src/worldmock/life/time_advance.py:262`、`:266` | 闹钟中断当前活动 |
| `src/worldmock/life/observe.py:251` | 观察中的闹钟事件 |
| `src/worldmock/life/decision_coordinator.py:390` | 到点事件集合 |
| `src/worldmock/life/rhythm.py:465` | `alarm_rang` 标志 |
| `custom/envs/life_env.py:29`、`custom/agents/life_agent.py:795` | AS2 侧白名单与分派 |
| `src/worldmock/agents/life_prompt.py:28`、`:62`、`:86` | 工具枚举、提示文字、工具描述 |
| `src/worldmock/footprints/models.py:47` | 存储侧唤醒原因闭集成员 |

**`send_message` 删除面（远大于闹钟，含整个社交子系统）**

| 位置 | 规模 |
| --- | --- |
| `src/worldmock/life/social.py` | 382 行 |
| `src/worldmock/life/social_actions.py` | 170 行 |
| `src/worldmock/life/contact_exchange.py` | 68 行 |
| `src/worldmock/life/recipients.py` | 60 行 |
| `src/worldmock/runtime.py:37-41`、`:676-801` | 联系人回复的全部机器（提示词、准备、解析、守卫、提交） |
| `ScheduledKind.MESSAGE_ARRIVAL` | 预排送达事件 |
| `src/worldmock/life/prompt_schemas.py:36`、`:94-95` | `TARGET_TOOLS` 与 person/contact 目标工具 |
| `src/worldmock/life/tool_layer.py:299` | 联系人候选的动作 |
| `src/worldmock/life/session_actions.py:630`、`:980`、`:1161` | 分派与实现 |
| `src/worldmock/life/memory_layer.py:12`、`:190` | 记忆层对「未成功 send_message」的挂欠判定 |
| `src/worldmock/evaluation/behavior_metrics.py:720`、`public_evidence.py:257` | 评估侧对消息的统计 |
| `src/worldmock/agents/life_prompt.py:87`、`custom/` | 提示与 AS2 侧 |

**方向一致**：删除 `send_message` 与 U10（「不做联系人，聊天按工作处理，只给一个 topic」）同向，可以一起做。

**连带收益**：删掉两者后，唤醒原因里的 `alarm_ring`、`message_arrival` 直接失去来源，服务 1.2.12 的收敛。

**代价（诚实说明）**：`send_message` 被 **20 个以上测试文件**覆盖，含 `tests/integration/life/test_contact_exchange.py`、
`tests/integration/runtime/test_seven_layer_request.py`、`tests/integration/life/test_memory_layer.py`、`tests/acs_acceptance.py` 等。
按 U13「不维护兼容性」，这些测试**随代码一并删除或改写**，不做兼容层。

<a name="proposal-envdataset-唤醒收敛"></a>
### 1.2.12 唤醒原因收敛（答复第二轮 Q2）

**现状**：23 个成员，定义在 `src/worldmock/life/schedule.py:51-79`，并由
`src/worldmock/footprints/models.py:40-50` 的存储侧词表**互锁防漂移**（两侧由测试锁定，防不一致）。
它们被三处消费：

1. **睡眠允许集合** `SLEEP_ALLOWED_REASONS`（`schedule.py:82-85`）：只有 `planned_end`、`wake_from_sleep`、
   `run_start`、`plan_reminder_due`、`alarm_ring` 能唤醒睡着的人。
2. **质量统计**（`footprints/quality.py:358`）：按原因计数。
3. **边界语义**：解释为什么给人物这次决策机会。

**按「谁产生的」归并成 6 组**：

| 归并后组名 | 现有成员 | 产生者 |
| --- | --- | --- |
| `time_boundary` | `run_start`、`planned_end`、`reconsideration_interval`、`progress_report_due`、`no_active_activity` | 时钟与节律 |
| `duty_boundary` | `shift_start`、`shift_end`、`break_start`、`break_end` | 班次排班 |
| `reminder_due` | `plan_reminder_due`、`errand_reminder_due`、`app_push`、~~`alarm_ring`~~ | 提醒到点 |
| `event_arrival` | ~~`message_arrival`~~、`delivery_arrival`、`person_arrived`、`person_left` | 外部到达 |
| `state_change` | `felt_band_change`、`commitment_threatened`、`wake_from_sleep` | 状态变化 |
| `outcome_feedback` | `activity_infeasible`、`rejection_feedback`、`discovery_result` | 受理结果 |

**关键约束——不能只归并成组名**：睡眠允不允许是**具体原因**的属性，不是组的属性。
`planned_end` 能唤醒睡眠者，`reconsideration_interval` 不能，但两者同属 `time_boundary`。
因此形态必须是 **`(组名, 明细)` 二元**：组名供统计与订阅，明细保留原取值。

**版本说明**：这两个词表的注释都写着「成员不可改名、不可删除，新增走版本表」。按 U13「不维护兼容性」
**可以重构**，但必须走版本升号（`WORLD_SCHEMA_VERSION` 现为 11，`src/worldmock/life/models.py:35`），
旧检查点与旧导出按版本拒绝。**不要用可选字段绕过。**

**收敛后的规模**：23 − `alarm_ring` − `message_arrival` ≈ **21 个明细，归入 6 组**；
若 `person_arrived`/`person_left` 也随 U10 一并删除，则 **19 个明细**。

**待作者定**：`person_arrived`/`person_left` 依赖「其他人也在同一地点」。U10 已定不做联系人——
若世界里没有其他人，这两个唤醒原因同样失去来源。是否一并删除？

<a name="proposal-envdataset-回调减负"></a>
### 1.2.13 回调的定位：给 persona 减负（答复第二轮 Q3）

**回调的目的不是让 persona 做更多，而是让 persona 做更少。** 现实里一个打车动作有
**浏览 → 出行 → 支付**三个阶段；系统把 persona 需要感知的部分压到只剩「我要去哪」，
其余阶段由系统自动匹配 app 并**把足迹记全**。

**打车场景的三阶段映射**：

| 现实阶段 | persona 是否感知 | 系统做什么 |
| --- | --- | --- |
| 浏览（打开滴滴/高德打车挑车型） | **否** | 按该动作上绑定的功能 app **按种子随机选一个**，写一条 `visibility=system` 的浏览足迹 |
| 出行（途中） | 只感知「我要去哪」 | 照常结算出行时长与到达 |
| 支付 | **否**，只看到余额减少 | 按种子随机选一个支付 app，记一条支付足迹与一条资源流 |

**好消息：这套机制已经存在，不是从零新建。**

| 已有能力 | 位置 |
| --- | --- |
| **系统内记录（人物看不到的足迹）** | `src/worldmock/scheduling/effects.py:72` 已有 `visibility = "system" if ... else "agent"` 的双可见性 |
| **app 足迹要求系统来源** | `src/worldmock/footprints/app_usage.py:114-153` 的 `validate_app_event_source` 要求 app 事件 `source_kind == "environment"` 且 `visibility ∈ {system, agent}`——**hook 产生的正是这类事件，落在既有契约内** |
| **「app 辅助线下行为」已有语义** | `AppUseManner.OFFLINE_ASSISTED`（`src/worldmock/exchange/types.py:48`，标签「借助 app 安排或记录线下行为」）就是为「人做了线下动作、app 留了记录」准备的 |
| **推送通道** | `ScheduledKind.APP_PUSH` + `PUSH_HOURS_LOCAL`（当地 08:00–21:00 整点，`src/worldmock/exchange/constants.py:36`） |
| **因果链传播** | `src/worldmock/scheduling/effects.py:76-82` 的 `caused_by` 传递 |

**需要新增的三件事**：

- **(A) 动作上的显式 hook 声明**：数据集按**具体动作**声明 hook 链，例如
  `打车 → [选车 app（浏览足迹）, 支付 app（支付足迹）]`。与 D9「声明式规则、暴露越少越好」同形。
- **(B) 同一动作内的 hook 顺序与因果**：两个足迹必须 `caused_by` 同一条动作事件，审计上可追溯。复用既有 `caused_by` 传播。
- **(C) 跨类型资源流**：出行动作触发支出，在 `src/worldmock/resources/ledger.py` 里属「转换」
  （原子跨资源），机制已支持，但须明确「出行 → 货币」这条转换的归属与可校验性。

**设计要点**：hook 产生 **N 条足迹、0 条或 1 条最小的 persona 可见提示**。
prompt 里只多一句「余额减少了 X 元」，而导出里**浏览记录与支付记录都在**。
这正是「系统把数字足迹记录全」与「persona 只选择我要去哪」的落点。

<a name="proposal-envdataset-功能app绑定"></a>
### 1.2.8 功能 app 的绑定语义（答复第二轮三问）

**绑定目标是具体动作，不是动作类型。** 系统只提供「绑定」这个**能力**；绑不绑、绑到什么，由环境数据集声明；
**默认不绑**（从人的角度看，不该有任何 app 被强行绑定）。

因此，功能 app 的绑定键是**具体动作**，而不是「出行 / 购买 / 睡眠 / 使用」这四个类型。本设计下的具体动作全表：

| 动作类型 | 具体动作（绑定的合法键） |
| --- | --- |
| 出行 | `步行`、`公共交通`、`打车`、`驾车` |
| 睡眠 | `睡觉` |
| 购买 | `购买`、`领取` |
| 使用 | `使用`（不再细分） |

例：高德导航**绑定到 `驾车` 与 `步行` 这两个具体动作**，不是绑定到「出行」这个类型；
支付宝/微信绑定到 `购买`。若数据集不声明某条绑定，则该动作发生时**不产生任何 app 记录**。

**闹钟不是功能 app**（第三轮修正）：闹钟是 **persona 自己发起的一次性排期动作**（`set_alarm`），
已经有原生实现，见 1.2.10。它不挂在任何动作类型下，也不由数据集绑定。

**触发语义**：persona 做出某具体动作时，系统检查该动作上是否声明了功能 app——
- 绑定且**唯一** → 记一条该 app 的使用记录（自动化，非 persona 选择）；
- 绑定且**多个** → 系统**按种子随机选一个**（如支付宝/微信）；
- **未绑定** → 不记。

**「使用」不再细分**（1.2.7-D2 与本节答案）：persona 侧只写「使用 + 内容 app」，具体内容由系统推送。
因此「使用」下**没有更细的动作键**可绑——内容 app 本身即该动作的目标，不存在功能 app 再挂上去的位置。

**`driving_days` 与天气的关系由数据集决定，不由系统决定**（第三轮修正）：
`driving_days` 是**周期性状态**，其增量的触发源就是回调**订阅了什么**。系统内置的订阅是「当天实际开过车」
（`src/worldmock/life/models.py:340-342`、`src/worldmock/life/fuel.py`）；**若数据集把回调同时订阅到天气，
天气就会参与驱动 `driving_days`**。系统只提供订阅能力，不预设哪些触发源参与。详见 1.2.9。

<a name="proposal-envdataset-随机源"></a>
### 1.2.6 随机源清单（答复 U8）

作者要求「但凡有随机的都绑定到 `config.toml` 的单个种子」。以下是**全部**随机源，按现状与新增分类。

**现状已有（各自独立，须收编）**：

| # | 随机源 | 位置 | 现在的种子来源 |
| --- | --- | --- | --- |
| R1 | 地图生成（节点与边长） | `src/worldmock/catalog/mapgen.py:16`、`:33` | `map.json` 的 `seed` 字段 |
| R2 | 场景随机态 | `src/worldmock/life/world_setup.py:69` | 场景的 `random_seed` |
| R3 | 应用选择与推送派生流 | `src/worldmock/life/randomness.py:7` | 由 `random_state` + key 派生 |
| R4 | 呈现轮转换新 | `src/worldmock/life/time_advance.py:96-97` | 冻结字节材料派生 |
| R5 | 评估自举 | `src/worldmock/evaluation/metrics.py:856` | 场景 `bootstrap_seed` |

**新增（本次设计引入）**：

| # | 随机源 | 用途 | 说明 |
| --- | --- | --- | --- |
| R6 | 天气 | 出行方式与时长系数 | U5：挂件，仅影响出行 |
| R7 | 到店 / 外卖判定 | 履约渠道 | D3：系统判定 |
| R8 | 支付 app 选择 | 支付宝 / 微信 | D3 + D4：功能 app 自动触发 |
| R9 | 购买所用 app 选择 | 用哪个 app 下单 | D3：系统随机 |
| R10 | 配送时长 | 快递 2–5 天 | app 侧商品 |
| R11 | 地址生成 | `地点名称-地址` | U1 |
| R12 | 实例落点 | 地图内坐标 | U7 |
| R13 | 内容推送/推荐选择 | 推送哪条内容 | 承接 R3 |

**落地要求**：全部并入 `config.toml` 的**单个 `seed`**，经现有 `random_stream(random_state, key)`
（`src/worldmock/life/randomness.py:7`）按用途分键派生，保证**同一数据集 + 同一种子 = 逐字节可重放**。
现状的 R1（在 `map.json`）与 R2（在场景）须一并收编，否则会出现「一部分随机听 config、一部分不听」的分裂。

<a name="proposal-envdataset-地址与坐标"></a>
### 1.2.5 地址、坐标与多实例设计（答复 U1/U2/U3/U7）

**地点条目的形状**：

```text
location.jsonl 每条 = 一个可售分组
  name          地点名称（分组名，如「四季便利」）
  kind          引擎闭集内的类型（必填，见 1.2.7-D7）
  count         在地图上放置的实例数，默认 1
  app_ids       可选 app 绑定（决定能否远程下单）
  opens         可选营业时段，缺省全天（U3）
  menu          可选动作菜单，缺省按 kind 取
```

**地址的表达与生成（U1）**：每条实例的地址由**带键随机流**从固定词表拼装，形如「区名 + 路名 + 门牌号」。
展示名为 **`地点名称-地址`**，例如 `四季便利-梧桐区柳林路128号`。
因为同一 `name` 下的 N 个实例必须可区分，地址在**同一 name 内保证唯一**；种子来自 `config.toml`，故同一数据集同一种子必然生成同一批地址。

**persona 如何指认（U2）**：persona **必须写「地点名称-地址」**，不接受只写「地点名称」。
因此当 `count > 1` 时不存在「去便利店指哪一个」的歧义——**歧义在输入侧就被消掉了**，引擎不需要实现选择规则。

**坐标与出行时长（U7）**：
- 地图的**边带长度**（现已有 `length_m`，`src/worldmock/catalog/models.py` 的 `seed`/边结构）；
  实例落点由系统按种子在地图内布置，**坐标只在系统内维护，不展示给 persona**。
- 出行时长 = `边长度 / (该方式基础速度 × 天气系数)`，四种方式（步行/公共交通/打车/驾车）各有基础速度；
  天气系数由 U5 的随机挂件给出。
- persona 只看到 `地点名称-地址` 这一**展示名**，看不到坐标；地址与坐标的对应关系由系统持有。
  是否要展示坐标**不需要决策**——按「persona 看到的是展示名」处理即可。

<a name="proposal-envdataset-q-结案"></a>
#### 1.2.4 全部 Q 的结案状态（登记处）

**本表是文档指定的登记处**，以第八章各 Q 的标题为准；两者不一致时以第八章为准。
**状态**：已结案 / 部分结案 / 待裁决。

| Q | 状态 | 依据 | 后果落在 |
| --- | --- | --- | --- |
| Q1 | 已结案 | §1.1 澄清一：「闪频」为笔误，实为「商品」 | §1.2.2 表 |
| Q1b | 已结案 | §1.1 澄清四：美团外卖是内容 app，两条路径并存 | §1.1、Q14 |
| Q1c | 已结案 | **作者 2026-10-09**：「系统自动」（方案 a，判定交回引擎） | C4b、X4、C15 的 K7 |
| Q2 | 已结案 | 作者：「叙述」——不要求按顺序提供 | C1、C9 |
| Q3 | 已结案 | 作者 D9：「a，回调暴露的越少越好」→ 声明式阈值规则 | C7、C15 的 K3/K4/K5 |
| Q4 | 已结案 | 作者 D5 + D6：四分类，`fuel` 属商品，`drink` 走心理侧 | C3 |
| Q5 | 已结案 | 作者：「不要 `store_ids`，就只有 `store_kinds` 字段」 | C4、C4b、§1.2.17 |
| Q6 | 已结案 | 作者 D11：`persona.toml` **替换** `background_story` | C10、X10 |
| Q7 | 已结案 | 作者 D12：非密参数 + 256k 加载期断言 | C10、X1 |
| Q8 | 已结案 | 作者：订阅键是**具体动作**，不是动作类型 | C7、§1.2.15 |
| Q9 | 已结案 | 作者 D10：引擎侧派生，数据集零改动（见 Q9 正文的锚点建议） | C6 |
| Q10 | 已结案 | 作者 D6：`drink` 走心理侧，`entertainment_need` 为准 | C3 |
| Q11 | 已结案 | 作者 D13：无油不阻断，只推送提醒 | C8 |
| Q12 | 已结案 | 见 Q21/Q12 正文：「使用」改为「活动」（`interact`） | C13 |
| Q13 | 已结案 | 作者澄清四（内容 app 才可被选中）＋ Q21 → 二分是**数据字段** | C13 |
| Q14 | 已结案 | 作者 D15：`manual` 指 `docs/manual/` 文档 | C12 |
| Q15 | 已结案 | **作者 2026-10-09**：删除场景地点筛选，出行 target 允许全量目录 | C16 |
| Q16 | 已结案 | 已被 Q5 + Q23 吸收：`menus_by_kind` 随类型表删除 | C14 |
| Q17 | 部分结案 | 闹钟已删（D17）；水电费**已结案为 (b)**（数据集定义，走回调） | C7、C13 |
| Q18 | 已结案 | 已被 Q9 吸收：内容侧无独立回调，进度续接即其周期语义 | C6 |
| Q19 | 已结案 | **作者 2026-10-09**：值由数据集声明，规则归引擎（重置到上限） | C8、C7 |
| Q20 | 已结案 | 合并后按模块归属：环境数据归 `docs/design/03-resource-engine.md` | §1.2.4、Q25 |
| Q21 | 已结案 | **作者 2026-10-09**：「1. 采纳」→ `(action, target)` 接口 | C13 |
| Q22 | 已结案 | 作者 D16：「该修修，保持文档一致性」 | C12 |
| Q23 | 已结案 | **作者 2026-10-09**：`kind` 改为数据集自由文本（方案 b） | C14、C15 的 K1/K2/K10 |
| Q24 | 已结案 | **作者 2026-10-09**：方案 (b) 驿站上的 `interact`，一次取走全部 | C13、Q22 第 3 点撤销 |
| Q25 | 待执行 | 合并后的引用迁移（本文自身维护项） | §9.1、C12 |

**说明**：Q12/Q13/Q17 在第八章的标题与本节的关系——
Q12 是「使用改活动」（结案）、Q13 是「app 二分是字段」（结案）、Q17 是「闹钟与水电费」（闹钟已删、水电费已结案 (b)）。
**旧版本的本表曾把 Q5/Q8/Q12/Q21 记成与结案相反的结论**（`store_ids` 并用、订阅粒度待定、`use` 动词、D16 修漂移），
已按第八章重建。


<a name="proposal-envdataset-四问"></a>
### 1.2.7 四个直接问题的答复

#### U14：「观测与隐私边界」是什么

指**哪些世界事实允许出现在 persona 的观察里**。系统内部持有的东西比 persona 看到的多得多，
例如：余额调整、天气设定、地点瞬移、验收强制通过、全世界读取——这些按现行合同**不向人物暴露**
（`docs/CONTRACTS.md:222`）。实现机制已经存在（`src/worldmock/life/projection.py` 负责把世界状态投影成人物可见的观察）。

**所以它不是设计缺口，而是文档缺口**：数据集的八文件包没有一处说明「persona 能看到什么」，
但这个范围一旦变化（例如 N1 引入的地址、U9 的初始余额），合同里必须有权威描述。
**处理方式：写进合同，不动代码。**

#### D2：「使用」统一成一个动作，有阻塞点吗

有，但不阻塞设计——**阻塞点是「效果分类」不能跟着动作类型一起塌缩**。

四个动作类型是 **persona 面向的分组**；而现行的 `ActionCategory`（五值）是 **行为效果分类**，它在六个地方被真实消费：

| 消费点 | 位置 | 作用 |
| --- | --- | --- |
| 体力消耗率 | `src/worldmock/exchange/constants.py:20-24` | 娱乐 1.0、工作学习 1.5、社交 1.5/1.3、运动 5.0、日常照护 2.0 |
| 地点资格 | `src/worldmock/life/action_run.py:55` | 某些类别只能在特定地点做（如工作学习只在工作地点） |
| 社交对象 | `src/worldmock/life/action_run.py:79-81` | 社交必须有 counterpart，其他类别必须没有 |
| 推送资格 | `src/worldmock/life/app_push.py:25`、`:62` | 只有「娱乐 + 持续使用手机」可被推送 |
| 工作内容 | `src/worldmock/catalog/admission.py:232`、`src/worldmock/life/activity_control.py:301` | 工作学习类的内容按实际秒数累计进度 |
| 占用声明 | `src/worldmock/catalog/affordances.py:120` | 动作资源占用必须带固定类别 |

**结论**：persona 侧塌缩为四个类型（「使用」不再细分），但**效果分类必须留在内容/app 条目上**。
即 `contents.jsonl` 的每个条目除内容本身外，还要带一份**效果档案**（类别 → 体力系数、地点资格、是否需要对象、能否推送）。
persona 只说「使用 + app + 内容」，引擎从内容条目取出效果档案来结算。这是唯一阻塞点的解法，且不破坏「只由环境变更世界状态」。

#### D7：需要 `kind` 这个字段吗

**需要，保留**，但要把它从「数据集文件」降为「条目上的必填属性」。

`kind` 今天承担五件事（全部真实消费）：

| 职责 | 位置 |
| --- | --- |
| 室内/室外/场所（气象与活动资格） | `PLACE_KIND_ENVIRONMENTS`（`types.py:101-106`） |
| 可售商品类别 | `GOODS_CATEGORY_STORE_KINDS`（`types.py:91-100`） |
| 该场所允许的动作菜单 | `interaction_menus_by_location_kind`（`scenario.py:315`、`:377`） |
| 中文展示名 | `PLACE_TYPES`（`life/readable.py:23`） |
| 判定「家」 | 现靠 `kind == home` |

若把 `kind` 整字段删掉，这五件事就得拆成**五个逐条目的显式字段**（环境、可售类别、动作菜单、展示名、是否家），
每行地点条目都会变长，而收益只是省掉一个枚举值——**不划算**。

**最终形态（已按 Q23 裁决）**：`kind` 保留为**地点条目的必填属性**，取值是**数据集自由文本**
（如「便利店」「咖啡馆」「驿站」），**不再是引擎闭集**；`location-type.jsonl` 这个独立文件去掉
——类型不再需要被独立声明。这样 N1 的「条目即分组」与 `kind` 并存，D8 的粒度问题自然消失。

**上表五件事的处置**（详见 §1.2.16 结论一与 C14）：

| 职责 | 处置 |
| --- | --- |
| 室内/室外 | `PLACE_KIND_ENVIRONMENTS` **删除**（只有一个真实消费者：运动地点资格，改为从显式集合派生） |
| 可售商品类别 | `GOODS_CATEGORY_STORE_KINDS` **删除**，改为商品自声明 `store_kinds`（Q5） |
| 该场所的动作菜单 | `interaction_menus_by_location_kind` **删除**（今天空转，且原理上无法表达有意义的地点点约束） |
| 中文展示名 | `PLACE_TYPES` **删除**——`kind` 本身已是中文，不需要第二张映射表 |
| 判定「家」 | 改为地点条目上的**显式标志**（如 `is_home: true`），不靠 `kind` 字符串比较 |

**注意（§1.2.16 结论一）**：`kind` **字段本身不能删**——它是上述六件事与「领取资格」等判据的共同载体；
删字段会直接坏掉运行。可删的是那几张**派生表**。

#### D10：引擎侧派生具体怎么派生

机制**已经存在**，不需要新建：

1. **边界来源**：场景参数 `progress_report_interval_seconds`（缺省 **1800** 秒）已经在活动进行中按该间隔安排决策边界
   （`src/worldmock/life/rhythm.py:213`、`:459`、`:569`；`src/worldmock/life/decision_coordinator.py:426`）。
   这就是「每 30 分钟」的天然锚点，**与内容切片无关**。
2. **内容进度来源**：活动数据里带 `content_id`（`src/worldmock/life/models.py:240`），
   状态里按 `content_id` 记 `content_progress`（`src/worldmock/life/activity_control.py:299-301` 读它，含 `completed_rounds`）。
3. **派生公式**：`已进行 = 当前时刻 - 活动开始时刻`，`总时长 = 内容条目声明的 duration_seconds`，
   于是 persona 恢复时看到的是「**进行到 X / Y 分钟**」这一客观事实。

**因此**：数据集只需给**一段**内容说明，无需 30 分钟切片。进度是引擎派生的事实（可重放、零数据成本）；
**暂停时的总结文字由 persona 自己写下**（走既有 `self_note` 通道，`progress_layer.py` 已支持），
恢复时随进度层回到上下文。二者分离——总结丢了不影响恢复。
唯一待定：该 1800 秒锚点是否与 `progress_report_interval_seconds` **共用**（现建议共用，因为二者本就是同一节律）。

**D10 的进度总结分析（回答「只给一段说明，暂停时怎么总结进度」）**：引擎侧派生 (b) 足够，且数据集零改动，机制分两层——

1. **进度事实由引擎派生**：内容进行期间按 1800 秒边界落进度记录（已进行 X 分钟 / 共 Y 分钟）。恢复时 persona 看到的是
   「进行到 45/90 分钟」这一**客观事实**，不依赖任何内容文本。
2. **总结文字由模型自述**：暂停时让模型写一行 `self_note`，走既有 self_note 通道（`progress_layer.py` 已支持）。
   恢复时这条自述随进度层回到上下文。

即：**进度＝引擎派生（客观、可重放、零数据成本），总结＝模型自述（主观、可选、失败不影响恢复）**。
数据集只需给一段说明，30 分钟切片因此**不是必需字段**。

---

<a name="proposal-envdataset-现状总结"></a>
<a name="proposal-envdataset-round1"></a>
## 二、第一轮九路取证：判定与 gap

> **历史快照声明**：本章是第一轮取证的**当时结论**，其中的「变更提案」与「开放问题」随后被作者的大量裁决覆盖（见 §1.2、第三章、第六章）。**本章保留原样以供溯源，不是当前设计主张。**
> 已知被覆盖的例子：本章建议「维持 `LocationKind` 闭集」（Q23 已改为自由文本）、「地点类型文件不可行」（Q23 已落地）、「`store_ids` 保留」（Q5 已删除）、「菜单机制保留待裁决」（§1.2.16 结论二已判空转）。**以第八、九章的结案状态为准。**

第一轮 workflow 对心智模型的九个维度逐条核对。每个维度包含：
- **符合度判定**：完全符合 / 部分符合 / 显著差异
- **关键 gap**：最重要的 1-2 个差异点
- **变更项数**：需要修改的地方

### 2.1 判定概览

| 维度 | 符合度 | 关键 gap | 变更项 |
|---|---|---|---|
| 环境数据集载体、文件清单与加载链路 | 显著差异 | 在文档层承认并固定「环境数据集包」的真实形态：把承载物的权威定义从散落的 docs/CONTRACTS.md:49/57... | 6 |
| 地点类型与地点 | 显著差异 | 补「住宿地点类型必须包含」的准入规则：在 validate_scenario_against_catalog（catal... | 6 |
| app 模型（内容 app / 功能 app 与动作绑定） | 显著差异 | 在 App 行增加显式的 app 分类字段（例如 kind/role，取值如 content/function 或更贴近... | 5 |
| 商品 catalog、购买与配送链路 | 显著差异 | 把 GoodsCategory 从六类改为心智模型的四类（零食 snack / 饮料 drink / 食品 food /... | 6 |
| 内容 catalog、消费方式与 30 分钟切分 | 显著差异 | 先在冻结规格层裁决「30 分钟切分」的归属与形状：把该条写成阶段锚点，明确片段是数据集必填字段（catalog 侧新增结... | 6 |
| 动作空间与 target 类型 | 显著差异 | 裁决「使用」是否为独立动作类型，并据此二选一：方案 A（贴心智模型）新增动词 use 与方式「使用」，把内部动作 use... | 6 |
| persona 状态维护与周期性商品/内容回调 | 显著差异 | 裁决并冻结「周期性消费状态」这条边界：确认是否允许环境数据集声明额外的 persona 状态名与回调。这是本维度其余所有... | 7 |
| persona 画像文件与 LLM/重试配置 | 显著差异 | 先由人裁决「人设信息载体」这一契约：是保留现状（场景 JSON `agents[].background_story` ... | 5 |
| 记录/导出/契约治理的连带影响 | 显著差异 | 先落一份新的特性规格 docs/dev/SPEC-<dataset-callbacks>.md，只定义行为面：周期性消费... | 7 |

### 2.2 各维度详细判定

#### 2.2.1 D1 环境数据集载体、文件清单与加载链路

**需要的变更**：

1. 在文档层承认并固定「环境数据集包」的真实形态：把承载物的权威定义从散落的 docs/CONTRACTS.md:49/57/1661-1671 收敛为一节显式的「环境数据集包（environment bundle）」合同，写明包 = 清单 + 六个目录 JSON + scenarios/，并显式声明「提供顺序」在本设计中不成立（清单按路径字典序登记，加载按固定读取序）。这一项不改代码，只把心智模型与现状的差异在权威文档里一次性说清，避免后续实现者按 8 文件顺序去改。
   - 模块：`docs/CONTRACTS.md（环境数据归属合同 + 环境目录文件两节），交叉引用 docs/EXPERIMENT.md:38-46`
   - 风险：纯文档，零运行风险。但会改变 docs/dev/ANALYSIS-spec-coverage.md 的条款映射基线：新增的自述性条款（包成分、无声明顺序）若被当作需求，需要新增豁免或新增测试条目，否则门禁的「391 条款无缺口」会计数漂移。

2. 如果确需「文件命名/格式」按心智模型落地（location-type.json / app-catalog.jsonl 等），需要一次目录格式版本升位：CatalogManifest.format_version: Literal[2] 升到 3，六个 JSON 改名并改格式（至少 jsonl 化），同步改名后的 REQUIRED_CATALOG_FILES、identity 分帧摘要、admission 的集合校验、mapgen 的输入位置。注意这会连带改变 catalog_content_sha256、map_sha256 与 runtime 的五字段 environment_identity，所有既往 run 不能打开/恢复/重放。
   - 模块：`src/worldmock/catalog/{models.py, identity.py, admission.py, loader.py, mapgen.py} + custom/agents/life_agent.py 的指纹绑定`

3. 把「地点类型」从引擎闭集改为数据集文件（location-type.json）是不可行的，应该明确拒绝：LocationKind 十值同时被 PLACE_KIND_ENVIRONMENTS 派生（models.py:225-227）、被场景菜单键的完整覆盖校验（admission.py 与 CONTRACTS.md:180/1243）、被 GOODS_CATEGORY_STORE_KINDS 供给规则（constants 侧）与交互菜单、词表、可读名映射共同依赖，把它交给数据集会让这些编译期常量变成运行期输入，直接违反「包规则只从引擎读取」。
   - 模块：`src/worldmock/exchange/types.py（LocationKind）+ src/worldmock/catalog/models.py:225-227 + 场景菜单校验`
   - 风险：若强行改，会破坏「环境没有规则副本」这一已冻结不变量（CONTRACTS.md:55）与「场景菜单键恰好等于十个 LocationKind」的 fail-closed 校验；测试侧 tests/test_validation.py 与地点类词表相关用例全部失效。这一项我建议维持现状并在 open_questions 里请人确认心智模型的「地点类型文件」是否只是「数据集需要知道有哪些类型」的表述。

4. 若要在保留现有架构的前提下满足「persona.toml」与「config.toml」两个载体诉求，最小可行做法是加一层「环境包描述文件」而不动目录格式：在环境根引入可选 config.toml（模型名/上下文下限/重试，引用仓库外 deepseek.env 作为凭据来源）与把 person 人设从 scenario JSON 的 background_story 外提为 persona 片段文件、由场景引用。需要新增 loader 读取与 pin 进 environment_identity。
   - 模块：`src/worldmock/catalog/loader.py 的包边界 + src/worldmock/life/scenario.py:121-143 + src/worldmock/model_endpoint.py:12-15`

5. 针对「item_callback.py 周期性消费状态注册」这一条，先做设计裁决再谈实现：现状是燃油被硬编码在引擎（fuel.py 全文 + constants.py:10,15-16），没有环境可注册的回调钩子。若确要支持，需要引入一张「环境声明 → 引擎状态字段」的允许表（白名单式，仍是引擎定义字段语义、环境只填初值与阈值），并挂在已存在的推送通道上（app_push.py 的 push 窗口 PUSH_HOURS_LOCAL=range(8,22)）。同时必须处理「不足时不禁止动作」与现状 DRIVING_DAYS_EXHAUSTED 拒绝（fuel.py:42-44）的矛盾——这两个语义不能同时成立。
   - 模块：`src/worldmock/life/fuel.py 与 src/worldmock/catalog/models.py（环境声明字段）+ src/worldmock/life/app_push.py（提醒投递）`

（另有 1 项变更）

**待裁决**：8 个开放问题

- 心智模型里 location.jsonl 一条写的是「绑定app的地点可以支持persona远程下单购买地点类型下包含的闪频」，「闪频」疑为「商品」的笔误（上下文与 merchant-catalog 的 sub-bullet 都在讲商品购买与到店/外卖判定）。我不替它做决定，请裁定「闪频」是否即「商...
- 「环境数据集按顺序提供以下信息」的「顺序」是需求还是叙述？现状是清单按路径字典序登记、加载按代码硬编码次序，不存在对数据集声明顺序的校验。如果顺序是硬需求（例如要求后一个文件只能引用前一个文件已定义的身份），需要新增一个跨文件的拓扑校验，这会引入一个现在完全没有的不变量。请裁决。
- 「persona.toml」与场景 JSON 内的 background_story 是替换关系还是并存关系？现状是人设作为场景字段（scenario.py:134），并且已有独立的 profile compile/check/validate 三段式（persona card + 大五 + cov...

（另有 5 个问题）

---

#### 2.2.2 D2 地点类型与地点

**需要的变更**：

1. 补「住宿地点类型必须包含」的准入规则：在 validate_scenario_against_catalog（catalog/admission.py:209-227）增加两条 fail-closed 校验——(a) 已登记地点中至少存在一个**住宿地点**；(b) 每个人物的 home_location_id 所指地点必须落在住宿集合内。**注意**：Q23 已把 `kind` 改为自由文本，故判据**不能**是 `kind == "home"` 字符串比较（会重演 §1.2.16 结论一里的 `parcel_station` 问题），而应是地点条目上的**显式标志**（如 `is_home: true`）。新增稳定登记码（建议 HOME_LOCATION_REQUIRED）与英文原因写入 catalog/errors.py 的 REGISTRATION_REASONS。
   - 模块：`src/worldmock/catalog/admission.py（新增 private _check_home_kind）、src/worldmock/catalog/errors.py、文档归属 docs/CONTRACTS.md 登记码表与 docs/dev/SPEC-action-and-content-simplification.md 的准入条款`

2. 裁决地点类型清单的归属与文件形态，二选一并落文档。方案 A（按心智模型新增 location-type.json）：扩展 identity.py:18-20 REQUIRED_CATALOG_FILES 为七文件，新增 models.LocationTypesFile，admission.validate_registration 校验每个 Location.kind 必须在该文件声明、且声明集合与 LocationKind 闭集的关系被明确（若允许环境自报新类型，则 LocationKind 必须从闭集降级为「系统内置类型 + 环境扩展类型」两层）；manifest format_version 递增、RULES_VERSION 与 PLACE_KIND_ENVIRONMENTS / STORE_KINDS / GOODS_CATEGORY_STORE_KINDS 的派生关系需同步裁决，map 生成器不需改（只读 location_id）。方案 B（维持系统闭集，改文档）：把心智模型的「环境数据集提供地点类型」改写为「地点类型为系统闭集，环境以 Location.kind 引用」，代码零改动。
   - 模块：`src/worldmock/catalog/identity.py、src/worldmock/catalog/models.py、src/worldmock/catalog/admission.py、src/worldmock/exchange/types.py、tests/fixtures/environment/catalog/manifest.json 与 docs/CONTRACTS.md#contracts-环境目录文件`

3. 把「地点类型下包含的商品」从近似索引改为真绑定：把 _goods_indexes 的 menus_by_kind 从 next(...) 取首个同类型地点改为该类型全部地点的菜单并集，或在 Goods 上增加地点类型级绑定（types 列表）使「类型菜单」成为一等公民，供 purchase_offers.menu_goods / offers 直接按类型取店铺集合，而不是先按已选店铺再过滤。
   - 模块：`src/worldmock/catalog/loader.py:81-91、src/worldmock/catalog/models.py:69-76 与 207、src/worldmock/life/purchase_offers.py:28-35、62、docs/CONTRACTS.md#contracts-环境目录文件`

4. 把「按类型/店铺的远程下单」明确为「按 app 的地点集合」并补上心智模型缺的那半句：现状已经做到「有绑定 app 的店铺可配送、无绑定 app 的店铺只得到店」，但判定入口是店铺清单。建议在 purchase_offers.offers 的 docstring 与 docs/CONTRACTS.md「购买来源」条款里把该推导写清（app → stores_by_app → 可配送店铺），避免把「地点类型」误当作配送判据；如果裁决要求类型级语义，则在 offer 生成处增加一层「类型 → 该类型下所有绑定该 app 的地点」的展开，而不是改结算。
   - 模块：`src/worldmock/life/purchase_offers.py:44-65、docs/CONTRACTS.md（购买来源与结算检查）、docs/design/03-resource-engine.md`

5. 统一文件命名与序列化形态的口径：现状 locations.json 是带根字段的 JSON 对象（LocationsFile），不是 JSONL。建议保持 .json 并修正心智模型表述；若裁决必须为 .jsonl，则需要改 identity.compute_catalog_identity 的分帧摘要输入集合、manifest.files 的路径、loader._read_model 的行式解析，以及所有环境的落盘字节。
   - 模块：`src/worldmock/catalog/identity.py:61-79、src/worldmock/catalog/loader.py:53-66、tests/fixtures/environment/catalog/manifest.json`
   - 风险：改 .jsonl 的破坏面很大且收益仅是命名一致：目录身份摘要（分帧：路径 + NUL + 长度 + 内容）对所有环境重算，全部环境发布字节与 manifest 改写，历史 runs 的 frozen-inputs 无法对应（SPEC-environment-data-ownership.md 要求历史证据不改写，只能新旧并存）。建议不做，作为文档裁决项。

（另有 1 项变更）

**待裁决**：7 个开放问题

- 条款 2 中的「闪频」在一次全文检索里没有任何命中（grep -rn "闪频|闪送|闪购" 整个仓，除 .work/runs 外只命中 tests/fixtures/dayrun_fidelity_inputs.json:305 与 docs/archive/dayrun-fidelity/PROP...
- 条款 2 的「命名为location.jsonl」与现行实现（locations.json，JSON 根对象 {locations:[...]}，见 catalog/models.py:147 与 loader.py:33）在扩展名与序列化形态上都不同。是要求真正的 JSONL 行格式，还是只是扩展...
- 条款 1 的「persona的住宿地点类型必须包含」有两种读法：(a) 数据集必须在类型清单里声明 home 这个类型（词表层面必须含有）；(b) 每个环境必须至少实例化一个 kind==home 的地点（实例层面必须含有）。当前实现两者都没有强制，只有 home_location_id 必须存在且...

（另有 4 个问题）

---

#### 2.2.3 D3 app 模型（内容 app / 功能 app 与动作绑定）

**需要的变更**：

1. 在 App 行增加显式的 app 分类字段（例如 kind/role，取值如 content/function 或更贴近现有语义的用途枚举），并把「功能 app 绑动作」与「内容 app 供消费」两条约束写成登记校验，而不是让数据集侧靠 purposes 与 Action.app_ids 隐式推断。落点：App 行模型与 AppsFile。
   - 模块：`src/worldmock/catalog/models.py（App 行）、src/worldmock/catalog/admission.py（_check_apps 增校验）、src/worldmock/catalog/errors.py（新增拒绝码）`

2. 把 AppPurpose 的用途集合与「功能 app」概念对齐：明确 navigation/payment 属于功能类、shopping 兼有两面，或者引入第三种用途；同步 admission 的三者互锁规则（is_shopping == 有店铺绑定 == payment_app_ids 非空）。
   - 模块：`src/worldmock/exchange/types.py（AppPurpose、APP_USE_MANNER_LABELS 邻域）、src/worldmock/catalog/admission.py:171-185`

3. 让功能 app 的自动触发与记录成为一等事实，而不是把「系统随机选支付 app」当作实现自由：若确实要随机选支付，需把 CONTRACTS.md:119 的「环境不代选 app、对象或支付 app」改写为显式例外，并把随机源、随机身份与可复现性写进合同（现有的可复现随机入口是 src/worldmock/life/randomness.py::random_stream，事件身份需带 scheduled_id 类稳定键，参见 src/worldmock/life/app_push.py:45-49）。
   - 模块：`docs/CONTRACTS.md:119/130/591/697、src/worldmock/life/routing.py（_purchase_issues）、src/worldmock/life/basket.py（_payment_binding）、src/worldmock/life/named_purchase.py（_payment）`

4. 为数据集侧补一个显式可写的关联声明，使「平台 app ↔ 可浏览商品集合」可以按心智模型写成 manual（而不是只能靠给店铺写 app_ids 隐式得到），并让 query_world 的 app 筛选取用同一关联声明。
   - 模块：`src/worldmock/catalog/models.py（新增关联行模型）、src/worldmock/catalog/loader.py:31-38（读取新文件）、src/worldmock/catalog/admission.py（新校验）、src/worldmock/life/world_query.py:202-215 与 src/worldmock/life/discovery.py:39/53`

5. 如把「使用」确立为第四个动作类型，则需在 WAYS 中新增方式词、在 START_ACTIVITY_ACTIONS 与 ActivityKind 中新增枚举、在 affordances.toml 的 kinds 闭集与 _ACTIVITY_KINDS 中新增一项，并同步 app 作为 target 的绑定类别。
   - 模块：`src/worldmock/life/vocabulary.py:9-14、src/worldmock/exchange/types.py:112-114、src/worldmock/life/models.py:150-156、src/worldmock/catalog/affordances.py:26（_ACTIVITY_KINDS）与 :161-163（kinds 必须恰为四类）、src/worldmock/data/affordances.toml`

**待裁决**：6 个开放问题

- 心智模型把「使用」列为四大动作类型之一（出行/睡眠/购买/使用），但系统侧的四个动作类型是 travel_to/sleep/wait/purchase（外加 do/collect_parcel），「使用」在 WAYS 与 START_ACTIVITY_ACTIONS 中都不存在。这是心智模型的笔误（...
- 心智模型要求「系统自动随机选择支付宝/微信进行支付」，这与已冻结的 docs/CONTRACTS.md:119「环境不代选 app、对象或支付 app」以及 PAYMENT_APP_REQUIRED 正面冲突。请裁决：是推翻该不变量（连带重写测试与导出语义），还是修正心智模型表述为「系统提示 per...
- 「内容 app / 功能 app」二分是数据字段（需要在 apps.json 增加显式分类列）还是文档约定（按 purposes 是否为空推导）？若是字段，apps.json 的字节变化会连带刷新 manifest 与历史冻结输入，需要明确走「新增格式版本的人工环境」还是「原地改写并重钉哈希」。

（另有 3 个问题）

---

#### 2.2.4 D4 商品 catalog、购买与配送链路

**需要的变更**：

1. 把 GoodsCategory 从六类改为心智模型的四类（零食 snack / 饮料 drink / 食品 food / 商品 goods），决定 fuel 与 daily 的归并去向（建议 fuel 保留为独立类别或改由 item_callback 的状态承载，daily 并入 goods）。同时改 GOODS_CATEGORY_LABELS、STORE_KINDS/GOODS_CATEGORY_STORE_KINDS 的类别键、GOODS_ENERGY_KCAL/GOODS_RECEIPT_EFFECTS 的键集合，以及 fixture 商品（tests/fixtures/environment/catalog/goods.json 现含 meal/snack/drink/leisure/daily 五类）。
   - 模块：`src/worldmock/exchange/types.py, src/worldmock/exchange/constants.py, tests/fixtures/environment/catalog/goods.json, tests/fixtures/environment/catalog/manifest.json`

2. 让「饮料/商品解决心理需求」在结算上成立：把 drink 从 GOODS_ENERGY_KCAL 移出，改为在 GOODS_RECEIPT_EFFECTS 里给一个明确的娱乐/情绪固定效果，并在 receipt._receipt_effect（receipt.py:78-96）补上 drink 的分派分支。
   - 模块：`src/worldmock/exchange/constants.py, src/worldmock/life/receipt.py`

3. 支持商品直接绑定地点类型：给 Goods 增加 `store_kinds: tuple[NonEmptyString, ...]`（**类型是自由文本**，已按 Q23 定），在登记期展开成实例级 `goods_by_store`。
   - 模块：`src/worldmock/catalog/models.py, src/worldmock/catalog/admission.py, src/worldmock/catalog/loader.py`

4. 让「系统按地理位置自动判定到店还是外卖」重新成立（当前由人物显式写 app 决定）。可选做法：恢复一个由环境求解的 `prefer`/自动方式语义，保留显式 app 作为可选约束。
   - 模块：`src/worldmock/life/named_purchase.py, src/worldmock/life/name_resolution.py, src/worldmock/life/routing.py, src/worldmock/life/models.py 的请求模型`

5. 若采纳心智模型的「绑定 app 即网购、时长 2-5 天随机、默认驿站」：删除或降级 Location.shipping_seconds 的数据集权威，改为系统随机抽样 2-5 天并让 STATION_PICKUP 成为 app 绑定的默认履约。
   - 模块：`src/worldmock/catalog/models.py:65, src/worldmock/life/basket.py:183-225, src/worldmock/life/parcel.py, src/worldmock/exchange/constants.py, src/worldmock/life/randomness.py`

（另有 1 项变更）

**待裁决**：8 个开放问题

- 心智模型的四分类里「商品」到底是哪个现有类别？现闭集是六个（meal/snack/drink/leisure/daily/fuel）。若「商品」= leisure+daily，则 fuel（燃油）在四分类里无位置——而心智模型自己的 item_callback 段落又明确把「燃油」当作周期性消费商品...
- 「饮料解决人物的心里需求」与现行代码直接相反（drink 在 GOODS_ENERGY_KCAL 里走 apply_food 加热量，src/worldmock/exchange/constants.py:11-13、src/worldmock/life/receipt.py:82-83）。这是笔误...
- 「商品至少绑定一个地点类型/app」中「地点类型」是要求按类型绑定（如 `store_kinds`），还是现有按实例绑定（store_ids 指向 location_id）就算满足？现有机制是实例级 + 类别可供给表间接约束，二者不等价，需明确。

（另有 5 个问题）

---

#### 2.2.5 D5 内容 catalog、消费方式与 30 分钟切分

**需要的变更**：

1. 先在冻结规格层裁决「30 分钟切分」的归属与形状：把该条写成阶段锚点，明确片段是数据集必填字段（catalog 侧新增结构）还是引擎侧按 1800 秒派生的进度/记忆里程碑（引擎侧计算、数据集不配置）。规格是 FROZEN，必须先取得用户裁定与版本表行（形如 2026-10-08-acs-content-segments），否则后续任何代码改动都是无规格施工。
   - 模块：`docs/dev/SPEC-action-and-content-simplification.md（内容目录合同的唯一所有者，见 :315 与 :423 两处表格）＋ docs/CONTRACTS.md 登记行`

2. 把「内容至少绑定一个 app」落成可执行的准入规则，与该条一句一字对应：Content.app_ids 加下界（非空）并在 _check_contents 中显式校验，同时为「动作本身无 app」的既有形态（small-talk、office-work、quiet-rest 与场景分配的 work_content_ids）写出单独豁免码，而不是靠 bool 相等隐式放行。
   - 模块：`src/worldmock/catalog/models.py（Content.app_ids）＋ src/worldmock/catalog/admission.py（_check_contents，现 :126-141）`

3. 若裁决为数据集侧片段：在目录模型新增内容片段结构（每条内容为 {segment_index, duration_seconds} 列表），登记时校验片段时长为正、合计等于 duration_seconds、每段不超过 1800 秒、索引连续，并把片段并入 contents.json 的索引冻结（contents_by_action / contents_by_action_app 旁增派生索引）。
   - 模块：`src/worldmock/catalog/models.py（新 ContentSegment 与 Content.segments）、src/worldmock/catalog/loader.py（_content_indexes）、src/worldmock/catalog/admission.py（新校验）`

4. 若裁决为引擎侧派生（推荐的低破坏方案）：保持目录不动，在 settle_action 落一次「跨过 1800 秒边界」的进度里程碑，并把记忆层的会话条目按该边界切句，使长内容无需新增数据集字段即获得进度与记忆粒度。
   - 模块：`src/worldmock/life/action_run.py（settle_action :160-190）＋ src/worldmock/life/progress_layer.py（:189-197 附近）＋ src/worldmock/life/memory_layer.py（窗口与条目组装）`

5. 文件改名 content-catalog.jsonl 与形式改为按行 JSONL：更新必需文件集合、加载器读取方式与清单 files 表，并把根对象 {contents: [...]} 改成逐行对象。
   - 模块：`src/worldmock/catalog/identity.py（REQUIRED_CATALOG_FILES :18-19）、src/worldmock/catalog/loader.py（:36 与 _read_model 的 JSON 解析）、src/worldmock/catalog/models.py（ContentsFile 根字段）、每个环境目录的 manifest.json`

（另有 1 项变更）

**待裁决**：7 个开放问题

- 「内容至少绑定一个 app」与现行绑定模型互斥：动作不绑 app 时（small-talk、office-work、quiet-rest，以及场景 work_content_ids 分配的 topic-sales），admission 强制内容的 app_ids 也必须为空。是要求所有动作都必须绑 ...
- 「按照当前系统设计的消费方式推送」中的「当前系统设计的消费方式」具体指哪一套？是现行 AppUseManner 三值（phone_continuous / other_device / offline_assisted），还是另一套尚未写下的推送策略（例如按内容类别、按时段）？该短语是引用现有设计还...
- 30 分钟切分的粒度归属未定：是数据集必须提供的「内容片段」（结构字段），还是引擎侧按 1800 秒派生的进度/记忆里程碑？原文「提供每30mins消费的内容片段」倾向数据集侧，但「用于系统做进度和记忆管理」又像引擎侧派生；两种方案的破坏面差别很大。

（另有 4 个问题）

---

#### 2.2.6 D6 动作空间与 target 类型

**需要的变更**：

1. 裁决「使用」是否为独立动作类型，并据此二选一：方案 A（贴心智模型）新增动词 use 与方式「使用」，把内部动作 use_app 加回 `LIFE_VERBS`/`WAYS`/`START_ACTIVITY_ACTIONS`；方案 B（贴现行冻结设计）保留 interact+进行，把心智模型的四类型改写为「出行/睡眠/购买/进行」。本项是后三项的前置，必须先裁决。
   - 模块：`src/worldmock/life/vocabulary.py（LIFE_VERBS、WAYS、ACTION_WAYS、WAY_SYNONYMS）；src/worldmock/exchange/types.py（START_ACTIVITY_ACTIONS）`

2. 若采纳方案 A：新增公开目标类 app，`TARGET_KINDS` 增加 "app"，`_WAY_KINDS` 增加 "使用"→frozenset({"app"})，并为每个已登记 app 生成 `kind="app"` 目标行（内容 app 全部列出，功能 app 不可由人物挑选，需另判是否列出）。
   - 模块：`src/worldmock/life/prompt_schemas.py:32；src/worldmock/life/name_resolution.py:18-22；src/worldmock/life/projection.py（新增 app 目标行构造）`

3. 若采纳方案 A：新增 use_app 受理分支，由人物点名的 app 定位其绑定动作与候选内容，复用现有内容选择器（R4…R6 次序）与 `_start_catalog_action` 的执行/结算路径，避免出现第二套内容执行器。
   - 模块：`src/worldmock/life/session_actions.py:544-559 `_dispatch_start_action`；src/worldmock/life/content_select.py、src/worldmock/life/action_run.py`
   - 风险：中。必须保持「人物选动作与可选 app、系统选内容」这一冻结不变量（SPEC-action-and-content-simplification.md:96）；若变成人物直接点名内容，会破坏内容随机推送与 last_consumed_at 唯一「看过」定义（同文件:93 P-25）。

4. 若采纳方案 A：同步系统层第 12、13 条正文与工具用途句（现文只写「出行、睡觉、等待、购买、领取」），并把工具层目标类清单从七类改为八类。
   - 模块：`src/worldmock/agents/life_prompt.py:47、:49；docs/dev/SPEC-life-realism.md、docs/dev/SPEC-agent-interaction.md、docs/CONTRACTS.md 的目标类与请求形状章节`
   - 风险：高。系统层十八条正文被多个规格台账逐字钉住（SPEC-action-and-content-simplification.md:746 列出逐字约束与测试），改一处会连带多处测试断言与提示指纹。

5. 若不采纳方案 A（推荐的最小改动）：不动代码，改为把心智模型侧的四类型说明收口到现有闭集——在环境数据集交接文档中写明「出行=tavel_to、睡眠=sleep、购买=purchase、使用=interact+进行」以及「等待/领取是系统补充的两类，不在数据集成动作空间内」，使数据集侧据以设计 app/动作/内容而不猜测动作类型。
   - 模块：`docs/dev/SPEC-action-and-content-simplification.md 的「环境格式」与「数据仓交接」章节；WorldMock-Dataset 的目录格式说明`
   - 风险：低。只改文档，不动闭集与指纹；风险是心智模型与实现长期不一致，后续每次数据集侧设计都需人工对照。

（另有 1 项变更）

**待裁决**：5 个开放问题

- 「使用」到底指什么？心智模型把「使用」列为与出行/睡眠/购买并列的第四类型，且其 target 是内容 app；但现行冻结设计明确把 app 从目标类删除（SPEC-action-and-content-simplification.md:983、:1281），人物只选动作与可选 app。这两者互斥...
- 「四个动作类型」里「购买」是否算一个可承载时长/内容的类型？现行系统中购买是零模拟秒的时点事实、不建活动、不占名额（SPEC-action-and-content-simplification.md:73；admission.py:110），而心智模型把它与出行、睡眠并列为动作类型。若数据集侧据心智...
- 功能 app「由系统在 persona 做出某个动作时自动触发」的力度：心智模型举支付宝/微信支付为例要求系统「自动随机选择」支付 app 并记录；现行设计中支付 app 由人物在请求里点名 `pay_app`，不点名则不留 app 记录（SPEC-action-and-content-simpli...

（另有 2 个问题）

---

#### 2.2.7 D7 persona 状态维护与周期性商品/内容回调

**需要的变更**：

1. 裁决并冻结「周期性消费状态」这条边界：确认是否允许环境数据集声明额外的 persona 状态名与回调。这是本维度其余所有变更的前置裁决，未裁决前不得动代码。若维持现状，则把心智模型第 6 项改写为「系统内置的周期性状态只有燃油可驾驶日」并把 item_callback.py 从数据集契约中删除；若采纳心智模型，则必须成对修改 docs/dev/SPEC-action-and-content-simplification.md:320 与 docs/dev/PROPOSAL_2026-10-07_APP_ACTION_RESOURCE_AND_RULE_SIMPLIFICATION.md:105 两条禁止面原文，并新增一份 SPEC，因为这两条是当前冻结契约的明文。
   - 模块：`docs/dev/SPEC-action-and-content-simplification.md（:320）、docs/dev/PROPOSAL_2026-10-07_APP_ACTION_RESOURCE_AND_RULE_SIMPLIFICATION.md（:105）、新增 docs/dev/SPEC-item-callback.md`
   - 风险：这是契约级反向修改：现行 SPEC 明文禁止，改它等于推翻「WorldMock 固定状态效果、Dataset 只填名称价格」的分工前提。破坏面波及目录 schema、场景 schema 与 docs/CONTRACTS.md 的归属声明。不改则整个条款 1 只能记「未实现」。

2. 把「燃油可驾驶日」从单一硬编码特例抽成通用的周期性状态契约：定义数据模型 `ItemCallbackState`（回调名、订阅的动作枚举、每次发生时的状态增量、上限、阈值、提醒文案键），并让 `AgentState` 用 `dict[str, ItemCallbackState]` 承载，替代 `driving_days` / `last_driving_date` 两个专用字段。
   - 模块：`src/worldmock/life/models.py（:333-374 AgentState、:341-343 两个专用字段）、新增 src/worldmock/life/item_callback.py`

3. 实现阈值驱动的提醒投递：让回调状态在跨越阈值时向人物发出 app 推送，复用现有的 `PUSH_HOURS_LOCAL = range(8, 22)` 时段与 `ScheduledKind.APP_PUSH` 排期，而不是新建事件类型。具体做法是在 `issue_app_push` 的候选挑选之前插入一个「周期性状态提醒」分支，命中阈值时以状态提醒文案取代娱乐内容候选。
   - 模块：`src/worldmock/life/app_push.py（:31-53 issue_app_push、:56-68 _push_candidates）、src/worldmock/scheduling/compile.py（:80-99 _push_rows）、src/worldmock/exchange/constants.py（:36 PUSH_HOURS_LOCAL）`

4. 打通状态到人物的可见面：把回调状态的当前值与分级写进环境层或待办层，使人物能知道油量并据此决定加油。现状 `driving_days` 在 agents、projection、environment_layer、presentation 四处 0 命中，人物无从知晓。实现方式参照 `BodyState` 的「数值进事件、分级进环境层」分离（src/worldmock/life/projection.py:1-8 的约定）。
   - 模块：`src/worldmock/life/projection.py（观察投影）、src/worldmock/life/environment_layer.py（环境层文本）、src/worldmock/life/todo_layer.py（如走待办）`
   - 风险：环境层文本变化会改变人物提示的 token 量，进而改变 `decision_prompt_token_median` 等报告项与既有的提示字节快照测试；同时新增分级常量会触发 test_style_gate 的常量命名与 docstring 规则。分级阈值必须登记来源（fact/assumption/scenario），否则违反 catalog/README.md 的溯源全覆盖要求。

5. 把「未购买不阻断正常动作」从反面改成正面：移除驾车出行的余额阻断，或把它改成只影响提示不影响可行性。现状 `require_driving_days` 在受理前抛 `DRIVING_DAYS_EXHAUSTED`（src/worldmock/life/fuel.py:33-44，受理点 src/worldmock/life/tool_layer.py:170-174 与 src/worldmock/life/session_actions.py:539-540）。
   - 模块：`src/worldmock/life/fuel.py、src/worldmock/life/tool_layer.py（:170-174 _travel_check）、src/worldmock/life/session_actions.py（:539-540）`

（另有 2 项变更）

**待裁决**：9 个开放问题

- 第 1 项的核心裁决必须由人做出：环境数据集是否可以注册额外的 persona 状态与回调。现行冻结契约明文禁止（docs/dev/SPEC-action-and-content-simplification.md:320「操作脚本或回调」、docs/dev/PROPOSAL_2026-10-07_...
- 「如果 persona 确实没有加油，乘车出行不会被禁止」与现状直接矛盾，需要裁决方向：现状是余额为 0 时拒绝驾车出行（src/worldmock/life/fuel.py:42-43、:57-58，受理点 src/worldmock/life/tool_layer.py:171）。是删掉这个拒绝...
- 「persona 加油之后燃油可使用天数回到 7 天」——是绝对重置为 7，还是像现状那样加 7（src/worldmock/life/receipt.py:50-53，`driving_days + provided`）？现状是加法，连续两次加油会得到 14 天，且没有上限字段。心智模型用「回到 ...

（另有 6 个问题）

---

#### 2.2.8 D8 persona 画像文件与 LLM/重试配置

**需要的变更**：

1. 先由人裁决「人设信息载体」这一契约：是保留现状（场景 JSON `agents[].background_story` 为唯一来源，persona.toml 仅作为起草用文本，不进入引擎读取面），还是新增可选的独立人物档案文件并在场景里引用它。裁决前不动代码。
   - 模块：`docs/CONTRACTS.md#contracts-世界mock-接口合同（人物档案字段）、docs/manual/personas.md、docs/dev/SPEC-action-and-content-simplification.md#spec-acs-场景结构8`
   - 风险：纯文档与决策项，不破坏任何不变量；但它是后续两项变更的前置，未裁决前不应改代码

2. 若裁决为「新增独立人物档案文件」：在 AgentConfig 增可选 `persona_file`（相对场景目录解析，UTF-8，仍须满足 100 字符下限），并把 src/worldmock/profile/compile.py 的 `_resolve_story` 扩展为「显式文本 > 文件内容 > background_story」，两者同时声明时明确报错而非静默择一。
   - 模块：`src/worldmock/life/scenario.py（AgentConfig）、src/worldmock/profile/compile.py（STORY_FIELD/_resolve_story）、src/worldmock/validation.py:91-92、src/worldmock/run_setup.py:349-358`

3. 若裁决为「配置文件进入环境数据集」：为 config.toml 定归属，并把它纳入目录闭集与身份绑定（新增到 REQUIRED_CATALOG_FILES 或另立环境级可选文件并写入清单 files 表）。
   - 模块：`src/worldmock/catalog/identity.py:18-20、src/worldmock/catalog/admission.py:15-31、docs/CONTRACTS.md#contracts-环境目录文件、docs/dev/SPEC-action-and-content-simplification.md#spec-acs-环境目录文件`

4. 把「模型窗口 + 重试预算」从散落的源码常量收敛为一份可声明、可校验、并冻结进运行清单的配置形状：至少包含 provider/base_url/requested_model、context_window、max_output_tokens、max_retries、transport_max_retries、max_react_turns；凭据仍走 ~/.config 私密文件，永不进入该配置。
   - 模块：`src/worldmock/model_endpoint.py:12-15、src/worldmock/run_setup.py:108-151 与 :400-450、docs/CONTRACTS.md#contracts-model-identity、docs/design/08-configuration.md#configuration-哪些模型参数可配置`

5. 把 256k+ 从「代码里恰好是 1_000_000」改为运行前的准入断言：在模型身份校验阶段断言声明的 context_window ≥ 262144，且当声明值与实际所用模型的权威元数据不可得时明确失败（不再只对 MODEL_ALIAS 做相等断言）。
   - 模块：`src/worldmock/life/prompt_capacity.py:52-57 `model_context_length()`、src/worldmock/run_setup.py:400-450 `validate_stage_model_identity()``
   - 风险：新增一条拒绝路径，可能拒绝今天能跑通的配置（例如未来换用小窗口模型）；需补对应的失败用例并更新 docs/dev/SPEC-agent-interaction.md:591 与 docs/dev/ANALYSIS-spec-coverage.md 的条款映射；对既有导出格式无影响，对历史运行的可恢复性有影响（清单字段增加）

**待裁决**：7 个开放问题

- 条款 1 说「人设信息，就是我们当前的人物画像文件」——这里的「当前的人物画像文件」是指既有的场景 `agents[].background_story` 正文（现状），还是要求数据集新增一个独立的 persona.toml 文件？两种读法对应完全不同的改动面（无改动 vs 场景 schema 升级...
- 若采纳独立 persona.toml：它与场景 `agents[].background_story` 的关系与优先级未定——是取代、并存二选一、还是文件优先？两者同时存在且内容冲突时如何处理，条款没有说。
- persona.toml 是否必须存在于环境数据集目录内？现有设计明写环境根只有 catalog/ 与 scenarios/（docs/CONTRACTS.md:49、:57），且 catalog 的六个文件是闭集；条款未说明 persona.toml 属于哪一层（环境数据集 / 场景目录 / 人物...

（另有 4 个问题）

---

#### 2.2.9 D9 记录/导出/契约治理的连带影响

**需要的变更**：

1. 先落一份新的特性规格 docs/dev/SPEC-<dataset-callbacks>.md，只定义行为面：周期性消费状态的注册语义、可订阅的动作集合（travel_to+by_car 等）、扣减口径（每个本地日期首次正秒驾车扣一天，与 src/worldmock/life/fuel.py:47-62 的 charge_driving_days 同源）、阈值与推送时段（复用 PUSH_HOURS_LOCAL）、以及「不购买不阻断动作」的明确要求。不在其中写文件名与字段名。
   - 模块：`docs/dev/SPEC-*.md（新文件）+ docs/INDEX.md:107 的文件表加一行`
   - 风险：只增文档，不动代码；风险是若同时裁定 item_callback.py 允许由数据仓提供，则本规格的前提（规则归引擎）被推翻，docs/dev/SPEC-action-and-content-simplification.md:320 与 docs/ARCHITECTURE.md:270 必须同批改写，属于破坏既有不变量。

2. 把「不购买不阻断动作」写成可判定条款，并把 src/worldmock/life/fuel.py:42-44 与 :57-59 的 DRIVING_DAYS_EXHAUSTED 抛错改为不阻断（例如耗尽后仅拒绝该次驾车方式的质量表达，或按心智模型改为仅推送提醒）。
   - 模块：`src/worldmock/life/fuel.py、src/worldmock/life/session_actions.py、调用点 src/worldmock/life/time_advance.py`
   - 风险：破坏面最大的一项：DRIVING_DAYS_EXHAUSTED 是已登记拒绝码，改动会动到拒绝码闭集与其测试；同时与 docs/CONTRACTS.md:75「只有环境明确声明拥有汽车的人物能够驾车；初始可用天数由环境声明」的现行陈述冲突，需同批改写合同正文。

3. 给周期性消费状态在 AgentState 上登记字段（如按商品/内容键的「剩余可用天数」与「上次提醒时刻」），并把 WORLD_SCHEMA_VERSION 从 11 升号，同时登记旧检查点与旧导出的拒绝路径。
   - 模块：`src/worldmock/life/models.py（AgentState、WORLD_SCHEMA_VERSION）、docs/CONTRACTS.md:61-64「LifeEnv 状态字段」与「持久化当下事实」、docs/dev/SPEC-action-and-content-simplification.md:781`

4. 把阈值提醒落到既有事件类型上，不新增 EventType：在 environment_event 的 app_push 载荷上原位扩展，并按需追加唤醒原因（若提醒要形成决策机会）到 WAKE_REASON_NAMES。
   - 模块：`docs/design/05-recording-engine.md:220-223 与 :651-670、src/worldmock/footprints/models.py:40-48、src/worldmock/life/app_push.py、src/worldmock/scheduling/events.py:16-28`

5. 裁决数据文件名与扩展名：若采纳七个新文件（location-type.json / app-catalog.jsonl / location.jsonl / merchant-catalog.jsonl / content-catalog.jsonl / persona.toml / config.toml），必须同批改 src/worldmock/catalog/identity.py:18-20 的 REQUIRED_CATALOG_FILES、src/worldmock/catalog/loader.py:26-39 的读取清单、src/worldmock/catalog/models.py 的行模型，并升目录 format_version（现为 2）。
   - 模块：`src/worldmock/catalog/{identity,loader,models,admission}.py、docs/CONTRACTS.md:1660-1685 与 :1690-1730 的错误码表、docs/dev/SPEC-action-and-content-simplification.md:301-323`

（另有 2 项变更）

**待裁决**：8 个开放问题

- 「item_callback.py（多个回调用一个文件承载）」的落点未定：它是环境数据集提供的文件、WorldMock 引擎内的模块、还是规格里只描述行为而由引擎实现？若按字面放进环境数据集，与 docs/dev/SPEC-action-and-content-simplification.md:3...
- 「绑定 app 的地点可以支持 persona 远程下单购买地点类型下包含的闪频」中的「闪频」疑为笔误（闪送？频次？商品？），语义无法判定，需原文澄清后才能判断它与现行「商品绑定店铺、店铺绑定购物 app」的供给链（docs/CONTRACTS.md:1710 附近）是否同一件事。
- 商品四个固定分类[零食，饮料，食品，商品]与现行 GoodsCategory 六个成员（meal/snack/drink/leisure/daily/fuel）不是改名关系：「零食、食品解决饥饿」对应当前 meal/snack 的 GOODS_ENERGY_KCAL，「饮料、商品解决心理需求」与当前...

（另有 5 个问题）

---



## 三、Gap 分析（第二轮）

> **历史快照声明**：本章是第二轮取证的**当时结论**（2026-10-09 合并前），其中的判定与去向受后续裁决影响。**其取证事实仍然有效**（尤其 §1.2.16 引用的三条：`kind` 不能删、菜单空转、商品类型级绑定已由不变量达成），但**结论以第八、九章为准**。

基于第一轮取证和你的裁决（选择 b：删除 LocationKind 枚举，改为数据集自由文本），第二轮 workflow 重新核对了心智模型与代码的符合度。

| 主题 | 判定 | 关键发现 |
|---|---|---|
| T4 商品绑定地点集合的现状声明方式，以及一个环境数据集包里有几份文件 | 商品绑定地点集合今天的**唯一声明方式是实例级列举**：`Goods.store_ids` 写一个 `location_id` 有序去重元组，或写 `"all"`。`"all"` 不是「所有地点」，而是「类型能供给该类别的全部店铺」——展开规则硬编码在 `src/worldmock/catalog/loader.py:85-86`，判别表是引擎包内固定的 `GOODS_CATEGORY_STORE_KINDS`（`src/worldmock/exchange/types.py:91-100`）。因此「多地点有售」有两种写法但一种粒度：列举多个实例，或用 `"all"` 让引擎按类型表代填；数据集中**不存在**任何类型级（商品→地点类型）供给声明，类型只在引擎侧存在，且只以「限制哪些类型可卖该类」的方式介入登记与展开。注册期还叠加一条「同类型店铺全绑或全不绑」的一致性约束（`admission.py:99-101`），使实例级列举在效果上等于类型级声明，`menus_by_kind`（`loader.py:89-90`）就架在这条不变量上——但它在生产链路没有消费者，只有测试引用。包结构上，`catalog/` 与 `scenarios/` 是「一份 catalog 配 N 份 scenario」：目录解析写死为场景上两级的 `catalog`（`life/semantics.py:60-62`），`validate --environment` 对 `scenarios/` 下所有 JSON 复用同一 catalog 加载（`validation.py:137-153`）。「每个环境集每种文件只有一份」**今天不成立**：正式环境 `shanghai-simplified` 同时有 `catalog/goods.json` 与 `sources/catalog/goods.json`，五个目录文件全部逐字节不同，且 `sources/` 既不被读取也不被拒绝（`validation.py:117-134`，行为断言见 `tests/test_validation.py:97-104`）；`docs/CONTRACTS.md:49`/`:57` 的「环境根只有 catalog 与 scenarios」是文档声明，不是被强制的不变量。能否删改：`store_ids` 字段本身动不得——删掉它 `Goods` 立即缺必填字段（`loader.py:66-68` 把 `store_ids` 缺失映射为 `GOODS_BINDING_MISSING`），`goods_by_store` 无法建立，购买与呈现三处直接断链（`basket.py:117`、`discovery.py:48`、`purchase_offers.py:34-35`）。`"all"` 分支删不得——`admission.py:92-93` 与 `loader.py:86` 两处成对存在，删掉任一侧会让 5 件在用商品无法加载或无法展开。`GOODS_CATEGORY_STORE_KINDS` 表删不得——它同时是注册拒绝条件（`admission.py:97`）与展开过滤器（`loader.py:85`），且被两条源码变异验收钉住（`tests/acs_acceptance.py:1160`、`tests/rrs_acceptance.py:1372`）。真要放开「按类型声明」，需要的是新增字段（提案 `PROPOSAL:606` 的 `store_kinds`）让登记期展开到实例级，而不是复用现有表——现有表是引擎固定规则，数据集填不进去，也不该由数据集填。另需注意两处文档漂移在此相关：`docs/CONTRACTS.md:693` 与 `:1668` 写「五个商品类别」，实际六类含 `fuel`；`docs/CONTRACTS.md:180` 写「八个地点类型」，实际十类。 | 商品侧唯一的地点声明字段是 `Goods.store_ids`，取值形态是「地点实例 id 的有序去重元组」或字面量 `"all"`，没有第三个分支：`src/... |
| T2 PLACE_KIND_ENVIRONMENTS 有没有真实消费者 | PLACE_KIND_ENVIRONMENTS 有且只有一个真实影响模拟结果的消费者：src/worldmock/life/rules.py:57 的 Exercise 活动地点资格判定（被 action_run.py:55 与 tool_layer.py:243 两条真实入口调用）。在这条判定里，home/park/fitness 三种 kind 分别派生为 home/outdoor/fitness 而通过，office 与全部六种 venue 场所被拒绝并抛 ELIGIBILITY_LOCATION_KIND_MISMATCH。所以「室内/室外（outdoor vs venue）的区分会不会改变模拟结果」的答案是：会，但仅限于「运动能不能在这里做」这一件事——它不改变出行、不改变结算、不改变身体模型、也不与天气发生任何关系（全仓库 Python 代码零 weather 命中，不存在天气-地点耦合）。另一条看似更广的路径（候选条目的地地点类过滤，candidates.py:344-351）虽然每次观察都会构造（presentation.py:124-126、428），但因为 observe.py:221 恒传 moment=False 且 apply_location_kinds 全仓库从未置真，_location_reason 在 candidates.py:336 提前返回 None，属于计算了却不可达的死语义；catalog/models.py:227 的 Catalog.location_kinds 则只有测试调用。能否删：删 kind 字段不行——它是交互菜单（interaction_menu.py:55-58）、人设提示的场所中文名（environment_layer.py:63-66）、事件级地点目录（observe.py:420）、商品菜单与目录准入（loader.py:85,91；admission.py:80,83,90,97,220）、场景菜单全覆盖校验（scenario.py:377）与加载校验（load_checks.py:42-48，world_setup.py:40）的共同载体，删掉会直接坏掉运行。单独删 PLACE_KIND_ENVIRONMENTS 这张表则只坏三处代码加两处测试：rules.py:57（运动资格，必须把 home/outdoor/fitness 判定内联保留）、candidates.py:72 的 LocationView.from_config（会 KeyError，且因 presentation.py:428 每次观察都急切求值，即使值不被读也会把整个观察打断）、catalog/models.py:227（仅测试调用）；测试侧 tests/unit/exchange/test_types.py:41 与 tests/test_single_source.py:47 会失败；文档侧 docs/CONTRACTS.md:182、1235 以及 SPEC-life-realism、SPEC-resource-and-rule-simplification、SPEC-life-runtime、SPEC-action-and-content-simplification 的多处双向引用需同步，否则文档引用门会失败。若要「删表但留行为」，正确做法是把 outdoor 归属改成从 PLACE_TYPES 或一个显式的「可运动场所」集合派生，而不是保留这张把 venue 一锅端的映射表——因为 place_type 中文表（readable.py:23）与商品供给表（GOODS_CATEGORY_STORE_KINDS）才是 kind 真正的语义归属。附注：同族的 LOCATION_KINDS（retrieval.py:42）与整个 retrieval 模块是一片更彻底的死代码，无任何生产导入者，可与本次清理一并处置。 | PLACE_KIND_ENVIRONMENTS 定义在 src/worldmock/exchange/types.py:101-106，把 10 种 Locat... |
| T3 动作与地点的绑定关系（interaction_menus_by_location_kind） | 这是什么：`interaction_menus_by_location_kind` 是**场景文档**里的一个必填映射，键是系统闭集 `LocationKind` 的 10 个场所类型，值是 `InteractionMenu{allowed_actions}`，取值只能来自 6 个内部规范动作（travel_to / sleep / wait / purchase / do / collect_parcel，且必须保留 wait 与 sleep）。它经 `resolve_interaction_menu` 与人物自己的 `interaction_menu` 求交，得到本轮有效菜单，然后被三处消费：宿主的协议校验、工具层的可用性标注、以及环境侧授权结算前的权威重验——后者是硬拒绝（`INTERACTION_MENU_DENIED`），绕过宿主也拦得住。

能不能删：**技术上能，且当前删掉不改变任何行为**。三份场景夹具的 10 个地点类都声明同一份 `START_ACTIVITY_ACTIONS` 全集，人物菜单全为 `null`，交集恒等于全集，所以这个字段今天对模拟结果零影响。但删它不是「清理死代码」而是**撤掉一条已冻结的特性合同**：`docs/dev/SPEC-interaction-menu.md`、`docs/CONTRACTS.md:180`、`docs/EXPERIMENT.md:85` 都把它列为实验条件（菜单声明进入场景原始字节身份，改菜单＝改实验身份），`docs/dev/SPEC-interaction-menu.md:69` 冻结了「只按地点类、不按具体地点」的粒度，`menu_execution.py` 的 `INTERACTION_MENU_DENIED` 与 `guidance.py:31`/`:104` 的两种说法也随之作废。删之前需要先裁决这个特性是否保留。

删了什么会坏：`scenario.py:315/377` 的必填与完整性校验、`_check_menu_actions`、`LifeScenario`/`RuntimeScenario` 的 `model_validate`（`tests/integration/life/test_public_interface_guards.py:241` 断言 10 键）、`tests/life/test_interaction_menu_*.py` 全套、`menu_execution.require_interaction_menu` 及其在 `session_actions.py:970` 的调用、`tool_layer.py:89-91` 的拒绝分支、`projection.py:231-233` 的过滤与 `environment_layer.py:54-55` 的 `fixed_rules` 句、`custom/agents/life_menu.py` 整个模块与 `life_agent.py:399/491`、`narration.py:30` 的禁键条目、以及 `docs/CONTRACTS.md`/`SPEC-interaction-menu.md`/`EXPERIMENT.md` 的相关条款与 ANASYS 映射。夹具场景需要删键并重钉身份哈希。

关键澄清——回答你的核心问题：**「在公园能不能点办公」「在工作地点能不能点跑步」这两件事，菜单管不了，也不该由菜单管。** 菜单的判定单位是「规范动作」，`do` 一个名字同时承载办公、跑步、看剧、游戏，所以按地点类声明动作名在原理上就无法表达「工作学习只在工作地点」。现有实现里真正生效的地点约束是另外两条：运动按类别限 home/outdoor/fitness（`rules.py:53-61`，office 与 cafe 被拒，并有测试固定），指派工作内容要求人在 `workplace_location_id`（`admission.py:109-119` + `166-179`，缺席时以 `ACTIVITY_PRECONDITION_UNMET` 拒绝并提示前往）。也就是说：**动作与地点的绑定目前既不在数据集、也不在菜单，而是硬编码在系统的两条规则里**——这与用户「功能 app 绑定什么动作是环境数据集定义的，系统只提供绑定这个能力」的主张正好相反，是当前实现与目标设计之间最大的落差。若要按你的设计改，绑定单位应从「6 个规范动作 × 10 个地点类」下沉到「数据集动作 × 地点集合」，而 `menus_by_kind` 目前是「地点类型 → 商品」的商品索引且无消费者，不适合直接复用，需要新建一条「动作 → 地点集合」的目录级声明。 | `interaction_menus_by_location_kind` 声明在 `src/worldmock/life/scenario.py:315`，形状... |
| T1 persona 在一次决策机会里能看到/收到的全部内容（U14 观测与隐私边界清单） | U14「观测与隐私边界」现在展示的是「七层请求」，它不是七个并列的层，而是 system 一条系统层 JSON + user 一条由六层对象组成的 JSON 数组（工具层、人设层、记忆层、待办层、环境层、进度层）再加上原生 tools 定义；唯一权威定义在 docs/dev/SPEC-life-realism.md:566，代码入口是 src/worldmock/agents/life_prompt.py:115 与 custom/agents/life_agent.py:368。persona 能看到的东西与看不到的东西边界很清楚：看得到体感分级文字（饥饿五档、困意五档等）、当前地点名与场所类型标签、可达地点全貌（带出行方式、路费、时长、费力）、其他地点的商品（按店铺可售性并入，窗口上限默认 20，其余靠品类计数与按名/品类翻页查询补齐）、原地与自己两个受保护动作、暂停活动与个人事务、当日计划的意图/文字/提醒/大致时刻、本 session 的工具调用与结果、消息全文与邮箱主题正文、推送与送达进展、以及环境给出的上班安排（当班日班次、地点、休息时段与四种状态）。看不到的：自己的余额（balance_fen 在黑名单）、任何身体数值（energy_kcal/sleep_pressure/sleep_deficit_hours 在黑名单，只有分级文字）、地点坐标与地图几何、室内/室外这类环境属性（location_kinds 只用于活动资格判定）、内容目录全表（只在该 app 有未释放推送时露标题）、一切内部身份（agent_id/activity_id/location_id/store_id 等）与合同全表、私有绑定与观察私有证据。天气是完全没有实现的功能——代码、场景模型与七层里都没有天气字段，只有文档把它写成「场景可配置能力」并把「天气设定不向人物暴露」写在接口合同里。删改时要注意的边界：黑名单是 fail-closed 的（narration.py enforce_field_blacklist 命中即抛错，不是过滤），人设层 600 汉字超限报错不截断，记忆层 8000 字符超限按固定次序缩减，工具层可用性与环境拒绝共用同一判定函数、提示与拒绝必须一字不差；要新增「室内/室外」「天气」这类观测，得同时改环境层构造器、prompt_schemas 的环境层 Schema、提示指纹，以及系统层文案里对应的白话规则，否则 persona 会拿到一个它没有语言去理解的新字段。 | 请求的物理组成固定为四块：原生 tools 定义、一条 system 消息（系统层 JSON）、一条 user 消息（六层 JSON 数组）、以及同秒续接追加的... |
| T5 worldmock-dataset 执行记录检索：模型实际有没有用到室内/室外、天气这类信息 | 结论：**模型从来没有拿到过天气，也从来没有拿到过系统的室内/室外判定**——「室内/室外（气象与活动资格）」里真正被用到的只有『活动资格』那一半，且只在环境侧生效，气象一半在代码里根本不存在。具体分层：(1) 天气：引擎源码 0 命中、观察 schema 0 字段、环境数据 0 声明。所有 `天气` 都是模型自造（2026-10-06 week run:1220 的『天气转凉了记得添衣』），或来自短剧字幕（activity_progress 的『今天外面天气好』）与语料段子；自造内容还被写进记忆（memory_updated）再回灌 prompt，形成自证循环。这意味着 prompt 里出现的 `天气` 不能当证据证明模型在关注天气——它关注的是自己编的。删除或不管这些字样都不会损失任何系统能力，因为本来就没有对应输入。(2) 室内/室外：`location_kinds` 是真实存在的能力，但只作**运行期资格门**（rules.py:53-61 仅约束跑步类 exercise 的场所；retrieval.py:42 六值闭集；candidates.py 取交集）。它写进 observation 证据段，却**从未进入 model_request 的 messages（3065 条全 0）**；模型看到的地点只有 `地点：彩虹桥` 这种裸名。唯一一次把场所类送到模型眼前，是 2026-09-28 早期 run 把它**拼死在地点名字里**（locations.json:1005『青西郊野公园（户外活动环境）』），2026-09-29 起已移除，当前环境只剩 kind。因此用户 D7 的判断是对的：模型从未"考虑过"这个字段——它没有这个输入；能观察到的只是模型自己从『不可用清单』反推场所（business run:2334）或靠地名猜（week run:5914）。删掉 location_kinds 从提示角度**零损失**（它本就不在提示里）；但删掉它会**打断 rules.py:53-61 的跑步地点限制与 retrieval/candidates 的交集门**，需要连 `PLACE_KIND_ENVIRONMENTS`（types.py:101-106）和 admission 校验一起处理。派生量 `activity_summary` 里的 `outdoor` 与 action id `outdoor-park-walk` 是**活动品类/id，与场所类同名而已**，改场所类不会影响它们，但检索时容易被误认成同一件事。 | 路径已定位：/Users/atishoo/Project/WorldMock-Dataset（即用户说的 worldmock-dataset），本仓另有 /Us... |
| 对抗性核查 | 这是什么：四个核查点里，两个上游结论错了、一个是范围错、两个漏了关键消费者。逐条。(1)「室内/室外」确实有真实消费者，且只有一个——src/worldmock/life/rules.py:57 的运动地点资格，两条活入口是 action_run.py:55 与 tool_layer.py:243；在真实数据集上它每天都会触发（103 地点里只有 24 个放行运动），但拒绝理由从不进 model_request（8 个 run 全 0），只落在 observation 私有证据里，而其中 45 个运动动作名里只有 1 个（骑行）曾出现在提示里。第二处 candidates.py:344-351 是真死路（candidates.py:336 提前 return，apply_location_kinds 全仓无置真处），第三处 catalog/models.py:227 只有测试调用。所以 T2「只有一个真实消费者、其余是校验或死语义」成立，但 T5 把 candidates.py:344 引成「恒可行」是反的——那是排除分支，恒可行在 347-348。

(2)「动作绑定地点」这条成立且是硬拒绝（三层一致，绕过宿主也拦得住），但今天对结果零影响：三个 fixture 加 dataset 全部 10 个场景的 10 个地点类都声明同一份 6 动作全集，有效菜单恒等于全集，且 8 个 run 里 INTERACTION_MENU_DENIED 命中 0。更重要的是它原理上做不到「按地点限动作」——判定单位是 6 个规范动作、键是地点类而不是地点实例，`do` 一个名字同时承载办公与跑步。真正在绑的是两条硬编码规则（rules.py 的运动类别限制、admission.py 的职场地要求），与你的「绑定单位应是数据集动作×地点集合」正好相反，这是当前实现与目标设计之间最大的落差。

(3) 商品多地点今天只能实例级列举（store_ids 元组）或用 \"all\"，而 \"all\" 的语义是「类型可供给该类别的店铺」，靠引擎固定表 GOODS_CATEGORY_STORE_KINDS 展开——它是登记期拒绝条件同时也是展开器，数据集既填不进去也不该填。所以「可售商品类别」与「商品绑地点集合」不重合：前者是引擎侧硬约束，后者是数据集侧实例声明；两层叠加「同类型店铺全绑或全不绑」后，实例列举在效果上等于类型级声明。dataset 157 件商品里 152 件逐字列举 26/50/60 个 location_id。

(4)「每个环境文件只有一份」今天不成立：shanghai-simplified 下 catalog/ 与 sources/catalog/ 六个文件逐字节全不同，10 份 scenarios 与 sources/scenarios/ 的 10 份并存，而 validation.py 对根下其他目录既不读也不拒（已被 tests/test_validation.py:97-104 固化为断言）。

上游漏掉、且会改变「kind 能否删」结论的事实有三条：kind 经 environment_layer.py:66 的 PLACE_TYPES 变成 place_type 进入 persona 提示（实测 143/159 条），所以「kind 对 persona 不可见」是错的；projection.py:303 与 name_resolution.py:201 用 kind == \"parcel_station\" 决定 persona 是否拿到「领取」动作，是两条直接可见的分支；admission.py:220 对 STORE_KINDS 地点强制 merchant 账户（0002 场景 33 个地点对 33 个账户），改 kind 要连带改账户合同。另外 T1 的「找不到现行七层请求」只在 WorldMock/runs 成立（0/57），WorldMock-Dataset 里有 143/159，属范围错误。

能不能删：kind 字段不能删——它是交互菜单（interaction_menu.py:58）、persona 可见的场所中文名（environment_layer.py:66）、事件地点目录（observe.py:420）、「领取」动作判据（projection.py:303、name_resolution.py:201）、运动资格（rules.py:57）、商品菜单与准入（loader.py:85、admission.py:97,220）、场景菜单全覆盖校验（scenario.py:377）与加载校验（load_checks.py:42）的共同载体。PLACE_KIND_ENVIRONMENTS 这张表单独删会同时坏三处 import（ImportError，不是 T2 说的 KeyError——KeyError 只在保留表但删掉某个 kind 时出现，且因 presentation.py:124-126 每次观察急切构造而立刻炸掉整个观察）、两处测试与「公园里能不能做运动」这一条真实行为；正确的做法是把 outward 归属内联成显式的「可运动场所」集合，而不是留下这张把 venue 一锅端的映射表。interaction_menus_by_location_kind 技术上能删且今天删了不改变行为，但那是撤掉一条已冻结的实验身份合同（菜单进场景原始字节），删前需先裁决；同族的 retrieval.py:42 LOCATION_KINDS 是无任何生产导入者的彻底死码，可与本次清理一并处置。另有两处文档漂移要顺手修：CONTRACTS.md:180 与 SPEC-interaction-menu.md:78 的「八个 LocationKind」应为十个。 | 核查1（室内/室外）：PLACE_KIND_ENVIRONMENTS 有且只有一个改变行为的消费者——src/worldmock/life/rules.py:5... |

### 详细判定

#### 1. T4 商品绑定地点集合的现状声明方式，以及一个环境数据集包里有几份文件

**判定**：商品绑定地点集合今天的**唯一声明方式是实例级列举**：`Goods.store_ids` 写一个 `location_id` 有序去重元组，或写 `"all"`。`"all"` 不是「所有地点」，而是「类型能供给该类别的全部店铺」——展开规则硬编码在 `src/worldmock/catalog/loader.py:85-86`，判别表是引擎包内固定的 `GOODS_CATEGORY_STORE_KINDS`（`src/worldmock/exchange/types.py:91-100`）。因此「多地点有售」有两种写法但一种粒度：列举多个实例，或用 `"all"` 让引擎按类型表代填；数据集中**不存在**任何类型级（商品→地点类型）供给声明，类型只在引擎侧存在，且只以「限制哪些类型可卖该类」的方式介入登记与展开。注册期还叠加一条「同类型店铺全绑或全不绑」的一致性约束（`admission.py:99-101`），使实例级列举在效果上等于类型级声明，`menus_by_kind`（`loader.py:89-90`）就架在这条不变量上——但它在生产链路没有消费者，只有测试引用。包结构上，`catalog/` 与 `scenarios/` 是「一份 catalog 配 N 份 scenario」：目录解析写死为场景上两级的 `catalog`（`life/semantics.py:60-62`），`validate --environment` 对 `scenarios/` 下所有 JSON 复用同一 catalog 加载（`validation.py:137-153`）。「每个环境集每种文件只有一份」**今天不成立**：正式环境 `shanghai-simplified` 同时有 `catalog/goods.json` 与 `sources/catalog/goods.json`，五个目录文件全部逐字节不同，且 `sources/` 既不被读取也不被拒绝（`validation.py:117-134`，行为断言见 `tests/test_validation.py:97-104`）；`docs/CONTRACTS.md:49`/`:57` 的「环境根只有 catalog 与 scenarios」是文档声明，不是被强制的不变量。能否删改：`store_ids` 字段本身动不得——删掉它 `Goods` 立即缺必填字段（`loader.py:66-68` 把 `store_ids` 缺失映射为 `GOODS_BINDING_MISSING`），`goods_by_store` 无法建立，购买与呈现三处直接断链（`basket.py:117`、`discovery.py:48`、`purchase_offers.py:34-35`）。`"all"` 分支删不得——`admission.py:92-93` 与 `loader.py:86` 两处成对存在，删掉任一侧会让 5 件在用商品无法加载或无法展开。`GOODS_CATEGORY_STORE_KINDS` 表删不得——它同时是注册拒绝条件（`admission.py:97`）与展开过滤器（`loader.py:85`），且被两条源码变异验收钉住（`tests/acs_acceptance.py:1160`、`tests/rrs_acceptance.py:1372`）。真要放开「按类型声明」，需要的是新增字段（提案 `PROPOSAL:606` 的 `store_kinds`）让登记期展开到实例级，而不是复用现有表——现有表是引擎固定规则，数据集填不进去，也不该由数据集填。另需注意两处文档漂移在此相关：`docs/CONTRACTS.md:693` 与 `:1668` 写「五个商品类别」，实际六类含 `fuel`；`docs/CONTRACTS.md:180` 写「八个地点类型」，实际十类。

**关键发现**：

1. 商品侧唯一的地点声明字段是 `Goods.store_ids`，取值形态是「地点实例 id 的有序去重元组」或字面量 `"all"`，没有第三个分支：`src/worldmock/catalog/models.py:73-80`（字段定义在 `:80` `store_ids: tuple[NonEmptyString, ...] | Literal["all"]`，字段类型是 tuple 而非集合，所以顺序即声明顺序）。

2. `Goods` 上没有任何 app 字段，商品不能直接绑 app；app 只能经店铺 `app_ids` 间接到达商品：`src/worldmock/catalog/models.py:73-80`（对照 `src/worldmock/catalog/models.py:52-70` 的 `Location.app_ids`）。

3. 登记期对 `store_ids` 的校验集中在 `_check_goods`：`src/worldmock/catalog/admission.py:87-101`。`store_ids == "all"` 直接 `continue` 跳过全部校验（`admission.py:92-93`），因此 `"all"` 的正确性完全由 loader 的展开规则兜底，而不是由登记期枚举。

4. 显式列表形态的登记规则有四条：非空（空列表 → `GOODS_BINDING_MISSING`，`admission.py:94-95`）、引用已登记地点且去重排序（`admission.py:96` 经 `_check_bindings`，规则见 `admission.py:58-64`）、每个店铺类型必须落在该类别可供给表中否则 `GOODS_STORE_KIND_INVALID`（`admission.py:97-98`）、以及同类型店铺「全绑或全不绑」否则 `GOODS_BINDING_KIND_INCONSISTENT`（`admission.py:99-101`）。

5. `"all"` 的确切语义是「全部类型可供给该类别的店铺」而不是「所有地点」：`src/worldmock/catalog/loader.py:83-88`，展开条件写在同一行的两个合取项 —— `place.kind in GOODS_CATEGORY_STORE_KINDS[goods.category] and (goods.store_ids == "all" or place.location_id in goods.store_ids)`（`:85-86`）。`home`/`office`/`park` 等非店铺类型永远得到空菜单（`tests/unit/catalog/test_loader.py:25` 断言这三个地点为 `()`）。

**代码证据**：53 处

**未找到**：

- 类型级的商品供给声明字段 `store_kinds`：在 `src/`、`tests/`、`custom/` 全量 grep 零命中，唯一出现是未冻结提案 `docs/dev/PROPOSAL-environment-dataset-contract.md:606`，未实现。
- 环境根级别的文件份数校验：grep `root.iterdir`、`root.glob`、`环境根` 于 `src/worldmock/validation.py` 零命中；除 `catalog/` 内部（`admission.py:31-37`）外没有「每个文件只有一份」的强制点。
- `experiments/published-environment/`：目录不存在（`ls -d experiments` 返回 ABSENT），尽管 `CLAUDE.md` 的命令示例与 `docs/dev/SPEC-environment-data-ownership.md:75` 引用该路径。

---

#### 2. T2 PLACE_KIND_ENVIRONMENTS 有没有真实消费者

**判定**：PLACE_KIND_ENVIRONMENTS 有且只有一个真实影响模拟结果的消费者：src/worldmock/life/rules.py:57 的 Exercise 活动地点资格判定（被 action_run.py:55 与 tool_layer.py:243 两条真实入口调用）。在这条判定里，home/park/fitness 三种 kind 分别派生为 home/outdoor/fitness 而通过，office 与全部六种 venue 场所被拒绝并抛 ELIGIBILITY_LOCATION_KIND_MISMATCH。所以「室内/室外（outdoor vs venue）的区分会不会改变模拟结果」的答案是：会，但仅限于「运动能不能在这里做」这一件事——它不改变出行、不改变结算、不改变身体模型、也不与天气发生任何关系（全仓库 Python 代码零 weather 命中，不存在天气-地点耦合）。另一条看似更广的路径（候选条目的地地点类过滤，candidates.py:344-351）虽然每次观察都会构造（presentation.py:124-126、428），但因为 observe.py:221 恒传 moment=False 且 apply_location_kinds 全仓库从未置真，_location_reason 在 candidates.py:336 提前返回 None，属于计算了却不可达的死语义；catalog/models.py:227 的 Catalog.location_kinds 则只有测试调用。能否删：删 kind 字段不行——它是交互菜单（interaction_menu.py:55-58）、人设提示的场所中文名（environment_layer.py:63-66）、事件级地点目录（observe.py:420）、商品菜单与目录准入（loader.py:85,91；admission.py:80,83,90,97,220）、场景菜单全覆盖校验（scenario.py:377）与加载校验（load_checks.py:42-48，world_setup.py:40）的共同载体，删掉会直接坏掉运行。单独删 PLACE_KIND_ENVIRONMENTS 这张表则只坏三处代码加两处测试：rules.py:57（运动资格，必须把 home/outdoor/fitness 判定内联保留）、candidates.py:72 的 LocationView.from_config（会 KeyError，且因 presentation.py:428 每次观察都急切求值，即使值不被读也会把整个观察打断）、catalog/models.py:227（仅测试调用）；测试侧 tests/unit/exchange/test_types.py:41 与 tests/test_single_source.py:47 会失败；文档侧 docs/CONTRACTS.md:182、1235 以及 SPEC-life-realism、SPEC-resource-and-rule-simplification、SPEC-life-runtime、SPEC-action-and-content-simplification 的多处双向引用需同步，否则文档引用门会失败。若要「删表但留行为」，正确做法是把 outdoor 归属改成从 PLACE_TYPES 或一个显式的「可运动场所」集合派生，而不是保留这张把 venue 一锅端的映射表——因为 place_type 中文表（readable.py:23）与商品供给表（GOODS_CATEGORY_STORE_KINDS）才是 kind 真正的语义归属。附注：同族的 LOCATION_KINDS（retrieval.py:42）与整个 retrieval 模块是一片更彻底的死代码，无任何生产导入者，可与本次清理一并处置。

**关键发现**：

1. PLACE_KIND_ENVIRONMENTS 定义在 src/worldmock/exchange/types.py:101-106，把 10 种 LocationKind 映射到 5 个环境名 home/office/outdoor/fitness/venue（restaurant、cafe、convenience_store、supermarket、gas_station、parcel_station 全部是 venue）。

2. 读取点共 3 处生产代码 + 2 处测试登记 + 1 处文档契约：src/worldmock/life/rules.py:9(import)/:57(使用)、src/worldmock/catalog/models.py:16(import)/:227(使用)、src/worldmock/life/candidates.py:15(import)/:72(使用)；测试是 tests/unit/exchange/test_types.py:9,41 与 tests/test_single_source.py:47。

3. 唯一真实影响行为的读取点：src/worldmock/life/rules.py:57，判定 Exercise 类别活动是否可在当前地点开始；允许集合是 {home, outdoor, fitness}，即 kind 为 home、park、fitness 的地点可做运动，office 与全部 venue 类型被拒绝，错误码 ELIGIBILITY_LOCATION_KIND_MISMATCH（rules.py:60）。

4. 该运动判定是活代码，有两条真实入口：src/worldmock/life/action_run.py:55（prepare_action，实际开始动作时抛 BusinessRuleError）与 src/worldmock/life/tool_layer.py:243（工具层第二次调用，用于提前拒绝并把拒绝返回给模型）。

5. 第二个读取点 src/worldmock/catalog/models.py:227（Catalog.location_kinds）没有任何生产调用者——全仓库 .location_kinds( 的调用只在 tests/unit/catalog/test_loader.py:40,41 与 tests/unit/catalog/test_supplement.py:39。它是纯测试/展示面接口。

**代码证据**：49 处

**未找到**：

- grep -rni "weather" --include="*.py"（全仓库）：0 命中——不存在任何天气字段、天气模型或天气-地点耦合代码。
- grep -rni "天气" src/ custom/：0 命中；Python 中「天气」仅存在于 tests/unit/evaluation/test_behavior_metrics.py:177,186,187,204 的观测字符串 fixture。
- grep -rn "apply_location_kinds" src custom tests：只有 src/worldmock/life/candidates.py:124 的默认值 False 与 candidates.py:334、presentation.py:412 的文档字符串；**没有任何一处把它设为 True**，故候选的地点类过滤在生产不可达。

---

#### 3. T3 动作与地点的绑定关系（interaction_menus_by_location_kind）

**判定**：这是什么：`interaction_menus_by_location_kind` 是**场景文档**里的一个必填映射，键是系统闭集 `LocationKind` 的 10 个场所类型，值是 `InteractionMenu{allowed_actions}`，取值只能来自 6 个内部规范动作（travel_to / sleep / wait / purchase / do / collect_parcel，且必须保留 wait 与 sleep）。它经 `resolve_interaction_menu` 与人物自己的 `interaction_menu` 求交，得到本轮有效菜单，然后被三处消费：宿主的协议校验、工具层的可用性标注、以及环境侧授权结算前的权威重验——后者是硬拒绝（`INTERACTION_MENU_DENIED`），绕过宿主也拦得住。

能不能删：**技术上能，且当前删掉不改变任何行为**。三份场景夹具的 10 个地点类都声明同一份 `START_ACTIVITY_ACTIONS` 全集，人物菜单全为 `null`，交集恒等于全集，所以这个字段今天对模拟结果零影响。但删它不是「清理死代码」而是**撤掉一条已冻结的特性合同**：`docs/dev/SPEC-interaction-menu.md`、`docs/CONTRACTS.md:180`、`docs/EXPERIMENT.md:85` 都把它列为实验条件（菜单声明进入场景原始字节身份，改菜单＝改实验身份），`docs/dev/SPEC-interaction-menu.md:69` 冻结了「只按地点类、不按具体地点」的粒度，`menu_execution.py` 的 `INTERACTION_MENU_DENIED` 与 `guidance.py:31`/`:104` 的两种说法也随之作废。删之前需要先裁决这个特性是否保留。

删了什么会坏：`scenario.py:315/377` 的必填与完整性校验、`_check_menu_actions`、`LifeScenario`/`RuntimeScenario` 的 `model_validate`（`tests/integration/life/test_public_interface_guards.py:241` 断言 10 键）、`tests/life/test_interaction_menu_*.py` 全套、`menu_execution.require_interaction_menu` 及其在 `session_actions.py:970` 的调用、`tool_layer.py:89-91` 的拒绝分支、`projection.py:231-233` 的过滤与 `environment_layer.py:54-55` 的 `fixed_rules` 句、`custom/agents/life_menu.py` 整个模块与 `life_agent.py:399/491`、`narration.py:30` 的禁键条目、以及 `docs/CONTRACTS.md`/`SPEC-interaction-menu.md`/`EXPERIMENT.md` 的相关条款与 ANASYS 映射。夹具场景需要删键并重钉身份哈希。

关键澄清——回答你的核心问题：**「在公园能不能点办公」「在工作地点能不能点跑步」这两件事，菜单管不了，也不该由菜单管。** 菜单的判定单位是「规范动作」，`do` 一个名字同时承载办公、跑步、看剧、游戏，所以按地点类声明动作名在原理上就无法表达「工作学习只在工作地点」。现有实现里真正生效的地点约束是另外两条：运动按类别限 home/outdoor/fitness（`rules.py:53-61`，office 与 cafe 被拒，并有测试固定），指派工作内容要求人在 `workplace_location_id`（`admission.py:109-119` + `166-179`，缺席时以 `ACTIVITY_PRECONDITION_UNMET` 拒绝并提示前往）。也就是说：**动作与地点的绑定目前既不在数据集、也不在菜单，而是硬编码在系统的两条规则里**——这与用户「功能 app 绑定什么动作是环境数据集定义的，系统只提供绑定这个能力」的主张正好相反，是当前实现与目标设计之间最大的落差。若要按你的设计改，绑定单位应从「6 个规范动作 × 10 个地点类」下沉到「数据集动作 × 地点集合」，而 `menus_by_kind` 目前是「地点类型 → 商品」的商品索引且无消费者，不适合直接复用，需要新建一条「动作 → 地点集合」的目录级声明。

**关键发现**：

1. `interaction_menus_by_location_kind` 声明在 `src/worldmock/life/scenario.py:315`，形状是 `dict[LocationKind, InteractionMenu]`；`InteractionMenu` 只有一个字段 `allowed_actions: tuple[str, ...]`（`src/worldmock/life/interaction_menu.py:30-42`），取值必须是 `START_ACTIVITY_ACTIONS` 闭集成员，且每份非空声明必须包含保护项 `wait`、`sleep`（`interaction_menu.py:20-22`、`:35-42`）。

2. 来源是场景文档，不是数据集：字段是场景根对象的必填键，键取自系统闭集 `LocationKind`（`src/worldmock/exchange/types.py:7-19`）；场景的 `locations[]` 只声明 `location_id`，`kind` 由目录登记决定（`tests/fixtures/environment/scenarios/single-person.json` 的 locations 无 `kind` 键，`life/models.py` 的 `Location` 才有）。

3. 完整性是强制的：`src/worldmock/life/scenario.py:375-378` 要求 `set(interaction_menus_by_location_kind) == set(LocationKind)`，缺项、多项、通配键都被拒绝；`LocationKind` 现有 10 个成员（home/office/park/fitness/restaurant/cafe/convenience_store/supermarket/gas_station/parcel_station，`exchange/types.py:7-19`），所以必须恰好声明 10 份。

4. 文档与代码不一致（文档漂移）：`docs/CONTRACTS.md:180` 与 `docs/dev/SPEC-interaction-menu.md:78` 写「八个 `LocationKind`」，`docs/manual/scenarios.md:161` 与 `docs/dev/SPEC-action-and-content-simplification.md:325` 写「十个」；代码与三份测试夹具都是 10。`docs/dev/SPEC-interaction-menu.md:45` 列举标准动作闭集只有 5 个（travel_to/sleep/wait/purchase/do），而代码 `exchange/types.py:112-114` 是 6 个（多 `collect_parcel`）。

5. 地点是否限制动作集合：机制存在且是硬拒绝，但粒度极粗。判定只看**内部标准动作名**是否落在菜单的交集里（`interaction_menu.py:70-83`），`allowed_actions` 只有 6 个规范动作，`do` 一个名字覆盖全部目录动作——所以菜单**无法**区分「办公」和「跑步」，而地点类菜单是按 `location.kind`（10 类）而非 `location_id` 声明的。

**代码证据**：82 处

**未找到**：

- 按关键字 `interaction_menus_by_location_kind` 与 `menus_by_kind` 全仓 grep（排除 `__pycache__`、`.mypy_cache`、`.work/`），并解析所有 `**/*.json`：**没有任何一份场景声明了按地点类区分开的菜单**。仅 3 个文件含该键（`tests/fixtures/environment/scenario
- `experiments/published-environment/scenarios/single-person.json` **不存在**：仓库内没有 `experiments/` 目录（`ls experiments` → No such file or directory），也没有 `scenarios/` 目录（`git ls-files 'scenarios/*.json'` 为空）
- **没有**按 `location_id`（具体地点实例）声明菜单的字段：`interaction_menus_by_location_kind` 的键类型是 `LocationKind`（10 类），`docs/dev/SPEC-interaction-menu.md:69` 明确冻结「只按现有 `LocationConfig.kind`，不新增具体地点覆盖或时段策略」。

---

#### 4. T1 persona 在一次决策机会里能看到/收到的全部内容（U14 观测与隐私边界清单）

**判定**：U14「观测与隐私边界」现在展示的是「七层请求」，它不是七个并列的层，而是 system 一条系统层 JSON + user 一条由六层对象组成的 JSON 数组（工具层、人设层、记忆层、待办层、环境层、进度层）再加上原生 tools 定义；唯一权威定义在 docs/dev/SPEC-life-realism.md:566，代码入口是 src/worldmock/agents/life_prompt.py:115 与 custom/agents/life_agent.py:368。persona 能看到的东西与看不到的东西边界很清楚：看得到体感分级文字（饥饿五档、困意五档等）、当前地点名与场所类型标签、可达地点全貌（带出行方式、路费、时长、费力）、其他地点的商品（按店铺可售性并入，窗口上限默认 20，其余靠品类计数与按名/品类翻页查询补齐）、原地与自己两个受保护动作、暂停活动与个人事务、当日计划的意图/文字/提醒/大致时刻、本 session 的工具调用与结果、消息全文与邮箱主题正文、推送与送达进展、以及环境给出的上班安排（当班日班次、地点、休息时段与四种状态）。看不到的：自己的余额（balance_fen 在黑名单）、任何身体数值（energy_kcal/sleep_pressure/sleep_deficit_hours 在黑名单，只有分级文字）、地点坐标与地图几何、室内/室外这类环境属性（location_kinds 只用于活动资格判定）、内容目录全表（只在该 app 有未释放推送时露标题）、一切内部身份（agent_id/activity_id/location_id/store_id 等）与合同全表、私有绑定与观察私有证据。天气是完全没有实现的功能——代码、场景模型与七层里都没有天气字段，只有文档把它写成「场景可配置能力」并把「天气设定不向人物暴露」写在接口合同里。删改时要注意的边界：黑名单是 fail-closed 的（narration.py enforce_field_blacklist 命中即抛错，不是过滤），人设层 600 汉字超限报错不截断，记忆层 8000 字符超限按固定次序缩减，工具层可用性与环境拒绝共用同一判定函数、提示与拒绝必须一字不差；要新增「室内/室外」「天气」这类观测，得同时改环境层构造器、prompt_schemas 的环境层 Schema、提示指纹，以及系统层文案里对应的白话规则，否则 persona 会拿到一个它没有语言去理解的新字段。

**关键发现**：

1. 请求的物理组成固定为四块：原生 tools 定义、一条 system 消息（系统层 JSON）、一条 user 消息（六层 JSON 数组）、以及同秒续接追加的 assistant/tool 消息——system 与 user 合计才叫「七层」（`src/worldmock/agents/life_prompt.py:115`、`custom/agents/life_agent.py:368`、`custom/agents/life_agent.py:372`）。

2. 七层的完整定义只在一处：`docs/dev/SPEC-life-realism.md:566`「七层请求」，六层 user 键序为 工具层、人设层、记忆层、待办层、环境层、进度层（`src/worldmock/agents/life_prompt.py:100`、`src/worldmock/life/prompt_schemas.py:267`）。

3. 原生工具面在生活决策里是「七个世界请求工具 + 三个只读查询（query_world、recall_task_summary、三个 memory_*）」，逐层看是 REQUEST_MODELS 八个减去 recall_task_summary 得七个世界请求工具，而查询工具只剩最后一个回合或截断重试时整批撤下（`custom/agents/life_agent.py:156`、`custom/agents/life_agent.py:164`、`custom/agents/life_agent.py:170`、`custom/agents/life_agent.py:172`、`custom/agents/life_agent.py:339`、`src/worldmock/life/models.py:1314`）。

4. 系统层是整次运行逐字节不变的两份固定 JSON（生活决策与只读问答各一份），生活决策有十八条 rules，只写交互规则，不写任何世界事实（`src/worldmock/agents/life_prompt.py:21`、`src/worldmock/agents/life_prompt.py:71`、`src/worldmock/agents/life_prompt.py:104`、`docs/dev/SPEC-life-realism.md:574`）。

5. 【身体】persona 只拿到五项体感分级文字，拿不到任何数值：环境层 body 五键 appetite/sleepiness/entertainment/social/mood 取值分别是 不饿/有点想吃/饿/很饿/饿得难受/饿得发慌、清醒/有点乏/困/很困/撑不住、不闷/有点想放松/很想放松、不想找人/想和人说说话/有点孤单、很烦/有点烦/平静/挺好/很开心（`src/worldmock/life/environment_layer.py:31`、`src/worldmock/life/environment_layer.py:36`、`src/worldmock/life/felt.py:55`、`src/worldmock/life/felt.py:70`、`src/worldmock/life/felt.py:81`、`src/worldmock/life/felt.py:90`...

**代码证据**：78 处

**未找到**：

- 天气：grep -rn "天气|weather|气温|降雨|temperature" 于 src/ custom/ catalog/ experiments/（排除 vendor 与 docs）零命中；src/worldmock/profile/extractor.py:53,185 的 TEMPERATURE=0.3 是模型采样温度，与气象无关。天气只在 docs/ARCHITECTURE.md
- 室内/室外属性：grep -rn "indoor|outdoor|室内|室外" 命中的都在 catalog/exchange 的 location_kinds 与 PLACE_KIND_ENVIRONMENTS（src/worldmock/exchange/types.py:101-106）以及测试与文档里，用途是生活活动的地点类资格判定与候选排除；环境层给的是 PLACE_TYPES 的十种场所
- 地点坐标：grep -rn "coordinate|latitude|longitude|\"x\"|\"y\"" 于 src/worldmock/life/ 零命中。地点模型（src/worldmock/catalog/models.py:57-70）无坐标字段；场景图上只有 distance_meters 与 path，且 route_row 的输出（src/worldmock/life/di

---

#### 5. T5 worldmock-dataset 执行记录检索：模型实际有没有用到室内/室外、天气这类信息

**判定**：结论：**模型从来没有拿到过天气，也从来没有拿到过系统的室内/室外判定**——「室内/室外（气象与活动资格）」里真正被用到的只有『活动资格』那一半，且只在环境侧生效，气象一半在代码里根本不存在。具体分层：(1) 天气：引擎源码 0 命中、观察 schema 0 字段、环境数据 0 声明。所有 `天气` 都是模型自造（2026-10-06 week run:1220 的『天气转凉了记得添衣』），或来自短剧字幕（activity_progress 的『今天外面天气好』）与语料段子；自造内容还被写进记忆（memory_updated）再回灌 prompt，形成自证循环。这意味着 prompt 里出现的 `天气` 不能当证据证明模型在关注天气——它关注的是自己编的。删除或不管这些字样都不会损失任何系统能力，因为本来就没有对应输入。(2) 室内/室外：`location_kinds` 是真实存在的能力，但只作**运行期资格门**（rules.py:53-61 仅约束跑步类 exercise 的场所；retrieval.py:42 六值闭集；candidates.py 取交集）。它写进 observation 证据段，却**从未进入 model_request 的 messages（3065 条全 0）**；模型看到的地点只有 `地点：彩虹桥` 这种裸名。唯一一次把场所类送到模型眼前，是 2026-09-28 早期 run 把它**拼死在地点名字里**（locations.json:1005『青西郊野公园（户外活动环境）』），2026-09-29 起已移除，当前环境只剩 kind。因此用户 D7 的判断是对的：模型从未"考虑过"这个字段——它没有这个输入；能观察到的只是模型自己从『不可用清单』反推场所（business run:2334）或靠地名猜（week run:5914）。删掉 location_kinds 从提示角度**零损失**（它本就不在提示里）；但删掉它会**打断 rules.py:53-61 的跑步地点限制与 retrieval/candidates 的交集门**，需要连 `PLACE_KIND_ENVIRONMENTS`（types.py:101-106）和 admission 校验一起处理。派生量 `activity_summary` 里的 `outdoor` 与 action id `outdoor-park-walk` 是**活动品类/id，与场所类同名而已**，改场所类不会影响它们，但检索时容易被误认成同一件事。

**关键发现**：

1. 路径已定位：/Users/atishoo/Project/WorldMock-Dataset（即用户说的 worldmock-dataset），本仓另有 /Users/atishoo/Project/WorldMock 的 runs/ 与 .work/ 执行记录。

2. 该仓执行记录分两层：execution/ 下 17 个过程记录目录（多为报告、审计、构建日志），真正的**模型观察/prompt 记录**在 requests/*/runs/*/worldmock-export/events.jsonl.gz，共 8 个文件、3065 条 model_request + 3065 条 model_response。

3. model_request 事件里 data.request.messages 是**完整原始 prompt**（system + user 七层 JSON），model_response 事件里 data.response 是完整模型返回；这是可用的执行记录主体，检索以它为准。

4. 引擎源码里**完全没有天气**：在 src/worldmock 与 custom 全量 grep `天气|weather|气温` 命中 0 处。

5. observation 事件的字段闭集共 49 个键（active_activities…world_query_page_size），无任何 weather/温度/降雨字段。

**代码证据**：31 处

**未找到**：

- 在 /Users/atishoo/Project/WorldMock 的 src/worldmock 与 custom 全量 grep `天气|weather|气温`（*.py）：0 处命中。引擎不产生、不传送任何天气事实。
- 在 8 个 run、3065 条 model_request 的 messages 正文里 grep `location_kinds` / `business_kind` / `latitude` / `longitude`：全部 0 命中。场所类从未进入模型提示。
- 在 observation 事件 49 个键里找 weather/temp/rain/sun/climate 类字段：0 个。观察 schema 无天气槽位。

---

#### 6. 对抗性核查

**判定**：这是什么：四个核查点里，两个上游结论错了、一个是范围错、两个漏了关键消费者。逐条。(1)「室内/室外」确实有真实消费者，且只有一个——src/worldmock/life/rules.py:57 的运动地点资格，两条活入口是 action_run.py:55 与 tool_layer.py:243；在真实数据集上它每天都会触发（103 地点里只有 24 个放行运动），但拒绝理由从不进 model_request（8 个 run 全 0），只落在 observation 私有证据里，而其中 45 个运动动作名里只有 1 个（骑行）曾出现在提示里。第二处 candidates.py:344-351 是真死路（candidates.py:336 提前 return，apply_location_kinds 全仓无置真处），第三处 catalog/models.py:227 只有测试调用。所以 T2「只有一个真实消费者、其余是校验或死语义」成立，但 T5 把 candidates.py:344 引成「恒可行」是反的——那是排除分支，恒可行在 347-348。

(2)「动作绑定地点」这条成立且是硬拒绝（三层一致，绕过宿主也拦得住），但今天对结果零影响：三个 fixture 加 dataset 全部 10 个场景的 10 个地点类都声明同一份 6 动作全集，有效菜单恒等于全集，且 8 个 run 里 INTERACTION_MENU_DENIED 命中 0。更重要的是它原理上做不到「按地点限动作」——判定单位是 6 个规范动作、键是地点类而不是地点实例，`do` 一个名字同时承载办公与跑步。真正在绑的是两条硬编码规则（rules.py 的运动类别限制、admission.py 的职场地要求），与你的「绑定单位应是数据集动作×地点集合」正好相反，这是当前实现与目标设计之间最大的落差。

(3) 商品多地点今天只能实例级列举（store_ids 元组）或用 \"all\"，而 \"all\" 的语义是「类型可供给该类别的店铺」，靠引擎固定表 GOODS_CATEGORY_STORE_KINDS 展开——它是登记期拒绝条件同时也是展开器，数据集既填不进去也不该填。所以「可售商品类别」与「商品绑地点集合」不重合：前者是引擎侧硬约束，后者是数据集侧实例声明；两层叠加「同类型店铺全绑或全不绑」后，实例列举在效果上等于类型级声明。dataset 157 件商品里 152 件逐字列举 26/50/60 个 location_id。

(4)「每个环境文件只有一份」今天不成立：shanghai-simplified 下 catalog/ 与 sources/catalog/ 六个文件逐字节全不同，10 份 scenarios 与 sources/scenarios/ 的 10 份并存，而 validation.py 对根下其他目录既不读也不拒（已被 tests/test_validation.py:97-104 固化为断言）。

上游漏掉、且会改变「kind 能否删」结论的事实有三条：kind 经 environment_layer.py:66 的 PLACE_TYPES 变成 place_type 进入 persona 提示（实测 143/159 条），所以「kind 对 persona 不可见」是错的；projection.py:303 与 name_resolution.py:201 用 kind == \"parcel_station\" 决定 persona 是否拿到「领取」动作，是两条直接可见的分支；admission.py:220 对 STORE_KINDS 地点强制 merchant 账户（0002 场景 33 个地点对 33 个账户），改 kind 要连带改账户合同。另外 T1 的「找不到现行七层请求」只在 WorldMock/runs 成立（0/57），WorldMock-Dataset 里有 143/159，属范围错误。

能不能删：kind 字段不能删——它是交互菜单（interaction_menu.py:58）、persona 可见的场所中文名（environment_layer.py:66）、事件地点目录（observe.py:420）、「领取」动作判据（projection.py:303、name_resolution.py:201）、运动资格（rules.py:57）、商品菜单与准入（loader.py:85、admission.py:97,220）、场景菜单全覆盖校验（scenario.py:377）与加载校验（load_checks.py:42）的共同载体。PLACE_KIND_ENVIRONMENTS 这张表单独删会同时坏三处 import（ImportError，不是 T2 说的 KeyError——KeyError 只在保留表但删掉某个 kind 时出现，且因 presentation.py:124-126 每次观察急切构造而立刻炸掉整个观察）、两处测试与「公园里能不能做运动」这一条真实行为；正确的做法是把 outward 归属内联成显式的「可运动场所」集合，而不是留下这张把 venue 一锅端的映射表。interaction_menus_by_location_kind 技术上能删且今天删了不改变行为，但那是撤掉一条已冻结的实验身份合同（菜单进场景原始字节），删前需先裁决；同族的 retrieval.py:42 LOCATION_KINDS 是无任何生产导入者的彻底死码，可与本次清理一并处置。另有两处文档漂移要顺手修：CONTRACTS.md:180 与 SPEC-interaction-menu.md:78 的「八个 LocationKind」应为十个。

**关键发现**：

1. 核查1（室内/室外）：PLACE_KIND_ENVIRONMENTS 有且只有一个改变行为的消费者——src/worldmock/life/rules.py:57 的 `PLACE_KIND_ENVIRONMENTS[location.kind] in {"home","outdoor","fitness"}`，只作用于 ActionCategory.EXERCISE，其余四类在 src/worldmock/life/rules.py:55 直接放行。

2. 核查1：该判定是活代码且真实可达，两条入口分别是实际受理的 src/worldmock/life/action_run.py:55 与工具层提前拒绝的 src/worldmock/life/tool_layer.py:243。

3. 核查1：它在真实数据集上真的会触发——dataset 的 103 个地点里只有 24 个（6 fitness + 12 park + 6 home）放行运动，其余 79 个（24 restaurant / 15 supermarket / 11 office / 11 convenience_store / 10 cafe / 5 parcel_station / 3 gas_station）一律拒绝。

4. 核查1：但拒绝理由从不进入 persona 提示——grep "does not admit this activity" 在 8 个 run 的全部 model_request.messages 中命中 0，只在 observation 私有证据里出现（2026-10-06 week run 440 条、2026-10-01 dayrun-r2 67 条）。

5. 核查1：候选层的第二处地点类判定在生产不可达——src/worldmock/life/candidates.py:336 在 require_location_instance 与 apply_location_kinds 同时为假时提前 return None，因此后面的 344-351 永不执行。

**代码证据**：63 处

**未找到**：

- grep -rn "PLACE_KIND_ENVIRONMENTS" --include="*.py" src custom tests scripts — 生产命中仅 3 个文件（exchange/types.py:101 定义、catalog/models.py:16+227、life/rules.py:9+57、life/candidates.py:15+72），测试命中 tests/exc
- grep -rn "apply_location_kinds" --include="*.py" src custom tests — 只有 candidates.py:124 的默认值 False 与 candidates.py:334、presentation.py:412 的文档字符串，无任何置真调用点。
- grep -rn "\.location_kinds(" --include="*.py" src custom — 0 命中，Catalog.location_kinds 无生产调用者。

---



## 四、现状总结

<a name="proposal-envdataset-今天的环境数据集"></a>
### 2.1 今天真正被当作「环境数据集」的东西

今天没有「八文件环境包」这一概念。环境数据集的现行形态是一个**目录**，由调用方显式传入，形状被钉死为两个并列子目录：

- 环境根只含 `catalog/` 与 `scenarios/`，引擎不搜索相邻仓库、不读语料、不比对规则副本（`docs/CONTRACTS.md:49`、`:57`；`README.md:87`；人工环境实例见 `tests/fixtures/environment/`）。
- `catalog/` 的封闭文件集合是 **清单 + 六个 JSON**：`REQUIRED_CATALOG_FILES = {apps.json, locations.json, goods.json, actions.json, contents.json, map.json}`（`src/worldmock/catalog/identity.py:18-20`），`manifest.json` 单独读取（`src/worldmock/catalog/identity.py:50`、`src/worldmock/catalog/loader.py:29`）。
- 文件集合 fail-closed：列外文件（含历史 `foods.json`、`work_topics.json`、`affordances.toml`、`rules/`）报 `ENVIRONMENT_STALE_FILE`，缺登记文件报 `ENVIRONMENT_FILE_UNLISTED`（`src/worldmock/catalog/admission.py:26`、`:28`、`:31`）。
- `manifest.json` 是强身份而非可选描述：`format_version: Literal[2]`、`environment_id`、`environment_version`、`rules_version`、`files[]`（逐文件 `path/sha256/size_bytes`）与 `catalog_content_sha256`（`src/worldmock/catalog/models.py:37-45`；人工环境实例 `tests/fixtures/environment/catalog/manifest.json`）。
- 清单的 `files` 数组必须等于按路径字典序的排列（`listed != sorted(listed)` 即拒，`src/worldmock/catalog/admission.py:32`），所以现行清单里 `paths` 的顺序是 `actions→apps→contents→goods→locations→map`，与心智模型的八步顺序无关。

**加载链路**（`src/worldmock/catalog/loader.py:26-39`）：`load_catalog(catalog_dir)` → 读 `manifest.json` → `validate_manifest` → 依次 `_read_model` 六个文件 → `validate_registration` → `_catalog_view` 冻结反向索引。这是**代码的读取次序**，不是数据集声明的提供次序；没有任何跨文件拓扑校验，也没有「后一个文件只能引用前一个已定义身份」的检查。

格式方向也是反的：六个文件都是**单个 JSON 对象**（顶层恰一个键，如 `AppsFile.apps`，`src/worldmock/catalog/models.py:141-144`），用 `json.loads` 解析并强制 LF 行尾（`src/worldmock/catalog/loader.py:53-66`），不是逐行 JSONL。

<a name="proposal-envdataset-八个文件名的现状"></a>
### 2.2 八个心智模型文件名在仓库中的现状

对 `location-type.json`、`app-catalog.jsonl`、`location.jsonl`、`merchant-catalog.jsonl`、`content-catalog.jsonl`、
`item_callback.py`、`persona.toml`、`config.toml` 逐一全仓检索，**跟踪文件命中为零**。其中 `config.toml` 需要一处更正：
仓库跟踪数为零，但工作树内存在 48 个 `config.toml`，全部落在被忽略目录（`runs/` 下，如 `runs/<run>/labelkit/config.toml`，
由 `.gitignore` 忽略），属下游产物而非本仓环境数据集。写成「仓库内不存在」会误导后续按名查找，应写「跟踪文件为零」。

八个概念今天以什么形态存在：

| 心智模型概念 | 今天的存在形态 | 证据 |
| --- | --- | --- |
| 地点类型 | 系统闭集枚举 `LocationKind` 十值，不是文件；每个地点内联必填 `kind` 引用它 | `src/worldmock/exchange/types.py:7-20`；`src/worldmock/catalog/models.py:57-65` |
| app catalog | `catalog/apps.json`，`App{app_id,name,purposes,payment_app_ids}` 无分类字段 | `src/worldmock/catalog/models.py:48-54`；`tests/fixtures/environment/catalog/apps.json` |
| 地点 | `catalog/locations.json`，`Location{location_id,name,branch_label?,kind,app_ids,shipping_seconds}` | `src/worldmock/catalog/models.py:57-65` |
| 商品 catalog | `catalog/goods.json`，`Goods{goods_id,name,category,price_fen,store_ids}`，类别六值 | `src/worldmock/catalog/models.py:73-80`；`src/worldmock/exchange/types.py:22-30` |
| 内容 catalog | `catalog/contents.json`，`Content{...,action_id,duration_seconds,app_ids,series?}` | `src/worldmock/catalog/models.py:110-120` |
| 周期性回调 | 不存在通用机制；燃油一个特例硬编码在引擎 | `src/worldmock/life/fuel.py:33-62`；`src/worldmock/exchange/constants.py:10`、`:15-16` |
| 人设 persona.toml | 场景 JSON 的 `agents[].background_story` 字段（≥100 字符） | `src/worldmock/life/scenario.py:134`、`:175-176`；`docs/manual/personas.md:19` |
| LLM 与重试 config.toml | 代码常量 + 仓外凭据文件 `~/.config/worldmock/deepseek.env` | `src/worldmock/model_endpoint.py:10-15`；`src/worldmock/run_setup.py:76-86`、`:108-151` |

<a name="proposal-envdataset-系统侧现状"></a>
### 2.3 系统侧：动作、app 与 target 的今天

- **动作类型不是四个而是一组分层**：人物动词闭集是 `LIFE_VERBS = (travel, interact, obtain, sleep)`（`src/worldmock/life/vocabulary.py:8`）；
  方式闭集七值 `WAYS`＝步行/驾车/睡觉/等待/购买/进行/领取（`src/worldmock/life/vocabulary.py:9-13`）；
  请求级标准动作六值 `START_ACTIVITY_ACTIONS = {travel_to,sleep,wait,purchase,do,collect_parcel}`（`src/worldmock/exchange/types.py:112-114`）；
  活动种类 `ActivityKind` 四值 `wait/sleep/travel/action`（`src/worldmock/life/models.py:150-156`，购买与领取是零模拟秒时点事实，不建活动）。
  **没有名为「使用」的动作类型**；ACS 阶段 2 已删除方式「使用」与动词 `consume`。
- **app 不是二分而是三层**：无 `purposes` 的 app 走动作绑定（内容 app，如夹具中的 bilibili/douyin/keep）；
  `AppPurpose` 三用途 `navigation/shopping/payment`（`src/worldmock/exchange/types.py:51-56`）的 app 由系统在动作中自动点名；
  `APP_UNBOUND` 要求 app 至少有用途或被某个动作绑定（`src/worldmock/catalog/admission.py:180-181`）。
- **导航与支付由人物点名，系统从不代选**。导航 app 可省略，写入的必须具导航用途（`src/worldmock/life/routing.py:134`）；
  支付 app 在远程下单时必填，缺失报 `PAYMENT_APP_REQUIRED`（`src/worldmock/life/routing.py:175`；`src/worldmock/life/basket.py:162`、`:164`），
  冻结合同原文「环境不代选 app、对象或支付 app」（`docs/CONTRACTS.md:119`；ACS P-12、P-13、P-14）。
- **target 类闭集恰七个且不含 app**：`TARGET_KINDS = (location, product, action, person, contact, self, activity)`
  （`src/worldmock/life/prompt_schemas.py:32`），内部绑定类 `BINDING_KINDS` 把 `supply` 对 LLM 呈现为 `product`（`src/worldmock/life/tool_layer.py:47-49`）。
  已经冻结的裁决明确写出「工具层目标类七个，app 不再是目标，动作行带 `app`」（`docs/dev/SPEC-action-and-content-simplification.md:1281`），
  阶段 3 删除表也把目标类 `app` 列为删除项（同文件 `:983`）。app 只作为动作行上的一个键参与解析与校验（`src/worldmock/life/routing.py:188-196`）。
- **平台 app 浏览商品已经能表达，但不是「manual」**：商品行的 `app_ids` 由店铺绑定派生（`src/worldmock/life/discovery.py:39`、`:53`），
  可售性要求所选 app 在 `catalog.apps_by_store[store]` 内（`src/worldmock/life/purchase_offers.py:55-58`），
  查询侧以 `app` 字段过滤（`src/worldmock/life/world_query.py:202-215`），纯导航/纯支付 app 作为筛选返回 0 件商品（`tests/integration/life/test_world_query.py:141-146`）。
  目录里没有任何名为 `manual` 的关联声明字段；`src` 中命中的 `manual` 是「瞬时手动动作」的内部标志（`src/worldmock/life/admission.py:67`、`:77`、`:106`、`:220`）。

<a name="proposal-envdataset-记录层现状"></a>
### 2.4 记录、导出与治理的现状

- 事件类型是封闭字面量集合，14 个成员（`src/worldmock/footprints/models.py:120-125`）；唤醒原因是第二套封闭集合，23 项（同文件 `:40-49`）。
- 导出可复现由显式 cutoff 保证：`export_run(store, cutoff, destination)`（`src/worldmock/footprints/export.py:33-35`）与 `read_events(cutoff)`（`src/worldmock/footprints/store.py:265`）。
- 世界状态只有一个写入者与一个提交入口：`FootprintStore.commit`（`src/worldmock/footprints/store.py:95`），版本不连续即拒（同文件 `:114-122`）。
- 随机性走带键 sha256 派生流，不推进世界随机状态（`src/worldmock/life/randomness.py:7-11`）。
- 世界结构号 `WORLD_SCHEMA_VERSION = 11`，定义在 `src/worldmock/life/models.py:35`（引用点 `:598`）。
- 数据仓被明文禁止提供脚本与回调、禁止定义类别与效果（`docs/dev/SPEC-action-and-content-simplification.md:320`、`:214`），架构侧同禁回调注册表（`docs/ARCHITECTURE.md:270`）。
- 目录格式 2、场景结构 8 是目前唯一被接受的格式；规则版本 `RULES_VERSION = 2`（`src/worldmock/exchange/constants.py:9`）。

---

<a name="proposal-envdataset-逐维度-gap-表"></a>
## 五、逐维度 Gap 表

判定用词只有五种：**已实现**（现状与条款语义一致）、**部分实现**（同一维度内一半成立一半不成立，逐子条款给判定）、
**未实现**（该能力完全不存在）、**与现有设计冲突**（现状存在相反的设计决定或明文禁令）、**以不同形态实现**（语义等价但载体、粒度或命名不同）。
两个维度判定不一致的同一事实，本章统一口径并在对应处写明更正。

<a name="proposal-envdataset-d1"></a>
### D1 环境数据集的载体、文件清单与加载链路

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 「环境数据集按顺序提供以下信息」（八文件作为一包的载体、边界与顺序） | 与现有设计冲突 | `src/worldmock/catalog/identity.py:18-20`；`src/worldmock/catalog/loader.py:26-39`；`src/worldmock/catalog/admission.py:26`、`:28`、`:31`、`:32`；`docs/CONTRACTS.md:49`、`:57` | 载体是「清单 + 六个 JSON」七个文件，加并列的 `scenarios/`；清单登记的是集合等价性而非提供顺序，`files` 必须按路径字典序；加载次序硬编码在代码里；环境根不接受第七个目录文件 |
| 八个文件名与格式（`location-type.json` / `app-catalog.jsonl` / `location.jsonl` / `merchant-catalog.jsonl` / `content-catalog.jsonl` / `item_callback.py` / `persona.toml` / `config.toml`） | 与现有设计冲突 | 全仓跟踪文件对八个名字零命中；实际为 `apps.json`/`locations.json`/`goods.json`/`contents.json`（`src/worldmock/catalog/loader.py:32-37`）；全部单根 JSON 对象（`src/worldmock/catalog/models.py:141-144`、`:147`、`:150-153`）；`config.toml` 的 48 个工作树实例均在忽略目录内 | 名称与格式两个方向都不同：既是别的名字，也是 JSON 单对象而非 JSONL；地点类型是枚举不是文件；人设是场景字段；模型接入在仓外凭据加代码常量 |
| 「环境数据集」与「系统侧」的职责边界如何切分 | 以不同形态实现 | `docs/CONTRACTS.md:52-59`；`docs/dev/SPEC-environment-data-ownership.md:24-38`；`src/worldmock/life/vocabulary.py:8`、`:14`；`src/worldmock/exchange/types.py:51-56` | 边界存在且有唯一权威文档，方向与心智模型一致（数据集供对象与绑定、引擎供规则与数值、以 `rules_version` 2 钉住），但切分内容差别很大：没有「使用」动作类型，app 不是二分而是「无用途（内容 app）＋ 三用途」，周期性状态没有注册入口 |

**本节更正（对抗性验证结论）**：`README.md` 引作「catalog/ 由清单和六个 JSON 组成」的出处应为 `README.md:87`
（`README.md:73` 实为 `python scripts/bootstrap.py --profile release`）；`docs/CONTRACTS.md:180` 写「键集合恰好等于八个内置
`LocationKind`」，而 `LocationKind` 有十个成员、`src/worldmock/life/scenario.py:377` 强制十类全覆盖——权威文档自身存在「八 vs 十」漂移，
按旧文改文档会删掉两个地点类型。

**需要新增或改造的接口/数据结构**：把它当作一次**显式的格式版本升级**而非改名，否则「按顺序」无从表达。
需要的形状是一个清单层声明：

```jsonc
// manifest.json（现 format_version 2）
{
  "format_version": 3,            // 升号：文件集合与命名变化
  "environment_id": "...",
  "environment_version": "...",
  "rules_version": 2,
  "files": [ /* path, sha256, size_bytes */ ],
  "catalog_content_sha256": "...",
  "provision_order": [            // 新增：显式提供顺序（若「按顺序」是硬需求）
    "location-type.json", "app-catalog.jsonl", "location.jsonl",
    "merchant-catalog.jsonl", "content-catalog.jsonl", "item-callback.jsonl"
  ]
}
```

`REQUIRED_CATALOG_FILES` 与 `load_catalog` 的读取序列随之改为按 `provision_order` 驱动，并新增跨文件拓扑校验
（后一个文件只能引用前一个已定义的身份）；`compute_catalog_identity` 的分帧摘要输入集合与 `environment_identity` 五字段随之改变。

<a name="proposal-envdataset-d2"></a>
### D2 地点类型与地点

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 「地点类型，其中 persona 的住宿地点类型必须包含，命名为 location-type.json」 | 与现有设计冲突（且「住宿类型必须包含」部分**未实现**） | 文件零命中；类型为闭集 `src/worldmock/exchange/types.py:7-20`；`PLACE_KIND_ENVIRONMENTS` 派生表 `:101-106`；无任何准入校验要求「存在 `kind==home` 的地点」——`grep -rn "LocationKind.HOME" src tests` 实跑**仅命中 `src/worldmock/exchange/types.py:102` 一条**（`types.py:10` 是 `HOME = "home"`，不含该字符串）；`src/worldmock/life/scenario.py:138`、`:367`；`src/worldmock/catalog/admission.py:223` | 「地点类型」是引擎闭集，数据集不能增类型（新增须改引擎源码并升 `RULES_VERSION`）；「住宿」只有**引用**强制（`home_location_id` 必填且须落在场景已选地点内），没有**类型**强制：一个 `kind=restaurant` 的地点作为住处可以通过加载 |
| 「地点名称必须绑定地点类型」 | 已实现 | `src/worldmock/catalog/models.py:57-65`（`kind: LocationKind` 无默认值）；`src/worldmock/life/load_checks.py:42-50` | 每个地点内联必填 `kind`，登记期校验 `kind ∈ LocationKind` |
| 「可选绑定 app」 | 已实现（粒度更窄） | `src/worldmock/catalog/models.py:63`；`src/worldmock/catalog/admission.py:76-84`；`STORE_KINDS` `src/worldmock/exchange/types.py:87-90` | `app_ids` 必填但可为空；只有五类店铺可绑非空 `app_ids`，驿站与非店铺地点必须为空 |
| 「绑定 app 的地点可以支持 persona 远程下单购买地点类型下包含的商品」（原文作「闪频」，作者已澄清为笔误，见 §1.1） | 部分实现（判定粒度是「店铺 + app」而非「地点类型 + app」） | `src/worldmock/life/purchase_offers.py:44-65`；`src/worldmock/life/discovery.py:57-71`、`:79-88`；`src/worldmock/life/basket.py:183-201`、`:210-225` | 远程下单的判定粒度是「**店铺 + app**」而非「地点类型 + app」：无绑定的分店只能到店、有绑定的分店可配送，效果与心智模型一致但推理起点是地点而非类型；「该类型持有的商品」不成立——商品绑店铺，类型级菜单 `menus_by_kind` 取「同类型首个地点」且在生产代码中无消费者 |
| 「命名为 location.jsonl」 | 与现有设计冲突 | `src/worldmock/catalog/loader.py:33`；`src/worldmock/catalog/models.py:147` | 实际为 `locations.json`，单根 JSON 对象 |

**本节更正**：该维度的判定不再用「部分实现」概括整条——「文件不存在 + 类型归引擎闭集 + 住宿类型无强制」应判
「与现有设计冲突」，否则 `HOME_LOCATION_KIND_REQUIRED` 这类新增失败路径会被误读成补齐小缺口。

**需要新增或改造的接口/数据结构**：若采纳「住宿类型必须包含」，最小形状是场景/目录准入的新增拒绝码，而不是新类型文件：

```python
# catalog/admission.py：validate_scenario_against_catalog 内
# (a) 已登记地点中至少一个 is_home
# (b) 每个人物的 home_location_id 所指地点 kind 必须是 HOME
raise RegistrationError("HOME_LOCATION_KIND_REQUIRED")
```

若确要把类型做成数据集文件，`LocationKind` 必须从闭集降级为「系统内置 + 环境扩展」两层，并同步
`PLACE_KIND_ENVIRONMENTS`、`STORE_KINDS`、`GOODS_CATEGORY_STORE_KINDS` 三张派生表、`src/worldmock/life/readable.py:23` 的第二张
中文标签表 `PLACE_TYPES`（由 `src/worldmock/life/environment_layer.py:63` 强制）与场景菜单的十类全覆盖不变量（`src/worldmock/life/scenario.py:377`）。

<a name="proposal-envdataset-d3"></a>
### D3 app 模型（内容 app / 功能 app 与动作绑定）

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 「app catalog…app 分为内容 app 和功能 app 两种分类，功能 app 需要绑定到某个具体的动作」 | 部分实现 | 文件零命中；`App` 仅四字段无分类字段（`src/worldmock/catalog/models.py:48-54`）；分类的实际载体是 `AppPurpose` 三值（`src/worldmock/exchange/types.py:51-56`）；绑定载体是 `Action.app_ids` 与 `Action.manner`，登记期强制同有同无（`src/worldmock/catalog/admission.py:118-120`） | 「app catalog」存在（名字与格式不同）；「内容/功能」二分在数据合同里完全不存在，替代物是用途标签与动作绑定；「功能 app 绑动作」这一半成立，但绑的是数据集自命名的 `action_id`，不是系统四类动作，且导航/支付用途的 app 可以完全不绑动作（`docs/CONTRACTS.md:683`） |
| 「内容 app 是"使用"这个动作下 persona 可见的 app」 | 与现有设计冲突 | 无「使用」动作：`src/worldmock/exchange/types.py:112-114`；`src/worldmock/life/vocabulary.py:9-13`；`src/worldmock/life/models.py:150-156` | 「使用」在动词闭集、方式闭集与标准动作闭集里都不存在，ACS 阶段 2 已删除方式「使用」与动词 `consume` |
| 「功能 app 是系统在 persona 做出某个动作时自动触发的」（导航、支付两例，「系统自动随机选择支付宝/微信」） | 与现有设计冲突 | 冻结合同「环境不代选 app、对象或支付 app」`docs/CONTRACTS.md:119`；`src/worldmock/life/routing.py:134`、`:175`；`src/worldmock/life/basket.py:162`、`:164`、`:168`；ACS P-12/P-13/P-14 `docs/dev/SPEC-action-and-content-simplification.md:80-83` | 导航 app 由人物点名且可省略（不写就是 `null`）；支付 app 在远程下单时**必填**，缺失直接业务拒绝；全仓不存在任何随机选支付 app 的受理路径；唯一同向的残存物是记录侧把导航记为 `offline_assisted` 触点，但前提仍是人物点名 |
| 「使用只能以内容 app 为 target」 | 与现有设计冲突（**app 目标类已被冻结退役**） | `src/worldmock/life/tool_layer.py:47-49`；`src/worldmock/life/prompt_schemas.py:32`；`docs/dev/SPEC-action-and-content-simplification.md:983`、`:1281`；`tests/integration/life/test_tool_layer.py:34` | app 不是任何 target 类；目标类闭集七个不含 app，且这是**显式删除**的裁决（阶段 3 删目标类 `app`，阶段 4 加入 `action` 并写明「app 不再是目标，动作行带 `app`」），不是换了个形态 |
| 「类似于美团外卖这样的平台 app…需要把这种关联方式写成 manual」 | 部分实现 | `src/worldmock/life/world_query.py:202-215`；`src/worldmock/life/discovery.py:39`、`:53`；`src/worldmock/life/purchase_offers.py:55-58`；`tests/integration/life/test_world_query.py:108-113`、`:141-146`；`src/worldmock/life/admission.py:67`、`:77`、`:106`、`:220` | 可见结果已成立（以 `app=美团` 查询返回的就是绑定美团的店铺在售商品，纯导航/纯支付 app 不误返回商品），但实现路径是「店铺 `app_ids` + 供给派生」，不是心智模型要求的 `manual` 显式命名关联；数据合同里没有任何 `manual` 形状的字段 |

**本节更正**：「使用只能以内容 app 为 target」的判定由「以不同形态实现」改为「与现有设计冲突」——既然 app 目标类是**显式退役**的，
本条的提案（新增 app 目标）必须被读作推翻冻结裁决，而不是恢复旧能力。

**需要新增或改造的接口/数据结构**：三个可选形状，按破坏面从小到大：

```python
# 形状 A（推荐）：不新增字段，把「功能 app」定义为派生概念，只补文档与准入校验
# 内容 app := purposes 为空且被至少一个动作绑定；功能 app := purposes 非空
# admission 增：功能 app 必须声明其用途，内容 app 不得声明用途（互斥检查）
# 形状 B：App 行加显式分类列（改 apps.json 字节，连带 manifest 全部 sha256 与 catalog_content_sha256）
class App(DeepFrozenModel):
    app_id: NonEmptyString
    name: NonEmptyString
    role: Literal["content", "function"]      # 新增
    purposes: tuple[AppPurpose, ...]
    payment_app_ids: tuple[NonEmptyString, ...]
# 形状 C：给功能 app 加显式「绑定动作」列（推翻用途隐式推导）
    bound_action_ids: tuple[NonEmptyString, ...]   # 新增
```

<a name="proposal-envdataset-d4"></a>
### D4 商品 catalog、购买与配送链路

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 「系统固定提供四个分类[零食，饮料，食品，商品]，商品必须与某个分类绑定」 | 与现有设计冲突 | `src/worldmock/exchange/types.py:22-30`（六值 `meal/snack/drink/leisure/daily/fuel`）；标签 `:101-105`；`src/worldmock/catalog/models.py:78`（`category` 无默认值） | 类别是**六个**不是四个；「零食/饮料/食品」可对上 `snack/drink/meal`，「商品」只能对上 `leisure + daily` 两个，`fuel` 在四分类中无位置 |
| 「零食、食品解决饥饿需求，饮料、商品解决心里需求，系统采用每个分类固定数值的方式维护」 | 部分实现 | `src/worldmock/exchange/constants.py:11-13`（`GOODS_ENERGY_KCAL`）与 `:15-17`（`GOODS_RECEIPT_EFFECTS`）；`src/worldmock/life/receipt.py:82-87`、`:89-96` | 「每分类固定数值」完全成立且是纯常量（数据集不可配）；方向部分不符：`meal/snack` 加热量符合「解决饥饿」，`leisure` 减 `entertainment_need`、`daily` 施 `mood_valence` 符合「解决心理需求」，但 **`drink` 走 `GOODS_ENERGY_KCAL` 加热量**，即现状是「饮料解决饥饿」；`fuel` 既非饥饿也非心理，它改 `driving_days` |
| 「商品至少绑定一个地点类型/app」（作者澄清：**两侧取其一，每侧可多个**，见 §1.1） | 部分实现（app 侧无任何绑定字段，因此 XOR 的 app 侧无法表达） | `src/worldmock/catalog/models.py:73-80`（`store_ids` 绑店铺实例或 `"all"`，**无 app 字段**）；`src/worldmock/catalog/admission.py:87-101`；`src/worldmock/exchange/types.py:91-100` | 绑定单位是**地点实例**（`location_id`）或 `"all"`，不是地点类型；类别可供给表只限定「哪类店铺可卖该类别」，不构成 goods→type 绑定；商品无 app 绑定字段，app 只能经店铺 `app_ids` 间接得到 |
| 「如果绑定地点类型中所有地点没有对应绑定的 app，persona 需要到地点后才能购买」 | 已实现（粒度不同） | `src/worldmock/life/purchase_offers.py:44-55`；`src/worldmock/life/basket.py:124-138`；`src/worldmock/life/discovery.py:57-71` | 无 app 的店铺只列到店；到店要求 `app` 为 `null` 且本人站在该店 |
| 「如果绑定地点类型有地点对应绑定 app，persona 购买该商品时根据 persona 当前的地理位置系统自动判定是到店购买还是外卖配送」（作者澄清：由系统自动判定，非人物挑选，见 §1.1） | 与现有设计冲突 | `src/worldmock/life/named_purchase.py:35-41`、`:78-110`；`src/worldmock/life/basket.py:141-152`；ACS P-12 (`docs/dev/SPEC-action-and-content-simplification.md:80`)、P-16（同文件 `:84`）；`docs/dev/SPEC-as2-integration.md:437` | 履约方式由**人物显式写的 `app`** 决定，不由系统按地理位置判定；请求已删除 `from` 与 `prefer`，渠道由 `app` 表达 |
| 「如果绑定 app，则 persona 的购买行为视为网购，即自动视为运送时长为 2-5 天（由系统随机，数据集不配置），默认使用驿站领取流程」（作者澄清：app 绑定 = 纯快递链路，与地点侧远程下单互斥，见 §1.1） | 与现有设计冲突 | `src/worldmock/catalog/models.py:65`（`shipping_seconds`）；`src/worldmock/life/basket.py:183-190`、`:192-202`（`:197` `if duration > 86400`）；`src/worldmock/exchange/constants.py:30-32`；ACS P-11 (`docs/dev/SPEC-action-and-content-simplification.md:79`)、P-10（同文件 `:78`）；`docs/dev/SPEC-map-and-storefront.md:65`；`tests/integration/life/test_acs_system_core.py:152` | 运输时长由**店铺声明** `shipping_seconds`，否则用图上距离与固定速度算；驿站只在时长 > 86400 秒时触发；配送链条里没有任何随机抽样（`random_stream` 在 `basket.py`/`parcel.py`/`receipt.py`/`commerce.py`/`operators.py` 零命中）；固定 500 分配送费与同一金额比较 |

**本节需引用的既有文档漂移**：`docs/CONTRACTS.md:693` 写「类别为 `meal`、`snack`、`drink`、`leisure`、`daily`」（漏 `fuel`），
`:1668` 写 `goods.json` 为「五个固定类别之一」——与 `src/worldmock/exchange/types.py:22-30` 的六类不符。
若照这两处修文档，会删掉 `fuel` 这条既存能力。

**需要新增或改造的接口/数据结构**：

```python
# 商品绑定地点类型（补齐「至少绑定一个地点类型/app」的类型一半）
class Goods(DeepFrozenModel):
    goods_id: NonEmptyString
    name: NonEmptyString
    category: GoodsCategory
    price_fen: PositiveInteger
    store_ids: tuple[NonEmptyString, ...] | Literal["all"]      # 保留
    store_kinds: tuple[LocationKind, ...] | Literal["all"] = () # 新增：按类型绑定，登记期展开为实例级
```

`admission._check_goods` 按 `GOODS_CATEGORY_STORE_KINDS[category]` 校验 `store_kinds` 并展开成实例级 `goods_by_store`；
若同时采纳「四分类」，`GoodsCategory` 需减为四值并把 `fuel` 归并去向下定（见 Q4），`RULES_VERSION` 必须升号。

> **本节已被 C4b 取代**：Q5 定「只保留 `store_kinds`，删除 `store_ids` 与 `"all"`」，
> 且 Q23 定 `kind` 为自由文本，故 `store_kinds` 的类型是 `tuple[NonEmptyString, ...]`，
> 校验也不再依赖引擎固定表 `GOODS_CATEGORY_STORE_KINDS`（该表已废）。**以 C4b 的代码块为准。**

<a name="proposal-envdataset-d5"></a>
### D5 内容 catalog、消费方式与 30 分钟切分

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 「内容 catalog…命名为 content-catalog.jsonl」 | 与现有设计冲突 | `src/worldmock/catalog/identity.py:18-19`；`src/worldmock/catalog/loader.py:36`；`src/worldmock/catalog/models.py:150-153` | 实际为 `catalog/contents.json`，单根 JSON 对象 `{contents:[...]}`，不是 JSONL，名字也不同 |
| 「内容至少绑定一个 app」 | 与现有设计冲突 | `src/worldmock/catalog/models.py:119`（`app_ids` 无下界）；`src/worldmock/catalog/admission.py:132-135`（`CONTENT_APP_BINDING_INVALID`）；`src/worldmock/catalog/admission.py:158-164`（`ACTION_CONTENT_EMPTY`）；夹具 `tests/fixtures/environment/catalog/contents.json` 中 `c-chat`/`c-rest`/`topic-sales` 的 `app_ids` 为空 | 登记层强制「内容 app 集合与动作 app 集合**同空同非空**且为子集」；动作不绑 app 时（`small-talk`/`office-work`/`quiet-rest`）其内容 `app_ids` 必须为空数组，否则直接拒绝。该条不是未实现，而是与现行绑定模型**互斥** |
| 「按照当前系统设计的消费方式推送给 persona 进行消费」 | 部分实现 | `src/worldmock/life/content_select.py:32`、`:49`；`src/worldmock/life/app_push.py:31`、`:56-64`；`src/worldmock/scheduling/compile.py:94`；`src/worldmock/life/prompt_schemas.py:32`；`src/worldmock/life/tool_layer.py:360-381` | 「系统推送、人物不点名内容」这半是真实实现：整点推送只在 `PUSH_HOURS_LOCAL = range(8,22)` 内、只对 `entertainment + phone_continuous` 动作发，人物可见目标闭集不含 `content`；缺的是「消费方式由数据集声明」这半：目录里没有消费方式字段，`AppUseManner` 三值由**动作**承载，推送窗口与频率是引擎常数 |
| 「内容需要声明消费时长」 | 已实现 | `src/worldmock/catalog/models.py:118`；`src/worldmock/exchange/constants.py:38`（`CONTENT_MIN_SECONDS = 60`）；`src/worldmock/life/action_run.py:149-190` | `duration_seconds` 必填严格整数且 ≥ 60；动作被禁止携带任何时长字段；一次动作的 `planned_end_at` 恒为该内容剩余时长的结束时刻 |
| 「如果总时长超过 30mins，需要以 30mins 为切分点提供每 30mins 消费的内容片段，用于系统做进度和记忆管理」 | 未实现 | 全仓对 `segment`/`chunk`/`fragment`/`片段`/`切片`/`1800` 在内容与会话模块零命中（`1800` 只命中决策节律 `progress_report_interval_seconds` 与供给可见期，见 `src/worldmock/life/scenario.py:304`、`src/worldmock/life/rhythm.py:101`、`src/worldmock/life/projection.py:8`）；夹具 `c-long` 2400 秒、`topic-sales` 32400 秒均无片段结构 | 长内容被当作一整条不可分对象：进度是连续秒数游标 `ContentProgress{content_id, position_seconds, completed_rounds, last_consumed_at}`（`src/worldmock/life/models.py:237-243`），记忆层按 session 区间派生并整体截断（`src/worldmock/life/memory_layer.py:45`、`:370`） |

**本节更正**：同一事实（文件不存在、格式为 JSON 对象）在本维度与 D1 必须取同一判定。本文统一判
**与现有设计冲突**（不是「以不同形态实现」），否则改名的取舍无法从两份报告推出同一答案。

**需要新增或改造的接口/数据结构**：**已裁决为引擎侧派生**（Q9/D10），**目录零改动**。

```python
# 已定方案（Q9）：引擎侧派生 1800 秒里程碑，数据集不加字段
#   - 边界来源：progress_report_interval_seconds（缺省 1800，已存在）
#   - 进度来源：活动带 content_id，状态按 content_id 记 ContentProgress
#   - 派生公式必须读 ContentProgress.position_seconds
#     （不是「当前时刻 − 活动开始时刻」——后者把暂停算成进度，违反 ACS P-2）
#   - 记忆层按 ACS:806 已定义的长内容句子规则处理
```

> 原稿的两个方案（甲：数据集提供内容片段；乙：引擎派生）**已由 Q9 定案为乙**。
> 方案甲（新增 `ContentSegment` 与目录字段）**已废弃**——它要给所有已发布环境升格式并重制，
> 而收益仅是命名一致。详见 C6。

<a name="proposal-envdataset-d6"></a>
### D6 动作空间与 target 类型约束

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 「出行，睡眠，购买，使用，四个动作类型」 | 与现有设计冲突 | `src/worldmock/life/vocabulary.py:8`、`:9-13`；`src/worldmock/exchange/types.py:112-114`；`src/worldmock/life/models.py:150-156`；`src/worldmock/agents/life_prompt.py:47`、`:49`；`src/worldmock/life/prompt_schemas.py:69-71` | LLM 侧确实只看到四个动词（`travel/interact/obtain/sleep`），但集合不同：出行→`travel`、睡眠→`sleep` 一一对应；**「使用」不存在**（ACS 阶段 2 已删方式「使用」与动词 `consume`，被 `interact`+方式「进行」映射到内部动作 `do` 取代）；「购买」不是动词而是方式（内部动词是 `obtain`，与「领取」共用）；心智模型没有的「等待」（引擎时间原语，受保护）与「领取」是系统补充的两类。层次也不同：请求级标准动作六个，活动种类四个（不含 `purchase`/`collect_parcel`，二者是零秒时点事实） |
| 「对 LLM 侧展现的是这四个类型下的具体动作」 | 部分实现 | `src/worldmock/life/tool_layer.py:322-325`；`src/worldmock/life/prompt_schemas.py:69-71` | 动作行按 `action`（动词）× `way`（方式）两字段公开，方式闭集七值被测试逐字钉住（`tests/integration/life/test_name_resolution.py:35`） |
| 「出行只能以地点为 target」 | 已实现（更严） | `src/worldmock/life/travel.py:52-61`（`:58-59` `DESTINATION_NOT_FOUND`）；`src/worldmock/life/models.py:706-736`；`src/worldmock/life/observe.py:281-290`、`:386-390`；`src/worldmock/life/name_resolution.py:199-204` | 三处钉死为地点：请求模型只收 `destination_id`、名称解析只对 `location` 目标发方式词、受理期再复核。比条款更严：目标必须是**场景已选地点**，登记但未选中的地点不能作为出行目标 |
| 「购买只能以商品作为 target」 | 已实现 | `src/worldmock/life/name_resolution.py:18-22`（`"购买": frozenset({"supply"})`）；`src/worldmock/life/projection.py:310-315`；`src/worldmock/life/tool_layer.py:48`（`supply → product`） | 购买类目标对 LLM 呈现为 `product`，对象是 `goods_id` |
| 「使用只能以内容 app 为 target」 | 与现有设计冲突 | 同 D3 该行；`docs/dev/SPEC-action-and-content-simplification.md:983`、`:1281`；`tests/integration/life/test_activity_menu.py:24` | app 目标类已被冻结退役，且被测试固化为不变量（断言 `{"content","game","app"}` 与绑定行的 `kind` 集合无交集） |
| 「内容和功能两个 app 类型」 | 与现有设计冲突 | `src/worldmock/exchange/types.py:51-56`；`src/worldmock/catalog/models.py:48-54`、`:113-121` | 不是二分而是「无用途（走动作绑定）+ 三用途」；内容 app 概念由 `Content.app_ids` 与 `Action.app_ids` 表达，不是 app 自身属性 |

**本节结论**：本维度是**方案 A / 方案 B 的二选一**，不能各自局部实施。
方案 A（贴条款）：新增动词 `use` 与方式「使用」、把内部动作 `use_app` 加回 `LIFE_VERBS`/`WAYS`/`START_ACTIVITY_ACTIONS`，
并新增公开目标类 `app`（`TARGET_KINDS` 增第八项、`_WAY_KINDS` 增 `"使用" → frozenset({"app"})`、投影层为每个内容 app 生成 `kind="app"` 行）。
方案 B（贴现状）：不动代码，把条款四类型改写为「出行/睡眠/购买/进行」并写明等待与领取是系统补充。
方案 A 会反转两条已冻结退役裁决并使 `tests/integration/life/test_activity_menu.py:24` 失败，同时改 `prompt_sha256` 与 `tool_schema_sha256`。

**需要新增或改造的接口/数据结构（仅方案 A）**：

```python
# life/vocabulary.py
LIFE_VERBS = ("travel", "interact", "obtain", "sleep", "use")        # 新增 use
WAYS = { ..., "使用": ("use", "use_app") }                            # 新增方式；WAY_LABELS 随之变为八项
# exchange/types.py
START_ACTIVITY_ACTIONS = frozenset({..., "use_app"})                  # 新增标准动作
# life/prompt_schemas.py
TARGET_KINDS = ("location", "product", "action", "person", "contact", "self", "activity", "app")
# life/name_resolution.py
_WAY_KINDS = { ..., "使用": frozenset({"app"}) }
```

<a name="proposal-envdataset-d7"></a>
### D7 persona 状态维护与周期性商品/内容回调

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 「支持环境集针对周期性消费商品/内容…注册额外的 persona 状态（仍然不引入库存系统）」 | 与现有设计冲突 | `src/worldmock/life/models.py:333-374`（`AgentState` 闭合字段集）；`src/worldmock/base_models.py:13-16`（`extra="forbid"`）；明文禁令 `docs/dev/SPEC-action-and-content-simplification.md:320`、`:214` 与 `docs/ARCHITECTURE.md:270`；`docs/dev/PROPOSAL_2026-10-07_APP_ACTION_RESOURCE_AND_RULE_SIMPLIFICATION.md:105` | 没有注册入口，任何未知字段即拒；数据集注册回调被两条规格原文明文禁止（数据仓永远不能设置「操作脚本或回调」；架构侧不保存回调注册表）。「不引入库存系统」这一半成立：全仓 `inventory` 零命中，「库存」9 处均为否定式注释 |
| 「回调可以订阅系统提供的某个动作，在动作发生时变化状态」 | 未实现（只有一个硬编码特例） | `src/worldmock/life/time_advance.py:330-333`；`src/worldmock/life/fuel.py:47-62` | 只有燃油一个特例：`ActivityKind.TRAVEL` 且 `travel_mode == "driving"` 时扣一天；不是订阅机制，动作到状态的绑定不可配置 |
| 「当状态到某个阈值时推送提醒 persona 再次消费商品/内容」 | 未实现 | `src/worldmock/life/app_push.py:56-68`（`:62` 过滤 `ENTERTAINMENT` + `PHONE_CONTINUOUS`）；`src/worldmock/life/reminders.py:46-49`、`:52-60`；`src/worldmock/life/scenario.py:304`；`src/worldmock/life/projection.py:1-8`；`src/worldmock/life/felt.py:286-294` | 推送窗口基础设施已存在（`PUSH_HOURS_LOCAL = range(8,22)`，`src/worldmock/exchange/constants.py:36`，按整点预排 `ScheduledKind.APP_PUSH`），但载荷固定为娱乐手机动作内容；提醒只服务当日目标与个人事务；`driving_days` 从未进入人物提示或投影，人物无从得知油量 |
| 「如果 persona 没有购买这些商品也不影响 persona 的正常动作（如果 persona 确实没有加油，乘车出行不会被禁止）」 | 与现有设计冲突（现状恰好相反） | `src/worldmock/life/fuel.py:42-43`、`:57-58`；受理点 `src/worldmock/life/tool_layer.py:170-174`、`src/worldmock/life/session_actions.py:539-540`；测试 `tests/integration/life/test_acs_contract_guards.py:129`、`tests/integration/life/test_acs_system_core.py:454`；变异锚点 `tests/acs_acceptance.py:1211` | 余额不足时驾车出行被**直接拒绝**（`DRIVING_DAYS_EXHAUSTED`），与条款「不会被禁止」正面相反 |
| 「persona 加油之后燃油可使用天数回到 7 天」 | 部分实现（语义是加不是重置） | `src/worldmock/life/receipt.py:50-53`（`driving_days + provided`）；`src/worldmock/exchange/constants.py:10`、`:15-16` | 是**加法**回补 7 天，没有上限字段，连续两次加油得到 14 天；与「回到 7」的重置语义不同 |
| 「每天使用了车子后，燃油可支撑天数 - 1」 | 已实现 | `src/worldmock/life/fuel.py:21-30`、`:47-62`；`tests/integration/life/test_acs_system_core.py:459-478` | 按本地日期扣减、同日多次不重扣、恰好停在午夜不扣次日，与「按日语义」一致 |

**本节结论**：这是**契约级方向冲突**，不是实现缺口。

**已裁决（Q3 取方案 b）**：采纳**声明式规则表**，数据不携带可执行代码。
`SPEC-ACS:320` 与 `:31` 两条冻结禁令相应被推翻（K5 已由作者裁定，见 C15），
故本项从「未裁决前不得动代码」转为**可实施**。形态见 §1.2.15 与 C7：
规则 = `(触发器, 效果)`，触发器四类 / 效果三类；文件名统一为 `callbacks.jsonl`。

<a name="proposal-envdataset-d8"></a>
### D8 persona 画像文件与 LLM/重试配置

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 「人设信息，就是我们当前的人物画像文件，命名为 persona.toml」 | 以不同形态实现 | `src/worldmock/life/scenario.py:134`（`background_story`）、`:175-176`（≥100 字符）；`src/worldmock/profile/compile.py:25`、`:64-80`；`docs/manual/personas.md:19`（明文「场景没有 `persona_file` 或画像文件引用字段」）；`docs/dev/SPEC-action-and-content-simplification.md:325` | 人物信息的唯一运行时来源是场景 JSON 的 `agents[].background_story`；另有供人工起草的独立 UTF-8 文本与编译产物 `PersonaCard`；仓库内不存在 `persona.toml`（唯一近名是包内规则资源 `src/worldmock/data/persona-mapping.toml`，与人物档案无关） |
| 「LLM 接入信息以及一些重试配置…命名为 config.toml」 | 以不同形态实现 | `src/worldmock/model_endpoint.py:12`、`:13`；`src/worldmock/run_setup.py:76-86`、`:108-151`、`:400-450`；`src/worldmock/runtime.py:329-335`；`docs/CONTRACTS.md:1537-1550`、`:1564-1576`；`docs/manual/runtime.md:145-147` | 接入信息分三处：代码常量（strict 端点、`deepseek-flash`）、仓外凭据 `~/.config/worldmock/deepseek.env`（只允许 `DEEPSEEK_NODE_KEYS`）、冻结进运行清单的阶段身份；重试预算全是源码/依赖常量（`max_retries=0`、`transport_max_retries=3`、`max_react_turns=16`、画像抽取 2、就寝反思 3、回复守卫 2）；没有任何数据集侧可配置面 |
| 「要求模型支持 256k+ 上下文」 | 已实现（数值满足，但无校验） | `src/worldmock/model_endpoint.py:15`（`DEEPSEEK_CONTEXT_LENGTH = 1_000_000`）；`src/worldmock/life/prompt_capacity.py:52-57`；`src/worldmock/life/prompt_capacity.py:27`（`CONTEXT_THRESHOLD = 0.85`） | 代码常量声明 1M 上下文，数值远超 256k；但没有任何代码在启动时**校验**模型声明的上下文下限，也没有从权威元数据核对（只对别名做相等断言，`src/worldmock/life/prompt_capacity.py:54-57`），而 `docs/dev/SPEC-agent-interaction.md:591` 要求「不能假定选用模型一定有 256k 窗口」 |

**需要新增或改造的接口/数据结构**（两选一，Q6 与 Q7 裁定后实施）：

```toml
# 若裁决为「数据集提供配置文件」，最小非密形状（凭据永不进入该文件）
# environment/config.toml
[model]
provider = "deepseek"
requested_model = "deepseek-flash"
context_window = 262144          # 下限声明，加载期校验 ≥ 262144
max_output_tokens = 8000
[retries]
max_retries = 0
transport_max_retries = 3
max_react_turns = 16
```

<a name="proposal-envdataset-d9"></a>
### D9 记录、导出与契约治理的连带影响

| 心智模型条款 | 判定 | 证据（file:line） | 现状实际行为 |
| --- | --- | --- | --- |
| 对「事件类型是封闭集合」的连带影响 | 与现有设计冲突（冲突点在机制形态，不在事件类型） | `src/worldmock/footprints/models.py:120-125`（14 个成员）；`:40-49`（23 项唤醒原因）；`:86-102`（闭集外写入即 `ValueError`）；`docs/dev/SPEC-action-and-content-simplification.md:777`、`:904`；`docs/ARCHITECTURE.md:270` | 新增需求（周期性状态、阈值提醒）**可以**复用既有成员（提醒走 `environment_event` 的 `app_push` 载荷与预排事件），但「回调可以订阅某个动作」这句话要求一个订阅/回调注册表，而架构与数据仓两条口径都把它禁掉，所以冲突不在事件类型上 |
| 对「导出可复现（cutoff）」的连带影响 | 部分实现（可做到，但有前提） | `src/worldmock/footprints/export.py:33-35`；`src/worldmock/footprints/store.py:265`；`src/worldmock/footprints/models.py:197-208`（规范 JSON）；`src/worldmock/life/randomness.py:7-11`；`src/worldmock/footprints/models.py:197-208`；`docs/dev/SPEC-action-and-content-simplification.md:781` | 只要回调产生的状态变化仍由引擎侧代码按冻结随机流求值，逐字节可复现性可保持（`random_stream` 已证明「随机时长」可做到不推进世界随机状态）。一旦改成环境提供 `item_callback.py` 执行，就出现一个不进 `catalog_content_sha256`、不进 `environment_identity` 五字段的行为来源，恢复与重放的身份核对拦不住它——这是实质破坏 |
| 对「只由环境变更世界状态」的连带影响 | 部分实现（语义成立，作者归属被挪动） | `src/worldmock/footprints/store.py:95`、`:114-122`；`docs/design/00-overview.md:145`、`:220`；`docs/dev/SPEC-action-and-content-simplification.md:214`、`:320` | 回调若由 `LifeEnv` 侧执行在语义上仍属「环境」，但把 `item_callback.py` 放进环境数据集清单，等于把**状态转移规则的作者**从引擎挪到数据仓，与「环境没有任何字段能进入本节的表、常数或算子」直接冲突 |
| 对「世界结构版本」的连带影响 | 未实现（需升号） | `src/worldmock/life/models.py:35`（`WORLD_SCHEMA_VERSION = 11`）、`:598`；`docs/CONTRACTS.md:64`；`docs/dev/SPEC-action-and-content-simplification.md:781` | 新增周期性状态若进 `AgentState`，必须升世界结构号；旧检查点与旧导出按版本拒绝（`docs/design/05-recording-engine.md:286` 一带） |
| 「契约归属：新接口该由谁承接」 | 部分实现（规则齐，但两处都宣称唯一） | `docs/CONTRACTS.md:19`；`docs/design/05-recording-engine.md:7`；`docs/dev/SPEC-action-and-content-simplification.md:214`；`docs/CONTRACTS.md:1660-1685`；`docs/dev/ANALYSIS-spec-coverage.md:264` 起 | 归属规则可直接吸收新接口，但 `SPEC-ACS` 自称「闭集、字段、常数与登记错误码的唯一定义处」，而 `CONTRACTS.md` 同时持有「环境目录文件」章并逐字给出六行；七个新文件名一旦落地，第一件事就是决定文件表归谁，不能两处都改 |
| 「三仓边界是否被越过」 | 与现有设计冲突（三处越界） | 归属表 `docs/dev/SPEC-action-and-content-simplification.md:840-844`；`docs/CONTRACTS.md:54-59`、`:1685`；`src/worldmock/catalog/identity.py:18-20`；`src/worldmock/catalog/admission.py:29-31`；`docs/CONTRACTS.md:1537-1550`、`:1564-1576` | ① `config.toml` 把模型身份与重试预算交给数据集，而这两项是 WorldMock 侧冻结的运行身份，凭据必须在仓外；② `item_callback.py` 把状态转移规则的作者挪到数据仓，且若塞进环境根会被 `rglob` 以 `ENVIRONMENT_STALE_FILE` 直接拒收（加载器从不 import 环境代码）；③ `merchant-catalog.jsonl` 的「四个分类 + 固定数值维护状态」把类别集合与状态效果交由数据侧表述，与 `GoodsCategory` 六值与 `exchange/constants.py` 的归属冲突。LabelKit-DataSet 一侧**未被越界**（条款全程未要求 WorldMock 做手机信号投影） |

**本节更正**：`docs/dev/SPEC-action-and-content-simplification.md:1650` 是 `app_push` 环境事件载荷行，不含「事件类型集合不变…23 个」
这句话；该句在 `:777` 与 `:904`。`EventType` 的定义跨度是 `120-125`（末行 `]` 落在 `:125`）。证据引用不得把 `.work/` 与 `runs/`
下被忽略的产物当作仓库事实。

---

<a name="proposal-envdataset-修改方案"></a>
## 六、修改方案

变更项按**依赖顺序**编号。每项写清目标、涉及文件/模块、接口或数据结构的 before/after、对既有不变量的影响与验证方式。
所有「改变目录字节」的变更项都以「新增格式版本、不原地改写历史」为默认立场：`docs/dev/SPEC-environment-data-ownership.md`
的「历史证据不可改写」是冻结条款，已存运行、导出与冻结输入保持原字节。

<a name="proposal-envdataset-c1"></a>
### C1 文档层先收口「环境数据集包」的真实形态（零代码）

- **目标**：把承载物的权威定义从散落的三处收敛为一节显式合同，写明包 = 清单 + 六个目录 JSON + `scenarios/`，
  并显式声明「提供顺序」在本设计中不成立（清单按路径字典序登记，加载按固定读取序）。
- **涉及**：`docs/CONTRACTS.md`（环境数据归属合同、环境目录文件两节）、`docs/EXPERIMENT.md:38-46`、`docs/INDEX.md` 环境数据归属节。
- **前后**：纯文档，无接口变化。
- **不变量影响**：不动任何运行时不变量。风险是新增自述性条款会改变 `docs/dev/ANALYSIS-spec-coverage.md`
  「条款无缺口」的计数基线，需要新增测试条目或豁免登记。
- **验证**：`tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve` 通过；
  映射表计数前后一致。
- **依赖**：无。

<a name="proposal-envdataset-c2"></a>
### C2 补「住宿地点类型必须包含」的准入规则

- **目标**：把 D2 的两种读法落成 fail-closed 校验（词表层面 home 是 `LocationKind` 成员已成立，本条补实例层面）。
- **涉及**：`src/worldmock/catalog/admission.py`（新增 `_check_home_kind`）、`src/worldmock/catalog/errors.py`（新增拒绝码）。
- **before/after**：

```python
# before：validate_scenario_against_catalog 只校验 home_location_id 落在场景已选地点内
# after：
def _check_home_kind(data: CatalogData, document: LifeScenario) -> None:
    """@brief 已登记地点至少有一个住宿地点，且每个人物的住处必须是其中之一。"""
    homes = {place.location_id for place in data.locations if place.is_home}
    if not homes:
        raise RegistrationError("HOME_LOCATION_REQUIRED")
    for agent in document.agents:
        if agent.home_location_id not in homes:
            raise RegistrationError("HOME_LOCATION_REQUIRED")
```

- **不变量影响**：新增失败路径，会让「有住处引用但无 home 类型地点」的环境从可加载改为拒绝。不改变
  「只有环境可变更世界状态」，不改变事件类型闭集，不影响导出（导出只记环境身份与版本）。既有历史 runs 不重跑、不重写。
- **验证**：新增反例测试（无 home 地点、住处指向 restaurant）；现存夹具 `tests/fixtures/environment/catalog/locations.json`
  已满足（`location_id=home`、`kind=home`），但大量用手工 `SimpleNamespace`/`model_copy` 构造 catalog 的用例需逐个补 home 地点
  （例如 `tests/unit/agent_protocol/test_interaction_menu.py:83`、`tests/integration/life/test_environment_layer.py:91`）；同时按 `docs/VERIFICATION.md`
  的台账闭合要求在 `docs/dev/ANALYSIS-spec-coverage.md` 补一行条款到测试的映射。
- **依赖**：C1。

<a name="proposal-envdataset-c3"></a>
### C3 裁决商品四分类与现有六分类的映射，并决定 `drink` 的归宿

- **目标**：裁决心智模型的 `[零食, 饮料, 食品, 商品]` 与代码 `[meal, snack, drink, leisure, daily, fuel]` 的映射，
  并决定 `drink` 的效果方向。
- **涉及**：`src/worldmock/exchange/types.py:22-30`、`:101-105`；`src/worldmock/exchange/constants.py:11-17`；
  `src/worldmock/life/receipt.py:78-96`；`src/worldmock/exchange/operators.py:25-35`；`docs/CONTRACTS.md:693`、`:1668`。
- **before/after（若采纳四分类且 `drink` 改走心理侧）**：

```python
# before
GOODS_ENERGY_KCAL = {MEAL: 600, SNACK: 200, DRINK: 100}
GOODS_RECEIPT_EFFECTS = {LEISURE: 0.10, DAILY: 0.05, FUEL: FUEL_DRIVING_DAYS}
# after（示例，数值须由裁决给出）
GOODS_ENERGY_KCAL = {MEAL: 600, SNACK: 200}                     # drink 移出热量表
GOODS_RECEIPT_EFFECTS = {DRINK: <娱乐缓解或情绪脉冲>, GOODS: <商品类固定效果>, FUEL: FUEL_DRIVING_DAYS}
# receipt._receipt_effect 补 drink 分派分支
```

- **不变量影响**：`GoodsCategory` 是目录行字段，改枚举等于改数据集契约：所有已发布目录的 `category` 取值、
  `GOODS_CATEGORY_STORE_KINDS`、`GOODS_CATEGORY_LABELS` 与人物卡片的 `food_kinds` 覆盖目标都要同批改。
  `RULES_VERSION`（现为 2）必须递增，否则 `catalog/admission.py:21` 以 `RULES_VERSION_MISMATCH` 拒绝全部已发布环境。
  改 `drink` 会移动身体结算与 `body_settlement` 的资源流，影响导出的分录对账与收支守恒断言。
  **同时修文档时必须修 `docs/CONTRACTS.md:693` 与 `:1668` 的「五类」，否则会删掉既存的 `fuel`。**
- **验证**：`tests/unit/catalog/test_admission.py` 与 `tests/integration/life/test_acs_system_core.py` 的按类别夹具；
  覆盖门禁 `scripts/coverage_gate.py`；导出行不变量由 `validate_export` 检查。
- **依赖**：C1。

<a name="proposal-envdataset-c4"></a>
### C4 商品按地点类型绑定（已按 Q5/Q23 定：唯一字段是 `store_kinds`）

- **目标**：让商品按**地点类型**声明供给，而不是逐店列举；`store_ids` 与 `"all"` 一并删除。
- **涉及**：`src/worldmock/catalog/models.py:73-80`；`src/worldmock/catalog/admission.py:87-101`；`src/worldmock/catalog/loader.py:81-91`。
- **before/after**：

```python
# before：逐店列举，或 "all" 让引擎按固定表代填
class Goods(DeepFrozenModel):
    store_ids: tuple[NonEmptyString, ...] | Literal["all"]

# after：只按类型声明（类型是数据集自由文本，Q23）
class Goods(DeepFrozenModel):
    store_kinds: tuple[NonEmptyString, ...]      # 如 ["便利店", "超市"]；可多可少，不得为空
# 展开（loader）：place.kind in goods.store_kinds 的加进该商品的有售地点集合
# 准入（admission）：每个 store_kind 必须在 locations 里至少有一条 kind 匹配，否则 GOODS_BINDING_DANGLING
```

- **已定要点**：
  1. **`store_ids` 与 `"all"` 删除**（Q5）——不存在按实例绑定的路径
  2. **`kind` 是自由文本**（Q23），故 `store_kinds` 的类型是 `tuple[NonEmptyString, ...]`，不是 `tuple[LocationKind, ...]`
  3. **`GOODS_CATEGORY_STORE_KINDS` 固定表废除**（Q23/C14）——「哪类地点能卖哪类」改由商品自声明
  4. **`menus_by_kind` 随类型表删除**（Q16）——它是 `"all"` 展开的副产物
- **不变量影响**：`store_ids` 是既有发布格式的**必填**字段，删除它等于改目录契约，`RULES_VERSION` 与 `format_version` 须升号；
  按 U13「不维护兼容性」，旧环境直接失效而非兼容读取。
- **验证**：`tests/unit/catalog/test_admission.py` 的 `GOODS_BINDING_DANGLING` 反例（绑了不存在的地点类型）；
  `tests/unit/catalog/test_loader.py` 的按 `store_kinds` 展开正反例；`menus_by_kind` 相关用例删除。
- **依赖**：C3、C14。

<a name="proposal-envdataset-c5"></a>
<a name="proposal-envdataset-c4b"></a>
### C4b 落成「商品绑定地点 XOR 绑定 app」的准入规则（作者澄清）

- **目标**：把 §1.1 澄清二的互斥约束变成 fail-closed 校验，并把两条分支的履约语义固定下来——
  地点侧能否远程下单由**该地点是否绑 app** 决定，app 侧恒为快递 2–5 天 + 驿站。
- **涉及**：`src/worldmock/catalog/models.py:73-80`（`Goods`）；`src/worldmock/catalog/admission.py:87-101`（`_check_goods`）；
  `src/worldmock/catalog/errors.py`（新拒绝码）；`src/worldmock/life/basket.py:183-202`（配送链路口）。
- **before/after**：

```python
# before：Goods 只有 store_ids（店铺实例或 "all"），无 app 字段，
#         因而 XOR 的 app 侧既不可表达，「两侧混用」也不可拒绝
# after：
class Goods(DeepFrozenModel):
    goods_id: NonEmptyString
    name: NonEmptyString
    category: GoodsCategory
    price_fen: PositiveInteger
    # 地点侧（Q5 定为唯一的地点到店/外卖路径；类型是自由文本，Q23）
    store_kinds: tuple[NonEmptyString, ...] = ()          # 至少一个，可多个
    # app 侧（个数不限）
    app_ids: tuple[NonEmptyString, ...] = ()
# admission._check_goods 增（互斥的是「侧」，不是个数）：
#   loc_side = bool(goods.store_kinds)
#   app_side = bool(goods.app_ids)
#   if not (loc_side or app_side):
#       raise RegistrationError("GOODS_BINDING_MISSING")        # 两侧皆空
#   if loc_side and app_side:
#       raise RegistrationError("GOODS_BINDING_SIDE_EXCLUSIVE") # 两侧混用
#   # 每个 app_id 必须在 app-catalog 中登记
#   # 每个 store_kind 必须在 locations 中至少有一条 kind 匹配，否则 GOODS_BINDING_DANGLING
# 履约分派（引擎侧纯函数，只由环境裁定）：
#   app_side             -> 快递链路：时长由带键随机流抽 2..5 天；优先最近家庭驿站领取
#   loc_side + 该地点绑 app -> 远程下单（闪送/外卖）
#   loc_side + 该地点无 app -> 必须到店
#   到店 vs 外卖（澄清三，离散读法）：persona 当前所处地点 ∈ 商品所绑地点 -> 到店，否则外卖
# 领取（Q24）：到站后由 persona 在驿站执行 interact，一次取走该站全部待取件
```

- **不变量影响**：新增失败路径（同时绑定即拒）；`RULES_VERSION` 须递增，否则已发布环境全部被 `RULES_VERSION_MISMATCH` 拒绝。
  改 `basket.py` 的配送分派会触及 `shipping_seconds` 的既有时长语义，故本条与 X5/X6 必修。
  随机时长必须走 `src/worldmock/life/randomness.py` 的带键流（§1.2.6 的 R10）；按 U13「不维护兼容性」，**新基线重新冻结**，不再要求旧基线字节不变。
  事件类型集合不新增（快递与驿站走既有事件），「只由环境变更世界状态」不受影响——到店/外卖的判定发生在环境内部。
  **前提已满足**：Q1c 已裁决为方案 (a)「把判定交回引擎」（作者 2026-10-09），故本项的履约派生按上表实施，不再是条件式。人物请求侧的 `app`/`pay_app` 字段随 Q21 删除。
- **验证**：新增三条反例（两侧混用、两侧皆空、`store_kind` 悬空）与三条正例（多地点到店、多地点外卖、多 app 快递）；
  `tests/integration/life/test_acs_system_core.py:152` 的运输时长用例按新语义重建；`docs/dev/ANALYSIS-spec-coverage.md` 补条款映射。
- **依赖**：C3、C4。

### C5 内容绑定改为「至少绑定一个容器」（已按 Q12/Q21 定为容器二选一）

- **目标**：把「内容至少绑定一个 app」放宽为**「至少绑定一个容器：app 或地点类型」**——
  这是 Q12/Q21 引入地点绑定内容（工作、跑步）的前提。
- **涉及**：`src/worldmock/catalog/models.py:119`；`src/worldmock/catalog/admission.py:126-141`。
- **before/after**：

```python
# before：bool(action.app_ids) != bool(content.app_ids) 即拒，靠同空同非空隐式放行
# after：两侧皆空才拒；任一侧非空即合法
class Content(DeepFrozenModel):
    app_ids: tuple[NonEmptyString, ...] = ()          # 可为空
    location_kinds: tuple[NonEmptyString, ...] = ()   # 新增：绑定地点类型（C13/C14 引入）
    # 准入：if not (content.app_ids or content.location_kinds): raise CONTENT_CONTAINER_MISSING
```

- **关键修正**：原稿写的 `app_ids: tuple[...] = Field(min_length=1)`（非空下界）**与 Q12/Q21 正面冲突**——
  「工作」「跑步」这类只绑地点的内容会在加载期即被拒，正好堵死 `interact` 的地点半支。
  评审 P1-8 指出该矛盾，本条据此改写。
- **不变量影响**：冲击已冻结的 `CONTENT_APP_BINDING_INVALID` 与 `ACTION_CONTENT_EMPTY`；
  夹具 `tests/fixtures/environment/catalog/contents.json` 的 `c-chat`/`c-rest`/`topic-sales`（app_ids 为空）
  在新规则下**合法**（它们绑地点或不绑容器，须逐个定），不再需要豁免码。
- **验证**：`tests/unit/catalog/test_loader.py`、`tests/integration/life/test_content_select.py`、
  `tests/integration/life/test_app_progress.py`；新拒绝码 `CONTENT_CONTAINER_MISSING` 进规格错误码表。
- **依赖**：C13（`interact` 接口）、C14（`kind` 自由文本）。

<a name="proposal-envdataset-c6"></a>
### C6 30 分钟切分：引擎侧派生，数据集零改动（已按 Q9 定）

- **目标**：按 Q9 的裁决落地——**走引擎侧派生**，不给数据集加切片字段。
- **涉及**：`src/worldmock/life/progress_layer.py`、`src/worldmock/life/memory_layer.py`、
  `src/worldmock/life/decision_coordinator.py:426`、`src/worldmock/life/rhythm.py`。
- **机制（已存在，不需新建）**：
  1. 边界来源：`progress_report_interval_seconds`（缺省 1800）已在活动进行中按该间隔安排决策边界
  2. 进度来源：活动带 `content_id`，状态按 `content_id` 记 `ContentProgress`
  3. **派生公式必须读 `ContentProgress.position_seconds`**，**不是**「当前时刻 − 活动开始时刻」——
     后者把暂停时间算成进度（评审 P1-16；ACS P-2 规定「暂停不加进度」，P-25 的 `position_seconds` 才是权威）
- **记忆层**：按 ACS:806 已定义的长内容句子规则处理，不新增机制。
- **不变量影响**：**零目录改动**（原稿的方案甲要升目录格式并重制全部环境，已废弃）；
  导出侧 `content_consumed` 行形状不变。
- **验证**：`tests/integration/life/test_progress_layer_projection.py`、`tests/integration/life/test_progress_events.py`；
  跨 1800 秒边界的进度记录与记忆层切句测试。
- **依赖**：无（不阻塞其他项）。

<a name="proposal-envdataset-c7"></a>
### C7 声明式回调规则表（已按 Q3/Q8/D9 定，K5 已解锁）

- **目标**：落地**一张声明式规则表 + 一个求值器**，无 per-app/per-action 函数，数据不携带可执行代码。
- **涉及**：`docs/dev/SPEC-action-and-content-simplification.md:320`、`:31`；`src/worldmock/life/models.py:333-374`；
  `src/worldmock/life/fuel.py`；`src/worldmock/life/app_push.py`；`src/worldmock/scheduling/`。
- **形态**（规则 = `(触发器, 效果)`，触发器四类、效果三类）：

```jsonc
// 数据文件（不是 .py）：callbacks.jsonl
{ "rule_id": "fuel-weekly",
  "trigger": { "class": "action", "key": "drive" },              // 订阅具体动作，不是动作类型（Q8）
  "effect":  { "class": "state_delta", "state": "driving_days", "delta": -1, "once_per": "local_day" },
  "notify":  { "when": { "state": "driving_days", "below": 3 }, "channel": "app_push" } }

{ "rule_id": "ride-hailing",
  "trigger": { "class": "action", "key": "take_taxi" },
  "effect":  { "class": "footprint_chain", "steps": [
      { "kind": "browse",  "app_pool": ["amap", "didi"], "pick": "random" },
      { "kind": "payment", "app_pool": ["alipay", "wechat"], "pick": "random" } ] },
  "notify":  { "visible": false } }
```

- **触发器四类**：`action`（具体动作，取值见 Q21 的七动作集）/ `time` / `state` / `event`。
- **效果三类**：`state_delta` / `footprint_chain` / `transfer`。
- **文件名统一为 `callbacks.jsonl`**（原稿混用四种写法：`item_callback.py`、`item-callback.jsonl`、
  `content-callback.jsonl`、`callbacks.jsonl`，本条统一）。
- **K5 已解锁**：`SPEC-ACS:31`「不引入规则语言、插件、逐 app 执行器；不新建状态池」被作者裁决推翻
  （2026-10-09：「这种没有写原因的约束都当作 agent 瞎写的」），故本项可实施；该条禁令须同步改写（见 C15 的 K5 行）。
- **不变量影响**：状态字段进 `AgentState` 要升 `WORLD_SCHEMA_VERSION`（现 11，`src/worldmock/life/models.py:35`），
  旧检查点与旧导出按版本拒绝；
  「同日不重扣」需要 `once_per: local_day` 字段承载（原稿的 schema 表达不出这个已实现的行为，评审 P1-12）；
  加油恢复须与 C3 的类别级收货效果**调和归属**（现状 `receipt.py:50-53` 是加法，Q19 已定为重置到上限）。
- **未决子项**：提醒载荷形态（`AppPush.content_id` 是 `min_length=1`，加油提醒没有内容，不能直接复用，
  须定义新载荷种类与会话位置）；睡眠中到点的处理（丢弃/醒来补发/次日再发）。
- **验证**：新增 SPEC 与映射；阈值触发推送投递测试；同日不重扣与跨午夜语义的等价断言。
- **依赖**：C1、C3（`drink`/`fuel` 的类别效果归属）、C8（不阻断）。

<a name="proposal-envdataset-c8"></a>
### C8 删除油量阻断（已按 Q11 定为方案 a）

- **目标**：**驾车永远可行**，油量只用于提醒——删除 `DRIVING_DAYS_EXHAUSTED` 这条拒绝。
- **涉及**：`src/worldmock/life/fuel.py:33-44`、`:47-62`；`src/worldmock/life/tool_layer.py:170-174`；
  `src/worldmock/life/session_actions.py:539-540`；`src/worldmock/life/receipt.py:50-53`（Q19 的重置语义）。
- **before/after**：

```python
# before：余额不足即抛
if needed > agent.driving_days:
    raise PreconditionError("DRIVING_DAYS_EXHAUSTED", ["driving_days"], public_reason="...")
# after（Q11 裁定）：不再抛；驾车照常受理。油量降到阈值时经 app_push 持续提醒
```

- **连带删除（Q19 引出）**：D13 生效后 `driving_days` **不再 gate 任何事**，
  故 `require_driving_days`（`fuel.py:33-43`）**失去调用方**，应一并删除。
  `charge_driving_days` 的扣减保留（它是状态变化的来源），但其内部的 `DRIVING_DAYS_EXHAUSTED` 抛出
  （`fuel.py:57-58`）也须删除或改为钳到零。
- **不变量影响**：`DRIVING_DAYS_EXHAUSTED` 是已登记拒绝码，退役须同步错误码表与其测试；
  变异锚点 `tests/system/mutations/test_acs_acceptance.py` 钉住 `require_driving_days` 的判定式，须同步（该函数若删除则锚点作废）；
  `docs/CONTRACTS.md:75`「只有环境明确声明拥有汽车的人物能够驾车」可保留（它讲 CAR_REQUIRED，不是油量）。
- **验证**：改写 `tests/integration/life/test_acs_contract_guards.py:129` 与 `tests/integration/life/test_acs_system_core.py:454`；
  更新变异锚点；覆盖门禁。
- **依赖**：C7（先定周期性状态语义）。

<a name="proposal-envdataset-c9"></a>
### C9 八文件包落地与目录格式升位（已按 D1/Q2 定）

- **目标**：按 D1 落地八文件包；按 Q2「顺序不保证」处理加载顺序。
- **文件集合**（八文件，其中 `location-type.jsonl` 已按 Q23/C14 **去掉**——类型不再是独立文件）：

```python
# before
REQUIRED_CATALOG_FILES = frozenset({"apps.json", "locations.json", "goods.json",
                                    "actions.json", "contents.json", "map.json"})
# after
REQUIRED_CATALOG_FILES = frozenset({"app-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl",
                                    "content-catalog.jsonl", "actions.jsonl", "callbacks.jsonl", "map.json"})
# 环境根另有：persona.toml、config.toml（不进 catalog 闭集，见 C10）
```

- **已定要点**：
  1. **不引入 `provision_order`**（Q2：顺序仅为叙述，不做跨文件拓扑校验）
  2. **`location-type.jsonl` 删除**（Q23：类型改为地点条目上的自由文本属性，不再独立成文件）
  3. **`callbacks.jsonl` 是数据文件**，不是 `item_callback.py`（C7 统一）
  4. **`persona.toml` / `config.toml` 在环境根**，不入 `catalog/`（否则撞 `ENVIRONMENT_STALE_FILE`）
- **涉及**：`src/worldmock/catalog/identity.py:18-20`；`src/worldmock/catalog/loader.py:26-39`；
  `src/worldmock/catalog/models.py`；`src/worldmock/catalog/admission.py:22-36`；
  `tests/fixtures/environment/catalog/*`；`docs/design/03-resource-engine.md`（环境目录节，原 `CONTRACTS.md:1660-1685`）。
- **不变量影响**：`format_version` 必须升号；`catalog_content_sha256`、`map_sha256` 与 `environment_identity` 五字段随之改变，
  所有既有 run 不能打开、恢复、重放（**U13：不维护兼容性**，符合预期）；`.json` 改 `.jsonl` 需要新解析路径与失败码。
- **验证**：人工环境全部文件重出并重钉哈希；`tests/system/environment/test_validation.py`、`tests/unit/catalog/*` 期望值同步；
  字节级 `--check` 重生成比较。
- **依赖**：C1、C3、C4、C5、C6、C13、C14。

<a name="proposal-envdataset-c10"></a>
### C10 `persona.toml` 替换人设字段；`config.toml` 放非密参数（已按 Q6/Q7 定）

- **目标**：按 Q6 用 `persona.toml` **替换**场景 `background_story`；按 Q7 让 `config.toml` 携带非密运行参数。
- **涉及**：`src/worldmock/life/scenario.py:121-145`；`src/worldmock/profile/compile.py:25`、`:64-80`；
  `src/worldmock/model_endpoint.py:12-15`；`src/worldmock/run_setup.py:108-151`；
  `docs/manual/personas.md:19`、`docs/manual/runtime.md:145-147`；`docs/design/08-configuration.md`。
- **before/after**：

```python
# persona.toml（环境根，替换场景 background_story）
class AgentConfig(DeepFrozenModel):
    background_story: str          # 删除
    persona_file: str              # 新增：指向 persona.toml，UTF-8，≥100 字符
# profile/compile._resolve_story 改为只读 persona.toml；两者并存时报错

# config.toml（环境根，只放非密参数）
#   seed          单一随机种子，覆盖 R1–R13 全部随机源（U8）
#   start_time / stop_time   模拟起止，精确到小时（U11）
#   retry 参数     降级重试预算（U12）
#   context_window 下限声明，加载期断言 ≥ 262144（256k）
# 凭据永不进入该文件，仍走仓外 ~/.config/worldmock/deepseek.env
```

- **已定要点**：
  1. **替换，不并存**（Q6）——不定义权威顺序，因为只有一个人设来源
  2. **模型身份不可被环境覆盖**——`provider`/`base_url`/`requested_model` 仍在运行清单冻结；
     `config.toml` 只带**运行参数**（种子、起止、重试），不带模型身份
  3. **256k 断言须与权威元数据核对**，不能只校验自报值（评审 P1-14；`docs/dev/SPEC-agent-interaction.md:591` 要求）
- **不变量影响**：改场景 schema 会使全部既有场景与冻结输入字节失效；
  `persona_coverage.json` 的输入指纹（`background_story_sha256`）须改为对 `persona.toml` 取指纹；
  新增根级文件与「环境根只有 `catalog/` 与 `scenarios/`」的旧表述冲突——按 N1/Q15 的方向，该表述本身要改。
- **验证**：场景结构升号的加载测试与旧字段拒绝测试；两份夹具（`tests/fixtures/environment/scenarios/*.json`）改写；
  `tests/unit/catalog/test_admission.py` 的文件集合反例改写。
- **依赖**：C1、C9。

<a name="proposal-envdataset-c11"></a>
### C11 需要显式处理的既有不变量（横切所有变更项）

| 不变量 | 现状锚点 | 本提案的处置 |
| --- | --- | --- |
| 事件类型封闭集（14 个） | `src/worldmock/footprints/models.py:120-125` | 所有新增需求**复用**既有成员：提醒走 `environment_event` 的 `app_push` 载荷与 `ScheduledKind.APP_PUSH`；不新增 `EventType`。若提醒要形成决策机会，须追加唤醒原因到 23 项闭集（`src/worldmock/footprints/models.py:40-49`），并同步决策节律表与互锁测试 |
| 只由环境变更世界状态 | `src/worldmock/footprints/store.py:95`、`:114-122`；`docs/design/00-overview.md:145` | C7 的所有形态都必须由 `LifeEnv`/引擎侧求值并走唯一 `commit` 入口；不得让数据集提供可执行脚本，否则状态转移规则的作者被挪出引擎 |
| 导出可复现（cutoff） | `src/worldmock/footprints/export.py:33-35`；`src/worldmock/footprints/store.py:265`；`src/worldmock/footprints/models.py:197-208` | 任何新随机量（例如 2-5 天运送时长）必须走 `random_stream`（`src/worldmock/life/randomness.py:7-11`），不用墙钟、不用内置 `hash()`、不推进世界随机状态；新增字段不得改变既有完整日的字节 |
| profile v2 拒绝 v1 模板字段 | `src/worldmock/profile/`（v1 九参数与四模板字段被显式报错而非别名）；ACS P-31 | C10 若改人设载体，必须走结构升号而非别名双读；不得为兼容旧文件放开 `extra=forbid` |
| 四条动作类型的 target 约束 | `src/worldmock/life/prompt_schemas.py:32`；`src/worldmock/life/name_resolution.py:18-22`；`src/worldmock/life/tool_layer.py:47-49`；`docs/dev/SPEC-action-and-content-simplification.md:983`、`:1281` | 出行→`location`、购买→`product` 保持不变（现状更严，见 D2/D6）；「使用→内容 app」需要 C6 层面的方案 A/B 裁决，且必须同时处理目标类 `app` 已被显式退役这一事实 |

<a name="proposal-envdataset-c12"></a>
### C12 收口文档与门禁

- **目标**：修掉分析中发现的既有漂移，并按治理要求补映射。
- **涉及**：`README.md:87`；`CLAUDE.md:67-76`（`scripts/` 下已不存在 `build_catalog.py`、`build_lifestyle_corpus.py`、
  `calibrate_traffic.py`、`migrate_scenarios.py`、`migrate_content_kinds.py`，也 `experiments/published-environment/` 在本仓不存在，
  实有 `bootstrap.py`、`coverage_gate.py`、`render_day.py`、`inspect_run.py` 等）；`docs/CONTRACTS.md:180`（八 vs 十地点类型）、
  `:693` 与 `:1668`（五 vs 六商品类别）；`docs/dev/ANALYSIS-spec-coverage.md`；`docs/VERIFICATION.md`。
- **不变量影响**：纯文档，零运行风险；但任何新增准则必须同时按 `docs/dev/ANALYSIS-spec-coverage.md` 的矩阵补 clause→test 映射，
  否则规格通过而门禁交不出证据。
- **验证**：`tests/test_documentation_gate.py` 全绿；映射计数闭合。
- **依赖**：无。

<a name="proposal-envdataset-c13"></a>
### C13 start_activity 接口重构为 (action, target)

- **目标**：简化接口，统一 app 绑定内容和地点绑定内容的消费入口，消除 way 实现细节泄露。
- **当前问题**：
  - `way` 是实现细节泄露："步行" vs "驾车" 只是同一动作（travel_to）的不同参数
  - `way = "进行"` 承载所有内容消费，无法区分 app 绑定内容和地点绑定内容
  - 工作、跑步这类地点限定活动既不是"使用 app"也不是"购买商品"，当前模型无法清晰表达
- **接口变更**：
  ```python
  # 当前
  class ActivityRequestItem:
      target: str      # 对象名
      way: str         # 步行/驾车/购买/进行/领取/睡觉/等待
      app: str | None
      to: str | None
      pay_app: str | None
  
  # 新设计
  class ActivityRequestItem:
      action: str      # walk/drive/take_taxi/take_bus/purchase/sleep/interact
      target: str      # 地点名/商品名/app名/地点名
  ```
- **四类动作**：
  - **出行类**：walk/drive/take_taxi/take_bus，target 是地点
  - **购买类**：purchase，target 是商品
  - **睡眠类**：sleep，target 是地点（home）
  - **活动类**：interact，target 是 **app 名或地点名**（双重语义）
- **interact 的双重语义**（详见 Q21）：
  - target 是 app：从 app 绑定的内容中按现有推荐逻辑随机选一个（看剧、游戏）
  - target 是地点：从地点绑定的内容中按同样逻辑随机选一个（工作、跑步）
  - persona 不能指定具体内容，只能指定容器（app 或地点）
- **内容模型调整**：
  ```python
  class Content:
      content_id: str
      name: str
      category: str
      duration_seconds: int
      app_ids: tuple[str, ...] | None          # 绑定 app（看剧、游戏）
      location_kinds: tuple[str, ...] | None   # 绑定地点类型（工作、跑步）
  ```
- **涉及模块**：
  - `src/worldmock/catalog/models.py`：Content 模型新增 `location_kinds` 字段
  - `src/worldmock/life/vocabulary.py`：删除 `WAYS` / `WAY_SYNONYMS`
  - `src/worldmock/life/name_resolution.py`：删除 `way_options()` / `pick_way()`，新增 `_ACTION_KINDS`
  - `src/worldmock/life/tool_layer.py`：ActivityRequestItem 字段变更，删除 way 处理逻辑
  - `src/worldmock/life/interaction_menu.py`：`binding_action()` 改为只接收 action
  - `src/worldmock/life/content_select.py`：ContentRequest 从 `(action_id, app_id)` 改为 `(target_id, target_kind)`
  - `src/worldmock/catalog/loader.py`：新增 `contents_by_app` / `contents_by_location_kind` 索引
  - `src/worldmock/life/projection.py`：构造 action 目录时包含地点绑定内容（只在当前地点匹配时）
- **破坏性影响**：
  - 所有包含 `start_activity` 调用的测试夹具需要改写（`{"target": "X", "way": "购买"}` → `{"action": "purchase", "target": "X"}`）
  - model_request 测试数据全部需要更新
  - 7 种 way 映射到 7 种 action（walk/drive/take_taxi/take_bus/purchase/sleep/interact）
  - 但语义更清晰：action 是「做什么类型的事」，target 是「对什么对象做」
- **数据集设计原则**（防止过度设计）：
  - 一个地点只绑定语义合理的内容：公司 → 只绑定「工作」，公园 → 绑定「跑步」「散步」
  - 不会把「唱K」和「工作」都绑到公司
  - 数据集设计者自己保证语义合理性
- **不变量影响**：
  - 四条动作类型的 target 约束（C11 表）需要更新：出行→location、购买→product 保持不变，「使用→内容 app」改为「活动→app 或地点」
  - 事件类型封闭集不变：interact 的事件记录复用既有 `do` 动作的事件类型
  - 导出可复现不变：内容选择继续走 `random_stream`
- **验证**：
  - `tests/integration/life/test_tool_layer.py`：ActivityRequestItem 解析测试
  - `tests/integration/life/test_name_resolution.py`：action 分派与 target 解析测试
  - `tests/integration/life/test_content_select.py`：按 app/location 过滤内容测试
  - `tests/unit/catalog/test_loader.py`：新索引构造测试
  - 所有 model_request 夹具改写并通过完整日回归
- **依赖**：Q21 裁决通过；C4（商品绑定地点类型）、C5（内容绑 app 下界）、C6（动作与 app 解绑）。

---


<a name="proposal-envdataset-c14"></a>
### C14 `Location.kind` 改为数据集自由文本，废除类型枚举与派生表

- **目标**：让地点类型成为数据集可定义的自由文本，使 Q5 的「商品绑地点类型」成立（作者选方案 b）。
- **涉及**：
  - `src/worldmock/exchange/types.py`：删 `LocationKind`、`STORE_KINDS`、`GOODS_CATEGORY_STORE_KINDS`、`PLACE_KIND_ENVIRONMENTS`
  - `src/worldmock/catalog/models.py`：`Location.kind` 类型改为 `NonEmptyString`；`Goods.store_kinds` 新增
  - `src/worldmock/catalog/admission.py`：删 `GOODS_STORE_KIND_INVALID` 的类型表校验，改校验 `store_kinds` 是否有对应地点
  - `src/worldmock/catalog/loader.py`：`_goods_indexes` 的展开条件改为按 `store_kinds` 字符串匹配
  - `src/worldmock/life/readable.py`：删 `PLACE_TYPES`
  - `src/worldmock/life/rules.py:57`：运动地点资格改为从显式集合或回调派生
  - `src/worldmock/life/projection.py:303`、`name_resolution.py:201`：`kind == "parcel_station"` 改为显式标志
- **before/after**：

```python
# before
class Location(DeepFrozenModel):
    kind: LocationKind          # 引擎闭集，10 个成员
    ...
class Goods(DeepFrozenModel):
    store_ids: tuple[NonEmptyString, ...] | Literal["all"]   # 实例或 "all"

# after
class Location(DeepFrozenModel):
    kind: NonEmptyString                      # 数据集自由文本，如「便利店」
    accepts_parcels: bool = False             # 显式标志，替代 kind == "parcel_station"
    ...
class Goods(DeepFrozenModel):
    store_kinds: tuple[NonEmptyString, ...]   # 地点类型标签，如 ["便利店", "超市"]
```

- **不变量影响**：
  - `catalog/` 六文件字节全变，`catalog_content_sha256` 重算，`RULES_VERSION` 与 `format_version` 必须升号
  - 旧环境在 `RULES_VERSION_MISMATCH` 处被拒（符合「不维护兼容性」，U13）
  - 不新增事件类型；不改变「只由环境变更世界状态」；导出格式不变（导出只记环境身份与版本）
- **验证**：
  - `tests/unit/catalog/test_admission.py`：`store_kinds` 悬空引用（`GOODS_BINDING_DANGLING`）反例
  - `tests/unit/catalog/test_loader.py`：按 `store_kinds` 展开的正反例；`menus_by_kind` 用例删除
  - `tests/integration/life/test_environment_layer.py`：场所中文名改由 `kind` 直接提供
  - `tests/unit/exchange/test_types.py:41`、`tests/test_single_source.py:47`：`PLACE_KIND_ENVIRONMENTS` 用例删除
  - `docs/CONTRACTS.md:180`、`:1235`，以及 SPEC-life-realism / SPEC-resource-and-rule-simplification /
    SPEC-life-runtime / SPEC-action-and-content-simplification 的多处双向引用同步
- **依赖**：C3（分类映射）、C4（商品绑类型）、C4b（XOR）。三者须合并实施。

---


<a name="proposal-envdataset-c15"></a>
### C15 冻结条款改写登记表

- **目标**：本提案的多项裁决会正面推翻**已冻结**的 ACS 条款，但原稿只在叙述里说「要改」，
  没有落成可核对的清单。本项把「改哪条、改成什么、连带哪个锚点」逐条登记，供实施时按表执行。
- **前提更正（2026-10-09 合并后核实）**：`docs/dev/SPEC-*.md` **全部 6 份都已是历史记录**——每份头部都有横幅「本文件保存提交 `72f004b0` 时的过程与判断，**不再定义当前系统**」。
  因此本表标题里的「冻结条款」措辞**已过时**：它们不是当前契约，而是历史档案。
  **当前权威在 `docs/design/*.md`**（`docs/INDEX.md`：每个接口、字段、规则和约束只在所属模块定义）。
  本表因此改为「**历史条款到当前模块设计的迁移登记**」——历史档案按横幅保持原样，  **真正要改的是 `docs/design/` 里的对应章节**。各行的「新归属」列已按此给出。
- **背景**（评审 P0-2）：ACS:320 逐字写着「数据仓永远不能设置：…**场所限制**、对象要求、前置条件…
  操作脚本或回调…**店铺类型的可供给类别**…」。Q23/C14 把「哪类地点能卖哪类商品」交给数据集
  （§1.2.17 甚至举例「零食可以只在加油站卖，燃油也可以在便利店卖」），C13 又让内容自带 `location_kinds`
  ——两条都落在 :320 明文禁止的项上，而提案全文对「场所限制」「可供给类别」**零命中**。
- **改写登记表**：

| # | 被改写的冻结条款 | 现状原文要点 | 本提案的改写 | 由哪个裁决驱动 | 连带锚点 |
| --- | --- | --- | --- | --- | --- |
| K1 | `SPEC-ACS:320`「场所限制」 | 数据仓不得设置场所限制 | 商品经 `store_kinds` 自声明可售地点类型；内容经 `location_kinds` 自声明可进行地点 | Q5 / Q13 / Q21 / Q23 | `spec-coverage` 条款表；`tests/acs_acceptance.py` |
| K2 | `SPEC-ACS:320`「店铺类型的可供给类别」 | `GOODS_CATEGORY_STORE_KINDS` 引擎固定 | 该表废除，改由商品自声明 | Q5 / Q23 | `types.py:91-100`；`CONTRACTS.md:1233` |
| K3 | `SPEC-ACS:320`「状态效果与缓解速率」 | 数据仓不得设置状态效果 | 回调规则的 `state_delta` 由数据集声明增量与阈值（**语义与求值仍归引擎**） | Q3 / D9 | §1.2.15；C7 |
| K4 | `SPEC-ACS:320`「操作脚本或回调」 | 数据仓不得携带回调 | 改为**声明式规则表**（数据文件，非可执行代码） | Q3 | §1.2.15；X2 |
| K5 | `SPEC-ACS:31`「不引入规则语言、插件或逐 app 执行器；不新建状态池」 | 禁止规则文法 | **已裁决推翻**：规则表正是「规则语言」，`state_delta` 正是「状态池」。作者裁决（2026-10-09）：「这种没有写原因的约束都当作 agent 瞎写的」 | Q3 / Q8 | `SPEC-ACS:31` 须同步改写 |
| K6 | `SPEC-ACS:983`、`:1281` app 目标类已退役 | app 不作目标类 | `interact` 以 app 或地点为 target（**不等于复活 app 目标类**：target 是容器名，不是新的目标类枚举） | Q12 / Q21 | C13 |
| K7 | `SPEC-ACS` P-10 / P-11 / P-12 / P-13 / P-14 / P-16 | 渠道与支付由人物点名；时长由店铺声明 | Q1c 把渠道判定交回引擎；`app`/`pay_app` 字段随 Q21 删除 | Q1c / Q21 | X3 / X4 / X5 / X6 |
| K8 | `SPEC-ACS` P-22 | `store_ids` 形态钉死，不为同一事实另造形状 | 改为 `store_kinds` | Q5 | C4 / C4b |
| K9 | `SPEC-ACS` P-25 的 `ContentProgress` 语义 | 暂停不加进度 | Q9 的公式文字须改为读 `position_seconds`（**公式写错，语义不变**，见 P1-16） | Q9 | C6 |
| K10 | `CONTRACTS.md:55`「包规则只从引擎读取；环境没有规则副本」 | 环境不带规则 | 地点类型与商品供给改由环境声明 | Q23 | C14 |
| K11 | `CONTRACTS.md:119`「环境不代选 app、对象或支付 app」 | 引擎不代选 | Q1c 让引擎派生渠道；D4 让引擎随机选支付 app | Q1c / D4 | X3 |
| K12 | `CONTRACTS.md:680`、`:1680` | 环境目录与文件集合 | 八文件包落地 | D1 | C9 |
| K13 | `CONTRACTS.md:75`「初始可用天数由环境声明」 | 无油拒绝驾车 | Q11 无油不阻断 | Q11 | C8 |

- **K5 已裁决**（2026-10-09）：作者明确「**这种没有写原因的约束都当作 agent 瞎写的**」，
  故承认该禁令被推翻。`SPEC-ACS:31` 须同步改写；**C7（回调实现）据此解锁**，不再阻塞。
- **不变量影响**：本项本身零代码，但它决定 C7/C13/C14 是否可实施。改写冻结 SPEC 须同步
  `docs/dev/ANALYSIS-spec-coverage.md` 的 clause→test 矩阵与 `docs/VERIFICATION.md` 的台账。
- **验证**：每张登记表行在实施后必须有对应的 SPEC 版本表行与测试；`docs/CONTRACTS.md` 的
  派生表（`:1235-1241` 另注：该表缺 `gas_station`/`parcel_station` 两行）须同步。
- **依赖**：无（登记项），但 C7/C13/C14 依赖本表被批准。


<a name="proposal-envdataset-c16"></a>
### C16 删除场景地点筛选，出行 target 改读全量目录

- **目标**：让出行 target 允许 catalog 中的任意地点，把「场景选地点」这一层整体删除（Q15）。
- **涉及**：
  - **5 处筛选点**改读 `catalog.locations`：`src/worldmock/life/travel.py:58`、`basket.py:95`、`parcel.py:39`、
    `discovery.py:45`、`purchase_offers.py:33`
  - **约 10 处名称查找**改读 catalog：`environment_layer.py:44`、`tool_layer.py:123`、`:238`、`result_projection.py:42`、
    `progress_layer.py:237`、`settled_text.py:124`、`:142`、`todo_layer.py:56`、`action_run.py:92`、
    `contact_exchange.py:41`、`day_plan_inputs.py:156`、`:217`、`presentation.py:126`
  - **3 处准入校验**改为直接对 catalog 校验：`src/worldmock/life/load_checks.py:86`（住处/工作地）、`:47`（kind 闭集，随 C14 一并删）、
    `:168`（display_name 唯一）
  - **场景结构**：删除 `LocationReference`（`src/worldmock/life/scenario.py:277-280`）与 `locations[]`（`:314`、`:347`），
    场景结构须升号
  - **装配点**：`src/worldmock/life/semantics.py` 的 `locations: tuple(catalog.locations[row.location_id] for row in document.locations)`
    改为直接取 `catalog.locations`
- **before/after**：

```python
# before：场景选入子集，运行时按 id 拼接
class LocationReference(DeepFrozenModel):
    """@brief 场景选入的登记地点身份。"""
    location_id: str = Field(min_length=1, pattern=r"\S")

class LifeScenario(DeepFrozenModel):
    locations: tuple[LocationReference, ...] = Field(min_length=1)   # 必填

# after：场景不再选地点，直接读目录全量
# LifeScenario 删除 locations 字段
# RuntimeScenario.locations = tuple(catalog.locations.values())
```

- **不变量影响**：
  - **prompt 变长**：persona 可达地点列表从「场景选中数」涨到「目录全量」（实测 `0006` 为 92 → 103）。
    `src/worldmock/life/observe.py:288` 是全量列出、不轮转的，故提示会实际增长
  - **场景不再兼任「世界分片」**：多场景共享一个目录时暴露同一份世界。与「同一目录配多个场景」的现有用法一致
  - **地图与场景彻底解耦**：地图本就是目录级产物（`src/worldmock/cli.py:153`）
  - `scenario_sha256` 取值变化（字段集合变），须随场景结构升号处理
  - 不新增事件类型；不改变「只由环境变更世界状态」
- **验证**：
  - `tests/unit/life/test_scenario.py`：`locations` 字段移除后的加载与拒绝测试
  - `tests/integration/life/test_travel_graph.py`、`test_travel_cost.py`：出行 target 改读全量后的可达性
  - `tests/integration/life/test_product_discovery.py`、`test_purchase_apps.py`：供给筛选改读全量
  - `tests/integration/life/test_environment_layer.py`：地点名称改读 catalog
  - 数据集 6 个场景的 `locations` 键需移除并重出
- **依赖**：C14（kind 自由文本，同一批目录契约变更）。


<a name="proposal-envdataset-c17"></a>
### C17 废弃 `actions.json`，`Content` 承接效果档案（已按 D22/D23 定）

- **目标**：删除 `Action` 层，把承重的 `category`/`manner` 移到 `Content` 上；删除 `communication_mode`。
- **涉及**：见 §1.2.18 的「涉及的文件」表（12 处 + `exchange/types.py` 的 `CommunicationMode`）。
- **before/after**：见 §1.2.18 的 `Content` 字段对照。
- **已定要点**：
  1. **`actions.json` 移出 `REQUIRED_CATALOG_FILES`**（文件集合见 C9）
  2. **`Content.action_id` 消失**——内容自己就是那个活动
  3. **`category`/`manner` 移到 `Content`**——它们仍承重（体力系数、运动门禁、推送资格）
  4. **`communication_mode` 删除**（D23）——社交路径已无
  5. **消费入口由绑定侧决定**：`app_ids` → 以 app 为 target；`location_kinds` → 以地点为 target
- **不变量影响**：目录 `format_version` 升号；`RULES_VERSION` 升号；`Content` 是导出与推送的共同输入，
  改字段会连带 `content_consumed` 行与推送载荷；按 U13 旧环境直接失效。
- **验证**：`tests/unit/catalog/test_loader.py`（内容索引）、`tests/unit/catalog/test_admission.py`（类别判定）、
  `tests/integration/life/test_app_push.py`（推送资格改读 content）、`tests/integration/life/test_action_run.py`（体力与门禁）、
  `tests/integration/life/test_content_catalog_titles.py`（标题索引）、全部 model_request 夹具。
- **依赖**：C13（`(action, target)` 接口）、C14（`kind` 自由文本）、C5（容器绑定）。

---

<a name="proposal-envdataset-冲突"></a>
## 七、与既有契约/不变量/项目边界的冲突

逐条列出这份心智模型与现有契约、架构边界、SPEC 条款相抵触的地方，并给出取舍建议。

| 编号 | 冲突点 | 现行权威原文与位置 | 取舍建议 |
| --- | --- | --- | --- |
| X1 | 环境数据集提供「模型接入信息与重试配置」 | 原文位置（合并前）：`docs/CONTRACTS.md:1537-1550` 把 `base_url`/`requested_model`/`max_retries`/`transport_max_retries`/`max_react_turns` 冻结进运行清单；`:1564-1576` 阶段身份继承且不能覆盖；凭据只在仓外 `~/.config/worldmock/deepseek.env` 且只允许一个键；`docs/manual/runtime.md:145-147` 明文「没有场景级 `model` 或 `base_url` 覆盖字段」 | **已裁决（Q7）：部分采纳**——`config.toml` 可携带**运行参数**（种子、起止时刻、重试预算），但**模型身份**（`provider`/`base_url`/`requested_model`）仍冻结在运行清单，环境不可覆盖；凭据永不入库；256k 落成加载期断言且须与权威元数据核对（C10） |
| X2 | 环境数据集提供 `item_callback.py`（可执行回调） | `docs/dev/SPEC-action-and-content-simplification.md:320`「数据仓永远不能设置：…操作脚本或回调…」；`:214`「环境没有任何字段能进入本节的表、常数或算子」；`docs/ARCHITECTURE.md:270`「不保存门面、服务字典或回调注册表」 | 形态上拒绝 `item_callback.py`，改为**声明式阈值规则**（数据只填订阅动作、增量、上下限、阈值、提醒键，语义与求值归引擎）。若坚持可执行回调，须成对改写上述两条禁令并新增 SPEC，等于推翻「WorldMock 固定状态效果」的前提——不建议 |
| X3 | 「System 自动随机选择支付宝/微信支付」 | 原文位置（合并前）：`docs/CONTRACTS.md:119`「环境不代选 app、对象或支付 app」；ACS P-12/P-13/P-14（`docs/dev/SPEC-action-and-content-simplification.md:80-83`）网购与支付 app 必须由人物点名，无默认值，最小 `app_id` 回退已删除 | **已裁决（D4）：采纳随机代选**——这是**推翻**现行禁令，不是「修正心智模型」。实施要点：随机源须走带键随机流（§1.2.6 的 R8/R9）；导出里 `payment_app_id` 的来源从请求字段变为环境选择，**反事实重放口径须重写**；ACS P-12/P-13/P-14 与 `CONTRACTS.md:119` 须成对改写（见 C15 的 K7/K11） |
| X4 | 「系统自动判定到店购买还是外卖配送」 | ACS P-16（`docs/dev/SPEC-action-and-content-simplification.md:84`）请求删除 `from` 与 `prefer`，渠道由 `app` 表达；`docs/dev/SPEC-as2-integration.md:437` 同 | **已裁决（Q1c 方案 a）：把判定交回引擎**。实施：重开 ACS P-16；引擎按「商品绑定侧 × persona 位置 × 该地点是否绑 app」派生渠道（§1.1 澄清三的离散读法）；`app`/`pay_app` 字段随 Q21 删除；改动 `named_purchase`/`basket`/`admission` 的受理路径。**这与现状相反**——现状由人物写的 `app` 表达渠道，引擎不做位置判定 |
| X5 | 「运送时长 2-5 天由系统随机，数据集不配置」 | ACS P-11（`docs/dev/SPEC-action-and-content-simplification.md:79`）店铺明确声明运输时长；P-10（`:78`）>1 天走最近家庭驿站到站、通知、本人领取；`docs/dev/SPEC-map-and-storefront.md:65` 固定 500 分与同额比较 | **已裁决（作者）：随机，数据集不配置**——`Location.shipping_seconds` 的数据集权威降级。实施：`app` 侧商品时长由带键随机流抽 2–5 天（§1.2.6 的 R10）；ACS P-11 须改写（见 C15 的 K7）；**既有完整日基线按 U13 不兼容**，新基线重新冻结（不再要求旧字节不变） |
| X6 | 「默认使用驿站领取流程」（默认 = 全部 app 绑定订单？） | ACS P-10 与 `src/worldmock/life/basket.py:197` 只在 `duration > 86400` 时改走驿站 | §1.1 澄清二已定其适用范围：**仅商品绑定 app 的快递链路**走驿站。故阈值应从「按 `shipping_seconds > 86400`」改为「按绑定侧分派」（见 C4b），而不是无条件驿站；地点侧（到店与远程下单）不经驿站 |
| X7 | 「饮料解决人物的心里需求」 | `src/worldmock/exchange/constants.py:12` `DRINK: 100` 走 `GOODS_ENERGY_KCAL` → `apply_food` 增加 `energy_kcal`（`src/worldmock/life/receipt.py:82-87`） | **已裁决（D6/Q4/Q10）：改结算方向**——`DRINK` 从 `GOODS_ENERGY_KCAL` 移出，改走心理侧（`entertainment_need` 递减或情绪脉冲）。连带改身体轨迹与导出分录；与 C3 合并实施 |
| X8 | 固定四分类 vs 代码六分类 | `src/worldmock/exchange/types.py:22-30` 六值；`docs/CONTRACTS.md:693` 与 `:1668` 又写五值（自身漂移） | 先修文档漂移（补 `fuel`），再裁决四分类映射；照文档改会删掉 `fuel` 与加油站供给能力 |
| X9 | 地点类型作为数据集文件 | `src/worldmock/exchange/types.py:7-20` 闭集；`docs/CONTRACTS.md:55`「包规则只从引擎读取；环境没有规则副本」；`:180` 与 `src/worldmock/life/scenario.py:377` 的十类菜单全覆盖 | **已裁决（Q23）：地点类型改为数据集自由文本**，`LocationKind` 枚举与三张派生表（`STORE_KINDS`/`GOODS_CATEGORY_STORE_KINDS`/`PLACE_KIND_ENVIRONMENTS`）废除（见 C14/C15 的 K1/K2/K10）。`kind` **字段本身保留**，但降级为条目上的自由文本属性；§1.2.16 的取证结论一说明它仍是六件事的载体 |
| X10 | persona.toml 与场景 `background_story` 并存 | 原文位置（合并前）：`docs/CONTRACTS.md:1750`；`docs/manual/personas.md:19`（明文无 `persona_file`）；`docs/dev/SPEC-environment-data-ownership.md:28` 单作者原则 | **已裁决（Q6）：替换，不并存**——`persona.toml` 成为唯一人设来源，场景 `background_story` 删除。走场景结构升号；单作者原则因此成立（只有一个来源） |
| X11 | 世界结构升号 | `src/worldmock/life/models.py:35`（`WORLD_SCHEMA_VERSION = 11`）；`docs/CONTRACTS.md:64` | 任何新增 persona 状态都必须升号，旧检查点与旧导出按版本拒绝；不要用可选字段绕过 |
| X12 | 三仓边界（WorldMock / WorldMock-Dataset / LabelKit-DataSet） | 归属表 `docs/dev/SPEC-action-and-content-simplification.md:840-844`；`docs/CONTRACTS.md:54-59` | 不越过：模型身份与重试、状态效果与类别含义、事件与导出格式仍归 WorldMock；世界素材与绑定归 Dataset；手机信号投影归 LabelKit-DataSet（本条款未越界这一侧） |
| X13 | 门禁与治理 | `docs/dev/ANALYSIS-spec-coverage.md` 的 clause→test 矩阵；`scripts/coverage_gate.py` 的 85% 分支门限与公共接口零缺口。（`docs/VERIFICATION.md` **已随 2026-10-09 合并不存在**，其职责改为各 `docs/dev/<主题>/…-verification.md`） | 每条变更落地必须同时补映射与测试节点，否则门禁失败而不是放行 |
| X14 | 商品绑定「地点侧」与「app 侧」互斥（作者澄清） | 现行 `Goods` 无 app 字段（`src/worldmock/catalog/models.py:73-80`），XOR 任一侧都无法表达，更无法拒绝 | 采纳为新增准入校验（C4b）：两侧取其一、每侧可多个；这是**增补**而非冲突，但会改变 `Goods` 的字段集合与 `RULES_VERSION`，须与 C3/C4 合并实施 |

---

<a name="proposal-envdataset-开放问题"></a>
## 八、需要人裁决的开放问题

按「阻塞程度」排序：**P0 = 不裁决就无法动任何代码**；**P1 = 阻塞某一维度的实现**；**P2 = 影响范围有限但必须先定口径**。
带「必须裁决」标记的是本文建议不得由实现方代选的项。

<a name="proposal-envdataset-q1"></a>
### Q1【已结案｜作者澄清 2026-10-08】「闪频」为笔误，实为「商品」

原句中的「闪频」确认为笔误，应为「商品」，故该条款读作「绑定 app 的地点支持 persona 远程下单购买该地点类型下包含的商品」。
§1.1 已记录，D2 表已按此更正。本条不再有待裁决内容。

<a name="proposal-envdataset-q1b"></a>
### Q1b【已结案｜作者澄清 2026-10-08】美团外卖是内容 app，两条路径并存

作者澄清：美团外卖是**内容 app**（只有内容 app 才能被「使用」选中并被浏览），同时可以作为**地点所绑的 app** 出现。
商品绑地点且该地点绑了美团外卖 → 外卖；商品直接绑美团外卖 → 快递。两路径互不冲突，详见 §1.1 澄清四。

本条曾被视为「最大遗留歧义」，结案后残余的只是 Q14 的 `manual` 载体问题（数据字段还是文档约定）。

> **前提依赖（实施前必须一并确认）**：本结案的措辞「只有内容 app 才能被『使用』选中并被浏览」预设了
> **「使用」是一个动作、且 `app` 是一个目标类**。而现行冻结设计已把 app 目标类显式删除
> （`docs/dev/SPEC-action-and-content-simplification.md:983`、`:1281`）。因此本条是否可直接落地，
> **取决于 Q12 的裁决方向**：若 Q12 取方案 B（「使用」为笔误，改心智模型），则「内容 app 被『使用』选中」这一表述
> 需要改写成当前设计里的等价机制（人物选动作 + 可选 `app`），本条的结论不变但措辞要跟着改；
> 若取方案 A（新增 `use` 动作与 `app` 目标类），则本条可原样落地。二者都不影响「商品绑地点得外卖 / 绑 app 得快递」
> 这条实质结论——那是一条**购买侧**的绑定规则，与「使用」动作无关。

<a name="proposal-envdataset-q1c"></a>
### Q1c【已结案｜本次会话 2026-10-09】到店 / 外卖的判定权归谁 → 方案 (a)：把判定交回引擎

作者澄清三已把判定规则定为**离散读法**（人在该地点即到店，否则外卖），并认为现状亦然。取证结论与此不符：
现状由 persona 显式写出的 `app` 决定渠道，引擎不做位置判定（ACS P-16）。

**裁决结果**：**采纳方案 (a)——把判定交回引擎**。

具体实施：
1. **重开 ACS P-16**：请求侧不再由 `app` 表达渠道
2. **引擎按位置与绑定侧派生**：
   - 商品绑定地点侧 + persona 当前在该地点 → 到店购买
   - 商品绑定地点侧 + persona 不在该地点但该地点绑了 app → 外卖配送
   - 商品绑定 app 侧 → 快递配送（2-5 天，走驿站）
3. **改动模块**：
   - `src/worldmock/life/basket.py`：`named_purchase()` 的受理路径与渠道判定
   - `src/worldmock/life/admission.py`：商品准入时按绑定侧 + 位置判定可行性
   - ACS P-12/P-13/P-16 需要一次性修订
4. **反事实重放口径**：需要重写，因为渠道从"请求字段"变为"引擎派生"
5. **与 Q21 合并实施**：两者都要求重开 ACS 阶段 2 的冻结决策，建议一次性完成

**连带影响**：
- X4 冲突解除：心智模型的「系统自动判定」与实现对齐
- C4b 需要补充渠道判定逻辑
- 导出记录里的 `delivery_method` 从请求字段变为派生字段

<a name="proposal-envdataset-q2"></a>
### Q2【已结案｜本次会话 2026-10-09】「按顺序提供」是需求还是叙述 → 仅为叙述

现状是清单按路径字典序登记、加载按代码硬编码次序，不存在对声明顺序的校验。若顺序是硬需求（例如后一个文件只能引用前一个已定义身份），
需要新增一个现在完全没有的跨文件拓扑校验与不变量；若只是叙述，C1 只需写明「本设计不声明提供顺序」。

**裁决结果**：**仅为叙述，不是硬需求**。

具体实施：
1. **C1 不引入 `provision_order` 字段**：文件加载顺序由引擎内部决定，数据集不声明
2. **无跨文件引用约束**：每个文件的内容必须是自包含的，或引用其他文件时不依赖加载顺序
3. **C9 不需要顺序校验**：format_version 只声明文件集合与字段，不声明加载顺序
4. **文档表述调整**：
   - C1 明确写「环境数据集提供以下文件（**顺序不保证**）」
   - 或改为「环境数据集包含以下文件」，删除「按顺序提供」的措辞

**连带影响**：
- D1 的 gap 判定简化：只需关注文件清单本身，不需要关注顺序
- 加载器实现不需要改动：继续按代码硬编码次序加载

<a name="proposal-envdataset-q3"></a>
### Q3【已结案｜本次会话 2026-10-09】回调采纳声明式阈值规则 → 方案 (b)

现行冻结契约明文禁止（`docs/dev/SPEC-action-and-content-simplification.md:320`、`:214`；`docs/ARCHITECTURE.md:270`），
而心智模型第 6 项要求支持。

**裁决结果**：**采纳方案 (b)——声明式阈值规则**（作者 D9：「回调暴露越少越好」）。

具体形态（见 §1.2.15）：
1. **一张声明式规则表 + 一个求值器**，数据不携带可执行代码
2. **规则 = `(触发器, 效果)`**：
   - 触发器四类：`action`（具体动作）/ `time` / `state` / `event`
   - 效果三类：`state_delta`（燃料式阈值）/ `footprint_chain`（打车式多足迹）/ `transfer`（跨资源）
3. **文件名改为数据文件**（如 `callbacks.jsonl`），不叫 `item_callback.py`——加载 `.py` 等于允许数据仓携带可执行代码，正是禁令所禁
4. **须成对修订两条禁令**：`docs/dev/SPEC-action-and-content-simplification.md:320` 与 `docs/ARCHITECTURE.md:270`，并新增 SPEC

**关键区分**（§1.2.15）：`state_delta` 覆盖燃油式周期状态；`footprint_chain` 覆盖打车式「系统记全足迹、persona 只看余额」；
两者是同一张表的两行，不是两套机制。

<a name="proposal-envdataset-q4"></a>
### Q4【已结案｜本次会话 2026-10-09】四分类成立，`fuel` 归「商品」，`drink` 走心理侧

**裁决结果**（作者 D5 + D6）：

| 心智模型分类 | 代码类别 | 效果 |
| --- | --- | --- |
| 零食 | `snack` | 加热量（解决饥饿） |
| 食品 | `meal` | 加热量（解决饥饿） |
| 饮料 | `drink` | **走心理侧**（净娱乐需求或情绪脉冲）——**从热量表移出** |
| 商品 | `leisure` + `daily` + **`fuel`** | `leisure` 减 `entertainment_need`、`daily` 施 `mood_valence`、`fuel` 改 `driving_days` |

要点：
1. **热量只由 `meal`/`snack` 承担**（作者 D6：「热量走零食食品类就行了，没必要搞这么复杂」）
2. **`fuel` 属「商品」类**，其周期语义由回调维护（作者 D5）
3. **`drink` 改走心理侧**：persona 买咖啡等由性格画像决定，不应补热量

**破坏性影响**：`GoodsCategory` 六值改四值（或保留六值但四分类作对外口径），`GOODS_ENERGY_KCAL` 移除 `DRINK`，
`GOODS_RECEIPT_EFFECTS` 增 `DRINK`，`receipt._receipt_effect` 补分派分支，`RULES_VERSION` 必须升号。
详见 C3。

<a name="proposal-envdataset-q5"></a>
### Q5【已结案｜本次会话 2026-10-09】只保留 `store_kinds`，删除 `store_ids` 与 `"all"`

**裁决结果**（作者明确：「不要 `store_ids`，就只有 `store_kinds` 字段」）：

1. **商品只声明 `store_kinds`**：取值是**地点类型标签**数组，如 `["便利店", "超市"]`
2. **`store_ids` 与 `"all"` 一并删除**——不存在"按实例绑定"这条路径
3. **XML 互斥约束保留**：两侧取其一，每侧可多个，两侧同时为空或同时非空即拒绝
4. **展开规则**：加载期遍历所有地点，`place.kind in goods.store_kinds` 的加进「该商品有售地点」集合
5. **校验**：`store_kinds` 里每个值必须在 `locations` 里至少有一个条目的 `kind` 匹配，否则 `GOODS_BINDING_DANGLING`

**注意 `store_kinds` 与 `app_ids` 同名但语义不同**：商品的 `store_kinds` 是地点类型标签（数据集自由文本），
与 §1.1 澄清二里「app 侧绑定」的 `app_ids` 是两回事，二者同样是 XOR 关系。

**连带**：`Location.kind` 从 `LocationKind` 枚举改为自由文本（见 Q23），`GOODS_CATEGORY_STORE_KINDS` 引擎固定表废除。
详见 C4、C4b 与 §1.2.17（数据集设计方法）。

<a name="proposal-envdataset-q6"></a>
### Q6【已结案｜本次会话 2026-10-09】`persona.toml` 替换场景 `background_story`

**裁决结果**（作者 D11）：**替换**。

具体实施：
1. **`persona.toml` 成为唯一人设来源**，场景 `agents[].background_story` **删除**
2. **profile 三段式管线输入改为读 `persona.toml`**：`src/worldmock/profile/compile.py` 的 `_resolve_story`
3. **溯源字段同步**：`scenario_sha256` 与 `background_story_sha256` 的取值来源改为 `persona.toml` 的文件身份
4. **persona.toml 的归属**：环境数据集根级文件（与环境根同层），不放进 `catalog/`（那六文件是闭集）
5. **profile v2 拒绝 v1 字段的规则不变**：`persona.toml` 仍须满足 v2 结构，旧模板字段继续 fail-closed 拒绝

**不变量影响**：场景结构须升号；`docs/CONTRACTS.md#contracts-世界mock-接口合同` 的人物档案字段须修订。
详见 C10。

<a name="proposal-envdataset-q7"></a>
### Q7【已结案｜本次会话 2026-10-09】`config.toml` 放非密参数 + 256k 加载期断言

**裁决结果**（作者 D12）：**方案 (a)**。

具体实施：
1. **`config.toml` 随环境发布**，位于环境数据集根级
2. **只含非密参数**：
   - `seed`：**单一随机种子**，覆盖全部随机源（U8 的 R1–R13，见 §1.2.6）
   - `start_time` / `stop_time`：模拟起止时刻，**精确到小时**（作者 U11 裁决）
   - 重试与降级参数（U12：简单降级重试，耗尽即停止）
   - `context_window` 下限声明
3. **凭据永不进入该文件**：仍走仓外 `~/.config/worldmock/deepseek.env`
4. **256k 落成加载期断言**：启动时校验声明的 `context_window ≥ 262144`，否则以英文错误拒绝启动
5. **须修订模型身份章**：`docs/manual/runtime.md:145-147` 明文「没有场景级 `model` 或 `base_url` 覆盖字段」需改写，
   以容纳「环境可声明种子与起止时刻」这一新增事实（注意：**模型身份仍不可被环境覆盖**）

**关键区分**：`config.toml` 携带的是**运行参数**（种子、起止、重试），不是**模型身份**（provider/base_url/requested_model）。
后者继续按 `docs/CONTRACTS.md:1537-1576` 冻结在运行清单里，环境不能覆盖。X1 冲突据此部分解除。
详见 C10。

<a name="proposal-envdataset-q8"></a>
### Q8【已结案｜本次会话 2026-10-09】订阅粒度 = **具体动作**，不是动作类型

**裁决结果**（作者明确：「绑定的是具体的动作，不是动作类型」）：

订阅键是**具体动作**，即 Q21 接口里的 action 值：

| 动作类型 | 可作为订阅键的具体动作 |
| --- | --- |
| 出行类 | `walk`、`drive`、`take_taxi`、`take_bus` |
| 购买类 | `purchase` |
| 睡眠类 | `sleep` |
| 活动类 | `interact` |

例：燃油的规则订阅 `drive` 这一个**具体动作**，不是「出行」这个类型——
所以只有驾车才扣油，步行/打车/公共交通都不扣。作者原文的 `travel_to + by_car` 即此意，
但在新接口下表达为单一的 `drive` 键，不再需要「动作+方式」的复合键。

**四类触发器**（见 §1.2.9 与 §1.2.15）：`action`（具体动作）/ `time` / `state` / `event`。
**效果三类**：`state_delta` / `footprint_chain` / `transfer`。

**「使用」类无可绑位置**：`interact` 是活动类的唯一动作，内容 app 本身即 target，
功能 app 在 `interact` 下没有挂载位——功能 app 只能绑到出行/睡眠/购买的具体动作上。

<a name="proposal-envdataset-q9"></a>
### Q9【已结案｜本次会话 2026-10-09】引擎侧派生，数据集零改动

**裁决结果**（作者 D10）：**方案 (b)——引擎侧按 1800 秒派生**。

机制（**已存在，不需新建**）：
1. **边界来源**：`progress_report_interval_seconds`（缺省 1800）已在活动进行中按该间隔安排决策边界
   （`src/worldmock/life/rhythm.py:213`、`:459`、`:569`；`src/worldmock/life/decision_coordinator.py:426`）
2. **进度来源**：活动数据带 `content_id`（`src/worldmock/life/models.py:240`），
   状态按 `content_id` 记 `content_progress`（`src/worldmock/life/activity_control.py:299-301`）
3. **派生公式**：`已进行 = 当前时刻 - 活动开始时刻`，`总时长 = duration_seconds`，
   persona 恢复时看到「进行到 X / Y 分钟」这一客观事实

**回答作者的原问题**（「只给一段说明，暂停时怎么总结进度」）：
- **进度＝引擎派生**（客观、可重放、零数据成本）
- **总结＝模型自述**（暂停时写一行 `self_note`，走既有通道 `progress_layer.py`；丢了不影响恢复）

因此**数据集只需给一段内容说明，30 分钟切片不是必需字段**。

**(c) 锚点问题**：建议**共用**同一个 1800 秒锚点（二者本就是同一节律），避免与决策成本上界互相干扰。

**连带**：D5 的 gap 判定从「未实现」改为「**以不同形态实现**」，且改动量从「目录结构新增切分字段」降为**零目录改动**。

<a name="proposal-envdataset-q10"></a>
### Q10【已结案｜本次会话 2026-10-09】`drink` 走心理侧，`entertainment_need` 为准

**裁决结果**（作者 D6：「`drink` 走心理需求，persona 买咖啡啥的都是性格画像决定的，热量走零食食品类就行了」）：

对应关系：
| 分类 | 字段 | 语义 |
| --- | --- | --- |
| `drink` | **`entertainment_need`**（递减）或情绪脉冲 | 「心里需求」= 心理需求，由 `entertainment_need` 承载 |
| `leisure` | `entertainment_need`（递减） | 现状不变 |
| `daily` | `mood_valence`（脉冲） | 现状不变 |
| `meal` / `snack` | `energy_kcal`（递增） | 解决饥饿，现状不变 |

**改动**：`DRINK` 从 `GOODS_ENERGY_KCAL` 移出，加入 `GOODS_RECEIPT_EFFECTS`；
`receipt._receipt_effect` 补 `drink` 分派分支。具体数值须由实现时给出并登记溯源（fact/assumption/scenario）。
详见 Q4 与 C3。

<a name="proposal-envdataset-q11"></a>
### Q11【已结案｜本次会话 2026-10-09】无油不阻断，只推送提醒

**裁决结果**（作者 D13：「不然这也不让做那也不让做，太麻烦了，让推送一直提醒 persona 就行」）：

具体实施：
1. **删除 `DRIVING_DAYS_EXHAUSTED` 拒绝**：`src/worldmock/life/fuel.py:42-43`、`:57-58` 不再阻断
2. **驾车永远可行**：油量只用于**提醒**，不影响可行性
3. **提醒走 `app_push`**：低于阈值时按 `PUSH_HOURS_LOCAL`（当地 08:00–21:00 整点）持续推送
4. **拒绝码闭集需同步**：`DRIVING_DAYS_EXHAUSTED` 从登记拒绝码中退役，须同步错误码表与三处测试断言
   （`tests/integration/life/test_acs_contract_guards.py`、`tests/integration/life/test_acs_system_core.py:454`、变异锚点 `tests/acs_acceptance.py:1211`）
5. **合同正文须改写**：`docs/CONTRACTS.md:75`「只有环境明确声明拥有汽车的人物能够驾车；初始可用天数由环境声明」需同步

详见 C8 与 X（冲突表）。

<a name="proposal-envdataset-q12"></a>
### Q12【已结案｜本次会话 2026-10-09】「使用」改为「活动」（interact），采纳 (action, target) 接口

原心智模型把「使用」列为与出行/睡眠/购买并列的第四类型，且其 target 是内容 app；但现行冻结设计已把 app 目标类**显式删除**
（`docs/dev/SPEC-action-and-content-simplification.md:983`、`:1281`），人物只选动作与可选 app。

**裁决结果**：本次会话作者确认采纳方案 A 的变体——不是新增 `use` 动词，而是：
1. **新增 `interact` 动词**（活动类），target 可以是 app 或地点
2. **接口简化为 `(action, target)`**，删除 `way` 参数
3. **工作、跑步定义为内容**，通过 `content.location_kinds` 绑定到地点类型
4. **内容可以绑定 app 或地点类型**（或两者都绑定）

详见 §1（心智模型原文【本次会话新增】段）、§1.2.2（D2 交付物更新版）、Q21（完整设计与裁决点）。

本条与 Q1c 同为 P0，因为二者都要求重开 ACS 阶段 2 的冻结决策。建议**合并实施**：
- Q21 的 `(action, target)` 接口重构
- Q1c 若取方案 (a)（把判定交回引擎），则一并修订 ACS P-12/P-13/P-16 并新增 SPEC
- 若 Q1c 取方案 (b)（维持人物点名），则只需实施 Q21 的接口变更

<a name="proposal-envdataset-q13"></a>
### Q13【已结案｜本次会话 2026-10-09】内容/功能 app 二分是**数据字段**

**裁决依据**：作者澄清四明确「只有内容 app 才能被『活动』选中」，且 Q21 的 `interact` 以 app 为 target——
因此二分**必须**是显式字段，不能靠 `purposes` 是否为空隐式推导（`shopping` 用途的 app 如美团外卖同时可作地点所绑 app，
隐式推导会误判）。

**具体实施**：
1. `App` 模型增加显式分类字段（如 `role`，取值 `content` / `function`）
2. **登记期校验**：
   - 内容 app 可被 `interact` 选中，**不得**绑定具体动作
   - 功能 app **必须**绑定至少一个具体动作，**不得**被 `interact` 选中
3. **字节影响**：`apps.json` 是 manifest 六文件之一，加字段会改文件字节、刷新 `catalog_content_sha256`
   → 按本项目「历史证据不可改写」立场，走**新增格式版本 + 新人工环境**，不原地改写既有夹具

**与 Q12/Q21 的关系**：本条是 `interact` 能否落地的前置——没有显式二分，系统无法判断哪些 app 可以作为 `interact` 的 target。
详见 C13 与 §1.2.8。

<a name="proposal-envdataset-q14"></a>
### Q14【已结案｜本次会话 2026-10-09】`manual` 指 `docs/manual/` 下的设计文档

**裁决结果**（作者 D15）：「`manual` 指我们 `docs/manual/` 目录下的 manual，教使用者如何设计构造环境数据集等支撑 WorldMock
运行的配置文件」。

具体实施：
1. **不是数据字段**：不新增关联文件或字段，目录文件集合不因此变化
2. **在 `docs/manual/` 下写明**：平台 app（美团外卖）作为 `interact` 的 target 时，其「可浏览商品 catalog」这一关联
   如何由数据集设计者构造——写成设计指南，而非机器可读字段
3. **载体已有**：`docs/manual/` 目录已存在（`scenarios.md`、`personas.md`、`resources.md`、`runtime.md` 等），
   本条只需在其中补一节「平台 app 与商品的关系」
4. **本次新增的设计方法应当一并落到该目录**：§1.2.17（地点类型与商品供给）的内容属于同一类文档，

**注意**：本提案（`docs/dev/PROPOSAL-*.md`）是未冻结提案，不是 `manual`；落地时须把设计方法正式迁入 `docs/manual/`。

<a name="proposal-envdataset-q15"></a>
### Q15【已结案｜本次会话 2026-10-09】删除场景地点筛选；出行 target 允许全量目录

#### 先答「场景是什么、存在意义是什么」

**场景（`scenarios/*.json`）是一次模拟运行的完整配置文档**，不是世界的一部分。字段分五类：

| 类别 | 字段 |
| --- | --- |
| **谁在跑** | `agents`（人设、工作安排、联系人、作息窗口） |
| **跑多久** | `start_at`、`end_at`、`timezone`、`calendar` |
| **什么初值** | `accounts`（账户余额）、`body`（身体）、`bills`、`payrolls` |
| **什么节律与上限** | `progress_report_interval_seconds`、`reconsideration_interval_seconds`、`max_same_moment_decisions`、`candidate_pool_limit`、`random_seed` |
| **世界的哪一部分在局** | `locations[]`、`environment`（环境身份钉） |

**存在意义**：场景是**实验身份**——同一份环境目录配 N 个场景 = N 个实验（不同人设、不同时间窗、不同初值），
`scenario_sha256` 进运行清单，导出与检查点按它溯源。数据集现有 6 个场景，其中 `0006` 是 5 个人共用一个世界。

#### `locations[]` 今天做三件事（不止出行筛选）

| 用途 | 消费点 |
| --- | --- |
| **可达 / 可买筛选** | `src/worldmock/life/travel.py:58`、`basket.py:95`、`parcel.py:39`、`discovery.py:45`、`purchase_offers.py:33` |
| **id → 名称查找** | 约 10 处：`environment_layer.py:44`、`tool_layer.py:123`、`result_projection.py:42`、`progress_layer.py:237`、`settled_text.py:124`、`todo_layer.py:56`、`action_run.py:92`、`contact_exchange.py:41`、`day_plan_inputs.py:156`、`presentation.py:126` |
| **准入校验** | `load_checks.py:86`（住处/工作地须在选中内）、`:47`（kind 须在闭集内）、`:168`（display_name 唯一） |

`LocationReference` 只带一个字段（`src/worldmock/life/scenario.py:277-280`，注释即「场景选入的登记地点身份」），
所有元数据都来自目录——它是**选择**，不是**定义**。

**关键事实**：地图节点由**目录**生成（`src/worldmock/cli.py:153` 传的是 catalog 的 locations），
图上本来就有全量节点，`locations[]` 只是在图上**再筛一遍**。实测：`0006` 场景选 **92** 个，目录共 **103** 个。

#### 裁决：删除筛选

**出行 target 允许全量 catalog 中的任意地点**，场景地点筛选整体删除。

**改动面**：
1. **5 处筛选点**改读 `catalog.locations`：`travel.py:58`、`basket.py:95`、`parcel.py:39`、`discovery.py:45`、`purchase_offers.py:33`
2. **约 10 处名称查找**改读 catalog（名称目录本来就有）
3. **3 处准入校验**改为直接对 catalog 校验（住处/工作地只需是已登记地点）
4. **`LocationReference` 与场景 `locations[]` 从场景结构删除**，场景结构须升号
5. **连带**：`load_checks.py:47` 的 kind 闭集校验随 C14 一并消失

**代价（诚实说明）**：
- **prompt 变长**：persona 的可达地点列表从 92 涨到 103。`src/worldmock/life/observe.py:288` 是**全量列出**可达地点、
  不参与轮转的（实测运行里 19 个地点列出 18 个目标），所以这是真实的提示增长
- **场景不再兼任「世界分片」**：多个场景共享一个目录时暴露同一份世界——这与「同一目录配多个场景」的现有用法一致，不冲突
- **地图与场景彻底解耦**：地图本就是目录级产物

**与 N1 / Q23 同向**：N1 的「每个环境集每种文件只有一份」与「场景不再选地点」是同一个方向——
**环境给世界，场景只给运行参数**。

<a name="proposal-envdataset-q16"></a>
### Q16【已结案｜本次会话 2026-10-09】已被 Q5 与 Q23 吸收，`menus_by_kind` 随类型表一并删除

**结案依据**：本条问的是「类型级商品索引该怎么办」，而它的两个前提都已被后续裁决消解：
- **Q5**：商品只声明 `store_kinds`，删除 `store_ids` 与 `"all"`
- **Q23**：`Location.kind` 改自由文本，类型不再是引擎闭集

于是「按类型取菜单」这件事由 `store_kinds` 的字符串匹配直接完成，`menus_by_kind`
（`src/worldmock/catalog/loader.py:81-91` 的 `next(...)` 取同类型首个地点）**失去存在理由**——它本来就是
`"all"` 展开的副产物，而 `"all"` 已删。

**处置**：随 C14 一并删除；`tests/unit/catalog/test_loader.py:22` 的断言同步移除。
`Catalog.location_kinds`（`src/worldmock/catalog/models.py:227`，仅测试调用）同批删除。

---


<a name="proposal-envdataset-q17"></a>
### Q17【已结案｜本次会话 2026-10-09】闹钟删除（不是回调）；水电费由数据集定义并走回调

**闹钟：不适用——已裁决删除**（作者 D17）。`set_alarm` 与 `ALARM_RING` 整个删除，见 §1.2.11。
作者原话：「他要是想设置一个闹钟，我们怎么让这个闹钟真的生效呢？」经查证**已有实现**（`src/worldmock/life/alarm.py:15-33`），
但作者决定删除该工具。**闹钟因此不进入回调清单**——它是 persona 原生请求，不是商品也不是周期状态。

**水电费：已裁决——方案 (b)，由数据集定义并走回调**（作者 2026-10-09）

**先查证前提**：查 WorldMock-Dataset 的 6 个场景，schema **都有 `bills` 键但全部为空**；
全域搜「水电」只命中**语料**（微博段子、知乎问答），**没有任何要求**。
所以水电费确系作者所举的例子，不是数据集的需求。

**裁决**：**方案 (b)**——水电费**是否存在由数据集定义**，走**回调规则表**
（`time` 触发器 + `transfer` 效果），而不是引擎内置的静态排期。数据集声明就有，不声明就没有。

**现状的可用部分保留**：`src/worldmock/scheduling/effects.py:134-145` 的 `_bill_due`
已经满足「**余额不足不阻断**」（记 `bill_paid: false` 并继续运行），这一语义在新形态下保留。

**更正先前提法**：`bills` 已是场景级可选字段（`src/worldmock/life/scenario.py:238`、`:318`），
所以「引擎该不该管」不是真问题；真问题是「数据集声明后走哪条机制」——答案是回调规则表。

<a name="proposal-envdataset-q18"></a>
### Q18【已结案｜本次会话 2026-10-09】已被 Q9 吸收，内容侧无独立回调

**结案依据**：本条问内容侧是否需要独立回调，答案由 **Q9**（D10 裁决）给出：

1. **内容进度已由 `ContentProgress` 承载**：`ContentProgress{content_id, position_seconds, completed_rounds,
   last_consumed_at}`（`src/worldmock/life/models.py:237-243`）
2. **30 分钟切分走引擎派生**（Q9）：按 `progress_report_interval_seconds`（缺省 1800）的既有边界，
   不新增数据集字段
3. **总结由模型自述**：走既有 `self_note` 通道，不构成回调

因此「内容回调」与「30 分钟切分」**是同一件事**，且已按引擎派生结案，不另立机制。

**与商品侧的分工**：商品侧的周期性状态（燃油等）走 Q3 定的声明式规则表（`state_delta`）；
内容侧的「周期」表现为**进度续接**（未完成优先，见 `content_select._unfinished_content`），不是状态阈值。

---


<a name="proposal-envdataset-q19"></a>
### Q19【已结案｜本次会话 2026-10-09】值由数据集声明、规则归引擎：重置到上限

**作者纠正**（2026-10-09）：「这个不应该是数据集定义的吗？我们定义的是规则」——
即「**7**」这个**值**由数据集声明，「**加油即重置到上限**」这条**规则**归引擎。
本文先前的提法把值与规则混为一谈。

**裁决**：
1. **引擎提供 `reset_to_cap` 语义**：加油后 `driving_days` **直接置为上限**，不是加法
2. **上限由数据集声明**：新增字段（如 `max_driving_days`），**不得写死在引擎**（否则又是引擎常量）
3. **现状不符**：`src/worldmock/life/receipt.py:50-53` 是 `driving_days + provided`（加法、无上限），
   连续两次加油得 14 天，与作者原文「**回到** 7 天」直接矛盾

**连带（重要）**：D13 已裁「无油不阻断」，因此 `driving_days` **不再 gate 任何事**，只剩提醒阈值。
于是 `src/worldmock/life/fuel.py:33-43` 的 `require_driving_days` **失去调用方**，其删除面须一并算进 C8：
`fuel.py:42-43` 与 `:57-58` 的 `DRIVING_DAYS_EXHAUSTED` 抛错、`tool_layer.py:170-174` 的 `_travel_check`、
`session_actions.py:539-540`。

<a name="proposal-envdataset-q20"></a>
### Q20【已结案｜本次会话 2026-10-09】按新模块结构归属：环境数据归 03-resource-engine.md

**背景更新（合并后重判）**：2026-10-09 的仓库合并删除了 `docs/CONTRACTS.md`（-2143 行），
同时删除 `docs/ARCHITECTURE.md`、`docs/EXPERIMENT.md`、`docs/VERIFICATION.md`。文档改为模块化：

| 目录 | 职责 |
| --- | --- |
| `docs/design/` | 当前模块设计——**接口、字段、规则、约束的唯一归属** |
| `docs/manual/` | 配置与操作步骤 |
| `docs/dev/` | 开发过程与历史证据（`SPEC-*.md` 仍在） |

`docs/INDEX.md` 明说：「每个接口、字段、规则和约束**只在所属模块定义**，其他章节引用它」。

**重新判定**：

| 内容 | 归属 |
| --- | --- |
| 环境目录文件、对象绑定、登记错误、地图与发布身份、地点供给与价格 | **`docs/design/03-resource-engine.md`**（已有「环境目录文件」「注册校验与错误」「环境发布身份」「地点供给和价格」等节） |
| 工具接口、活动、交易、计划与事务 | `docs/design/04-simulation-engine.md` |
| 固定类型、身体模型、数值与结算规则 | `docs/design/02-exchange-engine.md` |
| 场景结构、人物初值、模型凭据与加载检查 | `docs/design/08-configuration.md` |
| 事件、素材、检查点、认证导出 | `docs/design/05-recording-engine.md` |
| 闭集、常数、算子、`RULES_VERSION` | `docs/dev/SPEC-action-and-content-simplification.md`（**仍在**） |

于是「两处都宣称唯一」的冲突**自行消解**——范围按**模块**切分，而非按文档切分。
`SPEC-ACS:214` 自称的「唯一定义处」其范围本就限定在 `src/worldmock/exchange/`，
与 `03-resource-engine.md` 的「环境目录」范围不重叠。

**发布顺序**：按 `docs/dev/SPEC-environment-data-ownership.md` 的单作者原则——
**引擎先改模块设计并升 `RULES_VERSION`，数据仓随后按新版本重出目录**。
理由：引擎是拒绝方（`RULES_VERSION_MISMATCH` 直接拒载），先改能让数据仓拿到确切的失败码。

<a name="proposal-envdataset-q21"></a>
### Q21【已结案｜本次会话 2026-10-09】start_activity 接口简化为 (action, target) → 采纳

**问题**：当前 `start_activity` 接口使用 `(target, way)` 模式，其中 `way` 是实现细节泄露（"步行" vs "驾车" 只是同一动作的不同参数），
且 `way = "进行"` 承载了所有内容消费（看剧、游戏、工作、跑步），无法区分 app 绑定内容和地点绑定内容。

**裁决结果**：**采纳 `(action, target)` 接口**，详见 C13。

核心变更：
1. **接口简化**：只保留 `action` 和 `target` 两个字段，删除 `way` / `app` / `to` / `pay_app`
2. **7 种 action**：walk/drive/take_taxi/take_bus/purchase/sleep/interact
3. **interact 的双重语义**：
   - target 是 app：从 app 绑定的内容中按现有推荐逻辑随机选一个
   - target 是地点：从地点绑定的内容中按同样逻辑随机选一个
4. **内容绑定方式扩展**：`Content` 模型新增 `location_kinds` 字段（与 `app_ids` 二选一或两者都有）
5. **工作、跑步定义为内容**：不是动作，通过 `interact` 触发

实施要点（详见 C13）：
- 名称解析改为按 action 分派
- way 相关代码全部删除
- 内容选择逻辑沿用，只调整过滤器
- 破坏性改动：所有测试夹具需要改写

**与 Q1c 合并实施**：两者都要求重开 ACS 阶段 2 的冻结决策（目标类与渠道表达），建议一次性完成。

**当前接口**：
```python
class ActivityRequestItem:
    target: str      # 对象名（地点、商品、动作内容）
    way: str         # 方式（步行/驾车/购买/进行/领取/睡觉/等待）
    app: str | None
    to: str | None
    pay_app: str | None
```

**提议的新接口**：
```python
class ActivityRequestItem:
    action: str      # 具体动作名（walk/drive/take_taxi/take_bus/purchase/sleep/interact）
    target: str      # 对象名（地点/商品/app/地点）
```

**四类动作及其 target**：

| 动作类型 | 具体动作 | target 类型 | 示例 |
|---------|---------|------------|------|
| 出行类 | walk, drive, take_taxi, take_bus | 地点名 | `{"action": "walk", "target": "彩虹桥"}` |
| 购买类 | purchase | 商品名 | `{"action": "purchase", "target": "咖啡"}` |
| 睡眠类 | sleep | 地点名（home） | `{"action": "sleep", "target": "家"}` |
| 活动类 | interact | app 名或地点名 | `{"action": "interact", "target": "抖音"}` |

**interact 的双重语义**：

1. **target 是 app**：
   - persona 说 `{"action": "interact", "target": "抖音"}`
   - 系统从抖音绑定的内容中**按现有推荐逻辑随机选一个**（推送优先、未完成优先、系列优先、未看优先、轮次最少）
   - persona 不能指定具体内容，只能指定 app

2. **target 是地点**：
   - persona 当前在公园，说 `{"action": "interact", "target": "公园"}`
   - 系统从公园绑定的内容中**按同样的推荐逻辑随机选一个**（如"跑步"、"散步"）
   - persona 不能指定具体内容，只能指定地点

**内容的两种绑定方式**：

```python
class Content:
    content_id: str
    name: str
    category: str  # work, exercise, entertainment
    duration_seconds: int
    
    # 二选一（或两者都有）
    app_ids: tuple[str, ...] | None          # 绑定 app（看剧、游戏）
    location_kinds: tuple[str, ...] | None   # 绑定地点类型（工作、跑步）
```

**与商品的平行关系**：

| 维度 | 商品 | 内容 |
|---|---|---|
| 绑定 app | ❌ | ✅ 看剧绑抖音 |
| 绑定地点类型 | ✅ 零食绑便利店 | ✅ 工作绑 office |
| persona 如何获得 | 到地点后自动看到商品 | 到地点后自动看到内容（或打开 app 后看到内容） |
| 消费方式 | 购买（支付） | 进行（花时间） |

**数据集设计原则**：
- 一个地点只绑定语义合理的内容：公司 → 只绑定「工作」，公园 → 绑定「跑步」「散步」
- 不会过度设计：不会把「唱K」和「工作」都绑到公司
- 数据集设计者自己保证语义合理性

**需要调整的地方**：

1. **名称解析改为按 action 分派**：
```python
_ACTION_KINDS = {
    "walk": frozenset({"location"}),
    "drive": frozenset({"location"}),
    "take_taxi": frozenset({"location"}),
    "take_bus": frozenset({"location"}),
    "purchase": frozenset({"supply"}),
    "sleep": frozenset({"self"}),
    "interact": frozenset({"app", "location"}),  # 新：可以是 app 或地点
}
```

2. **内容目录包含两类**（只在 action=interact 时查询）：
   - app 绑定的内容（看剧、游戏）
   - 地点绑定的内容（工作、跑步）—— 只在当前地点的 kind 匹配时可见

3. **way 相关代码全部删除**：
   - `vocabulary.py` 的 `WAYS` / `WAY_SYNONYMS`
   - `name_resolution.py` 的 `way_options()` / `pick_way()`
   - `interaction_menu.py` 的 `binding_action()` 改为只接收 action

4. **内容选择逻辑沿用**：
   - `content_select.py` 的 `select_content()` 保持不变
   - 只需调整 `ContentRequest` 从 `(action_id, app_id)` 改为 `(target_id, target_kind)`
   - 过滤逻辑从 `contents_by_action_app[(action_id, app_id)]` 改为：
     - `catalog.contents_by_app[app_id]` 或
     - `catalog.contents_by_location_kind[location_kind]`（只在当前地点匹配时）

**兼容性**：
这是**破坏性改动**：
- 所有旧的 `{"target": "X", "way": "购买"}` 都要改成 `{"action": "purchase", "target": "X"}`
- 7 种 way 降到 7 种 action（walk/drive/take_taxi/take_bus/purchase/sleep/interact）
- 但系统内部更简洁：action 是「做什么类型的事」，target 是「对什么对象做」

**与心智模型的对应**：
- 心智模型第二段「系统侧提供：出行，睡眠，购买，使用，四个动作类型」
- 本提案把「使用」改为「活动」（interact），其 target 可以是 app（使用 app）或地点（在地点进行某活动）
- 工作、跑步被定义为「内容」而非「动作」，通过 interact 触发

**裁决点**：
1. 是否采纳 `(action, target)` 接口替代当前的 `(target, way)` 接口？
2. 是否采纳「活动」（interact）这个动作类型，统一 app 绑定内容和地点绑定内容？
3. 是否接受这个破坏性改动（需要重写所有 model_request 测试夹具）？



<a name="proposal-envdataset-q24"></a>
### Q24【已结案｜本次会话 2026-10-09】方案 (b)：驿站上的 `interact`，一次取走全部

**裁决**（作者 2026-10-09）：**方案 (b)**，且**一次 `interact` 取走该驿站的全部待取件**，
不需要「取 A 再取 B」。

**具体形态**：
```python
# persona 在驿站
{"action": "interact", "target": "菜鸟驿站"}
# 系统一次结算该驿站的全部待取件，不需要逐件操作
```

**落地要点**：
1. **驿站绑一条「取快递」内容**（`content.location_kinds = ["驿站"]`），复用 `interact` 的地点半支
2. **不需要新动作**——`collect_parcel` 可以退役
3. **一次取全部**：结算时取该地点上所有 `ready_for_pickup` 的订单，一次性全部签收；
   因此**不需要处理「一个驿站多条取件内容如何选」**——取件内容只有一条，订单有多件
4. **`accepts_parcels` 标志**（Q23 引入）改为「该地点可绑取件内容」的准入判据，而不是「有领取动作」的判据

**连带修正**：
- §1.2.2 与 §1.2.8 删掉「领取」作为购买类合法动作的表述
- **Q22 第 3 点撤销**（原要求往文档补回 `collect_parcel`，方向与新设计相反）
- C4b 的「优先最近家庭驿站领取」保留，但领取方式改为 `interact`

**原始问题记录**（保留供溯源）：

**问题**（评审 P0-1，经独立验证成立）：

Q21/C13 把动作集定为 `walk`/`drive`/`take_taxi`/`take_bus`/`purchase`/`sleep`/`interact`，
**不含 `collect_parcel`**。但作者亲自拍板的快递链路依赖领取这一步：
- §1.1 澄清二：「app 侧…**默认使用驿站领取流程**」
- C4b：「优先最近家庭驿站领取」
- X6：「仅商品绑定 app 的快递链路走驿站」

而全篇**没有任何地方回答「快递怎么被签收」**。现状依赖 `collect_parcel`：
`vocabulary.py:12`（`"领取": ("obtain", "collect_parcel")`）、`name_resolution.py:19`
（`_WAY_KINDS["领取"] = {"location"}`）、`projection.py:303` 与 `name_resolution.py:201`
（只在 `kind == "parcel_station"` 时提供「领取」）。

**两个连带错误**（同一缺陷的文档面）：
1. §1.2.2 与 §1.2.8 仍把「领取」写成购买类的合法动作（Q21 已删该动作）
2. Q22 第 3 点要求往文档里**补回** `collect_parcel`（「标准动作闭集 5 个 → 6 个」），
   方向与新设计相反

**三个候选方案**：

| 方案 | 内容 | 代价 |
| --- | --- | --- |
| **(a) 到站自动结算** | 快递送达驿站时刻即完成收货，不需要 persona 动作 | 最简；但「本人领取」这一真实行为丢失，`receipt` 的触发点从动作变为排期事件 |
| **(b) 驿站上的 `interact`** | `{"action": "interact", "target": "菜鸟驿站"}` → 内容绑定到驿站上（一条「取快递」内容） | 复用 `interact` 的地点半支，零新动作；但需要驿站绑一条内容，且要处理「有多个待领包裹」的选取 |
| **(c) 系统侧静默结算** | 到站后由引擎在结算时收货 | 最省代码；但等于「快递自动送到手里」，与「驿站」这一设定矛盾，且驿站地点失去用途 |

**建议**：(b) 最贴合作者的设计意图（驿站是 persona 要去的地点，`interact` 是已有的地点绑定入口），
且不需要新动作。但需先定「一个驿站绑多条取件内容时如何选」。

**已裁决（本条头部）**：方案 (b) 驿站上的 `interact`，一次取走全部；`collect_parcel` 退役成立。

**另需补的删除面表**：`wait`/`do`/`collect_parcel` 三者的删除面从未量过（§1.2.11 给 `set_alarm`
列了 14 行、给 `send_message` 列了更大一张，这三个动作一张都没有）。其中 `wait` 最重——
它同时是 persona 可见动作与 `ActivityKind.WAIT` 引擎原语（承载 idle、`no_active_activity` 语义），
散布约 21 个源文件，含 `body.py:25`（1.3 体力系数）、`readable.py:19/21/28`（三张标签表）、
`schedule.py:125`（活动族映射）、`interaction_menu.py:22`（`PROTECTED_SELF_ACTIONS` 强制保留）、
`same_moment.py:195`（受限轮判定）、`evaluation/behavior_metrics.py:326/404`（评估侧基类）。


<a name="proposal-envdataset-q25"></a>
### Q25【待执行｜P1】合并后的引用迁移

**背景**：2026-10-09 的合并重排了文档与测试目录，本提案**大量 `file:line` 引用失效**。
本文所有引用反映**合并前基线 `72f004b`**。

**失效清单**（合并前计数）：

| 被引对象 | 处数 | 现状 | 迁移去向 |
| --- | --- | --- | --- |
| `docs/CONTRACTS.md` | 89 | **已删除**（-2143 行） | 按模块拆分，见下表 |
| `docs/ARCHITECTURE.md` | 10 | **已删除** | `docs/design/01-architecture.md` |
| `docs/EXPERIMENT.md` | 6 | **已删除** | `docs/dev/experiments/zhangjiang-baseline.md` |
| `docs/VERIFICATION.md` | 4 | **已删除** | 各 `docs/dev/<主题>/…-verification.md` |
| `tests/life/*` | 36 | 拆分 | `tests/integration/life/` 或 `tests/unit/life/` |
| `tests/catalog/*` | 17 | 移动 | `tests/unit/catalog/` |
| `tests/exchange/*` | 4 | 移动 | `tests/unit/exchange/` |

**`CONTRACTS.md` 章 → 新归属**（按 `docs/INDEX.md` 的模块表）：

| 原章 | 新归属 |
| --- | --- |
| 环境数据归属合同、环境目录文件、品类契约与目录字段、类别供给的构建合同、营业与报价声明、冻结地图与场景引用、地点类词表 | `docs/design/03-resource-engine.md` |
| 工具接口、活动、交易、计划与事务 | `docs/design/04-simulation-engine.md` |
| 固定类型、身体模型、数值与结算规则 | `docs/design/02-exchange-engine.md` |
| 事件、素材、检查点、认证导出 | `docs/design/05-recording-engine.md` |
| 模型身份、场景、人物初值 | `docs/design/08-configuration.md` |
| CLI 命令接口、环境与检查命令 | `docs/design/10-runtime.md` |
| 世界与人物状态、严格字段 | `docs/design/09-io-formats.md` |
| 禁止进入提示的字段 | `docs/design/06-agent.md` |

**已执行的机械迁移**（路径唯一确定、已逐一核对存在性）：

- `tests/catalog/*.py` → `tests/unit/catalog/*.py`
- `tests/exchange/*.py` → `tests/unit/exchange/*.py`
- `tests/life/*.py` → 按实际位置逐个解析（`tests/integration/life/` 或 `tests/unit/life/`）

**未执行的部分（须后续处理）**：

- **89 处 `CONTRACTS.md:NNNN` 行号引用无法自动映射**——原文件已删除，行号无对应关系。
  须按上表迁移到模块文档的**节锚点**，而非行号。
- `docs/ARCHITECTURE.md` / `EXPERIMENT.md` / `VERIFICATION.md` 的 20 处引用须逐条改投。

**说明**：本条是**本文自身的维护项**，不改动系统设计，不阻塞任何 C 项。


<a name="proposal-envdataset-q26"></a>
### Q26【已结案｜本次会话 2026-10-09】可命名的名词收敛为四类；`self`/`person`/`contact`/`action` 删除

#### 先澄清：`TARGET_KINDS` 不是 `start_activity` 的 target 列表

它曾经是**所有工具接收的名字的种类**——不只是动作，还包括控制工具的参数。每个产出点都在
`src/worldmock/life/projection.py`：

| kind | 产出点 | 服务于谁 |
| --- | --- | --- |
| `location` | `projection.py:299`、`:304` | 出行（`travel_to`） |
| `supply` | `projection.py:312` | 购买（`purchase`） |
| `person` | `projection.py:331` | 同场人物（原社交） |
| `contact` | `projection.py:334` | 联系人（`send_message`） |
| `self` | `projection.py:343` | 「自己」——`wait` 与 `sleep` |
| `action` | `projection.py:354` | 旧设计的 `do`（进行某动作） |
| `activity` | `tool_layer.py:456` | **控制工具**（`pause_activity`、`conclude_activity` 的 `activity_name`） |

**所以「目标类」不是动作类型**，是「persona 能点名的名词分类」。本文先前的提法（「app 要不要成为第八类」）
把这张旧表当成了要继续存在的东西，是错的。

#### 裁决：收敛为四类

| 新 kind | 服务于谁 | 与旧表的关系 |
| --- | --- | --- |
| **`location`** | 出行、`sleep`（target 是 home）、`interact` 的地点分支 | 保留 |
| **`product`** | 购买 | 保留（旧表叫 `supply`，对外映射为 `product`） |
| **`app`** | `interact` 的 app 分支 | **新增**（旧设计把 app 从目标类删过，见 `SPEC-ACS:983`、`:1281`） |
| **`activity`** | 控制工具的 `activity_name`（暂停 / 恢复 / 结束） | 保留 |

**删除四类**（作者裁决 2026-10-09）：

| 删除的 kind | 理由 |
| --- | --- |
| `self` | `sleep` 的 target 已改定为**地点（home）**，`wait` 已删（D24），「自己」再无产出点 |
| `person` | U10 定了不做其他 persona |
| `contact` | D17 删了 `send_message`，社交路径已无 |
| `action` | D22 废弃 `actions.json`，其内容并入 contents |

**`app` 必须是可命名的目标**：作者原话「**如果 interact 的 target 是 app，那么就是使用 app**」——
target 就是 app 名，所以 app 得是 persona 能写出来的名字。这**推翻**了冻结设计里「app 不再是目标类」的裁决
（`SPEC-ACS:983`、`:1281`），须同步改写（见 C15 的目标类登记行）。

#### 四个子问题均已定

1. **`interact` 以地点为 target 时限定为「当前所在地点」**（作者 2026-10-09：「是的」）。
   即「在公园跑步」要求 persona 已在公园；不允许跨地点 interact——那样就与「先出行到某地」重复了。
2. **控制工具（暂停/恢复/结束）保留**。它们是真实机制：`ActivityChange.PAUSE/RESUME/CANCEL`
   （`src/worldmock/life/models.py:183-188`），暂停后活动进待办层的 `paused_activities`，
   进度保留在 `ContentProgress.position_seconds`；恢复要求人回到该活动的地点
   （`src/worldmock/life/activity_control.py:115`）。
   作者自己早前问过「暂停的时候 persona 怎么总结进度」，说明该场景在预期内。
3. **同一份内容不得既绑 `app_ids` 又绑 `location_kinds`**——与商品侧的 XOR 一致，避免两个消费入口。
   §1.2.18 的草图中「两侧都有」的写法据此收紧为**二选一**。
4. **`ActionCategory.SOCIAL` 删除**（作者 2026-10-09：「删除」）。它的关联已全部消失：
   `communication_mode` 已删（D23）、`send_message` 已删（D17）、`person`/`contact` 目标类也已删。
   其体力系数（`exchange/constants.py:22`，1.5/1.3）随之移除。
   **注意**：`SOCIAL` 是 P-17/P-18 的承载者（`docs/dev/SPEC-action-and-content-simplification.md:88-89`），
   删除须同步改写 `docs/design/02-exchange-engine.md`。

<a name="proposal-envdataset-q27"></a>
### Q27【已结案｜2026-10-09】`wait` 的删除面已量化；结论：**不是机械删除**

**裁决**（作者）：「删掉，我老讨厌这个动作了」——**删除 `wait` 已定**。
`do` 与 `collect_parcel` 同批退役（分别被 `interact` 与「驿站上的 `interact`」吸收）。

#### 先更正一条本文先前写错的事实

**`tests/life/` 仍然存在**——它现在只剩**夹具**（11 个 `.py`，`test_*.py` 为 **0**）。
三层的说法基本成立（所有 `test_*.py` 都在 `tests/unit/`、`tests/integration/`、`tests/system/`），
两个例外：`tests/acs_acceptance.py`（无 `test_` 前缀，是登记模块）与旧目录残留的夹具/conftest。

#### 一、词表与枚举

| 位置 | 内容 |
| --- | --- |
| `life/vocabulary.py:11`、`:12` | `WAYS` 里的 `"等待": ("interact","wait")`、`"进行": ("interact","do")`、`"领取": ("obtain","collect_parcel")` |
| `life/vocabulary.py:14` | `ACTION_WAYS` —— 删 3 项，剩 `do`/`wait`/`collect_parcel` 之外的 2 项 |
| `life/vocabulary.py:19`、`:20` | `WAY_SYNONYMS`：`"做"/"参加"`（→进行）、`"等"/"等着"/"等候"`（→等待）|
| `life/vocabulary.py:8` | `LIFE_VERBS`——**三者都不在动词集**（它们是 `interact`/`obtain` 下的方式），**无需改** |
| `exchange/types.py:112-114` | `START_ACTIVITY_ACTIONS` 六成员 → 删 3 |
| `life/models.py:150-156` | `ActivityKind`，`:153 WAIT = "wait"` |
| `life/interaction_menu.py:22` | `PROTECTED_SELF_ACTIONS = frozenset({"wait","sleep"})` |
| `prompt_schemas.py:34`、`:70`、`:125` | `WAY_LABELS = tuple(WAYS)` 由 `WAYS` 派生，改表即改 schema |
| `name_resolution.py:19`、`:20`、`:200-201` | `_WAY_KINDS` 的 `"领取"`/`"进行"`/`"等待"` 三项；`:200` 驿站才追加「领取」 |
| `catalog/affordances.py:26`、`:23`、`:162` | `_ACTIVITY_KINDS`、`RESOURCE_CLAIM_SLOTS`、报错字面 |
| `src/worldmock/data/affordances.toml:4-12`、`:46` | `[kinds.wait]` 整节；`[kinds.action] action = "do"` |

#### 二、`ActivityKind.WAIT` 的语义替身（**最重的一条**）

`WAIT` 不只表示「人物在等待」，它还是**三处「兜底档」的宿主**：

| 位置 | 内容 |
| --- | --- |
| `life/body.py:381-383` | `kind = ActivityKind.ACTION if ActivityKind.ACTION in kinds else ActivityKind.WAIT` —— **「非 sleep 非 action」的静息档** |
| `life/durations.py:18-22` | `derived_activity_duration`：`SLEEP` 走睡眠需要，**其余一律返回 `feedback_seconds`**（即 wait 的派生时长） |
| `life/schedule.py:125`、`:144` | `ACTIVITY_KIND_FAMILIES` 的 `wait→rest`；`"休息": frozenset({"wait","daily_care"})` |
| `exchange/body.py:24-26` | `activity_met = {"wait": 1.3, ...}` |
| `life/rhythm.py:460`、`life/schedule.py:408-409` | `no_active_activity` 的唯一来源与唤醒原因 |
| `life/rhythm.py:391-394`、`:407-415` | `_has_commitments`：进行中的等待不算在手承诺 |
| `life/tool_layer.py:257`、`:262-265` | `_OBJECT_CHECKS` 与 `_derived_seconds` |
| `life/session_actions.py:510-512`、`:553` | 无目标自身活动的分派 |
| `life/same_moment.py:195-196`、`:46`、`:48` | 受限轮的 `wait` 请求类 |
| `footprints/app_usage.py:125` | `{"wait","sleep","travel","action"}` 种类闭集校验 |

**处置（作者裁决 D25）：不新增任何兜底原语，三处逐点解决。**

作者原话：「**不保留，直接删除，耗尽就查问题，不要用兜底掩盖**」。

> **为什么这个立场更干净**：新增一个引擎内部的 `IDLE` 原语，等于把 P-19 的机器**原样保留下来、只是藏起来**——
> 行为不变，却对外宣称删掉了。那是掩盖，不是删除。作者要求的是让删除**真的改变行为**，
> 并把由此暴露的异常当作**待查的问题**，而不是用一层新枚举兜住。

逐点处置：

| 位置 | 处置 | 判据 |
| --- | --- | --- |
| `life/body.py:381-383` | `kind = ACTION if ACTION in kinds else WAIT` 这一兜底分支**删除**；`next_body_boundary_seconds` 改为接收「有无活动」两个显式入口，或让无活动路径**不经此函数** | 若无活动路径必须经过它，说明该函数的签名设计有问题，**须重构而非加枚举** |
| `life/durations.py:18-22` | 非 `SLEEP` 一律返回 `feedback_seconds` 的兜底分支**删除**；每个仍可启动的活动**必须自带时长来源** | 若删除后仍有活动落到该分支，说明那个活动的时长未定义完备，**须补定义** |
| `life/schedule.py:125`、`:144` | `ACTIVITY_KIND_FAMILIES` 去 `wait` 项；`"休息"` 族改为 `frozenset({"daily_care"})` | 纯表收缩，无争议 |

**关键判据**：这三处若在删除后**变得不可达**，说明它们本来就只为 `wait` 存在，删除即完成；
若某处**仍被触发**，那是**真问题**（该路径的语义从未被正确定义），须补设计而不是补兜底。

#### 三、受保护动作规则（**需要重定义，不是机械删除**）

`PROTECTED_SELF_ACTIONS`（`interaction_menu.py:20-22`）保证「每个地点都能等待与睡觉」。
删 `wait` 后只剩 `sleep`，而 **`sleep` 有前置条件**（睡眠需要、地点），
人物可能在某地点**不被保证任何动作**。

必须重写：要么改成「必须保留 `sleep`」并接受该风险；要么改成「必须保留至少一个无条件可用的自身动作」
——但那就需要一个**新的无条件自身动作**，等于把 `wait` 换个名字。

变异靶子 `tests/acs_acceptance.py:45`（`acs_wait_unprotected`）随之失效。

#### 四、idle 时钟推进：**常规路径变空**（最重的后果）

`docs/dev/SPEC-life-runtime.md:860` 明写技术停顿是「防止同一秒无限循环的**保险**，常规路径靠系统层规则与工具层说明」。
**`wait` 就是那个常规路径。** 删掉后每个空档变成：

```
同刻 3 轮 → 3 个受限轮 → 预算耗尽 → decision_budget_exhausted 事件 + feedback_seconds（默认 900s）跳步
```

**有世界后果**（作者裁决 D25：**这些后果被接受，不掩盖**）：

| 后果 | 定性 |
| --- | --- |
| 质量报告的 `decision_budget_exhausted_events` 从异常计数变成常态计数 | **转为诊断信号**——报告项的语义须重新定义：它不再表示「异常」，而表示「本段空档没有可用动作」。阈值与告警口径同步改 |
| 每个空档多写一条 `visibility=system` 的 `environment_event` | 接受（足迹更厚，但真实反映「这段时间人物没做事」） |
| 每个空档**多烧 6 次模型回合** | 接受；若成本不可接受，那是**动作空间设计的问题**，须从设计侧解决，不得用兜底动作掩盖 |
| `waited` 已核验经历（`footprints/experiences.py:193`，是 `docs/design/05-recording-engine.md:199` 记录的四种之一）与 `wait_minutes`（`evaluation/behavior_metrics.py:398`）**失去来源** | **随 `wait` 一并删除**；`docs/design/07-evaluation.md:112-116` 的报告项作废 |
| `effective_autonomous_decision_rate`（`:334`）的「等待算被动」口径失去判定基 | 该口径删除或重定义（被动不再由「等待」表达） |

> **作者立场的要点**：**耗尽是要去查的问题，不是要用兜底掩盖的现象。**
> 若删除 `wait` 后每个空档都耗尽预算，那说明**动作空间设计得不够密**——
> 真正的修法是让空档里**有正当事可做**（或让人物能合法地什么都不做而不记为异常），
> 而不是塞一个隐藏的 idle 动作把数字抹平。

#### 五、删 `wait` 会不会破坏「决策不耗时」？

**不破坏，但把一份保险变成常规路径。** 该不变量的承载者是三样**别的**东西：
时钟门禁（`time_advance.py:100-109`）、同秒再决策（`same_moment.py:142-153`）、规则 3 的技术停顿
（`same_moment.py:165-180` + `decision_coordinator.py:471-478`）。

**已裁决（D25）：不保留隐藏原语。** 本文先前建议的「引擎内部 idle 原语」被作者否决，
理由是那等于**把 P-19 的机器藏起来假装删掉**。

**接受的结果**：「空档」不再是一种**被记录的生活状态**，而是由**预算耗尽**表达，
并因此成为**可观测、可追责的信号**。若这个信号频繁出现，**那是动作空间的问题，须从设计侧修**
——这正是作者要的效果：**不掩盖，让它暴露出来**。

**落地时须同时做的两件事**（否则信号会变成噪音）：
1. **重新定义 `decision_budget_exhausted_events` 的报告语义**：从「异常」改为「本段无可用动作」的计数，
   并给出可接受区间，而不是零容忍阈值。
2. **在 `docs/design/06-agent.md` 的系统层提示里写清**：若确实无事可做，人物应当**选择一个合法的低消耗活动**
   （而不是被推入耗尽的调度兜底）——把「空档该怎么办」变成**给模型的显式指引**，
   这与原本 `wait` 承担的「常备无害动作」是两种做法：前者要求模型自己判断，后者由系统保证。

#### 六、测试与文档规模（粗略）

| 层 | 规模 |
| --- | --- |
| 测试 | **约 100 个文件**含相关 token；重点：`tests/integration/life/test_same_moment_decisions.py`（40）、`tests/integration/life/test_export_integration.py`（32）、`tests/unit/agent_protocol/test_interaction_menu.py`（28）、`tests/integration/runtime/test_same_second_continuation.py`（23）、`tests/integration/life/test_wait_passive_entertainment.py`（7，**文件名即主题**） |
| 文档 | `docs/design/` 68 处、`docs/manual/` 12 处、`docs/dev/SPEC-*.md` 约 200 处 |
| 关键互锁 | `tests/unit/life/test_readable.py:73`（两张标签表与枚举互锁）、`tests/unit/exchange/test_types.py:34`、`tests/unit/catalog/test_affordances.py:17` —— **这三条断言必须与枚举同批改** |
| 假阳性 | `docs/design/10-runtime.md:69`「等待环境 actor 返回」——动词，**不要误删** |

#### 七、`collect_parcel` 与 `do` 的代码面

| 动作 | 代码面 |
| --- | --- |
| `collect_parcel` | `life/parcel.py` 整模块（**65 行**）、`storefront_runtime.py:44-46`、`session_actions.py:557-558/603-606/733`、`admission.py:107/266`、`tool_layer.py:258/387`、`activity_control.py:110`、`projection.py:260/306`、`name_resolution.py:19/200-201` |
| `do` | 它是**所有目录动作**的规范动作名（`projection.py:356`、`admission.py:109`、`tool_layer.py:328`、`name_resolution.py:192`），改名一遍等于动整条动作受理链 |

**唯一未决点**：驿站要成为 `interact` 的 target 就得**绑一条内容**，而「一个驿站多条取件内容怎么选」——
Q24 已裁决「一次取走该站全部」，故取件内容应只有一条，此点随之关闭。

<a name="proposal-envdataset-q22"></a>
### Q22【已结案｜本次会话 2026-10-09】修，保持文档一致性

**裁决结果**（作者 D16：「该修修，保持文档一致性」）。

修正清单（C12 执行）：
1. `docs/CONTRACTS.md:180`「八个 `LocationKind`」→ **十个**；`docs/dev/SPEC-interaction-menu.md:78` 同
2. `docs/CONTRACTS.md:693` 与 `:1668`「五个商品类别」→ 按 Q4 裁决后为**四分类**（若最终保留六值则写六）
3. `docs/dev/SPEC-interaction-menu.md:45` 标准动作闭集 5 个 → 6 个（补 `collect_parcel`）
4. `README.md:87` 与 `CLAUDE.md:67-76` 引用的 `experiments/published-environment/` 与五个 helper 脚本在本仓**不存在**，
   实有 `bootstrap.py`、`coverage_gate.py`、`render_day.py`、`inspect_run.py` → 按实有内容改写
5. `docs/dev/SPEC-environment-data-ownership.md:75` 同样引用 `experiments/published-environment/` → 同步修正

**注意**：第 2 项依赖 Q4 的最终形态；第 1、3 项可立即执行。修改后须跑
`tests/test_documentation_gate.py`，并确认 `docs/dev/ANALYSIS-spec-coverage.md` 的引用计数不漂移。

<a name="proposal-envdataset-q23"></a>
### Q23【已结案｜本次会话 2026-10-09】`Location.kind` 改为数据集自由文本

**问题**：现状 `LocationKind` 是引擎闭集（10 个成员，`src/worldmock/exchange/types.py:7-20`），数据集只能从中选，
`docs/CONTRACTS.md:55` 明文「包规则只从引擎读取；环境没有规则副本」。而商品按类型绑定（Q5）要求类型是数据集可定义的。

**裁决结果**（作者选方案 b）：
1. **`Location.kind` 从 `LocationKind` 枚举改为自由文本**（如 `"便利店"`、`"咖啡馆"`、`"驿站"`）
2. **类型由数据集自己定义**，不再是系统规定的 10 个值
3. **匹配靠字符串相等**：商品的 `store_kinds` 与地点的 `kind` 用同一个词即绑上
4. **`LocationKind` 枚举与 `GOODS_CATEGORY_STORE_KINDS` 固定表废除**——「哪类地点能卖哪类商品」改由商品自己声明

**连带改动**：
| 项 | 处置 |
| --- | --- |
| `PLACE_KIND_ENVIRONMENTS` | 删除（它只有一个真实消费者：运动地点资格，见 §1.2.16 结论一）；运动资格改为从回调/显式集合派生 |
| `PLACE_TYPES`（中文标签表） | 删除——`kind` 本身已是中文，不需要第二张映射表 |
| `interaction_menus_by_location_kind` | 删除（菜单今天空转，且原理上无法表达有意义的地点点约束，见 §1.2.16 结论二） |
| `scenario.py:377` 十类全覆盖校验 | 删除 |
| `load_checks.py:42-48` | 改为只校验 `kind` 非空，不校验枚举归属 |
| `projection.py:303`、`name_resolution.py:201`（`kind == "parcel_station"` 判「领取」动作） | 改为**地点条目上的显式布尔标志**（如 `accepts_parcels: true`），不再靠 kind 字符串比较 |
| `admission.py:220`（`STORE_KINDS` 地点强制 merchant 账户） | 改为按「该地点是否可售」的显式标志判定 |

**不变量影响**：
- 这是**破坏性改动**：`kind` 的取值集合从 10 个枚举值变为自由文本，所有已发布目录的字节都会变
- 按「历史证据不可改写」立场走**新增格式版本 + 新人工环境**
- 地点条目仍是 `location.jsonl` 的一条（N1 的「条目即分组」与 kind 并存）

**作者原话依据**：
- 「室内/室外 —— 所有之前的运行数据来看，模型从来没有考虑过这个」（取证证实：`location_kinds` 从未进入 model_request）
- 「可售商品类别 —— 这个实现的能力不就是和商品绑定地点集合重合了吗？」
- 「该场所的动作菜单 —— 我们的动作什么时候和地点绑定了？」
- 「中文展示名 —— 没看懂」（答案：`PLACE_TYPES` 把 `kind` 翻成中文给 persona 看，新设计下不需要）

详见 §1.2.17（数据集设计方法）与 C14。

---

<a name="proposal-envdataset-验证计划"></a>
## 九、验证计划

<a name="proposal-envdataset-验证层次"></a>
### 9.1 各变更项的证明方式

| 变更项 | 证明载体 |
| --- | --- |
| C1、C12 文档层 | `tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve`；`docs/dev/ANALYSIS-spec-coverage.md` 的 clause→test 计数前后一致 |
| C2 住宿类型准入 | 新增反例测试（无 home 地点、住处指向 restaurant）＋ 现有人工环境加载测试；依赖 catalog 构造的用例逐个补 home 地点 |
| C3 商品分类与 `drink` 归宿 | `tests/unit/catalog/test_admission.py`、`tests/integration/life/test_acs_system_core.py` 的按类别夹具；`scripts/coverage_gate.py` 的 85% 分支门限与公共接口零缺口 |
| C4 商品绑定地点类型 | 新增「两分店菜单不同」的反例；`GOODS_BINDING_KIND_INCONSISTENT` 用例改写；登记期展开与 `goods_by_store` 一致性 |
| C5 内容绑 app 下界 | `tests/unit/catalog/test_loader.py`、`tests/integration/life/test_content_select.py`、`tests/integration/life/test_app_progress.py`；新拒绝码进错误码表 |
| C6 30 分钟切分 | 方案甲：登记校验（合计等于 `duration_seconds`、每段 ≤ 1800、索引连续）正反例；方案乙：跨 1800 秒边界的进度记录与记忆层切句测试 |
| C7 周期性状态与回调 | 新增 SPEC 与 clause→test 映射；阈值触发推送投递测试（复用 `PUSH_HOURS_LOCAL`）；同日不重扣与跨午夜语义在新结构上的等价断言 |
| C8 不阻断语义 | `tests/integration/life/test_acs_contract_guards.py:129` 与 `tests/integration/life/test_acs_system_core.py:454` 按新语义重建；`tests/acs_acceptance.py:1211` 变异锚点同步 |
| C9 文件名与格式 | 人工环境全部文件重出并重钉哈希；`tests/test_validation.py`、`tests/catalog/*` 期望值同步；字节级 `--check` 重生成比较 |
| C10 persona/config 载体 | 场景结构升号的加载与旧字段拒绝测试；两份场景夹具改写；`tests/unit/catalog/test_admission.py` 文件集合反例改写 |
| C4b 商品绑定 XOR | 三条反例（两侧混用、两侧皆空、`app_id` 未登记）＋四条正例（多地点到店、多地点外卖、多 app 快递、`store_ids`/`store_kinds` 并用） |
| C11 不变量 | 不是变更项，是横切约束表；每项须在对应 C 项的验证中体现 |
| C13 `(action, target)` 接口 | `tests/unit/life/test_start_activity_schema.py`、`tests/integration/life/test_start_activity_contract.py`、`tests/integration/life/test_name_resolution.py`、`tests/integration/life/test_content_select.py`；全部 model_request 夹具改写并通过完整日回归 |
| C14 `kind` 自由文本 | `tests/unit/catalog/test_admission.py`（`GOODS_BINDING_DANGLING` 反例）、`tests/unit/catalog/test_loader.py`（按 `store_kinds` 展开）、`tests/integration/life/test_environment_layer.py`（名称改由 `kind` 直接提供）、`tests/unit/exchange/test_types.py` 与 `tests/unit/engineering/test_single_source.py` 的 `PLACE_KIND_ENVIRONMENTS` 用例删除 |
| C15 冻结条款改写登记表 | 登记项本身零代码；每行实施后须有对应 SPEC 版本表行与测试；`docs/design/03-resource-engine.md` 的派生表同步 |
| C16 删除场景地点筛选 | `tests/unit/life/test_scenario.py`（`locations` 字段移除）、`tests/integration/life/test_travel_graph.py`、`test_travel_cost.py`、`test_product_discovery.py`、`test_purchase_apps.py`、`test_environment_layer.py`；数据集 6 个场景移除 `locations` 键并重出 |

<a name="proposal-envdataset-验证门禁"></a>
### 9.2 项目级门禁与不变量复核

每次落地后按项目既有口径复核：

1. **规格追溯**：`docs/dev/ANALYSIS-spec-coverage.md` 的 clause→test 矩阵必须无缺口；新增条款必须补一行映射，
   验证入口是真实模型调用、真实外部服务或长墙钟运行的按既有豁免规则登记。
2. **覆盖率门禁**：`uv run python scripts/coverage_gate.py`——分支覆盖率 ≥ 85%，公共接口零缺口；
   占位实现算未覆盖（`pyproject.toml` 取消了裸 `...` 的排除），故不得用 stub 交差。
3. **代码风格门禁**：`tests/test_style_gate.py` 对 `src/worldmock` 与 `custom` 生效——行 ≤ 120 字符、文件 ≤ 2000 行、
   生产函数 ≤ 50 行且 ≤ 5 参数、每个生产函数有含中文的 docstring。若动用 `_OUTSTANDING` 台账，必须同向收缩。
4. **字节级可复现**：导出以显式 cutoff 为唯一输入（`src/worldmock/footprints/export.py:33-35`、`store.py:265`）；
   任何新增随机量必须走 `src/worldmock/life/randomness.py:7-11` 的带键流；同一冻结目录加同一场景种子必须逐字节可复现；
   既有完整日基线不得被新字段改变，否则基线复跑失配。
5. **历史证据不可改写**：已存运行、导出与冻结输入保持原字节；不提供旧格式兼容层。因此 C3/C4/C5/C6/C9/C10 一律走
   「新增格式版本 + 新人工环境」，不原地改写既有夹具（除非该夹具本就未冻结，需逐项核对）。
6. **门的自证**：任何「加载期拒绝」的新增校验都必须同时给出反例测试，否则失败关闭规则无法证明。

<a name="proposal-envdataset-本文自身的门禁代价"></a>
### 9.3 本文自身的门禁代价（需在实施时处理）

本文为满足「逐条 file:line 取证」的要求，通篇使用 `路径:行号` 形式的源码引用，而
`scripts/documentation_gate.py` 的失败关闭规则禁止**当前文档**出现源码行号引用（`SOURCE_LINE`，该检查只对
历史文档豁免，判定依据是 `docs/archive/`、`docs/dev/research/` 目录或 `docs/INDEX.md` 中状态属于历史集合的行）。
本文位于 `docs/dev/` 且尚未在 `docs/INDEX.md` 登记，因此 `tests/test_documentation_gate.py` 会在本文上失败。三选一：

- (a) 在 `docs/INDEX.md` 把本文登记为 `未冻结提案`，使其进入历史集合而豁免行号检查；
- (b) 把行号引用改写为 `路径::符号名` 形式（该形式要求符号确实存在于声明处，见 `symbol_problem`）；
- (c) 接受门禁在本文上失败，只在实施阶段的文档中遵守行号禁令。

本文不改动 `docs/INDEX.md`（本次任务只允许创建这一个文件），故未自行登记。建议采纳 (a)，并在实施时同时补 `docs/INDEX.md`
的文档表一行与相应职责描述。

<a name="proposal-envdataset-验证证据边界"></a>
### 9.4 结论边界

本文是差距分析与变更排序，**不宣称任何变更已实现或已验收**。九路取证的证据层级为只读代码与文档核对加一轮对抗性交叉验证；
凡涉及运行行为的断言都需要按上表在实施阶段取得可收集的测试节点回执，不继承历史执行数字。
其中「事件类型封闭集」「只由环境变更世界状态」「导出可复现」「profile v2 拒绝 v1 模板字段」「四条动作类型的 target 约束」
五条不可变项为本次分析的硬边界（见 C11），任何变更项都不得以「先做再补」的方式绕过。


<a name="proposal-envdataset-design-method"></a>
### 1.2.17 环境数据集设计方法：地点类型与商品供给

本节面向**环境数据集设计者**，说明如何使用地点类型机制来组织商品供给，以及典型场景的处理方法。

#### 核心机制：类型是数据集自己定义的标签

**地点类型不是系统规定的 10 个固定值**，而是数据集自己定义的自由文本标签。你可以叫它「便利店」「咖啡馆」「驿站」，
也可以叫「24h 便利店」「精品咖啡」「社区服务点」——只要商品和地点用同一个词，就能匹配上。

**为什么需要类型**：让商品能写「在所有便利店有售」而不必把 50 个便利店的 `location_id` 逐个列出来。
新增一家便利店时，只要它的 `kind` 是「便利店」，之前声明 `store_kinds: ["便利店"]` 的商品自动覆盖到新店，
不需要回商品文件逐个加 ID。

#### 地点条目的三个字段

| 字段 | 含义 | 示例 | 必填 |
|---|---|---|---|
| `name` | 品牌或场所名 | `"7-11"`、`"罗森"`、`"菜鸟驿站"` | 是 |
| `branch_label` | 分店标签 | `"张江路店"`、`"研发中心店"` | 否 |
| `kind` | 业态或类型标签 | `"便利店"`、`"咖啡馆"`、`"驿站"` | 是 |

**persona 看到的地点名**：

- 无 `branch_label` 时：「地点：菜鸟驿站」
- 有 `branch_label` 时：「地点：7-11（张江路店）」

**多实例地点的典型写法**：

```json
{"location_id": "store-711-zhangjiang", "name": "7-11", "branch_label": "张江路店", "kind": "便利店", ...}
{"location_id": "store-711-rd", "name": "7-11", "branch_label": "研发中心店", "kind": "便利店", ...}
{"location_id": "store-family-zhangjiang", "name": "全家", "branch_label": "张江路店", "kind": "便利店", ...}
{"location_id": "store-lawson-metro", "name": "罗森", "branch_label": "地铁站店", "kind": "便利店", ...}
```

四个地点，`name` 不同（两个品牌），`branch_label` 各异，但 `kind` 都是「便利店」——
于是一件商品声明 `store_kinds: ["便利店"]`，自动在这四家都有售。

#### 商品条目的 `store_kinds` 字段

**唯一的供给声明字段**：`store_kinds`，取值是地点类型标签的数组。

```json
{
  "goods_id": "snack-chips-001",
  "name": "乐事薯片",
  "category": "snack",
  "store_kinds": ["便利店", "超市"],
  "price_fen": 850,
  ...
}
```

**展开规则**（加载期，引擎自动完成）：遍历所有地点，`place.kind in goods.store_kinds` 的加进「该商品有售地点」集合。

**旧做法对比**（已废弃的 `store_ids` 字段）：

```json
{
  "goods_id": "snack-chips-001",
  "name": "乐事薯片",
  "category": "snack",
  "store_ids": [
    "store-711-zhangjiang", "store-711-rd", "store-family-zhangjiang", "store-lawson-metro",
    "store-711-jinqiao", "store-family-lujiazui", "store-lawson-century", ...
    // 逐个列举 50-60 个地点 ID
  ],
  ...
}
```

新增一家便利店时，旧做法要回这里加一个 ID；新做法只要新地点的 `kind` 是「便利店」，商品自动覆盖。

#### 典型场景

##### 场景 1：零食饮料在便利店和超市有售

```json
// 商品
{"goods_id": "snack-chips-001", "name": "乐事薯片", "category": "snack", "store_kinds": ["便利店", "超市"], ...}
{"goods_id": "drink-coke-001", "name": "可口可乐", "category": "drink", "store_kinds": ["便利店", "超市"], ...}
```

结果：这两件商品在所有 `kind="便利店"` 或 `kind="超市"` 的地点都有售。

##### 场景 2：燃油只在加油站

```json
// 地点
{"location_id": "gas-sinopec-zhangjiang", "name": "中石化", "branch_label": "张江路加油站", "kind": "加油站", ...}
{"location_id": "gas-sinopec-jinqiao", "name": "中石化", "branch_label": "金桥加油站", "kind": "加油站", ...}

// 商品
{"goods_id": "fuel-92", "name": "92号汽油", "category": "fuel", "store_kinds": ["加油站"], ...}
{"goods_id": "fuel-95", "name": "95号汽油", "category": "fuel", "store_kinds": ["加油站"], ...}
```

结果：两种燃油在两个加油站都有售。

##### 场景 3：餐馆的招牌菜只在那一家

**方法 A：给那家餐馆起独特类型**

```json
// 地点
{"location_id": "restaurant-ajisen", "name": "味千拉面", "kind": "日式拉面", ...}
{"location_id": "restaurant-yonghe", "name": "永和大王", "kind": "中式快餐", ...}

// 商品
{"goods_id": "meal-ajisen-tonkotsu", "name": "味千豚骨拉面", "category": "meal", "store_kinds": ["日式拉面"], ...}
{"goods_id": "meal-yonghe-soybean", "name": "永和豆浆", "category": "meal", "store_kinds": ["中式快餐"], ...}
```

结果：每件商品只在对应餐馆有售。

**方法 B：多家特色餐馆共用一个类型**

如果有多家日式拉面店、每家都有自己的招牌菜，但所有日式拉面都在所有日式拉面店有售：

```json
// 地点
{"location_id": "restaurant-ajisen", "name": "味千拉面", "kind": "日式拉面", ...}
{"location_id": "restaurant-ichiran", "name": "一兰拉面", "kind": "日式拉面", ...}

// 商品
{"goods_id": "meal-tonkotsu", "name": "豚骨拉面", "category": "meal", "store_kinds": ["日式拉面"], ...}
{"goods_id": "meal-shoyu", "name": "酱油拉面", "category": "meal", "store_kinds": ["日式拉面"], ...}
```

结果：两种拉面在两家店都有售。

##### 场景 4：某商品只在 3 个特定地点有售（不是按业态，而是按实例）

给这 3 个地点起一个共同的、独特的类型：

```json
// 地点
{"location_id": "store-711-zhangjiang", "name": "7-11", "branch_label": "张江路店", "kind": "便利店-试点", ...}
{"location_id": "store-family-lujiazui", "name": "全家", "branch_label": "陆家嘴店", "kind": "便利店-试点", ...}
{"location_id": "store-lawson-pudong", "name": "罗森", "branch_label": "浦东店", "kind": "便利店-试点", ...}

// 商品
{"goods_id": "special-yogurt", "name": "手工酸奶", "category": "snack", "store_kinds": ["便利店-试点"], ...}
```

结果：手工酸奶只在这 3 家「便利店-试点」有售，其他便利店（`kind="便利店"`）没有。

**注意**：这 3 家店如果还要卖普通零食，普通零食的 `store_kinds` 要写 `["便利店", "便利店-试点"]`，
或者把这 3 家店改成 **`kind: "便利店"`，然后给手工酸奶单独起一个类型、只在这 3 家绑**——
这需要给这 3 家店**再加一个类型字段**，而当前模型不支持一个地点有多个类型。

**推荐做法**：如果「只在部分实例有售」的商品不多，给这些地点起独特类型；
如果很多商品都有各自的部分覆盖，说明数据集的粒度需求是「逐实例声明」，那应该保留旧的 `store_ids` 机制——
但按你的裁决，我们**只保留 `store_kinds`**，所以这类需求只能用「独特类型」处理。

#### 边界情况与校验

##### 商品绑了不存在的类型

```json
// 地点（只有「便利店」，没有「超市」）
{"location_id": "store-711-zj", "name": "7-11", "kind": "便利店", ...}

// 商品
{"goods_id": "snack-chips", "name": "薯片", "store_kinds": ["便利店", "超市"], ...}
```

**结果**：加载期拒绝，报错 `GOODS_BINDING_DANGLING`（商品绑定了不存在的地点类型「超市」）。

**修复**：要么删掉商品里的「超市」，要么新增一个 `kind="超市"` 的地点。

##### 地点没有任何商品

```json
{"location_id": "park-zhangjiang", "name": "张江公园", "kind": "公园", ...}
```

没有商品声明 `store_kinds: ["公园"]`。

**结果**：允许。公园可能只用来做活动（散步、运动），不卖东西。

##### 两个地点同名但类型不同

```json
{"location_id": "hema-fresh", "name": "盒马", "kind": "超市", ...}
{"location_id": "hema-station", "name": "盒马", "kind": "驿站", ...}
```

**结果**：允许。`name` 相同、`kind` 不同，用 `location_id` 区分。如果需要让 persona 区分，可以加 `branch_label`：

```json
{"location_id": "hema-fresh", "name": "盒马", "branch_label": "生鲜超市", "kind": "超市", ...}
{"location_id": "hema-station", "name": "盒马", "branch_label": "驿站", "kind": "驿站", ...}
```

persona 看到：「地点：盒马（生鲜超市）」「地点：盒马（驿站）」。

#### 设计建议

1. **优先按业态分类**：同一业态的地点用同一个 `kind`（所有便利店都是「便利店」），这样商品供给最容易管理。
2. **特殊情况用特殊类型**：如果某类商品只在少数几个地点有售，给这些地点起一个独特的 `kind`。
3. **类型名称用中文**：`kind` 会出现在导出记录里（「在便利店购买」），用中文可读性更好。
4. **避免过细的类型**：如果每个地点都是独特类型，就失去了类型的意义——那不如直接逐实例声明（但我们已经废弃 `store_ids`）。
5. **新增地点时检查类型一致性**：如果新增一家「7-11」，它的 `kind` 应该与其他 7-11 一致（都是「便利店」），
   除非这家店确实业态不同（比如「7-11 驿站」）。

#### 与引擎固定表的对比

**旧机制**（`GOODS_CATEGORY_STORE_KINDS`，引擎固定）：

```python
GOODS_CATEGORY_STORE_KINDS = {
    GoodsCategory.SNACK: frozenset({LocationKind.RESTAURANT, LocationKind.CAFE,
                                     LocationKind.CONVENIENCE_STORE, LocationKind.SUPERMARKET}),
    GoodsCategory.FUEL: frozenset({LocationKind.GAS_STATION}),
    ...
}
```

- 10 个地点类型是系统枚举，数据集只能从中选
- 6 个商品类别各自对应一组固定的地点类型
- 数据集**不能定义新类型**，也**不能改变类别与类型的对应关系**

**新机制**（`store_kinds`，数据集自由）：

- 地点类型是数据集自己定义的自由文本
- 商品直接声明 `store_kinds`，不受类别限制（零食可以只在加油站卖，燃油也可以在便利店卖——虽然不合常理，但机制允许）
- 数据集完全控制「什么商品在哪些类型地点有售」

**迁移路径**：

旧数据集用的 10 个类型（`home`/`office`/`park`/`fitness`/`restaurant`/`cafe`/`convenience_store`/`supermarket`/`gas_station`/`parcel_station`）
可以继续用，只需把枚举值改成对应的中文标签（「家」「办公室」「公园」……），或保持英文（`kind: "convenience_store"`）——
只要商品的 `store_kinds` 也用同样的词，就能匹配。


<a name="proposal-envdataset-actions-file"></a>
### 1.2.18 `actions.json` 废弃，并入 `content-catalog.jsonl`（裁决 D22）

#### 混淆的来源：两个不同的「动作」

| | 是什么 | 需不需要文件 |
| --- | --- | --- |
| **作者要的七动作** | `walk`/`drive`/`take_taxi`/`take_bus`/`purchase`/`sleep`/`interact`——**引擎固定词表** | **不需要**。它是请求 `action` 字段的闭集，写死在引擎里 |
| **`actions.json` 今天装的** | `刷推荐`、`办公`、`跑步`、`学课程`、`语音通话`——**数据集定义的具体活动**，每条带 `category`/`app_ids`/`manner` | 这是**另一个概念**，不是动作类型 |

**决定性证据**：作者最初列的八文件包里**没有 actions 文件**（地点类型、app、地点、商品、内容、回调、persona、config）。
`actions.json` 是现存六文件的遗留物，从未进入作者的设计。

#### 裁决

`actions.json` **废弃**，其内容并入 `content-catalog.jsonl`——因为在新设计里，那些具体活动**就是内容**。

#### 三个承重字段的归宿

`Action` 上有三个字段真正影响行为，不能跟着文件一起丢：

| 字段 | 谁在消费（`file:line`） | 新归属 |
| --- | --- | --- |
| **`category`** | 体力系数 `exchange/constants.py:20-24`（娱乐 1.0 / 工作学习 1.5 / 社交 1.5,1.3 / 运动 5.0 / 日常 2.0）；运动地点门禁 `life/rules.py:53-61`；推送资格 `life/app_push.py:25`、`:62`；工作内容累计 `catalog/admission.py:232` | **移到 `Content` 条目上** |
| **`manner`** | 推送资格（只有「娱乐 + `phone_continuous`」可被推送）；`offline_assisted` 触点记录 | **移到 `Content` 条目上** |
| **`communication_mode`** | 社交动作的语音/文字：`catalog/admission.py:122`、`life/activity_validation.py:42` | **删除**（裁决 D23）——社交路径已无 |

#### `Content` 的字段变化

```python
# before
class Content(DeepFrozenModel):
    content_id: NonEmptyString
    action_id: NonEmptyString          # 指向 Action；新设计下消失
    title: NonEmptyString
    summary: str
    summary_source: SummarySource
    duration_seconds: StrictInt
    app_ids: tuple[NonEmptyString, ...]
    series: ContentSeries | None = None

# after
class Content(DeepFrozenModel):
    content_id: NonEmptyString
    title: NonEmptyString
    category: ActionCategory           # 从 Action 移入（承重：体力/门禁/推送）
    manner: AppUseManner | None        # 从 Action 移入（承重：推送资格）
    summary: str
    summary_source: SummarySource
    duration_seconds: StrictInt
    app_ids: tuple[NonEmptyString, ...] = ()        # 绑定 app 的内容（看剧、游戏）
    location_kinds: tuple[NonEmptyString, ...] = () # 绑定地点的内容（工作、跑步）
    series: ContentSeries | None = None
    # action_id 消失；communication_mode 消失
```

#### 引擎判定的等价改写

「娱乐 + 手机」这类判定改为**读内容自身字段**，语义不变：

| 判定 | 现状（经 Action） | 新形态（直接读 Content） |
| --- | --- | --- |
| 推送资格 | `catalog.actions[content.action_id]`，取 `category` 与 `manner` | 直接读 `content.category` 与 `content.manner` |
| 体力系数 | 由 `action.category` 查表 | 由 `content.category` 查表 |
| 运动门禁 | `action.category == EXERCISE` | `content.category == EXERCISE` |
| 工作内容累计 | `actions[...].category != WORK_STUDY` 即拒 | `content.category != WORK_STUDY` 即拒 |

**消费入口由绑定侧决定**：
- 内容绑 `app_ids` → 经 `interact` 以 **app** 为 target 消费（看剧、游戏）
- 内容绑 `location_kinds` → 经 `interact` 以 **地点** 为 target 消费（工作、跑步、取快递）
- 两侧都有 → 两个入口都可（是否允许见 Q26 子问题 3）

#### 涉及的文件

| 位置 | 处置 |
| --- | --- |
| `src/worldmock/catalog/models.py`（`Action`、`Content`） | `Action` 类删除；`Content` 增 `category`/`manner`/`location_kinds` |
| `src/worldmock/catalog/identity.py:18-20`（`REQUIRED_CATALOG_FILES`） | `actions.json` 移出；文件集合见 C9 |
| `src/worldmock/catalog/loader.py` | 删 actions 读取；内容索引改为按 `app_ids`/`location_kinds` |
| `src/worldmock/catalog/admission.py:132`、`:232` | 改为对 `content.category` 判定 |
| `src/worldmock/life/rules.py:29`（`self.actions`） | 删 |
| `src/worldmock/life/action_run.py:49`、`:93`、`:121` | 改为读 `content` |
| `src/worldmock/life/app_push.py:23`、`:43`、`:50`、`:61` | 改为读 `content.category`/`manner` |
| `src/worldmock/life/activity_titles.py:27`、`:36` | 标题索引改由 contents 提供 |
| `src/worldmock/life/world_query.py:110` | 改读 `content.category` |
| `src/worldmock/life/settled_text.py:152` | 改读 `content.title` |
| `src/worldmock/life/observe.py:185`、`:259` | 事件载荷里的 actions 列表删除 |
| `src/worldmock/exchange/types.py:59-63`（`CommunicationMode`） | 删除 |
