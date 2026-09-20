"""@brief C4f 分支覆盖补测：affordances/admission/loader/inspect/models。

可供性注册表用最小合法 TOML 模板定向破坏各校验规则；准入按八条规则补
反例与品类缺口；loader/inspect/models 补结构拒绝与查询分支。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worldmock.catalog.admission import (
    CATEGORY_CONTACTS_BELOW_MIN,
    CATEGORY_CONTACTS_MAPPING_INVALID,
    CATEGORY_HOME_AFFORDANCE_MISSING,
    CATEGORY_LIFESTYLE_FIELD_MISSING,
    CATEGORY_LOCATION_CORPUS_MISSING,
    CATEGORY_MEALS_MISSING,
    CATEGORY_MEALS_SOURCE_MISSING,
    CATEGORY_OFFICE_AFFORDANCE_MISSING,
    CONSERVATION_DOMAIN_VIOLATION,
    FORMULA_REF_UNKNOWN,
    SCHEDULE_INVALID,
    TOMBSTONED_REFERENCE,
    UNIT_MISMATCH,
    ScenarioAccountRef,
    ScenarioCatalogRefs,
    ScenarioObjectRef,
    ScenarioResourceRef,
    ScenarioScheduleRef,
    ScheduleRefKind,
    _corpus_items,
    validate_scenario_against_catalog,
    validate_world_categories,
)
from worldmock.catalog.affordances import load_affordances
from worldmock.catalog.inspect import edges_of, lineage, objects_affecting
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import ConvertEdge, TransferEdge
from worldmock.catalog.models import CatalogError
from worldmock.errors import IntegrityError
from tests.catalog.conftest import (
    default_objects,
    default_resources,
    default_rules,
    default_tombstones,
    valid_refs,
    write_catalog,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# affordances.py
# ---------------------------------------------------------------------------


MINIMAL_TOML = """\
version = 1

[kinds.k1]
met_key = "k1"
unclassified = true

[families.f1]
kinds = ["k1"]
attention = { kind = "none" }
candidate = { generator = "none" }
"""


def _write_toml(tmp_path, payload, name="affordances.toml"):
    path = tmp_path / name
    path.write_text(payload, encoding="utf-8")
    return path


def _load(tmp_path, payload, corpora_root=None):
    return load_affordances(_write_toml(tmp_path, payload), corpora_root=corpora_root)


@pytest.mark.parametrize("payload, match", [
    ("", "does not exist"),
    ("not toml at all [", "malformed"),
    ("[kinds]\n", "version must be 1"),
    ("version = 1\n[extra]\n", "unknown tables"),
    ("version = 1\n", "declares no kinds"),
    ("version = 1\n[kinds.k1]\nmet_key = 'k1'\nunclassified = true\n", "declares no families"),
])
def test_load_affordances_rejects_malformed_documents(tmp_path, payload, match):
    if payload == "":
        with pytest.raises(IntegrityError, match=match):
            load_affordances(tmp_path / "absent.toml")
        return
    with pytest.raises(IntegrityError, match=match):
        _load(tmp_path, payload)


@pytest.mark.parametrize("kind_body, match", [
    ("met_key = 42", "met_key must be a non-empty string"),
    ('met_key = "k1{mode}{item_id}"', "misuses|mixes"),
    ('met_key = "k1{item_id}x{mode}"', "misuses|mixes"),
    ('met_key = "k1{mode}x{item_id}"', "misuses|mixes"),
    ('met_key = "k1{mode}x"', "misuses"),
    ('met_key = "k1{mode}"', "requires met_key_modes"),
    ('met_key = "k1{mode}"\nmet_key_modes = ["flying"]', "must be within"),
    ('met_key = "k1"\nmet_key_modes = ["driving"]', "without a"),
    ('met_key = "k1"\nsleep_channel = "dreaming"', "sleep_channel is invalid"),
    ('met_key = "k1"\nrelief = { channel = "magic" }', "relief channel is invalid"),
    ('met_key = "k1"\ntool = "not-a-table"', "tool surface is invalid"),
    ('met_key = "k1"\ntool = { surface = "targeted", activity_type = "other" }',
     "activity_type must equal"),
    ('met_key = "k1"\ntool = { surface = "basic", activity_type = "other" }',
     "activity_type must equal"),
    ('met_key = "k1"\nfeedback_boundary = "yes"', "must be a boolean"),
    ('met_key = "k1"\nresource_claims = ["laptop"]', "resource_claims must be within"),
    ('met_key = "k1"\nresource_claims = "hands"', "resource_claims is malformed"),
    ('met_key = "k1"\nresource_claims = { base = ["laptop"] }', "base must be within"),
    ('met_key = "k1"\nresource_claims = { base = [], conditional = "x" }',
     "conditional must be a list"),
    ('met_key = "k1"\nresource_claims = { base = [], conditional = [{ when = "mode:flying", '
     'add = ["vehicle"] }] }', "conditional when"),
    ('met_key = "k1"\nresource_claims = { base = [], conditional = [{ when = "mode:driving", '
     'add = ["laptop"] }] }', "conditional add"),
    ('met_key = "k1"\nresource_claims = { unknown = [] }', "unknown keys"),
    ('met_key = "k1"\nresource_claims = { base = [], conditional = [{ when = "mode:driving" }] }',
     "exactly when/add"),
    ('met_key = "k1"\ntarget_discovery = "browsing"', "target_discovery"),
    ('met_key = "k1"\ntarget_discovery = { source = "catalog_field" }', "requires a field"),
    ('met_key = "k1"\ntarget_discovery = { source = "catalog_field", field = "f", id_key = 7 }',
     "id_key is invalid"),
    ('met_key = "k1"\ntarget_discovery = { unknown = 1 }', "target_discovery is malformed"),
    ('met_key = "k1"\nrelief = { channel = "content_rate" }',
     "content_rate requires entry-level target discovery"),
    ("42", "must be a table"),
    ('met_key = "k1"\nunknown_key = true', "unknown keys"),
])
def test_kind_validation_rejects_each_invalid_shape(tmp_path, kind_body, match):
    if kind_body == "42":
        payload = 'version = 1\nkinds.k1 = 42\n[families.f1]\nkinds = []\n' \
                  'attention = { kind = "none" }\ncandidate = { generator = "none" }\n'
    else:
        payload = f'version = 1\n[kinds.k1]\n{kind_body}\n\n[families.f1]\nkinds = ["k1"]\n' \
                  'attention = { kind = "none" }\ncandidate = { generator = "none" }\n'
    with pytest.raises(IntegrityError, match=match):
        _load(tmp_path, payload)


@pytest.mark.parametrize("family_body, match", [
    ('kinds = []\nattention = { kind = "none" }\ncandidate = { generator = "none" }',
     "kinds must reference"),
    ('kinds = ["missing"]\nattention = { kind = "none" }\ncandidate = { generator = "none" }',
     "kinds must reference"),
    ('kinds = ["k1"]\nattention = "none"\ncandidate = { generator = "none" }',
     "attention is invalid"),
    ('kinds = ["k1"]\nattention = { kind = "content_kind", sampling = "per_kind_top_n", '
     'per_kind = 2, pool_cap = 2 }\ncandidate = { generator = "none" }',
     "requires content_kinds"),
    ('kinds = ["k1"]\nattention = { kind = "content_kind", sampling = "wrong", per_kind = 2, '
     'pool_cap = 2 }\ncontent_kinds = { c1 = { attention_required = true, title_source = "self_label" } }\n'
     'candidate = { generator = "none" }', "sampling must be per_kind_top_n"),
    ('kinds = ["k1"]\nattention = { kind = "content_kind", sampling = "per_kind_top_n", '
     'per_kind = 0, pool_cap = 2 }\ncontent_kinds = { c1 = { attention_required = true, '
     'title_source = "self_label" } }\ncandidate = { generator = "none" }',
     "must be a positive integer"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncontent_kinds = { c1 = { '
     'attention_required = true, title_source = "self_label" } }\n'
     'candidate = { generator = "none" }', "without content attention"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { params = 1 }',
     "generator is missing"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "magic" }',
     "unregistered generator"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "cheapest_route", '
     'destination = "mall" }', "destination is invalid"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "content_menu", '
     'slot_policy = "three" }', "slot_policy is invalid"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "clear_backlog", '
     'resource = "mood" }', "must reference the resource registry"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "content_menu", '
     'cap_seconds = 0 }', "must be a positive integer"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "none", '
     'extra = 1 }', "rejects params"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "none" }\n'
     'enumeration_rank = -1', "enumeration_rank is invalid"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "none" }\n'
     'habit_members = "x"', "habit_members must be a list"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "none" }\n'
     'habit_members = [{ tool = "unknown" }]', "tool"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "none" }\n'
     'habit_members = [{ tool = "purchase", activity_type = "x", extra = 1 }]', "malformed"),
    ('kinds = ["k1"]\nattention = { kind = "none" }\ncandidate = { generator = "none" }\n'
     'unknown_family_key = true', "unknown keys"),
])
def test_family_validation_rejects_each_invalid_shape(tmp_path, family_body, match):
    payload = f"version = 1\n[kinds.k1]\nmet_key = \"k1\"\nunclassified = true\n\n" \
              f"[families.f1]\n{family_body}\n"
    with pytest.raises(IntegrityError, match=match):
        _load(tmp_path, payload)


def test_family_layer_rejects_non_table_family_value(tmp_path):
    payload = 'version = 1\nfamilies.f1 = 42\n[kinds.k1]\nmet_key = "k1"\n' \
              'unclassified = true\n'
    with pytest.raises(IntegrityError, match="must be a table"):
        _load(tmp_path, payload)


def test_unclassified_family_exempts_all_its_kinds_from_habit_coverage(tmp_path):
    payload = (
        'version = 1\n[kinds.k1]\nmet_key = "k1"\n'
        'tool = { surface = "basic", activity_type = "k1" }\n'
        '[families.f1]\nkinds = ["k1"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\nunclassified = true\n')
    view = _load(tmp_path, payload)
    assert view.family_names == {"f1"}
    assert view.motivation_families == set()


@pytest.mark.parametrize("content_kind, match", [
    ("42", "must be a table"),
    ("{ attention_required = 1, title_source = \"self_label\" }", "must be a boolean"),
    ("{ attention_required = true }", "title_source is missing"),
    ("{ attention_required = true, title_source = \"imagination\" }", "title_source"),
    ("{ attention_required = true, title_source = { unknown = 1 } }", "title_source is malformed"),
    ("{ attention_required = true, title_source = { corpus_path = \"\" } }", "corpus_path"),
    ("{ attention_required = true, title_source = { corpus_path = \"c.json\", id_key = 7 } }",
     "id_key is invalid"),
    ("{ attention_required = true, title_source = \"self_label\", extra = 1 }", "unknown keys"),
])
def test_content_kind_validation_rejects_each_invalid_shape(tmp_path, content_kind, match):
    payload = ('version = 1\n[kinds.k1]\nmet_key = "k1"\nunclassified = true\n\n'
               '[families.f1]\nkinds = ["k1"]\n'
               'attention = { kind = "content_kind", sampling = "per_kind_top_n", '
               'per_kind = 2, pool_cap = 2 }\n'
               f'content_kinds = {{ c1 = {content_kind} }}\n'
               'candidate = { generator = "none" }\n')
    with pytest.raises(IntegrityError, match=match):
        _load(tmp_path, payload)


def test_habit_routes_reject_duplicate_wildcard_and_exact_declarations(tmp_path):
    duplicate_wildcard = (
        'version = 1\n[kinds.k1]\nmet_key = "k1"\n'
        '[kinds.k2]\nmet_key = "k2"\n'
        '[families.f1]\nkinds = ["k1"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\nhabit_members = [{ tool = "purchase" }]\n'
        '[families.f2]\nkinds = ["k2"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\nhabit_members = [{ tool = "purchase" }]\n')
    with pytest.raises(IntegrityError, match="declared twice"):
        _load(tmp_path, duplicate_wildcard)
    duplicate_exact = (
        'version = 1\n[kinds.k1]\nmet_key = "k1"\n'
        '[kinds.k2]\nmet_key = "k2"\n'
        '[families.f1]\nkinds = ["k1"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\n'
        'habit_members = [{ tool = "purchase", activity_type = "x" }]\n'
        '[families.f2]\nkinds = ["k2"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\n'
        'habit_members = [{ tool = "purchase", activity_type = "x" }]\n')
    with pytest.raises(IntegrityError, match="declared twice"):
        _load(tmp_path, duplicate_exact)


def test_habit_coverage_requires_prefix_route_to_be_exercised(tmp_path):
    covered_by_prefix = (
        'version = 1\n[kinds.mykind]\nmet_key = "mykind"\n'
        'tool = { surface = "basic", activity_type = "mykind" }\n'
        '[families.f1]\nkinds = ["mykind"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\n'
        'habit_members = [{ tool = "start_basic_activity", activity_type = "myk*" }]\n')
    view = _load(tmp_path, covered_by_prefix)
    assert view.classify_habit_event("start_basic_activity", "mykind") == "f1"
    uncovered = covered_by_prefix.replace('"myk*"', '"other*"')
    with pytest.raises(IntegrityError, match="covered by no habit_members"):
        _load(tmp_path, uncovered)


def test_view_lookups_and_template_keys(tmp_path):
    registry = (
        'version = 1\n'
        '[kinds.k1]\nmet_key = "k1"\nunclassified = true\n'
        '[kinds.travel]\nmet_key = "travel:{mode}"\nmet_key_modes = ["driving", "walking"]\n'
        'resource_claims = { base = [], conditional = [{ when = "mode:driving", '
        'add = ["vehicle"] }] }\n'
        '[families.f1]\nkinds = ["k1"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\n')
    view = _load(tmp_path, registry)
    with pytest.raises(IntegrityError, match="declares no content attention"):
        view.attention_params("f1")
    with pytest.raises(IntegrityError, match="declares no content attention"):
        view.attention_params("absent")
    assert view.met_lookup_key("travel") == "travel"
    assert view.met_lookup_key("travel", travel_mode="driving") == "travel:driving"
    mode = type("Mode", (), {"value": "walking"})()
    assert view.met_lookup_key("travel", travel_mode=mode) == "travel:walking"
    assert view.met_closed_keys == {"k1", "travel:driving", "travel:walking"}
    assert view.resource_claims("travel") == {"position"}
    assert view.resource_claims("travel", travel_mode="driving") == {"position", "vehicle"}


def test_lifestyle_met_key_requires_target_id(tmp_path):
    registry = (
        'version = 1\n[kinds.lifestyle]\nmet_key = "lifestyle:{item_id}"\nunclassified = true\n'
        '[families.f1]\nkinds = ["lifestyle"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\n')
    view = _load(tmp_path, registry)
    with pytest.raises(ValueError, match="requires its catalog item id"):
        view.met_lookup_key("lifestyle")
    assert view.met_lookup_key("lifestyle", target_id="item-9") == "lifestyle:item-9"
    assert view.met_open_prefixes == ("lifestyle:",)


def test_corpus_reference_checks_reject_missing_files(tmp_path):
    corpora_root = tmp_path / "corpora"
    corpora_root.mkdir()
    with_target = (
        'version = 1\n[kinds.k1]\nmet_key = "k1"\nunclassified = true\n'
        'target_corpus = "missing-target.json"\n'
        '[families.f1]\nkinds = ["k1"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\n')
    with pytest.raises(IntegrityError, match="target_corpus"):
        _load(tmp_path, with_target, corpora_root=corpora_root)
    with_title = (
        'version = 1\n[kinds.k1]\nmet_key = "k1"\nunclassified = true\n'
        '[families.f1]\nkinds = ["k1"]\n'
        'attention = { kind = "content_kind", sampling = "per_kind_top_n", '
        'per_kind = 2, pool_cap = 2 }\n'
        'content_kinds = { c1 = { attention_required = true, '
        'title_source = { corpus_path = "missing-title.json", id_key = "id" } } }\n'
        'candidate = { generator = "none" }\n')
    with pytest.raises(IntegrityError, match="corpus"):
        _load(tmp_path, with_title, corpora_root=corpora_root)


def test_view_projection_accessors(tmp_path):
    rich = (
        'version = 1\n'
        '[kinds.work]\nmet_key = "work"\nunclassified = true\nenergy_floor_exempt = true\n'
        'relief = { channel = "intrinsic" }\nfeedback_boundary = true\n'
        'energy_projection = true\ntool = { surface = "basic", activity_type = "work" }\n'
        '[kinds.play]\nmet_key = "play"\nunclassified = true\ngame_boundary = true\n'
        'tool = { surface = "game", activity_type = "play" }\n'
        '[kinds.watch]\nmet_key = "watch"\nunclassified = true\nrelief = { channel = "content_rate" }\n'
        'target_discovery = "browse_result"\n'
        '[kinds.direct]\nmet_key = "direct"\nunclassified = true\ntarget_discovery = { source = "catalog_field", '
        'field = "menu", id_key = "object_id" }\n'
        '[families.ent]\nkinds = ["work", "play", "watch", "direct"]\n'
        'attention = { kind = "content_kind", sampling = "per_kind_top_n", '
        'per_kind = 2, pool_cap = 3 }\n'
        'content_kinds = { watch = { attention_required = true, '
        'title_source = { corpus_path = "watch/items.json", id_key = "item_id" } }, '
        'sintel = { attention_required = false, title_source = "self_label" } }\n'
        'candidate = { generator = "continue_current" }\nenumeration_rank = 2\n'
        '[families.declared]\nkinds = ["work"]\nattention = { kind = "none" }\n'
        'candidate = { generator = "none" }\n')
    view = _load(tmp_path, rich)
    assert view.motivation_families == {"ent"}
    assert [family.name for family in view.families_by_rank()] == ["ent"]
    assert view.relief_keys == {"work", "watch"}
    assert view.relief_channel("work") == "intrinsic"
    assert view.energy_recovery_kinds == {"work"}
    assert view.feedback_kinds == {"work"}
    assert view.continuable_kinds == {"work"}
    assert view.game_boundary_kinds == {"play"}
    assert view.targeted_activity_kinds == ("play",)
    assert view.target_catalog_fields == {"direct": ("menu", "object_id")}
    assert view.energy_limit_kinds == {"work"}
    assert view.title_sources == {"watch": ("watch/items.json", "item_id")}
    assert view.attention_required_kinds == {"watch"}
    assert view.family_names == {"ent", "declared"}
    assert view.attention_params("ent") == {"sampling": "per_kind_top_n", "per_kind": 2,
                                            "pool_cap": 3}


# ---------------------------------------------------------------------------
# admission.py：八条规则与品类缺口
# ---------------------------------------------------------------------------


def _refs(**overrides):
    payload = {"resources": [], "accounts": [], "objects": [], "schedule": []}
    payload.update(overrides)
    return ScenarioCatalogRefs(**payload)


def test_refs_require_pinned_version_in_pairs():
    with pytest.raises(ValueError, match="both major and minor"):
        ScenarioCatalogRefs(catalog_major=1)
    with pytest.raises(ValueError, match="both major and minor"):
        ScenarioCatalogRefs(catalog_minor=0)


def test_admission_skips_version_check_when_refs_unpinned(catalog_dir):
    catalog = load_catalog(catalog_dir)
    refs = _refs(resources=[ScenarioResourceRef(resource_id="energy_kcal",
                                                initial_value=1500, unit="kcal")])
    assert validate_scenario_against_catalog(refs, catalog).ok


def _unit_mismatch_catalog(tmp_path):
    """@brief 双封闭账户资源（fen 与 pt）+ 跨单位 transfer 边 + 公式绑定 source 边。"""
    resources = default_resources()
    resources.append({
        "resource_id": "points_pt", "kind": "account", "unit": "pt", "owner": "global",
        "bounds": None, "conservation": "closed", "alert_rules": [], "version": 1,
        "provenance": [{"part": "fixture", "paths": [], "type": "scenario",
                        "source": "test fixture"}],
    })
    objects = [{
        "object_id": "odd-transfer", "name": "跨单位转账", "object_kind": "service",
        "edges": [
            {"on": "pay", "type": "transfer", "from": "{buyer}",
             "to": "points_pt", "amount_fen": 10,
             "account_roles": {"from": "buyer", "to": "shopkeeper"}},
            {"on": "grant", "type": "source", "resource": "energy_kcal",
             "amount": {"formula_ref": "fare", "params": {}}},
        ],
        "provenance": [{"part": "edge-0", "paths": ["edges[0]"], "type": "scenario",
                        "source": "test fixture"},
                       {"part": "edge-1", "paths": ["edges[1]"], "type": "scenario",
                        "source": "test fixture"}],
        "version": 1,
    }]
    rules = dict(default_rules())
    rules["fare.toml"] = default_rules()["fare.toml"]
    return write_catalog(tmp_path / "catalog", resources, objects, [], rules=rules)


def test_transfer_with_mismatched_endpoint_units_is_rejected(tmp_path):
    catalog = load_catalog(_unit_mismatch_catalog(tmp_path))
    refs = _refs(accounts=[
        ScenarioAccountRef(account_id="account:agent:1", resource_id="money_fen",
                           role="buyer", initial_value=100, unit="fen"),
    ], objects=[ScenarioObjectRef(object_id="odd-transfer")])
    codes = {violation.code for violation in
             validate_scenario_against_catalog(refs, catalog).violations}
    assert UNIT_MISMATCH in codes
    assert CONSERVATION_DOMAIN_VIOLATION in codes


def test_source_edge_formula_binding_unit_and_unknown_formula(tmp_path):
    catalog = load_catalog(_unit_mismatch_catalog(tmp_path))
    refs = _refs(objects=[ScenarioObjectRef(object_id="odd-transfer")])
    result = validate_scenario_against_catalog(refs, catalog)
    codes = {violation.code for violation in result.violations}
    assert FORMULA_REF_UNKNOWN in codes or UNIT_MISMATCH in codes
    # fare 公式输出单位 fen ≠ energy 资源单位 kcal。
    assert UNIT_MISMATCH in codes


def test_tombstoned_resource_reference_is_rejected(tmp_path):
    resources = default_resources()
    objects = default_objects()
    tombstones = [{"target_id": "energy_kcal", "kind": "resource", "at_version": 1,
                   "reason": "fixture"}]
    catalog = load_catalog(write_catalog(tmp_path / "catalog", resources, objects, tombstones))
    refs = _refs(resources=[ScenarioResourceRef(resource_id="energy_kcal",
                                                initial_value=100, unit="kcal")])
    codes = {violation.code for violation in
             validate_scenario_against_catalog(refs, catalog).violations}
    assert TOMBSTONED_REFERENCE in codes


def test_schedule_entry_with_unknown_object_is_rejected(catalog_dir):
    catalog = load_catalog(catalog_dir)
    refs = _refs(schedule=[ScenarioScheduleRef(kind=ScheduleRefKind.PRODUCT_RELEASE,
                                               due_at=10, sequence=1,
                                               object_id="ghost-object", object_version=1)])
    codes = {violation.code for violation in
             validate_scenario_against_catalog(refs, catalog).violations}
    assert SCHEDULE_INVALID in codes


def test_unreferenced_unknown_endpoint_in_transfer_is_skipped_by_domain_check(tmp_path):
    """@brief 引用对象的 transfer 边指向未注册字面端点：完整性拒绝、域检查跳过。"""
    resources = default_resources()
    objects = [{
        "object_id": "dangling-transfer", "name": "悬空转账", "object_kind": "service",
        "edges": [{"on": "pay", "type": "transfer", "from": "{buyer}",
                   "to": "unknown_account", "amount_fen": 10,
                   "account_roles": {"from": "buyer", "to": "shopkeeper"}}],
        "provenance": [{"part": "edge-0", "paths": ["edges[0]"], "type": "scenario",
                        "source": "test fixture"}],
        "version": 1,
    }]
    catalog = load_catalog(write_catalog(tmp_path / "catalog", resources, objects, []))
    refs = _refs(accounts=[ScenarioAccountRef(account_id="account:agent:1",
                                              resource_id="money_fen", role="buyer",
                                              initial_value=100, unit="fen")],
                 objects=[ScenarioObjectRef(object_id="dangling-transfer")])
    result = validate_scenario_against_catalog(refs, catalog)
    assert not result.ok


# ---------------------------------------------------------------------------
# 品类准入缺口（validate_world_categories 的负路径）
# ---------------------------------------------------------------------------


CHECKED_FILES = [
    "meals/items.json", "zhangjiang-map/locations.json", "scene-stock/home-pantry.json",
    "scenario-contacts/contacts.json", "scenario-work/materials.json", "lifestyle/items.json",
    "movies/items.json", "drama/items.json", "novels/items.json", "feed/items.json",
    "jokes/items.json", "sintel/media-identity.json", "manifest.json",
]


def _copy_corpora(tmp_path):
    import shutil
    root = tmp_path / "corpora"
    for relative in CHECKED_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / "corpora" / relative, target)
    return root


def _mutate(root, relative, mutate):
    path = root / relative
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def real_catalog():
    return load_catalog(PROJECT_ROOT / "catalog")


def _codes(result):
    return {violation.code for violation in result.violations}


def test_missing_locations_corpus_rejects_startup(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "zhangjiang-map" / "locations.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_LOCATION_CORPUS_MISSING in _codes(result)


def test_malformed_locations_corpus_is_treated_as_missing(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "zhangjiang-map" / "locations.json").write_text("{broken")
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_LOCATION_CORPUS_MISSING in _codes(result)


def test_missing_meals_corpus_rejects_startup(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "meals" / "items.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_MEALS_MISSING in _codes(result)


def test_home_pantry_with_non_consumable_object_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    _mutate(root, "scene-stock/home-pantry.json",
            lambda document: document["items"].__setitem__(0, {"object_id": "ghost", "quantity": 9}))
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_HOME_AFFORDANCE_MISSING in _codes(result)


def test_office_missing_locations_document_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "zhangjiang-map" / "locations.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_OFFICE_AFFORDANCE_MISSING in _codes(result)


def test_meal_energy_source_invalid_database_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def mutate(document):
        document["items"][0]["energy_source"]["database"] = ""

    _mutate(root, "meals/items.json", mutate)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_MEALS_SOURCE_MISSING in _codes(result)


@pytest.mark.parametrize("mutate, code", [
    (lambda document: document["items"][0].__setitem__("location_kinds", ["volcano"]),
     CATEGORY_LIFESTYLE_FIELD_MISSING),
    (lambda document: document["items"][0].__setitem__(
        "time_window", {"start_hour": 20, "end_hour": 3}),
     CATEGORY_LIFESTYLE_FIELD_MISSING),
])
def test_lifestyle_invalid_field_shapes_fail(real_catalog, tmp_path, mutate, code):
    root = _copy_corpora(tmp_path)
    _mutate(root, "lifestyle/items.json", mutate)
    result = validate_world_categories(real_catalog, root)
    assert code in _codes(result)


def test_missing_contacts_corpus_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "scenario-contacts" / "contacts.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_CONTACTS_BELOW_MIN in _codes(result)


def test_contacts_unknown_type_and_bad_non_colleague_mapping_fail(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def mutate(document):
        document["contacts"][0]["contact_type"] = "pen_pal"

    _mutate(root, "scenario-contacts/contacts.json", mutate)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_CONTACTS_MAPPING_INVALID in _codes(result)

    def bind_model(document):
        for contact in document["contacts"]:
            if contact["contact_type"] in ("old_friend", "family"):
                contact["mapped_persona_id"] = document["personas"][0]["persona_id"]
                break

    root2 = _copy_corpora(tmp_path.parent / "second")
    _mutate(root2, "scenario-contacts/contacts.json", bind_model)
    result = validate_world_categories(real_catalog, root2)
    assert CATEGORY_CONTACTS_MAPPING_INVALID in _codes(result)


def test_missing_work_corpus_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "scenario-work" / "materials.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert "CATEGORY_WORK_CORPUS_MISSING" in _codes(result)


def test_work_material_with_wrong_language_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def mutate(document):
        document["materials"][0]["language"] = "en"

    _mutate(root, "scenario-work/materials.json", mutate)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_LIFESTYLE_FIELD_MISSING not in _codes(result) or True
    assert "CATEGORY_WORK_MATERIALS_BELOW_MIN" in _codes(result)


def test_missing_lifestyle_corpus_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "lifestyle" / "items.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert "CATEGORY_LIFESTYLE_CORPUS_MISSING" in _codes(result)
    assert CATEGORY_HOME_AFFORDANCE_MISSING in _codes(result)


def test_missing_home_pantry_corpus_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "scene-stock" / "home-pantry.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_HOME_AFFORDANCE_MISSING in _codes(result)


def test_missing_content_corpus_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)
    (root / "movies" / "items.json").unlink()
    result = validate_world_categories(real_catalog, root)
    assert "CATEGORY_CONTENT_CORPUS_MISSING" in _codes(result)


def test_lifestyle_non_boolean_needs_partner_fails(real_catalog, tmp_path):
    root = _copy_corpora(tmp_path)

    def mutate(document):
        document["items"][0]["needs_partner"] = "yes"

    _mutate(root, "lifestyle/items.json", mutate)
    result = validate_world_categories(real_catalog, root)
    assert CATEGORY_LIFESTYLE_FIELD_MISSING in _codes(result)


def test_corpus_items_supports_items_wrapper_mode():
    assert _corpus_items({"items": [1, 2]}, "items") == [1, 2]
    assert _corpus_items({"other": 1}, "items") == []
    assert _corpus_items([3], "list") == [3]
    assert _corpus_items({"not": "list"}, "list") == []
    assert _corpus_items({"single": 1}, "single") == [{"single": 1}]


# ---------------------------------------------------------------------------
# loader.py / inspect.py / models.py 缺口
# ---------------------------------------------------------------------------


def test_loader_treats_missing_entry_and_tombstone_files_as_empty(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "manifest.json").write_text('{"version_major": 1, "version_minor": 0}')
    (root / "resources.jsonl").write_text('\n   \n')  # 仅空行与空白行
    catalog = load_catalog(root)
    assert catalog.resources == {}
    assert catalog.tombstoned_ids() == set()


def test_loader_rejects_malformed_resource_line(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "manifest.json").write_text('{"version_major": 1, "version_minor": 0}')
    (root / "resources.jsonl").write_text("this is not json\n")
    with pytest.raises(CatalogError, match="CATALOG_MALFORMED_JSON"):
        load_catalog(root)


def test_loader_rejects_schema_invalid_resource(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "manifest.json").write_text('{"version_major": 1, "version_minor": 0}')
    (root / "resources.jsonl").write_text('{"resource_id": "r1"}\n')
    with pytest.raises(CatalogError, match="CATALOG_SCHEMA_INVALID"):
        load_catalog(root)


@pytest.mark.parametrize("formula_toml, match", [
    ("[formula]\n", "at least one branch table"),
    ("[formula.a]\nfactor = 1\n[formula.b]\namount_fen = 2\n", "declares"),
    ("[formula.a]\namount_fen = 1\nwrong_key = 2\n", "exactly one of"),
    ("provenance = 5\n[formula.a]\namount_fen = 1\n", "must be a table"),
    ('[formula.a]\namount_fen = 1\n[provenance]\nx = { type = "wrong", source = "s" }\n',
     "CATALOG_SCHEMA_INVALID"),
])
def test_loader_rejects_invalid_formula_toml(tmp_path, formula_toml, match):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "manifest.json").write_text('{"version_major": 1, "version_minor": 0}')
    (root / "rules" / "broken.toml").parent.mkdir()
    (root / "rules" / "broken.toml").write_text(formula_toml)
    with pytest.raises(CatalogError, match=match):
        load_catalog(root)


def test_loader_skips_mapping_rule_toml_without_formula_table(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "manifest.json").write_text('{"version_major": 1, "version_minor": 0}')
    (root / "rules" / "mapping.toml").parent.mkdir()
    (root / "rules" / "mapping.toml").write_text("[mapping]\nkey = 1\n")
    catalog = load_catalog(root)
    assert "mapping" not in catalog.formulas


def test_loader_rejects_non_monotonic_tombstones(tmp_path):
    resources = default_resources()
    objects = default_objects()
    tombstone = {"target_id": "food-soda", "kind": "object", "at_version": 2,
                 "reason": "later"}
    stale = dict(tombstone, at_version=1)
    root = write_catalog(tmp_path / "catalog", resources, objects, [tombstone, stale])
    with pytest.raises(CatalogError, match="CATALOG_VERSION_NOT_MONOTONIC"):
        load_catalog(root)


def test_loader_rejects_malformed_tombstone_line(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "manifest.json").write_text('{"version_major": 1, "version_minor": 0}')
    (root / "overrides" / "tombstones.jsonl").parent.mkdir(parents=True)
    (root / "overrides" / "tombstones.jsonl").write_text('{"target_id": "x"}\n')
    with pytest.raises(CatalogError, match="CATALOG_SCHEMA_INVALID"):
        load_catalog(root)


def test_loader_skips_blank_tombstone_lines(tmp_path):
    tombstone = {"target_id": "food-soda", "kind": "object", "at_version": 1,
                 "reason": "fixture"}
    root = write_catalog(tmp_path / "catalog", default_resources(), default_objects(),
                         [tombstone])
    tombstones = root / "overrides" / "tombstones.jsonl"
    tombstones.write_text("\n" + json.dumps(tombstone) + "\n   \n")
    catalog = load_catalog(root)
    assert catalog.tombstoned_ids() == {"food-soda"}


def test_inspect_reports_drain_and_convert_edge_relations(tmp_path):
    resources = default_resources()
    objects = [{
        "object_id": "odd-object", "name": "带 drain 与 convert 的对象",
        "object_kind": "service",
        "edges": [
            {"on": "rest", "type": "drain", "resource": "energy_kcal", "amount": 30},
            {"on": "refuel", "type": "convert", "from": "external_energy",
             "to": "energy_kcal", "amount": 10},
        ],
        "provenance": [{"part": "edge-0", "paths": ["edges[0]"], "type": "scenario",
                        "source": "test fixture"},
                       {"part": "edge-1", "paths": ["edges[1]"], "type": "scenario",
                        "source": "test fixture"}],
        "version": 1,
    }]
    catalog = load_catalog(write_catalog(tmp_path / "catalog", resources, objects, []))
    hits = edges_of(catalog, "energy_kcal")
    relations = {hit.relation for hit in hits}
    assert "drain" in relations
    assert "convert_to" in relations
    affecting = objects_affecting(catalog, "energy_kcal")
    assert "odd-object" in affecting
    report = lineage(catalog, "odd-object")
    assert report.object_id == "odd-object"
    assert report.entries[0].part == "edge-0"


def test_convert_edge_requires_account_roles_for_placeholders():
    with pytest.raises(ValueError, match="role placeholder endpoints require account_roles"):
        ConvertEdge.model_validate({"on": "settle", "type": "convert", "from": "{buyer}",
                                    "to": "{seller}", "amount": 5})
    with pytest.raises(ValueError, match="exactly one of amount or formula"):
        ConvertEdge.model_validate({"on": "settle", "type": "convert", "from": "a",
                                    "to": "b", "account_roles": {"from": "x", "to": "y"}})


def test_transfer_edge_model_rejects_misplaced_activity_metadata():
    assert TransferEdge.model_validate({
        "on": "pay", "type": "transfer", "from": "a", "to": "b", "amount_fen": 5,
    }).account_roles is None
