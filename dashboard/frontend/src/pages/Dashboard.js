import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

const Dashboard = () => {
  const [guilds, setGuilds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchGuilds = async () => {
      try {
        const response = await axios.get('/api/guilds');
        setGuilds(response.data);
        setError(null);
      } catch (err) {
        console.error('Error fetching guilds:', err);
        setError('Failed to load servers. Please try again later.');
      } finally {
        setLoading(false);
      }
    };

    fetchGuilds();
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center items-center min-h-[calc(100vh-64px)]">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-discord-blurple"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center p-8 bg-discord-darker rounded-lg shadow-md">
        <div className="text-discord-red text-4xl mb-4">⚠️</div>
        <h2 className="text-xl font-bold mb-2">Error</h2>
        <p className="text-gray-300 mb-4">{error}</p>
        <button 
          onClick={() => window.location.reload()} 
          className="bg-discord-blurple hover:bg-opacity-80 text-white px-4 py-2 rounded"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>
      
      {guilds.length === 0 ? (
        <div className="text-center p-8 bg-discord-darker rounded-lg shadow-md">
          <p className="text-gray-300">No servers found. Invite the bot to a server to get started.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {guilds.map(guild => (
            <Link 
              key={guild.id} 
              to={`/guild/${guild.id}`}
              className="block p-6 bg-discord-darker rounded-lg shadow-md hover:bg-discord-dark transition duration-200"
            >
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold">{guild.name}</h2>
                {guild.is_playing && (
                  <div className="bg-discord-green px-2 py-1 rounded text-xs font-medium">
                    Playing
                  </div>
                )}
              </div>
              <div className="mt-4 flex justify-end">
                <span className="text-sm text-gray-400">
                  Click to manage
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
};

export default Dashboard; 