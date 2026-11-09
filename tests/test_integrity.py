"""P0-3：``content_sha256`` 必须真的能发现内容被修改。

能力边界（本文件同时把它钉死）：

    ✅ SHA-256 能检测「content 与 content_sha256 不一致」
    ✅ 能检测只改 content、不改摘要的情况
    ❌ **不能**阻止数据库被修改
    ❌ **不能**发现 content 与 content_sha256 被同时改写的情况

因此本文件既包含"能发现"的测试，也包含一条**如实记录"发现不了"**的测试。
不写后者就等于在暗示系统有完整防篡改能力 —— 那是过度声称。
"""

from __future__ import annotations

import hashlib


def _tamper_content(conn, experience_id: str, new_content: str) -> None:
    """直接改数据库里的 content，**保留旧哈希**（模拟外部程序只改内容）。"""
    conn.execute("UPDATE experiences SET content = ? WHERE id = ?", (new_content, experience_id))
    conn.commit() if not conn.in_transaction else None


def test_hash_covers_content_only(client, conn):
    """哈希只针对 content：改 metadata/source/outcome 不应触发完整性告警。"""
    created = client.post(
        "/observe",
        json={"content": "正文不改动", "source": "s1", "metadata": "m1", "outcome": "o1"},
    ).json()
    eid = created["id"]

    before = client.get(f"/experiences/{eid}").json()
    conn.execute("UPDATE experiences SET source = 's2', metadata = 'm2', outcome = 'o2' WHERE id = ?", (eid,))

    after = client.get(f"/experiences/{eid}")
    assert after.status_code == 200, "改 metadata/source 不该被判为完整性破坏"
    assert after.json()["content_sha256"] == before["content_sha256"]


def test_detects_content_tampered_directly_in_db(client, conn):
    """只改数据库里的 content、保留旧哈希 → 读取必须报 500 data_integrity_error。"""
    created = client.post("/observe", json={"content": "原始内容 ABC"}).json()
    eid = created["id"]
    _tamper_content(conn, eid, "被外部程序篡改后的内容")

    r = client.get(f"/experiences/{eid}")
    assert r.status_code == 500, f"篡改未被发现！返回 {r.status_code}"
    body = r.json()
    assert body["error"] == "data_integrity_error"
    assert body["detail"]["id"] == eid
    assert "被外部程序篡改后的内容" not in str(body), "错误响应不应回显被篡改的内容"


def test_list_detects_tamper(client, conn):
    """列表也必须校验，否则篡改可以通过翻页被静默带出。"""
    keep = client.post("/observe", json={"content": "正常的记录"}).json()
    bad = client.post("/observe", json={"content": "将被篡改的记录"}).json()
    _tamper_content(conn, bad["id"], "篡改版")

    r = client.get("/experiences")
    assert r.status_code == 500
    assert r.json()["error"] == "data_integrity_error"
    # 正常记录的详情仍然可读
    assert client.get(f"/experiences/{keep['id']}").status_code == 200


def test_hash_matches_sha256_of_content(client):
    """哈希算法与字段范围必须与文档一致：sha256(content.encode('utf-8'))。"""
    content = "中文内容 emoji😀 \n 换行"
    created = client.post("/observe", json={"content": content}).json()
    detail = client.get(f"/experiences/{created['id']}").json()

    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert detail["content_sha256"] == expected


def test_simultaneous_tamper_is_NOT_detectable(client, conn):
    """**如实记录能力边界**：同时改 content 与 content_sha256，普通 SHA-256 发现不了。

    这条测试是刻意写的。它断言"检测不到"，用来防止以后有人把
    "检测内容与摘要不一致" 说成 "防止篡改"。
    """
    created = client.post("/observe", json={"content": "原始内容"}).json()
    eid = created["id"]

    forged = "伪造内容"
    forged_hash = hashlib.sha256(forged.encode("utf-8")).hexdigest()
    conn.execute(
        "UPDATE experiences SET content = ?, content_sha256 = ? WHERE id = ?",
        (forged, forged_hash, eid),
    )

    r = client.get(f"/experiences/{eid}")
    assert r.status_code == 200, "若这里报错，说明实现比文档声称的更强（需更新文档）"
    assert r.json()["content"] == forged
    # 结论：本系统**无法**证明数据未被恶意篡改。文档不得声称具备完整防篡改能力。


def test_integrity_error_does_not_leak_content(client, conn):
    """错误响应只给定位信息，不回显内容。"""
    created = client.post("/observe", json={"content": "秘密内容不要泄漏"}).json()
    _tamper_content(conn, created["id"], "篡改后的秘密内容")

    body = client.get(f"/experiences/{created['id']}").json()
    serialized = str(body)
    assert "秘密内容不要泄漏" not in serialized
    assert "篡改后的秘密内容" not in serialized
    assert "sequence" in body["detail"]
