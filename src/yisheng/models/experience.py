"""Pydantic 模型：请求 / 响应的唯一形状定义。

三层字段策略（D1/E2 决策后）：
  * ``Experience``          —— 详情响应，含完整 ``content``
  * ``ExperienceSummary``   —— 列表响应的轻量条目，**不含 content 全文**
  * ``ObserveResponse``     —— 写入响应，只返回身份与时间四元组

为什么列表不返回全文：
    最坏情况下 ``limit=100 × 100,000 字符 = 1,000 万字符``，UTF-8 可达约 40MB，
    并发数请求会同时占用大量内存与带宽。列表因此只返回摘要字段，
    完整内容通过 ``GET /experiences/{id}`` 获取。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import config


def _normalize_ts(value: datetime) -> str:
    """把带时区的 datetime 归一化为契约 4 的 UTC 文本格式。"""
    return value.astimezone(timezone.utc).strftime(config.UTC_FORMAT)


class ObserveRequest(BaseModel):
    """``POST /observe`` 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=config.MAX_CONTENT_CHARS)
    source: str | None = Field(None, max_length=config.MAX_SOURCE_CHARS)
    context: str | None = Field(None, max_length=config.MAX_CONTEXT_CHARS)
    outcome: str | None = Field(None, max_length=config.MAX_OUTCOME_CHARS)
    occurred_at: datetime | None = None

    # 契约：metadata 只作为**原始字符串**存储。
    # 服务端不解析、不校验内部结构、不建索引 —— 即使它看起来像 JSON 也只当文本。
    # 因此这里必须是 str，而不是 dict；传对象会被 422 拒绝（不提供"自动识别"）。
    metadata: str | None = None

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content 不能为空或纯空白")
        return v

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at_has_tz(cls, v: datetime | None) -> str | None:
        """带时区则归一化为 UTC；**不带时区原样留下**，交由服务层判 400。

        为什么不在 validator 里直接报错：pydantic 的校验失败统一是 422，
        而契约要求「不带时区」明确返回 400。所以这个判断被刻意放在服务层。
        """
        if v is None:
            return None
        if v.tzinfo is None or v.utcoffset() is None:
            return v  # type: ignore[return-value]  # 服务层会拒绝它
        return _normalize_ts(v)


class ObserveResponse(BaseModel):
    """``POST /observe`` 的 201 响应。

    只返回身份与时间四元组，不返回完整记录 —— 写入路径保持轻量，读走 GET。
    """

    id: str
    sequence: int
    recorded_at: str
    occurred_at: str | None = None


class ExperienceSummary(BaseModel):
    """列表条目：轻量摘要，**不含 content 全文**。

    ``content_preview`` 是内容的前 ``CONTENT_PREVIEW_CHARS`` 个字符，
    属于**截断**而不是摘要/概括 —— Phase 1 不生成任何语义摘要。
    """

    id: str
    sequence: int
    recorded_at: str
    occurred_at: str | None = None
    source: str | None = None
    content_length: int          # 字符数（不是字节数）
    content_preview: str
    content_truncated: bool
    content_sha256: str


class Experience(BaseModel):
    """一条原始经历的完整表示（仅详情响应返回）。"""

    id: str
    sequence: int
    recorded_at: str
    occurred_at: str | None = None
    content: str
    source: str | None = None
    context: str | None = None
    outcome: str | None = None
    metadata: str | None = None      # 原样字符串，绝不反序列化
    content_sha256: str


class ExperiencePage(BaseModel):
    """``GET /experiences`` 的响应。"""

    items: list[ExperienceSummary]
    next_cursor: str | None = None
    has_more: bool


class HealthResponse(BaseModel):
    """``GET /health`` 的响应。

    ``db_writable`` 是三态：
      * ``true``  —— 已确证可写（仅在 ``?probe=write`` 且真实写入成功时）
      * ``false`` —— 确证不可写（只读 / 错误）
      * ``null``  —— 无法判断（默认未探测、或数据库正忙）

    契约要求：不得把无法确认的情况伪装成 ``true``，也不伪装成 ``false``。
    """

    status: str
    schema_version: int
    db_path: str
    db_writable: bool | None = None
    db_write_status: str          # writable | readonly | busy | unknown | error


__all__ = [
    "Experience",
    "ExperiencePage",
    "ExperienceSummary",
    "HealthResponse",
    "ObserveRequest",
    "ObserveResponse",
]
