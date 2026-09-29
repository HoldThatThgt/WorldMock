"""@brief 联系人回复的后验守卫：四类越界说法的正则闭集、命中后的重试请求与固定兜底句。

联系人只通过消息和人物来往（docs/dev/SPEC-dayrun-fidelity.md 7.3，裁决 D11）：回复不能说自己正在赶来或已经到场，
不能说已替对方做完或带着东西过来，不能揽下对方的活，也不能替结果打包票。模型回复命中任一类时，在同一次结算内
重试一次（原请求后附被拦下的回复与一句纠正）；重试仍命中则改用固定兜底句。类别码、正则与纠正句逐字登记在
docs/CONTRACTS.md「联系人回复」。正则只能拦下常见说法，守卫不是保证，兜底句才是。
"""

from __future__ import annotations

import copy
import re
from typing import Any

#: 一个分句内的字符（不跨标点与换行）。
_CLAUSE = r"[^，。！？；,.!?;\n]"
#: 守卫类别闭集：类别码 → 命中说法的正则；按此顺序判定与报告。
GUARD_PATTERNS: dict[str, re.Pattern[str]] = {
    "arriving": re.compile(
        r"(马上|立刻|立马|这就|现在就|等会儿?就|一会儿?就)(要)?(到|过来|过去|赶过来|赶过去|来找你|去找你)"
        r"|我(已经|刚|也)?(快)?到了"
        r"|到(你)?(门口|楼下|楼上|门外)"
        r"|(在|已经在)(路上|来的路上|过来的路上|去你那的路上)|路上了|出发了|动身了|往你那"
        r"|(去|过去|过来|来|上来|下来)找你"
        rf"|我{_CLAUSE}{{0,8}}(到|去|回|赶到)(公司|单位|办公室|工位|现场|你那|你这|你家|楼下)"),
    "done_or_bringing": re.compile(
        r"(已经|都|早就|全都)(帮你|替你|给你)?"
        r"(弄|做|写|改|整理|核对|对|搞|处理|办|准备|发|交|提交|传|归纳|算|订|买|点)(完|好|妥|齐|定|了)"
        rf"|(帮你|替你|给你)把?{_CLAUSE}{{0,6}}(弄|做|写|改|整理|核对|搞|处理|办|准备|交|提交|订|买|点|发)(完|好|妥|了)"
        r"|(都|也|已经)带(着|上了|好了|齐了|过来了|来了)"
        r"|(给你|帮你)(带|拿|捎)(了|过来|过去|着)"),
    "taking_over": re.compile(
        r"我来(?=[，。！？；,.!?;\n]|$)"
        r"|我来(吧|做|写|弄|改|整理|处理|负责|收尾|定稿|补|搞|核对|归纳|汇总|跟进)"
        r"|交给我|包在我身上|我替你|我帮你(做|写|弄|改|整理|处理|搞|核对|对|归纳|汇总|交|提交|补|定稿|收尾)"
        r"|替你(做|写|弄|改|整理|处理|搞|交|提交)|你负责|我负责|你只管"),
    "guaranteeing": re.compile(
        r"(肯定|一定|绝对|保证|保准|准保)(能|来得及|赶得上|没问题|搞得定|搞定|完成|交得了|交上|按时)"
        r"|打包票|放心(吧)?[，,]?(肯定|一定|保证)"),
}
#: 纠正句里各类别的说法（与 GUARD_PATTERNS 同一闭集）。
GUARD_REASONS: dict[str, str] = {
    "arriving": "说了自己正在赶来、已经到了或会到对方所在的地方",
    "done_or_bringing": "说了已经替对方做完了什么或带着东西过来",
    "taking_over": "揽下了对方的活或替对方分了工",
    "guaranteeing": "替结果打了包票",
}
#: 首次与重试都命中时的固定兜底回复。
FALLBACK_REPLY = "我这边先看看，稍后回你。"
#: 每条来信至多请求模型的次数：首次加命中后的一次重试。
REPLY_ATTEMPT_LIMIT = 2


def guard_matches(text: str) -> list[str]:
    """@brief 回复命中的守卫类别码，按闭集顺序；不命中时为空列表。"""
    return [code for code, pattern in GUARD_PATTERNS.items() if pattern.search(text)]


def correction_text(matched: list[str]) -> str:
    """@brief 重试时附在被拦下的回复之后的一句纠正：写明命中了哪几类说法，并重申只通过消息来往。"""
    reasons = "；".join(GUARD_REASONS[code] for code in matched)
    return (f"上面这条回复不能发出：{reasons}。你只通过消息和对方来往，不在对方身边，也不会去找对方；"
            "要做的事只写成打算。请重写这条回复，只输出回复正文。")


def retry_request(request: dict[str, Any], text: str, matched: list[str]) -> dict[str, Any]:
    """@brief 命中后的重试请求：原请求的消息之后附被拦下的回复（assistant）与纠正（user），其余参数原样不变。"""
    retried = copy.deepcopy(request)
    retried["messages"] = [*retried["messages"], {"role": "assistant", "content": text},
                           {"role": "user", "content": correction_text(matched)}]
    return retried
