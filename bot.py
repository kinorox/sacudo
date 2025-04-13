import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import youtube_dl
import asyncio
from yt_dlp import YoutubeDL
from collections import deque
import re
import logging
from logging.handlers import RotatingFileHandler
import datetime
import traceback
import atexit
import threading
import time
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room

# Set up logging with rotation
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_file = "bot.log"
log_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')  # 5MB per file, keep 5 backup files
log_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger = logging.getLogger('music_bot')
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logger.addHandler(console_handler)

# Load environment variables from .env file
load_dotenv()

# Retrieve the bot token from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Get API port from environment variables, default to 8000 if not set
API_PORT = int(os.getenv("API_PORT", 8000))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in the environment variables!")

# Create a PID file to indicate that the bot is running
def create_pid_file():
    """Create a PID file for the bot to allow detection by other processes"""
    pid = os.getpid()
    with open("bot.pid", "w") as f:
        f.write(str(pid))
    logger.info(f"Created PID file with PID: {pid}")

# Remove PID file on exit
def remove_pid_file():
    """Remove the PID file when the bot shuts down"""
    try:
        if os.path.exists("bot.pid"):
            os.remove("bot.pid")
            logger.info("Removed PID file")
    except Exception as e:
        logger.error(f"Error removing PID file: {e}")

# Create the PID file at startup
create_pid_file()

# Register cleanup handler
atexit.register(remove_pid_file)

# Global dictionaries for queues and currently playing song message
queues = {}
current_song = {}
current_song_message = {}  # Stores the last sent bot message per guild
song_cache = {}  # Cache for song information to avoid re-fetching
preloaded_songs = {}  # Store preloaded songs for each guild
playing_locks = {}  # Locks to prevent multiple songs from playing simultaneously

# Configure intents
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.voice_states = True
intents.message_content = True

# Define the bot
bot = commands.Bot(command_prefix='!', intents=intents)

youtube_dl.utils.bug_reports_message = lambda: ''


class YTDLError(Exception):
    pass


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.7):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('webpage_url')
        self.postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        self._start_time = None
        self._process = None  # Store the FFmpeg process

    @staticmethod
    def is_url(text):
        """Check if the provided text is a URL.
        
        This includes common YouTube and other music streaming service links.
        """
        # Standard URL patterns
        if text.startswith(('http://', 'https://')):
            return True
            
        # YouTube shortened URLs
        if text.startswith(('youtu.be/', 'youtube.com/', 'www.youtube.com/')):
            return True
            
        # Other common music services
        if any(domain in text for domain in ['spotify.com', 'soundcloud.com', 'bandcamp.com']):
            return True
            
        return False

    @classmethod
    async def from_url(cls, url_or_search, *, loop=None, stream=False, retry_count=0):
        loop = loop or asyncio.get_event_loop()
        
        # Check if it's a URL or search term
        if not cls.is_url(url_or_search):
            # It's a search term, convert to a YouTube search URL
            logger.info(f"Converting search term to YouTube search URL: {url_or_search}")
            search_term = url_or_search
            url = f"ytsearch:{search_term}"
        else:
            url = url_or_search
            
        logger.info(f"Creating source from URL: {url} (stream={stream})")
        
        # Check if we have the song in cache
        if url in song_cache:
            logger.info(f"Found song in cache: {url}")
            data = song_cache[url]
            # If URL is direct, we can use it immediately
            if 'url' in data:
                logger.info(f"Using cached URL for {url}")
                filename = data['url']
                ffmpeg_options = {
                    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                    'options': '-vn'
                }
                # Create the audio source
                audio_source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
                source = cls(audio_source, data=data)
                source.volume = 0.8
                return source
            # For non-direct URLs, we'll need to extract the info again
            # but we can use the cache for displaying metadata
            logger.info(f"Cached URL is not direct. Re-extracting for {url}")
        
        is_render = is_running_on_render()
        logger.info(f"Running on Render: {is_render}")
        
        # Base options for both search and direct URL
        base_options = {
            'format': 'bestaudio/best',
            'nocheckcertificate': True,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'http_chunk_size': 10485760,  # 10M chunks
            'retries': 5,
        }
            
        # Check for cookies file in app directory (copied from Render secrets by ensure_cookies_file)
        cookies_file = 'cookies.txt'
        cookies_exists = False
        cookies_path_to_use = cookies_file
        
        # Local cookies file should exist if ensure_cookies_file was called
        if os.path.exists(cookies_file):
            logger.info(f"Using local cookies file for YouTube: {cookies_file}")
            cookies_exists = True
            cookies_path_to_use = cookies_file
        else:
            logger.warning("No cookies file found! This may affect YouTube extraction.")
        
        # Add cookies if available
        if cookies_exists:
            with open(cookies_path_to_use, 'r') as f:
                content = f.read().strip()
                if content and content.startswith("# Netscape HTTP Cookie File"):
                    # Use the local file directly - it's already writable
                    base_options['cookiefile'] = cookies_path_to_use
                    # Add option to prevent trying to save cookies back to file
                    base_options['nooverwrites'] = True
                    logger.info(f"Using cookies file for authentication: {cookies_path_to_use}")
                else:
                    logger.warning(f"Cookies file exists but appears empty or is a template: {cookies_path_to_use}")
        
        # If this is a search query rather than direct URL
        if url.startswith('ytsearch:'):
            logger.info(f"Using search options")
            ydl_opts = {
                **base_options,
                'default_search': 'auto',
                'ignoreerrors': False,  # We want to catch errors for search queries
                'logtostderr': False,
                'source_address': '0.0.0.0',  # Bind to all interfaces
            }
        else:
            logger.info(f"Using direct URL options")
            ydl_opts = {
                **base_options,
                'postprocessors': [],        # No post-processing to avoid any delays
                'extract_flat': 'in_playlist',
                'ignoreerrors': True,
                'skip_download': True,       # Important: just streaming, not downloading
                'geo_bypass_country': 'US',
            }
        
        try:
            with YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Extracting info for URL: {url}")
                data = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=not stream))

            if data is None:
                logger.error(f"Failed to extract info for URL: {url}")
                raise YTDLError(f"Could not extract information from URL: {url}")

            # Handle both search results and playlists that have 'entries'
            if 'entries' in data and data['entries']:
                if url.startswith('ytsearch:'):
                    logger.info(f"Found {len(data['entries'])} search results for: {url}")
                    # Take the first search result
                    if len(data['entries']) > 0:
                        data = data['entries'][0]
                        logger.info(f"Selected first search result: {data.get('title', 'Unknown')}")
                    else:
                        logger.error(f"No search results found for: {url}")
                        raise YTDLError(f"No results found for search query")
                else:
                    logger.info(f"URL is a playlist, using first entry: {url}")
                    data = data['entries'][0]
                
                # Check if the entry is valid
                if not data:
                    logger.error(f"Empty entry in result for URL: {url}")
                    raise YTDLError(f"Empty entry for URL: {url}")
        except Exception as e:
            logger.error(f"Error extracting info for URL {url}: {str(e)}")
            logger.error(traceback.format_exc())
            
            # Check if this is a format error and we can retry with a different format
            error_msg = str(e).lower()
            if ("format" in error_msg or "requested format is not available" in error_msg) and retry_count < 2:
                logger.info(f"Format error detected for URL {url}, retrying with different format (retry: {retry_count+1})")
                # Try again with a different format
                return await cls.from_url(url, loop=loop, stream=stream, retry_count=retry_count+1)
            
            raise YTDLError(f"Error extracting info: {str(e)}")

        filename = data['url'] if stream else ydl.prepare_filename(data)
        
        # Get the streaming URL from the data
        if 'url' in data:
            filename = data['url']  # Direct URL to the audio stream
            logger.info(f"Using direct URL from data for streaming")
        else:
            filename = data.get('webpage_url', url)  # Fallback to webpage URL or original URL
            logger.warning(f"No direct URL found, using webpage URL: {filename}")
        
        # Update the player URL if it wasn't set
        if not data.get('webpage_url') and url.startswith('ytsearch:'):
            data['webpage_url'] = data.get('url', filename)
            logger.info(f"Setting webpage_url for search result: {data.get('webpage_url')}")
        
        # Cache the data for future use
        song_cache[url] = data
        logger.info(f"Cached data for URL: {url}")
        
        # Log important data for debugging
        logger.info(f"Title: {data.get('title')}, URL: {data.get('webpage_url')}, Stream URL: {filename}")
        
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        
        # Create the audio source
        audio_source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
        
        # Add a small delay to ensure the source is properly initialized
        await asyncio.sleep(0.5)
        
        logger.info(f"Created YTDLSource for URL: {url}, title: {data.get('title')}")
        # Ensure volume is set at a good audible level
        source = cls(audio_source, data=data)
        source.volume = 0.8  # Set a slightly higher volume to ensure audibility
        return source
        
    def cleanup(self):
        """Clean up resources when the source is done."""
        if hasattr(self, '_process') and self._process:
            try:
                logger.info(f"Cleaning up FFmpeg process for {self.title}")
                self._process.terminate()
                self._process = None
            except Exception as e:
                logger.error(f"Error cleaning up FFmpeg process: {e}")
                logger.error(traceback.format_exc())
                
    def __del__(self):
        """Ensure cleanup happens when the object is garbage collected."""
        self.cleanup()


# 🎵 Button Controls View 🎵
class MusicControls(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)  # Prevents buttons from auto-disabling after 15 minutes
        self.ctx = ctx

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.blurple)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        logger.info(f"Skip button pressed in guild {self.ctx.guild.id}")
        
        result = await handle_skip_request(self.ctx)
        if result.startswith("Error:"):
            await interaction.followup.send(f"❌ {result[7:]}", ephemeral=True)
        else:
            await interaction.followup.send(result, ephemeral=True)

    @discord.ui.button(label="⏸ Pause", style=discord.ButtonStyle.gray)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        logger.info(f"Pause button pressed in guild {self.ctx.guild.id}")
        
        result = await handle_pause_request(self.ctx)
        if result.startswith("Error:"):
            await interaction.followup.send(f"❌ {result[7:]}", ephemeral=True)
        else:
            await interaction.followup.send(result, ephemeral=True)

    @discord.ui.button(label="▶ Resume", style=discord.ButtonStyle.green)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        logger.info(f"Resume button pressed in guild {self.ctx.guild.id}")
        
        result = await handle_resume_request(self.ctx)
        if result.startswith("Error:"):
            await interaction.followup.send(f"❌ {result[7:]}", ephemeral=True)
        else:
            await interaction.followup.send(result, ephemeral=True)

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.red)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stops playback, clears the queue, and deletes the message."""
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        logger.info(f"Stop button pressed in guild {self.ctx.guild.id}")
        
        result = await handle_stop_request(self.ctx)
        if result.startswith("Error:"):
            await interaction.followup.send(f"❌ {result[7:]}", ephemeral=True)
        else:
            await interaction.followup.send(result, ephemeral=True)


@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    # Store the bot startup time
    bot.uptime = time.time()
    ensure_cookies_file()
    logger.info("Checked cookies file")
    
    # Test YouTube connection with various methods
    asyncio.create_task(test_youtube_connection())

# Define this function before it's used in test_youtube_connection
def is_running_on_render():
    """Check if the bot is running on Render."""
    return os.environ.get('RENDER') == 'true' or os.path.exists('/opt/render')

def safe_youtube_options_for_render(options):
    """Remove Render-incompatible options from YouTube download options."""
    if not is_running_on_render():
        return options
        
    # Make a copy to avoid modifying the original
    safe_options = dict(options)
    
    # Remove browser cookie options that won't work on Render
    if 'cookiesfrombrowser' in safe_options:
        logger.info("Removing cookiesfrombrowser option for Render environment")
        del safe_options['cookiesfrombrowser']
        
    # Always ensure we have a user agent
    if 'user_agent' not in safe_options:
        safe_options['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        
    return safe_options

def copy_cookies_to_temp(cookies_path):
    """Copy cookies from read-only location to a writable temp file.
    
    This is needed for Render's read-only filesystem where yt-dlp needs to read the cookies.
    Returns the temporary file path that should be used instead.
    """
    if not is_running_on_render() or not os.path.exists(cookies_path):
        return cookies_path
    
    try:
        import tempfile
        
        # Create a temp file for cookies in a definitely writable location
        temp_dir = '/tmp' if os.path.exists('/tmp') else None
        cookies_temp = tempfile.NamedTemporaryFile(delete=False, suffix='.txt', dir=temp_dir)
        cookies_temp.close()
        
        # Copy from source to temp location
        with open(cookies_path, 'r') as source:
            with open(cookies_temp.name, 'w') as dest:
                dest.write(source.read())
        
        logger.info(f"Copied cookies from {cookies_path} to temp file {cookies_temp.name}")
        return cookies_temp.name
    except Exception as e:
        logger.error(f"Error copying cookies to temp file: {e}")
        logger.error(traceback.format_exc())
        return cookies_path

async def test_youtube_connection():
    """Test if YouTube extraction is working properly and uses the best available method."""
    logger.info("Testing YouTube connection...")
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # A popular video that's unlikely to be removed
    
    # Check if we're running on Render
    render_env = is_running_on_render()
    logger.info(f"Detected environment: {'Render' if render_env else 'Local'}")
    
    # Check for cookies file in app directory
    cookies_file = 'cookies.txt'
    cookies_exists = False
    
    # Local cookies file should exist if ensure_cookies_file was called
    if os.path.exists(cookies_file):
        logger.info(f"Using local cookies file for connection test: {cookies_file}")
        cookies_exists = True
    else:
        logger.warning("No cookies file found for connection test! This may affect YouTube extraction tests.")
    
    # Define methods to test based on environment
    if render_env:
        # On Render, don't use browser cookie methods which won't work
        methods = [
            {"method": "default", "options": {}},
            {"method": "with_cookies", "options": {"cookiefile": cookies_file}} if cookies_exists else {"method": "default_no_cookies", "options": {}},
            {"method": "with_useragent", "options": {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }},
            {"method": "with_both", "options": {
                "cookiefile": cookies_file,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }} if cookies_exists else {"method": "with_useragent_only", "options": {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }}
        ]
    else:
        # On local machine, try browser cookies too
        methods = [
            {"method": "default", "options": {}},
            {"method": "with_cookies", "options": {"cookiefile": cookies_file}} if cookies_exists else {"method": "default_no_cookies", "options": {}},
            {"method": "with_useragent", "options": {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }},
            {"method": "with_both", "options": {
                "cookiefile": cookies_file,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }} if cookies_exists else {"method": "with_useragent_only", "options": {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }},
            {"method": "with_cookies_browser", "options": {
                "cookiesfrombrowser": ('chrome',)
            }},
            {"method": "with_all", "options": {
                "cookiefile": cookies_file if cookies_exists else None,
                "cookiesfrombrowser": ('chrome',),
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "geo_bypass": True,
                "geo_bypass_country": "US"
            }}
        ]
    
    working_methods = []
    
    for method in methods:
        try:
            logger.info(f"Testing YouTube extraction with method: {method['method']}")
            ydl_opts = {
                "format": "bestaudio",
                "quiet": True,
                "extract_flat": True,
                "skip_download": True,
                **method["options"]
            }
            
            # Add nooverwrites option when on Render to prevent saving cookies
            if is_running_on_render() and 'cookiefile' in method['options']:
                ydl_opts['nooverwrites'] = True
                logger.info(f"Added nooverwrites option for method {method['method']} on Render")
            
            with YoutubeDL(ydl_opts) as ydl:
                info = await bot.loop.run_in_executor(None, lambda: ydl.extract_info(test_url, download=False))
                
                if info and 'title' in info:
                    logger.info(f"✅ Method {method['method']} works! Title: {info['title']}")
                    working_methods.append(method)
                else:
                    logger.warning(f"❌ Method {method['method']} didn't extract proper info")
        except Exception as e:
            logger.error(f"❌ Method {method['method']} failed with error: {str(e)}")
    
    if working_methods:
        best_method = working_methods[0]
        logger.info(f"Best working method: {best_method['method']} with options: {best_method['options']}")
        # Store the best method options globally
        global best_youtube_options
        best_youtube_options = best_method["options"]
        logger.info("Successfully tested YouTube extraction methods")
    else:
        logger.error("No working YouTube extraction methods found. Music playback might not work.")
        # Use a simple fallback that should work in most cases
        best_youtube_options = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }
        
        # Add cookies if available
        if cookies_exists:
            best_youtube_options["cookiefile"] = cookies_file
            # Add option to prevent saving cookies
            best_youtube_options["nooverwrites"] = True
            logger.info(f"Added cookies to fallback method: {cookies_file}")
            
        logger.info(f"Using fallback method with options: {best_youtube_options}")

# Add this above the YouTubeDL class
best_youtube_options = {}

@bot.command()
async def join(ctx):
    if not ctx.message.author.voice:
        logger.warning(f"Join command used by {ctx.author} but not in a voice channel")
        await ctx.send("You are not connected to a voice channel.")
        return
    else:
        channel = ctx.message.author.voice.channel
    logger.info(f"Joining voice channel {channel.name} in guild {ctx.guild.id}")
    await channel.connect()


async def fix_queue(guild_id):
    """Fixes the queue by removing duplicates and ensuring proper order."""
    logger.info(f"Fixing queue for guild {guild_id}")
    
    if guild_id not in queues:
        logger.info(f"Creating new queue for guild {guild_id}")
        queues[guild_id] = deque()
        return 0
    
    # Log the original queue
    original_queue = list(queues[guild_id])
    logger.info(f"Original queue for guild {guild_id}: {original_queue}")
    
    # Get the current song URL if there is one
    current_song_url = None
    if guild_id in current_song and current_song[guild_id]:
        current_song_url = current_song[guild_id].url
        logger.info(f"Current song URL for queue cleaning: {current_song_url}")
    
    # Create a new queue with only unique URLs
    new_queue = deque()
    unique_urls = set()
    
    # Add only unique URLs to the new queue, excluding the currently playing song
    for url in queues[guild_id]:
        # Skip URLs that match the currently playing song
        if current_song_url and url == current_song_url:
            logger.warning(f"Found currently playing song in queue, removing it: {url}")
            continue
            
        if url not in unique_urls:
            unique_urls.add(url)
            new_queue.append(url)
    
    # Replace the old queue with the new one
    queues[guild_id] = new_queue
    
    # Log the new queue
    new_queue_list = list(new_queue)
    logger.info(f"New queue for guild {guild_id}: {new_queue_list}")
    
    # Log removed duplicates
    removed_count = len(original_queue) - len(new_queue_list)
    if removed_count > 0:
        logger.info(f"Removed {removed_count} duplicate songs from queue in guild {guild_id}")
    
    return len(queues[guild_id])


async def handle_play_request(ctx, search: str):
    """Core functionality for playing a song, used by both bot commands and API"""
    guild_id = ctx.guild.id
    logger.info(f"Play functionality called for guild {guild_id} with search: {search}")
    logger.info(f"Current current_song keys before play: {list(current_song.keys())}")
    
    if guild_id in current_song:
        logger.info(f"handle_play_request: Guild {guild_id} exists in current_song dictionary before play")
        if current_song[guild_id]:
            logger.info(f"handle_play_request: Current song before play for guild {guild_id}: {current_song[guild_id].title if hasattr(current_song[guild_id], 'title') else 'Unknown'}")
        else:
            logger.info(f"handle_play_request: Current song is None for guild {guild_id} before play")
    else:
        logger.info(f"handle_play_request: Guild {guild_id} not in current_song dictionary before play")
    
    if not ctx.voice_client:
        logger.info(f"Bot not in voice channel, joining for guild {guild_id}")
        await ctx.invoke(join)

    if 'list=' in search:
        logger.info(f"Detected playlist URL: {search}")
        return await handle_playlist(ctx, search)
    else:
        # Fix the queue before adding a new song
        logger.info(f"Fixing queue before adding new song in guild {guild_id}")
        await fix_queue(ctx.guild.id)
        
        if ctx.voice_client.is_playing():
            logger.info(f"Bot already playing, adding to queue: {search}")
            # Initialize queue if it doesn't exist
            if ctx.guild.id not in queues:
                queues[ctx.guild.id] = deque()
                logger.info(f"Created new queue for guild {ctx.guild.id}")
            
            # Check if it's a search query that's not a URL
            if not YTDLSource.is_url(search):
                # First, try to extract info without downloading to get the title
                logger.info(f"Extracting info for search query: {search}")
                try:
                    # This will be a background task so we don't block the main thread
                    # Create a task to add song to cache for better title display later
                    asyncio.create_task(extract_song_info_for_queue(search, ctx.guild.id))
                except Exception as e:
                    logger.error(f"Error extracting info for search: {search} - {str(e)}")
            
            # Add to queue
            queues[ctx.guild.id].append(search)
            
            # Emit queue update for dashboard
            emit_to_guild(ctx.guild.id, 'queue_update', {
                'guild_id': str(ctx.guild.id),
                'queue': queue_to_list(str(ctx.guild.id)),
                'action': 'add'
            })
            
            # Return message based on whether it's a URL or search term
            if YTDLSource.is_url(search):
                return f"🎵 Added to queue: {search}"
            else:
                return f"🎵 Added to queue: '{search}' (will search YouTube)"
        else:
            try:
                logger.info(f"Creating player for: {search}")
                
                # Show searching message if it's a search query
                if not YTDLSource.is_url(search):
                    await ctx.send(f"🔍 Searching YouTube for: '{search}'...")
                    
                player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
                
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                logger.info(f"Playing: {player.title}")
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                
                # Set the current song and log it
                current_song[ctx.guild.id] = player
                logger.info(f"Set current_song[{ctx.guild.id}] = {player.title}")
                logger.info(f"Current current_song keys after setting: {list(current_song.keys())}")
                
                # Now check if it was set correctly
                if ctx.guild.id in current_song:
                    if current_song[ctx.guild.id]:
                        logger.info(f"Verification: current_song[{ctx.guild.id}] successfully set to {current_song[ctx.guild.id].title if hasattr(current_song[ctx.guild.id], 'title') else 'Unknown'}")
                    else:
                        logger.warning(f"Verification failed: current_song[{ctx.guild.id}] is None right after setting it!")
                else:
                    logger.warning(f"Verification failed: guild {ctx.guild.id} not in current_song dictionary right after setting it!")

                # Update music message
                await update_music_message(ctx, player)
                
                # Emit song update for dashboard
                emit_to_guild(ctx.guild.id, 'song_update', {
                    'guild_id': str(ctx.guild.id),
                    'current_song': song_to_dict(player),
                    'action': 'play'
                })

                # Return different messages based on search type
                if not YTDLSource.is_url(search):
                    return f"🎵 Found and playing: **{player.title}**"
                else:
                    return f"🎵 Now playing: **{player.title}**"
                
            except YTDLError as e:
                logger.error(f"YTDL error for search: {search} - {str(e)}")
                # Extract the error message for a more user-friendly response
                error_msg = str(e)
                if "format" in error_msg.lower():
                    await ctx.send(f"❌ Error: The requested video format is unavailable. YouTube may have changed something. Trying to play the next song in the playlist...")
                    # Try to play the next song
                    asyncio.create_task(play_next(ctx))
                    return "Error: The requested video format is unavailable."
                elif "copyright" in error_msg.lower() or "removed" in error_msg.lower():
                    await ctx.send(f"❌ Error: The first video in the playlist may have been removed due to copyright issues. Trying to play the next song...")
                    # Try to play the next song
                    asyncio.create_task(play_next(ctx))
                    return "Error: The video may have been removed due to copyright issues."
                else:
                    return f"Error: Could not play '{search}'. Please try a different song or URL."
            except Exception as e:
                logger.error(f"Error in play command: {e}")
                logger.error(traceback.format_exc())
                return f"Error: An unexpected error occurred: {str(e)}"

# Helper function to extract song info in the background
async def extract_song_info_for_queue(search, guild_id):
    """Extract song info for a search query to be added to the queue"""
    logger.info(f"Extracting song info for search query in queue: {search}")
    try:
        # Check for cookies file in app directory 
        cookies_file = 'cookies.txt'
        cookies_exists = False
        
        # Local cookies file should exist if ensure_cookies_file was called
        if os.path.exists(cookies_file):
            logger.info(f"Using local cookies file for queue extraction: {cookies_file}")
            cookies_exists = True
        else:
            logger.warning("No cookies file found for queue extraction!")
        
        # Create a minimal YDL options set for just getting info
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'noplaylist': True,
            'skip_download': True,
            'nocheckcertificate': True
        }
        
        # Add cookies if available
        if cookies_exists:
            with open(cookies_file, 'r') as f:
                content = f.read().strip()
                if content and content.startswith("# Netscape HTTP Cookie File"):
                    ydl_opts['cookiefile'] = cookies_file
                    # Add option to prevent trying to save cookies back to file
                    ydl_opts['nooverwrites'] = True
                    logger.info(f"Using cookies file for queue extraction: {cookies_file}")
                else:
                    logger.warning(f"Cookies file exists but appears empty or is a template: {cookies_file}")
        
        # Add the best options we've determined through testing
        if best_youtube_options:
            # Use the helper function to get Render-safe options
            safe_options = safe_youtube_options_for_render(best_youtube_options)
            ydl_opts.update(safe_options)
        
        with YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Extracting info for queue: {search}")
            data = await bot.loop.run_in_executor(None, lambda: ydl.extract_info(search, download=False))
            
            if data is None:
                logger.error(f"Failed to extract info for queue: {search}")
                return
                
            # Handle search results
            if 'entries' in data:
                if len(data['entries']) > 0:
                    data = data['entries'][0]
                else:
                    logger.error(f"No search results found for queue: {search}")
                    return
            
            # Store in song cache
            if data.get('webpage_url'):
                song_cache[search] = data
                # If the search is also in the queue, update it
                if guild_id in queues:
                    for i, url in enumerate(queues[guild_id]):
                        if url == search:
                            logger.info(f"Found search term in queue, updating to actual URL: {search} -> {data.get('webpage_url')}")
                            queues[guild_id][i] = data.get('webpage_url')
                            # Also update the song cache with the URL
                            song_cache[data.get('webpage_url')] = data
                            break
                
                # Emit queue update with updated info
                emit_to_guild(str(guild_id), 'queue_update', {
                    'guild_id': str(guild_id),
                    'queue': queue_to_list(str(guild_id)),
                    'action': 'update'
                })
                            
                logger.info(f"Successfully extracted info for queue: {search} -> {data.get('title')}")
            else:
                logger.warning(f"No webpage URL found for queue item: {search}")
    except Exception as e:
        logger.error(f"Error extracting info for queue: {search} - {str(e)}")
        logger.error(traceback.format_exc())


@bot.command()
async def play(ctx, *, search: str):
    logger.info(f"Play command used by {ctx.author} in guild {ctx.guild.id} with search: {search}")
    
    async with ctx.typing():
        result = await handle_play_request(ctx, search)
        if result and not result.startswith("Error:"):
            await ctx.send(result)
        elif result:
            await ctx.send(f"❌ {result}")


async def update_music_message(ctx, player):
    """Updates the bot message to keep only one active message."""
    guild_id = ctx.guild.id
    logger.info(f"Updating music message for guild {guild_id} with song: {player.title}")

    if guild_id in current_song_message and current_song_message[guild_id]:
        try:
            logger.info(f"Deleting old music message in guild {guild_id}")
            await current_song_message[guild_id].delete()
        except discord.NotFound:
            logger.warning(f"Old music message not found in guild {guild_id}")

    # Safely extract video ID for thumbnail
    thumbnail_url = "https://i.imgur.com/ufxvZ0j.png"  # Default music thumbnail
    if player.url:
        try:
            if "v=" in player.url:
                video_id = player.url.split("v=")[-1]
                # Remove any additional parameters
                if "&" in video_id:
                    video_id = video_id.split("&")[0]
                thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            elif "youtu.be/" in player.url:
                video_id = player.url.split("youtu.be/")[-1]
                # Remove any additional parameters
                if "?" in video_id:
                    video_id = video_id.split("?")[0]
                thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        except Exception as e:
            logger.warning(f"Could not extract video ID from URL: {player.url}. Error: {e}")
            # Use default thumbnail

    # Create proper description based on whether we have a URL
    if player.url:
        embed_description = f"**[{player.title}]({player.url})**"
    else:
        embed_description = f"**{player.title}**"
        
    embed = discord.Embed(title="🎵 Now Playing", description=embed_description, color=discord.Color.blue())
    embed.set_thumbnail(url=thumbnail_url)
    embed.add_field(name="Queue Length", value=str(len(queues.get(ctx.guild.id, []))), inline=False)
    view = MusicControls(ctx)

    msg = await ctx.send(embed=embed, view=view)
    current_song_message[guild_id] = msg
    logger.info(f"Created new music message in guild {guild_id}")


async def play_next(ctx):
    """Plays the next song in the queue or updates the message if queue is empty."""
    guild_id = ctx.guild.id
    guild_id_str = str(guild_id)
    logger.info(f"play_next called for guild {guild_id}")
    logger.info(f"Current global current_song dictionary keys: {list(current_song.keys())}")
    
    # Check if current_song for this guild exists
    if guild_id_str in current_song:
        logger.info(f"play_next: Found guild {guild_id_str} in current_song dictionary")
        if current_song[guild_id_str]:
            logger.info(f"play_next: Current song for guild {guild_id_str} is {current_song[guild_id_str].title if hasattr(current_song[guild_id_str], 'title') else 'Unknown'}")
        else:
            logger.info(f"play_next: Current song for guild {guild_id_str} is None")
    elif guild_id in current_song:
        logger.info(f"play_next: Found guild {guild_id} in current_song dictionary (integer key)")
        # Copy current song to string key for consistency
        current_song[guild_id_str] = current_song[guild_id]
        # Remove integer key to avoid confusion
        del current_song[guild_id]
        logger.info(f"play_next: Converted guild ID from integer to string in current_song dictionary")
    else:
        logger.info(f"play_next: Guild {guild_id_str} not found in current_song dictionary")
        # Initialize the current_song entry for this guild
        current_song[guild_id_str] = None
    
    # Check if we're already playing a song (lock mechanism)
    if guild_id in playing_locks and playing_locks[guild_id]:
        logger.warning(f"Already playing a song in guild {guild_id_str}, skipping play_next call")
        # Instead of recursively calling play_next, just return
        return
    
    # Set the lock
    playing_locks[guild_id] = True
    logger.info(f"Set playing lock for guild {guild_id_str}")
    
    try:
        # Fix the queue to remove any duplicates
        logger.info(f"Fixing queue in play_next for guild {guild_id_str}")
        await fix_queue(guild_id)
        
        # Store the current song's URL for duplicate check
        current_url = None
        if guild_id_str in current_song and current_song[guild_id_str]:
            current_url = current_song[guild_id_str].url
            logger.info(f"Current song URL for duplicate check: {current_url}")
            
        # Clear the current song immediately - we'll set it again if we successfully play a new song
        current_song[guild_id_str] = None
        
        # Check if we have a preloaded song
        if guild_id in preloaded_songs and preloaded_songs[guild_id]:
            player = preloaded_songs[guild_id]
            preloaded_songs[guild_id] = None
            
            # Check if this preloaded song is the same as the current song
            if current_url and player.url == current_url:
                logger.warning(f"Preloaded song is the same as current song, skipping it for guild {guild_id_str}")
                player.cleanup()
                # Try the next song in the queue instead
                if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
                    logger.info(f"Moving to the next song in the queue for guild {guild_id_str}")
                    # Don't use the preloaded song and fall through to the next section
                else:
                    logger.info(f"No more songs in queue after skipping duplicate for guild {guild_id_str}")
                    if guild_id in current_song_message and current_song_message[guild_id_str]:
                        try:
                            embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                            await current_song_message[guild_id_str].edit(embed=embed, view=None)
                        except discord.NotFound:
                            pass
                            
                    # Emit socket events for queue end
                    emit_to_guild(guild_id, 'song_update', {
                        'guild_id': str(guild_id),
                        'current_song': None,
                        'action': 'queue_end'
                    })
                    return
            else:
                logger.info(f"Using preloaded song in guild {guild_id_str}: {player.title}")
                
                if not ctx.voice_client:
                    logger.info(f"Bot not in voice channel, joining for guild {guild_id_str}")
                    await ctx.invoke(join)
                try:
                    # Add a small delay to ensure buffer is filled
                    await asyncio.sleep(0.5)
                    
                    # Make sure we're not already playing something
                    if ctx.voice_client.is_playing():
                        logger.warning(f"Voice client is still playing in guild {guild_id_str}, stopping")
                        ctx.voice_client.stop()
                        await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                    
                    logger.info(f"Playing preloaded song in guild {guild_id_str}: {player.title}")
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                    current_song[guild_id_str] = player
                    logger.info(f"Set current_song[{guild_id_str}] to {player.title} (preloaded)")
                    
                    await update_music_message(ctx, player)
                    
                    # Emit socket events for new song
                    emit_to_guild(guild_id, 'song_update', {
                        'guild_id': str(guild_id),
                        'current_song': song_to_dict(player),
                        'action': 'play'
                    })
                    emit_to_guild(guild_id, 'queue_update', {
                        'guild_id': str(guild_id),
                        'queue': queue_to_list(guild_id),
                        'action': 'update'
                    })
                    
                except Exception as e:
                    logger.error(f"Error playing preloaded song in guild {guild_id_str}: {e}")
                    logger.error(traceback.format_exc())
                    
                    # Ensure the current song is null in case of error
                    current_song[guild_id_str] = None
                    
                    # Notify clients about the error
                    emit_to_guild(guild_id, 'song_update', {
                        'guild_id': str(guild_id),
                        'current_song': None,
                        'action': 'error'
                    })
                    
                    # If there's an error, try the next song
                    asyncio.create_task(play_next(ctx))
                
                # Start preloading the next song
                logger.info(f"Starting preload for next song in guild {guild_id_str}")
                asyncio.create_task(preload_next_song(ctx))
                return
        
        # Check if there are songs in the queue
        if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
            try:
                # Get the next URL from the queue
                next_url = queues[guild_id].popleft()
                logger.info(f"Next song in queue for guild {guild_id_str}: {next_url}")
                
                # Check if this is the same as the current song
                if current_url and next_url == current_url:
                    logger.warning(f"Next song in queue is the same as current song, skipping it for guild {guild_id_str}")
                    # Try the next song
                    return asyncio.create_task(play_next(ctx))
                
                # Create the player for the next song
                logger.info(f"Creating player for next song in guild {guild_id_str}")
                player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                
                # Make sure we're connected to a voice channel
                if not ctx.voice_client:
                    logger.info(f"Bot not in voice channel, joining for guild {guild_id_str}")
                    await ctx.invoke(join)
                
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                # Make sure we're not already playing something
                if ctx.voice_client.is_playing():
                    logger.warning(f"Voice client is still playing in guild {guild_id_str}, stopping")
                    ctx.voice_client.stop()
                    await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                
                # Play the next song
                logger.info(f"Playing next song in guild {guild_id_str}: {player.title}")
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                current_song[guild_id_str] = player
                logger.info(f"Set current_song[{guild_id_str}] to {player.title} (from queue)")
                
                # Update the now playing message
                await update_music_message(ctx, player)
                
                # Emit socket events for new song
                emit_to_guild(guild_id, 'song_update', {
                    'guild_id': str(guild_id),
                    'current_song': song_to_dict(player),
                    'action': 'play'
                })
                emit_to_guild(guild_id, 'queue_update', {
                    'guild_id': str(guild_id),
                    'queue': queue_to_list(guild_id),
                    'action': 'update'
                })
                
                # Start preloading the next song
                logger.info(f"Starting preload for next song in guild {guild_id_str}")
                asyncio.create_task(preload_next_song(ctx))
                return
            except YTDLError as e:
                logger.error(f"YTDL error for song: {next_url}")
                logger.error(f"YTDL error details: {str(e)}")
                
                # Ensure the current song is null on error
                current_song[guild_id_str] = None
                
                # Remove this URL from the queue if it's still there
                if guild_id in queues and next_url in queues[guild_id]:
                    logger.info(f"Removing problematic URL {next_url} from queue")
                    try:
                        queues[guild_id].remove(next_url)
                    except ValueError:
                        pass
                
                # Check if there are more songs in the queue
                if guild_id in queues and queues[guild_id]:
                    logger.info(f"There are {len(queues[guild_id])} more songs in the queue, trying next one")
                    # Extract the error message for a more user-friendly response
                    error_msg = str(e)
                    if "format is not available" in error_msg.lower() or "format" in error_msg.lower():
                        await ctx.send(f"❌ Error: YouTube format unavailable for '{next_url}'. This can happen due to YouTube limitations. Trying the next song...")
                        # Try to play the next song
                        asyncio.create_task(play_next(ctx))
                    elif "copyright" in error_msg.lower() or "removed" in error_msg.lower():
                        await ctx.send(f"❌ Error: The video may have been removed due to copyright issues. Trying the next song...")
                        # Try to play the next song
                        asyncio.create_task(play_next(ctx))
                    else:
                        await ctx.send(f"❌ Error: Could not play a song. Trying the next one...")
                
                # Notify clients about the error
                emit_to_guild(guild_id, 'song_update', {
                    'guild_id': str(guild_id),
                    'current_song': None,
                    'action': 'error'
                })
                
                # If there's an error with this song, try the next one
                asyncio.create_task(play_next(ctx))
            except Exception as e:
                logger.error(f"Unexpected error playing song in guild {guild_id_str}: {e}")
                logger.error(traceback.format_exc())
                
                # Ensure the current song is null on any error
                current_song[guild_id_str] = None
                
                # Notify clients about the error
                emit_to_guild(guild_id, 'song_update', {
                    'guild_id': str(guild_id),
                    'current_song': None,
                    'action': 'error'
                })
                
                # If there's an error, try the next song
                asyncio.create_task(play_next(ctx))
        
        # No more songs in queue - only show the message if we were actually playing something
        # and the queue is truly empty
        else:
            logger.info(f"No more songs in queue for guild {guild_id_str}")
            if guild_id in current_song_message and current_song_message[guild_id_str]:
                try:
                    embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                    await current_song_message[guild_id_str].edit(embed=embed, view=None)
                except discord.NotFound:
                    pass
            
            # Clear the current song
            current_song[guild_id_str] = None
            logger.info(f"Cleared current_song[{guild_id_str}]")
            
            # Emit socket events for queue end
            emit_to_guild(guild_id, 'song_update', {
                'guild_id': str(guild_id),
                'current_song': None,
                'action': 'queue_end'
            })
    finally:
        # Release the lock
        playing_locks[guild_id] = False
        logger.info(f"Released playing lock for guild {guild_id_str}")
        
        # Log final state of current_song
        if guild_id in current_song:
            if current_song[guild_id_str]:
                logger.info(f"Final state: current_song[{guild_id_str}] = {current_song[guild_id_str].title if hasattr(current_song[guild_id_str], 'title') else 'Unknown'}")
            else:
                logger.info(f"Final state: current_song[{guild_id_str}] = None")
        else:
            logger.info(f"Final state: guild {guild_id_str} not in current_song dictionary")


async def preload_next_song(ctx):
    """Preloads the next song in the queue to reduce latency when switching songs."""
    guild_id = ctx.guild.id
    guild_id_str = str(guild_id)
    logger.info(f"Preloading next song for guild {guild_id_str}")
    
    # Skip preloading if there's already a preloaded song
    if guild_id in preloaded_songs and preloaded_songs[guild_id]:
        logger.info(f"Already have a preloaded song for guild {guild_id_str}, skipping preload")
        return
    
    # Check if there are songs in the queue
    if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
        # Get the next URL without removing it from the queue
        next_url = queues[guild_id][0]
        
        # Check if this is the currently playing song
        current_song_obj = None
        if guild_id_str in current_song:
            current_song_obj = current_song[guild_id_str]
        elif guild_id in current_song:
            current_song_obj = current_song[guild_id]
            # Move to string key for consistency
            current_song[guild_id_str] = current_song[guild_id]
            del current_song[guild_id]
            
        if current_song_obj and current_song_obj.url == next_url:
            logger.warning(f"Next song in queue is the currently playing song, skipping preload for guild {guild_id_str}")
            # Remove the duplicate from the queue
            queues[guild_id].popleft()
            # Try preloading the next song if there is one
            if queues[guild_id] and len(queues[guild_id]) > 0:
                next_url = queues[guild_id][0]
            else:
                logger.info(f"No more songs in queue after removing duplicate for guild {guild_id_str}")
                return
        
        logger.info(f"Preloading song: {next_url} for guild {guild_id_str}")
        try:
            # Preload the song
            player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
            
            # Double check that this isn't the currently playing song
            if current_song_obj and current_song_obj.title == player.title:
                logger.warning(f"Preloaded song is the same as current song, discarding preloaded song for guild {guild_id_str}")
                player.cleanup()
                return
                
            preloaded_songs[guild_id] = player
            logger.info(f"Preloaded song: {player.title} for guild {guild_id_str}")
        except YTDLError:
            # If preloading fails, just continue
            logger.error(f"Failed to preload song: {next_url} for guild {guild_id_str}")
            pass
        except Exception as e:
            logger.error(f"Error preloading song in guild {guild_id_str}: {e}")
            logger.error(traceback.format_exc())
            pass


async def handle_playlist(ctx, url):
    """Handles the playlist and queues each song."""
    logger.info(f"Handling playlist URL: {url}")
    
    # YouTube playlist options
    ydl_opts = {
        'format': 'bestaudio/best',
        'extract_flat': 'in_playlist',
        'quiet': True,
        'ignoreerrors': True,
        'retries': 5,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'extractor_args': {'youtube': {'skip': ['dash', 'hls']}},
    }
    
    # Check for cookies file in app directory
    cookies_file = 'cookies.txt'
    cookies_exists = False
    
    # Local cookies file should exist if ensure_cookies_file was called
    if os.path.exists(cookies_file):
        logger.info(f"Using local cookies file for playlist: {cookies_file}")
        cookies_exists = True
    else:
        logger.warning("No cookies file found for playlist extraction!")
    
    # Add cookies if available
    if cookies_exists:
        with open(cookies_file, 'r') as f:
            content = f.read().strip()
            if content and content.startswith("# Netscape HTTP Cookie File"):
                ydl_opts['cookiefile'] = cookies_file
                # Add option to prevent trying to save cookies back to file
                ydl_opts['nooverwrites'] = True
                logger.info(f"Using cookies file for playlist authentication: {cookies_file}")
            else:
                logger.warning(f"Cookies file exists but appears empty or is a template: {cookies_file}")
    
    # Use best options we've found through testing
    if best_youtube_options:
        # Apply only the options that are safe for Render
        safe_options = safe_youtube_options_for_render(best_youtube_options)
        ydl_opts.update(safe_options)
    
    with YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        entries = info_dict['entries']
        if not entries:
            logger.warning(f"No entries found in playlist: {url}")
            return

        if ctx.guild.id not in queues:
            queues[ctx.guild.id] = deque()
            logger.info(f"Created new queue for guild {ctx.guild.id}")
        
        # Create a set to track unique URLs to prevent duplicates
        unique_urls = set()
        
        # First, add all unique URLs to the queue
        for entry in entries:
            if 'url' in entry and entry['url'] not in unique_urls:
                unique_urls.add(entry['url'])
                queues[ctx.guild.id].append(entry['url'])
        
        logger.info(f"Added {len(unique_urls)} unique songs from playlist to queue for guild {ctx.guild.id}")
        
        # Fix the queue to ensure no duplicates
        logger.info(f"Fixing queue after adding playlist for guild {ctx.guild.id}")
        await fix_queue(ctx.guild.id)
        
        # Emit queue update for dashboard
        emit_to_guild(ctx.guild.id, 'queue_update', {
            'guild_id': str(ctx.guild.id),
            'queue': queue_to_list(str(ctx.guild.id)),
            'action': 'add_playlist'
        })
        
        # If the bot is not already playing, start playing the first song
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            if queues[ctx.guild.id]:
                first_url = queues[ctx.guild.id].popleft()
                logger.info(f"Playing first song from playlist: {first_url} for guild {ctx.guild.id}")
                try:
                    player = await YTDLSource.from_url(first_url, loop=bot.loop, stream=True)
                    
                    # Add a small delay to ensure buffer is filled
                    await asyncio.sleep(0.5)
                    
                    logger.info(f"Playing first song from playlist: {player.title} for guild {ctx.guild.id}")
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                    current_song[ctx.guild.id] = player
                    
                    await update_music_message(ctx, player)
                    await ctx.send(f"🎵 Playing playlist. Added {len(queues[ctx.guild.id])} songs to the queue.")
                    
                    # Emit song update for dashboard
                    emit_to_guild(ctx.guild.id, 'song_update', {
                        'guild_id': str(ctx.guild.id),
                        'current_song': song_to_dict(player),
                        'action': 'play'
                    })
                    
                except Exception as e:
                    logger.error(f"Error playing first song from playlist in guild {ctx.guild.id}: {e}")
                    logger.error(traceback.format_exc())
                    await ctx.send(f"❌ Error playing the first song from the playlist: {str(e)}")
            else:
                logger.warning(f"No valid songs found in playlist for guild {ctx.guild.id}")
                await ctx.send("❌ No valid songs found in the playlist.")
        else:
            # If already playing, just add to queue
            logger.info(f"Bot already playing, added {len(queues[ctx.guild.id])} songs from playlist to queue for guild {ctx.guild.id}")
            await ctx.send(f"🎵 Added {len(queues[ctx.guild.id])} songs from the playlist to the queue.")

@bot.command()
async def leave(ctx):
    logger.info(f"Leave command used by {ctx.author} in guild {ctx.guild.id}")
    await ctx.voice_client.disconnect()

@bot.command()
async def clearcache(ctx):
    """Clears the song cache to free up memory."""
    logger.info(f"Clearcache command used by {ctx.author} in guild {ctx.guild.id}")
    global song_cache
    cache_size = len(song_cache)
    song_cache = {}
    await ctx.send(f"✅ Song cache cleared. Freed up memory from {cache_size} cached songs.")

@bot.command()
async def skip(ctx):
    """Skips the current song and plays the next one in the queue."""
    logger.info(f"Skip command used by {ctx.author} in guild {ctx.guild.id}")
    
    result = await handle_skip_request(ctx)
    if result.startswith("Error:"):
        await ctx.send(f"❌ {result[7:]}")
    else:
        await ctx.send(result)

@bot.command()
async def queue(ctx):
    """Shows the current queue of songs."""
    logger.info(f"Queue command used by {ctx.author} in guild {ctx.guild.id}")
    
    guild_id = ctx.guild.id
    
    if guild_id not in queues or not queues[guild_id]:
        logger.info(f"Queue is empty for guild {guild_id}")
        await ctx.send("📋 The queue is empty.")
        return
    
    # Create an embed to display the queue
    embed = discord.Embed(title="📋 Current Queue", color=discord.Color.blue())
    
    # Add the currently playing song if there is one
    if guild_id in current_song and current_song[guild_id]:
        embed.add_field(name="Now Playing", value=f"🎵 **{current_song[guild_id].title}**", inline=False)
    
    # Add the queued songs
    queue_list = ""
    for i, url in enumerate(queues[guild_id], 1):
        # Try to get the title from the URL
        try:
            # Use a simple regex to extract video ID
            video_id = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url)
            if video_id:
                video_id = video_id.group(1)
                queue_list += f"{i}. [Video](https://www.youtube.com/watch?v={video_id})\n"
            else:
                queue_list += f"{i}. {url}\n"
        except:
            queue_list += f"{i}. {url}\n"
    
    if queue_list:
        embed.add_field(name="Up Next", value=queue_list, inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
async def debug(ctx):
    """Shows debug information about the current state of the bot."""
    logger.info(f"Debug command used by {ctx.author} in guild {ctx.guild.id}")
    
    guild_id = ctx.guild.id
    
    # Create an embed for debug information
    embed = discord.Embed(title="🔍 Debug Information", color=discord.Color.blue())
    
    # Voice client status
    if ctx.voice_client:
        embed.add_field(name="Voice Client", value=f"Connected: {ctx.voice_client.is_connected()}\nPlaying: {ctx.voice_client.is_playing()}\nPaused: {ctx.voice_client.is_paused()}", inline=False)
    else:
        embed.add_field(name="Voice Client", value="Not connected", inline=False)
    
    # Queue information
    queue_length = len(queues.get(guild_id, [])) if guild_id in queues else 0
    embed.add_field(name="Queue", value=f"Length: {queue_length}", inline=False)
    
    # Current song information
    if guild_id in current_song and current_song[guild_id]:
        embed.add_field(name="Current Song", value=f"Title: {current_song[guild_id].title}\nURL: {current_song[guild_id].url}", inline=False)
    else:
        embed.add_field(name="Current Song", value="None", inline=False)
    
    # Preloaded song information
    if guild_id in preloaded_songs and preloaded_songs[guild_id]:
        embed.add_field(name="Preloaded Song", value=f"Title: {preloaded_songs[guild_id].title}\nURL: {preloaded_songs[guild_id].url}", inline=False)
    else:
        embed.add_field(name="Preloaded Song", value="None", inline=False)
    
    # Lock status
    embed.add_field(name="Lock Status", value=f"Playing Lock: {playing_locks.get(guild_id, False)}", inline=False)
    
    # Send the debug information
    await ctx.send(embed=embed)
    
    # If there were issues, try to play the next song
    if issues_found and ctx.voice_client and not ctx.voice_client.is_playing():
        logger.info(f"Attempting to play next song after diagnosis in guild {guild_id}")
        asyncio.create_task(play_next(ctx))

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates to clean up when the bot is disconnected."""
    # Check if the bot was disconnected
    if member.id == bot.user.id and before.channel and not after.channel:
        guild_id = before.channel.guild.id
        logger.info(f"Bot disconnected from voice channel in guild {guild_id}")
        
        # Clean up resources
        if guild_id in current_song and current_song[guild_id]:
            logger.info(f"Cleaning up current song in guild {guild_id}")
            current_song[guild_id].cleanup()
            current_song[guild_id] = None
            
        if guild_id in preloaded_songs and preloaded_songs[guild_id]:
            logger.info(f"Cleaning up preloaded song in guild {guild_id}")
            preloaded_songs[guild_id].cleanup()
            preloaded_songs[guild_id] = None
            
        # Reset the playing lock
        if guild_id in playing_locks:
            logger.info(f"Resetting playing lock in guild {guild_id}")
            playing_locks[guild_id] = False

@bot.command()
async def volume(ctx, volume: int):
    """Change the volume of the player (0-150)."""
    logger.info(f"Volume command used by {ctx.author} in guild {ctx.guild.id} with volume: {volume}")
    
    if not ctx.voice_client:
        return await ctx.send("❌ I'm not connected to a voice channel.")
        
    if not ctx.voice_client.is_playing():
        return await ctx.send("❌ Nothing is playing right now.")
    
    # Clamp volume between 0 and 150
    volume = max(0, min(150, volume))
    
    # Convert to a float value between 0 and 1.5
    guild_id = ctx.guild.id
    if guild_id in current_song and current_song[guild_id]:
        current_song[guild_id].volume = volume / 100
        await ctx.send(f"🔊 Volume set to {volume}%")
    else:
        await ctx.send("❌ Couldn't find the current song.")

def fix_cookies_file_format(cookies_path):
    """Add Netscape header to cookies file if it's missing to make it compatible with yt-dlp."""
    try:
        with open(cookies_path, 'r') as f:
            content = f.read()
        
        # Check if the file is missing the Netscape header
        if not content.startswith("# Netscape HTTP Cookie File"):
            logger.info(f"Adding Netscape header to cookies file: {cookies_path}")
            
            # Create new content with the header
            new_content = "# Netscape HTTP Cookie File\n# https://curl.se/docs/http-cookies.html\n# This file was generated by yt-dlp\n\n" + content
            
            # Write the fixed content back to the file
            with open(cookies_path, 'w') as f:
                f.write(new_content)
            
            logger.info(f"Fixed cookies file format at: {cookies_path}")
            return True
        else:
            # File already has the correct header
            return True
    except Exception as e:
        logger.error(f"Error fixing cookies file format: {e}")
        return False

# Create a function to ensure the cookies file exists
def ensure_cookies_file():
    """Ensure the cookies file exists to prevent errors."""
    # Standard path in the app root
    cookies_file = 'cookies.txt'
    # Render secrets path
    render_secrets_path = '/etc/secrets/cookies.txt'
    
    try:
        # Check if we're running on Render
        if is_running_on_render():
            logger.info("Running on Render - checking for cookies in secrets directory")
            
            # First check if the file exists in the Render secrets directory
            if os.path.exists(render_secrets_path):
                logger.info(f"Found cookies.txt in Render secrets directory: {render_secrets_path}")
                
                # Always copy from secrets to a local writable file
                try:
                    with open(render_secrets_path, 'r') as source:
                        content = source.read()
                    
                    # Write to the local app directory (which is writable)
                    with open(cookies_file, 'w') as dest:
                        dest.write(content)
                    logger.info(f"Copied cookies from Render secrets to local writable file: {cookies_file}")
                    
                    # Fix the format of the cookies file
                    fix_cookies_file_format(cookies_file)
                    return True
                except Exception as e:
                    logger.error(f"Error copying cookies from secrets: {e}")
                    logger.error(traceback.format_exc())
                    # Try to create a template file as fallback
                    ensure_cookies_file_has_content(cookies_file)
                    return True
            else:
                # On Render but cookies file not found in secrets
                logger.error("Cookies file not found in Render secrets directory! YouTube content may not play correctly.")
                logger.info("Please add cookies.txt to your Render secrets with the correct Netscape format.")
                
                # Check if we have a cookies file in the regular path as a fallback
                if os.path.exists(cookies_file):
                    logger.info(f"Found cookies.txt in app directory as fallback: {cookies_file}")
                    # Fix the format if needed
                    fix_cookies_file_format(cookies_file)
                    return True
                else:
                    # Create a template file
                    ensure_cookies_file_has_content(cookies_file)
                    return True

        # Local development behavior
        if not os.path.exists(cookies_file):
            logger.info(f"Creating empty cookies file: {cookies_file}")
            with open(cookies_file, 'w') as f:
                # Write a proper cookies file template
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# This file is intended to be used with yt-dlp / youtube-dl\n")
                f.write("# This file makes it more likely that YouTube will treat the request as coming from a real browser\n\n")
                f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tCONSENT\tYES+cb\n")
                f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR_INFO1_LIVE\tRandomValueHere\n")
                f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tYSC\tRandomValueHere\n")
                f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tGPS\t1\n")
                f.write(".google.com\tTRUE\t/\tTRUE\t0\tNID\tRandomValueHere\n")
                f.write(".google.com\tTRUE\t/\tTRUE\t0\tCONSENT\tYES+cb\n\n")
                f.write("# For better results, please login to YouTube in your browser and extract real cookies\n")
            logger.info(f"Created cookies file template at: {cookies_file}")
        else:
            # Check if the file has content
            with open(cookies_file, 'r') as f:
                content = f.read().strip()
                if not content or content == "# Netscape HTTP Cookie File":
                    logger.warning(f"Cookies file exists but appears empty. Creating template.")
                    ensure_cookies_file_has_content(cookies_file)
        return True
    except Exception as e:
        logger.error(f"Error managing cookies file: {e}")
        return False

def ensure_cookies_file_has_content(cookies_file):
    """Ensure the cookies file has some basic content to work with."""
    try:
        with open(cookies_file, 'w') as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# This file is intended to be used with yt-dlp / youtube-dl\n")
            f.write("# This file makes it more likely that YouTube will treat the request as coming from a real browser\n\n")
            f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tCONSENT\tYES+cb\n")
            f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR_INFO1_LIVE\tRandomValueHere\n")
            f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tYSC\tRandomValueHere\n")
            f.write(".youtube.com\tTRUE\t/\tTRUE\t0\tGPS\t1\n")
            f.write(".google.com\tTRUE\t/\tTRUE\t0\tNID\tRandomValueHere\n")
            f.write(".google.com\tTRUE\t/\tTRUE\t0\tCONSENT\tYES+cb\n\n")
            f.write("# For better results, please login to YouTube in your browser and extract real cookies\n")
        logger.info(f"Updated cookies file with template content: {cookies_file}")
        return True
    except Exception as e:
        logger.error(f"Error updating cookies file: {e}")
        return False

# Initialize Flask app
app = Flask(__name__, static_folder='dashboard/build')
CORS(app)
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    logger=True,  # Enable Socket.IO logging
    engineio_logger=True,  # Enable Engine.IO logging
    ping_timeout=60,  # Increase ping timeout for better connection stability
    ping_interval=25,  # Adjust ping interval
    async_mode='threading'  # Explicitly use threading mode
)

# Serve React App
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

# Store connected clients by guild_id
connected_clients = {}

# API Routes
@app.route('/api/status', methods=['GET'])
def get_status():
    """Get overall status info about the bot"""
    bot_status = {
        "user": {
            "name": bot.user.name if bot.user else "Unknown",
            "id": str(bot.user.id) if bot.user else "Unknown"
        },
        "guilds": len(bot.guilds),
        "active_servers": len(current_song)
    }
    
    return jsonify(bot_status)

@app.route('/api/guilds', methods=['GET'])
def get_guilds():
    """Get list of guilds the bot is in"""
    guilds_data = []
    for guild in bot.guilds:
        guilds_data.append({
            'id': str(guild.id),
            'name': guild.name,
            'is_playing': str(guild.id) in current_song and current_song[str(guild.id)] is not None
        })
    
    return jsonify(guilds_data)

@app.route('/api/guild/<guild_id>', methods=['GET'])
def get_guild_info(guild_id):
    """Get detailed information about a specific guild"""
    # Convert to string for consistency
    guild_id = str(guild_id)
    guild_id_int = int(guild_id)
    
    # Add debug logging
    logger.info(f"API: get_guild_info called for guild {guild_id}")
    logger.info(f"current_song keys: {list(current_song.keys())}")
    
    # Check both string and integer versions of guild_id
    current_song_obj = None
    if guild_id in current_song and current_song[guild_id] is not None:
        current_song_obj = current_song[guild_id]
        logger.info(f"Found current song using string guild_id: {guild_id}")
    elif guild_id_int in current_song and current_song[guild_id_int] is not None:
        current_song_obj = current_song[guild_id_int]
        logger.info(f"Found current song using integer guild_id: {guild_id_int}")
        # Copy to string key for consistency
        current_song[guild_id] = current_song[guild_id_int]
    
    # Log current song status
    if current_song_obj:
        logger.info(f"Current song for guild {guild_id}: {current_song_obj.title if hasattr(current_song_obj, 'title') else 'Unknown title'}")
    else:
        logger.info(f"No current song found for guild {guild_id} (checked both string and integer keys)")
    
    # Find the guild
    guild = None
    for g in bot.guilds:
        if str(g.id) == guild_id:
            guild = g
            break
    
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    
    # Check voice clients for this guild
    voice_client = None
    is_playing = False
    is_paused = False
    
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            is_playing = vc.is_playing()
            is_paused = vc.is_paused()
            logger.info(f"Voice client found for guild {guild_id}")
            logger.info(f"Voice client is playing: {is_playing}")
            logger.info(f"Voice client is paused: {is_paused}")
            break
    
    if not voice_client:
        logger.info(f"No voice client found for guild {guild_id}")
    
    # Convert current song to dict format
    current_song_dict = None
    if current_song_obj:
        try:
            current_song_dict = song_to_dict(current_song_obj)
            logger.info(f"Converted current song to dict: {current_song_dict}")
        except Exception as e:
            logger.error(f"Error converting current song to dict: {e}")
            current_song_dict = None
    
    # Get queue information using queue_to_list function
    queue_data = queue_to_list(guild_id)
    # If string ID didn't work, try integer ID
    if not queue_data and guild_id_int in queues:
        logger.info(f"Using integer guild_id {guild_id_int} for queue in get_guild_info")
        queue_data = queue_to_list(guild_id_int)
        # Copy to string version for consistency
        if queues.get(guild_id_int):
            queues[guild_id] = queues[guild_id_int].copy()  # Use copy to avoid reference issues
            logger.info(f"Copied queue from int to string guild_id in get_guild_info")
    
    queue_length = len(queue_data)
    logger.info(f"Queue has {queue_length} items in get_guild_info")
    
    # Get guild information
    guild_info = {
        'id': str(guild.id),
        'name': guild.name,
        'member_count': guild.member_count,
        'voice_connected': voice_client is not None,
        'is_playing': (current_song_obj is not None) and (voice_client is not None) and (is_playing or is_paused),
        'is_paused': is_paused if voice_client else False,
        'current_song': current_song_dict,
        'queue': queue_data,
        'queue_length': queue_length,
        'voice_channels': []  # Add voice channels to the response
    }
    
    # Add voice channels to the response
    for vc in guild.voice_channels:
        guild_info['voice_channels'].append({
            'id': str(vc.id),
            'name': vc.name,
            'member_count': len(vc.members),
            'has_bot': any(member.id == bot.user.id for member in vc.members)
        })
    
    # Check if the bot is connected to a voice channel in this guild
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            guild_info['voice_connected'] = True
            guild_info['is_paused'] = vc.is_paused()
            guild_info['connected_channel'] = {
                'id': str(vc.channel.id),
                'name': vc.channel.name
            }
            break
    
    # Log the final guild_info
    logger.info(f"Final guild_info for {guild_id}: is_playing={guild_info['is_playing']}, current_song={guild_info['current_song'] is not None}, queue_length={guild_info['queue_length']}")
    
    return jsonify(guild_info)

@app.route('/api/guild/<guild_id>/queue', methods=['GET'])
def get_queue(guild_id):
    """Get the current queue for a specific guild"""
    guild_id = str(guild_id)
    guild_id_int = int(guild_id)
    
    logger.info(f"API: get_queue called for guild {guild_id}")
    
    # Check for queue with both string and integer IDs
    queue_list = queue_to_list(guild_id)
    
    # If string ID didn't work, try integer ID
    if not queue_list and guild_id_int in queues:
        logger.info(f"API: Using integer guild_id {guild_id_int} for queue")
        queue_list = queue_to_list(guild_id_int)
        # Copy to string version for consistency
        if queues.get(guild_id_int):
            queues[guild_id] = queues[guild_id_int].copy()  # Use copy to avoid reference issues
            del queues[guild_id_int]  # Remove the integer key version
            logger.info(f"API: Copied queue from int to string guild_id and removed integer key")
    
    logger.info(f"API: Returning queue with {len(queue_list)} items")
    
    return jsonify({
        'queue': queue_list,
        'length': len(queue_list),
        'guild_id': guild_id
    })

@app.route('/api/guild/<guild_id>/current', methods=['GET'])
def get_current_song(guild_id):
    """Get the currently playing song for a specific guild"""
    guild_id = str(guild_id)
    
    if guild_id not in current_song or current_song[guild_id] is None:
        return jsonify({'current_song': None})
    
    song_data = song_to_dict(current_song[guild_id])
    song_data['thumbnail'] = get_thumbnail_url(song_data.get('url'))
    
    return jsonify({'current_song': song_data})

@app.route('/api/guild/<guild_id>/volume', methods=['POST'])
def set_volume(guild_id):
    """Set the volume for a specific guild"""
    guild_id = str(guild_id)
    
    # Get volume from request body
    data = request.json
    if not data or 'volume' not in data:
        return jsonify({"error": "Volume parameter is required"}), 400
        
    volume = int(data['volume'])
    # Clamp volume between 0 and 150
    volume = max(0, min(150, volume))
    
    # Find the guild in bot's guilds
    guild = None
    for g in bot.guilds:
        if str(g.id) == guild_id:
            guild = g
            break
            
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
        
    # Check if bot is in a voice channel in this guild
    voice_client = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            break
            
    if not voice_client:
        return jsonify({"error": "Bot not connected to a voice channel"}), 400
        
    if not voice_client.is_playing():
        return jsonify({"error": "Nothing is playing right now"}), 400
    
    # Set the volume on the current song
    if guild_id in current_song and current_song[guild_id]:
        current_song[guild_id].volume = volume / 100
        
        # Also update the song dictionary to reflect new volume
        emit_to_guild(guild_id, 'song_update', {
            'guild_id': guild_id,
            'current_song': song_to_dict(current_song[guild_id]),
            'action': 'volume_change'
        })
        
        return jsonify({"success": True, "volume": volume})
    else:
        return jsonify({"error": "Couldn't find the current song"}), 400

async def handle_pause_request(ctx):
    """Core functionality for pausing playback, used by both bot commands and API"""
    guild_id = ctx.guild.id
    logger.info(f"Pause functionality called for guild {guild_id}")
    
    if not ctx.voice_client:
        logger.warning(f"Pause called but bot not connected to voice in guild {guild_id}")
        return "Error: I'm not connected to a voice channel."
    
    if not ctx.voice_client.is_playing():
        logger.warning(f"Pause called but nothing is playing in guild {guild_id}")
        return "Error: Nothing is playing right now."
    
    if ctx.voice_client.is_paused():
        logger.warning(f"Pause called but song is already paused in guild {guild_id}")
        return "Error: Song is already paused."
    
    ctx.voice_client.pause()
    logger.info(f"Paused playback in guild {guild_id}")
    
    # Emit socket event to update UI
    emit_to_guild(guild_id, 'song_update', {
        'guild_id': str(guild_id),
        'is_paused': True,
        'action': 'pause'
    })
    
    return "⏸ Song paused."

async def handle_resume_request(ctx):
    """Core functionality for resuming playback, used by both bot commands and API"""
    guild_id = ctx.guild.id
    logger.info(f"Resume functionality called for guild {guild_id}")
    
    if not ctx.voice_client:
        logger.warning(f"Resume called but bot not connected to voice in guild {guild_id}")
        return "Error: I'm not connected to a voice channel."
    
    if not ctx.voice_client.is_paused():
        logger.warning(f"Resume called but no song is paused in guild {guild_id}")
        # Check if we're playing something
        if ctx.voice_client.is_playing():
            return "Error: Song is already playing."
        else:
            # If nothing is playing, try to play the next song
            logger.info(f"Nothing is playing, attempting to play next song in guild {guild_id}")
            asyncio.create_task(play_next(ctx))
            return "▶ No song was paused. Attempting to play next song..."
    
    ctx.voice_client.resume()
    logger.info(f"Resumed playback in guild {guild_id}")
    
    # Emit socket event to update UI
    emit_to_guild(guild_id, 'song_update', {
        'guild_id': str(guild_id),
        'is_paused': False,
        'action': 'resume'
    })
    
    return "▶ Song resumed."

@bot.command()
async def pause(ctx):
    """Pauses the current playback."""
    logger.info(f"Pause command used by {ctx.author} in guild {ctx.guild.id}")
    
    result = await handle_pause_request(ctx)
    if result.startswith("Error:"):
        await ctx.send(f"❌ {result[7:]}")
    else:
        await ctx.send(result)

@bot.command()
async def resume(ctx):
    """Resumes the paused playback."""
    logger.info(f"Resume command used by {ctx.author} in guild {ctx.guild.id}")
    
    result = await handle_resume_request(ctx)
    if result.startswith("Error:"):
        await ctx.send(f"❌ {result[7:]}")
    else:
        await ctx.send(result)

def run_command_with_context(fake_ctx, handler_func, *args):
    """Run a bot command with a fake context object"""
    
    async def run_command():
        try:
            logger.info(f"Running {handler_func.__name__} with context for guild {fake_ctx.guild.id}")
            
            # Validate context again before running
            if not fake_ctx.voice_client:
                logger.error(f"Voice client not available in context for guild {fake_ctx.guild.id}")
                return jsonify({"error": "Voice client not available"}), 400
                
            # Run the handler
            result = await handler_func(fake_ctx, *args)
            
            # Log the result
            logger.info(f"Command {handler_func.__name__} result: {result}")
            
            if result and isinstance(result, str):
                if result.startswith("Error:"):
                    # Remove the "Error: " prefix and return error message
                    return jsonify({"error": result[7:]}), 400
                else:
                    # Return success message
                    return jsonify({"success": True, "message": result})
            # If result is already a response tuple with jsonify and status code, return it directly
            return result
        except Exception as e:
            logger.error(f"Error running command with context: {e}")
            logger.error(traceback.format_exc())
            return jsonify({"error": str(e)}), 500
    
    # Run the command in the bot's event loop
    try:
        return asyncio.run_coroutine_threadsafe(run_command(), bot.loop).result()
    except Exception as e:
        logger.error(f"Error in run_command_with_context thread: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Command execution error: {str(e)}"}), 500

def create_fake_context(guild_id):
    """Create a fake context object for API endpoint use"""
    # Convert to string for consistency
    guild_id = str(guild_id)
    guild_id_int = int(guild_id)
    
    # Find the guild
    guild = None
    for g in bot.guilds:
        if str(g.id) == guild_id:
            guild = g
            break
            
    if not guild:
        return None, {"error": "Guild not found"}, 404
        
    # Find voice client for this guild
    voice_client = None
    channel = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            channel = vc.channel
            break
            
    if not voice_client:
        return None, {"error": "Bot not connected to a voice channel"}, 400
    
    # Create a fake context for bot command simulation
    class FakeContext:
        def __init__(self, guild, voice_client, channel):
            self.guild = guild
            self.voice_client = voice_client
            self.author = guild.me  # Use the bot as the author
            self.channel = channel
            
        async def invoke(self, command):
            logger.info(f"Fake context invoking {command.__name__}")
            return False
            
        async def send(self, content=None, *, embed=None, ephemeral=False, view=None):
            logger.info(f"API sending real message to Discord: {content}")
            # Actually send a real message to the Discord channel
            if self.channel:
                # Use bot.get_channel to ensure we have a proper channel object
                channel = bot.get_channel(self.channel.id)
                if channel:
                    try:
                        return await channel.send(content=content, embed=embed, view=view)
                    except Exception as e:
                        logger.error(f"Error sending message to channel: {e}")
                        logger.error(traceback.format_exc())
                else:
                    logger.error(f"Could not get channel {self.channel.id} for sending message")
            else:
                logger.error("No channel set in fake context, cannot send message")
            return None
            
        async def typing(self):
            class TypingContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    return None
            return TypingContextManager()
    
    # Create and return the fake context
    fake_ctx = FakeContext(guild, voice_client, channel)
    return fake_ctx, None, 200

# Create an alternative fake context that doesn't require a voice client connection
def create_basic_fake_context(guild_id):
    """Create a fake context object for API endpoint use without requiring a voice connection"""
    # Convert to string for consistency
    guild_id = str(guild_id)
    
    # Find the guild
    guild = None
    for g in bot.guilds:
        if str(g.id) == guild_id:
            guild = g
            break
            
    if not guild:
        return None, {"error": "Guild not found"}, 404
    
    # Get a text channel to use for messages
    channel = None
    if guild.text_channels:
        # Use the first text channel as default
        channel = guild.text_channels[0]
    
    # Create a fake context for bot command simulation
    class FakeContext:
        def __init__(self, guild, channel):
            self.guild = guild
            self.voice_client = None  # No voice client yet
            self.author = guild.me  # Use the bot as the author
            self.channel = channel
            
            # Add message attribute for join command
            self.message = type('obj', (object,), {
                'author': type('obj', (object,), {
                    'voice': None
                })
            })
            
        async def invoke(self, command):
            # Add detailed debugging to understand the command structure
            logger.info(f"Basic fake context invoking command: {command}")
            logger.info(f"Command type: {type(command)}")
            logger.info(f"Command dir: {dir(command)}")
            
            # Get command name safely
            command_name = None
            if hasattr(command, 'name'):
                command_name = command.name
            elif hasattr(command, '__name__'):
                command_name = command.__name__
            else:
                command_name = str(command)
                
            logger.info(f"Using command name: {command_name}")
            
            if command_name == 'join':
                # Special handling for join command
                channel_id = getattr(self, '_voice_channel_id', None)
                if channel_id:
                    # Find the voice channel
                    for vc in self.guild.voice_channels:
                        if str(vc.id) == channel_id:
                            # Set up the context for join command
                            self.message.author.voice = type('obj', (object,), {
                                'channel': vc
                            })
                            # Actually join the channel
                            await command(self)
                            return True
                return False
            return False
            
        async def send(self, content=None, *, embed=None, ephemeral=False, view=None):
            logger.info(f"API sending message to Discord: {content}")
            # Actually send a real message to the Discord channel
            if self.channel:
                # Use bot.get_channel to ensure we have a proper channel object
                channel = bot.get_channel(self.channel.id)
                if channel:
                    try:
                        return await channel.send(content=content, embed=embed, view=view)
                    except Exception as e:
                        logger.error(f"Error sending message to channel: {e}")
                        logger.error(traceback.format_exc())
                else:
                    logger.error(f"Could not get channel {self.channel.id} for sending message")
            else:
                logger.error("No channel set in fake context, cannot send message")
            return None
            
        async def typing(self):
            class TypingContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    return None
            return TypingContextManager()
    
    # Create and return the fake context
    fake_ctx = FakeContext(guild, channel)
    return fake_ctx, None, 200

@app.route('/api/guild/<guild_id>/play', methods=['POST'])
def play_song(guild_id):
    """Play a song via URL or search term"""
    guild_id = str(guild_id)
    
    # Get URL from request body
    data = request.json
    if not data or 'url' not in data:
        return jsonify({"error": "URL parameter is required"}), 400
    
    search = data['url']  # This can be a URL or search term
    
    # Create fake context
    fake_ctx, error, status_code = create_fake_context(guild_id)
    
    # If we got an error about not being connected to a voice channel,
    # and the request included a channel_id, try to join that channel first
    if error and status_code == 400 and error.get('error') == 'Bot not connected to a voice channel' and 'channel_id' in data:
        channel_id = data['channel_id']
        logger.info(f"Bot not in voice channel, trying to join channel {channel_id} in guild {guild_id}")
        
        # Try to join the voice channel
        join_data = {"channel_id": channel_id}
        join_response = join_voice_channel(guild_id)
        
        # If join was successful, try creating the context again
        if isinstance(join_response, dict) and join_response.get('success'):
            logger.info(f"Successfully joined voice channel, retrying play request")
            fake_ctx, error, status_code = create_fake_context(guild_id)
    
    if error:
        return jsonify(error), status_code
    
    # Initialize queue if it doesn't exist
    if guild_id not in queues:
        queues[guild_id] = deque()
    
    # Explicitly check for playlist URL - same logic as in handle_play_request
    if 'list=' in search:
        logger.info(f"API: Detected playlist URL: {search}")
        # Run handle_playlist in the bot's event loop
        try:
            async def run_playlist_handler():
                result = await handle_playlist(fake_ctx, search)
                return result
                
            # Run the async function in the bot's event loop
            result = asyncio.run_coroutine_threadsafe(run_playlist_handler(), bot.loop).result()
            return jsonify({"success": True, "message": "Playlist added to queue"}), 200
        except Exception as e:
            logger.error(f"Error handling playlist in API endpoint: {e}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"Error handling playlist: {str(e)}"}), 500
    
    # For regular URLs or search terms, use handle_play_request as before
    return run_command_with_context(fake_ctx, handle_play_request, search)

@app.route('/api/guild/<guild_id>/skip', methods=['POST'])
def skip_song(guild_id):
    """Skip the current song"""
    guild_id = str(guild_id)
    guild_id_int = int(guild_id)
    
    logger.info(f"API: skip_song called for guild {guild_id}")
    
    try:
        # Create fake context
        fake_ctx, error, status_code = create_fake_context(guild_id)
        if error:
            return jsonify(error), status_code
        
        # Extra validation to make sure the context is valid
        if not fake_ctx or not fake_ctx.guild or not fake_ctx.voice_client:
            logger.error(f"Invalid context for guild {guild_id} in skip_song")
            return jsonify({"error": "Invalid context or bot not connected to voice"}), 400
        
        # Run the command with context
        return run_command_with_context(fake_ctx, handle_skip_request)
    except Exception as e:
        logger.error(f"Error in skip_song API endpoint: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Server error: {str(e)}"}), 500

@app.route('/api/guild/<guild_id>/pause', methods=['POST'])
def pause_playback(guild_id):
    """Pause the current playback"""
    guild_id = str(guild_id)
    
    # Create fake context
    fake_ctx, error, status_code = create_fake_context(guild_id)
    if error:
        return jsonify(error), status_code
    
    # Run the command with context
    return run_command_with_context(fake_ctx, handle_pause_request)
    
@app.route('/api/guild/<guild_id>/resume', methods=['POST'])
def resume_playback(guild_id):
    """Resume the paused playback"""
    guild_id = str(guild_id)
    
    # Create fake context
    fake_ctx, error, status_code = create_fake_context(guild_id)
    if error:
        return jsonify(error), status_code
    
    # Run the command with context
    return run_command_with_context(fake_ctx, handle_resume_request)

@app.route('/api/guild/<guild_id>/stop', methods=['POST'])
def stop_playback(guild_id):
    """Stop playback and clear the queue"""
    guild_id = str(guild_id)
    
    # Create fake context
    fake_ctx, error, status_code = create_fake_context(guild_id)
    if error:
        return jsonify(error), status_code
    
    # Run the command with context
    return run_command_with_context(fake_ctx, handle_stop_request)

@app.route('/api/guild/<guild_id>/queue/<int:index>', methods=['DELETE'])
def remove_from_queue(guild_id, index):
    """Remove a song from the queue at the given index"""
    guild_id = str(guild_id)
    
    # Check if queue exists
    if guild_id not in queues:
        return jsonify({"error": "Queue not found"}), 404
    
    # Check if index is valid
    if index < 0 or index >= len(queues[guild_id]):
        return jsonify({"error": "Invalid queue index"}), 400
    
    # Remove the song at the specified index
    try:
        # Convert deque to list to allow index removal
        queue_list = list(queues[guild_id])
        removed_url = queue_list.pop(index)
        queues[guild_id] = deque(queue_list)
        
        # Emit socket event to update UI
        emit_to_guild(guild_id, 'queue_update', {
            'guild_id': guild_id,
            'queue': queue_to_list(guild_id),
            'action': 'remove'
        })
        
        return jsonify({"success": True, "message": "Removed from queue", "removed_url": removed_url})
    except Exception as e:
        return jsonify({"error": f"Failed to remove from queue: {str(e)}"}), 500

@app.route('/api/guild/<guild_id>/queue/<int:index>/play', methods=['POST'])
def play_from_index(guild_id, index):
    """Skip to and play a specific song in the queue"""
    guild_id = str(guild_id)
    
    # Check if queue exists
    if guild_id not in queues:
        return jsonify({"error": "Queue not found"}), 404
    
    # Check if index is valid
    if index < 0 or index >= len(queues[guild_id]):
        return jsonify({"error": "Invalid queue index"}), 400
    
    # Find the guild's voice client
    voice_client = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            break
            
    if not voice_client:
        return jsonify({"error": "Bot not connected to a voice channel"}), 400
    
    try:
        # Get the URL at the specified index
        queue_list = list(queues[guild_id])
        selected_url = queue_list[index]
        
        # Rearrange the queue: remove all songs before the selected one
        new_queue = deque([selected_url] + queue_list[index+1:])
        queues[guild_id] = new_queue
        
        # Stop current playback to trigger playing the next song
        voice_client.stop()
        
        # Emit socket event to update UI
        emit_to_guild(guild_id, 'queue_update', {
            'guild_id': guild_id,
            'queue': queue_to_list(guild_id),
            'action': 'reorder'
        })
        
        return jsonify({"success": True, "message": "Playing selected song"})
    except Exception as e:
        return jsonify({"error": f"Failed to play from index: {str(e)}"}), 500

@app.route('/api/guild/<guild_id>/queue/clear', methods=['POST'])
def clear_queue(guild_id):
    """Clear all songs from the queue"""
    guild_id = str(guild_id)
    
    # Check if queue exists
    if guild_id not in queues:
        return jsonify({"error": "Queue not found"}), 404
    
    # Clear the queue
    queues[guild_id].clear()
    
    # Emit socket event to update UI
    emit_to_guild(guild_id, 'queue_update', {
        'guild_id': guild_id,
        'queue': [],
        'queue_length': 0,
        'action': 'clear'
    })
    
    return jsonify({"success": True, "message": "Queue cleared"})

@app.route('/api/debug', methods=['GET'])
def debug():
    """Debug endpoint to test if API is running"""
    return jsonify({"status": "API is running", "bot": bot.user.name if bot.user else "Bot not connected"})

# Socket events
@socketio.on('connect')
def connect():
    logger.info(f"Client connected: {request.sid}")
    logger.info(f"Socket.IO connection details - SID: {request.sid}, Transport: {request.environ.get('wsgi.websocket', 'Not WebSocket')}")

@socketio.on('disconnect')
def disconnect():
    logger.info(f"Client disconnected: {request.sid}")
    # Remove client from all guild rooms
    for guild_id in list(connected_clients.keys()):
        if request.sid in connected_clients[guild_id]:
            connected_clients[guild_id].remove(request.sid)
            leave_room(guild_id)
            logger.info(f"Removed client {request.sid} from guild {guild_id} due to disconnect")

@socketio.on('join_guild')
def on_join_guild(data):
    logger.info(f"Received join_guild request: {data}")
    if 'guild_id' not in data:
        logger.error(f"Client {request.sid} sent invalid join_guild request without guild_id")
        return
    
    guild_id = str(data['guild_id'])
    
    # Add the client to the guild room
    join_room(guild_id)
    
    # Track the client
    if guild_id not in connected_clients:
        connected_clients[guild_id] = set()
    connected_clients[guild_id].add(request.sid)
    
    logger.info(f"Client {request.sid} joined guild {guild_id}")
    
    # Send an immediate update
    try:
        socketio.emit('song_update', {'guild_id': guild_id}, room=request.sid)
        socketio.emit('queue_update', {'guild_id': guild_id}, room=request.sid)
        logger.info(f"Sent initial updates to client {request.sid} for guild {guild_id}")
    except Exception as e:
        logger.error(f"Error sending initial updates to client {request.sid}: {e}")

@socketio.on('leave_guild')
def on_leave_guild(data):
    """Handle client leaving a specific guild channel"""
    guild_id = str(data.get('guild_id'))
    logger.info(f"Client {request.sid} leaving guild {guild_id}")
    
    # Remove client from the guild's room
    if guild_id in connected_clients and request.sid in connected_clients[guild_id]:
        connected_clients[guild_id].remove(request.sid)
        leave_room(guild_id)
        logger.info(f"Removed client {request.sid} from guild {guild_id}")

# Function to convert song data to a JSON-serializable format
def song_to_dict(song):
    if not song:
        return None
    
    # Extra debug logging for troubleshooting
    logger.info(f"song_to_dict called with song: {song}")
    
    # Extract the required information
    try:
        song_dict = {
            'title': song.title if hasattr(song, 'title') else "Unknown",
            'url': song.url if hasattr(song, 'url') else None,
            'thumbnail': get_thumbnail_url(song.url if hasattr(song, 'url') else None),
            'volume': song.volume * 100 if hasattr(song, 'volume') else 70  # Convert to percentage
        }
        return song_dict
    except Exception as e:
        logger.error(f"Error in song_to_dict: {e}")
        return None

# Function to get thumbnail URL from YouTube URL
def get_thumbnail_url(url):
    if not url:
        return "https://i.imgur.com/ufxvZ0j.png"  # Default music thumbnail
    
    try:
        if "v=" in url:
            video_id = url.split("v=")[-1]
            # Remove any additional parameters
            if "&" in video_id:
                video_id = video_id.split("&")[0]
            return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[-1]
            # Remove any additional parameters
            if "?" in video_id:
                video_id = video_id.split("?")[0]
            return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    except Exception as e:
        logger.warning(f"Could not extract video ID from URL: {url}. Error: {e}")
    
    return "https://i.imgur.com/ufxvZ0j.png"  # Default music thumbnail

# Function to convert queue data to a JSON-serializable format
def queue_to_list(guild_id):
    guild_id_str = str(guild_id)
    guild_id_int = int(guild_id) if str(guild_id).isdigit() else None
    
    # Try string ID first
    if guild_id_str in queues:
        logger.info(f"queue_to_list: Found queue using string guild_id {guild_id_str}")
        queue_items = queues[guild_id_str]
    # Then try integer ID
    elif guild_id_int is not None and guild_id_int in queues:
        logger.info(f"queue_to_list: Found queue using integer guild_id {guild_id_int}")
        queue_items = queues[guild_id_int]
        # Copy to string key for consistency
        queues[guild_id_str] = queues[guild_id_int].copy()
        del queues[guild_id_int]
        logger.info(f"queue_to_list: Synchronized queue from integer to string key")
    else:
        logger.info(f"queue_to_list: Guild {guild_id_str} not found in queues")
        return []
    
    logger.info(f"queue_to_list: Converting queue for guild {guild_id_str} with {len(queue_items)} items")
    queue_list = []
    for i, url in enumerate(queue_items):
        try:
            # Extract video ID for thumbnail
            thumbnail = get_thumbnail_url(url)
            
            # Use cached song info if available to get the title
            title = None
            if url in song_cache and 'title' in song_cache[url]:
                title = song_cache[url]['title']
            
            queue_item = {
                'url': url,
                'thumbnail': thumbnail,
                'title': title or url  # If no title found, use the URL
            }
            queue_list.append(queue_item)
            logger.info(f"queue_to_list: Processed item {i+1}: {title or url}")
        except Exception as e:
            logger.error(f"Error processing queue item {url}: {e}")
            # Still include the item even if there was an error
            queue_list.append({
                'url': url,
                'thumbnail': "https://i.imgur.com/ufxvZ0j.png",  # Default thumbnail
                'title': url
            })
    
    logger.info(f"queue_to_list: Returning {len(queue_list)} queue items")
    return queue_list

# Function to emit socket event to clients in a guild
def emit_to_guild(guild_id, event, data):
    """Emit an event to all clients in a specific guild"""
    # Convert guild_id to string for consistency in the socket system
    guild_id = str(guild_id)
    guild_id_int = int(guild_id)
    
    logger.info(f"emit_to_guild called for guild {guild_id}, event: {event}")
    
    if guild_id in connected_clients and connected_clients[guild_id]:
        logger.info(f"Emitting {event} to {len(connected_clients[guild_id])} clients in guild {guild_id}")
        
        # Make sure guild_id is included in the data
        if 'guild_id' not in data:
            data['guild_id'] = guild_id
        
        # Enhance data based on event type
        if event == 'song_update':
            # Always provide fresh current_song data for any song_update event
            # Find voice client to check if paused
            voice_client = None
            for vc in bot.voice_clients:
                if str(vc.guild.id) == guild_id:
                    voice_client = vc
                    break
            
            # Try to get song with guild ID as string and as int
            song_obj = None
            if guild_id in current_song:
                song_obj = current_song[guild_id]
            elif guild_id_int in current_song:
                song_obj = current_song[guild_id_int]
                # For consistency, update the current_song with string key
                current_song[guild_id] = song_obj
            
            logger.info(f"emit_to_guild: Got song_obj = {song_obj}")
            if song_obj:
                logger.info(f"emit_to_guild: Song title = {song_obj.title if hasattr(song_obj, 'title') else 'Unknown'}")
                
            is_playing = voice_client and (voice_client.is_playing() or voice_client.is_paused())
            is_paused = voice_client.is_paused() if voice_client else False
            
            # Get current song with more details
            current_song_data = None
            if song_obj is not None:
                try:
                    current_song_data = {
                        'title': song_obj.title if hasattr(song_obj, 'title') else "Unknown",
                        'url': song_obj.url if hasattr(song_obj, 'url') else None,
                        'thumbnail': get_thumbnail_url(song_obj.url if hasattr(song_obj, 'url') else None),
                        'volume': song_obj.volume * 100 if hasattr(song_obj, 'volume') else 70
                    }
                    logger.info(f"Emitting current song: {current_song_data['title']}")
                except Exception as e:
                    logger.error(f"Error creating current_song_data: {e}")
                    current_song_data = None
            else:
                logger.warning(f"No current song to emit for guild {guild_id}")
            
            # Always update the data with the latest song info, even if it was already provided
            data['current_song'] = current_song_data
            data['is_playing'] = is_playing
            data['is_paused'] = is_paused
            
        elif event == 'queue_update' and 'queue' not in data:
            # Get queue with more details using queue_to_list function for consistency
            logger.info(f"emit_to_guild: Getting queue for {guild_id}")
            queue_data = queue_to_list(guild_id)
            
            # If string version didn't work, try integer version 
            if not queue_data and guild_id_int in queues:
                logger.info(f"emit_to_guild: Trying integer guild_id {guild_id_int} for queue")
                queue_data = queue_to_list(guild_id_int)
                # If found with integer, copy to string version for consistency
                if queues.get(guild_id_int):
                    queues[guild_id] = queues[guild_id_int]
                    logger.info(f"emit_to_guild: Copied queue from int to string guild_id")
                
            data['queue'] = queue_data
            data['queue_length'] = len(queue_data)
            logger.info(f"emit_to_guild: Queue has {len(queue_data)} items")
            
        # Log the data being sent (but truncate large fields)
        log_data = data.copy()
        if 'queue' in log_data and log_data['queue']:
            log_data['queue'] = f"[{len(log_data['queue'])} items]"
        if 'current_song' in log_data and log_data['current_song']:
            log_data['current_song'] = {
                'title': log_data['current_song'].get('title', 'Unknown'),
                'url': log_data['current_song'].get('url', 'None')
            }
        logger.info(f"Emit data: {log_data}")
        
        # Keep track of successful emissions
        success_count = 0
        error_count = 0    
            
        for client_sid in connected_clients[guild_id]:
            try:
                socketio.emit(event, data, room=client_sid)
                success_count += 1
            except Exception as e:
                error_count += 1
                logger.error(f"Error emitting {event} to client {client_sid}: {e}")
                
        logger.info(f"Emitted {event} to {success_count} clients successfully, {error_count} failures")
    else:
        logger.info(f"No clients connected for guild {guild_id}, skipping {event} event")

# Add a new endpoint to get voice channels for a guild
@app.route('/api/guild/<guild_id>/voice_channels', methods=['GET'])
def get_voice_channels(guild_id):
    """Get list of voice channels in a guild"""
    guild_id = str(guild_id)
    
    # Find the guild
    guild = None
    for g in bot.guilds:
        if str(g.id) == guild_id:
            guild = g
            break
    
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    
    # Get voice channels
    voice_channels = []
    for vc in guild.voice_channels:
        # Count members in channel
        member_count = len(vc.members)
        
        voice_channels.append({
            'id': str(vc.id),
            'name': vc.name,
            'member_count': member_count,
            'has_bot': any(member.id == bot.user.id for member in vc.members)
        })
    
    return jsonify(voice_channels)

# Add a new endpoint to join a voice channel
@app.route('/api/guild/<guild_id>/join', methods=['POST'])
def join_voice_channel(guild_id):
    """Join a voice channel in a guild"""
    guild_id = str(guild_id)
    
    # Get voice channel ID from request body
    data = request.json
    if not data or 'channel_id' not in data:
        return jsonify({"error": "Voice channel ID is required"}), 400
    
    channel_id = str(data['channel_id'])
    
    # Create a basic fake context
    fake_ctx, error, status_code = create_basic_fake_context(guild_id)
    if error:
        return jsonify(error), status_code
    
    # Find the voice channel in the guild
    guild = fake_ctx.guild
    voice_channel = None
    for vc in guild.voice_channels:
        if str(vc.id) == channel_id:
            voice_channel = vc
            break
    
    if not voice_channel:
        return jsonify({"error": "Voice channel not found"}), 404
    
    # Set the voice channel ID for the join command
    fake_ctx._voice_channel_id = channel_id
    
    # Already connected to this channel?
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id and vc.channel.id == voice_channel.id:
            return jsonify({
                "success": True, 
                "message": f"Already connected to {voice_channel.name}",
                "already_connected": True
            })
    
    try:
        # Run the join command with the fake context
        asyncio.run_coroutine_threadsafe(fake_ctx.invoke(join), bot.loop).result()
        
        # Check if the bot is now connected
        connected = False
        for vc in bot.voice_clients:
            if str(vc.guild.id) == guild_id:
                connected = True
                break
        
        if connected:
            return jsonify({
                "success": True, 
                "message": f"Joined voice channel: {voice_channel.name}"
            })
        else:
            return jsonify({
                "error": "Failed to join voice channel"
            }), 500
    except Exception as e:
        logger.error(f"Error joining voice channel: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Error: {str(e)}"}), 500

# Health check endpoint for Render
@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Render to use."""
    return jsonify({
        "status": "healthy",
        "environment": "render" if is_running_on_render() else "local",
        "bot_connected": bot.user is not None,
        "uptime": time.time() - bot.uptime if hasattr(bot, 'uptime') else None
    })

# Function to run the Discord bot
def run_bot():
    """Run the Discord bot"""
    logger.info("Starting Discord bot")
    bot.run(BOT_TOKEN)

# Main entry point
if __name__ == "__main__":
    # Check if we should run in API mode or standalone bot mode
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--with-api":
        # Start the bot in a separate thread
        bot_thread = threading.Thread(target=run_bot)
        bot_thread.daemon = True
        bot_thread.start()
        
        # Create PID file
        create_pid_file()
        try:
            # Start the Flask server
            logger.info("Starting web server")
            socketio.run(
                app, 
                host='0.0.0.0', 
                port=API_PORT,
                debug=True,  # Enable debug mode
                allow_unsafe_werkzeug=True, 
                log_output=True,  # Log Socket.IO server output
                use_reloader=False  # Don't use reloader with threading
            )
        finally:
            # Remove PID file on shutdown
            remove_pid_file()
    else:
        # Run in standalone bot mode
        bot.run(BOT_TOKEN)

async def handle_skip_request(ctx):
    """Core functionality for skipping playback, used by both bot commands and API"""
    guild_id = ctx.guild.id
    guild_id_str = str(guild_id)
    logger.info(f"Skip functionality called for guild {guild_id_str}")
    
    if not ctx.voice_client:
        logger.warning(f"Skip called but bot not connected to voice in guild {guild_id_str}")
        return "Error: I'm not connected to a voice channel."
    
    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        logger.warning(f"Skip called but nothing is playing in guild {guild_id_str}")
        return "Error: Nothing is playing right now."
    
    # Store information about the current song before skipping
    current_song_info = None
    if guild_id_str in current_song and current_song[guild_id_str]:
        current_song_info = {
            'title': current_song[guild_id_str].title if hasattr(current_song[guild_id_str], 'title') else "Unknown"
        }
    elif guild_id in current_song and current_song[guild_id]:
        current_song_info = {
            'title': current_song[guild_id].title if hasattr(current_song[guild_id], 'title') else "Unknown"
        }
        # Move to string key for consistency
        current_song[guild_id_str] = current_song[guild_id]
        del current_song[guild_id]
    
    # Check if we have a next song to play
    has_next_song = False
    next_song_title = "Unknown"
    
    # First check preloaded song
    if guild_id in preloaded_songs and preloaded_songs[guild_id]:
        has_next_song = True
        next_song_title = preloaded_songs[guild_id].title if hasattr(preloaded_songs[guild_id], 'title') else "Unknown"
    # Then check queue
    elif guild_id in queues and queues[guild_id]:
        has_next_song = True
        # Try to get info about the next song from cache
        next_url = queues[guild_id][0]
        if next_url in song_cache and 'title' in song_cache[next_url]:
            next_song_title = song_cache[next_url]['title']
    
    # Stop current playback - this will trigger play_next which handles playing the next song
    ctx.voice_client.stop()
    logger.info(f"Stopped current song for skip in guild {guild_id_str}")
    
    # Immediately force a refresh of the queue for the dashboard
    emit_to_guild(guild_id, 'queue_update', {
        'guild_id': guild_id_str,
        'queue': queue_to_list(guild_id),
        'action': 'skip'
    })
    
    # Give the bot a moment to start playing the next song
    await asyncio.sleep(0.5)
    
    # Do an immediate refresh of the song data as well
    song_obj = None
    if guild_id_str in current_song:
        song_obj = current_song[guild_id_str]
    elif guild_id in current_song:
        song_obj = current_song[guild_id]
        # Move to string key for consistency
        current_song[guild_id_str] = current_song[guild_id]
        del current_song[guild_id]
    
    emit_to_guild(guild_id, 'song_update', {
        'guild_id': guild_id_str,
        'current_song': song_to_dict(song_obj),
        'action': 'skip'
    })
    
    if has_next_song:
        return f"⏭ Skipped to next song: {next_song_title}"
    else:
        return "⏭ Skipped current song. No more songs in queue."

async def handle_stop_request(ctx):
    """Core functionality for stopping playback, used by both bot commands and API"""
    guild_id = ctx.guild.id
    guild_id_str = str(guild_id)
    logger.info(f"Stop functionality called for guild {guild_id_str}")
    
    if not ctx.voice_client:
        logger.warning(f"Stop called but bot not connected to voice in guild {guild_id_str}")
        return "Error: I'm not connected to a voice channel."
    
    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        logger.warning(f"Stop called but nothing is playing in guild {guild_id_str}")
        return "Error: Nothing is playing right now."
    
    # Stop the current song
    ctx.voice_client.stop()
    
    # Clear the queue and current song
    if guild_id in queues:
        queues[guild_id].clear()
        logger.info(f"Cleared queue for guild {guild_id_str}")
    
    if guild_id_str in current_song:
        current_song[guild_id_str] = None
        logger.info(f"Cleared current song for guild {guild_id_str}")
    elif guild_id in current_song:
        current_song[guild_id] = None
        # Move to string key for consistency
        current_song[guild_id_str] = None
        del current_song[guild_id]
        logger.info(f"Cleared current song for guild {guild_id_str} (converted from int)")
    
    if guild_id in preloaded_songs and preloaded_songs[guild_id]:
        preloaded_songs[guild_id].cleanup()
        preloaded_songs[guild_id] = None
        logger.info(f"Cleared preloaded song for guild {guild_id_str}")
    
    # Update the music message if it exists
    if guild_id in current_song_message and current_song_message[guild_id]:
        try:
            embed = discord.Embed(title="⏹ Playback Stopped", description="The queue has been cleared.", color=discord.Color.red())
            await current_song_message[guild_id].edit(embed=embed, view=None)
        except discord.NotFound:
            pass
    
    # Emit socket events to update UI
    emit_to_guild(guild_id, 'song_update', {
        'guild_id': guild_id_str,
        'current_song': None,
        'is_playing': False,
        'action': 'stop'
    })
    
    emit_to_guild(guild_id, 'queue_update', {
        'guild_id': guild_id_str,
        'queue': [],
        'queue_length': 0,
        'action': 'clear'
    })
    
    return "⏹ Stopped playback and cleared the queue."

async def download_audio(url, output_path, cookies_file='cookies.txt'):
    """Download audio from a YouTube URL using yt-dlp."""
    try:
        # Check for cookies file in app directory
        cookies_exists = False
        
        # Local cookies file should exist if ensure_cookies_file was called
        if os.path.exists(cookies_file):
            logger.info(f"Using local cookies file for download: {cookies_file}")
            cookies_exists = True
        else:
            logger.error("No cookies file found for download! This may affect YouTube extraction.")

        # Prepare yt-dlp options
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': output_path,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
        }

        # Add cookies if available
        if cookies_exists:
            with open(cookies_file, 'r') as f:
                content = f.read().strip()
                if content and content.startswith("# Netscape HTTP Cookie File"):
                    ydl_opts['cookiefile'] = cookies_file
                    # Add option to prevent trying to save cookies back to file
                    ydl_opts['nooverwrites'] = True
                    logger.info(f"Using cookies file for authentication: {cookies_file}")
                else:
                    logger.warning(f"Cookies file exists but appears empty or is a template: {cookies_file}")

        # Use best options we've found through testing if available
        if best_youtube_options:
            # Apply only the options that are safe for Render
            safe_options = safe_youtube_options_for_render(best_youtube_options)
            ydl_opts.update(safe_options)

        # Download the audio
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"Error downloading audio: {e}")
        return False

