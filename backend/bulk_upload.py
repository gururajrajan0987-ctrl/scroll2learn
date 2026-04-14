import requests
import os
import json
import mimetypes
import sys

# Scroll2Learn Bulk Upload Script
# This enhanced version supports directory scanning and automatic media detection.

BASE_URL = "http://localhost:5000"
# Admin user by default
USERNAME = "guru" 
# IMPORTANT: Replace with a valid token from your browser's localStorage (s2l_token)
TOKEN = "dee62895d8f3093b7358b8cf73025c8975762fbbf8d508bbf86d04dd8bbb9194" 

def upload_file(file_path, domain="General", post_type=None):
    """
    Uploads a single file to the Scroll2Learn backend.
    """
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return False

    # Auto-detect type if not provided
    mime_type, _ = mimetypes.guess_type(file_path)
    if not post_type:
        if mime_type and mime_type.startswith('video'):
            post_type = "reel"
        else:
            post_type = "post"

    url = f"{BASE_URL}/posts"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    
    file_name = os.path.basename(file_path)
    title = os.path.splitext(file_name)[0].replace('_', ' ').title()
    description = f"Automated upload of {file_name} to the {domain} section."

    try:
        with open(file_path, 'rb') as f:
            files = {'media': f}
            data = {
                'type': post_type,
                'title': title,
                'description': description,
                'domain': domain,
                'hashtags': json.dumps(["#auto", "#bulk", "#" + domain.lower()])
            }
            
            print(f"🚀 Uploading [{post_type.upper()}] {file_name}...")
            response = requests.post(url, headers=headers, data=data, files=files)
            
            if response.status_code in [200, 201]:
                print(f"✅ Success! {response.json().get('message')}")
                return True
            else:
                print(f"❌ Failed ({response.status_code}): {response.text}")
                return False
    except Exception as e:
        print(f"💥 Error uploading {file_name}: {str(e)}")
        return False

def bulk_upload_dir(directory, domain="General"):
    """
    Scans a directory and uploads all media files found.
    """
    if not os.path.isdir(directory):
        print(f"❌ Directory not found: {directory}")
        return

    print(f"📂 Scanning directory: {directory}")
    valid_exts = ('.mp4', '.mov', '.avi', '.jpg', '.jpeg', '.png', '.webp')
    files = [f for f in os.listdir(directory) if f.lower().endswith(valid_exts)]
    
    if not files:
        print("📭 No media files found in this directory.")
        return

    print(f"📦 Found {len(files)} files. Starting batch upload...\n")
    success_count = 0
    for f in files:
        full_path = os.path.join(directory, f)
        if upload_file(full_path, domain):
            success_count += 1
    
    print(f"\n✨ Done! Successfully uploaded {success_count}/{len(files)} files.")

if __name__ == "__main__":
    if not TOKEN or len(TOKEN) < 10:
        print("🛑 ERROR: Missing or invalid TOKEN.")
        print("Please log in to Scroll2Learn in your browser, open DevTools (F12),")
        print("go to Application -> Local Storage, and copy the 's2l_token' value.")
        sys.exit(1)

    print("="*40)
    print("🌟 SCROLL2LEARN BULK UPLOADER V2.0 🌟")
    print("="*40)

    # USAGE EXAMPLES:
    # 1. Upload a single file:
    # upload_file("path/to/my_video.mp4", domain="Science")
    
    # 2. Upload an entire folder:
    # bulk_upload_dir("path/to/my_media_folder", domain="Python")

    print("\n💡 Tip: Edit this script to call bulk_upload_dir() with your folder path.")
    print("Current configuration:")
    print(f" - Base URL: {BASE_URL}")
    print(f" - Token: {TOKEN[:8]}...{TOKEN[-8:]}\n")

    # Change the path below to your actual media folder
    # bulk_upload_dir(r"C:\Users\Downloads\EducationalContent", domain="AI")
    
    print("Waiting for your instructions in the script code...")
