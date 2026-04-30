#!/usr/bin/env python3
"""
NexusGate Filesystem (FS) Performance Benchmark
================================================
Standalone benchmark for the NexusGate FS listing endpoint.
Reads configuration from a .env file or CLI arguments.

Usage:
    # Using .env file (recommended)
    python benches/bench_fs.py

    # Using CLI arguments (backward compatible)
    python benches/bench_fs.py <BEARER_TOKEN> [concurrency] [total_requests]

.env file format (place in project root):
    API_URL=http://127.0.0.1:4500
    API_KEY=<your-base64-encoded-name:secret>
    FS_ALIAS=local_fs
    CONCURRENCY=50
    TOTAL_REQUESTS=500
"""

import asyncio
import os
import re
import statistics
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

# ─────────────────────────────────────────────────────────────────────────────
# .env Loader
# ─────────────────────────────────────────────────────────────────────────────


def _find_env_file() -> Optional[str]:
    """Locate the .env file relative to the project root or current directory."""
    search_dirs = [
        os.path.dirname(os.path.abspath(__file__)),  # benches/
        os.getcwd(),
    ]

    # Also try the parent of benches/
    parent = os.path.dirname(search_dirs[0])
    search_dirs.append(parent)

    for d in search_dirs:
        candidate = os.path.join(d, ".env")
        if os.path.isfile(candidate):
            return candidate

    # Try project root by walking up to find src/
    cwd = os.getcwd()
    while True:
        candidate = os.path.join(cwd, ".env")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cwd)
        if parent == cwd:  # reached filesystem root
            break
        cwd = parent

    return None


def _parse_env_file(path: str) -> Dict[str, str]:
    """Parse a simple KEY=VALUE .env file (comments and quoted values supported)."""
    env: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if match:
                key = match.group(1)
                value = match.group(2)
                # Strip surrounding quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                env[key] = value
    return env


def _load_config() -> Dict[str, Any]:
    """Load benchmark configuration from .env or CLI arguments."""
    env_file = _find_env_file()
    env: Dict[str, str] = {}

    if env_file:
        env = _parse_env_file(env_file)
        print(f"[config] Loaded .env from: {env_file}")
    else:
        print("[config] No .env file found — falling back to CLI arguments.")

    # ── API Base URL ────────────────────────────────────────────────────
    api_url = env.get("API_URL", "http://127.0.0.1:4500").rstrip("/")

    # ── API Key (Bearer Token) ──────────────────────────────────────────
    api_key = env.get("API_KEY", "")

    # ── FS target ───────────────────────────────────────────────────────
    fs_alias = env.get("FS_ALIAS", "local_fs")

    # ── Concurrency & Load ──────────────────────────────────────────────
    concurrency = int(env.get("CONCURRENCY", "50"))
    total_requests = int(env.get("TOTAL_REQUESTS", "500"))

    # ── CLI overrides (backward compatible) ─────────────────────────────
    if len(sys.argv) >= 2:
        api_key = sys.argv[1]
    if len(sys.argv) >= 3:
        concurrency = int(sys.argv[2])
    if len(sys.argv) >= 4:
        total_requests = int(sys.argv[3])

    if not api_key:
        print(
            "[error] No API key found! Provide it via .env (API_KEY=...) "
            "or as CLI argument: python bench_fs.py <BEARER_TOKEN>"
        )
        print("[info]  The API key is a Base64-encoded 'name:secret' string.")
        sys.exit(1)

    return {
        "api_url": api_url,
        "api_key": api_key,
        "fs_alias": fs_alias,
        "concurrency": concurrency,
        "total_requests": total_requests,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Workers
# ─────────────────────────────────────────────────────────────────────────────


async def _execute_fs_list(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    stats: Dict[str, Any],
) -> None:
    """Execute a single GET request against the FS list endpoint."""
    start = time.time()
    try:
        response = await client.get(url, headers=headers, timeout=10.0)
        latency_ms = (time.time() - start) * 1000
        stats["latency"].append(latency_ms)
        if response.status_code == 200:
            stats["success"] += 1
        else:
            stats.setdefault("errors", {})
            stats["errors"][response.status_code] = (
                stats["errors"].get(response.status_code, 0) + 1
            )
            stats["failed"] += 1
    except Exception as exc:
        stats["failed"] += 1
        stats.setdefault("exceptions", []).append(str(exc))
        stats["latency"].append((time.time() - start) * 1000)


async def _gate_worker(
    semaphore: asyncio.Semaphore,
    worker_coro,
) -> None:
    """Wrap a worker coroutine with a concurrency-limiting semaphore."""
    async with semaphore:
        await worker_coro


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark Runner
# ─────────────────────────────────────────────────────────────────────────────


async def _run_benchmark(
    target_url: str,
    headers: Dict[str, str],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a complete benchmark for the FS endpoint."""
    print(f"\n{'=' * 60}")
    print("  Benchmark: Filesystem (List Directory)")
    print(f"  Endpoint:  {target_url}")
    print(
        f"  Concurrency: {config['concurrency']}  |  Requests: {config['total_requests']}"
    )
    print(f"{'=' * 60}")

    stats: Dict[str, Any] = {"success": 0, "failed": 0, "latency": []}
    sem = asyncio.Semaphore(config["concurrency"])
    limits = httpx.Limits(max_connections=config["concurrency"])

    start_time = time.time()

    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [
            _gate_worker(sem, _execute_fs_list(client, target_url, headers, stats))
            for _ in range(config["total_requests"])
        ]
        await asyncio.gather(*tasks)

    duration = time.time() - start_time
    stats["duration"] = duration
    stats["throughput"] = config["total_requests"] / duration
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────


def _p95(latencies: List[float]) -> float:
    """95th percentile latency."""
    if not latencies:
        return 0.0
    if len(latencies) < 20:
        return max(latencies)
    return statistics.quantiles(latencies, n=20)[18]


def _print_results(total: int, stats: Dict[str, Any]) -> None:
    """Pretty-print the benchmark results."""
    dur = stats["duration"]
    print("\n  ── Filesystem (List Directory) Results ──")
    print(f"    Duration:       {dur:.2f}s")
    print(f"    Successful:     {stats['success']}")
    print(f"    Failed:         {stats['failed']}")
    print(f"    Throughput:     {stats['throughput']:.2f} req/sec")

    latencies = stats["latency"]
    if latencies:
        avg = statistics.mean(latencies)
        print(f"    Latency (avg):  {avg:.2f}ms")
        print(f"    Latency (min):  {min(latencies):.2f}ms")
        print(f"    Latency (max):  {max(latencies):.2f}ms")
        print(f"    Latency (P95):  {_p95(latencies):.2f}ms")

    if "errors" in stats:
        for code, count in stats["errors"].items():
            print(f"    HTTP {code}:      {count}")
    if "exceptions" in stats:
        print(f"    Exceptions:     {len(stats['exceptions'])}")
        for exc in stats["exceptions"][:3]:
            print(f"      ↳ {exc}")

    print(f"\n Benchmark complete — {stats['throughput']:.1f} req/sec")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────


async def main():
    """Load config, run the FS list benchmark, and print results."""
    config = _load_config()
    print("\n  NexusGate FS Performance Benchmark")
    print(f"  Base URL:  {config['api_url']}")
    print(f"  FS Alias:  {config['fs_alias']}")
    print(
        f"  Concurrency: {config['concurrency']}  |  Requests: {config['total_requests']}"
    )

    headers = {"Authorization": f"Bearer {config['api_key']}"}
    fs_url = f"{config['api_url']}/api/v1/fs/{config['fs_alias']}/list?path=/"

    stats = await _run_benchmark(
        target_url=fs_url,
        headers=headers,
        config=config,
    )

    _print_results(config["total_requests"], stats)


if __name__ == "__main__":
    asyncio.run(main())
