> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/category-contract-files.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 品类契约逐文件修改清单

基线：`426af3bfe3d7e507b749f286e08857e697c2ac2f`。采集时 HEAD：`cb446d23d4651b9707b7c488eb502a8f59da587c`。
采集时刻：`2026-09-20 09:20:48 UTC`。采集目录：`/tmp/worldmock-category-fixes.AWRXZw`。
本表按基线到当时工作树的真实 Git 差异生成，包含已暂存与未暂存内容及本表自身；采集时未跟踪文件集合为空。
合入主分支前的最终特性提交 `02a54ddba5625b50284519f2da1333bc461656c2` 具有相同的路径与状态集合。
本表的范围固定为该特性提交，不包含主分支随后合入的决策上下文功能或合并验证文档。

Git 状态按采集结果原样记录：`A` 为新增，`M` 为修改，`D` 为删除。
清单只说明文件修改范围与责任，不单独断言实现或门禁通过。

| 文件状态 | 数量 |
| --- | --- |
| 新增 `A` | 46 |
| 修改 `M` | 64 |
| 删除 `D` | 2 |
| 实际文件合计 | 112 |

按路径排序后的原始状态与路径集合 SHA-256：`5d7c4a970bcacebe6d6f406b04688e9fea28cfdcdbb018d24f034410f51d4683`。

## 对照命令

以下命令按冻结提交复核表内路径、状态及文件集合摘要，不依赖当前工作树。
采集时另执行的 `git ls-files --others --exclude-standard` 输出为空。
摘要不包含文件内容。

```sh
git diff --name-status 426af3bfe3d7e507b749f286e08857e697c2ac2f 02a54ddba5625b50284519f2da1333bc461656c2
git diff --name-status 426af3bfe3d7e507b749f286e08857e697c2ac2f 02a54ddba5625b50284519f2da1333bc461656c2 | LC_ALL=C sort -k2,2 | shasum -a 256
```

已核查而未发生修改的 `src/worldmock/footprints/`、`src/worldmock/delivery/` 与 `tests/footprints/`
不列作修改文件。此处不把冻结规格中的责任范围当作 Git 差异。

## 冻结目录与场景

| 路径 | Git 状态 | 功能责任 |
| --- | --- | --- |
| `catalog/README.md` | `M` | 说明原生对象格式、冻结目录职责与再生成边界。 |
| `catalog/category-contract.json` | `A` | 发布品类树、继承槽位、默认值、稀疏配对及来源审核。 |
| `catalog/exchange-rules.json` | `A` | 发布品类引用的闭集交换算子及来源审核。 |
| `catalog/manifest.json` | `M` | 将目录版本提升到 3.0 并绑定契约、规则和完整内容摘要。 |
| `catalog/objects.jsonl` | `M` | 将全部现有对象重生成为身份、品类和属性增量声明。 |
| `catalog/resources.jsonl` | `M` | 声明库存数量的资源类型、单位与守恒域。 |
| `catalog/rules/travel-fare.toml` | `D` | 删除过时文本票价公式，由闭集交换规则承载。 |
| `catalog/rules/travel-time.toml` | `M` | 将出行时长分支改为有来源的数值配置。 |
| `scenarios/multi-person-half-day.json` | `M` | 绑定原生目录 3.0，并显式声明当前空配方输出映射。 |
| `scenarios/multi-person.json` | `M` | 绑定原生目录 3.0，并显式声明当前空配方输出映射。 |
| `scenarios/white-collar-12-weeks.json` | `M` | 绑定原生目录 3.0，并显式声明当前空配方输出映射。 |

## 品类契约实现

| 路径 | Git 状态 | 功能责任 |
| --- | --- | --- |
| `src/worldmock/categories/__init__.py` | `A` | 公开品类模型、解析、求值、来源与身份能力。 |
| `src/worldmock/categories/cli.py` | `A` | 注册并执行 category check、refine 和 publish 参数接口。 |
| `src/worldmock/categories/common.py` | `A` | 提供严格枚举、来源审核、数值校验和递归只读模型。 |
| `src/worldmock/categories/contract.py` | `A` | 验证品类树、槽位约束、逐层增量及对象属性解析。 |
| `src/worldmock/categories/identity.py` | `A` | 计算并原子刷新契约、规则和完整目录的内容摘要。 |
| `src/worldmock/categories/loader.py` | `A` | 从冻结 JSON 文件加载品类契约与交换规则。 |
| `src/worldmock/categories/maintenance.py` | `A` | 执行品类检查、细化建议、触达统计及排他原子目录发布。 |
| `src/worldmock/categories/models.py` | `A` | 定义品类节点、属性操作、三态配对和只读解析结果。 |
| `src/worldmock/categories/operators.py` | `A` | 以精确 Decimal 计算闭集金额、能量和库存缩放。 |
| `src/worldmock/categories/pairing.py` | `A` | 解析有向双端继承配对，保留唯一最具体声明并拒绝歧义。 |
| `src/worldmock/categories/provenance.py` | `A` | 按 JSON Pointer 验证静态数值来源的恰好一次覆盖。 |
| `src/worldmock/categories/rules.py` | `A` | 定义闭集算子模型并核验动作、品类、资源单位和守恒域。 |

## 目录加载、编译与维护

| 路径 | Git 状态 | 功能责任 |
| --- | --- | --- |
| `src/worldmock/catalog/__init__.py` | `M` | 更新目录公开类型与原始对象、编译视图入口。 |
| `src/worldmock/catalog/admission.py` | `M` | 按编译边和闭集规则校验引用、角色、单位与守恒域。 |
| `src/worldmock/catalog/builder.py` | `M` | 在对象追加与退役时保留清单字段，并在失败时恢复原文件。 |
| `src/worldmock/catalog/compiler.py` | `A` | 将对象属性增量和品类规则编译为只读运行对象。 |
| `src/worldmock/catalog/inspect.py` | `M` | 按编译边与新来源格式提供资源影响和对象溯源检视。 |
| `src/worldmock/catalog/loader.py` | `M` | 校验目录内容身份，加载严格原始声明并形成冻结运行视图。 |
| `src/worldmock/catalog/models.py` | `M` | 定义原始对象、编译边与清单身份，删除旧公式和原始边格式。 |

## 生活运行、命令与恢复

| 路径 | Git 状态 | 功能责任 |
| --- | --- | --- |
| `src/worldmock/cli.py` | `M` | 将品类维护子命令接入唯一 worldmock 命令面。 |
| `src/worldmock/life/activity_evidence.py` | `A` | 集中生成活动、消费和库存转换的已提交事实证据。 |
| `src/worldmock/life/basket.py` | `M` | 分离使用资格与普通库存检查，使配方按实际缩放数量验证。 |
| `src/worldmock/life/category_contract.py` | `A` | 推导场景可触达闭包，执行配对准入和运行动作复查。 |
| `src/worldmock/life/engine.py` | `M` | 接通真实品类结算、库存配方、规则命中足迹与运行身份。 |
| `src/worldmock/life/inventory_recipe.py` | `A` | 准备并原子完成库存转换，严格拒绝不足或不可表示的数量。 |
| `src/worldmock/life/projection.py` | `M` | 在观察中显示独立持有物的目录名称。 |
| `src/worldmock/life/rules.py` | `M` | 区分可购买商品索引与全部可持有、使用对象索引。 |
| `src/worldmock/life/run_identity.py` | `A` | 统一核验新运行、打开、恢复和重放的品类内容身份。 |
| `src/worldmock/life/scenario.py` | `M` | 声明初始库存、独立持有物与配方输出绑定，并校验场景引用。 |
| `src/worldmock/life/semantics.py` | `M` | 按命名槽位装配实际对象和合同，替换任意公式并执行完整准入。 |
| `src/worldmock/life/travel_time.py` | `A` | 严格加载出行时长数值表及其来源。 |
| `src/worldmock/life/work.py` | `M` | 让合同付款只经过冻结闭集计算路径和实际验收。 |
| `src/worldmock/runtime.py` | `M` | 在恢复或重放发布分支前核验原运行的品类内容身份。 |

## 构建与门禁脚本

| 路径 | Git 状态 | 功能责任 |
| --- | --- | --- |
| `scripts/build_catalog.py` | `M` | 从冻结品类槽位和规则生成原生目录，校验数值来源与可复现性。 |
| `scripts/coverage_gate.py` | `M` | 将覆盖率产物和文档引用统一到 docs/dev。 |
| `scripts/migrate_scenarios.py` | `D` | 删除过时场景迁移脚本。 |

## 测试与回归

| 路径 | Git 状态 | 功能责任 |
| --- | --- | --- |
| `tests/catalog/conftest.py` | `M` | 将目录测试夹具改为品类契约与原始对象增量格式。 |
| `tests/catalog/test_admission.py` | `M` | 验证新目录引用、资源单位、守恒和角色准入。 |
| `tests/catalog/test_affordance_shadow.py` | `M` | 补充现有可供性解析与声明错误分支断言。 |
| `tests/catalog/test_branch_coverage.py` | `M` | 更新新目录模型、构建、加载和准入的异常分支覆盖。 |
| `tests/catalog/test_builder.py` | `M` | 验证对象追加、退役、清单保留及失败恢复。 |
| `tests/catalog/test_catalog_data.py` | `M` | 验证正式原生目录、静态参数、对象规模和再生成结果。 |
| `tests/catalog/test_category_admission.py` | `M` | 在带完整内容身份的目录上验证世界素材品类拒绝。 |
| `tests/catalog/test_inspect.py` | `M` | 验证编译资源影响与严格来源溯源查询。 |
| `tests/catalog/test_loader.py` | `M` | 验证原始与编译视图、版本链、旧格式拒绝和文件篡改拒绝。 |
| `tests/catalog/test_models.py` | `M` | 验证新原始对象字段及保留的资源与编译边约束。 |
| `tests/categories/__init__.py` | `A` | 建立品类契约专项测试包。 |
| `tests/categories/conftest.py` | `A` | 提供完整品类、规则、目录和来源的独立测试夹具。 |
| `tests/categories/test_catalog_generation.py` | `A` | 验证生成器读取品类约束、来源覆盖和原生数据再现。 |
| `tests/categories/test_catalog_integration.py` | `A` | 验证契约与目录编译、原始格式及内容身份集成。 |
| `tests/categories/test_contract.py` | `A` | 验证品类树、继承增量、槽位、来源审核和嵌套只读语义。 |
| `tests/categories/test_maintenance.py` | `A` | 验证检查、细化、触达统计、明确归属及真实场景发布。 |
| `tests/categories/test_pairing.py` | `A` | 验证方向、三态、深层覆盖与不可比较配对的歧义拒绝。 |
| `tests/categories/test_publication_atomic.py` | `A` | 验证目标竞态下的排他发布及系统重命名调用参数。 |
| `tests/categories/test_rules.py` | `A` | 验证闭集算子、精确金额取整、库存缩放与守恒反例。 |
| `tests/corpora/test_category_corpora.py` | `M` | 将语料数值对照改为真实目录编译结果与新来源记录。 |
| `tests/integration/test_category_identity.py` | `A` | 验证运行中冻结语义、打开、恢复、重放和导出的内容身份。 |
| `tests/integration/test_host_branch_coverage.py` | `M` | 为真实宿主恢复夹具补齐必需的品类内容身份。 |
| `tests/integration/test_life_agent_protocol.py` | `M` | 修复实施前既有工具 Schema 摘要失配并保留反例断言。 |
| `tests/life/test_candidates.py` | `M` | 用原生目录验证退役候选，并限定运行路径无目录删除行为。 |
| `tests/life/test_category_runtime.py` | `A` | 通过真实 LifeEngine 验证系数、命名槽位、准入和库存原子性。 |
| `tests/life/test_category_runtime_edges.py` | `A` | 覆盖库存配方准备与完成的异常和数量边界。 |
| `tests/life/test_category_semantic_edges.py` | `A` | 覆盖闭集运行语义、合同规则和出行数值表的拒绝分支。 |
| `tests/life/test_domain_branch_coverage.py` | `M` | 使领域测试适配冻结语义，并补充新真实调用路径断言。 |
| `tests/life/test_motivation.py` | `M` | 为商品夹具补齐运行视图必需的品类身份。 |
| `tests/life/test_revision_contract.py` | `M` | 按冻结语义对象更新测试实例，保留新增字段。 |
| `tests/life/test_scenario.py` | `M` | 验证原生目录版本与场景装配的拒绝和边界。 |
| `tests/life/test_semantics.py` | `M` | 验证闭集场景结算和旧任意表达式入口彻底删除。 |
| `tests/life/test_travel_time.py` | `M` | 将出行时长验证改为数值表和完整目录身份。 |
| `tests/life/test_work_contracts.py` | `M` | 让现有工作合同用例经过冻结语义的单一付款路径。 |
| `tests/life/test_work_efficiency_factors.py` | `M` | 对齐已装配身体配置的目录 3.0 来源身份。 |
| `tests/test_style_gate.py` | `M` | 删除本次已修复的生产源码风格存量豁免。 |

## 规格、设计与验证文档

| 路径 | Git 状态 | 功能责任 |
| --- | --- | --- |
| `docs/ARCHITECTURE.md` | `M` | 明确品类契约、资源、交换和运行身份的职责边界。 |
| `docs/CONTRACTS.md` | `M` | 冻结品类字段、原始对象格式、来源审核、命令与拒绝审计边界。 |
| `docs/EXPERIMENT.md` | `M` | 对齐目录版本、冻结规则和运行内容身份。 |
| `docs/INDEX.md` | `M` | 登记特性规格、实施与审查证据，并统一文档状态词表。 |
| `docs/VERIFICATION.md` | `M` | 定义品类特性完整门禁及独立语义突变要求。 |
| `docs/design/02-exchange-engine.md` | `M` | 对齐品类配对、闭集计算和实际运行入口。 |
| `docs/design/03-resource-engine.md` | `M` | 区分原始对象与编译视图，更新目录发布与身份说明。 |
| `docs/design/05-recording-engine.md` | `M` | 说明运行清单品类身份、规则命中统计和冻结导出。 |
| `docs/design/08-configuration.md` | `M` | 说明配方输出绑定及场景完整可触达准入。 |
| `docs/design/09-io-formats.md` | `M` | 将品类与原始对象格式统一引用到接口合同。 |
| `docs/dev/ANALYSIS-spec-coverage.md` | `M` | 增加品类规格与对应验证证据的追溯入口。 |
| `docs/dev/PROPOSAL-category-contract.md` | `A` | 保存提案裁决记录，明确冻结规格的唯一行为权威。 |
| `docs/dev/SPEC-category-contract.md` | `A` | 冻结品类继承、配对、算子、库存转换、发布和身份的行为规格。 |
| `docs/dev/SPEC-life-realism.md` | `M` | 区分世界素材品类覆盖与交换配对准入。 |
| `docs/dev/SPEC-life-runtime.md` | `M` | 将生活动作与品类规则接线，并移除旧公式的规范引用。 |
| `docs/dev/category-contract-bob-review.md` | `A` | 汇总独立语义突变、发现、修复和复审证据。 |
| `docs/dev/category-contract-files.md` | `A` | 逐文件记录相对实施前基线的真实修改集合与功能责任。 |
| `docs/dev/category-contract-verification.md` | `A` | 逐要求记录实现、真实运行测试与最终门禁证据。 |
| `docs/dev/coverage-baseline.md` | `M` | 更新覆盖率口径下的当前基线与证据身份。 |
| `docs/dev/coverage-report.json` | `M` | 保存整体分支覆盖率与公共接口覆盖机器报告。 |
| `docs/dev/implementation-status.md` | `M` | 更新品类特性的实现状态与整体验收边界。 |
| `docs/dev/research/category-contract-feasibility.md` | `A` | 记录第一方行业依据、排除理由及实施前差距。 |

## 独立审查原始证据

| 路径 | Git 状态 | 功能责任 |
| --- | --- | --- |
| `docs/dev/evidence/category-contract/final-gates.json.gz` | `A` | 归档最终覆盖率、目录再生成、文档检查和独立文档复核原始证据。 |
| `docs/dev/evidence/category-contract/foundation-initial.json.gz` | `A` | 归档基础契约首轮独立突变、探针与原始执行证据。 |
| `docs/dev/evidence/category-contract/foundation-recheck.json.gz` | `A` | 归档基础契约修复后独立复审及恢复检查证据。 |
| `docs/dev/evidence/category-contract/maintenance-initial.json.gz` | `A` | 归档维护发布首轮独立突变、探针与原始执行证据。 |
| `docs/dev/evidence/category-contract/maintenance-recheck.json.gz` | `A` | 归档维护发布修复后独立复审及恢复检查证据。 |
| `docs/dev/evidence/category-contract/runtime-initial.json.gz` | `A` | 归档真实运行首轮独立突变、探针与原始执行证据。 |
| `docs/dev/evidence/category-contract/runtime-recheck.json.gz` | `A` | 归档运行语义复审、完成阶段同一补丁重放及恢复检查证据。 |
