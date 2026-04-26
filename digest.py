import os
import re
import json
import smtplib
import socket
import ssl
import html
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


def clean_text(text):
    """
    Performance Optimization: Strips HTML tags and unescapes entities from RSS summaries
    to reduce token usage in LLM prompts and improve classification accuracy.
    """
    if not text:
        return ""
    # Unescape HTML entities first so we don't accidentally leave things like &lt; in the text
    text = html.unescape(text)
    return TAG_RE.sub("", text).strip()


# Initialize Groq client once at the module level for resource reuse
_groq_client = None


def get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(
            api_key=os.getenv("GROQ_API_KEY"),
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

TOPIC_COLORS = {
    "International Relations": "#c0392b",
    "Economy": "#1e8449",
    "Polity & Governance": "#2980b9",
    "Security & Defence": "#8e44ad",
    "History & Culture": "#d35400",
    "Environment & Ecology": "#117864",
    "Social Issues": "#515a5a",
    "Science & Technology": "#2c3e50",
}

# Pre-calculate topic anchors and escaped names to save cycles during rendering
TOPIC_ANCHORS = {
    topic: re.sub(r"[^a-z0-9\-]", "", topic.replace(" ", "-").replace("&", "and").lower())
    for topic in TOPIC_COLORS
}
SAFE_TOPIC_NAMES = {topic: html.escape(topic) for topic in TOPIC_COLORS}

VALID_TOPICS = set(TOPIC_COLORS.keys()) | {"Not UPSC Relevant"}

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


def fetch_from_feed(url, source_name, limit=3):
    """Fetch up to `limit` articles from a single RSS feed URL."""
    articles = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:limit]:
            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            # Apply clean_text early to save memory and token budget
            summary = clean_text(raw_summary)
            articles.append({
                "title": entry.get("title", ""),
                "link":  entry.get("link", ""),
                "summary": summary,
                "source": source_name,
            })
        print(f"  [{source_name}] fetched {len(articles)} articles")
    except Exception as e:
        print(f"  [{source_name}] ERROR: {e}")
    return articles


def fetch_articles():
    articles = []
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as executor:
        futures = []
        for source, url in FEEDS.items():
            # Specialist sources (economy/international-only feeds) are capped at 3
            # so they don't crowd out other categories. General sources get 5.
            limit = 3 if source in SPECIALIST_SOURCES else 5
            futures.append(executor.submit(fetch_from_feed, url, source, limit))
        for future in futures:
            articles.extend(future.result())
    return articles


def classify_articles(articles):
    client = get_groq_client()

    # Optimization: Use list-based join for efficient string building
    articles_text_parts = []
    for i, a in enumerate(articles):
        articles_text_parts.append(
            f"\n--- Article {i} ---\n"
            f"Title: {a['title']}\n"
            f"Source: {a['source']}\n"
            f"Summary: {a['summary'][:300]}\n"
        )
    articles_text = "".join(articles_text_parts)

    prompt = f"""You are a UPSC exam preparation assistant focused on the Indian Civil Services Examination.

**Priority:** Strongly prefer articles with a direct India angle — Indian polity, governance, legislation, constitutional matters, Indian economy, Indian social issues, Indian environment policy, Indian science initiatives, India's defence, or Indian history and culture.

**International news:** Include purely international stories only if they are clearly significant for GS-II International Relations — major geopolitical events, major international agreements, or global developments with direct implications for India. Routine foreign news without clear exam relevance should be classified as "Not UPSC Relevant".

**Polity & Governance — classify ONLY if the article covers:** constitutional amendments or provisions, Parliament or state legislature bills or debates, Supreme Court or High Court judgments on constitutional or administrative matters, central or state government schemes and policies, electoral reforms (not campaign coverage), administrative or regulatory changes, federal relations, or lokpal/RTI/accountability mechanisms.
**Do NOT classify as Polity & Governance:** party political statements, opposition rhetoric, electoral campaign news, political rallies, intra-party matters, or opinion pieces on politics without a substantive constitutional or policy dimension — these are "Not UPSC Relevant" unless they fit another topic such as Economy or Social Issues.

Return ONLY a JSON object (no markdown, no code fences, no explanation) with exactly two keys:

1. "articles": an array of objects for each UPSC-relevant article with:
   - index: the article index number (int)
   - topic: one of exactly these topics: {', '.join(sorted(TOPIC_COLORS.keys()))}, Not UPSC Relevant
   - summary: sharp UPSC-focused summary in 4-5 sentences. Lead with the core decision, judgment, or policy. Then include: (a) the specific constitutional article, act, scheme, or regulatory body involved by name; (b) one or two concrete data points such as numbers, percentages, timelines, or committee names; (c) the GS paper and syllabus topic this maps to (e.g. "GS-II: Parliament and State Legislatures"); (d) the exam-relevant implication or significance. Avoid generic commentary, journalistic opinion, and vague statements like "experts say" or "this is significant".
   Omit articles that are "Not UPSC Relevant" — do not include them in the array at all.

2. "category_angles": an object mapping each topic that appeared in "articles" to an array of 3-5 bullet strings highlighting the collective UPSC exam relevance of all articles under that topic (mention specific GS papers, syllabus topics, or exam themes where applicable).

Articles:
{articles_text}"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=8000,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()

        data = json.loads(raw)
        # Security: Robustly validate LLM-generated JSON structure
        if not isinstance(data, dict):
            raise ValueError("LLM response is not a JSON object")
        classified = data.get("articles")
        if not isinstance(classified, list):
            raise ValueError("LLM 'articles' is not a list")
        category_angles = data.get("category_angles")
        if not isinstance(category_angles, dict):
            category_angles = {}
    except Exception as e:
        print(f"ERROR in Groq classification: {e}")
        return [], {}

    # Merge original article data back using index
    result = []
    for item in classified:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        topic = item.get("topic", "")
        if topic == "Not UPSC Relevant" or topic not in TOPIC_COLORS:
            continue
        # Security: Validate index is a non-negative integer within bounds
        if not isinstance(idx, int) or idx < 0 or idx >= len(articles):
            continue
        original = articles[idx]
        result.append({
            "title": original["title"],
            "link": original["link"],
            "source": original["source"],
            "topic": topic,
            "summary": item.get("summary", ""),
        })
    return result, category_angles


def render_html(grouped, category_angles):
    today = datetime.now().strftime("%A, %B %d, %Y")
    topics_present = list(grouped.keys())
    total_articles = sum(len(articles) for articles in grouped.values())
    reading_time = max(1, round(total_articles * 0.75))

    # Topic index bar
    index_bar_parts = []
    for topic in topics_present:
        # Optimization: Use direct dictionary lookups for pre-calculated values
        color = TOPIC_COLORS[topic]
        count = len(grouped[topic])
        safe_name = SAFE_TOPIC_NAMES[topic]
        anchor = TOPIC_ANCHORS[topic]
        index_bar_parts.append(
            f'<li style="display:inline-block;margin:0;">'
            f'<a href="#{anchor}" aria-label="Jump to {safe_name} section - {count} articles" '
            f'style="display:inline-block;margin:4px;padding:6px 14px;'
            f'background:{color};color:#fff;border-radius:20px;text-decoration:none;'
            f'font-size:13px;font-weight:600;">{safe_name} ({count})</a>'
            f'</li>'
        )
    index_bar_items = f'<ul style="list-style:none;padding:0;margin:0;">{"".join(index_bar_parts)}</ul>'

    # Article sections
    sections_parts = []
    for topic in topics_present:
        # Optimization: Use direct lookups; guaranteed safe for topics in TOPIC_ORDER
        color = TOPIC_COLORS[topic]
        anchor = TOPIC_ANCHORS[topic]
        header_id = f"header-{anchor}"
        articles = grouped[topic]

        cards_parts = []
        for a in articles:
            # Escape content to prevent XSS
            safe_title = html.escape(a.get("title", ""))
            safe_source = html.escape(a.get("source", ""))
            safe_summary = html.escape(a.get("summary", ""))

            # Simple URL validation: only allow http(s) protocols
            # Security: Validation must be case-insensitive to effectively block javascript: URIs
            link = a.get("link", "")
            if not link.lower().startswith(("http://", "https://")):
                link = "#"
            # Escape link to prevent attribute injection
            safe_link = html.escape(link, quote=True)

            cards_parts.append(f"""
            <article style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                        padding:18px 20px;margin-bottom:16px;display:block;">
              <h3 style="margin:0 0 8px 0;font-size:17px;font-weight:700;">
                <a href="{safe_link}" style="color:#1a1a1a;text-decoration:none;">{safe_title}</a>
              </h3>
              <div style="margin-bottom:10px;">
                <span style="background:#f0f0f0;color:#555;font-size:12px;font-weight:600;
                             padding:3px 9px;border-radius:12px;">{safe_source}</span>
              </div>
              <p style="color:#444;font-size:14px;line-height:1.6;margin:0 0 12px 0;">
                {safe_summary}
              </p>
              <a href="{safe_link}" aria-label="Read full article: {safe_title}"
                 style="color:{color};font-size:13px;font-weight:600;
                 text-decoration:none;">Read full article <span aria-hidden="true">&rarr;</span></a>
            </article>""")
        cards_html = "".join(cards_parts)

        angles = category_angles.get(topic, [])
        angles_html = ""
        # Security: Ensure angles is a list to prevent iterating over characters if AI returns a string
        if isinstance(angles, list) and angles:
            # Security: Defensive string conversion to prevent crashes on non-string AI output
            bullets = "".join(
                f'<li style="margin:4px 0;color:#78350f;font-size:13px;line-height:1.5;">{html.escape(str(b))}</li>'
                for b in angles
            )
            angles_html = f"""
          <div style="background:#fefce8;border-left:4px solid #f59e0b;
                      padding:12px 16px;border-radius:4px;margin-bottom:20px;">
            <h3 style="margin:0;display:inline;font-size:12px;font-weight:700;color:#b45309;
                         text-transform:uppercase;letter-spacing:0.5px;">
              <span aria-hidden="true">🎓</span> UPSC Exam Angles
            </h3>
            <ul style="margin:8px 0 0 0;padding-left:18px;">{bullets}</ul>
          </div>"""

        sections_parts.append(f"""
        <section id="{anchor}" aria-labelledby="{header_id}" style="margin-bottom:36px;">
          <h2 id="{header_id}" style="margin:0 0 16px 0;padding:12px 20px;background:{color};
                     color:#fff;border-radius:6px;font-size:18px;font-weight:700;">
            {SAFE_TOPIC_NAMES[topic]}
          </h2>
          {angles_html}
          {cards_html}
          <div style="text-align:right;">
            <a href="#top" aria-label="Back to topic index" style="color:#666;font-size:12px;text-decoration:none;"><span aria-hidden="true">&uarr;</span> Back to top</a>
          </div>
        </section>""")

    sections_html = "".join(sections_parts)

    # Preheader text for better inbox preview
    preheader_text = f"Today's UPSC Digest: {total_articles} curated articles across {len(topics_present)} topics. Reading time: {reading_time} min."

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>UPSC News Digest – {today}</title>
  <style>
    .skip-link:focus {{
      position: static !important;
      width: auto !important;
      height: auto !important;
      overflow: visible !important;
      background: #fff;
      padding: 10px;
      border: 2px solid #1a1a2e;
      z-index: 9999;
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
    <div style="background:#1a1a2e;border-radius:10px;padding:28px 30px;margin-bottom:24px;text-align:center;">
      <h1 style="color:#fff;margin:0 0 6px 0;font-size:26px;font-weight:700;">
        UPSC News Digest
      </h1>
      <p style="color:#aaa;margin:0;font-size:14px;">{today} &bull; {total_articles} articles &bull; {reading_time} min read</p>
    </div>

    <!-- Topic Index Bar -->
    <nav aria-label="Topic index" style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                padding:16px 20px;margin-bottom:28px;">
      <p style="margin:0 0 10px 0;font-size:13px;font-weight:700;color:#555;
                text-transform:uppercase;letter-spacing:0.5px;">Topics in this digest</p>
      <div>{index_bar_items}</div>
    </nav>

    <!-- Article Sections -->
    <main id="main-content">
      {sections_html}
    </main>

    <!-- Footer -->
    <div style="text-align:center;padding:20px;color:#5e5e5e;font-size:12px;">
      Generated automatically by UPSC News Digest &bull; Powered by Llama 3.3 via Groq
    </div>
  </div>
</body>
</html>"""
    return full_html


def send_email(html_body):
    # Security: Sanitize sender email to prevent header injection
    sender_raw = os.getenv("SENDER_EMAIL")
    sender = sender_raw.strip().replace("\r", "").replace("\n", "") if sender_raw else None
    password = os.getenv("SENDER_APP_PASSWORD")
    receiver_raw = os.getenv("RECEIVER_EMAIL")

    if not all([sender, password, receiver_raw]):
        raise ValueError("Missing one or more email env vars: SENDER_EMAIL, SENDER_APP_PASSWORD, RECEIVER_EMAIL")

    # Support comma-separated list of recipients.
    # Security: Strip newline characters to prevent email header injection.
    receivers = [r.strip().replace("\r", "").replace("\n", "") for r in receiver_raw.split(",") if r.strip()]

    today = datetime.now().strftime("%B %d, %Y")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"UPSC News Digest – {today}"
    msg["From"] = sender
    msg["To"] = ", ".join(receivers)

    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
    # Security: Mask recipient emails in logs to protect PII
    print(f"  Sent to {len(receivers)} recipient(s) successfully")


if __name__ == "__main__":
    print("=== UPSC News Digest ===")

    print("\n[1/4] Fetching articles from RSS feeds...")
    try:
        articles = fetch_articles()
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
        fetched_urls = set(FEEDS.values())
        expansion_urls_to_fetch = set()
        for topic in missing:
            for url in EXPANSION_FEEDS[topic]:
                if url not in fetched_urls:
                    expansion_urls_to_fetch.add(url)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for url in expansion_urls_to_fetch:
                source_name = url.split("/")[2]  # e.g. thewire.in
                futures.append(executor.submit(fetch_from_feed, url, source_name, limit=3))
            for future in futures:
                expansion_articles.extend(future.result())

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
        html = render_html(grouped, category_angles)
        print(f"  Topics covered: {', '.join(grouped.keys())}")
    except Exception as e:
        print(f"FATAL: HTML rendering failed: {e}")
        raise

    print("\n[4/4] Sending email via Gmail SMTP...")
    try:
        send_email(html)
        print("  Email sent successfully!")
    except Exception as e:
        print(f"FATAL: Email sending failed: {e}")
        raise

    print("\n=== Done! Digest delivered. ===")
