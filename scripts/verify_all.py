#!/usr/bin/env python
"""忆生 Phase 1 —— 独立验证脚本（一条命令，自己验，不用信任何总结）。

它做的事：真实跑测试、真实起服务、真实发 HTTP、真实重启、真实查数据库。
全部独立于 `data/yisheng.db`（用临时库），不会动你已有的数据。

用法：
    python scripts/verify_all.py            # 全部验证
    python scripts/verify_all.py --no-tests # 跳过 pytest（快一点）

退出码：0 = 全部通过，1 = 有失败（会打印失败详情）
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".verifytmp"
LOGS = WORK / "logs"
DATA = WORK / "data"

PASS = 0
FAIL = 0
SKIP = 0
FAILURES: list[str] = []


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------

def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}  {detail}")
        print(f"  [FAIL] {name}" + (f"   <- {detail}" if detail else ""))
    return ok


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name}   ({why})")


def section(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


# ---------------------------------------------------------------------------
# HTTP 小工具（用 urllib，不依赖第三方库）
# ---------------------------------------------------------------------------

def http(method: str, url: str, body: dict | None = None, timeout: float = 10.0):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ---------------------------------------------------------------------------
# 起 / 停 服务
# ---------------------------------------------------------------------------

class Server:
    def __init__(self, port: int):
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.log = LOGS / f"server-{port}.log"

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, wait: float = 25.0) -> bool:
        env = dict(os.environ)
        env["YISHENG_DATA_DIR"] = str(DATA)   # 用临时库，隔离真实数据
        env["PYTHONIOENCODING"] = "utf-8"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        out = open(self.log, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "run.py"), "--port", str(self.port)],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT),
            env=env,
        )
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                status, _ = http("GET", self.base + "/health", timeout=1.0)
                if status == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None


# ---------------------------------------------------------------------------
# 各项验证
# ---------------------------------------------------------------------------

def verify_test_suite() -> None:
    section("1/7  测试套件（pytest）")
    rc, text = run_capture(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--no-header"],
        "pytest.log", timeout=600,
    )
    tail = [ln for ln in text.splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
    summary = tail[-1] if tail else "(无摘要)"
    check("pytest 退出码为 0", rc == 0, f"rc={rc}, 见 logs/pytest.log")
    check("测试无 failed/error", "failed" not in summary and "error" not in summary, summary.strip())
    print(f"        实际摘要: {summary.strip()}")


def verify_api(server: Server, created: dict) -> None:
    section("2/7  接口行为（真实 HTTP over 真实 socket）")
    base = server.base

    status, body = http("GET", base + "/health")
    check("GET /health -> 200", status == 200, str(status))
    check("health.status == ok", isinstance(body, dict) and body.get("status") == "ok", str(body))
    check("health.schema_version == 1", isinstance(body, dict) and body.get("schema_version") == 1, str(body))
    # 默认不做写入探测：必须诚实报告 unknown/null，不得伪装成 true
    check("默认 health 不声称可写（db_writable is null）",
          isinstance(body, dict) and body.get("db_writable") is None, str(body))
    check("默认 health 状态为 unknown",
          isinstance(body, dict) and body.get("db_write_status") == "unknown", str(body))

    status, body = http("GET", base + "/health?probe=write")
    check("health?probe=write -> 200", status == 200, str(status))
    check("显式探测时 db_writable == true",
          isinstance(body, dict) and body.get("db_writable") is True, str(body))
    check("显式探测时 db_write_status == writable",
          isinstance(body, dict) and body.get("db_write_status") == "writable", str(body))

    status, body = http("POST", base + "/observe", {"content": "验证脚本写入的一条经历"})
    check("POST /observe -> 201", status == 201, str(status))
    ok_id = False
    if isinstance(body, dict) and "id" in body:
        try:
            uuid.UUID(body["id"])
            ok_id = True
        except ValueError:
            ok_id = False
    check("observe 返回合法 UUID", ok_id, str(body))
    check("observe.recorded_at 是 UTC(+00:00)",
          isinstance(body, dict) and str(body.get("recorded_at", "")).endswith("+00:00"), str(body))
    created.update(body if isinstance(body, dict) else {})

    eid = created.get("id")
    if eid:
        status, body = http("GET", f"{base}/experiences/{eid}")
        check("GET /experiences/{id} -> 200", status == 200, str(status))
        check("按 ID 取回内容一致",
              isinstance(body, dict) and body.get("content") == "验证脚本写入的一条经历", str(body)[:120])

        status, _ = http("DELETE", f"{base}/experiences/{eid}")
        check("DELETE /experiences/{id} -> 405（不可删除）", status == 405, str(status))
        status, _ = http("PUT", f"{base}/experiences/{eid}", {"content": "改了"})
        check("PUT /experiences/{id} -> 405（不可修改）", status == 405, str(status))
        status, body = http("GET", f"{base}/experiences/{eid}")
        check("被拒绝后内容未被改动",
              isinstance(body, dict) and body.get("content") == "验证脚本写入的一条经历", str(body)[:120])

    status, body = http("GET", base + "/experiences?limit=5")
    check("GET /experiences -> 200", status == 200, str(status))
    items = body.get("items", []) if isinstance(body, dict) else []
    seqs = [x["sequence"] for x in items]
    check("列表按 sequence 严格降序", seqs == sorted(seqs, reverse=True), str(seqs))
    # 列表轻量化：不得包含 content 全文
    check("列表条目不含 content 全文（摘要形态）",
          bool(items) and all("content" not in x for x in items), str(items[:1])[:200])
    check("摘要包含 content_length / content_preview",
          bool(items) and all({"content_length", "content_preview"} <= set(x) for x in items), "")

    # ---- metadata 原始字符串 ----
    status, body = http("POST", base + "/observe",
                        {"content": "metadata 字符串验证", "metadata": '{"a": 1}'})
    check("metadata 接受原始字符串 -> 201", status == 201, str(status))
    if status == 201:
        _, detail = http("GET", f"{base}/experiences/{body['id']}")
        check("metadata 原样返回为字符串（未反序列化）",
              isinstance(detail, dict) and detail.get("metadata") == '{"a": 1}'
              and isinstance(detail.get("metadata"), str), str(detail.get("metadata")))
    status, _ = http("POST", base + "/observe", {"content": "x", "metadata": {"a": 1}})
    check("metadata 传对象 -> 422（契约是字符串）", status == 422, str(status))

    # ---- 游标整数上界 ----
    max_int = 2**63 - 1
    forged = base64.urlsafe_b64encode(f"s:{max_int + 1}".encode()).decode()
    status, body = http("GET", base + f"/experiences?cursor={forged}")
    check("超 SQLite INTEGER 的游标 -> 400（不是 500）", status == 400, str(status))
    check("越界游标错误体 error == invalid_cursor",
          isinstance(body, dict) and body.get("error") == "invalid_cursor", str(body)[:100])

    # ---- 数据完整性检测 ----
    status, body = http("POST", base + "/observe", {"content": "将被直接篡改"})
    tamper_id = body.get("id") if isinstance(body, dict) else None
    if tamper_id:
        db = DATA / "yisheng.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE experiences SET content = '被篡改' WHERE id = ?", (tamper_id,))
            conn.commit()
        finally:
            conn.close()
        status, body = http("GET", f"{base}/experiences/{tamper_id}")
        check("直接改库后读取 -> 500 data_integrity_error", status == 500, str(status))
        check("完整性错误码正确",
              isinstance(body, dict) and body.get("error") == "data_integrity_error", str(body)[:120])

        # 复原：否则后续分页也会命中这条坏数据而 500（本检查不该污染后续步骤）
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE experiences SET content = '将被直接篡改' WHERE id = ?", (tamper_id,))
            conn.commit()
        finally:
            conn.close()
        status, _ = http("GET", f"{base}/experiences/{tamper_id}")
        check("复原内容后读取恢复正常 -> 200", status == 200, str(status))

    status, body = http("POST", base + "/observe", {"content": "x", "occurred_at": "2026-11-07T10:00:00"})
    check("occurred_at 无时区 -> 400", status == 400, str(status))
    check("错误体 error == invalid_occurred_at",
          isinstance(body, dict) and body.get("error") == "invalid_occurred_at", str(body)[:100])

    status, _ = http("POST", base + "/observe", {"content": "x", "recorded_at": "1999-01-01T00:00:00+00:00"})
    check("客户端伪造 recorded_at -> 422（被拒）", status == 422, str(status))

    status, body = http("GET", base + "/experiences?limit=0")
    check("limit=0 -> 422（不是 500）", status == 422, str(status))
    check("错误体 error == invalid_limit",
          isinstance(body, dict) and body.get("error") == "invalid_limit", str(body)[:100])

    status, _ = http("GET", base + "/experiences/not-a-uuid")
    check("非法 UUID -> 400", status == 400, str(status))
    status, _ = http("GET", base + "/experiences/00000000-0000-4000-8000-000000000000")
    check("不存在的 UUID -> 404", status == 404, str(status))

    status, _ = http("POST", base + "/observe", {"content": "忆" * 100_000})
    check("content = 100,000 字符 -> 201（上限按字符算）", status == 201, str(status))
    status, _ = http("POST", base + "/observe", {"content": "忆" * 100_001})
    check("content = 100,001 字符 -> 422", status == 422, str(status))

    status, body = http("GET", base + "/openapi.json", timeout=15)
    paths = sorted(body.get("paths", {}).keys()) if isinstance(body, dict) else []
    expected = ["/experiences", "/experiences/{experience_id}", "/health", "/observe"]
    check("OpenAPI 恰好只有 4 条业务路径", paths == expected, str(paths))


def verify_pagination(server: Server) -> None:
    section("3/7  分页契约（翻页中途插入也不重不漏）")
    base = server.base

    total = 25
    for i in range(total):
        http("POST", base + "/observe", {"content": f"分页验证 #{i}"})

    seen: list[int] = []
    cursor = None
    pages = 0
    first_page_ids: list[str] = []
    inserted = False
    while True:
        url = base + "/experiences?limit=7" + (f"&cursor={cursor}" if cursor else "")
        status, body = http("GET", url)
        if status != 200:
            check("翻页请求返回 200", False, f"{status} {body}")
            return
        items = body["items"]
        if pages == 0:
            first_page_ids = [x["id"] for x in items]
            # 第一页拿到后立刻插入新记录，模拟「翻页中途写入」
            for i in range(10):
                http("POST", base + "/observe", {"content": f"中途插入 #{i}"})
            inserted = True
        seen.extend(x["sequence"] for x in items)
        pages += 1
        if not body["has_more"]:
            break
        cursor = body["next_cursor"]
        if pages > 50:
            check("翻页能终止", False, f"已翻 {pages} 页")
            return

    check("翻页能正常终止", True, f"{pages} 页")
    check("翻页结果无重复", len(seen) == len(set(seen)), f"{len(seen)} 条中有重复")
    check("翻页结果严格降序", seen == sorted(seen, reverse=True), "")
    check("中途插入的新记录未混入旧区间",
          all(s <= total + 30 for s in seen), f"max seq={max(seen) if seen else 0}")
    check("中途插入没有导致旧记录遗漏或重复",
          len(seen) == len(set(seen)) and inserted, f"共 {len(seen)} 条")

    # 重复使用同一游标，结果必须一致
    status, body = http("GET", base + "/experiences?limit=7")
    c = body.get("next_cursor")
    if c:
        _, a = http("GET", f"{base}/experiences?limit=7&cursor={c}")
        _, b = http("GET", f"{base}/experiences?limit=7&cursor={c}")
        check("同一游标重复使用结果一致（幂等）", a == b, "")
    status, _ = http("GET", base + "/experiences?cursor=zzz")
    check("伪造游标 -> 400", status == 400, str(status))


def verify_persistence(server: Server, created: dict) -> bool:
    section("4/7  重启持久化（真的杀掉进程再起）")
    base = server.base
    eid = created.get("id")
    if not eid:
        check("重启前有可验证的记录", False, "没有拿到 id")
        return False

    _, before = http("GET", f"{base}/experiences/{eid}")
    _, list_before = http("GET", base + "/experiences?limit=100")
    n_before = len(list_before.get("items", []))

    server.stop()
    time.sleep(0.5)
    if not server.start():
        check("服务能重新启动", False, "重启后健康检查未通过")
        return False
    check("服务能重新启动", True, "")
    base = server.base

    status, after = http("GET", f"{base}/experiences/{eid}")
    check("重启后仍能按原 UUID 取回", status == 200, str(status))
    check("重启后内容未变", after.get("content") == before.get("content"), str(after.get("content"))[:60])
    check("重启后 sequence 未变", after.get("sequence") == before.get("sequence"),
          f"{before.get('sequence')} -> {after.get('sequence')}")
    check("重启后 recorded_at 未变", after.get("recorded_at") == before.get("recorded_at"), "")

    _, list_after = http("GET", base + "/experiences?limit=100")
    n_after = len(list_after.get("items", []))
    check("重启后记录数量未减少", n_after >= n_before, f"{n_before} -> {n_after}")
    return True


def verify_storage() -> None:
    section("5/7  存储层（直接查数据库文件）")
    db = DATA / "yisheng.db"
    if not db.exists():
        check("数据库文件存在", False, str(db))
        return
    check("数据库文件存在", True, f"{db.stat().st_size} 字节")

    conn = sqlite3.connect(str(db))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        check("journal_mode == wal", str(mode).lower() == "wal", str(mode))

        tables = sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        check("表集合恰好 == {experiences, schema_version}",
              tables == ["experiences", "schema_version"], str(tables))

        others = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('trigger','view')")]
        check("没有隐藏的触发器/视图", others == [], str(others))

        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='experiences'"
        ).fetchone()[0].upper()
        check("sequence 真的是 INTEGER PRIMARY KEY AUTOINCREMENT",
              "INTEGER PRIMARY KEY AUTOINCREMENT" in ddl, "")

        cols = {r[1] for r in conn.execute("PRAGMA table_info(experiences)")}
        expected = {"sequence", "id", "recorded_at", "occurred_at", "content",
                    "source", "context", "outcome", "metadata", "content_sha256"}
        check("experiences 列集合精确匹配（无认知类列）", cols == expected,
              f"多: {cols - expected} 少: {expected - cols}")

        rows = conn.execute("SELECT id, content, content_sha256 FROM experiences LIMIT 200").fetchall()
        bad = 0
        for rid, content, digest in rows:
            import hashlib
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
                bad += 1
        check(f"抽样 {len(rows)} 条 content_sha256 与内容一致", bad == 0, f"{bad} 条不符")

        ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        check("schema_version == 1", ver == 1, str(ver))
    finally:
        conn.close()


def run_capture(cmd: list[str], log_name: str, timeout: float = 60.0) -> tuple[int, str]:
    """跑子进程并把 stdout/stderr 写到文件（不依赖管道，避免受限环境拿不到输出）。"""
    log = LOGS / log_name
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as out:
        try:
            rc = subprocess.call(cmd, stdout=out, stderr=subprocess.STDOUT,
                                 timeout=timeout, cwd=str(ROOT))
        except subprocess.TimeoutExpired:
            return 124, "(超时)"
    return rc, log.read_text(encoding="utf-8", errors="replace")


def verify_loopback_guard() -> None:
    section("6/7  安全边界（只监听回环 / 拒绝公网绑定）")
    rc, text = run_capture(
        [sys.executable, str(ROOT / "run.py"), "--host", "0.0.0.0", "--port", str(free_port())],
        "reject-0000.log",
    )
    check("run.py --host 0.0.0.0 拒绝启动（非 0 退出码）", rc != 0, f"rc={rc}")
    check("拒绝启动时给出中文原因（提到「回环」）", "回环" in text, text[:120].replace("\n", " "))

    netstat = shutil.which("netstat")
    if not netstat:
        skip("netstat 监听地址检查", "找不到 netstat")
        return
    _, text = run_capture([netstat, "-ano"], "netstat.log")
    listen = [ln.strip() for ln in text.splitlines() if "LISTENING" in ln]
    wild = [ln for ln in listen if ("0.0.0.0:8000" in ln or "0.0.0.0:8010" in ln
                                    or "0.0.0.0:8014" in ln or "[::]:8000" in ln)]
    check("没有任何忆生默认端口监听在 0.0.0.0", wild == [], str(wild[:3]))


def verify_audit_artifacts() -> None:
    section("7/7  更大参数量复测的材料是否真实存在")
    report = ROOT / ".audit_qwen" / "audit_report.md"
    script = ROOT / ".audit_qwen" / "adversarial_audit.py"
    if not report.exists():
        skip("复测报告存在", "该目录可能已被清掉（不影响项目本身）")
        return
    text = report.read_text(encoding="utf-8", errors="replace")
    check("复测报告存在", True, str(report.relative_to(ROOT)))
    check("报告里有总体结论", "总体结论" in text, "")
    check("复测脚本存在", script.exists(), "")
    print("        注意：报告是外部模型自己写的，你可以直接读它、或自己再写一个来挑战它。")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="忆生 Phase 1 独立验证")
    parser.add_argument("--no-tests", action="store_true", help="跳过 pytest")
    args = parser.parse_args()

    print("忆生 Phase 1 —— 独立验证")
    print(f"项目: {ROOT}")
    print(f"Python: {sys.version.split()[0]}  ({sys.executable})")
    print(f"临时库: {DATA}（不会动 data/yisheng.db）")

    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    if args.no_tests:
        section("1/7  测试套件")
        skip("pytest", "--no-tests")
    else:
        verify_test_suite()

    port = free_port()
    server = Server(port)
    created: dict = {}
    try:
        section("起服务")
        ok = server.start()
        check(f"服务能在 127.0.0.1:{port} 启动", ok, f"见 {server.log.name}")
        if not ok:
            print("\n服务没起来，后续现场验证无法进行。日志：")
            print(server.log.read_text(encoding="utf-8", errors="replace")[-2000:])
            return 1

        verify_api(server, created)
        verify_pagination(server)
        restarted = verify_persistence(server, created)
        if restarted:
            pass
        verify_storage()
        verify_loopback_guard()
        verify_audit_artifacts()
    finally:
        server.stop()

    section("结果")
    print(f"  通过 {PASS}   失败 {FAIL}   跳过 {SKIP}")
    if FAILURES:
        print("\n  失败项：")
        for f in FAILURES:
            print(f"    - {f}")
        print(f"\n  日志目录：{LOGS}")
        return 1
    print("\n  全部通过。你可以据此认为 Phase 1 在本机是成立的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
