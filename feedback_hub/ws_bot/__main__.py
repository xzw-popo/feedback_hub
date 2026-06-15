"""入口：python -m feedback_hub.ws_bot

启动常驻进程：WebSocket 连接 + 定时推送 + HTTP 触发。
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime

from feedback_hub import db
from feedback_hub.config import ensure_dirs
from feedback_hub.pusher import candidate, formatter, push_log
from feedback_hub.ws_bot.client import BotClient
from feedback_hub.ws_bot.config import load_config
from feedback_hub.ws_bot.handler import MessageHandler
from feedback_hub.ws_bot.scheduler import PushScheduler
from feedback_hub.ws_bot.sender import send_markdown


# 全局实例
_config = None
_client = None
_handler = None


async def do_push(push_date: str | None) -> dict:
    """执行一次完整推送流程。被 scheduler 调用。"""
    global _client, _handler, _config

    if not _client.is_connected:
        return {"status": "disconnected"}

    # 确定推送日期和 now_ms
    if push_date:
        dt = datetime.strptime(push_date, "%Y-%m-%d")
        now_ms = int((dt.timestamp() + 86399) * 1000)
    else:
        now_ms = int(time.time() * 1000)
        push_date = datetime.fromtimestamp(now_ms / 1000).strftime("%Y-%m-%d")

    # 确定目标 chat_ids
    chat_ids = list(_config.chat_ids) or list(_handler.chat_ids)
    if not chat_ids:
        print("[ws_bot] no chat_ids available, skipping push", file=sys.stderr)
        return {"status": "error", "detail": "no chat_ids configured or discovered"}

    # 生成候选
    conn = db.connect()
    db.init_schema(conn)
    try:
        result = candidate.generate_candidates(conn, now_ms=now_ms)
        top_groups = result["top_groups"]
        markdown = formatter.format_message(
            push_date=push_date,
            scanned=result["scanned"],
            all_groups=result["all_groups_count"],
            top_groups=top_groups,
        )

        # 记录 push_log
        created_at = int(time.time())
        push_ids = push_log.save_top_groups(
            conn, push_date=push_date,
            top_groups=top_groups, created_at=created_at,
        )

        # 发送到每个群
        success_count = 0
        for chatid in chat_ids:
            chat_type = _handler.get_chat_type(chatid)
            ok = await send_markdown(
                _client, chatid=chatid, markdown=markdown, chat_type=chat_type,
            )
            if ok:
                success_count += 1

        # 标记送达
        if success_count > 0:
            push_log.mark_delivered(conn, push_ids, delivered_at=int(time.time()))

        return {"status": "ok", "pushed": success_count}
    finally:
        conn.close()


async def main() -> None:
    global _config, _client, _handler

    ensure_dirs()
    _config = load_config()

    # 初始化 handler
    _handler = MessageHandler(chat_ids_file=_config.chat_ids_file)
    _handler.load_persisted()

    # 初始化 client
    _client = BotClient(_config)
    _client.set_message_handler(_handler.on_message)
    _client.set_event_handler(_handler.on_event)

    # 初始化 scheduler
    scheduler = PushScheduler(
        push_cron=_config.push_cron,
        push_fn=do_push,
        trigger_port=_config.trigger_port,
    )

    # 启动 scheduler（HTTP server + 定时任务）
    await scheduler.start()

    # 启动 WebSocket 连接（阻塞）
    try:
        await _client.start()
    except KeyboardInterrupt:
        pass
    finally:
        await _client.stop()
        await scheduler.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[ws_bot] shutting down", file=sys.stderr)
        sys.exit(0)
