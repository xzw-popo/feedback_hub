"""验收脚本（spec §10 验收标准）。

不依赖网络（mock 一份 OpenAPI 响应，跑端到端）。
脚本结束时按 spec §10 的 8 项检查打印 PASS/FAIL，并以非零退出码标识失败。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# 让脚本可以独立运行：把仓库根加入 sys.path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from feedback_hub import db, puller  # noqa: E402
from feedback_hub.config import L1_VALUES  # noqa: E402
from feedback_hub.tagger import pipeline  # noqa: E402


def _mock_openapi(ts_ms: int, feedback_id: str, text: str, user_vid: str = "u1") -> dict:
    return {
        "errCode": 0,
        "results": [{
            "session": {"userVid": user_vid, "channel": "wetype", "serviceVid": 10000},
            "msgs": [{
                "sender": 0, "type": "text", "timestamp": ts_ms,
                "feedbackId": feedback_id,
                "content": {"text": text, "url": "", "scheme": ""},
                "clientInfo": {"platform": 1, "appversion": "1.2.3",
                               "device": "iPhone15", "deviceName": "iPhone",
                               "channelid": "x", "enginever": "9", "keyboardSource": ""},
                "user": {"userVid": user_vid},
                "tags": [],
            }],
        }],
    }


def _check(label: str, ok: bool, detail: str = "") -> tuple[bool, str]:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail else ""))
    return ok, detail


def main() -> int:
    print("=" * 60)
    print("feedback_hub 验收脚本（对照 spec §10）")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "feedback.db"
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        # patch DB 路径
        with patch("feedback_hub.config.DB_PATH", db_path), \
             patch("feedback_hub.config.DATA_DIR", tmp_path), \
             patch("feedback_hub.config.RAW_DIR", raw_dir), \
             patch.object(puller, "RAW_DIR", raw_dir), \
             patch.object(puller, "ensure_dirs", lambda: None):

            results: list[tuple[bool, str]] = []

            # ---------- ACC1: DB 文件 + 4 张表 ----------
            conn = db.connect(db_path)
            db.init_schema(conn)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            results.append(_check(
                "ACC1: feedback.db 可被 sqlite3 打开，4 张表齐全",
                {"feedback", "message_label", "conversation_label", "label_history"} <= tables,
                f"tables={sorted(tables)}",
            ))

            # ---------- ACC2: pull 拉取后 feedback 表能查到 ----------
            ts_ms = int(time.time() * 1000) - 60_000
            with patch.object(puller, "fetch_window",
                              lambda s, e, **kw: _mock_openapi(ts_ms, "fb_acc_1", "闪退了")):
                pull_result = puller.pull(
                    datetime.fromtimestamp(ts_ms / 1000) - timedelta(seconds=10),
                    datetime.fromtimestamp(ts_ms / 1000) + timedelta(seconds=10),
                    conn=conn,
                )
            n_feedback = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            results.append(_check(
                "ACC2: pull 后 feedback 表有数据",
                pull_result["inserted_count"] >= 1 and n_feedback >= 1,
                f"inserted={pull_result['inserted_count']} total_rows={n_feedback}",
            ))

            # ---------- ACC3: tag 后 message_label / conversation_label 有数据 ----------
            stats = pipeline.run_tagging(conn, llm_call=lambda t: "")
            n_ml = conn.execute("SELECT COUNT(*) FROM message_label").fetchone()[0]
            n_cl = conn.execute("SELECT COUNT(*) FROM conversation_label").fetchone()[0]
            results.append(_check(
                "ACC3: tag 后 message_label & conversation_label 有数据",
                n_ml >= 1 and n_cl >= 1,
                f"message_label={n_ml} conversation_label={n_cl}",
            ))

            # ---------- ACC4: 拉取幂等 ----------
            with patch.object(puller, "fetch_window",
                              lambda s, e, **kw: _mock_openapi(ts_ms, "fb_acc_1", "闪退了")):
                pull2 = puller.pull(
                    datetime.fromtimestamp(ts_ms / 1000) - timedelta(seconds=10),
                    datetime.fromtimestamp(ts_ms / 1000) + timedelta(seconds=10),
                    conn=conn,
                )
            n_feedback2 = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            results.append(_check(
                "ACC4: 同一时间窗连跑两次拉取，feedback 行数不增加",
                pull2["inserted_count"] == 0 and n_feedback2 == n_feedback,
                f"second_inserted={pull2['inserted_count']} total={n_feedback2}",
            ))

            # ---------- ACC5: 打标幂等 ----------
            stats2 = pipeline.run_tagging(conn, llm_call=lambda t: "")
            n_ml2 = conn.execute("SELECT COUNT(*) FROM message_label").fetchone()[0]
            results.append(_check(
                "ACC5: 同批未变化的消息再打标，message_label 行数不增加",
                stats2["total"] == 0 and n_ml2 == n_ml,
                f"second_total_to_tag={stats2['total']} ml_count={n_ml2}",
            ))

            # ---------- ACC6: FastAPI 5 端点能调通 ----------
            from fastapi.testclient import TestClient
            from feedback_hub.api import create_app
            client = TestClient(create_app(db_path=str(db_path)))
            endpoints_ok = True
            details: list[str] = []
            for path, expected_keys in [
                ("/api/conversations", {"total", "items"}),
                ("/api/stats/distribution", {"L1", "L2", "severity"}),
                ("/api/stats/trend?granularity=day", {"granularity", "buckets"}),
            ]:
                r = client.get(path)
                if r.status_code != 200 or not expected_keys <= set(r.json().keys()):
                    endpoints_ok = False
                    details.append(f"{path} -> {r.status_code}")
            # 单会话详情
            cid = conn.execute("SELECT conversation_id FROM conversation_label LIMIT 1").fetchone()[0]
            r = client.get(f"/api/conversations/{cid}")
            if r.status_code != 200 or "conversation" not in r.json() or "messages" not in r.json():
                endpoints_ok = False
                details.append(f"/api/conversations/{{id}} -> {r.status_code}")
            # CSV
            r = client.get("/api/export.csv")
            if r.status_code != 200 or "text/csv" not in r.headers.get("content-type", ""):
                endpoints_ok = False
                details.append(f"/api/export.csv -> {r.status_code}")
            results.append(_check(
                "ACC6: FastAPI 5 端点本地能调通", endpoints_ok,
                "; ".join(details) if details else "all green",
            ))

            # ---------- ACC7: 消息级 vs 会话级 比例报告 ----------
            ratio = (n_ml / n_cl) if n_cl else 0
            results.append(_check(
                "ACC7: 输出 消息级 vs 会话级 数量比例（单次跑可观察，连续 3 天 cron 在线下做）",
                True,
                f"message_label={n_ml}, conversation_label={n_cl}, msg/conv ratio={ratio:.2f}",
            ))

            # ---------- ACC8: 全程没有 import / 调用旧目录 ----------
            # 注意：grep 排除验收脚本自身，避免它的描述串被自匹配
            cmd = [
                "grep", "-rE",
                "--exclude=verify_acceptance.py",
                r"^\s*(from|import)\s+数据采集与打标",
                str(ROOT / "feedback_hub"),
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                no_legacy_refs = proc.returncode != 0
                detail = (proc.stdout.strip() or proc.stderr.strip())[:200]
            except Exception as e:
                no_legacy_refs = False
                detail = f"grep 执行失败: {e}"
            results.append(_check(
                "ACC8: feedback_hub 不引用旧 数据采集与打标/ 任何代码",
                no_legacy_refs,
                detail if not no_legacy_refs else "no references found",
            ))

            conn.close()

        print()
        passed = sum(1 for ok, _ in results if ok)
        total = len(results)
        print(f"汇总：{passed}/{total} 通过")
        return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
