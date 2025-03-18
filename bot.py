import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import youtube_dl
import asyncio
from yt_dlp import YoutubeDL
from collections import deque

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

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        ydl_opts = {
            'format': 'bestaudio/best',
            'extractaudio': True,
            'audioformat': 'mp3',
            'logtostderr': False,
            'no_warnings': True,
            'quiet': False,
            'ignoreerrors': True,
            'retries': 3,
            'nocheckcertificate': True,
            'skip_download': True,
            'default_search': 'auto',
            'cookies': 'cookies.txt'
        }
        with YoutubeDL(ydl_opts) as ydl:
            data = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=not stream))

        if data is None:
            raise YTDLError()

        if 'entries' in data and data['entries']:
            data = data['entries'][0]

        filename = data['url'] if stream else ydl.prepare_filename(data)
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)


# 🎵 Button Controls View 🎵
class MusicControls(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)  # Prevents buttons from auto-disabling after 15 minutes
        self.ctx = ctx

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.blurple)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ctx.voice_client and self.ctx.voice_client.is_playing():
            self.ctx.voice_client.stop()
        await interaction.response.defer()

    @discord.ui.button(label="⏸ Pause", style=discord.ButtonStyle.gray)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ctx.voice_client and self.ctx.voice_client.is_playing():
            self.ctx.voice_client.pause()
            await interaction.response.send_message("Song paused.", ephemeral=True)

    @discord.ui.button(label="▶ Resume", style=discord.ButtonStyle.green)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ctx.voice_client and self.ctx.voice_client.is_paused():
            self.ctx.voice_client.resume()
            await interaction.response.send_message("Song resumed.", ephemeral=True)

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.red)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stops playback, clears the queue, and deletes the message."""
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

        await interaction.response.defer()


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
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                    current_song[ctx.guild.id] = player

                    await update_music_message(ctx, player)

                except YTDLError:
                    await play_next(ctx)


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
    if ctx.guild.id in queues and queues[ctx.guild.id]:
        while queues[ctx.guild.id]:
            next_url = queues[ctx.guild.id].popleft()
            try:
                player = await YTDLSource.from_url(next_url, loop=bot.loop, stream=True)
                if not ctx.voice_client:
                    await ctx.invoke(join)
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop).result() if e is None else None)
                current_song[ctx.guild.id] = player

                await update_music_message(ctx, player)
                return
            except YTDLError:
                continue

    # No more songs in queue
    if ctx.guild.id in current_song_message and current_song_message[ctx.guild.id]:
        try:
            embed = discord.Embed(title="⏹ No More Songs to Play", description="The queue is empty. Add more songs to continue!", color=discord.Color.red())
            await current_song_message[ctx.guild.id].edit(embed=embed, view=None)
        except discord.NotFound:
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

bot.run(BOT_TOKEN)
