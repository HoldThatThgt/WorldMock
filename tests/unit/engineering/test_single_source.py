"""共享规则必须留在唯一归宿；结构扫描同时证明真实新增克隆会被拒绝。"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
OWNERS = {
    'src/worldmock/configuration/inputs/models.py': ('LEGACY_TEMPLATE_FIELDS', 'HourWindow', 'NonBlankText', 'Identity', 'Situation', 'Relationship', 'HabitDeclaration', 'Tastes'),
    'src/worldmock/configuration/inputs/warnings.py': ('INPUT_WARNING_CODES', 'InputWarning', 'frozen_input_warnings'),
    'src/worldmock/configuration/model/model_metadata.py': ('IndependentModelClient',),
    'src/worldmock/configuration/persona/persona_card.py': ('PersonaCard', 'persona_card', 'persona_card_struct'),
    'src/worldmock/configuration/persona/work_schedule.py': ('WEEKDAY_NAMES',),
    'src/worldmock/exchange_engine/commerce/operators.py': ('evaluate_purchase', 'evaluate_goods_receipt', 'evaluate_travel_fare', 'travel_seconds', 'delivery_seconds', 'delivery_fee_fen'),
    'src/worldmock/exchange_engine/commerce/shipping.py': ('shipping_delay_seconds',),
    'src/worldmock/exchange_engine/conditions/activity_rules.py': ('ACTIVITY_ACTIONS', 'MAIN_ACTIVITY_ACTIONS', 'SIDE_ACTIVITY_ACTIONS', 'APP_BOUND_ACTIONS', 'activity_slot'),
    'src/worldmock/exchange_engine/definitions/body_constants.py': ('BodyConstants', 'BODY_CONSTANTS', 'RESOURCE_METAS'),
    'src/worldmock/exchange_engine/definitions/constants.py': ('RULES_VERSION', 'WALKING_TRAVEL_METHOD_ID', 'WALKING_TRAVEL_METHOD_NAME', 'WALKING_METERS_PER_SECOND', 'DELIVERY_METERS_PER_SECOND', 'DELIVERY_PREP_SECONDS', 'DELIVERY_FEE_FEN', 'GOODS_ENERGY_KCAL', 'GOODS_RELAXATION_RELIEF', 'MAP_MIN_NODES', 'MAP_EDGE_LENGTH_M', 'MAP_EXTRA_EDGES', 'TRAVEL_MAX_SECONDS', 'WEATHER_FACTORS', 'PUSH_HOURS_LOCAL', 'PUSH_SUMMARY_MAX_CHARS'),
    'src/worldmock/exchange_engine/definitions/types.py': ('GoodsCategory', 'GOODS_CATEGORY_LABELS'),
    'src/worldmock/exchange_engine/ledger/ledger_models.py': ('AMOUNT_TYPES', 'Conservation'),
    'src/worldmock/foundation/atomic_write.py': ('atomic_write',),
    'src/worldmock/foundation/errors.py': ('reject_value',),
    'src/worldmock/foundation/filesystem.py': ('safe_file', 'relative_declaration'),
    'src/worldmock/foundation/model_feedback.py': ('correction_message', 'schema_error_details', 'schema_feedback', 'tool_arguments'),
    'src/worldmock/foundation/models.py': ('StrictModel', 'FrozenModel', 'CurvePoint', 'EfficiencyFactor', 'DeepFrozenModel', 'require_finite_number'),
    'src/worldmock/foundation/randomness.py': ('random_stream',),
    'src/worldmock/foundation/serialization.py': ('canonical_json', 'sha256_json', 'is_sha256'),
    'src/worldmock/foundation/time/calendar.py': ('window_days', 'local_day_bounds', 'split_local_days'),
    'src/worldmock/foundation/time/intervals.py': ('valid_interval', 'is_body_settlement', 'body_settlements'),
    'src/worldmock/foundation/time/readable.py': ('interval_phrases',),
    'src/worldmock/recording_engine/events/environment_events.py': ('ENTRY_KIND_CAUSES',),
    'src/worldmock/recording_engine/events/memory_evidence.py': ('MEMORY_WRITE_POINTS',),
    'src/worldmock/recording_engine/export/export.py': ('EXPORT_SCHEMA_VERSION', 'EXPORT_ARTIFACTS'),
    'src/worldmock/recording_engine/export/post_run.py': ('DROPPED_PAYLOADS', 'post_run_view', 'post_run_row'),
    'src/worldmock/recording_engine/projections/activities/location_stays.py': ('project_location_stays', 'validate_location_stays'),
    'src/worldmock/recording_engine/projections/presentation/display_wording.py': ('DisplayNames', 'display_names', 'goods_items', 'event_content_id', 'activity_phrase', 'phone_phrase', 'TITLE_LIST_LIMIT', 'title_list'),
    'src/worldmock/recording_engine/storage/observation_parts.py': ('WHOLE_PART_KEYS', 'DAY_PART_KEY', 'split_observation', 'rehydrate_observation', 'insert_parts', 'require_parts'),
    'src/worldmock/resource_engine/catalog/identity.py': ('REQUIRED_CATALOG_FILES', 'OPTIONAL_CATALOG_FILES'),
    'src/worldmock/resource_engine/catalog/loader.py': ('load_catalog',),
    'src/worldmock/resource_engine/catalog/models.py': ('Catalog', 'CatalogManifest', 'MapGraph', 'MapEdge', 'App', 'Goods', 'Content', 'Location', 'TravelMethod', 'SummarySource', 'ContentSeries', 'OpeningHours', 'ContentPrerequisite', 'require_declared_schedule'),
    'src/worldmock/resource_engine/catalog/travel_methods.py': ('travel_methods', 'travel_method', 'WALKING'),
    'src/worldmock/resource_engine/registration/errors.py': ('RegistrationError', 'REGISTRATION_REASONS'),
    'src/worldmock/simulation_engine/actions/start_group.py': ('GROUP_REJECTION_SUFFIX', 'group_rejection', 'frozen_observation'),
    'src/worldmock/simulation_engine/activities/content/content_select.py': ('eligible_contents', 'pinned_push_content', 'autoplay_content', 'next_episode', 'opening_duration', 'OpeningDuration'),
    'src/worldmock/simulation_engine/activities/content/location_activities.py': ('location_units', 'next_content', 'unit_facts', 'held_refusal', 'running_series_note'),
    'src/worldmock/simulation_engine/activities/lifecycle/activity_control.py': ('pausable', 'PAUSE_MIN_REMAINING_SECONDS'),
    'src/worldmock/simulation_engine/activities/travel/navigation_foreground.py': ('settle_navigation', 'navigation_app', 'foreground_event'),
    'src/worldmock/simulation_engine/activities/travel/travel.py': ('plan_trip',),
    'src/worldmock/simulation_engine/body/sleep_plan.py': ('SLEEP_WINDOW_SECONDS', 'OVERSLEEP_THRESHOLD_SECONDS', 'OVERSLEEP_MINUTES_MIN', 'OVERSLEEP_MINUTES_MAX', 'assumed_sleep', 'sleep_seconds_in_window', 'oversleep_seconds'),
    'src/worldmock/simulation_engine/commerce/fulfillment/order_notices.py': ('goods_names', 'goods_text', 'shipping_schedule'),
    'src/worldmock/simulation_engine/commerce/purchase/order_apps.py': ('order_app_ids', 'preferred_app_id', 'selling_app_ids'),
    'src/worldmock/simulation_engine/evidence/activity.py': ('app_use_ended', 'OUTCOME_APP_USE_ENDED'),
    'src/worldmock/simulation_engine/evidence/stays.py': ('stay_events', 'run_start_event'),
    'src/worldmock/simulation_engine/observation/wording/activity_titles.py': ('progress_snapshot',),
    'src/worldmock/simulation_engine/observation/wording/content_prerequisite.py': ('prerequisite_unmet', 'app_prerequisite_refusal', 'time_phrase', 'app_time_state'),
    'src/worldmock/state/identity.py': ('RUN_MANIFEST_SCHEMA_VERSION',),
    'src/worldmock/state/interaction/actions.py': ('START_TOOLS', 'start_tool'),
    'src/worldmock/state/interaction/control_items.py': ('CONTROL_TOOLS',),
    'src/worldmock/state/interaction/sleep.py': ('parse_sleep_minutes', 'SLEEP_MINUTES_MIN', 'SLEEP_MINUTES_MAX', 'SLEEP_MINUTES_REASON'),
    'src/worldmock/state/model/memory.py': ('SUBJECTIVE_TYPES', 'SUBJECTIVE_SNAPSHOT_FIELDS', 'valid_subjective_snapshot', 'valid_subjective_snapshots'),
    'src/worldmock/state/observation/labels.py': ('ACTIVITY_KIND_LABELS', 'ACTIVITY_DOING_LABELS', 'ACTION_LABELS'),
    'src/worldmock/state/world/agent.py': ('NavigationForeground',),
    'src/worldmock/state/world/order_notices.py': ('ORDER_PLACED', 'ORDER_SHIPPED', 'ORDER_DELIVERED', 'ORDER_TITLES', 'PAYMENT_SUCCEEDED'),
    'src/worldmock/state/world/world.py': ('validated_payload', 'active_side_activities'),
    'tools/analysis/behavior/metrics.py': ('BEDTIME_MIN_SECONDS', 'BEDTIME_DAY_START_MINUTES'),
}
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
    ownership = {name: path for path, names in OWNERS.items() for name in names}
    functions, tables, violations = defaultdict(list), defaultdict(list), []
    for base in ('src/worldmock', 'custom', 'tools/analysis'):
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
    """@brief 真实观察只提供五个现行动作，活动工具不附加退役资源门。"""
    engine = engine_factory()
    from tests.life.conftest import context

    data = engine.observe(context(engine, "actions"))
    assert data["known_activity_actions"] == ["travel", "purchase", "sleep", "interact", "query"]
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
                                    'def active_side_activities(state, agent_id):\n return ()\n',
                                    'TITLE_LIST_LIMIT = 5\n',
                                    'def opening_duration(catalog, state, request):\n return None\n'])
def test_wrong_shared_rule_owner_fails(tmp_path, source):
    _sample(tmp_path, 'custom/wrong.py', source)
    assert any('wrong owner' in item for item in single_source_violations(tmp_path))


def test_import_logger_type_and_thin_protocol_hooks_are_not_business_clones(tmp_path):
    source = ('from worldmock.foundation.serialization import canonical_json\nimport logging\nLOGGER = logging.getLogger(__name__)\nAMOUNTS = (int, float)\nclass Request:\n @field_validator("goals")\n def goals(cls, value):\n  validate_goals(value)\n  return value\n')
    _sample(tmp_path, 'src/worldmock/a.py', source)
    _sample(tmp_path, 'custom/b.py', source.replace('Request', 'Response'))
    assert not single_source_violations(tmp_path)


def test_protocol_hook_with_business_branch_is_scanned(tmp_path):
    source = ('class Request:\n @field_validator("goals")\n def goals(cls, value):\n'
              '  if len(value) > 8:\n   raise ValueError("too many")\n  return value\n')
    _sample(tmp_path, 'src/worldmock/a.py', source)
    _sample(tmp_path, 'custom/b.py', source.replace('Request', 'Response'))
    assert any('function clone' in item for item in single_source_violations(tmp_path))
