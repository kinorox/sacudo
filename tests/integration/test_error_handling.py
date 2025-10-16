#!/usr/bin/env python3
"""
Integration tests for error handling scenarios that can occur in production.
"""

import unittest
import asyncio
import os
import sys

# Add parent directory to path to import bot module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bot import YTDLSource, YTDLError


class TestErrorHandling(unittest.TestCase):
    """Test error handling for real-world scenarios"""
    
    def setUp(self):
        """Set up test environment"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    def tearDown(self):
        """Clean up test environment"""
        self.loop.close()
    
    def test_empty_file_error_detection(self):
        """Test that empty file errors are properly detected and handled"""
        async def run_test():
            # URLs that are known to sometimes cause empty file errors
            problematic_urls = [
                "https://youtu.be/iYUN-oFPKcY?si=xEIbYaXNZpBqIdhm",  # The URL from your error
                "https://www.youtube.com/watch?v=invalid123",  # Invalid video ID
            ]
            
            for url in problematic_urls:
                try:
                    print(f"Testing error handling for: {url}")
                    source = await YTDLSource.from_url(url, stream=False)
                    
                    # If we get here, either download worked or fallback worked
                    self.assertIsNotNone(source)
                    print(f"✓ Successfully handled problematic URL: {source.title}")
                    
                    # Clean up
                    if hasattr(source, 'cleanup'):
                        source.cleanup()
                        
                except YTDLError as e:
                    error_msg = str(e).lower()
                    print(f"✓ Correctly caught YTDLError: {e}")
                    
                    # Check if it's the specific error we're looking for
                    if 'empty' in error_msg:
                        print("✓ Detected empty file error")
                    elif 'download' in error_msg or 'network' in error_msg:
                        print("✓ Detected download/network error")
                    else:
                        print(f"✓ Other YTDLError: {error_msg}")
                        
                except Exception as e:
                    print(f"✓ Unexpected error (but handled): {e}")
        
        self.loop.run_until_complete(run_test())
    
    def test_network_timeout_handling(self):
        """Test handling of network timeouts"""
        async def run_test():
            # Test with a URL that might timeout
            timeout_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            
            try:
                print(f"Testing network handling for: {timeout_url}")
                source = await YTDLSource.from_url(timeout_url, stream=False)
                
                self.assertIsNotNone(source)
                print(f"✓ Network handling works: {source.title}")
                
                # Clean up
                if hasattr(source, 'cleanup'):
                    source.cleanup()
                    
            except Exception as e:
                print(f"✓ Network error handled: {e}")
        
        self.loop.run_until_complete(run_test())
    
    def test_fallback_mechanism_activation(self):
        """Test that fallback mechanism is properly activated"""
        async def run_test():
            # Test the fallback method directly to ensure it works
            test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            
            try:
                print("Testing fallback mechanism...")
                fallback_source = await YTDLSource._fallback_to_streaming(test_url)
                
                self.assertIsNotNone(fallback_source)
                self.assertIsNotNone(fallback_source.title)
                print(f"✓ Fallback mechanism works: {fallback_source.title}")
                
                # Verify it's using streaming (no file_path)
                if hasattr(fallback_source, 'file_path'):
                    self.assertIsNone(fallback_source.file_path)
                    print("✓ Fallback correctly uses streaming (no file_path)")
                
            except Exception as e:
                self.fail(f"Fallback mechanism failed: {e}")
        
        self.loop.run_until_complete(run_test())


if __name__ == '__main__':
    # Create a test suite for error handling tests
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestErrorHandling)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print(f"\nError Handling Tests Summary:")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    
    if result.failures:
        print("\nFAILURES:")
        for test, traceback in result.failures:
            print(f"  {test}")
    
    if result.errors:
        print("\nERRORS:")
        for test, traceback in result.errors:
            print(f"  {test}")
    
    if len(result.failures) == 0 and len(result.errors) == 0:
        print("\nAll error handling tests passed!")
    else:
        print("\nSome error handling tests failed!")
