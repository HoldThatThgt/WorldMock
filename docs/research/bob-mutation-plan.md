# WorldMock 核心合同 Uncle Bob 语义突变计划

## 结论与执行边界

本文把 `docs/agentsociety2-design.md` 的可观察核心合同映射到当前生产入口、现有行为测试和一个最小语义
突变。计划包含七十八个彼此独立执行的突变，覆盖本次父任务指定的核心范围；它不声称穷尽整份规范。

映射收口复核于 2026-09-15。权威规范 SHA-256 在 Bob 静态准备收据中冻结；执行前若规范继续变化，
执行器必须拒绝开始并重新生成清单。

最初静态准备快照没有可供隔离 worktree 固定的基准提交。随后两个已关闭的隔离会话只运行了失败基线，
均未应用突变；失败证据和当前测试组织修正在文末单独记录。父任务提供包含该修正的干净提交后，
执行者必须重新核对规范摘要、符号和精确替换文本；
任何漂移都先更新计划，不能把无法应用的替换记为 survived 或 killed。

## 后续执行合同

以下命令只供父任务提供干净提交之后执行。`BOB_CALLER` 保留原工作区，所有源码改写只发生在临时 detached
worktree。`bob_case` 后的参数就是下表完整、可执行的 pytest 选择器。

```bash
export BOB_CALLER=/Users/atishoo/Project/WorldMock
export BOB_PYTHON="$BOB_CALLER/.venv/bin/python"
test -z "$(git -C "$BOB_CALLER" status --porcelain=v1 --untracked-files=all)"
test -z "$(git -C "$BOB_CALLER/vendor/agentsociety2" status --porcelain=v1 --untracked-files=all)"
test -z "$(git -C "$BOB_CALLER/vendor/labelkit" status --porcelain=v1 --untracked-files=all)"
export BOB_HEAD="$(git -C "$BOB_CALLER" rev-parse HEAD)"
export BOB_WORKTREE="$(mktemp -d /tmp/worldmock-bob.XXXXXX)"
rmdir "$BOB_WORKTREE"
git -C "$BOB_CALLER" worktree add --detach "$BOB_WORKTREE" "$BOB_HEAD"
git -C "$BOB_WORKTREE" \
  -c protocol.file.allow=always \
  -c submodule.vendor/agentsociety2.url="$BOB_CALLER/vendor/agentsociety2" \
  -c submodule.vendor/labelkit.url="$BOB_CALLER/vendor/labelkit" \
  submodule update --init --checkout vendor/agentsociety2 vendor/labelkit
export BOB_AS2="$BOB_WORKTREE/vendor/agentsociety2"
export BOB_LABELKIT="$BOB_WORKTREE/vendor/labelkit"
export BOB_SOURCE_PATH="$BOB_WORKTREE/src:$BOB_AS2/packages/agentsociety2:$BOB_WORKTREE"
export WORKSPACE_PATH="$BOB_WORKTREE"
export LITELLM_LOCAL_MODEL_COST_MAP=True
test "$(git -C "$BOB_AS2" rev-parse HEAD)" = \
  "$(git -C "$BOB_WORKTREE" rev-parse HEAD:vendor/agentsociety2)"
test "$(git -C "$BOB_LABELKIT" rev-parse HEAD)" = \
  "$(git -C "$BOB_WORKTREE" rev-parse HEAD:vendor/labelkit)"
test -z "$(git -C "$BOB_AS2" status --porcelain=v1 --untracked-files=all)"
test -z "$(git -C "$BOB_LABELKIT" status --porcelain=v1 --untracked-files=all)"
uv sync --directory "$BOB_LABELKIT" --frozen --no-dev
test -x "$BOB_LABELKIT/.venv/bin/labelkit"
PYTHONPATH="$BOB_SOURCE_PATH" "$BOB_PYTHON" - <<'PY'
import os
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest

root = Path(os.environ["BOB_WORKTREE"]).resolve()
venv = (Path(os.environ["BOB_CALLER"]) / ".venv").resolve()
if Path(sys.prefix).resolve() != venv:
    raise RuntimeError("Bob Python did not retain the caller virtual environment")
if not pytest.__version__:
    raise RuntimeError("pytest is unavailable in the caller virtual environment")
if Path(os.environ["WORKSPACE_PATH"]).resolve() != root:
    raise RuntimeError("WORKSPACE_PATH does not resolve to the isolated worktree")
if os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] != "True":
    raise RuntimeError("LiteLLM local cost-map mode is not enabled")
for name in ("worldmock", "agentsociety2"):
    spec = find_spec(name)
    origin = Path(spec.origin).resolve() if spec and spec.origin else None
    if origin is None or not origin.is_relative_to(root):
        raise RuntimeError(f"{name} does not resolve inside the isolated worktree")
PY
bob_case() {
  (cd "$BOB_WORKTREE" && PYTHONPATH="$BOB_SOURCE_PATH" "$BOB_PYTHON" -m pytest -q "$@")
}
bob_restore() {
  case "$1" in
    vendor/agentsociety2/*)
      git -C "$BOB_AS2" restore --source=HEAD -- "${1#vendor/agentsociety2/}"
      ;;
    *)
      git -C "$BOB_WORKTREE" restore --source="$BOB_HEAD" -- "$1"
      ;;
  esac
  test -z "$(git -C "$BOB_WORKTREE" status --porcelain=v1 --untracked-files=all)"
  test -z "$(git -C "$BOB_AS2" status --porcelain=v1 --untracked-files=all)"
  test -z "$(git -C "$BOB_LABELKIT" status --porcelain=v1 --untracked-files=all)"
}
```

声明基线覆盖所有会被改写的本项目入口及三个 AS2 核心入口。两组测试使用独立 pytest 进程，
避免不同项目的 conftest 模块相互遮蔽。任一组基线失败时停止，不执行任何突变。

```bash
bob_case tests/footprints tests/life tests/integration tests/delivery tests/evaluation
bob_case vendor/agentsociety2/packages/agentsociety2/tests/test_decision_round.py \
  vendor/agentsociety2/packages/agentsociety2/tests/test_society_decision_round.py \
  vendor/agentsociety2/packages/agentsociety2/tests/test_agent_react_validation.py
```

每次只应用表中的一个替换或插入，先确认旧文本在目标符号中恰好出现一次，再运行该行命令。结果只允许
`killed`、`survived`、`invalid`、`inconclusive`。测试断言失败或预期异常消失为 killed；导入失败、语法错误、
夹具或环境失败不算 killed。每次记录源码 diff、测试退出码和关键输出，然后执行：

```bash
bob_restore <目标文件>
```

随后要求 `git status --porcelain=v1 --untracked-files=all` 为空，才可进入下一项。

## 预声明语义突变

表中规范位置均指当前 `docs/agentsociety2-design.md`。Python 入口使用 `文件::符号`，配置语义使用固定文件和字段，
避免并发编辑造成行号假精确。

| 表序号 | 可观察合同与规范位置 | 当前生产入口与唯一突变 | 精确测试命令 | 杀死条件 |
| --- | --- | --- | --- | --- |
| M01 | 同版本只允许追加观察，不能隐藏改变状态；“事务与失败处理”496–509 | `src/worldmock/footprints/store.py::FootprintStore._validate_state`：把同时检查 `delta == 0` 内容相等的条件替换为只检查 `delta not in (0, 1)` | `bob_case tests/footprints/test_store.py::test_state_and_events_commit_together_and_reject_same_version_mutation` | 同版本余额改写不再抛出 `IntegrityError`，测试失败 |
| M02 | 世界版本变化必须有事实事件；“导出认证与语义评审修正”1088–1097 | `src/worldmock/footprints/store.py::FootprintStore.commit`：把 `if state["version"] != before["version"] and not events` 替换为 `if False` | `bob_case tests/footprints/test_store.py::test_world_change_without_any_event_cannot_be_committed` | 无事件的状态变化被提交，测试失败 |
| M03 | 事件、资金和世界状态必须在一个事务中回滚；“事务与失败处理”496–509 | `src/worldmock/footprints/store.py::FootprintStore.commit`：在循环内 `_insert_event(record)` 后插入 `self.connection.commit()`，使第二条事件失败前第一条提前提交 | `bob_case tests/footprints/test_store.py::test_sql_failure_rolls_back_money_and_all_events` | 注入的第二次写入失败后数据库残留首条事件，回滚断言失败 |
| M04 | 每个账户差额必须逐项等于权威资金事件，不能只核对净和；“本次开发合同与实验绑定”792–839 | `src/worldmock/footprints/ledger.py::validate_account_transition`：把逐账户 `any(...)` 比较替换为 `sum(current.values()) - sum(previous.values()) != sum(changes.values())` | `bob_case tests/footprints/test_ledger.py::test_missing_or_false_ledger_cannot_commit_changed_accounts` | `wrong_amount` 的错误双方金额因净和为零而被接受，参数化测试失败 |
| M05 | 到达终点但没有成功共同检查点不能声明完成；“共同检查点与恢复”511–525、“导出认证”1088–1097 | `src/worldmock/footprints/export.py::_export_identity`：把 `saved` 改为只要求唯一 `run_ended` 位于窗口终点，删除最终检查点要求 | `bob_case tests/footprints/test_export.py::test_clock_end_without_successful_final_checkpoint_is_not_complete` | 未保存导出错误得到 `experiment_completed=True` |
| M06 | 正式比较固定执行一万次重抽样，不能用自然日数量替代；“真实性评估及结论边界”963–1007 | `src/worldmock/evaluation/metrics.py::_bootstrap_interval`：把抽样形状中的 `scenario.bootstrap_repetitions` 替换为 `len(scenario.window.days)` | `bob_case tests/evaluation/test_metrics.py::test_formal_window_uses_natural_week_blocks_and_ten_thousand_resamples` | 记录到的抽样形状从 `(10000, 12)` 变为 `(84, 12)` |
| M07 | 导出描述的物理行数必须与主文件一致；“最终交付格式”640–661 | `src/worldmock/footprints/validation.py::_read_artifact`：把行数不等的分支条件替换为 `if False` | `bob_case tests/footprints/test_validation.py::test_incomplete_or_tampered_export_never_reaches_labelkit` | 参数 `rows` 的伪造清单不再被拒绝，参数化测试失败 |
| M08 | 原生 `finish` 记忆必须真实写盘；写盘异常或空结果都要中止 Society，不能提交世界或检查点；“LifeAgent 的人格、记忆与 dreaming 边界” | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/memory_runtime.py::PersonMemoryRuntime.after_step`：把空写入分支的 `raise RuntimeError("Memory store did not persist finish memories")` 替换为 `written = [{"id": "fabricated"}]` | `bob_case tests/integration/test_life_agent_protocol.py::test_person_step_records_native_finish_memory_update 'tests/integration/test_life_agent_protocol.py::test_memory_persistence_failure_aborts_society_before_commit[exception]' 'tests/integration/test_life_agent_protocol.py::test_memory_persistence_failure_aborts_society_before_commit[empty]'` | 正常原生 step 和异常路径保持原行为；空 episode 被伪造成已保存记忆后，错误产生 memory update 并提交决策，零更新、零提交、零检查点和故障证据断言失败 |
| M09 | 真实性评估只能消费经过完整导出认证的 WorldMock 工件；“导出认证与语义评审修正”1088–1097、“真实性评估及结论边界”963–1007 | `src/worldmock/evaluation/validation.py::validate_canonical_delivery`：把 `validate_export(resolved)` 替换为无副作用表达式 `resolved.exists()` | `bob_case tests/evaluation/test_adapters.py::test_evaluation_uses_complete_export_quality_authentication` | 损坏的 `quality.json` 不再被生产认证器拒绝，测试的异常断言失败 |
| M10 | 恢复使用所选共同检查点，不得读取父运行后续 live state；“共同检查点与恢复”511–525 | `src/worldmock/footprints/checkpoint.py::_populate_branch`：把 `FootprintStore.create(..., checkpoint.state)` 的状态替换为 `store.state` | `bob_case tests/footprints/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace` | 子分支读取父运行后续时钟或因时钟回退失败，精确状态断言失败 |
| M11 | 子分支具有新运行身份且父运行不变；“共同检查点与恢复”511–525 | `src/worldmock/footprints/checkpoint.py::_populate_branch`：把新 `run_id=uuid4().hex` 替换为父 `store.manifest["run_id"]` | `bob_case tests/footprints/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace` | 子分支与父分支运行身份相同 |
| M12 | `finish` 记忆只能引用当前可见环境事件或真实持久记忆；“LifeAgent 的人格、记忆与 dreaming 边界” | `custom/agents/life_agent.py::LifeAgent._validate_finish`：把 `unresolved = refs - self._life_visible_refs` 替换为 `unresolved = set()` | `bob_case tests/integration/test_life_agent_protocol.py::test_finish_accepts_only_current_visible_environment_refs` | 未见过的 `unseen-event` 被接受 |
| M13 | 普通决策必须先提交恰好一个生活动作，再调用 `finish`；“决策轮共享接口冻结”1037–1046 | `custom/agents/life_agent.py::LifeAgent._validate_finish`：把 `_life_action_count != 1` 替换为 `_life_action_count < 0` | `bob_case tests/integration/test_life_agent_protocol.py::test_life_agent_rejects_invalid_or_non_atomic_decisions` | 未提交动作的 `finish` 不再返回协议错误 |
| M14 | 原生响应中 `finish` 必须是唯一工具调用；“AS2 核心链路需要补充的部分” | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py::AgentRuntimeMixin._build_react_decisions`：把混合调用保护条件替换为 `if False` | `bob_case vendor/agentsociety2/packages/agentsociety2/tests/test_agent_react_validation.py::test_finish_mixed_with_action_is_rejected_atomically` | 同一响应中的动作和 `finish` 被解析为有效结束 |
| M15 | 下一调度边界必须严格晚于当前时刻；“一个共享时钟”271–309 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/society/decision_round.py::SchedulingState.__post_init__`：把 `next_event_at <= current_time` 替换为 `<` | `bob_case vendor/agentsociety2/packages/agentsociety2/tests/test_decision_round.py::test_scheduling_state_requires_future_boundary` | 与当前时刻相等的边界被接受 |
| M16 | 结算后仍有人 ready 时必须再次决策，不能先推进时钟；“可决策时刻”310–320、“决策轮共享接口冻结”1037–1046 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/society/society.py::AgentSociety.step`：把 `if not after.ready_agent_ids` 替换为 `if True` | `bob_case vendor/agentsociety2/packages/agentsociety2/tests/test_society_decision_round.py::test_society_redecides_before_advancing_when_agent_remains_ready` | 首次拒绝后时钟提前六十秒，调用顺序断言失败 |
| M17 | 正式八十四天必须先形成十二个完整周块，不能按事件或单日独立抽样；“真实性评估及结论边界”963–1007 | `src/worldmock/evaluation/metrics.py::_block_mean_differences`：把 `size = scenario.block_days` 替换为 `size = 1` | `bob_case tests/evaluation/test_metrics.py::test_formal_window_uses_natural_week_blocks_and_ten_thousand_resamples` | 块值数量由十二变为八十四，重抽样总体和形状断言失败 |
| M18 | 食物能量按睡眠不足曲线折算，不得默认百分百利用；“资源与需求”208–223、“转换效率”224–251 | `src/worldmock/life/body.py::apply_food`：把曲线插值得到的 `efficiency` 替换为常量 `1.0` | `bob_case tests/life/test_body.py::test_food_uses_sleep_debt_curve_and_energy_cap` | 十二小时睡眠不足的效率不再是 0.875 |
| M19 | 节目知识只能按真实播放位置揭示；暂停空档、未来字幕和重复播放必须保留真实区间；“资源组合、转换效率与可解释性”252–265 | `src/worldmock/life/content.py::reveal_watch`：把 `stop = min(item.duration_seconds, activity.media_position_seconds)` 替换为 `stop = item.duration_seconds` | `bob_case tests/life/test_content_game.py::test_subtitles_reveal_only_played_intervals_across_pause_and_repeat` | 首段只播放到四百五十秒时泄漏后续字幕，位置、cue 上界或重复播放断言失败 |
| M20 | LabelKit annotate 与独立 verify 的真实请求均不能读取原始目标；“LabelKit 实测端点与规则消费者绑定”1065–1071 | `labelkit-projects/action.toml`：把 `[input]` 的 `text_field = "input"` 替换为 `text_field = "target"` | `bob_case tests/delivery/test_labelkit_process.py::test_real_labelkit_annotate_and_verify_requests_are_target_blind` | 两个 localhost 捕获请求之一出现冻结的 `future_canary` |
| M21 | 独立语义评审返回 fail 时不能进入事实回填或训练成品；“LabelKit 实测端点与规则消费者绑定”1065–1071 | `src/worldmock/delivery/pipeline.py::verify_process`：把 `if row["_meta"]["verification"]["verdict"] != "pass"` 替换为 `if False` | `bob_case tests/delivery/test_labelkit_process.py::test_failed_independent_semantic_verdict_blocks_process_output` | fail 记录不再抛出 `IntegrityError`，异常断言失败 |
| M22 | 共享原事件的派生窗口必须进入同一个来源连通组；“长序列与数据划分”663–670 | `src/worldmock/delivery/training.py::_source_identities`：删除把 `source_event_ids` 加入 identities 的语句 | `bob_case 'tests/delivery/test_training.py::test_shared_source_connects_different_declared_groups_into_one_split[event]'` | 四十个窗口不再由共享事件连通，最终分布到多个 split |
| M23 | 驾驶启程按冻结报价只扣一次并保持双边守恒；“场景与身体规则”840–856 | `src/worldmock/life/travel.py::pay_travel_fare`：把付款方扣款 `accounts[payer] -= fare` 替换为 `accounts[payer] -= fare * 2` | `bob_case tests/life/test_travel_cost.py::test_route_quote_is_paid_once_into_the_declared_transport_account` | 付款方被双扣或账户总额不守恒 |
| M24 | 同一 `action_id`、相同参数重放必须复用购买、驾驶和已验收工作的原结果，不得重复扣款、入账、库存变更或评审；“决策轮共享接口冻结”1037–1046 | `src/worldmock/life/engine.py::LifeEngine._resolve`：把 `if pending.reused` 替换为 `if False` | `bob_case tests/life/test_commerce.py::test_browse_purchase_replay_and_remote_delivery_use_one_canonical_payment tests/life/test_work_review.py::test_work_material_requires_time_and_paraphrase_can_be_paid` | 购买或驾驶重放改变账户、库存、配送或权威付款事件数量，或工作重放再次评审、付款并改变余额 |
| M25 | 共享实际素材的派生窗口必须进入同一个来源连通组；“长序列与数据划分”663–670 | `src/worldmock/delivery/training.py::_source_identities`：把素材循环内的 `identities.append(...)` 替换为 `continue` | `bob_case 'tests/delivery/test_training.py::test_shared_source_connects_different_declared_groups_into_one_split[asset]'` | 四十个窗口不再由共享素材连通，最终分布到多个 split |
| M26 | 适配必须逐事件守恒，不能静默丢掉 WorldMock 行；“真实性评估及结论边界”963–1007 | `src/worldmock/evaluation/adapters.py::_adapt_world_events`：把循环输入 `rows` 替换为 `rows[:-1]` | `bob_case tests/evaluation/test_adapters.py::test_worldmock_paused_gap_is_not_counted_and_right_boundary_is_accepted` | 守恒检查拒绝原本合法输入，或事件数不再为四 |
| M27 | 共同支持使用双方渠道交集，不能把只在一侧可见的渠道纳入比较；“真实性评估及结论边界”963–1007 | `src/worldmock/evaluation/metrics.py::shared_support`：把 `left_channels & right_channels` 替换为 `left_channels.union(right_channels)` | `bob_case tests/evaluation/test_metrics.py::test_physical_only_candidate_is_not_comparable` | 数字侧与纯物理侧错误得到共同渠道并变为 comparable |
| M28 | 盲评包不能暴露来源路径、运行、模型或解盲身份；“真实性评估及结论边界”963–1007 | `src/worldmock/evaluation/review.py::_timeline_row`：给公开行增加 `"source_pointer": activity.source_pointer` | `bob_case tests/evaluation/test_review.py::test_blind_packets_are_deterministic_and_hide_source_identity` | 序列化盲包出现 `source_pointer`，公开行字段闭集改变 |
| M29 | 一致性必须使用 nominal Krippendorff's alpha，左右候选编码不能合并；“真实性评估及结论边界”963–1007 | `src/worldmock/evaluation/review.py::_CANDIDATE_CODES`：把 `right_candidate` 编码从 `1.0` 改为 `0.0` | `bob_case tests/evaluation/test_review.py::test_review_ingest_unblinds_after_all_records_and_computes_alpha` | 候选模式失去两个类别，alpha 不再以 `ok/1.0` 返回 |
| M30 | 有因果硬错误的候选不能被宣布为优胜者，另一方也不能自动胜出；“真实性评估及结论边界”963–1007 | `src/worldmock/evaluation/decision.py::_outcome`：把任一候选不合格即返回 mixed 的条件替换为 `if False` | `bob_case tests/evaluation/test_decision.py::test_causal_hard_error_blocks_the_affected_candidate_from_winning` | 其余证据一致时，有硬错误的一方被错误宣布为 `*_more_supported` |
| M31 | 训练加载必须无损保留 JSON 数值、缺失与 null，并把已核验 JSON 文本直接交给对话列；“训练加载、规则执行与最后清单” | `src/worldmock/delivery/training.py::load_training`：把 `input`、`target`、`provenance` 三个 `Json(decode=False)` 同时替换为默认 `Json()`，恢复 Datasets 自动解码 | `bob_case tests/delivery/test_training.py::test_real_dataset_loading_preserves_all_facts_and_separates_answer` | `0.3` 或 `0.1` 在真实 Datasets 加载中改变，或保存重载后 JSON 文本、缺失与 null、prompt/completion 守恒断言失败 |

| M32 | 运行身份和实际模型配置必须冻结包含 /beta 的完整端点；“真实模型请求与工作验收补充” | `src/worldmock/runtime.py::module`：把冻结常量从完整 `/beta` 端点改为主站端点 | `bob_case tests/integration/test_runtime.py::test_model_identity_freezes_complete_strict_endpoint_and_request` | 模型身份无法继续满足 beta Literal，或精确 base_url 断言失败。 |
| M33 | 人物每一次原生模型请求都必须要求工具调用；“真实模型请求与工作验收补充” | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py::AgentRuntimeMixin._model_request`：把实际请求的 `tool_choice` 从 `required` 改为 `auto` | `bob_case vendor/agentsociety2/packages/agentsociety2/tests/test_agent_react_validation.py::test_invalid_response_has_one_terminal_evidence_and_stops` | 保存的实际请求中 tool_choice 不再等于 required。 |
| M34 | 每个当轮可用的 LifeAgent 函数必须声明 `strict=true`；“真实模型请求与工作验收补充” | `custom/agents/life_agent.py::_tool_schema`：把统一工具声明生成器的 `strict=true` 改为 `strict=false` | `bob_case tests/integration/test_activity_schema.py::test_all_life_tools_use_unmodified_deepseek_strict_schemas` | 函数表的逐项 strict 身份断言失败。 |
| M35 | 独立 WorkReviewer 函数必须声明 strict=true；“真实模型请求与工作验收补充” | `src/worldmock/life/work_review.py::prepare_work_review`：把 `submit_work_review` 的 `strict=true` 改为 `strict=false` | `bob_case tests/life/test_work_review.py::test_work_review_request_uses_required_strict_contract` | 准备后的 submit_work_review 函数 strict 精确断言失败。 |
| M36 | 独立 WorkReviewer 请求必须要求工具调用；“真实模型请求与工作验收补充” | `src/worldmock/life/work_review.py::prepare_work_review`：把工作评审请求的 `tool_choice` 从 `required` 改为 `auto` | `bob_case tests/life/test_work_review.py::test_work_review_request_uses_required_strict_contract` | 准备后的请求 tool_choice 精确断言失败。 |
| M37 | submit_work 的 MeetingMinutes 必须由同一子模型内联，不发送 $ref/$defs；“真实模型请求与工作验收补充” | `src/worldmock/life/models.py::SubmitWorkRequest`：移除 `SubmitWorkRequest.deliverable` 的内联 `WithJsonSchema` | `bob_case tests/integration/test_activity_schema.py::test_all_life_tools_use_unmodified_deepseek_strict_schemas tests/integration/test_activity_schema.py::test_nested_life_schemas_are_inlined_from_runtime_models` | 生成 Schema 出现引用，且 deliverable 不再等于内联子模型 Schema。 |
| M38 | finish 的 LifeMemoryEpisode 必须由同一子模型内联，不发送 $ref/$defs；“真实模型请求与工作验收补充” | `custom/agents/life_agent.py::FinishRequest`：移除 `FinishRequest.memories` 的内联 `WithJsonSchema` | `bob_case tests/integration/test_activity_schema.py::test_all_life_tools_use_unmodified_deepseek_strict_schemas tests/integration/test_activity_schema.py::test_nested_life_schemas_are_inlined_from_runtime_models` | 生成 Schema 出现引用，内联 memories.items 结构断言失败。 |
| M39 | WorkReviewer 的 ReferenceAssessment 必须内联，不发送 $ref/$defs；“真实模型请求与工作验收补充” | `src/worldmock/life/work_review.py::WorkReviewResponse`：移除 `WorkReviewResponse.reference_assessments` 的内联 `WithJsonSchema` | `bob_case tests/integration/test_activity_schema.py::test_work_review_nested_schema_is_inline_and_runtime_aligned` | 工作评审 Schema 的无引用断言失败。 |
| M40 | 服务可表达的嵌套非空文本约束必须同时存在于 submit_work Schema；“真实模型请求与工作验收补充” | `src/worldmock/life/models.py::MeetingMinutes`：删除 `MeetingMinutes.summary` 的非空白正则约束 | `bob_case tests/integration/test_activity_schema.py::test_submit_work_schema_rejects_blank_minutes` | 空白 summary 不再产生 JSON Schema 错误，而参数化同判测试要求拒绝。 |
| M41 | 服务不支持的 finish 数组数量边界必须由本地 Pydantic 保留；“真实模型请求与工作验收补充” | `custom/agents/life_agent.py::FinishRequest.validate_memory_count`：把本地记忆数量校验条件替换为 `if False` | `bob_case tests/integration/test_activity_schema.py::test_provider_array_or_text_limit_gap_remains_a_local_hard_rejection` | 空 memories 不再被本地模型拒绝，pytest.raises 断言失败。 |
| M42 | 服务不支持的 WorkReviewer 非空数组边界必须由本地 Pydantic 保留；“真实模型请求与工作验收补充” | `src/worldmock/life/work_review.py::WorkReviewResponse.validate_assessment_counts`：把本地工作评审数组非空校验条件替换为 `if False` | `bob_case tests/integration/test_activity_schema.py::test_work_review_array_cardinality_remains_a_local_hard_rejection` | 空 reference/delivery assessments 不再被本地模型拒绝。 |
| M43 | 每个模型工具对象必须 additionalProperties=false，并在本地拒绝未知字段；“真实模型请求与工作验收补充” | `src/worldmock/life/models.py::FrozenModel`：把所有生活模型的 `extra="forbid"` 改为 `extra="ignore"` | `bob_case tests/integration/test_activity_schema.py::test_all_life_tools_use_unmodified_deepseek_strict_schemas` | 生成器或递归对象断言发现对象不再封闭。 |
| M44 | 全部工具字段必须显式必填且不携带默认值；“真实模型请求与工作验收补充” | `src/worldmock/life/models.py::StartBasicActivityRequest`：给 `planned_duration_seconds` 添加默认值 `60` | `bob_case tests/integration/test_activity_schema.py::test_all_life_tools_use_unmodified_deepseek_strict_schemas` | 生成器或递归对象断言发现 planned_duration_seconds 不再必填并出现 default。 |
| M45 | 启动 Schema 只能验证原始 Pydantic Schema，不得删除可表达的最大时长约束；“真实模型请求与工作验收补充” | `src/worldmock/life/models.py::deepseek_strict_schema`：在严格 Schema 构造后删除 `planned_duration_seconds.maximum` | `bob_case tests/integration/test_activity_schema.py::test_duration_maximum_is_preserved_in_schema_and_runtime_validation` | 生成 Schema 不再等于原始模型 Schema，且 86401 秒不再被 JSON Schema 拒绝。 |
| M46 | WorkReviewer 实际客户端模型和完整 beta URL 必须与 manifest 一致；“真实模型请求与工作验收补充” | `src/worldmock/life/work_review.py::WorkReviewer.__init__`：把 WorkReviewer 客户端模型与完整 URL 身份检查替换为 `if False` | `bob_case tests/life/test_work_review.py::test_work_reviewer_rejects_model_or_full_base_url_mismatch tests/life/test_work_review.py::test_work_reviewer_accepts_exact_client_and_request_identity tests/life/test_work_review.py::test_work_reviewer_rejects_request_parameter_drift` | 错误模型和缺少 /beta 的客户端不再抛出 ProtocolError；匹配及参数合同用例同时守住正向边界。 |
| M47 | Legacy `actionInfo` 只证明声明，不能冒充完成；“真实性评估及结论边界” | `src/worldmock/evaluation/public_evidence.py::_legacy_action`：把状态 `declared` 改为 `completed` | `bob_case tests/evaluation/test_adapters.py::test_legacy_action_declarations_never_claim_completion_or_object_identity` | 订单和导航声明被错误显示为完成，状态断言失败 |
| M48 | Legacy 会话身份不能冒充持久对象身份；“真实性评估及结论边界” | `src/worldmock/evaluation/public_evidence.py::_legacy_action`：把空对象键改为 `sequence_id` | `bob_case tests/evaluation/test_adapters.py::test_legacy_action_declarations_never_claim_completion_or_object_identity` | 声明行得到伪造对象身份，对象键断言失败 |
| M49 | 商品使用必须按实际 `use_mode` 区分进食和非食品使用；“真实性评估及结论边界” | `src/worldmock/evaluation/public_evidence.py::_consume_category`：把 `use` 的分类从 `product_use` 改为 `eat` | `bob_case tests/evaluation/test_adapters.py::test_consume_category_uses_actual_closed_use_mode_for_evidence_and_duration tests/evaluation/test_adapters.py::test_worldmock_activity_duration_uses_the_same_consume_category` | 雨伞等商品使用被归入进食，公开证据或时长分类断言失败 |
| M50 | 支付证据只能取权威事件顶层的 `resource_changes`；“真实性评估及结论边界” | `src/worldmock/evaluation/public_evidence.py::_purchase_result`：把权威顶层账本差额改为读取业务 payload 副本 | `bob_case tests/evaluation/test_adapters.py::test_purchase_payment_uses_canonical_resource_changes_and_preserves_actual_states` | 只有 payload 副本的订单错误生成 `paid`，无权威账本反例失败 |
| M51 | 控制回执成功不等于活动完成；状态必须来自真实活动快照；“真实性评估及结论边界” | `src/worldmock/evaluation/public_evidence.py::_result_state`：把活动快照状态改为读取 ActionResult 回执状态 | `bob_case tests/evaluation/test_adapters.py::test_control_and_game_results_use_object_state_instead_of_receipt_status` | 继续和暂停活动被错误显示为完成，状态断言失败 |
| M52 | 暂停、继续、取消等控制状态属于活动对象；“真实性评估及结论边界” | `src/worldmock/evaluation/public_evidence.py::_control_shape`：把控制对象从 `activity_id` 改回内容目标对象 | `bob_case tests/evaluation/test_adapters.py::test_control_and_game_results_use_object_state_instead_of_receipt_status` | 控制事实不再绑定活动对象，对象类型或对象键断言失败 |
| M53 | 游戏命令成功不等于游戏终局，必须以 `done` 判定；“真实性评估及结论边界” | `src/worldmock/evaluation/public_evidence.py::_game_result_state`：把 `done=false` 的状态从 `in_progress` 改为 `completed` | `bob_case tests/evaluation/test_adapters.py::test_control_and_game_results_use_object_state_instead_of_receipt_status` | 非终局游戏命令被显示为完成，正反状态断言失败 |
| M54 | 任务截止事件只证明 deadline reached，不能推断工作失败；“真实性评估及结论边界” | `src/worldmock/evaluation/public_evidence.py::_world_environment`：把 `task_expired` 的公开状态从 `reached` 改为 `failed` | `bob_case tests/evaluation/test_adapters.py::test_mail_and_work_deadline_keep_public_facts_without_raw_text_or_failure_inference` | 已经验收付款的任务在截止时被错误标成失败，反推断断言失败 |
| M55 | 公开字段支持必须按类别和字段取双方实际交集并保留分母；“真实性评估及结论边界” | `src/worldmock/evaluation/metrics.py::_shared_public_fields`：把双方字段存在条件从 `and` 改为 `or` | `bob_case tests/evaluation/test_metrics.py::test_public_support_intersects_each_category_field_and_reports_missing_denominators` | 单侧履约方式或对象身份进入共同支持，精确字段与分母断言失败 |
| M56 | 匿名对象引用必须隔离真实候选命名空间；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_object_ref`：从 HMAC 消息中删除真实候选命名空间 | `bob_case tests/evaluation/test_review.py::test_object_references_are_candidate_and_reviewer_scoped_but_stable_across_days` | 相同私有键让左右候选得到相同匿名引用，隔离断言失败 |
| M57 | 匿名对象引用必须隔离三个评审会话；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_object_ref`：把按 reviewer slot 派生的 HMAC key 改为共享常量 | `bob_case tests/evaluation/test_review.py::test_object_references_are_candidate_and_reviewer_scoped_but_stable_across_days` | 不同评审得到相同匿名引用，隔离断言失败 |
| M58 | 盲包按真实会话和状态变化粒度折叠重复屏幕帧；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_public_evidence_rows`：禁用相同会话内的连续相同状态折叠 | `bob_case tests/evaluation/test_review.py::test_repeated_same_state_frames_collapse_only_within_their_real_session` | 一百条重复屏幕帧全部进入盲包，行数断言失败 |
| M59 | 重复证据只能在同一真实会话内折叠；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_evidence_change_identity`：把分组中的真实 session 改为共享常量 | `bob_case tests/evaluation/test_review.py::test_repeated_same_state_frames_collapse_only_within_their_real_session` | 两个独立会话被合并为一行，行数断言失败 |
| M60 | 因果连续性必须由双方都具有真实状态链才可比较；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_judgeability`：把左右因果链条件从 `and` 改为 `or` | `bob_case tests/evaluation/test_review.py::test_judgeability_requires_two_sided_state_changes_and_persistent_long_term_objects` | 只有一侧有状态变化时仍变成可判定，反例失败 |
| M61 | 长期结果只承认订单、任务、消息或游戏会话；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_has_state_chain`：禁用长期对象类型闭集过滤 | `bob_case tests/evaluation/test_review.py::test_judgeability_requires_two_sided_state_changes_and_persistent_long_term_objects` | 跨日重复路线被错误升级为长期结果，反例失败 |
| M62 | 不可判定维度必须提交 `unjudgeable`，不能奖励可观察性更多的一侧；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_enforce_judgeability`：把不可判定选择保护替换为 `if False` | `bob_case tests/evaluation/test_review.py::test_unjudgeable_dimension_rejects_a_candidate_preference` | 不可判定的因果维度接受候选偏好，异常断言失败 |
| M63 | 盲包只能暴露带候选和评审隔离的 HMAC 对象引用；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_public_evidence_row`：把 HMAC 对象引用替换为原始 `object_key` | `bob_case tests/evaluation/test_review.py::test_object_references_are_candidate_and_reviewer_scoped_but_stable_across_days` | 盲包出现 `shared-private-order`，原始身份泄漏断言失败 |
| M64 | 同一对象跨日重复但状态未改变不能构成因果链；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_has_state_chain`：把状态种类要求从大于一改为大于零 | `bob_case tests/evaluation/test_review.py::test_judgeability_requires_two_sided_state_changes_and_persistent_long_term_objects` | 只有重复 requested 的一侧被认作状态链，因果和长期反例失败 |
| M65 | 匿名对象引用必须在同一候选、同一评审内跨日稳定；“真实性评估及结论边界” | `src/worldmock/evaluation/review.py::_object_ref`：把日期加入 HMAC 消息 | `bob_case tests/evaluation/test_review.py::test_object_references_are_candidate_and_reviewer_scoped_but_stable_across_days` | 同一订单在不同日期得到多个匿名引用，跨日稳定性断言失败 |
| M66 | 共享画像只允许已核验的单人、白领和时区；“真实性评估及结论边界” | `src/worldmock/evaluation/scenario.py::_load_shared_profile`：禁用人数和职业闭集校验 | `bob_case 'tests/evaluation/test_evaluation_scenario.py::test_shared_profile_rejects_unverified_or_mismatched_claims'` | 多人或非白领画像被场景接受，参数化拒绝断言失败 |
| M67 | 共享画像不能携带偏好等私有推测字段；“真实性评估及结论边界” | `src/worldmock/evaluation/scenario.py::_load_shared_profile`：在字段闭集校验前静默删除 `preference` | `bob_case tests/evaluation/test_evaluation_scenario.py::test_shared_profile_rejects_additional_private_fields` | 带私有偏好的场景不再被拒绝，异常断言失败 |
| M68 | 生活动作提交前不能向模型开放 `finish`，提交后只能开放记忆与 `finish`；“人物工具与管理接口、动作结果” | `custom/agents/life_agent.py::LifeAgent.build_react_tool_schemas`：把 pre-action 工具闭集恢复为包含 `finish` 的旧全表 | `bob_case tests/integration/test_life_agent_protocol.py::test_life_agent_schema_is_closed_and_host_fields_are_hidden tests/integration/test_life_agent_protocol.py::test_native_react_submits_one_host_bound_life_action tests/integration/test_life_agent_protocol.py::test_native_react_rejects_finish_before_life_action` | 首个原生请求再次暴露 `finish`，模型可在零生活动作时结束，动态工具与拒绝断言失败 |
| M69 | 实验终点截断必须保留结构化 `interruption_reason=experiment_end`；“活动持续时间与边界、最终交付格式” | `src/worldmock/life/activities.py::truncate_activity`：只删除截断活动的 `interruption_reason`，保留 truncated 状态、边界和终态事件 | `bob_case tests/life/test_activities.py::test_completion_and_experiment_truncation_set_ended_at tests/life/test_activity_effects.py::test_paused_activity_is_truncated_at_experiment_end tests/life/test_engine_feedback.py::test_paused_activity_is_truncated_at_end_with_original_action_identity tests/life/test_export_integration.py::test_truncated_activity_exports_experiment_end_reason` | 活动仍能结束但内存状态或导出丢失实验终点原因，结构化原因断言失败 |
| M70 | 当前观察明确引用的历史动作结果必须作为权威原始事件进入输入；“导出认证与语义评审修正” | `src/worldmock/delivery/windows.py::_expand_observed_results`：把解析出的历史结果替换为当前观察本身 | `bob_case tests/delivery/test_windows.py::test_visible_prior_result_is_hydrated_as_direct_evidence_without_moving_cutoff` | 历史结果从 `input.events` 与引用闭集消失，原始事件水合断言失败 |
| M71 | 嵌套结果身份必须解析到 `action_result`；“导出认证与语义评审修正” | `src/worldmock/delivery/windows.py::_validated_observed_result`：禁用权威事件类型检查 | `bob_case 'tests/delivery/test_windows.py::test_observed_result_must_resolve_to_exact_visible_same_agent_prior_event[wrong_type]'` | `model_response` 被接纳为动作结果证据，类型反例失败 |
| M72 | 历史结果必须是人物可见事件；“导出认证与语义评审修正” | `src/worldmock/delivery/windows.py::_validated_observed_result`：禁用结果可见性检查 | `bob_case 'tests/delivery/test_windows.py::test_observed_result_must_resolve_to_exact_visible_same_agent_prior_event[private]'` | 系统私有事件进入证据窗口，可见性反例失败 |
| M73 | 历史结果必须属于当前观察人物；“导出认证与语义评审修正” | `src/worldmock/delivery/windows.py::_validated_observed_result`：禁用同人物检查 | `bob_case 'tests/delivery/test_windows.py::test_observed_result_must_resolve_to_exact_visible_same_agent_prior_event[other_agent]'` | 其他人物的结果进入证据窗口，同人物反例失败 |
| M74 | 历史结果必须早于观察且位于知识截止内；“导出认证与语义评审修正” | `src/worldmock/delivery/windows.py::_validated_observed_result`：禁用时序与截止检查 | `bob_case 'tests/delivery/test_windows.py::test_observed_result_must_resolve_to_exact_visible_same_agent_prior_event[future]'` | 未来结果被接纳为当前知识，截止反例失败 |
| M75 | 真实未来请求身份只能在语义审核通过后回填；“导出认证与语义评审修正” | `src/worldmock/delivery/windows.py::_attach_evidence`：用新列表把 future request ID 提前加入 action provenance，不改 allowed list | `bob_case 'tests/delivery/test_labelkit_process.py::test_real_labelkit_annotate_and_verify_requests_are_target_blind[pass]'` | 真实 LabelKit judge 请求出现未来请求 ID，实际请求盲性断言失败 |
| M76 | 公开 action 窗口身份只依赖可见输入；“导出认证与语义评审修正” | `src/worldmock/delivery/windows.py::_attach_evidence`：把隐藏未来目标加入 `window_id` 哈希 | `bob_case tests/delivery/test_windows.py::test_action_public_identity_is_independent_of_the_future_target` | 只替换未来目标就改变公开 provenance，身份稳定断言失败 |
| M77 | induction、reflection 与 counterfactual 必须具有不同窗口身份；“导出认证与语义评审修正” | `src/worldmock/delivery/windows.py::_window_identity`：从哈希输入删除 dreaming mode | `bob_case tests/delivery/test_windows.py::test_midnight_experiment_end_does_not_create_an_extra_training_day` | 三个 dreaming 模式折叠为同一公开身份，唯一性断言失败 |
| M78 | 语义验证必须从直接可见事实重建引用闭集；“导出认证与语义评审修正” | `src/worldmock/delivery/semantics.py::_visible_evidence_ids`：直接信任输入自报 allowlist | `bob_case tests/delivery/test_training.py::test_validator_accepts_hydrated_prior_result_and_rejects_allowed_list_tampering` | 伪造 allowlist 不再产生与可见证据不一致错误，篡改反例失败 |
## 预声明收口状态

M08、M19、M24 和 M31 保留原计划追加时的真实回归节点。M32 至 M46 与 M68 来自
`.work/verification/bob-strict-contract-additions.json` 的十六项独立严格请求、Schema 与动态工具合同；M47 至 M67
覆盖公开证据的声明边界、真实状态、共同字段、匿名身份、会话粒度和可判定性；M69 覆盖终点截断原因；
M70 至 M78 覆盖历史结果权威水合、来源类型、可见性、同人物、知识截止、未来身份盲性、窗口身份与
引用闭集重建。当前七十八项均已绑定现存生产符号和 pytest 节点，准备器会在干净提交上再次静态校验。
在父任务提供 clean HEAD 及明确执行放行前，本计划仍是 prepared，不能报告任何 killed 或 survived 结果。

## 执行记录模板

父任务取得 clean HEAD 后，每个表序号使用一行，不合并多项结果。

| 表序号 | 基准提交 | 突变 diff 摘要 | 测试命令 | 退出码 | 分类 | 关键证据 | 恢复后洁净 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Mxx | 待执行 | 待执行 | 待执行 | 待执行 | 待执行 | 待执行 | 待执行 |

完成全部条目后才运行与声明基线相同的相关全门。清理由准备器执行，路径必须解析到
`/tmp/worldmock-bob.*`，先反初始化子模块，再调用 `git worktree remove` 并从
`git worktree list --porcelain` 核对登记已消失。只有上述检查全部成功才删除 session；失败时保留 worktree、
Git 登记和 `.work/verification/bob-prepared/results/<HEAD>/cleanup/` 收据。

```bash
export WORLD_MOCK_BOB_APPROVED="$(shasum -a 256 docs/agentsociety2-design.md | awk '{print $1}')"
.venv/bin/python .work/verification/bob-prepared/bob_executor.py cleanup --execute
```

survived 表示测试缺口，不能通过改变规范或弱化生产实现来消除；补充最小行为测试后，必须在同一基准提交上
原样重放该突变并记录第二次结果。

## 基线分进程修正

首个根提交 `15c737392bab2238e17b3dfce497b413f150ca6b` 的隔离 setup 已验证源码与依赖来源。
将根项目与 AS2 测试放进同一个 pytest 进程时，裸 `from conftest` 被 AS2 的 conftest 遮蔽，产生十二个
collection error。第二个提交 `f287633563d14bb7518861e7c44c1f9546289170` 已把根项目和 AS2 分成两个进程；
AS2 组通过，但根项目内部 Life 与 Evaluation 仍共享顶层 `conftest` 名称，产生九个 collection error。
两个失败会话都在基线门停止，未创建任何 mutation attempt，且 cleanup 收据确认 worktree 登记和目录已删除。

当前修正给 `tests`、`tests/life`、`tests/evaluation` 明确包身份，并把 Life 与 Evaluation 的夹具导入改成限定路径。
原先最宽的单进程组合已经以 `351 passed in 18.61s` 通过，原始输出保存在
`.work/verification/qualified-test-imports.log`；该工作树验证只证明测试组织冲突已消失，不能替代下一 clean HEAD
上的两个隔离 baseline。执行器继续以两个进程运行同一测试范围，两个均通过后才允许 M01 至 M78。
