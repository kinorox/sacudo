import React from 'react';
import { Link } from 'react-router-dom';

const NotFound = () => {
  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-100px)]">
      <div className="text-6xl font-bold text-discord-red mb-4">404</div>
      <h1 className="text-2xl font-semibold mb-6">Page Not Found</h1>
      <p className="text-gray-300 mb-8 text-center max-w-md">
        The page you are looking for doesn't exist or has been moved.
      </p>
      <Link 
        to="/" 
        className="bg-discord-blurple hover:bg-opacity-80 text-white px-6 py-3 rounded-lg font-medium"
      >
        Go to Dashboard
      </Link>
    </div>
  );
};

export default NotFound; 