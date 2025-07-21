# Sacudo Dashboard

A React-based web dashboard for managing and monitoring the Sacudo Discord music bot. This dashboard provides a user-friendly interface to control music playback, manage queues, and monitor the status of your music bot.

## Features

- **Server Overview**: View all servers where the bot is active
- **Music Player Controls**: Play, pause, skip, and stop music playback
- **Queue Management**: View, add, remove, and reorder songs in the queue
- **Real-time Updates**: Get real-time updates of playback status via WebSockets
- **Volume Control**: Adjust the volume of the currently playing song

## Tech Stack

- **Backend**: Integrated Flask API in main bot.py with Flask-SocketIO
- **Frontend**: React with Tailwind CSS for styling
- **Communication**: WebSockets for real-time updates, REST API for actions

## Setup and Installation

### Prerequisites

- Node.js and npm
- Sacudo bot installed and configured

### Installation

1. Navigate to the dashboard directory:
   ```bash
   cd dashboard
   ```

2. Install the required npm packages:
   ```bash
   npm install
   ```

### Running the Dashboard

#### Option 1: One-command startup (Recommended)
```bash
sacudo --with-dashboard
```

This single command starts the bot, API, and React dashboard all together.

#### Option 2: Using the dashboard npm script
```bash
cd dashboard
npm run start:dev
```

This will start both the bot with API and the React frontend.

#### Option 3: Manual setup
1. Start the bot with API:
   ```bash
   sacudo --with-api
   # or directly: python bot.py --with-api
   ```

2. In a separate terminal, start the React development server:
   ```bash
   cd dashboard
   npm start
   ```

## Usage

1. Open your browser and navigate to `http://localhost:3000`
2. Select a server from the dashboard to view and control its music playback
3. Use the player controls to play, pause, skip, or stop the current song
4. Add new songs to the queue using the input form
5. Manage the queue by removing or reordering songs

## Development

### Building for Production

```bash
cd dashboard
npm run build
```

### Running Tests

```bash
cd dashboard
npm test
```

## API Integration

The dashboard communicates with the bot's integrated Flask API running on port 8000 (by default). The React development server proxies API requests to avoid CORS issues.

## WebSocket Events

The dashboard uses WebSockets for real-time updates:

- `song_update`: Sent when the current song changes
- `queue_update`: Sent when the queue changes 