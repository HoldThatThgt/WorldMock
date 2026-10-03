"""@brief 画像编译 v2 测试共用工厂：合法卡片与完整抽取载荷。"""

from __future__ import annotations

import json
import copy

from worldmock.profile.models import (
    HabitSignature,
    Identity,
    PersonaCard,
    Relationship,
    Situation,
    Tastes,
    TimeAnchors,
    TimeWindow,
)


def make_window(start: float, end: float) -> TimeWindow:
    """@brief 窗口工厂。"""
    return TimeWindow(start_hour=start, end_hour=end)


def make_card(**overrides) -> PersonaCard:
    """@brief 合法卡片工厂：字段可整体覆盖。"""
    base = dict(
        identity=Identity(age=32, occupation="产品经理", living_arrangement="与伴侣合租两居室"),
        relationships=(
            Relationship(name_or_appellation="母亲", role="家人", intimacy=25,
                         channels=("电话", "微信")),
            Relationship(name_or_appellation="老周", role="老友", intimacy=18,
                         channels=("微信", "见面")),
        ),
        situation=Situation(busy_this_week="新版上线收尾", cares_about="父母的体检结果"),
        time_anchors=TimeAnchors(
            wake=make_window(7.0, 7.5), leave_home=make_window(8.0, 8.5),
            lunch=make_window(12.0, 13.0), dinner=make_window(18.5, 19.5),
            bedtime=make_window(23.0, 24.0), leave_work=None, arrive_home=None),
        chronotype_midsleep=4.0,
        sleep_need_hours=7.5,
        habits=(
            HabitSignature(trigger="下班到家", response="公园慢跑半小时",
                           time_window=make_window(19.0, 20.0)),
            HabitSignature(trigger="睡前", response="读几页小说",
                           time_window=make_window(22.5, 23.0)),
        ),
        tastes=Tastes(content_genres=("悬疑", "纪录片"),
                      food_preferences=("川菜", "面食"), media=("长视频", "短视频")),
        coping="拼高达模型",
    )
    return PersonaCard.model_validate({**base, **overrides})


def make_payload(**overrides) -> dict:
    """@brief 完整合法抽取载荷工厂（FakeSession 响应内容用）。"""
    base = {
        "big_five": {"openness": 5, "conscientiousness": 4, "extraversion": 6,
                     "agreeableness": 4, "neuroticism": 3},
        "identity": {"age": 32, "occupation": "产品经理", "living_arrangement": "与伴侣合租两居室"},
        "relationships": [
            {"name_or_appellation": "母亲", "role": "家人", "intimacy": 25,
             "channels": ["电话", "微信"]},
            {"name_or_appellation": "老周", "role": "老友", "intimacy": 18,
             "channels": ["微信", "见面"]},
        ],
        "situation": {"busy_this_week": "新版上线收尾", "cares_about": "父母的体检结果"},
        "time_anchors": {"wake": "07:00-07:30", "leave_home": "08:00-08:30",
                         "lunch": "12:00-13:00", "dinner": "18:30-19:30",
                         "bedtime": "23:00-24:00"},
        "work_anchors": {"leave_work": None, "arrive_home": None},
        "chronotype_midsleep": "04:00",
        "sleep_need_hours": 7.5,
        "habits": [
            {"trigger": "下班到家", "response": "公园慢跑半小时", "time_window": "19:00-20:00"},
            {"trigger": "睡前", "response": "读几页小说", "time_window": "22:30-23:00"},
        ],
        "tastes": {"content_genres": ["悬疑", "纪录片"], "food_preferences": ["川菜", "面食"],
                   "media": ["长视频", "短视频"]},
        "coping": "拼高达模型",
    }
    payload = copy.deepcopy({**base, **overrides})
    return json.dumps(payload, ensure_ascii=False)
