"""统一错误模型与异常处理。

错误响应体统一为 ``{"error": "<机器可读类别>", "detail": <人类可读细节>}``。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("yisheng")


class InvalidCursorError(ValueError):
    """游标无法解码、格式非法或序号超出 SQLite INTEGER 范围。"""


class InvalidOccurredAtError(ValueError):
    """``occurred_at`` 缺失时区。"""


class InvalidLimitError(ValueError):
    """``limit`` 超出允许范围。"""


class DataIntegrityError(RuntimeError):
    """存储内容与其 ``content_sha256`` 不一致 —— 数据完整性已被破坏。

    这是**服务端数据损坏**，不是客户端冲突，因此映射为 HTTP 500。

    能力边界（不要过度声称）：
        本检测只能发现「content 与摘要不一致」。
        如果内容被同时改写 content 与 content_sha256，普通 SHA-256 无法识别。
    """

    def __init__(self, sequence: int, experience_id: str, reason: str = "") -> None:
        self.sequence = sequence
        self.experience_id = experience_id
        self.reason = reason
        detail = f"sequence={sequence} id={experience_id}"
        if reason:
            detail += f" reason={reason}"
        super().__init__(detail)


def _error(status: int, error: str, detail) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_: Request, exc: StarletteHTTPException):  # noqa: ANN202
        label = {
            400: "bad_request",
            404: "not_found",
            405: "method_not_allowed",
        }.get(exc.status_code, "http_error")
        return _error(exc.status_code, label, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(_: Request, exc: RequestValidationError):  # noqa: ANN202
        # pydantic 的 errors() 里 ctx.error 是异常对象，不能直接 JSON 序列化，
        # 且 input 可能是任意对象（例如整段 content），因此统一做一次可序列化清洗。
        cleaned = []
        for err in exc.errors():
            item = {k: v for k, v in err.items() if k not in ("ctx", "input")}
            ctx = err.get("ctx") or {}
            if ctx:
                item["ctx"] = {k: str(v) for k, v in ctx.items()}
            cleaned.append(item)
        return _error(422, "validation_error", cleaned)

    @app.exception_handler(InvalidCursorError)
    async def _cursor_exc(_: Request, exc: InvalidCursorError):  # noqa: ANN202
        return _error(400, "invalid_cursor", str(exc))

    @app.exception_handler(InvalidOccurredAtError)
    async def _occurred_exc(_: Request, exc: InvalidOccurredAtError):  # noqa: ANN202
        return _error(400, "invalid_occurred_at", str(exc))

    @app.exception_handler(InvalidLimitError)
    async def _limit_exc(_: Request, exc: InvalidLimitError):  # noqa: ANN202
        return _error(422, "invalid_limit", str(exc))

    @app.exception_handler(DataIntegrityError)
    async def _integrity_exc(_: Request, exc: DataIntegrityError):  # noqa: ANN202
        # 服务端数据损坏 → 500。不回显被篡改的内容，只给出定位信息。
        logger.error("data integrity violation: %s", exc)
        return _error(
            500,
            "data_integrity_error",
            {
                "message": "存储内容与其 content_sha256 不一致，数据完整性已被破坏",
                "sequence": exc.sequence,
                "id": exc.experience_id,
            },
        )


__all__ = [
    "DataIntegrityError",
    "InvalidCursorError",
    "InvalidLimitError",
    "InvalidOccurredAtError",
    "install_error_handlers",
]
