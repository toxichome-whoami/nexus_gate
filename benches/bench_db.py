import asyncio
import time
import httpx
import sys

async def worker(url: str, api_key: str, stats: dict):
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"sql": "SELECT 1", "params": {}}
    
    async with httpx.AsyncClient() as client:
        start = time.time()
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=2.0)
            if resp.status_code == 200:
                stats["success"] += 1
            else:
                stats["failed"] += 1
        except Exception:
            stats["failed"] += 1
            
        stats["latency"].append(time.time() - start)

async def main():
    if len(sys.argv) < 2:
        print("Usage: python bench_db.py <API_KEY>")
        return
        
    api_key = sys.argv[1]
    url = "http://127.0.0.1:4500/api/db/main_db/query"
    concurrency = 100
    
    print(f"Starting DB Benchmark. Concurrency: {concurrency}")
    stats = {"success": 0, "failed": 0, "latency": []}
    
    start = time.time()
    tasks = []
    for _ in range(concurrency):
        tasks.append(worker(url, api_key, stats))
        
    await asyncio.gather(*tasks)
    
    duration = time.time() - start
    avg_latency = sum(stats["latency"]) / len(stats["latency"]) * 1000 if stats["latency"] else 0
    
    print(f"Completed in {duration:.2f}s")
    print(f"Successful: {stats['success']}")
    print(f"Failed: {stats['failed']}")
    print(f"Average Latency: {avg_latency:.2f}ms")
    print(f"Requests per sec: {concurrency / duration:.2f}")

if __name__ == "__main__":
    asyncio.run(main())
