<a name="personas-编写-persona-toml"></a>
# 编写 persona.toml

在环境根的 persona.toml 声明唯一人物。身份、至少一百字符的 background_story、住处、初始地点、
资金、身体质量、能量、睡眠不足、初始放松需要和初始社交需要是当前输入合同。
可声明工作安排、节律覆盖、健康习惯与社交风格散文；完整字段见[配置](../design/08-configuration.md)。
住处与初始地点分别解析实际地点显示名，初始地点可以不同于住处。
初始资金使用整数分；两项心理初值在零至一内，身体值使用有限数值。
不要添加旧 occupation、routine_preference、consumption_preference、risk_preference 或多人 agents 数组。

```powershell
python -m worldmock profile compile --persona D:/datasets/my-world/persona.toml --out .work/persona.json
python -m worldmock profile check --persona D:/datasets/my-world/persona.toml
```

画像命令同时读取同目录 config.toml 的模型配置与实际服务元数据。编译执行三次独立抽取并保存共识卡片，
节律的显式人设声明覆盖对应锚点；输入与模型预算不进入人物可见请求。
检查报告关注卡片和字段一致性，详情见[人物设计](../design/06-agent.md)。
