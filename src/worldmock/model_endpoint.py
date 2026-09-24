"""@brief WorldMock 与画像编译共用的 DeepSeek 官方模型端点。"""

#: AgentSociety2 与独立画像请求均使用官方 OpenAI 格式接口。
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
#: DeepSeek 官方模型与价格文档声明的上下文长度。
DEEPSEEK_CONTEXT_LENGTH = 1_000_000
