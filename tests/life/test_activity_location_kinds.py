"""活动环境声明在当前地图的启动和恢复入口执行，不从模块全局语料猜测。"""

from pathlib import Path

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.activities import ActivityOrigin, require_location_kinds, start_activity
from worldmock.life.models import ActivityKind, ActivityStartParams, AgentState, BodyState
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.world_setup import load_scenario

SCENARIO = Path('scenarios/white-collar-12-weeks.json')
HOME_ACTIVITY = 'wellness-morning-wash'


def _agent(location_id):
    return AgentState(agent_id=1, name='worker', location_id=location_id,
                      body_settled_at=0,
                      balance_account_id='agent:1', next_decision_at=0,
                      body=BodyState(energy_kcal=1600, sleep_pressure=0, sleep_deficit_hours=0,
                                     entertainment_need=0))


def _request(activity_id=HOME_ACTIVITY):
    return ActivityStartParams(activity_type='lifestyle', planned_duration_seconds=900,
                                target_id=activity_id)


def _rules():
    scenario, _ = load_scenario(SCENARIO)
    return LifeRuleEngine(scenario, Path('corpora'))


def test_lifestyle_requirements_come_from_the_selected_corpus():
    rules = _rules()
    assert rules.activity_location_kinds[HOME_ACTIVITY] == frozenset({'home'})
    assert rules.activity_location_kinds['3d-printing'] == frozenset({'home', 'venue'})


@pytest.mark.parametrize('place, needed', [({'home'}, {'home'}), ({'outdoor', 'fitness'}, {'fitness'}),
                                          ({'outdoor'}, {'anywhere'})])
def test_explicit_matching_activity_environment_is_accepted(place, needed):
    require_location_kinds(frozenset(place), frozenset(needed))


@pytest.mark.parametrize('place, needed', [({'outdoor'}, {'home'}), (set(), {'home'}),
                                          ({'home'}, set())])
def test_missing_or_mismatched_environment_is_not_an_anywhere_default(place, needed):
    with pytest.raises(BusinessRuleError, match='ELIGIBILITY_LOCATION_KIND_MISMATCH'):
        require_location_kinds(frozenset(place), frozenset(needed))


def test_authoritative_start_gate_uses_current_map_location():
    rules = _rules()
    with pytest.raises(BusinessRuleError, match='ELIGIBILITY_LOCATION_KIND_MISMATCH'):
        rules.validate_start(_agent('poi-1868478037'), _request(), 100, 'poi-1868478037')
    rules.validate_start(_agent('home'), _request(), 100, 'home')


def test_en_route_is_no_place_for_a_lifestyle_activity():
    """途中（present 为 None）不在任何地点：location_id 仍是出发地的家，也不能开始或恢复要在家做的生活活动
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」的实际地点）。"""
    rules = _rules()
    with pytest.raises(BusinessRuleError, match='ELIGIBILITY_LOCATION_KIND_MISMATCH'):
        rules.validate_start(_agent('home'), _request(), 100, None)
    with pytest.raises(BusinessRuleError, match='ELIGIBILITY_LOCATION_KIND_MISMATCH'):
        rules.validate_activity_location(_agent('home'), ActivityKind.LIFESTYLE, HOME_ACTIVITY, None)


def test_unknown_lifestyle_is_rejected_instead_of_losing_its_location_requirement():
    with pytest.raises(BusinessRuleError, match='LIFESTYLE_ACTIVITY_NOT_FOUND'):
        _rules().validate_start(_agent('home'), _request('no-such-activity'), 100, 'home')


def test_non_lifestyle_activity_has_no_lifestyle_place_requirement():
    _rules().validate_activity_location(_agent('home'), ActivityKind.WAIT, None, 'home')


def test_resume_location_gate_rechecks_the_current_location():
    rules = _rules()
    rules.validate_activity_location(_agent('home'), ActivityKind.LIFESTYLE, HOME_ACTIVITY, 'home')
    with pytest.raises(BusinessRuleError, match='ELIGIBILITY_LOCATION_KIND_MISMATCH'):
        rules.validate_activity_location(_agent('office'), ActivityKind.LIFESTYLE, HOME_ACTIVITY, 'office')


def test_location_admission_returns_activity_resource_claims():
    """生活活动的占用是 hands（第五章「并行准入」占用表）。"""
    rules = _rules()
    agent = _agent('home')
    rules.validate_start(agent, _request(), 100, 'home')
    person, activity = start_activity(agent, _request(), 100, ActivityOrigin('a', 'd', 'e'), rules.scenario.body)
    assert person == agent
    assert activity.resource_claims == {'hands'}


def test_accepted_location_starts_real_activity():
    rules = _rules()
    agent = _agent('home')
    rules.validate_start(agent, _request(), 100, 'home')
    person, activity = start_activity(agent, _request(), 100, ActivityOrigin('a', 'd', 'e'), rules.scenario.body)
    assert person == agent
    assert activity.target_id == HOME_ACTIVITY
