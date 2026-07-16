import unittest
import re


# Mirror of TWITTER_STATUS_RE / find_twitter_urls in bot.py, kept local so the
# test doesn't import bot.py (which pulls in discord, aiohttp, etc.).
TWITTER_STATUS_RE = re.compile(
    r'https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/(\w+)/status/(\d+)',
    re.IGNORECASE,
)


def find_twitter_urls(text):
    if not text:
        return []
    return [
        (match.group(1), match.group(2), match.group(0))
        for match in TWITTER_STATUS_RE.finditer(text)
    ]


class TestTwitterURLDetection(unittest.TestCase):
    """Tests for X/Twitter status link detection."""

    def test_x_com_url(self):
        result = find_twitter_urls("https://x.com/jack/status/20")
        self.assertEqual(result, [("jack", "20", "https://x.com/jack/status/20")])

    def test_twitter_com_url(self):
        result = find_twitter_urls("check https://twitter.com/nasa/status/1234567890 out")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "nasa")
        self.assertEqual(result[0][1], "1234567890")

    def test_mobile_and_www_subdomains(self):
        self.assertEqual(find_twitter_urls("https://mobile.twitter.com/a/status/1")[0][0], "a")
        self.assertEqual(find_twitter_urls("https://www.x.com/b/status/2")[0][0], "b")

    def test_trailing_query_params_ignored(self):
        result = find_twitter_urls("https://x.com/user/status/99?s=20&t=abc")
        # group(0) stops at the numeric id, query string excluded.
        self.assertEqual(result[0][2], "https://x.com/user/status/99")

    def test_i_status_url(self):
        # X uses /i/status/ for some links.
        self.assertEqual(find_twitter_urls("https://x.com/i/status/555")[0][1], "555")

    def test_multiple_links_in_one_message(self):
        text = "https://x.com/a/status/1 and https://twitter.com/b/status/2"
        result = find_twitter_urls(text)
        self.assertEqual([(u, i) for u, i, _ in result], [("a", "1"), ("b", "2")])

    def test_case_insensitive_domain(self):
        self.assertEqual(find_twitter_urls("https://X.COM/user/status/7")[0][1], "7")

    # --- negatives ---

    def test_scheme_required_avoids_substring_false_positive(self):
        # "netflix.com" contains "x.com" as a substring; requiring the scheme
        # prevents a false match.
        self.assertEqual(find_twitter_urls("netflix.com/user/status/1"), [])

    def test_profile_url_without_status_returns_none(self):
        self.assertEqual(find_twitter_urls("https://x.com/someuser"), [])

    def test_youtube_url_returns_none(self):
        self.assertEqual(find_twitter_urls("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), [])

    def test_spotify_url_returns_none(self):
        self.assertEqual(find_twitter_urls("https://open.spotify.com/track/abc123"), [])

    def test_empty_string_returns_empty(self):
        self.assertEqual(find_twitter_urls(""), [])

    def test_none_returns_empty(self):
        self.assertEqual(find_twitter_urls(None), [])


if __name__ == '__main__':
    unittest.main()
