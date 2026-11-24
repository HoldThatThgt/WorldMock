"""@brief 独立搜索子会话的系统提示词、工具用途说明、隔离用户消息构造与指纹计算。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from worldmock.agents.prompt.narration import render_layer_json
from worldmock.agents.protocol.agent_loop import build_tool_schema
from worldmock.state.decisions.barrier import SearchTaskSpec
from worldmock.state.interaction.search_requests import SEARCH_SESSION_MODELS

#: 搜索子会话内部工具的用途说明，只描述用途与边界，不包含任何报价或规则数值。
SEARCH_TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_goods": "按多近义关键词与可选条件分页检索同城门店及线上应用的可售商品。",
    "inspect_goods_offers": "批量试算候选商品的实时价格、运费、营业与履约送达报价。",
    "search_places": "按关键词或地点类别检索同城地点与直达邻城枢纽的营业、出行摘要。",
    "submit_search_results": "提交经过白名单验证的最终候选商品与地点标识并结束搜索子会话。",
}

#: 搜索子会话系统层规则：强隔离输入边界并由 Fact Guard 负责真实数值投影。
SEARCH_SYSTEM_LAYER: dict[str, Any] = {
    "layer": "搜索系统层",
    "role": "你是商品与地点搜索引擎专员，在限定回合预算内从同城门店、线上应用及同城与直达邻城地点中筛选编号。",
    "rules": [
        "只依据工具返回与当前输入事实检索，严禁编造商品编号、地点编号或任何价格、运费、评分、配送时长与可下单状态。",
        ("若 scoped_app_id 非空，所有商品检索结果必须属于该限定应用；"
         "不限定时同时检索同城门店（到店、外卖）与线上应用（快递、即时到账）全部可用途径。"),
        ("先调用 search_goods 或 search_places（含同城地点与直达邻城枢纽）发现候选编号，"
         "再对关键候选商品调用 inspect_goods_offers 检查实时营业与报价。"),
        "最终必须调用 submit_search_results 提交候选 goods_ids（至多 20 项）与 place_ids（至多 10 项）以及简短说明 summary。",
        "submit_search_results 的 summary 只写自然语言推荐依据、替代说明或未找到原因，严禁写出价格、运费、评分、时长或可买结论。",
        "若真实目录没有命中任何匹配项，仍须提交空的 goods_ids/place_ids 并在 summary 明确说明未找到原因。",
    ],
}


def search_tool_schemas() -> list[dict[str, Any]]:
    """@brief 返回搜索子会话四个严格原生工具 Schema 列表。"""
    return [build_tool_schema(name, SEARCH_TOOL_DESCRIPTIONS[name], model)
            for name, model in SEARCH_SESSION_MODELS.items()]


def search_system_message() -> dict[str, str]:
    """@brief 构造搜索子会话的 system 消息。"""
    return {"role": "system", "content": render_layer_json(SEARCH_SYSTEM_LAYER)}


def search_user_message(task: SearchTaskSpec) -> dict[str, str]:
    """@brief 严格隔离构造搜索子会话的 user 消息，绝不泄漏人设、记忆与余额。"""
    payload = {
        "query": task.query,
        "prefer": task.prefer,
        "scoped_app_id": task.scoped_app_id,
        "location_name": task.location_name,
        "opened_at_clock": task.opened_at_clock,
        "local_date": task.local_date,
        "weather_label": task.weather_label,
        "open_apps": list(task.open_apps),
        "category_overview": dict(task.category_overview),
        "turn_budget": task.turn_budget,
    }
    return {"role": "user", "content": render_layer_json(payload)}


def search_prompt_sha256() -> str:
    """@brief 计算搜索系统提示模板的确定性 SHA-256 指纹。"""
    encoded = json.dumps({"search": SEARCH_SYSTEM_LAYER}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def search_tool_schema_sha256() -> str:
    """@brief 计算搜索子会话四个工具 Schema 的确定性 SHA-256 指纹。"""
    encoded = json.dumps(
        {"search_tool_schemas": search_tool_schemas()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()
