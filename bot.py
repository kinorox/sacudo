import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import youtube_dl
import asyncio
from yt_dlp import YoutubeDL
from collections import deque
import re

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
        self._start_time = None

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        
        # Check if we have cached data for this URL
        if url in song_cache:
            data = song_cache[url]
            filename = data['url'] if stream else data.get('filename')
            ffmpeg_options = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -nostdin',
                'options': '-vn -bufsize 128k -ar 48000 -ac 2 -f s16le -loglevel warning -af "aresample=48000:first_pts=0"'
            }
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
            
        ydl_opts = {
            'format': 'bestaudio/best',
            'extractaudio': True,
            'audioformat': 'mp3',
            'logtostderr': False,
            'no_warnings': True,
            'quiet': True,
            'ignoreerrors': True,
            'retries': 3,
            'nocheckcertificate': True,
            'skip_download': True,
            'default_search': 'auto',
            'cookies': 'cookies.txt',
            'no_playlist_metafiles': True,
            'extract_flat': 'in_playlist',
            'force_generic_extractor': False,
            'no_color': True,
            'geo_bypass': True,
            'socket_timeout': 10
        }
        with YoutubeDL(ydl_opts) as ydl:
            data = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=not stream))

        if data is None:
            raise YTDLError()

        if 'entries' in data and data['entries']:
            data = data['entries'][0]

        filename = data['url'] if stream else ydl.prepare_filename(data)
        
        # Cache the data for future use
        song_cache[url] = data
        
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin -nostdin',
            'options': '-vn -bufsize 128k -ar 48000 -ac 2 -f s16le -loglevel warning -af "aresample=48000:first_pts=0"'
        }
        
        # Create the audio source with a small delay to ensure proper initialization
        audio_source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
        
        # Add a small delay to ensure the source is properly initialized
        await asyncio.sleep(0.2)
        
        return cls(audio_source, data=data)


# 🎵 Button Controls View 🎵
class MusicControls(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)  # Prevents buttons from auto-disabling after 15 minutes
        self.ctx = ctx

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.blurple)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        if not self.ctx.voice_client:
            await interaction.followup.send("❌ I'm not connected to a voice channel.", ephemeral=True)
            return
            
        if not self.ctx.voice_client.is_playing():
            await interaction.followup.send("❌ Nothing is playing right now.", ephemeral=True)
            return
        
        # Get the guild ID
        guild_id = self.ctx.guild.id
        
        # Check if we're already processing a skip (lock mechanism)
        if guild_id in playing_locks and playing_locks[guild_id]:
            await interaction.followup.send("⏳ Please wait a moment before skipping again.", ephemeral=True)
            return
        
        # Set the lock
        playing_locks[guild_id] = True
        
        try:
            # Stop the current song
            self.ctx.voice_client.stop()
            
            # Check if there are songs in the queue
            if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
                # Get the next URL from the queue
                next_url = queues[guild_id].popleft()
                
                try:
                    # Create the player for the next song
                    player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                    
                    # Add a small delay to ensure buffer is filled
                    await asyncio.sleep(0.5)
                    
                    # Make sure we're not already playing something
                    if self.ctx.voice_client.is_playing():
                        self.ctx.voice_client.stop()
                        await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                    
                    # Play the next song
                    self.ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(self.ctx), bot.loop).result() if e is None else None)
                    current_song[guild_id] = player
                    
                    # Update the now playing message
                    await update_music_message(self.ctx, player)
                    
                    await interaction.followup.send(f"⏭ Skipped to: **{player.title}**", ephemeral=True)
                except Exception as e:
                    print(f"Error playing next song after skip: {e}")
                    await interaction.followup.send("❌ Error playing the next song. Trying to continue...", ephemeral=True)
                    # Try to play the next song in the queue
                    asyncio.create_task(play_next(self.ctx))
            else:
                # No more songs in queue
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

    @discord.ui.button(label="⏸ Pause", style=discord.ButtonStyle.gray)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        if self.ctx.voice_client and self.ctx.voice_client.is_playing():
            self.ctx.voice_client.pause()
            # Use followup instead of response since we already deferred
            await interaction.followup.send("Song paused.", ephemeral=True)
        else:
            # Use followup instead of response since we already deferred
            await interaction.followup.send("❌ Nothing is playing right now.", ephemeral=True)

    @discord.ui.button(label="▶ Resume", style=discord.ButtonStyle.green)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        if self.ctx.voice_client and self.ctx.voice_client.is_paused():
            self.ctx.voice_client.resume()
            # Use followup instead of response since we already deferred
            await interaction.followup.send("Song resumed.", ephemeral=True)
        else:
            # Use followup instead of response since we already deferred
            await interaction.followup.send("❌ No song is paused right now.", ephemeral=True)

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.red)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stops playback, clears the queue, and deletes the message."""
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        guild_id = self.ctx.guild.id
        if guild_id in queues:
            queues[guild_id].clear()
        if self.ctx.voice_client:
            self.ctx.voice_client.stop()

        if guild_id in current_song_message and current_song_message[guild_id]:
            try:
                await current_song_message[guild_id].delete()
                current_song_message[guild_id] = None
            except discord.NotFound:
                pass

        # Use followup instead of response since we already deferred
        await interaction.followup.send("⏹ Playback stopped and queue cleared.", ephemeral=True)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')


@bot.command()
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send("You are not connected to a voice channel.")
        return
    else:
        channel = ctx.message.author.voice.channel
    await channel.connect()


@bot.command()
async def play(ctx, *, search: str):
    if not ctx.voice_client:
        await ctx.invoke(join)

    async with ctx.typing():
        if 'list=' in search:
            await handle_playlist(ctx, search)
        else:
            if ctx.voice_client.is_playing():
                if ctx.guild.id not in queues:
                    queues[ctx.guild.id] = deque()
                queues[ctx.guild.id].append(search)
                await ctx.send(f"Added to queue: {search}")
            else:
                try:
                    player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
                    
                    # Add a small delay to ensure buffer is filled
                    await asyncio.sleep(0.5)
                    
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                    current_song[ctx.guild.id] = player

                    await update_music_message(ctx, player)

                except YTDLError:
                    await ctx.send(f"❌ Error: Could not play '{search}'. Please try a different song or URL.")
                except Exception as e:
                    await ctx.send(f"❌ An unexpected error occurred: {str(e)}")
                    print(f"Error in play command: {e}")


async def update_music_message(ctx, player):
    """Updates the bot message to keep only one active message."""
    guild_id = ctx.guild.id

    if guild_id in current_song_message and current_song_message[guild_id]:
        try:
            await current_song_message[guild_id].delete()
        except discord.NotFound:
            pass

    video_id = player.url.split("v=")[-1]
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

    embed = discord.Embed(title="🎵 Now Playing", description=f"**[{player.title}]({player.url})**", color=discord.Color.blue())
    embed.set_thumbnail(url=thumbnail_url)
    embed.add_field(name="Queue Length", value=str(len(queues.get(ctx.guild.id, []))), inline=False)
    view = MusicControls(ctx)

    msg = await ctx.send(embed=embed, view=view)
    current_song_message[guild_id] = msg


async def play_next(ctx):
    """Plays the next song in the queue or updates the message if queue is empty."""
    guild_id = ctx.guild.id
    
    # Check if we're already playing a song (lock mechanism)
    if guild_id in playing_locks and playing_locks[guild_id]:
        print(f"Already playing a song in guild {guild_id}, skipping play_next call")
        return
    
    # Set the lock
    playing_locks[guild_id] = True
    
    try:
        # Check if we have a preloaded song
        if guild_id in preloaded_songs and preloaded_songs[guild_id]:
            player = preloaded_songs[guild_id]
            preloaded_songs[guild_id] = None
            
            if not ctx.voice_client:
                await ctx.invoke(join)
            try:
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                # Make sure we're not already playing something
                if ctx.voice_client.is_playing():
                    ctx.voice_client.stop()
                    await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                current_song[guild_id] = player
                
                await update_music_message(ctx, player)
            except Exception as e:
                print(f"Error playing preloaded song: {e}")
                # If there's an error, try the next song
                await play_next(ctx)
            
            # Start preloading the next song
            asyncio.create_task(preload_next_song(ctx))
            return
        
        # Check if there are songs in the queue
        if guild_id in queues and queues[guild_id]:
            try:
                # Get the next URL from the queue
                next_url = queues[guild_id].popleft()
                
                # Create the player for the next song
                player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                
                # Make sure we're connected to a voice channel
                if not ctx.voice_client:
                    await ctx.invoke(join)
                
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                # Make sure we're not already playing something
                if ctx.voice_client.is_playing():
                    ctx.voice_client.stop()
                    await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                
                # Play the next song
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                current_song[guild_id] = player
                
                # Update the now playing message
                await update_music_message(ctx, player)
                
                # Start preloading the next song
                asyncio.create_task(preload_next_song(ctx))
                return
            except YTDLError:
                print(f"Error playing song: {next_url}")
                # If there's an error with this song, try the next one
                await play_next(ctx)
            except Exception as e:
                print(f"Unexpected error playing song: {e}")
                # If there's an error, try the next song
                await play_next(ctx)
        
        # No more songs in queue - only show the message if we were actually playing something
        if guild_id in current_song and current_song[guild_id]:
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


async def preload_next_song(ctx):
    """Preloads the next song in the queue to reduce latency when switching songs."""
    guild_id = ctx.guild.id
    
    # Clear any existing preloaded song
    preloaded_songs[guild_id] = None
    
    # Check if there are songs in the queue
    if guild_id in queues and queues[guild_id]:
        # Get the next URL without removing it from the queue
        next_url = queues[guild_id][0]
        try:
            # Preload the song
            player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
            preloaded_songs[guild_id] = player
        except YTDLError:
            # If preloading fails, just continue
            pass


async def handle_playlist(ctx, url):
    """Handles the playlist and queues each song."""
    ydl_opts = {'format': 'bestaudio/best', 'extract_flat': 'in_playlist', 'quiet': True}
    with YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        entries = info_dict['entries']
        if not entries:
            return

        if ctx.guild.id not in queues:
            queues[ctx.guild.id] = deque()

        await play(ctx, search=entries[0]['url'])
        for entry in entries[1:]:
            queues[ctx.guild.id].append(entry['url'])


@bot.command()
async def leave(ctx):
    await ctx.voice_client.disconnect()

@bot.command()
async def clearcache(ctx):
    """Clears the song cache to free up memory."""
    global song_cache
    cache_size = len(song_cache)
    song_cache = {}
    await ctx.send(f"✅ Song cache cleared. Freed up memory from {cache_size} cached songs.")

@bot.command()
async def skip(ctx):
    """Skips the current song and plays the next one in the queue."""
    if not ctx.voice_client:
        await ctx.send("❌ I'm not connected to a voice channel.")
        return
    
    if not ctx.voice_client.is_playing():
        await ctx.send("❌ Nothing is playing right now.")
        return
    
    # Get the guild ID
    guild_id = ctx.guild.id
    
    # Check if we're already processing a skip (lock mechanism)
    if guild_id in playing_locks and playing_locks[guild_id]:
        await ctx.send("⏳ Please wait a moment before skipping again.")
        return
    
    # Set the lock
    playing_locks[guild_id] = True
    
    try:
        # Stop the current song
        ctx.voice_client.stop()
        
        # Check if there are songs in the queue
        if guild_id in queues and queues[guild_id] and len(queues[guild_id]) > 0:
            # Get the next URL from the queue
            next_url = queues[guild_id].popleft()
            
            try:
                # Create the player for the next song
                player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                
                # Add a small delay to ensure buffer is filled
                await asyncio.sleep(0.5)
                
                # Make sure we're not already playing something
                if ctx.voice_client.is_playing():
                    ctx.voice_client.stop()
                    await asyncio.sleep(0.2)  # Small delay to ensure the previous song is fully stopped
                
                # Play the next song
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                current_song[guild_id] = player
                
                # Update the now playing message
                await update_music_message(ctx, player)
                
                await ctx.send(f"⏭ Skipped to: **{player.title}**")
            except Exception as e:
                print(f"Error playing next song after skip: {e}")
                await ctx.send("❌ Error playing the next song. Trying to continue...")
                # Try to play the next song in the queue
                asyncio.create_task(play_next(ctx))
        else:
            # No more songs in queue
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

@bot.command()
async def queue(ctx):
    """Shows the current queue of songs."""
    guild_id = ctx.guild.id
    
    if guild_id not in queues or not queues[guild_id]:
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

bot.run(BOT_TOKEN)
