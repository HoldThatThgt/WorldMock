"""@brief 手机记录测试共用的助手：读前台区间、通知与停留事件，按固定种子挑请求身份，以及提案例子的环境副本。"""

from datetime import datetime, timedelta
import hashlib
from zoneinfo import ZoneInfo

from tests.life.conftest import action_identity
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import events
from tests.life.prerequisite_inputs import add_rows
from worldmock.exchange.constants import WEATHER_FACTORS
from worldmock.life.order_routing import payment_order_key
from worldmock.life.randomness import random_stream

#: 例子的日期：2026 年 10 月 12 日，周一，运行时区 Asia/Shanghai。
ZONE = ZoneInfo("Asia/Shanghai")
#: 例子里路上用的那条哔哩哔哩内容；整个哔哩哔哩只留它一条，时长由用例给出。
CLIP_ID = "phone-clip"


def phone_events(engine, kind):
    """@brief 已提交的某一种环境事件，按事件先后。"""
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == kind]


def foreground(engine, activity_id=None):
    """@brief 前台区间事件写成（app，步骤，开始，结束）；给了活动身份就只取它的。"""
    return [(row.data["app_id"], row.data["step"], row.data["interval_start"], row.data["interval_end"])
            for row in phone_events(engine, "app_foreground") if activity_id is None or row.activity_id == activity_id]


def notices(engine):
    """@brief 订单通知与支付通知按事件先后写成（时刻，标题，发送的 app，记录身份）。"""
    return [(row.occurred_at, row.data["title"], row.data["app_id"], row.data["order_ids"])
            for row in events(engine, "environment_event")
            if row.data.get("kind") in {"order_notification", "payment_notification"}]


def stays(engine):
    """@brief 停留进出事件按事件先后写成（时刻，种类，地点，进入方式）。"""
    return [(row.occurred_at, row.data["kind"], row.data["location_id"], row.data.get("entry_kind"))
            for row in events(engine, "environment_event")
            if row.data.get("kind") in {"location_entered", "location_left"}]


def label_where(prefix, accepts, limit=400):
    """@brief 按固定种子挑一个请求身份：第一个让给定判定成立的 `<前缀>-<序号>`。"""
    return next(f"{prefix}-{index}" for index in range(limit) if accepts(f"{prefix}-{index}"))


def purchase_draws(engine, action_id, app_id):
    """@brief 一条购买动作对一张订单冻结的三个抽取：（购买时长，付款方式，付款时长）；键见配置「唯一种子与随机键」。

    :param app_id: 这张订单下单的内容 app；到店订单为 None。
    """
    state = engine.context.state.random_state
    key = payment_order_key(app_id)
    candidates = [*sorted(engine.context.catalog.apps_by_action.get("purchase", ())), None]
    return (random_stream(state, f"purchase-duration:{action_id}").randint(60, 300),
            random_stream(state, f"function-app:{action_id}:{key}").choice(candidates),
            random_stream(state, f"payment-duration:{action_id}:{key}").randint(10, 20))


def paying_label(engine, prefix, payments):
    """@brief 挑一个购买请求身份，使它的各张订单抽到给定的付款方式。

    :param payments: 下单的内容 app（到店订单为 None）到想要的支付 app（直付为 None）。
    """
    return label_where(prefix, lambda label: all(
        purchase_draws(engine, action_identity(label), app)[1] == wanted for app, wanted in payments.items()))


def returning_label(engine, trip_label, prefix, wanted):
    """@brief 挑一个用 app 的请求身份，使这项活动在这趟出行的路上停止执行时抽到给定的那一位。"""
    state = engine.context.state.random_state

    def accepts(label):
        key = f"navigation-return:activity-{action_identity(trip_label)}:activity-{action_identity(label)}"
        return (random_stream(state, key).getrandbits(1) == 1) is wanted

    return label_where(prefix, accepts)


def moment(hour, minute=0, second=0, day=12):
    """@brief 2026 年 10 月某日（缺省 12 日）的本地时刻对应的世界秒。"""
    return int(datetime(2026, 10, day, hour, minute, second, tzinfo=ZONE).timestamp())


def clear_day_seed(local_date="2026-10-12"):
    """@brief 找出给定本地日抽到晴天的最小种子：声明的距离不随种子变化，晴天时出行用时就是距离除以速度。"""
    for seed in range(500):
        state = hashlib.sha256(f"random:{seed}".encode()).hexdigest()
        if random_stream(state, f"weather:{local_date}").choice(tuple(WEATHER_FACTORS)) == "sunny":
            return seed
    raise AssertionError(local_date)


def phone_environment(destination, clip_seconds=540):
    """@brief 提案例子用的夹具副本，改完按生产发布入口刷新清单。

    内容 app 瑞幸咖啡取代饿了么、只绑定到三杯咖啡，加入只有咖啡店卖的冰美式（16 元）；声明家到咖啡店 1,500 米、
    家到公司 12,600 米（晴天驾车 30 分钟）；哔哩哔哩只留一条给定时长、不推送的内容。
    """
    root = copy_environment(destination)
    # 饿了么原先只绑定在三杯咖啡上；咖啡店改成只绑瑞幸咖啡之后它没有任何绑定，一并去掉。
    apps = [row for row in catalog_rows(root, "app-catalog.jsonl") if row["app_id"] != "eleme"]
    apps.append({"app_id": "luckin", "name": "瑞幸咖啡", "role": "content"})
    write_catalog_rows(root, "app-catalog.jsonl", sorted(apps, key=lambda row: row["app_id"]))
    places = catalog_rows(root, "location.jsonl")
    next(row for row in places if row["location_id"] == "cafe")["app_ids"] = ["luckin"]
    write_catalog_rows(root, "location.jsonl", places)
    add_rows(root, "merchant-catalog.jsonl", "goods_id", {
        "category": "drink", "goods_id": "iced-americano", "name": "冰美式", "price_fen": 1600,
        "store_kinds": ["cafe"]})
    contents = []
    for row in catalog_rows(root, "content-catalog.jsonl"):
        apps = [app for app in row.get("app_ids", []) if app != "bilibili"]
        if apps or "location_kinds" in row:
            contents.append({**row, **({"app_ids": apps} if "app_ids" in row else {})})
    clip = {"app_ids": ["bilibili"], "content_id": CLIP_ID, "duration_seconds": clip_seconds, "exertion": "light",
            "pushable": False, "relaxation_relief": "medium", "social_relief": "none", "summary": "路上看的一条视频。",
            "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "路上视频"}
    write_catalog_rows(root, "content-catalog.jsonl", sorted([*contents, clip], key=lambda row: row["content_id"]))
    write_catalog_rows(root, "map.jsonl", [{"a": "cafe", "b": "home", "length_m": 1500},
                                           {"a": "home", "b": "office", "length_m": 12600}])
    return root


def phone_window(start, hours, **settings):
    """@brief 运行从 2026 年 10 月 12 日给定的本地整点（时，分）开始、持续给定小时数；当天是晴天，其余键写进配置。"""
    def mutate(persona, config):
        begin = datetime(2026, 10, 12, *start, tzinfo=ZONE)
        config["start_time"] = begin
        config["stop_time"] = begin + timedelta(hours=hours)
        config["seed"] = clear_day_seed()
        config.update(settings)
    return mutate
