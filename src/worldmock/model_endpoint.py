"""@brief WorldMock 与画像编译共用的 DeepSeek 官方模型端点。"""

#: AgentSociety2 与独立画像请求均使用官方 OpenAI 格式接口的 strict（Beta）端点：探针实测主站不执行函数的 strict 声明，
#: /beta 在请求时校验 strict 函数的 Schema（不合规的 Schema 返回 400），输出取值不保证受约束（关闭思考时枚举仍可能越界），
#: 宿主本地的严格校验仍是权威
#: （docs/dev/SPEC-dayrun-fidelity.md 7.5，裁决 D14；探针 scripts/probe_deepseek_strict.py）。
DEEPSEEK_BASE_URL = "https://api.deepseek.com/beta"
DEEPSEEK_MODEL = "deepseek-flash"
#: DeepSeek 官方模型与价格文档声明的上下文长度。
DEEPSEEK_CONTEXT_LENGTH = 1_000_000
