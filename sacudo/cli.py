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
  sacudo                      # Run bot only
  sacudo --with-api           # Run bot with API backend
  sacudo --with-dashboard     # Run bot, API, and React dashboard
  sacudo --help               # Show this help message
        """
    )
    
    parser.add_argument(
        "--with-api",
        action="store_true",
        help="Run the bot with web dashboard API (default: bot only)"
    )
    
    parser.add_argument(
        "--with-dashboard",
        action="store_true", 
        help="Run bot with API and React dashboard (requires Node.js)"
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0"
    )
    
    args = parser.parse_args()
    
    # Handle mutually exclusive options - prefer --with-dashboard over --with-api
    if args.with_dashboard:
        args.with_api = True  # Dashboard requires API
    
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
        
        # Check Node.js if dashboard is requested
        if args.with_dashboard and not check_node_installed():
            print("❌ Error: Node.js is not installed or not available in PATH!")
            print("Please install Node.js from https://nodejs.org/ to use the dashboard.")
            sys.exit(1)
        
        # Set up sys.argv for the bot module
        if args.with_api:
            sys.argv = ["sacudo", "--with-api"]
        else:
            sys.argv = ["sacudo"]
        
        print("🎵 Starting Sacudo Discord Bot...")
        if args.with_dashboard:
            print("🌐 API will be available at http://localhost:8000")
            print("🎨 React dashboard will be available at http://localhost:3000")
        elif args.with_api:
            print("🌐 Web dashboard API will be available at http://localhost:8000")
        
        # Run the bot
        if args.with_api:
            # Start dashboard if requested
            if args.with_dashboard:
                if not start_dashboard():
                    print("❌ Failed to start dashboard, continuing with bot and API only...")
                else:
                    # Give the dashboard a moment to start
                    time.sleep(2)
            
            # Start the bot in a separate thread
            bot_thread = threading.Thread(target=bot.run_bot)
            bot_thread.daemon = True
            bot_thread.start()
            
            # Create PID file
            bot.create_pid_file()
            try:
                # Start the Flask server
                bot.logger.info("Starting web server")
                bot.socketio.run(
                    bot.app, 
                    host='0.0.0.0', 
                    port=bot.API_PORT,
                    debug=True,
                    allow_unsafe_werkzeug=True, 
                    log_output=True,
                    use_reloader=False
                )
            finally:
                # Remove PID file on shutdown
                bot.remove_pid_file()
                # Cleanup dashboard process
                cleanup_processes()
        else:
            # Run in standalone bot mode
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