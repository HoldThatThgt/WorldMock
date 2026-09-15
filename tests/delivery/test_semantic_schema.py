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
    instruction = project['annotate']['instruction']
    assert '事实中的每个独立断言都须由该 claim 所列来源直接支持' in instruction
    assert '不能借用未列出的 input.events 补证' in instruction
    assert '命名布尔字段的 false 只否定该字段表达的命题' in instruction
    assert 'intent=unknown、basis=uncertain 是合法组合' in instruction
    assert '没有历史进度不能由当前目录推出“从未开始”' in instruction
    assert '数值接近零本身不等同于高低或需求等级分类' in instruction
    assert '位置断言只写在 observation 事件支持的 claim 内' in instruction
    assert '不从动作结果推断动作的执行地点' in instruction
    assert '不写跨事件元断言' in instruction
    assert '不得同时在 claim 与 uncertainty 双重安置' in instruction
    criteria = project['verify']['extra_criteria']
    assert '每轮独立完整审查当前候选' in criteria
    assert '一次列出发现的全部实质问题' in criteria
    assert '主题聚合、逐条引用支持' in criteria
    assert 'fact/inference/hypothesis 分类及 intent/uncertainty 一致性' in criteria
    assert '意见对应的要求在任务指令中无明示依据' in criteria
    assert '不得作为 fail 的依据' in criteria
    assert '事实中的每个独立断言都须由该 claim 所列来源直接支持' not in criteria
    assert '身份字段是不透明标识' not in criteria


def test_all_process_roles_share_one_identical_semantic_and_pointer_contract():
    instructions = []
    for task in ('action', 'intent', 'dreaming'):
        project = tomllib.loads((ROOT / f'labelkit-projects/{task}.toml').read_text())
        common = project['annotate']['instruction'].split('仅输出 target 中的', 1)[1]
        instructions.append(common)
        assert 'input.observation_value_encoding 声明字段字典版本和解码后摘要' in common
        assert '按 RFC 6901 从当前 input 单跳取得完整字段值' in common
        assert '禁止引用字典指针、摘要或其他标识' in common
    assert instructions[0] == instructions[1] == instructions[2]


def test_all_process_roles_share_one_identical_verify_contract():
    criteria = []
    for task in ('action', 'intent', 'dreaming'):
        project = tomllib.loads((ROOT / f'labelkit-projects/{task}.toml').read_text())
        criteria.append(project['verify']['extra_criteria'])
        assert '意见对应的要求在任务指令中无明示依据' in project['verify']['extra_criteria']
        assert '该意见不得作为 fail 的依据' in project['verify']['extra_criteria']
    assert criteria[0] == criteria[1] == criteria[2]


def test_dreaming_specific_counterfactual_hypothesis_canon_is_present():
    dreaming = tomllib.loads((ROOT / 'labelkit-projects/dreaming.toml').read_text())
    instruction = dreaming['annotate']['instruction']
    assert '反事实 hypothesis 是明示合格的形态' in instruction
    assert 'evidence_event_ids 只须支持假设条件' in instruction
    assert '评审不得对假设结果本身索要事件级证据' in instruction
    assert '也不得因 hypothesis claim 未证实假设结果判失败' in instruction
    assert '不复述 hypothesis claim 已回答的具体反事实问题' in instruction
    assert 'hypothesis claim 已含的假设条件不再写入 uncertainty' in instruction
    assert '不得自行设定未出现的整数阈值' in instruction
    assert '评审也不得以近似数值判失败' in instruction
    for task in ('action', 'intent'):
        other = tomllib.loads((ROOT / f'labelkit-projects/{task}.toml').read_text())
        assert '反事实 hypothesis 是明示合格的形态' not in other['annotate']['instruction']


@pytest.mark.parametrize('task', ['action', 'intent', 'dreaming'])
def test_process_prompt_requires_thematic_aggregation_before_output(task):
    project = tomllib.loads((ROOT / f'labelkit-projects/{task}.toml').read_text())
    instruction = project['annotate']['instruction']

    assert '先按活动阶段或因果主题合并证据' in instruction
    assert '一条 claim 可以引用多个事件' in instruction
    assert '聚合用于消除同一主题的重复，不要求合并不同因果主题' in instruction
    assert '有意义的阶段变化或因果差异应保留' in instruction
    assert '按主体分组是明示合格的聚合形态' in instruction
    assert '动作结果、人物终态、可用任务、消息、出行选项、可选内容与娱乐、日历各自成一条 claim' in instruction
    assert '该形态不因涵盖条目多或信息集中而构成缺陷' in instruction
    assert '三十二条是上限而非目标数量' in instruction
    assert '必须指出共享主体、关系与尚未概括的重复内容' in instruction
    assert '不能只因条目数量或多个条目来自同一 observation 判失败' in instruction
    assert '必须指明具体哪几条 claim 共享同一主体且内容重复' in instruction
    assert '不得以“复述目录”一类笼统说法定缺陷' in instruction
    assert '摘要用共同阶段或同一活动的概括保留重复发生及有意义的差异' in instruction
    assert '不逐次建立同文 claim' in instruction
    assert '不为完整保存原始材料穷举 ID' in instruction
    assert '保留来源里的重复接触' not in instruction
    assert '禁止逐事件、逐目录项、逐终态字段或逐条未变化的起止状态复述' in instruction
    assert '输出前合并到最多 32 条 claims' in instruction


def test_default_output_limit_is_a_cap_for_bounded_semantic_summary():
    config = tomllib.loads((ROOT / 'labelkit-projects/config.toml').read_text())

    assert config['llm']['default']['max_output_tokens'] == 131072
    assert config['llm']['default']['thinking'] == 'disabled'
    assert config['llm']['judge']['max_output_tokens'] == 65536
    assert config['llm']['judge']['thinking'] == 'enabled'
    assert not (ROOT / 'labelkit-projects/schemas/training.json').exists()
