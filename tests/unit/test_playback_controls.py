import unittest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from collections import deque
import os
import sys

# Add parent directory to path to import bot module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bot import handle_skip_request, handle_play_request, handle_stop_request, queues, current_song


class TestPlaybackControls(unittest.TestCase):
    """Test playback control functions"""
    
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
        
        # Clear queues and current song for clean tests
        queues.clear()
        current_song.clear()
    
    def tearDown(self):
        """Clean up test environment"""
        self.loop.close()
        queues.clear()
        current_song.clear()
    
    def test_handle_skip_request_no_voice_client(self):
        """Test skip request when not connected to voice"""
        async def run_test():
            result = await handle_skip_request(self.mock_ctx)
            self.assertEqual(result, "Error: I'm not connected to a voice channel.")
        
        self.loop.run_until_complete(run_test())
    
    def test_handle_skip_request_nothing_playing(self):
        """Test skip request when nothing is playing"""
        # Mock voice client that's not playing
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = False
        mock_voice_client.is_paused.return_value = False
        self.mock_ctx.voice_client = mock_voice_client
        
        async def run_test():
            result = await handle_skip_request(self.mock_ctx)
            self.assertEqual(result, "Error: Nothing is playing right now.")
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.fix_queue')
    def test_handle_skip_request_success(self, mock_fix_queue):
        """Test successful skip request"""
        # Mock voice client that's playing
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = True
        mock_voice_client.is_paused.return_value = False
        mock_voice_client.stop = Mock()
        self.mock_ctx.voice_client = mock_voice_client
        
        # Set up current song
        guild_id_str = str(self.mock_ctx.guild.id)
        mock_current_song = Mock()
        mock_current_song.title = "Current Song"
        current_song[guild_id_str] = mock_current_song
        
        async def run_test():
            result = await handle_skip_request(self.mock_ctx)
            
            # Verify voice client stop was called
            mock_voice_client.stop.assert_called_once()
            
            # Verify fix_queue was called
            mock_fix_queue.assert_called_once_with(self.mock_ctx.guild.id)
            
            # Verify success message
            self.assertIn("Skipped", result)
        
        self.loop.run_until_complete(run_test())
    
    def test_handle_stop_request_no_voice_client(self):
        """Test stop request when not connected to voice"""
        async def run_test():
            result = await handle_stop_request(self.mock_ctx)
            self.assertEqual(result, "Error: I'm not connected to a voice channel.")
        
        self.loop.run_until_complete(run_test())
    
    def test_handle_stop_request_nothing_playing(self):
        """Test stop request when nothing is playing"""
        # Mock voice client that's not playing
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = False
        mock_voice_client.is_paused.return_value = False
        self.mock_ctx.voice_client = mock_voice_client
        
        async def run_test():
            result = await handle_stop_request(self.mock_ctx)
            self.assertEqual(result, "Error: Nothing is playing right now.")
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.fix_queue')
    def test_handle_stop_request_success(self, mock_fix_queue):
        """Test successful stop request"""
        # Mock voice client that's playing
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = True
        mock_voice_client.is_paused.return_value = False
        mock_voice_client.stop = Mock()
        self.mock_ctx.voice_client = mock_voice_client
        
        # Set up current song and queue
        guild_id_str = str(self.mock_ctx.guild.id)
        mock_current_song = Mock()
        mock_current_song.title = "Current Song"
        current_song[guild_id_str] = mock_current_song
        queues[guild_id_str] = deque(['url1', 'url2'])
        
        async def run_test():
            result = await handle_stop_request(self.mock_ctx)
            
            # Verify voice client stop was called
            mock_voice_client.stop.assert_called_once()
            
            # Verify queue was cleared
            self.assertEqual(len(queues[guild_id_str]), 0)
            
            # Verify current song was cleared
            self.assertIsNone(current_song.get(guild_id_str))
            
            # Verify success message
            self.assertEqual(result, "⏹ Playback stopped and queue cleared.")
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YTDLSource.from_url')
    @patch('bot.ensure_voice_connection')
    def test_handle_play_request_url_success(self, mock_ensure_voice, mock_from_url):
        """Test successful play request with URL"""
        # Mock voice client
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = False
        mock_voice_client.is_connected.return_value = True
        mock_voice_client.play = Mock()
        self.mock_ctx.voice_client = mock_voice_client
        
        # Mock YTDLSource
        mock_player = Mock()
        mock_player.title = "Test Song"
        mock_from_url.return_value = mock_player
        mock_ensure_voice.return_value = True
        
        async def run_test():
            result = await handle_play_request(self.mock_ctx, "https://youtube.com/watch?v=test")
            
            # Verify YTDLSource was created
            mock_from_url.assert_called_once()
            
            # Verify voice client play was called
            mock_voice_client.play.assert_called_once()
            
            # Verify success message
            self.assertIn("Now playing", result)
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YTDLSource.from_url')
    @patch('bot.ensure_voice_connection')
    def test_handle_play_request_search_success(self, mock_ensure_voice, mock_from_url):
        """Test successful play request with search term"""
        # Mock voice client
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = False
        mock_voice_client.is_connected.return_value = True
        mock_voice_client.play = Mock()
        self.mock_ctx.voice_client = mock_voice_client
        
        # Mock YTDLSource
        mock_player = Mock()
        mock_player.title = "Found Song"
        mock_from_url.return_value = mock_player
        mock_ensure_voice.return_value = True
        
        async def run_test():
            result = await handle_play_request(self.mock_ctx, "never gonna give you up")
            
            # Verify YTDLSource was created
            mock_from_url.assert_called_once()
            
            # Verify voice client play was called
            mock_voice_client.play.assert_called_once()
            
            # Verify success message
            self.assertIn("Found and playing", result)
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YTDLSource.is_url')
    def test_handle_play_request_queue_when_playing(self, mock_is_url):
        """Test that play request queues when already playing"""
        # Mock voice client that's already playing
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = True
        self.mock_ctx.voice_client = mock_voice_client
        
        # Mock URL detection
        mock_is_url.return_value = True
        
        async def run_test():
            result = await handle_play_request(self.mock_ctx, "https://youtube.com/watch?v=test")
            
            # Verify song was added to queue
            guild_id_str = str(self.mock_ctx.guild.id)
            self.assertIn(guild_id_str, queues)
            self.assertEqual(len(queues[guild_id_str]), 1)
            self.assertEqual(queues[guild_id_str][0], "https://youtube.com/watch?v=test")
            
            # Verify queue message
            self.assertIn("Added to queue", result)
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.YTDLSource.from_url')
    def test_handle_play_request_ytdl_error(self, mock_from_url):
        """Test handling of YTDL errors"""
        # Mock voice client
        mock_voice_client = Mock()
        mock_voice_client.is_playing.return_value = False
        self.mock_ctx.voice_client = mock_voice_client
        
        # Mock YTDLSource to raise error
        from bot import YTDLError
        mock_from_url.side_effect = YTDLError("Failed to extract info")
        
        async def run_test():
            result = await handle_play_request(self.mock_ctx, "https://youtube.com/watch?v=test")
            
            # Verify error message
            self.assertIn("Error", result)
            self.assertIn("Failed to extract info", result)
        
        self.loop.run_until_complete(run_test())


if __name__ == '__main__':
    unittest.main()
