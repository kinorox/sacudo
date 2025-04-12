import os
import sys
import json
import sqlite3
import browser_cookie3
import http.cookiejar
from pathlib import Path

def extract_cookies_to_file(output_file="cookies.txt"):
    """Extract cookies from browsers and save to file in Netscape format."""
    # Try to get cookies from Chrome first
    try:
        print("Attempting to extract cookies from Chrome...")
        cookie_jar = browser_cookie3.chrome(domain_name=".youtube.com")
        print(f"Found {len(cookie_jar)} cookies from Chrome")
    except Exception as e:
        print(f"Could not extract cookies from Chrome: {e}")
        # Then try Firefox
        try:
            print("Attempting to extract cookies from Firefox...")
            cookie_jar = browser_cookie3.firefox(domain_name=".youtube.com")
            print(f"Found {len(cookie_jar)} cookies from Firefox")
        except Exception as e:
            print(f"Could not extract cookies from Firefox: {e}")
            print("Unable to extract cookies from any browser.")
            return False

    # Convert to Netscape format and save to file
    with open(output_file, 'w') as f:
        f.write("# Netscape HTTP Cookie File\n")
        for cookie in cookie_jar:
            if cookie.domain.endswith('.youtube.com') or cookie.domain.endswith('.google.com'):
                secure_flag = "TRUE" if cookie.secure else "FALSE"
                http_only_flag = "TRUE" if cookie.has_nonstandard_attr('HttpOnly') else "FALSE"
                f.write(f"{cookie.domain}\t{'TRUE' if cookie.domain.startswith('.') else 'FALSE'}\t{cookie.path}\t{secure_flag}\t{cookie.expires if cookie.expires else 0}\t{cookie.name}\t{cookie.value}\n")
    
    print(f"Cookies saved to {output_file}")
    return True

if __name__ == "__main__":
    extract_cookies_to_file() 