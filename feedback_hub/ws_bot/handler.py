"""消息/事件回调处理 + chatid 自动发现。

当前仅实现 chatid 发现逻辑。后续扩展 B 能力时在此添加回复逻辑。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


class MessageHandler:
    """处理 aibot_msg_callback 和 aibot_event_callback。"""

    def __init__(self, *, chat_ids_file: Path):
        self._chat_ids_file = chat_ids_file
        self._chat_ids: set[str] = set()
        self._chat_types: dict[str, int] = {}  # chatid → chat_type

    @property
    def chat_ids(self) -> set[str]:
        return self._chat_ids

    def get_chat_type(self, chatid: str) -> int:
        """获取 chatid 对应的 chat_type，默认 2（群聊）。"""
        return self._chat_types.get(chatid, 2)

    def load_persisted(self) -> None:
        """启动时从文件加载已持久化的 chat_ids。"""
        if self._chat_ids_file.exists():
            try:
                data = json.loads(self._chat_ids_file.read_text(encoding="utf-8"))
                for cid in data.get("chat_ids", []):
                    self._chat_ids.add(cid)
                # 加载 chat_types
                for cid, ct in data.get("chat_types", {}).items():
                    self._chat_types[cid] = ct
            except (json.JSONDecodeError, IOError) as e:
                print(f"[ws_bot] failed to load chat_ids: {e}", file=sys.stderr)

    async def on_message(self, payload: dict) -> None:
        """处理 aibot_msg_callback。当前仅提取 chatid。"""
        body = payload.get("body", {})
        chatid = body.get("chatid", "")
        chat_type = body.get("chat_type", 2)
        if chatid:
            self._discover_chatid(chatid, chat_type)

    async def on_event(self, payload: dict) -> None:
        """处理 aibot_event_callback。当前仅处理 enter_chat 事件。"""
        body = payload.get("body", {})
        event_type = body.get("event_type", "")
        if event_type == "enter_chat":
            chatid = body.get("chatid", "")
            chat_type = body.get("chat_type", 2)
            if chatid:
                self._discover_chatid(chatid, chat_type)

    def _discover_chatid(self, chatid: str, chat_type: int = 2) -> None:
        """发现新 chatid 时记录并持久化。"""
        if chatid in self._chat_ids:
            return
        self._chat_ids.add(chatid)
        self._chat_types[chatid] = chat_type
        print(f"[ws_bot] discovered chatid: {chatid} (chat_type={chat_type})", file=sys.stderr)
        self._persist()

    def _persist(self) -> None:
        """将 chat_ids 写入 JSON 文件。"""
        self._chat_ids_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "chat_ids": sorted(self._chat_ids),
            "chat_types": self._chat_types,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._chat_ids_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
