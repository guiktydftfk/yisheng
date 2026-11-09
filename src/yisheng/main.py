"""FastAPI 应用装配。

默认只监听 127.0.0.1（见 ``config.HOST``）—— 不得暴露公网。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from . import __version__, config
from .api.routes import router
from .db.migrate import init_db
from .errors import install_error_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("yisheng")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    version = init_db()
    logger.info("schema_version=%s db=%s", version, config.DB_PATH)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=config.APP_NAME,
        version=__version__,
        description=(
            "忆生 (Living Memory) Phase 1 — Experience Stream。\n\n"
            "纯粹的原始经历记录系统：append-only、不可修改、不含任何认知功能。"
        ),
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()


def main() -> None:  # pragma: no cover - 命令行入口
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
