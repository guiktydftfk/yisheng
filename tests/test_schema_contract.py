"""契约测试（验收 1-7）。

验收要求：
  ① 原始经历的 ID 永久稳定，任何后续模块都可以引用
  ② 原始经历保存后不可被普通业务接口修改
  ③ 时间排序和 Cursor 分页具有确定性
  ④ 新记录写入不会导致已有分页产生重复或遗漏
  ⑤ 原始经历与未来衍生数据在架构上保持独立
  ⑥ 数据库中不存在未经需求批准的自动解释、关联或认知数据
  ⑦ 程序重启后，数据及其身份标识保持不变
"""

from __future__ import annotations

import pytest

from yisheng import config
from yisheng.db import repository
from yisheng.db.connection import connect
from yisheng.db.migrate import apply_migrations


# ---------------------------------------------------------------- ① 身份稳定

def test_uuid_stable_after_restart(client, db_path):
    created = client.post("/observe", json={"content": "重启后我还在"}).json()

    # 模拟重启：完全关闭应用与连接，重新打开数据库
    fresh = connect(db_path)
    row = repository.get_experience(fresh, created["id"])
    fresh.close()

    assert row is not None
    assert row["id"] == created["id"]
    assert row["sequence"] == created["sequence"]


def test_content_sha256_matches_content(client):
    created = client.post("/observe", json={"content": "内容与哈希必须对得上"}).json()
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["content_sha256"] == repository.sha256_of(detail["content"])


def test_uuid_is_unique_per_record(client):
    ids = {client.post("/observe", json={"content": f"same text {i}"}).json()["id"] for i in range(20)}
    assert len(ids) == 20


# ---------------------------------------------------------------- ② 不可修改

@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_modification_endpoints_forbidden(client, method):
    created = client.post("/observe", json={"content": "不能被改"}).json()
    kwargs = {} if method == "delete" else {"json": {"content": "改了"}}
    r = getattr(client, method)(f"/experiences/{created['id']}", **kwargs)
    assert r.status_code == 405
    # 原记录完好
    assert client.get(f"/experiences/{created['id']}").json()["content"] == "不能被改"


@pytest.mark.repository
def test_repository_exposes_no_update_or_delete():
    """结构上就不给修改留接口：仓储层不得导出 update/delete 方法。"""
    forbidden = {"update", "delete", "remove", "edit", "replace", "mutate", "patch"}
    public = {n for n in dir(repository) if not n.startswith("_")}
    assert not (public & forbidden), f"仓储层暴露了修改类接口: {public & forbidden}"


@pytest.mark.repository
@pytest.mark.static
def test_repository_contains_no_write_sql_besides_insert():
    """仓储层的可执行 SQL 里只允许 SELECT 与 INSERT。

    检查前先剥掉注释与文档字符串，避免把说明文字当成 SQL。
    """
    import re

    source = (config.PROJECT_ROOT / "src" / "yisheng" / "db" / "repository.py").read_text(encoding="utf-8")
    # 去掉三引号文档字符串与 # 注释
    stripped = re.sub(r'"""[\s\S]*?"""', "", source)
    stripped = re.sub(r"#.*", "", stripped)
    code = stripped.upper()

    assert "UPDATE " not in code, "仓储层出现了 UPDATE 语句"
    assert "DELETE " not in code, "仓储层出现了 DELETE 语句"
    assert "DROP " not in code, "仓储层出现了 DROP 语句"
    assert "ALTER " not in code, "仓储层出现了 ALTER 语句"


@pytest.mark.repository
@pytest.mark.static
def test_sql_only_in_repository_module():
    """契约自证：整个包内只有 repository.py 含 SQL。

    「业务逻辑里不许出现 SQL」若只是口头的，迟早会破。这条测试把它变成
    可静态检查的事实：除 repository.py 外，任何一个 .py 模块都不得出现
    SQL 关键字（DDL 只存在于 schema.sql 这个纯 SQL 文件里）。

    注：schema.sql 本身是 SQL 文件，不在本检查范围内；migrate.py 通过
    executescript 读取它，不含内联 SQL。
    """
    import re

    pkg = config.PROJECT_ROOT / "src" / "yisheng"
    allowed = {pkg / "db" / "repository.py"}

    # 只匹配「像 SQL 语句」的片段，避免误伤普通英文注释
    sql_pattern = re.compile(
        r"\b(SELECT\s+[\w*,.\s()]+?\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
        r"CREATE\s+(TABLE|INDEX|TRIGGER|VIEW)|DROP\s+(TABLE|INDEX|TRIGGER|VIEW)|ALTER\s+TABLE)\b",
        re.IGNORECASE,
    )

    offenders: list[str] = []
    for path in sorted(pkg.rglob("*.py")):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        # 剥掉文档字符串与行注释，只检查真正的代码
        stripped = re.sub(r'"""[\s\S]*?"""', "", text)
        stripped = re.sub(r"#.*", "", stripped)
        for m in sql_pattern.finditer(stripped):
            offenders.append(f"{path.relative_to(config.PROJECT_ROOT)}: {m.group(0)[:40]}")

    assert not offenders, "在 repository.py 之外发现了 SQL：\n" + "\n".join(offenders)


@pytest.mark.sqlite
def test_no_triggers_or_views(conn):
    """⑤⑥ 除了两张表，不允许存在任何隐藏的触发器或视图。"""
    assert repository.list_non_table_objects(conn) == [], "出现了未批准的触发器/视图"


@pytest.mark.static
def test_schema_sql_file_only_has_allowed_objects(conn):
    """schema.sql 中定义的建表语句只有 experiences 与 schema_version。"""
    import re

    ddl = config.SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    # 去掉 -- 注释后，提取所有 CREATE TABLE 目标
    body = re.sub(r"--.*", "", ddl)
    tables = re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", body, re.IGNORECASE)
    assert set(tables) == set(config.ALLOWED_TABLES), f"schema.sql 建了未批准的表: {set(tables) - set(config.ALLOWED_TABLES)}"


def test_sequence_not_reused_after_committed_delete(conn):
    """**已提交**记录用过的序号，后续插入不会重新使用。

    旧名称 `test_no_direct_sql_update` 名不符实：这条测试执行的是 DELETE + INSERT，
    与"SQL 不可 update"无关。现改名为准确描述其行为的名称。

    注意（AUTOINCREMENT 的真实保证）：
      ✅ 已提交用过的序号不复用
      ❌ 回滚事务占用的序号**可能被复用** —— 见 test_sequence_may_be_reused_after_rollback
      ❌ 不保证连续 —— 见 test_sequence_gaps_are_allowed
    """
    repository.insert_experience(conn, content="原始")
    before = repository.count_experiences(conn)
    conn.execute("DELETE FROM experiences")  # 仅测试：验证已提交序号不复用
    repository.insert_experience(conn, content="删除后重写")
    row = conn.execute("SELECT sequence FROM experiences").fetchone()
    assert before == 1
    assert row["sequence"] >= 2  # 已提交序号不回退、不复用（但是否连续不保证）


# ---------------------------------------------------------------- ③ 排序确定

def test_order_deterministic_same_millisecond(conn):
    """同一毫秒内写入的多条记录，排序仍然稳定（靠 sequence，不靠时间戳）。"""
    for i in range(20):
        repository.insert_experience(conn, content=f"burst {i}")

    rows = repository.list_experiences(conn, limit=20)
    seqs = [r["sequence"] for r in rows]
    assert seqs == sorted(seqs, reverse=True)          # 严格降序
    assert len(set(seqs)) == len(seqs)                  # 无重复

    again = [r["sequence"] for r in repository.list_experiences(conn, limit=20)]
    assert again == seqs                                # 重复查询结果一致


def test_list_sorted_desc_by_sequence(client):
    for i in range(5):
        client.post("/observe", json={"content": f"e{i}"})
    items = client.get("/experiences?limit=5").json()["items"]
    assert [x["sequence"] for x in items] == [5, 4, 3, 2, 1]


# ---------------------------------------------------------------- ④ 翻页不漏不重

def test_cursor_no_dup_no_miss(client):
    total = 57
    for i in range(total):
        assert client.post("/observe", json={"content": f"e{i}"}).status_code == 201

    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        url = "/experiences?limit=10" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url).json()
        seen.extend(item["sequence"] for item in page["items"])
        pages += 1
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
        assert pages < 100, "翻页没有终止"

    assert len(seen) == total                       # 不多不少
    assert len(set(seen)) == total                  # 无重复
    assert seen == sorted(seen, reverse=True)       # 顺序正确


def test_insert_during_pagination(client):
    """翻页过程中插入新记录：已翻过的页不受影响，后续页不重复不遗漏。"""
    for i in range(25):
        client.post("/observe", json={"content": f"old{i}"})

    first = client.get("/experiences?limit=10").json()
    seen = [x["sequence"] for x in first["items"]]

    # 翻页中途插入 5 条新记录（sequence 会更大，落在已翻过的区间外）
    for i in range(5):
        client.post("/observe", json={"content": f"new{i}"})

    cursor = first["next_cursor"]
    while cursor:
        page = client.get(f"/experiences?limit=10&cursor={cursor}").json()
        seen.extend(x["sequence"] for x in page["items"])
        cursor = page["next_cursor"]

    # 原有 25 条必须全部且仅出现一次
    old_seqs = [s for s in seen if s <= 25]
    assert sorted(old_seqs, reverse=True) == list(range(25, 0, -1))
    assert len(old_seqs) == len(set(old_seqs))
    # 新记录不应被插入到已翻过的位置造成重复
    assert len(seen) == len(set(seen))


def test_cursor_is_opaque_and_stable(client):
    for i in range(30):
        client.post("/observe", json={"content": f"e{i}"})
    page = client.get("/experiences?limit=10").json()
    cursor = page["next_cursor"]
    # 不透明：不是明文数字
    assert cursor != str(page["items"][-1]["sequence"])
    # 可重复使用并得到一致结果
    a = client.get(f"/experiences?limit=10&cursor={cursor}").json()
    b = client.get(f"/experiences?limit=10&cursor={cursor}").json()
    assert a == b


@pytest.mark.parametrize("bad", ["zzz", "!!!", "c2Fi", "czot", "czow"])
def test_invalid_cursor_rejected(client, bad):
    client.post("/observe", json={"content": "x"})
    r = client.get(f"/experiences?cursor={bad}")
    assert r.status_code == 400
    assert r.json()["error"] in {"invalid_cursor", "bad_request"}


# ---------------------------------------------------------------- ⑤⑥ 架构独立

def test_schema_has_only_allowed_tables(conn):
    """⑤⑥ **应用自定义表**必须恰好是 {experiences, schema_version}。

    措辞精确说明（D2 决策）：
      我们**不能**声称"整个 SQLite 库只有两张物理表"，
      因为 `AUTOINCREMENT` 会令 SQLite 自动创建内部表 `sqlite_sequence`。
      契约只约束「应用自定义表」这一集合。

    用 sqlite_master 而非 PRAGMA table_list：后者会额外返回
    sqlite_schema / sqlite_temp_schema 等内部表，无法直接做集合相等断言。
    """
    tables = set(repository.list_user_tables(conn))
    assert tables == set(config.ALLOWED_TABLES), f"出现了未批准的表: {tables - set(config.ALLOWED_TABLES)}"

    # 反向断言：确认排除法没有把真表也排掉 —— sqlite_sequence 确实存在，且被正确排除
    all_names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    }
    assert "sqlite_sequence" in all_names, "AUTOINCREMENT 应产生内部表 sqlite_sequence"
    assert all_names - tables == {"sqlite_sequence"}, f"出现了未预期的内部表: {all_names - tables}"


def test_schema_has_no_cognitive_columns(conn):
    """⑤ 原始经历表不得出现解释/关联/信念/认知类衍生列。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(experiences)")}
    expected = {
        "sequence", "id", "recorded_at", "occurred_at",
        "content", "source", "context", "outcome", "metadata", "content_sha256",
    }
    assert cols == expected, f"列集合偏移: 多了 {cols - expected}，少了 {expected - cols}"

    forbidden_tokens = ("embedding", "vector", "sentiment", "summary", "belief",
                        "cognitive", "emotion", "importance", "tag", "concept")
    assert not [c for c in cols if any(t in c.lower() for t in forbidden_tokens)]


def test_no_cognitive_endpoints(client):
    """⑥ OpenAPI 中不得出现解释 / 搜索 / 关联 / 反思类端点。"""
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {"/observe", "/experiences", "/experiences/{experience_id}", "/health"}

    forbidden = ("search", "semantic", "relate", "associate", "summar", "reflect",
                 "belief", "cognit", "embed", "interpret", "consolidat", "memor")
    offenders = [p for p in paths if any(t in p.lower() for t in forbidden)]
    assert not offenders, f"出现了认知类端点: {offenders}"


def test_empty_database_has_no_derived_data(conn):
    """⑥ 全新库中不存在任何未经批准的衍生数据。"""
    assert repository.count_experiences(conn) == 0
    assert set(repository.list_user_tables(conn)) == set(config.ALLOWED_TABLES)


# ---------------------------------------------------------------- ⑦ 重启持久化

def test_persist_after_restart(db_path):
    from fastapi.testclient import TestClient

    from yisheng.main import create_app

    with TestClient(create_app()) as c1:
        created = c1.post("/observe", json={"content": "重启前写入"}).json()

    # 全新应用实例、全新连接
    with TestClient(create_app()) as c2:
        detail = c2.get(f"/experiences/{created['id']}").json()
        assert detail["id"] == created["id"]
        assert detail["content"] == "重启前写入"
        page = c2.get("/experiences").json()
        assert len(page["items"]) == 1


def test_wal_enabled(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_schema_version_recorded(conn):
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert [r["version"] for r in rows] == [1]


def test_migrations_idempotent(conn):
    v = apply_migrations(conn)
    assert v == 1
    rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"]
    assert rows == 1


def test_sequence_autoincrement_no_reuse(workdir):
    """sequence 必须由 AUTOINCREMENT 生成：删除后新记录既不复用旧序号，也不回退。"""
    path = workdir / "seq.db"
    c = connect(path)
    apply_migrations(c)
    a = repository.insert_experience(c, content="a")
    b = repository.insert_experience(c, content="b")
    c.execute("DELETE FROM experiences WHERE sequence = ?", (b["sequence"],))
    d = repository.insert_experience(c, content="d")
    c.close()
    assert (a["sequence"], b["sequence"], d["sequence"]) == (1, 2, 3)


def test_sqlite_contract_autoincrement_keyword(conn):
    """schema 中的 sequence 必须真的是 INTEGER PRIMARY KEY AUTOINCREMENT。"""
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='experiences'"
    ).fetchone()["sql"].upper()
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in sql


def test_insert_failure_does_not_break_store(client):
    """无效输入不得污染已有数据。"""
    good = client.post("/observe", json={"content": "好的记录"}).json()

    assert client.post("/observe", json={"content": ""}).status_code == 422
    assert client.post("/observe", json={"content": "x" * (config.MAX_CONTENT_CHARS + 1)}).status_code == 422
    assert client.post("/observe", json={"content": "  "}).status_code == 422
    assert client.post("/observe", json={"content": "x", "bogus": 1}).status_code == 422

    page = client.get("/experiences").json()
    assert [x["id"] for x in page["items"]] == [good["id"]]
    # 库仍然可读、可写，且服务健康
    assert client.get(f"/experiences/{good['id']}").status_code == 200
    assert client.get("/health").status_code == 200
    again = client.post("/observe", json={"content": "好的记录之后还能继续写"})
    assert again.status_code == 201
    assert again.json()["sequence"] == good["sequence"] + 1
