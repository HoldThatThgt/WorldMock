"""@brief 两个开始工具的共享请求工厂。"""

from worldmock.life.models import StartMainActivityRequest, StartSideActivityRequest


def main_request(action, target, detail=None):
    """@brief start_main_activity：出行写目的地与出行方式，在地点做事写当前地点与单元名，睡眠写分钟数。"""
    return StartMainActivityRequest.model_validate({"action": action, "target": target, "detail": detail})


def side_request(action, *targets, prefer=None):
    """@brief start_side_activity：购买逐份写商品名，使用内容 app 只写一个 app 名，查询写商品名或类别名；prefer 缺省为 null。"""
    return StartSideActivityRequest.model_validate({"action": action, "target": list(targets), "prefer": prefer})
