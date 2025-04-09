import unittest
from collections import deque
import re

class TestMusicFlowIntegration(unittest.TestCase):
    """Integration tests for music playback flow"""
    
    def test_complete_music_flow(self):
        """Test the complete flow from URL to queue to playback"""
        
        # 1. Create a queue
        queue = deque()
        
        # 2. URL Validation Function
        def is_url(text):
            """Check if the provided text is a URL."""
            # Standard URL patterns
            if text.startswith(('http://', 'https://')):
                return True
                
            # YouTube shortened URLs
            if text.startswith(('youtu.be/', 'youtube.com/', 'www.youtube.com/')):
                return True
                
            # Other common music services
            if any(domain in text for domain in ['spotify.com', 'soundcloud.com', 'bandcamp.com']):
                return True
                
            return False
        
        # 3. Extract Video ID Function
        def extract_video_id(url):
            """Extract YouTube video ID from URL."""
            pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
            match = re.search(pattern, url)
            if match:
                return match.group(1)
            return None
        
        # 4. Queue Management Function
        def add_to_queue(queue, url, current_playing=None):
            """Add a validated URL to the queue, avoiding duplicates"""
            # Skip if it's the currently playing song
            if current_playing and url == current_playing:
                return False
                
            # Skip if it's a duplicate in the queue
            if url in queue:
                return False
                
            # Add to queue
            queue.append(url)
            return True
        
        # 5. Mock Playback Function
        def play_next(queue, current_playing=None):
            """Simulate playing the next song"""
            if not queue:
                return None
            
            next_url = queue.popleft()
            
            # Extract ID and generate details
            video_id = extract_video_id(next_url)
            if video_id:
                return {
                    'url': next_url,
                    'video_id': video_id,
                    'title': f"Test Song {video_id}",
                    'thumbnail': f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                }
            return None
        
        # Test the flow:
        
        # 1. Test URL validation
        url1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        url2 = "https://www.youtube.com/watch?v=abcdefghijk"
        url3 = "just a search query"
        
        self.assertTrue(is_url(url1))
        self.assertTrue(is_url(url2))
        self.assertFalse(is_url(url3))
        
        # 2. Test adding to queue
        self.assertTrue(add_to_queue(queue, url1))
        self.assertTrue(add_to_queue(queue, url2))
        self.assertFalse(add_to_queue(queue, url1))  # Duplicate should be rejected
        
        self.assertEqual(len(queue), 2)
        
        # 3. Test playing from queue
        song = play_next(queue)
        self.assertIsNotNone(song)
        self.assertEqual(song['url'], url1)
        self.assertEqual(song['video_id'], "dQw4w9WgXcQ")
        self.assertEqual(song['title'], "Test Song dQw4w9WgXcQ")
        
        # 4. Test queue is updated after playing
        self.assertEqual(len(queue), 1)
        
        # 5. Test playing the next song
        song2 = play_next(queue)
        self.assertIsNotNone(song2)
        self.assertEqual(song2['url'], url2)
        self.assertEqual(song2['video_id'], "abcdefghijk")
        
        # 6. Test empty queue
        self.assertEqual(len(queue), 0)
        song3 = play_next(queue)
        self.assertIsNone(song3)

if __name__ == '__main__':
    unittest.main() 