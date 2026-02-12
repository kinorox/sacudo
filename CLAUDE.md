# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Sacudo is a Discord YouTube music bot with a web dashboard interface. The project consists of:
- Main Discord bot (`bot.py`) - handles music playback, voice commands, and Flask API
- React web dashboard (`dashboard/`) - provides remote control interface
- Python package structure (`sacudo/`) - CLI entry point and package distribution

## Architecture

The bot runs as a single Python process that handles:
1. Discord bot functionality with slash commands for music control
2. YouTube audio extraction using yt-dlp with cookie-based authentication  
3. Flask API server with WebSocket support for real-time dashboard updates
4. Queue management system for music playback across multiple Discord guilds
5. Optional TTS integration with ElevenLabs API

Key components:
- `bot.py` - Main application file containing Discord bot, Flask API, and music player logic
- `dashboard/` - React frontend with Tailwind CSS styling
- `tests/` - Unit and integration tests for core functionality
- Cookie handling via `cookies.txt` for YouTube access

## Development Commands

### Python/Bot Development
```bash
# Install in development mode
pip install -e .

# Run bot only
python bot.py

# Run bot with web dashboard
python bot.py --with-api
sacudo --with-api

# Run tests
python -m pytest tests/
python tests/run_all_tests.py

# Check requirements
pip install -r requirements.txt
```

### Dashboard Development
```bash
cd dashboard

# Install dependencies
npm install

# Start React dev server only
npm start

# Build for production
npm build

# Run tests
npm test

# Start both React frontend and Python backend
npm run start:dev
```

## Environment Configuration

Required `.env` file in project root:
```
BOT_TOKEN=your_discord_bot_token_here
API_PORT=8000  # Optional, defaults to 8000
ELEVENLABS_API_KEY=your_key_here  # Optional, for TTS
ELEVENLABS_VOICE_ID=your_voice_id_here  # Optional, for TTS
SPOTIFY_CLIENT_ID=your_spotify_client_id_here  # Optional, for Spotify URL support
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret_here  # Optional, for Spotify URL support
```

## Testing Strategy

- Unit tests in `tests/unit/` cover core functionality like queue management and URL processing
- Integration tests in `tests/integration/` test command flows and music playback
- Frontend testing uses React Testing Library
- Run all tests with `python tests/run_all_tests.py`

## Key Technical Details

- Uses discord.py 2.0+ with slash commands and button interactions
- YouTube extraction requires `cookies.txt` file for authentication
- Multi-guild support with separate queues per Discord server
- WebSocket communication between React dashboard and Flask backend
- Logging with automatic rotation (5MB files, 5 backups)
- Packaged as installable Python package with console script entry point