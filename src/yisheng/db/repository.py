"""仓储层：**本包里唯一允许出现 SQL 的模块**。

设计约束（都来自 Phase 1 契约）：
  * 只有 insert / 只读查询 —— 不提供任何 UPDATE 或 DELETE 方法。
    结构上就不给「修改原始经历」留接口。
  * 列表查询一律 ``ORDER BY sequence DESC``，用键集游标翻页。
  * 所有值一律参数化绑定，绝不字符串拼接。
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from .. import config

# 只读投影：列表与详情返回同一组字段（第 86 轮决定：列表返回完整 content，不做摘要）。
_PROJECTION = (
    "sequence, id, recorded_at, occurred_at, content, source, context, outcome, metadata, content_sha256"
)


def sha256_of(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime(config.UTC_FORMAT)


def new_experience_id() -> str:
    """契约 1：永久身份，UUID v4。"""
    return str(uuid.uuid4())


def insert_experience(
    conn: sqlite3.Connection,
    *,
    content: str,
    source: str | None = None,
    context: str | None = None,
    outcome: str | None = None,
    occurred_at: str | None = None,
    metadata: str | None = None,
) -> dict[str, Any]:
    """写入一条原始经历。

    ``recorded_at`` 由服务端在此处生成（契约 4），调用方无法提供。
    """
    experience_id = new_experience_id()
    recorded_at = utc_now_str()
    digest = sha256_of(content)

    cur = conn.execute(
        """
        INSERT INTO experiences
            (id, recorded_at, occurred_at, content, source, context, outcome, metadata, content_sha256)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (experience_id, recorded_at, occurred_at, content, source, context, outcome, metadata, digest),
    )
    return {
        "sequence": int(cur.lastrowid),
        "id": experience_id,
        "recorded_at": recorded_at,
        "occurred_at": occurred_at,
    }


def get_experience(conn: sqlite3.Connection, experience_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {_PROJECTION} FROM experiences WHERE id = ?",
        (experience_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_experiences(
    conn: sqlite3.Connection,
    *,
    limit: int,
    before_sequence: int | None = None,
) -> list[dict[str, Any]]:
    """按 ``sequence DESC`` 取一页，多取 1 条用于判断 ``has_more``。

    ``before_sequence`` 即键集游标：只取序号严格小于它的记录。
    这就是为什么中途插入新记录不会导致翻页重复或遗漏 —— 新记录的 sequence 更大，
    永远落在已经翻过的区间里，不会挤进后续页。
    """
    if before_sequence is None:
        rows: Iterable[sqlite3.Row] = conn.execute(
            f"SELECT {_PROJECTION} FROM experiences ORDER BY sequence DESC LIMIT ?",
            (limit + 1,),
        )
    else:
        rows = conn.execute(
            f"SELECT {_PROJECTION} FROM experiences WHERE sequence < ? ORDER BY sequence DESC LIMIT ?",
            (before_sequence, limit + 1),
        )
    return [dict(r) for r in rows]


def count_experiences(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM experiences").fetchone()
    return int(row["n"])


def list_user_tables(conn: sqlite3.Connection) -> list[str]:
    """返回 ``main`` 库中的用户表名（排除 SQLite 内部表）。

    注意：必须用 ``sqlite_master`` —— ``PRAGMA table_list`` 会额外列出
    ``sqlite_schema`` 等内部表，不适合做「表集合恰好等于」的断言。
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def list_non_table_objects(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """返回 ``main`` 库中的触发器 / 视图（Phase 1 必须为空）。

    用于契约测试：除了两张表，不允许存在任何隐藏的数据库对象。
    """
    rows = conn.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('trigger', 'view') ORDER BY name"
    ).fetchall()
    return [(r["type"], r["name"]) for r in rows]


# ---------------------------------------------------------------------------
# Schema 版本（结构版本本身也走仓储层，保证「SQL 只出现在本文件」这条规则字面成立）
# ---------------------------------------------------------------------------

def schema_version_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    return row is not None


def read_schema_version(conn: sqlite3.Connection) -> int:
    """返回库中已应用的结构版本；无版本表或无记录时返回 0。"""
    if not schema_version_table_exists(conn):
        return 0
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def record_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (int(version), utc_now_str()),
    )


__all__ = [
    "count_experiences",
    "get_experience",
    "insert_experience",
    "list_experiences",
    "list_non_table_objects",
    "list_user_tables",
    "new_experience_id",
    "read_schema_version",
    "record_schema_version",
    "schema_version_table_exists",
    "sha256_of",
    "utc_now_str",
]
