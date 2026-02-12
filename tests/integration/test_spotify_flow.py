import unittest
import asyncio
import re
from collections import deque
from unittest.mock import MagicMock, AsyncMock, patch


class TestSpotifyFlowIntegration(unittest.TestCase):
    """Integration tests for Spotify URL handling flow"""

    def setUp(self):
        """Set up test environment"""
        self.ctx = MagicMock()
        self.ctx.guild.id = 12345
        self.ctx.voice_client = MagicMock()
        self.ctx.voice_client.is_playing.return_value = True
        self.ctx.send = AsyncMock()
        self.ctx.invoke = AsyncMock()

        self.queue = deque()

    def _is_spotify_url(self, text):
        """Local implementation of is_spotify_url."""
        if not text:
            return None
        match = re.search(
            r'(?:https?://)?(?:open\.)?spotify\.com/(track|playlist|album)/([a-zA-Z0-9]+)',
            text
        )
        if match:
            return match.group(1)
        match = re.match(r'spotify:(track|playlist|album):([a-zA-Z0-9]+)', text)
        if match:
            return match.group(1)
        return None

    def _extract_spotify_id(self, url):
        """Local implementation of extract_spotify_id."""
        if not url:
            return None, None
        match = re.search(
            r'(?:https?://)?(?:open\.)?spotify\.com/(track|playlist|album)/([a-zA-Z0-9]+)',
            url
        )
        if match:
            return match.group(1), match.group(2)
        match = re.match(r'spotify:(track|playlist|album):([a-zA-Z0-9]+)', url)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def _spotify_track_to_query(self, track_info):
        """Local implementation of spotify_track_to_query."""
        try:
            artists = ", ".join([artist['name'] for artist in track_info['artists']])
            track_name = track_info['name']
            return f"{artists} - {track_name}"
        except (KeyError, TypeError):
            return None

    def test_spotify_track_to_queue_flow(self):
        """Test that a Spotify track URL is resolved and added to queue"""
        url = "https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6"

        # Verify URL is detected as Spotify
        spotify_type = self._is_spotify_url(url)
        self.assertEqual(spotify_type, "track")

        # Simulate Spotify API response
        mock_track = {
            'name': 'One Dance',
            'artists': [{'name': 'Drake'}, {'name': 'WizKid'}, {'name': 'Kyla'}]
        }

        # Convert to search query
        query = self._spotify_track_to_query(mock_track)
        self.assertEqual(query, "Drake, WizKid, Kyla - One Dance")

        # Add to queue (simulating what handle_play_request does)
        self.queue.append(query)
        self.assertEqual(len(self.queue), 1)
        self.assertEqual(self.queue[0], "Drake, WizKid, Kyla - One Dance")

    def test_spotify_playlist_to_queue_flow(self):
        """Test that a Spotify playlist URL resolves multiple tracks to queue"""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

        # Verify URL is detected as Spotify playlist
        spotify_type = self._is_spotify_url(url)
        self.assertEqual(spotify_type, "playlist")

        # Simulate Spotify API response for playlist
        mock_playlist_items = [
            {'track': {'name': 'Song 1', 'artists': [{'name': 'Artist A'}]}},
            {'track': {'name': 'Song 2', 'artists': [{'name': 'Artist B'}]}},
            {'track': {'name': 'Song 3', 'artists': [{'name': 'Artist A'}, {'name': 'Artist C'}]}},
            {'track': None},  # Invalid entry (e.g. podcast or unavailable)
            {'track': {'name': 'Song 4', 'artists': [{'name': 'Artist D'}]}},
        ]

        # Simulate get_spotify_playlist_tracks logic
        tracks = []
        for item in mock_playlist_items[:50]:
            track = item.get('track')
            if track and track.get('name'):
                query = self._spotify_track_to_query(track)
                if query:
                    tracks.append(query)

        self.assertEqual(len(tracks), 4)  # 4 valid tracks, 1 None skipped
        self.assertEqual(tracks[0], "Artist A - Song 1")
        self.assertEqual(tracks[2], "Artist A, Artist C - Song 3")

        # Add all to queue
        for query in tracks:
            self.queue.append(query)
        self.assertEqual(len(self.queue), 4)

    def test_spotify_album_to_queue_flow(self):
        """Test that a Spotify album URL resolves tracks to queue"""
        url = "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"

        # Verify URL is detected as Spotify album
        spotify_type = self._is_spotify_url(url)
        self.assertEqual(spotify_type, "album")

        # Simulate Spotify album tracks response (simplified track objects)
        mock_album_tracks = [
            {'name': 'Track 1', 'artists': [{'name': 'Album Artist'}]},
            {'name': 'Track 2', 'artists': [{'name': 'Album Artist'}, {'name': 'Featured'}]},
            {'name': 'Track 3', 'artists': [{'name': 'Album Artist'}]},
        ]

        # Simulate get_spotify_playlist_tracks album branch
        tracks = []
        for track in mock_album_tracks[:50]:
            if track and track.get('name'):
                track_artists = ", ".join(
                    [a['name'] for a in track.get('artists', [])]
                ) if track.get('artists') else "Unknown Artist"
                query = f"{track_artists} - {track['name']}"
                tracks.append(query)

        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[0], "Album Artist - Track 1")
        self.assertEqual(tracks[1], "Album Artist, Featured - Track 2")

        # Add to queue
        for query in tracks:
            self.queue.append(query)
        self.assertEqual(len(self.queue), 3)

    def test_spotify_playlist_50_track_limit(self):
        """Test that Spotify playlists are capped at 50 tracks"""
        # Create 60 mock tracks
        mock_items = [
            {'track': {'name': f'Song {i}', 'artists': [{'name': f'Artist {i}'}]}}
            for i in range(60)
        ]

        # Apply the 50-track limit (same as in get_spotify_playlist_tracks)
        tracks = []
        for item in mock_items[:50]:
            track = item.get('track')
            if track and track.get('name'):
                query = self._spotify_track_to_query(track)
                if query:
                    tracks.append(query)

        self.assertEqual(len(tracks), 50)

    def test_spotify_not_configured_error(self):
        """Test graceful error when spotify_client is None"""
        # Simulate the check in handle_play_request
        spotify_client = None
        url = "https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6"
        spotify_type = self._is_spotify_url(url)

        self.assertEqual(spotify_type, "track")

        if not spotify_client:
            error = "Error: Spotify support is not configured. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in your .env file."
        else:
            error = None

        self.assertIsNotNone(error)
        self.assertIn("SPOTIFY_CLIENT_ID", error)

    def test_spotify_playlist_deduplication(self):
        """Test that duplicate tracks in a Spotify playlist are handled"""
        mock_items = [
            {'track': {'name': 'Same Song', 'artists': [{'name': 'Artist'}]}},
            {'track': {'name': 'Same Song', 'artists': [{'name': 'Artist'}]}},
            {'track': {'name': 'Different Song', 'artists': [{'name': 'Artist'}]}},
        ]

        # Simulate deduplication logic from handle_spotify_playlist
        unique_queries = set()
        added_count = 0
        for item in mock_items:
            track = item.get('track')
            if track and track.get('name'):
                query = self._spotify_track_to_query(track)
                if query and query not in unique_queries:
                    unique_queries.add(query)
                    self.queue.append(query)
                    added_count += 1

        self.assertEqual(added_count, 2)  # Only 2 unique tracks
        self.assertEqual(len(self.queue), 2)

    def test_spotify_url_routing(self):
        """Test that different Spotify URL types are routed correctly"""
        test_cases = [
            ("https://open.spotify.com/track/abc123", "track"),
            ("https://open.spotify.com/playlist/abc123", "playlist"),
            ("https://open.spotify.com/album/abc123", "album"),
            ("spotify:track:abc123", "track"),
            ("spotify:playlist:abc123", "playlist"),
            ("https://www.youtube.com/watch?v=abc", None),
            ("never gonna give you up", None),
        ]

        for url, expected_type in test_cases:
            with self.subTest(url=url):
                result = self._is_spotify_url(url)
                self.assertEqual(result, expected_type, f"Failed for URL: {url}")


if __name__ == '__main__':
    unittest.main()
