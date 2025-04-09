# Discord Music Bot Tests

This directory contains organized tests for the Discord music bot to ensure its functionality works correctly.

## Test Structure

The tests are organized into the following directories:

- **unit/**: Contains unit tests that verify individual components in isolation
  - `test_url_processing.py`: Tests for URL parsing and validation
  - `test_queue.py`: Tests for queue management and operations
  - `test_file_operations.py`: Tests for file-related operations
  - `test_url_validation.py`: Tests for URL validation logic
  
- **integration/**: Contains integration tests that verify components working together
  - `test_music_flow.py`: Tests the complete flow from URL to playback
  - `test_command_flow.py`: Tests command interactions
  
- **utils/**: Contains utility functions to help with testing
  - `test_helpers.py`: Common helper functions used across tests

## Running the Tests

You can run all tests with the master test runner:

```bash
python tests/run_all_tests.py
```

To run specific test categories:

```bash
# Run all unit tests
python -m unittest discover -s tests/unit

# Run all integration tests
python -m unittest discover -s tests/integration

# Run a specific test file
python -m unittest tests/unit/test_url_processing.py
```

## Test Approach

These tests are designed to verify the functionality of the Discord music bot without requiring an actual Discord connection or YouTube API access. They focus on:

1. **URL Processing**: Verifying that the bot correctly processes YouTube URLs
2. **Queue Management**: Testing the song queue and duplicate removal
3. **Command Flow**: Testing the flow of commands (play, skip, queue, etc.)

## Adding New Tests

When adding new tests:

1. Decide whether your test is a unit test (tests a single component) or an integration test (tests multiple components working together)
2. Create your test file in the appropriate directory with the `test_*.py` naming convention
3. Use the utilities in `tests/utils/test_helpers.py` for common functionality
4. Add your test to this README

## Test Guidelines

- Keep unit tests focused on a single piece of functionality
- Use descriptive test names that indicate what's being tested
- Avoid actual network calls in tests
- Mock external dependencies when needed
- Ensure all tests clean up after themselves 