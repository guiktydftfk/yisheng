"""边界与输入验证测试。"""

from __future__ import annotations

import pytest

from yisheng import config


def test_empty_content_rejected(client):
    r = client.post("/observe", json={"content": ""})
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_whitespace_content_rejected(client):
    r = client.post("/observe", json={"content": "   \n\t  "})
    assert r.status_code == 422


def test_missing_content_rejected(client):
    r = client.post("/observe", json={})
    assert r.status_code == 422


def test_content_exactly_max_accepted(client):
    """契约 4：上限按**字符数**算，恰好 100,000 字符应当被接受。"""
    content = "a" * config.MAX_CONTENT_CHARS
    r = client.post("/observe", json={"content": content})
    assert r.status_code == 201
    detail = client.get(f"/experiences/{r.json()['id']}").json()
    assert len(detail["content"]) == config.MAX_CONTENT_CHARS


def test_content_over_max_rejected(client):
    content = "a" * (config.MAX_CONTENT_CHARS + 1)
    r = client.post("/observe", json={"content": content})
    assert r.status_code == 422


def test_content_cjk_counts_characters(client):
    """中文按字符计：100,000 个汉字（约 300KB UTF-8）应当被接受。"""
    content = "忆" * config.MAX_CONTENT_CHARS
    assert len(content.encode("utf-8")) > config.MAX_CONTENT_CHARS  # 字节数确实超了
    r = client.post("/observe", json={"content": content})
    assert r.status_code == 201


def test_source_too_long_rejected(client):
    r = client.post("/observe", json={"content": "x", "source": "s" * (config.MAX_SOURCE_CHARS + 1)})
    assert r.status_code == 422


def test_occurred_at_requires_timezone(client):
    """契约 4：不带时区明确返回 400（而不是笼统的 422）。"""
    r = client.post("/observe", json={"content": "x", "occurred_at": "2026-11-07T10:00:00"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_occurred_at"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2026-11-07T10:00:00+08:00", "2026-11-07T02:00:00.000000+00:00"),
        ("2026-11-07T10:00:00Z", "2026-11-07T10:00:00.000000+00:00"),
        ("2026-11-07T10:00:00+00:00", "2026-11-07T10:00:00.000000+00:00"),
    ],
)
def test_occurred_at_normalized_to_utc(client, given, expected):
    r = client.post("/observe", json={"content": "x", "occurred_at": given})
    assert r.status_code == 201
    assert r.json()["occurred_at"] == expected


def test_occurred_at_future_accepted(client):
    """未来时间被接受：Phase 1 不判断时间是否合理，只负责原样记录。"""
    r = client.post("/observe", json={"content": "x", "occurred_at": "2099-01-01T00:00:00+00:00"})
    assert r.status_code == 201


def test_recorded_at_cannot_be_client_supplied(client):
    """契约 4：recorded_at 由服务端生成，客户端即使传了也必须被忽略。"""
    forged = "1999-01-01T00:00:00.000000+00:00"
    r = client.post("/observe", json={"content": "伪造时间", "recorded_at": forged})
    # extra="forbid" -> 直接拒绝，比静默忽略更严格
    assert r.status_code == 422

    # 即便通过别的方式混入，落库的 recorded_at 也必须是服务端时间
    created = client.post("/observe", json={"content": "正常"}).json()
    assert created["recorded_at"] != forged
    assert created["recorded_at"].endswith("+00:00")


def test_metadata_accepts_plain_string(client):
    """metadata 是原始字符串：传普通文本必须被接受。"""
    r = client.post("/observe", json={"content": "x", "metadata": "这是一段随手记的原文"})
    assert r.status_code == 201
    detail = client.get(f"/experiences/{r.json()['id']}").json()
    assert detail["metadata"] == "这是一段随手记的原文"


def test_metadata_rejects_object(client):
    """对象不再被接受：契约是原始字符串存储，不提供"自动识别 JSON"。

    若这里也接受对象，就等于服务端在解析与猜测，违反"不解析"的契约本意。
    """
    r = client.post("/observe", json={"content": "x", "metadata": {"k": "v"}})
    assert r.status_code == 422
    r = client.post("/observe", json={"content": "x", "metadata": [1, 2, 3]})
    assert r.status_code == 422


def test_metadata_json_text_kept_as_string(client):
    """看起来像 JSON 的文本，也只是一个字符串 —— 服务端不得反序列化。"""
    payload = '{"嵌套": {"a": [1, 2, {"b": null}]}, "布尔": true}'
    created = client.post("/observe", json={"content": "x", "metadata": payload}).json()
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["metadata"] == payload
    assert isinstance(detail["metadata"], str)


def test_extra_fields_rejected(client):
    r = client.post("/observe", json={"content": "x", "unexpected": "field"})
    assert r.status_code == 422


@pytest.mark.parametrize("limit", [0, -1, 101, 1000])
def test_invalid_limit_rejected(client, limit):
    r = client.get(f"/experiences?limit={limit}")
    assert r.status_code == 422


def test_invalid_limit_has_typed_error_not_500(client):
    """limit 越界必须是 422 + 统一错误体，绝不能是 500。

    独立复测指出：校验曾同时存在于路由层与服务层，若路由层那处被移除，
    服务层的 ValueError 会直接冒泡成 500。现在只有服务层一处校验，
    且抛的是带全局处理器的 InvalidLimitError。
    """
    r = client.get("/experiences?limit=0")
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_limit"
    assert "limit" in body["detail"]


@pytest.mark.static
def test_limit_validation_has_single_source(client):
    """范围校验只允许出现在服务层一处（静态检查）。

    config.py 定义常量不算校验；这里查的是「比较判断」出现在哪个模块。
    """
    root = config.PROJECT_ROOT / "src" / "yisheng"
    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # 只看真正的范围比较，而不是常量定义
        if "MIN_PAGE_LIMIT <=" in text or "MAX_PAGE_LIMIT <=" in text or "MAX_PAGE_LIMIT >=" in text:
            hits.append(path.relative_to(root).as_posix())
    assert hits == ["services/experience_service.py"], f"limit 范围校验出现在多处: {hits}"


@pytest.mark.parametrize("limit", [1, 100])
def test_boundary_limit_accepted(client, limit):
    client.post("/observe", json={"content": "x"})
    r = client.get(f"/experiences?limit={limit}")
    assert r.status_code == 200


def test_malformed_json_rejected(client):
    r = client.post("/observe", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_duplicate_identical_content_kept_separately(client):
    """重复请求不被去重：两条内容完全相同也是两次经历，各自身份独立。"""
    a = client.post("/observe", json={"content": "同样的文本"}).json()
    b = client.post("/observe", json={"content": "同样的文本"}).json()
    assert a["id"] != b["id"]
    assert a["sequence"] != b["sequence"]
