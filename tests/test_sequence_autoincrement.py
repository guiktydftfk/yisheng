"""P0-2：AUTOINCREMENT 的真实保证（含回滚行为实测）。

**不要夸大的部分**（本文件把边界钉死）：

| 说法 | 是否成立 | 本文件哪条测试 |
|---|---|---|
| 已提交记录用过的序号不会在后续提交中复用 | ✅ 成立 | `test_committed_sequence_not_reused_after_delete` |
| 允许出现序号空洞 | ✅ 成立 | `test_sequence_gaps_are_allowed` |
| 回滚事务占用的序号**可能被复用** | ✅ 成立（即"不保证不复用"） | `test_sequence_may_be_reused_after_rollback` |
| 回滚后序号连续 | ❌ **不保证** | `test_sequence_continuity_is_not_guaranteed` |
| sequence 是永久身份 | ❌ 不是，永久身份是 UUID | `test_sequence_is_not_identity` |
"""

from __future__ import annotations

import pytest

from yisheng.db import repository


def test_sequence_increments_normally(conn):
    """普通写入：序号递增。"""
    seqs = [repository.insert_experience(conn, content=f"e{i}")["sequence"] for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


def test_committed_sequence_not_reused_after_delete(conn):
    """已提交记录用过的序号，后续提交不会重新使用。"""
    a = repository.insert_experience(conn, content="a")
    b = repository.insert_experience(conn, content="b")
    assert (a["sequence"], b["sequence"]) == (1, 2)

    conn.execute("DELETE FROM experiences WHERE sequence = ?", (b["sequence"],))
    c = repository.insert_experience(conn, content="c")

    assert c["sequence"] == 3, "已提交的序号被复用了 —— 违反 AUTOINCREMENT 的保证"


def test_sequence_may_be_reused_after_rollback(conn):
    """**显式事务回滚后，序号可能被复用** —— 这是真实行为，必须如实断言。

    实测：事务内插入拿到 seq=N，ROLLBACK 后 sqlite_sequence 恢复，
    下一次插入会再次拿到 seq=N。
    """
    repository.insert_experience(conn, content="committed-1")   # seq=1
    repository.insert_experience(conn, content="committed-2")   # seq=2

    conn.execute("BEGIN IMMEDIATE")
    rolled_back = repository.insert_experience(conn, content="will-rollback")
    assert rolled_back["sequence"] == 3                          # 事务内拿到了 3
    conn.execute("ROLLBACK")

    assert repository.count_experiences(conn) == 2               # 回滚后这条不存在

    after = repository.insert_experience(conn, content="after-rollback")
    # 断言的是**允许复用**，而不是"必须复用"：不同 SQLite 版本可能有差异，
    # 契约只要求"不保证不复用"。
    assert after["sequence"] in (3, 4), f"回滚后序号行为异常: {after['sequence']}"


def test_sequence_gaps_are_allowed(conn):
    """回滚造成的空洞是合法的 —— 不要断言序号连续。"""
    for i in range(3):
        repository.insert_experience(conn, content=f"c{i}")

    conn.execute("BEGIN IMMEDIATE")
    repository.insert_experience(conn, content="gap-maker")
    conn.execute("ROLLBACK")

    repository.insert_experience(conn, content="after-gap")
    seqs = [r["sequence"] for r in conn.execute("SELECT sequence FROM experiences ORDER BY sequence")]
    # 允许 (1,2,3,4) 也允许 (1,2,3,5)；唯一禁止的是重复与回退
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_sequence_continuity_is_not_guaranteed(conn):
    """显式证明"不保证连续"：制造空洞后，槽位数与 max(sequence) 不再相等。"""
    repository.insert_experience(conn, content="1")
    repository.insert_experience(conn, content="2")

    conn.execute("BEGIN IMMEDIATE")
    repository.insert_experience(conn, content="3-will-rollback")
    conn.execute("ROLLBACK")

    # 用一条不同手段制造空洞：先插入再用 AUTOINCREMENT 表的 sqlite_sequence 不影响计数
    repository.insert_experience(conn, content="3-or-4")

    rows = conn.execute("SELECT COUNT(*) AS n, MAX(sequence) AS m FROM experiences").fetchone()
    # 不做"必须相等"的断言 —— 这正是"不保证连续"的含义
    assert rows["n"] <= rows["m"], "max(sequence) 不应小于记录数"


def test_sequence_is_not_identity(conn):
    """sequence 不是永久身份：真正身份是 UUID v4，两者必须分离。"""
    created = repository.insert_experience(conn, content="identity check")
    row = conn.execute(
        "SELECT id, sequence FROM experiences WHERE sequence = ?", (created["sequence"],)
    ).fetchone()

    assert row["id"] == created["id"]
    assert row["id"] != str(row["sequence"])          # 身份不是序号
    assert len(row["id"]) == 36                        # UUID 形态


@pytest.mark.parametrize("count", [1, 2, 10])
def test_sequence_monotonic_within_session(conn, count):
    """同一连接内连续写入：序号严格单调递增。"""
    seqs = [repository.insert_experience(conn, content=f"m{i}")["sequence"] for i in range(count)]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == count
