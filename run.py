#!/usr/bin/env python
"""忆生 (Living Memory) — Phase 1 启动入口。

用法：
    python run.py                # 默认 127.0.0.1:8000
    python run.py --port 8010    # 指定端口

为什么不直接用 `uvicorn yisheng.main:app`：
    本项目是 src 布局（代码在 src/yisheng/），直接跑 uvicorn 需要先安装包或
    设置 PYTHONPATH。这个脚本把 src 加入 sys.path，保证「克隆下来就能跑」。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from yisheng import config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="启动忆生 Phase 1（Experience Stream）")
    parser.add_argument("--host", default=config.HOST, help=f"监听地址（默认 {config.HOST}，不要改成 0.0.0.0）")
    parser.add_argument("--port", type=int, default=config.PORT, help=f"监听端口（默认 {config.PORT}）")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            "[拒绝启动] 忆生默认只允许监听回环地址。\n"
            f"  请求的地址: {args.host}\n"
            "  总纲要求：本地服务默认仅监听 127.0.0.1，不得暴露公网。\n"
            "  如确需改绑，请显式修改 config.HOST 并重新评估安全边界。",
            file=sys.stderr,
        )
        raise SystemExit(2)

    import uvicorn

    print(f"忆生 Phase 1 — http://{args.host}:{args.port}")
    print(f"  数据库: {config.DB_PATH}")
    print(f"  文档:   http://{args.host}:{args.port}/docs")
    uvicorn.run(
        "yisheng.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(SRC)] if args.reload else None,
        log_level="info",
    )


if __name__ == "__main__":
    main()
