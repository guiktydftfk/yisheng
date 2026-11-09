"""服务层：业务逻辑、游标编解码、完整性校验。

契约在这里被兑现：分页语义、游标不透明性与边界、时间规则、数据完整性检测。

分层约定（E5 决策 R1）：
    仓储层只做数据访问；**完整性校验放在本层**，便于统一抛 ``DataIntegrityError``。
"""

from __future__ import annotations

import base64
import binascii
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .. import config
from ..db import repository
from ..db.connection import (
    STATUS_UNKNOWN,
    probe_write_status,
    readonly_signals,
    writable_flag,
)
from ..errors import DataIntegrityError, InvalidCursorError, InvalidLimitError, InvalidOccurredAtError

logger = logging.getLogger("yisheng")


# ---------------------------------------------------------------------------
# 游标：base64("s:<sequence>")，对客户端是不透明字符串
#
# 注意：Base64 只用于隐藏内部格式，**不是加密**；游标是不透明 API 参数，
# 但**不是安全凭证**。客户端可以构造合法游标从任意位置开始读取，这是允许的。
# ---------------------------------------------------------------------------

def encode_cursor(sequence: int) -> str:
    raw = f"{config.CURSOR_PREFIX}{int(sequence)}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> int:
    """解码游标；非法或越界一律抛 :class:`InvalidCursorError`（→ 400）。

    必须在这里拦住 ``> SQLITE_MAX_INT`` 的值：
    超出 SQLite INTEGER 范围的 Python int 在绑定参数时会抛 ``OverflowError``，
    若不拦截，真实服务上会变成 HTTP 500 而不是 400。
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError("cursor 无法解码") from exc

    if not raw.startswith(config.CURSOR_PREFIX):
        raise InvalidCursorError("cursor 前缀不合法")

    tail = raw[len(config.CURSOR_PREFIX):]
    if not tail.isdigit():
        raise InvalidCursorError("cursor 内容不合法")

    value = int(tail)
    if value < 1:
        raise InvalidCursorError("cursor 序号越界")
    if value > config.SQLITE_MAX_INT:
        raise InvalidCursorError(
            f"cursor 序号超出 SQLite INTEGER 范围（上限 {config.SQLITE_MAX_INT}）"
        )
    return value


# ---------------------------------------------------------------------------
# 完整性
# ---------------------------------------------------------------------------

def verify_row_integrity(row: dict[str, Any]) -> None:
    """校验一行的 ``content`` 与 ``content_sha256`` 是否一致。

    能力边界：只能发现「内容与摘要不一致」。若两者被同时改写，本检测无法识别。

    抛 :class:`DataIntegrityError`（→ 500 ``data_integrity_error``）。
    不回显被篡改的内容，只给出定位信息。
    """
    content = row.get("content")
    stored = row.get("content_sha256")
    if content is None or stored is None:  # pragma: no cover - 列有 NOT NULL 约束
        raise DataIntegrityError(int(row.get("sequence", -1)), str(row.get("id", "?")), "missing fields")

    actual = repository.sha256_of(content)
    if actual != stored:
        raise DataIntegrityError(
            int(row["sequence"]),
            str(row["id"]),
            "content 与 content_sha256 不一致",
        )


def _to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """把完整行转成列表用的轻量摘要（不含 content 全文）。"""
    content = row["content"]
    preview = content[: config.CONTENT_PREVIEW_CHARS]
    return {
        "id": row["id"],
        "sequence": int(row["sequence"]),
        "recorded_at": row["recorded_at"],
        "occurred_at": row["occurred_at"],
        "source": row["source"],
        "content_length": len(content),
        "content_preview": preview,
        "content_truncated": len(content) > config.CONTENT_PREVIEW_CHARS,
        "content_sha256": row["content_sha256"],
    }


# ---------------------------------------------------------------------------
# 经历
# ---------------------------------------------------------------------------

def record_experience(
    conn: sqlite3.Connection,
    *,
    content: str,
    source: str | None = None,
    context: str | None = None,
    outcome: str | None = None,
    occurred_at: str | datetime | None = None,
    metadata: str | None = None,
) -> dict[str, Any]:
    """写入一条经历。``recorded_at`` 只由仓储层生成，调用方无法传入。

    契约 4：``occurred_at`` 必须带时区。不带时区是一个**明确的 400**，
    而不是笼统的 422 —— 因此这个判断放在服务层，而不是 pydantic validator。

    ``metadata`` 原样透传：不解析、不校验、不序列化。
    """
    occurred_at_text = _require_timezone(occurred_at)

    result = repository.insert_experience(
        conn,
        content=content,
        source=source,
        context=context,
        outcome=outcome,
        occurred_at=occurred_at_text,
        metadata=metadata,
    )
    logger.info("recorded experience id=%s sequence=%s", result["id"], result["sequence"])
    return result


def _require_timezone(value: str | datetime | None) -> str | None:
    """校验并归一化 ``occurred_at``；不带时区时抛 :class:`InvalidOccurredAtError`。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidOccurredAtError(
            "occurred_at 必须带时区（例如 2026-11-07T10:00:00+08:00 或 ...Z）"
        )
    return value.astimezone(timezone.utc).strftime(config.UTC_FORMAT)


def get_experience(conn: sqlite3.Connection, experience_id: str) -> dict[str, Any] | None:
    """按 ID 取单条完整经历，**并校验数据完整性**。"""
    row = repository.get_experience(conn, experience_id)
    if row is None:
        return None
    verify_row_integrity(row)          # 不一致 → DataIntegrityError → 500
    return row


def list_experiences(
    conn: sqlite3.Connection,
    *,
    limit: int,
    cursor: str | None = None,
) -> dict[str, Any]:
    """键集分页，返回轻量摘要列表。

    列表**同样校验完整性**（E4 决策 V1）：否则篡改内容可以通过翻页被静默带出。
    """
    validate_limit(limit)

    before = decode_cursor(cursor) if cursor else None
    rows = repository.list_experiences(conn, limit=limit, before_sequence=before)

    has_more = len(rows) > limit
    rows = rows[:limit]

    items: list[dict[str, Any]] = []
    for row in rows:
        verify_row_integrity(row)      # 先验，再产出摘要（不把坏数据放进响应）
        items.append(_to_summary(row))

    next_cursor = encode_cursor(items[-1]["sequence"]) if (has_more and items) else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


def validate_limit(limit: int) -> None:
    """``limit`` 的唯一校验点（路由层不再重复校验）。"""
    if not (config.MIN_PAGE_LIMIT <= limit <= config.MAX_PAGE_LIMIT):
        raise InvalidLimitError(
            f"limit 必须在 {config.MIN_PAGE_LIMIT}..{config.MAX_PAGE_LIMIT} 之间"
        )


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

def health(conn: sqlite3.Connection, *, probe_write: bool = False) -> dict[str, Any]:
    """健康检查。**默认不执行任何写入**，因此不会干扰正常经历写入。

    ``probe_write=True`` 时才做一次真实写入探测（``BEGIN IMMEDIATE`` +
    事务内写 ``user_version`` + ``ROLLBACK``）。

    状态语义见 :data:`yisheng.db.connection.WRITE_STATUSES`。
    """
    from ..db.migrate import current_version

    try:
        version = current_version(conn)
    except sqlite3.Error:
        return {
            "status": "degraded",
            "schema_version": -1,
            "db_path": str(config.DB_PATH),
            "db_writable": False,
            "db_write_status": "error",
        }

    if probe_write:
        status = probe_write_status(conn)
    else:
        status = readonly_signals(conn)      # readonly 或 unknown，从不写

    return {
        "status": "ok",
        "schema_version": version,
        "db_path": str(config.DB_PATH),
        "db_writable": writable_flag(status),
        "db_write_status": status or STATUS_UNKNOWN,
    }


__all__ = [
    "decode_cursor",
    "encode_cursor",
    "get_experience",
    "health",
    "list_experiences",
    "record_experience",
    "validate_limit",
    "verify_row_integrity",
]
