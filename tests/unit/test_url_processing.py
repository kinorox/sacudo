import unittest
import re

class TestURLProcessing(unittest.TestCase):
    """Tests for URL processing functionality"""
    
    def test_youtube_id_extraction(self):
        """Test extracting YouTube video ID from different URL formats"""
        
        def extract_video_id(url):
            """Extract YouTube video ID from URL."""
            # Standard YouTube URL format
            if "youtube.com/watch" in url and "v=" in url:
                video_id = url.split("v=")[-1]
                # Remove any additional parameters
                if "&" in video_id:
                    video_id = video_id.split("&")[0]
                return video_id
            
            # YouTube shortened URL format
            elif "youtu.be/" in url:
                video_id = url.split("youtu.be/")[-1]
                # Remove any additional parameters
                if "?" in video_id:
                    video_id = video_id.split("?")[0]
                return video_id
            
            # Not a recognized YouTube URL format
            return None
        
        # Test standard YouTube URL
        url1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(extract_video_id(url1), "dQw4w9WgXcQ")
        
        # Test YouTube URL with additional parameters
        url2 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s&feature=youtu.be"
        self.assertEqual(extract_video_id(url2), "dQw4w9WgXcQ")
        
        # Test shortened YouTube URL
        url3 = "https://youtu.be/dQw4w9WgXcQ"
        self.assertEqual(extract_video_id(url3), "dQw4w9WgXcQ")
        
        # Test shortened YouTube URL with parameters
        url4 = "https://youtu.be/dQw4w9WgXcQ?t=10"
        self.assertEqual(extract_video_id(url4), "dQw4w9WgXcQ")
        
        # Test non-YouTube URL
        url5 = "https://example.com/video"
        self.assertIsNone(extract_video_id(url5))
    
    def test_regex_video_id_extraction(self):
        """Test extracting video ID using regex pattern"""
        
        def extract_video_id_regex(url):
            """Extract YouTube video ID using regex."""
            pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
            match = re.search(pattern, url)
            if match:
                return match.group(1)
            return None
        
        # Test standard YouTube URL
        url1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(extract_video_id_regex(url1), "dQw4w9WgXcQ")
        
        # Test YouTube URL with additional parameters
        url2 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s"
        self.assertEqual(extract_video_id_regex(url2), "dQw4w9WgXcQ")
        
        # Test shortened YouTube URL
        url3 = "https://youtu.be/dQw4w9WgXcQ"
        self.assertEqual(extract_video_id_regex(url3), "dQw4w9WgXcQ")
        
        # Test invalid URL
        url4 = "https://example.com/video"
        self.assertIsNone(extract_video_id_regex(url4))
    
    def test_thumbnail_url_generation(self):
        """Test generating thumbnail URL from video ID"""
        
        def get_thumbnail_url(video_id):
            """Generate YouTube thumbnail URL from video ID."""
            return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        
        # Test with valid video ID
        video_id = "dQw4w9WgXcQ"
        expected_url = "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        self.assertEqual(get_thumbnail_url(video_id), expected_url)
        
        # Test complete flow: extract ID from URL and generate thumbnail URL
        def get_thumbnail_from_url(url):
            """Extract video ID from URL and generate thumbnail URL."""
            pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
            match = re.search(pattern, url)
            if match:
                video_id = match.group(1)
                return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            return "https://i.imgur.com/ufxvZ0j.png"  # Default image
        
        # Test standard YouTube URL
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        expected_url = "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        self.assertEqual(get_thumbnail_from_url(url), expected_url)
        
        # Test invalid URL
        invalid_url = "https://example.com/video"
        default_url = "https://i.imgur.com/ufxvZ0j.png"
        self.assertEqual(get_thumbnail_from_url(invalid_url), default_url)


if __name__ == '__main__':
    unittest.main() 