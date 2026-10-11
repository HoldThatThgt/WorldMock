"""带前提的 app 内容：钉钉打卡例子的环境副本、内容行与本地时刻。

基础夹具不含钉钉，也不含带前提的内容；这里在夹具副本上构造并用生产身份发布器刷新清单。
例子的日期照规格：2026 年 10 月 12 日是周一，10 月 17 日是周六，运行时区 Asia/Shanghai。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import tomlkit

from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows

#: 例子里办公室的显示名；地点身份与类型仍是 office。
OFFICE = "练秋湖研发中心"
ZONE = ZoneInfo("Asia/Shanghai")
WORKDAYS = ("mon", "tue", "wed", "thu", "fri")
#: 打卡在工作日能开始的两个时段。
CLOCK_HOURS = [["08:00", "10:00"], ["17:00", "20:00"]]
CLOCK_PREREQUISITE = {"location_kinds": ["office"],
                      "opens": {"weekly": {day: CLOCK_HOURS for day in WORKDAYS}}}


def weekday_hours(*intervals):
    """只在工作日的给定时段能开始的时段声明。"""
    return {"weekly": {day: [list(item) for item in intervals] for day in WORKDAYS}}


def app_content(content_id, title, prerequisite=None, **fields):
    """一条绑定到钉钉的 60 秒内容行；prerequisite 为 None 时省略这个字段。"""
    row = {"app_ids": ["dingtalk"], "content_id": content_id, "title": title, "duration_seconds": 60,
           "exertion": "idle", "pushable": False, "relaxation_relief": "none", "social_relief": "none",
           "summary": f"{title}的测试用合成摘要。",
           "summary_source": {"kind": "synthetic", "url": None, "notice": "数据集作者编写"}, **fields}
    if prerequisite is not None:
        row["prerequisite"] = prerequisite
    return row


#: 规格「前提对象」一节的那一行：只在办公室、只在工作日上下班时段能开始的打卡。
CLOCK = app_content("dingtalk-clock", "考勤打卡", CLOCK_PREREQUISITE, summary="打开考勤页，完成一次打卡。")


def add_rows(root, name, key, *rows):
    """往目录文件里加行并按身份升序写回，随后刷新清单。"""
    merged = [*catalog_rows(root, name), *rows]
    write_catalog_rows(root, name, sorted(merged, key=lambda row: row[key]))


def dingtalk_environment(destination, *contents):
    """夹具副本：办公室改名为练秋湖研发中心，加入内容 app 钉钉与给定内容（缺省只有打卡一条），刷新清单。"""
    root = copy_environment(destination)
    places = catalog_rows(root, "location.jsonl")
    next(row for row in places if row["location_id"] == "office")["name"] = OFFICE
    write_catalog_rows(root, "location.jsonl", places)
    persona = tomlkit.parse((root / "persona.toml").read_text(encoding="utf-8"))
    persona["work_schedule"]["workplace"] = OFFICE
    (root / "persona.toml").write_text(tomlkit.dumps(persona), encoding="utf-8", newline="\n")
    add_rows(root, "app-catalog.jsonl", "app_id", {"app_id": "dingtalk", "name": "钉钉", "role": "content"})
    add_rows(root, "content-catalog.jsonl", "content_id", *(contents or (CLOCK,)))
    return root


def moment(day, hour, minute=0, second=0):
    """2026 年 10 月某日的本地时刻对应的世界秒。"""
    return int(datetime(2026, 10, day, hour, minute, second, tzinfo=ZONE).timestamp())


def example_window(days=7, at_office=False, start_day=12, **settings):
    """运行从 10 月某日（缺省 12 日，周一）08:00 开始、持续给定天数的配置改写；at_office 时人物从办公室开始，
    其余键写进配置。测试用 stage_moment 改时刻时只在开始那一天之内，跨日的天气由零点边界登记。"""
    def mutate(persona, config):
        start = datetime(2026, 10, start_day, 8, tzinfo=ZONE)
        config["start_time"] = start
        config["stop_time"] = start + timedelta(days=days)
        config.update(settings)
        if at_office:
            persona["initial_location"] = OFFICE
    return mutate


def silence_other_pushes(root):
    """把钉钉以外的内容都改成不可推送：整点推送只可能落在钉钉上，结果不取决于抽中哪个 app。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    for row in rows:
        if "dingtalk" not in row.get("app_ids", []):
            row["pushable"] = False
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    return root
