## 2026-04-05 - [Parallelizing RSS fetching]
**Learning:** Network-bound I/O operations like fetching multiple RSS feeds in a sequential loop are significant performance bottlenecks. Using `ThreadPoolExecutor` from `concurrent.futures` can provide a near-linear speedup relative to the number of concurrent feeds, especially when each feed has a non-trivial network latency.
**Action:** Always check for sequential network calls in loops and evaluate if they can be safely parallelized using thread pools or async/await patterns.

## 2026-04-12 - [Deduplicating multi-phase fetches and string optimization]
**Learning:** In applications with multi-phase data fetching (e.g., main pass + expansion pass), neglecting to track already-fetched resources can lead to significant redundant network I/O. Additionally, for large-scale text generation (prompts/HTML), Python's string concatenation (+=) is a measurable bottleneck compared to list-based joins.
**Action:** Use sets to track fetched URLs across phases and always use "".join() for constructing large dynamic content blocks.

## 2026-05-15 - [Truncating large external payloads early]
**Learning:** External data sources like RSS feeds can occasionally return massive payloads (e.g., full article text in a summary field). Processing these through regex or `html.unescape` can be expensive. Truncating the input to a reasonable upper bound *before* processing significantly reduces CPU cycles and prevents potential ReDoS or memory issues.
**Action:** Always truncate external string inputs to a safe maximum length before applying expensive transformations or regex.

## 2026-05-15 - [Pre-calculating static joined strings and sets]
**Learning:** Inline operations like `", ".join(sorted(TOPIC_COLORS.keys()))` inside an LLM prompt construction or `set(FEEDS.values())` inside a loop are redundant if the underlying data is static. Moving these to module-level constants improves performance by avoiding repeated allocations and computations.
**Action:** Identify loop-invariant or static-data-dependent strings/sets and pre-calculate them at the module level.
