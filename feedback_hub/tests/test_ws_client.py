"""ws_bot client 模块测试。"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from feedback_hub.ws_bot.client import BotClient, ConnectionState
from feedback_hub.ws_bot.config import WsBotConfig


@pytest.fixture
def cfg():
    return WsBotConfig(bot_id="test_bot", secret="test_secret")


@pytest.mark.asyncio
async def test_initial_state(cfg):
    """初始状态为 DISCONNECTED。"""
    client = BotClient(cfg)
    assert client.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_subscribe_success(cfg):
    """订阅成功后状态变为 CONNECTED。"""
    client = BotClient(cfg)
    mock_ws = AsyncMock()
    subscribe_resp = json.dumps({
        "headers": {"req_id": "any"},
        "errcode": 0,
        "errmsg": "ok",
    })
    mock_ws.recv = AsyncMock(return_value=subscribe_resp)
    mock_ws.send = AsyncMock()
    mock_ws.close = AsyncMock()

    client._ws = mock_ws
    client._state = ConnectionState.SUBSCRIBING
    result = await client._do_subscribe()
    assert result is True
    assert client.state == ConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_subscribe_failure(cfg):
    """订阅失败返回 False。"""
    client = BotClient(cfg)
    mock_ws = AsyncMock()
    subscribe_resp = json.dumps({
        "headers": {"req_id": "any"},
        "errcode": 40001,
        "errmsg": "invalid secret",
    })
    mock_ws.recv = AsyncMock(return_value=subscribe_resp)
    mock_ws.send = AsyncMock()

    client._ws = mock_ws
    client._state = ConnectionState.SUBSCRIBING
    result = await client._do_subscribe()
    assert result is False


@pytest.mark.asyncio
async def test_backoff_calculation(cfg):
    """重连退避计算正确。"""
    client = BotClient(cfg)
    assert 0.8 <= client._calc_backoff(0) <= 1.2
    assert 1.6 <= client._calc_backoff(1) <= 2.4
    b5 = client._calc_backoff(5)
    assert b5 <= 36.0  # 30 * 1.2 max jitter
