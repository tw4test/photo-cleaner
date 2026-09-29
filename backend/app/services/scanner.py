import os
import fnmatch
from pathlib import Path
from typing import List, Dict, Any, Generator

SUPPORTED_PHOTO_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.bmp', '.gif', '.heic', '.heif'}
SUPPORTED_RAW_EXTS = {'.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.pef', '.raf'}
SUPPORTED_VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v'}
ALL_SUPPORTED_EXTS = SUPPORTED_PHOTO_EXTS | SUPPORTED_RAW_EXTS | SUPPORTED_VIDEO_EXTS

def normalize_windows_path(path_str: str) -> str:
    """Normalize path string and add long path prefix if on Windows."""
    p = Path(path_str).resolve()
    return str(p)

def is_excluded(rel_path: str, exclude_patterns: List[str]) -> bool:
    """Check if relative path matches any exclude pattern."""
    for pattern in exclude_patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(os.path.basename(rel_path), pattern):
            return True
    return False

def scan_directory(
    root_path: str,
    recursive: bool = True,
    exclude_patterns: str = ""
) -> Generator[Dict[str, Any], None, None]:
    """
    掃描指定目錄下的所有支援媒體檔案。
    回傳字典：{path, size, mtime, ext, format_category}
    """
    base_dir = Path(root_path).resolve()
    if not base_dir.exists() or not base_dir.is_dir():
        return

    patterns = [p.strip() for p in exclude_patterns.split(",") if p.strip()]

    if recursive:
        for current_root, dirs, files in os.walk(base_dir):
            # Check exclusions on dirs
            rel_dir = os.path.relpath(current_root, base_dir)
            if rel_dir != "." and is_excluded(rel_dir, patterns):
                dirs.clear()
                continue

            for file in files:
                rel_file = os.path.join(rel_dir, file) if rel_dir != "." else file
                if is_excluded(rel_file, patterns):
                    continue

                ext = Path(file).suffix.lower()
                if ext in ALL_SUPPORTED_EXTS:
                    full_path = os.path.join(current_root, file)
                    try:
                        stat = os.stat(full_path)
                        yield {
                            "path": full_path,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                            "ext": ext,
                            "is_raw": ext in SUPPORTED_RAW_EXTS,
                            "is_video": ext in SUPPORTED_VIDEO_EXTS,
                            "is_photo": ext in SUPPORTED_PHOTO_EXTS,
                        }
                    except OSError:
                        # 權限不足或檔案被鎖定，跳過
                        continue
    else:
        for entry in base_dir.iterdir():
            if entry.is_file():
                ext = entry.suffix.lower()
                if ext in ALL_SUPPORTED_EXTS:
                    if not is_excluded(entry.name, patterns):
                        try:
                            stat = entry.stat()
                            yield {
                                "path": str(entry.resolve()),
                                "size": stat.st_size,
                                "mtime": stat.st_mtime,
                                "ext": ext,
                                "is_raw": ext in SUPPORTED_RAW_EXTS,
                                "is_video": ext in SUPPORTED_VIDEO_EXTS,
                                "is_photo": ext in SUPPORTED_PHOTO_EXTS,
                            }
                        except OSError:
                            continue
