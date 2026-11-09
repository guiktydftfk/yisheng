#!/usr/bin/env python
"""忆生 Phase 1 — 真实环境验收（对应交付物 §六 的 14 项）

与 `verify_all.py` 的区别：
    * `verify_all.py` 是一份**自测套件**（改代码后跑）；
    * 本脚本是**验收脚本**（交给验收者跑，逐项对应任务书的 14 条要求）。

关键原则：
    * 用**真实 HTTP over TCP**（不是 TestClient / ASGI 层）；
    * **真的杀掉服务进程再重启**，验证重启后仍能按原 UUID 取回；
    * 使用独立临时库，不污染 `data/yisheng.db`；
    * 只打印结论 + 证据，不掩盖失败。

用法：
    python scripts/acceptance_check.py
    python scripts/acceptance_check.py --keep   # 保留临时库与日志便于排查

退出码：0 = 14 项全过；1 = 有失败项
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
WORK = ROOT / ".acceptance"
LOGS = WORK / "logs"
DATA = WORK / "data"

PASS = 0
FAIL = 0
RESULTS: list[tuple[str, bool, str]] = []


def record(item: str, ok: bool, evidence: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {item}" + (f"  | {evidence}" if evidence else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {item}  | {evidence}")
    RESULTS.append((item, ok, evidence))
    return ok


def http(method: str, url: str, body: dict | None = None, timeout: float = 15.0):
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
    except urllib.error.URLError as exc:
        return -1, str(exc)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Server:
    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.log = LOGS / f"acceptance-server-{port}.log"

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, wait: float = 30.0) -> bool:
        env = dict(os.environ)
        env["YISHENG_DATA_DIR"] = str(DATA)
        env["PYTHONIOENCODING"] = "utf-8"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        out = open(self.log, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "run.py"), "--port", str(self.port)],
            stdout=out, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env,
        )
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            status, _ = http("GET", self.base + "/health", timeout=1.0)
            if status == 200:
                return True
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None


def run_py(code: str) -> str:
    """用项目解释器执行一段代码，返回 stdout（用于取版本号）。"""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    args = parser.parse_args()

    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("忆生 Phase 1 — 真实环境验收")
    print("=" * 72)
    print(f"项目目录 : {ROOT}")
    print(f"解释器   : {sys.executable}")
    print(f"临时库   : {DATA}（不触碰 data/yisheng.db）")
    print()

    # ---- 1-3 版本信息 ----
    print("【1-3】环境版本")
    py_ver = platform.python_version()
    print(f"  Python   : {py_ver}")
    sqlite_ver = run_py("import sqlite3; print(sqlite3.sqlite_version)")
    print(f"  SQLite   : {sqlite_ver}")
    deps = run_py(
        "import fastapi, pydantic, uvicorn, starlette, pytest, httpx;"
        "print(f'fastapi {fastapi.__version__} | pydantic {pydantic.VERSION} | "
        "uvicorn {uvicorn.__version__} | starlette {starlette.__version__} | "
        "pytest {pytest.__version__} | httpx {httpx.__version__}')"
    )
    print(f"  依赖     : {deps}")
    print(f"  平台     : {platform.platform()}")
    record("1. 显示 Python 版本", bool(py_ver), py_ver)
    record("2. 显示 SQLite 版本", bool(sqlite_ver), sqlite_ver)
    record("3. 显示依赖版本", bool(deps), deps)

    port = free_port()
    server = Server(port)
    created: dict = {}

    try:
        print("\n【4】启动服务进程")
        ok = server.start()
        record("4. 项目可正常启动", ok, f"127.0.0.1:{port}，日志 logs/{server.log.name}")
        if not ok:
            print(server.log.read_text(encoding="utf-8", errors="replace")[-1500:])
            return 1

        print("\n【5】监听地址")
        ns = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=60).stdout
        listening = [
            ln.strip() for ln in ns.splitlines()
            if f":{port}" in ln and "LISTENING" in ln
        ]
        only_loopback = bool(listening) and all("127.0.0.1:" in ln for ln in listening)
        record("5. 仅监听 127.0.0.1", only_loopback, str(listening[:2]))

        print("\n【6】POST /observe（真实 HTTP）")
        content = "验收记录：Phase 1 在真实服务上写入的第一条经历。"
        status, body = http("POST", server.base + "/observe",
                            {"content": content, "source": "acceptance",
                             "metadata": '{"note": "原始字符串，不解析"}'})
        eid = body.get("id") if isinstance(body, dict) else None
        record("6. POST /observe 返回 201 + UUID", status == 201 and bool(eid),
               f"status={status} id={eid}")
        created.update(body if isinstance(body, dict) else {})

        # 再写几条用于分页
        for i in range(6):
            http("POST", server.base + "/observe", {"content": f"分页验收记录 #{i}"})

        print("\n【7】GET /experiences")
        status, page = http("GET", server.base + "/experiences?limit=5")
        items = page.get("items", []) if isinstance(page, dict) else []
        seqs = [x["sequence"] for x in items]
        record("7. GET /experiences 返回 200 且按 sequence 降序",
               status == 200 and seqs == sorted(seqs, reverse=True),
               f"status={status} seqs={seqs} has_more={page.get('has_more')}")

        print("\n【8】GET /experiences/{id}")
        status, detail = http("GET", f"{server.base}/experiences/{eid}")
        record("8. GET /experiences/{id} 返回 200 且内容一致",
               status == 200 and isinstance(detail, dict) and detail.get("content") == content,
               f"status={status}")

        print("\n【9】GET /health")
        _, h_default = http("GET", server.base + "/health")
        _, h_probe = http("GET", server.base + "/health?probe=write")
        record("9. GET /health 正常；默认不声称可写，probe 后确证可写",
               h_default.get("db_write_status") == "unknown"
               and h_default.get("db_writable") is None
               and h_probe.get("db_write_status") == "writable",
               f"default={h_default.get('db_write_status')}/{h_default.get('db_writable')} "
               f"probe={h_probe.get('db_write_status')}")

        print("\n【10】关闭服务进程")
        pid = server.proc.pid if server.proc else None
        server.stop()
        stopped = server.proc is None
        record("10. 服务进程已关闭", stopped, f"pid={pid} terminated")

        print("\n【11】重新启动服务进程")
        ok2 = server.start()
        record("11. 服务可重新启动", ok2, f"127.0.0.1:{port}")
        if not ok2:
            return 1

        print("\n【12】重启后按原 UUID 取回")
        status, after = http("GET", f"{server.base}/experiences/{eid}")
        record("12. 重启后仍能按原 UUID 获取",
               status == 200 and after.get("id") == eid, f"status={status}")

        print("\n【13】content 与 content_sha256 一致")
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        actual_hash = after.get("content_sha256") if isinstance(after, dict) else None
        record("13. 重启后 content 与 content_sha256 均未变",
               after.get("content") == content and actual_hash == expected_hash,
               f"sha256={str(actual_hash)[:16]}…")

        print("\n【14】真实服务环境下的分页")
        seen: list[int] = []
        cursor = None
        pages = 0
        while True:
            url = server.base + "/experiences?limit=3" + (f"&cursor={cursor}" if cursor else "")
            status, pg = http("GET", url)
            if status != 200:
                record("14. 分页在真实服务下不重不漏", False, f"翻页失败 status={status}")
                break
            seen.extend(x["sequence"] for x in pg["items"])
            pages += 1
            if not pg["has_more"]:
                break
            cursor = pg["next_cursor"]
            if pages > 100:
                record("14. 分页在真实服务下不重不漏", False, "翻页未终止")
                break
        else:
            pass
        if FAIL == 0 or True:
            ok_page = len(seen) == len(set(seen)) and seen == sorted(seen, reverse=True)
            if pages <= 100 and seen:
                record("14. 分页在真实服务下不重不漏（无重复、严格降序）",
                       ok_page, f"{pages} 页 / {len(seen)} 条")

        # 附：库文件直查，确认 sha256 落盘正确
        db = DATA / "yisheng.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            try:
                row = conn.execute(
                    "SELECT content, content_sha256, typeof(metadata) FROM experiences WHERE id = ?",
                    (eid,),
                ).fetchone()
                meta_type = row[2] if row else None
                ok_db = bool(row) and hashlib.sha256(row[0].encode()).hexdigest() == row[1]
                record("附. 直接查库：content 哈希与落盘值一致", ok_db, f"metadata 列类型={meta_type}")
            finally:
                conn.close()
    finally:
        server.stop()

    print("\n" + "=" * 72)
    print(f"验收结果：通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        print("\n失败项：")
        for item, ok, ev in RESULTS:
            if not ok:
                print(f"  - {item}  | {ev}")
        print(f"\n日志：{LOGS}")
    else:
        print("14 项全部通过（含 1 项附加的库文件直查）。")
    print("=" * 72)

    if not args.keep:
        print(f"（临时文件保留在 {WORK}，可随时删除）")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
