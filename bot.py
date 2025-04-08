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
    def __init__(self, source, *, data, volume=0.5):
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

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False, retry_count=0):
        loop = loop or asyncio.get_event_loop()
        
        logger.info(f"Creating YTDLSource from URL: {url}")
        
        # Validate URL format
        if not url.startswith(('http://', 'https://')):
            logger.error(f"Invalid URL format: {url}")
            raise YTDLError("Invalid URL format. Please provide a valid HTTP/HTTPS URL.")
        
        # Check if we have cached data for this URL
        if url in song_cache:
            logger.info(f"Using cached data for URL: {url}")
            data = song_cache[url]
            filename = data['url'] if stream else data.get('filename')
            ffmpeg_options = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -nostdin',
                'options': '-vn -bufsize 128k -ar 48000 -ac 2 -f s16le -loglevel warning -af "aresample=48000:first_pts=0"'
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
        
        ydl_opts = {
            'format': selected_format,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'logtostderr': False,
            'no_warnings': True,
            'quiet': True,
            'ignoreerrors': True,
            'retries': 10,
            'nocheckcertificate': True,
            'skip_download': True,
            'default_search': 'auto',
            'cookies': 'cookies.txt',
            'no_playlist_metafiles': True,
            'extract_flat': 'in_playlist',
            'force_generic_extractor': False,
            'no_color': True,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            'socket_timeout': 30,
            'extractor_args': {'youtube': {'skip': ['dash']}},
            'cookiefile': 'cookies.txt',
            'noplaylist': True,
            'youtube_include_dash_manifest': False
        }
        
        try:
            with YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Extracting info for URL: {url}")
                data = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=not stream))

            if data is None:
                logger.error(f"Failed to extract info for URL: {url}")
                raise YTDLError(f"Could not extract information from URL: {url}")

            if 'entries' in data and data['entries']:
                logger.info(f"URL is a playlist, using first entry: {url}")
                data = data['entries'][0]
                
                # Check if the entry is valid
                if not data:
                    logger.error(f"Empty entry in playlist for URL: {url}")
                    raise YTDLError(f"Empty entry in playlist for URL: {url}")
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
        
        # Cache the data for future use
        song_cache[url] = data
        logger.info(f"Cached data for URL: {url}")
        
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -nostdin',
            'options': '-vn -bufsize 128k -ar 48000 -ac 2 -f s16le -loglevel warning -af "aresample=48000:first_pts=0"'
        }
        
        # Create the audio source with a small delay to ensure proper initialization
        audio_source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
        
        # Add a small delay to ensure the source is properly initialized
        await asyncio.sleep(0.2)
        
        logger.info(f"Created YTDLSource for URL: {url}, title: {data.get('title')}")
        return cls(audio_source, data=data)
        
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
                await interaction.followup.send("⏭ Skipped. No more songs in the queue.", ephemeral=True)
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
                await ctx.send(f"Added to queue: {search}")
            else:
                try:
                    logger.info(f"Creating player for: {search}")
                    player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
                    
                    # Add a small delay to ensure buffer is filled
                    await asyncio.sleep(0.5)
                    
                    logger.info(f"Playing: {player.title}")
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                    current_song[ctx.guild.id] = player

                    await update_music_message(ctx, player)

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

    video_id = player.url.split("v=")[-1]
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

    embed = discord.Embed(title="🎵 Now Playing", description=f"**[{player.title}]({player.url})**", color=discord.Color.blue())
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
            await ctx.send("⏭ Skipped. No more songs in the queue.")
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

@bot.command()
async def checkduplicates(ctx):
    """Checks for duplicate songs in the queue."""
    logger.info(f"Checkduplicates command used by {ctx.author} in guild {ctx.guild.id}")
    
    guild_id = ctx.guild.id
    
    if guild_id not in queues or not queues[guild_id]:
        logger.info(f"Queue is empty for guild {guild_id}")
        await ctx.send("📋 The queue is empty.")
        return
    
    # Create a set to track unique URLs
    unique_urls = set()
    duplicates = []
    
    # Check for duplicates
    for url in queues[guild_id]:
        if url in unique_urls:
            duplicates.append(url)
        else:
            unique_urls.add(url)
    
    # Create an embed to display the results
    embed = discord.Embed(title="🔍 Duplicate Check", color=discord.Color.blue())
    
    if duplicates:
        # Extract video IDs for the duplicates
        duplicate_list = ""
        for i, url in enumerate(duplicates, 1):
            try:
                # Use a simple regex to extract video ID
                video_id = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url)
                if video_id:
                    video_id = video_id.group(1)
                    duplicate_list += f"{i}. [Video](https://www.youtube.com/watch?v={video_id})\n"
                else:
                    duplicate_list += f"{i}. {url}\n"
            except:
                duplicate_list += f"{i}. {url}\n"
        
        embed.add_field(name="Duplicates Found", value=f"Found {len(duplicates)} duplicate songs in the queue.", inline=False)
        embed.add_field(name="Duplicate Songs", value=duplicate_list, inline=False)
        
        # Add a button to remove duplicates
        view = RemoveDuplicatesView(ctx)
        await ctx.send(embed=embed, view=view)
    else:
        embed.add_field(name="Result", value="✅ No duplicate songs found in the queue.", inline=False)
        await ctx.send(embed=embed)


class RemoveDuplicatesView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=60)  # 60 second timeout
        self.ctx = ctx

    @discord.ui.button(label="Remove Duplicates", style=discord.ButtonStyle.red)
    async def remove_duplicates(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        guild_id = self.ctx.guild.id
        logger.info(f"Remove duplicates button pressed in guild {guild_id}")
        
        if guild_id not in queues or not queues[guild_id]:
            logger.warning(f"Remove duplicates button pressed but queue is empty in guild {guild_id}")
            await interaction.followup.send("❌ The queue is empty.", ephemeral=True)
            return
        
        # Create a new queue with only unique URLs
        new_queue = deque()
        unique_urls = set()
        
        # Add only unique URLs to the new queue
        for url in queues[guild_id]:
            if url not in unique_urls:
                unique_urls.add(url)
                new_queue.append(url)
        
        # Count how many duplicates were removed
        removed_count = len(queues[guild_id]) - len(new_queue)
        
        # Replace the old queue with the new one
        queues[guild_id] = new_queue
        
        # Disable the button
        self.remove_duplicates.disabled = True
        
        # Update the message
        embed = discord.Embed(title="✅ Duplicates Removed", description=f"Removed {removed_count} duplicate songs from the queue.", color=discord.Color.green())
        await interaction.message.edit(embed=embed, view=self)
        
        logger.info(f"Removed {removed_count} duplicate songs from queue in guild {guild_id}")
        await interaction.followup.send(f"✅ Removed {removed_count} duplicate songs from the queue.", ephemeral=True)

@bot.command()
async def fixqueue(ctx):
    """Manually fixes the queue by removing duplicates."""
    logger.info(f"Fixqueue command used by {ctx.author} in guild {ctx.guild.id}")
    
    guild_id = ctx.guild.id
    
    if guild_id not in queues or not queues[guild_id]:
        logger.info(f"Queue is empty for guild {guild_id}")
        await ctx.send("📋 The queue is empty.")
        return
    
    # Get the original queue length
    original_length = len(queues[guild_id])
    
    # Fix the queue
    new_length = await fix_queue(guild_id)
    
    # Calculate how many duplicates were removed
    removed_count = original_length - new_length
    
    if removed_count > 0:
        logger.info(f"Fixed queue by removing {removed_count} duplicate songs in guild {guild_id}")
        await ctx.send(f"✅ Fixed the queue by removing {removed_count} duplicate songs.")
    else:
        logger.info(f"Queue is already clean with no duplicates in guild {guild_id}")
        await ctx.send("✅ The queue is already clean with no duplicates.")

@bot.command()
async def diagnose(ctx):
    """Diagnoses and fixes common issues with the bot."""
    logger.info(f"Diagnose command used by {ctx.author} in guild {ctx.guild.id}")
    
    guild_id = ctx.guild.id
    issues_found = []
    fixes_applied = []
    
    # Create an embed for the diagnosis results
    embed = discord.Embed(title="🔍 Bot Diagnosis", color=discord.Color.blue())
    
    # Check voice client status
    if not ctx.voice_client:
        issues_found.append("Bot is not connected to a voice channel")
    else:
        embed.add_field(name="Voice Client", value=f"Connected: {ctx.voice_client.is_connected()}\nPlaying: {ctx.voice_client.is_playing()}\nPaused: {ctx.voice_client.is_paused()}", inline=False)
    
    # Check queue status
    if guild_id not in queues:
        issues_found.append("Queue does not exist")
        queues[guild_id] = deque()
        fixes_applied.append("Created new queue")
    elif not queues[guild_id]:
        embed.add_field(name="Queue", value="Empty", inline=False)
    else:
        # Check for duplicates
        original_length = len(queues[guild_id])
        new_length = await fix_queue(guild_id)
        if original_length != new_length:
            issues_found.append(f"Found {original_length - new_length} duplicate songs in queue")
            fixes_applied.append(f"Removed {original_length - new_length} duplicate songs")
        
        embed.add_field(name="Queue", value=f"Length: {len(queues[guild_id])}", inline=False)
    
    # Check current song status
    if guild_id in current_song and current_song[guild_id]:
        embed.add_field(name="Current Song", value=f"Title: {current_song[guild_id].title}\nURL: {current_song[guild_id].url}", inline=False)
    else:
        embed.add_field(name="Current Song", value="None", inline=False)
    
    # Check preloaded song status
    if guild_id in preloaded_songs and preloaded_songs[guild_id]:
        embed.add_field(name="Preloaded Song", value=f"Title: {preloaded_songs[guild_id].title}\nURL: {preloaded_songs[guild_id].url}", inline=False)
    else:
        embed.add_field(name="Preloaded Song", value="None", inline=False)
    
    # Check lock status
    if guild_id in playing_locks and playing_locks[guild_id]:
        issues_found.append("Playing lock is stuck (might be causing playback issues)")
        playing_locks[guild_id] = False
        fixes_applied.append("Reset playing lock")
    
    embed.add_field(name="Lock Status", value=f"Playing Lock: {playing_locks.get(guild_id, False)}", inline=False)
    
    # Add issues and fixes to the embed
    if issues_found:
        embed.add_field(name="Issues Found", value="\n".join([f"• {issue}" for issue in issues_found]), inline=False)
    else:
        embed.add_field(name="Issues Found", value="✅ No issues found", inline=False)
    
    if fixes_applied:
        embed.add_field(name="Fixes Applied", value="\n".join([f"• {fix}" for fix in fixes_applied]), inline=False)
    else:
        embed.add_field(name="Fixes Applied", value="No fixes were needed", inline=False)
    
    # Send the diagnosis results
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

bot.run(BOT_TOKEN)
