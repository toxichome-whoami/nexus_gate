import asyncio
import time
import httpx
import sys
import statistics

async def worker(client: httpx.AsyncClient, url: str, headers: dict, payload: dict, stats: dict, semaphore: asyncio.Semaphore):
    async with semaphore:
        start = time.time()
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=5.0)
            if resp.status_code == 200:
                stats["success"] += 1
            else:
                stats.setdefault("errors", {})
                stats["errors"][resp.status_code] = stats["errors"].get(resp.status_code, 0) + 1
                stats["failed"] += 1
        except Exception as e:
            stats["failed"] += 1
            stats.setdefault("exceptions", [])
            stats["exceptions"].append(str(e))
            
        stats["latency"].append((time.time() - start) * 1000)

async def main():
    if len(sys.argv) < 2:
        print("Usage: python bench_db.py <BEARER_TOKEN> [concurrency] [total_requests]")
        print("Note: <BEARER_TOKEN> should be the base64 encoded 'name:secret'")
        return
        
    api_key = sys.argv[1]
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    total_requests = int(sys.argv[3]) if len(sys.argv) > 3 else 500
    
    url = "http://127.0.0.1:4500/api/db/main_db/query"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"sql": "SELECT 1", "params": {}}
    
    print(f"--- NexusGate DB Benchmark ---")
    print(f"Target: {url}")
    print(f"Concurrency: {concurrency}")
    print(f"Total Requests: {total_requests}")
    
    stats = {"success": 0, "failed": 0, "latency": []}
    semaphore = asyncio.Semaphore(concurrency)
    
    start_time = time.time()
    
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=concurrency)) as client:
        tasks = [worker(client, url, headers, payload, stats, semaphore) for _ in range(total_requests)]
        await asyncio.gather(*tasks)
    
    duration = time.time() - start_time
    
    print("\n--- Results ---")
    print(f"Total Time: {duration:.2f}s")
    print(f"Successful: {stats['success']}")
    print(f"Failed:     {stats['failed']}")
    
    if stats["latency"]:
        print(f"Avg Latency:  {statistics.mean(stats['latency']):.2f}ms")
        print(f"Min Latency:  {min(stats['latency']):.2f}ms")
        print(f"Max Latency:  {max(stats['latency']):.2f}ms")
        print(f"P95 Latency:  {statistics.quantiles(stats['latency'], n=20)[18]:.2f}ms")
        
    print(f"Requests/sec: {total_requests / duration:.2f}")

    if "errors" in stats:
        print("\n--- Error Codes ---")
        for code, count in stats["errors"].items():
            print(f"HTTP {code}: {count}")

if __name__ == "__main__":
    asyncio.run(main())
