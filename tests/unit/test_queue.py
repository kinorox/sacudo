import unittest
from collections import deque

class TestQueueManagement(unittest.TestCase):
    """Tests for the queue management logic"""
    
    def test_queue_duplicate_removal(self):
        """Test removing duplicates from a queue"""
        # Create a queue with duplicates
        queue = deque([
            "https://www.youtube.com/watch?v=url1",
            "https://www.youtube.com/watch?v=url2",
            "https://www.youtube.com/watch?v=url1",  # Duplicate
            "https://www.youtube.com/watch?v=url3",
            "https://www.youtube.com/watch?v=url2"   # Duplicate
        ])
        
        # Function to remove duplicates (similar to fix_queue in bot.py)
        def remove_duplicates(queue, current_song_url=None):
            new_queue = deque()
            unique_urls = set()
            
            for url in queue:
                # Skip URLs that match the currently playing song
                if current_song_url and url == current_song_url:
                    continue
                    
                if url not in unique_urls:
                    unique_urls.add(url)
                    new_queue.append(url)
            
            return new_queue
        
        # Test without current song
        fixed_queue = remove_duplicates(queue)
        
        # Check length and content
        self.assertEqual(len(fixed_queue), 3)
        self.assertIn("https://www.youtube.com/watch?v=url1", fixed_queue)
        self.assertIn("https://www.youtube.com/watch?v=url2", fixed_queue)
        self.assertIn("https://www.youtube.com/watch?v=url3", fixed_queue)
        
        # Test with current song
        current_song_url = "https://www.youtube.com/watch?v=url1"
        fixed_queue_with_current = remove_duplicates(queue, current_song_url)
        
        # Check length and content
        self.assertEqual(len(fixed_queue_with_current), 2)
        self.assertNotIn(current_song_url, fixed_queue_with_current)
        self.assertIn("https://www.youtube.com/watch?v=url2", fixed_queue_with_current)
        self.assertIn("https://www.youtube.com/watch?v=url3", fixed_queue_with_current)
    
    def test_queue_operations(self):
        """Test basic queue operations"""
        # Create an empty queue
        queue = deque()
        
        # Add songs
        queue.append("https://www.youtube.com/watch?v=song1")
        queue.append("https://www.youtube.com/watch?v=song2")
        queue.append("https://www.youtube.com/watch?v=song3")
        
        # Check queue length
        self.assertEqual(len(queue), 3)
        
        # Get the next song (FIFO)
        next_song = queue.popleft()
        self.assertEqual(next_song, "https://www.youtube.com/watch?v=song1")
        
        # Check queue length after pop
        self.assertEqual(len(queue), 2)
        
        # Check remaining songs
        self.assertEqual(queue[0], "https://www.youtube.com/watch?v=song2")
        self.assertEqual(queue[1], "https://www.youtube.com/watch?v=song3")
        
        # Clear the queue
        queue.clear()
        self.assertEqual(len(queue), 0)

if __name__ == '__main__':
    unittest.main() 