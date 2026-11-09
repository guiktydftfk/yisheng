# 忆生 · Living Memory

> **Phase 1 — Experience Stream（连续经历记录系统）**
> 状态：**已实现 / 已测试（150 项全绿）/ 本机已真实跑通 HTTP / 经独立模型复测 / 已按审查意见修订**
> 实现时间：2026-11-07
> 依据：项目既定的三条架构契约与七条验收要求

---

## 项目简述

**忆生（Living Memory）是一个关于「AI 能不能积累经历」的实验项目。**

它不追求让模型更像人，而是先把**记忆这件事本身**做扎实：
一条经历进来，就被原样记住、永不改写、可被后来的一切稳定引用。

常见的"长期记忆"做法是给模型外挂一个向量库——**本项目认为那不是记忆**：
向量检索能找回相似文本，却说不清"我当时的判断是什么、后来改没改、为什么改"。
所以本项目不从这里开始，而是从一个更笨、也更硬的东西开始：

> **先有不可篡改的原始经历，才谈得上理解、关联、修正与自我。**

`忆生` 是分阶段推进的长期项目，当前处于 **Phase 1（经历记录层）**。
Phase 1 只做记录、不做理解——这是刻意的。

---

## 目标

### 长期目标

探索能否通过持续运行的记忆、学习与认知机制，使 AI 系统具备：

| # | 目标 | 阶段 |
|---|---|---|
| 1 | **持续经历**：不断接收并积累原始经历，而不是每次对话从零开始 | Phase 1 |
| 2 | **动态记忆**：对经历做解释、建立关联，让记忆之间形成结构 | Phase 2–3 |
| 3 | **记忆整合**：压缩、强化、遗忘（可塑记忆），而不是只增不减 | Phase 4 |
| 4 | **信念修正**：新经历与旧判断冲突时能修正自己，而不是覆盖历史 | Phase 5 |
| 5 | **内部认知状态**：有可观察、可影响行为的内部状态 | Phase 6 |
| 6 | **自我模型**：形成对"自己是什么、自己知道什么"的稳定表征 | Phase 6 |
| 7 | **持续学习**：从经历中改变行为，且可评估、可回滚 | Phase 7 |

### 一条贯穿全项目的边界纪律

> **不得把向量检索、知识图谱或自然语言反思直接等同于神经记忆；**
> **不得把功能性内部状态等同于主观意识。**

这条纪律是为了防止两种偷懒：把"能检索"当成"有记忆"，把"能说感受"当成"有感受"。

### 本阶段（Phase 1）的目标

把**原始经历层**做成可信的基础设施，具体四条：

1. **记下来** —— 原始文本、来源、背景、结果、元数据都能写入（`content` 上限 100,000 字符）
2. **记得准** —— 内容原样存储、一个字符都不改；存 SHA-256 可验证未被改动
3. **改不了** —— append-only；业务接口不提供任何修改/删除原始经历的能力
4. **查得回** —— 永久 UUID 引用 + 确定顺序（`sequence`）+ 键集分页；重启后一切不变

对应七条验收要求（见下节），由 **150 项测试**覆盖。

### 非目标（Phase 1 明确不做）

| 不做 | 原因 |
|---|---|
| LLM 调用 | 本阶段不依赖任何模型，纯记录 |
| 语义分析 / 摘要 | 那是 Phase 2 |
| 自动关联 / 向量检索 | 那是 Phase 3 |
| 记忆整合 / 遗忘 | 那是 Phase 4 |
| 信念修正 / 认知状态 | 那是 Phase 5–6 |
| 自我模型 / 持续学习 | 那是 Phase 7 |
| 训练 / 参数更新 | 不属于本阶段 |

> **Phase 1 的价值不在"功能"，在"契约"。**
> 它越无聊，后面的阶段越干净。这些契约只要破一个，后面全是脏数据。

---

## 三条架构契约

### 契约 1 — 经历身份永久不可变

每条原始经历分配一个 **UUID v4**，写入后永不改变，作为全系统引用该经历的统一标识。
未来的解释层、关联、信念都必须通过这个 ID 指向原始经历，**不得覆盖原始经历**。

### 契约 2 — 排序与分页确定性

`id`（UUID，永久身份）与 `sequence`（内部写入序号）**分离**：

| 字段 | 含义 | 生成方 |
|---|---|---|
| `id` | 永久 UUID 身份 | 服务端，一次生成永不改 |
| `sequence` | 内部写入序号，`INTEGER PRIMARY KEY AUTOINCREMENT` | SQLite |
| `recorded_at` | 系统实际记录时间 | **服务端生成，客户端不可指定** |
| `occurred_at` | 经历实际发生时间（可选） | 客户端可传入，**必须带时区** |

列表按 `sequence DESC` 排序，使用 **Keyset Cursor 分页**（不用 offset），返回 `next_cursor`。

### 契约 3 — Phase 1 功能边界

纯粹的原始经历记录系统。不具备理解、语义分析、自动关联、摘要生成、重要性判断、反思、信念修正、认知状态更新。

---

## 七条验收要求与对应的测试

| # | 验收要求 | 实现方式 | 测试 |
|---|---|---|---|
| ① | 经历 ID 永久稳定，可被后续模块引用 | UUID v4 + `id` 唯一约束 | `test_uuid_stable_after_restart`、`test_content_sha256_matches_content` |
| ② | 原始经历保存后不可被普通业务接口修改 | 无 PUT/PATCH/DELETE 端点；仓储层不导出修改方法 | `test_modification_endpoints_forbidden`、`test_repository_exposes_no_update_or_delete`、`test_repository_contains_no_write_sql_besides_insert` |
| ③ | 时间排序和 Cursor 分页具有确定性 | `ORDER BY sequence DESC` + 键集游标 | `test_order_deterministic_same_millisecond`、`test_list_sorted_desc_by_sequence`、`test_cursor_is_opaque_and_stable` |
| ④ | 新记录写入不导致分页重复或遗漏 | 游标用 `sequence <` 而非 offset | `test_cursor_no_dup_no_miss`、`test_insert_during_pagination` |
| ⑤ | 原始经历与衍生数据架构独立 | 只两张表；`experiences` 无任何衍生列 | `test_schema_has_only_allowed_tables`、`test_schema_has_no_cognitive_columns` |
| ⑥ | 库中不存在未经批准的自动解释/关联/认知数据 | 表集合恰好为 `{experiences, schema_version}` | `test_schema_has_only_allowed_tables`、`test_no_cognitive_endpoints`、`test_empty_database_has_no_derived_data` |
| ⑦ | 重启后数据及身份标识不变 | SQLite WAL + AUTOINCREMENT | `test_persist_after_restart`、`test_uuid_stable_after_restart`、`test_restart_persistence_across_connections` |

> **验收 ⑤⑥ 的验证思路**：用 `sqlite_master` 断言用户表集合**恰好等于** `{experiences, schema_version}`。
> 这样**新增一张表就构成一次契约变更，必须显式批准** —— 契约不会在无人察觉时被稀释。

---

## 目录结构

```
D:\LivingMemory\
├── run.py                     # 启动入口（把 src 加入 sys.path）
├── pyproject.toml
├── requirements.txt
├── scripts/
│   ├── verify_all.py          # ★ 一条命令自验（54 项检查）
│   └── scale_test.py          # 规模测试（默认 5000 条，可传参）
├── docs/                      # 本项目文档
│   ├── README.md              # 就是本文件
│   └── 运行说明.md
├── src/yisheng/               # 包名：忆生
│   ├── config.py              # 唯一配置来源（路径/上限/端口/版本）
│   ├── main.py                # FastAPI 装配
│   ├── errors.py              # 统一错误模型
│   ├── db/
│   │   ├── connection.py      # WAL / foreign_keys / busy_timeout
│   │   ├── schema.sql         # 建表 DDL
│   │   ├── migrate.py         # 结构版本与迁移（**零内联 SQL**）
│   │   └── repository.py      # ★ 全包唯一允许出现 SQL 的地方
│   ├── models/experience.py   # Pydantic 请求/响应模型
│   ├── services/experience_service.py  # 业务逻辑 + 游标编解码
│   └── api/
│       ├── deps.py            # 依赖注入（每请求一个连接）
│       └── routes.py          # 4 个端点
├── tests/                     # 150 项测试
│   ├── conftest.py
│   ├── test_observe.py        # 功能
│   ├── test_validation.py     # 边界与输入验证
│   ├── test_persistence.py    # 并发与持久化
│   ├── test_schema_contract.py# 契约 ①–⑦ + 静态结构断言
│   └── test_scenarios.py      # ★ 场景化强化测试（真实对话 / 相反场景 / 新颖场景）
└── data/                      # yisheng.db 生成于此（gitignore）
```

**模块边界铁律**：`repository.py` 之外**不允许出现 SQL 语句**（`SELECT` / `INSERT` / `UPDATE` / `DELETE` / DDL）。
由 `test_sql_only_in_repository_module` 静态扫描全包来保证 —— 这是一条能被机器证明的规则，不是自觉。

> 精确界定：连接层 `connection.py` 仍有 `PRAGMA`（`journal_mode` / `foreign_keys` / `busy_timeout` / 可写性探测），
> 那是**连接与存储配置**，不涉及任何经历数据的读写，因此不在上述禁令范围内。
> 禁令针对的是「业务数据的 SQL 散落在业务代码里」。

---

## API（4 个端点，全部监听 127.0.0.1）

### `POST /observe` — 记录一条经历

```json
{
  "content": "必填，1..100,000 字符（按字符数，非字节）",
  "source": "可选，≤200",
  "context": "可选，≤2000",
  "outcome": "可选，≤2000",
  "occurred_at": "可选，ISO8601，必须带时区",
  "metadata": "可选，**任意字符串**（原样存储，不解析；传对象会被 422 拒绝）"
}
```

> `metadata` 是**原始字符串**，不是 JSON 对象。
> 即使内容形如 `{"a":1}`，服务端也只当普通文本存取 —— 绝不 `json.loads`。

响应 `201`：

```json
{
  "id": "d390a111-e8fe-4819-ad96-cc524fcf2273",
  "sequence": 1,
  "recorded_at": "2026-11-07T13:17:05.403612+00:00",
  "occurred_at": null
}
```

> 不返回完整记录 —— 写入路径保持轻量，读走 GET。

### `GET /experiences?limit=20&cursor=<opaque>` — 列表（**摘要**）

```json
{
  "items": [
    {
      "id": "…",
      "sequence": 1,
      "recorded_at": "…",
      "occurred_at": null,
      "source": "…",
      "content_length": 12345,
      "content_preview": "前 200 个字符…",
      "content_truncated": true,
      "content_sha256": "…"
    }
  ],
  "next_cursor": "opaque-string-or-null",
  "has_more": false
}
```

- 排序：`ORDER BY sequence DESC`
- `limit`：默认 20，范围 1..100
- `cursor`：**不透明字符串**（内部是 base64 的 `s:<sequence>`），客户端不得解析；
  合法范围 `1 .. 2^63-1`，越界返回 **400**（不是 500）
- **列表不返回 `content` 全文**。`content_preview` 是**截断**，不是摘要/概括
  （Phase 1 不生成任何语义摘要）。完整内容请用 `GET /experiences/{id}`

> 为什么改：最坏情况下 `limit=100 × 100,000 字符 = 1,000 万字符`，
> UTF-8 可达 ~40MB/页，并发时会同时占用大量内存与带宽。
> 改为摘要后，实测覆盖 2,000,000 字符的一页响应只有 **18,376 字节**。

### `GET /experiences/{id}` — 详情

- `200` 完整对象（含 `content` 全文）
- `404` 不存在
- `400` id 不是合法 UUID
- **`500 data_integrity_error`** 存储内容与 `content_sha256` 不一致（见下「完整性边界」）

### `GET /health` — 健康检查

```json
{
  "status": "ok",
  "schema_version": 1,
  "db_path": "…",
  "db_writable": null,
  "db_write_status": "unknown"
}
```

`db_writable` 是**三态**，`db_write_status` 是五态：

| 情形 | `db_write_status` | `db_writable` |
|---|---|---|
| 默认（未探测） | `unknown` | `null` |
| `?probe=write` 且真实写入成功 | `writable` | `true` |
| 只读库 / `PRAGMA query_only=1` | `readonly` | `false` |
| 拿不到写锁（别的连接正在写） | `busy` | `null` |
| 其他数据库错误 | `error` | `false` |

- **默认不做任何写入**，因此不会与正常经历写入争抢写锁
- 需要确证可写时才加 `?probe=write`（一次事务内真实写入 + 回滚）
- **`busy` 与 `unknown` 一律返回 `null`** —— 不伪装成 `true`，也不伪装成 `false`

### 明确不实现

```
❌ DELETE /experiences/{id}      → 405
❌ PUT / PATCH /experiences/{id} → 405
❌ 任何搜索、语义查询、关联接口
❌ 任何 summarization / reflection / belief 端点
```

---

## 完整性边界（不要过度声称）

`content_sha256` 只针对 `content` 字段，读取（详情与列表）时都会重新计算并比对。

| 情形 | 能否发现 |
|---|---|
| 只改 `content`，摘要保持旧值 | ✅ 能，返回 `500 data_integrity_error` |
| 改 `metadata` / `source` / `outcome` | ➖ 不告警（哈希只覆盖 content） |
| 同时改 `content` 与 `content_sha256` | ❌ **发现不了**（普通 SHA-256 无法证明未被恶意篡改） |

> **SHA-256 能检测「内容与摘要不一致」，但不能阻止数据库被修改。**
> 本阶段不引入 HMAC、数字签名、密钥管理。
> 「同时改内容与摘要」这一情形由 `test_simultaneous_tamper_is_NOT_detectable` **明确断言为检测不到**，
> 防止以后有人把"检测不一致"说成"完整防篡改"。

---

## 不可修改的保护边界（三层）

| 层 | Phase 1 的保证 | 不保证 |
|---|---|---|
| HTTP API | 只有 4 个端点；`PUT/PATCH/DELETE` → 405 | —— |
| Repository | 不暴露任何 update/delete 方法；业务 SQL 只在 `repository.py` | —— |
| SQLite 文件 | append-only 由应用逻辑保证 | **外部程序直接改 `yisheng.db` 不在保护范围**；本阶段**不加触发器**（会改变数据库对象契约） |

---

## 关键设计决定（及理由）

| 决定 | 理由 |
|---|---|
| `sequence` 用 `INTEGER PRIMARY KEY AUTOINCREMENT` | 保证**已提交**记录用过的序号不被后续提交复用。⚠️ **不保证**回滚序号不复用、**不保证**连续（见下表）。 |
| `id` 与 `sequence` 分离 | `id` 是对外永久身份；`sequence` 是内部排序/分页基准。两者职责不同，不能合并。 |
| `recorded_at` 服务端生成 | 否则"发生时间"会被伪造成"记录时间"。客户端即使传 `recorded_at` 也会被 `extra="forbid"` 直接拒绝。 |
| 游标 base64 编码且不透明 | 只用于隐藏内部格式，**不是加密、不是安全凭证**；客户端可构造合法游标从任意位置读取（这是允许的）。 |
| 多取 1 条判断 `has_more` | 避免额外 `COUNT(*)` 查询。 |
| `metadata` 只当字符串存，不解析不索引 | 一旦解析，就引入了"什么算合法 metadata"的判断 —— **那是认知功能的开始**。 |
| `content_sha256` 存哈希 | 检测"内容与摘要不一致"，配合 append-only 使用；**不等于防篡改**。 |
| `content` 上限 100,000 **字符**（非字节） | 第 86 轮决定。按字符算，中文和英文的"条数"可比。 |
| 列表只返回摘要 | 避免单页 1,000 万字符 / ~40MB 的响应；完整内容走详情。 |
| `synchronous = FULL` | 可靠性优先：已提交即落盘。实测代价：单条提交 ~298 条/秒（NORMAL 为 22,198），但每条仅 +3.4ms，对经历流足够。 |
| 测试用真实文件库，不用内存库 | WAL 是文件库的持久属性；内存库上 `PRAGMA journal_mode=WAL` 会退化成 `memory`，会让 WAL 契约测试变成假绿。 |

### AUTOINCREMENT 的真实保证（实测）

| 说法 | 成立？ | 依据 |
|---|---|---|
| 已提交记录用过的序号不会被后续提交复用 | ✅ | `test_committed_sequence_not_reused_after_delete` |
| 允许出现序号空洞 | ✅ | `test_sequence_gaps_are_allowed` |
| 回滚事务占用的序号**可能被复用** | ✅（即"不保证不复用"） | `test_sequence_may_be_reused_after_rollback` |
| 序号连续 | ❌ 不保证 | `test_sequence_continuity_is_not_guaranteed` |
| `sequence` 是永久身份 | ❌ 不是，永久身份是 UUID | `test_sequence_is_not_identity` |

实测记录（本机 SQLite 3.45.3）：

```
插入 a,b,c        → seq [1,2,3]，sqlite_sequence = 3
BEGIN + 插入      → seq 4
ROLLBACK          → sqlite_sequence 回到 3，该行不存在
再插入            → seq 4      ← 回滚的序号被复用
DELETE seq=3 后插 → seq 5      ← 已提交序号不复用
```


---

## 与原始方案的差异（诚实记录）

| 项 | 原方案 | 实际实现 | 原因 |
|---|---|---|---|
| 表集合断言 | 计划用 `PRAGMA table_list` | 改用 `sqlite_master`（排除 `sqlite_%`） | `PRAGMA table_list` 会额外列出 `sqlite_schema`、`sqlite_temp_schema` 等内部表，无法直接做"集合恰好等于"的断言。语义上 `sqlite_master` 更精确。已在 `test_schema_has_only_allowed_tables` 的 docstring 里写明。 |
| 项目根目录 | 方案写 `D:\LivingMemory\` | 相同（`src/yisheng/`、`data/`、`tests/` 都在根下） | 与方案一致。唯一补充：方案未预见本机根目录还要存放会话归档（`00_`–`05_`），因此项目文档放在 `docs/`，**没有覆盖根目录原有的归档 `README.md`**。 |
| 测试文件划分 | 方案列了 6 个测试文件 | 收敛为 4 个（契约测试合并了 pagination） | 覆盖范围完全等价，减少文件碎片。对照见上表。 |
| `content` 上限 | 方案曾提 1 MiB | 100,000 字符 | 第 86 轮用户决定改回。 |
| "SQL 只在 repository.py" | 方案把这当约定 | **改成事实**，并加静态测试 | 独立复测指出 migrate.py 仍含内联 SQL，该断言当时不成立。已把所有 SQL 收进仓储层，让这条规则可被机器证明（`PRAGMA` 连接配置除外，见目录结构后的说明）。 |
| `db_writable` 检测 | 计划用 `PRAGMA user_version` 读一下 | 事务内真实写入 + 回滚 | 复测指出读操作无法证明可写性；实测 `BEGIN IMMEDIATE` 在 WAL 只读连接上也不报错，故必须真写。 |

---

## 已验证 / 未验证（严格区分）

> **想自己验，不用读这一节。** 跑 `python scripts\verify_all.py`：
> 它会真实起服务、真实发 HTTP、真的重启进程、直接查库，共 54 项检查，一条命令出结论。
> 详见 `docs/运行说明.md` 的「〇、我如何验证？」。

### ✅ 已实现

- 全部 4 个端点、Schema、迁移、仓储层、服务层、错误处理
- `run.py` 启动器（含非回环地址的主动拒绝）

### ✅ 已测试（150 项全部通过）

```
150 passed
```

| 文件 | 覆盖 | 数量 |
|---|---|---|
| `test_observe.py` | 基本写入与查询 | 12 |
| `test_validation.py` | 边界与输入验证 + limit 单点校验 | 27 |
| `test_persistence.py` | 并发写、WAL、可写性、重启持久化 | 8 |
| `test_schema_contract.py` | **七条验收契约** + 静态结构断言 | 33 |
| `test_scenarios.py` | **场景化强化测试**（真实对话 / 相反场景 / 新颖场景） | 22 |
| `test_metadata_raw.py` | **P0-1**：metadata 原始字符串往返（API + 库内逐字一致） | 8 |
| `test_sequence_autoincrement.py` | **P0-2**：AUTOINCREMENT 真实保证（含回滚实测） | 9 |
| `test_integrity.py` | **P0-3**：篡改检测与能力边界（含"检测不到"的如实断言） | 6 |
| `test_health.py` | **P0-4**：五态可写性判断、默认不写、并发下 health | 11 |
| `test_cursor_bounds.py` | **D4 + 分页边界**：2^63 边界、limit 边界、单页负载 | 14 |

**测试数量变化是复测、场景化与本次修订的结果，不是凑数：**

| 版本 | 数量 | 触发原因 |
|---|---|---|
| 初版 | 69 | —— |
| +3 | 72 | 复测指出「SQL 只在 `repository.py`」不成立 → 重构 + 3 条静态契约测试 |
| +5 | 77 | 复测指出 `db_writable` 检测不可靠、`limit` 校验重复且可能 500 |
| +22 | 99 | 增加场景化强化测试 `test_scenarios.py` |
| +51 | **150** | **按 GPT 审查意见修订**：P0-1..P0-4 + D4 + 分页边界（明细见 `docs/修订说明-v2.md`） |

### 测试分层标记

为区分"哪一层在证明什么"，测试带 pytest marker：

| marker | 含义 | 数量 |
|---|---|---|
| `api` | HTTP 层保护与行为 | 94 |
| `sqlite` | SQLite 真实行为（AUTOINCREMENT / WAL / 并发） | 18 |
| `integrity` | 数据完整性检测 | 6 |
| `contract` | 七条验收 | 33 |
| `repository` | 仓储层约束 | 3 |
| `static` | 静态源码检查（**补充证据，不替代运行时测试**） | 4 |

```powershell
python -m pytest -m api -q          # 只跑某一层
python -m pytest -m "not static" -q # 运行时证据
```

> 一个测试可同时带多个 marker（如既属 `contract` 又属 `static`）。


#### 场景化强化测试（`test_scenarios.py`，22 项）

**来历（如实记录）：** 有反馈认为总纲里应包含「现实场景对话 / 新颖场景 / 与上一轮相反的场景」这类测试。
核对归档后确认**总纲原文没有这段内容**——总纲关于测试只有一句「设计测试」，任务书列的是
「正常写入、多条写入、列表查询、单条查询、不存在 ID、空内容、超长内容、重启后持久化、重复请求、
无效输入不破坏数据」，全是数据层面的。因此这组测试**不是照抄某份文档，而是按需求意图做的忠实翻译**。

**翻译的关键点：** Phase 1 是纯记录器，它不能"理解"场景。所以场景测试的含义不是"看它怎么理解"，而是反过来：

> **看它会不会在真实、复杂、矛盾的输入面前，偷偷开始"理解"。**

四组场景：

| 组 | 内容 | 在证什么 |
|---|---|---|
| **A. 真实对话** | 整段多轮对话逐字保存；带 source/context/outcome/metadata 的完整记录；十轮对话**乱序补录**；同一段对话重复提交 | 原始性：换行/中文标点不被规范化；排序按 `sequence` 而非 `occurred_at`；不做去重 |
| **B. 与上一轮相反** | 「我怕黑」/「我不怕黑」并存；先记结论 A 再记「B 推翻 A」；反复改主意 5 次；连续 20 条"修正"后早期记录逐字节不变；写满真实对话后表结构不得多出任何东西 | **不可调和**：两条都逐字保留、谁也不覆盖谁；`content_sha256` 不变；无概念/关联/信念结构生成 |
| **C. 新颖场景** | 纯 emoji；Markdown 代码块 + JSON；只差一字的近重复；零宽空格与 NBSP；CRLF/制表符/多空行；10 万字符 emoji+中文；多语言双向文本；只含零宽空格的内容 | 不做内容规范化/去重/摘要；`content` 上限按字符算的语义边界 |
| **D. 现实时间场景** | 补记多年前的事（`occurred_at` 在过去）；预记未来的事（`occurred_at` 在未来） | 时间双轨互不干扰；Phase 1 不判断时间是否"合理" |
| **E. 端到端加固** | 写 → 改（405）→ 删（405）→ 读回完全一致；20 线程并发写入互相矛盾的内容 | 不可篡改 + 并发不丢不覆盖 |

**最重要的一条是 B1：**

```python
pos = observe(client, content="我怕黑。")
neg = observe(client, content="我不怕黑。")
# 断言：两条都逐字存在，content_sha256 各自正确，谁也不覆盖谁
```

**一个"会思考"的系统会想调和它们；Phase 1 必须不会。**
如果这里失败，说明认知功能从 Phase 1 泄漏进来了——那比任何性能问题都严重。

### ✅ 本机真实 runs（不只是单元测试）

- `uvicorn` 真实启动于 `127.0.0.1:8010`
- 用 Node `fetch` 走真实 HTTP：`health`/`observe`/`get`/`list`/`delete(405)`/`bad uuid(400)`/`openapi` 全部符合预期
- `netstat` 确认监听项为 `127.0.0.1:8010`，**未绑定 0.0.0.0**
- **关掉进程 → 重新启动 → 按原 UUID 取回同一条记录**，`sequence`、`id`、`content` 均未变

### ✅ 规模测试（更大数据量，`scripts/scale_test.py`）

单元测试只用几十条数据。为验证契约在真实体量下不退化，另做了两轮规模测试（各自独立数据库，不碰 `data/yisheng.db`）：

| 指标 | 5,000 条 | 20,000 条 |
|---|---|---|
| 写入速率 | 2,746 条/秒 | 1,278 条/秒 |
| 全量翻页页数（limit=97） | 52 页 | 207 页 |
| 翻页总耗时 | 0.05 s | 0.69 s |
| 单页延迟（中位 / 最大） | 0.84 ms / 1.99 ms | 2.78 ms / 9.50 ms |
| 条数正确（无遗漏） | ✅ | ✅ |
| 无重复 | ✅ | ✅ |
| 严格按 `sequence` 降序 | ✅ | ✅ |
| 翻页中途插入 500 条：无重叠 | ✅ | ✅ |
| 新记录未混入旧区间 | ✅ | ✅ |
| 100,000 字符记录 × 20 条：读回长度正确 | ✅ | ✅ |
| 抽样 500 条 `content_sha256` 一致 | ✅ | ✅ |
| 表集合恰为 `{experiences, schema_version}` | ✅ | ✅ |

**结论：** 契约 ③（排序确定性）与契约 ④（翻页不重不漏）在 20,000 条、207 页、且翻页中途插入 500 条的情况下依然成立。
单页延迟随数据量近似对数增长（0.84 ms → 2.78 ms），符合 `sequence` 索引下 Keyset 分页的预期。

> 注意：列表返回**完整 content** 是第 86 轮的决定。因此当一页里全是 100,000 字符的大记录时，单页响应可达 2,000,000 字符（实测 43–53 ms）。这是已知的、被接受的行为，不是缺陷。

### ✅ 更大参数量模型独立复测

为防止「作者自测自己」形成盲区，邀请了**独立的大参数量模型**对实现做对抗性复测。复测者不知道本文档此前写了什么，任务是**尽力找出违反契约之处**。

> ⚠️ **本节描述的是「修订前」的状态。**
> 复测之后又经历了按 GPT 审查意见的修订（P0-1..P0-4 / D1..D4），
> 修复清单与前后对照见 `docs/修订说明-v2.md`。本节的结论仅作为历史记录保留。
> 其中「metadata 嵌套透传」等描述已不适用于当前实现。

#### 参测模型与可用性

| 模型 | 参数量档位 | 路由状态 | 是否完成复测 |
|---|---|---|---|
| **Qwen3.8 Max** | Max 级 | ✅ 可用 | ✅ **完成**（本节的结论来源） |
| **GLM-5.3** | 完整版（非 flash） | ✅ 可用 | ❌ 长任务运行中断（短探测可通） |
| **DeepSeek-V4-Pro** | Pro 级 | ❌ **路由故障**（短探测也失败，与 effort 设置无关） | ❌ 未能启动 |

> 诚实记录：**三个候选里只有 Qwen3.8 Max 跑完了复测。**
> V4-Pro 的路由在本会话中不可用（两次探针均 `subagent run failed`），GLM-5.3 在长代理任务中提前中断。
> 因此下面不代表多模型共识，而是**同一个外部模型的两轮独立复测**。

#### 两轮复测的结果

| | 第一轮（体检式） | 第二轮（对抗式，更严） |
|---|---|---|
| 结论 | **通过** —— 没找到违反契约的证据 | **有条件通过** —— 契约全符合，但发现 3 处实现缺陷 |
| 自建检查点 | 40 个，40/40 | 30+ 项攻击角度逐一实测 |
| 当时 pytest | 69 passed | 72 passed |
| 有无产出修改 | 有（SQL 位置问题） | 有（3 处缺陷 + 文档偏差） |

**第一轮覆盖的攻击角度（20 个）**：`recorded_at` 注入、`occurred_at` 四种时区变体（无时区/`Z`/`+08:00`/非法）、
`limit` 边界（0 / -1 / 1 / 100 / 101）、空库翻页、仅 1 条时 `has_more`、游标伪造、游标重复使用、
翻页中途插入、相邻页 ID 交集、`content` 上限（100,000 / 100,001）、`metadata` 嵌套透传、
`sqlite_master` 表集合、触发器/视图、OpenAPI 路径集合、重启后 ID 稳定性、UUID 版本。
七条验收逐条给出实测证据，全部符合。

**第二轮额外做的**：20 线程 × 10 条并发 UUID 唯一性（200 条全唯一）、同毫秒 50 条写入排序稳定性、
超范围游标 `s:99999`、非法输入（`null`/`int`/`array` 作 content，`string`/`array` 作 metadata）不产生 500、
错误响应格式统一性、`repository.py` 是否存在 SQL 注入风险。

复现材料：`.audit_qwen/adversarial_audit.py`、`.audit_qwen/audit_report.md`

#### 复测让我改掉的 4 个真问题

**① SQL 位置声明不成立**（第一轮标记，第二轮复核）

`migrate.py` 里有内联 SQL（`SELECT ... FROM schema_version`、`INSERT INTO schema_version`），
而文档写的是「`repository.py` 之外**不允许出现 SQL**」——无条件的措辞，所以这句当时不成立。
（第一轮复测自己把它判为"误报"，理由是它属基础设施层。**我不接受这个豁免**：文档没写例外。）

→ 已把 `schema_version` 的读写与 `sqlite_master` 查询全部移入 `repository.py`；
`migrate.py` 现在零内联 SQL；新增静态测试锁死。

**② `db_writable` 检测不可靠**（第二轮，`[次要]`，但它是对的）

原实现用 `conn.execute("PRAGMA user_version")` 判断可写——**那是读操作，只读库上也会成功**，永远返回 true。

我第一版"修法"用 `BEGIN IMMEDIATE`，实测发现**在 WAL 只读连接上也不报错**，同样不可靠。
最终实现：在事务里做一次**真实写入**（`PRAGMA user_version = 当前值+1`）再 `ROLLBACK`。

实测验证：

```
只读连接 probe_writable = False
可写连接 probe_writable = True | user_version: 0   ← 回滚后完全恢复，未留痕迹
```

并确认真实写入后用 `ROLLBACK` **不消耗 `sequence`**（下一条真实记录仍是 `sequence=1`）。

**③ `limit` 校验重复，且服务层会冒 500**（第二轮，`[建议]`）

路由层与服务层各做一次范围检查；若路由那处被移除，服务层的 `ValueError` 没有处理器，会变成 **500** 而非 422。

→ 校验收敛到服务层单点（`validate_limit`），抛带全局处理器的 `InvalidLimitError` → **422**；
新增静态测试 `test_limit_validation_has_single_source` 确保范围比较只出现在一处。

**④ 装饰器 `status_code=405` 冗余**（第二轮，`[建议]`）

`@router.delete(..., status_code=405)` 设的是**成功**响应码，而函数体永远 raise，该参数从不生效。
→ 已移除。

> 结论：**复测没有推翻任何一条架构契约，但推翻了一句文档措辞和 3 处实现细节。**
> 这比"复测通过"本身更有价值——尤其是 ②，它证明**作者自测容易把"读成功"当成"写成功"**。

#### 复测中它说错的两处（我逐条核实并纠正）

| 它的说法 | 实测事实 |
|---|---|
| 「现有 pytest 套件 **73** 条测试全部通过」 | 复测当时是 **69**（第二轮读到 72）。它的 73 与任何时点都对不上。 |
| 「SQLite WAL 模式下的读一致性（当前使用**默认 journal 模式**）」 | **错。** `PRAGMA journal_mode` 实测返回 `wal`，且这是**库的持久属性**——直接用裸 `sqlite3.connect()` 也是 `wal`。它的观察应来自自己某次未经 `connect()` 封装的连接。 |

#### 复测未覆盖、但本项目另行覆盖的部分

| 它列入"未覆盖" | 本项目的覆盖 |
|---|---|
| 并发写入下序列号单调性 | `test_concurrent_writes_no_duplicate_sequence`：10 线程 × 5 条，无重复无空洞；复测自己也做了 20 线程 × 10 条 UUID 唯一性 |
| 真实 TCP 层 HTTP 行为（它用 ASGI TestClient） | 实机 `uvicorn` + Node `fetch` 走真实 socket，见「本机真实 runs」 |
| 大库性能 | 20,000 条 / 207 页规模测试，见上 |

### ⚠️ 未验证（必须由那台目标机器确认）

| 项 | 为什么没验证 |
|---|---|
| 目标机 Windows 路径 `D:\LivingMemory\` 的行为 | 本机是同一台机器，路径相同；但目标机环境描述与实测不符，见下 |
| Python 3.11/3.12 **完整版** | 本机用的是 3.12.8 **embeddable** 版，可能缺标准库模块 |
| 完整 venv 部署流程 | 本机未建 venv（直接用 embeddable 解释器） |
| 长时间运行 / 百万级数据 | 已测到 20,000 条；未做百万级与长时运行 |
| 极端时间值（公元 1 年、9999 年）的存储与排序 | 尚未补测（复测与本轮修订均未覆盖） |
| 超长 `metadata`（>1MB）的存储与内存影响 | metadata 现为无长度上限的字符串，该上限行为未测 |
| 断电/OS 崩溃下的 `synchronous=FULL` 实测 | 只做了逻辑判断与吞吐实测，**没有真的断电验证** |
| 多模型复测共识 | 只有 Qwen3.8 Max 跑完；GLM-5.3 中断、V4-Pro 路由故障 |
| 目标机硬件（i9-13900HX / RTX 4060 / 48GB） | 本机实测为 16 核 / 32GB，**与任务书描述不符**；验收结果归属需澄清 |

> **"在本机通过"不等于"在目标机通过"。**
> 但 SQLite 与 FastAPI 的核心行为是平台无关的，预期差异很小。
> 真正的验证者是在目标机上跑完测试的人。

---

## 下一步（Phase 2 之前）

1. 在目标机部署并跑 `docs/运行说明.md` 的验收清单，确认 **150 项全绿** + `acceptance_check.py` 全过
2. 澄清目标机是哪台（硬件描述与实测不符）
3. 补测两个空白：极端时间值、超大 `metadata`
4. 只有在 Phase 1 契约全部在目标机通过后，才进入 Phase 2（经历解释）

---

## 一句话

**Phase 1 不聪明。它的价值在于：三个月后，那些原始经历还在那里，一条没变，ID 一个没错。**
