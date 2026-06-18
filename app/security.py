"""
Stone Messages — Security Module
File validation, input sanitization, session protection, and security utilities.
"""

import os
import re
import struct
import hashlib
import secrets
from functools import wraps
from datetime import datetime

import bleach


# ---------------------------------------------------------------------------
# File Validation — Magic Bytes
# ---------------------------------------------------------------------------

# Magic byte signatures for common file types
MAGIC_SIGNATURES = {
    # Images
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'\xff\xd8\xff': 'image/jpeg',
    b'GIF87a': 'image/gif',
    b'GIF89a': 'image/gif',
    b'RIFF': 'image/webp',  # RIFF....WEBP (checked further)
    b'BM': 'image/bmp',
    b'II\x2a\x00': 'image/tiff',
    b'MM\x00\x2a': 'image/tiff',

    # Video
    b'\x00\x00\x00\x18ftypmp4': 'video/mp4',
    b'\x00\x00\x00\x1cftypmp4': 'video/mp4',
    b'\x00\x00\x00\x20ftypmp4': 'video/mp4',
    b'\x1aE\xdf\xa3': 'video/webm',

    # Audio
    b'ID3': 'audio/mpeg',
    b'\xff\xfb': 'audio/mpeg',
    b'\xff\xf3': 'audio/mpeg',
    b'\xff\xf2': 'audio/mpeg',
    b'OggS': 'audio/ogg',
    b'fLaC': 'audio/flac',
    b'RIFF': 'audio/wav',  # RIFF....WAVE (checked further)

    # Documents
    b'%PDF': 'application/pdf',
    b'PK\x03\x04': 'application/zip',  # ZIP, DOCX, XLSX, PPTX, etc.
    b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1': 'application/msword',  # DOC/XLS/PPT

    # Archives
    b'Rar!\x1a\x07': 'application/x-rar-compressed',
    b'7z\xbc\xaf\x27\x1c': 'application/x-7z-compressed',
    b'\x1f\x8b': 'application/gzip',
}

# Extension to expected MIME type mapping
EXTENSION_MIME_MAP = {
    'png': ['image/png'],
    'jpg': ['image/jpeg'],
    'jpeg': ['image/jpeg'],
    'gif': ['image/gif'],
    'webp': ['image/webp'],
    'bmp': ['image/bmp'],
    'svg': ['image/svg+xml', 'text/xml', 'application/xml'],
    'tiff': ['image/tiff'],
    'mp4': ['video/mp4'],
    'webm': ['video/webm'],
    'mov': ['video/quicktime', 'video/mp4'],
    'mp3': ['audio/mpeg'],
    'wav': ['audio/wav', 'audio/x-wav'],
    'ogg': ['audio/ogg'],
    'm4a': ['audio/mp4', 'audio/x-m4a'],
    'pdf': ['application/pdf'],
    'doc': ['application/msword'],
    'docx': ['application/zip', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    'xls': ['application/msword', 'application/vnd.ms-excel'],
    'xlsx': ['application/zip', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    'ppt': ['application/msword', 'application/vnd.ms-powerpoint'],
    'pptx': ['application/zip', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'],
    'txt': ['text/plain'],
    'csv': ['text/csv', 'text/plain'],
    'zip': ['application/zip'],
    'rar': ['application/x-rar-compressed'],
    '7z': ['application/x-7z-compressed'],
    'tar': ['application/x-tar'],
    'gz': ['application/gzip'],
}

# Dangerous file extensions that should NEVER be allowed
DANGEROUS_EXTENSIONS = {
    'exe', 'bat', 'cmd', 'com', 'msi', 'scr', 'pif',
    'sh', 'bash', 'csh', 'ksh',
    'ps1', 'psm1', 'psd1',
    'vbs', 'vbe', 'js', 'jse', 'wsf', 'wsh',
    'php', 'phtml', 'php3', 'php4', 'php5', 'phps',
    'asp', 'aspx', 'cer',
    'py', 'pyc', 'pyo',
    'rb', 'pl', 'cgi',
    'jar', 'class',
    'dll', 'so', 'dylib',
    'inf', 'reg', 'rgs',
    'hta', 'cpl', 'msc',
    'swf', 'flv',
}


def validate_file_magic(file_path, expected_extension):
    """
    Validate that a file's magic bytes match its declared extension.
    Returns (is_valid, detected_mime, reason).
    """
    if not os.path.exists(file_path):
        return False, None, "Arquivo não encontrado"

    ext = expected_extension.lower().lstrip('.')

    # Block dangerous extensions
    if ext in DANGEROUS_EXTENSIONS:
        return False, None, f"Extensão .{ext} não é permitida por segurança"

    # SVG and text files don't have reliable magic bytes
    if ext in ('svg', 'txt', 'csv'):
        return validate_text_file(file_path, ext)

    # Read first 32 bytes for magic byte detection
    try:
        with open(file_path, 'rb') as f:
            header = f.read(32)
    except Exception:
        return False, None, "Não foi possível ler o arquivo"

    if len(header) < 4:
        return False, None, "Arquivo muito pequeno ou corrompido"

    # Detect MIME from magic bytes
    detected_mime = detect_mime_from_magic(header)

    if detected_mime is None:
        # Could not detect — allow if extension is in known safe list
        if ext in EXTENSION_MIME_MAP:
            return True, 'application/octet-stream', None
        return False, None, "Tipo de arquivo não reconhecido"

    # Check if detected MIME matches expected extension
    expected_mimes = EXTENSION_MIME_MAP.get(ext, [])
    if not expected_mimes:
        return False, detected_mime, f"Extensão .{ext} não é suportada"

    if detected_mime in expected_mimes:
        return True, detected_mime, None

    # Special cases: ZIP-based formats (DOCX, XLSX, etc.)
    if detected_mime == 'application/zip' and ext in ('docx', 'xlsx', 'pptx', 'zip'):
        return True, detected_mime, None

    # RIFF can be both WAV and WEBP
    if detected_mime in ('image/webp', 'audio/wav') and ext in ('webp', 'wav'):
        return True, detected_mime, None

    return False, detected_mime, f"Conteúdo do arquivo ({detected_mime}) não corresponde à extensão .{ext}"


def detect_mime_from_magic(header):
    """Detect MIME type from file header bytes."""
    # Check RIFF-based formats (WEBP, WAV, AVI)
    if header[:4] == b'RIFF' and len(header) >= 12:
        subtype = header[8:12]
        if subtype == b'WEBP':
            return 'image/webp'
        elif subtype == b'WAVE':
            return 'audio/wav'
        elif subtype == b'AVI ':
            return 'video/x-msvideo'

    # Check ftyp-based formats (MP4, MOV, M4A)
    if len(header) >= 8 and header[4:8] == b'ftyp':
        return 'video/mp4'

    # Check other signatures (longest match first)
    for sig, mime in sorted(MAGIC_SIGNATURES.items(), key=lambda x: -len(x[0])):
        if sig == b'RIFF':
            continue  # Already handled above
        if header[:len(sig)] == sig:
            return mime

    return None


def validate_text_file(file_path, ext):
    """Validate text-based files (SVG, TXT, CSV)."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(4096)  # Read first 4KB

        if ext == 'svg':
            # SVG must contain <svg
            if '<svg' not in content.lower():
                return False, 'text/plain', "Arquivo não parece ser um SVG válido"
            # Check for embedded scripts (security)
            dangerous = ['<script', 'javascript:', 'onload=', 'onerror=', 'onclick=']
            content_lower = content.lower()
            for danger in dangerous:
                if danger in content_lower:
                    return False, 'image/svg+xml', f"SVG contém código potencialmente perigoso: {danger}"
            return True, 'image/svg+xml', None

        return True, 'text/plain', None

    except Exception:
        return False, None, "Não foi possível ler o arquivo como texto"


# ---------------------------------------------------------------------------
# Path Traversal Protection
# ---------------------------------------------------------------------------

def safe_filename(filename):
    """
    Sanitize filename to prevent path traversal.
    More strict than werkzeug's secure_filename.
    """
    # Remove path separators
    filename = filename.replace('\\', '/').split('/')[-1]
    # Remove null bytes
    filename = filename.replace('\x00', '')
    # Remove leading dots and spaces
    filename = filename.lstrip('. ')
    # Allow only safe characters
    filename = re.sub(r'[^\w\s\-.]', '', filename)
    # Collapse multiple dots
    filename = re.sub(r'\.{2,}', '.', filename)
    # Limit length
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200-len(ext)] + ext

    return filename or 'unnamed_file'


def validate_path_safety(base_dir, requested_path):
    """Ensure a path is within the base directory (prevent traversal)."""
    base = os.path.realpath(base_dir)
    target = os.path.realpath(requested_path)
    return target.startswith(base)


# ---------------------------------------------------------------------------
# Input Sanitization
# ---------------------------------------------------------------------------

# Allowed HTML tags and attributes for messages
ALLOWED_TAGS = []  # No HTML tags in messages
ALLOWED_ATTRIBUTES = {}

def sanitize_message(text):
    """Sanitize a message to prevent XSS. Strips all HTML."""
    if not text:
        return ''
    cleaned = bleach.clean(text, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)
    # Remove control characters except newlines and tabs
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', cleaned)
    # Limit consecutive newlines
    cleaned = re.sub(r'\n{4,}', '\n\n\n', cleaned)
    # Trim
    cleaned = cleaned.strip()
    return cleaned


def sanitize_room_name(name):
    """Sanitize a room name."""
    if not name:
        return ''
    cleaned = bleach.clean(name, tags=[], strip=True)
    cleaned = re.sub(r'[^\w\s\-!@#$%&()+=]', '', cleaned)
    return cleaned.strip()[:100]


def sanitize_username(username):
    """Validate and sanitize a username."""
    if not username:
        return ''
    # Only allow alphanumeric, underscore, hyphen, dot
    cleaned = re.sub(r'[^\w\-.]', '', username)
    return cleaned[:30]


# ---------------------------------------------------------------------------
# Session Security
# ---------------------------------------------------------------------------

def generate_csrf_token():
    """Generate a CSRF token."""
    return secrets.token_hex(32)


def create_session_fingerprint(request):
    """
    Create a session fingerprint based on User-Agent and IP prefix.
    Used to detect session hijacking.
    """
    ua = request.headers.get('User-Agent', '')
    # Use first 3 octets of IP (allows for minor IP changes)
    ip_parts = (request.remote_addr or '0.0.0.0').split('.')
    ip_prefix = '.'.join(ip_parts[:3]) if len(ip_parts) >= 3 else request.remote_addr

    data = f"{ua}:{ip_prefix}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def validate_session_fingerprint(session, request):
    """
    Validate that the current request matches the session fingerprint.
    Returns True if valid, False if potentially hijacked.
    """
    stored = session.get('_fingerprint')
    if not stored:
        return True  # No fingerprint stored, skip check
    current = create_session_fingerprint(request)
    return stored == current


# ---------------------------------------------------------------------------
# Rate Limiting Helpers
# ---------------------------------------------------------------------------

def is_spam_message(content, last_messages=None):
    """
    Detect potentially spammy messages.
    Returns (is_spam, reason).
    """
    if not content:
        return False, None

    # Too long
    if len(content) > 5000:
        return True, "Mensagem muito longa (máx 5000 caracteres)"

    # Repeated characters
    if re.search(r'(.)\1{20,}', content):
        return True, "Mensagem contém caracteres repetidos excessivamente"

    # Check for repeated messages (if history provided)
    if last_messages:
        duplicates = sum(1 for m in last_messages if m == content)
        if duplicates >= 3:
            return True, "Mensagem duplicada enviada muitas vezes"

    return False, None


# ---------------------------------------------------------------------------
# Security Event Logging
# ---------------------------------------------------------------------------

class SecurityEventType:
    LOGIN_SUCCESS = 'login_success'
    LOGIN_FAILED = 'login_failed'
    REGISTRATION = 'registration'
    PASSWORD_CHANGE = 'password_change'
    SUSPICIOUS_UPLOAD = 'suspicious_upload'
    RATE_LIMIT_HIT = 'rate_limit_hit'
    SESSION_ANOMALY = 'session_anomaly'
    FILE_VALIDATION_FAIL = 'file_validation_fail'
    ADMIN_ACTION = 'admin_action'
    SPAM_DETECTED = 'spam_detected'


def log_security_event(db, AuditLog, event_type, details=None,
                       user_id=None, username=None, ip_address=None):
    """Log a security-relevant event."""
    try:
        log = AuditLog(
            user_id=user_id,
            username=username,
            action=f"security:{event_type}",
            details=details,
            ip_address=ip_address,
            timestamp=datetime.utcnow()
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()
