"""记录一条 SQLite 连接执行过的语句，供语句级断言与查询计划检查使用。"""

import sqlite3


class StatementLog:
    """@brief 连接代理：execute 记下（语句，参数）再交给真实连接执行；其余属性一律转给真实连接。"""

    def __init__(self, connection: sqlite3.Connection):
        """@brief 包住真实连接，记录从空开始。"""
        self.connection = connection
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql: str, parameters=()):
        """@brief 记下语句与参数，返回真实连接的游标。"""
        self.statements.append((sql, tuple(parameters)))
        return self.connection.execute(sql, parameters)

    def __getattr__(self, name):
        """@brief 未代理的属性（commit、上下文管理等）交给真实连接。"""
        return getattr(self.connection, name)

    def __enter__(self):
        """@brief 事务上下文交给真实连接。"""
        return self.connection.__enter__()

    def __exit__(self, *exc):
        """@brief 事务上下文交给真实连接。"""
        return self.connection.__exit__(*exc)

    def plan(self, sql: str, parameters: tuple) -> str:
        """@brief 记下的那条语句在真实连接上的 EXPLAIN QUERY PLAN，各行细节以换行相连。"""
        return "\n".join(row[-1] for row in self.connection.execute(f"EXPLAIN QUERY PLAN {sql}", parameters))
