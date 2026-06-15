"""ws_bot 长连接专属配置。"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from feedback_hub.config import DATA_DIR


@dataclass
class WsBotConfig:
    bot_id: str
    secret: str
    ws_url: str = "wss://openws.work.weixin.qq.com"
    push_cron: str = "0 10 * * *"
    trigger_port: int = 8081
    chat_ids: list[str] = field(default_factory=list)
    chat_ids_file: Path = field(default_factory=lambda: DATA_DIR / "chat_ids.json")


def load_config() -> WsBotConfig:
    """从环境变量加载配置。必填字段缺失时 sys.exit。"""
    bot_id = os.environ.get("WECOM_BOT_ID", "").strip()
    secret = os.environ.get("WECOM_BOT_SECRET", "").strip()

    if not bot_id or not secret:
        print(
            "[ws_bot] error: WECOM_BOT_ID 和 WECOM_BOT_SECRET 环境变量必须设置",
            file=sys.stderr,
        )
        raise SystemExit(1)

    chat_ids_raw = os.environ.get("WECOM_CHAT_IDS", "").strip()
    chat_ids = [cid.strip() for cid in chat_ids_raw.split(",") if cid.strip()] if chat_ids_raw else []

    return WsBotConfig(
        bot_id=bot_id,
        secret=secret,
        ws_url=os.environ.get("WECOM_WS_URL", "wss://openws.work.weixin.qq.com").strip(),
        push_cron=os.environ.get("WECOM_PUSH_CRON", "0 10 * * *").strip(),
        trigger_port=int(os.environ.get("WECOM_TRIGGER_PORT", "8081")),
        chat_ids=chat_ids,
    )
