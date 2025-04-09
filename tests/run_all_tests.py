import unittest
import sys
import os

# Add the parent directory to the path so we can import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if __name__ == '__main__':
    # Create a test loader
    loader = unittest.TestLoader()
    
    # Discover all tests in the tests directory
    start_dir = os.path.dirname(os.path.abspath(__file__))
    test_suite = loader.discover(start_dir, pattern='test_*.py')
    
    # Create a test runner with verbosity
    runner = unittest.TextTestRunner(verbosity=2)
    
    # Run the tests
    result = runner.run(test_suite)
    
    # Print a summary
    print(f"\nTest Summary:")
    print(f"  Ran {result.testsRun} tests")
    print(f"  Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failures: {len(result.failures)}")
    print(f"  Errors: {len(result.errors)}")
    
    # Exit with non-zero code if there were failures or errors
    sys.exit(len(result.failures) + len(result.errors)) 