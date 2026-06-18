"""
Stone Messages — Storage Manager
Handles file compression, deduplication, thumbnails, cleanup, and usage tracking.
Optimized for VPS with limited resources.
"""

import os
import hashlib
import uuid
import shutil
from datetime import datetime, timedelta
from io import BytesIO

from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum dimensions for image compression
MAX_IMAGE_DIMENSION = 1920
THUMBNAIL_SIZE = (300, 300)
JPEG_QUALITY = 82
WEBP_QUALITY = 78

# File size thresholds (bytes)
COMPRESS_THRESHOLD = 200 * 1024  # Compress images > 200KB
DEFAULT_USER_QUOTA = 500 * 1024 * 1024  # 500 MB per user

# Supported image formats for compression
COMPRESSIBLE_FORMATS = {'png', 'jpg', 'jpeg', 'bmp', 'tiff', 'webp'}

# Formats to convert to WebP (better compression)
WEBP_CONVERTIBLE = {'png', 'bmp', 'tiff'}


# ---------------------------------------------------------------------------
# Image Compression
# ---------------------------------------------------------------------------

def compress_image(file_path, output_path=None, max_dimension=MAX_IMAGE_DIMENSION,
                   quality=JPEG_QUALITY, convert_to_webp=True):
    """
    Compress an image file in-place or to output_path.
    - Resizes if larger than max_dimension
    - Converts PNG/BMP/TIFF to WebP for better compression
    - Strips EXIF metadata to save space
    
    Returns: (new_file_path, original_size, compressed_size, new_extension)
    """
    if not os.path.exists(file_path):
        return file_path, 0, 0, None

    original_size = os.path.getsize(file_path)

    # Skip small files
    if original_size < COMPRESS_THRESHOLD:
        return file_path, original_size, original_size, None

    try:
        img = Image.open(file_path)
    except Exception:
        return file_path, original_size, original_size, None

    # Get original format
    original_format = img.format or 'JPEG'
    ext = os.path.splitext(file_path)[1].lower().lstrip('.')

    # Convert RGBA to RGB for JPEG/WebP (drop alpha if not needed)
    has_transparency = img.mode in ('RGBA', 'LA', 'PA')
    if img.mode == 'RGBA' and not _has_real_transparency(img):
        img = img.convert('RGB')
        has_transparency = False
    elif img.mode not in ('RGB', 'RGBA', 'L'):
        img = img.convert('RGB')

    # Resize if too large
    width, height = img.size
    if width > max_dimension or height > max_dimension:
        img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    # Strip EXIF data by re-creating without metadata
    clean_img = Image.new(img.mode, img.size)
    clean_img.putdata(list(img.getdata()))

    # Determine output format
    new_ext = ext
    save_kwargs = {}

    if convert_to_webp and ext in WEBP_CONVERTIBLE and not has_transparency:
        # Convert to WebP for better compression
        new_ext = 'webp'
        save_kwargs = {'quality': WEBP_QUALITY, 'method': 4}
    elif ext in ('jpg', 'jpeg'):
        save_kwargs = {'quality': quality, 'optimize': True}
    elif ext == 'webp':
        save_kwargs = {'quality': WEBP_QUALITY, 'method': 4}
    elif ext == 'png':
        if has_transparency:
            save_kwargs = {'optimize': True}
        else:
            # Convert opaque PNGs to WebP
            new_ext = 'webp'
            save_kwargs = {'quality': WEBP_QUALITY, 'method': 4}
    else:
        save_kwargs = {'quality': quality}

    # Determine output path
    if output_path is None:
        if new_ext != ext:
            output_path = os.path.splitext(file_path)[0] + '.' + new_ext
        else:
            output_path = file_path

    # Save compressed version
    format_map = {
        'jpg': 'JPEG', 'jpeg': 'JPEG',
        'png': 'PNG', 'webp': 'WEBP',
        'bmp': 'BMP', 'gif': 'GIF'
    }
    save_format = format_map.get(new_ext, 'JPEG')

    clean_img.save(output_path, format=save_format, **save_kwargs)
    compressed_size = os.path.getsize(output_path)

    # If compression made it larger, keep original
    if compressed_size >= original_size and output_path != file_path:
        os.remove(output_path)
        return file_path, original_size, original_size, None

    # If we compressed in-place with different extension, remove original
    if output_path != file_path and os.path.exists(file_path):
        os.remove(file_path)

    clean_img.close()
    img.close()

    return output_path, original_size, compressed_size, new_ext if new_ext != ext else None


def _has_real_transparency(img):
    """Check if RGBA image actually uses transparency."""
    if img.mode != 'RGBA':
        return False
    try:
        # Sample alpha channel
        alpha = img.getchannel('A')
        extrema = alpha.getextrema()
        return extrema[0] < 255  # Has some transparent pixels
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Thumbnail Generation
# ---------------------------------------------------------------------------

def generate_thumbnail(file_path, thumb_dir, size=THUMBNAIL_SIZE):
    """
    Generate a thumbnail for an image file.
    Returns the thumbnail filename or None on failure.
    """
    try:
        img = Image.open(file_path)
        img.thumbnail(size, Image.LANCZOS)

        # Always save thumbnails as WebP for space efficiency
        basename = os.path.splitext(os.path.basename(file_path))[0]
        thumb_name = f"thumb_{basename}.webp"
        thumb_path = os.path.join(thumb_dir, thumb_name)

        if img.mode == 'RGBA':
            img.save(thumb_path, 'WEBP', quality=70)
        else:
            img = img.convert('RGB')
            img.save(thumb_path, 'WEBP', quality=70)

        img.close()
        return thumb_name

    except Exception as e:
        print(f"Thumbnail generation failed: {e}")
        return None


# ---------------------------------------------------------------------------
# File Deduplication
# ---------------------------------------------------------------------------

def compute_file_hash(file_path, algorithm='sha256'):
    """Compute SHA256 hash of a file for deduplication."""
    h = hashlib.new(algorithm)
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_stream_hash(file_stream, algorithm='sha256'):
    """Compute SHA256 hash from a file stream (resets to beginning)."""
    h = hashlib.new(algorithm)
    while True:
        chunk = file_stream.read(8192)
        if not chunk:
            break
        h.update(chunk)
    file_stream.seek(0)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Storage Usage
# ---------------------------------------------------------------------------

def get_directory_size(path):
    """Get total size of a directory in bytes."""
    total = 0
    if not os.path.exists(path):
        return 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def get_storage_report(upload_dir):
    """
    Generate a storage usage report.
    Returns dict with total_bytes, file_count, breakdown by type.
    """
    report = {
        'total_bytes': 0,
        'total_mb': 0,
        'file_count': 0,
        'thumbnail_bytes': 0,
        'by_type': {
            'image': {'count': 0, 'bytes': 0},
            'video': {'count': 0, 'bytes': 0},
            'audio': {'count': 0, 'bytes': 0},
            'document': {'count': 0, 'bytes': 0},
            'archive': {'count': 0, 'bytes': 0},
            'other': {'count': 0, 'bytes': 0},
        }
    }

    if not os.path.exists(upload_dir):
        return report

    image_exts = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'}
    video_exts = {'mp4', 'webm', 'mov', 'avi'}
    audio_exts = {'mp3', 'wav', 'ogg', 'm4a', 'flac'}
    doc_exts = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv'}
    archive_exts = {'zip', 'rar', '7z', 'tar', 'gz'}

    thumb_dir = os.path.join(upload_dir, 'thumbnails')

    for dirpath, dirnames, filenames in os.walk(upload_dir):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                continue

            # Thumbnails tracked separately
            if dirpath == thumb_dir:
                report['thumbnail_bytes'] += size
                continue

            report['total_bytes'] += size
            report['file_count'] += 1

            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
            if ext in image_exts:
                report['by_type']['image']['count'] += 1
                report['by_type']['image']['bytes'] += size
            elif ext in video_exts:
                report['by_type']['video']['count'] += 1
                report['by_type']['video']['bytes'] += size
            elif ext in audio_exts:
                report['by_type']['audio']['count'] += 1
                report['by_type']['audio']['bytes'] += size
            elif ext in doc_exts:
                report['by_type']['document']['count'] += 1
                report['by_type']['document']['bytes'] += size
            elif ext in archive_exts:
                report['by_type']['archive']['count'] += 1
                report['by_type']['archive']['bytes'] += size
            else:
                report['by_type']['other']['count'] += 1
                report['by_type']['other']['bytes'] += size

    report['total_bytes'] += report['thumbnail_bytes']
    report['total_mb'] = round(report['total_bytes'] / (1024 * 1024), 2)

    return report


def get_user_storage_usage(db, FileAttachment, user_id):
    """Get total storage used by a specific user."""
    result = db.session.query(
        db.func.count(FileAttachment.id),
        db.func.coalesce(db.func.sum(FileAttachment.compressed_size), 0)
    ).filter_by(uploaded_by=user_id).first()

    return {
        'file_count': result[0] if result else 0,
        'total_bytes': result[1] if result else 0,
        'total_mb': round((result[1] or 0) / (1024 * 1024), 2)
    }


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_orphaned_files(upload_dir, db, FileAttachment):
    """
    Remove files from disk that have no corresponding database entry.
    Returns number of files cleaned up.
    """
    cleaned = 0
    thumb_dir = os.path.join(upload_dir, 'thumbnails')

    # Get all stored filenames from DB
    stored_files = set()
    for att in FileAttachment.query.all():
        if att.stored_name:
            stored_files.add(att.stored_name)
        if att.thumbnail_name:
            stored_files.add(att.thumbnail_name)

    # Check main upload directory
    if os.path.exists(upload_dir):
        for fname in os.listdir(upload_dir):
            fpath = os.path.join(upload_dir, fname)
            if os.path.isfile(fpath) and fname not in stored_files:
                try:
                    os.remove(fpath)
                    cleaned += 1
                except OSError:
                    pass

    # Check thumbnails directory
    if os.path.exists(thumb_dir):
        for fname in os.listdir(thumb_dir):
            fpath = os.path.join(thumb_dir, fname)
            if os.path.isfile(fpath) and fname not in stored_files:
                try:
                    os.remove(fpath)
                    cleaned += 1
                except OSError:
                    pass

    return cleaned


def cleanup_old_files(upload_dir, db, FileAttachment, max_age_days=90):
    """
    Remove files older than max_age_days.
    Returns number of files cleaned up.
    """
    cleaned = 0
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)

    old_files = FileAttachment.query.filter(FileAttachment.uploaded_at < cutoff).all()
    for att in old_files:
        # Remove physical file
        fpath = os.path.join(upload_dir, att.stored_name) if att.stored_name else None
        if fpath and os.path.exists(fpath):
            try:
                os.remove(fpath)
                cleaned += 1
            except OSError:
                pass

        # Remove thumbnail
        if att.thumbnail_name:
            tpath = os.path.join(upload_dir, 'thumbnails', att.thumbnail_name)
            if os.path.exists(tpath):
                try:
                    os.remove(tpath)
                except OSError:
                    pass

        # Remove DB record
        db.session.delete(att)

    db.session.commit()
    return cleaned


# ---------------------------------------------------------------------------
# File Processing Pipeline
# ---------------------------------------------------------------------------

def process_upload(file_stream, original_filename, upload_dir, user_id=None):
    """
    Complete upload processing pipeline:
    1. Save to disk with UUID name
    2. Compute SHA256 hash
    3. Compress if image
    4. Generate thumbnail if image
    5. Return metadata
    
    Returns dict with all file metadata.
    """
    # Determine extension and category
    ext = original_filename.rsplit('.', 1)[-1].lower() if '.' in original_filename else ''
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join(upload_dir, stored_name)

    # Save to disk
    file_stream.save(file_path)
    original_size = os.path.getsize(file_path)

    # Compute hash for deduplication
    file_hash = compute_file_hash(file_path)

    # Determine file category
    image_exts = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'svg'}
    video_exts = {'mp4', 'webm', 'mov'}
    audio_exts = {'mp3', 'wav', 'ogg', 'm4a'}
    doc_exts = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv'}
    archive_exts = {'zip', 'rar', '7z', 'tar', 'gz'}

    if ext in image_exts:
        category = 'image'
    elif ext in video_exts:
        category = 'video'
    elif ext in audio_exts:
        category = 'audio'
    elif ext in doc_exts:
        category = 'document'
    elif ext in archive_exts:
        category = 'archive'
    else:
        category = 'other'

    compressed_size = original_size
    new_ext = None
    thumbnail_name = None

    # Compress images (skip GIF and SVG)
    if category == 'image' and ext in COMPRESSIBLE_FORMATS:
        new_path, orig_sz, comp_sz, changed_ext = compress_image(file_path)
        compressed_size = comp_sz

        if changed_ext:
            # File was converted to a different format
            new_ext = changed_ext
            old_stored = stored_name
            stored_name = os.path.basename(new_path)
            file_path = new_path
            ext = changed_ext

    # Generate thumbnail for images
    if category == 'image' and ext != 'svg':
        thumb_dir = os.path.join(upload_dir, 'thumbnails')
        os.makedirs(thumb_dir, exist_ok=True)
        thumbnail_name = generate_thumbnail(file_path, thumb_dir)

    return {
        'stored_name': stored_name,
        'original_name': original_filename,
        'file_path': file_path,
        'file_hash': file_hash,
        'original_size': original_size,
        'compressed_size': compressed_size,
        'compression_ratio': round((1 - compressed_size / original_size) * 100, 1) if original_size > 0 else 0,
        'category': category,
        'extension': ext,
        'new_extension': new_ext,
        'thumbnail_name': thumbnail_name,
        'mime_type': _get_mime_type(ext),
    }


def _get_mime_type(ext):
    """Map extension to MIME type."""
    mime_map = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'gif': 'image/gif', 'webp': 'image/webp', 'svg': 'image/svg+xml',
        'bmp': 'image/bmp', 'tiff': 'image/tiff',
        'mp4': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime',
        'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg', 'm4a': 'audio/mp4',
        'pdf': 'application/pdf', 'zip': 'application/zip',
        'doc': 'application/msword', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'txt': 'text/plain', 'csv': 'text/csv',
    }
    return mime_map.get(ext, 'application/octet-stream')


def check_duplicate(file_hash, db, FileAttachment):
    """
    Check if a file with the same hash already exists.
    Returns the existing FileAttachment or None.
    """
    return FileAttachment.query.filter_by(file_hash=file_hash).first()
