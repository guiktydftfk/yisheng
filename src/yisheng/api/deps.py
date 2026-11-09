"""FastAPI 依赖注入。"""

from __future__ import annotations

import sqlite3
from typing import Iterator

from ..db.connection import connect


def get_conn() -> Iterator[sqlite3.Connection]:
    """请求级连接：每个请求一个连接，请求结束即关闭。

    SQLite 连接不是线程安全的，FastAPI 的同步端点跑在线程池里，
    因此按请求创建连接是最稳妥的做法；WAL 保证并发读不互相阻塞。
    """
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


__all__ = ["get_conn"]
