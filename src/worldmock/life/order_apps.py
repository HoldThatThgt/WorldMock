"""@brief 下单 app 与意向 app：能接单的内容 app，以及从人物所写 prefer 认出的那一个（docs/design/04-simulation-engine.md）。

购买、工具层与查询都从这里取下单 app 的判据，不各写一遍；只依赖冻结目录与名称规整，不依赖本次观察。
"""

from __future__ import annotations

from worldmock.life import object_labels


def selling_app_ids(catalog, goods_ids) -> tuple[str, ...]:
    """@brief 卖这组商品里至少一件的下单 app，按 app_id 升序；「哪个 app 卖哪件商品」的唯一判据。

    一个 app 卖一件商品按冻结目录的登记算，不看此刻营业：商品的 app_ids 里有这个 app；或者有一个地点，
    它的类型在商品的 store_kinds 里，并且它登记了这个 app。

    :param catalog: 这次运行冻结的环境目录。
    :param goods_ids: 一组商品身份。
    :return: 卖其中至少一件的 app 身份，按 app_id 升序。
    """
    wanted = frozenset(goods_ids)
    return tuple(key for key in sorted(catalog.apps) if wanted.intersection(catalog.goods_by_app[key])
                 or any(wanted.intersection(catalog.goods_by_store[store]) for store in catalog.stores_by_app[key]))


def order_app_ids(catalog) -> tuple[str, ...]:
    """@brief 全部下单 app 的身份，按 app_id 升序。

    下单 app 是能接单的内容 app：它直接登记了商品，或者登记它的地点里至少有一个确实卖商品，即卖目录里至少一件商品。
    功能 app 与只有内容、不卖东西的内容 app 都不是。

    :param catalog: 这次运行冻结的环境目录。
    :return: 下单 app 的身份，按 app_id 升序。
    """
    return selling_app_ids(catalog, catalog.goods)


def preferred_app_id(catalog, written: str | None) -> str | None:
    """@brief 从人物所写的 prefer 认出意向 app 的身份；写的是 null 或认不出时为 None。

    只在下单 app 的名称里用与 detail 相同的规整（object_labels.resolve_name）判断，规整后唯一对上才算。
    名称不存在、写的是功能 app 或不卖东西的内容 app、规整后对上多个，都当作没有意向；这是正常结果，不写日志。

    :param catalog: 这次运行冻结的环境目录。
    :param written: 人物在 prefer 里写的原文，可以为 None。
    :return: 意向 app 的身份，或 None。
    """
    if written is None:
        return None
    names = {catalog.apps[key].name: key for key in order_app_ids(catalog)}
    return names.get(object_labels.resolve_name(written, list(names)).name)
