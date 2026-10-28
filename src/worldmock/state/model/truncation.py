"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations



#: 截断重试的思考开关与工具选择（docs/design/06-agent.md「宿主模型协议」；思考模式拒收 required，关闭思考后可用，真实探针见规格 7.2）。
RETRY_THINKING = {"thinking": {"type": "disabled"}}
