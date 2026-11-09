"""规模测试：把数据量放大到 5,000 条以上，验证分页契约在真实体量下仍成立。

与单元测试的区别：单元测试用的是几十条数据；这里用几千条 + 最大尺寸记录，
检查「翻页无重复无遗漏」「排序确定性」「最大 content 的写入与读取」是否依然成立。

用法：
    python scripts/scale_test.py            # 默认 5000 条
    python scripts/scale_test.py 20000      # 指定条数

独立数据库：写到 .stresstmp/scale.db，不碰 data/yisheng.db。
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yisheng import config  # noqa: E402
from yisheng.db import repository  # noqa: E402
from yisheng.db.connection import connect  # noqa: E402
from yisheng.db.migrate import apply_migrations  # noqa: E402
from yisheng.services import experience_service  # noqa: E402


def main() -> int:
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 5_000

    work = ROOT / ".stresstmp"
    work.mkdir(parents=True, exist_ok=True)
    db = work / "scale.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()

    config.DB_PATH = db
    config.DATA_DIR = work

    conn = connect(db)
    apply_migrations(conn)

    print(f"=== 写入 {total} 条记录 ===")
    t0 = time.perf_counter()
    for i in range(total):
        repository.insert_experience(conn, content=f"规模测试 #{i} 内容内容内容", source="scale")
    write_elapsed = time.perf_counter() - t0
    print(f"  写入耗时: {write_elapsed:.2f}s  ({total / write_elapsed:,.0f} 条/秒)")
    print(f"  实际条数: {repository.count_experiences(conn)}")

    # ---- 全量翻页，验证无重复、无遗漏、严格降序 ----
    print("\n=== 全量翻页（limit=97，奇数以测边界） ===")
    t0 = time.perf_counter()
    seen: list[int] = []
    cursor = None
    pages = 0
    latencies: list[float] = []
    while True:
        tp = time.perf_counter()
        page = experience_service.list_experiences(conn, limit=97, cursor=cursor)
        latencies.append((time.perf_counter() - tp) * 1000)
        seen.extend(item["sequence"] for item in page["items"])
        pages += 1
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
        if pages > total:  # 防死循环
            print("  [失败] 翻页未终止")
            return 1
    page_elapsed = time.perf_counter() - t0

    ok_count = len(seen) == total
    ok_uniq = len(set(seen)) == total
    ok_order = seen == sorted(seen, reverse=True)
    print(f"  页数: {pages}   总耗时: {page_elapsed:.2f}s")
    print(f"  单页延迟: 中位 {statistics.median(latencies):.2f}ms  最大 {max(latencies):.2f}ms")
    print(f"  条数正确 ({len(seen)}=={total}): {'✅' if ok_count else '❌'}")
    print(f"  无重复:              {'✅' if ok_uniq else '❌'}")
    print(f"  严格降序:            {'✅' if ok_order else '❌'}")

    # ---- 翻页中途插入，验证不重复不遗漏 ----
    print("\n=== 翻页中途插入 500 条 ===")
    first = experience_service.list_experiences(conn, limit=100)
    baseline = set(seen)  # 之前已看到全部
    for i in range(500):
        repository.insert_experience(conn, content=f"中途插入 #{i}")
    after = experience_service.list_experiences(conn, limit=100, cursor=first["next_cursor"])
    after_seqs = [x["sequence"] for x in after["items"]]
    no_dup = not (set(after_seqs) & set(x["sequence"] for x in first["items"]))
    # 新插入的 500 条 sequence 均 > total，不应出现在旧记录的翻页区间里
    no_leak = all(s <= total for s in after_seqs)
    print(f"  第二页与第一页无重叠: {'✅' if no_dup else '❌'}")
    print(f"  新记录未混入旧区间:   {'✅' if no_leak else '❌'}")

    # ---- 最大尺寸记录 ----
    print(f"\n=== 最大 content（{config.MAX_CONTENT_CHARS:,} 字符 × 20 条） ===")
    big = "忆" * config.MAX_CONTENT_CHARS
    t0 = time.perf_counter()
    big_ids = [
        repository.insert_experience(conn, content=big)["id"]
        for _ in range(20)
    ]
    big_elapsed = time.perf_counter() - t0
    print(f"  20 条写入耗时: {big_elapsed:.2f}s")
    tb = time.perf_counter()
    ok_len = all(len(repository.get_experience(conn, i)["content"]) == config.MAX_CONTENT_CHARS for i in big_ids)
    print(f"  20 条读回长度正确: {'✅' if ok_len else '❌'}  ({(time.perf_counter() - tb) * 1000:.0f}ms)")

    # ---- 列表查询不会因大记录而爆炸：列表现在是**摘要**，不含 content 全文 ----
    tl = time.perf_counter()
    page = experience_service.list_experiences(conn, limit=20)
    list_big_ms = (time.perf_counter() - tl) * 1000
    chars = sum(x["content_length"] for x in page["items"])
    payload_bytes = len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
    has_full = any("content" in x for x in page["items"])
    print(f"  含大记录的列表页(limit=20): {list_big_ms:.0f}ms, 覆盖字符 {chars:,}, 响应 {payload_bytes:,} 字节")
    print(f"  摘要中不含 content 全文: {'✅' if not has_full else '❌'}")
    print(f"  响应体 < 1MB: {'✅' if payload_bytes < 1_000_000 else '❌'}")

    # ---- 完整性 ----
    print("\n=== 完整性 ===")
    bad = 0
    for row in conn.execute("SELECT id, content, content_sha256 FROM experiences LIMIT 500"):
        if repository.sha256_of(row["content"]) != row["content_sha256"]:
            bad += 1
    print(f"  抽样 500 条哈希校验: {'✅ 全部一致' if bad == 0 else f'❌ {bad} 条不符'}")

    tables = repository.list_user_tables(conn)
    print(f"  表集合: {tables}  {'✅' if set(tables) == set(config.ALLOWED_TABLES) else '❌'}")

    conn.close()

    all_ok = (ok_count and ok_uniq and ok_order and no_dup and no_leak and ok_len
              and bad == 0 and not has_full and payload_bytes < 1_000_000)
    print(f"\n=== 结论：{'全部通过' if all_ok else '存在问题'} ===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
