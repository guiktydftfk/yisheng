#!/usr/bin/env python
"""把「忆生 Phase 1」项目推送到 GitHub（纯标准库，不需要安装 git）。

为什么不用 git：
    本机没有安装 git / gh CLI；SSH 到 ssh.github.com:443 不通，但
    HTTPS（api.github.com）可用。因此直接用 GitHub REST API 的 Git Data 接口
    创建 blob → tree → commit → ref，效果等同于一次 git push。

安全设计：
    * Token **只从环境变量读取**（`GITHUB_TOKEN`），不落盘、不回显、不进命令历史；
    * 本脚本不会打印 token；
    * 默认**只推项目文件**，不含会话归档（00_~05_）、数据库、临时目录；
    * 推送前做一次敏感信息扫描（token/私钥/apiKey 特征）。

用法（在你自己的终端里，token 不要贴到对话里）：

    $env:GITHUB_TOKEN = "ghp_xxxxxxxx"          # 或 fine-grained token
    python scripts\\push_to_github.py --repo yisheng --private

    # 先看会推什么（不联网、不创建仓库）：
    python scripts\\push_to_github.py --dry-run

参数：
    --repo NAME        仓库名（默认 yisheng）
    --private          私有仓库（默认私有）
    --public           公开仓库（谨慎：会公开你的研究与代码）
    --branch NAME      分支名（默认 main）
    --include-archive  连同 00_~05_ 会话归档一起推（**强烈不建议**，见下）
    --dry-run          只列出文件与检查，不联网
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"

# ---------------------------------------------------------------------------
# 要推送的文件（白名单）
#
# 刻意不包含：
#   * 00_~05_ 会话归档  —— 含完整私人对话记录，推公开仓库等于公开隐私
#   * data/*.db         —— 运行数据
#   * .audit*/.verifytmp/.acceptance/.stresstmp/.pytest_tmp —— 临时产物
#   * docs/交叉验证-*.md —— 由脚本生成、含源码副本，可随时重新生成
# ---------------------------------------------------------------------------

INCLUDE_PATTERNS = [
    "src/**/*.py",
    "src/**/*.sql",
    "tests/**/*.py",
    "scripts/**/*.py",
    "docs/README.md",
    "docs/运行说明.md",
    "docs/修订说明-v2.md",
    "docs/修订方案-v2-待批准.md",
    ".gitignore",
    "pyproject.toml",
    "requirements.txt",
    "run.py",
]

# 归档路径（仅 --include-archive 时加入）
ARCHIVE_PATTERNS = ["00_会话原始记录/**", "01_会话文本导出/**", "02_我写下的文档/**",
                    "03_抓取的资料/**", "04_工具与环境/**", "05_图片与证据/**", "README.md"]

# 任何情况下都不推的东西
ALWAYS_EXCLUDE = [
    r"\.tmp", r"\.pytest_tmp", r"\.verifytmp", r"\.acceptance", r"\.stresstmp",
    r"\.audit", r"__pycache__", r"\.db$", r"\.db-wal$", r"\.db-shm$", r"\.pyc$",
    r"\.git/", r"\.pytest_q\.txt", r"\.pytest_collect\.txt", r"\.pytest_last_run\.txt",
]

# 敏感信息特征（命中则拒绝推送，除非 --force）
SECRET_PATTERNS = [
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained token"),
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI/DeepSeek 风格 key"),
    (r"-----BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY-----", "私钥"),
    (r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}", "疑似 apiKey 赋值"),
]


def iter_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in INCLUDE_PATTERNS:
        for p in ROOT.glob(pattern):
            if p.is_file():
                files.add(p)
    return sorted(files)


def iter_archive() -> list[Path]:
    files: set[Path] = set()
    for pattern in ARCHIVE_PATTERNS:
        for p in ROOT.glob(pattern):
            if p.is_file():
                files.add(p)
    return sorted(files)


def is_excluded(rel: str) -> bool:
    return any(re.search(pat, rel, re.IGNORECASE) for pat in ALWAYS_EXCLUDE)


def build_tree(files: list[Path], include_archive: bool) -> dict[str, bytes]:
    """返回 {仓库内路径: 内容}。"""
    out: dict[str, bytes] = {}

    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if is_excluded(rel):
            continue
        out[rel] = f.read_bytes()

    # 仓库根 README：用项目 README（不是归档 README）
    project_readme = ROOT / "docs" / "README.md"
    if project_readme.exists() and not include_archive:
        out["README.md"] = project_readme.read_bytes()

    if include_archive:
        for f in iter_archive():
            rel = f.relative_to(ROOT).as_posix()
            if is_excluded(rel):
                continue
            if rel == "README.md":
                continue  # 归档 README 不覆盖仓库 README
            out[rel] = f.read_bytes()

    return dict(sorted(out.items()))


def scan_secrets(tree: dict[str, bytes]) -> list[str]:
    findings: list[str] = []
    for path, data in tree.items():
        if path.endswith((".png", ".jpg", ".zip", ".zst", ".apk")):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, text):
                # 只报告位置，不回显命中的内容本身
                findings.append(f"{path}  （命中特征：{label}）")
    return findings


def api(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"message": body[:300]}


def sanitize_token(raw: str) -> tuple[str, str | None]:
    """清理并检查 token。

    返回 (token, 错误信息)。错误信息非空时调用方应中止。

    为什么需要：
        HTTP 头只能放 latin-1 可编码字符。如果 token 里混入了中文
        （最常见：把示例里的占位符 `"你的token"` 原样复制），
        urllib 会抛 ``UnicodeEncodeError: 'latin-1' codec can't encode ...``，
        堆栈完全看不出真正原因。
    """
    token = raw.strip()

    # 去掉从网页/文档复制时可能带上的各种引号
    for quote in ('"', "'", "“", "”", "‘", "’", "`", "「", "」"):
        token = token.strip(quote)

    token = token.strip()

    if not token:
        return "", "GITHUB_TOKEN 是空的（或只有引号/空白）"

    if token == "你的token" or token.startswith("你的"):
        return "", "你填的是示例占位符「你的token」，不是真 token"

    bad = [(i, ch) for i, ch in enumerate(token) if ord(ch) > 127]
    if bad:
        pos, ch = bad[0]
        return "", (
            f"token 第 {pos + 1} 个字符不是 ASCII（{ch!r}），共 {len(bad)} 个非 ASCII 字符。\n"
            "         GitHub token 只由字母、数字、下划线组成。\n"
            "         最常见原因：把示例里的「你的token」原样复制进去了。"
        )

    if token != raw.strip():
        return token, None  # 已被清理（去引号），但仍可用

    return token, None


def push(token: str, repo: str, private: bool, branch: str, tree: dict[str, bytes]) -> str:
    status, user = api("GET", f"{API}/user", token)
    if status != 200:
        raise SystemExit(f"[失败] token 无效或权限不足（HTTP {status}）：{user.get('message')}")
    owner = user["login"]
    print(f"  账号：{owner}")

    status, existing = api("GET", f"{API}/repos/{owner}/{repo}", token)
    if status == 404:
        status, created = api("POST", f"{API}/user/repos", token, {
            "name": repo,
            "private": private,
            "description": "忆生（Living Memory）Phase 1 — Experience Stream：append-only 的原始经历记录系统",
            "auto_init": False,
        })
        if status not in (200, 201):
            raise SystemExit(f"[失败] 创建仓库失败（HTTP {status}）：{created.get('message')}")
        print(f"  已创建{'私有' if private else '公开'}仓库：{created['full_name']}")
    elif status == 200:
        print(f"  仓库已存在：{existing['full_name']}（{'私有' if existing['private'] else '公开'}）")
        private = existing["private"]
    else:
        raise SystemExit(f"[失败] 查询仓库失败（HTTP {status}）：{existing.get('message')}")

    # 1) blobs
    print(f"  上传 {len(tree)} 个文件…")
    entries = []
    for i, (path, data) in enumerate(tree.items(), 1):
        status, blob = api("POST", f"{API}/repos/{owner}/{repo}/git/blobs", token, {
            "content": base64.b64encode(data).decode("ascii"),
            "encoding": "base64",
        })
        if status not in (200, 201):
            raise SystemExit(f"[失败] 上传 {path} 失败（HTTP {status}）：{blob.get('message')}")
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if i % 10 == 0 or i == len(tree):
            print(f"    {i}/{len(tree)}")

    # 2) tree
    status, tree_resp = api("POST", f"{API}/repos/{owner}/{repo}/git/trees", token, {"tree": entries})
    if status not in (200, 201):
        raise SystemExit(f"[失败] 创建 tree 失败（HTTP {status}）：{tree_resp.get('message')}")

    # 3) 父提交（若分支已存在）
    parents: list[str] = []
    status, ref = api("GET", f"{API}/repos/{owner}/{repo}/git/ref/heads/{branch}", token)
    if status == 200:
        parents = [ref["object"]["sha"]]
        print(f"  基于已有提交 {parents[0][:8]} 创建新提交")
    else:
        print("  首次提交（分支尚不存在）")

    # 4) commit
    message = "忆生 Phase 1：Experience Stream（append-only 原始经历记录系统）\n\n" + \
              "含三条架构契约、七条验收要求、150 项测试与真实环境验收脚本。"
    payload = {"message": message, "tree": tree_resp["sha"], "parents": parents}
    status, commit = api("POST", f"{API}/repos/{owner}/{repo}/git/commits", token, payload)
    if status not in (200, 201):
        raise SystemExit(f"[失败] 创建 commit 失败（HTTP {status}）：{commit.get('message')}")
    print(f"  提交：{commit['sha'][:8]}")

    # 5) ref
    if parents:
        status, res = api("PATCH", f"{API}/repos/{owner}/{repo}/git/refs/heads/{branch}", token,
                          {"sha": commit["sha"], "force": False})
    else:
        status, res = api("POST", f"{API}/repos/{owner}/{repo}/git/refs", token,
                          {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    if status not in (200, 201):
        raise SystemExit(f"[失败] 更新分支失败（HTTP {status}）：{res.get('message')}")

    return f"https://github.com/{owner}/{repo}"


def main() -> int:
    parser = argparse.ArgumentParser(description="把忆生项目推送到 GitHub")
    parser.add_argument("--repo", default="yisheng")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--private", action="store_true", help="私有仓库（默认）")
    parser.add_argument("--public", action="store_true", help="公开仓库")
    parser.add_argument("--include-archive", action="store_true", help="连同会话归档一起推（不建议）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--zip", metavar="PATH", default=None,
                        help="不推送，改为把待推文件打包成 ZIP（用于 GitHub 网页上传）")
    parser.add_argument("--force", action="store_true", help="忽略敏感信息扫描结果")
    args = parser.parse_args()

    private = not args.public

    tree = build_tree(iter_files(), args.include_archive)

    total = sum(len(v) for v in tree.values())
    print("=" * 66)
    print("待推送内容")
    print("=" * 66)
    print(f"  文件数：{len(tree)}　总大小：{total / 1024:.1f} KB")
    for path, data in tree.items():
        print(f"    {len(data):>7} B  {path}")

    if args.include_archive:
        print("\n  ⚠️ 已选择包含会话归档 —— 其中含完整私人对话记录。")
        print("     若仓库是 public，这等于把私人对话公开。请确认这是你想要的。")

    findings = scan_secrets(tree)
    if findings:
        print("\n  ⚠️ 敏感信息扫描发现可疑内容：")
        for f in findings:
            print(f"    - {f}")
        if not args.force:
            print("\n  已中止。确认无泄漏后可加 --force 继续。")
            return 2
    else:
        print("\n  敏感信息扫描：未发现 token / 私钥 / apiKey 特征")

    if args.dry_run:
        print("\n[dry-run] 未联网、未创建仓库。去掉 --dry-run 才会真正推送。")
        return 0

    if args.zip:
        import zipfile

        target = Path(args.zip)
        if not target.is_absolute():
            target = ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path, data in tree.items():
                zf.writestr(path, data)
        print(f"\n已打包：{target}")
        print(f"  {len(tree)} 个文件，{target.stat().st_size / 1024:.1f} KB")
        print("\n用法：在 GitHub 网页上新建仓库 → 点『uploading an existing file』")
        print("      → 把这个 ZIP 里的**内容**（不是 ZIP 本身）拖进去 → Commit。")
        print("      ZIP 内已按仓库根目录组织好，直接全选拖入即可。")
        return 0

    token_raw = os.environ.get("GITHUB_TOKEN", "")
    if not token_raw.strip():
        print("\n[需要 token] 未找到环境变量 GITHUB_TOKEN。")
        print("  在你自己的终端设置（不要把 token 贴进对话）：")
        print('    $env:GITHUB_TOKEN = "ghp_..."')
        print("  需要的权限：classic token 勾选 `repo`；fine-grained token 勾选")
        print("  Administration: Read and write + Contents: Read and write。")
        return 3

    token, token_error = sanitize_token(token_raw)
    if token_error:
        print("\n[token 无效] " + token_error)
        print("  请到 https://github.com/settings/tokens 重新生成一个 token。")
        print("  设置方式（注意不要把占位符原样粘贴）：")
        print('    $env:GITHUB_TOKEN = "ghp_你的真实token"')
        return 4

    print("\n开始推送…")
    try:
        url = push(token, args.repo, private, args.branch, tree)
    except SystemExit:
        raise
    except UnicodeEncodeError as exc:  # 兜底：理论上 sanitize_token 已拦住
        print(f"\n[失败] 请求中有无法编码的字符：{exc}")
        print("  请检查 token 是否只含 ASCII 字符。")
        return 4

    print("\n" + "=" * 66)
    print(f"完成：{url}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
