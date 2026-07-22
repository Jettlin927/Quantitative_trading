#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_START_DATE = date(2010, 1, 1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过持久 Worker 分批回填实验级美股日线")
    parser.add_argument("--api-base", default="http://127.0.0.1:18000")
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--batch-delay-seconds", type=float, default=5.0)
    parser.add_argument("--validation-sample-size", type=int, default=30)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--retry-attempts", type=int, default=3)
    parser.add_argument("--retry-base-delay-seconds", type=float, default=15.0)
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--job-timeout-seconds", type=int, default=7200)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--skip-universe-refresh", action="store_true")
    parser.add_argument("--source-codes-file", type=Path)
    return parser.parse_args()


class ApiClient:
    def __init__(self, api_base: str, poll_seconds: int, timeout_seconds: int):
        self.api_base = api_base.rstrip("/")
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.api_base}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (HTTPError, URLError) as exc:
            detail = exc.read().decode("utf-8", errors="replace") if isinstance(exc, HTTPError) else str(exc)
            raise RuntimeError(f"{method} {path} 失败：{detail}") from exc

    def submit_and_wait(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        job = self.request("POST", "/api/sync-jobs", {"action": action, "payload": payload})
        job_id = str(job.get("id") or "")
        if not job_id:
            raise RuntimeError(f"{action} 入队响应缺少 id：{job}")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            job = self.request("GET", f"/api/sync-jobs/{job_id}")
            status = job.get("status")
            print(f"SYNC action={action} job_id={job_id} status={status}", flush=True)
            if status in {"ok", "partial", "failed"}:
                return job
            if status not in {"queued", "running"}:
                raise RuntimeError(f"{action} 返回未知状态：{status}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{action} 任务 {job_id} 超过 {self.timeout_seconds} 秒")
            time.sleep(self.poll_seconds)

    def current_source_codes(self) -> list[str]:
        offset = 0
        result: list[str] = []
        while True:
            query = urlencode({"current_only": "true", "limit": 1000, "offset": offset})
            page = self.request("GET", f"/api/us-experiment/instruments?{query}")
            items = page.get("items") or []
            result.extend(str(item["sourceCode"]) for item in items)
            offset += len(items)
            if offset >= int(page.get("total") or 0) or not items:
                return result


def deterministic_validation_sample(source_codes: list[str], as_of: date, size: int) -> set[str]:
    ranked = sorted(
        source_codes,
        key=lambda code: sha256(f"{as_of.isoformat()}:{code}".encode("utf-8")).hexdigest(),
    )
    return set(ranked[: max(size, 0)])


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def read_target_symbols(path: Path) -> list[str]:
    if not path.is_file():
        raise ValueError(f"显式目标名单文件不存在：{path}")
    symbols: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        symbol = raw_line.strip().upper()
        if not symbol or symbol.startswith("#"):
            continue
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,31}", symbol):
            raise ValueError(f"显式目标名单第 {line_number} 行不是有效美股 ticker：{symbol}")
        if symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise ValueError("显式目标名单不能为空")
    return sorted(symbols)


def default_checkpoint(start_date: date, end_date: date) -> Path:
    return Path("outputs/us-experiment-checkpoints") / f"{start_date.isoformat()}_{end_date.isoformat()}.json"


def contract_hash(start_date: date, end_date: date, source_codes: list[str]) -> str:
    payload = {
        "schema": 1,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "sourceCodes": source_codes,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def load_checkpoint(path: Path, expected_hash: str) -> dict[str, Any]:
    if not path.exists():
        return {"contractSha256": expected_hash, "completedSourceCodes": [], "failures": {}}
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("contractSha256") != expected_hash:
        raise RuntimeError(f"checkpoint 合同不匹配，请换用新文件：{path}")
    return checkpoint


def save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_args(args: argparse.Namespace) -> None:
    if args.start_date > args.end_date:
        raise ValueError("start-date 不能晚于 end-date")
    for name in ("batch_size", "retry_attempts", "retry_base_delay_seconds", "poll_seconds", "job_timeout_seconds"):
        if getattr(args, name) <= 0:
            raise ValueError(f"{name.replace('_', '-')} 必须大于 0")
    if args.batch_delay_seconds < 0:
        raise ValueError("batch-delay-seconds 不能为负数")
    if args.batch_size > 100:
        raise ValueError("batch-size 不能超过 API 合同上限 100")
    if args.validation_sample_size < 0 or args.max_symbols < 0:
        raise ValueError("validation-sample-size 和 max-symbols 不能为负数")
    if getattr(args, "source_codes_file", None) and args.skip_universe_refresh:
        raise ValueError("source-codes-file 与 skip-universe-refresh 不能同时使用")


def main() -> int:
    args = parse_args()
    validate_args(args)
    client = ApiClient(args.api_base, args.poll_seconds, args.job_timeout_seconds)
    source_codes_file = getattr(args, "source_codes_file", None)
    targeted_mode = source_codes_file is not None
    if targeted_mode:
        symbols = read_target_symbols(source_codes_file)
        universe_job = client.submit_and_wait("us_experiment_targeted_universe", {"symbols": symbols})
        if universe_job["status"] != "ok":
            raise RuntimeError(f"显式目标名单注册失败：{universe_job}")
        source_codes = list((universe_job.get("result") or {}).get("sourceCodes") or [])
    else:
        if not args.skip_universe_refresh:
            universe_job = client.submit_and_wait("us_experiment_universe", {})
            if universe_job["status"] != "ok":
                raise RuntimeError(f"美股目录刷新失败：{universe_job}")
        source_codes = client.current_source_codes()
    if args.max_symbols:
        source_codes = source_codes[: args.max_symbols]
    if not source_codes:
        raise RuntimeError("当前美股实验目录为空")

    checkpoint_path = args.checkpoint or default_checkpoint(args.start_date, args.end_date)
    frozen_hash = contract_hash(args.start_date, args.end_date, source_codes)
    checkpoint = load_checkpoint(checkpoint_path, frozen_hash)
    completed = set(checkpoint.get("completedSourceCodes") or [])
    failures = dict(checkpoint.get("failures") or {})
    validation_sample = (
        set()
        if targeted_mode
        else deterministic_validation_sample(source_codes, args.end_date, args.validation_sample_size)
    )
    saw_validation_alert = bool(checkpoint.get("validationAlertObserved"))
    batches = chunked(source_codes, args.batch_size)

    for batch_index, batch in enumerate(batches):
        pending = [code for code in batch if code not in completed]
        for attempt in range(1, args.retry_attempts + 1):
            if not pending:
                break
            attempted = list(pending)
            job = client.submit_and_wait(
                "us_experiment_prices",
                {
                    "start_date": args.start_date.isoformat(),
                    "end_date": args.end_date.isoformat(),
                    "source_codes": pending,
                    "validation_source_codes": [code for code in pending if code in validation_sample],
                },
            )
            result = job.get("result") or {}
            saw_validation_alert = saw_validation_alert or bool(
                result.get("validationAlerts") or result.get("validationErrors")
            )
            successful = set(result.get("successfulSourceCodes") or [])
            completed.update(successful)
            for code in successful:
                failures.pop(code, None)
            failed_items = result.get("failed") or []
            failed_codes = {
                str(item["sourceCode"])
                for item in failed_items
                if item.get("sourceCode") not in completed
            }
            pending = [code for code in attempted if code not in completed]
            for item in failed_items:
                failures[str(item.get("sourceCode"))] = str(item.get("error") or "unknown")
            if job.get("status") == "failed" or not result:
                error = str(result.get("error") or job.get("message") or "worker job failed")
                for code in pending:
                    failures[code] = error
            elif set(pending) - failed_codes:
                for code in set(pending) - failed_codes:
                    failures[code] = "任务未返回该代码的成功或失败事实"
            checkpoint.update(
                {
                    "contractSha256": frozen_hash,
                    "completedSourceCodes": sorted(completed),
                    "failures": failures,
                    "validationAlertObserved": saw_validation_alert,
                    "lastJobId": job.get("id"),
                    "lastJobStatus": job.get("status"),
                    "updatedAtEpoch": int(time.time()),
                }
            )
            save_checkpoint(checkpoint_path, checkpoint)
            if pending and attempt < args.retry_attempts:
                retry_delay = args.retry_base_delay_seconds * (2 ** (attempt - 1))
                print(
                    f"RETRY attempt={attempt + 1} pending={len(pending)} delay_seconds={retry_delay:g}",
                    flush=True,
                )
                time.sleep(retry_delay)
        if batch_index < len(batches) - 1 and args.batch_delay_seconds:
            print(f"RATE_LIMIT next_batch_delay_seconds={args.batch_delay_seconds:g}", flush=True)
            time.sleep(args.batch_delay_seconds)

    remaining = [code for code in source_codes if code not in completed]
    is_partial = bool(remaining) or saw_validation_alert
    # 只在整轮结束后排队一次重型聚合；普通前端读取始终消费该持久化快照。
    client.submit_and_wait("us_experiment_overview_refresh", {})
    status = "partial" if is_partial else "ok"
    print(
        f"FINISH status={status} universe={len(source_codes)} completed={len(completed)} "
        f"remaining={len(remaining)} checkpoint={checkpoint_path}",
        flush=True,
    )
    return 2 if is_partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
