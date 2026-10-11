"""@brief 睡眠请求所写分钟数的解析：宿主与环境共用的纯函数，不引用世界模型。"""

import logging
import unicodedata

#: 人物可以写的睡眠分钟数下限与上限。
SLEEP_MINUTES_MIN = 10
SLEEP_MINUTES_MAX = 720
#: 分钟数写得不对时的英文原因。
SLEEP_MINUTES_REASON = ("For sleep, target must be a whole number of minutes from 10 to 720 written in digits, "
                        "for example 470.")
_DIGITS = frozenset("0123456789")


def parse_sleep_minutes(target: str) -> int:
    """@brief 把睡眠请求的 target 读成分钟数：全半角归一并去掉空白后整串是十进制数字，且在 10 到 720 之间。

    带单位、小数、正负号的写法都不接受；不换算、不取整，也不把越界的数截到范围内。

    :raises ValueError: 写法或范围不符，文字为 SLEEP_MINUTES_REASON。
    """
    text = "".join(unicodedata.normalize("NFKC", target).split())
    significant = text.lstrip("0")
    if not text or not _DIGITS.issuperset(text) or len(significant) > len(str(SLEEP_MINUTES_MAX)):
        logging.error("Sleep minutes are not written as digits within the allowed range")
        raise ValueError(SLEEP_MINUTES_REASON)
    minutes = int(text)
    if not SLEEP_MINUTES_MIN <= minutes <= SLEEP_MINUTES_MAX:
        logging.error("Sleep minutes are outside the allowed range")
        raise ValueError(SLEEP_MINUTES_REASON)
    return minutes
