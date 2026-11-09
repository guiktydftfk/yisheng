"""pytest 配置与公共夹具。

两个要点：

1. **不使用 pytest 的 ``tmp_path`` / ``--basetemp``**
   DSH 沙箱在 Windows 上会拒绝 pytest 创建与清理临时目录树（WinError 5），
   导致每个测试在 setup 阶段就失败。因此本套件用**工作区内的临时目录**，
   并在每个测试结束后自行清理，不依赖 pytest 的临时目录设施。

2. **测试分层标记**（对应审查要求：区分 API 层 / Repository 层 / SQLite 行为 / 完整性检测）
   为免给近百个用例逐个贴标签，这里按文件给出默认层；
   特殊用例在文件内显式加 ``@pytest.mark.static`` / ``repository`` 覆盖（两者并存）。

   分层含义：
     * ``api``        —— HTTP 层保护与行为（状态码、405、三态 health）
     * ``repository`` —— 仓储层约束（不暴露 update/delete、SQL 位置）
     * ``sqlite``     —— SQLite 真实行为（AUTOINCREMENT 回滚、WAL、并发、synchronous）
     * ``integrity``  —— 数据完整性检测（篡改内容后能否发现）
     * ``contract``   —— 七条验收要求
     * ``static``     —— 静态源码检查（**补充证据，不替代运行时测试**）
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

# 让 tests/ 无需安装即可 import src/yisheng
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient  # noqa: E402

from yisheng import config  # noqa: E402
from yisheng.db import repository  # noqa: E402
from yisheng.db.connection import connect  # noqa: E402
from yisheng.db.migrate import apply_migrations  # noqa: E402
from yisheng.main import create_app  # noqa: E402

# 工作区内、被 .gitignore 忽略的临时根目录
TMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest_tmp"

# 文件 → 默认分层标记
FILE_LAYER = {
    "test_observe.py": "api",
    "test_validation.py": "api",
    "test_metadata_raw.py": "api",
    "test_health.py": "api",
    "test_cursor_bounds.py": "api",
    "test_scenarios.py": "api",
    "test_integrity.py": "integrity",
    "test_sequence_autoincrement.py": "sqlite",
    "test_persistence.py": "sqlite",
    "test_schema_contract.py": "contract",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """按文件名给出默认分层标记（文件内显式标记仍并存）。"""
    for item in items:
        layer = FILE_LAYER.get(Path(str(item.fspath)).name)
        if layer:
            item.add_marker(getattr(pytest.mark, layer))


@pytest.fixture()
def workdir():
    """每个测试一个独立目录，测试结束自动清理。"""
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TMP_ROOT / f"t_{uuid.uuid4().hex[:12]}"
    path.mkdir()
    try:
        yield path
    finally:
        _rmtree_quiet(path)


def _rmtree_quiet(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


@pytest.fixture()
def db_path(workdir, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把全局 DB 路径指向临时目录，返回该文件路径。"""
    path = workdir / "yisheng.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATA_DIR", workdir)
    return path


@pytest.fixture()
def conn(db_path: Path):
    """一个已完成迁移的连接。"""
    connection = connect(db_path)
    apply_migrations(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def repo():
    return repository
