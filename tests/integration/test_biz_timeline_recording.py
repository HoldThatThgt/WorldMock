"""@brief 商务时间线夹具由当前人工环境的真实通话与工作事件重新录制。"""

import json
import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.life.conftest import (activity_name, advance_until_ready, conclude_request, context,
                                 report_request, stage_day_plan, start_activity, submit_round, wake_by_mail)
from worldmock.footprints.timeline_labels import _load_render_day


def _business_scenario(document):
    """@brief 人工联系人和办公室是显式场景事实，不读取历史商务运行。"""
    document['start_at'] = '2026-01-05T09:15:00+08:00'
    document['agents'][0]['home_location_id'] = 'office'
    document['agents'][0]['contacts'] = [
        {'contact_id': 'colleague', 'salutation': '刘洋', 'relation': '同事', 'closeness': 16,
         'channels': ['phone'], 'reply_policy': 'none'},
        {'contact_id': 'client', 'salutation': '张总', 'relation': '客户', 'closeness': 16,
         'channels': ['phone'], 'reply_policy': 'none'}]


def _business_activity(engine, target, way, label):
    """@brief 开始真实活动并确认受理，不复制或改造旧活动快照。"""
    _, results = submit_round(engine, {1: ('start_activity', start_activity(way, target))}, label)
    assert results[0].error_code is None
    return results[0].activity_id


def _finish_call(engine, key):
    """@brief 真实推进完成通话，并在结束当秒收尾。"""
    name = activity_name(engine, key)
    for _ in range(16):
        assert advance_until_ready(engine)
        if engine.context.state.activities[key].status == 'completed':
            submit_round(engine, {1: ('conclude_activity', conclude_request(name))}, f'end-{key}')
            return
        submit_round(engine, {1: ('report_status', report_request(name))}, f'keep-{engine.context.state.version}')
    raise AssertionError('Business fixture call did not complete')


def recorded_business_timeline(factory):
    """@brief 收集当前引擎存储中的观察、进度与实际渲染行。"""
    engine = factory(mutate=_business_scenario)
    stage_day_plan(engine)
    _finish_call(engine, _business_activity(engine, '联系人：刘洋', '通话', 'call-colleague'))
    _finish_call(engine, _business_activity(engine, '联系人：张总', '通话', 'call-client'))
    topic = engine.context.scenario.work_topics[0]
    view = engine.observe(context(engine, 'work-topic-preview'))
    target = next(row['name'] for row in view['tool_layer']['targets'] if row['kind'] == 'task')
    key = _business_activity(engine, target, '工作', 'work-topic')
    name = activity_name(engine, key)
    wake_by_mail(engine, offset=2100)
    start = engine.context.state.current_time
    while engine.context.state.current_time < start + 2100:
        assert advance_until_ready(engine)
        if engine.context.state.current_time < start + 2100:
            submit_round(engine, {1: ('report_status', report_request(name))}, f'work-{engine.context.state.version}')
    submit_round(engine, {1: ('conclude_activity', conclude_request(name))}, 'end-work')
    engine.observe(context(engine, 'business-final'))
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    keep = lambda event: {key: event.model_dump(mode='json')[key]
                          for key in ('event_type', 'agent_id', 'occurred_at', 'data')}
    renderer = _load_render_day()
    day = datetime.fromtimestamp(start, ZoneInfo(engine.context.scenario.timezone)).date()
    text = renderer.render_day_markdown(engine.context.store.connection, 1, day, engine.context.scenario.timezone)
    return {'source': 'current artificial environment: genuine LifeEngine requests and committed events',
            'day': day.isoformat(), 'agent_id': 1,
            'observation': keep(next(event for event in reversed(events) if event.event_type == 'observation')),
            'progress': [keep(event) for event in events if event.event_type == 'activity_progress'],
            'exported_rows': text.splitlines()[4:]}


def test_business_fixture_is_recorded_from_current_engine(_engine_factory, monkeypatch, tmp_path):
    """@brief 真实录制逐字段相同，时间线包含两位联系人和当前工作主题。"""
    from tests.dayrun_baseline import deterministic_day_runtime
    deterministic_day_runtime(monkeypatch)
    recorded = recorded_business_timeline(_engine_factory)
    candidate = tmp_path / 'biz-record.json'
    candidate.write_text(json.dumps(recorded, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    rows = recorded['exported_rows']
    assert any('和刘洋通话' in row for row in rows)
    assert any('和张总通话' in row for row in rows)
    assert any('工作「季度销售数据整理」' in row for row in rows)
    fixture = Path(__file__).parents[1] / 'fixtures/dayrun-biz-timeline.json'
    assert json.loads(fixture.read_text(encoding='utf-8')) == recorded


def test_current_inspection_names_actual_work_and_app_progress(_engine_factory):
    """@brief 检查器直接读取真实工作与 app 进度中的人类名称。"""
    path = Path(__file__).parents[2] / 'scripts/inspect_run.py'
    spec = importlib.util.spec_from_file_location('current_inspect_run', path)
    inspector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inspector)
    business = recorded_business_timeline(_engine_factory)
    work = next(event for event in business['progress'] if event['data']['activity']['kind'] == 'work')
    assert '季度销售数据整理' in inspector._brief(work)
    assert 'topic-sales' not in inspector._brief(work)
    engine = _engine_factory()
    stage_day_plan(engine)
    view = engine.observe(context(engine, 'app-inspection-preview'))
    target = next(row['name'] for row in view['tool_layer']['targets'] if row['kind'] == 'app')
    key = _business_activity(engine, target, '打开', 'app-inspection')
    wake_by_mail(engine, offset=120)
    assert advance_until_ready(engine)
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    event = next(event for event in events if event.event_type == 'activity_progress'
                 and event.data['activity']['activity_id'] == key)
    brief = inspector._brief(event.model_dump(mode='json'))
    assert target.split('：', 1)[-1] in brief
    assert 'use_app' not in brief and 'target=' not in brief
