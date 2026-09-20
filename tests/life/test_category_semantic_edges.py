"""@brief 品类语义公共接口的严格失败边界测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from worldmock.errors import IntegrityError
from worldmock.life.semantics import ScenarioSemantics, load_runtime_scenario
from worldmock.life.travel_time import parse_travel_time_profile

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def semantics() -> ScenarioSemantics:
    """@brief 加载正式半日场景与同目录冻结语义。"""
    result, _ = load_runtime_scenario(
        ROOT / "scenarios/multi-person-half-day.json", ROOT / "catalog",
    )
    return result


def test_with_price_translates_invalid_integer_input(semantics: ScenarioSemantics) -> None:
    """价格替换把正浮点分值的算子拒绝翻译为完整性错误。"""
    product_id = semantics.scenario.products[0].product_id
    with pytest.raises(IntegrityError, match="Price change exchange failed: EXCHANGE_INPUT_INVALID"):
        semantics.with_price(product_id, 1.5, "invalid-price")


def test_contract_payment_rejects_incompatible_rule(
    semantics: ScenarioSemantics, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """合同付款在复查结果不是合同算子时失败关闭。"""
    travel = semantics.travel_match("walking")
    monkeypatch.setattr(ScenarioSemantics, "contract_match", lambda self, task: travel)
    task = SimpleNamespace(task_id="incompatible-contract")

    with pytest.raises(IntegrityError, match="resolved to an incompatible exchange rule"):
        semantics.contract_payment(task, 0, None)


def test_travel_match_rejects_unknown_mode(semantics: ScenarioSemantics) -> None:
    """出行规则复查拒绝闭集之外的方式。"""
    with pytest.raises(IntegrityError, match="Travel mode cycling has no category action"):
        semantics.travel_match("cycling")


def test_travel_time_factor_rejects_missing_branch() -> None:
    """时长表查询拒绝不存在的方式与小时分支。"""
    profile = parse_travel_time_profile((ROOT / "catalog/rules/travel-time.toml").read_bytes())
    with pytest.raises(IntegrityError, match="has no branch for cycling_12"):
        profile.factor("cycling", 12)


def test_travel_time_parser_rejects_malformed_bytes() -> None:
    """时长表解析器把非 UTF-8 输入归一为稳定完整性错误。"""
    with pytest.raises(IntegrityError, match="Travel time profile is malformed"):
        parse_travel_time_profile(b"\xff")


@pytest.mark.parametrize(
    ("provenance", "message"),
    (
        ('provenance = "missing"', "profile provenance is missing"),
        ('[provenance]\nfactors = "wrong"', "provenance does not cover /factors"),
    ),
)
def test_travel_time_parser_rejects_invalid_provenance(
    provenance: str, message: str,
) -> None:
    """时长表要求来源容器和 factors 记录同时满足冻结结构。"""
    with pytest.raises(IntegrityError, match=message):
        parse_travel_time_profile(_profile_bytes(provenance))


def _profile_bytes(provenance: str) -> bytes:
    """@brief 构造数值分支合法、来源结构可定向变异的 TOML。"""
    factors = ["walking = 1.0"]
    factors.extend(f"driving_{hour:02d} = 1.0" for hour in range(24))
    document = "\n".join(("version = 1", provenance, "[factors]", *factors, ""))
    return document.encode("utf-8")
