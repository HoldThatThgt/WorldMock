"""@brief 场景目录边界的身份、商家与有限工作内容纯校验。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from worldmock.catalog.admission import validate_scenario_against_catalog
from worldmock.catalog.errors import RegistrationError


def _document(catalog):
    """@brief 构造只包含校验器所读事实的场景，不导入运行引擎。"""
    shift = SimpleNamespace(starts=540, ends=1080, breaks=((720, 780),))
    schedule = SimpleNamespace(weekly={str(day): shift if day < 5 else None for day in range(7)},
                               exceptions={}, workplace_location_id="office")
    start = datetime(2026, 1, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    return SimpleNamespace(
        environment=SimpleNamespace(environment_id=catalog.manifest.environment_id,
                                    environment_version=catalog.manifest.environment_version),
        locations=tuple(SimpleNamespace(location_id=identity) for identity in catalog.locations),
        accounts=tuple(SimpleNamespace(role=f"merchant:{identity}")
                       for identity in ("cafe", "noodle-1", "noodle-2", "store")),
        agents=[SimpleNamespace(home_location_id="home", work_content_ids=("topic-sales",), work_schedule=schedule)],
        timezone="Asia/Shanghai", start_at=start, end_at=start + timedelta(days=1),
    )


def test_scenario_accepts_work_content_covering_whole_shift(loaded_catalog):
    """@brief 内容三万二千四百秒覆盖九小时班次，声明休息也不扣除。"""
    validate_scenario_against_catalog(_document(loaded_catalog), loaded_catalog)


@pytest.mark.parametrize("case,code", [
    ("pin", "ENVIRONMENT_PIN_MISMATCH"), ("place", "ENVIRONMENT_REF_UNKNOWN"),
    ("home", "ENVIRONMENT_REF_UNKNOWN"), ("merchant", "MERCHANT_ACCOUNT_MISSING"),
    ("unknown", "WORK_CONTENT_UNKNOWN"), ("entertainment", "WORK_CONTENT_UNKNOWN"),
    ("no_schedule", "WORK_SCHEDULE_REQUIRED"), ("no_content", "WORK_CONTENT_REQUIRED"),
    ("two_days", "WORK_CONTENT_INSUFFICIENT"),
])
def test_scenario_rejects_invalid_catalog_references(loaded_catalog, case, code):
    """@brief 场景业务错误通过唯一校验器返回稳定目录码。"""
    document = _document(loaded_catalog)
    agent = document.agents[0]
    if case == "pin":
        document.environment.environment_version = "other"
    elif case == "place":
        document.locations += (SimpleNamespace(location_id="unknown"),)
    elif case == "home":
        agent.home_location_id = "unknown"
    elif case == "merchant":
        document.accounts = ()
    elif case == "unknown":
        agent.work_content_ids = ("unknown",)
    elif case == "entertainment":
        agent.work_content_ids = ("c-short",)
    elif case == "no_schedule":
        agent.work_schedule = None
    elif case == "no_content":
        agent.work_content_ids = ()
    else:
        document.end_at += timedelta(days=1)
    with pytest.raises(RegistrationError, match=code):
        validate_scenario_against_catalog(document, loaded_catalog)


def test_scenario_uses_date_exceptions_for_work_coverage(loaded_catalog):
    """@brief 每周班次的日期覆盖可显式取消或新增第二天的班次。"""
    document = _document(loaded_catalog)
    document.end_at += timedelta(days=1)
    document.agents[0].work_schedule.exceptions["2026-01-06"] = None
    validate_scenario_against_catalog(document, loaded_catalog)
    document.agents[0].work_schedule.exceptions["2026-01-06"] = SimpleNamespace(starts=540, ends=1080, breaks=())
    with pytest.raises(RegistrationError, match="WORK_CONTENT_INSUFFICIENT"):
        validate_scenario_against_catalog(document, loaded_catalog)


def test_scenario_half_day_still_requires_full_shift_content(loaded_catalog):
    """@brief 运行窗口只覆盖部分班次时，规格仍按该班次起止全长校验。"""
    document = _document(loaded_catalog)
    document.start_at += timedelta(hours=8)
    document.end_at = document.start_at + timedelta(hours=4)
    document.agents[0].work_content_ids = ("c-course",)
    with pytest.raises(RegistrationError, match="WORK_CONTENT_INSUFFICIENT"):
        validate_scenario_against_catalog(document, loaded_catalog)


def test_scenario_run_ending_at_shift_start_does_not_require_shift_duration(loaded_catalog):
    """@brief 半开窗口恰在班次开始前结束，不计该班次。"""
    document = _document(loaded_catalog)
    document.end_at = document.start_at + timedelta(hours=9)
    document.agents[0].work_content_ids = ("c-course",)
    validate_scenario_against_catalog(document, loaded_catalog)
