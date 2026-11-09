"""Phase 1 的四个端点。

明确**不实现**（任何加进来的都是契约变更）：
  ❌ DELETE /experiences/{id}
  ❌ PUT / PATCH /experiences/{id}
  ❌ 任何搜索、语义查询、关联接口
  ❌ 任何 summarization / reflection / belief 端点

端点数量固定为 4。`/health` 的 `probe` 是**查询参数**，不是新端点。
"""

from __future__ import annotations

import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import config
from ..models import (
    Experience,
    ExperiencePage,
    HealthResponse,
    ObserveRequest,
    ObserveResponse,
)
from ..services import experience_service
from .deps import get_conn

router = APIRouter()


@router.post("/observe", response_model=ObserveResponse, status_code=201)
def observe(payload: ObserveRequest, conn: sqlite3.Connection = Depends(get_conn)) -> ObserveResponse:
    """记录一条原始经历。

    ``metadata`` 按**原始字符串**接收与存储：不解析、不校验内部结构。
    """
    result = experience_service.record_experience(
        conn,
        content=payload.content,
        source=payload.source,
        context=payload.context,
        outcome=payload.outcome,
        occurred_at=payload.occurred_at,  # 已由 validator 归一化为 UTC 字符串，或未带时区的 datetime
        metadata=payload.metadata,        # 原样字符串，不做任何序列化
    )
    return ObserveResponse(**result)


@router.get("/experiences", response_model=ExperiencePage)
def list_experiences(
    limit: int = Query(config.DEFAULT_PAGE_LIMIT),
    cursor: str | None = Query(None),
    conn: sqlite3.Connection = Depends(get_conn),
) -> ExperiencePage:
    """按 ``sequence DESC`` 键集分页列出经历**摘要**。

    列表不返回 ``content`` 全文，只返回 ``content_length`` / ``content_preview`` /
    ``content_truncated``。完整内容请用 ``GET /experiences/{id}``。

    范围校验只在服务层做一处（``validate_limit``），失败映射为 422。
    """
    page = experience_service.list_experiences(conn, limit=limit, cursor=cursor)
    return ExperiencePage(**page)


@router.get("/experiences/{experience_id}", response_model=Experience)
def get_experience(experience_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> Experience:
    """按永久 ID 取单条完整经历（含 content 全文），并校验数据完整性。"""
    try:
        uuid.UUID(experience_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="id 不是合法的 UUID") from exc

    row = experience_service.get_experience(conn, experience_id)
    if row is None:
        raise HTTPException(status_code=404, detail="经历不存在")
    return Experience(**row)


@router.get("/health", response_model=HealthResponse)
def health(
    probe: str | None = Query(None, description="传 'write' 时才执行一次真实写入探测"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> HealthResponse:
    """健康检查。不含任何认知功能。

    * 默认：**不做任何写入**，``db_writable`` 通常为 ``null``（无法判断）。
    * ``?probe=write``：做一次事务内真实写入并回滚，给出确证结论。

    为什么不默认探测：真实写入会与正常经历写入争抢写锁，且默认频繁写入没有必要。
    """
    probe_write = (probe or "").lower() == "write"
    return HealthResponse(**experience_service.health(conn, probe_write=probe_write))


# 显式拒绝修改类动作：语义上给出 405，而不是让路由落到 404 或 500。
# 注意：这只保护**业务接口**；外部程序直接改 SQLite 文件不在 Phase 1 保护范围内。
@router.delete("/experiences/{experience_id}")
@router.put("/experiences/{experience_id}")
@router.patch("/experiences/{experience_id}")
def _forbidden_modification(experience_id: str) -> None:  # noqa: ARG001
    raise HTTPException(
        status_code=405,
        detail="Phase 1 是 append-only 记录系统：原始经历不可修改、不可删除。",
    )


__all__ = ["router"]
