"""Platform-aware storage access layer.

On Android (API 29+), uses MediaStore through pyjnius to query images,
working correctly with Scoped Storage. Falls back to os.walk() on desktop
and older Android versions where direct filesystem access still works.
"""

import os
from typing import Callable, Optional

from kivy.utils import platform

# ── Platform detection ──────────────────────────────────────────────────

def _is_android() -> bool:
    return platform == 'android'

def _android_api_level() -> int:
    """Return the Android API level, or 0 if not on Android."""
    if not _is_android():
        return 0
    try:
        from android.os import Build  # type: ignore[import-untyped]
        return Build.VERSION.SDK_INT
    except Exception:
        # Fallback: try reading ro.build.version.sdk via os
        try:
            with open('/system/build.prop', 'r') as f:
                for line in f:
                    if 'ro.build.version.sdk=' in line:
                        return int(line.split('=')[1].strip())
        except Exception:
            pass
    return 0


# ── ImageFile dataclass (mirror of scanner.ImageFile for this module) ────

class ImageFile:
    """Lightweight image file record (compatible with scanner.ImageFile)."""
    __slots__ = ('path', 'file_size', 'modified_time', 'md5_hash', 'dhash')

    def __init__(self, path: str, file_size: int = 0, modified_time: float = 0.0,
                 md5_hash: str = '', dhash: str = ''):
        self.path = path
        self.file_size = file_size
        self.modified_time = modified_time
        self.md5_hash = md5_hash
        self.dhash = dhash


# ── Android MediaStore scanner ───────────────────────────────────────────

class _AndroidMediaStoreScanner:
    """Queries Android's MediaStore for image files – works with Scoped Storage."""

    SUPPORTED_MIME_TYPES = (
        'image/jpeg',
        'image/png',
        'image/gif',
        'image/bmp',
        'image/webp',
        'image/heic',
        'image/heif',
        'image/tiff',
    )

    def __init__(self):
        self._cancelled = False
        self._context = None
        self._mediastore_results: list = []

    def cancel(self):
        self._cancelled = True

    def _get_context(self):
        """Lazy-load the Android app context via pyjnius."""
        if self._context is not None:
            return self._context
        try:
            from jnius import autoclass  # type: ignore[import-untyped]
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            self._context = PythonActivity.mActivity.getApplicationContext()
            return self._context
        except Exception:
            return None

    def scan(
        self,
        root_path: str,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> list:
        """Scan for images using MediaStore. Falls back to os.walk if MediaStore fails."""
        if not self._try_mediastore(progress_callback):
            # MediaStore failed — fall back to direct filesystem scan
            return _FallbackScanner().scan(root_path, progress_callback, self._cancelled)
        return self._mediastore_results

    def _try_mediastore(
        self,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> bool:
        """Attempt MediaStore query. Returns False if it fails, True on success."""
        ctx = self._get_context()
        if ctx is None:
            return False

        try:
            from jnius import autoclass  # type: ignore[import-untyped]

            Build = autoclass('android.os.Build')
            Environment = autoclass('android.os.Environment')
            MediaStore = autoclass('android.provider.MediaStore')
            MediaStore_Images = MediaStore.Images
            MediaStore_Images_Media = MediaStore_Images.Media
            ContentUris = autoclass('android.content.ContentUris')
            Cursor = autoclass('android.database.Cursor')

            content_uri = MediaStore_Images_Media.EXTERNAL_CONTENT_URI

            projection = [
                MediaStore_Images_Media._ID,
                MediaStore_Images_Media.DATA,
                MediaStore_Images_Media.SIZE,
                MediaStore_Images_Media.DATE_MODIFIED,
                MediaStore_Images_Media.MIME_TYPE,
                MediaStore_Images_Media.RELATIVE_PATH,
                MediaStore_Images_Media.DISPLAY_NAME,
            ]

            # Build selection for MIME types
            selection_parts = []
            selection_args = []
            for mt in self.SUPPORTED_MIME_TYPES:
                selection_parts.append(f'{MediaStore_Images_Media.MIME_TYPE}=?')
                selection_args.append(mt)
            selection = ' OR '.join(selection_parts)

            sort_order = f'{MediaStore_Images_Media.DATE_MODIFIED} DESC'

            resolver = ctx.getContentResolver()
            cursor = resolver.query(
                content_uri,
                projection,
                selection,
                selection_args,
                sort_order
            )

            if cursor is None:
                return False

            data_col = cursor.getColumnIndex(MediaStore_Images_Media.DATA)
            size_col = cursor.getColumnIndex(MediaStore_Images_Media.SIZE)
            mtime_col = cursor.getColumnIndex(MediaStore_Images_Media.DATE_MODIFIED)
            id_col = cursor.getColumnIndex(MediaStore_Images_Media._ID)

            results = []
            count = 0

            while cursor.moveToNext() and not self._cancelled:
                path = cursor.getString(data_col)
                file_size = cursor.getLong(size_col)
                modified_time = float(cursor.getLong(mtime_col))

                results.append(ImageFile(
                    path=path,
                    file_size=file_size,
                    modified_time=modified_time,
                ))

                count += 1
                if progress_callback and count % 100 == 0:
                    progress_callback(count, path)

            cursor.close()

            if progress_callback:
                progress_callback(len(results), '')

            results.sort(key=lambda img: img.path)
            self._mediastore_results = results
            return True

        except Exception:
            return False


class _FallbackScanner:
    """Direct filesystem scanner using os.walk – for desktop and older Android."""

    SUPPORTED_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.bmp',
        '.webp', '.heic', '.heif', '.tiff', '.tif'
    }

    SKIP_DIRS = {
        '.thumbnails', '.Trash', 'thumbnails', 'cache',
        '.cache', 'temp', '.temp', 'tmp', '.tmp'
    }

    def scan(
        self,
        root_path: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancelled_flag=None
    ) -> list:
        """Walk root_path for image files."""
        images = []
        cancelled = lambda: bool(cancelled_flag)

        for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
            if cancelled_flag is not None and cancelled_flag:
                break

            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in self.SKIP_DIRS
                and not d.startswith('.')
            ]

            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in self.SUPPORTED_EXTENSIONS:
                    continue

                filepath = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(filepath)
                    images.append(ImageFile(
                        path=filepath,
                        file_size=stat.st_size,
                        modified_time=stat.st_mtime
                    ))
                except OSError:
                    continue

                if progress_callback and len(images) % 50 == 0:
                    progress_callback(len(images), filepath)

        if progress_callback:
            progress_callback(len(images), '')

        images.sort(key=lambda img: img.path)
        return images


# ── Common default paths ─────────────────────────────────────────────────

# Default scan paths by platform
DEFAULT_SCAN_PATHS = {
    'win': lambda: os.path.expanduser('~/Pictures'),
    'darwin': lambda: os.path.expanduser('~/Pictures'),
    'linux': lambda: _android_path() if _is_android() else os.path.expanduser('~/Pictures'),
}

DEFAULT_ANDROID_PATHS = [
    '/storage/emulated/0/DCIM',
    '/storage/emulated/0/Pictures',
    '/storage/emulated/0/Download',
    '/sdcard/DCIM',
    '/sdcard/Pictures',
]


def _android_path() -> str:
    """Pick the best default scan path on Android."""
    # Try android.storage first (python-for-android)
    try:
        from android.storage import primary_external_storage_path  # type: ignore[import-untyped]
        path = primary_external_storage_path()
        if path:
            dcim = os.path.join(path, 'DCIM')
            if os.path.isdir(dcim):
                return dcim
            pics = os.path.join(path, 'Pictures')
            if os.path.isdir(pics):
                return pics
            return path
    except ImportError:
        pass

    # Try known paths
    for p in DEFAULT_ANDROID_PATHS:
        if os.path.isdir(p):
            return p

    # Fallback: DCIM even if it doesn't exist yet
    return '/storage/emulated/0/DCIM'


def get_default_scan_path() -> str:
    """Return a reasonable default scan directory for the current platform."""
    import platform as _py_platform
    system = _py_platform.system().lower()
    if system in DEFAULT_SCAN_PATHS:
        return DEFAULT_SCAN_PATHS[system]()
    return os.path.expanduser('~')


# ── Public API ───────────────────────────────────────────────────────────

def create_scanner():
    """Return a scanner instance appropriate for the current platform."""
    return _AndroidMediaStoreScanner() if _is_android() else _FallbackScanner()


def scan_images(
    root_path: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> list:
    """Convenience function: scan for images using the best available method."""
    scanner = create_scanner()
    return scanner.scan(root_path, progress_callback)


def count_images(root_path: str) -> int:
    """Quick count of image files."""
    if _is_android():
        scanner = _AndroidMediaStoreScanner()
        images = scanner.scan(root_path)
        return len(images)
    else:
        scanner = _FallbackScanner()
        images = scanner.scan(root_path)
        return len(images)


def is_android_scoped_storage() -> bool:
    """Return True if the device enforces scoped storage (Android 10+)."""
    return _is_android() and _android_api_level() >= 29
