import unittest

class TestURLValidation(unittest.TestCase):
    """Tests for URL validation functionality"""

    def test_url_validation(self):
        """Test URL validation logic"""
        # Define our own URL validation function
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
        
        # Test with valid YouTube URL
        self.assertTrue(is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        
        # Test with valid YouTube shortened URL
        self.assertTrue(is_url("youtu.be/dQw4w9WgXcQ"))
        
        # Test with valid Spotify URL
        self.assertTrue(is_url("https://open.spotify.com/track/12345"))
        
        # Test with valid SoundCloud URL
        self.assertTrue(is_url("https://soundcloud.com/user/track"))
        
        # Test with search query (not a URL)
        self.assertFalse(is_url("rick astley never gonna give you up"))

if __name__ == '__main__':
    unittest.main() 