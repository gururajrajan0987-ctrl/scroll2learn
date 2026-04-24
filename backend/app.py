# ── Imports ────────────────────────────────────────────────────────────────
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import os, hashlib, secrets, time, json, random, smtplib
import psycopg2
from psycopg2.extras import RealDictCursor
from flask_socketio import SocketIO, emit, join_room, leave_room
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import cloudinary
import cloudinary.uploader
import cloudinary.api
from dotenv import load_dotenv

# ── Load .env FIRST so all os.getenv() calls below pick up values ──────────
load_dotenv()

# ── Cloudinary configuration (reads from environment variables) ─────────────
cloudinary.config(
    cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key    = os.getenv('CLOUDINARY_API_KEY'),
    api_secret = os.getenv('CLOUDINARY_API_SECRET')
)

# ── Gmail credentials ───────────────────────────────────────────────────────
GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_PASS = os.getenv('GMAIL_PASS')

# ── Gemini AI ───────────────────────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types
    GEMINI_KEY = os.getenv('GEMINI_KEY')
    gemini_client = genai.Client(api_key=GEMINI_KEY)
    GEMINI_OK = True
    print('✅ Gemini AI loaded')
except Exception as e:
    GEMINI_OK = False
    print(f'⚠️  Gemini AI not available: {e}')

app = Flask(__name__)
CORS(app, supports_credentials=True, origins=[
    'https://scroll2learn.netlify.app',
    'https://scroll2learn.vercel.app',
    'http://localhost:3000',
    'http://localhost:5500',
    'http://127.0.0.1:5500'
])

# ── WebSocket configuration ──────────────────────────────────────────────────
# Force websocket transport and relax CORS for SocketIO to avoid Render handshake issues
socketio = SocketIO(app, 
    cors_allowed_origins=[
        'https://scroll2learn.netlify.app',
        'https://scroll2learn.vercel.app',
        'http://localhost:3000',
        'http://localhost:5500',
        'http://127.0.0.1:5500'
    ],
    async_mode='gevent',
    logger=True, 
    engineio_logger=True,
    ping_timeout=60,
    ping_interval=25
)

DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

SECRET_KEY = os.getenv('SECRET_KEY', 'scroll2learn_secret_key')
online_users = set()

app.config['SECRET_KEY'] = SECRET_KEY

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm', 'mov'}
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'guru')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '2005')

app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

def get_db():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"❌ DATABASE CONNECTION ERROR: {e}")
        if not DATABASE_URL:
            print("⚠️  CRITICAL: DATABASE_URL is MISSING! Using non-persistent DB fallback.")
        return None

def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def generate_token(): return secrets.token_hex(32)
def allowed_file(f): return '.' in f and f.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def time_ago(ts):
    try:
        if isinstance(ts, datetime):
            dt = ts
        elif isinstance(ts, str):
            # Try multiple formats for robustness
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
                try:
                    dt = datetime.strptime(ts, fmt)
                    break
                except ValueError:
                    continue
            else:
                return 'recently'
        else:
            return 'recently'
        diff = datetime.utcnow() - dt
        secs = int(diff.total_seconds())
        if secs < 0: return 'just now'  # future timestamps from timezone drift
        if secs < 60: return 'just now'
        if secs < 3600: return f'{secs//60}m ago'
        if diff.days < 1: return f'{secs//3600}h ago'
        if diff.days < 7: return f'{diff.days}d ago'
        return dt.strftime('%b %d')
    except Exception:
        return 'recently'

def get_current_user(req):
    auth = req.headers.get('Authorization', '')
    if not auth.startswith('Bearer '): return None
    token = auth[7:]
    conn = get_db()
    if not conn: return None
    curr = conn.cursor()
    curr.execute('SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token=%s', (token,))
    row = curr.fetchone()
    conn.close()
    return dict(row) if row else None

def serialize_user(u):
    return {'id': u['id'], 'username': u['username'], 'email': u['email'],
            'full_name': u.get('full_name') or '', 'bio': u.get('bio') or '',
            'avatar': u.get('avatar') or '', 'website': u.get('website') or '',
            'is_setup': bool(u.get('is_setup')), 'is_admin': bool(u.get('is_admin', 0)),
            'profession': u.get('profession') or 'College',
            'online': u['id'] in online_users,
            'interests': json.loads(u.get('interests') or '[]'),
            'followers_count': u.get('followers_count', 0),
            'following_count': u.get('following_count', 0)}

def format_post(d, liked=False, saved=False):
    media = d.get('media_url', '')
    avatar = d.get('avatar', '')
    return {'id': d['id'], 'type': d.get('type', 'post'), 'title': d.get('title', ''),
            'description': d.get('description', ''), 'media_url': media,
            'hashtags': json.loads(d.get('hashtags', '[]')) if isinstance(d.get('hashtags'), str) else [],
            'likes_count': d.get('likes_count', 0), 'comments_count': d.get('comments_count', 0),
            'liked': liked, 'saved': saved, 'author': d.get('username', ''),
            'author_id': d.get('user_id'),
            'is_following': bool(d.get('is_following', 0)),
            'full_name': d.get('full_name', '') or d.get('username', ''),
            'avatar': avatar, 'time': time_ago(d.get('created_at', '')),
            'is_approved': bool(d.get('is_approved', 0)),
            'rejection_reason': d.get('rejection_reason', ''),
            'domain': d.get('domain', '')}

def init_db():
    conn = get_db()
    if not conn: return
    c = conn.cursor()
    # Create Tables
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY, 
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL, 
        password_hash TEXT NOT NULL,
        full_name TEXT DEFAULT '', 
        bio TEXT DEFAULT '', 
        avatar TEXT DEFAULT '',
        website TEXT DEFAULT '', 
        is_setup INTEGER DEFAULT 0, 
        is_admin INTEGER DEFAULT 0,
        interests TEXT DEFAULT '[]',
        profession TEXT DEFAULT 'College',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        id SERIAL PRIMARY KEY, 
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token TEXT UNIQUE NOT NULL, 
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id SERIAL PRIMARY KEY, 
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        type TEXT NOT NULL DEFAULT 'post', 
        title TEXT DEFAULT '',
        description TEXT DEFAULT '', 
        media_url TEXT NOT NULL, 
        hashtags TEXT DEFAULT '[]',
        likes_count INTEGER DEFAULT 0, 
        comments_count INTEGER DEFAULT 0,
        is_approved INTEGER DEFAULT 0, 
        rejection_reason TEXT DEFAULT '',
        domain TEXT DEFAULT '',
        target_profession TEXT DEFAULT '["School", "College", "Working"]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS likes (
        id SERIAL PRIMARY KEY, 
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, 
        post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
        UNIQUE(user_id, post_id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS comments (
        id SERIAL PRIMARY KEY, 
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, 
        post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        text TEXT NOT NULL, 
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS saves (
        id SERIAL PRIMARY KEY, 
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, 
        post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
        UNIQUE(user_id, post_id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY, 
        sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, 
        receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        text TEXT NOT NULL, 
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS otp_requests (
        id SERIAL PRIMARY KEY, 
        email TEXT NOT NULL, 
        otp TEXT NOT NULL,
        expires_at TIMESTAMP NOT NULL, 
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS followers (
        id SERIAL PRIMARY KEY, 
        follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, 
        following_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(follower_id, following_id))''')

    # Ensure columns exist (migrations)
    try:
        c.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_read INTEGER DEFAULT 0")
    except: pass

    c.execute("SELECT id FROM users WHERE username=%s", (ADMIN_USERNAME,))
    existing = c.fetchone()
    pass_hash = hash_password(ADMIN_PASSWORD)
    if existing:
        c.execute("UPDATE users SET password_hash=%s, is_admin=1, is_setup=1, full_name='Admin Guru' WHERE username=%s",
                  (pass_hash, ADMIN_USERNAME))
    else:
        try:
            c.execute("INSERT INTO users (username,email,password_hash,full_name,is_admin,is_setup) VALUES (%s,%s,%s,%s,1,1)",
                      (ADMIN_USERNAME, 'admin@scroll2learn.com', pass_hash, 'Admin Guru'))
        except: pass
    conn.commit()
    conn.close()

init_db()

# AUTH
def send_otp_email(recipient_email, otp):
    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = recipient_email
    msg['Subject'] = 'Your Scroll2Learn Verification Code'
    body = f"Hello!\\n\\nYour verification code is: {otp}\\n\\nThis code will expire in 5 minutes.\\n\\nStay curious,\\nScroll2Learn Team"
    msg.attach(MIMEText(body, 'plain'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False

@app.route('/auth/request-otp', methods=['POST'])
def request_otp():
    d = request.get_json()
    email = d.get('email', '').strip().lower()
    username = d.get('username', '').strip().lower()
    
    if not email or not username: return jsonify({'error': 'Email and username required'}), 400
    
    conn = get_db()
    curr = conn.cursor()
    curr.execute("SELECT id FROM users WHERE email=%s OR username=%s", (email, username))
    existing = curr.fetchone()
    if existing:
        conn.close()
        return jsonify({'error': 'Username or email already taken'}), 409
        
    otp = str(random.randint(100000, 999999))
    expires_at = (datetime.now() + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    
    # cleanup old otps for this email
    curr = conn.cursor()
    curr.execute("DELETE FROM otp_requests WHERE email=%s", (email,))
    curr.execute("INSERT INTO otp_requests (email, otp, expires_at) VALUES (%s, %s, %s)", (email, otp, expires_at))
    conn.commit()
    conn.close()
    
    success = send_otp_email(email, otp)
    if not success:
        print(f"Warning: Failed to send real email. The OTP for {email} is {otp}")

    return jsonify({'message': 'OTP sent successfully'})

@app.route('/auth/register', methods=['POST'])
def register():
    d = request.get_json()
    username = d.get('username','').strip().lower()
    email = d.get('email','').strip().lower()
    password = d.get('password','')
    otp = d.get('otp', '').strip()
    
    if not username or not email or not password or not otp: 
        return jsonify({'error':'All fields and OTP required'}),400
    if len(password)<6: return jsonify({'error':'Password must be at least 6 chars'}),400
    if username==ADMIN_USERNAME: return jsonify({'error':'Username not available'}),409
    
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    curr = conn.cursor()
    curr.execute("SELECT * FROM otp_requests WHERE email=%s AND otp=%s AND expires_at > %s", (email, otp, now_str))
    otp_record = curr.fetchone()
    
    if not otp_record:
        conn.close()
        return jsonify({'error': 'Invalid or expired OTP'}), 400

    try:
        curr.execute('INSERT INTO users (username,email,password_hash) VALUES (%s,%s,%s)',(username,email,hash_password(password)))
        curr.execute('DELETE FROM otp_requests WHERE email=%s', (email,)) # Clear OTP
        conn.commit()
        curr.execute('SELECT * FROM users WHERE username=%s',(username,))
        user = curr.fetchone()
        token = generate_token()
        curr.execute('INSERT INTO sessions (user_id,token) VALUES (%s,%s)',(user['id'],token))
        conn.commit(); conn.close()
        return jsonify({'token':token,'user':serialize_user(dict(user)),'is_new':True})
    except psycopg2.IntegrityError:
        conn.close(); return jsonify({'error':'Username or email already taken'}),409

@app.route('/auth/login', methods=['POST'])
def login():
    d = request.get_json()
    identifier = d.get('identifier','').strip().lower()
    password = d.get('password','')
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('SELECT * FROM users WHERE (username=%s OR email=%s) AND password_hash=%s',
                        (identifier,identifier,hash_password(password)))
    user = curr.fetchone()
    if not user: conn.close(); return jsonify({'error':'Invalid username or password'}),401
    token = generate_token()
    curr.execute('INSERT INTO sessions (user_id,token) VALUES (%s,%s)',(user['id'],token))
    conn.commit(); conn.close()
    return jsonify({'token':token,'user':serialize_user(dict(user)),'is_new':not user['is_setup']})

@app.route('/auth/me', methods=['GET'])
def me():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    return jsonify({'user':serialize_user(user)})

@app.route('/auth/logout', methods=['POST'])
def logout():
    auth = request.headers.get('Authorization','')
    if auth.startswith('Bearer '):
        conn = get_db()
        if conn:
            curr = conn.cursor()
            curr.execute('DELETE FROM sessions WHERE token=%s',(auth[7:],))
            conn.commit(); conn.close()
    return jsonify({'message':'ok'})

# PROFILE
@app.route('/profile/setup', methods=['PUT'])
def setup_profile():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    full_name = request.form.get('full_name','').strip()
    bio = request.form.get('bio','').strip()
    website = request.form.get('website','').strip()
    interests = request.form.get('interests','[]')
    profession = request.form.get('profession','College').strip()
    avatar_url = user.get('avatar','')
    if 'avatar' in request.files:
        f = request.files['avatar']
        if f and allowed_file(f.filename):
            try:
                result = cloudinary.uploader.upload(f, resource_type="auto")
                avatar_url = result.get('secure_url')
            except Exception as e:
                return jsonify({'error': f'Cloudinary upload failed: {str(e)}'}), 500
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('UPDATE users SET full_name=%s,bio=%s,website=%s,avatar=%s,interests=%s,profession=%s,is_setup=1 WHERE id=%s',
                 (full_name,bio,website,avatar_url,interests,profession,user['id']))
    conn.commit()
    curr.execute('SELECT * FROM users WHERE id=%s',(user['id'],))
    updated = curr.fetchone()
    conn.close(); return jsonify({'user':serialize_user(dict(updated))})

@app.route('/profile/profession', methods=['PUT'])
def update_profession():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    data = request.json or {}
    prof = data.get('profession', 'College').strip()
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('UPDATE users SET profession=%s WHERE id=%s', (prof, user['id']))
    conn.commit()
    curr.execute('SELECT * FROM users WHERE id=%s',(user['id'],))
    updated = curr.fetchone()
    conn.close(); return jsonify({'user':serialize_user(dict(updated))})

@app.route('/profile/stats', methods=['GET'])
def profile_stats():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("SELECT COUNT(*) FROM posts WHERE user_id=%s AND type='post'",(user['id'],))
    posts = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM posts WHERE user_id=%s AND type='reel'",(user['id'],))
    reels = curr.fetchone()['count']
    curr.execute("SELECT COALESCE(SUM(likes_count),0) FROM posts WHERE user_id=%s",(user['id'],))
    likes = curr.fetchone()['coalesce']
    curr.execute("SELECT COUNT(*) FROM saves WHERE user_id=%s",(user['id'],))
    saved = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM posts WHERE user_id=%s AND is_approved=0 AND rejection_reason=''",(user['id'],))
    pending = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM followers WHERE following_id=%s", (user['id'],))
    followers_count = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM followers WHERE follower_id=%s", (user['id'],))
    following_count = curr.fetchone()['count']
    conn.close(); return jsonify({'posts':posts,'reels':reels,'likes':likes,'saved':saved,'pending':pending, 'followers': followers_count, 'following': following_count})

# FEED
@app.route('/feed', methods=['GET'])
def get_feed():
    user = get_current_user(request)
    page = max(1,int(request.args.get('page',1)))
    per_page = min(20,int(request.args.get('per_page',10)))
    offset = (page-1)*per_page
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    # Recommended feed priorities:
    # 1. Users current user follows
    # 2. Domains matching user's interests or liked domains
    # 3. Overall popularity (likes/comments)
    # 4. Recency
    # Recommended feed priorities:
    # 1. Users current user follows
    # 2. Domains matching user's interests or liked domains
    # 3. Overall popularity (likes/comments)
    # 4. Recency
    if user:
        user_interests = json.loads(user.get('interests') or '[]')
        query = """
            SELECT p.*, u.username, u.full_name, u.avatar,
                   (CASE WHEN f.id IS NOT NULL THEN 1 ELSE 0 END) as is_following,
                   (CASE WHEN p.domain = ANY(%s) THEN 1 ELSE 0 END) as interest_priority
            FROM posts p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN followers f ON (f.following_id = p.user_id AND f.follower_id = %s)
            WHERE p.is_approved = 1
            ORDER BY is_following DESC, interest_priority DESC, p.likes_count DESC, p.created_at DESC
            LIMIT %s OFFSET %s
        """
        curr.execute(query, (user_interests, user['id'], per_page, offset))
    else:
        curr.execute("SELECT p.*, u.username, u.full_name, u.avatar FROM posts p JOIN users u ON p.user_id = u.id WHERE p.is_approved=1 ORDER BY p.created_at DESC LIMIT %s OFFSET %s", (per_page, offset))
    
    rows = curr.fetchall()
    curr.execute('SELECT COUNT(*) FROM posts WHERE is_approved=1')
    total = curr.fetchone()['count']
    result = []
    for row in rows:
        d = dict(row); liked=saved=False
        if user:
            curr.execute('SELECT 1 FROM likes WHERE user_id=%s AND post_id=%s',(user['id'],d['id']))
            liked = bool(curr.fetchone())
            curr.execute('SELECT 1 FROM saves WHERE user_id=%s AND post_id=%s',(user['id'],d['id']))
            saved = bool(curr.fetchone())
        result.append(format_post(d,liked,saved))
    conn.close()
    return jsonify({'posts':result,'page':page,'has_more':offset+per_page<total,'total':total})

@app.route('/posts', methods=['POST'])
def create_post():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    post_type = request.form.get('type','post')
    title = request.form.get('title','').strip()
    description = request.form.get('description','').strip()
    hashtags = request.form.get('hashtags','[]')
    domain = request.form.get('domain','').strip()
    target_profession = request.form.get('target_profession', '["School", "College", "Working"]')
    if not title or not description or not domain:
        return jsonify({'error':'Title, description, and domain are mandatory'}),400
    f = request.files.get('media')
    if not f or not allowed_file(f.filename): return jsonify({'error':'Valid media file required'}),400
    
    try:
        result = cloudinary.uploader.upload(f, resource_type="auto")
        media_url = result.get('secure_url')
    except Exception as e:
        return jsonify({'error': f'Cloudinary upload failed: {str(e)}'}), 500
    
    is_approved = 1 if user.get('is_admin') else 0  # Only admin posts auto-approved
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('INSERT INTO posts (user_id,type,title,description,media_url,hashtags,is_approved,domain,target_profession) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                     (user['id'],post_type,title,description,media_url,hashtags,is_approved,domain,target_profession))
    post_id = curr.fetchone()['id']
    conn.commit()
    curr.execute('SELECT p.*,u.username,u.full_name,u.avatar FROM posts p JOIN users u ON p.user_id=u.id WHERE p.id=%s',(post_id,))
    row = curr.fetchone()
    conn.close()
    return jsonify({'post':format_post(dict(row)),'pending':not bool(is_approved)}),201

@app.route('/posts/<int:pid>/like', methods=['POST'])
def toggle_like(pid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('SELECT 1 FROM likes WHERE user_id=%s AND post_id=%s',(user['id'],pid))
    exists = curr.fetchone()
    if exists:
        curr.execute('DELETE FROM likes WHERE user_id=%s AND post_id=%s',(user['id'],pid))
        curr.execute('UPDATE posts SET likes_count=GREATEST(0,likes_count-1) WHERE id=%s',(pid,)); liked=False
    else:
        curr.execute('INSERT INTO likes (user_id,post_id) VALUES (%s,%s)',(user['id'],pid))
        curr.execute('UPDATE posts SET likes_count=likes_count+1 WHERE id=%s',(pid,)); liked=True
    conn.commit()
    curr.execute('SELECT likes_count FROM posts WHERE id=%s',(pid,))
    count = curr.fetchone()['likes_count']
    conn.close(); return jsonify({'liked':liked,'likes_count':count})

@app.route('/posts/<int:pid>/save', methods=['POST'])
def toggle_save(pid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('SELECT 1 FROM saves WHERE user_id=%s AND post_id=%s',(user['id'],pid))
    exists = curr.fetchone()
    if exists: 
        curr.execute('DELETE FROM saves WHERE user_id=%s AND post_id=%s',(user['id'],pid)); saved=False
    else: 
        curr.execute('INSERT INTO saves (user_id,post_id) VALUES (%s,%s)',(user['id'],pid)); saved=True
    conn.commit(); conn.close(); return jsonify({'saved':saved})

@app.route('/posts/<int:pid>/comments', methods=['GET'])
def get_comments(pid):
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('SELECT c.*,u.username,u.avatar FROM comments c JOIN users u ON c.user_id=u.id WHERE c.post_id=%s ORDER BY c.created_at DESC',(pid,))
    rows = curr.fetchall()
    conn.close()
    return jsonify([{'id':r['id'],'text':r['text'],'username':r['username'],'avatar':r['avatar'] or '','time':time_ago(str(r['created_at']))} for r in rows])

@app.route('/posts/<int:pid>/comments', methods=['POST'])
def add_comment(pid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    text = (request.get_json() or {}).get('text','').strip()
    if not text: return jsonify({'error':'Empty'}),400
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('INSERT INTO comments (user_id,post_id,text) VALUES (%s,%s,%s)',(user['id'],pid,text))
    curr.execute('UPDATE posts SET comments_count=comments_count+1 WHERE id=%s',(pid,))
    conn.commit(); conn.close()
    return jsonify({'message':'ok','username':user['username']}),201

@app.route('/posts/<int:pid>', methods=['DELETE'])
def delete_post(pid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('SELECT * FROM posts WHERE id=%s',(pid,))
    post = curr.fetchone()
    if not post: conn.close(); return jsonify({'error':'Post not found'}),404
    if post['user_id'] != user['id'] and not user.get('is_admin'):
        conn.close(); return jsonify({'error':'Not allowed'}),403
    curr.execute('DELETE FROM likes WHERE post_id=%s',(pid,))
    curr.execute('DELETE FROM comments WHERE post_id=%s',(pid,))
    curr.execute('DELETE FROM saves WHERE post_id=%s',(pid,))
    curr.execute('DELETE FROM posts WHERE id=%s',(pid,))
    conn.commit(); conn.close()
    return jsonify({'message':'Deleted'})

@app.route('/profile/posts', methods=['GET'])
def user_posts():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('''SELECT p.*,u.username,u.full_name,u.avatar FROM posts p
        JOIN users u ON p.user_id=u.id WHERE p.user_id=%s ORDER BY p.created_at DESC''',(user['id'],))
    rows = curr.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        curr.execute('SELECT 1 FROM likes WHERE user_id=%s AND post_id=%s',(user['id'],d['id']))
        liked = bool(curr.fetchone())
        curr.execute('SELECT 1 FROM saves WHERE user_id=%s AND post_id=%s',(user['id'],d['id']))
        saved = bool(curr.fetchone())
        result.append(format_post(d,liked,saved))
    conn.close()
    return jsonify({'posts':result})

# ADMIN
def require_admin(req):
    user = get_current_user(req)
    return user if user and user.get('is_admin') else None

@app.route('/admin/pending', methods=['GET'])
def admin_pending():
    if not require_admin(request): return jsonify({'error':'Admin only'}),403
    page=max(1,int(request.args.get('page',1))); per_page=20; offset=(page-1)*per_page
    conn=get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("SELECT p.*,u.username,u.full_name,u.avatar FROM posts p JOIN users u ON p.user_id=u.id WHERE p.is_approved=0 AND p.rejection_reason='' ORDER BY p.created_at ASC LIMIT %s OFFSET %s",(per_page,offset))
    rows = curr.fetchall()
    curr.execute("SELECT COUNT(*) FROM posts WHERE is_approved=0 AND rejection_reason=''"  )
    total = curr.fetchone()['count']
    conn.close(); return jsonify({'posts':[format_post(dict(r)) for r in rows],'total':total})

@app.route('/admin/posts/<int:pid>/approve', methods=['POST'])
def admin_approve(pid):
    if not require_admin(request): return jsonify({'error':'Admin only'}),403
    conn=get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("UPDATE posts SET is_approved=1,rejection_reason='' WHERE id=%s",(pid,))
    conn.commit(); conn.close()
    return jsonify({'message':'Approved'})

@app.route('/admin/posts/<int:pid>/reject', methods=['POST'])
def admin_reject(pid):
    if not require_admin(request): return jsonify({'error':'Admin only'}),403
    reason=(request.get_json() or {}).get('reason','Does not meet guidelines')
    conn=get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute('UPDATE posts SET is_approved=0, rejection_reason=%s WHERE id=%s', (reason, pid))
    conn.commit(); conn.close()
    return jsonify({'message':'rejected'})

@app.route('/admin/users', methods=['GET'])
def admin_users():
    user = get_current_user(request)
    if not user or not user.get('is_admin'): return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("SELECT id, username, email, full_name, is_admin, created_at FROM users ORDER BY created_at DESC")
    u_list = curr.fetchall()
    conn.close()
    return jsonify({'users': [dict(u) for u in u_list]})

@app.route('/admin/users/<int:uid>', methods=['DELETE'])
def admin_delete_user(uid):
    user = get_current_user(request)
    if not user or not user.get('is_admin'): return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    # CASCADE on foreign keys handles related records automatically
    curr.execute('DELETE FROM sessions WHERE user_id=%s', (uid,))
    curr.execute('DELETE FROM users WHERE id=%s', (uid,))
    conn.commit(); conn.close()
    return jsonify({'success':True})

@app.route('/admin/posts/<int:pid>/edit', methods=['PUT'])
def admin_edit_post(pid):
    if not require_admin(request): return jsonify({'error':'Admin only'}),403
    d = request.get_json() or {}
    title = d.get('title','').strip()
    description = d.get('description','').strip()
    domain = d.get('domain','').strip()
    if not title or not description or not domain:
        return jsonify({'error':'Title, description, and domain cannot be empty'}),400
    
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("UPDATE posts SET title=%s, description=%s, domain=%s WHERE id=%s", (title, description, domain, pid))
    conn.commit()
    conn.close()
    return jsonify({'ok':True})

@app.route('/admin/stats', methods=['GET'])
def admin_stats():
    if not require_admin(request): return jsonify({'error':'Admin only'}),403
    conn=get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("SELECT COUNT(*) FROM posts WHERE type='post'"); p_count = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM posts WHERE type='reel'"); r_count = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM posts WHERE is_approved=0 AND rejection_reason=''"); pen_count = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM posts WHERE is_approved=1"); app_count = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM posts WHERE is_approved=0 AND rejection_reason!=''"); rej_count = curr.fetchone()['count']
    curr.execute("SELECT COUNT(*) FROM users WHERE is_admin=0"); u_count = curr.fetchone()['count']
    curr.execute("SELECT COALESCE(SUM(likes_count),0) FROM posts"); l_count = curr.fetchone()['coalesce']
    curr.execute("SELECT COUNT(*) FROM comments"); c_count = curr.fetchone()['count']
    
    stats={
        'posts': p_count,
        'reels': r_count,
        'pending': pen_count,
        'approved': app_count,
        'rejected': rej_count,
        'users': u_count,
        'likes': l_count,
        'comments': c_count,
        'db_type': 'postgres' if DATABASE_URL and 'postgresql' in DATABASE_URL else 'sqlite',
        'ai_ok': GEMINI_OK
    }
    conn.close(); return jsonify(stats)

# SEARCH
@app.route('/search', methods=['GET'])
def search():
    q = request.args.get('q','').strip()
    if not q: return jsonify({'posts':[], 'users':[]})
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    qq = f"%{q}%"
    curr.execute("SELECT id, username, full_name, avatar FROM users WHERE username ILIKE %s OR full_name ILIKE %s LIMIT 15", (qq, qq))
    users = curr.fetchall()
    curr.execute("SELECT p.id, p.title, p.description, p.media_url, u.username as author, p.created_at as time FROM posts p JOIN users u ON p.user_id = u.id WHERE p.is_approved=1 AND (p.title ILIKE %s OR p.description ILIKE %s OR p.hashtags ILIKE %s OR u.username ILIKE %s) LIMIT 20", (qq, qq, qq, qq))
    posts = curr.fetchall()
    conn.close()
    return jsonify({'users': [dict(u) for u in users], 'posts': [dict(p) for p in posts]})

@app.route('/chat/recent', methods=['GET'])
def get_recent_chats():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    # Recently messaged users with unread counts
    curr.execute("""
        SELECT u.id, u.username, u.full_name, u.avatar, MAX(m.created_at) as last_msg,
               COUNT(m.id) FILTER (WHERE m.receiver_id = %s AND m.is_read = 0) as unread_count
        FROM users u
        JOIN messages m ON (u.id = m.sender_id OR u.id = m.receiver_id)
        WHERE (m.sender_id = %s OR m.receiver_id = %s) AND u.id != %s
        GROUP BY u.id, u.username, u.full_name, u.avatar
        ORDER BY last_msg DESC
        LIMIT 30
    """, (user['id'], user['id'], user['id'], user['id']))
    users = curr.fetchall()
    conn.close()
    
    formatted = []
    for u in users:
        d = dict(u)
        d['online'] = d['id'] in online_users
        d['unread_count'] = int(d.get('unread_count', 0))
        d['last_msg'] = str(d.get('last_msg', ''))
        formatted.append(d)
        
    return jsonify({'users': formatted})

@app.route('/users/suggested', methods=['GET'])
def get_suggested_users():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    # Random users excluding self and those already messaged
    curr.execute("""
        SELECT id, username, full_name, avatar 
        FROM users 
        WHERE id != %s 
        AND id NOT IN (
            SELECT DISTINCT CASE 
                WHEN sender_id = %s THEN receiver_id 
                ELSE sender_id 
            END 
            FROM messages 
            WHERE sender_id = %s OR receiver_id = %s
        )
        ORDER BY RANDOM() 
        LIMIT 10
    """, (user['id'], user['id'], user['id'], user['id']))
    users = curr.fetchall()
    conn.close()
    
    formatted = []
    for u in users:
        d = dict(u)
        d['online'] = d['id'] in online_users
        d['unread_count'] = 0
        formatted.append(d)
        
    return jsonify({'users': formatted})

@app.route('/chat/users', methods=['GET'])
def get_chat_users():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("SELECT id, username, full_name, avatar FROM users WHERE id != %s ORDER BY id DESC", (user['id'],))
    users = curr.fetchall()
    conn.close()
    return jsonify({'users': [dict(u) for u in users]})

@app.route('/chat/<int:uid>', methods=['GET'])
def get_chat_messages(uid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("SELECT * FROM messages WHERE (sender_id=%s AND receiver_id=%s) OR (sender_id=%s AND receiver_id=%s) ORDER BY created_at ASC", (user['id'], uid, uid, user['id']))
    msgs = curr.fetchall()
    conn.close()
    # Serialize datetime objects for JSON
    messages_list = []
    for m in msgs:
        md = dict(m)
        md['created_at'] = str(md['created_at'])
        messages_list.append(md)
    return jsonify({'messages': messages_list})

@app.route('/chat/<int:uid>', methods=['POST'])
def send_chat_message(uid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    text = request.get_json().get('text','').strip()
    if not text: return jsonify({'error':'Text required'}), 400
    conn = get_db()
    curr = conn.cursor()
    curr.execute("INSERT INTO messages (sender_id, receiver_id, text) VALUES (%s,%s,%s) RETURNING id, created_at", (user['id'], uid, text))
    msg_data = curr.fetchone()
    conn.commit(); conn.close()
    
    # WebSocket: Emit to both sender and receiver rooms
    msg_obj = {
        'id': msg_data['id'],
        'sender_id': user['id'],
        'receiver_id': uid,
        'text': text,
        'created_at': str(msg_data['created_at'])
    }
    socketio.emit('new_message', msg_obj, room=f"user_{uid}")
    socketio.emit('new_message', msg_obj, room=f"user_{user['id']}")
    
    return jsonify({'success': True, 'message': msg_obj})

@app.route('/chat/<int:uid>/read', methods=['POST'])
def mark_read(uid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    conn = get_db()
    curr = conn.cursor()
    curr.execute("UPDATE messages SET is_read = 1 WHERE sender_id = %s AND receiver_id = %s AND is_read = 0", (uid, user['id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ── SocketIO Events ──────────────────────────────────────────────────────────
@socketio.on('join')
def on_join(data):
    user_id = data.get('user_id')
    if user_id:
        join_room(f"user_{user_id}")
        online_users.add(user_id)
        emit('user_status', {'user_id': user_id, 'status': 'online'}, broadcast=True)
        print(f"User {user_id} joined room")

@socketio.on('disconnect')
def on_disconnect():
    # We don't have user_id here directly, but we can manage it via session or by tracking sids
    # For now, we'll use a simple approach: if user explicitly leaves or on disconnect we cleanup
    pass

@socketio.on('leave')
def on_leave(data):
    user_id = data.get('user_id')
    if user_id in online_users:
        online_users.remove(user_id)
        emit('user_status', {'user_id': user_id, 'status': 'offline'}, broadcast=True)

@socketio.on('typing')
def on_typing(data):
    recipient_id = data.get('recipient_id')
    sender_id = data.get('sender_id')
    if recipient_id:
        emit('typing', {'sender_id': sender_id}, room=f"user_{recipient_id}")

# AI CHAT
@app.route('/ai/chat', methods=['POST'])
def ai_chat():
    if not GEMINI_OK:
        return jsonify({'error':'AI service not available'}),503
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    d = request.get_json() or {}
    message = d.get('message','').strip()
    history = d.get('history', [])
    if not message: return jsonify({'error':'Message required'}),400

    # Build conversation contents
    contents = []
    for h in history:
        contents.append(types.Content(
            role=h.get('role','user'),
            parts=[types.Part.from_text(text=h.get('text',''))]
        ))
    contents.append(types.Content(
        role='user',
        parts=[types.Part.from_text(text=message)]
    ))

    try:
        config = types.GenerateContentConfig(
            system_instruction=[
                types.Part.from_text(text="""You are a helpful and professional AI assistant for scroll2learn, an educational social media platform.
Goal: Help users learn and solve doubts about any educational topic including programming, competitive exams (UPSC, TNPSC, GATE), AI, data science, web development, and more.
Tone: Friendly, encouraging, and concise.
Constraint: If a user asks a question unrelated to education or learning, politely guide them back to the platform's main purpose.
Formatting: Use bullet points for lists. Use **bold** for key terms. Keep answers focused and practical.""")
            ],
        )
        # Try models in order of preference
        models_to_try = [
            'gemini-2.0-flash-lite',
            'gemini-2.0-flash',
            'gemini-flash-latest', # Replaces 1.5-flash
            'gemini-2.5-flash',    # Future proofing
            'gemini-pro-latest'
        ]
        last_err = None
        for model_name in models_to_try:
            try:
                response = gemini_client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                return jsonify({'reply': response.text})
            except Exception as model_err:
                last_err = model_err
                err_msg = str(model_err)
                # Retry if rate limited (429) OR if model not found (404) to move to the next fallback
                if '429' in err_msg or '404' in err_msg or 'NOT_FOUND' in err_msg or 'RESOURCE_EXHAUSTED' in err_msg:
                    continue 
                break # Hard failure for other errors
        # If all models failed
        err_str = str(last_err)
        if '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
            return jsonify({'error': 'AI is temporarily busy. Please try again in about a minute.'}), 429
        return jsonify({'error': f'AI error: {err_str}'}), 500
    except Exception as e:
        return jsonify({'error': f'AI error: {str(e)}'}), 500


# FOLLOW SYSTEM
@app.route('/follow/<int:uid>', methods=['POST', 'DELETE'])
def follow_user(uid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    if user['id'] == uid: return jsonify({'error':'Cannot follow yourself'}),400
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    if request.method == 'POST':
        try:
            curr.execute("INSERT INTO followers (follower_id, following_id) VALUES (%s, %s)", (user['id'], uid))
            conn.commit()
            return jsonify({'message': 'Followed'})
        except: return jsonify({'error': 'Already following'}), 400
    else:
        curr.execute("DELETE FROM followers WHERE follower_id=%s AND following_id=%s", (user['id'], uid))
        conn.commit()
        return jsonify({'message': 'Unfollowed'})

@app.route('/followers/<int:uid>', methods=['GET'])
def get_followers(uid):
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("SELECT u.id, u.username, u.full_name, u.avatar FROM users u JOIN followers f ON u.id = f.follower_id WHERE f.following_id = %s", (uid,))
    users = curr.fetchall()
    conn.close(); return jsonify({'users': [dict(u) for u in users]})

@app.route('/following/<int:uid>', methods=['GET'])
def get_following(uid):
    conn = get_db()
    if not conn: return jsonify({'error': 'DB Error'}), 500
    curr = conn.cursor()
    curr.execute("SELECT u.id, u.username, u.full_name, u.avatar FROM users u JOIN followers f ON u.id = f.following_id WHERE f.follower_id = %s", (uid,))
    users = curr.fetchall()
    conn.close(); return jsonify({'users': [dict(u) for u in users]})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n>>> Scroll2Learn API running on port {port}")
    print(f">>> Admin login: username={ADMIN_USERNAME}  password={ADMIN_PASSWORD}\n")
    socketio.run(app, debug=False, port=port, host="0.0.0.0", use_reloader=False)