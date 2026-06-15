"""ws_bot scheduler 模块测试。"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from feedback_hub.ws_bot.scheduler import PushScheduler


@pytest.fixture
def mock_push_fn():
    return AsyncMock(return_value={"status": "ok", "pushed": 1})


@pytest.mark.asyncio
async def test_parse_cron_next_run():
    """cron 表达式解析得到合理的下次运行时间。"""
    scheduler = PushScheduler(
        push_cron="0 10 * * *",
        push_fn=AsyncMock(),
        trigger_port=0,
    )
    delay = scheduler._next_run_delay()
    assert delay > 0
    assert delay <= 86400


@pytest.mark.asyncio
async def test_trigger_endpoint(mock_push_fn):
    """HTTP /trigger 端点调用推送函数并返回 200。"""
    scheduler = PushScheduler(
        push_cron="0 10 * * *",
        push_fn=mock_push_fn,
        trigger_port=0,
    )
    app = scheduler._build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/trigger", json={"date": "2026-05-27"})
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
    mock_push_fn.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_endpoint_error(mock_push_fn):
    """推送函数抛异常时返回 500。"""
    mock_push_fn.side_effect = RuntimeError("db error")
    scheduler = PushScheduler(
        push_cron="0 10 * * *",
        push_fn=mock_push_fn,
        trigger_port=0,
    )
    app = scheduler._build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/trigger")
        assert resp.status == 500
        body = await resp.json()
        assert body["status"] == "error"
