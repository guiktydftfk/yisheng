#!/usr/bin/env python
"""生成「交叉验证包」——可直接复制给 GPT 的自包含审阅文档。

产出两份：
    docs/交叉验证-给GPT.md         主文（先发这份）
    docs/交叉验证-附录-完整测试.md   附录（GPT 索要测试全文时再发）

为什么要生成而不是手写：
    * 源码一改，手抄的审阅包就过期，审阅者会对着旧代码下结论；
    * 这个脚本保证「审阅包里的代码 = 仓库里的代码」。

用法：
    python scripts/make_review_packet.py
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_MAIN = ROOT / "docs" / "交叉验证-给GPT.md"
OUT_APPENDIX = ROOT / "docs" / "交叉验证-附录-完整测试.md"
OUT_COMPACT = ROOT / "docs" / "交叉验证-精简版.md"

# 精简版只放判定契约最关键的几个文件
COMPACT_FILES = [
    "src/yisheng/db/schema.sql",
    "src/yisheng/db/repository.py",
    "src/yisheng/db/connection.py",
    "src/yisheng/services/experience_service.py",
    "src/yisheng/api/routes.py",
]

COMPACT_EXCERPTS = [
    ("tests/test_schema_contract.py", "test_schema_has_only_allowed_tables"),
    ("tests/test_schema_contract.py", "test_cursor_no_dup_no_miss"),
]

SOURCE_FILES = [
    "src/yisheng/config.py",
    "src/yisheng/db/schema.sql",
    "src/yisheng/db/connection.py",
    "src/yisheng/db/migrate.py",
    "src/yisheng/db/repository.py",
    "src/yisheng/models/experience.py",
    "src/yisheng/errors.py",
    "src/yisheng/services/experience_service.py",
    "src/yisheng/api/deps.py",
    "src/yisheng/api/routes.py",
    "src/yisheng/main.py",
    "run.py",
]

ALL_TEST_FILES = [
    "tests/test_observe.py",
    "tests/test_validation.py",
    "tests/test_metadata_raw.py",
    "tests/test_persistence.py",
    "tests/test_sequence_autoincrement.py",
    "tests/test_integrity.py",
    "tests/test_health.py",
    "tests/test_cursor_bounds.py",
    "tests/test_schema_contract.py",
    "tests/test_scenarios.py",
]

# 主文里内嵌的关键测试（用来说明「断言到底有多硬」）
KEY_EXCERPTS = [
    ("tests/test_schema_contract.py", "test_schema_has_only_allowed_tables"),
    ("tests/test_schema_contract.py", "test_cursor_no_dup_no_miss"),
    ("tests/test_schema_contract.py", "test_insert_during_pagination"),
    ("tests/test_schema_contract.py", "test_uuid_stable_after_restart"),
    ("tests/test_schema_contract.py", "test_sql_only_in_repository_module"),
    ("tests/test_persistence.py", "test_concurrent_writes_no_duplicate_sequence"),
    ("tests/test_scenarios.py", "test_B1_contradictory_experiences_both_kept_verbatim"),
    ("tests/test_metadata_raw.py", "test_metadata_db_value_equals_submitted"),
    ("tests/test_integrity.py", "test_simultaneous_tamper_is_NOT_detectable"),
    ("tests/test_health.py", "test_busy_is_not_reported_as_writable"),
    ("tests/test_sequence_autoincrement.py", "test_sequence_may_be_reused_after_rollback"),
    ("tests/test_cursor_bounds.py", "test_cursor_over_sqlite_max_returns_400"),
]

LANG = {".py": "python", ".sql": "sql"}


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def fence(rel: str) -> str:
    lang = LANG.get(Path(rel).suffix, "")
    return f"````{lang}\n# ---- {rel} ----\n{read(rel).rstrip()}\n````\n"


def test_names(rel: str) -> list[str]:
    return re.findall(r"^def (test_\w+)", read(rel), re.MULTILINE)


def extract_func(rel: str, name: str) -> str | None:
    lines = read(rel).splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith(("def ", "class ", "@")):
            end = j
            break
    return "\n".join(lines[start:end]).rstrip()


# ---------------------------------------------------------------------------

HEADER = """# 交叉验证请求：忆生（Living Memory）Phase 1 实现审查

> **给 GPT 的说明**
> 这是一个独立开发的 Python 项目的第一阶段实现。请你做**严格的独立审查**，不要写鼓励性评语。
> 我会把你的意见当作正式设计评审处理，所以**宁可尖锐，不要客套**。
> 你看不到我的磁盘，本文件已自带必要材料。若判断材料不足，请在最后明确列出要我补什么。

---

## 一、你的角色与审查纪律

你是**独立技术审稿人**，专长：数据库设计、API 契约、并发正确性、测试有效性（防止"假绿"）。

纪律：

1. **只依据本文件材料下结论。** 材料没写的，写「材料不足，无法判断」，不要脑补。
2. **区分「契约要求」与「我的实现选择」。** 契约是硬约束；实现选择可以被你质疑和替代。
3. **不要复述我已有的结论来表示赞同。** 我要的是找反例。
4. 每个问题给出：位置（文件/函数）→ 证据（代码）→ 后果（违反哪条契约）→ 最小修复建议。
5. 某项检查即使通过，也用一句话说明**你凭什么判断它通过**，避免"看起来没问题"式放行。

---

## 二、项目背景

**名称**：忆生（Living Memory）　**阶段**：Phase 1 — Experience Stream（连续经历记录系统）

长期目标：探索能否通过持续运行的记忆、学习与认知机制，使 AI 积累经历、改变知识间联系、
修正自身判断，并逐渐形成相对稳定的内部认知结构。

**但 Phase 1 刻意只做一件事：把原始经历原样、不可改地记下来。**

依据的判断是：

> **Phase 1 的价值不在"功能"，在"契约"。它越无聊，Phase 2 越干净。**
> 七个契约只要破一个，后面全是脏数据。

因此 Phase 1 明确**不实现**任何认知色彩功能：不解释、不摘要、不关联、不检索、不判断重要性、
不反思、不修正信念。那些属于 Phase 2–7。

技术栈：Python 3.12 + SQLite（WAL）+ FastAPI，仅监听 127.0.0.1，不依赖任何 LLM。
代码规模：约 1,100 行实现 + 99 项测试。

---

## 三、审查基准：三条架构契约（硬约束）

### 契约 1 — 经历身份永久不可变
每条原始经历分配 **UUID v4**，写入后永不改变，作为全系统引用该经历的统一标识。
未来的解释层、关联、信念都必须通过这个 ID 指向原始经历，**不得覆盖原始经历**。

### 契约 2 — 排序与分页确定性
- `id`（UUID，对外永久身份）与 `sequence`（内部写入序号）**分离**
- `sequence` 必须是 `INTEGER PRIMARY KEY AUTOINCREMENT`（SQLite 中唯一能保证"单调递增、不复用、回滚不回退"的机制）
- `recorded_at`：**服务端生成，客户端不可指定**
- `occurred_at`：客户端可传，但**必须带时区**
- 列表按 `sequence DESC` 排序，使用 **Keyset Cursor 分页**（不用 offset），返回 `next_cursor`

### 契约 3 — Phase 1 功能边界
纯原始经历记录系统。不具备理解、语义分析、自动关联、摘要生成、重要性判断、反思、信念修正、认知状态更新。

---

## 四、审查基准：七条验收要求

| # | 要求 |
|---|---|
| ① | 原始经历 ID 永久稳定，任何后续模块都可以引用 |
| ② | 原始经历保存后不可被普通业务接口修改 |
| ③ | 时间排序和 Cursor 分页具有确定性 |
| ④ | 新记录写入不会导致已有分页产生重复或遗漏 |
| ⑤ | 原始经历与未来衍生数据在架构上保持独立 |
| ⑥ | 数据库中不存在未经需求批准的自动解释、关联或认知数据 |
| ⑦ | 程序重启后，数据及其身份标识保持不变 |

### 其他明确约定

| 项 | 约定 |
|---|---|
| 端点 | 恰好 4 个：`POST /observe`、`GET /experiences`、`GET /experiences/{id}`、`GET /health` |
| `content` 上限 | **100,000 字符**（不是字节） |
| `metadata` | 只当字符串存，**不解析、不校验内部结构、不建索引** |
| `content_sha256` | 存内容哈希，用于防篡改 |
| 监听地址 | 只允许 `127.0.0.1` |
| `occurred_at` 无时区 | 必须返回 **400**（不是 422） |
| 表集合 | 必须**恰好**是 `{experiences, schema_version}` —— 新增表即契约变更 |
| SQL 位置 | 业务数据 SQL 只允许出现在 `db/repository.py` |

---
"""

SRC_FULL_INTRO = """
## 五、核心源码（完整，未删改）

以这份代码为准，不要以我的描述为准。
"""

SRC_COMPACT_INTRO = """
## 五、核心源码（节选：判定契约最关键的 5 个文件）

为控制篇幅，只保留对判定契约最关键的部分。未包含的是 `main.py`（应用装配）、
`errors.py`（错误处理映射）、`migrate.py`（结构版本迁移）、`api/deps.py`（依赖注入）、
`run.py`（启动入口）—— 这几个逻辑简单，需要时可索取。
"""

AFTER_SOURCE = """
---

## 六、测试现状

测试共 **99 项**，分五个文件：

| 文件 | 覆盖 | 数量 |
|---|---|---|
| `test_observe.py` | 基本写入与查询 | 11 |
| `test_validation.py` | 边界与输入验证 | 26 |
| `test_persistence.py` | 并发写、WAL、可写性探测、重启持久化 | 7 |
| `test_schema_contract.py` | 七条验收契约 + 静态结构断言 | 33 |
| `test_scenarios.py` | 场景化：真实对话 / 相反场景 / 新颖场景 | 22 |

运行方式：真实文件库（非内存库），因为 WAL 只在文件库上生效；
每个测试独立临时库，测试后清理。

### 关键测试的实现（我特意挑出"最硬"的几条给你判断断言强度）

下面这些是主文内嵌的关键断言。完整测试代码见附录（我可以在你索要时提供）。

"""

AFTER_EXCERPTS = """
### 为什么这些测试要这么写（供你质疑）

- **用真实文件库而非内存库**：内存库上 `PRAGMA journal_mode=WAL` 会退化成 `memory`，
  会让 WAL 契约测试变成假绿。
- **表集合用 `sqlite_master` 而不是 `PRAGMA table_list`**：后者会额外列出
  `sqlite_schema`、`sqlite_temp_schema` 等内部表，无法做"集合恰好等于"的断言。
- **翻页测试在翻页中途真的插入新记录**：只在静态数据上翻页是测不出"不重不漏"的。
- **并发测试用 10 线程 × 5 条**，断言 `sequence` 无重复且无空洞。

---

## 七、项目方自己的结论（**请重点挑战**）

| # | 我的声称 | 你要判断的 |
|---|---|---|
| A1 | 七条验收全部满足 | 是否存在反例？尤其 ④ 和 ⑥ |
| A2 | 99 项测试全部通过 | 测试是否有效？有没有关键路径没测？ |
| A3 | `sequence` 保证不复用、不回退 | 并发 + WAL + 多连接下这个假设成立吗？ |
| A4 | Keyset 分页保证不重不漏 | 有没有边界会破？（游标伪造、跨页插入、`has_more` 判定） |
| A5 | `content_sha256` 可作防篡改证据 | 它真能防住什么、防不住什么？ |
| A6 | Phase 1 无任何认知功能泄漏 | 代码里有没有偷偷"理解"的痕迹？ |
| A7 | 只监听 127.0.0.1，无公网暴露 | 代码层面是否足够？ |
| A8 | 列表返回完整 content（不做摘要）是合理取舍 | 这个取舍在 100KB 上限下成立吗？ |

### 已做过的外部复测（供你避免重复，或判断其质量）

另一个大模型（Qwen3.8 Max）做过两轮对抗性复测，发现并已修复 4 个问题：

1. **「SQL 只在 repository.py」当时不成立** —— `migrate.py` 内有内联 SQL。
   已把版本表读写移入 `repository.py`，`migrate.py` 现在零内联 SQL。
2. **`db_writable` 检测不可靠** —— 原用 `PRAGMA user_version`（读操作）判断可写性，
   只读库上也会返回 true。我第一版修法用 `BEGIN IMMEDIATE`，
   但实测**在 WAL 只读连接上也不报错**，同样不可靠。
   最终改为：事务内真实写入 `PRAGMA user_version = 当前值+1`，然后 `ROLLBACK`。
3. **`limit` 校验重复** —— 路由层与服务层各查一次；若路由那处被移除，
   服务层的 `ValueError` 无处理器会变 500。已收敛到服务层单点，抛 `InvalidLimitError` → 422。
4. **装饰器 `status_code=405` 冗余** —— 设的是成功响应码，函数永远 raise，从不生效。已移除。

**请重点审查第 2 条的修复方案**：`/health` 里做一次真实写入再回滚，是否安全？
并发或崩溃下有无副作用？有没有更合适的做法？

---

## 八、已知未验证的部分（我不声称这些）

- 目标机上尚未部署验证（本机用 embeddable Python）
- Python 完整版未测（本机是 3.12.8 embeddable，可能缺标准库模块）
- 未测百万级数据与长时间运行（目前测到 20,000 条、207 页）
- 未测极端时间值（公元 1 年、9999 年）
- 未测超大 `metadata`（>1MB）的存储与内存影响
- 只有**一个**外部模型完成复测（GLM-5.3 中断、DeepSeek-V4-Pro 路由故障），不构成多模型共识

---

## 九、请你回答的具体问题（按此顺序逐条作答）

**Q1 契约自洽性**：三条契约 + 七条验收之间有没有冲突？
（例如 ①"ID 永久稳定"与 ⑥"不得有未经批准的数据"在将来加解释层时会不会打架？）

**Q2 实现是否真的满足契约**：逐条（①–⑦）判断 满足 / 有风险 / 违反，并给出依据的代码位置。
**凡判"有风险"的，请给出能击穿它的具体请求序列。**

**Q3 测试有效性（防假绿）**：哪些断言太弱？哪些是 happy path？哪些本该测却没测？

**Q4 分页攻击**：设计 3 个我可能没想到的翻页攻击场景，并说明当前实现会怎么表现。

**Q5 `sequence` 与并发**：在"每请求一连接 + WAL + AUTOINCREMENT"下，
单调性与唯一性是否绝对成立？推理依据是什么？何时会失效？

**Q6 `/health` 可写性探测**：这个"真写入再回滚"方案是否恰当？有何隐患？你会怎么改？

**Q7 上限与列表全文**：100,000 字符上限 + 列表返回完整 content，真机上会不会出问题？
如果会，你会怎么改（注意：改 API 形态需重新走契约批准）？

**Q8 文档过度声称**：我的声称有没有超出代码能证实的范围？请具体列出。

**Q9 目标机最小验收集**：最小且充分、能真正暴露问题的验收步骤是什么？
（不是把测试全跑一遍）

**Q10 最严重的三个问题**：按严重程度排序取前三，并说明若不管它们，
在 Phase 2 会以什么形式爆炸。

---

## 十、输出格式（请严格遵守，我要直接归档）

```
## 总体结论
<通过 / 有条件通过 / 不通过> + 一句话理由

## 逐条契约判断
| 契约 | 判断 | 依据（文件/函数） | 击穿请求序列（如有风险） |
|---|---|---|---|
| 1 |  |  |  |
| 2 |  |  |  |
| 3 |  |  |  |
| ① |  |  |  |
| ② |  |  |  |
| ③ |  |  |  |
| ④ |  |  |  |
| ⑤ |  |  |  |
| ⑥ |  |  |  |
| ⑦ |  |  |  |

## 测试有效性
- 太弱的断言：
- 缺失的关键用例：

## 分页攻击场景（Q4）
1. …
2. …
3. …

## 问题清单（按严重程度）
### [阻塞]/[严重]/[次要]/[建议] 标题
- 位置：
- 证据：
- 后果：
- 建议修复：

## 文档过度声称（Q8）
- …

## 最严重的三个问题（Q10）
1. …
2. …
3. …

## 需要我补充的材料
- …
```

---

*本文件由 `scripts/make_review_packet.py` 生成，源码与仓库一致。*
*完整测试代码见另一份《交叉验证-附录-完整测试.md》，需要时可索取。*
"""

APPENDIX_HEADER = """# 交叉验证 · 附录：完整测试代码

> 这是《交叉验证请求：忆生 Phase 1 实现审查》的附录。
> 用途：当审阅者（GPT）需要判断"测试是否假绿"时，提供全部 99 项测试的实现。
> 上下文顺序：本附录承接主文的第十节输出格式，不改变任何审查问题。

## 测试环境约定

- 使用**真实文件库**（不是内存库），因为 WAL 只在文件库上生效
- `tests/conftest.py` 为每个测试创建独立临时目录 `.pytest_tmp/`，测试后清理
- 不从 pytest 的 `tmp_path` 取目录（受管环境下会被沙箱拒绝）
- API 测试通过 FastAPI `TestClient`（ASGI 层，非真实 TCP）

"""


def main() -> int:
    OUT_MAIN.parent.mkdir(parents=True, exist_ok=True)

    def build(files: list[str], intro: str, excerpts: list[tuple[str, str]],
              with_name_list: bool) -> str:
        parts: list[str] = [HEADER, intro]
        for rel in files:
            parts.append(f"### `{rel}`\n\n")
            parts.append(fence(rel))
            parts.append("\n")

        parts.append(AFTER_SOURCE)
        for rel, name in excerpts:
            body = extract_func(rel, name)
            if body is None:
                continue
            parts.append(f"#### `{rel}::{name}`\n\n")
            parts.append(f"````python\n{body}\n````\n\n")

        parts.append(AFTER_EXCERPTS)

        if with_name_list:
            parts.append("### 全部用例名清单（便于你点名索要具体实现）\n\n")
            for rel in ALL_TEST_FILES:
                names = test_names(rel)
                parts.append(f"**`{rel}`**（{len(names)} 项）\n\n")
                for n in names:
                    parts.append(f"- `{n}`\n")
                parts.append("\n")
        return "".join(parts)

    # ---------- 主文 ----------
    main_text = build(SOURCE_FILES, SRC_FULL_INTRO, KEY_EXCERPTS, with_name_list=True)
    OUT_MAIN.write_text(main_text, encoding="utf-8", newline="\n")

    # ---------- 精简版 ----------
    compact_text = build(COMPACT_FILES, SRC_COMPACT_INTRO, COMPACT_EXCERPTS, with_name_list=False)
    OUT_COMPACT.write_text(compact_text, encoding="utf-8", newline="\n")

    # ---------- 附录 ----------
    ap: list[str] = [APPENDIX_HEADER]
    for rel in ALL_TEST_FILES:
        ap.append(f"## `{rel}`（{len(test_names(rel))} 项）\n\n")
        ap.append(fence(rel))
        ap.append("\n")
    appendix_text = "".join(ap)
    OUT_APPENDIX.write_text(appendix_text, encoding="utf-8", newline="\n")

    for label, path, text in (
        ("精简版（先发这个，篇幅最小）", OUT_COMPACT, compact_text),
        ("主文（含全部源码）", OUT_MAIN, main_text),
        ("附录（完整测试，按需再发）", OUT_APPENDIX, appendix_text),
    ):
        print(f"{label}")
        print(f"  {path}")
        print(f"  字符数 {len(text):,}   行数 {text.count(chr(10)) + 1:,}")
    print("\n用法：先发精简版或主文；GPT 若要测试全文，再发附录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
