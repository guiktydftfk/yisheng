"""并发与持久化测试。"""

from __future__ import annotations

import threading

from yisheng.db import repository
from yisheng.db.connection import connect


def test_concurrent_writes_no_duplicate_sequence(conn, db_path):
    """10 线程并发写，sequence 必须无重复、无空洞（在无回滚的前提下）。"""
    errors: list[BaseException] = []
    barrier = threading.Barrier(10)

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=10)
            c = connect(db_path)
            try:
                for i in range(5):
                    repository.insert_experience(c, content=f"t{idx}-{i}")
            finally:
                c.close()
        except BaseException as exc:  # noqa: BLE001 - 收集所有线程异常
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"并发写入出错: {errors!r}"

    rows = conn.execute("SELECT sequence FROM experiences ORDER BY sequence").fetchall()
    seqs = [r["sequence"] for r in rows]
    assert len(seqs) == 50
    assert len(set(seqs)) == 50            # 无重复
    assert seqs == list(range(1, 51))      # 无空洞


def test_restart_persistence_across_connections(db_path):
    from yisheng.db.migrate import init_db

    init_db(db_path)
    c1 = connect(db_path)
    created = [repository.insert_experience(c1, content=f"e{i}") for i in range(5)]
    c1.close()

    c2 = connect(db_path)
    try:
        assert repository.count_experiences(c2) == 5
        for item in created:
            row = repository.get_experience(c2, item["id"])
            assert row is not None
            assert row["sequence"] == item["sequence"]
    finally:
        c2.close()


def test_wal_files_created(workdir):
    """真实文件库上确实生成 WAL 旁路文件。"""
    from yisheng.db.migrate import init_db

    path = workdir / "wal.db"
    init_db(path)
    c = connect(path)
    repository.insert_experience(c, content="wal")
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    c.close()

    assert mode.lower() == "wal"
    assert path.exists()


def test_memory_db_is_not_wal_documented_behaviour():
    """说明性测试：内存库会退化，WAL 断言只在文件库上有意义。

    这条测试把「为什么不用内存库」写进代码，避免以后有人改成内存库后
    WAL 契约测试变成假绿。
    """
    import sqlite3

    c = sqlite3.connect(":memory:")
    mode = c.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    c.close()
    assert mode.lower() == "memory"


def test_health_default_does_not_claim_writable(client):
    """默认 /health 不做写入探测，因此不得声称可写（三态应为 null）。"""
    body = client.get("/health").json()
    assert body["db_writable"] is None
    assert body["db_write_status"] == "unknown"


def test_health_probe_reports_writable(client):
    body = client.get("/health?probe=write").json()
    assert body["db_writable"] is True
    assert body["db_write_status"] == "writable"


def test_health_probe_readonly_db(db_path):
    """只读库上真实探测必须返回 readonly / False，不能误报。"""
    import sqlite3

    from yisheng.db.migrate import init_db
    from yisheng.services import experience_service

    init_db(db_path)

    ro = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        result = experience_service.health(ro, probe_write=True)
        assert result["db_write_status"] == "readonly"
        assert result["db_writable"] is False
    finally:
        ro.close()


def test_health_probe_does_not_consume_sequence_or_data(conn):
    """可写性探测不得写数据、不得消耗 sequence、不得改动 user_version。"""
    from yisheng.db.connection import probe_write_status

    assert probe_write_status(conn) == "writable"
    assert repository.count_experiences(conn) == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0

    first = repository.insert_experience(conn, content="探测之后的第一条")
    assert first["sequence"] == 1
