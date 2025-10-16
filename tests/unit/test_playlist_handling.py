import unittest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from collections import deque
import os
import sys

# Add parent directory to path to import bot module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bot import handle_playlist, queues, YTDLSource


class TestPlaylistHandling(unittest.TestCase):
    """Test playlist handling functionality"""
    
    def setUp(self):
        """Set up test environment"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Create mock context
        self.mock_ctx = Mock()
        self.mock_ctx.guild.id = 12345
        self.mock_ctx.guild.voice_client = None
        self.mock_ctx.voice_client = None
        self.mock_ctx.send = AsyncMock()
        self.mock_ctx.invoke = AsyncMock()
        
        # Clear queues for clean tests
        queues.clear()
    
    def tearDown(self):
        """Clean up test environment"""
        self.loop.close()
        queues.clear()
    
    @patch('bot.YoutubeDL')
    @patch('bot.play_next')
    def test_handle_playlist_success(self, mock_play_next, mock_ydl_class):
        """Test successful playlist handling"""
        # Mock YoutubeDL instance
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        
        # Mock playlist data
        mock_playlist_data = {
            'title': 'Test Playlist',
            'entries': [
                {'url': 'https://youtube.com/watch?v=video1'},
                {'url': 'https://youtube.com/watch?v=video2'},
                {'url': 'https://youtube.com/watch?v=video3'},
            ]
        }
        mock_ydl.extract_info.return_value = mock_playlist_data
        
        async def run_test():
            result = await handle_playlist(self.mock_ctx, "https://youtube.com/playlist?list=test")
            
            # Verify queue was populated
            guild_id_str = str(self.mock_ctx.guild.id)
            self.assertIn(guild_id_str, queues)
            self.assertEqual(len(queues[guild_id_str]), 3)
            
            # Verify URLs were added correctly
            queue_urls = list(queues[guild_id_str])
            self.assertIn('https://youtube.com/watch?v=video1', queue_urls)
            self.assertIn('https://youtube.com/watch?v=video2', queue_urls)
            self.assertIn('https://youtube.com/watch?v=video3', queue_urls)
            
            # Verify play_next was called
            mock_play_next.assert_called_once_with(self.mock_ctx)
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YoutubeDL')
    def test_handle_playlist_deduplication(self, mock_ydl_class):
        """Test playlist deduplication"""
        # Mock YoutubeDL instance
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        
        # Mock playlist data with duplicates
        mock_playlist_data = {
            'title': 'Test Playlist',
            'entries': [
                {'url': 'https://youtube.com/watch?v=video1'},
                {'url': 'https://youtube.com/watch?v=video2'},
                {'url': 'https://youtube.com/watch?v=video1'},  # Duplicate
                {'url': 'https://youtube.com/watch?v=video3'},
                {'url': 'https://youtube.com/watch?v=video2'},  # Duplicate
            ]
        }
        mock_ydl.extract_info.return_value = mock_playlist_data
        
        async def run_test():
            await handle_playlist(self.mock_ctx, "https://youtube.com/playlist?list=test")
            
            # Verify queue was populated without duplicates
            guild_id_str = str(self.mock_ctx.guild.id)
            self.assertIn(guild_id_str, queues)
            self.assertEqual(len(queues[guild_id_str]), 3)  # Should be 3 unique URLs
            
            # Verify no duplicates
            queue_urls = list(queues[guild_id_str])
            unique_urls = set(queue_urls)
            self.assertEqual(len(queue_urls), len(unique_urls))
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YoutubeDL')
    def test_handle_playlist_no_entries(self, mock_ydl_class):
        """Test handling of playlist with no entries"""
        # Mock YoutubeDL instance
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        
        # Mock empty playlist data
        mock_playlist_data = {
            'title': 'Empty Playlist',
            'entries': []
        }
        mock_ydl.extract_info.return_value = mock_playlist_data
        
        async def run_test():
            await handle_playlist(self.mock_ctx, "https://youtube.com/playlist?list=empty")
            
            # Verify error message was sent
            self.mock_ctx.send.assert_called_with("❌ No valid songs found in the playlist.")
            
            # Verify queue was not populated
            guild_id_str = str(self.mock_ctx.guild.id)
            self.assertNotIn(guild_id_str, queues)
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YoutubeDL')
    def test_handle_playlist_extraction_error(self, mock_ydl_class):
        """Test handling of playlist extraction errors"""
        # Mock YoutubeDL instance that raises an exception
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = Exception("Network error")
        
        async def run_test():
            await handle_playlist(self.mock_ctx, "https://youtube.com/playlist?list=error")
            
            # Verify error message was sent
            self.mock_ctx.send.assert_called_with("❌ Error processing playlist: Network error")
            
            # Verify queue was not populated
            guild_id_str = str(self.mock_ctx.guild.id)
            self.assertNotIn(guild_id_str, queues)
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YoutubeDL')
    @patch('bot.play_next')
    def test_handle_playlist_already_playing(self, mock_play_next, mock_ydl_class):
        """Test playlist handling when bot is already playing"""
        # Mock YoutubeDL instance
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        
        # Mock playlist data
        mock_playlist_data = {
            'title': 'Test Playlist',
            'entries': [
                {'url': 'https://youtube.com/watch?v=video1'},
                {'url': 'https://youtube.com/watch?v=video2'},
            ]
        }
        mock_ydl.extract_info.return_value = mock_playlist_data
        
        # Mock voice client that's already playing
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = True
        self.mock_ctx.voice_client = mock_voice_client
        
        async def run_test():
            await handle_playlist(self.mock_ctx, "https://youtube.com/playlist?list=test")
            
            # Verify queue was populated
            guild_id_str = str(self.mock_ctx.guild.id)
            self.assertIn(guild_id_str, queues)
            self.assertEqual(len(queues[guild_id_str]), 2)
            
            # Verify play_next was NOT called (already playing)
            mock_play_next.assert_not_called()
            
            # Verify appropriate message was sent
            self.mock_ctx.send.assert_called_with("🎵 Added 2 songs from the playlist to the queue.")
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YoutubeDL')
    def test_handle_playlist_voice_channel_join(self, mock_ydl_class):
        """Test that bot joins voice channel if not connected"""
        # Mock YoutubeDL instance
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        
        # Mock playlist data
        mock_playlist_data = {
            'title': 'Test Playlist',
            'entries': [
                {'url': 'https://youtube.com/watch?v=video1'},
            ]
        }
        mock_ydl.extract_info.return_value = mock_playlist_data
        
        # Mock join command
        mock_join = Mock()
        self.mock_ctx.invoke.return_value = mock_join
        
        async def run_test():
            await handle_playlist(self.mock_ctx, "https://youtube.com/playlist?list=test")
            
            # Verify join was called
            self.mock_ctx.invoke.assert_called_once()
        
        self.loop.run_until_complete(run_test())


if __name__ == '__main__':
    unittest.main()
