# AS2 生活仿真开发验收记录

更新时间：2026-09-15。目标是完整实现 `agentsociety2-design.md`，完成 DeepSeek 十二周足迹、真实数据消费和真实性比较。

## 当前结论

生活、内容、足迹、恢复、数据加工和评估功能均已接入实际生产路径，当前处于真实运行、修复与最终审查阶段。
工程尚未完成最终交付：正式十二周、真实多人/恢复/反事实分支、根工程 Uncle Bob 和三会话盲评仍在执行计划内，
不能把短窗口或测试夹具当成这些结果。

```mermaid
flowchart LR
    A[修订规范与业界核验完成] --> B[全部功能接入与行为测试通过]
    B --> C[真实一天与完整数据链联调]
    C --> D[干净提交与源码突变审查]
    D --> E[DeepSeek 十二周]
    E --> F[实际盲评与真实性报告]
```

## 已有可复核证据

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 根项目测试 | 历史结果闭集、目标隔离与截断修订后353 passed，23.87秒 | `.work/verification/known-evidence-root-tests.log`；新增评估测试文件已避免同名导入冲突 |
| AS2 全量测试 | strict修订后392 passed，两个第三方弃用警告，13.17秒 | `vendor/agentsociety2/packages/agentsociety2/tests`；显式设置WORKSPACE_PATH |
| Life与运行集成 | strict修订后Life、integration、footprints共236 passed，6.94秒；其中Schema专项33项通过 | `tests/life`、`tests/integration`、`tests/footprints` |
| 原生工具与失败传播 | 动态引用闭集、完整原生工具历史、零自动重试；写盘错/空记忆返回沿实际 PersonAgent step 中止 Society | `tests/integration/test_life_agent_protocol.py` 与 AS2 专项 |
| 严格请求真实预检 | 两次真实 HTTP 请求通过：携带17工具的Life请求实际返回合法basic活动；WorkReviewer返回完整3参考与3交付结论，生产解析通过 | `.work/verification/deepseek-strict-no-ref-final-preflight.json`，完整wire摘要与schema身份留证 |
| Life 与实际语料 | 110 passed；食品、字幕、真实 TextWorld、AMI、邮件和冻结道路均验证 | `tests/life`、`tests/corpora`；当前研究报告 |
| 正式启动校验 | 49 个语料条目、11 份 Schema、冻结依赖全部通过 | `worldmock validate` |
| 生产源码约束 | 本项目文件、函数、行宽和入参数量零超限 | `.work/verification/integrated-gate-receipt.json` |
| 人格档案 | 职业、作息/消费/风险倾向实际进入原生人物提示；假设来源明示 | `tests/integration/test_persona_profile.py` |
| 真实完整一天 | run `6c687988d6154c66bc25e632769bd92c`；979事件、25活动、192次DeepSeek响应、2328066 token、69条记忆更新，引用错误零；正式导出认证通过 | `.work/verification/runtime-acceptance/day-dynamic-tools-export`；修复后动作与意图各69条通过，dreaming仍因长度限制失败 |
| 真实一小时 | run `646e5952c7ad498b8a79cd49a2fef02a`；两次 DeepSeek、9296 输入与344输出 token、16事件、1个睡眠活动、2条记忆 | `runs/deepseek-hour-source-enum` |
| 一小时完整导出 | 到01:00终点、睡眠正确截断、最终共同检查点、逐账户账本与全部素材通过；记忆引用问题为零 | 该运行 `exports/final/manifest.json`、`quality.json` |
| 真实小时语义加工 | action 1、intent 1、dreaming 3 均由 DeepSeek 生成且独立 verify 通过 | 该运行 `delivery/labelkit/out`；规则阶段随后因 provider 响应格式错误而原子失败 |
| 真实消费者独立穿刺 | 作者夹具5条训练记录、DatasetDict保存重载一致、规则4分支11事件、独立预期全部通过、61文件哈希一致 | `.work/verification/full-delivery-authored-fixture/success-receipt.json`，明确不是生活模拟 |
| 目标隔离 | 早期canary检查通过，但真实day暴露32/32 judge请求携带未来目标IDs；完整身份隔离已修复并经实际LabelKit请求测试，旧canary证据不能作为完整通过结论 | `.work/verification/delivery-known-result-regression.json`、`test_labelkit_process.py` |
| 评估工具 | 49项通过；读取旧数据16320事件、4380活动、84天；公开对象状态、字段覆盖、匿名引用与可判定性约束均已接入 | `tests/evaluation`、`docs/research/realism-evaluation.md` |
| LabelKit 请求修复 | 相关765项通过；隔离 Bob 的总截止与非对象响应两项突变均 killed，恢复基线通过 | `.work/verification/labelkit-bob/bob-report.md` |
| 真实小时规则消费者 | 第三次交付中语义5条及规则4分支11事件通过；实际允许结果为 true/false/false/false | `.work/verification/deepseek-hour-real-rules.json` |
| 训练 JSON 精度 | Datasets 默认解码改变0.3；固定官方 `Json(decode=False)` 后5条真实记录逐项及保存重载一致，相关7项测试通过 | `.work/verification/deepseek-hour-hf-lossless/receipt.json` |
| 数据加工完整门禁 | 30 passed，8.81秒；覆盖真实LabelKit CLI返工、未来目标隔离、规则与训练消费者 | `tests/delivery` |
| 旧工程清理 | 旧手机生产文件、测试、配置、experiments和.runtime删除，旧入口移除 | `docs/history/mobile-prototype-lessons.md`；清理清单位于`.work/verification` |

## 最新真实运行问题

源码 `5514e5299cdd4acdc778cfe14a29f051ab8733dda242d56671c9f2a735d9a1d0` 的真实一天运行
`runs/deepseek-day-acceptance` 在08:59:44停止，实际66次响应、468708输入和19097输出token。
最后一次决策未提交生活动作便调用finish，被协议拒绝。当前已将finish限定为实际提交行动后开放，
同时撤下生活动作工具；显式只读查询保留独立结束合同，47项相关测试通过。新一天运行
`runs/deepseek-day-dynamic-tools` 绑定源码 `827a875d2ace77fc3a690cecad946c83212f5038acfb338b444421ff8b5178cd`，已完整执行到一天终点；
本次8次工作不存在或不可见的业务拒绝和所有原始模型证据保留，不代选行动或改写失败记录。
正式场景和当前一天场景均已通过启动校验；LabelKit生成与评审使用官方已核验的一百万token容量，保留原有预算门控。

`runs/deepseek-day-preflight` 已实际推进至 2026-01-05 07:28:38，保存52事件、10次模型请求/响应和4个共同检查点。
人物完成睡眠、步行通勤及途中继续活动，然后提出没有 `target_id` 的工作请求，原生协议严格中止。
Pydantic 的模型级条件现已进入实际工具 JSON Schema，并经18项原生 Schema 与运行校验一致性测试。
新运行 `runs/deepseek-day-native-schema` 到08:01后在出行请求遗漏 `planned_duration_seconds`，该字段本已在
Schema 中必填；普通工具输出仍可能违反 Schema。现已将全部原生工具改成直接满足官方 beta 子集的明确接口，
嵌套结构使用同一Pydantic子模型直接内联。最终一次预检的两次HTTP请求均通过，wire确认所有函数strict、
required、thinking disabled、无ref；不修改失败运行原始数据，也不把失败分支导出为完整交付。

中间一次工作验收的 `$defs` 请求出现嵌套字段违约，后续 `$def` 单次请求合规；样本不足以证明服务根因。
最终正式结构不依赖这些引用形式，并保留全部本地证据校验。该预检验证请求合同，不等于十七种工具均已实际执行。

小时交付的规则阶段已确认是外部请求等待：一次请求耗时629587毫秒，超过配置的180秒。HTTPX 的阶段空闲
超时不会限制持续收到字节时的总墙钟时间。请求最终以非对象 JSON 返回，触发格式错误，LabelKit 保留失败
报告且不发布成品。LabelKit worktree 已补充单次请求总截止与明确的响应类型校验，并完成两项独立 Bob。

第二次小时交付的普通反事实错误声称已经执行环境分支，独立评审正确拒绝。现已明确 observed/resimulated
事实边界并使用 LabelKit 已有的两轮语义返工，每次仍独立复评，所有请求均不含未来目标。
第三次小时交付完成语义与规则阶段后因 Dataset 默认 JSON 浮点解码改变原值失败；生产加载器已使用官方
关闭自动解码功能，另行验证这五条真实记录无损。三次失败目录均保留原始 failure.json，未补造最终清单。

较早的小时失败包括缺少原生工具历史、记忆摘要函数缺失、活动来源断链和非法记忆引用，均已在源头修复并保留
失败原始证据。它们属于新工程的诊断历史，不构成通过结果；旧手机测试也不计入新工程门禁。

## 当前身份与可重复命令

运行源码摘要在 `.work/verification/integrated-gate-receipt.json`；已通过的一小时单独绑定其实际源码与场景摘要，
后续修复不会改写旧收据。AS2 维护分支本地提交 `0ccc476456ed6192af85ed98e94ec757c8669530`，LabelKit固定
`6b8a8ba48977b70e5593ae800ceeba7a67e54503`。根仓库为本次新建的本地 `main`，以干净提交作为隔离审查基准；准确提交身份见 Git 历史与 Bob 执行收据。

```sh
.venv/bin/python -m pytest -q tests
(cd vendor/agentsociety2 && WORKSPACE_PATH=/Users/atishoo/Project/WorldMock ../../.venv/bin/python -m pytest -q packages/agentsociety2/tests)
.venv/bin/worldmock validate --scenario scenarios/white-collar-12-weeks.json
```

AS2 与 LabelKit 使用各自冻结环境，原因是实际 protobuf 依赖范围互斥。凭据只存在用户私密文件，最近扫描747个
工程/材料文件且无实际密钥匹配，范围见 `.work/verification/secret-scan.json`。
旧 AS2 `agentsociety` 命令已在重新 `uv sync --frozen` 后确认移除，重复 `.venv-as2` 环境已删除。

Uncle Bob 计划位于 `docs/research/bob-mutation-plan.md`。正式执行必须使用可核验的干净提交和隔离 worktree，
不得把源码存在、普通绿色测试或尚未执行的突变计划称为最终验收。

## 完整一天后的修正

动作加工读取69条输入，32条通过、37条拒绝。实际审计证明越界引用均为同一人物已见的历史
`action_result`，不是未来事件或伪造标识；窗口未将观察内明确引用的历史结果纳入原始事实集合，现已修复。
原始拒绝与trace保留在该day的 `delivery/labelkit`，没有发布最终交付清单。

终点截断的独立原因字段已补为 `experiment_end`，覆盖主动和暂停活动、原动作终态与导出质量。
测试辅助模块改用明确包路径，解决显式组合收集时同名conftest互相遮蔽；原先失败的组合命令
已得到351 passed，18.61秒，日志位于`.work/verification/qualified-test-imports.log`。
两次Bob基线收集失败均保留，尚未执行任何根项目源码突变；原始临时worktree已清理且Git登记已移除。

实际day的32次judge请求均含未来目标事件ID，来源是语义后处理提前复制了完整provenance。
该缺陷已纳入当前修复：LabelKit各阶段仅使用可见来源，公开窗口身份也不由未来目标派生；
审核通过后才组装真实目标与目标来源。原始trace保留，旧canary证据的适用范围已收窄。

当前生产已冻结在 `852fe71baeae9954455a9f91f85da8ff3c30b05ded3498ea61b97c5e8e3312a5`。
根测试353项通过；相关delivery、footprints与life export共125项通过。
`.work/verification/delivery-known-result-regression.json` 证明69个原始动作输出重关联通过，
117次已见历史引用均有直接canonical事实，未来和私有候选均未进入允许集合；该回归没有新模型调用。
LabelKit生成、评审与返工现在都只收到当前可见来源和当前知识截止；真实目标及来源在通过后回填。

新交付 `runs/deepseek-day-dynamic-tools/delivery-known-evidence` 的动作与意图均为69条输入、69条通过、零失败。
Dreaming的三条输入全部失败：一条生成达到8192输出token上限，两条在复核阶段超过输入预算。
原始失败报告保留；规则消费者、训练保存重载和最终交付清单未完成。正在按真实请求定位长度膨胀来源。
真实多人一天 `runs/deepseek-multi-acceptance` 正在同一冻结源码上执行；尚不声明多人验收通过。
