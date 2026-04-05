## 2026-04-05 - [Parallelizing RSS fetching]
**Learning:** Network-bound I/O operations like fetching multiple RSS feeds in a sequential loop are significant performance bottlenecks. Using `ThreadPoolExecutor` from `concurrent.futures` can provide a near-linear speedup relative to the number of concurrent feeds, especially when each feed has a non-trivial network latency.
**Action:** Always check for sequential network calls in loops and evaluate if they can be safely parallelized using thread pools or async/await patterns.
