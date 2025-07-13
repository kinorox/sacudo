# Sacudo 🎵

A Discord YouTube music bot with a web dashboard interface.

## Features

- 🎵 Play music from YouTube URLs or search queries
- 🎛️ Web dashboard for remote control
- 📱 Discord slash commands and buttons
- 🔊 High-quality audio playback
- 📋 Queue management
- 🎤 Text-to-speech support (with ElevenLabs)
- 🔄 Auto-disconnect when voice channel is empty

## Installation

### Prerequisites

- Python 3.8 or higher
- FFmpeg (for audio processing)
- A Discord bot token
- (Optional) ElevenLabs API key for TTS

### Step 1: Clone the Repository

```bash
git clone https://github.com/yourusername/sacudo.git
cd sacudo
```

### Step 2: Install the Bot

```bash
pip install .
```

This will install Sacudo and all its dependencies, making the `sacudo` command available system-wide.

### Step 3: Configure Environment Variables

Create a `.env` file in the project directory:

```bash
# Required
BOT_TOKEN=your_discord_bot_token_here

# Optional
API_PORT=8000
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
ELEVENLABS_VOICE_ID=your_voice_id_here
```

To get a Discord bot token:
1. Go to https://discord.com/developers/applications
2. Create a new application
3. Go to the "Bot" section
4. Copy the token

### Step 4: Set up FFmpeg

#### Windows
Download from https://ffmpeg.org/download.html and add to PATH

#### Linux/macOS
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

## Usage

### Basic Usage (Bot Only)

```bash
sacudo
```

This starts the Discord bot without the web dashboard.

### With Web Dashboard

```bash
sacudo --with-api
```

This starts both the Discord bot and the web dashboard at `http://localhost:8000`.

### Command Line Options

```bash
sacudo --help          # Show help message
sacudo --version       # Show version
sacudo --with-api      # Run with web dashboard
```

## Discord Commands

- `/play <song>` - Play a song from YouTube
- `/skip` - Skip the current song
- `/queue` - Show the current queue
- `/volume <1-100>` - Set the volume
- `/pause` - Pause playback
- `/resume` - Resume playback
- `/stop` - Stop playback and clear queue
- `/leave` - Leave the voice channel
- `/join` - Join your voice channel
- `/talk <text>` - Text-to-speech (requires ElevenLabs)
- `/debug` - Show debug information

## Web Dashboard

When running with `--with-api`, you can access the web dashboard at `http://localhost:8000`.

Features:
- View connected servers
- Control playback remotely
- Manage queue
- Real-time updates via WebSocket

## Development

### Running in Development Mode

```bash
python bot.py                 # Bot only
python bot.py --with-api      # Bot with web dashboard
```

### Running Tests

```bash
python -m pytest tests/
```

## Troubleshooting

### Common Issues

1. **"BOT_TOKEN is not set"**
   - Make sure you have a `.env` file with your bot token

2. **"FFmpeg not found"**
   - Install FFmpeg and ensure it's in your PATH

3. **"Permission denied"**
   - Make sure the bot has the necessary permissions in your Discord server

4. **YouTube extraction errors**
   - Check if cookies.txt exists and is properly formatted
   - Some videos may be region-restricted

### Logs

Bot logs are stored in `bot.log` with automatic rotation.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

If you encounter any issues, please create an issue on GitHub or contact the maintainers.
