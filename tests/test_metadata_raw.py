"""P0-1：``metadata`` 必须真正按原始字符串存储。

契约：
    metadata 只作为字符串存储，**不解析、不校验内部结构、不建索引**。

本文件同时在 API 层与数据库层两个层面证明这件事：
    * API 层：传字符串能过、传对象被拒、返回值是 str
    * 数据库层：库里那一列与提交的字符串**逐字节相同**
"""

from __future__ import annotations

import sqlite3


def _raw_metadata(conn: sqlite3.Connection, experience_id: str):
    row = conn.execute(
        "SELECT metadata, typeof(metadata) AS t FROM experiences WHERE id = ?",
        (experience_id,),
    ).fetchone()
    assert row is not None
    return row["metadata"], row["t"]


def test_metadata_plain_string_roundtrip(client, conn):
    """普通字符串原样保存。"""
    text = "随手记：今天没什么特别的。"
    created = client.post("/observe", json={"content": "x", "metadata": text}).json()
    detail = client.get(f"/experiences/{created['id']}").json()

    assert detail["metadata"] == text
    assert isinstance(detail["metadata"], str)


def test_metadata_json_text_roundtrip_as_string(client, conn):
    """看起来像 JSON 的文本，也只是文本 —— 服务端不得反序列化。"""
    text = '{"speaker": "user", "turn": 41, "tags": ["a", "b"]}'
    created = client.post("/observe", json={"content": "x", "metadata": text}).json()
    detail = client.get(f"/experiences/{created['id']}").json()

    assert detail["metadata"] == text          # 逐字相等
    assert isinstance(detail["metadata"], str)  # 没有被变成 dict
    assert not isinstance(detail["metadata"], (dict, list))


def test_metadata_special_chars_preserved(client, conn):
    """空格、换行、制表符、引号、反斜杠、emoji 全部原样保留。"""
    text = '  前导空格\n\t制表与换行\r\n双引号"与\'单引号\' 反斜杠\\ 结尾空格  😀 '
    created = client.post("/observe", json={"content": "x", "metadata": text}).json()
    detail = client.get(f"/experiences/{created['id']}").json()

    assert detail["metadata"] == text


def test_metadata_not_interpreted(client):
    """不被擅自解释：非法 JSON 也照存，不报错、不改写。"""
    bad = '{"unclosed": [1, 2, '     # 明显不是合法 JSON
    r = client.post("/observe", json={"content": "x", "metadata": bad})
    assert r.status_code == 201
    detail = client.get(f"/experiences/{r.json()['id']}").json()
    assert detail["metadata"] == bad


def test_metadata_db_value_equals_submitted(client, conn):
    """数据库中的 metadata 与提交的字符串**逐字一致**（列类型为 TEXT）。"""
    text = "库里应该和这里一模一样：包含 中文、emoji😀、以及 'quote'"
    created = client.post("/observe", json={"content": "x", "metadata": text}).json()

    raw, coltype = _raw_metadata(conn, created["id"])
    assert raw == text, "数据库里存的不是提交的原始字符串"
    assert coltype == "text"


def test_metadata_object_rejected(client):
    """对象/数组一律 422：契约是原始字符串，不提供"自动识别 JSON"。"""
    for bad in ({"k": "v"}, [1, 2, 3], 123, True):
        r = client.post("/observe", json={"content": "x", "metadata": bad})
        assert r.status_code == 422, f"metadata={bad!r} 应被拒绝"


def test_metadata_absurd_structure_not_rejected(client):
    """超出常规范围的结构（超长嵌套文本）不被解释，也不被拒绝。

    只要它是字符串，服务端就不该对它做任何判断。
    """
    text = "[" * 500 + "]" * 500          # 很深但仍是字符串
    r = client.post("/observe", json={"content": "x", "metadata": text})
    assert r.status_code == 201
    detail = client.get(f"/experiences/{r.json()['id']}").json()
    assert detail["metadata"] == text


def test_metadata_none_stays_none(client):
    created = client.post("/observe", json={"content": "x"}).json()
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["metadata"] is None
