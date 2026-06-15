"""ws_bot handler 模块测试。"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from feedback_hub.ws_bot.handler import MessageHandler


@pytest.fixture
def tmp_chat_file(tmp_path):
    return tmp_path / "chat_ids.json"


@pytest.mark.asyncio
async def test_on_message_discovers_chatid(tmp_chat_file):
    """收到消息回调时自动发现并持久化 chatid。"""
    handler = MessageHandler(chat_ids_file=tmp_chat_file)
    payload = {
        "cmd": "aibot_msg_callback",
        "body": {
            "chatid": "new_chat_001",
            "chat_type": 1,
            "msgtype": "text",
            "text": {"content": "hello"},
        },
    }
    await handler.on_message(payload)
    assert "new_chat_001" in handler.chat_ids

    # 验证持久化
    data = json.loads(tmp_chat_file.read_text())
    assert "new_chat_001" in data["chat_ids"]


@pytest.mark.asyncio
async def test_on_message_no_duplicate(tmp_chat_file):
    """重复 chatid 不重复写入。"""
    handler = MessageHandler(chat_ids_file=tmp_chat_file)
    handler._chat_ids = {"existing_chat"}
    payload = {
        "cmd": "aibot_msg_callback",
        "body": {"chatid": "existing_chat", "chat_type": 1},
    }
    await handler.on_message(payload)
    assert len(handler.chat_ids) == 1


@pytest.mark.asyncio
async def test_on_event_enter_chat(tmp_chat_file):
    """enter_chat 事件也触发 chatid 发现。"""
    handler = MessageHandler(chat_ids_file=tmp_chat_file)
    payload = {
        "cmd": "aibot_event_callback",
        "body": {
            "event_type": "enter_chat",
            "chatid": "event_chat_002",
        },
    }
    await handler.on_event(payload)
    assert "event_chat_002" in handler.chat_ids


@pytest.mark.asyncio
async def test_load_existing_chat_ids(tmp_chat_file):
    """启动时加载已持久化的 chatid。"""
    tmp_chat_file.write_text(json.dumps({
        "chat_ids": ["saved_chat"],
        "updated_at": "2026-05-27T10:00:00",
    }))
    handler = MessageHandler(chat_ids_file=tmp_chat_file)
    handler.load_persisted()
    assert "saved_chat" in handler.chat_ids
