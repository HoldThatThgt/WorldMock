"""共享规则必须留在唯一归宿；结构扫描同时证明真实新增克隆会被拒绝。"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNERS = {'base_models.py': ('StrictModel',
                    'FrozenModel',
                    'CurvePoint',
                    'EfficiencyFactor',
                    'DeepFrozenModel',
                    'require_finite_number'),
 'errors.py': ('reject_value',),
 'footprints/models.py': ('canonical_json', 'sha256_json', 'is_sha256'),
 'footprints/assets.py': ('atomic_write',),
 'corpora/manifest.py': ('safe_file', 'relative_declaration'),
 'footprints/intervals.py': ('valid_interval', 'is_body_settlement', 'body_settlements'),
 'footprints/timeline_labels.py': ('window_days', 'local_day_bounds', 'split_local_days'),
 'life/models.py': ('DERIVED_DURATION_CAP_SECONDS', 'CONSUME_DURATION_CAP_SECONDS', 'validated_payload'),
 'life/durations.py': ('capped_duration_seconds', 'derived_activity_duration'),
 'life/work.py': ('work_start_refusal',),
 'life/travel.py': ('planned_travel',),
 'life/activities.py': ('entertainment_relief_rate', 'content_entertainment_relief_rate'),
 'life/candidates.py': ('CANDIDATE_ACTIVITY_KINDS', 'CANDIDATE_SOURCES'),
 'life/schedule.py': ('activity_family', 'ACTIVITY_KIND_FAMILIES', 'LIFESTYLE_CATEGORY_FAMILIES'),
 'life/readable.py': ('ACTIVITY_KIND_LABELS', 'ACTIVITY_DOING_LABELS'),
 'life/memory_evidence.py': ('SUBJECTIVE_TYPES',
                             'SUBJECTIVE_SNAPSHOT_FIELDS',
                             'MEMORY_WRITE_POINTS',
                             'valid_subjective_snapshot',
                             'valid_subjective_snapshots'),
 'life/content.py': ('mail_headers',),
 'life/randomness.py': ('random_stream',),
 'life/control_items.py': ('CONTROL_TOOLS',),
 'evaluation/metrics.py': ('date_blocks',),
 'evaluation/adapters.py': ('parse_world_event',),
 'model_endpoint.py': ('IndependentModelClient',),
 'model_feedback.py': ('correction_message', 'schema_error_details', 'schema_feedback', 'tool_arguments'),
 'resources/models.py': ('AMOUNT_TYPES', 'Conservation'),
 'exchange/types.py': ('LocationKind',
                       'GoodsCategory',
                       'SummarySourceKind',
                       'STORE_KINDS',
                       'PLACE_KIND_ENVIRONMENTS',
                       'GOODS_CATEGORY_LABELS',
                       'START_ACTIVITY_ACTIONS',
                       'ActionCategory',
                       'AppUseManner',
                       'MANNER_DEVICE',
                       'AppPurpose',
                       'GOODS_CATEGORY_STORE_KINDS'),
 'exchange/constants.py': ('RULES_VERSION',
                           'GOODS_ENERGY_KCAL',
                           'WALKING_METERS_PER_SECOND',
                           'DRIVING_METERS_PER_SECOND',
                           'DRIVING_FARE_MINIMUM_FEN',
                           'DRIVING_FARE_FEN_PER_METER',
                           'DELIVERY_METERS_PER_SECOND',
                           'DELIVERY_PREP_SECONDS',
                           'DELIVERY_FEE_FEN',
                           'MAP_MIN_NODES',
                           'MAP_EDGE_LENGTH_M',
                           'MAP_GENERATOR_RULE_VERSION',
                           'MAP_EXTRA_EDGES',
                           'PUSH_HOURS_LOCAL',
                           'PUSH_SUMMARY_MAX_CHARS',
                           'ENTERTAINMENT_RELIEF_PER_HOUR',
                           'GOODS_RECEIPT_EFFECTS',
                           'CATEGORY_MET',
                           'CONTENT_MIN_SECONDS',
                           'SOCIAL_NEED_AWAKE_RATE',
                           'SOCIAL_NEED_CONVERSATION_RATE',
                           'SOCIAL_NEED_CALL_RATE',
                           'SOCIAL_NEED_MESSAGE_RELIEF',
                           'CHAT_RELIEF_DECLINED_PER_HOUR',
                           'MOOD_PULSES',
                           'MOOD_HALFLIFE_MINUTES',
                           'MOOD_HALFLIFE_NEUROTICISM_FACTOR',
                           'MOOD_PULSE_NEUROTICISM_FACTOR',
                           'MOOD_DISCOMFORT_TRIGGER_HOURS',
                           'LIFESTYLE_HEALTH_MOOD_PER_HOUR'),
 'exchange/body.py': ('BodyConstants', 'BODY_CONSTANTS', 'RESOURCE_METAS'),
 'exchange/operators.py': ('evaluate_purchase',
                           'evaluate_goods_receipt',
                           'evaluate_travel_fare',
                           'travel_seconds',
                           'delivery_seconds',
                           'delivery_fee_fen'),
 'catalog/errors.py': ('RegistrationError', 'REGISTRATION_REASONS'),
 'catalog/models.py': ('Catalog',
                       'CatalogManifest',
                       'MapGraph',
                       'MapEdge',
                       'App',
                       'Goods',
                       'Content',
                       'Location',
                       'Action',
                       'SummarySource',
                       'ContentSeries',
                       'GoodsFile',
                       'ActionsFile'),
 'catalog/loader.py': ('load_catalog',)}
# 语义排除仅限普通 logger、Python 类型声明、不同类作用域的薄协议钩子与导入绑定。
SEMANTIC_EXCLUSIONS = ('module logger', 'Python type declaration', 'thin decorated protocol hook', 'import binding')


def _without_docstring(node):
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            return body[1:]
    return body


def _protocol_hook(node, body):
    names = {decorator.func.id for decorator in node.decorator_list
             if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)}
    if not names.intersection({'field_validator', 'model_validator', 'field_serializer', 'model_serializer'}):
        return False
    # 无分支的委托加原值返回仅是协议登记；包含实际业务判断的钩子仍参与扫描。
    return (len(body) == 2 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Call)
            and isinstance(body[1], ast.Return) and isinstance(body[1].value, ast.Name))


def _table(value):
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id in {'frozenset', 'set', 'tuple', 'list'} and len(value.args) == 1):
        value = value.args[0]
    if not isinstance(value, (ast.Dict, ast.Set, ast.Tuple, ast.List)):
        return None
    items = value.keys if isinstance(value, ast.Dict) else value.elts
    if len(items) < 2:
        return None
    if not isinstance(value, ast.Dict) and all(isinstance(item, ast.Name) for item in items):
        # 只有内建类型名的元组为类型声明；业务名的同值规则仍由唯一归宿登记覆盖。
        if all(item.id in {'int', 'float', 'str', 'bytes', 'bool', 'dict', 'list', 'tuple', 'None'} for item in items):
            return None
    return ast.dump(value, include_attributes=False)


def single_source_violations(root):
    ownership = {name: f'src/worldmock/{path}' for path, names in OWNERS.items() for name in names}
    functions, tables, violations = defaultdict(list), defaultdict(list), []
    for base in ('src/worldmock', 'custom'):
        for path in sorted((root / base).rglob('*.py')):
            relative = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name in ownership and relative != ownership[node.name]:
                        violations.append(f'wrong owner: {relative}:{node.lineno} defines {node.name}')
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = _without_docstring(node)
                    if len(body) >= 2 and not _protocol_hook(node, body):
                        key = ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)
                        functions[key].append(f'{relative}:{node.lineno}:{node.name}')
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in ownership and relative != ownership[target.id]:
                        violations.append(f'wrong owner: {relative}:{node.lineno} defines {target.id}')
                key = _table(node.value)
                if key:
                    tables[key].append(f'{relative}:{node.lineno}')
    for kind, groups in (('function clone', functions), ('rule table clone', tables)):
        violations.extend(f'{kind}: {", ".join(group)}' for group in groups.values() if len(group) > 1)
    return violations


def _sample(root, relative, source):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding='utf-8')


def test_production_shared_rules_have_one_owner_and_no_business_clones():
    assert not single_source_violations(ROOT)


def test_current_registry_and_observation_contract_retire_energy_and_game_keys():
    """@brief 当前可供性声明和真实观察合同都没有已退役的能量投影与游戏边界字段。"""
    from worldmock.catalog.affordances import KindSpec, engine_affordances
    from worldmock.life.engine_context import AFFORDANCE_CONTRACT_KEYS, affordance_contract

    assert "energy_projection" not in KindSpec.__dataclass_fields__
    assert AFFORDANCE_CONTRACT_KEYS == {"action", "target_discovery", "target_field", "feedback_eligible"}
    rows = affordance_contract(engine_affordances())
    assert "action" in rows
    assert all(set(row) == AFFORDANCE_CONTRACT_KEYS for row in rows.values())
    assert all(not {"energy_projection", "game_boundary"} & row.keys() for row in rows.values())


def test_new_function_clone_fails_even_with_different_name_and_docstring(tmp_path):
    _sample(tmp_path, 'src/worldmock/a.py', 'def first(value):\n "a"\n result = value + 1\n return result\n')
    _sample(tmp_path, 'custom/b.py', 'def second(value):\n "b"\n result = value + 1\n return result\n')
    assert any('function clone' in item for item in single_source_violations(tmp_path))


def test_copied_rule_table_fails_even_after_renaming(tmp_path):
    _sample(tmp_path, 'src/worldmock/a.py', 'KINDS = {"chat": "social", "call": "social"}\n')
    _sample(tmp_path, 'custom/b.py', 'OTHER_KINDS = {"chat": "social", "call": "social"}\n')
    assert any('rule table clone' in item for item in single_source_violations(tmp_path))


@pytest.mark.parametrize('source', ['def canonical_json(value):\n return value\n',
                                    'class CurvePoint:\n pass\n',
                                    'SUBJECTIVE_TYPES = frozenset({"invented"})\n'])
def test_wrong_shared_rule_owner_fails(tmp_path, source):
    _sample(tmp_path, 'custom/wrong.py', source)
    assert any('wrong owner' in item for item in single_source_violations(tmp_path))


def test_import_logger_type_and_thin_protocol_hooks_are_not_business_clones(tmp_path):
    source = ('from worldmock.footprints.models import canonical_json\nimport logging\n'
              'LOGGER = logging.getLogger(__name__)\nAMOUNTS = (int, float)\n'
              'class Request:\n @field_validator("goals")\n def goals(cls, value):\n'
              '  validate_goals(value)\n  return value\n')
    _sample(tmp_path, 'src/worldmock/a.py', source)
    _sample(tmp_path, 'custom/b.py', source.replace('Request', 'Response'))
    assert not single_source_violations(tmp_path)


def test_protocol_hook_with_business_branch_is_scanned(tmp_path):
    source = ('class Request:\n @field_validator("goals")\n def goals(cls, value):\n'
              '  if len(value) > 8:\n   raise ValueError("too many")\n  return value\n')
    _sample(tmp_path, 'src/worldmock/a.py', source)
    _sample(tmp_path, 'custom/b.py', source.replace('Request', 'Response'))
    assert any('function clone' in item for item in single_source_violations(tmp_path))
