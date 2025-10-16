import unittest
from collections import deque
import sys
import os

# Add parent directory to path to import bot module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bot import fix_queue, queues, current_song


class TestQueueManagement(unittest.TestCase):
    """Test queue management functionality"""
    
    def setUp(self):
        """Set up test environment"""
        # Clear queues and current song for clean tests
        queues.clear()
        current_song.clear()
    
    def tearDown(self):
        """Clean up test environment"""
        queues.clear()
        current_song.clear()
    
    def test_fix_queue_removes_duplicates(self):
        """Test that fix_queue removes duplicate URLs"""
        guild_id = 12345
        guild_id_str = str(guild_id)
        
        # Create queue with duplicates
        test_queue = deque([
            'https://youtube.com/watch?v=video1',
            'https://youtube.com/watch?v=video2',
            'https://youtube.com/watch?v=video1',  # Duplicate
            'https://youtube.com/watch?v=video3',
            'https://youtube.com/watch?v=video2'   # Duplicate
        ])
        queues[guild_id_str] = test_queue
        
        # Run fix_queue
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_test():
            await fix_queue(guild_id)
        
        loop.run_until_complete(run_test())
        loop.close()
        
        # Check that duplicates were removed
        self.assertEqual(len(queues[guild_id_str]), 3)
        unique_urls = set(queues[guild_id_str])
        self.assertEqual(len(unique_urls), 3)
        
        # Check specific URLs
        queue_list = list(queues[guild_id_str])
        self.assertIn('https://youtube.com/watch?v=video1', queue_list)
        self.assertIn('https://youtube.com/watch?v=video2', queue_list)
        self.assertIn('https://youtube.com/watch?v=video3', queue_list)
    
    def test_fix_queue_removes_current_song(self):
        """Test that fix_queue removes current song from queue"""
        guild_id = 12345
        guild_id_str = str(guild_id)
        
        # Create queue with current song
        current_url = 'https://youtube.com/watch?v=current'
        test_queue = deque([
            'https://youtube.com/watch?v=video1',
            current_url,  # Current song
            'https://youtube.com/watch?v=video2'
        ])
        queues[guild_id_str] = test_queue
        
        # Set current song
        mock_current_song = type('MockSong', (), {'url': current_url})()
        current_song[guild_id_str] = mock_current_song
        
        # Run fix_queue
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_test():
            await fix_queue(guild_id)
        
        loop.run_until_complete(run_test())
        loop.close()
        
        # Check that current song was removed
        self.assertEqual(len(queues[guild_id_str]), 2)
        queue_list = list(queues[guild_id_str])
        self.assertNotIn(current_url, queue_list)
        self.assertIn('https://youtube.com/watch?v=video1', queue_list)
        self.assertIn('https://youtube.com/watch?v=video2', queue_list)
    
    def test_fix_queue_handles_empty_queue(self):
        """Test that fix_queue handles empty queues gracefully"""
        guild_id = 12345
        guild_id_str = str(guild_id)
        
        # Create empty queue
        queues[guild_id_str] = deque()
        
        # Run fix_queue
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_test():
            await fix_queue(guild_id)
        
        loop.run_until_complete(run_test())
        loop.close()
        
        # Check that empty queue remains empty
        self.assertEqual(len(queues[guild_id_str]), 0)
    
    def test_fix_queue_handles_nonexistent_guild(self):
        """Test that fix_queue handles nonexistent guild gracefully"""
        guild_id = 99999  # Non-existent guild
        
        # Run fix_queue
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_test():
            await fix_queue(guild_id)
        
        # Should not raise an exception
        loop.run_until_complete(run_test())
        loop.close()
    
    def test_fix_queue_preserves_order(self):
        """Test that fix_queue preserves the order of unique URLs"""
        guild_id = 12345
        guild_id_str = str(guild_id)
        
        # Create queue with duplicates
        test_queue = deque([
            'https://youtube.com/watch?v=video1',
            'https://youtube.com/watch?v=video2',
            'https://youtube.com/watch?v=video1',  # Duplicate
            'https://youtube.com/watch?v=video3',
            'https://youtube.com/watch?v=video2',  # Duplicate
            'https://youtube.com/watch?v=video4'
        ])
        queues[guild_id_str] = test_queue
        
        # Run fix_queue
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_test():
            await fix_queue(guild_id)
        
        loop.run_until_complete(run_test())
        loop.close()
        
        # Check that order is preserved
        expected_order = [
            'https://youtube.com/watch?v=video1',
            'https://youtube.com/watch?v=video2',
            'https://youtube.com/watch?v=video3',
            'https://youtube.com/watch?v=video4'
        ]
        self.assertEqual(list(queues[guild_id_str]), expected_order)
    
    def test_fix_queue_handles_mixed_url_types(self):
        """Test that fix_queue handles mixed URL types correctly"""
        guild_id = 12345
        guild_id_str = str(guild_id)
        
        # Create queue with different URL types
        test_queue = deque([
            'https://youtube.com/watch?v=video1',
            'https://youtu.be/video2',
            'https://youtube.com/watch?v=video1',  # Duplicate
            'ytsearch:search term',
            'https://spotify.com/track/123'
        ])
        queues[guild_id_str] = test_queue
        
        # Run fix_queue
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def run_test():
            await fix_queue(guild_id)
        
        loop.run_until_complete(run_test())
        loop.close()
        
        # Check that duplicates were removed but different URLs preserved
        self.assertEqual(len(queues[guild_id_str]), 4)
        queue_list = list(queues[guild_id_str])
        
        # Should have one of each unique URL
        self.assertEqual(queue_list.count('https://youtube.com/watch?v=video1'), 1)
        self.assertIn('https://youtu.be/video2', queue_list)
        self.assertIn('ytsearch:search term', queue_list)
        self.assertIn('https://spotify.com/track/123', queue_list)


if __name__ == '__main__':
    unittest.main()
