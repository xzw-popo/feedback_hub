"""封装 aibot_send_msg 主动推送。"""
from __future__ import annotations

import sys
import uuid

from feedback_hub.ws_bot.client import BotClient


async def send_markdown(
    client: BotClient, *, chatid: str, markdown: str,
    chat_type: int = 2, timeout: float = 10.0,
) -> bool:
    """通过长连接发送 markdown 消息到指定群聊。

    Args:
        chat_type: 1=单聊, 2=群聊（默认群聊）

    Returns:
        True = 发送成功（errcode==0）
        False = 未连接 / 超时 / errcode!=0
    """
    if not client.is_connected:
        print("[ws_bot] cannot send: not connected", file=sys.stderr)
        return False

    req_id = str(uuid.uuid4())
    payload = {
        "cmd": "aibot_send_msg",
        "headers": {"req_id": req_id},
        "body": {
            "chatid": chatid,
            "chat_type": chat_type,
            "msgtype": "markdown",
            "markdown": {"content": markdown},
        },
    }

    resp = await client.send_and_wait(payload, timeout=timeout)
    if resp is None:
        print(f"[ws_bot] send timeout for chatid={chatid}", file=sys.stderr)
        return False

    errcode = resp.get("errcode", -1)
    if errcode == 0:
        print(f"[ws_bot] pushed to {chatid}, req_id={req_id}", file=sys.stderr)
        return True
    else:
        print(
            f"[ws_bot] send failed: errcode={errcode} errmsg={resp.get('errmsg')}",
            file=sys.stderr,
        )
        return False
