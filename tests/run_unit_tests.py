#!/usr/bin/env python3
"""
Run all unit tests for the sacudo bot.
This script runs all unit tests and provides a summary of results.
"""

import unittest
import sys
import os
from io import StringIO

# Add the tests directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def run_all_tests():
    """Run all unit tests and return results"""
    
    # Test modules to run
    test_modules = [
        'test_syntax_validation',
        'test_ytdl_source', 
        'test_playlist_handling',
        'test_playback_controls',
        'test_queue_management'
    ]
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test modules
    for module_name in test_modules:
        try:
            # Import from unit subdirectory
            module_path = f"unit.{module_name}"
            module = __import__(module_path, fromlist=[module_name])
            tests = loader.loadTestsFromModule(module)
            suite.addTests(tests)
            print(f"[OK] Loaded {module_name}")
        except ImportError as e:
            print(f"[FAIL] Failed to load {module_name}: {e}")
        except Exception as e:
            print(f"[ERROR] Error loading {module_name}: {e}")
    
    # Run tests
    print(f"\nRunning {suite.countTestCases()} tests...")
    print("=" * 50)
    
    # Capture output
    stream = StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)
    
    # Print results
    print(stream.getvalue())
    
    # Summary
    print("=" * 50)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    
    if result.failures:
        print("\nFAILURES:")
        for test, traceback in result.failures:
            error_msg = traceback.split('AssertionError: ')[-1].split('\n')[0]
            print(f"  {test}: {error_msg}")
    
    if result.errors:
        print("\nERRORS:")
        for test, traceback in result.errors:
            error_msg = traceback.split('\n')[-2]
            print(f"  {test}: {error_msg}")
    
    # Return success status
    return len(result.failures) == 0 and len(result.errors) == 0

if __name__ == '__main__':
    success = run_all_tests()
    
    if success:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed!")
        sys.exit(1)
