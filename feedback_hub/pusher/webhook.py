"""企微群机器人 webhook 客户端（spec 阶段 2 §4.2）。

POST 失败时按指数退避重试 (1s / 2s / 4s)。
错误日志写到 stderr；不抛异常给上层（让上层根据返回值决定是否落 delivered_at）。
"""
from __future__ import annotations

import sys
import time

import requests

DEFAULT_TIMEOUT = 15.0


def send_markdown(
    *, webhook_url: str, markdown: str, max_retries: int = 3,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """POST markdown 消息到企微 webhook。

    Returns:
        True  = 至少一次返回 HTTP 200 且 errcode==0
        False = 所有重试都失败 / errcode != 0 / 网络异常
    """
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": markdown},
    }

    last_err = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=timeout)
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception:
                    body = {"errcode": -1, "errmsg": "non-json response"}
                if body.get("errcode") == 0:
                    return True
                last_err = (
                    f"errcode={body.get('errcode')} "
                    f"errmsg={body.get('errmsg')}"
                )
            else:
                last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_err = f"RequestException: {e}"
        except Exception as e:  # 防御性
            last_err = f"Unexpected: {type(e).__name__}: {e}"

        if attempt < max_retries - 1:
            backoff = 2 ** attempt  # 1s, 2s, 4s
            print(
                f"[webhook] attempt {attempt + 1} failed: {last_err}; "
                f"sleeping {backoff}s",
                file=sys.stderr,
            )
            time.sleep(backoff)

    print(f"[webhook] all {max_retries} attempts failed: {last_err}",
          file=sys.stderr)
    return False
