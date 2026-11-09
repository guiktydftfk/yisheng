"""P0-4：``/health`` 的可写性判断。

设计要求：
  1. 普通健康检查**不得频繁执行真实写入**（避免与正常写入争写锁）
  2. ``SQLITE_BUSY`` **不得**被报告为可写
  3. 区分 writable / readonly / busy / unknown / error 五态
  4. 无法确认时返回 ``null``，不伪装成 true，也不伪装成 false
"""

from __future__ import annotations

import sqlite3
import threading

from yisheng.db.connection import (
    STATUS_BUSY,
    STATUS_READONLY,
    STATUS_UNKNOWN,
    STATUS_WRITABLE,
    probe_write_status,
    readonly_signals,
    writable_flag,
)
from yisheng.db.migrate import init_db
from yisheng.services import experience_service


# ---------------------------------------------------------------- 三态映射

def test_writable_flag_mapping():
    """五态 → 三态布尔的映射必须把 busy/unknown 留成 None。"""
    assert writable_flag(STATUS_WRITABLE) is True
    assert writable_flag(STATUS_READONLY) is False
    assert writable_flag(STATUS_BUSY) is None       # 不伪装
    assert writable_flag(STATUS_UNKNOWN) is None    # 不伪装
    assert writable_flag("error") is False


# ---------------------------------------------------------------- 默认不写

def test_health_does_not_write_by_default(client, conn):
    """默认 /health 不得写入：不消耗 sequence、不改 user_version。"""
    conn.execute("PRAGMA user_version = 0")

    for _ in range(5):
        body = client.get("/health").json()
        assert body["db_write_status"] == "unknown"
        assert body["db_writable"] is None

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    # 之后写入仍从 1 开始，说明健康检查没占用序号
    from yisheng.db import repository

    assert repository.insert_experience(conn, content="health 之后第一条")["sequence"] == 1


def test_health_repeated_calls_have_no_side_effects(client, conn):
    """重复调用 /health 不产生任何数据。"""
    for _ in range(10):
        assert client.get("/health").status_code == 200
    from yisheng.db import repository

    assert repository.count_experiences(conn) == 0


# ---------------------------------------------------------------- 显式探测

def test_health_probe_write_reports_writable(client):
    body = client.get("/health?probe=write").json()
    assert body["db_write_status"] == "writable"
    assert body["db_writable"] is True


def test_health_probe_ignores_other_values(client):
    """probe 只认 'write'，其他值按默认（不探测）处理。"""
    for value in ("1", "true", "yes", ""):
        body = client.get(f"/health?probe={value}").json()
        assert body["db_write_status"] == "unknown", f"probe={value!r} 不该触发写入"


def test_health_readonly_reports_readonly(db_path):
    """只读连接：必须报 readonly / False。"""
    init_db(db_path)
    ro = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        result = experience_service.health(ro, probe_write=True)
        assert result["db_write_status"] == "readonly"
        assert result["db_writable"] is False
    finally:
        ro.close()


def test_readonly_signals_detects_query_only(conn):
    """``PRAGMA query_only=1`` 无需写入即可识别为只读。"""
    conn.execute("PRAGMA query_only = 1")
    try:
        assert readonly_signals(conn) == STATUS_READONLY
        # 默认 health 也应据此报只读（且不写入）
        result = experience_service.health(conn, probe_write=False)
        assert result["db_write_status"] == "readonly"
        assert result["db_writable"] is False
    finally:
        conn.execute("PRAGMA query_only = 0")


# ---------------------------------------------------------------- BUSY 语义

def test_busy_is_not_reported_as_writable(db_path):
    """另一连接持有写锁时，探测必须返回 busy，且 db_writable 为 None。

    这是本次修订的核心：旧实现把 busy 直接当成可写。
    """
    init_db(db_path)

    holder = sqlite3.connect(str(db_path), isolation_level=None)
    prober = sqlite3.connect(str(db_path), isolation_level=None, timeout=0.05)
    prober.row_factory = sqlite3.Row
    try:
        holder.execute("BEGIN IMMEDIATE")          # 占住写锁
        holder.execute("INSERT INTO schema_version (version, applied_at) VALUES (999, 'x')")

        result = experience_service.health(prober, probe_write=True)
        assert result["db_write_status"] == "busy", f"期望 busy，实际 {result['db_write_status']}"
        assert result["db_writable"] is None, "busy 不得被映射成 true 或 false"
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        prober.close()


# ---------------------------------------------------------------- 并发

def test_health_under_concurrent_writes(client):
    """持续写入的同时反复调用 /health：不得抛异常，不得返回 500。"""
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for i in range(30):
                r = client.post("/observe", json={"content": f"并发经历 {i}"})
                assert r.status_code == 201
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def prober() -> None:
        try:
            while not stop.is_set():
                r = client.get("/health")
                assert r.status_code == 200, f"health 返回 {r.status_code}"
                assert r.json()["db_write_status"] in {
                    "writable", "readonly", "busy", "unknown", "error",
                }
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    w = threading.Thread(target=writer)
    p = threading.Thread(target=prober)
    p.start()
    w.start()
    w.join(timeout=120)
    stop.set()
    p.join(timeout=30)

    assert not errors, f"出错: {errors!r}"


def test_probe_does_not_leave_user_version_changed(conn):
    """真实写入探测必须回滚，user_version 不能留在被改过的值。"""
    conn.execute("PRAGMA user_version = 0")
    assert probe_write_status(conn) == STATUS_WRITABLE
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0


def test_health_response_has_five_state_field(client):
    body = client.get("/health").json()
    assert body["db_write_status"] in {
        "writable", "readonly", "busy", "unknown", "error",
    }
    assert body["db_write_status"] != STATUS_UNKNOWN or body["db_writable"] is None
