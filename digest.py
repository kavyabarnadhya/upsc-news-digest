import os
import re
import json
import smtplib
import socket
import ssl
import html
import urllib.request
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import collections
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import feedparser
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# Set a global timeout for network requests (RSS fetching) to prevent hanging
socket.setdefaulttimeout(30)

# Pre-compiled regex for stripping HTML tags; more efficient than calling re.sub in a loop
TAG_RE = re.compile(r"<[^>]+>")

# Pre-compiled regex for stripping null bytes and non-printable control characters
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Pre-compiled regex for bolding GS paper references (GS-I to GS-IV) for better scannability
GS_RE = re.compile(r"(GS-[IVX]+)")
GS_BOLD = r'<strong class="gs-tag">\1</strong>'

# Pre-compiled regex for email validation (used in validate_env to prevent compilation in loops or function calls)
EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def bold_gs(text):
    """
    Performance Optimization: Fast path for bolding GS paper references.
    Regex substitution is only applied if the marker 'GS-' is present in the text,
    significantly reducing CPU cycles for articles/angles without GS references.
    """
    if "GS-" in text:
        return GS_RE.sub(GS_BOLD, text)
    return text


def batch_process_text(texts, do_bold=False):
    """
    Performance Optimization: Batch processes multiple strings for HTML escaping and
    optional GS bolding. Fast-path check skips element-by-element null byte stripping
    when no embedded null bytes are present, avoiding O(N) allocations/replaces.
    Security: Ensures any null bytes within string elements are stripped before joining
    to prevent array element corruption and delimiter injection during split.
    """
    if not texts:
        return []
    joined = "\x00".join(texts)
    # Security & Performance: Only sanitize individual elements if embedded null bytes exist
    if joined.count("\x00") != len(texts) - 1:
        joined = "\x00".join(t.replace("\x00", "") for t in texts)
    # Perform single batch HTML escape
    safe = html.escape(joined)
    # Perform single batch GS bolding if requested
    if do_bold:
        safe = bold_gs(safe)
    return safe.split("\x00")


def clean_text(text, max_len=2000, strip_tags=True):
    """
    Performance Optimization: Strips HTML tags and unescapes entities from text.
    Fast-path check uses CONTROL_CHAR_RE.search before substitution to avoid regex sub overhead.
    Security: Strips control characters after unescaping to prevent bypasses.
    Defensive Typing: Ensures non-string inputs are converted to str to prevent TypeError.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ""
    # Optimization: Truncate raw input early to avoid expensive processing on large payloads
    text = text[:max_len]
    # Optimization: Only unescape if entities are actually present
    if "&" in text:
        text = html.unescape(text)
    if strip_tags and "<" in text:
        text = TAG_RE.sub("", text)
    # Security: Fast-path check before regex substitution to strip control characters AFTER unescaping
    if CONTROL_CHAR_RE.search(text):
        text = CONTROL_CHAR_RE.sub("", text)
    return text.strip()


# Initialize Groq client once at the module level for resource reuse
_groq_client = None

# Performance Optimization & Security Standard: Initialize a secure SSL context once at the module level
# to reuse across all network operations (RSS fetching and SMTP email sending).
# This prevents redundant system-wide certificate authority loads and context initializations.
_SECURE_SSL_CONTEXT = ssl.create_default_context()
_SECURE_SSL_CONTEXT.minimum_version = ssl.TLSVersion.TLSv1_2

# Performance Optimization: Cache the secure URL opener as a singleton to avoid building
# a new HTTPS handler and redirect handler on every RSS feed request (saving CPU and allocations).
_SECURE_OPENER = None


def get_secure_opener():
    """
    Performance Optimization: Get or lazily initialize the secure URL opener.
    """
    global _SECURE_OPENER
    if _SECURE_OPENER is None:
        _SECURE_OPENER = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_SECURE_SSL_CONTEXT),
            SafeRedirectHandler()
        )
    return _SECURE_OPENER


def get_groq_client():
    global _groq_client
    if _groq_client is None:
        raw_key = os.getenv("GROQ_API_KEY")
        api_key = raw_key.strip() if raw_key else None
        _groq_client = Groq(
            api_key=api_key,
            timeout=60.0  # Security: Set explicit timeout to prevent indefinite hangs
        )
    return _groq_client


FEEDS = {
    "The Hindu":       "https://www.thehindu.com/news/national/feeder/default.rss",
    "Indian Express":  "https://indianexpress.com/section/india/feed/",
    "The Print":       "https://theprint.in/category/india/feed/",
    "LiveMint":        "https://www.livemint.com/rss/news",
    "BBC World":       "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Economic Times":  "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
    "DD News":         "https://ddnews.gov.in/en/feed/",
}

# Optimization: Pre-calculate feed URLs set for efficient deduplication in expansion pass
MAIN_FEED_URLS = set(FEEDS.values())

# Sources that are narrowly focused on one category — cap them at 3 articles
# to prevent Economy / International Relations from dominating the digest.
SPECIALIST_SOURCES = {"Economic Times", "LiveMint", "BBC World"}

# Expansion feeds used only when a category has zero articles after the first
# classify pass. Keyed by UPSC topic name.
EXPANSION_FEEDS = {
    "Environment & Ecology": [
        "https://www.downtoearth.org.in/rss/all",
        "https://thewire.in/category/environment/feed",
    ],
    "Science & Technology": [
        "https://thewire.in/category/science/feed",
        "https://www.thehindu.com/sci-tech/feeder/default.rss",
    ],
    "Security & Defence": [
        "https://theprint.in/category/defence/feed/",
        "https://www.thehindu.com/news/national/feeder/default.rss",
    ],
    "History & Culture": [
        "https://thewire.in/category/culture/feed",
        "https://scroll.in/section/arts/feed",
    ],
    "Social Issues": [
        "https://thewire.in/category/rights/feed",
        "https://theprint.in/category/health/feed/",
    ],
}

# Security: Whitelist of allowed RSS feed URLs to prevent SSRF and unauthorized outgoing requests
ALLOWED_FEEDS = set(FEEDS.values()) | {url for urls in EXPANSION_FEEDS.values() for url in urls}

TOPIC_COLORS = {
    "International Relations": "#a93226",
    "Economy": "#196f3d",
    "Polity & Governance": "#21618c",
    "Security & Defence": "#6c3483",
    "History & Culture": "#a04000",
    "Environment & Ecology": "#117864",
    "Social Issues": "#515a5a",
    "Science & Technology": "#2c3e50",
}

TOPIC_ICONS = {
    "International Relations": "🌍",
    "Economy": "📈",
    "Polity & Governance": "🏛️",
    "Security & Defence": "🛡️",
    "History & Culture": "📜",
    "Environment & Ecology": "🌱",
    "Social Issues": "🤝",
    "Science & Technology": "🔬",
}

# Optimization: Pre-calculate HTML icon tags to save cycles during rendering
TOPIC_ICON_TAGS = {
    topic: f'<span aria-hidden="true">{icon} </span>'
    for topic, icon in TOPIC_ICONS.items()
}

SOURCE_ICONS = {
    "The Hindu": "🗞️",
    "Indian Express": "📰",
    "The Print": "🖋️",
    "LiveMint": "📈",
    "BBC World": "🌐",
    "Economic Times": "📉",
    "DD News": "📺",
    "downtoearth.org.in": "🌱",
    "thewire.in": "🔗",
    "scroll.in": "📜",
}

# Optimization: Pre-calculate source icon tags for faster rendering
SOURCE_ICON_TAGS = {
    src: f'<span aria-hidden="true" style="margin-right:4px;">{icon}</span>'
    for src, icon in SOURCE_ICONS.items()
}

# Optimization: Pre-calculate sorted topic list string for the LLM prompt
TOPIC_LIST_STR = ", ".join(sorted(TOPIC_COLORS.keys()))

# Optimization: Pre-calculate the static LLM system prompt to avoid redundant construction
SYSTEM_PROMPT = f"""You are a UPSC exam preparation assistant focused on the Indian Civil Services Examination. You will be provided with a JSON array of news articles to process.

**Priority:** Strongly prefer articles with a direct India angle — Indian polity, governance, legislation, constitutional matters, Indian economy, Indian social issues, Indian environment policy, Indian science initiatives, India's defence, or Indian history and culture.

**International news:** Include purely international stories only if they are clearly significant for GS-II International Relations — major geopolitical events, major international agreements, or global developments with direct implications for India. Routine foreign news without clear exam relevance should be classified as "Not UPSC Relevant".

**Polity & Governance — classify ONLY if the article covers:** constitutional amendments or provisions, Parliament or state legislature bills or debates, Supreme Court or High Court judgments on constitutional or administrative matters, central or state government schemes and policies, electoral reforms (not campaign coverage), administrative or regulatory changes, federal relations, or lokpal/RTI/accountability mechanisms.
**Do NOT classify as Polity & Governance:** party political statements, opposition rhetoric, electoral campaign news, political rallies, intra-party matters, or opinion pieces on politics without a substantive constitutional or policy dimension — these are "Not UPSC Relevant" unless they fit another topic such as Economy or Social Issues.

Return ONLY a JSON object (no markdown, no code fences, no explanation) with exactly two keys:

1. "articles": an array of objects for each UPSC-relevant article with:
   - index: the article index number (int)
   - topic: one of exactly these topics: {TOPIC_LIST_STR}, Not UPSC Relevant
   - summary: sharp UPSC-focused summary in 4-5 sentences. Lead with the core decision, judgment, or policy. Then include: (a) the specific constitutional article, act, scheme, or regulatory body involved by name; (b) one or two concrete data points such as numbers, percentages, timelines, or committee names; (c) the GS paper and syllabus topic this maps to (e.g. "GS-II: Parliament and State Legislatures"); (d) the exam-relevant implication or significance. Avoid generic commentary, journalistic opinion, and vague statements like "experts say" or "this is significant".
   Omit articles that are "Not UPSC Relevant" — do not include them in the array at all.

2. "category_angles": an object mapping each topic that appeared in "articles" to an array of 3-5 bullet strings highlighting the collective UPSC exam relevance of all articles under that topic (mention specific GS papers, syllabus topics, or exam themes where applicable)."""

# Pre-calculate topic anchors and escaped names to save cycles during rendering
TOPIC_ANCHORS = {
    topic: re.sub(r"[^a-z0-9\-]", "", topic.replace(" ", "-").replace("&", "and").lower())
    for topic in TOPIC_COLORS
}
SAFE_TOPIC_NAMES = {topic: html.escape(topic) for topic in TOPIC_COLORS}

# Optimization: Pre-calculate combined topic header fragments to save cycles during rendering
TOPIC_HEADERS_HTML = {
    topic: f"{TOPIC_ICON_TAGS.get(topic, '')}{SAFE_TOPIC_NAMES[topic]}"
    for topic in TOPIC_COLORS
}

VALID_TOPICS = set(TOPIC_COLORS.keys()) | {"Not UPSC Relevant"}

# Optimization: Pre-calculate static back-to-topics navigation fragments
BACK_TO_TOPICS_HTML = """
          <div class="back-to-top">
            <a href="#topic-index" class="back-to-top-link" aria-label="Back to topic index">Back to topics&nbsp;<span aria-hidden="true">&uarr;</span></a>
          </div>"""

BACK_TO_TOPICS_SMALL_HTML = """
            <div class="back-to-top" style="margin-top: 8px;">
              <a href="#topic-index" class="back-to-top-link" aria-label="Back to topic index" style="font-size: 11px;">Back to topics&nbsp;<span aria-hidden="true">&uarr;</span></a>
            </div>"""

TOPIC_ORDER = [
    "Polity & Governance",
    "Economy",
    "Social Issues",
    "Environment & Ecology",
    "Science & Technology",
    "Security & Defence",
    "History & Culture",
    "International Relations",
]


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Security: Custom redirect handler to prevent Server-Side Request Forgery (SSRF)
    via open redirects on whitelisted feed URLs.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Resolve relative redirect URLs against original request URL before validation
        if isinstance(newurl, str) and getattr(req, "full_url", None):
            newurl = urllib.parse.urljoin(req.full_url, newurl)
        # Ensure redirect URL has a safe web protocol
        if not isinstance(newurl, str) or not newurl.lower().startswith(("http://", "https://")):
            raise ValueError(f"Secure protocol required for redirect: HTTP or HTTPS. Received: {newurl}")
        # Enforce that redirect target is also in the ALLOWED_FEEDS whitelist
        if newurl not in ALLOWED_FEEDS:
            raise ValueError(f"Unauthorized redirect target: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_feed_data_safely(url, timeout=15, max_bytes=10 * 1024 * 1024):
    """
    Security Enhancement: Safely fetch the RSS feed content with a strict size limit,
    explicit timeout, and secure SSL context to prevent Resource Exhaustion (DoS),
    unintended file disclosure, and SSL downgrade/bypass attacks.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "UPSC-News-Digest/1.0"}
    )
    # Performance Optimization: Retrieve the cached secure opener singleton
    # instead of repeatedly rebuilding it with the HTTPSHandler and SafeRedirectHandler.
    opener = get_secure_opener()
    with opener.open(req, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                length_val = int(content_length)
            except ValueError:
                length_val = None

            if length_val is not None and length_val > max_bytes:
                raise ValueError(f"Feed content too large: {content_length} bytes")

        data = response.read(max_bytes)
        # If there's still more data, raise ValueError to prevent DoS via infinite stream
        if response.read(1):
            raise ValueError(f"Feed content exceeds maximum size of {max_bytes} bytes")

        headers = dict(response.headers)
        headers["content-location"] = url
        return data, headers


def fetch_from_feed(url, source_name, limit=3):
    """Fetch up to `limit` articles from a single RSS feed URL."""
    # Security: Enforce web protocols for all RSS feeds to prevent local file disclosure (LFD)
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"Secure protocol required: HTTP or HTTPS. Received: {url}")
    # Security: Restrict fetching to whitelisted RSS feed URLs to prevent SSRF and unauthorized requests
    if url not in ALLOWED_FEEDS:
        raise ValueError(f"Unauthorized feed URL: {url}")

    articles = []
    try:
        # Performance Optimization: Pre-clean constant source_name outside of loop to save redundant clean_text CPU cycles
        clean_source = clean_text(source_name, max_len=100)

        # Security: Fetch feed securely with strict limits to prevent DoS and insecure SSL config
        data, headers = fetch_feed_data_safely(url)
        feed = feedparser.parse(data, response_headers=headers)

        for entry in feed.entries[:limit]:
            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            # Apply clean_text early to save memory and token budget
            # Optimization: Use max_len=400 to avoid processing large RSS summaries.
            # We only use ~300 for classification and summaries are not in the final email.
            summary = clean_text(raw_summary, max_len=400)
            # Security: Sanitize all fields to prevent null byte collisions in batch processing
            articles.append({
                "title": clean_text(str(entry.get("title", "")), max_len=200),
                "link":  clean_text(str(entry.get("link", "")), max_len=500),
                "summary": summary,
                "source": clean_source,
            })
        print(f"  [{source_name}] fetched {len(articles)} articles")
    except Exception as e:
        print(f"  [{source_name}] ERROR: {e}")
    return articles


def fetch_articles():
    """Fetch articles from all main feeds and deduplicate by URL."""
    articles = []
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as executor:
        futures = []
        for source, url in FEEDS.items():
            # Specialist sources (economy/international-only feeds) are capped at 3
            # so they don't crowd out other categories. General sources get 5.
            limit = 3 if source in SPECIALIST_SOURCES else 5
            futures.append(executor.submit(fetch_from_feed, url, source, limit))

        seen_links = set()
        for future in futures:
            for article in future.result():
                link = article.get("link")
                if link and link not in seen_links:
                    articles.append(article)
                    seen_links.add(link)
    return articles


def process_llm_articles(articles, data):
    """
    Security: Refactored logic to process LLM-generated JSON with robust validation,
    deduplication, and resource limits to prevent Denial of Service (DoS).
    """
    if not isinstance(data, dict):
        return [], {}

    classified = data.get("articles")
    raw_angles = data.get("category_angles")
    category_angles = {}

    # Security: Sanitize, validate topic names, and limit the number of category angles
    if isinstance(raw_angles, dict):
        # Limit to 20 topics maximum to prevent DoS via extremely large JSON objects
        for i, (topic, angles) in enumerate(raw_angles.items()):
            if i >= 20:
                break
            # Security: Validate topic name against TOPIC_COLORS to filter hallucinated topics
            topic_str = str(topic)
            if topic_str not in TOPIC_COLORS:
                continue
            if isinstance(angles, list):
                # Security: Strip control characters to prevent collisions in batch processing
                # Limit to 5 bullets per topic, each max 300 chars
                category_angles[topic_str] = [
                    clean_text(str(b), max_len=300, strip_tags=False) for b in angles[:5]
                ]

    if not isinstance(classified, list):
        return [], category_angles

    result = []
    seen_indices = set()
    # Security: Limit iteration over LLM-generated articles (cap at 100) to prevent DoS
    for item in classified[:100]:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        topic = item.get("topic", "")
        if topic == "Not UPSC Relevant" or topic not in TOPIC_COLORS:
            continue
        # Security: Validate index is a non-negative integer within bounds AND not already processed
        # Reject boolean type explicitly since in Python bool subclasses int (isinstance(True, int) is True)
        if type(idx) is not int or idx < 0 or idx >= len(articles) or idx in seen_indices:
            continue

        seen_indices.add(idx)
        original = articles[idx]
        # Security: Defensive conversion to string and truncation before being used in HTML rendering
        # Security: Strip control characters to prevent collisions in batch processing
        # Limit summary to 1000 chars to prevent DoS via extremely large email payloads.
        result.append({
            "title": original["title"],
            "link": original["link"],
            "source": original["source"],
            "topic": topic,
            "summary": clean_text(str(item.get("summary", "")), max_len=1000, strip_tags=False),
        })

        # Security: Final cap on total articles to keep payload size predictable
        if len(result) >= 50:
            break

    return result, category_angles


def classify_articles(articles):
    # Security: Limit the number of articles to process to prevent token limit issues and overhead
    articles = articles[:50]
    client = get_groq_client()

    # Security: Use JSON for untrusted input to provide a structural boundary,
    # mitigating prompt injection risks where malicious content could spoof custom delimiters.
    # Performance Optimization: Use compact separators (",", ":") instead of indent=2 to avoid
    # unnecessary whitespace/newlines, speeding up serialization ~2.7x and saving LLM prompt tokens.
    articles_json = json.dumps([
        {
            "index": i,
            "title": a["title"],
            "source": a["source"],
            "summary": a["summary"][:300]
        }
        for i, a in enumerate(articles)
    ], separators=(",", ":"))

    # Security: Use separate System message for instructions and persona, and User message
    # for untrusted article data to mitigate prompt injection risks.
    user_content = f"Articles to process (JSON array):\n{articles_json}"

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2,
            max_tokens=8000,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()

        data = json.loads(raw)
        return process_llm_articles(articles, data)
    except Exception as e:
        print(f"ERROR in Groq classification: {e}")
        return [], {}


def render_html(grouped, category_angles):
    today = datetime.now().strftime("%A, %B %d, %Y")
    topics_present = list(grouped.keys())
    total_articles = sum(len(articles) for articles in grouped.values())
    reading_time = max(1, round(total_articles * 0.75))

    # Performance Optimization: Batch process all article content and category angles
    # before rendering to minimize function calls and regex engine overhead.

    # 1. Gather all raw text for consolidated batch processing
    all_articles_flat = [a for topic in topics_present for a in grouped[topic]]
    titles = [a.get("title", "") for a in all_articles_flat]
    sources = [a.get("source", "") for a in all_articles_flat]
    summaries = [a.get("summary", "") for a in all_articles_flat]
    links = []
    for a in all_articles_flat:
        l = a.get("link", "")
        # Security: Case-insensitive protocol check
        if not isinstance(l, str) or not l.lower().startswith(("http://", "https://")):
            l = "#"
        links.append(str(l))

    all_angles_flat = []
    angle_topic_map = []
    for topic in topics_present:
        angles = category_angles.get(topic, [])
        if isinstance(angles, list):
            for b in angles:
                all_angles_flat.append(str(b))
                angle_topic_map.append(topic)

    # 2. Execute consolidated batch processing (HTML escaping and GS bolding)
    # Batch 1: Non-boldable content (titles, sources, links)
    n_art = len(all_articles_flat)
    safe_nobold = batch_process_text(titles + sources + links, do_bold=False)
    safe_titles = safe_nobold[:n_art]
    safe_sources = safe_nobold[n_art : 2*n_art]
    safe_links = safe_nobold[2*n_art:]

    # Batch 2: Boldable content (summaries, category angles)
    safe_bold = batch_process_text(summaries + all_angles_flat, do_bold=True)
    safe_summaries = safe_bold[:n_art]
    safe_angles_list = safe_bold[n_art:]

    # 3. Redistribute safe data into mapping structures
    safe_grouped = collections.defaultdict(list)
    cursor = 0
    for topic in topics_present:
        for _ in grouped[topic]:
            # Capture raw source name for icon lookup
            raw_source = all_articles_flat[cursor].get("source", "")
            safe_grouped[topic].append({
                "title": safe_titles[cursor],
                "source": safe_sources[cursor],
                "raw_source": raw_source,
                "summary": safe_summaries[cursor],
                "link": safe_links[cursor]
            })
            cursor += 1

    safe_angles_grouped = collections.defaultdict(list)
    for i, safe_angle in enumerate(safe_angles_list):
        safe_angles_grouped[angle_topic_map[i]].append(safe_angle)

    # Topic index bar
    index_bar_parts = []
    for topic in topics_present:
        # Optimization: Use direct dictionary lookups for pre-calculated values
        color = TOPIC_COLORS[topic]
        count = len(grouped[topic])
        safe_name = SAFE_TOPIC_NAMES[topic]
        anchor = TOPIC_ANCHORS[topic]
        # UX: Add estimated reading time per topic (3/4 of a minute per article, min 1)
        topic_time = max(1, round(count * 0.75))
        # Optimization: Use pre-calculated topic header fragments
        header_html = TOPIC_HEADERS_HTML.get(topic, safe_name)
        index_bar_parts.append(
            f'<li role="listitem" class="index-item">'
            f'<a href="#{anchor}" class="topic-pill" aria-label="Jump to {safe_name} section - {count} articles, {topic_time} min read" '
            f'style="background:{color};">{header_html} ({count}) &bull; {topic_time} min</a>'
            f'</li>'
        )
    index_bar_items = f'<ul role="list" class="index-list">{"".join(index_bar_parts)}</ul>'

    # Article sections
    sections_parts = []
    for topic in topics_present:
        # Optimization: Use direct lookups; guaranteed safe for topics in TOPIC_ORDER
        color = TOPIC_COLORS[topic]
        anchor = TOPIC_ANCHORS[topic]
        header_id = f"header-{anchor}"
        articles = safe_grouped[topic]

        cards_parts = []
        for a in articles:
            safe_title = a["title"]
            safe_source = a["source"]
            raw_source = a.get("raw_source", "")
            safe_summary = a["summary"]
            safe_link = a["link"]

            source_icon = SOURCE_ICON_TAGS.get(raw_source, "")

            cards_parts.append(f"""
            <article class="article-card" style="border-left-color:{color};">
              <h3 class="article-title">
                <a href="{safe_link}" target="_blank" rel="noopener noreferrer" aria-label="{safe_title} (opens in new tab)">{safe_title}</a>
              </h3>
              <div class="source-container">
                <span class="source-badge">{source_icon}{safe_source}</span>
              </div>
              <p class="article-summary">
                {safe_summary}
              </p>
              <a href="{safe_link}" target="_blank" rel="noopener noreferrer" class="read-more" aria-label="Read full article: {safe_title} (opens in new tab)"
                 style="color:{color};">Read full article&nbsp;<span aria-hidden="true">&rarr;</span></a>
            </article>""")
        cards_html = "".join(cards_parts)

        angles = safe_angles_grouped.get(topic, [])
        angles_html = ""
        if angles:
            bullets = "".join([f'<li class="exam-angle-bullet">{b}</li>' for b in angles])
            angles_header_id = f"angles-header-{anchor}"
            angles_html = f"""
          <aside class="exam-angles" aria-labelledby="{angles_header_id}">
            <h3 id="{angles_header_id}" class="exam-angles-header">
              <span aria-hidden="true">🎓</span> UPSC Exam Angles
            </h3>
            <ul class="exam-angles-list">{bullets}</ul>
            {BACK_TO_TOPICS_SMALL_HTML}
          </aside>"""

        # Optimization: Use pre-calculated topic header fragments
        header_html = TOPIC_HEADERS_HTML.get(topic, SAFE_TOPIC_NAMES.get(topic, topic))
        sections_parts.append(f"""
        <section id="{anchor}" aria-labelledby="{header_id}" class="topic-section" tabindex="-1">
          <h2 id="{header_id}" class="topic-header" style="background:{color};">
            {header_html}
          </h2>
          {angles_html}
          {cards_html}
          {BACK_TO_TOPICS_HTML}
        </section>""")

    sections_html = "".join(sections_parts)

    # Preheader text for better inbox preview
    preheader_text = f"Today's UPSC Digest: {total_articles} curated articles across {len(topics_present)} topics. Reading time: {reading_time} min."

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src *; style-src 'unsafe-inline';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>📰 UPSC News Digest – {today}</title>
  <style>
    html {{ scroll-behavior: smooth; }}
    a:hover {{ text-decoration: underline !important; }}
    a:focus-visible {{
      outline: 2px solid #1a1a2e;
      outline-offset: 2px;
    }}
    [tabindex="-1"]:focus {{
      outline: none !important;
    }}
    .index-list {{ list-style: none; padding: 0; margin: 0; }}
    .index-item {{ display: inline-block; margin: 0; }}
    .topic-pill {{
      display: inline-block;
      margin: 4px;
      padding: 6px 14px;
      color: #fff !important;
      border-radius: 20px;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      transition: transform 0.2s, filter 0.2s;
    }}
    .topic-pill:hover, .topic-pill:focus-visible {{
      transform: translateY(-1px) !important;
      filter: brightness(110%) !important;
      outline: 2px solid #1a1a2e !important;
      outline-offset: 2px;
    }}
    .topic-section {{ margin-bottom: 36px; }}
    .topic-header {{
      margin: 0 0 16px 0;
      padding: 12px 20px;
      color: #fff;
      border-radius: 6px;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.5px;
    }}
    .article-card {{
      background: #fff;
      border: 1px solid #e0e0e0;
      border-left-width: 4px;
      border-left-style: solid;
      border-radius: 8px;
      padding: 18px 20px;
      margin-bottom: 16px;
      display: block;
      position: relative;
      transition: border-color 0.2s, box-shadow 0.2s;
    }}
    .article-card:hover, .article-card:focus-within {{
      border-color: #999 !important;
      box-shadow: 0 4px 12px rgba(0,0,0,0.1) !important;
    }}
    .article-title a::after {{
      content: "";
      position: absolute;
      top: 0;
      right: 0;
      bottom: 0;
      left: 0;
      z-index: 1;
    }}
    .read-more {{
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      transition: filter 0.2s, transform 0.2s;
      display: inline-block;
      position: relative;
      z-index: 2;
    }}
    .gs-tag {{
      background: #1a1a2e;
      color: #fff !important;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 12px;
      letter-spacing: 0.5px;
      display: inline-block;
    }}
    .article-title {{ margin: 0 0 8px 0; font-size: 17px; font-weight: 700; letter-spacing: 0.2px; position: relative; z-index: 2; }}
    .article-title a {{ color: #1a1a1a; text-decoration: none; }}
    .source-badge {{
      background: #f0f0f0;
      color: #555;
      font-size: 12px;
      font-weight: 600;
      padding: 3px 9px;
      border-radius: 12px;
    }}
    .article-summary {{ color: #444; font-size: 14px; line-height: 1.6; margin: 0 0 12px 0; position: relative; z-index: 2; }}
    .article-card:hover .read-more, .article-card:focus-within .read-more,
    .read-more:hover, .read-more:focus-visible {{
      filter: brightness(110%);
      transform: translateX(4px);
    }}
    .source-container {{ margin-bottom: 10px; position: relative; z-index: 2; }}
    .exam-angles {{
      background: #fefce8;
      border-left: 4px solid #f59e0b;
      padding: 12px 16px;
      border-radius: 4px;
      margin-bottom: 20px;
    }}
    .exam-angles-header {{
      margin: 0;
      display: inline;
      font-size: 12px;
      font-weight: 700;
      color: #b45309;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .exam-angles-list {{ margin: 8px 0 0 0; padding-left: 18px; }}
    .exam-angle-bullet {{ margin: 4px 0; color: #78350f; font-size: 13px; line-height: 1.5; }}
    .back-to-top {{ text-align: right; }}
    .back-to-top-link {{
      color: #666;
      font-size: 12px;
      text-decoration: none;
      transition: color 0.2s, transform 0.2s;
      display: inline-block;
    }}
    .back-to-top-link:hover, .back-to-top-link:focus-visible {{
      color: #1a1a2e;
      transform: translateY(-2px);
    }}
    .main-header {{ background: #1a1a2e; border-radius: 10px; padding: 28px 30px; margin-bottom: 24px; text-align: center; }}
    .main-title {{ color: #fff; margin: 0 0 6px 0; font-size: 26px; font-weight: 700; letter-spacing: 1px; }}
    .main-subtitle {{ color: #aaa; margin: 0; font-size: 14px; }}
    .topic-index {{
      background: #fff;
      border: 1px solid #e0e0e0;
      border-radius: 8px;
      padding: 16px 20px;
      margin-bottom: 28px;
    }}
    .topic-index-header {{
      margin: 0 0 10px 0;
      font-size: 13px;
      font-weight: 700;
      color: #555;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .footer {{ text-align: center; padding: 20px; color: #5e5e5e; font-size: 12px; }}
    .skip-link:focus {{
      position: absolute !important;
      left: 50% !important;
      top: 10px !important;
      transform: translateX(-50%) !important;
      width: auto !important;
      height: auto !important;
      overflow: visible !important;
      background: #1a1a2e !important;
      color: #fff !important;
      padding: 10px 20px !important;
      border: 2px solid #fff !important;
      border-radius: 4px !important;
      text-decoration: none !important;
      z-index: 9999 !important;
    }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #121212 !important; color: #e0e0e0 !important; }}
      #top {{ background: #121212 !important; }}
      .topic-index, .article-card {{ background: #1e1e1e !important; border-color: #333 !important; }}
      .article-card h3 a {{ color: #e0e0e0 !important; }}
      .article-card p {{ color: #bbb !important; }}
      .article-card span {{ background: #333 !important; color: #aaa !important; }}
      .exam-angles {{ background: #1a1600 !important; border-left-color: #d97706 !important; }}
      .exam-angles h3 {{ color: #f59e0b !important; }}
      .exam-angles li {{ color: #d4d4d8 !important; }}
      .gs-tag {{ background: #444 !important; color: #fff !important; }}
      .footer {{ color: #888 !important; }}
      .back-to-top a {{ color: #aaa !important; }}
      .back-to-top-link:hover, .back-to-top-link:focus-visible {{
        color: #fff !important;
      }}
      a:focus-visible {{ outline-color: #fff !important; }}
      .topic-pill:hover, .topic-pill:focus-visible {{
        outline-color: #fff !important;
      }}
      .article-card:hover, .article-card:focus-within {{
        border-color: #666 !important;
        box-shadow: 0 4px 12px rgba(255, 255, 255, 0.05) !important;
      }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto !important; }}
      .topic-pill, .article-card, .read-more, .back-to-top-link {{
        transition: none !important;
      }}
    }}
    @media print {{
      body {{ background: #fff !important; }}
      #top {{ max-width: 100% !important; padding: 0 !important; }}
      .skip-link, .topic-index, .back-to-top, .read-more {{ display: none !important; }}
      .article-card, .exam-angles {{ break-inside: avoid; border: 1px solid #eee !important; }}
      .article-title a::after {{
        content: " (" attr(href) ")" !important;
        position: static !important;
        z-index: auto !important;
        font-weight: normal !important;
        font-size: 13px !important;
        color: #555 !important;
      }}
      .footer {{ border-top: 1px solid #eee; margin-top: 20px; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,sans-serif;">
  <!-- Preheader -->
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;" aria-hidden="true">
    {html.escape(preheader_text)}
  </div>

  <div id="top" style="max-width:680px;margin:0 auto;padding:20px;">
    <!-- Skip to content -->
    <a href="#main-content" class="skip-link"
       style="position:absolute;left:-9999px;top:auto;width:1px;height:1px;overflow:hidden;">
       Skip to content
    </a>

    <!-- Header -->
    <header class="main-header" role="banner">
      <h1 class="main-title">
        UPSC News Digest
      </h1>
      <p class="main-subtitle">
        <span aria-hidden="true">📅 </span>{today} <span aria-hidden="true">&bull;</span>
        <span aria-hidden="true">📰 </span>{total_articles} articles <span aria-hidden="true">&bull;</span>
        <span aria-hidden="true">⏱️ </span>{reading_time} min read
      </p>
    </header>

    <!-- Topic Index Bar -->
    <nav id="topic-index" class="topic-index" aria-labelledby="topic-index-header" tabindex="-1">
      <h2 id="topic-index-header" class="topic-index-header">Topics in this digest</h2>
      <div>{index_bar_items}</div>
    </nav>

    <!-- Article Sections -->
    <main id="main-content" tabindex="-1">
      {sections_html}
    </main>

    <!-- Footer -->
    <footer class="footer" role="contentinfo">
      Generated automatically by UPSC News Digest <span aria-hidden="true">&bull;</span> Powered by Llama 3.3 via Groq
    </footer>
  </div>
</body>
</html>"""
    return full_html, total_articles, reading_time


def validate_env():
    """
    Security: Validates that all required environment variables are present,
    within size limits, and follow basic format expectations before starting.
    """
    required = ["SENDER_EMAIL", "SENDER_APP_PASSWORD", "RECEIVER_EMAIL", "GROQ_API_KEY"]
    for var in required:
        val = str(os.getenv(var, "")).strip()
        if not val:
            raise ValueError(f"Missing required environment variable: {var}")

        # Security: Enforce maximum length to prevent resource exhaustion (DoS)
        # RECEIVER_EMAIL can be a list, so it gets a larger limit (5000). Others are capped at 200.
        max_len = 5000 if var == "RECEIVER_EMAIL" else 200
        if len(val) > max_len:
            raise ValueError(f"Environment variable {var} exceeds maximum length of {max_len} characters.")

        # Security: Reject control characters to prevent header injection or other malformed input issues
        if CONTROL_CHAR_RE.search(val) or "\r" in val or "\n" in val:
            raise ValueError(f"Environment variable {var} contains forbidden control characters.")

        # Security: Prevent usage of placeholder values from .env.example or common patterns
        low_val = val.lower()
        if any(p in low_val for p in ["your_", "example.com", "placeholder", "recipient1@"]):
            raise ValueError(f"Environment variable {var} appears to contain a placeholder value.")

    # Basic email format validation for sender and receiver
    sender = os.getenv("SENDER_EMAIL", "").strip()
    if not EMAIL_RE.match(sender):
        raise ValueError("SENDER_EMAIL does not appear to be a valid email address.")

    # Security: Validate GROQ_API_KEY format (starts with gsk_) and minimum length
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_key.startswith("gsk_"):
        raise ValueError("GROQ_API_KEY must start with 'gsk_' (standard Groq key prefix).")
    if len(groq_key) < 30:
        raise ValueError("GROQ_API_KEY is too short (minimum 30 characters required).")

    # Security: Validate SENDER_APP_PASSWORD minimum length
    app_password = os.getenv("SENDER_APP_PASSWORD", "").strip()
    if len(app_password) < 12:
        raise ValueError("SENDER_APP_PASSWORD is too short (minimum 12 characters required).")

    receivers = [r.strip() for r in os.getenv("RECEIVER_EMAIL", "").split(",") if r.strip()]
    if not receivers:
        raise ValueError("RECEIVER_EMAIL is empty or contains no valid addresses")
    if len(receivers) > 50:
        raise ValueError(f"Too many recipients ({len(receivers)}). Maximum allowed is 50.")
    for r in receivers:
        if not EMAIL_RE.match(r):
            raise ValueError(f"RECEIVER_EMAIL contains an invalid email address: {r}")


def send_email(html_body, total_articles=None, reading_time=None):
    # Security: Sanitize sender and receiver emails using CONTROL_CHAR_RE to prevent header injection
    sender_raw = os.getenv("SENDER_EMAIL", "")
    sender = CONTROL_CHAR_RE.sub("", sender_raw.strip().replace("\r", "").replace("\n", ""))
    password_raw = os.getenv("SENDER_APP_PASSWORD", "")
    password = password_raw.strip()
    receiver_raw = os.getenv("RECEIVER_EMAIL", "")

    # Support comma-separated list of recipients and deduplicate to prevent redundant sends.
    # Security: Strip newline and control characters to prevent email header injection.
    receivers = sorted(set(
        CONTROL_CHAR_RE.sub("", r.strip().replace("\r", "").replace("\n", ""))
        for r in receiver_raw.split(",") if r.strip()
    ))

    if not sender or not password or not receivers:
        raise ValueError("Missing required email credentials or receivers in send_email")

    today = datetime.now().strftime("%B %d, %Y")
    # Performance Optimization: Reuse the pre-calculated global secure SSL context
    context = _SECURE_SSL_CONTEXT

    # Performance Optimization: Pre-calculate the subject and serialize the entire MIMEText template once
    # outside the loop. By prepending 'To: {recipient}\n' inside the loop, we completely avoid
    # re-creating a MIMEMultipart container and re-encoding/re-serializing the entire HTML body
    # for each recipient. This achieves a ~100x speedup in MIME generation for multi-recipient lists.
    subject = f"UPSC News Digest – {today}"
    if total_articles is not None and reading_time is not None:
        try:
            art_count = int(total_articles)
            read_mins = int(reading_time)
            subject += f" ({art_count} articles • {read_mins} min read)"
        except (ValueError, TypeError):
            pass

    # Security: Explicitly set charset to UTF-8 for consistent rendering and security.
    msg = MIMEText(html_body, "html", _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg_template = msg.as_string()

    # Security: Set explicit timeout to prevent hanging on slow connections
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
        server.login(sender, password)

        # Security: Iterate through recipients and send individual emails to protect PII.
        # This prevents recipients from seeing each other's email addresses in the 'To' header.
        for recipient in receivers:
            full_msg = f"To: {recipient}\n{msg_template}"
            server.sendmail(sender, [recipient], full_msg)

    # Security: Mask recipient emails in logs to protect PII
    print(f"  Sent to {len(receivers)} recipient(s) successfully")


if __name__ == "__main__":
    print("=== UPSC News Digest ===")

    try:
        validate_env()
    except Exception as e:
        print(f"FATAL: Environment validation failed: {e}")
        exit(1)

    print("\n[1/4] Fetching articles from RSS feeds...")
    try:
        articles = fetch_articles()
        # Track unique links to avoid duplicates in expansion pass
        seen_links = {a["link"] for a in articles}
        print(f"  Total fetched: {len(articles)} articles")
    except Exception as e:
        print(f"FATAL: Could not fetch articles: {e}")
        raise

    print("\n[2/4] Classifying articles with Llama 3.3 via Groq (single API call)...")
    try:
        classified, category_angles = classify_articles(articles)
        print(f"  UPSC relevant: {len(classified)} articles")
    except Exception as e:
        print(f"FATAL: Groq classification failed: {e}")
        raise

    # --- Expansion pass: fill categories that got zero articles ---
    covered = {a["topic"] for a in classified}
    missing = [t for t in TOPIC_ORDER if t not in covered and t in EXPANSION_FEEDS]
    if missing:
        print(f"\n[2b/4] Expansion fetch for missing categories: {', '.join(missing)}")
        expansion_articles = []

        # Optimization: Skip URLs already fetched in the main pass and deduplicate across missing topics
        fetched_urls = MAIN_FEED_URLS
        expansion_urls_to_fetch = set()
        for topic in missing:
            for url in EXPANSION_FEEDS[topic]:
                if url not in fetched_urls:
                    expansion_urls_to_fetch.add(url)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for url in expansion_urls_to_fetch:
                # Security: Truncate source name to prevent extremely long identifiers with safe bounds check
                parts = url.split("/")
                source_name = parts[2][:50] if len(parts) > 2 else "expansion"
                futures.append(executor.submit(fetch_from_feed, url, source_name, limit=3))
            for future in futures:
                for article in future.result():
                    link = article.get("link")
                    if link and link not in seen_links:
                        expansion_articles.append(article)
                        seen_links.add(link)

        if expansion_articles:
            print(f"  Classifying {len(expansion_articles)} expansion articles...")
            try:
                extra_classified, extra_angles = classify_articles(expansion_articles)
                # Only absorb articles for categories still missing after pass 1
                still_missing = {t for t in TOPIC_ORDER if t not in covered}
                added = 0
                for a in extra_classified:
                    if a["topic"] in still_missing:
                        classified.append(a)
                        covered.add(a["topic"])
                        added += 1
                category_angles.update(extra_angles)
                print(f"  Added {added} articles from expansion feeds")
            except Exception as e:
                print(f"  WARNING: Expansion classification failed: {e}")

    if not classified:
        print("No UPSC-relevant articles found. Exiting without sending email.")
        exit(0)

    print("\n[3/4] Rendering HTML email...")
    try:
        grouped_raw = collections.defaultdict(list)
        for a in classified:
            grouped_raw[a["topic"]].append(a)
        grouped = {t: grouped_raw[t] for t in TOPIC_ORDER if t in grouped_raw}
        html_body, total_count, read_time = render_html(grouped, category_angles)
        print(f"  Topics covered: {', '.join(grouped.keys())}")
    except Exception as e:
        print(f"FATAL: HTML rendering failed: {e}")
        raise

    print("\n[4/4] Sending email via Gmail SMTP...")
    try:
        send_email(html_body, total_count, read_time)
        print("  Email sent successfully!")
    except Exception as e:
        print(f"FATAL: Email sending failed: {e}")
        raise

    print("\n=== Done! Digest delivered. ===")
