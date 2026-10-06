# 对抗审查 r3 — 镜头：覆盖

> 审查对象：工作树 HEAD `694cb55e6b9da958521031ae689db49dc654aff2`。
> 最高权威：`docs/dev/SPEC-resource-and-rule-simplification.md`，状态 `UNFROZEN`。
> 起始规格 SHA-256：`fbb765e30c51d5e11efa18d89e946ee396eefec967b865e1f1acab6d9fb1387f`。
> 判定口径：只阻断会产生不同运行事实、无法实现的字面合同、虚假行为证据或阶段延期漏洞的问题；措辞、计数展示和格式问题不阻断。

## 冻结裁决

**不可冻结（BLOCKED）：1 项阻断。**

生产实现尚未开始不计为阻断。阶段 9 的 Windows、正式环境、其他机器和换目录回执保持“未执行”，也不计为阶段 1–8 的延期。

## 阻断发现

### `app_push` 环境事件把事件判别键与内容类型写成了同一个 `kind`

**观察事实**

- D-45 要求 LabelKit 窗口投影恰为 `{kind, app, kind_label, summary}`；这里同时存在事件种类 `kind` 与内容类型标签 `kind_label`。
- 「状态、事件、检查点与导出」明确以 `environment_event.data.kind` 作为事件判别键，却把人物可见事件写成
  `app_push{push_id, app_id, app, content_id, title, kind, summary, session_id, issued_at}`。同一个 JSON 对象不能让
  `data.kind` 同时等于 `app_push` 和 `article`、`short_video` 等内容类型。
- 这不是对既有机制的猜测。当前生产消费者普遍以 `event.data.get("kind")` 分派环境事件；例如
  `src/worldmock/evaluation/public_evidence.py::_world_environment` 和 `src/worldmock/runtime.py` 的恢复事件读取均如此。
- B-4 已为进度层使用无冲突形状：`type: "app_push"` 是记录判别键，`kind` 是内容类型。环境事件没有 `type`
  判别键，因此不能照抄这两个字段名。
- T41 只写“`app_push` 记录的 Schema 闭集”，T52 只写“窗口里 `app_push` 投影四键”；二者没有钉住源事件
  `data.kind == "app_push"` 与内容类型标签之间的映射。一个实现把 `data.kind` 写成内容类型、另一个丢掉内容类型，
  都可能各自构造出表面四键输出，却产生不同的认证事件和下游手机足迹。

**影响**

推送是本特性第一种非空公开环境事件，也是 `push_delivered`、进度层、质量报告和手机投影的共同来源。当前合同无法生成
唯一且可分派的源事件；这会直接破坏“真实 app 使用与推送消费痕迹来自认证事件”的目标，而不是字段命名的外观问题。

**最小修复**

保持既有事件机制：源事件固定 `data.kind = "app_push"`，把内容类型字段定名为 `content_kind`。D-45 的
`kind_label` 只由 `CONTENT_KIND_LABELS[data.content_kind]` 投影，`kind` 固定为 `app_push`。T41 或 T52 增加正反断言：
源事件判别键不能被内容类型覆盖，公开投影的 `kind_label` 必须来自同一源事件的 `content_kind`。现有 app 推送契约的行为变异
应覆盖“把 `data.kind` 覆写为内容类型”这一生产错误；无需新增产品行为或兼容字段。

## 66 行、75 分句与阶段核对

只读解析 `spec-rrs-变异登记` 得到 **66 行、75 个中文分号分句**；每行的变异分句、具名契约点和
`earliest_runnable_killer_stage` 数量完全相等。所有契约点存在，分句阶段都等于行为所有者阶段；唯一
`earliest_runnable_killer_stage != owner_stage` 的分句是“恢复时不核对 `environment_identity`”，正确登记为 owner 1、
killer 5、T16。

第 2 轮后新增或拆开的覆盖点已全部落表：跨 app 陈旧位置、重复轮数或释放、SHA-256 完整摘要或小端种子、系统层第 2 条、
睡眠允许集合、最终足迹唤醒词表、饮品常数、分段进食、购买 `app_id` 与 `content_consumed`。其中核心新增项的关系如下：

| 行为 | 生产边界 | 所有者阶段 | 最早杀手 |
| --- | --- | --- | --- |
| 跨 app 部分推进后恢复旧活动 | `ContentProgress` 合并与恢复 | 4 | T34 / 阶段 4 |
| 跨 app 完成后不重复轮数或释放 | 内容完成与推送释放 | 4 | T35 / 阶段 4 |
| 摘要截取与大端整数种子 | B-30 随机流构造 | 4 | T38 / 阶段 4 |
| 系统层第 2 条不再宣传 `submit_work` | `LIFE_SYSTEM_LAYER` 与实际请求 | 5 | T47 / 阶段 5；T49 仅作阶段 6 全文复验 |
| `app-usage.jsonl` 来自真实内容推进 | 阶段 7 事件投影 | 7 | T52 / 阶段 7 |

除上面的 `app_push` 源事件字段冲突外，没有发现把阶段 1–7 行为抬到阶段 8 才证明的分句，也没有以 T56 结构守卫替代这些
行为杀手。

## T15、T16 与集成波次

当前修订没有伪造早期引擎：

- T15 在阶段 1 只用 `WorldState.model_validate` 证明结构 9 拒绝；真实打开、恢复与重放入口在阶段 5 的
  `tests/integration/test_rrs_engine_wave.py` 证明。交付拒绝只由阶段 7 的 T54 证明。
- T16 的运行清单形状可由阶段 1 纯节点检查；删除 `runtime._prepare_resume` 身份核对调用的变异只能在阶段 5 通过真实恢复入口
  杀死。当前源码也证实 `_prepare_resume` 在恢复分支打开存储、取检查点后调用身份核对，因此直接测 helper 不能替代该入口。
- `STAGES_DONE` 只接受 `∅ → {1,2,3,4,5} → {1…6} → {1…7} → {1…8}`。阶段 5 关闭前必须先让 owner 1–5
  的全部到期基线通过，再逐项杀死全部到期变异；阶段 6–8 分别只在自己的分句到期后关闭。纯节点可在物理所有者阶段真实运行，
  但不能以部分 `STAGES_DONE` 宣称波次已经完成。

这一模型允许阶段 1–4 的真实纯门禁提前运行，也保证任何尚需完整 `LifeEngine` 的证明最迟在阶段 5 完成；未发现阶段 8
补做早期行为的机器漏洞。

## 原第 1 轮 11 项覆盖发现的关闭映射

| 原发现 | r3 判定 | 关闭依据 |
| --- | --- | --- |
| 覆盖-B1 阶段、删除、夹具与台账互相矛盾 | 关闭 | B-33、B-36、B-37、B-41；T15/T16 的纯节点与真实入口分开，图查询归阶段 2 |
| 覆盖-B2 唤醒与预排删除过早 | 关闭 | 阶段 3 的 24、阶段 4 的 25、阶段 5 的最终 22 及 T36/T39 |
| 覆盖-B3 拒绝码无唯一定义 | 关闭 | 注册码表、活动受理拒绝码表、B-14/B-28 与 T4/T24/T32 |
| 覆盖-B4 身体常数未定稿 | 关闭 | 唯一 `exchange` 常数表、T1/T2/T5 与进食复验 T27 |
| 覆盖-B5 人工环境无法构造拒绝分支 | 关闭 | 只在无 app 便利店出售的 `rice-ball`，由 T24/T32 构造 |
| 覆盖-B6 指纹阶段与真实绑定不符 | 关闭 | B-19/B-39：app Schema 阶段 4，工具和工作 Schema 阶段 5，剩余提示阶段 6 |
| 覆盖-B7 `WAYS` 漏保留方式 | 关闭 | 16 个方式、完整动作映射与 T11/T42 |
| 覆盖-B8 未登记对旧冻结裁决的取代 | 关闭 | B-22/B-23 与对应原位文档、T40/T43/T49 |
| 覆盖-B9 非食品用品被静默删除 | 关闭 | B-26 明示最终目录闭集对旧提案句的取代 |
| 覆盖-B10 `buyer` 显式账户冲突 | 关闭 | B-24 与场景 7 统一为人物余额隐式账户 |
| 覆盖-B11 数字域矛盾 | 关闭 | B-25 与登记错误表区分正整数、有限正数、非负整数和任意整数种子 |

第 2 轮的五项阻断也均已关闭：B-35 + T34/T35 覆盖共享进度，B-30 + T38 覆盖摘要字节与端序，B-19/B-39 修复
指纹阶段，B-36 把图查询归阶段 2，`evaluate_food_energy` 已归阶段 1 并由 T2 杀死。本轮阻断是新的输出协议冲突。

## 核验命令与结果

| 核验 | 结果 |
| --- | --- |
| `git rev-parse HEAD` | `694cb55e6b9da958521031ae689db49dc654aff2` |
| `shasum -a 256 docs/dev/SPEC-resource-and-rule-simplification.md` | 起始与报告落盘前均为 `fbb765e30c51d5e11efa18d89e946ee396eefec967b865e1f1acab6d9fb1387f` |
| 只读表解析 | 变异 66 行、75 分句；分句/契约/阶段长度不匹配 0；owner 与 killer 不同仅 T16 的 1 → 5 |
| 只读源路径核对 | `runtime._prepare_resume` 的恢复调用存在；`EngineContext.validate` 是完整引擎构造链；环境事件消费者按 `data.kind` 分派 |

本轮只新增本报告，没有修改规格、生产代码、测试、配置或 Git 历史。

## 定向复核：`app_push` 事件判别键

定向复核规格 SHA-256 起始值：`2ce047d3b648def790eabdaac4d8024894e6821129d273376bddcc361ba6ed05`。

**上一轮唯一阻断已关闭；覆盖镜头可以冻结。**

- D-45 固定公开投影为 `{kind: "app_push", app, kind_label, summary}`，并规定
  `kind_label = CONTENT_KIND_LABELS[source.content_kind]`。
- B-43 与环境事件合同固定人物可见源事件的 `data.kind = "app_push"`，内容类型独立写入
  `data.content_kind`；同时明确不改其他事件、进度记录和结果载荷的既有 `kind` 机制。
- T41 在行为所有者阶段验证真实人物可见事件的判别键与 `content_kind` 闭集；T52 从同一条认证源事件验证
  LabelKit 四键投影，并验证 `pushed: false` 的系统结算不产生公开投影。
- 变异登记加入“环境事件把 `data.kind` 覆写为 `content_kind`”，绑定阶段 4 的「推送与消费关联」，由 T41、T52
  杀死；它验证的是实际源事件到下游投影的关系，不是结构守卫替代行为证据。

定向范围内没有残留双重用途、投影来源分叉或证据延期。未重新开展全量、格式或其他行为审查。

定向复核规格 SHA-256 结束值：`2ce047d3b648def790eabdaac4d8024894e6821129d273376bddcc361ba6ed05`。
