-- 忆生 (Living Memory) — Phase 1 Schema
-- ---------------------------------------------------------------------------
-- 契约自证：本文件中只允许出现两张表。
--   1. schema_version —— 数据库结构版本
--   2. experiences    —— 原始经历（append-only，不可修改）
-- 任何第三张表的加入都构成一次契约变更，必须显式批准。
-- tests/test_schema_contract.py 用 PRAGMA table_list 断言这一点。
-- ---------------------------------------------------------------------------

-- 契约 3：内部序号。AUTOINCREMENT 的真实保证（勿夸大）：
--   ✅ 已提交记录用过的序号，后续提交不会重新使用
--   ✅ 允许出现序号空洞
--   ❌ 回滚事务占用的序号可能被复用（sqlite_sequence 随事务回滚而恢复）
--   ❌ 不保证序号连续
-- 因此 sequence 只承担内部排序与分页职责；永久身份由 id(UUID v4) 承担。
-- 另注：本表会令 SQLite 自动创建内部表 sqlite_sequence，它不属于应用表集合。
CREATE TABLE IF NOT EXISTS experiences (
    sequence       INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 契约 1：永久身份。UUID v4，写入后永不改变，全系统引用原始经历的统一标识。
    id             TEXT    NOT NULL UNIQUE,

    -- 契约 4：时间双轨（均为 UTC ISO8601，格式 YYYY-MM-DDTHH:MM:SS.ffffff+00:00）
    recorded_at    TEXT    NOT NULL,   -- 系统写入时刻；服务端生成，客户端不可指定
    occurred_at    TEXT,               -- 经历实际发生时刻；可选，客户端须带时区

    -- 原始内容（原样存储，不解释、不摘要）
    content        TEXT    NOT NULL,
    source         TEXT,
    context        TEXT,
    outcome        TEXT,

    -- 客户端提交的**原始字符串**：服务端不解析、不校验内部结构、不建索引。
    -- 即使内容看起来像 JSON，也只当普通文本存取，绝不 json.loads。
    metadata       TEXT,

    -- 完整性：仅针对 content 字段的 SHA-256。
    -- 能检测「内容与摘要不一致」，但不能阻止数据库被修改；
    -- 若攻击者同时改写 content 与 content_sha256，普通 SHA-256 无法证明未被篡改。
    content_sha256 TEXT    NOT NULL,

    CHECK (length(content) > 0),
    CHECK (recorded_at IS NOT NULL)
);

-- 契约 3：列表查询按 sequence DESC 排序，键集游标翻页。
CREATE INDEX IF NOT EXISTS idx_exp_sequence_desc
    ON experiences (sequence DESC);

-- 便于按记录时间做人工排查（不改变排序契约）。
CREATE INDEX IF NOT EXISTS idx_exp_recorded_at
    ON experiences (recorded_at);

-- ---------------------------------------------------------------------------
-- 结构版本
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL PRIMARY KEY,
    applied_at TEXT    NOT NULL
);
