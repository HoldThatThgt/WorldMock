"""@brief 营业时间与报价声明的边界行为验证。"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from worldmock.life.map_models import LocationConfig, OpeningCalendar, SupplyConfig
from worldmock.life.opening_hours import next_opening_change, opening_status, quote_at


def source():
    return {"kind": "scenario", "url": "scenario://test", "retrieved_at": "2026-09-20T00:00:00+08:00",
            "license": "test", "notice": "test assumption", "fact_cutoff": "2026-09-20",
            "applicability": "controlled simulation fixture"}


def calendar(**changes):
    value = {"effective_from": "2026-01-01T00:00:00+08:00", "effective_to": None,
             "reviewed_on": "2026-09-20", "still_valid_on": "2027-12-31", "source": source(),
             "weekly": {str(day): [{"opens": 600, "closes": 720}, {"opens": 780, "closes": 1260}]
                        for day in range(7)}, "exceptions": {}}
    value.update(changes)
    return value


def location(calendars=None):
    return LocationConfig.model_validate({
        "location_id": "store", "name": "Store", "kind": "retail", "business_kind": "supermarket",
        "longitude": 121.6, "latitude": 31.2, "location_kinds": ["venue"],
        "opening_calendars": [calendar()] if calendars is None else calendars,
        "required_qualifications": [], "brand": None, "source": source(), "affordances": {},
    })


def epoch(value):
    return int(datetime.fromisoformat(value).timestamp())


@pytest.mark.parametrize(("time", "status"), [
    ("2026-01-05T09:59:59+08:00", "closed"), ("2026-01-05T10:00:00+08:00", "open"),
    ("2026-01-05T12:00:00+08:00", "closed"), ("2026-01-05T13:00:00+08:00", "open"),
    ("2026-01-05T21:00:00+08:00", "closed"), ("2025-12-31T23:59:59+08:00", "unknown"),
])
def test_opening_status_uses_half_open_minute_intervals(time, status):
    assert opening_status(location(), epoch(time), "Asia/Shanghai") == status


def test_cross_midnight_and_explicit_closed_exception():
    weekly = {str(day): [{"opens": 1320, "closes": 1560}] for day in range(7)}
    place = location([calendar(weekly=weekly)])
    now = epoch("2026-01-06T01:30:00+08:00")
    assert opening_status(place, now, "Asia/Shanghai") == "open"
    assert opening_status(place, now, "Asia/Shanghai", {"2026-01-06": []}) == "closed"
    assert opening_status(place, epoch("2026-01-06T02:00:00+08:00"), "Asia/Shanghai") == "closed"
    dated = location([calendar(weekly=weekly, exceptions={"2026-01-06": []})])
    assert opening_status(dated, now, "Asia/Shanghai") == "closed"


def test_world_exception_overrides_frozen_calendar_and_produces_next_event():
    place = location([calendar(exceptions={"2026-01-05": []})])
    now = epoch("2026-01-05T10:30:00+08:00")
    override = {"2026-01-05": [(660, 675)]}
    assert opening_status(place, now, "Asia/Shanghai", override) == "closed"
    assert next_opening_change(place, now, "Asia/Shanghai", override) == epoch("2026-01-05T11:00:00+08:00")
    assert opening_status(place, epoch("2026-01-05T11:10:00+08:00"), "Asia/Shanghai", override) == "open"


def test_temporal_gaps_and_future_declaration_have_explicit_unknown_state():
    first = calendar(effective_to="2026-01-05T11:00:00+08:00")
    second = calendar(effective_from="2026-01-07T00:00:00+08:00")
    place = location([first, second])
    now = epoch("2026-01-05T11:00:00+08:00")
    assert opening_status(place, now, "Asia/Shanghai") == "unknown"
    assert next_opening_change(place, now, "Asia/Shanghai") == epoch("2026-01-07T00:00:00+08:00")
    assert opening_status(location([]), now, "Asia/Shanghai") == "unknown"
    assert next_opening_change(location([]), now, "Asia/Shanghai") is None


def test_all_day_open_has_no_fake_midnight_change():
    weekly = {str(day): [{"opens": 0, "closes": 1440}] for day in range(7)}
    place = location([calendar(weekly=weekly)])
    now = epoch("2026-01-05T23:59:59+08:00")
    assert opening_status(place, now, "Asia/Shanghai") == "open"
    assert opening_status(place, now + 1, "Asia/Shanghai") == "open"
    assert next_opening_change(place, now, "Asia/Shanghai") is None


def test_timezone_controls_opening_independently_of_host_clock():
    place = location()
    now = epoch("2026-01-05T02:30:00+00:00")
    assert opening_status(place, now, "Asia/Shanghai") == "open"
    assert opening_status(place, now, "UTC") == "closed"


@pytest.mark.parametrize("changes", [
    {"effective_to": "2025-12-31T00:00:00+08:00"}, {"effective_from": "2026-01-01T00:00:00"},
    {"reviewed_on": "2028-01-01"}, {"weekly": {"0": []}},
    {"weekly": {str(day): [{"opens": 600, "closes": 800}, {"opens": 750, "closes": 900}]
                for day in range(7)}},
    {"weekly": {str(day): [{"opens": 0, "closes": 120}, {"opens": 1380, "closes": 1500}]
                for day in range(7)}},
    {"exceptions": {"2026-1-2": []}},
])
def test_invalid_calendar_is_rejected(changes):
    with pytest.raises(ValidationError):
        OpeningCalendar.model_validate(calendar(**changes))


def test_calendar_exception_keys_require_canonical_iso_dates():
    """@brief 可解析的紧凑日期仍须拒绝，避免同一天以不同文本身份重复声明。"""
    with pytest.raises(ValidationError, match="Opening exception date must use ISO format"):
        OpeningCalendar.model_validate(calendar(exceptions={"20260105": []}))


def test_overlapping_effective_calendars_are_rejected():
    with pytest.raises(ValidationError, match="overlap"):
        location([calendar(), calendar(effective_from="2026-02-01T00:00:00+08:00")])


def test_quote_identity_changes_only_at_effective_boundary():
    declaration = {key: value for key, value in calendar().items() if key not in {"weekly", "exceptions"}}
    first = {**declaration, "price_version": "a", "price_fen": 700,
             "effective_to": "2026-01-05T12:00:00+08:00"}
    second = {**declaration, "price_version": "b", "price_fen": 900,
              "effective_from": "2026-01-05T12:00:00+08:00"}
    supply = SupplyConfig.model_validate({"supply_id": "s", "location_id": "store", "product_id": "p",
        "seller_account_id": "seller", "quotes": [first, second], "initial_stock": 4,
        "fulfillment_modes": ["pickup"], "delivery_destination_ids": [], "delivery_delay_seconds": 0,
        "required_qualifications": [], "source": source()})
    boundary = epoch("2026-01-05T12:00:00+08:00")
    assert quote_at(supply, boundary - 1).price_version == "a"
    assert quote_at(supply, boundary).price_version == "b"
    assert quote_at(supply, epoch("2025-12-31T23:59:59+08:00")) is None
