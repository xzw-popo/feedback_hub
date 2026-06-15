"""WebSocket 连接管理：连接、订阅、心跳、断线重连。"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import uuid
from enum import Enum
from typing import Any, Callable, Coroutine

import websockets
from websockets.exceptions import ConnectionClosed

from feedback_hub.ws_bot.config import WsBotConfig


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    SUBSCRIBING = "subscribing"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class BotClient:
    """企微智能机器人 WebSocket 客户端。"""

    HEARTBEAT_INTERVAL = 25  # seconds
    PONG_TIMEOUT = 10  # seconds
    MAX_BACKOFF = 30  # seconds
    MAX_RECONNECT_ATTEMPTS = 10

    def __init__(self, config: WsBotConfig):
        self._config = config
        self._state = ConnectionState.DISCONNECTED
        self._ws = None
        self._reconnect_attempts = 0
        self._heartbeat_task: asyncio.Task | None = None
        self._listener_task: asyncio.Task | None = None
        self._pong_event: asyncio.Event = asyncio.Event()
        self._pending_responses: dict[str, asyncio.Future] = {}
        self._on_message: Callable[[dict], Coroutine] | None = None
        self._on_event: Callable[[dict], Coroutine] | None = None
        self._stop_event = asyncio.Event()

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    def set_message_handler(self, handler: Callable[[dict], Coroutine]) -> None:
        self._on_message = handler

    def set_event_handler(self, handler: Callable[[dict], Coroutine]) -> None:
        self._on_event = handler

    async def start(self) -> None:
        """启动连接循环。阻塞直到 stop() 被调用。"""
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                await self._connect_and_run()
            except Exception as e:
                print(f"[ws_bot] connection error: {e}", file=sys.stderr)
            if self._stop_event.is_set():
                break
            # 重连
            self._state = ConnectionState.RECONNECTING
            backoff = self._calc_backoff(self._reconnect_attempts)
            print(
                f"[ws_bot] reconnect attempt {self._reconnect_attempts + 1}, "
                f"backoff={backoff:.1f}s",
                file=sys.stderr,
            )
            self._reconnect_attempts += 1
            if self._reconnect_attempts > self.MAX_RECONNECT_ATTEMPTS:
                print(
                    f"[ws_bot] reconnect failed after {self.MAX_RECONNECT_ATTEMPTS} attempts",
                    file=sys.stderr,
                )
            await asyncio.sleep(backoff)

    async def stop(self) -> None:
        """优雅关闭。"""
        self._stop_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._listener_task:
            self._listener_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._state = ConnectionState.DISCONNECTED

    async def send_and_wait(self, payload: dict, timeout: float = 10.0) -> dict | None:
        """发送消息并等待匹配 req_id 的响应。超时返回 None。"""
        if not self._ws or not self.is_connected:
            return None
        req_id = payload.get("headers", {}).get("req_id", str(uuid.uuid4()))
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_responses[req_id] = future
        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        except (ConnectionClosed, OSError) as e:
            print(f"[ws_bot] send_and_wait connection lost: {e}", file=sys.stderr)
            return None
        finally:
            self._pending_responses.pop(req_id, None)

    async def _connect_and_run(self) -> None:
        """建立连接 → 订阅 → 心跳 + 监听循环。

        websockets 15.x 默认启用协议级 ping/pong (每 20s)，
        我们额外发送应用层 JSON ping 以满足企微的心跳要求。
        """
        self._state = ConnectionState.CONNECTING
        # ping_interval=None 禁用 websockets 库的自动 ping，
        # 由我们自行管理应用层心跳
        async with websockets.connect(
            self._config.ws_url,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._state = ConnectionState.SUBSCRIBING

            if not await self._do_subscribe():
                return

            # 连接成功，重置计数器
            self._reconnect_attempts = 0
            print("[ws_bot] connected and subscribed", file=sys.stderr)

            # 启动心跳
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            try:
                await self._listen_loop()
            finally:
                self._heartbeat_task.cancel()
                self._state = ConnectionState.DISCONNECTED

    async def _do_subscribe(self) -> bool:
        """发送 aibot_subscribe 并等待响应。"""
        req_id = str(uuid.uuid4())
        subscribe_msg = {
            "cmd": "aibot_subscribe",
            "headers": {"req_id": req_id},
            "body": {
                "bot_id": self._config.bot_id,
                "secret": self._config.secret,
            },
        }
        await self._ws.send(json.dumps(subscribe_msg))

        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
        except asyncio.TimeoutError:
            print("[ws_bot] subscribe timeout", file=sys.stderr)
            return False

        resp = json.loads(raw)
        if resp.get("errcode") == 0:
            self._state = ConnectionState.CONNECTED
            return True
        else:
            print(
                f"[ws_bot] subscribe failed: errcode={resp.get('errcode')} "
                f"errmsg={resp.get('errmsg')}",
                file=sys.stderr,
            )
            return False

    async def _listen_loop(self) -> None:
        """持续接收消息并分发。"""
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            cmd = msg.get("cmd", "")
            req_id = msg.get("headers", {}).get("req_id", "")

            # pong 响应：匹配 cmd=="pong" 或无 cmd 但 req_id 是心跳的
            if cmd == "pong":
                self._pong_event.set()
                continue

            # 匹配 pending response（包括心跳 ping 的 req_id 响应）
            if req_id and req_id in self._pending_responses:
                fut = self._pending_responses.pop(req_id)
                if not fut.done():
                    fut.set_result(msg)
                continue

            # 消息回调
            if cmd == "aibot_msg_callback" and self._on_message:
                asyncio.create_task(self._on_message(msg))
                continue

            # 事件回调
            if cmd == "aibot_event_callback" and self._on_event:
                asyncio.create_task(self._on_event(msg))
                continue

            # 未识别的消息，打印调试日志
            print(f"[ws_bot] unhandled msg: cmd={cmd} req_id={req_id}", file=sys.stderr)

    async def _heartbeat_loop(self) -> None:
        """每 25s 发应用层 JSON ping，通过 pending_responses 追踪 pong。

        企微服务端对 JSON ping 的响应会带回相同 req_id，
        我们将其注册到 _pending_responses 中以可靠检测 pong。
        同时兼容 cmd=="pong" 的响应格式。
        """
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            self._pong_event.clear()
            req_id = str(uuid.uuid4())
            ping_msg = {
                "cmd": "ping",
                "headers": {"req_id": req_id},
            }

            # 注册到 pending_responses，这样 listen_loop 收到带此 req_id 的
            # 任何响应都会 set pong_event
            loop = asyncio.get_running_loop()
            pong_future: asyncio.Future = loop.create_future()
            self._pending_responses[req_id] = pong_future

            try:
                await self._ws.send(json.dumps(ping_msg))
            except (ConnectionClosed, OSError):
                self._pending_responses.pop(req_id, None)
                break

            # 等待 pong：要么 cmd=="pong" 触发 _pong_event，
            # 要么 pending_response 被 listen_loop resolve
            try:
                done, _ = await asyncio.wait(
                    [
                        asyncio.ensure_future(self._pong_event.wait()),
                        pong_future,
                    ],
                    timeout=self.PONG_TIMEOUT,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    # 超时
                    raise asyncio.TimeoutError()
            except asyncio.TimeoutError:
                print("[ws_bot] pong timeout, reconnecting", file=sys.stderr)
                self._pending_responses.pop(req_id, None)
                try:
                    await self._ws.close()
                except Exception:
                    pass
                break
            finally:
                self._pending_responses.pop(req_id, None)
                if not pong_future.done():
                    pong_future.cancel()

    def _calc_backoff(self, attempt: int) -> float:
        """指数退避 + ±20% 抖动，封顶 30s。"""
        base = min(2 ** attempt, self.MAX_BACKOFF)
        jitter = base * random.uniform(-0.2, 0.2)
        return base + jitter
