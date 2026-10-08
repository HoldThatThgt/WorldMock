"""@brief 活动派生时长使用唯一共享规则。"""


from __future__ import annotations


def test_activity_duration_limits_use_the_shared_model_constants() -> None:
    """时长上限从唯一生产归宿导入，替代旧文件内常量的逐字钉。"""
    from worldmock.life.models import DERIVED_DURATION_CAP_SECONDS

    assert DERIVED_DURATION_CAP_SECONDS == 86400
    from worldmock.life import models

    assert not hasattr(models, "CONSUME_DURATION_CAP_SECONDS")
