# 规格审查（第 2 轮）：冲突

- 被审文件：`docs/dev/SPEC-resource-and-rule-simplification.md`
- 工作树提交：`694cb55e6b9da958521031ae689db49dc654aff2`
- 开始 SHA-256：`48a6ba2f6f154cab8ac7e57bb02f57328dc4bf9083c5b322fcf5cf19f4c48f77`
- 结束 SHA-256：`48a6ba2f6f154cab8ac7e57bb02f57328dc4bf9083c5b322fcf5cf19f4c48f77`
- 权威顺序：`rulings-final.md` > `understand/SYNTHESIS.md` 第 3、4 节 > 早期备忘；本报告没有建议改变任何裁决。

## 冻结结论

**BLOCKED：不可冻结。阻断问题 14 项；非阻断确认 5 项。**

原冲突审查的 15 项阻断中，`B9`、`B13`、`B14` 已关闭，另外 12 项没有关闭；本轮另发现两项会让确定性实现出现两个合法解释的内部冲突。`revision-closure.md` 所写「34 项均已给出唯一修订」与当前规范正文不符。

本轮不把尚未实施的代码或尚未原位改写的现行文档本身当成阻断。阻断来自规范的阶段合同漏掉冲突锚点，或规范自身同时给出不能并存的实现规则。

## 审查方法与总证据

- 完整阅读规范、`rulings-final.md`、`understand/SYNTHESIS.md` 第 3、4 节、`critique-conflicts-r1.md` 与 `revision-closure.md`，并回到每个现行锚点核对当前正文。
- `spec-brief.md` 第 40 行要求「每个冲突的现行文档与 section (anchor)」都进入原位改写表；规范自己的 `现行文档原位改写` 也规定冲突条款在所属正文原位改写并保留锚点（规范第 683–686 行）。因此只有泛称文件或相邻章节，不能覆盖一个仍带相反要求的现行锚点。
- 对第 1 轮阻断与次要冲突列出的锚点做机械交叉检查：86 个不同锚点都在当前 `docs/**/*.md` 中恰好定义一次；规范的阶段改写表只出现 `spec-idle-fillers-系统层`、`spec-idle-fillers-班次边界同步点`、`spec-idle-fillers-班次边界情形` 三个，另外 **83 个未登记**。原 15 项阻断自身的 66 个检查项中，63 项未登记；共享的 `simulation-engine-校验与占用安排` 去重后是 62 个不同锚点未登记。
- 对现有源码和依赖入口另查到：开发环境实际由 `scripts/bootstrap.py:32` 安装 `requirements/lock.txt`，环境回执由 `scripts/bootstrap_environment.py:116-119` 记录该锁；`requirements/lock.txt:523` 与 `uv.lock:1315-1320` 当前都解析为 NetworkX 3.6.1。

## 阻断问题

### 阶段 5 与阶段 6 的系统层归属冲突（原 B1）

**矛盾。** B-19 规定阶段 5 同步删除 `_TOOL_MODELS`、`REQUEST_MODELS`、`TOOL_DESCRIPTIONS` 的 `submit_work`，阶段 6 只改「剩余系统层文本」（规范第 272 行）；T47 又要求阶段 5 已没有 `submit_work` 工具与模型（第 1042 行）。但文档阶段 6 仍把第 2、7、8、12、13、17 条全部留到阶段 6（第 866 行），阶段 6 实施表也再次拥有 `life_prompt.py` 与 `prompt_schemas.py`（第 1159 行）。第 2 条在阶段 5 和阶段 6 之间仍会宣传已经不存在的 `submit_work`。

**最小修复。** 在 B-19、阶段 5 文档表、阶段 5 实施表与测试行中明确：第 2 条和 `contracts-model-request` 随 `submit_work` 在阶段 5 改；阶段 6 只改第 7、8、12、13、17 条。阶段 6 的六条测试可保留为最终整体验证，但不能作为第 2 条的首次落地阶段。

### 并行活动 R4 的取代链与原位改写缺失（原 B2）

**矛盾。** D-50/B-22 已把 R4 第二步从工作截止改为主题上限（规范第 93、275、502 行），但原位改写总则仍只说 R5、R6 被取代（第 686 行）。现行 `SPEC-parallel-activities.md` 的 R4、`spec-parallel-activities-名额落点`、`spec-parallel-activities-测试` 仍分别写「工作截止与约束」「工作截止之后」「工作截止先于名额」（第 45、85、211 行），`simulation-engine-校验与占用安排` 仍写同一旧次序（`04-simulation-engine.md:487`）；阶段表只登记了 `spec-parallel-activities-占用声明`（规范第 820 行）。`spec-parallel-activities-名额情形` 的观看例子也未登记。

**最小修复。** 总则明确 R4/R5/R6 均由本规格取代；阶段 4/5 表逐项加入 `spec-parallel-activities-名额落点`、`spec-parallel-activities-测试`、`spec-parallel-activities-名额情形`、`simulation-engine-校验与占用安排`，按第 1 轮修复写主题上限、app 使用与已裁决的四步次序。

### 生活运行时规格的冲突锚点未进入阶段合同（原 B3）

**矛盾。** 以下 11 个锚点全未出现在规范：`spec-life-runtime-验收矩阵`、`spec-life-runtime-两条排除纪律`、`spec-life-runtime-状态归属规则`、`tool-interaction-工具请求-开始活动`、`tool-interaction-工具请求-只读查询`、`spec-life-runtime-加速`、`spec-life-runtime-结算间隔与续行`、`spec-life-runtime-不进入提示的字段`、`spec-life-runtime-社交最小可行版`、`time-and-energy-决策不耗时时间只在做事时流动-同一秒内的呈现与刚才`、`spec-life-runtime-资源表达`。这些现行节仍规定营业与工作日历（第 51 行）、工作截止/合同/任务（第 441 行）、`game_catalog`（第 826 行）、营业/报价/库存排除（第 1016 行）、主动游戏（第 1174 行）、制作方式（第 2184 行）、报价与营业查询（第 2253 行），验收矩阵仍钉字幕、轨迹与游戏（第 2654、2657、2671 行）。它们与新固定规则不能同时成立，而阶段表没有承诺改写它们。

**最小修复。** 按第 1 轮 B3 的阶段与替换语义，把这 11 个精确锚点逐行加入阶段 3、4、5、8；不要用同文件的其他节代替。

### 人物决策与画像规格的冲突锚点未进入阶段合同（原 B4）

**矛盾。** 规范漏掉 `spec-life-realism-location_kinds-的运行期强制语义`、`persona-prompt-七层请求-工具层的紧凑编码与即时查询`、`spec-life-realism-呈现与导出`、`spec-life-realism-与相邻规格的边界`、`spec-life-realism-单一行动阶段`。现行正文仍要求地点显式 `location_kinds`（第 322、328 行）、按未知报价或营业筛选（第 769 行）、观看与游戏标题（第 587 行）、商品可见期/材料/字幕边界（第 90 行）和口味权重措辞（第 151 行）。

**最小修复。** 按第 1 轮 B4 把五个锚点加入阶段 1、3、4，并分别写派生地点环境、食品类别查询、app 使用呈现、删除材料字幕边界、删除口味权重。

### 数字人交互规格的冲突锚点未进入阶段合同（原 B5）

**矛盾。** 规范漏掉 `spec-agent-interaction-会话与活动的关系`、`spec-agent-interaction-上下文分层`、`spec-agent-interaction-会话字段`、`spec-agent-interaction-即时查询完整纠错与紧凑呈现`、`spec-agent-interaction-与既有文档的一致性核对`。现行正文仍以工作截止和合同上限恢复（第 117–118 行），进度层仍含 `task_deadline`（第 578–580 行），仍隐藏报价版本并列旧内部身份（第 584–599 行），查询仍依赖浏览揭示（第 403 行），一致性清单仍要求工作评审（第 722 行）。

**最小修复。** 按第 1 轮 B5 将五个锚点逐项加入阶段 3、4、5，写入主题上限、`app_push/work_completed`、新禁键、无浏览、无工作评审的替换语义。

### 决策上下文规格的冲突锚点未进入阶段合同（原 B6）

**矛盾。** 规范漏掉 `spec-decision-context-地点发现与供给比较`、`spec-decision-context-即时查询操作备忘与紧凑结果`、`spec-decision-context-预算与失败`、`spec-decision-context-同秒续接与只读路径`。现行正文仍展示供给报价、路线预计时间、报价版本与营业/库存（第 65–74 行），同秒事件仍排除无来源 `task_deadline`（第 87 行），世界查询仍以浏览揭示和未知库存为边界（第 162 行），容量规则仍要求材料揭示与商品可见期（第 187 行）。

**最小修复。** 按第 1 轮 B6 把四个锚点加入阶段 3、5，分别改为图距离/固定食品价/app 来源、删除浏览与库存、删除材料可见期、删除 `task_deadline`。

### 交互菜单规格的冲突锚点未进入阶段合同（原 B7）

**矛盾。** 规范漏掉 `errands-workday-查询筛选与返回精简-只看此刻能做的`、`errands-workday-查询筛选与返回精简-执行位置与保留约束`、`spec-interaction-menu-验收台账`。现行正文仍按报价、营业与配送可达性筛选（第 321 行），仍写可见凭据（第 348 行），验收仍要求游戏/工作区分、私有库存和工作评审拒绝（第 362、366、367 行）。

**最小修复。** 按第 1 轮 B7 把三个锚点加入阶段 3、5、8，以固定菜单/app 绑定、新工具面和删除评审后的验收语义原位改写。

### 当日目标规格的冲突锚点未进入阶段合同（原 B8）

**矛盾。** 规范漏掉 `time-and-energy-零点计划与计划提醒-计划与提醒`、`spec-day-plan-goals-当前工具交互约束`、`spec-day-plan-goals-心智模型与范围`。现行正文仍把环境任务截止与营业变化作为决策机会（第 59 行），仍说普通工作由通知开放（第 256 行），运行阶段键仍为 `action`、`work_review`（第 443–444 行）。

**最小修复。** 按第 1 轮 B8 把三个锚点加入阶段 4、5，写 app 娱乐家族、主题自运行开始可见、阶段键只有 `action`，并删除截止与营业机会来源。

### 接口合同的冲突锚点未进入阶段合同（原 B10）

**矛盾。** 规范漏掉 `contracts-交互菜单声明与有效菜单`、`contracts-day-goal-request`、`contracts-交谈与通话`、`contracts-个人事务`。现行合同仍以地点 `location_kinds` 资格、游戏与工作验收定义菜单（第 181–215 行），娱乐家族仍是 `watch/play_game/browse`（第 292 行），交谈仍排除主动游戏且可与观看/播客并行（第 1126、1132 行），个人事务仍把制作产出算作取得（第 457 行）。

**最小修复。** 按第 1 轮 B10 将四个锚点加入阶段 2、3、4、5，写八种地点类型、新娱乐家族、无主动游戏/播客、无制作产出及无工作验收。

### 设计章节的冲突锚点未进入阶段合同（原 B11）

**矛盾。** 规范漏掉 14 个现行设计锚点：`simulation-engine-校验与占用安排`、`simulation-engine-验收重点`、`simulation-engine-调用并等待人物决策`、`recording-engine-工具纠错只读查询与当天操作备忘的证据`、`recording-engine-事件字段`、`io-formats-职责与边界`、`overview-地图中的消费行为`、`overview-非功能约束`、`architecture-状态归属与写入边界`、`exchange-engine-返回规则结果`、`resource-engine-职责约定`、`resource-engine-准入失败的处理`、`resource-engine-资源校验的实现与验收`、`evaluation-活动activitysession`。现行设计仍把工作截止、报价、库存、可见凭据、游戏与工作验收放进引擎（如 `04-simulation-engine.md:487-491`），把 `category-contract.json`/`exchange-rules.json` 当作冻结接口（`09-io-formats.md:43-44`），要求 TextWorld 放行（`00-overview.md:224`），并保留 `segments`（`07-evaluation.md:286-300`）。

**最小修复。** 按第 1 轮 B11 把 14 个精确锚点分入阶段 1–7，使用规范已有的新图、食品/app、主题和记录形状替换；不要只改同一设计文件的相邻章节。

### 手册的冲突锚点未进入阶段合同（原 B12）

**矛盾。** 规范漏掉 `rules-活动声明`、`rules-单个人物的参数`、`rules-人物映射表`、`scenarios-修改人物身体与账户`、`personas-在场景中填写人物`。现行手册仍列口味缓解、11 个占用名、游戏边界与 `attention_prior`（`rules.md:248-253`），仍要求 `daily_effective_work_seconds`（第 339、361 行），仍以口味权重接入（第 416 行），场景仍有缓解下限（`scenarios.md:165-167`），人物示例仍要求技能与资格（`personas.md:70-81`、第 121–122 行）。

**最小修复。** 按第 1 轮 B12 把五个锚点加入阶段 1、3、4、5；`rules-单个人物的参数` 必须与已正确裁决的 B-21 一起改，不能只改生产字段。

### 其他现行合同与登记锚点仍漏改（原 B15，并含第 1 轮 M3–M6）

**矛盾。** 原 B15 的六个锚点全部缺失：`verification-真实性评估与结论边界`、`verification-共享规则与可重建环境特性门禁`、`architecture-必须达到的行为`、`layering-imports`、`layering-tiers`、`shared-rules-文档原子同步清单`。其现行正文仍钉工作截止/游戏终局（`VERIFICATION.md:709-710`）、Linux x86_64 TextWorld 门禁（第 43–44 行）、职业技能合同与游戏内容（`ARCHITECTURE.md:83-86`）、`map_models`/工作材料/TextWorld（`SPEC-layering-and-test-tiers.md:73-76,191`）及 TextWorld 与旧品类规格同步（`SPEC-shared-rules-and-rebuildable-env.md:229,245`）。

第 1 轮标为次要、但冻结前同样必须进入“每个 section anchor”表的 20 个锚点也全部缺失：

- `implementation-status.md` 的 `个人事务与工作安排开发状态`、`人物-prompt-七层开发状态`、`共享规则与可重建环境开发状态`、`同时做两件事开发状态`、`规格追溯与突变守卫`、`历史静态可达性缺陷记录`、`候选特性历史门禁输出原命令与关键行`；
- `ANALYSIS-spec-coverage.md` 的 `个人事务与工作安排增量映射`、`人物-prompt-七层增量映射`、`时间与能量增量映射`；
- `experiment-实验身份与范围`、`errands-workday-作息补全与工作安排-工作安排声明`、`readme-修改配置前要知道的约定`、`shared-rules-摘要安全路径持久写入与错误`；
- `spec-dayrun-behavior-共同时间与身体结算`、`spec-dayrun-behavior-事件投影`、`time-and-energy-能量不拦人-让人物趁早吃`、`time-and-energy-会话活动与记忆提示中不出现内部身份-活动的生命周期`、`spec-life-runtime-数据进入环境的过程`、`spec-life-runtime-运行观测`。

这些不是历史版本行：例如实验范围仍说世界日历约束营业和工作机会（`EXPERIMENT.md:26`），共享规则仍把品类身份、工作材料、出行响应与文字游戏列作安全文件消费者（`SPEC-shared-rules-and-rebuildable-env.md:50-52`），一日行为仍把游戏交互和目录浏览作为边界与投影（`SPEC-dayrun-behavior.md:166,232`）。

**最小修复。** 将原 B15 六项及上述 20 个锚点按第 1 轮 B15/M3–M6 的替换语义加入阶段 1、3、4、5、8；阶段 8 的泛称“各节”不能代替这些精确锚点。

### B-30 与娱乐正文定义了两套随机种子解释

**矛盾。** B-30 唯一定义是 UTF-8 材料 → SHA-256 摘要前八字节 → 大端无符号整数 → `random.Random`，排序候选后 `randrange`（规范第 283 行），T38 也逐项钉住（第 1033 行）。娱乐正文却另写打开/接续为 `sha256(f"{random_state}:...")`，推送为 `random.Random(sha256(f"{random_state}:{scheduled_id}"))`（第 488、492 行）。后者既没有前八字节和大端整数解释，也不是 B-30 的可执行构造；实现者可合理写出不同序列。

**最小修复。** 第 488、492 行只引用 B-30 并写各自的键，或逐字复述 B-30 的字节到整数过程；删除三个简写算法，不改变 D-52/B-30 裁决。

### 地图与双跑基线绑定了非实际安装锁

**矛盾。** B-13 把 `MAP_GENERATOR_RULE_VERSION` 绑定到 `uv.lock`（规范第 266 行），B-34 的 `generation` 也只记录 `uv.lock` SHA（第 287 行）。但用户指定的环境入口 `python scripts/bootstrap.py --profile dev` 实际在 `scripts/bootstrap.py:32` 安装 `requirements/lock.txt`，环境回执也在 `scripts/bootstrap_environment.py:116-119` 记录 `requirements/lock.txt`。两个锁当前恰好都锁 NetworkX 3.6.1，不等于未来变更仍同步；实际生成器依赖可以变化而 B-13/B-34 身份不变。

**最小修复。** 以实际 bootstrap 使用的 `requirements/lock.txt` 作为 B-13、B-34、T55 与回执的依赖锁身份；若项目要求同时约束 `uv.lock`，则明确记录并校验两者，而不能只记录未被该入口安装的锁。无需新增配置层。

## 原 15 项关闭映射

| 原发现 | 本轮状态 | 证据 |
| --- | --- | --- |
| B1 | 阻断 | 第 2 条仍归阶段 6，阶段 5 已删 `submit_work` |
| B2 | 阻断 | R4 取代总则与四个原位锚点缺失 |
| B3 | 阻断 | 11/11 锚点缺失 |
| B4 | 阻断 | 5/5 锚点缺失 |
| B5 | 阻断 | 5/5 锚点缺失 |
| B6 | 阻断 | 4/4 锚点缺失 |
| B7 | 阻断 | 3/3 锚点缺失 |
| B8 | 阻断 | 3/3 锚点缺失 |
| B9 | 已关闭 | 三个精确锚点均在阶段 4/5 表（规范第 821、859 行），T10/T12 已改为 app 内容边界 |
| B10 | 阻断 | 4/4 锚点缺失 |
| B11 | 阻断 | 14/14 锚点缺失 |
| B12 | 阻断 | 5/5 锚点缺失 |
| B13 | 已关闭 | B-21 唯一规定三个字段改名并删除效率快照（第 274 行）；手册原位遗漏另计 B12 |
| B14 | 已关闭 | 阶段 6 使用真实节点 `test_progress_schema_registers_the_eight_record_types_with_both_errand_records` 并改为九种记录（第 932、1199 行），该节点在当前测试源码存在 |
| B15 | 阻断 | 原六个锚点及第 1 轮 M3–M6 的 20 个锚点均未登记 |

## 非阻断确认

- `rulings-final.md` 的 C-1…C-8 与 SYNTHESIS 第 3、4 节的优先顺序在规范第 31 行明确；本轮没有发现规范用早期备忘反向覆盖最终裁决。
- 用户后续把 C-7 改裁为当前 macOS 同场景同种子双跑，B-27 已留下覆盖原因，Windows 明确为未执行（规范第 280、1171–1174 行）；不构成冲突。
- 推送固定日期的夏令时语义由 B-32 与 T36 给出缺失小时、重复小时、`fold=0`、同 epoch 不去重的可测规则（第 285、1031 行）；按已确认范围不需要新时区数据库配置层。
- 原 B9 的等待例外锚点、同步点和 app 内容完成边界已经完整登记；不因现行文档尚未实施改写而重复报阻断。
- 原 B13 的生产字段口径与原 B14 的测试节点都已有唯一答案；这里只要求把相应手册和阶段合同同步，不重新打开裁决。

## 验证记录

- `git rev-parse HEAD` → `694cb55e6b9da958521031ae689db49dc654aff2`
- `shasum -a 256 docs/dev/SPEC-resource-and-rule-simplification.md` 在审查开始和报告写完后均为 `48a6ba2f6f154cab8ac7e57bb02f57328dc4bf9083c5b322fcf5cf19f4c48f77`
- 锚点脚本结果：`anchors 86; definitions_once 86; missing_defs 0; duplicate_defs 0`；规范出现 `3`，未出现 `83`
- 未运行实现测试：本轮是只读文档冲突审查，唯一产物为本报告。
