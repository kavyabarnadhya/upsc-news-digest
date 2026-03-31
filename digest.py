import os
import json
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import collections
from datetime import datetime

import feedparser
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

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
    "Economy": "#27ae60",
    "Polity & Governance": "#2980b9",
    "Security & Defence": "#8e44ad",
    "History & Culture": "#d35400",
    "Environment & Ecology": "#16a085",
    "Social Issues": "#7f8c8d",
    "Science & Technology": "#2c3e50",
}

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
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
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
    for source, url in FEEDS.items():
        # Specialist sources (economy/international-only feeds) are capped at 3
        # so they don't crowd out other categories. General sources get 5.
        limit = 3 if source in SPECIALIST_SOURCES else 5
        articles.extend(fetch_from_feed(url, source, limit))
    return articles


def classify_articles(articles):
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    articles_text = ""
    for i, a in enumerate(articles):
        articles_text += (
            f"\n--- Article {i} ---\n"
            f"Title: {a['title']}\n"
            f"Source: {a['source']}\n"
            f"Summary: {a['summary'][:300]}\n"
        )

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
        classified = data["articles"]
        category_angles = data.get("category_angles", {})
    except Exception as e:
        print(f"ERROR in Groq classification: {e}")
        return [], {}

    # Merge original article data back using index
    result = []
    for item in classified:
        idx = item.get("index")
        topic = item.get("topic", "")
        if topic == "Not UPSC Relevant" or topic not in TOPIC_COLORS:
            continue
        if idx is None or idx >= len(articles):
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
    today = datetime.now().strftime("%B %d, %Y")
    topics_present = list(grouped.keys())

    # Topic index bar
    index_bar_items = ""
    for topic in topics_present:
        color = TOPIC_COLORS[topic]
        anchor = topic.replace(" ", "-").replace("&", "and").lower()
        index_bar_items += (
            f'<a href="#{anchor}" style="display:inline-block;margin:4px;padding:6px 14px;'
            f'background:{color};color:#fff;border-radius:20px;text-decoration:none;'
            f'font-size:13px;font-weight:600;">{topic}</a>'
        )

    # Article sections
    sections_html = ""
    for topic in topics_present:
        color = TOPIC_COLORS[topic]
        anchor = topic.replace(" ", "-").replace("&", "and").lower()
        articles = grouped[topic]

        cards_html = ""
        for a in articles:
            cards_html += f"""
            <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                        padding:18px 20px;margin-bottom:16px;">
              <div style="margin-bottom:8px;">
                <a href="{a['link']}" style="font-size:17px;font-weight:700;color:#1a1a1a;
                   text-decoration:none;">{a['title']}</a>
              </div>
              <div style="margin-bottom:10px;">
                <span style="background:#f0f0f0;color:#555;font-size:12px;font-weight:600;
                             padding:3px 9px;border-radius:12px;">{a['source']}</span>
              </div>
              <p style="color:#444;font-size:14px;line-height:1.6;margin:0 0 12px 0;">
                {a['summary']}
              </p>
              <a href="{a['link']}" style="color:{color};font-size:13px;font-weight:600;
                 text-decoration:none;">Read full article &rarr;</a>
            </div>"""

        angles = category_angles.get(topic, [])
        angles_html = ""
        if angles:
            bullets = "".join(
                f'<li style="margin:4px 0;color:#78350f;font-size:13px;line-height:1.5;">{b}</li>'
                for b in angles
            )
            angles_html = f"""
          <div style="background:#fefce8;border-left:4px solid #f59e0b;
                      padding:12px 16px;border-radius:4px;margin-bottom:20px;">
            <span style="font-size:12px;font-weight:700;color:#b45309;
                         text-transform:uppercase;letter-spacing:0.5px;">UPSC Exam Angles</span>
            <ul style="margin:8px 0 0 0;padding-left:18px;">{bullets}</ul>
          </div>"""

        sections_html += f"""
        <div id="{anchor}" style="margin-bottom:36px;">
          <h2 style="margin:0 0 16px 0;padding:12px 20px;background:{color};
                     color:#fff;border-radius:6px;font-size:18px;font-weight:700;">
            {topic}
          </h2>
          {angles_html}
          {cards_html}
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>UPSC News Digest – {today}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,sans-serif;">
  <div style="max-width:680px;margin:0 auto;padding:20px;">

    <!-- Header -->
    <div style="background:#1a1a2e;border-radius:10px;padding:28px 30px;margin-bottom:24px;text-align:center;">
      <h1 style="color:#fff;margin:0 0 6px 0;font-size:26px;font-weight:700;">
        UPSC News Digest
      </h1>
      <p style="color:#aaa;margin:0;font-size:14px;">{today}</p>
    </div>

    <!-- Topic Index Bar -->
    <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                padding:16px 20px;margin-bottom:28px;">
      <p style="margin:0 0 10px 0;font-size:13px;font-weight:700;color:#555;
                text-transform:uppercase;letter-spacing:0.5px;">Topics in this digest</p>
      <div>{index_bar_items}</div>
    </div>

    <!-- Article Sections -->
    {sections_html}

    <!-- Footer -->
    <div style="text-align:center;padding:20px;color:#999;font-size:12px;">
      Generated automatically by UPSC News Digest &bull; Powered by Llama 3.3 via Groq
    </div>
  </div>
</body>
</html>"""
    return html


def send_email(html_body):
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_APP_PASSWORD")
    receiver_raw = os.getenv("RECEIVER_EMAIL")

    if not all([sender, password, receiver_raw]):
        raise ValueError("Missing one or more email env vars: SENDER_EMAIL, SENDER_APP_PASSWORD, RECEIVER_EMAIL")

    # Support comma-separated list of recipients
    receivers = [r.strip() for r in receiver_raw.split(",") if r.strip()]

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
    print(f"  Sent to: {', '.join(receivers)}")


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
        for topic in missing:
            for url in EXPANSION_FEEDS[topic]:
                source_name = url.split("/")[2]  # e.g. thewire.in
                expansion_articles.extend(fetch_from_feed(url, source_name, limit=3))

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
