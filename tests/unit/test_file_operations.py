import unittest
import os

class TestFileOperations(unittest.TestCase):
    """Tests for file operations functionality"""

    def test_cookies_file_creation(self):
        """Test creating a cookies file directly"""
        cookies_file = 'test_cookies.txt'
        
        # Remove the file if it exists
        if os.path.exists(cookies_file):
            os.remove(cookies_file)
        
        # Create the cookies file
        try:
            with open(cookies_file, 'w') as f:
                # Write an empty cookies file
                f.write("# Netscape HTTP Cookie File\n")
            created = True
        except Exception:
            created = False
        
        # Check that the file was created
        self.assertTrue(created)
        self.assertTrue(os.path.exists(cookies_file))
        
        # Check the file content
        with open(cookies_file, 'r') as f:
            content = f.read()
            self.assertEqual(content, "# Netscape HTTP Cookie File\n")
        
        # Clean up
        os.remove(cookies_file)

if __name__ == '__main__':
    unittest.main() 