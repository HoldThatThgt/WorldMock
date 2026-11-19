"""@brief 截断回复的共享夹具：思考用尽输出上限、没有工具调用的提供商形状回复。"""

from __future__ import annotations

from types import SimpleNamespace


class TruncatedResponse:
    """思考用尽输出上限的回复（形状取自 4a5b0e6 运行记录）：finish_reason=length，正文为空，没有工具调用，思考写满。"""

    def __init__(self, reasoning: str, tokens: int = 8000) -> None:
        self._reasoning, self._tokens = reasoning, tokens
        message = SimpleNamespace(content="", tool_calls=None, reasoning_content=reasoning)
        self.choices = [SimpleNamespace(finish_reason="length", index=0, message=message)]

    def model_dump(self, *, mode: str) -> dict:
        """提供商形状的回复：usage 带输出 token 与其中的思考 token。"""
        assert mode == "json"
        message = {"role": "assistant", "content": "", "tool_calls": None, "reasoning_content": self._reasoning}
        return {"model": "deepseek-flash", "choices": [{"finish_reason": "length", "index": 0, "message": message}],
                "usage": {"prompt_tokens": 12000, "completion_tokens": self._tokens,
                          "total_tokens": 12000 + self._tokens, "prompt_cache_hit_tokens": 2000,
                          "prompt_cache_miss_tokens": 10000,
                          "completion_tokens_details": {"reasoning_tokens": self._tokens}}}
