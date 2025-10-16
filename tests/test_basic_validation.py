#!/usr/bin/env python3
"""
Basic validation tests for sacudo bot.
This script runs essential tests to catch syntax errors and basic functionality.
"""

import unittest
import ast
import os
import sys
import importlib.util


class BasicValidationTests(unittest.TestCase):
    """Basic validation tests for the sacudo bot"""
    
    def test_bot_py_syntax(self):
        """Test that bot.py has valid Python syntax"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bot.py')
        
        try:
            with open(bot_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Parse the AST to check for syntax errors
            ast.parse(source_code)
            
        except SyntaxError as e:
            self.fail(f"Syntax error in bot.py at line {e.lineno}: {e.msg}")
        except Exception as e:
            self.fail(f"Error reading bot.py: {e}")
    
    def test_cli_py_syntax(self):
        """Test that sacudo/cli.py has valid Python syntax"""
        cli_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sacudo', 'cli.py')
        
        try:
            with open(cli_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Parse the AST to check for syntax errors
            ast.parse(source_code)
            
        except SyntaxError as e:
            self.fail(f"Syntax error in sacudo/cli.py at line {e.lineno}: {e.msg}")
        except Exception as e:
            self.fail(f"Error reading sacudo/cli.py: {e}")
    
    def test_bot_py_key_functions_exist(self):
        """Test that key functions are properly defined in bot.py"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bot.py')
        
        try:
            with open(bot_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Parse the AST
            tree = ast.parse(source_code)
            
            # Find all function definitions (including async functions)
            function_names = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function_names.append(node.name)
            
            # Check for key functions (using actual function names from bot.py)
            expected_functions = [
                'handle_play_request',
                'handle_skip_request', 
                'handle_stop_request',
                'handle_playlist',
                'play_next',
                'ensure_voice_connection'
            ]
            
            missing_functions = []
            for func_name in expected_functions:
                if func_name not in function_names:
                    missing_functions.append(func_name)
            
            if missing_functions:
                # Debug: show what functions were actually found
                found_functions = [name for name in function_names if any(expected in name for expected in expected_functions)]
                self.fail(f"Missing required functions in bot.py: {missing_functions}. Found similar: {found_functions}")
                
        except Exception as e:
            self.fail(f"Error analyzing bot.py functions: {e}")
    
    def test_bot_py_key_classes_exist(self):
        """Test that key classes are properly defined in bot.py"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bot.py')
        
        try:
            with open(bot_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Parse the AST
            tree = ast.parse(source_code)
            
            # Find all class definitions
            class_names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_names.append(node.name)
            
            # Check for key classes
            expected_classes = ['YTDLSource', 'YTDLError']
            
            missing_classes = []
            for class_name in expected_classes:
                if class_name not in class_names:
                    missing_classes.append(class_name)
            
            if missing_classes:
                self.fail(f"Missing required classes in bot.py: {missing_classes}")
                
        except Exception as e:
            self.fail(f"Error analyzing bot.py classes: {e}")
    
    def test_no_stream_true_calls(self):
        """Test that no YTDLSource.from_url calls use stream=True"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bot.py')
        
        try:
            with open(bot_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Check for stream=True calls (should not exist after our changes)
            if 'stream=True' in source_code:
                self.fail("Found 'stream=True' in bot.py - should have been changed to stream=False")
            
            # Check that stream=False is used instead
            if 'stream=False' not in source_code:
                self.fail("No 'stream=False' found in bot.py - pre-download implementation may be missing")
                
        except Exception as e:
            self.fail(f"Error checking stream parameters in bot.py: {e}")
    
    def test_dashboard_removed_from_cli(self):
        """Test that dashboard options are removed from CLI"""
        cli_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sacudo', 'cli.py')
        
        try:
            with open(cli_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Check that dashboard options are removed
            if '--with-dashboard' in source_code:
                self.fail("Found '--with-dashboard' in sacudo/cli.py - should have been removed")
            
            if '--with-api' in source_code:
                self.fail("Found '--with-api' in sacudo/cli.py - should have been removed")
            
            # Check that Flask/SocketIO imports are handled gracefully
            if 'Flask' in source_code and 'API_AVAILABLE = False' not in source_code:
                self.fail("Flask imports found without API_AVAILABLE fallback in sacudo/cli.py")
                
        except Exception as e:
            self.fail(f"Error checking CLI changes: {e}")
    
    def test_requirements_txt_cleaned(self):
        """Test that requirements.txt has been cleaned of dashboard dependencies"""
        req_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'requirements.txt')
        
        try:
            with open(req_path, 'r', encoding='utf-8') as f:
                requirements = f.read()
            
            # Check that dashboard dependencies are removed
            dashboard_deps = ['Flask', 'Flask-CORS', 'Flask-SocketIO', 'eventlet']
            
            for dep in dashboard_deps:
                if dep in requirements:
                    self.fail(f"Found dashboard dependency '{dep}' in requirements.txt - should have been removed")
            
            # Check that core dependencies are still present
            core_deps = ['discord.py', 'python-dotenv', 'yt-dlp']
            
            for dep in core_deps:
                if dep not in requirements:
                    self.fail(f"Missing core dependency '{dep}' in requirements.txt")
                
        except Exception as e:
            self.fail(f"Error checking requirements.txt: {e}")


if __name__ == '__main__':
    # Create a simple test runner
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(BasicValidationTests)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print(f"\nTests run: {result.testsRun}")
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
        print("\nAll basic validation tests passed!")
        sys.exit(0)
    else:
        print("\nSome tests failed!")
        sys.exit(1)
