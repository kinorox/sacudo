# Discord Music Bot Dashboard

A web-based dashboard for managing and monitoring the Discord Music Bot. This dashboard provides a user-friendly interface to control music playback, manage queues, and monitor the status of your music bot.

## Features

- **Server Overview**: View all servers where the bot is active
- **Music Player Controls**: Play, pause, skip, and stop music playback
- **Queue Management**: View, add, remove, and reorder songs in the queue
- **Real-time Updates**: Get real-time updates of playback status via WebSockets
- **Volume Control**: Adjust the volume of the currently playing song

## Tech Stack

- **Backend**: Flask (Python) with Flask-SocketIO for real-time updates
- **Frontend**: React with Tailwind CSS for styling
- **Communication**: WebSockets for real-time updates, REST API for actions

## Setup and Installation

### Prerequisites

- Node.js and npm
- Python 3.7+
- Discord Music Bot running

### Backend Setup

1. Navigate to the backend directory:
   ```
   cd dashboard/backend
   ```

2. Install the required Python packages:
   ```
   pip install -r requirements.txt
   ```

3. Run the API server:
   ```
   python run_api.py
   ```

### Frontend Setup

1. Navigate to the frontend directory:
   ```
   cd dashboard/frontend
   ```

2. Install the required npm packages:
   ```
   npm install
   ```

3. Start the React development server:
   ```
   npm start
   ```

### Running Both Together

On Windows, you can use the provided batch file to start both the backend and frontend:
```
dashboard/run_dashboard.bat
```

## Usage

1. Open your browser and navigate to `http://localhost:3000`
2. Select a server from the dashboard to view and control its music playback
3. Use the player controls to play, pause, skip, or stop the current song
4. Add new songs to the queue using the input form
5. Manage the queue by removing or reordering songs

## API Endpoints

The dashboard uses a REST API to communicate with the bot. The main endpoints are:

- `GET /api/status`: Get overall bot status
- `GET /api/guilds`: Get all guilds where the bot is present
- `GET /api/guild/<guild_id>`: Get detailed information about a specific guild
- `GET /api/guild/<guild_id>/queue`: Get the current queue for a guild
- `POST /api/guild/<guild_id>/play`: Play a song in the specified guild
- `POST /api/guild/<guild_id>/skip`: Skip the current song
- `POST /api/guild/<guild_id>/pause`: Pause the current song
- `POST /api/guild/<guild_id>/resume`: Resume the current song
- `POST /api/guild/<guild_id>/stop`: Stop playback and clear the queue
- `POST /api/guild/<guild_id>/volume`: Set the volume of the current song

## WebSocket Events

The dashboard uses WebSockets for real-time updates:

- `song_update`: Sent when the current song changes
- `queue_update`: Sent when the queue changes 