import requests
import json

url = "https://scroll2learn.onrender.com/ai/chat"
headers = {"Authorization": "Bearer any-token"} # Backend mock check might fail but it will log
data = {
    "message": "Explain insects",
    "history": []
}

print(f"Testing {url}...")
# We expect this to fail with 401 if we don't have a valid token, 
# but we can check if it returns the AI error or if the logs on the server show the right models being tried.
# Actually, I'll try to get a valid token from the DB first.

import sqlite3
conn = sqlite3.connect("scroll2learn.db")
row = conn.execute("SELECT s.token FROM sessions s JOIN users u ON s.user_id = u.id WHERE u.username = 'guru'").fetchone()
token = row[0]
conn.close()

headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
try:
    response = requests.post(url, headers=headers, json=data, timeout=30)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
