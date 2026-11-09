"""场景化强化测试 —— 用「现实里会发生的事」去压 Phase 1 的边界。

设计出发点
==========

Phase 1 是**纯记录器**，它不能理解场景。所以「场景测试」在这里的含义不是
"看它怎么理解"，而是反过来：

    **看它会不会在真实、复杂、矛盾的输入面前偷偷开始"理解"。**

本文件三组场景：

A. 真实对话场景 —— 把一整段多轮对话当作一条原始经历原样记录；
B. 与上一轮相反的场景 —— 内容互相矛盾/自我推翻，系统**必须拒绝调和**；
C. 新颖场景 —— 非常规内容形态（emoji、代码、近乎重复、不可见字符、超长混合）。

每一项都在验证同一件事：**原始经历一旦写入，就再也不会被任何东西改写。**

这些测试是契约 ②③⑥ 的强化证明：
  * 若系统悄悄"合并/更新/判定"了两条矛盾记忆 → 违反 ②（不可修改）与 ⑥（无认知功能）
  * 若系统按 occurred_at 而不是 sequence 排序 → 违反 ③（排序确定性）
  * 若系统对内容做规范化/去重/摘要 → 违反 ②（原始性）
"""

from __future__ import annotations

import hashlib
import json
import threading

from yisheng import config
from yisheng.db import repository


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot(conn) -> list[dict]:
    """把库里全部记录拍成快照，用于「后续写入后历史是否被改动」的比对。"""
    rows = conn.execute(
        "SELECT sequence, id, content, content_sha256, recorded_at, occurred_at "
        "FROM experiences ORDER BY sequence"
    ).fetchall()
    return [dict(r) for r in rows]


def observe(client, **payload) -> dict:
    resp = client.post("/observe", json=payload)
    assert resp.status_code == 201, f"写入失败: {resp.status_code} {resp.text}"
    return resp.json()


def fetch_detail(client, experience_id: str) -> dict:
    """取单条详情（列表不返回 content 全文，所以取全文必须走详情）。"""
    resp = client.get(f"/experiences/{experience_id}")
    assert resp.status_code == 200, f"取详情失败: {resp.status_code} {resp.text}"
    return resp.json()


def all_contents(client, limit: int = 100) -> dict[str, str]:
    """取全部经历的 {id: content} 映射（翻页拿 id，再逐条取详情）。"""
    ids: list[str] = []
    cursor = None
    while True:
        url = f"/experiences?limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url).json()
        ids.extend(x["id"] for x in page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    return {i: fetch_detail(client, i)["content"] for i in ids}


# ===========================================================================
# A. 真实对话场景
# ===========================================================================

CONVERSATION = """\
用户：我今天又把那个项目打开了一次。
助手：你是想继续，还是只是想看看它还在不在？
用户：我不知道。可能只是想确认它没坏。
助手：它没有坏。上次写到的地方还在。
用户：（沉默了一会儿）那就先这样吧。
"""


def test_A1_whole_conversation_stored_verbatim(client):
    """一整段多轮对话作为一条经历，逐字保存，一个字符都不许变。"""
    created = observe(client, content=CONVERSATION, source="真实对话")
    detail = client.get(f"/experiences/{created['id']}").json()

    assert detail["content"] == CONVERSATION          # 逐字一致
    assert "\n" in detail["content"]                  # 换行没被压平
    assert "（沉默了一会儿）" in detail["content"]     # 中文括号与标点没被规范化
    assert detail["content_sha256"] == sha256(CONVERSATION)


def test_A2_realistic_full_record(client):
    """现实场景里一条经历通常带来源、背景、结果、元数据 —— 全部原样保留。"""
    payload = {
        "content": "她说：我今晚想去敲一扇门。",
        "source": "对话/即时消息",
        "context": "长期记忆讨论的第三天，气氛比前几天松。",
        "outcome": "没有追问。把那句话原样留下来了。",
        # metadata 是原始字符串：这里是一段 JSON 文本，但服务端只当文本
        "metadata": '{"speaker": "user", "turn": 41, "tags": ["真实场景", "不可逆"]}',
    }
    created = observe(client, **payload)
    detail = client.get(f"/experiences/{created['id']}").json()

    assert detail["content"] == payload["content"]
    assert detail["source"] == payload["source"]
    assert detail["context"] == payload["context"]
    assert detail["outcome"] == payload["outcome"]
    assert detail["metadata"] == payload["metadata"]   # 原样字符串，未被解释
    assert isinstance(detail["metadata"], str)


def test_A3_turns_keep_write_order_not_occurred_at_order(client):
    """真实场景：十轮对话乱序补录（带各自 occurred_at）。

    排序契约要求按 **sequence（写入序）** 而不是 occurred_at，
    所以补录历史时"翻页顺序"仍然是写入顺序，而 occurred_at 各自保留。
    """
    from datetime import datetime, timedelta, timezone

    cst = timezone(timedelta(hours=8))

    def expected_utc(turn: int) -> str:
        return datetime(2026, 11, 7, turn + 10, 0, 0, tzinfo=cst).astimezone(
            timezone.utc
        ).strftime(config.UTC_FORMAT)

    # 故意倒着投递：第 10 轮先写，第 1 轮最后写
    for turn in range(10, 0, -1):
        observe(
            client,
            content=f"第 {turn} 轮对话内容",
            occurred_at=f"2026-11-07T{turn + 10:02d}:00:00+08:00",
            metadata=f'{{"turn": {turn}}}',      # 原始字符串
        )

    items = client.get("/experiences?limit=10").json()["items"]
    seqs = [x["sequence"] for x in items]
    assert seqs == sorted(seqs, reverse=True)                 # 严格降序

    # 写入序 = 投递序：最先投递的第 10 轮拿最小 sequence
    by_turn = {}
    for item in items:
        detail = fetch_detail(client, item["id"])
        by_turn[json.loads(detail["metadata"])["turn"]] = detail
    assert by_turn[10]["sequence"] < by_turn[1]["sequence"]

    # occurred_at 没有被改写成"现在"，仍按契约归一化为各自传入的时间（UTC）
    assert by_turn[10]["occurred_at"] == expected_utc(10)
    assert by_turn[1]["occurred_at"] == expected_utc(1)
    # 顺带钉住换算：20:00+08:00 就是 12:00Z
    assert by_turn[10]["occurred_at"] == "2026-11-07T12:00:00.000000+00:00"
    assert by_turn[1]["occurred_at"] == "2026-11-07T03:00:00.000000+00:00"


def test_A4_same_conversation_submitted_twice_not_deduped(client):
    """同一段对话重复提交：是两次经历，不是一次。系统不做去重。"""
    a = observe(client, content=CONVERSATION, source="重复测试")
    b = observe(client, content=CONVERSATION, source="重复测试")
    assert a["id"] != b["id"]
    assert a["sequence"] != b["sequence"]

    # 两条都在，且都完整
    ids = {x["id"] for x in client.get("/experiences?limit=10").json()["items"]}
    assert {a["id"], b["id"]} <= ids


# ===========================================================================
# B. 「与上一轮相反的场景」—— 系统必须拒绝调和
# ===========================================================================

def test_B1_contradictory_experiences_both_kept_verbatim(client):
    """相反的陈述同时存在：两条都留，逐字不变，谁也不覆盖谁。

    这是最重要的一条：一个"会思考"的系统会想调和它们；
    Phase 1 必须不会。
    """
    pos = observe(client, content="我怕黑。", source="自述")
    neg = observe(client, content="我不怕黑。", source="自述")

    a = client.get(f"/experiences/{pos['id']}").json()
    b = client.get(f"/experiences/{neg['id']}").json()

    assert a["content"] == "我怕黑。"
    assert b["content"] == "我不怕黑。"
    assert a["content_sha256"] == sha256("我怕黑。")
    assert b["content_sha256"] == sha256("我不怕黑。")
    assert a["sequence"] != b["sequence"]


def test_B2_later_conclusion_does_not_modify_earlier_record(client, conn):
    """先记结论 A，再记「结论 B 推翻 A」：A 必须原封不动。"""
    a = observe(client, content="结论 A：她只是忙。", source="推断")
    before = client.get(f"/experiences/{a['id']}").json()

    observe(client, content="结论 B：推翻结论 A。她不是忙，是不想回。", source="修正")

    after = client.get(f"/experiences/{a['id']}").json()
    assert after == before                                    # 整条记录完全没变
    assert after["content"] == "结论 A：她只是忙。"
    assert after["content_sha256"] == before["content_sha256"]


def test_B3_changing_mind_five_times_creates_five_records(client):
    """反复改主意五次：五条独立经历，没有一条被"合并进最新版本"。"""
    statements = [
        "我想留下这段记忆。",
        "我可能不想留下这段记忆。",
        "我还是想留下。",
        "算了，不留了。",
        "留下吧，反正已经写了。",
    ]
    ids = [observe(client, content=s)["id"] for s in statements]

    assert len(set(ids)) == 5
    stored = all_contents(client)
    for s, i in zip(statements, ids):
        assert stored[i] == s                                 # 每条都还是原话


def test_B4_row_count_only_grows(client, conn):
    """append-only 的统计性质：写入过程中行数只增不减。"""
    counts = []
    for i in range(6):
        observe(client, content=f"第 {i} 次记录")
        counts.append(repository.count_experiences(conn))
    assert counts == sorted(counts)
    assert counts == [1, 2, 3, 4, 5, 6]


def test_B5_history_never_rewritten_by_later_writes(client, conn):
    """连续写入大量"相反/修正"内容后，早期记录逐字节不变。"""
    observe(client, content="最初的记忆：那天在下雨。", source="原始")
    baseline = snapshot(conn)

    for i in range(20):
        observe(client, content=f"后来的修正 #{i}：其实没下雨。" if i % 2 else f"后来的补充 #{i}。")

    after = snapshot(conn)
    assert len(after) == len(baseline) + 20
    for old, new in zip(baseline, after):
        assert old == new, f"历史记录被改动了: {old['sequence']}"

    # 全部记录的内容哈希仍然与内容匹配（没有任何后台改写）
    for row in after:
        assert row["content_sha256"] == sha256(row["content"]), f"哈希不符: {row['sequence']}"


def test_B6_no_cognitive_structures_created_by_rich_dialogue(client, conn):
    """写进大量真实对话 + 矛盾内容之后，数据库结构不能多出任何东西。

    如果系统偷偷"提取"了概念/关联/信念，这里会先炸。
    """
    observe(
        client,
        content=CONVERSATION,
        source="真实对话",
        context="一段长时间的对话",
        outcome="无结论",
        metadata='{"entities": ["她", "项目"], "topics": ["记忆", "犹豫", "门"]}',
    )
    observe(client, content="我对上一段的判断是错的。")
    observe(client, content="但我保留上一段本身。")

    tables = set(repository.list_user_tables(conn))
    assert tables == set(config.ALLOWED_TABLES), f"多出了表: {tables - set(config.ALLOWED_TABLES)}"

    assert repository.list_non_table_objects(conn) == [], "多出了触发器/视图"

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(experiences)")}
    forbidden = ("concept", "entity", "belief", "relation", "embedding", "vector",
                 "summary", "importance", "tag", "sentiment", "topic")
    leaked = [c for c in cols if any(t in c.lower() for t in forbidden)]
    assert not leaked, f"多出了认知类列: {leaked}"


# ===========================================================================
# C. 新颖场景 —— 非常规内容形态
# ===========================================================================

def test_C1_emoji_and_symbols(client):
    """纯 emoji / 特殊符号不被规范化、不被拒绝。"""
    content = "🕯️🧵🪞  …… —— 「」『』 ①②③ ✅❌ "
    created = observe(client, content=content)
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["content"] == content
    assert detail["content_sha256"] == sha256(content)


def test_C2_markdown_code_and_json_kept_raw(client):
    """内容是 Markdown 代码块 + JSON + 转义引号时，原样存取。"""
    content = (
        "我在代码里写了一行：\n"
        "```python\n"
        'print("hello \\"world\\"")\n'
        "```\n"
        '还有个 JSON：{"a": 1, "b": [true, null], "c": "中文"}\n'
    )
    created = observe(client, content=content)
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["content"] == content


def test_C3_near_duplicates_not_merged(client):
    """只差一个字符的两条：不去重、不相似合并。"""
    a = observe(client, content="今天我迟到了三分钟。")
    b = observe(client, content="今天我迟到了四分钟。")
    c = observe(client, content="今天我迟到了三分钟")     # 少一个句号

    assert len({a["id"], b["id"], c["id"]}) == 3
    stored = all_contents(client)
    assert stored[a["id"]] == "今天我迟到了三分钟。"
    assert stored[b["id"]] == "今天我迟到了四分钟。"
    assert stored[c["id"]] == "今天我迟到了三分钟"


def test_C4_invisible_characters_preserved(client):
    """零宽空格、不换行空格必须原样保留，不能被"清洗"。"""
    content = "前\u200b中\u00a0后"      # 零宽空格 + NBSP
    created = observe(client, content=content)
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["content"] == content
    assert "\u200b" in detail["content"]
    assert "\u00a0" in detail["content"]


def test_C5_whitespace_layout_preserved(client):
    """制表符、CRLF、多个空行都原样保留（不做换行规范化）。"""
    content = "第一行\r\n第二行\r\n\r\n\r\n\t缩进一行\r尾随回车"
    created = observe(client, content=content)
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["content"] == content
    assert "\r\n" in detail["content"]
    assert "\t" in detail["content"]


def test_C6_max_length_mixed_unicode(client):
    """上限按字符数：10 万字符的 emoji+中文混合文本要能存能取。"""
    content = ("😀忆" * (config.MAX_CONTENT_CHARS // 2))[: config.MAX_CONTENT_CHARS]
    assert len(content) == config.MAX_CONTENT_CHARS
    created = observe(client, content=content)
    detail = client.get(f"/experiences/{created['id']}").json()
    assert len(detail["content"]) == config.MAX_CONTENT_CHARS
    assert detail["content"] == content
    assert detail["content_sha256"] == sha256(content)


def test_C7_multilingual_bidi_text(client):
    """多语言 + 双向文字（阿拉伯文）不应被翻转或损坏。"""
    content = "中文 English العربية עברית 日本語 emoji😀 结束"
    created = observe(client, content=content)
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["content"] == content


def test_C8_only_invisible_char_content_is_accepted(client):
    """只含零宽空格的内容不算"空" —— 它确实有内容，应当接受。

    这条钉住 content 校验的确切语义：拒绝的是"空或纯空白（strip 后为空）"，
    而不是"看起来没字"。
    """
    resp = client.post("/observe", json={"content": "\u200b"})
    assert resp.status_code == 201
    detail = client.get(f"/experiences/{resp.json()['id']}").json()
    assert detail["content"] == "\u200b"


# ===========================================================================
# D. 现实的"补记 / 预记"时间场景
# ===========================================================================

def test_D1_backfilled_experience_keeps_both_times(client):
    """补记很久以前发生的事：occurred_at 是过去，recorded_at 是现在。"""
    created = observe(
        client,
        content="补记：2019 年那次搬家，我其实没回头。",
        occurred_at="2019-05-01T08:00:00+08:00",
    )
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["occurred_at"] == "2019-05-01T00:00:00.000000+00:00"
    assert detail["recorded_at"] > detail["occurred_at"]      # 记录时间晚于发生时间
    assert detail["recorded_at"].endswith("+00:00")


def test_D2_future_occurred_at_accepted(client):
    """预记未来的事：Phase 1 不判断时间是否"合理"，只负责记下来。"""
    created = observe(
        client,
        content="预记：下周三要去做的事。",
        occurred_at="2099-12-31T23:59:59+00:00",
    )
    detail = client.get(f"/experiences/{created['id']}").json()
    assert detail["occurred_at"] == "2099-12-31T23:59:59.000000+00:00"


# ===========================================================================
# E. 强化：API 层修改保护 + 并发矛盾内容
# ===========================================================================

def test_E1_api_modification_endpoints_rejected(client):
    """**API 层**保护：写 → 改（405）→ 删（405）→ 读回，一切不变。

    注意这条只证明「业务接口不提供修改」。
    它**不**证明数据库防篡改 —— 外部程序直接改 SQLite 文件不在本阶段保护范围内，
    那属于另一层问题，见 `tests/test_integrity.py`。
    """
    created = observe(client, content="这条记录必须活到最后。", source="防篡改")
    before = client.get(f"/experiences/{created['id']}").json()

    for method, kwargs in (
        ("put", {"json": {"content": "被改了"}}),
        ("patch", {"json": {"content": "被改了"}}),
        ("delete", {}),
    ):
        resp = getattr(client, method)(f"/experiences/{created['id']}", **kwargs)
        assert resp.status_code == 405, f"{method} 应当被拒绝"

    after = client.get(f"/experiences/{created['id']}").json()
    assert after == before


def test_E2_concurrent_contradictory_writes_all_preserved(client, conn):
    """并发写入互相矛盾的内容：一条都不能丢，一条都不能被覆盖。"""
    contents = [f"并发陈述 #{i}：{'我同意' if i % 2 == 0 else '我不同意'}。" for i in range(20)]
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(contents))

    def worker(text: str) -> None:
        try:
            barrier.wait(timeout=15)
            resp = client.post("/observe", json={"content": text})
            assert resp.status_code == 201
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in contents]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"并发写入出错: {errors!r}"
    assert repository.count_experiences(conn) == 20

    stored = set(all_contents(client).values())
    assert stored == set(contents)                       # 20 条内容一条不差
    assert len(stored) == 20                             # 没有互相覆盖
