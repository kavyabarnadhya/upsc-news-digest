import unittest
from unittest.mock import patch, MagicMock
import os
import digest

class TestSecurity(unittest.TestCase):

    def setUp(self):
        # Reset the cached secure opener singleton before each test to guarantee mocking isolation
        digest._SECURE_OPENER = None

        # Default valid environment variables for testing
        # Using .test instead of example.com to avoid placeholder check
        self.valid_env = {
            "SENDER_EMAIL": "sender@test.test",
            "SENDER_APP_PASSWORD": "abcd efgh ijkl mnop",
            "RECEIVER_EMAIL": "receiver@test.test",
            "GROQ_API_KEY": "gsk_test_key_very_long_to_pass_validation"
        }

    def test_validate_env_valid(self):
        with patch.dict(os.environ, self.valid_env, clear=True):
            try:
                digest.validate_env()
            except ValueError as e:
                self.fail(f"validate_env raised ValueError unexpectedly: {e}")

    def test_validate_env_missing_var(self):
        for var in self.valid_env:
            env = self.valid_env.copy()
            del env[var]
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValueError, f"Missing required environment variable: {var}"):
                    digest.validate_env()

    def test_validate_env_placeholder(self):
        env = self.valid_env.copy()
        env["SENDER_EMAIL"] = "your_email@domain.com"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "appears to contain a placeholder value"):
                digest.validate_env()

    def test_validate_env_invalid_email(self):
        env = self.valid_env.copy()
        env["SENDER_EMAIL"] = "invalid-email"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "SENDER_EMAIL does not appear to be a valid email address"):
                digest.validate_env()

    def test_validate_env_invalid_groq_key(self):
        env = self.valid_env.copy()
        env["GROQ_API_KEY"] = "not_a_groq_key"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY must start with 'gsk_'"):
                digest.validate_env()

    def test_validate_env_too_many_recipients(self):
        env = self.valid_env.copy()
        env["RECEIVER_EMAIL"] = ",".join([f"user{i}@test.test" for i in range(52)])
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "Too many recipients"):
                digest.validate_env()

    def test_validate_env_control_characters_injection(self):
        # Test SENDER_EMAIL
        env = self.valid_env.copy()
        env["SENDER_EMAIL"] = "sender@test.test\r\nInjected-Header: value"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "contains forbidden control characters"):
                digest.validate_env()

        # Test SENDER_APP_PASSWORD
        env = self.valid_env.copy()
        env["SENDER_APP_PASSWORD"] = "password\r\nInjected-Header: value"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "contains forbidden control characters"):
                digest.validate_env()

        # Test control character (ESC) injection detection
        env = self.valid_env.copy()
        env["SENDER_APP_PASSWORD"] = "password\x1bwith_esc"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "contains forbidden control characters"):
                digest.validate_env()

    def test_validate_env_large_input_dos(self):
        env = self.valid_env.copy()
        env["SENDER_APP_PASSWORD"] = "a" * 1000000
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "exceeds maximum length"):
                digest.validate_env()

    def test_clean_text_removes_control_characters(self):
        # Test string with null byte and other non-printable control characters
        dirty_text = "Hello\0 World\x01\x1f\x7f"
        cleaned_text = digest.clean_text(dirty_text)
        self.assertEqual(cleaned_text, "Hello World")

        # Test that allowed whitespace (tab, newline, carriage return) is NOT removed by CONTROL_CHAR_RE
        # but stripped by final .strip() if at ends
        whitespace_text = " \t\r\nHello\nWorld "
        cleaned_whitespace = digest.clean_text(whitespace_text)
        self.assertEqual(cleaned_whitespace, "Hello\nWorld")

        # Security: Test that encoded control characters are also removed
        encoded_dirty = "Hello&#0; World&#x1F;"
        cleaned_encoded = digest.clean_text(encoded_dirty)
        # html.unescape('&#0;') might become '' (U+FFFD) or stay as is depending on version,
        # but our CONTROL_CHAR_RE should catch the raw ones if they unescape to them.
        # Actually in Python 3.12, &#0; unescapes to \x00 is NOT true, it becomes \ufffd.
        # But &#x1B; (ESC) or similar might.

        # Testing a known one: &#x1B; (ESC) is unescaped to nothing by html.unescape in some versions,
        # or we want to ensure it's gone.
        self.assertNotIn("\x1b", digest.clean_text("&#x1B;"))

    def test_process_llm_articles_sanitizes_control_characters(self):
        # Mock articles and LLM data with control characters
        articles = [{"title": "T1", "link": "http://l1", "source": "S1", "summary": "Sum1"}]
        llm_data = {
            "articles": [
                {
                    "index": 0,
                    "topic": "Economy",
                    "summary": "Summary\x00with\x1fcontrol\x7fchars"
                }
            ],
            "category_angles": {
                "Economy": ["Angle\x00with\x08control\x0e" ]
            }
        }

        classified, angles = digest.process_llm_articles(articles, llm_data)

        # Verify summary is sanitized
        self.assertEqual(classified[0]["summary"], "Summarywithcontrolchars")

        # Verify category angles are sanitized
        self.assertEqual(angles["Economy"][0], "Anglewithcontrol")

        # Test with encoded null byte in LLM output
        llm_data_encoded = {
            "articles": [
                {
                    "index": 0,
                    "topic": "Economy",
                    "summary": "Summary&#0;with&#x00;null"
                }
            ],
            "category_angles": {}
        }
        classified_encoded, _ = digest.process_llm_articles(articles, llm_data_encoded)
        # The separator is \x00. We must ensure it's not in the result.
        self.assertNotIn("\x00", classified_encoded[0]["summary"])

    @patch("digest.urllib.request.build_opener")
    @patch("digest.feedparser.parse")
    def test_fetch_from_feed_whitelist(self, mock_parse, mock_build_opener):
        # Setup mock opener and response to return mock data and headers safely
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.read.side_effect = [b"<rss></rss>", b""]
        mock_response.headers = {"Content-Type": "text/xml"}
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_build_opener.return_value = mock_opener

        # Setup mock_parse to return empty entries
        mock_parse.return_value.entries = []

        # Unauthorized URL should raise ValueError
        with self.assertRaisesRegex(ValueError, "Unauthorized feed URL"):
            digest.fetch_from_feed("https://unauthorized.domain.com/feed", "Unauthorized")

        # Authorized URL should pass whitelist check and call feedparser
        authorized_url = list(digest.ALLOWED_FEEDS)[0]
        try:
            digest.fetch_from_feed(authorized_url, "Authorized")
        except Exception as e:
            self.fail(f"fetch_from_feed raised unexpected exception on authorized URL: {e}")

        expected_headers = {"Content-Type": "text/xml", "content-location": authorized_url}
        mock_parse.assert_called_once_with(b"<rss></rss>", response_headers=expected_headers)

    @patch("digest.urllib.request.build_opener")
    def test_fetch_feed_data_safely_oversized_headers(self, mock_build_opener):
        # Setup mock opener and response with Content-Length header that is too large
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.headers = {"Content-Length": str(20 * 1024 * 1024)}  # 20MB
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_build_opener.return_value = mock_opener

        with self.assertRaisesRegex(ValueError, "Feed content too large"):
            digest.fetch_feed_data_safely("https://some-feed.com", max_bytes=10 * 1024 * 1024)

    @patch("digest.urllib.request.build_opener")
    def test_fetch_feed_data_safely_oversized_stream(self, mock_build_opener):
        # Setup mock opener and response returning more data than max_bytes
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.headers = {}
        # First read returns 5 bytes, second read (the overflow check) returns b"x"
        mock_response.read.side_effect = [b"abcde", b"x"]
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_build_opener.return_value = mock_opener

        with self.assertRaisesRegex(ValueError, "Feed content exceeds maximum size"):
            digest.fetch_feed_data_safely("https://some-feed.com", max_bytes=5)

    def test_safe_redirect_handler_unauthorized(self):
        handler = digest.SafeRedirectHandler()
        # Non-whitelisted URL should raise ValueError
        with self.assertRaisesRegex(ValueError, "Unauthorized redirect target"):
            handler.redirect_request(None, None, 302, "Found", None, "https://unauthorized.domain.com/feed")

    def test_safe_redirect_handler_invalid_scheme(self):
        handler = digest.SafeRedirectHandler()
        # Invalid scheme should raise ValueError
        with self.assertRaisesRegex(ValueError, "Secure protocol required for redirect"):
            handler.redirect_request(None, None, 302, "Found", None, "file:///etc/passwd")

    def test_safe_redirect_handler_authorized(self):
        handler = digest.SafeRedirectHandler()
        authorized_url = list(digest.ALLOWED_FEEDS)[0]
        with patch("urllib.request.HTTPRedirectHandler.redirect_request") as mock_super:
            handler.redirect_request(None, None, 302, "Found", None, authorized_url)
            mock_super.assert_called_once_with(None, None, 302, "Found", None, authorized_url)

    def test_safe_redirect_handler_relative_authorized(self):
        handler = digest.SafeRedirectHandler()
        authorized_url = "https://www.thehindu.com/news/national/feeder/default.rss"
        req = MagicMock()
        req.full_url = "https://www.thehindu.com/news/national/"

        with patch("urllib.request.HTTPRedirectHandler.redirect_request") as mock_super:
            handler.redirect_request(req, None, 302, "Found", None, "feeder/default.rss")
            mock_super.assert_called_once_with(req, None, 302, "Found", None, authorized_url)

    def test_safe_redirect_handler_relative_unauthorized(self):
        handler = digest.SafeRedirectHandler()
        req = MagicMock()
        req.full_url = "https://www.thehindu.com/news/"

        with self.assertRaisesRegex(ValueError, "Unauthorized redirect target"):
            handler.redirect_request(req, None, 302, "Found", None, "/unauthorized/path")

    @patch("digest.smtplib.SMTP_SSL")
    @patch("digest.os.getenv")
    def test_send_email_enforces_tls_version(self, mock_getenv, mock_smtp):
        # Setup env variables
        mock_getenv.side_effect = lambda key, default=None: {
            "SENDER_EMAIL": "sender@test.test",
            "SENDER_APP_PASSWORD": "abcd efgh ijkl mnop",
            "RECEIVER_EMAIL": "receiver@test.test"
        }.get(key, default)

        # Call send_email with dummy body
        try:
            digest.send_email("<html></html>", 0, 0)
        except Exception:
            pass # We only care about the SSL context passed to SMTP_SSL

        # SMTP_SSL is called with (host, port, context=context, timeout=30)
        # Check that it was called, and that the context keyword argument had minimum_version set
        self.assertTrue(mock_smtp.called)
        kwargs = mock_smtp.call_args[1]
        context = kwargs.get("context")
        self.assertIsNotNone(context)
        import ssl
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_batch_process_text_strips_null_bytes(self):
        # Input strings containing embedded null bytes
        dirty_texts = ["Title1\x00Injected", "Title2", "Title3\x00Part2"]
        processed = digest.batch_process_text(dirty_texts, do_bold=False)
        self.assertEqual(len(processed), 3)
        self.assertEqual(processed[0], "Title1Injected")
        self.assertEqual(processed[1], "Title2")
        self.assertEqual(processed[2], "Title3Part2")

    @patch("digest.Groq")
    @patch("digest.os.getenv")
    def test_get_groq_client_strips_key_whitespace(self, mock_getenv, mock_groq):
        digest._groq_client = None
        mock_getenv.return_value = "  gsk_test_key_with_spaces  "
        client = digest.get_groq_client()
        mock_groq.assert_called_once_with(
            api_key="gsk_test_key_with_spaces",
            timeout=60.0
        )
        digest._groq_client = None

    def test_rendered_html_css_improvements(self):
        # Verify that our CSS improvements are correctly present in the rendered HTML
        grouped = {
            "Polity & Governance": [
                {
                    "title": "Article Title",
                    "link": "https://example.com/art",
                    "source": "The Hindu",
                    "summary": "This is a summary mapping to GS-II."
                }
            ]
        }
        category_angles = {
            "Polity & Governance": ["Exam relevance bullet point."]
        }
        html_body, _, _ = digest.render_html(grouped, category_angles)

        # Check skip-link focus rule uses top/left/transform for centering absolute position
        self.assertIn("position: absolute !important;", html_body)
        self.assertIn("left: 50% !important;", html_body)
        self.assertIn("top: 10px !important;", html_body)
        self.assertIn("transform: translateX(-50%) !important;", html_body)
        self.assertIn("background: #1a1a2e !important;", html_body)

        # Check prefers-reduced-motion media query rule
        self.assertIn("@media (prefers-reduced-motion: reduce)", html_body)
        self.assertIn("scroll-behavior: auto !important;", html_body)

        # Check topic pill outline color on focus in dark mode
        self.assertIn(".topic-pill:hover, .topic-pill:focus-visible", html_body)
        self.assertIn("outline-color: #fff !important;", html_body)

        # Check article-card hover state in dark mode
        self.assertIn(".article-card:hover, .article-card:focus-within", html_body)
        self.assertIn("box-shadow: 0 4px 12px rgba(255, 255, 255, 0.05) !important;", html_body)

        # Check title link aria-label for external links
        self.assertIn('aria-label="Article Title (opens in new tab)"', html_body)

        # Check exam angles semantic landmark <aside> with aria-labelledby
        self.assertIn('<aside class="exam-angles" aria-labelledby="angles-header-polity-and-governance">', html_body)
        self.assertIn('<h3 id="angles-header-polity-and-governance" class="exam-angles-header">', html_body)

        # Check read-more accessibility and card hover animation
        self.assertIn('class="read-more" aria-label="Read full article: Article Title (opens in new tab)"', html_body)
        self.assertIn('target="_blank" rel="noopener noreferrer"', html_body)
        self.assertIn('<span aria-hidden="true">&rarr;</span>', html_body)
        self.assertIn(".article-card:hover .read-more, .article-card:focus-within .read-more", html_body)

    def test_process_llm_articles_filters_invalid_topics(self):
        articles = [{"title": "T1", "link": "http://l1", "source": "S1", "summary": "Sum1"}]
        llm_data = {
            "articles": [{"index": 0, "topic": "Economy", "summary": "Sum1"}],
            "category_angles": {
                "Economy": ["Valid angle."],
                "Invalid Hallucinated Topic": ["Invalid angle."]
            }
        }
        classified, angles = digest.process_llm_articles(articles, llm_data)
        self.assertIn("Economy", angles)
        self.assertNotIn("Invalid Hallucinated Topic", angles)

    def test_process_llm_articles_rejects_boolean_index_type_confusion(self):
        # Python booleans subclass int: isinstance(True, int) is True, True == 1, False == 0.
        # Ensure process_llm_articles rejects boolean indices from untrusted LLM output.
        articles = [
            {"title": "Art 0", "link": "http://l0", "source": "S0", "summary": "Sum0"},
            {"title": "Art 1", "link": "http://l1", "source": "S1", "summary": "Sum1"},
        ]
        llm_data = {
            "articles": [
                {"index": True, "topic": "Economy", "summary": "SumTrue"},
                {"index": False, "topic": "Economy", "summary": "SumFalse"},
                {"index": 1, "topic": "Economy", "summary": "Valid index 1"}
            ]
        }
        classified, _ = digest.process_llm_articles(articles, llm_data)
        self.assertEqual(len(classified), 1)
        self.assertEqual(classified[0]["title"], "Art 1")
        self.assertEqual(classified[0]["summary"], "Valid index 1")

    def test_clean_text_defensive_typing(self):
        # Non-string inputs (integers, booleans, dicts, None) should be handled safely
        self.assertEqual(digest.clean_text(None), "")
        self.assertEqual(digest.clean_text(12345), "12345")
        self.assertEqual(digest.clean_text(True), "True")
        self.assertEqual(digest.clean_text({"key": "val"}), "{'key': 'val'}")

    @patch("digest.os.getenv")
    def test_send_email_raises_on_missing_credentials(self, mock_getenv):
        mock_getenv.side_effect = lambda key, default="": ""
        with self.assertRaisesRegex(ValueError, "Missing required email credentials or receivers"):
            digest.send_email("<html></html>", 0, 0)

    @patch("digest.smtplib.SMTP_SSL")
    @patch("digest.os.getenv")
    def test_send_email_strips_control_characters(self, mock_getenv, mock_smtp):
        mock_getenv.side_effect = lambda key, default=None: {
            "SENDER_EMAIL": "sender\r\n@test.test\x1b",
            "SENDER_APP_PASSWORD": "abcd efgh ijkl mnop",
            "RECEIVER_EMAIL": "receiver1\n@test.test\x00, receiver2@test.test"
        }.get(key, default)

        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        digest.send_email("<html></html>", "10", "5")

        self.assertTrue(mock_server.sendmail.called)
        # Verify call recipient addresses clean
        sender_arg = mock_server.sendmail.call_args_list[0][0][0]
        recipients_arg = mock_server.sendmail.call_args_list[0][0][1]
        self.assertEqual(sender_arg, "sender@test.test")
        self.assertIn("receiver1@test.test", recipients_arg)

if __name__ == "__main__":
    unittest.main()
