import unittest
import re


class TestSunoURLDetection(unittest.TestCase):
    """Tests for Suno song and playlist URL detection."""

    def _is_suno_url(self, text):
        """Local implementation of is_suno_url for testing."""
        if not text:
            return None
        match = re.search(
            r'(?:https?://)?(?:www\.)?(?:suno\.com|app\.suno\.ai)/song/([a-f0-9-]+)',
            text
        )
        return match.group(1) if match else None

    def _is_suno_playlist_url(self, text):
        """Local implementation of is_suno_playlist_url for testing."""
        if not text:
            return None
        match = re.search(
            r'(?:https?://)?(?:www\.)?(?:suno\.com|app\.suno\.ai)/playlist/([a-f0-9-]+)',
            text
        )
        return match.group(1) if match else None

    # --- is_suno_playlist_url tests ---

    def test_playlist_url(self):
        """Standard Suno playlist URL returns the playlist ID."""
        result = self._is_suno_playlist_url(
            "https://suno.com/playlist/13f53000-767f-4ba6-8168-59cc812f5aa2"
        )
        self.assertEqual(result, "13f53000-767f-4ba6-8168-59cc812f5aa2")

    def test_playlist_url_app_subdomain(self):
        """app.suno.ai playlist URL is recognized."""
        result = self._is_suno_playlist_url(
            "https://app.suno.ai/playlist/13f53000-767f-4ba6-8168-59cc812f5aa2"
        )
        self.assertEqual(result, "13f53000-767f-4ba6-8168-59cc812f5aa2")

    def test_playlist_url_with_query_params(self):
        """Trailing query params do not break ID extraction."""
        result = self._is_suno_playlist_url(
            "https://suno.com/playlist/13f53000-767f-4ba6-8168-59cc812f5aa2?sh=abc"
        )
        self.assertEqual(result, "13f53000-767f-4ba6-8168-59cc812f5aa2")

    def test_song_url_is_not_a_playlist(self):
        """A Suno song URL must not be detected as a playlist."""
        self.assertIsNone(self._is_suno_playlist_url(
            "https://suno.com/song/e9382dab-14b9-4be5-bcd7-2588f9b6e1ec"
        ))

    def test_playlist_url_is_not_a_song(self):
        """A Suno playlist URL must not be detected as a song."""
        self.assertIsNone(self._is_suno_url(
            "https://suno.com/playlist/13f53000-767f-4ba6-8168-59cc812f5aa2"
        ))

    def test_empty_string_returns_none(self):
        self.assertIsNone(self._is_suno_playlist_url(""))

    def test_none_returns_none(self):
        self.assertIsNone(self._is_suno_playlist_url(None))

    def test_youtube_url_returns_none(self):
        self.assertIsNone(self._is_suno_playlist_url(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ))

    def test_spotify_playlist_returns_none(self):
        self.assertIsNone(self._is_suno_playlist_url(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        ))

    # --- is_suno_url (song) regression tests ---

    def test_song_url_still_detected(self):
        result = self._is_suno_url(
            "https://suno.com/song/e9382dab-14b9-4be5-bcd7-2588f9b6e1ec"
        )
        self.assertEqual(result, "e9382dab-14b9-4be5-bcd7-2588f9b6e1ec")


if __name__ == '__main__':
    unittest.main()
