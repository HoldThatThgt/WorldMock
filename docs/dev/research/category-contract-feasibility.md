# 品类契约业界依据与可行性

> 核对日期：2026-09-20。依据只支持所列机制，不构成 WorldMock 已实现的证据。

| 来源 | 支持的机制 | 适用边界 |
| --- | --- | --- |
| [GS1：How GPC works](https://www.gs1.org/standards/gpc/how-gpc-works) | 产品层次与品类属性分离 | 分类标准不能证明经济交换公式或成本下降倍数 |
| [GS1：GPC and GDSN](https://ref.gs1.org/architecture/findings/gpc-gdsn-analysis/) | 区分属于商品的属性与属于分类的属性 | 不能直接照搬所有商品标准字段 |
| [Cataclysm: DDA：JSON Inheritance](https://github.com/CleverRaven/Cataclysm-DDA/blob/master/doc/JSON/JSON_INHERITANCE.md) | copy-from、relative、proportional 的数据继承 | 官方明确不同类型支持有差异；WorldMock 必须自行冻结冲突与类型语义 |
| [Cataclysm: DDA：JSON Interface](https://docs.cataclysmdda.org/c%2B%2B/JSON_INTERFACE.html) | 先复制父值、再应用差异的可执行加载机制 | 不引入游戏数据系统，也不照搬宽松缺省 |
| [OPA：Policy Language](https://www.openpolicyagent.org/docs/policy-language) | 未定义结果与显式结果能够分开建模 | OPA 的默认值和内置函数错误可被视为 false；本特性明确禁止照搬此宽松处理 |
| [OCI：Descriptor](https://specs.opencontainers.org/image-spec/descriptor/?v=v1.1.1) | 用字节内容摘要识别并验证冻结产物 | 只借用内容身份，不引入容器运行依赖 |
| [Python：Decimal](https://docs.python.org/3/library/decimal.html) | 局部计算上下文与精确十进制运算 | 默认精度只有 28 位；须按已接受操作数设置精度并验证取整，不能把使用 Decimal 等同于天然精确 |
| [Linux：rename](https://man7.org/linux/man-pages/man2/rename.2.html)、[Apple：排他重命名能力](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsexclusiverenaming) | 系统原子且不覆盖目标的目录重命名 | 文件锁和先检查再普通 rename 不满足竞争安全；文件系统不支持时明确失败，禁止普通 rename 替代 |

这些资料支持部件机制的可行性；品类配对的偏序优先级、作用域守恒与库存转换是结合本仓库约束作出的设计推导。
它们仍需通过严格模型、资源账本、实际动作与反例测试验证，不能由资料类比替代。

目录发布使用标准库调用系统的排他重命名原语：macOS 的 `renamex_np(RENAME_EXCL)`，
Linux 的 `renameat2(RENAME_NOREPLACE)`。macOS 声明和标志同时核对了本机 SDK 的 `sys/stdio.h`。
当前依赖中的 filelock 只负责协作锁；portalocker 的文件原子写入不提供目录排他公开，因此不复用它们完成此边界。
本机 macOS 测试在最终系统调用前创建竞争方空目录，验证发布被拒绝且原目录 inode 保持不变；
Linux 调用参数另有测试，当前验证不将其等同于 Linux 文件系统实测。

## 排除与收窄

RxNorm/RxClass 的身份归一化和分类能力不足以证明“相互作用断言天然继承于类、新增成分只改一个节点”。
[NLM 官方 FAQ](https://lhncbc-portal.lhcaws-prod-pub.nlm.nih.gov/RxNav/information/FAQs.html)
明确 RxNav 的药物相互作用功能已于 2024-01-02 停止。删除该项作为本实现的理论依据。
未验证的“属性替代上百品类”“降低若干量级”不保留为可量化结论。
投入产出矩阵和实体组件系统只提供宽泛类比，不作为本次继承、稀疏性或守恒实现的证明。

## 实施前差距与验证目标

下表锚定实施前提交 `426af3bfe3d7e507b749f286e08857e697c2ac2f`，用于说明需要改变的入口。
已经删除的常量、解析器和旧对象格式不代表当前实现仍然存在。

| 差距 | 基线入口 | 所需可观察证据 |
| --- | --- | --- |
| 对象重复规则 | `src/worldmock/catalog/models.py` 的 ExchangeObject、`catalog/objects.jsonl` | 163 个现有对象改为增量声明且运行价格、能量和合同语义保持一致 |
| 密度窗归属错误 | `scripts/build_catalog.py` 的 FOOD2_CATEGORY_DENSITY_WINDOWS | 生成器读取品类槽位范围，删除原常量表 |
| 来源覆盖不在准入 | 生成脚本 assert_full_coverage 与目录 loader | 缺失数值来源在正常加载入口被拒绝 |
| 目录发布丢字段 | `src/worldmock/catalog/builder.py` 的 _bump_minor | 新增对象后世界素材要求及品类摘要完整保留 |
| 任意公式与付款旁路 | `src/worldmock/life/semantics.py` 的表达式解析器与 work._payment | 删除解析器，真实付款使用闭集算子 |
| 运行未绑定目录字节 | `src/worldmock/life/engine.py` 的 _run_manifest/_validate_runtime | 相同版本目录内容改变后，open/resume/replay 拒绝 |

最终实现状态和验收记录归 `docs/dev/category-contract-verification.md`。
