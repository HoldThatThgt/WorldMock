"""食品状态与食材包状态是冻结目录的派生量：被写了 food 的内容凭着的回调状态，以及其中写了重置商品的那些。"""

from tests.life.environment_inputs import GROCERIES, catalog_rows, grocery_environment, write_catalog_rows
from worldmock.resource_engine.catalog.loader import load_catalog


def _location_row(content_id, title, kind, state, food):
    """一条凭 state 的地点内容；food 为真时做完供一餐。"""
    row = {"consumes_state": state, "content_id": content_id, "duration_seconds": 1800, "exertion": "light",
           "location_kinds": [kind], "pushable": False, "relaxation_relief": "none", "social_relief": "none",
           "summary": f"{title}的测试用合成摘要。",
           "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": title}
    if food:
        row["completion_effects"] = {"food": "meal"}
    return row


def _with(root, states, *contents):
    """副本里追加回调状态与内容行，各按身份升序写回并刷新清单，返回加载后的目录。"""
    write_catalog_rows(root, "callbacks.jsonl", sorted([*catalog_rows(root, "callbacks.jsonl"), *states],
                                                       key=lambda row: row["state"]))
    rows = [*catalog_rows(root, "content-catalog.jsonl"), *contents]
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    return load_catalog(root / "catalog", 7)


def test_food_states_are_states_a_food_content_consumes(tmp_path):
    """groceries（做饭凭它、写 food、扑克牌重置）与 team_dinner（凭它的地点内容写 food、没有重置商品）是食品状态，
    detergent（凭它的内容不写 food、纸巾重置）不是；食材包状态只有写了重置商品的 groceries。"""
    root = grocery_environment(tmp_path / "input")
    catalog = _with(root, [{"state": "team_dinner", "initial": 1, "cap": 1, "reset_on_goods": []},
                           {"state": "detergent", "initial": 0, "cap": 1, "reset_on_goods": ["tissue"]}],
                    _location_row("team-dinner", "和同事聚餐", "restaurant", "team_dinner", True),
                    _location_row("laundry", "洗衣服", "home", "detergent", False))
    assert catalog.food_states() == {GROCERIES, "team_dinner"}
    assert catalog.kit_states() == {GROCERIES}


def test_a_series_is_a_food_state_when_any_episode_feeds(tmp_path):
    """三集系列各集凭同一状态、只有第 2 集写 food：这个状态仍是食品状态。"""
    root = grocery_environment(tmp_path / "input")
    episodes = [{**_location_row(f"dinner-{episode}", f"晚饭第{episode}步", "home", "pantry", episode == 2),
                 "series": {"title": "做晚饭", "episode": episode}} for episode in (1, 2, 3)]
    catalog = _with(root, [{"state": "pantry", "initial": 0, "cap": 1, "reset_on_goods": ["tissue"]}], *episodes)
    assert "pantry" in catalog.food_states()
    assert [catalog.contents[f"dinner-{episode}"].completion_effects is not None for episode in (1, 2, 3)] == [
        False, True, False]
