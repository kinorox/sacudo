import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import io from 'socket.io-client';

// Music Player Component
const MusicPlayer = ({ currentSong, guildInfo, refreshData }) => {
  const [volume, setVolume] = useState(currentSong?.volume || 70);
  
  // Update volume state when currentSong changes
  useEffect(() => {
    if (currentSong?.volume) {
      setVolume(currentSong.volume);
    }
  }, [currentSong]);
  
  // Add debugging to see what props we're getting
  console.log('MusicPlayer render:', { 
    currentSong, 
    hasCurrentSong: !!currentSong,
    volume,
    guildInfo: {
      is_paused: guildInfo?.is_paused,
      is_playing: guildInfo?.is_playing,
    }
  });
  
  const handlePlayPause = async () => {
    try {
      if (guildInfo.is_paused) {
        await axios.post(`/api/guild/${guildInfo.id}/resume`);
      } else {
        await axios.post(`/api/guild/${guildInfo.id}/pause`);
      }
      refreshData();
    } catch (err) {
      console.error('Error toggling play/pause:', err);
    }
  };
  
  const handleSkip = async () => {
    try {
      await axios.post(`/api/guild/${guildInfo.id}/skip`);
      refreshData();
    } catch (err) {
      console.error('Error skipping song:', err);
    }
  };
  
  const handleStop = async () => {
    try {
      await axios.post(`/api/guild/${guildInfo.id}/stop`);
      refreshData();
    } catch (err) {
      console.error('Error stopping playback:', err);
    }
  };
  
  const handleVolumeChange = async (e) => {
    const newVolume = parseInt(e.target.value);
    setVolume(newVolume);
    
    try {
      await axios.post(`/api/guild/${guildInfo.id}/volume`, { volume: newVolume });
      refreshData();
    } catch (err) {
      console.error('Error setting volume:', err);
    }
  };
  
  if (!currentSong) {
    return (
      <div className="bg-discord-darker p-6 rounded-lg shadow-md mb-6 text-center">
        <p className="text-gray-300">No song is currently playing</p>
      </div>
    );
  }
  
  return (
    <div className="bg-discord-darker p-6 rounded-lg shadow-md mb-6">
      <div className="flex flex-col md:flex-row items-center">
        <div className="md:w-1/4 flex justify-center mb-4 md:mb-0">
          <img 
            src={currentSong.thumbnail || 'https://i.imgur.com/ufxvZ0j.png'} 
            alt={currentSong.title} 
            className="h-32 w-32 object-cover rounded-lg shadow-lg"
            onError={(e) => {
              e.target.src = 'https://i.imgur.com/ufxvZ0j.png';
            }}
          />
        </div>
        
        <div className="md:w-3/4 md:pl-6">
          <h2 className="text-xl font-semibold mb-2">{currentSong.title}</h2>
          
          {currentSong.url && (
            <a 
              href={currentSong.url} 
              target="_blank" 
              rel="noopener noreferrer" 
              className="text-discord-blurple hover:underline mb-4 inline-block"
            >
              Watch on YouTube
            </a>
          )}
          
          <div className="flex items-center mt-4 space-x-4">
            <button
              onClick={handlePlayPause}
              className="bg-discord-blurple hover:bg-opacity-80 text-white rounded-full w-12 h-12 flex items-center justify-center"
            >
              {guildInfo.is_paused ? (
                <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              ) : (
                <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                </svg>
              )}
            </button>
            
            <button
              onClick={handleSkip}
              className="bg-discord-darker hover:bg-discord-light text-white rounded-full w-12 h-12 flex items-center justify-center border border-discord-light"
            >
              <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z" />
              </svg>
            </button>
            
            <button
              onClick={handleStop}
              className="bg-discord-red hover:bg-opacity-80 text-white rounded-full w-12 h-12 flex items-center justify-center"
            >
              <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6 6h12v12H6z" />
              </svg>
            </button>
          </div>
          
          <div className="mt-4">
            <label className="text-sm text-gray-300 block mb-1">Volume: {volume}%</label>
            <input
              type="range"
              min="0"
              max="150"
              value={volume}
              onChange={handleVolumeChange}
              className="w-full h-2 bg-discord-light rounded-lg appearance-none cursor-pointer"
            />
          </div>
        </div>
      </div>
    </div>
  );
};

// Queue Component
const QueueList = ({ queue, guildId, refreshData }) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [searchInput, setSearchInput] = useState('');
  
  const handleRemoveFromQueue = async (index) => {
    try {
      await axios.delete(`/api/guild/${guildId}/queue/${index}`);
      refreshData();
    } catch (err) {
      console.error('Error removing from queue:', err);
    }
  };
  
  const handlePlayNow = async (index) => {
    try {
      await axios.post(`/api/guild/${guildId}/queue/${index}/play`);
      refreshData();
    } catch (err) {
      console.error('Error playing from queue:', err);
    }
  };
  
  const handleClearQueue = async () => {
    try {
      await axios.post(`/api/guild/${guildId}/queue/clear`);
      refreshData();
    } catch (err) {
      console.error('Error clearing queue:', err);
    }
  };
  
  const handleSearchSubmit = async (e) => {
    e.preventDefault();
    
    if (!searchInput.trim()) return;
    
    try {
      await axios.post(`/api/guild/${guildId}/play`, { url: searchInput });
      setSearchInput('');
      refreshData();
    } catch (err) {
      console.error('Error adding to queue:', err);
    }
  };
  
  return (
    <div className="bg-discord-darker p-6 rounded-lg shadow-md">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between mb-6">
        <h2 className="text-xl font-semibold mb-4 md:mb-0">Queue ({queue.length} songs)</h2>
        
        {queue.length > 0 && (
          <button
            onClick={handleClearQueue}
            className="bg-discord-red hover:bg-opacity-80 text-white px-4 py-2 rounded"
          >
            Clear Queue
          </button>
        )}
      </div>
      
      <form onSubmit={handleSearchSubmit} className="mb-6">
        <div className="flex">
          <input
            type="text"
            placeholder="Add YouTube URL or search term"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="flex-grow px-4 py-2 rounded-l bg-discord-darkest text-white border border-discord-light focus:outline-none focus:border-discord-blurple"
          />
          <button
            type="submit"
            className="bg-discord-blurple hover:bg-opacity-80 text-white px-4 py-2 rounded-r"
          >
            Add
          </button>
        </div>
      </form>
      
      {queue.length === 0 ? (
        <div className="text-center py-4">
          <p className="text-gray-300">The queue is empty. Add songs using the form above.</p>
        </div>
      ) : (
        <div className="space-y-4 max-h-96 overflow-y-auto pr-2">
          {queue.map((item, index) => (
            <div 
              key={index} 
              className="flex items-center bg-discord-darkest p-4 rounded-lg"
            >
              <div className="flex-shrink-0 mr-4">
                <img 
                  src={item.thumbnail || 'https://i.imgur.com/ufxvZ0j.png'} 
                  alt="Thumbnail" 
                  className="h-16 w-16 object-cover rounded"
                />
              </div>
              
              <div className="flex-grow min-w-0">
                <h3 className="font-medium text-white truncate mb-1">
                  {item.title || 'Unknown'}
                </h3>
                <p className="text-xs text-gray-400 truncate">
                  {item.url}
                </p>
              </div>
              
              <div className="flex-shrink-0 flex ml-4 space-x-2">
                <button
                  onClick={() => handlePlayNow(index)}
                  className="bg-discord-green hover:bg-opacity-80 text-white p-2 rounded"
                  title="Play Now"
                >
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                </button>
                
                <button
                  onClick={() => handleRemoveFromQueue(index)}
                  className="bg-discord-red hover:bg-opacity-80 text-white p-2 rounded"
                  title="Remove"
                >
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
                  </svg>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// Main Guild Page Component
const GuildPage = () => {
  const { guildId } = useParams();
  const navigate = useNavigate();
  const [guildInfo, setGuildInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const socketRef = useRef(null);
  
  const fetchGuildInfo = useCallback(async () => {
    try {
      const response = await axios.get(`/api/guild/${guildId}`);
      setGuildInfo(response.data);
      setError(null);
    } catch (err) {
      console.error('Error fetching guild info:', err);
      if (err.response && err.response.status === 404) {
        navigate('/404');
      } else {
        setError('Failed to load server information. Please try again later.');
      }
    } finally {
      setLoading(false);
    }
  }, [guildId, navigate]);
  
  useEffect(() => {
    fetchGuildInfo();
    
    // Set up socket connection with explicit backend URL
    socketRef.current = io('http://localhost:5000', {
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
      transports: ['websocket', 'polling']
    });
    
    // Add connection status logging
    socketRef.current.on('connect', () => {
      console.log('Socket connected successfully to http://localhost:5000');
    });
    
    socketRef.current.on('connect_error', (error) => {
      console.error('Socket connection error:', error);
      console.error('Socket connection details:', {
        url: 'http://localhost:5000',
        readyState: socketRef.current.readyState,
        connected: socketRef.current.connected
      });
    });
    
    // Join the guild room
    socketRef.current.emit('join_guild', { guild_id: guildId });
    console.log(`Emitted join_guild event for guild ${guildId}`);
    
    // Listen for updates
    socketRef.current.on('song_update', (data) => {
      console.log('Received song_update event:', data);
      if (data.guild_id === guildId) {
        console.log('Updating guild info due to song update');
        
        // Always update state when we receive new data
        setGuildInfo(prevState => {
          if (!prevState) return prevState; // Guard against null state
          
          const newState = {
            ...prevState,
            current_song: data.current_song,
            is_playing: data.is_playing !== undefined ? data.is_playing : (data.current_song ? true : false),
            is_paused: data.is_paused !== undefined ? data.is_paused : prevState.is_paused
          };
          
          console.log('State update from song_update:', {
            prev: prevState ? { 
              current_song: prevState.current_song ? prevState.current_song.title : null,
              is_playing: prevState.is_playing, 
              is_paused: prevState.is_paused 
            } : null,
            next: { 
              current_song: newState.current_song ? newState.current_song.title : null,
              is_playing: newState.is_playing, 
              is_paused: newState.is_paused 
            }
          });
          
          return newState;
        });
      }
    });
    
    socketRef.current.on('queue_update', (data) => {
      console.log('Received queue_update event:', data);
      if (data.guild_id === guildId) {
        console.log('Updating guild info due to queue update');
        
        // Always update state when we receive new data
        setGuildInfo(prevState => {
          if (!prevState) return prevState; // Guard against null state
          
          const newState = {
            ...prevState,
            queue: data.queue || prevState.queue || [],
            queue_length: data.queue_length !== undefined ? data.queue_length : 
                         (data.queue ? data.queue.length : prevState.queue_length || 0)
          };
          
          console.log('State update from queue_update:', {
            prev: prevState ? { 
              queue_length: prevState.queue ? prevState.queue.length : 0
            } : null,
            next: { 
              queue_length: newState.queue ? newState.queue.length : 0
            }
          });
          
          return newState;
        });
      }
    });
    
    return () => {
      // Clean up socket connection
      if (socketRef.current) {
        console.log(`Emitting leave_guild event for guild ${guildId}`);
        socketRef.current.emit('leave_guild', { guild_id: guildId });
        socketRef.current.disconnect();
      }
    };
  }, [guildId, navigate, fetchGuildInfo]);
  
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
      <div className="mb-6">
        <Link to="/" className="text-discord-blurple hover:underline flex items-center">
          <svg className="w-5 h-5 mr-1" fill="currentColor" viewBox="0 0 24 24">
            <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z" />
          </svg>
          Back to Dashboard
        </Link>
      </div>
      
      <div className="flex flex-col md:flex-row items-center justify-between mb-6">
        <h1 className="text-2xl font-bold mb-4 md:mb-0">{guildInfo.name}</h1>
        
        <div className="flex items-center space-x-4">
          <div className="flex items-center text-sm">
            <div className={`w-2 h-2 rounded-full mr-2 ${guildInfo.voice_connected ? 'bg-discord-green' : 'bg-discord-red'}`}></div>
            <span>{guildInfo.voice_connected ? 'Connected' : 'Not Connected'}</span>
          </div>
          
          <div className="flex items-center text-sm">
            <svg className="w-5 h-5 mr-1 text-discord-light" fill="currentColor" viewBox="0 0 24 24">
              <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z" />
            </svg>
            <span>{guildInfo.member_count} Members</span>
          </div>
        </div>
      </div>
      
      <MusicPlayer 
        currentSong={guildInfo.current_song} 
        guildInfo={guildInfo} 
        refreshData={fetchGuildInfo} 
      />
      
      <QueueList 
        queue={guildInfo.queue || []} 
        guildId={guildInfo.id} 
        refreshData={fetchGuildInfo}
      />
    </div>
  );
};

export default GuildPage; 