"""ws_bot sender 模块测试。"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from feedback_hub.ws_bot.sender import send_markdown
from feedback_hub.ws_bot.client import BotClient, ConnectionState
from feedback_hub.ws_bot.config import WsBotConfig


@pytest.fixture
def mock_client():
    cfg = WsBotConfig(bot_id="bot1", secret="sec1")
    client = BotClient(cfg)
    client._state = ConnectionState.CONNECTED
    return client


@pytest.mark.asyncio
async def test_send_markdown_success(mock_client):
    """发送成功返回 True。"""
    mock_client.send_and_wait = AsyncMock(return_value={
        "headers": {"req_id": "any"},
        "errcode": 0,
        "errmsg": "ok",
    })
    result = await send_markdown(mock_client, chatid="chat_001", markdown="**hello**")
    assert result is True
    call_args = mock_client.send_and_wait.call_args[0][0]
    assert call_args["cmd"] == "aibot_send_msg"
    assert call_args["body"]["chatid"] == "chat_001"
    assert call_args["body"]["msgtype"] == "markdown"
    assert call_args["body"]["markdown"]["content"] == "**hello**"


@pytest.mark.asyncio
async def test_send_markdown_timeout(mock_client):
    """超时返回 False。"""
    mock_client.send_and_wait = AsyncMock(return_value=None)
    result = await send_markdown(mock_client, chatid="chat_001", markdown="test")
    assert result is False


@pytest.mark.asyncio
async def test_send_markdown_error_code(mock_client):
    """errcode != 0 返回 False。"""
    mock_client.send_and_wait = AsyncMock(return_value={
        "headers": {"req_id": "any"},
        "errcode": 40001,
        "errmsg": "invalid chatid",
    })
    result = await send_markdown(mock_client, chatid="chat_001", markdown="test")
    assert result is False


@pytest.mark.asyncio
async def test_send_markdown_disconnected():
    """未连接时返回 False。"""
    cfg = WsBotConfig(bot_id="bot1", secret="sec1")
    client = BotClient(cfg)
    result = await send_markdown(client, chatid="chat_001", markdown="test")
    assert result is False
