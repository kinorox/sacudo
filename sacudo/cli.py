#!/usr/bin/env python3
"""
CLI entry point for the Sacudo Discord bot.
"""

import argparse
import sys
import os
import subprocess
import threading
import time
import signal
from pathlib import Path

# Add the parent directory to the path so we can import bot.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Global variables for process management
dashboard_process = None

def check_node_installed():
    """Check if Node.js is installed and available."""
    try:
        result = subprocess.run(['node', '--version'], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False

def start_dashboard():
    """Start the React dashboard."""
    global dashboard_process
    
    # Get the project root directory
    project_root = Path(__file__).parent.parent
    dashboard_dir = project_root / "dashboard"
    
    if not dashboard_dir.exists():
        print("❌ Error: Dashboard directory not found!")
        return False
    
    # Check if node_modules exists
    node_modules = dashboard_dir / "node_modules"
    if not node_modules.exists():
        print("📦 Installing dashboard dependencies...")
        try:
            subprocess.run(['npm', 'install'], cwd=dashboard_dir, check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ Error installing dashboard dependencies: {e}")
            return False
    
    # Start the React development server
    print("🚀 Starting React dashboard...")
    try:
        dashboard_process = subprocess.Popen(
            ['npm', 'start'],
            cwd=dashboard_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error starting dashboard: {e}")
        return False
    except FileNotFoundError:
        print("❌ Error: npm not found. Please install Node.js")
        return False

def cleanup_processes():
    """Clean up dashboard process on exit."""
    global dashboard_process
    
    if dashboard_process:
        print("🛑 Stopping dashboard...")
        try:
            if sys.platform == "win32":
                # On Windows, kill the entire process group
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(dashboard_process.pid)], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # On Unix-like systems
                dashboard_process.terminate()
                dashboard_process.wait(timeout=5)
        except (subprocess.TimeoutExpired, Exception):
            if dashboard_process:
                dashboard_process.kill()
        dashboard_process = None

def signal_handler(signum, frame):
    """Handle interrupt signals."""
    print("\n👋 Shutting down Sacudo...")
    cleanup_processes()
    sys.exit(0)

def main():
    """Main entry point for the sacudo command."""
    parser = argparse.ArgumentParser(
        description="Sacudo - Discord YouTube Music Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sacudo                      # Run bot
  sacudo --help               # Show this help message
        """
    )
    
    # Dashboard/API removed for a minimal bot experience
    
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0"
    )
    
    args = parser.parse_args()
    
    # No dashboard/API modes
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Import bot module
        import bot
        
        # Check if required environment variables are set
        if not os.getenv("BOT_TOKEN"):
            print("❌ Error: BOT_TOKEN environment variable is not set!")
            print("Please create a .env file with your Discord bot token:")
            print("BOT_TOKEN=your_discord_bot_token_here")
            sys.exit(1)
        
        # Set up sys.argv for the bot module
        sys.argv = ["sacudo"]
        
        print("🎵 Starting Sacudo Discord Bot...")
        # Dashboard/API removed
        
        # Run the bot
        # Run the bot
        bot.run_bot()
            
    except KeyboardInterrupt:
        print("\n👋 Shutting down Sacudo...")
        cleanup_processes()
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        cleanup_processes()
        sys.exit(1)

if __name__ == "__main__":
    main() 