"""@brief 把实际动作结果投影成人物可见事实，不公开内部状态；购买写明下单 app 或到店，支付 app 仍不公开。

环境对一条开始请求先后写两条结果，进度层只显示最新的一条（docs/design/06-agent.md「进度、待办与记忆」）：
受理时的第一条带 planned 与开始的那条内容 content（购买另带报价）；活动终结时的第二条带做过的内容 contents、
付款后的订单或到达。contents 至多 TITLE_LIST_LIMIT 行，多出的行写成计数 contents_omitted「还有 N 条没列」；完整的
放过列表留在结果事件的活动快照里。明细在投影时由结果事件里的活动快照和冻结目录算出，事件本身不带这些键。
"""

from __future__ import annotations

from typing import Any

from worldmock.environment import EnvironmentInputs
from worldmock.footprints.display_wording import TITLE_LIST_LIMIT
from worldmock.life.action_evidence import placed_orders
from worldmock.life.query_answer import query_details
from worldmock.life.readable import duration_phrase, iso_time, money_phrase, portion_phrase

#: 以下载荷已经由各所属行为构造为人物可见名称与文字。
_PASSTHROUGH_DETAILS = ("recall", "reason", "next_step", "fix", "items", "outcome", "resolved", "notes",
                        "errands")


def public_result(result: dict[str, Any], inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 只给本次结果、实际公开明细和运行时区的时刻。"""
    return {"occurred_at": iso_time(result["occurred_at"], inputs.config.timezone), "status": result["status"],
            "error_code": result.get("error_code"), "message": result["message"],
            "details": _details(result, inputs)}


def _details(result: dict[str, Any], inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 这条动作下的订单、计划时长、做过的内容与到达由实际快照派生，系统足迹和在驿站领取的包裹不进入购买明细。

    进行中的第一条结果写 planned 与 content；终态（completed、cancelled、failed）结果里 interact 活动的快照写 contents
    （多出的行另写 contents_omitted，紧跟在 contents 之后），查询的快照投影 query：有答案的是查完的形状，没有的是「没有查完」。
    """
    payload = result.get("data") or {}
    details = {key: payload[key] for key in _PASSTHROUGH_DETAILS if key in payload}
    orders = placed_orders(payload)
    if orders:
        details["purchases"] = [_purchase(order, inputs) for order in orders]
    if result["status"] == "in_progress":
        details["planned"] = [{"name": item["name"], "duration": duration_phrase(item["planned_duration_seconds"])}
                              for item in _activity_rows(payload)]
        details.update(_interact_details(_activity_rows(payload), inputs))
    if result["status"] in ("completed", "cancelled", "failed"):
        details.update(_done_contents(_activity_rows(payload), inputs))
    if payload.get("resumed"):
        details["planned"] = [{"name": item["name"], "duration": duration_phrase(item["planned_duration_seconds"])}
                              for item in payload["resumed"]]
    snapshot = payload.get("activity")
    if snapshot is not None and snapshot["kind"] == "query":
        details["query"] = query_details(snapshot, inputs)
    arrivals = [item["data"]["arrival"] for item in _activity_rows(payload) if item["data"].get("arrival")]
    if arrivals:
        row = arrivals[0]
        details["arrival"] = {"place": inputs.catalog.locations[row["location_id"]].display_name,
                              "access": "进入" if row["access_status"] == "allowed" else "没能进去"}
    return details


def _interact_details(activities: list[dict], inputs: EnvironmentInputs) -> dict:
    """@brief 开始结果展示实际开始的那条内容。"""
    activity = next((row for row in activities if row.get("kind") == "interact"), None)
    if activity is None:
        return {}
    data, catalog = activity["data"], inputs.catalog
    app = None if data["app_id"] is None else catalog.apps[data["app_id"]].name
    content = catalog.contents[data["content_id"]]
    return {"content": {"app": app, "title": content.title, "summary": content.summary,
                        "duration": duration_phrase(content.duration_seconds),
                        "pushed": data["pinned_by_push_id"] is not None}}


def _done_contents(activities: list[dict], inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 终态结果里这项 interact 活动放过或做过的内容：{}、{"contents": [...]}，或再带 contents_omitted。

    全部行按做的先后各一行 {title, summary, duration, finished}：先是快照 played 的各项（做到末尾，finished 为真）；
    快照状态不是 completed 且当前这条在这项活动里确有推进（media_position_seconds 大于 start_position_seconds）时再加
    当前这条，finished 为假。duration 是这条内容登记的全长。只写前 TITLE_LIST_LIMIT 行，没写出的行不查目录；有没写出的
    行时另写 contents_omitted「还有 N 条没列」，N 是全部行数减去写出的行数。没有 interact 活动或一行都没有时为空字典；
    每一项 interact 活动都有内容。
    """
    activity = next((row for row in activities if row.get("kind") == "interact"), None)
    if activity is None:
        return {}
    data, contents = activity["data"], inputs.catalog.contents
    done = [(item["content_id"], True) for item in data["played"]]
    if activity["status"] != "completed" and activity["media_position_seconds"] > data["start_position_seconds"]:
        done.append((data["content_id"], False))
    rows = [{"title": contents[key].title, "summary": contents[key].summary,
             "duration": duration_phrase(contents[key].duration_seconds), "finished": finished}
            for key, finished in done[:TITLE_LIST_LIMIT]]
    omitted = len(done) - len(rows)
    if not rows:
        return {}
    return {"contents": rows} if omitted == 0 else {"contents": rows, "contents_omitted": f"还有 {omitted} 条没列"}


def _activity_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 单项和整组结果统一读取实际活动快照。"""
    return [payload["activity"]] if "activity" in payload else payload.get("activities", [])


def _purchase(order: dict[str, Any], inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 商品、份数、渠道、冻结送达地点和时刻、报价或实际付款；写明下单 app 或到店，不含支付 app、账户与身份。

    渠道取自商品履约记录上的下单 app，不取人物写的 prefer：经 app 下单的是它的完整名称，到店购买是「到店」。
    """
    row = {"product": inputs.catalog.goods[order["goods_id"]].name,
           "quantity": portion_phrase(order["quantity"]),
           "channel": "到店" if order["app_id"] is None else inputs.catalog.apps[order["app_id"]].name,
           "destination": inputs.catalog.locations[order["destination_id"]].display_name,
           "expected_at": iso_time(order["expected_at"], inputs.config.timezone)}
    row["paid" if order["paid_at"] is not None else "price"] = money_phrase(order["total_price_fen"])
    return row
