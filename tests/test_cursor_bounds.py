"""D4 + 分页边界：游标的整数上界与分页边界条件。

为什么单独一个文件：
    游标越界在旧实现里会抛 ``OverflowError``（Python int 超出 SQLite INTEGER），
    在真实服务上表现为 **HTTP 500** 而不是 400。这是实际缺陷，需要专门钉死。

同时补齐分页的边界条件（恰好一页、limit==总数、limit+1、单页最大负载）。
"""

from __future__ import annotations

import base64

import pytest

from yisheng import config


def forge(sequence_text: str) -> str:
    """构造一个"格式合法但数值可能非法"的游标（模拟客户端伪造）。"""
    return base64.urlsafe_b64encode(f"s:{sequence_text}".encode()).decode()


# ---------------------------------------------------------------- 整数上界

def test_cursor_at_sqlite_max_is_accepted(client):
    """恰好 2^63-1：是合法整数，应被接受。

    注意语义：``WHERE sequence < cursor``，所以用最大整数做游标会返回**全部**记录
    （所有真实 sequence 都比它小），这是正确行为而不是空页。
    """
    for i in range(3):
        client.post("/observe", json={"content": f"e{i}"})

    r = client.get(f"/experiences?cursor={forge(str(config.SQLITE_MAX_INT))}")
    assert r.status_code == 200
    body = r.json()
    assert [x["sequence"] for x in body["items"]] == [3, 2, 1]
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_cursor_over_sqlite_max_returns_400(client):
    """2^63 必须被拦成 400，**不能是 500**（旧实现会抛 OverflowError）。"""
    client.post("/observe", json={"content": "x"})
    for value in (config.SQLITE_MAX_INT + 1, config.SQLITE_MAX_INT + 12345):
        r = client.get(f"/experiences?cursor={forge(str(value))}")
        assert r.status_code == 400, f"{value} 应返回 400，实际 {r.status_code}"
        assert r.json()["error"] == "invalid_cursor"


def test_absurdly_large_cursor_returns_400(client):
    """远超 64 位范围的数字也必须 400。"""
    client.post("/observe", json={"content": "x"})
    r = client.get(f"/experiences?cursor={forge('9' * 30)}")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_cursor"


@pytest.mark.parametrize("bad", ["0", "-1", "-999"])
def test_non_positive_cursor_returns_400(client, bad):
    client.post("/observe", json={"content": "x"})
    r = client.get(f"/experiences?cursor={forge(bad)}")
    assert r.status_code == 400


def test_decode_cursor_rejects_bad_base64(client):
    for bad in ("zzz", "!!!", "不是base64"):
        r = client.get(f"/experiences?cursor={bad}")
        assert r.status_code == 400
        assert r.json()["error"] in {"invalid_cursor", "bad_request"}


# ---------------------------------------------------------------- 分页边界

def test_records_equal_limit_has_more_false(client):
    """数据量 == limit：has_more 必须 false，next_cursor 为 null。"""
    for i in range(5):
        client.post("/observe", json={"content": f"e{i}"})
    body = client.get("/experiences?limit=5").json()
    assert len(body["items"]) == 5
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_records_limit_plus_one_has_more_true(client):
    """数据量 == limit + 1：has_more 必须 true，next_cursor 非空。"""
    for i in range(6):
        client.post("/observe", json={"content": f"e{i}"})
    body = client.get("/experiences?limit=5").json()
    assert len(body["items"]) == 5
    assert body["has_more"] is True
    assert body["next_cursor"]

    # 用游标取下一页：恰好剩下 1 条，且没有更多
    second = client.get(f"/experiences?limit=5&cursor={body['next_cursor']}").json()
    assert len(second["items"]) == 1
    assert second["has_more"] is False
    assert second["next_cursor"] is None


def test_single_full_page_has_more_false(client):
    for i in range(3):
        client.post("/observe", json={"content": f"e{i}"})
    body = client.get("/experiences?limit=3").json()
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_empty_database_pagination(client):
    body = client.get("/experiences").json()
    assert body == {"items": [], "next_cursor": None, "has_more": False}


def test_forged_but_valid_cursor_accepted_by_design(client):
    """伪造的**合法**游标应当被接受 —— 游标不是安全凭证。

    这是刻意的设计：Base64 只隐藏内部格式，不提供访问控制。
    本测试防止以后有人误把它当签名来验。
    """
    for i in range(10):
        client.post("/observe", json={"content": f"e{i}"})

    # 直接构造一个"从第 4 条之后开始"的游标，绕过服务端发放
    body = client.get(f"/experiences?limit=100&cursor={forge('4')}").json()
    assert len(body["items"]) == 3            # sequence 3,2,1
    assert [x["sequence"] for x in body["items"]] == [3, 2, 1]


def test_repeated_cursor_same_result(client):
    for i in range(20):
        client.post("/observe", json={"content": f"e{i}"})
    cursor = client.get("/experiences?limit=5").json()["next_cursor"]
    a = client.get(f"/experiences?limit=5&cursor={cursor}").json()
    b = client.get(f"/experiences?limit=5&cursor={cursor}").json()
    assert a == b


# ---------------------------------------------------------------- 单页负载

def test_max_page_payload_measurement(client):
    """测量最坏单页负载：limit=100 全为 100,000 字符记录。

    列表已改为只返回摘要，因此 payload 应当很小（不随 content 长度线性膨胀）。
    这条测试同时是 E2 决策（列表轻量化）的回归保护。
    """
    import json

    big = "忆" * config.MAX_CONTENT_CHARS
    for _ in range(config.MAX_PAGE_LIMIT):
        r = client.post("/observe", json={"content": big})
        assert r.status_code == 201

    body = client.get(f"/experiences?limit={config.MAX_PAGE_LIMIT}").json()
    assert len(body["items"]) == config.MAX_PAGE_LIMIT

    # 摘要里没有 content 全文
    for item in body["items"]:
        assert "content" not in item
        assert item["content_length"] == config.MAX_CONTENT_CHARS
        assert item["content_truncated"] is True
        assert len(item["content_preview"]) == config.CONTENT_PREVIEW_CHARS

    payload_bytes = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    # 旧实现在这里会是 ~30MB（10,000,000 字符 × 3 字节）。
    # 摘要形态下应当远小于 1MB。
    assert payload_bytes < 1_000_000, f"单页响应过大: {payload_bytes} 字节"
