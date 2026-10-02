"""预排事件子系统：提取等价、五类新事件效果与缺省场景零变化。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from worldmock.catalog import load_catalog
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.basket import purchase_order
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    ModelIdentity,
    PurchaseOrderRequest,
    RunIdentity,
    ScheduledKind,
)
from worldmock.life.scenario import LifeScenario, PriceChangeConfig
from worldmock.life.semantics import assemble_semantics, load_runtime_scenario
from worldmock.scheduling.compile import compile_schedule
from tests.life.conftest import advance_next

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DUE_TEXT = "2026-01-05T10:00:00+08:00"


def _document(base_name: str) -> dict:
    """读入正式场景文档的可变深拷贝。"""
    return json.loads((_PROJECT_ROOT / "scenarios" / base_name).read_text(encoding="utf-8"))


def _write_scenario(document: dict, tmp_path: Path) -> Path:
    """把改写文档与冻结目录复制到隔离结构中并返回路径。"""
    document["corpus_root"] = str(_PROJECT_ROOT / "corpora")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir(parents=True)
    shutil.copytree(_PROJECT_ROOT / "catalog", tmp_path / "catalog")
    path = scenarios / "custom.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _identity(document: dict, run_id: str) -> RunIdentity:
    """按文档窗口与人物构造运行身份。"""
    return RunIdentity(
        run_id=run_id,
        source_group_id=f"group-{run_id}",
        agent_ids=tuple(item["agent_id"] for item in document["agents"]),
        start_at=document["start_at"],
        end_at=document["end_at"],
        timezone=document["timezone"],
        code_sha256="2" * 64,
        model_identity=ModelIdentity(
            provider="openai_compatible",
            base_url="https://api.deepseek.com/beta",
            requested_model="deepseek-flash",
            thinking="enabled",
            tool_choice="auto",
            max_retries=0,
            prompt_sha256="0" * 64,
            tool_schema_sha256="1" * 64,
        ),
    )


def _custom_engine(base_name: str, mutate, tmp_path: Path, run_id: str = "sched") -> LifeEngine:
    """从改写文档建立隔离运行。"""
    document = _document(base_name)
    mutate(document)
    path = _write_scenario(document, tmp_path)
    return LifeEngine.create(path, tmp_path / f"run-{run_id}", _identity(document, run_id))


def _stage(engine: LifeEngine, scheduled_id: str) -> int:
    """把世界直接置于目标事件前一分钟，返回目标时刻。"""
    event = next(item for item in engine.state.pending_events
                 if item.scheduled_id == scheduled_id)
    staged = engine.state.model_copy(deep=True)
    staged.current_time = event.due_at - 60
    staged.pending_events = [event]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(
            update={"next_decision_at": event.due_at + 3600, "body_settled_at": staged.current_time}, deep=True)
    engine.state = staged
    return event.due_at


def _fire(engine: LifeEngine, scheduled_id: str) -> int:
    """把世界置于目标事件前一分钟并推进到点火。"""
    due = _stage(engine, scheduled_id)
    advance_next(engine)
    return due


def _environment_events(engine: LifeEngine, kind: str) -> list:
    """读取足迹中给定 kind 的环境事件。"""
    return [event for event in engine.store.read_events(engine.store.last_event_seq)
            if event.event_type == "environment_event" and event.data.get("kind") == kind]


def _bare_world(engine: LifeEngine) -> dict:
    """剔除时钟、版本与决策节律簿记后的可比世界快照。

    修订合同新增的节律/情绪/账本簿记（next_decision_at、mood、discomfort、
    felt 参照等）在结算边界自然演化，不属于预排事件语义。
    """
    payload = engine.state.model_dump(mode="json", exclude={"version", "current_time",
                                                            "pending_events"})
    volatile = {"next_decision_at", "pending_wake_reasons", "mood_updated_at",
                "discomfort_started_at", "discomfort_pulse_hours",
                "last_decision_time", "felt_bands_at_last_decision",
                "people_at_last_decision", "known_task_ids", "body_settled_at", "body"}
    for agent in payload["agents"].values():
        for key in volatile:
            agent.pop(key, None)

    return payload


def _midnight_epoch(date_text: str, timezone: str) -> int:
    """按场景时区计算自然日 00:00 的纪元秒。"""
    return int(datetime.fromisoformat(f"{date_text}T00:00:00")
               .replace(tzinfo=ZoneInfo(timezone)).timestamp())


# ---------------------------------------------------------------------------
# 提取等价与缺省零变化
# ---------------------------------------------------------------------------


def test_work_availability_reaches_only_the_assigned_person() -> None:
    """工作合同到期前只向场景指定的人物发布一次。"""
    for name in ("multi-person.json", "white-collar-12-weeks.json"):
        semantics, _ = load_runtime_scenario(_PROJECT_ROOT / "scenarios" / name)
        scenario = semantics.scenario
        start = int(scenario.start_at.timestamp())
        end = int(scenario.end_at.timestamp())
        compiled = compile_schedule(scenario, start, end)
        available = [event for event in compiled if event.kind == ScheduledKind.WORK_AVAILABLE]
        for task in scenario.tasks:
            matches = [event for event in available if event.data["task_id"] == task.task_id]
            expected_count = int(start < int(task.available_at.timestamp()) < end)
            assert len(matches) == expected_count
            if matches:
                assert matches[0].agent_id == int(task.assignee_agent_id.removeprefix("agent:"))


def test_calendar_flags_compile_at_local_midnight() -> None:
    """每个有旗标的自然日在场景时区 00:00 各一条，旗标名与观察侧一致。"""
    semantics, _ = load_runtime_scenario(_PROJECT_ROOT / "scenarios/white-collar-12-weeks.json")
    scenario = semantics.scenario
    start = int(scenario.start_at.timestamp())
    end = int(scenario.end_at.timestamp())
    calendar = scenario.calendar
    expected: dict[str, list[str]] = {}
    for flag, dates in (("spring_festival", calendar.spring_festival_dates),
                        ("makeup_workday", calendar.makeup_work_dates),
                        ("overtime_opportunity", calendar.overtime_opportunity_dates)):
        for value in dates:
            expected.setdefault(value, []).append(flag)
    for flag, value in (("sick_leave", calendar.sick_leave_date),
                        ("personal_leave", calendar.personal_leave_date),
                        ("social_dinner_opportunity", calendar.social_dinner_date)):
        expected.setdefault(value, []).append(flag)
    flags = [item for item in compile_schedule(scenario, start, end)
             if item.kind == ScheduledKind.CALENDAR_FLAG]
    assert len(flags) == len(expected)
    for event in flags:
        assert event.scheduled_id == f"calendar-{event.data['date']}"
        assert event.data["flags"] == sorted(expected[event.data["date"]])
        assert event.due_at == _midnight_epoch(event.data["date"], scenario.timezone)


def test_calendar_flag_dispatch_is_zero_state_audit_event(tmp_path: Path) -> None:
    """日历旗标到点零状态变更，只追加审计环境事件；观察侧注入路径保持。

    旗标在本地零点到期，该边界同时是零点计划边界：零点计划为每个人物登记新一天的计划
    （docs/dev/SPEC-time-and-energy.md 第四章），除这项登记外世界不变。
    """
    engine = _custom_engine("white-collar-12-weeks.json", lambda doc: None, tmp_path)
    assert _environment_events(engine, "calendar_flag") == []
    due = _stage(engine, "calendar-2026-01-24")
    before = _bare_world(engine)
    energy = {identity: agent.body.energy_kcal for identity, agent in engine.state.agents.items()}
    advance_next(engine)
    for identity, agent in engine.state.agents.items():
        assert agent.body.energy_kcal == pytest.approx(
            energy[identity] - engine.semantics.body.basal_kcal_per_hour / 60)
    assert engine.state.current_time == due
    events = _environment_events(engine, "calendar_flag")
    assert len(events) == 1
    assert events[0].data["flags"] == ["overtime_opportunity"]
    assert events[0].data["scheduled_id"] == "calendar-2026-01-24"
    registered = _environment_events(engine, "day_plan_generated")
    assert [(event.agent_id, event.occurred_at) for event in registered] == [
        (identity, due) for identity in sorted(engine.state.agents)]
    next_midnight = _midnight_epoch("2026-01-25", engine.scenario.timezone)
    for agent in engine.state.agents.values():
        assert agent.plan_submitted_dates == {"2026-01-24"}
        assert agent.day_plan.day_end == next_midnight and agent.day_plan.goals == ()
    after = _bare_world(engine)
    for world in (before, after):
        for agent in world["agents"].values():
            agent.pop("day_plan")
            agent.pop("plan_submitted_dates")
    assert after == before
    observation = engine._observation_data(engine.state.agents[1])
    assert observation["calendar_context"]["overtime_opportunity"] is True
    assert observation["calendar_context"]["spring_festival"] is False


# ---------------------------------------------------------------------------
# BILL_DUE 与 PAYROLL
# ---------------------------------------------------------------------------


def test_bill_due_settles_transfer_via_ledger(tmp_path: Path) -> None:
    """余额充足时到期账单经结算通道双边扣款并留下零和证据。"""

    def mutate(document: dict) -> None:
        document["bills"] = [{"bill_id": "rent-january", "account_id": "agent:1",
                              "payee_account_id": "merchant:grocery", "amount_fen": 50_000,
                              "due_at": _DUE_TEXT}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    _fire(engine, "bill-rent-january")
    assert engine.state.accounts["agent:1"] == 800_000 - 50_000
    assert engine.state.accounts["merchant:grocery"] == 10_000_000 + 50_000
    events = _environment_events(engine, "bill_due")
    assert len(events) == 1
    assert events[0].data["bill_paid"] is True
    assert events[0].data["resource_changes"] == {
        "account:agent:1": -50_000, "account:merchant:grocery": 50_000}


def test_bill_due_to_external_anchor_absorbs_credit(tmp_path: Path) -> None:
    """docs/design/04-simulation-engine.md「预排事件」的房租/水电形态：付款入外部锚点，锚点侧吸收不入运行账。"""

    def mutate(document: dict) -> None:
        document["bills"] = [{"bill_id": "utility-jan", "account_id": "agent:1",
                              "payee_account_id": "external:scenario", "amount_fen": 12_000,
                              "due_at": _DUE_TEXT}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    _fire(engine, "bill-utility-jan")
    assert engine.state.accounts["agent:1"] == 800_000 - 12_000
    assert "external:scenario" not in engine.state.accounts
    events = _environment_events(engine, "bill_due")
    assert events[0].data["bill_paid"] is True
    assert sum(events[0].data["resource_changes"].values()) == 0


def test_bill_due_insufficient_funds_refuses_without_crash(tmp_path: Path) -> None:
    """余额不足时拒付：事件照发、resource_changes 为空、余额不动、运行不崩。"""

    def mutate(document: dict) -> None:
        document["bills"] = [{"bill_id": "rent-impossible", "account_id": "agent:1",
                              "payee_account_id": "merchant:grocery", "amount_fen": 900_000_000,
                              "due_at": _DUE_TEXT}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    due = _stage(engine, "bill-rent-impossible")
    before = _bare_world(engine)
    energy = {identity: agent.body.energy_kcal for identity, agent in engine.state.agents.items()}
    advance_next(engine)
    for identity, agent in engine.state.agents.items():
        assert agent.body.energy_kcal == pytest.approx(
            energy[identity] - engine.semantics.body.basal_kcal_per_hour / 60)
    assert engine.state.current_time == due
    assert engine.state.accounts["agent:1"] == 800_000
    assert engine.state.accounts["merchant:grocery"] == 10_000_000
    events = _environment_events(engine, "bill_due")
    assert len(events) == 1
    assert events[0].data["bill_paid"] is False
    assert events[0].data["resource_changes"] == {}
    assert _bare_world(engine) == before


def test_payroll_credits_agent_account(tmp_path: Path) -> None:
    """到点工资从雇主账户向人物账户入账并留零和证据。"""

    def mutate(document: dict) -> None:
        document["payrolls"] = [{"payroll_id": "salary-jan", "employer_account_id": "employer:zhangjiang",
                                 "agent_id": 2, "amount_fen": 100_000, "due_at": _DUE_TEXT}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    _fire(engine, "payroll-salary-jan")
    assert engine.state.accounts["employer:zhangjiang"] == 4_000_000 - 100_000
    assert engine.state.accounts["agent:2"] == 800_000 + 100_000
    events = _environment_events(engine, "payroll")
    assert len(events) == 1
    assert events[0].data["payroll_paid"] is True
    assert events[0].data["resource_changes"] == {
        "account:employer:zhangjiang": -100_000, "account:agent:2": 100_000}


def test_payroll_insufficient_employer_refuses_without_crash(tmp_path: Path) -> None:
    """雇主余额不足时拒付不崩，人物账户不动。"""

    def mutate(document: dict) -> None:
        document["payrolls"] = [{"payroll_id": "salary-huge", "employer_account_id": "employer:zhangjiang",
                                 "agent_id": 1, "amount_fen": 5_000_000, "due_at": _DUE_TEXT}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    _fire(engine, "payroll-salary-huge")
    assert engine.state.accounts["employer:zhangjiang"] == 4_000_000
    assert engine.state.accounts["agent:1"] == 800_000
    events = _environment_events(engine, "payroll")
    assert events[0].data["payroll_paid"] is False
    assert events[0].data["resource_changes"] == {}


# ---------------------------------------------------------------------------
# PRICE_CHANGE 与 PRODUCT_RELEASE
# ---------------------------------------------------------------------------


def _supply_for(product_id: str):
    """从正式地图取得具体集市供给，商品对象只提供规格。"""
    semantics, _ = load_runtime_scenario(_PROJECT_ROOT / "scenarios/multi-person.json")
    return next(item for item in semantics.scenario.supplies
                if item.product_id == product_id and item.location_id == "market")


def _scheduled_source() -> dict:
    """定时价格与库存拥有独立场景假设来源。"""
    return _supply_for("food-6923644266066").source.model_copy(update={
        "url": "scenario://scheduled-tests", "applicability": "Explicit scheduled test change"}).model_dump(mode="json")


def _supply_request(engine, supply) -> PurchaseOrderRequest:
    """购买引用供给身份及当前世界报价。"""
    return PurchaseOrderRequest(items=({"supply_id": supply.supply_id, "quantity": 1, "recipient_id": "agent:1",
                                        "price_version": engine.state.supply_quotes[supply.supply_id].price_version},),
                                fulfillment="delivery", destination_id="home")


def test_price_change_updates_persisted_quote_and_purchase_uses_new_price(tmp_path: Path) -> None:
    """调价只替换世界供给报价；此后购买按新价结算，旧报价被拒。"""
    supply = _supply_for("food-6923644266066")

    def mutate(document: dict) -> None:
        document["price_changes"] = [{"change_id": "grocery-promo", "supply_id": supply.supply_id,
                                      "price_version": "promo-v1", "amount_fen": 700,
                                      "due_at": _DUE_TEXT, "source": _scheduled_source()}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    old_product = engine.rules.products[supply.product_id]
    old_quote = engine.state.supply_quotes[supply.supply_id].model_copy(deep=True)
    _fire(engine, "price-grocery-promo")
    updated = engine.state.supply_quotes[supply.supply_id]
    assert engine.rules.products[supply.product_id] is old_product
    assert updated.price_fen == 700 and updated.price_version == "promo-v1"
    assert updated.source == _scheduled_source()
    events = _environment_events(engine, "price_change")
    assert events[0].data["amount_fen"] == 700
    assert events[0].data["quote_before"] == old_quote.model_dump(mode="json")
    request = _supply_request(engine, supply)
    outcome = purchase_order(engine.state, engine.semantics, 1, request)
    assert engine.state.accounts["agent:1"] - outcome.state.accounts["agent:1"] == 700
    stale = request.model_copy(update={"items": (request.items[0].model_copy(
        update={"price_version": old_quote.price_version}),)})
    with pytest.raises(BusinessRuleError, match="PRICE_VERSION_MISMATCH"):
        purchase_order(engine.state, engine.semantics, 1, stale)


def test_product_release_restocks_and_enables_purchase(tmp_path: Path) -> None:
    """补货设置目标供给库存；零库存供给到期后可购买。"""
    supply = _supply_for("item-umbrella")

    def mutate(document: dict) -> None:
        for item in document["supplies"]:
            if item["supply_id"] == supply.supply_id:
                item["initial_stock"] = 0
        document["product_releases"] = [{"release_id": "umbrella-launch", "supply_id": supply.supply_id,
                                         "stock": 3, "due_at": _DUE_TEXT, "source": _scheduled_source()}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    assert engine.state.supply_stock[supply.supply_id] == 0
    request = _supply_request(engine, supply)
    with pytest.raises(BusinessRuleError, match="OUT_OF_STOCK"):
        purchase_order(engine.state, engine.semantics, 1, request)
    _fire(engine, "release-umbrella-launch")
    assert engine.state.supply_stock[supply.supply_id] == 3
    events = _environment_events(engine, "product_release")
    assert events[0].data["stock_before"] == 0 and events[0].data["stock_after"] == 3
    outcome = purchase_order(engine.state, engine.semantics, 1, request)
    assert outcome.state.supply_stock[supply.supply_id] == 2


def test_document_rejects_price_change_for_undeclared_supply() -> None:
    """调价只能引用场景实际选入的门店供给。"""
    document = _document("multi-person.json")
    document["price_changes"] = [{"change_id": "bad-pin", "supply_id": "missing-supply",
                                  "price_version": "v2", "amount_fen": 1_000,
                                  "due_at": _DUE_TEXT, "source": _scheduled_source()}]
    with pytest.raises(ValueError, match="unknown supply"):
        LifeScenario.model_validate(document)


def test_document_rejects_bill_with_undeclared_account() -> None:
    """文档校验拒绝引用未声明账户的账单。"""
    document = _document("multi-person.json")
    document["corpus_root"] = str(_PROJECT_ROOT / "corpora")
    document["bills"] = [{"bill_id": "ghost", "account_id": "agent:9",
                          "payee_account_id": "merchant:grocery", "amount_fen": 1,
                          "due_at": _DUE_TEXT}]
    with pytest.raises(ValueError, match="undeclared account"):
        LifeScenario.model_validate(document)


def test_price_change_requires_positive_amount_and_explicit_assumption_source() -> None:
    """调价金额必须为正且不能把新假定价格标成原门店事实。"""
    payload = {"change_id": "invalid", "supply_id": _supply_for("item-umbrella").supply_id,
               "price_version": "v2", "amount_fen": 0, "due_at": _DUE_TEXT, "source": _scheduled_source()}
    with pytest.raises(ValueError, match="greater than 0"):
        PriceChangeConfig.model_validate(payload)
    payload["amount_fen"] = 100
    payload["source"]["kind"] = "fact"
    with pytest.raises(ValueError, match="explicit scenario source"):
        PriceChangeConfig.model_validate(payload)


def test_schedule_refs_cover_new_kinds_in_order() -> None:
    """目录准入引用只保留资金账户，供给调价独立接受地图关系准入。"""
    document = _document("multi-person.json")
    document["corpus_root"] = str(_PROJECT_ROOT / "corpora")
    document["bills"] = [{"bill_id": "b1", "account_id": "agent:1",
                          "payee_account_id": "merchant:grocery", "amount_fen": 5,
                          "due_at": "2026-01-05T09:30:00+08:00"}]
    document["payrolls"] = [{"payroll_id": "p1", "employer_account_id": "employer:zhangjiang",
                             "agent_id": 1, "amount_fen": 7, "due_at": "2026-01-05T09:40:00+08:00"}]
    supply = _supply_for("item-umbrella")
    document["price_changes"] = [{"change_id": "c1", "supply_id": supply.supply_id,
                                  "price_version": "v2", "amount_fen": 4_000, "source": _scheduled_source(),
                                  "due_at": "2026-01-05T09:50:00+08:00"}]
    parsed = LifeScenario.model_validate(document)
    baseline, _ = load_runtime_scenario(_PROJECT_ROOT / "scenarios/multi-person.json")
    semantics = assemble_semantics(parsed, load_catalog(_PROJECT_ROOT / "catalog"), baseline.scenario.map_dataset)
    refs = semantics.refs.schedule
    assert [(item.kind.value, item.account_id, item.object_id) for item in refs] == [
        ("bill_due", "agent:1", None),
        ("payroll", "employer:zhangjiang", None),
    ]
    event = next(item for item in compile_schedule(semantics.scenario,
        int(semantics.scenario.start_at.timestamp()), int(semantics.scenario.end_at.timestamp()))
        if item.kind == ScheduledKind.PRICE_CHANGE)
    assert event.data["supply_id"] == supply.supply_id
    assert event.data["price_version"] == "v2"
    ordered = [(item.due_at, item.sequence) for item in refs]
    assert ordered == sorted(ordered) and len(set(ordered)) == len(ordered)
