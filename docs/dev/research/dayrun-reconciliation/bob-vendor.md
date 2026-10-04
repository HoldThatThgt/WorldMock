> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-reconciliation/bob-vendor.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

状态：**COMPLETE**。审查完成，但存在 **2 个 survived 测试判据缺口**，不能据此宣称所有选定约束都已被测试保护。

## 边界与基线

- 调用者仓库：`/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/vendor/agentsociety2`。
- 已核验提交：`8fb24348953c5cfa3e8d4b280c8830a8efcf5de6`。
- 唯一产品规范：`/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/docs/CONTRACTS.md`，“模型身份”，本次读取时第 773–784 行中的传输独立预算相关条款。
- 额外授权约束：本次调用者明确要求退避前释放并发许可、日志只输出异常类型而不输出供应商敏感正文；它们不是从实现反推的产品规范。
- 临时 detached 工作树：`/tmp/worldmock-vendor-bob.OwZAFY`，由 `mktemp -d /tmp/worldmock-vendor-bob.XXXXXX` 创建，再 `git worktree add --detach` 到上述提交；现已干净移除。
- 固定命令工作目录：上述 detached 工作树。所有突变仅修改临时工作树的生产源码；测试、调用者源码及测试从未修改，也没有提交。

审查前已读取仓库 AGENTS.md / CLAUDE.md。调用者初始完整状态为空，创建工作树后完整状态仍为空。

## 固定测试命令

命令在突变前声明，基线与全部十六个突变均保持一致：

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/tmp/worldmock-vendor-bob.OwZAFY/packages/agentsociety2 \
MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True \
WORKSPACE_PATH=/tmp/worldmock-vendor-bob-runtime.EtyLr3 \
/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/.venv/bin/python \
-m pytest -q -p no:cacheprovider \
packages/agentsociety2/tests/test_llm_dispatcher_transport.py \
packages/agentsociety2/tests/test_llm_dispatcher_no_retry.py \
packages/agentsociety2/tests/test_llm_callback_lifecycle.py
```

基线：退出码 **0**，**43 passed in 1.60s**，无跳过。全部模型响应使用本地脚本化 Router；生命周期用例构造真实 LiteLLM Router，但未向模型服务发送请求。

## 规范映射

映射 **14 项可观察要求**，静态核查没有发现选定范围内的缺失实现或既有实现偏离。
不统计两阶段 manifest、凭据池、ReAct 轮次或画像客户端，它们不在本次 vendor 传输审查范围内。

| 要求 | 规范锚点 | 实现与测试证据 |
| --- | --- | --- |
| 连接异常可用传输预算恢复 | CONTRACTS.md:781 | `_TRANSPORT_ERRORS`、类型化恢复测试 |
| 超时异常可用传输预算恢复 | CONTRACTS.md:781 | 同上，Timeout 参数 |
| 内部服务错误可用传输预算恢复 | CONTRACTS.md:781 | 同上，InternalServerError 参数 |
| 服务暂不可用可用传输预算恢复 | CONTRACTS.md:781 | 同上，ServiceUnavailableError 参数 |
| 固定最多额外三次传输尝试 | CONTRACTS.md:774,782 | `TRANSPORT_MAX_RETRIES` 与四次请求停止测试 |
| 两类预算独立消耗、耗尽不可借用 | CONTRACTS.md:782–783 | 混合序列与两个方向的耗尽测试 |
| 零普通重试且不能把普通错误当传输错误 | CONTRACTS.md:773,783 | 认证、参数、业务、结构错误与 no_retry 测试 |
| 传输退避依次为 2、4、8 秒 | CONTRACTS.md:782 | 延迟序列断言 |
| 失败摘要报告真实总尝试数 | CONTRACTS.md:783 | 不同错误组合的 after N attempts 断言 |
| LiteLLM Router 自身零重试 | CONTRACTS.md:784 | `_build_router` 显式传 num_retries=0；本次发现测试判据缺口 |
| 不启用备用模型 | CONTRACTS.md:784 | `_build_router` 未配置 fallbacks；本次发现测试判据缺口 |
| 重试保持逻辑请求参数 | CONTRACTS.md:784 | 每次请求字典相等断言 |
| 退避前释放并发许可 | 本次显式安全约束 | `_complete_once` finally 释放与 sleep 钩子断言 |
| 不输出供应商错误正文 | 本次显式安全约束 | 类型名日志与敏感测试标记排除断言 |

计数：实现缺失 **0**，静态实现偏离 **0**；**killed 14、survived 2、invalid 0、inconclusive 0**。
不报告突变分数或规范覆盖率。

## 突变账本

以下每行均执行上方固定测试命令。生产路径统一为 `packages/agentsociety2/agentsociety2/config/llm_dispatcher.py`，行号按基线提交标注。
每次先证明 detached 工作树干净，仅应用该行的一处语义突变，执行固定命令，然后立即恢复；
恢复后 `git diff --exit-code` 和 `git status --porcelain=v1 --untracked-files=all` 均再次证明干净。

| 突变名称 | 规范锚点 | 生产行 | 结果 | 因果证据 |
| --- | --- | --- | --- | --- |
| `exclude_APIConnectionError` | CONTRACTS.md:781 | 513 | **killed**；exit 1；6 failed, 37 passed in 1.67s | 连接异常第一次即失败，预期恢复及四次失败断言发现差异。 |
| `exclude_Timeout` | CONTRACTS.md:781 | 513 | **killed**；exit 1；8 failed, 35 passed in 1.69s | 超时异常第一次即失败，预期恢复及四次失败断言发现差异。 |
| `exclude_InternalServerError` | CONTRACTS.md:781 | 513 | **killed**；exit 1；2 failed, 41 passed in 1.67s | 内部服务异常第一次即失败，预期恢复及四次失败断言发现差异。 |
| `exclude_ServiceUnavailableError` | CONTRACTS.md:781 | 513 | **killed**；exit 1；3 failed, 40 passed in 1.68s | 服务不可用异常第一次即失败，并破坏混合错误序列。 |
| `extra_transport_attempt` | CONTRACTS.md:782 | 514 | **killed**；exit 1；6 failed, 37 passed in 1.65s | 预期 after 4 attempts，实际 after 5 attempts。 |
| `transport_spends_ordinary_budget` | CONTRACTS.md:782-783 | 516 | **killed**；exit 1；1 failed, 42 passed in 1.69s | 混合序列原应第六次成功，突变第四次遇限流时提前失败。 |
| `ordinary_borrows_transport_budget` | CONTRACTS.md:783 | 518 | **killed**；exit 1；14 failed, 29 passed in 1.69s | 认证、参数及业务错误后继续请求并成功，DID NOT RAISE。 |
| `transport_borrows_ordinary_budget` | CONTRACTS.md:782-783 | 514 | **killed**；exit 1；1 failed, 42 passed in 1.66s | max_retries=2 时传输请求超过四次；脚本耗尽后报告八次，四次边界断言失败。 |
| `wrong_transport_backoff` | CONTRACTS.md:782 | 515 | **killed**；exit 1；17 failed, 26 passed in 1.68s | 预期 [2.0]，实际 [1.0]，完整退避序列断言也失败。 |
| `wrong_reported_attempts` | CONTRACTS.md:783 | 524 | **killed**；exit 1；12 failed, 31 passed in 1.72s | 四次真实传输失败被错误报告为一次，错误次数断言失败。 |
| `ordinary_error_as_transport` | CONTRACTS.md:773,783 | 65 | **killed**；exit 1；7 failed, 36 passed in 15.80s | ValueError 与结构错误开始借用传输预算；零普通重试断言失败。 |
| `router_retries_enabled` | CONTRACTS.md:784 | 338 | **survived**；exit 0；43 passed in 1.59s | 43 例全部通过；独立构造诊断确认实际 Router.num_retries 为 1。 |
| `fallback_model_enabled` | CONTRACTS.md:784 | 338 | **survived**；exit 0；43 passed in 1.55s | 43 例全部通过；独立构造诊断确认实际 Router.fallbacks 包含备用模型。 |
| `request_model_changes_on_retry` | CONTRACTS.md:784 | 531 | **killed**；exit 1；4 failed, 39 passed in 1.66s | 请求参数比较发现 offline-model 与 backup-offline-model 不同。 |
| `semaphore_release_removed` | explicit safety constraint: release before backoff | 554 | **killed**；exit 1；20 failed, 23 passed in 1.77s | 退避钩子断言 acquired == 0，实际为 1。 |
| `provider_body_in_logs` | explicit safety constraint: log exception type only | 512 | **killed**；exit 1；1 failed, 42 passed in 1.66s | 敏感测试标记出现在失败摘要中，正文排除断言失败。 |

“传输借用普通预算”突变在应停止的第四次后继续执行请求，才耗尽脚本化响应并产生后续 RuntimeError；
失败来自正确的四次停止判据被违反，不是语法、导入或启动失败。各突变均通过 AST 解析检查并执行到目标路径。

## 两个存活突变与缺少的断言

固定三文件虽然覆盖 dispatcher 重试循环和 Router 生命周期，但未对真实 Router 的重试和备用模型配置设断言。
两个突变均能构造有效 Router，且全部既有测试通过。额外本地构造诊断只用于确认突变有效性，
不替换或修改预先声明的测试判据；诊断用虚构 test-key 和 example.invalid，没有真实请求。

```python
# 基线原文
return Router(model_list=model_list, cache_responses=True, num_retries=0)

# router_retries_enabled 的完整替换
return Router(model_list=model_list, cache_responses=True, num_retries=1)

# fallback_model_enabled 的完整替换
return Router(model_list=model_list, cache_responses=True, num_retries=0, fallbacks=[{model: [f"backup-{model}"]}])
```

| 存活突变 | 额外有效性证据 | 缺少的可观察断言 |
| --- | --- | --- |
| router_retries_enabled | 真实构造输出 `REACHABILITY 1 None` | 真实 Router 的自身重试预算为零；内部重试不能放大 dispatcher 的外部请求次数 |
| fallback_model_enabled | 真实构造输出 `REACHABILITY 0 [{'offline-model': ['backup-offline-model']}]` | 真实 Router 没有备用模型映射；失败路径不能转投备用模型 |

本次未修复生产代码或测试。存活项不说明当前提交已启用备用模型，只说明现有三文件不能发现这种回归。

## 恢复与清理证明

- 每个突变和两次有效性诊断后，都恢复唯一被修改的生产文件到原始字节，并验证 diff 为空、完整 status 为空。
- 临时工作树最终 HEAD 为 `8fb24348953c5cfa3e8d4b280c8830a8efcf5de6`，diff/status 为空。
- `git worktree remove /tmp/worldmock-vendor-bob.OwZAFY` 成功，退出码 0；未使用强制移除或递归删除。
- 调用者最终 HEAD 保持上述提交，`git diff --exit-code` 退出码 0，`git status --porcelain=v1 --untracked-files=all` 输出为空。
- 最终 `git worktree list --porcelain` 已不包含 Bob 临时工作树。
- 原始突变、固定命令驱动脚本、逐项测试输出及有效性证据保存在 `/tmp/worldmock-vendor-bob-runtime.EtyLr3/`。`mutation-results.json` 记录每项完整 before/after、退出码和失败测试。

未执行真实网络模型请求、长跑验收或全 AS2 测试，也没有证明跨宿主或供应商网络层行为。
