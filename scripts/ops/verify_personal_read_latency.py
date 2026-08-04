#!/usr/bin/env python3
"""重复读取个人工作台 HTTP 接口，并对 warm read 的 p95 做门禁。"""

from __future__ import annotations

import argparse
import json
import math
from statistics import mean
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


DEFAULT_PATHS = ("/api/personal/portfolio", "/api/personal/today")


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def read_once(base_url: str, path: str, timeout: float) -> float:
    started = time.monotonic()
    origin = f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
    request = Request(urljoin(base_url, path), headers={"Origin": origin})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} 返回 HTTP {response.status}")
        json.load(response)
    return time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", action="append", dest="paths")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--p95-limit", type=float, default=1.0)
    parser.add_argument("--request-timeout", type=float, default=2.0)
    args = parser.parse_args()
    if args.samples <= 0 or args.warmups < 0:
        parser.error("samples 必须大于 0，warmups 不能小于 0")

    failed = False
    for path in args.paths or DEFAULT_PATHS:
        for _ in range(args.warmups):
            read_once(args.base_url, path, args.request_timeout)
        durations = [
            read_once(args.base_url, path, args.request_timeout)
            for _ in range(args.samples)
        ]
        p95 = percentile(durations, 0.95)
        result = {
            "path": path,
            "samples": len(durations),
            "mean_seconds": round(mean(durations), 4),
            "p95_seconds": round(p95, 4),
            "limit_seconds": args.p95_limit,
            "passed": p95 < args.p95_limit,
        }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        failed = failed or not result["passed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
