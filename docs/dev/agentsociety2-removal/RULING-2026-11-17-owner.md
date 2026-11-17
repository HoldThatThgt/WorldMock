# 去掉 AgentSociety2：负责人裁定记录（2026-11-17）

> 属于[去掉 AgentSociety2 的提案](PROPOSAL-agentsociety2-removal.md)。本文记录提案第十二章「待裁定」各行的决定，以及负责人对开发方式的指示。

## 负责人的原话

> in a separate worktree
>
> confirm the details of the implementation plan and the list of files to change (especially the design document) of proposal docs\dev\agentsociety2-removal\PROPOSAL-agentsociety2-removal.md, come up with the final feature-development spec document. spec is a detailed doc about how to implement mental model described in proposal - so no "I think this is better" or any kind of drifting of spec to proposal.
>
> then implement the functionality described in spec . Do not defer any pending implementation work — enforce this with test cases — and do not build anything the spec doesn't describe.
>
> this task is unattended, for questions takes recommend solution and log them, put them in final reports; spec, and Impl gets one worflow, each workflow gets one pass review and fix, no separate review and fix workflows. apply all changes at once with multiple agents in workflow, then run test, then one pass review & fix; don't run tests here and there which would slow down task
>
> when finished, commit, merge, and push to main

## 读法

- 「for questions takes recommend solution」：第十二章每一行都取「推荐」一栏的选项。下表逐行写明取了什么。
- 规格与实现阶段遇到的其余问题，同样取推荐做法，记在[开发规格](SPEC-agentsociety2-removal.md)各章的「规格阶段的决定」里，并汇总进最终报告。
- 开发方式以负责人的这段话为准：规格一个工作流、实现一个工作流，各带一轮评审与修正；实现时多个代理一次改完，再统一跑测试，再做一轮评审与修正。提案第九章「按可运行的提交排列」的提交次序由这条指示取代，细节见开发规格的实施一章。

## 各行的决定

| 序号 | 事项 | 决定 | 说明 |
| --- | --- | --- | --- |
| 1 | 去不去掉 AgentSociety2，什么时候做 | 正式 12 周运行之前做 | 一条分支，一次合并 |
| 2 | 推翻「要推翻的既往决定」一节列出的旧规定 | 推翻 | `docs/dev/SPEC-as2-integration.md:40-55`、`:378-391` 的复用边界与改动管理原则，`docs/dev/research/as2-feasibility.md` 的结论，`docs/design/00-overview.md` 的框架选型与并发方案，由本裁定取代；冻结的旧记录不回写 |
| 3 | 人物工作区文件夹 | 缩成 WorldMock 自己的两个文件（`MEMORY.md` 与 `memory/episodes.jsonl`） | 「完全从事件推导、取消人物快照」不在本次，另立提案 |
| 4 | 「记忆已更新」记录里三个恒定字段 | 随这次一起删 | `consolidated`、`reflection`、`state_sha256` |
| 5 | 运行目录名 | 改为 `run_dir/agents/agent_NNNN` | |
| 6 | 模型请求底稿里多出来的参数 | 删除 | 底稿只记真正交给客户端的键 |
| 7 | 向人物提外部问题的入口（只读问答） | 删除 | 提示指纹 `prompt_sha256` 随之改变 |
| 8 | 运行出故障停下时写下的原因 | 原始错误直接上报，并去掉「决策轮没有开着」这条假错误 | |
| 9 | 随请求附带的请求头 | WorldMock 自持一个网络客户端，整次运行共用，结束时关一次 | |
| 10 | 证明请求不变用的录音 | 取回一份，再批准一次带计划内重启的计费录制 | 取回不成：10-31 的四次两天运行目录不在本机，本机的 WorldMock-Dataset 检出（`330b606`）也没有。因此只用计费录制：在基线工作树上录一份带一次计划内重启的两天运行，作为基线与新分支录音重跑的共同来源 |
| 11 | 合并后的计费试跑 | 跑一次短的 | 检查退出时没有未关闭连接的告警、`transport_attempts` 与 `status.json` 正常；模型输出不确定，只作传输层证据 |
| 12 | 对外的说法 | 改成「WorldMock 自有的决策循环」 | |
