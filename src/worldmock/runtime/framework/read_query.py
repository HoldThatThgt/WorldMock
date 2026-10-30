"""@brief 在原生 AS2 生命周期中承载生活环境的只读通道（docs/design/10-runtime.md「原生模型与工具」）。

这是锁定 AS2 提交上的 Life 扩展：不 monkey patch、不改 AS2、不做多版本兼容。两个子类各只新增一个只读方法 read_query，
在冻结观察上回答人物的只读查询。运行装配（worldmock.runtime._build_society）以它们建立环境 actor 与代理；装配 AS2 原类时
这个调用到不了环境。
对 AS2 三个私有成员的依赖是随锁定提交验证的耦合点，升级 fork 时逐项复核：
1. DirectEnvActor._router：环境 actor 构造时建立的直接路由（LifeEnvActor.read_query 经它取环境）；
2. DirectRouter._environment()：取得唯一实现完整直接调用合同的环境（LifeEnvActor.read_query 调用）；
3. EnvRouterProxy._actor：代理持有的 Ray actor 句柄（LifeEnvProxy.read_query 经它发起远端调用）。
"""

from __future__ import annotations

from typing import Any

from agentsociety2.env.direct_env_actor import DirectEnvActor
from agentsociety2.env.env_router_proxy import EnvRouterProxy


class LifeEnvActor(DirectEnvActor):
    """@brief 为生活环境提供跨进程只读服务；相对 AS2 的 DirectEnvActor 只新增 read_query。"""

    async def read_query(self, context: Any, tool: str, request: Any) -> dict:
        """@brief 将回看任务与检索记忆交给当前唯一生活环境，不经过世界动作结算。

        耦合点：DirectEnvActor._router 与 DirectRouter._environment()（AS2 私有成员，随锁定提交验证）。
        工具与请求类型不符、找不到唯一观察由环境以协议违约抛出；两者的业务拒绝是返回值里的错误结构。
        """
        return await self._router._environment().read_query(context, tool, request)


class LifeEnvProxy(EnvRouterProxy):
    """@brief 将人物的只读调用送到拥有世界状态的生活环境进程；相对 AS2 的 EnvRouterProxy 只新增 read_query。"""

    async def read_query(self, context: Any, tool: str, request: Any) -> dict:
        """@brief 等待远端回看任务与检索记忆的真实结果并返回结果对象，不把对象引用交给调用方。

        耦合点：EnvRouterProxy._actor（AS2 私有成员，随锁定提交验证）。
        """
        return await self._actor.read_query.remote(context, tool, request)


def life_env_actor_class() -> Any:
    """@brief 构造保持单写入约束的生活环境 Ray actor 类：并发度固定为 1，不提供参数。"""
    import ray

    return ray.remote(max_concurrency=1)(LifeEnvActor)
