import os
import sys

# Add app and root to python path
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.join(current_dir, "..")
app_dir = os.path.join(root_dir, "app")
mcp_dir = os.path.join(root_dir, "mcp_servers")

for d in [root_dir, app_dir, mcp_dir]:
    if d not in sys.path:
        sys.path.insert(0, d)

from app import app

# Vercel looks for 'app' or WSGI callable
app = app
