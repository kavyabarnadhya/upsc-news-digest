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

## 2026-05-18 - [Optimization Trap: Overlapping Pattern Replacement]
**Learning:** Replacing regex with iterative `str.replace()` for overlapping patterns (e.g., 'GS-I' and 'GS-II') or when the replacement contains the search pattern is unsafe. It can cause duplicate bolding or corrupt existing HTML tags (e.g., matching 'GS-I' inside '<strong>GS-II</strong>').
**Action:** Use `re.sub` for correct token matching when dealing with overlapping patterns or HTML injection.

## 2026-06-12 - [Article Deduplication across multi-source feeds]
**Learning:** In applications aggregating news from multiple overlapping sources, the same article often appears in different feeds. Deduplicating these by URL () before LLM processing significantly reduces token costs and classification overhead.
**Action:** Implement a `seen_links` set during article collection phases (main and expansion) to filter out redundant content before it reaches the LLM or final rendering.

## 2026-06-12 - [Article Deduplication across multi-source feeds]
**Learning:** In applications aggregating news from multiple overlapping sources, the same article often appears in different feeds. Deduplicating these by URL (`link`) before LLM processing significantly reduces token costs and classification overhead.
**Action:** Implement a `seen_links` set during article collection phases (main and expansion) to filter out redundant content before it reaches the LLM or final rendering.

## 2026-06-13 - [Pre-calculating MIMEText body in email loops]
**Learning:** When sending individual emails to a large recipient list, re-creating the `MIMEText` body part inside the loop causes redundant encoding and memory allocations. Pre-calculating this part once and attaching it to each `MIMEMultipart` message provides a measurable speedup in email generation.
**Action:** Always pre-calculate static or common MIME parts outside of loops when sending bulk or multi-recipient emails.

## 2026-06-14 - [Consolidating CSS to reduce HTML payload]
**Learning:** In applications generating large HTML emails with repeated components (e.g., article cards), using repetitive inline styles significantly inflates the payload size. Moving these to a centralized `<style>` block using semantic CSS classes can reduce the HTML size by ~25%, which reduces both client-side rendering time and MIME encoding overhead.
**Action:** Always use CSS classes for redundant structural elements in HTML templates, keeping only dynamic properties (like theme colors) inline.

## 2026-06-28 - [Early Truncation for Text Processing]
**Learning:** When processing untrusted external data (like RSS summaries) that requires regex sanitization or HTML unescaping, performing string truncation *before* these operations is significantly more efficient than doing it after. In this codebase, moving the truncation to the top of `clean_text` resulted in a ~500x speedup for 1MB inputs by avoiding the processing of hundreds of thousands of characters that would ultimately be discarded.
**Action:** Implement 'early truncation' in all sanitization utilities to minimize CPU cycles spent on discarded data.

## 2026-06-29 - [Batching Heterogeneous Strings for Sanitization]
**Learning:** In high-frequency rendering loops, calling sanitization utilities (like `batch_process_text`) multiple times with small lists of different fields (titles, links, sources) introduces significant overhead from repeated list joins and regex initialization. Consolidating these into a single large batch, even when they represent different semantic fields, amortizes this overhead.
**Action:** Group heterogeneous string fields into the largest possible batches before processing, using index-based slicing or mapping to redistribute the results correctly.

## 2026-06-30 - [Pre-serializing MIMEText Template to Avoid Loop Re-encoding]
**Learning:** When sending individual emails to multiple recipients, even pre-calculating the MIMEText body outside the loop still incurs significant overhead inside the loop if those parts are attached to a newly created MIMEMultipart object on each iteration (due to repeated multipart boundary generation and serialization during `as_string()`). Re-structuring the message to a single MIMEText template (pre-serialized to a string once with Subject and From) and simply prepending the `To` header inside the loop yields a massive 100x speedup in MIME generation without losing email standards compliance.
**Action:** For single-body template-based multi-recipient emails, pre-serialize the MIMEText message with invariant headers once and prepend recipient-specific headers (like `To:`) inside the loop rather than rebuilding MIME objects.

## 2026-07-02 - [Fast-Path Search Before Regex Substitution & String Processing]
**Learning:** Calling `re.sub` or `str.replace` unconditionally in batch processing functions creates unnecessary string allocations and regex engine overhead for inputs that do not contain target characters. Checking for presence first (`CONTROL_CHAR_RE.search(text)` or checking delimiter count `joined.count("\x00") != len(texts) - 1`) provides a fast path that avoids redundant processing on clean inputs (~1.15x–1.20x speedup) while fully preserving security and sanitization invariants.
**Action:** Use fast-path search checks (`.search()` or presence checks) before running regex `sub` or list comprehensions with `str.replace` when processing high-volume text streams.

## 2026-07-03 - [Optimization Trap: Consolidating batch_process_text with mixed do_bold flags]
**Learning:** Attempting to consolidate two batch_process_text calls (one with do_bold=False and one with do_bold=True) into a single batch call in render_html resulted in a ~10% performance regression because re-joining and re-splitting the batch slice to apply regex bolding added more string allocation overhead than was saved by the single batch call.
**Action:** Keep batch calls separate when transformation flags (like do_bold) differ across string subsets.

## 2026-07-04 - [Optimization Trap: Micro-optimizations on Cold Paths and Pre-Checking `html.escape`]
**Learning:** Micro-optimizations on already-fast cold paths (e.g. replacing `.lower().startswith()` with pre-checks) degrade code readability without measurable benefit. Furthermore, adding `any(c in string ...)` before `html.escape` adds double-pass string scanning overhead whenever special characters exist.
**Action:** Avoid micro-optimizations on cold paths, and ensure fast-path checks do not penalize standard inputs containing special characters.

## 2026-07-04 - [Optimization Trap: Unrolling HTML F-String Literals to Single Lines]
**Learning:** Collapsing multi-line Python HTML f-string literals into dense, single-line strings degrades code maintainability and readability without yielding significant execution gains in Python. Readability must never be sacrificed for string formatting micro-optimizations.
**Action:** Preserve clean, indented multi-line string templates in Python rendering code and avoid micro-optimizations that harm maintainability.
