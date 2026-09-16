"""预排事件子系统：提取等价、五类新事件效果与缺省场景零变化。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from worldmock.catalog import load_catalog
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.commerce import purchase
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    ModelIdentity,
    PurchaseRequest,
    RunIdentity,
    ScheduledEvent,
    ScheduledKind,
)
from worldmock.life.scenario import LifeScenario, RuntimeScenario
from worldmock.life.semantics import assemble_semantics, load_runtime_scenario
from worldmock.scheduling.compile import compile_schedule
from tests.life.conftest import advance_next

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DUE_TEXT = "2026-01-05T10:00:00+08:00"


def _legacy_initial_events(scenario: RuntimeScenario, start: int, end: int) -> list[ScheduledEvent]:
    """提取前的 engine._initial_events 原文（HEAD b5e2d4b），作为等价对照基准。"""
    rows = []
    for message in scenario.messages:
        due = int(message.sent_at.timestamp())
        if start < due < end:
            rows.append((due, message.recipient_agent_id, ScheduledKind.MAIL_ARRIVAL,
                         {"message_id": message.message_id}, f"mail-{message.message_id}"))
    for task in scenario.tasks:
        due = int(task.available_at.timestamp())
        if start < due < end:
            for agent in scenario.agents:
                rows.append((due, agent.agent_id, ScheduledKind.WORK_AVAILABLE,
                             {"task_id": task.task_id}, f"work-{task.task_id}-{agent.agent_id}"))
        deadline = int(task.due_at.timestamp())
        if start < deadline <= end:
            rows.append((deadline, None, ScheduledKind.TASK_EXPIRED,
                         {"task_id": task.task_id}, f"expired-{task.task_id}"))
    rows.sort(key=lambda row: (row[0], row[4]))
    return [ScheduledEvent(scheduled_id=row[4], kind=row[2], due_at=row[0], sequence=index,
                           agent_id=row[1], data=row[3]) for index, row in enumerate(rows, start=1)]


def _document(base_name: str) -> dict:
    """读入正式场景文档的可变深拷贝。"""
    return json.loads((_PROJECT_ROOT / "scenarios" / base_name).read_text(encoding="utf-8"))


def _write_scenario(document: dict, tmp_path: Path) -> Path:
    """把改写文档落盘到带目录符号链接的隔离结构中并返回路径。"""
    document["corpus_root"] = str(_PROJECT_ROOT / "corpora")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir(parents=True)
    (tmp_path / "catalog").symlink_to(_PROJECT_ROOT / "catalog")
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
            provider="deepseek",
            base_url="https://api.deepseek.com/beta",
            requested_model="deepseek-flash",
            thinking="disabled",
            tool_choice="required",
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
            update={"next_decision_at": event.due_at + 3600}, deep=True)
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
    """剔除时钟与版本后的可比世界快照。"""
    return engine.state.model_dump(mode="json", exclude={"version", "current_time",
                                                         "pending_events"})


def _midnight_epoch(date_text: str, timezone: str) -> int:
    """按场景时区计算自然日 00:00 的纪元秒。"""
    return int(datetime.fromisoformat(f"{date_text}T00:00:00")
               .replace(tzinfo=ZoneInfo(timezone)).timestamp())


# ---------------------------------------------------------------------------
# 提取等价与缺省零变化
# ---------------------------------------------------------------------------


def test_default_scenario_compilation_matches_legacy_algorithm() -> None:
    """缺省场景（无可选段）编译结果与提取前算法逐条一致。"""
    for name, calendar_flags in (("multi-person.json", 0), ("white-collar-12-weeks.json", 16)):
        semantics, _ = load_runtime_scenario(_PROJECT_ROOT / "scenarios" / name)
        scenario = semantics.scenario
        start = int(scenario.start_at.timestamp())
        end = int(scenario.end_at.timestamp())
        legacy = _legacy_initial_events(scenario, start, end)
        compiled = compile_schedule(scenario, start, end)
        non_calendar = [item for item in compiled
                        if item.kind != ScheduledKind.CALENDAR_FLAG]
        assert len(compiled) - len(non_calendar) == calendar_flags
        assert [item.model_dump(exclude={"sequence"}) for item in non_calendar] == \
            [item.model_dump(exclude={"sequence"}) for item in legacy]
        if calendar_flags == 0:
            assert [item.model_dump() for item in compiled] == \
                [item.model_dump() for item in legacy]


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
    """日历旗标到点零状态变更，只追加审计环境事件；观察侧注入路径保持。"""
    engine = _custom_engine("white-collar-12-weeks.json", lambda doc: None, tmp_path)
    assert _environment_events(engine, "calendar_flag") == []
    due = _stage(engine, "calendar-2026-01-24")
    before = _bare_world(engine)
    advance_next(engine)
    assert engine.state.current_time == due
    events = _environment_events(engine, "calendar_flag")
    assert len(events) == 1
    assert events[0].data["flags"] == ["overtime_opportunity"]
    assert events[0].data["scheduled_id"] == "calendar-2026-01-24"
    assert _bare_world(engine) == before
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
    """设计 §7.2 的房租/水电形态：付款入外部锚点，锚点侧吸收不入运行账。"""

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
    advance_next(engine)
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


def test_price_change_updates_semantics_and_purchase_uses_new_price(tmp_path: Path) -> None:
    """调价到点换持语义；此后购买按新价结算，旧报价版本被拒。"""

    def mutate(document: dict) -> None:
        document["price_changes"] = [{"change_id": "grocery-promo", "object_id": "food-6923644266066",
                                      "object_version": 1, "amount_fen": 1_990, "due_at": _DUE_TEXT}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    old = engine.rules.products["food-6923644266066"]
    old_version = old.price_version
    _fire(engine, "price-grocery-promo")
    updated = engine.rules.products["food-6923644266066"]
    assert updated is not old
    assert updated.price_fen == 1_990
    assert updated.price_version == "catalog:food-6923644266066:v1@price-grocery-promo"
    assert engine.scenario is engine.semantics.scenario
    assert engine.semantics.scenario.products[0].price_fen == 1_990
    events = _environment_events(engine, "price_change")
    assert events[0].data["amount_fen"] == 1_990
    request = {"product_id": "food-6923644266066", "quantity": 1,
               "fulfillment": "delivery", "destination_id": "home",
               "price_version": updated.price_version}
    outcome = purchase(engine.state, engine.semantics.scenario, 1, PurchaseRequest(**request))
    assert engine.state.accounts["agent:1"] - outcome.state.accounts["agent:1"] == 1_990
    stale = {**request, "price_version": old_version}
    with pytest.raises(BusinessRuleError, match="PRICE_VERSION_MISMATCH"):
        purchase(engine.state, engine.semantics.scenario, 1, PurchaseRequest(**stale))


def test_product_release_restocks_and_enables_purchase(tmp_path: Path) -> None:
    """上架把商家该对象库存置为给定值；零库存商品上架后可购买。"""

    def mutate(document: dict) -> None:
        for item in document["products"]:
            if item["product_id"] == "item-umbrella":
                item["stock"] = 0
        document["product_releases"] = [{"release_id": "umbrella-launch", "object_id": "item-umbrella",
                                         "merchant": "merchant:general", "stock": 3,
                                         "due_at": _DUE_TEXT}]

    engine = _custom_engine("multi-person.json", mutate, tmp_path)
    assert engine.state.merchant_stock["item-umbrella"] == 0
    request = PurchaseRequest(product_id="item-umbrella", quantity=1, fulfillment="delivery",
                              destination_id="home",
                              price_version=engine.rules.products["item-umbrella"].price_version)
    with pytest.raises(BusinessRuleError, match="OUT_OF_STOCK"):
        purchase(engine.state, engine.semantics.scenario, 1, request)
    _fire(engine, "release-umbrella-launch")
    assert engine.state.merchant_stock["item-umbrella"] == 3
    events = _environment_events(engine, "product_release")
    assert events[0].data["stock_before"] == 0
    assert events[0].data["stock"] == 3
    outcome = purchase(engine.state, engine.semantics.scenario, 1, request)
    assert outcome.state.merchant_stock["item-umbrella"] == 2


# ---------------------------------------------------------------------------
# 准入联动与语义方法
# ---------------------------------------------------------------------------


def test_admission_rejects_wrong_object_version_pin() -> None:
    """PRICE_CHANGE 版本钉扎与目录不符时准入以 SCHEDULE_INVALID 拒绝。"""
    document = _document("multi-person.json")
    document["corpus_root"] = str(_PROJECT_ROOT / "corpora")
    document["price_changes"] = [{"change_id": "bad-pin", "object_id": "food-6923644266066",
                                  "object_version": 2, "amount_fen": 1_000, "due_at": _DUE_TEXT}]
    parsed = LifeScenario.model_validate(document)
    with pytest.raises(IntegrityError, match="SCHEDULE_INVALID"):
        assemble_semantics(parsed, load_catalog(_PROJECT_ROOT / "catalog"))


def test_document_rejects_bill_with_undeclared_account() -> None:
    """文档校验拒绝引用未声明账户的账单。"""
    document = _document("multi-person.json")
    document["corpus_root"] = str(_PROJECT_ROOT / "corpora")
    document["bills"] = [{"bill_id": "ghost", "account_id": "agent:9",
                          "payee_account_id": "merchant:grocery", "amount_fen": 1,
                          "due_at": _DUE_TEXT}]
    with pytest.raises(ValueError, match="undeclared account"):
        LifeScenario.model_validate(document)


def test_with_price_rejects_unknown_product_and_non_positive_amount() -> None:
    """语义受控价格替换对未知对象与非正金额 fail-closed。"""
    semantics, _ = load_runtime_scenario(_PROJECT_ROOT / "scenarios/multi-person.json")
    with pytest.raises(IntegrityError, match="unknown product"):
        semantics.with_price("item-unknown", 100, "v")
    product = semantics.scenario.products[0]
    with pytest.raises(IntegrityError, match="positive"):
        semantics.with_price(product.product_id, 0, "v")


def test_schedule_refs_cover_new_kinds_in_order() -> None:
    """refs.schedule 与编译表同序，携带账户与对象版本钉扎。"""
    document = _document("multi-person.json")
    document["corpus_root"] = str(_PROJECT_ROOT / "corpora")
    document["bills"] = [{"bill_id": "b1", "account_id": "agent:1",
                          "payee_account_id": "merchant:grocery", "amount_fen": 5,
                          "due_at": "2026-01-05T09:30:00+08:00"}]
    document["payrolls"] = [{"payroll_id": "p1", "employer_account_id": "employer:zhangjiang",
                             "agent_id": 1, "amount_fen": 7, "due_at": "2026-01-05T09:40:00+08:00"}]
    document["price_changes"] = [{"change_id": "c1", "object_id": "item-umbrella",
                                  "object_version": 1, "amount_fen": 9,
                                  "due_at": "2026-01-05T09:50:00+08:00"}]
    parsed = LifeScenario.model_validate(document)
    semantics = assemble_semantics(parsed, load_catalog(_PROJECT_ROOT / "catalog"))
    refs = semantics.refs.schedule
    assert [(item.kind.value, item.account_id, item.object_id) for item in refs] == [
        ("bill_due", "agent:1", None),
        ("payroll", "employer:zhangjiang", None),
        ("price_change", None, "item-umbrella"),
    ]
    assert refs[2].object_version == 1
    ordered = [(item.due_at, item.sequence) for item in refs]
    assert ordered == sorted(ordered) and len(set(ordered)) == len(ordered)
