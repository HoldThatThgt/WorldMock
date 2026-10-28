"""@brief 测试用已提交世界守卫：LifeEngine 的任何公开方法都不得原地改动它开始时的那份世界。"""

from __future__ import annotations

import functools
import inspect

from worldmock.foundation.serialization import canonical_json


def public_methods(cls) -> tuple[str, ...]:
    """@brief 类自身定义的公开实例方法名：不以下划线开头的普通函数（类方法、静态方法与属性不算）。"""
    return tuple(name for name, value in vars(cls).items() if not name.startswith("_") and inspect.isfunction(value))


class CommittedWorldGuard:
    """@brief 包住 LifeEngine 的每个公开方法，记下改动了调用开始时那份世界的调用；夹具在用例结束时断言没有记录。"""

    def __init__(self) -> None:
        #: 改动了开始时世界的调用，按发生先后，形如 "LifeEngine.advance changed the world it started from"。
        self.leaks: list[str] = []

    def install(self, monkeypatch) -> None:
        """@brief 用 monkeypatch 包住 LifeEngine 类上当前的全部公开方法，用例结束时自动还原。"""
        from worldmock.simulation_engine.engine import LifeEngine

        for name in public_methods(LifeEngine):
            monkeypatch.setattr(LifeEngine, name, self._guarded(getattr(LifeEngine, name)))

    def _guarded(self, method):
        """@brief 调用前后各编码一次调用开始时的世界对象；字节不同即记一条，原异常照常抛出。"""
        @functools.wraps(method)
        def call(engine, *args, **kwargs):
            world = engine.context.state
            before = canonical_json(world.model_dump(mode="json"))
            try:
                return method(engine, *args, **kwargs)
            finally:
                if canonical_json(world.model_dump(mode="json")) != before:
                    self.leaks.append(f"LifeEngine.{method.__name__} changed the world it started from")
        return call
