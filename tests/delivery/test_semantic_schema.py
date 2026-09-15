import copy
import json
import tomllib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / 'labelkit-projects/schemas/semantic-target.json'


def candidate():
    return {
        'target': {
            'claims': [{'text': 'Observed rest need.', 'kind': 'fact',
                        'evidence_event_ids': ['event-1']}],
            'intent_candidates': [{'intent': 'rest', 'basis': 'inferred'}],
            'uncertainty': '',
        },
    }


def invalid_candidates():
    cases = []
    cases.append(('claims_max', set_value(candidate(), 'claims', candidate()['target']['claims'] * 33), 'maxItems'))
    cases.append(('claim_text_max', set_value(candidate(), 'text', 'x' * 513), 'maxLength'))
    cases.append(('claim_text_min', set_value(candidate(), 'text', ''), 'minLength'))
    cases.append(('refs_max', set_value(candidate(), 'evidence_event_ids', [f'event-{i}' for i in range(65)]),
                  'maxItems'))
    cases.append(('ref_max', set_value(candidate(), 'evidence_event_ids', ['x' * 65]), 'maxLength'))
    cases.append(('ref_min', set_value(candidate(), 'evidence_event_ids', ['']), 'minLength'))
    intents = [{'intent': 'rest', 'basis': 'inferred'} for _ in range(9)]
    cases.append(('intents_max', set_value(candidate(), 'intent_candidates', intents), 'maxItems'))
    cases.append(('intents_unique', set_value(candidate(), 'intent_candidates', intents[:2]), 'uniqueItems'))
    cases.append(('uncertainty_max', set_value(candidate(), 'uncertainty', 'x' * 1025), 'maxLength'))
    extra = candidate()
    extra['input'] = {}
    cases.append(('top_level_closed', extra, 'additionalProperties'))
    facts = candidate()
    facts['target']['facts'] = {}
    cases.append(('target_closed', facts, 'additionalProperties'))
    return cases


def set_value(value, field, replacement):
    result = copy.deepcopy(value)
    if field in result['target']:
        result['target'][field] = replacement
    else:
        result['target']['claims'][0][field] = replacement
    return result


def test_target_only_schema_accepts_the_declared_boundary():
    schema = json.loads(SCHEMA_PATH.read_text())
    value = candidate()
    value['target']['claims'] *= 32
    value['target']['claims'][0]['text'] = 'x' * 512
    value['target']['claims'][0]['evidence_event_ids'] = [f'event-{i}' for i in range(64)]
    value['target']['intent_candidates'] = [
        {'intent': intent, 'basis': 'inferred'}
        for intent in ('hunger', 'rest', 'work', 'entertainment', 'travel', 'purchase', 'communication', 'unknown')
    ]
    value['target']['uncertainty'] = 'x' * 1024

    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(value)) == []


@pytest.mark.parametrize('name,value,keyword', invalid_candidates())
def test_target_only_schema_rejects_each_declared_boundary(name, value, keyword):
    del name
    errors = list(Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).iter_errors(value))
    assert errors
    assert keyword in {error.validator for error in errors}


@pytest.mark.parametrize('task', ['action', 'intent', 'dreaming'])
def test_process_projects_use_target_only_schema_without_postprocessor(task):
    project = tomllib.loads((ROOT / f'labelkit-projects/{task}.toml').read_text())

    assert 'postprocessor' not in project['annotate']
    assert project['output']['schema_path'] == 'schemas/semantic-target.json'
    assert '最多 32 条' in project['annotate']['instruction']
    assert 'input 原事件由工程完整保存' in project['annotate']['instruction']
    assert 'target 是最多 32 条的聚合摘要，不要求逐事实复述' in project['annotate']['instruction']
    assert '不删除或裁剪原始事件' not in project['annotate']['instruction']
    assert '语义候选只有 target' in project['verify']['extra_criteria']


@pytest.mark.parametrize('task', ['action', 'intent', 'dreaming'])
def test_process_prompt_requires_thematic_aggregation_before_output(task):
    project = tomllib.loads((ROOT / f'labelkit-projects/{task}.toml').read_text())
    instruction = project['annotate']['instruction']

    assert '先按活动阶段或因果主题合并证据' in instruction
    assert '一条 claim 可以引用多个事件' in instruction
    assert '禁止逐事件、逐目录项、逐终态字段或逐条未变化的起止状态复述' in instruction
    assert '输出前合并到最多 32 条 claims' in instruction


def test_default_output_limit_is_a_cap_for_bounded_semantic_summary():
    config = tomllib.loads((ROOT / 'labelkit-projects/config.toml').read_text())

    assert config['llm']['default']['max_output_tokens'] == 131072
    assert config['llm']['judge']['max_output_tokens'] == 4096
    assert not (ROOT / 'labelkit-projects/schemas/training.json').exists()
