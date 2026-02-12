import unittest
import re


class TestSpotifyURLDetection(unittest.TestCase):
    """Tests for Spotify URL detection and parsing"""

    def _is_spotify_url(self, text):
        """Local implementation of is_spotify_url for testing."""
        if not text:
            return None

        spotify_url_match = re.search(
            r'(?:https?://)?(?:open\.)?spotify\.com/(track|playlist|album)/([a-zA-Z0-9]+)',
            text
        )
        if spotify_url_match:
            return spotify_url_match.group(1)

        spotify_uri_match = re.match(
            r'spotify:(track|playlist|album):([a-zA-Z0-9]+)',
            text
        )
        if spotify_uri_match:
            return spotify_uri_match.group(1)

        return None

    def _extract_spotify_id(self, url):
        """Local implementation of extract_spotify_id for testing."""
        if not url:
            return None, None

        match = re.search(
            r'(?:https?://)?(?:open\.)?spotify\.com/(track|playlist|album)/([a-zA-Z0-9]+)',
            url
        )
        if match:
            return match.group(1), match.group(2)

        match = re.match(
            r'spotify:(track|playlist|album):([a-zA-Z0-9]+)',
            url
        )
        if match:
            return match.group(1), match.group(2)

        return None, None

    # --- is_spotify_url tests ---

    def test_track_url(self):
        """Test standard Spotify track URL"""
        result = self._is_spotify_url("https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6")
        self.assertEqual(result, "track")

    def test_track_url_with_query_params(self):
        """Test Spotify track URL with ?si= tracking parameter"""
        result = self._is_spotify_url("https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6?si=abc123")
        self.assertEqual(result, "track")

    def test_playlist_url(self):
        """Test Spotify playlist URL"""
        result = self._is_spotify_url("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual(result, "playlist")

    def test_album_url(self):
        """Test Spotify album URL"""
        result = self._is_spotify_url("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")
        self.assertEqual(result, "album")

    def test_spotify_uri_track(self):
        """Test Spotify URI format for tracks"""
        result = self._is_spotify_url("spotify:track:6rqhFgbbKwnb9MLmUQDhG6")
        self.assertEqual(result, "track")

    def test_spotify_uri_playlist(self):
        """Test Spotify URI format for playlists"""
        result = self._is_spotify_url("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual(result, "playlist")

    def test_spotify_uri_album(self):
        """Test Spotify URI format for albums"""
        result = self._is_spotify_url("spotify:album:4aawyAB9vmqN3uQ7FjRGTy")
        self.assertEqual(result, "album")

    def test_http_url(self):
        """Test HTTP (non-HTTPS) Spotify URL"""
        result = self._is_spotify_url("http://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6")
        self.assertEqual(result, "track")

    def test_youtube_url_returns_none(self):
        """Test that YouTube URLs return None"""
        result = self._is_spotify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIsNone(result)

    def test_search_query_returns_none(self):
        """Test that search queries return None"""
        result = self._is_spotify_url("rick astley never gonna give you up")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        """Test that empty string returns None"""
        result = self._is_spotify_url("")
        self.assertIsNone(result)

    def test_none_returns_none(self):
        """Test that None returns None"""
        result = self._is_spotify_url(None)
        self.assertIsNone(result)

    def test_soundcloud_url_returns_none(self):
        """Test that SoundCloud URLs return None"""
        result = self._is_spotify_url("https://soundcloud.com/user/track")
        self.assertIsNone(result)

    # --- extract_spotify_id tests ---

    def test_extract_track_id(self):
        """Test extracting ID from track URL"""
        resource_type, resource_id = self._extract_spotify_id(
            "https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6"
        )
        self.assertEqual(resource_type, "track")
        self.assertEqual(resource_id, "6rqhFgbbKwnb9MLmUQDhG6")

    def test_extract_playlist_id(self):
        """Test extracting ID from playlist URL"""
        resource_type, resource_id = self._extract_spotify_id(
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
        self.assertEqual(resource_type, "playlist")
        self.assertEqual(resource_id, "37i9dQZF1DXcBWIGoYBM5M")

    def test_extract_album_id(self):
        """Test extracting ID from album URL"""
        resource_type, resource_id = self._extract_spotify_id(
            "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"
        )
        self.assertEqual(resource_type, "album")
        self.assertEqual(resource_id, "4aawyAB9vmqN3uQ7FjRGTy")

    def test_extract_id_with_query_params(self):
        """Test that query parameters don't affect ID extraction"""
        resource_type, resource_id = self._extract_spotify_id(
            "https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6?si=abc123&utm_source=copy"
        )
        self.assertEqual(resource_type, "track")
        self.assertEqual(resource_id, "6rqhFgbbKwnb9MLmUQDhG6")

    def test_extract_id_from_uri(self):
        """Test extracting ID from Spotify URI format"""
        resource_type, resource_id = self._extract_spotify_id(
            "spotify:track:6rqhFgbbKwnb9MLmUQDhG6"
        )
        self.assertEqual(resource_type, "track")
        self.assertEqual(resource_id, "6rqhFgbbKwnb9MLmUQDhG6")

    def test_extract_id_none_input(self):
        """Test extract_spotify_id with None input"""
        resource_type, resource_id = self._extract_spotify_id(None)
        self.assertIsNone(resource_type)
        self.assertIsNone(resource_id)

    def test_extract_id_invalid_url(self):
        """Test extract_spotify_id with non-Spotify URL"""
        resource_type, resource_id = self._extract_spotify_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.assertIsNone(resource_type)
        self.assertIsNone(resource_id)


class TestSpotifyTrackToQuery(unittest.TestCase):
    """Tests for converting Spotify track info to YouTube search queries"""

    def _spotify_track_to_query(self, track_info):
        """Local implementation of spotify_track_to_query for testing."""
        try:
            artists = ", ".join([artist['name'] for artist in track_info['artists']])
            track_name = track_info['name']
            return f"{artists} - {track_name}"
        except (KeyError, TypeError):
            return None

    def test_single_artist(self):
        """Test track with a single artist"""
        track_info = {
            'name': 'One Dance',
            'artists': [{'name': 'Drake'}]
        }
        result = self._spotify_track_to_query(track_info)
        self.assertEqual(result, "Drake - One Dance")

    def test_multiple_artists(self):
        """Test track with multiple artists"""
        track_info = {
            'name': 'HUMBLE.',
            'artists': [{'name': 'Kendrick Lamar'}, {'name': 'Mike WiLL Made-It'}]
        }
        result = self._spotify_track_to_query(track_info)
        self.assertEqual(result, "Kendrick Lamar, Mike WiLL Made-It - HUMBLE.")

    def test_missing_artists_key(self):
        """Test track with missing artists key"""
        track_info = {'name': 'Some Song'}
        result = self._spotify_track_to_query(track_info)
        self.assertIsNone(result)

    def test_missing_name_key(self):
        """Test track with missing name key"""
        track_info = {'artists': [{'name': 'Artist'}]}
        result = self._spotify_track_to_query(track_info)
        self.assertIsNone(result)

    def test_none_input(self):
        """Test with None input"""
        result = self._spotify_track_to_query(None)
        self.assertIsNone(result)

    def test_empty_dict(self):
        """Test with empty dict"""
        result = self._spotify_track_to_query({})
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
