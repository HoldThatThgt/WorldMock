> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-reconciliation/bob-vendor-recheck.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

状态：**COMPLETE**。原报告中的两个存活突变本次均为 **killed**；复验范围内无存活、无效或不确定突变。

## 复验边界

- 调用者路径：`/tmp/worldmock-router-guards.WdU2jA`，Git 解析根目录为 `/private/tmp/worldmock-router-guards.WdU2jA`。
- 基线提交：`ae3224ce982d52f1eb773a4f70521a7b60e19cba`。
- 产品规范：`/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/docs/CONTRACTS.md`，“模型身份”中的“LiteLLM Router 自身不重试，不启用备用模型”条款；原审查锚点为第 784 行。
- 原报告：`/tmp/worldmock-dayrun-bob-vendor.md`，本次未覆盖或修改。
- 本次只复验 `router_retries_enabled` 与 `fallback_model_enabled` 两个原样突变，不把旧提交的其他十四个结果冒充为在新提交重跑。

按已加载的 Uncle Bob skill 执行。已读取调用者 AGENTS.md，确认调用者有真实提交、完整工作区状态为空。
`git diff --stat 8fb2434..HEAD` 确认新提交只在 `test_llm_dispatcher_transport.py` 增加 32 行测试，生产源码没有变化。

## 隔离与固定命令

`mktemp -d` 创建 `/tmp/worldmock-vendor-bob-recheck.J9U4vo`，再从新基线创建 detached 工作树。
创建后调用者完整状态仍为空。所有基线、突变与恢复都在该临时工作树执行，未改动调用者、当前隔离根或其中的 vendor。

工作目录：`/tmp/worldmock-vendor-bob-recheck.J9U4vo`。突变前声明并在基线与两个突变中保持不变的命令为：

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/tmp/worldmock-vendor-bob-recheck.J9U4vo/packages/agentsociety2 \
MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True \
WORKSPACE_PATH=/tmp/worldmock-vendor-bob-recheck-runtime.5FIzcq \
/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/.venv/bin/python \
-m pytest -q -p no:cacheprovider \
packages/agentsociety2/tests/test_llm_dispatcher_transport.py \
packages/agentsociety2/tests/test_llm_dispatcher_no_retry.py \
packages/agentsociety2/tests/test_llm_callback_lifecycle.py
```

基线退出码 **0**，结果 **45 passed in 1.59s**，无跳过。
真实 Router 测试只替换最后一层 `_acompletion` 供应商调用，保留真实路由和内部重试流程；本次没有网络模型请求。

## 规范映射与计数

| 规范要求 | 生产实现 | 本次测试证据 |
| --- | --- | --- |
| Router 自身零重试 | `_build_router` 显式设置 `num_retries=0` | `test_real_router_makes_only_one_provider_attempt` 断言真实 Router 只向供应商层尝试一次 |
| 不启用备用模型 | `_build_router` 不设置备用模型 | 上述实际请求序列断言，以及 `test_real_router_has_no_fallback_models` 对普通、上下文窗口与内容政策备用模型集合的断言 |

映射要求 **2**；实现缺失 **0**、静态实现偏离 **0**；**killed 2、survived 0、invalid 0、inconclusive 0**。
不报告全规范覆盖率或突变分数。

## 原样突变与因果证据

唯一修改的生产路径为 `packages/agentsociety2/agentsociety2/config/llm_dispatcher.py` 中 `_build_router` 返回语句。
每项先校验匹配原文唯一、修改后可通过 AST 解析；执行上方固定命令后立即还原并证明干净，再进行下一项。

```python
# 共同原文
return Router(model_list=model_list, cache_responses=True, num_retries=0)

# router_retries_enabled：与原报告完全一致
return Router(model_list=model_list, cache_responses=True, num_retries=1)

# fallback_model_enabled：与原报告完全一致
return Router(model_list=model_list, cache_responses=True, num_retries=0, fallbacks=[{model: [f"backup-{model}"]}])
```

| 突变 | 固定命令结果 | 分类与直接失败证据 |
| --- | --- | --- |
| `router_retries_enabled` | exit 1；**1 failed, 44 passed in 4.74s** | **killed**；`test_real_router_makes_only_one_provider_attempt` 观察到 `['test-model', 'test-model']`，与期望的一次 `['test-model']` 不同 |
| `fallback_model_enabled` | exit 1；**2 failed, 43 passed in 1.66s** | **killed**；实际供应商层序列出现额外 `backup-test-model`；独立集合断言也发现 `router.fallbacks == [{'test-model': ['backup-test-model']}]` 非空 |

两项失败都来自直接可观察的规范违反：多发一次供应商尝试或转投备用模型。
没有语法、导入、启动或夹具加载失败，因此不归为 invalid。

## 恢复与清理

- 基线后、每个突变前后均执行 `git diff --exit-code` 与 `git status --porcelain=v1 --untracked-files=all`，结果为 diff 为空、完整 status 为空。
- 临时工作树最终 HEAD 保持 `ae3224ce982d52f1eb773a4f70521a7b60e19cba`。
- `git worktree remove /tmp/worldmock-vendor-bob-recheck.J9U4vo` 成功，退出码 0；未强制删除。
- 调用者最终 HEAD 仍为上述提交，diff 检查退出码 0、完整 status 输出为空。
- 最终 `git worktree list --porcelain` 不再包含本次 Bob 工作树。
- 未修改测试、未修复实现、未提交、未修改原报告。
- 原始结果与逐项日志保留于 `/tmp/worldmock-vendor-bob-recheck-runtime.5FIzcq/`，`mutation-results.json` 含完整 before/after、退出码及失败测试。

本次只执行固定三文件复验，没有重跑调用者提供的全套 480 测试，也未验证真实网络、实际供应商或长跑结果。
