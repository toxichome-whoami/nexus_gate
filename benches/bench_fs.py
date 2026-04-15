import asyncio
import time
import httpx
import sys
import statistics
from typing import Dict, Any, List

# ─────────────────────────────────────────────────────────────────────────────
# Request Handlers
# ─────────────────────────────────────────────────────────────────────────────

async def _perform_latency_request(
    client: httpx.AsyncClient, 
    url: str, 
    headers: Dict[str, str], 
    stats: Dict[str, Any]
) -> None:
    """Executes a single FS listing request and records performance metadata."""
    start_point = time.time()
    try:
        response = await client.get(url, headers=headers, timeout=5.0)
        
        latency_ms = (time.time() - start_point) * 1000
        stats["latency"].append(latency_ms)

        if response.status_code == 200:
            stats["success"] += 1
            return

        # Handle non-successful status codes
        stats.setdefault("errors", {})
        stats["errors"][response.status_code] = stats["errors"].get(response.status_code, 0) + 1
        stats["failed"] += 1

    except Exception as network_error:
        stats["failed"] += 1
        stats.setdefault("exceptions", []).append(str(network_error))
        stats["latency"].append((time.time() - start_point) * 1000)

async def _concurrency_gate(
    client: httpx.AsyncClient, 
    url: str, 
    headers: Dict[str, str], 
    stats: Dict[str, Any], 
    semaphore: asyncio.Semaphore
) -> None:
    """Entry gate for worker tasks to ensure concurrency constraints are met."""
    async with semaphore:
        await _perform_latency_request(client, url, headers, stats)

# ─────────────────────────────────────────────────────────────────────────────
# Analytics & Formatting
# ─────────────────────────────────────────────────────────────────────────────

def _display_header(url: str, concurrency: int, total_requests: int) -> None:
    """Prints the benchmark initialization block."""
    print(f"--- NexusGate FileSystem (FS) Performance Benchmark ---")
    print(f"Target Endpoint: {url}")
    print(f"Concurrency:     {concurrency}")
    print(f"Total Requests:  {total_requests}\n")

def _safe_calculate_p95(latencies: List[float]) -> float:
    """Helper to return P95 or Max depending on sample size logic."""
    if len(latencies) < 20:
        return max(latencies) if latencies else 0.0
    return statistics.quantiles(latencies, n=20)[18]

def _display_final_report(total_count: int, duration_sec: float, stats: Dict[str, Any]) -> None:
    """Generates a summary of the benchmark execution."""
    print("--- Performance Summary ---")
    print(f"Total Time:     {duration_sec:.2f}s")
    print(f"Success Count:  {stats['success']}")
    print(f"Failure Count:  {stats['failed']}")
    print(f"Throughput:     {total_count / duration_sec:.2f} req/sec")

    latencies = stats["latency"]
    if latencies:
        print(f"\nLatency Statistics:")
        print(f"  Average: {statistics.mean(latencies):.2f}ms")
        print(f"  Min/Max: {min(latencies):.2f}ms / {max(latencies):.2f}ms")
        print(f"  P95:     {_safe_calculate_p95(latencies):.2f}ms")

    if "errors" in stats:
        print("\nHTTP Error Breakdown:")
        for status_code, occurrences in stats["errors"].items():
            print(f"  Status {status_code}: {occurrences}")

# ─────────────────────────────────────────────────────────────────────────────
# Application Flow
# ─────────────────────────────────────────────────────────────────────────────

def _get_runtime_params():
    """Validates CLI input and returns a structured configuration."""
    if len(sys.argv) < 2:
        print("Usage: python bench_fs.py <BEARER_TOKEN> [concurrency] [total_requests]")
        print("Example: python bench_fs.py 'YWRtaW46c2VjcmV0' 100 1000")
        sys.exit(1)

    return {
        "api_key": sys.argv[1],
        "concurrency": int(sys.argv[2]) if len(sys.argv) > 2 else 50,
        "total_requests": int(sys.argv[3]) if len(sys.argv) > 3 else 500
    }

async def execute_bench_suite():
    params = _get_runtime_params()
    
    # Target endpoint for FS listing performance
    target_url = "http://127.0.0.1:4500/api/fs/local_fs/list?path=/"
    headers = {"Authorization": f"Bearer {params['api_key']}"}
    
    _display_header(target_url, params["concurrency"], params["total_requests"])
    
    bench_stats = {"success": 0, "failed": 0, "latency": []}
    limit_gate = asyncio.Semaphore(params["concurrency"])
    
    start_time = time.time()
    
    # Configure high-performance async client with specific connection limits
    http_limits = httpx.Limits(max_connections=params["concurrency"])
    async with httpx.AsyncClient(limits=http_limits) as client:
        work_load = [
            _concurrency_gate(client, target_url, headers, bench_stats, limit_gate) 
            for _ in range(params["total_requests"])
        ]
        await asyncio.gather(*work_load)
    
    execution_time = time.time() - start_time
    _display_final_report(params["total_requests"], execution_time, bench_stats)

if __name__ == "__main__":
    asyncio.run(execute_bench_suite())
