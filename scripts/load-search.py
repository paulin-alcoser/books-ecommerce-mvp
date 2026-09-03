#!/usr/bin/env python3
"""Hit GET /search for DURATION seconds and print latency / error summary."""
from __future__ import annotations

import argparse
import json
import statistics
import time
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def one_search(url: str, timeout: float) -> tuple[int, float]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status = resp.status
            resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, socket.timeout, TimeoutError):
        status = 0
    return status, (time.perf_counter() - started) * 1000


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Load-test Search API")
    parser.add_argument("--url", default="http://localhost:8001")
    parser.add_argument("--q", default="Harry Potter")
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    target = f"{args.url.rstrip('/')}/search?q={urllib.parse.quote(args.q)}&n={args.n}"
    print(
        f"load {target} for {args.duration}s  concurrency={args.concurrency}",
        flush=True,
    )

    latencies: list[float] = []
    statuses: dict[int, int] = {}
    deadline = time.time() + args.duration

    def worker() -> tuple[int, float]:
        return one_search(target, args.timeout)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        in_flight = {pool.submit(worker) for _ in range(args.concurrency)}
        while in_flight:
            done = next(as_completed(in_flight))
            in_flight.remove(done)
            status, ms = done.result()
            statuses[status] = statuses.get(status, 0) + 1
            latencies.append(ms)
            if time.time() < deadline:
                in_flight.add(pool.submit(worker))

    elapsed = args.duration
    total = len(latencies)
    errors = sum(count for code, count in statuses.items() if code != 200)
    summary = {
        "duration_s": elapsed,
        "concurrency": args.concurrency,
        "requests": total,
        "rps": round(total / elapsed, 2) if elapsed else 0,
        "errors": errors,
        "error_rate": round(errors / total, 4) if total else 0,
        "p50_ms": round(percentile(latencies, 50), 2),
        "p95_ms": round(percentile(latencies, 95), 2),
        "p99_ms": round(percentile(latencies, 99), 2),
        "avg_ms": round(statistics.mean(latencies), 2) if latencies else 0,
        "max_ms": round(max(latencies), 2) if latencies else 0,
        "statuses": statuses,
        "query": args.q,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
