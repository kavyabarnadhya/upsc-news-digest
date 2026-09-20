## 2025-05-15 - Fail-Fast Environment Validation and Defensive Typing
**Vulnerability:** Application crashes or insecure behavior due to missing environment variables or malformed data from external sources (RSS/LLM).
**Learning:** External data should never be assumed to be of a specific type (e.g., LLMs might return non-strings). Missing env vars should be caught at startup rather than mid-execution.
**Prevention:** Implement a `validate_env()` function to check all required secrets and basic formats at boot. Use `isinstance(var, str)` and explicit `str()` conversion for all dynamic data before processing for HTML.

## 2026-05-06 - validate_env() Bug: Empty-String Handling in RECEIVER_EMAIL
**Incident:** The `validate_env()` function introduced in the Sentinel PR caused 3 consecutive daily workflow failures. The RECEIVER_EMAIL regex check iterated over all entries from `.split(",")` without filtering empty strings first. A trailing comma in the secret (e.g. `user@example.com,`) produces an empty string that fails the regex, raising a ValueError → exit(1).
**Fix:** Filter empty entries before validation: `[r.strip() for r in val.split(",") if r.strip()]`
**Lesson:** Always mirror the same split/filter logic used in `send_email()` when validating the same field. **Do NOT modify `validate_env()` or email-handling logic without running `python -m unittest test_parallel_logic.py` AND manually testing the validate_env paths.** For security-sensitive functions, add unit tests before and after changes.

## 2026-05-18 - Protocol Enforcement vs. Compatibility
**Vulnerability:** Risk of Local File Disclosure (LFD) or SSRF via `feedparser.parse()` and dangerous URI schemes (e.g., `javascript:`) in article links.
**Fix:** Implement strict protocol validation to allow only `http://` and `https://`.
**Learning:** Overly restrictive security (e.g., HTTPS-only) can break existing functionality in applications that rely on external legacy sources. While HTTPS is preferred, mandating it for all external feeds can cause regressions if the provider only supports HTTP.
**Prevention:** Use an allow-list of safe web protocols (`http`, `https`) rather than a single restrictive one, unless the environment is fully controlled.

## 2026-05-24 - AI-Driven Resource Exhaustion (DoS)
**Vulnerability:** Resource exhaustion via malformed or malicious LLM output containing duplicate article indices or excessively large JSON structures.
**Learning:** AI-generated content should be treated as untrusted input. Trusting indices returned by an LLM without deduplication and bounding can lead to unbounded iteration or payload growth.
**Fix:** Extracted LLM processing into `process_llm_articles()` with strict bounds: capped input iteration at 100, deduplicated indices via a `seen_indices` set, capped final article count at 50, and limited category topics to 20.
**Prevention:** Always implement hard bounds and deduplication when mapping untrusted identifiers (like indices) back to internal data structures.

## 2026-05-30 - LLM Role Separation for Injection Defense
**Vulnerability:** Prompt injection risk where untrusted article data could override system instructions if both are sent in a single 'user' message.
**Learning:** Sending instructions and untrusted data in the same message makes it easier for an attacker (or malformed input) to manipulate the model's behavior.
**Fix:** Separated persona and instructions into a 'system' message, while keeping the external article data in a 'user' message.
**Prevention:** Always use the 'system' role for static instructions and the 'user' role for dynamic/untrusted data to leverage the model's internal role-based priority and boundary enforcement.

## 2026-06-28 - Defense in Depth: CSP and Control Character Sanitization
**Vulnerability:** Risk of XSS in web-based email clients and potential logic errors/injection via malformed control characters in RSS feeds.
**Learning:** Even with HTML escaping, adding a CSP provides a critical second layer of defense. Sanitizing control characters early in the pipeline prevents them from reaching sensitive sinks (LLM, HTML renderer).
**Prevention:** Always implement CSP where possible and sanitize all untrusted text inputs for non-printable characters at the boundaries.

## 2026-07-05 - JSON-Structured Input for Prompt Injection Defense
**Vulnerability:** Risk of prompt injection where malicious or malformed RSS content could spoof custom delimiters (e.g., "--- Article X ---") to manipulate LLM classification or summary output.
**Learning:** Custom string delimiters are fragile and easily spoofed by untrusted content. LLMs are highly proficient at parsing standard structured formats like JSON, which provides a more robust structural boundary between instruction and data.
**Prevention:** Always use `json.dumps()` to wrap untrusted data arrays or objects before sending them to an LLM. Update system prompts to explicitly state that input will be provided in JSON format to reinforce structural expectations.

## 2026-07-12 - RSS Feed URL Whitelisting & Secure TLS Protocols
**Vulnerability:** Risk of Server-Side Request Forgery (SSRF), unauthorized outgoing network requests, or TLS protocol downgrade attacks on SMTP connections.
**Learning:** Even if the application currently uses a fixed list of static RSS feeds, dynamically fetched URL parameters are vulnerable if downstream modules are modified. In addition, default SSL contexts can sometimes allow weak legacy protocols depending on the client system configuration.
**Prevention:** Define a strict whitelisted set of URLs (`ALLOWED_FEEDS`) at module load time and reject any feeds outside this set. Enforce a minimum TLS version of TLSv1.2 (`context.minimum_version = ssl.TLSVersion.TLSv1_2`) explicitly on mail server contexts to prevent downgrade attacks.

## 2026-07-19 - Safe RSS Data Fetching and Resource Exhaustion (DoS) Mitigation
**Vulnerability:** Denial of Service (DoS) / Out-Of-Memory (OOM) crashes if external RSS feed servers return excessively large payloads or infinite streams during fetching.
**Learning:** `feedparser.parse(url)` fetches URLs using raw, unbounded urllib requests without response content length limits. Passing raw URLs to third-party parsing libraries exposes the application to resource exhaustion or decompression bomb payloads.
**Prevention:** Explicitly fetch RSS feed data using a custom `urllib.request` handler. Set an explicit network `timeout`, enforce a strict content length limit, truncate/verify stream data reading, use a secure SSL/TLS context enforcing `TLSv1.2` minimum, and pass parsed byte payloads and headers to `feedparser`.

## 2026-07-26 - SSRF via Open Redirect on Whitelisted Domains
**Vulnerability:** Server-Side Request Forgery (SSRF) bypass because `urllib.request` automatically follows HTTP/HTTPS redirects. Whitelisting the initial feed URLs does not prevent the remote server from redirecting the fetcher to internal/private resources or non-whitelisted domains.
**Learning:** Whitelisting domains or URLs at the client level before triggering requests is insufficient when the underlying HTTP library automatically follows redirects. Redirect targets must be validated with the same security checks as the initial request.
**Prevention:** Subclass `urllib.request.HTTPRedirectHandler` to intercept redirects and validate that the redirected URL (`newurl`) has a safe scheme (HTTP/HTTPS) and is present in the `ALLOWED_FEEDS` whitelist. Register this handler using `urllib.request.build_opener`.

## 2026-08-02 - Relative Redirect Resolution in Open Redirect Security Handlers
**Vulnerability:** Application rejection or protocol validation failures when RSS feed servers return relative redirect URLs (e.g. `/rss/news`), because relative URLs do not start with `http://` or `https://` until resolved against the request base URL.
**Learning:** `urllib.request` passes raw `Location` header values to `redirect_request()`, which can be relative paths. Security handlers that enforce protocol schemes or domain whitelists on redirect targets must resolve relative paths against `req.full_url` prior to validation.
**Prevention:** Use `urllib.parse.urljoin(req.full_url, newurl)` inside custom `HTTPRedirectHandler` implementations before evaluating scheme or domain whitelists.

## 2026-08-09 - Order of Execution: Sanitization Before Length Truncation
**Vulnerability:** Truncating raw input text to `max_len` before applying regex HTML tag stripping (`TAG_RE.sub`) allows malformed/unclosed HTML tags to bypass tag removal when the tag's closing `>` falls beyond the truncation boundary.
**Learning:** Performing length truncation prior to regex tag stripping can split an HTML tag midway, removing the closing bracket `>`. The regex `r'<[^>]+>'` fails to match the unclosed tag fragment (e.g., `<iframe src="http://malicious`), which is then emitted raw into downstream outputs.
**Prevention:** Always perform HTML entity unescaping, tag stripping, and control character removal on input text (bounded defensive length) *before* applying final length slicing `text[:max_len]`.
