"""@brief 品类维护命令、细化建议和新版本发布的可观察行为。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tests.categories.conftest import review
from worldmock.catalog import load_catalog
from worldmock.categories import CategoryError, refresh_manifest_identity
from worldmock.categories.maintenance import (
    category_touches,
    check_catalog,
    publish_catalog,
    refinement_report,
)
from worldmock.cli import main
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore


def _read(path):
    return json.loads(path.read_text())


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def _candidate(source, destination):
    shutil.copytree(source, destination)
    contract = _read(destination / "category-contract.json")
    contract["contract_version"] += 1
    _write(destination / "category-contract.json", contract)
    rules = _read(destination / "exchange-rules.json")
    rules["contract_version"] = contract["contract_version"]
    _write(destination / "exchange-rules.json", rules)
    manifest = _read(destination / "manifest.json")
    manifest["version_minor"] += 1
    _write(destination / "manifest.json", manifest)
    refresh_manifest_identity(destination)
    return destination


def _child(identity, schema):
    return {"category_id": identity, "name": identity, "parent_id": "food", "kind": "product",
            "slots": {"energy_kcal_per_serving": {"schema": schema, "required": True}},
            "values": {"absolute": {}, "relative": {}, "proportional": {}}}


def _add_children(candidate, children):
    contract = _read(candidate / "category-contract.json")
    contract["categories"].extend(children)
    _write(candidate / "category-contract.json", contract)
    refresh_manifest_identity(candidate)


def _run_with_matches(path, rows):
    store = FootprintStore.create(path, {"run_id": "category-test"}, {"version": 0, "current_time": 100})
    for row in rows:
        store.commit(store.state, [EventDraft(event_type=row[0], occurred_at=100,
                                             data={"category_matches": row[1]})])
    store.close()
    return path


def test_check_reports_missing_pairing_without_rejecting_static_catalog(valid_catalog_dir):
    before = _snapshot(valid_catalog_dir)
    query = {"actor_category_id": "person", "target_category_id": "product", "action": "consume:use"}
    result = check_catalog(valid_catalog_dir, [query])
    assert result["queries"][0]["state"] == "undefined"
    assert result["explicit_pairing_counts"] == {"defined": 2}
    assert result["object_count"] == 1
    assert result["touches"] == []
    assert _snapshot(valid_catalog_dir) == before


@pytest.mark.parametrize("queries", [{}, [{"action": "purchase"}], [{"actor_category_id": "person",
    "target_category_id": "food", "action": "purchase", "invented": True}]])
def test_check_rejects_malformed_queries(valid_catalog_dir, queries):
    with pytest.raises(ValueError, match="CATEGORY_QUERY_INVALID"):
        check_catalog(valid_catalog_dir, queries)


def test_refinement_uses_current_attributes_and_reports_unique_ambiguous_and_unmatched(valid_catalog_dir, tmp_path):
    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    _add_children(candidate, [_child("light-food", {"type": "number", "maximum": 100}),
                              _child("dense-food", {"type": "number", "minimum": 101})])
    before = _snapshot(valid_catalog_dir)
    report = refinement_report(valid_catalog_dir, candidate, "food")
    assert report["objects"] == [{"object_id": "milk", "current_category": "food",
                                  "candidates": ["dense-food"], "status": "unique",
                                  "suggested_category": "dense-food"}]
    _add_children(candidate, [_child("overlap-food", {"type": "number", "minimum": 180})])
    ambiguous = refinement_report(valid_catalog_dir, candidate, "food")["objects"][0]
    assert ambiguous["status"] == "ambiguous" and ambiguous["suggested_category"] is None
    assert ambiguous["candidates"] == ["dense-food", "overlap-food"]
    contract = _read(candidate / "category-contract.json")
    contract["categories"] = [row for row in contract["categories"]
                              if row["category_id"] not in {"dense-food", "overlap-food"}]
    _write(candidate / "category-contract.json", contract)
    refresh_manifest_identity(candidate)
    assert refinement_report(valid_catalog_dir, candidate, "food")["objects"][0]["status"] == "unmatched"
    assert _snapshot(valid_catalog_dir) == before


def test_refinement_reports_rule_and_pairing_changes_and_raw_touch_counts(valid_catalog_dir, tmp_path):
    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    _add_children(candidate, [_child("food-child", {"type": "number", "minimum": 0})])
    contract = _read(candidate / "category-contract.json")
    contract["pairings"][0]["coefficient"] = 2
    contract["pairings"].append({"actor_category_id": "person", "target_category_id": "product",
                                 "action": "consume:use", "state": "no_effect", "review": review()})
    _write(candidate / "category-contract.json", contract)
    rules = _read(candidate / "exchange-rules.json")
    rules["rules"][0]["from_role"] = "customer"
    _write(candidate / "exchange-rules.json", rules)
    refresh_manifest_identity(candidate)
    match = {"category_id": "food", "rule_id": "buy-product", "action": "purchase"}
    run = _run_with_matches(tmp_path / "run", [("action_result", [match]), ("observation", [match]),
                                               ("action_result", [match]), ("action_result", [])])
    report = refinement_report(valid_catalog_dir, candidate, "food", run)
    assert report["touches"] == [{**match, "count": 2}]
    assert report["refinement_signals"] == [{"category_id": "food", "rule_id": "buy-product", "count": 2}]
    assert {row["change"] for row in report["pairing_changes"]} == {"changed", "added"}
    assert report["rule_changes"][0]["after"]["from_role"] == "customer"
    assert check_catalog(candidate, run_dir=run)["touches"] == report["touches"]


def test_empty_refinement_and_multiple_observed_rules_do_not_invent_suggestions(valid_catalog_dir, tmp_path):
    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    with pytest.raises(ValueError, match="CATEGORY_REFINEMENT_EMPTY"):
        refinement_report(valid_catalog_dir, candidate, "food")
    _add_children(candidate, [_child("child", {"type": "number", "minimum": 0})])
    matches = [{"category_id": "food", "rule_id": rule, "action": action}
               for rule, action in [("buy-product", "purchase"), ("eat-food", "consume:eat")]]
    matches.append({"category_id": "product", "rule_id": "buy-product", "action": "purchase"})
    run = _run_with_matches(tmp_path / "run", [("action_result", matches)])
    assert len(category_touches(run)) == 3
    assert refinement_report(valid_catalog_dir, candidate, "food", run)["refinement_signals"] == []


def test_publish_runs_actual_scenario_admission_and_preserves_source(tmp_path):
    source = Path("catalog")
    candidate = _candidate(source, tmp_path / "candidate")
    scenario = _read(Path("scenarios/multi-person-half-day.json"))
    scenario["corpus_root"] = str(Path("corpora").resolve())
    manifest = _read(candidate / "manifest.json")
    scenario["catalog_version"] = {"major": manifest["version_major"], "minor": manifest["version_minor"]}
    scenario_path = tmp_path / "scenario.json"
    _write(scenario_path, scenario)
    before = _snapshot(source)
    result = publish_catalog(source, candidate, tmp_path / "published", {}, (scenario_path,))
    published = load_catalog(tmp_path / "published")
    assert published.category_contract_version == 2
    assert len(published.objects) == 163
    assert published.category_requirements == load_catalog(source).category_requirements
    assert result["identity"]["catalog_content_sha256"] == published.catalog_content_sha256
    assert _snapshot(source) == before
    assert not list(tmp_path.glob(".category-publish-*"))


def test_publication_failure_has_no_destination_or_source_changes(valid_catalog_dir, tmp_path, monkeypatch):
    from worldmock.categories import maintenance

    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    before = _snapshot(valid_catalog_dir)
    def reject_scenario(*args):
        raise ValueError("actual scenario rejects undefined pairing")
    monkeypatch.setattr(maintenance, "_admit_scenarios", reject_scenario)
    with pytest.raises(ValueError, match="undefined pairing"):
        publish_catalog(valid_catalog_dir, candidate, tmp_path / "published", {}, (Path("scenario.json"),))
    assert not (tmp_path / "published").exists()
    assert _snapshot(valid_catalog_dir) == before
    assert not list(tmp_path.glob(".category-publish-*"))


def test_destination_created_during_validation_is_not_overwritten(valid_catalog_dir, tmp_path, monkeypatch):
    from worldmock.categories import maintenance

    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    destination = tmp_path / "published"
    before = _snapshot(valid_catalog_dir)

    def competing_publication(*args):
        destination.mkdir()
        (destination / "owner.txt").write_text("another publisher")

    monkeypatch.setattr(maintenance, "_admit_scenarios", competing_publication)
    with pytest.raises(ValueError, match="CATEGORY_DESTINATION_EXISTS"):
        publish_catalog(valid_catalog_dir, candidate, destination, {}, (Path("scene"),))
    assert {path.name for path in destination.iterdir()} == {"owner.txt"}
    assert (destination / "owner.txt").read_text() == "another publisher"
    assert _snapshot(valid_catalog_dir) == before
    assert not list(tmp_path.glob(".category-publish-*"))


def test_publish_requires_explicit_assignment_and_next_object_version(valid_catalog_dir, tmp_path, monkeypatch):
    from worldmock.categories import maintenance

    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    _add_children(candidate, [_child("child", {"type": "number", "minimum": 0})])
    monkeypatch.setattr(maintenance, "_admit_scenarios", lambda *args: None)
    publish_catalog(valid_catalog_dir, candidate, tmp_path / "published", {"milk": "child"}, (Path("scene"),))
    obj = load_catalog(tmp_path / "published").raw_objects["milk"]
    assert obj.category_id == "child" and obj.version == 2
    rows = [json.loads(row) for row in (candidate / "objects.jsonl").read_text().splitlines()]
    rows[0].update(category_id="child", version=2)
    (candidate / "objects.jsonl").write_text(json.dumps(rows[0]) + "\n")
    refresh_manifest_identity(candidate)
    with pytest.raises(ValueError, match="CATEGORY_ASSIGNMENT_UNCONFIRMED"):
        publish_catalog(valid_catalog_dir, candidate, tmp_path / "rejected", {}, (Path("scene"),))
    publish_catalog(valid_catalog_dir, candidate, tmp_path / "confirmed", {"milk": "child"}, (Path("scene"),))
    rows[0]["version"] = 1
    (candidate / "objects.jsonl").write_text(json.dumps(rows[0]) + "\n")
    with pytest.raises(ValueError, match="CATEGORY_OBJECT_VERSION_INVALID"):
        publish_catalog(valid_catalog_dir, candidate, tmp_path / "bad-version", {"milk": "child"}, (Path("scene"),))


@pytest.mark.parametrize("case, error", [
    ("destination", "CATEGORY_DESTINATION_EXISTS"),
    ("nested", "CATEGORY_DESTINATION_NESTED"),
    ("missing", "CATEGORY_DIRECTORY_MISSING"),
    ("symlink", "CATEGORY_SYMLINK_FORBIDDEN"),
    ("assignments", "CATEGORY_ASSIGNMENTS_INVALID"),
    ("unknown", "CATEGORY_ASSIGNMENT_UNKNOWN"),
    ("scenarios", "CATEGORY_SCENARIOS_REQUIRED"),
    ("version", "CATEGORY_VERSION_NOT_INCREMENTED"),
    ("catalog_version", "CATEGORY_VERSION_NOT_INCREMENTED"),
    ("requirements", "CATEGORY_REQUIREMENTS_CHANGED"),
    ("review", "review"),
])
def test_publication_rejects_invalid_requests(valid_catalog_dir, tmp_path, case, error):
    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    destination, assignments, scenarios = tmp_path / "published", {}, (Path("scene"),)
    if case == "destination":
        destination.mkdir()
    elif case == "nested":
        destination = valid_catalog_dir / "nested"
    elif case == "missing":
        candidate = tmp_path / "missing"
    elif case == "symlink":
        (candidate / "link").symlink_to(valid_catalog_dir / "objects.jsonl")
    elif case == "assignments":
        assignments = {"milk": 1}
    elif case == "unknown":
        assignments = {"missing": "food"}
    elif case == "scenarios":
        scenarios = ()
    elif case == "version":
        document = _read(candidate / "category-contract.json")
        document["contract_version"] = 1
        _write(candidate / "category-contract.json", document)
    elif case == "catalog_version":
        document = _read(candidate / "manifest.json")
        document["version_minor"] -= 1
        _write(candidate / "manifest.json", document)
    elif case == "requirements":
        document = _read(candidate / "manifest.json")
        document["category_requirements"] = {"declared_at": "2099-01-01"}
        _write(candidate / "manifest.json", document)
    elif case == "review":
        document = _read(candidate / "category-contract.json")
        document.pop("review")
        _write(candidate / "category-contract.json", document)
    before = _snapshot(valid_catalog_dir)
    with pytest.raises((ValueError, CategoryError), match=error):
        publish_catalog(valid_catalog_dir, candidate, destination, assignments, scenarios)
    assert _snapshot(valid_catalog_dir) == before
    assert not list(tmp_path.glob(".category-publish-*"))


def test_category_cli_executes_check_refinement_and_publication(valid_catalog_dir, tmp_path, capsys, monkeypatch):
    from worldmock.categories import maintenance

    queries = tmp_path / "queries.json"
    _write(queries, [{"actor_category_id": "person", "target_category_id": "food", "action": "purchase"}])
    assert main(["category", "check", "--catalog", str(valid_catalog_dir), "--queries", str(queries)]) == 0
    assert json.loads(capsys.readouterr().out)["queries"][0]["state"] == "defined"
    candidate = _candidate(valid_catalog_dir, tmp_path / "candidate")
    _add_children(candidate, [_child("child", {"type": "number", "minimum": 0})])
    assert main(["category", "refine", "--catalog", str(valid_catalog_dir), "--candidate", str(candidate),
                 "--parent-category", "food"]) == 0
    assert json.loads(capsys.readouterr().out)["objects"][0]["suggested_category"] == "child"
    assignments = tmp_path / "assignments.json"
    _write(assignments, {"milk": "child"})
    monkeypatch.setattr(maintenance, "_admit_scenarios", lambda *args: None)
    assert main(["category", "publish", "--catalog", str(valid_catalog_dir), "--candidate", str(candidate),
                 "--assignments", str(assignments), "--scenario", "scene", "--output", str(tmp_path / "output")]) == 0
    assert Path(json.loads(capsys.readouterr().out)["published"]).is_dir()
    assert main(["category", "check", "--catalog", str(tmp_path / "missing")]) == 1
