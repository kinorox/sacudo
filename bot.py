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

import aiohttp
import uuid

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

# Define the bot with improved voice client settings
bot = commands.Bot(command_prefix='!', intents=intents)

# Configure FFmpeg path for Windows
import shutil
ffmpeg_path = shutil.which('ffmpeg')
if not ffmpeg_path:
    ffmpeg_path = r'C:\ffmpeg\bin\ffmpeg.exe'
    
discord.FFmpegPCMAudio.executable = ffmpeg_path

# Add improved voice client settings
discord.voice_client.VoiceClient.warn_nacl = False

# Additional voice connection improvements for error 4006
try:
    # Set voice client parameters to help with 4006 errors
    original_voice_state_init = discord.VoiceProtocol.__init__
    
    def patched_voice_init(self, client, channel):
        """Patched voice protocol init with better settings"""
        original_voice_state_init(self, client, channel)
        # Force region to None to let Discord auto-select
        if hasattr(self, '_voice_state'):
            self._voice_state.region = None
    
    discord.VoiceProtocol.__init__ = patched_voice_init
    logger.info("Applied voice connection patches for 4006 error mitigation")
    
except Exception as e:
    logger.warning(f"Could not apply voice patches: {e}")

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
                # Create the audio source with proper FFmpeg options
                audio_source = discord.FFmpegPCMAudio(
                    filename,
                    before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                    options='-vn -ar 48000 -ac 2 -b:a 128k -f s16le'
                )
                source = cls(audio_source, data=data)
                source.volume = 0.8
                return source
            # For non-direct URLs, we'll need to extract the info again
            # but we can use the cache for displaying metadata
            logger.info(f"Cached URL is not direct. Re-extracting for {url}")
        
        # Use simplified options
        ydl_opts = default_youtube_options.copy()
        
        # Add specific options based on URL type
        if url.startswith('ytsearch:'):
            logger.info(f"Using search options")
            ydl_opts.update({
                'default_search': 'auto',
                'ignoreerrors': False,  # We want to catch errors for search queries
            })
        else:
            logger.info(f"Using direct URL options")
            ydl_opts.update({
                'ignoreerrors': True,
                'skip_download': True,  # Important: just streaming, not downloading
            })
        
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
        
        # Create the audio source with proper FFmpeg options
        audio_source = discord.FFmpegPCMAudio(
            filename,
            before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            options='-vn -ar 48000 -ac 2 -b:a 128k -f s16le'
        )
        
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


# 🎵 Handler functions for player controls 🎵
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
    
    # Fix the queue to remove duplicates before checking if we have a next song
    logger.info(f"Fixing queue in handle_skip_request for guild {guild_id_str}")
    await fix_queue(guild_id)
    
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
        else:
            next_song_title = "Next song in queue"
            logger.info(f"Next song URL: {next_url} (title not in cache)")
    
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
    
    # Clear the queue and current song BEFORE stopping to prevent race condition
    # This prevents play_next from trying to play the next song when stop() triggers the after callback
    
    # Clear queue using both integer and string keys to ensure it's cleared
    queue_cleared = False
    if guild_id in queues:
        queues[guild_id].clear()
        logger.info(f"Cleared queue for guild {guild_id_str} (int key)")
        queue_cleared = True
    if guild_id_str in queues:
        queues[guild_id_str].clear()
        logger.info(f"Cleared queue for guild {guild_id_str} (string key)")
        queue_cleared = True
    
    if not queue_cleared:
        logger.warning(f"No queue found to clear for guild {guild_id_str}")
    
    # Clear current song using both keys
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
    
    # Now stop the current song - this will trigger play_next but queue is already empty
    ctx.voice_client.stop()
    
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
    logger.info("Bot is ready!")

@bot.event
async def on_error(event, *args, **kwargs):
    """Global error handler for bot events"""
    logger.error(f"Error in event {event}: {args} {kwargs}")
    # Log the full traceback for debugging
    import traceback
    logger.error(f"Full traceback: {traceback.format_exc()}")

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates to clean up when the bot is disconnected."""
    try:
        guild_id = None
        
        # Check if the bot was disconnected
        if member.id == bot.user.id:
            if before.channel and not after.channel:
                guild_id = before.channel.guild.id
                logger.info(f"Bot disconnected from voice channel in guild {guild_id}")
                
                # Store the channel for potential reconnection
                last_voice_channel[guild_id] = before.channel
                
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
                
                # Try to reconnect and continue playback if there's a queue
                if guild_id in queues and queues[str(guild_id)] and len(queues[str(guild_id)]) > 0:
                    logger.info(f"Queue exists for guild {guild_id}, attempting reconnection")
                    
                    # Create a fake context for reconnection
                    guild = bot.get_guild(guild_id)
                    if guild:
                        # Get any text channel to create a fake context
                        text_channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
                        if text_channel:
                            fake_ctx = await bot.get_context(await text_channel.fetch_message(text_channel.last_message_id) if text_channel.last_message_id else None)
                            fake_ctx.guild = guild
                            # We can't directly assign to fake_ctx.voice_client, but we can work with the guild's voice client
                            
                            # Attempt reconnection after a short delay
                            asyncio.create_task(reconnect_and_resume(fake_ctx))
            elif after.channel and not before.channel:
                # Bot connected to a new channel
                guild_id = after.channel.guild.id
                last_voice_channel[guild_id] = after.channel
                logger.info(f"Bot connected to voice channel {after.channel.name} in guild {guild_id}")
    except Exception as e:
        logger.error(f"Error in voice state update handler: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")

# Add voice connection error handling
async def handle_voice_connection_error(guild_id, error, context="unknown"):
    """Handle voice connection errors with proper logging and recovery"""
    guild_id_str = str(guild_id)
    logger.error(f"Voice connection error in guild {guild_id_str} ({context}): {error}")
    
    # Check if it's a 4006 error (session ended)
    if hasattr(error, 'code') and error.code == 4006:
        logger.warning(f"Session ended error (4006) for guild {guild_id_str}, attempting recovery")
        
        # Wait a bit before attempting recovery
        await asyncio.sleep(5)
        
        # Try to clean up any existing connections
        guild = bot.get_guild(guild_id)
        if guild and guild.voice_client:
            try:
                await guild.voice_client.disconnect(force=True)
                logger.info(f"Force disconnected voice client for guild {guild_id_str} after 4006 error")
            except Exception as e:
                logger.error(f"Error during force disconnect for guild {guild_id_str}: {e}")
        
        # If there's a queue, try to reconnect
        if guild_id_str in queues and queues[guild_id_str] and len(queues[guild_id_str]) > 0:
            logger.info(f"Attempting to reconnect after 4006 error for guild {guild_id_str}")
            # Create a task to attempt reconnection
            asyncio.create_task(delayed_reconnect_attempt(guild_id))
    
    # For other errors, log and potentially attempt recovery
    elif hasattr(error, 'code'):
        logger.error(f"Discord error code {error.code} for guild {guild_id_str}: {error}")
    else:
        logger.error(f"Unknown voice connection error for guild {guild_id_str}: {error}")

async def delayed_reconnect_attempt(guild_id):
    """Attempt reconnection after a delay"""
    await asyncio.sleep(10)  # Wait 10 seconds before attempting reconnection
    
    try:
        guild = bot.get_guild(guild_id)
        if not guild:
            logger.error(f"Guild {guild_id} not found during reconnection attempt")
            return
        
        # Check if we have a last known channel
        if guild_id in last_voice_channel:
            channel = last_voice_channel[guild_id]
            logger.info(f"Attempting reconnection to {channel.name} for guild {guild_id}")
            
            try:
                voice_client = await channel.connect()
                if voice_client and voice_client.is_connected():
                    logger.info(f"Successfully reconnected to {channel.name} for guild {guild_id}")
                    # Resume playback if there's a queue
                    if str(guild_id) in queues and queues[str(guild_id)]:
                        await play_next_from_queue(guild_id)
                else:
                    logger.error(f"Reconnection failed for guild {guild_id}")
            except Exception as e:
                logger.error(f"Error during reconnection attempt for guild {guild_id}: {e}")
        else:
            logger.warning(f"No last known channel for guild {guild_id}, cannot attempt reconnection")
            
    except Exception as e:
        logger.error(f"Error in delayed reconnection attempt for guild {guild_id}: {e}")

async def play_next_from_queue(guild_id):
    """Play the next song from the queue for a specific guild"""
    try:
        guild_id_str = str(guild_id)
        if guild_id_str in queues and queues[guild_id_str]:
            # Get the next song from the queue
            next_song = queues[guild_id_str].popleft()
            logger.info(f"Playing next song from queue for guild {guild_id}: {next_song}")
            
            # Create a fake context for playing
            guild = bot.get_guild(guild_id)
            if guild:
                text_channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)

                if text_channel:
                    fake_ctx = await bot.get_context(await text_channel.fetch_message(text_channel.last_message_id) if text_channel.last_message_id else None)
                    fake_ctx.guild = guild
                    
                    # Try to play the song
                    await handle_play_request(fake_ctx, next_song)
                    
    except Exception as e:
        logger.error(f"Error playing next song from queue for guild {guild_id}: {e}")

# Add a global voice connection error handler
@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates to clean up when the bot is disconnected."""
    try:
        guild_id = None
        
        # Check if the bot was disconnected
        if member.id == bot.user.id:
            if before.channel and not after.channel:
                guild_id = before.channel.guild.id
                logger.info(f"Bot disconnected from voice channel in guild {guild_id}")
                
                # Store the channel for potential reconnection
                last_voice_channel[guild_id] = before.channel
                
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
                    playing_locks[guild_id] = None
                    
                # Try to reconnect and continue playback if there's a queue
                if guild_id in queues and queues[str(guild_id)] and len(queues[str(guild_id)]) > 0:
                    logger.info(f"Queue exists for guild {guild_id}, attempting reconnection")
                    
                    # Create a fake context for reconnection
                    guild = bot.get_guild(guild_id)
                    if guild:
                        # Get any text channel to create a fake context
                        text_channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
                        if text_channel:
                            fake_ctx = await bot.get_context(await text_channel.fetch_message(text_channel.last_message_id) if text_channel.last_message_id else None)
                            fake_ctx.guild = guild
                            # We can't directly assign to fake_ctx.voice_client, but we can work with the guild's voice client
                            
                            # Attempt reconnection after a short delay
                            asyncio.create_task(reconnect_and_resume(fake_ctx))
            elif after.channel and not before.channel:
                # Bot connected to a new channel
                guild_id = after.channel.guild.id
                last_voice_channel[guild_id] = after.channel
                logger.info(f"Bot connected to voice channel {after.channel.name} in guild {guild_id}")
    except Exception as e:
        logger.error(f"Error in voice state update handler: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")


@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates to clean up when the bot is disconnected."""
    try:
        guild_id = None
        
        # Check if the bot was disconnected
        if member.id == bot.user.id:
            if before.channel and not after.channel:
                guild_id = before.channel.guild.id
                logger.info(f"Bot disconnected from voice channel in guild {guild_id}")
                
                # Store the channel for potential reconnection
                last_voice_channel[guild_id] = before.channel
                
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
                
                # Try to reconnect and continue playback if there's a queue
                if guild_id in queues and queues[str(guild_id)] and len(queues[str(guild_id)]) > 0:
                    logger.info(f"Queue exists for guild {guild_id}, attempting reconnection")
                    
                    # Create a fake context for reconnection
                    guild = bot.get_guild(guild_id)
                    if guild:
                        # Get any text channel to create a fake context
                        text_channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
                        if text_channel:
                            fake_ctx = await bot.get_context(await text_channel.fetch_message(text_channel.last_message_id) if text_channel.last_message_id else None)
                            fake_ctx.guild = guild
                            # We can't directly assign to fake_ctx.voice_client, but we can work with the guild's voice client
                            
                            # Attempt reconnection after a short delay
                            asyncio.create_task(reconnect_and_resume(fake_ctx))
            elif after.channel and not before.channel:
                # Bot connected to a new channel
                guild_id = after.channel.guild.id
                last_voice_channel[guild_id] = after.channel
                logger.info(f"Bot connected to voice channel {after.channel.name} in guild {guild_id}")
    except Exception as e:
        logger.error(f"Error in voice state update handler: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")

# Add voice connection error handling
async def handle_voice_connection_error(guild_id, error, context="unknown"):
    """Handle voice connection errors with proper logging and recovery"""
    guild_id_str = str(guild_id)
    logger.error(f"Voice connection error in guild {guild_id_str} ({context}): {error}")
    
    # Check if it's a 4006 error (session ended)
    if hasattr(error, 'code') and error.code == 4006:
        logger.warning(f"Session ended error (4006) for guild {guild_id_str}, attempting recovery")
        
        # Wait a bit before attempting recovery
        await asyncio.sleep(5)
        
        # Try to clean up any existing connections
        guild = bot.get_guild(guild_id)
        if guild and guild.voice_client:
            try:
                await guild.voice_client.disconnect(force=True)
                logger.info(f"Force disconnected voice client for guild {guild_id_str} after 4006 error")
            except Exception as e:
                logger.error(f"Error during force disconnect for guild {guild_id_str}: {e}")
        
        # If there's a queue, try to reconnect
        if guild_id_str in queues and queues[guild_id_str] and len(queues[guild_id_str]) > 0:
            logger.info(f"Attempting to reconnect after 4006 error for guild {guild_id_str}")
            # Create a task to attempt reconnection
            asyncio.create_task(delayed_reconnect_attempt(guild_id))
    
    # For other errors, log and potentially attempt recovery
    elif hasattr(error, 'code'):
        logger.error(f"Discord error code {error.code} for guild {guild_id_str}: {error}")
    else:
        logger.error(f"Unknown voice connection error for guild {guild_id_str}: {error}")

async def delayed_reconnect_attempt(guild_id):
    """Attempt reconnection after a delay"""
    await asyncio.sleep(10)  # Wait 10 seconds before attempting reconnection
    
    try:
        guild = bot.get_guild(guild_id)
        if not guild:
            logger.error(f"Guild {guild_id} not found during reconnection attempt")
            return
        
        # Check if we have a last known channel
        if guild_id in last_voice_channel:
            channel = last_voice_channel[guild_id]
            logger.info(f"Attempting reconnection to {channel.name} for guild {guild_id}")
            
            try:
                voice_client = await channel.connect()
                if voice_client and voice_client.is_connected():
                    logger.info(f"Successfully reconnected to {channel.name} for guild {guild_id}")
                    # Resume playback if there's a queue
                    if str(guild_id) in queues and queues[str(guild_id)]:
                        await play_next_from_queue(guild_id)
                else:
                    logger.error(f"Reconnection failed for guild {guild_id}")
            except Exception as e:
                logger.error(f"Error during reconnection attempt for guild {guild_id}: {e}")
        else:
            logger.warning(f"No last known channel for guild {guild_id}, cannot attempt reconnection")
            
    except Exception as e:
        logger.error(f"Error in delayed reconnection attempt for guild {guild_id}: {e}")

async def play_next_from_queue(guild_id):
    """Play the next song from the queue for a specific guild"""
    try:
        guild_id_str = str(guild_id)
        if guild_id_str in queues and queues[guild_id_str]:
            # Get the next song from the queue
            next_song = queues[guild_id_str].popleft()
            logger.info(f"Playing next song from queue for guild {guild_id}: {next_song}")
            
            # Create a fake context for playing
            guild = bot.get_guild(guild_id)
            if guild:
                text_channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
                if text_channel:
                    fake_ctx = await bot.get_context(await text_channel.fetch_message(text_channel.last_message_id) if text_channel.last_message_id else None)
                    fake_ctx.guild = guild
                    
                    # Try to play the song
                    await handle_play_request(fake_ctx, next_song)
                    
    except Exception as e:
        logger.error(f"Error playing next song from queue for guild {guild_id}: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates to clean up when the bot is disconnected."""
    try:
        guild_id = None
        
        # Check if the bot was disconnected
        if member.id == bot.user.id:
            if before.channel and not after.channel:
                guild_id = before.channel.guild.id
                logger.info(f"Bot disconnected from voice channel in guild {guild_id}")
                
                # Store the channel for potential reconnection
                last_voice_channel[guild_id] = before.channel
                
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
                
                # Try to reconnect and continue playback if there's a queue
                if guild_id in queues and queues[str(guild_id)] and len(queues[str(guild_id)]) > 0:
                    logger.info(f"Queue exists for guild {guild_id}, attempting reconnection")
                    
                    # Create a fake context for reconnection
                    guild = bot.get_guild(guild_id)
                    if guild:
                        # Get any text channel to create a fake context
                        text_channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
                        if text_channel:
                            fake_ctx = await bot.get_context(await text_channel.fetch_message(text_channel.last_message_id) if text_channel.last_message_id else None)
                            fake_ctx.guild = guild
                            # We can't directly assign to fake_ctx.voice_client, but we can work with the guild's voice client
                            
                            # Attempt reconnection after a short delay
                            asyncio.create_task(reconnect_and_resume(fake_ctx))
            elif after.channel and not before.channel:
                # Bot connected to a new channel
                guild_id = after.channel.guild.id
                last_voice_channel[guild_id] = after.channel
                logger.info(f"Bot connected to voice channel {after.channel.name} in guild {guild_id}")
    except Exception as e:
        logger.error(f"Error in voice state update handler: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")

# Simplified YouTube options
default_youtube_options = {
    'format': 'bestaudio/best',
    'nocheckcertificate': True,
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
}

@bot.command()
async def join(ctx):
    if not ctx.message.author.voice:
        logger.warning(f"Join command used by {ctx.author} but not in a voice channel")
        await ctx.send("You are not connected to a voice channel.")
        return
    else:
        channel = ctx.message.author.voice.channel
    
    logger.info(f"Joining voice channel {channel.name} in guild {ctx.guild.id}")
    
    # Add retry logic for voice connection with proper error handling
    max_retries = 5  # Increased from 3 to 5
    for attempt in range(max_retries):
        try:
            # Use a timeout for voice connection to prevent hanging with specific parameters
            voice_client = await asyncio.wait_for(
                channel.connect(timeout=30, reconnect=False, cls=discord.VoiceClient), 
                timeout=15.0
            )
            logger.info(f"Successfully connected to voice channel {channel.name} in guild {ctx.guild.id}")
            break
        except IndexError as e:
            if "list index out of range" in str(e) and attempt < max_retries - 1:
                logger.warning(f"Voice connection attempt {attempt + 1} failed with IndexError (empty modes array), retrying...")
                await asyncio.sleep(2)  # Increased delay
                continue
            else:
                logger.error(f"Voice connection failed after {max_retries} attempts: {e}")
                await ctx.send("Failed to connect to voice channel. Discord voice servers may be experiencing issues. Please try again later.")
                return
        except discord.errors.ConnectionClosed as e:
            # Handle specific Discord voice connection errors using the new error handler
            await handle_voice_connection_error(ctx.guild.id, e, f"join_attempt_{attempt + 1}")
            
            error_code = getattr(e, 'code', None)
            if error_code == 4006:
                logger.warning(f"Voice connection attempt {attempt + 1} failed with error 4006 (session ended), retrying...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)  # Longer delay for 4006 errors
                    continue
                else:
                    logger.error(f"Voice connection failed after {max_retries} attempts due to error 4006")
                    await ctx.send("Failed to connect to voice channel due to session issues. Please try again in a few moments.")
                    return
            elif error_code == 1000:
                logger.warning(f"Voice connection attempt {attempt + 1} failed with error 1000 (normal closure), retrying...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)  # Delay for 1000 errors
                    continue
                else:
                    logger.error(f"Voice connection failed after {max_retries} attempts due to error 1000")
                    await ctx.send("Failed to connect to voice channel due to normal closure. Please try again in a few moments.")
                    return
            else:
                logger.error(f"Discord connection closed during voice connection attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                else:
                    await ctx.send("Failed to connect to voice channel due to Discord connection issues. Please try again later.")
                    return
        except discord.errors.ClientException as e:
            if "Already connected to a voice channel" in str(e):
                logger.info(f"Already connected to voice channel in guild {ctx.guild.id}")
                break
            else:
                logger.error(f"Client exception during voice connection attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                else:
                    await ctx.send(f"Failed to connect to voice channel: {e}")
                    return
        except asyncio.TimeoutError:
            logger.error(f"Voice connection attempt {attempt + 1} timed out")
            if attempt < max_retries - 1:
                await asyncio.sleep(3)
                continue
            else:
                await ctx.send("Failed to connect to voice channel: Connection timed out. Please try again.")
                return
        except Exception as e:
            logger.error(f"Unexpected error during voice connection attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                continue
            else:
                await ctx.send(f"Failed to connect to voice channel: {e}")
                return
    
    # Store the channel for reconnection purposes
    last_voice_channel[ctx.guild.id] = channel


async def fix_queue(guild_id):
    """Fixes the queue by removing duplicates and ensuring proper order."""
    guild_id_str = str(guild_id)
    logger.info(f"Fixing queue for guild {guild_id} (string: {guild_id_str})")
    
    # Check for queue under both string and integer keys, normalize to string
    if guild_id_str not in queues and guild_id in queues:
        logger.info(f"Moving queue from integer key {guild_id} to string key {guild_id_str}")
        queues[guild_id_str] = queues[guild_id]
        del queues[guild_id]
    
    if guild_id_str not in queues:
        logger.info(f"Creating new queue for guild {guild_id_str}")
        queues[guild_id_str] = deque()
        return 0
    
    # Log the original queue
    original_queue = list(queues[guild_id_str])
    logger.info(f"Original queue for guild {guild_id_str}: {original_queue}")
    
    # Get the current song URL if there is one
    current_song_url = None
    
    if guild_id_str in current_song and current_song[guild_id_str]:
        current_song_url = current_song[guild_id_str].url
        logger.info(f"Current song URL for queue cleaning (string key): {current_song_url}")
    elif guild_id in current_song and current_song[guild_id]:
        current_song_url = current_song[guild_id].url
        logger.info(f"Current song URL for queue cleaning (int key): {current_song_url}")
    
    # Create a new queue with only unique URLs
    new_queue = deque()
    unique_urls = set()
    
    # Check if the queue is already empty
    if not queues[guild_id_str]:
        logger.info(f"Queue is already empty for guild {guild_id_str}")
        return 0
    
    # Add only unique URLs to the new queue
    for i, url in enumerate(queues[guild_id_str]):
        # Skip URLs that match the currently playing song, but only if it's not the first item in queue
        # When skipping, we want to preserve the next song in the queue
        if current_song_url and url == current_song_url and i > 0:
            logger.warning(f"Found currently playing song in queue at position {i}, removing it: {url}")
            continue
            
        if url not in unique_urls:
            unique_urls.add(url)
            new_queue.append(url)
        else:
            logger.warning(f"Found duplicate URL in queue at position {i}, removing it: {url}")
    
    # Replace the old queue with the new one
    queues[guild_id_str] = new_queue
    
    # Log the new queue
    new_queue_list = list(new_queue)
    logger.info(f"New queue for guild {guild_id_str}: {new_queue_list}")
    
    # Log removed duplicates
    removed_count = len(original_queue) - len(new_queue_list)
    if removed_count > 0:
        logger.info(f"Removed {removed_count} duplicate songs from queue in guild {guild_id_str}")
    
    return len(queues[guild_id_str])


async def handle_play_request(ctx, search: str):
    """Core functionality for playing a song, used by both bot commands and API"""
    guild_id = ctx.guild.id
    guild_id_str = str(guild_id)
    logger.info(f"Play functionality called for guild {guild_id_str} with search: {search}")
    logger.info(f"Current current_song keys before play: {list(current_song.keys())}")
    
    if guild_id_str in current_song:
        logger.info(f"handle_play_request: Guild {guild_id_str} exists in current_song dictionary before play")
        if current_song[guild_id_str]:
            logger.info(f"handle_play_request: Current song before play for guild {guild_id_str}: {current_song[guild_id_str].title if hasattr(current_song[guild_id_str], 'title') else 'Unknown'}")
        else:
            logger.info(f"handle_play_request: Current song is None for guild {guild_id_str} before play")
    else:
        logger.info(f"handle_play_request: Guild {guild_id_str} not in current_song dictionary before play")
    
    if not ctx.voice_client:
        logger.info(f"Bot not in voice channel, joining for guild {guild_id_str}")
        await ctx.invoke(join)

    if 'list=' in search:
        logger.info(f"Detected playlist URL: {search}")
        return await handle_playlist(ctx, search)
    else:
        # Fix the queue before adding a new song
        logger.info(f"Fixing queue before adding new song in guild {guild_id_str}")
        await fix_queue(guild_id)
        
        if ctx.voice_client.is_playing():
            logger.info(f"Bot already playing, adding to queue: {search}")
            # Initialize queue if it doesn't exist
            if guild_id_str not in queues:
                queues[guild_id_str] = deque()
                logger.info(f"Created new queue for guild {guild_id_str}")
            
            # Check if it's a search query that's not a URL
            if not YTDLSource.is_url(search):
                # First, try to extract info without downloading to get the title
                logger.info(f"Extracting info for search query: {search}")
                try:
                    # This will be a background task so we don't block the main thread
                    # Create a task to add song to cache for better title display later
                    asyncio.create_task(extract_song_info_for_queue(search, guild_id))
                except Exception as e:
                    logger.error(f"Error extracting info for search: {search} - {str(e)}")
            
            # Add to queue
            queues[guild_id_str].append(search)
            
            # Emit queue update for dashboard
            emit_to_guild(guild_id, 'queue_update', {
                'guild_id': guild_id_str,
                'queue': queue_to_list(guild_id_str),
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
                
                # Verify connection before playing - handle Discord API state issues
                voice_client_ready = False
                if ctx.voice_client and ctx.voice_client.is_connected():
                    voice_client_ready = True
                elif ctx.guild.voice_client and ctx.guild.voice_client.is_connected():
                    # Discord API state issue workaround
                    logger.info(f"Using guild voice client as fallback for guild {guild_id_str}")
                    # We can't directly assign to ctx.voice_client, but we can work with the guild's voice client
                    # The context will automatically use the guild's voice client
                    voice_client_ready = True
                
                if not voice_client_ready:
                    logger.warning(f"Voice client not connected before playing in guild {guild_id_str}")
                    
                    # Try force disconnect and clean reconnection due to Discord API issues
                    logger.info(f"Attempting force disconnect and clean reconnection for guild {guild_id_str}")
                    
                    # Force disconnect any existing connections
                    for client in [ctx.voice_client, ctx.guild.voice_client]:
                        if client:
                            try:
                                await client.disconnect(force=True)
                                logger.info(f"Force disconnected voice client for guild {guild_id_str}")
                            except:
                                pass
                    
                    # Clear the context - we can't directly assign to ctx.voice_client
                    # Instead, we'll work with the guild's voice client directly
                    
                    # Wait a moment for Discord to process the disconnect
                    await asyncio.sleep(2)
                    
                    # Try to reconnect using the join function directly with enhanced error handling
                    if guild_id in last_voice_channel:
                        channel = last_voice_channel[guild_id]
                        max_reconnect_attempts = 3
                        for reconnect_attempt in range(max_reconnect_attempts):
                            try:
                                logger.info(f"Attempting clean reconnection to {channel.name} for guild {guild_id_str} (attempt {reconnect_attempt + 1})")
                                voice_client = await asyncio.wait_for(
                                    channel.connect(timeout=30, reconnect=False, cls=discord.VoiceClient), 
                                    timeout=15.0
                                )
                                if voice_client and voice_client.is_connected():
                                    # We can't directly assign to ctx.voice_client, but we can work with the guild's voice client
                                    # The context will automatically use the guild's voice client
                                    logger.info(f"Successfully reconnected after force disconnect for guild {guild_id_str}")
                                    voice_client_ready = True
                                    break
                                else:
                                    logger.error(f"Clean reconnection failed for guild {guild_id_str}")
                            except discord.errors.ConnectionClosed as e:
                                error_code = getattr(e, 'code', None)
                                if error_code == 4006:
                                    logger.warning(f"Reconnection attempt {reconnect_attempt + 1} failed with error 4006 for guild {guild_id_str}")
                                    if reconnect_attempt < max_reconnect_attempts - 1:
                                        await asyncio.sleep(3)  # Longer delay for 4006 errors
                                        continue
                                    else:
                                        logger.error(f"Failed to reconnect after {max_reconnect_attempts} attempts due to error 4006")
                                        break
                                else:
                                    logger.error(f"Discord connection closed during reconnection attempt {reconnect_attempt + 1}: {e}")
                                    if reconnect_attempt < max_reconnect_attempts - 1:
                                        await asyncio.sleep(2)
                                        continue
                                    else:
                                        break
                            except Exception as e:
                                logger.error(f"Clean reconnection error for guild {guild_id_str} (attempt {reconnect_attempt + 1}): {e}")
                                if reconnect_attempt < max_reconnect_attempts - 1:
                                    await asyncio.sleep(2)
                                    continue
                                else:
                                    break
                    
                    if not voice_client_ready:
                        logger.error(f"Cannot establish voice connection for guild {guild_id_str}")
                        return f"Error: Cannot establish voice connection due to Discord API issues. Try using the !join command first."
                
                logger.info(f"Playing: {player.title}")
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop) if e is None else logger.error(f"Audio playback error: {e}"))
                
                # Set the current song and log it
                current_song[guild_id_str] = player
                logger.info(f"Set current_song[{guild_id_str}] = {player.title}")
                logger.info(f"Current current_song keys after setting: {list(current_song.keys())}")
                
                # Now check if it was set correctly
                if guild_id_str in current_song:
                    if current_song[guild_id_str]:
                        logger.info(f"Verification: current_song[{guild_id_str}] successfully set to {current_song[guild_id_str].title if hasattr(current_song[guild_id_str], 'title') else 'Unknown'}")
                    else:
                        logger.warning(f"Verification failed: current_song[{guild_id_str}] is None right after setting it!")
                else:
                    logger.warning(f"Verification failed: guild {guild_id_str} not in current_song dictionary right after setting it!")

                # Update music message
                await update_music_message(ctx, player)
                
                # Emit song update for dashboard
                emit_to_guild(guild_id, 'song_update', {
                    'guild_id': guild_id_str,
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
        # Use simplified options
        ydl_opts = default_youtube_options.copy()
        ydl_opts.update({
            'default_search': 'auto',
            'skip_download': True,
        })
        
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
        # Store the current song's URL for duplicate check
        current_url = None
        if guild_id_str in current_song and current_song[guild_id_str]:
            current_url = current_song[guild_id_str].url
            logger.info(f"Current song URL for duplicate check: {current_url}")
            
        # Now fix the queue to remove any duplicates
        # We do this after saving the current URL to avoid removing the current song before we can check
        logger.info(f"Fixing queue in play_next for guild {guild_id_str}")
        queue_length = await fix_queue(guild_id)
        logger.info(f"Queue length after fixing: {queue_length}")
            
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
                    # Also ensure we're using consistent keys for current_song_message
                    if guild_id_str in current_song_message and current_song_message[guild_id_str]:
                        try:
                            embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                            await current_song_message[guild_id_str].edit(embed=embed, view=None)
                        except discord.NotFound:
                            pass
                    elif guild_id in current_song_message and current_song_message[guild_id]:
                        try:
                            embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                            await current_song_message[guild_id].edit(embed=embed, view=None)
                            # Move to string key for consistency
                            current_song_message[guild_id_str] = current_song_message[guild_id]
                            del current_song_message[guild_id]
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
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop) if e is None else logger.error(f"Audio playback error: {e}"))
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
        
        # Check if there are songs in the queue - check both string and integer guild IDs
        queue_to_use = None
        if guild_id_str in queues and queues[guild_id_str] and len(queues[guild_id_str]) > 0:
            queue_to_use = queues[guild_id_str]
            logger.info(f"Found queue using string guild_id {guild_id_str}")
        elif guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
            # Move queue from integer key to string key for consistency
            queues[guild_id_str] = queues[guild_id]
            del queues[guild_id]
            queue_to_use = queues[guild_id_str]
            logger.info(f"Found queue using integer guild_id {guild_id}, moved to string key {guild_id_str}")
        
        if queue_to_use:
            try:
                # Log the queue before we pop from it
                logger.info(f"Queue before popleft: {list(queues[guild_id_str])}")
                
                # Get the next URL from the queue
                next_url = queues[guild_id_str].popleft()
                logger.info(f"Next song in queue for guild {guild_id_str}: {next_url}")
                
                # Check if this is the same as the current song
                if current_url and next_url == current_url:
                    logger.warning(f"Next song in queue is the same as current song, skipping it for guild {guild_id_str}")
                    # Try the next song
                    return asyncio.create_task(play_next(ctx))
                
                # Create the player for the next song
                logger.info(f"Creating player for next song in guild {guild_id_str}")
                player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                
                # Ensure we have a stable voice connection
                if not await ensure_voice_connection(ctx):
                    logger.error(f"Failed to establish voice connection for guild {guild_id_str}")
                    # Try the next song if connection fails
                    asyncio.create_task(play_next(ctx))
                    return
                
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                # Make sure we're not already playing something
                if ctx.voice_client.is_playing():
                    logger.warning(f"Voice client is still playing in guild {guild_id_str}, stopping")
                    ctx.voice_client.stop()
                    await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                
                # Verify connection is still stable before playing
                if not ctx.voice_client or not ctx.voice_client.is_connected():
                    logger.warning(f"Voice client disconnected, attempting reconnection for guild {guild_id_str}")
                    if not await ensure_voice_connection(ctx):
                        logger.error(f"Cannot establish voice connection for next song in guild {guild_id_str}")
                        # Try again later
                        asyncio.create_task(play_next(ctx))
                        return
                
                # Play the next song
                logger.info(f"Playing next song in guild {guild_id_str}: {player.title}")
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop) if e is None else logger.error(f"Audio playback error: {e}"))
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
                if guild_id_str in queues and queues[guild_id_str]:
                    logger.info(f"There are {len(queues[guild_id_str])} more songs in the queue, trying next one")
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
            logger.info(f"No queue found for guild {guild_id_str}")
            logger.info(f"Available queue keys: {list(queues.keys())}")
            # Double-check both string and integer keys for debugging
            if guild_id_str in queues:
                logger.info(f"String key {guild_id_str} exists with {len(queues[guild_id_str])} items")
            if guild_id in queues:
                logger.info(f"Integer key {guild_id} exists with {len(queues[guild_id])} items")
            logger.info(f"No more songs in queue for guild {guild_id_str}")
            # Check for both string and integer keys for current_song_message
            if guild_id_str in current_song_message and current_song_message[guild_id_str]:
                try:
                    embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                    await current_song_message[guild_id_str].edit(embed=embed, view=None)
                except discord.NotFound:
                    pass
            elif guild_id in current_song_message and current_song_message[guild_id]:
                try:
                    embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
                    await current_song_message[guild_id].edit(embed=embed, view=None)
                    # Move to string key for consistency
                    current_song_message[guild_id_str] = current_song_message[guild_id]
                    del current_song_message[guild_id]
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
        if guild_id_str in current_song:
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
    if guild_id_str in queues and queues[guild_id_str] and len(queues[guild_id_str]) > 0:
        # Get the next URL without removing it from the queue
        next_url = queues[guild_id_str][0]
        
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
            queues[guild_id_str].popleft()
            # Try preloading the next song if there is one
            if queues[guild_id_str] and len(queues[guild_id_str]) > 0:
                next_url = queues[guild_id_str][0]
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
    guild_id = ctx.guild.id
    guild_id_str = str(guild_id)
    
    # Ensure bot is connected to voice channel
    if not ctx.voice_client:
        logger.info(f"Bot not in voice channel, joining for playlist in guild {guild_id_str}")
        await ctx.invoke(join)
    
    # Use specific options for playlist extraction
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',  # Extract playlist entries without downloading videos
        'ignoreerrors': True,
        'nocheckcertificate': True,
        'skip_download': True,
        'playlistend': 50,  # Limit playlist size for performance
        # Don't include 'noplaylist' option here since we want to extract playlists
    }
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Extracting playlist info for: {url}")
            info_dict = ydl.extract_info(url, download=False)
            
            logger.info(f"Playlist extraction result: {type(info_dict)}")
            if info_dict:
                logger.info(f"Info dict keys: {list(info_dict.keys())}")
                logger.info(f"Playlist title: {info_dict.get('title', 'Unknown')}")
                logger.info(f"Has entries: {'entries' in info_dict}")
                if 'entries' in info_dict:
                    logger.info(f"Number of entries: {len(info_dict['entries']) if info_dict['entries'] else 0}")
            
            entries = info_dict.get('entries') if info_dict else None
            
            # Handle case where yt-dlp returns a redirect URL instead of entries
            if not entries and info_dict and info_dict.get('_type') == 'url':
                redirect_url = info_dict.get('url')
                logger.info(f"Got redirect URL, trying to extract: {redirect_url}")
                if redirect_url and redirect_url != url:
                    try:
                        # Try extracting from the redirect URL with timeout
                        info_dict = ydl.extract_info(redirect_url, download=False)
                        logger.info(f"Redirect extraction result: {type(info_dict)}")
                        if info_dict:
                            logger.info(f"Redirect info dict keys: {list(info_dict.keys())}")
                            entries = info_dict.get('entries')
                            if entries:
                                logger.info(f"Found {len(entries)} entries from redirect URL")
                    except Exception as redirect_error:
                        logger.error(f"Error processing redirect URL {redirect_url}: {redirect_error}")
                        # Continue with original processing if redirect fails
            
            if not entries:
                logger.warning(f"No entries found in playlist: {url}")
                logger.warning(f"Final info_dict: {info_dict}")
                await ctx.send("❌ No valid songs found in the playlist.")
                return
    except Exception as e:
        logger.error(f"Error extracting playlist info: {e}")
        logger.error(traceback.format_exc())
        await ctx.send(f"❌ Error processing playlist: {str(e)}")
        return

    # Use string guild ID for consistency
    if guild_id_str not in queues:
        queues[guild_id_str] = deque()
        logger.info(f"Created new queue for guild {guild_id_str}")
    
    # Create a set to track unique URLs to prevent duplicates
    unique_urls = set()
    added_count = 0
    
    # First, add all unique URLs to the queue (with progress feedback for large playlists)
    total_entries = len(entries)
    processed = 0
    
    for entry in entries:
        if entry and 'url' in entry and entry['url'] not in unique_urls:
            unique_urls.add(entry['url'])
            queues[guild_id_str].append(entry['url'])
            added_count += 1
        
        processed += 1
        # Send progress update for large playlists
        if total_entries > 20 and processed % 10 == 0:
            logger.info(f"Processing playlist: {processed}/{total_entries} songs")
            try:
                await ctx.send(f"📊 Processing playlist: {processed}/{total_entries} songs...", delete_after=5)
            except:
                pass  # Don't fail if we can't send progress update
    
    if added_count == 0:
        logger.warning(f"No unique songs found to add from playlist {url} for guild {guild_id_str}")
        await ctx.send("❌ No valid songs found in the playlist.")
        return
        
    logger.info(f"Added {added_count} unique songs from playlist to queue for guild {guild_id_str}")
    
    # Fix the queue to ensure no duplicates
    logger.info(f"Fixing queue after adding playlist for guild {guild_id_str}")
    await fix_queue(guild_id)
    
    # Emit queue update for dashboard
    emit_to_guild(guild_id, 'queue_update', {
        'guild_id': guild_id_str,
        'queue': queue_to_list(guild_id_str),
        'action': 'add_playlist'
    })
    
    # If the bot is not already playing, start playing the first song
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        # Ensure queue has items before trying to play
        if queues.get(guild_id_str):
            first_url = queues[guild_id_str].popleft()
            logger.info(f"Playing first song from playlist: {first_url} for guild {guild_id_str}")
            try:
                player = await YTDLSource.from_url(first_url, loop=bot.loop, stream=True)
                
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                logger.info(f"Playing first song from playlist: {player.title} for guild {guild_id_str}")
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop) if e is None else logger.error(f"Audio playback error: {e}"))
                current_song[guild_id_str] = player
                
                await update_music_message(ctx, player)
                await ctx.send(f"🎵 Playing playlist. Added {len(queues[guild_id_str])} songs to the queue.")
                
                # Emit song update for dashboard
                emit_to_guild(guild_id, 'song_update', {
                    'guild_id': guild_id_str,
                    'current_song': song_to_dict(player),
                    'action': 'play'
                })
                
            except Exception as e:
                logger.error(f"Error playing first song from playlist in guild {guild_id_str}: {e}")
                logger.error(traceback.format_exc())
                await ctx.send(f"❌ Error playing the first song from the playlist: {str(e)}")
                # Try to play the next song if possible
                asyncio.create_task(play_next(ctx))
        else:
            logger.warning(f"Queue for guild {guild_id_str} is empty after trying to play the first song.")
            await ctx.send("❌ Queue is empty after processing the playlist.")
    else:
        # If already playing, just add to queue
        logger.info(f"Bot already playing, added {added_count} songs from playlist to queue for guild {guild_id_str}")
        await ctx.send(f"🎵 Added {added_count} songs from the playlist to the queue.")

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
    
    # Check if bot should try to play next song (removed problematic issues_found check)
    if ctx.voice_client and not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        # If voice client exists but nothing is playing and not paused, try to play next song
        logger.info(f"Voice client not playing, attempting to play next song in guild {guild_id}")
        asyncio.create_task(play_next(ctx))

# Store the last connected voice channel per guild for reconnection
last_voice_channel = {}

async def ensure_voice_connection(ctx, max_retries=3):
    """Ensure we have a stable voice connection, attempt reconnection if needed."""
    guild_id = ctx.guild.id
    
    # First, check if we already have a working connection
    if ctx.voice_client and ctx.voice_client.is_connected():
        logger.info(f"Voice client already connected for guild {guild_id}")
        return True
    
    # Check if guild has a voice client we can use
    if ctx.guild.voice_client and ctx.guild.voice_client.is_connected():
        logger.info(f"Found existing guild voice client for guild {guild_id}, context will use it automatically")
        return True
    
    # Try to reconnect to the last known channel
    if guild_id not in last_voice_channel:
        logger.warning(f"No previous voice channel stored for guild {guild_id}")
        return False
    
    channel = last_voice_channel[guild_id]
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Attempting to reconnect to voice channel {channel.name} in guild {guild_id} (attempt {attempt + 1})")
            
            # Only disconnect if we have a non-working connection
            if ctx.voice_client and not ctx.voice_client.is_connected():
                try:
                    await ctx.voice_client.disconnect()
                    # We can't directly assign to ctx.voice_client, but the disconnect will clear the connection
                except:
                    pass
            
            # Only disconnect guild client if it's not working
            if ctx.guild.voice_client and not ctx.guild.voice_client.is_connected():
                try:
                    await ctx.guild.voice_client.disconnect()
                except:
                    pass
            
            # Try to connect with enhanced error handling
            voice_client = None
            connection_retries = 3
            for conn_attempt in range(connection_retries):
                try:
                    voice_client = await asyncio.wait_for(
                        channel.connect(timeout=30, reconnect=False, cls=discord.VoiceClient), 
                        timeout=15.0
                    )
                    break
                except IndexError as e:
                    if "list index out of range" in str(e) and conn_attempt < connection_retries - 1:
                        logger.warning(f"Voice connection attempt {conn_attempt + 1} failed with IndexError (empty modes array), retrying...")
                        await asyncio.sleep(2)  # Increased delay
                        continue
                    else:
                        logger.error(f"Voice connection failed after {connection_retries} IndexError retries")
                        raise e
                except discord.errors.ConnectionClosed as e:
                    # Handle specific Discord voice connection errors using the new error handler
                    await handle_voice_connection_error(guild_id, e, f"connection_attempt_{conn_attempt + 1}")
                    
                    error_code = getattr(e, 'code', None)
                    if error_code == 4006:
                        logger.warning(f"Voice connection attempt {conn_attempt + 1} failed with error 4006 (session ended), retrying...")
                        if conn_attempt < connection_retries - 1:
                            await asyncio.sleep(3)  # Longer delay for 4006 errors
                            continue
                        else:
                            logger.error(f"Voice connection failed after {connection_retries} attempts due to error 4006")
                            raise e
                    elif error_code == 1000:
                        logger.warning(f"Voice connection attempt {conn_attempt + 1} failed with error 1000 (normal closure), retrying...")
                        if conn_attempt < connection_retries - 1:
                            await asyncio.sleep(2)  # Delay for 1000 errors
                            continue
                        else:
                            logger.error(f"Voice connection failed after {connection_retries} attempts due to error 1000")
                            raise e
                    else:
                        logger.error(f"Discord connection closed during voice connection attempt {conn_attempt + 1}: {e}")
                        if conn_attempt < connection_retries - 1:
                            await asyncio.sleep(2)
                            continue
                        else:
                            raise e
                except discord.errors.ClientException as e:
                    if "Already connected to a voice channel" in str(e):
                        # Check if we can find the existing connection
                        if ctx.guild.voice_client and ctx.guild.voice_client.is_connected():
                            logger.info(f"Found existing connection after 'already connected' error")
                            voice_client = ctx.guild.voice_client
                            break
                        else:
                            logger.warning(f"'Already connected' but no valid client found, this may indicate a state issue")
                            raise e
                    else:
                        logger.error(f"Client exception during voice connection attempt {conn_attempt + 1}: {e}")
                        if conn_attempt < connection_retries - 1:
                            await asyncio.sleep(2)
                            continue
                        else:
                            raise e
            
            if voice_client and voice_client.is_connected():
                logger.info(f"Successfully connected to voice channel in guild {guild_id}")
                # We can't directly assign to ctx.voice_client, but the connection will be available through the guild
                return True
            else:
                logger.warning(f"Voice connection attempt {attempt + 1} failed - no valid client obtained")
                
        except discord.errors.ConnectionClosed as e:
            # Use the new error handler for better error management
            await handle_voice_connection_error(guild_id, e, f"reconnection_attempt_{attempt + 1}")
            
            error_code = getattr(e, 'code', None)
            if error_code == 4006:
                logger.warning(f"Discord connection closed with error 4006 during reconnection attempt {attempt + 1} for guild {guild_id}")
            elif error_code == 1000:
                logger.warning(f"Discord connection closed with error 1000 (normal closure) during reconnection attempt {attempt + 1} for guild {guild_id}")
            else:
                logger.warning(f"Discord connection closed during reconnection attempt {attempt + 1} for guild {guild_id}: {e}")
        except Exception as e:
            logger.warning(f"Voice reconnection attempt {attempt + 1} failed for guild {guild_id}: {e}")
            
        if attempt < max_retries - 1:
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
    
    logger.error(f"Failed to establish voice connection after {max_retries} attempts for guild {guild_id}")
    return False

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates to clean up when the bot is disconnected."""
    guild_id = None
    
    # Check if the bot was disconnected
    if member.id == bot.user.id:
        if before.channel and not after.channel:
            guild_id = before.channel.guild.id
            logger.info(f"Bot disconnected from voice channel in guild {guild_id}")
            
            # Store the channel for potential reconnection
            last_voice_channel[guild_id] = before.channel
            
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
            
            # Try to reconnect and continue playback if there's a queue
            if guild_id in queues and queues[str(guild_id)] and len(queues[str(guild_id)]) > 0:
                logger.info(f"Queue exists for guild {guild_id}, attempting reconnection")
                
                # Create a fake context for reconnection
                guild = bot.get_guild(guild_id)
                if guild:
                    # Get any text channel to create a fake context
                    text_channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
                    if text_channel:
                        fake_ctx = await bot.get_context(await text_channel.fetch_message(text_channel.last_message_id) if text_channel.last_message_id else None)
                        fake_ctx.guild = guild
                        # We can't directly assign to fake_ctx.voice_client, but we can work with the guild's voice client
                        
                        # Attempt reconnection after a short delay
                        asyncio.create_task(reconnect_and_resume(fake_ctx))
        elif after.channel and not before.channel:
            # Bot connected to a new channel
            guild_id = after.channel.guild.id
            last_voice_channel[guild_id] = after.channel
            logger.info(f"Bot connected to voice channel {after.channel.name} in guild {guild_id}")

async def reconnect_and_resume(ctx):
    """Attempt to reconnect and resume playback."""
    await asyncio.sleep(5)  # Wait before attempting reconnection
    guild_id = ctx.guild.id
    
    try:
        logger.info(f"Attempting to reconnect and resume playback for guild {guild_id}")
        
        # Try to establish voice connection
        if await ensure_voice_connection(ctx):
            logger.info(f"Reconnected successfully, resuming playback for guild {guild_id}")
            # Resume playback
            await play_next(ctx)
        else:
            logger.error(f"Failed to reconnect for guild {guild_id}")
            
    except Exception as e:
        logger.error(f"Error during reconnection attempt for guild {guild_id}: {e}")

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
                
            # Run the async function in the bot's event loop with timeout
            future = asyncio.run_coroutine_threadsafe(run_playlist_handler(), bot.loop)
            result = future.result(timeout=30)  # 30 second timeout
            return jsonify({"success": True, "message": "Playlist added to queue"}), 200
        except asyncio.TimeoutError:
            logger.error(f"Timeout handling playlist in API endpoint: {search}")
            return jsonify({"error": "Playlist processing timed out. Try a smaller playlist."}), 408
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

async def download_audio(url, output_path):
    """Download audio from a YouTube URL using yt-dlp."""
    try:
        # Use simplified options for download
        ydl_opts = default_youtube_options.copy()
        ydl_opts.update({
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': output_path,
        })

        # Download the audio
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"Error downloading audio: {e}")
        return False

# ElevenLabs API configuration
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")

# New command for text-to-speech using ElevenLabs
@bot.command()
async def talk(ctx, *, text: str):
    """Generate and play TTS from ElevenLabs for the given text."""
    if not ELEVENLABS_API_KEY:
        return await ctx.send("❌ ElevenLabs API key not set.")
    # Ensure bot is in voice channel
    if not ctx.voice_client:
        await ctx.invoke(join)
    voice_client = ctx.voice_client
    # Pause current playback if any
    resume_after = False
    if voice_client.is_playing():
        voice_client.pause()
        resume_after = True
    # Generate TTS
    temp_filename = f"tts_{uuid.uuid4()}.mp3"
    api_url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }
    payload = {"text": text}
    async with aiohttp.ClientSession() as session:
        async with session.post(api_url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                return await ctx.send(f"❌ ElevenLabs TTS error: {resp.status} {err_text}")
            audio_data = await resp.read()
    # Save TTS to file
    with open(temp_filename, "wb") as f:
        f.write(audio_data)
    # Play the generated TTS
    source = discord.FFmpegPCMAudio(
        temp_filename,
        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options="-vn -ar 48000 -ac 2 -b:a 128k -f s16le"
    )
    voice_client.play(source, after=lambda e: _after_tts(e, temp_filename, resume_after, voice_client))
    await ctx.send(f"🔊 Speaking: \"{text}\"")

# Helper to resume and cleanup after TTS playback
def _after_tts(error, filename, resume_after, voice_client):
    if error:
        logger.error(f"TTS playback error: {error}")
    try:
        if resume_after and voice_client:
            voice_client.resume()
    except Exception as e:
        logger.error(f"Error resuming playback after TTS: {e}")
    try:
        if os.path.exists(filename):
            os.remove(filename)
    except Exception as e:
        logger.error(f"Error removing TTS file {filename}: {e}")

@bot.command()
async def voice_debug(ctx):
    """Shows detailed debug information about voice connection status."""
    logger.info(f"Voice debug command used by {ctx.author} in guild {ctx.guild.id}")
    
    guild_id = ctx.guild.id
    
    # Create an embed for voice debug information
    embed = discord.Embed(title="🔊 Voice Connection Debug", color=discord.Color.blue())
    
    # Voice client status
    if ctx.voice_client:
        embed.add_field(name="Voice Client Status", value=f"Connected: {ctx.voice_client.is_connected()}\nPlaying: {ctx.voice_client.is_playing()}\nPaused: {ctx.voice_client.is_paused()}\nChannel: {ctx.voice_client.channel.name if ctx.voice_client.channel else 'None'}", inline=False)
    else:
        embed.add_field(name="Voice Client Status", value="Not connected", inline=False)
    
    # Guild voice client status
    if ctx.guild.voice_client:
        embed.add_field(name="Guild Voice Client", value=f"Connected: {ctx.guild.voice_client.is_connected()}\nChannel: {ctx.guild.voice_client.channel.name if ctx.guild.voice_client.channel else 'None'}", inline=False)
    else:
        embed.add_field(name="Guild Voice Client", value="Not connected", inline=False)
    
    # Last known voice channel
    if guild_id in last_voice_channel:
        embed.add_field(name="Last Known Channel", value=f"Name: {last_voice_channel[guild_id].name}\nID: {last_voice_channel[guild_id].id}", inline=False)
    else:
        embed.add_field(name="Last Known Channel", value="None stored", inline=False)
    
    # User's voice status
    if ctx.author.voice:
        embed.add_field(name="Your Voice Status", value=f"Connected: Yes\nChannel: {ctx.author.voice.channel.name}\nMembers: {len(ctx.author.voice.channel.members)}", inline=False)
    else:
        embed.add_field(name="Your Voice Status", value="Not connected to voice", inline=False)
    
    # Available voice channels
    voice_channels = []
    for vc in ctx.guild.voice_channels:
        member_count = len(vc.members)
        has_bot = any(member.id == bot.user.id for member in vc.members)
        voice_channels.append(f"• {vc.name} ({member_count} members{' - Bot here' if has_bot else ''})")
    
    if voice_channels:
        embed.add_field(name="Available Voice Channels", value="\n".join(voice_channels), inline=False)
    else:
        embed.add_field(name="Available Voice Channels", value="None found", inline=False)
    
    # Connection troubleshooting tips
    tips = []
    if not ctx.voice_client:
        tips.append("• Use `!join` to connect to your current voice channel")
    if ctx.author.voice and not ctx.voice_client:
        tips.append("• Make sure you're in a voice channel before using `!join`")
    if ctx.voice_client and not ctx.voice_client.is_connected():
        tips.append("• Voice connection appears broken, try `!join` again")
    if not tips:
        tips.append("• Voice connection appears healthy")
    
    embed.add_field(name="Troubleshooting Tips", value="\n".join(tips), inline=False)
    
    await ctx.send(embed=embed)



