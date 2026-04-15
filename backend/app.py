# ── Imports ────────────────────────────────────────────────────────────────
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import sqlite3, os, hashlib, secrets, time, json, random, smtplib
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

# ── Flask app ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, supports_credentials=True)

DATABASE = 'scroll2learn.db'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm', 'mov'}
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'guru')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '2005')

app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
def generate_token(): return secrets.token_hex(32)
def allowed_file(f): return '.' in f and f.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def time_ago(ts):
    try:
        dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
        diff = datetime.utcnow() - dt
        secs = int(diff.total_seconds())
        if secs < 60: return 'just now'
        if secs < 3600: return f'{secs//60}m ago'
        if diff.days < 1: return f'{secs//3600}h ago'
        if diff.days < 7: return f'{diff.days}d ago'
        return dt.strftime('%b %d')
    except: return 'recently'

def get_current_user(req):
    auth = req.headers.get('Authorization', '')
    if not auth.startswith('Bearer '): return None
    token = auth[7:]
    conn = get_db()
    row = conn.execute('SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token=?', (token,)).fetchone()
    conn.close()
    return dict(row) if row else None

def serialize_user(u):
    return {'id': u['id'], 'username': u['username'], 'email': u['email'],
            'full_name': u.get('full_name') or '', 'bio': u.get('bio') or '',
            'avatar': u.get('avatar') or '', 'website': u.get('website') or '',
            'is_setup': bool(u.get('is_setup')), 'is_admin': bool(u.get('is_admin', 0)),
            'interests': json.loads(u.get('interests') or '[]')}

def format_post(d, liked=False, saved=False):
    media = d.get('media_url', '')
    avatar = d.get('avatar', '')
    return {'id': d['id'], 'type': d.get('type', 'post'), 'title': d.get('title', ''),
            'description': d.get('description', ''), 'media_url': media,
            'hashtags': json.loads(d.get('hashtags', '[]')) if isinstance(d.get('hashtags'), str) else [],
            'likes_count': d.get('likes_count', 0), 'comments_count': d.get('comments_count', 0),
            'liked': liked, 'saved': saved, 'author': d.get('username', ''),
            'full_name': d.get('full_name', '') or d.get('username', ''),
            'avatar': avatar, 'time': time_ago(d.get('created_at', '')),
            'is_approved': bool(d.get('is_approved', 0)),
            'rejection_reason': d.get('rejection_reason', ''),
            'domain': d.get('domain', '')}

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
        full_name TEXT DEFAULT '', bio TEXT DEFAULT '', avatar TEXT DEFAULT '',
        website TEXT DEFAULT '', is_setup INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    for col in [('is_admin', 'INTEGER DEFAULT 0'), ('interests', "TEXT DEFAULT '[]'"), ('profession', "TEXT DEFAULT 'College'")]:
        try: c.execute(f"ALTER TABLE users ADD COLUMN {col[0]} {col[1]}"); conn.commit()
        except: pass
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        token TEXT UNIQUE NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        type TEXT NOT NULL DEFAULT 'post', title TEXT DEFAULT '',
        description TEXT DEFAULT '', media_url TEXT NOT NULL, hashtags TEXT DEFAULT '[]',
        likes_count INTEGER DEFAULT 0, comments_count INTEGER DEFAULT 0,
        is_approved INTEGER DEFAULT 0, rejection_reason TEXT DEFAULT '',
        domain TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    for col, typ, dflt in [('is_approved','INTEGER','0'), ('rejection_reason','TEXT',"''"), ('domain','TEXT',"''"), ('target_profession','TEXT',"'[\"School\", \"College\", \"Working\"]'")]:
        try: c.execute(f"ALTER TABLE posts ADD COLUMN {col} {typ} DEFAULT {dflt}"); conn.commit()
        except: pass
    c.execute('''CREATE TABLE IF NOT EXISTS likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, post_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, post_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, post_id INTEGER NOT NULL,
        text TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS saves (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, post_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, post_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id INTEGER NOT NULL, receiver_id INTEGER NOT NULL,
        text TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS otp_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, otp TEXT NOT NULL,
        expires_at TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS stories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        media_url TEXT NOT NULL, caption TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, expires_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS story_views (
        id INTEGER PRIMARY KEY AUTOINCREMENT, story_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        viewed_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(story_id, user_id))''')
    # Create/update admin user
    existing = c.execute("SELECT id FROM users WHERE username=?", (ADMIN_USERNAME,)).fetchone()
    if existing:
        c.execute("UPDATE users SET password_hash=?,is_admin=1,is_setup=1,full_name='Admin Guru' WHERE username=?",
                  (hash_password(ADMIN_PASSWORD), ADMIN_USERNAME))
    else:
        try:
            c.execute("INSERT INTO users (username,email,password_hash,full_name,is_admin,is_setup) VALUES (?,?,?,?,1,1)",
                      (ADMIN_USERNAME,'admin@scroll2learn.com',hash_password(ADMIN_PASSWORD),'Admin Guru'))
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
    existing = conn.execute("SELECT id FROM users WHERE email=? OR username=?", (email, username)).fetchone()
    if existing:
        conn.close()
        return jsonify({'error': 'Username or email already taken'}), 409
        
    otp = str(random.randint(100000, 999999))
    expires_at = (datetime.now() + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    
    # cleanup old otps for this email
    conn.execute("DELETE FROM otp_requests WHERE email=?", (email,))
    conn.execute("INSERT INTO otp_requests (email, otp, expires_at) VALUES (?, ?, ?)", (email, otp, expires_at))
    conn.commit()
    conn.close()
    
    success = send_otp_email(email, otp)
    if not success:
        print(f"Warning: Failed to send real email. The OTP for {email} is {otp}")
        # In development if missing credentials, we might still want to proceed, but if they want real email:
        # return jsonify({'error': 'Failed to send OTP email. Please check server logs or SMTP config.'}), 500

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
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    otp_record = conn.execute("SELECT * FROM otp_requests WHERE email=? AND otp=? AND expires_at > ?", (email, otp, now_str)).fetchone()
    
    if not otp_record:
        conn.close()
        return jsonify({'error': 'Invalid or expired OTP'}), 400

    try:
        conn.execute('INSERT INTO users (username,email,password_hash) VALUES (?,?,?)',(username,email,hash_password(password)))
        conn.execute('DELETE FROM otp_requests WHERE email=?', (email,)) # Clear OTP
        conn.commit()
        user = conn.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        token = generate_token()
        conn.execute('INSERT INTO sessions (user_id,token) VALUES (?,?)',(user['id'],token))
        conn.commit(); conn.close()
        return jsonify({'token':token,'user':serialize_user(dict(user)),'is_new':True})
    except sqlite3.IntegrityError:
        conn.close(); return jsonify({'error':'Username or email already taken'}),409

@app.route('/auth/login', methods=['POST'])
def login():
    d = request.get_json()
    identifier = d.get('identifier','').strip().lower()
    password = d.get('password','')
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE (username=? OR email=?) AND password_hash=?',
                        (identifier,identifier,hash_password(password))).fetchone()
    if not user: conn.close(); return jsonify({'error':'Invalid username or password'}),401
    token = generate_token()
    conn.execute('INSERT INTO sessions (user_id,token) VALUES (?,?)',(user['id'],token))
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
        conn = get_db(); conn.execute('DELETE FROM sessions WHERE token=?',(auth[7:],)); conn.commit(); conn.close()
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
            result = cloudinary.uploader.upload(f, resource_type="auto")
            avatar_url = result.get('secure_url')
    conn = get_db()
    conn.execute('UPDATE users SET full_name=?,bio=?,website=?,avatar=?,interests=?,profession=?,is_setup=1 WHERE id=?',
                 (full_name,bio,website,avatar_url,interests,profession,user['id']))
    conn.commit()
    updated = conn.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
    conn.close(); return jsonify({'user':serialize_user(dict(updated))})

@app.route('/profile/profession', methods=['PUT'])
def update_profession():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    data = request.json or {}
    prof = data.get('profession', 'College').strip()
    conn = get_db()
    conn.execute('UPDATE users SET profession=? WHERE id=?', (prof, user['id']))
    conn.commit()
    updated = conn.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
    conn.close(); return jsonify({'user':serialize_user(dict(updated))})

@app.route('/profile/stats', methods=['GET'])
def profile_stats():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    posts   = conn.execute("SELECT COUNT(*) FROM posts WHERE user_id=? AND type='post'",(user['id'],)).fetchone()[0]
    reels   = conn.execute("SELECT COUNT(*) FROM posts WHERE user_id=? AND type='reel'",(user['id'],)).fetchone()[0]
    likes   = conn.execute("SELECT COALESCE(SUM(likes_count),0) FROM posts WHERE user_id=?",(user['id'],)).fetchone()[0]
    saved   = conn.execute("SELECT COUNT(*) FROM saves WHERE user_id=?",(user['id'],)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM posts WHERE user_id=? AND is_approved=0 AND rejection_reason=''",(user['id'],)).fetchone()[0]
    conn.close(); return jsonify({'posts':posts,'reels':reels,'likes':likes,'saved':saved,'pending':pending})

# FEED
@app.route('/feed', methods=['GET'])
def get_feed():
    user = get_current_user(request)
    page = max(1,int(request.args.get('page',1)))
    per_page = min(20,int(request.args.get('per_page',10)))
    offset = (page-1)*per_page
    conn = get_db()
    if user:
        user_interests = json.loads(user.get('interests') or '[]')
        user_prof = user.get('profession') or 'College'
        prof_arg = f'"{user_prof}"'
        
        if user_interests:
            placeholders = ','.join('?' for _ in user_interests)
            query = f'''SELECT p.*,u.username,u.full_name,u.avatar,
                CASE 
                    WHEN p.target_profession LIKE '%' || ? || '%' THEN 0 
                    WHEN p.domain IN ({placeholders}) THEN 1 
                    ELSE 2 
                END AS priority
                FROM posts p JOIN users u ON p.user_id=u.id WHERE p.is_approved=1
                ORDER BY priority ASC, p.created_at DESC LIMIT ? OFFSET ?'''
            rows = conn.execute(query, (prof_arg, *user_interests, per_page, offset)).fetchall()
        else:
            query = '''SELECT p.*,u.username,u.full_name,u.avatar,
                CASE WHEN p.target_profession LIKE '%' || ? || '%' THEN 0 ELSE 1 END AS priority
                FROM posts p JOIN users u ON p.user_id=u.id WHERE p.is_approved=1
                ORDER BY priority ASC, p.created_at DESC LIMIT ? OFFSET ?'''
            rows = conn.execute(query, (prof_arg, per_page, offset)).fetchall()
    else:
        rows = conn.execute('''SELECT p.*,u.username,u.full_name,u.avatar FROM posts p
            JOIN users u ON p.user_id=u.id WHERE p.is_approved=1
            ORDER BY p.created_at DESC LIMIT ? OFFSET ?''',(per_page,offset)).fetchall()
    total = conn.execute('SELECT COUNT(*) FROM posts WHERE is_approved=1').fetchone()[0]
    result = []
    for row in rows:
        d = dict(row); liked=saved=False
        if user:
            liked = bool(conn.execute('SELECT 1 FROM likes WHERE user_id=? AND post_id=?',(user['id'],d['id'])).fetchone())
            saved = bool(conn.execute('SELECT 1 FROM saves WHERE user_id=? AND post_id=?',(user['id'],d['id'])).fetchone())
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
    
    result = cloudinary.uploader.upload(f, resource_type="auto")
    media_url = result.get('secure_url')
    
    is_approved = 1 if user.get('is_admin') else 0  # Only admin posts auto-approved
    conn = get_db()
    c = conn.execute('INSERT INTO posts (user_id,type,title,description,media_url,hashtags,is_approved,domain,target_profession) VALUES (?,?,?,?,?,?,?,?,?)',
                     (user['id'],post_type,title,description,media_url,hashtags,is_approved,domain,target_profession))
    conn.commit()
    row = conn.execute('SELECT p.*,u.username,u.full_name,u.avatar FROM posts p JOIN users u ON p.user_id=u.id WHERE p.id=?',(c.lastrowid,)).fetchone()
    conn.close()
    return jsonify({'post':format_post(dict(row)),'pending':not bool(is_approved)}),201

@app.route('/posts/<int:pid>/like', methods=['POST'])
def toggle_like(pid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    exists = conn.execute('SELECT 1 FROM likes WHERE user_id=? AND post_id=?',(user['id'],pid)).fetchone()
    if exists:
        conn.execute('DELETE FROM likes WHERE user_id=? AND post_id=?',(user['id'],pid))
        conn.execute('UPDATE posts SET likes_count=MAX(0,likes_count-1) WHERE id=?',(pid,)); liked=False
    else:
        conn.execute('INSERT INTO likes (user_id,post_id) VALUES (?,?)',(user['id'],pid))
        conn.execute('UPDATE posts SET likes_count=likes_count+1 WHERE id=?',(pid,)); liked=True
    conn.commit()
    count = conn.execute('SELECT likes_count FROM posts WHERE id=?',(pid,)).fetchone()[0]
    conn.close(); return jsonify({'liked':liked,'likes_count':count})

@app.route('/posts/<int:pid>/save', methods=['POST'])
def toggle_save(pid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    exists = conn.execute('SELECT 1 FROM saves WHERE user_id=? AND post_id=?',(user['id'],pid)).fetchone()
    if exists: conn.execute('DELETE FROM saves WHERE user_id=? AND post_id=?',(user['id'],pid)); saved=False
    else: conn.execute('INSERT INTO saves (user_id,post_id) VALUES (?,?)',(user['id'],pid)); saved=True
    conn.commit(); conn.close(); return jsonify({'saved':saved})

@app.route('/posts/<int:pid>/comments', methods=['GET'])
def get_comments(pid):
    conn = get_db()
    rows = conn.execute('SELECT c.*,u.username,u.avatar FROM comments c JOIN users u ON c.user_id=u.id WHERE c.post_id=? ORDER BY c.created_at DESC',(pid,)).fetchall()
    conn.close()
    return jsonify([{'id':r['id'],'text':r['text'],'username':r['username'],'avatar':r['avatar'] or '','time':time_ago(r['created_at'])} for r in rows])

@app.route('/posts/<int:pid>/comments', methods=['POST'])
def add_comment(pid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    text = (request.get_json() or {}).get('text','').strip()
    if not text: return jsonify({'error':'Empty'}),400
    conn = get_db()
    conn.execute('INSERT INTO comments (user_id,post_id,text) VALUES (?,?,?)',(user['id'],pid,text))
    conn.execute('UPDATE posts SET comments_count=comments_count+1 WHERE id=?',(pid,))
    conn.commit(); conn.close()
    return jsonify({'message':'ok','username':user['username']}),201

@app.route('/posts/<int:pid>', methods=['DELETE'])
def delete_post(pid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    post = conn.execute('SELECT * FROM posts WHERE id=?',(pid,)).fetchone()
    if not post: conn.close(); return jsonify({'error':'Post not found'}),404
    if post['user_id'] != user['id'] and not user.get('is_admin'):
        conn.close(); return jsonify({'error':'Not allowed'}),403
    conn.execute('DELETE FROM likes WHERE post_id=?',(pid,))
    conn.execute('DELETE FROM comments WHERE post_id=?',(pid,))
    conn.execute('DELETE FROM saves WHERE post_id=?',(pid,))
    conn.execute('DELETE FROM posts WHERE id=?',(pid,))
    conn.commit(); conn.close()
    return jsonify({'message':'Deleted'})

@app.route('/profile/posts', methods=['GET'])
def user_posts():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    rows = conn.execute('''SELECT p.*,u.username,u.full_name,u.avatar FROM posts p
        JOIN users u ON p.user_id=u.id WHERE p.user_id=? ORDER BY p.created_at DESC''',(user['id'],)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        liked = bool(conn.execute('SELECT 1 FROM likes WHERE user_id=? AND post_id=?',(user['id'],d['id'])).fetchone())
        saved = bool(conn.execute('SELECT 1 FROM saves WHERE user_id=? AND post_id=?',(user['id'],d['id'])).fetchone())
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
    rows=conn.execute("SELECT p.*,u.username,u.full_name,u.avatar FROM posts p JOIN users u ON p.user_id=u.id WHERE p.is_approved=0 AND p.rejection_reason='' ORDER BY p.created_at ASC LIMIT ? OFFSET ?",(per_page,offset)).fetchall()
    total=conn.execute("SELECT COUNT(*) FROM posts WHERE is_approved=0 AND rejection_reason=''"  ).fetchone()[0]
    conn.close(); return jsonify({'posts':[format_post(dict(r)) for r in rows],'total':total})

@app.route('/admin/posts/<int:pid>/approve', methods=['POST'])
def admin_approve(pid):
    if not require_admin(request): return jsonify({'error':'Admin only'}),403
    conn=get_db(); conn.execute("UPDATE posts SET is_approved=1,rejection_reason='' WHERE id=?",(pid,)); conn.commit(); conn.close()
    return jsonify({'message':'Approved'})

@app.route('/admin/posts/<int:pid>/reject', methods=['POST'])
def admin_reject(pid):
    if not require_admin(request): return jsonify({'error':'Admin only'}),403
    reason=(request.get_json() or {}).get('reason','Does not meet guidelines')
    conn=get_db();    conn.execute('UPDATE posts SET is_approved=0, rejection_reason=? WHERE id=?', (reason, pid))
    conn.commit(); conn.close()
    return jsonify({'message':'rejected'})

@app.route('/admin/users', methods=['GET'])
def admin_users():
    user = get_current_user(request)
    if not user or not user.get('is_admin'): return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    u_list = conn.execute("SELECT id, username, email, full_name, is_admin, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify({'users': [dict(u) for u in u_list]})

@app.route('/admin/users/<int:uid>', methods=['DELETE'])
def admin_delete_user(uid):
    user = get_current_user(request)
    if not user or not user.get('is_admin'): return jsonify({'error':'Unauthorized'}),401
    conn = get_db()
    conn.execute('DELETE FROM users WHERE id=?', (uid,))
    conn.execute('DELETE FROM posts WHERE user_id=?', (uid,))
    conn.execute('DELETE FROM comments WHERE user_id=?', (uid,))
    conn.execute('DELETE FROM likes WHERE user_id=?', (uid,))
    conn.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
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
    conn.execute("UPDATE posts SET title=?, description=?, domain=? WHERE id=?", (title, description, domain, pid))
    conn.commit()
    conn.close()
    return jsonify({'ok':True})

@app.route('/admin/stats', methods=['GET'])
def admin_stats():
    if not require_admin(request): return jsonify({'error':'Admin only'}),403
    conn=get_db()
    stats={
        'posts':conn.execute("SELECT COUNT(*) FROM posts WHERE type='post'").fetchone()[0],
        'reels':conn.execute("SELECT COUNT(*) FROM posts WHERE type='reel'").fetchone()[0],
        'pending':conn.execute("SELECT COUNT(*) FROM posts WHERE is_approved=0 AND rejection_reason=''").fetchone()[0],
        'approved':conn.execute("SELECT COUNT(*) FROM posts WHERE is_approved=1").fetchone()[0],
        'rejected':conn.execute("SELECT COUNT(*) FROM posts WHERE is_approved=0 AND rejection_reason!=''").fetchone()[0],
        'users':conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=0").fetchone()[0],
        'likes':conn.execute("SELECT COALESCE(SUM(likes_count),0) FROM posts").fetchone()[0],
        'comments':conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
    }
    conn.close(); return jsonify(stats)

# STORIES
@app.route('/stories', methods=['GET'])
def get_stories():
    user=get_current_user(request)
    conn=get_db(); now=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    rows=conn.execute("SELECT s.*,u.username,u.avatar,u.full_name FROM stories s JOIN users u ON s.user_id=u.id WHERE s.expires_at>? ORDER BY s.created_at DESC",(now,)).fetchall()
    grouped={}
    for r in rows:
        uid=r['user_id']
        if uid not in grouped:
            grouped[uid]={'user_id':uid,'username':r['username'],'avatar':r['avatar'] or '','full_name':r['full_name'] or r['username'],'stories':[],'has_unseen':False}
        viewed=False
        if user: viewed=bool(conn.execute('SELECT 1 FROM story_views WHERE story_id=? AND user_id=?',(r['id'],user['id'])).fetchone())
        if not viewed: grouped[uid]['has_unseen']=True
        grouped[uid]['stories'].append({'id':r['id'],'media_url':r['media_url'],'caption':r['caption'],'time':time_ago(r['created_at']),'viewed':viewed})
    conn.close(); return jsonify(list(grouped.values()))

@app.route('/stories', methods=['POST'])
def create_story():
    user=get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}),401
    caption=request.form.get('caption','')
    f=request.files.get('media')
    if not f or not allowed_file(f.filename): return jsonify({'error':'Media required'}),400
    
    result = cloudinary.uploader.upload(f, resource_type="auto")
    media_url = result.get('secure_url')
    
    expires=( datetime.utcnow()+timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    conn=get_db(); conn.execute('INSERT INTO stories (user_id,media_url,caption,expires_at) VALUES (?,?,?,?)',(user['id'],media_url,caption,expires)); conn.commit(); conn.close()
    return jsonify({'message':'ok'}),201

@app.route('/stories/<int:sid>/view', methods=['POST'])
def view_story(sid):
    user=get_current_user(request)
    if user:
        conn=get_db()
        try: conn.execute('INSERT OR IGNORE INTO story_views (story_id,user_id) VALUES (?,?)',(sid,user['id'])); conn.commit()
        except: pass
        conn.close()
    return jsonify({'ok':True})

# SEARCH
@app.route('/search', methods=['GET'])
def search():
    q = request.args.get('q','').strip()
    if not q: return jsonify({'posts':[], 'users':[]})
    conn = get_db()
    qq = f"%{q}%"
    users = conn.execute("SELECT id, username, full_name, avatar FROM users WHERE username LIKE ? OR full_name LIKE ? LIMIT 15", (qq, qq)).fetchall()
    posts = conn.execute("SELECT p.id, p.title, p.description, p.media_url, u.username as author, p.created_at as time FROM posts p JOIN users u ON p.user_id = u.id WHERE p.is_approved=1 AND (p.title LIKE ? OR p.description LIKE ? OR p.hashtags LIKE ? OR u.username LIKE ?) LIMIT 20", (qq, qq, qq, qq)).fetchall()
    conn.close()
    return jsonify({'users': [dict(u) for u in users], 'posts': [dict(p) for p in posts]})

@app.route('/chat/users', methods=['GET'])
def get_chat_users():
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    conn = get_db()
    users = conn.execute("SELECT id, username, full_name, avatar FROM users WHERE id != ? ORDER BY id DESC", (user['id'],)).fetchall()
    conn.close()
    return jsonify({'users': [dict(u) for u in users]})

@app.route('/chat/<int:uid>', methods=['GET'])
def get_chat_messages(uid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    conn = get_db()
    msgs = conn.execute("SELECT * FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY created_at ASC", (user['id'], uid, uid, user['id'])).fetchall()
    conn.close()
    return jsonify({'messages': [dict(m) for m in msgs]})

@app.route('/chat/<int:uid>', methods=['POST'])
def send_chat_message(uid):
    user = get_current_user(request)
    if not user: return jsonify({'error':'Unauthorized'}), 401
    text = request.get_json().get('text','').strip()
    if not text: return jsonify({'error':'Text required'}), 400
    conn = get_db()
    conn.execute("INSERT INTO messages (sender_id, receiver_id, text) VALUES (?,?,?)", (user['id'], uid, text))
    conn.commit(); conn.close()
    return jsonify({'success': True})

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

@app.route('/')
def index(): return jsonify({'status':'Scroll2Learn API v2.1 🚀'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n>>> Scroll2Learn API running on port {port}")
    print(f">>> Admin login: username={ADMIN_USERNAME}  password={ADMIN_PASSWORD}\n")
    app.run(debug=False, port=port, host='0.0.0.0', use_reloader=False)