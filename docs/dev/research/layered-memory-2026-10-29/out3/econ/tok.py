"""Character-class token model for DeepSeek prompts, fitted on usage.prompt_tokens (read-only)."""
import re
CLASSES = ("cjk", "cjkp", "alnum", "asciip", "other")
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_CJKP = re.compile(r"[　-〿＀-￯‘-‟…·]")
_ALNUM = re.compile(r"[A-Za-z0-9]")
_ASCIIP = re.compile(r"[\x00-\x7f]")


def feats(text):
    """counts per class"""
    c = len(_CJK.findall(text)); p = len(_CJKP.findall(text)); a = len(_ALNUM.findall(text))
    q = len(_ASCIIP.findall(text)) - a
    return [c, p, a, q, len(text) - c - p - a - q]


def stream_parts(req):
    """Segments in model order: system, tools, then each later message (role tag + content + reasoning + tool calls)."""
    from common import ser
    msgs = req["messages"]
    parts = [msgs[0]["content"], ser(req.get("tools") or [])]
    for m in msgs[1:]:
        s = "<|" + m["role"] + "|>"
        if m.get("reasoning_content"):
            s += m["reasoning_content"]
        if m.get("content"):
            s += m["content"]
        for tc in m.get("tool_calls") or []:
            s += tc["function"]["name"] + tc["function"]["arguments"]
        parts.append(s)
    return parts
