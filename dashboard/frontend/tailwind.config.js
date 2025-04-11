/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        discord: {
          blurple: '#5865F2',
          green: '#57F287',
          yellow: '#FEE75C',
          red: '#ED4245',
          dark: '#36393F',
          darker: '#2F3136',
          darkest: '#202225',
          light: '#4F545C',
          white: '#FFFFFF',
        },
      },
    },
  },
  plugins: [],
} 