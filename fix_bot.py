# Script to fix the play_from_queue function order in bot.py
try:
    # Try different encodings
    encodings = ['utf-8', 'latin-1', 'cp1252']
    content = None
    
    for encoding in encodings:
        try:
            with open('bot.py', 'r', encoding=encoding) as f:
                content = f.read()
            print(f"Successfully read file with encoding: {encoding}")
            break
        except UnicodeDecodeError:
            print(f"Failed to read with encoding: {encoding}")
    
    if content is None:
        print("Could not read file with any encoding")
        exit(1)
    
    # Find the play_from_queue function
    start_func = content.find('async def play_from_queue')
    if start_func == -1:
        print("Could not find 'async def play_from_queue' in the file")
        exit(1)
        
    end_func = content.find('        logger.warning(f"No songs in queue for guild {guild_id} in play_from_queue")')
    if end_func == -1:
        print("Could not find the end of the play_from_queue function")
        exit(1)
    
    end_func += 86  # Add length of the line
    play_from_queue_func = content[start_func:end_func]
    
    # Find insertion point before play_song API endpoint
    insertion_point = content.find('@app.route(\'/api/guild/<guild_id>/play\', methods=[\'POST\'])')
    if insertion_point == -1:
        print("Could not find the play_song API endpoint")
        exit(1)
    
    # Create new content with function moved
    new_content = content[:insertion_point] + '\n' + play_from_queue_func + '\n\n' + content[insertion_point:]
    
    # Remove the original function to avoid duplication
    # We need to find the position in the new_content after we've already added the function
    original_start = new_content.rfind('async def play_from_queue')  # Use rfind to find the last occurrence
    if original_start == -1:
        print("Could not find the original function to remove")
        exit(1)
        
    original_end = new_content.rfind('        logger.warning(f"No songs in queue for guild {guild_id} in play_from_queue")')
    if original_end == -1:
        print("Could not find the end of the original function to remove")
        exit(1)
        
    original_end += 86  # Add length of the line
    
    # Create the final content by removing the original function
    final_content = new_content[:original_start] + new_content[original_end:]
    
    # Write the updated content
    with open('bot_fixed.py', 'w', encoding='utf-8') as f:
        f.write(final_content)
    
    print("Function moved successfully and original removed. New file created as bot_fixed.py")
    
except Exception as e:
    print(f"Error: {str(e)}") 