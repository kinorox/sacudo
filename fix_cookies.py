import re

# Read the current cookies.txt file
with open('cookies.txt', 'r') as f:
    content = f.read()

# Extract the header
header = '# Netscape HTTP Cookie File'

# Process the rest of the content to fix formatting issues
lines = []
for line in content.split('\n'):
    if line.strip() and not line.startswith('#'):
        # Strip excess whitespace
        clean_line = re.sub(r'\s+', '\t', line.strip())
        # Replace 'false' with 'FALSE' and 'true' with 'TRUE'
        clean_line = clean_line.replace('false', 'FALSE').replace('true', 'TRUE')
        # Make sure we have exactly 6 tabs
        parts = clean_line.split('\t')
        if len(parts) >= 7:  # We have enough parts
            # Keep only the necessary parts and recombine with tabs
            fixed_line = '\t'.join([parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]])
            lines.append(fixed_line)

# Create the new cookies.txt file
with open('fixed_cookies.txt', 'w') as f:
    f.write(header + '\n')
    for line in lines:
        f.write(line + '\n')

print('Created fixed_cookies.txt with proper formatting') 