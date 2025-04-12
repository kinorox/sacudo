import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import axios from 'axios';

// Import our components
import Navbar from './components/Navbar';
import Dashboard from './pages/Dashboard';
import GuildPage from './pages/GuildPage';
import NotFound from './pages/NotFound';

function App() {
  const [loading, setLoading] = useState(true);
  const [botStatus, setBotStatus] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    // Check if the API is available when the app starts
    const fetchStatus = async () => {
      try {
        setLoading(true);
        const response = await axios.get('/api/status');
        setBotStatus(response.data);
        setError(null);
      } catch (err) {
        console.error('Error fetching bot status:', err);
        setError('Failed to connect to the bot API. Make sure the bot is running.');
      } finally {
        setLoading(false);
      }
    };

    fetchStatus();
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-discord-dark text-white">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-discord-blurple mx-auto"></div>
          <p className="mt-4 text-lg">Connecting to bot...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-discord-dark text-white">
        <div className="max-w-md p-8 bg-discord-darker rounded-lg shadow-lg text-center">
          <div className="text-discord-red text-5xl mb-4">⚠️</div>
          <h1 className="text-2xl font-bold mb-4">Connection Error</h1>
          <p className="mb-6">{error}</p>
          <button 
            onClick={() => window.location.reload()} 
            className="bg-discord-blurple hover:bg-opacity-80 text-white px-4 py-2 rounded"
          >
            Retry Connection
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-discord-dark text-white">
      <Navbar botStatus={botStatus} />
      <main className="container mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/guild/:guildId" element={<GuildPage />} />
          <Route path="/404" element={<NotFound />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default App; 