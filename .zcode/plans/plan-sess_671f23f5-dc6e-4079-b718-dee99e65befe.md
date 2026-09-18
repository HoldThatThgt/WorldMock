## 目标

完成文档治理特性从冻结规格到可验证实现的闭环：修订最终 SPEC 以消除内部矛盾，原子迁移文档树，建立唯一权威与证据快照，修正四项协议门禁口径，并用真实测试证明每条 MUST 条款已落地。整个范围只涉及文档、文档门禁、coverage 报告身份和必要的文档 provenance 更新，不改变 LifeEnv、registry、结算、模型或运行时行为。

## 已确认的设计依据

- OpenAPI/AsyncAPI 支持可观察接口合同与实现/架构分离。
- Kubernetes KEP、Rust RFC/ADR 支持显式状态、替代关系和可发现的决策记录。
- OpenTelemetry 支持 Development/Stable/Deprecated/Removed 等显式成熟度元数据。
- Diátaxis 支持单一 Start-here 导航入口。
- 这些行业方案支持治理形态，但不规定 WorldMock 的文件名；文件名按项目裁决。
- 活动文件采用 AGENTS.md 要求的平铺前缀：`docs/dev/SPEC-*.md`、`docs/dev/PROPOSAL-*.md`、`docs/dev/ANALYSIS-*.md`。治理 SPEC 保留现有固定路径 `docs/dev/SPEC-document-governance.md` 作为唯一例外。
- 两个 registry 提案保持非当前合同；现有 `affordances.py`/`semantics.py`/`affordances.toml` 的既有依赖被明确标为 pre-existing implementation，不把它们当成本轮新增依赖，也不回退运行时。未来扩展仍须单独 SPEC。
- 旧主稿、v2、guide、组织分析、research 和 registry 提案在一次原子切换中完成迁移、marker 补齐、引用更新和索引更新；不建立重定向或兼容文件。

## 阶段 A：修订并重新冻结 SPEC

先由单一 SPEC owner 修订 `docs/dev/SPEC-document-governance.md`，保留已有 12 个 MUST，但必须修正以下已核实矛盾：

- 将活动文档路径统一为平铺 `docs/dev/SPEC-*.md`、`PROPOSAL-*.md`、`ANALYSIS-*.md`，移除 `docs/dev/SPEC/`、`PROPOSAL/`、`ANALYSIS/` 的目标语义。
- 将 registry 目标统一为 `docs/dev/PROPOSAL-affordance-registry.md` 和 `docs/dev/PROPOSAL-resource-registry.md`。
- 在权威层级、目标树和 marker 门禁中明确保留根目录的 `implementation-status.md`、`coverage-baseline.md`、`spec-coverage.md`，三者都必须有 marker；`coverage-report.json` 为 GENERATED。
- 规定 `SUPERSEDED` 只能用于存在明确 `superseded_by` 的文件；`mobile-prototype-lessons.md` 改用 `DEPRECATED` 或明确的历史登记状态，不强行伪造替代文件。
- 允许 `docs/dev/ANALYSIS-*.md` 为 `DRAFT`；历史分析移入 `docs/history/` 后才使用 `SUPERSEDED`。
- 将 GOV-MUST-05 改为“不得由本治理 SPEC 新增 registry 运行时依赖；既有依赖必须在 INDEX/EXPERIMENT 中登记为 pre-existing”，避免错误要求回退现有实现。
- 明确 `tree_sha256` 的纳入文件、排序、排除规则，避免 snapshot 只能验证格式不能验证内容身份。
- 明确 marker 检查只解析每个文件首行，不扫描 SPEC 中用于教学的 marker 示例代码块。
- 明确 coverage-baseline、implementation-status、spec-coverage 的当前快照身份与 2525/2529/旧 coverage JSON 的历史边界；不把 stale 数字拼成一个当前结论。

验收：SPEC 首行 marker 合法，目标路径没有冲突，MUST 条款与 FILE-OWNERSHIP/TEST-MATRIX 一致；先运行 SPEC 结构自检。

## 阶段 B：原子文档迁移与内容分层

按不相交 owner 扇出至少 10 个实现代理，每个代理只改自己的文件集合；所有代理先读取修订后的 SPEC 和同一 ground-truth，提交自己的变更并运行局部测试。

建议 fanout 单元与文件所有权如下：

1. **治理 SPEC owner**：只改 `docs/dev/SPEC-document-governance.md`，完成阶段 A 修订和版本表更新。
2. **根文档 owner**：只创建/编辑 `docs/INDEX.md`、`docs/ARCHITECTURE.md`、`docs/CONTRACTS.md`、`docs/EXPERIMENT.md`、`docs/VERIFICATION.md`；从主稿迁移职责，所有文件加首行 marker。
3. **生命周期文档 owner**：只更新 `README.md`，把入口切换到 `docs/INDEX.md`，补当前/提案/历史/研究导航；不得定义接口。
4. **状态文档 owner**：只更新 `docs/implementation-status.md`、`docs/coverage-baseline.md`、`docs/spec-coverage.md`，添加 marker、当前 snapshot 状态和四项 gate 口径，保留真实长周期 `NOT-COMPLETE`。
5. **历史迁移 owner**：只移动/标记 `docs/agentsociety2-design.md`、`docs/agentsociety2-design-v2.md`、`docs/design-improvement-guide.md`、`docs/DESIGN-ORGANIZATION-ANALYSIS.md` 到 `docs/history/`；保留全文、历史身份和替代关系，不建 redirect。
6. **研究迁移 owner**：只移动/标记 `docs/research/*.md` 到 `docs/history/research/`，修复确实存在的仓库相对链接；不伪造 `/tmp` 或外部绝对路径的可复现性。
7. **registry 提案 owner**：只移动/标记两个 registry 文档为 `docs/dev/PROPOSAL-affordance-registry.md` 和 `docs/dev/PROPOSAL-resource-registry.md`，保留未冻结声明；不得修改 registry 代码或配置。
8. **registry provenance owner**：只更新 `scripts/build_catalog.py`、`catalog/README.md`、`catalog/rules/*.toml`、`src/worldmock/catalog/affordances.py`、`catalog/affordances.toml` 中的文档引用，并按生成器规范重建/校验 provenance；不得改运行逻辑。
9. **catalog manifest owner**：只处理 `catalog/manifest.json`、`catalog/resources.jsonl`、必要的生成输出身份，运行 `scripts/build_catalog.py --check`；若生成差异超出文档 provenance，停止并报告，不手工改 JSONL。
10. **snapshot helper owner**：只新增 `scripts/evidence_manifest.py` 或 SPEC 指定的最小 snapshot helper，并新增其单元测试；实现 canonical JSON、tree scope、sha256、相对路径和 evidence claim 校验。
11. **coverage gate owner**：只修改 `scripts/coverage_gate.py` 和 `tests/test_coverage_gate.py`，让生成 report 绑定 manifest `snapshot_id`、manifest hash、source identity；不改分支/接口覆盖判定逻辑。
12. **documentation gate owner**：只新增 `scripts/documentation_gate.py` 和 `tests/documentation/test_governance.py`，实现 marker、目录状态、唯一当前规范、迁移映射、相对链接、owner 不相交、registry pre-existing 规则和 real-acceptance NOT-COMPLETE 检查。
13. **snapshot test owner**：只新增 `tests/documentation/test_snapshot_identity.py`，用 `tmp_path` 合成正反例测试 canonical JSON、hash、manifest 字段、coverage report binding；不读取临时/运行真实证据作为当前真值。
14. **protocol gate test owner**：只修改 `tests/evaluation/test_behavior_metrics.py` 中与文档治理相关的断言或新增静态集合测试，确保源码四项 gate 与文档机器清单一致，且 `plan_actual_deviation` 保持 report-only；不改生产行为。

上述 14 个 owner 文件集合互不相交。真正 fanout 时按用户要求一次派出 10+ 个 subagent；每个 dispatch 携带修订后的 ground-truth、明确 fence、交付格式和失败默认。若两个代理需要同一文件，必须先合并 owner，不允许并行写冲突文件。

## 阶段 C：实现门禁与局部验证

- 文档测试复用 `scripts/coverage_gate.py` 的纯函数、失败关闭和合成输入模式。
- 使用 `src/worldmock/footprints/models.py:canonical_json` 的字节语义，或以等价标准库实现并增加字节级一致性测试。
- 使用 `ast` 检查 gate 集合和运行时新增 registry 依赖边界；不靠自然语言 grep 判断接口重复。
- Markdown 检查只验证本地相对路径和真实目标，不访问外部 URL，不依赖 heading slug；首行 marker 只解析第一行，避免 SPEC 示例块误报。
- coverage report 写入前校验 manifest 的 `snapshot_id`、manifest SHA、代码/规范身份；身份不一致必须失败关闭。
- 生成物 `catalog/*.jsonl`、`catalog/manifest.json` 只能由现有生成器重建并运行 `--check`，禁止手工修补。
- 对所有 local/agent 单元逐一收验：文件存在、首行格式、结构完整、提交只含 owner 文件、局部测试有真实退出码。

## 阶段 D：合并、对抗验证和修复

1. 合并所有已收验单元，任何 blocked 单元显式记录，不用主 agent 探测替代。
2. 先跑文档门禁：
   - `uv run pytest -q tests/documentation/test_governance.py tests/documentation/test_snapshot_identity.py`
   - `uv run python scripts/documentation_gate.py`
3. 再跑 gate/相关回归：
   - `uv run pytest -q tests/test_coverage_gate.py tests/evaluation/test_behavior_metrics.py`
   - `uv run python scripts/coverage_gate.py`
4. 再跑全量 `uv run pytest -q`，记录真实 passed/failed/skip 数字。
5. 另派独立反驳代理，不拥有实现文件，只读取 SPEC 和合并后的树；它要故意制造以下违规 probe 并确认测试能抓到：删除第四项 gate、移除 snapshot_id、把历史文档标成当前、把旧 registry 路径写回 provenance、将 NOT-COMPLETE 改为 COMPLETE、制造 owner 文件集合交集。未被测试捕获的 probe 必须进入修复波。
6. 对确认的测试缺口再派 fix agent，按文件所有权修复并重跑同一失败集；修复后再派新的 recheck agent，不由原实现代理自证。

## 阶段 E：最终 SPEC/状态收口

- 用最终实际路径、测试名、snapshot schema 和门禁命令回写 `docs/dev/SPEC-document-governance.md` 的 FILE-OWNERSHIP、TEST-MATRIX、版本表；SPEC 正文不再包含已经被修订的错误子目录路径或错误 registry runtime 结论。
- `implementation-status.md` 只在治理门禁、文档测试、coverage gate 和所有迁移证据真实通过后更新治理项为 COMPLETE；真实 DeepSeek 十二周、三次隔离盲评、完整自然日交付仍保持 NOT-COMPLETE。
- `spec-coverage.md` 更新当前规范集合和四项协议 gate 口径；不能把治理测试覆盖率写成产品真实验收完成。
- 最终输出包含：修改文件清单、SPEC 修订点、行业依据、实际测试数字、门禁命令退出结果、剩余 NOT-COMPLETE/UNVERIFIABLE 项、未合并/blocked 单元结局。