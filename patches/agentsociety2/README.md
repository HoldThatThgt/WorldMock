# AgentSociety2 固定源码与修改说明

本目录的唯一来源声明为 `SERIES`。固定上游为公共仓库 `tsinghua-fib-lab/agentsociety` 的
`670c94fff7c64c4f79b632125f2ccf968155e746`，版本为 AgentSociety2 2.8.7。
`baseline.tar.gz` 只包含该提交的 Git 跟踪安装文件与包源码，保留上游 Apache-2.0 `LICENSE`。

WorldMock 修改明确保存在 Git 补丁中，并依次由标准 `git apply` 应用：

- `decision-round.patch` 修改共同决策轮、直接环境调用与完整合同注册校验、恢复与时钟编排。
- `failure-propagation.patch` 修改工具协议和故障传播，不以合成结果掩盖失败；模型调用证据带这次调用的失败发送 `transport_attempts`。
- `memory-checkpoints.patch` 修改记忆存储、工作区恢复与人物接入钩子。
- `model-lifecycle.patch` 修改 worker 模型客户端清理（在任务自己的事件循环里关闭并移出 LiteLLM 缓存的异步客户端）、配置中的普通与传输独立重试预算、计数用尽后的停放重发（传输错误与限流 429，十二小时，`Retry-After`）、单次发送整次期限与失败发送收集，关闭底层隐式重试及响应缓存。
- `package-metadata.patch` 删除不再存在的上游 CLI 入口。

WorldMock 修改源于原固定维护提交 `8768e14df5c887c710fe96bf24981a2d8508346e`，
随后将生活记忆合并、巩固与反思规则迁入宿主；保留实际运行装配需要的完整直接环境合同注册校验。
框架保留严格检查点、调度与失败传播合同；原完整补丁压缩保存于 `evidence/original.patch.gz`，
只用于复现旧摘要的测试证据，不参与安装。
归档、每份补丁、安装元数据与最终包字节均经 `SERIES` 中的 SHA-256 核对。
源码目录由 `scripts/bootstrap.py` 生成，不能作为另一个手工维护的安装来源。
