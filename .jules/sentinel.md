## 2025-05-15 - Fail-Fast Environment Validation and Defensive Typing
**Vulnerability:** Application crashes or insecure behavior due to missing environment variables or malformed data from external sources (RSS/LLM).
**Learning:** External data should never be assumed to be of a specific type (e.g., LLMs might return non-strings). Missing env vars should be caught at startup rather than mid-execution.
**Prevention:** Implement a `validate_env()` function to check all required secrets and basic formats at boot. Use `isinstance(var, str)` and explicit `str()` conversion for all dynamic data before processing for HTML.
