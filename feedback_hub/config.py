"""feedback_hub 全局配置常量。

所有可调参数集中此处，禁止散落到业务代码里。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------- 路径 ----------
PKG_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = PKG_DIR / "data"
RAW_DIR: Path = DATA_DIR / "raw"
DB_PATH: Path = DATA_DIR / "feedback.db"
SCHEMA_PATH: Path = PKG_DIR / "schema.sql"

# ---------- 会话聚合 ----------
CONVERSATION_GAP_SECONDS: int = 1800  # 30 分钟

# ---------- 标签枚举 ----------
L1_VALUES: list[str] = ["A.Bug", "B.建议", "C.咨询", "D.情绪", "E.无效", "待定"]
L2_VALUES: list[str] = [
    "输入核心", "语音", "符号表情", "皮肤", "词库", "账号",
    "键盘交互", "安装更新", "性能", "权限隐私", "广告活动", "其他",
]
SEVERITY_VALUES: list[str] = ["P0", "P1", "P2", "P3"]

# L1 严重度优先级（数字越大越严重）
L1_PRIORITY: dict[str, int] = {
    "A.Bug": 5, "B.建议": 4, "C.咨询": 3, "D.情绪": 2, "E.无效": 1, "待定": 0,
}
# severity 优先级（数字越大越严重）
SEVERITY_PRIORITY: dict[str, int] = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}

# ---------- LLM Agent ----------
DEFAULT_AGENT_KEY: str = "7db9e110-d586-4bc5-be3f-07dab992cb49"
AGENT_RUN_URL: str = "http://winkagentsvr.dante.weread2.woa.com/agent/run"
AGENT_POLL_URL: str = "http://winkagentsvr.dante.weread2.woa.com/agent/poll"


def get_agent_key() -> str:
    """优先取环境变量 WINK_AGENT_KEY，否则用默认。"""
    return os.environ.get("WINK_AGENT_KEY") or DEFAULT_AGENT_KEY


# ---------- OpenAPI 拉取 ----------
OPENAPI_ENDPOINT: str = "https://wrfeedback.weread.woa.com/openapi/search/session"
DEFAULT_CHANNEL: str = "wetype"
DEFAULT_SERVICE_VID: int = 10000
DEFAULT_HTTP_TIMEOUT: int = 120


def ensure_dirs() -> None:
    """启动期调用，确保数据目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
