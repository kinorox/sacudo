import unittest
import asyncio
import tempfile
import os
from unittest.mock import Mock, patch, AsyncMock
import discord
from yt_dlp import YoutubeDL

# Add parent directory to path to import bot module
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bot import YTDLSource, YTDLError


class TestYTDLSource(unittest.TestCase):
    """Test YTDLSource pre-download functionality"""
    
    def setUp(self):
        """Set up test environment"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Create temp cache directory
        self.cache_dir = tempfile.mkdtemp(prefix="sacudo_test_")
        
        # Mock data for successful download
        self.mock_data = {
            'id': 'test123',
            'title': 'Test Song',
            'webpage_url': 'https://youtube.com/watch?v=test123',
            'url': 'https://youtube.com/watch?v=test123',
            'ext': 'm4a',
            'requested_downloads': [{
                'filepath': os.path.join(self.cache_dir, 'test123.m4a')
            }]
        }
    
    def tearDown(self):
        """Clean up test environment"""
        self.loop.close()
        # Clean up temp directory
        import shutil
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
    
    def test_is_url_detection(self):
        """Test URL detection for various formats"""
        # Valid URLs
        self.assertTrue(YTDLSource.is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(YTDLSource.is_url("https://youtu.be/dQw4w9WgXcQ"))
        self.assertTrue(YTDLSource.is_url("https://youtube.com/playlist?list=PLrAXtmRdnEQy6nuLM"))
        self.assertTrue(YTDLSource.is_url("https://spotify.com/track/123"))
        
        # Invalid URLs (search terms)
        self.assertFalse(YTDLSource.is_url("never gonna give you up"))
        self.assertFalse(YTDLSource.is_url("rick roll"))
        self.assertFalse(YTDLSource.is_url(""))
    
    @patch('bot.song_cache', {})
    @patch('os.makedirs')
    @patch('yt_dlp.YoutubeDL')
    def test_from_url_success(self, mock_ydl_class, mock_makedirs):
        """Test successful audio download and source creation"""
        # Mock YoutubeDL instance
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = self.mock_data
        
        # Mock FFmpegPCMAudio
        with patch('discord.FFmpegPCMAudio') as mock_ffmpeg:
            mock_source = Mock()
            mock_ffmpeg.return_value = mock_source
            
            # Create a temporary file for the mock download
            test_file = os.path.join(self.cache_dir, 'test123.m4a')
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(test_file, 'w') as f:
                f.write('mock audio data')
            
            async def run_test():
                source = await YTDLSource.from_url("https://youtube.com/watch?v=test123")
                
                # Verify YoutubeDL was called correctly
                mock_ydl.extract_info.assert_called_once_with("https://youtube.com/watch?v=test123", download=True)
                
                # Verify FFmpegPCMAudio was created with correct file path
                mock_ffmpeg.assert_called_once()
                call_args = mock_ffmpeg.call_args[0]
                self.assertEqual(call_args[0], test_file)
                
                # Verify source properties
                self.assertEqual(source.title, "Test Song")
                self.assertEqual(source.file_path, test_file)
                self.assertEqual(source.volume, 0.8)
            
            self.loop.run_until_complete(run_test())
    
    @patch('bot.song_cache', {})
    @patch('os.makedirs')
    @patch('yt_dlp.YoutubeDL')
    def test_from_url_search_term(self, mock_ydl_class, mock_makedirs):
        """Test handling of search terms (non-URLs)"""
        # Mock YoutubeDL instance
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        
        # Mock search results
        search_data = {
            'entries': [self.mock_data]
        }
        mock_ydl.extract_info.return_value = search_data
        
        # Mock FFmpegPCMAudio
        with patch('discord.FFmpegPCMAudio') as mock_ffmpeg:
            mock_source = Mock()
            mock_ffmpeg.return_value = mock_source
            
            # Create a temporary file for the mock download
            test_file = os.path.join(self.cache_dir, 'test123.m4a')
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(test_file, 'w') as f:
                f.write('mock audio data')
            
            async def run_test():
                source = await YTDLSource.from_url("never gonna give you up")
                
                # Verify search URL was used
                expected_url = "ytsearch:never gonna give you up"
                mock_ydl.extract_info.assert_called_once_with(expected_url, download=True)
                
                # Verify source was created correctly
                self.assertEqual(source.title, "Test Song")
            
            self.loop.run_until_complete(run_test())
    
    @patch('bot.song_cache', {})
    @patch('os.makedirs')
    @patch('yt_dlp.YoutubeDL')
    def test_from_url_extraction_failure(self, mock_ydl_class, mock_makedirs):
        """Test handling of extraction failures"""
        # Mock YoutubeDL instance
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = None
        
        async def run_test():
            with self.assertRaises(YTDLError):
                await YTDLSource.from_url("https://youtube.com/watch?v=invalid")
        
        self.loop.run_until_complete(run_test())
    
    @patch('bot.song_cache', {})
    @patch('os.makedirs')
    @patch('yt_dlp.YoutubeDL')
    def test_from_url_no_file_path(self, mock_ydl_class, mock_makedirs):
        """Test handling when no file path is available"""
        # Mock YoutubeDL instance with incomplete data
        mock_ydl = Mock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        
        incomplete_data = {
            'id': 'test123',
            'title': 'Test Song',
            'webpage_url': 'https://youtube.com/watch?v=test123',
            'url': 'https://youtube.com/watch?v=test123',
            'ext': 'm4a',
            # Missing 'requested_downloads'
        }
        mock_ydl.extract_info.return_value = incomplete_data
        
        async def run_test():
            with self.assertRaises(YTDLError):
                await YTDLSource.from_url("https://youtube.com/watch?v=test123")
        
        self.loop.run_until_complete(run_test())
    
    def test_cleanup_method(self):
        """Test cleanup of downloaded files"""
        # Create a mock source with file path
        mock_source = Mock()
        mock_data = {'title': 'Test Song'}
        source = YTDLSource(mock_source, data=mock_data)
        source.file_path = os.path.join(self.cache_dir, 'test123.m4a')
        
        # Create the test file
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(source.file_path, 'w') as f:
            f.write('mock audio data')
        
        # Verify file exists
        self.assertTrue(os.path.exists(source.file_path))
        
        # Call cleanup
        source.cleanup()
        
        # Verify file was deleted
        self.assertFalse(os.path.exists(source.file_path))


if __name__ == '__main__':
    unittest.main()
