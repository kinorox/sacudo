import React from 'react';
import { Link } from 'react-router-dom';

const Navbar = ({ botStatus }) => {
  return (
    <nav className="bg-discord-darker shadow-md">
      <div className="container mx-auto px-4 py-3">
        <div className="flex justify-between items-center">
          <div className="flex items-center">
            <Link to="/" className="flex items-center">
              <svg 
                className="h-8 w-8 mr-2 text-discord-blurple"
                fill="currentColor" 
                viewBox="0 0 24 24"
              >
                <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 18a8 8 0 1 1 8-8 8 8 0 0 1-8 8zm4-9h-3V8a1 1 0 0 0-2 0v3H8a1 1 0 0 0 0 2h3v3a1 1 0 0 0 2 0v-3h3a1 1 0 0 0 0-2z"/>
              </svg>
              <span className="text-xl font-bold text-white">Music Bot Dashboard</span>
            </Link>
          </div>
          
          {botStatus && (
            <div className="flex items-center space-x-4">
              <div className="text-sm text-gray-300 flex items-center">
                <div className="bg-discord-green w-2 h-2 rounded-full mr-2"></div>
                <span>{botStatus.user.name}</span>
              </div>
              <div className="text-sm text-gray-300">
                {botStatus.guilds} Servers
              </div>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
};

export default Navbar; 