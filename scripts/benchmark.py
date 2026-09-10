import asyncio
import time
import statistics
import httpx

BASE_URL = "http://localhost:8000"
TOTAL_REQUESTS = 2000
CONCURRENCY = 50


async def create_target_url(client: httpx.AsyncClient) -> str:
    """Creates a sample shortened URL to benchmark."""
    res = await client.post(
        f"{BASE_URL}/api/v1/urls",
        json={"url": "https://example.com/production/load-test", "custom_alias": f"bench_{int(time.time())}"}
    )
    if res.status_code == 201:
        return res.json()["short_code"]
    elif res.status_code == 409:
        return f"bench_{int(time.time())}"
    else:
        raise RuntimeError(f"Failed creating target URL: {res.text}")


async def worker(client: httpx.AsyncClient, short_code: str, num_requests: int, latencies: list, status_codes: list):
    for _ in range(num_requests):
        t0 = time.perf_counter()
        try:
            r = await client.get(f"{BASE_URL}/r/{short_code}", follow_redirects=False)
            dt = (time.perf_counter() - t0) * 1000.0  # ms
            latencies.append(dt)
            status_codes.append(r.status_code)
        except Exception:
            status_codes.append(0)


async def run_benchmark():
    print(f"============================================================")
    print(f" Distributed URL Shortener - Production Load Benchmark")
    print(f" Total Requests: {TOTAL_REQUESTS} | Concurrency: {CONCURRENCY}")
    print(f"============================================================")

    async with httpx.AsyncClient(timeout=10.0) as setup_client:
        try:
            short_code = await create_target_url(setup_client)
            print(f"[+] Seeded target short code: '{short_code}'")
        except Exception as e:
            print(f"[-] Could not seed test URL: {e}")
            return

    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(limits=limits, timeout=10.0) as bench_client:
        latencies = []
        status_codes = []
        reqs_per_worker = TOTAL_REQUESTS // CONCURRENCY

        t_start = time.perf_counter()
        tasks = [
            asyncio.create_task(worker(bench_client, short_code, reqs_per_worker, latencies, status_codes))
            for _ in range(CONCURRENCY)
        ]
        await asyncio.gather(*tasks)
        total_time = time.perf_counter() - t_start

    successful = [c for c in status_codes if c == 302]
    rps = len(successful) / total_time

    latencies.sort()
    p50 = statistics.median(latencies) if latencies else 0
    p90 = latencies[int(len(latencies) * 0.90)] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

    print("\n------------------ BENCHMARK RESULTS ------------------")
    print(f" Duration:           {total_time:.2f} seconds")
    print(f" Throughput:         {rps:.1f} req/sec")
    print(f" Total Served:       {len(status_codes)} (Success: {len(successful)}, Errors: {len(status_codes) - len(successful)})")
    print(f" Latency p50:        {p50:.2f} ms")
    print(f" Latency p90:        {p90:.2f} ms")
    print(f" Latency p95:        {p95:.2f} ms")
    print(f" Latency p99:        {p99:.2f} ms")
    print("-------------------------------------------------------\n")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
