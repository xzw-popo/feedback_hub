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
DEFAULT_AGENT_KEY: str = ""
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


# ---------- Pusher（spec 阶段 2）----------
KW_GROUPS_PATH: Path = PKG_DIR / "pusher" / "kw_groups.yaml"


def get_webhook_url() -> str | None:
    """企微群机器人 webhook URL；优先取环境变量 WECHAT_WEBHOOK_URL。"""
    return os.environ.get("WECHAT_WEBHOOK_URL")


# ---------- 导入 API ----------
IMPORT_TOKEN: str = os.environ.get("IMPORT_TOKEN", "")


# ---------- 智能搜索 LLM ----------
LLM_API_URL: str = os.environ.get(
    "LLM_API_URL", "https://api.deepseek.com/v1/chat/completions"
)
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

# 精筛参数
FINE_FILTER_MAX_CONVERSATIONS: int = 500   # 单次精筛上限
FINE_FILTER_BATCH_SIZE: int = 10           # 每批条数（单次 LLM 调用评估 10 条）
FINE_FILTER_MAX_CONCURRENCY: int = 50      # 最大并发 LLM 调用
FINE_FILTER_BATCH_TIMEOUT: int = 30        # 单批超时(秒)
FINE_FILTER_TOTAL_TIMEOUT: int = 180       # 整体超时(秒)

# AI 搜索正则匹配的单条文本最大长度。超长文本（崩溃日志/直播口水等）配合
# LLM 生成的含 .* 正则会触发灾难性回溯，实测单条卡死 80s+，远超前端 60s 超时。
# 超过此长度的文本不参与正则匹配，且 SQLite REGEXP 回调内会截断后再匹配。
SEARCH_REGEXP_MAX_TEXT_LEN: int = 2000
