import time
import feedparser
import collections
from concurrent.futures import ThreadPoolExecutor

# Mocking feedparser.parse to simulate network delay
def mock_parse(url):
    time.sleep(0.5)  # Simulate 500ms delay per feed
    return collections.namedtuple('Feed', ['entries'])(entries=[{'title': 'Test', 'link': 'http://test.com', 'summary': 'Test summary'}] * 5)

original_parse = feedparser.parse
feedparser.parse = mock_parse

FEEDS = {
    "The Hindu":       "https://www.thehindu.com/news/national/feeder/default.rss",
    "Indian Express":  "https://indianexpress.com/section/india/feed/",
    "The Print":       "https://theprint.in/category/india/feed/",
    "LiveMint":        "https://www.livemint.com/rss/news",
    "BBC World":       "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Economic Times":  "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
    "DD News":         "https://ddnews.gov.in/en/feed/",
}
SPECIALIST_SOURCES = {"Economic Times", "LiveMint", "BBC World"}

def fetch_from_feed(url, source_name, limit=3):
    articles = []
    feed = feedparser.parse(url)
    for entry in feed.entries[:limit]:
        articles.append({"title": entry['title'], "link": entry['link'], "summary": entry['summary'], "source": source_name})
    return articles

def fetch_articles_sequential():
    articles = []
    for source, url in FEEDS.items():
        limit = 3 if source in SPECIALIST_SOURCES else 5
        articles.extend(fetch_from_feed(url, source, limit))
    return articles

def fetch_articles_parallel():
    articles = []
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as executor:
        futures = []
        for source, url in FEEDS.items():
            limit = 3 if source in SPECIALIST_SOURCES else 5
            futures.append(executor.submit(fetch_from_feed, url, source, limit))
        for future in futures:
            articles.extend(future.result())
    return articles

print("Measuring Sequential...")
start = time.time()
fetch_articles_sequential()
seq_time = time.time() - start
print(f"Sequential Time: {seq_time:.2f}s")

print("Measuring Parallel...")
start = time.time()
fetch_articles_parallel()
par_time = time.time() - start
print(f"Parallel Time: {par_time:.2f}s")

print(f"Speedup: {seq_time / par_time:.2f}x")
