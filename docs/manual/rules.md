<a name="rules-调整世界规则与人物动机参数"></a>
# 调整世界规则与人物动机参数

> 按[动作与内容简化规格](../dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订「固定规则、目录格式与加载边界」（阶段 1）。

> 2026-10-07：按[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)同步阶段 5 的工作主题累计参数。

> 2026-10-07：按[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订「品类契约、交换规则、供给与制作边界、检查与发布品类修改、出行时长、食品来源映射说明、配置入口与生效范围、身体规则、在独立目录中调参并验证」（阶段 1）；「活动声明、单个人物的参数、人物映射表」（阶段 3）；再次修订「活动声明」（阶段 4）。

> 更新日期：2026-10-07。[工具交互规格](../dev/SPEC-life-runtime.md#tool-interaction-工具请求)中的构建价格范围、完整类别供给、
> 正式食材配方和发布身份已实现；当前场景结构 8、品类契约与规则绑定 3，目录身份以 Dataset 环境清单为准；下班与到家锚点、卡片结构 3 见[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)，画像映射表不变。
> 2026-10-06：按[同时做两件事规格](../dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)同步「活动声明」的占用名（阶段 2）；仍带旧占用名副本的外部环境在装配时被拒绝，要由 WorldMock-Dataset 按新占用名重新发布。

人物初值与节律在[场景](scenarios.md#scenarios-生活场景)调整；对象和绑定在[环境目录](resources.md#resources-定义地点门店供给与世界素材)调整。
分类、身体、食品、出行与交换常数由 WorldMock 包固定，研究规则变化需修改其唯一定义并发布规则版本。
本页命令使用显式相对环境根，修改只影响新的运行输入。

```mermaid
flowchart LR
    W[WorldMock 固定类型与常数] --> R[按版本发布系统规则]
    S[场景初值与人物映射] --> V[校验新环境与场景]
    R --> V
    V --> E[新的真实运行]
```

<a name="rules-共享规则与可重建环境修订"></a>
## 共享规则与可重建环境修订

按[共享规则与可重建环境规格](../dev/SPEC-shared-rules-and-rebuildable-env.md)修订；验收结果独立记录。

一般活动的显示时长与受理时长共用推导，超过一天按一天；消费 60–14400 秒且越界拒绝。MET 键从活动注册表读取，效率曲线有限非负。sleep_channel 是当前声明字段，仍不驱动身体睡眠，不能靠配置该字段改变睡眠行为。

<a name="rules-配置入口与生效范围"></a>
## 配置入口与生效范围

| 入口 | 可调整内容 | 生效边界 |
| --- | --- | --- |
| 人物映射文件 | 明确画像编译所用映射 | 只影响明确传参的编译 |
| 场景参数 | 身体初值、节律、工作安排、菜单 | 严格场景结构 8；不能改闭集或常数 |

固定类型、类别表、身体演化、收货效果、速度、费用、配送与地图常数归 `worldmock.exchange`，由 `RULES_VERSION = 2` 绑定。包内可供性注册表与人物映射是唯一通用规则来源，环境没有副本。规则维护修改唯一定义与测试并升版本。


<a name="rules-品类契约"></a>
## 品类契约



<a name="rules-交换规则"></a>
## 交换规则



<a name="rules-供给与制作边界"></a>
### 供给与制作边界



<a name="rules-检查与发布品类修改"></a>
### 检查与发布品类修改



<a name="rules-活动声明"></a>
## 活动声明

[包内 `affordances.toml`](../../src/worldmock/data/affordances.toml) 为版本 2，只有 `wait`、`sleep`、`travel`、`action` 四个种类行，没有家族层。
占用名固定为 `wait`、`travel`、`use_app`、`entertainment`、`work_study`、`talk`、`exercise`、`daily_care` 八项；条件占用按 `mode`、`category`、`manner` 声明，睡眠占全部八项，驾车只允许同时交谈。
环境与场景不能修改种类、占用或类别表。

| 动作类别 | 内容完成后的接续 | MET | 按实际秒数的状态效果 | 场所 |
| --- | --- | --- | --- | --- |
| `entertainment` | 同一活动内接下一条 | 1.0 | 按固定 `ENTERTAINMENT_RELIEF_PER_HOUR = 0.3` 缓解娱乐需要 | 任意 |
| `work_study` | 一条结束 | 1.5 | 无按秒效果；分配工作内容完成有一次 +0.05 情绪脉冲 | 分配内容只能在本人工作地点，其余任意 |
| `social` | 一条结束 | 无 app 1.5，有 app 1.3 | 共同有效社交分别每小时缓解 0.5、0.4，对方不在的秒数每小时 0.2 | 任意；请求必须有对象 |
| `exercise` | 一条结束 | 5.0 | 每小时情绪 +0.1 | `home`、`outdoor`、`fitness` |
| `daily_care` | 一条结束 | 2.0 | 能量消耗之外无效果 | 任意 |

| 使用方式 | 设备 | 额外占用 |
| --- | --- | --- |
| `phone_continuous` | `phone` | `use_app` |
| `other_device` | `other` | 无 |
| `offline_assisted` | `phone` | 无 |


<a name="rules-出行时长"></a>
## 出行时长



<a name="rules-动机评分"></a>
## 动机评分

内部动机评分退役，没有可调参数；人物选择来自七层请求。

<a name="rules-跨人物的通用参数"></a>
### 跨人物的通用参数

内部动机评分退役，没有可调参数；人物选择来自七层请求。


<a name="rules-单个人物的参数"></a>
### 单个人物的参数

内部动机评分退役，没有可调参数；人物选择来自七层请求。



<a name="rules-人物映射表"></a>
## 人物映射表

[引擎包的 `persona-mapping.toml`](../../src/worldmock/data/persona-mapping.toml) 当前要求 `version = 2`，
有 `big_five_center`、`world_dynamics`、`defaults` 三组。画像编译中的大五刻度是 **1–7**。

`big_five_center.center/span` 把量表分数归一化；每个 `world_dynamics` 段使用
`intercept + slope_<特质> × 归一分数`，然后按 `min/max` 限制范围并吸附到 `step` 格点。
必须完整保留以下八个段，斜率名称只能引用已有大五特质。

| 映射结果 | 编译模型允许范围 |
| --- | --- |
| `mood_pulse_amplitude` | 0–1 |
| `mood_halflife_minutes` | 10–180 分钟 |
| `social_need_growth_per_hour` | 大于 0、小于等于 0.5，每小时 |
| `conversation_relief_per_hour` | 0–1，每小时 |
| `diversity_weight`、`invitation_acceptance`、`reply_tendency`、`present_preference` | 0–1 |

以下是 `[defaults]` 和子表的声明形状，用于故事未明确提及时的编译缺省；通用默认值随引擎发布：

```toml
[defaults]
sleep_need_hours = 7.5
chronotype_midsleep = "04:00"

[defaults.time_anchors]
wake = "07:00-07:30"
leave_home = "08:00-08:30"
lunch = "12:00-13:00"
dinner = "18:30-19:30"
bedtime = "23:00-24:00"
```

这些缺省在故事没提到时补进卡片。场景人物写出 `chronotype_midsleep`、`sleep_need_hours`、`lunch_window`、`dinner_window`、
`bedtime_window`、`leave_work_window` 或 `arrive_home_window` 时，人设层改取场景值，对应的缺省只在场景没写该字段时进入人设层；`wake` 与 `leave_home`
没有场景字段，故事不提时总取这里的缺省，见[人物画像](personas.md#personas-正文与实际世界条件要一致)。
下班与到家（卡片的 `leave_work`、`arrive_home`）没有缺省：本表 `[defaults.time_anchors]` 仍只有五个键，`version` 仍为 2，目录身份不变；故事与场景都没写固定时刻时，卡片与人设层的这两项为 null。

编译默认调用 `load_mapping_config()` 读取包资源；明确的编译实验可调用
`load_mapping_config(Path("../WorldMock-Dataset/environment/my-world/persona-mapping.toml"))`，
再将所得 `config` 传给 `compile_persona`。不通过环境变量或当前目录搜索映射文件。
实际场景装配只从包读取人物映射，环境里没有副本。
当前运行时主要从编译结果接入人物卡片散文，**尚未把整份 `world_dynamics` 自动应用到世界状态**。
改变映射表可以改变编译输出，不能据此声称运行中的情绪、社交和邀约参数都已相应改变。
需要明确人物睡眠与作息时，仍在场景人物字段中填写，详见[人物画像](personas.md#personas-人物画像)。

`tastes` 只映射为卡片散文，不生成候选口味权重、注意力先验或食品价格参数；覆盖率读取登记目录的真实目标名。


<a name="rules-食品来源映射说明"></a>
## 食品来源映射说明



<a name="rules-身体规则"></a>
## 身体规则

身体演化读取 `worldmock.exchange.body.BODY_CONSTANTS`，`source_kind = worldmock:<RULES_VERSION>`。能量上限 2400 千卡、基础消耗 65 千卡/小时、饥饿与紧急阈值 800/400 千卡；食品利用效率按睡眠不足求值。场景提供体重、初始能量、初始睡眠债与初始娱乐需要。
食品每份固定热量：正餐 600 千卡、零食 200 千卡、饮品 100 千卡；没有食用秒数常数。人物仍只接收体感分级。


<a name="rules-在独立目录中调参并验证"></a>
## 在独立目录中调参并验证

复制完整环境到 `experiments/my-world/`，调整场景初值或明确的画像映射，更新场景环境钉。环境对象与绑定改变时刷新目录身份，再执行：

```bash
worldmock validate --environment experiments/my-world
worldmock validate --scenario experiments/my-world/scenarios/single-person.json
```

环境命令检查全部场景与图字节，没有语料与包规则副本；项目命令保留 AS2、依赖与覆盖率检查。规则变化需改 WorldMock 唯一定义并升 `RULES_VERSION`，不覆盖旧运行输入。
