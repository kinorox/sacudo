import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import io from 'socket.io-client';

// Music Player Component
const MusicPlayer = ({ currentSong, guildInfo, refreshData }) => {
  const [volume, setVolume] = useState(currentSong?.volume || 70);
  const [showDebug, setShowDebug] = useState(false);
  
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
      voice_connected: guildInfo?.voice_connected,
      is_paused: guildInfo?.is_paused,
      is_playing: guildInfo?.is_playing,
    }
  });
  
  const handlePlayPause = async () => {
    try {
      console.log("Before Play/Pause - Current song:", currentSong?.title);
      
      if (guildInfo.is_paused) {
        await axios.post(`/api/guild/${guildInfo.id}/resume`);
      } else {
        await axios.post(`/api/guild/${guildInfo.id}/pause`);
      }
      
      console.log("After Play/Pause API call - waiting for socket updates");
      // We'll let the socket update handle the UI change
      // but we can still refresh as a fallback
      setTimeout(() => {
        console.log("Calling refreshData as fallback after Play/Pause");
        refreshData();
      }, 1000);
    } catch (err) {
      console.error('Error toggling play/pause:', err);
    }
  };
  
  const handleSkip = async () => {
    try {
      console.log("Before Skip - Current song:", currentSong?.title);
      
      const response = await axios.post(`/api/guild/${guildInfo.id}/skip`);
      console.log("Skip successful:", response.data);
      
      console.log("After Skip API call - waiting for socket updates");
      // We'll let the socket update handle the UI change
      // but we can still refresh as a fallback
      setTimeout(() => {
        console.log("Calling refreshData as fallback after Skip");
        refreshData();
      }, 1000);
    } catch (err) {
      console.error('Error skipping song:', err);
      // Log more detailed error information for debugging
      if (err.response) {
        console.error('Error response data:', err.response.data);
        console.error('Error response status:', err.response.status);
      }
      alert(`Failed to skip song: ${err.response?.data?.error || err.message}`);
    }
  };
  
  const handleStop = async () => {
    try {
      console.log("Before Stop - Current song:", currentSong?.title);
      
      await axios.post(`/api/guild/${guildInfo.id}/stop`);
      
      console.log("After Stop API call - waiting for socket updates");
      // We'll let the socket update handle the UI change
      // but we can still refresh as a fallback
      setTimeout(() => {
        console.log("Calling refreshData as fallback after Stop");
        refreshData();
      }, 1000);
    } catch (err) {
      console.error('Error stopping playback:', err);
    }
  };
  
  const handleVolumeChange = async (e) => {
    const newVolume = parseInt(e.target.value);
    setVolume(newVolume);
    
    try {
      console.log("Before Volume Change - Current song:", currentSong?.title);
      
      await axios.post(`/api/guild/${guildInfo.id}/volume`, { volume: newVolume });
      
      console.log("After Volume API call - waiting for socket updates");
      // We'll let the socket update handle the UI change
      // but we can still refresh as a fallback
      setTimeout(() => {
        console.log("Calling refreshData as fallback after Volume Change");
        refreshData();
      }, 1000);
    } catch (err) {
      console.error('Error setting volume:', err);
    }
  };
  
  const handleRefresh = () => {
    console.log("Manual refresh requested");
    refreshData();
  };
  
  // If no current song or not playing but bot is connected to voice
  if (!currentSong) {
    return (
      <div className="bg-discord-darker p-6 rounded-lg shadow-md mb-6">
        <div className="text-center">
          <p className="text-gray-300 mb-4">
            {guildInfo.voice_connected 
              ? "No song information available. A song might be playing but not detected." 
              : "No song is currently playing"}
          </p>
          
          {guildInfo.voice_connected && (
            <div className="space-x-2 mb-4">
              <button
                onClick={handlePlayPause}
                className="bg-discord-blurple hover:bg-opacity-80 text-white py-2 px-4 rounded"
              >
                {guildInfo.is_paused ? 'Resume' : 'Pause'}
              </button>
              
              <button
                onClick={handleSkip}
                className="bg-discord-light hover:bg-opacity-80 text-white py-2 px-4 rounded"
              >
                Skip
              </button>
              
              <button
                onClick={handleStop}
                className="bg-discord-red hover:bg-opacity-80 text-white py-2 px-4 rounded"
              >
                Stop
              </button>
            </div>
          )}
          
          <button 
            onClick={handleRefresh} 
            className="bg-discord-green hover:bg-opacity-80 text-white py-2 px-4 rounded"
          >
            Refresh
          </button>
          
          <div className="mt-4">
            <button 
              onClick={() => setShowDebug(!showDebug)} 
              className="text-xs text-discord-light hover:text-white"
            >
              {showDebug ? 'Hide Debug Info' : 'Show Debug Info'}
            </button>
            
            {showDebug && (
              <div className="mt-2 text-left bg-black bg-opacity-50 p-3 rounded text-xs overflow-auto max-h-48">
                <pre>
                  {JSON.stringify({
                    currentSong: currentSong || 'null',
                    is_playing: guildInfo.is_playing,
                    is_paused: guildInfo.is_paused,
                    voice_connected: guildInfo.voice_connected,
                    connected_channel: guildInfo.connected_channel
                  }, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>
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
          <div className="flex justify-between items-start">
            <div>
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
            </div>
            
            <div className="flex items-center">
              <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs ${guildInfo.is_paused ? 'bg-discord-yellow text-black' : 'bg-discord-green text-white'}`}>
                {guildInfo.is_paused ? 'Paused' : 'Playing'}
              </span>
            </div>
          </div>
          
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
            
            <button
              onClick={handleRefresh}
              className="bg-discord-green hover:bg-opacity-80 text-white rounded-full w-12 h-12 flex items-center justify-center"
              title="Refresh"
            >
              <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                <path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z" />
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
          
          <div className="mt-4 text-right">
            <button 
              onClick={() => setShowDebug(!showDebug)} 
              className="text-xs text-discord-light hover:text-white"
            >
              {showDebug ? 'Hide Debug Info' : 'Show Debug Info'}
            </button>
            
            {showDebug && (
              <div className="mt-2 text-left bg-black bg-opacity-50 p-3 rounded text-xs overflow-auto max-h-48">
                <pre>
                  {JSON.stringify({
                    title: currentSong.title,
                    url: currentSong.url,
                    thumbnail: currentSong.thumbnail,
                    volume: volume,
                    is_playing: guildInfo.is_playing,
                    is_paused: guildInfo.is_paused
                  }, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

// Queue Component
const QueueList = ({ queue, guildId, refreshData, guildInfo }) => {
  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  
  const handleRemoveFromQueue = async (index) => {
    try {
      const response = await fetch(`/api/guild/${guildId}/queue/${index}`, {
        method: 'DELETE',
      });
      
      if (!response.ok) {
        const data = await response.json();
        console.error('Error removing from queue:', data.error);
      } else {
        // Refresh data after removing from queue
        refreshData();
      }
    } catch (error) {
      console.error('Error removing from queue:', error);
    }
  };
  
  const handlePlayNow = async (index) => {
    try {
      const response = await fetch(`/api/guild/${guildId}/queue/${index}/play`, {
        method: 'POST',
      });
      
      if (!response.ok) {
        const data = await response.json();
        console.error('Error playing song from queue:', data.error);
      } else {
        // Refresh data after playing from queue
        refreshData();
      }
    } catch (error) {
      console.error('Error playing song from queue:', error);
    }
  };
  
  const handleClearQueue = async () => {
    try {
      const response = await fetch(`/api/guild/${guildId}/queue/clear`, {
        method: 'POST',
      });
      
      if (!response.ok) {
        const data = await response.json();
        console.error('Error clearing queue:', data.error);
      } else {
        // Refresh data after clearing queue
        refreshData();
      }
    } catch (error) {
      console.error('Error clearing queue:', error);
    }
  };
  
  const handleSearchSubmit = async (e) => {
    e.preventDefault();
    if (!search.trim()) return;
    
    setIsLoading(true);
    try {
      const requestBody = { url: search };
      
      const response = await fetch(`/api/guild/${guildId}/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });
      
      const data = await response.json();
      
      if (!response.ok) {
        console.error('Error adding song:', data.error);
        alert(`Failed to add song: ${data.error}`);
      } else {
        setSearch("");
        // Refresh data after adding a song
        refreshData();
      }
    } catch (error) {
      console.error('Error adding song:', error);
      alert('Error adding song. Check the console for details.');
    } finally {
      setIsLoading(false);
    }
  };
  
  const handleRefresh = () => {
    refreshData();
  };
  
  // Monitor changes to current song for debugging
  useEffect(() => {
    if (guildInfo?.current_song) {
      console.log('GUILD INFO UPDATED - Current song:', {
        title: guildInfo.current_song.title,
        url: guildInfo.current_song.url,
        timestamp: new Date().toISOString(),
        is_playing: guildInfo.is_playing,
        is_paused: guildInfo.is_paused 
      });
    }
  }, [guildInfo]);
  
  return (
    <div className="bg-discord-dark rounded-lg shadow-md p-4 mt-4">
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">Queue</h2>
        
        <button 
          onClick={handleRefresh}
          className="bg-discord-green hover:bg-opacity-80 text-white p-2 rounded"
          title="Refresh Queue"
        >
          <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
            <path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z" />
          </svg>
        </button>
      </div>
      
      <form onSubmit={handleSearchSubmit} className="mb-4">
        <div className="flex flex-col sm:flex-row gap-3 mb-4">
          <input
            type="text"
            placeholder="Add a song (URL or search term)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-discord-darker text-white py-2 px-3 rounded border border-discord-blurple focus:outline-none focus:ring-2 focus:ring-discord-blurple flex-grow"
          />
          <button
            type="submit"
            disabled={isLoading || !search.trim()}
            className="bg-discord-green hover:bg-opacity-80 text-white py-2 px-4 rounded disabled:opacity-50"
          >
            {isLoading ? 'Adding...' : 'Add to Queue'}
          </button>
        </div>
      </form>
      
      {queue && queue.length > 0 ? (
        <>
          <div className="mb-2 text-sm text-discord-light">
            {queue.length} {queue.length === 1 ? 'song' : 'songs'} in queue
          </div>
          <ul className="space-y-2 max-h-[300px] overflow-y-auto p-2">
            {queue.map((item, index) => (
              <li key={index} className="bg-discord-darker p-3 rounded flex items-center gap-3">
                <div className="flex-shrink-0">
                  <span className="text-discord-light text-sm">{index + 1}.</span>
                </div>
                {item.thumbnail && (
                  <img 
                    src={item.thumbnail} 
                    alt="Thumbnail" 
                    className="w-12 h-12 object-cover rounded"
                    onError={(e) => {
                      e.target.src = 'https://i.imgur.com/ufxvZ0j.png';
                    }}
                  />
                )}
                <div className="flex-grow truncate">
                  <div className="truncate text-sm font-medium">
                    {item.title || item.url}
                  </div>
                  {item.title && item.url && (
                    <div className="truncate text-xs text-discord-light">
                      {item.url}
                    </div>
                  )}
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => handlePlayNow(index)}
                    className="bg-discord-blurple hover:bg-opacity-80 text-white p-2 rounded"
                    title="Play Now"
                  >
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  </button>
                  <button
                    onClick={() => handleRemoveFromQueue(index)}
                    className="bg-discord-red hover:bg-opacity-80 text-white p-2 rounded"
                    title="Remove"
                  >
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
                    </svg>
                  </button>
                </div>
              </li>
            ))}
          </ul>
          
          <div className="mt-4 flex justify-between items-center">
            <button
              onClick={() => setShowDebug(!showDebug)}
              className="text-xs text-discord-light hover:text-white"
            >
              {showDebug ? 'Hide Debug Info' : 'Show Debug Info'}
            </button>
            
            <button
              onClick={handleClearQueue}
              className="bg-discord-red hover:bg-opacity-80 text-white py-2 px-4 rounded"
            >
              Clear Queue
            </button>
          </div>
          
          {showDebug && (
            <div className="mt-2 bg-black bg-opacity-50 p-3 rounded text-xs overflow-auto max-h-48">
              <pre>
                {JSON.stringify(queue, null, 2)}
              </pre>
            </div>
          )}
        </>
      ) : (
        <div className="text-center py-8 text-discord-light">
          <div className="text-4xl mb-3">🎵</div>
          <p className="text-lg mb-2">Queue is empty</p>
          <p className="text-sm mb-4">Add songs using the search box above</p>
          
          <button
            onClick={() => setShowDebug(!showDebug)}
            className="text-xs text-discord-light hover:text-white"
          >
            {showDebug ? 'Hide Debug Info' : 'Show Debug Info'}
          </button>
          
          {showDebug && (
            <div className="mt-2 bg-black bg-opacity-50 p-3 rounded text-xs overflow-auto max-h-48">
              <pre>
                {JSON.stringify({ queue_empty: true, queue_length: queue ? queue.length : 0 }, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// Add a VoiceChannelSelector component
const VoiceChannelSelector = ({ guildInfo, onJoinChannel, refreshData }) => {
  const [isJoining, setIsJoining] = useState(false);
  const [selectedChannel, setSelectedChannel] = useState('');
  
  const handleJoinChannel = async () => {
    if (!selectedChannel) return;
    
    setIsJoining(true);
    try {
      const response = await fetch(`/api/guild/${guildInfo.id}/join`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ channel_id: selectedChannel }),
      });
      
      const data = await response.json();
      
      if (!response.ok) {
        console.error('Error joining channel:', data.error);
        alert(`Failed to join channel: ${data.error}`);
      } else {
        console.log('Successfully joined channel:', data);
        // Refresh data after joining
        refreshData();
        if (onJoinChannel) onJoinChannel(selectedChannel);
      }
    } catch (error) {
      console.error('Error joining channel:', error);
      alert('Failed to join channel. Check the console for details.');
    } finally {
      setIsJoining(false);
    }
  };
  
  // If already connected, show which channel
  if (guildInfo.voice_connected && guildInfo.connected_channel) {
    return (
      <div className="bg-discord-dark p-4 rounded-lg shadow-md mb-4">
        <h3 className="text-lg font-semibold mb-2 flex items-center">
          <div className="w-3 h-3 bg-discord-green rounded-full mr-2"></div>
          Connected to Voice
        </h3>
        <p className="text-discord-light mb-2">
          Bot is connected to: <span className="font-medium">{guildInfo.connected_channel.name}</span>
        </p>
      </div>
    );
  }
  
  // Not connected, show channel selector
  return (
    <div className="bg-discord-dark p-4 rounded-lg shadow-md mb-4">
      <h3 className="text-lg font-semibold mb-2 flex items-center">
        <div className="w-3 h-3 bg-discord-red rounded-full mr-2"></div>
        Not Connected to Voice
      </h3>
      <p className="text-discord-light mb-4">
        The bot needs to join a voice channel before playing music.
      </p>
      
      <div className="flex flex-col sm:flex-row gap-3">
        <select 
          className="bg-discord-darker text-white py-2 px-3 rounded border border-discord-blurple focus:outline-none focus:ring-2 focus:ring-discord-blurple flex-grow"
          value={selectedChannel}
          onChange={(e) => setSelectedChannel(e.target.value)}
        >
          <option value="">Select a voice channel</option>
          {guildInfo.voice_channels && guildInfo.voice_channels.map(channel => (
            <option key={channel.id} value={channel.id}>
              {channel.name} ({channel.member_count} {channel.member_count === 1 ? 'member' : 'members'})
            </option>
          ))}
        </select>
        
        <button
          className="bg-discord-blurple hover:bg-opacity-80 text-white py-2 px-4 rounded disabled:opacity-50"
          onClick={handleJoinChannel}
          disabled={!selectedChannel || isJoining}
        >
          {isJoining ? 'Joining...' : 'Join Channel'}
        </button>
      </div>
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
    const apiUrl = process.env.REACT_APP_API_URL || 'http://localhost:8000';
    socketRef.current = io(apiUrl, {
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
      transports: ['websocket', 'polling']
    });
    
    // Add connection status logging
    socketRef.current.on('connect', () => {
      console.log(`Socket connected successfully to ${apiUrl}`);
      
      // Once connected, join the guild room
      socketRef.current.emit('join_guild', { guild_id: guildId });
      console.log(`Emitted join_guild event for guild ${guildId}`);
    });
    
    socketRef.current.on('connect_error', (error) => {
      console.error('Socket connection error:', error);
      console.error('Socket connection details:', {
        url: apiUrl,
        readyState: socketRef.current.readyState,
        connected: socketRef.current.connected
      });
    });
    
    // Listen for updates
    socketRef.current.on('song_update', (data) => {
      console.log('Received song_update event:', data);
      if (data.guild_id === guildId) {
        console.log('Updating guild info due to song update');
        console.log('Current song before update:', guildInfo?.current_song?.title);
        console.log('New song from socket:', data.current_song?.title);
        
        // Immediately update the current song data rather than waiting for fetchGuildInfo
        if (guildInfo) {
          setGuildInfo(prevState => {
            const newState = {
              ...prevState,
              current_song: data.current_song,
              is_playing: data.is_playing,
              is_paused: data.is_paused
            };
            
            // Log here instead of outside to see actual updated values
            console.log('New state after update:', {
              current_song: newState.current_song?.title,
              is_playing: newState.is_playing,
              is_paused: newState.is_paused
            });
            
            return newState;
          });
          
          // Remove this setTimeout as it's using the old state before React updates
          // setTimeout(() => {
          //   console.log('State after song_update:', {
          //     current_song: guildInfo.current_song?.title,
          //     is_playing: guildInfo.is_playing,
          //     is_paused: guildInfo.is_paused
          //   });
          // }, 100);
        }
        
        // Remove the fetchGuildInfo call to prevent overwriting the current song data
        // fetchGuildInfo();
      }
    });
    
    socketRef.current.on('queue_update', (data) => {
      console.log('Received queue_update event:', data);
      if (data.guild_id === guildId) {
        console.log('Updating guild info due to queue update');
        console.log('Current queue length before update:', guildInfo?.queue?.length);
        console.log('New queue length from socket:', data.queue?.length);
        
        // Immediately update the queue data rather than waiting for fetchGuildInfo
        if (guildInfo && data.queue) {
          setGuildInfo(prevState => {
            const newState = {
              ...prevState,
              queue: data.queue,
              queue_length: data.queue_length || (data.queue ? data.queue.length : 0)
            };
            
            // Log here instead of outside to see actual updated values
            console.log('New state after queue update:', {
              queue_length: newState.queue?.length
            });
            
            return newState;
          });
          
          // Remove this setTimeout as it's using the old state before React updates
          // setTimeout(() => {
          //   console.log('State after queue_update:', {
          //     queue_length: guildInfo.queue?.length
          //   });
          // }, 100);
        }
        
        // Remove the fetchGuildInfo call to prevent overwriting the socket data
        // fetchGuildInfo();
      }
    });
    
    // Set an interval to refresh guild data periodically as a fallback
    const refreshInterval = setInterval(() => {
      fetchGuildInfo();
    }, 10000);  // Refresh every 10 seconds
    
    return () => {
      // Clean up socket connection and interval
      if (socketRef.current) {
        console.log(`Emitting leave_guild event for guild ${guildId}`);
        socketRef.current.emit('leave_guild', { guild_id: guildId });
        socketRef.current.disconnect();
      }
      clearInterval(refreshInterval);
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
      
      {/* Add the VoiceChannelSelector before the MusicPlayer */}
      <VoiceChannelSelector 
        guildInfo={guildInfo} 
        onJoinChannel={fetchGuildInfo}
        refreshData={fetchGuildInfo} 
      />
      
      <MusicPlayer 
        currentSong={guildInfo.current_song} 
        guildInfo={guildInfo} 
        refreshData={fetchGuildInfo} 
      />
      
      <QueueList 
        queue={guildInfo.queue || []} 
        guildId={guildInfo.id} 
        refreshData={fetchGuildInfo}
        guildInfo={guildInfo}
      />
    </div>
  );
};

export default GuildPage; 