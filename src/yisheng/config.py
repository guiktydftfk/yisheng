"""忆生 (Living Memory) — 单一配置来源。

本文件是整个包里唯一允许出现「魔数」的地方：路径、上限、端口、版本都在这里，
其它模块一律 import，不得自行定义。
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# 项目定位
# --------------------------------------------------------------------------

PACKAGE_NAME = "yisheng"
APP_NAME = "忆生 (Living Memory) — Phase 1"
SCHEMA_VERSION = 1

# 项目根目录：本文件位于 <root>/src/yisheng/config.py
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

DATA_DIR: Path = Path(os.environ.get("YISHENG_DATA_DIR", PROJECT_ROOT / "data"))
DB_FILENAME = "yisheng.db"
DB_PATH: Path = DATA_DIR / DB_FILENAME
SCHEMA_SQL_PATH: Path = Path(__file__).resolve().parent / "db" / "schema.sql"

# --------------------------------------------------------------------------
# 网络：默认只监听回环地址（总纲要求，不得暴露公网）
# --------------------------------------------------------------------------

HOST = "127.0.0.1"
PORT = int(os.environ.get("YISHENG_PORT", "8000"))

# --------------------------------------------------------------------------
# 契约常量
# --------------------------------------------------------------------------

# 契约 4：content 上限。按**字符数**（不是字节数）—— 第 86 轮明确决定。
MAX_CONTENT_CHARS = 100_000
MAX_SOURCE_CHARS = 200
MAX_CONTEXT_CHARS = 2_000
MAX_OUTCOME_CHARS = 2_000

# 契约 3 / 分页
DEFAULT_PAGE_LIMIT = 20
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 100

# 列表返回轻量字段（不返回 content 全文）：
# 完整 content 只能通过 GET /experiences/{id} 获取。
# 为什么：最坏情况下单页 limit=100 × 100,000 字符 = 1,000 万字符（UTF-8 可达 ~40MB）。
CONTENT_PREVIEW_CHARS = 200

# 游标编码前缀。客户端视为不透明字符串，不得解析。
# 注意：Base64 只用于隐藏内部格式，**不是加密**，也不是安全凭证。
CURSOR_PREFIX = "s:"

# SQLite INTEGER 上限。游标序号超过它会造成 Python int → SQLite INTEGER 溢出，
# 必须在解码阶段就拦截成 400，而不是让它冒泡成 500。
SQLITE_MAX_INT = 2**63 - 1

# SQLite
SQLITE_BUSY_TIMEOUT_MS = 5_000

# 持久性策略（D1 决策：可靠性优先）。
#   NORMAL：应用崩溃安全；OS 崩溃/断电可能丢失最近已提交事务（库不损坏）。
#   FULL  ：每次提交 fsync WAL，已提交即落盘。
# 实测代价（WAL，每条一个事务）：NORMAL ~22,198 条/秒，FULL ~298 条/秒（约 +3.4ms/条）。
# synchronous 只影响写操作，因此读连接设 FULL 无额外成本 —— 全局 FULL 即可。
SYNCHRONOUS_MODE = "FULL"

# 唯一被允许存在的「应用自定义表」。契约测试用它断言「新增表即契约变更」。
# 注意：物理库里还会存在 SQLite 内部表 sqlite_sequence（AUTOINCREMENT 自动创建），
# 它不属于应用表集合，断言时必须排除 sqlite_% 前缀。
ALLOWED_TABLES: frozenset[str] = frozenset({"experiences", "schema_version"})

# AUTOINCREMENT 的真实保证（勿夸大）：
#   ✅ 已提交记录用过的序号，后续提交不会重新使用
#   ✅ 允许出现序号空洞
#   ❌ 回滚事务占用的序号可能被复用（sqlite_sequence 随回滚恢复）
#   ❌ 不保证序号连续
# sequence 只承担内部排序与分页职责；永久身份由 id(UUID v4) 承担。
SEQUENCE_GUARANTEE_NOTE = "committed-not-reused; gaps-allowed; rollback-may-reuse"


# 强制 UTC 输出格式（契约 4）
UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"
