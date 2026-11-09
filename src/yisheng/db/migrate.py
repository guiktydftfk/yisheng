"""Schema 应用与版本迁移。

Phase 1 只有版本 1。这里保留迁移骨架，是为了让「结构版本」成为显式契约：
任何后续变更都必须新增 migration，而不是就地改 schema.sql。

**本文件不含任何内联 SQL。**
DDL 从 ``schema.sql`` 文件读取并整体执行；版本表的读写全部委托给
:mod:`yisheng.db.repository`。这样「SQL 只出现在 repository.py」是一条
可以被静态检查证明的事实，而不是一句约定。
"""

from __future__ import annotations

import sqlite3

from .. import config
from . import repository


def current_version(conn: sqlite3.Connection) -> int:
    """返回库中已应用的结构版本；空库返回 0。"""
    return repository.read_schema_version(conn)


def apply_migrations(conn: sqlite3.Connection) -> int:
    """把库升级到 ``config.SCHEMA_VERSION``，返回应用后的版本。

    幂等：重复调用不会重复插入版本行。
    """
    version = current_version(conn)
    if version == 0:
        ddl = config.SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        conn.executescript(ddl)
        repository.record_schema_version(conn, config.SCHEMA_VERSION)
        version = config.SCHEMA_VERSION

    if version != config.SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库结构版本 {version} 与代码期望的 {config.SCHEMA_VERSION} 不一致；"
            "Phase 1 不提供降级迁移。"
        )
    return version


def init_db(db_path=None) -> int:
    """建库 + 应用迁移，返回结构版本。"""
    from .connection import connect

    conn = connect(db_path)
    try:
        return apply_migrations(conn)
    finally:
        conn.close()


__all__ = ["apply_migrations", "current_version", "init_db"]
