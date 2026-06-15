"""定时推送调度 + HTTP 手动触发接口。"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine

from aiohttp import web


class PushScheduler:
    """管理定时推送和 HTTP 触发。"""

    def __init__(
        self,
        *,
        push_cron: str,
        push_fn: Callable[[str | None], Coroutine[Any, Any, dict]],
        trigger_port: int,
    ):
        self._push_cron = push_cron
        self._push_fn = push_fn
        self._trigger_port = trigger_port
        self._scheduler_task: asyncio.Task | None = None
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        """启动定时调度 + HTTP server。"""
        self._scheduler_task = asyncio.create_task(self._schedule_loop())
        if self._trigger_port > 0:
            app = self._build_app()
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            site = web.TCPSite(self._runner, "0.0.0.0", self._trigger_port)
            await site.start()
            print(
                f"[ws_bot] trigger server listening on port {self._trigger_port}",
                file=sys.stderr,
            )

    async def stop(self) -> None:
        """停止调度和 HTTP server。"""
        if self._scheduler_task:
            self._scheduler_task.cancel()
        if self._runner:
            await self._runner.cleanup()

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/trigger", self._handle_trigger)
        return app

    async def _handle_trigger(self, request: web.Request) -> web.Response:
        """POST /trigger 手动触发推送。"""
        print("[ws_bot] manual push triggered via HTTP", file=sys.stderr)
        try:
            body = await request.json() if request.content_length else {}
        except Exception:
            body = {}

        push_date = body.get("date")
        try:
            result = await self._push_fn(push_date)
            return web.json_response(result)
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            return web.json_response(
                {"status": "error", "detail": str(e)}, status=500,
            )

    async def _schedule_loop(self) -> None:
        """按 cron 配置定时触发推送。"""
        while True:
            delay = self._next_run_delay()
            print(
                f"[ws_bot] next scheduled push in {delay:.0f}s "
                f"({delay / 3600:.1f}h)",
                file=sys.stderr,
            )
            await asyncio.sleep(delay)
            print("[ws_bot] scheduled push triggered", file=sys.stderr)
            try:
                await self._push_fn(None)
            except Exception as e:
                print(f"[ws_bot] scheduled push failed: {e}", file=sys.stderr)

    def _next_run_delay(self) -> float:
        """解析简单 cron 'M H * * *' 格式，计算距下次触发的秒数。"""
        parts = self._push_cron.strip().split()
        if len(parts) < 2:
            return 3600.0

        try:
            minute = int(parts[0])
            hour = int(parts[1])
        except ValueError:
            return 3600.0

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        return (target - now).total_seconds()
