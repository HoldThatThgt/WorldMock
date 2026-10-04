> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/day-plan-goals-main-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 当日目标与 main 组合验收

> 状态：主线组合的代码身份、依赖可达性与文档一致性验收通过。
> 日期：2026-09-21。功能与验收范围仍由[当日目标规格](../SPEC-day-plan-goals.md)定义。

## 合并边界

| 身份 | 固定提交 |
| --- | --- |
| 合并前 main | `b4a4f41acda441aac91dfd6d329f69b661ae6672` |
| 特性分支 | `4ae28c713266cb57ef0aa171c818e961b4a17a30` |
| 特性全量被测提交 | `b53079d67e87e5ff075c083f1bd96238b6bf4d99` |
| AS2 被测依赖 | `da802c18ddde04e2a3a7241198f533b5e2b423df` |

main 在特性开发后增加了用户配置手册。合并保留该提交全部手册与入口，手册首页唯一的
add/add 冲突采用 main 的最新措辞。新增手册随后按已冻结生产合同同步：

| 文档 | 组合修订 |
| --- | --- |
| 生活场景手册 | 补充空闲重估间隔、与常规间隔的大小关系及在手承诺语义 |
| 接口合同 | 删除人物作息段落残留的旧当日计划骨架就寝锚点说明 |
| README 与运行手册 | 对齐 `DEEPSEEK_NODE_KEYS`、密钥池、实际端点/模型与各入口读取差别 |
| 交付手册 | 区分私密文件输入键 `DEEPSEEK_NODE_KEYS` 与 LabelKit 子进程环境键 `DEEPSEEK_API_KEY` |
| 配置设计 | 保留主线手册导航，统一凭据字段、实际运行检查与权限建议；不宣称 validate 强制检查权限 |

这些变更只同步文档，不新增配置能力或改变任何运行行为。
原特性开发时冻结的已有运行修复及依赖随分支保留，未从其他会话的当前工作树重新取值。

## 验证与可复现性

合并后执行：

```sh
git diff --exit-code 4ae28c7 -- src custom tests vendor .gitmodules pyproject.toml uv.lock
```

退出码零、无差异；生产代码、测试、依赖与锁文件和特性验收版本完全相同。
因此继续使用[特性全量验收](day-plan-goals-verification.md)的固定源码证据：
3638 passed、4 个既有缺历史产物测试跳过、分支覆盖率 96.51%、未覆盖公共接口为零。
没有把文档组合称为又一轮全量测试。原三十五个语义突变及存活补丁复验也保留各自源码身份。

组合新增检查命令：

```sh
LITELLM_LOCAL_MODEL_COST_MAP=True \
PYTHONPATH=src:.:vendor/agentsociety2/packages/agentsociety2:vendor/labelkit/src \
/Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest \
  tests/test_style_gate.py tests/test_documentation_gate.py \
  tests/integration/test_llm_dispatcher_multikey.py -q
```

实际结果为 `11 passed in 2.14s`。文档独立审查核对新手册与运行、目标合同，
指出的重估参数、旧锚点、凭据字段/模型及权限检查差异均已修正。
最终文档链接门禁与 `git diff --check` 另行执行并通过。

AS2 的固定提交已发布到 `.gitmodules` 指定的自有仓库
`HoldThatThgt/WorldMock-AgentSociety` 的 `codex/day-plan-goals-dependency` 分支，
主仓库仍以精确提交固定依赖，不依赖本地孤立对象。
主目录两份未跟踪提案原稿与特性开发基线逐字节一致；合并前另行保留原稿，不将它们丢弃或覆盖为无备份内容。

本次操作不修改其他工作树，也不改变项目级十二周正式运行、盲评和完整自然日交付的验收状态。
