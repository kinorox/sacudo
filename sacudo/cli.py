#!/usr/bin/env python3
"""
CLI entry point for the Sacudo Discord bot.
"""

import argparse
import sys
import os

# Add the parent directory to the path so we can import bot.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    """Main entry point for the sacudo command."""
    parser = argparse.ArgumentParser(
        description="Sacudo - Discord YouTube Music Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sacudo                    # Run bot only
  sacudo --with-api         # Run bot with web dashboard
  sacudo --help             # Show this help message
        """
    )
    
    parser.add_argument(
        "--with-api",
        action="store_true",
        help="Run the bot with web dashboard API (default: bot only)"
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0"
    )
    
    args = parser.parse_args()
    
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
        if args.with_api:
            sys.argv = ["sacudo", "--with-api"]
        else:
            sys.argv = ["sacudo"]
        
        print("🎵 Starting Sacudo Discord Bot...")
        if args.with_api:
            print("🌐 Web dashboard will be available at http://localhost:8000")
        
        # Run the bot
        if args.with_api:
            # Import required modules for API mode
            import threading
            
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
        else:
            # Run in standalone bot mode
            bot.run_bot()
            
    except KeyboardInterrupt:
        print("\n👋 Shutting down Sacudo...")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 