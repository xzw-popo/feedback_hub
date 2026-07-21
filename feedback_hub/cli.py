"""统一 CLI 入口：python -m feedback_hub.cli {pull,tag,serve,push}

子命令：
    pull   ：调 puller.pull 拉一段时间窗口
    tag    ：调 pipeline.run_tagging 跑一轮打标
    serve  ：用 uvicorn 启 FastAPI 服务
    push   ：生成 Top 5 Bug 候选并推送企微（spec 阶段 2）

时间窗口（同旧 pull_openapi.py，参考语义不复用代码）：
    --start-ts <unix秒>   --end-ts <unix秒>
    --start "YYYY-MM-DD HH:MM[:SS]"   --end "..."
    --last 30m | 2h | 1d
    --date 2026-05-18
    （默认）最近 24h
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from feedback_hub import db
from feedback_hub.config import (
    DEFAULT_CHANNEL,
    DEFAULT_SERVICE_VID,
    ensure_dirs,
)


_SAFE_CHANNEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_INCREMENTAL_WINDOW = timedelta(hours=6)
_MAX_BACKFILL_LOOKBACK = timedelta(days=366)
_MAX_BACKFILL_CHUNK = timedelta(days=1)


def _emit_json(payload: dict) -> None:
    """Write one compact object for commands intended for automation."""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _command_name(values: list[str]) -> str | None:
    if values[:2] == ["pipeline", "incremental"]:
        return "pipeline incremental"
    if values[:2] == ["ingest", "backfill"]:
        return "ingest backfill"
    if values[:2] == ["ingest", "pull"]:
        return "ingest pull"
    return None


def _safe_duration(spec: str, *, label: str, maximum: timedelta) -> timedelta:
    """Parse an automation duration without accepting NaN, zero, or huge jobs."""
    value = spec.strip().lower() if isinstance(spec, str) else ""
    matched = re.fullmatch(r"(\d+(?:\.\d+)?|\.\d+)([smhd])", value)
    if not matched:
        raise ValueError(f"{label} must be a positive duration using s/m/h/d")
    number = float(matched.group(1))
    unit = matched.group(2)
    seconds = number * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    if not seconds.is_integer() or seconds <= 0:
        raise ValueError(f"{label} must be a positive whole-second duration")
    duration = timedelta(seconds=int(seconds))
    if duration > maximum:
        raise ValueError(f"{label} exceeds the safe maximum")
    return duration


def _safe_channel(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_CHANNEL.fullmatch(value) or value in {".", ".."}:
        raise ValueError("channel must be a safe identifier")
    return value


def _result_payload(command: str, result, *, ok: bool) -> dict:
    payload = {"ok": ok, "command": command}
    payload.update(asdict(result))
    return payload


def _parse_dt_str(s: str) -> datetime:
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit(f"无法解析时间: {s}")


def _parse_last(spec: str) -> timedelta:
    spec = spec.strip().lower()
    if not spec:
        raise SystemExit("--last 不能为空")
    unit = spec[-1]
    try:
        n = float(spec[:-1])
    except ValueError:
        raise SystemExit(f"--last 解析失败: {spec}（示例：30m / 2h / 1d）")
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    if unit == "d":
        return timedelta(days=n)
    raise SystemExit(f"--last 单位不支持: {unit}（仅支持 s/m/h/d）")


def _resolve_window(args) -> tuple[datetime, datetime]:
    now = datetime.now()
    if args.start_ts and args.end_ts:
        return datetime.fromtimestamp(args.start_ts), datetime.fromtimestamp(args.end_ts)
    if args.start and args.end:
        return _parse_dt_str(args.start), _parse_dt_str(args.end)
    if args.last:
        delta = _parse_last(args.last)
        return now - delta, now
    if args.date:
        d = _parse_dt_str(args.date)
        return d.replace(hour=0, minute=0, second=0, microsecond=0), \
               d.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return now - timedelta(hours=24), now


def cmd_pull(args) -> int:
    from feedback_hub import puller

    ensure_dirs()
    s, e = _resolve_window(args)
    print(f"[pull] window = [{s} ~ {e}]")
    conn = db.connect()
    db.init_schema(conn)
    try:
        result = puller.pull(s, e, channel=args.channel,
                             service_vid=args.service_vid, conn=conn)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_ingest_pull(args) -> int:
    """The modern pull spelling keeps legacy ``pull`` output untouched."""
    from feedback_hub import puller

    try:
        if args.last is not None:
            _safe_duration(args.last, label="--last", maximum=_MAX_BACKFILL_LOOKBACK)
        _safe_channel(args.channel)
    except (ValueError, TypeError, OverflowError) as error:
        print(f"ingest pull argument error: {type(error).__name__}", file=sys.stderr)
        _emit_json({"ok": False, "command": "ingest pull", "error": "ArgumentError"})
        return 2
    try:
        ensure_dirs()
        start, end = _resolve_window(args)
        conn = db.connect()
        db.init_schema(conn)
        try:
            result = puller.pull(start, end, channel=args.channel,
                                 service_vid=args.service_vid, conn=conn)
        finally:
            conn.close()
    except Exception as error:
        print(f"ingest pull failed: {type(error).__name__}", file=sys.stderr)
        _emit_json({"ok": False, "command": "ingest pull", "status": "failed",
                    "error": type(error).__name__})
        return 1
    _emit_json({"ok": True, "command": "ingest pull", **result})
    return 0


def cmd_pipeline_incremental(args) -> int:
    """Run the locked pull-then-vector stage with a cron-safe JSON contract."""
    try:
        pull_window = _safe_duration(
            args.pull_window, label="--pull-window", maximum=_MAX_INCREMENTAL_WINDOW,
        )
        channel = _safe_channel(args.channel)
        # UTC avoids host-local timezone/DST arithmetic in scheduled jobs.
        now = datetime.now(timezone.utc)
        from feedback_hub.incremental_pipeline import (
            IncrementalPipelineError, PipelineAlreadyRunning, run_incremental,
        )
        try:
            result = run_incremental(now=now, pull_window=pull_window, channel=channel)
        except PipelineAlreadyRunning:
            print("incremental pipeline already running", file=sys.stderr)
            _emit_json({"ok": False, "command": "pipeline incremental", "status": "skipped_locked"})
            return 75
        except IncrementalPipelineError as error:
            print("incremental pipeline failed: vector stage", file=sys.stderr)
            _emit_json(_result_payload("pipeline incremental", error.result, ok=False))
            return 1
    except (ValueError, TypeError, OverflowError) as error:
        print(f"incremental pipeline argument error: {type(error).__name__}", file=sys.stderr)
        _emit_json({"ok": False, "command": "pipeline incremental", "error": "ArgumentError"})
        return 2
    except Exception as error:
        print(f"incremental pipeline failed: {type(error).__name__}", file=sys.stderr)
        _emit_json({"ok": False, "command": "pipeline incremental", "status": "failed",
                    "error": type(error).__name__})
        return 1
    payload = _result_payload("pipeline incremental", result, ok=result.status == "succeeded")
    payload["window_minutes"] = int(pull_window.total_seconds() // 60)
    _emit_json(payload)
    if result.status != "succeeded":
        print(f"incremental pipeline incomplete: {result.status}", file=sys.stderr)
        return 1
    return 0


def cmd_ingest_backfill(args) -> int:
    """Backfill source coverage only; it intentionally does not tag or vectorize."""
    try:
        lookback = _safe_duration(args.last, label="--last", maximum=_MAX_BACKFILL_LOOKBACK)
        chunk = _safe_duration(args.chunk, label="--chunk", maximum=_MAX_BACKFILL_CHUNK)
        channel = _safe_channel(args.channel)
        end = datetime.now(timezone.utc)
        start = end - lookback
        from feedback_hub.incremental_pipeline import backfill_coverage
        result = backfill_coverage(start=start, end=end, chunk=chunk, channel=channel)
    except (ValueError, TypeError, OverflowError) as error:
        print(f"ingest backfill argument error: {type(error).__name__}", file=sys.stderr)
        _emit_json({"ok": False, "command": "ingest backfill", "error": "ArgumentError"})
        return 2
    except Exception as error:
        print(f"ingest backfill failed: {type(error).__name__}", file=sys.stderr)
        _emit_json({"ok": False, "command": "ingest backfill", "status": "failed",
                    "error": type(error).__name__})
        return 1
    _emit_json({"ok": True, "command": "ingest backfill", **asdict(result)})
    return 0


def cmd_tag(args) -> int:
    from feedback_hub.tagger import pipeline

    ensure_dirs()
    conn = db.connect()
    db.init_schema(conn)
    try:
        # 离线模式：不连真实 LLM，未命中规则的一律标 '待定'（Bug 兜底，方便先跑通流水线）
        llm_call = None if args.online else (lambda text: "")
        # 当 llm_call 是 lambda return ""，label_parser 会回 'parse_error'，
        # _tag_one 会返回 source=llm + L1=待定。这正是离线兜底语义。
        stats = pipeline.run_tagging(conn, llm_call=llm_call, limit=args.limit)
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def cmd_push(args) -> int:
    """生成 Top 5 候选 → 写 push_log → 推送企微（除非 --dry-run）。

    （spec 阶段 2 §3 / §4 / §5）
    """
    import time as _time
    from datetime import datetime as _dt

    from feedback_hub import config
    from feedback_hub.pusher import candidate, formatter, push_log, webhook

    ensure_dirs()

    # ws 通道：转发到 ws_bot HTTP trigger
    if getattr(args, "via", "webhook") == "ws" and not args.dry_run and not args.no_send:
        import requests as _requests
        trigger_port = int(os.environ.get("WECOM_TRIGGER_PORT", "8081"))
        trigger_url = f"http://localhost:{trigger_port}/trigger"
        body = {"date": args.date} if args.date else {}
        try:
            resp = _requests.post(trigger_url, json=body, timeout=15)
            data = resp.json()
            if resp.status_code == 200:
                print(f"[push] via ws: {data}")
                return 0
            else:
                print(f"[push] via ws failed: {data}", file=sys.stderr)
                return 1
        except Exception as e:
            print(f"[push] via ws error: {e}", file=sys.stderr)
            return 1

    # webhook url 校验（dry-run 与 no-send 不需要）
    if not args.dry_run and not args.no_send:
        if not config.get_webhook_url():
            print(
                "[push] error: 环境变量 WECHAT_WEBHOOK_URL 未设置；"
                "如仅查看候选请用 --dry-run",
                file=sys.stderr,
            )
            return 2

    # push_date：默认今天，可通过 --date 覆盖
    if args.date:
        push_date = args.date
        # now_ms 用 push_date 的 23:59:59 → 让 7 天窗口对齐"补推那天"
        dt = _dt.strptime(push_date, "%Y-%m-%d")
        now_ms = int((dt.timestamp() + 86399) * 1000)
    else:
        now_ms = int(_time.time() * 1000)
        push_date = _dt.fromtimestamp(now_ms / 1000).strftime("%Y-%m-%d")

    conn = db.connect()
    db.init_schema(conn)
    try:
        result = candidate.generate_candidates(conn, now_ms=now_ms)
        top_groups = result["top_groups"]
        markdown = formatter.format_message(
            push_date=push_date,
            scanned=result["scanned"],
            all_groups=result["all_groups_count"],
            top_groups=top_groups,
        )

        if args.dry_run:
            print(markdown)
            return 0

        created_at = int(_time.time())
        push_ids = push_log.save_top_groups(
            conn, push_date=push_date,
            top_groups=top_groups, created_at=created_at,
        )

        if args.no_send:
            print(markdown)
            return 0

        ok = webhook.send_markdown(
            webhook_url=config.get_webhook_url(),
            markdown=markdown,
        )
        if ok:
            push_log.mark_delivered(
                conn, push_ids, delivered_at=int(_time.time()),
            )
            print(f"[push] delivered {len(push_ids)} rows for {push_date}")
            return 0
        else:
            print(
                "[push] webhook failed; push_log rows kept "
                "with delivered_at=NULL",
                file=sys.stderr,
            )
            return 1
    finally:
        conn.close()


def cmd_serve(args) -> int:
    import uvicorn

    ensure_dirs()
    conn = db.connect()
    db.init_schema(conn)
    conn.close()
    # CloudBase CloudRun 注入 PORT 环境变量，优先使用
    port = int(os.environ.get("PORT", args.port))
    host = os.environ.get("HOST", args.host)
    uvicorn.run("feedback_hub.api:app", host=host, port=port,
                reload=False, log_level="info")
    return 0


def cmd_vectors(args) -> int:
    """Load optional vector dependencies only for the nested vector command."""
    from feedback_hub.vector_index.commands import cmd_vectors as dispatch_vectors
    return dispatch_vectors(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="feedback_hub")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_pull_window_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--channel", default=DEFAULT_CHANNEL)
        parser.add_argument("--service-vid", type=int, default=DEFAULT_SERVICE_VID)
        parser.add_argument("--last")
        parser.add_argument("--date")
        parser.add_argument("--start")
        parser.add_argument("--end")
        parser.add_argument("--start-ts", type=int)
        parser.add_argument("--end-ts", type=int)

    pp = sub.add_parser("pull", help="拉取 OpenAPI 反馈并写库")
    add_pull_window_arguments(pp)
    pp.set_defaults(func=cmd_pull)

    pipeline_parser = sub.add_parser("pipeline", help="运行受锁保护的反馈增量流水线")
    pipeline_sub = pipeline_parser.add_subparsers(dest="pipeline_command", required=True)
    incremental = pipeline_sub.add_parser("incremental", help="拉取后增量写入缺失向量")
    incremental.add_argument("--pull-window", default="30m")
    incremental.add_argument("--channel", default=DEFAULT_CHANNEL)
    incremental.set_defaults(func=cmd_pipeline_incremental)

    ingest_parser = sub.add_parser("ingest", help="管理原始反馈拉取与覆盖回填")
    ingest_sub = ingest_parser.add_subparsers(dest="ingest_command", required=True)
    ingest_pull = ingest_sub.add_parser("pull", help="以自动化 JSON 契约拉取反馈")
    add_pull_window_arguments(ingest_pull)
    ingest_pull.set_defaults(func=cmd_ingest_pull)
    ingest_backfill = ingest_sub.add_parser("backfill", help="补齐可恢复的原始来源覆盖")
    ingest_backfill.add_argument("--last", required=True)
    ingest_backfill.add_argument("--chunk", default="6h")
    ingest_backfill.add_argument("--channel", default=DEFAULT_CHANNEL)
    ingest_backfill.set_defaults(func=cmd_ingest_backfill)

    pt = sub.add_parser("tag", help="对未打标的 feedback 跑一轮打标")
    pt.add_argument("--online", action="store_true",
                    help="开启则连真实 LLM；默认离线模式（无 LLM，未命中规则的标 '待定'）")
    pt.add_argument("--limit", type=int, default=None,
                    help="单次最多处理多少条；默认全部")
    pt.set_defaults(func=cmd_tag)

    ps = sub.add_parser("serve", help="启动 FastAPI 只读服务")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8000)
    ps.set_defaults(func=cmd_serve)

    pu = sub.add_parser("push", help="生成 Top 5 候选并推送企微")
    pu.add_argument("--dry-run", action="store_true",
                    help="只打印消息体，不写 push_log 也不推送")
    pu.add_argument("--no-send", action="store_true",
                    help="写 push_log 但不调 webhook（用于自动化校验）")
    pu.add_argument("--date", default=None,
                    help="指定推送日期 YYYY-MM-DD，默认今天")
    pu.add_argument("--via", choices=["webhook", "ws"], default="webhook",
                    help="推送通道：webhook（默认）或 ws（长连接，需 ws_bot 进程运行）")
    pu.set_defaults(func=cmd_push)

    vectors = sub.add_parser("vectors", help="管理反馈向量索引")
    vector_sub = vectors.add_subparsers(dest="vector_command", required=True)

    def add_vector_config(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--db", dest="db_path", help="反馈 SQLite 数据库路径")
        parser.add_argument("--data-dir", help="向量索引运行目录")
        parser.add_argument("--model-dir", help="本地 Qwen 模型目录")
        parser.add_argument("--index-name", help="向量索引名称")
        parser.add_argument("--model-version", help="增量索引模型版本")
        parser.add_argument("--dimension", type=int, help="向量维度（默认 1024）")
        parser.add_argument("--batch-size", type=int, help="模型批大小")
        parser.add_argument("--max-length", type=int, help="模型最大 token 数")
        parser.add_argument("--shard-size", type=int, help="每个不可变分片的向量数")
        parser.add_argument("--compact-after-shards", type=int, help="触发压缩的分片数")
        parser.add_argument("--host", help="兼容性参数；服务始终绑定 localhost")
        parser.add_argument("--port", type=int, help="localhost 服务端口")

    vs = vector_sub.add_parser("sync", help="增量写入缺失反馈向量")
    add_vector_config(vs)
    vs.add_argument("--max-items", type=int, help="本次最多编码多少条")

    vst = vector_sub.add_parser("status", help="输出活动索引健康状态")
    add_vector_config(vst)

    vr = vector_sub.add_parser("rebuild", help="构建并原子切换新的模型代次")
    add_vector_config(vr)
    vr.add_argument("--target-model-version", required=True, help="新的逻辑模型版本")
    vr.add_argument("--generation-id", required=True, help="稳定、可恢复的代次 ID")

    vc = vector_sub.add_parser("compact", help="压缩活动不可变分片")
    add_vector_config(vc)

    vsearch = vector_sub.add_parser("search", help="本地精确向量检索")
    add_vector_config(vsearch)
    vsearch.add_argument("--query", action="append", help="正向查询，可重复")
    vsearch.add_argument("--negative-query", action="append", help="负向查询，可重复")
    vsearch.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1-100")
    vsearch.add_argument("--start-ts-ms", type=int)
    vsearch.add_argument("--end-ts-ms", type=int)
    vsearch.add_argument("--platform", action="append")
    vsearch.add_argument("--channel", action="append")
    vsearch.add_argument("--version", action="append")
    vsearch.add_argument("--product", action="append")

    vserve = vector_sub.add_parser("serve", help="仅在 localhost 启动向量 API")
    add_vector_config(vserve)
    vserve.add_argument("--allow-empty-index", action="store_true",
                        help="仅用于受控 bootstrap；允许服务以未就绪状态启动")
    vserve.add_argument("--runtime-token", default="", help=argparse.SUPPRESS)

    vsmoke = vector_sub.add_parser("smoke-encode", help="编码一条文本以验证模型")
    add_vector_config(vsmoke)
    vsmoke.add_argument("--text", default="微信输入法向量编码健康检查")

    vectors.set_defaults(func=cmd_vectors)

    return p


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(values)
    except SystemExit as error:
        # Keep vector automation machine-readable even for argparse failures.
        # Existing command behavior intentionally remains argparse-native.
        if values and values[0] == "vectors" and error.code:
            print(json.dumps({"ok": False, "command": "vectors", "error": "ArgumentError"},
                             ensure_ascii=False, separators=(",", ":")))
            return int(error.code)
        command = _command_name(values)
        if command and error.code:
            _emit_json({"ok": False, "command": command, "error": "ArgumentError"})
            return int(error.code)
        raise
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
