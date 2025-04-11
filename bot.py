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
import datetime
import traceback
import atexit
import threading
import time
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"bot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('music_bot')

# Load environment variables from .env file
load_dotenv()

# Retrieve the bot token from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")

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
        
        # Check if this is a URL or a search query
        if cls.is_url(url_or_search):
            logger.info(f"Processing URL: {url_or_search}")
            url = url_or_search
        else:
            logger.info(f"Processing search query: {url_or_search}")
            # Treat as search query
            url = f"ytsearch:{url_or_search}"
        
        logger.info(f"Creating YTDLSource from URL: {url}")
        
        # Only validate URL format for actual URLs, not for search queries
        if not url.startswith('ytsearch:') and not url.startswith(('http://', 'https://')):
            logger.error(f"Invalid URL format: {url}")
            raise YTDLError("Invalid URL format. Please provide a valid HTTP/HTTPS URL.")
        
        # Check if we have cached data for this URL
        if url in song_cache:
            logger.info(f"Using cached data for: {url}")
            data = song_cache[url]
            filename = data['url'] if stream else data.get('filename')
            ffmpeg_options = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                'options': '-vn'
            }
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
            
        # Define different format options to try in case of format errors
        format_options = [
            'bestaudio[ext=m4a]/bestaudio/best',  # Try first with m4a format
            'bestaudio/best',                     # Then try any audio format
            'worstaudio/worst'                    # Last resort, any audio quality
        ]
        
        # Use the appropriate format option based on retry count
        if retry_count < len(format_options):
            selected_format = format_options[retry_count]
        else:
            # If we've tried all formats, raise an error
            logger.error(f"Tried all format options for URL: {url}")
            raise YTDLError(f"Could not extract information from URL after multiple format attempts: {url}")
            
        logger.info(f"Using format option for URL {url}: {selected_format} (retry: {retry_count})")
        
        # Special handling for search queries vs direct URLs
        if url.startswith('ytsearch:'):
            logger.info(f"Using simplified options for search query")
            ydl_opts = {
                'format': selected_format,
                'quiet': True,
                'no_warnings': True,
                'default_search': 'auto',
                'noplaylist': True,
                'nocheckcertificate': True,
                'ignoreerrors': False,  # We want to catch errors for search queries
                'logtostderr': False,
                'geo_bypass': True,
                'source_address': '0.0.0.0',  # Bind to all interfaces
                'retries': 5
            }
        else:
            ydl_opts = {
                'format': selected_format,
                'postprocessors': [],        # No post-processing to avoid any delays
                'extract_flat': 'in_playlist',
                'quiet': True,
                'ignoreerrors': True,
                'retries': 10,
                'nocheckcertificate': True,
                'skip_download': True,       # Important: just streaming, not downloading
                'default_search': 'auto',
                'cookies': 'cookies.txt',
                'geo_bypass': True,          # Bypass geo-restrictions
                'geo_bypass_country': 'US',
                'socket_timeout': 30,
                'cookiefile': 'cookies.txt',
                'noplaylist': True
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
        
        guild_id = self.ctx.guild.id
        logger.info(f"Skip button pressed in guild {guild_id}")
        
        if not self.ctx.voice_client:
            logger.warning(f"Skip button pressed but bot not connected to voice in guild {guild_id}")
            await interaction.followup.send("❌ I'm not connected to a voice channel.", ephemeral=True)
            return
            
        if not self.ctx.voice_client.is_playing():
            logger.warning(f"Skip button pressed but nothing is playing in guild {guild_id}")
            await interaction.followup.send("❌ Nothing is playing right now.", ephemeral=True)
            return
        
        # Check if we're already processing a skip (lock mechanism)
        if guild_id in playing_locks and playing_locks[guild_id]:
            logger.warning(f"Skip button pressed but already processing a skip in guild {guild_id}")
            await interaction.followup.send("⏳ Please wait a moment before skipping again.", ephemeral=True)
            return
        
        # Set the lock
        playing_locks[guild_id] = True
        logger.info(f"Set playing lock for guild {guild_id}")
        
        try:
            # Fix the queue to remove any duplicates
            logger.info(f"Fixing queue for guild {guild_id} before skip")
            await fix_queue(guild_id)
            
            # Clean up the current song's FFmpeg process
            if guild_id in current_song and current_song[guild_id]:
                logger.info(f"Cleaning up current song before skip in guild {guild_id}")
                current_song[guild_id].cleanup()
            
            # Stop the current song
            logger.info(f"Stopping current song in guild {guild_id}")
            self.ctx.voice_client.stop()
            
            # Add a small delay to ensure the previous song is fully stopped
            await asyncio.sleep(0.5)
            
            # Check if there are songs in the queue
            if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
                # Get the next URL from the queue
                next_url = queues[guild_id].popleft()
                logger.info(f"Next song in queue for guild {guild_id}: {next_url}")
                
                try:
                    # Create the player for the next song
                    logger.info(f"Creating player for next song in guild {guild_id}")
                    player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                    
                    # Add a small delay to ensure buffer is filled
                    await asyncio.sleep(0.5)
                    
                    # Make sure we're not already playing something
                    if self.ctx.voice_client.is_playing():
                        logger.warning(f"Voice client is still playing after stop in guild {guild_id}, stopping again")
                        self.ctx.voice_client.stop()
                        await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                    
                    # Play the next song
                    logger.info(f"Playing next song in guild {guild_id}: {player.title}")
                    self.ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(self.ctx), bot.loop).result() if e is None else None)
                    current_song[guild_id] = player
                    
                    # Update the now playing message
                    await update_music_message(self.ctx, player)
                    
                    await interaction.followup.send(f"⏭ Skipped to: **{player.title}**", ephemeral=True)
                except Exception as e:
                    logger.error(f"Error playing next song after skip in guild {guild_id}: {e}")
                    logger.error(traceback.format_exc())
                    await interaction.followup.send("❌ Error playing the next song. Trying to continue...", ephemeral=True)
                    # Try to play the next song in the queue
                    asyncio.create_task(play_next(self.ctx))
            else:
                # No more songs in queue
                logger.info(f"No more songs in queue for guild {guild_id}")
                if guild_id in current_song_message and current_song_message[guild_id]:
                    try:
                        embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                        await current_song_message[guild_id].edit(embed=embed, view=None)
                    except discord.NotFound:
                        pass
                
                # Clear the current song
                current_song[guild_id] = None
        finally:
            # Release the lock
            playing_locks[guild_id] = False
            logger.info(f"Released playing lock for guild {guild_id}")

    @discord.ui.button(label="⏸ Pause", style=discord.ButtonStyle.gray)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        guild_id = self.ctx.guild.id
        logger.info(f"Pause button pressed in guild {guild_id}")
        
        if not self.ctx.voice_client:
            logger.warning(f"Pause button pressed but bot not connected to voice in guild {guild_id}")
            await interaction.followup.send("❌ I'm not connected to a voice channel.", ephemeral=True)
            return
        
        if not self.ctx.voice_client.is_playing():
            logger.warning(f"Pause button pressed but nothing is playing in guild {guild_id}")
            await interaction.followup.send("❌ Nothing is playing right now.", ephemeral=True)
            return
        
        if self.ctx.voice_client.is_paused():
            logger.warning(f"Pause button pressed but song is already paused in guild {guild_id}")
            await interaction.followup.send("⏸ Song is already paused.", ephemeral=True)
            return
        
        self.ctx.voice_client.pause()
        logger.info(f"Paused playback in guild {guild_id}")
        # Use followup instead of response since we already deferred
        await interaction.followup.send("⏸ Song paused.", ephemeral=True)

    @discord.ui.button(label="▶ Resume", style=discord.ButtonStyle.green)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        guild_id = self.ctx.guild.id
        logger.info(f"Resume button pressed in guild {guild_id}")
        
        if not self.ctx.voice_client:
            logger.warning(f"Resume button pressed but bot not connected to voice in guild {guild_id}")
            await interaction.followup.send("❌ I'm not connected to a voice channel.", ephemeral=True)
            return
        
        if not self.ctx.voice_client.is_paused():
            logger.warning(f"Resume button pressed but no song is paused in guild {guild_id}")
            # Check if we're playing something
            if self.ctx.voice_client.is_playing():
                await interaction.followup.send("▶ Song is already playing.", ephemeral=True)
            else:
                # If nothing is playing, try to play the next song
                logger.info(f"Nothing is playing, attempting to play next song in guild {guild_id}")
                asyncio.create_task(play_next(self.ctx))
                await interaction.followup.send("▶ No song was paused. Attempting to play next song...", ephemeral=True)
        else:
            self.ctx.voice_client.resume()
            logger.info(f"Resumed playback in guild {guild_id}")
            # Use followup instead of response since we already deferred
            await interaction.followup.send("▶ Song resumed.", ephemeral=True)

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.red)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stops playback, clears the queue, and deletes the message."""
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        guild_id = self.ctx.guild.id
        logger.info(f"Stop button pressed in guild {guild_id}")
        
        if guild_id in queues:
            queue_length = len(queues[guild_id])
            queues[guild_id].clear()
            logger.info(f"Cleared queue with {queue_length} songs in guild {guild_id}")
        if self.ctx.voice_client:
            self.ctx.voice_client.stop()
            logger.info(f"Stopped playback in guild {guild_id}")

        if guild_id in current_song_message and current_song_message[guild_id]:
            try:
                await current_song_message[guild_id].delete()
                current_song_message[guild_id] = None
                logger.info(f"Deleted current song message in guild {guild_id}")
            except discord.NotFound:
                pass

        # Use followup instead of response since we already deferred
        await interaction.followup.send("⏹ Playback stopped and queue cleared.", ephemeral=True)


@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    ensure_cookies_file()
    logger.info("Checked cookies file")


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


@bot.command()
async def play(ctx, *, search: str):
    logger.info(f"Play command used by {ctx.author} in guild {ctx.guild.id} with search: {search}")
    
    if not ctx.voice_client:
        logger.info(f"Bot not in voice channel, joining for guild {ctx.guild.id}")
        await ctx.invoke(join)

    async with ctx.typing():
        if 'list=' in search:
            logger.info(f"Detected playlist URL: {search}")
            await handle_playlist(ctx, search)
        else:
            # Fix the queue before adding a new song
            logger.info(f"Fixing queue before adding new song in guild {ctx.guild.id}")
            await fix_queue(ctx.guild.id)
            
            if ctx.voice_client.is_playing():
                logger.info(f"Bot already playing, adding to queue: {search}")
                if ctx.guild.id not in queues:
                    queues[ctx.guild.id] = deque()
                queues[ctx.guild.id].append(search)
                
                # Different message based on whether it's a URL or search term
                if YTDLSource.is_url(search):
                    await ctx.send(f"🎵 Added to queue: {search}")
                else:
                    await ctx.send(f"🎵 Added to queue: '{search}' (will search YouTube)")
                
                # Emit queue update for dashboard
                emit_to_guild(ctx.guild.id, 'queue_update', {
                    'guild_id': str(ctx.guild.id),
                    'queue': queue_to_list(str(ctx.guild.id)),
                    'action': 'add'
                })
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
                    current_song[ctx.guild.id] = player

                    # If this was a search query, show what was found
                    if not YTDLSource.is_url(search):
                        await ctx.send(f"🎵 Found and playing: **{player.title}**")
                        
                    await update_music_message(ctx, player)
                    
                    # Emit song update for dashboard
                    emit_to_guild(ctx.guild.id, 'song_update', {
                        'guild_id': str(ctx.guild.id),
                        'current_song': song_to_dict(player),
                        'action': 'play'
                    })

                except YTDLError as e:
                    logger.error(f"YTDL error for search: {search} - {str(e)}")
                    # Extract the error message for a more user-friendly response
                    error_msg = str(e)
                    if "format" in error_msg.lower():
                        await ctx.send(f"❌ Error: The requested video format is unavailable. YouTube may have changed something. Trying to play the next song in the playlist...")
                        # Try to play the next song
                        asyncio.create_task(play_next(ctx))
                    elif "copyright" in error_msg.lower() or "removed" in error_msg.lower():
                        await ctx.send(f"❌ Error: The first video in the playlist may have been removed due to copyright issues. Trying to play the next song...")
                        # Try to play the next song
                        asyncio.create_task(play_next(ctx))
                    else:
                        await ctx.send(f"❌ Error: Could not play '{search}'. Please try a different song or URL.")
                except Exception as e:
                    logger.error(f"Error in play command: {e}")
                    logger.error(traceback.format_exc())
                    await ctx.send(f"❌ An unexpected error occurred: {str(e)}")
                    print(f"Error in play command: {e}")


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
    logger.info(f"play_next called for guild {guild_id}")
    
    # Check if we're already playing a song (lock mechanism)
    if guild_id in playing_locks and playing_locks[guild_id]:
        logger.warning(f"Already playing a song in guild {guild_id}, skipping play_next call")
        # Instead of recursively calling play_next, just return
        return
    
    # Set the lock
    playing_locks[guild_id] = True
    logger.info(f"Set playing lock for guild {guild_id}")
    
    try:
        # Fix the queue to remove any duplicates
        logger.info(f"Fixing queue in play_next for guild {guild_id}")
        await fix_queue(guild_id)
        
        # Store the current song's URL for duplicate check
        current_url = None
        if guild_id in current_song and current_song[guild_id]:
            current_url = current_song[guild_id].url
            logger.info(f"Current song URL for duplicate check: {current_url}")
        
        # Check if we have a preloaded song
        if guild_id in preloaded_songs and preloaded_songs[guild_id]:
            player = preloaded_songs[guild_id]
            preloaded_songs[guild_id] = None
            
            # Check if this preloaded song is the same as the current song
            if current_url and player.url == current_url:
                logger.warning(f"Preloaded song is the same as current song, skipping it for guild {guild_id}")
                player.cleanup()
                # Try the next song in the queue instead
                if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
                    logger.info(f"Moving to the next song in the queue for guild {guild_id}")
                    # Don't use the preloaded song and fall through to the next section
                else:
                    logger.info(f"No more songs in queue after skipping duplicate for guild {guild_id}")
                    if guild_id in current_song_message and current_song_message[guild_id]:
                        try:
                            embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                            await current_song_message[guild_id].edit(embed=embed, view=None)
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
                logger.info(f"Using preloaded song in guild {guild_id}: {player.title}")
                
                if not ctx.voice_client:
                    logger.info(f"Bot not in voice channel, joining for guild {guild_id}")
                    await ctx.invoke(join)
                try:
                    # Add a small delay to ensure buffer is filled
                    await asyncio.sleep(0.5)
                    
                    # Make sure we're not already playing something
                    if ctx.voice_client.is_playing():
                        logger.warning(f"Voice client is still playing in guild {guild_id}, stopping")
                        ctx.voice_client.stop()
                        await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                    
                    logger.info(f"Playing preloaded song in guild {guild_id}: {player.title}")
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                    current_song[guild_id] = player
                    
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
                    logger.error(f"Error playing preloaded song in guild {guild_id}: {e}")
                    logger.error(traceback.format_exc())
                    # If there's an error, try the next song
                    asyncio.create_task(play_next(ctx))
                
                # Start preloading the next song
                logger.info(f"Starting preload for next song in guild {guild_id}")
                asyncio.create_task(preload_next_song(ctx))
                return
        
        # Check if there are songs in the queue
        if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
            try:
                # Get the next URL from the queue
                next_url = queues[guild_id].popleft()
                logger.info(f"Next song in queue for guild {guild_id}: {next_url}")
                
                # Check if this is the same as the current song
                if current_url and next_url == current_url:
                    logger.warning(f"Next song in queue is the same as current song, skipping it for guild {guild_id}")
                    # Try the next song
                    return asyncio.create_task(play_next(ctx))
                
                # Create the player for the next song
                logger.info(f"Creating player for next song in guild {guild_id}")
                player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                
                # Make sure we're connected to a voice channel
                if not ctx.voice_client:
                    logger.info(f"Bot not in voice channel, joining for guild {guild_id}")
                    await ctx.invoke(join)
                
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                # Make sure we're not already playing something
                if ctx.voice_client.is_playing():
                    logger.warning(f"Voice client is still playing in guild {guild_id}, stopping")
                    ctx.voice_client.stop()
                    await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                
                # Play the next song
                logger.info(f"Playing next song in guild {guild_id}: {player.title}")
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                current_song[guild_id] = player
                
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
                logger.info(f"Starting preload for next song in guild {guild_id}")
                asyncio.create_task(preload_next_song(ctx))
                return
            except YTDLError as e:
                logger.error(f"YTDL error for song: {next_url}")
                logger.error(f"YTDL error details: {str(e)}")
                
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
                # If there's an error with this song, try the next one
                asyncio.create_task(play_next(ctx))
            except Exception as e:
                logger.error(f"Unexpected error playing song in guild {guild_id}: {e}")
                logger.error(traceback.format_exc())
                # If there's an error, try the next song
                asyncio.create_task(play_next(ctx))
        
        # No more songs in queue - only show the message if we were actually playing something
        # and the queue is truly empty
        if guild_id in current_song and current_song[guild_id]:
            # Double-check if the queue is really empty
            if not (guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0):
                logger.info(f"No more songs in queue for guild {guild_id}")
                if guild_id in current_song_message and current_song_message[guild_id]:
                    try:
                        embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                        await current_song_message[guild_id].edit(embed=embed, view=None)
                    except discord.NotFound:
                        pass
                # Clear the current song
                current_song[guild_id] = None
                
                # Emit socket events for queue end
                emit_to_guild(guild_id, 'song_update', {
                    'guild_id': str(guild_id),
                    'current_song': None,
                    'action': 'queue_end'
                })
    finally:
        # Release the lock
        playing_locks[guild_id] = False
        logger.info(f"Released playing lock for guild {guild_id}")


async def preload_next_song(ctx):
    """Preloads the next song in the queue to reduce latency when switching songs."""
    guild_id = ctx.guild.id
    logger.info(f"Preloading next song for guild {guild_id}")
    
    # Skip preloading if there's already a preloaded song
    if guild_id in preloaded_songs and preloaded_songs[guild_id]:
        logger.info(f"Already have a preloaded song for guild {guild_id}, skipping preload")
        return
    
    # Check if there are songs in the queue
    if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
        # Get the next URL without removing it from the queue
        next_url = queues[guild_id][0]
        
        # Check if this is the currently playing song
        if guild_id in current_song and current_song[guild_id] and current_song[guild_id].url == next_url:
            logger.warning(f"Next song in queue is the currently playing song, skipping preload for guild {guild_id}")
            # Remove the duplicate from the queue
            queues[guild_id].popleft()
            # Try preloading the next song if there is one
            if queues[guild_id] and len(queues[guild_id]) > 0:
                next_url = queues[guild_id][0]
            else:
                logger.info(f"No more songs in queue after removing duplicate for guild {guild_id}")
                return
        
        logger.info(f"Preloading song: {next_url} for guild {guild_id}")
        try:
            # Preload the song
            player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
            
            # Double check that this isn't the currently playing song
            if guild_id in current_song and current_song[guild_id] and current_song[guild_id].title == player.title:
                logger.warning(f"Preloaded song is the same as current song, discarding preloaded song for guild {guild_id}")
                player.cleanup()
                return
                
            preloaded_songs[guild_id] = player
            logger.info(f"Preloaded song: {player.title} for guild {guild_id}")
        except YTDLError:
            # If preloading fails, just continue
            logger.error(f"Failed to preload song: {next_url} for guild {guild_id}")
            pass
        except Exception as e:
            logger.error(f"Error preloading song in guild {guild_id}: {e}")
            logger.error(traceback.format_exc())
            pass

        except YTDLError as e:
            # If preloading fails, remove problematic URL from queue and try next one
            logger.error(f"Failed to preload song: {next_url} for guild {guild_id}: {str(e)}")
            
            # Remove this URL from the queue if it exists
            if guild_id in queues and queues[guild_id] and queues[guild_id][0] == next_url:
                logger.info(f"Removing problematic URL {next_url} from queue during preload")
                queues[guild_id].popleft()
                
                # Try preloading the next song if there is one
                if queues[guild_id] and len(queues[guild_id]) > 0:
                    asyncio.create_task(preload_next_song(ctx))
            pass


async def handle_playlist(ctx, url):
    """Handles the playlist and queues each song."""
    logger.info(f"Handling playlist: {url} for guild {ctx.guild.id}")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'extract_flat': 'in_playlist',
        'quiet': True,
        'ignoreerrors': True,
        'retries': 5,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'extractor_args': {'youtube': {'skip': ['dash', 'hls']}},
        'cookiefile': 'cookies.txt'
    }
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
    
    if not ctx.voice_client:
        logger.warning(f"Skip command used but bot not connected to voice in guild {ctx.guild.id}")
        await ctx.send("❌ I'm not connected to a voice channel.")
        return
    
    if not ctx.voice_client.is_playing():
        logger.warning(f"Skip command used but nothing is playing in guild {ctx.guild.id}")
        await ctx.send("❌ Nothing is playing right now.")
        return
    
    # Get the guild ID
    guild_id = ctx.guild.id
    
    # Check if we're already processing a skip (lock mechanism)
    if guild_id in playing_locks and playing_locks[guild_id]:
        logger.warning(f"Skip command used but already processing a skip in guild {guild_id}")
        await ctx.send("⏳ Please wait a moment before skipping again.")
        return
    
    # Set the lock
    playing_locks[guild_id] = True
    logger.info(f"Set playing lock for guild {guild_id}")
    
    try:
        # Fix the queue to remove any duplicates
        logger.info(f"Fixing queue before skip for guild {guild_id}")
        await fix_queue(guild_id)
        
        # Clean up the current song's FFmpeg process
        if guild_id in current_song and current_song[guild_id]:
            logger.info(f"Cleaning up current song before skip in guild {guild_id}")
            current_song[guild_id].cleanup()
        
        # Stop the current song
        logger.info(f"Stopping current song in guild {guild_id}")
        ctx.voice_client.stop()
        
        # Add a small delay to ensure the previous song is fully stopped
        await asyncio.sleep(0.5)
        
        # Check if there are songs in the queue
        if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
            # Get the next URL from the queue
            next_url = queues[guild_id].popleft()
            logger.info(f"Next song in queue for guild {guild_id}: {next_url}")
            
            try:
                # Create the player for the next song
                logger.info(f"Creating player for next song in guild {guild_id}")
                player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                # Make sure we're not already playing something
                if ctx.voice_client.is_playing():
                    logger.warning(f"Voice client is still playing after stop in guild {guild_id}, stopping again")
                    ctx.voice_client.stop()
                    await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                
                # Play the next song
                logger.info(f"Playing next song in guild {guild_id}: {player.title}")
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                current_song[guild_id] = player
                
                # Update the now playing message
                await update_music_message(ctx, player)
                
                await ctx.send(f"⏭ Skipped to: **{player.title}**")
                
                # Emit socket events to update dashboard
                emit_to_guild(guild_id, 'song_update', {
                    'guild_id': str(guild_id),
                    'current_song': song_to_dict(player),
                    'action': 'skip'
                })
                emit_to_guild(guild_id, 'queue_update', {
                    'guild_id': str(guild_id),
                    'queue': queue_to_list(str(guild_id)),
                    'action': 'update'
                })
                
            except Exception as e:
                logger.error(f"Error playing next song after skip in guild {guild_id}: {e}")
                logger.error(traceback.format_exc())
                await ctx.send("❌ Error playing the next song. Trying to continue...")
                # Try to play the next song in the queue
                asyncio.create_task(play_next(ctx))
        else:
            # No more songs in queue
            logger.info(f"No more songs in queue for guild {guild_id}")
            if guild_id in current_song_message and current_song_message[guild_id]:
                try:
                    embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                    await current_song_message[guild_id].edit(embed=embed, view=None)
                except discord.NotFound:
                    pass
            
            # Clear the current song
            current_song[guild_id] = None
            await ctx.send("⏭ Skipped. No more songs in the queue.", ephemeral=True)
            
            # Emit socket events to update dashboard that no song is playing
            emit_to_guild(guild_id, 'song_update', {
                'guild_id': str(guild_id),
                'current_song': None,
                'action': 'skip'
            })
    finally:
        # Release the lock
        playing_locks[guild_id] = False
        logger.info(f"Released playing lock for guild {guild_id}")

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

# Create a function to ensure the cookies file exists
def ensure_cookies_file():
    """Ensure the cookies file exists to prevent errors."""
    cookies_file = 'cookies.txt'
    try:
        if not os.path.exists(cookies_file):
            logger.info(f"Creating empty cookies file: {cookies_file}")
            with open(cookies_file, 'w') as f:
                # Write an empty cookies file
                f.write("# Netscape HTTP Cookie File\n")
        return True
    except Exception as e:
        logger.error(f"Error creating cookies file: {e}")
        return False

# Initialize Flask app
app = Flask(__name__, static_folder='dashboard/frontend/build')
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
    
    # Find the guild
    guild = None
    for g in bot.guilds:
        if str(g.id) == guild_id:
            guild = g
            break
    
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    
    # Get guild information
    guild_info = {
        'id': str(guild.id),
        'name': guild.name,
        'member_count': guild.member_count,
        'voice_connected': False,  # Default value
        'is_playing': guild_id in current_song and current_song[guild_id] is not None,
        'current_song': song_to_dict(current_song.get(guild_id)),
        'queue': queue_to_list(guild_id),
        'queue_length': len(queues.get(guild_id, []))
    }
    
    # Check if the bot is connected to a voice channel in this guild
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            guild_info['voice_connected'] = True
            guild_info['is_paused'] = vc.is_paused()
            break
    
    return jsonify(guild_info)

@app.route('/api/guild/<guild_id>/queue', methods=['GET'])
def get_queue(guild_id):
    """Get the current queue for a specific guild"""
    guild_id = str(guild_id)
    queue_list = queue_to_list(guild_id)
    
    return jsonify({
        'queue': queue_list,
        'length': len(queue_list)
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

@app.route('/api/guild/<guild_id>/pause', methods=['POST'])
def pause_playback(guild_id):
    """Pause the current playback"""
    guild_id = str(guild_id)
    
    # Find the guild's voice client
    voice_client = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            break
            
    if not voice_client:
        return jsonify({"error": "Bot not connected to a voice channel"}), 400
        
    if not voice_client.is_playing():
        return jsonify({"error": "Nothing is playing right now"}), 400
        
    if voice_client.is_paused():
        return jsonify({"error": "Playback is already paused"}), 400
        
    # Pause the playback
    voice_client.pause()
    
    # Emit socket event to update UI
    emit_to_guild(guild_id, 'song_update', {
        'guild_id': guild_id,
        'is_paused': True,
        'action': 'pause'
    })
    
    return jsonify({"success": True, "message": "Playback paused"})
    
@app.route('/api/guild/<guild_id>/resume', methods=['POST'])
def resume_playback(guild_id):
    """Resume the paused playback"""
    guild_id = str(guild_id)
    
    # Find the guild's voice client
    voice_client = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            break
            
    if not voice_client:
        return jsonify({"error": "Bot not connected to a voice channel"}), 400
        
    if not voice_client.is_paused():
        return jsonify({"error": "Playback is not paused"}), 400
        
    # Resume the playback
    voice_client.resume()
    
    # Emit socket event to update UI
    emit_to_guild(guild_id, 'song_update', {
        'guild_id': guild_id,
        'is_paused': False,
        'action': 'resume'
    })
    
    return jsonify({"success": True, "message": "Playback resumed"})
    
@app.route('/api/guild/<guild_id>/skip', methods=['POST'])
def skip_song(guild_id):
    """Skip the current song"""
    guild_id = str(guild_id)
    
    # Find the guild's voice client
    voice_client = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            break
            
    if not voice_client:
        return jsonify({"error": "Bot not connected to a voice channel"}), 400
        
    if not voice_client.is_playing() and not voice_client.is_paused():
        return jsonify({"error": "Nothing is playing right now"}), 400
    
    # Stop current playback to trigger the 'after' callback which will play the next song
    voice_client.stop()
    
    return jsonify({"success": True, "message": "Skipped to next song"})
    
@app.route('/api/guild/<guild_id>/stop', methods=['POST'])
def stop_playback(guild_id):
    """Stop playback and clear the queue"""
    guild_id = str(guild_id)
    
    # Find the guild's voice client
    voice_client = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            break
            
    if not voice_client:
        return jsonify({"error": "Bot not connected to a voice channel"}), 400
    
    # Clear the queue
    if guild_id in queues:
        queues[guild_id].clear()
    
    # Stop playback if something is playing
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()
        current_song[guild_id] = None
    
    # Emit socket events to update UI
    emit_to_guild(guild_id, 'song_update', {
        'guild_id': guild_id,
        'current_song': None,
        'is_playing': False,
        'action': 'stop'
    })
    
    emit_to_guild(guild_id, 'queue_update', {
        'guild_id': guild_id,
        'queue': [],
        'queue_length': 0,
        'action': 'clear'
    })
    
    return jsonify({"success": True, "message": "Playback stopped and queue cleared"})

# Add this function above the Flask routes section

def run_async(coro):
    """Run async code in a separate thread through a Future object"""
    loop = asyncio.get_event_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=60)  # Set a reasonable timeout
    except Exception as e:
        logger.error(f"Error in async task: {e}")
        logger.error(traceback.format_exc())
        return None

# Then modify the play_song function to use this helper


async def play_from_queue(guild, voice_channel):
    """Start playing songs from the queue for the given guild and voice channel."""
    logger.info(f"play_from_queue called for guild {guild.id}")
    
    guild_id = str(guild.id)
    
    # Create a context-like object for play_next
    class FakeContext:
        def __init__(self, guild, voice_client):
            self.guild = guild
            self.voice_client = voice_client
            self.channel = None  # Will be set later
            
        async def invoke(self, command):
            logger.info(f"Fake context invoking {command.__name__}")
            # Simplified handling - we assume the bot is already connected
            pass
            
        async def send(self, content=None, *, embed=None, ephemeral=False, view=None):
            logger.info(f"Fake context send: {content}")
            # No actual sending, just log
            pass
            
    # Wait a moment to ensure everything is ready
    await asyncio.sleep(0.5)
    
    # Find the voice client for this guild
    voice_client = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            break
    
    if not voice_client:
        logger.error(f"No voice client found for guild {guild_id} in play_from_queue")
        return
    
    # Create a fake context
    fake_ctx = FakeContext(guild, voice_client)
    fake_ctx.channel = voice_channel
    
    # Check if there are songs in the queue
    if guild_id in queues and queues[guild_id]:
        # Use play_next to start playing from the queue
        await play_next(fake_ctx)
    else:
        logger.warning(f"No songs in queue for guild {guild_id} in play_from_queue")


@app.route('/api/guild/<guild_id>/play', methods=['POST'])
def play_song(guild_id):
    """Add a song to the queue and play it if nothing is playing"""
    guild_id = str(guild_id)
    
    # Get URL from request body
    data = request.json
    if not data or 'url' not in data:
        return jsonify({"error": "URL parameter is required"}), 400
    
    search = data['url']  # This can be a URL or search term
    
    # Find the guild
    guild = None
    for g in bot.guilds:
        if str(g.id) == guild_id:
            guild = g
            break
            
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    
    # Initialize queue if it doesn't exist
    if guild_id not in queues:
        queues[guild_id] = deque()
    
    # Find voice client for this guild
    voice_client = None
    for vc in bot.voice_clients:
        if str(vc.guild.id) == guild_id:
            voice_client = vc
            break
    
    # Create a fake context for bot command simulation
    class FakeContext:
        def __init__(self, guild, voice_client=None):
            self.guild = guild
            self.voice_client = voice_client
            self.author = guild.me  # Use the bot as the author
            self.channel = None    # Will be set if available
            
        async def invoke(self, command):
            logger.info(f"Fake context invoking {command.__name__}")
            if command == join and voice_client is None:
                # For joining voice channels - find the most populated voice channel
                voice_channels = guild.voice_channels
                target_channel = None
                max_members = -1
                
                for vc in voice_channels:
                    if len(vc.members) > max_members:
                        max_members = len(vc.members)
                        target_channel = vc
                
                if target_channel:
                    logger.info(f"Joining voice channel {target_channel.name} in guild {guild.id}")
                    try:
                        self.voice_client = await target_channel.connect()
                        return True
                    except Exception as e:
                        logger.error(f"Error joining voice channel: {e}")
                        return False
            return False
            
        async def send(self, content=None, *, embed=None, ephemeral=False, view=None):
            logger.info(f"API would have sent: {content}")
            return None
            
        async def typing(self):
            class TypingContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    return None
            return TypingContextManager()
    
    # If the user submitted a playlist URL
    if 'list=' in search:
        # Call the playlist handler async function via the helper
        if voice_client:
            fake_ctx = FakeContext(guild, voice_client)
            run_async(handle_playlist(fake_ctx, search))
            return jsonify({"success": True, "message": "Processing playlist"})
        else:
            # Need to join a voice channel first
            return jsonify({"error": "Bot not in a voice channel. Please join a voice channel first."}), 400
            
    # For regular URLs or search terms, emulate the logic from the play command:
    
    # If we're already playing something, just add to queue
    if voice_client and voice_client.is_playing():
        logger.info(f"Bot already playing, adding to queue: {search}")
        queues[guild_id].append(search)
        
        # Emit queue update for dashboard
        emit_to_guild(guild_id, 'queue_update', {
            'guild_id': guild_id,
            'queue': queue_to_list(str(guild_id)),
            'action': 'add'
        })
        
        # Different message based on whether it's a URL or search term
        if YTDLSource.is_url(search):
            return jsonify({"success": True, "message": f"Added URL to queue: {search}"})
        else:
            return jsonify({"success": True, "message": f"Added to queue: '{search}' (will search YouTube)"})
    
    # If we're not playing anything, start playing
    fake_ctx = FakeContext(guild, voice_client)
    
    # If we're not connected to a voice channel yet
    if not voice_client:
        # Try to join a voice channel
        voice_channels = guild.voice_channels
        if not voice_channels:
            return jsonify({"error": "No voice channels available in this server"}), 400
            
        # Find the most populated voice channel
        target_channel = None
        max_members = -1
        
        for vc in voice_channels:
            if len(vc.members) > max_members:
                max_members = len(vc.members)
                target_channel = vc
                
        if not target_channel:
            return jsonify({"error": "No suitable voice channel found"}), 400
            
        try:
            logger.info(f"API joining voice channel {target_channel.name} in guild {guild.id}")
            voice_client = run_async(target_channel.connect())
            fake_ctx.voice_client = voice_client
        except Exception as e:
            logger.error(f"Error joining voice channel: {e}")
            return jsonify({"error": f"Failed to join voice channel: {str(e)}"}), 500
    
    # Now play the song
    try:
        # If it's a direct URL, try to play it directly
        if YTDLSource.is_url(search):
            # Add to queue and start playing with play_from_queue
            queues[guild_id].append(search)
            run_async(play_from_queue(guild, voice_client.channel))
            return jsonify({"success": True, "message": f"Playing URL: {search}"})
        else:
            # It's a search term, queue it and start playing
            queues[guild_id].append(search)
            run_async(play_from_queue(guild, voice_client.channel))
            return jsonify({"success": True, "message": f"Searching for and playing: '{search}'"})
    except Exception as e:
        logger.error(f"Error in play API endpoint: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

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

# Serve React frontend
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

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
    
    # Extract the required information
    return {
        'title': song.title if hasattr(song, 'title') else "Unknown",
        'url': song.url if hasattr(song, 'url') else None,
        'volume': song.volume * 100 if hasattr(song, 'volume') else 70  # Convert to percentage
    }

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
    if guild_id not in queues:
        return []
    
    queue_list = []
    for url in queues[guild_id]:
        queue_item = {
            'url': url,
            'thumbnail': get_thumbnail_url(url)
        }
        queue_list.append(queue_item)
    
    return queue_list

# Function to emit socket event to clients in a guild
def emit_to_guild(guild_id, event, data):
    """Emit an event to all clients in a specific guild"""
    guild_id = str(guild_id)
    if guild_id in connected_clients and connected_clients[guild_id]:
        logger.info(f"Emitting {event} to {len(connected_clients[guild_id])} clients in guild {guild_id}")
        
        # Make sure guild_id is included in the data
        if 'guild_id' not in data:
            data['guild_id'] = guild_id
        
        # Enhance data based on event type
        if event == 'song_update' and 'current_song' not in data:
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
            elif int(guild_id) in current_song:
                song_obj = current_song[int(guild_id)]
                # For consistency, update the current_song with string key
                current_song[guild_id] = song_obj
                
            is_playing = song_obj is not None
            is_paused = voice_client.is_paused() if voice_client else False
            
            # Get current song with more details
            current_song_data = None
            if song_obj is not None:
                current_song_data = {
                    'title': song_obj.title if hasattr(song_obj, 'title') else "Unknown",
                    'url': song_obj.url if hasattr(song_obj, 'url') else None,
                    'thumbnail': get_thumbnail_url(song_obj.url if hasattr(song_obj, 'url') else None),
                    'volume': song_obj.volume * 100 if hasattr(song_obj, 'volume') else 70
                }
                logger.info(f"Emitting current song: {current_song_data['title']}")
            
            data['current_song'] = current_song_data
            data['is_playing'] = is_playing
            data['is_paused'] = is_paused
            
        elif event == 'queue_update' and 'queue' not in data:
            # Get queue with more details
            queue_data = []
            if guild_id in queues:
                for url in queues[guild_id]:
                    queue_data.append({
                        'url': url,
                        'thumbnail': get_thumbnail_url(url),
                        'title': url  # For now, just use URL as title
                    })
            elif int(guild_id) in queues:
                for url in queues[int(guild_id)]:
                    queue_data.append({
                        'url': url,
                        'thumbnail': get_thumbnail_url(url),
                        'title': url  # For now, just use URL as title
                    })
                    
            data['queue'] = queue_data
            data['queue_length'] = len(queue_data)
            
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
        
        logger.info(f"Emit summary: {success_count} successful, {error_count} failed")
    else:
        logger.info(f"No clients connected for guild {guild_id}, skipping {event} event")

# Run the bot in a separate thread
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
        
        # Give the bot time to connect before starting the API
        time.sleep(5)
        
        # Start the API server
        port = int(os.environ.get("PORT", 5000))
        logger.info(f"Starting API server on port {port}")
        logger.info("WebSocket server will be available at ws://localhost:{port}/socket.io/")
        socketio.run(
            app, 
            host='0.0.0.0', 
            port=port, 
            debug=False, 
            allow_unsafe_werkzeug=True, 
            log_output=True,  # Log Socket.IO server output
            use_reloader=False  # Don't use reloader with threading
        )
    else:
        # Run in standalone bot mode
        bot.run(BOT_TOKEN)

