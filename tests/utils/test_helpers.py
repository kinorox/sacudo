import re
from collections import deque

def is_url(text):
    """Check if the provided text is a URL.
    
    This is a utility function that simulates the one in bot.py.
    """
    # Standard URL patterns
    if text.startswith(('http://', 'https://')):
        return True
        
    # YouTube shortened URLs
    if text.startswith(('youtu.be/', 'youtube.com/', 'www.youtube.com/')):
        return True
        
    # Other common music services
    if any(domain in text for domain in ['spotify.com', 'soundcloud.com', 'bandcamp.com']):
        return True
        
    return False

def extract_video_id(url):
    """Extract YouTube video ID from URL.
    
    This is a utility function that simulates the one in bot.py.
    """
    # Standard YouTube URL format
    if "youtube.com/watch" in url and "v=" in url:
        video_id = url.split("v=")[-1]
        # Remove any additional parameters
        if "&" in video_id:
            video_id = video_id.split("&")[0]
        return video_id
    
    # YouTube shortened URL format
    elif "youtu.be/" in url:
        video_id = url.split("youtu.be/")[-1]
        # Remove any additional parameters
        if "?" in video_id:
            video_id = video_id.split("?")[0]
        return video_id
    
    # Not a recognized YouTube URL format
    return None

def extract_video_id_regex(url):
    """Extract YouTube video ID using regex.
    
    This is an alternative implementation using regex.
    """
    pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    return None

def get_thumbnail_url(video_id):
    """Generate YouTube thumbnail URL from video ID."""
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

def remove_duplicates_from_queue(queue, current_song_url=None):
    """Remove duplicates from a queue.
    
    This is a utility function that simulates fix_queue in bot.py.
    
    Args:
        queue: A deque of URLs
        current_song_url: The URL of the currently playing song (optional)
        
    Returns:
        A new deque with duplicates removed
    """
    new_queue = deque()
    unique_urls = set()
    
    for url in queue:
        # Skip URLs that match the currently playing song
        if current_song_url and url == current_song_url:
            continue
            
        if url not in unique_urls:
            unique_urls.add(url)
            new_queue.append(url)
    
    return new_queue

def create_mock_song_data(url):
    """Create mock song data for testing.
    
    Args:
        url: The URL of the song
        
    Returns:
        A dictionary with mock song data
    """
    video_id = extract_video_id(url) or "unknown"
    return {
        'url': url,
        'title': f"Test Song {video_id}",
        'video_id': video_id,
        'thumbnail': get_thumbnail_url(video_id)
    } 