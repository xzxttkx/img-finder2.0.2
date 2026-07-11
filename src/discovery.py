"""Folder discovery: find image-containing folders on the device.

Platform-aware: uses MediaStore BUCKET grouping on Android, os.walk on desktop.
"""

import os
from dataclasses import dataclass
from typing import Callable, Optional

from kivy.utils import platform


@dataclass
class FolderInfo:
    """Represents a folder containing image files."""
    name: str          # Display name (e.g. "Camera", "Screenshots")
    path: str          # Full filesystem path
    image_count: int   # Number of image files in this folder


def _is_android() -> bool:
    return platform == 'android'


# ── Public API ───────────────────────────────────────────────────────────

def discover_folders(
    progress_callback: Optional[Callable[[str], None]] = None
) -> list[FolderInfo]:
    """Discover all folders containing images on the device.

    Returns a list of FolderInfo sorted by: well-known folders first,
    then by image_count descending.
    """
    if _is_android():
        return _discover_android(progress_callback)
    else:
        return _discover_desktop(progress_callback)


# ── Desktop discovery ────────────────────────────────────────────────────

_DESKTOP_SEARCH_ROOTS = [
    os.path.expanduser('~/Pictures'),
    os.path.expanduser('~/Desktop'),
    os.path.expanduser('~/Downloads'),
]

_IMAGE_EXTS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp',
    '.webp', '.heic', '.heif', '.tiff', '.tif'
}


def _discover_desktop(
    progress_callback: Optional[Callable[[str], None]] = None
) -> list[FolderInfo]:
    """Walk common picture directories and find folders with images."""
    folders: dict[str, FolderInfo] = {}  # path -> FolderInfo

    for root in _DESKTOP_SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue

        if progress_callback:
            progress_callback(f'正在扫描 {root}...')

        # Add the root itself
        root_count = _count_images_in_dir(root)
        if root_count > 0:
            folders[root] = FolderInfo(
                name=os.path.basename(root) or root,
                path=root,
                image_count=root_count
            )

        # Add direct subdirectories
        try:
            entries = os.listdir(root)
        except OSError:
            continue

        for entry in sorted(entries):
            subpath = os.path.join(root, entry)
            if not os.path.isdir(subpath) or entry.startswith('.'):
                continue
            count = _count_images_in_dir(subpath)
            if count > 0:
                folders[subpath] = FolderInfo(
                    name=entry,
                    path=subpath,
                    image_count=count
                )

    # Sort: by image_count descending
    result = sorted(folders.values(), key=lambda f: (-f.image_count, f.name))
    return result


def _count_images_in_dir(dirpath: str) -> int:
    """Count image files in a single directory (non-recursive)."""
    count = 0
    try:
        for filename in os.listdir(dirpath):
            ext = os.path.splitext(filename)[1].lower()
            if ext in _IMAGE_EXTS:
                count += 1
    except OSError:
        pass
    return count


# ── Android discovery ────────────────────────────────────────────────────

def _discover_android(
    progress_callback: Optional[Callable[[str], None]] = None
) -> list[FolderInfo]:
    """Use MediaStore BUCKET grouping to discover image folders.

    Falls back to filesystem walk on known paths if MediaStore fails.
    """
    try:
        return _discover_android_mediastore(progress_callback)
    except Exception:
        return _discover_android_fallback(progress_callback)


def _discover_android_mediastore(
    progress_callback: Optional[Callable[[str], None]] = None
) -> list[FolderInfo]:
    """Query MediaStore grouped by bucket."""
    from jnius import autoclass

    if progress_callback:
        progress_callback('正在查询图片文件夹...')

    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    ctx = PythonActivity.mActivity.getApplicationContext()

    MediaStore = autoclass('android.provider.MediaStore')
    Images = MediaStore.Images
    Media = Images.Media

    uri = Media.EXTERNAL_CONTENT_URI

    # Build MIME type filter
    mime_types = [
        'image/jpeg', 'image/png', 'image/gif',
        'image/bmp', 'image/webp', 'image/heic', 'image/heif', 'image/tiff'
    ]
    selection_parts = [f'{Media.MIME_TYPE}=?' for _ in mime_types]
    selection = ' OR '.join(selection_parts)

    projection = [
        Media.DATA,
        Media.BUCKET_DISPLAY_NAME,
        Media.BUCKET_ID,
        Media.RELATIVE_PATH,
    ]

    resolver = ctx.getContentResolver()
    cursor = resolver.query(uri, projection, selection, mime_types, None)

    if cursor is None:
        raise RuntimeError('MediaStore query returned null cursor')

    data_col = cursor.getColumnIndex(Media.DATA)
    bucket_col = cursor.getColumnIndex(Media.BUCKET_DISPLAY_NAME)
    bucket_id_col = cursor.getColumnIndex(Media.BUCKET_ID)
    rel_path_col = cursor.getColumnIndex(Media.RELATIVE_PATH)

    # Count per bucket
    bucket_counts: dict[str, int] = {}
    bucket_names: dict[str, str] = {}
    bucket_paths: dict[str, str] = {}

    while cursor.moveToNext():
        data_path = cursor.getString(data_col) or ''
        bucket_name = cursor.getString(bucket_col) or ''
        bucket_id = str(cursor.getString(bucket_id_col) or '')
        rel_path = cursor.getString(rel_path_col) or ''

        if data_path and not bucket_id:
            bucket_id = data_path  # fallback

        # Determine the actual folder path from DATA
        folder = os.path.dirname(data_path)
        if not folder:
            continue

        # Use folder path as key for deduplication
        key = folder
        bucket_counts[key] = bucket_counts.get(key, 0) + 1
        if key not in bucket_names or not bucket_names[key]:
            bucket_names[key] = bucket_name or os.path.basename(folder)
        bucket_paths[key] = folder

    cursor.close()

    # Build FolderInfo list
    folders = []
    for key, count in bucket_counts.items():
        name = bucket_names.get(key, os.path.basename(key))
        path = bucket_paths.get(key, key)
        folders.append(FolderInfo(name=name, path=path, image_count=count))

    # Sort: by image_count descending
    folders.sort(key=lambda f: (-f.image_count, f.name))
    return folders


def _discover_android_fallback(
    progress_callback: Optional[Callable[[str], None]] = None
) -> list[FolderInfo]:
    """Fallback: walk known Android paths to find image folders."""
    search_roots = [
        '/storage/emulated/0/DCIM',
        '/storage/emulated/0/Pictures',
        '/storage/emulated/0/Download',
    ]

    folders: list[FolderInfo] = []

    for root in search_roots:
        if not os.path.isdir(root):
            continue

        if progress_callback:
            progress_callback(f'正在扫描 {root}...')

        # Add the root
        root_count = _count_images_in_dir(root)
        if root_count > 0:
            folders.append(FolderInfo(
                name=os.path.basename(root),
                path=root,
                image_count=root_count
            ))

        # Add subdirectories
        try:
            for entry in sorted(os.listdir(root)):
                subpath = os.path.join(root, entry)
                if not os.path.isdir(subpath) or entry.startswith('.'):
                    continue
                count = _count_images_in_dir(subpath)
                if count > 0:
                    folders.append(FolderInfo(
                        name=entry,
                        path=subpath,
                        image_count=count
                    ))
        except OSError:
            continue

    folders.sort(key=lambda f: (-f.image_count, f.name))
    return folders
