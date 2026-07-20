#!/usr/bin/env python3
"""Standard-library client for the internal feedback topic-mining API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


TIMEOUT_SECONDS = 30


class LocalError(Exception):
    pass


class TransportError(Exception):
    pass


def _redact(text: str, token: str) -> str:
    return text.replace(token, "[REDACTED]") if token else text


def _base_url(value: str | None) -> str:
    base_url = value or os.environ.get("FEEDBACK_TOPIC_API_URL", "")
    if not base_url.strip():
        raise LocalError("FEEDBACK_TOPIC_API_URL or --base-url is required")
    return base_url.rstrip("/") + "/api/topic-mining"


def _read_json(path: str, *, require_list: bool = False) -> Any:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalError(f"cannot read JSON file: {Path(path).name}") from error
    if require_list and not isinstance(value, list):
        raise LocalError("override file must contain a JSON list")
    return value


def _request(base_url: str, token: str, method: str, path: str, payload: Any = None) -> tuple[Any, bytes]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base_url + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, OSError) as error:
        raise TransportError(str(error)) from error
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, raw


def _write_atomic(path: str, raw: bytes) -> None:
    target = Path(path)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_bytes(raw)
        temporary.replace(target)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise LocalError(f"cannot write output: {target.name}") from error


def _safe_artifact_name(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise LocalError("artifact name must be a single filename")
    return name


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Override FEEDBACK_TOPIC_API_URL")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capabilities")
    create = subparsers.add_parser("create-run"); create.add_argument("--spec", required=True)
    get = subparsers.add_parser("get-run"); get.add_argument("run_id")
    review = subparsers.add_parser("review-queue"); review.add_argument("run_id"); review.add_argument("--output", required=True)
    overrides = subparsers.add_parser("apply-overrides"); overrides.add_argument("run_id"); overrides.add_argument("--file", required=True)
    verify = subparsers.add_parser("verify"); verify.add_argument("run_id")
    export = subparsers.add_parser("export"); export.add_argument("run_id"); export.add_argument("--format", choices=("xlsx", "jsonl"), required=True)
    download = subparsers.add_parser("download"); download.add_argument("run_id"); download.add_argument("artifact_name"); download.add_argument("--output", required=True)
    return parser


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    base_url = _base_url(arguments.base_url)
    token = os.environ.get("FEEDBACK_TOPIC_API_TOKEN", "")
    command = arguments.command
    if command == "capabilities":
        response, _ = _request(base_url, token, "GET", "/capabilities")
        return response
    if command == "create-run":
        response, _ = _request(base_url, token, "POST", "/runs", _read_json(arguments.spec))
        return response
    if command == "get-run":
        response, _ = _request(base_url, token, "GET", f"/runs/{arguments.run_id}")
        return response
    if command == "review-queue":
        response, raw = _request(base_url, token, "GET", f"/runs/{arguments.run_id}/review-queue")
        _write_atomic(arguments.output, raw)
        return response
    if command == "apply-overrides":
        response, _ = _request(base_url, token, "POST", f"/runs/{arguments.run_id}/overrides", _read_json(arguments.file, require_list=True))
        return response
    if command == "verify":
        response, _ = _request(base_url, token, "POST", f"/runs/{arguments.run_id}/verify")
        return response
    if command == "export":
        response, _ = _request(base_url, token, "POST", f"/runs/{arguments.run_id}/export", {"format": arguments.format})
        return response
    if command == "download":
        name = _safe_artifact_name(arguments.artifact_name)
        _, raw = _request(base_url, token, "GET", f"/runs/{arguments.run_id}/artifacts/{name}")
        _write_atomic(arguments.output, raw)
        return {"output": arguments.output, "run_id": arguments.run_id, "artifact_name": name}
    raise LocalError("unknown command")


def main(argv: list[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    token = os.environ.get("FEEDBACK_TOPIC_API_TOKEN", "")
    try:
        result = _run(arguments)
    except LocalError as error:
        print(_redact(str(error), token), file=sys.stderr)
        raise SystemExit(2)
    except RuntimeError as error:
        print(_redact(str(error), token), file=sys.stderr)
        raise SystemExit(3)
    except TransportError as error:
        print(_redact(str(error), token), file=sys.stderr)
        raise SystemExit(4)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
