"""@brief 按职责保存现有定义与纯计算。"""

from collections.abc import Iterable
from worldmock.foundation.randomness import random_stream



#: 人物可以写的睡眠分钟数下限与上限。


#: 「过去 24 小时」的窗口长度：从这一觉开始的时刻往回数的秒数。
SLEEP_WINDOW_SECONDS = 86400

#: 窗口内的睡眠合计不足这么多秒才可能睡过头；恰好这么多按时醒。
OVERSLEEP_THRESHOLD_SECONDS = 14400

#: 睡过头时多睡的分钟数范围，两端都能取到。
OVERSLEEP_MINUTES_MIN = 20

OVERSLEEP_MINUTES_MAX = 60

#: 分钟数写得不对时的英文原因。




def assumed_sleep(run_start: int, start_hour: float, midsleep_hour: float, sleep_need_hours: float) -> tuple[int, int]:
    """@brief 运行开始前的假定睡眠区间（开始时刻, 结束时刻）：人物在惯常起床时刻睡醒，此前睡满每日睡眠需要。

    与身体初值同一假设：惯常起床时刻是睡眠中点加每日睡眠需要的一半；只假定这一段，更早的时间按没睡算。

    :param run_start: 运行开始时刻（UTC epoch 秒）。
    :param start_hour: 运行开始的本地钟点（自零点起的小时数）。
    :param midsleep_hour: 人物的睡眠中点。
    :param sleep_need_hours: 人物的每日睡眠需要。
    """
    wake_hour = (midsleep_hour + sleep_need_hours / 2) % 24
    awake_seconds = round(((start_hour - wake_hour) % 24) * 3600)
    ended_at = run_start - awake_seconds
    return ended_at - round(sleep_need_hours * 3600), ended_at



def sleep_seconds_in_window(now: int, sleeps: Iterable[tuple[int, int]]) -> int:
    """@brief 这一觉开始的时刻往回 24 小时里睡了多少秒：刚开始的这一觉不算，此前的睡眠只计落在窗口内的那一截。

    会不会睡过头按开始睡的那一刻判定，与这一觉写了多久无关，所以这里不接收写的时长。

    :param now: 受理时刻，也就是这一觉的开始时刻；窗口在这一刻结束。
    :param sleeps: 此前各段睡眠的（开始时刻, 结束时刻）：本人已完成的睡眠与运行开始前的假定睡眠。
    """
    window_start = now - SLEEP_WINDOW_SECONDS
    return sum(max(0, ended_at - max(started_at, window_start)) for started_at, ended_at in sleeps)



def oversleep_seconds(window_seconds: int, random_state: str, action_id: str) -> int:
    """@brief 多睡的秒数：窗口内的合计不足 4 小时才取随机流，先抽一位，抽中再抽 20 到 60 的整数分钟。

    每段睡眠只调用一次；随机键只含运行的随机态和开始这段睡眠的动作身份，重算得到同一结果。
    写的时长不参与判定，写满 12 小时的一觉也可能多睡，一段睡眠最长 780 分钟。

    :param window_seconds: sleep_seconds_in_window 的结果。
    :param random_state: 运行的随机态。
    :param action_id: 开始这段睡眠的那条动作的身份。
    """
    if window_seconds >= OVERSLEEP_THRESHOLD_SECONDS:
        return 0
    stream = random_stream(random_state, f"oversleep:{action_id}")
    if not stream.getrandbits(1):
        return 0
    return stream.randint(OVERSLEEP_MINUTES_MIN, OVERSLEEP_MINUTES_MAX) * 60
