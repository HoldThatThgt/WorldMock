"""LifeEnv 在自己所属循环关闭模型客户端，同时释放持久存储连接。"""

import asyncio
import sqlite3

import litellm
import pytest

from tests.integration.runtime.current_contract_fixtures import environment


@pytest.mark.parametrize("fails", [False, True])
async def test_life_env_close_closes_cached_clients_in_its_own_loop(engine_factory, fails):
    """缓存关闭成功或失败均移除条目、关闭引擎；错误保留给调用方。"""
    engine = engine_factory()
    env = environment(engine)
    loop = asyncio.get_running_loop()
    key = f"worldmock-close-test-{id(loop)}"
    closed = []

    class Client:
        async def close(self):
            closed.append(asyncio.get_running_loop())
            if fails:
                raise RuntimeError("client close failed")

    cache = litellm.in_memory_llm_clients_cache
    cache.cache_dict[key] = Client()
    try:
        if fails:
            with pytest.raises(RuntimeError, match="client close failed"):
                await env.close()
        else:
            await env.close()
        assert closed == [loop]
        assert key not in cache.cache_dict
        assert env.engine is None
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            engine.context.store.connection.execute("SELECT 1")
    finally:
        cache.delete_cache(key)
