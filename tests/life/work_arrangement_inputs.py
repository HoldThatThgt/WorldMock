"""上班安排用例的人设变更函数（docs/design/06-agent.md#agent-环境层-duty ；不是测试模块，不被收集）。

样例环境的人物星期一到星期五 09:00–18:00、12:00–13:00 休息，没有日期例外，运行从 2026-01-05（星期一）08:00 开始。
每个函数都是 engine_factory(mutate=...) 的 mutate(persona, config)：只在副本的人设上改工作安排（rest_tuesday_from_midnight
另把副本运行配置的开始与截止挪到零点），样例环境本身不动。
"""

from __future__ import annotations

#: 样例环境的标准班次：09:00–18:00，12:00–13:00 休息（自当地零点起的分钟）。
_STANDARD_SHIFT = {"starts": 540, "ends": 1080, "breaks": [[720, 780]]}


def holiday_monday(persona, config):
    """人设：2026-01-05（星期一）按日期例外休息。"""
    persona["work_schedule"]["exceptions"] = {"2026-01-05": []}


def makeup_monday(persona, config):
    """人设：每周声明去掉星期一，2026-01-05（星期一）按日期例外上 09:00–18:00、12:00–13:00 休息的班。"""
    del persona["work_schedule"]["weekly"]["0"]
    persona["work_schedule"]["exceptions"] = {"2026-01-05": dict(_STANDARD_SHIFT)}


def shorter_monday(persona, config):
    """人设：2026-01-05（星期一）按日期例外上 10:00–17:00、不带休息的班（同是上班，只差时段）。"""
    persona["work_schedule"]["exceptions"] = {"2026-01-05": {"starts": 600, "ends": 1020, "breaks": []}}


def rest_tuesday(persona, config):
    """人设：2026-01-06（星期二）按日期例外休息，今天（星期一）照常上班。"""
    persona["work_schedule"]["exceptions"] = {"2026-01-06": []}


def rest_tuesday_from_midnight(persona, config):
    """人设同 rest_tuesday；运行配置：从 2026-01-05（星期一）00:00+08:00 开始、次日零点截止。开始那一刻是 UTC 的
    2026-01-04 16:00，UTC 日期还是星期日，本地日与 UTC 日期不同。"""
    rest_tuesday(persona, config)
    config["start_time"] = config["start_time"].replace(hour=0)
    config["stop_time"] = config["stop_time"].replace(hour=0)


def no_work_schedule(persona, config):
    """人设：去掉整个工作安排。"""
    del persona["work_schedule"]
