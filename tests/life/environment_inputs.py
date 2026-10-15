"""用于实际准入反例和定向业务场景的冻结环境副本。"""

import json
import shutil

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.catalog.identity import refresh_manifest_identity


def copy_environment(destination):
    """复制输入原文，所有后续变化须发布新身份。"""
    shutil.copytree(ENVIRONMENT_ROOT, destination)
    return destination


def write_catalog_rows(root, name, rows):
    """写合法 LF JSONL 并通过生产身份发布器重算完整目录身份。"""
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    (root / "catalog" / name).write_text(text, encoding="utf-8", newline="\n")
    refresh_manifest_identity(root / "catalog")


def catalog_rows(root, name):
    """读取实际逐行对象用于只修改所需字段。"""
    return [json.loads(line) for line in (root / "catalog" / name).read_text(encoding="utf-8").splitlines()]


def set_content(root, content_id, **fields):
    """在副本里改一条内容的几个字段并刷新清单。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    next(row for row in rows if row["content_id"] == content_id).update(fields)
    write_catalog_rows(root, "content-catalog.jsonl", rows)


def walking_only(root):
    """去掉数据集定义的出行方式：删除 travel-method.jsonl，导航 app 只绑定引擎自带的步行，并按生产入口刷新清单。"""
    (root / "catalog" / "travel-method.jsonl").unlink()
    rows = catalog_rows(root, "app-catalog.jsonl")
    for row in rows:
        if "travel_method_ids" in row:
            row["travel_method_ids"] = ["walking"]
    write_catalog_rows(root, "app-catalog.jsonl", rows)
    return root


def travel_method_row(travel_method_id, name, speed, start_price_fen=0, price_per_km_fen=0, exertion="light"):
    """一行出行方式：六个字段都写出，缺省免费、轻体力。"""
    return {"travel_method_id": travel_method_id, "name": name, "speed_meters_per_second": speed,
            "start_price_fen": start_price_fen, "price_per_km_fen": price_per_km_fen, "exertion": exertion}


def replace_travel_methods(root, methods, navigation):
    """换一套数据集出行方式：按身份升序写 travel-method.jsonl，导航 app 改为绑定 navigation 列出的出行方式身份。"""
    write_catalog_rows(root, "travel-method.jsonl", sorted(methods, key=lambda row: row["travel_method_id"]))
    rows = catalog_rows(root, "app-catalog.jsonl")
    next(row for row in rows if row["app_id"] == "amap")["travel_method_ids"] = sorted(navigation)
    write_catalog_rows(root, "app-catalog.jsonl", rows)
    return root


#: 「做晚饭」三集：内容身份、集号、标题、时长（秒）、体力档位；只有《吃晚饭》供一餐。
DINNER_EPISODES = (("dinner-1-cook", 1, "备菜做饭", 1800, "light"), ("dinner-2-eat", 2, "吃晚饭", 1500, "idle"),
                   ("dinner-3-wash", 3, "洗碗收拾厨房", 900, "light"))


def meal_environment(destination):
    """样例环境的副本：「测试休息」（c-rest）做完供一餐，家多一个三集系列「做晚饭」，只有《吃晚饭》供一餐。

    各集照地点系列的写法：绑定到家，不推送，两项缓解都是 none，合成摘要；全部行按内容身份升序写回并刷新清单。
    个别用例要的别的变体在这份副本上再改一行。返回副本的根目录。
    """
    root = copy_environment(destination)
    rows = catalog_rows(root, "content-catalog.jsonl")
    next(row for row in rows if row["content_id"] == "c-rest")["completion_effects"] = {"food": "meal"}
    for content_id, episode, title, seconds, exertion in DINNER_EPISODES:
        row = {"content_id": content_id, "duration_seconds": seconds, "exertion": exertion,
               "location_kinds": ["home"], "pushable": False, "relaxation_relief": "none",
               "series": {"episode": episode, "title": "做晚饭"}, "social_relief": "none",
               "summary": f"{title}的测试用合成摘要。",
               "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": title}
        if content_id == "dinner-2-eat":
            row["completion_effects"] = {"food": "meal"}
        rows.append(row)
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    return root


def bilibili_series_rows(prefix, title, relief):
    """一部两集的哔哩哔哩系列的两行：第 N 集身份 <prefix>-N、标题「<title>第一集」「<title>第二集」，各 600 秒、轻体力、
    可推送，放松缓解取 relief，社交缓解 none，合成摘要，不写前提。"""
    return [{"app_ids": ["bilibili"], "content_id": f"{prefix}-{episode}", "duration_seconds": 600, "exertion": "light",
             "pushable": True, "relaxation_relief": relief, "series": {"episode": episode, "title": title},
             "social_relief": "none", "summary": f"{name}的测试用合成摘要。",
             "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": name}
            for episode, name in ((1, f"{title}第一集"), (2, f"{title}第二集"))]


def _with_contents(destination, *rows):
    """样例环境的副本追加内容行：全部行按内容身份升序写回并刷新清单，返回副本的根目录。"""
    root = copy_environment(destination)
    merged = [*catalog_rows(root, "content-catalog.jsonl"), *rows]
    write_catalog_rows(root, "content-catalog.jsonl", sorted(merged, key=lambda row: row["content_id"]))
    return root


def second_series_environment(destination):
    """样例环境加一部两集的哔哩哔哩系列「测试番剧」（c-anime-1、c-anime-2，各 600 秒，放松缓解 high）。

    哔哩哔哩由此有两部系列：「测试剧集」（三集各 1800 秒，缓解 medium）和「测试番剧」，接续顺序的「另一部」有得接，
    接上时缓解档位看得出换了一条。要第三部的用例在这份副本上再追加 bilibili_series_rows("c-doc", "测试纪录片", "low")。
    """
    return _with_contents(destination, *bilibili_series_rows("c-anime", "测试番剧", "high"))


def office_stretch_environment(destination):
    """样例环境加一条 Keep 内容《办公室拉伸》（c-office-stretch，300 秒，只在公司合格，可推送，放松缓解 medium）。

    Keep 由此有一条不可推送的《测试慢跑》（在家能打开、放 1200 秒）和这一条：《测试慢跑》放完那一秒人在哪，决定接不接
    《办公室拉伸》，候选至多一条，结果不靠随机数。
    """
    return _with_contents(destination, {
        "app_ids": ["keep"], "content_id": "c-office-stretch", "duration_seconds": 300, "exertion": "light",
        "prerequisite": {"location_kinds": ["office"]}, "pushable": True, "relaxation_relief": "medium",
        "social_relief": "none", "summary": "办公室拉伸的测试用合成摘要。",
        "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "办公室拉伸"})


#: 五集副本里「测试剧集」各集的中文集号。
_EPISODE_NUMBERS = "一二三四五"


def five_episode_environment(destination):
    """「五集副本」：样例环境删去只绑定哔哩哔哩的内容行（c-article、c-course、c-image、原有的三集），c-long 只留抖音；
    再写五集「测试剧集」c-drama-1…c-drama-5（各 400 秒、轻体力、可推送，放松缓解 medium，社交缓解 none，合成摘要）。

    这份副本里自动接续只走「下一集」一种，第五集放完时没有别的系列，使用停下待收尾。全部行按内容身份升序写回并刷新
    清单，返回副本的根目录。
    """
    root = copy_environment(destination)
    rows = [row for row in catalog_rows(root, "content-catalog.jsonl") if row.get("app_ids") != ["bilibili"]]
    next(row for row in rows if row["content_id"] == "c-long")["app_ids"] = ["douyin"]
    for episode, number in enumerate(_EPISODE_NUMBERS, start=1):
        title = f"测试剧集第{number}集"
        rows.append({"app_ids": ["bilibili"], "content_id": f"c-drama-{episode}", "duration_seconds": 400,
                     "exertion": "light", "pushable": True, "relaxation_relief": "medium",
                     "series": {"episode": episode, "title": "测试剧集"}, "social_relief": "none",
                     "summary": f"{title}的测试用合成摘要。",
                     "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": title})
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    return root


#: 回调状态「家里的食材」的名称。
GROCERIES = "groceries"


def grocery_environment(destination, initial=0, cap=1, goods=("playing-cards",)):
    """样例环境的副本：登记回调状态 groceries（初值 initial、上限 cap，收到 goods 里的商品时置为上限），家多一件凭它的
    「做饭」（cook，一小时，轻体力，放松缓解 low，做完供一餐），照手册「买菜做饭」的例子行。

    goods 缺省是扑克牌：类别是 goods，三杯咖啡有卖，在家下单走外卖。个别用例要的别的变体在这份副本上再改几行。
    返回副本的根目录。
    """
    root = copy_environment(destination)
    write_catalog_rows(root, "callbacks.jsonl", [
        {"state": GROCERIES, "initial": initial, "cap": cap, "reset_on_goods": sorted(goods)}])
    rows = catalog_rows(root, "content-catalog.jsonl")
    rows.append({"completion_effects": {"food": "meal"}, "consumes_state": GROCERIES, "content_id": "cook",
                 "duration_seconds": 3600, "exertion": "light", "location_kinds": ["home"], "pushable": False,
                 "relaxation_relief": "low", "social_relief": "none", "summary": "用买来的食材做一顿饭，吃完收拾。",
                 "summary_source": {"kind": "synthetic", "notice": "数据集作者编写", "url": None}, "title": "做饭"})
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    return root
