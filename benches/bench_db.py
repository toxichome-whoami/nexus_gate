import asyncio
import time
import httpx
import sys
import statistics
from typing import Dict, Any, List

# ─────────────────────────────────────────────────────────────────────────────
# Worker Logic
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_single_request(
    client: httpx.AsyncClient, 
    url: str, 
    headers: Dict[str, str], 
    payload: Dict[str, Any], 
    stats: Dict[str, Any]
) -> None:
    """Performs a single SQL query request and updates the shared statistics."""
    start_timestamp = time.time()
    try:
        response = await client.post(url, headers=headers, json=payload, timeout=5.0)
        
        latency_ms = (time.time() - start_timestamp) * 1000
        stats["latency"].append(latency_ms)

        if response.status_code == 200:
            stats["success"] += 1
            return

        # Record non-200 HTTP statuses
        stats.setdefault("errors", {})
        stats["errors"][response.status_code] = stats["errors"].get(response.status_code, 0) + 1
        stats["failed"] += 1

    except Exception as exception:
        stats["failed"] += 1
        stats.setdefault("exceptions", []).append(str(exception))
        stats["latency"].append((time.time() - start_timestamp) * 1000)

async def _benchmark_processor(
    client: httpx.AsyncClient, 
    url: str, 
    headers: Dict[str, str], 
    payload: Dict[str, Any], 
    stats: Dict[str, Any], 
    concurrency_limit: asyncio.Semaphore
) -> None:
    """Wraps the request execution with semaphore-based concurrency control."""
    async with concurrency_limit:
        await _execute_single_request(client, url, headers, payload, stats)

# ─────────────────────────────────────────────────────────────────────────────
# Reporting & Analysis
# ─────────────────────────────────────────────────────────────────────────────

def _print_benchmark_header(url: str, concurrency: int, total_requests: int) -> None:
    """Outputs the initial benchmark configuration to the console."""
    print(f"--- NexusGate Database Performance Benchmark ---")
    print(f"Target Endpoint: {url}")
    print(f"Concurrency Level: {concurrency}")
    print(f"Total Requests: {total_requests}\n")

def _calculate_p95_latency(latencies: List[float]) -> float:
    """Computes the 95th percentile latency from a list of samples."""
    if len(latencies) < 20:
        return max(latencies) if latencies else 0.0
    return statistics.quantiles(latencies, n=20)[18]

def _report_results(total_requests: int, execution_duration: float, stats: Dict[str, Any]) -> None:
    """Aggregates and prints the final performance metrics."""
    print("--- Benchmark Results ---")
    print(f"Total Duration: {execution_duration:.2f}s")
    print(f"Successful Requests: {stats['success']}")
    print(f"Failed Requests:     {stats['failed']}")
    print(f"Throughput:          {total_requests / execution_duration:.2f} req/sec")

    latencies = stats["latency"]
    if latencies:
        print(f"\nLatency Profile:")
        print(f"  Average: {statistics.mean(latencies):.2f}ms")
        print(f"  Minimum: {min(latencies):.2f}ms")
        print(f"  Maximum: {max(latencies):.2f}ms")
        print(f"  P95:     {_calculate_p95_latency(latencies):.2f}ms")

    if "errors" in stats:
        print("\nDistribution of Error Codes:")
        for code, count in stats["errors"].items():
            print(f"  HTTP {code}: {count}")

# ─────────────────────────────────────────────────────────────────────────────
# Execution Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_cli_arguments():
    """Extracts and validates command-line parameters."""
    if len(sys.argv) < 2:
        print("Usage: python bench_db.py <BEARER_TOKEN> [concurrency] [total_requests]")
        print("Required: <BEARER_TOKEN> (Base64 encoded 'name:secret')")
        sys.exit(1)

    return {
        "api_key": sys.argv[1],
        "concurrency": int(sys.argv[2]) if len(sys.argv) > 2 else 50,
        "total_requests": int(sys.argv[3]) if len(sys.argv) > 3 else 500
    }

async def run_benchmark():
    config = _parse_cli_arguments()
    
    target_url = "http://127.0.0.1:4500/api/db/main_db/query"
    request_headers = {"Authorization": f"Bearer {config['api_key']}"}
    query_payload = {"sql": "SELECT 1", "params": {}}
    
    _print_benchmark_header(target_url, config["concurrency"], config["total_requests"])
    
    benchmark_stats = {"success": 0, "failed": 0, "latency": []}
    concurrency_limit = asyncio.Semaphore(config["concurrency"])
    
    overall_start_time = time.time()
    
    client_limits = httpx.Limits(max_connections=config["concurrency"])
    async with httpx.AsyncClient(limits=client_limits) as client:
        work_tasks = [
            _benchmark_processor(client, target_url, request_headers, query_payload, benchmark_stats, concurrency_limit) 
            for _ in range(config["total_requests"])
        ]
        await asyncio.gather(*work_tasks)
    
    total_duration = time.time() - overall_start_time
    _report_results(config["total_requests"], total_duration, benchmark_stats)

if __name__ == "__main__":
    asyncio.run(run_benchmark())
