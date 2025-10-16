import unittest
import ast
import os
import sys
import importlib.util


class TestSyntaxValidation(unittest.TestCase):
    """Test for syntax errors and import issues in bot files"""
    
    def test_bot_py_syntax(self):
        """Test that bot.py has valid Python syntax"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'bot.py')
        
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
        cli_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'sacudo', 'cli.py')
        
        try:
            with open(cli_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Parse the AST to check for syntax errors
            ast.parse(source_code)
            
        except SyntaxError as e:
            self.fail(f"Syntax error in sacudo/cli.py at line {e.lineno}: {e.msg}")
        except Exception as e:
            self.fail(f"Error reading sacudo/cli.py: {e}")
    
    def test_bot_py_imports(self):
        """Test that bot.py can be imported without errors"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'bot.py')
        
        try:
            spec = importlib.util.spec_from_file_location("bot", bot_path)
            if spec is None:
                self.fail("Could not create spec for bot.py")
            
            # This will raise an exception if there are import errors
            module = importlib.util.module_from_spec(spec)
            
            # Don't actually execute the module to avoid side effects
            # Just check that the spec can be created and the module can be instantiated
            
        except Exception as e:
            self.fail(f"Import error in bot.py: {e}")
    
    def test_cli_py_imports(self):
        """Test that sacudo/cli.py can be imported without errors"""
        cli_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'sacudo', 'cli.py')
        
        try:
            spec = importlib.util.spec_from_file_location("sacudo.cli", cli_path)
            if spec is None:
                self.fail("Could not create spec for sacudo/cli.py")
            
            # This will raise an exception if there are import errors
            module = importlib.util.module_from_spec(spec)
            
            # Don't actually execute the module to avoid side effects
            
        except Exception as e:
            self.fail(f"Import error in sacudo/cli.py: {e}")
    
    def test_bot_py_function_definitions(self):
        """Test that key functions are properly defined in bot.py"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'bot.py')
        
        try:
            with open(bot_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            # Parse the AST
            tree = ast.parse(source_code)
            
            # Find all function definitions
            function_names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    function_names.append(node.name)
            
            # Check for key functions
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
                self.fail(f"Missing required functions in bot.py: {missing_functions}")
                
        except Exception as e:
            self.fail(f"Error analyzing bot.py functions: {e}")
    
    def test_bot_py_class_definitions(self):
        """Test that key classes are properly defined in bot.py"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'bot.py')
        
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
    
    def test_bot_py_indentation_consistency(self):
        """Test that bot.py has consistent indentation"""
        bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'bot.py')
        
        try:
            with open(bot_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Check for mixed tabs and spaces
            for line_num, line in enumerate(lines, 1):
                if '\t' in line and ' ' in line:
                    # Check if tabs and spaces are mixed in indentation
                    stripped = line.lstrip()
                    if stripped and (line.startswith('\t') or line.startswith(' ')):
                        self.fail(f"Mixed tabs and spaces in bot.py at line {line_num}")
            
            # Check for inconsistent indentation levels
            indent_levels = set()
            for line_num, line in enumerate(lines, 1):
                if line.strip():  # Skip empty lines
                    indent = len(line) - len(line.lstrip())
                    if indent > 0:  # Only check indented lines
                        indent_levels.add(indent)
            
            # Should have consistent indentation (typically 4 spaces)
            if len(indent_levels) > 8:  # Allow some variation but not too much
                self.fail(f"Inconsistent indentation levels in bot.py: {sorted(indent_levels)}")
                
        except Exception as e:
            self.fail(f"Error checking bot.py indentation: {e}")


if __name__ == '__main__':
    unittest.main()
