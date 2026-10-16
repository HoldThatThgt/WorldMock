"""行为评估逐行读 events.jsonl、只留事后读取视图：适配结果与读完整事件时相同，拒绝与以前相同。"""

import pytest

from worldmock.evaluation import adapters
from worldmock.evaluation.adapters import adapt_worldmock
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.io import iter_jsonl, read_jsonl
from worldmock.evaluation.scenario import load_scenario


def test_world_dataset_view_is_equal_with_and_without_the_post_run_view(evaluation_fixture, monkeypatch):
    scenario = load_scenario(evaluation_fixture.scenario_path)
    viewed = adapt_worldmock(evaluation_fixture.worldmock_root, scenario)
    monkeypatch.setattr(adapters, "post_run_view", lambda event: event)
    full = adapt_worldmock(evaluation_fixture.worldmock_root, scenario)
    assert viewed.events and viewed.public_evidence
    assert viewed == full


def test_world_adapter_keeps_only_post_run_views(evaluation_fixture, monkeypatch):
    scenario = load_scenario(evaluation_fixture.scenario_path)
    captured, original = [], adapters.worldmock_public_evidence

    def capture(events, *args):
        captured.extend(events)
        return original(events, *args)

    monkeypatch.setattr(adapters, "worldmock_public_evidence", capture)
    adapt_worldmock(evaluation_fixture.worldmock_root, scenario)
    observations = [event for event in captured if event.event_type == "observation"]
    assert observations
    assert not any("activity_bindings" in event.data for event in observations)


@pytest.mark.parametrize("content", [b'{"a": 1}\n\n', b'{"a": 1}\nnot json\n', b'{"a": 1}\n[1, 2]\n', None],
                         ids=["blank-row", "invalid-json", "non-object-row", "missing-file"])
def test_iter_jsonl_rejects_like_read_jsonl(tmp_path, content):
    path = tmp_path / "rows.jsonl"
    if content is not None:
        path.write_bytes(content)
    failures = []
    for read in (read_jsonl, lambda target: list(iter_jsonl(target))):
        with pytest.raises(EvaluationError) as failure:
            read(path)
        failures.append((failure.value.code, str(failure.value)))
    assert failures[0] == failures[1]
    assert failures[0][0] == ("missing_artifact" if content is None else "invalid_jsonl")
