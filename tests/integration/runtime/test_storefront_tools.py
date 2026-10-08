"""@brief 实际固定菜单的完整工具目标与七层 Schema 边界，无引擎生命周期。"""

import pytest
from pydantic import ValidationError
from worldmock.life.callable_index import callable_index
from worldmock.life.models import ActivityStartParams
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.tool_layer import tool_layer
from tests.life.purchase_worlds import menu_context


def test_real_goods_targets_validate_against_the_production_tool_layer_schema():
    context = menu_context("noodle-2")
    layer = tool_layer(context, callable_index(context))
    validate_layer(layer)
    assert any(target["name"] == "牛肉面" for target in layer["targets"])
    assert not any(target["kind"] == "catalog" for target in layer["targets"])


def test_retired_browse_activity_request_is_strictly_rejected():
    with pytest.raises(ValidationError):
        ActivityStartParams(activity_type="browse", target_id="products", planned_duration_seconds=60)
