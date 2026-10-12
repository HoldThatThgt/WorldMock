"""共享规则必须留在唯一归宿；结构扫描同时证明真实新增克隆会被拒绝。"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
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
 'life/models.py': ('validated_payload', 'active_side_activities', 'NavigationForeground',
                    'RUN_MANIFEST_SCHEMA_VERSION'),
 'life/navigation_foreground.py': ('settle_navigation', 'navigation_app', 'foreground_event'),
 'life/order_apps.py': ('order_app_ids', 'preferred_app_id'),
 'life/stay_records.py': ('stay_events', 'run_start_event'),
 'life/order_notices.py': ('goods_names', 'goods_text', 'shipping_delay_seconds', 'shipping_schedule',
                           'ORDER_PLACED', 'ORDER_SHIPPED', 'ORDER_DELIVERED', 'ORDER_TITLES',
                           'PAYMENT_SUCCEEDED'),
 'footprints/location_stays.py': ('project_location_stays', 'validate_location_stays'),
 'footprints/export.py': ('EXPORT_SCHEMA_VERSION', 'EXPORT_ARTIFACTS'),
 'footprints/environment_events.py': ('ENTRY_KIND_CAUSES',),
 'life/travel.py': ('plan_trip',),
 'life/travel_methods.py': ('travel_methods', 'travel_method', 'WALKING'),
 'life/readable.py': ('ACTIVITY_KIND_LABELS', 'ACTIVITY_DOING_LABELS', 'interval_phrases'),
 'life/memory_evidence.py': ('SUBJECTIVE_TYPES',
                             'SUBJECTIVE_SNAPSHOT_FIELDS',
                             'MEMORY_WRITE_POINTS',
                             'valid_subjective_snapshot',
                             'valid_subjective_snapshots'),
 'life/randomness.py': ('random_stream',),
 'life/control_items.py': ('CONTROL_TOOLS',),
 'evaluation/metrics.py': ('date_blocks',),
 'evaluation/adapters.py': ('parse_world_event',),
 'model_endpoint.py': ('IndependentModelClient',),
 'model_feedback.py': ('correction_message', 'schema_error_details', 'schema_feedback', 'tool_arguments'),
 'resources/models.py': ('AMOUNT_TYPES', 'Conservation'),
 'exchange/types.py': ('GoodsCategory', 'GOODS_CATEGORY_LABELS'),
 'exchange/constants.py': ('RULES_VERSION',
                           'WALKING_TRAVEL_METHOD_ID',
                           'WALKING_TRAVEL_METHOD_NAME',
                           'WALKING_METERS_PER_SECOND',
                           'DELIVERY_METERS_PER_SECOND',
                           'DELIVERY_PREP_SECONDS',
                           'DELIVERY_FEE_FEN',
                           'GOODS_ENERGY_KCAL',
                           'GOODS_RELAXATION_RELIEF',
                           'MAP_MIN_NODES',
                           'MAP_EDGE_LENGTH_M',
                           'MAP_EXTRA_EDGES',
                           'TRAVEL_MAX_SECONDS',
                           'WEATHER_FACTORS',
                           'PUSH_HOURS_LOCAL',
                           'PUSH_SUMMARY_MAX_CHARS'),
 'exchange/body.py': ('BodyConstants', 'BODY_CONSTANTS', 'RESOURCE_METAS'),
 'exchange/operators.py': ('evaluate_purchase',
                           'evaluate_goods_receipt',
                           'evaluate_travel_fare',
                           'travel_seconds',
                           'delivery_seconds',
                           'delivery_fee_fen'),
 'catalog/errors.py': ('RegistrationError', 'REGISTRATION_REASONS'),
 'catalog/identity.py': ('REQUIRED_CATALOG_FILES', 'OPTIONAL_CATALOG_FILES'),
 'catalog/models.py': ('Catalog',
                       'CatalogManifest',
                       'MapGraph',
                       'MapEdge',
                       'App',
                       'Goods',
                       'Content',
                       'Location',
                       'TravelMethod',
                       'SummarySource',
                       'ContentSeries',
                       'OpeningHours',
                       'ContentPrerequisite',
                       'require_declared_schedule'),
 'catalog/loader.py': ('load_catalog',),
 'input_models.py': ('LEGACY_TEMPLATE_FIELDS', 'HourWindow', 'NonBlankText', 'Identity', 'Situation',
                     'Relationship', 'HabitDeclaration', 'Tastes'),
 'input_warnings.py': ('INPUT_WARNING_CODES', 'InputWarning', 'frozen_input_warnings'),
 'profile/models.py': ('PersonaCard', 'persona_card', 'persona_card_struct'),
 'life/action_contract.py': ('ACTIVITY_ACTIONS',
                             'MAIN_ACTIVITY_ACTIONS',
                             'SIDE_ACTIVITY_ACTIONS',
                             'START_TOOLS',
                             'ACTION_LABELS',
                             'activity_slot',
                             'start_tool'),
 'life/sleep_plan.py': ('parse_sleep_minutes', 'SLEEP_MINUTES_MIN', 'SLEEP_MINUTES_MAX', 'SLEEP_MINUTES_REASON',
                        'SLEEP_WINDOW_SECONDS', 'OVERSLEEP_THRESHOLD_SECONDS', 'OVERSLEEP_MINUTES_MIN',
                        'OVERSLEEP_MINUTES_MAX', 'assumed_sleep', 'sleep_seconds_in_window', 'oversleep_seconds'),
 'evaluation/behavior_metrics.py': ('BEDTIME_MIN_SECONDS', 'BEDTIME_DAY_START_MINUTES'),
 'life/location_activities.py': ('location_units', 'next_content', 'unit_facts', 'held_refusal'),
 'life/content_prerequisite.py': ('prerequisite_unmet', 'app_prerequisite_refusal'),
 'life/content_select.py': ('eligible_contents', 'pinned_push_content'),
 'life/start_group.py': ('GROUP_REJECTION_SUFFIX', 'group_rejection', 'frozen_observation'),
 'life/activity_control.py': ('pausable', 'PAUSE_MIN_REMAINING_SECONDS')}
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


def test_observation_offers_only_current_actions_without_retired_resource_gate(engine_factory):
    """@brief 真实观察只提供四个现行动作，活动工具不附加退役资源门。"""
    engine = engine_factory()
    from tests.life.conftest import context

    data = engine.observe(context(engine, "actions"))
    assert data["known_activity_actions"] == ["travel", "purchase", "sleep", "interact"]
    assert not {"affordance_contract", "game_boundary", "energy_projection"} & data.keys()


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
                                    'SUBJECTIVE_TYPES = frozenset({"invented"})\n',
                                    'GOODS_ENERGY_KCAL = {"snack": 210, "meal": 610}\n',
                                    'GOODS_RELAXATION_RELIEF = 0.2\n',
                                    'NonBlankText = str\n',
                                    'RUN_MANIFEST_SCHEMA_VERSION = 5\n',
                                    'def persona_card(persona):\n return None\n',
                                    'INPUT_WARNING_CODES = ("CARD_FIELD_CONFLICT",)\n',
                                    'WALKING_TRAVEL_METHOD_NAME = "散步"\n',
                                    'WALKING = None\n',
                                    'def travel_methods(catalog):\n return ()\n',
                                    'OVERSLEEP_THRESHOLD_SECONDS = 18000\n',
                                    'PAUSE_MIN_REMAINING_SECONDS = 120\n',
                                    'def pausable(state, activity):\n return True\n',
                                    'BEDTIME_MIN_SECONDS = 7200\n',
                                    'EXPORT_SCHEMA_VERSION = 4\n',
                                    'ORDER_TITLES = frozenset({"订单已取消"})\n',
                                    'def shipping_delay_seconds(interval):\n return interval // 2\n',
                                    'def active_side_activities(state, agent_id):\n return ()\n'])
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
