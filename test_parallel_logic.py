import time
import collections
import unittest
from unittest.mock import patch, MagicMock
import digest

class TestParallelLogic(unittest.TestCase):
    @patch('feedparser.parse')
    def test_fetch_articles_parallel(self, mock_parse):
        # Simulate network delay
        def side_effect(url):
            time.sleep(0.1)
            mock_feed = MagicMock()
            mock_feed.entries = [{'title': 'Test Article', 'link': 'http://test.com', 'summary': 'Summary'}] * 3
            return mock_feed

        mock_parse.side_effect = side_effect

        start_time = time.time()
        articles = digest.fetch_articles()
        end_time = time.time()

        # There are 7 feeds. If sequential, it would take > 0.7s.
        # If parallel, it should take around 0.1s - 0.2s.
        duration = end_time - start_time
        print(f"Fetch duration: {duration:.4f}s")

        self.assertTrue(len(articles) > 0)
        self.assertLess(duration, 0.5) # Should be much less than 0.7s if parallel

        # Verify all sources are present
        sources = {a['source'] for a in articles}
        for source in digest.FEEDS:
            self.assertIn(source, sources)

    @patch('feedparser.parse')
    def test_expansion_fetch_parallel(self, mock_parse):
        # Mocking the missing categories expansion fetch
        def side_effect(url):
            time.sleep(0.1)
            mock_feed = MagicMock()
            mock_feed.entries = [{'title': 'Expansion Article', 'link': 'http://test-expansion.com', 'summary': 'Summary'}] * 3
            return mock_feed

        mock_parse.side_effect = side_effect

        # Test the core logic of expansion fetching
        missing = ["Environment & Ecology", "Science & Technology"]
        expansion_articles = []

        from concurrent.futures import ThreadPoolExecutor

        start_time = time.time()
        # This mirrors the logic in the main block
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for topic in missing:
                for url in digest.EXPANSION_FEEDS[topic]:
                    source_name = url.split("/")[2]
                    futures.append(executor.submit(digest.fetch_from_feed, url, source_name, limit=3))
            for future in futures:
                expansion_articles.extend(future.result())
        end_time = time.time()

        duration = end_time - start_time
        print(f"Expansion fetch duration: {duration:.4f}s")

        # Environment & Ecology has 2 feeds, Science & Technology has 2 feeds. Total 4 feeds.
        # Sequential: 0.4s. Parallel: 0.1s - 0.2s.
        self.assertEqual(len(expansion_articles), 4 * 3)
        self.assertLess(duration, 0.3)

if __name__ == '__main__':
    unittest.main()
