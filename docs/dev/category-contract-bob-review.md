# 品类契约独立 Bob 审查记录

> 唯一规范：`docs/dev/SPEC-category-contract.md`。
> 状态：独立复审完成；136 项有效语义突变验证通过，0 survived。
> 规范 SHA-256：`5b369e385fc3af1dd3852a737b9b295fa28d361a053476a7f3e591e726cd5146`。

三个独立审查 agent 分别负责基础契约、维护发布、真实运行。实现者与审查者分开；
每次审查从干净提交创建 detached 工作树，先固定测试命令并证明基线通过，再逐次修改生产行为。
每个突变之后检查完整 Git 状态和 diff，恢复原字节后才继续。未修改规格来消除存活结果。

## 首轮发现

首轮共同提交为 `796d0856ead1025a39bfbed63fb4b2e57fdfecde`。

| 审查范围 | 基线测试 | killed | survived | invalid | inconclusive |
| --- | --- | --- | --- | --- | --- |
| 基础契约 | 99 passed | 37 | 12 | 0 | 0 |
| 维护、发布与身份摘要 | 59 passed | 21 | 13 | 0 | 2 |
| 真实结算、库存转换与运行身份 | 92 passed | 27 | 10 | 1 | 0 |

存活结果暴露测试判据缺口，包括非单位系数、双端不可比配对、逐项摘要依赖、版本边界和真实提交失败。
不确定的版本用例原本因其他错误失败，已改为其他输入与真实场景都合法的独立反例。
唯一 invalid 是删去重复付款角色守卫后，另一层仍正确拒绝；没有把错误文案变化计作 killed。

直接发现并修复的实现问题包括：默认 Decimal 精度丢失低位、人工审核可缺授权、
公开目录覆盖竞争方空目录、合同不作用在启动阶段被错误拒绝、微量配方归零或不足输入放行，
以及配对系数小于一时用未缩放数量检查库存。
扩大真实状态断言还发现通用拒绝情绪反应改变不作用动作的身体状态，现已纳入统一受理路径的回归。

## 修复后的独立复审

| 审查范围 | 被审提交 | 恢复后测试 | 结果 |
| --- | --- | --- | --- |
| 基础契约 | `979997a38c1fe48edbb44928490e273ccf2a1a72` | 283 passed | 原 49 个语义及 5 个新增回归全部 killed；0 survived、invalid、inconclusive |
| 维护、发布与身份摘要 | `979997a38c1fe48edbb44928490e273ccf2a1a72` | 91 passed | 原 36 个语义及真实覆盖竞争回归全部 killed；0 survived、invalid、inconclusive |
| 真实运行 | `ffb89bf4e08d0c6cae4af5d37bff44f4746b85b7` | 114 passed | 原 37 个有效语义与 7 个新增回归 killed；0 survived；原冗余守卫与不完整夹具各记 invalid |
| 完成阶段配对复查 | `5bd851186e8124b19d5a1786ea4c858be338ef4e` | 115 passed | 补强完整夹具后，原补丁逐字节重放并有效 killed；0 survived、invalid、inconclusive |

真实运行复审中，完成阶段复查突变最初只触发缺少资源字段的 `AttributeError`，不能计 killed。
补强用例以真实资源定义、合法余额、可执行规则和双侧库存转换的成功对照证明夹具完整。
相同补丁重放后，三个负例都因本应抛出的 `BusinessRuleError` 没有发生而失败，没有导入或夹具异常。
补丁 SHA-256 为 `f7f64968e93863cbea900c9a9ef28a8fa89932ab545361a846707be805f7c907`。

三个范围合计 **136 项有效语义突变验证通过，0 survived**；其中真实运行为原 37 个及新增 8 个。
原冗余付款守卫仍按 invalid 保留，不计入有效数量；历史不完整夹具的 invalid 也保留原记录，另记修复复验。
基础契约与维护发布复审后，对应生产和测试文件均未变；最后一个提交只补强完成阶段测试夹具。
冻结规格的 SHA-256 在所有轮次保持一致。

精度回归以默认 Decimal 上下文中的错误金额断言判定，不以刻意收紧环境后发生异常充数。
原子发布回归在最后重命名前创建真实竞争空目录，以目录未被覆盖的断言判定，
不以平台调用参数的 mock 断言替代真实文件系统行为。
Linux 分支有调用参数测试，当前没有 Linux 主机上的文件系统实测；本机原生边界验证使用 macOS。

## 可独立读取的原始证据

下列 gzip 压缩 JSON 内保存独立报告、预声明命令、准确生产补丁、原始输出、探针、
提交及恢复证据。每个原始文件带 SHA-256；JSON 键中的 `/tmp` 路径保留采集身份，
正文已经归档，不依赖临时目录仍然存在。可用标准 `gzip -dc` 读取。

| 审查 | 首轮证据 | 修复复审证据 |
| --- | --- | --- |
| 基础契约 | [原始包](evidence/category-contract/foundation-initial.json.gz) | [复审包](evidence/category-contract/foundation-recheck.json.gz) |
| 维护发布 | [原始包](evidence/category-contract/maintenance-initial.json.gz) | [复审包](evidence/category-contract/maintenance-recheck.json.gz) |
| 真实运行 | [原始包](evidence/category-contract/runtime-initial.json.gz) | [复审与完成阶段复验包](evidence/category-contract/runtime-recheck.json.gz) |

首轮基础与维护工作树的普通删除被 Git 子模块限制拒绝，按审查技能保留了干净路径
`/tmp/worldmock-bob-foundation.AkQHtH` 与 `/tmp/worldmock-bob-maintenance.iW4hCz`，没有强制删除。
之后的独立复审工作树在首次清理前把自行创建的子模块克隆移入证据目录，再正常移除工作树。
各轮报告保存调用方与临时工作树的最终状态；不将清理尝试等同为已清理。

本记录给出实际审查范围、正反例与逐项闭合，不用突变数量计算完整规格覆盖率。
最终工程门禁与全部验收项归 `docs/dev/category-contract-verification.md`。
