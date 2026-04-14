# Scroll2Learn — Instagram-Style Microlearning Platform

## Project Structure
```
scroll2learn/
├── backend/
│   ├── app.py           ← Flask API server
│   ├── requirements.txt
│   └── uploads/         ← User-uploaded media
└── frontend/
    └── index.html       ← Full SPA (open in browser)
```

## Quick Start

### 1. Install Backend Dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 2. Run Backend
```bash
python app.py
# Runs at http://localhost:5000
```

### 3. Open Frontend
Open `frontend/index.html` directly in your browser.
> The app auto-connects to `http://localhost:5000`

---

## Features Built (Part 1)

### ✅ Authentication
- Sign up with username, email, password
- Log in with email or username
- Secure token-based auth (stored in localStorage)

### ✅ Profile Setup
- Full name, bio, website
- Avatar photo upload
- Shown immediately after first sign-up

### ✅ Home Feed (Instagram UX)
- Stories row at top (click to view, click to add your story)
- Mixed feed: posts + reels
- Infinite scroll (loads more as you scroll down)

### ✅ Stories
- Add your own story (photo/video + caption)
- View others' stories in fullscreen viewer
- Progress bars with auto-advance
- Tap left/right to navigate
- Auto-expires after 24h

### ✅ Posts & Reels
- Like ❤️ (with optimistic UI + animation)
- Comment 💬 (sheet panel with threaded comments)
- Save 🔖 (bookmark)
- Share (copy link, WhatsApp, Twitter)

### ✅ Upload
- Post (image), Reel (video), Story
- Hashtag picker + custom hashtags
- Live media preview before publish

### ✅ Search
- Search posts & users
- Filter by tag chips

### ✅ Profile
- Stats: Posts, Reels, Likes, Saved
- No content grid (as requested)
- Badges display

### ✅ Navigation
- Bottom nav: Home, Reels, Add, Search, Profile
- Reels tab: fullscreen vertical scroll (snap-scroll)
- Notifications panel

---

## API Endpoints
| Method | Route | Description |
|--------|-------|-------------|
| POST | /auth/register | Register new user |
| POST | /auth/login | Login |
| GET | /auth/me | Get current user |
| PUT | /profile/setup | Update profile |
| GET | /feed | Paginated feed |
| POST | /posts | Create post/reel |
| POST | /posts/:id/like | Toggle like |
| GET/POST | /posts/:id/comments | Get/add comments |
| POST | /posts/:id/save | Toggle save |
| GET | /stories | Get active stories |
| POST | /stories | Create story |
| POST | /stories/:id/view | Mark story as viewed |
| GET | /search?q= | Search posts & users |
| GET | /profile/stats | Get user stats |

---

## Tech Stack
- **Backend**: Python Flask, SQLite, Werkzeug
- **Frontend**: Vanilla HTML/CSS/JS (single file SPA)
- **Fonts**: Syne (display) + DM Sans (body)
- **Icons**: Material Icons Round
