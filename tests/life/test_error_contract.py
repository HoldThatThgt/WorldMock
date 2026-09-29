"""错误合同的环境一侧与共用结构（docs/dev/SPEC-dayrun-fidelity.md 第四章「错误合同」）。

源码中会抛出的每个业务拒绝码都有具体的默认原因（机械核对：扫描 src 与 custom 中的拒绝构造，码由变量传入的构造处须在本文件
登记它能抛出的码，新增的码或构造处不补原因就通不过）；共用结构的键闭集与候选形状；真实 LifeEngine 上多项请求中第二项出错时
items 逐项写出（index 为 1）、整组仍原子、message 就是 reason、action_rejected 带 fix 与 items；受理结果的 resolved 与 notes
原样进入公开结果。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from tests.life.conftest import context, last_result, start_request  # noqa: E402
from tests.life.test_named_purchase import (_MILK, _Layer, _Scenario, _Supply, _browsed, _hidden,  # noqa: E402
                                            _layer_context, _round, _world)
from worldmock.errors import BusinessRuleError  # noqa: E402
from worldmock.life.error_contract import (CANDIDATE_KEYS, ERROR_KEYS, HOST_ERROR_CODES, HOST_NAME_CODES,  # noqa: E402
                                           ITEM_KEYS, Candidate, ErrorItem, ErrorReport, host_defaults, host_report,
                                           items_fix)
from worldmock.life.guidance import (GENERIC_REASON, Refusal, default_reason, items_refusal,  # noqa: E402
                                     refusal_for_code, rejection_guidance)
from worldmock.life.interaction_menu import NAMED_SUPPLY_KIND  # noqa: E402
from worldmock.life.named_purchase import (near_labels, resolve_named_rows, resolve_named_supply,  # noqa: E402
                                           supply_label)
from worldmock.life.result_projection import public_result  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
#: 构造业务拒绝的调用（第一个参数是错误码）；_refusal 是指名购买带候选标签的构造助手。
_CONSTRUCTORS = frozenset({"BusinessRuleError", "PreconditionError", "Refusal", "guided", "refusal_for_code",
                           "_refusal"})


def _callee(function: ast.expr) -> str | None:
    """调用对象的名称（名字或属性的末段）。"""
    if isinstance(function, ast.Name):
        return function.id
    return function.attr if isinstance(function, ast.Attribute) else None


def _literal_codes(path: str, callees: frozenset[str]) -> set[str]:
    """一个源文件中对 callees 的调用以字面量写出的第一个参数（错误码）。"""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return {call.args[0].value for call in ast.walk(tree)
            if isinstance(call, ast.Call) and _callee(call.func) in callees and call.args
            and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)}


#: 份量换算（categories.operators.scale_inventory_recipe 与 common.require_finite_number）会抛出的品类错误码。
_SCALE_CODES = _literal_codes("src/worldmock/categories/operators.py", frozenset({"CategoryError"})) | _literal_codes(
    "src/worldmock/categories/common.py", frozenset({"CategoryError"}))
#: 资源账本（resources.ledger 与分录校验）会抛出的错误码。
_LEDGER_CODES = _literal_codes("src/worldmock/resources/ledger.py", frozenset({"ResourceLedgerError"})) | (
    _literal_codes("src/worldmock/resources/models.py", frozenset({"ResourceLedgerError", "_require_amount"})))
#: 码不是字面量的构造处（文件:函数）→ 该处实际会抛出的码；空元组表示原样传下调用方给的码（调用方的字面码另行计入）。
#: 新增这类构造处必须在此登记，测试才会通过。
_DYNAMIC_SITES: dict[str, tuple[str, ...] | set[str]] = {
    "src/worldmock/life/guidance.py:error": (),
    "src/worldmock/life/guidance.py:guided": (),
    "src/worldmock/life/guidance.py:refusal_for_code": (),
    "src/worldmock/life/guidance.py:items_refusal": (),
    "src/worldmock/life/named_purchase.py:_refusal": (),
    "src/worldmock/life/tool_layer.py:_start_refusal": (),
    "src/worldmock/life/travel.py:route_for": ("ROUTE_UNREACHABLE", "ROUTE_INFORMATION_UNKNOWN"),
    "src/worldmock/life/inventory_recipe.py:prepare_inventory_recipe": _SCALE_CODES,
    "src/worldmock/life/inventory_recipe.py:_apply_inventory_ledger": _LEDGER_CODES,
}


def _constructor_calls(node: ast.AST, function: str):
    """逐个产出（所在函数名，拒绝构造调用）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Call) and _callee(child.func) in _CONSTRUCTORS:
            yield function, child
        inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
        yield from _constructor_calls(child, inner)


def _code_values(argument: ast.expr | None, constants: dict[str, str]) -> list[str] | None:
    """错误码参数的取值：字面量、模块级字符串常量或两支都是字面量的条件式；其余（变量传入）为 None。"""
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return [argument.value]
    if isinstance(argument, ast.Name) and argument.id in constants:
        return [constants[argument.id]]
    if isinstance(argument, ast.IfExp) and all(isinstance(side, ast.Constant) for side in (argument.body,
                                                                                            argument.orelse)):
        return [argument.body.value, argument.orelse.value]
    return None


def _raised_codes() -> tuple[set[str], set[str]]:
    """扫描 src 与 custom：（以字面量抛出的码，码由变量传入的构造处）。"""
    codes: set[str] = set()
    dynamic: set[str] = set()
    for base in ("src", "custom"):
        for path in sorted((ROOT / base).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            constants = {target.id: node.value.value for node in tree.body if isinstance(node, ast.Assign)
                         and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                         for target in node.targets if isinstance(target, ast.Name)}
            for function, call in _constructor_calls(tree, "<module>"):
                argument = call.args[0] if call.args else next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "code"), None)
                values = _code_values(argument, constants)
                if values is None:
                    dynamic.add(f"{path.relative_to(ROOT).as_posix()}:{function}")
                else:
                    codes.update(values)
    return codes, dynamic


def test_every_business_rejection_code_raised_in_the_source_has_a_specific_reason() -> None:
    """源码中会抛出的每个业务拒绝码（字面量抛出的与登记的变量构造处能抛出的）都有具体的默认原因，不退回到兜底句；拒绝
    结果与工具层可用性说明取同一原因（docs/dev/SPEC-dayrun-fidelity.md 第四章：补全所有会被抛出的码的具体默认原因）。"""
    literal, dynamic = _raised_codes()
    assert dynamic == set(_DYNAMIC_SITES), "register every variable-code raise site with the codes it can raise"
    codes = literal | {code for values in _DYNAMIC_SITES.values() for code in values}
    assert len(literal) >= 99 and {"ROUTE_UNREACHABLE", "NEGATIVE_BALANCE", "CATEGORY_NUMBER_INVALID"} <= codes
    assert sorted(code for code in codes if default_reason(code) == GENERIC_REASON) == []
    for code in sorted(codes):
        guidance = rejection_guidance(BusinessRuleError(code))
        assert guidance["reason"] == refusal_for_code(code).reason == default_reason(code), code
        assert guidance["next_step"] is None or guidance["next_step"].strip(), code
        assert guidance["fix"] is None or guidance["fix"].strip(), code
    assert default_reason("NOT_A_RAISED_CODE") == GENERIC_REASON


def test_error_structure_keys_and_candidate_shape_are_closed() -> None:
    """共用结构写出 error_code、reason、fix、items、unmet 五个键；逐项明细与可照填候选的键闭集；候选至多 3 个，序号从
    0 起；没有改法时 fix 写 null，没有明细时 items 写 []。"""
    candidate = Candidate("活动：洗碗", "interact", "进行", note="约 20 分钟")
    item = ErrorItem(1, "targets", "家务：收拾碗筷与厨房", "NAME_NOT_FOUND", (candidate,))
    report = ErrorReport("NAME_NOT_FOUND", "第 2 项「家务：收拾碗筷与厨房」不是本轮列出的对象", None, (item,))
    payload = report.payload()
    assert tuple(payload) == ERROR_KEYS and payload["fix"] is None and payload["unmet"] == []
    [row] = payload["items"]
    assert tuple(row) == ITEM_KEYS and row["index"] == 1
    assert tuple(row["candidates"][0])[:5] == CANDIDATE_KEYS and row["candidates"][0]["note"] == "约 20 分钟"
    assert "note" not in Candidate("活动：洗碗", "interact", "进行").payload()
    with pytest.raises(ValueError, match="at most three"):
        ErrorItem(0, "targets", "x", "NAME_NOT_FOUND", (candidate,) * 4)
    with pytest.raises(ValueError, match="non-negative"):
        ErrorItem(-1, "targets", "x", "NAME_NOT_FOUND")
    with pytest.raises(ValueError, match="code and a reason"):
        ErrorReport("NAME_NOT_FOUND", "")
    assert ErrorReport("SUPPLY_NOT_FOUND", "没有", items=()).payload()["items"] == []


def test_host_codes_are_the_closed_set_with_a_reason_and_a_fix() -> None:
    """宿主错误码闭集恰为规格列出的 11 个，每个都有具体的默认原因与改法；闭集之外的码是宿主的程序错误；名称类宿主码是
    其中五个。"""
    assert HOST_ERROR_CODES == ("ARGUMENTS_NOT_JSON", "ARGUMENTS_INVALID", "TOOL_CALL_REQUIRED", "TOO_MANY_TARGETS",
                                "NAME_NOT_FOUND", "NAME_AMBIGUOUS", "WAY_NOT_OFFERED", "ROUTE_FIELDS_INVALID",
                                "SUPPLY_SOURCE_MISMATCH", "SUBJECT_NOT_KNOWN", "OUTPUT_TRUNCATED")
    assert HOST_NAME_CODES == {"NAME_NOT_FOUND", "NAME_AMBIGUOUS", "WAY_NOT_OFFERED", "SUPPLY_SOURCE_MISMATCH",
                               "SUBJECT_NOT_KNOWN"}
    for code in HOST_ERROR_CODES:
        reason, fix = host_defaults(code)
        assert reason and fix and reason != GENERIC_REASON
        assert host_report(code).payload() == {"error_code": code, "reason": reason, "fix": fix, "items": [],
                                               "unmet": []}
    assert host_defaults("OUTPUT_TRUNCATED") == ("输出在思考阶段用尽，尚未调用工具", "只调用一个工具")
    with pytest.raises(ValueError, match="closed set"):
        host_report("ACTION_REQUEST_INVALID")


def test_item_reasons_and_fixes_carry_the_one_based_item_number_only_for_several_items() -> None:
    """逐项原因写「第 N 项「原文」：原因」（请求只有一项时不写序号），改法按码写「第 N 项<改法>」；两码混合时各写一句。"""
    second = ErrorItem(1, "targets", "咖啡加冰", "SUPPLY_NOT_FOUND")
    refusal = items_refusal([(second, default_reason("SUPPLY_NOT_FOUND"))], 2)
    assert (refusal.code, refusal.reason) == ("SUPPLY_NOT_FOUND", "第 2 项「咖啡加冰」：没有找到这个名字的商品")
    assert refusal.fix == "第 2 项改用候选里的完整标签，或者照本轮列出的写法写商品"
    single = items_refusal([(ErrorItem(0, "targets", "咖啡加冰", "SUPPLY_NOT_FOUND"), "没有找到这个名字的商品")], 1)
    assert single.reason == "「咖啡加冰」：没有找到这个名字的商品"
    mixed = (ErrorItem(0, "targets", "a", "SUPPLY_NOT_FOUND"), ErrorItem(1, "targets", "b", "SUPPLY_NAME_AMBIGUOUS"))
    fixes = {"SUPPLY_NOT_FOUND": "改用候选", "SUPPLY_NAME_AMBIGUOUS": "写明门店"}
    assert items_fix(mixed, 2, fixes) == "第 1 项改用候选；第 2 项写明门店"
    assert items_fix(mixed, 2, {}) is None


def test_guidance_passes_fix_items_resolved_and_notes_from_the_refusal() -> None:
    """引导性拒绝写明的改法、逐项明细、名称归一与说明原样进入公开的 guidance；没有写明改法时取该码的默认改法；世界条件
    造成的拒绝没有改法，fix 为 null。"""
    item = ErrorItem(0, "targets", "咖啡", "SUPPLY_NOT_FOUND", (Candidate("好德出售的咖啡", "obtain", "远程下单",
                                                                           None, "自己"),))
    error = Refusal("SUPPLY_NOT_FOUND", "没有", fix="改用候选", items=(item,), suggested={
        "next_step": "从候选里选", "resolved": [{"written": "沈昊", "as": "联系人：沈昊"}], "notes": ["from 可以写 null"]}
                    ).error()
    guidance = rejection_guidance(error)
    assert guidance == {"reason": "没有", "next_step": "从候选里选", "fix": "改用候选", "items": [item.payload()],
                        "resolved": [{"written": "沈昊", "as": "联系人：沈昊"}], "notes": ["from 可以写 null"]}
    assert rejection_guidance(BusinessRuleError("TASK_NOT_FOUND"))["fix"] == (
        "照工具层、待办层或记忆层中的写法填写任务、目标或活动的名称")
    assert rejection_guidance(BusinessRuleError("MERCHANT_CLOSED")) == {"reason": "店没开门", "next_step": None,
                                                                        "fix": None}


def _two_item_purchase(targets: list[str]):
    """两项远程下单送给自己的 start_activity。"""
    return start_request(targets=targets, actions=["obtain", "obtain"], ways=["远程下单", "远程下单"])


def test_second_named_item_failing_is_reported_at_index_one_and_the_group_stays_atomic(engine_factory) -> None:
    """两项指名购买中第一项能解析、第二项找不到：一次 SUPPLY_NOT_FOUND 拒绝，items 只有第二项（index 为 1），原因与改法
    写「第 2 项」，候选是可照填的请求；整组原子，第一项也不下单；message 就是 reason，action_rejected 与公开结果都带 fix
    与 items（docs/dev/SPEC-dayrun-fidelity.md 第四章）。"""
    engine = engine_factory()
    listed = _browsed(engine)
    label = supply_label(_layer_context(engine), _hidden(engine, listed, unique_name=True))
    unknown = f"{_MILK}加冰"
    before = _world(engine)
    _, result = _round(engine, "second-fails", _two_item_purchase([label, unknown]))
    assert (result.status, result.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    [item] = result.data["items"]
    assert (item["index"], item["field"], item["written"], item["error_code"]) == (1, "targets", unknown,
                                                                                    "SUPPLY_NOT_FOUND")
    assert [row["target"] for row in item["candidates"]] == near_labels(_layer_context(engine), unknown, "delivery",
                                                                         "home")
    assert all(tuple(row) == ("target", "action", "way", "from", "to") for row in item["candidates"])
    assert result.data["reason"] == f"第 2 项「{unknown}」：没有找到这个名字的商品"
    assert result.data["fix"] == "第 2 项改用候选里的完整标签，或者照本轮列出的写法写商品"
    assert result.message == result.data["reason"] and "Action rejected" not in result.message
    assert _world(engine) == before and "orders" not in result.data
    [rejected] = [event for event in engine.store.read_agent_events("action_rejected", 1)
                  if event.action_id == "action-second-fails"]
    assert (rejected.data["error_code"], rejected.data["fix"], rejected.data["items"]) == (
        "SUPPLY_NOT_FOUND", result.data["fix"], [item])
    details = last_result(engine.observe(context(engine, "second-fails-seen")))["details"]
    assert (details["reason"], details["fix"], details["items"]) == (result.data["reason"], result.data["fix"], [item])


def test_every_failing_named_item_is_reported_in_one_rejection(engine_factory) -> None:
    """两项指名购买都找不到：一次拒绝逐项报全部名称类错误（index 0 与 1），不止第一条。"""
    engine = engine_factory()
    _browsed(engine)
    names = [f"{_MILK}加冰", "冰镇菠萝啤"]
    _, result = _round(engine, "both-fail", _two_item_purchase(names))
    assert (result.status, result.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    assert [(item["index"], item["written"]) for item in result.data["items"]] == [(0, names[0]), (1, names[1])]
    assert result.data["reason"] == "；".join(f"第 {index + 1} 项「{name}」：没有找到这个名字的商品"
                                              for index, name in enumerate(names))
    assert result.data["fix"] == "第 1、2 项改用候选里的完整标签，或者照本轮列出的写法写商品"


def test_named_item_candidates_use_a_way_the_supply_supports() -> None:
    """按标签找到的供给不支持所写的方式（FULFILLMENT_NOT_SUPPORTED）：该项候选是同一标签换成它支持的方式，from 为 null、
    to 照写，可以照填；原因写出这一项的原文。"""
    layer = _Layer(_Scenario([_Supply("s1", "p1", fulfillment_modes=("pickup",))], {"s1": "好德"}, {"p1": _MILK}))
    label = f"好德出售的{_MILK}"
    row = {"kind": NAMED_SUPPLY_KIND, "object": label, "selected_action": {"label": "远程下单"}, "to": "人物：李凡"}
    with pytest.raises(BusinessRuleError, match="FULFILLMENT_NOT_SUPPORTED") as refused:
        resolve_named_rows(layer, [row], lambda item: resolve_named_supply(layer, item["object"], "远程下单", None,
                                                                            "home"))
    [item] = refused.value.suggested["items"]
    assert item["candidates"] == [{"target": label, "action": "obtain", "way": "到店", "from": None,
                                   "to": "人物：李凡"}]
    assert refused.value.public_reason == f"「{label}」：这件商品不能用这种方式买"
    assert refused.value.suggested["fix"] == "改用这件商品支持的取得方式"


def test_accepted_results_pass_resolved_and_notes_into_public_details(engine_factory) -> None:
    """受理结果里名称归一 resolved（[{written, as}]）与说明 notes 原样进入公开结果 details；拒绝结果的 fix 与 items 同样
    进入（进度层 result.details 键闭集，docs/dev/SPEC-dayrun-fidelity.md 第四章）。"""
    engine = engine_factory()
    resolved = [{"written": "沈昊", "as": "联系人：沈昊"}]
    accepted = {"occurred_at": engine.state.current_time, "status": "completed", "error_code": None,
                "message": "Message delivered", "data": {"resolved": resolved, "notes": ["from 可以写 null"]}}
    assert public_result(accepted, engine.scenario)["details"] == {"resolved": resolved,
                                                                    "notes": ["from 可以写 null"]}
    item = {"index": 0, "field": "targets", "written": "咖啡", "error_code": "SUPPLY_NOT_FOUND", "candidates": []}
    rejected = {"occurred_at": engine.state.current_time, "status": "rejected", "error_code": "SUPPLY_NOT_FOUND",
                "message": "没有", "data": {"reason": "没有", "next_step": None, "fix": "改用候选", "items": [item],
                                            "unmet": ["x"], "suggested": {"candidates": ["内部"]}}}
    assert public_result(rejected, engine.scenario)["details"] == {"reason": "没有", "next_step": None,
                                                                    "fix": "改用候选", "items": [item]}
