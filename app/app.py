import os
import uuid
import hashlib
import csv
import json
import random
import string
from datetime import datetime, timedelta
from functools import wraps

import bcrypt
import bleach
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory, abort
)
from flask_socketio import SocketIO, send, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pywebpush import webpush, WebPushException
from werkzeug.utils import secure_filename

import security
import storage_manager
from apscheduler.schedulers.background import BackgroundScheduler

# ---------------------------------------------------------------------------
# App Configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'sua_chave_secreta_dev')
database_uri = os.environ.get(
    'DATABASE_URI',
    'mysql+pymysql://Nate:Natecrusader25@147.93.67.17:3306/default'
)

# Test connection, fallback to SQLite if unreachable
if 'mysql' in database_uri:
    try:
        from sqlalchemy import create_engine
        test_engine = create_engine(database_uri, connect_args={"connect_timeout": 3})
        conn = test_engine.connect()
        conn.close()
        test_engine.dispose()
        print("Conectado ao banco de dados MySQL com sucesso!")
    except Exception as e:
        print(f"Aviso: Não foi possível conectar ao MySQL ({e}). Usando SQLite local para desenvolvimento...")
        database_uri = 'sqlite:///' + os.path.join(os.path.dirname(__file__), 'dev.db')

app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # 25 MB
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', os.path.join(os.path.dirname(__file__), 'uploads'))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

DEFAULT_USER_QUOTA = 500 * 1024 * 1024  # 500 MB per user

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails'), exist_ok=True)

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per minute"])

VAPID_PUBLIC_KEY = os.environ.get(
    'VAPID_PUBLIC_KEY',
    'BKGeyfjwHzKcgPEM0I-XqudWHWiSVuOIFcBs5dLv5hOy9BhAaFbznVbsHqqi8zXzHcHefAMa0qpIuDVI4vAMKvI'
)
VAPID_PRIVATE_KEY = os.environ.get(
    'VAPID_PRIVATE_KEY',
    'LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1JR0hBZ0VBTUJNR0J5cUdTTTQ5QWdFR0NDcUdTTTQ5QXdFSEJHMHdhd0lCQVFRZ3hvbWtQWGk4cXJrYVZHMSsKQWhMSUNVMnlBV1NmdHVQMVl1a1NVVXlHL1BDaFJBTkNBQVNobnNuNDhCOHluSUR4RE5DUGw2cm5WaDFva2xiagppQlhBYk9YUzcrWVRzdlFZUUdoVzg1MVc3QjZxb3ZNMTh4M0IzbndER3RLcVNMZzFTT0x3RENyeQotLS0tLUVORCBQUklWQVRFIEtFWS0tLS0tCg'
)
VAPID_CLAIMS = {"sub": os.environ.get('VAPID_EMAIL', 'mailto:jbplsyer406@gmail.com')}

ALLOWED_EXTENSIONS = {
    'image': {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'},
    'video': {'mp4', 'webm', 'mov'},
    'audio': {'mp3', 'wav', 'ogg', 'm4a'},
    'document': {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv'},
    'archive': {'zip', 'rar', '7z', 'tar', 'gz'}
}
ALL_ALLOWED = set()
for exts in ALLOWED_EXTENSIONS.values():
    ALL_ALLOWED |= exts

# ---------------------------------------------------------------------------
# Database Models
# ---------------------------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    registration_code = db.Column(db.String(10), nullable=True)
    avatar_color = db.Column(db.String(7), default='#d67f9d')
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    settings = db.relationship('UserSettings', backref='user', uselist=False, lazy=True)
    status = db.relationship('UserStatus', backref='user', uselist=False, lazy=True)


class ChatRoom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), default='')
    is_private = db.Column(db.Boolean, default=False)
    is_direct = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    last_message_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    creator = db.relationship('User', backref='created_rooms', foreign_keys=[created_by])
    members = db.relationship('ChatMember', backref='room', lazy=True, cascade='all, delete-orphan')
    messages = db.relationship('Message', backref='room', lazy=True, cascade='all, delete-orphan')


class ChatMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('chat_room.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(20), default='member')  # admin, member
    joined_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    is_favorite = db.Column(db.Boolean, default=False)
    is_archived = db.Column(db.Boolean, default=False)
    is_muted = db.Column(db.Boolean, default=False)
    last_read_message_id = db.Column(db.Integer, default=0)
    unread_count = db.Column(db.Integer, default=0)

    user = db.relationship('User', backref='memberships')

    __table_args__ = (db.UniqueConstraint('room_id', 'user_id'),)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    username = db.Column(db.String(80), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    room_id = db.Column(db.Integer, db.ForeignKey('chat_room.id'), nullable=True)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())
    edited_at = db.Column(db.DateTime, nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)
    is_pinned = db.Column(db.Boolean, default=False)
    message_type = db.Column(db.String(20), default='text')  # text, file, system
    file_url = db.Column(db.String(500), nullable=True)
    file_name = db.Column(db.String(255), nullable=True)
    file_type = db.Column(db.String(50), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)

    reply_to = db.relationship('Message', remote_side=[id], backref='replies')
    reactions = db.relationship('Reaction', backref='message', lazy=True, cascade='all, delete-orphan')
    author = db.relationship('User', backref='messages_sent', foreign_keys=[user_id])


class Reaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    emoji = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship('User', backref='reactions')

    __table_args__ = (db.UniqueConstraint('message_id', 'user_id', 'emoji'),)


class UserStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    is_online = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, default=db.func.current_timestamp())
    socket_id = db.Column(db.String(100), nullable=True)


class UserSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    theme = db.Column(db.String(30), default='default')
    notifications_enabled = db.Column(db.Boolean, default=True)


class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    endpoint = db.Column(db.String(500), nullable=False)
    p256dh = db.Column(db.String(500), nullable=False)
    auth = db.Column(db.String(500), nullable=False)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    username = db.Column(db.String(80), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())


class FileAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(100), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    compressed_size = db.Column(db.Integer, nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('chat_room.id'), nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    file_hash = db.Column(db.String(64), nullable=False)
    is_compressed = db.Column(db.Boolean, default=False)
    thumbnail_name = db.Column(db.String(100), nullable=True)

    uploader = db.relationship('User', backref='uploads')
    room = db.relationship('ChatRoom', backref='attachments')


# ---------------------------------------------------------------------------
# Database Initialization
# ---------------------------------------------------------------------------
with app.app_context():
    db.create_all()

    # Create admin user if not exists
    admin_user = User.query.filter_by(username='admin').first()
    if not admin_user:
        hashed_password = bcrypt.hashpw('admin123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        admin_user = User(username='admin', password=hashed_password, is_admin=True)
        db.session.add(admin_user)
        db.session.commit()
        print('Usuário admin criado com sucesso!')

    # Create default "General" room if no rooms exist
    if ChatRoom.query.count() == 0:
        admin = User.query.filter_by(username='admin').first()
        general = ChatRoom(
            name='Geral',
            description='Sala geral de conversas',
            is_private=False,
            created_by=admin.id
        )
        db.session.add(general)
        db.session.commit()

        # Add all existing users to general room
        for user in User.query.all():
            member = ChatMember(room_id=general.id, user_id=user.id, role='member')
            db.session.add(member)

        # Migrate existing messages to general room
        for msg in Message.query.filter_by(room_id=None).all():
            msg.room_id = general.id
            u = User.query.filter_by(username=msg.username).first()
            if u:
                msg.user_id = u.id
        db.session.commit()
        print('Sala "Geral" criada e mensagens migradas!')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def check_password(password, hashed_password):
    if not hashed_password.startswith('$2b$'):
        return False
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))


def sanitize(text):
    """Sanitize message content to prevent XSS."""
    return security.sanitize_message(text)


def get_file_category(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    for category, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return category
    return None


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALL_ALLOWED


def log_audit(action, details=None, user_id=None, username=None):
    try:
        log = AuditLog(
            user_id=user_id,
            username=username or session.get('username'),
            action=action,
            details=details,
            ip_address=request.remote_addr if request else None
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Não autenticado'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Não autenticado'}), 401
            return redirect(url_for('login'))
        user = User.query.filter_by(username=session['username']).first()
        if not user or not user.is_admin:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Acesso negado'}), 403
            flash('Acesso negado. Você não é um administrador.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    if 'username' in session:
        return User.query.filter_by(username=session['username']).first()
    return None


def read_registration_codes():
    codes = []
    try:
        with open('convites.csv', mode='r', newline='') as file:
            reader = csv.reader(file)
            codes = [row[0] for row in reader]
    except FileNotFoundError:
        pass
    return codes


def write_registration_code(code):
    with open('convites.csv', mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([code])


def delete_registration_code(code):
    codes = read_registration_codes()
    codes = [c for c in codes if c != code]
    with open('convites.csv', mode='w', newline='') as file:
        writer = csv.writer(file)
        for c in codes:
            writer.writerow([c])


def serialize_message(msg):
    """Serialize a message for JSON response."""
    reply_data = None
    if msg.reply_to_id and msg.reply_to:
        reply_data = {
            'id': msg.reply_to.id,
            'content': msg.reply_to.content if not msg.reply_to.is_deleted else '[Mensagem apagada]',
            'username': msg.reply_to.username
        }

    reactions_map = {}
    for r in msg.reactions:
        if r.emoji not in reactions_map:
            reactions_map[r.emoji] = {'emoji': r.emoji, 'count': 0, 'users': []}
        reactions_map[r.emoji]['count'] += 1
        reactions_map[r.emoji]['users'].append(r.user.username if r.user else '?')

    return {
        'id': msg.id,
        'content': msg.content if not msg.is_deleted else '[Mensagem apagada]',
        'username': msg.username,
        'user_id': msg.user_id,
        'room_id': msg.room_id,
        'timestamp': msg.timestamp.isoformat() if msg.timestamp else None,
        'edited_at': msg.edited_at.isoformat() if msg.edited_at else None,
        'is_deleted': msg.is_deleted,
        'is_pinned': msg.is_pinned,
        'message_type': msg.message_type or 'text',
        'file_url': msg.file_url,
        'file_name': msg.file_name,
        'file_type': msg.file_type,
        'file_size': msg.file_size,
        'reply_to': reply_data,
        'reactions': list(reactions_map.values()),
        'avatar_color': msg.author.avatar_color if msg.author else '#d67f9d'
    }


def serialize_room(room, user_id):
    """Serialize a room for JSON response."""
    membership = ChatMember.query.filter_by(room_id=room.id, user_id=user_id).first()

    last_msg = Message.query.filter_by(room_id=room.id, is_deleted=False)\
        .order_by(Message.timestamp.desc()).first()

    member_count = ChatMember.query.filter_by(room_id=room.id).count()

    # For direct chats, show the other user's name
    display_name = room.name
    if room.is_direct:
        other_member = ChatMember.query.filter(
            ChatMember.room_id == room.id,
            ChatMember.user_id != user_id
        ).first()
        if other_member and other_member.user:
            display_name = other_member.user.username

    return {
        'id': room.id,
        'name': display_name,
        'description': room.description,
        'is_private': room.is_private,
        'is_direct': room.is_direct,
        'created_by': room.created_by,
        'member_count': member_count,
        'is_favorite': membership.is_favorite if membership else False,
        'is_archived': membership.is_archived if membership else False,
        'is_muted': membership.is_muted if membership else False,
        'unread_count': membership.unread_count if membership else 0,
        'last_message': {
            'content': last_msg.content if last_msg and not last_msg.is_deleted else None,
            'username': last_msg.username if last_msg else None,
            'timestamp': last_msg.timestamp.isoformat() if last_msg else None,
            'message_type': last_msg.message_type if last_msg else None,
        } if last_msg else None,
        'last_message_at': room.last_message_at.isoformat() if room.last_message_at else None
    }


# ---------------------------------------------------------------------------
# Online users tracking (in-memory for performance)
# ---------------------------------------------------------------------------
online_users = {}  # socket_id -> {user_id, username}


# ---------------------------------------------------------------------------
# Page Routes
# ---------------------------------------------------------------------------

@app.context_processor
def inject_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = security.generate_csrf_token()
    return dict(csrf_token=session['_csrf_token'])


@app.before_request
def check_session_fingerprint():
    if request.path.startswith('/static/'):
        return
    if 'username' in session:
        if not security.validate_session_fingerprint(session, request):
            username = session.get('username')
            user = User.query.filter_by(username=username).first()
            security.log_security_event(
                db, AuditLog,
                event_type=security.SecurityEventType.SESSION_ANOMALY,
                details=f"Anomalia de fingerprint de sessão detectada. IP: {request.remote_addr}",
                user_id=user.id if user else None,
                username=username,
                ip_address=request.remote_addr
            )
            session.clear()
            flash('Sua sessão foi encerrada por motivos de segurança.', 'warning')
            return redirect(url_for('login'))


@app.before_request
def validate_csrf():
    if request.method in ['POST', 'PUT', 'DELETE']:
        # Skip socket.io handshakes and push notification subscribe endpoint
        if request.path.startswith('/socket.io') or request.path == '/subscribe':
            return
        
        token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        stored_token = session.get('_csrf_token')
        
        if not stored_token or not token or token != stored_token:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Token CSRF inválido ou ausente'}), 400
            abort(400, 'Token CSRF inválido ou ausente')


@app.route('/')
@login_required
def index():
    user = get_current_user()
    theme = 'default'
    if user and user.settings:
        theme = user.settings.theme or 'default'
    return render_template('index.html',
                           username=session['username'],
                           user_id=user.id,
                           is_admin=user.is_admin,
                           theme=theme,
                           avatar_color=user.avatar_color if user else '#d67f9d')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password(password, user.password):
            session['username'] = username
            session['user_id'] = user.id
            session.permanent = True
            session['_fingerprint'] = security.create_session_fingerprint(request)
            security.log_security_event(
                db, AuditLog,
                event_type=security.SecurityEventType.LOGIN_SUCCESS,
                details=f'Usuário {username} fez login com sucesso',
                user_id=user.id,
                username=username,
                ip_address=request.remote_addr
            )
            log_audit('login', f'Usuário {username} fez login', user.id, username)
            return redirect(url_for('index'))
        
        security.log_security_event(
            db, AuditLog,
            event_type=security.SecurityEventType.LOGIN_FAILED,
            details=f'Falha de login para o usuário: {username}',
            username=username,
            ip_address=request.remote_addr
        )
        flash('Usuário ou senha inválidos', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        registration_code = request.form['registration_code']

        valid_codes = read_registration_codes()
        if registration_code not in valid_codes:
            flash('Código de registro inválido.', 'error')
            return redirect(url_for('register'))

        if not username or not password or not confirm_password:
            flash('Todos os campos são obrigatórios.', 'error')
        elif password != confirm_password:
            flash('As senhas não coincidem.', 'error')
        elif len(username) < 3 or len(username) > 30:
            flash('Nome de usuário deve ter entre 3 e 30 caracteres.', 'error')
        elif len(password) < 6:
            flash('Senha deve ter pelo menos 6 caracteres.', 'error')
        elif User.query.filter_by(username=username).first():
            flash('Nome de usuário já existe.', 'error')
        else:
            # Random avatar color from a curated palette
            colors = ['#d67f9d', '#6366f1', '#06b6d4', '#10b981', '#f59e0b',
                      '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316']
            hashed_password = hash_password(password)
            new_user = User(
                username=username,
                password=hashed_password,
                avatar_color=random.choice(colors)
            )
            db.session.add(new_user)
            db.session.commit()

            # Create default settings
            settings = UserSettings(user_id=new_user.id)
            db.session.add(settings)

            # Create status
            status = UserStatus(user_id=new_user.id)
            db.session.add(status)

            # Add to all public rooms
            public_rooms = ChatRoom.query.filter_by(is_private=False, is_direct=False).all()
            for room in public_rooms:
                member = ChatMember(room_id=room.id, user_id=new_user.id, role='member')
                db.session.add(member)

            db.session.commit()
            delete_registration_code(registration_code)
            security.log_security_event(
                db, AuditLog,
                event_type=security.SecurityEventType.REGISTRATION,
                details=f'Novo usuário registrado: {username}',
                user_id=new_user.id,
                username=username,
                ip_address=request.remote_addr
            )
            log_audit('register', f'Novo usuário registrado: {username}', new_user.id, username)
            flash('Registro realizado com sucesso! Faça login.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/logout')
def logout():
    username = session.get('username')
    if username:
        log_audit('logout', f'Usuário {username} fez logout')
    session.pop('username', None)
    session.pop('user_id', None)
    return redirect(url_for('login'))


@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        user = get_current_user()
        if not user or not check_password(current_password, user.password):
            flash('Senha atual incorreta.', 'error')
        elif new_password != confirm_password:
            flash('As novas senhas não coincidem.', 'error')
        elif len(new_password) < 6:
            flash('Nova senha deve ter pelo menos 6 caracteres.', 'error')
        else:
            user.password = hash_password(new_password)
            db.session.commit()
            security.log_security_event(
                db, AuditLog,
                event_type=security.SecurityEventType.PASSWORD_CHANGE,
                details='Senha alterada pelo próprio usuário',
                user_id=user.id,
                username=user.username,
                ip_address=request.remote_addr
            )
            log_audit('password_change', 'Senha alterada', user.id)
            flash('Senha alterada com sucesso!', 'success')
            return redirect(url_for('index'))
    return render_template('change_password.html')


# ---------------------------------------------------------------------------
# Admin Routes
# ---------------------------------------------------------------------------

@app.route('/admin')
@admin_required
def admin_panel():
    users = User.query.all()
    registration_codes = read_registration_codes()
    rooms = ChatRoom.query.all()
    total_messages = Message.query.count()
    online_count = len(online_users)

    return render_template('admin.html',
                           users=users,
                           registration_codes=registration_codes,
                           rooms=rooms,
                           total_messages=total_messages,
                           online_count=online_count)


@app.route('/admin/generate_code', methods=['POST'])
@admin_required
def generate_code():
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    write_registration_code(code)
    log_audit('generate_code', f'Código gerado: {code}')
    return redirect(url_for('admin_panel'))


@app.route('/admin/delete_code/<string:code>', methods=['POST'])
@admin_required
def delete_code(code):
    delete_registration_code(code)
    log_audit('delete_code', f'Código excluído: {code}')
    return redirect(url_for('admin_panel'))


@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user_to_edit = User.query.get_or_404(user_id)
    if request.method == 'POST':
        username = request.form['username']
        is_admin = request.form.get('is_admin') == 'on'
        user_to_edit.username = username
        user_to_edit.is_admin = is_admin
        db.session.commit()
        log_audit('edit_user', f'Usuário {username} editado', user_to_edit.id)
        flash('Usuário atualizado com sucesso!', 'success')
        return redirect(url_for('admin_panel'))
    return render_template('edit_user.html', user=user_to_edit)


@app.route('/delete_user/<int:user_id>')
@admin_required
def delete_user(user_id):
    user_to_delete = User.query.get_or_404(user_id)
    log_audit('delete_user', f'Usuário {user_to_delete.username} excluído')
    db.session.delete(user_to_delete)
    db.session.commit()
    flash('Usuário excluído com sucesso!', 'success')
    return redirect(url_for('admin_panel'))


# ---------------------------------------------------------------------------
# API Routes — Rooms
# ---------------------------------------------------------------------------

@app.route('/api/rooms', methods=['GET'])
@login_required
def api_get_rooms():
    user = get_current_user()
    memberships = ChatMember.query.filter_by(user_id=user.id).all()
    room_ids = [m.room_id for m in memberships]
    rooms = ChatRoom.query.filter(ChatRoom.id.in_(room_ids)).order_by(ChatRoom.last_message_at.desc()).all()
    return jsonify([serialize_room(r, user.id) for r in rooms])


@app.route('/api/rooms', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def api_create_room():
    user = get_current_user()
    data = request.json
    name = sanitize(data.get('name', '').strip())
    description = sanitize(data.get('description', '').strip())
    is_private = data.get('is_private', False)
    member_ids = data.get('members', [])

    if not name or len(name) < 2:
        return jsonify({'error': 'Nome da sala inválido'}), 400

    room = ChatRoom(
        name=name,
        description=description,
        is_private=is_private,
        created_by=user.id
    )
    db.session.add(room)
    db.session.flush()

    # Add creator as admin
    creator_member = ChatMember(room_id=room.id, user_id=user.id, role='admin')
    db.session.add(creator_member)

    # Add other members
    for uid in member_ids:
        if uid != user.id:
            m = ChatMember(room_id=room.id, user_id=uid, role='member')
            db.session.add(m)

    # System message
    sys_msg = Message(
        content=f'{user.username} criou a sala "{name}"',
        username='Sistema',
        user_id=user.id,
        room_id=room.id,
        message_type='system'
    )
    db.session.add(sys_msg)
    db.session.commit()

    log_audit('create_room', f'Sala "{name}" criada', user.id)
    return jsonify(serialize_room(room, user.id)), 201


@app.route('/api/rooms/<int:room_id>', methods=['GET'])
@login_required
def api_get_room(room_id):
    user = get_current_user()
    room = ChatRoom.query.get_or_404(room_id)
    membership = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    if not membership and room.is_private:
        return jsonify({'error': 'Acesso negado'}), 403

    members = []
    for m in room.members:
        u = User.query.get(m.user_id)
        if u:
            st = UserStatus.query.filter_by(user_id=u.id).first()
            members.append({
                'id': u.id,
                'username': u.username,
                'avatar_color': u.avatar_color,
                'role': m.role,
                'is_online': st.is_online if st else False
            })

    pinned = Message.query.filter_by(room_id=room_id, is_pinned=True, is_deleted=False)\
        .order_by(Message.timestamp.desc()).all()

    result = serialize_room(room, user.id)
    result['members'] = members
    result['pinned_messages'] = [serialize_message(m) for m in pinned]
    return jsonify(result)


@app.route('/api/rooms/<int:room_id>/join', methods=['POST'])
@login_required
def api_join_room(room_id):
    user = get_current_user()
    room = ChatRoom.query.get_or_404(room_id)
    if room.is_private:
        return jsonify({'error': 'Sala privada'}), 403

    existing = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    if not existing:
        member = ChatMember(room_id=room_id, user_id=user.id, role='member')
        db.session.add(member)
        db.session.commit()

    return jsonify({'success': True})


@app.route('/api/rooms/public', methods=['GET'])
@login_required
def api_public_rooms():
    user = get_current_user()
    rooms = ChatRoom.query.filter_by(is_private=False, is_direct=False).all()
    return jsonify([serialize_room(r, user.id) for r in rooms])


@app.route('/api/rooms/direct', methods=['POST'])
@login_required
def api_create_direct():
    """Create or get existing direct message room."""
    user = get_current_user()
    data = request.json
    target_user_id = data.get('user_id')

    if not target_user_id or target_user_id == user.id:
        return jsonify({'error': 'ID de usuário inválido'}), 400

    target_user = User.query.get(target_user_id)
    if not target_user:
        return jsonify({'error': 'Usuário não encontrado'}), 404

    # Check for existing direct room
    user_rooms = db.session.query(ChatMember.room_id).filter_by(user_id=user.id).subquery()
    target_rooms = db.session.query(ChatMember.room_id).filter_by(user_id=target_user_id).subquery()
    existing = ChatRoom.query.filter(
        ChatRoom.is_direct == True,
        ChatRoom.id.in_(db.session.query(user_rooms.c.room_id)),
        ChatRoom.id.in_(db.session.query(target_rooms.c.room_id))
    ).first()

    if existing:
        return jsonify(serialize_room(existing, user.id))

    room = ChatRoom(
        name=f'DM-{user.username}-{target_user.username}',
        is_private=True,
        is_direct=True,
        created_by=user.id
    )
    db.session.add(room)
    db.session.flush()

    db.session.add(ChatMember(room_id=room.id, user_id=user.id, role='member'))
    db.session.add(ChatMember(room_id=room.id, user_id=target_user_id, role='member'))
    db.session.commit()

    return jsonify(serialize_room(room, user.id)), 201


@app.route('/api/rooms/<int:room_id>/favorite', methods=['PUT'])
@login_required
def api_toggle_favorite(room_id):
    user = get_current_user()
    membership = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    if not membership:
        return jsonify({'error': 'Não é membro'}), 403
    membership.is_favorite = not membership.is_favorite
    db.session.commit()
    return jsonify({'is_favorite': membership.is_favorite})


@app.route('/api/rooms/<int:room_id>/archive', methods=['PUT'])
@login_required
def api_toggle_archive(room_id):
    user = get_current_user()
    membership = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    if not membership:
        return jsonify({'error': 'Não é membro'}), 403
    membership.is_archived = not membership.is_archived
    db.session.commit()
    return jsonify({'is_archived': membership.is_archived})


@app.route('/api/rooms/<int:room_id>/mute', methods=['PUT'])
@login_required
def api_toggle_mute(room_id):
    user = get_current_user()
    membership = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    if not membership:
        return jsonify({'error': 'Não é membro'}), 403
    membership.is_muted = not membership.is_muted
    db.session.commit()
    return jsonify({'is_muted': membership.is_muted})


# ---------------------------------------------------------------------------
# API Routes — Messages
# ---------------------------------------------------------------------------

@app.route('/api/rooms/<int:room_id>/messages', methods=['GET'])
@login_required
def api_get_messages(room_id):
    user = get_current_user()
    membership = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    room = ChatRoom.query.get_or_404(room_id)
    if not membership and room.is_private:
        return jsonify({'error': 'Acesso negado'}), 403

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(per_page, 100)

    messages = Message.query.filter_by(room_id=room_id)\
        .order_by(Message.timestamp.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)

    # Mark as read
    if membership:
        membership.unread_count = 0
        if messages.items:
            membership.last_read_message_id = messages.items[0].id
        db.session.commit()

    return jsonify({
        'messages': [serialize_message(m) for m in reversed(messages.items)],
        'has_more': messages.has_next,
        'page': page,
        'total': messages.total
    })


@app.route('/api/messages/<int:message_id>', methods=['PUT'])
@login_required
@limiter.limit("30 per minute")
def api_edit_message(message_id):
    user = get_current_user()
    msg = Message.query.get_or_404(message_id)

    if msg.user_id != user.id and not user.is_admin:
        return jsonify({'error': 'Sem permissão'}), 403
    if msg.is_deleted:
        return jsonify({'error': 'Mensagem já deletada'}), 400

    data = request.json
    new_content = sanitize(data.get('content', '').strip())
    if not new_content:
        return jsonify({'error': 'Conteúdo vazio'}), 400

    msg.content = new_content
    msg.edited_at = datetime.utcnow()
    db.session.commit()

    socketio.emit('message_edited', serialize_message(msg), room=f'room_{msg.room_id}')
    return jsonify(serialize_message(msg))


@app.route('/api/messages/<int:message_id>', methods=['DELETE'])
@login_required
def api_delete_message(message_id):
    user = get_current_user()
    msg = Message.query.get_or_404(message_id)

    if msg.user_id != user.id and not user.is_admin:
        return jsonify({'error': 'Sem permissão'}), 403

    msg.is_deleted = True
    msg.content = '[Mensagem apagada]'
    db.session.commit()

    socketio.emit('message_deleted', {
        'id': msg.id,
        'room_id': msg.room_id
    }, room=f'room_{msg.room_id}')
    return jsonify({'success': True})


@app.route('/api/messages/<int:message_id>/pin', methods=['POST'])
@login_required
def api_toggle_pin(message_id):
    user = get_current_user()
    msg = Message.query.get_or_404(message_id)
    membership = ChatMember.query.filter_by(room_id=msg.room_id, user_id=user.id).first()

    if not membership or (membership.role != 'admin' and not user.is_admin):
        return jsonify({'error': 'Sem permissão'}), 403

    msg.is_pinned = not msg.is_pinned
    db.session.commit()

    socketio.emit('message_pinned', {
        'id': msg.id,
        'room_id': msg.room_id,
        'is_pinned': msg.is_pinned,
        'username': user.username
    }, room=f'room_{msg.room_id}')
    return jsonify({'is_pinned': msg.is_pinned})


@app.route('/api/messages/<int:message_id>/react', methods=['POST'])
@login_required
@limiter.limit("60 per minute")
def api_react(message_id):
    user = get_current_user()
    data = request.json
    emoji = data.get('emoji', '')

    if not emoji or len(emoji) > 10:
        return jsonify({'error': 'Emoji inválido'}), 400

    msg = Message.query.get_or_404(message_id)

    existing = Reaction.query.filter_by(
        message_id=message_id, user_id=user.id, emoji=emoji
    ).first()

    if existing:
        db.session.delete(existing)
        action = 'removed'
    else:
        reaction = Reaction(message_id=message_id, user_id=user.id, emoji=emoji)
        db.session.add(reaction)
        action = 'added'

    db.session.commit()

    socketio.emit('reaction_updated', {
        'message_id': message_id,
        'room_id': msg.room_id,
        'emoji': emoji,
        'username': user.username,
        'action': action,
        'message': serialize_message(msg)
    }, room=f'room_{msg.room_id}')

    return jsonify({'action': action})


# ---------------------------------------------------------------------------
# API Routes — Search
# ---------------------------------------------------------------------------

@app.route('/api/search', methods=['GET'])
@login_required
def api_search():
    user = get_current_user()
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify({'messages': [], 'rooms': [], 'users': []})

    # Search rooms user is member of
    member_room_ids = [m.room_id for m in ChatMember.query.filter_by(user_id=user.id).all()]

    messages = Message.query.filter(
        Message.room_id.in_(member_room_ids),
        Message.content.ilike(f'%{query}%'),
        Message.is_deleted == False
    ).order_by(Message.timestamp.desc()).limit(20).all()

    rooms = ChatRoom.query.filter(
        ChatRoom.name.ilike(f'%{query}%'),
        db.or_(
            ChatRoom.is_private == False,
            ChatRoom.id.in_(member_room_ids)
        )
    ).limit(10).all()

    users_result = User.query.filter(
        User.username.ilike(f'%{query}%')
    ).limit(10).all()

    return jsonify({
        'messages': [serialize_message(m) for m in messages],
        'rooms': [serialize_room(r, user.id) for r in rooms],
        'users': [{'id': u.id, 'username': u.username, 'avatar_color': u.avatar_color} for u in users_result]
    })


# ---------------------------------------------------------------------------
# API Routes — Users & Settings
# ---------------------------------------------------------------------------

@app.route('/api/users', methods=['GET'])
@login_required
def api_get_users():
    users = User.query.all()
    result = []
    for u in users:
        st = UserStatus.query.filter_by(user_id=u.id).first()
        result.append({
            'id': u.id,
            'username': u.username,
            'avatar_color': u.avatar_color,
            'is_online': st.is_online if st else False,
            'last_seen': st.last_seen.isoformat() if st and st.last_seen else None
        })
    return jsonify(result)


@app.route('/api/user/settings', methods=['GET'])
@login_required
def api_get_settings():
    user = get_current_user()
    if not user.settings:
        settings = UserSettings(user_id=user.id)
        db.session.add(settings)
        db.session.commit()
    return jsonify({
        'theme': user.settings.theme,
        'notifications_enabled': user.settings.notifications_enabled
    })


@app.route('/api/user/settings', methods=['PUT'])
@login_required
def api_update_settings():
    user = get_current_user()
    data = request.json
    if not user.settings:
        settings = UserSettings(user_id=user.id)
        db.session.add(settings)
        db.session.flush()

    if 'theme' in data:
        valid_themes = ['default', 'hello-kitty', 'midnight', 'ocean']
        if data['theme'] in valid_themes:
            user.settings.theme = data['theme']
    if 'notifications_enabled' in data:
        user.settings.notifications_enabled = bool(data['notifications_enabled'])

    db.session.commit()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# API Routes — File Upload
# ---------------------------------------------------------------------------

@app.route('/api/upload', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def api_upload():
    user = get_current_user()
    room_id = request.form.get('room_id')
    if not room_id:
        return jsonify({'error': 'ID da sala não fornecido'}), 400

    # Verify membership in room
    membership = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    if not membership:
        return jsonify({'error': 'Você não tem permissão para fazer upload nesta sala'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nome de arquivo vazio'}), 400

    # Check if user quota exceeded BEFORE processing
    usage = storage_manager.get_user_storage_usage(db, FileAttachment, user.id)
    if usage['total_bytes'] >= DEFAULT_USER_QUOTA:
        return jsonify({'error': 'Limite de armazenamento individual de 500MB atingido'}), 400

    # Basic extension check (first level defence)
    filename = security.safe_filename(file.filename)
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALL_ALLOWED:
        return jsonify({'error': 'Tipo de arquivo não permitido'}), 400

    # Process upload: saving, resizing/compressing, thumbnailing
    try:
        upload_result = storage_manager.process_upload(
            file_stream=file,
            original_filename=filename,
            upload_dir=app.config['UPLOAD_FOLDER'],
            user_id=user.id
        )
    except Exception as e:
        return jsonify({'error': f'Falha ao processar arquivo: {str(e)}'}), 500

    file_path = upload_result['file_path']
    compressed_size = upload_result['compressed_size']
    file_hash = upload_result['file_hash']
    category = upload_result['category']
    thumbnail_name = upload_result['thumbnail_name']
    ext = upload_result['extension']

    # Validate file content using magic bytes (second level defence)
    is_valid, detected_mime, reason = security.validate_file_magic(file_path, ext)
    if not is_valid:
        # Delete file and thumbnail
        if os.path.exists(file_path):
            os.remove(file_path)
        if thumbnail_name:
            tpath = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails', thumbnail_name)
            if os.path.exists(tpath):
                os.remove(tpath)

        # Log security event
        security.log_security_event(
            db, AuditLog,
            event_type=security.SecurityEventType.FILE_VALIDATION_FAIL,
            details=f"Falha na validação do arquivo: {reason} (MIME: {detected_mime})",
            user_id=user.id,
            username=user.username,
            ip_address=request.remote_addr
        )
        return jsonify({'error': f"Arquivo inválido: {reason}"}), 400

    # Check if user quota exceeded including the new compressed file
    if usage['total_bytes'] + compressed_size > DEFAULT_USER_QUOTA:
        # Clean up physical files
        if os.path.exists(file_path):
            os.remove(file_path)
        if thumbnail_name:
            tpath = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails', thumbnail_name)
            if os.path.exists(tpath):
                os.remove(tpath)
        return jsonify({'error': 'O upload excede seu limite de armazenamento de 500MB'}), 400

    # Deduplication Check
    existing_attachment = storage_manager.check_duplicate(file_hash, db, FileAttachment)
    if existing_attachment:
        # Verify if the existing physical file actually exists on disk
        existing_path = os.path.join(app.config['UPLOAD_FOLDER'], existing_attachment.stored_name)
        if os.path.exists(existing_path):
            # Delete the new physical file and thumbnail we just saved (deduped!)
            if os.path.exists(file_path):
                os.remove(file_path)
            if thumbnail_name:
                tpath = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails', thumbnail_name)
                if os.path.exists(tpath):
                    os.remove(tpath)

            # Reuse existing file data
            stored_name = existing_attachment.stored_name
            thumbnail_name = existing_attachment.thumbnail_name
            mime_type = existing_attachment.mime_type
            compressed_size = existing_attachment.compressed_size
            is_compressed = existing_attachment.is_compressed
        else:
            # Database has it but file was missing from disk; use the new file
            stored_name = upload_result['stored_name']
            mime_type = upload_result['mime_type']
            is_compressed = (compressed_size < upload_result['original_size'])
    else:
        # New file
        stored_name = upload_result['stored_name']
        mime_type = upload_result['mime_type']
        is_compressed = (compressed_size < upload_result['original_size'])

    # Save database record
    attachment = FileAttachment(
        original_name=filename,
        stored_name=stored_name,
        mime_type=mime_type,
        size_bytes=upload_result['original_size'],
        compressed_size=compressed_size,
        room_id=room_id,
        uploaded_by=user.id,
        file_hash=file_hash,
        is_compressed=is_compressed,
        thumbnail_name=thumbnail_name
    )
    db.session.add(attachment)
    db.session.commit()

    # Success response
    thumb_url = f"/uploads/thumbnails/{thumbnail_name}" if thumbnail_name else None
    return jsonify({
        'url': f"/uploads/{stored_name}",
        'thumbnail': thumb_url,
        'original_name': filename,
        'stored_name': stored_name,
        'size': compressed_size,
        'type': category,
        'mime': ext
    })


@app.route('/uploads/<path:filename>')
@login_required
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# User storage usage endpoint
@app.route('/api/user/storage', methods=['GET'])
@login_required
def api_user_storage_usage():
    user = get_current_user()
    usage = storage_manager.get_user_storage_usage(db, FileAttachment, user.id)
    return jsonify({
        'used_bytes': usage['total_bytes'],
        'used_mb': usage['total_mb'],
        'quota_bytes': DEFAULT_USER_QUOTA,
        'quota_mb': round(DEFAULT_USER_QUOTA / (1024 * 1024), 2)
    })


# Admin list file attachments
@app.route('/api/admin/files', methods=['GET'])
@admin_required
def api_admin_files():
    page = request.args.get('page', 1, type=int)
    attachments = FileAttachment.query.order_by(FileAttachment.uploaded_at.desc())\
        .paginate(page=page, per_page=50, error_out=False)
    return jsonify({
        'files': [{
            'id': f.id,
            'original_name': f.original_name,
            'stored_name': f.stored_name,
            'mime_type': f.mime_type,
            'size_bytes': f.size_bytes,
            'compressed_size': f.compressed_size,
            'uploaded_by': f.uploader.username if f.uploader else 'Desconhecido',
            'uploaded_at': f.uploaded_at.isoformat() if f.uploaded_at else None,
            'room_name': f.room.name if f.room else 'Geral',
            'thumbnail': f"/uploads/thumbnails/{f.thumbnail_name}" if f.thumbnail_name else None
        } for f in attachments.items],
        'has_more': attachments.has_next,
        'total': attachments.total
    })


# Admin delete file attachment
@app.route('/api/admin/files/<int:file_id>', methods=['DELETE'])
@admin_required
def api_admin_delete_file(file_id):
    att = FileAttachment.query.get_or_404(file_id)
    # Check if other attachments share the same stored file
    others_share = FileAttachment.query.filter(
        FileAttachment.stored_name == att.stored_name,
        FileAttachment.id != att.id
    ).count() > 0
    
    if not others_share:
        # Actually delete the file from disk
        fpath = os.path.join(app.config['UPLOAD_FOLDER'], att.stored_name)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass
        # Delete thumbnail
        if att.thumbnail_name:
            tpath = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails', att.thumbnail_name)
            if os.path.exists(tpath):
                try:
                    os.remove(tpath)
                except OSError:
                    pass
                    
    db.session.delete(att)
    db.session.commit()
    log_audit('delete_file_admin', f'Arquivo {att.original_name} excluído pelo administrador', user_id=session.get('user_id'))
    return jsonify({'success': True})


@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def api_admin_stats():
    total_users = User.query.count()
    total_rooms = ChatRoom.query.count()
    total_messages = Message.query.count()
    online_count = len(online_users)

    # Messages in last 24h
    yesterday = datetime.utcnow() - timedelta(hours=24)
    recent_messages = Message.query.filter(Message.timestamp >= yesterday).count()

    # Storage usage
    storage_report = storage_manager.get_storage_report(app.config['UPLOAD_FOLDER'])

    return jsonify({
        'total_users': total_users,
        'total_rooms': total_rooms,
        'total_messages': total_messages,
        'online_count': online_count,
        'recent_messages': recent_messages,
        'storage': {
            'total_bytes': storage_report['total_bytes'],
            'total_mb': storage_report['total_mb'],
            'file_count': storage_report['file_count'],
            'by_type': storage_report['by_type']
        }
    })


@app.route('/api/admin/audit-logs', methods=['GET'])
@admin_required
def api_admin_audit_logs():
    page = request.args.get('page', 1, type=int)
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc())\
        .paginate(page=page, per_page=50, error_out=False)
    return jsonify({
        'logs': [{
            'id': l.id,
            'username': l.username,
            'action': l.action,
            'details': l.details,
            'ip_address': l.ip_address,
            'timestamp': l.timestamp.isoformat() if l.timestamp else None
        } for l in logs.items],
        'has_more': logs.has_next,
        'total': logs.total
    })


# ---------------------------------------------------------------------------
# Push Subscription
# ---------------------------------------------------------------------------

@app.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    subscription_info = request.json
    endpoint = subscription_info['endpoint']
    p256dh = subscription_info['keys']['p256dh']
    auth = subscription_info['keys']['auth']
    user = get_current_user()

    existing = Subscription.query.filter_by(endpoint=endpoint).first()
    if not existing:
        new_sub = Subscription(
            endpoint=endpoint, p256dh=p256dh, auth=auth,
            user_id=user.id if user else None
        )
        db.session.add(new_sub)
        db.session.commit()

    return jsonify({"success": True}), 200


def get_all_subscriptions():
    subscriptions = Subscription.query.all()
    return [{
        'endpoint': s.endpoint,
        'keys': {'p256dh': s.p256dh, 'auth': s.auth}
    } for s in subscriptions]


# ---------------------------------------------------------------------------
# Socket.IO Events
# ---------------------------------------------------------------------------

@socketio.on('connect')
def handle_connect():
    username = session.get('username')
    if not username:
        return False

    user = User.query.filter_by(username=username).first()
    if not user:
        return False

    online_users[request.sid] = {'user_id': user.id, 'username': username}

    # Update status
    status = UserStatus.query.filter_by(user_id=user.id).first()
    if not status:
        status = UserStatus(user_id=user.id)
        db.session.add(status)
    status.is_online = True
    status.socket_id = request.sid
    db.session.commit()

    # Join all user's rooms
    memberships = ChatMember.query.filter_by(user_id=user.id).all()
    for m in memberships:
        join_room(f'room_{m.room_id}')

    # Broadcast online status
    emit('user_status', {
        'user_id': user.id,
        'username': username,
        'is_online': True
    }, broadcast=True)


@socketio.on('disconnect')
def handle_disconnect():
    user_info = online_users.pop(request.sid, None)
    if user_info:
        user_id = user_info['user_id']
        # Check if user has other active connections
        still_online = any(
            info['user_id'] == user_id
            for sid, info in online_users.items()
        )
        if not still_online:
            status = UserStatus.query.filter_by(user_id=user_id).first()
            if status:
                status.is_online = False
                status.last_seen = datetime.utcnow()
                db.session.commit()

            emit('user_status', {
                'user_id': user_id,
                'username': user_info['username'],
                'is_online': False
            }, broadcast=True)


@socketio.on('join_room')
def handle_join_room(data):
    room_id = data.get('room_id')
    if room_id:
        join_room(f'room_{room_id}')


@socketio.on('leave_room')
def handle_leave_room(data):
    room_id = data.get('room_id')
    if room_id:
        leave_room(f'room_{room_id}')


@socketio.on('room_message')
def handle_room_message(data):
    username = session.get('username', 'Anônimo')
    user = User.query.filter_by(username=username).first()
    if not user:
        return

    room_id = data.get('room_id')
    content = sanitize(data.get('content', '').strip())
    reply_to_id = data.get('reply_to_id')
    message_type = data.get('message_type', 'text')
    file_url = data.get('file_url')
    file_name = data.get('file_name')
    file_type = data.get('file_type')
    file_size = data.get('file_size')

    if not room_id or (not content and not file_url):
        return

    # Verify membership
    membership = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
    if not membership:
        emit('error', {'message': 'Você não tem permissão para enviar mensagens nesta sala.'})
        return

    # Spam protection
    if content:
        last_msgs = [m.content for m in Message.query.filter_by(user_id=user.id).order_by(Message.timestamp.desc()).limit(5).all()]
        is_spam, spam_reason = security.is_spam_message(content, last_msgs)
        if is_spam:
            security.log_security_event(
                db, AuditLog,
                event_type=security.SecurityEventType.SPAM_DETECTED,
                details=f"Mensagem bloqueada por spam na sala {room_id}: {spam_reason}",
                user_id=user.id,
                username=user.username,
                ip_address=request.remote_addr
            )
            emit('error', {'message': f"Bloqueado por spam: {spam_reason}"})
            return

    new_message = Message(
        content=content or '',
        username=username,
        user_id=user.id,
        room_id=room_id,
        reply_to_id=reply_to_id,
        message_type=message_type,
        file_url=file_url,
        file_name=file_name,
        file_type=file_type,
        file_size=file_size
    )
    db.session.add(new_message)

    # Update room's last message timestamp
    room = ChatRoom.query.get(room_id)
    if room:
        room.last_message_at = datetime.utcnow()

    # Update unread counts for other members
    other_members = ChatMember.query.filter(
        ChatMember.room_id == room_id,
        ChatMember.user_id != user.id
    ).all()
    for m in other_members:
        m.unread_count = (m.unread_count or 0) + 1

    db.session.commit()

    msg_data = serialize_message(new_message)
    emit('room_message', msg_data, room=f'room_{room_id}', include_self=False)

    # Send unread updates
    for m in other_members:
        user_sockets = [
            sid for sid, info in online_users.items()
            if info['user_id'] == m.user_id
        ]
        for sid in user_sockets:
            emit('unread_update', {
                'room_id': room_id,
                'unread_count': m.unread_count
            }, room=sid)

    # Push notifications
    for m in other_members:
        if m.is_muted:
            continue
        subs = Subscription.query.filter_by(user_id=m.user_id).all()
        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        'endpoint': sub.endpoint,
                        'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}
                    },
                    data=json.dumps({
                        'username': username,
                        'content': content[:100] if content else f'📎 {file_name or "Arquivo"}',
                        'room_id': room_id,
                        'room_name': room.name if room else ''
                    }),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS
                )
            except WebPushException:
                pass


@socketio.on('typing')
def handle_typing(data):
    username = session.get('username')
    room_id = data.get('room_id')
    is_typing = data.get('is_typing', True)
    if username and room_id:
        emit('user_typing', {
            'username': username,
            'room_id': room_id,
            'is_typing': is_typing
        }, room=f'room_{room_id}', include_self=False)


@socketio.on('mark_read')
def handle_mark_read(data):
    username = session.get('username')
    user = User.query.filter_by(username=username).first()
    room_id = data.get('room_id')
    if user and room_id:
        membership = ChatMember.query.filter_by(room_id=room_id, user_id=user.id).first()
        if membership:
            membership.unread_count = 0
            db.session.commit()


# Keep legacy 'message' event for backward compatibility during migration
@socketio.on('message')
def handleMessage(msg):
    username = session.get('username', 'Anônimo')
    user = User.query.filter_by(username=username).first()

    # Find first room user is member of (fallback)
    membership = ChatMember.query.filter_by(user_id=user.id).first() if user else None
    room_id = membership.room_id if membership else None

    new_message = Message(
        content=sanitize(msg),
        username=username,
        user_id=user.id if user else None,
        room_id=room_id
    )
    db.session.add(new_message)
    db.session.commit()

    send({'username': username, 'content': msg}, broadcast=True, include_self=False)


# ---------------------------------------------------------------------------
# Background Scheduler (Cleanup Tasks)
# ---------------------------------------------------------------------------
scheduler = BackgroundScheduler()

def scheduled_cleanup():
    with app.app_context():
        try:
            orphans_cleaned = storage_manager.cleanup_orphaned_files(app.config['UPLOAD_FOLDER'], db, FileAttachment)
            old_cleaned = storage_manager.cleanup_old_files(app.config['UPLOAD_FOLDER'], db, FileAttachment, max_age_days=90)
            if orphans_cleaned > 0 or old_cleaned > 0:
                print(f"[Cleanup Scheduler] Removidos {orphans_cleaned} arquivos órfãos e {old_cleaned} arquivos antigos.")
        except Exception as e:
            print(f"[Cleanup Scheduler] Erro durante limpeza agendada: {e}")

# Run daily
scheduler.add_job(func=scheduled_cleanup, trigger="interval", days=1)
scheduler.start()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    socketio.run(app, host="0.0.0.0", port=8083, debug=True, allow_unsafe_werkzeug=True)