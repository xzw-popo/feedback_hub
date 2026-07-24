#!/usr/bin/env python3
"""Standard-library client for the internal feedback topic-mining API."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit


TIMEOUT_SECONDS = 30
DEFAULT_BASE_URL = "http://charvelxia-any2.devcloud.woa.com:8000"


class LocalError(Exception):
    pass


class TransportError(Exception):
    pass


class _ClientArgumentParser(argparse.ArgumentParser):
    """Route parse failures through the same redacted local-error boundary."""

    def error(self, message: str) -> None:
        raise LocalError(message)


_SAFE_PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _redact(text: str, token: str) -> str:
    return text.replace(token, "[REDACTED]") if token else text


def _base_url(value: str | None) -> str:
    if value is not None:
        if not value.strip():
            raise LocalError("API base URL must not be blank")
        base_url = value
    else:
        environment_url = os.environ.get("FEEDBACK_TOPIC_API_URL", "")
        base_url = environment_url if environment_url.strip() else DEFAULT_BASE_URL
    try:
        parsed = urlsplit(base_url)
        # Accessing port forces urllib to validate malformed netloc values.
        parsed.port
    except ValueError as error:
        raise LocalError("API base URL must be a valid absolute HTTP(S) URL") from error
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise LocalError("API base URL must be an absolute HTTP(S) URL without query or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/api/topic-mining", "", ""))


def _read_json(path: str, *, require_list: bool = False) -> Any:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalError(f"cannot read JSON file: {Path(path).name}") from error
    if require_list and not isinstance(value, list):
        raise LocalError("override file must contain a JSON list")
    return value


def _request(
    base_url: str, token: str, method: str, path: str, payload: Any = None, *, expect_json: bool = True,
) -> tuple[dict[str, Any] | None, bytes]:
    try:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(base_url + path, data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException):
            detail = "response body unavailable"
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except TypeError as error:
        raise LocalError("request payload is not JSON serializable") from error
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as error:
        raise TransportError(str(error)) from error
    if not expect_json:
        return None, raw
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransportError("invalid JSON response") from error
    if not isinstance(response, dict):
        raise TransportError("invalid JSON response")
    return response, raw


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


def _safe_path_segment(value: str, label: str) -> str:
    if not _SAFE_PATH_SEGMENT.fullmatch(value):
        raise LocalError(f"{label} must be a single safe URL path segment")
    return value


def _page_offset(value: str) -> int:
    try:
        offset = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("page offset must be an integer") from error
    if offset < 0:
        raise argparse.ArgumentTypeError("page offset must be at least 0")
    return offset


def _candidate_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "candidate limit must be an integer",
        ) from error
    if not 1 <= limit <= 20:
        raise argparse.ArgumentTypeError(
            "candidate limit must be between 1 and 20",
        )
    return limit


def _positive_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("window days must be an integer") from error
    if days < 1:
        raise argparse.ArgumentTypeError("window days must be at least 1")
    return days


def _platform_contract(capabilities: Any) -> dict[str, Any]:
    if not isinstance(capabilities, dict):
        raise LocalError("backend platform contract is missing")
    scope_filters = capabilities.get("scope_filters")
    if not isinstance(scope_filters, dict):
        raise LocalError("backend platform contract is missing")
    platforms = scope_filters.get("platforms")
    if not isinstance(platforms, dict):
        raise LocalError("backend platform contract is missing")
    return platforms


def _classification_contract(capabilities: Any) -> dict[str, Any]:
    expected = {
        "version": 3,
        "owner": "caller_ai",
        "candidate_page_default": 20,
        "candidate_page_maximum": 20,
        "matched_evidence": "exact_candidate_substring",
        "partial_acceptance": True,
    }
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("classification_protocol") != expected
    ):
        raise LocalError("backend caller-AI classification protocol is unavailable")
    return expected


def _parser() -> argparse.ArgumentParser:
    parser = _ClientArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Override FEEDBACK_TOPIC_API_URL")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capabilities")
    prepare = subparsers.add_parser("prepare-spec")
    prepare.add_argument("--spec", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--window-days", type=_positive_days)
    create = subparsers.add_parser("create-run"); create.add_argument("--spec", required=True)
    get = subparsers.add_parser("get-run"); get.add_argument("run_id")
    resume = subparsers.add_parser("resume"); resume.add_argument("run_id")
    candidates = subparsers.add_parser("candidate-page"); candidates.add_argument("run_id"); candidates.add_argument("--output", required=True); candidates.add_argument("--offset", type=_page_offset, default=0); candidates.add_argument("--limit", type=_candidate_limit, default=20)
    classifications = subparsers.add_parser("apply-classifications"); classifications.add_argument("run_id"); classifications.add_argument("--page", required=True); classifications.add_argument("--file", required=True); classifications.add_argument("--repair", action="store_true")
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
    if command == "prepare-spec":
        capabilities, _ = _request(base_url, token, "GET", "/capabilities")
        _classification_contract(capabilities)
        freshness = capabilities.get("source_freshness") if isinstance(capabilities, dict) else None
        if not isinstance(freshness, dict) or freshness.get("ready") is not True:
            raise LocalError("source freshness is not ready")
        available_through = freshness.get("available_through")
        if not isinstance(available_through, str) or not available_through.strip():
            raise LocalError("source freshness has no valid available_through")
        default_days = capabilities.get("default_time_days")
        if isinstance(default_days, bool) or not isinstance(default_days, int) or default_days < 1:
            raise LocalError("backend default_time_days is invalid")
        window_days = arguments.window_days or default_days
        platform_contract = _platform_contract(capabilities)
        validator = Path(__file__).with_name("validate_topic_spec.py")
        completed = subprocess.run(
            [
                sys.executable, str(validator), arguments.spec,
                "--default-now", available_through,
                "--default-days", str(window_days),
                "--platform-contract-json",
                json.dumps(platform_contract, ensure_ascii=False, separators=(",", ":")),
                "--output", arguments.output,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise LocalError(completed.stderr.strip() or "topic spec preparation failed")
        try:
            prepared = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise LocalError("topic spec preparation returned invalid JSON") from error
        if not isinstance(prepared, dict):
            raise LocalError("topic spec preparation returned invalid JSON")
        return prepared
    if command == "create-run":
        capabilities, _ = _request(base_url, token, "GET", "/capabilities")
        _classification_contract(capabilities)
        response, _ = _request(base_url, token, "POST", "/runs", _read_json(arguments.spec))
        return response
    if command == "get-run":
        run_id = _safe_path_segment(arguments.run_id, "run id")
        response, _ = _request(base_url, token, "GET", f"/runs/{run_id}")
        return response
    if command == "resume":
        run_id = _safe_path_segment(arguments.run_id, "run id")
        response, _ = _request(base_url, token, "POST", f"/runs/{run_id}/resume")
        return response
    if command == "candidate-page":
        capabilities, _ = _request(base_url, token, "GET", "/capabilities")
        _classification_contract(capabilities)
        run_id = _safe_path_segment(arguments.run_id, "run id")
        query = urlencode({"offset": arguments.offset, "limit": arguments.limit})
        response, raw = _request(
            base_url, token, "GET",
            f"/runs/{run_id}/candidate-page?{query}",
        )
        _write_atomic(arguments.output, raw)
        return response
    if command == "apply-classifications":
        capabilities, _ = _request(base_url, token, "GET", "/capabilities")
        _classification_contract(capabilities)
        run_id = _safe_path_segment(arguments.run_id, "run id")
        validator = Path(__file__).with_name("validate_topic_decisions.py")
        decision_path = Path(arguments.file)
        prepared = decision_path.with_name(
            f".{decision_path.name}.{os.getpid()}.prepared.json",
        )
        try:
            command_line = [
                sys.executable, str(validator), "--page", arguments.page,
                "--file", arguments.file, "--output", str(prepared),
            ]
            if arguments.repair:
                command_line.append("--repair")
            completed = subprocess.run(
                command_line,
                text=True, capture_output=True, check=False,
            )
            if completed.returncode != 0:
                raise LocalError(
                    completed.stderr.strip()
                    or "topic decision validation failed",
                )
            payload = _read_json(str(prepared), require_list=True)
            response, _ = _request(
                base_url, token, "POST",
                f"/runs/{run_id}/classifications", payload,
            )
        finally:
            try:
                prepared.unlink(missing_ok=True)
            except OSError:
                pass
        return response
    if command == "verify":
        run_id = _safe_path_segment(arguments.run_id, "run id")
        response, _ = _request(base_url, token, "POST", f"/runs/{run_id}/verify")
        return response
    if command == "export":
        run_id = _safe_path_segment(arguments.run_id, "run id")
        response, _ = _request(base_url, token, "POST", f"/runs/{run_id}/export", {"format": arguments.format})
        return response
    if command == "download":
        run_id = _safe_path_segment(arguments.run_id, "run id")
        name = _safe_path_segment(arguments.artifact_name, "artifact name")
        _, raw = _request(base_url, token, "GET", f"/runs/{run_id}/artifacts/{name}", expect_json=False)
        _write_atomic(arguments.output, raw)
        return {"output": arguments.output, "run_id": run_id, "artifact_name": name}
    raise LocalError("unknown command")


def main(argv: list[str] | None = None) -> None:
    token = os.environ.get("FEEDBACK_TOPIC_API_TOKEN", "")
    try:
        arguments = _parser().parse_args(argv)
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
