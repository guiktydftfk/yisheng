"""功能测试：写入与查询的基本形态。"""

from __future__ import annotations

import uuid


def test_observe_basic(client):
    r = client.post("/observe", json={"content": "第一次经历"})
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"id", "sequence", "recorded_at", "occurred_at"}
    assert body["sequence"] == 1
    assert body["occurred_at"] is None
    uuid.UUID(body["id"])  # 必须是合法 UUID
    assert body["recorded_at"].endswith("+00:00")


def test_observe_multiple_sequences_increase(client):
    seqs = []
    for i in range(10):
        r = client.post("/observe", json={"content": f"经历 {i}"})
        assert r.status_code == 201
        seqs.append(r.json()["sequence"])
    assert seqs == list(range(1, 11))
    assert len({*seqs}) == 10


def test_observe_minimal_only_content(client):
    r = client.post("/observe", json={"content": "只有内容"})
    assert r.status_code == 201
    detail = client.get(f"/experiences/{r.json()['id']}").json()
    assert detail["source"] is None
    assert detail["context"] is None
    assert detail["outcome"] is None
    assert detail["metadata"] is None


def test_observe_full_fields(client):
    payload = {
        "content": "完整字段",
        "source": "unit-test",
        "context": "背景说明",
        "outcome": "结果说明",
        "occurred_at": "2026-11-01T12:00:00+08:00",
        "metadata": '{"k": "v", "n": 1}',   # metadata 是原始字符串
    }
    r = client.post("/observe", json=payload)
    assert r.status_code == 201
    detail = client.get(f"/experiences/{r.json()['id']}").json()
    assert detail["source"] == "unit-test"
    assert detail["context"] == "背景说明"
    assert detail["outcome"] == "结果说明"
    # 契约 4：统一转成 UTC
    assert detail["occurred_at"] == "2026-11-01T04:00:00.000000+00:00"
    # 契约：metadata 原样字符串返回，绝不反序列化成对象
    assert detail["metadata"] == '{"k": "v", "n": 1}'


def test_list_default_limit(client):
    for i in range(25):
        client.post("/observe", json={"content": f"e{i}"})
    page = client.get("/experiences").json()
    assert len(page["items"]) == 20          # 默认 limit=20
    assert page["has_more"] is True
    assert page["next_cursor"]


def test_list_respects_limit(client):
    for i in range(5):
        client.post("/observe", json={"content": f"e{i}"})
    page = client.get("/experiences?limit=3").json()
    assert len(page["items"]) == 3
    assert page["has_more"] is True
    page = client.get("/experiences?limit=5").json()
    assert len(page["items"]) == 5
    assert page["has_more"] is False


def test_get_by_id(client):
    created = client.post("/observe", json={"content": "按 ID 取"}).json()
    r = client.get(f"/experiences/{created['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert body["content"] == "按 ID 取"


def test_get_not_found(client):
    r = client.get("/experiences/00000000-0000-4000-8000-000000000000")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_get_bad_uuid(client):
    r = client.get("/experiences/not-a-uuid")
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["schema_version"] == 1
    # 默认不做写入探测 → 无法判断可写性，必须是 null，不能伪装成 true
    assert body["db_writable"] is None
    assert body["db_write_status"] == "unknown"


def test_health_probe_write(client):
    """只有显式探测时才给出确证结论。"""
    r = client.get("/health?probe=write")
    assert r.status_code == 200
    body = r.json()
    assert body["db_writable"] is True
    assert body["db_write_status"] == "writable"


def test_health_has_no_cognitive_fields(client):
    """health 只报告结构性与存储状态，不含任何认知指标。"""
    body = client.get("/health").json()
    assert set(body) == {
        "status", "schema_version", "db_path", "db_writable", "db_write_status",
    }
