"""允许 `python -m feedback_hub` 时进入 CLI（与 cli.py 直接运行等价）。"""
from __future__ import annotations

from feedback_hub.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
