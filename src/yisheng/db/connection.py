"""SQLite 连接管理。

每个连接都显式设置 WAL / synchronous / foreign_keys / busy_timeout —— 不依赖任何全局默认值。
本模块同时提供「可写性探测」，它与连接配置同属存储层关注点。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import config

# 可写性探测的状态（用于 /health 的 db_write_status）
STATUS_WRITABLE = "writable"
STATUS_READONLY = "readonly"
STATUS_BUSY = "busy"
STATUS_UNKNOWN = "unknown"
STATUS_ERROR = "error"

WRITE_STATUSES = (STATUS_WRITABLE, STATUS_READONLY, STATUS_BUSY, STATUS_UNKNOWN, STATUS_ERROR)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """打开一个已经配置好 PRAGMA 的连接。

    注意：WAL 是**文件库**的持久属性，内存库会退化为 ``memory``。
    契约测试使用真实文件库，因此 WAL 断言是有效的。

    ``synchronous`` 按 D1 决策设为 FULL：已提交的事务在断电后仍然存在。
    它只影响写操作，读连接设置 FULL 不产生额外成本。
    """
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(path),
        timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,  # 显式控制事务，避免隐式 BEGIN 带来的意外
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA synchronous = {config.SYNCHRONOUS_MODE}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {config.SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def discard_wal_side_files(db_path: Path | str) -> None:
    """删除 ``-wal`` / ``-shm`` 旁路文件（供测试清理使用）。"""
    base = Path(db_path)
    for suffix in ("-wal", "-shm"):
        side = base.with_name(base.name + suffix)
        if side.exists():
            side.unlink()


def readonly_signals(conn: sqlite3.Connection) -> str:
    """**无副作用**地判断「是否只读」。不写入任何数据。

    只做两件事：
      1. 读 ``PRAGMA query_only``：连接被显式设为只读时为 1；
      2. 若连接是以 ``mode=ro`` 打开的，写操作会失败，但这只能通过试写才知道 ——
         所以这里只返回 ``unknown``，由调用方决定是否要做真实探测。

    返回 ``readonly`` 或 ``unknown``。
    """
    try:
        row = conn.execute("PRAGMA query_only").fetchone()
        if row is not None and int(row[0]) == 1:
            return STATUS_READONLY
    except sqlite3.Error:
        return STATUS_ERROR
    return STATUS_UNKNOWN


def probe_write_status(conn: sqlite3.Connection) -> str:
    """**真实探测**连接能否写入，返回五态之一。

    为什么不只看读操作：``PRAGMA user_version``（读）在只读库上也会成功；
    ``BEGIN IMMEDIATE`` 在 WAL 只读连接上也不报错（均经实测）。
    因此必须做一次真实写入。

    做法：事务内把 ``user_version`` 改成 ``当前值 + 1``，随后 ``ROLLBACK``。
      * 只读库 / 只读文件系统 → 抛 ``readonly``;
      * 拿不到写锁（别人正在写）→ 抛 ``locked/busy``，返回 ``busy``
        —— **忙不等于可写，也不等于只读，因此绝不折叠成 writable/readonly**;
      * 其他错误 → ``error``。

    安全性：变更在事务内，``ROLLBACK`` 后完全恢复；进程崩溃时 SQLite 自动回滚；
    不写 ``experiences``、不消耗 ``sequence``、不改 schema、不产生任何行。
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        return _classify(exc)
    except sqlite3.Error:
        return STATUS_ERROR

    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        current = int(row[0]) if row is not None else 0
        conn.execute(f"PRAGMA user_version = {current + 1}")  # 唯一一次真实写入
    except sqlite3.OperationalError as exc:
        _safe_rollback(conn)
        return _classify(exc)
    except sqlite3.Error:
        _safe_rollback(conn)
        return STATUS_ERROR

    return STATUS_WRITABLE if _safe_rollback(conn) else STATUS_ERROR


def _classify(exc: sqlite3.OperationalError) -> str:
    """把写探测的失败原因分成：只读 / 忙 / 其他错误。

    **关键：busy 与 readonly 语义不同，绝不合并。**
    忙只说明此刻拿不到锁，不能证明可写，也不能证明不可写。
    """
    message = str(exc).lower()
    if "readonly" in message or "read-only" in message:
        return STATUS_READONLY
    if "locked" in message or "busy" in message:
        return STATUS_BUSY
    return STATUS_ERROR


def _safe_rollback(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("ROLLBACK")
        return True
    except sqlite3.Error:  # pragma: no cover - 回滚失败说明连接已坏
        return False


def writable_flag(status: str) -> bool | None:
    """把五态映射为三态布尔：``True`` / ``False`` / ``None``(无法判断)。

    ``busy`` 与 ``unknown`` 都映射为 ``None`` —— 不伪装成 True，也不伪装成 False。
    """
    if status == STATUS_WRITABLE:
        return True
    if status in (STATUS_READONLY, STATUS_ERROR):
        return False
    return None  # busy / unknown


__all__ = [
    "connect",
    "discard_wal_side_files",
    "probe_write_status",
    "readonly_signals",
    "sqlite3",
    "writable_flag",
    "STATUS_BUSY",
    "STATUS_ERROR",
    "STATUS_READONLY",
    "STATUS_UNKNOWN",
    "STATUS_WRITABLE",
    "WRITE_STATUSES",
]
