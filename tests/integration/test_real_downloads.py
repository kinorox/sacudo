#!/usr/bin/env python3
"""
Integration tests that test real YouTube downloads to catch issues like empty files.
These tests are slower but catch real-world problems.
"""

import unittest
import asyncio
import tempfile
import os
import sys
import shutil

# Add parent directory to path to import bot module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bot import YTDLSource, YTDLError


class TestRealDownloads(unittest.TestCase):
    """Test real YouTube downloads to catch network/file issues"""
    
    def setUp(self):
        """Set up test environment"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Create temp cache directory
        self.cache_dir = tempfile.mkdtemp(prefix="sacudo_real_test_")
        
        # Known working YouTube URLs for testing
        self.test_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # Rick Roll (short, reliable)
            "https://youtu.be/dQw4w9WgXcQ",  # Short URL version
        ]
    
    def tearDown(self):
        """Clean up test environment"""
        self.loop.close()
        # Clean up temp directory
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
    
    def test_real_download_success(self):
        """Test that real downloads work with known good URLs"""
        async def run_test():
            for url in self.test_urls:
                try:
                    print(f"Testing real download: {url}")
                    source = await YTDLSource.from_url(url, stream=False)
                    
                    # Verify source was created
                    self.assertIsNotNone(source)
                    self.assertIsNotNone(source.title)
                    self.assertIsNotNone(source.file_path)
                    
                    # Verify file exists and has content
                    if source.file_path and os.path.exists(source.file_path):
                        file_size = os.path.getsize(source.file_path)
                        self.assertGreater(file_size, 0, f"Downloaded file is empty: {source.file_path}")
                        print(f"✓ Successfully downloaded {source.title} ({file_size} bytes)")
                    else:
                        # If no file path, it might be streaming (fallback worked)
                        print(f"✓ Streaming fallback used for {source.title}")
                    
                    # Clean up
                    if hasattr(source, 'cleanup'):
                        source.cleanup()
                        
                except Exception as e:
                    self.fail(f"Real download failed for {url}: {e}")
        
        self.loop.run_until_complete(run_test())
    
    def test_real_download_empty_file_handling(self):
        """Test handling of empty file errors"""
        async def run_test():
            # Test with a URL that might cause empty file issues
            problematic_url = "https://youtu.be/iYUN-oFPKcY?si=xEIbYaXNZpBqIdhm"
            
            try:
                print(f"Testing problematic URL: {problematic_url}")
                source = await YTDLSource.from_url(problematic_url, stream=False)
                
                # If we get here, either download worked or fallback worked
                self.assertIsNotNone(source)
                self.assertIsNotNone(source.title)
                print(f"✓ Handled problematic URL: {source.title}")
                
                # Clean up
                if hasattr(source, 'cleanup'):
                    source.cleanup()
                    
            except YTDLError as e:
                # This is expected for some problematic URLs
                print(f"✓ Correctly handled problematic URL with error: {e}")
            except Exception as e:
                self.fail(f"Unexpected error for problematic URL: {e}")
        
        self.loop.run_until_complete(run_test())
    
    def test_search_term_handling(self):
        """Test that search terms work with real YouTube"""
        async def run_test():
            search_terms = [
                "rick roll",
                "never gonna give you up"
            ]
            
            for search_term in search_terms:
                try:
                    print(f"Testing search: {search_term}")
                    source = await YTDLSource.from_url(search_term, stream=False)
                    
                    # Verify source was created
                    self.assertIsNotNone(source)
                    self.assertIsNotNone(source.title)
                    print(f"✓ Search worked: {source.title}")
                    
                    # Clean up
                    if hasattr(source, 'cleanup'):
                        source.cleanup()
                        
                except Exception as e:
                    self.fail(f"Search failed for '{search_term}': {e}")
        
        self.loop.run_until_complete(run_test())
    
    def test_fallback_mechanism(self):
        """Test that fallback to streaming works when download fails"""
        async def run_test():
            # This test verifies the fallback mechanism works
            # We can't easily force a download failure, but we can test the fallback method directly
            
            try:
                # Test the fallback method directly
                fallback_source = await YTDLSource._fallback_to_streaming(
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
                )
                
                self.assertIsNotNone(fallback_source)
                self.assertIsNotNone(fallback_source.title)
                print(f"✓ Fallback mechanism works: {fallback_source.title}")
                
            except Exception as e:
                self.fail(f"Fallback mechanism failed: {e}")
        
        self.loop.run_until_complete(run_test())


if __name__ == '__main__':
    # Create a test suite for integration tests
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestRealDownloads)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print(f"\nIntegration Tests Summary:")
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
        print("\nAll integration tests passed!")
    else:
        print("\nSome integration tests failed!")
